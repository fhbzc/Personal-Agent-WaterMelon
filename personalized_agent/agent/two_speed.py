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
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..common.events.models import new_id
from ..common.languages import in_display
from ..models import answers
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
#: THE FULL AGENT'S TOOLS IN ONE OR TWO SENTENCES (the user, 2026-11-07: "所有工具整体略写，
#: 一两句概况所有就行了"): the quick voice has none, and hands over whenever one is wanted.
QUICK_RULES = """

RIGHT NOW YOU ARE THE QUICK VOICE, AND THIS OVERRIDES ANYTHING ABOVE. For this one reply you have \
no tools, and you see only the last few messages: nothing of the user's memory, work, contacts, \
calendar or files. What is above is who you are and what the application has, to judge by - here \
you can do none of it. The FULL AGENT - you, with all of that and every tool - takes the message up \
after you, unless you stop it. Its answer appears folded under yours. \
With its tools the full agent can search the user's to-dos, projects and tasks, what was learned \
of them, what they were doing at a time and the web, and can read and write their files and set \
calendar entries, reminders, notifications, to-dos and what to remember. Whenever the answer \
needs any of that, leave it running.

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
- WHAT YOU CANNOT DO AND THE FULL AGENT CAN, SAY IT WILL DO (the user, 2026-11-18: "快速agent对自己不能handle, 但是完整agent \
可以handle的事情，要说等完整agent做"): anything a tool below does, or anything that needs what you cannot see, is not \
yours to refuse - say the full agent is doing it and to wait for it below, and leave it running ("full_agent": "run").
- Never say the agent cannot do what one of the full agent's tools below does: it can, after you.
- Asked to do something ON THEIR SCREEN - fill this in, click that, open it, type there - that is the user's word to \
take it (the user, 2026-11-18: "你填这个job application" was answered "我现在没法直接操作你的浏览器"): say the agent is \
taking their screen to do it; never that it cannot, never that they must first allow it.
- Never say the agent lacks their information, files or past: you cannot see them; the full agent looks.
- Never state anything about the user's data, schedule, contacts, files or past: you cannot know it.
- Never say something is done: you can do nothing here. Say it is being handled.
- When the full agent runs, say what you can and tell the user that you are looking into this and \
that more will follow below."""

#: EVERY TOOL OF THE FULL AGENT, KNOWN (the user, 2026-11-18: "快速回复也要知道他有这个能力（快速回复会知道所有的能力
#: 和description，只是不能调用)" - asked to fill a form on the screen, the quick voice said the agent could not
#: control the screen, which the full agent then did): each by its name and its whole description, to know what
#: can be done - never to call.
TOOLS_KNOWN = """

THE FULL AGENT'S TOOLS - what it can do after you. You cannot call any of them; know them so that you never \
say something cannot be done that one of them does, and so that you leave the full agent running for it:
{tools}"""


def tools_known(specs: Sequence[Any]) -> str:
    """The full agent's tools, each its name and description, for the quick voice to know."""
    lines = [f"- {one.name}: {' '.join(str(one.description or '').split())}" for one in specs]
    return TOOLS_KNOWN.format(tools="\n".join(lines)) if lines else ""


#: WHAT WAS WRITTEN WHILE IT WAITED (the user, 2026-11-09: a "1" sent before a question was asked
#: was taken as its yes - the answer to the message before it, written while this one waited, came
#: first in what the model was given). Those answers are told apart, as written after it.
WAITED_NOTE = """

[While this message waited its turn, the message before it was answered - AFTER this one was sent:
\"\"\"{said}\"\"\"
This message was written BEFORE that answer existed. It does not reply to anything that answer
says or asks, and it is never a yes to it. Do not repeat that answer.]"""

