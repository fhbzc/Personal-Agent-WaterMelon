"""EVERY TIME IN THE USER'S OWN (the user, 2026-11-20: "你告诉heads up 的model, 所有的时间都用user时间说话，不要UTC"):
what a model is given, its times turned from UTC into the user's own clock - with the zone's offset kept, so nothing is
lost - and the words it is told to write its own times in."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

#: An ISO time with its zone said ("...Z" or "+00:00"), as the application keeps them.
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})$")

#: Said to a model whose words reach the user.
IN_THEIR_TIME = ("EVERY TIME YOU WRITE IS IN THE USER'S OWN TIME ZONE - the times you are given are in it already "
                 "(\"now\" too); never write UTC, never convert to it.")


def local(value: Any) -> Any:
    """`value` with every zoned ISO time in it put in the user's own zone - dicts and lists gone through."""
    if isinstance(value, dict):
        return {key: local(one) for key, one in value.items()}
    if isinstance(value, list):
        return [local(one) for one in value]
    if isinstance(value, str) and _ISO.match(value):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().isoformat(timespec="minutes")
        except ValueError:
            return value
    return value


def now_local() -> str:
    return datetime.now().astimezone().isoformat(timespec="minutes")


__all__ = ["IN_THEIR_TIME", "local", "now_local"]
