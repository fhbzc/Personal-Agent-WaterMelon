/* THE WAITING NOTIFICATIONS, A PAGE OF THEIR OWN (the user, 2026-11-14: "精简页面是可以的"). The side panel used
   to load the whole application - 1.5 MB of script, and a dozen requests the panel never needs (the agenda,
   tokens, the debugger) queued ahead of its list: measured on a copy of the user's data, the list came
   7.5 s after opening while the agent was busy, 31 ms when asked alone. This page asks for the list, and
   draws it. Its words are the application's, translated by i18n.js as the rest are. */

function escapeHtml(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

const $ = (selector, scope = document) => scope.querySelector(selector);

/* A moment the agent wrote down, in UTC, in this computer's time zone (as app.js's localStamp). */
function localStamp(value) {
  const text = String(value || "");
  const zoned = /T\d\d:\d\d.*(Z|[+-]\d\d:?\d\d)$/.test(text);
  const when = zoned ? new Date(text) : null;
  if (!when || Number.isNaN(when.getTime())) return text.slice(0, 16).replace("T", " ");
  const pad = (n) => String(n).padStart(2, "0");
  return `${when.getFullYear()}-${pad(when.getMonth() + 1)}-${pad(when.getDate())} ${pad(when.getHours())}:${pad(when.getMinutes())}`;
}

function toast(message, kind = "info") {
  const item = document.createElement("div");
  item.className = "toast-item";
  item.textContent = message;
  if (kind === "error") item.style.borderLeftColor = "var(--err)";
  $("#toast").appendChild(item);
  setTimeout(() => item.remove(), 6000);
}

/* Not signed in (opened by hand, or its sign-in gone): said once, at the top - as app.js says it. */
let signInNoticeShown = false;
function signInNeeded(response) {
  if (response.status !== 401 || signInNoticeShown) return;
  signInNoticeShown = true;
  const notice = document.createElement("div");
  notice.setAttribute("role", "alert");
  notice.style.cssText = "position:fixed;left:0;right:0;top:0;z-index:10000;padding:10px 16px;" +
    "background:#7a2e1f;color:#fff;font:14px/1.4 system-ui,sans-serif;text-align:center";
  notice.textContent = "This page is not signed in to the agent. Open it again from the agent's icon " +
    "beside the clock, or from its window.";
  document.body.appendChild(notice);
}

const PAGE_ZONE = (() => {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch (_err) { return ""; }
})();

const api = {
  async get(path) {
    const response = await fetch(path, { headers: { Accept: "application/json", "X-Time-Zone": PAGE_ZONE } });
    signInNeeded(response);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || response.statusText);
    return data;
  },
  async post(path, body) {
    const response = await fetch(path, { method: "POST",
      headers: { "Content-Type": "application/json", "X-Time-Zone": PAGE_ZONE }, body: JSON.stringify(body || {}) });
    signInNeeded(response);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || response.statusText);
    return data;
  },
};

/* -- THE WAITING NOTIFICATIONS, A PANEL DOWN THE WHOLE SIDE OF THE SCREEN (the user, 2026-11-13: "你现在这个
   notifications/recommendation的channel太不现代了，能不能直接占满整个侧边框的那种（类似reports每个事项点击之后弹出来
   的内容)"): its own window, docked to the right edge, as tall as the screen; each by its title, opened in
   place - its words, why it was told, its answers, and a reply in words. Answered, it folds away. -- */
// Always alone in its window, now: its own page (notes.html).
const NOTES_SOLO = true;
const notesState = { sections: [], open: new Set(), leaving: new Set(), q: "", drawn: "", writing: "" };

/* WHEN, IN THE USER'S OWN TIME, THE DATE WRITTEN OUT (the user, 2026-11-16: "你标的时间要用用户的时间，写清楚日期"): this
   computer's time zone, "2026-11-16 20:51" - never a bare "08:51 PM". */
function notesAt(at) {
  if (!at) return "";
  const when = new Date(at);
  if (Number.isNaN(when.getTime())) return "";
  return localStamp(at);
}

/* ONE WAITING, AS THE AGENT SAYING IT (the user, 2026-11-16: "你现在整个sidebar都变成chat, agent发notification的时候
   也是相当于在给用户chat"): a message of the agent's in the chat - what kind and when, its words whole, why it was
   said, and its answers under it. Its words the agent's, never looked up to be translated (.chat-message). */
/* ITS ANSWERS AS QUICK REPLIES BY THE BOX (the user, 2026-11-16: "这些按钮本质上作为快速回复（在input旁边有按钮，点击可以
   快速回复)"): each a reply the user can give with one press - its own answers, a reply in words (the box says to
   which), and Go to it, in the report (the user, 2026-11-15: "要有一个option是go to the task"). */
function sideQuickHtml(row) {
  // Short on the button - what it does in brackets left to its tip: "Got it (no more notifications)" is "Got it".
  const short = (label) => String(label || "").replace(/\s*[(（][^)）]*[)）]\s*$/, "") || String(label || "");
  const answers = (row.actions || []).filter((one) => !one.writes).map((one, n) => `<button type="button"
      class="small${n ? " secondary" : ""} side-quick-one" title="${escapeHtml(one.label)}" data-notes-act="${escapeHtml(row.id)}" data-key="${escapeHtml(one.key)}">${escapeHtml(short(one.label))}</button>`).join("");
  const reply = (row.actions || []).find((one) => one.writes);
  const replies = reply ? `<button type="button" class="small secondary side-quick-one" data-notes-replyto="${escapeHtml(row.id)}">${escapeHtml(reply.label || "Reply")}</button>` : "";
  const goes = row.goes ? `<button type="button" class="small secondary side-quick-one" data-notes-go="${escapeHtml(row.id)}">${escapeHtml("Go to it")}</button>` : "";
  return answers + replies + goes;
}

function notesCard(row, kind) {
  // TOLD IN THE AGENT'S OWN WORDS, EVERY LINK KEPT (the user, 2026-11-17: "允许agent整体转述，但是要agent一定保留里面的所有
  // url/超链接信息"): what it said of it (agent/information_queue.py "say", its links checked there), its own link under
  // it if the words do not have it; what it was as it was made, and why it was raised, under its marks (sideExtras).
  // Not judged by the agent (the user's own reminder, an error): why first, then what it is, as it was made.
  const said = String(row.said || "");
  const details = [row.why ? `<p>${linkify(escapeHtml(row.why))}</p>` : "",
    said ? sideContentHtml({ title: row.title, detail: row.detail }) : "",
    row.note ? `<p>${linkify(escapeHtml(row.note))}</p>` : ""].filter((one) => one.trim()).join("");
  const url = String(row.url || "").trim();
  return `<div class="side-note-head"><i class="side-note-dot" aria-hidden="true"></i><span class="notes-kind">${escapeHtml(kind)}</span>
      <span class="notes-when">${escapeHtml(notesAt(row.at))}</span></div>
    ${said ? `<div class="notes-text chat-message">${linkify(escapeHtml(said))}</div>
        ${url && !said.includes(url) ? sideContentHtml({ url }) : (url ? "" : sideOpenThePage("", row.goes ? row.id : ""))}`
      : `${row.why ? `<p class="side-note-why chat-message">${linkify(escapeHtml(row.why))}</p>` : ""}${sideContentHtml(row)}`}
    ${row.learning ? sideLearningHtml(row) : ""}
    ${sideExtras([{ key: "rules", label: "Rules used", html: sideRulesList(row.rules) },
                  { key: "details", label: "More details", html: said ? details : (row.note ? `<p>${linkify(escapeHtml(row.note))}</p>` : "") }])}
    ${typeof sideReactHtml === "function" ? sideReactHtml(row.reactions || [], row.bad) : ""}`;
}

/* A LEARNING QUESTION'S OPTIONS (the user, 2026-11-17: "learning的question 要求必须给option (让用户可以点，同时有一个chat
   bar让用户回复)"): each pressed, the user's own rule at once; or an answer in words, in the chat's box. */
function sideLearningHtml(row) {
  const learning = row.learning || {};
  if (learning.answered) {
    return `<p class="side-learn-done">${escapeHtml("You chose:")} <strong class="chat-message">${escapeHtml(learning.answered)}</strong></p>`;
  }
  return `<div class="side-learn-options">${(learning.options || []).map((label, n) => `<button type="button"
      class="side-learn-option chat-message" data-learn-note="${escapeHtml(row.id)}" data-learn-option="${n}">${escapeHtml(label)}</button>`).join("")}
    <button type="button" class="side-learn-words" data-notes-replyto="${escapeHtml(row.id)}">${escapeHtml("Reply in words")}</button></div>`;
}
async function sideLearningPressed(button) {
  const box = button.closest(".side-learn-options");
  box?.querySelectorAll("button").forEach((one) => { one.disabled = true; });
  try {
    const said = await api.post("/api/learning/answer", { id: button.dataset.learnNote,
      option: Number(button.dataset.learnOption) });
    if (box) box.outerHTML = `<p class="side-learn-done">${escapeHtml("You chose:")} <strong class="chat-message">${escapeHtml(said.answered || button.textContent)}</strong></p>`;
  } catch (err) {
    box?.querySelectorAll("button").forEach((one) => { one.disabled = false; });
    toast(err.message, "error");
  }
}

/* What was told, as it was made: its title, its words (not when they only say the title again) and its link, the
   address itself - else, its place in the report. */
function sideContentHtml(row) {
  const title = String(row.title || "").trim();
  const words = String(row.detail || "").trim();
  const url = String(row.url || "").trim();
  return `${title ? `<div class="side-note-title chat-message">${linkify(escapeHtml(title))}</div>` : ""}
    ${words && words !== title ? `<div class="notes-text chat-message">${linkify(escapeHtml(words))}</div>` : ""}
    ${url ? `<p class="side-note-url chat-message"><a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(url)}</a></p>`
      : sideOpenThePage("", row.goes ? row.id : "")}`;
}

/* UNDER EACH MESSAGE, ITS MARKS (the user, 2026-11-17: "Rules used 这个是可以collapse的（每个message下有多个logo，显示不同
   collapse的content, 包括rules used, more details等)"): a small mark for each part there is, its part shown under it
   when pressed, put away when pressed again - one at a time. */
const SIDE_EXTRA_MARKS = {
  rules: `<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M8 2v12M4.5 14h7M3 5h10M3 5l-2 4.5h4zM13 5l-2 4.5h4z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  details: `<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M8 7.2v4M8 4.9v.1" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>`,
};
/* NOT LIKE THE REACTIONS UNDER IT (the user, 2026-11-17: "这个两行的label太像了 ... 想办法区分开"): its parts are the
   message's own - on the left, each named, with the arrow that opens it; what the user answers with is theirs - on
   the right, in one rounded box (sideReactHtml). */
