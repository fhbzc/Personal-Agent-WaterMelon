"""TUCKED INTO THE SIDE OF THE SCREEN (the user, 2026-11-04: "放在屏幕旁边的时候，会自动
隐藏进屏幕边框里（只有叶子露在外面），点击可以把它抓出来"; "贴边只会在一段时间后，偷偷摸摸
的躲到旁边，要有偷偷摸摸的动画").

Left beside a side of a screen - left, right, top or bottom - and left alone a
while, the avatar SNEAKS in: it looks round to see that nobody is watching, tiptoes back
into the side a step at a time with its eyes on the room, peeks once, and
slips away, only its leaves left, standing up the side and holding on to it. The pointer coming onto it catches it, and it
comes back. In the side, the pointer on them brings its head out to look,
the leaves still holding on, and away it goes in again. A click on them, or a drag, brings it out; left
there, it goes in again. A side with another screen beyond it is no side to hide in: the rest
of the avatar would show there. A side with the taskbar on it, or a Mac's menu
bar or Dock, counts as one: it would lie over them, not go in.

The places are worked out here; the sliding is ui/desktop.py's, and the body
slid off the leaves `Watermelon.frame`'s (`tuck`).
"""

from __future__ import annotations

import logging
import math
import sys
from typing import Any, NamedTuple, Tuple

log = logging.getLogger(__name__)

#: How near the side it is let go to go in; how much of its width stays out.
WITHIN = 24
PEEK = 34 / 128
#: How much of it is out when it looks round: both eyes, its face being
#: nearer the top of it than the bottom.
HEAD_OUT = {"left": 88 / 128, "right": 88 / 128, "top": 100 / 128, "bottom": 84 / 128}
#: How far from the side it comes back to: still beside it.
BACK_AT = WITHIN - 8
#: How long it is left alone before it sneaks in.
SNEAK_AFTER = 20.0
#: Its steps: when each begins and ends, and how far in it has got by then.
STEPS = ((1.2, 1.7, 0.0, 0.38), (2.0, 2.5, 0.38, 0.72), (3.1, 3.6, 0.72, 1.0))
#: When it slips off its leaves, the last of it.
SLIP = (3.6, 3.95)
#: Where it looks until then: -1 at the room, 1 at the side it is going to.
GLANCES = ((0.4, -1.0), (0.8, 1.0), (1.2, -1.0), (1.7, 1.0), (2.5, -1.0))


#: Clicked in the side, it jumps out (the user, 2026-11-04): how long that
#: takes, and how high it goes, of its size, out of a left or right side.
LEAP_SECONDS = 0.75
LEAP_LIFT = 0.3
#: Out of the top it has the least way to go, with its head out that far, and it does
#: not rise: in the same time it crept (the user, 2026-11-04: "在上边框探头，跳出来的动画特别慢").
LEAP_SECONDS_FROM = {"top": 0.45}
#: The parts of the jump, of 1: gathering itself, then in the air; and how far
#: back into the side it draws, and past where it lands it flies.
LEAP_GATHER, LEAP_AIR = 0.18, 0.60
LEAP_BACK, LEAP_PAST = 0.06, 0.10


#: ITS HEAD COMING OUT, THE LEAVES PUSHED ASIDE (the user, 2026-11-05: "你要拨动那个叶子"): how
#: long they take, swaying past and settling.
PART_SECONDS = 0.7
#: HELD, THEY STRUGGLE (the user, 2026-11-05: "叶子摆动挣扎（被抓包的感觉）"): how far they
#: swing, in degrees, how many times a second, and how far out of step the two halves are.
FLAIL_DEGREES, FLAIL_PER_SECOND, FLAIL_OUT_OF_STEP = 17.0, 3.1, 2.2


def parted(seconds: float, start: float, to: float) -> float:
    """How far apart the leaves are, this long after they began to move from `start` to `to`."""
    into = seconds / PART_SECONDS
    if into >= 1:
        return to
    return start + (to - start) * (1 - math.exp(-5 * into) * math.cos(9 * into))


def flail_at(seconds: float) -> Tuple[float, float]:
    """Each half's swing, in degrees: never quite in time, so that it looks like a struggle."""
    beat = 2 * math.pi * FLAIL_PER_SECOND * seconds
    return (FLAIL_DEGREES * math.sin(beat),
            FLAIL_DEGREES * math.sin(beat * 1.17 + FLAIL_OUT_OF_STEP))


