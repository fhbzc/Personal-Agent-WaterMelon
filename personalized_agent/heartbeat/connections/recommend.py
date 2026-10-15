"""CUSTOMIZED RECOMMENDATION - what the user has not seen and would learn the
most from, as a newspaper: a page for each thing they are doing.

  1. SUBJECTS  what they are doing now: each task or project something
               happened in within `ACTIVE_DAYS` (one switched off, or let
               go, has nothing new and drops out), and each thing they have
               been reading or watching - piano, restaurant reviews - from
               the reading log's topics: an INTEREST - read on two days, for
               ten minutes, or three different things read in it; a glance,
               or a problem looked up to be fixed, is not one. A topic that is part of a task's
               work (its centre near the task's) goes with the task.
  2. PAGES     at most `MOST_PAGES`: subjects alike share a page - the two
               nearest put together until there are few enough.
  3. SEARCH    each page on the public web (round.py) FROM ITS DETAIL - the
               states of its tasks, the titles, summaries and points of
               what was read for it - never from a heading alone: someone
               who watches restaurant reviews is not looking for food
               prices. Mostly CLOSE (bears directly on what they do or the
               very kind of thing they read), a few BROAD (a step to the
               side, still tied to it).
  4. CHECK     in code, not by the model: each story's RELEVANCE - its
               closeness to the page's own detail (its states, what was
               read for it). Below `BROAD_AT` it is not printed; from
               `CLOSE_AT` it is close. What they read already, or were
               shown, or that is the same story as either, is left out.
  5. SCORE     gain x relevance x freshness - not how long something was
               watched: a lot of one kind of video does not drown the rest.
  6. LAY OUT   each page mostly close, its broad ones among them; the front
               page the best of each page.

Only the user's own say counts for what is recommended next: more like
this, not interested, already knew, a story opened.
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..reading.topics import cosine, decayed

#: The pages of an issue, at most - one search each.
MOST_PAGES = 8
#: A task or project is followed while something of it happened this recently.
ACTIVE_DAYS = 14
#: What each subject brings to its page's search: its latest states, or the
#: pieces read for it.
MOST_DETAIL = 8
#: A page's detail, at most, as given to its search.
PAGE_DETAIL = 18
#: What the user knows of a page, given with its search.
KNOWN_POINTS = 10
#: Stories one page's search may bring.
PER_TOPIC = 6
#: Stories printed on a page; of them broad, at most - and never more than
#: half its close ones (one at least, when there is a close one).
PER_PAGE = 6
BROAD_PER_PAGE = 2
#: The front page: the best story of each page, at most this many.
FRONT = 6
#: Relevance - a story's closeness to the nearest thing the page is about
#: (measured on this machine's own model: stories the user found closely
#: related came out 0.45-0.58, food prices for restaurant reviews 0.30).
CLOSE_AT = 0.42
BROAD_AT = 0.35
#: A story this close to something already read is the same story.
SEEN_CLOSENESS = 0.78
#: And this close to one shown in a recent issue.
SHOWN_ALIKE = 0.85
#: A reading topic goes with a task when their centres are this close.
FOLD_AT = 0.5
#: AN INTEREST, NOT A GLANCE. A 12-second look at a product page, a 46-second
#: video, a 4-second one, each made a page of its own that the user never
#: wanted. A topic is an interest when what was read of it was read on
#: `INTEREST_DAYS` days and for a minute at least (`INTEREST_DAYS_SECONDS`),
#: or for `INTEREST_SECONDS` in all, or when `INTEREST_PIECES` different
#: things were read in it - an evening's papers, each open for less than a
#: minute between other windows, is an interest; one page is not. A piece
#: on screen less than `SEEN_SECONDS` (an address passed on the way) does
#: not count.
SEEN_SECONDS = 3
INTEREST_DAYS = 2
INTEREST_DAYS_SECONDS = 60
INTEREST_SECONDS = 600
INTEREST_PIECES = 3
#: What was looked up to get one thing done - how to fix an error, an
#: assistant's help with a problem (reading/log.py) - is not an interest:
#: the problem was the user's, the subject was not.
NOT_INTERESTS = ("known", "help")
#: A subject the user said "not interested" to this often - more often
#: than anything for it - is left out.
TURNED_AWAY = 2
#: How fast a story's freshness halves, in days.
FRESH_HALF_LIFE = 7.0
#: The longest an issue looks back.
MOST_DAYS_BACK = 7

#: The user's say on a story, for its topic. "Already knew" is FOR it: they
#: follow it closely enough to know - what counts against is what they knew,
#: kept as known (`record_feedback`), so what is like it is not news.
FOR = ("more", "opened", "knew")
AGAINST = ("less",)

TOPIC_SYSTEM = """You find, on the PUBLIC web, what one person would learn the most from -
things they have not seen. Only public pages: a web search, a page that
needs no sign-in. Never sign in, never post. Nothing invented: every story
says where it came from."""

PAGE_PLAN = """A PAGE OF THEIR NEWSPAPER. It is about:
{subjects}

