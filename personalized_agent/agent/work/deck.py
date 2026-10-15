"""The results of a run, as slides: what was produced, where each piece is
and what it is for, and one slide per key result with its key variables
explained - what each means, its value, and where it comes from.

The deck shows the RESULTS only. How the run went - its rounds, the
reviewer's findings, the decisions made for the user - is in the run's
report, not here.

A VERSION'S SLIDES are a presentation, not a page of text: where the whole
work stands at that version told in PICTURES - charts the builder drew of
the work's real numbers, diagrams of its structure and flow, the images the
work produced - laid out as 16:9 slides from a fixed set of layouts (see
`LAYOUTS` and `visual_spec`), with little text: short captions, a few
points. A version saved before they were drawn - or whose figures could not
be made - has text slides, which still render here, in the same frame.

One renderer for every place a deck is seen: the `presentation.html`
written to the run's folder (figures linked as files), the work view in the
app (figures served by the app, or - a version's slides - carried in the
page itself as data, as the version keeps them). Self-contained: no script
or style is loaded from anywhere. One slide at a time, in a 16:9 frame
scaled to whatever box it is shown in; prev and next, the arrow keys, a
counter, and full screen.
"""

from __future__ import annotations

import base64
import html
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

#: What a figure is shown from, given its path.
ImageSource = Callable[[str], str]

IMAGE_TYPES = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")

#: How each image type is named in a data address.
MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".svg": "image/svg+xml", ".webp": "image/webp"}

#: THE LAYOUTS a version's slide may take - each a way of SHOWING, not of
#: listing: one is chosen per slide by what shows its point best, and only
#: "bullets" is text alone (and only when nothing visual fits).
LAYOUTS = ("title", "image", "image+text", "two-images", "comparison", "chart",
           "diagram", "timeline", "metrics", "bullets")

#: Names a model may give a layout, for the one it means.
LAYOUT_NAMES = {"cover": "title", "figure": "image", "picture": "image",
                "image-text": "image+text", "image_text": "image+text",
                "text+image": "image+text", "figure+text": "image+text",
                "two_images": "two-images", "twoimages": "two-images",
                "images": "two-images", "compare": "comparison",
                "versus": "comparison", "graph": "chart", "plot": "chart",
                "flow": "diagram", "flowchart": "diagram", "milestones": "timeline",
                "metric": "metrics", "numbers": "metrics", "kpi": "metrics",
                "stats": "metrics", "text": "bullets", "points": "bullets",
                "list": "bullets"}

#: The layouts a slide has only for the figure it shows: without it, it is
#: nothing.
FIGURE_LAYOUTS = ("image", "chart")
#: The most key terms a slide's notes explain.
MOST_TERMS = 8

#: How much a deck holds: slides, and of each kind of item on one.
MOST_SLIDES = 14
MOST = {"points": 4, "bullets": 5, "metrics": 4, "milestones": 8, "nodes": 14,
        "edges": 24, "columns": 3, "images": 2}

#: THE SLIDE, as drawn: 1280 by 720, scaled to the box it is shown in.
WIDTH, HEIGHT = 1280, 720


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _text(value: Any, most: int) -> str:
    """A short piece of text, cut to `most` characters."""
    said = " ".join(str(value if value is not None else "").split())
    return said if len(said) <= most else said[:most - 1].rstrip() + "…"


def file_uri(path: str) -> str:
    try:
        return Path(path).resolve().as_uri()
    except (OSError, ValueError):
        return ""


def data_uri(data: bytes, path: str) -> str:
    """An image carried in the page itself: its bytes, as a data address."""
    kind = MEDIA_TYPES.get(Path(str(path)).suffix.lower(), "application/octet-stream")
    return f"data:{kind};base64,{base64.b64encode(data).decode('ascii')}"


# -- the run's results deck, and text slides ----------------------------------------
def slides_of(deck: Dict[str, Any], image: ImageSource) -> List[str]:
    """Each slide's inner HTML, in order."""
    slides: List[str] = []
    unfinished = not deck.get("finished", True)
    banner = ('<p class="unfinished">Not finished - this is what exists so far. '
              'What is left is in the run\'s to-do list.</p>' if unfinished else "")
    slides.append(f"""<div class="cover">
      <h1>{_e(deck.get("title") or "Results")}</h1>
      {banner}
      <p class="lead">{_e(deck.get("summary") or "")}</p>
      {f'<p class="goal"><span>Goal</span> {_e(deck["goal"])}</p>' if deck.get("goal") else ""}
    </div>""")
    rows = "".join(
        f"""<tr><td class="name">{_e(one.get("name"))}</td>
          <td><code>{_e(one.get("location"))}</code></td>
          <td>{_e(one.get("what"))}</td></tr>"""
        for one in deck.get("deliverables") or [] if isinstance(one, dict))
    slides.append(f"""<h2>Key deliverables</h2>
      <table class="deliverables"><thead><tr><th>Deliverable</th><th>Where it is</th>
        <th>What it is, and what it is for</th></tr></thead><tbody>{rows}</tbody></table>""")
    slides += [_result_slide(one, image) for one in deck.get("results") or []
               if isinstance(one, dict)]
    return slides


def _result_slide(result: Dict[str, Any], image: ImageSource) -> str:
    """One key result: what it shows, its figure or table, and its key
    variables, each explained."""
    figure = str(result.get("figure") or "")
    shown = image(figure) if figure.lower().endswith(IMAGE_TYPES) else ""
    table = result.get("table") if isinstance(result.get("table"), dict) else {}
    table_html = ""
    if table.get("rows"):
        head = "".join(f"<th>{_e(c)}</th>" for c in table.get("columns") or [])
        body = "".join("<tr>" + "".join(f"<td>{_e(c)}</td>" for c in row) + "</tr>"
                       for row in table["rows"][:14] if isinstance(row, list))
        table_html = (f'<table class="data"><thead><tr>{head}</tr></thead>'
                      f"<tbody>{body}</tbody></table>")
    variables = "".join(
        f"""<li><div class="var"><code>{_e(v.get("name"))}</code>
            <span class="value">{_e(v.get("value"))}</span></div>
          <div>{_e(v.get("meaning"))}</div>
          <div class="source">from {_e(v.get("source"))}</div></li>"""
        for v in result.get("variables") or [] if isinstance(v, dict))
    visual = ""
    if shown:
        visual = f'<figure><img src="{_e(shown)}" alt="{_e(result.get("title"))}"></figure>'
    elif table_html:
        visual = table_html
    return f"""<h2>{_e(result.get("title"))}</h2>
      <p class="lead">{_e(result.get("shows"))}</p>
      <div class="result{' with-visual' if visual else ''}">
        {visual}
        {f'<div class="variables"><h3>Key variables</h3><ul>{variables}</ul></div>' if variables else ""}
      </div>"""


