"""What a state's work was done with or on, linked and kept when the state is
written.

The agent heartbeat's first step writes what the user is doing as states,
and it is the one moment when everything about that stretch is still at
hand: the raw activity is pruned after about a week, a file is edited again,
a page goes behind a sign-in. So as each state is written, its MATERIALS -
the picture of a slide, the document or email read, the file opened or
saved, the page, the user's own words to an agent - are linked to it and
copies kept, in the workspace:

  - a PDF, Word, PowerPoint or Excel file: the raw file as it was then,
    and its text, extracted - two copies, so a model that cannot read the
    file still has what it says;
  - any other file that is text: its text; one that is not: the raw file;
  - a picture: the one the listener took;
  - a window read on screen, an online document or an email: everything
    read from that window in the stretch, merged;
  - the user's message to an agent: exactly what they wrote.

A stage of the user's work then takes its references from its states'
materials and searches for nothing again. What no state links is never
copied; the pictures taken that no state wanted are deleted at the end of
the run that read them.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

log = logging.getLogger(__name__)

#: A file larger than this is linked by its path only, not copied.
COPY_AT_MOST = 50 * 1024 * 1024

#: The most text kept of one material.
TEXT_AT_MOST = 200_000

#: Files whose text is extracted, beside the raw copy.
DOCUMENTS = (".pdf", ".docx", ".pptx")
#: Files that are text already.
TEXTS = (".txt", ".md", ".csv", ".tsv", ".json", ".tex", ".bib", ".py", ".r",
         ".js", ".ts", ".html", ".htm", ".css", ".yaml", ".yml", ".toml",
         ".ipynb", ".sql", ".sh", ".log", ".xml", ".rst")

# Direct chat's messages are material too (msg_...), not only the
# Listener's events (PR #8, 2026-10-27: they were dropped as linked).
_ID = re.compile(r"(?:event|msg)_[0-9a-f]{8,}")

#: How a message the agent's own harness put in the conversation begins -
#: a subagent's report, a task notification. Not the user's words.
HARNESS = ("Another Claude session sent a message", "<agent-message",
           "<task-notification", "<system-reminder", "<command-",
           "Caveat: The messages below")


def _from_the_harness(text: Any) -> bool:
    return str(text or "").lstrip().startswith(HARNESS)
_SESSION = re.compile(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){0,4}")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _data(row: Dict[str, Any]) -> Dict[str, Any]:
    try:
        out = json.loads(row.get("data") or "{}")
    except ValueError:
        return {}
    return out if isinstance(out, dict) else {}


def _slug(text: str, most: int = 60) -> str:
    return re.sub(r"[^\w一-鿿.]+", "-", str(text)).strip("-")[:most] or "x"


def event_ids(text: Any) -> List[str]:
    """The event ids named anywhere in a value, once each, in order."""
    found: List[str] = []
    for one in _ID.findall(json.dumps(text, ensure_ascii=False)
                           if not isinstance(text, str) else text):
        if one not in found:
            found.append(one)
    return found


def extract_text(path: Path) -> str:
    """A file's text, by the listener's own readers where it has them."""
    kind = path.suffix.lower()
    try:
        if kind in DOCUMENTS:
            from ...listener.providers.filesystem import _document_text
            return _document_text(str(path), kind, TEXT_AT_MOST) or ""
        if kind in TEXTS:
            return path.read_text(encoding="utf-8", errors="replace")[:TEXT_AT_MOST]
    except Exception:            # pragma: no cover - an unreadable file
        log.debug("could not read the text of %s", path, exc_info=True)
    return ""


class Materials:
    """Links materials to states, keeps their copies, and reads them back."""

    def __init__(self, db: Any, root: Optional[Path]) -> None:
        self.db = db
        self.root = Path(root) if root else None

    # -- reading back -------------------------------------------------------
    def of_states(self, state_ids: Sequence[str]) -> Dict[str, List[Dict[str, Any]]]:
        out: Dict[str, List[Dict[str, Any]]] = {}
        ids = list(state_ids)
        for start in range(0, len(ids), 400):
            chunk = ids[start:start + 400]
            holes = ", ".join("?" for _ in chunk)
            for row in self.db.query(
                    f"SELECT * FROM state_materials WHERE state_id IN ({holes}) "
                    f"ORDER BY created_at", tuple(chunk)):
                row = dict(row)
                try:
                    row["copies"] = json.loads(row.get("copies") or "{}")
                except ValueError:
                    row["copies"] = {}
                out.setdefault(row["state_id"], []).append(row)
        return out

    def linked(self, event_id: str) -> bool:
        return bool(self.db.query("SELECT 1 FROM state_materials WHERE "
                                  "event_id = ? LIMIT 1", (event_id,)))

    # -- linking --------------------------------------------------------------
    def link(self, state: Any, chosen: Iterable[str], looked_at: Dict[str, Any]
             ) -> List[Dict[str, Any]]:
        """Link a state's materials and keep their copies.

        Three sources, so what matters does not hang on the model's memory:
        what the model named; every event the state's evidence and sources
        already cite; and the user's own messages to the agent session the
        state is about, in its time.
        """
        meta = getattr(state, "metadata", None) or {}
        picks: Dict[str, str] = {}
        for one in event_ids(list(chosen)):
            picks.setdefault(one, "model")
        for one in event_ids([meta.get("evidence"), meta.get("sources"),
                              meta.get("reasoning")]):
            picks.setdefault(one, "evidence")
        for one in self._said_to_the_session(state, looked_at):
            picks.setdefault(one, "session")
        # A DIRECT-CHAT TURN KEPT WHOLE: one side of it cited, the user's
        # message and the agent's reply both kept, each as who said it.
        for one in list(picks):
            if one.startswith("msg_"):
                for message in self._chat_turn(one):
                    picks.setdefault(message["id"], "chat")
        out = []
        for event_id, how in picks.items():
            try:
                made = self._link_one(state.id, event_id, how, looked_at)
            except Exception:        # pragma: no cover - one material
                log.warning("could not keep material %s", event_id, exc_info=True)
                made = None
            if made:
                out.append(made)
        return out

    def _chat_turn(self, message_id: str) -> List[Dict[str, Any]]:
        """The direct-chat turn a message is in: the user's message and the
        agent's reply to it - not what the Chat debugger sent, which is a
        test."""
        rows = self.db.query(
            "SELECT m.* FROM messages m WHERE m.conversation_id = "
            "(SELECT conversation_id FROM messages WHERE id = ?) "
            "ORDER BY m.created_at, m.rowid", (message_id,))
        index = next((i for i, row in enumerate(rows) if row["id"] == message_id), None)
        if index is None:
            return []
        start = index
        while start > 0 and rows[start]["role"] != "user":
            start -= 1
        end = start + 1
        while end < len(rows) and rows[end]["role"] != "user":
            end += 1
        out = []
        for row in rows[start:end]:
            try:
                origin = json.loads(row["metadata"] or "{}").get("origin")
            except ValueError:
                origin = None
            if row["role"] in ("user", "assistant") and origin != "chat_debugger":
                out.append(row)
        return out

    def _said_to_the_session(self, state: Any, looked_at: Dict[str, Any]
                             ) -> List[str]:
        """The user's own messages in the agent session the state names -
        by its id or its project folder - while the state ran."""
        text = " ".join(str(part) for part in (
            getattr(state, "title", ""), getattr(state, "detail", ""),
            json.dumps((getattr(state, "metadata", None) or {}).get("evidence") or [],
                       ensure_ascii=False),
            json.dumps((getattr(state, "metadata", None) or {}).get("sources") or [],
                       ensure_ascii=False)))
        began = str(looked_at.get("from") or "")
        ended = str(looked_at.get("to") or "")
        if not began or not ended:
            return []
        rows = self.db.query(
            "SELECT id, data FROM raw_events WHERE event_type = 'assistant.message' "
            "AND timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
            (began, ended))
        sessions = {s[:8] for s in _SESSION.findall(text.lower())}
        out = []
        for row in rows:
            data = _data(row)
            if data.get("role") != "user":
                continue
            if _from_the_harness(data.get("text")):
                continue
            session = str(data.get("session_id") or "")[:8].lower()
            project = Path(str(data.get("project") or "")).name.lower()
            if (session and session in sessions) or (
                    len(project) >= 4 and project in text.lower()):
                out.append(row["id"])
        return out

    def _link_one(self, state_id: str, event_id: str, how: str,
                  looked_at: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        # One copy of one event, whichever state links it first. Looked for
        # BEFORE the raw row, because the copy is what outlives it: the raw
        # activity is let go after about a week, and an event linked again
        # after that still has its copy to link to.
        known = self.db.query("SELECT kind, title, location, copies FROM "
                              "state_materials WHERE event_id = ? LIMIT 1",
                              (event_id,))
        if known:
            said = dict(known[0])
            try:
                said["copies"] = json.loads(said.get("copies") or "{}")
            except ValueError:
                said["copies"] = {}
        else:
            rows = self.db.query("SELECT id, event_type, timestamp, data FROM "
                                 "raw_events WHERE id = ?", (event_id,))
            if not rows and event_id.startswith("msg_"):
                # Direct chat lives in its messages, not the Listener's events.
                for message in self._chat_turn(event_id):
                    if message["id"] == event_id:
                        rows = [{"id": event_id, "timestamp": message["created_at"],
                                 "event_type": "assistant.message",
                                 "data": json.dumps({
                                     "text": message["content"], "role": message["role"],
                                     "tool": "direct chat",
                                     "session_id": message["conversation_id"]})}]
                        break
            if not rows:
                return None
            row = rows[0]
            said = self._keep(row, str(row["event_type"]), _data(row), looked_at)
            if said is None:
                return None
        self.db.execute(
            "INSERT OR IGNORE INTO state_materials (state_id, event_id, kind, "
            "title, location, copies, linked_by, created_at) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?)",
            (state_id, event_id, said["kind"], said["title"][:300],
             said["location"][:1000],
             json.dumps(said["copies"], ensure_ascii=False), how, _now()))
        return {"event_id": event_id, **said}

    # -- the copies ---------------------------------------------------------------
    def _folder(self, row: Dict[str, Any]) -> Optional[Path]:
        if self.root is None:
            return None
        folder = self.root / "materials" / str(row["timestamp"])[:10]
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _write_text(self, row: Dict[str, Any], name: str, text: str,
                    head: List[str]) -> str:
        folder = self._folder(row)
        if folder is None or not text.strip():
            return ""
        path = folder / f"{row['id']}-{_slug(name)}.txt"
        path.write_text("\n".join(head + ["", "---", "", text[:TEXT_AT_MOST]]) + "\n",
                        encoding="utf-8")
        return str(path)

    def _keep(self, row: Dict[str, Any], kind: str, data: Dict[str, Any],
              looked_at: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        when = str(row["timestamp"])[:19]
        if kind == "screen.shot":
            path = str(data.get("path") or "")
            if not path or not Path(path).exists():
                return None
            return {"kind": "picture", "title": str(data.get("window_title") or ""),
                    "location": str(data.get("window_title") or ""),
                    "copies": {"picture": path}}
        if kind.startswith("filesystem."):
            path = Path(str(data.get("destination_path") or data.get("path") or ""))
            copies: Dict[str, str] = {}
            if path.is_file():
                # EVERY FILE ITS RAW COPY, as it was then; and its text - a
                # file that is text already is its own text copy, so the two
                # are one file rather than the same bytes twice.
                folder = self._folder(row)
                if folder is not None and path.stat().st_size <= COPY_AT_MOST:
                    raw = folder / f"{row['id']}-{_slug(path.name)}"
                    shutil.copy2(path, raw)
                    copies["raw"] = str(raw)
                if path.suffix.lower() in TEXTS and copies.get("raw"):
                    copies["text"] = copies["raw"]
                else:
                    text = extract_text(path) or str(data.get("content") or "")
                    if text:
                        copies["text"] = self._write_text(
                            row, path.name, text,
                            [f"# {path.name}", f"- Original: {path}",
                             f"- As it was at: {when}"])
            elif data.get("content"):
                copies["text"] = self._write_text(
                    row, path.name or "file", str(data["content"]),
                    [f"# {path.name}", f"- Original: {path}",
                     f"- As read at: {when}"])
            return {"kind": "file", "title": path.name, "location": str(path),
                    "copies": {k: v for k, v in copies.items() if v}}
        if kind in ("screen.text", "application.focused", "application.focus_ended"):
            title = str(data.get("window_title") or "")
            text = self._all_read_from(title, looked_at) or str(data.get("text") or "")
            copies = {}
            if text:
                copies["text"] = self._write_text(
                    row, title, text,
                    [f"# {title}", f"- Window: {title}",
                     f"- Read on screen around: {when}",
                     "- Text only, as the listener read it; what was never on "
                     "screen is not here."])
            return {"kind": "screen", "title": title, "location": title,
                    "copies": {k: v for k, v in copies.items() if v}}
        if kind == "browser.navigation":
            title, url = str(data.get("title") or ""), str(data.get("url") or "")
            text = self._all_read_from(title, looked_at) if title else ""
            copies = {}
            if text:
                copies["text"] = self._write_text(
                    row, title or url, text,
                    [f"# {title}", f"- Address: {url}", f"- Seen: {when}"])
            return {"kind": "page", "title": title, "location": url,
                    "copies": {k: v for k, v in copies.items() if v}}
        if kind == "assistant.message":
            text = str(data.get("text") or "")
            if _from_the_harness(text):
                # Put in the conversation by the agent's own harness - a
                # subagent's report - and kept as the user's words it would
                # be quoted as theirs.
                return None
            where = f"{data.get('tool') or 'agent'} session " \
                    f"{str(data.get('session_id') or '')[:8]} in {data.get('project') or ''}"
            copies = {"text": self._write_text(
                row, f"{data.get('role')}-message", text,
                [f"# {data.get('role') or ''} message", f"- In: {where}",
                 f"- At: {when}"])}
            # The user's words are a message; an agent's answer is a
            # reply - often a result in its own right, and never to be
            # quoted as the user's.
            return {"kind": "message" if data.get("role") == "user" else "reply",
                    "title": text[:120], "location": where,
                    "copies": {k: v for k, v in copies.items() if v}}
        return None

    def _all_read_from(self, title: str, looked_at: Dict[str, Any]) -> str:
        """Everything read from one window in the stretch, in the order each
        part first appeared - scrolling through it reads it in pieces."""
        began = str(looked_at.get("from") or "")
        ended = str(looked_at.get("to") or "")
        if not title or not began or not ended:
            return ""
        seen, lines = set(), []
        for row in self.db.query(
                "SELECT data FROM raw_events WHERE event_type = 'screen.text' "
                "AND timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
                (began, ended)):
            data = _data(row)
            if str(data.get("window_title") or "") != title:
                continue
            for line in str(data.get("text") or "").splitlines():
                line = line.strip()
                if line and line not in seen:
                    seen.add(line)
                    lines.append(line)
        return "\n".join(lines)

    # -- pinning down what a stage names ---------------------------------------------
    #: A path cut short in the middle is looked for this deep below the part
    #: that was kept, through at most this many folders.
    SEARCH_DEPTH, SEARCH_FOLDERS = 6, 5000

    @classmethod
    def whole_path(cls, location: str) -> Optional[str]:
        """A local path written with "..." in the middle, found whole: the
        folder or file under the part before the "..." whose own path ends
        with the part after it. None when it is not one, or not found."""
        text = str(location or "").strip()
        for mark in ("...", "…"):
            if mark in text:
                head, _, tail = text.partition(mark)
                break
        else:
            return None
        head = Path(head.rstrip("\\/"))
        wanted = [part for part in re.split(r"[\\/]+", tail) if part]
        if not wanted or not head.is_dir():
            return None
        import os
        seen = 0
        for here, folders, files in os.walk(head):
            depth = len(Path(here).relative_to(head).parts)
            seen += 1
            if seen > cls.SEARCH_FOLDERS:
                return None
            if depth >= cls.SEARCH_DEPTH:
                folders[:] = []
            for name in folders + files:
                candidate = Path(here) / name
                if [p.lower() for p in candidate.parts[-len(wanted):]] == \
                        [p.lower() for p in wanted]:
                    return str(candidate)
            # Not into folders that are never the user's work.
            folders[:] = [f for f in folders if not f.startswith(".")
                          and f not in ("node_modules", "__pycache__")]
        return None

    @staticmethod
    def transcript(location: str) -> Optional[str]:
        """An agent session named by its id - the whole conversation, kept by
        the agent's own client on this computer: Claude Code's, or Codex's."""
        text = str(location or "").lower()
        found = _UUID.search(text)
        if found:
            session, rest = found.group(0), ""
        else:
            # Named by the first eight of its id, as people do -
            # "session 7d1e5b20".
            short = re.search(r"session\s+([0-9a-f]{8})\b", text)
            if not short:
                return None
            session, rest = short.group(1), "*"
        home = Path.home()
        # Codex names the file by its time and then the id: the id is
        # looked for anywhere in the name, once ("**" is no pattern here).
        for pattern in (f".claude/projects/*/{session}{rest}.jsonl",
                        f".codex/sessions/*/*/*/*{session}*.jsonl"):
            for path in home.glob(pattern):
                if path.is_file():
                    return str(path)
        return None

    # -- kept when a stage is written ------------------------------------------------
    def copy_now(self, path: Path) -> Dict[str, str]:
        """A local file a stage names that no state kept a copy of - kept
        now, as it is now: the raw file and its text (one file, for a file
        that is text already). Said to be today's version, not the one the
        stage was about."""
        path = Path(path)
        if self.root is None or not path.is_file():
            return {}
        folder = self.root / "materials" / "now" / datetime.now().strftime("%Y-%m-%d")
        folder.mkdir(parents=True, exist_ok=True)
        stem = f"{_slug(str(abs(hash(str(path))) % 10**8))}-{_slug(path.name)}"
        copies: Dict[str, str] = {}
        if path.stat().st_size <= COPY_AT_MOST:
            raw = folder / stem
            shutil.copy2(path, raw)
            copies["raw"] = str(raw)
        if path.suffix.lower() in TEXTS and copies.get("raw"):
            copies["text"] = copies["raw"]
            return copies
        text = extract_text(path)
        if text:
            out = folder / f"{stem}.txt"
            out.write_text("\n".join([
                f"# {path.name}", f"- Original: {path}",
                f"- As it is now ({datetime.now().strftime('%Y-%m-%d')}), not "
                f"necessarily as it was then", "", "---", "", text]) + "\n",
                encoding="utf-8")
            copies["text"] = str(out)
        return copies

    def commit(self, repo: Path, sha: str) -> Optional[Dict[str, Any]]:
        """A commit named in the work, if it is one in this repository: its
        hash, the files it changed, and its diff kept as text - what a
        deliverable of code is, and something a model can look up."""
        from ...common import children
        repo = Path(repo).resolve()
        if self.root is None or not (repo / ".git").exists():
            return None
        try:
            done = children.run_short(
                ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet",
                 f"{sha}^{{commit}}"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=20)
            full = done.stdout.strip()
            if done.returncode != 0 or not full:
                return None
            shown = children.run_short(
                ["git", "-C", str(repo), "show", "--stat", "--patch",
                 "--format=%H%n%an %ad%n%n%s%n%n%b", full],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=60)
            names = children.run_short(
                ["git", "-C", str(repo), "show", "--name-only", "--format=", full],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=30)
        except Exception:            # pragma: no cover - no git here
            log.debug("could not read commit %s", sha, exc_info=True)
            return None
        files = [line for line in names.stdout.splitlines() if line.strip()]
        folder = self.root / "materials" / "commits"
        folder.mkdir(parents=True, exist_ok=True)
        out = folder / f"{_slug(repo.name)}-{full[:12]}.txt"
        if not out.exists():
            out.write_text(f"# Commit {full[:12]} in {repo}\n\n---\n\n"
                           + shown.stdout[:TEXT_AT_MOST], encoding="utf-8")
        subject = (shown.stdout.splitlines()[3:4] or [""])[0]
        return {"kind": "commit", "location": f"{repo} @ {full[:12]}",
                "commit": full, "repository": str(repo), "files": files[:60],
                "title": subject, "copies": {"text": str(out)},
                "access": "local"}

    # -- the states written before there were materials --------------------------
    def backfill(self, states: Any, days: int = 14) -> Dict[str, int]:
        """The states of the last fortnight - the raw activity still there -
        given materials from what their evidence already cites and the
        user's messages to the session they are about. By script: the
        model that wrote them is not asked again."""
        report = {"states": 0, "materials": 0}
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = self.db.query(
            "SELECT id FROM states WHERE created_at >= ? AND id NOT IN "
            "(SELECT DISTINCT state_id FROM state_materials)", (since,))
        for row in rows:
            state = states.get(row["id"])
            if state is None:
                continue
            meta = getattr(state, "metadata", None) or {}
            looked_at = dict(meta.get("looked_at") or {})
            if not looked_at.get("from"):
                starts = getattr(state, "starts_at", None)
                ends = getattr(state, "ends_at", None) or starts
                if starts is None:
                    continue
                looked_at = {"from": starts.isoformat(), "to": ends.isoformat()}
            made = self.link(state, [], looked_at)
            report["states"] += 1
            report["materials"] += len(made)
        return report

    # -- the pictures no state wanted -----------------------------------------------
    def drop_unlinked_pictures(self, looked_at: Dict[str, Any]) -> Dict[str, int]:
        """After a run's states are written: the pictures taken in the
        stretch it read that none of them linked, deleted. They can never
        be referred to - a stage takes its references from states, and the
        states of this stretch are written."""
        report = {"kept": 0, "deleted": 0}
        began = str(looked_at.get("from") or "")
        ended = str(looked_at.get("to") or "")
        if not began or not ended or self.root is None:
            return report
        root = self.root.resolve()
        for row in self.db.query(
                "SELECT id, data FROM raw_events WHERE event_type = 'screen.shot' "
                "AND timestamp >= ? AND timestamp <= ?", (began, ended)):
            if self.linked(row["id"]):
                report["kept"] += 1
                continue
            path = Path(str(_data(row).get("path") or ""))
            try:
                if path.is_file() and root in path.resolve().parents:
                    path.unlink()
                    report["deleted"] += 1
            except OSError:          # pragma: no cover - in use
                continue
        return report
