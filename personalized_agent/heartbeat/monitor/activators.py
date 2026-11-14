"""THE ACTIVATORS - what turns a real-time signal into a model being asked, or
a word to the user (the user, 2026-11-03: "编程的本质就是编辑一个real-time
signal到model calling的activator ... activator 是能用代码运行的（而不是模型）").

An Activator is data, written by the model (from Chat or from the heartbeat's
step_monitor) or built in, and RUN BY CODE: a condition over the signals and
events of signals.py - and, for what appears on the screen, weighted keywords -
and what is done when it holds, by score:

    notify     say it at once, in the words given (no model)
    ask_model  ask the model now - "recommend": whether to recommend, and
               what; "calendar": the notification step on that window;
               "evening": the evening report
    queue      kept for the next heartbeat's step_monitor to judge

    {"name": "...", "purpose": "...", "group": "recommend|risk|calendar|rest|other",
     "when": <condition>,
     "then": [{"min_score": 8, "do": "notify", "title": "...", "message": "..."},
              {"min_score": 4, "do": "queue"}],
     "cooldown_minutes": 30, "most_per_day": 4}

    <condition> = {"all": [<condition>, ...]} | {"any": [...]} | {"not": <condition>}
                | {"signal": "since_break_minutes", "op": ">=", "value": 120}
                | {"signal": "work_minutes", "op": ">=", "value": 90,
                   "habit": "usual_work_stretch_minutes", "times": 1, "plus": 0}
                  (BY THE USER'S HABIT: against the habit's value times "times" plus "plus" -
                   "value" only while the habit is not known yet, -1)
                | {"event": "back"}
                | {"keywords": {"words": {"verify your account": 3, ...},
                                "where": ["page", "email"]}}
                | {"pattern": "<regex>", "in": "title|text|either", "score": 1}  (built in only)
                | {"new_text": {"least_chars": 800, "unlike": 0.9, "minutes": 30}}  (built in only)

Code holds what the model writes to this shape (`check`): only signals and
events there are, a few nodes, plain words (never a pattern), a cooldown
never shorter than a minute. A PROTECTED Activator - the user's switch; the
spam and the two calendar ones are protected until the user says otherwise -
is never edited or deleted by the model, whatever it asks (`edit`, `delete`).
Every change is kept: who, why, before and after.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ...common.events.models import new_id, utcnow
from .signals import EVENTS, SIGNALS, words_of

SCHEMA = """
CREATE TABLE IF NOT EXISTS monitor_activators (
    id          TEXT PRIMARY KEY,
    data        TEXT NOT NULL,                 -- the Activator, as written
    enabled     INTEGER NOT NULL DEFAULT 1,    -- the user's switch
    protected   INTEGER NOT NULL DEFAULT 0,    -- the user's: never changed by the model
    builtin     TEXT NOT NULL DEFAULT '',      -- which built-in one it is, if any
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    deleted_at  TEXT
);
CREATE TABLE IF NOT EXISTS monitor_changes (
    id            TEXT PRIMARY KEY,
    activator_id  TEXT NOT NULL,
    at            TEXT NOT NULL,
    by            TEXT NOT NULL,               -- built in | chat | heartbeat | user
    op            TEXT NOT NULL,               -- add | edit | delete | on | off | protect | unprotect
    why           TEXT NOT NULL DEFAULT '',
    before        TEXT,
    after         TEXT
);
CREATE INDEX IF NOT EXISTS idx_monitor_changes ON monitor_changes(activator_id, at);
CREATE TABLE IF NOT EXISTS monitor_quiet (
    id          TEXT PRIMARY KEY,
    data        TEXT NOT NULL,                 -- {name, when}
    created_at  TEXT NOT NULL,
    deleted_at  TEXT
);
"""

GROUPS = ("recommend", "risk", "calendar", "deadline", "rest", "summary", "system", "other")
#: A BUILT-IN ONE IS FIXED (the user, 2026-11-13: "现在设置的几个monitor都是protected, 不允许改"): on or off only.
BUILT_IN_FIXED = "A built-in Activator is fixed: it can only be switched on or off."
#: How what an Activator sends is delivered: at once, or into the waiting list.
DELIVERIES = ("pop", "wait")
DOES = ("notify", "ask_model", "queue")
HANDLERS = ("recommend", "judge", "calendar", "evening", "evening_now", "old_project", "morning")
#: What may come after a notify, once it is said (`and_then`).
FOLLOWS = ("ask_model", "queue")
OPS = ("<", "<=", ">", ">=", "==", "!=", "contains")
WHERE = ("page", "email", "chat", "terminal", "any")
#: The bounds code holds a written Activator to.
MOST_NODES, MOST_DEPTH, MOST_WORDS, MOST_WORD = 40, 5, 300, 60
LEAST_COOLDOWN, MOST_A_DAY = 1, 50
MOST_ACTIVATORS = 60

#: A LOT OF NEW TEXT: fewer words than this say nothing of how unlike it is, and with fewer words of
#: late to hold it against, after a start or a gap, everything would be.
NEW_TEXT_LEAST_WORDS, NEW_TEXT_LEAST_KNOWN = 20, 200

#: THE DISPLAY LANGUAGES (the user, 2026-11-03: "必须支持agent支持的所有display
#: language"): those code can tell by their script, and the rest by name.
SCRIPTS = {"Chinese": re.compile(r"[一-鿿]"), "Japanese": re.compile(r"[぀-ヿ]"),
           "Korean": re.compile(r"[가-힯]"), "Russian": re.compile(r"[Ѐ-ӿ]")}
LANGUAGES = "English and Chinese"

#: How the model is told to write them - Chat's tools and step_monitor alike.
RULES_FOR_THE_MODEL = f"""HOW TO WRITE AN ACTIVATOR (the Monitor) - code runs it, not you:
- "when" is a condition over the SIGNALS and EVENTS listed (monitor_signals),
  and, for what appears on the screen, weighted "keywords" ("where": page,
  email, chat, terminal or any). Keep it as simple as it can be. A name is
  matched with "contains" - names change. For a project or task, weigh
  "project_how": one told by "words" is wrong about one time in five, so what
  matters asks for grouping, history or paths.
