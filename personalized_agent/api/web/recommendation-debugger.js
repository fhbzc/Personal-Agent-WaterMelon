/* The recommendation debugger: what is kept ready, the words that set each off, when it was last
   looked for and why, and what matched (debugger/recommendation.py). Read-only. */
"use strict";
(() => {
  const $ = (s) => document.querySelector(s);
  if (!$("#recommenddebug")) return;
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const when = (v) => (v ? new Date(v).toLocaleString() : "—");
  const STATES = { live: "Ready", past: "Past its time", raw: "Waiting to be made ready",
    told: "Told", seen: "Already seen", dropped: "Dropped" };
  const KINDS = [["proper", "Proper names"], ["zh", "Chinese concepts"], ["en", "English concepts"]];
  const state = { data: null, request: 0 };

  function fact(label, value) {
    return `<div class="mon-fact"><span class="muted">${esc(label)}</span>
      <strong>${esc(value)}</strong></div>`;
  }

  function drawNow(data) {
    const on = (value) => (value ? "On" : "Off");
    const looks = data.looks || {};
    const scores = data.scores || {};
    const reasons = (looks.reasons || []).map((one) => `<li>${esc(one)}</li>`).join("");
    const signals = (looks.signals || []).map((one) => `<li>${esc(one)}</li>`).join("");
    $("#rec-now").innerHTML = `<div class="mon-facts">
      ${fact("Recommendations", on(data.switches.module))}
      ${fact("Monitor", on(data.switches.monitor))}
      ${fact("May search the web", on(data.switches.search))}
      ${fact("Last looked", when(looks.last))}
      ${fact("Next look", when(looks.next))}
      ${fact("Due now", looks.due ? "Yes" : "No")}
      ${fact("A name is worth", scores.proper)}
      ${fact("A concept is worth", scores.concept)}
      ${fact("A model is asked from", scores.middle)}
      ${fact("Sent without a model from", scores.high)}
      ${fact("Texts rarity was counted on", (data.rarity || {}).documents || 0)}
      ${fact("Rarity counted", when((data.rarity || {}).at))}
    </div>
    <h4>Why it last looked</h4>${reasons ? `<ul>${reasons}</ul>`
      : `<p class="muted">Nothing had changed: it was time for a look.</p>`}
    <h4>What would wake it now</h4>${signals ? `<ul>${signals}</ul>`
      : `<p class="muted">Nothing.</p>`}`;
  }

  function drawFilter(data) {
    const select = $("#rec-state");
    const chosen = select.value || "live";
    const counts = data.states || {};
    select.innerHTML = Object.keys(STATES).map((key) =>
      `<option value="${key}">${esc(STATES[key])} (${Number(counts[key] || 0)})</option>`).join("")
      + `<option value="all">Everything (${(data.items || []).length})</option>`;
    select.value = chosen;
  }

  function chip(word) {
    const weight = ((state.data.rarity || {}).weights || {})[String(word).toLowerCase()];
    return `<span class="rec-word">${esc(word)}${weight !== undefined && weight < 1
      ? ` <span class="muted">×${esc(weight)}</span>` : ""}</span>`;
  }

  function keywords(item) {
    const words = item.keywords || {};
    return KINDS.map(([kind, label]) => ((words[kind] || []).length
      ? `<div class="rec-words"><span class="muted">${esc(label)}</span>${words[kind]
        .map(chip).join("")}</div>` : "")).join("")
      || `<span class="muted">None yet</span>`;
  }

  function address(item) {
    const url = String(item.url || "");
    return /^https?:\/\//i.test(url)
      ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(item.title)}</a>`
      : esc(item.title);
  }

  function drawItems() {
    const data = state.data;
    if (!data) return;
    const chosen = $("#rec-state").value;
    const asked = $("#rec-find").value.trim().toLowerCase();
    const shown = (data.items || []).filter((one) => (chosen === "all" || one.state === chosen)
      && (!asked || JSON.stringify([one.title, one.keywords, one.for_work, one.angle])
        .toLowerCase().includes(asked)));
    $("#rec-items").innerHTML = shown.length ? `<table class="rec-table"><thead><tr>
        <th>What</th><th>Words that set it off</th><th>Why</th><th>For</th><th>Good until</th>
        <th>Matches</th></tr></thead><tbody>${shown.map((one) => `<tr data-item="${esc(one.id)}">
        <td>${address(one)}<div class="muted">${esc(STATES[one.state] || one.state)} · ${
          esc(one.source)}</div></td>
        <td>${keywords(one)}</td>
        <td>${esc(one.angle)}<div class="muted">${esc(one.why)}</div></td>
        <td>${esc(one.for_work) || "—"}</td>
        <td>${esc(when(one.expires_at))}</td>
        <td>${Number(one.matches || 0)}</td></tr>`).join("")}</tbody></table>`
      : `<p class="muted">Nothing here.</p>`;
  }

  function drawHits(data) {
    const hits = data.hits || [];
    $("#rec-hits").innerHTML = hits.length ? `<table class="rec-table"><thead><tr><th>When</th>
        <th>What</th><th>Words</th><th>Score</th><th>Read from</th><th>What was done</th></tr></thead>
        <tbody>${hits.map((one) => `<tr><td>${esc(when(one.at))}</td><td>${esc(one.title)}</td>
        <td>${(one.hit || []).map((word) => `<span class="rec-word">${esc(word)}</span>`).join("")}</td>
        <td>${esc(one.score)} (${esc(one.tier)})</td><td>${esc(one.seen_by)}</td>
        <td>${esc(one.done)}</td></tr>`).join("")}</tbody></table>`
      : `<p class="muted">Nothing has matched yet.</p>`;
  }

  function drawLearned(data) {
    const learned = data.learned || {};
    const told = learned.predictions || {};
    const reasons = learned.reasons || [];
    const changed = learned.changed || [];
    if (learned.error) {
      $("#rec-learned").innerHTML = `<p class="muted">${esc(learned.error)}</p>`;
      return;
    }
    $("#rec-learned").innerHTML = `<p class="muted">${esc(
      `Answers learned from: ${Number(learned.judgments || 0)}`)}</p>
      <p>${told.count ? esc(`Answers guessed before they were learned from: ${told.count}. Right with the reasons: ${
        told.with_reasons}. Right without: ${told.without}.`) : `<span class="muted">Nothing guessed yet.</span>`}</p>
      ${reasons.length ? `<table class="rec-table"><thead><tr><th>Reason</th><th>Kind</th><th>State</th></tr></thead>
        <tbody>${reasons.map((one) => `<tr><td>${esc(one.statement)}</td><td>${esc(one.aspect)}</td><td>${
          esc(one.state)}</td></tr>`).join("")}</tbody></table>`
        : `<p class="muted">Nothing learned from them yet.</p>`}
      ${changed.length ? `<p>Reasons of your work changed by an answer</p>
        <table class="rec-table"><thead><tr><th>Date</th><th>Was</th><th>Now</th><th>Why</th><th>Answers</th></tr></thead>
        <tbody>${changed.map((one) => `<tr><td>${esc(one.at)}</td><td>${esc(one.was)}</td><td>${esc(one.now)}</td><td>${
          esc(one.why)}</td><td>${esc((one.answers || []).join(", "))}</td></tr>`).join("")}</tbody></table>` : ""}`;
  }

  async function load() {
    const request = ++state.request;
    try {
      const data = await api.get("/api/debugger/recommendation");
      if (request !== state.request) return;
      state.data = data;
      drawNow(data);
      drawFilter(data);
      drawItems();
      drawHits(data);
      drawLearned(data);
    } catch (error) {
      $("#rec-now").innerHTML = `<p class="muted">${esc(error.message)}</p>`;
    }
  }

  $("#rec-state").addEventListener("change", drawItems);
  $("#rec-find").addEventListener("input", drawItems);
  $("#rec-read").addEventListener("click", load);
  window.recommendationDebugger = { load };
})();
