"""What a page was about, worked out from what was read on it.

The Listener reads the text of the window in front - a page, a chat, a
mail - and the overview showed none of it: an address and a title, which
say where somebody was and nothing of what they were doing there. This
turns the text into something a person can scan: a handful of keywords, a
line of the page that carries them, and the opening of the text itself.

No model is asked. It is TF-IDF across everything read in the same window
of time: a word scores high when it is frequent on THIS page and rare
across the others. That is what removes the furniture without a list of
it - "Search", "Sign in", "Settings" and the rest of a site's chrome are
on every page, so they are on no page in particular.

Chinese, Japanese and Korean have no spaces to split on, so a run of
those characters is cut into overlapping pairs, which is how they are
searched elsewhere in the agent too: a pair is short enough to recur and
long enough to mean something.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ...common.titles import normalise_title
from ...data_manage.text import COMMON_WORDS

#: How many keywords a page is described by.
KEYWORDS = 6
#: How much of what was read is offered to expand.
EXCERPT = 1000
#: The longest a describing line is allowed to run before it is cut.
LINE = 160
#: The most of one page weighed for its keywords. A terminal left open all
#: day holds a hundred and thirty thousand characters, and the keywords of
#: its opening twenty thousand are its keywords: past that the cost grows
#: and the answer does not change.
MOST_WEIGHED = 20000

#: Words that carry grammar rather than subject. The agent's shared list
#: is tuned for search, which keeps more; a keyword has to say what a page
#: was about, and "about", "before" and "would" never do.
_FUNCTION_WORDS = frozenset("""
about above after again against also among another any anyone anything are
around asks back because been before being below between both but came can
cannot could did does doing done down during each either else even ever
every few first from further get gets getting give given goes going gone got
had has have having her here hers herself him himself his how however into
its itself just last later least less let like made make makes many may
might more most much must near need needs never new next nor not now off
often once one only onto other others our ours out over own per please put
quite rather really said same say says see seen shall she should show shown
since some still such sure take than that the their them then there these
they thing things this those though through thus too took under until upon
use used uses using very want was way ways well went were what when where
whether which while who whom whose why will with within without would yes
yet you your yours yourself
""".split())

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9'\-]{2,}")
_WIDE = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]+")
_LINES = re.compile(r"[\r\n]+|(?<=[.!?。！？])\s+")


def terms(text: str) -> List[str]:
    """The words of a text worth counting, and the pairs of a wide script.

    Lower-cased; common words, numbers and anything very long (a hash, a
    token, a path run together) are left out.
    """
    out: List[str] = []
    for word in _WORD.findall(str(text or "")):
        lowered = word.lower().strip("'-")
        if (len(lowered) < 3 or len(lowered) > 30
                or lowered in COMMON_WORDS or lowered in _FUNCTION_WORDS
                or lowered.isdigit()):
            continue
        out.append(lowered)
    for run in _WIDE.findall(str(text or "")):
        if len(run) == 1:
            continue
        out.extend(run[index:index + 2] for index in range(len(run) - 1))
    return out


class Corpus:
    """Pages read in one window of time, weighed against one another."""

    def __init__(self) -> None:
        self._texts: Dict[str, List[str]] = {}

    def add(self, key: str, text: str) -> None:
        if not key or not str(text or "").strip():
            return
        self._texts.setdefault(key, []).append(str(text))

    def __contains__(self, key: str) -> bool:
        return key in self._texts

    def about(self) -> Dict[str, Dict[str, object]]:
        """Each page's keywords, its describing line, and its opening."""
        joined = {key: "\n".join(parts) for key, parts in self._texts.items()}
        counted = {key: Counter(terms(text[:MOST_WEIGHED]))
                   for key, text in joined.items()}
        pages = max(1, len(counted))
        seen_on: Counter = Counter()
        for counts in counted.values():
            seen_on.update(counts.keys())
        out: Dict[str, Dict[str, object]] = {}
        for key, counts in counted.items():
            total = max(1, sum(counts.values()))
            scored = {
                term: (count / total)
                * (math.log((pages + 1) / (seen_on[term] + 1)) + 1.0)
                for term, count in counts.items()}
            # A PAIR HAS TO RECUR. Cutting a run of characters into pairs
            # makes one at every boundary, and a pair seen once is usually
            # an accident of where the cut fell - 和水, 在市 - rather than a
            # word. One that comes back is one the page is using.
            # AND A KEYWORD IS NOT ON MOST OF THE PAGES. Weighing pushes the
            # furniture down, but a page with little of its own still gets
            # its six from somewhere, and the somewhere was "help", "search"
            # and "sign". Fewer keywords that mean something are better than
            # six that do not.
            common = pages >= 3
            eligible = {term: score for term, score in scored.items()
                        if (not _WIDE.fullmatch(term) or counts[term] >= 2)
                        and not (common and seen_on[term] * 2 > pages)}
            top = sorted(eligible.items(), key=lambda one: (-one[1], one[0]))
            keywords = _joined_pairs([term for term, _ in top[:KEYWORDS * 2]],
                                     joined[key])[:KEYWORDS]
            out[key] = {
                "keywords": keywords,
                "description": _describing_line(joined[key][:MOST_WEIGHED],
                                                scored),
                "excerpt": joined[key][:EXCERPT],
                "characters": len(joined[key]),
            }
        return out


