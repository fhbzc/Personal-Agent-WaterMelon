"""THE WEB GUARD: what an agent may search for, and which pages it may open.

Asking for a page sends its address to the site, and a search sends its
words to the search engine - so an address or a search the model composed
can carry what it read off to whoever is listening, and a page it read may
tell it to do exactly that (prompt injection). Two checks stand between:

* A SEARCH carries words, not data: none longer than a sentence, none that
  looks like a key, a token or something encoded (`check_query`).
* A PAGE is opened only when nothing read can travel in its address
  (`check_address`) - in one of five ways, from the narrowest; the first
  was the only one until the user widened it (2026-10-18):

  1. an address that EXISTS - found in search results, on a page read, in
     the user's own instruction, files or records - or the front page of a
     site one came from;
  2. any page of a site THE USER NAMED - in their instruction, files or
     records: it came from them, not from a page;
  3. any page of a REFERENCE SITE (`REFERENCE_HOSTS`) - documentation,
     package registries, encyclopaedias - whose owners see nobody's visits;
  4. a known address made SHORTER - a level up, its query or fragment
     dropped, http made https: nothing can be added that way;
  5. on a site already known, a short address with no query, NONE OF WHOSE
     WORDS IS THE USER'S ALONE: every word in it either never appeared in
     what was read of the user's, or appeared on the public pages of ANOTHER
     site too - a page cannot make the user's words public for its own
     site by listing every word there is - and no number of four digits
     or more of the user's at all.

  A site made to collect what is sent to it (`COLLECTORS`) is opened only
  at an address that exists, whatever the rest allows.

What is known grows as the agent reads: `know(text, "user")` for what came
from the user (their instruction, their files, their recorded work),
`know(text, "web")` for what came from the web. One guard serves one
agent's tools; a client that searches by itself (Claude Code) is held to
the same guard through its hooks (models/providers/mcp_bridge.py).
"""

from __future__ import annotations

import re
import threading
import urllib.parse
from typing import Any, Dict, Iterable, Optional, Set

#: The longest search sent.
MOST_QUERY = 300
#: What looks like a secret or an encoded payload: a long run of key-like
#: characters, or a known key's prefix. Text from the user's files carried
#: out inside a search or an address looks like this once encoded.
SECRET_LIKE = re.compile(
    r"(?:[A-Za-z0-9+/_\-]{40,}={0,2})|(?:\b[0-9a-fA-F]{32,}\b)"
    r"|(?:\b(?:sk-|ghp_|gho_|github_pat_|xox[abpr]-|AKIA|AIza|ya29\.)[\w\-]{8,})")
URL_IN_TEXT = re.compile(r"""(?i)\bhttps?://[^\s<>"'()\[\]{}]+""")
#: The words of a text, as they are compared.
WORDS = re.compile(r"[a-z0-9]+")

#: REFERENCE SITES: any page may be opened. Documentation, package
#: registries and encyclopaedias, whose owners are not shown who asked for
#: which page. Not a site where anyone may publish and then watch their
#: pages' visits (a code host's traffic pages, a docs host's analytics).
#: A host here covers its subdomains.
REFERENCE_HOSTS = frozenset({
    # Python
    "docs.python.org", "peps.python.org", "pypi.org", "packaging.python.org",
    "numpy.org", "pandas.pydata.org", "matplotlib.org", "scipy.org",
    "scikit-learn.org", "pytorch.org", "docs.pytest.org", "docs.djangoproject.com",
    "flask.palletsprojects.com", "docs.sqlalchemy.org",
    # The web, JavaScript
    "developer.mozilla.org", "nodejs.org", "npmjs.com", "typescriptlang.org",
    "react.dev", "vuejs.org", "w3.org", "whatwg.org", "web.dev", "caniuse.com",
    # Other languages and their registries
    "doc.rust-lang.org", "docs.rs", "crates.io", "go.dev", "pkg.go.dev",
    "cran.r-project.org", "r-project.org", "docs.oracle.com", "cppreference.com",
    "en.cppreference.com", "kotlinlang.org", "swift.org", "ruby-doc.org",
    "rubygems.org", "php.net", "docs.julialang.org", "hackage.haskell.org",
    # Platforms' own documentation
    "learn.microsoft.com", "developer.apple.com", "developer.android.com",
    "docs.github.com", "docs.docker.com", "kubernetes.io", "git-scm.com",
    "postgresql.org", "sqlite.org", "dev.mysql.com", "redis.io",
    # Reference
    "wikipedia.org", "wikimedia.org", "wiktionary.org", "wikidata.org",
    "stackoverflow.com", "stackexchange.com", "superuser.com", "serverfault.com",
    "askubuntu.com", "arxiv.org", "doi.org", "rfc-editor.org", "ietf.org",
    "unicode.org", "iso.org", "latex-project.org", "ctan.org",
    # PUBLIC CODE AND DATA, as hosted (the user, 2026-10-27: a work could not
    # fetch the CFF 1.2.0 schema from raw.githubusercontent.com). Where files,
    # schemas and data live - and none shows a repository's or a package's
    # owner who asked for what, so an address made up there carries nothing
    # to anyone. Not the pages their users host (github.io, a Space): those
    # run their owner's own scripts, which can see the address.
    "github.com", "githubusercontent.com", "gitlab.com", "bitbucket.org",
    "codeberg.org", "files.pythonhosted.org", "registry.npmjs.org",
    "cdn.jsdelivr.net", "unpkg.com", "zenodo.org", "figshare.com",
    "json.schemastore.org", "schema.org",
})

