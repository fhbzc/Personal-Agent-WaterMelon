/* THE PICTURE BESIDE THE ANSWER (the user, 2026-11-16: the side panel in two columns - the chat, and "generate
   visualization style/graphical display"; "model觉得有必要画图的才画"). The full agent, asked from the side panel,
   may end an answer with one ```view block (agent/two_speed.py VISUAL_NOTE): what to draw, as data. Drawn here,
   by this page - no code of the model's is run - as SVG: amounts compared ("bar"), a change over time ("line"),
   a "table", a "timeline", one number ("stat"). Every chart has its table a press away and says each value
   under the pointer; colours from the panel's own four (light and dark, each checked against its surface). */

const SIDE_VIEW_BLOCK = /```view[^\n]*\n([\s\S]*?)```\s*$/;
const SIDE_VIEW_KINDS = new Set(["bar", "line", "table", "timeline", "stat"]);
/* Not a drawing of its own: marks the agent puts on a copy of a picture in the small screen, or a name it gives one
   (side-board.js). */
const SIDE_VIEW_EDITS = new Set(["marks", "name"]);
const SIDE_SERIES_MOST = 4;

/* An answer's words and its picture, the block taken out of the words. A block that is not readable, or not
   one of the kinds, is no picture - and the words are shown whole. */
function sideViewOf(text) {
  const words = String(text || "");
  const found = words.match(SIDE_VIEW_BLOCK);
  if (!found) return { words, view: null };
  let view = null;
  try { view = JSON.parse(found[1]); } catch (_err) { return { words, view: null }; }
  if (!view || typeof view !== "object" || !(SIDE_VIEW_KINDS.has(view.kind) || SIDE_VIEW_EDITS.has(view.kind))) {
    return { words, view: null };
  }
  return { words: words.slice(0, found.index).trimEnd(), view };
}

const SVG = "http://www.w3.org/2000/svg";
function svgOf(tag, attrs = {}, parent = null) {
  const one = document.createElementNS(SVG, tag);
  Object.entries(attrs).forEach(([k, v]) => one.setAttribute(k, String(v)));
  if (parent) parent.appendChild(one);
  return one;
}

function sideEl(tag, cls = "", text = "") {
  const one = document.createElement(tag);
  if (cls) one.className = cls;
  if (text) one.textContent = text;
  return one;
}

/* Numbers as people write them: a few significant figures, thousands grouped, the unit after. */
function sideNumber(value, unit = "") {
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value ?? "");
  const shown = Math.abs(n) >= 100 ? Math.round(n).toLocaleString() : Number(n.toPrecision(3)).toLocaleString();
  return unit ? `${shown} ${unit}` : shown;
}

/* Round steps for an axis from 0 (or below it) to above the most: 3-5 lines. */
function sideTicks(least, most) {
  const span = Math.max(most - least, 1e-9);
  const raw = span / 4;
  const power = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((f) => f * power).find((s) => span / s <= 5) || 10 * power;
  const ticks = [];
  for (let at = Math.floor(least / step) * step; at <= most + step * 0.001; at += step) ticks.push(Number(at.toPrecision(10)));
  if (ticks[ticks.length - 1] < most) ticks.push(ticks[ticks.length - 1] + step);
  return ticks;
}

function sideSeries(view) {
  const x = (view.x || []).map(String);
  const series = (view.series || []).slice(0, SIDE_SERIES_MOST).map((one, n) => ({
    name: String(one.name || ""), slot: n + 1,
    values: x.map((_label, i) => Number((one.values || [])[i])),
  }));
  return { x, series };
}

let sideTip = null;
function sideTipShow(event, lines) {
  if (!sideTip) { sideTip = sideEl("div", "side-viz-tip"); document.body.appendChild(sideTip); }
  sideTip.replaceChildren(...lines.map((line) => sideEl("div", "", line)));
  sideTip.hidden = false;
  const room = document.documentElement.clientWidth;
  const x = Math.min(event.clientX + 14, room - sideTip.offsetWidth - 8);
  sideTip.style.left = `${Math.max(8, x)}px`;
  sideTip.style.top = `${Math.max(8, event.clientY - sideTip.offsetHeight - 10)}px`;
}
function sideTipHide() { if (sideTip) sideTip.hidden = true; }

