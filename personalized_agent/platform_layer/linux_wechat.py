"""WeChat's messages one by one on Linux (section 19.2).

Linux gives no window's words to read - nothing like UI Automation or
Accessibility that WeChat answers - so the agent reads no window's text there,
except WeChat's (the user, 2026-10-26): its window's picture, cut to the
conversation's name and its bubbles (`wechat_layout.prepare_wechat_ocr`),
read by RapidOCR on the computer, line by line with each line's box, and
told apart into messages by where the bubbles are, as on a Mac
(`wechat_layout.parse_wechat_frame`). RapidOCR is what reads pictures on Linux
already (`ocr.LinuxOcr`), Chinese and English, added to the application's own
Python when the user adds those languages; without it, nothing is read.

On X only: Wayland gives no window's picture (`linux.front_window_image`).
"""
from __future__ import annotations

import io
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from .window_text import WindowText, is_wechat_application

log = logging.getLogger(__name__)

_engine: Any = None
_engine_lock = threading.Lock()


def rapid_available() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401
        return True
    except ImportError:
        return False


def _rapid() -> Any:
    """One RapidOCR, kept: loading its models takes seconds."""
    global _engine
    with _engine_lock:
        if _engine is None:
            from rapidocr_onnxruntime import RapidOCR
            _engine = RapidOCR()
        return _engine


def read_layout(png: bytes) -> Dict[str, Any]:
    """Every line RapidOCR reads in a picture, with its box from the top
    left in pixels and its confidence 0-100 - the shape Apple Vision's
    `window_ocr.read_layout` gives, for `parse_wechat_frame`."""
    from PIL import Image
    picture = Image.open(io.BytesIO(png))
    width, height = picture.size
    result, _elapsed = _rapid()(png)
    words: List[Dict[str, Any]] = []
    for index, found in enumerate(result or []):
        try:
            corners, text, score = found[0], str(found[1]), float(found[2])
            xs = [float(point[0]) for point in corners]
            ys = [float(point[1]) for point in corners]
        except (TypeError, ValueError, IndexError):
            continue
        if not text.strip():
            continue
        left, top = max(0, int(min(xs))), max(0, int(min(ys)))
        words.append({"text": text.strip(),
                      "bbox": [left, top, max(1, int(max(xs)) - left), max(1, int(max(ys)) - top)],
                      "confidence": round(max(0.0, min(1.0, score)) * 100, 1),
                      "line": (index,)})
    return {"width": width, "height": height, "words": words,
            "text": "\n".join(word["text"] for word in words)}


def wechat_in_front(activity: Any, allowed: Callable[[Any], bool]) -> Optional[WindowText]:
    """WeChat in front, read message by message - None for any other window,
    one the privacy checks turn away, or one that left the front while it
    was being read."""
    front = activity.get_active_window()
    if front is None or not is_wechat_application(front.application or ""):
        return None
    if not allowed(front):
        return None
    if not rapid_available():
        raise RuntimeError("reading WeChat on Linux needs RapidOCR: add Chinese or "
                           "English for reading pictures")
    began = time.perf_counter()
    image = activity.front_window_image()
    if image is None:
        return None
    # Still the same window in front, and still allowed: else thrown away.
    after = activity.get_active_window()
    if after is None or after.pid != front.pid or not allowed(after):
        return None
    captured_at = datetime.now(timezone.utc).isoformat()
    out = io.BytesIO()
    image.convert("RGB").save(out, format="PNG")
    png = out.getvalue()

    def finish() -> Optional[WindowText]:
        """ITS WORDS, LATER (listener/providers/deferred_ocr.py): the picture
        taken above recognised - if WeChat is still allowed to be read."""
        if not allowed(front):
            return None
        from .wechat_layout import parse_wechat_frame, prepare_wechat_ocr_cut, shift_layout
        took = time.perf_counter()
        # Only where the words are is read: the name and the bubbles.
        cut, offset = prepare_wechat_ocr_cut(png)
        layout = shift_layout(read_layout(cut), offset)
        capture = parse_wechat_frame(png, layout)
        return WindowText(application=front.application or "", title=front.title or "",
                          text=layout["text"], how="linux_rapidocr",
                          elements=len(layout["words"]),
                          milliseconds=(time.perf_counter() - took) * 1000,
                          handle=int(front.pid or 0), pid=int(front.pid or 0),
                          capture_scope="visible_text_only", wechat_capture=capture,
                          captured_at=captured_at)
    return WindowText(application=front.application or "", title=front.title or "",
                      text="", how="linux_rapidocr_waiting",
                      milliseconds=(time.perf_counter() - began) * 1000,
                      handle=int(front.pid or 0), pid=int(front.pid or 0),
                      capture_scope="visible_text_only", finish=finish,
                      captured_at=captured_at)
