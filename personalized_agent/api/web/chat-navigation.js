/* THE CHAT'S SIDEBAR: every conversation, to reopen, and a new one - chats,
   in one list, newest first; no projects to file them under. Ported from
   PR #4 (by daniela-miaut).

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
    <button type="button" class="chat-new" data-new><span aria-hidden="true">＋</span> New chat</button>
    <div class="chat-sidebar-scroll">
      <div class="chat-section-label">Chats</div>
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

  const $s = (selector) => sidebar.querySelector(selector);
  const mobile = window.matchMedia("(max-width: 700px)");
  const route = () => new URLSearchParams(location.search);
  const state = { conversations: [], conversation: null,
    busy: false, request: 0, loading: false, initialized: false };
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

  function historyRows(host, conversations) {
    if (!conversations.length) {
      const empty = document.createElement("p");
      empty.className = "chat-nav-empty muted";
      empty.textContent = "No conversations yet";
      host.appendChild(empty);
    }
    for (const conversation of conversations) {
      const button = row(conversation.title || "New chat", state.conversation === conversation.id,
        () => openConversation(conversation.id));
      button.dataset.conversation = conversation.id;
      host.appendChild(button);
    }
  }

  function render() {
    const current = state.conversations.find((one) => one.id === state.conversation);
    const title = layout.querySelector("[data-chat-title]");
    if (title) title.textContent = current?.title || "New chat";
    const list = $s(".chat-history");
    list.replaceChildren();
    // Every conversation, those once filed under a project too: one list.
    historyRows(list, state.conversations);
  }

  async function refresh() {
    if (state.loading) return;
    state.loading = true;
    try {
      const conversations = await api.get("/api/conversations");
      state.conversations = conversations.conversations || [];
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
      updateUrl(false);
      await refresh();
    },
  };
};
