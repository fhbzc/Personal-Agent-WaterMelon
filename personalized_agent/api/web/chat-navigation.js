/* THE CHAT'S SIDEBAR: every conversation, to reopen, and a new one - newest
   first; pinned ones on top, then the user's TOPICS (the user, 2026-11-03,
   from PR #8), each with its conversations and a shared context given to
   every chat in it, then the chats in no topic. Ported from PR #4 (by
   daniela-miaut).

   Shared by the Chat tab and the conversation window (chat.html), which draw
   a conversation each in their own way: this keeps the list, and hands the
   page a conversation to draw through `onOpen` - the whole of it, every
   message with its id and the follow-ups asked about its replies, so a
   reopened conversation comes back as it was left.

   What is reopened is also what the agent is given: continuing a
   conversation from here sends the model that conversation's earlier
   messages (agent_core.py, `_past`). A new chat starts with nothing. */
"use strict";

window.createChatNavigation = function ({ api, onOpen }) {
  const layout = document.querySelector(".chat-layout");
  if (!layout) return null;
  const workspace = layout.querySelector(".chat-workspace");
  const opener = layout.querySelector("[data-chat-sidebar-open]");
  const sidebar = document.createElement("aside");
  sidebar.id = "chat-sidebar";
  sidebar.className = "chat-sidebar";
  sidebar.setAttribute("aria-label", "Conversations");
  sidebar.innerHTML = `
    <div class="chat-sidebar-head"><strong>Conversations</strong>
      <button type="button" class="chat-nav-icon" data-close aria-label="Close the sidebar">☰</button></div>
    <div class="chat-new-row">
      <button type="button" class="chat-new" data-new><span aria-hidden="true">＋</span> New chat</button>
      <button type="button" class="chat-new" data-new-topic><span aria-hidden="true">＋</span> New topic</button>
    </div>
    <div class="chat-sidebar-scroll">
      <button type="button" class="chat-section-label chat-topics-label" data-fold="topics"
              aria-expanded="true"><span class="chat-fold-mark" aria-hidden="true">▾</span>
        <span>Topics</span></button>
      <div class="chat-topics" aria-label="Topics"></div>
      <button type="button" class="chat-section-label" data-fold="chats"
              aria-expanded="true"><span class="chat-fold-mark" aria-hidden="true">▾</span>
        <span>Chats</span></button>
      <nav class="chat-history" aria-label="Conversations"></nav>
    </div>
    <div class="chat-nav-status muted" role="status" aria-live="polite"></div>`;
  const backdrop = document.createElement("button");
  backdrop.type = "button";
  backdrop.className = "chat-sidebar-backdrop";
  backdrop.setAttribute("aria-label", "Close the sidebar");
  backdrop.tabIndex = -1;
  backdrop.hidden = true;
  layout.prepend(backdrop);
  layout.prepend(sidebar);
  // The two columns exist only once there is a sidebar to fill the first:
  // without this script the conversation keeps the whole width.
  layout.classList.add("has-sidebar");

  // ITS WIDTH IS THE USER'S (2026-11-06): its edge dragged, or the arrow keys on it, kept for next
  // time; a double click puts it back. The most is in the stylesheet, as a share of the layout,
  // so that a narrower window does not lose the conversation to a width chosen on a wider one.
  const SIDEBAR_LEAST = 180, SIDEBAR_MOST_SHARE = 0.6, SIDEBAR_KEY = "chat-sidebar-width";
  const grip = document.createElement("div");
  grip.className = "chat-sidebar-grip";
  grip.tabIndex = 0;
  grip.setAttribute("role", "separator");
  grip.setAttribute("aria-orientation", "vertical");
  grip.setAttribute("aria-label", "Width of the sidebar");
  grip.title = "Drag to change the width; a double click puts it back";
  sidebar.append(grip);
  function sidebarTo(width, keep = true) {
    const most = Math.max(SIDEBAR_LEAST, layout.clientWidth * SIDEBAR_MOST_SHARE);
    const shown = Math.round(Math.max(SIDEBAR_LEAST, Math.min(most, width)));
    layout.style.setProperty("--chat-sidebar-width", `${shown}px`);
    if (keep) {
      try { localStorage.setItem(SIDEBAR_KEY, String(shown)); } catch (_) { /* kept for this page only */ }
    }
    try {
      if (typeof window.layoutFollowUps === "function") window.layoutFollowUps();
    } catch (_) { /* the page is still loading */ }
  }
  try {
    const kept = Number(localStorage.getItem(SIDEBAR_KEY));
    if (kept > 0) layout.style.setProperty("--chat-sidebar-width", `${Math.max(SIDEBAR_LEAST, kept)}px`);
  } catch (_) { /* a private window */ }
  let dragged = null;
  grip.addEventListener("pointerdown", (event) => {
    dragged = { x: event.clientX, width: sidebar.getBoundingClientRect().width };
    grip.setPointerCapture(event.pointerId);
    grip.classList.add("dragged");
    event.preventDefault();
  });
  grip.addEventListener("pointermove", (event) => {
    if (dragged) sidebarTo(dragged.width + event.clientX - dragged.x, false);
  });
  const dropped = (event) => {
    if (!dragged) return;
    sidebarTo(dragged.width + event.clientX - dragged.x);
    dragged = null;
    grip.classList.remove("dragged");
  };
  grip.addEventListener("pointerup", dropped);
  grip.addEventListener("pointercancel", dropped);
  grip.addEventListener("dblclick", () => {
    layout.style.removeProperty("--chat-sidebar-width");
    try { localStorage.removeItem(SIDEBAR_KEY); } catch (_) { /* kept for this page only */ }
  });
  grip.addEventListener("keydown", (event) => {
    const step = { ArrowLeft: -16, ArrowRight: 16 }[event.key];
    if (!step) return;
    sidebarTo(sidebar.getBoundingClientRect().width + step);
    event.preventDefault();
  });

  const $s = (selector) => sidebar.querySelector(selector);
  const mobile = window.matchMedia("(max-width: 700px)");
  const route = () => new URLSearchParams(location.search);
  const state = { conversations: [], conversation: null, topics: [], editing: "",
    // A new chat started under a topic: put there once its first message made it.
    pendingTopic: "", open: new Set(),
    busy: false, request: 0, loading: false, initialized: false,
    // Topics and Chats, each folded by its label (the user, 2026-11-06), kept for next time.
    folded: new Set() };
  try {
    state.folded = new Set(JSON.parse(localStorage.getItem("chat-list-folded") || "[]"));
  } catch (_) { /* a private window */ }
  sidebar.querySelectorAll("[data-fold]").forEach((label) => {
    label.addEventListener("click", () => {
      const part = label.dataset.fold;
      if (!state.folded.delete(part)) state.folded.add(part);
      try { localStorage.setItem("chat-list-folded", JSON.stringify([...state.folded])); } catch (_) { /* this page only */ }
      render();
    });
  });
  let desktopOpen = true;
  let drawerOpen = false;
  try { desktopOpen = localStorage.getItem("chat-sidebar-collapsed") !== "true"; } catch (_) { /* private window */ }

  function setOpen(open, restoreFocus = false) {
    if (mobile.matches) drawerOpen = open;
    else {
      desktopOpen = open;
      try { localStorage.setItem("chat-sidebar-collapsed", String(!open)); } catch (_) { /* kept for this page only */ }
    }
    syncLayout();
    if (open && mobile.matches) $s("[data-close]").focus();
    if (!open && restoreFocus && opener) opener.focus();
  }

  function syncLayout() {
    const open = mobile.matches ? drawerOpen : desktopOpen;
    layout.classList.toggle("sidebar-collapsed", !open);
    layout.classList.toggle("sidebar-drawer-open", mobile.matches && open);
    sidebar.hidden = !open;
    backdrop.hidden = !mobile.matches || !open;
    if (workspace) workspace.inert = mobile.matches && open;
    if (opener) {
      opener.hidden = open && !mobile.matches;
      opener.setAttribute("aria-expanded", String(open));
    }
    sidebar.setAttribute("role", mobile.matches && open ? "dialog" : "complementary");
    if (mobile.matches && open) sidebar.setAttribute("aria-modal", "true");
    else sidebar.removeAttribute("aria-modal");
    // The follow-ups' margin depends on how wide the conversation is. Not
    // yet callable while the page that has them is still loading - this
    // first runs from inside it - and then there is nothing to lay out.
    try {
      if (typeof window.layoutFollowUps === "function") window.layoutFollowUps();
    } catch (_) { /* the page is still loading */ }
  }

  function updateUrl(push = true) {
    const url = new URL(location.href);
    for (const key of ["conversation", "project", "message"]) url.searchParams.delete(key);
    if (state.conversation) url.searchParams.set("conversation", state.conversation);
    if (document.querySelector("#tabs")) url.searchParams.set("tab", "chat");
    if (url.href !== location.href) history[push ? "pushState" : "replaceState"]({}, "", url);
  }

  function status(text) { $s(".chat-nav-status").textContent = text; }

  function row(title, selected, action, className = "chat-nav-row") {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `${className}${selected ? " selected" : ""}`;
    button.textContent = title;
    button.title = title;
    button.disabled = state.busy;
    if (selected) button.setAttribute("aria-current", "page");
    button.addEventListener("click", action);
    return button;
  }

  function historyRows(host, conversations, emptyText) {
    if (!conversations.length && emptyText) {
      const empty = document.createElement("p");
      empty.className = "chat-nav-empty muted";
      empty.textContent = emptyText;
      host.appendChild(empty);
    }
    for (const conversation of conversations) {
      const line = document.createElement("div");
      line.className = "chat-nav-line";
      // Not `data-conversation`: that is the row's own button, which is
      // what anything looking a conversation up in the list must find.
      line.dataset.line = conversation.id;
      line.classList.toggle("pinned", Boolean(conversation.pinned_at));
      const button = row(conversation.title || "New chat", state.conversation === conversation.id,
        () => openConversation(conversation.id));
      button.dataset.conversation = conversation.id;
      // EACH ONE HAS ITS OWN MENU, as in other chat applications: pin it
      // to the top, rename it, or delete it - asked once, in the row.
      const more = document.createElement("button");
      more.type = "button";
      more.classList.add("chat-nav-more");
      more.textContent = "⋯";
      more.title = "Pin, rename or delete";
      more.setAttribute("aria-label", "Pin, rename or delete");
      more.setAttribute("aria-haspopup", "menu");
      more.disabled = state.busy;
      more.addEventListener("click", (event) => {
        event.stopPropagation();
        openMenu(line, conversation, more);
      });
      line.append(button, more);
      host.appendChild(line);
    }
  }

  function closeMenu() {
    document.querySelectorAll(".chat-nav-menu").forEach((one) => one.remove());
    document.querySelectorAll(".chat-nav-line.menu-open")
      .forEach((one) => one.classList.remove("menu-open"));
  }

  function openMenu(line, conversation, opener) {
    const wasOpen = line.classList.contains("menu-open");
    closeMenu();
    if (wasOpen) return;
    line.classList.add("menu-open");
    const menu = document.createElement("div");
    menu.classList.add("chat-nav-menu");
    menu.setAttribute("role", "menu");
    const item = (text, action, danger) => {
      const button = document.createElement("button");
      button.type = "button";
      button.setAttribute("role", "menuitem");
      button.textContent = text;
      if (danger) button.classList.add("danger-text");
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        closeMenu();
        action();
      });
      menu.appendChild(button);
      return button;
    };
    const first = item(conversation.pinned_at ? "Unpin" : "Pin",
      () => pinConversation(conversation, !conversation.pinned_at));
    item("Rename", () => startRename(line, conversation));
    // UNDER A TOPIC, or under none, or a new one (the user, 2026-11-03).
    for (const topic of state.topics) {
      if (topic.id !== conversation.project_id) {
        item(`Move to “${topic.name}”`, () => moveToTopic(conversation.id, topic.id));
      }
    }
    if (conversation.project_id) item("Take out of its topic", () => moveToTopic(conversation.id, ""));
    item("Move to a new topic…", () => startTopic(conversation.id));
    item("Delete", () => askToDelete(line, conversation), true);
    // ABOVE THE PAGE, not inside the list: the list scrolls, and a menu
    // inside it was cut off at its edge - Delete was below the fold. Placed
    // under the row's button, or above it where there is no room below.
    menu.style.position = "fixed";
    menu.style.visibility = "hidden";
    document.body.appendChild(menu);
    const at = opener.getBoundingClientRect();
    const tall = menu.offsetHeight;
    const below = at.bottom + 4 + tall <= window.innerHeight;
    menu.style.top = `${Math.max(4, below ? at.bottom + 4 : at.top - tall - 4)}px`;
    menu.style.left = `${Math.max(4, at.right - menu.offsetWidth)}px`;
    menu.style.visibility = "";
    first.focus();
    opener.setAttribute("aria-expanded", "true");
  }

  // Anywhere else, or Escape: the menu goes.
  document.addEventListener("click", (event) => {
    if (!event.target.closest?.(".chat-nav-menu")) closeMenu();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeMenu();
  });
  // Above the page, it would stay put while its row scrolled away.
  $s(".chat-sidebar-scroll").addEventListener("scroll", closeMenu, { passive: true });

  /* -- TOPICS ------------------------------------------------------------- */
  async function moveToTopic(id, topic) {
    try {
      await api.post("/api/conversation/topic", { id, topic });
      if (topic) state.open.add(topic);
      await refresh();
    } catch (error) {
      status(`Could not move the conversation: ${error.message}`);
    }
  }

  async function startTopic(conversationId = "") {
    // A new topic - its name asked in place, then its context to write.
    state.editing = `new:${conversationId}`;
    state.folded.delete("topics");
    render();
    $s("[data-topic-name]")?.focus();
  }

  function topicForm(topic, conversationId) {
    const form = document.createElement("form");
    form.className = "chat-topic-form";
    const name = document.createElement("input");
    name.type = "text";
    name.maxLength = 120;
    name.dataset.topicName = "";
    name.placeholder = "Topic name";
    name.setAttribute("aria-label", "Topic name");
    name.value = topic ? topic.name : "";
    const context = document.createElement("textarea");
    context.rows = 5;
    context.maxLength = 20000;
    context.placeholder = "Shared context - given to every chat in this topic";
    context.setAttribute("aria-label", "Shared context - given to every chat in this topic");
    context.value = topic ? topic.context || "" : "";
    const buttons = document.createElement("div");
    buttons.className = "chat-topic-buttons";
    const save = document.createElement("button");
    save.type = "submit";
    save.className = "small";
    save.textContent = topic ? "Save" : "Make the topic";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "small secondary";
    cancel.textContent = "Cancel";
    cancel.addEventListener("click", () => { state.editing = ""; render(); });
    buttons.append(save, cancel);
    if (topic) {
      const remove = document.createElement("button");
      remove.type = "button";
      remove.classList.add("small", "secondary", "danger-text");
      remove.textContent = "Delete topic";
      remove.title = "Its conversations are kept, in no topic";
      remove.addEventListener("click", async () => {
        try {
          await api.post("/api/chat/projects/delete", { id: topic.id });
          state.editing = "";
          await refresh();
        } catch (error) { status(`Could not delete the topic: ${error.message}`); }
      });
      buttons.append(remove);
    }
    form.append(name, context, buttons);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!name.value.trim()) { name.focus(); return; }
      try {
        let id = topic ? topic.id : "";
        if (!topic) id = (await api.post("/api/chat/projects", { name: name.value.trim() })).id;
        await api.post("/api/chat/projects/edit", { id, name: name.value.trim(), context: context.value });
        if (conversationId) await api.post("/api/conversation/topic", { id: conversationId, topic: id });
        state.open.add(id);
        state.editing = "";
        await refresh();
      } catch (error) { status(`Could not save the topic: ${error.message}`); }
    });
    return form;
  }

  const pinnedFirst = (list) => [...list.filter((one) => one.pinned_at),
    ...list.filter((one) => !one.pinned_at)];

  function renderTopics(host) {
    host.replaceChildren();
    if (state.editing.startsWith("new:")) host.appendChild(topicForm(null, state.editing.slice(4)));
    for (const topic of state.topics) {
      const section = document.createElement("section");
      section.className = "chat-topic";
      const chats = pinnedFirst(state.conversations.filter((one) => one.project_id === topic.id));
      const open = state.open.has(topic.id) || chats.some((one) => one.id === state.conversation);
      const head = document.createElement("div");
      head.className = "chat-topic-head";
      const toggle = row(`${open ? "▾" : "▸"} ${topic.name}`, false, () => {
        if (state.open.has(topic.id)) state.open.delete(topic.id); else state.open.add(topic.id);
        render();
      }, "chat-nav-row chat-topic-name");
      toggle.setAttribute("aria-expanded", String(open));
      const add = document.createElement("button");
      add.type = "button";
      add.className = "chat-nav-more";
      add.textContent = "＋";
      add.title = "New chat in this topic";
      add.setAttribute("aria-label", "New chat in this topic");
      add.addEventListener("click", () => { newChat(); state.pendingTopic = topic.id; status(`New chat in “${topic.name}”`); });
      const edit = document.createElement("button");
      edit.type = "button";
      edit.className = "chat-nav-more";
      edit.textContent = "✎";
      edit.title = "Edit topic & context";
      edit.setAttribute("aria-label", "Edit topic & context");
      edit.addEventListener("click", () => { state.editing = topic.id; render(); });
      head.append(toggle, add, edit);
      section.appendChild(head);
      if (state.editing === topic.id) section.appendChild(topicForm(topic, ""));
      if (topic.context && open) {
        const said = document.createElement("p");
        said.classList.add("chat-topic-context", "muted");
        said.textContent = topic.context;
        section.appendChild(said);
      }
      // Pinned, it stays in sight under its topic with the topic folded.
      const shown = open ? chats : chats.filter((one) => one.pinned_at);
      if (open || shown.length) {
        const list = document.createElement("div");
        list.className = "chat-topic-chats";
        historyRows(list, shown, "No conversations in this topic yet");
        section.appendChild(list);
      }
      host.appendChild(section);
    }
  }

  async function pinConversation(conversation, pinned) {
    try {
      await api.post("/api/conversation/pin", { id: conversation.id, pinned });
      await refresh();
    } catch (error) {
      status(`Could not pin the conversation: ${error.message}`);
    }
  }

  function startRename(line, conversation) {
    line.replaceChildren();
    line.classList.add("renaming");
    const input = document.createElement("input");
    input.type = "text";
    input.classList.add("chat-nav-rename");
    input.value = conversation.title || "";
    input.maxLength = 120;
    input.setAttribute("aria-label", "New name");
    let done = false;
    const finish = async (save) => {
      if (done) return;
      done = true;
      const title = input.value.trim();
      if (!save || !title || title === conversation.title) {
        render();
        return;
      }
      try {
        await api.post("/api/conversation/rename", { id: conversation.id, title });
        conversation.title = title;
        render();
        status("");
      } catch (error) {
        render();
        status(`Could not rename the conversation: ${error.message}`);
      }
    };
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") { event.preventDefault(); finish(true); }
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); finish(false); }
    });
    input.addEventListener("blur", () => finish(true));
    line.appendChild(input);
    input.focus();
    input.select();
  }

  function askToDelete(line, conversation) {
    line.replaceChildren();
    line.classList.add("asking");
    const said = document.createElement("span");
    said.className = "chat-nav-ask";
    said.textContent = "Delete this conversation? It cannot be brought back.";
    const yes = document.createElement("button");
    yes.type = "button";
    yes.classList.add("small", "danger");
    yes.textContent = "Delete";
    yes.addEventListener("click", () => deleteConversation(conversation.id));
    const no = document.createElement("button");
    no.type = "button";
    no.className = "small secondary";
    no.textContent = "Keep";
    no.addEventListener("click", () => render());
    line.append(said, yes, no);
    yes.focus();
  }

  async function deleteConversation(id) {
    try {
      await api.post("/api/conversation/delete", { id });
      state.conversations = state.conversations.filter((one) => one.id !== id);
      // The one on screen gone with it: an empty chat in its place.
      if (state.conversation === id) newChat();
      else render();
      status("");
    } catch (error) {
      render();
      status(`Could not delete the conversation: ${error.message}`);
    }
  }

  function render() {
    const current = state.conversations.find((one) => one.id === state.conversation);
    const title = layout.querySelector("[data-chat-title]");
    if (title) title.textContent = current?.title || "New chat";
    const list = $s(".chat-history");
    list.replaceChildren();
    // PINNED, ON TOP OF ITS OWN PART (the user, 2026-11-06): a chat's among the chats, one under
    // a topic under that topic. Every other conversation, those once filed under a project too,
    // is a chat.
    renderTopics($s(".chat-topics"));
    const known = new Set(state.topics.map((one) => one.id));
    historyRows(list, pinnedFirst(state.conversations.filter(
      (one) => !(one.project_id && known.has(one.project_id)))),
      state.topics.length ? "" : "No conversations yet");
    for (const [part, host] of [["topics", ".chat-topics"], ["chats", ".chat-history"]]) {
      const folded = state.folded.has(part);
      $s(host).hidden = folded;
      const label = $s(`[data-fold="${part}"]`);
      label.setAttribute("aria-expanded", String(!folded));
      label.querySelector(".chat-fold-mark").textContent = folded ? "▸" : "▾";
    }
  }

  async function refresh() {
    if (state.loading) return;
    state.loading = true;
    try {
      const [conversations, topics] = await Promise.all([api.get("/api/conversations"),
        api.get("/api/chat/projects").catch(() => ({ projects: state.topics }))]);
      state.conversations = conversations.conversations || [];
      state.topics = topics.projects || [];
      render();
      status("");
    } catch (error) {
      status(`Could not load the conversations: ${error.message}`);
    } finally { state.loading = false; }
  }

  async function openConversation(id, push = true) {
    if (state.busy) return;
    const request = ++state.request;
    status("Opening the conversation…");
    try {
      const conversation = await api.get(`/api/conversation?id=${encodeURIComponent(id)}`);
      // Another was chosen while this one loaded: that one wins.
      if (request !== state.request) return;
      state.conversation = conversation.conversation_id;
      onOpen(conversation);
      render();
      updateUrl(push);
      status("");
      if (mobile.matches) setOpen(false, true);
    } catch (error) {
      if (request === state.request) status(`Could not open the conversation: ${error.message}`);
    }
  }

  function newChat(push = true) {
    if (state.busy) return;
    ++state.request;
    state.conversation = null;
    state.pendingTopic = "";
    onOpen({ conversation_id: null, messages: [], followups: [] });
    render();
    updateUrl(push);
    status("");
    if (mobile.matches) setOpen(false);
    document.querySelector("#chat-input")?.focus();
  }

  function setBusy(busy) {
    state.busy = busy;
    sidebar.setAttribute("aria-busy", String(busy));
    sidebar.querySelectorAll("button, input").forEach((control) => {
      if (!control.matches("[data-close]")) control.disabled = busy;
    });
  }

  opener?.addEventListener("click", () => setOpen(true));
  $s("[data-close]").addEventListener("click", () => setOpen(false, true));
  backdrop.addEventListener("click", () => setOpen(false, true));
  $s("[data-new]").addEventListener("click", () => newChat());
  $s("[data-new-topic]").addEventListener("click", () => startTopic(""));
  document.addEventListener("keydown", (event) => {
    if (!mobile.matches || !drawerOpen) return;
    if (event.key === "Escape") { event.preventDefault(); setOpen(false, true); }
    if (event.key === "Tab") {
      const controls = Array.from(sidebar.querySelectorAll("button, input"))
        .filter((control) => !control.disabled && control.getClientRects().length);
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });
  mobile.addEventListener("change", () => { drawerOpen = false; syncLayout(); });
  window.addEventListener("popstate", () => {
    if (state.busy) { updateUrl(false); return; }
    document.querySelector('#tabs button[data-tab="chat"]')?.click();
    const params = route();
    if (params.get("conversation")) openConversation(params.get("conversation"), false);
    else newChat(false);
  });
  syncLayout();

  return {
    /* The list again, and - the first time - the conversation the address
       names, which is how the desktop opens the one it was last in. */
    async activate() {
      await refresh();
      if (!state.initialized) {
        state.initialized = true;
        if (route().get("conversation")) await openConversation(route().get("conversation"), false);
      }
    },
    setBusy,
    /* The conversation a message is about to be sent in: none for a new
       chat, which the first message makes. */
    async prepareConversation() {
      ++state.request;
      return state.conversation;
    },
    async recordConversation(id) {
      state.conversation = id;
      // Started under a topic: put there now that it is made.
      if (state.pendingTopic && id) {
        const topic = state.pendingTopic;
        state.pendingTopic = "";
        api.post("/api/conversation/topic", { id, topic }).then(refresh).catch(() => {});
      }
      updateUrl(false);
      await refresh();
    },
  };
};