- "then" says what is done by score, highest "min_score" first: "notify" (said at
  once, in the "title" and "message" you give - {{signal_name}} is put in),
  "ask_model" with "handler" "recommend" and a "brief", or "evening" (the report of the day,
  asked first), or "evening_now" (written without asking), or "queue"
  (judged at the next heartbeat).
- A "notify" MAY ALSO HAVE, when it should not wait for a model: "reason" (shown under it;
  {{matched}} is what set it off) and "and_then", what comes once it is said:
  {{"do": "ask_model", "handler": "judge", "brief": "..."}}, a model looks closer at
  once and says more only if it has more to say, or {{"do": "queue"}}. What it says
  comes beside the first notification and never takes it back, with no link or
  picture. Most need neither: use them when a first word cannot wait.
- AN ACTIVATOR IS A CONDITION AND A JOB. The "brief" of an "ask_model" says what the
  model is to do when the condition holds, as an order: what to look for, where,
  and what to tell the user - "Find this week's new papers on soil drainage and
  send the best one". Write the user's own request into it. It is required.
- KEYWORDS IN EVERY DISPLAY LANGUAGE the agent supports - {LANGUAGES} - each
  word or phrase as it is written in that language, weighted 0.5 to 10.
- A cooldown of minutes and a most a day that keep it from being a nuisance.
- "delivery": "pop" (what it sends comes up at once) or "wait" (it waits in the user's list);
  the user's to set - left out, a risk and a meeting pop and the rest wait.
- PREFER EDITING one there is to adding another. DELETE ONLY WHEN IT IS REALLY
  NECESSARY - one that keeps being called a bad notification is first narrowed
  (a higher score, fewer words, a longer cooldown), not deleted.
- A PROTECTED one cannot be changed or deleted - the user protected it.
- WHEN THE USER WANTS TO BE LEFT ALONE AT SOME MOMENT ("not during meetings"), that
  is ONE blocker (monitor_quiet_add), not an edit of every Activator: while it
  applies no reminder of any kind is shown.
- TO CHANGE OR DELETE ONE, LOOK FIRST: monitor_evidence, then cite the cases that
  truly bear on it. The more you change in one turn the more it takes, and code
  holds you to it. Nobody is asked: what you cannot show, you do not do - keep
  the lesson and change less. The evidence builds up, and it can go further later.
- WHEN THE USER THEMSELVES ORDERED THE CHANGE AND NAMED WHAT IT COVERS, quote their
  words in "user_said": that is the evidence. Never for what you only inferred."""


def _now() -> str:
    return utcnow().isoformat()


def _clean_words(words: Any) -> Dict[str, float]:
    if not isinstance(words, dict):
        raise ValueError('"words" is an object of word or phrase -> weight')
    out: Dict[str, float] = {}
    for word, weight in words.items():
        word = " ".join(str(word or "").split()).lower()
        if not word:
            continue
        if len(word) > MOST_WORD:
            raise ValueError(f"a keyword is at most {MOST_WORD} characters: {word[:30]}...")
        try:
            weight = float(weight)
        except (TypeError, ValueError):
            raise ValueError(f"the weight of {word!r} is a number")
        out[word] = max(0.5, min(10.0, weight))
    if not out:
        raise ValueError("keywords need at least one word")
    return out


def _check_condition(cond: Any, builtin: bool, counted: List[int], depth: int = 0,
                     words: Optional[List[str]] = None) -> Dict[str, Any]:
    if depth > MOST_DEPTH:
        raise ValueError(f"a condition is at most {MOST_DEPTH} deep")
    counted[0] += 1
    if counted[0] > MOST_NODES:
        raise ValueError(f"a condition has at most {MOST_NODES} parts")
    if not isinstance(cond, dict) or len(cond) < 1:
        raise ValueError("each part of a condition is an object")
    for key in ("all", "any"):
        if key in cond:
            parts = cond[key]
            if not isinstance(parts, list) or not parts:
                raise ValueError(f'"{key}" is a list of conditions')
            return {key: [_check_condition(one, builtin, counted, depth + 1, words)
                          for one in parts]}
    if "not" in cond:
        return {"not": _check_condition(cond["not"], builtin, counted, depth + 1, words)}
    if "signal" in cond:
        name = str(cond.get("signal") or "")
        if name not in SIGNALS:
            raise ValueError(f"there is no signal {name!r} - see monitor_signals")
        op = str(cond.get("op") or "==")
        if op not in OPS:
            raise ValueError(f"op is one of {', '.join(OPS)}")
        if op == "contains" and SIGNALS[name][0] != "text":
            raise ValueError(f"contains is for text, and {name} is a {SIGNALS[name][0]}")
        value = cond.get("value")
        if SIGNALS[name][0] == "number":
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise ValueError(f"{name} is a number")
        elif SIGNALS[name][0] == "bool":
            value = value if isinstance(value, bool) else str(value).lower() in ("true", "1", "yes")
        else:
            value = str(value or "")[:120]
        out = {"signal": name, "op": op, "value": value}
        habit = cond.get("habit")
        if habit:
            habit = str(habit)
            if SIGNALS.get(habit, ("",))[0] != "number" or SIGNALS[name][0] != "number":
                raise ValueError("a habit is a number signal, for a number signal")
            try:
                times = float(cond.get("times", 1) or 1)
                plus = float(cond.get("plus", 0) or 0)
            except (TypeError, ValueError):
                raise ValueError('"times" and "plus" are numbers')
            if not 0.1 <= times <= 10:
                raise ValueError('"times" is between 0.1 and 10')
            out.update(habit=habit, times=times, plus=plus)
        return out
    if "event" in cond:
        name = str(cond.get("event") or "")
        if name not in EVENTS or name == "text":
            raise ValueError(f"there is no event {name!r} - see monitor_signals")
        return {"event": name}
    if "keywords" in cond:
        spec = cond.get("keywords") or {}
        if not isinstance(spec, dict):
            raise ValueError('"keywords" is {"words": {...}, "where": [...]}')
        found = _clean_words(spec.get("words"))
        if words is not None:
            words.extend(found)
        where = spec.get("where") or ["any"]
        where = [where] if isinstance(where, str) else list(where)
        if any(one not in WHERE for one in where):
            raise ValueError(f"where is any of {', '.join(WHERE)}")
        return {"keywords": {"words": found, "where": where}}
    if "new_text" in cond:
        if not builtin:
            raise ValueError("only plain keywords - new_text is for the built-in ones")
        spec = cond.get("new_text") or {}
        return {"new_text": {"least_chars": int(spec.get("least_chars") or 800),
                             "unlike": float(spec.get("unlike") or 0.9),
                             "minutes": float(spec.get("minutes") or 30)}}
    if "pattern" in cond:
        if not builtin:
            raise ValueError("only plain keywords - a pattern is for the built-in ones")
        pattern = str(cond.get("pattern") or "")
        re.compile(pattern)
        place = str(cond.get("in") or "either")
        return {"pattern": pattern, "in": place if place in ("title", "text", "either") else
                "either", "score": float(cond.get("score") or 1)}
    raise ValueError("a part of a condition is all, any, not, signal, event or keywords")


def check(data: Any, builtin: bool = False) -> Tuple[Dict[str, Any], List[str]]:
    """An Activator held to its shape - or ValueError, saying what is wrong -
    and what the model should still look at (warnings)."""
    if not isinstance(data, dict):
        raise ValueError("an Activator is an object")
    name = " ".join(str(data.get("name") or "").split())[:60]
    if not name:
        raise ValueError("an Activator needs a name")
    group = str(data.get("group") or "other")
    if group not in GROUPS:
        raise ValueError(f"group is one of {', '.join(GROUPS)}")
    words: List[str] = []
    when = _check_condition(data.get("when"), builtin, [0], words=words)
    then = data.get("then")
    if isinstance(then, dict):
        then = [then]
    if not isinstance(then, list) or not then:
        raise ValueError('"then" is a list of what is done, by score')
    tiers = []
    for tier in then:
        if not isinstance(tier, dict):
            raise ValueError("each of then is an object")
        do = str(tier.get("do") or "")
        if do not in DOES:
            raise ValueError(f"do is one of {', '.join(DOES)}")
        clean: Dict[str, Any] = {"do": do, "min_score": float(tier.get("min_score") or 0)}
        if do == "notify":
            clean["title"] = str(tier.get("title") or "").strip()[:120]
            clean["message"] = str(tier.get("message") or "").strip()[:600]
            if not clean["title"]:
                raise ValueError("a notify says its title")
            if tier.get("choices") in ("break",):
                clean["choices"] = tier["choices"]
            if tier.get("reason"):
                clean["reason"] = str(tier["reason"]).strip()[:300]
            if tier.get("and_then"):
                clean["and_then"] = _check_follow(tier["and_then"])
        if do == "ask_model":
            handler = str(tier.get("handler") or "recommend")
            if handler not in HANDLERS or (handler in ("calendar", "old_project", "morning")
                                            and not builtin):
                raise ValueError("handler is recommend or evening")
            clean["handler"] = handler
            brief = str(tier.get("brief") or "").strip()[:600]
            if handler == "recommend" and not brief:
                raise ValueError("an ask_model says what the model is to do, in its brief")
            if brief:
                clean["brief"] = brief
        tiers.append(clean)
    tiers.sort(key=lambda one: -one["min_score"])
    try:
        cooldown = float(data.get("cooldown_minutes", 30))
        most = int(data.get("most_per_day", 6))
    except (TypeError, ValueError):
        raise ValueError("cooldown_minutes and most_per_day are numbers")
    # POPPED OR WAITING, ITS OWN SETTING (the user, 2026-11-07: "monitor 里可以编写default设置"):
    # what it sends comes up at once ("pop") or waits in the list ("wait"); unsaid, a risk and a
    # meeting pop and the rest wait.
    delivery = str(data.get("delivery") or "").strip().lower()
    if delivery not in DELIVERIES:
        delivery = "pop" if group in ("risk", "calendar") else "wait"
    out = {"name": name, "purpose": str(data.get("purpose") or "")[:400], "group": group,
           "when": when, "then": tiers,
           "cooldown_minutes": max(LEAST_COOLDOWN, cooldown),
           "most_per_day": max(1, min(MOST_A_DAY, most)), "delivery": delivery}
    warnings = []
    if words:
        missing = [lang for lang, script in SCRIPTS.items()
                   if not any(script.search(word) for word in words)]
        if missing:
            warnings.append("keywords in no " + ", ".join(missing) + " - every display "
                            "language is needed (" + LANGUAGES + ")")
    return out, warnings


# -- running a condition ------------------------------------------------------------------
def _check_follow(follow: Any) -> Dict[str, Any]:
    """What comes after a notify: code says at once, a model after it."""
    if not isinstance(follow, dict) or follow.get("do") not in FOLLOWS:
        raise ValueError(f"and_then does one of {', '.join(FOLLOWS)}")
    if follow["do"] == "queue":
        return {"do": "queue"}
    handler = str(follow.get("handler") or "judge")
    brief = str(follow.get("brief") or "").strip()[:600]
    if handler not in ("judge", "recommend") or not brief:
        raise ValueError("and_then asks the model with a handler, judge or recommend, and a brief")
    return {"do": "ask_model", "handler": handler, "brief": brief}


def text_driven(cond: Dict[str, Any]) -> bool:
    """Whether a condition looks at what appears on the screen."""
    if "keywords" in cond or "pattern" in cond or "new_text" in cond:
        return True
    for key in ("all", "any"):
        if key in cond:
            return any(text_driven(one) for one in cond[key])
    if "not" in cond:
        return text_driven(cond["not"])
    return False


def want_of(cond: Dict[str, Any], signals: Dict[str, Any]) -> Tuple[Any, str]:
    """What a signal is compared with: the user's habit when the condition names one and it is
    known (times, plus) - else its fixed value, the default; and how it is said in the why."""
    habit = cond.get("habit")
    if not habit:
        return cond["value"], ""
    try:
        usual = float(signals.get(habit, -1))
    except (TypeError, ValueError):
        usual = -1.0
    if usual < 0:
        return cond["value"], f" (the default {cond['value']:g}: {habit} not known yet)"
    want = round(usual * float(cond.get("times", 1)) + float(cond.get("plus", 0)), 2)
    return want, f" (by the user's habit, {habit} {usual:g}: {want:g})"


def _compare(have: Any, op: str, want: Any) -> bool:
    try:
        if isinstance(want, (int, float)) and not isinstance(want, bool):
            have = float(have)
        elif isinstance(want, bool):
            have = bool(have)
        else:
            have, want = str(have or "").lower(), str(want or "").lower()
    except (TypeError, ValueError):
        return False
    if op == "contains":
        return str(want).lower() in str(have).lower()
    return {"<": have < want, "<=": have <= want, ">": have > want, ">=": have >= want,
            "==": have == want, "!=": have != want}[op]


def _word_found(word: str, text: str) -> bool:
    if re.fullmatch(r"[a-z0-9 '\-]+", word):
        return re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", text) is not None
    return word in text


def evaluate(cond: Dict[str, Any], ctx: Dict[str, Any]) -> Tuple[bool, float, List[str]]:
    """(holds, score, why) of a condition, on `ctx`: "signals" (a snapshot),
    "events" (said this look), and for a text look "text", "title", "where"."""
    if "all" in cond:
        held, score, why = True, 0.0, []
        for one in cond["all"]:
            ok, got, said = evaluate(one, ctx)
            if not ok:
                return False, 0.0, []
            score, why = score + got, why + said
        return held, score, why
    if "any" in cond:
        held, score, why = False, 0.0, []
        for one in cond["any"]:
            ok, got, said = evaluate(one, ctx)
            if ok:
                held, score, why = True, score + got, why + said
        return held, score, why
    if "not" in cond:
        ok, _score, _why = evaluate(cond["not"], ctx)
        return (not ok), 0.0, []
    if "signal" in cond:
        signals = ctx.get("signals") or {}
        have = signals.get(cond["signal"])
        want, said = want_of(cond, signals)
        ok = have is not None and _compare(have, cond["op"], want)
        return ok, 0.0, [f"{cond['signal']} {have}{said}"] if ok else []
    if "event" in cond:
        ok = cond["event"] in (ctx.get("events") or ())
        return ok, 0.0, [f"event {cond['event']}"] if ok else []
    if "keywords" in cond:
        spec = cond["keywords"]
        where = spec.get("where") or ["any"]
        if "any" not in where and ctx.get("where") not in where:
            return False, 0.0, []
        text = (str(ctx.get("title") or "") + "\n" + str(ctx.get("text") or "")).lower()
        found = [(word, weight) for word, weight in spec["words"].items()
                 if _word_found(word, text)]
        score = sum(weight for _word, weight in found)
        return bool(found), score, [f"“{word}”" for word, _w in found[:12]]
    if "new_text" in cond:
        return _new_text(cond["new_text"], ctx)
    if "pattern" in cond:
        place = cond.get("in", "either")
        texts = {"title": [ctx.get("title") or ""], "text": [ctx.get("text") or ""],
                 "either": [ctx.get("title") or "", ctx.get("text") or ""]}[place]
        for text in texts:
            hit = re.search(cond["pattern"], str(text))
            if hit:
                return True, float(cond.get("score") or 1), [f"“{hit.group(0)[:60]}”"]
        return False, 0.0, []
    return False, 0.0, []


def _new_text(spec: Dict[str, Any], ctx: Dict[str, Any]) -> Tuple[bool, float, List[str]]:
    """A LOT OF NEW TEXT, UNLIKE THE WORDS OF LATE (the user, 2026-11-06): the share of its words that
    were on no screen for the last minutes. Not a terminal: a coding assistant writes there all day.
    Mail and chats too: what the model may search with is held (runtime/web_guard.py, `hold`). Not
    what shows a password, which no model is to be given."""
    text, recent = str(ctx.get("text") or ""), ctx.get("recent_words")
    if recent is None or ctx.get("where") == "terminal" or len(text) < spec["least_chars"]:
        return False, 0.0, []
    words = words_of(text)
    if len(words) < NEW_TEXT_LEAST_WORDS:
        return False, 0.0, []
    known = recent(spec["minutes"])
    if len(known) < NEW_TEXT_LEAST_KNOWN:
        return False, 0.0, []
    new = sum(1 for word in words if word not in known) / len(words)
    if new < spec["unlike"]:
        return False, 0.0, []
    from ...runtime.documents import shows_credentials
    if shows_credentials(text):
        return False, 0.0, []
    return True, 0.0, [f"{len(text)} new characters, {round(new * 100)}% of the words unlike the "
                       f"last {spec['minutes']:.0f} minutes"]


def tier_for(activator: Dict[str, Any], score: float) -> Optional[Dict[str, Any]]:
    for tier in activator["then"]:
        if score >= tier["min_score"]:
            return tier
    return None


# -- the store ------------------------------------------------------------------------------
class ActivatorStore:
    """The Activators, their switches and every change to them."""

    def __init__(self, db: Any) -> None:
        self.db = db
        with self.db.write() as conn:
            conn.executescript(SCHEMA)
            # THE NAME THE USER SEES (the user, 2026-11-07): theirs alone, kept apart from what the
            # model writes (`data`), so no change of the model's reaches it; empty - its own name.
            names = {row[1] for row in conn.execute("PRAGMA table_info(monitor_activators)")}
            if "display_name" not in names:
                conn.execute("ALTER TABLE monitor_activators ADD COLUMN display_name TEXT NOT "
                             "NULL DEFAULT ''")

    def _row(self, row: Any) -> Dict[str, Any]:
        data = json.loads(row["data"] or "{}")
        shown = ""
        try:
            shown = str(row["display_name"] or "")
        except (IndexError, KeyError):
            shown = ""
        return dict(data, id=row["id"], enabled=bool(row["enabled"]),
                    display_name=shown or str(data.get("name") or ""),
                    protected=bool(row["protected"]), builtin=row["builtin"] or "",
                    created_at=row["created_at"], updated_at=row["updated_at"],
                    text_driven=text_driven(data.get("when") or {}))

    def list(self) -> List[Dict[str, Any]]:
        return [self._row(row) for row in self.db.query(
            "SELECT * FROM monitor_activators WHERE deleted_at IS NULL ORDER BY created_at, rowid")]

    def get(self, ident: str) -> Optional[Dict[str, Any]]:
        row = self.db.query_one("SELECT * FROM monitor_activators WHERE id = ? AND "
                                "deleted_at IS NULL", (str(ident or ""),))
        return self._row(row) if row else None

    # -- rules of quiet: everything that speaks for itself held while one applies ---------
    def quiet_rules(self) -> List[Dict[str, Any]]:
        out = []
        for row in self.db.query("SELECT id, data, created_at FROM monitor_quiet "
                                 "WHERE deleted_at IS NULL ORDER BY created_at"):
            out.append({"id": row["id"], "created_at": row["created_at"],
                        **json.loads(row["data"])})
        return out

    def add_quiet(self, name: Any, when: Any, by: str, why: str = "") -> str:
        """A rule of quiet, its condition held to the shape of an Activator's."""
        clean, _warnings = check({"name": name, "purpose": str(why or name), "group": "other",
                                  "when": when, "then": [{"do": "queue"}]})
        ident = new_id("quiet")
        data = {"name": clean["name"], "when": clean["when"]}
        self.db.execute("INSERT INTO monitor_quiet (id, data, created_at) VALUES (?, ?, ?)",
                        (ident, json.dumps(data, ensure_ascii=False), _now()))
        self._said(ident, by, "quiet_add", why, None, data)
        return ident

    def remove_quiet(self, ident: str, by: str, why: str = "") -> bool:
        gone = self.db.execute("UPDATE monitor_quiet SET deleted_at = ? WHERE id = ? AND "
                               "deleted_at IS NULL", (_now(), ident)).rowcount
        if gone:
            self._said(ident, by, "quiet_remove", why, None, None)
        return bool(gone)

    def undo(self, change_ids: Sequence[str]) -> int:
        """Changes taken back, the latest first: the way the agent's changes
        are answered for, there being no asking before them. Once each."""
        undone = 0
        for ident in reversed(list(change_ids)):
            row = self.db.query_one("SELECT * FROM monitor_changes WHERE id = ?", (ident,))
            if row is None or self.db.query_one("SELECT id FROM monitor_changes WHERE op = 'undo' "
                                                "AND why = ?", (ident,)):
                continue
            target, op = row["activator_id"], row["op"]
            if op == "edit" and row["before"]:
                self.db.execute("UPDATE monitor_activators SET data = ?, updated_at = ? WHERE id = ?",
                                (row["before"], _now(), target))
            elif op == "delete":
                self.db.execute("UPDATE monitor_activators SET deleted_at = NULL, updated_at = ? "
                                "WHERE id = ?", (_now(), target))
            elif op == "add":
                self.db.execute("UPDATE monitor_activators SET deleted_at = ?, updated_at = ? "
                                "WHERE id = ?", (_now(), _now(), target))
            elif op == "quiet_add":
                self.db.execute("UPDATE monitor_quiet SET deleted_at = ? WHERE id = ?", (_now(), target))
            else:
                continue
            self._said(target, "user", "undo", ident, None, None)
            undone += 1
        return undone

    def _said(self, ident: str, by: str, op: str, why: str, before: Any, after: Any) -> None:
        self.db.execute(
            "INSERT INTO monitor_changes (id, activator_id, at, by, op, why, before, after) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (new_id("monchg"), ident, _now(), by, op, str(why or "")[:600],
             json.dumps(before, ensure_ascii=False) if before is not None else None,
             json.dumps(after, ensure_ascii=False) if after is not None else None))

    def add(self, data: Any, by: str, why: str = "", builtin: str = "",
            protected: bool = False, enabled: bool = True) -> Tuple[str, List[str]]:
        clean, warnings = check(data, builtin=bool(builtin))
        if not builtin and len(self.list()) >= MOST_ACTIVATORS:
            raise ValueError(f"there are {MOST_ACTIVATORS} Activators already - edit one")
        ident = new_id("act")
        now = _now()
        self.db.execute(
            "INSERT INTO monitor_activators (id, data, enabled, protected, builtin, created_at, "
            "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ident, json.dumps(clean, ensure_ascii=False), int(enabled), int(protected),
             builtin, now, now))
        self._said(ident, by, "add", why, None, clean)
        return ident, warnings

    @staticmethod
    def _fixed(found: Dict[str, Any]) -> bool:
        """ONE OF THE APPLICATION'S OWN, AS IT IS NOW (the user, 2026-11-13: "现在设置的几个monitor都是protected, 不
        允许改"): switched on or off, nothing more. One once built in and since taken out of the
        application (an old break reminder kept as the user's) is theirs as any other."""
        from .builtins import builtins as given
        return bool(found.get("builtin")) and str(found["builtin"]) in {
            str(one.get("builtin")) for one in given()}

    def _model_may(self, found: Optional[Dict[str, Any]], ident: str) -> Dict[str, Any]:
        if found is None:
            raise ValueError(f"there is no Activator {ident!r}")
        if found["protected"]:
            raise ValueError(f"“{found['name']}” is protected by the user: it cannot be "
                             "changed or deleted")
        return found

    def edit(self, ident: str, changes: Any, by: str, why: str = "") -> Tuple[Dict[str, Any], List[str]]:
        """An Activator changed by the model - the fields given replace its own;
        never a protected one (code, not the prompt, refuses it)."""
        found = self._model_may(self.get(ident), ident)
        if not isinstance(changes, dict):
            raise ValueError("the changes are an object of fields")
        before = {key: found.get(key) for key in ("name", "purpose", "group", "when", "then",
                                                   "cooldown_minutes", "most_per_day",
                                                   "delivery")}
        clean, warnings = check(dict(before, **{k: v for k, v in changes.items()
                                                 if k in before}), builtin=bool(found["builtin"]))
        self.db.execute("UPDATE monitor_activators SET data = ?, updated_at = ? WHERE id = ?",
                        (json.dumps(clean, ensure_ascii=False), _now(), found["id"]))
        self._said(found["id"], by, "edit", why, before, clean)
        return clean, warnings

    #: What the user may change from the panel.
    USER_FIELDS = ("purpose", "when", "then", "cooldown_minutes", "most_per_day", "delivery")

    def user_edit(self, ident: str, changes: Any, why: str = "") -> Tuple[Dict[str, Any], List[str]]:
        """THE USER'S OWN CHANGE (the user, 2026-11-07: "monitor要允许用户进行edit"): held to the
        same shape (`check`), a protected one too - protection keeps it from the model, not from
        them; the change is said to be theirs, so the one they changed is theirs to keep."""
        found = self.get(ident)
        if found is None:
            raise ValueError(f"there is no Activator {ident!r}")
        if self._fixed(found):
            raise ValueError(BUILT_IN_FIXED)
        if not isinstance(changes, dict):
            raise ValueError("the changes are an object of fields")
        before = {key: found.get(key) for key in ("name", "purpose", "group", "when", "then",
                                                   "cooldown_minutes", "most_per_day",
                                                   "delivery")}
        clean, warnings = check(dict(before, **{k: v for k, v in changes.items()
                                                 if k in self.USER_FIELDS}),
                                builtin=bool(found["builtin"]))
        self.db.execute("UPDATE monitor_activators SET data = ?, updated_at = ? WHERE id = ?",
                        (json.dumps(clean, ensure_ascii=False), _now(), found["id"]))
        self._said(found["id"], "user", "edit", why or "the user changed it", before, clean)
        return clean, warnings

    def rename(self, ident: str, display_name: Any) -> Dict[str, Any]:
        """The user's name for it - only the user's: empty gives it back its own name."""
        found = self.get(ident)
        if found is None:
            raise ValueError(f"there is no Activator {ident!r}")
        shown = " ".join(str(display_name or "").split())[:60]
        if shown == found["name"]:
            shown = ""
        self.db.execute("UPDATE monitor_activators SET display_name = ?, updated_at = ? WHERE "
                        "id = ?", (shown, _now(), found["id"]))
        self._said(found["id"], "user", "rename", "", {"display_name": found["display_name"]},
                   {"display_name": shown or found["name"]})
        return self.get(ident)

    def delete(self, ident: str, by: str, why: str = "") -> None:
        # The application's own taking out of one of its built-in ones, whatever its protection.
        found = self.get(ident) if by == "built in" else self._model_may(self.get(ident), ident)
        if found is None:
            raise ValueError(f"there is no Activator {ident!r}")
        self.db.execute("UPDATE monitor_activators SET deleted_at = ?, updated_at = ? WHERE id = ?",
                        (_now(), _now(), found["id"]))
        self._said(found["id"], by, "delete", why, {k: found[k] for k in ("name", "when", "then")},
                   None)

    def switch(self, ident: str, on: bool) -> Dict[str, Any]:
        """The user's own switch - on or off (the only manual control)."""
        found = self.get(ident)
        if found is None:
            raise ValueError(f"there is no Activator {ident!r}")
        self.db.execute("UPDATE monitor_activators SET enabled = ?, updated_at = ? WHERE id = ?",
                        (int(bool(on)), _now(), found["id"]))
        self._said(found["id"], "user", "on" if on else "off", "", None, None)
        return self.get(ident)

    def protect(self, ident: str, protected: bool) -> Dict[str, Any]:
        """The user's: protected, the model can neither change nor delete it."""
        found = self.get(ident)
        if found is None:
            raise ValueError(f"there is no Activator {ident!r}")
        if self._fixed(found) and not protected:
            raise ValueError(BUILT_IN_FIXED)
        self.db.execute("UPDATE monitor_activators SET protected = ?, updated_at = ? WHERE id = ?",
                        (int(bool(protected)), _now(), found["id"]))
        self._said(found["id"], "user", "protect" if protected else "unprotect", "", None, None)
        return self.get(ident)

    def changes(self, ident: str = "", limit: int = 100) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM monitor_changes"
        params: Tuple[Any, ...] = ()
        if ident:
            sql += " WHERE activator_id = ?"
            params = (ident,)
        rows = self.db.query(sql + " ORDER BY at DESC, rowid DESC LIMIT ?", params + (limit,))
        out = []
        for row in rows:
            one = dict(row)
            for key in ("before", "after"):
                one[key] = json.loads(one[key]) if one.get(key) else None
            out.append(one)
        return out

    def seed(self, builtins: List[Dict[str, Any]]) -> int:
        """The built-in ones, each written once: one the model or the user
        deleted is not brought back."""
        made = 0
        for one in builtins:
            key = "monitor.seeded." + one["builtin"]
            row = self.db.query_one("SELECT value FROM kv WHERE key = ?", (key,))
            if row and row["value"]:
                continue
            data = {k: v for k, v in one.items() if k not in ("builtin", "protected")}
            self.add(data, "built in", "built in", builtin=one["builtin"],
                     protected=bool(one.get("protected")))
            self.db.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO "
                            "UPDATE SET value = excluded.value",
                            (key, datetime.now(timezone.utc).isoformat()))
            made += 1
        return made

    def retire(self, builtin: str, why: str) -> int:
        """A built-in one taken out of the application: gone from those kept, as a delete is; what
        it did stays on the record. ONLY WHERE NO ONE CHANGED IT (the user, 2026-11-07: what the
        application gives is the default, what the user adds or changes is theirs): one the user
        or the model made their own stays, theirs to keep or delete."""
        rows = self.db.query("SELECT id FROM monitor_activators WHERE builtin = ? AND "
                             "COALESCE(deleted_at, '') = ''", (builtin,))
        gone = 0
        for row in rows:
            if self._untouched(row["id"]):
                self.delete(row["id"], "built in", why)
                gone += 1
        return gone

    def _untouched(self, ident: str) -> bool:
        """Whether only the application ever wrote it: no change by the user or the model. THEIR
        NAME FOR IT IS NOT A CHANGE (the user, 2026-11-07: "display name 只是user level的设定"): a
        built-in one renamed still follows the application's defaults."""
        return self.db.query_one("SELECT 1 FROM monitor_changes WHERE activator_id = ? AND "
                                 "by != 'built in' AND op != 'rename' LIMIT 1", (ident,)) is None

    def refresh(self, builtins: List[Dict[str, Any]]) -> int:
        """A BUILT-IN ONE'S DEFAULT CHANGED, the copies kept of it follow - where no one changed
        them (the user, 2026-11-07: one written when its cooldown was 30 minutes kept 30 after the
        application said 5). FIXED NOW (the user, 2026-11-13: "都是protected, 不允许改"): every copy follows -
        what is the user's of it, its switch and their name for it, is kept apart and stays."""
        changed = 0
        for one in builtins:
            fresh, _warnings = check({k: v for k, v in one.items()
                                      if k not in ("builtin", "protected")}, builtin=True)
            for row in self.db.query("SELECT id, data FROM monitor_activators WHERE builtin = ? "
                                     "AND COALESCE(deleted_at, '') = ''", (one["builtin"],)):
                kept = json.loads(row["data"] or "{}")
                if kept == fresh:
                    continue
                self.db.execute("UPDATE monitor_activators SET data = ?, updated_at = ? WHERE id = ?",
                                (json.dumps(fresh, ensure_ascii=False), _now(), row["id"]))
                self._said(row["id"], "built in", "edit", "the application's default changed",
                           kept, fresh)
                changed += 1
        return changed

    def seed_quiet(self, rules: Any) -> int:
        """The built-in rules of quiet, each written once: one taken away is not
        brought back."""
        made = 0
        for key, name, when in rules:
            seeded = "monitor.seeded.quiet." + key
            row = self.db.query_one("SELECT value FROM kv WHERE key = ?", (seeded,))
            if row and row["value"]:
                continue
            self.add_quiet(name, when, "built in", "built in")
            self.db.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO "
                            "UPDATE SET value = excluded.value", (seeded, _now()))
            made += 1
        return made

    def reword(self, builtin: str, pairs: Any) -> int:
        """Words of a built-in one already kept changed, where they are still
        the words it was given: what the user or the model wrote stays."""
        changed = 0
        for row in self.db.query("SELECT id, data FROM monitor_activators WHERE builtin = ?",
                                 (builtin,)):
            data = row["data"]
            for old, new in pairs:
                data = data.replace(json.dumps(old, ensure_ascii=False),
                                    json.dumps(new, ensure_ascii=False))
            if data != row["data"]:
                self.db.execute("UPDATE monitor_activators SET data = ? WHERE id = ?",
                                (data, row["id"]))
                changed += 1
        return changed

    def retier(self, builtin: str, old: Any, new: Any) -> int:
        """What a built-in one does changed, where it still does what it was given:
        what the user or the model made of it stays."""
        changed = 0
        for row in self.db.query("SELECT id, data FROM monitor_activators WHERE builtin = ?",
                                 (builtin,)):
            data = json.loads(row["data"] or "{}")
            if data.get("then") == old:
                data["then"] = new
                self.db.execute("UPDATE monitor_activators SET data = ?, updated_at = ? WHERE id = ?",
                                (json.dumps(data, ensure_ascii=False), _now(), row["id"]))
                changed += 1
        return changed

    def for_the_model(self) -> List[Dict[str, Any]]:
        """What the model is shown of them."""
        return [{key: one.get(key) for key in ("id", "name", "purpose", "group", "when", "then",
                                               "cooldown_minutes", "most_per_day", "delivery",
                                               "enabled", "protected", "builtin")}
                for one in self.list()]


__all__ = ["ActivatorStore", "check", "evaluate", "tier_for", "text_driven", "GROUPS",
           "RULES_FOR_THE_MODEL", "LANGUAGES"]
