/* THE CONVERSATION - one, the same wherever it is opened: in a window of
   its own (chat.html, the desktop agent clicked once) and in the main page's
   Chat tab. Each page gives it the way it asks the agent (`api`) and says
   things (`toast`); everything else - the conversation, its sidebar
   (chat-navigation.js), the stages shown while the agent works, and the
   follow-ups ("Ask about") in the margin - is here, once, so the two can
   never come to differ again. */
"use strict";

window.createChat = function ({ api, toast }) {
const $ = (selector) => document.querySelector(selector);
if (!$("#chat-log") || !$("#chat-form")) return null;
const escapeHtml = (text) => String(text).replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* Section 15.1: while the agent works, show what it is doing.

   A request that takes twenty seconds and says only "thinking" is
   indistinguishable from one that has hung. The stages are polled while the
   answer is awaited, and the panel disappears when it arrives - it describes
   work in progress, and finished work is described by the answer itself. */
function progressPanel(host) {
  let stop = false;
  let element = null;

  const render = (steps, seconds) => {
    if (!element) {
      element = document.createElement("div");
      element.className = "progress-panel";
      host.appendChild(element);
    }
    const rows = steps.map((step, index) => {
      const done = index < steps.length - 1;
      const took = step.finished_at && step.started_at
        ? ((Date.parse(step.finished_at) - Date.parse(step.started_at)) / 1000)
        : null;
      return `<li class="${done ? "done" : "doing"}">
        <span class="progress-mark">${done ? "✓" : "•"}</span>
        <span class="progress-stage">${escapeHtml(step.stage)}</span>
        ${step.detail ? `<span class="muted">${escapeHtml(step.detail)}</span>` : ""}
        ${took !== null && took >= 0.1
          ? `<span class="muted progress-took">${took.toFixed(1)}s</span>` : ""}</li>`;
    }).join("");
    element.innerHTML = `<div class="progress-head muted">Working…
      <span class="progress-elapsed">${seconds.toFixed(0)}s</span></div>
      <ol class="progress-steps">${rows}</ol>`;
    host.scrollTop = host.scrollHeight;
  };

  /* THE REPLY AS IT IS WRITTEN (the user, 2026-11-04), read with the progress;
     the whole one takes its place when it comes. */
  let live = null;
  const write = (text) => {
    if (!live) {
      live = document.createElement("div");
      live.classList.add("msg", "assistant", "is-live");
      host.appendChild(live);
    }
    live.textContent = text;
    host.scrollTop = host.scrollHeight;
  };

  const started = Date.now();
  (async () => {
    // A first answer can arrive before any stage is recorded; the panel only
    // appears once there is something to say.
    while (!stop) {
      try {
        const state = await fetch("/api/agent/progress?origin=user",
                                  { headers: { Accept: "application/json" } })
          .then((r) => r.json());
        if (stop) break;
        if (state.active && (state.steps || []).length) {
          render(state.steps, (Date.now() - started) / 1000);
        }
        if (state.active && state.text) write(state.text);
      } catch (err) {
        /* the panel is an aid, never a reason for the chat to fail */
      }
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
  })();

  return {
    finish() {
      stop = true;
      if (element) element.remove();
      if (live) live.remove();
    },
  };
}

let conversationId = null;
/* The sidebar (chat-navigation.js), when the page has it. Everything below
   works without it - a page that never loaded it is one conversation. */
const chatNavigation = window.createChatNavigation?.({ api, onOpen: showConversation });

/* A conversation drawn again as it was left: every message - each reply
   with its id, so it can still be asked about - and the follow-ups already
   asked, back in the margin beside the words they were about. */
function showConversation(conversation) {
  conversationId = conversation.conversation_id || null;
  // A citation card the main page has open (app.js), closed with it.
  if (typeof window.closeCitation === "function") window.closeCitation();
  hideFollowUpPick();
  $("#chat-log").replaceChildren();
  $("#chat-meta").textContent = "";
  $("#chat-input").value = "";
  chatFiles.clear();
  chatMentions.length = 0;
  hideMentions();
  for (const message of conversation.messages || []) {
    for (const one of (message.metadata || {}).attachments || []) chatFiles.set(one.id, one);
  }
  const asked = new Map();
  for (const one of conversation.followups || []) {
    if (!asked.has(one.message_id)) asked.set(one.message_id, []);
    asked.get(one.message_id).push(one);
  }
  let quick = null;
  for (const message of conversation.messages || []) {
    const reply = message.role === "assistant";
    const meta = message.metadata || {};
    if (meta.layer === "slow") {
      // Under the quick reply it follows, as it was when it came.
      if (message.content || meta.did) {
        const fold = chatFold(quick);
        chatFoldDone(fold, message.content, message.id, meta.kind, meta.did);
        if (message.content && asked.has(message.id)) {
          restoreFollowUps(fold.querySelector(".msg"), asked.get(message.id));
        }
      }
      continue;
    }
    const el = appendMessage(message.role, message.content, reply ? message.id : "");
    if (reply && asked.has(message.id)) restoreFollowUps(el, asked.get(message.id));
    drawAttached(el, meta.attachments);
    if (meta.layer === "fast") el.classList.add("layer-fast");
    quick = meta.layer === "fast" ? el : null;
  }
  layoutFollowUps();
  $("#chat-log").scrollTop = $("#chat-log").scrollHeight;
}

/* FILES AND PICTURES ATTACHED (the user, 2026-11-04: "要和gpt一样支持上传附件或者
   图片"): "+" beside the box, dropped anywhere on the chat, or pasted
   - each shown above the box until it is sent, and with its message after. */
const CHAT_MOST_FILES = 10;
const CHAT_MOST_MB = 25;
const chatAttached = [];                  // {id, name, picture, preview} - or uploading: true
const chatFiles = new Map();              // the files sent in this conversation, by id
const chatMentions = [];                  // the files @ed in what is being written
const chatAttachRow = document.createElement("div");
chatAttachRow.className = "chat-attached";
chatAttachRow.hidden = true;
const chatFilePicker = document.createElement("input");
chatFilePicker.type = "file";
chatFilePicker.multiple = true;
chatFilePicker.hidden = true;
const chatClip = document.createElement("button");
chatClip.type = "button";
chatClip.classList.add("secondary", "chat-clip");
chatClip.textContent = "+";
chatClip.title = "Attach a file or a picture";
chatClip.setAttribute("aria-label", "Attach a file or a picture");
$("#chat-form").prepend(chatClip, chatFilePicker);
$("#chat-form").before(chatAttachRow);
chatClip.addEventListener("click", () => chatFilePicker.click());
chatFilePicker.addEventListener("change", () => {
  chatAttach([...chatFilePicker.files]);
  chatFilePicker.value = "";
});

function drawChatAttached() {
  chatAttachRow.hidden = !chatAttached.length;
  chatAttachRow.replaceChildren(...chatAttached.map((one) => {
    const chip = document.createElement("span");
    chip.className = "chat-file-chip";
    chip.title = one.name;
    /* A PICTURE IS SEEN BEFORE IT IS SENT (the user, 2026-11-04): large, and at
       once - from the file itself, before the upload has finished. */
    const seen = one.preview || (one.picture && one.id
      ? `/api/chat/file?id=${encodeURIComponent(one.id)}` : "");
    const name = document.createElement("span");
    if (seen) {
      chip.classList.add("is-picture");
      chip.classList.toggle("is-uploading", Boolean(one.uploading));
      const picture = document.createElement("img");
      picture.src = seen;
      picture.alt = one.name;
      chip.append(picture);
    } else {
      name.textContent = one.uploading ? `${one.name} …` : one.name;
      chip.append(name);
    }
    const gone = document.createElement("button");
    gone.type = "button";
    gone.textContent = "×";
    gone.title = "Remove";
    gone.setAttribute("aria-label", "Remove");
    gone.addEventListener("click", () => {
      chatAttached.splice(chatAttached.indexOf(one), 1);
      one.removed = true;
      chatLetGo(one);
      drawChatAttached();
    });
    chip.append(gone);
    return chip;
  }));
}

/* Taken off: what it was being read for stops, and it goes - once it is there:
   one still on its way is let go when it arrives. */
function chatLetGo(one) {
  if (one.id) api.post("/api/chat/file/remove", { id: one.id }).catch(() => {});
}

async function chatAttach(files) {
  for (const file of files) {
    if (chatAttached.length >= CHAT_MOST_FILES) {
      $("#chat-meta").textContent = `At most ${CHAT_MOST_FILES} files in one message`;
      break;
    }
    if (file.size > CHAT_MOST_MB * 1024 * 1024) {
      $("#chat-meta").textContent = `A file is at most ${CHAT_MOST_MB} MB`;
      continue;
    }
    const one = { name: file.name || "picture.png", uploading: true };
    if ((file.type || "").startsWith("image/")) one.preview = URL.createObjectURL(file);
    chatAttached.push(one);
    drawChatAttached();
    try {
      const data = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(",")[1] || "");
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(file);
      });
      Object.assign(one, await api.post("/api/chat/upload", { name: one.name, data }), { uploading: false });
      if (one.removed) chatLetGo(one);
    } catch (err) {
      if (chatAttached.includes(one)) chatAttached.splice(chatAttached.indexOf(one), 1);
      $("#chat-meta").textContent = err.message;
    }
    drawChatAttached();
  }
}

