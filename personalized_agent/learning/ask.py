"""One model call of the learning's, asking for JSON - counted under its task
(`learning_*`, the Work learning module) and written in English."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Dict, Optional

log = logging.getLogger(__name__)

#: What each kind of call is counted under (common/modules.py).
TASKS = {
    "folders": "learning_folders",
    "read": "learning_read",
    "reasons": "learning_reasons",
    # The reasons behind the user's answers, guessed and tied across contexts (reasons.py
    # predict / across): Learning from your answers.
    "answers": "learning_answers",
    "terms": "learning_terms",
    "plan": "learning_plan",
    "split": "learning_split",
    "interests": "learning_interests",
}


def as_json(text: str) -> Dict[str, Any]:
    text = str(text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        value = json.loads(text[start:end + 1])
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


#: The provider that stands in when no model is set up - never one to learn with.
OFFLINE = "offline"
OFFLINE_NOTE = ("the model chosen is the offline stand-in, which cannot learn: "
                "choose a model in Settings > Models")


class Asker:
    """Calls a model for the learning. `spent(tokens)` is told what each
    call cost; `stopped()` is asked before each call - a paused update
    makes no more."""

    def __init__(self, models: Any, spent: Optional[Callable[[int], None]] = None,
                 stopped: Optional[Callable[[], bool]] = None) -> None:
        self.models = models
        self.spent = spent
        self.stopped = stopped
        self.calls = 0

    def __call__(self, kind: str, system: str, content: str, max_tokens: int = 4000,
                 what: str = "") -> Dict[str, Any]:
        if self.models is None or (self.stopped is not None and self.stopped()):
            return {}
        from ..models.base import ModelRequest
        from ..common.languages import in_english
        try:
            response = self.models.invoke(
                ModelRequest(messages=[{"role": "user", "content": content}],
                             system=system, max_tokens=max_tokens,
                             # Said where it is read off the code: one of TASKS.
                             task=f"learning_{kind}",
                             language=in_english(what or "everything you write"),
                             metadata={"kind": "learning " + kind}),
                role="heartbeat")
        except Exception as exc:                 # a call that failed: said, not raised
            log.info("a learning call (%s) failed: %s", kind, exc)
            return {"_failed": str(exc)[:300]}
        self.calls += 1
        usage = getattr(response, "usage", None)
        tokens = int(getattr(usage, "input_tokens", 0) or 0) \
            + int(getattr(usage, "cached_input_tokens", 0) or 0) \
            + int(getattr(usage, "cache_written_tokens", 0) or 0) \
            + int(getattr(usage, "output_tokens", 0) or 0)
        if self.spent is not None:
            self.spent(tokens)
        # THE OFFLINE STAND-IN DOES NOT LEARN (2026-11-07): it answers anything, never what was
        # asked, and every part it answered was recorded failed. Not an answer: nothing is
        # marked, and the update stops saying which model to choose (`_model_wrong`).
        if str(getattr(response, "provider", "") or "") == OFFLINE:
            return {"_failed": OFFLINE_NOTE, "_model_wrong": OFFLINE_NOTE}
        # A CUT OR UNREADABLE ANSWER IS A FAILED ONE (2026-11-07): it was read as {} and taken as
        # "nothing in it". Now it says so, as a call that failed does - "_cut" when it stopped at
        # the output limit - and its callers keep nothing of it.
        from ..models import answers
        read = answers.read(response, parse=answers.json_of)
        if read.ok:
            return read.data or {}
        log.info("a learning call (%s) was not answered: %s", kind, read.why_not)
        return {"_failed": read.why_not, **({"_cut": True} if read.cut else {})}
