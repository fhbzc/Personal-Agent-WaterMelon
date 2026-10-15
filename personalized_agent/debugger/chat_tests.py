"""The chat debugger's scripted scenarios.

Ported from PR #4 (by daniela-miaut), rebuilt on the chat as it is now: there
is no memory search any more, so the scenarios that exercised it are gone,
and the chat now reads the conversation it is asked in - which two new ones
check, the long conversation summarised and the new one that starts empty.

Each scenario builds a whole application the ordinary way and scripts only
the model, at the point the agent calls it - so what is tested is the real
prompt, the real tool registry, the real sandbox and the real scheduler, and
no credential or network is needed.

THE THROWAWAY APPLICATION LIVES UNDER THIS ONE'S OWN DATA ROOT -
``<root>/internal/chat_debugger/<run>/`` - never in the system's temporary
folder: nothing this application does on the user's behalf writes outside the
root they gave it. It is deleted when the scenario ends, pass or fail.
"""

from __future__ import annotations

import copy
import json
import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..common.events.models import new_id
from ..models.base import (AvailabilityState, FailureClass, ModelAvailability,
                           ModelResponse, ModelUsage, ToolCall)
from ..models.manager import ModelUnavailable

log = logging.getLogger(__name__)

#: Where under the data root the throwaway applications go.
SCRATCH_DIR = "chat_debugger"

TESTS = [
    {"id": "chat.prompt_context", "name": "Prompt and tools",
     "description": "The system prompt, the message list and the tools the model is "
                    "actually sent for the first message of a new conversation."},
    {"id": "chat.workspace_write_read", "name": "Workspace write and read",
     "description": "Write a file in the workspace and read it back through the runtime."},
    {"id": "chat.workspace_denial", "name": "Writing outside the workspace",
     "description": "A write outside the workspace is refused by the runtime, and the "
                    "chat carries on."},
    {"id": "chat.unknown_tool", "name": "A tool that does not exist",
     "description": "A call to an unknown tool is answered with an error the model can "
                    "recover from."},
    {"id": "chat.delayed_task", "name": "Scheduling for later",
     "description": "A task for later goes to the scheduler, and the turn is classed "
                    "as delayed."},
    {"id": "chat.model_unavailable", "name": "Model unavailable",
     "description": "The user is told plainly that the model cannot be used, and why."},
    {"id": "chat.iteration_cap", "name": "Tool step limit",
     "description": "A model that keeps calling tools is stopped at the step limit."},
    {"id": "chat.blank_response", "name": "A blank answer",
     "description": "An empty or whitespace-only answer is replaced by a plain fallback."},
    {"id": "chat.history_unicode", "name": "Conversation so far, and Unicode",
     "description": "A continued conversation gives the model its earlier turns in "
                    "order, word for word, with Unicode intact."},
    {"id": "chat.history_summary", "name": "A long conversation summarised",
     "description": "Past the budget, the older part of a conversation is summarised "
                    "once and kept; the latest turns stay word for word."},
    {"id": "chat.new_conversation", "name": "A new conversation starts empty",
     "description": "A new conversation is given nothing of any other conversation."},
]


class _ScriptedModels:
    """The model manager with its answers written in advance.

    Everything but `invoke` is the real manager's, so the model the turn
    names in its progress is still the configured one.
    """

    def __init__(self, original: Any, script: List[Any]) -> None:
        self.original = original
        self.script = list(script)
        self.requests: List[Any] = []

    def resolve(self, role: str = "primary") -> Any:
        return self.original.resolve(role)

    def invoke(self, request: Any, role: str = "primary") -> ModelResponse:
        self.requests.append(copy.deepcopy(request))
        if not self.script:
            return ModelResponse(text="Script complete.", model="offline-1",
                                 provider="offline")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            item = item(request)
        return item


def _response(text: str = "", *calls: ToolCall) -> ModelResponse:
    return ModelResponse(
        text=text, tool_calls=list(calls), usage=ModelUsage(12, 5),
        stop_reason="tool_use" if calls else "end_turn",
        raw_content=([{"type": "tool_use", "id": call.id, "name": call.name,
                       "input": call.arguments} for call in calls] if calls else text),
        model="offline-1", provider="offline")


def _call(n: int, name: str, **arguments: Any) -> ToolCall:
    return ToolCall(id=f"chat_debug_{n}", name=name, arguments=arguments)