const SIDE_EXTRA_OPENS = `<svg class="side-extra-opens" viewBox="0 0 16 16" width="10" height="10" aria-hidden="true"><path d="M6 4l4 4-4 4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
function sideExtras(parts) {
  const shown = (parts || []).filter((one) => one && one.html);
  if (!shown.length) return "";
  return `<div class="side-extras"><div class="side-extra-bar">${shown.map((one) => `<button type="button"
      class="side-extra" data-extra="${escapeHtml(one.key)}" aria-expanded="false" title="${escapeHtml(one.label)}"
      aria-label="${escapeHtml(one.label)}">${SIDE_EXTRA_MARKS[one.key] || ""}<span class="side-extra-label">${escapeHtml(one.label)}</span>${SIDE_EXTRA_OPENS}</button>`).join("")}</div>
    ${shown.map((one) => `<div class="side-extra-body" data-extra-body="${escapeHtml(one.key)}" hidden>
      <div class="side-extra-title">${escapeHtml(one.label)}</div><div class="chat-message">${one.html}</div></div>`).join("")}</div>`;
}
function sideExtraPressed(mark) {
  const box = mark.closest(".side-extras");
  if (!box) return;
  const opening = mark.getAttribute("aria-expanded") !== "true";
  box.querySelectorAll(".side-extra").forEach((one) => one.setAttribute("aria-expanded", "false"));
  box.querySelectorAll(".side-extra-body").forEach((one) => { one.hidden = true; });
  if (!opening) return;
  mark.setAttribute("aria-expanded", "true");
  const body = box.querySelector(`.side-extra-body[data-extra-body="${mark.dataset.extra}"]`);
  if (body) body.hidden = false;
}
function sideRulesList(rules) {
  const list = (rules || []).filter(Boolean).slice(0, 3);
  return list.length ? `<ul>${list.map((one) => `<li>${escapeHtml(one)}</li>`).join("")}</ul>` : "";
}

/* THE LEARNED RULES IT FOLLOWED, at most three, small under it - for the user to judge (the user, 2026-11-16:
   "model 可以在chat里列出某个decision用到了哪些规则（选最重要的，不超过3条），方便我evaluate"). */
function sideRulesHtml(rules) {
  const list = (rules || []).filter(Boolean).slice(0, 3);
  if (!list.length) return "";
  return `<div class="side-rules"><span>${escapeHtml("Rules used:")}</span>
    <ul class="chat-message">${list.map((one) => `<li>${escapeHtml(one)}</li>`).join("")}</ul></div>`;
}

/* A reply's own "Rules used:" block at its end, taken out of its words and drawn small (agent: RULES_USED_NOTE). */
const SIDE_RULES_HEAD = /\n\s*(?:\*\*)?(Rules used|用到的规则)\s*[:：](?:\*\*)?\s*\n((?:\s*[-•*]\s*.+\n?){1,5})\s*$/;
function sideRulesOf(words) {
  const found = String(words || "").match(SIDE_RULES_HEAD);
  if (!found) return { words, rules: [] };
  const rules = found[2].split("\n").map((one) => one.replace(/^\s*[-•*]\s*/, "").trim()).filter(Boolean).slice(0, 3);
  return { words: String(words).slice(0, found.index).trimEnd(), rules };
}

/* OPEN THE PAGE, ALWAYS WHERE THERE IS ONE (the user, 2026-11-16: "现在notification 不用快速回复了，content里只要always 有
   open the page就行"): its own page; else where its thing is in the report. */
function sideOpenThePage(url, goes) {
  if (url) return `<p><a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml("Open the page")}</a></p>`;
  if (goes) return `<p><button type="button" class="small secondary" data-notes-go="${escapeHtml(goes)}">${escapeHtml("Open the page")}</button></p>`;
  return "";
}

/* The one the quick replies answer: the one pressed on, while it waits - else the newest that waits. */
function sideTarget() {
  const waiting = SIDE_LOG ? [...SIDE_LOG.querySelectorAll(".side-note:not(.answered)")] : [];
  const chosen = waiting.find((one) => one.dataset.note === sideChat.target);
  return chosen || waiting[waiting.length - 1] || null;
}

/* The quick replies drawn for it - none when nothing waits. */
function sideQuickDraw() {
  const quick = $("#side-quick");
  if (!quick) return;
  // NO QUICK REPLIES NOW (the user, 2026-11-16: "现在notification 不用快速回复了"): what the agent tells is said; the
  // user answers it in the chat, if at all.
  quick.hidden = true;
  quick.innerHTML = "";
  if (quick) return;
  const target = sideTarget();
  SIDE_LOG?.querySelectorAll(".side-note.targeted").forEach((one) => one.classList.remove("targeted"));
  const row = target ? notesRow(target.dataset.note) : null;
  if (!row) { quick.hidden = true; quick.innerHTML = ""; return; }
  target.classList.add("targeted");
  quick.innerHTML = sideQuickHtml(row);
  quick.hidden = !quick.innerHTML;
}

/* A quick reply given: said in the chat in the user's words - the button's - and done. */
function sideQuickAnswer(id, key) {
  const row = notesRow(id);
  const action = row && (row.actions || []).find((one) => one.key === key);
  if (!action) return;
  sideChat.used = true;
  sideSay("you", String(action.label || ""));
  if (sideChat.target === id) sideChat.target = "";
  notesAnswer(row.id, action);
}

/* Replying to one that waits: the chat's box says to which; what is sent goes to it, not to the chat. */
function sideReplyTo(id) {
  const row = notesRow(id);
  const action = row && (row.actions || []).find((one) => one.writes);
  const chip = $("#side-replying");
  sideChat.replyTo = action ? { id, action } : null;
  if (chip) {
    chip.hidden = !action;
    const title = chip.querySelector(".side-replying-title");
    if (title) title.textContent = action ? String(row.title || "") : "";
  }
  if (action) $("#side-input")?.focus();
}

/* Whether the panel is pinned - asked when it is drawn; `pinned` given, made so. */
async function sidePinned(pinned) {
  let said = null;
  try { said = await api.post("/api/notifications/panel_pin", pinned === undefined ? {} : { pinned }); }
  catch (_err) { said = null; }
  const pin = $("#side-pin");
  if (pin && said) pin.setAttribute("aria-pressed", String(Boolean(said.pinned)));
}

/* A line put in the chat by when it was said: before the first that was said after it. */
function sideInsert(line, at) {
  if (!SIDE_LOG) return;
  line.dataset.at = String(at || new Date().toISOString());
  const when = Date.parse(line.dataset.at) || Date.now();
  const after = [...SIDE_LOG.children].find((one) => (Date.parse(one.dataset.at || "") || 0) > when);
  if (after) SIDE_LOG.insertBefore(line, after); else SIDE_LOG.appendChild(line);
}

/* Answered - here, beside the avatar or in the report: it stays, as said. */
function sideNoteAnswered(node) {
  node.classList.add("answered");                 // told: it stays in the chat, as said
  node.classList.remove("targeted");
}

/* Links in what was said: a bare address, or [words](address) shown as its words (the report's events, the user,
   2026-11-17: "这个需要加link, 跳到panel里的细节"). */
function linkify(html) {
  return html.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)<]+)\)|https?:\/\/[^\s<]+/g, (whole, words, address) => {
    const url = address || whole;
    return `<a href="${url}" target="_blank" rel="noopener noreferrer">${words || url}</a>`;
  });
}

/* THE SIDE PANEL, ONE CHAT (the user, 2026-11-16): its head drawn once - with the button that puts it away, off
   the screen ("notification要有collapse按键，点击之后会collapse到屏幕外") - and each waiting one a message of the
   agent's, put in among what was said by when it came; drawn again only when it changed, and never while its
   reply is being written. One no longer waiting stays, answered. */
function renderNotes(force = false) {
  const page = $("#notes-page");
  if (!page || !SIDE_LOG) return;
  if (!page.querySelector(".notes-scroll")) {
    // ITS PIN (the user, 2026-11-16: "只有user主动选择固定在右边，才是缩放的view"): pressed, the right of the screen is
    // the panel's and the other windows stand beside it; not, it opens over them.
    page.innerHTML = `<header class="notes-head side-head">
        <div><h2 class="notes-heading">${escapeHtml("Your agent")}</h2><span class="muted" id="side-count"></span></div>
        <div class="side-head-buttons">
          <button type="button" class="side-collapse" id="side-play-open" aria-pressed="false"
            aria-label="${escapeHtml("Play - your world")}" title="${escapeHtml("Play - your world")}"><svg viewBox="0 0 24 24"
            width="18" height="18" aria-hidden="true"><path d="M7 7h10a5 5 0 0 1 0 10c-1.6 0-2.4-1-3.3-2h-3.4c-.9 1-1.7 2-3.3 2a5 5 0 0 1 0-10z"
            fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M7.5 10.5v3M6 12h3" stroke="currentColor"
            stroke-width="1.8" stroke-linecap="round"/><circle cx="15.5" cy="11" r="1.1" fill="currentColor"/><circle cx="17.5" cy="13.2"
            r="1.1" fill="currentColor"/></svg></button>
          <button type="button" class="side-collapse" id="side-search-open" aria-pressed="false"
            aria-label="${escapeHtml("Search")}" title="${escapeHtml("Search")}"><svg viewBox="0 0 24 24" width="18"
            height="18" aria-hidden="true"><circle cx="11" cy="11" r="6.5" fill="none" stroke="currentColor"
            stroke-width="1.8"/><path d="M16 16l5 5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg></button>
          <button type="button" class="side-collapse side-pin" id="side-pin" aria-pressed="false"
            aria-label="${escapeHtml("Pin to the right")}" title="${escapeHtml("Pin to the right")}"><svg viewBox="0 0 24 24"
            width="18" height="18" aria-hidden="true"><path d="M14 3l7 7-3 1-4 4 1 5-2 1-4-4-5 5-1-1 5-5-4-4 1-2 5 1 4-4z"
            fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg></button>
        </div>
      </header>
      <div class="side-search" id="side-search" hidden>
        <input type="search" id="side-search-input" autocomplete="off"
          placeholder="${escapeHtml("Search the conversation")}" aria-label="${escapeHtml("Search the conversation")}">
        <div class="side-search-hits" id="side-search-hits"></div>
      </div>
      <div class="notes-scroll"></div>`;
    page.querySelector(".notes-scroll").appendChild(SIDE_LOG);
    const scroller = page.querySelector(".notes-scroll");
    scroller.addEventListener("scroll", () => { if (scroller.scrollTop < 80) sideLoadEarlier(); });
    // The cards beside what they ask, again as the chat or the panel changes size.
    window.addEventListener("resize", sideLayoutAskedSoon);
    if (typeof ResizeObserver !== "undefined") new ResizeObserver(sideLayoutAskedSoon).observe(SIDE_LOG);
    $("#side-search-open").onclick = () => sideSearchOpen($("#side-search").hidden);
    // THE PLAY VIEW (play.js): the agent's world of the user's days - a view of its own, over the chat.
    $("#side-play-open").onclick = () => { if (typeof playToggle === "function") playToggle(); };
    $("#side-search-input").addEventListener("input", sideSearchTyped);
    $("#side-search-input").addEventListener("keydown", (event) => { if (event.key === "Escape") sideSearchOpen(false); });
    $("#side-search-hits").addEventListener("click", (event) => {
      const hit = event.target.closest("[data-hit]");
      if (hit) sideJumpTo(hit.dataset.hit);
    });
    sidePinned();
  }
  const rows = notesState.sections.flatMap((part) => (part.rows || []).map((row) => ({ row, kind: part.name,
    ink: ["notifications", "summaries", "recommendations", "headsup"].includes(part.kind) ? part.kind : "notifications" })));
  const count = $("#side-count");
  if (count) count.textContent = "";
  const here = new Set(rows.map((one) => one.row.id));
  SIDE_LOG.querySelectorAll(".side-note:not(.answered)").forEach((node) => {
    if (!here.has(node.dataset.note)) sideNoteAnswered(node);
  });
  let added = false;
  rows.forEach(({ row, kind, ink }) => {
    const drawn = JSON.stringify([row, kind, ink]);
    const node = [...SIDE_LOG.querySelectorAll(".side-note")].find((one) => one.dataset.note === row.id);
    if (node && !force && node.dataset.drawn === drawn) return;
    if (node && node.querySelector(".notes-reply textarea:focus")) return;     // a reply being written
    const line = document.createElement("div");
    line.className = `side-msg agent side-note ink-${ink}`;       // its kind's colour, as beside the avatar
    line.dataset.note = row.id;
    line.dataset.time = notesAt(row.at);        // under it, as every other message's
    line.dataset.who = "agent";
    line.dataset.drawn = drawn;
    line.innerHTML = notesCard(row, kind);
    line._reactions = [...(row.reactions || [])];
    line._bad = Boolean(row.bad);
    if (node) { line.dataset.at = node.dataset.at; node.replaceWith(line); }
    else {
      sideInsert(line, row.at);
      added = true;
      if (sideNotesDrawn) sideTypeOut(line);        // come now, of itself: said as if spoken
    }
  });
  // Something new: followed to the end only when the user is at it already - never pulled away from what they read.
  if (added && sideNearEnd()) sideToEnd();
  sideNotesDrawn = true;
  sideQuickDraw();
  sideSeen(rows.map((one) => one.row.id));
}

/* READ ONLY ONCE THE USER HAS SEEN IT (the user, 2026-11-16: "只有你真的点过、看过侧边栏才算已读"): shown here, it waits
   until they press, type or scroll in the panel, or open it themselves - not when a notification slid it out while
   they were away. Waiting, a dot on its tab says so. No answer to learn from. */
const sideSeenSent = new Set();
const sidePending = new Set();
let sideLookedAt = 0;
/* Whether the notes were drawn once: those there when the panel opened are not said again as if spoken. */
let sideNotesDrawn = false;
function sideSeen(ids) {
  ids.filter((id) => !sideSeenSent.has(id)).forEach((id) => sidePending.add(id));
  [...sidePending].forEach((id) => { if (!ids.includes(id)) sidePending.delete(id); });   // read elsewhere
  if (Date.now() - sideLookedAt < 60000 && !document.hidden) sideSeenFlush();
  sideUnreadDraw();
}
function sideLooked() {
  sideLookedAt = Date.now();
  sideSeenFlush();
}
function sideSeenFlush() {
  const fresh = [...sidePending].filter((id) => !sideSeenSent.has(id));
  sidePending.clear();
  sideUnreadDraw();
  if (!fresh.length) return;
  fresh.forEach((id) => sideSeenSent.add(id));
  api.post("/api/notifications/seen", { ids: fresh }).catch(() => fresh.forEach((id) => {
    sideSeenSent.delete(id);
    sidePending.add(id);
  }));
}
function sideUnreadDraw() {
  $("#notes-close")?.classList.toggle("has-unread", sidePending.size > 0);
}

async function loadNotes(force = false) {
  let said;
  try { said = await api.get("/api/notifications/waiting_list"); }
  catch (_err) { return; }
  notesState.sections = said.sections || [];
  notesState.private = Boolean(said.private);
  const here = new Set(notesState.sections.flatMap((part) => (part.rows || []).map((row) => row.id)));
  notesState.leaving.forEach((id) => { if (!here.has(id)) notesState.leaving.delete(id); });
  renderNotes(force);
  sidePrivate(notesState.private);
  // THE LAST ONE ANSWERED, IT GOES (the user, 2026-11-13: "waiting notification没有内容了之后不会自动关闭了") -
  // opened with nothing in it, it stays.
  // ITS LAST ONE GONE, HOWEVER IT WENT (the user, 2026-11-13: "这个好像消息结束了没有自动关闭窗口" - one answered
  // beside the avatar or in the report left it open, empty): opened empty, it stays.
  // NOT PUT AWAY WHEN ITS LIST EMPTIES ANY MORE (2026-11-16): it is the chat - what is told is read once seen. Opened
  // by a notification alone and never touched, the desktop puts it away (desktop.py `_put_away_untouched`).
}

/* PUT AWAY, NOT CLOSED: slid out to the edge by the desktop and kept, to slide back in at once next
   time; where it cannot be, closed. */
async function closeNotes() {
  notesState.open.clear();
  let said = null;
  try { said = await api.post("/api/notifications/panel_hide", {}); } catch (_err) { said = null; }
  if (!said || !said.hidden) window.close();
}

function notesRow(id) {
  for (const part of notesState.sections) {
    const row = (part.rows || []).find((one) => one.id === id);
    if (row) return row;
  }
  return null;
}

async function notesAnswer(id, action, extra = {}) {
  notesState.leaving.add(id);                     // folded away on the press; back if it failed
  renderNotes(true);
  try {
    await api.post(action.path, Object.assign({}, action.body || {}, extra));
    notesState.answered = true;
    setTimeout(() => loadNotes(true), 260);
  } catch (err) {
    notesState.leaving.delete(id);
    toast(err.message, "error");
    renderNotes(true);
  }
}

/* -- THE CHAT IN THE SIDE PANEL (the user, 2026-11-16: "用户可以和agent chat"): under what waits, what is said,
   and a box at the bottom - the quick reply at once, the full agent's when it comes, told down the line
   (no asking every 300 ms). Its own conversation, carried on when the panel opens again. -- */
const SIDE_LOG = document.getElementById("side-chat-log");

/* WHO SAID IT, AT A GLANCE (the user, 2026-11-17: "你现在的display 方式不能明确区分哪些是agent哪些是我说的"): the user's
   words in a filled bubble on the right; the agent's on the left, each run of them under its face and its name. */
const sideAgent = { name: "" };
function sideMarkSpeakers() {
  if (!SIDE_LOG) return;
  let last = "";
  [...SIDE_LOG.children].forEach((one) => {
    if (!one.classList.contains("side-msg")) return;
    const who = one.dataset.who === "you" || one.classList.contains("you") ? "you" : "agent";
    if (who === "agent" && last !== "agent") {
      one.dataset.starts = "1";
      one.dataset.name = sideAgent.name || "Agent";
    } else delete one.dataset.starts;
    last = who;
  });
}
async function sideAgentFace() {
  try {
    const said = await api.get("/api/avatar/icon?size=40");
    sideAgent.name = String(said.name || "");
    if (said.png) document.documentElement.style.setProperty("--side-agent-face", `url("${said.png}")`);
  } catch (_err) { /* its name alone */ }
  sideMarkSpeakers();
}
if (SIDE_LOG) {
  new MutationObserver(() => sideMarkSpeakers()).observe(SIDE_LOG, { childList: true });
  SIDE_LOG.addEventListener("click", (event) => {
    const option = event.target.closest && event.target.closest(".side-learn-option");
    if (option) { event.stopPropagation(); sideLearningPressed(option); return; }
    const bad = event.target.closest && event.target.closest(".side-bad");
    if (bad) {
      event.stopPropagation();
      const line = bad.closest(".side-msg");
      if (line) sideBad(line, bad.getAttribute("aria-pressed") !== "true");
      return;
    }
    const add = event.target.closest && event.target.closest(".side-react-add");
    const chip = event.target.closest && event.target.closest(".side-react-chip");
    if (add) { event.stopPropagation(); sideReactPick(add); return; }
    if (chip) {
      event.stopPropagation();
      const line = chip.closest(".side-msg");
      if (line) sideReact(line, chip.dataset.react, false);
      return;
    }
    const mark = event.target.closest && event.target.closest(".side-extra");
    if (!mark) return;
    event.stopPropagation();                      // a mark, not the message it is under
    sideExtraPressed(mark);
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest || !event.target.closest(".side-react-pick, .side-react-add")) {
      document.querySelector(".side-react-pick")?.remove();
    }
  });
  sideAgentFace();
}
const SIDE_KEPT = "side.conversation";
const SIDE_SHOWN = 30;
const sideChat = { id: "", used: false, sending: false, turns: new Map(), target: "", replyTo: null,
  oldest: "", earlier: false, loadingEarlier: false };

function sideKept() { try { return localStorage.getItem(SIDE_KEPT) || ""; } catch (_err) { return ""; } }
function sideKeep(id) { try { localStorage.setItem(SIDE_KEPT, id); } catch (_err) { /* kept for this time only */ } }

function sideNearEnd() {
  const scroll = document.querySelector("#notes-page .notes-scroll");
  return !scroll || scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 160;
}
function sideToEnd() {
  const scroll = document.querySelector("#notes-page .notes-scroll");
  if (scroll) scroll.scrollTop = scroll.scrollHeight;
}

/* One line said: by "you" or the "agent"; its words the user's or the model's - never looked up to be translated
   (.chat-message, i18n.js) - unless it is the panel's own "Thinking…" while it waits. */
function sideWrite(line, words, kind = "", fresh = true) {
  line.className = `side-msg ${line.dataset.who}${kind ? ` ${kind}` : ""}${kind === "working" ? "" : " chat-message"}`;
  if (kind === "working") { line.textContent = words; return; }
  // ITS PICTURE, IF IT HAS ONE (side-visual.js): taken out of the words, drawn beside them - at once when the
  // answer is new, on a press of its mark when it is from before.
  const { words: said, view } = line.dataset.who === "agent" && typeof sideViewOf === "function"
    ? sideViewOf(words) : { words, view: null };
  const { words: told, rules } = line.dataset.who === "agent" ? sideRulesOf(said) : { words: said, rules: [] };
  line.innerHTML = linkify(escapeHtml(told)) + sideExtras([{ key: "rules", label: "Rules used", html: sideRulesList(rules) }])
    + (line.dataset.who === "agent" ? sideReactHtml(line._reactions || [], line._bad) : "");
  line._view = view;
  if (view) {
    // Beside the line, not in it: its word is the panel's, translated (the line's are not).
    const mark = line._mark || document.createElement("button");
    mark.type = "button";
    mark.className = "small secondary side-msg-view";
    mark.textContent = "Picture";
    mark.onclick = () => sideShowView(view);
    if (!line._mark) { line._mark = mark; line.after(mark); }
    if (fresh) sideShowView(view);
  }
}

/* DRAGGED BY ITS LEFT EDGE (the user, 2026-11-16: "那个side bar 要允许我鼠标拖动size"): as wide as it is dragged -
   the desktop told at most every SIDE_GRIP_MS, only the panel itself moving; let go, the width kept and the
   other windows moved for it, once. With its picture beside the chat, the chat's half is what is kept. */
const SIDE_GRIP_MS = 40;
/* How far the edge is moved before it is a drag - a press alone is not one. */
const SIDE_GRIP_FROM = 4;

/* ZOOMED IN AND OUT (the user, 2026-11-16: "那个sidebar 也需要能够zoom in/out"): Ctrl (or Command) with the wheel, or
   with + and -, and 0 for as it was - the page's own zoom, kept for the next time; the browser's own is not used,
   which would change the width the edge is dragged from. */
// Down to 40 % (the user, 2026-11-17: "zoom in out的大小，最小要变小" - 70 % was the least).
const SIDE_ZOOM = { least: 0.4, most: 2, step: 0.1, key: "side-zoom" };
/* ZOOMED ABOUT THE POINTER (the user, 2026-11-17: "我要的缩放是我鼠标点的位置的view大体不变"): what is under the pointer
   stays where it is on the screen - the chat scrolled after the zoom by as much as it moved. `at`: where the pointer
   is (the last place it was, for the keys). */
const sidePointer = { x: 0, y: 0 };
window.addEventListener("mousemove", (event) => { sidePointer.x = event.clientX; sidePointer.y = event.clientY; },
  { passive: true });
function sideZoomAbout(level, at = sidePointer) {
  const scroll = document.querySelector("#notes-page .notes-scroll");
  const under = scroll && at && document.elementFromPoint(at.x, at.y);
  const kept = under && scroll.contains(under) ? under : null;
  const before = kept ? kept.getBoundingClientRect().top : 0;
  const zoom = sideZoomTo(level);
  if (!kept) return zoom;
  for (let again = 0; again < 2; again += 1) {      // the scroll's units are the zoomed page's: set, then set right
    const moved = kept.getBoundingClientRect().top - before;
    if (Math.abs(moved) < 1) break;
    scroll.scrollTop += moved / zoom;
  }
  return zoom;
}
function sideZoomTo(level, say = true) {
  const zoom = Math.min(SIDE_ZOOM.most, Math.max(SIDE_ZOOM.least, Math.round(Number(level || 1) * 10) / 10));
  document.documentElement.style.zoom = zoom === 1 ? "" : String(zoom);
  document.documentElement.style.setProperty("--side-zoom", String(zoom));   // the page as tall as the window
  try { localStorage.setItem(SIDE_ZOOM.key, String(zoom)); } catch (_err) { /* this time only */ }
  // No "80%" said each step (the user, 2026-11-17: "你会弹出 X%这个消息框，不用弹出来"): the page itself shows it.
  return zoom;
}
function sideZoomNow() {
  return Number(document.documentElement.style.zoom || 1) || 1;
}
function sideZoom() {
  let kept = 1;
  try { kept = Number(localStorage.getItem(SIDE_ZOOM.key) || 1) || 1; } catch (_err) { kept = 1; }
  sideZoomTo(kept, false);
  window.addEventListener("wheel", (event) => {
    if (!(event.ctrlKey || event.metaKey) || sideViewOpen()) return;     // the picture's box zooms the picture
    event.preventDefault();
    sideZoomAbout(sideZoomNow() + (event.deltaY < 0 ? SIDE_ZOOM.step : -SIDE_ZOOM.step),
      { x: event.clientX, y: event.clientY });
  }, { passive: false });
  window.addEventListener("keydown", (event) => {
    if (sideViewOpen()) {                        // the picture's box: its own zoom, never the panel's
      if (["+", "="].includes(event.key)) { event.preventDefault(); sideViewZoom(1.25); }
      else if (["-", "_"].includes(event.key)) { event.preventDefault(); sideViewZoom(1 / 1.25); }
      else if (event.key === "0") { event.preventDefault(); sideViewFit(); }
      return;
    }
    if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
    const by = ["+", "="].includes(event.key) ? SIDE_ZOOM.step : ["-", "_"].includes(event.key) ? -SIDE_ZOOM.step
      : event.key === "0" ? 0 : null;
    if (by === null) return;
    event.preventDefault();
    sideZoomAbout(by === 0 ? 1 : sideZoomNow() + by);
  });
}

/* THE BOX'S HEIGHT, DRAGGED BY ITS TOP (the user, 2026-11-16: "下面那个input的位置是可调高度的（比如text特别长可以
   高点)"): kept for the next time; a long message makes it taller still, to `grows` or as tall as it was made. */
const SIDE_INPUT = { key: "side-input-height", least: 36, grows: 240 };
const sideInput = { chosen: 0 };
function sideInputGrip(box, fit) {
  const grip = $("#side-input-grip");
  try { sideInput.chosen = Math.max(0, Number(localStorage.getItem(SIDE_INPUT.key) || 0) || 0); }
  catch (_err) { sideInput.chosen = 0; }
  if (!grip || !box) return;
  let start = null;
  grip.addEventListener("pointerdown", (event) => {
    try { grip.setPointerCapture(event.pointerId); } catch (_err) { /* followed as it goes */ }
    start = { y: event.clientY, height: box.offsetHeight };
    grip.classList.add("dragging");
    event.preventDefault();
  });
  grip.addEventListener("pointermove", (event) => {
    if (!start) return;
    const zoom = sideZoomNow();
    const most = Math.round((window.innerHeight * 0.6) / zoom);
    sideInput.chosen = Math.min(most, Math.max(SIDE_INPUT.least, Math.round(start.height + (start.y - event.clientY) / zoom)));
    fit();
  });
  const end = () => {
    if (!start) return;
    start = null;
    grip.classList.remove("dragging");
    try { localStorage.setItem(SIDE_INPUT.key, String(sideInput.chosen)); } catch (_err) { /* this time only */ }
  };
  grip.addEventListener("pointerup", end);
  grip.addEventListener("pointercancel", end);
  fit();
}

/* IN PRIVATE MODE, SAID AT ITS TOP (the user, 2026-11-16: "如果现在是private mode, 这个sidebar上面会显示 you are on
   private mode之类的"): under its head, while it lasts. */
function sidePrivate(on) {
  const head = document.querySelector("#notes-page .side-head");
  if (!head) return;
  let line = $("#side-private");
  if (!line) {
    line = document.createElement("div");
    line.id = "side-private";
    line.className = "side-private";
    line.setAttribute("role", "status");
    line.innerHTML = `<strong>${escapeHtml("Private mode is on")}</strong>
      <span>${escapeHtml("Nothing of what you do is recorded until you turn it off (the avatar's right-click menu).")}</span>`;
    head.after(line);
  }
  line.hidden = !on;
}

function sideGrip() {
  const grip = document.createElement("div");
  grip.className = "side-grip";
  grip.setAttribute("aria-hidden", "true");
  document.body.appendChild(grip);
  // COLLAPSE, IN THE MIDDLE OF ITS LEFT EDGE (the user, 2026-11-16: "collapse sidebar的按钮，放在sidebar的左侧中间"):
  // pressed, the panel goes off the screen; over the edge that is dragged, never dragged itself.
  const away = document.createElement("button");
  away.type = "button";
  away.id = "notes-close";
  away.className = "side-collapse-tab";
  away.setAttribute("aria-label", "Collapse");
  away.title = "Collapse";
  away.textContent = "»";
  // PRESSED: put away to this tab at the screen's edge, or back (the user, 2026-11-16: "collapse的时候这个凸起还是在边框
  // 外（这样我可以点击展开)"); where the desktop cannot, put away as before.
  // ITS TABS, ON THE LEFT EDGE NEAR THE TOP (the user, 2026-11-16): each opens a layer left of the chat.
  const rail = document.createElement("div");
  rail.className = "side-rail";
  const icons = {
    // A picture with a pen on it: the small screen, to draw on (side-board.js).
    picture: '<rect x="2.5" y="3.5" width="15" height="13" rx="2" fill="none" stroke="currentColor" stroke-width="2"/><path d="M2.5 13.5l4-4 3 3 2-2 2 2" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><path d="M13.5 21.5l1-3.5 6-6 2.5 2.5-6 6z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>',
    images: '<rect x="3" y="4" width="18" height="16" rx="2" fill="none" stroke="currentColor" stroke-width="2.2"/><path d="M3 17l5-5 4 4 3-3 6 6" fill="none" stroke="currentColor" stroke-width="2.2"/>',
    told: '<path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4zM10 21h4" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linejoin="round"/>',
  };
  Object.keys(SIDE_DRAWERS).forEach((which) => {
    const tab = document.createElement("button");
    tab.type = "button";
    tab.dataset.drawer = which;
    tab.title = SIDE_DRAWERS[which];
    tab.setAttribute("aria-label", SIDE_DRAWERS[which]);
    tab.setAttribute("aria-pressed", "false");
    tab.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${icons[which]}</svg>`;
    tab.addEventListener("click", () => sideDrawer(which));
    rail.appendChild(tab);
  });
  document.body.appendChild(rail);
  away.addEventListener("click", async () => {
    let said = null;
    try { said = await api.post("/api/notifications/panel_toggle", {}); } catch (_err) { said = null; }
    if (!said || !said.done) { closeNotes(); return; }
    sidePeeking(said.peeking);
  });
  document.body.appendChild(away);
  let start = null, told = 0, waiting = null;
  const width = (event) => {
    const total = start.total + (start.x - event.screenX);
    return Math.round(start.wide ? total / 2 : total);
  };
  const tell = (chat, final) => api.post("/api/notifications/panel_wide",
    { width: chat, final, wide: start.wide }).catch(() => {});
  grip.addEventListener("pointerdown", (event) => {
    try { grip.setPointerCapture(event.pointerId); } catch (_err) { /* followed as it goes */ }
    const wide = document.body.classList.contains("side-wide");
    start = { x: event.screenX, total: window.innerWidth, wide, moved: false, asking: true, grabbed: false };
    grip.classList.add("dragging");
    event.preventDefault();
    // ON WINDOWS THE DESKTOP FOLLOWS IT, from where the pointer really is (the user, 2026-11-16: "还是没法移动
    // sidebar" - this page's idea of where the pointer is went stale as its window was moved): asked first; where
    // it cannot (a Mac), followed here.
    const asked = start;
    api.post("/api/notifications/panel_wide", { grab: true, wide })
      .then((said) => { asked.grabbed = Boolean(said && said.grabbed); })
      .catch(() => { asked.grabbed = false; })
      .finally(() => { asked.asking = false; });
  });
  grip.addEventListener("pointermove", (event) => {
    if (!start || start.asking || start.grabbed) return;
    // ONLY ONCE IT IS DRAGGED (the user, 2026-11-16: "点击sidebar 左边，本来想拖动，但是发现他突然变了位置"): a press
    // alone sizes nothing, moves nothing.
    if (!start.moved && Math.abs(event.screenX - start.x) < SIDE_GRIP_FROM) return;
    start.moved = true;
    const chat = width(event);
    clearTimeout(waiting);
    const due = SIDE_GRIP_MS - (Date.now() - told);
    if (due <= 0) { told = Date.now(); tell(chat, false); }
    else waiting = setTimeout(() => { told = Date.now(); tell(chat, false); }, due);
  });
  const end = (event) => {
    if (!start) return;
    clearTimeout(waiting);
    if (start.moved && !start.grabbed && !start.asking) tell(width(event), true);
    start = null;
    grip.classList.remove("dragging");
  };
  grip.addEventListener("pointerup", end);
  grip.addEventListener("pointercancel", end);
}

