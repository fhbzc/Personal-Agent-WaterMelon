"""TWO SPEEDS IN CHAT (the user, 2026-11-04: "有一个快模型，没有context直接回复，然后慢模型在该
intervene的时候插入").

A message is answered at once by a small model given only the last few messages
and no tools; the full agent then takes it up, with all it knows and all its
tools, and what it writes is folded under the quick reply.

ONLY THE FULL AGENT ACTS. The quick model has no tools at all, so a lesson, a
notification or a to-do has one author and nothing is done twice. Its one power
is to say the full agent need not start - which is why it is told so firmly to
leave it running whenever a tool might be wanted: stopped wrongly, what the user
asked to be remembered would never be written.

It runs after the quick reply, not beside it: it reads what was said, and being
stopped is never having begun - nothing is cut off half way.

A message written while the full agent is at work is answered at once as ever;
its full turn waits, and all that waited are taken up together.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..common.events.models import new_id
from ..common.languages import in_display
from ..models.base import ModelRequest

log = logging.getLogger(__name__)

#: How much of the conversation the quick model is given.
RECENT_MESSAGES = 6
RECENT_CHARS = 600
QUICK_TOKENS = 400
#: Turns remembered for the page to ask after.
MOST_TURNS = 200

#: What the full agent writes when there is nothing to add; what begins a correction.
NOTHING_TO_ADD = "NOTHING_TO_ADD"
CORRECTION = "[CORRECTION]"

#: Added to the agent's own system prompt: who it is and what the application has
#: are the same at both speeds (the user, 2026-11-04), and they are what it
#: judges by. This says what is different, and goes last so that it holds.
QUICK_RULES = """

RIGHT NOW YOU ARE THE QUICK VOICE, AND THIS OVERRIDES ANYTHING ABOVE. For this one reply you have \
no tools, and you see only the last few messages: nothing of the user's memory, work, contacts, \
calendar or files. What is above is who you are and what the application has, to judge by - here \
you can do none of it. The FULL AGENT - you, with all of that and every tool - takes the message up \
after you, unless you stop it. Its answer appears folded under yours.

Answer with JSON alone: {"reply": "...", "full_agent": "run" or "stop"}

"full_agent": "stop" ONLY when nothing more could possibly be wanted: a greeting, thanks, small \
talk, or a general question you answer fully yourself.

"full_agent": "run" WHENEVER A TOOL MIGHT BE NEEDED OR WANTED, and whenever you are unsure. Always \
for: anything to remember, learn or keep ("remember", "from now on", "don't ever", a correction of \
how the agent behaves - these are written into its memory, and if you stop it they are lost); \
reminders, notifications, to-dos, the calendar, contacts, files, searching the web, work to be \
done; anything about the user, their work, their past messages or anything attached. Stopping \
wrongly loses what the user asked for; running needlessly costs little.

"reply": one to three short sentences, as yourself.
- Never state anything about the user's data, schedule, contacts, files or past: you cannot know it.
- Never say something is done: you can do nothing here. Say it is being handled.
- When the full agent runs, say what you can and tell the user that you are looking into this and \
that more will follow below."""

FULL_NOTE = """

