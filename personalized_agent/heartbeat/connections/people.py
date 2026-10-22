"""Step 2 of the agent heartbeat: THE PEOPLE in a window of activity.

The user's decision: step 2 - who the user is - was paused, and its place is
this. From the same window step 1 read, and the states it wrote: who appears
(written to, met, read about, talked about), what they are to the user, and
what they are doing - each fact resting on the window's events, matched to
the people already known, and given the user's own changes to their contacts
(the connection lessons, dated) so a person the user deleted is not added
back on a hunch.

Only what the user had in front of them: the agent signs in to nothing.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .store import ConnectionsStore, name_key

log = logging.getLogger(__name__)

#: At most this many organisations and platforms are listed to be reused,
#: and this many people written from one window.
MOST_NAMES = 120
MOST_WRITTEN = 30
#: EVERY CONTACT is given, with their details and their latest activity
#: (`RECENT_ACTIVITY` items) - up to `MOST_CONTEXT_CHARS` of it; past that,
#: the rest one line each, and past `MOST_LISTED_CHARS` not at all (they are
#: still matched by name and account when the answer is written).
RECENT_ACTIVITY = 5
MOST_CONTEXT_CHARS = 80000
MOST_LISTED_CHARS = 120000

INSTRUCTION = """FIND THE PEOPLE IN THIS WINDOW - who appears in what the user did, what
they are to the user, and what they are doing - for the user's contacts.

A NAME ON THE SCREEN IS NOT A CONTACT. What counts is the CONTEXT it is in:
the person is in the user's own life or work, and the window shows that.

A person counts when the user DEALS WITH THEM: someone the user wrote to or
had a message, a mail, a comment or an invitation from; met, called or has a
meeting with; works with on a shared document, a repository, a paper, a
project or a task; whose own page or profile the user went to as someone
they know, or are about to deal with (a recruiter, a reviewer, a new
collaborator).

A person does NOT count when the user only READ or WATCHED about them: a name
in the news, an article, a blog, a video or a podcast; the author of a
paper, a post or a book the user read; a public figure, an executive, a
politician or a celebrity they read about; a name in search results, in a
feed or a list of strangers; a fictional character; a name in code, data or
an example. Unless the window ALSO shows the user dealing with them. When in
doubt, leave them out. Not the user themselves.

FOR EACH PERSON, only what the window shows:
  - who they are: their name as written, other names (an English name, a
    nickname, a handle), and any account or profile address seen (a LinkedIn
    page, a GitHub profile, an email address);
  - what they are to the user, when the window shows it - ONE OR MORE (a
    colleague who is a friend too), THE MAIN ONE FIRST: family, partner,
    friend, colleague, manager, report, mentor, student, classmate,
    collaborator, client, recruiter, acquaintance, other - or one of those
    the user already uses (below);
  - their job or profession ("software engineer", "PhD student", "doctor");
  - where they work or study - EACH ORGANISATION, with their role there, THE
    MAIN ONE FIRST - and where they are;
  - WHAT THEY ARE DOING: each thing the window shows them doing, saying, or
    that happened to them - with when it happened, and the ids of the events
    that show it. "Sent the revised budget", "started at Google as a PM",
    "asked the user to review her paper by Friday".

THE USER'S CONTACTS ARE ALL BELOW - each with their id, their details, their
accounts and what they have been doing lately. KEEP THE LIST RIGHT, and
WITHOUT DUPLICATES. For each person you write, an "action":
  - "update" - SOMEONE ALREADY LISTED ("person_id"): what is new about them,
    and what they did. The same person under another name, in another
    script (谭梅 / Mei Tan) or on another platform IS someone listed: check
    every name, other name and account first. Activity already listed under
    them is not written again - not in other words either.
  - "add" - someone NOT in the list at all.

  A DIFFERENT PERSON IS NOT AN UPDATE. Names that cannot be one person's - a
  different family name, a name in another script that is not the same
  name - or a different organisation, field or place with nothing tying
  them together, are SOMEONE ELSE: "add" them. Two people seen together -
  in one window, one email (its sender and those copied), one organisation,
  one paper or one topic - are still two people, each with their own entry;
  never give one person's id to another. When you cannot tell, "add": a
  duplicate is merged later, but two people made one mixes their lives up.
  - "merge" - two listed entries are ONE person: "person_id" the one kept,
    "same_as" the other, "why". Everything of the other comes over.
  - "split" - a listed entry is MORE THAN ONE PERSON: its names, other
    names, accounts or activity cannot all be one person's (look at every
    contact listed, not only those in this window). "person_id" the entry,
    "why", and "parts": each OTHER person in it - their "name", "aliases",
    the "accounts" of theirs (each handle or address as listed) and the
    "activity" of theirs (the ids in brackets, as listed). The entry keeps
    what is left, as the person it was first.
  - "delete" - a listed person who is NOT a contact by the rules above (a
    name from the news, the author of something the user only read), with
    "why" - ONLY one marked "added by the agent, untouched": never one the
    user added or changed.

