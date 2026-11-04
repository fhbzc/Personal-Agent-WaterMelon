"""FILES AND PICTURES ATTACHED IN CHAT (the user, 2026-11-04: "chat, 要和gpt一样支持上传
附件或者图片").

A file the user attaches is kept in the agent's own folder (`chat_files/<id>/`).
What the model is given of it, with that message:

- ITS WORDS, read here (runtime/documents.py: PDFs, Office files, text; the
  words in a picture by the computer's own OCR) - for every model;
- A PICTURE AS A PICTURE, for a model that sees them; the manager takes it out
  for one that does not (models/vision.py), which has the words.

LATER TURNS KEEP IT AS WELL ("和gpt一样"): the latest pictures of the conversation
go on being sent as pictures, so "what is on the left of it?" can be answered;
of a document they have its name, its id and the start of its words, and the
model reads on when it needs to, or an older picture, with `attached_file_read`.

A DOCUMENT'S PAGES ARE SEEN TOO (runtime/document_pages.py; the user, 2026-11-04):
a deck's every page, of a PDF or a Word file those with a picture or a chart,
drawn when it is first sent, with that message only - a page after that is
asked for (`attached_file_read`, or the file @ed again).

READ AS SOON AS IT COMES (the user, 2026-11-04: "上传就在后台转"): its words and
pages are got ready in the background while the message is written, and a
message waits only for what is left. A file taken off stops it.

A picture that shows a password or a code is not given to a model, here as for
any file read (runtime/documents.py): its words are, with those hidden.

@ A FILE OF THE CONVERSATION (the user, 2026-11-04: "@我当前发送的某个图片/pdf名字，
代表对这个resource的reference"): a file named so in a message is given with it,
words and picture, as if attached again - and only a file of that conversation.

GONE WITH ITS CONVERSATION (the user, 2026-11-04): a conversation deleted takes
its files with it, and what was read of them; so what is attached and never
sent goes, a day on. A screenshot holds other people's names and words.
"""

from __future__ import annotations

import base64
import io
import logging
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..common.events.models import new_id
from ..models.base import ToolSpec

log = logging.getLogger(__name__)

#: The largest file; the most in one message.
MOST_BYTES = 25 * 1024 * 1024
MOST_FILES = 10
#: What is given of one file's words, of all of a message's, and later on.
MOST_WORDS = 30000
MOST_ALL_WORDS = 60000
MOST_LATER = 3000
#: The pictures of a conversation sent again each turn, the latest; the older
#: ones are read when asked for.
MOST_PICTURES_LATER = 8
#: A picture larger than this is made smaller before it is sent.
MOST_PICTURE_BYTES = 4 * 1024 * 1024
LARGEST_SIDE = 2000
#: What the model is told when only files were sent.
ALONE = "Please look at what I attached."
#: The most pictures with one message: its pictures and pages together.
MOST_PAGES_IN_A_MESSAGE = 20
#: How long a message waits for a file still being got ready.
READY_SECONDS = 150
#: How long a file attached and not sent is kept.
UNSENT_SECONDS = 24 * 3600
#: Said instead of a picture held back.
HELD_BACK = "(the picture is not shown: it shows a password or a code)"
MEDIA = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
         ".webp": "image/webp"}


def _clean_name(name: Any) -> str:
    said = Path(str(name or "").replace("\\", "/")).name.strip()
    said = "".join(one for one in said if one not in '<>:"|?*' and ord(one) >= 32)
    return said[:120] or "file"


def _suffix(data: bytes) -> str:
    if data[:2] == b"\xff\xd8":
        return ".jpg"
    if data[:4] == b"GIF8":
        return ".gif"
    if data[:4] == b"RIFF":
        return ".webp"
    return ".png"