/* DRAGGED ON, AS IN GOOGLE DRIVE (the user, 2026-11-04): the moment a file
   comes over the page all of the chat turns into where to let it go. Let go
   anywhere - beside the chat too: the browser would leave the page to open it. */
const chatDropHost = $("#chat-form").closest(".chat-layout") || $("#chat-form");
chatDropHost.classList.add("chat-drop-host");
const chatDropCover = document.createElement("div");
chatDropCover.className = "chat-drop-cover";
chatDropCover.hidden = true;
chatDropCover.innerHTML = `<div class="chat-drop-card">
  <svg viewBox="0 0 24 24" width="30" height="30" fill="none" stroke="currentColor" stroke-width="2"
    stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <path d="M12 16V5"/><path d="M7 10l5-5 5 5"/><path d="M5 19h14"/></svg>
</div><strong></strong><span></span>`;
chatDropCover.querySelector("strong").textContent = "Drop files to add them";
chatDropCover.querySelector("span").textContent =
  `Pictures, PDFs and documents, up to ${CHAT_MOST_MB} MB each`;
chatDropHost.append(chatDropCover);

const chatFilesDragged = (event) => Boolean($("#chat-form").offsetParent)
  && [...(event.dataTransfer?.types || [])].includes("Files");
for (const kind of ["dragenter", "dragover"]) {
  document.addEventListener(kind, (event) => {
    if (!chatFilesDragged(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    chatDropCover.hidden = false;
  });
}
document.addEventListener("dragleave", (event) => {
  if (!event.relatedTarget) chatDropCover.hidden = true;     // out of the window
});
document.addEventListener("dragend", () => { chatDropCover.hidden = true; });
document.addEventListener("drop", (event) => {
  chatDropCover.hidden = true;
  if (!chatFilesDragged(event)) return;
  event.preventDefault();
  chatAttach([...(event.dataTransfer?.files || [])]);
  $("#chat-input").focus();
});

/* PASTED, WHATEVER IT WAS COPIED FROM (the user, 2026-11-04: a WeChat screenshot
   had to go through WeChat again first). What the page can read it takes; for
   the rest it asks the application, which reads the clipboard itself. Words
   pasted stay words. */
async function chatFromClipboard(withText) {
  const waiting = { name: "…", uploading: true };
  chatAttached.push(waiting);
  drawChatAttached();
  try {
    const said = await api.post("/api/chat/clipboard", { with_text: withText });
    chatAttached.splice(chatAttached.indexOf(waiting), 1);
    chatAttached.push(...(said.files || []).slice(0, CHAT_MOST_FILES - chatAttached.length));
    if (!withText && !(said.files || []).length) {
      $("#chat-meta").textContent = "Nothing to attach was found";
    }
  } catch (err) {
    chatAttached.splice(chatAttached.indexOf(waiting), 1);
    $("#chat-meta").textContent = err.message;
  }
  drawChatAttached();
}

document.addEventListener("paste", (event) => {
  if (!$("#chat-form").offsetParent) return;             // another tab is open
  const where = event.target;
  if (where !== $("#chat-input") && (where?.isContentEditable || /^(INPUT|TEXTAREA)$/.test(where?.tagName))) return;
  const data = event.clipboardData;
  const files = [...(data?.files || [])];
  if (files.length) {
    event.preventDefault();
    chatAttach(files);
    return;
  }
  const words = (data?.getData("text/plain") || "").trim();
  if (words && !/<img/i.test(data?.getData("text/html") || "")) return;
  chatFromClipboard(Boolean(words));
});

/* @ A FILE OF THIS CONVERSATION (the user, 2026-11-04: "@会自动跳出联想"): typed
   after a space or at the start, the files come up to choose from - those
   about to be sent and those sent before, the newest first - and the one
   chosen is given to the model with the message. */
const chatMentionBox = document.createElement("div");
chatMentionBox.className = "chat-mention";
chatMentionBox.hidden = true;
$("#chat-form").append(chatMentionBox);
let chatMentionAt = null;                 // {start, shown, index}

function hideMentions() {
  chatMentionAt = null;
  chatMentionBox.hidden = true;
}

function drawMentions() {
  chatMentionBox.replaceChildren(...chatMentionAt.shown.map((one, index) => {
    const row = document.createElement("button");
    row.type = "button";
    row.classList.toggle("is-on", index === chatMentionAt.index);
    const seen = one.preview || (one.picture && one.id
      ? `/api/chat/file?id=${encodeURIComponent(one.id)}` : "");
    if (seen) {
      const picture = document.createElement("img");
      picture.src = seen;
      picture.alt = "";
      row.append(picture);
    }
    const name = document.createElement("span");
    name.textContent = one.name;
    row.append(name);
    // Before the box loses the caret to the press.
    row.addEventListener("mousedown", (event) => {
      event.preventDefault();
      pickMention(one);
    });
    return row;
  }));
  chatMentionBox.hidden = false;
}

function watchMentions() {
  const input = $("#chat-input");
  const caret = input.selectionStart ?? input.value.length;
  const typed = input.value.slice(0, caret).match(/(^|\s)@([^\s@]*)$/);
  if (!typed) return hideMentions();
  const query = typed[2].toLowerCase();
  const every = [...chatAttached.filter((one) => one.id), ...[...chatFiles.values()].reverse()];
  const shown = every.filter((one, index) => every.findIndex((other) => other.id === one.id) === index
    && one.name.toLowerCase().includes(query)).slice(0, 8);
  if (!shown.length) return hideMentions();
  chatMentionAt = { start: caret - typed[2].length - 1, caret, shown, index: 0 };
  drawMentions();
}

function pickMention(one) {
  const input = $("#chat-input");
  const { start, caret } = chatMentionAt;
  const said = `@${one.name} `;
  input.value = input.value.slice(0, start) + said + input.value.slice(caret);
  input.setSelectionRange(start + said.length, start + said.length);
  chatMentions.push(one);
  hideMentions();
  input.focus();
}

$("#chat-input").addEventListener("input", watchMentions);
$("#chat-input").addEventListener("blur", hideMentions);
$("#chat-input").addEventListener("keydown", (event) => {
  if (!chatMentionAt) return;
  const { shown } = chatMentionAt;
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    const step = event.key === "ArrowDown" ? 1 : shown.length - 1;
    chatMentionAt.index = (chatMentionAt.index + step) % shown.length;
    drawMentions();
  } else if (event.key === "Enter" || event.key === "Tab") {
    event.preventDefault();
    pickMention(shown[chatMentionAt.index]);
  } else if (event.key === "Escape") {
    event.preventDefault();
    hideMentions();
  }
});

/* A message's files, under its words. */
function drawAttached(message, files) {
  if (!message || !(files || []).length) return;
  const row = document.createElement("div");
  row.className = "chat-message-files";
  for (const one of files) {
    const link = document.createElement("a");
    link.href = `/api/chat/file?id=${encodeURIComponent(one.id)}`;
    link.target = "_blank";
    link.rel = "noopener";
    if (one.picture) {
      const picture = document.createElement("img");
      picture.src = link.href;
      picture.alt = one.name;
      link.append(picture);
    } else {
      link.textContent = `📄 ${one.name}`;
    }
    row.append(link);
  }
  message.append(row);
}

/* TWO SPEEDS (the user, 2026-11-04; agent/two_speed.py): the quick reply in blue, and
   the full agent's folded under it in red, whole when it is done. */
function chatFold(quick) {
  const fold = document.createElement("details");
  fold.className = "chat-fold";
  fold.innerHTML = "<summary></summary><div class=\"chat-fold-body\"></div>";
  fold.open = !quick;                    // nothing above it: it is the answer
  (quick || $("#chat-log")).append(fold);
  return fold;
}

/* What it changed, on the line itself: seen without opening it, and taken back
   with one press - there being no asking before such a change. */
function chatDid(did) {
  const parts = [];
  if (did?.remembered) parts.push("Remembered");
  if (did?.quiet) parts.push("Quiet rule added");
  if (did?.rules === 1) parts.push("1 rule changed");
  if (did?.rules > 1) parts.push(`${did.rules} rules changed`);
  return parts;
}

function chatFoldDone(fold, text, messageId, kind, did) {
  const title = kind === "correction" ? "A correction" : "More detail";
  const summary = fold.querySelector("summary");
  summary.textContent = [...chatDid(did), ...(text ? [title] : [])].join(" · ");
  fold.classList.toggle("is-correction", kind === "correction");
  const body = fold.querySelector(".chat-fold-body");
  if (text) {
    const said = appendMessage("assistant", text, messageId);
    said.classList.add("layer-slow");
    body.replaceChildren(said);
  }
  if (did?.changes?.length) {
    const undo = document.createElement("button");
    undo.type = "button";
    undo.classList.add("secondary", "chat-fold-undo");
    undo.textContent = "Undo";
    undo.addEventListener("click", async (event) => {
      event.preventDefault();
      undo.disabled = true;
      await api.post("/api/monitor/undo", { ids: did.changes });
      undo.textContent = "Undone";
    });
    summary.append(undo);
  }
}

async function chatFollow(quick, turn) {
  const fold = chatFold(quick);
  const summary = fold.querySelector("summary");
  summary.textContent = "Looking into it…";
  fold.classList.add("is-working");
  for (;;) {
    await new Promise((resolve) => setTimeout(resolve, 300));
    let state;
    try {
      state = await fetch(`/api/chat/turn?id=${encodeURIComponent(turn)}`,
                          { headers: { Accept: "application/json" } }).then((r) => r.json());
    } catch (err) {
      continue;                          // a moment's trouble: asked again
    }
    if (state.state === "running") {
      if (state.text) fold.querySelector(".chat-fold-body").textContent = state.text;
      continue;
    }
    fold.classList.remove("is-working");
    if (state.state === "done" && state.error) {
      fold.open = true;
      summary.textContent = "The full agent could not answer";
      fold.querySelector(".chat-fold-body").textContent = state.error;
    } else if (state.state === "done" && (state.answer || Object.keys(state.did || {}).length)) {
      chatFoldDone(fold, state.answer, state.message_id, state.kind, state.did);
    } else {
      fold.remove();                     // nothing to add, or taken up with the next message
    }
    return;
  }
}

/* One message at a time. A second send while the first is answered would
   race it to the conversation, and the answers would arrive in whichever
   order they finished. */
let chatSending = false;
$("#chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = $("#chat-input");
  const text = input.value.trim();
  if (chatAttached.some((one) => one.uploading)) return;   // still on their way
  const sent = chatAttached.splice(0);
  if ((!text && !sent.length) || chatSending) {
    chatAttached.push(...sent);
    return;
  }
  drawChatAttached();
  hideMentions();
  const mentioned = chatMentions.splice(0).filter((one) => text.includes(`@${one.name}`))
    .map((one) => one.id);
  for (const one of sent) chatFiles.set(one.id, one);
  chatSending = true;
  chatNavigation?.setBusy(true);
  $("#chat-form button[type=submit]").disabled = true;
  drawAttached(appendMessage("user", text), sent);
  input.value = "";
  $("#chat-meta").textContent = "thinking…";
  const progress = progressPanel($("#chat-log"));
  try {
    if (chatNavigation) conversationId = await chatNavigation.prepareConversation();
    const result = await api.post("/api/chat/turn", { message: text, conversation_id: conversationId,
      attachments: sent.map((one) => one.id), mentions: mentioned });
    progress.finish();
    conversationId = result.conversation_id;
    chatNavigation?.recordConversation(conversationId);
    const quick = result.answer ? appendMessage("assistant", result.answer, result.message_id) : null;
    if (result.layer === "fast") quick?.classList.add("layer-fast");
    $("#chat-meta").textContent = result.layer ? "" :
      `${result.classification} · ${(result.tool_calls || []).length} tool call(s) · ` +
      `${result.duration_seconds}s`;
    if (result.turn) chatFollow(quick, result.turn);
  } catch (err) {
    progress.finish();
    appendMessage("assistant", `Error: ${err.message}`);
    $("#chat-meta").textContent = "";
  } finally {
    chatSending = false;
    chatNavigation?.setBusy(false);
    $("#chat-form button[type=submit]").disabled = false;
  }
  input.focus();
});