def _check(name: str, passed: Any, detail: Any) -> Dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": str(detail)[:4000]}


def _throwaway(where: Path) -> Any:
    """A whole application of its own, under `where`, built the ordinary way.

    Its settings are written before it is built: an application that finds
    an empty settings folder takes it for a first start and moves the state
    of an older installation in - and the user's older installation is the
    last thing this should touch. Logging and the keychain stay the running
    application's (see AgentApplication).
    """
    from ..app import AgentApplication

    agent_root = where.resolve() / "agent"
    settings = agent_root / "internal" / "settings"
    settings.mkdir(parents=True, exist_ok=True)
    (settings / "settings.json").write_text(json.dumps({
        "agent_root": str(agent_root),
        "workspace_root": str(agent_root / "workspace"),
        "listener_enabled": False,
    }), encoding="utf-8")
    return AgentApplication(root=agent_root, configure_logs=False, use_keyring=False)


def run_test(test_id: str, debug: Callable[[str, Any], None], *,
             where: Optional[Path] = None) -> Dict[str, Any]:
    """Run one scenario; KeyError for one that does not exist.

    `where` is the folder the throwaway application is built in - inside the
    running application's data root, chosen by the caller - and it is gone
    when this returns.
    """
    definition = next((one for one in TESTS if one["id"] == test_id), None)
    if definition is None:
        raise KeyError(test_id)
    if where is None:
        raise RuntimeError("a scenario needs a folder under the application's data root")
    where = Path(where)
    where.mkdir(parents=True, exist_ok=False)
    app = None
    try:
        app = _throwaway(where)
        return _scenario(app, test_id, debug, where)
    finally:
        if app is not None:
            try:
                app.db.close()
            except Exception:              # pragma: no cover - closing is tidying
                log.debug("could not close the throwaway database", exc_info=True)
        shutil.rmtree(where, ignore_errors=True)
        if where.exists():                 # pragma: no cover - a file still held
            log.warning("could not delete the chat debugger's folder %s", where)


def _run(app: Any, script: List[Any], text: str, conversation_id: Optional[str] = None,
         max_iterations: Optional[int] = None,
         debug: Optional[Callable[[str, Any], None]] = None):
    models = _ScriptedModels(app.models, script)
    app.agent.models = models
    if max_iterations is not None:
        app.agent.max_tool_iterations = max_iterations
    # The debug callback goes to the agent unchanged, so the events are
    # exactly the ones an ordinary chat turn would produce.
    result = app.agent.handle_message(text, conversation_id, debug=debug)
    return result, models


