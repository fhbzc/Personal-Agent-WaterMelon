/* THE SMALL SCREEN - A PICTURE THE USER AND THE AGENT SHARE (the user, 2026-11-17: "点开之后会打开一个小屏幕（在sidebar的
   上方 ... 覆盖在chat上，不需要chat缩放)"; "image界面可以允许模型画图，也允许我做screenshot, 总之就是里面有图片，我可以edit,
   然后模型会看到这个(类似于一个视觉版的Interation界面)"; "本质上只需要一个按钮").

   One button on the panel's edge opens it, over the top of the chat - the chat not made smaller. In it, one picture:
   the screen as it is now (Screen), one of the conversation's pictures (Pictures), or what the agent drew beside an
   answer (side-visual.js). On it the user draws - a line, a box, an arrow, words. What it holds goes with the next
   message: a picture, drawn on, as a picture of the message (shown in the user's own bubble) - in place of the screen
   the message would have taken; on the agent's drawing, what each mark is on, in words (side-visual.js sideParts),
   as the agent knows the drawing already. Only what changed since the last message goes.

   MANY OF THEM, AS A DECK'S SLIDES (the user, 2026-11-17: "要支持我创建新的image (默认空白), 然后image panel 旁边有个可折叠
   panel是选最近用过的image (包括agent自己生成的)，最多保存100个(是类似于slide 侧边页的设计)"): each picture put in - a new
   whiteboard, the screen, one dropped or chosen, one the agent drew - is a page of its own, with its drawing; the pages
   in a strip beside it, folded away or not, the latest used first; kept in this panel's own store (IndexedDB) between
   starts, a hundred at most - the least lately used let go of. A whiteboard never drawn on is not kept.

   THE WHITEBOARD AS WIDE AS THE SCREEN IS (the user, 2026-11-17: "这个size缩放是只改变panel高度，不改变宽度的"): it fills
   the stage; its drawing is kept in the board's own pixels, not as parts of its size - made taller, it has more room,
   and nothing drawn is stretched. A picture's drawing is kept as parts of the picture, which keeps its shape. */

/* AS WINDOWS' PAINT HAS IT (the user, 2026-11-17: "button太多了，需要有sidebar button, new button, clear button (清空当前的),
   然后是各种形状的基础款，画笔，橡皮擦，文字，旋转等（和windows自带画图差不多)"): the strip, New (a whiteboard, the screen, a
   picture of the conversation), Clear; Shapes (box, ellipse, line, arrow), the pen, the eraser, words, Rotate; the colour
   and the line's size. Ctrl+Z undoes. A tool pressed again is put down: the picture is pressed on as it is (an agent's
   chart says its values under the pointer). */
const SIDE_BOARD_COLOURS = ["#e5484d", "#f5a524", "#ffd60a", "#2fbf71", "#3e8ef7", "#9b5de5", "#111111", "#ffffff"];
const SIDE_BOARD_SIZES = [["Thin", 2], ["Medium", 4], ["Thick", 8]];
const SIDE_BOARD_SHAPES = [["box", "Rectangle"], ["ellipse", "Ellipse"], ["line", "Line"], ["arrow", "Arrow"]];
const SIDE_BOARD_ERASER = 9;            // how near the eraser takes a stroke, in the board's pixels
const SIDE_BOARD_UNDO_MOST = 50;
/* Each button a mark, said by its title - one line, however narrow the panel. */
const SIDE_BOARD_ICONS = {
  pages: '<rect x="1.5" y="2" width="4.5" height="3.5" rx=".6" fill="none" stroke="currentColor" stroke-width="1.2"/><rect x="1.5" y="7" width="4.5" height="3.5" rx=".6" fill="none" stroke="currentColor" stroke-width="1.2"/><rect x="1.5" y="12" width="4.5" height="2.5" rx=".6" fill="none" stroke="currentColor" stroke-width="1.2"/><rect x="8" y="2" width="6.5" height="12.5" rx="1" fill="none" stroke="currentColor" stroke-width="1.3"/>',
  new: '<path d="M3.5 1.5h6l3 3v10h-9z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M8 7v5M5.5 9.5h5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>',
  clear: '<path d="M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.7 9h5.6l.7-9" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>',
  shapes: '<rect x="1.5" y="7" width="7" height="7" rx="1" fill="none" stroke="currentColor" stroke-width="1.3"/><circle cx="10.5" cy="5.5" r="4" fill="none" stroke="currentColor" stroke-width="1.3"/>',
  pen: '<path d="M2.5 13.5l.8-3 7.6-7.6 2.2 2.2-7.6 7.6zM9.6 4.2l2.2 2.2" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>',
  eraser: '<path d="M6 13.5h7.5M2.8 9.8l5.9-5.9a1.4 1.4 0 0 1 2 0l2.2 2.2a1.4 1.4 0 0 1 0 2l-4.3 4.4H6.2z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M5.6 7l3.4 3.4" stroke="currentColor" stroke-width="1.3"/>',
  text: '<path d="M3 3.5h10M8 3.5V13.5" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>',
  rotate: '<path d="M13 8.5a5 5 0 1 1-1.5-4.2M13 2v3.3H9.7" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>',
  box: '<rect x="2.5" y="3.5" width="11" height="9" rx="1" fill="none" stroke="currentColor" stroke-width="1.5"/>',
  ellipse: '<ellipse cx="8" cy="8" rx="6" ry="4.5" fill="none" stroke="currentColor" stroke-width="1.5"/>',
  line: '<path d="M3 13L13 3" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>',
  arrow: '<path d="M3 13L13 3M7 3h6v6" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
  whiteboard: '<rect x="2" y="2.5" width="12" height="9.5" rx="1" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M5 14.5l1.5-2.5M11 14.5l-1.5-2.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>',
  snip: '<path d="M2 5V2.5h2.5M11.5 2.5H14V5M14 11v2.5h-2.5M4.5 13.5H2V11" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/><circle cx="8" cy="8" r="2.2" fill="none" stroke="currentColor" stroke-width="1.3"/>',
  screen: '<rect x="1.5" y="2.5" width="13" height="9" rx="1.2" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M5.5 14h5M8 11.5V14" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>',
  pictures: '<rect x="1.5" y="2.5" width="13" height="11" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M1.5 11l3.5-3.5 3 3 2-2 4.5 4.5" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/><circle cx="11" cy="5.6" r="1.2" fill="currentColor"/>',
};
function sideBoardIcon(key) {
  return `<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">${SIDE_BOARD_ICONS[key]}</svg>`;
}
const SIDE_BOARD_SHARE = 0.46;          // of the panel's height, at first
const SIDE_BOARD_WHITE_SENT = 2;         // a whiteboard sent at twice its size on the screen
const SIDE_BOARD_LEAST = 140;
const SIDE_BOARD_MOST_SIDE = 2400;      // a picture sent no larger than this across
const SIDE_BOARD_HEIGHT_KEY = "sideBoardHeight";
const SIDE_BOARD_STRIP_KEY = "sideBoardStrip";
const SIDE_BOARD_KEPT = 100;            // pages kept, at most
const SIDE_BOARD_THUMB = 160;           // a page's small picture, this wide
const SIDE_BOARD_DB = "side-board";
/* The page shown is `page`; its picture, drawing and whether it changed since the last message are its own. */
const sideBoard = {
  open: false, page: null, pages: [], tool: "pen", shape: "box", colour: 0, size: 4, drawing: null, height: 0,
  loaded: false,
  get base() { return this.page ? this.page.base : null; },
  get shapes() { return this.page ? this.page.shapes : []; },
  set shapes(value) { if (this.page) this.page.shapes = value; },
  get dirty() { return Boolean(this.page && this.page.dirty); },
  set dirty(value) {
    if (!this.page) return;
    this.page.dirty = Boolean(value);
    if (value) sideBoardChanged(this.page);
  },
};

/* Whether a page's drawing is in the board's own pixels (a whiteboard) rather than parts of its picture. */
function sideBoardInPixels(page) {
  return Boolean(page && page.base.kind === "blank");
}

function sideBoardZoom() {
  return parseFloat(getComputedStyle(document.documentElement).zoom) || 1;
}