function appendMessage(role, text, messageId = "") {
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  if (role === "assistant" && messageId) {
    // A reply can be asked about: select a word or phrase in it - or all of
    // it - and "Ask about" appears. The answers sit under it, one level deep.
    el.dataset.message = messageId;
    el.innerHTML = `<div class="msg-text"></div><div class="followups"></div>`;
    el.querySelector(".msg-text").textContent = text;
  } else {
    el.textContent = text;
  }
  $("#chat-log").appendChild(el);
  $("#chat-log").scrollTop = $("#chat-log").scrollHeight;
  layoutFollowUps();
  return el;
}


/* ------------------------------------------------------------ follow-ups
   A follow-up is its own question, asked with nothing of the conversation
   around it: "what is X", where X is a word or phrase selected in a reply,
   or the whole reply when all of it is selected. There is no button for it:
   selecting is how it is asked. Its answer can be folded and closed, and has
   no follow-ups of its own - two levels at most.

   IN THE MARGIN, AS A COMMENT IS. Where the chat is wide enough, each
   follow-up is a card in a column to the right of the conversation, at the
   height of what it asks about - the words selected, kept marked in the
   reply - and pointing at each other on hover. Cards that would overlap are
   pushed down, in the order of what they point at. Under the reply, as
   before, where there is no room for a margin. */
