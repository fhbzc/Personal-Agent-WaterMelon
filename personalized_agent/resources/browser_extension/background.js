/* THE AGENT'S BROWSER EXTENSION (platform_layer/browser_extension.py) - one
   extension, to carry more of the agent's parts in the browser later.

   Today, DELEGATE WORK on the browser's own right-click menu (the user,
   2026-11-01: "网页选字什么和网址一样，都是右键delegate work") - on the page,
   on selected text, on a link, on a picture. No script runs in the pages
   before that, so the extension asks for no access to every site: once
   Delegate work is picked, the page right-clicked - and only it - is read
   (the selection as it is, its paragraph, the page's words; activeTab).
   What was picked goes to the agent on this computer by the browser's native
   messaging - no network, no port - and the agent opens its box on it.
   Nothing is sent anywhere else. */

const ext = globalThis.browser || globalThis.chrome;
const HOST = "com.watermelon.personal_agent";
const MENU = "delegate-work";
const AROUND = 4000, PAGE = 8000;

function word(key, fallback) {
  try { return ext.i18n.getMessage(key) || fallback; } catch (_err) { return fallback; }
}

function browserName() {
  const agent = navigator.userAgent || "";
  if (/Edg\//.test(agent)) return "Microsoft Edge";
  if (/Firefox\//.test(agent)) return "Firefox";
  if (/OPR\//.test(agent)) return "Opera";
  if (/Vivaldi/.test(agent)) return "Vivaldi";
  if (navigator.brave) return "Brave";
  return "Google Chrome";
}

/* HERE, IN THIS BROWSER: the agent told, so that its own icon beside
   selected text on Windows leaves this browser's pages to this menu. */
function sayHere() {
  Promise.resolve(ext.runtime.sendNativeMessage(HOST, { type: "here", browser: browserName() }))
    .catch(() => { /* not connected yet: the install page says how */ });
}

function menus() {
  ext.contextMenus.removeAll(() => {
    ext.contextMenus.create({
      id: MENU, title: word("delegate", "Delegate work"),
      contexts: ["page", "selection", "link", "image", "frame", "video", "audio"],
    });
  });
}
ext.runtime.onInstalled.addListener(() => { menus(); sayHere(); });
if (ext.runtime.onStartup) ext.runtime.onStartup.addListener(() => { menus(); sayHere(); });

function notConnected(why) {
  ext.tabs.create({ url: ext.runtime.getURL("help.html") + "#" + encodeURIComponent(why || "") });
}

/* Run in the page right-clicked, once Delegate work is picked: the
   selection as it is (the menu's own copy loses its line breaks), the
   paragraph it is in, and the page's words - each so far. */
function readThePage(around, most) {
  const said = { selected: "", around: "", page: "" };
  try {
    const selection = window.getSelection();
    said.selected = selection ? String(selection) : "";
    if (selection && selection.rangeCount) {
      let node = selection.getRangeAt(0).commonAncestorContainer;
      if (node && node.nodeType !== 1) node = node.parentElement;
      while (node && node.parentElement && (node.innerText || "").length < 200) {
        node = node.parentElement;
      }
      said.around = node ? String(node.innerText || "").slice(0, around) : "";
    }
    said.page = String((document.body && document.body.innerText) || "").slice(0, most);
  } catch (_err) { /* a page that will not be read: its address is enough */ }
  return said;
}

async function pageWords(tab, info) {
  if (!ext.scripting || !tab || tab.id === undefined) return {};
  try {
    const target = { tabId: tab.id };
    if (info.frameId) target.frameIds = [info.frameId];
    const found = await ext.scripting.executeScript({ target, func: readThePage,
                                                      args: [AROUND, PAGE] });
    return (found && found[0] && found[0].result) || {};
  } catch (_err) {
    return {};   // the browser's own pages, the stores: not to be read
  }
}

/* THE BOX AT ONCE, IN THIS BROWSER (the user, 2026-11-02: "delegate work
   panel 弹出来要卡一下。这个不应该，因为很多context是可以之后慢慢收集的").
   This browser is running already, where the agent's own window was a
   browser started cold - and the box is opened the moment Delegate work is
   picked, as the page is read and the agent told, not after:

   * Chromium browsers: the box itself, on the note the agent is being
     handed (named here). Signed in already - the browser's sign-in lasts
     as long as it runs - it is there at once, and waits a moment for the
     note. Not yet: the box asks this extension for its sign-in (only the
     agent's own page can, `externally_connectable`) and signs in from its
     own page - opened from a site, the agent signs in nothing.
   * Firefox, whose pages cannot ask an extension: a window saying it is
     opening (opening.html), and the box in its place once the agent says
     its address.

   Opened by the browser, never navigated to from the extension's page: the
   agent signs in only a page opened from outside a site (Sec-Fetch-Site),
   and the box closes its own window when it is done. */
const BOX_W = 780, BOX_H = 900;
const IS_FIREFOX = /Firefox\//.test(navigator.userAgent || "");
const DEFAULT_BASE = "http://127.0.0.1:8756";
const signIns = new Map();          // note id -> {said} or {answer}

/* Where the agent's page is: read once as the extension starts, and kept
   here - never waited for at a right-click (reading the extension's
   storage took Edge three seconds, measured). */
const BASE_FORM = /^http:\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$/;
let knownBase = DEFAULT_BASE;
try {
  Promise.resolve(ext.storage.local.get("base")).then((kept) => {
    if (kept && BASE_FORM.test(kept.base || "")) knownBase = kept.base;
  }).catch(() => {});
} catch (_err) { /* the default */ }

function settleSignIn(id, said) {
  const one = signIns.get(id) || {};
  if (one.answer) { one.answer(said); signIns.delete(id); }
  else signIns.set(id, { said });
  setTimeout(() => signIns.delete(id), 120000);
}

if (ext.runtime.onMessageExternal) {
  ext.runtime.onMessageExternal.addListener((message, sender, answer) => {
    if (!message || message.type !== "sign-in" || typeof message.id !== "string") return false;
    if (!/^http:\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d+)?\//.test((sender && sender.url) || "")) {
      return false;
    }
    const one = signIns.get(message.id);
    if (one && one.said) { signIns.delete(message.id); answer(one.said); return false; }
    signIns.set(message.id, { answer });
    return true;                    // answered once the agent has said
  });
}

async function boxBounds(tab) {
  const size = { width: BOX_W, height: BOX_H };
  try {
    const around = await ext.windows.get(tab.windowId);
    size.height = Math.min(BOX_H, around.height || BOX_H);
    size.width = Math.min(BOX_W, around.width || BOX_W);
    size.left = Math.round((around.left || 0) + ((around.width || BOX_W) - size.width) / 2);
    size.top = Math.round((around.top || 0) + ((around.height || BOX_H) - size.height) / 2);
  } catch (_err) { /* where the browser puts it */ }
  return size;
}

async function popup(url, size) {
  try {
    return await ext.windows.create(Object.assign({ url, type: "popup", focused: true }, size));
  } catch (_err) {
    return null;
  }
}

/* THE AGENT'S HOST STARTED AT THE CLICK, ASKED ONCE THE PAGE IS READ: its
   start (a program's, half a second) goes on while the page is read. One
   message, its answer, and the host let go. */
function startHost() {
  const state = { said: null, gone: null, waiting: [] };
  let port = null;
  try {
    port = ext.runtime.connectNative(HOST);
  } catch (err) {
    state.gone = String((err && err.message) || err || "");
  }
  const settle = () => {
    while (state.waiting.length) {
      const one = state.waiting.shift();
      if (state.said) one.resolve(state.said); else one.reject(new Error(state.gone || ""));
    }
  };
  if (port) {
    port.onMessage.addListener((said) => {
      if (state.said) return;
      state.said = said;
      settle();
      try { port.disconnect(); } catch (_err) { /* gone already */ }
    });
    port.onDisconnect.addListener(() => {
      if (state.said) return;
      state.gone = (ext.runtime.lastError && ext.runtime.lastError.message) || "the agent's host closed";
      settle();
    });
  }
  return (message) => new Promise((resolve, reject) => {
    if (state.said) { resolve(state.said); return; }
    if (state.gone !== null) { reject(new Error(state.gone)); return; }
    state.waiting.push({ resolve, reject });
    try {
      port.postMessage(message);
    } catch (err) {
      state.gone = String((err && err.message) || err || "");
      settle();
    }
  });
}

function noteId() {
  const bytes = new Uint8Array(12);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

async function delegateClicked(info, tab) {
  if (info.menuItemId !== MENU) return;
  const id = noteId();
  const ask = startHost();
  // At once - before the page is read or the agent asked.
  const size = await boxBounds(tab || {});
  const base = knownBase;
  const opened = IS_FIREFOX
    ? popup(ext.runtime.getURL("opening.html"), size)
    : popup(`${base}/?solo=delegate&delegate_inbox=${id}&ext=${ext.runtime.id}`, size);
  const words = await pageWords(tab, info);
  const selected = words.selected || info.selectionText || "";
  const picked = {
    where: "browser", app: browserName(),
    kind: selected ? "selection" : info.linkUrl ? "link" : info.srcUrl ? "image" : "page",
    url: info.frameUrl || info.pageUrl || (tab && tab.url) || "",
    title: (tab && tab.title) || "",
    text: selected, around: selected ? (words.around || "") : "",
    page: words.page || "",
    link: info.linkUrl || "", link_text: info.linkText || "", src: info.srcUrl || "",
  };
  const gone = async () => {
    const one = await opened;
    if (one && one.id !== undefined) ext.windows.remove(one.id).catch(() => {});
  };
  try {
    // "opens": this browser shows the box - the agent keeps the note under
    // `id` and says the box's address, signed in.
    const said = await ask({ type: "delegate", picked, opens: true, id });
    if (!said || !said.ok) {
      settleSignIn(id, {});
      await gone();
      notConnected((said && said.error) || "");
      return;
    }
    if (!said.url) {
      // Opened by the agent itself, as before (it was not running).
      settleSignIn(id, {});
      await gone();
      return;
    }
    const origin = new URL(said.url).origin;
    if (origin !== base) {
      knownBase = origin;
      Promise.resolve(ext.storage.local.set({ base: origin })).catch(() => {});
    }
    if (IS_FIREFOX || origin !== base) {
      // The box where the window was, at the agent's own address.
      const one = await opened;
      let where = size;
      try {
        if (one && one.id !== undefined) {
          const now = await ext.windows.get(one.id);
          where = { width: now.width, height: now.height, left: now.left, top: now.top };
        }
      } catch (_err) { /* as it was opened */ }
      settleSignIn(id, {});
      await popup(said.url, where);
      await gone();
      return;
    }
    settleSignIn(id, { url: said.url });   // the box signs in with it, if it must
  } catch (err) {
    settleSignIn(id, {});
    await gone();
    notConnected(String((err && err.message) || err || ""));
  }
}
ext.contextMenus.onClicked.addListener(delegateClicked);
