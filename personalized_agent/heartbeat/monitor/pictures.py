"""A NOTIFICATION'S PICTURE (the user, 2026-11-04: "notification里允许加图片").

The model names a picture on the public web, or says "page" for the one a page
shows of itself when shared. It is fetched once, when the notification is made:
held to the same checks as every address the model opens, never from this
computer or its network, and drawn again here as a plain PNG, so what the bubble
and the page show is a picture and nothing else. A picture that cannot be had
costs the notification nothing: it is said without one.
"""

from __future__ import annotations

import hashlib
import io
import ipaddress
import logging
import socket
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Optional, Tuple

from ..connections.avatars import download, page_picture

log = logging.getLogger(__name__)

#: "image": "page" - the picture the notification's own page shows of itself.
OF_THE_PAGE = "page"
#: The longest side kept, in pixels.
MOST_SIDE = 800
#: Smaller than this on either side is a tracking pixel or an icon.
LEAST_SIDE = 48
#: As long as the notifications themselves are kept.
KEEP_DAYS = 30


def is_public(url: str) -> bool:
    """Whether the address is out on the internet, not here or on this network."""
    host = urllib.parse.urlsplit(str(url or "")).hostname
    if not host:
        return False
    try:
        found = socket.getaddrinfo(host, None)
    except OSError:
        return False
    return bool(found) and all(
        ipaddress.ip_address(str(one[4][0]).split("%")[0]).is_global for one in found)


def _let_go_of_old(folder: Path) -> None:
    oldest = time.time() - KEEP_DAYS * 86400
    for one in folder.glob("*.png"):
        try:
            if one.stat().st_mtime < oldest:
                one.unlink()
        except OSError:
            log.debug("an old picture could not be let go of", exc_info=True)


def _kept(folder: Path, data: bytes) -> str:
    from PIL import Image
    with Image.open(io.BytesIO(data)) as found:
        picture = found.convert("RGBA")
    if min(picture.size) < LEAST_SIDE:
        return ""
    picture.thumbnail((MOST_SIDE, MOST_SIDE))
    folder.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha1(data).hexdigest()[:24] + ".png"
    picture.save(folder / name, "PNG")
    _let_go_of_old(folder)
    return name


def keep(folder: Optional[Path], image: str, page: str, refused: Callable[[str], Any],
         get: Callable[[str], Tuple[bytes, str]] = download,
         public: Callable[[str], bool] = is_public) -> str:
    """The name of the picture kept, or "" - never an error: `refused` says why
    an address may not be opened, as the model's own web tools are told."""
    if folder is None or not image:
        return ""

    def allowed(address: str) -> bool:
        return not refused(address) and public(address)
    try:
        address = image
        if image == OF_THE_PAGE:
            if not page or not allowed(page):
                return ""
            data, kind = get(page)
            if not kind.startswith("text/html"):
                return ""
            address = page_picture(data.decode("utf-8", "replace"), page) or ""
        if not address or not allowed(address):
            return ""
        return _kept(Path(folder), get(address)[0])
    except Exception:
        log.info("a notification's picture could not be had", exc_info=True)
        return ""


__all__ = ["OF_THE_PAGE", "is_public", "keep"]
