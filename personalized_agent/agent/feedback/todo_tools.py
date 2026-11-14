"""THE CHAT'S TO-DO TOOLS (the user, 2026-11-03: "chat里要能直接加to do").

    todo_add               a to-do on the user's list - when they ask for one in the chat
    todo_list              what is on it, open and lately done
    todo_suggest_done      it looks done: Mark as done offered on it, with why
    todo_suggest_remove    no longer wanted: Remove offered on it, with why

None takes one off or marks one done: ONLY THE USER REMOVES ONE (todo_list.py). The two that
suggest (the user, 2026-11-09: "包括chat, 现在也要有把to do 建议删掉的功能") put it on the to-do,
where the user confirms it with one press, or turns it down.
"""

from __future__ import annotations

from typing import Any, Dict

from ...models.base import ToolSpec


def _schema(properties: Dict[str, Any], required: list) -> Dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required,
            "additionalProperties": False}


def add_tools(registry: Any, todos: Any, links: Any = None) -> None:
    def add(args: Dict[str, Any]) -> Any:
        text = " ".join(str(args.get("text") or "").split())
        if not text:
            return {"error": "a to-do needs a few words saying what to do"}
        made = todos.add(text, str(args.get("detail") or ""))
        return {"added": made["id"], "text": made["text"],
                "said": "On the user's to-do list now (the Overview, beside the Summary). "
                        "Tell them, in a few words."}

    def listed(_args: Dict[str, Any]) -> Any:
        def part_of(ident: str) -> Dict[str, Any]:
            note = links.note("todo", ident) if links is not None else ""
            return {"part_of": note} if note else {}
        return {"todos": [{"id": one["id"], "text": one["text"], "detail": one["detail"],
                           "added": one["created_at"], "done": bool(one["done_at"]),
                           **({"suggested": one["suggested_kind"] or "done",
                               "suggested_why": one["suggested_why"]} if one.get("suggested_at")
                              else {}),
                           **({"they_said_not_done": one["declined_at"]} if one.get("declined_at")
                              else {}),
                           **part_of(one["id"])}
                          for one in todos.shown()]}

    def _open(args: Dict[str, Any]) -> Any:
        one = todos.get(str(args.get("id") or ""))
        if one is None or one.get("deleted_at") or one.get("done_at"):
            return None
        return one

    def suggest(args: Dict[str, Any], kind: str) -> Any:
        one = _open(args)
        why = " ".join(str(args.get("why") or "").split())
        if one is None:
            return {"error": "no such open to-do: its id is in todo_list"}
        if not why:
            return {"error": "say why, in one sentence: it is shown on the to-do"}
        todos.suggest(one["id"], why, ["chat"], kind=kind)
        return {"suggested": one["id"], "text": one["text"],
                "said": "Shown on the to-do with your why: the user confirms it with one press "
                        f"({'Mark as done' if kind == 'done' else 'Remove'}), or turns it down. "
                        "Tell them so, in a few words."}

    registry.add(ToolSpec(
        "todo_add",
        "Add a to-do to the user's own to-do list - when they ask for one (\"add a to-do\", "
        "\"remind me to...\", \"put it on my list\"). One line saying what to do, in their "
        "words and language; the detail only what they said or what makes it clear.",
        _schema({"text": {"type": "string", "description": "what to do, one line"},
                 "detail": {"type": "string", "description": "a few more words, if any"}},
                ["text"])), add)
    registry.add(ToolSpec(
        "todo_list", "Read the user's to-do list: what is open, and what was done lately.",
        _schema({}, []), read_only=True), listed)
    target = {"id": {"type": "string", "description": "the to-do's id, from todo_list"},
              "why": {"type": "string", "description": "one sentence: what shows it"}}
    registry.add(ToolSpec(
        "todo_suggest_done",
        "Say a to-do on the user's list looks done, with what shows it - when the conversation or "
        "what you found shows it was done. It is NOT marked done: the to-do offers Mark as done "
        "with your why, for the user to press. One they said is not done (they_said_not_done) is "
        "suggested again only on clear progress since.",
        _schema(target, ["id", "why"])), lambda args: suggest(args, "done"))
    registry.add(ToolSpec(
        "todo_suggest_remove",
        "Suggest taking a to-do off the user's list - when they say they no longer want it, or it "
        "is plainly no longer wanted. You cannot remove it yourself: the to-do offers Remove with "
        "your why, for the user to press. Say in why what is to STAY when it goes, as they said it "
        "(\"only off the list - the meeting stays on the calendar\"): once it is removed, what was "
        "left for it is looked over by your why.",
        _schema(target, ["id", "why"])), lambda args: suggest(args, "remove"))


__all__ = ["add_tools"]
