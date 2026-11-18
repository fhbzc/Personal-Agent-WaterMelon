/* THE PLAY VIEW - THE AGENT'S WORLD OF THE USER'S DAYS, TO WALK IN (agent/playworld.py; the user, 2026-11-17: "切换到一个play
   view之后，你的agent会根据他对你的理解和看法，生成不同的场景 ... 到某个位置就会有你当时的activity的图片显示出来 ... 你可以操控这个
   agent在这个世界上下左右走"; "要做好优化，agent在里面跑的时候不会卡"; "到一个不同的区域要大屏幕显示名字（就和游戏里到另一个区域
   一样）").

   One tile set (Kenney's Roguelike packs, CC0 - play/ASSETS.txt), 16 pixels a tile, drawn larger without smoothing.
   The world as the agent keeps it (rooms, things, where each is) is laid into tiles here; drawn ONCE into blocks of
   32 by 32 tiles, each made when it first comes into sight and kept - each frame only the blocks in sight are put on
   the screen, then the agent. Nothing is drawn while nothing moves: the frames run only while a key is held, the
   agent is still walking, or a room's name is showing. A room entered: its name large, on a wooden sign. A thing
   near: what it is, when it was made, and why the agent made it - in its own words - with the user's picture when it
   holds one. */

const PLAY_TILE = 16;
const PLAY_CHUNK = 32;
const PLAY_SPEED = 4.6;                 // tiles a second
const PLAY_SHEETS = { rpg: "/play/rpg.png", indoor: "/play/indoor.png", me: "/play/watermelon.png", city: "/play/city.png",
                      cave: "/play/cave.png", town: "/play/town.png", urban: "/play/urban.png", dungeon: "/play/dungeon.png",
                      creatures: "/play/creatures.png", farm: "/play/farm.png", ships: "/play/ships.png",
                      grave: "/play/grave.png", festive: "/play/festive.png", drawn: "/play/drawn.png",
                      sea: "/play/sea.png", love: "/play/love.png", yard: "/play/yard.png" };
/* [sheet, column, row] of each ground piece. */
const PLAY_GROUND = {
  grass: ["rpg", 5, 0], grass2: ["rpg", 5, 1], road: ["rpg", 6, 0], road2: ["rpg", 6, 1],
  wood: ["rpg", 5, 2], stone: ["rpg", 6, 2], tiles: ["rpg", 7, 2], flowers: ["rpg", 0, 9],
  wallTL: ["rpg", 16, 12], wallT: ["rpg", 14, 12], wallTR: ["rpg", 17, 12], wallSide: ["rpg", 15, 13],
  faceHi: ["rpg", 18, 15], faceLo: ["rpg", 13, 15], wallBL: ["rpg", 16, 13], wallBR: ["rpg", 17, 13],
  outHiL: ["rpg", 17, 15], outHiR: ["rpg", 19, 15], outLoL: ["rpg", 14, 15], outLoR: ["rpg", 16, 15],
  doorL: ["rpg", 13, 12], doorR: ["rpg", 15, 12],
};
/* [sheet, column, row, width, height] of each kind of thing (agent/playworld.py ITEM_KINDS); a frame is drawn. */
const PLAY_ITEMS = {
  window: ["rpg", 42, 2, 1, 1], mirror: ["rpg", 25, 7, 1, 2], bookshelf: ["rpg", 42, 12, 1, 2],
  bookcase: ["rpg", 44, 12, 2, 2], desk: ["rpg", 25, 5, 1, 1], table: ["rpg", 26, 3, 2, 1], chair: ["rpg", 19, 3, 1, 1],
  bed: ["indoor", 12, 0, 2, 2], wardrobe: ["rpg", 26, 5, 1, 2], cabinet: ["rpg", 28, 3, 1, 2], lamp: ["indoor", 19, 4, 1, 2],
  piano: ["indoor", 23, 8, 2, 1], plant: ["indoor", 16, 0, 1, 1], kitchen: ["indoor", 4, 12, 4, 1],
  stove: ["indoor", 14, 14, 1, 1], fireplace: ["rpg", 13, 0, 1, 1], flowerpot: ["rpg", 28, 9, 1, 1], tree: ["rpg", 13, 10, 1, 1],
  pine: ["rpg", 16, 10, 1, 1], bush: ["rpg", 23, 9, 1, 1], frame: null,
};
/* A FEW LOOKS FOR A KIND (the user, 2026-11-17: "你现在的item太单调了"): each thing one of its kind's, chosen by the
   thing itself - two beds, two bookshelves, never quite the same room twice. [sheet, column, row, width, height]. */
const PLAY_LOOKS = {
  bed: [["indoor", 12, 0, 2, 2], ["indoor", 14, 0, 2, 2]], plant: [["indoor", 16, 0, 1, 1], ["indoor", 17, 0, 1, 1]],
  bookshelf: [["rpg", 42, 12, 1, 2], ["rpg", 43, 12, 1, 2], ["rpg", 41, 12, 1, 2], ["dungeon", 3, 5, 1, 2]],
  chest: [["dungeon", 5, 7, 1, 1], ["dungeon", 6, 7, 1, 1]], well: [["town", 8, 7, 1, 2]], beehive: [["town", 10, 7, 1, 1]],
  bookcase: [["rpg", 44, 12, 2, 2], ["rpg", 46, 12, 2, 2]], cabinet: [["rpg", 28, 3, 1, 2], ["rpg", 34, 10, 1, 2], ["rpg", 36, 10, 1, 2]],
  sofa: [["indoor", 14, 8, 2, 1]], armchair: [["indoor", 14, 9, 1, 1], ["indoor", 15, 9, 1, 1], ["indoor", 14, 10, 1, 1]],
  dining_table: [["indoor", 3, 0, 2, 2]], candelabra: [["indoor", 19, 0, 1, 1], ["indoor", 20, 0, 1, 1]],
  candle: [["indoor", 16, 6, 1, 1], ["rpg", 21, 7, 1, 1]], sink: [["indoor", 8, 12, 1, 1]],
  counter: [["indoor", 1, 13, 1, 1], ["indoor", 5, 13, 1, 1], ["indoor", 6, 13, 1, 1]], organ: [["indoor", 25, 8, 1, 2]],
  barrel: [["rpg", 23, 0, 1, 1], ["rpg", 24, 0, 1, 1], ["dungeon", 6, 5, 1, 1]], wall_torch: [["indoor", 20, 6, 1, 1]],
  painting: [["indoor", 16, 12, 1, 1], ["indoor", 17, 12, 1, 1], ["indoor", 18, 12, 1, 1]], map: [["indoor", 19, 12, 1, 1]],
  tapestry: [["indoor", 16, 14, 3, 1], ["indoor", 16, 15, 3, 1], ["indoor", 16, 16, 3, 1]],
  portrait: [["indoor", 19, 15, 1, 2], ["indoor", 20, 15, 1, 2], ["indoor", 21, 15, 1, 2]],
  wall_shelf: [["indoor", 19, 17, 1, 1], ["indoor", 20, 17, 1, 1], ["indoor", 21, 17, 1, 1]],
  shield: [["indoor", 22, 8, 1, 1], ["indoor", 22, 10, 1, 1]], rug: [null],
  statue_of: [null], banner: [null], poster: [null], pixel_art: [null], pixel_art_large: [null], wall_art: [null],
  cat: [["creatures", 4, 9, 1, 1]], dog: [["creatures", 9, 16, 1, 1]], rabbit: [["creatures", 7, 17, 1, 1]],
  gravestone: [["yard", 12, 0, 1, 1], ["yard", 13, 0, 1, 1], ["yard", 10, 0, 1, 1], ["yard", 16, 0, 1, 1]], coffin: [["grave", 18, 0, 2, 2]],
  pumpkin: [["love", 9, 0, 1, 1], ["festive", 17, 0, 1, 1], ["festive", 18, 0, 1, 1]],
  lantern: [["drawn", 0, 0, 1, 1]], firecrackers: [["drawn", 1, 0, 1, 1]], heart: [["love", 0, 0, 1, 1], ["love", 1, 0, 1, 1]], roses: [["love", 4, 0, 1, 1], ["love", 5, 0, 1, 1]],
  balloons: [["drawn", 4, 0, 1, 1]], present: [["festive", 0, 0, 1, 1], ["festive", 1, 0, 1, 1], ["festive", 2, 0, 1, 1], ["festive", 3, 0, 1, 1]],
  xmas_tree: [["festive", 4, 0, 1, 1], ["festive", 5, 0, 1, 1]], fish_tank: [["drawn", 20, 0, 2, 1]], jellyfish: [["drawn", 6, 0, 1, 1]],
  coral: [["drawn", 7, 0, 1, 1], ["drawn", 8, 0, 1, 1]], ticket_booth: [["drawn", 34, 0, 2, 2]], candy_stand: [["drawn", 40, 0, 2, 2], ["drawn", 36, 0, 2, 2], ["drawn", 38, 0, 2, 2]],
  ferris_wheel: [["drawn", 62, 0, 5, 5]], carousel: [["drawn", 56, 0, 4, 4]], coaster_car: [["drawn", 13, 0, 1, 1]],
  sunflower: [["farm", 11, 6, 1, 1]],
  whiteboard: [["drawn", 89, 0, 2, 1]],  school_desk: [["drawn", 91, 0, 1, 1]],  lab_bench: [["drawn", 92, 0, 2, 1]],  microscope: [["drawn", 94, 0, 1, 1]],  computer: [["drawn", 95, 0, 1, 1]],  cubicle: [["drawn", 96, 0, 2, 2]],  water_cooler: [["drawn", 98, 0, 1, 2]],  printer: [["drawn", 99, 0, 1, 1]],  lockers: [["drawn", 100, 0, 2, 2]],  bunk_bed: [["drawn", 102, 0, 2, 2]],  kitchenette: [["drawn", 104, 0, 2, 1]],  server_rack: [["drawn", 106, 0, 1, 2]],
 crops: [["farm", 6, 4, 1, 1], ["farm", 8, 3, 1, 1], ["farm", 8, 2, 1, 1], ["farm", 8, 0, 1, 1]],
  robot: [["creatures", 1, 8, 1, 1], ["creatures", 2, 8, 1, 1], ["creatures", 0, 8, 1, 1]],
  // Outdoors (agent/playplaces.py): a fountain, a lounger, a parasol, a statue, a lamp post, a hedge, a flowerbed.
  fountain: [["city", 26, 4, 3, 3]], lounger: [["rpg", 18, 4, 1, 1]], parasol: [["city", 34, 14, 1, 1], ["city", 35, 14, 1, 1]],
  statue: [["rpg", 50, 10, 1, 2]], lamp_post: [["rpg", 15, 7, 1, 1]], hedge: [["rpg", 21, 9, 1, 1], ["rpg", 19, 9, 1, 1]],
  flowerbed: [["rpg", 29, 9, 1, 1], ["rpg", 31, 9, 1, 1], ["rpg", 30, 9, 1, 1]],
};
for (const [kind, looks] of Object.entries(PLAY_LOOKS)) if (looks[0]) PLAY_ITEMS[kind] = looks[0];
PLAY_ITEMS.rug = null;
for (const kind of ["statue_of", "banner", "poster", "pixel_art", "pixel_art_large", "wall_art"]) PLAY_ITEMS[kind] = null;
const PLAY_RUG_INKS = [["#9b3b2e", "#e9c38a"], ["#2f6b5a", "#e8dcb5"], ["#3a4f8f", "#e6d9a8"], ["#7b4a8c", "#f0d8a0"],
                       ["#b8742c", "#f6e7c1"], ["#5b6b2e", "#efe0b0"]];
const PLAY_SIZES = Object.fromEntries(Object.entries(PLAY_ITEMS).map(([k, v]) => [k, v ? [v[3], v[4]] : [2, 2]]));
PLAY_SIZES.frame = [2, 2];
PLAY_SIZES.rug = [3, 2];
Object.assign(PLAY_SIZES, { statue_of: [1, 2], banner: [1, 2], poster: [2, 2], pixel_art: [1, 1], pixel_art_large: [2, 2], wall_art: [2, 1] });
const PLAY_UNDER_KINDS = new Set(["rug", "parasol", "flowerbed"]);       // walked over (a rug drawn first)
/* THE AGENT IS THE WATERMELON (the user, 2026-11-17: "logo就不在显示在屏幕上，但是会显示在游戏视图里（主角的avatar, 有行走动画
   什么的)"): scripts/play_watermelon.py - a row each way it faces, four frames of a step; while the view is open the
   avatar is off the screen (ui/desktop.py avatar_in_play). */
const PLAY_ME_ROWS = { down: 0, left: 1, right: 2, up: 3 };
const PLAY_STEPS_A_SECOND = 7;
const PLAY_RUN = 1.9;                    // how much faster, running
const PLAY_DUST_MS = 450;
const PLAY_WALL_KINDS = new Set(["frame", "window", "mirror", "wall_torch", "painting", "map", "tapestry", "portrait",
                                 "wall_shelf", "shield", "banner", "poster", "wall_art"]);
const PLAY_FLOORS = { home: "wood", study: "stone", library: "wood", screening: "stone", cafe: "tiles", studio: "wood",
                      station: "tiles", garden: "grass", park: "grass", pool: "paving", courtyard: "cobble", terrace: "bridge",
                      lake: "grass", waterfall: "rock", plaza: "paving", grove: "grass2", meadow: "grass", mountain: "grass2",
                      volcano: "rock", peak: "grass2", aquarium: "tiles", classroom: "wood", lab: "tiles", office: "tiles",
                      apartment: "wood", lecture_hall: "wood", sea_tunnel: "water", roller_coaster: "grass",
                      cemetery: "grass2", greenhouse: "grass", funfair: "paving" };
/* OPEN PLACES, no walls (agent/playplaces.py OUTDOOR): their edge, and a pool's water in its middle. */
const PLAY_OUTDOOR = { garden: "flowers", park: "grass2", pool: "paving", courtyard: "paving", terrace: "paving",
                       lake: "grass2", waterfall: "rock2", plaza: "cobble", grove: "grass", meadow: "grass2", mountain: "grass2",
                       volcano: "rock2", peak: "grass2", sea_tunnel: "water", roller_coaster: "grass2", cemetery: "dirt2",
                       funfair: "cobble" };
/* A building's style: its walls' material and its floor (agent/playplaces.py STYLES); unset, its region's. */
const PLAY_STYLES = { cottage: ["timber", "wood"], classic: ["sandstone", "wood"], modern: ["stone", "tiles"],
                      rustic: ["timber", "wood"], zen: ["timber", "stone"], industrial: ["stone", "stone"],
                      mansion: ["marble", "tiles"], toy: ["marble", "tiles"], magic: ["marble", "stone"],
                      futuristic: ["stone", "tiles"], ruins: ["sandstone", "stone"], sky: ["marble", "tiles"],
                      haunted: ["timber", "wood"], skyscraper: ["stone", "tiles"], office: ["stone", "tiles"],
                      apartment: ["sandstone", "wood"], school: ["sandstone", "wood"], laboratory: ["stone", "tiles"] };
/* A toy house's walls bright as a toy's; a magic one's violet (the user, 2026-11-17: "玩具城风格，魔法风格"). */
const PLAY_STYLE_WALLS = { skyscraper: [70, 110, 160, 0.45], office: [90, 120, 150, 0.4], apartment: [180, 120, 90, 0.35],
                           school: [190, 80, 60, 0.4], laboratory: [230, 236, 240, 0.5], haunted: [58, 40, 78, 0.55], toy: [232, 72, 85, 0.45], magic: [128, 82, 214, 0.45], futuristic: [70, 200, 225, 0.42],
                           ruins: [120, 108, 88, 0.38], sky: [236, 246, 255, 0.45] };
