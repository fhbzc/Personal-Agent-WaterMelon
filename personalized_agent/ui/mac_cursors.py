"""THE MAC'S OWN POINTERS (the user, 2026-11-21: "你在mac上，那个手型鼠标为什么这么奇怪"): the windows here ask Tk for X's
pointer names - "hand2" over what is pressed, "fleur" over what is dragged - which Tk on a Mac has no system pointer for
and draws from X's old black and white bitmaps. On a Mac every such name is asked for as the system's own pointer
instead - the pointing hand, the open hand - whichever window, however it is set (made, or configured later). Nowhere
else is anything changed."""

from __future__ import annotations

import sys
import tkinter as tk
from typing import Any, Dict, Optional, Tuple

#: X's names asked for here, and the Mac's own pointer for each.
ON_A_MAC: Dict[str, str] = {"hand2": "pointinghand", "hand1": "pointinghand", "fleur": "openhand"}


def mac_pointer(name: Any) -> Any:
    """The Mac's own pointer for an X pointer name; anything else as it is."""
    return ON_A_MAC.get(name, name) if isinstance(name, str) else name


def use_the_macs_pointers(platform: Optional[str] = None) -> bool:
    """On a Mac, every window's "cursor" option given as the Mac's own pointer from now on (Tk's option list,
    tkinter.Misc._options, through which every widget is made and configured). Once; whether it is in use."""
    if (platform or sys.platform) != "darwin":
        return False
    if getattr(tk.Misc, "_mac_pointers", False):
        return True
    original = tk.Misc._options

    def options(self: Any, cnf: Any, kw: Any = None) -> Tuple[Any, ...]:
        said = list(original(self, cnf, kw))
        for n in range(0, len(said) - 1, 2):
            if said[n] == "-cursor":
                said[n + 1] = mac_pointer(said[n + 1])
        return tuple(said)

    tk.Misc._options = options
    tk.Misc._mac_pointers = True
    return True


__all__ = ["ON_A_MAC", "mac_pointer", "use_the_macs_pointers"]
