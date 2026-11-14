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

function notesAt(at) {
  if (!at) return "";
  const when = new Date(at);
  if (Number.isNaN(when.getTime())) return "";
  const today = new Date().toDateString() === when.toDateString();
  return today ? when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : localStamp(at);
}

function notesCard(row, kind) {
  const open = notesState.open.has(row.id) || !row.folds;
  const words = String(row.detail || "");
  const shown = notesState.q ? `${row.title} ${words}`.toLowerCase().includes(notesState.q) : true;
  if (!shown) return "";
  const answers = (row.actions || []).filter((one) => !one.writes).map((one, n) => `<button type="button"
      class="small${n ? " secondary" : ""}" data-notes-act="${escapeHtml(row.id)}" data-key="${escapeHtml(one.key)}">${escapeHtml(one.label)}</button>`).join("");
  const reply = (row.actions || []).find((one) => one.writes);
  return `<li class="notes-card${open ? " open" : ""}${notesState.leaving.has(row.id) ? " leaving" : ""}">
    <button type="button" class="notes-card-head" data-notes-open="${escapeHtml(row.id)}" aria-expanded="${open}">
      <span class="notes-kind">${escapeHtml(kind)}</span>
      <span class="notes-when">${escapeHtml(notesAt(row.at))}</span>
      <span class="notes-title">${escapeHtml(row.title || "(untitled)")}</span>
      ${open ? "" : `<span class="notes-preview">${escapeHtml(words.replace(/\s+/g, " ").slice(0, 220))}</span>`}
    </button>
    ${open ? `<div class="notes-body">
      <div class="notes-text">${linkify(escapeHtml(words))}</div>
      ${row.url ? `<p><a href="${escapeHtml(row.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml("Open the page")}</a></p>` : ""}
      ${row.why ? `<p class="notes-why">${escapeHtml(row.why)}</p>` : ""}
      ${row.note ? `<p class="notes-why">${escapeHtml(row.note)}</p>` : ""}
      <div class="notes-answers">${answers}</div>
      ${reply ? `<form class="report-chat notes-reply" data-notes-reply="${escapeHtml(row.id)}">
        <textarea rows="1" placeholder="${escapeHtml((reply.fields || [])[0] || "Reply")}"></textarea>
        <button type="submit" class="small">${escapeHtml(reply.yes || "Send")}</button></form>` : ""}
    </div>` : ""}
  </li>`;
}

function linkify(html) {
  return html.replace(/https?:\/\/[^\s<]+/g, (url) => `<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`);
}

function renderNotes(force = false) {
  const page = $("#notes-page");
  if (!page) return;
  const typing = page.querySelector(".notes-reply textarea:focus");
  const drawn = JSON.stringify([notesState.sections, [...notesState.open], [...notesState.leaving], notesState.q]);
  if (!force && (drawn === notesState.drawn || typing)) return;
  notesState.drawn = drawn;
  const count = notesState.sections.reduce((n, part) => n + (part.rows || []).length, 0);
  const lists = notesState.sections.map((part) => {
    const cards = (part.rows || []).map((row) => notesCard(row, part.name)).join("");
    return cards ? `<h3 class="notes-group">${escapeHtml(part.name)}</h3><ul class="notes-list">${cards}</ul>` : "";
  }).join("");
  const search = $("#notes-q");
  const searchHad = Boolean(search) && search === document.activeElement;
  page.innerHTML = `<header class="notes-head">
      <div><h2 class="notes-heading">${escapeHtml("Waiting notifications")}</h2>
        <span class="muted">${count ? escapeHtml("{0} waiting").replace("{0}", String(count)) : ""}</span></div>
      ${NOTES_SOLO ? "" : `<button type="button" class="report-drawer-close" id="notes-close" aria-label="${escapeHtml("Close")}">×</button>`}
    </header>
    <div class="notes-search"><input id="notes-q" type="search" placeholder="${escapeHtml("Search")}" value="${escapeHtml(notesState.q)}"></div>
    <div class="notes-scroll">${lists || `<p class="notes-empty">${escapeHtml("Nothing is waiting.")}</p>`}</div>`;
  if (searchHad) { const box = $("#notes-q"); box.focus(); box.setSelectionRange(box.value.length, box.value.length); }
}

async function loadNotes(force = false) {
  try { notesState.sections = (await api.get("/api/notifications/waiting_list")).sections || []; }
  catch (_err) { return; }
  const here = new Set(notesState.sections.flatMap((part) => (part.rows || []).map((row) => row.id)));
  notesState.leaving.forEach((id) => { if (!here.has(id)) notesState.leaving.delete(id); });
  renderNotes(force);
  // THE LAST ONE ANSWERED, IT GOES (the user, 2026-11-13: "waiting notification没有内容了之后不会自动关闭了") -
  // opened with nothing in it, it stays.
  // ITS LAST ONE GONE, HOWEVER IT WENT (the user, 2026-11-13: "这个好像消息结束了没有自动关闭窗口" - one answered
  // beside the avatar or in the report left it open, empty): opened empty, it stays.
  if (here.size) notesState.hadSome = true;
  if ((notesState.answered || notesState.hadSome) && !here.size) {
    notesState.answered = false;
    notesState.hadSome = false;
    closeNotes();
  }
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
  setInterval(() => { if (!document.hidden) loadNotes(); }, 8000);
  window.addEventListener("focus", () => loadNotes());
  // SLID BACK IN: what waits now, at once.
  document.addEventListener("visibilitychange", () => { if (!document.hidden) loadNotes(true); });
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
    const at = event.target.closest && event.target.closest("#notes-page button");
    if (!at) return;
    if (at.id === "notes-close") { closeNotes(); return; }
    if (at.dataset.notesOpen) {
      const id = at.dataset.notesOpen;
      if (notesState.open.has(id)) notesState.open.delete(id); else notesState.open.add(id);
      renderNotes(true);
      return;
    }
    if (at.dataset.notesAct) {
      const row = notesRow(at.dataset.notesAct);
      const action = row && (row.actions || []).find((one) => one.key === at.dataset.key);
      if (action) notesAnswer(row.id, action);
    }
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
    if (event.key === "Escape" && !event.target.closest(".notes-reply")) closeNotes();
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