/* Put away to its tab or not: the tab's arrow points the way a press will take it. */
function sidePeeking(peeking) {
  const tab = $("#notes-close");
  if (!tab) return;
  tab.textContent = peeking ? "«" : "»";
  tab.title = peeking ? "Open" : "Collapse";
  tab.setAttribute("aria-label", tab.title);
}

/* Words still coming: a picture's block not yet whole is not shown as words. */
function sideComing(words) {
  const at = String(words || "").lastIndexOf("```view");
  return at >= 0 ? String(words).slice(0, at).trimEnd() : String(words || "");
}

/* The picture's column: drawn, and the panel made wide for it - or, where the desktop cannot widen it, put
   above the chat. Closed, the panel is as narrow as before. */
async function sideShowView(view) {
  // In the small screen, for the user to draw on (side-board.js).
  if (typeof sideBoardShowView === "function") { sideBoardShowView(view); return; }
  const body = $("#side-visual-body");
  if (!body) return;
  sidePick("picture");
  await sideOpenColumn();
  // Drawn now, its width read from the layout just set - not at the next frame, which a panel put away
  // never has.
  if (!sideDraw(body, view)) sideHideView();
}

/* THE SECOND LAYER OPENED, LEFT OF THE CHAT: the panel made wide for it - or, where the desktop cannot widen it, put
   above the chat. */