const FOLLOW_UP_SHOWN = 80;
//: How wide the conversation must be for the follow-ups to go in a margin.
const FOLLOW_UP_MARGIN_FROM = 760;

/* Every follow-up, and the question being written, put where it belongs:
   in the margin beside what it points at, or under its reply. */
function layoutFollowUps() {
  const log = $("#chat-log");
  if (!log) return;
  const notes = [...log.querySelectorAll(".followups > .followup, .followups > .fu-form")];
  log.classList.toggle("has-notes", notes.length > 0);
  const wide = log.clientWidth >= FOLLOW_UP_MARGIN_FROM;
  log.classList.toggle("fu-margin-mode", wide);
  if (!wide) {
    notes.forEach((note) => { note.style.top = ""; });
    return;
  }
  const origin = log.getBoundingClientRect().top - log.scrollTop;
  const pointedAt = (note) => {
    const mark = note.__anchor;
    const target = mark && mark.isConnected ? mark : note.closest(".msg");
    return target.getBoundingClientRect().top - origin;
  };
  let below = 0;
  notes.map((note, order) => ({ note, order, at: pointedAt(note) }))
    .sort((a, b) => a.at - b.at || a.order - b.order)
    .forEach(({ note, at }) => {
      const top = Math.max(at, below);
      note.style.top = `${Math.round(top)}px`;
      below = top + note.offsetHeight + 8;
    });
}

window.addEventListener("resize", () => layoutFollowUps());

/* The words a follow-up asks about, marked in the reply - or nothing, when
   it is about the whole reply or the selection crosses another mark. */
function markAsked(range) {
  if (!range) return null;
  const mark = document.createElement("mark");
  mark.className = "fu-anchor";
  try {
    range.surroundContents(mark);
  } catch (err) {
    return null;
  }
  return mark;
}

function unmarkAsked(mark) {
  if (!mark || !mark.isConnected) return;
  const parent = mark.parentNode;
  mark.replaceWith(...mark.childNodes);
  parent.normalize();
}

/* A card and what it points at light up together. */
function pairFollowUp(note, mark) {
  note.__anchor = mark;
  if (!mark) return;
  mark.__note = note;
  const on = (yes) => {
    const card = mark.__note;
    mark.classList.toggle("fu-active", yes);
    if (card) card.classList.toggle("fu-active", yes);
  };
  if (!mark.__paired) {
    mark.__paired = true;
    mark.addEventListener("mouseenter", () => on(true));
    mark.addEventListener("mouseleave", () => on(false));
  }
  note.addEventListener("mouseenter", () => on(true));
  note.addEventListener("mouseleave", () => on(false));
}

function shortened(text, most = FOLLOW_UP_SHOWN) {
  const one = String(text || "").replace(/\s+/g, " ").trim();
  return one.length > most ? `${one.slice(0, most - 1)}\u2026` : one;
}