/* Made once: its bar, its stage (the picture and the layer drawn on), the grip under it. */
function sideBoardEl() {
  let board = document.getElementById("side-board");
  if (board) return board;
  board = document.createElement("section");
  board.id = "side-board";
  board.className = "side-board";
  board.hidden = true;
  board.setAttribute("aria-label", "Small screen");
  const button = (what, label, extra = "") => `<button type="button" class="side-board-tool" data-board="${what}"
      title="${escapeHtml(label)}" aria-label="${escapeHtml(label)}"${extra}>${sideBoardIcon(what)}</button>`;
  const tool = (key, label, icon = key) => `<button type="button" class="side-board-tool" data-board-tool="${key}"
      aria-pressed="false" title="${escapeHtml(label)}" aria-label="${escapeHtml(label)}"${key === "shape"
        ? ' aria-haspopup="true"' : ""}>${sideBoardIcon(icon)}</button>`;
  board.innerHTML = `<div class="side-board-bar"><div class="side-board-tools">
      ${button("pages", "Keep recent images shown", ' aria-pressed="true"')}
      ${button("new", "New page")}
      ${button("snip", "Screenshot: the whole screen, or a part of it", ' aria-haspopup="true"')}
      ${button("clear", "Clear this picture's drawing")}
      <span class="side-board-line" aria-hidden="true"></span>
      ${tool("shape", "Shapes", "box")}
      ${tool("pen", "Pen")}
      ${tool("eraser", "Eraser")}
      ${tool("text", "Text")}
      ${button("rotate", "Rotate right")}
      <span class="side-board-line" aria-hidden="true"></span>
      <button type="button" class="side-board-colour" data-board="colour" aria-haspopup="true" title="${escapeHtml("Colour and size")}"
        aria-label="${escapeHtml("Colour and size")}"><i></i></button></div>
      <button type="button" class="side-board-close" data-board="close" aria-label="${escapeHtml("Close")}" title="${escapeHtml("Close")}">×</button>
    </div>
    <div class="side-board-pick" hidden></div>
    <div class="side-board-body"><div class="side-board-edge" title="${escapeHtml("Recent images")}" aria-hidden="true"></div>
    <div class="side-board-strip" role="list" aria-label="${escapeHtml("Recent images")}"></div>
    <div class="side-board-stage"><div class="side-board-frame"><div class="side-board-base"></div>
      <canvas class="side-board-ink"></canvas></div>
</div></div>
    <p class="side-board-note">${escapeHtml("Goes with your next message, with what you draw on it.")}</p>
    <div class="side-board-grip" title="${escapeHtml("Drag to make it taller or shorter")}" aria-hidden="true"></div>`;
  document.body.appendChild(board);
  board.addEventListener("click", sideBoardPressed);
  sideBoardKeys();
  sideBoardInk(board);
  sideBoardGrip(board);
  sideBoardDrop(board);
  new ResizeObserver(() => sideBoardFit()).observe(board.querySelector(".side-board-frame"));
  sideBoardToolNow(sideBoard.tool);
  sideBoardColourNow();
  let folded = false;
  try { folded = localStorage.getItem(SIDE_BOARD_STRIP_KEY) === "folded"; } catch (_err) { folded = false; }
  sideBoardStripShown(!folded, board);
  sideBoardPeeking(board);
  sideBoardLoad();
  return board;
}

/* WHERE IT BEGINS: under the panel's head - and under the search, while it is open (the user, 2026-11-17: "search应该是
   在画图板的下方" - it was hidden under it); followed as the search opens, closes, or its results grow. */
function sideBoardTop() {
  const below = [...document.querySelectorAll("#notes-page .notes-head, #side-search")]
    .filter((one) => !one.hidden && one.offsetParent !== null)
    .map((one) => one.getBoundingClientRect().bottom);
  return below.length ? Math.max(...below) : 0;
}
let sideBoardWatching = false;
function sideBoardWatchAbove() {
  if (sideBoardWatching || typeof ResizeObserver === "undefined") return;
  const search = document.getElementById("side-search");
  const head = document.querySelector("#notes-page .notes-head");
  if (!search && !head) return;
  sideBoardWatching = true;
  const again = () => { if (sideBoard.open) sideBoardPlace(); };
  const watch = new ResizeObserver(again);
  [search, head].filter(Boolean).forEach((one) => watch.observe(one));
  if (search) new MutationObserver(again).observe(search, { attributes: true, attributeFilter: ["hidden"] });
}

/* Under the panel's head (and its search), over the chat; as tall as the user left it. */
function sideBoardPlace() {
  const board = sideBoardEl();
  const zoom = sideBoardZoom();
  const top = sideBoardTop() / zoom;
  const room = window.innerHeight / zoom - top;
  if (!sideBoard.height) {
    let kept = 0;
    try { kept = Number(localStorage.getItem(SIDE_BOARD_HEIGHT_KEY)) || 0; } catch (_err) { kept = 0; }
    sideBoard.height = kept || Math.round(room * SIDE_BOARD_SHARE);
  }
  sideBoard.height = Math.max(SIDE_BOARD_LEAST, Math.min(Math.round(room - 120), sideBoard.height));
  board.style.top = `${Math.round(top)}px`;
  board.style.height = `${sideBoard.height}px`;
  if (!board.hidden) sideBoardFit();
}

function sideBoardRail(on) {
  document.querySelectorAll('.side-rail button[data-drawer="picture"]').forEach((one) =>
    one.setAttribute("aria-pressed", String(on)));
}

function sideBoardOpen() {
  const board = sideBoardEl();
  sideBoardWatchAbove();
  sideBoardPlace();
  board.hidden = false;
  sideBoard.open = true;
  sideBoardRail(true);
  // THE PAGE LAST USED, ELSE A WHITEBOARD (the user, 2026-11-17: "default应该是白板，这样我可以draw").
  if (!sideBoard.page) {
    if (sideBoard.pages.length) sideBoardShow(sideBoard.pages[0]);
    else sideBoardBase({ kind: "blank" });
  }
  sideBoardFit();
}

function sideBoardClose() {
  const board = document.getElementById("side-board");
  if (board) board.hidden = true;
  sideBoard.open = false;
  sideBoardRail(false);
  if (typeof sideTipHide === "function") sideTipHide();
}

function sideBoardToggle() {
  if (sideBoard.open) sideBoardClose(); else sideBoardOpen();
}

/* A NEW PAGE: a picture put in it (`blob` its file, kept with it), a drawing of its own - shown, first in the strip. */
function sideBoardBase(base, blob = null) {
  sideBoardEl();
  const now = Date.now();
  const page = { id: `p${now.toString(36)}${Math.random().toString(36).slice(2, 7)}`, base, shapes: [], blob,
                 created: now, used: now, thumb: "", size: null,
                 dirty: base.kind !== "blank" };                  // a clean whiteboard says nothing till drawn on
  sideBoard.pages.unshift(page);
  sideBoardShow(page);
  sideBoardLetGo();
  if (page.dirty) sideBoardChanged(page);
  return page;
}

/* A page shown: its picture under its drawing. A whiteboard left untouched is let go of on the way. */
function sideBoardShow(page) {
  const board = sideBoardEl();
  const was = sideBoard.page;
  if (was && was !== page && was.base.kind === "blank" && !was.shapes.length) sideBoardLetGoOf(was);
  sideBoard.page = page;
  sideBoard.drawing = null;
  page.used = Date.now();
  const base = page.base;
  const where = board.querySelector(".side-board-base");
  where.replaceChildren();
  board.classList.add("has-base");
  board.classList.toggle("is-view", base.kind === "view");
  board.classList.toggle("is-blank", base.kind === "blank");
  if (base.kind === "blank") {
    where.appendChild(Object.assign(document.createElement("div"), { className: "side-board-white" }));
  } else if (base.kind === "view") {
    if (typeof sideDraw !== "function" || !sideDraw(where, base.view)) {
      page.base = { kind: "blank" };
      page.shapes = [];
      sideBoardShow(page);
      return;
    }
  } else {
    const picture = document.createElement("img");
    picture.alt = "";
    picture.draggable = false;
    picture.onload = () => sideBoardFit();
    picture.src = base.url;
    where.appendChild(picture);
  }
  sideBoardFit();
  sideBoardStripDraw();
}

/* A page chosen in the strip: shown, and it goes with the next message - as it is, with its drawing. */
function sideBoardSelect(id) {
  const page = sideBoard.pages.find((one) => one.id === id);
  if (!page || page === sideBoard.page) return;
  sideBoardShow(page);
  if (page.base.kind !== "blank" || page.shapes.length) page.dirty = true;
  sideBoardSave(page);
}

/* A page let go of by its ×: out of the strip and the store; the one shown, the next shown instead (or a whiteboard). */
function sideBoardRemove(id) {
  const page = sideBoard.pages.find((one) => one.id === id);
  if (!page) return;
  const shown = page === sideBoard.page;
  sideBoardLetGoOf(page);
  if (shown) {
    sideBoard.page = null;
    if (sideBoard.pages.length) sideBoardShow(sideBoard.pages[0]); else sideBoardBase({ kind: "blank" });
  }
  sideBoardStripDraw();
}

function sideBoardLetGoOf(page) {
  sideBoard.pages = sideBoard.pages.filter((one) => one !== page);
  clearTimeout(sideBoardLater.get(page.id));
  sideBoardLater.delete(page.id);
  if (page.blob && String(page.base.url || "").startsWith("blob:")) URL.revokeObjectURL(page.base.url);
  sideBoardForget(page.id);
}