const PLAY_CELL = [18, 15];

const play = {
  on: false, sheets: {}, world: null, map: null, chunks: new Map(), pictures: new Map(), keys: new Set(),
  me: { x: 0, y: 0, facing: "down", step: 0, jump: 0 }, said: null, running: false, dust: [], leaves: [], touched: null, camera: { x: 0, y: 0 }, zone: null, banner: null, near: null,
  frame: 0, last: 0, scale: 3, zoom: 0, el: null, canvas: null, ctx: null,
};
/* ZOOMED IN AND OUT (the user, 2026-11-17: "你那个游戏界面也要能放大缩小"): the wheel, + and -, 0 back - the world alone, in
   whole steps of its pixels (crisp at each); the panel's own zoom untouched. Kept for the next time. */
const PLAY_ZOOM_KEY = "playZoom";
const PLAY_ZOOM_MOST = 4;
const PLAY_ZOOM_ON_OPEN = 1;
const PLAY_WHERE_KEY = "playWhere";

/* Where the walker is, kept - while it walks (now and then), and when the view shuts or is hidden. */
function playKeepWhere() {
  if (!play.world) return;
  try {
    localStorage.setItem(PLAY_WHERE_KEY, JSON.stringify({ born: play.world.born, x: +play.me.x.toFixed(2),
                                                          y: +play.me.y.toFixed(2), facing: play.me.facing }));
  } catch (_err) { /* this time only */ }
}

/* -- the view, opened and shut ---------------------------------------------------------------------------------- */
function playEl() {
  if (play.el) return play.el;
  const box = document.createElement("section");
  box.id = "side-play";
  box.className = "side-play";
  box.hidden = true;
  box.setAttribute("aria-label", "Play");
  box.innerHTML = `<canvas class="side-play-canvas" tabindex="0" aria-label="${escapeHtml("Your world - walk with the arrow keys")}"></canvas>
    <p class="side-play-hint">${escapeHtml("Arrow keys or WASD to walk, Shift to run, Space to jump, E to look, M for the map")}</p>
    <p class="side-play-off" hidden>${escapeHtml("Your world is not growing - switched off in Settings > Modules > Play world.")}</p>
    <div class="side-play-corner"><p class="side-play-growing" hidden></p>
    <canvas class="side-play-mini" width="336" height="256" aria-label="${escapeHtml("Map of your world")}"
      title="${escapeHtml("Map of your world - press to make it larger or smaller")}"></canvas></div>
    <div class="side-play-card" hidden></div>
    <div class="side-play-zoom"><button type="button" data-play-zoom="-1" aria-label="${escapeHtml("Zoom out")}"
      title="${escapeHtml("Zoom out")}">−</button><button type="button" data-play-zoom="1" aria-label="${escapeHtml("Zoom in")}"
      title="${escapeHtml("Zoom in")}">+</button><button type="button" data-play-map aria-label="${escapeHtml("Map (M)")}"
      title="${escapeHtml("Map (M)")}">▦</button><button type="button" data-play-unstuck aria-label="${escapeHtml("Stuck? Move somewhere free")}"
      title="${escapeHtml("Stuck? Move somewhere free")}">⤨</button><button type="button" data-play-sound aria-label="${escapeHtml("Sound")}"
      title="${escapeHtml("Sound")}">♪</button></div>`;
  document.body.appendChild(box);
  play.el = box;
  play.canvas = box.querySelector("canvas");
  play.ctx = play.canvas.getContext("2d");
  play.mini = box.querySelector(".side-play-mini");
  try { play.mini.hidden = localStorage.getItem("playMini") === "off"; } catch (_err) { /* shown */ }
  // A press makes it larger or smaller - never hides it (the user, 2026-11-17: "现在小地图怎么一点就没有了"); the map
  // button in the corner, or M, hides and shows it.
  play.mini.addEventListener("click", () => playMiniLarger());
  try { play.mini.classList.toggle("large", localStorage.getItem("playMiniLarge") === "on"); } catch (_err) { /* small */ }
  new ResizeObserver(() => playFit()).observe(box);
  play.canvas.addEventListener("pointerdown", () => play.canvas.focus());
  try { play.zoom = Number(localStorage.getItem(PLAY_ZOOM_KEY)) || 0; } catch (_err) { play.zoom = 0; }
  box.querySelector(".side-play-zoom").addEventListener("click", (event) => {
    const step = event.target.closest && event.target.closest("[data-play-zoom]");
    if (step) playZoom(Number(step.dataset.playZoom));
    if (event.target.closest && event.target.closest("[data-play-sound]")) playSoundPanel(box);
    if (event.target.closest && event.target.closest("[data-play-unstuck]")) playUnstuck();
    if (event.target.closest && event.target.closest("[data-play-map]")) playMiniToggle();
  });
  play.canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    event.stopPropagation();                   // the world's, not the panel's
    playZoom(event.deltaY < 0 ? 1 : -1);
  }, { passive: false });
  box.querySelector(".side-play-card").addEventListener("click", (event) => {
    const picture = event.target.closest && event.target.closest("img");
    if (picture && typeof sideLarger === "function") sideLarger(picture.src);
  });
  return box;
}

async function playOpen() {
  const box = playEl();
  playPlace();
  box.hidden = false;
  play.on = true;
  document.body.classList.add("side-playing");
  if (typeof sideBoard !== "undefined" && sideBoard.open && typeof sideBoardClose === "function") sideBoardClose();   // no small screen in play
  playPressed();
  playAvatarOff(true);
  playSoundStart();                            // opened by a press: the page may make sound now
  await playLoadSheets();
  await playLoadWorld();
  // A CLOSE LOOK ON COMING IN (the user, 2026-11-17: "每次刚进入play mode的时候，不要给全景，要给一个把avatar放中间的比较narrow的
  // view"): the walker in the middle, a step nearer than the usual - however far it was zoomed out before.
  play.zoom = PLAY_ZOOM_ON_OPEN;
  playFit();
  playWhere();
  playDraw();                                  // drawn again with its place known: its sky from the first look
  playSoundRoom(play.zone);
  play.canvas.focus({ preventScroll: true });
  playRun();
}

function playClose() {
  playKeepWhere();
  playAvatarOff(false);
  playSoundStop();
  play.on = false;
  play.keys.clear();
  playPressed();
  if (play.el) play.el.hidden = true;
  document.body.classList.remove("side-playing");
}

/* The avatar off the screen while it walks here; back when the view shuts, or the panel is put away. */
function playAvatarOff(on) {
  if (play.avatarOff === on) return;
  play.avatarOff = on;
  api.post("/api/play/showing", { on }).catch(() => { play.avatarOff = !on; });
}
document.addEventListener("visibilitychange", () => {
  if (!play.on) return;
  if (document.hidden) playKeepWhere();
  playAvatarOff(!document.hidden);
  if (document.hidden) playSoundStop(); else { playSoundStart(); playSoundRoom(play.zone); }
});

/* ITS BUTTON BLUE WHILE IT IS OPEN, as Pin's while pinned (the user, 2026-11-17: "你选择play的时候，你要和pin的按钮一样，
   显示蓝色"): however it was opened or shut. */
function playPressed() {
  document.getElementById("side-play-open")?.setAttribute("aria-pressed", String(play.on));
}

/* -- JUMPING AND LOOKING (the user, 2026-11-17: "你那个游戏空格是跳跃吧，增加点交互") ---------------------------------- */
const PLAY_JUMP_MS = 420;
function playJump() {
  if (playJumpHeight() > 0) return;
  play.me.jump = performance.now();
  playSoundHop();
  playRun();
}
function playJumpHeight() {
  const age = performance.now() - (play.me.jump || 0);
  return age >= 0 && age < PLAY_JUMP_MS ? Math.sin(Math.PI * age / PLAY_JUMP_MS) : 0;
}

/* E or Enter: the watermelon says what the thing near is to it - why it made it - in a bubble over its head, a word at a
   time; a picture's frame opens the picture large. Nothing near: what this room, or this land, is to it. */
const PLAY_SAY_MOST = 160;
/* How long something said stays (the user, 2026-11-17: "E之后打开的look持续的时间太长了"): short, at most five seconds;
   gone at once if E is pressed again or the walker steps away. */
// Three seconds longer than it was (the user, 2026-11-17: "look agent 说的话（游戏里) 稍微多留3秒").
function playSayFor(text) { return Math.min(8000, 4200 + text.length * 35); }
function playLook() {
  if (typeof playAct === "function" && playAct()) { playDraw(); return; }   // done (play-act.js): sat, lit, cast, gone
  if (play.said && performance.now() < play.said.until) { play.said = null; playDraw(); return; }   // pressed again: gone
  const near = play.near;
  if (near && near.picture_url && typeof sideLarger === "function") sideLarger(near.picture_url);
  if (near && !near.at && /book|shelf|desk|map|scroll/.test(near.kind)) playSfx("book-flip", 0.45);
  const place = play.zone || play.region;
  const sign = near && near.at ? (near.zone || near.region) : null;
  const text = sign ? (sign.why || near.name || "") : near ? (near.why || near.name || "") : (place && (place.why || place.name)) || "";
  if (!text) return;
  const words = text.length > PLAY_SAY_MOST ? `${text.slice(0, PLAY_SAY_MOST - 1)}…` : text;
  play.said = { text: words, at: performance.now(), until: performance.now() + playSayFor(words), x: play.me.x, y: play.me.y };
  playSoundNear();
  playRun();
}

/* The bubble: white, a dark edge, its tail to the watermelon's head; the words wrapped to the view's width. */
function playSaidDraw(cx, top) {
  const said = play.said;
  if (!said) return;
  const now = performance.now();
  const away = said.x !== undefined && Math.hypot(play.me.x - said.x, play.me.y - said.y) > 1.5;
  if (now > said.until || away) { play.said = null; return; }
  const { ctx, canvas } = play;
  const shown = said.text.slice(0, Math.max(1, Math.floor((now - said.at) / 28)));     // a letter at a time
  const fontPx = Math.max(13, Math.round(play.scale * 4.2));
  ctx.save();
  ctx.font = `${fontPx}px "Microsoft YaHei", "PingFang SC", system-ui, sans-serif`;
  const most = Math.min(canvas.width * 0.8, fontPx * 18);
  const lines = [];
  let line = "";
  for (const ch of said.text) {                        // wrapped as the whole will be, so it does not jump about
    if (ctx.measureText(line + ch).width > most && line) { lines.push(line); line = ch; } else line += ch;
  }
  if (line) lines.push(line);
  const pad = Math.round(fontPx * 0.6), lineH = Math.round(fontPx * 1.35);
  const w = Math.max(...lines.map((one) => ctx.measureText(one).width)) + pad * 2, h = lines.length * lineH + pad * 2 - (lineH - fontPx);
  const x = Math.max(6, Math.min(canvas.width - w - 6, cx - w / 2)), y = Math.max(6, top - h - fontPx);
  ctx.fillStyle = "#fffdf6";
  ctx.strokeStyle = "#3b2414";
  ctx.lineWidth = Math.max(2, Math.round(play.scale * 0.7));
  ctx.beginPath();
  ctx.roundRect(x, y, w, h, Math.round(fontPx * 0.5));
  ctx.moveTo(cx - fontPx * 0.4, y + h);
  ctx.lineTo(cx, y + h + fontPx * 0.7);
  ctx.lineTo(cx + fontPx * 0.4, y + h);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = "#fffdf6";
  ctx.fillRect(cx - fontPx * 0.4 + ctx.lineWidth, y + h - ctx.lineWidth, fontPx * 0.8 - 2 * ctx.lineWidth, ctx.lineWidth * 1.5);
  ctx.fillStyle = "#3b2414";
  ctx.textBaseline = "top";
  let left = shown.length;
  lines.forEach((one, n) => {
    if (left <= 0) return;
    ctx.fillText(one.slice(0, left), x + pad, y + pad + n * lineH);
    left -= one.length;
  });
  ctx.restore();
}

/* UNSTUCK (the user, 2026-11-17: "为了防止再次卡住，要有防卡按钮"; "防止卡顿会随机刷新在比较附近的区域，而不是全地图随机"):
   a short way off, at random - the nearest ring round the walker (two tiles out, then three, up to six) that has a
   free spot, free all round so it is not another corner; one on a road first if that ring has one. */
const PLAY_UNSTUCK_RINGS = [2, 3, 4, 5, 6];
function playUnstuck() {
  const map = play.map;
  if (!map) return;
  const mx = Math.floor(play.me.x), my = Math.floor(play.me.y);
  let pool = [];
  for (const ring of PLAY_UNSTUCK_RINGS) {
    const free = [], roads = [];
    for (let dy = -ring; dy <= ring; dy += 1) {
      for (let dx = -ring; dx <= ring; dx += 1) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) !== ring) continue;
        const cx = mx + dx + 0.5, cy = my + dy + 0.6;
        if (playBlocked(cx, cy) || [[1, 0], [-1, 0], [0, 1], [0, -1]].some(([ax, ay]) => playBlocked(cx + ax, cy + ay))) continue;
        free.push([cx, cy]);
        const [block, i] = playCell(map, mx + dx, my + dy);
        if (/^(road|dirt|cobble|bridge|asphalt|paving|pavement|caveSand)/.test(block.ground[i])) roads.push([cx, cy]);
      }
    }
    pool = roads.length ? roads : free;
    if (pool.length) break;
  }
  play.atEdge = false;
  if (!pool.length) return;
  const [x, y] = pool[Math.floor(Math.random() * pool.length)];
  play.me.x = x;
  play.me.y = y;
  play.me.jump = performance.now();            // a hop to land
  playSoundHop();
  playWhere();
  playKeepWhere();
  playRun();
  playDraw();
}

function playToggle() {
  if (play.on) playClose(); else playOpen();
}

/* Under the panel's head, down to its foot - over the chat and its box. */
function playPlace() {
  const head = document.querySelector("#notes-page .notes-head");
  const zoom = parseFloat(getComputedStyle(document.documentElement).zoom) || 1;
  play.el.style.top = `${Math.round((head ? head.getBoundingClientRect().bottom : 0) / zoom)}px`;
}

function playZoom(step) {
  if (step === 0) play.zoom = 0; else play.zoom += step;
  try { localStorage.setItem(PLAY_ZOOM_KEY, String(play.zoom)); } catch (_err) { /* this time only */ }
  playFit();
}

function playFit() {
  if (!play.canvas || play.el.hidden) return;
  const ratio = window.devicePixelRatio || 1;
  const box = play.canvas.getBoundingClientRect();
  play.canvas.width = Math.max(1, Math.round(box.width * ratio));
  play.canvas.height = Math.max(1, Math.round(box.height * ratio));
  // About fourteen tiles across at first, whole pixels each: crisp - then as zoomed.
  const base = Math.max(1, Math.round(play.canvas.width / (14 * PLAY_TILE)));
  play.zoom = Math.max(1 - base, Math.min(PLAY_ZOOM_MOST, play.zoom));
  play.scale = base + play.zoom;
  play.ctx.imageSmoothingEnabled = false;
  playDraw();
}

/* -- what it is made of ------------------------------------------------------------------------------------------ */
function playLoadSheets() {
  return Promise.all(Object.entries(PLAY_SHEETS).map(([name, url]) => play.sheets[name] || new Promise((done) => {
    const one = new Image();
    one.onload = () => { play.sheets[name] = one; done(); };
    one.onerror = () => done();
    one.src = url;
  })));
}

