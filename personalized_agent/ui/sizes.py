"""SIZES BY THE SCREEN (the user, 2026-11-05: "这个所有的size不应该用绝对size"; a classmate's chat box
was very small and could not be sized).

A window of 380 pixels is a quarter of one screen and a tenth of another. The sizes written in the
windows are as they look on `REFERENCE`, and each is taken by `px` to the screen it is on; what the
user sized a window to is kept as its share of the screen. A smaller screen keeps them as they are:
below that, words stop being readable.
"""

from __future__ import annotations

from typing import Any, List, Optional, Sequence, Tuple

#: The screen the written sizes look right on, as a window sees it (2560 x 1440 at 175%).
REFERENCE = (1463, 823)
#: The most they grow.
MOST = 2.5


#: ONE ZOOM FOR EVERY WINDOW OF THE AGENT'S (the user, 2026-11-05: "不要每个panel独立，而是和Muse一样的
#: 缩放逻辑"), in steps from where they start: as far as the chat's steps go either way.
ZOOM_LEAST, ZOOM_MOST = -7, 7
#: What each step makes of their words, and the step they start at.
ZOOMS = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.25, 1.4, 1.6, 1.8, 2.0, 2.25)
ZOOM_START = 7
#: The smallest words any of them is zoomed to, in points (the user, 2026-11-06: smaller than it was).
SMALLEST_WORDS = 5
#: Where the chat's own steps started, when each window kept its own zoom.
_CHAT_STARTED_AT = 2


def within_zoom(step: Any) -> int:
    return max(ZOOM_LEAST, min(ZOOM_MOST, int(step)))


def zoomed(points: float, step: Any) -> int:
    """WORDS AT A STEP, BY SHARE AND NOT BY POINTS TAKEN OFF (the user, 2026-11-06: a notification
    would go no smaller): text set large on a large screen is 26 points, and five steps took 5 off."""
    return max(SMALLEST_WORDS, int(round(points * ZOOMS[ZOOM_START + within_zoom(step)])))


def popup_zoom(config: Any) -> int:
    """The step they are at. Where none was kept yet, the chat's own, as it was left."""
    try:
        said = config.get("popup_zoom", None)
        if said is None:
            said = int(config.get("chat_bubble_zoom", _CHAT_STARTED_AT)) - _CHAT_STARTED_AT
        return within_zoom(said)
    except (AttributeError, TypeError, ValueError):
        return 0


def scale(widget: Any) -> float:
    """How much larger than on `REFERENCE` things are here."""
    try:
        wide, high = int(widget.winfo_screenwidth()), int(widget.winfo_screenheight())
    except Exception:
        return 1.0
    if wide <= 0 or high <= 0:
        return 1.0
    return max(1.0, min(MOST, wide / REFERENCE[0], high / REFERENCE[1]))


def px(widget: Any, value: float) -> int:
    return int(round(value * scale(widget)))


def share(widget: Any, size: Sequence[int]) -> List[float]:
    """A size as what is kept: its share of the screen."""
    return [round(int(size[0]) / max(1, int(widget.winfo_screenwidth())), 4),
            round(int(size[1]) / max(1, int(widget.winfo_screenheight())), 4)]


def kept(widget: Any, said: Any) -> Optional[Tuple[int, int]]:
    """What was kept, in pixels on this screen. Pixels themselves are what older versions kept."""
    try:
        first, second = float(said[0]), float(said[1])
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    if first <= 0 or second <= 0:
        return None
    if first <= 1 and second <= 1:
        return (int(round(first * widget.winfo_screenwidth())),
                int(round(second * widget.winfo_screenheight())))
    return int(first), int(second)


__all__ = ["REFERENCE", "MOST", "ZOOM_LEAST", "ZOOM_MOST", "SMALLEST_WORDS", "ZOOMS", "ZOOM_START", "within_zoom", "zoomed", "popup_zoom", "scale",
           "px", "share", "kept"]