function sideLegend(series) {
  if (series.length < 2) return null;                       // one series: the title names it
  const row = sideEl("div", "side-viz-legend chat-message");
  series.forEach((one) => {
    const item = sideEl("span", "side-viz-key");
    item.appendChild(sideEl("i", `side-viz-swatch s${one.slot}`));
    item.appendChild(document.createTextNode(one.name));
    row.appendChild(item);
  });
  return row;
}

/* A bar's top rounded, its foot square on the baseline. */
function sideBarPath(x, y, w, h, up) {
  const r = Math.min(4, w / 2, Math.abs(h));
  if (up) return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
  return `M${x},${y}V${y + h - r}Q${x},${y + h} ${x + r},${y + h}H${x + w - r}Q${x + w},${y + h} ${x + w},${y + h - r}V${y}Z`;
}

function sideChart(view, room) {
  const { x, series } = sideSeries(view);
  const values = series.flatMap((one) => one.values.filter(Number.isFinite));
  if (!x.length || !values.length) return null;
  const width = Math.max(260, room), height = 240;
  const pad = { left: 44, right: 12, top: 10, bottom: 30 };
  const plotW = width - pad.left - pad.right, plotH = height - pad.top - pad.bottom;
  const ticks = sideTicks(Math.min(0, ...values), Math.max(0, ...values));
  const least = ticks[0], most = ticks[ticks.length - 1];
  const yOf = (v) => pad.top + plotH - ((v - least) / (most - least || 1)) * plotH;
  const svg = svgOf("svg", { viewBox: `0 0 ${width} ${height}`, width, height, role: "img",
    "aria-label": String(view.title || view.kind) });
  const grid = svgOf("g", { class: "side-viz-grid" }, svg);
  ticks.forEach((tick) => {
    const y = yOf(tick);
    svgOf("line", { x1: pad.left, x2: width - pad.right, y1: y, y2: y, class: tick === 0 ? "zero" : "" }, grid);
    const label = svgOf("text", { x: pad.left - 6, y: y + 4, "text-anchor": "end", class: "side-viz-axis" }, grid);
    label.textContent = sideNumber(tick);
  });
  const band = plotW / x.length;
  // EVERY LABEL WHILE THERE IS ROOM FOR ONE (2026-11-16, looked at: four of four, seven of seven fitted, and every
  // other one was left out): a label each band, cut to the band's width; only a band too narrow for any word
  // shows every second or third.
  const every = band >= 28 ? 1 : Math.ceil(28 / band);
  const fits = Math.max(3, Math.floor(band * every / 6.5));
  x.forEach((label, i) => {
    if (i % every) return;
    const text = svgOf("text", { x: pad.left + band * (i + 0.5), y: height - 10, "text-anchor": "middle",
      class: "side-viz-axis" }, svg);
    text.textContent = label.length > fits ? `${label.slice(0, fits - 1)}…` : label;
  });
  const unit = String(view.unit || "");
  if (view.kind === "bar") {
    const group = Math.min(band * 0.72, 56 * series.length);
    const gap = series.length > 1 ? 2 : 0;
    const w = Math.max(2, (group - gap * (series.length - 1)) / series.length);
    const peak = Math.max(...values);
    x.forEach((label, i) => {
      series.forEach((one, k) => {
        const v = one.values[i];
        if (!Number.isFinite(v)) return;
        const left = pad.left + band * i + (band - group) / 2 + k * (w + gap);
        const top = yOf(Math.max(v, 0)), foot = yOf(Math.min(v, 0));
        const bar = svgOf("path", { d: sideBarPath(left, top, w, Math.max(foot - top, 0.5), v >= 0),
          class: `side-viz-bar s${one.slot}` }, svg);
        // The hit target larger than the bar: the whole band of its group.
        const hit = svgOf("rect", { x: pad.left + band * i, y: pad.top, width: band, height: plotH,
          class: "side-viz-hit" }, svg);
        if (k === 0) {                                     // the group, found inside a mark by its middle
          bar.dataset.i = String(i);
          bar.dataset.x = String(pad.left + band * (i + 0.5));
          bar.dataset.y = String((top + foot) / 2);
        }
        const said = series.map((s) => `${s.name ? `${s.name}: ` : ""}${sideNumber(s.values[i], unit)}`);
        hit.addEventListener("pointermove", (event) => sideTipShow(event, [label, ...said]));
        hit.addEventListener("pointerleave", sideTipHide);
        bar.dataset.value = String(v);
        if (series.length === 1 && v === peak) {           // the one number said on the chart: the most
          const tag = svgOf("text", { x: left + w / 2, y: top - 5, "text-anchor": "middle", class: "side-viz-label" }, svg);
          tag.textContent = sideNumber(v, unit);
        }
      });
    });
  } else {
    const xOf = (i) => pad.left + band * (i + 0.5);
    series.forEach((one, k) => {
      const points = one.values.map((v, i) => (Number.isFinite(v) ? `${xOf(i)},${yOf(v)}` : null)).filter(Boolean);
      svgOf("polyline", { points: points.join(" "), class: `side-viz-line s${one.slot}` }, svg);
      // Each point where it is, unseen: what a mark round it is found to hold.
      one.values.forEach((v, i) => {
        if (Number.isFinite(v)) svgOf("circle", { cx: xOf(i), cy: yOf(v), r: 3, class: "side-viz-anchor",
          "data-i": i, "data-x": xOf(i), "data-y": yOf(v) }, svg);
      });
    });
    if (series.length === 1) {                             // the last value said, at its end
      const last = series[0].values.length - 1;
      const v = series[0].values[last];
      if (Number.isFinite(v)) {
        const tag = svgOf("text", { x: xOf(last), y: yOf(v) - 8, "text-anchor": "end", class: "side-viz-label" }, svg);
        tag.textContent = sideNumber(v, unit);
      }
    }
    const cross = svgOf("line", { y1: pad.top, y2: pad.top + plotH, class: "side-viz-cross", visibility: "hidden" }, svg);
    const dots = series.map((one) => svgOf("circle", { r: 4.5, class: `side-viz-dot s${one.slot}`, visibility: "hidden" }, svg));
    const hit = svgOf("rect", { x: pad.left, y: pad.top, width: plotW, height: plotH, class: "side-viz-hit" }, svg);
    hit.addEventListener("pointermove", (event) => {
      const box = svg.getBoundingClientRect();
      const at = ((event.clientX - box.left) / box.width) * width;
      const i = Math.max(0, Math.min(x.length - 1, Math.floor((at - pad.left) / band)));
      cross.setAttribute("x1", xOf(i)); cross.setAttribute("x2", xOf(i)); cross.setAttribute("visibility", "visible");
      series.forEach((one, k) => {
        const v = one.values[i];
        dots[k].setAttribute("visibility", Number.isFinite(v) ? "visible" : "hidden");
        if (Number.isFinite(v)) { dots[k].setAttribute("cx", xOf(i)); dots[k].setAttribute("cy", yOf(v)); }
      });
      sideTipShow(event, [x[i], ...series.map((s) => `${s.name ? `${s.name}: ` : ""}${sideNumber(s.values[i], unit)}`)]);
    });
    hit.addEventListener("pointerleave", () => {
      cross.setAttribute("visibility", "hidden");
      dots.forEach((dot) => dot.setAttribute("visibility", "hidden"));
      sideTipHide();
    });
  }
  return svg;
}