async function sideOpenColumn() {
  const column = $("#side-visual");
  if (!column) return;
  column.hidden = false;
  let wide = false;
  if (!document.body.classList.contains("side-wide")) {
    try { wide = Boolean((await api.post("/api/notifications/panel_wide", { wide: true })).wide); }
    catch (_err) { wide = false; }
  } else wide = true;
  document.body.classList.toggle("side-wide", wide);
  document.body.classList.toggle("side-stacked", !wide);
}

/* ITS LAYERS (the user, 2026-11-16: "再展开一层sidebar（放在原先sidebar的左边)。里面有各种功能，包括刚刚的image"): one
   shown at a time, by its tab on the panel's left edge. */
// ONE BUTTON (the user, 2026-11-17: "所以你现在3个按钮，本质上只需要一个按钮"): the small screen (side-board.js) - the
// conversation's pictures are put in it from its own bar; what was told is in the chat.
const SIDE_DRAWERS = { picture: "Small screen" };
let sideDrawerNow = "";
function sidePick(which) {
  sideDrawerNow = which;
  const column = $("#side-visual");
  if (!column) return;
  column.querySelectorAll("[data-drawer]").forEach((one) => { one.hidden = one.dataset.drawer !== which; });
  const title = $("#side-drawer-title");
  if (title) title.textContent = SIDE_DRAWERS[which] || "";
  document.querySelectorAll(".side-rail button").forEach((one) =>
    one.setAttribute("aria-pressed", String(one.dataset.drawer === which)));
}
async function sideDrawer(which) {
  const column = $("#side-visual");
  if (!column) return;
  // THE SMALL SCREEN, OVER THE TOP OF THE CHAT (side-board.js) - the panel not made wider.
  if (which === "picture" && typeof sideBoardToggle === "function") {
    if ($("#notes-close")?.textContent === "«") {
      try { await api.post("/api/notifications/panel_toggle", {}); sidePeeking(false); } catch (_err) { /* as it is */ }
    }
    sideBoardToggle();
    return;
  }
  if (sideDrawerNow === which && !column.hidden) { sideHideView(); return; }
  // Put away to its tab: brought back first.
  if ($("#notes-close")?.textContent === "«") {
    try { await api.post("/api/notifications/panel_toggle", {}); sidePeeking(false); } catch (_err) { /* as it is */ }
  }
  sidePick(which);
  if (which === "picture" && !$("#side-visual-body").children.length) {
    $("#side-visual-body").innerHTML = `<p class="side-drawer-empty">${escapeHtml("No picture yet - the agent draws one beside an answer when it helps.")}</p>`;
  }
  if (which === "images") sideDrawImages();
  await sideOpenColumn();
  if (which === "told") sideDrawTold();
}
/* THE PICTURES OF THIS CONVERSATION: those sent with messages and those of the screen that went with them. */
function sideDrawImages() {
  const box = $("#side-drawer-images");
  if (!box || !SIDE_LOG) return;
  const found = [...new Set([...SIDE_LOG.parentElement.querySelectorAll(".side-msg-files img, .side-shot img")]
    .map((one) => one.getAttribute("src")).filter(Boolean))];
  box.innerHTML = found.length ? "" : `<p class="side-drawer-empty">${escapeHtml("No pictures in this conversation yet.")}</p>`;
  found.reverse().forEach((address) => {
    const picture = document.createElement("img");
    picture.src = address;
    picture.alt = "";
    picture.onclick = () => sideLarger(address);
    box.appendChild(picture);
  });
}
/* WHAT THE AGENT TOLD LATELY, read or not - each as it was said, and where to open it. */
async function sideDrawTold() {
  const box = $("#side-drawer-told");
  if (!box) return;
  let said;
  try { said = await api.get("/api/side/told"); } catch (_err) { return; }
  const ink = { "Summaries": "summaries", "Recommended content": "recommendations", "Heads up": "headsup" };
  box.innerHTML = (said.told || []).map((one) => `<div class="side-told ink-${ink[one.kind] || "notifications"}">
      <div class="side-told-head">${escapeHtml(one.kind)} · ${escapeHtml(notesAt(one.at))}</div>
      ${one.said ? `<div class="chat-message">${linkify(escapeHtml(one.said))}</div>${one.url && !one.said.includes(one.url) ? sideContentHtml({ url: one.url }) : ""}`
        : sideContentHtml({ title: one.title, detail: one.words, url: one.url })}
      ${sideRulesHtml(one.rules)}</div>`).join("")
    || `<p class="side-drawer-empty">${escapeHtml("Nothing told in the last two days.")}</p>`;
}

async function sideHideView() {
  const column = $("#side-visual");
  if (column) column.hidden = true;
  sideDrawerNow = "";
  document.querySelectorAll(".side-rail button").forEach((one) => one.setAttribute("aria-pressed", "false"));
  const was = document.body.classList.contains("side-wide");
  document.body.classList.remove("side-wide", "side-stacked");
  if (typeof sideTipHide === "function") sideTipHide();
  if (was) { try { await api.post("/api/notifications/panel_wide", { wide: false }); } catch (_err) { /* left wide */ } }
}

function sideSay(who, words, kind = "", fresh = true, at = "", toEnd = true) {
  if (!SIDE_LOG) return null;
  const line = document.createElement("div");
  line.dataset.who = who;
  // WHEN, UNDER EACH (the user, 2026-11-16: "每个message要显示时间").
  line.dataset.time = notesAt(at || new Date().toISOString());
  sideInsert(line, at || new Date().toISOString());  // in its place first, by when: what goes beside it goes there
  sideWrite(line, words, kind, fresh);
  if (toEnd) sideToEnd();
  return line;
}

