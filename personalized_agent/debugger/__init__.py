"""System Debugger backend (section 14).

Registers the running system with the Observability Service and provides the
built-in diagnostic tests.  The Debugger UI reads everything through
``/api/debugger/*``; nothing here renders anything itself.
"""

from .builtin_tests import register_builtin_tests
from .wiring import register_system

__all__ = ["register_builtin_tests", "register_system"]