function openFollowUp(messageEl, about, range = null) {
  const reply = messageEl.querySelector(".msg-text").textContent;
  const subject = about || reply;
  const asked = `what is ${subject}`;
  const open = messageEl.querySelector(".fu-form");
  if (open) {
    unmarkAsked(open.__anchor);
    open.remove();
  }
  const mark = about ? markAsked(range) : null;
  const form = document.createElement("form");
  form.className = "fu-form";
  form.innerHTML = `<span class="fu-about"></span>
    <input type="text" class="fu-input" autocomplete="off">
    <button type="submit" class="small">Ask</button>
    <button type="button" class="small secondary fu-cancel" title="Cancel">\u00d7</button>`;
  form.querySelector(".fu-about").textContent =
    about ? `\u201c${shortened(about, 40)}\u201d` : "Whole reply";
  const input = form.querySelector(".fu-input");
  input.placeholder = `${shortened(asked)}  (Tab to fill in)`;
  // Tab fills in the question it would ask anyway.
  input.addEventListener("keydown", (event) => {
    if (event.key === "Tab" && !event.shiftKey && !input.value.trim()) {
      event.preventDefault();
      input.value = asked;
    }
    if (event.key === "Escape") cancel();
  });
  const cancel = () => {
    unmarkAsked(mark);
    form.remove();
    layoutFollowUps();
  };
  form.querySelector(".fu-cancel").addEventListener("click", cancel);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const question = input.value.trim() || asked;
    form.remove();
    askFollowUp(messageEl, about, question, mark);
  });
  messageEl.querySelector(".followups").appendChild(form);
  pairFollowUp(form, mark);
  layoutFollowUps();
  input.focus({ preventScroll: true });
}

function followUpBox(about, question) {
  const box = document.createElement("div");
  box.className = "followup";
  box.innerHTML = `<div class="fu-head">
      <button type="button" class="fu-toggle" aria-expanded="true"
        title="Fold or unfold">\u25be</button>
      <span class="fu-title"><strong></strong>: <span class="fu-question"></span></span>
      <button type="button" class="fu-close" title="Delete this follow-up">\u00d7</button>
    </div>
    <div class="fu-body muted">thinking\u2026</div>`;
  box.querySelector(".fu-title strong").textContent =
    about ? shortened(about, 60) : "Whole reply";
  box.querySelector(".fu-question").textContent = shortened(question, 160);
  box.querySelector(".fu-question").title = question;
  const toggle = box.querySelector(".fu-toggle");
  // THE WHOLE TOP ROW folds it, like a header - not only the small
  // triangle, which was the one place a click did anything. The cross
  // still deletes it, and nothing else.
  box.querySelector(".fu-head").addEventListener("click", (event) => {
    if (event.target.closest(".fu-close")) return;
    const folded = box.classList.toggle("folded");
    toggle.setAttribute("aria-expanded", String(!folded));
    toggle.textContent = folded ? "\u25b8" : "\u25be";
    layoutFollowUps();
  });
  return box;
}

/* Closing a card deletes the follow-up, and unmarks the words it was about. */
async function deleteFollowUp(box) {
  unmarkAsked(box.__anchor);
  box.remove();
  layoutFollowUps();
  if (box.dataset.id) {
    try {
      await api.post("/api/chat/followup/delete", { id: box.dataset.id });
    } catch (err) { toast(err.message, "error"); }
  }
}

/* The words a kept follow-up was about, found in the reply again and
   marked - the first place they appear that is not already marked. Nothing
   marked when they cannot be found, and the card then sits at its reply. */
function markAgain(messageEl, about) {
  const text = messageEl.querySelector(".msg-text");
  if (!text || !about) return null;
  const walker = document.createTreeWalker(text, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (node.parentElement?.closest("mark.fu-anchor")) continue;
    const at = node.textContent.indexOf(about);
    if (at < 0) continue;
    const range = document.createRange();
    range.setStart(node, at);
    range.setEnd(node, at + about.length);
    return markAsked(range);
  }
  return null;
}

function restoreFollowUps(messageEl, followups) {
  for (const one of followups) {
    const mark = one.about ? markAgain(messageEl, one.about) : null;
    const box = followUpBox(one.about, one.question);
    box.dataset.id = one.id;
    const body = box.querySelector(".fu-body");
    body.classList.remove("muted");
    body.textContent = one.answer;
    messageEl.querySelector(".followups").appendChild(box);
    pairFollowUp(box, mark);
    box.querySelector(".fu-close").addEventListener("click", () => deleteFollowUp(box));
  }
}

async function askFollowUp(messageEl, about, question, mark = null) {
  const box = followUpBox(about, question);
  messageEl.querySelector(".followups").appendChild(box);
  pairFollowUp(box, mark);
  layoutFollowUps();
  let closed = false;
  box.querySelector(".fu-close").addEventListener("click", () => {
    closed = true;
    deleteFollowUp(box);
  });
  try {
    const said = await api.post("/api/chat/followup", {
      message_id: messageEl.dataset.message, about, question });
    box.dataset.id = said.id;
    // Closed while it was being answered: nothing to keep.
    if (closed) {
      api.post("/api/chat/followup/delete", { id: said.id }).catch(() => {});
      return;
    }
    const body = box.querySelector(".fu-body");
    body.classList.remove("muted");
    body.textContent = said.answer;
  } catch (err) {
    const body = box.querySelector(".fu-body");
    body.textContent = `Error: ${err.message}`;
  }
  layoutFollowUps();
}

/* Selecting a word or phrase in a reply offers to ask about it. Not in a
   follow-up's answer: a follow-up has no follow-ups. */
function selectedInReply() {
  const picked = window.getSelection();
  const text = picked ? picked.toString().trim() : "";
  if (!text || picked.rangeCount === 0) return null;
  const range = picked.getRangeAt(0);
  const node = range.commonAncestorContainer;
  const host = (node.nodeType === 1 ? node : node.parentElement)
    ?.closest(".msg.assistant[data-message] .msg-text");
  if (!host) return null;
  // All of the reply selected is a question about the whole reply.
  const whole = text === host.textContent.trim();
  return { text: whole ? "" : text, shown: text, message: host.closest(".msg"),
           rect: range.getBoundingClientRect(), range: range.cloneRange() };
}

function hideFollowUpPick() {
  document.querySelector("#fu-pick")?.remove();
}

document.addEventListener("mouseup", (event) => {
  if (event.target.closest?.("#fu-pick")) return;
  setTimeout(() => {
    hideFollowUpPick();
    const picked = selectedInReply();
    if (!picked) return;
    const pick = document.createElement("button");
    pick.type = "button";
    pick.id = "fu-pick";
    pick.className = "small";
    pick.textContent = picked.text
      ? `Ask about \u201c${shortened(picked.text, 30)}\u201d`
      : "Ask about this reply";
    pick.style.top = `${window.scrollY + picked.rect.bottom + 6}px`;
    pick.style.left = `${window.scrollX + picked.rect.left}px`;
    pick.addEventListener("mousedown", (e) => e.preventDefault());
    pick.addEventListener("click", () => {
      hideFollowUpPick();
      window.getSelection()?.removeAllRanges();
      openFollowUp(picked.message, picked.text, picked.range);
    });
    document.body.appendChild(pick);
  }, 0);
});


/* Who answers, at the top: the agent's name, and its model - or that the
   model needs the user (signed out, say). */
