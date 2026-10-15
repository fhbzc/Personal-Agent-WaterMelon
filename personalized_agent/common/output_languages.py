"""WHICH LANGUAGE EACH MODEL OUTPUT IS WRITTEN IN (the Language debugger) -
read off the source, and off what was sent.

A model call says its language in one way only: `language=in_display(...)
+ in_english(...)` (common/languages.py) - at the call, at a helper's
default, in a module's constant, or in a step's definition. So the source
says it all, and this reads it there each time it is asked, with Python's
own parser: a language added, changed or taken away is shown the next time
the page is opened, with nothing to keep up to date by hand.

  - every place a language is said: what is in English (``internal``), what
    in the display language (``display``), or both (``mixed``), and where;
  - every model call that says none (``unspecified``) - a `ModelRequest`
    with no `language=`, or a call to a helper that passes one through
    without giving it;
  - a prompt that still says a language in its own words, which the rest
    cannot see;
  - and what was SENT: each call records the language it carried
    (model_calls.language), so the page sets the code beside the calls.
"""

from __future__ import annotations

import ast
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

INTERNAL, DISPLAY, MIXED, UNSPECIFIED = "internal", "display", "mixed", "unspecified"
MAKERS = {"in_display": DISPLAY, "in_english": INTERNAL}
_SKIP = {"languages.py", "output_languages.py"}

#: Words that say a language inside a prompt, rather than through language=.
#: Commands only - "IN ENGLISH", "write it in English", a placeholder for a
#: language - not a sentence that describes how things are kept.
_INLINE = re.compile(r"IN ENGLISH|\{language\}|\{display_language\}|"
                     r"(?i:\bwrite\b[^.\n]{0,40}\bin english\b)|THE USER'S DISPLAY LANGUAGE")


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def _text(node: ast.AST) -> Optional[str]:
    """A string the source spells out: a constant, constants joined with +,
    or an f-string's fixed parts."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _text(node.left), _text(node.right)
        return None if left is None or right is None else left + right
    if isinstance(node, ast.JoinedStr):
        return "".join(part.value if isinstance(part, ast.Constant) else "{...}"
                       for part in node.values)
    return None


def _rules(node: ast.AST) -> Optional[List[Dict[str, str]]]:
    """The rules a language expression says: in_display(...) + in_english(...)."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _rules(node.left), _rules(node.right)
        return None if left is None or right is None else left + right
    if isinstance(node, ast.Call):
        name = node.func.id if isinstance(node.func, ast.Name) else \
            node.func.attr if isinstance(node.func, ast.Attribute) else ""
        if name in MAKERS:
            what = _text(node.args[0]) if node.args else None
            if what is None and name == "in_display":
                what = "everything the user will read"
            return [{"rule": MAKERS[name], "what": what or "(said in code)"}]
    return None


def _rule_of(rules: List[Dict[str, str]]) -> str:
    kinds = {one["rule"] for one in rules}
    return MIXED if len(kinds) > 1 else (kinds.pop() if kinds else UNSPECIFIED)