WHAT THEY ACTUALLY DID, READ AND WATCHED FOR IT - the detail, newest first.
Your searches come from THIS, not from the headings above: the specific
names, works, people, places, methods, tools, the exact kind of thing they
read or watched:
{detail}

WHAT THEY ALREADY KNOW - do not recommend these again, or what only repeats
them:
{known}

SHOWN IN RECENT ISSUES - not again:
{shown}

THEY ASKED FOR MORE LIKE THESE - the kind of find they value here; look for
more in this direction (not the same stories again):
{liked}

THEY WERE NOT INTERESTED IN THESE - not this direction:
{disliked}

SINCE: {since}
TODAY: {today}

Search the web (web_search, fetch_page) - and again from what you find -
with queries made from the detail above, for what is NEW since {since} that
they have not seen:
- CLOSE - most of the page: what bears directly on what they are doing, or
  is the very kind of thing they read or watched (more of what they watch
  about piano: piano; restaurant reviews: restaurant reviews).
- BROAD - at most {broad}: a step to the side - the same field, a
  neighbouring question - still unmistakably tied to the detail above.
Nothing only loosely connected: food prices for someone who watches
restaurant reviews is WRONG; so is general AI-industry news for someone
whose work is one research question. Fewer stories, or none, rather than
loose ones.

For each, judge how much they would learn from it, given what they know:
gain 0 (they know it already) to 5 (it changes what they know).

Return ONLY this JSON object - at most {most} stories, an empty list when
nothing is worth it:
{{"heading": "the page's name, two to four words",
  "items": [{{"title": "short, what the user sees first",
             "detail": "one to three plain sentences: what it says",{english}
             "relation": "close or broad",
             "because": "which of the things above it follows from, in a few words",
             "new_to_you": "in one sentence, what in it is new to them",
             "gain": 3,
             "published": "YYYY-MM-DD, if the page says",
             "sources": ["the page addresses it comes from"],
             "image_url": "the address of the picture the page shows with it, if it has one"}}]}}"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _when(text: Any) -> Optional[datetime]:
    try:
        when = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def url_key(url: Any) -> str:
    from ..reading.log import url_key as key
    return key(url)


def said_of_topics(db: Any) -> Dict[str, Dict[str, int]]:
    """What the user said of each topic's - or page's - stories: for, against."""
    out: Dict[str, Dict[str, int]] = {}
    try:
        rows = db.query("SELECT topic_id, action, COUNT(*) AS n FROM rec_feedback "
                        "WHERE topic_id != '' GROUP BY topic_id, action")
    except Exception:
        return out
    for row in rows:
        mine = out.setdefault(row["topic_id"], {"for": 0, "against": 0})
        if row["action"] in FOR:
            mine["for"] += int(row["n"])
        elif row["action"] in AGAINST:
            mine["against"] += int(row["n"])
    return out


# -- 1. subjects ---------------------------------------------------------------------------------
def _loads(text: Any, default: Any) -> Any:
    try:
        value = json.loads(text) if text else default
    except (TypeError, ValueError):
        return default
    return value if value is not None else default