class Leap(NamedTuple):
    """A moment of jumping out: how far along, 0 in the side and 1 landed; how
    high, of the most; and its squash, below 1 flat."""
    out: float
    lift: float
    squash: float


def leap_seconds(side: str) -> float:
    """How long the jump out of that side takes."""
    return LEAP_SECONDS_FROM.get(side, LEAP_SECONDS)


def leap(into: float) -> Leap:
    """Where it is `into` the jump, 0 to 1: it draws back, flat against the
    side, flies out past where it will land, and lands, squashed and settling."""
    if into < LEAP_GATHER:
        part = into / LEAP_GATHER
        return Leap(-LEAP_BACK * math.sin(math.pi / 2 * part), 0.0, 1 - 0.2 * part)
    if into < LEAP_GATHER + LEAP_AIR:
        part = (into - LEAP_GATHER) / LEAP_AIR
        stretch = (0.8 + 0.32 * math.sin(math.pi / 2 * part / 0.3) if part < 0.3
                   else 1.12 - 0.12 * (part - 0.3) / 0.7)
        return Leap(-LEAP_BACK + (1 + LEAP_PAST + LEAP_BACK) * (1 - (1 - part) ** 2),
                    math.sin(math.pi * part), stretch)
    part = (into - LEAP_GATHER - LEAP_AIR) / (1 - LEAP_GATHER - LEAP_AIR)
    return Leap(1 + LEAP_PAST * (1 - part), 0.0, 1 - 0.2 * math.sin(math.pi * part))


class Sides(NamedTuple):
    """The sides of the screen something is on, and whether nothing is beyond
    each."""
    left: int
    right: int
    left_free: bool
    right_free: bool
    top: int = 0
    bottom: int = 0
    top_free: bool = False
    bottom_free: bool = False


class Sneak(NamedTuple):
    """A moment of sneaking in."""
    walk: float                                          # 0 where it was, 1 in the side
    tuck: float
    look: float
    hop: float
    squash: float
    done: bool


def sneak_at(seconds: float) -> Sneak:
    """Where it has got to, so many seconds in."""
    walk, hop = 1.0, 0.0
    for start, end, was, then in STEPS:
        if seconds < start:
            walk = was
            break
        if seconds < end:
            into = (seconds - start) / (end - start)
            walk = was + (then - was) * into
            hop = 2.5 * math.sin(math.pi * into)
            break
    look = next((where for until, where in GLANCES if seconds < until), -1.0)
    tuck = max(0.0, min(1.0, (seconds - SLIP[0]) / (SLIP[1] - SLIP[0])))
    return Sneak(walk, tuck, look, hop, 0.97 if hop else 0.94, seconds >= SLIP[1])


def side_to_tuck(x: int, y: int, width: int, sides: Sides) -> str:
    """"left", "right", "top", "bottom" or "" - where a window let go there
    goes in."""
    if sides.left_free and x <= sides.left + WITHIN:
        return "left"
    if sides.right_free and x + width >= sides.right - WITHIN:
        return "right"
    if sides.top_free and y <= sides.top + WITHIN:
        return "top"
    if sides.bottom_free and y + width >= sides.bottom - WITHIN:
        return "bottom"
    return ""


#: LET GO MOSTLY PAST THE SIDE, IT JUMPS IN AT ONCE (the user, 2026-11-08: "如果西瓜被放置的位置在屏幕外
#: 超过50%，西瓜直接跳进屏幕边框，不是慢慢挪进去"): more than this share of it beyond the side - a quarter
#: (the user, 2026-11-08: "在边框外的比例可以低一点就足够触发").
JUMP_IN_PAST = 0.25
#: How long that jump takes: a short way, quicker than a jump out.
JUMP_IN_SECONDS = 0.5


def off_by(side: str, x: int, y: int, width: int, sides: Sides) -> float:
    """How much of it, 0 to 1, is beyond that side of the screen."""
    beyond = {"left": sides.left - x, "right": x + width - sides.right,
              "top": sides.top - y, "bottom": y + width - sides.bottom}.get(side, 0)
    return max(0.0, min(1.0, beyond / max(1, width)))


def jumps_in(side: str, x: int, y: int, width: int, sides: Sides) -> bool:
    """Whether, let go there, it jumps into that side at once rather than sneaking in later."""
    return bool(side) and off_by(side, x, y, width, sides) > JUMP_IN_PAST