THE USER'S OWN CHANGES TO THEIR CONTACTS are below, each dated, with their
reason when they gave one. They are the user's decisions - follow them:
  - a person the user DELETED is not added back, nor written about, unless
    this window holds strong evidence NEWER than the deletion that they
    matter to the user now (they are working together again, a meeting is
    set) - then give "add_back_because", saying what it is;
  - what the user corrected (a relationship, an organisation) stays as they
    set it;
  - what they removed as noise (a mailing list, a bot) is not added again.

YOU DECIDE WHAT YOU NEED TO SEE, WITHIN THIS WINDOW: `open_by_id` reads an
event or a state of it whole - the full message, not its keywords. There is
nothing to search beyond it.

AN EMPTY LIST IS A GOOD ANSWER when no one appears. Nothing invented: a
relationship, a job, a fact you did not see is left out."""

RETURNS = """{
  "people": [
    {"action": "update | add | merge | split | delete",
     "person_id": "the listed person's id - for update, merge (the one kept) and delete; null for add",
     "same_as": "merge only: the id of the other entry, which becomes this one",
     "why": "merge, split and delete: why",
     "parts": [{"name": "split only: one other person in the entry", "aliases": [],
                "accounts": ["their handles or addresses, as listed"],
                "activity": ["their activity's ids, as listed"]}],
     "name": "their name as written",
     "aliases": ["other names, handles"],
     "accounts": [{"platform": "linkedin|github|email|wechat|x|...", "handle": "...", "url": "..."}],
     "relationships": ["what they are to the user, the main one first - empty when the window does not show it"],
     "profession": "their job or profession, or empty",
     "affiliations": [{"org": "an organisation they are at", "role": "their role there"}],
     "location": "",
     "tags": ["a few words to find them by - a project, a topic, a group"],
     "activity": [{"at": "ISO-8601 with the user's offset, when it happened",
                   "what": "what they did, said or what happened to them, in a line",
                   "evidence": ["the ids of the events that show it"]}],
     "evidence": ["the ids of the events this person rests on"],
     "add_back_because": "ONLY for a person the user deleted: the strong, newer evidence that they matter now"}
  ]
}"""


def _named_in(person: Dict[str, Any], hay: str) -> bool:
    """Whether a person's name, or one of their other names, is in the
    window - a name of two Chinese, Japanese or Korean characters too, which
    is a whole name there (谭梅), as three letters are elsewhere."""
    for name in [person["name"], *person["aliases"]]:
        key = name_key(name)
        if key and (len(key) >= 3 or (len(key) >= 2 and _wide(key))) and key in hay:
            return True
    return False


def _wide(text: str) -> bool:
    return any("\u3040" <= ch <= "\u9fff" or "\uac00" <= ch <= "\ud7af" for ch in text)


def _who_added(person: Dict[str, Any]) -> str:
    if person.get("added_by") == "user":
        return "added by the user"
    return ("added by the agent, changed by the user" if person.get("user_set")
            else "added by the agent, untouched")


def _person_text(store: ConnectionsStore, person: Dict[str, Any], whole: bool) -> str:
    """One contact as the step reads them: everything known of them - or,
    `whole` False, a line."""
    said = [f"[{person['id']}] {person['name']}"]
    if person["aliases"]:
        # Every other name: a name that is not theirs is how two people made
        # one are seen.
        said.append("also " + ", ".join(person["aliases"][:30]))
    if person["deleted"]:
        said.append(f"DELETED by the {person['deleted_by'] or 'user'} on "
                    f"{person['deleted_at'][:10]}"
                    + (f" - {person['deleted_reason']}" if person["deleted_reason"] else ""))
        return " · ".join(said)
    if not whole:
        if person["org"]:
            said.append(person["org"])
        return " · ".join(said)
    if person["relationships"]:
        said.append("relationships: " + " / ".join(person["relationships"]))
    jobs = person.get("professions") or ([person["profession"]] if person["profession"] else [])
    if jobs:
        said.append(("jobs: " if len(jobs) > 1 else "job: ") + " / ".join(jobs))
    if person["affiliations"]:
        said.append("organisations: " + "; ".join(
            " at ".join(filter(None, [one.get("role"), one.get("org")]))
            for one in person["affiliations"][:5]))
    places = person.get("locations") or ([person["location"]] if person.get("location") else [])
    if places:
        said.append("in " + " / ".join(places))
    if person["tags"]:
        said.append("tags: " + ", ".join(person["tags"][:10]))
    accounts = [" ".join(filter(None, [one.get("platform"), one.get("handle"), one.get("url")]))
                for one in store.accounts(person["id"])[:6]]
    if accounts:
        said.append("accounts: " + "; ".join(accounts))
    said.append(_who_added(person))
    lines = [" · ".join(said)]
    for one in store.activity(person["id"], RECENT_ACTIVITY):
        lines.append(f"    [{one['id']}] {str(one.get('at') or '')[:10] or 'undated'}: "
                     f"{one['what']}")
    return "\n".join(lines)


def context(store: ConnectionsStore, activity_text: str) -> str:
    """What the step is given beside the window: EVERY CONTACT - those named
    in it first, then the most recently active, those the user deleted last
    - each with their details, accounts and latest activity, so a person is
    updated rather than added again, and what they did is not written twice;
    then the user's own changes, dated."""
    hay = name_key(activity_text)
    people = store.people(include_deleted=True)
    named = [one for one in people if _named_in(one, hay)]
    live = sorted((one for one in people if one not in named and not one["deleted"]),
                  key=lambda one: one.get("last_activity_at") or one.get("updated_at") or "",
                  reverse=True)
    rest = live + [one for one in people if one not in named and one["deleted"]]
    lines, used, short, left = [], 0, 0, 0
    for one in [*named, *rest]:
        text = _person_text(store, one, whole=used < MOST_CONTEXT_CHARS)
        if used + len(text) > MOST_LISTED_CHARS:
            left += 1
            continue
        short += int(used >= MOST_CONTEXT_CHARS)
        used += len(text) + 1
        lines.append(text)
    known = "\n".join(lines) or "None yet."
    if short or left:
        known += (f"\n({short} listed in a line only, {left} not listed: they are still "
                  "matched by their names and accounts when you add someone.)")
    # ONE SPELLING FOR EACH THING: an organisation, or the platform of an
    # account, that means one already written is written as it is there.
    orgs = ", ".join(one["name"] for one in store.names_in_use("org")[:MOST_NAMES]) \
        or "None yet."
    labels = ", ".join(one["name"] for one in store.names_in_use("label")[:MOST_NAMES])
    relationships = ", ".join(store.relationships_in_use())
    return (f"THE USER'S CONTACTS ({len(people)} - those named in this window first):\n"
            f"{known}\n\nLESSONS ABOUT THE USER'S CONTACTS - learned from their own "
            f"changes, each with how sure and how many cases back it; follow the sure "
            f"ones:\n{store.lessons_text()}\n\nSPELLINGS ALREADY IN USE - "
            f"an organisation or an account's platform that means one of these is "
            f"written as it is here:\n  organisations: {orgs}\n  platforms: {labels}\n"
            f"  relationships: {relationships}")


