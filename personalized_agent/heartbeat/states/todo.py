"""The next steps on a piece of work, kept between rounds.

Only the TOP of what the user sees carries a list: a project, or a task
that is in no project. A task inside a project is a part of something the
project already describes, and two lists for one piece of work would be
two answers to "what is next" that disagree.

The list is the model's, and the user's as well. The model writes it each
time it describes the work; the user may add a step, delete one, or mark
one finished in between, and what they changed is shown to the next call
once - which follows it where it can and says so where it does not. So the list lives
in the work's own description, `described["todo"]`, and the user's edits
since the last answer in `described["todo_changes"]`.

ONE WRITER FOR THESE KEYS. A round holds its tasks in memory for minutes
and saves them as it goes, and the user's edits arrive through the page
in between. Had the round's save written its own copy of the list, an
edit made while a round ran would be undone by the next save without
anyone noticing - so every save carries forward whatever the row has for
these keys (`carried`), and only `change` writes them, under one lock.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

log = logging.getLogger(__name__)

#: What a description holds about its next steps. Written only by `change`.
KEYS = ("todo", "todo_changes", "todo_note", "todo_at", "todo_asking")

#: How many steps the model may give. Next steps, not a plan: a list
#: longer than this has stopped being what to do next.
MOST = 8
#: How long one step may be. A step is a line, not a paragraph.
LONGEST = 300

#: Held while a row's description is read and written back, by `change`
#: and by the saves that carry these keys forward - so the two cannot
#: interleave and drop an edit between a read and a write.
LOCK = threading.RLock()

#: The rules, sent beside the instruction wherever a list is asked for.
#: `{what}` is "project" or "task".
TODO_PROMPT = """AND ITS NEXT STEPS: A TO-DO LIST FOR THIS {WHAT}.

This {what} is at the top of what the user sees - nothing above it holds
it - so it carries a short list of what still needs doing on it NOW. The
message shows THE NEXT STEPS SO FAR, the list as it stands, with the
user's own changes already made to it, and WHAT THE USER CHANGED SINCE
the list was last written: steps they added themselves, steps they
deleted, and steps they marked finished. Write the new list by these four
rules:

  1. FOLLOW THE USER'S CHANGES AS FAR AS YOU CAN. A step they added
     belongs in the list; a step they deleted stays out of it. You may
     go against them - a step they added that the states show is already
     done, say - but only for a reason, and the reason goes in
     `todo_note`.

  2. A STEP THAT IS ALREADY DONE IS LEFT OUT. Done means the states show
     it finished, not that it has gone quiet - or the user marked it
     finished, which they know better than the states do - or the agent
     heartbeat ticked it, from what it saw the user do, and the user has
     not taken the tick back. Keep one that was ticked only for a clear
     reason, given in `todo_note`.

  3. NEW THINGS TO DO ARE ADDED: what the states show still needs doing
     that the list does not have yet.

  4. A STEP NOT YET DONE KEEPS EXACTLY THE SAME WORDS. Copy it character
     for character: the list is matched by its words, and a step that is
     reworded reads as a new one and loses its place. Reword it only when
     it has really changed - partly done, so that what is left is smaller
     - and then say what is left.

NEXT STEPS, NOT LONG-TERM GOALS. Each one is something the user could sit
down and do next - "Re-run scripts/fetch.py against the fresh census
pull", not "Finish the replication". An imperative, the particulars
named, one step to a line, at most {most} of them, in the order they
would be done. An empty list is the right answer when nothing is left to
do. Write the steps in the language the rest of your answer is written
in; paths and names keep their form.

