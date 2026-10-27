"""DELEGATE WORK - and how every piece of work starts: context, a plan the
user checks, then the run.

The user hands something over - text they selected, a folder or a file they
right-clicked, a to-do, or what they typed into Add work - and before
anything is done:

1. CONTEXT IS COLLECTED, in a process of its own (child.py): what they
   handed over; for selected text, the window it came from (its title, its
   address, the words it showed - as read off the screen, never by signing
   in) and the public page at that address, read as anyone could; what is
   in the folders (their names, a README); and what of the user's own
   records - states, tasks, stages, from any time - bears on it, found by
   its words (context_search.py).
2. A HIGH-LEVEL PLAN IS DRAFTED from it: not the detailed plan - that is
   the run's contract - but enough for the user to see whether the task was
   understood: what it is, the goal, the main steps, what will be delivered
   and what is left out, and anything it needs to ask.
3. THE USER EDITS IT, or not, and presses Start - or cancels. Nothing runs
   before that.
4. THE RUN GOES, the approved plan given to it word for word as the user's.

Delegated work becomes a project of its own, under "Delegated work" in
Experience; work started from a project or task goes on that one.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...common.languages import in_display

log = logging.getLogger(__name__)

COLLECTING, DRAFTING, READY, STARTED, CANCELLED, FAILED = (
    "collecting", "drafting", "ready", "started", "cancelled", "failed")
#: Handed over, waiting for the user's instruction: nothing is collected until
#: they press Create (`compose`) - from anywhere work is handed over.
WRITING = "writing"
#: Start pressed, and the work being set going apart (`start_apart`): the
#: page is answered at once, and asks how it went.
STARTING = "starting"
#: What the plan says - the user may change each.
PLAN_PARTS = ("title", "understanding", "goal", "steps", "deliverables", "out_of_scope",
              "questions")
#: HOW THE WORK IS DONE (the user, 2026-10-26): "quick" - one agent on its
#: own judgement, with the user's context - or "full", Thorough: a builder
#: and a reviewer, with a contract. The plan says which it would take and
#: why; the user chooses.
WAYS = ("quick", "full")
#: How much is read of each thing collected.
PAGE_CHARS, SCREEN_CHARS, README_CHARS, MOST_ENTRIES = 8000, 6000, 3000, 60

PLAN_PROMPT = """THE USER IS HANDING YOU A PIECE OF WORK. Before any of it is done, say
how you understand it, as a short HIGH-LEVEL PLAN they will check and may
change: not the detailed plan - only enough for them to see at a glance
whether you understood the task.

Below is what they handed over, and the context collected around it: the
window it came from (when they selected text in it) and its public page, the
folders they gave and what is in them, and what was FOUND of their own
records by its words - their activity, tasks and past work, each with how
relevant and how recent it is. Use what bears on the task; leave out what
does not.

THIS PLAN IS HIGH-LEVEL, AND QUICK: drafted from what is below, with no
looking further. The detailed plan is made when the work starts - with what
was found here as references, and the work's own tools to search the
user's records, their files and the web.