#: SITES MADE TO COLLECT WHAT IS SENT TO THEM - request bins, tunnels,
#: interaction catchers: opened only at an address that exists.
COLLECTORS = frozenset({
    "webhook.site", "requestbin.com", "requestbin.net", "pipedream.net",
    "pipedream.com", "beeceptor.com", "hookbin.com", "requestcatcher.com",
    "ngrok.io", "ngrok.app", "ngrok-free.app", "ngrok.dev", "trycloudflare.com",
    "loca.lt", "localtunnel.me", "serveo.net", "burpcollaborator.net",
    "oastify.com", "interact.sh", "oast.fun", "oast.live", "oast.me", "oast.online",
    "oast.pro", "oast.site", "canarytokens.com", "dnslog.cn", "ceye.io",
    "free.beeceptor.com", "postb.in", "httpbin.org", "mockbin.org", "smee.io",
})

#: An address composed on a known site (way 5): at most this long, in at
#: most this many parts.
MOST_COMPOSED = 120
MOST_PARTS = 8


def address_key(url: str) -> str:
    """An address as it is compared: scheme and host in lower case, the
    fragment dropped, a trailing full stop or comma from prose dropped."""
    url = url.strip().rstrip(".,;:")
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parts.scheme.lower(), parts.netloc.lower(),
                                    parts.path or "/", parts.query, ""))


def _host(netloc: str) -> str:
    """The host alone: no port, no user."""
    host = netloc.rsplit("@", 1)[-1]
    if host.startswith("["):
        return host.split("]", 1)[0] + "]"
    return host.split(":", 1)[0]


def _under(host: str, names: Iterable[str]) -> bool:
    """Whether a host is one of these or under one of them."""
    return any(host == one or host.endswith("." + one) for one in names)


def _segments(path: str) -> list:
    return [one for one in (path or "/").split("/") if one]