def apply(store: ConnectionsStore, people: Any, evidence_known: Optional[set] = None,
          by: str = "agent", found_at: str = "") -> List[Dict[str, Any]]:
    """The step's answer, written: each person matched (by the id it gave,
    else by an account or the exact name) or added; a deleted one added back
    only with its reason; fields the user set left as they are (the store);
    each piece of activity added once. What was done with each, said."""
    out: List[Dict[str, Any]] = []
    if not isinstance(people, list):
        return out
    for entry in people[:MOST_WRITTEN]:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        accounts = [one for one in entry.get("accounts") or [] if isinstance(one, dict)]
        action = str(entry.get("action") or "").strip().lower() or (
            "update" if entry.get("person_id") else "add")
        row: Dict[str, Any] = {"name": name, "ok": False, "action": action}
        try:
            if action == "split":
                row.update(_split(store, entry, by))
                out.append(row)
                continue
            if action in ("delete", "merge"):
                row.update(_remove(store, action, entry, by))
                out.append(row)
                if action == "delete" or not row.get("ok"):
                    continue
                # Merged: what this window says of them goes to the one kept.
                row = {"name": name, "ok": False, "action": "update"}
            person = None
            if entry.get("person_id"):
                person = store.get(str(entry["person_id"]))
            if person is None:
                person = store.find(name, accounts)
            if person is not None and person.get("is_self"):
                # The user's own profile is kept from their public pages
                # only, never from what was on their screen.
                row.update(error="the user themselves")
                out.append(row)
                continue
            fields = {key: entry.get(key) for key in ("org", "role", "location", "profession")
                      if str(entry.get(key) or "").strip()}
            # What they are to the user - the main one first: the usual ones,
            # or one the user already uses; never a new word of the model's.
            known = set(store.relationships_in_use())
            said = entry.get("relationships")
            said = said if isinstance(said, list) else [entry.get("relationship")]
            relationships = [one for one in (str(r or "").strip().lower() for r in said)
                             if one in known]
            if relationships:
                fields["relationships"] = relationships
            affiliations = [one for one in entry.get("affiliations") or []
                            if isinstance(one, dict) and str(one.get("org") or "").strip()]
            if affiliations:
                fields["affiliations"] = affiliations
                fields.pop("org", None)
                fields.pop("role", None)
            tags = [str(one) for one in entry.get("tags") or [] if str(one).strip()]
            aliases = [str(one) for one in entry.get("aliases") or [] if str(one).strip()]
            if person is not None and person["deleted"]:
                because = str(entry.get("add_back_because") or "").strip()
                if not because:
                    row.update(person_id=person["id"], error="deleted by the user, and no "
                               "strong new evidence was given to add them back")
                    out.append(row)
                    continue
                person = store.restore(person["id"], by=by, reason=because)
                row["added_back"] = because
            if person is None:
                if not name:
                    row["error"] = "a person needs a name"
                    out.append(row)
                    continue
                person = store.add(name, by=by, reason="seen in the user's activity",
                                   found_at=found_at, aliases=aliases, tags=tags, **fields)
                row["added"] = True
            else:
                more = dict(fields)
                if tags:
                    more["tags"] = tags
                if aliases or (name and name_key(name) != name_key(person["name"])):
                    more["aliases"] = [*aliases, *([name] if name and name_key(name)
                                                   != name_key(person["name"]) else [])]
                if more:
                    person = store.edit(person["id"], by=by, found_at=found_at,
                                        reason="seen in the user's activity", **more)
            for account in accounts:
                store.add_account(person["id"], str(account.get("platform") or ""),
                                  str(account.get("handle") or ""), str(account.get("url") or ""),
                                  source="the user's activity", found_at=found_at)
            added = 0
            for item in entry.get("activity") or []:
                if not isinstance(item, dict):
                    continue
                ids = [str(one) for one in item.get("evidence") or [] if str(one).strip()]
                if evidence_known is not None and ids and not set(ids) & evidence_known:
                    continue          # rests on nothing this window holds
                if store.add_activity(person["id"], str(item.get("what") or ""),
                                      str(item.get("at") or ""), "the user's activity",
                                      evidence=ids, found_at=found_at):
                    added += 1
            row.update(ok=True, person_id=person["id"], name=person["name"], activity=added)
        except Exception as exc:          # one person's failure is not the step's
            log.warning("could not write a person from the activity", exc_info=True)
            row["error"] = f"{type(exc).__name__}: {exc}"[:300]
        out.append(row)
    return out