Add these two fields to the JSON object you return. Send `todo` WHOLE,
EVERY TIME - including when nothing in it changed, and including when
you leave every other part out:
  "todo": ["the next steps, each an imperative, in the order they would
           be done"],
  "todo_note": "one sentence, only where you went against something the
                user changed: what, and why. Leave it out otherwise.\""""


def rules(what: str) -> str:
    """The rules for a list, about a project or a task."""
    return (TODO_PROMPT.replace("{WHAT}", what.upper())
            .replace("{what}", what).replace("{most}", str(MOST)))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _same(text: Any) -> str:
    """A step as it is matched: its words, not its spacing or capitals."""
    return " ".join(str(text or "").split()).casefold()


def _clean(text: Any) -> str:
    return " ".join(str(text or "").split())[:LONGEST]


def items(described: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The steps a description holds, each with an id and its text."""
    out: List[Dict[str, Any]] = []
    for one in (described or {}).get("todo") or []:
        if isinstance(one, dict) and _clean(one.get("text")):
            out.append(dict(one))
    return out


#: The kinds of change made since the list was last written, in the order
#: they are told: the user's - and the steps the agent heartbeat ticked,
#: each from what it saw (`ticked`), which the user may take back.
CHANGES = ("added", "removed", "finished", "agent_finished")


def changes(described: Optional[Dict[str, Any]]) -> Dict[str, List[str]]:
    """What the user added, deleted and marked finished since the last
    answer."""
    held = (described or {}).get("todo_changes") or {}
    if not isinstance(held, dict):
        held = {}
    return {kind: [_clean(one) for one in held.get(kind) or [] if _clean(one)]
            for kind in CHANGES}


def pending(described: Optional[Dict[str, Any]]) -> bool:
    """Whether the user changed the list since the model last saw it."""
    said = changes(described)
    return any(said[kind] for kind in CHANGES)


def snapshot(described: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The list and the user's changes, as a call is about to be shown them.

    Kept with the call, because what is cleared when its answer lands is
    exactly what it was shown - an edit the user makes while the call is
    out was not, and must reach the next one.
    """
    said = changes(described)
    return {"todo": items(described), "at": _now(),
            **{kind: list(said[kind]) for kind in CHANGES}}


def told(shown: Dict[str, Any]) -> str:
    """The part of the message a list is written from."""
    lines = ["THE NEXT STEPS SO FAR:"]
    steps = shown.get("todo") or []

    def ticked(one: Dict[str, Any]) -> str:
        if not one.get("done"):
            return ""
        if one.get("done_by") == "agent":
            return (" (the agent heartbeat marked this finished: "
                    f"{one.get('done_why') or 'from what it saw'})")
        return " (the user marked this finished)"

    lines += ([f"- {one.get('text')}{ticked(one)}" for one in steps]
              or ["(none yet - this is the first list for it)"])
    whys = {_same(one.get("text")): one.get("done_why") for one in steps
            if one.get("done_by") == "agent"}
    lines += ["", "WHAT CHANGED SINCE:"]
    if any(shown.get(kind) for kind in CHANGES):
        for kind, heading in (("added", "the user added:"),
                              ("removed", "the user removed:"),
                              ("finished", "the user marked finished:"),
                              ("agent_finished", "the agent heartbeat marked finished, from "
                               "what it saw the user do - and the user has not taken it back:")):
            lines.append(heading)
            lines += ([f"- {one}" + (f" - because: {whys[_same(one)]}"
                                     if kind == "agent_finished" and whys.get(_same(one))
                                     else "")
                       for one in shown.get(kind) or []]
                      or ["  (nothing)"])
    else:
        lines.append("nothing - the list has not changed since it was "
                     "written.")
    return "\n".join(lines)


def read_answer(answered: Any) -> Optional[List[str]]:
    """The steps an answer gave, or None when it gave none at all.

    None and an empty list are different answers: an empty list says
    nothing is left to do, and a missing one says nothing about the list -
    which is then kept as it was.
    """
    if not isinstance(answered, dict) or "todo" not in answered:
        return None
    said = answered.get("todo")
    if isinstance(said, str):
        said = [said]
    if not isinstance(said, list):
        return None
    out: List[str] = []
    for one in said:
        text = _clean(one.get("text") if isinstance(one, dict) else one)
        if text and _same(text) not in {_same(had) for had in out}:
            out.append(text)
    return out[:MOST]


def _take(listed: List[str], gone: Sequence[str]) -> List[str]:
    """`listed` with one of each of `gone` taken out, by its words."""
    out = list(listed)
    for one in gone:
        for at, had in enumerate(out):
            if _same(had) == _same(one):
                del out[at]
                break
    return out


def answered(described: Dict[str, Any], shown: Optional[Dict[str, Any]],
             said: Optional[List[str]], note: str = "",
             failed: bool = False) -> Dict[str, Any]:
    """The keys a description has after a call that was shown `shown`.

    `described` is what the row holds NOW, which may include edits the
    user made while the call was out. Those were not shown, so they stay
    pending - and are laid over the new list, so a step the user added a
    moment ago does not vanish from the page until the next round.

    A call that FAILED - refused, never answered - was not shown anything,
    so nothing is cleared. A call that answered and gave no list cleared
    what it was shown all the same: it saw the changes, and they are not
    shown twice.
    """
    out = {key: described.get(key) for key in KEYS if key in described}
    out["todo_asking"] = None
    if failed or shown is None:
        return out
    now = _now()
    held = changes(described)
    added = _take(held["added"], shown.get("added") or [])
    removed = _take(held["removed"], shown.get("removed") or [])
    finished = _take(held["finished"], shown.get("finished") or [])
    agent_finished = _take(held["agent_finished"], shown.get("agent_finished") or [])
    before = items(described)
    seen_done = {_same(one) for one in list(shown.get("finished") or [])
                 + list(shown.get("agent_finished") or [])}
    if said is None:
        # NO LIST, SO THE OLD ONE STANDS - less what the user finished and
        # the call was told about: that is done, and an answer that said
        # nothing about the list is no reason to keep it.
        steps = [one for one in before if not (
            one.get("done") and _same(one.get("text")) in seen_done)]
    else:
        known: Dict[str, Dict[str, Any]] = {}
        for one in list(shown.get("todo") or []) + before:
            known[_same(one.get("text"))] = one
        by_user = {_same(one) for one in
                   list(shown.get("added") or []) + added}
        steps = []
        for text in said:
            was = known.get(_same(text))
            if was is not None:
                # THE SAME STEP: its id, who put it there and since when.
                steps.append({"id": was.get("id") or _an_id(),
                              "text": str(was.get("text") or text),
                              "by": was.get("by") or "agent",
                              "since": was.get("since") or now,
                              **({"note": was["note"]} if was.get("note")
                                 else {})})
            else:
                steps.append({"id": _an_id(), "text": text,
                              "by": "user" if _same(text) in by_user
                              else "agent", "since": now})
        # WHAT THE USER DID WHILE THE CALL WAS OUT, laid on top.
        listed = {_same(one["text"]) for one in steps}
        for text in added:
            if _same(text) in listed:
                continue
            had = next((one for one in before
                        if _same(one.get("text")) == _same(text)), None)
            steps.append(had or {"id": _an_id(), "text": text, "by": "user",
                                 "since": now})
            listed.add(_same(text))
        gone = {_same(one) for one in removed}
        steps = [one for one in steps if _same(one["text"]) not in gone]
        # And what was finished while it was out stays ticked - by whom.
        ticks = {_same(one.get("text")): one for one in before if one.get("done")}
        done_now = {_same(one) for one in finished + agent_finished}
        for one in steps:
            if _same(one["text"]) in done_now:
                one["done"] = True
                had = ticks.get(_same(one["text"])) or {}
                for key in ("done_at", "done_by", "done_why", "done_evidence"):
                    if had.get(key):
                        one[key] = had[key]
        out["todo_note"] = _clean(note)
    out["todo"] = steps
    out["todo_changes"] = {"added": added, "removed": removed,
                           "finished": finished, "agent_finished": agent_finished}
    out["todo_at"] = now
    return out


def _an_id() -> str:
    return f"todo-{uuid.uuid4().hex[:10]}"


def added(described: Dict[str, Any], text: str) -> Dict[str, Any]:
    """The keys after the user adds a step. It shows at once."""
    text = _clean(text)
    if not text:
        raise ValueError("a step needs some words")
    out = {key: described.get(key) for key in KEYS if key in described}
    steps = items(described)
    if any(_same(one.get("text")) == _same(text) for one in steps):
        return out
    held = changes(described)
    asking = described.get("todo_asking") or {}
    shown_removed = {_same(one) for one in asking.get("removed") or []}
    if (_same(text) in {_same(one) for one in held["removed"]}
            and _same(text) not in shown_removed):
        # DELETED AND PUT BACK before anything saw either: no change.
        held["removed"] = _take(held["removed"], [text])
    else:
        held["added"].append(text)
    steps.append({"id": _an_id(), "text": text, "by": "user",
                  "since": _now()})
    out["todo"] = steps
    out["todo_changes"] = held
    return out


def removed(described: Dict[str, Any], which: str) -> Dict[str, Any]:
    """The keys after the user deletes a step, by its id."""
    steps = items(described)
    gone = next((one for one in steps if str(one.get("id")) == str(which)),
                None)
    if gone is None:
        raise LookupError("no such step")
    out = {key: described.get(key) for key in KEYS if key in described}
    text = _clean(gone.get("text"))
    held = changes(described)
    asking = described.get("todo_asking") or {}
    shown_added = {_same(one) for one in asking.get("added") or []}
    shown_done = {_same(one) for one in asking.get("finished") or []}
    if (_same(text) in {_same(one) for one in held["finished"]}
            and _same(text) not in shown_done):
        # Finished and then deleted: the deletion says it all.
        held["finished"] = _take(held["finished"], [text])
    if _same(text) in {_same(one) for one in held["added"]}:
        # ADDED AND DELETED AGAIN. If no call has seen the addition, it
        # is as if it never happened; if one is reading it right now, the
        # deletion is news the next call must hear.
        held["added"] = _take(held["added"], [text])
        if _same(text) in shown_added:
            held["removed"].append(text)
    elif _same(text) not in {_same(one) for one in held["removed"]}:
        held["removed"].append(text)
    out["todo"] = [one for one in steps if one is not gone]
    out["todo_changes"] = held
    return out


def finished(described: Dict[str, Any], which: str) -> Dict[str, Any]:
    """The keys after the user marks a step finished, by its id.

    It stays in the list, ticked, until the next answer - which is told it
    was finished and leaves it out (rule 2)."""
    steps = items(described)
    done = next((one for one in steps if str(one.get("id")) == str(which)),
                None)
    if done is None:
        raise LookupError("no such step")
    out = {key: described.get(key) for key in KEYS if key in described}
    if done.get("done"):
        return out
    done["done"] = True
    done["done_at"] = _now()
    held = changes(described)
    text = _clean(done.get("text"))
    if _same(text) not in {_same(one) for one in held["finished"]}:
        held["finished"].append(text)
    out["todo"] = steps
    out["todo_changes"] = held
    return out


def ticked(described: Dict[str, Any], which: str, why: str,
           evidence: Sequence[str] = ()) -> Dict[str, Any]:
    """The keys after THE AGENT HEARTBEAT marks a step finished, by its id -
    from what it saw the user do: ticked at once, saying so and why, and
    told to the next call that writes the list. The user may take it back
    (`unfinished`); a step the user has ticked already is left as it is."""
    steps = items(described)
    done = next((one for one in steps if str(one.get("id")) == str(which)),
                None)
    if done is None:
        raise LookupError("no such step")
    out = {key: described.get(key) for key in KEYS if key in described}
    if done.get("done"):
        return out
    done.update(done=True, done_at=_now(), done_by="agent",
                done_why=_clean(why)[:LONGEST],
                done_evidence=[str(one)[:80] for one in evidence or []][:8])
    held = changes(described)
    text = _clean(done.get("text"))
    if _same(text) not in {_same(one) for one in held["agent_finished"]}:
        held["agent_finished"].append(text)
    out["todo"] = steps
    out["todo_changes"] = held
    return out


def unfinished(described: Dict[str, Any], which: str) -> Dict[str, Any]:
    """The keys after the user takes a tick back, by the step's id.

    A tick no call has been shown yet is as if it never happened. One a call
    is reading right now will make that call leave the step out - so the
    step is then said again as one the user wants kept (an addition), which
    the next call hears."""
    steps = items(described)
    step = next((one for one in steps if str(one.get("id")) == str(which)),
                None)
    if step is None:
        raise LookupError("no such step")
    out = {key: described.get(key) for key in KEYS if key in described}
    if not step.get("done"):
        return out
    for key in ("done", "done_at", "done_by", "done_why", "done_evidence"):
        step.pop(key, None)
    held = changes(described)
    text = _clean(step.get("text"))
    asking = described.get("todo_asking") or {}
    # A tick taken back - the user's own, or the agent heartbeat's.
    held["finished"] = _take(held["finished"], [text])
    held["agent_finished"] = _take(held["agent_finished"], [text])
    shown_done = {_same(one) for one in list(asking.get("finished") or [])
                  + list(asking.get("agent_finished") or [])}
    if _same(text) in shown_done \
            and _same(text) not in {_same(one) for one in held["added"]}:
        held["added"].append(text)
    out["todo"] = steps
    out["todo_changes"] = held
    return out


# -- the rows -----------------------------------------------------------------
def carried(db: Any, table: str, scope: str,
            things: Sequence[Any]) -> None:
    """Each thing's description given the to-do keys its row already has.

    Called by the saves, with `LOCK` held across the read and the write
    that follows it. A thing with no row yet keeps its own - that is the
    first write of a new task, or of one made from another.
    """
    ids = [str(getattr(one, "id", "") or "") for one in things]
    ids = [one for one in ids if one]
    if db is None or not ids:
        return
    held: Dict[str, Dict[str, Any]] = {}
    try:
        for start in range(0, len(ids), 400):
            part = ids[start:start + 400]
            rows = db.query(f"SELECT id, described FROM {table} WHERE scope = ? "
                            f"AND id IN ({', '.join('?' for _ in part)})",
                            (scope, *part))
            for row in rows:
                try:
                    said = json.loads(dict(row).get("described") or "{}")
                except (TypeError, ValueError):
                    said = {}
                held[str(dict(row).get("id"))] = (
                    said if isinstance(said, dict) else {})
    except Exception:            # pragma: no cover - a read, not the save
        log.debug("could not read the to-do lists before saving",
                  exc_info=True)
        return
    for one in things:
        if str(getattr(one, "id", "")) not in held:
            continue
        stored = held[str(one.id)]
        mine = dict(getattr(one, "described", None) or {})
        for key in KEYS:
            if key in stored:
                mine[key] = stored[key]
            else:
                mine.pop(key, None)
        one.described = mine


def change(db: Any, table: str, scope: str, which: str,
           how: Callable[[Dict[str, Any]], Dict[str, Any]]
           ) -> Optional[Dict[str, Any]]:
    """Read one row's description, change its to-do keys, write it back.

    `how` is given the description as the row has it and returns the
    to-do keys it should have; a key returned as None is taken out. The
    other keys of the description are not touched. Returns the
    description written, or None when there is no such row.
    """
    if db is None:
        return None
    with LOCK:
        row = db.query_one(f"SELECT described FROM {table} "
                           f"WHERE id = ? AND scope = ?", (which, scope))
        if row is None:
            return None
        try:
            described = json.loads(dict(row).get("described") or "{}")
        except (TypeError, ValueError):
            described = {}
        if not isinstance(described, dict):
            described = {}
        for key, value in how(dict(described)).items():
            if key not in KEYS:
                continue
            if value is None:
                described.pop(key, None)
            else:
                described[key] = value
        db.execute(f"UPDATE {table} SET described = ? "
                   f"WHERE id = ? AND scope = ?",
                   (json.dumps(described), which, scope))
        return described


def drawn(described: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """What the page is sent about a list: the steps, and whether the
    user has changes the model has not seen yet."""
    held = described or {}
    return {"todo": items(held), "todo_changes": changes(held),
            "todo_note": str(held.get("todo_note") or ""),
            "todo_at": str(held.get("todo_at") or "")}


__all__ = ["KEYS", "MOST", "LOCK", "TODO_PROMPT", "CHANGES", "rules",
           "items", "changes", "pending", "snapshot", "told", "read_answer",
           "answered", "added", "removed", "finished", "unfinished", "ticked",
           "carried", "change",
           "drawn"]
