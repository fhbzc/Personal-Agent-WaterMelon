"""Agent Core (sections 53-55, 66, 67).

Direct user instruction workflow:

    instruction -> reason -> immediate execution or a scheduled task ->
    Raw Edit -> respond

Answered from the instruction itself and from the conversation it was asked
in: there is no memory to search and no context gathered before the model is
asked. A conversation the user continues - one reopened from the chat's
sidebar, or the one still open - is given to the model as it was said, whole
while it fits, and with its older part summarised once it does not. A new
conversation brings nothing with it.

The Agent Core owns the tool loop so that every tool call can be validated by
the sandbox and recorded as a Raw Edit before the next model turn.
"""

from __future__ import annotations

import copy
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from ...common.events.models import new_id
from ...common.observability.tracing import NullTracer, new_trace_id
from ...models.base import ModelRequest
from ...common.languages import in_display, in_english
from ..character.manager import avatar_name
from ...models.manager import ModelUnavailable
from ..dialog.files import ConversationFolder, chat_root, place
from .tools import ToolRegistry, build_registry, result_to_text
from ...scheduler.core.given import now_line

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are {agent_name}, a personal desktop agent for one user.

You can act on the user's workspace, and you can schedule work for later.
Answer from what you are asked and from this conversation so far; there is no
memory to search.

1. Decide whether the task is IMMEDIATE (do it now, in this reply) or DELAYED
   (it should happen at a later time). For a delayed task call `schedule_task`
   instead of pretending to do it now.
2. You may only write inside the user's authorized workspace. Attempts outside
   it are refused by the system, not by you. A file you write with a relative
   path is kept in this conversation's own folder ({chat_folder}); say
   where it is when you make one.
3. Never reveal credentials, API keys or tokens; you do not have access to them.
4. Be concise and concrete.

What this application has - each its own thing, never taken for another:
- Calendar: this application's own entries, which last. `schedule_task` puts
  one on it - a meeting, a reminder, something at a time - and
  `list_scheduled_tasks` and `cancel_scheduled_task` see and remove them. It
  is not Google's or Outlook's calendar, not a browser, and not anything the
  program running you has of its own; none of those stands in for it. An
  entry only notifies: nothing on the calendar is carried out.
- Tasks: what the agent grouped from the user's activity, with their stages.
  Not calendar entries - `schedule_task` does not make one.
- To-do: things for the user to do, which they can hand over to Work.
- Work: longer pieces of work handed to the agent, each in its own folder.
- Connections: the people around the user and what they are doing, as the
  user saw them. The agent never contacts anyone.
- Notifications: `notify_user` tells the user now; for later, `schedule_task`
  with notify="notify".
- The web: search it and read public pages, to answer from what is there now.
You act only through the tools offered here. For anything above you have no
tool for, say where it is in the application - never that you did it.

Putting something on the calendar:
- A time not wholly said - no date, no time, no zone - is taken from the
  conversation and the clock below, on the user's own clock unless they name
  another place's. Do not stop to ask: put it on, then say what you took.
- A meeting goes on quietly (notify="silent") unless the user asks to be
  reminded of it.
- Say it is on the calendar only once `schedule_task` gave back its task_id,
  with the date, the time and the zone it is at.