def _items(values: Any) -> List[str]:
    return [str(one).strip() for one in values or [] if str(one or "").strip()]


def progress_slides_of(deck: Dict[str, Any], image: ImageSource) -> List[str]:
    """A VERSION'S TEXT SLIDES - where the whole work stands at it: the
    cover (the version, the goal, the state in a few sentences), what is
    done with its evidence, what is new since the version before, the key
    results so far, and what remains - open problems and next steps. What a
    version had before its slides were drawn, and what it has when its
    figures could not be made (`fallback`, said on the cover)."""
    slides: List[str] = []
    version = str(deck.get("version") or "")
    since = str(deck.get("since") or "")
    where = " · ".join(_e(one) for one in (version, deck.get("label"),
                                            deck.get("version_kind")) if one)
    fallback = (f'<p class="fallback">Text slides - the figures could not be made'
                f'{": " + _e(deck["fallback"]) if isinstance(deck.get("fallback"), str) else ""}'
                f'</p>' if deck.get("fallback") else "")
    slides.append(f"""<div class="cover">
      <p class="where">{where}</p>
      <h1>{_e(deck.get("title") or "Where the work stands")}</h1>
      {fallback}
      <p class="lead">{_e(deck.get("summary") or "")}</p>
      {f'<p class="goal"><span>Goal</span> {_e(deck["goal"])}</p>' if deck.get("goal") else ""}
    </div>""")
    done = [one for one in deck.get("done") or [] if isinstance(one, dict)
            and str(one.get("what") or "").strip()]
    if done:
        rows = "".join(f"""<tr><td class="name">{_e(one.get("what"))}</td>
          <td>{_e(one.get("evidence"))}</td></tr>""" for one in done)
        slides.append(f"""<h2>Done so far</h2>
          <table class="deliverables"><thead><tr><th>What is done</th>
            <th>What shows it</th></tr></thead><tbody>{rows}</tbody></table>""")
    new = _items(deck.get("new"))
    if new:
        items = "".join(f"<li>{_e(one)}</li>" for one in new)
        slides.append(f"""<h2>New since {_e(since or "the start")}</h2>
          <ul class="points">{items}</ul>""")
    slides += [_result_slide(one, image) for one in deck.get("results") or []
               if isinstance(one, dict)]
    remaining, problems, next_ = (_items(deck.get("remaining")), _items(deck.get("problems")),
                                  _items(deck.get("next")))
    if remaining or problems or next_:
        parts = "".join(f"""<div><h3>{title}</h3><ul class="points">{"".join(
            f"<li>{_e(one)}</li>" for one in items)}</ul></div>"""
            for title, items in (("What remains", remaining), ("Open problems", problems),
                                 ("Next steps", next_)) if items)
        slides.append(f"""<h2>What remains</h2><div class="columns">{parts}</div>""")
    return slides


# -- a version's slides, in pictures ----------------------------------------------
def is_visual(deck: Any) -> bool:
    """Whether a deck is a version's drawn slides (a list of laid-out
    slides), not text slides or a results deck."""
    return isinstance(deck, dict) and isinstance(deck.get("slides"), list)


def _image_of(one: Dict[str, Any]) -> str:
    for name in ("image", "figure", "path", "src"):
        if isinstance(one.get(name), str) and one[name].strip():
            return one[name].strip()
    return ""


def _lines(values: Any, most: int, chars: int = 140) -> List[str]:
    if isinstance(values, str):
        values = [values]
    return [_text(one, chars) for one in values or []
            if isinstance(one, (str, int, float)) and str(one).strip()][:most]


def _notes_of(raw: Any) -> Dict[str, Any]:
    """A SLIDE'S NOTES - shown beside it, so it can be read without the
    one who made it: what it shows, its key terms each with what it means,
    how to read it (axes, colours, what to compare) and what to take from
    it. Given as those four, or as plain words (kept as what it shows).
    Cut short and bounded; {} for none."""
    if isinstance(raw, str):
        raw = {"shows": raw}
    if not isinstance(raw, dict):
        return {}
    terms = []
    for item in raw.get("terms") or raw.get("keywords") or []:
        if isinstance(item, dict):
            term = _text(item.get("term") or item.get("name") or item.get("word"), 60)
            means = _text(item.get("means") or item.get("meaning")
                          or item.get("explain"), 300)
        elif isinstance(item, str) and ":" in item:
            term, _, means = (part.strip() for part in item.partition(":"))
            term, means = _text(term, 60), _text(means, 300)
        else:
            continue
        if term and means:
            terms.append({"term": term, "means": means})
    notes = {"shows": _text(raw.get("shows") or raw.get("what"), 700),
             "terms": terms[:MOST_TERMS],
             "read": _text(raw.get("read") or raw.get("how_to_read"), 700),
             "takeaway": _text(raw.get("takeaway") or raw.get("take_away"), 400)}
    return notes if any(notes.values()) else {}