async function sideLoad() {
  // THE SIDE PANEL'S ONE CONVERSATION (the user, 2026-11-16: "所有agent的话，所有我的话都在一个session里"): what the
  // agent told and what was said here, as the agent reads it - kept by the agent, not by this page. A STRETCH AT A
  // TIME (2026-11-17: "默认显示的是到上上个summary point 为止的history, 如果用户继续上翻会继续load之前的"): the rest
  // as the user scrolls up (sideLoadEarlier).
  let said;
  try { said = await api.get("/api/side/conversation"); } catch (_err) { return; }
  sideChat.id = said.conversation_id || "";
  sideShowPast(said);
  sideChat.oldest = said.from || "";
  sideChat.earlier = Boolean(said.earlier);
  sideToEndOnceDrawn();
}

/* At the chat's end - now, and again once what is still coming in (its notes, pictures, cards) is drawn. */
function sideToEndOnceDrawn() {
  sideToEnd();
  requestAnimationFrame(() => sideToEnd());
  setTimeout(sideToEnd, 350);
}

/* A stretch from before, drawn in its place - never moving the chat to its end. */
function sideShowPast(said) {
  const asked = new Map();
  (said.followups || []).forEach((one) => {
    if (!asked.has(one.message_id)) asked.set(one.message_id, []);
    asked.get(one.message_id).push(one);
  });
  for (const one of said.messages || []) {
    const meta = one.metadata || {};
    if (meta.origin === "notification_answer") continue;   // the agent's to read, not a line said
    if (meta.origin === "notification") {
      sidePastNote(one);
      // Its follow-ups under it - on its live card when it is still waiting (that took its place).
      const told = SIDE_LOG.querySelector(`[data-id="${CSS.escape(String(one.id))}"]`)
        || SIDE_LOG.querySelector(`[data-note="${CSS.escape(String(meta.notification_id || ""))}"]`);
      if (told) (asked.get(one.id) || []).forEach((fu) => sideFollowUpCard(told, fu.about, fu.question, fu.answer, fu.id));
      continue;
    }
    if (meta.origin === "compacted") { sideCompacted(one); continue; }
    if (!(one.role === "user" || one.role === "assistant") || !String(one.content || "").trim()) continue;
    sideChat.used = true;
    // From before: their pictures a press away, none opened by themselves.
    const line = sideSay(one.role === "user" ? "you" : "agent", one.content, "", false, one.created_at, false);
    if (!line) continue;
    line.dataset.id = one.id;
    if (one.role === "assistant" && ((meta.reactions || []).length || meta.bad_content)) {
      sideReactDraw(line, meta.reactions || [], Boolean(meta.bad_content));
    }
    if (one.role === "assistant") line.dataset.message = one.id;
    if (one.role === "user") sideFilesShown(line, meta.attachments || []);
    (asked.get(one.id) || []).forEach((fu) => sideFollowUpCard(line, fu.about, fu.question, fu.answer, fu.id));
  }
}

/* SCROLLED TO THE TOP, THE STRETCH BEFORE IT - the chat kept where it was read. */
async function sideLoadEarlier() {
  if (sideChat.loadingEarlier || !sideChat.earlier || !sideChat.oldest) return;
  sideChat.loadingEarlier = true;
  const scroll = document.querySelector("#notes-page .notes-scroll");
  const fromEnd = scroll ? scroll.scrollHeight - scroll.scrollTop : 0;
  try {
    const said = await api.get(`/api/side/conversation?before=${encodeURIComponent(sideChat.oldest)}`);
    sideShowPast(said);
    sideChat.oldest = said.from || sideChat.oldest;
    sideChat.earlier = Boolean(said.earlier);
    if (scroll) scroll.scrollTop = scroll.scrollHeight - fromEnd;
  } catch (_err) { /* tried again at the next scroll */ }
  finally { sideChat.loadingEarlier = false; }
}

/* SEARCHED, AS WECHAT SEARCHES A CHAT (the user, 2026-11-17: "要支持搜索功能，可以做exact match找到之前的聊天内容并且
   display in chat context"): the messages with the words, exactly, the newest first; pressed, the chat goes to it,
   what was said round it drawn - from before it on, scrolled either way. */
let sideSearchTimer = 0;
function sideSearchOpen(open) {
  const box = $("#side-search");
  if (!box) return;
  box.hidden = !open;
  $("#side-search-open")?.setAttribute("aria-pressed", String(open));
  if (open) $("#side-search-input")?.focus();
  else { const hits = $("#side-search-hits"); if (hits) hits.innerHTML = ""; }
}
function sideSearchTyped() {
  clearTimeout(sideSearchTimer);
  sideSearchTimer = setTimeout(async () => {
    const words = String($("#side-search-input")?.value || "").trim();
    const hits = $("#side-search-hits");
    if (!hits) return;
    if (!words) { hits.innerHTML = ""; return; }
    let said;
    try { said = await api.get(`/api/side/search?q=${encodeURIComponent(words)}`); } catch (_err) { return; }
    hits.innerHTML = (said.hits || []).map((one) => `<button type="button" class="side-search-hit" data-hit="${escapeHtml(one.id)}">
        <span class="side-search-who">${escapeHtml(one.role === "user" ? "You" : (sideAgent.name || "Agent"))} · ${escapeHtml(notesAt(one.created_at))}</span>
        <span class="side-search-words chat-message">${escapeHtml(one.before)}<mark>${escapeHtml(one.found)}</mark>${escapeHtml(one.after)}</span></button>`).join("")
      || `<p class="side-search-none">${escapeHtml("Nothing found")}</p>`;
  }, 250);
}
async function sideJumpTo(id) {
  if (!SIDE_LOG || !id) return;
  const find = () => SIDE_LOG.querySelector(`[data-id="${CSS.escape(String(id))}"]`);
  let line = find();
  if (!line) {
    try {
      const said = await api.get(`/api/side/conversation?around=${encodeURIComponent(id)}`
        + (sideChat.oldest ? `&before=${encodeURIComponent(sideChat.oldest)}` : ""));
      sideShowPast(said);
      if (said.from) sideChat.oldest = said.from;
      sideChat.earlier = Boolean(said.earlier);
    } catch (_err) { return; }
    line = find();
  }
  if (!line) return;
  line.scrollIntoView({ block: "center" });
  // The small screen over the top of the chat (side-board.js): the hit in the middle of what is left below it.
  const board = document.getElementById("side-board");
  const scroller = line.closest(".notes-scroll");
  if (board && !board.hidden && scroller) {
    const zoom = parseFloat(getComputedStyle(document.documentElement).zoom) || 1;
    const below = board.getBoundingClientRect().bottom, end = scroller.getBoundingClientRect().bottom;
    const at = line.getBoundingClientRect();
    scroller.scrollTop += ((at.top + at.height / 2) - (below + end) / 2) / zoom;
  }
  line.classList.add("side-found");
  setTimeout(() => line.classList.remove("side-found"), 2400);
}

/* SAID AS IF SPOKEN (the user, 2026-11-17: "凡是agent被自己trigger 弹出消息的（不是用户说他回答的），这个输出用比较慢的word by
   word, 有一种在说话的感觉"): what the agent tells of itself shown a word at a time (a character at a time in Chinese
   and Japanese) - its marks and its head at once. Not where the user asked for less motion. */
const SIDE_WORD_MS = 90;
const SIDE_HAN_MS = 55;
function sideTypeOut(line) {
  if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const walker = document.createTreeWalker(line, NodeFilter.SHOW_TEXT, { acceptNode: (node) =>
    node.parentElement && node.parentElement.closest(".side-extras, .side-note-head, .side-react-row, button")
      ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT });
  const parts = [];
  while (walker.nextNode()) parts.push(walker.currentNode);
  const han = /[\u3000-\u9fff\uac00-\ud7af\uff00-\uffef]/;
  const queue = parts.map((node) => {
    const bits = String(node.textContent || "").match(/[\u3000-\u9fff\uac00-\ud7af\uff00-\uffef]|[^\s\u3000-\u9fff\uac00-\ud7af\uff00-\uffef]+\s*|\s+/g) || [];
    node.textContent = "";
    return { node, bits };
  });
  line.classList.add("side-typing");
  let at = 0;
  let bit = 0;
  const scroll = document.querySelector("#notes-page .notes-scroll");
  const step = () => {
    while (at < queue.length && bit >= queue[at].bits.length) { at += 1; bit = 0; }
    if (at >= queue.length) { line.classList.remove("side-typing"); return; }
    const said = queue[at].bits[bit];
    queue[at].node.textContent += said;
    bit += 1;
    if (scroll && scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 120) sideToEnd();
    setTimeout(step, han.test(said) ? SIDE_HAN_MS : SIDE_WORD_MS);
  };
  step();
}

/* QUICK REACTIONS, AS SLACK HAS THEM (the user, 2026-11-17: "agent的每个回复，有一个快速emoji功能，代表用户对这个的反应（里面有
   emoji代表good content和bad content)"): under each thing the agent said, those given, and a face to add one. */
// Expressions only ("emoji 只是用户的expression"); whether it was bad content is its own button beside them.
const SIDE_REACTIONS = [["\u{1F44D}", "Thumbs up"], ["\u2764\uFE0F", "Heart"], ["\u{1F602}", "Laughing"],
  ["\u{1F62E}", "Surprised"], ["\u{1F622}", "Sad"], ["\u{1F914}", "Thinking"], ["\u{1F389}", "Celebrating"]];
const SIDE_REACT_FACE = `<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M5.4 9.4c.7 1 1.6 1.5 2.6 1.5s1.9-.5 2.6-1.5" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/><circle cx="6" cy="6.6" r=".8" fill="currentColor"/><circle cx="10" cy="6.6" r=".8" fill="currentColor"/></svg>`;
/* BAD CONTENT AS A MARK, NOT WORDS (the user, 2026-11-17: "bad content也用一个logo"): a thumb down, said by its title. */
const SIDE_BAD_MARK = `<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M6.2 9.6H3.4a1.2 1.2 0 0 1-1.2-1.4l.8-4.3A1.3 1.3 0 0 1 4.3 2.8h5.5v6.6L7.6 13.4a1 1 0 0 1-1.8-.5zM9.8 2.8h2.6a1 1 0 0 1 1 1v4.6a1 1 0 0 1-1 1H9.8" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>`;
function sideReactHtml(given, bad = false) {
  const named = new Map(SIDE_REACTIONS);
  return `<div class="side-react-row">${(given || []).filter((one) => named.has(one)).map((one) => `<button type="button"
      class="side-react-chip" data-react="${escapeHtml(one)}" aria-pressed="true" title="${escapeHtml(named.get(one))}">${one}</button>`).join("")}
    <span class="side-react-tools"><button type="button" class="side-react-add" aria-label="${escapeHtml("React")}"
      title="${escapeHtml("React")}">${SIDE_REACT_FACE}</button><button type="button" class="side-bad"
      aria-pressed="${bad ? "true" : "false"}" title="${escapeHtml("Bad content")}" aria-label="${escapeHtml("Bad content")}">${SIDE_BAD_MARK}</button></span></div>`;
}
function sideReactDraw(line, given, bad = line._bad) {
  line._reactions = [...(given || [])];
  line._bad = Boolean(bad);
  const row = line.querySelector(":scope > .side-react-row");
  if (row) row.outerHTML = sideReactHtml(line._reactions, line._bad);
  else line.insertAdjacentHTML("beforeend", sideReactHtml(line._reactions, line._bad));
}
function sideReactPick(button) {
  document.querySelector(".side-react-pick")?.remove();
  const line = button.closest(".side-msg");
  if (!line) return;
  const pick = document.createElement("div");
  pick.className = "side-react-pick";
  pick.innerHTML = SIDE_REACTIONS.map(([one, what]) => `<button type="button" data-pick="${escapeHtml(one)}"
    title="${escapeHtml(what)}" aria-label="${escapeHtml(what)}">${one}</button>`).join("");
  button.closest(".side-react-row").appendChild(pick);
  pick.addEventListener("click", (event) => {
    const chosen = event.target.closest("[data-pick]");
    if (!chosen) return;
    event.stopPropagation();
    pick.remove();
    sideReact(line, chosen.dataset.pick, !(line._reactions || []).includes(chosen.dataset.pick));
  });
}
/* BAD CONTENT, A PRESS (the user, 2026-11-17: "要在emoji旁边加一个快速的bad content"): learned from, as the emoji are. */
async function sideBad(line, on) {
  sideReactDraw(line, line._reactions || [], on);
  try {
    await api.post("/api/side/react", { id: line.dataset.id || line.dataset.message || "", note: line.dataset.note || "",
      bad: true, on });
  } catch (_err) { /* shown; kept the next time */ }
}
async function sideReact(line, emoji, on) {
  const now = (line._reactions || []).filter((one) => one !== emoji);
  sideReactDraw(line, on ? [...now, emoji] : now);
  try {
    await api.post("/api/side/react", { id: line.dataset.id || line.dataset.message || "", note: line.dataset.note || "",
      emoji, on });
  } catch (_err) { /* shown; kept the next time */ }
}

