"""READING THE WEB FOR THE LEARNING LIST (learning/items.py, the user,
2026-10-24).

Only what an item names, as anyone could read it:

  * ITS SCOPE AND NOTHING ELSE: the one page; the pages under its address
    on its own site, found by following the links of those pages (at most
    `MOST_PAGES`); a GitHub repository through GitHub's public interface -
    its description, its README, the list of its files and the text of the
    first few; for the user's own repository, also what the user wrote in
    its review comments, from their account in Connections (the profile
    they keep of themselves). No link that leaves the scope is followed.
  * NEVER SIGNED IN: no cookie, no key, no password is sent; a page that
    asks for one is not read. Only GET. The site's robots.txt is kept to,
    and a site is asked at most once a second.
  * WHAT A PAGE SAYS IS MATERIAL, NEVER AN INSTRUCTION. Nothing read here
    reaches a model that can act: the reading model has no tools. A page
    written to steer whoever reads it - words addressed to an assistant,
    text hidden from a human reader (styled away, in comments, in
    zero-width characters) - is SET ASIDE, not read, and said so on the
    item (`suspicious`).
  * KEPT SMALL: the text of a page up to `KEPT_CHARS` is kept as a copy in
    the learning's own folder (where it came from, when, its hash); a larger
    one is kept only as its address - what was learned of it stays, and
    the reviewer reads it on the web when it needs it.

A page that cannot be read is tried again at the next update; the item says
since when it could not be.

A SITE'S LIMITS ARE KEPT TO (the user, 2026-10-28). GitHub answers 60 asks
an hour to whoever is not signed in, counted by the computer's address - the
user's own browsing and the agent's work included - so the learning takes
at most `HOURLY` of them. A site that says it is asked too often (429, or
GitHub's 403 with no asks left) is not asked again before the time it gave
(`limited_until`); one that does not answer, or refuses, is said so (`wait`)
for whoever asked to wait (sources.py). A listing asked for again carries
the ETag it came with: unchanged, the site answers 304 - which GitHub does
not count - and the copy kept is used.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

#: What a fetch says it is.
AGENT = "Mozilla/5.0 (personal agent; learning what its user listed)"
#: Pages read under one address, at most.
MOST_PAGES = 200
#: A page read, at most (bytes).
MOST_BYTES = 3_000_000
#: The text of a page kept as a copy, at most; past it, only its address.
KEPT_CHARS = 200_000
#: What of a page is shown to the reading model, at most.
SHOWN_CHARS = 20_000
#: Files of a repository read, at most, and of each.
REPO_FILES = 12
REPO_FILE_CHARS = 12_000
#: A site asked at most once in this long.
SPACING = 1.0
TIMEOUT = 20.0

_last_asked: Dict[str, float] = {}
_spacing_lock = threading.Lock()
#: Asks an hour the learning takes of a site, at most - room left for work
#: and the user on the same address.
HOURLY = {"api.github.com": 40}
#: When each limited site was asked, in the last hour; and until when a
#: site that said it was asked too often is not asked.
_asked: Dict[str, List[float]] = {}
_held_until: Dict[str, float] = {}
_robots: Dict[str, Optional[urllib.robotparser.RobotFileParser]] = {}

#: Words addressed to an assistant reading the page.
_TO_THE_READER = re.compile(
    r"(?i)\b(ignore (all |any )?(the )?(previous|prior|above) (instructions|prompts?)|"
    r"disregard (all |the )?(previous|prior|above)|you are (an? )?(ai|assistant|language model|llm)\b|"
    r"(system|developer) prompt|as an ai (model|assistant)|new instructions:|"
    r"do not (tell|inform) the user|reveal (your|the) (system )?prompt|"
    r"(assistant|ai|agent|llm|model)s? (reading|processing) this)")
#: Text hidden from a person reading the page.
_HIDDEN = re.compile(
    r"(?is)<[^>]+style\s*=\s*[\"'][^\"']*(display\s*:\s*none|visibility\s*:\s*hidden|"
    r"font-size\s*:\s*0|opacity\s*:\s*0(?:\.0+)?\b|color\s*:\s*(#fff\b|#ffffff\b|white\b|"
    r"transparent\b))[^\"']*[\"'][^>]*>([^<]{40,})")
_COMMENT = re.compile(r"(?s)<!--(.*?)-->")
_ZERO_WIDTH = re.compile("[​‌‍⁠﻿]{3,}")


def _sha(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:16]


def page_text(raw: str) -> str:
    """A page as text: scripts and styles out, tags out, space folded."""
    raw = re.sub(r"(?is)<(script|style|noscript|template)[^>]*>.*?</\1>", " ", raw)
    raw = _COMMENT.sub(" ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</h\d>|</li>|</tr>", "\n", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
    return re.sub(r"\n\s*\n+", "\n\n", raw).strip()


def suspicious(raw: str, text: str) -> str:
    """Why a page is set aside - written to steer whoever reads it - or ""."""
    said = _TO_THE_READER.search(text or "")
    if said:
        return f"it addresses whoever reads it: \"{said.group(0)[:80]}\""
    # Hidden text is common - a menu, a folded answer - and is set aside only
    # when it is words for an assistant.
    for match in _HIDDEN.finditer(raw or ""):
        if _TO_THE_READER.search(match.group(match.lastindex or 0)):
            return "it holds words for an assistant, hidden from a person reading it"
    for comment in _COMMENT.findall(raw or ""):
        if _TO_THE_READER.search(comment):
            return "it holds words for an assistant in its hidden comments"
    if _ZERO_WIDTH.search(raw or ""):
        return "it holds runs of invisible characters"
    return ""


def _wait_for(host: str) -> None:
    with _spacing_lock:
        wait = SPACING - (time.time() - _last_asked.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        _last_asked[host] = time.time()


def robots_allow(url: str, opener: Optional[Callable[..., Any]] = None) -> bool:
    parts = urllib.parse.urlsplit(url)
    base = f"{parts.scheme}://{parts.netloc}"
    if base not in _robots:
        parser = urllib.robotparser.RobotFileParser()
        try:
            request = urllib.request.Request(base + "/robots.txt", headers={"User-Agent": AGENT})
            with (opener or urllib.request.urlopen)(request, timeout=TIMEOUT) as response:
                parser.parse(response.read(200_000).decode("utf-8", "replace").splitlines())
            _robots[base] = parser
        except urllib.error.HTTPError as exc:
            # No robots.txt: everything may be read; one that refuses: none.
            _robots[base] = None if exc.code in (404, 410) else parser
            if exc.code in (401, 403):
                parser.disallow_all = True
        except Exception:
            _robots[base] = None
    parser = _robots[base]
    return True if parser is None else parser.can_fetch(AGENT, url)


def _held(host: str) -> float:
    """Until when a site is not asked: the time it gave, or the hour's
    asks used up - 0 when it may be asked now."""
    moment = time.time()
    with _spacing_lock:
        if _held_until.get(host, 0) > moment:
            return _held_until[host]
        most = HOURLY.get(host)
        if most is None:
            return 0.0
        asked = [one for one in _asked.get(host, []) if moment - one < 3600]
        _asked[host] = asked
        return asked[0] + 3600 if len(asked) >= most else 0.0


def _count(host: str, back: bool = False) -> None:
    if host not in HOURLY:
        return
    with _spacing_lock:
        asked = _asked.setdefault(host, [])
        if back:
            if asked:
                asked.pop()
        else:
            asked.append(time.time())


def _limited(exc: Any) -> float:
    """When a site that refused said it may be asked again - 0 when the
    refusal was not about being asked too often."""
    headers = getattr(exc, "headers", None) or {}
    moment = time.time()
    remaining = str(headers.get("X-RateLimit-Remaining", "") or "")
    if exc.code == 429 or (exc.code == 403 and remaining == "0"):
        after = str(headers.get("Retry-After", "") or "")
        if after.isdigit():
            return moment + int(after)
        reset = str(headers.get("X-RateLimit-Reset", "") or "")
        if reset.isdigit():
            return max(moment + 5, float(reset) + 5)
        return moment + 3600
    return 0.0


def get(url: str, accept: str = "", opener: Optional[Callable[..., Any]] = None,
        etag: str = "") -> Dict[str, Any]:
    """One address read, signed in to nothing: its kind, its text, the raw
    page, its ETag; `not_modified` when the ETag given still holds; or what
    went wrong - with `wait` ("limited" and `limited_until`, "temporary",
    "refused") when asking again later may go otherwise."""
    if not re.match(r"(?i)^https?://", url or ""):
        return {"error": "only http and https addresses are read"}
    host = urllib.parse.urlsplit(url).netloc.lower()
    held = _held(host)
    if held:
        return {"error": f"{host} is not asked again before {time.strftime('%H:%M', time.localtime(held))} "
                         "- it limits how often it is asked", "wait": "limited",
                "limited_until": held}
    _wait_for(host)
    headers = {"User-Agent": AGENT}
    if accept:
        headers["Accept"] = accept
    if etag:
        headers["If-None-Match"] = etag
    request = urllib.request.Request(url, headers=headers)
    _count(host)
    try:
        with (opener or urllib.request.urlopen)(request, timeout=TIMEOUT) as response:
            kind = response.headers.get("Content-Type", "") or ""
            tag = response.headers.get("ETag", "") or ""
            body = response.read(MOST_BYTES)
            final = response.geturl() if hasattr(response, "geturl") else url
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            _count(host, back=True)            # not counted by the site either
            return {"url": url, "not_modified": True, "etag": etag}
        until = _limited(exc)
        if until:
            with _spacing_lock:
                _held_until[host] = until
            return {"error": f"{host} limits how often it is asked - asked again at "
                             f"{time.strftime('%H:%M', time.localtime(until))}",
                    "wait": "limited", "limited_until": until}
        if exc.code in (401, 403):
            return {"error": f"it asks to be signed in ({exc.code}) - only public pages are read",
                    "wait": "refused"}
        if exc.code >= 500:
            return {"error": f"it could not be read ({exc.code})", "wait": "temporary"}
        return {"error": f"it could not be read ({exc.code})"}
    except Exception as exc:
        return {"error": f"it could not be read: {exc}"[:300], "wait": "temporary"}
    charset = (re.search(r"charset=([\w-]+)", kind) or [None, "utf-8"])[1]
    raw = body.decode(charset or "utf-8", errors="replace")
    return {"url": final, "kind": kind, "raw": raw, "etag": tag}


def _links(raw: str, base: str) -> List[str]:
    out = []
    for href in re.findall(r'(?is)<a[^>]+href\s*=\s*["\']([^"\'#][^"\']*)["\']', raw):
        absolute = urllib.parse.urljoin(base, html.unescape(href)).split("#", 1)[0]
        if re.match(r"(?i)^https?://", absolute) and absolute not in out:
            out.append(absolute)
    return out


def _within(url: str, scope: str) -> bool:
    a, b = urllib.parse.urlsplit(url), urllib.parse.urlsplit(scope)
    if a.netloc.lower() != b.netloc.lower():
        return False
    root = b.path.rstrip("/")
    return a.path == root or a.path.startswith(root + "/") or not root


_ASSET = re.compile(r"(?i)\.(png|jpe?g|gif|svg|webp|ico|css|js|mjs|zip|gz|tar|exe|dmg|mp4|"
                    r"mp3|wav|woff2?|ttf|pdf)(\?|$)")


class Page(dict):
    """One page read: url, title, text, hash, kept (its copy, or "" when it
    is kept only as its address), state (ok, suspicious, failed), note."""


def _page(url: str, got: Dict[str, Any]) -> Page:
    if got.get("error"):
        return Page(url=url, title="", text="", hash="", state="failed", note=got["error"])
    raw = got.get("raw") or ""
    is_html = "html" in str(got.get("kind") or "").lower() or raw.lstrip()[:1] == "<"
    title = ""
    if is_html:
        found = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw)
        title = html.unescape(found.group(1)).strip()[:200] if found else ""
        text = page_text(raw)
    else:
        text = raw
    why = suspicious(raw if is_html else "", text)
    if why:
        return Page(url=got.get("url") or url, title=title, text="", hash=_sha(text),
                    state="suspicious", note=why)
    return Page(url=got.get("url") or url, title=title, text=text, hash=_sha(text),
                state="ok", note="")


class Online:
    """The web read for the learning list, with each page's copy kept in
    `folder` (the learning's own)."""

    def __init__(self, folder: Path, opener: Optional[Callable[..., Any]] = None) -> None:
        self.folder = Path(folder)
        self.opener = opener
        self._tags: Optional[Dict[str, Any]] = None

    def _get(self, url: str, accept: str = "") -> Dict[str, Any]:
        if not robots_allow(url, self.opener):
            return {"error": "the site's robots.txt asks not to be read", "wait": "refused"}
        if "github" not in accept:
            return get(url, accept, self.opener)
        # A listing of GitHub's: asked with the ETag it came with, the copy
        # kept used when it has not changed (304, not counted by GitHub).
        tags = self._etags()
        kept = tags.get(url) or {}
        got = get(url, accept, self.opener, str(kept.get("etag") or ""))
        if got.get("not_modified") and "raw" in kept:
            return {"url": url, "kind": "application/json", "raw": kept["raw"],
                    "etag": kept.get("etag", ""), "not_modified": True}
        if got.get("etag") and not got.get("error") and len(got.get("raw") or "") <= 2_000_000:
            tags[url] = {"etag": got["etag"], "raw": got["raw"]}
            self._save_etags()
        return got

    def _etags(self) -> Dict[str, Any]:
        if self._tags is None:
            try:
                self._tags = json.loads((self.folder / "etags.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._tags = {}
        return self._tags

    def _save_etags(self) -> None:
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            tags = self._etags()
            while len(tags) > 400:
                tags.pop(next(iter(tags)))
            (self.folder / "etags.json").write_text(json.dumps(tags), encoding="utf-8")
        except OSError:
            pass

    def keep(self, page: Page) -> str:
        """Its text kept as a copy, when it is small enough: where."""
        if page.get("state") != "ok" or len(page.get("text") or "") > KEPT_CHARS:
            return ""
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            path = self.folder / (hashlib.sha1(page["url"].encode("utf-8")).hexdigest() + ".txt")
            path.write_text(json.dumps({"url": page["url"], "title": page.get("title", ""),
                                        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                        "hash": page.get("hash", "")}, ensure_ascii=False)
                            + "\n" + page["text"], encoding="utf-8")
            return str(path)
        except OSError:
            return ""

    # -- an item's pages ----------------------------------------------------------
    def pages(self, item: Dict[str, Any], login: str = "") -> List[Page]:
        """Every page of the item's scope, as it is now - of each of its
        addresses, when a unit holds several (`more`, sources.py)."""
        out: List[Page] = []
        for source in [str(item.get("source") or ""), *[str(one) for one in item.get("more") or []]]:
            if source:
                out += self._pages_of(source, item.get("scope") or "page",
                                      login if item.get("whose") == "mine" else "")
        return out

    def _pages_of(self, source: str, scope: str, login: str) -> List[Page]:
        if scope == "repo" or (scope != "prefix" and _github_repo(source)):
            found = _github_repo(source)
            if found:
                # "?files=top": the files at its top level only (sources.py).
                return self._repo(found[0], found[1], login, _repo_folder(source),
                                  top_only=source.endswith("?files=top"))
        if scope == "prefix" or (scope == "repo" and source.endswith("/")):
            return self._crawl(source)
        return [_page(source, self._get(source))]

    def _crawl(self, start: str) -> List[Page]:
        queue, seen, out = [start], {start}, []
        while queue and len(out) < MOST_PAGES:
            url = queue.pop(0)
            got = self._get(url)
            page = _page(url, got)
            out.append(page)
            for link in _links(got.get("raw") or "", got.get("url") or url):
                if link not in seen and _within(link, start) and not _ASSET.search(link):
                    seen.add(link)
                    queue.append(link)
        return out

    def _repo(self, owner: str, name: str, login: str, folder: str = "",
              top_only: bool = False) -> List[Page]:
        api = f"https://api.github.com/repos/{owner}/{name}"
        out: List[Page] = []
        meta = self._get(api, "application/vnd.github+json")
        if meta.get("error"):
            return [Page(url=f"https://github.com/{owner}/{name}", title="", text="", hash="",
                         state="failed", note=meta["error"])]
        try:
            info = json.loads(meta.get("raw") or "{}")
        except ValueError:
            info = {}
        branch = str(info.get("default_branch") or "main")
        about = (f"REPOSITORY {owner}/{name}: {info.get('description') or ''}\n"
                 f"language: {info.get('language') or '?'}; topics: "
                 f"{', '.join(info.get('topics') or [])}; owner: {owner}")
        out.append(Page(url=f"https://github.com/{owner}/{name}", title=f"{owner}/{name}",
                        text=about, hash=_sha(about), state="ok", note=""))
        tree = self._get(f"{api}/git/trees/{urllib.parse.quote(branch)}?recursive=1",
                         "application/vnd.github+json")
        files: List[str] = []
        try:
            files = [one["path"] for one in json.loads(tree.get("raw") or "{}").get("tree") or []
                     if one.get("type") == "blob"]
        except (ValueError, AttributeError):
            files = []
        if folder:
            # One project of a repository that holds several (sources.py).
            files = [one for one in files if one.startswith(folder.rstrip("/") + "/")]
        elif top_only:
            files = [one for one in files if "/" not in one]
        if files:
            listing = "THE FILES OF THE REPOSITORY:\n" + "\n".join(f"  {one}" for one in files[:400])
            out.append(Page(url=f"https://github.com/{owner}/{name}/tree/{branch}",
                            title="its files", text=listing, hash=_sha(listing), state="ok", note=""))
        for path in _worth_reading(files)[:REPO_FILES]:
            raw = f"https://raw.githubusercontent.com/{owner}/{name}/{branch}/{path}"
            page = _page(raw, self._get(raw))
            page["text"] = (page.get("text") or "")[:REPO_FILE_CHARS]
            page["title"] = path
            out.append(page)
        if login:
            out += self._review_words(api, owner, name, login)
        return out

    def _review_words(self, api: str, owner: str, name: str, login: str) -> List[Page]:
        """What the user wrote on the repository - review comments and
        comments on its issues and pull requests - and nobody else's."""
        out: List[Page] = []
        for kind, address in (("review comments", f"{api}/pulls/comments?per_page=100"),
                              ("comments", f"{api}/issues/comments?per_page=100")):
            got = self._get(address, "application/vnd.github+json")
            try:
                rows = json.loads(got.get("raw") or "[]")
            except ValueError:
                continue
            mine = [one for one in rows if isinstance(one, dict)
                    and str((one.get("user") or {}).get("login") or "").lower() == login.lower()]
            if not mine:
                continue
            text = (f"WHAT THE USER ({login}) WROTE IN THIS REPOSITORY'S {kind.upper()}:\n"
                    + "\n".join(f"  [{str(one.get('created_at') or '')[:10]}] "
                                f"{one.get('path') or one.get('html_url') or ''}: "
                                f"{str(one.get('body') or '')[:1500]}" for one in mine[:100]))
            why = suspicious("", text)
            out.append(Page(url=f"https://github.com/{owner}/{name}#{kind.replace(' ', '-')}",
                            title=f"your {kind}", text="" if why else text, hash=_sha(text),
                            state="suspicious" if why else "ok", note=why))
        return out

    # -- before it is added: what it would take ---------------------------------------
    def look(self, url: str, scope: str = "page") -> Dict[str, Any]:
        """A first look, for the plan: its title, the start of its text, how
        many pages its scope holds (for a site, those its first page links
        to under the address), and whether it can be read."""
        repo = _github_repo(url)
        got = self._get(url)
        page = _page(url, got)
        out = {"url": url, "title": page.get("title", ""), "state": page["state"],
               "note": page.get("note", ""), "start": (page.get("text") or "")[:3000],
               "chars": len(page.get("text") or ""), "pages": 1,
               "repo": "/".join(repo) if repo else ""}
        if scope == "prefix" and not got.get("error"):
            under_it = [one for one in _links(got.get("raw") or "", got.get("url") or url)
                        if _within(one, url) and not _ASSET.search(one)]
            out["pages"] = min(MOST_PAGES, 1 + len(set(under_it)))
        return out


def _repo_folder(url: str) -> str:
    """The folder of a repository an address names (.../tree/<branch>/<path>), or ""."""
    found = re.match(r"(?i)^https?://(?:www\.)?github\.com/[\w.-]+/[\w.-]+/tree/[^/]+/(.+?)/?$",
                     str(url or "").strip())
    return found.group(1) if found else ""


def _github_repo(url: str) -> Optional[Tuple[str, str]]:
    found = re.match(r"(?i)^https?://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?(?:/.*)?$",
                     str(url or "").strip())
    if not found or found.group(1).lower() in ("orgs", "topics", "search", "settings"):
        return None
    return found.group(1), found.group(2)


_README = re.compile(r"(?i)(^|/)(readme|contributing|style|styleguide|conventions|agents|claude)"
                     r"[^/]*\.(md|rst|txt)$")
_TEXT = re.compile(r"(?i)\.(md|rst|txt|py|js|ts|tsx|jsx|go|rs|java|kt|rb|r|tex|toml|yaml|yml)$")


def _worth_reading(files: List[str]) -> List[str]:
    """Of a repository's files, those that say most of how it is made: its
    READMEs and guides first, then its shallowest text files."""
    guides = [one for one in files if _README.search(one)]
    rest = [one for one in files if one not in guides and _TEXT.search(one)
            and not re.search(r"(?i)(^|/)(node_modules|vendor|dist|build|\.github)/", one)]
    rest.sort(key=lambda one: (one.count("/"), len(one)))
    return guides + rest


def my_logins(db: Any) -> Dict[str, str]:
    """The user's own accounts, as their profile in Connections keeps them:
    {"github": "<login>", ...}."""
    out: Dict[str, str] = {}
    try:
        rows = db.query("SELECT a.platform, a.handle, a.url FROM connection_accounts a "
                        "JOIN connection_people p ON p.id = a.person_id WHERE p.is_self = 1")
    except Exception:
        return out
    for row in rows:
        url = str(row["url"] or "")
        found = re.match(r"(?i)^https?://(?:www\.)?github\.com/([A-Za-z0-9-]{1,39})/?$", url)
        handle = str(row["handle"] or "").strip().lstrip("@")
        if found:
            out.setdefault("github", found.group(1))
        elif str(row["platform"] or "").lower() == "github" and \
                re.fullmatch(r"[A-Za-z0-9-]{1,39}", handle):
            out.setdefault("github", handle)
    return out


def owned_by(url: str, logins: Dict[str, str]) -> bool:
    """Whether an address is the user's own, as far as can be told: a
    repository of their GitHub account."""
    repo = _github_repo(url)
    return bool(repo and logins.get("github") and repo[0].lower() == logins["github"].lower())


__all__ = ["Online", "Page", "page_text", "suspicious", "my_logins", "owned_by",
           "KEPT_CHARS", "SHOWN_CHARS", "MOST_PAGES"]