def _split(store: ConnectionsStore, entry: Dict[str, Any], by: str) -> Dict[str, Any]:
    """A SPLIT the step asked for: an entry that is more than one person,
    each other person in it made an entry of their own (store.split)."""
    why = str(entry.get("why") or "").strip()
    person = store.get(str(entry.get("person_id") or ""))
    if person is None:
        return {"error": "no such person"}
    if not why:
        return {"person_id": person["id"], "error": "a split needs its why"}
    parts = [one for one in entry.get("parts") or [] if isinstance(one, dict)
             and str(one.get("name") or "").strip()]
    if not parts:
        return {"person_id": person["id"], "error": "a split needs the other people in it"}
    try:
        made = store.split(person["id"], parts, by=by, reason=why)
    except (KeyError, ValueError) as exc:
        return {"person_id": person["id"], "error": str(exc)}
    return {"ok": bool(made), "person_id": person["id"], "name": person["name"],
            "split_into": [one["name"] for one in made]}


def _remove(store: ConnectionsStore, action: str, entry: Dict[str, Any],
            by: str) -> Dict[str, Any]:
    """A DELETE or a MERGE the step asked for. Deleted only one the agent
    added and the user never changed - the user's own are theirs to remove
    - and kept, marked deleted, to be added back. Merged two entries that are
    one person. Never the user themselves."""
    why = str(entry.get("why") or "").strip()
    person = store.get(str(entry.get("person_id") or ""))
    if person is None:
        return {"error": "no such person"}
    if person.get("is_self"):
        return {"person_id": person["id"], "error": "the user themselves"}
    if not why:
        return {"person_id": person["id"], "error": f"a {action} needs its why"}
    if action == "delete":
        if person.get("added_by") == "user" or person.get("user_set"):
            return {"person_id": person["id"],
                    "error": "added or changed by the user: theirs to delete"}
        store.delete(person["id"], by=by, reason=why)
        return {"ok": True, "person_id": person["id"], "name": person["name"],
                "deleted": why}
    other = store.get(str(entry.get("same_as") or ""))
    if other is None or other["id"] == person["id"]:
        return {"person_id": person["id"], "error": "no other entry to merge"}
    if other.get("is_self"):
        return {"person_id": person["id"], "error": "the user themselves"}
    kept = store.merge(person["id"], other["id"], by=by, reason=why)
    return {"ok": True, "person_id": kept["id"], "name": kept["name"],
            "merged": other["name"]}
