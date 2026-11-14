"""THE LESSONS' MODEL LOOKS FOR ITSELF (the user, 2026-11-09: "你是怎么知道我真实原因的，能不能让模型学习
的时候和你一样做reasoning"; "总结经验的模型需要有搜索的能力，比如可以自己去搜索邮件").

A recommendation called bad was learned as "do not send unverified tips" - the user had been resting,
watching a video, and it was not on their screen at all; a meeting called bad was learned against
a lesson about quiet reminders - its time was still being settled by mail, and the agent had
picked one. What was missing was what an analyst looks at: what the user was doing then, what set
the thing off, their mail and chats about it. These are the searches the lessons' model is given,
read-only, to find why the user did what they did before it says what it means.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List

from ...models.base import ToolSpec

#: The most rounds of looking in one call; the last is answered without tools.
MOST_ROUNDS = 6
MOST_FOUND = 12


def _schema(properties: Dict[str, Any], required: List[str]) -> Dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required}


TOOLS = [
    ToolSpec("search_activity",
             "What the user was doing - their activity as the agent read it from their screen: "
             "pages, mail, chats, programs - by words, by a stretch of time, or both. Look at "
             "the moment of a record (from a little before to a little after it) to see what "
             "they were doing when they did it.",
             _schema({"query": {"type": "string", "description": "words, any language; may be empty"},
                      "from_local": {"type": "string", "description": "YYYY-MM-DD HH:MM, their clock"},
                      "until_local": {"type": "string", "description": "YYYY-MM-DD HH:MM, their clock"},
                      "limit": {"type": "integer"}}, []), read_only=True),
    ToolSpec("search_mail",
             "The user's mail threads seen on their screen, by words in their subject or "
             "people, each with when it was last seen - and what their activity says of them.",
             _schema({"query": {"type": "string"}}, ["query"]), read_only=True),
    ToolSpec("search_chat",
             "What the user and the agent said in the agent's chat, by words.",
             _schema({"query": {"type": "string"}}, ["query"]), read_only=True),
    ToolSpec("search_people",
             "The user's contacts who may be meant by some words: a name, what they are called "
             "(学姐), what they do.",
             _schema({"query": {"type": "string"}}, ["query"]), read_only=True),
]

RULES = """
WHY THE USER DID IT - find it before you answer, as a careful analyst would. A record says what
the user did; it does not say why. For each, weigh the reasons it could have had, and take the one
the evidence supports:
  - THE MOMENT: what they were doing then (resting, in a meeting, writing to someone) - look
    at their activity around its time;
  - WHERE IT CAME FROM: what set it off, and whether that was in front of them at all;
  - THE THING ITSELF: its subject, its kind, how it was said;
  - IT WAS WRONG: a time not agreed, a fact not true, a person mistaken - look at their mail
    and chats about it;
  - THEY ALREADY KNEW IT, OR HAD ALREADY DONE IT.
You have searches for this - their activity, mail, chat and contacts: use them when the record
alone does not settle it. Say the reason in "why", and "sure": false when the evidence does not
settle it - then nothing narrower than the evidence is to be read into it."""


class Looking:
    """The searches, run on the agent's own records - never written to."""

    def __init__(self, db: Any) -> None:
        self.db = db

    def run(self, name: str, arguments: Dict[str, Any]) -> Any:
        try:
            if name == "search_activity":
                from ...agent.core.memory_tools import search_activity
                return search_activity(self.db, dict(arguments or {}, limit=min(
                    MOST_FOUND, int((arguments or {}).get("limit") or MOST_FOUND))))
            if name == "search_mail":
                return self._mail(str((arguments or {}).get("query") or ""))
            if name == "search_chat":
                from ..core import related
                return {"found": related.chat(self.db, str((arguments or {}).get("query") or ""),
                                              most=MOST_FOUND)}
            if name == "search_people":
                from ..core import related
                return {"found": related.people(self.db, str((arguments or {}).get("query") or ""))}
        except Exception as exc:                     # said to the model, which goes on
            return {"error": str(exc)[:300]}
        return {"error": f"no tool {name}"}

    def _mail(self, query: str) -> Any:
        from ..core import related
        words = related._words(query)
        threads = []
        try:
            for row in self.db.query("SELECT title, people, last_seen FROM work_mail_pages "
                                     "ORDER BY last_seen DESC LIMIT 600"):
                said = f"{row.get('title') or ''} {row.get('people') or ''}"
                if words and words & related._words(said):
                    threads.append({"subject": str(row.get("title") or "")[:200],
                                    "people": str(row.get("people") or "")[:200],
                                    "last_seen": str(row.get("last_seen") or "")[:16]})
        except Exception:                            # no mail kept here
            pass
        return {"threads": threads[:MOST_FOUND],
                "activity": related.activity(self.db, f"{query} email mail gmail outlook",
                                             most=MOST_FOUND // 2)}


def local_time(at: Any) -> str:
    """A record's time on the user's own clock, as the searches take it."""
    try:
        moment = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    except ValueError:
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone().strftime("%Y-%m-%d %H:%M")


def ask_looking(models: Any, request: Any, looking: "Looking") -> Any:
    """`request` (a ModelRequest), with the searches: rounds of looking, then the answer."""
    from ...models.base import ModelRequest
    messages = list(request.messages)
    tools = list(TOOLS)
    response = None
    for round_ in range(MOST_ROUNDS + 1):
        asked = ModelRequest(messages=messages, system=request.system, tools=tools,
                             tool_runner=looking.run if tools else None,
                             max_tokens=request.max_tokens, task=request.task,
                             language=request.language)
        response = models.invoke(asked, role="heartbeat")
        calls = getattr(response, "tool_calls", None) or []
        if not calls or not tools:
            return response
        messages.append({"role": "assistant", "content": getattr(response, "raw_content", None)
                         or getattr(response, "text", "")})
        messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": call.id,
             "content": json.dumps(looking.run(call.name, call.arguments or {}),
                                   ensure_ascii=False, default=str)[:12000]} for call in calls]})
        if round_ >= MOST_ROUNDS - 1:
            tools = []                               # the last: answered from what was found
    return response


__all__ = ["TOOLS", "RULES", "Looking", "ask_looking", "local_time", "MOST_ROUNDS"]