def edge_of(side: str, sides: Sides) -> int:
    """That side of the screen, where it is."""
    return getattr(sides, side)


def _out_by(side: str, out: int, x: int, y: int, width: int, sides: Sides) -> Tuple[int, int]:
    """Where the window is with `out` pixels of it on the screen."""
    if side == "right":
        return sides.right - out, y
    if side == "left":
        return sides.left - width + out, y
    if side == "top":
        return x, sides.top - width + out
    return x, sides.bottom - out


def tucked_at(side: str, x: int, y: int, width: int, sides: Sides) -> Tuple[int, int]:
    """Where the window is, in that side, its leaves out."""
    return _out_by(side, int(round(width * PEEK)), x, y, width, sides)


def peek_at(side: str, x: int, y: int, width: int, sides: Sides) -> Tuple[int, int]:
    """Where the window is with its head out."""
    return _out_by(side, int(round(width * HEAD_OUT[side])), x, y, width, sides)


def back_at(side: str, x: int, y: int, width: int, sides: Sides) -> Tuple[int, int]:
    """Where it comes back to."""
    return _out_by(side, width + BACK_AT, x, y, width, sides)


def seen_at(side: str, x: int, y: int, width: int) -> Tuple[int, int]:
    """A point of it that is out, when it is in that side."""
    peek = int(round(width * PEEK))
    middle = (x + width // 2, y + width // 2)
    if side == "right":
        return x + peek // 2, middle[1]
    if side == "left":
        return x + width - peek // 2, middle[1]
    if side == "top":
        return middle[0], y + width - peek // 2
    return middle[0], y + peek // 2


def sides_of(window: Any, x: int, y: int) -> Sides:
    """The sides of the screen the point is on."""
    try:
        if sys.platform.startswith("win"):
            return _windows(x, y)
        if sys.platform == "darwin":
            return _mac(x, y)
    except Exception:                                    # pragma: no cover - asked of the system
        log.debug("the screen's sides could not be read", exc_info=True)
    return Sides(0, int(window.winfo_screenwidth()), True, True,
                 0, int(window.winfo_screenheight()), True, True)


def _windows(x: int, y: int) -> Sides:
    import ctypes
    from ctypes import wintypes

    class Info(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    user32 = ctypes.windll.user32
    user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    user32.MonitorFromPoint.restype = wintypes.HANDLE
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Info)]
    info = Info()
    info.cbSize = ctypes.sizeof(Info)
    nearest, none = 2, 0
    if not user32.GetMonitorInfoW(user32.MonitorFromPoint(wintypes.POINT(int(x), int(y)), nearest),
                                  ctypes.byref(info)):
        raise OSError("no monitor")
    work = info.rcWork
    left, right, top, bottom = int(work.left), int(work.right), int(work.top), int(work.bottom)

    def free(at_x: int, at_y: int) -> bool:
        """Just past a side of what windows may use: no screen, and no taskbar."""
        return not user32.MonitorFromPoint(wintypes.POINT(at_x, at_y), none)
    return Sides(left, right, free(left - 8, int(y)), free(right + 8, int(y)),
                 top, bottom, free(int(x), top - 8), free(int(x), bottom + 8))


def _mac(x: int, y: int) -> Sides:
    import AppKit
    screens = AppKit.NSScreen.screens()
    height = int(screens[0].frame().size.height)        # the system counts from the bottom

    def box(frame: Any) -> Tuple[int, int, int, int]:
        left, low = int(frame.origin.x), int(frame.origin.y)
        return (left, left + int(frame.size.width), height - low - int(frame.size.height),
                height - low)

    whole = [box(one.frame()) for one in screens]
    here = next((n for n, one in enumerate(whole)
                 if one[0] <= x < one[1] and one[2] <= y < one[3]), 0)
    left, right, top, bottom = box(screens[here].visibleFrame())

    def free(at_x: int, at_y: int) -> bool:
        return not any(one[0] <= at_x < one[1] and one[2] <= at_y < one[3] for one in whole)
    return Sides(left, right, free(left - 8, y), free(right + 8, y),
                 top, bottom, free(x, top - 8), free(x, bottom + 8))


__all__ = ["PEEK", "SNEAK_AFTER", "WITHIN", "Sides", "Sneak", "back_at", "edge_of", "peek_at",
           "seen_at", "side_to_tuck", "sides_of", "sneak_at", "tucked_at"]