async function playLoadWorld() {
  let said = null;
  try { said = await api.get("/api/play/world"); } catch (err) { toast(err.message, "error"); return; }
  const fresh = JSON.stringify(said.world);
  // WHAT IT IS DRAWN FROM, ALWAYS SAID (the user, 2026-11-17: "你的world 要显示，是基于截止到XX日期前的activity画的"):
  // the day the world is drawn up to - and that more is being added, while it is.
  play.upTo = String((said.world && said.world.caught_up) || "").slice(0, 10);
  play.growing = Boolean(said.growing);
  playStatus();
  play.el.querySelector(".side-play-off").hidden = said.grows !== false;
  if (said.growing) setTimeout(() => { if (play.on) playLoadWorld().then(playDraw); }, 8000);
  if (play.world && JSON.stringify(play.world) === fresh) return;
  const first = !play.world;
  play.world = said.world;
  play.map = playLay(play.world);
  play.miniBase = null;                        // the minimap drawn again for this world
  play.zoneKinds = null;
  play.chunks.clear();
  if (first) {
    const home = play.world.zones[0];
    const at = playDoor(home);
    play.me.x = at[0] + 0.5;
    play.me.y = at[1] - 1.5;                 // just inside its door
    // WHERE IT WAS LEFT (the user, 2026-11-17: "每次重新打开agent, 要从上次退出的地方重生"): kept in this browser,
    // for this world - unless something stands there now.
    try {
      const kept = JSON.parse(localStorage.getItem(PLAY_WHERE_KEY) || "null");
      if (kept && kept.born === play.world.born && Number.isFinite(kept.x) && Number.isFinite(kept.y)
          && !playBlocked(kept.x, kept.y)) {
        play.me.x = kept.x;
        play.me.y = kept.y;
        play.me.facing = PLAY_ME_ROWS[kept.facing] !== undefined ? kept.facing : "down";
      }
    } catch (_err) { /* home's door, then */ }
    play.atEdge = false;
  }
}

/* -- the world laid into tiles ------------------------------------------------------------------------------------ */
function playDoor(zone) { return [zone.door || zone.x + 1 + Math.floor(zone.w / 2), zone.y + 3 + zone.h]; }
function playFloorOf(zone) {
  return PLAY_OUTDOOR[zone.kind] ? [zone.x, zone.y + 1, zone.w, zone.h] : [zone.x + 1, zone.y + 3, zone.w, zone.h];
}

/* playLay - the world laid into tiles, region by region: play-regions.js. */

/* -- drawn: blocks made once, kept --------------------------------------------------------------------------------- */
function playTile(context, piece, x, y) {
  const spec = playPieceSpec(piece);
  if (spec && spec.length === 1) { context.fillStyle = spec[0]; context.fillRect(x, y, 16, 16); return; }
  const sheet = spec && play.sheets[spec[0]];
  if (!sheet) return;
  context.drawImage(sheet, spec[1] * 17, spec[2] * 17, 16, 16, x, y, 16, 16);
}

function playChunk(cx, cy) {
  const key = `${cx},${cy}`;
  if (play.chunks.has(key)) return play.chunks.get(key);
  const block = playBlock(play.map, cx, cy);            // laid now, if it was not
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = PLAY_CHUNK * PLAY_TILE;
  const context = canvas.getContext("2d");
  context.imageSmoothingEnabled = false;
  const x0 = cx * PLAY_CHUNK, y0 = cy * PLAY_CHUNK, M = PLAY_LAY_MARGIN;
  for (let ty = 0; ty < PLAY_CHUNK; ty += 1) {
    for (let tx = 0; tx < PLAY_CHUNK; tx += 1) {
      const i = (ty + M) * block.W + (tx + M);
      const piece = block.ground[i];
      if (block.under[i] !== piece) playTile(context, block.under[i], tx * 16, ty * 16);   // under what is not whole
      playTile(context, piece, tx * 16, ty * 16);
      if (piece === "voidSpace") {                             // the dark of space, its stars
        const wx = x0 + tx, wy = y0 + ty;
        for (let s = 0; s < 3; s += 1) {
          if (playHash(wx, wy, 300 + s) < 0.22) {
            context.fillStyle = playHash(wx, wy, 310 + s) < 0.2 ? "#ffe9a8" : "#e8eefc";
            context.fillRect(tx * 16 + Math.floor(playHash(wx, wy, 320 + s) * 15), ty * 16 + Math.floor(playHash(wx, wy, 330 + s) * 15), 1, 1);
          }
        }
      }
    }
  }
  // A place's floor made its project's: its logo, a picture the agent drew, its name spelled large (playFloorArt).
  for (const zone of play.world.zones) playFloorArt(context, zone, x0, y0);
  // What stands about (the land's, the theme's, the landmark), then the light of each tile over all of it.
  for (const one of block.deco) {
    if (one.rustle) continue;                   // a bush: drawn each frame (playBushesDraw), to sway when touched
    const [sheet, col, row, sw, sh] = one.spec;
    const up = one.tall ? sh - 1 : 0;                          // a tall tree or lamp: drawn up from its foot
    // Whether it reaches this block counted from its top, not its foot: a tree standing on the next block's first row
    // has its crown here (the user, 2026-11-17: "这个树，是不是上面没有显示完整").
    if (one.x + sw <= x0 || one.x >= x0 + PLAY_CHUNK || one.y - up + sh <= y0 || one.y - up >= y0 + PLAY_CHUNK) continue;
    const image = play.sheets[sheet];
    const scaled = one.spec[5];                                // a giant one: one small tile drawn large
    if (image) context.drawImage(image, col * 17, row * 17, scaled ? 16 : sw * 17 - 1, scaled ? 16 : sh * 17 - 1,
                                 (one.x - x0) * 16, (one.y - up - y0) * 16, sw * 16, sh * 16);
  }
  playLight(context, block);
  // The things in the rooms, top to bottom - in their own colours (the user's pictures among them).
  const items = play.world.items.filter((one) => {
    const [iw, ih] = PLAY_SIZES[one.kind] || [1, 1];
    return one.x + iw > x0 && one.x < x0 + PLAY_CHUNK && one.y + ih > y0 && one.y < y0 + PLAY_CHUNK;
  }).sort((a, b) => (PLAY_UNDER_KINDS.has(b.kind) - PLAY_UNDER_KINDS.has(a.kind)) || a.y - b.y);
  for (const item of items) playThing(context, item, (item.x - x0) * 16, (item.y - y0) * 16);
  play.chunks.set(key, canvas);
  // Kept within reason: the blocks longest out of sight let go of.
  if (play.chunks.size > 48) play.chunks.delete(play.chunks.keys().next().value);
  return canvas;
}

function playThing(context, item, x, y) {
  if (item.kind === "rug") { playRug(context, item, x, y); return; }
  if (item.pixels) { playPixels(context, item, x, y); return; }
  if (item.kind === "statue_of" || item.kind === "banner" || item.kind === "poster") { playPictureThing(context, item, x, y); return; }
  const looks = PLAY_LOOKS[item.kind];
  let spec = looks && looks.length > 1
    ? looks[Math.floor(playHash(Number(String(item.id).replace(/\D/g, "")) || 0, 7, 31) * looks.length)]
    : PLAY_ITEMS[item.kind];
  // A thing of the catalogue (agent/playcatalogue.py): its picture is the scenery's of that name, scaled if giant.
  if (!spec && PLAY_SCENERY[item.kind]) {
    const s = PLAY_SCENERY[item.kind], sheet = play.sheets[s[0]], w = s[3] || 1, h = s[4] || 1;
    if (sheet) context.drawImage(sheet, s[1] * 17, s[2] * 17, s[5] ? 16 : w * 17 - 1, s[5] ? 16 : h * 17 - 1, x, y, w * 16, h * 16);
    return;
  }
  if (spec) {
    const sheet = play.sheets[spec[0]];
    if (sheet) context.drawImage(sheet, spec[1] * 17, spec[2] * 17, spec[3] * 17 - 1, spec[4] * 17 - 1, x, y, spec[3] * 16, spec[4] * 16);
    return;
  }
  // A FRAME: the user's picture in a wooden frame, small - its pixels as large as the world's.
  context.fillStyle = "#5a3a1e";
  context.fillRect(x + 2, y + 3, 28, 26);
  context.fillStyle = "#a8743c";
  context.fillRect(x + 3, y + 4, 26, 24);
  const picture = item.picture_url && play.pictures.get(item.picture_url);
  if (picture && picture.complete && picture.naturalWidth) {
    const side = Math.min(picture.naturalWidth, picture.naturalHeight);
    context.drawImage(picture, (picture.naturalWidth - side) / 2, (picture.naturalHeight - side) / 2, side, side, x + 5, y + 6, 22, 20);
  } else {
    context.fillStyle = "#d9c9a8";
    context.fillRect(x + 5, y + 6, 22, 20);
    if (item.picture_url && !play.pictures.has(item.picture_url)) {
      const one = new Image();
      play.pictures.set(item.picture_url, one);
      one.onload = () => { play.chunks.clear(); playDraw(); };     // drawn again with it
      one.src = item.picture_url;
    }
  }
}

/* A THING THE AGENT DREW (agent/playworld.py drawn_pixels): its pixels, a world's pixel each, in its tiles. */
function playPixels(context, item, x, y) {
  const [w, h] = PLAY_SIZES[item.kind] || [1, 1];
  const rows = item.pixels.rows, cols = Math.max(1, ...rows.map((r) => r.length));
  const cell = Math.max(1, Math.floor(Math.min((w * 16) / cols, (h * 16) / rows.length)));
  const ox = x + Math.floor((w * 16 - cols * cell) / 2), oy = y + (h * 16 - rows.length * cell);
  rows.forEach((row, ry) => [...row].forEach((ch, rx) => {
    if (ch === ".") return;
    const colour = item.pixels.palette[Number(ch)];
    if (!colour) return;
    context.fillStyle = colour;
    context.fillRect(ox + rx * cell, oy + ry * cell, cell, cell);
  }));
}

/* ONE OF ITS PICTURES MADE A THING: a statue - the picture made small and stony on a pedestal; a banner - hanging cloth
   with it; a poster - on the wall, framed thin. */
function playPictureThing(context, item, x, y) {
  const picture = item.picture_url && play.pictures.get(item.picture_url);
  const ready = picture && picture.complete && picture.naturalWidth;
  if (!ready && item.picture_url && !play.pictures.has(item.picture_url)) {
    const one = new Image();
    play.pictures.set(item.picture_url, one);
    one.onload = () => { play.chunks.clear(); playDraw(); };
    one.src = item.picture_url;
  }
  const lay = (dx, dy, dw, dh, filter) => {
    if (!ready) { context.fillStyle = "#cbbfa8"; context.fillRect(dx, dy, dw, dh); return; }
    const small = document.createElement("canvas");
    small.width = Math.max(1, Math.round(dw / 2)); small.height = Math.max(1, Math.round(dh / 2));
    const s = small.getContext("2d");
    if (filter) s.filter = filter;
    const side = Math.min(picture.naturalWidth, picture.naturalHeight);
    s.drawImage(picture, (picture.naturalWidth - side) / 2, (picture.naturalHeight - side) / 2, side, side, 0, 0, small.width, small.height);
    context.imageSmoothingEnabled = false;
    context.drawImage(small, dx, dy, dw, dh);
  };
  if (item.kind === "statue_of") {
    context.fillStyle = "#7d7f86"; context.fillRect(x + 2, y + 24, 12, 8);          // its pedestal
    context.fillStyle = "#a3a6ad"; context.fillRect(x + 1, y + 22, 14, 3);
    lay(x + 2, y + 4, 12, 18, "grayscale(0.7) contrast(1.2) brightness(1.1)");
    context.fillStyle = "rgba(255,255,255,0.18)"; context.fillRect(x + 2, y + 4, 4, 18);
  } else if (item.kind === "banner") {
    context.fillStyle = "#5a3a1e"; context.fillRect(x, y + 1, 16, 2);                // its rod
    context.fillStyle = "#8c2f39"; context.fillRect(x + 2, y + 3, 12, 25);
    lay(x + 3, y + 5, 10, 14, "");
    context.fillStyle = "#8c2f39"; context.beginPath(); context.moveTo(x + 2, y + 28); context.lineTo(x + 8, y + 31);
    context.lineTo(x + 14, y + 28); context.fill();
  } else {
    context.fillStyle = "#e9e4d8"; context.fillRect(x + 1, y + 2, 30, 28);
    lay(x + 3, y + 4, 26, 24, "");
  }
}

/* A RUG, drawn here: a border, a field, a pattern down its middle - its colours and pattern its own. Walked over. */
function playRug(context, item, x, y) {
  const n = Number(String(item.id).replace(/\D/g, "")) || 0;
  const [field, trim] = PLAY_RUG_INKS[Math.floor(playHash(n, 3, 37) * PLAY_RUG_INKS.length)];
  const w = 48, h = 32;
  context.fillStyle = trim;
  context.fillRect(x + 2, y + 3, w - 4, h - 6);
  context.fillStyle = field;
  context.fillRect(x + 4, y + 5, w - 8, h - 10);
  context.fillStyle = trim;
  const kind = Math.floor(playHash(n, 5, 41) * 3);
  if (kind === 0) for (let i = 0; i < 5; i += 1) context.fillRect(x + 9 + i * 7, y + 14, 3, 3);            // a row of dots
  else if (kind === 1) { context.fillRect(x + 8, y + 9, w - 16, 1); context.fillRect(x + 8, y + h - 10, w - 16, 1); }  // bands
  else { context.fillRect(x + 22, y + 9, 4, 4); context.fillRect(x + 18, y + 13, 12, 4); context.fillRect(x + 22, y + 17, 4, 4); }
  for (let i = 0; i < 8; i += 1) { context.fillRect(x + 4 + i * 5, y + 1, 1, 2); context.fillRect(x + 4 + i * 5, y + h - 3, 1, 2); }
}

