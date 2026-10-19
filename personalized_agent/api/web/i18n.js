/* THE INTERFACE IN THE DISPLAY LANGUAGE (Settings > Display; English by
   default).

   The application's own words - its tabs, headings, buttons, labels, the
   explanations under them and the states it shows - are translated where
   they appear, as they are drawn, from a fixed table for each language:
   lang/<code>.json, English on the left, the language's own words on the
   right. Nothing is translated on the fly. A text, or a placeholder, title
   or label, that is exactly one of the table's entries is replaced; one
   made of the application's words around a number, a name or a date is
   matched to an entry with numbered places ({0}, {1}...), and those places
   are carried over - each, when it is itself one of the table's words, in
   the language too. Nothing else is touched: what the agent wrote, the
   user's own records, names, paths and code stay as they were written.
   Dates and times the page writes follow the language as well.

   The desktop's menus and the notification's buttons read the same tables
   (common/interface_words.py). Changing the language draws the page again
   (app.js). */
(() => {
  // The languages the screens can be shown in, and the locale their dates
  // and times are written in.
  const LOCALES = { zh: "zh-CN", es: "es-ES", ja: "ja-JP", ko: "ko-KR", ru: "ru-RU",
                    fr: "fr-FR", de: "de-DE", it: "it-IT" };
  const ATTRIBUTES = ["placeholder", "title", "aria-label", "label"];
  // Where the words are the user's or the agent's, never the application's.
  const LEAVE = ".msg-text, .chat-message, .fu-body, .cx-summary, .xp-live-text, .lesson-card-words, .xp-added-words, textarea, pre, code";
  const SLOT = /\{(\d+)\}/g;
  let exact = null;
  let patterns = [];
  const ours = new WeakMap();        // a text we put there, not to be read again
  const oursSet = new WeakMap();     // and an attribute: element -> {name: value}

  // An entry with places: its words in order, the places' numbers, and the
  // longest of the words - to pass over most texts at once.
  function compile(table) {
    exact = {};
    patterns = [];
    Object.keys(table).forEach((key) => {
      const value = table[key];
      if (typeof value !== "string") return;     // "": nothing, in this language
      if (!/\{\d+\}/.test(key)) { exact[key] = value; return; }
      const parts = key.split(/\{\d+\}/);
      const order = [...key.matchAll(SLOT)].map((m) => m[1]);
      // Too few words of its own, it would take the user's words for ours.
      if (parts.join("").replace(/[^\p{L}]/gu, "").length < 3) return;
      const longest = parts.reduce((a, b) => (b.length > a.length ? b : a), "");
      patterns.push({ parts, order, value, longest, size: parts.join("").length });
    });
    patterns.sort((a, b) => b.size - a.size);       // the most particular first
  }

  // The words found in order, from the left, each place what lies between
  // them - in one pass. (A pattern with several places, tried against a long
  // explanation, backtracks without end.)
  function matched(parts, text) {
    const first = parts[0];
    const last = parts[parts.length - 1];
    if (!text.startsWith(first) || !text.endsWith(last)) return null;
    const end = text.length - last.length;
    const found = [];
    let at = first.length;
    for (let i = 1; i < parts.length - 1; i += 1) {
      const next = text.indexOf(parts[i], at);
      if (next < 0 || next > end) return null;
      found.push(text.slice(at, next));
      at = next + parts[i].length;
    }
    if (at > end) return null;
    found.push(text.slice(at, end));
    return found;
  }

  // Each place filled with what was found there - itself in the language
  // when it is one of the table's texts ("every 2 minutes", "Started by
  // you, last 72 hours: ...").
  function filled(value, order, found, depth) {
    return value.replace(SLOT, (_m, slot) => {
      const at = order.indexOf(slot);
      const said = at >= 0 ? found[at] : "";
      const bare = said.trim();
      const word = bare && depth < 3 ? lookUp(bare, depth + 1) : undefined;
      return word !== undefined ? said.replace(bare, word) : said;
    });
  }

  const lookUp = (text, depth = 0) => {
    const key = String(text || "").replace(/\s+/g, " ").trim();
    if (!key) return undefined;
    if (exact[key] !== undefined) return exact[key];
    if (!/[A-Za-z]/.test(key)) return undefined;
    for (const p of patterns) {
      if (p.longest && !key.includes(p.longest)) continue;
      const found = matched(p.parts, key);
      if (found) return filled(p.value, p.order, found, depth);
    }
    return undefined;
  };

  function translateText(node) {
    const raw = node.nodeValue;
    if (!raw || !raw.trim() || ours.get(node) === raw) return;
    const parent = node.parentElement;
    if (parent && parent.closest(LEAVE)) return;
    const hit = lookUp(raw);
    if (hit !== undefined && hit !== raw.trim()) {
      const next = raw.match(/^\s*/)[0] + hit + raw.match(/\s*$/)[0];
      ours.set(node, next);
      node.nodeValue = next;
    }
  }

  function translateAttributes(element) {
    if (!element.getAttribute) return;
    // A text box's words are the user's; its placeholder and title are ours.
    if (element.parentElement && element.parentElement.closest(LEAVE)) return;
    if (element.matches(LEAVE) && !/^(TEXTAREA|INPUT)$/.test(element.tagName)) return;
    ATTRIBUTES.forEach((name) => {
      const value = element.getAttribute(name);
      if (!value) return;
      const put = oursSet.get(element) || {};
      if (put[name] === value) return;
      const hit = lookUp(value);
      if (hit !== undefined && hit !== value) {
        put[name] = hit;
        oursSet.set(element, put);
        element.setAttribute(name, hit);
      }
    });
  }

  function translate(root) {
    if (!root || !exact) return;
    if (root.nodeType === Node.TEXT_NODE) { translateText(root); return; }
    if (root.nodeType !== Node.ELEMENT_NODE) return;
    translateAttributes(root);
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    let node = walker.nextNode();
    while (node) {
      if (node.nodeType === Node.TEXT_NODE) translateText(node);
      else translateAttributes(node);
      node = walker.nextNode();
    }
  }

  // Dates and times written with the page's default locale are written in
  // the display language's instead; one asked for in a named locale is not
  // changed.
  function datesIn(locale) {
    const unnamed = (locales) => locales === undefined || locales === null
      || (Array.isArray(locales) && !locales.length);
    ["toLocaleString", "toLocaleDateString", "toLocaleTimeString"].forEach((name) => {
      const original = Date.prototype[name];
      Date.prototype[name] = function (locales, options) {
        return original.call(this, unnamed(locales) ? locale : locales, options);
      };
    });
    const Format = Intl.DateTimeFormat;
    const Shown = function (locales, options) {
      return new Format(unnamed(locales) ? locale : locales, options);
    };
    Shown.prototype = Format.prototype;
    Shown.supportedLocalesOf = Format.supportedLocalesOf;
    Intl.DateTimeFormat = Shown;
  }

  function remembered() {
    try { return localStorage.getItem("display-language") || ""; } catch (err) { return ""; }
  }

  function remember(language) {
    try { localStorage.setItem("display-language", language); } catch (err) { /* only a convenience */ }
  }

  // English is written in this browser's own English, or American English.
  const localeOf = (language) => LOCALES[language]
    || (/^en\b/i.test(navigator.language || "") ? navigator.language : "en-US");

  // The display language this page was drawn in last time - its dates are
  // right from the first one drawn, before the settings are read.
  const before = remembered();
  if (before) datesIn(localeOf(before));

  function shownIn(code) {
    const shown = String(code || "en").toLowerCase();
    return shown.startsWith("zh") ? "zh" : shown.split(/[-_]/)[0];
  }

  async function start() {
    let language = "en";
    try {
      const response = await fetch("/api/settings", { headers: { Accept: "application/json" } });
      if (response.ok) language = shownIn(((await response.json()).values || {}).display_language);
    } catch (err) {
      return;                       // English, as the page was written
    }
    remember(language);
    if (before !== language) datesIn(localeOf(language));
    if (!LOCALES[language]) return;
    let table = null;
    try {
      const response = await fetch(`/lang/${language}.json`, { headers: { Accept: "application/json" } });
      if (response.ok) table = await response.json();
    } catch (err) {
      return;
    }
    if (!table || !Object.keys(table).length) return;
    use(table, language);
  }

  function use(table, language) {
    compile(table);
    document.documentElement.lang = LOCALES[language] || language;
    translate(document.body);
    new MutationObserver((changes) => {
      changes.forEach((change) => {
        if (change.type === "characterData") translateText(change.target);
        else if (change.type === "attributes") translateAttributes(change.target);
        else change.addedNodes.forEach(translate);
      });
    }).observe(document.body, { subtree: true, childList: true, characterData: true,
                                attributes: true, attributeFilter: ATTRIBUTES });
  }

  // For a test: put a table in use, and translate one text with it.
  window.interfaceLanguage = { locales: LOCALES, translate,
                               lookUp: (t) => (exact ? lookUp(t) : undefined),
                               use: (table, language) => use(table, language || "zh") };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