/* SUMMARISED, AS CLAUDE'S CLI SAYS IT (the user, 2026-11-17: "总结这个用claude cli 一样的显示，不作为一个notification提醒用户，
   只是能看到"): a quiet line where what was summarised ends, the summary under it a press away. */
function sideCompacted(one) {
  if (!SIDE_LOG || SIDE_LOG.querySelector(`[data-compacted="${CSS.escape(String(one.id))}"]`)) return;
  const meta = one.metadata || {};
  const line = document.createElement("div");
  line.className = "side-compacted";
  line.dataset.compacted = String(one.id);
  line.innerHTML = `<button type="button" class="side-compacted-head" aria-expanded="false">
      <span>${escapeHtml("Earlier conversation summarized")}</span></button>
    <div class="side-compacted-body chat-message" hidden>${escapeHtml(String(meta.summary || ""))}</div>`;
  const head = line.querySelector(".side-compacted-head");
  head.onclick = () => {
    const body = line.querySelector(".side-compacted-body");
    body.hidden = !body.hidden;
    head.setAttribute("aria-expanded", String(!body.hidden));
  };
  sideInsert(line, meta.at || one.created_at);
}
async function sideCompactedNow() {
  let said;
  try { said = await api.get("/api/side/conversation"); } catch (_err) { return; }
  (said.messages || []).filter((one) => (one.metadata || {}).origin === "compacted").forEach(sideCompacted);
}

/* A notification told before, as the agent said it then - in its place by when; its live card, if it still waits,
   takes its place (renderNotes). */
function sidePastNote(one) {
  const meta = one.metadata || {};
  if (!SIDE_LOG || !meta.notification_id) return;
  if ([...SIDE_LOG.querySelectorAll(".side-note")].some((node) => node.dataset.note === meta.notification_id)) return;
  const ink = { "Summaries": "summaries", "Recommended content": "recommendations", "Heads up": "headsup" }[meta.category]
    || "notifications";
  // KEPT BEFORE ITS PARTS WERE (2026-11-16: "recommendation content怎么都变成untitled了"): its words as they were said
  // then - the message itself, its first line (for the model) left out.
  const bare = !(meta.title || meta.words || meta.said);
  const spoken = bare ? String(one.content || "").replace(/^\(Sent to the user as a notification:[^)]*\)\s*/, "").trim() : "";
  const line = document.createElement("div");
  line.className = `side-msg agent side-note answered ink-${ink}`;
  line.dataset.note = meta.notification_id;
  line.dataset.who = "agent";
  line.dataset.id = one.id;
  line.dataset.time = notesAt(one.created_at);   // under it, as every other message's
  line.innerHTML = notesCard({ id: meta.notification_id, at: one.created_at, why: meta.why || "",
    title: meta.title || "", detail: meta.words || "", url: meta.url || "", said: meta.said || spoken,
    rules: meta.rules || [], reactions: meta.reactions || [], bad: Boolean(meta.bad_content) },
    meta.category || "");
  line._reactions = [...(meta.reactions || [])];
  line._bad = Boolean(meta.bad_content);
  sideInsert(line, one.created_at);
}

async function sideSend(text) {
  if (sideChat.sending) return;
  const files = sideFilesSent();
  // WHAT THE SMALL SCREEN HOLDS, DRAWN ON, goes with it (side-board.js) - not with a reply to one that waits.
  let board = { ids: [], shown: [], words: "" };
  if (!sideChat.replyTo && typeof sideBoardForMessage === "function") {
    try { board = await sideBoardForMessage(); } catch (err) { toast(err.message, "error"); }
  }
  files.ids.push(...board.ids);
  files.shown.push(...board.shown);
  if (board.words) text = text ? `${text}

${board.words}` : board.words;
  if (!text && !files.ids.length) return;
  // A REPLY TO ONE THAT WAITS (its Reply pressed): said in the chat, and given to it - as its own box gave it.
  if (sideChat.replyTo && text) {
    const { id, action } = sideChat.replyTo;
    sideReplyTo("");
    sideChat.used = true;
    sideSay("you", text);
    notesAnswer(id, action, { text });
    return;
  }
  sideChat.sending = true;
  sideChat.used = true;
  const button = $("#side-send");
  if (button) button.disabled = true;
  // WHAT THE USER MARKED ON THE PICTURE goes with what they say (side-visual.js) - shown with it, so what
  // the agent is told is what they see; the marks then cleared.
  const marked = typeof sideMarksSaid === "function" ? sideMarksSaid() : "";
  if (marked) {
    text = text ? `${text}\n\n${marked}` : marked;
    sideMarksClear();
  }
  const mine = sideSay("you", text || " ");
  sideFilesShown(mine, files.shown);
  const waiting = sideSay("agent", "Thinking…", "working");
  try {
    // From the side panel: the window in front and its picture go with it; Quick or Slow as chosen; the full
    // agent may add a picture (agent/two_speed.py VISUAL_NOTE).
    const said = await api.post("/api/chat/turn", { message: text, conversation_id: sideChat.id || null,
      panel: "side", attachments: files.ids });
    if (said.conversation_id) sideChat.id = said.conversation_id;
    sideShot(mine, said.picture || {});
    if (said.answer) {
      sideWrite(waiting, said.answer, "quick");
      waiting.dataset.message = said.message_id || "";
    }
    if (said.turn) {
      const full = said.answer ? sideSay("agent", "Looking into it…", "working") : waiting;
      if (said.answer) full.dataset.afterQuick = "1";
      sideChat.turns.set(said.turn, full);
      sideTurn(said.turn);                         // done already, it may be
      if (typeof EventSource === "undefined") sideAskAgain(said.turn);
    } else if (!said.answer) {
      waiting.remove();
    }
  } catch (err) {
    sideWrite(waiting, err.message, "failed");
  } finally {
    sideChat.sending = false;
    if (button) button.disabled = false;
  }
}

/* A full turn told to have moved: how it stands, read once. */
async function sideTurn(turn) {
  const line = sideChat.turns.get(turn);
  if (!line) return;
  let state;
  try { state = await api.get(`/api/chat/turn?id=${encodeURIComponent(turn)}`); } catch (_err) { return; }
  if (!sideChat.turns.has(turn)) return;
  if (state.state === "running") {
    if (state.text) { sideWrite(line, sideComing(state.text), "", false); sideToEnd(); }
    return;
  }
  sideChat.turns.delete(turn);
  if (state.state === "done" && state.error) sideWrite(line, state.error, "failed");
  else if (state.state === "done" && state.answer) {
    sideWrite(line, state.answer);
    line.dataset.message = state.message_id || "";
    if (line.dataset.afterQuick) sideFold(line, state);
    sideDid(line, state);
  } else line.remove();                            // nothing to add, or taken up with the next message
  sideToEnd();
}

/* THE FULL AGENT'S ANSWER UNDER A QUICK ONE, AS IN THE BUBBLE: under its line - "More detail" or "A correction",
   and "Questions to answer" when it asks - open (what it tells, a notification among it, is not hidden), folded
   by a press on its line. */
function sideFold(line, state) {
  const body = document.createElement("div");
  body.className = "side-fold-body";
  while (line.firstChild) body.appendChild(line.firstChild);
  const head = document.createElement("button");
  head.type = "button";
  head.className = "side-fold-head";
  // Its words each their own, so that each is translated (i18n.js reads whole words, not a line made of them).
  // (the user, 2026-11-17: "More detail ... 应该写成full model response (或者其它表达)" - "或者改成answer from deeper look")
  const title = state.kind === "correction" ? "A correction" : "Answer from a deeper look";
  head.innerHTML = `<span class="side-fold-mark">▾</span> <span>${escapeHtml(title)}</span>`
    + (state.asks ? ` · <span>${escapeHtml("Questions to answer")}</span>` : "");
  head.setAttribute("aria-expanded", "true");
  head.onclick = () => {
    body.hidden = !body.hidden;
    head.setAttribute("aria-expanded", String(!body.hidden));
    head.querySelector(".side-fold-mark").textContent = body.hidden ? "▸" : "▾";
  };
  line.append(head, body);
}

/* WHAT IT DID, a small grey line each under its answer (the bubble's `_write_said`). */
function sideDid(line, state) {
  const did = state.did || {};
  const said = [...(state.actions || []).map(String)];
  if (did.remembered) said.push("Remembered");
  if (did.quiet) said.push("Blocker added");
  if (did.rules === 1) said.push("1 rule changed");
  else if (did.rules) said.push("{0} rules changed".replace("{0}", String(did.rules)));
  let after = line;
  said.forEach((words) => {
    const one = document.createElement("div");
    one.className = "side-did";
    one.textContent = words;
    after.after(one);
    after = one;
  });
}

/* No line to be told down (a browser without it): asked each second instead, until it is done. */
function sideAskAgain(turn) {
  const again = setInterval(async () => {
    await sideTurn(turn);
    if (!sideChat.turns.has(turn)) clearInterval(again);
  }, 1000);
}

/* What the panel is told as it happens (api/side_events.py): "notes", the list again; "turn", a chat turn.
   The browser keeps the line up by itself, and says the last it had when it comes back. */
const NOTES_LOOK_MS = 60000;
function notesListen() {
  if (typeof EventSource === "undefined") return null;
  const line = new EventSource("/api/side/stream");
  line.addEventListener("notes", () => loadNotes());
  line.addEventListener("compacted", () => sideCompactedNow());
  line.addEventListener("panel", (event) => {
    let said = {};
    try { said = JSON.parse(event.data); } catch (_err) { said = {}; }
    sidePeeking(Boolean(said.peeking));
    // OPENED BY THE USER, AT ITS END (the user, 2026-11-17: "每次打开sidebar, 一开始应该是会放到最下面的") - not when
    // anything else moves it ("不需要自动刷新").
    if (!said.peeking && said.by_user) sideToEndOnceDrawn();
    if (said.peeking) sideLookedAt = 0;          // put away: what comes now waits to be seen
    else if (said.by_user) sideLooked();         // opened by the user: what it shows is seen
    // Its keys pressed while it is pinned: the keys to the chat's box (ui/desktop.py _keys_to_the_side_chat).
    if (said.focus) setTimeout(() => $("#side-input")?.focus({ preventScroll: true }), 0);
  });
  // A PART OF THE SCREEN CUT OUT FOR THE SMALL SCREEN (ui/desktop.py _snip_into_the_board): taken in, or why not said.
  line.addEventListener("board", (event) => {
    let said = {};
    try { said = JSON.parse(event.data); } catch (_err) { said = {}; }
    if (said.error) { toast(said.error, "error"); return; }
    if (typeof sideBoardInbox === "function") sideBoardInbox();
  });
  line.addEventListener("turn", (event) => {
    try { sideTurn(JSON.parse(event.data).id); } catch (_err) { /* asked at the next */ }
  });
  // Back after the line dropped: whatever turn was waiting, read again.
  line.addEventListener("hello", () => sideChat.turns.forEach((_line, turn) => sideTurn(turn)));
  return line;
}

/* FILES AND PICTURES WITH THE NEXT MESSAGE (the user, 2026-11-16: "chat也要输入我的图片"): "+", pasted, or dropped on
   the panel - kept by the agent (agent/chat_files.py) as the chat page's are, sent with the next message. */
const sideFiles = [];
function sideFilesDraw() {
  const row = $("#side-files");
  if (!row) return;
  row.innerHTML = "";
  sideFiles.forEach((one, n) => {
    const chip = document.createElement("div");
    chip.className = "side-file";
    chip.innerHTML = one.picture
      ? `<img src="/api/chat/file?id=${encodeURIComponent(one.id)}" alt="">`
      : `<span>📄 ${escapeHtml(one.name)}</span>`;
    const off = document.createElement("button");
    off.type = "button";
    off.className = "side-file-off";
    off.title = "Remove";
    off.textContent = "×";
    off.onclick = () => {
      sideFiles.splice(n, 1);
      api.post("/api/chat/file/remove", { id: one.id }).catch(() => {});
      sideFilesDraw();
    };
    chip.appendChild(off);
    row.appendChild(chip);
  });
  row.hidden = !sideFiles.length;
}
async function sideAttach(file) {
  if (!file) return;
  const data = await new Promise((done, fail) => {
    const reader = new FileReader();
    reader.onload = () => done(String(reader.result || "").split(",")[1] || "");
    reader.onerror = () => fail(reader.error);
    reader.readAsDataURL(file);
  });
  try {
    const kept = await api.post("/api/chat/upload", { name: file.name || "pasted.png", data });
    sideFiles.push(kept);
    sideFilesDraw();
  } catch (err) { toast(err.message, "error"); }
}
function sideFilesSent() {
  const shown = sideFiles.splice(0, sideFiles.length);
  sideFilesDraw();
  return { ids: shown.map((one) => one.id), shown };
}
/* The files sent with a line, under it - pictures small, pressed to be seen larger. */
function sideFilesShown(line, files) {
  if (!line || !(files || []).length) return;
  const row = document.createElement("div");
  row.className = "side-msg-files";
  files.forEach((one) => {
    const address = `/api/chat/file?id=${encodeURIComponent(one.id)}`;
    if (one.picture) {
      const picture = document.createElement("img");
      picture.src = address;
      picture.alt = one.name || "";
      picture.onclick = () => sideLarger(address);
      row.appendChild(picture);
    } else {
      const link = document.createElement("a");
      link.href = address;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = `📄 ${one.name || ""}`;
      row.appendChild(link);
    }
  });
  line.appendChild(row);
}