Return ONLY this JSON object:
{"context_summary": "two or three sentences: what you found that bears on it,
                     and where",
 "title": "a short name for the work, four to eight words",
 "understanding": "one or two sentences: what you understand them to want",
 "goal": "one sentence: what is true when it is done",
 "steps": ["three to seven main steps, each a short line"],
 "deliverables": ["what they will get - each IN THE FORM MOST CONVENIENT for
                   what they will do with it, and how they get it: an email
                   they will send - a draft opened in their own mail, ready to
                   send; a meeting - an event for their calendar; words to
                   paste - text to copy; a document to share - .docx or .pdf;
                   figures - a sheet; a change to their files - the change;
                   an answer - the answer. With "full", always a README.md
                   saying how to check and use what was made"],
 "out_of_scope": ["what you will not do, where it could be mistaken for part
                   of it"],
 "questions": ["only what you cannot settle from the context and would change
                the plan - often none"],
 "way": "quick or full",
 "way_why": "one sentence: why that way, from what the work is"}

THE WAY: "quick" - ONE AGENT ON ITS OWN, told everything at the start, as
a session of a coding assistant would do it: light work with one result the
user will read themselves - an email, a reply, a note, a short document, a
small change. "full" - THOROUGH: a builder and a reviewer, a contract of
what each deliverable must meet, checked round by round - work with several
deliverables that depend on each other, code to build and test, anything
long, or where a mistake is costly. When you are not sure, "full".
With "quick", a README.md only when it makes more than one file."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Drafts:
    """The `work_drafts` rows: each piece of work handed over, from its
    context to its run."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.db = app.db
        #: The drafts being started now (`start_apart`), and how each start
        #: went - what `start` said - for the page to ask.
        self._starting: set = set()
        self._started: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        #: The drafts waiting for their plan to be started (`_when_ready`).
        self._waiting: set = set()
        self._resume_waiting()

    # -- the agent's side ---------------------------------------------------------------------
    def create(self, text: str = "", folders: Optional[List[str]] = None,
               files: Optional[List[str]] = None, window: Optional[Dict[str, Any]] = None,
               unit_id: str = "", source: str = "delegate",
               options: Optional[Dict[str, Any]] = None, collect: bool = True,
               write_first: bool = False) -> Dict[str, Any]:
        """A piece of work handed over: kept, and its context collected and
        its plan drafted in a process of its own (`collect` false: not yet -
        the tests). `write_first`: as every Delegate work is - kept, and
        WAITING FOR THE USER'S INSTRUCTION, which `compose` takes; nothing is
        collected before it."""
        # WHERE IT WORKS IS THE USER'S TO SAY (the user, 2026-10-26): what is
        # handed over is where it works - listed in the box, each to take
        # off: a folder, to work in; a FILE, that file alone, changed in
        # place - never its folder, which opened the whole of it to changes
        # nobody asked for.
        folders = [str(Path(one)) for one in folders or [] if str(one).strip()]
        files = [str(Path(one)) for one in files or [] if str(one).strip()]
        folders += [one for one in files if one not in folders]
        window = dict(window or {})
        # THE WINDOW IT CAME FROM: only text selected in one has one - its
        # words read off the screen record. Anything else handed over (a
        # to-do, Add work, a folder) came from no window.
        if source == "selection" and window.get("title") and not window.get("text"):
            window.update(self._screen_now(window))
        row = {"id": "draft_" + uuid.uuid4().hex[:12], "created_at": _now(),
               "updated_at": _now(), "source": source, "text": str(text or "")[:20000],
               "folders": folders[:12], "files": files[:24], "window": window,
               "unit_id": str(unit_id or ""),
               "status": WRITING if write_first else COLLECTING, "context": {},
               "plan": {}, "plan_by": "", "options": options or {}, "run_id": "",
               "error": ""}
        self.db.execute(
            "INSERT INTO work_drafts (id, created_at, updated_at, source, text, folders, "
            "files, window, unit_id, status, context, plan, plan_by, options, run_id, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (row["id"], row["created_at"], row["updated_at"], row["source"], row["text"],
             json.dumps(row["folders"], ensure_ascii=False),
             json.dumps(row["files"], ensure_ascii=False),
             json.dumps(row["window"], ensure_ascii=False), row["unit_id"], row["status"],
             "{}", "{}", "", json.dumps(row["options"], ensure_ascii=False), "", ""))
        if collect and not write_first:
            self._collect_apart(row["id"])
        return self.get(row["id"]) or row

    def _screen_now(self, window: Dict[str, Any]) -> Dict[str, Any]:
        """The window in front, as the listener last read it off the screen
        - its words, never by signing in (listener/providers/screen.py)."""
        since = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        try:
            rows = self.db.query(
                "SELECT event_type, data FROM raw_events WHERE event_type IN "
                "('screen.text', 'browser.navigation') AND timestamp >= ? "
                "ORDER BY timestamp DESC LIMIT 40", (since,))
        except Exception:
            return {}
        out: Dict[str, Any] = {}
        for row in rows:
            try:
                said = json.loads(row["data"] or "{}")
            except (TypeError, ValueError):
                continue
            if row["event_type"] == "screen.text" and "text" not in out:
                title = str(said.get("window_title") or "")
                if window.get("title") and title and window["title"] not in title:
                    continue
                out.update({"title": window.get("title") or title,
                            "app": window.get("app") or said.get("application") or "",
                            "text": str(said.get("text") or "")[:SCREEN_CHARS]})
            elif row["event_type"] == "browser.navigation" and "url" not in out:
                # The page a browser in front was showing - its address, to
                # be read as anyone could.
                out["url"] = window.get("url") or str(said.get("url") or "")
                out.setdefault("page_title", str(said.get("title") or ""))
        if "text" not in out:
            out.pop("url", None)
        return out

    def _collect_apart(self, draft_id: str) -> None:
        """Collected and drafted in a process of its own - or, where runs go
        in this process, on a thread of its own."""
        work = self.app.work
        if getattr(work, "in_subprocess", False) and getattr(work, "paths", None) is not None:
            from .child import spawn_draft
            try:
                spawn_draft(work, draft_id)
                return
            except Exception:
                log.warning("could not start the draft's process; drafting here",
                            exc_info=True)
        threading.Thread(target=self.collect_and_plan, args=(draft_id,),
                         name=f"draft-{draft_id}", daemon=True).start()

    def get(self, draft_id: str) -> Optional[Dict[str, Any]]:
        row = self.db.query_one("SELECT * FROM work_drafts WHERE id = ?", (draft_id,))
        if row is None:
            return None
        out = dict(row)
        for name, empty in (("folders", []), ("files", []), ("window", {}), ("context", {}),
                            ("plan", {}), ("options", {})):
            try:
                out[name] = json.loads(out.get(name) or "null") or empty
            except (TypeError, ValueError):
                out[name] = empty
        # Each place it works, a folder or a file - as the box shows it.
        out["kinds"] = {one: "file" if Path(one).is_file() else "folder"
                        for one in out.get("folders") or []}
        with self._lock:
            going = draft_id in self._starting
            said = self._started.get(draft_id)
        if out.get("status") == STARTING and not going:
            # Its start went with the application: to be started again.
            out["status"] = READY
            out["error"] = out.get("error") or "it did not start - the application closed"
        if said is not None:
            out["start"] = said
        return out

    def recent(self, most: int = 20) -> List[Dict[str, Any]]:
        return [self.get(row["id"]) for row in self.db.query(
            "SELECT id FROM work_drafts ORDER BY created_at DESC LIMIT ?", (most,))]

    def saved(self, most: int = 50) -> List[Dict[str, Any]]:
        """THE DRAFTS SAVED (`save`), not started nor discarded: the newest
        first, for Work's list of them."""
        out = []
        for row in self.db.query("SELECT id FROM work_drafts WHERE options LIKE ? AND "
                                 "status NOT IN (?, ?, ?) ORDER BY updated_at DESC LIMIT ?",
                                 ("%\"saved\": true%", STARTED, STARTING, CANCELLED, most)):
            draft = self.get(row["id"])
            if draft is not None and (draft.get("options") or {}).get("saved") \
                    and not (draft.get("options") or {}).get("removed"):
                out.append(draft)
        return out

    def save(self, draft_id: str, instruction: Optional[str] = None,
             plan: Optional[Dict[str, Any]] = None, folders: Optional[List[str]] = None,
             options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """SAVE AS DRAFT (the user, 2026-10-26) - in place of Create: what the
        box holds kept as it is - the instruction, where it works, the
        settings and the way chosen, the plan as changed - to be opened again
        from Work's list of drafts and started later. Saved before its
        instruction was taken, the instruction is taken now, and the
        context collected and the plan drafted meanwhile."""
        draft = self.get(draft_id)
        if draft is None:
            return {"error": "no such piece of work"}
        if draft["status"] in (STARTED, STARTING):
            return {"error": "it was started already"}
        kept = dict(draft.get("options") or {})
        kept.update({k: v for k, v in (options or {}).items()
                     if k in ("limits", "packages", "sound", "unconfined", "way")})
        kept["saved"] = True
        values: Dict[str, Any] = {"options": kept}
        if folders is not None:
            values["folders"] = [str(one) for one in folders if str(one).strip()][:12]
        if isinstance(plan, dict) and draft["status"] in (READY, FAILED) \
                and any(plan.get(part) for part in PLAN_PARTS):
            approved = plan_of(plan, fallback=asked_of(draft) or draft.get("text") or "")
            values["plan"] = approved
            if approved != plan_of(draft.get("plan") or {}):
                values["plan_by"] = "user"
        self._set(draft_id, **values)
        if draft["status"] == WRITING and (str(instruction or "").strip() or draft.get("text")
                                           or draft.get("folders") or draft.get("files")
                                           or draft.get("unit_id")):
            self.compose(draft_id, instruction or "")
        return {"draft": self.get(draft_id)}

    def of_todos(self) -> Dict[str, Dict[str, Any]]:
        """WHAT CAME OF A TO-DO HANDED OVER, by its id - EVERY TIME IT WAS
        (the user, 2026-10-26): a to-do may be handed over again and again,
        each a VERSION of its own (V1, V2, ...), the oldest first:
        {"versions": [...]}. Each is being written or drafted (`drafting`,
        its draft to open again), at work (`ongoing`), finished (`finished`,
        with what it handed over, else the deck's first deliverable there,
        else its folder) or ended otherwise (`stopped`). A cancelled one is
        as if it had never been."""
        out: Dict[str, Dict[str, Any]] = {}
        for row in self.db.query("SELECT id, status, options, unit_id FROM work_drafts "
                                 "WHERE source = 'todo' ORDER BY created_at"):
            try:
                options = json.loads(row["options"] or "{}") or {}
            except (TypeError, ValueError):
                options = {}
            todo = str(options.get("todo") or "")
            if not todo or row["status"] == CANCELLED or options.get("removed"):
                continue
            versions = out.setdefault(todo, {"versions": []})["versions"]
            said = {"draft": row["id"],
                    "version": int(options.get("version") or 0) or len(versions) + 1,
                    "state": "ongoing" if row["status"] == STARTING
                    or options.get("start_when_ready") else "drafting"}
            if row["status"] == STARTED and row["unit_id"]:
                said.update(self._came_of(row["unit_id"]))
            elif (options.get("start_when_ready") or {}).get("held"):
                said["held"] = options["start_when_ready"]["held"]
            versions.append(said)
        return out

    def _next_version(self, todo: str, besides: str = "") -> int:
        """The version a to-do's work started now is: one more than the
        latest of it started before (`besides`: the one being started)."""
        most = 0
        for row in self.db.query("SELECT options FROM work_drafts WHERE source = 'todo' "
                                 "AND status IN (?, ?) AND id != ?",
                                 (STARTED, STARTING, besides)):
            try:
                options = json.loads(row["options"] or "{}") or {}
            except (TypeError, ValueError):
                continue
            if str(options.get("todo") or "") == todo:
                most = max(most, int(options.get("version") or 1))
        return most + 1

    def _came_of(self, unit_id: str) -> Dict[str, Any]:
        from .runner import DONE
        work = self.app.work
        run = work.latest_of(unit_id)
        if run is None:
            return {"state": "ongoing"}
        said: Dict[str, Any] = {"run": run["id"], "unit": unit_id,
                                "status": str(run.get("status") or "")}
        if run["alive"]:
            return dict(said, state="ongoing")
        if run.get("status") != DONE:
            # Carried on from the to-do, as in Work (the user, 2026-10-26):
            # one that did not stop cleanly goes back to its last save point.
            from .runner import CONTINUABLE, GOES_BACK, _resumes_from
            from .versions import VersionStore
            said.update(continuable=said["status"] in CONTINUABLE,
                        goes_back=said["status"] in GOES_BACK)
            if said["goes_back"]:
                try:
                    run_dir = work.run_dir_of(run)
                    index = VersionStore(run_dir).index()
                    head = next((one for one in index["versions"]
                                 if one["id"] == index["head"]), None)
                    if head:
                        said["back_to"] = _resumes_from(run_dir, head).get("label", "")
                except Exception:          # the button says less, not wrong
                    pass
            return dict(said, state="stopped")
        # WHAT IT HANDED TO THE USER first (handed.py) - opened as it is
        # meant to be: an email in their mail, an event in their calendar.
        items = (run.get("plan") or {}).get("handed") or []
        said["ended"] = str(run.get("ended_at") or "")
        if items:
            # WHAT IT IS, before it is opened: each thing handed over - its
            # kind and what it is called, a file's or folder's name.
            return dict(said, state="finished",
                        handed=str(items[0].get("title") or items[0].get("kind") or ""),
                        items=[{"n": n, "kind": str(one.get("kind") or ""),
                                "title": str(one.get("title") or ""),
                                "name": Path(str(one.get("path"))).name if one.get("path") else ""}
                               for n, one in enumerate(items[:8])])
        made = [str(one.get("location") or "") for one in
                ((run.get("plan") or {}).get("deck") or {}).get("deliverables") or []
                if isinstance(one, dict)]
        output = ((self.app.state_heartbeat.experience.store.workspace(unit_id) or {})
                  .get("summary") or {}).get("output_folder") or ""
        for one in made + [output, str(run.get("folder") or "")]:
            found = work.readable_by(run["id"], one) if one else None
            if found is not None:
                return dict(said, state="finished", result=one,
                            result_kind="folder" if found.is_dir() else "file",
                            result_name=found.name)
        return dict(said, state="finished", result="")

    def _set(self, draft_id: str, **values: Any) -> None:
        sets = ", ".join(f"{name} = ?" for name in values) + ", updated_at = ?"
        params = [json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list))
                  else value for value in values.values()]
        self.db.execute(f"UPDATE work_drafts SET {sets} WHERE id = ?",
                        (*params, _now(), draft_id))

    # -- collecting and drafting (in the draft's own process) -----------------------------------
    def collect_and_plan(self, draft_id: str) -> Optional[Dict[str, Any]]:
        draft = self.get(draft_id)
        if draft is None or draft["status"] not in (COLLECTING, FAILED):
            return draft
        try:
            context = self.collect(draft)
            self._set(draft_id, status=DRAFTING, context=context)
            plan = self.plan(draft, context)
            # The user's own title, where they gave one.
            given = str((draft.get("options") or {}).get("title") or "").strip()
            if given:
                plan["title"] = given
            self._set(draft_id, status=READY, plan=plan, plan_by="agent", error="")
        except Exception as exc:
            log.exception("could not draft the plan of %s", draft_id)
            self._set(draft_id, status=FAILED, error=f"could not draft the plan: {exc}"[:500])
        return self.get(draft_id)

    def collect(self, draft: Dict[str, Any]) -> Dict[str, Any]:
        """What bears on the work, gathered: the window and its page, the
        folders, and what is FOUND of the user's own records by its words
        (context_search.py) - relevant, from any time, the recent first."""
        context: Dict[str, Any] = {}
        window = draft.get("window") or {}
        if window:
            context["window"] = {k: window.get(k) for k in ("title", "app", "url")
                                 if window.get(k)}
            if window.get("text"):
                context["window"]["text"] = str(window["text"])[:SCREEN_CHARS]
        url = str(window.get("url") or "")
        if url.lower().startswith(("http://", "https://")):
            context["page"] = self._page(url)
        context["folders"] = [self._folder(Path(one)) for one in draft.get("folders") or []]
        context["files"] = [self._file(Path(one)) for one in draft.get("files") or []][:6]
        unit = self._unit(draft)
        if unit is not None:
            context["work"] = {"name": unit.get("name"), "kind": unit.get("kind"),
                               "about": str(unit.get("description") or unit.get("why")
                                            or "")[:1500]}
        # FOUND BY ITS WORDS: what was handed over, in English search words
        # (one model call), matched against every state, task and stage.
        from .context_search import Index, english_words, records
        handed = {"text": "\n".join(one for one in (asked_of(draft), draft.get("text") or "")
                                    if one),
                  "unit": (context.get("work") or {}).get("name"),
                  "folders": [Path(one).name for one in draft.get("folders") or []],
                  "files": [Path(one).name for one in draft.get("files") or []],
                  "page": (context.get("page") or {}).get("title") or window.get("title") or ""}
        query = english_words(self.app.models, handed)
        self._index = Index(records(self.db))
        context["query"] = query
        context["found"] = [{k: one.get(k) for k in ("kind", "id", "title", "detail", "at",
                                                      "relevance", "recency")}
                            for one in self._index.search(query["keywords"])]
        return context

    @staticmethod
    def _page(url: str) -> Dict[str, Any]:
        """The public page, as anyone could read it - never signed in."""
        try:
            from .tools import fetch
            said = fetch(url)
        except Exception as exc:
            return {"url": url, "error": str(exc)[:200]}
        if isinstance(said, dict):
            return {"url": url, "title": str(said.get("title") or "")[:300],
                    "text": str(said.get("text") or "")[:PAGE_CHARS],
                    **({"error": str(said["error"])[:200]} if said.get("error") else {})}
        return {"url": url, "text": str(said)[:PAGE_CHARS]}

    @staticmethod
    def _folder(folder: Path) -> Dict[str, Any]:
        out: Dict[str, Any] = {"path": str(folder)}
        try:
            entries = sorted(folder.iterdir(), key=lambda one: (not one.is_dir(), one.name))
        except OSError as exc:
            out["error"] = str(exc)[:200]
            return out
        out["entries"] = [one.name + ("/" if one.is_dir() else "")
                          for one in entries[:MOST_ENTRIES]]
        out["more"] = max(0, len(entries) - MOST_ENTRIES)
        for name in ("README.md", "README.txt", "README", "readme.md"):
            readme = folder / name
            if readme.is_file():
                try:
                    out["readme"] = readme.read_text(encoding="utf-8",
                                                     errors="replace")[:README_CHARS]
                except OSError:
                    pass
                break
        return out

    @staticmethod
    def _file(path: Path) -> Dict[str, Any]:
        out: Dict[str, Any] = {"path": str(path)}
        try:
            out["size"] = path.stat().st_size
            if path.suffix.lower() in (".md", ".txt", ".py", ".csv", ".json", ".tex", ".r",
                                       ".js", ".html", ".yaml", ".yml", ".toml", ".ipynb"):
                out["start"] = path.read_text(encoding="utf-8",
                                              errors="replace")[:README_CHARS]
        except OSError as exc:
            out["error"] = str(exc)[:200]
        return out

    def _unit(self, draft: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not draft.get("unit_id"):
            return None
        try:
            return self.app.state_heartbeat.experience.unit(draft["unit_id"])
        except Exception:
            return None

    def plan(self, draft: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """The HIGH-LEVEL plan, in the user's language - one call, from what
        was collected and found: quick, for the user to check. The detailed
        plan is the run's, which looks further with its own tools."""
        from ...models.base import ModelRequest
        handed = {"their instruction": asked_of(draft) or "(none - go by what they handed over)",
                  "what they handed over": draft.get("text") or "(nothing typed or selected)",
                  "folders they gave": draft.get("folders") or [],
                  "files they gave": draft.get("files") or [], "context": context}
        response = self.app.models.invoke(ModelRequest(
            messages=[{"role": "user", "content": json.dumps(handed, ensure_ascii=False,
                                                             indent=1)[:60000]}],
            system=PLAN_PROMPT, max_tokens=2000, task="work_draft",
            language=in_display("every field of the plan and the context summary - names of "
                                "files, people, products and code keep their own wording")),
            role="primary")
        from ...heartbeat.states.heartbeat import _as_json
        said = _as_json(getattr(response, "text", "") or "") or {}
        context["summary"] = str(said.get("context_summary") or "")[:1500]
        self._set(draft["id"], context=context)
        return plan_of(said, fallback=asked_of(draft) or draft.get("text") or "")

    # -- the user's side ------------------------------------------------------------------------
    def start_apart(self, draft_id: str, plan: Optional[Dict[str, Any]] = None,
                    folders: Optional[List[str]] = None,
                    options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """START, ANSWERED AT ONCE: setting the work going - its project
        made, its folders granted, room found, its process started - took
        seconds, the box held open meanwhile. Now it is done apart: the
        draft is "starting" until it is "started", or back where it was with
        why it did not start; how it went is in the draft (`get`, "start")."""
        draft = self.get(draft_id)
        if draft is None:
            return {"error": "no such piece of work"}
        if draft["status"] in (STARTED, STARTING):
            return {"error": "it was started already", "run": draft["run_id"]}
        if draft["status"] == WRITING:
            return {"error": "say what you want done first"}
        if draft["status"] in (COLLECTING, DRAFTING):
            # STARTED BEFORE ITS PLAN IS DRAFTED (the user, 2026-10-26): it
            # starts once it is, with these settings - nothing more to approve.
            way = str((plan or {}).get("way") or "auto")
            kept = dict(draft.get("options") or {},
                        **{k: v for k, v in (options or {}).items()
                           if k in ("limits", "packages", "sound", "unconfined")},
                        start_when_ready={"way": way, "at": _now(),
                                          # where it works, as the box said it
                                          **({"folders": [str(one) for one in folders]}
                                             if folders is not None else {})})
            self._set(draft_id, options=kept, error="")
            self._wait_then_start(draft_id)
            return {"starting": True, "draft": draft_id, "when_ready": True}
        before = draft["status"]
        with self._lock:
            self._starting.add(draft_id)
            self._started.pop(draft_id, None)
        self._set(draft_id, status=STARTING, error="")

        def go() -> None:
            try:
                said = self.start(draft_id, plan=plan, folders=folders, options=options)
            except Exception as exc:                 # pragma: no cover - said, not raised
                log.exception("the work %s could not be started", draft_id)
                said = {"error": f"{type(exc).__name__}: {exc}"[:600]}
            if said.get("error"):
                self._set(draft_id, status=before, error=str(said["error"])[:600])
            with self._lock:
                self._started[draft_id] = {k: v for k, v in said.items()
                                           if isinstance(v, (str, int, float, bool, type(None)))}
                self._starting.discard(draft_id)

        threading.Thread(target=go, name=f"work-start-{draft_id}", daemon=True).start()
        return {"starting": True, "draft": draft_id}

    def _wait_then_start(self, draft_id: str) -> None:
        with self._lock:
            if draft_id in self._waiting:
                return
            self._waiting.add(draft_id)
        threading.Thread(target=self._when_ready, args=(draft_id,),
                         name=f"work-start-when-ready-{draft_id}", daemon=True).start()

    def _when_ready(self, draft_id: str, every: float = 1.0,
                    most: float = 30 * 60.0) -> None:
        """A draft started before its plan was drafted: once it is - or
        could not be, and is to be written from what was handed over - it
        is started with the plan as drafted, the way chosen (or the plan's
        own, "auto") and the settings given."""
        try:
            began = time.monotonic()
            while time.monotonic() - began < most:
                draft = self.get(draft_id)
                if draft is None or draft["status"] in (CANCELLED, STARTED, STARTING):
                    return
                wanted = (draft.get("options") or {}).get("start_when_ready")
                if not wanted:
                    return
                if draft["status"] in (READY, FAILED):
                    folders = wanted.get("folders", draft.get("folders")) or []
                    held = self.app.work.folder_in_use(folders) \
                        if hasattr(self.app.work, "folder_in_use") else None
                    if held:
                        # STARTED BY ITSELF: never into a folder other work is
                        # in - it waits for the folder, and says so.
                        said = f"waiting for the folder {held['folder']}: \"{held['by']}\" " \
                               "is working in it"
                        if wanted.get("held") != said:
                            self._set(draft_id, options=dict(
                                draft.get("options") or {},
                                start_when_ready=dict(wanted, held=said)))
                        began = time.monotonic()
                        time.sleep(every)
                        continue
                    options = {k: v for k, v in (draft.get("options") or {}).items()
                               if k != "start_when_ready"}
                    self._set(draft_id, options=options)
                    plan = dict(draft.get("plan") or {})
                    if wanted.get("way") in WAYS:
                        plan["way"] = wanted["way"]
                    self.start_apart(draft_id, plan=plan,
                                     folders=wanted.get("folders", draft.get("folders")))
                    return
                time.sleep(every)
            self._set(draft_id, error="its plan was not drafted in time - start it again")
        finally:
            with self._lock:
                self._waiting.discard(draft_id)

    def _resume_waiting(self) -> None:
        """Drafts started before their plan was drafted, when the application
        closed: waited for again."""
        try:
            rows = self.db.query("SELECT id FROM work_drafts WHERE options LIKE ? AND "
                                 "status IN (?, ?, ?, ?)",
                                 ("%start_when_ready%", COLLECTING, DRAFTING, READY, FAILED))
        except Exception:                        # pragma: no cover - a database before drafts
            return
        for row in rows:
            self._wait_then_start(row["id"])

    def start(self, draft_id: str, plan: Optional[Dict[str, Any]] = None,
              folders: Optional[List[str]] = None,
              options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Started, with the plan as the user left it: on its project or task,
        or - handed over - as a project of its own under Delegated work."""
        draft = self.get(draft_id)
        if draft is None:
            return {"error": "no such piece of work"}
        if draft["status"] == STARTED:
            return {"error": "it was started already", "run": draft["run_id"]}
        if draft["status"] in (WRITING, COLLECTING, DRAFTING):
            return {"error": "its plan is not drafted yet - Create it first"}
        approved = plan_of(plan if isinstance(plan, dict) else draft.get("plan") or {},
                           fallback=asked_of(draft) or draft.get("text") or "")
        edited = isinstance(plan, dict) and plan_of(plan) != plan_of(draft.get("plan") or {})
        folders = [str(one) for one in (folders if folders is not None
                                        else draft.get("folders") or []) if str(one).strip()]
        experience = self.app.state_heartbeat.experience
        work = self.app.work
        held = work.folder_in_use(folders) if hasattr(work, "folder_in_use") else None
        if held:
            return {"error": work.folder_said(held), "folder_held": held}
        # The box's settings as the user left them, over what it opened with.
        options = dict(draft.get("options") or {},
                       **{k: v for k, v in (options or {}).items()
                          if k in ("limits", "packages", "sound", "unconfined")})
        if draft.get("unit_id"):
            unit = experience.unit(draft["unit_id"])
            if unit is None:
                return {"error": "its project or task is gone"}
            made = None
        else:
            name = approved["title"] or "Delegated work"
            # A TO-DO HANDED OVER AGAIN: a version of its own, named so -
            # "(V2)" - its folder too.
            if draft.get("source") == "todo" and options.get("todo"):
                options["version"] = self._next_version(str(options["todo"]), draft_id)
                if options["version"] > 1:
                    name = f"{name} (V{options['version']})"
            made = experience.store.create_work_project(name)
            # GIVEN NO FOLDER: what it makes goes in agent_work, where all the
            # agents' work is, in a folder named after it - a second of the
            # same name told apart by when.
            from .runner import WORK_DIR
            output = "" if folders else str(output_folder_for(
                Path(work.app_policy.workspace_root) / WORK_DIR, name))
            experience.store.mark_delegated(made["unit_id"], draft.get("source") or "delegate",
                                            output=output)
            unit = experience.unit(made["unit_id"])
        for path in folders:
            said = work.grant(unit["id"], path)
            if said.get("error"):
                if made is not None:
                    experience.store.drop_workspace(unit["id"])
                return {"error": said["error"]}
        said = work.start(unit, instruction_of(draft, approved, folders),
                          limits=options.get("limits") or {},
                          packages=options.get("packages"), sound=options.get("sound"),
                          way=approved["way"],
                          **({"unconfined": True} if options.get("unconfined") else {}))
        if said.get("error"):
            if made is not None:
                experience.store.drop_workspace(unit["id"])
            return {"error": said["error"]}
        self._set(draft_id, status=STARTED, plan=approved, options=options,
                  plan_by="user" if edited else "agent", run_id=str(said.get("run") or ""),
                  unit_id=unit["id"])
        return dict(said, unit=unit["id"], draft=draft_id)

    def compose(self, draft_id: str, instruction: str = "", title: str = "") -> Dict[str, Any]:
        """The user's instruction, as they wrote it in the box - then the
        context collected and the plan drafted, for them to approve. A
        `title` the user gave (the shortcut's box) is the work's title: said
        to the planner with the instruction, and the plan's own in place of
        the one the planner would have named it."""
        draft = self.get(draft_id)
        if draft is None:
            return {"error": "no such piece of work"}
        if draft["status"] != WRITING:
            return {"error": "its plan is already being drafted", "draft": draft}
        instruction = str(instruction or "").strip()[:20000]
        title = " ".join(str(title or "").split())[:120]
        if title:
            instruction = f"{title}\n\n{instruction}" if instruction else title
        if not (instruction or str(draft.get("text") or "").strip() or draft.get("folders")
                or draft.get("files") or draft.get("unit_id")):
            return {"error": "say what you want done"}
        options = dict(draft.get("options") or {}, instruction=instruction)
        if title:
            options["title"] = title
        self._set(draft_id, options=options, status=COLLECTING)
        self._collect_apart(draft_id)
        return {"draft": self.get(draft_id)}

    def remove(self, draft_id: str) -> Dict[str, Any]:
        """A DRAFT OR A WORK'S RESULT REMOVED, as the user confirmed (the user,
        2026-10-26): not shown again - in the to-do's work, in Work's drafts,
        and, started, its project not in Work's list (`remove_unit`). NOTHING
        IS DELETED: its files stay where they are. Not while it is going."""
        draft = self.get(draft_id)
        if draft is None:
            return {"error": "no such piece of work"}
        if draft["status"] == STARTING or (draft["status"] == STARTED and draft.get("unit_id")
                                           and self._came_of(draft["unit_id"])["state"]
                                           == "ongoing"):
            return {"error": "it is going - stop it first"}
        if draft["status"] == STARTED and draft.get("unit_id"):
            said = self.app.state_heartbeat.experience.remove_unit(draft["unit_id"])
            if said.get("error") and said["error"] != "no such project":
                return said
        options = {k: v for k, v in (draft.get("options") or {}).items()
                   if k != "start_when_ready"}
        options["removed"] = True
        self._set(draft_id, options=options)
        return {"removed": draft_id}

    def discard(self, draft_id: str) -> bool:
        """A saved draft thrown away: as one cancelled."""
        return self.cancel(draft_id)

    def cancel(self, draft_id: str) -> bool:
        draft = self.get(draft_id)
        if draft is None or draft["status"] in (STARTED, STARTING):
            return False
        if (draft.get("options") or {}).get("start_when_ready"):
            # Cancelled while it waited for its plan: it does not start.
            self._set(draft_id, options={k: v for k, v in draft["options"].items()
                                         if k != "start_when_ready"})
        self._set(draft_id, status=CANCELLED)
        return True


#: Files that run as programs when opened - shown in their folder, never
#: opened by a click on the page.
PROGRAM_SUFFIXES = frozenset((
    ".exe", ".com", ".bat", ".cmd", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse",
    ".wsf", ".wsh", ".msc", ".msi", ".msp", ".scr", ".pif", ".cpl", ".hta", ".jar",
    ".lnk", ".url", ".reg", ".inf", ".appref-ms", ".application", ".py", ".pyw",
    ".sh", ".command", ".app", ".pkg", ".dmg", ".run", ".bin", ".desktop"))


def runs_as_program(path: Path) -> bool:
    """Whether opening the file would run it: by its suffix, or - off
    Windows - its being marked executable."""
    import os
    if Path(path).suffix.lower() in PROGRAM_SUFFIXES:
        return True
    return os.name != "nt" and os.access(str(path), os.X_OK)


def output_folder_for(workspace: Path, name: str,
                      now: Optional[datetime] = None) -> Path:
    """The folder in agent_work named after the work - its name as a folder
    may have it - or, where one of that name is there already, the same name
    with the date and time, so copies are told apart."""
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", str(name)).strip(" .")
    clean = re.sub(r"\s+", " ", clean)[:80].strip(" .") or "Delegated work"
    folder = Path(workspace) / clean
    if not folder.exists():
        return folder
    stamp = (now or datetime.now()).strftime("%Y-%m-%d %H%M")
    folder = Path(workspace) / f"{clean} {stamp}"
    count = 2
    while folder.exists():
        folder = Path(workspace) / f"{clean} {stamp} ({count})"
        count += 1
    return folder


def plan_of(said: Any, fallback: str = "") -> Dict[str, Any]:
    """A plan as kept: each part there, lists as lists of short lines."""
    said = said if isinstance(said, dict) else {}
    out: Dict[str, Any] = {}
    for part in PLAN_PARTS:
        value = said.get(part)
        if part in ("title", "understanding", "goal"):
            out[part] = str(value or "").strip()[:600]
        else:
            items = value if isinstance(value, list) else str(value or "").splitlines()
            out[part] = [str(one).strip(" -*\t")[:400] for one in items
                         if str(one).strip(" -*\t")][:12]
    if not out["title"]:
        out["title"] = " ".join(str(fallback).split()[:8])[:80] or "Delegated work"
    # The way, as the planner suggested it or the user chose: thorough when
    # nothing says otherwise.
    way = str(said.get("way") or "").strip().lower()
    out["way"] = way if way in WAYS else "full"
    out["way_why"] = str(said.get("way_why") or "").strip()[:400]
    if out["way"] == "full" and not any("readme" in one.lower() for one in out["deliverables"]):
        out["deliverables"].append("README.md - what was made, where it is, how to check "
                                   "it and how to use it")
    return out


def asked_of(draft: Dict[str, Any]) -> str:
    """The instruction the user wrote in the box, before Create."""
    return str((draft.get("options") or {}).get("instruction") or "").strip()


def instruction_of(draft: Dict[str, Any], plan: Dict[str, Any],
                   folders: Optional[List[str]] = None) -> str:
    """What the run is told: the user's instruction and what they handed
    over, WHERE IT WORKS, and the plan they approved - theirs, word for word.
    `folders`: the ones the user gave it, as they left them in the box."""
    lines = []
    if asked_of(draft):
        lines += ["THE USER'S INSTRUCTION:", asked_of(draft), ""]
    if str(draft.get("text") or "").strip():
        lines += ["WHAT THE USER HANDED OVER:", str(draft["text"]).strip(), ""]
    for path in draft.get("files") or []:
        lines.append(f"FILE THEY HANDED OVER: {path}")
    if draft.get("files"):
        lines.append("")
    window = draft.get("window") or {}
    if window.get("title") or window.get("url"):
        lines += [f"IT CAME FROM: {window.get('title') or ''} {window.get('url') or ''}".strip(),
                  ""]
    found = (draft.get("context") or {}).get("found") or []
    if found:
        lines.append("REFERENCES FROM THE USER'S OWN RECORDS - found by the work's words, "
                     "the most relevant and recent first; search further with "
                     "search_records, and read files and the web, as the detail needs:")
        lines += [f"  - [{one.get('kind')}] {one.get('title')} ({str(one.get('at') or '')[:10]})"
                  for one in found]
        lines.append("")
    # WHERE IT WORKS (the user, 2026-10-26): a folder they gave is the work's
    # own - worked in directly; none, a folder of its own is.
    given = [str(one) for one in (folders if folders is not None
                                  else draft.get("folders") or []) if str(one).strip()]
    if given:
        lines += ["WHERE YOU WORK - what the user gave for this work. Work in it "
                  "DIRECTLY: in a folder, change, add to and fix what is there as the work "
                  "needs; a FILE, change that file in place - nothing beside it is yours "
                  "to change. It is their copy, so leave alone what the work does not "
                  "need changed:",
                  *[f"  - {one}{' (a file)' if Path(one).is_file() else ''}" for one in given],
                  ""]
    elif not draft.get("unit_id"):
        lines += ["WHERE YOU WORK: no folder was given - make what you make in your own "
                  "folder, the one you may write in.", ""]
    lines += ["THE PLAN THE USER APPROVED - follow it; it is theirs:"
              if plan.get("way") == "quick" else
              "THE PLAN THE USER APPROVED - follow it; it is theirs, and the detailed "
              "contract is drafted from it:",
              f"Title: {plan.get('title')}",
              f"What they want: {plan.get('understanding')}",
              f"Goal: {plan.get('goal')}"]
    for name, label in (("steps", "Steps"), ("deliverables", "Deliverables"),
                        ("out_of_scope", "Not part of it"), ("questions", "Open questions")):
        if plan.get(name):
            lines.append(f"{label}:")
            lines += [f"  - {one}" for one in plan[name]]
    return "\n".join(lines)


__all__ = ["Drafts", "plan_of", "instruction_of", "asked_of", "WRITING", "output_folder_for", "PLAN_PARTS", "COLLECTING", "READY",
           "STARTED", "CANCELLED", "FAILED"]
