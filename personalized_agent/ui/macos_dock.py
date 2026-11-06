"""THE DOCK'S MENU ON A MAC (the user, 2026-11-05: the watermelon in the Dock, right-clicked, showed
nothing of the agent's): the same as the avatar's menu and the menu bar icon's. Tk makes none, so
it is handed to the application here, and handed again when what it says changes. The system adds
its own Quit under it."""

from __future__ import annotations

import logging
from typing import Any, Callable, List, Tuple

log = logging.getLogger(__name__)

#: What the menu's items call, made once: a class of this name can be defined only once.
_TARGET: List[Any] = []


def _target() -> Any:
    if not _TARGET:
        import AppKit

        class WatermelonDockItems(AppKit.NSObject):
            def pressed_(self, sender: Any) -> None:
                try:
                    self.actions[int(sender.tag())]()
                except Exception:
                    log.exception("the Dock's menu could not do that")

        _TARGET.append(WatermelonDockItems.alloc().init())
    return _TARGET[0]


def show(items: List[Tuple[str, Callable[[], None]]]) -> bool:
    """`items`, (words, what they do), as the Dock's menu. Whether it could be."""
    try:
        import AppKit
        target = _target()
        target.actions = [act for _words, act in items]
        menu = AppKit.NSMenu.alloc().initWithTitle_("WaterMelon")
        menu.setAutoenablesItems_(False)
        for number, (words, _act) in enumerate(items):
            item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                words, b"pressed:", "")
            item.setTarget_(target)
            item.setTag_(number)
            item.setEnabled_(True)
            menu.addItem_(item)
        AppKit.NSApplication.sharedApplication().setDockMenu_(menu)
        return True
    except Exception:
        log.info("no menu of the agent's in the Dock", exc_info=True)
        return False


__all__ = ["show"]