def _callee(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _task_of(call: ast.Call, constants: Dict[str, str]) -> str:
    for keyword in call.keywords:
        if keyword.arg != "task":
            continue
        value = keyword.value
        text = _text(value)
        if text is not None:
            return text.split("{", 1)[0] + ("*" if "{" in text else "")
        if isinstance(value, ast.Name):
            return constants.get(value.id, value.id)
        if isinstance(value, ast.Call) and _callee(value) == "model_task":
            part = _text(value.args[1]) if len(value.args) > 1 else None
            return f"experience_{part}" if part else "experience"
    return ""


class _Module:
    """One file, read: its constants, its functions, its calls - each node
    with its parent."""

    def __init__(self, path: Path, rel: str) -> None:
        self.path, self.rel = path, rel
        self.text = path.read_text(encoding="utf-8")
        self.tree = ast.parse(self.text)
        self.parent: Dict[ast.AST, ast.AST] = {}
        for node in ast.walk(self.tree):
            for child in ast.iter_child_nodes(node):
                self.parent[child] = node
        self.strings = {}       # NAME = "a string": a task tag's constant
        self.languages = {}     # NAME = in_display(...): a language's constant
        for node in self.tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and \
                    isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                text = _text(node.value)
                if text is not None:
                    self.strings[name] = text
                rules = _rules(node.value)
                if rules is not None:
                    self.languages[name] = rules

    def where(self, node: ast.AST) -> str:
        return f"{self.rel}:{getattr(node, 'lineno', 0)}"

    def up(self, node: ast.AST, kinds: Tuple[type, ...]) -> Optional[ast.AST]:
        while node in self.parent:
            node = self.parent[node]
            if isinstance(node, kinds):
                return node
        return None

    def function(self, node: ast.AST) -> str:
        found = self.up(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        return found.name if found is not None else "(module)"


def _modules(root: Path) -> List[_Module]:
    out = []
    for path in sorted(root.rglob("*.py")):
        if path.name in _SKIP or "__pycache__" in path.parts:
            continue
        try:
            out.append(_Module(path, path.relative_to(root).as_posix()))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
    return out


def read_code(root: Optional[Path] = None) -> Dict[str, Any]:
    """Every language the source says, every model call that says none, and
    every prompt that still says one in its own words."""
    root = root or _root()
    said: List[Dict[str, Any]] = []
    unsaid: List[Dict[str, Any]] = []
    inline: List[Dict[str, Any]] = []
    for module in _modules(root):
        # Helpers that pass a language through to the call they make:
        # name -> (whether it has one of its own when not given, its task).
        through: Dict[str, Tuple[bool, str]] = {}
        for node in ast.walk(module.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            args = node.args.args + node.args.kwonlyargs
            if "language" not in [arg.arg for arg in args]:
                continue
            defaults = dict(zip([a.arg for a in node.args.args][-len(node.args.defaults):],
                                node.args.defaults)) if node.args.defaults else {}
            defaults.update({a.arg: d for a, d in zip(node.args.kwonlyargs,
                                                      node.args.kw_defaults) if d is not None})
            default = defaults.get("language")
            has_own = default is not None and not (isinstance(default, ast.Constant)
                                                  and default.value is None)
            task = ""
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call) and _callee(inner) == "ModelRequest":
                    task = _task_of(inner, module.strings)
            through[node.name] = (has_own, task)
        # A helper that passes it to another helper: the other's task.
        for _ in range(3):
            for node in ast.walk(module.tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or                         node.name not in through or through[node.name][1]:
                    continue
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Call) and _callee(inner) in through and                             through[_callee(inner)][1]:
                        through[node.name] = (through[node.name][0],
                                              through[_callee(inner)][1])
                        break
        for node in ast.walk(module.tree):
            # A language said: its outermost expression only.
            rules = _rules(node) if isinstance(node, (ast.BinOp, ast.Call)) else None
            if rules is not None:
                above = module.parent.get(node)
                if isinstance(above, ast.BinOp) and _rules(above) is not None:
                    continue
                said.append(_place(module, node, rules, through))
                continue
            if isinstance(node, ast.Call) and _callee(node) in ("ModelRequest",) + tuple(through):
                keywords = {keyword.arg: keyword.value for keyword in node.keywords}
                name = _callee(node)
                if name == "ModelRequest":
                    if "language" not in keywords:
                        unsaid.append({"task": _task_of(node, module.strings) or "general",
                                       "where": module.where(node),
                                       "in": module.function(node), "through": "ModelRequest",
                                       "rule": UNSPECIFIED, "rules": []})
                    continue
                has_own, task = through[name]
                if "language" not in keywords and not has_own and \
                        module.function(node) != name:
                    unsaid.append({"task": task or "general", "where": module.where(node),
                                   "in": module.function(node), "through": name,
                                   "rule": UNSPECIFIED, "rules": []})
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and \
                    _INLINE.search(node.value):
                above = module.parent.get(node)
                if isinstance(above, ast.Expr):
                    continue                     # a docstring, not a prompt
                match = _INLINE.search(node.value)
                start = max(0, match.start() - 50)
                inline.append({"where": f"{module.rel}:{node.lineno}",
                               "in": module.function(node),
                               "words": " ".join(node.value[start:match.end() + 60].split())})
    return {"said": said, "unspecified": unsaid, "inline": inline}


def _place(module: _Module, node: ast.AST, rules: List[Dict[str, str]],
           through: Dict[str, Tuple[bool, str]]) -> Dict[str, Any]:
    """Where a language is said, and which calls it is for."""
    row = {"where": module.where(node), "in": module.function(node), "rules": rules,
           "rule": _rule_of(rules), "task": "", "through": "", "via": "call"}
    parent = module.parent.get(node)
    # `language=shape.get("language") or in_display()`: the fallback's call.
    while isinstance(parent, (ast.BoolOp, ast.IfExp)):
        node, parent = parent, module.parent.get(parent)
    if isinstance(parent, ast.keyword) and parent.arg == "language":
        call = module.parent.get(parent)
        if isinstance(call, ast.Call):
            row["through"] = _callee(call)
            row["task"] = _task_of(call, module.strings) or \
                through.get(row["through"], (False, ""))[1]
        return row
    if isinstance(parent, ast.Assign):
        target = parent.targets[0]
        row["via"] = "constant " + (target.id if isinstance(target, ast.Name) else "")
        row["task"] = _users_of(module, getattr(target, "id", ""), through)
        return row
    if isinstance(parent, ast.Dict):
        # A step's definition: {"step_people": {..., "language": ...}}.
        outer = module.parent.get(parent)
        if isinstance(outer, ast.Dict):
            for key, value in zip(outer.keys, outer.values):
                if value is parent and isinstance(key, ast.Constant):
                    row["via"] = f"the definition of {key.value}"
                    row["task"] = f"heartbeat:{key.value}" if str(key.value).startswith(
                        "step_") else str(key.value)
        return row
    if isinstance(parent, (ast.arguments,)):
        function = module.parent.get(parent)
        row["via"] = f"the default of {getattr(function, 'name', '')}()"
        row["task"] = through.get(getattr(function, "name", ""), (False, ""))[1]
    return row


def _users_of(module: _Module, name: str, through: Dict[str, Tuple[bool, str]]) -> str:
    """The task of the calls a language constant is given to."""
    for node in ast.walk(module.tree):
        if isinstance(node, ast.keyword) and node.arg == "language" and \
                isinstance(node.value, ast.Name) and node.value.id == name:
            call = module.parent.get(node)
            if isinstance(call, ast.Call):
                return _task_of(call, module.strings) or \
                    through.get(_callee(call), (False, ""))[1]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for default in node.args.defaults + [d for d in node.args.kw_defaults if d]:
                if isinstance(default, ast.Name) and default.id == name:
                    return through.get(node.name, (False, ""))[1]
    return ""


def read_sent(db: Any, days: int = 7) -> List[Dict[str, Any]]:
    """WHAT WAS SENT: each task's calls in the last `days`, by the language
    each carried - "" when it carried none."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        rows = db.query("SELECT task, COALESCE(language, '') AS language, COUNT(*) AS calls, "
                        "MAX(created_at) AS last FROM model_calls WHERE created_at >= ? "
                        "GROUP BY task, COALESCE(language, '') ORDER BY task", (since,))
    except Exception:
        return []
    out = []
    for row in rows:
        try:
            rules = json.loads(row["language"]) if row["language"] else []
        except ValueError:
            rules = []
        out.append({"task": row["task"], "rules": rules,
                    "rule": _rule_of(rules) if rules else UNSPECIFIED,
                    "calls": int(row["calls"]), "last": row["last"]})
    return out


def described(display: str = "", db: Any = None, days: int = 7) -> Dict[str, Any]:
    """The Language debugger's page."""
    code = read_code()
    groups = {rule: [one for one in code["said"] if one["rule"] == rule]
              for rule in (INTERNAL, DISPLAY, MIXED)}
    groups[UNSPECIFIED] = code["unspecified"]
    sent = read_sent(db, days) if db is not None else []
    return {"display_language": display, "groups": groups, "inline": code["inline"],
            "sent": sent, "days": days,
            "counts": {rule: len(rows) for rule, rows in groups.items()}}


__all__ = ["described", "read_code", "read_sent", "INTERNAL", "DISPLAY", "MIXED",
           "UNSPECIFIED"]