function playDraw() {
  if (!play.on || !play.map || !play.ctx) return;
  const { ctx, canvas, scale } = play;
  const size = PLAY_TILE * scale;
  // The agent in the middle.
  play.camera.x = play.me.x * size - canvas.width / 2;
  play.camera.y = play.me.y * size - canvas.height / 2;
  ctx.fillStyle = "#1b1d22";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  // Only the blocks in sight - each laid and drawn the first time it comes into sight.
  const chunkSize = PLAY_CHUNK * size;
  const c0 = Math.floor(play.camera.x / chunkSize), c1 = Math.floor((play.camera.x + canvas.width) / chunkSize);
  const r0 = Math.floor(play.camera.y / chunkSize), r1 = Math.floor((play.camera.y + canvas.height) / chunkSize);
  for (let cy = r0; cy <= r1; cy += 1) {
    for (let cx = c0; cx <= c1; cx += 1) {
      ctx.drawImage(playChunk(cx, cy), Math.round(cx * chunkSize - play.camera.x), Math.round(cy * chunkSize - play.camera.y),
                    chunkSize, chunkSize);
    }
  }
  playBushesDraw(size, c0, c1, r0, r1, false);
  playCoastersDraw(size);
  playFacadesDraw(size);
  if (typeof playActDraw === "function") playActDraw(size);
  playSignsDraw(size);
  // The watermelon, the way it goes; a step's frames while it walks, standing when not.
  const sheet = play.sheets.me;
  if (sheet && !play.ride) {
    const walking = play.keys.size > 0;
    const step = walking ? Math.floor(play.me.step * PLAY_STEPS_A_SECOND) % 4 : 0;
    const px = Math.round(play.me.x * size - play.camera.x - size / 2);
    const py = Math.round(play.me.y * size - play.camera.y - size + scale);
    const now = performance.now();
    play.dust = play.dust.filter((one) => now - one.at < PLAY_DUST_MS);
    for (const one of play.dust) {
      const age = (now - one.at) / PLAY_DUST_MS;
      const side = Math.max(1, Math.round(scale * (2.6 - age * 1.4)));
      ctx.fillStyle = `rgba(214, 190, 150, ${(0.75 * (1 - age)).toFixed(2)})`;
      ctx.fillRect(Math.round(one.x * size - play.camera.x + one.drift * age * size * 0.6 - side / 2),
                   Math.round(one.y * size - play.camera.y - age * size * 0.35 - side), side, side);
    }
    // A JUMP: up and down in a curve, its shadow left on the ground, smaller while it is up.
    const up = playJumpHeight();
    const shadow = 1 - up * 0.5;
    ctx.fillStyle = "rgba(0, 0, 0, 0.22)";
    ctx.beginPath();
    ctx.ellipse(px + size / 2, py + size - scale, size * 0.3 * shadow, size * 0.09 * shadow, 0, 0, Math.PI * 2);
    ctx.fill();
    if (play.swimming) {
      // In the water: its top half, bobbing; rings spreading round it.
      const bob = Math.round(Math.sin(now / 300) * scale);
      ctx.drawImage(sheet, step * 16, (PLAY_ME_ROWS[play.me.facing] || 0) * 16, 16, 9, px, py + bob + Math.round(size * 0.25), size, size * 9 / 16);
      for (let k = 0; k < 2; k += 1) {
        const u = ((now / 1200) + k / 2) % 1;
        ctx.strokeStyle = `rgba(255, 255, 255, ${(0.7 * (1 - u)).toFixed(2)})`;
        ctx.lineWidth = Math.max(1, scale * 0.6);
        ctx.beginPath(); ctx.ellipse(px + size / 2, py + size * 0.82, size * (0.35 + u * 0.4), size * (0.1 + u * 0.12), 0, 0, Math.PI * 2); ctx.stroke();
      }
    } else if (play.sit) {
      // Sitting: a little lower, a little squat.
      ctx.drawImage(sheet, 0, (PLAY_ME_ROWS[play.me.facing] || 0) * 16, 16, 16, px, py + Math.round(size * 0.12), size, Math.round(size * 0.88));
    } else {
      ctx.drawImage(sheet, (up > 0 ? 1 : step) * 16, (PLAY_ME_ROWS[play.me.facing] || 0) * 16, 16, 16, px,
                    py - Math.round(up * size * 0.7), size, size);
    }
    play.saidAnchor = [px + size / 2, py - Math.round(up * size * 0.7)];
  }
  playBushesDraw(size, c0, c1, r0, r1, true);   // the bush it stands in, over its feet
  if (typeof playActOver === "function") playActOver(size);
  // The sky over it all (play-sky.js); then what is said and the sign, above the sky - read at night as by day.
  playSkyDraw(ctx, canvas);
  playMiniDraw();
  if (play.saidAnchor) playSaidDraw(...play.saidAnchor);
  playBannerDraw();
}

/* SIGNS (the user, 2026-11-17: "你的房间要加上门牌（代表日期，主题什么的），外场建筑，地标性建筑物也要有牌子"): a small
   wooden board by every door - the place's name, the day it was made and its style or kind; at the foot of every open
   place; and at every landmark, with its region's name. Drawn over the ground, crisp at every zoom, only in sight. */
const PLAY_LANDMARK_NAMES = { fountain: "Fountain", statue: "Statue", great_tree: "Great Tree", camp: "Camp", station: "Station",
                              stones: "Standing Stones", pond: "Pond", market: "Market", crystals: "Crystals", giant_pet: "Giant Pet",
                              rocket: "Rocket", ancient_ruins: "Ancient Ruins", mausoleum: "Mausoleum", fair: "Fairground",
                              greenhouse: "Greenhouse", christmas_tree: "Christmas Tree", lantern_gate: "Lantern Gate",
                              giant_heart: "Giant Heart", skyline: "Skyline" };
/* WHEN A PLACE WAS LIVED (the user, 2026-11-17: "日期是这个project是什么时候的"): its own days - a task's, or the days
   drawn - else the days of the growth that made it; its building date apart, in its details only. */
function playLived(zone) {
  if (zone.days && zone.days[0]) return zone.days;
  const growth = (play.world.growths || []).find((one) => one.at === zone.made && one.days);
  return growth ? growth.days : null;
}
function playShort(text, most) {
  text = String(text || "").trim();
  if (text.length <= most) return text;
  const cut = text.slice(0, most);
  const space = cut.lastIndexOf(" ");
  return `${(space > most * 0.5 ? cut.slice(0, space) : cut).replace(/[\s,.;:·-]+$/, "")}…`;
}
/* A land's days: from the first lived of its places to the last; none lived, the days of the growth that opened it. */
function playRegionLived(region) {
  const all = play.world.zones.filter((one) => one.region === region.id).map(playLived).filter(Boolean);
  if (all.length) return [all.map((d) => d[0]).sort()[0], all.map((d) => d[1] || d[0]).sort().slice(-1)[0]];
  const growth = (play.world.growths || []).find((one) => one.at === region.made && one.days);
  return growth ? growth.days : null;
}
function playPeriod(days) {
  if (!days) return "";
  const [a, b] = days;
  if (!b || a === b) return a;
  return `${a} – ${a.slice(0, 4) === b.slice(0, 4) ? b.slice(5) : b}`;
}

function playSigns() {
  const index = play.map;
  if (!index || !index.zones) return [];
  if (index.signs) return index.signs;
  const signs = [];
  for (const one of index.zones) {
    const zone = one.zone;
    const at = one.garden ? [zone.x + zone.w / 2, zone.y + 1 + zone.h + 0.9] : [one.door + PLAY_DOOR_W + 1.6, zone.y + 3 + zone.h + 3.2];
    const label = (one) => playWords((one[0].toUpperCase() + one.slice(1)).replace(/_/g, " "));   // "Sea tunnel"
    const kind = zone.style ? label(zone.style) : label(zone.kind);
    // A task's place: its own name always on its sign (the user: a project's room had no sign with the project's name).
    const title = zone.task_title && !String(zone.name || "").toLowerCase().includes(zone.task_title.toLowerCase()) ? zone.task_title : "";
    const lived = playPeriod(playLived(zone));
    signs.push({ at, name: zone.name || zone.kind, under: [lived, kind].filter(Boolean).join(" · "), zone, kind, title });
  }
  const life = index.life || { posts: [], order: [] };
  for (const post of life.posts) {
    const to = post.to.zone, n = life.order.indexOf(post.to) + 1;
    signs.push({ at: [post.x + 0.5, post.y + 0.9], post, zone: to, name: post.start ? playWords("Start of your story") : to.name || to.kind,
                 under: post.start ? playPeriod(playLived(to)) : `${n} / ${life.order.length} · ${playPeriod(playLived(to))}` });
  }
  for (const mark of index.landmarks || []) {
    signs.push({ at: [mark.cx + 0.5, mark.square[3] - 0.2], name: playWords(PLAY_LANDMARK_NAMES[mark.region.landmark] || mark.region.landmark),
                 under: mark.region.name || "", region: mark.region });
  }
  index.signs = signs;
  return signs;
}

function playPostDraw(sign, x, y, size, fontPx) {
  const { ctx } = play;
  const post = sign.post, u = Math.max(2, Math.round(play.scale * 0.5));
  const px = Math.round(fontPx * 0.72);
  ctx.save();
  ctx.font = `bold ${px}px "Microsoft YaHei", "PingFang SC", system-ui, sans-serif`;
  let name = sign.name;
  const most = px * 9;
  if (ctx.measureText(name).width > most) {
    while (name.length > 1 && ctx.measureText(`${name}…`).width > most) name = name.slice(0, -1);
    name = `${name.trimEnd()}…`;
  }
  const textW = ctx.measureText(name).width, pad = Math.round(px * 0.4);
  const h = Math.round(px * 1.5), tip = Math.round(h * 0.55);
  const w = textW + pad * 2;
  const foot = y, top = foot - Math.round(size * 1.35);
  ctx.fillStyle = "#5c3317";                                     // its post
  ctx.fillRect(x - u, top, u * 2, foot - top);
  // The board: pointing left or right the way the road goes; up or down, a small arrow on it instead.
  const left = post.dx < 0;
  const bx = left ? x - w + tip / 2 : x - tip / 2, by = top - Math.round(h * 0.2);
  ctx.beginPath();
  if (left) { ctx.moveTo(bx - tip, by + h / 2); ctx.lineTo(bx, by); ctx.lineTo(bx + w, by); ctx.lineTo(bx + w, by + h); ctx.lineTo(bx, by + h); }
  else { ctx.moveTo(bx, by); ctx.lineTo(bx + w, by); ctx.lineTo(bx + w + tip, by + h / 2); ctx.lineTo(bx + w, by + h); ctx.lineTo(bx, by + h); }
  ctx.closePath();
  ctx.fillStyle = post.start ? "#d8a24a" : "#c98f4e";
  ctx.fill();
  ctx.lineWidth = u; ctx.strokeStyle = "#3b2414"; ctx.stroke();
  ctx.fillStyle = "#2a170b";
  ctx.textBaseline = "middle";
  let text = name;
  if (post.dy < 0) text = `↑ ${name}`; else if (post.dy > 0) text = `↓ ${name}`;
  ctx.fillText(text, bx + pad - (post.dy ? 0 : 0), by + h / 2 + 1);
  ctx.restore();
}

/* SIGNS IN STYLES (the user, 2026-11-17: "那个牌匾（标志）也有几种style"): a place's own, said by the agent ("sign"); else
   by its building's style or its land - gilt for a mansion, metal for the future, a scroll for magic, stone for ruins,
   red and gold for the New Year, pink for Valentine's, neon at a fair; wood the rest. */
const PLAY_SIGN_STYLES = {
  wood: { post: "#5c3317", edge: "#3b2414", face: "#c98f4e", light: "#e8bf86", ink: "#2a170b", under: "#4a2c14", shape: "rect" },
  stone: { post: "#6b6760", edge: "#3e3c38", face: "#a8a39a", light: "#cdc8be", ink: "#26241f", under: "#3c3934", shape: "arch" },
  metal: { post: "#4a5160", edge: "#1f2430", face: "#3a4252", light: "#6fe3ff", ink: "#e8f6ff", under: "#9fd8ee", shape: "rect", glow: "rgba(111, 227, 255, 0.6)" },
  scroll: { post: "#5c3317", edge: "#6b4a2a", face: "#efe0bb", light: "#fff4d6", ink: "#3b2a14", under: "#5e4628", shape: "scroll" },
  neon: { post: "#333", edge: "#141418", face: "#22202a", light: "#ff5fb0", ink: "#ffe36e", under: "#ff9ed0", shape: "round", glow: "rgba(255, 95, 176, 0.75)" },
  gold: { post: "#5c3317", edge: "#7a5410", face: "#3a2412", light: "#f2c94c", ink: "#f2c94c", under: "#e9d49a", shape: "rect" },
  red: { post: "#5c1a14", edge: "#7a5410", face: "#b8242c", light: "#f2c94c", ink: "#f8d66a", under: "#ffe9b0", shape: "rect" },
  pink: { post: "#b0577f", edge: "#a23e6e", face: "#ffc6dc", light: "#fff0f6", ink: "#7a1f4a", under: "#9a3f68", shape: "round" },
};
const PLAY_SIGN_BY_STYLE = { mansion: "gold", classic: "gold", futuristic: "metal", industrial: "metal", modern: "metal",
                             magic: "scroll", zen: "scroll", ruins: "stone", haunted: "stone", toy: "neon", sky: "pink" };
const PLAY_SIGN_BY_LAND = { newyear: "red", valentine: "pink", funfair: "neon", city: "metal", scifi: "metal", space: "metal",
                            ruins: "stone", graveyard: "stone", halloween: "stone", enchanted: "scroll", skycity: "gold",
                            underwater: "metal" };
function playSignStyle(sign) {
  const zone = sign.zone, region = (zone && play.map && play.map.byId && play.map.regions[play.map.byId[zone.region]]) || sign.region;
  return (zone && zone.sign) || (zone && PLAY_SIGN_BY_STYLE[zone.style]) || (region && PLAY_SIGN_BY_LAND[region.biome]) || "wood";
}
function playSignPlate(ctx, look, left, top, w, h, u, x, fontPx) {
  ctx.fillStyle = look.post;                                     // its post
  ctx.fillRect(x - u, top + h, u * 2, Math.round(fontPx * 0.6));
  const r = look.shape === "round" ? Math.min(h / 2, fontPx * 0.5) : 0;
  const path = (grow) => {
    const L = left - grow, T = top - grow, W = w + 2 * grow, H = h + 2 * grow;
    ctx.beginPath();
    if (look.shape === "arch") { ctx.moveTo(L, T + H); ctx.lineTo(L, T + H * 0.35); ctx.quadraticCurveTo(L + W / 2, T - H * 0.35, L + W, T + H * 0.35); ctx.lineTo(L + W, T + H); ctx.closePath(); }
    else if (r) { ctx.roundRect ? ctx.roundRect(L, T, W, H, r + grow) : ctx.rect(L, T, W, H); }
    else ctx.rect(L, T, W, H);
  };
  ctx.fillStyle = look.edge; path(u); ctx.fill();
  ctx.fillStyle = look.face; path(0); ctx.fill();
  if (look.shape === "scroll") {                                 // its rolled ends
    ctx.fillStyle = look.edge;
    ctx.fillRect(left - u * 2.5, top - u, u * 2, h + 2 * u);
    ctx.fillRect(left + w + u * 0.5, top - u, u * 2, h + 2 * u);
  }
  ctx.fillStyle = look.light;
  if (look.shape === "rect" || look.shape === "scroll") ctx.fillRect(left, top, w, u);
  if (look.glow) { ctx.strokeStyle = look.light; ctx.lineWidth = Math.max(1, u * 0.6); path(-u * 0.6); ctx.stroke(); }
}

