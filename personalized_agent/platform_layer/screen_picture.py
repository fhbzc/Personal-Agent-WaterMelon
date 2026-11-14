"""A PICTURE OF THE WHOLE SCREEN, ON EVERY SYSTEM (the user, 2026-11-08: "能给整个屏幕吗"; "能不能越过chat
窗口截屏"; "mac不要涂灰，我要的效果就是不带chat的截图"; "linux也要做"): what each system's own way of
taking it shares - what shows of a window never read painted over, where the system can only take
the screen as it is (Windows, for those; Linux) - and, for the Mac, which windows go into the picture.
The Mac's leaves a window out altogether: this application's, and those never read."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

#: What a window never read is painted over with on a picture of the screen.
BLANKED = (128, 128, 128)

#: THE MOUSE POINTER, DRAWN ON THE PICTURE (the user, 2026-11-13: "你现在chat打开的那个截屏，你要能截取我的鼠标位置"):
#: a picture of the screen holds no pointer - it is drawn where it was, an arrow as the system draws
#: one, its size for a screen this tall.
POINTER_SHAPE = ((0, 0), (0, 17), (4, 13), (7, 20), (10, 19), (7, 12), (12, 12))
POINTER_FOR_HEIGHT = 1080


def draw_pointer(image: Any, screen: Any, pointer: Optional[Tuple[int, int]]) -> Any:
    """`image` of `screen` (left, top, right, bottom) with the mouse pointer drawn where `pointer` is -
    and kept with it (`info["pointer"]`: where, out of its width and height); as it was when the pointer
    is off it."""
    if image is None or pointer is None or screen is None:
        return image
    x, y = pointer[0] - screen[0], pointer[1] - screen[1]
    if not (0 <= x < image.width and 0 <= y < image.height):
        return image
    try:
        from PIL import ImageDraw
        size = max(1.0, image.height / POINTER_FOR_HEIGHT) * 1.4
        shape = [(x + px * size, y + py * size) for px, py in POINTER_SHAPE]
        draw = ImageDraw.Draw(image)
        draw.polygon(shape, fill=(255, 255, 255), outline=(0, 0, 0))
        draw.line(shape + [shape[0]], fill=(0, 0, 0), width=max(1, int(size)))
        image.info["pointer"] = (x, y, image.width, image.height)
    except Exception:
        pass                                     # the picture as it is, then
    return image


def blank_windows(image: Any, screen: tuple, windows: List[Dict[str, Any]],
                  hidden: Optional[Any] = None) -> Any:
    """`image` of `screen` (left, top, right, bottom), with what shows of each window that is this
    application's or that `hidden(application, title)` keeps from being read painted over.
    `windows` from the top down. What is under a window that hides it is that window's; under one
    that does not (a layered or see-through one), still the one beneath - painted over as that
    one is: too much grey, never a window that is not to be read."""
    import numpy

    left, top = screen[0], screen[1]
    width, height = image.size
    owner = numpy.full((height, width), -1, dtype=numpy.int32)
    blank = set()
    for index in range(len(windows) - 1, -1, -1):         # from the bottom up
        one = windows[index]
        never = bool(one.get("ours")) or bool(
            hidden and hidden(str(one.get("application") or ""), str(one.get("title") or "")))
        if not (never or one.get("opaque", True)):
            continue                                       # it hides nothing under it
        x0, y0 = max(0, one["box"][0] - left), max(0, one["box"][1] - top)
        x1, y1 = min(width, one["box"][2] - left), min(height, one["box"][3] - top)
        if x1 <= x0 or y1 <= y0:
            continue
        owner[y0:y1, x0:x1] = index
        if never:
            blank.add(index)
    if not blank:
        return image
    out = numpy.array(image.convert("RGB"))
    out[numpy.isin(owner, list(blank))] = BLANKED
    from PIL import Image
    return Image.fromarray(out)


def mac_windows(infos: Any, pid: int) -> List[Dict[str, Any]]:
    """The windows the Mac says are on the screen (CGWindowListCopyWindowInfo, from the front),
    as the picture needs them: its number, its frame (left, top, right, bottom, in points), its
    program and title, whether it is this application's."""
    out = []
    for one in infos or []:
        try:
            bounds = one.get("kCGWindowBounds") or {}
            x, y = float(bounds.get("X", 0)), float(bounds.get("Y", 0))
            w, h = float(bounds.get("Width", 0)), float(bounds.get("Height", 0))
            out.append({"id": int(one.get("kCGWindowNumber")),
                        "box": (int(x), int(y), int(x + w), int(y + h)),
                        "application": str(one.get("kCGWindowOwnerName") or ""),
                        "title": str(one.get("kCGWindowName") or ""),
                        "layer": int(one.get("kCGWindowLayer") or 0),
                        "ours": int(one.get("kCGWindowOwnerPID") or 0) == int(pid)})
        except (TypeError, ValueError):
            continue
    return out


def kept_in_picture(windows: List[Dict[str, Any]], hidden: Optional[Any] = None) -> List[int]:
    """The windows a Mac's picture is made of, from the front: every one but this application's
    and those never read - left out, not painted over: what is behind them shows."""
    return [one["id"] for one in windows if not one.get("ours") and not (
        hidden and hidden(str(one.get("application") or ""), str(one.get("title") or "")))]


def display_of(box: Optional[Tuple[int, int, int, int]], displays: List[Tuple[int, int, int, int]]
               ) -> Optional[Tuple[int, int, int, int]]:
    """The display a window is on - the one holding its middle - else the first."""
    if not displays:
        return None
    if box:
        middle = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
        for one in displays:
            if one[0] <= middle[0] < one[2] and one[1] <= middle[1] < one[3]:
                return one
    return displays[0]


def the_window_meant(windows: List[Dict[str, Any]], title: str) -> Optional[Dict[str, Any]]:
    """Of the user's windows on the screen, from the front: the one of that title, else the front."""
    users = [one for one in windows if not one.get("ours") and int(one.get("layer") or 0) == 0
             and one["box"][2] - one["box"][0] >= 120 and one["box"][3] - one["box"][1] >= 90]
    return next((one for one in users if title and one.get("title") == title),
                users[0] if users else None)
