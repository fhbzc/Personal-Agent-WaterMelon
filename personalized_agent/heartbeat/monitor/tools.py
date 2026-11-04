"""THE MONITOR FROM CHAT (the user, 2026-11-03: "这个monitor 是一个模型自己可以编程
的，chat可以调用"): what the real-time input offers, the Activators there are, and
the model adding, editing and - only when it must - deleting them. Code holds
each to its shape and never touches a PROTECTED one (activators.py).

    monitor_signals   the signals and events, and their values now
    monitor_list      the Activators, with what they did lately
    monitor_add       a new one
    monitor_edit      one changed - the fields given
    monitor_delete    one taken away, only when it is really necessary
    monitor_evidence  an Activator's record and what is like it: looked at first
    monitor_quiet_add one rule of quiet, in place of editing many
"""

from __future__ import annotations

from typing import Any, Dict

from ...models.base import ToolSpec
from . import activators as act
from .decide import apply_program
from .evidence import Evidence
from .signals import Signals


def _schema(properties: Dict[str, Any], required: list) -> Dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required}


def add_tools(registry: Any, monitor: Any, retriever: Any = lambda: None) -> None:
    evidence = monitor.evidence = Evidence(monitor, retriever)

    def signals(_args: Dict[str, Any]) -> Any:
        return dict(Signals.describe(), now={k: v for k, v in (monitor.latest or {}).items()})

    def listed(_args: Dict[str, Any]) -> Any:
        said: Dict[str, Dict[str, int]] = {}
        for row in monitor.firings(200):
            one = said.setdefault(row["activator_id"], {"fired": 0, "acted": 0})
            one["fired"] += 1
            one["acted"] += int(row.get("acted") or 0)
        return {"activators": [dict(one, lately=said.get(one["id"], {}))
                               for one in monitor.store.for_the_model()]}

    def program(op: str):
        def run(args: Dict[str, Any]) -> Any:
            if op in ("edit", "delete"):
                held = evidence.refusal(op, str(args.get("id") or ""), args.get("evidence"),
                                        args.get("user_said"))
                if held:
                    return {"error": held}
            said = apply_program(monitor.store, dict(args, op=op), "chat")
            if "error" not in said:
                said["said"] = ("Done - it runs from now on, by code. Tell the user in a few "
                                "words what it watches for and what it does.")
            return said
        return run

    registry.add(ToolSpec(
        "monitor_signals",
        "The Monitor's real-time input: every signal and event an Activator can be written "
        "against, and the values now.", _schema({}, []), read_only=True), signals)
    registry.add(ToolSpec(
        "monitor_list", "The Monitor's Activators - what each watches for and does, whether "
        "it is on or protected, and what it did lately.", _schema({}, []), read_only=True), listed)
    activator = {"type": "object", "description": "the Activator: name, purpose, group, when, "
                 "then, cooldown_minutes, most_per_day"}
    cited = {"type": "array", "items": {"type": "string"},
             "description": "ids of the cases from monitor_evidence that bear on this"}
    ordered = {"type": "string",
               "description": "ONLY when the user themselves ordered this very change and named "
                              "what it covers (\"all the recommendation rules\"): their words, "
                              "quoted exactly as they wrote them. It stands for the evidence. "
                              "Never for a change you inferred from what they said."}
    registry.add(ToolSpec(
        "monitor_add",
        "Add an Activator to the Monitor - when the user asks to be told of something, or "
        "to be recommended something at some moment. Code runs it, not you.\n\n"
        + act.RULES_FOR_THE_MODEL + "\n\n" + act.__doc__.split("\n\n", 2)[1],
        _schema({"activator": activator, "why": {"type": "string"}}, ["activator", "why"])),
        program("add"))
    registry.add(ToolSpec(
        "monitor_edit",
        "Change an Activator - the fields given replace its own. A protected one cannot be "
        "changed.\n\n" + act.RULES_FOR_THE_MODEL,
        _schema({"id": {"type": "string"}, "changes": {"type": "object"},
                 "why": {"type": "string"}, "evidence": cited, "user_said": ordered},
                ["id", "changes", "why"])),
        program("edit"))
    registry.add(ToolSpec(
        "monitor_delete",
        "Delete an Activator - ONLY WHEN IT IS REALLY NECESSARY (prefer narrowing it with "
        "monitor_edit). A protected one cannot be deleted.",
        _schema({"id": {"type": "string"}, "why": {"type": "string"}, "evidence": cited,
                 "user_said": ordered}, ["id", "why"])), program("delete"))
    registry.add(ToolSpec(
        "monitor_evidence",
        "What must be looked at before an Activator is changed or deleted: what it did, how "
        "the user answered, and the lessons and past cases like the change. `about`: the "
        "change, in English.",
        _schema({"id": {"type": "string"}, "about": {"type": "string"}}, ["id", "about"]),
        read_only=True), lambda args: evidence.look(str(args.get("id") or ""),
                                                    str(args.get("about") or "")))

    def quiet(args: Dict[str, Any]) -> Any:
        try:
            ident = monitor.store.add_quiet(args.get("name"), args.get("when"), "chat",
                                            str(args.get("why") or ""))
        except ValueError as exc:
            return {"error": str(exc)}
        return {"id": ident, "said": "Done. Tell the user in a few words when it keeps quiet."}

    def unquiet(args: Dict[str, Any]) -> Any:
        return {"removed": monitor.store.remove_quiet(str(args.get("id") or ""), "chat",
                                                      str(args.get("why") or ""))}

    registry.add(ToolSpec(
        "monitor_quiet_add",
        "A rule of quiet: while `when` holds, nothing the Monitor would say or recommend by "
        "itself is said - every Activator of those, by this one rule. ONLY when the user asks "
        "to be left alone at some moment. `when` is a condition as an Activator's "
        "(monitor_signals).",
        _schema({"name": {"type": "string"}, "when": {"type": "object"},
                 "why": {"type": "string"}}, ["name", "when", "why"])), quiet)
    registry.add(ToolSpec(
        "monitor_quiet_remove", "A rule of quiet taken away, when the user asks.",
        _schema({"id": {"type": "string"}, "why": {"type": "string"}}, ["id", "why"])), unquiet)


__all__ = ["add_tools"]
