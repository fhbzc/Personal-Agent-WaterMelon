/* A RIGHT-CLICK MENU WHERE THE WINDOW HAS NONE (the user, 2026-11-16: "你现在那些summary, 要能选中并且右键，支持复制什么的"):
   the agent's own windows (the side panel, the report - pywebview's WebView2) have the browser's menu turned off, so a
   right-click did nothing. In them only - a page in the user's own browser keeps the browser's menu - one of the
   page's own: Copy what is selected, Copy the message pressed on, Select it, and - on an answer of the agent's in the
   side panel - Ask about what is selected (notes.js sideAskForm). */
(() => {
  let menu = null;
  const ours = () => Boolean(window.pywebview);           // the agent's own window, not the user's browser
  const close = () => { if (menu) { menu.remove(); menu = null; } };

  async function copy(text) {
    if (!text) return;
    try { await navigator.clipboard.writeText(text); return; } catch (_err) { /* the old way, then */ }
    const box = document.createElement("textarea");
    box.value = text;
    box.style.cssText = "position:fixed;left:-9999px;top:0";
    document.body.appendChild(box);
    box.select();
    try { document.execCommand("copy"); } catch (_err) { /* nothing more to try */ }
    box.remove();
  }

  // The piece of the page a right-click is on: a message, a card, a block of words.
  const pieceAt = (target) => target.closest && target.closest(
    ".side-msg, .side-followup, .side-told, .msg, .report-card, .card, .notes-text, p, li, td, pre") || null;

  document.addEventListener("contextmenu", (event) => {
    if (!ours()) return;
    // NONE OVER THE WORLD (the user, 2026-11-19: "在play view下不能右键弹出copy的panel"): a right-click in the Play view
    // opens nothing - neither this menu nor the browser's.
    if (event.target.closest && event.target.closest(".side-play")) { event.preventDefault(); close(); return; }
    if (event.target.closest && event.target.closest("input, textarea, [contenteditable='true']")) return;
    event.preventDefault();
    close();
    const picked = String(window.getSelection ? window.getSelection() : "").trim();
    const piece = pieceAt(event.target);
    const answer = piece && piece.matches && piece.matches(".side-msg.agent[data-message]:not(.side-note)") ? piece : null;
    const items = [
      { label: "Copy", off: !picked, run: () => copy(picked) },
      { label: "Copy this message", off: !piece, run: () => copy(piece ? piece.innerText.trim() : "") },
      { label: "Select it", off: !piece, run: () => {
        const range = document.createRange();
        range.selectNodeContents(piece);
        const chosen = window.getSelection();
        chosen.removeAllRanges();
        chosen.addRange(range);
      } },
    ];
    if (answer && typeof sideAskForm === "function") {
      items.push({ label: "Ask about", off: false, run: () => sideAskForm(answer, picked && picked !== answer.innerText.trim()
        ? picked.slice(0, 300) : "") });
    }
    menu = document.createElement("div");
    menu.className = "page-menu";
    menu.setAttribute("role", "menu");
    items.forEach((one) => {
      const button = document.createElement("button");
      button.type = "button";
      button.setAttribute("role", "menuitem");
      button.textContent = one.label;
      button.disabled = one.off;
      button.onmousedown = (press) => press.preventDefault();   // the selection kept
      button.onclick = () => { close(); one.run(); };
      menu.appendChild(button);
    });
    document.body.appendChild(menu);
    // ITS TOP LEFT CORNER WHERE IT WAS PRESSED (the user, 2026-11-17: "右键的位置是panel的左上角"): not pushed along to
    // fit - opened to the left of the pointer (its top right corner there) only where there is no room on its right,
    // above it where there is none below. In the page's own pixels: its zoom (the side panel's) taken off.
    const room = menu.getBoundingClientRect();
    const zoom = parseFloat(getComputedStyle(document.documentElement).zoom) || 1;
    let x = event.clientX;
    let y = event.clientY;
    if (x + room.width > window.innerWidth - 2) x = Math.max(2, x - room.width);
    if (y + room.height > window.innerHeight - 2) y = Math.max(2, y - room.height);
    menu.style.left = `${x / zoom}px`;
    menu.style.top = `${y / zoom}px`;
  });
  document.addEventListener("mousedown", (event) => { if (menu && !menu.contains(event.target)) close(); }, true);
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") close(); }, true);
  window.addEventListener("blur", close);
  document.addEventListener("scroll", close, true);
})();