/* No more than SIDE_BOARD_KEPT: the least lately used let go of (never the one shown). */
function sideBoardLetGo() {
  if (sideBoard.pages.length <= SIDE_BOARD_KEPT) return;
  const oldest = sideBoard.pages.filter((one) => one !== sideBoard.page).sort((a, b) => a.used - b.used);
  oldest.slice(0, sideBoard.pages.length - SIDE_BOARD_KEPT).forEach(sideBoardLetGoOf);
  sideBoardStripDraw();
}

/* A page changed: its small picture made again and it is kept - a little after, not at every stroke. */
const sideBoardLater = new Map();
function sideBoardChanged(page) {
  page.used = Date.now();
  if (sideBoardInPixels(page)) {                     // the whiteboard's size as it was drawn on: its small picture's
    const frame = document.querySelector("#side-board .side-board-frame");
    if (frame && frame.clientWidth) page.size = [frame.clientWidth, frame.clientHeight];
  }
  clearTimeout(sideBoardLater.get(page.id));
  sideBoardLater.set(page.id, setTimeout(async () => {
    sideBoardLater.delete(page.id);
    if (!sideBoard.pages.includes(page)) return;     // let go of meanwhile: not kept again
    page.thumb = await sideBoardThumb(page);
    if (!sideBoard.pages.includes(page)) return;
    sideBoardStripDraw();
    sideBoardSave(page);
  }, 400));
}

/* A page's small picture: its picture and drawing, SIDE_BOARD_THUMB wide ("" for the agent's drawing: named instead). */
async function sideBoardThumb(page) {
  if (page.base.kind === "view") return "";
  try {
    let w = SIDE_BOARD_THUMB, h = Math.round(SIDE_BOARD_THUMB * 0.625);
    let picture = null, px = 0;
    if (page.base.kind === "blank") {
      const size = page.size || [440, 275];
      h = Math.max(1, Math.round(w * size[1] / Math.max(1, size[0])));
      if (h > w) { w = Math.max(1, Math.round(w * w / h)); h = SIDE_BOARD_THUMB; }
      px = w / Math.max(1, size[0]);
    } else {
      picture = await new Promise((done, fail) => {
        const one = new Image();
        one.onload = () => done(one);
        one.onerror = fail;
        one.src = page.base.url;
      });
      h = Math.max(1, Math.round(w * picture.naturalHeight / Math.max(1, picture.naturalWidth)));
      if (h > w) { w = Math.max(1, Math.round(w * w / h)); h = SIDE_BOARD_THUMB; }
    }
    const canvas = Object.assign(document.createElement("canvas"), { width: w, height: h });
    const context = canvas.getContext("2d");
    context.fillStyle = "#ffffff";
    context.fillRect(0, 0, w, h);
    if (picture) context.drawImage(picture, 0, 0, w, h);
    sideBoardPaint(context, w, h, px || w / 440, page.shapes, px);
    return canvas.toDataURL("image/jpeg", 0.8);
  } catch (_err) {
    return "";
  }
}

/* THE STRIP OF PAGES, AS A DECK'S: the latest used first, the one shown marked; each chosen by a press, let go of by
   its ×. Folded away by the bar's first button. */
function sideBoardStripDraw() {
  const strip = document.querySelector("#side-board .side-board-strip");
  if (!strip) return;
  strip.replaceChildren(...sideBoard.pages.map((page) => {
    const one = document.createElement("div");
    one.className = "side-board-page";
    one.setAttribute("role", "listitem");
    const label = page.name || (page.base.kind === "view" ? String(page.base.view.title || "The agent's picture")
      : page.base.kind === "blank" ? "Whiteboard" : page.base.screen ? "Screen" : page.base.cut ? "Part of the screen" : "Picture");
    const face = page.thumb ? `<img src="${page.thumb}" alt="">`
      : `<span class="side-board-page-name">${escapeHtml(label)}</span>`;
    one.innerHTML = `<button type="button" class="side-board-page-pick" data-page="${page.id}"
        aria-current="${page === sideBoard.page}" title="${escapeHtml(label)}">${face}${page.base.agent
          ? `<span class="side-board-page-agent">${escapeHtml("Agent")}</span>` : ""}</button>
      ${page.name ? `<span class="side-board-page-label chat-message">${escapeHtml(page.name)}</span>` : ""}
      <button type="button" class="side-board-page-off" data-page-off="${page.id}" title="${escapeHtml("Remove this image")}"
        aria-label="${escapeHtml("Remove this image")}">×</button>`;
    return one;
  }));
  strip.querySelector('[aria-current="true"]')?.scrollIntoView({ block: "nearest" });
}

/* PINNED, OR SHOWN ONLY WHILE THE POINTER IS ON IT (the user, 2026-11-17: "可以固定也可以只在鼠标放在上面才展开，鼠标移开就
   拿走"): not pinned, a thin edge is left on the left; the pointer on it, the strip comes out over the picture, and goes
   once the pointer has left both a moment. */
const SIDE_BOARD_PEEK_GONE_MS = 300;
function sideBoardPeeking(board) {
  const edge = board.querySelector(".side-board-edge");
  const strip = board.querySelector(".side-board-strip");
  let leaving = 0;
  const show = () => {
    clearTimeout(leaving);
    if (board.classList.contains("strip-hover")) board.classList.add("strip-peek");
  };
  const hide = () => {
    clearTimeout(leaving);
    leaving = setTimeout(() => board.classList.remove("strip-peek"), SIDE_BOARD_PEEK_GONE_MS);
  };
  [edge, strip].forEach((one) => {
    one.addEventListener("pointerenter", show);
    one.addEventListener("pointerleave", hide);
  });
}

function sideBoardStripShown(on, board = sideBoardEl()) {
  board.classList.toggle("strip-hover", !on);
  board.classList.remove("strip-peek");
  board.querySelector('[data-board="pages"]')?.setAttribute("aria-pressed", String(on));
  try { localStorage.setItem(SIDE_BOARD_STRIP_KEY, on ? "shown" : "folded"); } catch (_err) { /* this time only */ }
  sideBoardFit();
}

/* -- KEPT BETWEEN STARTS: this panel's own store. Anything amiss there, the pages are kept for this time only. -- */
let sideBoardStore = null;
function sideBoardDb() {
  if (!sideBoardStore) {
    sideBoardStore = new Promise((done, fail) => {
      if (typeof indexedDB === "undefined") { fail(new Error("no store")); return; }
      const asked = indexedDB.open(SIDE_BOARD_DB, 1);
      asked.onupgradeneeded = () => asked.result.createObjectStore("pages", { keyPath: "id" });
      asked.onsuccess = () => done(asked.result);
      asked.onerror = () => fail(asked.error);
    });
  }
  return sideBoardStore;
}
async function sideBoardSave(page) {
  if (page.base.kind === "blank" && !page.shapes.length) return;      // a whiteboard never drawn on: not kept
  try {
    const db = await sideBoardDb();
    const base = { ...page.base, url: page.blob ? "" : page.base.url };
    const writing = db.transaction("pages", "readwrite");
    writing.objectStore("pages").put({ id: page.id, base, shapes: page.shapes, blob: page.blob || null,
      created: page.created, used: page.used, thumb: page.thumb, size: page.size, name: page.name || "",
      madeBy: page.madeBy || "", from: page.from || "" });
    await new Promise((done) => { writing.oncomplete = done; writing.onerror = done; writing.onabort = done; });
  } catch (_err) { /* kept for this time only */ }
}
async function sideBoardForget(id) {
  try {
    const db = await sideBoardDb();
    db.transaction("pages", "readwrite").objectStore("pages").delete(id);
  } catch (_err) { /* nothing kept */ }
}
async function sideBoardLoad() {
  if (sideBoard.loaded) return;
  sideBoard.loaded = true;
  let kept = [];
  try {
    const db = await sideBoardDb();
    kept = await new Promise((done, fail) => {
      const asked = db.transaction("pages").objectStore("pages").getAll();
      asked.onsuccess = () => done(asked.result || []);
      asked.onerror = () => fail(asked.error);
    });
  } catch (_err) {
    kept = [];
  }
  const here = new Set(sideBoard.pages.map((one) => one.id));
  const pages = kept.filter((one) => one && one.base && !here.has(one.id)).map((one) => ({
    ...one, dirty: false, shapes: one.shapes || [],
    base: { ...one.base, url: one.blob ? URL.createObjectURL(one.blob) : one.base.url } }));
  pages.sort((a, b) => b.used - a.used);
  // Opened before they were read, on a whiteboard not drawn on yet: the page last used instead.
  const fresh = sideBoard.page && sideBoard.page.base.kind === "blank" && !sideBoard.page.shapes.length;
  sideBoard.pages = [...sideBoard.pages, ...pages];
  if (fresh && pages.length && sideBoard.open) sideBoardShow(pages[0]);
  sideBoardLetGo();
  sideBoardStripDraw();
}

