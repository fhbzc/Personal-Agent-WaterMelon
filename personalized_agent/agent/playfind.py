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
import threading
import time
import urllib.error
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
#: How many choices a find gives, tried in turn.
CHOICES_MOST = 4
# WHO ASKS, AND WHERE TO READ ABOUT IT: Wikimedia and others answer a program that names itself and its page, and
# refuse a nameless one sooner (the user saw 429s from upload.wikimedia.org, 2026-11-20).
AGENT = "PersonalAgentWaterMelon/1.0 (a personal agent looking at one picture; +https://github.com/fhbzc/Personal-Agent-WaterMelon)"
#: SITES THAT REFUSE EVERY PROGRAM (403 / 999 to any reader that is not a person's browser): never asked - a failure
#: each time, and asking again and again is what they block (the user, 2026-11-20).
REFUSING = ("linkedin.com", "facebook.com", "instagram.com", "x.com", "twitter.com", "tiktok.com", "threads.net",
            "xiaohongshu.com", "zhihu.com", "weibo.com")
#: A SITE ASKED AT MOST THIS OFTEN (seconds) - several pictures from one wiki no longer at once.
SPACING = 2.0
#: A SITE THAT SAID "TOO MANY" (429) or refused (403, 999) is left alone this long (seconds), or as long as it asked
#: (Retry-After).
HELD_TOO_MANY, HELD_REFUSED = 15 * 60, 6 * 3600
_asked_at: Dict[str, float] = {}
_held_until: Dict[str, float] = {}
_hosts_lock = threading.Lock()


def _host(url: str) -> str:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _refusing(host: str) -> bool:
    return any(host == one or host.endswith("." + one) for one in REFUSING)


def _ascii_address(url: str) -> str:
    """AN ADDRESS AS IT CAN BE SENT: a name in any language as IDNA, and the path and query percent-encoded - what is
    already encoded kept (a Chinese Wikipedia file name broke the read: "'ascii' codec can't encode", 2026-11-20)."""
    parts = urllib.parse.urlsplit(url)
    try:
        host = parts.hostname.encode("idna").decode("ascii") if parts.hostname else ""
    except UnicodeError:
        host = parts.hostname or ""
    netloc = host + (f":{parts.port}" if parts.port else "")
    if parts.username:
        netloc = parts.username + (f":{parts.password}" if parts.password else "") + "@" + netloc
    keep = "/:@!$&'()*+,;=%~-._"
    return urllib.parse.urlunsplit((parts.scheme, netloc, urllib.parse.quote(parts.path, safe=keep),
                                    urllib.parse.quote(parts.query, safe=keep + "?"),
                                    urllib.parse.quote(parts.fragment, safe=keep + "?")))


def _wait_turn(host: str) -> bool:
    """False when the site is being left alone; else waits its turn (SPACING) and takes it."""
    with _hosts_lock:
        now = time.time()
        if _held_until.get(host, 0) > now:
            return False
        wait = _asked_at.get(host, 0) + SPACING - now
        _asked_at[host] = max(now, _asked_at.get(host, 0) + SPACING)
    if wait > 0:
        time.sleep(wait)
    return True


def _hold(host: str, error: Exception) -> None:
    code = getattr(error, "code", None)
    if code not in (403, 429, 999):
        return
    seconds = HELD_TOO_MANY if code == 429 else HELD_REFUSED
    try:
        asked = int(str((getattr(error, "headers", None) or {}).get("Retry-After") or "0"))
        if asked > 0:
            seconds = min(max(asked, 60), 24 * 3600)
    except (TypeError, ValueError):
        pass
    with _hosts_lock:
        _held_until[host] = time.time() + seconds

FIND_SYSTEM = """You find the picture for a thing in a small pixel world that pictures someone's work - shown when \
they look at it closely. What the thing is ("what"), the card of their work it stands for ("card": its title, what is \
true when it is done, its status) and what its picture should show ("look_for") are given.
THE THING ITSELF, NEVER ONE LIKE IT (the user, 2026-11-20: "这个材料包的照片明显不是我的"; "明显可以用我的照片"): \
when it is THEIR OWN WORK - their paper, their code, their release, their draft, their data, their notes - its \
picture is theirs: one of their files ("their_files": found on their computer now by the card's words; a PDF shown \
at a page, a folder as its tree, a text as its first lines), their screenshots ("their_pictures") or the files and \
pages they had open ("their_sources"). When it is A PUBLIC THING - a journal's cover, a product, a place, an event, a \
published paper's first page - SEARCH THE WEB, with several searches and in more than one way (the publisher's own \
page, an encyclopedia or Wikimedia Commons, an image search's results, the paper's PDF): an address you saw in \
your searches. What is theirs is private: never put any of it in a search.
GIVE UP TO 4 CHOICES, THE BEST FIRST, from different places - each is tried in turn till one can be read (a site \
that refuses is passed over). Answer with only JSON: {"choices": [{"theirs": "<the id of one of their_files, \
their_pictures or their_sources>"} or {"url": "<the direct address of the image, of the paper's PDF, or of the page \
whose own picture it is>", "what": "<what it shows, a few words>"}, ...]} - {"choices": []} when nothing fits."""