function sideTable(columns, rows) {
  const table = sideEl("table", "side-viz-table chat-message");
  const head = sideEl("tr");
  columns.forEach((name) => head.appendChild(sideEl("th", "", String(name))));
  table.appendChild(sideEl("thead")).appendChild(head);
  const body = table.appendChild(sideEl("tbody"));
  rows.slice(0, 20).forEach((row) => {
    const line = sideEl("tr");
    (Array.isArray(row) ? row : [row]).forEach((cell) => line.appendChild(sideEl("td", "", String(cell ?? ""))));
    body.appendChild(line);
  });
  return table;
}

/* The chart's own numbers as a table: the same data, read without colour. */
function sideChartTable(view) {
  const { x, series } = sideSeries(view);
  const unit = String(view.unit || "");
  return sideTable(["", ...series.map((one) => one.name || (unit || "Value"))],
    x.map((label, i) => [label, ...series.map((one) => sideNumber(one.values[i], unit))]));
}

/* -- MARKED BY THE USER (the user, 2026-11-16: "这个图用户是可以用类似画图的方法edit (比如画圈之类的), 然后model可以把
   用户edit的内容作为input"): "Mark" on, a drag draws a ring on the picture; what a ring holds - a bar, a point, a
   row, a step - is said with the next message, in words the user sees too. -- */
