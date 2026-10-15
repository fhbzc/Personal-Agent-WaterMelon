"""The agent's icon in the notification area, beside the clock.

While the agent runs it sits there, as a chat or sync application's does:
its face - the idle frame of the character the user chose - and a menu.
A click opens the dashboard; the menu opens a chat, shows or hides the
character on screen, pauses monitoring, opens the workspace, and quits.

Optional, like everything that needs a package: without pystray (or on a
desktop with no notification area) the agent runs as before, without it,
and says so once in the log.

THE TRAY RUNS ON ITS OWN THREAD and Tk may only be touched from the thread
that made it - so nothing here touches a window. Every menu item is handed
to the desktop's queue, which its own thread drains.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

try:                                   # pragma: no cover - depends on install
    import pystray
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except Exception:                      # pragma: no cover - degrade quietly
    pystray = None
    Image = ImageDraw = None
    TRAY_AVAILABLE = False

#: The icon's size: drawn larger and scaled down by the system.
ICON_SIZE = 64
#: Room left round the avatar in its icon, as a share of the icon: none -
#: the melon fills its square, as large as other programs' icons.
ICON_MARGIN = 0.0


def fitted(image: Any, size: int = ICON_SIZE) -> Any:
    """The avatar as an icon: cut to what is drawn - a frame is mostly empty
    round the figure, which made the icon smaller than others - and set in
    the middle of a square, `size` across."""
    box = image.getbbox()
    if box:
        image = image.crop(box)
    edge = max(image.width, image.height)
    room = int(round(edge * ICON_MARGIN / (1 - 2 * ICON_MARGIN)))
    square = Image.new("RGBA", (edge + 2 * room, edge + 2 * room), (0, 0, 0, 0))
    square.paste(image, ((square.width - image.width) // 2,
                         (square.height - image.height) // 2), image)
    return square.resize((size, size), Image.LANCZOS)


def icon_image(characters: Any, name: str, size: int = ICON_SIZE) -> Any:
    """The face of the chosen character, for the icon - its first idle
    frame, cut to the figure - or a plain disc in the agent's blue when
    there is none."""
    if Image is None:
        return None
    package = None
    try:
        package = characters.get(name) or characters.get("watermelon")
    except Exception:
        package = None
    # One drawn live is drawn for the icon too, at its size: sharp at any.
    if ((package or {}).get("rig") or {}).get("kind") == "watermelon":
        try:
            from ..agent.character.watermelon import Watermelon
            scale = max(1, min(8, -(-size // 128)))
            return fitted(Watermelon(scale=scale, package=package.get("path")).icon(), size)
        except Exception:
            log.debug("could not draw the avatar for its icon", exc_info=True)
    frames = ((package or {}).get("frames") or {})
    for state in ("idle", *frames):
        for path in frames.get(state) or []:
            try:
                with Image.open(path) as frame:
                    return fitted(frame.convert("RGBA"), size)
            except Exception:
                continue
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((2, 2, size - 2, size - 2), fill=(61, 107, 229, 255))
    return image


def tooltip(app: Any) -> str:
    """The tray icon's words: the avatar's name - and, when it is not the
    standard one, the data folder, so it is plain which agent this is."""
    name = (app.avatar_name() if hasattr(app, "avatar_name") else "") or "Personalized Agent"
    try:
        from ..common.paths import default_root
        root = getattr(app, "root", None)
        if root is not None and Path(root).resolve() != default_root().resolve():
            return f"{name} ({root})"[:127]
    except Exception:          # pragma: no cover - cosmetic
        pass
    return name


class Tray:
    """The notification-area icon of one running desktop agent."""

    def __init__(self, desktop: Any) -> None:
        self.desktop = desktop
        self.config = desktop.config
        self.icon: Optional[Any] = None

    # -- what the menu does: each handed to the window thread ----------------
    def _later(self, work: Callable[[], None]) -> Callable[..., None]:
        def run(*_args: Any) -> None:
            self.desktop.queue.put(("call", work))
        return run

    def _character_shown(self, _item: Any = None) -> bool:
        try:
            return self.desktop.agent_window.state() != "withdrawn"
        except Exception:
            return True

    def _menu(self) -> Any:
        item, menu = pystray.MenuItem, pystray.Menu
        desktop = self.desktop
        # THE SAME FOUR AS THE AVATAR'S MENU: the chat, the Overview (what a
        # click on the icon does), the avatar hidden or shown - by how it is
        # now - and quitting.
        # In the display language, read each time the menu opens.
        say = getattr(desktop, "_words", None) or (lambda text: text)
        return menu(
            item(lambda _item: say("Chat"), self._later(desktop.open_chat)),
            item(lambda _item: say("Overview"), self._later(lambda: desktop.open_url("/#overview")),
                 default=True),
            item(lambda _item: say("Hide avatar" if self._character_shown() else "Show avatar"),
                 self._later(desktop.toggle_character)),
            menu.SEPARATOR,
            item(lambda _item: say("Quit"), self._later(desktop.quit)),
        )

    # -- start and stop -------------------------------------------------------
    def start(self) -> bool:
        """Put the icon in the notification area. False when it cannot be."""
        if not TRAY_AVAILABLE or not self.config.get("tray_icon_enabled", True):
            if not TRAY_AVAILABLE:
                log.info("no notification-area icon: pystray is not installed")
            return False
        try:
            name = tooltip(self.desktop.app)
            self.icon = pystray.Icon(
                "personalized-agent",
                icon_image(self.desktop.app.characters,
                           str(self.config.get("character", "watermelon"))),
                name, menu=self._menu())
            if sys.platform == "darwin":
                # macOS keeps its menu bar on the main thread, which Tk's
                # loop owns: attached to it rather than run beside it.
                self.icon.run_detached()
            else:
                import threading
                threading.Thread(target=self.icon.run, name="tray",
                                 daemon=True).start()
            return True
        except Exception:      # pragma: no cover - no notification area
            log.info("no notification-area icon on this desktop", exc_info=True)
            self.icon = None
            return False

    def refresh(self) -> None:
        """The character changed: the icon wears its face too."""
        if self.icon is None:
            return
        try:
            self.icon.icon = icon_image(self.desktop.app.characters,
                                        str(self.config.get("character", "watermelon")))
            # Its name too, which is the avatar's.
            self.icon.title = tooltip(self.desktop.app)
            self.icon.update_menu()
        except Exception:      # pragma: no cover - cosmetic
            log.debug("could not refresh the tray icon", exc_info=True)

    def stop(self) -> None:
        if self.icon is None:
            return
        try:
            self.icon.stop()
        except Exception:      # pragma: no cover - already gone
            pass
        self.icon = None