def _states_of(db: Any, members: List[str], most: int) -> List[Dict[str, Any]]:
    """A task's latest states - a few hundred ids at a time."""
    rows: List[Dict[str, Any]] = []
    for at in range(0, len(members), 400):
        part = members[at:at + 400]
        rows += [dict(row) for row in db.query(
            f"SELECT title, detail, starts_at FROM states WHERE id IN "
            f"({','.join('?' * len(part))})", tuple(part))]
    rows.sort(key=lambda row: str(row.get("starts_at") or ""), reverse=True)
    return rows[:most]


def _project_english(project: Dict[str, Any]) -> str:
    """A project, in English: its English copy where it has one."""
    said = _loads(project.get("described"), {})
    english = said.get("in_english") if isinstance(said, dict) and isinstance(
        said.get("in_english"), dict) else {}
    return (f"{english.get('name') or project['name']}. "
            f"{str(english.get('status') or project.get('why') or '')[:300]}")


def subjects(db: Any, now: Optional[datetime] = None,
             active_days: int = ACTIVE_DAYS) -> List[Dict[str, Any]]:
    """What the user is doing now (module docstring, 1): the tasks and
    projects with something in them lately - each with its detail - and the
    things read or watched lately, each with what was read."""
    now = now or _now()
    since = (now - timedelta(days=active_days)).isoformat()
    out: List[Dict[str, Any]] = []
    try:
        tasks = db.query("SELECT id, members, described FROM state_tasks WHERE scope = 'live' "
                         "AND COALESCE(retired_at, '') = '' AND COALESCE(archived_at, '') = '' "
                         "AND COALESCE(described, '') != ''")
        projects = db.query("SELECT id, name, why, tasks, described FROM state_projects "
                            "WHERE scope = 'live'")
    except Exception:                      # no state heartbeat tables yet
        tasks, projects = [], []
    project_of: Dict[str, Dict[str, Any]] = {}
    for row in projects:
        for task_id in _loads(row["tasks"], []):
            project_of.setdefault(str(task_id), dict(row))
    grouped: Dict[str, Dict[str, Any]] = {}
    for row in tasks:
        said = _loads(row["described"], {})
        # What a summary judged not to be a task - watching videos between
        # things, keeping up with chats - is not a page: what was watched
        # and read in it comes in as the reading log's topics.
        if not isinstance(said, dict) or not said.get("title") or said.get("concrete") is False:
            continue
        states = _states_of(db, [str(one) for one in _loads(row["members"], [])], MOST_DETAIL)
        last = str((states[0] if states else {}).get("starts_at") or "")
        if not last or last < since:
            continue
        try:
            weight = float(said.get("importance") or 0) + float(said.get("urgency") or 0)
        except (TypeError, ValueError):
            weight = 0.0
        steps = said.get("next_steps") or said.get("next") or []
        lines = [f"TASK: {said['title']} - {str(said.get('summary') or said.get('goal') or '')[:400]}"]
        if said.get("status"):
            lines.append(f"Where it stands: {str(said['status'])[:300]}")
        if isinstance(steps, list) and steps:
            lines.append("Next: " + "; ".join(str(one)[:120] for one in steps[:4]))
        lines += [f"{str(one.get('title') or '')[:160]}: {str(one.get('detail') or '')[:300]}"
                  for one in states]
        # WHAT IT IS MEASURED BY, in English: the task's English copy, where
        # the user does not read English (else what they read is English),
        # and its states - written in English. Not its next steps: the
        # user's own list, in their words.
        english = said.get("in_english") if isinstance(said.get("in_english"), dict) else {}
        measured = [f"{english.get('title') or said['title']}. "
                    f"{str(english.get('summary') or said.get('summary') or '')[:400]}"]
        if english.get("status") or said.get("status"):
            measured.append(str(english.get("status") or said.get("status"))[:300])
        measured += [f"{str(one.get('title') or '')[:160]}: {str(one.get('detail') or '')[:300]}"
                     for one in states]
        project = project_of.get(str(row["id"]))
        key = f"project:{project['id']}" if project else f"task:{row['id']}"
        mine = grouped.get(key)
        if mine is None:
            mine = grouped[key] = {
                "key": key, "kind": "work", "weight": weight, "last_at": last, "detail": [],
                "measured": [],
                "title": str(project["name"]) if project else str(said["title"]),
                "text": _project_english(project) if project else measured[0],
                "tasks": []}
        mine["tasks"].append(str(said["title"]))
        mine["detail"] += lines
        mine["measured"] += measured
        mine["weight"] = max(mine["weight"], weight)
        mine["last_at"] = max(mine["last_at"], last)
    # The weightiest first; of equal weight, the latest.
    work = sorted(grouped.values(), key=lambda one: one["last_at"], reverse=True)
    out += sorted(work, key=lambda one: -one["weight"])
    # THE THINGS READ AND WATCHED LATELY: each topic, from what was read in it.
    try:
        topics = db.query("SELECT id, label, centre FROM reading_topics")
    except Exception:
        topics = []
    for topic in topics:
        pieces = [dict(row) for row in db.query(
            "SELECT title, summary, points, engagement, last_at, vector, seconds, kind "
            "FROM reading_pieces WHERE topic_id = ? AND last_at >= ? AND seconds >= ?",
            (topic["id"], since, SEEN_SECONDS)) if row["kind"] not in NOT_INTERESTS]
        if not interest(pieces):
            continue
        pieces.sort(key=lambda row: -decayed(row["engagement"], row["last_at"], now))
        lines = []
        for row in pieces[:MOST_DETAIL]:
            points = [str(one) for one in _loads(row["points"], [])[:3]]
            lines.append(f"READ/WATCHED: {str(row['title'] or '')[:160]} - "
                         f"{str(row['summary'] or '')[:300]}"
                         + (f" ({'; '.join(points)})" if points else ""))
        out.append({"key": f"topic:{topic['id']}", "kind": "interest", "topic_id": topic["id"],
                    "title": str(topic["label"]), "detail": lines,
                    "weight": round(sum(decayed(row["engagement"], row["last_at"], now)
                                        for row in pieces), 4),
                    "last_at": max(str(row["last_at"]) for row in pieces),
                    "vector": _loads(topic["centre"], []),
                    "anchors": [_loads(row["vector"], []) for row in pieces
                                if row.get("vector")][:MOST_DETAIL * 2]})
    return out


