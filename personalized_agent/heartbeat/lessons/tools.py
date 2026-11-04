"""LESSONS FROM THE CHAT (the user, 2026-11-03: "chat也可以加入lesson ... 按照所有
lesson一样的格式加入").

    lesson_add    what the user just told the agent to remember - an example
                  like any other: its class, their exact words, the conversation
                  it was said in; with the rule, when they stated one
    lesson_find   the rules and examples like a moment (the one way, retrieve.py)
"""

from __future__ import annotations

from typing import Any, Dict

from ...models.base import ToolSpec

#: What a lesson from the chat may be about.
KINDS = ("notifications", "connections", "general")


def add_tools(registry: Any, engine: Any, retriever: Any) -> None:
    def add(args: Dict[str, Any]) -> Any:
        said = " ".join(str(args.get("user_said") or "").split())
        if not said:
            return {"error": "a lesson from the chat needs the user's own words"}
        kind = str(args.get("about") or "general")
        kind = kind if kind in KINDS else "general"
        ident = engine.record(kind, "", str(args.get("situation") or said)[:4000],
                              action="told_in_chat", reason=said,
                              content=str(args.get("situation") or ""))
        rule = " ".join(str(args.get("rule") or "").split())
        made = ""
        if rule:
            # Said by the user themselves: in force at once - no cases needed.
            made = engine.rule_from_the_user(kind, rule, ident)
        return {"added": ident, "rule": made,
                "said": "Kept as a lesson. Tell the user in a few words what you will do."}

    def find(args: Dict[str, Any]) -> Any:
        return {"lessons": retriever.text(KINDS, str(args.get("moment") or ""), rules=8,
                                          examples=4) or "None learned yet."}

    registry.add(ToolSpec(
        "lesson_add",
        "Keep a lesson: ONLY when the USER says something should be remembered about how "
        "they want things done (\"remember this\", \"from now on...\", \"don't ever...\", or "
        "plainly correcting how you or the agent acted). Never your own guess. Kept with "
        "every other lesson, and used when the agent next decides anything like it.",
        {"type": "object", "properties": {
            "user_said": {"type": "string", "description": "the user's own words, exactly"},
            "about": {"type": "string", "enum": list(KINDS),
                      "description": "notifications (what to tell them, when), connections "
                                     "(their contacts), or general"},
            "situation": {"type": "string",
                          "description": "what was going on, from this conversation"},
            "rule": {"type": "string",
                     "description": "the rule they stated, IN ENGLISH, one sentence said to "
                                    "the agent: when ..., do / do not ... - only if they stated one"}},
         "required": ["user_said", "about"]}), add)
    registry.add(ToolSpec(
        "lesson_find",
        "Read the lessons - the rules and what the user did - like a moment you describe.",
        {"type": "object", "properties": {"moment": {"type": "string"}}, "required": ["moment"]},
        read_only=True), find)


__all__ = ["add_tools", "KINDS"]
