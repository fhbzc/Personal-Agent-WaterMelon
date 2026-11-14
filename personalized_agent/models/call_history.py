"""EVERY MODEL CALL, KEPT FOR TESTING - IN A BUILD WITH THE DEBUGGER ONLY (the user, 2026-11-11:
"在debugger模式下，可以存历史记录（安装版，或者编译版不debugger不存）方便之后测试").

What each call was given (its system prompt, its messages, the tools it was offered) and what it
answered, with its model, effort, tokens, cost and time - so a call can be asked again later on
another model, or at another effort, and the two answers set side by side (whether a step can run
on a small model). One gzipped JSON file a call, in a folder a day under the data folder's
`model_calls/`.

NEVER IN A BUILD WITHOUT THE DEBUGGER: the installers leave the Debugger out (build_flags.py), and
with it this - nothing is kept. In a build that has it, the Debugger's own switch, "Collect
debugger data" (`debugger_enabled`), turns this off with the rest of what it collects.

KEPT FOR KEEP_DAYS, AND NO MORE THAN MOST_BYTES: the oldest days go first. A call too big to keep
whole (a long piece of work) is kept with its messages cut at MOST_CHARS, and says so.
"""

from __future__ import annotations

import gzip
import json
import logging
import re
import shutil
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

#: How long calls are kept, and how much of them at most.
KEEP_DAYS = 14
MOST_BYTES = 1_500_000_000
#: A call's messages beyond this many characters are cut, and it says so.
MOST_CHARS = 2_000_000
#: The folder under the data folder.
FOLDER = "model_calls"


def _text_of(content: Any) -> Any:
    """A message's content as it can be kept: its text whole, a picture said by name."""
    if isinstance(content, list):
        out: List[Any] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") in ("image", "image_url"):
                out.append({"type": "image", "kept": "not kept"})
            else:
                out.append(part)
        return out
    return content


class CallHistory:
    """The model calls of this build, kept by day (see the module's words)."""

    def __init__(self, data_dir: Any, config: Any = None,
                 available: Optional[Any] = None) -> None:
        self.folder = Path(data_dir) / FOLDER
        self.config = config
        if available is None:
            from ..build_flags import debugger_available
            available = debugger_available
        self._available = available
        self._lock = threading.Lock()
        self._pruned_on = ""

    def on(self) -> bool:
        """Kept only in a build with the Debugger, and while it collects."""
        try:
            if not self._available():
                return False
        except Exception:                      # pragma: no cover - not there, then
            return False
        return self.config is None or bool(self.config.get("debugger_enabled", True))

    def keep(self, request: Any, *, role: str, provider: str, model: str, answered: str,
             response: Any, latency_ms: float, cost: Optional[float],
             module: str = "", by_user: Optional[bool] = None) -> Optional[Path]:
        """One call, written down - never in the way of the call: whatever fails is logged."""
        if not self.on():
            return None
        try:
            now = datetime.now(timezone.utc)
            messages = [dict(one, content=_text_of(one.get("content")))
                        for one in list(getattr(request, "messages", None) or [])
                        if isinstance(one, dict)]
            text = json.dumps(messages, ensure_ascii=False, default=str)
            cut = len(text) > MOST_CHARS
            if cut:
                messages = [{"role": "user", "content": text[:MOST_CHARS]}]
            usage = getattr(response, "usage", None)
            record: Dict[str, Any] = {
                "id": f"call_{uuid.uuid4().hex[:16]}", "at": now.isoformat(),
                "task": str(getattr(request, "task", "") or "general"), "role": role,
                "module": module, "by_user": by_user,
                "provider": provider, "model": model, "answered_by": answered,
                "effort": getattr(request, "reasoning_effort", None),
                "max_tokens": getattr(request, "max_tokens", None),
                "system": getattr(request, "system", None),
                "messages": messages, "cut": cut,
                "tools": [str(getattr(one, "name", one)) for one in
                          list(getattr(request, "tools", None) or [])],
                "answer": str(getattr(response, "content", "") or ""),
                "stop_reason": getattr(response, "stop_reason", None),
                "usage": ({key: value for key, value in vars(usage).items()
                           if isinstance(value, (int, float, str, type(None)))}
                          if usage is not None else {}),
                "cost": cost, "latency_ms": round(float(latency_ms or 0), 1),
            }
            day = self.folder / now.strftime("%Y-%m-%d")
            name = re.sub(r"[^A-Za-z0-9_.-]+", "_", record["task"])[:60]
            target = day / f"{name}.{record['id']}.json.gz"
            with self._lock:
                day.mkdir(parents=True, exist_ok=True)
                with gzip.open(target, "wt", encoding="utf-8") as out:
                    json.dump(record, out, ensure_ascii=False, default=str)
                if self._pruned_on != now.strftime("%Y-%m-%d"):
                    self._pruned_on = now.strftime("%Y-%m-%d")
                    self.prune(now)
            return target
        except Exception:
            log.warning("a model call could not be kept for testing", exc_info=True)
            return None

    def prune(self, now: Optional[datetime] = None) -> List[str]:
        """The days past KEEP_DAYS gone; then, past MOST_BYTES, the oldest days."""
        now = now or datetime.now(timezone.utc)
        gone: List[str] = []
        if not self.folder.exists():
            return gone
        days = sorted(one for one in self.folder.iterdir()
                      if one.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", one.name))
        oldest = (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
        for one in list(days):
            if one.name < oldest:
                shutil.rmtree(one, ignore_errors=True)
                gone.append(one.name)
                days.remove(one)
        sizes = {one: sum(f.stat().st_size for f in one.iterdir() if f.is_file()) for one in days}
        total = sum(sizes.values())
        for one in days[:-1]:                    # never the day being written
            if total <= MOST_BYTES:
                break
            total -= sizes[one]
            shutil.rmtree(one, ignore_errors=True)
            gone.append(one.name)
        if gone:
            log.info("model calls kept for testing: let go of %s", ", ".join(gone))
        return gone


def read(path: Any) -> Dict[str, Any]:
    """One kept call, as written."""
    with gzip.open(Path(path), "rt", encoding="utf-8") as handle:
        return json.load(handle)


__all__ = ["CallHistory", "FOLDER", "KEEP_DAYS", "MOST_BYTES", "MOST_CHARS", "read"]
