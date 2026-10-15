/* THE CHAT DEBUGGER'S TAB: the scenarios, the runs, and one run read whole.
   Ported from PR #4 (by daniela-miaut).

   Everything comes from /api/debugger/chat; nothing here reaches into the
   agent. A run is a list of events in the order they happened - what the
   model was sent, which model the manager resolved to, the exact payload the
   client was handed, what came back, every tool call and result - each one
   folded, because a whole prompt is a page and most of the time one event
   is the question. */
"use strict";

(() => {
  const $ = (selector) => document.querySelector(selector);
  if (!$("#chatdebug")) return;
  const state = { active: false, busy: false, polling: false, manuallySelected: false,
    selectionRequest: 0, timer: null, tests: [], runs: [], selected: null,
    customConversationId: "" };
  const esc = (value) => escapeHtml(String(value ?? ""));
  const statusClass = (status) => ({ passed: "ok", failed: "err", error: "err",
    running: "warn", completed: "ok" }[status] || "warn");

  function renderTests() {
    const host = $("#chat-debugger-tests");
    if (!state.tests.length) {
      host.innerHTML = '<p class="muted">No scenarios.</p>';
      return;
    }
    host.innerHTML = state.tests.map((test) => `<div class="chat-debugger-test">
      <div><strong>${esc(test.name || test.id)}</strong><p class="muted">${esc(test.description || "")}</p></div>
      <button type="button" class="small secondary" data-chat-test="${esc(test.id)}" ${state.busy ? "disabled" : ""}>Run</button>
    </div>`).join("");
    host.querySelectorAll("[data-chat-test]").forEach((button) =>
      button.addEventListener("click", () => runTest(button.dataset.chatTest)));
  }

  function renderControls() {
    $("#chat-debugger-run-suite").disabled = state.busy;
    $("#chat-debugger-run-custom").disabled = state.busy;
    $("#chat-debugger-prompt").disabled = state.busy;
    $("#chat-debugger-continue").disabled = state.busy || !state.customConversationId;
  }

  function renderRuns() {
    const host = $("#chat-debugger-runs");
    if (!state.runs.length) {
      host.innerHTML = '<p class="muted">No runs yet.</p>';
      return;
    }
    host.innerHTML = state.runs.map((run) => `<button type="button" class="chat-debugger-run ${
      state.selected?.id === run.id ? "selected" : ""}" data-chat-run="${esc(run.id)}">
      <span><strong>${esc(run.name || run.test_id || "Your own prompt")}</strong>${
        run.prompt ? `<span class="chat-debugger-run-prompt">${esc(run.prompt)}</span>` : ""}<small>${
        esc(run.started_at || "")}</small></span>
      <span class="badge ${statusClass(run.status)}">${esc(run.status || "unknown")}</span>
    </button>`).join("");
    host.querySelectorAll("[data-chat-run]").forEach((button) =>
      button.addEventListener("click", () => loadRun(button.dataset.chatRun, true)));
  }

  function renderDetail(run) {
    const host = $("#chat-debugger-detail");
    // Drawn again while a run is going: what the reader had open stays open.
    const sameRun = state.selected?.id === run.id;
    const openEvents = new Set(Array.from(sameRun
      ? host.querySelectorAll("details[data-event-seq][open]") : [])
      .map((element) => element.dataset.eventSeq));
    state.selected = run;
    $("#chat-debugger-detail-title").textContent = run.name || run.test_id || "Your own prompt";
    $("#chat-debugger-detail-status").innerHTML =
      `<span class="badge ${statusClass(run.status)}">${esc(run.status || "unknown")}</span>`;
    const checks = (run.checks || []).map((check) => `<li class="${check.passed ? "passed" : "failed"}">
      <strong>${check.passed ? "✓" : "×"} ${esc(check.name)}</strong>${
        check.detail ? `<div class="muted">${esc(check.detail)}</div>` : ""}</li>`).join("");
    const events = (run.events || []).slice()
      .sort((a, b) => (a.sequence ?? 0) - (b.sequence ?? 0))
      .map((event, index) => {
        const sequence = String(event.sequence ?? index + 1);
        return `<details class="chat-debugger-event" data-event-seq="${esc(sequence)}" ${
          openEvents.has(sequence) ? "open" : ""}><summary><strong>${esc(sequence)}. ${
          esc(event.kind || "event")}</strong><span class="muted">${esc(event.time || "")}</span></summary>
          <pre>${esc(JSON.stringify(event.data ?? {}, null, 2))}</pre></details>`;
      }).join("");
    const result = run.result == null ? "" : `<details class="chat-debugger-event" data-event-seq="result" ${
      openEvents.has("result") ? "open" : ""}><summary><strong>Result</strong></summary><pre>${
      esc(JSON.stringify(run.result, null, 2))}</pre></details>`;
    const truncated = run.capture_truncated
      ? '<p class="chat-debugger-truncated">The transcript reached its limit; later events were not kept.</p>'
      : "";
    host.innerHTML = `
      <div class="chat-debugger-run-meta"><span class="muted">Run ${esc(run.id || "")}</span>
        <span class="muted">${esc(run.duration_seconds ?? "")}s</span></div>
      ${run.prompt ? `<h4>Prompt</h4><pre class="chat-debugger-prompt-content">${esc(run.prompt)}</pre>` : ""}
      ${run.error ? `<p class="chat-debugger-error">${esc(run.error)}</p>` : ""}
      ${truncated}
      ${checks ? `<h4>Checks</h4><ul class="chat-debugger-checks">${checks}</ul>` : ""}
      <h4>Events <span class="muted">(${(run.events || []).length})</span></h4>
      ${events || '<p class="muted">This run recorded no events.</p>'}${result}`;
    renderRuns();
  }

  async function loadHistory() {
    if (state.polling) return;
    state.polling = true;
    try {
      const payload = await api.get("/api/debugger/chat");
      state.tests = payload.tests || [];
      state.runs = payload.runs || [];
      if (!state.customConversationId) {
        const lastOwn = state.runs.find((run) => run.test_id === "custom" && run.conversation_id);
        state.customConversationId = lastOwn?.conversation_id || "";
      }
      if (state.selected && !state.runs.some((run) => run.id === state.selected.id)) state.selected = null;
      renderControls();
      renderTests();
      renderRuns();
      $("#chat-debugger-test-status").textContent = `${state.tests.length} scenarios`;
    } catch (error) {
      $("#chat-debugger-test-status").textContent = error.message;
    } finally {
      state.polling = false;
    }
  }

  async function loadRun(id, userSelected = false) {
    if (userSelected) {
      state.manuallySelected = true;
      state.selectionRequest += 1;
    }
    const selectionRequest = state.selectionRequest;
    try {
      const run = await api.get(`/api/debugger/chat/run?id=${encodeURIComponent(id)}`);
      if (selectionRequest !== state.selectionRequest) return;
      if (userSelected || !state.manuallySelected || state.selected?.id === id) renderDetail(run);
    } catch (error) {
      $("#chat-debugger-detail").innerHTML = `<p class="chat-debugger-error">${esc(error.message)}</p>`;
    }
  }

  async function submitRun(payload, label) {
    if (state.busy) return;
    state.busy = true;
    state.manuallySelected = false;
    $("#chat-debugger-run-status").textContent = `Running ${label}…`;
    renderControls();
    renderTests();
    try {
      let run = await api.post("/api/debugger/chat/run", payload);
      if (payload.message && (run.conversation_id || run.result?.conversation_id)) {
        state.customConversationId = run.conversation_id || run.result.conversation_id;
      }
      if (!state.manuallySelected) renderDetail(run);
      $("#chat-debugger-run-status").textContent = `${label}: ${run.status}`;
      await loadHistory();
      if (run.id) {
        try {
          run = await api.get(`/api/debugger/chat/run?id=${encodeURIComponent(run.id)}`);
        } catch (_error) { /* the run already returned is whole */ }
      }
      if (!state.manuallySelected) renderDetail(run);
    } catch (error) {
      $("#chat-debugger-run-status").textContent = error.message;
    } finally {
      state.busy = false;
      renderControls();
      renderTests();
    }
  }

  function runTest(testId) {
    const test = state.tests.find((item) => item.id === testId);
    return submitRun({ test_id: testId }, test?.name || testId);
  }

  async function runSuite() {
    if (state.busy) return;
    state.busy = true;
    state.manuallySelected = false;
    renderControls();
    renderTests();
    const tests = state.tests.slice();
    const totals = { passed: 0, failed: 0, other: 0 };
    for (let index = 0; index < tests.length; index += 1) {
      const test = tests[index];
      $("#chat-debugger-run-status").textContent =
        `Running ${index + 1} of ${tests.length}: ${test.name || test.id}`;
      try {
        const run = await api.post("/api/debugger/chat/run", { test_id: test.id });
        if (run.status === "passed") totals.passed += 1;
        else if (["failed", "error"].includes(run.status)) totals.failed += 1;
        else totals.other += 1;
        if (!state.manuallySelected) renderDetail(run);
        await loadHistory();
        if (!state.manuallySelected) renderDetail(run);
      } catch (error) {
        totals.failed += 1;
        $("#chat-debugger-run-status").textContent = `${test.name || test.id}: ${error.message}`;
      }
    }
    state.busy = false;
    renderControls();
    renderTests();
    $("#chat-debugger-run-status").textContent = `Ran ${tests.length}: ${totals.passed} passed, ${
      totals.failed} failed${totals.other ? `, ${totals.other} other` : ""}`;
  }

  $("#chat-debugger-run-suite").addEventListener("click", runSuite);
  $("#chat-debugger-run-custom").addEventListener("click", () => {
    const message = $("#chat-debugger-prompt").value.trim();
    if (!message) { $("#chat-debugger-run-status").textContent = "Write a prompt first."; return; }
    const payload = { message };
    if ($("#chat-debugger-continue").checked && state.customConversationId) {
      payload.conversation_id = state.customConversationId;
    }
    submitRun(payload, "your prompt");
  });

  window.chatDebuggerPanel = {
    /* Opened: the list, the newest run, and - while a run is going - its
       events as they arrive. */
    async activate() {
      state.active = true;
      renderControls();
      await loadHistory();
      if (!state.active) return;
      if (!state.selected && state.runs.length) await loadRun(state.runs[0].id);
      if (!state.active || state.timer) return;
      state.timer = setInterval(async () => {
        if (!state.active || document.hidden) return;
        // Only while something is running: an idle list is fetched again
        // with the rest of the page, every fifteen seconds.
        if (!state.busy && state.selected?.status !== "running") return;
        await loadHistory();
        if (state.busy && !state.manuallySelected) {
          const running = state.runs.find((run) => run.status === "running");
          if (running && running.id !== state.selected?.id) await loadRun(running.id);
        }
        if (state.selected?.status === "running" && state.selected.id) await loadRun(state.selected.id);
      }, 1000);
    },
    /* Left: nothing polls a tab nobody is looking at. */
    deactivate() {
      state.active = false;
      if (state.timer) { clearInterval(state.timer); state.timer = null; }
    },
  };
})();
