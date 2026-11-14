"""EACH THING ON THE PLATE, JUDGED (the user, 2026-11-09: "哪些它能做的，自己做，让我审阅。哪些必须要我做的，
进行task triagging") - only those new or changed since they were last judged, in one call for up to
MOST_AT_ONCE of them; each with what was found about it (related.py: the user's activity, the chat,
the people who may be meant, by the agent's own search words) and what was learned of the user.

For each the model says, every judgment resting on what it was shown:
  who       THREE KINDS (the user, 2026-11-09: "之后我会让agent 全盘接管比如send email这种，让人尽可能
            做deep work。所以我觉得要分3类 ... 这三个分类也可以interative 调整的"):
            delegable - the agent does it; the user approves it now, and may hand it over for good
            approve   - the agent does it; the user approves it now and always
            human     - the user's own work, their deep work; the agent helps where it can
            - and gone (done already, or no longer wanted)
  kind      the kind of work it is, in a few words that recur ("reply to a scheduling email"):
            what the user sets for a kind (agenda_modes) holds for every thing of that kind
  deadline  when it must be done by, on the user's clock - "hard" only when a line shows it; with why
  minutes   how long it takes the user (for agent work: their part, looking it over)
  focus     deep (long thought) / shallow (quick, small) / meeting / review / errand (somewhere else)
  context   what it belongs with - a project, a person - for keeping like things together
  place     where it must be done, when it is somewhere
Until a kind is handed over, nothing goes out without the user's yes - in phase 1 nothing is done.

WHEN IT COMES TO DOING IT (phase 3, the user, 2026-11-09: "agent在执行的时候不确定是否要执行的（比如信息
不足）也变成给人审批"): a delegable thing - even of a kind handed over for good - that the agent, as it
does it, is not sure it should do (what it needs is not there, things are not as they were when it
was sorted) is not done: it is put to the user to approve, saying what it is unsure of.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

#: A call's answer is cut off past what it may write: 25 at once, the last 17 of the second call's
#: were lost (the user's first day, 2026-11-09) - so 12, and calls one after another until none is
#: left, at most MOST_CALLS in one go; what an answer left out is asked again on its own.
MOST_AT_ONCE = 12
MOST_CALLS = 10
#: SEVERAL CALLS AT ONCE (the user, 2026-11-09: "为什么79/80卡了这么久" - a call took 60 to 90 seconds,
#: eighty things seven calls one after another): the first alone - the names it gives are shown to
#: the rest - then this many together. Calls already apart, run side by side; none split or merged.
TOGETHER = 3
WORDS_KEY = "agenda.words."
WHO = ("delegable", "approve", "human", "gone")
#: Judged by an earlier way of sorting: judged again (the three kinds came 2026-11-09).
VERSION = "3kinds-decide-alone-2"  # judged again: what waits for a time, looked at as such
FOCUS = ("deep", "shallow", "meeting", "review", "errand")

from ...agent.outside_review import guard as _guard

SYSTEM = """You sort the things on the user's plate, so that their day can be planned. For each thing
you are given where it came from (a to-do, the calendar, something agreed waiting for a time, a
project's next step, work delegated to the agent), its words, and what was found about it: the
user's activity as the agent read it from their screen (their mail, chats and pages among it), what
they said in the chat with the agent, the people who may be meant. You are given what was learned
of how the user works, too.

For each thing say:
- "who", one of three kinds - the user wants the agent to take over all it can in time, so that
  they spend themselves on deep work:
  THE LINE BETWEEN THE TWO (the user, 2026-11-09: "agent单独基于当前信息无法做决定的(或者大概率无法
  做决定的列为一定要给人审批）"): CAN THE AGENT DECIDE IT ALONE, FROM WHAT IS KNOWN NOW?
  "delegable": yes - the agent can do it and decide every part of it from what it has: replies,
    setting a time the user already said suits them, forms, bookings, ordering, collecting,
    sorting, chasing, drafting - EVEN WHEN IT GOES OUT TO OTHERS. WRITING TO SOMEONE THE USER HAS
    ALREADY DECIDED TO WRITE TO - to invite them, to ask to present or to be asked, to ping, to
    follow up ("Write to <someone> about presenting in their group", "Ping <someone> about a talk in
    their speaker series") - is this: the decision is made; what is left the agent writes, from
    what is known of the user and the person (the user, 2026-11-09: "这种应该是agent完全自己做"). For now the agent does it and the
    user approves it before anything goes out; in time it may be handed over for good. Most of what
    the agent can do is this (the user: "绝大多数都是不需要approve的").
  "approve": no, or most likely not - something must come from the user: what is not known (which
    day suits them, what they think of it, a fact only they have), their own judgment or
    preference, a decision only they can make (accepting or turning down an offer, a hire, a
    grade), or a commitment that is theirs to make (money, signing, agreeing to terms). Not a
    message they have already decided to send. The agent prepares all it can; the user decides. Say in
    "missing" what the agent would need to decide it alone.
  "human": the user's own work - thinking, writing that is theirs, a talk or meeting they are in,
    being somewhere. The agent helps where it can: collecting, a first draft of a part, the files.
  "gone": what was found plainly shows it done already, or no longer wanted.
  Work delegated to the agent and finished is "approve" or "delegable" by its kind, "focus" "review".
- "kind_id": the kind of work it is, CHOSEN FROM "kinds" by its id ("K3") - each kind listed with
  the "who" it has, and whether the user set it ("set_by": "user": then give that "who" too). Only
  when it is of none of them: "kind_id": "new", and "new_kind" - a few words general enough to come
  again ("reply to a scheduling email", "review a pull request", "book a trip"), not this thing's
  own details. Two things here of one new kind: the same "new_kind", word for word.
- "deadline": when it must be done by, "YYYY-MM-DD HH:MM" on the user's clock, or "" for none.
  A REMINDER'S TIME IS NOT A DEADLINE: it is when the user wanted to be reminded - the deadline is
  what was promised or asked ("by the end of the week"), else none.
  "hard": true only when a line shows that time (a mail saying "by Friday", the event's own time,
  someone asking again); a time you only think likely is not hard. "deadline_why": one sentence;
  "shown_by": the ids of the lines that show it.
- "lead_minutes": for a thing with a deadline that the agent does or prepares: how long before the
  deadline the user must hear that it is not done yet, to still do it themselves in time - by what
  it is (a talk or a report to prepare: hours or a day; a quick reply: half an hour).
- "minutes": for a meeting or an event, its length; for the agent's work, how long the agent's
  part takes. The user's own things are not timed (the user, 2026-11-10: "现在不需要给我做的东西估计时
  间了") - give 30 for them.
- "focus": "deep" (long thought, best unbroken), "shallow" (quick and small: a reply, a form),
  "meeting", "review" (looking over what the agent made), "errand" (done somewhere else).
- "context": what it belongs with, in a few words - the project, or the person - the same words
  for things that belong together, so that they can be done together.
- "place": where it is done, when it is not at their desk; "" otherwise.
- ONE NAME FOR ONE THING. "contexts_in_use" are the names other things were given already: when
  this one belongs with one of them, give that name WORD FOR WORD - not another way of saying it.
  A new name only for what belongs with none of them.
- "same_as": the id of one of "the_other_things" (or of another thing here) that is THE SAME
  THING as this one - the to-do of a meeting that is on the calendar too, a project's step that
  is a to-do as well; "" when it is not. Only when it is plainly the same, not merely alike.
- "missing": for "approve", in one sentence, what must come from the user - "" otherwise.
- "question": when something is "missing", THE QUESTION AS THE USER WILL READ IT, with nothing else
  in front of them: say what it is about (the thing, the person, the day, where it came from), what
  is known, and exactly what you need - offering the choices you see ("Is the evening call on
  WeChat with ...? And is it Tuesday at 22:00 or 23:00 your time?"). One or two sentences, in their
  language; "" otherwise.
- "agent_part": what the agent would do - all of it for "delegable" and "approve", its help for
  "human" - one sentence.
- "why": one sentence for the user, saying why you sorted it so.
Something that only may interest them, and asks nothing of them, is "human", "focus" "shallow",
with no deadline.

ON THE CALENDAR (from "calendar"; the user, 2026-11-09): say "at_time" - true when it happens AT
that time and the user is in it (a meeting, a call, a talk they go to); false when the time is only
when they wanted to be reminded to do something ("Write to <someone> about ...", "Ping <someone>
...") - then it is a thing to do from that day on, sorted like any other: a message they decided to
send is "delegable".

AGREED, ITS TIME NOT SET (from "waiting"; the user, 2026-11-09: "很多没有确认时间的event也要加进去的吧
（确认一下有没有做完然后加进去)"). FIRST see, from what was found, whether it is over: its time was set
already (in a mail or a chat), it took place, it was called off, or it has stood a long while
("open_for_days") with nothing said of it since - then "gone", saying which in "why". Otherwise
what is on the plate is SETTLING ITS TIME, not the event: "who" for that - "delegable" when the
agent can offer times from the user's free days and nothing of theirs is needed, "approve" when it
needs their say ("missing": what) - "minutes" a few, "focus" "shallow". The event itself goes on the
calendar when its time is known. Judge as the user would, by what was learned of them.

Answer in JSON only:
{"things": [{"id": "...", "who": "...", "kind_id": "K1 or new", "new_kind": "", "deadline": "", "hard": false, "deadline_why": "",
  "shown_by": [], "minutes": 30, "lead_minutes": 60, "focus": "...", "context": "...", "place": "", "agent_part": "",
  "same_as": "", "missing": "", "question": "", "at_time": true, "why": "..."}]}"""
#: What came from outside is text, never an instruction (the user, 2026-11-12).
SYSTEM = SYSTEM + "\n\n" + _guard()


def _kv(db: Any, key: str) -> str:
    row = db.query_one("SELECT value FROM kv WHERE key = ?", (key,))
    return str(row["value"]) if row and row["value"] else ""


def _set(db: Any, key: str, value: str) -> None:
    db.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE "
               "SET value = excluded.value", (key, value))


def judged_for(one: Dict[str, Any]) -> str:
    """What a thing's judgment is kept against: its words, and the way of sorting."""
    return f"{one.get('fingerprint') or ''}:{VERSION}"


#: "approve" sorted before this rule is sorted again - those alone (the user, 2026-11-09: writing to
#: someone they already decided to write to is the agent's; not everything sorted once more).
APPROVE_RULE = "decided-messages-delegable"


def is_judged(one: Dict[str, Any]) -> bool:
    if one.get("triaged_for") != judged_for(one):
        return False
    said = one.get("triage") or {}
    if isinstance(said, str):
        try:
            said = json.loads(said)
        except ValueError:
            said = {}
    if one.get("source") == "calendar" and "at_time" not in said:
        return False                       # sorted before reminders were told from meetings
    return said.get("who") != "approve" or said.get("rule") == APPROVE_RULE


def wanting(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The open ones not judged since their words last changed - or judged the old way."""
    return [one for one in items if not is_judged(one)]


# -- the kinds of work, and what the user set for each ----------------------------------------
def modes(db: Any) -> Dict[str, Dict[str, Any]]:
    """Each kind of work the user (or the agent, before them) said is delegable, to approve or
    human - by the kind's words, lowered."""
    out: Dict[str, Dict[str, Any]] = {}
    try:
        for row in db.query("SELECT * FROM agenda_modes ORDER BY updated_at"):
            out[str(row["kind"]).strip().lower()] = dict(row)
    except Exception:                                # an older database: none yet
        pass
    return out


def set_mode(db: Any, kind: str, mode: str, by: str = "user") -> Optional[Dict[str, Any]]:
    """The kind of work set to a mode (ITERATIVE, the user's word: changed as often as wanted). A
    kind the model made (`by` "agent") is only added - never over one already there."""
    kind = " ".join(str(kind or "").split())[:120]
    if not kind or mode not in WHO or mode == "gone":
        return None
    stamp = datetime.now(timezone.utc).isoformat()
    if by == "user":
        db.execute("INSERT INTO agenda_modes (kind, mode, set_by, updated_at) VALUES (?, ?, ?, ?) "
                   "ON CONFLICT(kind) DO UPDATE SET mode = excluded.mode, set_by = excluded.set_by, "
                   "updated_at = excluded.updated_at", (kind.lower(), mode, by, stamp))
    else:
        db.execute("INSERT INTO agenda_modes (kind, mode, set_by, updated_at) VALUES (?, ?, ?, ?) "
                   "ON CONFLICT(kind) DO NOTHING", (kind.lower(), mode, by, stamp))
    return modes(db).get(kind.lower())


def times_taken(db: Any) -> Dict[str, str]:
    """How long each kind of work was given and how long it really took (agenda_estimates)."""
    try:
        rows = db.query("SELECT kind, planned, actual FROM agenda_estimates WHERE kind != '' "
                        "ORDER BY at DESC LIMIT 400")
    except Exception:
        return {}
    by_kind: Dict[str, List[Any]] = {}
    for row in rows:
        by_kind.setdefault(row["kind"], []).append((int(row["planned"]), int(row["actual"])))
    out = {}
    for kind, pairs in by_kind.items():
        planned, actual = sorted(one[0] for one in pairs), sorted(one[1] for one in pairs)
        out[kind] = (f"given about {planned[len(planned) // 2]} min, took about "
                     f"{actual[len(actual) // 2]} min ({len(pairs)} times)")
    return out


def kinds_listed(set_modes: Dict[str, Dict[str, Any]]) -> Any:
    """THE KINDS AS A LIST TO CHOOSE FROM (the user, 2026-11-09: "不再让模型自由起名 ... 从清单里选";
    "他应该允许创建新编号"): each with an id for this call alone - K1, K2... - the user's first."""
    ordered = sorted(set_modes.values(), key=lambda one: (one["set_by"] != "user", one["kind"]))
    listed = [{"id": f"K{n + 1}", "kind": one["kind"], "who": one["mode"], "set_by": one["set_by"]}
              for n, one in enumerate(ordered[:200])]
    return listed, {one["id"]: one["kind"] for one in listed}


def kind_of(entry: Dict[str, Any], by_id: Dict[str, str]) -> Any:
    """The kind an answer chose - one listed, by its id - or the new one it named; and whether new."""
    chosen = str(entry.get("kind_id") or "").strip().upper()
    if chosen in by_id:
        return by_id[chosen], False
    named = " ".join(str(entry.get("new_kind") or entry.get("kind") or "").split())[:120]
    return named, bool(named)


def _search_words(db: Any, models: Any, items: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """The agent's own search words for each, kept with what they were written for (as the to-do
    check keeps its own) - one call for all new."""
    from ..core import related
    out: Dict[str, List[str]] = {}
    wanted = []
    for one in items:
        about = f"{one['title']} {one.get('detail') or ''}"
        key = hashlib.sha1(about.encode("utf-8")).hexdigest()[:16]
        kept = {}
        try:
            kept = json.loads(_kv(db, WORDS_KEY + one["id"]) or "{}")
        except ValueError:
            pass
        if kept.get("for") == key:
            out[one["id"]] = list(kept.get("words") or [])
        else:
            wanted.append((one["id"], about, key))
    if wanted and models is not None:
        said = related.search_words(models, [{"id": ident, "text": about}
                                             for ident, about, _key in wanted], "agenda_words")
        for ident, _about, key in wanted:
            out[ident] = said.get(ident, [])
            if ident in said:
                _set(db, WORDS_KEY + ident, json.dumps({"for": key, "words": out[ident]},
                                                       ensure_ascii=False))
    return out


def shown(db: Any, items: List[Dict[str, Any]], words: Dict[str, List[str]]) -> List[Dict[str, Any]]:
    from ..core import related
    out = []
    for one in items:
        since = str((one.get("triage") or {}).get("_from", {}).get("since") or "")
        found = related.context(db, f"{one['title']} {one.get('detail') or ''}", since[:19],
                                words=words.get(one["id"], []))
        open_for = _days_since(since) if one["source"] == "waiting" else None
        out.append({"id": one["id"], "from": one["source"], "title": one["title"],
                    "detail": one.get("detail") or "",
                    **({"open_for_days": open_for} if open_for is not None else {}),
                    **({"at": _local(one["fixed_at"])} if one.get("fixed_at") else {}),
                    **({"place": one["place"]} if one.get("place") else {}),
                    **({"project": one["project"]} if one.get("project") else {}),
                    **found})
    return out


def _days_since(at: str) -> Optional[int]:
    try:
        moment = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - moment).days)


def _local(at: str) -> str:
    try:
        moment = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    except ValueError:
        return str(at)[:16]
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone().strftime("%Y-%m-%d %H:%M")


def judged(entry: Any, one: Dict[str, Any], lines: set,
           known: Optional[set] = None) -> Optional[Dict[str, Any]]:
    """What the model said of one thing, kept to what it may be: a hard deadline only on a line it
    was shown; anything going out is the user's to finish."""
    if not isinstance(entry, dict):
        return None
    who = str(entry.get("who") or "")
    if who not in WHO:
        return None
    focus = str(entry.get("focus") or "shallow")
    shown_by = [str(ident) for ident in entry.get("shown_by") or [] if str(ident) in lines]
    deadline = " ".join(str(entry.get("deadline") or "").split())[:16]
    if deadline:
        try:
            datetime.strptime(deadline, "%Y-%m-%d %H:%M")
        except ValueError:
            deadline = ""
    hard = bool(entry.get("hard")) and bool(deadline) and (bool(shown_by) or bool(one.get("fixed_at")))
    try:
        minutes = max(5, min(600, int(entry.get("minutes") or 30)))
    except (TypeError, ValueError):
        minutes = 30
    try:
        lead = max(15, min(2880, int(entry.get("lead_minutes") or 0))) if deadline else 0
    except (TypeError, ValueError):
        lead = 0
    kind = " ".join(str(entry.get("kind") or "").split())[:120]
    same_as = str(entry.get("same_as") or "").strip()
    same_as = same_as if known is not None and same_as in known and same_as != one.get("id") else ""
    return {"who": who, "kind": kind, "deadline": deadline, "hard": hard, "same_as": same_as,
            "deadline_why": " ".join(str(entry.get("deadline_why") or "").split())[:300],
            "shown_by": shown_by, "minutes": minutes, "lead_minutes": lead,
            "focus": focus if focus in FOCUS else "shallow",
            "context": " ".join(str(entry.get("context") or "").split())[:80],
            "place": " ".join(str(entry.get("place") or one.get("place") or "").split())[:200],
            "agent_part": " ".join(str(entry.get("agent_part") or "").split())[:300],
            "missing": " ".join(str(entry.get("missing") or "").split())[:300] if who == "approve" else "",
            "question": " ".join(str(entry.get("question") or "").split())[:600]
            if who == "approve" and entry.get("missing") else "",
            # A calendar thing at its time (a meeting), or a reminder to do something from then on.
            **({"at_time": bool(entry.get("at_time"))} if one.get("fixed_at") else {}),
            "why": " ".join(str(entry.get("why") or "").split())[:300]}


#: The other things listed with each call, at most; their titles cut to this.
MOST_OTHERS, OTHER_TITLE = 250, 120


def in_use(db: Any, want: List[Dict[str, Any]],
           everyone: Optional[List[Dict[str, Any]]] = None) -> Any:
    """The context names the open things were given already - read fresh, so a call made after
    another sees what that one named - and the other open things, by id and title."""
    from . import items as gathered
    rows = gathered.open_items(db)
    asked = {one["id"] for one in want}
    names: Dict[str, int] = {}
    for one in rows:
        name = str((one.get("triage") or {}).get("context") or "").strip()
        if name and is_judged(one):
            names[name] = names.get(name, 0) + 1
    contexts = [name for name, _n in sorted(names.items(), key=lambda kv: (-kv[1], kv[0]))][:80]
    others = [{"id": one["id"], "from": one["source"], "title": str(one["title"])[:OTHER_TITLE],
               **({"at": _local(one["fixed_at"])} if one.get("fixed_at") else {}),
               **({"context": (one.get("triage") or {}).get("context")}
                  if (one.get("triage") or {}).get("context") else {})}
              for one in rows if one["id"] not in asked][:MOST_OTHERS]
    return contexts, others


def run(db: Any, models: Any, items: List[Dict[str, Any]],
        lessons: Optional[Callable[[str], str]] = None) -> Dict[str, Any]:
    """Every open one wanting it, judged: MOST_AT_ONCE to a call, the oldest first, one call after
    another - what a call's answer left out goes in a call of its own after. At most MOST_CALLS."""
    from concurrent.futures import ThreadPoolExecutor
    queue = list(wanting(items))
    judged = asked = calls = 0
    left_out: List[Dict[str, Any]] = []
    first = True
    while queue and calls < MOST_CALLS and models is not None:
        together = 1 if first else min(TOGETHER, MOST_CALLS - calls)
        first = False
        batches = []
        while queue and len(batches) < together:
            batch, queue = queue[:MOST_AT_ONCE], queue[MOST_AT_ONCE:]
            batches.append(batch)
        # What was learned, read here one after another - the embedder is not asked from two
        # threads at once - then the calls side by side.
        learned = [_learned(lessons, batch) for batch in batches]

        def one_call(n: int) -> Dict[str, Any]:
            try:
                return run_once(db, models, batches[n], lessons, everyone=items, learned=learned[n])
            except Exception:
                log.warning("a sorting call failed", exc_info=True)
                return {"judged": 0, "ids": []}
            finally:
                if len(batches) > 1:
                    db.close()                      # this thread's own connection
        if len(batches) == 1:
            results = [one_call(0)]
        else:
            with ThreadPoolExecutor(max_workers=len(batches), thread_name_prefix="agenda-sort") as pool:
                results = list(pool.map(one_call, range(len(batches))))
        calls += len(batches)
        for batch, said in zip(batches, results):
            judged += said["judged"]
            asked += len(batch)
            missed = [one for one in batch if one["id"] not in said["ids"]]
            if len(batch) > 1 and len(missed) < len(batch):
                queue = missed + queue
            else:
                left_out += missed
    return {"judged": judged, "asked": asked, "calls": calls,
            "left": len(queue) + len(left_out)}


def _learned(lessons: Optional[Callable[[str], str]], want: List[Dict[str, Any]]) -> str:
    if lessons is None:
        return ""
    try:
        return lessons(" ".join(one["title"] for one in want)[:2000])
    except Exception:
        log.warning("no lessons for the agenda's sorting", exc_info=True)
        return ""


def run_once(db: Any, models: Any, want: List[Dict[str, Any]],
             lessons: Optional[Callable[[str], str]] = None,
             everyone: Optional[List[Dict[str, Any]]] = None,
             learned: Optional[str] = None) -> Dict[str, Any]:
    """One call: these judged - shown, too, the names the rest were given and every other thing's
    title (the user, 2026-11-09: one name for one thing across calls; the same thing said so)."""
    from ...common.languages import in_display, in_english
    from ...models.base import ModelRequest
    from ..states.heartbeat import _as_json
    words = _search_words(db, models, want)
    handed = shown(db, want, words)
    if learned is None:
        learned = _learned(lessons, want)
    contexts, others = in_use(db, want, everyone)
    set_modes = modes(db)
    listed_kinds, kind_ids = kinds_listed(set_modes)
    response = models.invoke(ModelRequest(
        messages=[{"role": "user", "content": json.dumps(
            {"now": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %A"),
             "what_was_learned_of_the_user": learned[:12000],
             "contexts_in_use": contexts, "the_other_things": others,
             "kinds": listed_kinds,
             "things": handed},
            ensure_ascii=False, indent=1)[:90000]}],
        system=SYSTEM, max_tokens=6000, task="agenda_triage",
        language=in_display("each thing's \"question\" - the user reads it")
        + in_english("everything else: \"why\", \"missing\", the kinds and the context names")),
        role="heartbeat")
    said = _as_json(getattr(response, "text", "") or "") or {}
    entries = said.get("things") if isinstance(said, dict) else None
    by_id = {one["id"]: one for one in want}
    known = set(by_id) | {one["id"] for one in others}
    lines_of = {one["id"]: {line["id"] for line in one["activity_about_it"] + one["said_in_the_chat"]}
                for one in handed}
    stamp = datetime.now(timezone.utc).isoformat()
    count = 0
    ids: List[str] = []
    for entry in entries if isinstance(entries, list) else []:
        one = by_id.get(str((entry or {}).get("id") or "")) if isinstance(entry, dict) else None
        if one is None:
            continue
        kind, new = kind_of(entry, kind_ids)
        kept = judged(dict(entry, kind=kind), one, lines_of.get(one["id"], set()), known)
        if kept is None:
            continue
        if new and kept["who"] != "gone":
            set_mode(db, kind, kept["who"], "agent")   # listed, by its id, to the calls after
        kept["_from"] = (one.get("triage") or {}).get("_from") or {}
        kept["at"] = stamp
        kept["rule"] = APPROVE_RULE
        # THE USER'S WORD ON IT (act.py) outlasts its being sorted again.
        before = one.get("triage") or {}
        for key in ("user_not_before", "user_after", "user_minutes", "user_state"):
            if before.get(key):
                kept[key] = before[key]
        if before.get("user_minutes"):
            kept["minutes"] = int(before["user_minutes"])
        # WHAT THE USER SET FOR ITS KIND HOLDS - by code, not left to the model's word; a thing the
        # user set by itself keeps its own.
        own = ((one.get("triage") or {}).get("set_by_user") or "")
        if own in WHO:
            kept["who"], kept["set_by_user"] = own, own
        else:
            held = set_modes.get(kept["kind"].lower()) if kept["kind"] else None
            if held and held.get("set_by") == "user" and kept["who"] != "gone":
                kept["who"] = held["mode"]
        db.execute("UPDATE agenda_items SET triage = ?, triaged_for = ? WHERE id = ?",
                   (json.dumps(kept, ensure_ascii=False), judged_for(one), one["id"]))
        count += 1
        ids.append(one["id"])
    return {"judged": count, "asked": len(want), "ids": ids}


__all__ = ["run", "run_once", "wanting", "judged", "is_judged", "judged_for", "modes", "set_mode",
           "kinds_listed", "kind_of",
           "SYSTEM", "WHO", "FOCUS", "MOST_AT_ONCE", "MOST_CALLS", "VERSION"]
