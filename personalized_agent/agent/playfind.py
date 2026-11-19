"""A PICTURE FOUND FOR A THING (the user, 2026-11-19: "程序不一定要看已有截图，而是要去找自己觉得合适的（比如程序自己去找那个论
文）"; "后面有截图的，模型觉得截图不够好也可以去自己搜索"; the sources they allowed: "你打开过的本地文件, 你访问过的网页, agent
自己上网搜").

A thing of the Play world may ask for a picture to be found for it ("find_picture": what it should show). THE MODEL
LOOKS FOR IT ITSELF, ON THE WEB FIRST (the user, 2026-11-19: "模型主要rely on 自己搜，只是用户截图也是搜的一个source"): one
call of the main model with its own web search (play_world_find), their screenshots of those days and the files and
pages they had open given beside it as one source among the others - known to its web guard as theirs, so none of it
goes out in a search. It answers with one of theirs, or an address on the web - read only if its search saw it:

- A FILE OF THEIRS: an image as it is, a PDF its first page - never one in a private folder;
- AN ADDRESS (a page they read, or one the search found): an image as it is, a PDF its first page (a paper's page on
  arXiv its PDF), a web page its own picture ("og:image") - never a private address.

What is found is kept whole in the world's folder (its original, opened from the thing's card) and shown as every
picture is: in pixels on the map, the real one in its card (agent/playworld.py _attach_shot).
"""

from __future__ import annotations

import html
import io
import json
import logging
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

TASK_FIND = "play_world_find"
#: The most read of an address; the widest a picture is kept.
MOST_BYTES, MOST_SIDE = 12_000_000, 8000     # kept whole: its original, opened from the card
#: What a file is read as.
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
AGENT = "Mozilla/5.0 (personal agent; looking at one picture)"

FIND_SYSTEM = """You find ONE picture for a thing in a small pixel world that pictures someone's work - shown when \
they look at it closely. What it is ("what") and what its picture should show ("look_for") are given. SEARCH THE \
WEB FOR IT FIRST - the paper's first page or its key figure, the product's own picture, the place's photograph. \
Their own screenshots of those days ("their_pictures") and the files and pages they had open ("their_sources") are \
ONE SOURCE AMONG THE OTHERS: choose one of theirs only when it shows the thing better than what the web has. What is \
theirs is private: never put any of it in a search - search with the public words of "look_for".
Answer with only JSON: {"theirs": "<the id of one of their_pictures or their_sources>"} - or {"url": "<the direct \
address of the image, or of the paper's PDF, or of the page whose own picture it is>", "what": "<what it shows, a \
few words>"} - or {"url": ""} when nothing fits."""


def _save(image: Any, out: Path) -> bool:
    """A picture kept as PNG, no wider than MOST_SIDE."""
    try:
        image = image.convert("RGB")
        image.thumbnail((MOST_SIDE, MOST_SIDE))
        out.parent.mkdir(parents=True, exist_ok=True)
        image.save(out, "PNG")
        return True
    except Exception:
        log.debug("a found picture could not be kept", exc_info=True)
        return False


def _from_pdf(data: bytes, out: Path) -> bool:
    """A PDF's first page, as a picture."""
    try:
        import pypdfium2 as pdfium
        document = pdfium.PdfDocument(data)
        try:
            page = document[0]
            image = page.render(scale=3.2).to_pil()      # sharp when opened large (2026-11-19: was 1.6)
            page.close()
        finally:
            document.close()
        return _save(image, out)
    except Exception:
        log.debug("a PDF's first page could not be drawn", exc_info=True)
        return False


def _from_bytes(data: bytes, out: Path) -> bool:
    if data[:5] == b"%PDF-":
        return _from_pdf(data, out)
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            return _save(image, out)
    except Exception:
        return False


def from_file(path: Any, out: Path, config: Any = None) -> bool:
    """A FILE THEY OPENED, AS A PICTURE: an image as it is, a PDF its first page - never one in a private folder."""
    source = Path(str(path or ""))
    try:
        from ..common.privacy_folders import is_private
        if not source.is_file() or is_private(source, config):
            return False
        if source.suffix.lower() not in IMAGE_SUFFIXES + (".pdf",) or source.stat().st_size > MOST_BYTES:
            return False
        return _from_bytes(source.read_bytes(), out)
    except Exception:
        log.debug("a file could not be made a picture", exc_info=True)
        return False