/* THE SCREEN AS IT IS NOW (api/routes.py /api/side/screen_now) - the panel left out of it. */
async function sideBoardScreen() {
  sideBoardOpen();
  const board = sideBoardEl();
  const button = board.querySelector('[data-board="screen"]');
  if (button) button.disabled = true;
  try {
    const answer = await fetch("/api/side/screen_now", { headers: { "X-Time-Zone": PAGE_ZONE } });
    if (!answer.ok) {
      let said = "";
      try { said = (await answer.json()).error || ""; } catch (_err) { said = ""; }
      throw new Error(said || "The screen could not be taken.");
    }
    const blob = await answer.blob();
    sideBoardBase({ kind: "image", url: URL.createObjectURL(blob), screen: true, own: true }, blob);
  } catch (err) {
    toast(err.message, "error");
  } finally {
    if (button) button.disabled = false;
  }
}

/* THE AGENT'S DRAWING beside an answer, here (side-visual.js sideDraw) - a page of its own, the small screen opened.
   NEVER CHANGED IN PLACE (the user, 2026-11-17: "模型改user的图，要求只能是复制user的图再改，所以不直接edit"; "agent对自己的
   图也是复制了再改"): marks it puts on a picture here (kind "marks") go on a copy of it - a new page, first, under the
   name it gives; a drawing of its own drawn again is a new page too, the earlier one kept. It names pictures, the
   user's as well ("name", or "names": {page: name}) - a name only, the picture untouched. */
function sideBoardShowView(view) {
  sideBoardNamed(view);
  if (view.kind === "name") return;                  // naming alone: nothing opened, nothing drawn
  sideBoardOpen();
  if (view.kind === "marks") { sideBoardAgentCopy(view); return; }
  // The same drawing shown again (its mark in the chat pressed): its own page, with what was drawn on it.
  const same = JSON.stringify(view);
  const found = sideBoard.pages.find((one) => one.base.kind === "view" && JSON.stringify(one.base.view) === same);
  if (found) sideBoardShow(found);
  else {
    const page = sideBoardBase({ kind: "view", view, agent: true });
    const name = String(view.name || view.id || "").trim();
    if (name) { page.name = name.slice(0, 60); sideBoardStripDraw(); }
  }
  sideBoardFitTaller();
}

/* A page by what the agent calls it: its small-screen id, else its name (the one most lately used of that name). */
function sideBoardFind(ref) {
  const said = String(ref || "").trim();
  if (!said) return null;
  const byId = sideBoard.pages.find((one) => one.id === said);
  if (byId) return byId;
  const low = said.toLowerCase();
  return [...sideBoard.pages].sort((a, b) => b.used - a.used)
    .find((one) => String(one.name || "").toLowerCase() === low
      || (one.base.kind === "view" && String(one.base.view.id || one.base.view.name || "").toLowerCase() === low)) || null;
}

/* Names the agent gives - its block's "names" ({page: name}) and, for kind "name", its "page" and "name". */
function sideBoardNamed(view) {
  const names = { ...((view && typeof view.names === "object" && view.names) || {}) };
  if (view.kind === "name" && view.page && view.name) names[view.page] = view.name;
  let any = false;
  Object.entries(names).forEach(([ref, name]) => {
    const page = sideBoardFind(ref);
    const given = String(name || "").trim().slice(0, 60);
    if (page && given && page.name !== given) {
      page.name = given;
      sideBoardSave(page);
      any = true;
    }
  });
  if (any) sideBoardStripDraw();
}

/* The agent's own colour, apart from the user's four. */
const SIDE_BOARD_AGENT_COLOUR = "#9b5de5";
const SIDE_BOARD_AGENT_MOST = 30;

/* MARKS THE AGENT PUTS ON A PICTURE HERE - the user's whiteboard or screenshot, or a drawing of its own - by its
   small-screen id (said with the picture in the user's message) or its name: on A COPY of it, a new page first in the
   strip and shown, named as the agent says - the picture itself untouched. A box, an arrow, words, a line, each place
   a part of the picture's width and height, in the agent's own colour. Made once: its mark in the chat pressed again
   shows the same copy. */
function sideBoardAgentCopy(view) {
  const key = JSON.stringify(view);
  const made = sideBoard.pages.find((one) => one.madeBy === key);
  if (made) { sideBoardShow(made); return; }
  const source = sideBoardFind(view.page);
  if (!source) { toast("The picture the agent drew on is no longer in the small screen.", "error"); return; }
  const size = sideBoardInPixels(source) ? (source.size || sideBoardSizeNow() || [440, 275]) : [1, 1];
  const at = (one) => (Array.isArray(one) && one.length >= 2 && one.every((n) => Number.isFinite(Number(n)))
    ? [Math.min(1, Math.max(0, Number(one[0]))) * size[0], Math.min(1, Math.max(0, Number(one[1]))) * size[1]] : null);
  const added = [];
  (Array.isArray(view.marks) ? view.marks : []).slice(0, SIDE_BOARD_AGENT_MOST).forEach((mark) => {
    const shape = { colour: SIDE_BOARD_AGENT_COLOUR, agent: true };
    if (mark.shape === "box" && Array.isArray(mark.at) && mark.at.length === 4) {
      const a = at(mark.at.slice(0, 2)), b = at(mark.at.slice(2));
      if (a && b) added.push({ ...shape, kind: "box", points: [a, b] });
    } else if (mark.shape === "ellipse" && Array.isArray(mark.at) && mark.at.length === 4) {
      const a = at(mark.at.slice(0, 2)), b = at(mark.at.slice(2));
      if (a && b) added.push({ ...shape, kind: "ellipse", points: [a, b] });
    } else if (mark.shape === "arrow" || mark.shape === "line") {
      const a = at(mark.from), b = at(mark.to);
      if (a && b) added.push({ ...shape, kind: mark.shape, points: [a, b] });
    } else if (mark.shape === "text" && mark.text) {
      const a = at(mark.at);
      if (a) added.push({ ...shape, kind: "text", points: [a], text: String(mark.text).slice(0, 80) });
    } else if ((mark.shape === "pen" || mark.shape === "line") && Array.isArray(mark.points)) {
      const points = mark.points.slice(0, 200).map(at).filter(Boolean);
      if (points.length > 1) added.push({ ...shape, kind: "pen", points });
    }
  });
  // The copy: the same picture (its file shared, never changed), what was on it, and the agent's marks over that.
  const base = { ...source.base, agent: true };
  if (source.blob) base.url = URL.createObjectURL(source.blob);
  const copy = sideBoardBase(base, source.blob || null);
  copy.shapes = [...JSON.parse(JSON.stringify(source.shapes)), ...added];
  copy.size = source.size;
  copy.madeBy = key;
  copy.from = source.id;
  copy.name = String(view.name || "").trim().slice(0, 60) || (source.name ? `${source.name} (marked)` : "");
  copy.dirty = false;                                  // the agent's: not sent back to it unasked
  sideBoardChanged(copy);
  sideBoardShow(copy);
}

/* The whiteboard's size on the screen now, when one is shown. */
function sideBoardSizeNow() {
  const frame = document.querySelector("#side-board .side-board-frame");
  return frame && frame.clientWidth ? [frame.clientWidth, frame.clientHeight] : null;
}

function sideBoardFitTaller() {
  // Taller for the agent's drawing, this once, when it would not fit - no more than seven tenths of the panel.
  if (!sideBoard.base || sideBoard.base.kind !== "view") return;
  const board = sideBoardEl();
  const stage = board.querySelector(".side-board-stage");
  const frame = board.querySelector(".side-board-frame");
  const short = frame.scrollHeight + 12 - stage.clientHeight;
  if (short > 0) {
    const zoom = sideBoardZoom();
    const room = window.innerHeight / zoom - sideBoardTop() / zoom;
    sideBoard.height = Math.min(Math.round(room * 0.7), sideBoard.height + short);
    sideBoardPlace();
  }
}

/* One of the conversation's pictures, here. */
function sideBoardShowImage(url) {
  sideBoardOpen();
  sideBoardBase({ kind: "image", url, screen: false });
}

/* The conversation's pictures to choose from - those sent with messages and the screens that went with them. */
function sideBoardPictures(button) {
  const board = sideBoardEl();
  const pick = board.querySelector(".side-board-pick");
  if (!pick.hidden) { pick.hidden = true; return; }
  const log = document.getElementById("side-chat-log");
  const found = log ? [...new Set([...log.parentElement.querySelectorAll(".side-msg-files img, .side-shot img")]
    .map((one) => one.getAttribute("src")).filter(Boolean))].reverse() : [];
  pick.innerHTML = found.length ? "" : `<p class="side-board-none">${escapeHtml("No pictures in this conversation yet.")}</p>`;
  found.forEach((url) => {
    const one = document.createElement("button");
    one.type = "button";
    one.className = "side-board-thumb";
    one.innerHTML = `<img src="${escapeHtml(url)}" alt="">`;
    one.addEventListener("click", () => {
      pick.hidden = true;
      sideBoardShowImage(url);
    });
    pick.appendChild(one);
  });
  pick.hidden = false;
}

