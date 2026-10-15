/* System Debugger (section 14).
   Interactive system map + component/connection inspector + trace timeline.
   Everything is drawn from the runtime component registry served by
   /api/debugger/*; nothing here is a static diagram. */
"use strict";

(() => {
  const $ = (s) => document.querySelector(s);
  const esc = (v) =>
    String(v ?? "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STATE_COLORS = {
    running: "--state-running", idle: "--state-idle", busy: "--state-busy",
    degraded: "--state-degraded", blocked: "--state-blocked", failed: "--state-failed",
    stopped: "--state-stopped", disabled: "--state-disabled",
    unavailable: "--state-unavailable", starting: "--state-busy",
    healthy: "--state-running",
  };

  const S = {
    topology: null, nodes: new Map(), edges: [], collapsed: new Set(),
    selection: null, tab: "overview", detail: null,
    traces: [], trace: null, highlight: { nodes: new Set(), edges: new Set() },
    search: "", window: "", focus: "", filter: "",
    view: { x: 0, y: 0, scale: 1 }, hover: null, loaded: false, fitted: false,
  };

  const canvas = $("#dbg-map");
  const ctx = canvas.getContext("2d");
  const color = (token) =>
    getComputedStyle(document.body).getPropertyValue(token).trim() || "#888";

  /* ------------------------------------------------------------- loading */

  /* Section 14.33: one panel per module.

     The map says which components exist and how they connect. It cannot say
     what any one of them holds, so a wrong event or a stuck task is invisible
     until something downstream misbehaves. These panels say what is inside.
  */
  const moduleState = { open: {}, choice: {}, payload: null, shape: "" };

  async function loadModules() {
    let payload = { monitoring: true, modules: [] };
    try {
      payload = await api.get("/api/debugger/modules");
    } catch (err) {
      payload = { monitoring: true, modules: [] };
    }
    moduleState.payload = payload;
    renderModules();
  }

  function renderModules() {
    const payload = moduleState.payload || { monitoring: true, modules: [] };
    const box = document.getElementById("dbg-modules");
    if (!box) return;
    const toggle = document.getElementById("dbg-monitoring");
    if (toggle) toggle.checked = Boolean(payload.monitoring);

    if (!payload.monitoring) {
      box.innerHTML = `<div class="card dbg-off">
        <strong>Monitoring is off.</strong>
        <div class="muted">Nothing is being collected, so the panels have
          nothing to show. The agent goes on working exactly as before.
          Turning it back on starts collection from that moment - it cannot
          recover what was not gathered.</div></div>`;
      return;
    }

    const shown = payload.modules.filter(
      (mod) => !S.focus || mod.parent === S.focus || mod.id === S.focus);
    if (!shown.length) {
      box.innerHTML = `<div class="card dbg-off muted">No modules in this part of
        the system. Choose "Whole system" to see them all.</div>`;
      moduleState.shape = "";
      return;
    }

    /* Section 14.33: a live refresh must not destroy what is being read.

       These panels refresh every ten seconds. Replacing the grid wholesale
       each time collapses whatever entry the reader had opened, loses their
       place in the list, and resets the type they had chosen - so the panel
       is unreadable exactly when it is being read. When the set of modules
       has not changed, the numbers are written into the panels that are
       already there and nothing else is touched.
    */
    const shape = shown.map((mod) => mod.id).join("|");
    if (shape === moduleState.shape && box.querySelector(".dbg-module")) {
      shown.forEach((mod) => {
        const panel = box.querySelector(`.dbg-module[data-module="${mod.id}"]`);
        if (!panel) return;
        const stats = panel.querySelector(".dbg-module-stats");
        const fresh = statLine(mod.status, mod.metrics);
        if (stats && stats.innerHTML !== fresh) stats.innerHTML = fresh;
      });
      refreshOpenDetails();
      return;
    }
    moduleState.shape = shape;

    box.innerHTML = shown.map((mod) => `
      <div class="card dbg-module" data-module="${escapeHtml(mod.id)}">
        <div class="dbg-module-head">
          <h3>${escapeHtml(mod.name || mod.id)}</h3>
          <span class="badge muted">${escapeHtml(mod.component_type || "")}</span>
        </div>
        <div class="muted dbg-module-what">${
          escapeHtml(mod.description || "No description recorded for this module.")}</div>
        <div class="dbg-module-stats">${statLine(mod.status, mod.metrics)}</div>
        ${mod.has_detail
          ? `<button type="button" class="small secondary dbg-module-open">${
              moduleState.open[mod.id] ? "Hide what is in it" : "Show what is in it"}</button>
             <div class="dbg-module-detail" data-for="${escapeHtml(mod.id)}"></div>`
          : `<div class="muted dbg-module-nodetail">No detail view for this module.</div>`}
      </div>`).join("");

    box.querySelectorAll(".dbg-module-open").forEach((button) => {
      button.addEventListener("click", async () => {
        const id = button.closest(".dbg-module").dataset.module;
        moduleState.open[id] = !moduleState.open[id];
        button.textContent = moduleState.open[id]
          ? "Hide what is in it" : "Show what is in it";
        const host = box.querySelector(`.dbg-module-detail[data-for="${id}"]`);
        if (!moduleState.open[id]) { host.innerHTML = ""; return; }
        await renderModuleDetail(id, host);
      });
    });

    Object.keys(moduleState.open).forEach(async (id) => {
      if (!shown.some((mod) => mod.id === id)) return;
      if (!moduleState.open[id]) return;
      const host = box.querySelector(`.dbg-module-detail[data-for="${id}"]`);
      if (host) await renderModuleDetail(id, host);
    });
  }

  function refreshOpenDetails() {
    const box = document.getElementById("dbg-modules");
    if (!box) return;
    Object.keys(moduleState.open).forEach(async (id) => {
      if (!moduleState.open[id]) return;
      const host = box.querySelector(`.dbg-module-detail[data-for="${id}"]`);
      if (host) await renderModuleDetail(id, host);
    });
  }

  function statLine(status, metrics) {
    const pairs = [];
    Object.entries(status || {}).forEach(([k, v]) => pairs.push([k, v]));
    Object.entries(metrics || {}).forEach(([k, v]) => pairs.push([k, v]));
    if (!pairs.length) return "";
    return pairs.slice(0, 8).map(([k, v]) =>
      `<span class="dbg-stat"><span class="muted">${escapeHtml(k)}</span> ${
        escapeHtml(shortValue(v))}</span>`).join("");
  }

  function shortValue(value) {
    if (value === null || value === undefined) return "-";
    if (typeof value === "object") return JSON.stringify(value).slice(0, 60);
    return String(value).slice(0, 60);
  }

  async function renderModuleDetail(id, host) {
    // What the reader had opened and where they were, so a refresh can put
    // them back rather than starting them over.
    const wasOpen = new Set(
      [...host.querySelectorAll(".dbg-entry[open]")].map((el) => el.dataset.key));
    const scroller = host.closest(".dbg-module") || host;
    const scrollTop = scroller.scrollTop;
    const hadContent = Boolean(host.querySelector(".dbg-groups"));
    if (!hadContent) host.innerHTML = `<div class="muted">Reading…</div>`;
    const chosen = moduleState.choice[id] || {};
    const query = Object.entries(chosen)
      .filter(([, v]) => v)
      .map(([k, v]) => `&${k}=${encodeURIComponent(v)}`).join("");
    let detail = {};
    try {
      detail = (await api.get(`/api/debugger/module?id=${encodeURIComponent(id)}${query}`)).detail;
    } catch (err) {
      host.innerHTML = `<div class="muted">Could not read this module: ${
        escapeHtml(err.message)}</div>`;
      return;
    }
    if (detail && detail.monitoring === "off") {
      host.innerHTML = `<div class="muted">${escapeHtml(detail.note || "")}</div>`;
      return;
    }
    if (detail && detail.error) {
      host.innerHTML = `<div class="muted">${escapeHtml(detail.error)}</div>`;
      return;
    }

    const summary = Object.entries(detail.summary || {}).map(([k, v]) =>
      `<span class="dbg-stat"><span class="muted">${escapeHtml(k)}</span> ${
        escapeHtml(shortValue(v))}</span>`).join("");

    const choices = detail.choices || null;
    const picker = choices ? `
      <label class="dbg-picker">${escapeHtml(choices.label || "Show")}
        <select data-field="${escapeHtml(choices.field)}">
          <option value="">All (${(choices.options || []).length})</option>
          ${(choices.options || []).map((o) =>
            `<option value="${escapeHtml(o.value)}"${
              o.value === choices.selected ? " selected" : ""}>${
              escapeHtml(o.label)}</option>`).join("")}
        </select>
      </label>` : "";

    const groups = (detail.groups || []).map((group) => {
      const label = group[choices ? choices.field : "group"] ?? "";
      const rows = (group.events || []).map((entry) => `
        <details class="dbg-entry" data-key="${escapeHtml(entryKey(entry))}"${
          wasOpen.has(entryKey(entry)) ? " open" : ""}>
          <summary>${escapeHtml(entryLabel(entry))}</summary>
          <table class="dbg-fields">${Object.entries(entry).map(([k, v]) =>
            `<tr><th>${escapeHtml(k)}</th><td>${escapeHtml(fullValue(v))}</td></tr>`
          ).join("")}</table>
        </details>`).join("");
      return `<div class="dbg-group">
          <div class="dbg-group-head">${escapeHtml(String(label))}
            <span class="muted">${group.count} total, showing ${
              (group.events || []).length}</span></div>
          ${rows || `<div class="muted">nothing recorded</div>`}
        </div>`;
    }).join("");

    host.innerHTML = `<div class="dbg-module-stats">${summary}</div>
      ${picker}<div class="dbg-groups">${groups}</div>`;

    scroller.scrollTop = scrollTop;

    const select = host.querySelector("select[data-field]");
    if (select) {
      // What the reader chose is the client's own record, not something to
      // read back from the response: a refresh must not quietly return them
      // to the unfiltered list because the reply did not echo the choice.
      const chosen = (moduleState.choice[id] || {})[select.dataset.field];
      if (chosen !== undefined) select.value = chosen;
      select.addEventListener("change", async () => {
        moduleState.choice[id] = { [select.dataset.field]: select.value };
        await renderModuleDetail(id, host);
      });
    }
  }

  function entryKey(entry) {
    // Rows are matched across refreshes by their own id where they have one,
    // and by time and kind where they do not.
    return String(entry.id || `${entry.timestamp || entry.scheduled_at || ""}` +
      `/${entry.event_type || entry.action || entry.type || ""}`);
  }

  function entryLabel(entry) {
    const when = entry.timestamp || entry.scheduled_at || entry.updated_at
      || entry.created_at || "";
    const what = entry.title || entry.event_type || entry.action || entry.type
      || entry.id || "";
    return `${String(when).slice(0, 19)}  ${what}`.trim();
  }

  function fullValue(value) {
    if (value === null || value === undefined) return "";
    if (typeof value === "object") return JSON.stringify(value, null, 1);
    return String(value);
  }

  async function setMonitoring(enabled) {
    try {
      await api.post("/api/debugger/monitoring", { enabled });
      await loadModules();
    } catch (err) {
      /* leave the checkbox as the server left it */
    }
  }

  // Binding only. The panels themselves load when the tab is opened, the way
  // everything else in this file does - loading them at page load fetches for
  // a tab the user may never open, and then never refreshes.
  document.addEventListener("DOMContentLoaded", () => {
    const toggle = document.getElementById("dbg-monitoring");
    if (toggle) {
      toggle.addEventListener("change", () => setMonitoring(toggle.checked));
    }
  });

  async function loadTopology() {
    const data = await api.get("/api/debugger/topology");
    S.topology = data;
    layout(data);
    if (!S.loaded) {
      const focus = $("#dbg-focus");
      focus.innerHTML =
        '<option value="">Whole system</option>' +
        data.subsystems.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join("");
      data.subsystems.forEach((s) => S.collapsed.add(s.id));
      S.loaded = true;
      layout(data);
    }
    $("#dbg-status").textContent =
      `${data.components.length} components · ${data.connections.length} connections · ` +
      `v${data.application.version}`;
    renderLegend();
    if (!S.fitted) { fitToView(); S.fitted = true; } else { draw(); }
  }

  /* Deterministic layout (section 14.18): collapsed subsystems flow in rows,
     an expanded subsystem gets a full-width band with its components beside it. */
  function layout(data) {
    S.nodes = new Map();
    const byParent = new Map();
    for (const node of data.components) {
      if (node.component_type === "subsystem") continue;
      const parent = node.parent_component || "platform";
      if (!byParent.has(parent)) byParent.set(parent, []);
      byParent.get(parent).push(node);
    }
    const ROW_WIDTH = 1020;
    let x = 60;
    let y = 60;
    let rowHeight = 0;

    for (const { id: band } of data.subsystems) {
      const subsystem = data.components.find((c) => c.id === band);
      if (!subsystem) continue;
      const children = byParent.get(band) || [];

      if (S.collapsed.has(band) || !children.length) {
        if (x > 60 && x + 200 > ROW_WIDTH) { x = 60; y += rowHeight + 26; rowHeight = 0; }
        S.nodes.set(band, {
          ...subsystem, x: x + 95, y: y + 24, w: 190, h: 48, band, isSubsystem: true,
          childCount: children.length,
          state: worstState(children.map((c) => c.state)) || subsystem.state,
        });
        x += 214;
        rowHeight = Math.max(rowHeight, 48);
        continue;
      }

      if (x > 60) { x = 60; y += rowHeight + 26; rowHeight = 0; }
      const perRow = 4;
      const rows = Math.ceil(children.length / perRow);
      const bandHeight = Math.max(52, rows * 52);
      S.nodes.set(band, {
        ...subsystem, x: 145, y: y + bandHeight / 2, w: 160, h: 44, band,
        isSubsystem: true, expanded: true, childCount: children.length,
        state: subsystem.state,
      });
      children.forEach((child, index) => {
        S.nodes.set(child.id, {
          ...child, band,
          x: 330 + (index % perRow) * 176,
          y: y + 22 + Math.floor(index / perRow) * 52,
          w: 164, h: 40,
        });
      });
      y += bandHeight + 30;
      x = 60;
      rowHeight = 0;
    }

    S.edges = data.connections
      .map((edge) => ({ ...edge, from: resolveNode(edge.source), to: resolveNode(edge.target) }))
      .filter((edge) => edge.from && edge.to && edge.from !== edge.to);
  }

  /* Fit the whole topology into the visible map area. */
  function fitToView() {
    const nodes = [...S.nodes.values()].filter(inFocus);
    if (!nodes.length) return draw();
    const rect = canvas.getBoundingClientRect();
    const minX = Math.min(...nodes.map((n) => n.x - n.w / 2)) - 24;
    const maxX = Math.max(...nodes.map((n) => n.x + n.w / 2)) + 24;
    const minY = Math.min(...nodes.map((n) => n.y - n.h / 2)) - 24;
    const maxY = Math.max(...nodes.map((n) => n.y + n.h / 2)) + 24;
    const scale = Math.min(rect.width / (maxX - minX), rect.height / (maxY - minY), 1.6);
    S.view.scale = Math.max(0.3, scale);
    S.view.x = (rect.width - (maxX - minX) * S.view.scale) / 2 - minX * S.view.scale;
    S.view.y = (rect.height - (maxY - minY) * S.view.scale) / 2 - minY * S.view.scale;
    draw();
  }

  function resolveNode(id) {
    if (S.nodes.has(id)) return S.nodes.get(id);
    const component = S.topology.components.find((c) => c.id === id);
    if (component && component.parent_component && S.nodes.has(component.parent_component)) {
      return S.nodes.get(component.parent_component);
    }
    return null;
  }

  function worstState(states) {
    const order = ["failed", "blocked", "degraded", "busy", "running", "starting",
                   "idle", "stopped", "disabled", "unavailable"];
    for (const state of order) if (states.includes(state)) return state;
    return null;
  }

  function inFocus(node) {
    if (!S.focus) return true;
    return node.band === S.focus || node.id === S.focus;
  }

  /* ------------------------------------------------------------ drawing */
  function draw() {
    const ratio = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * ratio;
    canvas.height = rect.height * ratio;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, rect.width, rect.height);
    ctx.save();
    ctx.translate(S.view.x, S.view.y);
    ctx.scale(S.view.scale, S.view.scale);

    for (const edge of S.edges) {
      if (!inFocus(edge.from) || !inFocus(edge.to)) continue;
      drawEdge(edge);
    }
    for (const node of S.nodes.values()) {
      if (!inFocus(node)) continue;
      drawNode(node);
    }
    ctx.restore();
  }

  function anchor(node, other) {
    const dx = other.x - node.x;
    return { x: node.x + (dx >= 0 ? node.w / 2 : -node.w / 2), y: node.y };
  }

  function drawEdge(edge) {
    const a = anchor(edge.from, edge.to);
    const b = anchor(edge.to, edge.from);
    const selected = S.selection?.kind === "connection" && S.selection.id === edge.id;
    const highlighted = S.highlight.edges.has(edge.id);
    const failing = edge.state === "failed" || edge.state === "blocked";
    ctx.strokeStyle = failing ? color("--state-failed")
      : selected || highlighted ? color("--accent") : color("--line");
    ctx.lineWidth = selected || highlighted ? 2.4 : 1.2;
    if (edge.active) ctx.setLineDash([6, 4]);
    ctx.beginPath();
    const midX = (a.x + b.x) / 2;
    ctx.moveTo(a.x, a.y);
    ctx.bezierCurveTo(midX, a.y, midX, b.y, b.x, b.y);
    ctx.stroke();
    ctx.setLineDash([]);
    const angle = Math.atan2(b.y - a.y, b.x - midX);
    ctx.fillStyle = ctx.strokeStyle;
    ctx.beginPath();
    ctx.moveTo(b.x, b.y);
    ctx.lineTo(b.x - Math.cos(angle - 0.4) * 9, b.y - Math.sin(angle - 0.4) * 9);
    ctx.lineTo(b.x - Math.cos(angle + 0.4) * 9, b.y - Math.sin(angle + 0.4) * 9);
    ctx.closePath();
    ctx.fill();
    edge._hit = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
  }

  function drawNode(node) {
    const selected = S.selection?.kind === "component" && S.selection.id === node.id;
    const highlighted = S.highlight.nodes.has(node.id);
    const matched = S.search && (node.name.toLowerCase().includes(S.search) ||
                                 node.id.toLowerCase().includes(S.search));
    const x = node.x - node.w / 2;
    const y = node.y - node.h / 2;
    ctx.beginPath();
    ctx.roundRect(x, y, node.w, node.h, 9);
    ctx.fillStyle = color("--panel");
    ctx.fill();
    ctx.lineWidth = selected ? 2.5 : highlighted || matched ? 2 : 1;
    ctx.strokeStyle = selected || highlighted ? color("--accent")
      : matched ? color("--warn") : color("--line");
    ctx.stroke();

    ctx.fillStyle = color(STATE_COLORS[node.state] || "--state-idle");
    ctx.beginPath();
    ctx.arc(x + 12, node.y, 4.5, 0, Math.PI * 2);
    ctx.fill();

    ctx.fillStyle = color("--ink");
    ctx.font = node.isSubsystem ? "600 12.5px system-ui" : "12px system-ui";
    ctx.textBaseline = "middle";
    const label = node.isSubsystem && node.childCount && S.collapsed.has(node.id)
      ? `${node.name} (${node.childCount})` : node.name;
    ctx.fillText(truncate(label, node.isSubsystem ? 20 : 19), x + 22, node.y - 1);
    if (node.error_count) {
      ctx.fillStyle = color("--err");
      ctx.font = "10px system-ui";
      ctx.fillText(`${node.error_count} err`, x + node.w - 34, node.y + 12);
    }
  }

  function truncate(text, max) {
    return text.length > max ? `${text.slice(0, max - 1)}…` : text;
  }

  function renderLegend() {
    const states = ["running", "idle", "busy", "degraded", "blocked", "failed",
                    "stopped", "disabled", "unavailable"];
    $("#dbg-legend").innerHTML = states
      .map((s) => `<span><i style="background:var(${STATE_COLORS[s]})"></i>${s}</span>`)
      .join("") + '<span><i style="background:var(--state-security)"></i>security event</span>';
  }

  /* --------------------------------------------------------- interaction */
  function toWorld(event) {
    const rect = canvas.getBoundingClientRect();
    return {
      x: (event.clientX - rect.left - S.view.x) / S.view.scale,
      y: (event.clientY - rect.top - S.view.y) / S.view.scale,
    };
  }

  function nodeAt(point) {
    for (const node of S.nodes.values()) {
      if (!inFocus(node)) continue;
      if (Math.abs(point.x - node.x) <= node.w / 2 && Math.abs(point.y - node.y) <= node.h / 2) {
        return node;
      }
    }
    return null;
  }

  function edgeAt(point) {
    for (const edge of S.edges) {
      if (!edge._hit || !inFocus(edge.from) || !inFocus(edge.to)) continue;
      if (Math.hypot(point.x - edge._hit.x, point.y - edge._hit.y) < 14) return edge;
    }
    return null;
  }

  let dragging = null;
  canvas.addEventListener("mousedown", (event) => {
    dragging = { x: event.clientX, y: event.clientY, view: { ...S.view }, moved: false };
    canvas.classList.add("dragging");
  });
  window.addEventListener("mousemove", (event) => {
    if (!dragging) return;
    const dx = event.clientX - dragging.x;
    const dy = event.clientY - dragging.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) dragging.moved = true;
    S.view.x = dragging.view.x + dx;
    S.view.y = dragging.view.y + dy;
    draw();
  });
  window.addEventListener("mouseup", () => {
    canvas.classList.remove("dragging");
    dragging = null;
  });

  canvas.addEventListener("click", (event) => {
    if (dragging?.moved) return;
    const point = toWorld(event);
    const node = nodeAt(point);
    if (node) return selectComponent(node.id);       // section 14.32.1
    const edge = edgeAt(point);
    if (edge) return selectConnection(edge.id);       // section 14.32.3
  });

  canvas.addEventListener("dblclick", (event) => {
    const node = nodeAt(toWorld(event));
    if (!node) return;
    if (node.isSubsystem || node.childCount) {        // section 14.32.2
      if (S.collapsed.has(node.id)) S.collapsed.delete(node.id);
      else S.collapsed.add(node.id);
      layout(S.topology);
      fitToView();
    } else {
      S.focus = node.band;                            // focus the neighbourhood
      $("#dbg-focus").value = node.band;
      draw();
    }
  });

  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    const factor = event.deltaY < 0 ? 1.1 : 0.9;
    const rect = canvas.getBoundingClientRect();
    const cx = event.clientX - rect.left;
    const cy = event.clientY - rect.top;
    S.view.x = cx - (cx - S.view.x) * factor;
    S.view.y = cy - (cy - S.view.y) * factor;
    S.view.scale = Math.min(2.5, Math.max(0.35, S.view.scale * factor));
    draw();
  }, { passive: false });

  canvas.addEventListener("mousemove", (event) => {
    if (dragging) return;
    const point = toWorld(event);
    canvas.style.cursor = nodeAt(point) || edgeAt(point) ? "pointer" : "grab";
  });

  /* ---------------------------------------------------------- inspector */
  async function selectComponent(id, keepTab) {
    S.selection = { kind: "component", id };
    S.tab = keepTab && S.tab ? S.tab : "overview";
    highlightForComponent(id);
    S.detail = await api.get(`/api/debugger/component?id=${encodeURIComponent(id)}${windowParam()}`);
    renderInspector();
    draw();
  }

  async function selectConnection(id) {
    S.selection = { kind: "connection", id };
    S.tab = "overview";
    const edge = S.topology.connections.find((c) => c.id === id);
    S.highlight = {
      nodes: new Set(edge ? [edge.source, edge.target] : []),
      edges: new Set([id]),
    };
    S.detail = await api.get(`/api/debugger/connection?id=${encodeURIComponent(id)}${windowParam()}`);
    renderInspector();
    draw();
  }

  function windowParam() {
    return S.window ? `&minutes=${S.window}` : "";
  }

  function highlightForComponent(id) {
    const edges = S.topology.connections.filter((c) => c.source === id || c.target === id);
    S.highlight = {
      nodes: new Set([id, ...edges.map((e) => (e.source === id ? e.target : e.source))]),
      edges: new Set(edges.map((e) => e.id)),
    };
  }

  const TABS = {
    component: ["overview", "trace", "metrics", "tests", "errors"],
    connection: ["overview", "trace", "tests", "errors"],
  };

  function renderInspector() {
    const detail = S.detail;
    if (!detail) return;
    const overview = detail.overview;
    $("#dbg-sel-kind").textContent =
      detail.kind === "component"
        ? `${overview.component_type} · ${overview.parent_name || "system"}`
        : `${overview.connection_type}`;
    $("#dbg-sel-name").textContent =
      detail.kind === "component" ? overview.name
        : `${overview.source_name} → ${overview.target_name}`;

    $("#dbg-tabs").innerHTML = TABS[detail.kind]
      .map((tab) => {
        const count = tab === "tests" ? detail.tests.length
          : tab === "errors" ? detail.errors.length
          : tab === "trace" ? detail.traces.length : null;
        return `<button data-tab="${tab}" class="${S.tab === tab ? "active" : ""}">` +
          `${tab}${count ? ` (${count})` : ""}</button>`;
      })
      .join("");
    $("#dbg-tabs").querySelectorAll("button").forEach((button) =>
      button.addEventListener("click", () => {
        S.tab = button.dataset.tab;
        renderInspector();
      })
    );
    $("#dbg-inspector-body").innerHTML = renderTab(detail);
    wireInspector(detail);
  }

  function kv(pairs) {
    const rows = pairs
      .filter(([, value]) => value !== undefined && value !== null && value !== "")
      .map(([key, value]) => `<dt>${esc(key)}</dt><dd>${value}</dd>`)
      .join("");
    return `<dl class="dbg-kv">${rows}</dl>`;
  }

  function stateBadge(state) {
    return `<span class="badge" style="color:var(${STATE_COLORS[state] || "--state-idle"});` +
      `border-color:var(${STATE_COLORS[state] || "--state-idle"})">${esc(state)}</span>`;
  }

  function renderTab(detail) {
    const o = detail.overview;
    if (S.tab === "overview") {
      if (detail.kind === "component") {
        const status = o.status || {};
        return kv([
          ["Component", esc(o.name)], ["ID", `<code>${esc(o.id)}</code>`],
          ["Type", esc(o.component_type)], ["Subsystem", esc(o.parent_name || "—")],
          ["State", stateBadge(o.state)],
          ["Version", esc(o.version)], ["Uptime", `${o.uptime_seconds}s`],
          ["Platform", esc(o.platform_implementation)],
          ["Current task", esc(status.current_task)],
          ["Last error", status.last_error ? `<span style="color:var(--err)">
            ${esc(status.last_error)}</span>` : ""],
          ["Dependencies", (o.dependencies || []).map(esc).join(", ")],
          ["Children", (o.children || []).map((c) => esc(c.name)).join(", ")],
          ["Incoming", (o.incoming || []).map((c) => esc(c.source)).join(", ")],
          ["Outgoing", (o.outgoing || []).map((c) => esc(c.target)).join(", ")],
        ]) + `<pre>${esc(JSON.stringify(status, null, 1))}</pre>`;
      }
      return kv([
        ["Source", esc(o.source_name)], ["Destination", esc(o.target_name)],
        ["Type", esc(o.connection_type)], ["Interface", `<code>${esc(o.interface)}</code>`],
        ["Status", stateBadge(o.state)],
        ["Requests", o.request_count], ["Failures", o.failure_count],
        ["Average latency", o.average_latency_ms != null ? `${o.average_latency_ms} ms` : "—"],
        ["Last success", esc(o.last_success)], ["Last failure", esc(o.last_failure)],
        ["Description", esc(o.description)],
      ]);
    }
    if (S.tab === "trace") return renderSpans(detail.traces);
    if (S.tab === "metrics") {
      return `<pre>${esc(JSON.stringify(detail.metrics || {}, null, 1))}</pre>`;
    }
    if (S.tab === "tests") return renderTests(detail.tests);
    if (S.tab === "errors") {
      return detail.errors.length ? renderSpans(detail.errors)
        : '<p class="muted">No warnings, failures or security events recorded.</p>';
    }
    return "";
  }

  function renderSpans(spans) {
    if (!spans.length) return '<p class="muted">No trace events in this window.</p>';
    return spans
      .map((span) => {
        const time = new Date(span.timestamp).toLocaleTimeString([], { hour12: false }) +
          "." + String(new Date(span.timestamp).getMilliseconds()).padStart(3, "0");
        const cls = span.severity === "security" ? "security"
          : span.status === "error" || span.severity === "error" ? "error" : "";
        return `<div class="dbg-row ${cls}" data-trace="${esc(span.trace_id)}">
          <span class="t">${time}</span>
          <span class="op">${esc(span.operation)}${span.error ?
            ` — <span style="color:var(--err)">${esc(span.error)}</span>` : ""}</span>
          <span class="ms">${span.duration_ms != null ? `${span.duration_ms} ms` : ""}</span>
        </div>`;
      })
      .join("");
  }

  function renderTests(tests) {
    if (!tests.length) return '<p class="muted">No diagnostic tests are registered here.</p>';
    return tests
      .map(
        (test) => `<div class="dbg-test" data-test="${esc(test.id)}">
          <header>
            <strong>${esc(test.name)}</strong>
            <span class="risk ${esc(test.risk_level)}">${esc(test.risk_level)}</span>
            <button class="small" data-run="${esc(test.id)}">Run</button>
          </header>
          <p>${esc(test.description)}</p>
          ${test.modifies_state ? '<p class="muted">Changes persistent state.</p>' : ""}
          ${test.leaves_machine ? '<p class="muted">Sends a request off this machine.</p>' : ""}
          <div class="out" hidden></div>
        </div>`
      )
      .join("");
  }

  function wireInspector(detail) {
    $("#dbg-inspector-body").querySelectorAll("[data-run]").forEach((button) =>
      button.addEventListener("click", async () => {
        const card = button.closest(".dbg-test");
        const out = card.querySelector(".out");
        out.hidden = false;
        out.innerHTML = '<span class="muted">running…</span>';
        button.disabled = true;
        try {
          const result = await api.post("/api/debugger/tests/run",
                                        { test_id: button.dataset.run });
          const cls = { passed: "ok", warning: "warn", failed: "err" }[result.status] || "";
          out.innerHTML =
            `<span class="badge ${cls}">${esc(result.status)}</span> ` +
            `<span class="muted">${result.duration_ms} ms</span><br>${esc(result.summary)}` +
            (result.recommendations?.length
              ? `<ul>${result.recommendations.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>`
              : "") +
            (result.trace_id
              ? `<div><button class="small secondary" data-open-trace="${esc(result.trace_id)}">
                   Open trace</button></div>`
              : "") +
            `<details><summary class="muted">details</summary>
               <pre>${esc(JSON.stringify(result.details, null, 1))}</pre></details>`;
          out.querySelectorAll("[data-open-trace]").forEach((b) =>
            b.addEventListener("click", () => openTrace(b.dataset.openTrace))
          );
          loadTimeline();
        } catch (err) {
          out.innerHTML = `<span class="badge err">error</span> ${esc(err.message)}`;
        } finally {
          button.disabled = false;
        }
      })
    );
    // Section 14.32.5 / 14.32.6 - component or error to end-to-end trace.
    $("#dbg-inspector-body").querySelectorAll("[data-trace]").forEach((row) =>
      row.addEventListener("click", () => openTrace(row.dataset.trace))
    );
  }

  /* ----------------------------------------------------------- timeline */
  async function loadTimeline() {
    const params = new URLSearchParams();
    params.set("limit", "40");
    if (S.window) params.set("minutes", S.window);
    if (S.filter === "errors") params.set("severity", "error");
    const data = await api.get(`/api/debugger/timeline?${params}`);
    S.traces = data.traces.filter((t) =>
      !S.filter || S.filter === "errors" || t.kind === S.filter);
    $("#dbg-trace-count").textContent = `${S.traces.length} traces`;
    $("#dbg-trace-list").innerHTML = S.traces.length
      ? S.traces
          .map(
            (t) => `<div class="dbg-trace ${S.trace?.trace_id === t.trace_id ? "active" : ""}"
              data-trace="${esc(t.trace_id)}">
              <div><strong>${esc(t.root)}</strong>
                ${t.status === "error" ? '<span class="badge err">error</span>' : ""}
                <span class="muted">${t.duration_ms} ms</span></div>
              <div class="path">${new Date(t.started_at).toLocaleTimeString([], { hour12: false })}
                · ${esc(t.components.join(" → "))}</div>
            </div>`
          )
          .join("")
      : '<div class="dbg-empty">No traces in this window yet.</div>';
    $("#dbg-trace-list").querySelectorAll("[data-trace]").forEach((row) =>
      row.addEventListener("click", () => openTrace(row.dataset.trace))
    );
  }

  async function openTrace(traceId) {
    const trace = await api.get(`/api/debugger/trace?id=${encodeURIComponent(traceId)}`);
    S.trace = trace;
    // Section 14.32.4 - highlight the participating components and connections.
    S.highlight = {
      nodes: new Set(trace.components),
      edges: new Set(trace.connections),
    };
    $("#dbg-trace-detail").innerHTML =
      `<div class="muted">${esc(trace.trace_id)} · ${esc(trace.compact)}</div>` +
      trace.steps
        .map((step) => {
          const time = new Date(step.timestamp);
          const cls = step.severity === "security" ? "security"
            : step.status === "error" ? "error" : "";
          return `<div class="dbg-step ${cls}" data-component="${esc(step.component)}">
            <span class="when">${time.toLocaleTimeString([], { hour12: false })}.${
              String(time.getMilliseconds()).padStart(3, "0")}</span>
            <span>${esc(step.component)} · ${esc(step.operation)}
              ${step.error ? `<span style="color:var(--err)">${esc(step.error)}</span>` : ""}</span>
            <span class="gap">${step.duration_ms != null ? `${step.duration_ms} ms` : ""}${
              step.gap_ms ? ` (+${step.gap_ms})` : ""}</span>
          </div>`;
        })
        .join("");
    // Section 14.31.5 - a step navigates to its component.
    $("#dbg-trace-detail").querySelectorAll("[data-component]").forEach((step) =>
      step.addEventListener("click", () => selectComponent(step.dataset.component))
    );
    document.querySelectorAll(".dbg-trace").forEach((row) =>
      row.classList.toggle("active", row.dataset.trace === traceId)
    );
    draw();
  }

  /* -------------------------------------------------------------- chrome */
  $("#dbg-search").addEventListener("input", async (event) => {
    S.search = event.target.value.trim().toLowerCase();
    draw();
    if (S.search.length < 2) return;
    const found = await api.get(`/api/debugger/search?q=${encodeURIComponent(S.search)}`);
    const hits = found.components.length + found.connections.length + found.traces.length;
    $("#dbg-status").textContent = `${hits} match(es): ` +
      `${found.components.length} components, ${found.connections.length} connections, ` +
      `${found.traces.length} traces`;
    if (found.traces.length) {
      S.traces = found.traces;
      $("#dbg-trace-list").innerHTML = found.traces
        .map((t) => `<div class="dbg-trace" data-trace="${esc(t.trace_id)}">
          <div><strong>${esc(t.root)}</strong> <span class="muted">${t.duration_ms} ms</span></div>
          <div class="path">${esc(t.components.join(" → "))}</div></div>`)
        .join("");
      $("#dbg-trace-list").querySelectorAll("[data-trace]").forEach((row) =>
        row.addEventListener("click", () => openTrace(row.dataset.trace)));
    }
    // Section 14.31.7 - a single match selects the node, connection or trace.
    if (found.components.length === 1) selectComponent(found.components[0].id);
    else if (!found.components.length && found.connections.length === 1) {
      selectConnection(found.connections[0].id);
    } else if (!found.components.length && !found.connections.length &&
               found.traces.length === 1) {
      openTrace(found.traces[0].trace_id);
    }
  });

  $("#dbg-window").addEventListener("change", async (event) => {
    S.window = event.target.value;
    await loadTimeline();
    if (S.selection?.kind === "component") selectComponent(S.selection.id, true);
    if (S.selection?.kind === "connection") selectConnection(S.selection.id);
  });

  $("#dbg-focus").addEventListener("change", (event) => {
    S.focus = event.target.value;
    fitToView();
    // Section 14.33: focus narrows the panels too. It used to move only the
    // map, so with the panels below it the control read as doing nothing -
    // and "show me the knowledge subsystem" plainly means these as well.
    renderModules();
  });

  $("#dbg-trace-filter").addEventListener("change", (event) => {
    S.filter = event.target.value;
    loadTimeline();
  });

  $("#dbg-expand-all").addEventListener("click", () => {
    S.collapsed.clear();
    layout(S.topology);
    fitToView();
  });
  $("#dbg-collapse-all").addEventListener("click", () => {
    S.topology.subsystems.forEach((s) => S.collapsed.add(s.id));
    layout(S.topology);
    fitToView();
  });

  $("#dbg-export").addEventListener("click", async () => {
    try {
      const result = await api.post("/api/debugger/export",
                                    { trace_ids: S.trace ? [S.trace.trace_id] : null });
      toast(`Diagnostic report written to ${result.written}`);
    } catch (err) {
      toast(err.message, "error");
    }
  });

  $("#dbg-inspector-collapse").addEventListener("click", () => {
    const inspector = $("#dbg-inspector");
    inspector.classList.toggle("collapsed");
    $("#dbg-inspector-collapse").textContent =
      inspector.classList.contains("collapsed") ? "‹" : "›";
    draw();
  });
  $("#dbg-timeline-collapse").addEventListener("click", () => {
    const timeline = $("#dbg-timeline");
    timeline.classList.toggle("collapsed");
    $("#dbg-timeline-collapse").textContent =
      timeline.classList.contains("collapsed") ? "⌃" : "⌄";
    draw();
  });

  /* Resizable regions (section 14.31.10). */
  function drag(handle, onMove) {
    handle.addEventListener("mousedown", (event) => {
      event.preventDefault();
      const move = (e) => onMove(e);
      const up = () => {
        window.removeEventListener("mousemove", move);
        window.removeEventListener("mouseup", up);
        draw();
      };
      window.addEventListener("mousemove", move);
      window.addEventListener("mouseup", up);
    });
  }
  drag($("#dbg-split-v"), (event) => {
    const rect = $("#dbg-body").getBoundingClientRect();
    const width = Math.min(720, Math.max(240, rect.right - event.clientX));
    $("#dbg-inspector").style.width = `${width}px`;
    draw();
  });
  drag($("#dbg-split-h"), (event) => {
    const rect = $("#dbg-shell").getBoundingClientRect();
    const height = Math.min(rect.height - 220, Math.max(80, rect.bottom - event.clientY));
    $("#dbg-timeline").style.height = `${height}px`;
    draw();
  });

  window.addEventListener("resize", () => { if (S.topology) draw(); });

  /* ------------------------------------------------------------ refresh */
  let timer = null;
  window.debuggerPanel = {
    async activate() {
      await loadTopology();
      await loadTimeline();
      await loadModules();
      clearInterval(timer);
      timer = setInterval(async () => {
        if (!document.querySelector("#debugger.active")) return;
        await loadTopology();
        await loadTimeline();
        await loadModules();
      }, 10000);
    },
    deactivate() { clearInterval(timer); timer = null; },
  };
})();

