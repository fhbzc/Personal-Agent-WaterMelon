"""THE MONITOR FROM CHAT (the user, 2026-11-03: "这个monitor 是一个模型自己可以编程
的，chat可以调用"): what the real-time input offers, the Activators there are, and
the model adding, editing and - only when it must - deleting them. Code holds
each to its shape and never touches a PROTECTED one (activators.py).

THE SAME TOOLS FOR THE CHAT AND FOR DELEGATED WORK (the user, 2026-11-14: "不是protected的话agent是允许改
monitor的, chat/delegate什么的都要有改monitor的工具"): `tools_for(monitor, by)` - each change kept as whose it was.

    monitor_signals   the signals and events, and their values now
    monitor_list      the Activators, with what they did lately
    monitor_add       a new one
    monitor_edit      one changed - the fields given
    monitor_delete    one taken away, only when it is really necessary
    monitor_switch    one switched on or off
    monitor_evidence  an Activator's record and what is like it: looked at first
    monitor_quiet_add one rule of quiet, in place of editing many
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Tuple

from ...models.base import ToolSpec
from . import activators as act
from .decide import apply_program
from .evidence import Evidence
from .signals import Signals


def _schema(properties: Dict[str, Any], required: list) -> Dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required}


def add_tools(registry: Any, monitor: Any, retriever: Any = lambda: None) -> None:
    """The chat's."""
    monitor.evidence = Evidence(monitor, retriever)
    for spec, handler in tools_for(monitor, "chat", retriever):
        registry.add(spec, handler)


class _Into:
    def __init__(self) -> None:
        self.made: List[Tuple[ToolSpec, Callable[[Dict[str, Any]], Any]]] = []

    def add(self, spec: ToolSpec, handler: Callable[[Dict[str, Any]], Any]) -> None:
        self.made.append((spec, handler))


def tools_for(monitor: Any, by: str = "chat", retriever: Any = lambda: None
              ) -> List[Tuple[ToolSpec, Callable[[Dict[str, Any]], Any]]]:
    """The Monitor's tools, each change kept as `by`'s ("chat", "work")."""
    registry = _Into()
    evidence = getattr(monitor, "evidence", None)
    if evidence is None:
        evidence = monitor.evidence = Evidence(monitor, retriever)
    done_said = ("Done - it runs from now on, by code. Tell the user in a few words what it watches "
                 "for and what it does." if by == "chat" else
                 "Done - it runs from now on, by code. Say in what you hand over what you changed "
                 "in the Monitor, and why.")

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
            said = apply_program(monitor.store, dict(args, op=op), by)
            if "error" not in said:
                said["said"] = done_said
            return said
        return run

    registry.add(ToolSpec(
        "monitor_signals",
        "The Monitor's real-time input: every signal and event an Activator can be written "
        "against, and the values now.", _schema({}, []), read_only=True), signals)
    registry.add(ToolSpec(
        "monitor_list", "The Monitor's Activators - what each watches for and does, whether "
        "it is on or protected, and what it did lately. A PROTECTED one is the user's: it cannot be "
        "changed, switched or deleted.", _schema({}, []), read_only=True), listed)
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
        "monitor_switch",
        "Switch an Activator on or off - when the user asks, or when what it says is no longer "
        "wanted. A protected one cannot be switched.",
        _schema({"id": {"type": "string"}, "on": {"type": "boolean"}, "why": {"type": "string"}},
                ["id", "on", "why"])), program("switch"))
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
            ident = monitor.store.add_quiet(args.get("name"), args.get("when"), by,
                                            str(args.get("why") or ""))
        except ValueError as exc:
            return {"error": str(exc)}
        return {"id": ident, "said": "Done. Tell the user in a few words when it keeps quiet."}

    def unquiet(args: Dict[str, Any]) -> Any:
        try:
            return {"removed": monitor.store.remove_quiet(str(args.get("id") or ""), by,
                                                          str(args.get("why") or ""))}
        except ValueError as exc:
            return {"error": str(exc)}

    # A SILENCE IS A MONITOR TOO (the user, 2026-11-14): changed and switched as an Activator is - never a
    # protected one.
    def quiet_edit(args: Dict[str, Any]) -> Any:
        try:
            said = monitor.store.edit_quiet(str(args.get("id") or ""),
                                            {key: args[key] for key in ("name", "when") if args.get(key)},
                                            by, str(args.get("why") or ""))
        except ValueError as exc:
            return {"error": str(exc)}
        return {"quiet": said, "said": done_said}

    def quiet_switch(args: Dict[str, Any]) -> Any:
        if not isinstance(args.get("on"), bool):
            return {"error": "on is true or false"}
        try:
            said = monitor.store.switch_quiet(str(args.get("id") or ""), args["on"], by,
                                              str(args.get("why") or ""))
        except ValueError as exc:
            return {"error": str(exc)}
        return {"quiet": said, "said": done_said}

    def quiet_listed(_args: Dict[str, Any]) -> Any:
        return {"quiet": [{key: one.get(key) for key in ("id", "name", "when", "enabled", "protected",
                                                         "builtin")}
                          for one in monitor.store.quiet_rules()]}

    registry.add(ToolSpec(
        "monitor_quiet_add",
        "A blocker: while `when` holds, no reminder of any kind is shown. What the Monitor would "
        "say or recommend by itself is not said; any other reminder waits until it is over. "
        "ONLY when the user asks "
        "to be left alone at some moment. `when` is a condition as an Activator's "
        "(monitor_signals).",
        _schema({"name": {"type": "string"}, "when": {"type": "object"},
                 "why": {"type": "string"}}, ["name", "when", "why"])), quiet)
    registry.add(ToolSpec(
        "monitor_quiet_remove", "A blocker taken away, when the user asks. A protected one cannot "
        "be removed.",
        _schema({"id": {"type": "string"}, "why": {"type": "string"}}, ["id", "why"])), unquiet)
    registry.add(ToolSpec(
        "monitor_quiet_list", "The Monitor's blockers (rules of quiet): what each holds for, whether it "
        "is on, and whether it is protected - a protected one is the user's: it cannot be changed, "
        "switched or removed.", _schema({}, []), read_only=True), quiet_listed)
    registry.add(ToolSpec(
        "monitor_quiet_edit", "A blocker changed - its `when` (a condition as an Activator's) or its "
        "name, the fields given. A protected one cannot be changed.",
        _schema({"id": {"type": "string"}, "name": {"type": "string"}, "when": {"type": "object"},
                 "why": {"type": "string"}}, ["id", "why"])), quiet_edit)
    registry.add(ToolSpec(
        "monitor_quiet_switch", "A blocker switched on or off - when the user asks. A protected one "
        "cannot be switched.",
        _schema({"id": {"type": "string"}, "on": {"type": "boolean"}, "why": {"type": "string"}},
                ["id", "on", "why"])), quiet_switch)
    return registry.made


__all__ = ["add_tools", "tools_for"]
