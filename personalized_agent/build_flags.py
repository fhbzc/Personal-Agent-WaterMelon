"""What was built in (section 14.33, as amended).

A build-time choice, not a setting. The Debugger shows the agent's internals -
every state it has inferred about the user, what each answer was assembled
from, what every module holds - and a build meant for someone who is not
developing the agent should not carry it at all.

There is an existing ``debugger_enabled`` setting, and it is a different
thing: it says whether the Debugger *collects* while it is there. This says
whether it is there. A setting can be turned back on; a module that was never
packaged cannot.

The flag is read, not configured. In a source checkout the Debugger is always
present - that is what a checkout is for. A packaged build decides at the point
of packaging, and ``build.py --no-debugger`` excludes the package, at which
point ``debugger_available()`` finds nothing to import and says so.
"""

from __future__ import annotations

import importlib.util
from functools import lru_cache

#: The module a build drops when the Debugger is left out.
DEBUGGER_MODULE = "personalized_agent.debugger"


@lru_cache(maxsize=1)
def debugger_available() -> bool:
    """Whether this build carries the Debugger at all.

    Asked of the package rather than of a constant, so that the answer cannot
    drift from the truth: a build that excluded the module reports False
    because the module is genuinely not there, and no separate flag has to be
    kept in step with the packaging.
    """
    try:
        return importlib.util.find_spec(DEBUGGER_MODULE) is not None
    except (ImportError, ValueError):       # pragma: no cover - excluded build
        return False


__all__ = ["DEBUGGER_MODULE", "debugger_available"]
