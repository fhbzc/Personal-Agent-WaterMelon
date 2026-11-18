"""The live avatar's frames, drawn once and kept.

THE AVATAR WAS DRAWN AFRESH SIX TIMES A SECOND DOING NOTHING (measured 2026-11-16: the window's thread alone
used a fifth of a core, one frame 12.6 ms - the user: "不拖慢电脑整体的速度"). Its breathing changes every
frame, so no frame was ever the one before. Here the breathing goes round in BREATH_STEPS poses, and what a
frame is drawn from is rounded finely enough that it does not show - so a still avatar is drawn for one
breath, then only shown; and a frame the same as the one on the screen is not shown again.

Only the frames that repeat are kept: not talking or thinking (their mouths and bubbles move with time), not
tucked, leaning, holding on, turning or flailing.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple

#: One breath, and the poses it goes round in - one each 50 ms (the user, 2026-11-16: "那个动作不是很smooth":
#: at 20, a pose each 160 ms against frames each 160 ms, the same pose came twice and then one was skipped).
#: At 64, a frame each 160 ms goes three or four on, evenly; a still avatar's breath is some 33 frames, kept.
BREATH_SECONDS = 3.2
BREATH_STEPS = 64
BREATH_DEPTH = 0.015
#: Frames kept - a breath of each way of looking it rests in, and more.
KEPT = 160

#: What moves with time, or is never the same twice: drawn every time.
UNKEPT_MOODS = frozenset({"talking", "thinking"})


def breathing(t: float) -> float:
    """The squash of the breath at `t` seconds, on one of BREATH_STEPS poses."""
    step = int(round((t % BREATH_SECONDS) / BREATH_SECONDS * BREATH_STEPS)) % BREATH_STEPS
    return 1 + BREATH_DEPTH * math.sin(2 * math.pi * step / BREATH_STEPS)


def rounded(look: Tuple[float, float], blink: float, squash: float, hop: float,
            smile: Any) -> Tuple[Tuple[float, float], float, float, float, Any]:
    """What a frame is drawn from, rounded past what shows: the eyes by a fiftieth of their way across,
    a blink and a smile by a twentieth, a hop by half a unit."""
    return ((round(look[0] * 50) / 50, round(look[1] * 50) / 50), round(blink * 20) / 20,
            round(squash, 4), round(hop * 2) / 2, None if smile is None else round(float(smile) * 20) / 20)


class FrameCache:
    """Frames by what they were drawn from; the oldest let go past KEPT."""

    def __init__(self, kept: int = KEPT) -> None:
        self.kept = kept
        self._frames: "OrderedDict[tuple, Any]" = OrderedDict()
        self.drawn = 0
        self.reused = 0

    @staticmethod
    def key(mood: str, look: Tuple[float, float], blink: float, squash: float, hop: float, size: int,
            facing: str, smile: Any, more: Dict[str, Any], quick: bool) -> Optional[tuple]:
        """The frame's key - None for one that is not kept."""
        if mood in UNKEPT_MOODS or more:
            return None
        return (mood, look, blink, squash, hop, size, facing, smile, quick)

    def get(self, key: Optional[tuple]) -> Any:
        if key is None:
            return None
        frame = self._frames.get(key)
        if frame is not None:
            self._frames.move_to_end(key)
            self.reused += 1
        return frame

    def put(self, key: Optional[tuple], frame: Any) -> None:
        self.drawn += 1
        if key is None:
            return
        self._frames[key] = frame
        self._frames.move_to_end(key)
        while len(self._frames) > self.kept:
            self._frames.popitem(last=False)

    def clear(self) -> None:
        self._frames.clear()

    def __len__(self) -> int:
        return len(self._frames)
