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
#: What the plan says - the user may change each.
PLAN_PARTS = ("title", "understanding", "goal", "steps", "deliverables", "out_of_scope",
              "questions")
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
 "deliverables": ["what they will get - each a file, a change or an answer,
                   and where; always a README.md saying how to check and use
                   what was made"],
 "out_of_scope": ["what you will not do, where it could be mistaken for part
                   of it"],
 "questions": ["only what you cannot settle from the context and would change
                the plan - often none"]}"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Drafts:
    """The `work_drafts` rows: each piece of work handed over, from its
    context to its run."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.db = app.db

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
        folders = [str(Path(one)) for one in folders or [] if str(one).strip()]
        files = [str(Path(one)) for one in files or [] if str(one).strip()]
        # A file handed over: its folder is where the work is done.
        for one in files:
            parent = str(Path(one).parent)
            if parent not in folders:
                folders.append(parent)
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
        return out

    def recent(self, most: int = 20) -> List[Dict[str, Any]]:
        return [self.get(row["id"]) for row in self.db.query(
            "SELECT id FROM work_drafts ORDER BY created_at DESC LIMIT ?", (most,))]

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
    def start(self, draft_id: str, plan: Optional[Dict[str, Any]] = None,
              folders: Optional[List[str]] = None) -> Dict[str, Any]:
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
        options = draft.get("options") or {}
        if draft.get("unit_id"):
            unit = experience.unit(draft["unit_id"])
            if unit is None:
                return {"error": "its project or task is gone"}
            made = None
        else:
            name = approved["title"] or "Delegated work"
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
        said = work.start(unit, instruction_of(draft, approved),
                          limits=options.get("limits") or {},
                          packages=options.get("packages"), sound=options.get("sound"))
        if said.get("error"):
            if made is not None:
                experience.store.drop_workspace(unit["id"])
            return {"error": said["error"]}
        self._set(draft_id, status=STARTED, plan=approved,
                  plan_by="user" if edited else "agent", run_id=str(said.get("run") or ""),
                  unit_id=unit["id"])
        return dict(said, unit=unit["id"], draft=draft_id)

    def compose(self, draft_id: str, instruction: str = "") -> Dict[str, Any]:
        """The user's instruction, as they wrote it in the box - then the
        context collected and the plan drafted, for them to approve."""
        draft = self.get(draft_id)
        if draft is None:
            return {"error": "no such piece of work"}
        if draft["status"] != WRITING:
            return {"error": "its plan is already being drafted", "draft": draft}
        instruction = str(instruction or "").strip()[:20000]
        if not (instruction or str(draft.get("text") or "").strip() or draft.get("folders")
                or draft.get("files") or draft.get("unit_id")):
            return {"error": "say what you want done"}
        options = dict(draft.get("options") or {}, instruction=instruction)
        self._set(draft_id, options=options, status=COLLECTING)
        self._collect_apart(draft_id)
        return {"draft": self.get(draft_id)}

    def cancel(self, draft_id: str) -> bool:
        draft = self.get(draft_id)
        if draft is None or draft["status"] == STARTED:
            return False
        self._set(draft_id, status=CANCELLED)
        return True


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
    if not any("readme" in one.lower() for one in out["deliverables"]):
        out["deliverables"].append("README.md - what was made, where it is, how to check "
                                   "it and how to use it")
    return out


def asked_of(draft: Dict[str, Any]) -> str:
    """The instruction the user wrote in the box, before Create."""
    return str((draft.get("options") or {}).get("instruction") or "").strip()


def instruction_of(draft: Dict[str, Any], plan: Dict[str, Any]) -> str:
    """What the run is told: the user's instruction and what they handed
    over, and the plan they approved - theirs, word for word."""
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
    lines += ["THE PLAN THE USER APPROVED - follow it; it is theirs, and the detailed "
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
