/* THE BOX'S WINDOW, THERE AT ONCE (the user, 2026-11-02: the Delegate work
   box "弹出来要卡一下" - its context can be collected after). Opened by the
   right-click in this browser, already running; the box itself opens where
   it is once the agent has kept what was picked (background.js). */

const ext = globalThis.browser || globalThis.chrome;

function word(key, fallback) {
  try { return ext.i18n.getMessage(key) || fallback; } catch (_err) { return fallback; }
}

document.getElementById("title").textContent = word("delegate", "Delegate work");
document.title = word("delegate", "Delegate work") + " - Personal Agent WaterMelon";
document.getElementById("said").textContent = word("opening", "Opening…");
