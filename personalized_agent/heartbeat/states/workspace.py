"""What a state was worked on, as opposed to what it was worked on with.

Section 34.3, as amended. The grouping reads what a state SAYS - a
sentence and a paragraph, written by a model, embedded and compared. That
is an inference about an inference: two states about one afternoon are two
summaries of different fifteen-minute slices, and their similarity is how
alike the model happened to word them. It has a high noise floor for
exactly the reason the design doc gives, that one model writes every state
in a stable vocabulary about a handful of projects.

A path is not a paraphrase. ``survey-weights/scripts/fetch.py`` is the
same string every time or it is absent, and two states naming it are on
one piece of work whatever either of them says about it.

THE OBJECT, NOT THE INSTRUMENT. The thing that identifies work is what was
open - the file, the page, the document - and not what it was open in. One
script edited in Sublime, run in a terminal and diffed in a git client is
one workspace and three applications; a page is the same page in Firefox
and in Chrome. Keying on the application does not merely add noise, it
inverts the structure: it merges Gmail with YouTube for both being
Firefox, and splits one paper across three tools.

So applications are extracted and recorded - they are worth showing - and
they never identify anything. There is no list of which applications to
ignore, because that is a list that goes stale; the question asked of each
piece of evidence is only whether it names a thing or a way of handling
things.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Iterable, List, Sequence, Tuple
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

#: A Windows or POSIX path, as it appears inside a sentence.
_PATH = re.compile(
    # The drive letter must stand ALONE. Without the lookbehind the
    # "s" of "https://" is a drive and every web address reads as a
    # file path, which is how `s://chatgpt.com` became a workspace.
    r"(?:(?<![A-Za-z])[A-Za-z]:[\\/][^\s'\"<>|?*]+"
    r"|(?:/(?:home|Users|mnt|opt|srv|var|etc)/[^\s'\"<>|?*]+))")

#: A URL, with or without its scheme.
_URL = re.compile(
    r"(?:https?://[^\s'\"<>]+"
    r"|\bwww\.[a-z0-9-]+\.[a-z]{2,}[^\s'\"<>]*"
    r"|\b[a-z0-9-]+\.(?:com|org|net|edu|io|ai|co|cn|gov|dev|app|me|tv)"
    # The suffix must END there. Without this, `assistant.message` is a
    # `.me` domain, and it was - in a hundred and one states, which made
    # it the commonest workspace on the calendar.
    r"(?![a-z])"
    r"(?:/[^\s'\"<>]*)?)", re.I)

#: An executable, which names a tool and never a piece of work.
_EXE = re.compile(r"\b([A-Za-z0-9_+.-]+\.exe)\b", re.I)

#: How a window title separates what is open from what it is open in.
#:
#: Almost always "<the document> - <the application>", in one of a few
#: dashes. Not universal - `cmd.exe - python run.py` is the other way
#: round - so what comes out of this is treated as the weakest kind of
#: identity there is, and never as a path or a page.
_TITLE_SPLIT = re.compile(r"\s+[\u2013\u2014\u2015|-]\s+")

#: Query parameters that carry the identity of a page rather than a
#: session, a campaign or a scroll position.
#:
#: Named rather than guessed: `?id=` on OpenReview IS the paper, and
#: `?utm_source=` is never anything. Anything not named here is dropped,
#: because a query string that varies per visit turns one page into a
#: hundred workspaces.
IDENTIFYING = ("id", "v", "p", "paperid", "arxivid", "doi", "q", "search")

#: The query parameters above are kept only on hosts where they mean it.
#: Elsewhere `?q=` is a search box, and a search is a way of getting
#: somewhere rather than a place.
KEEPS_ITS_QUERY = ("openreview.net", "arxiv.org", "youtube.com",
                   "bilibili.com", "doi.org")

#: Hosts that are a way through rather than a place to be.
#:
#: This is the one list here, and it is short on purpose: a search engine
#: is structurally a hub - everybody's path to everywhere - so keying work
#: on it would put a morning's unrelated lookups in one task. It is not a
#: list of applications; those are excluded by kind.
A_WAY_THROUGH = ("google.com", "www.google.com", "bing.com",
                 "duckduckgo.com", "baidu.com", "t.co", "localhost")


def _tidy_path(said: str) -> str:
    """A path as a comparable key: one separator, no case, no trailing mark."""
    path = said.replace("\\", "/").rstrip("/.,;:)\"'")
    return path.lower()


def _tidy_url(said: str) -> str:
    """A URL as a comparable key: the host and the part that identifies it.

    The scheme, `www.`, the fragment and the session-shaped query are all
    ways of arriving at the same page, and two states that arrived
    differently are still on the same page.
    """
    raw = said if "//" in said else "http://" + said
    try:
        bits = urlsplit(raw)
    except ValueError:                   # pragma: no cover - defensive
        return ""
    host = (bits.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return ""
    path = (bits.path or "").rstrip("/").lower()
    kept = ""
    if host in KEEPS_ITS_QUERY and bits.query:
        for part in bits.query.split("&"):
            name, _, value = part.partition("=")
            if name.strip().lower() in IDENTIFYING and value:
                kept = f"?{name.strip().lower()}={value}"
                break
    return f"{host}{path}{kept}"


#: "App.exe: title", "App.exe window 'title'", "App.exe window: title".
_NAMED_WINDOW = re.compile(
    r"^\s*(?P<app>[\w .+-]+?\.exe)\s*(?:window\s*:?|:)\s*(?P<title>.+?)"
    r"\s*(?:\((?:[^()]*)\))?\s*$", re.I)
#: "title (App.exe)".
_APP_BEHIND = re.compile(
    r"^\s*(?P<title>.+?)\s*\((?P<app>[\w .+-]+?\.exe)\)\s*$", re.I)


def _from_a_title(said: str) -> Tuple[str, str]:
    """A window title split into what was open and what it was open in.

    Returns (document, application), either of which may be empty. The
    application half is kept because it is worth showing and thrown away
    for every purpose that decides what a state is about.
    """
    text = said.strip()
    if not text:
        return "", ""
    # THE SHAPES THE REVIEW WRITES A WINDOW IN, which are not window
    # titles: "WindowsTerminal.exe: ✳ notes", "Firefox.exe window
    # 'Inbox'", "Weixin.exe window: \"Calls\"", "notes (WindowsTerminal.exe)".
    # Read as titles, the whole string became an application's name - and
    # the session it named was dropped as a tool.
    named = _NAMED_WINDOW.match(text)
    if named:
        inner = named.group("title").strip().strip("'\"‘’“”").strip()
        document, _tool = _from_a_title(inner) if inner else ("", "")
        return (document or inner), named.group("app").strip()
    behind = _APP_BEHIND.match(text)
    if behind:
        inner = behind.group("title").strip().strip("'\"‘’“”").strip()
        document, _tool = _from_a_title(inner) if inner else ("", "")
        return (document or inner), behind.group("app").strip()
    # "Weixin.exe window: Photos and Videos" - the listener's own shape.
    if " window: " in text:
        tool, _, rest = text.partition(" window: ")
        return rest.strip(), tool.strip()
    parts = [one.strip() for one in _TITLE_SPLIT.split(text) if one.strip()]
    if len(parts) < 2:
        return ("", text) if _EXE.search(text) else (text, "")
    # The application is normally last. Where the FIRST part is plainly an
    # executable the title is the other way round, which is what a console
    # window looks like.
    if _EXE.search(parts[0]) and not _EXE.search(parts[-1]):
        return " - ".join(parts[1:]), parts[0]
    return " - ".join(parts[:-1]), parts[-1]


def read(sources: Sequence[str], evidence: Sequence[str] = ()) -> Dict[str, Any]:
    """What was open, from whatever a state recorded.

    Paths and URLs wherever they appear, including inside the sentences
    the review wrote - the listener knows them exactly and the state
    dissolves them into prose, so until that is fixed upstream this reads
    them back out. Everything is deduplicated and ordered, so the same
    activity gives the same keys.
    """
    files: List[str] = []
    urls: List[str] = []
    documents: List[str] = []
    apps: List[str] = []

    def keep(into: List[str], value: str) -> None:
        if value and value not in into:
            into.append(value)

    for said in list(sources or []) + list(evidence or []):
        text = str(said or "")
        if not text.strip():
            continue
        for hit in _PATH.findall(text):
            tidy = _tidy_path(hit)
            # `c` is not a path. The pattern can end on a drive letter
            # where a sentence breaks after "C:", and two states anchored
            # on it.
            if len(tidy) < 4 or tidy.count("/") < 1:
                continue
            # `C:/Windows/System32/cmd.exe` is a path to an APPLICATION,
            # and the rule here is the object and not the instrument. It
            # was the second commonest key before this line.
            if _EXE.search(tidy.rsplit("/", 1)[-1]):
                keep(apps, tidy.rsplit("/", 1)[-1])
                continue
            keep(files, tidy)
        for hit in _URL.findall(text):
            # A path like C:/x/y.com/z should not also read as a URL.
            if "://" not in hit and any(hit.lower() in one for one in files):
                continue
            tidy = _tidy_url(hit)
            if tidy and tidy.split("/")[0] not in A_WAY_THROUGH:
                keep(urls, tidy)
        for hit in _EXE.findall(text):
            keep(apps, hit.lower())

    # Window titles, which are what most of this user's states carry
    # instead of a path: the document half is the weakest identity there
    # is and is still an identity.
    for said in list(sources or []):
        document, tool = _from_a_title(str(said or ""))
        if tool:
            keep(apps, tool.lower())
        if not document:
            continue
        if _PATH.search(document) or _URL.search(document):
            continue           # already read, and read better, above
        if "://" in document or document.startswith(("http", "s://")):
            # A title that carries a web address, split across the dash
            # into something that is neither a name nor a URL.
            continue
        if len(document) < 3 or len(document) > 120:
            continue
        # The review sometimes writes a sentence about the listener's own
        # events into `sources`. "claude code assistant.session /
        # assistant.message events" describes where a state came from,
        # not a thing the user had open.
        if any(word in document for word in
               (".session", ".message", "focus_", "application.",
                " events", "window:")):
            continue
        keep(documents, document.lower())

    return {"files": files, "urls": urls, "documents": documents,
            "apps": apps}


def keys(workspace: Dict[str, Any]) -> List[str]:
    """The keys that identify work, most specific first.

    Applications are not among them, at any rarity. That is the whole
    rule: a browser is a way of opening a page and a terminal is a way of
    running a script, and neither says which page or which script.
    """
    out: List[str] = []
    for name in ("files", "urls", "documents"):
        for one in (workspace or {}).get(name) or []:
            if one not in out:
                out.append(str(one))
    return out


def ancestors(key: str) -> List[str]:
    """The places a key sits inside, widest last.

    A path gives its directories and a URL gives its host. These never
    anchor - `Research/AA/BB` and `Research/AA/CC` share `Research/AA` and
    may be one task with two parts or two unrelated papers, and nothing in
    the path says which. They are what PULLS two states together so that
    the splitter is asked the question at all.
    """
    if not key:
        return []
    if "/" not in key:
        return []
    parts = [one for one in key.split("/") if one]
    out = []
    # Drop the leaf: the key itself is not its own ancestor.
    for depth in range(len(parts) - 1, 0, -1):
        out.append("/".join(parts[:depth]))
    return out


def tools_in(seen: Iterable[Dict[str, Any]]) -> set:
    """Every name the calendar uses for an application, from the calendar.

    A bare window title cannot be told from a document by its shape:
    "Personalized Agent" is a repository page and "Mozilla Firefox" is a
    browser, and both are two capitalised words. What tells them apart is
    that one of them also turns up as the application half of other
    titles, and as an executable - so the corpus answers it, and there is
    no list to keep.
    """
    out = set()
    for one in seen:
        for name in (one or {}).get("apps") or []:
            said = str(name or "").strip().lower()
            if not said:
                continue
            out.add(said)
            if said.endswith(".exe"):
                out.add(said[:-4])
            # "Mozilla Firefox" is also "firefox" when it is an exe.
            for word in said.replace(".exe", "").split():
                if len(word) > 3:
                    out.add(word)
    return out


def identifying(workspace: Dict[str, Any], tools: set = frozenset()
                ) -> List[str]:
    """The keys of a state that name work, with the tools taken out.

    A state that names nothing comes back empty, and an empty one takes
    no part in anchoring: two states that both name nothing have nothing in
    common, and a rule that let them anchor would put everything the
    listener could not see into one task.
    """
    out = []
    for key in keys(workspace):
        said = str(key or "").strip().lower()
        if not said or said in out:
            continue
        if said in tools:
            continue
        # A document that is only an application's name, however spelled -
        # as two words or one ("Mozilla Firefox", "mozillafirefox"). Not
        # a name that only runs into an EXECUTABLE's when its spaces are
        # taken out: the agent's own program is PersonalizedAgent.exe, and
        # its page in a browser, "Personalized Agent", is a page.
        if "/" not in said and said.replace(" ", "") in {
                one.replace(" ", "") for one in tools
                if " " in one}:
            continue
        if said.split("/")[0] in A_WAY_THROUGH:
            continue
        out.append(said)
    return out


#: How common a key may be and still anchor, as a share of the window.
#:
#: A key on a fifth of the calendar is where the user's work lives rather
#: than what it is: `personalized_agent_release/` holds six months of
#: unrelated tasks. Below this a key is specific enough that two states
#: naming it are doing one thing.
#:
#: Note that RARE IS NOT THE SAME AS DISCRIMINATING, which is why this is
#: a second test and not the only one: a browser you use twice is rare and
#: means nothing, and is excluded by kind before it is ever counted.
ANCHORS_UNDER = 0.10


def anchor(workspaces: Sequence[Dict[str, Any]],
           anchors_under: float = ANCHORS_UNDER) -> Dict[str, Any]:
    """Join states that were open on the same thing.

    Union-find over an inverted index, which is the whole of it: no pairs
    are scored and no vectors are read. Two states anchor when they share
    a key that names work - a file, a page, a document - and that key is
    specific enough not to be the place all the work lives.

    A state that names nothing is its own anchor of one. That is the
    point: absence is not agreement, and the thing that made the earlier
    two-measure design a structural zero was letting two states with no
    evidence look identical to each other.

    Returns the anchor each state belongs to, what joined it, and the
    ancestors two anchors have in common - which do not anchor and are
    what later pulls them together.
    """
    tools = tools_in(workspaces)
    held = [identifying(one or {}, tools) for one in workspaces]
    how_many = len(held)

    counted: Dict[str, int] = {}
    for mine in held:
        for key in set(mine):
            counted[key] = counted.get(key, 0) + 1
    # A FLOOR as well as a share. The share is what matters at a
    # thousand states, where a tenth is a hundred and a key on more than
    # that is where the work lives rather than what it is. On a small
    # window it is nonsense: a tenth of twelve is one, so no key could be
    # shared by two states and anchor, and anchoring never fired at all.
    # A key naming five states or fewer is specific by any measure.
    # A FLOOR and a CAP, either side of the share.
    #
    # The share is the real rule and it is right at the size this runs
    # at: a task holds twenty or forty of a thousand states, so a key
    # naming one is on two to four per cent, and a key on a tenth is
    # where the work lives rather than what it is.
    #
    # The floor is for small windows, where a tenth rounds to one and no
    # key could ever be shared by two states and anchor. The cap is the
    # more important of the two: an anchored group is merged at distance
    # nought, which takes it out of the splitter's reach - so an anchor
    # of two hundred states would be one task nothing could divide. Forty
    # is the size of a large task.
    ceiling = max(8, min(40, int(how_many * float(anchors_under))))
    # A key on one state alone joins nothing, so it is not carried.
    joins = {key for key, n in counted.items() if 2 <= n <= ceiling}

    # NO TRANSITIVE CHAINING. The first version was union-find over any
    # shared key, and on four hundred real states it produced one anchor
    # of a hundred and forty-seven with NOTHING in common across it:
    # A shared a file with B, B shared a page with C, and three unrelated
    # months went into one task. That is single-linkage chaining, and an
    # anchor is supposed to be the part that is certain.
    #
    # So an anchor is the set of states that share ONE key, and a state
    # that names several joins the rarest of them - the rarest key it
    # names is the most specific thing it was open on, and so its best
    # identity. Every anchor therefore has a key, and no anchor holds two
    # states that were never open on the same thing.
    where: Dict[str, List[int]] = {}
    for index, mine in enumerate(held):
        mine_joining = [key for key in mine if key in joins]
        if not mine_joining:
            continue
        best = min(mine_joining, key=lambda key: (counted[key], key))
        where.setdefault(best, []).append(index)

    anchors = []
    taken = set()
    for key in sorted(where, key=lambda one: (counted[one], one)):
        members = [one for one in where[key] if one not in taken]
        if len(members) < 2:
            continue
        taken.update(members)
        anchors.append({"members": members, "on": [key],
                        "named": True, "of": len(members)})
    # And everything else stands alone, which is what a state that named
    # nothing must do: absence is not agreement.
    for index in range(how_many):
        if index not in taken:
            anchors.append({"members": [index],
                            "on": list(held[index][:1]),
                            "named": bool(held[index]), "of": 1})
    anchors.sort(key=lambda one: one["members"][0])

    return {
        "anchors": anchors,
        "of": {index: n for n, one in enumerate(anchors)
               for index in one["members"]},
        "keys": held,
        "joining": sorted(joins),
        "too_common": sorted(key for key, n in counted.items() if n > ceiling),
        "named": sum(1 for one in held if one),
        "states": how_many,
        "ceiling": ceiling,
    }


def pull(left: Sequence[str], right: Sequence[str]) -> float:
    """How strongly two states' workspaces draw together, 0 to 1.

    NOT a distance, and never pushes apart. A shared workspace is nearly
    conclusive that two states are one task; sharing none is almost no
    evidence that they are not, because one task spans a browser, an
    editor and a terminal. Only one of those two directions is worth
    acting on, so this returns something to subtract and nothing to add.

    Shared ancestors, not shared keys: the keys themselves have already
    anchored. `Research/AA/BB` and `Research/AA/CC` share `Research/AA`,
    which is the signature of one task with two parts - or of two papers
    on one shelf - and this is what makes the splitter ask which.
    """
    if not left or not right:
        return 0.0
    mine = {one: set(ancestors(one)) for one in left}
    yours = {one: set(ancestors(one)) for one in right}
    best = 0.0
    for a, a_up in mine.items():
        for b, b_up in yours.items():
            if a == b:
                return 1.0
            common = a_up & b_up
            if not common:
                continue
            # The deepest thing they share, against the deeper of the two
            # paths: sharing three levels of four is close, and sharing
            # one of five is a shelf.
            deepest = max(len(one.split("/")) for one in common)
            mine_deep = len(a.split("/"))
            yours_deep = len(b.split("/"))
            best = max(best, deepest / max(mine_deep, yours_deep, 1))
    return round(min(1.0, best), 4)


#: How rare a word must be to count as distinctive, as a share of the
#: window.
#:
#: The embedding smooths proper nouns away - it is trained to know that
#: "paper" and "manuscript" are alike, which is the opposite of what is
#: wanted for "FieldNotes" or "survey-weights". An exact word that
#: almost nothing else uses is evidence of a kind the vectors cannot
#: carry, and this is the threshold for "almost nothing else".
WORDS_UNDER = 0.05

#: How many rare words two states must share before they are one thing.
#:
#: THREE, and not one. One rare word in common is a coincidence - two
#: states mentioning "Lisbon" once each may be a trip and a restaurant -
#: and the anchor is meant to be the part that is certain. Three distinct
#: rare words is a subject.
WORDS_AGREE = 3


def anchor_by_words(said: Sequence[Sequence[str]],
                    words_under: float = WORDS_UNDER,
                    agree: int = WORDS_AGREE) -> Dict[str, Any]:
    """Join states that share several words almost nothing else uses.

    The second kind of anchor, and it catches what the first cannot: work
    with no file and no page. A conversation about a paper, a week of
    reading, an exchange with a reviewer - none of them name a path, and
    all of them say "FieldNotes" or "survey-weights" over and over.

    It is not what the embedding does. The embedding is trained to know
    that "paper" and "manuscript" mean the same, which is exactly wrong
    for a proper noun: the whole value of "FieldNotes" is that nothing
    else is called that, and a model that has smoothed it towards
    "notebook" has thrown away the one thing that identified it.

    AND EVERY ANCHOR MUST HAVE A COMMON CORE. Components are built from
    pairs, and a component whose members share nothing between them all
    is a chain - A with B, B with C, and A with C not at all - which is
    how the first version of the workspace anchoring put a hundred and
    forty-seven unrelated states in one group. A component with an empty
    intersection is refused outright rather than trimmed, because there
    is no honest way to choose which half of a chain was the real one.
    """
    how_many = len(said)
    counted: Dict[str, int] = {}
    for one in said:
        for word in set(one):
            counted[word] = counted.get(word, 0) + 1
    # The same shape as the workspace ceiling above, and for the same
    # reasons: a floor so a small window can anchor at all, a cap so an
    # anchor stays the size of a task rather than the size of a month.
    ceiling = max(6, min(40, int(how_many * float(words_under))))
    rare = [{word for word in set(one)
             if 2 <= counted.get(word, 0) <= ceiling} for one in said]

    where: Dict[str, List[int]] = {}
    for index, mine in enumerate(rare):
        for word in mine:
            where.setdefault(word, []).append(index)

    # Pairs that share enough, counted through the index rather than over
    # every pair: a word on three states makes three pairs, not n squared.
    shared: Dict[Tuple[int, int], int] = {}
    for members in where.values():
        if len(members) > max(4, ceiling):
            continue          # a word on that many states is not rare here
        for at, a in enumerate(members):
            for b in members[at + 1:]:
                shared[(a, b)] = shared.get((a, b), 0) + 1

    seat = list(range(how_many))

    def find(one: int) -> int:
        while seat[one] != one:
            seat[one] = seat[seat[one]]
            one = seat[one]
        return one

    edges = [pair for pair, n in shared.items() if n >= int(agree)]
    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            seat[max(ra, rb)] = min(ra, rb)

    groups: Dict[int, List[int]] = {}
    for index in range(how_many):
        groups.setdefault(find(index), []).append(index)

    anchors = []
    for members in sorted(groups.values(), key=lambda one: one[0]):
        if len(members) < 2:
            continue
        core = set(rare[members[0]])
        for one in members[1:]:
            core &= rare[one]
        if not core:
            # A chain. Refused whole: there is no honest way to pick
            # which of its links was the real one.
            continue
        anchors.append({"members": members,
                        "on": sorted(core)[:8],
                        "of": len(members)})
    return {
        "anchors": anchors,
        "of": {index: n for n, one in enumerate(anchors)
               for index in one["members"]},
        "rare": rare,
        "ceiling": ceiling,
        "edges": len(edges),
        "refused": sum(1 for members in groups.values() if len(members) > 1)
                   - len(anchors),
    }