function playSignsDraw(size) {
  const signs = playSigns();
  if (!signs.length) return;
  const { ctx, canvas } = play;
  const fontPx = Math.max(14, Math.round(play.scale * 4.2));    // larger (the user, 2026-11-17: "门牌标志字体大一点")
  for (let sign of signs) {
    const x = Math.round(sign.at[0] * size - play.camera.x), y = Math.round(sign.at[1] * size - play.camera.y);
    if (x < -200 || x > canvas.width + 200 || y < -60 || y > canvas.height + 60) continue;
    if (sign.post) { playPostDraw(sign, x, y, size, fontPx); continue; }
    ctx.save();
    ctx.font = `bold ${fontPx}px "Microsoft YaHei", "PingFang SC", system-ui, sans-serif`;
    const most = fontPx * 13;                                    // a sign's width at the most
    if (ctx.measureText(sign.name).width > most) {
      let cut = sign.name;
      while (cut.length > 1 && ctx.measureText(`${cut}…`).width > most) cut = cut.slice(0, -1);
      sign = { ...sign, name: `${cut.trimEnd()}…` };
    }
    const nameW = ctx.measureText(sign.name).width;
    ctx.font = `${Math.round(fontPx * 0.8)}px "Microsoft YaHei", "PingFang SC", system-ui, sans-serif`;
    const underW = sign.under ? ctx.measureText(sign.under).width : 0;
    const pad = Math.round(fontPx * 0.45), w = Math.max(nameW, underW) + pad * 2;
    const h = Math.round(fontPx * (sign.under ? 2.3 : 1.4)) + pad;
    const left = Math.round(x - w / 2), top = y - h - Math.round(fontPx * 0.6);
    const u = Math.max(2, Math.round(play.scale * 0.6));
    const look = PLAY_SIGN_STYLES[playSignStyle(sign)] || PLAY_SIGN_STYLES.wood;
    playSignPlate(ctx, look, left, top, w, h, u, x, fontPx);
    ctx.textBaseline = "top";
    ctx.font = `bold ${fontPx}px "Microsoft YaHei", "PingFang SC", system-ui, sans-serif`;
    if (look.glow) { ctx.shadowColor = look.glow; ctx.shadowBlur = fontPx * 0.6; }
    ctx.fillStyle = look.ink;
    ctx.fillText(sign.name, left + (w - nameW) / 2, top + pad * 0.8);
    if (sign.under) {
      ctx.font = `${Math.round(fontPx * 0.8)}px "Microsoft YaHei", "PingFang SC", system-ui, sans-serif`;
      ctx.fillStyle = look.under;
      ctx.fillText(sign.under, left + (w - underW) / 2, top + pad * 0.8 + fontPx * 1.15);
    }
    ctx.shadowBlur = 0;
    ctx.restore();
  }
}

/* -- a ROOM'S NAME, LARGE, on a wooden sign (the user: "到一个不同的区域要大屏幕显示名字 ... 和星露谷物语的style相似"): its
   words written small and drawn larger without smoothing - pixels as the world's; shown, then gone. -- */
const PLAY_BANNER_MS = 2600;
function playBannerDraw() {
  const banner = play.banner;
  if (!banner) return;
  const age = performance.now() - banner.at;
  if (age > PLAY_BANNER_MS) { play.banner = null; return; }
  const fade = Math.min(1, age / 250, (PLAY_BANNER_MS - age) / 400);
  const { ctx, canvas } = play;
  const unit = Math.max(2, Math.round(play.scale * 0.8));
  if (!banner.words) {
    const small = document.createElement("canvas");
    const write = small.getContext("2d");
    const font = `bold 13px "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", system-ui, sans-serif`;
    write.font = font;
    const wide = Math.ceil(write.measureText(banner.name).width) + 2;
    const under = banner.since ? Math.ceil((write.font = `bold 10px Consolas, "Courier New", monospace`, write.measureText(banner.since).width)) : 0;
    small.width = Math.max(wide, under) + 2;
    small.height = banner.since ? 31 : 18;
    write.font = font;
    write.textBaseline = "top";
    write.fillStyle = "#3b2414";
    write.fillText(banner.name, Math.round((small.width - wide) / 2) + 1, 2);
    if (banner.since) {
      write.font = `bold 10px Consolas, "Courier New", monospace`;
      write.fillStyle = "#6b4a2a";
      write.fillText(banner.since, Math.round((small.width - under) / 2), 19);
    }
    // Hard edges: every half-lit pixel made whole or nothing, as a pixel font's.
    const data = write.getImageData(0, 0, small.width, small.height);
    for (let i = 3; i < data.data.length; i += 4) data.data[i] = data.data[i] > 80 ? 255 : 0;
    write.putImageData(data, 0, 0);
    banner.words = small;
  }
  const words = banner.words;
  const w = (words.width + 14) * unit, h = (words.height + 10) * unit;
  const x = Math.round((canvas.width - w) / 2), y = Math.round(canvas.height * 0.16);
  ctx.save();
  ctx.globalAlpha = fade;
  // The sign: a dark edge, a lighter wood rim, the board - square corners notched, as pixel signs are.
  const rect = (fill, inset) => {
    ctx.fillStyle = fill;
    ctx.fillRect(x + inset * unit + unit, y + inset * unit, w - 2 * inset * unit - 2 * unit, h - 2 * inset * unit);
    ctx.fillRect(x + inset * unit, y + inset * unit + unit, w - 2 * inset * unit, h - 2 * inset * unit - 2 * unit);
  };
  rect("#3b2414", 0);
  rect("#c27a3a", 1);
  rect("#8a4f24", 2);
  rect("#f3d7a4", 3);
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(words, x + 7 * unit, y + 5 * unit, words.width * unit, words.height * unit);
  ctx.restore();
}

/* A region's tune outdoors: its own if the agent wrote one, else its land's. Its other sounds come from its things. */
function playRegionSound(region) {
  if (!region) return null;
  return { id: `region-${region.id}`, kind: region.land.tune || "town", music: region.music };
}

/* Words the page's language has for a text of its own (the starting village's name, the hints). */
function playWords(text) {
  try { return (window.interfaceLanguage && window.interfaceLanguage.lookUp(text)) || text; } catch (_err) { return text; }
}

/* WHAT IS HEARD FROM WHERE THE WALKER IS (play-sound.js playSoundHear): each sound from the things near that make it -
   the nearest of each, fading out by its reach; through a wall (in a room and it outside, or the other way) little. */
const PLAY_ITEM_SOUND = { fireplace: "fire", stove: "fire", fountain: "water", candelabra: "", wall_torch: "fire" };
const PLAY_REACH = { fire: 6, water: 7, birds: 8, crowd: 9 };
function playHeard() {
  const out = { water: 0, fire: 0, birds: 0, crowd: 0 };
  const map = play.map;
  if (!map || !play.world) return out;
  const mx = play.me.x, my = play.me.y, here = play.zone ? play.zone.id : "";
  const hear = (sound, x, y, room) => {
    const d = Math.hypot(x - mx, y - my), reach = PLAY_REACH[sound];
    if (d >= reach) return;
    const through = (room || "") === here ? 1 : 0.15;
    out[sound] = Math.max(out[sound], (1 - d / reach) ** 2 * through);
  };
  for (const item of play.world.items) {
    const sound = PLAY_ITEM_SOUND[item.kind];
    if (sound) hear(sound, item.x + 0.5, item.y + 0.5, item.zone);
  }
  const bx = Math.floor(mx / PLAY_CHUNK), by = Math.floor(my / PLAY_CHUNK);
  for (let cy = by - 1; cy <= by + 1; cy += 1) {
    for (let cx = bx - 1; cx <= bx + 1; cx += 1) {
      for (const one of playBlock(map, cx, cy).deco) if (one.sound) hear(one.sound, one.x + one.spec[3] / 2, one.y + one.spec[4] / 2, "");
    }
  }
  // Water as it lies - the nearest of it within reach.
  const reach = PLAY_REACH.water;
  for (let y = Math.floor(my - reach); y <= my + reach; y += 1) {
    for (let x = Math.floor(mx - reach); x <= mx + reach; x += 1) {
      const [block, i] = playCell(map, x, y);
      if (block.ground[i].startsWith("water")) hear("water", x + 0.5, y + 0.5, "");
    }
  }
  return out;
}

/* THE STATUS BAR (the user, 2026-11-17: "你的world 要显示，是基于截止到XX日期前的activity画的"; "要有一个状态栏，显示当前
   季节（季节是根据日程发生的季节），然后阴晴圆缺，天气"): what the world is drawn from, up to which day; then the
   game's own: its moon, the weather where the walker is, and its hour. */
const PLAY_MOONS = [["New moon", "🌑"], ["Waxing crescent", "🌒"], ["First quarter", "🌓"], ["Waxing gibbous", "🌔"],
                    ["Full moon", "🌕"], ["Waning gibbous", "🌖"], ["Last quarter", "🌗"], ["Waning crescent", "🌘"]];
const PLAY_WEATHER_MARKS = { fair: ["Fair", "☀"], cloudy: ["Cloudy", "☁"], rain: ["Rain", "🌧"], snow: ["Snow", "❄"], fog: ["Fog", "🌫"] };
function playStatus() {
  const note = play.el && play.el.querySelector(".side-play-growing");
  if (!note) return;
  const lines = [];
  if (play.upTo) {
    lines.push([playWords("Drawn from your activity up to {0}").replace("{0}", play.upTo),
                play.growing ? playWords("adding more...") : ""].filter(Boolean).join(" · "));
  }
  // IN THE GAME NO DATE, NO SEASON (the user, 2026-11-17: "游戏里是没有日期的，季节什么也不会变化，只有天气和白天晚上（并且
  // 游戏里时间是一直会走的，程序没开也会走)"): the moon by the game's days, the weather, and the game's hour - which runs
  // on whether the application is open or not.
  const region = play.region;
  const [moonName, moonMark] = PLAY_MOONS[Math.round(playMoonPhase() * 8) % 8];
  const parts = [`${moonMark} ${playWords(moonName)}`];
  if (region) {
    const [weather, mark] = PLAY_WEATHER_MARKS[playSkyWeather(region)] || PLAY_WEATHER_MARKS.fair;
    const { light, warm } = playSkyDaylight(region);
    const clock = playGameTime();
    const hour = light < 0.2 ? "Night" : warm > 0.3 ? (clock.hour < 12 ? "Dawn" : "Dusk") : "Daytime";
    const hh = String(Math.floor(clock.hour)).padStart(2, "0"), mm = String(Math.floor((clock.hour % 1) * 60)).padStart(2, "0");
    parts.push(`${mark} ${playWords(weather)}`, `${playWords(hour)} ${hh}:${mm}`);
  }
  lines.push(parts.join(" · "));
  note.innerHTML = lines.map((one) => `<span>${escapeHtml(one)}</span>`).join("<br>");
  note.hidden = false;
}
setInterval(() => { if (play.on) playStatus(); }, 5000);           // the game's clock moves a minute a second

/* A MINIMAP OF THE WHOLE WORLD (the user, 2026-11-17: "要有一个小地图，代表整个区域"): its land by its region's colour,
   its paths, its places and landmarks, dark where nothing is (as the world); the walker a red dot, what is in view a
   white frame. Its picture made once a world; each frame only the dot and the frame. M, or a press, hides it. */
const PLAY_MINI_LAND = { meadow: "#6aa84f", autumn: "#c9733a", enchanted: "#9b5de5", desert: "#d9bb72", seaside: "#e2d29c",
                         forest: "#3f7d3a", highland: "#9aa0a6", snow: "#e8eef5", city: "#9aa3ad", farmland: "#8fbf4f",
                         cavern: "#7a6046", lakeside: "#5fa86b", toytown: "#8fd16a", underwater: "#2f6f9f", skycity: "#dfeefb",
                         space: "#4a4d5c", scifi: "#7f8ea3", zoo: "#7cb85a", ruins: "#c9b48a", graveyard: "#4c5a46",
                         christmas: "#eef3f8", newyear: "#7fbf5a", halloween: "#7a6a3a", valentine: "#9fd36a", funfair: "#c8c0b0",
                         botanical: "#5fb04a", primeval: "#2f5a2c" };
function playMiniToggle() {
  if (!play.mini) return;
  play.mini.hidden = !play.mini.hidden;
  try { localStorage.setItem("playMini", play.mini.hidden ? "off" : "on"); } catch (_err) { /* this time */ }
  playDraw();
}
function playMiniLarger() {
  if (!play.mini) return;
  const large = !play.mini.classList.contains("large");
  play.mini.classList.toggle("large", large);
  try { localStorage.setItem("playMiniLarge", large ? "on" : "off"); } catch (_err) { /* this time */ }
}
/* THE MINIMAP, THE WORLD ITSELF SEEN FROM HIGH UP (the user, 2026-11-17: "小地图上还要包括具体的景物，而不只是底色（我要从
   小地图看整个地图长什么样，有个美观的效果）"): each block drawn as the walk draws it - its ground, its houses and floors,
   trees, bushes, water, roads and signposts, the things in the yards - shrunk to PLAY_MINI_PX pixels a tile; dark
   where nothing is. Block by block, nearest the walker first, a few at a time; KEPT (the user: "是的小地图可以存起来")
   as a small picture a block with what made it - the regions, the places, landmarks and roads that reach it - and
   drawn again only when that changed. */
const PLAY_MINI_DARK = "#15161a", PLAY_MINI_PX = 4;
const PLAY_MINI_KEPT_VERSION = 2, PLAY_MINI_KEPT_MOST = 300, PLAY_MINI_KEPT_KEY = "playMiniPictures";
function playMiniKept() {
  if (play.miniKept) return play.miniKept;
  play.miniKept = new Map();
  try {
    localStorage.removeItem("playMiniKept");                                // the colour-only one before
    const said = JSON.parse(localStorage.getItem(PLAY_MINI_KEPT_KEY) || "null");
    if (said && said.v === PLAY_MINI_KEPT_VERSION) for (const [key, sig, url] of said.blocks) play.miniKept.set(key, { sig, url });
  } catch (_err) { /* drawn anew */ }
  return play.miniKept;
}
function playMiniKeep() {
  const kept = playMiniKept();
  while (kept.size > PLAY_MINI_KEPT_MOST) kept.delete(kept.keys().next().value);
  let blocks = [...kept].map(([key, one]) => [key, one.sig, one.url]);
  for (let tries = 0; tries < 3 && blocks.length; tries += 1) {
    try { localStorage.setItem(PLAY_MINI_KEPT_KEY, JSON.stringify({ v: PLAY_MINI_KEPT_VERSION, blocks })); return; }
    catch (_err) { blocks = blocks.slice(Math.floor(blocks.length / 2)); }    // too much: the newest half
  }
}
function playMiniSig(index, cx, cy) {
  const P = PLAY_LAND_MOST + PLAY_LAY_MARGIN + 2;
  const x0 = cx * PLAY_CHUNK - P, y0 = cy * PLAY_CHUNK - P, x1 = (cx + 1) * PLAY_CHUNK + P, y1 = (cy + 1) * PLAY_CHUNK + P;
  const meets = (r) => r[0] < x1 && r[2] > x0 && r[1] < y1 && r[3] > y0;
  const parts = [JSON.stringify(play.world.regions || [])];
  for (const one of index.zones) {
    if (!meets(one.rect)) continue;
    const z = one.zone;
    parts.push(`${z.id}:${one.rect.join(",")}:${z.kind}:${z.style || ""}:${z.name || ""}:${z.floor_text || ""}:${z.floor_color || ""}:${(z.days || []).join("")}`);
  }
  for (const one of index.landmarks) if (meets(one.square)) parts.push(`L${one.square.join(",")}`);
  for (const one of play.world.items) {
    if (one.x >= x0 && one.x < x1 && one.y >= y0 && one.y < y1) parts.push(`i${one.id || ""}${one.kind}${one.x},${one.y}`);
  }
  const near = [];
  for (const key of index.paths.keys()) {
    const comma = key.indexOf(","), x = +key.slice(0, comma), y = +key.slice(comma + 1);
    if (x >= x0 && x < x1 && y >= y0 && y < y1) near.push(key);
  }
  parts.push(near.sort().join(";"));
  for (const post of (index.life && index.life.posts) || []) if (post.x >= x0 && post.x < x1 && post.y >= y0 && post.y < y1) parts.push(`p${post.x},${post.y}`);
  let h = 2166136261;                                              // FNV-1a, of all of it
  const text = parts.join("|");
  for (let i = 0; i < text.length; i += 1) { h ^= text.charCodeAt(i); h = Math.imul(h, 16777619) >>> 0; }
  return `${text.length}:${h.toString(36)}`;
}
/* One block, shrunk: the walk's own picture of it (playChunk) and its bushes over it - not kept among the walk's
   blocks once drawn, so the walk's own stay. */