TASK_FIND_LOOK = "play_world_find_look"
#: THE PICTURE LOOKED AT BEFORE IT IS HUNG (the user, 2026-11-20: "不是你抓到图之后不读一下是否符合场景吗？" - a page's own
#: picture, the user's avatar, was hung for "the agent's queued reminder bubbles"): shown the picture, the model says
#: whether it is what was asked for.
FIND_LOOK_SYSTEM = """You are given a picture found for a thing in a small world that pictures someone's work, and \
what the picture was meant to show ("look_for", "what", "card"). LOOK AT IT: is it really that - the thing itself, \
or near enough that one seeing it in the thing's card would say "yes, that is it"? A logo, a person's photo or \
avatar, a page's banner or an unrelated image is not, unless that is what was asked for.
Answer with only JSON: {"fits": true or false, "why": "<one sentence>"}"""


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


#: A text file drawn as its first lines (from_file).
TEXT_SUFFIXES = (".md", ".txt", ".py", ".r", ".do", ".tex", ".csv", ".json", ".yaml", ".yml", ".toml", ".js", ".ts",
                 ".ipynb", ".sql", ".sh", ".rst", ".html", ".bib")
#: The most lines of a text, and of a folder's tree, drawn.
TEXT_LINES_MOST, TREE_LINES_MOST = 40, 36
#: Where a long line is wrapped.
WRAP_AT = 96
#: Folders no one made by hand, left out of a tree.
TREE_SKIPPED = {"__pycache__", "node_modules", ".git", "venv", ".venv", "build", "dist", ".ipynb_checkpoints"}


def from_file(path: Any, out: Path, config: Any = None) -> bool:
    """A FILE OF THEIRS AS A PICTURE (the user, 2026-11-20: "你应该让他支持找文件，从文件里截图之类"): an image as it is;
    a PDF its first page; A FOLDER as its tree (a replication package's folders and files); A TEXT - code, notes, a
    README, a table - as its first lines, drawn as a page. Never one in a private folder."""
    source = Path(str(path or ""))
    try:
        from ..common.privacy_folders import is_private
        if is_private(source, config):
            return False
        if source.is_dir():
            return _draw_lines(_tree_of(source, config), out, title=source.name + "/")
        if not source.is_file() or source.stat().st_size > MOST_BYTES:
            return False
        suffix = source.suffix.lower()
        if suffix in IMAGE_SUFFIXES + (".pdf",):
            return _from_bytes(source.read_bytes(), out)
        if suffix in TEXT_SUFFIXES:
            text = source.read_text(encoding="utf-8", errors="replace")
            if suffix == ".ipynb":
                try:
                    cells = json.loads(text).get("cells") or []
                    text = "\n\n".join("".join(c.get("source") or []) for c in cells)
                except ValueError:
                    pass
            return _draw_lines(text.splitlines()[:TEXT_LINES_MOST], out, title=source.name)
        return False
    except Exception:
        log.debug("a file could not be made a picture", exc_info=True)
        return False


def _tree_of(folder: Path, config: Any = None) -> List[str]:
    """A folder's tree, two levels deep, folders first - what is private or hidden left out."""
    from ..common.privacy_folders import is_private
    lines: List[str] = []

    def walk(at: Path, depth: int) -> None:
        try:
            children = sorted((c for c in at.iterdir() if not c.name.startswith(".") and c.name not in TREE_SKIPPED
                               and not is_private(c, config)), key=lambda c: (not c.is_dir(), c.name.lower()))
        except OSError:
            return
        for child in children:
            if len(lines) >= TREE_LINES_MOST:
                return
            lines.append("    " * depth + (child.name + "/" if child.is_dir() else "- " + child.name))
            if child.is_dir() and depth < 1:
                walk(child, depth + 1)

    walk(folder, 0)
    return lines


def _draw_lines(lines: List[str], out: Path, title: str = "") -> bool:
    """Lines drawn as a page - a title bar, then each line wrapped to the page - in a fixed-width face, or one with
    Chinese in it when the lines have any; sharp enough to open large."""
    try:
        import textwrap
        from PIL import Image, ImageDraw, ImageFont
        wrapped: List[str] = []
        for one in [str(x).replace("	", "    ") for x in lines] or [""]:
            wrapped += textwrap.wrap(one, WRAP_AT, replace_whitespace=False, drop_whitespace=False) or [""]
        wrapped = wrapped[:TEXT_LINES_MOST + 20]
        chinese = any(re.search(r"[　-鿿＀-￯]", one) for one in wrapped + [title])
        faces = ("msyh.ttc", "PingFang.ttc", "NotoSansCJK-Regular.ttc", "wqy-microhei.ttc", "simhei.ttf") if chinese             else ("consola.ttf", "DejaVuSansMono.ttf", "Menlo.ttc", "cour.ttf")
        font = None
        for name in faces:
            try:
                font = ImageFont.truetype(name, 28)
                break
            except OSError:
                continue
        font = font or ImageFont.load_default()
        step = 40
        longest = max(font.getlength(one) for one in wrapped + [title])
        image = Image.new("RGB", (int(max(900, longest + 60)), 110 + step * len(wrapped)), (250, 248, 242))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, image.width, 64), fill=(52, 58, 72))
        draw.text((24, 16), title[:90], fill=(240, 240, 240), font=font)
        for n, one in enumerate(wrapped):
            draw.text((24, 86 + n * step), one, fill=(40, 40, 46), font=font)
        return _save(image, out)
    except Exception:
        log.debug("lines could not be drawn as a picture", exc_info=True)
        return False