def _slide_spec(raw: Any) -> Optional[Dict[str, Any]]:
    """One slide as the model gave it, made one of the LAYOUTS - its text
    cut short, its lists bounded; None for what is none of them."""
    if not isinstance(raw, dict):
        return None
    layout = str(raw.get("layout") or raw.get("type") or "").strip().lower()
    layout = LAYOUT_NAMES.get(layout, layout)
    if layout not in LAYOUTS:
        layout = "bullets" if raw.get("points") or raw.get("bullets") else ""
    if not layout:
        return None
    one: Dict[str, Any] = {"layout": layout, "title": _text(raw.get("title"), 120)}
    notes = _notes_of(raw.get("notes") or raw.get("note") or raw.get("explain"))
    if notes:
        one["notes"] = notes
    caption = _text(raw.get("caption"), 240)
    if layout == "title":
        one["subtitle"] = _text(raw.get("subtitle") or raw.get("summary"), 320)
    elif layout in FIGURE_LAYOUTS:
        one.update(image=_image_of(raw), caption=caption)
    elif layout == "image+text":
        side = str(raw.get("side") or "left").lower()
        one.update(image=_image_of(raw), caption=caption,
                   points=_lines(raw.get("points") or raw.get("bullets"), MOST["points"]),
                   side="right" if side == "right" else "left")
    elif layout == "two-images":
        images = [{"image": _image_of(item), "caption": _text(item.get("caption"), 200)}
                  for item in raw.get("images") or [] if isinstance(item, dict)]
        one.update(images=images[:MOST["images"]], caption=caption)
    elif layout == "comparison":
        columns = [{"heading": _text(item.get("heading") or item.get("title"), 80),
                    "image": _image_of(item), "caption": _text(item.get("caption"), 200),
                    "points": _lines(item.get("points"), MOST["points"], 120)}
                   for item in raw.get("columns") or raw.get("images") or []
                   if isinstance(item, dict)]
        one.update(columns=columns[:MOST["columns"]], caption=caption)
    elif layout == "diagram":
        nodes, seen = [], set()
        for item in raw.get("nodes") or []:
            if isinstance(item, str):
                item = {"id": item, "label": item}
            if not isinstance(item, dict):
                continue
            key = str(item.get("id") or item.get("label") or "").strip()[:60]
            if key and key not in seen:
                seen.add(key)
                nodes.append({"id": key, "label": _text(item.get("label") or key, 60)})
        edges = [{"from": str(item.get("from") or "").strip()[:60],
                  "to": str(item.get("to") or "").strip()[:60],
                  "label": _text(item.get("label"), 30)}
                 for item in raw.get("edges") or [] if isinstance(item, dict)]
        nodes = nodes[:MOST["nodes"]]
        known = {node["id"] for node in nodes}
        edges = [edge for edge in edges if edge["from"] in known and edge["to"] in known]
        one.update(image=_image_of(raw), caption=caption, nodes=nodes,
                   edges=edges[:MOST["edges"]])
    elif layout == "timeline":
        milestones = [{"when": _text(item.get("when") or item.get("date"), 40),
                       "what": _text(item.get("what") or item.get("label"), 90),
                       "done": item.get("done") is not False}
                      for item in raw.get("milestones") or raw.get("items") or []
                      if isinstance(item, dict) and (item.get("what") or item.get("label"))]
        one.update(milestones=milestones[:MOST["milestones"]], caption=caption)
    elif layout == "metrics":
        metrics = [{"value": _text(item.get("value"), 16),
                    "label": _text(item.get("label") or item.get("name"), 60),
                    "note": _text(item.get("note"), 90)}
                   for item in raw.get("metrics") or raw.get("numbers") or []
                   if isinstance(item, dict) and str(item.get("value") or "").strip()]
        one.update(metrics=metrics[:MOST["metrics"]], caption=caption)
    else:
        one["points"] = _lines(raw.get("points") or raw.get("bullets"), MOST["bullets"])
    return one


def visual_spec(said: Dict[str, Any]) -> Dict[str, Any]:
    """The builder's SLIDE SPEC, made safe to draw: its title, and its
    slides each one of the LAYOUTS (`_slide_spec`), at most `MOST_SLIDES`."""
    slides = [one for one in (_slide_spec(raw) for raw in said.get("slides") or [])
              if one is not None]
    return {"title": _text(said.get("title"), 120),
            "subtitle": _text(said.get("subtitle") or said.get("summary"), 320),
            "slides": slides[:MOST_SLIDES]}


def place_images(spec: Dict[str, Any], keep: Callable[[str], str]) -> List[Dict[str, Any]]:
    """Every image a slide names, through `keep` - which says what it is
    kept as, or "" for one that may not be shown (not a file of the run's
    folder, not an image) - and each slide settled without what was left
    out: a figure slide with no figure is gone, a figure beside points
    keeps its points, two images with one left become one. The slides
    that remain."""
    kept: List[Dict[str, Any]] = []
    for one in spec.get("slides") or []:
        layout = one["layout"]
        if "image" in one:
            one["image"] = keep(one["image"]) if one["image"] else ""
        for item in one.get("images") or []:
            item["image"] = keep(item["image"]) if item["image"] else ""
        for item in one.get("columns") or []:
            item["image"] = keep(item["image"]) if item["image"] else ""
        if layout in FIGURE_LAYOUTS and not one["image"]:
            continue
        if layout == "image+text" and not one["image"]:
            if not one.get("points"):
                continue
            one = {"layout": "bullets", "title": one["title"], "points": one["points"]}
        if layout == "two-images":
            images = [item for item in one["images"] if item["image"]]
            if not images:
                continue
            if len(images) == 1:
                one = {"layout": "image", "title": one["title"], "image": images[0]["image"],
                       "caption": images[0]["caption"] or one.get("caption", "")}
            else:
                one["images"] = images
        if layout == "comparison" and len(one["columns"]) < 2:
            continue
        if layout == "diagram" and not one["image"] and len(one["nodes"]) < 2:
            continue
        if layout == "timeline" and not one["milestones"]:
            continue
        if layout == "metrics" and not one["metrics"]:
            continue
        if layout == "bullets" and not one.get("points"):
            continue
        kept.append(one)
    return kept


def shows_something(slides: List[Dict[str, Any]]) -> bool:
    """Whether any slide carries a visual: anything but a title or a list."""
    return any(one.get("layout") not in ("title", "bullets") for one in slides)


def _figure(src: str, alt: str, caption: str = "") -> str:
    if not src:
        return ""
    said = f'<figcaption>{_e(caption)}</figcaption>' if caption else ""
    return (f'<figure class="fig"><div class="fig-box"><img src="{_e(src)}" '
            f'alt="{_e(alt or caption)}"></div>{said}</figure>')