class ChatFiles:
    """The files attached in Chat."""

    def __init__(self, folder: Any, prepares: bool = False) -> None:
        self.folder = Path(folder)
        #: Got ready in the background from when each comes (`prepare`).
        self.prepares = prepares
        self._pool = ThreadPoolExecutor(2, thread_name_prefix="chat-file")
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._jobs_lock = threading.Lock()

    def _path(self, ident: str) -> Path:
        ident = str(ident or "")
        if not ident.startswith("chatfile_") or not ident.replace("_", "").isalnum():
            raise KeyError("no such file")
        here = self.folder / ident
        found = [one for one in here.iterdir() if one.is_file()] if here.is_dir() else []
        if not found:
            raise KeyError("no such file")
        return found[0]

    def save(self, name: Any, data: bytes) -> Dict[str, Any]:
        """A file kept - or ValueError, saying why not."""
        if not data:
            raise ValueError("the file is empty")
        if len(data) > MOST_BYTES:
            raise ValueError(f"a file is at most {MOST_BYTES // (1024 * 1024)} MB")
        ident = new_id("chatfile")
        path = self.folder / ident / _clean_name(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if self.prepares:
            self.prepare(ident)
        return self.described(ident)

    # -- got ready in the background -------------------------------------------------------
    def prepare(self, ident: str) -> None:
        """Its words read and its pages drawn, on a thread of the pool's."""
        job: Dict[str, Any] = {"stop": threading.Event(), "sha": ""}
        with self._jobs_lock:
            self._jobs[ident] = job
        job["done"] = self._pool.submit(self._get_ready, ident, job)

    def _get_ready(self, ident: str, job: Dict[str, Any]) -> None:
        from ..runtime import documents
        stop = job["stop"]
        try:
            if stop.is_set():
                return
            job["sha"] = documents.sha256_of(self._path(ident))
            words = self.words(ident)
            if not stop.is_set():
                self._pages(ident, words, stop)
        except KeyError:
            pass                                         # taken off already
        except Exception:
            log.info("%s could not be got ready", ident, exc_info=True)
        finally:
            with self._jobs_lock:
                self._jobs.pop(ident, None)
            if stop.is_set():                            # what it made after it was taken off
                if job["sha"]:
                    documents.forget_copy_of(job["sha"])
                shutil.rmtree(self.folder / ident, ignore_errors=True)

    def _ready(self, ident: str) -> None:
        """Waited for, if it is still being got ready."""
        with self._jobs_lock:
            job = self._jobs.get(ident)
        if job is not None and job.get("done") is not None:
            try:
                job["done"].result(timeout=READY_SECONDS)
            except Exception:
                log.info("%s was not ready in time", ident, exc_info=True)

    def stop(self) -> None:
        """The application is closing: nothing more is begun."""
        with self._jobs_lock:
            for job in self._jobs.values():
                job["stop"].set()
        self._pool.shutdown(wait=False, cancel_futures=True)

    def add_paths(self, paths: Sequence[Any]) -> Dict[str, Any]:
        """Files on this computer, kept here: {"files", "skipped"}."""
        files: List[Dict[str, Any]] = []
        skipped: List[str] = []
        for one in paths:
            path = Path(one)
            try:
                if not path.is_file():
                    raise ValueError("not a file")
                if path.stat().st_size > MOST_BYTES:
                    raise ValueError("too large")
                files.append(self.save(path.name, path.read_bytes()))
            except (OSError, ValueError):
                skipped.append(path.name)
        return {"files": files, "skipped": skipped}

    def from_clipboard(self, with_text: bool = False,
                       read: Optional[Callable[..., List[Any]]] = None) -> Dict[str, Any]:
        """What the user has just pasted, kept here - however the program it
        was copied from put it there (platform_layer/clipboard.py)."""
        from ..platform_layer import clipboard
        found = (read or clipboard.files_on_it)(with_text)
        said = self.add_paths([one for one in found if not isinstance(one, bytes)])
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        for one in (item for item in found if isinstance(item, bytes)):
            try:
                data = clipboard.as_picture(one)
                said["files"].append(self.save(f"screenshot-{stamp}{_suffix(data)}", data))
            except (OSError, ValueError):                # what Pillow cannot read is one too
                log.info("a picture on the clipboard could not be read", exc_info=True)
                said["skipped"].append("picture")
        return said

    def described(self, ident: str) -> Dict[str, Any]:
        path = self._path(ident)
        return {"id": ident, "name": path.name, "size": path.stat().st_size,
                "picture": path.suffix.lower() in MEDIA}

    def file(self, ident: str) -> tuple:
        """(its bytes, its media type) - for the page."""
        path = self._path(ident)
        return path.read_bytes(), MEDIA.get(path.suffix.lower(), "application/octet-stream")

    def words(self, ident: str) -> str:
        """What can be read of it."""
        from ..runtime import documents
        path = self._path(ident)
        try:
            if documents.needs_reading(path):
                return str(documents.extract(path).get("text") or "")
            return path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            log.info("%s could not be read", path.name, exc_info=True)
            return ""

    def forget(self, ids: Sequence[str]) -> None:
        """Files let go: each, and what was read of it."""
        from ..runtime import documents
        for ident in ids:
            with self._jobs_lock:
                job = self._jobs.get(str(ident))
            if job is not None:
                job["stop"].set()
            try:
                path = self._path(str(ident))
            except KeyError:
                continue
            try:
                documents.forget_copy(path)
            except OSError:
                log.info("what was read of %s could not be let go", ident, exc_info=True)
            shutil.rmtree(path.parent, ignore_errors=True)

    def forget_unsent(self, sent: Sequence[str]) -> None:
        """What was attached and never sent, a day on."""
        if not self.folder.is_dir():
            return
        kept = set(sent)
        before = time.time() - UNSENT_SECONDS
        self.forget([one.name for one in self.folder.iterdir()
                     if one.name not in kept and one.stat().st_mtime < before])

    def thumbnail(self, ident: str, side: int) -> Any:
        """A small picture of it, for the bubble."""
        from PIL import Image
        with Image.open(self._path(ident)) as image:
            small = image.convert("RGBA")
            small.thumbnail((side, side))
        return small

    def _shown(self, ident: str) -> bool:
        """Whether a picture may be given as a picture."""
        from ..runtime import documents
        try:
            return not documents.held_back(self._path(ident))
        except Exception:
            log.info("%s could not be looked at: not given", ident, exc_info=True)
            return False

    def _picture(self, ident: str) -> Dict[str, str]:
        path = self._path(ident)
        data, media = path.read_bytes(), MEDIA[path.suffix.lower()]
        if len(data) > MOST_PICTURE_BYTES:
            from PIL import Image
            with Image.open(path) as image:
                image = image.convert("RGB")
                image.thumbnail((LARGEST_SIDE, LARGEST_SIDE))
                out = io.BytesIO()
                image.save(out, "JPEG", quality=85)
            data, media = out.getvalue(), "image/jpeg"
        return {"media_type": media, "data": base64.b64encode(data).decode("ascii")}

    def _pages(self, ident: str, words: str, stop: Any = None) -> Dict[str, Any]:
        """A document's pages, drawn: {"pages", "of", "by"} - none for a file
        with none, or whose words hold a password or a code."""
        from ..runtime import document_pages, documents
        path = self._path(ident)
        if not document_pages.draws(path) or documents.shows_credentials(words):
            return {"pages": [], "of": 0, "by": ""}
        return document_pages.draw_pages(path, path.parent / "pages", stop)

    def for_the_model(self, ids: Sequence[str]) -> Dict[str, Any]:
        """What a message's files add to it: "text", to go after the user's
        words; "pictures"; and "files", kept with the message."""
        if len(ids) > MOST_FILES:
            raise ValueError(f"at most {MOST_FILES} files in one message")
        parts: List[str] = []
        pictures: List[Dict[str, str]] = []
        files: List[Dict[str, Any]] = []
        left = MOST_ALL_WORDS
        for ident in ids:
            self._ready(str(ident))
            try:
                about = self.described(str(ident))
            except KeyError:
                raise ValueError("a file attached is no longer there")
            files.append(about)
            words = self.words(about["id"]).strip()
            shown = words[:min(MOST_WORDS, max(0, left))]
            left -= len(shown)
            shows = about["picture"] and self._shown(about["id"])
            if shows:
                pictures.append(self._picture(about["id"]))
            head = f'--- attached: {about["name"]} (id {about["id"]}) ---'
            if about["picture"] and not shows:
                head += f"\n{HELD_BACK}"
            head += self._pages_given(about["id"], words, pictures)
            if shown:
                note = " (the words read in this picture)" if about["picture"] else ""
                cut = "" if len(shown) == len(words) else (
                    f"\n[... cut here at {len(shown)} of {len(words)} characters: "
                    "attached_file_read reads on]")
                parts.append(f"{head}{note}\n{shown}{cut}")
            else:
                parts.append(f"{head}\n" + ("(a picture, with no words read in it)"
                                            if about["picture"] else "(nothing could be read in it)"))
        return {"text": "\n\n".join(parts), "pictures": pictures, "files": files}

    def with_mentions(self, attached: Optional[Dict[str, Any]], attachments: Sequence[str],
                      mentions: Sequence[str], known: Sequence[str]) -> Optional[Dict[str, Any]]:
        """What a message's files add to it, with the files it @s: those of
        its conversation, or attached to it. "files" stays what was attached."""
        allowed = set(known) | set(attachments)
        named = [one for one in dict.fromkeys(str(one) for one in mentions) if one in allowed]
        if not named:
            return attached
        said = dict(attached or {"text": "", "pictures": [], "files": []})
        room = MOST_FILES - len(attachments)
        more = self.for_the_model([one for one in named if one not in attachments][:max(0, room)])
        names = ", ".join(f'@{self.described(one)["name"]}' for one in named)
        said["text"] = "\n\n".join(part for part in (
            said["text"], more["text"],
            f"In the message, {names} refers to the file of that name given here.") if part)
        said["pictures"] = [*said["pictures"], *more["pictures"]]
        return said

    def _pages_given(self, ident: str, words: str, pictures: List[Dict[str, str]]) -> str:
        """Its pages added to the message's pictures, as many as there is room
        for; what to say of them beside the file."""
        from ..runtime import document_pages, documents
        drawn = self._pages(ident, words)
        room = max(0, MOST_PAGES_IN_A_MESSAGE - len(pictures))
        given = []
        for page in drawn["pages"][:room]:
            picture = documents.picture_for_model(page)
            if picture is not None:
                pictures.append(picture)
                given.append(_page_number(page))
        if given:
            return (f"\n(pages {', '.join(given)} of {drawn['of']} are shown as pictures with this "
                    "message, in that order)")
        layout = "" if drawn["by"] else document_pages.slide_layout(self._path(ident))
        return f"\nHOW THE SLIDES ARE LAID OUT:\n{layout}" if layout else ""

    def later(self, messages: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Earlier messages, each with its files said beside it - name, id and
        the start of their words - and, under "pictures", the latest pictures
        of the conversation as pictures."""
        out: List[Dict[str, Any]] = []
        room = MOST_PICTURES_LATER
        for message in reversed(list(messages)):
            files = (message.get("metadata") or {}).get("attachments") or []
            if not files:
                out.append(message)
                continue
            parts = [str(message.get("content") or "")]
            pictures: List[Dict[str, str]] = []
            for one in reversed(files):
                ident = str(one.get("id"))
                try:
                    words = self.words(ident).strip()
                    if one.get("picture") and room > 0 and self._shown(ident):
                        pictures.insert(0, self._picture(ident))
                        room -= 1
                except (KeyError, OSError, ValueError):
                    parts.append(f'[attached: {one.get("name")} - no longer there]')
                    continue
                more = f"\n{words[:MOST_LATER]}" if words else ""
                if len(words) > MOST_LATER:
                    more += "\n[... attached_file_read reads on]"
                seen = self._kept_page_numbers(ident)
                if seen:
                    more += f"\n[pages {', '.join(seen)} can be looked at: attached_file_read, page]"
                parts.append(f'[attached: {one.get("name")}, id {ident}]{more}')
            out.append(dict(message, content="\n\n".join(part for part in parts if part),
                            pictures=pictures))
        out.reverse()
        return out

    def _read_page(self, ident: str, about: Dict[str, Any], page: int) -> Dict[str, Any]:
        from ..runtime import documents
        drawn = self._pages(ident, self.words(ident))
        wanted = next((one for one in drawn["pages"] if _page_number(one) == str(page)), None)
        if wanted is None:
            numbers = [_page_number(one) for one in drawn["pages"]]
            return {"name": about["name"], "page": page,
                    "error": f"that page is not drawn; these are: {numbers}" if numbers
                    else "this file's pages cannot be drawn: read its words"}
        picture = documents.picture_for_model(wanted)
        return {"name": about["name"], "page": page, "of": drawn["of"],
                **({"_pictures": [picture]} if picture else {})}

    def _kept_page_numbers(self, ident: str) -> List[str]:
        from ..runtime import document_pages
        kept = document_pages.kept_pages(self._path(ident).parent / "pages")
        return [_page_number(one) for one in kept["pages"]]

    def read(self, ident: str, start: int = 0, page: int = 0) -> Dict[str, Any]:
        """A file attached, for the model: its words from `start` on, and a
        picture as a picture - or one of its pages, drawn."""
        about = self.described(ident)
        if page:
            return self._read_page(ident, about, int(page))
        words = self.words(ident)
        start = max(0, int(start or 0))
        shown = words[start:start + MOST_WORDS]
        out: Dict[str, Any] = {"name": about["name"], "text": shown, "from": start,
                               "to": start + len(shown), "of": len(words)}
        if about["picture"]:
            if self._shown(ident):
                out["_pictures"] = [self._picture(ident)]
            else:
                out["picture_held_back"] = HELD_BACK
        return out


def _page_number(path: Any) -> str:
    return str(int(Path(str(path)).stem.split("-")[-1]))


def add_tools(registry: Any, files: ChatFiles) -> None:
    def read(args: Dict[str, Any]) -> Any:
        try:
            return files.read(str(args.get("id") or ""), args.get("start") or 0,
                              args.get("page") or 0)
        except (KeyError, OSError):
            return {"error": "no such file: it is the id said beside the file in the conversation"}

    registry.add(ToolSpec(
        "attached_file_read",
        "Read a file the user attached in this chat: the rest of a long document (its words "
        "from `start` on, 30000 characters at a time), look at one page of a PDF, a deck or a "
        "Word file as a picture (`page`), or look again at a picture no longer shown. The id "
        "is said beside the file in the conversation.",
        {"type": "object", "properties": {
            "id": {"type": "string", "description": "the file's id (chatfile_...)"},
            "start": {"type": "integer", "description": "the character to read from; 0 is the start"},
            "page": {"type": "integer", "description": "a page to see as a picture, from 1"}},
         "required": ["id"]}), read)


__all__ = ["ChatFiles", "ALONE", "MOST_FILES", "MOST_BYTES", "add_tools"]