def _read(url: str, config: Any = None) -> Optional[tuple]:
    """An address read (http or https, never a private one): (its bytes, its type, the address it ended at)."""
    if not re.match(r"(?i)^https?://", url or ""):
        return None
    from ..common.privacy_folders import address_is_private
    if address_is_private(url, config):
        return None
    host = _host(url)
    if _refusing(host):
        log.info("a picture's address not read at %s: the site refuses programs", host)
        return None
    if not _wait_turn(host):
        log.info("a picture's address not read at %s: the site asked to be left alone for a while", host)
        return None
    try:
        request = urllib.request.Request(_ascii_address(url), headers={"User-Agent": AGENT})
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read(MOST_BYTES), str(response.headers.get("Content-Type") or ""), response.geturl()
    except Exception as error:                         # why, for the log: refused (403), not found, too slow
        _hold(host, error)
        log.info("a picture's address could not be read at %s: %s", host, str(error)[:120])
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
         sources: List[Dict[str, Any]], card: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """THE PICTURE'S CHOICES, THE BEST FIRST (play_world_find; the user, 2026-11-19: "模型主要rely on 自己搜"; 2026-11-20:
    "这个网站封面不对你可以去Google搜，不一定要一个途径"): the main model with its own web search, their files on their
    computer, screenshots and the files and pages they had open among what it may choose - known to the web guard as
    theirs, so none of it goes out in a search. Each choice {"theirs": id}, or {"url", "what", "allowed"} for the
    web's ("allowed": whether an address may be read - seen by that search); [] when nothing came. `sources` each
    {"id", "kind" ("file", "folder", "page"), "title"}."""
    from ..common.languages import in_english
    from ..models.answers import json_objects, read_text
    from ..models.base import ModelRequest
    from ..runtime.web_guard import WebGuard
    look_for = " ".join(str(look_for or "").split())[:200]
    files = [s for s in sources if s.get("kind") in ("file", "folder") and str(s.get("id", "")).startswith("file-")]
    opened = [s for s in sources if s not in files]
    given = {"what": " ".join(str(what or "").split())[:300], "look_for": look_for,
             **({"card": {k: str(card.get(k) or "")[:300] for k in ("title", "statement", "status") if card.get(k)}}
                if card else {}),
             **({"their_files": [{"id": s["id"], "kind": s["kind"], "name": s["title"]} for s in files]} if files else {}),
             **({"their_pictures": [{"id": p["id"], "what": p.get("what", ""), "when": p.get("when", "")}
                                    for p in theirs]} if theirs else {}),
             **({"their_sources": [{"id": s["id"], "kind": s["kind"], "title": s["title"]} for s in opened]}
                if opened else {})}
    guard = WebGuard()
    private = json.dumps({k: v for k, v in given.items() if k.startswith("their_") or k == "card"}, ensure_ascii=False)
    guard.know(private, "user")
    # HELD, not only known - only what is secret (2026-11-20: "搜索检查只查密码邮箱之类，项目名人名都不拦").
    guard.hold(private)
    try:
        response = models.invoke(ModelRequest(
            system=FIND_SYSTEM, task=TASK_FIND, max_tokens=1500, builtin_web=True, web_guard=guard,
            messages=[{"role": "user", "content": json.dumps(given, ensure_ascii=False)}],
            language=in_english('"what" - an address kept exactly as it is')), role="fast_reply")   # the small model (2026-11-20)
        text, _cut = read_text(response)
        said = next(iter(json_objects(text or "")), {})
    except Exception:
        log.info("a picture could not be found", exc_info=True)
        return []
    raw = said.get("choices") if isinstance(said.get("choices"), list) else [said]
    known = {p["id"] for p in theirs} | {s["id"] for s in sources}
    allowed = lambda address: guard.check_address(address) is None          # noqa: E731
    out: List[Dict[str, Any]] = []
    for one in raw[:CHOICES_MOST]:
        if not isinstance(one, dict):
            continue
        mine = str(one.get("theirs") or "").strip()
        url = str(one.get("url") or "").strip()
        if mine and mine in known:
            out.append({"theirs": mine})
        elif url:
            out.append({"url": url, "what": " ".join(str(one.get("what") or "").split())[:160], "allowed": allowed})
    return out


__all__ = ["TASK_FIND", "FIND_SYSTEM", "from_file", "from_url", "find", "TASK_FIND_LOOK", "FIND_LOOK_SYSTEM"]