def _head(one: Dict[str, Any]) -> str:
    return f'<header class="s-head"><h2>{_e(one.get("title"))}</h2></header>' \
        if one.get("title") else ""


def _points(points: List[str], cls: str = "pts") -> str:
    return (f'<ul class="{cls}">' + "".join(f"<li>{_e(p)}</li>" for p in points) + "</ul>") \
        if points else ""


def _caption(text: str) -> str:
    return f'<p class="cap">{_e(text)}</p>' if text else ""


def _width_of(text: str, size: float) -> float:
    """About how wide a line of text is drawn: wide characters (CJK and
    the like) a whole em, the rest a little over half one."""
    return sum(size * (1.0 if ord(c) > 0x2E80 else 0.56) for c in text)


def _wrap(text: str, width: float, size: float, most: int = 3) -> List[str]:
    """A label in lines no wider than `width` - by words where it has them,
    by characters where it does not - at most `most`, the last cut short."""
    words = re.findall(r"\S+\s*", text) if " " in text else list(text)
    lines: List[str] = []
    line = ""
    for word in words:
        if line and _width_of(line + word, size) > width:
            lines.append(line.rstrip())
            line = word.lstrip()
        else:
            line += word
    if line.strip():
        lines.append(line.rstrip())
    if len(lines) > most:
        lines = lines[:most]
        lines[-1] = lines[-1][:max(1, len(lines[-1]) - 1)] + "…"
    return lines or [""]


def diagram_svg(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]], n: int = 0) -> str:
    """A diagram drawn from its nodes and edges: the nodes in columns by how
    far along the edges they come (left to right), each column centred; an
    edge to the next column is a curve from one box's right to the next
    one's left, an edge over columns an arc above them, an edge back (a
    loop: what feeds back) or within a column an arc beneath. Colours from
    the page's own theme, so it reads in light and dark."""
    W, H = 1136, 500
    ids = [one["id"] for one in nodes]
    # The edges that close a loop are found first, walking the edges from
    # each node in the order given: the columns follow the rest - a loop
    # would otherwise push its nodes ever further right.
    after: Dict[str, List[Tuple[int, str]]] = {key: [] for key in ids}
    for i, edge in enumerate(edges):
        after[edge["from"]].append((i, edge["to"]))
    back, state = set(), {}

    def walk(key: str) -> None:
        state[key] = "open"
        for i, target in after[key]:
            if state.get(target) == "open":
                back.add(i)
            elif target not in state:
                walk(target)
        state[key] = "done"

    for key in ids:
        if key not in state:
            walk(key)
    layer = {key: 0 for key in ids}
    for _ in range(len(ids)):
        changed = False
        for i, edge in enumerate(edges):
            a, b = edge["from"], edge["to"]
            if i not in back and layer[b] < layer[a] + 1:
                layer[b] = layer[a] + 1
                changed = True
        if not changed:
            break
    order = sorted(set(layer.values()))
    columns: List[List[str]] = [[key for key in ids if layer[key] == value] for value in order]
    count = len(columns)
    gap_x = 64 if count > 1 else 0
    box_w = min(250.0, (W - gap_x * (count - 1)) / max(1, count))
    tallest = max(len(one) for one in columns)
    gap_y = 26
    box_h = min(86.0, (H - 40 - gap_y * (tallest - 1)) / max(1, tallest))
    size = 19 if box_w >= 180 else 16
    left = (W - (count * box_w + (count - 1) * gap_x)) / 2
    at: Dict[str, Tuple[float, float]] = {}
    for c, keys in enumerate(columns):
        height = len(keys) * box_h + (len(keys) - 1) * gap_y
        top = (H - height) / 2
        for r, key in enumerate(keys):
            at[key] = (left + c * (box_w + gap_x), top + r * (box_h + gap_y))
    lowest = max(y for _x, y in at.values()) + box_h
    marker = f"dg-arrow-{n}"
    parts = [f'<svg class="dg" viewBox="0 0 {W} {H}" preserveAspectRatio="xMidYMid meet" '
             f'role="img" xmlns="http://www.w3.org/2000/svg"><defs><marker id="{marker}" '
             f'viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" '
             f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="dg-head"/>'
             f'</marker></defs>']
    labels = []
    for edge in edges:
        (x1, y1), (x2, y2) = at[edge["from"]], at[edge["to"]]
        step = layer[edge["to"]] - layer[edge["from"]]
        if step == 1:
            sx, sy, ex, ey = x1 + box_w, y1 + box_h / 2, x2 - 2, y2 + box_h / 2
            bend = max(30.0, (ex - sx) / 2)
            path = f"M{sx:.1f},{sy:.1f} C{sx + bend:.1f},{sy:.1f} {ex - bend:.1f},{ey:.1f} {ex:.1f},{ey:.1f}"
            mx, my = (sx + ex) / 2, (sy + ey) / 2
        elif step > 1:
            sx, sy = x1 + box_w / 2, y1
            ex, ey = x2 + box_w / 2, y2 - 2
            high = max(6.0, min(sy, ey) - 56)
            path = f"M{sx:.1f},{sy:.1f} C{sx:.1f},{high:.1f} {ex:.1f},{high:.1f} {ex:.1f},{ey:.1f}"
            mx, my = (sx + ex) / 2, high + 10
        elif step == 0:
            # Within a column: out of one box's right side, into the other's.
            sx, sy = x1 + box_w, y1 + box_h / 2
            ex, ey = x2 + box_w + 2, y2 + box_h / 2
            path = (f"M{sx:.1f},{sy:.1f} C{sx + 44:.1f},{sy:.1f} {ex + 44:.1f},{ey:.1f} "
                    f"{ex:.1f},{ey:.1f}")
            mx, my = sx + 34, (sy + ey) / 2
        else:
            # Back - what feeds back: down from the box, along beneath every
            # box, and up the gap before the one it goes back to, into its
            # left side.
            r = 14.0
            sx, sy = x1 + box_w / 2, y1 + box_h
            tx, ty = x2 - gap_x / 3, y2 + box_h * 0.7
            base = min(H - 6.0, lowest + 34)
            path = (f"M{sx:.1f},{sy:.1f} L{sx:.1f},{base - r:.1f} Q{sx:.1f},{base:.1f} "
                    f"{sx - r:.1f},{base:.1f} L{tx + r:.1f},{base:.1f} Q{tx:.1f},{base:.1f} "
                    f"{tx:.1f},{base - r:.1f} L{tx:.1f},{ty + r:.1f} Q{tx:.1f},{ty:.1f} "
                    f"{tx + r:.1f},{ty:.1f} L{x2 - 2:.1f},{ty:.1f}")
            mx, my = (sx + tx) / 2, base
        parts.append(f'<path d="{path}" class="dg-edge" marker-end="url(#{marker})"/>')
        if edge.get("label"):
            width = _width_of(edge["label"], 15) + 14
            labels.append(f'<rect x="{mx - width / 2:.1f}" y="{my - 12:.1f}" width="{width:.1f}" '
                          f'height="24" rx="6" class="dg-tag"/><text x="{mx:.1f}" y="{my + 5:.1f}" '
                          f'text-anchor="middle" class="dg-tag-text">{_e(edge["label"])}</text>')
    for one in nodes:
        x, y = at[one["id"]]
        lines = _wrap(one["label"], box_w - 22, size, 3 if box_h >= 70 else 2)
        first = y + box_h / 2 - (len(lines) - 1) * size * 0.62 + size * 0.36
        text = "".join(f'<tspan x="{x + box_w / 2:.1f}" y="{first + i * size * 1.24:.1f}">'
                       f'{_e(line)}</tspan>' for i, line in enumerate(lines))
        parts.append(f'<g class="dg-node"><rect x="{x:.1f}" y="{y:.1f}" width="{box_w:.1f}" '
                     f'height="{box_h:.1f}" rx="12"/><text text-anchor="middle" '
                     f'style="font-size:{size}px">{text}</text></g>')
    parts += labels
    parts.append("</svg>")
    return "".join(parts)


