"""How the agent's own tools travel through a local client's transcript (76.1).

Claude Code and Codex are run as programs that take a prompt and print an
answer. Neither carries the API's tool protocol, so the agent's tools are
offered in the prompt, a call comes back as a JSON object in the reply, and
the result goes back as text on the next turn. Each provider says this in its
own words (its TOOL_PROTOCOL): what is shared here is only the mechanics - how
the tools are listed, how a call is read out of a reply, and how a result and
a call are written back into the transcript - because a fix to any of those
belongs to both clients at once.

None of this runs a tool. A call read here is handed to the agent, which runs
it through its own registry and sandbox, exactly as a call from the API is.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, List, Optional

from ..base import ModelRequest, ToolCall, ToolSpec

log = logging.getLogger(__name__)

#: How many JSON objects to look at before deciding a reply holds no call.
#:
#: A reply is prose and at most a few objects. Scanning every brace in a
#: long answer is quadratic on the one input that is already the largest.
CALLS_LOOKED_FOR = 8


def describe_tools(specs: List[ToolSpec]) -> str:
    """Each tool by name, what it is for, and the arguments it takes.

    It used to be the name and the description and nothing else, and a model
    asked to call ``schedule_task`` from that had to guess what its arguments
    were called - "when" or "time" or "scheduled_at" - and which of them it
    could leave out. A guess that misses is a call the tool refuses, after a
    whole round trip through the client. The API hands the model the schema;
    so does this, whole, as the JSON the tool will be checked against.
    """
    lines = []
    for spec in specs:
        lines.append(f"- {spec.name}: {spec.description}")
        schema = spec.input_schema or {"type": "object", "properties": {}}
        lines.append("  arguments (JSON schema): "
                     + json.dumps(schema, ensure_ascii=False, sort_keys=True))
    return "\n".join(lines)


def tool_results(blocks: List[Any]) -> Optional[str]:
    """The blocks rendered as tool output, or None if that is not what they are.

    What a tool returned is not the user speaking. The API carries a tool
    result on a user turn, which is its convention and invisible there;
    rendered into a transcript as "User: [{...}]" it reads as the user pasting
    a block of JSON, and an answer built on it gets disowned - "these are from
    my own records, not from checking just now".
    """
    if not blocks or not all(isinstance(block, dict)
                             and block.get("type") == "tool_result"
                             for block in blocks):
        return None
    from ..vision import text_of
    return "\n\n".join(f"Tool result: {text_of(block.get('content'))}" for block in blocks)


def leading_tool_call(text: str, request: ModelRequest, prefix: str) -> List[ToolCall]:
    """The tool call in a reply, when there is one.

    **Read from the front, not whole.** It used to require the reply to be
    exactly one JSON object and nothing else, and to treat anything longer
    as an answer. What that met in practice was a reply that opened with a
    perfectly good call and then carried on - the model continuing the
    transcript by writing the tool's reply for it:

        {"tool": "list_scheduled_tasks", "arguments": {...}}

        Tool result: [ {"id": "task_1f0c...", "title": ...}, ... ]

    Read strictly, that is "not a call", so it became the answer: the user
    was shown a raw JSON call followed by invented results. So whatever
    *follows* a call is discarded - the agent runs the tool and supplies the
    real result. Nothing a model writes after its call can be a tool result,
    because tool results do not come from the model.

    **What comes before it is not fatal either.** A model that says why it
    is asking writes a sentence, then a good call. The discriminator was
    never the position: it is whether the object names a tool this request
    offered - an answer is a JSON object too, and it names no tool. So the
    first object that does is the call, and the prose in front of it is the
    reason for it.
    """
    if not request.tools:
        return []
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:]
        stripped = stripped.strip()
    known = {spec.name for spec in request.tools}
    decoder = json.JSONDecoder()
    at = stripped.find("{")
    # Bounded: a reply is prose and at most a few objects, and a scan that
    # tries every brace in a long answer is quadratic on the one input that
    # is already the largest.
    for _ in range(CALLS_LOOKED_FOR):
        if at < 0:
            return []
        try:
            parsed, ate = decoder.raw_decode(stripped[at:])
        except ValueError:
            at = stripped.find("{", at + 1)
            continue
        name = parsed.get("tool") if isinstance(parsed, dict) else None
        if not isinstance(name, str) or name not in known:
            # A JSON object that names no tool this request offered is not a
            # call - most often it is the answer itself.
            at = stripped.find("{", at + ate)
            continue
        said = stripped[:at].strip()
        if said:
            log.info("the model gave a reason before its tool call; "
                     "%d characters", len(said))
        trailing = stripped[at + ate:].strip()
        if trailing:
            log.info("the model continued past its tool call; %d characters "
                     "discarded, including anything it wrote as a result",
                     len(trailing))
        arguments = parsed.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        return [ToolCall(id=f"{prefix}_{int(time.time() * 1000)}",
                         name=name, arguments=arguments)]
    return []


def as_written(text: str, tool_calls: List[ToolCall]) -> str:
    """The assistant's turn as it should be remembered.

    A call is remembered as the call and nothing else. Left as the whole
    reply, a result the model invented after its call would be read back to
    it next turn as its own words, immediately above the real result -
    teaching it, turn by turn, that writing results is what it does here.
    """
    if not tool_calls:
        return text
    call = tool_calls[0]
    return json.dumps({"tool": call.name, "arguments": call.arguments},
                      ensure_ascii=False)
