"""A picture of the window in front, when its text does not carry it.

Screen reading records what a window SAYS. A slide, a figure, a design, a
chart, a photo - and anything drawn onto a canvas, as Google Slides and an
Overleaf preview are - says little or nothing as text, and what the user saw
there is lost. So when a window holds more than its text, it is
photographed: its own area only, scaled down, kept in the workspace beside
the other copies of references, and deleted after a month unless a stage
of the user's work cites it.

THE RULE IS A SCRIPT, never a model: each check is a few milliseconds of
arithmetic on a small copy of the window, and costs no tokens. A window is
taken when ALL of these hold:

  1. It holds more than its text, by either of:
       - it is a known visual document: a presentation, PDF or image
         application, or a slides, design, image or PDF page; or
       - measured: it is busy (many edges) but little of it came off as
         text, or it is colourful (slides, charts, photos) and its text is
         modest.
  2. It has been in front for a while - glances are not looked at.
  3. It has settled: unchanged since the last check. A picture that changes
     on every check is a video or an animation, and is not taken at all.
  4. It is not what the last picture of the same window showed.
  5. Within the limits: one picture per window every half minute, and a
     cap a day.

And never: with monitoring off, of a window the user has excluded from being
read, of the agent itself, or with nobody at the keyboard.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ...common.events.models import ListenerEvent
from ...platform_layer import get_platform
from ...version import APP_NAME, FORMER_NAMES
from ..core.manager import STOPPING_GRACE_SECONDS
from ..core.provider import ListenerProvider, PluginManifest

log = logging.getLogger(__name__)

# -- what the screen reader got from each window ------------------------------------
_TEXT_LOCK = threading.Lock()
_TEXT_SEEN: Dict[Tuple[str, str], Tuple[int, float]] = {}


def note_text(application: str, title: str, characters: int) -> None:
    """The screen reader says how much readable text a window gave."""
    with _TEXT_LOCK:
        _TEXT_SEEN[(application or "", title or "")] = (characters, time.time())
        if len(_TEXT_SEEN) > 200:
            oldest = min(_TEXT_SEEN, key=lambda key: _TEXT_SEEN[key][1])
            _TEXT_SEEN.pop(oldest, None)


def text_of(application: str, title: str, within: float = 60.0) -> Optional[int]:
    """How much text this window gave lately, or None when not read."""
    with _TEXT_LOCK:
        said = _TEXT_SEEN.get((application or "", title or ""))
    if said is None or time.time() - said[1] > within:
        return None
    return said[0]


# -- known visual documents ---------------------------------------------------------
VISUAL_APPLICATIONS = (
    "powerpnt", "keynote", "impress", "acrord", "acrobat", "sumatrapdf",
    "foxitpdf", "preview", "photos", "mspaint", "paint.net", "figma",
    "illustrator", "photoshop", "inkscape", "gimp", "visio", "draw.io",
    "xnview", "irfanview", "imageglass", "origin", "graphpad", "tableau")
VISUAL_TITLES = re.compile(
    r"google slides|- canva|figma|miro|overleaf|powerpoint|keynote|"
    r"\.(png|jpe?g|gif|svg|webp|tiff?|pdf|pptx?|key|fig|ai|eps)\b|^figure \d+",
    re.I)


def is_visual_document(application: str, title: str) -> bool:
    app = (application or "").lower()
    return (any(name in app for name in VISUAL_APPLICATIONS)
            or bool(VISUAL_TITLES.search(title or "")))


# -- the measures ---------------------------------------------------------------------
def measures(image: Any) -> Dict[str, Any]:
    """How busy and how colourful a picture is, and its fingerprint."""
    from PIL import ImageFilter, ImageStat

    small = image.convert("RGB")
    small.thumbnail((320, 320))
    grey = small.convert("L")
    edges = grey.filter(ImageFilter.FIND_EDGES)
    histogram = edges.histogram()
    total = max(1, sum(histogram))
    busy = sum(histogram[40:]) / total
    # Colourfulness, after Hasler and Suesstrunk: how far the picture's
    # colours spread from grey.
    m_rg, s_rg, m_yb, s_yb = _colour_spread(small)
    colourful = (s_rg ** 2 + s_yb ** 2) ** 0.5 + 0.3 * (m_rg ** 2 + m_yb ** 2) ** 0.5
    # A difference hash: 64 bits that change little when the picture does.
    tiny = grey.resize((9, 8))
    values = list(tiny.getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (values[row * 9 + col] < values[row * 9 + col + 1])
    blank = ImageStat.Stat(grey).stddev[0] < 3
    return {"busy": round(busy, 4), "colourful": round(colourful, 1),
            "fingerprint": bits, "blank": blank}


def _colour_spread(small: Any) -> Tuple[float, float, float, float]:
    """Mean and spread of r - g and of (r + g) / 2 - b over every pixel.

    COUNTED BY COLOUR, NOT BY PIXEL. This ran every four seconds as four
    Python loops over every pixel of the 320-pixel copy. A window has far
    fewer colours than pixels, and PIL counts them in C, so the same sums
    are made once per colour, weighted by how often it occurs. Measured on
    a 320 x 180 copy of a window of text: 27 ms of a core a check before,
    1.2 ms after. A picture in which nearly every pixel is its own colour -
    noise, a photograph - costs about what it did.

    The same numbers: the sums are whole numbers and exact, the means are
    divided exactly as they were, and the spread is the exact variance
    rounded once - where a running float sum over a hundred thousand
    squares rounded at every step - so what differs is the last few bits of
    a float, a millionth of a millionth of the value, far below the one
    decimal the result is rounded to before any threshold sees it.
    """
    width, height = small.size
    n = width * height
    s_rg = q_rg = s_yb = q_yb = 0
    # (r + g) / 2 - b is a whole number of halves, so it is summed doubled -
    # as r + g - 2b - to stay in whole numbers, and halved at the end.
    for count, (r, g, b) in small.getcolors(n):
        rg = r - g
        yb = r + g - 2 * b
        s_rg += count * rg
        q_rg += count * rg * rg
        s_yb += count * yb
        q_yb += count * yb * yb
    m_rg = s_rg / n
    m_yb = (s_yb / 2) / n
    # Sum of squared deviations from the mean, exactly: n*sum(v^2) - sum(v)^2
    # over n^2 - and over 4n^2 for the doubled values.
    v_rg = (n * q_rg - s_rg * s_rg) / (n * n)
    v_yb = (n * q_yb - s_yb * s_yb) / (4 * n * n)
    return m_rg, v_rg ** 0.5, m_yb, v_yb ** 0.5


def apart(one: int, other: int) -> int:
    """How many of the 64 bits two fingerprints differ in."""
    return bin(one ^ other).count("1")


class ScreenshotProvider(ListenerProvider):
    provider_id = "screenshot"
    #: Its own category: turning off the reading of window text does not
    #: silently drop the pictures, which have their own switch.
    category = "screenshot"

    #: How often the window in front is looked at, in seconds.
    EVERY = 4.0
    #: In front for this long before it is looked at - not a glance.
    DWELL = 8.0
    #: Fingerprints this close are the same picture: unchanged, settled.
    SAME = 4
    #: This far from the last picture of the window, it is a new one.
    NEW = 10
    #: Changed on this many checks in a row: video or animation, not taken.
    MOVING = 3
    #: One picture per window at most this often.
    PER_WINDOW = 30.0
    #: Measured thresholds - recorded with every picture, to be tuned.
    BUSY = 0.035          # a busy window: edges in this share of it
    LITTLE_TEXT = 200     # ... with less text than this read from it
    COLOURFUL = 28.0      # a colourful window: slides, charts, photos
    MODEST_TEXT = 1500    # ... with no more text than this
    #: Pictures are kept this wide at most.
    WIDTH = 1280

    def __init__(self, config: Any, platform: Any = None,
                 folder: Optional[Path] = None) -> None:
        super().__init__()
        self.config = config
        self.platform = platform or get_platform()
        self.folder = folder
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._window: Optional[Tuple[str, str]] = None
        self._since = 0.0
        self._last_print: Optional[int] = None
        self._moves = 0
        self._taken: Dict[Tuple[str, str], Tuple[float, int]] = {}
        self._today = ""
        self._count_today = 0
        self.taken = 0
        #: Pictures not kept because their words held a password or a code.
        self.held_back = 0
        # Only the synchronous emit of an image we just saved may request
        # the manager's screenshot-evidence path exception.
        self._emitting_capture: Optional[Path] = None

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        activity = getattr(self.platform, "activity", None)
        if getattr(activity, "front_window_image", None) is None:
            raise RuntimeError("this platform cannot take a picture of a window")
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="listener-screenshot",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=STOPPING_GRACE_SECONDS)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.look()
            except Exception as exc:       # one window is not fatal
                self.record_error(str(exc)[:200])
            self._stop.wait(self.EVERY)

    # -- the rule -------------------------------------------------------------
    def _allowed(self, application: str, title: str, pid: Optional[int]) -> bool:
        if not bool(self.config.get("listener_screenshots_enabled", True)):
            return False
        if not bool(self.config.get("listener_enabled", True)):
            return False
        if pid and pid == os.getpid():
            return False
        said = f"{application} {title}".lower()
        if any(name.lower() in said for name in (APP_NAME, *FORMER_NAMES)):
            return False
        haystack = f"{application} {title}".lower()
        for pattern in self.config.lines("listener_screen_excluded_applications"):
            if pattern.strip() and pattern.strip().lower() in haystack:
                return False
        return self._somebody_is_here()

    def _somebody_is_here(self) -> bool:
        ask = getattr(getattr(self.platform, "activity", None),
                      "seconds_since_input", None)
        try:
            idle = ask() if ask else None
        except Exception:
            idle = None
        if idle is None:
            return True
        try:
            window = float(self.config.get("listener_presence_seconds", 300))
        except (TypeError, ValueError):
            window = 300.0
        return idle <= window

    @classmethod
    def worth_a_picture(cls, application: str, title: str,
                        measured: Dict[str, Any], text: Optional[int]) -> str:
        """Why this window holds more than its text - or "" when it does not."""
        if measured.get("blank"):
            return ""
        if is_visual_document(application, title):
            return "a visual document"
        read = text if text is not None else 0
        if measured["busy"] >= cls.BUSY and read < cls.LITTLE_TEXT:
            return "busy, and little of it is text"
        if measured["colourful"] >= cls.COLOURFUL and read <= cls.MODEST_TEXT:
            return "colourful, and its text is modest"
        return ""

    def look(self) -> Optional[str]:
        """One check of the window in front; the picture's path if taken."""
        activity = self.platform.activity
        front = activity.get_active_window()
        application, title = front.application or "", front.title or ""
        if not title or not self._allowed(application, title, front.pid):
            self._window = None
            return None
        now = time.time()
        key = (application, title)
        if key != self._window:
            self._window, self._since = key, now
            self._last_print, self._moves = None, 0
            return None
        if now - self._since < self.DWELL:
            return None
        image = activity.front_window_image()
        if image is None:
            return None
        measured = measures(image)
        settled = (self._last_print is not None and
                   apart(measured["fingerprint"], self._last_print) <= self.SAME)
        self._moves = 0 if settled else self._moves + 1
        self._last_print = measured["fingerprint"]
        if not settled or self._moves >= self.MOVING:
            return None
        why = self.worth_a_picture(application, title, measured,
                                   text_of(application, title))
        if not why:
            return None
        last = self._taken.get(key)
        if last is not None and (now - last[0] < self.PER_WINDOW or
                                 apart(measured["fingerprint"], last[1]) < self.NEW):
            return None
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self._today:
            self._today, self._count_today = today, 0
        try:
            cap = int(self.config.get("listener_screenshots_per_day", 80))
        except (TypeError, ValueError):
            cap = 80
        if self._count_today >= cap:
            return None
        if self._shows_credentials(image):
            # Not kept, and so never given to a model that sees pictures:
            # its words can be hidden, a picture of them cannot.
            self._taken[key] = (now, measured["fingerprint"])
            self.held_back += 1
            from ..core.filtered import note
            note(self.provider_id, self.category, "screen.shot", "credentials", application)
            return None
        path = self._save(image, application, title, today)
        if path is None:
            return None
        self._taken[key] = (now, measured["fingerprint"])
        self._count_today += 1
        self.taken += 1
        event = ListenerEvent(
            event_type="screen.shot", source=self.provider_id,
            application=application or None,
            data={"application": application, "window_title": title,
                  "path": str(path), "width": image.width, "height": image.height,
                  "why": why, "busy": measured["busy"],
                  "colourful": measured["colourful"],
                  "text_read": text_of(application, title),
                  "fingerprint": f"{measured['fingerprint']:016x}"},
            metadata={"platform": getattr(self.platform, "name", "")})
        self._emitting_capture = path
        try:
            self.emit(event)
        finally:
            self._emitting_capture = None
        # Its words read now, on this computer, and kept as its copy - for
        # whatever reads it next (runtime/documents.py).
        try:
            from ...runtime import documents
            documents.read_later(path)
        except Exception:                          # pragma: no cover - read when asked
            pass
        return str(path)

    def trusted_capture_root(self, event: ListenerEvent) -> Optional[Path]:
        """Vouch for this exact path only while emitting a fresh capture."""
        if (event.event_type == "screen.shot" and event.source == self.provider_id
                and self._emitting_capture is not None
                and str(self._emitting_capture) == (event.data or {}).get("path")):
            self._emitting_capture = None
            return self.folder or self._default_folder()
        return None

    def _shows_credentials(self, image: Any) -> bool:
        """Whether the picture's words, read on this computer, hold a
        password or a code (common/credentials_guard.py) - while that
        protection is on."""
        if not self.config.get("listener_hide_credentials", True):
            return False
        try:
            from ...common.credentials_guard import hide_credentials
            from ...runtime import documents
            words = documents.ocr_picture(image.copy())
        except Exception:                          # pragma: no cover - kept, as before
            return False
        return bool(words) and hide_credentials(words) != words

    def _save(self, image: Any, application: str, title: str, day: str
              ) -> Optional[Path]:
        folder = self.folder or self._default_folder()
        if folder is None:
            return None
        folder = Path(folder) / day
        folder.mkdir(parents=True, exist_ok=True)
        picture = image.convert("RGB")
        if picture.width > self.WIDTH:
            picture = picture.resize(
                (self.WIDTH, round(picture.height * self.WIDTH / picture.width)))
        name = re.sub(r"[^\w一-鿿]+", "-", title.lower()).strip("-")[:50]
        path = folder / f"{datetime.now().strftime('%H%M%S')}-{name or 'window'}.jpg"
        picture.save(path, "JPEG", quality=85)
        return path

    def _default_folder(self) -> Optional[Path]:
        root = self.config.get("workspace_root", "")
        if not root:
            return None
        return Path(root) / "data" / "references" / "screens"

    # -- status ---------------------------------------------------------------
    def get_status(self) -> Dict[str, Any]:
        return {"taken": self.taken, "today": self._count_today,
                "held_back_for_credentials": self.held_back}

    def supported_event_types(self) -> List[str]:
        return ["screen.shot"]

    def get_capabilities(self) -> Dict[str, Any]:
        return {"pictures": True}

    def manifest(self) -> PluginManifest:
        return PluginManifest(
            plugin_id=self.provider_id,
            version="1.0.0",
            supported_platforms=["windows", "macos", "linux"],
            supported_event_types=self.supported_event_types(),
            required_permissions=["screen_recording"],
            configuration_schema={
                "listener_screenshots_enabled": "bool",
                "listener_screenshots_per_day": "int",
                "listener_screen_excluded_applications": "multiline",
            },
            description="A picture of the window in front when it holds more "
                        "than its text: slides, figures, designs.",
        )