def _timeline(milestones: List[Dict[str, Any]]) -> str:
    items = "".join(
        f'<li class="tl-item{" done" if one["done"] else ""}">'
        f'<span class="tl-when">{_e(one["when"])}</span><span class="tl-dot"></span>'
        f'<span class="tl-what">{_e(one["what"])}</span></li>' for one in milestones)
    return (f'<ol class="tl" style="--n:{len(milestones)}"><li class="tl-line" '
            f'aria-hidden="true"></li>{items}</ol>')


def _metrics(metrics: List[Dict[str, Any]]) -> str:
    return (f'<div class="mx" style="--n:{len(metrics)}">' + "".join(
        f'<div class="mx-one"><div class="mx-value">{_e(one["value"])}</div>'
        f'<div class="mx-label">{_e(one["label"])}</div>'
        + (f'<div class="mx-note">{_e(one["note"])}</div>' if one.get("note") else "")
        + "</div>" for one in metrics) + "</div>")


def _visual_list(deck: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The slides of a drawn deck, in order - a cover first: the one the
    builder made, or one made of the deck's title when it made none."""
    slides = [one for one in deck.get("slides") or [] if isinstance(one, dict)]
    if not slides or slides[0].get("layout") != "title":
        slides.insert(0, {"layout": "title", "title": deck.get("title") or "Where the work stands",
                          "subtitle": deck.get("subtitle") or ""})
    return slides


def notes_html(notes: Any) -> str:
    """A slide's notes as they are shown beside it."""
    notes = notes if isinstance(notes, dict) else {}
    parts = []
    if notes.get("shows"):
        parts.append(f'<h3>What it shows</h3><p>{_e(notes["shows"])}</p>')
    if notes.get("terms"):
        parts.append("<h3>Key terms</h3><dl>" + "".join(
            f'<dt>{_e(one.get("term"))}</dt><dd>{_e(one.get("means"))}</dd>'
            for one in notes["terms"] if isinstance(one, dict)) + "</dl>")
    if notes.get("read"):
        parts.append(f'<h3>How to read it</h3><p>{_e(notes["read"])}</p>')
    if notes.get("takeaway"):
        parts.append(f'<h3>What to take from it</h3><p>{_e(notes["takeaway"])}</p>')
    return "".join(parts) or '<p class="no-notes">No explanation was written for this slide.</p>'


def visual_slides_of(deck: Dict[str, Any], image: ImageSource) -> List[Tuple[str, str]]:
    """A VERSION'S SLIDES, DRAWN: each slide's layout and inner HTML, in
    order - a cover first (the one the builder made, or one made of the
    deck's title when it made none), then each slide as its layout lays it
    out, the figures as `image` gives them."""
    out: List[Tuple[str, str]] = []
    slides = _visual_list(deck)
    where = " · ".join(_e(one) for one in (deck.get("version"), deck.get("label"),
                                            deck.get("version_kind")) if one)
    for n, one in enumerate(slides):
        layout = str(one.get("layout") or "bullets")
        if layout == "title":
            goal = (f'<p class="t-goal"><span>Goal</span>{_e(deck["goal"])}</p>'
                    if deck.get("goal") and n == 0 else "")
            out.append((layout, f"""<div class="t-band"></div><div class="t-body">
              <p class="t-where">{where if n == 0 else ""}</p>
              <h1>{_e(one.get("title") or deck.get("title") or "Where the work stands")}</h1>
              <p class="t-sub">{_e(one.get("subtitle") or "")}</p>{goal}</div>"""))
            continue
        src = image(one["image"]) if one.get("image") else ""
        if layout in FIGURE_LAYOUTS:
            body = _figure(src, one.get("title", ""), one.get("caption", ""))
        elif layout == "image+text":
            body = (f'<div class="it it-{_e(one.get("side") or "left")}">'
                    f'{_figure(src, one.get("title", ""), one.get("caption", ""))}'
                    f'{_points(one.get("points") or [])}</div>')
        elif layout == "two-images":
            body = ('<div class="cols" style="--n:2">' + "".join(
                _figure(image(item["image"]), item.get("caption", ""), item.get("caption", ""))
                for item in one.get("images") or []) + "</div>" + _caption(one.get("caption", "")))
        elif layout == "comparison":
            body = (f'<div class="cols cmp" style="--n:{len(one.get("columns") or [])}">' + "".join(
                f'<div class="cmp-col"><h3>{_e(item.get("heading"))}</h3>'
                + _figure(image(item["image"]) if item.get("image") else "",
                          item.get("heading", ""), item.get("caption", ""))
                + ("" if item.get("image") else _caption(item.get("caption", "")))
                + _points(item.get("points") or [], "pts small" if item.get("image") else "pts")
                + "</div>"
                for item in one.get("columns") or []) + "</div>"
                + _caption(one.get("caption", "")))
        elif layout == "diagram":
            body = (_figure(src, one.get("title", ""), one.get("caption", "")) if src else
                    f'<figure class="fig"><div class="fig-box dg-box">'
                    f'{diagram_svg(one.get("nodes") or [], one.get("edges") or [], n)}</div>'
                    + (f'<figcaption>{_e(one["caption"])}</figcaption>' if one.get("caption")
                       else "") + "</figure>")
        elif layout == "timeline":
            body = _timeline(one.get("milestones") or []) + _caption(one.get("caption", ""))
        elif layout == "metrics":
            body = _metrics(one.get("metrics") or []) + _caption(one.get("caption", ""))
        else:
            body = _points(one.get("points") or [], "pts big")
        out.append((layout, f'{_head(one)}<div class="s-body">{body}</div>'))
    return out


STYLE = """
:root { --bg:#e9e7e1; --panel:#ffffff; --ink:#1d1c1a; --muted:#6b6862; --line:#dedbd3;
        --accent:#2f5d8a; --accent-soft:#e7eef6; --warn:#9a5b13; --code:#efede7;
        --node:#f3f6fa; --shadow:0 6px 30px rgba(30,28,24,.14); }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) { --bg:#0f0f0e; --panel:#1e1d1b; --ink:#ecebe7;
    --muted:#a3a09a; --line:#3a3936; --accent:#8fb6dd; --accent-soft:#22303d;
    --warn:#e0a35a; --code:#2a2927; --node:#25303a; --shadow:0 6px 30px rgba(0,0,0,.5); } }
:root[data-theme="dark"] { --bg:#0f0f0e; --panel:#1e1d1b; --ink:#ecebe7; --muted:#a3a09a;
  --line:#3a3936; --accent:#8fb6dd; --accent-soft:#22303d; --warn:#e0a35a; --code:#2a2927;
  --node:#25303a; --shadow:0 6px 30px rgba(0,0,0,.5); }
* { box-sizing: border-box; }
html, body { margin:0; height:100%; background:var(--bg); color:var(--ink);
  font: 16px/1.5 system-ui, -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; }
.deck { height:100%; display:flex; flex-direction:column; }
.main { flex:1; display:flex; min-height:0; }
.stage { flex:1; position:relative; overflow:hidden; min-height:0; min-width:0; }
/* the slide's notes, beside it: what it shows, its terms, how to read it */
.notes { width:34%; max-width:460px; min-width:240px; overflow:auto; padding:14px 18px;
  border-left:1px solid var(--line); background:var(--panel); font-size:15px; line-height:1.55; }
.note { display:none; }
.note.on { display:block; }
.note h3 { font-size:12px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted);
  margin:14px 0 4px; }
.note h3:first-child { margin-top:0; }
.note p { margin:0; }
.note dl { margin:0; display:grid; gap:6px; }
.note dt { font-weight:650; color:var(--accent); }
.note dd { margin:0 0 0 10px; }
.no-notes { color:var(--muted); font-style:italic; }
@media (max-width: 720px) {
  .main { flex-direction:column; }
  .notes { width:auto; max-width:none; min-width:0; max-height:42%; border-left:0;
    border-top:1px solid var(--line); }
}
.frame { position:absolute; left:50%; top:50%; width:1280px; height:720px;
  transform:translate(-50%, -50%) scale(var(--k, .5)); transform-origin:50% 50%; }
.slide { display:none; position:absolute; inset:0; background:var(--panel);
  border-radius:14px; box-shadow:var(--shadow); overflow:hidden; }
.slide.on { display:block; }
.s { position:absolute; inset:0; display:flex; flex-direction:column;
  padding:52px 72px 64px; }
.s-foot { position:absolute; left:72px; right:72px; bottom:22px; display:flex;
  justify-content:space-between; color:var(--muted); font-size:15px; }
.s-foot .s-num { font-variant-numeric:tabular-nums; }
.s-head h2 { font-size:40px; line-height:1.15; margin:0; font-weight:650;
  letter-spacing:-.01em; }
.s-head::after { content:""; display:block; width:72px; height:5px; margin:16px 0 0;
  border-radius:3px; background:var(--accent); }
.s-body { flex:1; min-height:0; display:flex; flex-direction:column; margin-top:26px; }
/* the cover */
.l-title { padding:0; }
.t-band { position:absolute; left:0; top:0; bottom:0; width:22px; background:var(--accent); }
.t-body { position:absolute; left:110px; right:110px; top:0; bottom:0; display:flex;
  flex-direction:column; justify-content:center; }
.t-where { color:var(--accent); font-size:18px; font-weight:600; letter-spacing:.06em;
  text-transform:uppercase; margin:0 0 18px; min-height:1em; }
.l-title h1 { font-size:60px; line-height:1.08; margin:0; font-weight:700; letter-spacing:-.02em; }
.t-sub { font-size:26px; line-height:1.4; color:var(--muted); margin:26px 0 0; max-width:30em; }
.t-goal { margin:34px 0 0; font-size:19px; color:var(--ink); max-width:46em;
  border-left:4px solid var(--accent); padding:4px 0 4px 16px; }
.t-goal span { display:block; font-size:14px; color:var(--muted); text-transform:uppercase;
  letter-spacing:.06em; }
/* figures: as large as the slide lets them be */
.fig { margin:0; flex:1; min-height:0; min-width:0; display:flex; flex-direction:column; }
.fig-box { flex:1; min-height:0; display:flex; align-items:center; justify-content:center; }
.fig-box img { max-width:100%; max-height:100%; object-fit:contain; background:#fff;
  border-radius:8px; border:1px solid var(--line); }
.fig figcaption, .cap { text-align:center; color:var(--muted); font-size:20px;
  margin:14px 0 0; line-height:1.35; }
.it { flex:1; min-height:0; display:grid; grid-template-columns:minmax(0,3fr) minmax(0,2fr);
  gap:48px; align-items:center; }
.it.it-right { grid-template-columns:minmax(0,2fr) minmax(0,3fr); }
.it.it-right .fig { order:2; }
.it .fig { height:100%; }
.pts { list-style:none; margin:0; padding:0; display:grid; gap:20px; font-size:25px;
  line-height:1.35; }
.pts li { position:relative; padding-left:30px; }
.pts li::before { content:""; position:absolute; left:0; top:.42em; width:12px; height:12px;
  border-radius:3px; background:var(--accent); }
.pts.big { font-size:30px; gap:26px; align-content:center; flex:1; }
.pts.big li::before { width:14px; height:14px; }
.pts.small { font-size:19px; gap:10px; margin-top:12px; }
.pts.small li { padding-left:22px; }
.pts.small li::before { width:9px; height:9px; }
.cols { flex:1; min-height:0; display:grid; grid-template-columns:repeat(var(--n), minmax(0,1fr));
  gap:40px; }
.cmp-col { display:flex; flex-direction:column; min-height:0; border-top:4px solid var(--accent);
  padding-top:14px; }
.cmp-col h3 { margin:0 0 10px; font-size:24px; }
/* the diagram drawn from nodes and edges */
.dg-box { background:transparent; }
.dg { width:100%; height:100%; }
.dg-node rect { fill:var(--node); stroke:var(--accent); stroke-width:2; }
.dg-node text { fill:var(--ink); font-weight:600; }
.dg-edge { fill:none; stroke:var(--muted); stroke-width:2.4; }
.dg-head { fill:var(--muted); }
.dg-tag { fill:var(--panel); stroke:var(--line); }
.dg-tag-text { fill:var(--muted); font-size:15px; }
/* the timeline: a line, a dot a milestone */
.tl { list-style:none; margin:auto 0; padding:0; position:relative; display:grid;
  grid-template-columns:repeat(var(--n), minmax(0,1fr)); }
.tl-line { position:absolute; left:0; right:0; top:70px; height:4px; border-radius:2px;
  background:var(--line); }
.tl-item { position:relative; display:flex; flex-direction:column; align-items:center;
  text-align:center; padding:0 10px; }
.tl-when { height:52px; display:flex; align-items:flex-end; color:var(--accent);
  font-weight:650; font-size:19px; }
.tl-dot { width:26px; height:26px; border-radius:50%; margin:6px 0 16px; z-index:1;
  background:var(--panel); border:4px solid var(--accent); }
.tl-item.done .tl-dot { background:var(--accent); }
.tl-what { font-size:19px; line-height:1.35; }
/* the big numbers */
.mx { margin:auto 0; display:grid; grid-template-columns:repeat(var(--n), minmax(0,1fr)); gap:28px; }
.mx-one { background:var(--accent-soft); border-radius:14px; padding:34px 26px; text-align:center; }
.mx-value { font-size:72px; line-height:1; font-weight:750; color:var(--accent);
  font-variant-numeric:tabular-nums; letter-spacing:-.02em; overflow-wrap:anywhere; }
.mx-label { font-size:22px; margin-top:16px; font-weight:600; }
.mx-note { font-size:16px; margin-top:8px; color:var(--muted); }
/* text slides: the results deck, and a version's slides from before */
.s-text { position:absolute; inset:0; overflow:auto; padding:48px 64px 64px; font-size:19px; }
.s-text h1 { font-size:48px; line-height:1.1; margin:0 0 16px; }
.s-text h2 { font-size:36px; margin:0 0 12px; }
.s-text h3 { font-size:16px; margin:0 0 8px; color:var(--muted); text-transform:uppercase;
  letter-spacing:.04em; }
.s-text .cover { min-height:100%; display:flex; flex-direction:column; justify-content:center;
  border-left:10px solid var(--accent); padding-left:36px; }
.lead { font-size:22px; color:var(--ink); max-width:60ch; }
.goal span { font-weight:600; color:var(--accent); margin-right:6px; }
.unfinished, .fallback { color:var(--warn); font-weight:600; }
code { background:var(--code); padding:1px 5px; border-radius:4px; font-size:.92em;
  word-break:break-all; }
table { border-collapse:collapse; width:100%; margin-top:12px; }
th, td { text-align:left; vertical-align:top; padding:8px 10px;
  border-bottom:1px solid var(--line); }
th { color:var(--muted); font-weight:600; font-size:16px; }
td.name { font-weight:600; }
.result { display:grid; gap:24px; margin-top:12px; align-items:start; }
.result.with-visual { grid-template-columns: minmax(0, 3fr) minmax(0, 2fr); }
.result figure { margin:0; }
.result figure img { max-width:100%; max-height:430px; border:1px solid var(--line);
  border-radius:6px; background:#fff; }
.variables ul { list-style:none; padding:0; margin:0; display:grid; gap:12px; }
.variables li { border-left:3px solid var(--accent); padding-left:10px; }
.var { display:flex; gap:10px; align-items:baseline; flex-wrap:wrap; }
.value { font-weight:600; font-variant-numeric: tabular-nums; }
.source { color:var(--muted); font-size:16px; }
.where { color:var(--muted); font-size:16px; margin:0 0 6px; }
ul.points { margin:8px 0 0; padding-left:1.2em; display:grid; gap:8px; max-width:80ch; }
.columns { display:grid; gap:24px; grid-template-columns:repeat(auto-fit, minmax(240px, 1fr)); }
/* the controls */
.bar { display:flex; align-items:center; justify-content:center; gap:12px;
  padding:6px 12px 8px; color:var(--muted); font-variant-numeric:tabular-nums; }
.bar button { font:inherit; padding:3px 14px; border-radius:6px; cursor:pointer;
  border:1px solid var(--line); background:var(--panel); color:var(--ink); }
.bar button:hover { border-color:var(--accent); }
.bar #full { position:absolute; right:12px; }
.bar { position:relative; }
:fullscreen .bar { opacity:.35; transition:opacity .2s; }
:fullscreen .bar:hover { opacity:1; }
/* IN A FRAME OF THE APPLICATION'S PAGE, not full screen: the notes UNDER
   the slide, whole, and the slide the frame's full width - the frame is
   made as tall as both, so nothing in it scrolls. Beside the slide they
   took a third of the width and scrolled three times its height. */
.framed .main { flex-direction:column; }
.framed .stage { flex:none; }
.framed .notes { width:auto; max-width:none; min-width:0; max-height:none; overflow:visible;
  flex:none; border-left:0; border-top:1px solid var(--line); }
"""

SCRIPT = """
(() => {
  const slides = [...document.querySelectorAll('.slide')];
  const stage = document.getElementById('stage');
  const frame = document.getElementById('frame');
  const notes = document.getElementById('notes');
  let at = 0;
  // The slide is drawn at 1280 x 720 and scaled to the box it is in.
  const fit = () => {
    // In a frame, with notes: the notes below, the slide as wide as the frame.
    const below = Boolean(notes) && window.parent !== window && !document.fullscreenElement;
    document.body.classList.toggle('framed', below);
    stage.style.height = below ? (stage.clientWidth * 720 / 1280) + 'px' : '';
    const k = Math.min(stage.clientWidth / 1280, stage.clientHeight / 720);
    frame.style.setProperty('--k', String(k > 0 ? k : 0.5));
    tell();
  };
  // SHOWN IN A FRAME OF THE APPLICATION'S PAGE: how tall the frame must be
  // for the slide to fill its width - the slide, the notes when they are
  // below it, and the bar - so the frame is that tall, with nothing above
  // or below the slide to scroll past (the page caps it to the window).
  const bar = document.querySelector('.bar');
  let told = 0;
  const tell = () => {
    if (window.parent === window || document.fullscreenElement) return;
    const slide = stage.clientWidth * 720 / 1280;
    // The notes under it, as tall as the slide's own notes are: told again
    // whenever another slide is shown.
    const below = document.body.classList.contains('framed');
    const wanted = Math.ceil(slide + (below ? notes.offsetHeight : 0)
      + (bar ? bar.offsetHeight : 0));
    if (wanted > 0 && Math.abs(wanted - told) > 1) {
      told = wanted;
      window.parent.postMessage({ deckHeight: wanted, notesBelow: below }, '*');
    }
  };
  const show = (n) => {
    at = Math.max(0, Math.min(slides.length - 1, n));
    slides.forEach((s, i) => s.classList.toggle('on', i === at));
    document.querySelectorAll('.note').forEach((one) =>
      one.classList.toggle('on', Number(one.dataset.n) === at));
    document.getElementById('where').textContent = (at + 1) + ' / ' + slides.length;
    tell();
  };
  const full = document.getElementById('full');
  const toggle = () => {
    if (document.fullscreenElement) { document.exitFullscreen().catch(() => {}); return; }
    const root = document.documentElement;
    if (root.requestFullscreen) root.requestFullscreen().catch(() => {});
  };
  if (!document.fullscreenEnabled) full.hidden = true;
  full.onclick = toggle;
  document.getElementById('prev').onclick = () => show(at - 1);
  document.getElementById('next').onclick = () => show(at + 1);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowRight' || e.key === 'PageDown' || e.key === ' ') show(at + 1);
    if (e.key === 'ArrowLeft' || e.key === 'PageUp') show(at - 1);
    if (e.key === 'Home') show(0);
    if (e.key === 'End') show(slides.length - 1);
    if (e.key === 'f' || e.key === 'F') toggle();
  });
  window.addEventListener('resize', fit);
  document.addEventListener('fullscreenchange', fit);
  if (window.ResizeObserver) new ResizeObserver(fit).observe(stage);
  fit();
  // #3 opens at the third slide.
  show((parseInt(location.hash.slice(1), 10) || 1) - 1);
})();
"""


def _laid_out(deck: Dict[str, Any], image: ImageSource) -> List[Tuple[str, str]]:
    """Each slide of any deck - its layout, and its inner HTML in its box."""
    if is_visual(deck):
        return [(layout, f'<div class="s l-{_e(layout.replace("+", "-"))}">{inner}</div>')
                for layout, inner in visual_slides_of(deck, image)]
    # A version's text slides and a run's results deck: in the same frame.
    made = progress_slides_of if deck.get("kind") == "progress" else slides_of
    return [("text", f'<div class="s-text">{inner}</div>') for inner in made(deck, image)]


def render(deck: Dict[str, Any], image: Optional[ImageSource] = None) -> str:
    """The whole deck as one page: one 16:9 slide at a time, each with the
    deck's title and its number at the foot."""
    image = image or file_uri
    laid = _laid_out(deck, image)
    title = _text(deck.get("title") or "Results", 90)
    count = len(laid)
    # BESIDE EACH SLIDE, what it shows and how to read it - for a drawn
    # deck any slide of which has notes; one with none says so.
    listed = _visual_list(deck) if is_visual(deck) else []
    with_notes = any(one.get("notes") for one in listed)
    notes = "".join(
        f'<div class="note" data-n="{n}">{notes_html(one.get("notes"))}</div>'
        for n, one in enumerate(listed)) if with_notes else ""
    slides = "".join(
        f'<section class="slide">{inner}<footer class="s-foot"><span>{_e(title)}</span>'
        f'<span class="s-num">{n} / {count}</span></footer></section>'
        for n, (_layout, inner) in enumerate(laid, start=1))
    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(deck.get("title") or "Results")}</title>
<style>{STYLE}</style></head>
<body><div class="deck{" with-notes" if with_notes else ""}"><div class="main">
<div class="stage" id="stage"><div class="frame" id="frame">{slides}</div></div>{
  f'<aside class="notes" id="notes" aria-live="polite">{notes}</aside>' if with_notes else ""}</div>
<div class="bar"><button id="prev" type="button" title="Previous (&larr;)">&larr;</button><span id="where"></span>
<button id="next" type="button" title="Next (&rarr;)">&rarr;</button>
<button id="full" type="button" title="Full screen (F)">&#x26F6;</button></div></div>
<script>{SCRIPT}</script></body></html>"""


def count_of(page: str) -> int:
    """How many slides a rendered page holds."""
    return page.count('<section class="slide">')