/* THE PICTURE OF THE SCREEN THAT WENT WITH IT, under the message - as the bubble showed it: whether it went, and
   why not; none taken, and why. Pressed, seen larger. */
function sideShot(line, picture) {
  if (!line || !picture || !(picture.url || picture.how || picture.withheld)) return;
  const shot = document.createElement("div");
  shot.className = "side-shot";
  const words = picture.withheld
    ? `${"No picture of your screen went with it"} (${picture.withheld})`
    : picture.how === "sent" ? "Sent with it, a picture of your screen:"
      : picture.how === "words" ? "Sent with it, the words read in this picture of your screen (this model sees no pictures):"
        : `${"Not sent with it"}${picture.why ? ` (${picture.why})` : ""}`;
  const said = document.createElement("div");
  said.textContent = words;
  shot.appendChild(said);
  if (picture.url) {
    const small = document.createElement("img");
    small.src = picture.url;
    small.alt = "";
    small.onclick = () => sideLarger(picture.url);
    shot.appendChild(small);
  }
  // IN THE MESSAGE IT WENT WITH (the user, 2026-11-17: "这个picture应该attach在那个chat旁边（包括如果我send with
  // file/attachment, 都应该attach在chat上)"): in its bubble, with its files - not a block of its own under it.
  line.appendChild(shot);
  sideToEnd();
}
/* A picture seen larger, over the panel - a press or Esc and it is gone. */
function sideLarger(address) {
  const over = $("#side-larger");
  if (!over) return;
  over.querySelector("img").src = address;
  sideView.scale = 1;
  sideView.x = 0;
  sideView.y = 0;
  sideViewApply();
  over.hidden = false;
}
/* THE PICTURE IN ITS OWN BOX, ZOOMED BY ITSELF (the user, 2026-11-17: "这个图片点击之后，要打开一个框，zoom in /out 只对图片
   生效"): the wheel (Ctrl or not), + and -, or its buttons zoom it about the pointer; dragged, it moves; 0 or ⤢ fits it
   again; Esc, ×, or a press beside it closes it. The panel's own zoom is not touched while it is open. */
const sideView = { scale: 1, x: 0, y: 0, drag: null, least: 0.2, most: 10 };
function sideViewOpen() {
  const over = $("#side-larger");
  return Boolean(over && !over.hidden);
}
function sideViewApply() {
  const over = $("#side-larger");
  if (!over) return;
  const picture = over.querySelector("img");
  picture.style.transform = `translate(${sideView.x}px, ${sideView.y}px) scale(${sideView.scale})`;
  const said = over.querySelector(".side-larger-scale");
  if (said) said.textContent = `${Math.round(sideView.scale * 100)}%`;
}
function sideViewZoom(by, at) {
  const over = $("#side-larger");
  if (!over) return;
  const picture = over.querySelector("img");
  const zoom = parseFloat(getComputedStyle(document.documentElement).zoom) || 1;
  const box = over.getBoundingClientRect();
  // About the pointer (else the middle): what is under it stays under it.
  const px = ((at ? at.x : box.left + box.width / 2) - box.left) / zoom;
  const py = ((at ? at.y : box.top + box.height / 2) - box.top) / zoom;
  const left = picture.offsetLeft;
  const top = picture.offsetTop;
  const next = Math.min(sideView.most, Math.max(sideView.least, sideView.scale * by));
  const ux = (px - left - sideView.x) / sideView.scale;
  const uy = (py - top - sideView.y) / sideView.scale;
  sideView.x = px - left - ux * next;
  sideView.y = py - top - uy * next;
  sideView.scale = next;
  sideViewApply();
}
function sideViewFit() {
  sideView.scale = 1;
  sideView.x = 0;
  sideView.y = 0;
  sideViewApply();
}
function sideViewSetUp() {
  const over = $("#side-larger");
  if (!over || over.dataset.ready) return;
  over.dataset.ready = "1";
  const picture = over.querySelector("img");
  picture.style.transformOrigin = "0 0";
  over.addEventListener("wheel", (event) => {
    event.preventDefault();
    event.stopPropagation();
    sideViewZoom(event.deltaY < 0 ? 1.15 : 1 / 1.15, { x: event.clientX, y: event.clientY });
  }, { passive: false });
  picture.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    sideView.drag = { x: event.clientX, y: event.clientY, from: [sideView.x, sideView.y] };
    picture.setPointerCapture(event.pointerId);
  });
  picture.addEventListener("pointermove", (event) => {
    if (!sideView.drag) return;
    const zoom = parseFloat(getComputedStyle(document.documentElement).zoom) || 1;
    sideView.x = sideView.drag.from[0] + (event.clientX - sideView.drag.x) / zoom;
    sideView.y = sideView.drag.from[1] + (event.clientY - sideView.drag.y) / zoom;
    sideViewApply();
  });
  picture.addEventListener("pointerup", () => { sideView.drag = null; });
  picture.addEventListener("dblclick", sideViewFit);
  over.addEventListener("click", (event) => {
    const button = event.target.closest && event.target.closest("[data-view]");
    if (button) {
      const what = button.dataset.view;
      if (what === "in") sideViewZoom(1.25);
      else if (what === "out") sideViewZoom(1 / 1.25);
      else if (what === "fit") sideViewFit();
      else over.hidden = true;
      return;
    }
    if (event.target === over) over.hidden = true;     // a press beside the picture
  });
}

/* WHAT IS IN FRONT OF THE USER, GIVEN WITH WHAT IS SAID (the bubble's "On your screen, given as context:"): one line
   under the head, while the panel is seen. */
const SIDE_FRONT_MS = 3000;
async function sideFront() {
  if (document.hidden) return;
  let said;
  try { said = await api.get("/api/side/front"); } catch (_err) { return; }
  const head = document.querySelector("#notes-page .side-head");
  if (!head) return;
  let line = $("#side-front");
  if (!line) {
    line = document.createElement("div");
    line.id = "side-front";
    line.className = "side-front";
    ($("#side-private") || head).after(line);
  }
  const shown = String(said.line || "");
  // NOT SHOWN (the user, 2026-11-17: "On your screen, given as context: ... 这一行不用显示了"): what is in front still
  // goes with each message - only the line saying so is gone; kept, hidden, for its title.
  line.hidden = true;
  line.textContent = shown ? "On your screen, given as context: {0}".replace("{0}", shown) : "";
  line.title = line.textContent;
}

/* FOLLOW-UPS, THE CHAT'S "ASK ABOUT" (the user, 2026-11-16: "和你那个chat一样，我可以点击inside a chat, 然后会有sticker
   写在旁边"): words selected in an answer - or all of it - asked about on their own; the answer a card under it,
   closed to delete it. */
/* ASKED ABOUT, BESIDE WHAT WAS ASKED (the user, 2026-11-17: "这个问题的答案应该是可以collapose的 ... agent的回答的右边
   不是空一点吗，ask about 的位置拉出一根线对应具体的question ... 必须要到ask about的那个内容的位置, 而不是在整个message下"):
   each a small card in the empty room right of the reply, at the height of the words it asks about, a line drawn from
   those words to it; folded to its question, opened over the reply to its answer. */
function sideAskLines() {
  let lines = SIDE_LOG.querySelector(":scope > svg.side-fu-lines");
  if (!lines) {
    lines = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    lines.setAttribute("class", "side-fu-lines");
    lines.setAttribute("aria-hidden", "true");
    SIDE_LOG.appendChild(lines);
  }
  return lines;
}
/* The words asked about, marked where they are - from what was picked, else found in the reply; none, the reply. */
function sideAnchor(line, about, range) {
  if (range) {
    try {
      const mark = document.createElement("mark");
      mark.className = "side-fu-anchor";
      range.surroundContents(mark);
      return mark;
    } catch (_err) { /* across its parts: found by its words below */ }
  }
  const words = String(about || "").trim();
  if (!words) return null;
  const walker = document.createTreeWalker(line, NodeFilter.SHOW_TEXT, { acceptNode: (node) =>
    node.parentElement && node.parentElement.closest(".side-extras, .side-react-row, .side-note-head, mark, button")
      ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT });
  const probe = words.slice(0, 80);
  while (walker.nextNode()) {
    const node = walker.currentNode;
    const at = node.textContent.indexOf(probe);
    if (at < 0) continue;
    const range2 = document.createRange();
    range2.setStart(node, at);
    range2.setEnd(node, at + probe.length);
    const mark = document.createElement("mark");
    mark.className = "side-fu-anchor";
    range2.surroundContents(mark);
    return mark;
  }
  return null;
}
let sideAskedFrame = 0;
function sideLayoutAskedSoon() {
  cancelAnimationFrame(sideAskedFrame);
  sideAskedFrame = requestAnimationFrame(sideLayoutAsked);
}
function sideLayoutAsked() {
  if (!SIDE_LOG) return;
  const cards = [...SIDE_LOG.querySelectorAll(":scope > .side-followup")].filter((card) => card._line?.isConnected);
  const lines = sideAskLines();
  lines.innerHTML = "";
  if (!cards.length) return;
  const box = SIDE_LOG.getBoundingClientRect();
  const zoom = parseFloat(getComputedStyle(document.documentElement).zoom) || 1;
  // As large as the chat (CSS), never sized by what it holds: sized by the chat's scroll height, it grew by the zoom
  // each time it was laid out, and the chat scrolled into blank room (the user, 2026-11-17: "view直接刷到最底下看不见
  // 东西的地方了").
  const placed = cards.map((card) => {
    const anchor = card._anchor && card._anchor.isConnected ? card._anchor : null;
    const parts = anchor ? [...anchor.getClientRects()] : [];
    const end = parts.length ? parts[parts.length - 1] : card._line.getBoundingClientRect();
    return { card, anchor, end, want: (end.top - box.top) / zoom };
  }).sort((a, b) => a.want - b.want);
  let below = 0;
  const width = box.width / zoom;
  placed.forEach(({ card, anchor, end, want }) => {
    const bubble = card._line.getBoundingClientRect();
    const room = Math.max(72, width - (bubble.right - box.left) / zoom - 18);
    const open = card.classList.contains("open");
    const wide = open ? Math.min(width * 0.82, 380) : room;
    const left = width - wide - 4;
    const top = Math.max(want - 4, below);
    card.style.left = `${Math.round(left)}px`;
    card.style.top = `${Math.round(top)}px`;
    card.style.width = `${Math.round(wide)}px`;
    // Stacked by their folded heads: one opened lies over the others, never pushing them away.
    below = top + (card.querySelector(".side-fu-head")?.offsetHeight || card.offsetHeight) + 6;
    // The line: under the words asked about, along their line to the reply's edge - never through the words after
    // them - and on to the card.
    const edge = (bubble.right - box.left) / zoom - 6;
    const x0 = anchor ? (end.left - box.left) / zoom : edge;
    const y0 = anchor ? (end.bottom - box.top) / zoom + 1 : (bubble.top - box.top) / zoom + 12;
    const x2 = left;
    const y2 = top + 12;
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    const bend = Math.max(10, (x2 - edge) / 2);
    path.setAttribute("d", `M ${x0} ${y0} L ${edge} ${y0} C ${edge + bend} ${y0}, ${x2 - bend} ${y2}, ${x2} ${y2}`);
    path.setAttribute("class", open ? "open" : "");
    lines.appendChild(path);
  });
}
function sideFollowUpCard(line, about, question, answer = "", ident = "", range = null) {
  const card = document.createElement("div");
  card.className = "side-followup";
  card.dataset.id = ident;
  card._line = line;
  card._anchor = sideAnchor(line, about, range);
  card.innerHTML = `<button type="button" class="side-fu-head" aria-expanded="false"><span class="side-fu-mark">?</span>
      <span class="side-fu-question chat-message"></span></button>
    <div class="side-fu-open" hidden><div class="side-fu-about chat-message"></div><div class="side-fu-body chat-message"></div>
      <button type="button" class="side-fu-delete" title="Delete this follow-up">×</button></div>`;
  card.querySelector(".side-fu-question").textContent = question;
  card.querySelector(".side-fu-about").textContent = about ? `“${about.slice(0, 80)}”` : "Whole reply";
  card.querySelector(".side-fu-body").textContent = answer || "thinking…";
  const head = card.querySelector(".side-fu-head");
  // Folded to its question; pressed, opened to its answer over the reply - pressed again, folded.
  head.onclick = (event) => {
    event.stopPropagation();
    const open = !card.classList.contains("open");
    card.classList.toggle("open", open);
    card.querySelector(".side-fu-open").hidden = !open;
    head.setAttribute("aria-expanded", String(open));
    card._anchor?.classList.toggle("lit", open);
    sideLayoutAsked();
  };
  card.querySelector(".side-fu-delete").onclick = (event) => {
    event.stopPropagation();
    card.remove();
    const mark = card._anchor;
    if (mark && mark.isConnected) mark.replaceWith(...mark.childNodes);
    sideLayoutAsked();
    if (card.dataset.id) api.post("/api/chat/followup/delete", { id: card.dataset.id }).catch(() => {});
  };
  SIDE_LOG.appendChild(card);
  sideLayoutAskedSoon();
  return card;
}
function head_open(card) {
  const head = card.querySelector(".side-fu-head");
  if (head && !card.classList.contains("open")) head.click();
}
function sideAskForm(line, about) {
  line.querySelector(".side-fu-form")?.remove();
  const asked = `what is ${about || "this"}`;
  const form = document.createElement("form");
  form.className = "side-fu-form";
  form.innerHTML = `<input type="text" autocomplete="off"><button type="submit" class="small">Ask</button>
    <button type="button" class="small secondary" title="Cancel">×</button>`;
  const input = form.querySelector("input");
  input.placeholder = `${asked.slice(0, 60)}  (Tab to fill in)`;
  input.addEventListener("keydown", (event) => {
    if (event.key === "Tab" && !event.shiftKey && !input.value.trim()) { event.preventDefault(); input.value = asked; }
    if (event.key === "Escape") form.remove();
  });
  form.querySelector("button[type=button]").onclick = () => form.remove();
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const question = input.value.trim() || asked;
    form.remove();
    const card = sideFollowUpCard(line, about, question, "", "", line._askRange || null);
    line._askRange = null;
    head_open(card);                             // its answer, opened as it comes
    try {
      const said = await api.post("/api/chat/followup", { message_id: line.dataset.message || line.dataset.id || "",
        note: line.dataset.note || "", about, question });
      card.dataset.id = said.id || "";
      card.querySelector(".side-fu-body").textContent = said.answer || "";
    } catch (err) {
      card.querySelector(".side-fu-body").textContent = `Error: ${err.message}`;
    }
    sideLayoutAsked();
  });
  line.appendChild(form);
  input.focus({ preventScroll: true });
}
function sideAskPick() {
  document.querySelector(".side-ask")?.remove();
  const picked = window.getSelection();
  const text = picked ? picked.toString().trim() : "";
  if (!text || !picked.rangeCount) return;
  const range = picked.getRangeAt(0);
  const node = range.commonAncestorContainer;
  // WHAT THE AGENT TOLD OF ITSELF TOO (the user, 2026-11-17: "recommended content 好像不能ask about?"): a
  // notification, a summary, a recommendation - by its message, or by the notification it said.
  const line = (node.nodeType === 1 ? node : node.parentElement)?.closest(".side-msg.agent");
  if (!line || !(line.dataset.message || line.dataset.id || line.dataset.note)) return;
  const whole = text === line.textContent.trim();
  const at = range.getBoundingClientRect();
  const button = document.createElement("button");
  button.type = "button";
  button.className = "small side-ask";
  button.textContent = "Ask about";
  button.onmousedown = (event) => event.preventDefault();
  const asked = whole ? null : range.cloneRange();
  button.onclick = () => { button.remove(); line._askRange = asked; sideAskForm(line, whole ? "" : text.slice(0, 300)); };
  button.style.visibility = "hidden";
  document.body.appendChild(button);
  // RIGHT BY WHAT WAS PICKED (the user, 2026-11-17: "ask about 按钮离的和选中text不够近"): just above its first line,
  // by the button's own height - under its last where there is no room above; in the page's pixels, its zoom taken off.
  const room = button.getBoundingClientRect();
  const zoom = parseFloat(getComputedStyle(document.documentElement).zoom) || 1;
  const left = Math.max(4, Math.min(window.innerWidth - room.width - 4, at.left));
  const top = at.top - room.height - 4 >= 4 ? at.top - room.height - 4 : at.bottom + 4;
  button.style.left = `${left / zoom}px`;
  button.style.top = `${top / zoom}px`;
  button.style.visibility = "";
}