function sideBoardToolNow(tool) {
  sideBoard.tool = tool;
  const board = document.getElementById("side-board");
  if (!board) return;
  board.querySelectorAll("[data-board-tool]").forEach((one) =>
    one.setAttribute("aria-pressed", String(one.dataset.boardTool === tool)));
  board.classList.toggle("drawing", Boolean(tool));
  board.classList.toggle("erasing", tool === "eraser");
  const shape = board.querySelector('[data-board-tool="shape"]');
  if (shape) shape.innerHTML = sideBoardIcon(sideBoard.shape);    // the shape chosen shown on its button
}

function sideBoardColourNow() {
  const dot = document.querySelector("#side-board .side-board-colour i");
  if (!dot) return;
  dot.style.background = SIDE_BOARD_COLOURS[sideBoard.colour];
  dot.style.setProperty("--size", `${Math.max(6, Math.min(14, sideBoard.size + 6))}px`);
}

/* A menu under its button: one at a time; a press elsewhere, or Esc, puts it away. */
function sideBoardMenu(button, html) {
  const board = sideBoardEl();
  const open = board.querySelector(".side-board-menu");
  const same = open && open.dataset.for === button.dataset.board + (button.dataset.boardTool || "");
  sideBoardMenuAway();
  if (same) return;
  const menu = document.createElement("div");
  menu.className = "side-board-menu";
  menu.setAttribute("role", "menu");
  menu.dataset.for = button.dataset.board + (button.dataset.boardTool || "");
  menu.innerHTML = html;
  board.appendChild(menu);
  const zoom = sideBoardZoom();
  const at = button.getBoundingClientRect(), whole = board.getBoundingClientRect();
  menu.style.left = `${Math.max(4, Math.min((at.left - whole.left) / zoom, whole.width / zoom - menu.offsetWidth - 4))}px`;
  menu.style.top = `${(at.bottom - whole.top) / zoom + 4}px`;
  button.setAttribute("aria-expanded", "true");
}
function sideBoardMenuAway() {
  const board = document.getElementById("side-board");
  board?.querySelector(".side-board-menu")?.remove();
  board?.querySelectorAll('[aria-expanded="true"][aria-haspopup]').forEach((one) => one.setAttribute("aria-expanded", "false"));
}
function sideBoardMenuItem(data, icon, label, pressed = false) {
  return `<button type="button" role="menuitem" class="side-board-item${pressed ? " on" : ""}" ${data}>${icon ? sideBoardIcon(icon) : ""}
    <span>${escapeHtml(label)}</span></button>`;
}

function sideBoardPressed(event) {
  const pick = (selector) => event.target.closest && event.target.closest(selector);
  const off = pick("[data-page-off]");
  if (off) { sideBoardRemove(off.dataset.pageOff); return; }
  const page = pick("[data-page]");
  if (page) { sideBoardSelect(page.dataset.page); return; }
  // In a menu.
  const made = pick("[data-board-new]");
  if (made) {
    sideBoardMenuAway();
    const what = made.dataset.boardNew;
    if (what === "blank") { sideBoardOpen(); sideBoardBase({ kind: "blank" }); }
    else if (what === "screen") sideBoardScreen();
    else sideBoardPictures(sideBoardEl().querySelector('[data-board="new"]'));
    return;
  }
  const cut = pick("[data-board-snip]");
  if (cut) {
    sideBoardMenuAway();
    if (cut.dataset.boardSnip === "whole") sideBoardScreen();
    else sideBoardSnip();
    return;
  }
  const shape = pick("[data-board-shape]");
  if (shape) { sideBoard.shape = shape.dataset.boardShape; sideBoardMenuAway(); sideBoardToolNow("shape"); return; }
  const colour = pick("[data-board-colour]");
  if (colour) { sideBoard.colour = Number(colour.dataset.boardColour); sideBoardMenuAway(); sideBoardColourNow(); return; }
  const size = pick("[data-board-size]");
  if (size) { sideBoard.size = Number(size.dataset.boardSize); sideBoardMenuAway(); sideBoardColourNow(); return; }
  // On the bar.
  const tool = pick("[data-board-tool]");
  if (tool) {
    const key = tool.dataset.boardTool;
    if (key === "shape") {
      sideBoardToolNow("shape");
      sideBoardMenu(tool, SIDE_BOARD_SHAPES.map(([one, label]) => sideBoardMenuItem(`data-board-shape="${one}"`, one, label,
        one === sideBoard.shape)).join(""));
      return;
    }
    sideBoardMenuAway();
    sideBoardToolNow(sideBoard.tool === key ? "" : key);              // pressed again: put down
    return;
  }
  const button = pick("[data-board]");
  if (!button) { if (!pick(".side-board-menu")) sideBoardMenuAway(); return; }
  const what = button.dataset.board;
  if (what === "new") {
    // NEW IS A NEW PAGE, NOTHING ELSE (the user, 2026-11-18: "你的new就是单独创建一个新的，然后screenshot有两种"): a blank page
    // at once; the screen is the screenshot's (the whole of it, or a part).
    sideBoardMenuAway();
    sideBoardOpen();
    sideBoardBase({ kind: "blank" });
    return;
  }
  if (what === "colour") {
    sideBoardMenu(button, `<div class="side-board-swatches">${SIDE_BOARD_COLOURS.map((one, n) => `<button type="button"
        class="side-board-swatch${n === sideBoard.colour ? " on" : ""}" data-board-colour="${n}" style="background:${one}"
        aria-label="${escapeHtml(one)}"></button>`).join("")}</div>`
      + SIDE_BOARD_SIZES.map(([label, px]) => sideBoardMenuItem(`data-board-size="${px}"`, "", label, px === sideBoard.size)
        .replace("<span>", `<i class="side-board-size" style="height:${px}px"></i><span>`)).join(""));
    return;
  }
  sideBoardMenuAway();
  if (what === "pages") sideBoardStripShown(button.getAttribute("aria-pressed") !== "true");
  else if (what === "clear") sideBoardClear();
  else if (what === "snip") {
    // TWO WAYS (the user, 2026-11-18: "screen shot有两个选项，一个是截取整个屏幕，一个是截取一个部分的").
    sideBoardMenu(button, sideBoardMenuItem('data-board-snip="whole"', "screen", "The whole screen")
      + sideBoardMenuItem('data-board-snip="part"', "snip", "Part of the screen"));
    return;
  }
  else if (what === "rotate") sideBoardRotate();
  else if (what === "close") sideBoardClose();
}

/* -- UNDONE BY CTRL+Z: each change to a page's drawing kept before it is made (not between starts). -- */
function sideBoardRemember(page = sideBoard.page) {
  if (!page) return;
  page.undo = page.undo || [];
  page.undo.push(JSON.stringify({ shapes: page.shapes, base: page.base.kind === "image" ? null : page.base }));
  if (page.undo.length > SIDE_BOARD_UNDO_MOST) page.undo.shift();
}
function sideBoardUndo() {
  const page = sideBoard.page;
  if (!page || !(page.undo || []).length) return false;
  const was = JSON.parse(page.undo.pop());
  if (was.rotated) { toast("A turned picture is turned back by turning it again.", "error"); return false; }
  page.shapes = was.shapes;
  sideBoard.dirty = true;
  sideBoardPaintNow();
  return true;
}
function sideBoardKeys() {
  document.addEventListener("keydown", (event) => {
    if (!sideBoard.open || !(event.ctrlKey || event.metaKey) || event.altKey || String(event.key).toLowerCase() !== "z") return;
    const typing = document.activeElement && /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
    if (typing || document.activeElement?.isContentEditable) return;      // the box's own undo
    if (sideBoardUndo()) event.preventDefault();
  });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") sideBoardMenuAway(); });
  document.addEventListener("pointerdown", (event) => {
    if (!(event.target.closest && event.target.closest(".side-board-menu, [aria-haspopup]"))) sideBoardMenuAway();
  }, true);
}

/* CLEAR: this picture's drawing gone (the picture itself kept) - undone by Ctrl+Z. */
function sideBoardClear() {
  if (!sideBoard.shapes.length) return;
  sideBoardRemember();
  sideBoard.shapes = [];
  sideBoard.dirty = true;
  sideBoardPaintNow();
}

/* ROTATE RIGHT, a quarter turn: a picture turned (made anew, its drawing with it); a whiteboard's drawing turned about
   its middle. Not the agent's chart, which it lays out itself. */
