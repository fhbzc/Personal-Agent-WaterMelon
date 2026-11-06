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
ZOOM_LEAST, ZOOM_MOST = -2, 7


def within_zoom(step: Any) -> int:
    return max(ZOOM_LEAST, min(ZOOM_MOST, int(step)))


def popup_zoom(config: Any) -> int:
    """The step they are at. Where none was kept yet, the chat's own, as it was left."""
    try:
        said = config.get("popup_zoom", None)
        if said is None:
            said = int(config.get("chat_bubble_zoom", -ZOOM_LEAST)) + ZOOM_LEAST
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


__all__ = ["REFERENCE", "MOST", "ZOOM_LEAST", "ZOOM_MOST", "within_zoom", "popup_zoom", "scale",
           "px", "share", "kept"]