def _joined_pairs(ranked: List[str], text: str) -> List[str]:
    """Overlapping wide-script pairs put back into the word they came from.

    Cut into pairs, 国际象棋 is 国际, 际象 and 象棋 - three keywords for one
    word, one of them meaningless. Any two that overlap by a character and
    appear joined in the page are joined, again and again until nothing
    more joins, and the joined word keeps the rank of its best part.
    """
    out = list(dict.fromkeys(ranked))
    joined = True
    while joined:
        joined = False
        for first in range(len(out)):
            for second in range(len(out)):
                a, b = out[first], out[second]
                if (first == second or not _WIDE.fullmatch(a)
                        or not _WIDE.fullmatch(b) or a[-1] != b[0]):
                    continue
                word = a + b[1:]
                if word not in text:
                    continue
                keep, drop = min(first, second), max(first, second)
                out[keep] = word
                del out[drop]
                joined = True
                break
            if joined:
                break
    # A word swallowed into a longer one already listed is not listed twice.
    return [term for term in out
            if not any(term != other and term in other for other in out)]


def _describing_line(text: str, scored: Dict[str, float]) -> str:
    """The line of the page that carries the most of what it is about.

    Scored by the weight of its terms over the square root of how many it
    has, so a long line is not preferred merely for being long and a menu
    item of one keyword is not preferred for being short.
    """
    best, best_score = "", 0.0
    for line in _LINES.split(str(text or "")):
        line = " ".join(line.split())
        if len(line) < 12:
            continue
        found = terms(line)
        if len(found) < 2:
            continue
        score = sum(scored.get(term, 0.0) for term in found) / math.sqrt(len(found))
        if score > best_score:
            best, best_score = line, score
    if len(best) > LINE:
        best = best[:LINE - 1].rstrip() + "…"
    return best


#: How a browser signs its windows: "Page title - Google Chrome".
#: Only a browser's own profile names are taken off with it ("Notes -
#: Personal - Microsoft Edge"); a site's name before the browser's is part
#: of the page's title and is kept ("Inbox - Gmail - Google Chrome").
_BROWSER_SUFFIX = re.compile(
    r"\s+[-–—]\s+(?:(?:Personal|Work|Guest|InPrivate|Incognito"
    r"|Profile\s*\d+)\s+[-–—]\s+)?"
    r"(?:Google Chrome|Chromium|Mozilla Firefox|Firefox|Microsoft​?\s*Edge"
    r"|Brave|Opera|Vivaldi|Arc|Safari)\s*$", re.IGNORECASE)