#: A PICTURE BESIDE THE ANSWER, WHEN IT HELPS (the user, 2026-11-16: "model觉得有必要画图的才画"): said to the
#: full agent only, of a message from the side panel - the one place a picture is drawn (notes.js). In the
#: answer itself, not a call of its own.
VISUAL_NOTE = """

[This chat is shown in a side panel with a PICTURE PANEL - a small screen at its top. To show the user a \
picture, use it: the block below opens the picture panel and draws your picture there (never draw a chart in \
words, as text art, or as a link to an image). The user can draw on what is in the picture panel - a mark, a \
box, words - and what they marked comes back to you with their message, "[On the picture ..., I marked: ...]"; a \
picture they drew on is sent with their message. Only when a picture \
would make the answer clearer than words - numbers to compare, a change over time, a day's schedule, a \
few things side by side - end your answer with exactly one block, after your words:
```view
{"kind": "bar", "title": "...", "x": ["...", "..."], "series": [{"name": "...", "values": [1, 2]}], "unit": "..."}
```
"kind" is one of: "bar" (amounts compared), "line" (a change over time) - both with "x", "series" (at most \
four, each as many values as "x" has labels) and optionally "unit"; "table" with "columns" and "rows" (at \
most 20); "timeline" with "items": [{"when": "...", "what": "...", "note": "..."}] in order; "stat" with \
"value", "label" and optionally "note" (one number that is the answer). Only numbers and facts you found \
with your tools or the user gave - never made up. No block when words are enough; never more than one.
Give each picture you draw a short "name". You may name the user's pictures too: "names": {"<small screen picture \
id>": "<name>"} in any block, or {"kind": "name", "page": "<id>", "name": "<name>"} alone. NEVER CHANGE A PICTURE IN \
PLACE, the user's or yours: to show something ON a picture already in the panel - the user's whiteboard or screenshot \
("small screen picture p..." in their message) or one of yours - use {"kind": "marks", "page": "<its id or name>", \
"name": "<a name for your copy>", "marks": [...]} with any of {"shape": "box" or "ellipse", "at": [x0, y0, x1, y1]}, \
{"shape": "arrow" or "line", "from": [x, y], "to": [x, y]}, {"shape": "text", "at": [x, y], "text": "..."}, {"shape": "pen", "points": \
[[x, y], ...]} - each place a fraction of the picture's width and height, 0 to 1 from its top left. A copy of the \
picture is made with your marks on it, in your own colour, put first and shown; the picture itself stays as it was. \
To redraw a picture of yours, draw it again under a new name: a new picture, the earlier one kept.]"""