function playMiniShrink(cx, cy) {
  const key = `${cx},${cy}`, R = PLAY_MINI_PX, side = PLAY_CHUNK * R;
  const hadChunk = play.chunks.has(key), hadBlock = play.map.laid.has(key);
  const chunk = playChunk(cx, cy);
  const small = document.createElement("canvas");
  small.width = small.height = side;
  const c = small.getContext("2d");
  c.imageSmoothingEnabled = true;
  c.imageSmoothingQuality = "high";
  c.drawImage(chunk, 0, 0, side, side);
  const block = playBlock(play.map, cx, cy);
  const x0 = cx * PLAY_CHUNK, y0 = cy * PLAY_CHUNK;
  for (const one of block.deco) {                                         // the bushes, drawn each frame on the walk
    if (!one.rustle || one.x < x0 || one.y < y0 || one.x >= x0 + PLAY_CHUNK || one.y >= y0 + PLAY_CHUNK) continue;
    const image = play.sheets[one.spec[0]];
    if (image) c.drawImage(image, one.spec[1] * 17, one.spec[2] * 17, 16, 16, (one.x - x0) * R, (one.y - y0) * R, R, R);
  }
  for (const post of (play.map.life && play.map.life.posts) || []) {     // the signposts, a dot of wood each
    if (post.x < x0 || post.y < y0 || post.x >= x0 + PLAY_CHUNK || post.y >= y0 + PLAY_CHUNK) continue;
    c.fillStyle = post.start ? "#f2c94c" : "#8a5a2b";
    c.fillRect((post.x - x0) * R, (post.y - y0) * R - R, R, R * 2);
  }
  if (!hadChunk) play.chunks.delete(key);
  if (!hadBlock) play.map.laid.delete(key);
  return small;
}
function playMiniBlock(tiles, left, top, cx, cy, done) {
  const key = `${cx},${cy}`, sig = playMiniSig(play.map, cx, cy), kept = playMiniKept(), R = PLAY_MINI_PX;
  const c = tiles.getContext("2d");
  const at = [(cx * PLAY_CHUNK - left) * R, (cy * PLAY_CHUNK - top) * R];
  const was = kept.get(key);
  if (was && was.sig === sig && was.url) {                                // as it was: its kept picture
    const image = new Image();
    image.onload = () => { c.drawImage(image, at[0], at[1]); done(); };
    image.onerror = () => { kept.delete(key); playMiniBlock(tiles, left, top, cx, cy, done); };
    image.src = was.url;
    return false;
  }
  const small = playMiniShrink(cx, cy);
  c.drawImage(small, at[0], at[1]);
  let url = "";
  try { url = small.toDataURL("image/png"); } catch (_err) { url = ""; }
  kept.delete(key);
  if (url) kept.set(key, { sig, url });
  done();
  return true;
}
function playMiniBase() {
  const index = play.map;
  const rects = [...index.zones.map((one) => one.rect), ...index.landmarks.map((one) => one.square)];
  for (const key of index.paths.keys()) { const [x, y] = key.split(",").map(Number); rects.push([x, y, x + 1, y + 1]); }
  const pad = 8, R = PLAY_MINI_PX;
  const left = Math.min(...rects.map((r) => r[0])) - pad, top = Math.min(...rects.map((r) => r[1])) - pad;
  const right = Math.max(...rects.map((r) => r[2])) + pad, bottom = Math.max(...rects.map((r) => r[3])) + pad;
  const W = play.mini.width, H = play.mini.height;
  const k = Math.min(W / (right - left), H / (bottom - top));
  const ox = (W - (right - left) * k) / 2, oy = (H - (bottom - top) * k) / 2;
  const at = (x, y) => [ox + (x - left) * k, oy + (y - top) * k];
  const tiles = document.createElement("canvas");                          // the world, PLAY_MINI_PX a tile
  tiles.width = (right - left) * R; tiles.height = (bottom - top) * R;
  const tc = tiles.getContext("2d");
  tc.fillStyle = PLAY_MINI_DARK;
  tc.fillRect(0, 0, tiles.width, tiles.height);
  const base = document.createElement("canvas");
  base.width = W; base.height = H;
  const mine = { canvas: base, at, k };
  const compose = () => {
    const c = base.getContext("2d");
    c.fillStyle = PLAY_MINI_DARK;
    c.fillRect(0, 0, W, H);
    c.imageSmoothingEnabled = true;
    c.imageSmoothingQuality = "high";
    c.drawImage(tiles, ox, oy, (right - left) * k, (bottom - top) * k);
    c.fillStyle = "#f2c94c"; c.strokeStyle = "#3b2a10";
    for (const one of index.landmarks) { const [x, y] = at(one.cx, one.cy); c.fillRect(x - 2, y - 2, 4, 4); c.strokeRect(x - 2.5, y - 2.5, 5, 5); }
  };
  compose();
  // The blocks to draw: those that touch the world, nearest the walker first.
  const todo = [];
  for (let cy = Math.floor(top / PLAY_CHUNK); cy * PLAY_CHUNK < bottom; cy += 1) {
    for (let cx = Math.floor(left / PLAY_CHUNK); cx * PLAY_CHUNK < right; cx += 1) todo.push([cx, cy]);
  }
  const mx = play.me.x / PLAY_CHUNK, my = play.me.y / PLAY_CHUNK;
  todo.sort((p, q) => Math.hypot(p[0] + 0.5 - mx, p[1] + 0.5 - my) - Math.hypot(q[0] + 0.5 - mx, q[1] + 0.5 - my));
  let drawn = 0, waiting = 0, finished = false;
  const shown = () => { compose(); if (play.on) playMiniDraw(); };
  const step = () => {
    if (play.miniBase !== mine || !play.map) return;                      // another world since: stop
    const until = performance.now() + 14;
    while (todo.length && performance.now() < until) {
      const [cx, cy] = todo.shift();
      waiting += 1;
      if (playMiniBlock(tiles, left, top, cx, cy, () => { waiting -= 1; if (!todo.length && !waiting && !finished) { finished = true; shown(); if (drawn) playMiniKeep(); } })) drawn += 1;
    }
    shown();
    if (todo.length) setTimeout(step, 40);
  };
  play.miniBase = mine;
  setTimeout(step, 0);
}
function playMiniDraw() {
  if (!play.mini || play.mini.hidden || !play.map) return;
  if (!play.miniBase) playMiniBase();
  const { canvas: base, at, k } = play.miniBase;
  const c = play.mini.getContext("2d");
  c.drawImage(base, 0, 0);
  const size = PLAY_TILE * play.scale;
  const [vx, vy] = at(play.camera.x / size, play.camera.y / size);
  c.strokeStyle = "rgba(255, 255, 255, 0.85)";
  c.lineWidth = 1;
  c.strokeRect(vx + 0.5, vy + 0.5, (play.canvas.width / size) * k, (play.canvas.height / size) * k);
  if (typeof playPortals === "function") {
    c.fillStyle = "#6fe3ff";
    for (const one of playPortals()) { const [qx, qy] = at(one.x + 0.5, one.y + 0.5); c.fillRect(Math.round(qx) - 2, Math.round(qy) - 2, 3, 3); }
  }
  const [mx, my] = at(play.me.x, play.me.y);
  c.fillStyle = "#e5484d";
  c.fillRect(Math.round(mx) - 2, Math.round(my) - 2, 4, 4);
}

/* THE BUSHES: drawn each frame from the blocks in sight - one touched sways a moment; the one the walker stands in drawn
   again over its feet (`over`), so it walks in it, not on it. Leaves fly from a touch. */
const PLAY_SWAY_MS = 450, PLAY_LEAF_MS = 600;
function playBushAt(x, y) {
  if (!play.map) return null;
  const tx = Math.floor(x), ty = Math.floor(y);
  const block = playBlock(play.map, Math.floor(tx / PLAY_CHUNK), Math.floor(ty / PLAY_CHUNK));
  return block.bushes.get(`${tx},${ty}`) || null;
}
function playBushesDraw(size, c0, c1, r0, r1, over) {
  const { ctx } = play;
  const now = performance.now();
  const one = (bush, clipTop) => {
    const [sheet, col, row] = bush.spec;
    const image = play.sheets[sheet];
    if (!image) return;
    const age = now - (bush.touchedAt || -1e9);
    const sway = age < PLAY_SWAY_MS ? Math.round(Math.sin(age / 45) * (1 - age / PLAY_SWAY_MS) * play.scale * 1.2) : 0;
    const x = Math.round(bush.x * size - play.camera.x) + sway, y = Math.round(bush.y * size - play.camera.y);
    if (clipTop) {
      ctx.save();
      ctx.beginPath();
      ctx.rect(x - size, y + size * 0.45, size * 3, size);
      ctx.clip();
    }
    ctx.drawImage(image, col * 17, row * 17, 16, 16, x, y, size, size);
    if (clipTop) ctx.restore();
  };
  if (over) {
    const bush = playBushAt(play.me.x, play.me.y);
    if (bush) one(bush, true);
    // Leaves from a touch, falling.
    play.leaves = play.leaves.filter((leaf) => now - leaf.at < PLAY_LEAF_MS);
    for (const leaf of play.leaves) {
      const t = (now - leaf.at) / PLAY_LEAF_MS;
      const s = Math.max(1, Math.round(play.scale * (1.4 - t)));
      ctx.fillStyle = `rgba(92, 150, 64, ${(1 - t).toFixed(2)})`;
      ctx.fillRect(Math.round((leaf.x + leaf.dx * t) * size - play.camera.x), Math.round((leaf.y + leaf.dy * t + 1.2 * t * t) * size - play.camera.y), s, s);
    }
    return;
  }
  for (let cy = r0; cy <= r1; cy += 1) {
    for (let cx = c0; cx <= c1; cx += 1) {
      for (const bush of playBlock(play.map, cx, cy).bushes.values()) {
        if (Math.floor(bush.x / PLAY_CHUNK) === cx && Math.floor(bush.y / PLAY_CHUNK) === cy) one(bush, false);
      }
    }
  }
}

/* A PLACE KNOWN AT A GLANCE (the user, 2026-11-17: "整个房间一看就知道是那个project的"): on its floor, under its things -
   its logo laid as a mosaic, a picture the agent drew, its name spelled in large pixel letters - each fitted to the
   floor, in the middle, a step in from its walls. */
function playFloorArt(context, zone, x0, y0) {
  if (!zone.floor_text && !zone.floor_picture_url && !zone.mural) return;
  const [fx, fy, fw, fh] = playFloorOf(zone);
  if (fx + fw < x0 || fx > x0 + PLAY_CHUNK || fy + fh < y0 || fy > y0 + PLAY_CHUNK) return;
  const left = (fx + 1 - x0) * 16, top = (fy + 1 - y0) * 16, w = (fw - 2) * 16, h = (fh - 2) * 16;
  if (w < 32 || h < 32) return;
  context.save();
  context.beginPath();
  context.rect(left, top, w, h);
  context.clip();
  context.imageSmoothingEnabled = false;
  const parts = [zone.floor_picture_url ? "picture" : "", zone.mural ? "mural" : "", zone.floor_text ? "text" : ""].filter(Boolean);
  const bandH = h / parts.length;
  parts.forEach((part, n) => {
    const bx = left, by = top + n * bandH, bw = w, bh = bandH;
    if (part === "picture") {
      const image = play.pictures.get(zone.floor_picture_url);
      if (image && image.complete && image.naturalWidth) {
        // A mosaic: the logo made small, then laid large - square pixels, a little faded into the floor.
        const s = Math.min(bw / image.naturalWidth, bh / image.naturalHeight) * 0.9;
        const dw = Math.round(image.naturalWidth * s), dh = Math.round(image.naturalHeight * s);
        const small = document.createElement("canvas");
        small.width = Math.max(1, Math.round(dw / 4)); small.height = Math.max(1, Math.round(dh / 4));
        small.getContext("2d").drawImage(image, 0, 0, small.width, small.height);
        context.globalAlpha = 0.85;
        context.drawImage(small, Math.round(bx + (bw - dw) / 2), Math.round(by + (bh - dh) / 2), dw, dh);
        context.globalAlpha = 1;
      } else if (!play.pictures.has(zone.floor_picture_url)) {
        const one = new Image();
        play.pictures.set(zone.floor_picture_url, one);
        one.onload = () => { play.chunks.clear(); playDraw(); };
        one.src = zone.floor_picture_url;
      }
    } else if (part === "mural") {
      const rows = zone.mural.rows, cols = Math.max(...rows.map((r) => r.length));
      const cell = Math.max(2, Math.floor(Math.min(bw / cols, bh / rows.length)));
      const mx = Math.round(bx + (bw - cols * cell) / 2), my = Math.round(by + (bh - rows.length * cell) / 2);
      context.globalAlpha = 0.9;
      rows.forEach((row, ry) => [...row].forEach((ch, rx) => {
        if (ch === ".") return;
        const colour = zone.mural.palette[Number(ch)];
        if (!colour) return;
        context.fillStyle = colour;
        context.fillRect(mx + rx * cell, my + ry * cell, cell, cell);
      }));
      context.globalAlpha = 1;
    } else {
      // Its name in pixel letters: written small, hardened, laid large.
      const small = document.createElement("canvas");
      const write = small.getContext("2d");
      write.font = "bold 12px Consolas, 'Courier New', monospace";
      const tw = Math.ceil(write.measureText(zone.floor_text).width) + 2;
      small.width = tw; small.height = 14;
      write.font = "bold 12px Consolas, 'Courier New', monospace";
      write.textBaseline = "top";
      write.fillStyle = zone.floor_color || "#7a4fa0";
      write.fillText(zone.floor_text, 1, 1);
      const data = write.getImageData(0, 0, small.width, small.height);
      for (let i = 3; i < data.data.length; i += 4) data.data[i] = data.data[i] > 110 ? 230 : 0;
      write.putImageData(data, 0, 0);
      const s = Math.max(1, Math.floor(Math.min(bw / small.width, bh / small.height)));
      context.drawImage(small, Math.round(bx + (bw - small.width * s) / 2), Math.round(by + (bh - small.height * s) / 2),
                        small.width * s, small.height * s);
    }
  });
  context.restore();
}

/* -- walked ------------------------------------------------------------------------------------------------------- */
function playBlocked(x, y) {
  const map = play.map;
  // Its feet only (a step's worth): it brushes past what stands by a road, as one does ("刚刚走的时候卡住了").
  const probe = [[x - 0.28, y - 0.12], [x + 0.28, y - 0.12], [x - 0.28, y], [x + 0.28, y]];
  return probe.some(([px, py]) => {
    const [block, i] = playCell(map, Math.floor(px), Math.floor(py));
    if (block.none[i]) {
      play.atEdge = true;                        // the world's end, not a thing in the way
      return true;
    }
    // WATER IS SWUM IN (the user, 2026-11-17: "水里可以钓鱼，游泳之类"): not in the way - a fall's rock is.
    const piece = String(block.ground[i] || "");
    if (piece.startsWith("water") && !piece.startsWith("waterFall")) return false;
    return Boolean(block.solid[i]);
  });
}

/* THE WORLD'S EDGE SPEAKS (the user, 2026-11-17: "世界边缘是黑色，如果走过去会显示会话（类似未来会有什么呢之类的)"): walked
   into, the watermelon wonders what lies beyond - the part of their life not lived yet. One line, not again for a while. */
