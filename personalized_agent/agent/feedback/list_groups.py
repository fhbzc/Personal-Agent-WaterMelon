"""THE TO-DO LIST AND WORK ASSIGNED, EACH IN GROUPS (the user, 2026-11-03:
"你所有的to do, 和assigned work, 都会被自动group (有addition/edit就自动触发
用代码group)").

Each list on its own (the user's choice): its items under a few high-level
names, made by a model - by code, at once, whenever an item is added or its
words change (`notice`, from the lists' own reads; a few seconds' wait so
that several changes are grouped once).

THE USER MOVES WHAT IT MADE: renames a group, takes an item out of one,
puts one in another or in a new one (`rename`, `move`). Each change is
kept in words - "Renamed the group "Reviews" to "ICLR reviews"" - the 100
newest of each list (older ones deleted), and handed to the next grouping
as THE USER'S PREFERENCES, put above everything else (the user: "告诉model
human的preference 优先"). Only the model is told: the code does not hold
an item where the user put it. EACH IS A LESSON TOO (the user, 2026-11-04),
and a grouping is told the lessons like its items, so what was done to one
list, or to a project's name, bears on the other.

OTHERS, AND GROUPS OF THEIR OWN (the user, 2026-11-03): what fits no group
well is in "Others", always last, never renamed or deleted - an item taken
out of a group goes there. A group can be added with nothing in it, and a
group emptied stays, until the user deletes it (its items to Others) or the
next grouping finds nothing for it.

Its model calls are Task organisation's (common/modules.py, "list_grouping"),
and none is made while that is switched off: the lists are shown as they
were last grouped, anything new under no heading.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Callable, Dict, List, Optional

from ...common.events.models import new_id, utcnow
from ...common.languages import in_display

log = logging.getLogger(__name__)

LISTS = ("todo", "assigned", "collection")
#: The user's changes kept for each list, the newest (the user: "最多留100条
#: ... 久远的就删掉").
KEEP_CHANGES = 100
#: How long after a change the grouping waits, for more to come.
WAIT_SECONDS = 4.0
#: Fewer items than this are not grouped.
LEAST_ITEMS = 2
#: What fits no group well (the user, 2026-11-03: "有一个others group, 没有特别好
#: group的都放里面"): always the last group; an item the model left out goes
#: here when it had no group before.
OTHER = "Others"

SCHEMA = """
CREATE TABLE IF NOT EXISTS list_groupings (
    list      TEXT PRIMARY KEY,           -- todo | assigned
    groups    TEXT NOT NULL DEFAULT '[]', -- [{"name": ..., "items": [ids]}]
    loose     TEXT NOT NULL DEFAULT '[]', -- (before Others) ids taken out of every group
    seen      TEXT NOT NULL DEFAULT '{}', -- {id: words} as last grouped
    made_at   TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS list_group_changes (
    id    TEXT PRIMARY KEY,
    list  TEXT NOT NULL,
    at    TEXT NOT NULL,
    said  TEXT NOT NULL                   -- the change, in words, for the model
);
CREATE INDEX IF NOT EXISTS idx_list_group_changes ON list_group_changes(list, at);
"""

WHAT = {"todo": "the user's to-do list - what they mean to do themselves",
        "assigned": "the work the user handed to their agent to do",
        "collection": "the articles and recommended content the user kept to come back to"}

#: KEEP THE GROUPING THERE IS (the user, 2026-11-03: "尽可能保留之前的分组，除非非常
#: 有必要，尽可能只把新的或者edit的content放到之前已有的组别里"): told, with each
#: item marked new or edited - only the model is told, the code holds nothing.
SYSTEM = """You put the items of {what} under a few HIGH-LEVEL NAMES, so the
list reads at a glance.

- Every item goes in exactly one group; use each item's id exactly as given.
- A few groups (two to seven, fewer for a short list); a group of one item is
  fine when nothing else is like it.
- What fits no group well goes in the group named exactly "{others}", listed
  last. Do not make a group of things that only have nothing else in common.
- Each name is short - one to four words - and says what its items have in
  common (a project, a person, a kind of work).
- KEEP THE GROUPING THERE IS. Every group in "the_groups_now" keeps its name
  and its items. Only the items marked "new" or "edited" are placed: each
  into the group already there that it fits (an edited one stays where it
  is when it still fits there) - or into "{others}". Make a new group, rename
  one, or move an item that was not changed ONLY when it is really
  necessary - when the list would otherwise read wrongly - never to tidy it.
- A group the user made with nothing in it yet is theirs: put in it what
  belongs there. A group with no item left is dropped - leave it out.

THE USER'S OWN CHANGES COME FIRST - ABOVE EVERYTHING ABOVE. They are listed,
the newest last: names they gave, items they put in a group, took out of
one, or put in a new one. Follow them: keep the names they gave, keep apart
what they took apart, and group new items the way they would. Where two
of their changes disagree, the newer wins. An item they put in "{others}"
stays there unless a newer change says otherwise. "what_the_user_did_elsewhere",
when given, is how they named and grouped other things: group the way it shows,
after their changes here.

Answer with JSON alone:
{{"groups": [{{"name": "...", "items": ["<id>", ...]}}, ..., {{"name": "{others}", "items": [...]}}]}}"""


def _words(item: Dict[str, Any]) -> str:
    return " ".join(f"{item.get('text') or ''} {item.get('detail') or ''}".split())[:400]


class ListGroups:
    """The groupings of the two lists, and the user's changes to them."""

    def __init__(self, db: Any, models: Callable[[], Any], items: Dict[str, Callable[[], List]],
                 allowed: Callable[[], bool] = lambda: True) -> None:
        self.db = db
        self._models = models
        self._items = items
        self._allowed = allowed
        self._lock = threading.Lock()
        self._timers: Dict[str, threading.Timer] = {}
        self._running: Dict[str, bool] = {}
        self._again: Dict[str, bool] = {}
        #: Each list's count of the user's changes to its groups: one made
        #: while a grouping runs is not written over by it.
        self._version: Dict[str, int] = {}
        #: Set by the application: where what the user does is kept, and found.
        self.lessons: Any = None
        self.retriever: Any = None
        with self.db.write() as conn:
            conn.executescript(SCHEMA)

    # -- what a list shows ---------------------------------------------------------------
    def _kept(self, which: str) -> Dict[str, Any]:
        row = self.db.query_one("SELECT * FROM list_groupings WHERE list = ?", (which,))
        if row is None:
            return {"groups": [], "loose": [], "seen": {}, "made_at": ""}
        return {"groups": json.loads(row["groups"] or "[]"), "loose": json.loads(row["loose"] or "[]"),
                "seen": json.loads(row["seen"] or "{}"), "made_at": row["made_at"]}

    def _keep(self, which: str, groups: List[Dict[str, Any]], loose: List[str],
              seen: Optional[Dict[str, str]] = None) -> None:
        kept = self._kept(which)
        self.db.execute(
            "INSERT INTO list_groupings (list, groups, loose, seen, made_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(list) DO UPDATE SET groups = excluded.groups, loose = excluded.loose, "
            "seen = excluded.seen, made_at = excluded.made_at",
            (which, json.dumps(groups, ensure_ascii=False), json.dumps(loose),
             json.dumps(kept["seen"] if seen is None else seen, ensure_ascii=False),
             utcnow().isoformat() if seen is not None else kept["made_at"]))

    def view(self, which: str, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """THE LIST IN ITS GROUPS, as it is now: the groups of the items there
        are, the ones the user took out, and the ones grouped yet by no one
        ("new"); whether a grouping is on its way. Noticed, too: a change
        starts one."""
        self.notice(which, items)
        kept = self._kept(which)
        here = [str(one.get("id")) for one in items]
        present = set(here)
        placed = set()
        groups, others = [], []
        for group in kept["groups"]:
            ids = [one for one in group.get("items") or [] if one in present and one not in placed]
            placed.update(ids)
            if group.get("name") == OTHER:
                others.extend(ids)
            else:                                # kept when emptied: the user deletes it
                groups.append({"name": str(group.get("name") or ""), "items": ids})
        others.extend(one for one in kept["loose"] if one in present and one not in placed)
        placed.update(others)
        if groups or others:
            groups.append({"name": OTHER, "items": others, "others": True})
        return {"groups": groups, "loose": [], "others": OTHER,
                "new": [one for one in here if one not in placed],
                "grouping": bool(self._running.get(which) or which in self._timers)}

    # -- grouping, at once on a change ---------------------------------------------------
    def notice(self, which: str, items: List[Dict[str, Any]]) -> None:
        """An item added, gone or reworded since the last grouping: a new
        one, in a few seconds."""
        seen = {str(one.get("id")): _words(one) for one in items}
        if not self._allowed():
            return
        with self._lock:
            # Compared HERE, under the lock a grouping keeps its result under: before it, a
            # grouping ending in between was taken for one still to do, and asked for again.
            if seen == self._kept(which)["seen"]:
                return
            if self._running.get(which):
                self._again[which] = True
                return
            if which in self._timers:
                return
            timer = threading.Timer(WAIT_SECONDS, self._group_now, args=(which,))
            timer.daemon = True
            self._timers[which] = timer
            timer.start()

    def _group_now(self, which: str) -> None:
        with self._lock:
            self._timers.pop(which, None)
            self._running[which] = True
        try:
            self.group(which)
        except Exception:
            log.exception("the %s list could not be grouped", which)
        finally:
            with self._lock:
                self._running[which] = False
                self._again.pop(which, None)
        # CHANGED WHILE IT WAS GROUPED (the user, 2026-11-03: "如果我在上一次to do
        # grouping的时候edit了，grouping好之后要再做一次grouping"): the list as it is
        # now looked at again - an item added or edited meanwhile, or a change
        # of the user's that this grouping did not write over, is grouped again.
        self.notice(which, self._items[which]())

    def group(self, which: str) -> Dict[str, Any]:
        """One grouping of a list - by the model, the user's changes first."""
        items = list(self._items[which]())
        seen = {str(one.get("id")): _words(one) for one in items}
        kept = self._kept(which)
        version = self._version.get(which, 0)
        if len(items) < LEAST_ITEMS:
            self._keep(which, [], [one for one in kept["loose"] if one in seen], seen)
            return {"groups": [], "loose": []}
        from ...models.base import ModelRequest
        # WHAT CHANGED since the last grouping: the items to place - the
        # others stay where they are (SYSTEM).
        before = kept["seen"]

        def changed(ident: str) -> str:
            if not kept["groups"] and not kept["loose"]:
                return ""                        # the first grouping: all are placed
            if ident not in before:
                return "new"
            return "edited" if before[ident] != seen[ident] else ""

        def part_of(ident: str) -> Dict[str, str]:
            links = getattr(self, "item_links", None)
            note = links.note(which, ident) if links is not None else ""
            return {"part_of": note} if note else {}
        handed = {
            "items": [dict({"id": str(one.get("id")), "text": str(one.get("text") or ""),
                            "detail": str(one.get("detail") or "")},
                           **part_of(str(one.get("id"))),
                           **({"changed": changed(str(one.get("id")))}
                              if changed(str(one.get("id"))) else {})) for one in items],
            # The groups as they are now - only the items still in the list;
            # a group emptied is there, with none.
            "the_groups_now": [dict(group, items=[one for one in group["items"] if one in seen])
                               for group in self._with_others(kept)],
            "the_users_changes_newest_last": [f"{one['at'][:16].replace('T', ' ')} - {one['said']}"
                                              for one in self.changes(which)]}
        if self.retriever is not None:
            elsewhere = self.retriever.the_users_own(
                " ".join(sorted(seen.values()))[:2000], "list_grouping")
            if elsewhere:
                handed["what_the_user_did_elsewhere"] = elsewhere
        response = self._models().invoke(ModelRequest(
            messages=[{"role": "user", "content": json.dumps(handed, ensure_ascii=False,
                                                             indent=1)[:60000]}],
            system=SYSTEM.format(what=WHAT[which], others=OTHER), max_tokens=3000,
            task="list_grouping",
            language=in_display("each group's name - but \"Others\" stays exactly as it is")),
            role="heartbeat")
        from ...heartbeat.states.heartbeat import _as_json
        said = _as_json(getattr(response, "text", "") or "") or {}
        groups = self._checked(said, seen, kept)
        with self._lock:
            if self._version.get(which, 0) != version:
                # The user moved the groups meanwhile: theirs stands, and the
                # list is grouped again with their change told first (what
                # was seen is not saved, so the next look notices it).
                self._again[which] = True
                return {"groups": self._kept(which)["groups"], "again": True}
            self._keep(which, groups, [], seen)
        return {"groups": groups}

    @staticmethod
    def _with_others(kept: Dict[str, Any]) -> List[Dict[str, Any]]:
        """The groups kept, what was taken out of every group (before Others)
        in Others, Others last."""
        groups = [dict(group, items=list(group.get("items") or [])) for group in kept["groups"]]
        others = next((group for group in groups if group.get("name") == OTHER), None)
        if others is None and kept["loose"]:
            others = {"name": OTHER, "items": []}
            groups.append(others)
        if others is not None:
            others["items"].extend(one for one in kept["loose"] if one not in others["items"])
            groups.remove(others)
            groups.append(others)
        return groups

    @staticmethod
    def _checked(said: Dict[str, Any], seen: Dict[str, str],
                 kept: Dict[str, Any]) -> tuple:
        """What the model said, held to the rules by code: ids that are
        there, each once; a group with none dropped; one it left out goes
        where it was, else in Others - last."""
        placed, groups = set(), []
        for group in said.get("groups") or []:
            if not isinstance(group, dict):
                continue
            name = " ".join(str(group.get("name") or "").split())[:60]
            if not name:
                continue
            ids = []
            for one in group.get("items") or []:
                if str(one) in seen and str(one) not in placed:
                    placed.add(str(one))
                    ids.append(str(one))
            if not ids:
                continue
            same = next((one for one in groups if one["name"] == name), None)
            if same is not None:
                same["items"].extend(ids)
            else:
                groups.append({"name": name, "items": ids})
        def others() -> Dict[str, Any]:
            found = next((group for group in groups if group["name"] == OTHER), None)
            if found is None:
                found = {"name": OTHER, "items": []}
                groups.append(found)
            return found
        # "loose", as it was answered before Others: in Others.
        for one in said.get("loose") or []:
            if str(one) in seen and str(one) not in placed:
                others()["items"].append(str(one))
                placed.add(str(one))
        for one in seen:
            if one in placed:
                continue
            where = next((group["name"] for group in kept["groups"]
                          if one in (group.get("items") or [])), "")
            target = next((group for group in groups if group["name"] == where), None)
            (target or others())["items"].append(one)
            placed.add(one)
        last = next((group for group in groups if group["name"] == OTHER), None)
        if last is not None:
            groups.remove(last)
            if last["items"]:
                groups.append(last)
        return groups

    # -- the user's changes --------------------------------------------------------------
    def changes(self, which: str) -> List[Dict[str, Any]]:
        """The user's changes to a list's groups, the oldest first."""
        return list(reversed(self.db.query(
            "SELECT * FROM list_group_changes WHERE list = ? ORDER BY at DESC, rowid DESC LIMIT ?",
            (which, KEEP_CHANGES))))

    def _said(self, which: str, said: str) -> None:
        with self._lock:
            self._version[which] = self._version.get(which, 0) + 1
        self.db.execute("INSERT INTO list_group_changes (id, list, at, said) VALUES (?, ?, ?, ?)",
                        (new_id("lgc"), which, utcnow().isoformat(), said))
        # THE NEWEST 100 KEPT, older ones deleted.
        self.db.execute(
            "DELETE FROM list_group_changes WHERE list = ? AND id NOT IN (SELECT id FROM "
            "list_group_changes WHERE list = ? ORDER BY at DESC, rowid DESC LIMIT ?)",
            (which, which, KEEP_CHANGES))
        if self.lessons is not None:
            try:
                self.lessons.the_user_did("moved_list_groups", f"In {WHAT[which]}: {said}")
            except Exception:
                log.warning("what the user did could not be kept as a lesson", exc_info=True)

    def _text_of(self, which: str, item: str) -> str:
        for one in self._items[which]():
            if str(one.get("id")) == item:
                return " ".join(str(one.get("text") or "").split())[:120]
        return item

    def rename(self, which: str, old: str, new: str) -> Dict[str, Any]:
        old, new = " ".join(str(old or "").split()), " ".join(str(new or "").split())[:60]
        if which not in LISTS or not new:
            raise ValueError("a group needs a name")
        if old == OTHER:
            raise ValueError(f'"{OTHER}" is not renamed')
        kept = self._kept(which)
        found = next((group for group in kept["groups"] if group["name"] == old), None)
        if found is None:
            raise ValueError(f"there is no group called {old!r}")
        if new == old:
            return self.view(which, self._items[which]())
        other = next((group for group in kept["groups"] if group["name"] == new), None)
        if other is not None:                    # renamed to one there is: the two are one
            other["items"].extend(one for one in found["items"] if one not in other["items"])
            kept["groups"].remove(found)
        else:
            found["name"] = new
        self._keep(which, kept["groups"], kept["loose"])
        self._said(which, f'Renamed the group "{old}" to "{new}".')
        return self.view(which, self._items[which]())

    def move(self, which: str, item: str, to: str) -> Dict[str, Any]:
        """An item put in a group - one there is, or a new one - or, `to`
        empty or Others, in Others. The group it leaves stays, emptied."""
        if which not in LISTS:
            raise ValueError("no such list")
        item, to = str(item or ""), " ".join(str(to or "").split())[:60] or OTHER
        words = self._text_of(which, item)
        kept = self._kept(which)
        kept["groups"] = self._with_others(kept)
        was = ""
        for group in kept["groups"]:
            if item in group["items"]:
                was = group["name"]
                group["items"].remove(item)
        target = next((group for group in kept["groups"] if group["name"] == to), None)
        made = target is None
        if made:
            target = {"name": to, "items": []}
            at = next((n for n, group in enumerate(kept["groups"]) if group["name"] == OTHER),
                      len(kept["groups"]))
            kept["groups"].insert(at, target)
        target["items"].append(item)
        if to == OTHER:
            said = (f'Took "{words}" out of the group "{was}" and put it in "{OTHER}" - it fits '
                    f'none of the groups.' if was and was != OTHER
                    else f'Put "{words}" in "{OTHER}".')
        else:
            said = (f'Made a new group "{to}" and put "{words}" in it' if made
                    else f'Put "{words}" in the group "{to}"')
            said += f' (it was in "{was}").' if was else "."
        self._keep(which, kept["groups"], [])
        self._said(which, said)
        return self.view(which, self._items[which]())

    def add_group(self, which: str, name: str) -> Dict[str, Any]:
        """A group of the user's, with nothing in it yet (the user: "单纯add
        group")."""
        name = " ".join(str(name or "").split())[:60]
        if which not in LISTS or not name:
            raise ValueError("a group needs a name")
        kept = self._kept(which)
        kept["groups"] = self._with_others(kept)
        if name == OTHER or any(group["name"] == name for group in kept["groups"]):
            raise ValueError(f'there is a group called "{name}" already')
        at = next((n for n, group in enumerate(kept["groups"]) if group["name"] == OTHER),
                  len(kept["groups"]))
        kept["groups"].insert(at, {"name": name, "items": []})
        self._keep(which, kept["groups"], [])
        self._said(which, f'Made a new group "{name}", with nothing in it yet.')
        return self.view(which, self._items[which]())

    def delete_group(self, which: str, name: str) -> Dict[str, Any]:
        """A group deleted by the user: what was in it, in Others."""
        name = " ".join(str(name or "").split())
        if which not in LISTS:
            raise ValueError("no such list")
        if name == OTHER:
            raise ValueError(f'"{OTHER}" is not deleted')
        kept = self._kept(which)
        kept["groups"] = self._with_others(kept)
        found = next((group for group in kept["groups"] if group["name"] == name), None)
        if found is None:
            raise ValueError(f"there is no group called {name!r}")
        kept["groups"].remove(found)
        present = {str(one.get("id")) for one in self._items[which]()}
        moving = [one for one in found["items"] if one in present]
        if moving:
            others = next((group for group in kept["groups"] if group["name"] == OTHER), None)
            if others is None:
                others = {"name": OTHER, "items": []}
                kept["groups"].append(others)
            others["items"].extend(moving)
        self._keep(which, kept["groups"], [])
        self._said(which, f'Deleted the group "{name}"' + (
            f' - its {len(moving)} item(s) put in "{OTHER}".' if moving else "."))
        return self.view(which, self._items[which]())


__all__ = ["ListGroups", "LISTS", "KEEP_CHANGES", "OTHER"]