#: An unread count, which changes while the page stays the same page.
_COUNT = re.compile(r"\(\d+\+?\)")


def page_key(title: str) -> str:
    """A window or page title, reduced to what both forms of it share.

    A browser window is titled with the page's title and the browser's name
    after it, and the browser's own history has the page's title alone; this
    is the part the two have in common, so a page and what was read on it
    can be matched.
    """
    # The decoration a program animates at the front of its title - a
    # spinner, a star - changes while the window does not.
    text = _COUNT.sub("", _BROWSER_SUFFIX.sub("", normalise_title(title or "")))
    return " ".join(text.lower().split())


def pairs_of(items: Iterable[Tuple[str, str]]) -> Corpus:
    """A corpus from (key, text) pairs."""
    corpus = Corpus()
    for key, text in items:
        corpus.add(key, text)
    return corpus


#: A window read again within this long is the same stretch of it.
STRETCH_GAP_SECONDS = 10 * 60
#: And a stretch is cut after this long even if it never paused, so a
#: terminal in front all afternoon is several stretches, not one.
STRETCH_LONGEST_SECONDS = 30 * 60
#: How far from anything read in a window a moment can be and still be
#: given that window's keywords. Past this, what the window said then is
#: not known, and an old answer would be passed off as a current one.
NEAREST_SECONDS = 2 * 3600


def _moment(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        when = value
    else:
        try:
            when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


class Moments:
    """What each window was about AT EACH STRETCH OF TIME, not all day.

    Keywords were filed by title alone, so a window whose title does not
    change - a terminal named after its session, WeChat's main window, an
    editor - was one "page" holding a whole day of different things, and
    every event in it got the same six words: on one machine 389 readings
    of a terminal over two days, half a million characters, one set of
    keywords for all of it, stamped on a window switch at 21:56 whatever
    was on the screen at 21:56.

    The readings of each window are now cut into stretches - read again
    within ten minutes, and no longer than half an hour - and each
    stretch is a page of its own, weighed against every other stretch
    of the day, this window's included. A moment is given the stretch
    it falls in, or failing that the nearest one within two hours.
    """

    def __init__(self, reads: Iterable[Tuple[str, Any, str]]) -> None:
        by_key: Dict[str, List[Tuple[datetime, str]]] = defaultdict(list)
        for key, when, text in reads:
            moment = _moment(when)
            if key and moment is not None and str(text or "").strip():
                by_key[key].append((moment, str(text)))
        corpus = Corpus()
        self._spans: Dict[str, List[list]] = defaultdict(list)
        for key, items in by_key.items():
            items.sort(key=lambda one: one[0])
            for moment, text in items:
                spans = self._spans[key]
                last = spans[-1] if spans else None
                if (last is None
                        or (moment - last[1]).total_seconds() > STRETCH_GAP_SECONDS
                        or (moment - last[0]).total_seconds() > STRETCH_LONGEST_SECONDS):
                    last = [moment, moment, f"{key}|stretch {len(spans)}"]
                    spans.append(last)
                last[1] = moment
                corpus.add(last[2], text)
        self._about = corpus.about()

    def __contains__(self, key: str) -> bool:
        return key in self._spans

    def near(self, key: str, when: Any = None) -> Optional[Dict[str, object]]:
        """The keywords of `key` at `when` - or its latest, without one."""
        spans = self._spans.get(key)
        if not spans:
            return None
        moment = _moment(when) if when is not None else None
        if moment is None:
            return self._about.get(spans[-1][2])

        def distance(span: list) -> float:
            if span[0] <= moment <= span[1]:
                return 0.0
            return min(abs((moment - span[0]).total_seconds()),
                       abs((moment - span[1]).total_seconds()))

        best = min(spans, key=distance)
        if distance(best) > NEAREST_SECONDS:
            return None
        found = self._about.get(best[2])
        if found is None:
            return None
        return dict(found, read_from=best[0].isoformat(),
                    read_until=best[1].isoformat())

