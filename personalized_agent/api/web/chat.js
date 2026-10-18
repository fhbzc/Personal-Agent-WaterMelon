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
      } catch (err) {
        /* the panel is an aid, never a reason for the chat to fail */
      }
      await new Promise((resolve) => setTimeout(resolve, 400));
    }
  })();

  return {
    finish() {
      stop = true;
      if (element) element.remove();
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
  const asked = new Map();
  for (const one of conversation.followups || []) {
    if (!asked.has(one.message_id)) asked.set(one.message_id, []);
    asked.get(one.message_id).push(one);
  }
  for (const message of conversation.messages || []) {
    const reply = message.role === "assistant";
    const el = appendMessage(message.role, message.content, reply ? message.id : "");
    if (reply && asked.has(message.id)) restoreFollowUps(el, asked.get(message.id));
  }
  layoutFollowUps();
  $("#chat-log").scrollTop = $("#chat-log").scrollHeight;
}

/* One message at a time. A second send while the first is answered would
   race it to the conversation, and the answers would arrive in whichever
   order they finished. */
let chatSending = false;
$("#chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = $("#chat-input");
  const text = input.value.trim();
  if (!text || chatSending) return;
  chatSending = true;
  chatNavigation?.setBusy(true);
  $("#chat-form button").disabled = true;
  appendMessage("user", text);
  input.value = "";
  $("#chat-meta").textContent = "thinking…";
  const progress = progressPanel($("#chat-log"));
  try {
    if (chatNavigation) conversationId = await chatNavigation.prepareConversation();
    const result = await api.post("/api/chat", { message: text, conversation_id: conversationId });
    progress.finish();
    conversationId = result.conversation_id;
    chatNavigation?.recordConversation(conversationId);
    appendMessage("assistant", result.answer, result.message_id);
    $("#chat-meta").textContent =
      `${result.classification} · ${(result.tool_calls || []).length} tool call(s) · ` +
      `${result.duration_seconds}s`;
  } catch (err) {
    progress.finish();
    appendMessage("assistant", `Error: ${err.message}`);
    $("#chat-meta").textContent = "";
  } finally {
    chatSending = false;
    chatNavigation?.setBusy(false);
    $("#chat-form button").disabled = false;
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
