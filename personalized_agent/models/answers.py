"""AN ANSWER CUT OFF IS NOT AN ANSWER (the user, 2026-11-07: "你看看你的整个代码还有没有别的没有考虑
输出被截断的情况"; "完整计划开始").

A model's answer stops where its output limit is, mid-sentence or mid-JSON; read as JSON, it was
read as nothing, and nothing looked the same as "nothing to do": what it was about was marked
done and never asked again. Here an answer is one of three:

  - whole and readable - `Answer.ok`: what it says is taken, and what it was about may move on;
  - cut off - `Answer.cut`: by the model's own word (`stop_reason`), or, since Claude Code and
    Codex nearly always say "end_turn", by JSON that does not close;
  - unreadable - neither: no JSON object in it at all.

A caller takes what an answer says only on `ok`. On anything else it NEVER ASKS THE SAME THING
AGAIN THE SAME WAY (the user, 2026-11-07: "重做要不就是缩短context，要不就别重做，就记为失败"):
what can be made shorter - a batch, a stretch, a list - is asked again in smaller parts
(`in_parts`: the user's choice B, beside the longer limit A, every call at least 20000,
models/manager.py); what is still not answered at its smallest, or cannot be made shorter, is
RECORDED AS FAILED - why, where it can be seen - and moved past, so the next round begins with
new work. What the user had (their groups, their to-do edits, a summary kept from before) is
never overwritten by a failed answer.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

#: What a model says when its answer stopped at the limit (each service has its word).
CUT_OFF = frozenset({"max_tokens", "length", "max_output_tokens", "incomplete"})


def was_cut(response: Any) -> bool:
    """Whether the model said its answer stopped at the output limit."""
    return str(getattr(response, "stop_reason", "") or "").strip().lower() in CUT_OFF


def json_of(text: str) -> Optional[Dict[str, Any]]:
    """The JSON object in an answer - fenced, or from its first { to its last } - or None."""
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start, end = text.find("{"), text.rfind("}")
        candidate = text[start:end + 1] if start != -1 and end > start else None
    if not candidate:
        return None
    try:
        parsed = json.loads(candidate)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _opens_and_does_not_close(text: str) -> bool:
    """JSON begun and not finished: more { or [ opened than closed, outside strings."""
    depth, in_string, escaped = 0, False, False
    begun = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            depth += 1
            begun = True
        elif char in "}]":
            depth -= 1
    return begun and (depth > 0 or in_string)


@dataclass
class Answer:
    """What a model gave back, read: `data` the JSON object or None; `cut` when it stopped at
    the limit; `text` as it came."""
    data: Optional[Dict[str, Any]]
    cut: bool
    text: str
    response: Any = field(default=None, repr=False)

    @property
    def ok(self) -> bool:
        return self.data is not None and not self.cut

    @property
    def why_not(self) -> str:
        """Why it is not taken, for a report: "" when it is."""
        if self.ok:
            return ""
        if self.cut:
            return "the answer was cut off at the output limit"
        return "the answer was empty" if not self.text.strip() else "the answer could not be read"


def read(response: Any, parse: Callable[[str], Any] = json_of,
         wants: Sequence[str] = ()) -> Answer:
    """An answer read: whole only when it parses, says every key in `wants`, and was not cut off
    - by the model's word, or by JSON left open."""
    text = str(getattr(response, "text", "") or "")
    data = parse(text) if text else None
    if data is not None and not isinstance(data, dict):
        data = None
    if data is not None and any(key not in data for key in wants):
        data = None
    cut = was_cut(response) or (data is None and _opens_and_does_not_close(text))
    return Answer(data=data, cut=cut, text=text, response=response)


def read_text(response: Any) -> Tuple[str, bool]:
    """An answer in words: its text, and whether it was cut off."""
    return str(getattr(response, "text", "") or ""), was_cut(response)


def in_parts(items: Sequence[Any], ask: Callable[[Sequence[Any]], Answer],
             least: int = 1) -> List[Tuple[Sequence[Any], Answer]]:
    """A batch asked whole, and - when its answer was cut off - again in halves, and so on, down
    to `least` items: each part with its answer. A part not answered at its smallest is given
    back with that answer, for the caller to leave as it was."""
    answer = ask(items)
    if answer.ok or not answer.cut or len(items) <= max(1, least):
        return [(items, answer)]
    half = len(items) // 2
    return in_parts(items[:half], ask, least) + in_parts(items[half:], ask, least)


__all__ = ["Answer", "CUT_OFF", "was_cut", "json_of", "read", "read_text", "in_parts"]
