/* Read-only inspection of the production Listener. Never starts collection. */
"use strict";
(() => {
  const $ = (s) => document.querySelector(s);
  if (!$("#listenerdebug")) return;
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, c =>
    ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[c]));
  const state = { active: false, timer: null, request: 0, conversation: null };
  const time = (v) => v ? new Date(v).toLocaleString() : "none yet";
  const names = { application: "Application in front", browser: "Browser visits", screen: "Screen text",
    screenshot: "Pictures", filesystem: "Files", assistant: "Assistant", app_integration: "Application integration" };
  const appName = (v) => /^(wechat|weixin|微信)$/i.test(v || "") ? "WeChat" : v;
  const methods = { macos_accessibility: "Accessibility", macos_vision_ocr: "Apple Vision" };
  const layoutReasons = {
    chat_boundary_not_found: "the chat's columns were not found",
    header_or_input_boundary_not_found: "the chat's title or the box to type in was not found",
    chat_header_not_readable: "the chat's title could not be read",
    no_readable_chat_bubbles: "no message bubble could be read",
    non_chat_page: "not a chat",
    ocr_failed_or_unavailable: "the text recognition failed",
    layout_parse_failed: "the layout could not be read" };
  const layoutReason = v => layoutReasons[v] || v || "no reason recorded";
  function readingStatus(a) {
    const r = a.reading;
    if (!r) return "";
    if (["failed", "degraded"].includes(r.status)) return `<div class="ld-error"><b>${esc(appName(a.application))}: its text could not be read</b>${r.status === "degraded" ? " (Accessibility pieces only)" : ""}<div>${esc(time(r.last_failure_at))} · ${esc(r.last_error)}</div></div>`;
    if (r.wechat_layout?.status === "unrecognized") return `<div class="ld-error"><b>WeChat: its messages could not be told apart</b><div>${esc(layoutReason(r.wechat_layout.reason))}</div><span class="muted">Recognition running does not mean the chat was sorted right.</span></div>`;
    const status = r.status === "ok" ? "last read well" : r.status === "blocked" ? "a protected field, or a safety check not done: not recognised" : r.status === "skipped" ? "the window or the privacy settings changed while it was read: dropped" : "reading";
    return `<div>${esc(status)} · ${esc(time(r.last_success_at || r.last_attempt_at))}</div>${r.method ? `<div class="muted">${esc(methods[r.method] || r.method)}</div>` : ""}`;
  }
  const labels = { running: "running", degraded: "to check", failed: "failed", stopped: "stopped",
    disabled_by_settings: "off in Settings", registered: "not started", unknown: "unknown" };
  const reasons = { directory_excluded: "a folder kept out", application_excluded: "an application kept out",
    website_excluded: "a website kept out", agent_bookkeeping: "the agent's own records", file_pattern_excluded: "a file kept out",
    monitoring_or_category_disabled: "the listener, or this kind, is off", presence_or_user_activity_policy: "nobody at the computer",
    untrusted_or_invalid_screenshot: "a picture not from the agent's own capture" };
  const badge = (s) => `<span class="ld-badge ${s === "running" ? "ok" : ["failed", "degraded"].includes(s) ? "warn" : ""}">${esc(labels[s] || s)}</span>`;

  function choices(selector, values, all) {
    const select = $(selector), selected = select.value;
    if (selected && !values.includes(selected)) values.push(selected);
    const items = ["", ...values];
    if (JSON.stringify([...select.options].map(o => o.value)) === JSON.stringify(items)) return;
    select.innerHTML = `<option value="">${all}</option>` + values.map(v =>
      `<option value="${esc(v)}">${esc(names[v] || v)}</option>`).join("");
    select.value = selected;
  }
  function update(host, markup) {
    const open = new Set([...host.querySelectorAll("details[open][data-key]")].map(el => el.dataset.key));
    if (host.dataset.markup === markup) return;
    const scroll = new Map([...host.querySelectorAll("pre[data-scroll]")].map(el => [el.dataset.scroll, el.scrollTop]));
    host.innerHTML = markup;
    host.dataset.markup = markup;
    host.querySelectorAll("details[data-key]").forEach(el => { el.open = open.has(el.dataset.key); });
    host.querySelectorAll("pre[data-scroll]").forEach(el => { if (scroll.has(el.dataset.scroll)) el.scrollTop = scroll.get(el.dataset.scroll); });
  }
  /* FAST SAMPLING, said: a chat, mail or social window in front is looked at
     every second and read only when it changed (listener/providers/
     fast_reading.py). */
  function fastSaid(s) {
    const fast = s.fast;
    if (!fast) return { head: `Every ${esc(s.screen_interval_seconds)} s`, more: "" };
    return {
      head: `Chats, mail, social media: every ${esc(fast.fastest_seconds)} s`,
      more: `looked at that often, read only when the window changed; ${[...fast.slower].reverse().map(([after, every]) => `every ${esc(every)} s after ${esc(after)} s the same`).join(", ")}; within ${esc(fast.cpu_budget_percent)}% of one core${fast.on_battery ? `; on battery, every ${esc(fast.battery_floor_seconds)} s at most` : ""}. ${fast.in_front ? `In front now${fast.over_budget ? " - over its budget, slowed" : ""}.` : "Not in front now."} Other windows: every ${esc(s.screen_interval_seconds)} s. `,
    };
  }
  function render(data) {
    if (!data.monitoring) {
      $("#ld-status").textContent = "Debugger monitoring is off. Turn it on in Settings to see this; nothing here turns any listening on.";
      ["summary", "issues", "providers", "apps", "events", "wechat", "wechat-detail"].forEach(id => update($("#ld-" + id), ""));
      state.conversation = null;
      return;
    }
    const w = data.window, s = data.settings;
    const pace = fastSaid(s);
    $("#ld-status").textContent = `Updated ${time(data.generated_at)} · the last ${w.hours} hours · ${w.sample_events} events counted${w.sample_limited ? ` (the most counted, ${w.sample_limit}: the counts may not cover the whole time, the earliest is ${time(w.sample_since)})` : ""}. Whether what was read is whole has not been checked against the screen.`;
    choices("#ld-application", data.applications.map(a => a.application).sort(), "All applications");
    choices("#ld-source", [...new Set([...data.providers.map(p => p.id), ...data.events.map(e => e.source)])].sort(), "All sources");
    update($("#ld-summary"), `<div class="card"><div class="ld-summary">
      <div class="ld-stat">Listening<strong>${data.running && s.enabled ? "on" : "stopped or off"}</strong><span class="muted">Screen text: ${s.screen_enabled ? "on" : "off"}</span></div>
      <div class="ld-stat">Reading the screen<strong>${pace.head}</strong><span class="muted">${pace.more}${s.wechat_immediate_write ? "WeChat's new messages are stored as soon as they are read. " : ""}${s.focus_check_seconds ? `The window in front is checked every ${esc(s.focus_check_seconds)} s. ` : ""}At most ${esc(s.screen_max_chars)} characters a record, repeats and the rest cut.</span></div>
      <div class="ld-stat">Pictures<strong>${s.screenshots_enabled ? "on" : "off"}</strong><span class="muted">At most ${esc(s.screenshots_per_day)} a day, taken by how long a window stays and how still it is.</span></div>
    </div><p class="muted">What is read is what is visible in front. Windows behind, messages scrolled out of sight, comments not opened, video and voice are not covered.</p></div>`);
    update($("#ld-issues"), data.issues.map(issue => {
      const message = issue.kind === "screen_read_failed"
        ? `${appName(issue.application)}: its text could not be read${issue.partial ? " (Accessibility pieces only)" : ""} · ${time(issue.at)}: ${issue.message}`
        : issue.kind === "wechat_layout_unrecognized" ? `WeChat: its messages could not be told apart: ${layoutReason(issue.message)}. The text is kept as evidence of no one in particular; who wrote it is not said.`
        : issue.kind === "screenshot_not_persisted"
        ? `Pictures: ${issue.taken} taken in this run, none stored.`
        : issue.kind === "screenshot_path_excluded" ? "The folder pictures are saved in is a folder kept out: their events may not be stored."
        : `${names[issue.source] || issue.source} reported an error: ${issue.message}`;
      return `<div class="ld-issue">${esc(message)}</div>`;
    }).join(""));
    update($("#ld-providers"), data.providers.map(p => {
      const r = p.routing, status = p.status || {};
      const metrics = Object.entries(status).filter(([,v]) => typeof v === "number");
      const metricLabels = {readings: "reads", recorded: "text events made", unchanged: "nothing new", gathered: "reads with something new", skipped: "skipped", repeated_lines_dropped: "repeated lines dropped", furniture_lines_dropped: "interface text dropped", average_milliseconds: "average read, ms", effective_interval_seconds: "interval now, s", focus_check_seconds: "window in front checked every, s", taken: "pictures taken", today: "pictures today", held_back_for_credentials: "held back for passwords", emitted: "events made", polls: "polls"};
      return `<div class="ld-provider"><div class="ld-provider-head"><strong>${esc(names[p.id] || p.id)}</strong>${badge(p.state)}</div>
        <div class="ld-metrics"><span>stored in the time range <b>${p.stored.events}</b></span><span>stored in this run <b>${p.session_recorded}</b></span>${p.id === "screen" && status.reader ? `<span>recognitions run <b>${esc(status.reader.ocr_executions ?? 0)}</b></span><span>pictures recognised before, reused <b>${esc(status.reader.ocr_image_cache_hits ?? 0)}</b></span>` : ""}${p.id === "screen" && status.fast ? `<span>looks <b>${esc(status.fast.looks)}</b></span><span>looks with no change <b>${esc(status.fast.looks_unchanged)}</b></span><span>reads held by the budget <b>${esc(status.fast.held_by_budget)}</b></span>` : ""}</div>
        <p class="muted">Last stored: ${esc(time(p.stored.last_event))}</p>
        ${r ? `<div class="ld-metrics"><span>arrived ${r.received}</span><span>kept out ${r.filtered}</span><span>failed ${r.failed}</span></div><p class="muted">Last arrived: ${esc(time(r.last_received_at))}</p>${Object.entries(r.filter_reasons || {}).map(([k,v]) => `<div class="muted">${esc(reasons[k] || k)}: ${v}</div>`).join("")}` : '<p class="muted">No count of what was kept out in this run.</p>'}
        ${p.errors.map(e => `<p class="ld-error">${esc(e)}</p>`).join("")}
        <details data-key="provider-${esc(p.id)}"><summary>Reads, repeats and its own state</summary><div class="ld-metrics">${metrics.map(([k,v]) => `<span>${esc(metricLabels[k] || k)}: ${esc(v)}</span>`).join("")}</div><pre data-scroll="provider-${esc(p.id)}">${esc(JSON.stringify(status, null, 2))}</pre></details></div>`;
    }).join(""));
    const apps = data.applications.filter(a => !$("#ld-application").value || a.application === $("#ld-application").value);
    const repair = data.wechat?.association_repair || {};
    const repairInfo = (repair.aliased_messages || repair.uncertain_associations) ? `<p class="${repair.uncertain_associations ? "ld-error" : "muted"}">Message numbers seen twice before, joined: ${esc(repair.aliased_messages || 0)}; links still to check: ${esc(repair.uncertain_associations || 0)}. The old numbers and what they rest on stay in the conversation's JSON.</p>` : "";
    const chats = (data.wechat?.conversations || []).map(c => `<p><button type="button" class="secondary small" data-ld-conversation="${esc(c.id)}">Conversation ${esc(c.id.slice(-8))}</button> · ${esc(c.messages)} messages (mine ${esc(c.self_messages ?? "?")} / theirs ${esc(c.other_messages ?? "?")} / the rest unknown) · ${esc(c.segments ?? 0)} stretches seen · ${esc(time(c.last_seen_at))}</p>`).join("");
    const observations = (data.wechat?.diagnostics || []).map(o => `<tr><td>${esc(time(o.last_seen_at))}</td><td>${o.conversation_id ? "in a conversation" : "in none"}</td><td>${esc(o.layout_status === "recognized" ? "messages told apart" : layoutReason(o.layout_reason))}</td><td>${esc(o.diagnostics?.assigned_messages ?? 0)} / ${esc(o.diagnostics?.unknown_senders ?? 0)}</td></tr>`).join("");
    update($("#ld-wechat"), repairInfo + '<p class="muted">By the text recognised on this computer and the layout alone: sorted into conversations with no names and into stretches seen, no contact identified. A frame not recognised makes no conversation; older records are kept.</p>' + (chats || '<p class="muted">No structured conversation with messages yet.</p>') + (observations ? `<details data-key="wechat-observations"><summary>The latest reads</summary><table class="ld-table"><thead><tr><th>Last seen</th><th>Conversation</th><th>Layout</th><th>Messages / writer unknown</th></tr></thead><tbody>${observations}</tbody></table></details>` : ""));
    update($("#ld-apps"), `<table class="ld-table"><thead><tr><th>Application</th><th>Times in front</th><th>Pieces of text / characters</th><th>Last text</th><th>Read by</th><th>Reading</th><th>To check</th></tr></thead><tbody>${apps.map(a => `<tr><td><button class="ld-filter-link" data-ld-app="${esc(a.application)}">${esc(a.application)}</button></td><td>${a.focus_events}</td><td>${a.text_events} / ${a.characters}</td><td>${esc(time(a.last_text))}</td><td>${esc(Object.entries(a.methods).map(([k,v]) => `${k} (${v})`).join(", ") || "no text yet")}</td><td>${readingStatus(a) || "no read in this run"}</td><td>${a.long_focus_without_new_text ? `${a.long_focus_without_new_text} stretches in front of a minute or more with no new text.` : ""}${a.at_character_limit ? ` ${a.at_character_limit} at the character limit.` : ""}<div class="muted">${a.text_events ? "text kept, not checked to be whole" : "no text in this time range"}</div></td></tr>`).join("")}</tbody></table>${!apps.length ? '<p class="muted">No application in the time range chosen.</p>' : ""}`);
    update($("#ld-events"), data.events.map(e => `<details class="ld-event" data-key="${esc(e.id)}"><summary><strong>${esc(e.application || "unknown application")} · ${esc(e.event_type)}</strong><span class="muted">${esc(time(e.timestamp))}</span></summary><p class="muted">From ${esc(e.source)} · record ${esc(e.id)} · linked to ${e.linked_states} states${e.preview_truncated ? " · preview cut" : ""}</p><pre data-scroll="${esc(e.id)}">${esc(e.data_preview)}</pre></details>`).join("") || '<p class="muted">Nothing stored for what is chosen. Nothing stored does not mean nothing happened.</p>');
  }
  function schedule() {
    clearTimeout(state.timer);
    if (state.active && !document.hidden && $("#ld-auto").checked) state.timer = setTimeout(load, 10000);
  }
  async function load() {
    clearTimeout(state.timer);
    if (!state.active || document.hidden) return;
    const request = ++state.request;
    const params = new URLSearchParams({hours:$("#ld-hours").value, application:$("#ld-application").value, source:$("#ld-source").value});
    $("#ld-refresh").disabled = true;
    try {
      const data = await api.get("/api/debugger/listener?" + params);
      if (state.active && request === state.request) render(data);
    } catch (error) {
      if (state.active && request === state.request) $("#ld-status").textContent = `Could not be read: ${error.message}. What is shown may be out of date - try again.`;
    } finally {
      if (request === state.request) { $("#ld-refresh").disabled = false; schedule(); }
    }
  }
  ["hours", "application", "source"].forEach(id => $("#ld-" + id).addEventListener("change", load));
  $("#ld-auto").addEventListener("change", schedule);
  $("#ld-refresh").addEventListener("click", load);
  $("#ld-apps").addEventListener("click", event => {
    const button = event.target.closest("[data-ld-app]");
    if (button) { $("#ld-application").value = button.dataset.ldApp; load(); }
  });
  function showConversation(data) {
    state.conversation = data;
    $("#ld-wechat-detail").innerHTML = '<p class="muted">What is loaded: a message keeps its number; corrections are in revision / revisions, stretches in segments. When it was seen is not when it was sent, and it has not been checked to be whole.</p><button type="button" class="secondary small" data-ld-download>Download what is loaded (JSON)</button> <button type="button" class="secondary small" data-ld-older>Load earlier messages</button><p role="status" data-ld-conversation-status></p><pre data-ld-json></pre>';
    $("[data-ld-json]").textContent = JSON.stringify(data, null, 2);
    $("[data-ld-older]").hidden = !data.pagination.has_more;
  }
  $("#ld-wechat").addEventListener("click", async event => {
    const button = event.target.closest("[data-ld-conversation]");
    if (!button) return;
    button.disabled = true;
    try {
      const data = await api.get('/api/debugger/listener/wechat/conversation?' + new URLSearchParams({id:button.dataset.ldConversation}));
      if (state.active) showConversation(data);
    } catch (error) { $("#ld-wechat-detail").textContent = `Could not be read: ${error.message}`; }
    finally { button.disabled = false; }
  });
  $("#ld-wechat-detail").addEventListener("click", async event => {
    const data = state.conversation;
    if (!data) return;
    if (event.target.closest('[data-ld-download]')) {
      const url = URL.createObjectURL(new Blob([JSON.stringify(data,null,2)], {type:'application/json'}));
      const link = document.createElement('a'); link.href=url; link.download=data.conversation.conversation_id+'.json'; link.click();
      setTimeout(() => URL.revokeObjectURL(url),1000);
    }
    const button = event.target.closest('[data-ld-older]');
    if (!button || !data.pagination.has_more) return;
    button.disabled = true;
    try {
      const older = await api.get('/api/debugger/listener/wechat/conversation?' + new URLSearchParams({id:data.conversation.conversation_id,before:data.pagination.next_before}));
      if (state.conversation !== data || !state.active) return;
      const captures = new Map([...older.captures,...data.captures].map(c => [c.capture_id,c]));
      showConversation({...data,messages:[...older.messages,...data.messages],captures:[...captures.values()],pagination:older.pagination});
    } catch (error) { const host=$('[data-ld-conversation-status]'); if(host) host.textContent=`Could not be read: ${error.message}`; }
    finally { button.disabled = false; }
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) { clearTimeout(state.timer); state.request++; $("#ld-refresh").disabled = false; }
    else if (state.active) load();
  });
  window.listenerDebuggerPanel = {
    activate: async () => { if (state.active) return; state.active = true; await load(); },
    deactivate: () => { state.active = false; state.request++; clearTimeout(state.timer); $("#ld-refresh").disabled = false; }
  };
})();