{now}
Workspace: {workspace}
"""

#: THE WEB, FOR CHAT (the user, 2026-10-27): the app's search and page reader.
#: A client with search of its own is never given web_search beside it
#: (claude_code_provider, codex_provider); fetch_url reads a page as it is now.
from ...models.base import ToolSpec as _ToolSpec
CHAT_WEB_TOOLS = [
    _ToolSpec("web_search", "Search the public web. Returns titles, addresses and "
              "snippets. Public results only; nothing is signed in to.",
              {"type": "object", "properties": {"query": {"type": "string"}},
               "required": ["query"]}),
    _ToolSpec("fetch_url", "Read a public http or https page as it is now, as text "
              "with its links: an address found in a search, on a page read or in "
              "the conversation - or one that carries nothing of the user's.",
              {"type": "object", "properties": {"url": {"type": "string"}},
               "required": ["url"]}),
]
CHAT_WEB_TOOL_NAMES = {one.name for one in CHAT_WEB_TOOLS}

#: Added to the prompt when the older part of the conversation is summarised.
EARLIER_IN_CONVERSATION = """
Earlier in this conversation, summarised (the messages after it follow in full):
{summary}
"""

#: How much of a conversation's earlier messages the model is given word for
#: word, in characters - about six thousand tokens. A conversation that fits
#: is given whole: the user who reopens one from the sidebar and asks "and the
#: second one?" is asking about something said in it, and a summary of a short
#: conversation is only a less exact copy of it.
HISTORY_BUDGET_CHARS = 24_000

#: Once it does not fit, how much of the end of it stays word for word beside
#: the summary of the rest. The last few turns are what a follow-on question
#: is most likely about, and the exact wording matters most there.
RECENT_BUDGET_CHARS = 12_000

#: The longest one earlier message is given at. A whole file pasted into the
#: chat a week ago should not crowd out everything said since.
MESSAGE_CHARS = 8_000

SUMMARY_SYSTEM = """You summarise the earlier part of a conversation between a
user and their personal desktop agent, for the agent to read before it answers
the next message. Keep what a later message could refer back to: what the user
asked for and decided, facts and names they gave, what the agent did (files it
wrote, tasks it scheduled, with their times) and what it promised, and anything
left open. Plain prose or short bullets. No preamble, and nothing that was
not said."""

SUMMARY_MAX_TOKENS = 1200


class AgentCore:
    def __init__(
        self,
        *,
        config: Any,
        runtime: Any,
        scheduler: Any,
        notifications: Any,
        models: Any,
        recorder: Any,
        conversations: Any,
        bus: Any = None,
        observability: Any = None,
        tracer: Any = None,
        max_tool_iterations: int = 8,
    ) -> None:
        self.tracer = tracer or NullTracer()
        self.config = config
        self.runtime = runtime
        self.scheduler = scheduler
        self.notifications = notifications
        self.models = models
        self.recorder = recorder
        self.conversations = conversations
        self.bus = bus
        self.observability = observability
        self.max_tool_iterations = max_tool_iterations
        self._lock = threading.RLock()
        #: One lock per conversation. Two turns of the same conversation
        #: taken at once would each read the conversation without the other
        #: and write their answers in whichever order they finished - so a
        #: conversation's turns are taken one at a time. Different
        #: conversations, and scheduled runs, are not held up by each other.
        self._conversation_locks: Dict[str, threading.Lock] = {}
        self.tools: ToolRegistry = build_registry(
            runtime=runtime, scheduler=scheduler,
            notifications=notifications, config=config,
        )

    # -- prompt -------------------------------------------------------------
    def _system_prompt(self, summary: str = "") -> str:
        prompt = SYSTEM_PROMPT.format(
            agent_name=avatar_name(self.config, getattr(self, "characters", None)),
            # The user's clock by name, not UTC: a time to schedule is given
            # as a clock shows it, and the offset worked out by code
            # (scheduler/core/given.py).
            now=now_line(),
            workspace=str(self.runtime.policy.workspace_root),
            chat_folder=str(chat_root(self.runtime.policy.workspace_root)
                            / "<this conversation's title>"),
        )
        if summary:
            prompt += EARLIER_IN_CONVERSATION.format(summary=summary.strip())
        return prompt

    # -- main entry points --------------------------------------------------
    def handle_message(
        self,
        text: str,
        conversation_id: Optional[str] = None,
        origin: str = "user",
        *,
        debug: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """One turn of a conversation.

        The model is given what was said in this conversation before this
        message (see `_past`); a conversation id that is unknown, or none,
        starts a new conversation, which has nothing before it.

        `debug`, when given, is handed a snapshot of each step - what the
        model was sent and what came back, every tool call and its result -
        for the chat debugger. An ordinary chat passes nothing and nothing
        is copied.
        """
        conversation_id = self.conversations.ensure(conversation_id)
        with self._conversation_lock(conversation_id):
            # Read before this message is written: the history is what came
            # before it, and the message itself is the turn being answered.
            earlier = self.conversations.messages(conversation_id, limit=None)
            # Where it came from - the Chat debugger's are tests, and not
            # read as the user's own words (heartbeat `_direct_chat`).
            self.conversations.add_message(conversation_id, "user", text,
                                           metadata={"origin": origin} if origin else None)
            result = self.run_instruction(
                text, {"conversation_id": conversation_id, "origin": origin,
                       "earlier": earlier}, debug=debug)
            said = self.conversations.add_message(
                conversation_id, "assistant", result["answer"],
                metadata={"task_id": result["task_id"],
                          "classification": result["classification"],
                          # What it searched for and read, and which tools
                          # it used - for the heartbeat beside the reply.
                          "looked_up": result.get("looked_up") or [],
                          "tools_used": sorted({one["tool"] for one in
                                                result.get("tool_calls") or []}),
                          **({"origin": origin} if origin else {})},
            )
        result["conversation_id"] = conversation_id
        # Which reply this is, so a follow-up can be asked about it.
        result["message_id"] = said["id"]
        return result

    def _conversation_lock(self, conversation_id: str) -> threading.Lock:
        with self._lock:
            return self._conversation_locks.setdefault(conversation_id, threading.Lock())

    @staticmethod
    def _emit_debug(debug: Any, kind: str, data: Dict[str, Any]) -> None:
        """A snapshot for whoever is watching, if anyone is.

        A copy, so a watcher that edits what it was given edits nothing of
        the turn; and a watcher that fails is logged and forgotten - being
        watched must never be the reason a turn did not finish.
        """
        if not callable(debug):
            return
        try:
            debug(kind, copy.deepcopy(data))
        except Exception:
            log.debug("chat debug callback failed", exc_info=True)

    # -- the conversation so far ---------------------------------------------
    def _past(self, conversation_id: Optional[str], earlier: List[Dict[str, Any]],
              task_id: str, debug: Any = None) -> Tuple[List[Dict[str, Any]], str]:
        """What the model is given of the conversation before this message.

        Every earlier message word for word while they fit the budget. Past
        it, the older part is summarised - by the model, once, kept with the
        conversation and used again for as long as what follows it still
        fits beside it - and the most recent turns stay word for word.

        Returns the messages to put before this one and the summary of what
        comes before those ("" when nothing is summarised).
        """
        turns = [one for one in earlier
                 if one.get("role") in ("user", "assistant")
                 and str(one.get("content") or "").strip()]
        if not turns or not conversation_id:
            return [], ""
        if _chars(turns) <= HISTORY_BUDGET_CHARS:
            return _verbatim(turns), ""

        ids = [one["id"] for one in turns]
        cached = self.conversations.summary(conversation_id)
        covered = 0
        if cached and cached.get("through_id") in ids:
            covered = ids.index(cached["through_id"]) + 1
        else:
            cached = None
        # Still fresh: what was said since it was written fits beside it.
        if cached and covered < len(turns) and (
                _chars(turns[covered:]) + len(cached["summary"]) <= HISTORY_BUDGET_CHARS):
            return _verbatim(turns[covered:]), cached["summary"]

        # Stale, or never written. The end stays word for word - at least the
        # last exchange, however long - and starts with something the user
        # said, so the turns still read as a conversation.
        split, kept = len(turns), 0
        while split > 0:
            size = min(len(str(turns[split - 1]["content"])), MESSAGE_CHARS)
            if kept + size > RECENT_BUDGET_CHARS:
                break
            kept += size
            split -= 1
        split = min(split, max(0, len(turns) - 2))
        while split < len(turns) - 1 and turns[split]["role"] != "user":
            split += 1
        if cached and covered >= split:
            return _verbatim(turns[covered:]), cached["summary"]
        prior = cached["summary"] if cached else ""
        older = turns[covered:split] if cached else turns[:split]
        self._step(task_id, "Reading the conversation so far",
                   f"summarising {split} earlier message{'s' if split != 1 else ''}")
        summary = self._summarise(prior, older, task_id, debug)
        if summary:
            self.conversations.save_summary(conversation_id, turns[split - 1]["id"],
                                            split, summary)
        else:
            # Not saved: the next turn tries again. Said, so the model does
            # not take the conversation as starting where the turns below do.
            summary = prior or (f"({split} earlier message{'s' if split != 1 else ''} "
                                "of this conversation could not be summarised and "
                                "are not shown.)")
        return _verbatim(turns[split:]), summary

    def _summarise(self, prior: str, older: List[Dict[str, Any]], task_id: str,
                   debug: Any = None) -> str:
        """The older part of a conversation in a few paragraphs, or "".

        Given the summary written last time, only what was said since is
        read again: it is extended, never rebuilt from the start.
        """
        transcript = "\n\n".join(
            f"{'User' if one['role'] == 'user' else 'Agent'}: "
            f"{_clip(str(one['content']))}" for one in older)
        content = (f"The summary so far:\n{prior}\n\nWhat was said after it:\n{transcript}"
                   if prior else transcript)
        request = ModelRequest(
            messages=[{"role": "user", "content": content}],
            system=SUMMARY_SYSTEM, max_tokens=SUMMARY_MAX_TOKENS,
            task="chat_history_summary",
            # Read by the model before it answers, not by the user.
            language=in_english("the summary"),
            debug=(lambda kind, data: self._emit_debug(
                debug, kind, {**data, "task_id": task_id, "purpose": "history_summary"}))
            if callable(debug) else None,
        )
        try:
            response = self.models.invoke(request, role="primary")
        except Exception as exc:          # a summary is help, never a failure
            log.info("could not summarise the earlier conversation: %s", exc)
            self._emit_debug(debug, "history_summary", {
                "task_id": task_id, "error": f"{type(exc).__name__}: {exc}"})
            return ""
        summary = (response.text or "").strip()
        self._emit_debug(debug, "history_summary", {
            "task_id": task_id, "messages_summarised": len(older),
            "extended_previous": bool(prior), "summary": summary})
        return summary

    def follow_up(self, message_id: str, about: str, question: str) -> Dict[str, Any]:
        """A follow-up about one reply - a word or phrase in it, or the whole
        of it - asked ON ITS OWN: the question is the whole prompt, with
        nothing of the conversation around it, and it is not added to the
        conversation. Its answer has no follow-ups of its own."""
        message = self.conversations.message(message_id)
        if message is None or message.get("role") != "assistant":
            raise ValueError("there is no such reply")
        about = str(about or "").strip()
        question = str(question or "").strip() or f"what is {about or message['content']}"
        response = self.models.invoke(
            ModelRequest(messages=[{"role": "user", "content": question}],
                         max_tokens=1500, task="chat_followup",
                         language=in_display(),
                         metadata={"context_summary": question[:200]}),
            role="primary")
        return self.conversations.add_followup(
            message, about, question, (response.text or "").strip())

    def _model_label(self) -> str:
        """The model doing the thinking, named for the person waiting."""
        try:
            return self.models.resolve("primary").label
        except Exception:                # pragma: no cover - naming is cosmetic
            return "the model"

    def _step(self, task_id: str, stage: str, detail: str = "") -> None:
        """Say what is happening now, for whoever is waiting on the answer.

        A request that takes twenty seconds and says only "thinking" is
        indistinguishable from one that has hung (section 15.1).
        """
        if self.observability is not None:
            try:
                self.observability.task_step(task_id, stage, detail)
            except Exception:            # pragma: no cover - never break a turn
                log.debug("could not record progress", exc_info=True)

    def run_instruction(self, instruction: str, context: Optional[Dict[str, Any]] = None,
                        *, debug: Optional[Callable[[str, Dict[str, Any]], None]] = None,
                        ) -> Dict[str, Any]:
        context = context or {}
        task_id = context.get("task_id") or new_id("task")
        origin = context.get("origin", "user")
        started = datetime.now(timezone.utc)
        if self.observability is not None:
            self.observability.task_started(task_id, origin, instruction[:120])
        self.recorder.instruction_received(instruction, task_id, source=origin)

        # One trace_id spans the whole instruction so the Debugger can
        # reconstruct it (14.6).
        trace_id = new_trace_id()
        self._emit_debug(debug, "instruction", {
            "task_id": task_id, "trace_id": trace_id,
            "instruction": instruction, "origin": origin,
            "conversation_id": context.get("conversation_id"),
        })
        try:
            with self.tracer.span(
                "agent.instruction", trace_id=trace_id, task_id=task_id,
                event_type="agent.instruction", inputs=instruction[:200],
                metadata={"kind": "user_instruction", "origin": origin},
            ):
                history, summary = self._past(context.get("conversation_id"),
                                              context.get("earlier") or [],
                                              task_id, debug)
                self._emit_debug(debug, "context", {
                    "task_id": task_id, "trace_id": trace_id,
                    "conversation_id": context.get("conversation_id"),
                    "earlier_messages": len(context.get("earlier") or []),
                    "history": history, "summary": summary,
                })
                # The conversation's own folder, for the files it makes - given
                # to each call, so conversations answered at once keep apart.
                cid = context.get("conversation_id")
                folder = (ConversationFolder(self.conversations, cid,
                                             self.runtime.policy.workspace_root)
                          if cid and self.conversations is not None else None)
                result = self._reason(instruction, task_id, trace_id, started,
                                      withheld=WITHHELD_FROM.get(origin, ()),
                                      history=history, summary=summary, debug=debug,
                                      folder=folder)
        except Exception as exc:
            self._emit_debug(debug, "error", {
                "task_id": task_id, "trace_id": trace_id,
                "error": f"{type(exc).__name__}: {exc}"})
            if self.observability is not None:
                self.observability.task_finished(task_id, "error", str(exc))
            raise
        self._emit_debug(debug, "completion", result)
        if self.bus is not None:
            self.bus.publish("agent.instruction_completed", result)
        return result

    def _reason(self, instruction: str, task_id: str, trace_id: str,
                started: datetime, withheld: tuple = (),
                history: Optional[List[Dict[str, Any]]] = None, summary: str = "",
                debug: Any = None,
                folder: Optional[ConversationFolder] = None) -> Dict[str, Any]:

        messages: List[Dict[str, Any]] = [*(history or []),
                                          {"role": "user", "content": instruction}]
        tool_calls_made: List[Dict[str, Any]] = []
        answer = ""
        classification = "immediate"
        error: Optional[str] = None
        # THE WEB'S CHECKS FOR THIS TURN (runtime/web_guard), and what was
        # looked up in it: what the user said and the conversation so far
        # are theirs - the addresses in them exist, their words do not travel
        # out in one. A client with search of its own is held to it through
        # its hooks; the app's web tools ask it themselves.
        import json as _json
        from ...runtime.web_guard import WebGuard
        guard = WebGuard()
        guard.know(_json.dumps(messages, ensure_ascii=False, default=str), "user")
        made = {"classification": classification, "iteration": 0}

        def run_tool(name: str, arguments: Dict[str, Any], call_id: str = "") -> Any:
            """ONE TOOL, RUN - by the client in the call (the checked route:
            `tool_runner`) or here, from a call the model wrote out."""
            iteration = made["iteration"]
            arguments = arguments or {}
            self._emit_debug(debug, "tool_call", {
                "task_id": task_id, "trace_id": trace_id, "iteration": iteration,
                "id": call_id, "name": name, "arguments": arguments})
            self._step(task_id, f"Using {name}", _tool_summary(arguments))
            # Not offered is not enough: a call written out by hand is
            # still refused.
            if name in withheld:
                outcome: Any = {"error": f"{name} is not available to a scheduled run"}
            elif name in CHAT_WEB_TOOL_NAMES:
                outcome = self._web(guard, name, arguments)
            else:
                outcome = self._execute_tool(task_id, name,
                                             self._placed(name, arguments, folder))
            self._emit_debug(debug, "tool_result", {
                "task_id": task_id, "trace_id": trace_id, "iteration": iteration,
                "id": call_id, "name": name, "arguments": arguments,
                "result": outcome,
                "ok": not (isinstance(outcome, dict) and outcome.get("error"))})
            self._step(task_id, f"{name} finished", result_to_text(outcome, 120))
            tool_calls_made.append({"tool": name, "arguments": arguments,
                                    "result": result_to_text(outcome, 400)})
            if name == "schedule_task" and isinstance(outcome, dict) \
                    and outcome.get("task_id"):
                made["classification"] = "delayed"  # section 53
            return outcome

        for iteration in range(self.max_tool_iterations):
            made["iteration"] = iteration
            self._step(task_id, "Thinking",
                       f"{self._model_label()}"
                       + (f", step {iteration + 1}" if iteration else ""))
            from ...runtime.filesystem import unreadable_for
            request = ModelRequest(
                messages=messages,
                # The client's own reading tools, denied where the chat may
                # not read.
                native_read=unreadable_for(self.runtime.policy),
                system=self._system_prompt(summary),
                # The app's tools, and its web tools - which a client with
                # search of its own is given only to read pages with.
                tools=[spec for spec in [*self.tools.specs, *CHAT_WEB_TOOLS]
                       if spec.name not in withheld],
                # ON THE CHECKED ROUTE (the user, 2026-10-27, Option A): a
                # client that can runs the tools in the call, and its own
                # search and reading are held to the web's checks.
                tool_runner=lambda name, arguments: run_tool(name, arguments),
                web_guard=guard,
                max_tokens=int(self.config.get("max_output_tokens", 4096)),
                task="agent_instruction",
                language=in_display(),
                # Bound to this step, so what the manager and the provider
                # report can be told apart from the next step's.
                debug=(lambda kind, data, step=iteration: self._emit_debug(
                    debug, kind, {**data, "task_id": task_id,
                                  "trace_id": trace_id, "iteration": step}))
                if callable(debug) else None,
            )
            if callable(debug):
                self._emit_debug(debug, "model_request", {
                    "task_id": task_id, "trace_id": trace_id, "iteration": iteration,
                    "role": "primary", "model": self._model_label(),
                    "system": request.system, "messages": request.messages,
                    "tools": [tool.to_dict() for tool in request.tools],
                    "max_tokens": request.max_tokens,
                })
            try:
                response = self.models.invoke(request, role="primary")
            except ModelUnavailable as exc:
                error = str(exc)
                self._emit_debug(debug, "model_error", {
                    "task_id": task_id, "trace_id": trace_id,
                    "iteration": iteration, "error": error})
                answer = (
                    "I cannot reason about this right now: the configured model is "
                    f"unavailable ({exc.availability.reason or exc.availability.state.value}). "
                    "Open Settings > Models to fix the configuration."
                )
                # A lost sign-in is asked for by the application itself
                # ("Sign in to ... again"): one problem, one message (88.1).
                from ...models.base import SIGN_IN_STATES
                if (exc.availability.requires_user_action()
                        and exc.availability.state not in SIGN_IN_STATES):
                    self.notifications.notify(
                        "Model unavailable", answer, kind="warning",
                        dedupe_key="agent.model_unavailable",
                        signature=exc.availability.state.value,
                        action_hint="Open Settings > Models.",
                    )
                break

            if callable(debug):
                self._emit_debug(debug, "model_response", {
                    "task_id": task_id, "trace_id": trace_id, "iteration": iteration,
                    "text": response.text,
                    "tool_calls": [{"id": call.id, "name": call.name,
                                    "arguments": call.arguments}
                                   for call in response.tool_calls],
                    "raw_content": response.raw_content,
                    "usage": {"input_tokens": response.usage.input_tokens,
                              "output_tokens": response.usage.output_tokens},
                    "stop_reason": response.stop_reason,
                    "model": response.model, "provider": response.provider,
                })
            if response.text:
                answer = response.text
            if not response.tool_calls:
                break

            messages.append({"role": "assistant",
                             "content": response.raw_content or response.text})
            results_block = []
            for call in response.tool_calls:
                outcome = run_tool(call.name, call.arguments, call.id)
                # A picture it read goes as a picture (models/vision.py).
                from ...models.vision import tool_content
                results_block.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": tool_content(outcome)
                        if isinstance(outcome, dict) and outcome.get("_pictures")
                        else result_to_text(outcome),
                    }
                )
            messages.append({"role": "user", "content": results_block})
        else:
            error = error or "tool iteration limit reached"

        # Blank is not an answer either: a reply of spaces drew an empty
        # bubble that looked like the chat had failed to render.
        if not answer.strip():
            answer = "I could not produce an answer for this request."
        self._step(task_id, "Writing the answer", "")

        self.recorder.agent_response(task_id, instruction, answer)
        if self.observability is not None:
            self.observability.task_finished(task_id, "error" if error else "completed",
                                             error or "")
        classification = made["classification"]
        report = {
            "task_id": task_id,
            "trace_id": trace_id,
            "answer": answer,
            "classification": classification,
            "tool_calls": tool_calls_made,
            # What it searched for and read, in order - by the app's tools or
            # the client's own (the guard's record): kept with the reply, for
            # the heartbeat to read beside it.
            "looked_up": list(guard.looked),
            "error": error,
            "duration_seconds": round(
                (datetime.now(timezone.utc) - started).total_seconds(), 2
            ),
        }
        return report

    # -- tools --------------------------------------------------------------
    @staticmethod
    def _web(guard: Any, name: str, arguments: Dict[str, Any]) -> Any:
        """The app's web search, or a page read - held to the web's checks
        (runtime/web_guard), as Work's and Connections' are."""
        import json as _json
        import urllib.parse
        from ..work.tools import fetch, search
        if name == "web_search":
            query = str(arguments.get("query") or "").strip()
            if not query:
                return {"error": "say what to search for"}
            refused = guard.check_query(query)
            if refused:
                return {"error": refused}
            said = search(query)
            guard.know(_json.dumps(said, ensure_ascii=False, default=str), "web", "search")
            guard.note("search", query)
            return said
        url = str(arguments.get("url") or "").strip()
        refused = guard.check_address(url)
        if refused:
            return {"error": refused}
        said = fetch(url)
        if isinstance(said, dict):
            guard.know(" ".join([str(said.get("url") or url), str(said.get("text") or "")]
                                + [str(one) for one in said.get("links") or []]),
                       "web", urllib.parse.urlsplit(url).netloc)
            if not said.get("error"):
                guard.note("read", url)
            if isinstance(said.get("text"), str):
                said = dict(said, text=said["text"][:12000])
        return said

    @staticmethod
    def _placed(name: str, arguments: Dict[str, Any],
                folder: Optional[ConversationFolder]) -> Dict[str, Any]:
        """A relative path, in the conversation's own folder (dialog/files.py)."""
        try:
            return place(name, arguments, folder)
        except OSError as exc:
            log.warning("no folder for the conversation's files: %s", exc)
            return arguments

    def _execute_tool(self, task_id: str, name: str, arguments: Dict[str, Any]) -> Any:
        try:
            with self.tracer.span(
                f"agent.tool.{name}", task_id=task_id, event_type="agent.tool",
                inputs=arguments, metadata={"tool": name},
            ) as span:
                outcome = self.tools.call(name, arguments)
                span.output_summary = result_to_text(outcome, 160)
            ok = not (isinstance(outcome, dict) and outcome.get("error"))
        except Exception as exc:
            log.exception("tool %s failed", name)
            outcome = {"error": f"{type(exc).__name__}: {exc}"}
            ok = False
        self.recorder.tool_used(task_id, name, arguments, result_to_text(outcome, 600), ok=ok)
        if self.observability is not None:
            self.observability.increment(f"agent.tool.{name}")
        return outcome

    # -- scheduler bridge ---------------------------------------------------
    def scheduled_runner(self, instruction: str, context: Dict[str, Any]) -> str:
        return self.run_instruction(instruction, context)["answer"]

    def status(self) -> Dict[str, Any]:
        return {
            "tools": self.tools.describe(),
            "max_tool_iterations": self.max_tool_iterations,
        }


