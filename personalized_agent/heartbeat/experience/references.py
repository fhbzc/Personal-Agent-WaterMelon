"""Whether a reference can be opened, and the backstop that keeps the copies
of references from filling the disk.

A reference's copies - its text, the raw file, its picture - are kept when
the state it belongs to is written (see materials.py). Whether the original
can be opened as well is said here: "local" (a file on this computer),
"public" (a page needing no sign-in, as far as can be told), "sign-in" (a
service behind the user's account) or "screen-only" (seen, with nowhere to
open it).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

#: Services behind the user's account: a page on one cannot be opened by an
#: agent without signing in, whatever its address says.
SIGN_IN_HOSTS = (
    "docs.google.com", "drive.google.com", "mail.google.com",
    "calendar.google.com", "slides.google.com", "overleaf.com",
    "outlook.office.com", "outlook.live.com", "office.com", "sharepoint.com",
    "onedrive.live.com", "dropbox.com", "notion.so", "slack.com",
    "app.slack.com", "teams.microsoft.com", "figma.com", "canva.com",
    "miro.com", "chatgpt.com", "claude.ai", "linkedin.com", "web.whatsapp.com",
    "wx.qq.com", "openreview.net/group", "openreview.net/forum?id=",
)

#: A service named in words rather than by its address - "Google Slides:
#: 'the deck'", "ChatGPT conversation '...'" - is behind a sign-in too.
SIGN_IN_NAMES = ("google", "gmail", "overleaf", "openreview", "chatgpt",
                 "claude.ai", "slack", "outlook", "teams", "notion", "dropbox",
                 "onedrive", "sharepoint", "figma", "canva", "wechat", "微信",
                 "linkedin", "whatsapp", "icloud")

#: Kinds of reference that are an account's however they are written.
SIGN_IN_KINDS = ("email", "message")

_LOCAL = re.compile(r"^(?:[A-Za-z]:[\\/]|/|~[\\/]|\\\\)")


def is_local(location: str) -> bool:
    return bool(_LOCAL.match(location.strip()))


def access_of(reference: Dict[str, Any]) -> str:
    """Whether an agent could open this reference itself."""
    location = str(reference.get("location") or "").strip()
    if is_local(location) and Path(location).exists():
        return "local"
    if is_local(location):
        return "screen-only"              # a path that is not on this computer
    if location.startswith(("http://", "https://")):
        low = location.lower()
        if any(host in low for host in SIGN_IN_HOSTS):
            return "sign-in"
        host = urlparse(location).hostname or ""
        if host in ("localhost", "127.0.0.1"):
            return "sign-in"              # the user's own running service
        return "public"
    if str(reference.get("kind") or "") in SIGN_IN_KINDS:
        return "sign-in"
    if any(name in location.lower() for name in SIGN_IN_NAMES):
        return "sign-in"
    return "screen-only"


def collect_garbage(db: Any, root: Optional[Path], keep_days: int,
                    cutoff: str = "") -> Dict[str, int]:
    """Delete the copies of references nothing can use any more.

    RUN ONLY ONCE THE STAGES ARE ALL WRITTEN, and measured back from the
    stage pass's own cutoff, not from now: every state up to the cutoff
    has been turned into stages, so a state from more than `keep_days`
    before it whose materials no stage cited will never be cited. Deleted:

      - the copies of materials linked only by such states (and the links);
      - any other file in the references folder older than that, that no
        stage cites - a copy from a deleted stage, a picture no state
        linked that outlived its run.

    Kept: everything a stage cites, and the materials of states within
    `keep_days` of the cutoff. Only the agent's own copies in the
    references folder are ever deleted - never the user's files, which a
    reference names as its original.
    """
    report = {"kept": 0, "deleted": 0, "bytes": 0, "links_dropped": 0}
    if root is None or keep_days <= 0 or not Path(root).exists():
        return report
    root = Path(root).resolve()
    try:
        until = datetime.fromisoformat(cutoff) if cutoff else datetime.now().astimezone()
    except ValueError:
        until = datetime.now().astimezone()
    threshold = until - timedelta(days=keep_days)

    def paths_of(reference: Dict[str, Any]):
        copies = reference.get("copies") or {}
        yield from [v for v in copies.values() if isinstance(v, str)]
        yield from [reference.get("snapshot"), reference.get("screenshot"),
                    *(reference.get("screenshots") or [])]

    cited = set()
    for row in db.query("SELECT work FROM experience_stages"):
        try:
            work = json.loads(row.get("work") or "{}")
        except ValueError:
            continue
        for reference in (work or {}).get("references") or []:
            if isinstance(reference, dict):
                for one in paths_of(reference):
                    if one:
                        cited.add(_resolved(one))
    # The materials of states recent enough to be cited yet - and the links
    # of older ones whose copies no stage cites, which go with them.
    recent = set()
    for row in db.query(
            "SELECT m.state_id, m.event_id, m.copies, s.created_at FROM "
            "state_materials m LEFT JOIN states s ON s.id = m.state_id"):
        try:
            copies = [v for v in json.loads(row.get("copies") or "{}").values()
                      if isinstance(v, str)]
        except ValueError:
            copies = []
        made = _when(row.get("created_at"))
        if made is not None and made >= threshold:
            recent.update(_resolved(one) for one in copies)
        elif not any(_resolved(one) in cited for one in copies):
            db.execute("DELETE FROM state_materials WHERE state_id = ? AND "
                       "event_id = ?", (row["state_id"], row["event_id"]))
            report["links_dropped"] += 1
    keep = cited | recent
    edge = threshold.timestamp()
    for path in sorted(root.rglob("*"), key=lambda one: -len(one.parts)):
        try:
            if path.is_dir():
                if path != root and not any(path.iterdir()):
                    path.rmdir()
                continue
            if path.resolve() in keep or path.stat().st_mtime > edge:
                report["kept"] += 1
                continue
            report["bytes"] += path.stat().st_size
            path.unlink()
            report["deleted"] += 1
        except OSError:            # pragma: no cover - in use, or gone
            continue
    return report


def _resolved(path: str) -> Path:
    try:
        return Path(path).resolve()
    except OSError:
        return Path(path)


def _when(value: Any) -> Optional[datetime]:
    try:
        when = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo else when.astimezone()
