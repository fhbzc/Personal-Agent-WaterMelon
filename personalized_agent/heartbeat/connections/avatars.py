"""A contact's picture, from a public profile of theirs - or none, and the
page draws their initials.

Where it comes from, in order: a GitHub account's own picture
(github.com/<name>.png); then the picture a public profile page shows of
itself when shared (its og:image or twitter:image). Only public pages,
fetched with no cookie and no sign-in; a page that needs one is skipped.
At most `MOST_BYTES`, and only an image.
"""

from __future__ import annotations

import html
import logging
import re
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

from .store import ConnectionsStore, url_key

log = logging.getLogger(__name__)

MOST_BYTES = 2 * 1024 * 1024
TIMEOUT = 15.0
AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PersonalAgentWaterMelon/1.0"
TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg",
         "image/gif": "gif", "image/webp": "webp"}
#: Tried again for one who had none at most this often (seconds).
RETRY_AFTER = 24 * 3600


def download(url: str) -> Tuple[bytes, str]:
    """(the bytes, their content type) of a public address - bounded."""
    request = urllib.request.Request(url, headers={"User-Agent": AGENT,
                                                   "Accept": "*/*"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as answer:  # noqa: S310 - http(s) only
        kind = str(answer.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        data = answer.read(MOST_BYTES + 1)
    if len(data) > MOST_BYTES:
        raise ValueError("too large")
    return data, kind


def _public(url: str) -> bool:
    parts = urllib.parse.urlparse(str(url or ""))
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def candidates(accounts: List[Dict[str, Any]]) -> List[Tuple[str, str]]:
    """(how, address) to try, best first: a GitHub picture, then each
    public profile page (whose own picture is read from it)."""
    out: List[Tuple[str, str]] = []
    for one in accounts:
        handle = str(one.get("handle") or "").strip().lstrip("@")
        url = str(one.get("url") or "")
        key = url_key(url)
        if key.startswith("github.com/"):
            handle = key.split("/")[1] if len(key.split("/")) > 1 else handle
            if handle:
                out.append(("image", f"https://github.com/{handle}.png?size=200"))
        elif str(one.get("platform") or "").lower() == "github" and \
                re.fullmatch(r"[A-Za-z0-9-]{1,39}", handle):
            out.append(("image", f"https://github.com/{handle}.png?size=200"))
    for one in accounts:
        url = str(one.get("url") or "")
        if _public(url) and not url_key(url).startswith("github.com/"):
            out.append(("page", url))
    return out


_META = re.compile(r"<meta\b[^>]*>", re.I)


def page_picture(text: str, base: str) -> Optional[str]:
    """The picture a page says it has: og:image, then twitter:image."""
    found: Dict[str, str] = {}
    for tag in _META.findall(text or ""):
        name = re.search(r'(?:property|name)\s*=\s*["\']([^"\']+)', tag, re.I)
        value = re.search(r'content\s*=\s*["\']([^"\']+)', tag, re.I)
        if name and value:
            found.setdefault(name.group(1).lower(), html.unescape(value.group(1)))
    for key in ("og:image:secure_url", "og:image", "twitter:image", "twitter:image:src"):
        if found.get(key):
            url = urllib.parse.urljoin(base, found[key])
            return url if _public(url) else None
    return None


#: A path that names a full-size copy, beside one cut down or cropped.
_FULL_WORDS = ("hires", "original", "orig", "full", "large", "master", "raw")
_SMALL_WORDS = ("tmb", "thumb", "thumbnail", "small", "preview")
_ATTRS = re.compile(r'([\w:-]+)\s*=\s*["\']([^"\']+)["\']')
_SIZE_SUFFIX = re.compile(r"-\d{2,4}x\d{2,4}(?=\.\w{3,4}(?:$|\?))")


def fuller_picture(text: str, picture: str, base: str) -> Optional[str]:
    """THE SAME PICTURE, WHOLE. A page shows a picture cut to fit - "800a",
    "-800x530", a thumbnail - and names the whole one beside it: in the
    srcset, in the gallery that opens it larger (data-src, an <a> around
    it), or under another path with the same file name ("hires"). That
    one; None when the page names none."""
    if not text or not picture:
        return None
    path = urllib.parse.urlsplit(picture).path
    name = path.rsplit("/", 1)[-1]
    if not name or "." not in name:
        return None
    stem = _SIZE_SUFFIX.sub("", name)

    def score(url: str) -> int:
        """How whole a copy looks: a full-size path, no size cut into its name."""
        lowered = urllib.parse.urlsplit(url).path.lower()
        tail = lowered.rsplit("/", 1)[-1]
        return (2 if any(f"/{word}" in lowered for word in _FULL_WORDS) else 0) \
            + (1 if tail == stem.lower() else 0)

    found: List[Tuple[int, str]] = []
    for raw in re.findall(r"""["'\s,]((?:https?:)?//[^"'\s,]+|/[^"'\s,]+)""", text):
        url = urllib.parse.urljoin(base, html.unescape(raw))
        tail = urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]
        if url == picture or _SIZE_SUFFIX.sub("", tail) != stem:
            continue
        lowered = urllib.parse.urlsplit(url).path.lower()
        if any(f"/{word}/" in lowered for word in _SMALL_WORDS):
            continue
        found.append((score(url), url))
    # The srcset of the picture itself: its widest, which is as whole as it gets.
    for tag in re.findall(r"<img\b[^>]*>", text, re.I):
        attrs = {key.lower(): value for key, value in _ATTRS.findall(tag)}
        if urllib.parse.urljoin(base, html.unescape(attrs.get("src", ""))) != picture:
            continue
        widest, url = 0, ""
        for part in html.unescape(attrs.get("srcset", "")).split(","):
            bits = part.split()
            if len(bits) == 2 and bits[1].endswith("w") and bits[1][:-1].isdigit() \
                    and int(bits[1][:-1]) > widest:
                widest, url = int(bits[1][:-1]), urllib.parse.urljoin(base, bits[0])
        if url and url != picture:
            found.append((9, url))
    # Only a copy that looks whole-r than the one chosen.
    own = score(picture)
    found = [(rank, url) for rank, url in found if _public(url) and rank > own]
    if not found:
        return None
    return max(found, key=lambda one: one[0])[1]


def fetch_avatar(store: ConnectionsStore, person_id: str,
                 get: Callable[[str], Tuple[bytes, str]] = download) -> Dict[str, Any]:
    """Look for a person's picture and keep the first one found. What came
    of it: {"ok", "from"} or {"ok": False, "error"}."""
    person = store.get(person_id)
    if person is None:
        return {"ok": False, "error": "no such person"}
    if store.folder is None:
        return {"ok": False, "error": "no folder for pictures"}
    store.avatar_tried(person_id)
    tried = []
    for how, url in candidates(store.accounts(person_id)):
        try:
            if how == "page":
                data, kind = get(url)
                if not kind.startswith("text/html"):
                    continue
                picture = page_picture(data.decode("utf-8", "replace"), url)
                if not picture:
                    tried.append(f"{url}: no picture on the page")
                    continue
                url = picture
            data, kind = get(url)
            ext = TYPES.get(kind)
            if ext is None or not data:
                tried.append(f"{url}: not an image")
                continue
            store.set_avatar(person_id, data, ext, url)
            return {"ok": True, "from": url}
        except Exception as exc:          # one source failing is not the end
            tried.append(f"{url}: {type(exc).__name__}")
            log.debug("could not get a picture from %s", url, exc_info=True)
    return {"ok": False, "error": "no public picture found" + (
        f" ({'; '.join(tried[:3])})" if tried else " - no public profile with one")}


def wanting(store: ConnectionsStore, most: int = 10) -> List[str]:
    """The people with an account or profile and no picture, not tried in
    the last day - the round looks for theirs."""
    from .round import _days_ago
    out = []
    for person in store.people(include_self=True):
        if person.get("avatar") and store.avatar_path(person):
            continue
        tried = person.get("avatar_tried_at")
        if tried and (_days_ago(tried) or 0) * 86400 < RETRY_AFTER:
            continue
        if candidates(store.accounts(person["id"])):
            out.append(person["id"])
        if len(out) >= most:
            break
    return out