const PLAY_EDGE_LINES = [
  "What's out there? It isn't drawn yet - it will be, as you live it.",
  "Beyond here is the part of your life that hasn't happened yet.",
  "Nothing here yet. I wonder what you'll do next.",
  "The map ends here - for now. Let's see where you take it.",
  "Whatever you do next, I'll build it right here.",
];
const PLAY_EDGE_EVERY_MS = 9000;
function playEdgeSay() {
  const now = performance.now();
  if (play.said || now - (play.edgeSaidAt || 0) < PLAY_EDGE_EVERY_MS) return;
  play.edgeSaidAt = now;
  play.edgeLine = ((play.edgeLine ?? -1) + 1) % PLAY_EDGE_LINES.length;
  const text = playWords(PLAY_EDGE_LINES[play.edgeLine]);
  play.said = { text, at: now, until: now + playSayFor(text), x: play.me.x, y: play.me.y };
}

function playStep(seconds) {
  if (play.ride) return false;                                 // riding: the vehicle moves it (play-act.js)
  if (play.sit && play.keys.size) play.sit = null;             // a step: stood up
  if (play.sit) return false;
  if (play.fish && play.keys.size) play.fish = null;           // walked off: the line reeled in
  let dx = 0, dy = 0;
  if (play.keys.has("left")) dx -= 1;
  if (play.keys.has("right")) dx += 1;
  if (play.keys.has("up")) dy -= 1;
  if (play.keys.has("down")) dy += 1;
  if (!dx && !dy) return false;
  const length = Math.hypot(dx, dy);
  // RUNNING with Shift held (the user, 2026-11-17: "按住shift可以加速跑步（或者用更酷的方式跑）"): faster, its steps
  // quicker, and a puff of dust left behind now and then.
  const running = play.running;
  const inBush = Boolean(playBushAt(play.me.x, play.me.y));
  play.swimming = typeof playActWater === "function" && playActWater(play.me.x, play.me.y - 0.1);
  const far = PLAY_SPEED * (running ? PLAY_RUN : 1) * (inBush ? 0.7 : 1) * (play.swimming ? 0.55 : 1) * Math.min(seconds, 0.05);
  const nx = play.me.x + (dx / length) * far, ny = play.me.y + (dy / length) * far;
  play.atEdge = false;
  if (!playBlocked(nx, play.me.y)) play.me.x = nx;      // along a wall, not stopped by it
  if (!playBlocked(play.me.x, ny)) play.me.y = ny;
  if (play.atEdge) playEdgeSay();
  // A bush stepped into: it sways, leaves fly, it rustles.
  const bush = playBushAt(play.me.x, play.me.y);
  if (bush && bush !== play.touched) {
    play.touched = bush;
    bush.touchedAt = performance.now();
    for (let n = 0; n < 4; n += 1) {
      play.leaves.push({ x: bush.x + 0.5, y: bush.y + 0.4, at: bush.touchedAt, dx: (Math.random() - 0.5) * 1.6, dy: -0.4 - Math.random() * 0.6 });
    }
    playSoundRustle();
    playSfx("cloth", 0.18);
  } else if (!bush) play.touched = null;
  play.me.facing = Math.abs(dx) >= Math.abs(dy) && dx ? (dx < 0 ? "left" : "right") : (dy < 0 ? "up" : "down");
  const before = Math.floor(play.me.step * PLAY_STEPS_A_SECOND);
  play.me.step += seconds * (running ? 1.7 : 1);
  if (running && performance.now() - (play.dustAt || 0) > 70) {
    play.dustAt = performance.now();
    play.dust.push({ x: play.me.x - (dx / length) * 0.25, y: play.me.y - 0.05, at: play.dustAt, drift: Math.random() - 0.5 });
  }
  // A footstep at each step's landing (frames 1 and 3), by what is underfoot.
  const frame = Math.floor(play.me.step * PLAY_STEPS_A_SECOND);
  if (frame !== before && frame % 2 === 1) {
    const [block, i] = playCell(play.map, Math.floor(play.me.x), Math.floor(play.me.y));
    // What is underfoot: a rug lying there, else the ground (snowy in a snowy land or an eternal winter).
    const tx = Math.floor(play.me.x), ty = Math.floor(play.me.y);
    const rug = play.world.items.some((one) => one.kind === "rug" && tx >= one.x && tx < one.x + 3 && ty >= one.y && ty < one.y + 2);
    const region = play.region || {};
    const snowy = region.biome === "snow" || region.weather === "eternal_winter";
    playSoundStep(rug ? "rug" : playMaterialOf(block.ground[i], snowy));
  }
  playWhere();
  if (performance.now() - (play.keptAt || 0) > 2000) { play.keptAt = performance.now(); playKeepWhere(); }
  return true;
}

/* The room it is in (its name shown when it changes) and the thing nearest (its card). */
function playWhere() {
  const tx = play.me.x, ty = play.me.y;
  const zone = play.world.zones.find((one) => {
    const [fx, fy, fw, fh] = playFloorOf(one);
    return tx >= fx && tx < fx + fw && ty >= fy && ty < fy + fh;
  }) || null;
  // THE REGION ENTERED (play-regions.js): its name on the sign, a chime - not where the walk began.
  const region = playRegionAt(play.map, tx, ty);
  if (region && (!play.region || region.id !== play.region.id)) {
    const first = !play.region;
    play.region = region;
    playStatus();
    if (!first) {
      play.banner = { name: playWords(region.name || ""), since: playPeriod(playRegionLived(region)), at: performance.now(),
                      words: null, region: true };
      playSoundChime();
    }
    if (!zone) playSoundRoom(playRegionSound(region));
  }
  if ((zone && zone.id) !== (play.zone && play.zone.id)) {
    const wasIn = play.zone && !PLAY_OUTDOOR[play.zone.kind], nowIn = zone && !PLAY_OUTDOOR[zone.kind];
    if (wasIn !== nowIn && (wasIn || nowIn)) playSfx(nowIn ? "door-open" : "door-close", 0.35);
    play.zone = zone;
    playSoundRoom(zone || playRegionSound(play.region));
    if (zone && !(play.banner && play.banner.region && performance.now() - play.banner.at < 1200)) {
      // When it was lived, not when it was built (the user, 2026-11-17: "这个时间也是event时间，而不是make 时间").
      play.banner = { name: zone.name || zone.kind, since: playPeriod(playLived(zone)),
                      at: performance.now(), words: null };
    }
  }
  let near = null, best = 1.6;
  // NOT THROUGH A WALL (the user, 2026-11-17: "除了窗户，在房屋外面不能触发某个内部设施的介绍"): a thing in a building is
  // known from inside its room; from outside, only its windows. An open place has no walls.
  const here = play.zone ? play.zone.id : "";
  const kinds = play.zoneKinds || (play.zoneKinds = Object.fromEntries(play.world.zones.map((one) => [one.id, one.kind])));
  for (const item of play.world.items) {
    if (item.zone !== here && item.kind !== "window" && !PLAY_OUTDOOR[kinds[item.zone]]) continue;
    const [iw, ih] = PLAY_SIZES[item.kind] || [1, 1];
    const cx = Math.max(item.x, Math.min(tx, item.x + iw)), cy = Math.max(item.y, Math.min(ty, item.y + ih));
    const d = Math.hypot(tx - cx, ty - cy);
    if (d < best && (item.why || item.name || item.picture_url)) { best = d; near = item; }
  }
  // A SIGN NEAR (the user: "每个房间或者外景的标牌都要有交互"): its place told, if no thing is nearer.
  if (!near && typeof playActNear === "function") near = playActNear(tx, ty);   // a portal, a stop
  if (!near) {
    let bestSign = 1.6;
    for (const sign of playSigns()) {
      const d = Math.hypot(tx - sign.at[0], ty - (sign.at[1] - 0.4));
      if (d < bestSign) { bestSign = d; near = sign; }
    }
  }
  if (near !== play.near) { play.near = near; playCard(); if (near) playSoundNear(); }
}

/* A THING'S CARD: its picture, its name, when it was made, why - the agent's own words - and from what. Text set to the
   card's left, each part on its own line (the user: "图片的font什么要修改align"). */
/* A SIGN'S CARD: what the place is, as a whole - its name and its task's, when it was made and how it grew, why the agent
   made it, what the task was, how many things it holds; for a landmark, its land and why it is so. */
function playSignCard(sign) {
  if ((sign.portal || sign.stop) && typeof playActCard === "function") return playActCard(sign);
  if (sign.post) {
    const life = play.map.life, to = sign.post.to.zone, from = sign.post.from && sign.post.from.zone;
    const n = life.order.indexOf(sign.post.to) + 1;
    const lines = [`${playWords("This way")}: ${to.name || to.kind} · ${playPeriod(playLived(to))}`,
                   from ? `${playWords("From")}: ${from.name || from.kind}` : "",
                   `${playWords("Step")} ${n} / ${life.order.length}`];
    return `<div class="side-play-card-words"><strong class="chat-message">${escapeHtml(sign.post.start ? playWords("Start of your story") : `→ ${to.name || to.kind}`)}</strong>
      ${lines.filter(Boolean).map((one) => `<span class="side-play-card-when">${escapeHtml(one)}</span>`).join("")}
      ${to.why ? `<p class="chat-message">${escapeHtml(playShort(to.why, 160))}</p>` : ""}</div>`;
  }
  if (sign.region) {
    const r = sign.region;
    return `<div class="side-play-card-words"><strong class="chat-message">${escapeHtml(sign.name)} · ${escapeHtml(r.name || "")}</strong>
      <span class="side-play-card-when">${escapeHtml(playPeriod(playRegionLived(r)))}</span>
      ${r.why ? `<p class="chat-message">${escapeHtml(r.why)}</p>` : ""}</div>`;
  }
  const zone = sign.zone;
  const things = play.world.items.filter((one) => one.zone === zone.id).length;
  const grown = (zone.grown || []).slice(-3).map((one) => `${String(one.at || "").slice(0, 10)} ${one.what || ""}`);
  const lived = playPeriod(playLived(zone));
  const lines = [[lived, zone.task_title ? `${playWords("Task")}: ${playShort(zone.task_title, 48)}` : ""].filter(Boolean).join(" · "),
                 zone.between_names && zone.between_names.length ? `${playWords("Between")}: ${zone.between_names.join(" · ")}` : "",
                 zone.goes_on_names && zone.goes_on_names.length ? `${playWords("Goes on from")}: ${zone.goes_on_names.join(" → ")}` : "",
                 zone.town_names && zone.town_names.length ? `${playWords("Town of")}: ${zone.town_names.join(" · ")}` : "",
                 zone.rooms && zone.rooms.length ? `${playWords("Rooms")}: ${zone.rooms.map((one) => one.name).join(" · ")}` : "",
                 zone.group_titles && zone.group_titles.length ? `${playWords("Small tasks")}: ${zone.group_titles.map((one) => playShort(one, 28)).join(" · ")}` : "",
                 `${sign.kind} · ${things} ${playWords(things === 1 ? "thing" : "things")}`];
  const built = `${playWords("Built")} ${String(zone.made || "").slice(0, 10)}`;
  return `<div class="side-play-card-words"><strong class="chat-message">${escapeHtml(zone.name || zone.kind)}</strong>
    ${lines.filter(Boolean).map((one) => `<span class="side-play-card-when">${escapeHtml(one)}</span>`).join("")}
    ${zone.why ? `<p class="chat-message">${escapeHtml(zone.why)}</p>` : ""}
    ${zone.task_summary ? `<p class="side-play-card-from chat-message">${escapeHtml(zone.task_summary)}</p>` : ""}
    <p class="side-play-card-from chat-message">${escapeHtml(built)}${grown.length ? escapeHtml(` · ${playWords("Grown")}: ${grown.join("; ")}`) : ""}</p></div>`;
}

function playCard() {
  const card = play.el.querySelector(".side-play-card");
  const item = play.near;
  if (!item) { card.hidden = true; return; }
  if (item.at) { card.innerHTML = playSignCard(item); card.hidden = false; return; }          // a sign, not a thing
  const made = String(item.made || "").slice(0, 10);
  card.innerHTML = `${item.picture_url ? `<img src="${escapeHtml(item.picture_url)}" alt="">` : ""}
    <div class="side-play-card-words">
      <strong class="chat-message">${escapeHtml(item.name || item.kind)}</strong>
      <span class="side-play-card-when">${escapeHtml(playPeriod(item.days) || "")}</span>
      ${item.why ? `<p class="chat-message">${escapeHtml(item.why)}</p>` : ""}
      ${item.from ? `<p class="side-play-card-from chat-message">${escapeHtml(item.from)}</p>` : ""}
      <p class="side-play-card-from chat-message">${escapeHtml(`${playWords("Built")} ${made}`)}</p>
    </div>`;
  card.hidden = false;
}

/* THE GREAT ROLLER COASTER (the user, 2026-11-17: "过山车可以有超大的（另外你看看过山车大亨里的场景和过山车元素）"): after
   RollerCoaster Tycoon's - its pictures not taken, its kinds of thing drawn here. Its track runs round its ground, high
   over it: a station, a lift hill with its chain, a first drop, hills, a vertical loop, the brakes home - red steel rails
   on ties, steel supports down to the ground (the higher, the longer), its shadow on the grass. As large as its ground (a
   large task's is large). A train of cars runs round it, slow up the lift, fast down the drops. The track drawn once a
   coaster into a picture of its own; each frame, that picture and the train. */