class WebGuard:
    """One agent's checks on the web, and what it has seen so far."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        #: The addresses that exist, as compared (`address_key`).
        self.known_addresses: Set[str] = set()
        #: The sites they are on (host and port, as in the address).
        self.known_hosts: Set[str] = set()
        #: The sites named in what came from the user.
        self.user_hosts: Set[str] = set()
        #: The words of what was read of the user's - and of the public web,
        #: by the site they were read on ("search" for search results).
        self.private_words: Set[str] = set()
        self.public_words: Dict[str, Set[str]] = {}

    # -- what is known --------------------------------------------------------------
    def know(self, text: Any, source: str = "user", where: str = "") -> None:
        """What was read: the addresses in it exist, and its words are the
        user's ("user") or public ("web") - read on the site `where`."""
        text = str(text or "")
        if not text:
            return
        words = set(WORDS.findall(text.lower()))
        with self._lock:
            if source == "web":
                self.public_words.setdefault(_host(where.lower()) or "?", set()).update(words)
            else:
                self.private_words.update(words)
            for found in URL_IN_TEXT.findall(text):
                key = address_key(found)
                netloc = urllib.parse.urlsplit(key).netloc
                if not netloc:
                    continue
                self.known_addresses.add(key)
                self.known_hosts.add(netloc)
                if source != "web":
                    self.user_hosts.add(_host(netloc))

    # -- the checks -----------------------------------------------------------------
    @staticmethod
    def check_query(query: str) -> Optional[str]:
        """Why this search may not be sent, or None: too long, or carrying
        something that looks like a key or an encoded payload."""
        if len(query or "") > MOST_QUERY:
            return f"a search is at most {MOST_QUERY} characters"
        if SECRET_LIKE.search(query or ""):
            return ("the search carries something that looks like a key, a "
                    "token or encoded data - searches are words, not data")
        return None

    def check_address(self, url: str) -> Optional[str]:
        """Why this address may not be opened, or None (the module says the
        ways it may)."""
        return self.why_not(url)[1]

    def why_not(self, url: str) -> tuple:
        """(the way it may be opened, None) - or ("", why not)."""
        if not re.match(r"(?i)^https?://", url or ""):
            return "", "only http and https addresses can be read"
        key = address_key(url)
        parts = urllib.parse.urlsplit(key)
        host = _host(parts.netloc)
        with self._lock:
            if key in self.known_addresses:
                return "it exists", None
            if parts.path in ("", "/") and not parts.query and parts.netloc in self.known_hosts:
                return "the front page of a known site", None
            if _under(host, COLLECTORS):
                return "", ("this site collects what is sent to it: only an address "
                            "that already exists there can be opened")
            if _under(host, self.user_hosts):
                return "a site the user named", None
            if _under(host, REFERENCE_HOSTS):
                return "a reference site", None
            if self._shorter(parts):
                return "a known address, shorter", None
            if parts.netloc in self.known_hosts:
                why = self._composed(parts)
                if why is None:
                    return "on a known site, nothing of the user's in it", None
                return "", why
        return "", ("this address did not come from a search, a page read, the "
                    "user's files or their instruction, and is not on a site they "
                    "named or a reference site - only an address that already "
                    "exists can be opened (so nothing read can be sent out inside "
                    "one). Search for it, or open the page that links to it.")

    def _shorter(self, parts: urllib.parse.SplitResult) -> bool:
        """A known address made shorter: on its site (http or https), a level
        up or the same path, its query the same or dropped."""
        mine = _segments(parts.path)
        for known in self.known_addresses:
            other = urllib.parse.urlsplit(known)
            if other.netloc != parts.netloc:
                continue
            theirs = _segments(other.path)
            if len(mine) > len(theirs) or theirs[:len(mine)] != mine:
                continue
            if parts.query and (parts.query != other.query or mine != theirs):
                continue
            return True
        return False

    def _composed(self, parts: urllib.parse.SplitResult) -> Optional[str]:
        """Why an address composed on a known site may not be opened, or
        None: short, no query, nothing key-like, no word the user's alone."""
        if parts.query:
            return "an address made up here may not carry a query - search for the page"
        path = urllib.parse.unquote(parts.path or "/")
        if len(path) > MOST_COMPOSED or len(_segments(path)) > MOST_PARTS:
            return "an address made up here must be short - search for the page"
        if SECRET_LIKE.search(path):
            return "the address carries something that looks like a key or encoded data"
        host = _host(parts.netloc)
        elsewhere = [words for site, words in self.public_words.items() if site != host]
        theirs = [word for word in WORDS.findall(path.lower())
                  if word in self.private_words and (
                      (word.isdigit() and len(word) >= 4)
                      or not any(word in words for words in elsewhere))]
        if theirs:
            return ("the address carries words from the user's own files or "
                    "work that no public page read has shown - search for the page")
        return None

    # -- a client's own web tools, held to the same checks (its hooks) ---------------------
    def hook(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """A client's hook asking before its own WebSearch or WebFetch runs,
        or telling what came back: the answer the hook prints."""
        event = str(payload.get("hook_event_name") or "")
        tool = str(payload.get("tool_name") or "")
        given = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
        if event == "PreToolUse":
            if tool == "WebSearch":
                why = self.check_query(str(given.get("query") or ""))
            elif tool == "WebFetch":
                why = self.check_address(str(given.get("url") or ""))
            else:
                why = None
            if tool not in ("WebSearch", "WebFetch"):
                return {}
            # Said either way: the client allows neither of them by itself
            # (claude_code_provider), so only this answer lets one through.
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny" if why else "allow",
                "permissionDecisionReason": why or "the agent's web check passed"}}
        if event == "PostToolUse" and tool in ("WebSearch", "WebFetch"):
            import json
            url = str(given.get("url") or "")
            where = urllib.parse.urlsplit(url).netloc if tool == "WebFetch" else "search"
            self.know(" ".join([url, json.dumps(payload.get("tool_response"),
                                                ensure_ascii=False, default=str)]),
                      "web", where)
        return {}


__all__ = ["WebGuard", "address_key", "MOST_QUERY", "SECRET_LIKE", "URL_IN_TEXT",
           "REFERENCE_HOSTS", "COLLECTORS"]