async function sideBoardRotate() {
  const page = sideBoard.page;
  if (!page) return;
  if (page.base.kind === "view") { toast("The agent's chart cannot be turned - ask it for another.", "error"); return; }
  if (page.base.kind === "blank") {
    const [w, h] = sideBoardSizeNow() || page.size || [440, 275];
    const cx = w / 2, cy = h / 2;
    sideBoardRemember(page);
    page.shapes.forEach((shape) => { shape.points = shape.points.map(([x, y]) => [cx - (y - cy), cy + (x - cx)]); });
    sideBoard.dirty = true;
    sideBoardPaintNow();
    return;
  }
  try {
    const picture = await new Promise((done, fail) => {
      const one = new Image();
      one.onload = () => done(one);
      one.onerror = fail;
      one.src = page.base.url;
    });
    const canvas = Object.assign(document.createElement("canvas"), { width: picture.naturalHeight, height: picture.naturalWidth });
    const context = canvas.getContext("2d");
    context.translate(canvas.width, 0);
    context.rotate(Math.PI / 2);
    context.drawImage(picture, 0, 0);
    const blob = await new Promise((done) => canvas.toBlob(done, "image/png"));
    if (!blob) throw new Error("not turned");
    page.undo = page.undo || [];
    page.undo.push(JSON.stringify({ rotated: true }));
    if (page.blob && String(page.base.url).startsWith("blob:")) URL.revokeObjectURL(page.base.url);
    page.blob = blob;
    page.base = { ...page.base, url: URL.createObjectURL(blob) };
    page.shapes.forEach((shape) => { shape.points = shape.points.map(([x, y]) => [1 - y, x]); });
    sideBoardShow(page);
    sideBoard.dirty = true;
  } catch (_err) {
    toast("This picture could not be turned.", "error");
  }
}

/* -- THE ERASER: a stroke it passes over is taken away whole (as the agent's marks are), undone by Ctrl+Z. -- */
function sideBoardNear(point, shape, toPixels, reach) {
  const p = toPixels(point);
  const segment = (a, b) => {
    const dx = b[0] - a[0], dy = b[1] - a[1];
    const along = dx || dy ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy))) : 0;
    return Math.hypot(p[0] - (a[0] + along * dx), p[1] - (a[1] + along * dy)) <= reach + (shape.size || 3) / 2;
  };
  const pts = shape.points.map(toPixels);
  if (shape.kind === "pen") return pts.length === 1 ? segment(pts[0], pts[0]) : pts.slice(1).some((b, i) => segment(pts[i], b));
  if (shape.kind === "line" || shape.kind === "arrow") return segment(pts[0], pts[1]);
  const [x0, x1] = [Math.min(pts[0][0], pts[1] ? pts[1][0] : pts[0][0]), Math.max(pts[0][0], pts[1] ? pts[1][0] : pts[0][0])];
  const [y0, y1] = [Math.min(pts[0][1], pts[1] ? pts[1][1] : pts[0][1]), Math.max(pts[0][1], pts[1] ? pts[1][1] : pts[0][1])];
  if (shape.kind === "box") {
    return [[[x0, y0], [x1, y0]], [[x1, y0], [x1, y1]], [[x1, y1], [x0, y1]], [[x0, y1], [x0, y0]]].some(([a, b]) => segment(a, b));
  }
  if (shape.kind === "ellipse") {
    const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2, rx = (x1 - x0) / 2, ry = (y1 - y0) / 2;
    const ring = Array.from({ length: 37 }, (_one, i) => [cx + rx * Math.cos(i * Math.PI / 18), cy + ry * Math.sin(i * Math.PI / 18)]);
    return ring.slice(1).some((b, i) => segment(ring[i], b));
  }
  if (shape.kind === "text") {
    const context = document.createElement("canvas").getContext("2d");
    context.font = `600 16px system-ui, -apple-system, "Segoe UI", sans-serif`;
    const wide = context.measureText(shape.text || "").width;
    return p[0] >= pts[0][0] - reach && p[0] <= pts[0][0] + wide + reach && p[1] >= pts[0][1] - reach && p[1] <= pts[0][1] + 20 + reach;
  }
  return false;
}
function sideBoardErase(point) {
  const frame = document.querySelector("#side-board .side-board-frame");
  if (!frame) return false;
  const pixels = sideBoardInPixels(sideBoard.page);
  const toPixels = (one) => (pixels ? one : [one[0] * frame.clientWidth, one[1] * frame.clientHeight]);
  const kept = sideBoard.shapes.filter((shape) => !sideBoardNear(point, shape, toPixels, SIDE_BOARD_ERASER));
  if (kept.length === sideBoard.shapes.length) return false;
  sideBoard.shapes = kept;
  sideBoardPaintNow();
  return true;
}

/* The layer drawn on, as large as the picture under it. */
function sideBoardFit() {
  const board = document.getElementById("side-board");
  if (!board || board.hidden) return;
  const frame = board.querySelector(".side-board-frame");
  const ink = board.querySelector(".side-board-ink");
  // A picture as tall as the stage leaves room for, never taller (its width follows).
  const stage = board.querySelector(".side-board-stage");
  board.style.setProperty("--side-board-room", `${Math.max(40, stage.clientHeight - 8)}px`);
  const white = board.querySelector(".side-board-white");
  if (white) {                                      // as wide and tall as the stage: taller, more room
    const style = getComputedStyle(stage);
    const across = stage.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
    const down = stage.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom);
    white.style.width = `${Math.max(60, Math.floor(across))}px`;
    white.style.height = `${Math.max(40, Math.floor(down))}px`;
  }
  const ratio = window.devicePixelRatio || 1;
  const width = Math.max(1, Math.round(frame.clientWidth * ratio));
  const height = Math.max(1, Math.round(frame.clientHeight * ratio));
  if (ink.width !== width || ink.height !== height) { ink.width = width; ink.height = height; }
  sideBoardPaintNow();
}

function sideBoardPaintNow() {
  const ink = document.querySelector("#side-board .side-board-ink");
  if (!ink) return;
  const context = ink.getContext("2d");
  context.clearRect(0, 0, ink.width, ink.height);
  const frame = ink.parentElement;
  const k = ink.width / Math.max(1, frame.clientWidth);
  sideBoardPaint(context, ink.width, ink.height, k,
    sideBoard.drawing ? [...sideBoard.shapes, sideBoard.drawing] : sideBoard.shapes,
    sideBoardInPixels(sideBoard.page) ? k : 0);
}

/* Every shape drawn into `context`, `w` by `h` - its places kept as parts of the picture's width and height, so the
   same drawing fits any size; `k` how many of these pixels a line's pixel is. `px`: the places are the board's own
   pixels instead (a whiteboard), this many of these to each. */
function sideBoardPaint(context, w, h, k, shapes, px = 0) {
  context.lineCap = "round";
  context.lineJoin = "round";
  shapes.forEach((shape) => {
    context.strokeStyle = shape.colour;
    context.fillStyle = shape.colour;
    context.lineWidth = (shape.size || 3) * k;
    const at = (point) => (px ? [point[0] * px, point[1] * px] : [point[0] * w, point[1] * h]);
    if (shape.kind === "pen" && shape.points.length) {
      context.beginPath();
      const [x0, y0] = at(shape.points[0]);
      context.moveTo(x0, y0);
      shape.points.slice(1).forEach((point) => context.lineTo(...at(point)));
      if (shape.points.length === 1) context.lineTo(x0 + 0.1, y0);
      context.stroke();
    } else if (shape.kind === "box" && shape.points.length === 2) {
      const [x0, y0] = at(shape.points[0]);
      const [x1, y1] = at(shape.points[1]);
      context.strokeRect(Math.min(x0, x1), Math.min(y0, y1), Math.abs(x1 - x0), Math.abs(y1 - y0));
    } else if (shape.kind === "ellipse" && shape.points.length === 2) {
      const [x0, y0] = at(shape.points[0]);
      const [x1, y1] = at(shape.points[1]);
      context.beginPath();
      context.ellipse((x0 + x1) / 2, (y0 + y1) / 2, Math.abs(x1 - x0) / 2, Math.abs(y1 - y0) / 2, 0, 0, Math.PI * 2);
      context.stroke();
    } else if (shape.kind === "line" && shape.points.length === 2) {
      context.beginPath();
      context.moveTo(...at(shape.points[0]));
      context.lineTo(...at(shape.points[1]));
      context.stroke();
    } else if (shape.kind === "arrow" && shape.points.length === 2) {
      const [x0, y0] = at(shape.points[0]);
      const [x1, y1] = at(shape.points[1]);
      const angle = Math.atan2(y1 - y0, x1 - x0);
      const head = (10 + 1.5 * (shape.size || 3)) * k;
      context.beginPath();
      context.moveTo(x0, y0);
      context.lineTo(x1, y1);
      context.moveTo(x1, y1);
      context.lineTo(x1 - head * Math.cos(angle - 0.45), y1 - head * Math.sin(angle - 0.45));
      context.moveTo(x1, y1);
      context.lineTo(x1 - head * Math.cos(angle + 0.45), y1 - head * Math.sin(angle + 0.45));
      context.stroke();
    } else if (shape.kind === "text" && shape.text) {
      const [x, y] = at(shape.points[0]);
      context.font = `600 ${Math.round(16 * k)}px system-ui, -apple-system, "Segoe UI", sans-serif`;
      context.textBaseline = "top";
      context.lineWidth = 4 * k;
      context.strokeStyle = "rgba(0, 0, 0, 0.75)";          // a dark edge: read on any picture
      context.strokeText(shape.text, x, y);
      context.fillText(shape.text, x, y);
    }
  });
}