(async () => {
  const name = $("#agent-name");
  const state = $("#chat-state");
  if (!name && !state) return;
  try {
    const said = await api.get("/api/agent-state");
    if (name) name.textContent = said.agent_name || "Conversation";
    if (state) {
      const model = said.model || {};
      state.textContent = said.needs_attention
        ? `sign in to ${said.needs_attention.provider} again`
        : model.available === false ? `${model.label}: ${model.state}` : (model.label || "");
    }
  } catch (err) {
    /* the conversation still works without it */
  }
})();

/* FROM THE CHAT, TWO THINGS MADE OF WHAT WAS SAID (the user, 2026-11-03, from
   PR #8): a SUMMARY DOCUMENT of answers ticked - in this
   conversation or others - organised by the model from them alone, then
   previewed and saved as PDF or PNG pages; and a NEWSPAPER SPECIAL ISSUE,
   its brief made clear here with the agent, which hands it to the newspaper
   (make_special_issue). Messages are named by their ids: the server reads
   what was said. */
(function chatDocuments() {
  // Just above the box written in, at its left (the user, 2026-11-03).
  const form = document.querySelector("#chat-form");
  if (!form) return;
  const actions = document.createElement("span");
  actions.className = "chat-documents";
  const summaryButton = document.createElement("button");
  summaryButton.type = "button";
  summaryButton.className = "small secondary";
  summaryButton.textContent = "Summary document";
  summaryButton.title = "Organise answers you tick into a document to save";
  const specialButton = document.createElement("button");
  specialButton.type = "button";
  specialButton.className = "small secondary";
  specialButton.textContent = "Newspaper special issue";
  specialButton.title = "Make a newspaper issue about what these conversations are about";
  actions.append(summaryButton, specialButton);
  form.before(actions);
  summaryButton.addEventListener("click", () => openSelection(true));
  specialButton.addEventListener("click", () => openSelection(false));

  async function openSelection(summaryMode) {
    if (chatSending) return;
    const dialog = document.createElement("dialog");
    dialog.className = "chat-doc-dialog";
    dialog.innerHTML = `<form method="dialog">
      <h2 data-doc-head>${escapeHtml(summaryMode ? "A summary document of answers"
        : "A newspaper special issue")}</h2>
      <p data-doc-lead>${escapeHtml(summaryMode
        ? "Tick the answers to organise into a document. Only what you tick is used - nothing is looked up or added."
        : "Tick the messages it is to be about, write what you want, then talk it over in the chat: the agent hands the brief to the newspaper.")}</p>
      <div class="chat-doc-conversations">
        <label>${escapeHtml("Find a conversation")}<input type="search" data-doc-conversation-search
          placeholder="${escapeHtml("Search conversation titles")}"></label>
        <label>${escapeHtml("Add another conversation")}<select data-doc-conversation></select></label>
        <button type="button" class="small" data-doc-add>${escapeHtml("Add")}</button>
      </div>
      <div class="chat-doc-selection">
        <label>${escapeHtml("Find messages")}<input type="search" data-doc-message-search
          placeholder="${escapeHtml("Search the messages shown")}"></label>
        <label class="chat-doc-inline"><input type="checkbox" data-doc-selected-only> ${
          escapeHtml("Ticked ones only")}</label>
        <button type="button" class="secondary small" data-doc-clear>${escapeHtml("Clear the ticks")}</button>
        <output data-doc-count aria-live="polite"></output>
      </div>
      <div class="chat-doc-messages"></div>
      <div class="chat-doc-brief">
        <label>${escapeHtml("Title (optional)")}<input data-doc-title maxlength="160" placeholder="${
          escapeHtml(summaryMode ? "Chosen for you if left empty" : "The newspaper chooses one if left empty")}"></label>
        ${summaryMode ? `<label>${escapeHtml("Style")}<select data-doc-style>
            <option value="plain">${escapeHtml("Plain - text and sections")}</option>
            <option value="enriched">${escapeHtml("Enriched - highlights, headings and small diagrams")}</option>
          </select><span class="muted">${escapeHtml("Diagrams show only sequences the answers themselves describe.")}</span></label>`
          : `<label>${escapeHtml("What you want")}<textarea rows="2" data-doc-requirements placeholder="${
            escapeHtml("Audience, depth, time range, sources…")}"></textarea></label>`}
      </div>
      <p class="chat-doc-error" role="alert"></p>
      <div class="chat-doc-actions">
        <button type="button" data-doc-go>${escapeHtml(summaryMode ? "Make the summary" : "Talk it over in the chat")}</button>
        <button value="cancel" class="secondary">${escapeHtml("Cancel")}</button></div></form>`;
    const choices = dialog.querySelector(".chat-doc-messages");
    const error = dialog.querySelector(".chat-doc-error");
    const loaded = new Set();

    function counted() {
      const query = dialog.querySelector("[data-doc-message-search]").value.trim().toLocaleLowerCase();
      const only = dialog.querySelector("[data-doc-selected-only]").checked;
      let ticked = 0, groups = 0;
      for (const group of choices.children) {
        let shown = 0, here = 0;
        for (const row of group.querySelectorAll(".chat-doc-message")) {
          const box = row.querySelector("input");
          if (box.checked) { ticked++; here++; }
          row.hidden = (only && !box.checked) || !row.dataset.search.includes(query);
          if (!row.hidden) shown++;
          row.classList.toggle("is-ticked", box.checked);
        }
        if (here) groups++;
        group.querySelector("[data-doc-group-count]").textContent = `${here} ticked, ${shown} shown`;
      }
      dialog.querySelector("[data-doc-count]").textContent = ticked
        ? `${ticked} ticked, from ${groups} conversation(s)` : "Nothing ticked yet";
    }
    dialog.querySelector("[data-doc-message-search]").addEventListener("input", counted);
    dialog.querySelector("[data-doc-selected-only]").addEventListener("change", counted);
    choices.addEventListener("change", counted);
    dialog.querySelector("[data-doc-clear]").addEventListener("click", () => {
      choices.querySelectorAll("input:checked").forEach((one) => { one.checked = false; });
      counted();
    });

    function addGroup(title, messages) {
      const group = document.createElement("fieldset");
      const legend = document.createElement("legend");
      legend.textContent = title;
      const tools = document.createElement("div");
      tools.className = "chat-doc-group-tools";
      tools.innerHTML = `<button type="button" class="secondary small" data-doc-all>${escapeHtml("Tick the shown")}</button>
        <button type="button" class="secondary small" data-doc-none>${escapeHtml("Clear")}</button>
        <span class="muted" data-doc-group-count></span>`;
      tools.querySelector("[data-doc-all]").addEventListener("click", () => {
        group.querySelectorAll(".chat-doc-message").forEach((row) => {
          if (!row.hidden) row.querySelector("input").checked = true;
        });
        counted();
      });
      tools.querySelector("[data-doc-none]").addEventListener("click", () => {
        group.querySelectorAll("input").forEach((one) => { one.checked = false; });
        counted();
      });
      const pane = document.createElement("div");
      pane.className = "chat-doc-pane";
      pane.tabIndex = 0;
      group.append(legend, tools, pane);
      let any = false;
      for (const message of messages) {
        if (!["user", "assistant"].includes(message.role)) continue;
        if (summaryMode && message.role !== "assistant") continue;
        any = true;
        const text = String(message.content || "");
        const who = message.role === "assistant" ? "Agent" : "You";
        const row = document.createElement("div");
        row.className = "chat-doc-message";
        row.dataset.search = `${title} ${text}`.toLocaleLowerCase();
        const label = document.createElement("label");
        const box = document.createElement("input");
        box.type = "checkbox";
        box.dataset.id = String(message.id || "");
        box.dataset.context = `${title} - ${who}: ${text}`;
        const preview = document.createElement("span");
        preview.textContent = `${who}: ${text.length > 260 ? `${text.slice(0, 260)}…` : text}`;
        label.append(box, preview);
        row.append(label);
        if (text.length > 260) {
          const more = document.createElement("details");
          const summary = document.createElement("summary");
          summary.textContent = "Read all of it";
          const all = document.createElement("p");
          all.textContent = text;
          more.append(summary, all);
          row.append(more);
        }
        pane.append(row);
      }
      if (!any) {
        const empty = document.createElement("p");
        empty.className = "muted";
        empty.textContent = summaryMode ? "No answers in this conversation yet." : "No messages in this conversation yet.";
        pane.append(empty);
      }
      choices.append(group);
      counted();
    }

    async function load(id, title) {
      const conversation = await api.get(`/api/conversation?id=${encodeURIComponent(id)}`);
      if (!dialog.isConnected) return;
      addGroup(title || conversation.title || "Conversation", conversation.messages || []);
      loaded.add(id);
    }

    const picker = dialog.querySelector("[data-doc-conversation]");
    let conversations = [];
    function listed() {
      const query = dialog.querySelector("[data-doc-conversation-search]").value.trim().toLocaleLowerCase();
      picker.replaceChildren(new Option("Choose a conversation…", ""));
      for (const one of conversations) {
        if (loaded.has(one.id) || !(one.title || "").toLocaleLowerCase().includes(query)) continue;
        const day = one.updated_at ? new Date(one.updated_at).toLocaleDateString() : "";
        picker.append(new Option(`${one.title || "Conversation"} (${one.message_count || 0})${day ? ` ${day}` : ""}`, one.id));
      }
      dialog.querySelector("[data-doc-add]").disabled = picker.options.length <= 1;
    }
    dialog.querySelector("[data-doc-conversation-search]").addEventListener("input", listed);
    dialog.querySelector("[data-doc-add]").addEventListener("click", async (event) => {
      if (!picker.value || loaded.has(picker.value)) return;
      event.target.disabled = true;
      try { await load(picker.value); listed(); }
      catch (err) { error.textContent = err.message; }
      finally { event.target.disabled = false; }
    });

    dialog.querySelector("[data-doc-go]").addEventListener("click", async (event) => {
      const ticked = [...choices.querySelectorAll("input:checked")];
      const title = dialog.querySelector("[data-doc-title]").value.trim();
      error.textContent = "";
      if (summaryMode) {
        if (!ticked.length) { error.textContent = "Tick at least one answer."; return; }
        event.target.disabled = true;
        event.target.textContent = "Organising the answers…";
        try {
          const made = await api.post("/api/chat/summary", { ids: ticked.map((one) => one.dataset.id),
            title, style: dialog.querySelector("[data-doc-style]").value });
          if (dialog.isConnected) { dialog.close(); showDocument(made); }
        } catch (err) { error.textContent = err.message; }
        finally { event.target.disabled = false; event.target.textContent = "Make the summary"; }
        return;
      }
      const wanted = dialog.querySelector("[data-doc-requirements]").value.trim();
      if (!ticked.length && !wanted) { error.textContent = "Tick some messages, or say what it is to be about."; return; }
      // TALKED OVER IN THE CHAT: the brief as a message of the user's; the
      // agent asks what is unclear, then hands it to the newspaper.
      $("#chat-input").value = "I would like a newspaper special issue.\n"
        + (ticked.length ? `What it is about, from these messages:\n${ticked.map((one) => one.dataset.context).join("\n\n")}\n\n` : "")
        + `What I want: ${wanted || "Help me make the brief clear."}\n`
        + (title ? `Its title: ${title}\n` : "The newspaper may choose its title.\n")
        + "Ask me what is unclear, then hand it to the newspaper to make. Use the messages as its subjects, not as its stories.";
      dialog.close();
      $("#chat-form").requestSubmit();
    });

    dialog.addEventListener("close", () => dialog.remove());
    document.body.append(dialog);
    dialog.showModal();
    try {
      if (conversationId) await load(conversationId, "This conversation");
      else addGroup("This conversation", []);
      conversations = (await api.get("/api/conversations")).conversations || [];
      if (dialog.isConnected) listed();
    } catch (err) { error.textContent = err.message; }
  }

  /* THE DOCUMENT, PREVIEWED AND SAVED: drawn once into pages - the same for
     the PNG pages and for the PDF, whose pages are those pictures (the
     browser's own fonts, any language, no library, nothing fetched). */
  function showDocument(made) {
    const dialog = document.createElement("dialog");
    dialog.className = "chat-doc-dialog";
    dialog.innerHTML = `<form method="dialog"><h2>${escapeHtml("The summary document")}</h2>
      <p data-doc-about></p>
      <div class="chat-doc-actions"><button type="button" data-doc-pdf>${escapeHtml("Download PDF")}</button>
        <button type="button" data-doc-png>${escapeHtml("Download PNG pages")}</button>
        <button value="close" class="secondary">${escapeHtml("Close")}</button></div>
      <p class="chat-doc-error" role="alert"></p><div class="chat-doc-preview"></div></form>`;
    dialog.querySelector("[data-doc-about]").textContent = `${made.answer_count} answer(s), ${
      made.style === "enriched" ? "enriched" : "plain"} - read it before you share it.`;
    const preview = dialog.querySelector(".chat-doc-preview");
    const pages = [];
    const width = 1240, height = 1754, margin = 100;
    let canvas, ctx, y;
    function page() {
      canvas = document.createElement("canvas");
      canvas.width = width; canvas.height = height;
      canvas.className = "chat-doc-page";
      ctx = canvas.getContext("2d");
      ctx.fillStyle = "#ffffff"; ctx.fillRect(0, 0, width, height);
      ctx.fillStyle = "#666666"; ctx.font = "20px sans-serif";
      ctx.fillText(String(pages.length + 1), width - margin, height - 45);
      pages.push(canvas); y = margin;
      preview.append(canvas);
    }
    page();
    const enriched = made.style === "enriched" && made.document;
    function write(text, kind = "paragraph") {
      const title = kind === "title", heading = kind === "heading";
      const boxed = kind === "highlight" || kind === "node";
      const inset = boxed ? 24 : 0;
      const font = title ? "bold 42px sans-serif" : heading ? "bold 32px sans-serif" : "27px sans-serif";
      const leading = title ? 58 : heading ? 48 : 41;
      const room = width - margin * 2 - inset * 2;
      const rows = [];
      for (const line of String(text).split("\n")) {
        ctx.font = font;
        let row = "";
        for (const token of line.match(/\S+\s*|\s+/gu) || []) {
          if (ctx.measureText(row + token).width <= room) { row += token; continue; }
          if (row) { rows.push(row.trimEnd()); row = ""; }
          for (const char of token) {          // a word longer than the line, or no spaces at all
            if (ctx.measureText(row + char).width > room) { rows.push(row.trimEnd()); row = ""; }
            row += char;
          }
        }
        rows.push(row.trimEnd());
      }
      // A heading kept with what follows it, a highlight on one page.
      const needed = Math.min(rows.length * leading + 28, height - margin * 2);
      if (y + (boxed ? needed : heading ? leading * 2 : leading) > height - margin) page();
      if (boxed) y += 12;
      for (const row of rows) {
        if (y + leading + 12 > height - margin) page();
        if (boxed) {
          ctx.fillStyle = kind === "highlight" ? "#fff3cd" : "#eaf2fc";
          ctx.fillRect(margin, y, width - margin * 2, leading + 4);
          ctx.fillStyle = kind === "highlight" ? "#c58a12" : "#3873ab";
          ctx.fillRect(margin, y, 5, leading + 4);
        }
        ctx.font = font;
        ctx.fillStyle = enriched && (title || heading) ? "#174d75" : "#172330";
        ctx.fillText(row, margin + inset, y + leading - 8);
        y += leading;
      }
      y += heading ? 12 : boxed ? 24 : 16;
    }
    const lines = String(made.summary || "").split("\n");
    if (enriched) {
      write(made.document.title, "title");
      for (const block of made.document.blocks) {
        if (block.type !== "diagram") { write(block.text, block.type); continue; }
        write(block.text, "heading");
        block.items.forEach((item, index) => {
          if (index) {
            if (y + 100 > height - margin) page();
            else { ctx.font = "30px sans-serif"; ctx.fillStyle = "#3873ab"; ctx.fillText("↓", width / 2 - 10, y + 24); y += 36; }
          }
          write(`${index + 1}. ${item}`, "node");
        });
      }
    } else {
      lines.forEach((line, index) => write(line, index === 0 ? "title" : "paragraph"));
    }
    const name = ((enriched ? made.document.title : lines[0]) || "Summary")
      .replace(/[^\p{L}\p{N} _-]/gu, "").trim().slice(0, 80) || "Summary";
    function save(blob, file) {
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url; link.download = file;
      document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    }
    dialog.querySelector("[data-doc-png]").addEventListener("click", () => {
      preview.querySelectorAll("a").forEach((one) => one.remove());
      pages.forEach((one, index) => one.toBlob((blob) => {
        if (!blob || !dialog.isConnected) return;
        // A link per page as well: a browser may let only the first go.
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url; link.download = `${name}-${index + 1}.png`;
        link.textContent = `Page ${index + 1} (PNG)`;
        link.className = "chat-doc-page-link";
        preview.insertBefore(link, pages[index]);
        dialog.addEventListener("close", () => URL.revokeObjectURL(url), { once: true });
        if (pages.length === 1) link.click();
      }, "image/png"));
    });
    dialog.querySelector("[data-doc-pdf]").addEventListener("click", () => {
      try {
        const encoder = new TextEncoder(), chunks = [], offsets = [0];
        let size = 0;
        const add = (value) => {
          const bytes = typeof value === "string" ? encoder.encode(value) : value;
          chunks.push(bytes); size += bytes.length;
        };
        const object = (id, value) => { offsets[id] = size; add(`${id} 0 obj\n${value}\nendobj\n`); };
        add("%PDF-1.4\n");
        object(1, "<< /Type /Catalog /Pages 2 0 R >>");
        object(2, `<< /Type /Pages /Count ${pages.length} /Kids [${pages.map((_, i) => `${3 + i * 3} 0 R`).join(" ")}] >>`);
        pages.forEach((one, index) => {
          const id = 3 + index * 3;
          const jpeg = Uint8Array.from(atob(one.toDataURL("image/jpeg", 0.94).split(",")[1]), (c) => c.charCodeAt(0));
          object(id, `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /XObject << /Im ${id + 1} 0 R >> >> /Contents ${id + 2} 0 R >>`);
          offsets[id + 1] = size;
          add(`${id + 1} 0 obj\n<< /Type /XObject /Subtype /Image /Width ${width} /Height ${height} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${jpeg.length} >>\nstream\n`);
          add(jpeg); add("\nendstream\nendobj\n");
          const content = "q 595 0 0 842 0 0 cm /Im Do Q\n";
          object(id + 2, `<< /Length ${content.length} >>\nstream\n${content}endstream`);
        });
        const start = size, count = 3 + pages.length * 3;
        add(`xref\n0 ${count}\n0000000000 65535 f \n`);
        for (let id = 1; id < count; id++) add(`${String(offsets[id]).padStart(10, "0")} 00000 n \n`);
        add(`trailer\n<< /Size ${count} /Root 1 0 R >>\nstartxref\n${start}\n%%EOF`);
        save(new Blob(chunks, { type: "application/pdf" }), `${name}.pdf`);
      } catch (err) { dialog.querySelector(".chat-doc-error").textContent = err.message; }
    });
    dialog.addEventListener("close", () => dialog.remove());
    document.body.append(dialog);
    dialog.showModal();
  }
  window.chatDocumentsShow = showDocument;        // for the tests
})();

window.layoutFollowUps = layoutFollowUps;
return {
  /* The list of conversations again, and - the first time - the one the
     address names. */
  async activate() {
    if (chatNavigation) await chatNavigation.activate();
    layoutFollowUps();
  },
  layoutFollowUps,
  progressPanel,
  /* Something the agent says first (the desktop's opening message). */
  say(text) { return appendMessage("assistant", text); },
};
};