FULL_NOTE = """

[A quick reply has already been shown to the user, written with none of your context and no tools:
\"\"\"{reply}\"\"\"
What you write now appears folded beneath it, and the user reads it as one voice: do not repeat it, \
greet, or comment on it. Do what the message asks, with your tools, and say what came of it. Begin \
with {correction} only if the quick reply stated a fact that is false and would mislead the user, \
and then put that fact right. If what you write asks the user something they need to answer, end it \
with {questions} - its fold then says so, and it is opened. Its saying it could not see something, was looking into it, or \
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


#: THE SCREEN OFFERED, NOT SENT (the user, 2026-11-20: "每个Input都会给图片（截图），我在想应该让agent自己决定要不要看（不用说
#: 内容，就说当前截图可以看）"; "这个对快速和full 都生效"): what each is told of the picture of the screen taken when the user
#: wrote - only that it is there to look at.
QUICK_SCREEN_NOTE = ("\n\n[A picture of the user's screen as it was when they wrote this can be looked at. Look only "
                     "when the answer needs what is on their screen: then answer with JSON alone, {\"look\": true}, "
                     "and you are shown it.]")
FULL_SCREEN_NOTE = ("\n\n[A picture of the user's screen as it was when they wrote this can be looked at: look_at_screen "
                    "shows it. Look only when the answer needs what is on their screen.]")


#: EVERY ANSWER IN THE SIDE PANEL WITH ITS QUICK REPLIES (the user, 2026-11-20: "agent的每个话都自带几个快速回复选项（我也
#: 可以点）"): written with the answer itself - no call of their own - on its last line; the panel shows them as buttons.
REPLIES_NOTE = ("\n\n[END YOUR ANSWER WITH ONE LINE: \"Quick replies: <2 to 4 short replies the user might send next, "
                "in their language, separated by |>\" - nothing after it.]")


def _with_pictures(text: str, pictures: Optional[List[Dict[str, str]]]) -> Any:
    """A message's content: its words, and the pictures that go with it as pictures."""
    pictures = [one for one in pictures or [] if one.get("data") and one.get("media_type")]
    if not pictures:
        return text
    return [{"type": "text", "text": text},
            *[{"type": "image", "source": {"type": "base64", "media_type": one["media_type"],
                                           "data": one["data"]}} for one in pictures]]


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
        #: TOLD THE MOMENT A TURN MOVES (the side panel, pushed - api/side_events.py): `on_change(turn)`, its
        #: words as they come at most each TOLD_EVERY seconds.
        self.on_change: Optional[Callable[[str], None]] = None
        self._told_at: Dict[str, float] = {}
        #: THE INFORMATION QUEUE (agent/information_queue.py): `held_note()`, what is held, for the full agent
        #: answering the side panel; `on_told(ids)`, those its reply named as told.
        self.held_note: Optional[Callable[[], str]] = None
        #: HOW THE USER TALKS (agent/talk_style.py): said to both answering in the side panel.
        self.style_note: Optional[Callable[[], str]] = None
        self.on_told: Optional[Callable[[List[str]], None]] = None
        #: The links of those a reply named as told (information_queue `links_told`) - every one kept in the reply.
        self.links_of_told: Optional[Callable[[List[str]], List[str]]] = None
        #: One told apart from the reply, a message of its own (information_queue `tell_apart(id, words)`).
        self.on_tell: Optional[Callable[[str, str], Any]] = None
        #: The picture of the screen that went with a message kept and described (chat_pictures `keep(id, seen, text)`).
        self.on_picture: Optional[Callable[[str, List[Dict[str, str]], str], Any]] = None
        #: The picture offered with the message the full agent answers now (look_at_screen), and each turn's.
        self._offer_now: Optional[Dict[str, Any]] = None
        self._add_screen_tool()

    def _add_screen_tool(self) -> None:
        """LOOK_AT_SCREEN, the full agent's: the picture of the screen offered with the message it answers - shown when
        it asks, kept with the message then (and told to the panel, which shows it with it)."""
        tools = getattr(self.agent, "tools", None)
        if tools is None or not hasattr(tools, "add"):
            return
        try:
            if any(getattr(one, "name", "") == "look_at_screen" for one in getattr(tools, "specs", []) or []):
                return
            from ..models.base import ToolSpec
            tools.add(ToolSpec("look_at_screen", "See the picture of the user's screen taken when they wrote their message "
                               "- only when the answer needs what is on their screen.",
                               {"type": "object", "properties": {}}, read_only=True), lambda args: self._look_now())
        except Exception:
            log.info("the screen could not be offered to the full agent", exc_info=True)

    def _look_now(self) -> Dict[str, Any]:
        offer = self._offer_now
        if not offer or not offer.get("pictures"):
            return {"error": "no picture of the screen went with this message"}
        self._looked(offer)
        return {"what": "the user's screen when they wrote", "_pictures": offer["pictures"]}

    def _looked(self, offer: Dict[str, Any]) -> None:
        """The picture looked at: kept with the user's message (chat_pictures), and the turn told so the panel shows it."""
        if offer.get("looked"):
            return
        offer["looked"] = True
        if callable(self.on_picture):
            try:
                self.on_picture(offer["message_id"], offer["pictures"], offer.get("text", ""))
            except Exception:
                log.debug("the picture looked at could not be kept", exc_info=True)
        turn = offer.get("turn")
        if turn:
            with self._lock:
                if turn in self._turns:
                    self._turns[turn]["picture"] = offer.get("shown") or {}
            self._changed(turn)

    def working(self, conversation_id: str) -> bool:
        """Whether the full agent is answering in that conversation now."""
        with self._lock:
            return conversation_id in self._working

    TOLD_EVERY = 0.1

    def _changed(self, turn: str, words: bool = False) -> None:
        if self.on_change is None:
            return
        now = time.monotonic()
        if words and now - self._told_at.get(turn, 0.0) < self.TOLD_EVERY:
            return
        self._told_at[turn] = now
        try:
            self.on_change(turn)
        except Exception:                          # the panel asks again
            log.debug("the turn's change could not be told", exc_info=True)

    @property
    def on(self) -> bool:
        return bool(self.config.get("chat_two_speed", True))

    # -- a message ---------------------------------------------------------------------------
    def send(self, text: str, conversation_id: Optional[str] = None,
             attachments: Optional[Sequence[str]] = None, mentions: Optional[Sequence[str]] = None,
             on_done: Optional[Callable[[Dict[str, Any]], None]] = None,
             front: str = "", seen: Optional[List[Dict[str, str]]] = None,
             visual: bool = False, quick: bool = True, offered: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """The quick reply, at once: "answer", "message_id", "conversation_id"
        and "turn", the full agent's - "" when it was stopped. Raises
        ValueError for a file no longer there. `front`: what is in front of
        the user (agent/front_screen.py), for both to read. `visual`: from the
        side panel, which draws a picture the full agent may add (VISUAL_NOTE). `quick` False (the side
        panel's Slow, the user, 2026-11-16: "chat要支持quick和slow mode"): no quick reply - the full agent
        answers, and only it."""
        attachments = [str(one) for one in attachments or []]
        mentions = [str(one) for one in mentions or []]
        described = self._described(attachments)
        conversation_id = self.conversations.ensure(conversation_id)
        earlier = self.conversations.messages(conversation_id, limit=None)[-RECENT_MESSAGES:]
        metadata: Dict[str, Any] = {"origin": "user"}
        if described:
            metadata["attachments"] = described
        user = self.conversations.add_message(conversation_id, "user", text, metadata=metadata)
        # OFFERED, NOT SENT (QUICK_SCREEN_NOTE): the picture kept aside - looked at only when asked for.
        offer = {**offered, "message_id": user["id"], "text": text, "looked": False} \
            if offered and offered.get("pictures") else None
        if seen and callable(self.on_picture):
            try:
                self.on_picture(user["id"], seen, text)
            except Exception:
                log.debug("the picture that went with the message could not be kept", exc_info=True)
        if visual and callable(self.style_note):
            try:
                front = front + (self.style_note() or "")
            except Exception:
                log.debug("how the user talks could not be read", exc_info=True)
        if visual:
            front = front + REPLIES_NOTE                # the quick voice and the full agent both end with them
        if quick and offer:
            quick = self._quick(text, earlier, described, front + QUICK_SCREEN_NOTE, seen)
            if quick.get("look"):                       # it asked to see it: asked again, shown it
                self._looked(offer)
                quick = self._quick(text, earlier, described, front, offer["pictures"])
        else:
            quick = self._quick(text, earlier, described, front, seen) if quick else {"reply": "", "run": True}
        # WHAT IS HELD TO BE TOLD, for the full agent only (never the small model): it answers whenever there is.
        held = ""
        if visual and callable(self.held_note):
            try:
                held = self.held_note() or ""
            except Exception:
                log.debug("what is held could not be read", exc_info=True)
        run = quick["run"] or bool(described) or bool(mentions) or not quick["reply"] or bool(held)
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
            if offer is not None:
                offer["turn"] = turn
            self._begin(conversation_id, {
                "turn": turn, "text": text, "ids": ids, "reply": quick["reply"],
                "attachments": attachments, "mentions": mentions, "on_done": on_done,
                "front": front + (VISUAL_NOTE if visual else "") + held
                + (FULL_SCREEN_NOTE if offer is not None and not offer.get("looked") else ""),
                "seen": offer["pictures"] if offer is not None and offer.get("looked") else seen, "offer": offer})
        return {"conversation_id": conversation_id, "answer": quick["reply"], "layer": "fast",
                "message_id": said["id"] if said else "", "turn": turn,
                "picture": (offer.get("shown") or {}) if offer is not None and offer.get("looked") else {}}

    def _described(self, ids: List[str]) -> List[Dict[str, Any]]:
        if not ids or self.files is None:
            return []
        try:
            return [self.files.described(one) for one in ids]
        except KeyError:
            raise ValueError("a file attached is no longer there")

    def _full_tools(self) -> List[Any]:
        """The tools the full agent is offered now - its own and the chat's web tools."""
        try:
            from .core.agent_core import CHAT_WEB_TOOLS
            return [*self.agent.tools.specs, *CHAT_WEB_TOOLS]
        except Exception:
            log.info("the full agent's tools could not be listed for the quick voice", exc_info=True)
            return []

    def _quick(self, text: str, earlier: List[Dict[str, Any]],
               files: List[Dict[str, Any]], front: str = "",
               seen: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
        """What the small model says; nothing, and the full agent answers. `seen`: the picture
        of the screen that goes with the message - given to it as to the full agent (the user,
        2026-11-11: it said only "Hi Fang," of an email was visible: the window's own text held
        the first line, the picture all of it). A model that sees none has it taken out by the
        manager (models/vision.py)."""
        from .chat_pictures import told
        turns = [{"role": one["role"], "content": told(one)[:RECENT_CHARS]}
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
            known = tools_known(self._full_tools())
            answer = self.models.invoke(ModelRequest(
                messages=[*turns, {"role": "user", "content": _with_pictures(said, seen)}],
                system=self.agent._system_prompt() + QUICK_RULES + known, max_tokens=QUICK_TOKENS,
                task="chat_fast", language=in_display()), role="fast_reply")
        except Exception:
            log.info("the quick reply could not be had", exc_info=True)
            return {"reply": "", "run": True}
        read = answers.read(answer, parse=_parsed)
        if not read.ok:
            text = read.text.strip()
            if text and not read.cut and "{" not in text:
                # Words, whole, and no JSON at all: they are the reply; the full agent goes on.
                return {"reply": text, "run": True}
            # NOTHING QUICK WHEN UNREADABLE (2026-11-07): its raw text - a cut-off
            # '{"reply": "...' - was shown as the reply. Nothing is shown, and the full
            # agent answers (`send` runs it when there is no quick reply).
            log.info("the quick reply was not used: %s", read.why_not)
            return {"reply": "", "run": True}
        parsed = read.data
        if parsed.get("look") is True and not seen:
            return {"reply": "", "run": True, "look": True}
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

        from .information_queue import before_the_marks, interrupting, told_apart
        early: set = set()

        def told(text: str) -> None:
            # WHAT INTERRUPTS, TOLD AT ONCE - before the reply, as soon as it is written (information_queue).
            if callable(self.on_tell):
                for ident, words in interrupting(text):
                    if ident not in early:
                        early.add(ident)
                        try:
                            self.on_tell(ident, words)
                        except Exception:
                            log.warning("what interrupted could not be told", exc_info=True)
            # The reply alone while it is written: never a mark, nor what is told apart.
            text = before_the_marks(text)
            with self._lock:
                if last in self._turns:
                    self._turns[last]["text"] = text
            self._changed(last, words=True)
        self._offer_now = jobs[-1].get("offer")
        try:
            result = self.agent.handle_message(
                "\n\n".join(one["text"] for one in jobs), conversation_id,
                attachments=[f for one in jobs for f in one["attachments"]] or None,
                mentions=[f for one in jobs for f in one["mentions"]] or None, on_text=told,
                front=jobs[-1].get("front", ""), seen=jobs[-1].get("seen"),
                chat=True,                                   # the user's own messages: the screen may be asked for
                after_quick={"ids": [i for one in jobs for i in one["ids"]],
                             "reply": "\n".join(one["reply"] for one in jobs if one["reply"]),
                             "did": lambda said: self._did(mark, said)})
            answer, apart, named = told_apart(str(result.get("answer", "") or ""))
            if named and callable(self.links_of_told):
                # EVERY LINK OF WHAT IT TOLD, KEPT (the user, 2026-11-17: "一定保留里面的所有url/超链接信息") - told in
                # the reply's own words, a link it left out added at its end, as when it is told alone (with_links).
                try:
                    missing = [one for one in self.links_of_told(named) if one not in answer]
                except Exception:
                    missing = []
                    log.debug("the links of what was told could not be read", exc_info=True)
                if missing:
                    answer = answer.rstrip() + "\n\n" + "\n".join(missing)
            if answer != str(result.get("answer", "") or ""):
                # Kept without the mark too: what the model reads again, and the user sees again, is the reply.
                try:
                    self.conversations.set_content(str(result.get("message_id") or ""), answer)
                except Exception:
                    log.debug("the reply could not be kept without its mark", exc_info=True)
                result = dict(result, answer=answer)
            # EACH TOLD APART, A MESSAGE OF ITS OWN after the reply (the user, 2026-11-17: "一个话就是回答一个的").
            for ident, words in apart:
                if callable(self.on_tell) and ident not in early:
                    try:
                        self.on_tell(ident, words)
                    except Exception:
                        log.warning("what the reply told apart could not be told", exc_info=True)
            if named and callable(self.on_told):
                try:
                    self.on_told(named)
                except Exception:
                    log.warning("what the reply told could not be marked told", exc_info=True)
            done = {"state": "done", "answer": result.get("answer", ""),
                    "message_id": result.get("message_id", ""), "kind": result.get("kind", "more"),
                    "nothing": bool(result.get("nothing")), "did": result.get("did") or {},
                    "actions": list(result.get("actions") or []),
                    "asks": bool(result.get("asks"))}
        except Exception as exc:
            log.warning("the full agent could not answer", exc_info=True)
            done = {"state": "done", "answer": "", "message_id": "", "kind": "more",
                    "nothing": False, "error": str(exc)[:300]}
        for one in jobs:
            said = done if one["turn"] == last else {"state": "merged"}
            with self._lock:
                self._turns[one["turn"]] = said
            self._told_at.pop(one["turn"], None)
            self._changed(one["turn"])
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