[A quick reply has already been shown to the user, written with none of your context and no tools:
\"\"\"{reply}\"\"\"
What you write now appears folded beneath it, and the user reads it as one voice: do not repeat it, \
greet, or comment on it. Do what the message asks, with your tools, and say what came of it. Begin \
with {correction} only if the quick reply stated a fact that is false and would mislead the user, \
and then put that fact right. Its saying it could not see something, was looking into it, or \
wording a thing a little loosely is not a mistake. If you did nothing and have nothing to add, \
write exactly {nothing} and no more.]"""


def _parsed(text: str) -> Optional[Dict[str, Any]]:
    found = re.search(r"\{.*\}", text or "", re.S)
    if not found:
        return None
    try:
        said = json.loads(found.group(0))
    except ValueError:
        return None
    return said if isinstance(said, dict) else None


class TwoSpeed:
    """A chat's turns at two speeds."""

    def __init__(self, agent: Any, conversations: Any, models: Any, config: Any,
                 files: Any = None, db: Any = None) -> None:
        self.agent, self.conversations, self.models = agent, conversations, models
        self.config, self.files, self.db = config, files, db
        self._lock = threading.Lock()
        self._turns: Dict[str, Dict[str, Any]] = {}
        self._waiting: Dict[str, List[Dict[str, Any]]] = {}
        self._working: set = set()

    @property
    def on(self) -> bool:
        return bool(self.config.get("chat_two_speed", True))

    # -- a message ---------------------------------------------------------------------------
    def send(self, text: str, conversation_id: Optional[str] = None,
             attachments: Optional[Sequence[str]] = None, mentions: Optional[Sequence[str]] = None,
             on_done: Optional[Callable[[Dict[str, Any]], None]] = None,
             front: str = "", seen: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
        """The quick reply, at once: "answer", "message_id", "conversation_id"
        and "turn", the full agent's - "" when it was stopped. Raises
        ValueError for a file no longer there. `front`: what is in front of
        the user (agent/front_screen.py), for both to read."""
        attachments = [str(one) for one in attachments or []]
        mentions = [str(one) for one in mentions or []]
        described = self._described(attachments)
        conversation_id = self.conversations.ensure(conversation_id)
        earlier = self.conversations.messages(conversation_id, limit=None)[-RECENT_MESSAGES:]
        metadata: Dict[str, Any] = {"origin": "user"}
        if described:
            metadata["attachments"] = described
        user = self.conversations.add_message(conversation_id, "user", text, metadata=metadata)
        quick = self._quick(text, earlier, described, front)
        run = quick["run"] or bool(described) or bool(mentions) or not quick["reply"]
        ids = [user["id"]]
        said: Optional[Dict[str, Any]] = None
        if quick["reply"]:
            self.agent.speaks_whole(quick["reply"])
            said = self.conversations.add_message(
                conversation_id, "assistant", quick["reply"],
                metadata={"layer": "fast", "full_agent": "run" if run else "stop", "origin": "user"})
            ids.append(said["id"])
        turn = ""
        if run:
            turn = new_id("turn")
            self._begin(conversation_id, {
                "turn": turn, "text": text, "ids": ids, "reply": quick["reply"],
                "attachments": attachments, "mentions": mentions, "on_done": on_done,
                "front": front, "seen": seen})
        return {"conversation_id": conversation_id, "answer": quick["reply"], "layer": "fast",
                "message_id": said["id"] if said else "", "turn": turn}

    def _described(self, ids: List[str]) -> List[Dict[str, Any]]:
        if not ids or self.files is None:
            return []
        try:
            return [self.files.described(one) for one in ids]
        except KeyError:
            raise ValueError("a file attached is no longer there")

    def _quick(self, text: str, earlier: List[Dict[str, Any]],
               files: List[Dict[str, Any]], front: str = "") -> Dict[str, Any]:
        """What the small model says; nothing, and the full agent answers."""
        turns = [{"role": one["role"], "content": str(one.get("content") or "")[:RECENT_CHARS]}
                 for one in earlier
                 if one.get("role") in ("user", "assistant") and str(one.get("content") or "").strip()]
        while turns and turns[0]["role"] != "user":
            turns.pop(0)
        said = text or "(no words)"
        if files:
            said += "\n\n[attached, which you cannot read: " + ", ".join(
                str(one["name"]) for one in files) + "]"
        said += front
        try:
            answer = self.models.invoke(ModelRequest(
                messages=[*turns, {"role": "user", "content": said}],
                system=self.agent._system_prompt() + QUICK_RULES, max_tokens=QUICK_TOKENS,
                task="chat_fast", language=in_display()), role="fast_reply")
        except Exception:
            log.info("the quick reply could not be had", exc_info=True)
            return {"reply": "", "run": True}
        parsed = _parsed(answer.text)
        if parsed is None:
            return {"reply": str(answer.text or "").strip(), "run": True}
        return {"reply": str(parsed.get("reply") or "").strip(),
                "run": str(parsed.get("full_agent") or "run").strip().lower() != "stop"}

    # -- the full agent ----------------------------------------------------------------------
    def _begin(self, conversation_id: str, job: Dict[str, Any]) -> None:
        with self._lock:
            self._turns[job["turn"]] = {"state": "running", "text": ""}
            while len(self._turns) > MOST_TURNS:
                self._turns.pop(next(iter(self._turns)))
            self._waiting.setdefault(conversation_id, []).append(job)
            if conversation_id in self._working:
                return
            self._working.add(conversation_id)
        threading.Thread(target=self._work, args=(conversation_id,), name="chat-full-agent",
                         daemon=True).start()

    def _work(self, conversation_id: str) -> None:
        """One turn after another in a conversation; those that waited, as one."""
        while True:
            with self._lock:
                jobs = self._waiting.pop(conversation_id, [])
                if not jobs:
                    self._working.discard(conversation_id)
                    return
            self._full_turn(conversation_id, jobs)

    def _mark(self) -> int:
        """Where the record of the Monitor's changes stands."""
        if self.db is None:
            return 0
        try:
            return int(self.db.query_one("SELECT COALESCE(MAX(rowid), 0) AS n "
                                         "FROM monitor_changes")["n"])
        except Exception:
            return 0

    def _did(self, mark: int, result: Dict[str, Any]) -> Dict[str, Any]:
        """What the turn changed, said on the line of its fold: it is what
        the user must be able to see without opening it, and take back."""
        tools = [str(one.get("tool")) for one in result.get("tool_calls") or []]
        did: Dict[str, Any] = {"remembered": tools.count("lesson_add"), "rules": 0, "quiet": 0,
                               "changes": []}
        if self.db is not None:
            for row in self.db.query("SELECT id, op FROM monitor_changes WHERE rowid > ? "
                                     "AND by = 'chat' ORDER BY rowid", (mark,)):
                if row["op"] in ("add", "edit", "delete"):
                    did["rules"] += 1
                elif row["op"] == "quiet_add":
                    did["quiet"] += 1
                else:
                    continue
                did["changes"].append(row["id"])
        return did if did["remembered"] or did["changes"] else {}

    def _full_turn(self, conversation_id: str, jobs: List[Dict[str, Any]]) -> None:
        last = jobs[-1]["turn"]
        mark = self._mark()

        def told(text: str) -> None:
            with self._lock:
                if last in self._turns:
                    self._turns[last]["text"] = text
        try:
            result = self.agent.handle_message(
                "\n\n".join(one["text"] for one in jobs), conversation_id,
                attachments=[f for one in jobs for f in one["attachments"]] or None,
                mentions=[f for one in jobs for f in one["mentions"]] or None, on_text=told,
                front=jobs[-1].get("front", ""), seen=jobs[-1].get("seen"),
                after_quick={"ids": [i for one in jobs for i in one["ids"]],
                             "reply": "\n".join(one["reply"] for one in jobs if one["reply"]),
                             "did": lambda said: self._did(mark, said)})
            done = {"state": "done", "answer": result.get("answer", ""),
                    "message_id": result.get("message_id", ""), "kind": result.get("kind", "more"),
                    "nothing": bool(result.get("nothing")), "did": result.get("did") or {}}
        except Exception as exc:
            log.warning("the full agent could not answer", exc_info=True)
            done = {"state": "done", "answer": "", "message_id": "", "kind": "more",
                    "nothing": False, "error": str(exc)[:300]}
        for one in jobs:
            said = done if one["turn"] == last else {"state": "merged"}
            with self._lock:
                self._turns[one["turn"]] = said
            if callable(one["on_done"]):
                try:
                    one["on_done"](said)
                except Exception:
                    log.debug("nobody heard the full agent", exc_info=True)

    def state(self, turn: str) -> Dict[str, Any]:
        """How a full turn stands: "running" with "text" so far, "done", or
        "merged" into a later one."""
        with self._lock:
            return dict(self._turns.get(str(turn)) or {"state": "unknown"})


__all__ = ["TwoSpeed", "NOTHING_TO_ADD", "CORRECTION", "FULL_NOTE"]