def interest(pieces: Sequence[Dict[str, Any]]) -> bool:
    """Whether what was read of a topic makes it an interest (`INTEREST_DAYS`
    and the rest, above)."""
    days = {str(one.get("last_at") or "")[:10] for one in pieces}
    seconds = sum(float(one.get("seconds") or 0) for one in pieces)
    return bool(pieces) and ((len(days) >= INTEREST_DAYS and seconds >= INTEREST_DAYS_SECONDS)
                             or seconds >= INTEREST_SECONDS
                             or len(pieces) >= INTEREST_PIECES)


# -- 2. pages ------------------------------------------------------------------------------------
def _mean(vectors: Sequence[Sequence[float]]) -> List[float]:
    vectors = [one for one in vectors if one]
    if not vectors:
        return []
    size = len(vectors[0])
    return [sum(one[i] for one in vectors if len(one) == size) / len(vectors)
            for i in range(size)]


def plan_pages(found: List[Dict[str, Any]], embed: Optional[Callable[[List[str]], List]],
               said: Optional[Dict[str, Dict[str, int]]] = None,
               most: int = MOST_PAGES) -> List[Dict[str, Any]]:
    """The pages (module docstring, 2): each subject's vector; a topic that
    is part of a task's work put with it; one turned away left out; then
    the two nearest put together until there are `most`. Each page with
    its detail and its ANCHORS - the vectors a story is measured against."""
    said = said or {}
    subjects_ = []
    for one in found:
        mine = said.get(one.get("topic_id") or one["key"]) or {}
        if mine.get("against", 0) >= TURNED_AWAY and mine["against"] > mine.get("for", 0):
            continue
        subjects_.append(dict(one, anchors=list(one.get("anchors") or [])))
    wanting = [one for one in subjects_ if not one.get("vector")]
    if wanting and embed is not None:
        try:
            # Measured in English: the English copy, and the states.
            texts = [one.get("text") or one["title"] for one in wanting]
            details = [line for one in wanting for line in one.get("measured") or []]
            vectors = embed(texts + details)
        except Exception:
            vectors = []
        if vectors:
            for one, vector in zip(wanting, vectors[:len(wanting)]):
                one["vector"] = vector
            at = len(wanting)
            for one in wanting:
                count = len(one.get("measured") or [])
                one["anchors"] += vectors[at:at + count]
                at += count
    for one in subjects_:
        if one.get("vector"):
            one["anchors"].append(one["vector"])
    # A topic that is the work of a task goes with the task.
    work = [one for one in subjects_ if one["kind"] == "work" and one.get("vector")]
    kept = []
    for one in subjects_:
        if one["kind"] == "interest" and one.get("vector") and work:
            near = max(work, key=lambda task: cosine(one["vector"], task["vector"]))
            if cosine(one["vector"], near["vector"]) >= FOLD_AT:
                near["detail"] = near["detail"] + one["detail"][:MOST_DETAIL // 2]
                near["anchors"] = near["anchors"] + one["anchors"]
                near.setdefault("topics", []).append(one["topic_id"])
                continue
        kept.append(one)
    pages = [{"subjects": [one], "vector": one.get("vector") or []} for one in kept]
    # The two nearest together, until few enough - alike kinds first.
    while len(pages) > most:
        best, pair = -9.0, (0, 1)
        for i in range(len(pages)):
            for j in range(i + 1, len(pages)):
                closeness = cosine(pages[i]["vector"], pages[j]["vector"])
                if pages[i]["subjects"][0]["kind"] == pages[j]["subjects"][0]["kind"]:
                    closeness += 0.1
                if closeness > best:
                    best, pair = closeness, (i, j)
        i, j = pair
        pages[i]["subjects"] += pages[j]["subjects"]
        pages[i]["vector"] = _mean([one.get("vector") or [] for one in pages[i]["subjects"]])
        pages.pop(j)
    out = []
    for page in pages:
        members = page["subjects"]
        detail: List[str] = []
        # Each subject's detail in turn, so a shared page is not all one.
        for at in range(max(len(one["detail"]) for one in members)):
            for one in members:
                if at < len(one["detail"]) and len(detail) < PAGE_DETAIL:
                    detail.append(one["detail"][at])
        out.append({"key": members[0]["key"] if len(members) == 1
                    # Whole: what a story came from can be traced to each part.
                    else "page:" + "+".join(sorted(one["key"] for one in members)),
                    "title": " · ".join(one["title"] for one in members[:2]),
                    "kind": members[0]["kind"],
                    "subjects": [{"key": one["key"], "title": one["title"], "kind": one["kind"],
                                  "topic_id": one.get("topic_id", ""),
                                  "topics": one.get("topics", [])} for one in members],
                    "detail": detail,
                    "topic_ids": [tid for one in members for tid in
                                  ([one["topic_id"]] if one.get("topic_id") else [])
                                  + list(one.get("topics") or [])],
                    "anchors": [vector for one in members for vector in one["anchors"]
                                if vector]})
    return out


# -- what is known, and what was seen -----------------------------------------------------------
def known_points(db: Any, topic_id: str, most: int = KNOWN_POINTS) -> List[str]:
    """What the user read of a topic, the weightiest now first: its points."""
    rows = [dict(row) for row in db.query(
        "SELECT points, summary, engagement, last_at FROM reading_pieces WHERE topic_id = ?",
        (topic_id,))]
    rows.sort(key=lambda row: -decayed(row["engagement"], row["last_at"]))
    out: List[str] = []
    for row in rows:
        points = json.loads(row["points"] or "[]") or ([row["summary"]] if row["summary"]
                                                      else [])
        for point in points:
            if point and point not in out:
                out.append(str(point))
        if len(out) >= most:
            break
    return out[:most]


def seen_addresses(db: Any, briefings: List[Dict[str, Any]]) -> set:
    """Every address the user read, or was shown in an issue before."""
    seen = {row["url_key"] for row in db.query(
        "SELECT url_key FROM reading_pieces WHERE url_key != ''")}
    for issue in briefings:
        for item in issue.get("items") or []:
            for source in item.get("sources") or []:
                seen.add(url_key(source))
    return seen


def freshness(published: Any, now: Optional[datetime] = None) -> float:
    when = _when(published)
    if when is None:
        return 1.0
    days = max(0.0, ((now or _now()) - when).total_seconds() / 86400.0)
    return 0.5 ** (days / FRESH_HALF_LIFE)


# -- 4-6. check, score, lay out ------------------------------------------------------------------
def story_text(story: Dict[str, Any]) -> str:
    """What a story is measured by: its English, said for the agent's own use
    - the title and detail are in the user's language, and the model that
    measures reads English."""
    return str(story.get("english") or "").strip() or \
        f"{story.get('title') or ''}. {story.get('detail') or ''}"


#: Each story's English, asked for only when the user does not read English
#: (languages.needs_english_copy): what it is measured by.
STORY_ENGLISH = ('\n             "english": "the story in one or two plain English '
                 'sentences, for the agent\'s own use - LEFT OUT when the title and detail '
                 'are in English already: each word you write is paid for",')


#: A story this near one the user asked for more of, or was not interested
#: in, is like it: worth more, or less.
LIKE_IT = 0.6
MORE_LIKE_IT, LESS_LIKE_IT = 1.25, 0.6


def lay_out(stories: List[Dict[str, Any]], pages: List[Dict[str, Any]], embedder: Any,
            read_vectors: List[List[float]], shown_vectors: List[List[float]],
            seen: set, liked: Optional[List[List[float]]] = None,
            disliked: Optional[List[List[float]]] = None
            ) -> "tuple[List[Dict[str, Any]], Dict[str, Any]]":
    """Each story checked, scored and placed (module docstring, 4-6): what
    was read or shown, or is the same story as either, left out; each put on
    the page it is nearest - its own page when it is near enough to it - and
    left out when it is near none; close or broad by how near. Then each
    page: its close ones, its broad ones among them; the front page the
    best of each. The stories printed, and the paper.

    NEWS OF SOMEONE THEY KNOW is information they want whatever they are
    working on: near a page, on it; near none, on a page of its own -
    never left out for being far from their work. And with no page at all
    (nothing done or read lately), nothing to measure against: what was
    found, on one page."""
    if not stories:
        return [], {"front": [], "brief": [], "pages": []}
    pages = list(pages) or [{"key": "general", "title": "What you need to know",
                             "anchors": [], "topic_ids": []}]
    try:
        vectors = embedder.embed([story_text(one) for one in stories]) \
            if embedder is not None else []
    except Exception:
        vectors = []
    measurable = any(page["anchors"] for page in pages)
    people = {"key": "people", "title": "People you know", "anchors": [], "topic_ids": []}
    by_key = {page["key"]: page for page in pages}
    placed: Dict[str, List[Dict[str, Any]]] = {page["key"]: [] for page in pages}
    for n, one in enumerate(stories):
        if any(url_key(source) in seen for source in one.get("sources") or []):
            continue
        vector = vectors[n] if n < len(vectors) else []
        if vector and max((cosine(vector, read) for read in read_vectors), default=0.0) \
                >= SEEN_CLOSENESS:
            continue
        if vector and max((cosine(vector, shown) for shown in shown_vectors), default=0.0) \
                >= SHOWN_ALIKE:
            continue

        def near(page: Dict[str, Any]) -> float:
            return max((cosine(vector, anchor) for anchor in page["anchors"]), default=0.0)

        if vector and measurable:
            own = by_key.get(one.get("page") or "")
            page = own if own is not None and near(own) >= BROAD_AT else \
                max(pages, key=near)
            relevance = near(page)
        else:
            # Nothing to measure with, or against: kept where it was found
            # for (the first page, else), as broad - never put before what
            # could be measured.
            page = by_key.get(one.get("page") or "") or pages[0]
            relevance = BROAD_AT
        # Not measured: not called broad either - it would be cut to the
        # few broad ones a page takes for no reason it has.
        unmeasured = not (vector and measurable)
        if relevance < BROAD_AT:
            if not (one.get("kind") == "person" or one.get("people")):
                continue
            # News of someone they know, near none of their pages.
            if "people" not in by_key:
                pages.append(people)
                by_key["people"] = people
                placed["people"] = []
            page, relevance = people, CLOSE_AT
        relation = "close" if relevance >= CLOSE_AT or unmeasured else "broad"
        try:
            gain = float(one.get("gain")) if one.get("gain") is not None \
                else float(one.get("importance") or 3)
        except (TypeError, ValueError):
            gain = 3.0
        gain = max(0.0, min(5.0, gain))
        score = (gain / 5.0) * relevance * freshness(one.get("published"))
        # Like what they asked for more of: worth more; like what they were
        # not interested in: less.
        if vector and liked and max(cosine(vector, other) for other in liked) >= LIKE_IT:
            score *= MORE_LIKE_IT
        if vector and disliked and \
                max(cosine(vector, other) for other in disliked) >= LIKE_IT:
            score *= LESS_LIKE_IT
        placed[page["key"]].append(dict(one, page=page["key"], relation=relation,
                                        relevance=round(relevance, 3), score=round(score, 4),
                                        topic_id=one.get("topic_id") or (
                                            page["topic_ids"][0] if page.get("topic_ids")
                                            else page["key"])))
    printed: List[Dict[str, Any]] = []
    laid: List[Dict[str, Any]] = []
    for page in pages:
        mine = sorted(placed[page["key"]], key=lambda one: -one["score"])
        close = [one for one in mine if one["relation"] == "close"][:PER_PAGE]
        broad_room = min(BROAD_PER_PAGE, max(1, len(close) // 2), PER_PAGE - len(close)) \
            if close else min(BROAD_PER_PAGE, PER_PAGE)
        broad = [one for one in mine if one["relation"] == "broad"][:max(0, broad_room)]
        # The broad ones among the close: one after every two.
        order: List[Dict[str, Any]] = []
        left = list(broad)
        for at, one in enumerate(close, 1):
            order.append(one)
            if at % 2 == 0 and left:
                order.append(left.pop(0))
        order += left
        if not order:
            continue
        printed += order
        laid.append({"key": page["key"], "title": page.get("heading") or page["title"],
                     "stories": [one["id"] for one in order],
                     "best": order[0]["score"] if order else 0.0})
    laid.sort(key=lambda page: -page["best"])
    for page in laid:
        page.pop("best", None)
    by_id = {one["id"]: one for one in printed}
    tops = sorted((by_id[page["stories"][0]] for page in laid), key=lambda one: -one["score"])
    front = [one["id"] for one in tops[:FRONT]]
    seconds = sorted((by_id[page["stories"][1]] for page in laid if len(page["stories"]) > 1),
                     key=lambda one: -one["score"])
    brief = [one["id"] for one in seconds if one["id"] not in front][:FRONT]
    return printed, {"front": front, "brief": brief, "pages": laid}


def said_examples(db: Any, store: Any, days: int = 90, most: int = 60
                  ) -> List[Dict[str, Any]]:
    """The stories the user said "more like this" or "not interested" to,
    lately - each with its English: examples of what they value, and of what
    they do not."""
    since = (_now() - timedelta(days=days)).isoformat()
    try:
        rows = db.query("SELECT issue_id, story_id, action FROM rec_feedback WHERE "
                        "action IN ('more', 'less') AND at >= ? ORDER BY at DESC LIMIT ?",
                        (since, most))
    except Exception:
        return []
    issues: Dict[str, Dict[str, Any]] = {}
    out = []
    for row in rows:
        issue = issues.setdefault(row["issue_id"], store.briefing(row["issue_id"]) or {})
        story = next((one for one in issue.get("items") or []
                      if one.get("id") == row["story_id"]), None)
        if story is None:
            continue
        out.append({"action": row["action"], "title": str(story.get("title") or ""),
                    "english": story_text(story)})
    return out


def since_of(last_issue_at: Any, now: Optional[datetime] = None) -> datetime:
    """How far an issue looks back: to the last one - a day at least, a
    week at most."""
    now = now or _now()
    last = _when(last_issue_at)
    if last is None:
        return now - timedelta(days=1)
    return max(now - timedelta(days=MOST_DAYS_BACK), min(last, now - timedelta(days=1)))


# -- the user's say ---------------------------------------------------------------------------------
ACTIONS = ("more", "less", "knew", "opened")
#: The user's PREFERENCE on a story - one at a time: changed, the one before
#: is taken back (what it added to its topic, and a story kept as known).
PREFERENCES = ("more", "less", "knew")


def says_of(db: Any, issue_id: str) -> Dict[str, str]:
    """Each story's preference in an issue, as the user last said it."""
    try:
        rows = db.query("SELECT story_id, action FROM rec_feedback WHERE issue_id = ? AND "
                        "action IN ('more', 'less', 'knew')", (str(issue_id),))
    except Exception:
        return {}
    return {row["story_id"]: row["action"] for row in rows}


def record_feedback(db: Any, store: Any, issue_id: str, story_id: str,
                    action: str, embedder: Any = None) -> Dict[str, Any]:
    """The user's say on a story (module docstring): kept for its topic. What
    they knew already counts for the topic, and is kept as KNOWN - with its
    vector, so a story too like it is left out (`lay_out`); and its points among what they know, given with
    the topic's search. A story is opened once."""
    from ...common.events.models import new_id
    if action not in ACTIONS:
        raise ValueError(f"action is one of {', '.join(ACTIONS)}")
    issue = store.briefing(issue_id)
    story = next((one for one in (issue or {}).get("items") or []
                  if one.get("id") == story_id), None)
    if story is None:
        raise KeyError("no such story")
    at = _now().isoformat()
    if action == "opened" and db.query_one("SELECT 1 FROM rec_feedback WHERE story_id = ? "
                                           "AND action = 'opened'", (story_id,)):
        return {"ok": True, "again": True}
    if action in PREFERENCES:
        # ONE PREFERENCE A STORY: the same again changes nothing; another
        # takes back the one before - its count for the topic, and what was
        # kept as known for it.
        before = db.query_one("SELECT id, action FROM rec_feedback WHERE story_id = ? AND "
                              "action IN ('more', 'less', 'knew')", (story_id,))
        if before is not None and before["action"] == action:
            return {"ok": True, "say": action, "unchanged": True}
        if before is not None:
            db.execute("DELETE FROM rec_feedback WHERE id = ?", (before["id"],))
            if before["action"] == "knew":
                db.execute("DELETE FROM reading_pieces WHERE id = ?", (f"read_known_{story_id}",))
    db.execute("INSERT INTO rec_feedback (id, at, issue_id, story_id, topic_id, action, title, "
               "url) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
               (new_id("recfb"), at, issue_id, story_id, str(story.get("topic_id") or ""),
                action, str(story.get("title") or "")[:300],
                str((story.get("sources") or [""])[0])[:1000]))
    if action == "knew":
        url = str((story.get("sources") or [""])[0])
        text = story_text(story)
        vector = None
        if embedder is not None:
            try:
                vector = json.dumps([round(float(x), 5) for x in embedder.embed([text])[0]])
            except Exception:                        # known by its words, then
                vector = None
        db.execute(
            "INSERT INTO reading_pieces (id, first_at, last_at, url, url_key, title, title_key, "
            "app, kind, topics, entities, points, summary, seconds, visits, characters, fetched, "
            "engagement, topic_id, vector, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, "
            "'', 'known', '[]', '[]', ?, ?, 0, 1, 0, 0, 0.3, ?, ?, ?, ?)",
            (f"read_known_{story_id}", at, at, url[:1000], url_key(url) if url else "",
             str(story.get("title") or "")[:300], "", json.dumps(
                 [str(story.get("detail") or story.get("title") or "")[:400]]),
             str(story.get("title") or "")[:400], str(story.get("topic_id") or ""),
             vector, at, at))
    return {"ok": True, "say": action if action in PREFERENCES else ""}


__all__ = ["record_feedback", "ACTIONS", "subjects", "plan_pages", "lay_out", "story_text",
           "said_examples",
           "known_points", "since_of", "said_of_topics", "seen_addresses", "freshness",
           "PAGE_PLAN", "TOPIC_SYSTEM", "FOR", "AGAINST", "MOST_PAGES", "CLOSE_AT",
           "BROAD_AT"]
