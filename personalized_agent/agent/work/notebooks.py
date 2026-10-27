"""JUPYTER NOTEBOOKS, cell by cell (the user, 2026-10-25: as Claude Code's
NotebookEdit). A notebook is JSON - its cells, each with its source and,
for code, what it printed - and edited as text it is easy to break. So it
is read as its cells, and changed one cell at a time: replaced, inserted or
deleted, everything else in the file - its metadata, the other cells - as
it was. A code cell changed loses its outputs, which no longer belong to it.
A notebook is RUN by the work's command (jupyter nbconvert --execute), in
the sandbox.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional, Tuple

#: What one output is shown with at most, and an image as.
MOST_OUTPUT = 4000
MODES = ("replace", "insert", "delete")
CELL_TYPES = ("code", "markdown", "raw")


def _source(cell: Dict[str, Any]) -> str:
    source = cell.get("source", "")
    return "".join(source) if isinstance(source, list) else str(source or "")


def _lines(text: str) -> List[str]:
    """A cell's source as nbformat keeps it: lines with their ends."""
    parts = text.splitlines(keepends=True)
    return parts


def _output_text(output: Dict[str, Any]) -> str:
    kind = output.get("output_type")
    if kind == "stream":
        return _source({"source": output.get("text", "")})
    if kind == "error":
        return f"{output.get('ename', '')}: {output.get('evalue', '')}"
    data = output.get("data") or {}
    if "text/plain" in data:
        text = _source({"source": data["text/plain"]})
    else:
        text = ""
    pictures = [one for one in data if one.startswith("image/")]
    if pictures:
        text += ("\n" if text else "") + f"[{', '.join(pictures)}]"
    return text


def shown(raw: str) -> str:
    """A notebook as its cells: each numbered, with its id and type, its
    source, and what it printed."""
    data = json.loads(raw)
    cells = data.get("cells") if isinstance(data, dict) else None
    if not isinstance(cells, list):
        raise ValueError("not a notebook: it has no cells")
    out: List[str] = [f"A NOTEBOOK of {len(cells)} cells "
                      f"(nbformat {data.get('nbformat', '?')}.{data.get('nbformat_minor', '?')})"]
    for number, cell in enumerate(cells):
        kind = cell.get("cell_type", "?")
        ident = cell.get("id")
        head = f"--- cell {number}{f' [{ident}]' if ident else ''} ({kind})"
        if kind == "code" and cell.get("execution_count") is not None:
            head += f" [ran {cell['execution_count']}]"
        out.append(head + " ---")
        out.append(_source(cell))
        printed = [_output_text(one) for one in cell.get("outputs") or [] if isinstance(one, dict)]
        printed = [one for one in printed if one]
        if printed:
            text = "\n".join(printed)
            out.append("--- its output ---")
            out.append(text[:MOST_OUTPUT] + ("\n[cut]" if len(text) > MOST_OUTPUT else ""))
    return "\n".join(out)


def _index(cells: List[Dict[str, Any]], cell: Any, mode: str) -> int:
    """The position `cell` names - a number, or a cell's id."""
    if isinstance(cell, int) or (isinstance(cell, str) and cell.strip().lstrip("-").isdigit()):
        index = int(cell)
        most = len(cells) if mode == "insert" else len(cells) - 1
        if index < 0 or index > most:
            raise ValueError(f"there is no cell {index}: the notebook has {len(cells)}")
        return index
    for number, one in enumerate(cells):
        if one.get("id") == cell:
            return number
    raise ValueError(f"no cell has the id {cell!r}")


def edit(raw: str, cell: Any, mode: str = "replace", source: str = "",
         cell_type: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
    """The notebook with one cell replaced, inserted (before the one named;
    at the end with the number of cells) or deleted - and what was done."""
    if mode not in MODES:
        raise ValueError(f"mode is one of {', '.join(MODES)}")
    data = json.loads(raw)
    cells = data.get("cells") if isinstance(data, dict) else None
    if not isinstance(cells, list):
        raise ValueError("not a notebook: it has no cells")
    if cell_type is not None and cell_type not in CELL_TYPES:
        raise ValueError(f"cell_type is one of {', '.join(CELL_TYPES)}")
    index = _index(cells, cell, mode)
    with_ids = (data.get("nbformat", 4), data.get("nbformat_minor", 0)) >= (4, 5)
    if mode == "delete":
        gone = cells.pop(index)
        said = {"deleted": index, "id": gone.get("id"), "cells": len(cells)}
    elif mode == "insert":
        kind = cell_type or "code"
        new: Dict[str, Any] = {"cell_type": kind, "metadata": {}, "source": _lines(source)}
        if with_ids:
            new["id"] = uuid.uuid4().hex[:8]
        if kind == "code":
            new.update(execution_count=None, outputs=[])
        cells.insert(index, new)
        said = {"inserted": index, "id": new.get("id"), "cells": len(cells)}
    else:
        one = cells[index]
        kind = cell_type or one.get("cell_type", "code")
        one["source"] = _lines(source)
        if kind != one.get("cell_type"):
            one["cell_type"] = kind
        if kind == "code":
            # What it printed belonged to the code it had.
            one["outputs"] = []
            one["execution_count"] = None
        else:
            one.pop("outputs", None)
            one.pop("execution_count", None)
        said = {"replaced": index, "id": one.get("id"), "cells": len(cells)}
    return json.dumps(data, ensure_ascii=False, indent=1) + "\n", said