const sideMarks = { view: null, rings: [], on: false };

/* What a picture's parts are, each where it is on the screen and what it says - to be found inside a ring. */
function sideParts(card, view) {
  const parts = [];
  const { x, series } = view.kind === "bar" || view.kind === "line" ? sideSeries(view) : { x: [], series: [] };
  const unit = String(view.unit || "");
  // A chart's parts where they were drawn (data-x, data-y, in the drawing's own units), put on the screen by
  // where the drawing is and how large - not asked of each part, which a panel not shown does not lay out.
  const svg = card.querySelector(".side-viz-plot svg");
  const frame = svg ? svg.getBoundingClientRect() : null;
  const across = svg ? Number(svg.getAttribute("width")) || 1 : 1;
  const scale = frame && frame.width ? frame.width / across : 1;
  card.querySelectorAll(".side-viz-plot [data-x]").forEach((one) => {
    const i = Number(one.dataset.i);
    const px = frame.left + Number(one.dataset.x) * scale, py = frame.top + Number(one.dataset.y) * scale;
    parts.push({ box: { left: px, top: py, width: 0, height: 0 },
      said: `${x[i]} (${series.map((s) => `${s.name ? `${s.name} ` : ""}${sideNumber(s.values[i], unit)}`).join(", ")})` });
  });
  card.querySelectorAll(".side-viz-table:not([hidden]) tbody tr, .side-viz-timeline li").forEach((row) => {
    parts.push({ box: row.getBoundingClientRect(), said: [...row.children].map((one) => one.textContent).join(" · ") });
  });
  card.querySelectorAll(".side-viz-stat").forEach((one) => parts.push({ box: one.getBoundingClientRect(),
    said: [...one.children].map((part) => part.textContent).join(" - ") }));
  return parts;
}

/* `ring` in the layer's own place (scrolled or not); `box` on the screen, taken there by `area`. */
function sideInRing(box, ring, area) {
  const cx = box.left + box.width / 2 - area.left, cy = box.top + box.height / 2 - area.top;
  const rx = Math.max(ring.rx, 1), ry = Math.max(ring.ry, 1);
  return ((cx - ring.cx) / rx) ** 2 + ((cy - ring.cy) / ry) ** 2 <= 1;
}

/* What the rings hold, each part once, in the order they are marked. */
function sideHeld(parts, rings, area) {
  const held = [];
  rings.forEach((ring) => parts.forEach((part) => {
    if (sideInRing(part.box, ring, area) && !held.includes(part.said)) held.push(part.said);
  }));
  return held;
}

/* What the rings hold, said: "" when nothing is marked. */
function sideMarksSaid() {
  const card = document.querySelector("#side-visual-body .side-viz-card");
  if (!sideMarks.view || !sideMarks.rings.length || !card) return "";
  const parts = sideParts(card, sideMarks.view);
  const area = card.querySelector(".side-viz-layer").getBoundingClientRect();
  const held = sideHeld(parts, sideMarks.rings, area);
  const title = sideMarks.view.title ? ` "${sideMarks.view.title}"` : "";
  return held.length ? `[On the picture${title}, I marked: ${held.join("; ")}]`
    : `[On the picture${title}, I drew ${sideMarks.rings.length} mark(s) on no part of it]`;
}

function sideMarksClear() {
  sideMarks.rings = [];
  document.querySelectorAll("#side-visual-body .side-viz-ring").forEach((one) => one.remove());
}

