"""Activity Overview data service (sections 8, 9).

The frontend never reads Listener storage directly - it calls this service
through the backend API.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from ...common.agent_sessions import is_the_agent_itself
from ...common.file_noise import churns_regardless, rules_from
from ...common.titles import about_this_file
from ...common.own_interface import is_our_own_interface, our_port
from ...common.titles import normalise_title
from .keywords import Moments, page_key, pairs_of


def _under(path: object, folders: List[str]) -> bool:
    """Whether a path is inside one of these directories."""
    if not path or not folders:
        return False
    here = os.path.normcase(os.path.abspath(str(path)))
    return any(here == folder or here.startswith(folder + os.sep)
               for folder in folders)


class OverviewService:
    def __init__(self, raw_store: Any, library: Any = None, config: Any = None) -> None:
        self.raw_store = raw_store
        self.library = library
        # Read only to explain an empty view: a source that is switched off is
        # not the same as a source that saw nothing (section 9, as amended).
        self.config = config

    def _agent_root(self) -> str:
        get = getattr(self.config, "get", None)
        if get is None:
            return ""
        try:
            return str(get("agent_root", "") or "")
        except Exception:                        # a config that cannot answer
            return ""

    def _own_records(self) -> List[str]:
        """The two directories that hold nothing but the agent's own records.

        Not the workspace, which is the point: the user's documents and
        downloads live there too, and they are exactly the activity worth
        seeing. Only the folders of memory the agent keeps, and the internal
        storage beside them.
        """
        root = self._agent_root()
        if not root:
            return []
        base = os.path.abspath(root)
        return [os.path.normcase(os.path.join(base, "workspace", "data")),
                os.path.normcase(os.path.join(base, "internal"))]

    def _worth_showing(self, events: List[Any]) -> List[Any]:
        """The same events without the files that churn wherever they are.

        A rule added on Tuesday does nothing about the thirty days already
        written down, and the panel is where the user meets them. Measured on
        one machine the day after the exclusions went in: of 24,610 file
        events in the last day, **10,653 were one registry hive log** - all
        of them recorded ten minutes before the rule existed, and all of them
        filed under Firefox, because Firefox was in front while Windows
        flushed its registry.

        Shown rather than deleted is not the choice here - both happen - but
        showing is the one that covers every old rule, including the ones
        nobody has thought of yet.
        """
        rules = rules_from(self.config)
        judge = bool(self.config.get("listener_only_what_the_user_did", True))
        kept = []
        for event in events:
            if str(event.event_type).startswith("filesystem."):
                where = (event.data.get("path") or event.data.get("destination_path")
                         or event.data.get("source_path"))
                if churns_regardless(where, **rules):
                    continue
                if judge and not self._probably_theirs(event, where):
                    continue
            kept.append(event)
        return kept

    @staticmethod
    def _probably_theirs(event: Any, where: Optional[str]) -> bool:
        """Whether a stored event still looks like something the user did.

        The same question the Listener asks before recording, asked again
        before showing - because the rules behind it keep improving and the
        records already stored were judged by the older ones. Five distinct
        ways they said yes wrongly were found in this record alone, and each
        fix left a panel still displaying what the Listener had by then
        learned not to record.

        Only where the event carries the window it happened in. An older
        record with no window at all cannot be re-judged, and an event that
        cannot be judged is kept - which is the same answer the Listener
        gives, for the same reason.
        """
        data = event.data or {}
        showing = data.get("while_showing")
        if showing is None:
            return True
        if not where:
            return True
        return about_this_file(where, showing, data.get("while_using"))

    def _not_the_agent(self, events: List[Any]) -> List[Any]:
        """The same events, without the ones that are the agent about itself.

        The Listener no longer records either of these, but thirty days of
        them are already written down and the user is looking at those.
        Expanding one stretch of Firefox showed the relabeller asking for
        keywords, the data heartbeat asking what some raw activity meant, and
        217 writes to the agent's own memory folders - all filed under
        Firefox, because Firefox was the window in front while the agent
        worked in the background.

        Two things are dropped, for the same reason:

        - what the agent said to a model, which is it thinking rather than
          anything that happened at this desk
        - what it wrote to its own records, which is it keeping its books

        Neither is the user's activity, and the record is not rewritten to
        say so - it is simply not shown as theirs, because it never was. What
        the agent writes *for* the user is untouched: a file a task produced
        is something that happened, wherever it landed.
        """
        root = self._agent_root()
        if not root:
            return events
        ours = self._own_records()
        port = our_port(self.config)
        kept = []
        for event in events:
            kind = str(event.event_type)
            if kind.startswith("assistant.") and is_the_agent_itself(
                    event.data.get("session_id"), event.data.get("project"), root):
                continue
            if kind.startswith("filesystem.") and _under(
                    event.data.get("path") or event.data.get("destination_path")
                    or event.data.get("source_path"), ours):
                continue
            if kind.startswith("browser.") and is_our_own_interface(
                    event.data.get("url"), port):
                continue
            kept.append(event)
        return kept

    def summary(self, hours: int = 24) -> Dict[str, Any]:
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        usage = self.raw_store.application_usage(since=since)
        total_seconds = round(sum(entry["seconds"] for entry in usage), 1)
        for entry in usage:
            entry["share"] = round(entry["seconds"] / total_seconds, 4) if total_seconds else 0.0
            entry["human"] = _human(entry["seconds"])
        # Section 9, as amended: what an application was used *for*, next to
        # how long it was used. Gathered in one pass over the window rather
        # than a query per application, which would grow with the list.
        breakdowns = self._breakdowns(since)
        for entry in usage:
            found = breakdowns.get(entry["application"], {})
            entry["breakdown"] = found.get("items", [])
            entry["breakdown_kind"] = found.get("kind", "")
        recent = self._not_the_agent(self.raw_store.recent_events(limit=25))
        return {
            "window_hours": hours,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_seconds": total_seconds,
            "total_human": _human(total_seconds),
            "applications": usage,
            "recent_applications": _unique(
                [e.application for e in recent if e.application], limit=8
            ),
            "recent_events": [_event_row(e) for e in recent],
            "event_counts": _counts([e.event_type for e in recent]),
        }

    def _breakdowns(self, since: datetime, limit: int = 8) -> Dict[str, Dict[str, Any]]:
        """What each application was used for, in one pass over the window.

        A browser's time is really a list of sites; an editor's is a list of
        files. Which of those an application is, is not declared anywhere -
        it is simply what was recorded against it - so the kind follows the
        events rather than a list of known program names, and an application
        nobody has classified still gets whatever it did record.
        """
        events = self._not_the_agent(self.raw_store.recent_events(
            limit=self.MOST_EVENTS, since=since,
            not_types=self.NOT_WORTH_READING, not_under=self._own_records()))
        events = self._worth_showing(events)
        # Who a page or a file belongs to is not simply what the event says.
        # Usage is counted under the process name the system reports -
        # "firefox.exe" - while browsing is recorded under the name of the
        # history source it was read from - "Firefox" - and a file change
        # names no application at all, because the filesystem does not know
        # which program made it. Joined literally, these never meet, and every
        # row would offer nothing to open.
        focus = self._focus_intervals(events)
        # Only the names the usage list is actually keyed by are join targets.
        # Built from every event instead, a browser event's own "Firefox"
        # would claim the name and the sites would be filed under a row that
        # does not exist - which is the same as showing nothing.
        known = {_normalise(application): application
                 for _start, _end, application in focus}
        hosts = self._assistant_hosts(events, focus)
        tally: Dict[str, Dict[str, Dict[str, Any]]] = {}
        kinds: Dict[str, str] = {}
        for event in events:
            application = self._owner(event, known, focus, hosts)
            if not application:
                continue
            if event.event_type.startswith("browser."):
                url = str(event.data.get("url") or "").strip()
                if not url:
                    continue
                key, label, kind = _site(url) or url, _site(url) or url, "sites"
                extra = {"url": url, "title": str(event.data.get("title") or "").strip()}
            elif event.event_type.startswith("filesystem."):
                path = str(event.data.get("path")
                           or event.data.get("destination_path")
                           or event.data.get("source_path") or "").strip()
                if not path or "directory" in event.event_type:
                    continue
                key, label, kind = path, os.path.basename(path) or path, "files"
                extra = {"path": path}
            else:
                continue
            # An application is whatever it recorded most of: a browser that
            # also saved a file is still a browser to this list.
            kinds.setdefault(application, kind)
            bucket = tally.setdefault(application, {})
            item = bucket.setdefault(key, {"label": label, "count": 0,
                                           "kind": kind, "last": None, **extra})
            item["count"] += 1
            stamp = event.timestamp.isoformat()
            if item["last"] is None or stamp > item["last"]:
                item["last"] = stamp
        result: Dict[str, Dict[str, Any]] = {}
        for application, bucket in tally.items():
            items = sorted(bucket.values(),
                           key=lambda i: (i["count"], i["last"] or ""), reverse=True)
            result[application] = {"kind": kinds.get(application, ""),
                                   "items": items[:limit]}
        return result

    @staticmethod
    def _focus_intervals(events: List[Any]) -> List[tuple]:
        """When each application was the one in front, newest first."""
        intervals = []
        for event in events:
            if event.event_type != "application.focus_ended" or not event.application:
                continue
            seconds = float(event.data.get("duration_seconds") or 0)
            intervals.append((event.timestamp - timedelta(seconds=seconds),
                              event.timestamp, event.application))
        intervals.sort(key=lambda i: i[1], reverse=True)
        return intervals

    @staticmethod
    def _assistant_hosts(events: List[Any], focus: List[tuple]) -> Dict[str, str]:
        """Which window each assistant session was actually worked in.

        Section 9, as amended: an assistant session is not bounded by focus.
        It is started in a terminal and then runs for minutes or hours while
        the user reads something else, so "what was in front when the
        transcript last grew" is not where the work was happening. Asked that
        way, a session of 14,646 exchanges was filed under the browser
        because a video was playing while it ran; measured over recent
        history the same rule filed assistant work under the chat client and
        under the **lock screen**, which is the machine sitting locked.

        What does say where a session lives is the turns the user typed.
        Typing into an assistant means being in its window, so the window a
        session's own turns were typed into is the window it belongs to, for
        its whole life - including the stretches the user spent elsewhere
        while it worked.

        A session whose conversation was not recorded has no such evidence
        and is not attributed. It is still in the record of work done through
        an assistant, which is kept by project and does not depend on this.
        """
        votes: Dict[tuple, int] = {}
        for event in events:
            if event.event_type != "assistant.message":
                continue
            if str(event.data.get("role") or "") != "user":
                continue
            session = str(event.data.get("session_id") or "")
            if not session:
                continue
            for start, end, application in focus:
                if start <= event.timestamp <= end:
                    key = (session, application)
                    votes[key] = votes.get(key, 0) + 1
                    break
        # One window per session: where its turns were mostly typed.
        best: Dict[str, tuple] = {}
        for (session, application), count in votes.items():
            if count > best.get(session, (0, ""))[0]:
                best[session] = (count, application)
        return {session: application for session, (_n, application) in best.items()}

    @staticmethod
    def _owner(event: Any, known: Dict[str, str], focus: List[tuple],
               hosts: Optional[Dict[str, str]] = None) -> Optional[str]:
        """Which application a page, a file or a session belongs to.

        Its own name where that matches something the usage list knows about,
        allowing for the two naming schemes; otherwise whichever application
        was in focus when it happened.

        The second is an inference, and it is the honest one available: the
        filesystem reports a change, not a culprit. It is right for the case
        that matters - the user was working in the editor when the file
        changed - and where nothing was in focus it declines to guess.

        An assistant session is the exception, because that inference is
        wrong for it: the session runs whether or not its window is in front,
        so it is placed by where its turns were typed instead, and by nothing
        if that is not known.
        """
        if str(event.event_type).startswith("assistant."):
            return (hosts or {}).get(str(event.data.get("session_id") or "")) or None
        named = _normalise(event.application) if event.application else ""
        if named and named in known:
            return known[named]
        page = str(event.event_type).startswith("browser.")
        for start, end, application in focus:
            if start <= event.timestamp <= end:
                # A page was read in a browser. Which browser is sometimes
                # unknown - a history source whose name nothing matched -
                # and "whatever was in
                # front" is then not an answer to it: on one machine it filed
                # pages under a terminal and an SSH client. Where the window
                # in front is a browser it is the honest guess; where it is
                # not, there is no guess to make.
                if page and not _is_a_browser(application):
                    return None
                return application
        return None

    def history(self, days: int = 7) -> List[Dict[str, Any]]:
        """Per-day totals, in local calendar days (section 9).

        Each day is bounded at both ends. Bounding only the start makes every
        window a running total that includes every later day, so a week of
        days all report the same number.
        """
        out = []
        today = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        starts = [today - timedelta(days=offset) for offset in range(days)]
        usages = self.raw_store.application_usage_in(
            [(day_start, day_start + timedelta(days=1)) for day_start in starts])
        for day_start, usage in zip(starts, usages):
            total = round(sum(u["seconds"] for u in usage), 1)
            out.append(
                {
                    "date": day_start.date().isoformat(),
                    "total_seconds": total,
                    "total_human": _human(total),
                    "top_application": usage[0]["application"] if usage else None,
                    "applications": usage[:5],
                }
            )
        return list(reversed(out))

    def hourly(self, hours: int = 24, top: int = 5) -> Dict[str, Any]:
        """What was being used in each of the last N hours (section 9, amended).

        Returns one bucket per hour with a per-application breakdown, so the
        overview can show how activity moved through the day rather than only
        what it added up to.
        """
        now = datetime.now().astimezone()
        end = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        start = end - timedelta(hours=hours)
        starts = [start + timedelta(hours=offset) for offset in range(hours)]
        usage, *per_hour = self.raw_store.application_usage_in(
            [(start, end)] + [(one, one + timedelta(hours=1)) for one in starts])
        ranked = [entry["application"] for entry in usage[:top]]

        buckets: List[Dict[str, Any]] = []
        for bucket_start, per_app in zip(starts, per_hour):
            bucket_end = bucket_start + timedelta(hours=1)
            by_name = {entry["application"]: round(entry["seconds"], 1) for entry in per_app}
            total = round(sum(by_name.values()), 1)
            buckets.append({
                "hour": bucket_start.isoformat(),
                "label": bucket_start.strftime("%H:%M"),
                # A 24 hour window crosses midnight, so an hour on its own is
                # ambiguous between today and yesterday (section 9, amended).
                "date": bucket_start.strftime("%Y-%m-%d"),
                "date_label": bucket_start.strftime("%b %d"),
                "starts_a_day": bucket_start.hour == 0,
                "total_seconds": total,
                "total_human": _human(total),
                # HOW MUCH OF THE HOUR THERE HAS BEEN. Every hour is sixty
                # minutes except the one still going, and a share of the
                # hour taken against sixty minutes would make twenty busy
                # minutes into it look like a quiet hour.
                "span_seconds": round(max(1.0, min(
                    3600.0, (min(bucket_end, now) - bucket_start).total_seconds())), 1),
                "applications": by_name,
                "top_application": per_app[0]["application"] if per_app else None,
            })
        dates = sorted({bucket["date"] for bucket in buckets})
        span = (f"{buckets[0]['date_label']} {buckets[0]['label']} - "
                f"{buckets[-1]['date_label']} {buckets[-1]['label']}") if buckets else ""
        return {
            "generated_at": now.isoformat(),
            "window_hours": hours,
            "series": ranked,
            "buckets": buckets,
            "dates": dates,
            "period": span,
            "max_seconds": max([b["total_seconds"] for b in buckets] or [0.0]),
        }

    # -- what was actually read and worked on (section 9, as amended) --------
    def pages(self, hours: int = 24, limit: int = 40) -> Dict[str, Any]:
        """Individual pages visited, not merely that a browser was open."""
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        events = self.raw_store.recent_events(limit=2000, event_type="browser.navigation",
                                              since=since)
        visits: Dict[str, Dict[str, Any]] = {}
        for event in events:
            url = str(event.data.get("url") or "").strip()
            if not url:
                continue
            entry = visits.setdefault(url, {
                "url": url,
                "title": str(event.data.get("title") or "").strip(),
                "site": _site(url),
                "browser": event.data.get("browser"),
                "visits": 0,
                "last_visited": None,
            })
            entry["visits"] += 1
            if not entry["title"]:
                entry["title"] = str(event.data.get("title") or "").strip()
            stamp = event.timestamp.isoformat()
            if entry["last_visited"] is None or stamp > entry["last_visited"]:
                entry["last_visited"] = stamp
        ordered = sorted(visits.values(),
                         key=lambda e: (e["visits"], e["last_visited"] or ""), reverse=True)
        return {
            "window_hours": hours,
            "pages": ordered[:limit],
            "total_pages": len(ordered),
            "total_visits": sum(e["visits"] for e in ordered),
            "sites": _top_counts([e["site"] for e in ordered if e["site"]]),
            "collection": self._collection_state("browser"),
        }

    #: A file saved twenty times while it is being worked on is one stretch
    #: of working on it, not twenty things that happened. Runs of the same
    #: file separated by less than this are one row.
    SAME_SITTING_SECONDS = 300

    #: What a filesystem event is called, in the words of what happened.
    WHAT_HAPPENED = {
        "opened": "read", "modified": "edited", "created": "created",
        "deleted": "deleted", "moved": "moved", "renamed": "renamed",
    }

    def file_activity(self, hours: int = 24, limit: int = 60) -> Dict[str, Any]:
        """The most recent file activity, newest first, in the order it happened.

        Different from the list of files above, which answers "what has been
        worked on most" by collapsing everything about one file into a count.
        This answers "what have I just been doing", which is a sequence and
        has to keep its order.

        **Read and written are told apart** (section 19, as amended). Windows
        reports both as one kind of change and the Listener separates them by
        whether the content actually moved, so a file the user only opened is
        not reported back to them as one they edited.

        Repeated touches of the same file in one sitting are one row carrying
        how many and over how long - a file saved twenty times while it is
        being written is one stretch of work, not twenty events.
        """
        window = max(1, int(hours))
        since = datetime.now(timezone.utc) - timedelta(hours=window)
        events = self._worth_showing(self._not_the_agent(self.raw_store.recent_events(
            limit=self.MOST_EVENTS, since=since, event_type="filesystem.",
            not_types=self.NOT_WORTH_READING, not_under=self._own_records())))

        rows: List[Dict[str, Any]] = []
        open_rows: Dict[tuple, Dict[str, Any]] = {}
        for event in sorted(events, key=lambda e: e.timestamp):
            if "directory" in event.event_type:
                continue
            path = str(event.data.get("path")
                       or event.data.get("destination_path")
                       or event.data.get("source_path") or "").strip()
            if not path:
                continue
            kind = self.WHAT_HAPPENED.get(
                event.event_type.split(".", 1)[-1], event.event_type.split(".", 1)[-1])
            key = (path, kind)
            row = open_rows.get(key)
            if row is not None and (
                    event.timestamp - row["_last"]).total_seconds() <= self.SAME_SITTING_SECONDS:
                row["_last"] = event.timestamp
                row["last_at"] = event.timestamp.isoformat()
                row["times"] += 1
                continue
            row = {
                "path": path,
                "name": os.path.basename(path) or path,
                "folder": os.path.dirname(path),
                "what": kind,
                "read": kind == "read",
                "at": event.timestamp.isoformat(),
                "last_at": event.timestamp.isoformat(),
                "times": 1,
                "_first": event.timestamp,
                "_last": event.timestamp,
            }
            open_rows[key] = row
            rows.append(row)

        # WHAT EACH FILE WAS ABOUT, from the part of it the Listener kept
        # when it was opened or written - the same keywords, line and
        # opening as a page, weighed against the other files in the window
        # so what every file of a project shares (its imports, its licence
        # header) is not what any one of them is about.
        kept_text = pairs_of(
            (str(event.data.get("path") or ""), str(event.data.get("content") or ""))
            for event in events if event.data.get("content")).about()
        for row in rows:
            found = kept_text.get(row["path"])
            if found:
                row.update(found)

        for row in rows:
            seconds = (row["_last"] - row["_first"]).total_seconds()
            row["over_seconds"] = round(seconds, 1)
            # Said only when it means something: one touch has no span, and
            # a span of a second is not a stretch of work.
            row["over_human"] = _human(seconds) if seconds >= 1 else ""
            row.pop("_first", None)
            row.pop("_last", None)
        rows.sort(key=lambda r: r["last_at"], reverse=True)
        shown = rows[:limit]
        return {
            "window_hours": window,
            "activity": shown,
            "total": len(rows),
            "read": sum(1 for r in rows if r["read"]),
            "written": sum(1 for r in rows if not r["read"]),
            "collection": self._collection_state("filesystem"),
        }

    def files(self, hours: int = 24, limit: int = 40) -> Dict[str, Any]:
        """Individual files worked on, not merely that an editor was open."""
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        events = self.raw_store.recent_events(limit=2000, event_type="filesystem.",
                                              since=since)
        touched: Dict[str, Dict[str, Any]] = {}
        for event in events:
            if "directory" in event.event_type:
                continue
            path = str(event.data.get("path")
                       or event.data.get("destination_path")
                       or event.data.get("source_path") or "").strip()
            if not path:
                continue
            entry = touched.setdefault(path, {
                "path": path,
                "name": os.path.basename(path) or path,
                "folder": os.path.dirname(path),
                "changes": 0,
                "kinds": {},
                "last_change": None,
            })
            entry["changes"] += 1
            kind = event.event_type.split(".", 1)[-1]
            entry["kinds"][kind] = entry["kinds"].get(kind, 0) + 1
            stamp = event.timestamp.isoformat()
            if entry["last_change"] is None or stamp > entry["last_change"]:
                entry["last_change"] = stamp
        for entry in touched.values():
            entry["change"] = max(entry["kinds"], key=entry["kinds"].get) if entry["kinds"] else ""
        ordered = sorted(touched.values(),
                         key=lambda e: (e["changes"], e["last_change"] or ""), reverse=True)
        return {
            "window_hours": hours,
            "files": ordered[:limit],
            "total_files": len(ordered),
            "total_changes": sum(e["changes"] for e in ordered),
            "folders": _top_counts([e["folder"] for e in ordered if e["folder"]]),
            "collection": self._collection_state("filesystem"),
        }

    def assistants(self, hours: int = 24, limit: int = 30) -> Dict[str, Any]:
        """Work done through an assistant, by project (section 25, as amended)."""
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        events = self._not_the_agent(
            self.raw_store.recent_events(limit=2000, event_type="assistant.",
                                         since=since))
        projects: Dict[str, Dict[str, Any]] = {}
        for event in events:
            data = event.data or {}
            project = str(data.get("project") or "").strip()
            tool = str(data.get("tool") or event.application or "assistant")
            key = (tool, project)
            entry = projects.setdefault(key, {
                "tool": tool,
                "project": project,
                "name": os.path.basename(project.rstrip("/\\")) or project,
                "sessions": set(),
                "exchanges": 0,
                "last_activity": None,
            })
            session = data.get("session_id")
            if session:
                entry["sessions"].add(session)
            try:
                entry["exchanges"] = max(entry["exchanges"], int(data.get("exchanges") or 0))
            except (TypeError, ValueError):
                pass
            stamp = str(data.get("last_activity") or event.timestamp.isoformat())
            if entry["last_activity"] is None or stamp > entry["last_activity"]:
                entry["last_activity"] = stamp
        rows = []
        for entry in projects.values():
            row = dict(entry)
            row["sessions"] = len(entry["sessions"])
            rows.append(row)
        rows.sort(key=lambda e: (e["exchanges"], e["last_activity"] or ""), reverse=True)
        return {
            "window_hours": hours,
            "projects": rows[:limit],
            "total_projects": len(rows),
            "total_sessions": sum(r["sessions"] for r in rows),
            "tools": _top_counts([r["tool"] for r in rows]),
            "collection": self._collection_state("assistant"),
        }

    def _collection_state(self, source: str) -> Dict[str, Any]:
        """Why a view may be empty: an off source is not the same as no activity.

        Section 9, as amended: the panel shows only what the Listener collected,
        and must say when it collected nothing because it was told not to.
        """
        config = getattr(self, "config", None)
        if config is None:
            return {"enabled": None, "reason": ""}
        if not bool(config.get("listener_enabled")):
            return {"enabled": False,
                    "reason": "activity monitoring is turned off in Settings"}
        key = {"browser": "listener_browser_enabled",
               "filesystem": "listener_filesystem_enabled",
               "assistant": "listener_assistant_enabled"}.get(source)
        if key and not bool(config.get(key)):
            label = {"browser": "browser monitoring",
                     "filesystem": "filesystem monitoring",
                     "assistant": "assistant activity"}.get(source, source)
            return {"enabled": False, "reason": f"{label} is turned off in Settings"}
        return {"enabled": True, "reason": ""}

    #: What the panel reads when it is asked about an application.
    #:
    #: A window of time, not a number of rows. Read as "the newest 2,000
    #: events" it covered whatever those happened to span - on one machine,
    #: **213 minutes** of a day the tab said it was showing - and said
    #: nothing about the rest. Which stretches of use an application had
    #: yesterday evening is exactly the question this panel is opened with.
    WINDOW_HOURS = 24

    #: A ceiling, so that a machine with a pathological day cannot make the
    #: panel unbounded. High enough that reaching it is news rather than
    #: routine, and when it is reached the answer says so.
    MOST_EVENTS = 40000

    #: Recorded for the directory, never read for anything: every file change
    #: inside it arrives separately. Excluded in the query rather than
    #: skipped in the loop, because the cost is in building it.
    NOT_WORTH_READING = ("filesystem.directory_modified",)

    def application_detail(self, application: str, limit: int = 100,
                           site: Optional[str] = None,
                           hours: Optional[int] = None) -> Dict[str, Any]:
        # Asking the store for this application by name returns only what was
        # recorded under that exact name - which is the focus events and
        # nothing else, because browsing is recorded under the browser's other
        # name and a file change under none at all. Gathering it that way is
        # why an expanded stretch of use used to be empty. So the same
        # attribution the usage list uses applies here.
        window = max(1, int(hours or self.WINDOW_HOURS))
        since = datetime.now(timezone.utc) - timedelta(hours=window)
        raw = self.raw_store.recent_events(
            limit=self.MOST_EVENTS, since=since,
            not_types=self.NOT_WORTH_READING, not_under=self._own_records())
        recent = self._not_the_agent(raw)
        focus = self._focus_intervals(recent)
        known = {_normalise(name): name for _start, _end, name in focus}
        hosts = self._assistant_hosts(recent, focus)
        events = [event for event in recent
                  if self._owner(event, known, focus, hosts) == application][:limit * 4]
        active_periods = []
        for event in events:
            if event.event_type == "application.focus_ended":
                active_periods.append(
                    {
                        "ended_at": event.timestamp.isoformat(),
                        "duration_seconds": event.data.get("duration_seconds"),
                        "window_title": event.data.get("window_title"),
                    }
                )
        browser_events = [
            {"timestamp": e.timestamp.isoformat(), "url": e.data.get("url"),
             "title": e.data.get("title")}
            for e in events if e.event_type.startswith("browser.")
        ]
        file_events = [
            {"timestamp": e.timestamp.isoformat(), "event_type": e.event_type,
             "path": e.data.get("path")}
            for e in events if e.event_type.startswith("filesystem.")
        ]
        # WEIGHED ACROSS EVERYTHING READ IN THE WINDOW, not only this
        # application's: a word is a keyword when it is rare elsewhere, and
        # "elsewhere" has to include the other applications or every page
        # of a site shares the site's furniture with nothing to tell it by.
        about = self._what_pages_were_about(
            [event for event in recent if event.event_type == "screen.text"])
        interactions = self._interactions(events, active_periods, about)
        if site:
            interactions = self._only_site(interactions, site)
        return {
            "application": application,
            "window_hours": window,
            # Said rather than silently dropped: a panel that shows a slice
            # and calls it a day is worse than one that says it is a slice.
            "complete": len(raw) < self.MOST_EVENTS,
            "site": site or "",
            "interactions": interactions,
            "active_periods": active_periods,
            "window_activity": [
                {"timestamp": e.timestamp.isoformat(),
                 "window_title": e.data.get("window_title"),
                 "event_type": e.event_type}
                for e in events if e.event_type.startswith("application.")
            ],
            "browser_navigation": browser_events,
            "file_interaction": file_events,
            "events": [_event_row(e) for e in events],
        }


    # -- section 9, as amended: what an interaction actually consisted of ---
    def _what_pages_were_about(self, reads: List[Any]) -> Moments:
        """The keywords of every page read in the window, kept until a new
        read arrives.

        What was read does not change after it was read, so working the
        keywords out again on every request - which is every time somebody
        opens an application, and again on each refresh - was over a second
        each time for an answer that was already known.
        """
        mark = (len(reads), max((str(e.timestamp) for e in reads), default=""))
        held = getattr(self, "_about_kept", None)
        if held and held[0] == mark:
            return held[1]
        # By window AND by stretch of time: a window whose title does not
        # change is not one page all day (see `Moments`).
        about = Moments(
            (page_key(str(event.data.get("window_title") or "")),
             event.timestamp, str(event.data.get("text") or ""))
            for event in reads)
        self._about_kept = (mark, about)
        return about

    @staticmethod
    def _interactions(events: List[Any],
                      active_periods: List[Dict[str, Any]],
                      about: Optional[Moments] = None
                      ) -> List[Dict[str, Any]]:
        """Each stretch of use, with what was open during it.

        A list of durations answers "how long", never "doing what". The
        pages and files are therefore attached to the period they happened
        in, so that expanding one shows the actual visit or edit rather than
        sending the user to a separate list to line the timestamps up
        themselves.
        """
        periods: List[Dict[str, Any]] = []
        for entry in active_periods:
            ended = _parse(entry.get("ended_at"))
            if ended is None:
                continue
            seconds = float(entry.get("duration_seconds") or 0)
            periods.append({
                "started_at": (ended - timedelta(seconds=seconds)).isoformat(),
                "ended_at": entry["ended_at"],
                "duration_seconds": seconds,
                "duration_human": _human(seconds),
                # Shown with its animation stripped: the frame the title
                # happened to be on when the stretch ended says nothing.
                "window_title": normalise_title(entry.get("window_title") or ""),
                "pages": [], "files": [], "windows": [], "assistant": [],
                "reads": [],
                "_start": ended - timedelta(seconds=seconds),
                "_end": ended,
            })
        periods.sort(key=lambda p: p["_end"], reverse=True)
        # Data already recorded was fragmented by the same animated titles the
        # Listener no longer splits on, and it does not repair itself. Adjacent
        # stretches of the same window, with only a moment between them, were
        # one stretch of work and are shown as one.
        periods = _merge_adjacent(periods)

        # Anything after the most recent completed period is still going on:
        # it belongs to the user's current stretch of use, not to nothing.
        ongoing: Dict[str, Any] = {
            "started_at": None, "ended_at": None, "duration_seconds": None,
            "duration_human": "in progress", "window_title": None,
            "pages": [], "files": [], "windows": [], "assistant": [],
            "reads": [],
            "ongoing": True,
        }
        latest_end = periods[0]["_end"] if periods else None

        for event in events:
            if event.event_type.startswith("browser."):
                item = {"kind": "page",
                        "at": event.timestamp.isoformat(),
                        "url": event.data.get("url"),
                        "title": event.data.get("title"),
                        "site": _site(str(event.data.get("url") or ""))}
                key = "pages"
            elif event.event_type.startswith("filesystem."):
                path = str(event.data.get("path")
                           or event.data.get("destination_path")
                           or event.data.get("source_path") or "")
                if not path or "directory" in event.event_type:
                    continue
                item = {"kind": "file", "at": event.timestamp.isoformat(), "path": path,
                        "name": os.path.basename(path) or path,
                        "change": event.event_type.split(".", 1)[-1]}
                key = "files"
            elif event.event_type.startswith("assistant."):
                # Section 18, as amended: work done through a coding assistant
                # is the interaction. The terminal is only where it happened -
                # a stretch of terminal use that says "nothing recorded" while
                # a session ran inside it is describing the window, not the work.
                if event.event_type == "assistant.message":
                    item = {"kind": "assistant", "at": event.timestamp.isoformat(),
                            "role": event.data.get("role") or "",
                            "text": event.data.get("text") or "",
                            "project": event.data.get("project") or "",
                            "tool": event.data.get("tool") or ""}
                else:
                    exchanges = event.data.get("exchanges")
                    item = {"kind": "assistant", "at": event.timestamp.isoformat(),
                            "role": "session",
                            "text": (f"{event.data.get('tool') or 'assistant'} session"
                                     + (f", {exchanges} exchanges" if exchanges else "")),
                            "project": event.data.get("project") or "",
                            "tool": event.data.get("tool") or ""}
                if not item["text"]:
                    continue
                key = "assistant"
            elif event.event_type == "screen.text":
                # WHAT WAS ON THE PAGE. Read by the screen listener and then
                # dropped here: this branch did not exist, so the overview
                # showed an address and a title for a page whose whole text
                # the agent had. Kept as the window it was read in; it is
                # joined to the matching page or window below.
                title = str(event.data.get("window_title") or "").strip()
                if not title or not str(event.data.get("text") or "").strip():
                    continue
                item = {"kind": "read", "at": event.timestamp.isoformat(),
                        "title": title, "key": page_key(title)}
                key = "reads"
            elif event.event_type == "application.focused":
                # Section 9, as amended: for an application that opens no pages
                # and touches no watched files - a terminal, a chat window, a
                # slide deck - what it was doing is in its window title, which
                # names the project, the command or the document. Without this
                # such a stretch reads "nothing recorded" when the record
                # exists and simply was not being looked at.
                title = str(event.data.get("window_title") or "").strip()
                if not title:
                    continue
                item = {"kind": "window", "at": event.timestamp.isoformat(),
                        "title": title}
                key = "windows"
            else:
                continue
            placed = False
            for period in periods:
                if period["_start"] <= event.timestamp <= period["_end"]:
                    period[key].append(item)
                    placed = True
                    break
            if not placed and (latest_end is None or event.timestamp > latest_end):
                ongoing[key].append(item)

        for period in periods:
            period.pop("_start", None)
            period.pop("_end", None)
            # Consecutive identical titles are one thing, not several: a
            # window keeps its title while the user works in it.
            period["windows"] = _first_of_each(period["windows"])
            period["assistant"] = _one_per_session(period["assistant"])
            _what_was_read(period, about or Moments([]))
            period["item_count"] = (len(period["pages"]) + len(period["files"])
                                    + len(period["windows"])
                                    + len(period["assistant"])
                                    + len(period["reads"]))
        if (ongoing["pages"] or ongoing["files"] or ongoing["windows"]
                or ongoing["assistant"] or ongoing["reads"]):
            ongoing["windows"] = _first_of_each(ongoing["windows"])
            ongoing["assistant"] = _one_per_session(ongoing["assistant"])
            _what_was_read(ongoing, about or Moments([]))
            ongoing["item_count"] = (len(ongoing["pages"]) + len(ongoing["files"])
                                     + len(ongoing["windows"])
                                     + len(ongoing["assistant"])
                                     + len(ongoing["reads"]))
            periods.insert(0, ongoing)
        return periods

    @staticmethod
    def _only_site(interactions: List[Dict[str, Any]], site: str) -> List[Dict[str, Any]]:
        """The same stretches of use, narrowed to one site.

        Asking about one site is asking a narrower version of the same
        question, so the answer keeps its shape: the stretches of use are
        still the stretches of use, they just carry only what belongs to that
        site. A stretch that never touched it is not shown as empty - it is
        not part of the answer at all.
        """
        wanted = site.strip().lower()
        kept: List[Dict[str, Any]] = []
        for interaction in interactions:
            pages = [page for page in interaction.get("pages", [])
                     if str(page.get("site") or "").lower() == wanted]
            if not pages:
                continue
            narrowed = dict(interaction)
            narrowed["pages"] = pages
            # Files belong to the application, not to the site, so a view of
            # one site does not carry them.
            narrowed["files"] = []
            narrowed["item_count"] = len(pages)
            kept.append(narrowed)
        return kept

    def page_detail(self, url: str, limit: int = 200) -> Dict[str, Any]:
        """Every recorded interaction with one page.

        The counterpart to selecting an application: a page is a thing the
        user did something with, so it can be asked about directly rather
        than only appearing inside an application's log.
        """
        target = url.strip()
        events = self._not_the_agent(
            self.raw_store.recent_events(limit=2000, event_type="browser.navigation"))
        here = [e for e in events if str(e.data.get("url") or "").strip() == target]
        visits = [
            {"at": e.timestamp.isoformat(),
             "title": str(e.data.get("title") or "").strip(),
             "application": e.application,
             "browser": e.data.get("browser")}
            for e in here
        ]
        visits.sort(key=lambda v: v["at"], reverse=True)
        titles = [v["title"] for v in visits if v["title"]]
        if not titles:
            titles = [str(e.data.get("title") or "").strip() for e in here
                      if str(e.data.get("title") or "").strip()]
        return {
            "url": target,
            "site": _site(target),
            "title": titles[0] if titles else "",
            "visits": visits[:limit],
            "visit_count": len(visits),
            "first_seen": visits[-1]["at"] if visits else None,
            "last_seen": visits[0]["at"] if visits else None,
            "applications": _top_counts([v["application"] for v in visits if v["application"]]),
            "collection": self._collection_state("browser"),
        }


#: Browsers whose own name is not their process name.
#:
#: The suffix rule below gets "Chrome" to "chrome.exe" and "Firefox" to
#: "firefox.exe" because those differ only in case and the extension. Edge
#: does not: the browser calls itself Edge and the process is msedge.exe, so
#: the two never met and every page read in Edge was attributed to whatever
#: was in front instead - measured on one machine, 16 of 17 went to an SSH
#: client.
ALIASES = {
    "edge": "msedge",
    "microsoft edge": "msedge",
    "brave": "brave",
    "vivaldi": "vivaldi",
    "opera": "opera",
    "chromium": "chromium",
}

#: What a browser is called, so that a page can be told it does not belong to
#: a terminal. Both spellings of each, because either may be what arrives.
BROWSERS = {"firefox", "chrome", "msedge", "edge", "brave", "vivaldi",
            "opera", "chromium", "safari", "arc", "zen"}


def _normalise(name: Any) -> str:
    """One application under one name, however it was reported.

    The process name and the history-source name differ in case and in the
    trailing executable suffix, and - for the few above - in the word itself.
    """
    text = str(name or "").strip().lower()
    for suffix in (".exe", ".app"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return ALIASES.get(text, text)


def _is_a_browser(name: Any) -> bool:
    return _normalise(name) in BROWSERS


#: A gap this small between two stretches of the same window is the poll
#: interval, not the user going away and coming back.
SAME_STRETCH_GAP_SECONDS = 90


def _merge_adjacent(periods: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One stretch of use, however many windows it was spread across.

    A stretch ends when the user leaves the application, and it used to end
    whenever the *title* changed - so switching tab ended one stretch of
    browsing and began another. Measured on one machine: 127 stretches of
    Firefox, 81% of them under thirty seconds, median six; and seventeen
    stretches of Chrome for two minutes of use, 94% under thirty seconds.
    Expanded, each held a line or two, which is not a record of an evening.

    The title was never the boundary. It is what varied *inside* the stretch,
    and the panel lists it there. What ends a stretch is going somewhere else
    and staying - so what is left of the old rule is the gap, which is the
    difference between glancing at another window and leaving.

    **The duration stays honest.** A merged stretch spans the moments spent
    elsewhere in the middle of it, so end-minus-start would count time in
    other applications as time here. The duration is the sum of the stretches
    actually spent in this application; the span is carried separately, and
    shown only when the two differ.
    """
    if not periods:
        return periods
    ordered = sorted(periods, key=lambda p: p["_start"])
    merged: List[Dict[str, Any]] = [dict(ordered[0])]
    merged[0]["windows_seen"] = 1
    for period in ordered[1:]:
        last = merged[-1]
        gap = (period["_start"] - last["_end"]).total_seconds()
        # Negative is an overlap, and overlaps are the ordinary case rather
        # than a fault: a window's reported duration covers time the next
        # window of the same application also reports, because the title
        # changing is what ends one and begins the other. Measured on one
        # machine, 90 of 217 consecutive stretches of Firefox overlapped -
        # so a rule that only joined stretches with a gap left nearly half
        # of them apart for the one reason that is not a reason.
        if gap <= SAME_STRETCH_GAP_SECONDS:
            # The union, not the sum: overlapping stretches would otherwise
            # count the same seconds twice, and a stretch shared with another
            # window would count that window's time as this one's.
            added = (period["_end"] - max(period["_start"], last["_end"])).total_seconds()
            last["_end"] = max(last["_end"], period["_end"])
            last["ended_at"] = (period["ended_at"] if period["_end"] >= last["_end"]
                                else last["ended_at"])
            last["duration_seconds"] = round(
                float(last["duration_seconds"] or 0) + max(0.0, added), 1)
            last["duration_human"] = _human(last["duration_seconds"])
            # The stretch is named by what it ended on, as it always was.
            last["window_title"] = (period.get("window_title")
                                    or last.get("window_title"))
            last["windows_seen"] = int(last.get("windows_seen") or 1) + 1
            continue
        fresh = dict(period)
        fresh["windows_seen"] = 1
        merged.append(fresh)
    for period in merged:
        span = (period["_end"] - period["_start"]).total_seconds()
        period["span_seconds"] = round(span, 1)
        period["span_human"] = (
            _human(span) if span - float(period["duration_seconds"] or 0) > 1 else "")
    merged.sort(key=lambda p: p["_end"], reverse=True)
    return merged