#: Tools a run does not get, by who started it. A scheduled task that runs
#: the agent does not schedule: on 7 Oct a check the old heartbeat had set
#: read its own words ("on Wednesday morning this checks...") as the user
#: asking, and scheduled a second task instead of checking. Tasks are made
#: when the user asks in the chat, or by the heartbeat's own step - never
#: by a task.
WITHHELD_FROM = {"scheduler": ("schedule_task",)}


#: Arguments that say what a call is *about*, in the order they answer it.
#:
#: The line exists to tell the reader what the agent is doing, and the first
#: two arguments as written are not reliably that: a call written
#: ``{"limit": 40}`` read "Using search_memory limit: 40", which says how much
#: of something the reader cannot see.
WHAT_IT_IS_ABOUT = ("queries", "query", "about", "title", "instruction",
                    "question", "id", "path", "folder")


def _chars(turns: List[Dict[str, Any]]) -> int:
    return sum(min(len(str(one.get("content") or "")), MESSAGE_CHARS) for one in turns)


def _clip(text: str) -> str:
    if len(text) <= MESSAGE_CHARS:
        return text
    return text[:MESSAGE_CHARS] + f"\n[... {len(text) - MESSAGE_CHARS} more characters not shown]"


def _verbatim(turns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Stored messages as the model's message list: the role and the words."""
    return [{"role": one["role"], "content": _clip(str(one["content"]))} for one in turns]


def _tool_summary(arguments: Any) -> str:
    """The arguments of a tool call, short enough to sit in a progress line."""
    if not isinstance(arguments, dict) or not arguments:
        return ""
    ordered = [key for key in WHAT_IT_IS_ABOUT if key in arguments]
    ordered += [key for key in arguments if key not in ordered]
    parts = []
    for key in ordered[:2]:
        value = arguments[key]
        text = ", ".join(str(v) for v in value) if isinstance(value, list) \
            else str(value)
        parts.append(f"{key}: {text[:40]}" + ("…" if len(text) > 40 else ""))
    return ", ".join(parts)
