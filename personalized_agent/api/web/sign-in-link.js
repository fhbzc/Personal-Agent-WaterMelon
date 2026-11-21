/* A SIGN-IN LINK WHEREVER IT IS SAID (the user, 2026-11-21: "你提示的Other message里也可以加上登录链接，而不是让我去claude
   auth login"; "你的message要给我一个超链接呀"): in any line on the page that says the Claude client is not signed in - a
   notification in the side panel's chat, a model's reason in Settings, a reply that could not be made - the words
   "Sign in to Claude Code" (or "登录 Claude Code", or an old "claude auth login") made a link, else a link put at its end.
   Pressed, the client's own sign-in opens in a terminal (api/routes.py /api/models/claude/sign_in); then the link asks to
   check again. On every page (index, chat, the side panel), whatever drew the line. */
(function signInLinks() {
  const SAID = /Claude Code(?: \([^)]{1,20}\))? is not signed in|Claude Code 没有登录|Claude Code CLI sign-in|claude auth login/i;
  const PHRASE = /Sign in to Claude Code|登录 Claude Code|`?claude auth login`?/i;
  const words = (text) => {
    try { return (window.interfaceLanguage && window.interfaceLanguage.lookUp(text)) || text; } catch (_err) { return text; }
  };
  async function post(path, body) {
    const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" },
                                         body: JSON.stringify(body || {}) });
    return response.ok ? response.json() : {};
  }
  function link(text) {
    const a = document.createElement("a");
    a.href = "#";
    a.className = "sign-in-link";
    a.dataset.signInLink = "claude_code";
    a.textContent = text;
    return a;
  }
  function skip(element) {
    return !element || element.closest("[data-sign-in-link], .side-macperm, .model-link, script, style, textarea, input");
  }
  /* ONE LINK A MESSAGE (the user, 2026-11-21: "你有的message sign in to claude code写了两遍"): the message the line is in -
     a notification, a line of the chat, a row - and none put where it has one already. */
  function messageOf(element) {
    return element.closest(".notes-item, .note, [data-note], [data-who], li, article, tr, .chain-why") || element;
  }
  function linkIn(node) {
    const element = node.parentElement;
    const text = node.nodeValue || "";
    if (skip(element) || !SAID.test(text)) return;
    const message = messageOf(element);
    if (message.querySelector && message.querySelector("[data-sign-in-link]")) return;   // its link is there already
    const found = text.match(PHRASE);
    if (found) {                                   // the words themselves made the link
      const before = text.slice(0, found.index), after = text.slice(found.index + found[0].length);
      const parts = [document.createTextNode(before), link(/claude auth login/i.test(found[0]) ? words("Sign in to Claude Code") : found[0]),
                     document.createTextNode(after)];
      node.replaceWith(...parts);
      return;
    }
    if (element.querySelector && element.querySelector("[data-sign-in-link]")) return;
    node.after(document.createTextNode(" "), link(words("Sign in to Claude Code")));
  }
  function look(root) {
    if (!root) return;
    if (root.nodeType === 3) { linkIn(root); return; }
    if (!root.querySelectorAll) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const hits = [];
    for (let node = walker.nextNode(); node; node = walker.nextNode()) if (SAID.test(node.nodeValue || "")) hits.push(node);
    hits.forEach(linkIn);
  }
  document.addEventListener("click", async (event) => {
    const a = event.target.closest && event.target.closest("[data-sign-in-link]");
    if (!a) return;
    event.preventDefault();
    event.stopPropagation();
    if (a.dataset.checking) {
      try { await post("/api/models/claude/recheck", { provider: "claude_code" }); } catch (_err) { /* the page shows it */ }
      a.textContent = words("Checked");
      return;
    }
    let said = {};
    try { said = await post("/api/models/claude/sign_in", { provider: "claude_code" }); } catch (_err) { said = {}; }
    a.dataset.checking = "1";
    a.textContent = words(said.opened ? "Signed in? Check again" : "Open a terminal and run claude auth login, then check again");
  }, true);
  const watch = () => {
    look(document.body);
    new MutationObserver((changes) => {
      for (const change of changes) {
        if (change.type === "characterData") { look(change.target); continue; }
        for (const node of change.addedNodes) look(node);
      }
    }).observe(document.body, { childList: true, subtree: true, characterData: true });
  };
  if (document.body) watch(); else document.addEventListener("DOMContentLoaded", watch);
})();