def _scenario(app: Any, test_id: str, debug: Callable[[str, Any], None],
              where: Path) -> Dict[str, Any]:
    events: List[Any] = []

    def capture(kind: str, data: Any) -> None:
        events.append((kind, copy.deepcopy(data)))
        debug(kind, data)

    debug("fixture", {"action": "built a throwaway application for this run",
                      "agent_root": str(app.root),
                      "workspace": str(app.paths.workspace_dir),
                      "model": "scripted where the agent calls the model"})
    result: Dict[str, Any] = {}
    checks: List[Dict[str, Any]] = []

    if test_id == "chat.prompt_context":
        result, models = _run(app, [_response("Hello.")], "What can you do?", debug=capture)
        request = models.requests[0]
        names = [tool.name for tool in request.tools]
        checks = [
            _check("the system prompt names the agent, the time and the workspace",
                   "personal desktop agent" in request.system
                   and "Current UTC time:" in request.system
                   and str(app.paths.workspace_dir) in request.system, request.system[:800]),
            _check("a new conversation sends only the message itself",
                   request.messages == [{"role": "user", "content": "What can you do?"}],
                   request.messages),
            _check("the chat's tools are offered", {"schedule_task", "write_file",
                                                    "read_file"} <= set(names), names),
            _check("the transcript holds the request and the response",
                   {"model_request", "model_response"} <= {kind for kind, _ in events},
                   [kind for kind, _ in events]),
        ]
    elif test_id == "chat.workspace_write_read":
        target = app.policy.write_root / "chat-debug-note.txt"
        result, models = _run(app, [
            _response("", _call(1, "write_file", path=str(target),
                                content="hello from the chat debugger")),
            _response("", _call(2, "read_file", path=str(target))),
            _response("The note says hello from the chat debugger.")],
            "Write a note and read it back.", debug=capture)
        results = [data for kind, data in events if kind == "tool_result"]
        checks = [
            _check("the file is in the workspace with what was written",
                   target.is_file() and target.read_text(encoding="utf-8")
                   == "hello from the chat debugger", target),
            _check("the model was given the file's contents",
                   "hello from the chat debugger" in str(result.get("tool_calls")),
                   result.get("tool_calls")),
            _check("both calls are marked successful",
                   len(results) >= 2 and all(one.get("ok") is True for one in results[:2]),
                   results),
        ]
    elif test_id == "chat.workspace_denial":
        # Outside the throwaway workspace - and still inside this run's own
        # folder, so even a refusal that failed would write nowhere else.
        outside = where / "outside.txt"
        result, models = _run(app, [
            _response("", _call(1, "write_file", path=str(outside), content="refused")),
            _response("I could not write outside the workspace.")],
            "Write outside the workspace.", debug=capture)
        refused = next((data for kind, data in events
                        if kind == "tool_result" and data.get("name") == "write_file"), {})
        said = str(result.get("tool_calls", []))
        checks = [
            _check("the runtime refused the path", not outside.exists()
                   and "security" in said.lower(), said),
            _check("the refusal is marked failed", refused.get("ok") is False, refused),
            _check("the chat carried on", bool(result.get("answer")), result.get("answer")),
        ]
    elif test_id == "chat.unknown_tool":
        result, models = _run(app, [
            _response("", _call(1, "teleport", destination="Mars")),
            _response("I cannot teleport, but I can help plan the trip.")],
            "Teleport me to Mars.", debug=capture)
        checks = [
            _check("the unknown tool was answered with an error",
                   "unknown tool teleport" in str(result.get("tool_calls")),
                   result.get("tool_calls")),
            _check("the model recovered with an answer",
                   "plan the trip" in result.get("answer", ""), result.get("answer")),
        ]
    elif test_id == "chat.delayed_task":
        when = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        result, models = _run(app, [
            _response("", _call(1, "schedule_task", title="Review the release notes",
                                description="Read through the release notes.",
                                rationale="The user asked for it in two days.",
                                scheduled_at=when, notify="silent")),
            _response("I scheduled the review.")],
            "In two days, have me review the release notes.", debug=capture)
        tasks = app.scheduler_store.list()
        checks = [
            _check("the scheduler kept the task",
                   any(task.title == "Review the release notes" for task in tasks),
                   [task.title for task in tasks]),
            _check("the turn is classed as delayed", result.get("classification") == "delayed",
                   result.get("classification")),
        ]
    elif test_id == "chat.model_unavailable":
        resolved = app.models.resolve("primary")
        refused = ModelAvailability(False, AvailabilityState.MODEL_UNAVAILABLE,
                                    reason="the scripted model is unavailable",
                                    authentication_valid=True,
                                    failure_class=FailureClass.USER_ACTION_REQUIRED)
        result, models = _run(app, [ModelUnavailable(resolved, refused)],
                              "Answer this question.", debug=capture)
        checks = [
            _check("the user is told the model is unavailable, and why",
                   "model is unavailable" in result.get("answer", "")
                   and "scripted model" in result.get("answer", ""), result.get("answer")),
            _check("the turn reports the failure", bool(result.get("error")),
                   result.get("error")),
        ]
    elif test_id == "chat.iteration_cap":
        repeated = [_response("", _call(n, "current_time")) for n in range(3)]
        result, models = _run(app, repeated, "Keep checking the time.",
                              max_iterations=2, debug=capture)
        checks = [
            _check("the model was asked no more than the limit",
                   len(models.requests) == 2, f"calls={len(models.requests)}"),
            _check("the limit is reported", "iteration limit" in (result.get("error") or ""),
                   result.get("error")),
        ]
    elif test_id == "chat.blank_response":
        fallback = "I could not produce an answer for this request."
        empty, _ = _run(app, [_response("")], "Give me an answer.", debug=capture)
        result, _ = _run(app, [_response("   \n\t")], "Give me a blank answer.",
                         debug=capture)
        checks = [
            _check("an empty answer is replaced", empty.get("answer") == fallback,
                   empty.get("answer")),
            _check("a whitespace answer is replaced", result.get("answer") == fallback,
                   result.get("answer")),
        ]
    elif test_id == "chat.history_unicode":
        first, _ = _run(app, [_response("Noted: the café ☕.")],
                        "First: remember the café ☕", debug=capture)
        conversation = first["conversation_id"]
        result, models = _run(app, [_response("You asked about the café ☕.")],
                              "What did I ask about?", conversation, debug=capture)
        sent = models.requests[0].messages
        checks = [
            _check("the earlier turns come first, in order, word for word",
                   sent == [{"role": "user", "content": "First: remember the café ☕"},
                            {"role": "assistant", "content": "Noted: the café ☕."},
                            {"role": "user", "content": "What did I ask about?"}], sent),
            _check("nothing is summarised in a short conversation",
                   "Earlier in this conversation" not in models.requests[0].system,
                   models.requests[0].system[-300:]),
            _check("the answer is kept in the conversation",
                   any(one.get("content") == result["answer"]
                       for one in app.conversations.messages(conversation, limit=None)),
                   app.conversations.messages(conversation, limit=None)),
        ]
    elif test_id == "chat.history_summary":
        from ..agent.core.agent_core import HISTORY_BUDGET_CHARS

        conversation = app.conversations.create()
        # Twenty turns, each long enough that the whole is well past what is
        # given word for word.
        size = HISTORY_BUDGET_CHARS // 12
        for n in range(20):
            app.conversations.add_message(conversation, "user",
                                          f"Question {n}: " + "q" * size)
            app.conversations.add_message(conversation, "assistant",
                                          f"Answer {n}: " + "a" * size)

        def answering(request: Any) -> ModelResponse:
            if request.task == "chat_history_summary":
                return _response("SUMMARY: twenty questions were asked and answered.")
            return _response("Here is the answer.")

        result, models = _run(app, [answering, answering], "And the next one?",
                              conversation, debug=capture)
        summarised = [one for one in models.requests if one.task == "chat_history_summary"]
        turn = [one for one in models.requests if one.task == "agent_instruction"]
        kept = app.conversations.summary(conversation)
        sent = turn[0].messages if turn else []
        again, models_again = _run(app, [answering], "And after that?", conversation,
                                   debug=capture)
        checks = [
            _check("the older part was summarised once",
                   len(summarised) == 1 and "Question 0:" in str(summarised[0].messages),
                   [one.task for one in models.requests]),
            _check("the model was given the summary", bool(turn)
                   and "SUMMARY: twenty questions" in turn[0].system,
                   turn[0].system[-400:] if turn else ""),
            _check("the latest turns are word for word and the oldest are not",
                   sent and sent[-1]["content"] == "And the next one?"
                   and "Answer 19:" in str(sent) and "Question 0:" not in str(sent),
                   [one["content"][:20] for one in sent]),
            _check("the summary is kept with the conversation",
                   kept is not None and kept["summary"].startswith("SUMMARY"), kept),
            _check("the next turn uses the kept summary",
                   not any(one.task == "chat_history_summary"
                           for one in models_again.requests)
                   and "SUMMARY: twenty questions" in models_again.requests[0].system,
                   [one.task for one in models_again.requests]),
        ]
        result = again
    else:  # chat.new_conversation
        _run(app, [_response("Noted: Aurora.")], "My project is Aurora.", debug=capture)
        result, models = _run(app, [_response("Which project?")], "What is my project?",
                              debug=capture)
        sent = models.requests[0].messages
        checks = [
            _check("only the new message is sent",
                   sent == [{"role": "user", "content": "What is my project?"}], sent),
            _check("nothing of the other conversation is in the prompt",
                   "Aurora" not in json.dumps(sent) + models.requests[0].system,
                   models.requests[0].system[-300:]),
        ]

    debug("verification", {"test_id": test_id, "checks": checks})
    passed = all(check["passed"] for check in checks)
    return {"result": result, "checks": checks,
            "status": "passed" if passed else "failed",
            "summary": f"{sum(c['passed'] for c in checks)}/{len(checks)} checks passed"}


def scratch_folder(app: Any) -> Path:
    """A new folder for one scenario, under the application's own data root."""
    paths = getattr(app, "paths", None)
    if paths is None:
        raise RuntimeError("this application has no data root to run a scenario in")
    return Path(paths.data_dir) / SCRATCH_DIR / new_id("run")