const PLAY_COASTER_STEP = 0.25, PLAY_COASTER_LAP_S = 26;
function playCoasterPath(zone) {
  const [fx, fy, fw, fh] = playFloorOf(zone);
  const L = fx + 0.5, T = fy + 0.5, R = fx + fw - 0.5, B = fy + fh - 0.5, r = Math.min(1.5, (fw - 1) / 2, (fh - 1) / 2);
  // Round the edge, from the station at its foot's left: right along the foot, up the right, left along the top, down the left.
  const legs = [[[L + r, B], [R - r, B]], [[R, B - r], [R, T + r]], [[R - r, T], [L + r, T]], [[L, T + r], [L, B - r]]];
  const corners = [[R - r, B - r, Math.PI / 2, 0], [R - r, T + r, 0, -Math.PI / 2], [L + r, T + r, -Math.PI / 2, -Math.PI], [L + r, B - r, Math.PI, Math.PI / 2]];
  const pts = [];
  legs.forEach(([[ax, ay], [bx, by]], n) => {
    const len = Math.hypot(bx - ax, by - ay), steps = Math.max(1, Math.round(len / PLAY_COASTER_STEP));
    for (let i = 0; i < steps; i += 1) pts.push([ax + (bx - ax) * i / steps, ay + (by - ay) * i / steps]);
    const [cx, cy, a0, a1] = corners[n], arc = Math.max(1, Math.round(r * Math.PI / 2 / PLAY_COASTER_STEP));
    for (let i = 0; i < arc; i += 1) { const a = a0 + (a1 - a0) * i / arc; pts.push([cx + Math.cos(a) * r, cy + Math.sin(a) * r]); }
  });
  const n = pts.length, top = Math.min(7, 1.6 + (fw + fh) / 9);
  // How high each point is, in tiles: the station, the lift, the drop, hills less and less, the brakes.
  const height = (u) => {
    if (u < 0.07) return 0.35;
    if (u < 0.3) return 0.35 + (top - 0.35) * (u - 0.07) / 0.23;
    if (u < 0.38) return top - (top - 0.6) * Math.sin((u - 0.3) / 0.08 * Math.PI / 2);
    if (u < 0.86) { const v = (u - 0.38) / 0.48; return 0.6 + top * 0.55 * (1 - v * 0.6) * Math.abs(Math.sin(v * Math.PI * 3)); }
    return 0.6 - 0.25 * (u - 0.86) / 0.14;
  };
  const path = pts.map(([x, y], i) => ({ x, y, h: height(i / n), lift: i / n >= 0.07 && i / n < 0.3 }));
  // The loop: on the top's middle, where the hills run.
  const loopAt = path.reduce((best, p, i) => (Math.abs(p.y - T) < 0.01 && Math.abs(p.x - (L + R) / 2) < Math.abs(path[best].x - (L + R) / 2) ? i : best), 0);
  return { path, top, loopAt, loopR: Math.max(1.5, Math.min(3.2, fh / 5)), floor: [fx, fy, fw, fh] };
}
function playCoasterPicture(zone) {
  play.coasters = play.coasters || new Map();
  const key = `${zone.id}:${zone.x},${zone.y},${zone.w},${zone.h}`;
  if (play.coasters.has(key)) return play.coasters.get(key);
  const route = playCoasterPath(zone), T = PLAY_TILE;
  const [fx, fy, fw, fh] = route.floor, up = Math.ceil(route.top + route.loopR * 2 + 1.5);
  const canvas = document.createElement("canvas");
  canvas.width = (fw + 2) * T; canvas.height = (fh + 2 + up) * T;
  const c = canvas.getContext("2d");
  const ox = (1 - fx) * T, oy = (up + 1 - fy) * T;            // world tile -> this picture's pixel
  const at = (p, lift = 0) => [p.x * T + ox, (p.y - p.h - lift) * T + oy];
  const path = route.path, n = path.length;
  // Its shadow on the ground.
  c.strokeStyle = "rgba(0, 0, 0, 0.18)"; c.lineWidth = 6; c.beginPath();
  path.forEach((p, i) => { const x = p.x * T + ox, y = p.y * T + oy + 3; if (i) c.lineTo(x, y); else c.moveTo(x, y); });
  c.closePath(); c.stroke();
  // Supports: steel posts down to the ground, braced when tall.
  for (let i = 0; i < n; i += 4) {
    const p = path[i], [x, y] = at(p), gy = p.y * T + oy + 2;
    c.fillStyle = "#6e7480"; c.fillRect(Math.round(x) - 1, Math.round(y) + 3, 2, Math.max(0, gy - y - 3));
    c.fillStyle = "#a9b0bc"; c.fillRect(Math.round(x) - 1, Math.round(y) + 3, 1, Math.max(0, gy - y - 3));
    if (p.h > 2.2) { c.strokeStyle = "#7d8490"; c.lineWidth = 1; c.beginPath(); c.moveTo(x - 4, gy); c.lineTo(x, y + T * 0.8); c.lineTo(x + 4, gy); c.stroke(); }
  }
  // The track: ties, then two red rails, a light edge on each; the lift's chain down its middle.
  const side = (i) => { const a = at(path[(i + n - 1) % n]), b = at(path[(i + 1) % n]); const dx = b[0] - a[0], dy = b[1] - a[1], d = Math.hypot(dx, dy) || 1; return [-dy / d, dx / d]; };
  c.strokeStyle = "#5b3a24"; c.lineWidth = 2;
  for (let i = 0; i < n; i += 1) {
    const [x, y] = at(path[i]), [sx, sy] = side(i);
    c.beginPath(); c.moveTo(x - sx * 4, y - sy * 4); c.lineTo(x + sx * 4, y + sy * 4); c.stroke();
  }
  for (const [off, colour, width] of [[-3, "#8e1b22", 2.4], [3, "#8e1b22", 2.4], [-3, "#e2453f", 1.2], [3, "#e2453f", 1.2]]) {
    c.strokeStyle = colour; c.lineWidth = width; c.beginPath();
    for (let i = 0; i <= n; i += 1) { const [x, y] = at(path[i % n]), [sx, sy] = side(i % n); const px = x + sx * off, py = y + sy * off; if (i) c.lineTo(px, py); else c.moveTo(px, py); }
    c.stroke();
  }
  c.fillStyle = "#2b2b33";
  for (let i = 0; i < n; i += 1) if (path[i].lift && i % 2 === 0) { const [x, y] = at(path[i]); c.fillRect(Math.round(x) - 1, Math.round(y) - 1, 2, 2); }
  // The vertical loop, standing on the track at the top's middle.
  const lp = path[route.loopAt], [lx, ly] = at(lp), LR = route.loopR * T;
  c.strokeStyle = "#7d8490"; c.lineWidth = 2;
  c.beginPath(); c.moveTo(lx - LR * 0.7, ly); c.lineTo(lx, ly - LR * 2); c.lineTo(lx + LR * 0.7, ly); c.stroke();
  for (const [dr, colour, width] of [[-3, "#8e1b22", 2.4], [3, "#8e1b22", 2.4], [-3, "#e2453f", 1.2], [3, "#e2453f", 1.2]]) {
    c.strokeStyle = colour; c.lineWidth = width; c.beginPath();
    c.ellipse(lx, ly - LR, LR * 0.62 + dr * 0.4, LR + dr * 0.4, 0, 0, Math.PI * 2); c.stroke();
  }
  // The station, at the start.
  const station = PLAY_SCENERY.coasterStation, sheet = station && play.sheets[station[0]];
  if (sheet) {
    const p = path[1], [x, y] = at(p);
    c.drawImage(sheet, station[1] * 17, station[2] * 17, station[3] * 17 - 1, station[4] * 17 - 1, x - T * 0.5, y - T * 1.6, station[3] * T, station[4] * T);
  }
  const made = { canvas, ox, oy, route };
  play.coasters.set(key, made);
  return made;
}
/* A BUILDING'S FACE, BY ITS STYLE (the user, 2026-11-17: "摩天大楼 ... 学校教学楼，实验室，办公场所，公寓"): a skyscraper's
   tower rises behind its rooms, floor on floor of glass; an office's lower, a block of flats in brick with balconies; a
   school a clock tower and a flag; a lab a white dome with its aerial. Lit windows by night. */
const PLAY_FACADES = { skyscraper: { floors: 9, glass: true }, office: { floors: 5, glass: true }, apartment: { floors: 4, glass: false } };
function playFacadesDraw(size) {
  const { ctx, canvas } = play;
  const night = typeof playSkyDaylight === "function" && play.region ? (playSkyDaylight(play.region) || {}).light < 0.55 : false;
  for (const zone of play.world.zones) {
    const style = zone.style;
    if (!PLAY_FACADES[style] && style !== "school" && style !== "laboratory") continue;
    const left = zone.x * size - play.camera.x, top = zone.y * size - play.camera.y, w = (zone.w + 2) * size;
    if (left > canvas.width || left + w < 0 || top - 10 * size > canvas.height || top + 4 * size < 0) continue;
    ctx.save();
    if (PLAY_FACADES[style]) {
      const look = PLAY_FACADES[style], floors = Math.min(look.floors, 3 + Math.round(zone.w / 3)), floorH = size * 0.9;
      const tw = w * (style === "skyscraper" ? 0.7 : 0.9), tx = left + (w - tw) / 2, ty = top - floors * floorH;
      ctx.fillStyle = look.glass ? "#3c6a9c" : "#9a5a44";
      ctx.fillRect(tx, ty, tw, floors * floorH + size * 0.2);
      ctx.fillStyle = look.glass ? "#5e8fc4" : "#b8735a";
      ctx.fillRect(tx, ty, tw * 0.12, floors * floorH);                  // its lit edge
      for (let f = 0; f < floors; f += 1) {
        for (let k = 0; k < Math.floor(tw / (size * 0.45)); k += 1) {
          const lit = night && playHash(zone.x + k, zone.y + f, 71) < 0.45;
          ctx.fillStyle = lit ? "#ffe08a" : (look.glass ? "#9fd0f2" : "#e9d7b8");
          ctx.fillRect(tx + size * 0.15 + k * size * 0.45, ty + f * floorH + size * 0.18, size * 0.25, size * 0.42);
        }
        if (style === "apartment") { ctx.fillStyle = "#6b4a36"; ctx.fillRect(tx, ty + f * floorH + floorH - size * 0.12, tw, size * 0.1); }
      }
      ctx.fillStyle = "#6b7280"; ctx.fillRect(tx - size * 0.1, ty - size * 0.25, tw + size * 0.2, size * 0.25);   // its roof
      if (style === "skyscraper") {
        ctx.fillRect(tx + tw / 2 - 1, ty - size * 1.2, 2, size);
        ctx.fillStyle = (performance.now() / 600) % 2 < 1 ? "#ff4d4d" : "#7a1f1f"; ctx.fillRect(tx + tw / 2 - 2, ty - size * 1.3, 4, 4);
      }
    } else if (style === "school") {
      const cx = left + w / 2, ty = top - size * 2.6;
      ctx.fillStyle = "#b04a3a"; ctx.fillRect(cx - size * 0.7, ty, size * 1.4, size * 2.7);
      ctx.fillStyle = "#7a2f25"; ctx.beginPath(); ctx.moveTo(cx - size * 0.9, ty); ctx.lineTo(cx, ty - size * 0.8); ctx.lineTo(cx + size * 0.9, ty); ctx.fill();
      ctx.fillStyle = "#f6f1e4"; ctx.beginPath(); ctx.arc(cx, ty + size * 0.6, size * 0.38, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = "#2a1f1a"; ctx.lineWidth = Math.max(1, size / 14);
      ctx.beginPath(); ctx.moveTo(cx, ty + size * 0.6); ctx.lineTo(cx, ty + size * 0.36); ctx.moveTo(cx, ty + size * 0.6); ctx.lineTo(cx + size * 0.18, ty + size * 0.66); ctx.stroke();
      ctx.fillStyle = "#555"; ctx.fillRect(left + w - size * 0.6, top - size * 2.2, 2, size * 2.2);         // its flag
      ctx.fillStyle = "#d33"; ctx.fillRect(left + w - size * 0.6 + 2, top - size * 2.2, size * 0.6, size * 0.4);
    } else {
      const cx = left + w / 2, ty = top - size * 0.4;
      ctx.fillStyle = "#e8ecef"; ctx.beginPath(); ctx.ellipse(cx, ty, size * 1.4, size * 1.1, 0, Math.PI, 0); ctx.fill();
      ctx.fillStyle = "#c9d1d8"; ctx.fillRect(cx - size * 0.15, ty - size * 1.1, size * 0.3, size * 1.1);
      ctx.fillStyle = "#56c2e6"; ctx.fillRect(cx - size * 0.1, ty - size * 1.1, size * 0.2, size * 0.5);
      ctx.strokeStyle = "#7d8490"; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(cx + size, ty - size * 0.3); ctx.lineTo(cx + size * 1.4, ty - size * 1.8); ctx.stroke();
      ctx.fillStyle = "#e5484d"; ctx.fillRect(cx + size * 1.4 - 2, ty - size * 1.9, 4, 4);
    }
    ctx.restore();
  }
}

function playCoastersDraw(size) {
  const { ctx, canvas } = play;
  const k = size / PLAY_TILE;
  for (const zone of play.world.zones) {
    if (zone.kind !== "roller_coaster") continue;
    const made = playCoasterPicture(zone);
    const x = -made.ox * k - play.camera.x, y = -made.oy * k - play.camera.y;
    if (x > canvas.width || y > canvas.height || x + made.canvas.width * k < 0 || y + made.canvas.height * k < 0) continue;
    ctx.drawImage(made.canvas, Math.round(x), Math.round(y), made.canvas.width * k, made.canvas.height * k);
    // Its train: five cars, round and round - slow up the lift, fast where it is low.
    const path = made.route.path, n = path.length, car = PLAY_SCENERY.coasterCar, sheet = car && play.sheets[car[0]];
    if (!sheet) continue;
    const lap = (performance.now() / 1000 / PLAY_COASTER_LAP_S) % 1;
    const ease = (u) => (u < 0.07 ? u * 0.6 : u < 0.3 ? 0.042 + (u - 0.07) * 1.6 : 0.41 + (u - 0.3) * (0.59 / 0.7));
    const lead = Math.floor(ease(lap) * n) % n;
    for (let j = 0; j < 5; j += 1) {
      const i = (lead - j * 3 + n * 4) % n, p = path[i], q = path[(i + 1) % n];
      const px = (p.x * PLAY_TILE + made.ox) * k + x, py = ((p.y - p.h) * PLAY_TILE + made.oy) * k + y;
      const qx = (q.x * PLAY_TILE + made.ox) * k + x, qy = ((q.y - q.h) * PLAY_TILE + made.oy) * k + y;
      ctx.save();
      ctx.translate(px, py);
      ctx.rotate(Math.atan2(qy - py, qx - px));
      ctx.drawImage(sheet, car[1] * 17, car[2] * 17, 16, 16, -size * 0.4, -size * 0.5, size * 0.8, size * 0.8);
      ctx.restore();
    }
  }
}

/* THE FRAMES RUN ONLY WHILE SOMETHING MOVES - a key held, a sign showing; still, nothing is drawn. */
let playRunning = false;
function playRun() {
  if (playRunning || !play.on) return;
  playRunning = true;
  play.last = performance.now();
  const tick = (now) => {
    if (!play.on) { playRunning = false; return; }
    const seconds = (now - play.last) / 1000;
    play.last = now;
    const moved = playStep(seconds);
    const busy = play.banner || playJumpHeight() > 0 || (play.said && performance.now() < play.said.until) || play.dust.length
      || play.leaves.length || (play.touched && performance.now() - play.touched.touchedAt < PLAY_SWAY_MS);
    if (moved || busy) playDraw();
    if (moved || busy || play.keys.size) requestAnimationFrame(tick);
    else { playDraw(); playRunning = false; }
  };
  requestAnimationFrame(tick);
}

const PLAY_KEYS = { ArrowLeft: "left", a: "left", A: "left", ArrowRight: "right", d: "right", D: "right",
                    ArrowUp: "up", w: "up", W: "up", ArrowDown: "down", s: "down", S: "down" };
window.addEventListener("keydown", (event) => {
  if (!play.on || event.altKey || event.metaKey) return;
  play.running = event.shiftKey;
  const typing = document.activeElement && /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
  if (!typing && ["+", "=", "-", "_", "0"].includes(event.key)) {
    event.preventDefault();
    event.stopPropagation();                   // the world zoomed, not the panel
    playZoom(event.key === "0" ? 0 : ["+", "="].includes(event.key) ? 1 : -1);
    return;
  }
  if (event.ctrlKey) return;
  if (!typing && event.key === " ") {
    event.preventDefault();
    if (!event.repeat) playJump();
    return;
  }
  if (!typing && ["m", "M"].includes(event.key)) {
    event.preventDefault();
    if (!event.repeat) playMiniToggle();
    return;
  }
  if (!typing && ["e", "E", "Enter"].includes(event.key)) {
    event.preventDefault();
    if (!event.repeat) playLook();
    return;
  }
  const way = PLAY_KEYS[event.key];
  if (!way || typing) return;
  event.preventDefault();
  play.keys.add(way);
  playRun();
});
window.addEventListener("keyup", (event) => {
  play.running = event.shiftKey;
  const way = PLAY_KEYS[event.key];
  if (way) play.keys.delete(way);
});
window.addEventListener("blur", () => { play.keys.clear(); play.running = false; });
window.addEventListener("pagehide", () => { if (play.on) playKeepWhere(); });
document.addEventListener("visibilitychange", () => { if (document.hidden) play.keys.clear(); });
window.addEventListener("resize", () => { if (play.on) { playPlace(); playFit(); } });
