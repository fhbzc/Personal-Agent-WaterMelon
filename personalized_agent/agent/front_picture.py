"""A PICTURE OF THE USER'S WINDOW, WHEN THE CHAT OR DELEGATE WORK IS OPENED (the user, 2026-11-04:
"按下chat的时候，或者delegate work自动生成一张当前屏幕的截屏给模型做context (文字也要给,
In case模型读不了图)。 如果屏幕和之前一样就复用", "截屏context在后台，不需要显示").

Taken at once on the press, before the bubble or the box covers anything; what is
slow - reading its words, the check for a password, the file - goes on behind it.
The words go too, for a model that sees no pictures. A screen as it was last time
is the same picture: nothing is read or written again.

THAT WINDOW ALONE, NOT THE SCREEN (the user agreed, 2026-11-04): a window kept from
being read could stand beside the one in front, and the list is asked of that one.
Never with activity monitoring or the pictures switched off (Settings), of a window
excluded from being read, and never one whose words, as far as they can be read
here, hold a password or a code: that one is dropped whole.

Kept in the workspace, among the references, so that a run can read it, and let
go of after the newest `KEEP` are there.
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

#: The longest side given to a model; how many are kept.
LARGEST_SIDE, KEEP = 2000, 20
#: How much two screens may differ and still be one, out of 255 on a small copy:
#: a clock in the taskbar, not a line of text changed.
SAME_BELOW = 0.35
SMALL = (240, 135)
#: How long a message waits for what is behind it, in seconds; past it, none goes.
WAIT_SECONDS = 4.0


def _small(image: Any) -> Any:
    return image.convert("L").resize(SMALL)


def same_screen(one: Any, other: Any) -> bool:
    from PIL import ImageChops, ImageStat
    return ImageStat.Stat(ImageChops.difference(one, other)).mean[0] < SAME_BELOW


def block(path: Any) -> Optional[Dict[str, str]]:
    """A picture as a model is given it - or None, where it cannot be read."""
    try:
        from PIL import Image
        with Image.open(path) as found:
            picture = found.convert("RGB")
        picture.thumbnail((LARGEST_SIDE, LARGEST_SIDE))
        out = io.BytesIO()
        picture.save(out, "JPEG", quality=85)
        return {"media_type": "image/jpeg", "data": base64.b64encode(out.getvalue()).decode("ascii")}
    except Exception:
        log.debug("a picture could not be read for the model", exc_info=True)
        return None


class FrontPictures:
    """The latest picture of the screen."""

    def __init__(self, config: Any, activity: Any, folder: Optional[Path],
                 front: Callable[[], Dict[str, str]],
                 read_words: Optional[Callable[[Any], str]] = None) -> None:
        self.config = config
        self.activity = activity
        self.folder = Path(folder) if folder else None
        self.front = front
        self.read_words = read_words
        self._done = threading.Event()
        self._done.set()
        self._shot: Dict[str, Any] = {}
        #: The last one kept, and its small copy, to tell the same screen by.
        self._kept: Dict[str, Any] = {}
        self._small: Any = None

    def _on(self) -> bool:
        return (self.folder is not None and self.activity is not None
                and bool(self.config.get("listener_enabled", True))
                and bool(self.config.get("listener_screenshots_enabled", True)))

    def _excluded(self, said: Dict[str, str]) -> bool:
        haystack = f"{said.get('application', '')} {said.get('title', '')}".lower()
        return any(one.strip() and one.strip().lower() in haystack
                   for one in self.config.lines("listener_screen_excluded_applications"))

    def take(self) -> None:
        """The press: the screen now. Only the grab is done here - where what a window
        draws can be asked of it, not even that (the user, 2026-11-04: Chat opened with a
        catch): nothing opened after it can be in that, so nothing need wait for it."""
        self._shot = {}
        if not self._on():
            return
        if getattr(self.activity, "draws_windows", False) is True:
            self._done = done = threading.Event()
            threading.Thread(target=self._drawn_and_kept, args=(done,), name="front-picture",
                             daemon=True).start()
            return
        try:
            image = self.activity.user_window_image()
        except Exception:
            log.debug("the screen could not be taken", exc_info=True)
            image = None
        if image is None:
            return
        self._done = done = threading.Event()
        threading.Thread(target=self._keep, args=(image, done), name="front-picture",
                         daemon=True).start()

    def _drawn_and_kept(self, done: threading.Event) -> None:
        try:
            title = str((self.front() or {}).get("title") or "")
            image = self.activity.user_window_image(itself_only=True, title=title)
        except Exception:
            log.debug("the window could not be taken", exc_info=True)
            image = None
        if image is None:
            done.set()
            return
        self._keep(image, done)

    def _keep(self, image: Any, done: threading.Event) -> None:
        try:
            said = self.front() or {}
            if not self._excluded(said):
                self._shot = self._kept_of(image, said)
        except Exception:
            log.info("the screen's picture could not be kept", exc_info=True)
        finally:
            done.set()

    def _words_of(self, image: Any) -> str:
        if self.read_words is not None:
            return str(self.read_words(image) or "")
        from ..runtime import documents
        return str(documents.ocr_picture(image.copy()) or "")

    def _held_back(self, words: str) -> bool:
        if not words or not self.config.get("listener_hide_credentials", True):
            return False
        from ..common.credentials_guard import hide_credentials
        return hide_credentials(words) != words

    def _kept_of(self, image: Any, said: Dict[str, str]) -> Dict[str, Any]:
        small = _small(image)
        if self._small is not None and self._kept and same_screen(small, self._small) \
                and Path(self._kept["path"]).is_file():
            return dict(self._kept, title=said.get("title", ""))
        words = self._words_of(image)
        if self._held_back(words):
            self._small, self._kept = None, {}
            return {}
        picture = image.convert("RGB")
        picture.thumbnail((LARGEST_SIDE, LARGEST_SIDE))
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / (hashlib.sha1(picture.tobytes()).hexdigest()[:20] + ".jpg")
        picture.save(path, "JPEG", quality=85)
        self._let_go_of_old()
        self._small = small
        self._kept = {"path": str(path), "words": words.strip(), "title": said.get("title", "")}
        return dict(self._kept)

    def _let_go_of_old(self) -> None:
        pictures = sorted(self.folder.glob("*.jpg"), key=lambda one: one.stat().st_mtime,
                          reverse=True)
        for one in pictures[KEEP:]:
            try:
                one.unlink()
            except OSError:
                log.debug("an old picture of the screen could not be let go of", exc_info=True)

    def latest(self, title: str = "", wait: float = WAIT_SECONDS) -> Dict[str, Any]:
        """The picture of the last press: {"path", "words", "title"} - {} when
        there is none, it is not ready, or `title` says the user has moved on."""
        if not self._done.wait(wait):
            return {}
        shot = dict(self._shot)
        return shot if shot and (not title or shot.get("title") == title) else {}

    def blocks(self, shot: Dict[str, Any]) -> List[Dict[str, str]]:
        said = block(shot["path"]) if shot.get("path") else None
        return [said] if said else []


__all__ = ["FrontPictures", "block", "same_screen", "KEEP", "WAIT_SECONDS"]
