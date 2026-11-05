"""THE CHAT'S TO-DO TOOLS (the user, 2026-11-03: "chat里要能直接加to do").

    todo_add    a to-do on the user's list - when they ask for one in the chat
    todo_list   what is on it, open and lately done

None takes one off or marks one done: ONLY THE USER REMOVES ONE (todo_list.py).
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
                           **part_of(one["id"])}
                          for one in todos.shown()]}

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


__all__ = ["add_tools"]