/* Drawn by the pointer: where it is, as parts of the picture's width and height. */
function sideBoardInk(board) {
  const ink = board.querySelector(".side-board-ink");
  const where = (event) => {
    const box = ink.getBoundingClientRect();
    const x = Math.min(1, Math.max(0, (event.clientX - box.left) / Math.max(1, box.width)));
    const y = Math.min(1, Math.max(0, (event.clientY - box.top) / Math.max(1, box.height)));
    if (!sideBoardInPixels(sideBoard.page)) return [x, y];
    const frame = ink.parentElement;                 // the whiteboard: in its own pixels
    return [x * frame.clientWidth, y * frame.clientHeight];
  };
  let erasing = false;
  ink.addEventListener("pointerdown", (event) => {
    if (!sideBoard.base || !sideBoard.tool) return;
    event.preventDefault();
    sideBoardMenuAway();
    if (sideBoard.tool === "text") { sideBoardWrite(where(event)); return; }
    try { ink.setPointerCapture(event.pointerId); } catch (_err) { /* followed as it goes */ }
    const point = where(event);
    if (sideBoard.tool === "eraser") {
      sideBoardRemember();
      erasing = sideBoardErase(point) ? "took" : "on";
      return;
    }
    const kind = sideBoard.tool === "shape" ? sideBoard.shape : sideBoard.tool;
    sideBoard.drawing = { kind, colour: SIDE_BOARD_COLOURS[sideBoard.colour], size: sideBoard.size,
      points: kind === "pen" ? [point] : [point, point] };
    sideBoardPaintNow();
  });
  ink.addEventListener("pointermove", (event) => {
    if (erasing) { if (sideBoardErase(where(event))) erasing = "took"; return; }
    const shape = sideBoard.drawing;
    if (!shape) return;
    if (shape.kind === "pen") shape.points.push(where(event));
    else shape.points[1] = where(event);
    sideBoardPaintNow();
  });
  const done = () => {
    if (erasing) {
      if (erasing === "took") sideBoard.dirty = true;
      else if (sideBoard.page && sideBoard.page.undo) sideBoard.page.undo.pop();   // nothing taken: nothing to undo
      erasing = false;
      return;
    }
    const shape = sideBoard.drawing;
    sideBoard.drawing = null;
    if (!shape) return;
    const [a, b] = [shape.points[0], shape.points[shape.points.length - 1]];
    const least = sideBoardInPixels(sideBoard.page) ? 4 : 0.01;
    const moved = shape.kind === "pen" ? shape.points.length > 1 : Math.hypot(b[0] - a[0], b[1] - a[1]) > least;
    if (moved) { sideBoardRemember(); sideBoard.shapes.push(shape); sideBoard.dirty = true; }
    sideBoardPaintNow();
  };
  ink.addEventListener("pointerup", done);
  ink.addEventListener("pointercancel", done);
}

/* A PICTURE DRAGGED ONTO IT (the user, 2026-11-17: "允许我直接拖拽一个图片进去") is put in it, to draw on - a file from
   the desktop or a folder, a picture of the chat, or one off a page that lets itself be taken. Never the message's
   attachment as well: what is dropped here is the small screen's (the panel's own dropping is for the box). */