def _one_per_session(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Every turn of a conversation, but each session summarised once.

    A session is re-reported whenever it grows, so one stretch can hold the
    same session several times over. What was said is different every time
    and is kept; the summary of the session is not.
    """
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for item in sorted(items, key=lambda i: i.get("at") or ""):
        if item.get("role") == "session":
            key = (item.get("tool"), item.get("project"))
            if key in seen:
                continue
            seen.add(key)
        out.append(item)
    return out


def _what_was_read(period: Dict[str, Any], about: Moments) -> None:
    """Join what was read in a stretch to the pages and windows it was on.

    A page and a window whose title matches something read carry its
    keywords, its describing line and the opening of the text. What was
    read and matched neither - a desktop application's window, say - is
    kept as a row of its own, once per title.

    Each with what its window showed AT ITS OWN TIME, so the same terminal
    in two stretches of one afternoon says two different things.
    """
    middle = period.get("ended_at") or period.get("started_at")
    joined = set()
    for bucket in ("pages", "windows"):
        for item in period.get(bucket) or []:
            key = page_key(str(item.get("title") or ""))
            found = about.near(key, item.get("at") or middle)
            if found:
                item.update(found)
                joined.add(key)
    kept, seen = [], set()
    for item in sorted(period.get("reads") or [],
                       key=lambda one: one["at"], reverse=True):
        key = item.pop("key", "")
        if key in joined or key in seen or key not in about:
            continue
        found = about.near(key, item.get("at") or middle)
        if not found:
            continue
        seen.add(key)
        item.update(found)
        kept.append(item)
    period["reads"] = kept


def _first_of_each(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapse a run of the same title into the first of them."""
    out: List[Dict[str, Any]] = []
    for item in sorted(items, key=lambda i: i.get("at") or ""):
        item = dict(item, title=normalise_title(item.get("title", "")))
        if not item["title"]:
            continue
        if out and out[-1]["title"] == item["title"]:
            continue
        out.append(item)
    return out


def _parse(value: Any) -> Any:
    """A timestamp as recorded, back into something comparable."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _event_row(event: Any) -> Dict[str, Any]:
    return {
        "id": event.id,
        "timestamp": event.timestamp.isoformat(),
        "event_type": event.event_type,
        "application": event.application,
        "source": event.source,
        "data": event.data,
    }


def _human(seconds: float) -> str:
    seconds = int(seconds or 0)
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m"
    return f"{seconds}s"


def _unique(values: List[str], limit: int = 10) -> List[str]:
    seen: List[str] = []
    for value in values:
        if value not in seen:
            seen.append(value)
        if len(seen) >= limit:
            break
    return seen


def _counts(values: List[str]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: kv[1], reverse=True))


def _site(url: str) -> str:
    """The site a page belongs to, for grouping."""
    from urllib.parse import urlparse

    try:
        host = urlparse(url).netloc
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _top_counts(values: List[str], limit: int = 8) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    return [{"name": name, "count": count} for name, count in ordered[:limit]]