/* The marking layer over a drawn card: rings drawn by dragging while "Mark" is on. */
function sideMarking(card, view) {
  sideMarks.view = view;
  sideMarks.rings = [];
  const tools = sideEl("div", "side-viz-tools");
  const mark = sideEl("button", "small secondary", "Mark");
  mark.type = "button";
  mark.setAttribute("aria-pressed", "false");
  const clear = sideEl("button", "small secondary", "Clear");
  clear.type = "button";
  tools.append(mark, clear);
  card.prepend(tools);
  const layer = sideEl("div", "side-viz-layer");
  card.appendChild(layer);
  const setOn = (on) => {
    sideMarks.on = on;
    mark.setAttribute("aria-pressed", String(on));
    card.classList.toggle("marking", on);
  };
  mark.addEventListener("click", () => setOn(!sideMarks.on));
  clear.addEventListener("click", sideMarksClear);
  let start = null, ring = null;
  layer.addEventListener("pointerdown", (event) => {
    if (!sideMarks.on) return;
    try { layer.setPointerCapture(event.pointerId); } catch (_err) { /* the drag followed as it goes */ }
    start = { x: event.clientX, y: event.clientY };
    ring = sideEl("div", "side-viz-ring");
    layer.appendChild(ring);
  });
  layer.addEventListener("pointermove", (event) => {
    if (!start || !ring) return;
    const area = layer.getBoundingClientRect();
    const left = Math.min(start.x, event.clientX), top = Math.min(start.y, event.clientY);
    ring.style.left = `${left - area.left}px`;
    ring.style.top = `${top - area.top}px`;
    ring.style.width = `${Math.abs(event.clientX - start.x)}px`;
    ring.style.height = `${Math.abs(event.clientY - start.y)}px`;
  });
  layer.addEventListener("pointerup", (event) => {
    if (!start || !ring) return;
    const w = Math.abs(event.clientX - start.x), h = Math.abs(event.clientY - start.y);
    const area = layer.getBoundingClientRect();
    if (w < 6 && h < 6) ring.remove();
    else sideMarks.rings.push({ cx: (start.x + event.clientX) / 2 - area.left, cy: (start.y + event.clientY) / 2 - area.top,
      rx: w / 2, ry: h / 2 });
    start = null;
    ring = null;
  });
}

/* The picture drawn into `where`, the room there is; true when there was one to draw. */
function sideDraw(where, view) {
  where.replaceChildren();
  if (!view) return false;
  // What the model wrote is never looked up to be translated (.chat-message, i18n.js) - the panel's own
  // words on it, its Table / Chart button, are.
  const card = sideEl("figure", "side-viz-card");
  if (view.title) card.appendChild(sideEl("figcaption", "side-viz-title chat-message", String(view.title)));
  const room = Math.max(260, (where.clientWidth || 440) - 32);
  let drawn = null;
  if (view.kind === "bar" || view.kind === "line") {
    const chart = sideChart(view, room);
    if (!chart) return false;
    const legend = sideLegend(sideSeries(view).series);
    if (legend) card.appendChild(legend);
    const plot = sideEl("div", "side-viz-plot chat-message");
    plot.appendChild(chart);
    card.appendChild(plot);
    const asTable = sideChartTable(view);
    asTable.hidden = true;
    card.appendChild(asTable);
    const toggle = sideEl("button", "small secondary side-viz-toggle", "Table");
    toggle.type = "button";
    toggle.addEventListener("click", () => {
      const showing = asTable.hidden;
      asTable.hidden = !showing;
      plot.hidden = showing;
      toggle.textContent = showing ? "Chart" : "Table";
    });
    card.appendChild(toggle);
    drawn = chart;
  } else if (view.kind === "table") {
    drawn = sideTable(view.columns || [], view.rows || []);
    card.appendChild(drawn);
  } else if (view.kind === "timeline") {
    drawn = sideEl("ol", "side-viz-timeline chat-message");
    (view.items || []).slice(0, 30).forEach((one) => {
      const item = sideEl("li");
      item.appendChild(sideEl("span", "side-viz-when", String(one.when || "")));
      item.appendChild(sideEl("span", "side-viz-what", String(one.what || "")));
      if (one.note) item.appendChild(sideEl("span", "side-viz-note", String(one.note)));
      drawn.appendChild(item);
    });
    card.appendChild(drawn);
  } else if (view.kind === "stat") {
    drawn = sideEl("div", "side-viz-stat chat-message");
    drawn.appendChild(sideEl("div", "side-viz-value", String(view.value ?? "")));
    if (view.label) drawn.appendChild(sideEl("div", "side-viz-what", String(view.label)));
    if (view.note) drawn.appendChild(sideEl("div", "side-viz-note", String(view.note)));
    card.appendChild(drawn);
  }
  if (!drawn) return false;
  sideMarking(card, view);
  where.appendChild(card);
  return true;
}