function sideBoardDrop(board) {
  const carries = (event) => {
    const types = [...((event.dataTransfer && event.dataTransfer.types) || [])];
    return types.includes("Files") || types.includes("text/uri-list");
  };
  board.addEventListener("dragover", (event) => {
    if (!carries(event)) return;
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = "copy";
    board.classList.add("dropping");
    document.body.classList.remove("side-dropping");
  });
  board.addEventListener("dragleave", (event) => {
    if (!event.relatedTarget || !board.contains(event.relatedTarget)) board.classList.remove("dropping");
  });
  board.addEventListener("drop", async (event) => {
    board.classList.remove("dropping");
    document.body.classList.remove("side-dropping");
    if (!carries(event)) return;
    event.preventDefault();
    event.stopPropagation();
    const file = [...(event.dataTransfer.files || [])].find((one) => /^image\//.test(one.type || ""));
    if (file) { sideBoardTake(file); return; }
    if ((event.dataTransfer.files || []).length) { toast("Only a picture can go in the small screen.", "error"); return; }
    // A PICTURE DRAGGED OFF A PAGE (the user, 2026-11-17: "一张图片，我直接拖，而不是下载什么的"): the picture itself (its
    // <img> in what was dragged), else the address dragged; taken here when the page lets it be, else fetched by the
    // agent - nothing to save first.
    let picture = "";
    try {
      const html = event.dataTransfer.getData("text/html");
      if (html) picture = new DOMParser().parseFromString(html, "text/html").querySelector("img")?.getAttribute("src") || "";
    } catch (_err) { picture = ""; }
    const address = picture || String(event.dataTransfer.getData("text/uri-list") || "").split(/\r?\n/)
      .find((one) => one && !one.startsWith("#")) || "";
    if (!address) return;
    const url = new URL(address, location.href);
    if (url.origin === location.origin) { sideBoardShowImage(url.href); return; }
    try {
      const answer = await fetch(url.href, { mode: "cors" });
      const blob = await answer.blob();
      if (!answer.ok || !/^image\//.test(blob.type)) throw new Error("not a picture");
      sideBoardTake(blob);
      return;
    } catch (_err) { /* the page will not give it: the agent fetches it */ }
    if (!/^https?:$/.test(url.protocol)) { toast("Only a picture can go in the small screen.", "error"); return; }
    try {
      const answer = await fetch("/api/side/fetch_picture", { method: "POST",
        headers: { "Content-Type": "application/json", "X-Time-Zone": PAGE_ZONE }, body: JSON.stringify({ url: url.href }) });
      if (!answer.ok) {
        let said = "";
        try { said = (await answer.json()).error || ""; } catch (_err) { said = ""; }
        throw new Error(said || "That picture could not be fetched.");
      }
      sideBoardTake(await answer.blob());
    } catch (err) {
      toast(err.message, "error");
    }
  });
}

/* A picture file put in it. */
function sideBoardTake(file) {
  sideBoardOpen();
  sideBoardBase({ kind: "image", url: URL.createObjectURL(file), screen: false, own: true, dropped: true }, file);
}

/* THE PARTS OF THE SCREEN THE USER CUT OUT (its shortcut - ui/snip.py), each a page of its own, shown - taken from the
   agent once (api/routes.py /api/side/board_inbox). */
let sideBoardTaking = false;
async function sideBoardInbox() {
  if (sideBoardTaking) return;
  sideBoardTaking = true;
  try {
    const said = await api.get("/api/side/board_inbox");
    for (const ident of said.cuts || []) {
      const answer = await fetch(`/api/side/board_cut?id=${encodeURIComponent(ident)}`, { headers: { "X-Time-Zone": PAGE_ZONE } });
      if (!answer.ok) continue;
      const blob = await answer.blob();
      sideBoardOpen();
      sideBoardBase({ kind: "image", url: URL.createObjectURL(blob), screen: false, own: true, cut: true }, blob);
    }
  } catch (_err) {
    /* taken at the next */
  } finally {
    sideBoardTaking = false;
  }
}
// One cut out while the panel was away or starting: taken as it is here.
setTimeout(() => { if (typeof api !== "undefined") sideBoardInbox(); }, 1500);

/* Words put on the picture where it was pressed: Enter keeps them, Esc lets them go. */
function sideBoardWrite(point) {
  const frame = document.querySelector("#side-board .side-board-frame");
  if (!frame) return;
  frame.querySelector(".side-board-words")?.remove();
  const box = document.createElement("input");
  box.type = "text";
  box.className = "side-board-words";
  box.placeholder = "Type, then Enter";
  const pixels = sideBoardInPixels(sideBoard.page);
  box.style.left = pixels ? `${point[0]}px` : `${point[0] * 100}%`;
  box.style.top = pixels ? `${point[1]}px` : `${point[1] * 100}%`;
  box.style.color = SIDE_BOARD_COLOURS[sideBoard.colour];
  frame.appendChild(box);
  let kept = false;
  const keep = () => {
    if (kept) return;
    kept = true;
    const words = box.value.trim();
    box.remove();
    if (!words) return;
    sideBoardRemember();
    sideBoard.shapes.push({ kind: "text", colour: SIDE_BOARD_COLOURS[sideBoard.colour], points: [point], text: words });
    sideBoard.dirty = true;
    sideBoardPaintNow();
  };
  box.addEventListener("keydown", (event) => {
    event.stopPropagation();
    if (event.key === "Enter" && !event.isComposing) { event.preventDefault(); keep(); }
    else if (event.key === "Escape") { kept = true; box.remove(); }
  });
  box.addEventListener("blur", keep);
  setTimeout(() => box.focus(), 0);
}

/* Its height dragged by the grip under it - kept for the next time. */
function sideBoardGrip(board) {
  const grip = board.querySelector(".side-board-grip");
  let start = null;
  grip.addEventListener("pointerdown", (event) => {
    try { grip.setPointerCapture(event.pointerId); } catch (_err) { /* followed as it goes */ }
    start = { y: event.clientY, height: sideBoard.height };
    event.preventDefault();
  });
  grip.addEventListener("pointermove", (event) => {
    if (!start) return;
    sideBoard.height = start.height + (event.clientY - start.y) / sideBoardZoom();
    sideBoardPlace();
  });
  const done = () => {
    if (!start) return;
    start = null;
    try { localStorage.setItem(SIDE_BOARD_HEIGHT_KEY, String(Math.round(sideBoard.height))); } catch (_err) { /* this time only */ }
  };
  grip.addEventListener("pointerup", done);
  grip.addEventListener("pointercancel", done);
}

/* The picture with its drawing, as large as it is (no larger than SIDE_BOARD_MOST_SIDE), on a canvas. */
function sideBoardPicture() {
  const board = document.getElementById("side-board");
  const frame = board && board.querySelector(".side-board-frame");
  if (board && board.querySelector(".side-board-white")) {     // the whiteboard: white, and what was drawn on it
    const k = SIDE_BOARD_WHITE_SENT;
    const w = Math.round(frame.clientWidth * k), h = Math.round(frame.clientHeight * k);
    const canvas = Object.assign(document.createElement("canvas"), { width: w, height: h });
    const context = canvas.getContext("2d");
    context.fillStyle = "#ffffff";
    context.fillRect(0, 0, w, h);
    sideBoardPaint(context, w, h, k, sideBoard.shapes, k);
    return canvas;
  }
  const picture = board && board.querySelector(".side-board-base img");
  if (!picture || !picture.naturalWidth) return null;
  const scale = Math.min(1, SIDE_BOARD_MOST_SIDE / Math.max(picture.naturalWidth, picture.naturalHeight));
  const w = Math.round(picture.naturalWidth * scale), h = Math.round(picture.naturalHeight * scale);
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const context = canvas.getContext("2d");
  context.drawImage(picture, 0, 0, w, h);
  sideBoardPaint(context, w, h, w / Math.max(1, frame.clientWidth), sideBoard.shapes);
  return canvas;
}

/* On the agent's drawing: what each mark is on, in words - a ring round each shape (side-visual.js sideHeld). */
function sideBoardSaid() {
  const board = document.getElementById("side-board");
  const card = board && board.querySelector(".side-board-base .side-viz-card");
  const view = sideBoard.base && sideBoard.base.view;
  if (!card || !view || !sideBoard.shapes.length || typeof sideParts !== "function") return "";
  const area = board.querySelector(".side-board-frame").getBoundingClientRect();
  const parts = sideParts(card, view);
  const ringOf = (points, pad = 0) => {
    const xs = points.map((p) => p[0] * area.width), ys = points.map((p) => p[1] * area.height);
    const left = Math.min(...xs) - pad, right = Math.max(...xs) + pad;
    const top = Math.min(...ys) - pad, bottom = Math.max(...ys) + pad;
    return { cx: (left + right) / 2, cy: (top + bottom) / 2, rx: (right - left) / 2, ry: (bottom - top) / 2 };
  };
  const title = `${view.title ? ` "${view.title}"` : ""} (small screen picture ${sideBoard.page.id}${sideBoard.page.name ? `, named "${sideBoard.page.name}"` : ""})`;
  const said = [];
  const marks = sideBoard.shapes.filter((one) => one.kind !== "text" && !one.agent);
  if (marks.length) {
    // An arrow points at where it ends: that end is what it marks.
    const held = sideHeld(parts, marks.map((one) => one.kind === "arrow" ? ringOf([one.points[1]], 24) : ringOf(one.points, 4)), area);
    said.push(held.length ? `[On the picture${title}, I marked: ${held.join("; ")}]`
      : `[On the picture${title}, I drew ${marks.length} mark(s) on no part of it]`);
  }
  sideBoard.shapes.filter((one) => one.kind === "text" && !one.agent).forEach((one) => {
    const near = sideHeld(parts, [ringOf(one.points, 40)], area);
    said.push(`[On the picture${title}, I wrote "${one.text}"${near.length ? ` by: ${near.join("; ")}` : ""}]`);
  });
  return said.join("\n");
}

/* WHAT GOES WITH THE NEXT MESSAGE (notes.js sideSend), WHILE IT IS OPEN - every message: nothing when it is shut or a
   whiteboard not drawn on;
   a picture - uploaded as the message's own - said what it is; on the agent's drawing, words. THE SCREEN NOW GOES AS
   WELL (the user, 2026-11-17: "是同时把2个图发给chat, 一个是主屏幕的一个是小窗口的，chat自己判断怎么处理"): the small screen's
   is a picture beside it, never in its place - a screenshot in it said with when it was taken, so an old one is never
   taken for the screen as it is. */
async function sideBoardForMessage() {
  const out = { ids: [], shown: [], words: "" };
  if (!sideBoard.open || !sideBoard.base) return out;
  if (sideBoard.base.kind === "view") {
    if (!sideBoard.dirty) return out;               // its marks said once - the agent knows its own drawing
    out.words = sideBoardSaid();
    sideBoard.dirty = false;
    return out;
  }
  const canvas = sideBoardPicture();
  if (!canvas) return out;
  const data = canvas.toDataURL("image/png").split(",")[1] || "";
  const blank = sideBoard.base.kind === "blank";
  if (blank && !sideBoard.shapes.length) { sideBoard.dirty = false; return out; }   // all of it rubbed out: nothing
  const name = blank ? "whiteboard.png" : sideBoard.base.screen ? "screen-drawn-on.png" : "picture-drawn-on.png";
  const kept = await api.post("/api/chat/upload", { name, data });
  out.ids.push(kept.id);
  out.shown.push(kept);
  sideBoard.page.used = Date.now();
  sideBoardSave(sideBoard.page);
  out.words = sideBoardWhatItIs(sideBoard.page);
  sideBoard.dirty = false;
  return out;
}

/* The small screen's picture, said: what it is, when a screenshot was taken, and whether the user drew on it - told
   apart from the picture of the screen as it is now, which goes with the message too. */
function sideBoardWhatItIs(page) {
  const mine = page.shapes.filter((one) => !one.agent).length, yours = page.shapes.length - mine;
  const drawn = (mine ? ", with what I drew on it" : "") + (yours ? `${mine ? " and" : ","} the marks you drew on it` : "");
  let what = `a whiteboard${drawn}`;
  if (page.base.kind !== "blank") {
    if (page.base.screen) {
      const at = new Date(page.created);
      const today = at.toDateString() === new Date().toDateString();
      const when = at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      what = `a screenshot of my screen I took ${today ? "at" : `on ${at.toLocaleDateString()} at`} ${when}${drawn}`;
    } else {
      what = (page.base.cut ? `a part of my screen I cut out at ${new Date(page.created).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`
        : page.base.dropped ? "a picture I put in" : "a picture from our conversation") + drawn;
    }
  }
  const named = page.name ? `, named "${page.name}"` : "";
  return `[One picture with this message is from the small screen (small screen picture ${page.id}${named}): ${what}. Any other picture of my screen with it is my screen as it is now.]`;
}

window.addEventListener("resize", () => { if (sideBoard.open) sideBoardPlace(); });

/* A SCREENSHOT BUTTON ON THE BAR (the user, 2026-11-17: "我的image panel 要加个截屏按钮"): the same cut-out as the shortcut -
   the screen frozen, a part of it dragged out, put in the small screen as a new picture (ui/desktop.py
   _snip_into_the_board, then the "board" event takes it). Esc leaves all as it was. */
function sideBoardSnip() {
  api.post("/api/side/board_snip", {}).then((said) => {
    if (said && said.error) toast(said.error, "error");
  }).catch((err) => toast(err.message, "error"));
}