if (NOTES_SOLO) {
  document.documentElement.classList.add("notes-solo");
  document.body.classList.add("notes-solo");
  // The agent's name in it: the listener knows the agent's own windows by it.
  document.title = "Waiting notifications - Personal Agent WaterMelon";
  $("#notes-page").hidden = false;
  // DOWN THE WHOLE RIGHT SIDE OF THE SCREEN, measured by the page, in the window's own units - unless
  // the desktop put it there itself, before it was ever seen ("placed": moved here again, it jumped).
  if (!new URLSearchParams(location.search).get("placed")) try {
    const w = Math.min(460, screen.availWidth), h = screen.availHeight;
    window.resizeTo(w, h);
    window.moveTo((screen.availLeft || 0) + screen.availWidth - w, screen.availTop || 0);
  } catch (_err) { /* a browser that does not let it: left as it opened */ }
  renderNotes(true);
  loadNotes(true);
  sideLoad();
  sideGrip();
  sideZoom();
  const box = $("#side-input");
  const fit = () => {
    if (!box) return;
    // As tall as the user made it, taller with a long message - to the most it may be, a bar only past that.
    const chosen = sideInput.chosen, most = Math.max(SIDE_INPUT.grows, chosen);
    box.style.height = "auto";
    const want = Math.max(chosen, Math.min(box.scrollHeight, most));
    box.style.height = `${want}px`;
    box.style.overflowY = box.scrollHeight > want + 1 ? "auto" : "hidden";
  };
  sideInputGrip(box, fit);
  $("#side-compose")?.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = box.value.trim();
    if ((!text && !sideFiles.length) || sideChat.sending) return;
    box.value = "";
    fit();
    sideSend(text);
  });
  // FILES AND PICTURES: "+", pasted, dropped on the panel.
  $("#side-attach")?.addEventListener("click", () => $("#side-file")?.click());
  $("#side-file")?.addEventListener("change", (event) => {
    [...(event.target.files || [])].forEach((one) => sideAttach(one));
    event.target.value = "";
  });
  document.addEventListener("paste", (event) => {
    const files = [...((event.clipboardData && event.clipboardData.files) || [])];
    if (!files.length) return;                      // words pasted stay words
    event.preventDefault();
    files.forEach((one) => sideAttach(one));
  });
  document.addEventListener("dragover", (event) => {
    if ([...(event.dataTransfer?.types || [])].includes("Files")) {
      event.preventDefault();
      document.body.classList.add("side-dropping");
    }
  });
  document.addEventListener("dragleave", (event) => {
    if (!event.relatedTarget) document.body.classList.remove("side-dropping");
  });
  document.addEventListener("drop", (event) => {
    document.body.classList.remove("side-dropping");
    const files = [...(event.dataTransfer?.files || [])];
    if (!files.length) return;
    event.preventDefault();
    files.forEach((one) => sideAttach(one));
  });
  // QUICK, THEN SLOW - AS THE CHAT (the user, 2026-11-16: "我说的quick/slow是和chat一样的，quick mode, 可以支持slow mode慢慢弄"):
  // a short answer at once, the full agent's after it when it has more - no choosing between them.
  // A PICTURE SEEN LARGER: a press or Esc closes it.
  sideViewSetUp();
  // ASK ABOUT: words selected in an answer.
  document.addEventListener("mouseup", () => setTimeout(sideAskPick, 0));
  // WHAT IS IN FRONT, kept up while the panel is seen.
  sideFront();
  setInterval(sideFront, SIDE_FRONT_MS);
  // TOUCHED: the desktop leaves a panel a notification opened where it is once the user has used it.
  let touchedAt = 0;
  const touched = () => {
    sideLooked();                                  // pressed or typed in: what it shows is seen
    if (Date.now() - touchedAt < 10000) return;
    touchedAt = Date.now();
    api.post("/api/side/touched", {}).catch(() => {});
  };
  document.addEventListener("pointerdown", touched, true);
  document.addEventListener("keydown", touched, true);
  document.addEventListener("wheel", () => sideLooked(), { capture: true, passive: true });
  box?.addEventListener("input", fit);
  $("#side-visual-close")?.addEventListener("click", () => sideHideView());
  $("#side-replying-off")?.addEventListener("click", () => sideReplyTo(""));
  box?.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {   // not while a Chinese word is being made
      event.preventDefault();
      $("#side-compose").requestSubmit();
    }
  });
  // TOLD, NOT ASKING (api/side_events.py; the user, 2026-11-16: quick, and not slowing the computer): the
  // list drawn again the moment it changes; looked at besides only once a minute, should the line drop.
  notesListen();
  setInterval(() => { if (!document.hidden) loadNotes(); }, NOTES_LOOK_MS);
  window.addEventListener("focus", () => loadNotes());
  // THE KEYS IN THE BOX WHEN THE PANEL COMES TO THE FRONT (the user, 2026-11-17: "第一次按钮expand, 焦点会在search上"): its
  // host gives the page the keys as its window is activated, and with nothing chosen yet they went to the first
  // button, the search's - the box has them instead. Not when the user pressed something of the panel's.
  let sidePressedAt = 0;
  document.addEventListener("pointerdown", () => { sidePressedAt = Date.now(); }, true);
  window.addEventListener("focus", () => setTimeout(() => {
    if (Date.now() - sidePressedAt < 400) return;
    const now = document.activeElement;
    if (!now || now === document.body || now.id === "side-search-open") $("#side-input")?.focus({ preventScroll: true });
  }, 30));
  // SLID BACK IN: what waits now, at once.
  // UNCOVERED AGAIN (another window over it, then not - WebView2 calls that hidden): what waits looked at, nothing drawn
  // again nor moved (the user, 2026-11-17: "现在好像会自动刷新view(chat的), 不需要自动刷新").
  document.addEventListener("visibilitychange", () => { if (!document.hidden) { loadNotes(); sideFront(); } });
  // ITS LINKS IN THE USER'S OWN BROWSER, not a window of the panel's own (kept off the screen, signed in
  // nowhere: "open the page 弹出来的google chrome没法显示").
  document.addEventListener("click", async (event) => {
    const link = event.target.closest && event.target.closest("#notes-page a[href^='http']");
    if (!link) return;
    event.preventDefault();
    let said = null;
    try { said = await api.post("/api/notifications/open_link", { url: link.href }); } catch (_err) { said = null; }
    if (!said || !said.opened) window.open(link.href, "_blank", "noopener");
  });
  document.addEventListener("click", (event) => {
    const at = event.target.closest && event.target.closest("#notes-page button, #side-quick button");
    if (!at) return;
    if (at.id === "notes-close") { closeNotes(); return; }
    if (at.id === "side-pin") { sidePinned(at.getAttribute("aria-pressed") !== "true"); return; }
    if (at.dataset.notesReplyto) { sideReplyTo(at.dataset.notesReplyto); return; }
    if (at.dataset.notesOpen) {
      const id = at.dataset.notesOpen;
      if (notesState.open.has(id)) notesState.open.delete(id); else notesState.open.add(id);
      renderNotes(true);
      return;
    }
    if (at.dataset.notesGo) {
      api.post("/api/notifications/go_to", { id: at.dataset.notesGo })
        .then((said) => { if (!said.opened) toast("Open the report from the agent's window - it could not be opened from here.", "error"); })
        .catch((err) => toast(err.message, "error"));
      return;
    }
    if (at.dataset.notesAct) sideQuickAnswer(at.dataset.notesAct, at.dataset.key);
  });
  document.addEventListener("submit", (event) => {
    const form = event.target.closest && event.target.closest("[data-notes-reply]");
    if (!form) return;
    event.preventDefault();
    const text = form.querySelector("textarea").value.trim();
    const row = notesRow(form.dataset.notesReply);
    const action = row && (row.actions || []).find((one) => one.writes);
    if (text && action) notesAnswer(row.id, action, { text });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && $("#side-larger") && !$("#side-larger").hidden) { $("#side-larger").hidden = true; return; }
    if (event.key === "Escape" && !event.target.closest(".notes-reply, .side-fu-form")) closeNotes();
    const box = event.target.closest && event.target.closest(".notes-reply textarea");
    if (box && event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      box.form.requestSubmit();
    }
  });
  document.addEventListener("input", (event) => {
    if (event.target.id !== "notes-q") return;
    notesState.q = event.target.value.trim().toLowerCase();
    renderNotes(true);
  });
}