def _read(url: str, config: Any = None) -> Optional[tuple]:
    """An address read (http or https, never a private one): (its bytes, its type, the address it ended at)."""
    if not re.match(r"(?i)^https?://", url or ""):
        return None
    from ..common.privacy_folders import address_is_private
    if address_is_private(url, config):
        return None
    try:
        request = urllib.request.Request(url, headers={"User-Agent": AGENT})
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read(MOST_BYTES), str(response.headers.get("Content-Type") or ""), response.geturl()
    except Exception:
        log.debug("an address could not be read for its picture", exc_info=True)
        return None


def from_url(url: str, out: Path, config: Any = None, allowed: Optional[Any] = None) -> bool:
    """A PAGE AS A PICTURE: an image as it is; a PDF (a paper's page on arXiv its PDF) its first page; a web page its
    own picture (og:image, twitter:image), one step on. `allowed(url)`: whether an address may be read (a search's
    guard) - each step asked."""
    url = re.sub(r"(?i)^(https?://(?:www\.)?arxiv\.org)/abs/", r"\1/pdf/", str(url or "").strip())
    for step in range(2):
        # The address given asked; the page's own picture, one step on, as the page itself names it - copied from it
        # word for word, never made up, so nothing of theirs can ride in it (a public address, as every one read).
        if step == 0 and allowed is not None and not allowed(url):
            return False
        got = _read(url, config)
        if got is None:
            return False
        data, kind, where = got
        if "html" not in kind.lower() and not data.lstrip()[:20].lower().startswith((b"<!doctype", b"<html")):
            return _from_bytes(data, out)
        text = data.decode("utf-8", errors="replace")
        found = re.search(r'(?is)<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]*content=["\']([^"\']+)',
                          text) or re.search(r'(?is)<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\']'
                                             r'(?:og:image|twitter:image)["\']', text)
        if not found:
            return False
        url = urllib.parse.urljoin(where, html.unescape(found.group(1)))
    return False


def find(models: Any, what: str, look_for: str, theirs: List[Dict[str, Any]],
         sources: List[Dict[str, Any]]) -> Dict[str, Any]:
    """ONE PICTURE FOUND (play_world_find; the user, 2026-11-19: "模型主要rely on 自己搜，只是用户截图也是搜的一个source"):
    the main model with its own web search, their screenshots and their files and pages among what it may choose -
    those known to the web guard as theirs, so none of it goes out in a search. {"theirs": id} for one of theirs;
    {"url", "what", "allowed"} for the web's ("allowed": whether an address may be read - seen by that search); {}
    when nothing came."""
    from ..common.languages import in_english
    from ..models.answers import read_text
    from ..models.base import ModelRequest
    from ..runtime.web_guard import WebGuard
    look_for = " ".join(str(look_for or "").split())[:200]
    given = {"what": " ".join(str(what or "").split())[:300], "look_for": look_for,
             **({"their_pictures": [{"id": p["id"], "what": p.get("what", ""), "when": p.get("when", "")}
                                    for p in theirs]} if theirs else {}),
             **({"their_sources": [{"id": s["id"], "kind": s["kind"], "title": s["title"]} for s in sources]}
                if sources else {})}
    guard = WebGuard()
    guard.know(json.dumps({k: v for k, v in given.items() if k.startswith("their_")}, ensure_ascii=False), "user")
    try:
        response = models.invoke(ModelRequest(
            system=FIND_SYSTEM, task=TASK_FIND, max_tokens=800, builtin_web=True, web_guard=guard,
            messages=[{"role": "user", "content": json.dumps(given, ensure_ascii=False)}],
            language=in_english('"what" - an address kept exactly as it is')), role="primary")
        text, _cut = read_text(response)
        match = re.search(r"\{.*\}", text or "", re.DOTALL)
        said = json.loads(match.group(0)) if match else {}
    except Exception:
        log.info("a picture could not be found", exc_info=True)
        return {}
    mine = str(said.get("theirs") or "").strip()
    if mine and (any(p["id"] == mine for p in theirs) or any(s["id"] == mine for s in sources)):
        return {"theirs": mine}
    url = str(said.get("url") or "").strip()
    if not url:
        return {}
    return {"url": url, "what": " ".join(str(said.get("what") or "").split())[:160],
            "allowed": lambda address: guard.check_address(address) is None}


__all__ = ["TASK_FIND", "FIND_SYSTEM", "from_file", "from_url", "find"]
