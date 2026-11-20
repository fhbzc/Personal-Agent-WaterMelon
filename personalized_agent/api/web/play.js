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
                      sea: "/play/sea.png", love: "/play/love.png", yard: "/play/yard.png", produce: "/play/produce.png" };
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
  shield: [["indoor", 22, 8, 1, 1], ["indoor", 22, 10, 1, 1]], rug: [null], paint: [null],
  statue_of: [null], banner: [null], poster: [null], pixel_art: [null], pixel_art_large: [null], wall_art: [null],
  pixel_art_huge: [null], pixel_art_wide: [null],
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
 // Crops and robots as they are now (produce.png): the ripe plants of the harvest; robots of the fifty.
 crops: [["produce", 7, 2, 1, 1], ["produce", 1, 2, 1, 1], ["produce", 1, 9, 1, 1], ["produce", 1, 5, 1, 1], ["produce", 1, 6, 1, 1], ["produce", 1, 3, 1, 1], ["produce", 1, 7, 1, 1], ["produce", 1, 1, 1, 1]],
  robot: [["produce", 0, 13, 1, 1], ["produce", 4, 13, 1, 1], ["produce", 8, 13, 1, 1], ["produce", 0, 14, 1, 1], ["produce", 4, 14, 1, 1], ["produce", 8, 14, 1, 1], ["produce", 0, 15, 1, 1], ["produce", 4, 15, 1, 1], ["produce", 8, 15, 1, 1], ["produce", 0, 16, 1, 1], ["produce", 4, 16, 1, 1], ["produce", 8, 16, 1, 1], ["produce", 0, 17, 1, 1]],
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
// A progress widget and a great piece: their sizes their own (playItemSize) - these only if none is given.
Object.assign(PLAY_SIZES, { widget: [2, 2], piece: [3, 3] });
/* MADE TO HANG (agent/playworld.py; the user, 2026-11-19: "现在为什么还是大量用灯笼这个挂件"): drawn here - a chandelier,
   a hanging plant, a wind chime, a birdcage, a mobile, a hanging cloth. */
const PLAY_HANGINGS = {
  chandelier: [2, 1], hanging_plant: [1, 1], wind_chime: [1, 1], birdcage: [1, 1], mobile: [1, 1], hanging_cloth: [1, 2],
  crystal_chandelier: [2, 2], pumpkin_lantern: [1, 1],
};
Object.assign(PLAY_SIZES, PLAY_HANGINGS);
/* A THEME'S THING TO HANG (play/hangings.json): its cord, its shape in its colour, its glow if it has one. */
function playThemeHangingDraw(c, look, x, y) {
  const tall = ["lantern", "banner", "net"].includes(look.shape), H = tall ? 32 : 16, cx = x + 8, top = y + 3;
  const colour = /^#[0-9a-f]{6}$/i.test(look.colour || "") ? look.colour : "#c9a227";
  c.save();
  if (look.glow) { c.fillStyle = look.glow; c.globalAlpha = 0.25; c.beginPath(); c.arc(cx, y + H * 0.6, 9, 0, Math.PI * 2); c.fill(); c.globalAlpha = 1; }
  c.strokeStyle = "#4a3424"; c.lineWidth = 1; c.beginPath(); c.moveTo(cx, y); c.lineTo(cx, top); c.stroke();
  c.fillStyle = colour; c.strokeStyle = "rgba(0, 0, 0, 0.45)";
  const path = (pts) => { c.beginPath(); pts.forEach(([px, py], i) => (i ? c.lineTo(x + px, y + py) : c.moveTo(x + px, y + py))); c.closePath(); c.fill(); c.stroke(); };
  const ball = (px, py, r, fill = colour) => { c.fillStyle = fill; c.beginPath(); c.arc(x + px, y + py, r, 0, Math.PI * 2); c.fill(); c.stroke(); };
  const s = look.shape;
  if (s === "ball") ball(8, 9, 4.5);
  else if (s === "lantern") { c.fillRect(x + 4, top + 3, 8, H - 12); c.strokeRect(x + 4.5, top + 3.5, 7, H - 13); c.fillStyle = "#3a2a1a"; c.fillRect(x + 5, top + 1, 6, 2); c.fillRect(x + 5, top + H - 9, 6, 2); if (look.glow) { c.fillStyle = look.glow; c.fillRect(x + 6, top + 6, 4, H - 18); } }
  else if (s === "star") path([[8, 3], [9.8, 7.5], [14.5, 7.8], [10.8, 10.8], [12, 15], [8, 12.6], [4, 15], [5.2, 10.8], [1.5, 7.8], [6.2, 7.5]]);
  else if (s === "heart") { c.beginPath(); c.moveTo(x + 8, y + 14); c.bezierCurveTo(x + 1, y + 9, x + 3, y + 3, x + 8, y + 6.5); c.bezierCurveTo(x + 13, y + 3, x + 15, y + 9, x + 8, y + 14); c.fill(); c.stroke(); }
  else if (s === "bell") { c.beginPath(); c.moveTo(x + 3, y + 13); c.quadraticCurveTo(x + 4, y + 4, x + 8, y + 4); c.quadraticCurveTo(x + 12, y + 4, x + 13, y + 13); c.closePath(); c.fill(); c.stroke(); if (look.glow) ball(8, 13, 1.6, look.glow); }
  else if (s === "cage") { c.strokeStyle = colour; for (const px of [4, 8, 12]) { c.beginPath(); c.moveTo(x + px, y + 5); c.lineTo(x + px, y + 14); c.stroke(); } c.beginPath(); c.arc(x + 8, y + 9, 5, Math.PI, 0); c.stroke(); c.fillStyle = colour; c.fillRect(x + 3, y + 14, 10, 1.5); }
  else if (s === "ring") { c.strokeStyle = colour; c.lineWidth = 2.2; c.beginPath(); c.arc(x + 8, y + 9.5, 5, 0, Math.PI * 2); c.stroke(); if (look.glow) { c.strokeStyle = look.glow; c.lineWidth = 0.8; c.stroke(); } }
  else if (s === "drop" || s === "crystal") path(s === "drop" ? [[8, 4], [11.5, 10], [8, 15], [4.5, 10]] : [[8, 3], [11, 7], [10, 14], [6, 14], [5, 7]]);
  else if (s === "flags") { c.strokeStyle = "#4a3424"; c.beginPath(); c.moveTo(x, y + 4); c.quadraticCurveTo(x + 8, y + 8, x + 16, y + 4); c.stroke(); const cs = [colour, "#f2c230", "#3a6ea5", "#3a9a6a"]; [1, 5, 9, 13].forEach((px, k) => { c.fillStyle = cs[k % 4]; c.beginPath(); c.moveTo(x + px, y + 5 + (k % 2)); c.lineTo(x + px + 3, y + 5 + (k % 2)); c.lineTo(x + px + 1.5, y + 10); c.fill(); }); }
  else if (s === "bulb") { c.fillStyle = "#3a3a40"; c.fillRect(x + 6, top, 4, 3); ball(8, 9, 3.5, look.glow || colour); }
  else if (s === "skull") { ball(8, 8.5, 4.5, colour); c.fillStyle = "#2a2a2a"; c.fillRect(x + 5.5, y + 7, 2, 2); c.fillRect(x + 8.5, y + 7, 2, 2); c.fillRect(x + 7, y + 11, 2, 2); }
  else if (s === "bat") path([[8, 7], [10, 5], [11, 7], [15, 6], [13, 10], [10, 9], [8, 12], [6, 9], [3, 10], [1, 6], [5, 7], [6, 5]]);
  else if (s === "planet") { ball(8, 9, 4); c.strokeStyle = "#e8e0cc"; c.beginPath(); c.ellipse(x + 8, y + 9, 7, 2, -0.3, 0, Math.PI * 2); c.stroke(); }
  else if (s === "fish") { c.beginPath(); c.ellipse(x + 7, y + 9, 4.5, 2.8, 0, 0, Math.PI * 2); c.fill(); c.stroke(); path([[11, 9], [15, 6], [15, 12]]); }
  else if (s === "leaf") { for (const [px, py] of [[4, 6], [8, 9], [12, 6], [6, 12], [10, 13]]) { c.fillStyle = colour; c.beginPath(); c.ellipse(x + px, y + py, 2.6, 1.5, 0.6, 0, Math.PI * 2); c.fill(); } }
  else if (s === "paper") { c.beginPath(); c.ellipse(x + 8, y + 9.5, 5, 5.5, 0, 0, Math.PI * 2); c.fill(); c.stroke(); c.strokeStyle = "rgba(0, 0, 0, 0.2)"; for (const dy of [-2.5, 0, 2.5]) { c.beginPath(); c.moveTo(x + 3.5, y + 9.5 + dy); c.lineTo(x + 12.5, y + 9.5 + dy); c.stroke(); } }
  else if (s === "pot") { c.fillRect(x + 4, y + 8, 8, 5); c.strokeRect(x + 4.5, y + 8.5, 7, 4); for (const [px, py] of [[4, 8], [8, 6], [12, 8], [6, 14], [11, 14]]) { c.fillStyle = "#4f8a2f"; c.beginPath(); c.arc(x + px, y + py, 2, 0, Math.PI * 2); c.fill(); } }
  else if (s === "net") { c.strokeStyle = colour; for (let k = 2; k <= 14; k += 3) { c.beginPath(); c.moveTo(x + k, y + 3); c.lineTo(x + 8, y + H - 4); c.stroke(); } for (let k = 8; k < H - 4; k += 5) { c.beginPath(); c.moveTo(x + 3, y + k); c.lineTo(x + 13, y + k); c.stroke(); } }
  else if (s === "gear") { c.beginPath(); for (let k = 0; k < 16; k += 1) { const a = k * Math.PI / 8, r = k % 2 ? 4 : 5.5; c.lineTo(x + 8 + Math.cos(a) * r, y + 9.5 + Math.sin(a) * r); } c.closePath(); c.fill(); c.stroke(); ball(8, 9.5, 1.5, "#3a3a40"); }
  else if (s === "drone") { c.fillRect(x + 5, y + 8, 6, 3); c.strokeStyle = "#3a3a40"; c.beginPath(); c.moveTo(x + 1, y + 7); c.lineTo(x + 6, y + 7); c.moveTo(x + 10, y + 7); c.lineTo(x + 15, y + 7); c.stroke(); if (look.glow) ball(8, 12.5, 1.2, look.glow); }
  else if (s === "cloud") { for (const [px, py, r] of [[5, 10, 3], [8, 8, 4], [11.5, 10, 3]]) ball(px, py, r); }
  else if (s === "feather") { c.beginPath(); c.ellipse(x + 8, y + 9.5, 2.2, 5.5, 0.4, 0, Math.PI * 2); c.fill(); c.stroke(); }
  else if (s === "snowflake") { c.strokeStyle = colour; c.lineWidth = 1.3; for (let k = 0; k < 3; k += 1) { const a = k * Math.PI / 3; c.beginPath(); c.moveTo(x + 8 - Math.cos(a) * 5, y + 9.5 - Math.sin(a) * 5); c.lineTo(x + 8 + Math.cos(a) * 5, y + 9.5 + Math.sin(a) * 5); c.stroke(); } }
  else if (s === "moon") { ball(8, 9.5, 5); c.fillStyle = "rgba(20, 20, 40, 0.85)"; c.beginPath(); c.arc(x + 10.5, y + 8, 4.2, 0, Math.PI * 2); c.fill(); }
  else if (s === "shell") { c.beginPath(); c.moveTo(x + 3, y + 13); c.quadraticCurveTo(x + 8, y + 2, x + 13, y + 13); c.closePath(); c.fill(); c.stroke(); }
  else if (s === "mushroom") { c.beginPath(); c.arc(x + 8, y + 10, 5, Math.PI, 0); c.fill(); c.stroke(); c.fillStyle = "#f7f4ec"; c.fillRect(x + 7, y + 10, 2, 4); }
  else if (s === "book") { c.fillRect(x + 3, y + 6, 10, 7); c.strokeRect(x + 3.5, y + 6.5, 9, 6); c.fillStyle = "#f7f4ec"; c.fillRect(x + 8, y + 6.5, 0.8, 6); }
  else if (s === "candle") { for (const px of [4, 8, 12]) { c.fillStyle = colour; c.fillRect(x + px - 1, y + 7 + (px % 3), 2, 6); c.fillStyle = look.glow || "#ffd88a"; c.beginPath(); c.arc(x + px, y + 6 + (px % 3), 1.2, 0, Math.PI * 2); c.fill(); } }
  else if (s === "banner") { c.fillRect(x + 3, top + 1, 10, H - 9); c.strokeRect(x + 3.5, top + 1.5, 9, H - 10); c.beginPath(); c.moveTo(x + 3, top + H - 8); c.lineTo(x + 8, top + H - 5); c.lineTo(x + 13, top + H - 8); c.fill(); if (look.glow) { c.fillStyle = look.glow; c.fillRect(x + 5, top + 5, 6, 2); } }
  else ball(8, 9, 4);
  c.restore();
}
function playHangingDraw(c, kind, x, y) {
  const T = 16;
  const line = (x0, y0, x1, y1, colour, width = 1) => { c.strokeStyle = colour; c.lineWidth = width; c.beginPath(); c.moveTo(x + x0, y + y0); c.lineTo(x + x1, y + y1); c.stroke(); };
  const dot = (px, py, r, colour) => { c.fillStyle = colour; c.beginPath(); c.arc(x + px, y + py, r, 0, Math.PI * 2); c.fill(); };
  if (kind === "chandelier") {
    line(T, 0, T, 5, "#6b5a3a");
    c.fillStyle = "#c9a227"; c.fillRect(x + 6, y + 5, 2 * T - 12, 3);
    for (const px of [7, 12, 16, 20, 25]) { line(px, 8, px, 11, "#c9a227"); dot(px, 6 + 7, 1.6, "#fff4c2"); }
    c.fillStyle = "rgba(255, 240, 190, 0.25)"; c.beginPath(); c.arc(x + T, y + 11, 10, 0, Math.PI * 2); c.fill();
  } else if (kind === "hanging_plant") {
    line(8, 0, 4, 7, "#6b5a3a"); line(8, 0, 12, 7, "#6b5a3a");
    c.fillStyle = "#a0662f"; c.fillRect(x + 4, y + 7, 8, 4);
    for (const [px, py] of [[4, 11], [7, 13], [10, 12], [12, 14], [6, 15]]) dot(px, py, 2, "#4f8a2f");
  } else if (kind === "wind_chime") {
    line(8, 0, 8, 3, "#6b5a3a"); c.fillStyle = "#8b5a2b"; c.fillRect(x + 3, y + 3, 10, 2);
    for (const [px, len] of [[4, 7], [6, 9], [8, 11], [10, 9], [12, 7]]) line(px, 5, px, 5 + len, "#c8ccd2", 1.4);
  } else if (kind === "birdcage") {
    line(8, 0, 8, 3, "#6b5a3a");
    c.strokeStyle = "#c9a227"; c.lineWidth = 1;
    c.beginPath(); c.arc(x + 8, y + 9, 5, Math.PI, 0); c.lineTo(x + 13, y + 15); c.lineTo(x + 3, y + 15); c.closePath(); c.stroke();
    for (const px of [5, 8, 11]) line(px, 5, px, 15, "#c9a227");
    dot(8, 11, 2, "#f2c230");
  } else if (kind === "mobile") {
    line(8, 0, 8, 4, "#6b5a3a"); line(2, 4, 14, 4, "#6b5a3a");
    for (const [px, colour] of [[2, "#d94a3a"], [8, "#3a6ea5"], [14, "#f2c230"]]) { line(px, 4, px, 9, "#6b5a3a"); dot(px, 11, 2.2, colour); }
  } else if (kind === "crystal_chandelier") {
    // A CRYSTAL CHANDELIER (the user: "水晶吊灯"): tiers of gilt arms, drops of glass catching the light, a glow round it.
    c.fillStyle = "rgba(255, 245, 210, 0.22)"; c.beginPath(); c.arc(x + T, y + 14, 15, 0, Math.PI * 2); c.fill();
    line(T, 0, T, 4, "#8a7440");
    for (const [w, yy] of [[10, 6], [16, 12], [22, 18]]) {
      c.fillStyle = "#d4b24a"; c.fillRect(x + T - w / 2, y + yy, w, 2);
      for (let k = 0; k <= w; k += 3) {
        const px = T - w / 2 + k;
        line(px, yy + 2, px, yy + 5, "rgba(220, 240, 255, 0.9)");
        dot(px, yy + 6, 1.3, k % 6 ? "#e8f6ff" : "#bfe3ff");
      }
      for (let k = 0; k <= w; k += w / 2) dot(T - w / 2 + k, yy - 1, 1.2, "#fff6c8");   // its candles
    }
    dot(T, 28, 2.2, "#dff2ff");
  } else if (kind === "pumpkin_lantern") {
    // A PUMPKIN LANTERN (the user: "南瓜灯"), hung on a cord: its ribbed shell, its carved face lit from inside.
    line(8, 0, 8, 4, "#4a3424");
    c.fillStyle = "#e07b1f"; c.beginPath(); c.ellipse(x + 8, y + 10, 6.5, 5.5, 0, 0, Math.PI * 2); c.fill();
    c.strokeStyle = "#b35a10"; c.lineWidth = 1;
    for (const dx of [-3, 0, 3]) { c.beginPath(); c.ellipse(x + 8 + dx, y + 10, 2.2, 5.2, 0, 0, Math.PI * 2); c.stroke(); }
    c.fillStyle = "#4f6b2a"; c.fillRect(x + 7, y + 3.5, 2, 2);
    c.fillStyle = "#ffd85a";
    c.beginPath(); c.moveTo(x + 4.5, y + 9); c.lineTo(x + 6.5, y + 7.5); c.lineTo(x + 7, y + 9.5); c.fill();
    c.beginPath(); c.moveTo(x + 11.5, y + 9); c.lineTo(x + 9.5, y + 7.5); c.lineTo(x + 9, y + 9.5); c.fill();
    c.fillRect(x + 5, y + 12, 6, 1.6);
    c.fillStyle = "rgba(255, 200, 80, 0.2)"; c.beginPath(); c.arc(x + 8, y + 10, 9, 0, Math.PI * 2); c.fill();
  } else if (kind === "hanging_cloth") {
    line(2, 1, 14, 1, "#6b5a3a", 2);
    c.fillStyle = "#8e3b46"; c.fillRect(x + 3, y + 2, 10, 2 * T - 6);
    c.fillStyle = "#d9b44a"; c.fillRect(x + 3, y + 8, 10, 2); c.fillRect(x + 3, y + 2 * T - 10, 10, 2);
    c.beginPath(); c.moveTo(x + 3, y + 2 * T - 4); c.lineTo(x + 8, y + 2 * T - 1); c.lineTo(x + 13, y + 2 * T - 4); c.fillStyle = "#8e3b46"; c.fill();
  }
}
PLAY_SIZES.paint = [1, 1];                     // a tile of a picture laid in things
PLAY_SIZES.frame = [2, 2];
PLAY_SIZES.rug = [3, 2];
Object.assign(PLAY_SIZES, { statue_of: [1, 2], banner: [1, 2], poster: [2, 2], pixel_art: [1, 1], pixel_art_large: [2, 2], wall_art: [2, 1],
                            pixel_art_huge: [4, 4], pixel_art_wide: [6, 3] });
const PLAY_UNDER_KINDS = new Set(["rug", "parasol", "flowerbed", "paint"]);       // walked over (a rug drawn first)
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
                      apartment: "wood", lecture_hall: "wood", estate: "grass", sea_tunnel: "water", roller_coaster: "grass",
                      cemetery: "grass2", greenhouse: "grass", funfair: "paving" };
/* OPEN PLACES, no walls (agent/playplaces.py OUTDOOR): their edge, and a pool's water in its middle. */
const PLAY_OUTDOOR = { garden: "flowers", park: "grass2", pool: "paving", courtyard: "paving", terrace: "paving",
                       lake: "grass2", waterfall: "rock2", plaza: "cobble", grove: "grass", meadow: "grass2", mountain: "grass2",
                       volcano: "rock2", peak: "grass2", sea_tunnel: "water", roller_coaster: "grass2", cemetery: "dirt2", estate: "grass2",
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
                                                          y: +play.me.y.toFixed(2), facing: play.me.facing, area: play.area }));
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
  box.innerHTML = `<canvas class="side-play-canvas" tabindex="0" data-play-label="Your world - walk with the arrow keys" aria-label="${escapeHtml(playWords("Your world - walk with the arrow keys"))}"></canvas>
    <p class="side-play-hint" data-play-say="Arrow keys or WASD to walk, Shift to run, Space to jump, E to look, M for the map">${escapeHtml(playWords("Arrow keys or WASD to walk, Shift to run, Space to jump, E to look, M for the map"))}</p>
    <p class="side-play-off" hidden data-play-say="Your world is not growing - switched off in Settings > Modules > Play world.">${escapeHtml(playWords("Your world is not growing - switched off in Settings > Modules > Play world."))}</p>
    <div class="side-play-corner"><p class="side-play-growing" hidden></p>
    <canvas class="side-play-mini" width="336" height="256" data-play-label="Map of your world" aria-label="${escapeHtml(playWords("Map of your world"))}"
      data-play-title="Map of your world - press or drag to look there, an arrow key to come back; press twice to make it larger or smaller" title="${escapeHtml(playWords("Map of your world - press or drag to look there, an arrow key to come back; press twice to make it larger or smaller"))}"></canvas></div>
    <div class="side-play-card" hidden></div>
    <div class="side-play-zoom"><button type="button" data-play-zoom="-1" data-play-label="Zoom out" aria-label="${escapeHtml(playWords("Zoom out"))}"
      title="${escapeHtml(playWords("Zoom out"))}">−</button><button type="button" data-play-zoom="1" data-play-label="Zoom in" aria-label="${escapeHtml(playWords("Zoom in"))}"
      title="${escapeHtml(playWords("Zoom in"))}">+</button><button type="button" data-play-map data-play-label="Map (M)" aria-label="${escapeHtml(playWords("Map (M)"))}"
      title="${escapeHtml(playWords("Map (M)"))}">▦</button><button type="button" data-play-unstuck data-play-label="Go to the middle of the view" aria-label="${escapeHtml(playWords("Go to the middle of the view"))}"
      title="${escapeHtml(playWords("Go to the middle of the view"))}">⤨</button><button type="button" data-play-sound data-play-label="Sound" aria-label="${escapeHtml(playWords("Sound"))}"
      title="${escapeHtml(playWords("Sound"))}">♪</button><button type="button" data-play-language data-play-label="Language of the world" aria-label="${escapeHtml(playWords("Language of the world"))}"
      title="${escapeHtml(playWords("Language of the world"))}">${escapeHtml((PLAY_LANGUAGES.find(([code]) => code === playLanguage()) || PLAY_LANGUAGES[0])[1])}</button></div>`;
  document.body.appendChild(box);
  play.el = box;
  play.canvas = box.querySelector("canvas");
  play.ctx = play.canvas.getContext("2d");
  play.mini = box.querySelector(".side-play-mini");
  try { play.mini.hidden = localStorage.getItem("playMini") === "off"; } catch (_err) { /* shown */ }
  // A PRESS LOOKS THERE (the user, 2026-11-18: "你现在小地图允许我用鼠标点，view会跳到我小地图上看的那个位置（所以我的小地图
  // 上面移动可以看全景）。然后我操作上下左右的时候又跳回agent视角"): pressed, or dragged over, the view goes where it points;
  // a step brings it back to the walker. Pressed twice, larger or smaller - never hidden (2026-11-17: "现在小地图怎么一点
  // 就没有了"); the map button in the corner, or M, hides and shows it.
  let looking = false;
  // THE LEFT BUTTON ALONE (the user, 2026-11-19: "在play的小地图上，点击鼠标中键之后就变成可以移动上下作用view了"): the
  // middle one started the browser's own scrolling, its release never came, and the view went on following the mouse.
  play.mini.addEventListener("mousedown", (event) => { if (event.button === 1) event.preventDefault(); });
  play.mini.addEventListener("auxclick", (event) => event.preventDefault());
  play.mini.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    looking = true; play.mini.setPointerCapture?.(event.pointerId); playMiniLook(event);
  });
  play.mini.addEventListener("pointermove", (event) => { if (looking && (event.buttons & 1)) playMiniLook(event); else looking = false; });
  play.mini.addEventListener("pointerup", () => { looking = false; play.canvas.focus({ preventScroll: true }); });
  play.mini.addEventListener("pointercancel", () => { looking = false; });
  play.mini.addEventListener("dblclick", () => playMiniLarger());
  try { play.mini.classList.toggle("large", localStorage.getItem("playMiniLarge") === "on"); } catch (_err) { /* small */ }
  new ResizeObserver(() => playFit()).observe(box);
  play.canvas.addEventListener("pointerdown", () => play.canvas.focus());
  play.canvas.addEventListener("blur", () => setTimeout(() => playNote(`the world lost the keys to ${playNoteKeys()}`), 0));
  try { play.zoom = Number(localStorage.getItem(PLAY_ZOOM_KEY)) || 0; } catch (_err) { play.zoom = 0; }
  box.querySelector(".side-play-zoom").addEventListener("click", (event) => {
    const step = event.target.closest && event.target.closest("[data-play-zoom]");
    if (step) playZoom(Number(step.dataset.playZoom));
    if (event.target.closest && event.target.closest("[data-play-sound]")) playSoundPanel(box);
    if (event.target.closest && event.target.closest("[data-play-language]")) playLanguagePanel(box);
    if (event.target.closest && event.target.closest("[data-play-unstuck]")) playUnstuck(true);
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

/* WHAT HAPPENED IN ITS FIRST SECONDS, TOLD TO THE AGENT'S LOG (the user, 2026-11-18: "刚刚又出现了，开始的时候play不能操控"
   - not seen where it was tried): when it opened and loaded, each key pressed - where the keys were, whether the
   panel had them at all, whether the walker moved - and any frame that failed; told once, ten seconds after it
   opened. Keys' names and focus only, nothing of what is in it. */
// WATCHED LONGER (the user, 2026-11-19: "又加载play卡住了"; "点一下 ... 好像没用，过了几秒才有用"): its first minute, not ten
// seconds - and what holds the page: each task of the page longer than PLAY_LONG_TASK_MS, the window's focus coming and
// going, each load of the world and how long it took, each frame slower than PLAY_SLOW_FRAME_MS.
const PLAY_WATCH_MS = 60000, PLAY_NOTES_MOST = 150, PLAY_LONG_TASK_MS = 200, PLAY_SLOW_FRAME_MS = 120;
const playFirst = { at: 0, sent: true, notes: [] };
function playNote(what) {
  if (playFirst.sent || playFirst.notes.length > PLAY_NOTES_MOST) return;
  playFirst.notes.push(`${Math.round(performance.now() - playFirst.at)}ms ${what}`);
}
function playNoteKeys() {
  const one = document.activeElement;
  return `${one ? one.tagName.toLowerCase() + (one.id ? "#" + one.id : "") + (one.className && typeof one.className === "string" ? "." + one.className.split(" ")[0] : "") : "none"} panel-has-keys=${document.hasFocus()}`;
}
function playNoteSend() {
  if (playFirst.sent) return;
  playFirst.sent = true;
  api.post("/api/play/note", { notes: playFirst.notes.slice(0, PLAY_NOTES_MOST) }).catch(() => {});
}
(function playWatchThePage() {
  try {
    new PerformanceObserver((list) => {
      for (const one of list.getEntries()) {
        if (one.duration >= PLAY_LONG_TASK_MS) playNote(`the page held ${Math.round(one.duration)}ms (from ${Math.round(one.startTime - playFirst.at)}ms)`);
      }
    }).observe({ type: "longtask", buffered: false });
  } catch (_err) { /* not in this browser */ }
  window.addEventListener("blur", () => playNote(`the window lost the keys; keys at ${playNoteKeys()}`));
  window.addEventListener("focus", () => playNote(`the window has the keys again; keys at ${playNoteKeys()}`));
})();

async function playOpen() {
  // THE CHAT'S "ASK ABOUT" PUT AWAY WITH THE CHAT (the user, 2026-11-19: "你现在chat的ask about 在切换进play之后还留在屏幕上"):
  // the button over what was picked, and the picking itself - the world over the chat now.
  document.querySelectorAll(".side-ask, #fu-pick").forEach((one) => one.remove());
  try { window.getSelection()?.removeAllRanges(); } catch (_err) { /* nothing picked */ }
  playFirst.at = performance.now();
  playFirst.sent = false;
  playFirst.notes = [];
  playNote(`opened; keys at ${playNoteKeys()}`);
  setTimeout(playNoteSend, PLAY_WATCH_MS);
  const box = playEl();
  playPlace();
  box.hidden = false;
  play.on = true;
  document.body.classList.add("side-playing");
  if (typeof sideBoard !== "undefined" && sideBoard.open && typeof sideBoardClose === "function") sideBoardClose();   // no small screen in play
  playPressed();
  playAvatarOff(true);
  playSoundStart();                            // opened by a press: the page may make sound now
  play.canvas.focus({ preventScroll: true });  // the keys the world's at once, not the button's while it loads
  await playLoadSheets();
  await playLoadWorld();
  // A CLOSE LOOK ON COMING IN (the user, 2026-11-17: "每次刚进入play mode的时候，不要给全景，要给一个把avatar放中间的比较narrow的
  // view"): the walker in the middle, a step nearer than the usual - however far it was zoomed out before.
  play.zoom = PLAY_ZOOM_ON_OPEN;
  playFit();
  playWhere();
  // NEVER OPENED BOXED IN (the user, 2026-11-18: "还是刚刚的问题，一开始不能移动"): a place grown full since it was left
  // may hold the walker where no step is free - moved, at once, to free ground near.
  if ([[0.35, 0], [-0.35, 0], [0, 0.35], [0, -0.35]].every(([dx, dy]) => playBlocked(play.me.x + dx, play.me.y + dy))) playUnstuck();
  playDraw();                                  // drawn again with its place known: its sky from the first look
  playSoundRoom(playMusicHeard());
  play.canvas.focus({ preventScroll: true });
  playNote(`loaded; walker ${play.me.x.toFixed(1)},${play.me.y.toFixed(1)} boxed=${[[0.35, 0], [-0.35, 0], [0, 0.35], [0, -0.35]]
    .every(([dx, dy]) => playBlocked(play.me.x + dx, play.me.y + dy))}; keys at ${playNoteKeys()}`);
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
  if (document.hidden) playSoundStop(); else { playSoundStart(); playSoundRoom(playMusicHeard()); }
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
function playPages(text, most) {
  const pages = [];
  let rest = String(text || "").trim();
  while (rest.length > most) {
    let cut = Math.max(rest.lastIndexOf(". ", most), rest.lastIndexOf("。", most), rest.lastIndexOf("！", most), rest.lastIndexOf("? ", most));
    if (cut < most * 0.4) cut = rest.lastIndexOf(" ", most);
    if (cut < most * 0.4) cut = most - 1;
    pages.push(rest.slice(0, cut + 1).trim());
    rest = rest.slice(cut + 1).trim();
  }
  if (rest) pages.push(rest);
  return pages;
}
function playSayPage(pages, page) {
  const more = page < pages.length - 1;
  const words = more ? `${pages[page]}  ▸ E` : pages[page];
  play.said = { text: words, pages, page, at: performance.now(), until: performance.now() + playSayFor(words) + (more ? 4000 : 0),
                x: play.me.x, y: play.me.y };
}
/* How long something said stays (the user, 2026-11-17: "E之后打开的look持续的时间太长了"): short, at most five seconds;
   gone at once if E is pressed again or the walker steps away. */
// Three seconds longer than it was (the user, 2026-11-17: "look agent 说的话（游戏里) 稍微多留3秒").
function playSayFor(text) { return Math.min(8000, 4200 + text.length * 35); }
function playLook() {
  if (typeof playAct === "function" && playAct()) { playDraw(); return; }   // done (play-act.js): sat, lit, cast, gone
  if (play.said && performance.now() < play.said.until) {
    const said = play.said;
    if (said.pages && said.page < said.pages.length - 1) { playSayPage(said.pages, said.page + 1); playRun(); return; }   // the next page
    play.said = null; playDraw(); return;                                                     // pressed again: gone
  }
  const near = play.near;
  if (near && !near.at && /book|shelf|desk|map|scroll/.test(near.kind)) playSfx("book-flip", 0.45);
  const place = play.zone || play.region;
  const sign = near && near.at ? (near.zone || near.region) : null;
  const text = sign ? (sign.why || near.name || "") : near ? (near.why || near.name || "") : (place && (place.why || place.name)) || "";
  if (!text) return;
  // PAGED, NEVER CUT (the user, 2026-11-18: "agent look还有省略内容，不要省略 ... 省略继续展开"): what is longer than a
  // bubble is said a page at a time; E turns to the next, after the last it is gone.
  const pages = playPages(text, PLAY_SAY_MOST);
  playSayPage(pages, 0);
  // IN THE LANGUAGE CHOSEN, AT ONCE (the user, 2026-11-19: "你play切换语言的时候，你look生成的话也要换语言"): looked at before
  // the rounds of translation reached it, it is put into it now - said again in it while it is still being looked at.
  const holder = sign || near || place;
  const key = holder && holder.id ? `${near && !sign ? "items" : holder.ox !== undefined ? "regions" : "zones"}:${holder.id}` : "";
  if (key && playNotIn(text, playLanguage())) {
    const shown = play.said;
    api.post("/api/play/translate", { key }).then((said) => {
      const words = (said && said.words) || {};
      for (const field of PLAY_WORDS) if (words[field]) holder[field] = words[field];
      const again = sign ? (sign.why || near.name || "") : near ? (near.why || near.name || "") : (holder.why || holder.name || "");
      if (again && again !== text && play.said === shown) { playSayPage(playPages(again, PLAY_SAY_MOST), 0); playRun(); }
    }).catch(() => {});
  }
  playSoundNear();
  playRun();
}

/* WORDS WHOLE ON A LINE (the user, 2026-11-18: "你现在英文显示怎么老把一个词拆成2行显示 在look的时候"): a line broken
   between words - Chinese between any two characters, as it is written - and a word longer than a whole line alone
   broken within it. Each line is the text's own run, so what is shown a letter at a time keeps its count. */
function playWrapped(ctx, text, most) {
  const parts = String(text).match(/[⺀-鿿豈-﫿＀-￯　-〿]|\s+|[^\s⺀-鿿豈-﫿＀-￯　-〿]+/g) || [];
  const lines = [];
  let line = "";
  const fits = (words) => ctx.measureText(words).width <= most;
  for (const part of parts) {
    if (fits(line + part.replace(/\s+$/, "")) || !line.trim()) {
      if (!fits(line + part) && !line.trim() && !/^\s+$/.test(part)) {
        for (const ch of part) {                       // a word longer than a line: broken within it
          if (!fits(line + ch) && line) { lines.push(line); line = ch; } else line += ch;
        }
      } else line += part;
    } else {
      lines.push(line);
      line = part;
    }
  }
  if (line) lines.push(line);
  return lines;
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
  const lines = playWrapped(ctx, said.text, most);   // wrapped as the whole will be, so it does not jump about
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
   free spot, free all round so it is not another corner; one on a road first if that ring has one.
   THE BUTTON TAKES THE WALKER TO THE MIDDLE OF THE VIEW (the user, 2026-11-19: "stuck那个改成在view的中心（或者附近，
   如果中心不能站）刷新，这样我可以直接到任意地方"): where the view is - the walker, or wherever the minimap was pressed or
   dragged to - its middle if free, else the nearest free spot round it; the view then follows the walker again. */
const PLAY_UNSTUCK_RINGS = [2, 3, 4, 5, 6];
const PLAY_TO_VIEW_RINGS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14, 16, 20, 24];
function playUnstuck(toView = false) {
  const map = play.map;
  if (!map) return;
  const middle = toView && play.lookAt ? play.lookAt : [play.me.x, play.me.y];
  const mx = Math.floor(middle[0]), my = Math.floor(middle[1]);
  let pool = [];
  for (const ring of toView ? PLAY_TO_VIEW_RINGS : PLAY_UNSTUCK_RINGS) {
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
  if (toView) {
    play.sit = null;                           // up from any seat, and the view on the walker where it lands
    play.lookAt = null;
  }
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
/* WHAT ROOMS ARE MADE OF (play/materials.json; agent/playmade.py): its floors' tiles drawn from their SVG into pixels
   (each 16 x 16, its colours brought to a palette of its own), its walls a wall set and a wash; the agent's own made
   floors beside them (said.made). Loaded with the sheets, so a room is laid in its own. */
let PLAY_ROOM_MATERIALS = { floors: {}, walls: {} };
function playMaterialTiles(floors, store = "materialTiles") {
  const tiles = play[store] = play[store] || {};
  for (const [name, one] of Object.entries(floors || {})) {
    if (!one || !one.svg || tiles[name]) continue;
    tiles[name] = "loading";
    play.tilesLoading = (play.tilesLoading || 0) + 1;
    playSvgImage(one.svg, (image) => {
      if (image) tiles[name] = playFinePixel(image, 16, 16, { outline: false, colours: 12 });
      else delete tiles[name];
      play.tilesLoading = Math.max(0, (play.tilesLoading || 1) - 1);
      if (!play.tilesLoading) playTilesLaidAgain();
    });
  }
  // (one that never answers: laid again all the same, a moment later)
  clearTimeout(play.tilesFallback);
  play.tilesFallback = setTimeout(() => { if (play.tilesLoading) { play.tilesLoading = 0; playTilesLaidAgain(); } }, 4000);
}
/* THE WORLD LAID AGAIN ONCE, WHEN THE MATERIALS' TILES ARE ALL IN (the user, 2026-11-19: "刚刚又卡了" - laid again for each
   of the 47 tiles as it came, about 300 ms each, the first seconds held again and again). */
function playTilesLaidAgain() {
  if (play.wallTiles) play.wallTiles.clear();
  if (play.chunks) play.chunks.clear();
  if (play.on) playDraw();
}
/* A room's floor as a piece of ground: a built-in one by its own name, any other "mat:" and its name. */
function playFloorPiece(name) {
  const one = (PLAY_ROOM_MATERIALS.floors || {})[name] || ((play.made && play.made.floors) || {})[name];
  if (!one) return "";
  return one.builtin || `mat:${name}`;
}
function playLoadSheets() {
  if (!play.materialsLoaded) {
    play.materialsLoaded = fetch("/play/materials.json").then((r) => (r.ok ? r.json() : {})).then((said) => {
      PLAY_ROOM_MATERIALS = { floors: (said && said.floors) || {}, walls: (said && said.walls) || {} };
      playMaterialTiles(PLAY_ROOM_MATERIALS.floors);
      playMaterialTiles(PLAY_ROOM_MATERIALS.walls, "wallFaces");     // each wall's own face (playWallTile)
    }).catch(() => {});
  }
  // EACH THEME'S THINGS TO HANG (play/hangings.json): their shapes and colours, drawn here (playThemeHangingDraw).
  if (!play.themeHangings) {
    play.themeHangings = {};
    fetch("/play/hangings.json").then((r) => (r.ok ? r.json() : {})).then((said) => {
      for (const ones of Object.values((said && said.hangings) || {})) {
        for (const [name, shape, colour, glow] of ones) {
          play.themeHangings[name] = { shape, colour, glow };
          PLAY_SIZES[name] = ["lantern", "banner", "net"].includes(shape) ? [1, 2] : [1, 1];
        }
      }
      if (play.chunks) play.chunks.clear();
      if (play.on) playDraw();
    }).catch(() => {});
  }
  return Promise.all([play.materialsLoaded, ...Object.entries(PLAY_SHEETS).map(([name, url]) => play.sheets[name] || new Promise((done) => {
    const one = new Image();
    one.onload = () => { play.sheets[name] = one; done(); };
    one.onerror = () => done();
    one.src = url;
  }))]);
}

/* IN THE LANGUAGE CHOSEN (the user, 2026-11-18: "你的play mode, 要给中英文都有，我可以在setting里选择dispay 中文或者英文";
   "让他做双语翻译"): each place, thing, land and project carries its words in both ("words": {"en": {...}, "zh": {...}},
   agent/playworld.py _translate); the world shown in the one chosen - what has no words of that language yet, as it
   was written. Chosen again in settings: the world shown again in it. */
const PLAY_WORDS = ["name", "why", "room_idea", "grounds_name", "summary", "floor_text", "card_title", "news_text",
                    "feeling", "to_them", "look"];      // a land's feeling and its words to them too (agent/playworld.py _words_of)
/* Whether words hold another language than the one shown (agent/playworld.py _not_in - the same test). */
function playNotIn(text, language) {
  return language === "zh" ? /[A-Za-z]{2,}/.test(String(text || "")) : /[\u3400-\u9fff]/.test(String(text || ""));
}
function playLanguage() {
  // PLAY'S OWN (the user, 2026-11-19: "选语言的setting, 要放在play panel里（这个setting是只对play panel内生效的，即使display
  // languagde还是英文，play也可以选中文）"): as the world says it is shown (its "play_language", else the display's).
  if (play.language === "zh" || play.language === "en") return play.language;
  return /^zh/i.test(document.documentElement.lang || "") ? "zh" : "en";
}
/* THE PANEL'S LANGUAGE BUTTON (the user, 2026-11-19: "language那个符号应该是点一下出来一个panel, 显示当前语言是什么并且
   切换。那个符号显示的就是当前语言"): it shows the language the world is in now; pressed, a small panel says which it is
   and offers the others - the one chosen kept as Play's own setting, the world put into it as it is translated
   (agent/playworld.py translate_all), shown a batch at a time. */
const PLAY_LANGUAGES = [["en", "EN", "English"], ["zh", "中", "中文"]];
function playLanguagePanel(box) {
  const open = box.querySelector(".side-play-language");
  if (open) { open.remove(); return; }
  const now = playLanguage();
  const panel = document.createElement("div");
  panel.className = "side-play-language";
  // The one chosen ticked (the user, 2026-11-19: "在按钮上加上打勾(selected)来表示当前是什么").
  panel.setAttribute("aria-label", playWords("Language of the world"));
  panel.innerHTML = PLAY_LANGUAGES.map(([code, , name]) => `<button type="button" data-play-language-to="${code}"${code === now
    ? ' aria-pressed="true" disabled' : ""}><span class="side-play-tick" aria-hidden="true">${code === now ? "✓" : ""}</span>${escapeHtml(name)}</button>`).join("");
  panel.addEventListener("click", (event) => {
    const to = event.target.closest && event.target.closest("[data-play-language-to]");
    if (!to || to.disabled) return;
    panel.remove();
    playLanguageSwitch(to.dataset.playLanguageTo);
  });
  box.appendChild(panel);
}
async function playLanguageSwitch(next) {
  if (next !== "zh" && next !== "en") return;
  if (next === playLanguage()) return;
  play.language = next;
  playLanguageButton();
  try { await api.post("/api/settings", { values: { play_language: next } }); } catch (err) { toast(err.message, "error"); }
  await playWordsTable();
  playLoadWorld().then(playDraw);
}
function playLanguageButton() {
  const button = play.el && play.el.querySelector("[data-play-language]");
  if (button) button.textContent = (PLAY_LANGUAGES.find(([code]) => code === playLanguage()) || PLAY_LANGUAGES[0])[1];
  playRelabel();
}
/* ITS OWN BUTTONS AND HINTS IN ITS OWN LANGUAGE (the user, 2026-11-19: "你play翻译成中文之后，你的那些按钮(比如music) 也要
   变成中文"): each said again in Play's language - the page's own translation leaves the Play view to it (i18n.js). */
function playRelabel() {
  if (!play.el) return;
  for (const one of play.el.querySelectorAll("[data-play-label]")) {
    const said = playWords(one.dataset.playLabel);
    one.setAttribute("aria-label", said);
    if (one.hasAttribute("title")) one.setAttribute("title", playWords(one.dataset.playTitle || one.dataset.playLabel));
  }
  for (const one of play.el.querySelectorAll("[data-play-say]")) one.textContent = playWords(one.dataset.playSay);
  const open = play.el.querySelector(".side-play-sound");      // the sound panel, if open: said again
  if (open && typeof playSoundPanel === "function") { open.remove(); playSoundPanel(play.el); }
}
/* Its own words in Chinese when the page's are not (the display language another): the application's table, once. */
async function playWordsTable() {
  if (playLanguage() !== "zh" || play.wordsZh) return;
  try {
    const response = await fetch("/lang/zh.json", { headers: { Accept: "application/json" } });
    if (response.ok) play.wordsZh = await response.json();
  } catch (_err) { /* its words as written */ }
}
function playInLanguage(world, language) {
  const swap = (one) => {
    if (!one || !one.words) return;
    const said = one.words[language] || {};
    for (const field of PLAY_WORDS) if (said[field]) one[field] = said[field];
    if (said.idea && one.concept) one.concept.idea = said.idea;
    if (said.design && one.muse) one.muse.design = said.design;
  };
  for (const key of ["zones", "items", "regions", "projects"]) (world[key] || []).forEach(swap);
  ((world.transport || {}).lines || []).forEach(swap);   // the lines' names too
  return world;
}
let playLanguageWatch = null;
function playWatchLanguage() {
  if (playLanguageWatch || typeof MutationObserver !== "function") return;
  playLanguageWatch = new MutationObserver(() => { if (play.on && play.world) playLoadWorld().then(playDraw); });
  playLanguageWatch.observe(document.documentElement, { attributes: true, attributeFilter: ["lang"] });
}
async function playLoadWorld() {
  playWatchLanguage();
  let said = null;
  try { said = await api.get("/api/play/world"); } catch (err) { toast(err.message, "error"); return; }
  const fresh = JSON.stringify(said.world);
  if (said.language === "zh" || said.language === "en") play.language = said.language;
  await playWordsTable();
  playLanguageButton();
  // WHAT IT IS DRAWN FROM, ALWAYS SAID (the user, 2026-11-17: "你的world 要显示，是基于截止到XX日期前的activity画的"):
  // the day the world is drawn up to - and that more is being added, while it is.
  play.upTo = String((said.world && said.world.caught_up) || "").slice(0, 10);
  // MORE COMING WHILE IT IS BEHIND (the user, 2026-11-19: "这个是停滞了吗，怎么没有adding more了"): growing now, or not yet
  // caught up - between two parts too - "adding more", and looked at again.
  play.growing = Boolean(said.growing || said.behind);
  playStatus();
  play.el.querySelector(".side-play-off").hidden = said.grows !== false;
  if (play.growing) setTimeout(() => {
    if (!play.on) return;
    const began = performance.now();
    playLoadWorld().then(() => { playDraw(); playNote(`the world loaded again in ${Math.round(performance.now() - began)}ms`); });
  }, 8000);
  const language = playLanguage();
  if (play.world && play.worldText === fresh && play.worldLanguage === language) return;
  const first = !play.world;
  play.world = playInLanguage(said.world, language);
  // THE AGENT'S MADE THINGS AND FLOORS (agent/playmade.py): each thing a kind of its size, drawn from its picture.
  play.made = said.made || { items: {}, floors: {}, walls: {} };
  for (const [name, one] of Object.entries(play.made.items || {})) if (Array.isArray(one.size)) PLAY_SIZES[name] = one.size;
  playMaterialTiles(play.made.floors);
  playMaterialTiles(play.made.walls, "wallFaces");
  play.worldText = fresh;
  play.worldLanguage = language;
  if (first) {                                 // the area the walker was left in, if the world has several
    try {
      const kept = JSON.parse(localStorage.getItem(PLAY_WHERE_KEY) || "null");
      if (kept && kept.born === play.world.born && kept.area) play.area = kept.area;
    } catch (_err) { /* the first area */ }
  }
  if (!playAreas(play.world).includes(play.area)) play.area = playAreaOf((play.world.regions || [])[0]);
  play.map = playLay(play.world, play.area);
  // The minimap drawn again for this world - out of sight, the one shown kept till the new one is whole; the first
  // time, as it comes.
  if (play.miniBase && play.mini && !play.mini.hidden) playMiniBase(true);
  else play.miniBase = null;
  play.zoneKinds = null;
  play.chunks.clear();
  if (first) {
    const home = play.map.zones.length ? play.map.zones[0].zone : play.world.zones[0];
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
/* A WALL OF ITS OWN MATERIAL (the user, 2026-11-19: "房间的墙壁是不是只有土色和灰色，加多种形态，比如冰块，石块，木头"):
   "wallT~stone^ice" - the wall set's piece for its outline and its shading, its material's face (play/materials.json
   "svg") laid over it, the shading multiplied back in. Made once each; meanwhile the set's own. */
function playWallTile(piece) {
  play.wallTiles = play.wallTiles || new Map();
  if (play.wallTiles.has(piece)) return play.wallTiles.get(piece);
  const [base, face] = piece.split("^");
  const spec = playPieceSpec(base), sheet = spec && spec.length > 1 && play.sheets[spec[0]];
  if (!sheet) return null;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 16;
  const c = canvas.getContext("2d");
  const set = () => c.drawImage(sheet, spec[1] * 17, spec[2] * 17, 16, 16, 0, 0, 16, 16);
  set();
  const tex = (play.wallFaces || {})[face];
  if (!tex || tex === "loading") return canvas;           // not kept: made again once its face is drawn
  c.globalCompositeOperation = "source-atop"; c.globalAlpha = 0.9; c.drawImage(tex, 0, 0);
  c.globalCompositeOperation = "multiply"; c.globalAlpha = 0.45; set();
  c.globalCompositeOperation = "destination-in"; c.globalAlpha = 1; set();
  play.wallTiles.set(piece, canvas);
  return canvas;
}
function playTile(context, piece, x, y) {
  if (piece && piece.includes("^")) {                     // a wall of its own material
    const tile = playWallTile(piece);
    if (tile) context.drawImage(tile, x, y, 16, 16);
    return;
  }
  if (piece && piece.startsWith("mat:")) {                // a material's floor: its tile, else its colour meanwhile
    const tile = (play.materialTiles || {})[piece.slice(4)];
    if (tile && tile !== "loading") context.drawImage(tile, x, y, 16, 16);
    else { context.fillStyle = "#b9a58a"; context.fillRect(x, y, 16, 16); }
    return;
  }
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
    const [iw, ih] = playItemSize(one);
    return one.x + iw > x0 && one.x < x0 + PLAY_CHUNK && one.y + ih > y0 && one.y < y0 + PLAY_CHUNK;
  }).sort((a, b) => (PLAY_UNDER_KINDS.has(b.kind) - PLAY_UNDER_KINDS.has(a.kind)) || (playLayer(a) - playLayer(b)) || a.y - b.y);
  for (const item of items) {
    const x = (item.x - x0) * 16, y = (item.y - y0) * 16;
    // ITS CARD OF THE WORK AS IT STANDS (agent/playworld.py _mark_card): set aside, faded; under way, its scaffold.
    if (item.card_status === "set_aside") context.globalAlpha = 0.45;
    if (item.hang) {                                           // hung from above: its cord, it a little higher
      context.fillStyle = "rgba(40, 30, 24, 0.85)"; context.fillRect(x + 7, y - 14, 2, 8);
      playThing(context, item, x, y - 8);
    } else playThing(context, item, x, y - (item.on ? 5 : 0));   // set on a table: on its top
    context.globalAlpha = 1;
    if (item.card_status === "working") {
      const [w] = playItemSize(item);
      for (let i = 0; i < w * 4; i += 1) {                     // a band of yellow and black along its foot
        context.fillStyle = i % 2 ? "#2a2a2a" : "#f2c230";
        context.fillRect(x + i * 4, y + 14, 4, 2);
      }
    }
  }
  play.chunks.set(key, canvas);
  // Kept within reason: the blocks longest out of sight let go of.
  if (play.chunks.size > 48) play.chunks.delete(play.chunks.keys().next().value);
  return canvas;
}

/* A THING MADE LARGE (agent/playworld.py item_size: its "scale" - the user, 2026-11-18: "可以是做一个item但是那个Item
   很大"): its tiles, and its picture, that many times its kind's every way. */
function playItemScale(item) {
  // A picture of the user's alone is made large - its grain its own; anything else would only be coarser (the user,
  // 2026-11-18: "对这种大的东西，一定要细粒度").
  return item.picture_url || item.picture ? Math.max(1, Math.min(6, Math.floor(Number(item.scale) || 1))) : 1;
}
function playItemSize(item) {
  if (item.kind === "piece") return [Math.max(1, item.w || 1), Math.max(1, item.h || 1)];
  if (item.kind === "widget") return PLAY_WIDGET_SIZES[item.widget] || (String(item.widget || "").startsWith("field_") ? [3, 2] : [2, 2]);
  const [w, h] = PLAY_SIZES[item.kind] || [1, 1], s = playItemScale(item);
  return [w * s, h * s];
}
/* A ROOM IN LAYERS (agent/playmuse.py; the user, 2026-11-18: "室内的东西的size就正常 ... 你只能靠加布置"): what lies under
   foot first, then what stands, what is set on a table, what hangs from above. */
function playLayer(item) {
  return item.hang ? 2 : item.on ? 1 : 0;
}
/* PROGRESS WIDGETS (agent/playwidgets.py; the user, 2026-11-19: "里面支持养鸡场，植物种植之类，不同阶段会变化形态，作为控件给
   模型"): the stage shown is the work's - from the card's status as it is now (or a great ground's stage), so it moves on
   by itself. Each widget drawn here, from the packs' pictures where they have one, else in plain shapes. */
const PLAY_CROPS = ["turnip", "rose", "cucumber", "tulip", "tomato", "melon", "eggplant", "lemon", "pineapple", "rice", "wheat",
  "grapes", "strawberry", "cassava", "potato", "coffee", "sunflower", "corn", "orange", "avocado"];
const PLAY_CROP_STAGES = ["Seeded", "Sprout", "Young", "Growing", "Ripe", "Picked"];
const PLAY_WIDGETS = {
  orchard: 5, chicken_farm: 5, pasture: 5, apiary: 4, fish_pond: 5, building_site: 5, rocket_pad: 5, vineyard: 5,
  greenhouse: 5, mushroom_farm: 4, workbench: 4, bookshelf: 4, bakery: 4, easel: 4, writing_desk: 4, forge: 4,
  aquarium: 4, loom: 4, kiln: 4, telescope: 4,
};
for (const crop of PLAY_CROPS) PLAY_WIDGETS[`field_${crop}`] = 6;
const PLAY_WIDGET_SIZES = {
  orchard: [3, 2], chicken_farm: [3, 2], pasture: [4, 3], apiary: [2, 2], fish_pond: [3, 2], building_site: [3, 3],
  rocket_pad: [2, 3], vineyard: [3, 2], greenhouse: [3, 2], mushroom_farm: [2, 2], workbench: [2, 1], bookshelf: [2, 2],
  bakery: [2, 1], easel: [1, 2], writing_desk: [2, 1], forge: [2, 1], aquarium: [2, 1], loom: [2, 1], kiln: [2, 2], telescope: [2, 2],
};
/* How far the work it stands for has gone, 0 to 1 - and whether it was set aside: its card and every card under it
   (the done share), else a great ground's stage. */
function playWidgetProgress(item) {
  const trees = (play.world && play.world.work_trees) || {};
  if (item.card) {
    const all = Object.values(trees).flat();
    const card = all.find((one) => one.id === item.card);
    if (!card) return { share: 0, aside: false };
    if (card.status === "done") return { share: 1, aside: false };
    if (card.status === "set_aside") return { share: 0, aside: true };
    const under = [], todo = [card.id];
    while (todo.length) {
      const id = todo.pop();
      for (const one of all) if (one.parent === id && !under.includes(one)) { under.push(one); todo.push(one.id); }
    }
    const done = under.filter((one) => one.status === "done").length;
    return { share: under.length ? done / under.length : (card.status === "working" ? 0.5 : 0), aside: false, working: card.status === "working" };
  }
  const zone = play.world && play.world.zones.find((one) => one.id === item.zone);
  const kept = zone && zone.great && ((play.world.great || {})[zone.great] || {});
  const stage = kept && item.stage && (kept.stages || []).find((one) => one.n === item.stage);
  if (stage) return { share: stage.of ? stage.done / stage.of : 0, aside: false, working: stage.status === "working" };
  return { share: 0, aside: false };
}
/* Its stage of `count`: not begun the first, done the last, under way between by how much is done; -1 set aside. */
function playWidgetStage(item) {
  const count = PLAY_WIDGETS[item.widget] || 4;
  const { share, aside, working } = playWidgetProgress(item);
  if (aside) return -1;
  if (share >= 1) return count - 1;
  if (share <= 0 && !working) return 0;
  return Math.max(1, Math.min(count - 2, 1 + Math.floor(share * (count - 2))));
}
function playWidgetDraw(context, item, x, y) {
  const T = 16, name = item.widget || "", [w, h] = PLAY_WIDGET_SIZES[name] || (name.startsWith("field_") ? [3, 2] : [2, 2]);
  const stage = playWidgetStage(item), last = (PLAY_WIDGETS[name] || 4) - 1;
  const spr = (piece, tx, ty, tw = 1, th = 1) => {           // a picture of the packs, at a tile of it
    const spec = PLAY_SCENERY[piece] || (typeof PLAY_ITEMS !== "undefined" && PLAY_ITEMS[piece]);
    const sheet = spec && play.sheets[spec[0]];
    if (!sheet) return false;
    context.drawImage(sheet, spec[1] * 17, spec[2] * 17, (spec[3] || 1) * 17 - 1, (spec[4] || 1) * 17 - 1, x + tx * T, y + ty * T, tw * T, th * T);
    return true;
  };
  const box = (tx, ty, tw, th, fill, edge) => {
    context.fillStyle = fill; context.fillRect(x + tx * T, y + ty * T, tw * T, th * T);
    if (edge) { context.strokeStyle = edge; context.lineWidth = 1; context.strokeRect(x + tx * T + 0.5, y + ty * T + 0.5, tw * T - 1, th * T - 1); }
  };
  const dot = (px, py, r, fill) => { context.fillStyle = fill; context.beginPath(); context.arc(x + px, y + py, r, 0, Math.PI * 2); context.fill(); };
  const soil = () => box(0, 0, w, h, "#7a5230", "#5c3c22");
  const each = (fn) => { for (let ty = 0; ty < h; ty += 1) for (let tx = 0; tx < w; tx += 1) fn(tx, ty); };
  context.save();
  if (stage < 0) context.globalAlpha = 0.5;                  // set aside: faded, covered
  if (name.startsWith("field_")) {
    // A FIELD: each plant its stage - under way, as many ripe as the share done, the rest still growing.
    soil();
    const crop = name.slice(6), share = playWidgetProgress(item).share, tiles = w * h;
    let n = 0;
    each((tx, ty) => {
      const s = stage < 0 ? 0 : stage === last ? last : n < Math.round(share * tiles) ? 4 : Math.min(3, Math.max(1, stage));
      spr(`${crop}${PLAY_CROP_STAGES[s]}`, tx, ty);
      n += 1;
    });
  } else if (name === "orchard") {
    box(0, 0, w, h, "#6c8f3a");
    const tree = ["seedling", "smallTree", "roundTree", "fruitTree", "fruitTree"][Math.max(0, stage)];
    for (let k = 0; k < w; k += 1) spr(tree, k, 0, 1, stage >= 2 ? 2 : 1) || box(k, 0, 1, 1, "#3d7a2e");
    if (stage === last) for (let k = 0; k < w; k += 1) { box(k + 0.2, h - 0.5, 0.6, 0.4, "#a0662f"); dot(k * T + 8, h * T - 9, 2.5, "#d94a3a"); }
  } else if (name === "chicken_farm") {
    box(0, 0, w, h, "#c9b27c");
    box(0, 0, 1.4, 1.2, "#8b5a2b", "#5c3c22"); box(0, -0.25, 1.4, 0.3, "#a83c2c");           // the coop
    if (stage >= 1) for (let k = 0; k < 3; k += 1) dot(26 + k * 6, 10, 2.5, "#fdf6e3");          // eggs in the nests
    if (stage === 2) for (let k = 0; k < 4; k += 1) { dot(14 + k * 9, 26, 3, "#f2d24a"); dot(16 + k * 9, 25, 1, "#e07b1f"); }   // chicks
    if (stage >= 3) for (let k = 0; k < Math.min(w, 3); k += 1) spr("chicken", k, 1);
    if (stage === last) { box(2.2, 0.2, 0.7, 0.5, "#a0662f"); for (let k = 0; k < 3; k += 1) dot(38 + k * 3, 6, 2, "#fdf6e3"); }
  } else if (name === "pasture") {
    box(0, 0, w, h, "#7fae4b");
    for (let k = 0; k < w; k += 1) { spr("fence", k, 0) || box(k, 0, 1, 0.2, "#8b5a2b"); }
    if (stage >= 1) spr("hayBale", 0, 1);
    if (stage >= 2) { spr("cow", 1, 1); spr("sheep", 2, 2); }
    if (stage >= 3) { spr("cow", 2, 1); spr("sheep", 3, 1); spr("sheep", 1, 2); }
    if (stage === last) for (let k = 0; k < 2; k += 1) box(3 + k * 0.4, 2.3, 0.3, 0.6, "#c8ccd2", "#7d8490");   // milk churns
  } else if (name === "apiary") {
    box(0, 0, w, h, "#9cc25a");
    if (stage === 0) box(0.5, 0.6, 1, 0.8, "#c9a46a", "#7a5230");
    if (stage >= 1) { spr("beehive", 0, 0) || box(0.2, 0.3, 0.7, 0.8, "#e0b84a"); if (stage >= 2) spr("beehive", 1, 0); }
    if (stage >= 2) for (let k = 0; k < 6; k += 1) dot(6 + (k * 13) % 26, 22 + (k * 7) % 8, 1.2, "#2a2a2a");   // bees
    if (stage === last) for (let k = 0; k < 3; k += 1) { box(0.2 + k * 0.55, 1.4, 0.4, 0.5, "#f2a71b", "#b06d00"); }
  } else if (name === "fish_pond") {
    box(0, 0, w, h, "#7a5230");
    if (stage >= 1) box(0.25, 0.25, w - 0.5, h - 0.5, "#3e8fc9", "#2b6aa9");
    if (stage === 2) for (let k = 0; k < 5; k += 1) dot(12 + k * 6, 14 + (k % 2) * 6, 1.2, "#f6e7a0");
    if (stage >= 3) { spr("fish1", 1, 0); spr("smallFish", 0, 1); }
    if (stage === last) { box(w - 0.9, h - 0.8, 0.8, 0.6, "#a0662f"); dot(w * T - 7, h * T - 9, 2.5, "#9fc6e0"); }
  } else if (name === "building_site" || name === "rocket_pad") {
    // SCAFFOLDING UP TO WHERE IT IS BUILT (the user: "施工到哪一步"): the built part, the frame round the rest.
    box(0, h - 0.4, w, 0.4, "#8a8f98");
    const built = [0.15, 0.3, 0.55, 0.8, 1][Math.max(0, stage)];
    if (name === "building_site") {
      const top = h - 0.4 - (h - 0.6) * built;
      box(0.3, top, w - 0.6, h - 0.4 - top, "#c96f4a", "#7a3b22");
      for (let k = 0; k < w - 1; k += 1) if (top < h - 1.4) box(0.7 + k, top + 0.4, 0.4, 0.4, "#cfe8f5");
      if (stage >= 3) { context.fillStyle = "#7d3b2a"; context.beginPath(); context.moveTo(x, y + top * T); context.lineTo(x + w * T / 2, y + (top - 0.8) * T); context.lineTo(x + w * T, y + top * T); context.fill(); }
    } else {
      const tall = (h - 0.4) * built;
      box(w / 2 - 0.35, h - 0.4 - tall, 0.7, tall, "#e8e8ec", "#7d8490");
      if (stage >= 3) { context.fillStyle = "#d9482b"; context.beginPath(); context.moveTo(x + (w / 2 - 0.35) * T, y + (h - 0.4 - tall) * T); context.lineTo(x + w * T / 2, y + (h - 1 - tall) * T); context.lineTo(x + (w / 2 + 0.35) * T, y + (h - 0.4 - tall) * T); context.fill(); }
      if (stage === last) for (let k = 0; k < 4; k += 1) dot(w * T / 2 - 8 + k * 5, h * T - 4, 3, "rgba(230, 230, 230, 0.85)");   // its smoke
    }
    if (stage < last) {                                         // the scaffold: poles and boards
      context.strokeStyle = "#a07a45"; context.lineWidth = 1;
      for (let k = 0; k <= w; k += 1) { context.beginPath(); context.moveTo(x + k * T, y + h * T); context.lineTo(x + k * T, y + 2); context.stroke(); }
      for (let k = 1; k < h * 2; k += 1) { context.beginPath(); context.moveTo(x, y + k * T / 2); context.lineTo(x + w * T, y + k * T / 2); context.stroke(); }
    }
  } else if (name === "vineyard") {
    box(0, 0, w, h, "#9a7b4f");
    for (let k = 0; k < w; k += 1) {
      box(k + 0.45, 0.2, 0.1, h - 0.4, "#6b4a2a");
      if (stage >= 1) box(k + 0.3, 0.4, 0.4, h - 0.8, stage >= 2 ? "#4f8a2f" : "#7a6a3a");
      if (stage >= 3) for (let j = 0; j < h; j += 1) dot(k * T + 8, j * T + 10, 2.5, "#6b2d7a");
    }
    if (stage === last) spr("barrel", w - 1, h - 1);
  } else if (name === "greenhouse") {
    box(0, 0, w, h, stage >= 1 ? "rgba(190, 230, 240, 0.55)" : "#9aa3ad");
    context.strokeStyle = "#e8e8ec"; context.lineWidth = 1;
    for (let k = 0; k <= w * 2; k += 1) { context.beginPath(); context.moveTo(x + k * T / 2, y); context.lineTo(x + k * T / 2, y + h * T); context.stroke(); }
    if (stage >= 2) each((tx, ty) => spr(stage >= 4 ? "sunflowerRipe" : stage >= 3 ? "tulipGrowing" : "tulipSprout", tx, ty));
  } else if (name === "mushroom_farm") {
    box(0, 0, w, h, "#5f6b3a");
    for (let k = 0; k < w; k += 1) spr("log", k, 1) || box(k, 1.3, 1, 0.5, "#6b4a2a");
    if (stage === 1) for (let k = 0; k < w; k += 1) dot(k * T + 8, 22, 2.5, "#e8d5b0");
    if (stage >= 2) for (let k = 0; k < w; k += 1) spr(k % 2 ? "mushroom2" : "mushroom", k, 0);
    if (stage === last) box(0.6, 0.1, 0.8, 0.5, "#a0662f");
  } else {
    // THINGS OF A ROOM: a bench, shelves, an oven, an easel, a desk, a forge, a tank, a loom, a kiln, a telescope.
    box(0, 0, w, h, "#8b5a2b", "#5c3c22");
    const s = Math.max(0, stage), share = s / Math.max(1, last);
    if (name === "workbench") { if (s === 0) spr("crate", 0, 0); for (let k = 0; k < s * 3; k += 1) box(0.1 + (k % 6) * 0.3, 0.15, 0.2, 0.2, "#9aa3ad"); if (s === last) spr("robot1", 1, 0); }
    else if (name === "bookshelf") { for (let r = 0; r < h * 2; r += 1) for (let k = 0; k < Math.round(share * 8); k += 1) box(0.1 + k * 0.23, r * 0.5 + 0.08, 0.18, 0.38, ["#a83c2c", "#3a6ea5", "#d9b44a", "#4f8a2f"][(k + r) % 4]); }
    else if (name === "bakery") { box(1.1, 0.05, 0.85, 0.9, "#5b4a42"); if (s >= 2) box(1.25, 0.4, 0.55, 0.4, "#f2a71b"); if (s === 0) box(0.1, 0.3, 0.4, 0.6, "#ece3cf"); if (s === 1) dot(14, 10, 5, "#f1e2c1"); if (s === last) for (let k = 0; k < 3; k += 1) dot(6 + k * 6, 9, 3, "#c98a3f"); }
    else if (name === "easel") { box(0.15, 0.1, 0.7, 1.1, ["#f7f4ec", "#f0ebdf", "#f7f4ec", "#f7f4ec"][s], "#5c3c22"); if (s >= 1) { context.strokeStyle = "#555"; context.beginPath(); context.moveTo(x + 5, y + 14); context.lineTo(x + 11, y + 6); context.stroke(); } if (s >= 2) { box(0.25, 0.3, 0.3, 0.3, "#3a6ea5"); box(0.45, 0.6, 0.3, 0.3, "#d94a3a"); } if (s === last) { context.strokeStyle = "#d9b44a"; context.lineWidth = 2; context.strokeRect(x + 2, y + 1, 12, 18); } }
    else if (name === "writing_desk") { for (let k = 0; k < Math.max(1, s * 3); k += 1) box(0.3 + k * 0.03, 0.4 - k * 0.04, 0.6, 0.4, "#f7f4ec"); if (s === last) box(1.1, 0.2, 0.7, 0.55, "#7a2e3a", "#3a1010"); }
    else if (name === "forge") { box(0, 0, 0.9, 1, "#5b5b60"); if (s >= 1) dot(7, 9, 4, "#f2701b"); if (s === 0) for (let k = 0; k < 3; k += 1) dot(20 + k * 4, 10, 2.5, "#6e6a64"); if (s >= 2) spr("goldIngot", 1, 0); if (s === last) { box(1.2, 0.2, 0.1, 0.6, "#9aa3ad"); box(1.5, 0.2, 0.1, 0.6, "#9aa3ad"); } }
    else if (name === "aquarium") { box(0.05, 0.05, w - 0.1, 0.9, s >= 1 ? "rgba(80, 160, 220, 0.75)" : "rgba(220, 235, 245, 0.6)", "#9aa3ad"); for (let k = 0; k < (s === 2 ? 2 : s === 3 ? 6 : 0); k += 1) dot(5 + k * 4, 6 + (k % 2) * 4, 1.6, ["#f2701b", "#f2d24a", "#d94a3a"][k % 3]); }
    else if (name === "loom") { for (let k = 0; k < 8; k += 1) box(0.1 + k * 0.23, 0.05, 0.03, 0.9, "#e8e0cc"); box(0.1, 0.95 - share * 0.85, 1.8, share * 0.85, "#a83c2c"); }
    else if (name === "kiln") { box(0.6, 0.2, 1.1, 1.6, "#a0522d", "#5c3c22"); if (s === 2) dot(19, 24, 4, "#f2701b"); for (let k = 0; k < (s >= 1 ? 3 : 0); k += 1) box(0.1 + k * 0.6, 1.5, 0.4, 0.45, s === last ? "#3a7bd5" : "#c98a5a"); if (s === 0) dot(8, 26, 4, "#b07a55"); }
    else if (name === "telescope") { box(0.2, 1.4, 1.6, 0.4, "#5b5b60"); if (s >= 1) box(0.9, 0.9, 0.2, 0.6, "#3a3a40"); if (s >= 2) { context.save(); context.translate(x + 16, y + 16); context.rotate(s === last ? -0.9 : -0.3); context.fillStyle = "#c8ccd2"; context.fillRect(-12, -3, 24, 6); context.restore(); } if (s === last) dot(26, 4, 1.5, "#ffe9a8"); }
  }
  context.restore();
}
function playThing(context, item, x, y) {
  if (item.kind === "widget") { playWidgetDraw(context, item, x, y); return; }   // a progress widget: its stage now
  if (item.kind === "piece") return;                     // a great piece: drawn over the world (playPiecesDraw)
  if (PLAY_HANGINGS[item.kind]) { playHangingDraw(context, item.kind, x, y); return; }   // made to hang: drawn here
  if (play.made && (play.made.items || {})[item.kind]) return;   // made by the agent: in fine pixels (playFineThingsDraw)
  if (play.themeHangings && play.themeHangings[item.kind]) { playThemeHangingDraw(context, play.themeHangings[item.kind], x, y); return; }
  const scale = playItemScale(item);
  if (scale > 1) {                                       // drawn at its own size, then made large - its pixels crisp
    context.save();
    context.translate(x, y);
    context.scale(scale, scale);
    playThing(context, { ...item, scale: 1 }, 0, 0);
    context.restore();
    return;
  }
  if (item.kind === "rug") { playRug(context, item, x, y); return; }
  // A tile of a picture laid in things (agent/playworld.py _lay_picture): its paint on the ground, a little worn.
  if (item.kind === "paint") {
    context.fillStyle = /^#[0-9a-f]{6}$/i.test(item.colour || "") ? item.colour : "#c94a4a";
    context.fillRect(x, y, 16, 16);
    context.fillStyle = "rgba(0, 0, 0, 0.06)"; context.fillRect(x, y + 14, 16, 2);
    return;
  }
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
  // A FRAME: a little cartoon painting in a wooden frame - never their picture, which is in its card (the user,
  // 2026-11-19: "这个地图上应该画的是卡通物品，然后交互细节里有图片").
  context.fillStyle = "#5a3a1e";
  context.fillRect(x + 2, y + 3, 28, 26);
  context.fillStyle = "#a8743c";
  context.fillRect(x + 3, y + 4, 26, 24);
  playCartoonPainting(context, item, x + 5, y + 6, 22, 20);
}

/* A CARTOON PAINTING, ITS OWN BY THE THING'S ID: a sky, hills, the sun or the moon, a tree or a house. */
const PLAY_PAINTING_SKIES = [["#9fd3f0", "#f6d36b"], ["#f4b393", "#fff2b0"], ["#2e3a6b", "#f2efd8"], ["#bfe3c8", "#ffffff"],
                             ["#d9b8e8", "#fbe6a2"]];
const PLAY_PAINTING_HILLS = ["#6aa84f", "#4f8a5b", "#c9a15a", "#7f9cc9", "#8fbf6a"];
function playCartoonPainting(context, item, x, y, w, h) {
  const n = Number(String(item.id || "").replace(/\D/g, "")) || 1;
  const [sky, light] = PLAY_PAINTING_SKIES[Math.floor(playHash(n, 1, 61) * PLAY_PAINTING_SKIES.length)];
  const hill = PLAY_PAINTING_HILLS[Math.floor(playHash(n, 2, 61) * PLAY_PAINTING_HILLS.length)];
  context.fillStyle = sky; context.fillRect(x, y, w, h);
  context.fillStyle = light;                                        // the sun, or the moon
  const sx = x + 3 + Math.floor(playHash(n, 3, 61) * (w - 8));
  context.fillRect(sx, y + 2, 4, 4); context.fillRect(sx - 1, y + 3, 6, 2); context.fillRect(sx + 1, y + 1, 2, 6);
  context.fillStyle = hill;                                         // two hills
  for (let i = 0; i < w; i += 1) {
    const top = Math.round(h * 0.55 + Math.sin((i + n) / 3.1) * 2.5 + Math.sin((i * 2 + n) / 5) * 1.5);
    context.fillRect(x + i, y + Math.max(2, Math.min(h - 2, top)), 1, h - Math.max(2, Math.min(h - 2, top)));
  }
  if (playHash(n, 4, 61) < 0.5) {                                   // a tree
    const tx = x + 4 + Math.floor(playHash(n, 5, 61) * (w - 10));
    context.fillStyle = "#6b4a2b"; context.fillRect(tx + 2, y + h - 7, 2, 5);
    context.fillStyle = "#3f7a3a"; context.fillRect(tx, y + h - 11, 6, 5);
  } else {                                                          // a little house
    const hx = x + 5 + Math.floor(playHash(n, 6, 61) * (w - 12));
    context.fillStyle = "#f2e6cf"; context.fillRect(hx, y + h - 8, 6, 5);
    context.fillStyle = "#b5543c"; context.fillRect(hx - 1, y + h - 10, 8, 2);
    context.fillStyle = "#6b4a2b"; context.fillRect(hx + 2, y + h - 6, 2, 3);
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
  // CARTOON THINGS, THEIR PICTURE ONLY IN THE CARD (the user, 2026-11-19: "这个地图上应该画的是卡通物品"): a statue a
  // grey figure, a banner its emblem, a poster a headline and its blocks.
  const n = Number(String(item.id || "").replace(/\D/g, "")) || 1;
  const ink = ["#c94f3a", "#3a6fc9", "#3a9c6a", "#c9a23a", "#8a4fc9"][Math.floor(playHash(n, 7, 61) * 5)];
  if (item.kind === "statue_of") {
    context.fillStyle = "#7d7f86"; context.fillRect(x + 2, y + 24, 12, 8);          // its pedestal
    context.fillStyle = "#a3a6ad"; context.fillRect(x + 1, y + 22, 14, 3);
    context.fillStyle = "#b9bcc3";                                                  // a figure, arms up
    context.fillRect(x + 6, y + 5, 4, 4); context.fillRect(x + 5, y + 10, 6, 8); context.fillRect(x + 6, y + 18, 4, 4);
    context.fillRect(x + 3, y + 7, 2, 5); context.fillRect(x + 11, y + 7, 2, 5);
    context.fillStyle = "rgba(255,255,255,0.25)"; context.fillRect(x + 5, y + 10, 2, 8);
  } else if (item.kind === "banner") {
    context.fillStyle = "#5a3a1e"; context.fillRect(x, y + 1, 16, 2);                // its rod
    context.fillStyle = "#8c2f39"; context.fillRect(x + 2, y + 3, 12, 25);
    context.fillStyle = "#f2d16b";                                                  // its emblem, a star
    context.fillRect(x + 7, y + 8, 2, 8); context.fillRect(x + 4, y + 11, 8, 2); context.fillRect(x + 5, y + 9, 6, 6);
    context.fillStyle = "#8c2f39"; context.beginPath(); context.moveTo(x + 2, y + 28); context.lineTo(x + 8, y + 31);
    context.lineTo(x + 14, y + 28); context.fill();
  } else {
    context.fillStyle = "#e9e4d8"; context.fillRect(x + 1, y + 2, 30, 28);           // a poster
    context.fillStyle = ink; context.fillRect(x + 4, y + 5, 24, 5);                  // its headline
    context.fillStyle = "#9a9384"; context.fillRect(x + 4, y + 12, 16, 2); context.fillRect(x + 4, y + 16, 20, 2);
    context.fillStyle = ink; context.globalAlpha = 0.55; context.fillRect(x + 4, y + 20, 10, 7);
    context.globalAlpha = 1; context.fillStyle = "#c7c0b0"; context.fillRect(x + 16, y + 20, 12, 7);
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

/* A RIDE NEVER LEFT ON (the user, 2026-11-19: "agent 在play上的形象消失了"): the walker is not drawn while it rides, so a
   ride that was not ended - the view shut on the way, the piece out of sight or laid again when the world was loaded
   again - would hide it for good. Past its time, it is ended here: off at its stop, or by the piece's gate. */
function playRideOverdue(now = performance.now()) {
  const ride = play.ride;
  if (!ride) return;
  const seconds = ride.line ? ride.seconds || 0 : PLAY_PIECE_LAP_S;
  if (now - ride.at < seconds * 1000 + 1500) return;
  play.ride = null;
  let land = null;
  if (ride.line && ride.to) {
    land = ride.line.kind === "ferry" ? [ride.to.x, ride.to.y] : (playActSpotNear(ride.to.x, ride.to.y + 1, 3) || [ride.to.x, ride.to.y + 1]);
  } else if (ride.piece) {
    const item = ride.piece, gate = ((item.spec || {}).entrance || [Math.floor((item.w - 1) / 2)])[0];
    land = playActSpotNear(item.x + gate, item.y + item.h);
  }
  if (land) { play.me.x = land[0] + 0.5; play.me.y = land[1] + 0.6; }
  if (playBlocked(play.me.x, play.me.y)) playUnstuck();
  playWhere();
}
/* The piece being ridden - the same one after the world is loaded again (a new object each load). */
function playRidingPiece(item) {
  const one = play.ride && play.ride.piece;
  return Boolean(one) && (one === item || (one.x === item.x && one.y === item.y && one.w === item.w && one.h === item.h));
}
function playDraw() {
  if (!play.on || !play.map || !play.ctx) return;
  playRideOverdue();
  const { ctx, canvas, scale } = play;
  const size = PLAY_TILE * scale;
  // The agent in the middle - or, looked elsewhere on the map (playMiniLook), there till a step is taken.
  const middle = play.lookAt || [play.me.x, play.me.y];
  play.camera.x = middle[0] * size - canvas.width / 2;
  play.camera.y = middle[1] * size - canvas.height / 2;
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
  // EACH LAYER ON ITS OWN (2026-11-19: one great piece the page could not draw stopped the frame, and the walker was
  // never drawn - "agent 在play上的形象消失了"): a layer that fails is told once and the frame goes on.
  const layer = (name, draw) => {
    ctx.save();
    try { draw(); } catch (err) {
      ctx.restore(); ctx.save();                 // what it left half-set (a turn, a scale) put back
      ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.globalAlpha = 1;
      play.layerFailed = play.layerFailed || new Set();
      if (!play.layerFailed.has(name)) { play.layerFailed.add(name); console.error(`play: ${name}`, err); playNote(`layer ${name} failed: ${err}`); }
    }
    ctx.restore();
  };
  layer("bushes", () => playBushesDraw(size, c0, c1, r0, r1, false));
  layer("coasters", () => playCoastersDraw(size));
  layer("pieces", () => playPiecesDraw(size));                 // great pieces (playPiecePicture), over the ground
  layer("fine things", () => playFineThingsDraw(size));        // the user's pictures and the agent's paintings, in fine pixels
  layer("facades", () => playFacadesDraw(size));
  if (typeof playActDraw === "function") layer("acts", () => playActDraw(size));
  layer("signs", () => playSignsDraw(size));
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
    signs.push({ at: [mark.cx + 0.5, mark.square[3] - 0.2], name: playWords(PLAY_LANDMARK_NAMES[mark.landmark] || mark.landmark),
                 under: mark.region.name || "", region: mark.region });
  }
  index.signs = signs;
  return signs;
}

/* WHERE A SIGN'S BOARD WOULD BE (playSignsDraw, playPostDraw - the same sums): its left, right, top, bottom on the
   screen, before any raising. */
function playSignBox(sign, x, y, size, fontPx) {
  const { ctx } = play;
  ctx.save();
  let box;
  if (sign.post) {
    const px = Math.round(fontPx * 0.72);
    ctx.font = `bold ${px}px "Microsoft YaHei", "PingFang SC", system-ui, sans-serif`;
    const w = Math.min(ctx.measureText(sign.name).width, px * 9.5) + Math.round(px * 0.4) * 2;
    const h = Math.round(px * 1.5), tip = Math.round(h * 0.55), top = y - Math.round(size * 1.35) - Math.round(h * 0.2);
    box = sign.post.dx < 0 ? { left: x - w - tip / 2, right: x + tip / 2, top, bottom: top + h }
      : { left: x - tip / 2, right: x + w + tip, top, bottom: top + h };
  } else {
    ctx.font = `bold ${fontPx}px "Microsoft YaHei", "PingFang SC", system-ui, sans-serif`;
    const nameW = Math.min(ctx.measureText(sign.name).width, fontPx * 13.5);
    ctx.font = `${Math.round(fontPx * 0.8)}px "Microsoft YaHei", "PingFang SC", system-ui, sans-serif`;
    const underW = sign.under ? ctx.measureText(sign.under).width : 0;
    const pad = Math.round(fontPx * 0.45), w = Math.max(nameW, underW) + pad * 2;
    const h = Math.round(fontPx * (sign.under ? 2.3 : 1.4)) + pad, top = y - h - Math.round(fontPx * 0.6);
    box = { left: x - w / 2, right: x + w / 2, top, bottom: top + h };
  }
  ctx.restore();
  return box;
}
function playPostDraw(sign, x, y, size, fontPx, lift = 0) {
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
  const foot = y, top = foot - Math.round(size * 1.35) - lift;
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
  // NONE OVER ANOTHER (the user, 2026-11-19: "你很多门牌都互相遮挡，要用代码防止这个"): each sign's board where it would be
  // drawn, the places' boards first, the nearer first; one that would cover a board already placed raised over it - a
  // post's pole made as much longer - till it is clear.
  const placed = [], lifts = new Map();
  const order = signs.map((sign) => ({ sign, x: Math.round(sign.at[0] * size - play.camera.x), y: Math.round(sign.at[1] * size - play.camera.y) }))
    .filter(({ x, y }) => !(x < -200 || x > canvas.width + 200 || y < -60 || y > canvas.height + 60))
    .sort((a, b) => (Boolean(a.sign.post) - Boolean(b.sign.post)) || b.y - a.y);
  for (const one of order) {
    const box = playSignBox(one.sign, one.x, one.y, size, fontPx);
    let lift = 0;
    for (let tries = 0; tries < 12; tries += 1) {
      const hit = placed.find((b) => box.left < b.right + 3 && box.right + 3 > b.left && box.top - lift < b.bottom + 3 && box.bottom - lift + 3 > b.top);
      if (!hit) break;
      lift = Math.ceil(box.bottom - hit.top + 4);
    }
    placed.push({ left: box.left, right: box.right, top: box.top - lift, bottom: box.bottom - lift });
    lifts.set(one.sign, lift);
  }
  for (let { sign, x, y } of order.slice().reverse()) {
    const lift = lifts.get(sign) || 0;
    if (sign.post) { playPostDraw(sign, x, y, size, fontPx, lift); continue; }
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
    const left = Math.round(x - w / 2), top = y - h - Math.round(fontPx * 0.6) - lift;
    const u = Math.max(2, Math.round(play.scale * 0.6));
    const look = PLAY_SIGN_STYLES[playSignStyle(sign)] || PLAY_SIGN_STYLES.wood;
    if (lift) { ctx.fillStyle = look.post || "#5c3317"; ctx.fillRect(x - u, top + h, u * 2, lift); }   // raised: on a longer post
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
const PLAY_BANNER_MS = 2600, PLAY_BANNER_WORDS_MS = 7000;
function playBannerDraw() {
  const banner = play.banner;
  if (!banner) return;
  const age = performance.now() - banner.at;
  // A land's title stays as long as its words take to read (the user, 2026-11-19: "每个区域的title, 要加上用户心情，和agent
  // 说的话，比如安慰什么的").
  const lasts = banner.feeling || banner.toThem ? PLAY_BANNER_WORDS_MS : PLAY_BANNER_MS;
  if (age > lasts) { play.banner = null; return; }
  const fade = Math.min(1, age / 250, (lasts - age) / 400);
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
  if (banner.feeling || banner.toThem) {
    // UNDER IT, HOW THESE DAYS FELT AND WHAT THE AGENT SAYS TO THEM: a card of its own, in the world's own words.
    const px = Math.max(13, Math.round(play.scale * 4));
    ctx.font = `${px}px "Microsoft YaHei", "PingFang SC", system-ui, sans-serif`;
    const most = Math.min(canvas.width * 0.8, px * 26);
    const wrap = (text) => {
      const out = [];
      let line = "";
      for (const ch of String(text || "")) {
        if (ctx.measureText(line + ch).width > most && line) { out.push(line); line = ""; }
        line += ch;
      }
      if (line) out.push(line);
      return out;
    };
    const rows = [...(banner.feeling ? wrap(banner.feeling).map((one) => ["feeling", one]) : []),
                  ...(banner.toThem ? wrap(banner.toThem).map((one) => ["words", one]) : [])].slice(0, 6);
    const cw = Math.max(...rows.map(([, one]) => ctx.measureText(one).width)) + px * 1.6, ch = rows.length * px * 1.45 + px;
    const cx = Math.round((canvas.width - cw) / 2), cy = y + h + px * 0.6;
    ctx.fillStyle = "rgba(43, 28, 16, 0.82)";
    ctx.fillRect(cx, cy, cw, ch);
    ctx.textBaseline = "top";
    rows.forEach(([kind, one], n) => {
      ctx.fillStyle = kind === "feeling" ? "#f3d7a4" : "#fff6e8";
      ctx.font = `${kind === "feeling" ? "bold " : "italic "}${px}px "Microsoft YaHei", "PingFang SC", system-ui, sans-serif`;
      ctx.fillText(one, cx + px * 0.8, cy + px * 0.5 + n * px * 1.45);
    });
  }
  ctx.restore();
}

/* A region's tune outdoors: its own if the agent wrote one, else its land's. Its other sounds come from its things. */
function playRegionSound(region) {
  if (!region) return null;
  return { id: `region-${region.id}`, kind: region.land.tune || "town", music: region.music, scene: region.scene || "" };
}

/* Words the page's language has for a text of its own (the starting village's name, the hints). */
function playWords(text) {
  const own = playLanguage();
  if (own === "en") return text;                                  // Play in English, whatever the page is in
  if (play.wordsZh && typeof play.wordsZh[text] === "string") return play.wordsZh[text];
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
  // THE HOUSE ALONE, SAID (the user, 2026-11-19): the world outside comes when the agent knows how their days feel.
  if (play.map && play.map.closed) lines.push(playWords("Only your home for now - the world outside opens once I know how your days feel"));
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
/* The view where the map is pressed: its point, as the canvas is drawn whatever its size on the page. */
function playMiniLook(event) {
  if (!play.miniBase || !play.miniBase.back) return;
  const box = play.mini.getBoundingClientRect();
  const mx = (event.clientX - box.left) * (play.mini.width / Math.max(1, box.width));
  const my = (event.clientY - box.top) * (play.mini.height / Math.max(1, box.height));
  play.lookAt = play.miniBase.back(mx, my);
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
const PLAY_MINI_KEPT_VERSION = 5, PLAY_MINI_KEPT_MOST = 300, PLAY_MINI_KEPT_KEY = "playMiniPictures";
/* THE KEPT PICTURES OF THE CODE THAT DREW THEM (the user, 2026-11-19: "你现在绘画的小地图暗的问题改好了吗"): a block's
   kept picture is the world's and the drawing's - the code laying and drawing the blocks changed, every one drawn again,
   not an old one shown for a world drawn otherwise now. */
function playMiniKeptVersion() {
  if (play.miniKeptVersion) return play.miniKeptVersion;
  const code = [typeof playLayBlock === "function" ? playLayBlock : "", typeof playChunk === "function" ? playChunk : "",
    typeof playMiniShrink === "function" ? playMiniShrink : "", typeof playLight === "function" ? playLight : "",
    JSON.stringify(typeof PLAY_EDGES === "object" ? PLAY_EDGES : {})].map(String).join("|");
  let h = 2166136261;
  for (let i = 0; i < code.length; i += 1) { h ^= code.charCodeAt(i); h = Math.imul(h, 16777619) >>> 0; }
  play.miniKeptVersion = `${PLAY_MINI_KEPT_VERSION}:${h.toString(36)}`;
  return play.miniKeptVersion;
}
function playMiniKept() {
  if (play.miniKept) return play.miniKept;
  play.miniKept = new Map();
  try {
    localStorage.removeItem("playMiniKept");                                // the colour-only one before
    const said = JSON.parse(localStorage.getItem(PLAY_MINI_KEPT_KEY) || "null");
    if (said && said.v === playMiniKeptVersion()) for (const [key, sig, url] of said.blocks) play.miniKept.set(key, { sig, url });
  } catch (_err) { /* drawn anew */ }
  return play.miniKept;
}
function playMiniKeep() {
  const kept = playMiniKept();
  while (kept.size > PLAY_MINI_KEPT_MOST) kept.delete(kept.keys().next().value);
  let blocks = [...kept].map(([key, one]) => [key, one.sig, one.url]);
  for (let tries = 0; tries < 3 && blocks.length; tries += 1) {
    try { localStorage.setItem(PLAY_MINI_KEPT_KEY, JSON.stringify({ v: playMiniKeptVersion(), blocks })); return; }
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
/* `behind`: made out of sight and put in place whole once done - the map shown till then as it was (the user,
   2026-11-18: "你现在进入另外一个地块的时候，小地图按理不需要重新refresh" - each part of the world drawn, the map was thrown
   away and drawn again before their eyes); else drawn as it comes (the first time, another area). */
function playMiniBase(behind = false) {
  const index = play.map;
  const rects = [...index.zones.map((one) => one.rect), ...index.landmarks.map((one) => one.square)];
  for (const key of index.paths.keys()) { const [x, y] = key.split(",").map(Number); rects.push([x, y, x + 1, y + 1]); }
  // AS FAR AS THE LAND REACHES (the user, 2026-11-19: "我应该是在一个有树的地方，但是小地图上白框显示这里是黑的"): a cape
  // (PLAY_COAST_FAR) and past it the sea or the land's own grown-shut edge (PLAY_SEA_MOST) - PLAY_LAND_MOST in all;
  // the map stopping at the capes had left the edge's woods dark on it.
  const pad = PLAY_LAND_MOST + 2, R = PLAY_MINI_PX;
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
  // `back`: a point of the map to the world's tiles (a press on it, playMiniLook).
  const mine = { canvas: base, at, k, back: (mx, my) => [left + (mx - ox) / k, top + (my - oy) / k] };
  const compose = () => {
    const c = base.getContext("2d");
    c.fillStyle = PLAY_MINI_DARK;
    c.fillRect(0, 0, W, H);
    c.imageSmoothingEnabled = true;
    c.imageSmoothingQuality = "high";
    c.drawImage(tiles, ox, oy, (right - left) * k, (bottom - top) * k);
    // NOTHING DRAWN OVER THE GROUNDS (the user, 2026-11-18: "你的小地图，不要标出block, 就正常色块"): the map's own tiles,
    // and the boards with the lands' and the main work's names (playMiniLabels) - no box, no dots, no outline.
    c.fillStyle = "#f2c94c"; c.strokeStyle = "#3b2a10";
    for (const one of index.landmarks) { const [x, y] = at(one.cx, one.cy); c.fillRect(x - 2, y - 2, 4, 4); c.strokeRect(x - 2.5, y - 2.5, 5, 5); }
    playMiniLabels(c, at);
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
  const shown = () => {
    compose();
    if (behind && !finished) return;                                       // out of sight till it is whole
    if (behind) {
      if (play.miniBuilding !== mine || play.map !== index) return;
      play.miniBase = mine; play.miniBuilding = null;
    }
    if (play.on) playMiniDraw();
  };
  const step = () => {
    // Another world or another area since: stop - never put in place over the map now walked.
    if ((behind ? play.miniBuilding : play.miniBase) !== mine || play.map !== index) return;
    const until = performance.now() + 14;
    while (todo.length && performance.now() < until) {
      const [cx, cy] = todo.shift();
      waiting += 1;
      if (playMiniBlock(tiles, left, top, cx, cy, () => { waiting -= 1; if (!todo.length && !waiting && !finished) { finished = true; shown(); if (drawn) playMiniKeep(); } })) drawn += 1;
    }
    shown();
    if (todo.length) setTimeout(step, 40);
  };
  if (behind) play.miniBuilding = mine;
  else { play.miniBase = mine; play.miniBuilding = null; }
  // AFTER THE VIEW (the user, 2026-11-18: "world先加载用户旁边的 ... 小地图是一次性画好的"): the walker's own blocks drawn
  // first; the map begun when the page is idle, made once and kept (playMiniKept).
  const begin = () => setTimeout(step, 0);
  if (typeof requestIdleCallback === "function") setTimeout(() => requestIdleCallback(begin, { timeout: 3000 }), 1200);
  else setTimeout(begin, 1500);
}
/* A GREAT GROUND SEEN ON THE MAP (the user, 2026-11-18: "如果是大的景观，设计要在小地图上看得出来东西"): shrunk to the map
   a project's ground is a smudge, so over it, larger than the map's scale - its edge in its colour, its arranged things
   as dots of their own colours (rows of melons read as rows), its landmark's outline with its emblem, its name. */
const playMiniColours = new Map();
function playMiniColour(item) {
  const key = item.pixels ? `p${item.id}` : item.kind;
  if (playMiniColours.has(key)) return playMiniColours.get(key);
  let colour = "#c9b98f";
  try {
    const [w, h] = PLAY_SIZES[item.kind] || [1, 1];
    const one = document.createElement("canvas");
    one.width = w * 16; one.height = h * 16;
    const c = one.getContext("2d");
    playThing(c, { ...item, x: 0, y: 0 }, 0, 0);
    const data = c.getImageData(0, 0, one.width, one.height).data;
    let r = 0, g = 0, b = 0, n = 0;
    for (let i = 0; i < data.length; i += 4) if (data[i + 3] > 128) { r += data[i]; g += data[i + 1]; b += data[i + 2]; n += 1; }
    if (n) colour = `rgb(${Math.round(r / n)}, ${Math.round(g / n)}, ${Math.round(b / n)})`;
    else return colour;                                  // its picture not here yet: asked again next time
  } catch (_err) { return colour; }
  playMiniColours.set(key, colour);
  return colour;
}
function playMiniGrounds(c, at, k) {
  const index = play.map;
  const plots = index.zones.filter((one) => one.zone.plot);
  if (!plots.length) return;
  const zones = new Map(index.zones.map((one) => [one.zone.id, one]));
  c.save();
  for (const one of plots) {
    const plot = one.zone;
    const main = play.world.zones.find((z) => z.task === plot.task && !z.plot && (z.landmark || !z.part_of)) || plot;
    const colour = main.floor_color || "#2f7d3a";
    const [x0, y0] = at(one.rect[0], one.rect[1]), [x1, y1] = at(one.rect[2], one.rect[3]);
    if (x1 - x0 < 6) continue;
    // NO BOX DRAWN ROUND IT (the user, 2026-11-18: "你的小地图，不要标出block, 就正常色块"): its ground as it is.
    // Its things, in their own colours: rows read as rows.
    const dot = Math.max(1.5, k * 0.9);
    for (const item of play.world.items) {
      const z = zones.get(item.zone);
      if (!z || !(z.zone === plot || z.zone.task === plot.task)) continue;
      const [ix, iy] = at(item.x + 0.5, item.y + 0.5);
      if (ix < x0 || ix > x1 || iy < y0 || iy > y1) continue;
      c.fillStyle = playMiniColour(item);
      c.fillRect(ix - dot / 2, iy - dot / 2, dot, dot);
    }
    // Its landmark: the hall, the tower and its dome in outline, its emblem on it - never smaller than reads.
    const hall = main.landmark ? zones.get(main.id) : null;
    if (hall) {
      const [hx0, hy] = at(hall.rect[0], hall.rect[1]), [hx1] = at(hall.rect[2], hall.rect[1]);
      const hw = Math.max(14, hx1 - hx0), cx = (hx0 + hx1) / 2, hh = Math.max(5, hw * 0.22);
      const tw = hw * 0.32, th = hh * 2.2;
      c.fillStyle = "#efe4cc"; c.strokeStyle = "#3b2a10"; c.lineWidth = 1;
      c.fillRect(cx - hw / 2, hy - hh, hw, hh); c.strokeRect(cx - hw / 2 + 0.5, hy - hh + 0.5, hw - 1, hh - 1);
      c.fillRect(cx - tw / 2, hy - hh - th, tw, th); c.strokeRect(cx - tw / 2 + 0.5, hy - hh - th + 0.5, tw - 1, th);
      c.fillStyle = colour; c.beginPath(); c.ellipse(cx, hy - hh - th, tw * 0.62, tw * 0.5, 0, Math.PI, 0); c.fill(); c.stroke();
      const mural = main.mural;
      if (mural && mural.rows && mural.rows.length) {
        const cols = Math.max(...mural.rows.map((r) => r.length));
        const cell = Math.max(0.6, Math.min(tw * 0.9 / cols, th * 0.8 / mural.rows.length));
        const ex = cx - (cols * cell) / 2, ey = hy - hh - th + (th - mural.rows.length * cell) / 2;
        mural.rows.forEach((row, ry) => [...row].forEach((ch, rx) => {
          const fill = ch === "." ? null : mural.palette[Number(ch)];
          if (fill) { c.fillStyle = fill; c.fillRect(ex + rx * cell, ey + ry * cell, cell + 0.2, cell + 0.2); }
        }));
      }
    }
    // Its name, when there is room for it.
    const name = main.floor_text || main.task_title || plot.name || "";
    if (name && x1 - x0 > 36) {
      c.font = 'bold 9px "Microsoft YaHei", "PingFang SC", system-ui, sans-serif';
      c.textBaseline = "bottom"; c.lineWidth = 3; c.strokeStyle = "rgba(0, 0, 0, 0.75)"; c.fillStyle = "#fff";
      c.strokeText(name, x0 + 3, y1 - 2, x1 - x0 - 6); c.fillText(name, x0 + 3, y1 - 2, x1 - x0 - 6);   // narrowed, never cut
    }
  }
  c.restore();
}
/* THE MAIN WORK AT A GLANCE, AND EVERY LAND'S NAME (the user, 2026-11-18: "你的造景我还是很难在小地图上一眼看出哪个是
   personal agent"; a world map with each place's name on a board, for what is meant): the work the user gave the most
   time to - a project's tasks together (world.projects), else a task - its places ringed in glowing gold and its name
   on the largest board, a star before it, over them; each land's name on a small wooden board over its middle. Weighed
   by the minutes kept (world.tasks), or, in a world drawn before they were kept, by the ground its places take. */
function playMainWork(index) {
  const book = play.world.tasks || {};
  const projectOf = new Map();
  for (const p of play.world.projects || []) for (const t of p.tasks || []) projectOf.set(t, p);
  const groups = new Map();
  for (const one of index.zones) {
    const task = one.zone.task;
    if (!task) continue;
    const p = projectOf.get(task), key = p ? `p:${p.id || p.name}` : `t:${task}`;
    if (!groups.has(key)) groups.set(key, { project: p, tasks: new Set(), zones: [], area: 0 });
    const g = groups.get(key);
    g.zones.push(one); g.tasks.add(task); g.area += one.zone.w * one.zone.h;
  }
  const all = [...groups.values()];
  for (const g of all) g.minutes = [...g.tasks].reduce((s, t) => s + (Number(book[t] && book[t].minutes) || 0), 0);
  const byMinutes = all.some((g) => g.minutes > 0);
  const main = all.sort((p, q) => (byMinutes ? q.minutes - p.minutes : q.area - p.area))[0];
  if (!main) return null;
  const biggest = main.zones.slice().sort((p, q) => q.zone.w * q.zone.h - p.zone.w * p.zone.h)[0].zone;
  main.name = (main.project && main.project.name) || biggest.floor_text || biggest.name || biggest.task_title || "";
  return main;
}
function playMiniBoard(c, text, cx, cy, px, gold) {
  // A board: dark wood, a light face, the words on it - narrowed to fit, never cut.
  c.save();
  c.font = `bold ${px}px "Microsoft YaHei", "PingFang SC", system-ui, sans-serif`;
  c.textAlign = "center"; c.textBaseline = "middle";
  const most = Math.max(60, c.canvas.width * 0.42);
  const w = Math.min(most, c.measureText(text).width) + px * 1.4, h = px * 1.7;
  const x = Math.max(2, Math.min(c.canvas.width - w - 2, cx - w / 2)), y = Math.max(2, Math.min(c.canvas.height - h - 2, cy - h / 2));
  if (gold) { c.shadowColor = "rgba(255, 213, 74, 0.9)"; c.shadowBlur = px * 0.9; }
  c.fillStyle = gold ? "#6b3f12" : "#5a3a1e";
  c.beginPath(); c.roundRect(x, y, w, h, px * 0.35); c.fill();
  c.shadowBlur = 0;
  c.fillStyle = gold ? "#f6c945" : "#e9d3a8";
  c.beginPath(); c.roundRect(x + px * 0.18, y + px * 0.18, w - px * 0.36, h - px * 0.36, px * 0.25); c.fill();
  c.fillStyle = gold ? "#3b2008" : "#3b2a14";
  c.fillText(text, x + w / 2, y + h / 2 + px * 0.06, w - px * 0.9);
  c.restore();
  return [x, y, x + w, y + h];
}
function playMiniLabels(c, at) {
  const index = play.map, W = c.canvas.width;
  const big = Math.round(Math.max(11, Math.min(26, W / 26))), small = Math.round(Math.max(8, Math.min(17, W / 44)));
  const taken = [];
  const main = playMainWork(index);
  if (main) {
    // Its board over the top of its places - no box drawn round them (the user, 2026-11-18: "你的小地图，不要标出block,
    // 就正常色块").
    let top = Infinity, left = Infinity, right = -Infinity;
    for (const one of main.zones) {
      const [x0, y0] = at(one.rect[0], one.rect[1]), [x1] = at(one.rect[2], one.rect[3]);
      top = Math.min(top, y0); left = Math.min(left, x0); right = Math.max(right, x1);
    }
    if (main.name) taken.push(playMiniBoard(c, `★ ${main.name}`, (left + right) / 2, top - big * 1.3, big, true));
  }
  // Each land's name over its middle - off the main work's board.
  for (const region of index.regions) {
    const mine = index.zones.filter((one) => one.region === region);
    if (!mine.length || !region.name) continue;
    const xs = mine.flatMap((one) => [one.rect[0], one.rect[2]]), ys = mine.flatMap((one) => [one.rect[1], one.rect[3]]);
    let [cx, cy] = at((Math.min(...xs) + Math.max(...xs)) / 2, Math.min(...ys));
    cy -= small * 1.6;
    const box = (y) => [cx - 60, y - small, cx + 60, y + small];
    const hits = (b) => taken.some((t) => b[0] < t[2] && b[2] > t[0] && b[1] < t[3] && b[3] > t[1]);
    if (hits(box(cy))) cy = at(0, Math.max(...ys))[1] + small * 1.6;      // under its land instead
    taken.push(playMiniBoard(c, playWords(region.name), cx, cy, small, false));
  }
}
function playMiniDraw() {
  if (!play.mini || play.mini.hidden || !play.map) return;
  if (!play.miniBase) playMiniBase();
  const { canvas: base, at, k } = play.miniBase;
  const c = play.mini.getContext("2d");
  c.drawImage(base, 0, 0);
  const size = PLAY_TILE * play.scale;
  const vw = (play.canvas.width / size) * k, vh = (play.canvas.height / size) * k;
  // WHERE THE AGENT IS, IN YELLOW (the user, 2026-11-18: "agent view用黄色边框，这样可以很清楚的看到agent在哪"): the view
  // round the walker; looked elsewhere on the map (playMiniLook), what is in sight in white besides.
  if (play.lookAt) {
    const [lx, ly] = at(play.camera.x / size, play.camera.y / size);
    c.strokeStyle = "rgba(255, 255, 255, 0.9)"; c.lineWidth = 1;
    c.strokeRect(lx + 0.5, ly + 0.5, vw, vh);
  }
  const [ax, ay] = at(play.me.x - play.canvas.width / size / 2, play.me.y - play.canvas.height / size / 2);
  c.strokeStyle = "#ffd54a"; c.lineWidth = 2;
  c.strokeRect(ax + 1, ay + 1, vw - 1, vh - 1);
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
  if (fx + fw < x0 - 2 || fx > x0 + PLAY_CHUNK || fy - 4 > y0 + PLAY_CHUNK || fy + fh < y0) return;
  let left, top, w, h;
  if (zone.art_on === "wall" && !PLAY_OUTDOOR[zone.kind] && (zone.form || "closed") !== "pavilion") {
    // ON ITS WALL (the user: "比如刷墙"): painted along the face of its back wall, two tiles high.
    left = (zone.x + 1.5 - x0) * 16; top = (zone.y + 1 - y0) * 16; w = (zone.w - 1) * 16; h = 32;
  } else {
    // ON ITS FLOOR, A THIRD OF IT AT MOST, in its upper part - its sign stands at its foot.
    const aw = Math.max(2, Math.round((fw - 2) * 0.6)), ah = Math.max(2, Math.min(fh - 4, Math.round(((fw - 2) * (fh - 2)) / 3 / aw)));
    left = (fx + 1 + ((fw - 2) - aw) / 2 - x0) * 16; top = (fy + 1 - y0) * 16; w = aw * 16; h = ah * 16;
  }
  if (w < 32 || h < 24) return;
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
        // IN PIXELS, AS EVERY PICTURE ON THE MAP (the user, 2026-11-19: "在地图上所有图片尽可能是像素风"): its own
        // palette (playFinePixel), not a picture merely made small.
        const small = playFinePixel(image, Math.max(1, Math.round(dw / 4)), Math.max(1, Math.round(dh / 4)),
                                    { outline: false, colours: 16 });
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
  if (!play.map) return false;                                 // still loading: the keys held wait for it
  if (play.ride) return false;                                // riding: the vehicle moves it (play-act.js)
  if (play.sit && play.keys.size) {
    // A STEP: STOOD UP WHERE IT STOOD BEFORE (the user, 2026-11-18: "你现在椅子sit之后不能起身了" - sat on the seat, it
    // was in the seat's own tile, which stands in the way, and every step from it met the seat): back where it sat
    // down from, then on - or, that taken since, to free ground near.
    const was = play.sit;
    play.sit = null;
    play.me.x = was.fromX;
    play.me.y = was.fromY;
    if (playBlocked(play.me.x, play.me.y)) playUnstuck();
  }
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
/* THE FIRST STEP OUTSIDE (the user, 2026-11-19: "等有足够数据（心情）再设置初始外景，第一次走出"): the land the feeling of their
   days made, named on the sign as they step out of the house - once, for this world. */
function playFirstOutside(region) {
  const key = `playOutside:${play.world && play.world.born}`;
  try {
    if (localStorage.getItem(key)) return;
    localStorage.setItem(key, "1");
  } catch (_err) { return; }
  play.banner = { name: playWords(region.name || ""), since: playWords("The world outside, for the first time"),
                  at: performance.now(), words: null, region: true };
  playSoundChime();
}
/* NEAR A PLACE, ITS MUSIC; OUT IN THE LAND, THE LAND'S (the user, 2026-11-19: "其实不应该有公共区域，所有位置的音乐就是他
   最近的主题区域的音乐"; then "户外也可以有自己的音乐"): just outside a place - PLAY_PLACE_HEARD tiles of it - the
   place's own music still; farther out, the land's own (its feeling's music, else its scene's, play-sound.js
   PLAY_SCENE_TUNES). What is heard is kept till another is nearer by PLAY_HEARD_MARGIN tiles, so it does not flicker on
   a boundary. */
const PLAY_HEARD_MARGIN = 2, PLAY_PLACE_HEARD = 4;
function playNearestZone(x, y, within = Infinity) {
  let best = null, bestD = Infinity, held = null, heldD = Infinity;
  for (const one of (play.world && play.world.zones) || []) {
    if (one.plot) continue;
    const [fx, fy, fw, fh] = playFloorOf(one);
    const d = Math.hypot(Math.max(fx - x, 0, x - (fx + fw)), Math.max(fy - y, 0, y - (fy + fh)));
    if (d < bestD) { best = one; bestD = d; }
    if (one.id === play.heardId) { held = one; heldD = d; }
  }
  const [one, d] = held && heldD <= bestD + PLAY_HEARD_MARGIN ? [held, heldD] : [best, bestD];
  return one && d <= within + (one.id === play.heardId ? PLAY_HEARD_MARGIN : 0) ? one : null;
}
function playOutsideHeard(x, y) {
  const region = (play.map && playRegionAt(play.map, x, y)) || play.region;
  return playNearestZone(x, y, PLAY_PLACE_HEARD) || playRegionSound(region) || playNearestZone(x, y);
}
/* THE MUSIC HEARD HERE (not playHeard - the sounds of things near, above - whose name this once took, leaving the
   fire, the water and the birds silent): the place walked in, else what is heard outside. */
function playMusicHeard(x = play.me.x, y = play.me.y) {
  return play.zone || playOutsideHeard(x, y);
}
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
                      words: null, region: true, feeling: region.feeling || "", toThem: region.to_them || "" };
      playSoundChime();
    }
    if (!zone) playSoundRoom(playOutsideHeard(tx, ty));
  }
  if (!zone && play.zone && region && region.opened && !(play.map && play.map.closed)) playFirstOutside(region);
  if ((zone && zone.id) !== (play.zone && play.zone.id)) {
    const wasIn = play.zone && !PLAY_OUTDOOR[play.zone.kind], nowIn = zone && !PLAY_OUTDOOR[zone.kind];
    if (wasIn !== nowIn && (wasIn || nowIn)) playSfx(nowIn ? "door-open" : "door-close", 0.35);
    play.zone = zone;
    playSoundRoom(playMusicHeard(tx, ty));
    if (zone && !(play.banner && play.banner.region && performance.now() - play.banner.at < 1200)) {
      // When it was lived, not when it was built (the user, 2026-11-17: "这个时间也是event时间，而不是make 时间").
      play.banner = { name: zone.name || zone.kind, since: playPeriod(playLived(zone)),
                      at: performance.now(), words: null };
    }
  }
  if (!zone) {                                       // outside: a place's music near it, the land's farther out
    const heard = playOutsideHeard(tx, ty);
    if (heard && heard.id !== play.heardId) { play.heardId = heard.id; playSoundRoom(heard); }
  } else play.heardId = zone.id;
  let near = null, best = 1.6;
  // NOT THROUGH A WALL (the user, 2026-11-17: "除了窗户，在房屋外面不能触发某个内部设施的介绍"): a thing in a building is
  // known from inside its room; from outside, only its windows. An open place has no walls.
  const here = play.zone ? play.zone.id : "";
  const kinds = play.zoneKinds || (play.zoneKinds = Object.fromEntries(play.world.zones.map((one) => [one.id, one.kind])));
  for (const item of play.world.items) {
    if (item.zone !== here && item.kind !== "window" && !PLAY_OUTDOOR[kinds[item.zone]]) continue;
    const [iw, ih] = playItemSize(item);
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
                 zone.concept && zone.concept.idea ? `${playWords("The idea")}: ${zone.concept.idea}` : "",
                 zone.rooms && zone.rooms.length ? `${playWords("Rooms")}: ${zone.rooms.map((one) => one.name).join(" · ")}` : "",
                 zone.group_titles && zone.group_titles.length ? `${playWords("Small tasks")}: ${zone.group_titles.map((one) => playShort(one, 28)).join(" · ")}` : "",
                 `${sign.kind} · ${things} ${playWords(things === 1 ? "thing" : "things")}`];
  const built = `${playWords("Built")} ${String(zone.made || "").slice(0, 10)}`;
  return `<div class="side-play-card-words"><strong class="chat-message">${escapeHtml(zone.name || zone.kind)}</strong>
    ${lines.filter(Boolean).map((one) => `<span class="side-play-card-when">${escapeHtml(one)}</span>`).join("")}
    ${zone.why ? `<p class="chat-message">${escapeHtml(zone.why)}</p>` : ""}
    ${playMuseCard(zone)}
    ${playGreatCard(zone)}
    ${zone.task_summary ? `<p class="side-play-card-from chat-message">${escapeHtml(zone.task_summary)}</p>` : ""}
    <p class="side-play-card-from chat-message">${escapeHtml(built)}${grown.length ? escapeHtml(` · ${playWords("Grown")}: ${grown.join("; ")}`) : ""}</p></div>`;
}

/* ITS MUSES (agent/playmuse.py; the user, 2026-11-18: "让agent用诗歌，音乐，绘画，视频各找一个符合她意境的内容"): the poem,
   the music, the painting and the video its place was designed from - what was found, by its link. */
function playMuseCard(zone) {
  const m = zone.muse;
  if (!m) return "";
  const link = (one) => one && one.url ? ` <a href="${escapeHtml(one.url)}" target="_blank" rel="noopener">${escapeHtml(playWords("open"))}</a>` : "";
  const who = (one) => [one.title, one.by, one.year].filter(Boolean).join(" · ");
  const rows = [];
  if (m.poem) rows.push(`<b>${escapeHtml(playWords("Poem"))}:</b> ${escapeHtml(who(m.poem))}${link(m.poem)}${(m.poem.lines || []).length ? `<br><i>${m.poem.lines.map(escapeHtml).join("<br>")}</i>` : ""}`);
  if (m.music) rows.push(`<b>${escapeHtml(playWords("Music"))}:</b> ${escapeHtml(who(m.music))}${link(m.music)}`);
  if (m.painting) rows.push(`<b>${escapeHtml(playWords("Painting"))}:</b> ${escapeHtml(who(m.painting))}${link(m.painting)}`);
  if (m.video) rows.push(`<b>${escapeHtml(playWords("Video"))}:</b> ${escapeHtml(who(m.video))}${link(m.video)}${m.video.why ? ` - ${escapeHtml(m.video.why)}` : ""}`);
  if (m.design) rows.push(`<b>${escapeHtml(playWords("What they asked of it"))}:</b> ${escapeHtml(m.design)}`);
  return rows.length ? `<p class="side-play-card-from chat-message">${rows.join("<br>")}</p>` : "";
}
/* A THING OF A GREAT GROUND (agent/playworld.py _lay_great): the stage it shows - how far that stage is, what it made -
   and, a featured step, why it stood out (the user: "progress (第一阶段成果，第二阶段成果)"; "featured step"). */
function playGreatThingCard(item) {
  const zone = play.world.zones.find((one) => one.id === item.zone);
  const kept = zone && zone.great && (play.world.great || {})[zone.great];
  const rows = [];
  if (kept && item.stage) {
    const stage = (kept.stages || []).find((one) => one.n === item.stage);
    if (stage) {
      rows.push(`<b>${escapeHtml(playWords("Stage"))} ${stage.n}:</b> ${escapeHtml(stage.title || "")} · ${stage.done}/${stage.of}`
        + (stage.result ? `<br>${escapeHtml(playWords("What it made"))}: ${escapeHtml(stage.result)}` : ""));
    }
  }
  if (item.featured !== undefined) rows.push(`<b>${escapeHtml(playWords("A step that stood out"))}</b>${item.featured ? `: ${escapeHtml(item.featured)}` : ""}`);
  return rows.length ? `<p class="side-play-card-from chat-message">${rows.join("<br>")}</p>` : "";
}
/* A GREAT GROUND'S OWN CARD: its idea, the places it draws from, its stages one by one. */
function playGreatCard(zone) {
  const kept = zone && zone.great && (play.world.great || {})[zone.great];
  if (!kept) return "";
  const rows = [];
  if ((kept.references || []).length) {
    rows.push(`<b>${escapeHtml(playWords("Drawn from"))}:</b> ${kept.references.map((one) => escapeHtml(one.name)
      + (/^https?:\/\//.test(one.url || "") ? ` <a href="${escapeHtml(one.url)}" target="_blank" rel="noopener">${escapeHtml(playWords("open"))}</a>` : "")).join(" · ")}`);
  }
  for (const stage of kept.stages || []) {
    rows.push(`${escapeHtml(playWords("Stage"))} ${stage.n}: ${escapeHtml(stage.title || "")} · ${stage.done}/${stage.of}`);
  }
  return rows.length ? `<p class="side-play-card-from chat-message">${rows.join("<br>")}</p>` : "";
}
/* THE WORLD THEN (agent/playworld.py _place_news; the user, 2026-11-19: "在你做这个project的时候，世界上发生了什么大事"):
   the headlines of the days this work was done, in the language shown, each with where it was reported. */
function playNewsCard(item) {
  const news = Array.isArray(item.news) ? item.news : [];
  if (!news.length) return "";
  const said = String(item.news_text || "").split(" · ");
  const rows = news.map((one, n) => {
    const text = said.length === news.length ? said[n] : `${String(one.date || "").slice(5)} ${one.headline || ""}`;
    const link = /^https?:\/\//.test(one.url || "") ? ` <a href="${escapeHtml(one.url)}" target="_blank" rel="noopener">${escapeHtml(playWords("source"))}</a>` : "";
    return `${escapeHtml(text)}${link}`;
  });
  return `<p class="side-play-card-from chat-message"><b>${escapeHtml(playWords("The world then"))}:</b><br>${rows.join("<br>")}</p>`;
}
/* A CARD OF THE WORK, AS ITS THING SAYS IT (the user, 2026-11-19: "每个card都要有一个（或者多个item)代表"). */
const PLAY_CARD_STATUS = { open: "Open", working: "Under way", done: "Done", set_aside: "Set aside" };
function playCard() {
  const card = play.el.querySelector(".side-play-card");
  const item = play.near;
  if (!item) { card.hidden = true; return; }
  if (item.at) { card.innerHTML = playSignCard(item); card.hidden = false; return; }          // a sign, not a thing
  const made = String(item.made || "").slice(0, 10);
  // ITS REAL PICTURE, IN ITS CARD (the user, 2026-11-19: "item会和截图connect起来"; "图片用户可以点击，显示原图"): its
  // screenshot, or the picture it shows - pressed, the original whole, in its own box (sideLarger).
  // The original itself, sharp in the card and when opened (the user, 2026-11-19: "你的图片放大之后清晰度要高"; "加载原图").
  const real = item.original_url || item.shot_url || item.picture_url;
  card.innerHTML = `${real ? `<img class="side-play-card-real" src="${escapeHtml(real)}" alt="${escapeHtml(item.shot_what || "")}"
    title="${escapeHtml(playWords("Open the original"))}" style="cursor: zoom-in">` : ""}
    <div class="side-play-card-words">
      <strong class="chat-message">${escapeHtml(item.name || item.kind)}</strong>
      <span class="side-play-card-when">${escapeHtml(playPeriod(item.days) || "")}</span>
      ${item.group ? `<span class="side-play-card-when">${escapeHtml(`${playWords("One of")} ${play.world.items.filter((one) => one.group === item.group).length}`)}</span>` : ""}
      ${item.why ? `<p class="chat-message side-play-card-said">${escapeHtml(item.why)}</p>` : ""}
      ${(item.poem || []).length ? `<p class="chat-message"><i>${item.poem.map(escapeHtml).join("<br>")}</i></p>` : ""}
      ${playNewsCard(item)}
      ${playGreatThingCard(item)}
      ${item.kind === "widget" ? `<p class="side-play-card-from chat-message"><b>${escapeHtml(playWords("How far it has come"))}:</b> ${playWidgetStage(item) < 0 ? escapeHtml(playWords("Set aside")) : `${playWidgetStage(item) + 1}/${PLAY_WIDGETS[item.widget] || 4} · ${Math.round(100 * playWidgetProgress(item).share)}%`}</p>` : ""}
      ${item.card_title ? `<p class="side-play-card-from chat-message"><b>${escapeHtml(playWords("Its work"))}:</b> ${escapeHtml(item.card_title)}
        · ${escapeHtml(playWords(PLAY_CARD_STATUS[item.card_status] || "Open"))}${item.card_statement ? `<br>${escapeHtml(playWords("Done when"))}: ${escapeHtml(item.card_statement)}` : ""}</p>` : ""}
      ${item.from ? `<p class="side-play-card-from chat-message">${escapeHtml(item.from)}</p>` : ""}
      <p class="side-play-card-from chat-message">${escapeHtml(`${playWords("Built")} ${made}`)}</p>
    </div>`;
  const shown = card.querySelector(".side-play-card-real");
  if (shown && typeof sideLarger === "function") {
    shown.addEventListener("click", () => sideLarger(item.original_url || real));
  }
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
    if (zone.landmark) { playLandmarkDraw(zone, size, night); continue; }
    const style = zone.style;
    if (!PLAY_FACADES[style] && style !== "school" && style !== "laboratory") continue;
    if (zone.shape && zone.shape.kind && zone.shape.kind !== "rect" && !PLAY_OUTDOOR[zone.kind]) continue;   // a room of its own outline: no square front over it
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

/* A DISTRICT'S LANDMARK (the user, 2026-11-18: "我如何形成大的地标性建筑"): behind its main place a great building in the
   project's own colour - a broad hall, a tower rising from it, a dome and a spire; its name on a banner, its emblem (the
   picture drawn for it) over the door; lit windows by night. Seen from far off. */
function playLandmarkDraw(zone, size, night) {
  const { ctx, canvas } = play;
  const left = zone.x * size - play.camera.x, base = zone.y * size - play.camera.y, w = (zone.w + 2) * size;
  if (left > canvas.width || left + w < 0 || base - 14 * size > canvas.height || base + 2 * size < 0) return;
  const colour = zone.floor_color || "#2f7d3a";
  ctx.save();
  // The hall: as wide as the place, three floors.
  const hallH = size * 3.2, hallTop = base - hallH;
  ctx.fillStyle = "#e9dcc2"; ctx.fillRect(left + size * 0.5, hallTop, w - size, hallH);
  ctx.fillStyle = colour; ctx.fillRect(left + size * 0.3, hallTop - size * 0.4, w - size * 0.6, size * 0.45);
  for (let k = 0; k < Math.floor((w - size) / (size * 1.1)); k += 1) {             // its columns, its windows
    const x = left + size * 0.9 + k * size * 1.1;
    ctx.fillStyle = "#cbbd9f"; ctx.fillRect(x, hallTop + size * 0.2, size * 0.18, hallH - size * 0.2);
    ctx.fillStyle = night && playHash(zone.x + k, zone.y, 73) < 0.6 ? "#ffe08a" : "#7aa6c9";
    ctx.fillRect(x + size * 0.32, hallTop + size * 0.5, size * 0.45, size * 0.7);
    ctx.fillRect(x + size * 0.32, hallTop + size * 1.7, size * 0.45, size * 0.7);
  }
  // The tower in its middle, a dome and a spire on it.
  const tw = Math.min(w * 0.3, size * 6), tx = left + (w - tw) / 2, towerH = size * 6.5, towerTop = hallTop - towerH;
  ctx.fillStyle = "#efe4cc"; ctx.fillRect(tx, towerTop, tw, towerH);
  ctx.fillStyle = "#d8c9a8"; ctx.fillRect(tx + tw * 0.78, towerTop, tw * 0.22, towerH);
  for (let f = 0; f < 5; f += 1) {
    ctx.fillStyle = night && playHash(zone.x, zone.y + f, 79) < 0.7 ? "#ffe08a" : "#7aa6c9";
    ctx.fillRect(tx + tw * 0.3, towerTop + size * (0.5 + f * 1.2), tw * 0.4, size * 0.6);
  }
  ctx.fillStyle = colour;
  ctx.beginPath(); ctx.ellipse(tx + tw / 2, towerTop, tw * 0.62, tw * 0.5, 0, Math.PI, 0); ctx.fill();
  ctx.fillStyle = "#f2c94c"; ctx.fillRect(tx + tw / 2 - 1.5, towerTop - tw * 0.5 - size * 1.4, 3, size * 1.4);
  ctx.beginPath(); ctx.arc(tx + tw / 2, towerTop - tw * 0.5 - size * 1.45, size * 0.18, 0, Math.PI * 2); ctx.fill();
  // Its emblem, the picture drawn for it, over the tower's face; its name on a banner across the hall.
  if (zone.mural && zone.mural.rows && zone.mural.rows.length) {
    const rows = zone.mural.rows, cols = Math.max(...rows.map((r) => r.length));
    const cell = Math.max(1, Math.floor(Math.min(tw * 0.8 / cols, size * 2.2 / rows.length)));
    const ex = tx + (tw - cols * cell) / 2, ey = towerTop + size * 0.2;
    rows.forEach((row, ry) => [...row].forEach((ch, rx) => {
      const c = ch === "." ? null : zone.mural.palette[Number(ch)];
      if (c) { ctx.fillStyle = c; ctx.fillRect(ex + rx * cell, ey + ry * cell, cell, cell); }
    }));
  }
  const name = zone.floor_text || zone.task_title || zone.name || "";
  if (name) {
    const fontPx = Math.max(10, Math.round(size * 0.5));
    ctx.font = `bold ${fontPx}px "Microsoft YaHei", "PingFang SC", system-ui, sans-serif`;
    const nw = Math.min(w - size, ctx.measureText(name).width + size);
    ctx.fillStyle = colour; ctx.fillRect(left + (w - nw) / 2, hallTop - size * 1.1, nw, size * 0.7);
    ctx.fillStyle = "#fff"; ctx.textBaseline = "middle";
    ctx.fillText(name, left + (w - nw) / 2 + size * 0.5, hallTop - size * 0.75, nw - size);
  }
  ctx.restore();
}

/* GREAT PIECES (agent/playpieces.py; the user, 2026-11-19: "用类似过山车的构筑方法做"; "允许交互"): each built as the
   roller coaster is - its kit's parts assembled into one picture, drawn once and kept, over the world (so a tall one
   is never cut at a block's edge); its moving parts each frame. As much of it built as its work has come
   (playWidgetProgress): a tower up to there and the rest scaffolding, a track laid so far, a field ripening. A track
   ridden, a stack climbed; anything else looked at. */
const PLAY_PIECE_UP = 12, PLAY_PIECE_LAP_S = 14;
const PLAY_PIECE_UP_SVG = 12;            // agent/playpieces.py UP_TILES: the air a drawn piece rises in

function playPieceBuilt(item) {
  const { share, aside } = playWidgetProgress(item);
  return aside ? 0.3 : item.card || item.stage ? Math.max(0.15, share) : 1;
}
function playPieceTrack(item) {
  // Its path: a closed curve through its points (Catmull-Rom), each point's height from the element it is in.
  const spec = item.spec || {}, pts = spec.points || [], n = pts.length;
  const heights = pts.map(() => 0.3);
  for (const e of spec.elements || []) for (let i = e.at; i < n; i += 1) heights[i] = e.kind === "station" ? 0.3 : e.height;
  const out = [];
  for (let i = 0; i < n; i += 1) {
    const p0 = pts[(i - 1 + n) % n], p1 = pts[i], p2 = pts[(i + 1) % n], p3 = pts[(i + 2) % n];
    const h1 = heights[i], h2 = heights[(i + 1) % n];
    for (let s = 0; s < 8; s += 1) {
      const t = s / 8, t2 = t * t, t3 = t2 * t;
      const f = (a, b, c, d) => 0.5 * ((2 * b) + (-a + c) * t + (2 * a - 5 * b + 4 * c - d) * t2 + (-a + 3 * b - 3 * c + d) * t3);
      out.push({ x: item.x + f(p0[0], p1[0], p2[0], p3[0]), y: item.y + f(p0[1], p1[1], p2[1], p3[1]),
                 h: h1 + (h2 - h1) * (3 * t2 - 2 * t3), kind: ((spec.elements || []).filter((e) => e.at <= i).pop() || {}).kind });
    }
  }
  return out;
}
function playPiecePicture(item) {
  play.pieces = play.pieces || new Map();
  const built = playPieceBuilt(item);
  const key = `${item.id}:${item.x},${item.y}:${Math.round(built * 20)}:${JSON.stringify(item.spec || {}).length}`;
  if (play.pieces.has(key)) return play.pieces.get(key);
  const T = PLAY_TILE, w = item.w, h = item.h, spec = item.spec || {};
  const x0 = item.x - 1, y0 = item.y - 1 - PLAY_PIECE_UP;
  const canvas = document.createElement("canvas");
  canvas.width = (w + 2) * T; canvas.height = (h + 2 + PLAY_PIECE_UP) * T;
  const c = canvas.getContext("2d");
  const px = (x) => (x - x0) * T, py = (y, up = 0) => (y - up - y0) * T;
  c.fillStyle = "rgba(0, 0, 0, 0.16)";                                   // its shadow on its ground
  c.beginPath(); c.ellipse(px(item.x + w / 2), py(item.y + h * 0.6), w * T * 0.5, h * T * 0.42, 0, 0, Math.PI * 2); c.fill();
  const scaffold = (x, top, width, bottom) => {
    c.strokeStyle = "#a07a45"; c.lineWidth = 1;
    for (let k = x; k <= x + width + 0.01; k += 0.5) { c.beginPath(); c.moveTo(k, bottom); c.lineTo(k, top); c.stroke(); }
    for (let k = bottom; k >= top; k -= T / 2) { c.beginPath(); c.moveTo(x, k); c.lineTo(x + width, k); c.stroke(); }
  };
  let path = null;
  if (item.kit === "track") {
    path = playPieceTrack(item);
    const n = path.length, laid = Math.max(2, Math.floor(n * built));
    c.strokeStyle = "rgba(0, 0, 0, 0.18)"; c.lineWidth = 5; c.beginPath();
    path.forEach((p, i) => (i ? c.lineTo(px(p.x), py(p.y) + 3) : c.moveTo(px(p.x), py(p.y) + 3))); c.closePath(); c.stroke();
    for (let i = 0; i < n; i += 3) {                                     // supports down to the ground
      const p = path[i];
      if (p.h < 0.6) continue;
      c.fillStyle = i < laid ? "#7d8490" : "rgba(125, 132, 144, 0.35)";
      c.fillRect(Math.round(px(p.x)) - 1, py(p.y, p.h) + 3, 2, Math.max(0, py(p.y) - py(p.y, p.h) - 3));
    }
    const colour = /^#[0-9a-f]{6}$/i.test(spec.colour || "") ? spec.colour : "#d9382f";
    c.lineCap = "round";
    for (const [part, style, width] of [[0, colour, 3.2], [1, "rgba(255,255,255,0.35)", 1]]) {
      c.strokeStyle = style; c.lineWidth = width; c.beginPath();
      for (let i = 0; i <= laid; i += 1) { const p = path[i % n]; if (i) c.lineTo(px(p.x), py(p.y, p.h)); else c.moveTo(px(p.x), py(p.y, p.h)); }
      c.stroke(); void part;
    }
    if (laid < n) {                                                      // still to lay: its line drawn faint
      c.setLineDash([3, 3]); c.strokeStyle = "rgba(255, 255, 255, 0.5)"; c.lineWidth = 1; c.beginPath();
      for (let i = laid; i <= n; i += 1) { const p = path[i % n]; if (i > laid) c.lineTo(px(p.x), py(p.y, p.h)); else c.moveTo(px(p.x), py(p.y, p.h)); }
      c.stroke(); c.setLineDash([]);
    }
    for (const p of path.filter((one, i) => i < laid && one.kind === "loop" && i % 8 === 4)) {   // its loops, standing up
      c.strokeStyle = colour; c.lineWidth = 3; c.beginPath(); c.arc(px(p.x), py(p.y, p.h) - T * 1.1, T * 1.1, 0, Math.PI * 2); c.stroke();
    }
    const st = path[0];                                                  // its station
    c.fillStyle = "#c9a46a"; c.fillRect(px(st.x) - T, py(st.y) - T * 0.6, T * 2, T * 0.8);
    c.fillStyle = "#8a4f24"; c.fillRect(px(st.x) - T * 1.1, py(st.y) - T * 0.9, T * 2.2, T * 0.35);
  } else if (item.kit === "stack") {
    const segs = spec.segments || [], total = segs.reduce((s, one) => s + one.height, 0) || 1;
    let up = 0;
    const bottom = py(item.y + h - 0.3);
    for (const one of segs) {
      const sw = one.width * w * T, sx = px(item.x + w / 2) - sw / 2, sh = one.height * T;
      const done = (up + one.height) / total <= built + 1e-6, top = bottom - (up + one.height) * T;
      c.globalAlpha = done ? 1 : 0.25;
      c.fillStyle = one.colour; c.strokeStyle = "rgba(0, 0, 0, 0.45)"; c.lineWidth = 1;
      if (["spire", "roof", "nozzle"].includes(one.kind)) {
        c.beginPath();
        if (one.kind === "nozzle") { c.moveTo(sx + sw * 0.2, top); c.lineTo(sx + sw * 0.8, top); c.lineTo(sx + sw, top + sh); c.lineTo(sx, top + sh); }
        else { c.moveTo(sx, top + sh); c.lineTo(sx + sw / 2, top); c.lineTo(sx + sw, top + sh); }
        c.closePath(); c.fill(); c.stroke();
      } else if (one.kind === "dome") {
        c.beginPath(); c.ellipse(sx + sw / 2, top + sh, sw / 2, sh, 0, Math.PI, 0); c.fill(); c.stroke();
      } else {
        c.fillRect(sx, top, sw, sh); c.strokeRect(sx + 0.5, top + 0.5, sw - 1, sh - 1);
        c.fillStyle = "rgba(255, 255, 255, 0.18)"; c.fillRect(sx, top, sw * 0.25, sh);          // its lit side
        if (one.kind === "windows" || one.kind === "body") {
          c.fillStyle = "#cfe8f5";
          for (let wy = top + 3; wy < top + sh - 4; wy += 7) for (let wx = sx + 3; wx < sx + sw - 4; wx += 6) c.fillRect(wx, wy, 3, 4);
        }
        if (one.kind === "clock") { c.fillStyle = "#f7f4ec"; c.beginPath(); c.arc(sx + sw / 2, top + sh / 2, Math.min(sw, sh) * 0.3, 0, Math.PI * 2); c.fill(); }
        if (one.kind === "balcony" || one.kind === "battlement") { c.fillStyle = one.colour; for (let wx = sx - 2; wx < sx + sw + 2; wx += 4) c.fillRect(wx, top - 3, 2, 3); }
        if (one.kind === "fins") { c.fillStyle = one.colour; c.beginPath(); c.moveTo(sx, top); c.lineTo(sx - sw * 0.35, top + sh); c.lineTo(sx, top + sh); c.fill(); c.beginPath(); c.moveTo(sx + sw, top); c.lineTo(sx + sw * 1.35, top + sh); c.lineTo(sx + sw, top + sh); c.fill(); }
        if (one.kind === "antenna") { c.fillStyle = "#555"; c.fillRect(sx + sw / 2 - 1, top - T * 0.8, 2, T * 0.8); }
        if (one.kind === "lantern") { c.fillStyle = "rgba(255, 230, 140, 0.9)"; c.fillRect(sx + 2, top + 2, sw - 4, sh - 4); }
      }
      c.globalAlpha = 1;
      if (!done) scaffold(sx - 2, top, sw + 4, top + sh);
      up += one.height;
    }
    if (spec.door) { c.fillStyle = "#4a3424"; c.fillRect(px(item.x + w / 2) - T * 0.3, bottom - T * 0.8, T * 0.6, T * 0.8); }
  } else if (item.kit === "field") {
    const rows = spec.rows || 2, cols = spec.cols || 2, cw = w / cols, ch = h / rows;
    if (spec.fence) { c.strokeStyle = spec.fence; c.lineWidth = 2; c.strokeRect(px(item.x) + 1, py(item.y) + 1, w * T - 2, h * T - 2); }
    const [ww, wh] = PLAY_WIDGET_SIZES[spec.widget] || (String(spec.widget || "").startsWith("field_") ? [3, 2] : [2, 2]);
    const stage = { kind: "widget", widget: spec.widget, card: item.card, stage: item.stage, zone: item.zone };
    for (let r = 0; r < rows; r += 1) for (let k = 0; k < cols; k += 1) {
      c.save();
      c.translate(px(item.x + k * cw) + 1, py(item.y + r * ch) + 1);
      c.scale(Math.min(1, (cw * T - 2) / (ww * T)), Math.min(1, (ch * T - 2) / (wh * T)));
      playWidgetDraw(c, stage, 0, 0);
      c.restore();
    }
  } else if (item.kit === "water") {
    const colour = /^#[0-9a-f]{6}$/i.test(spec.colour || "") ? spec.colour : "#3e8fc9";
    const shape = { kind: spec.outline || "blob", seed: 2 };
    for (let y = item.y; y < item.y + h; y += 1) for (let x = item.x; x < item.x + w; x += 1) {
      if (!playShapeHas(shape, [item.x, item.y, w, h], x, y)) continue;
      c.fillStyle = colour; c.fillRect(px(x), py(y), T, T);
      c.fillStyle = "rgba(255, 255, 255, 0.12)"; if ((x + y) % 3 === 0) c.fillRect(px(x) + 3, py(y) + 6, 6, 1);
    }
    for (let k = 0; k < (spec.bridges || 0); k += 1) {
      const bx = item.x + Math.round((k + 1) * w / ((spec.bridges || 0) + 1));
      c.fillStyle = "#8b5a2b"; c.fillRect(px(bx), py(item.y), T, h * T);
      c.fillStyle = "#5c3c22"; c.fillRect(px(bx), py(item.y), 2, h * T); c.fillRect(px(bx) + T - 2, py(item.y), 2, h * T);
    }
    if (spec.fountain) { c.fillStyle = "#c8ccd2"; c.beginPath(); c.arc(px(item.x + w / 2), py(item.y + h / 2), T * 0.6, 0, Math.PI * 2); c.fill(); }
  } else if (item.kit === "composite") {
    const shapes = spec.shapes || [], shown = Math.max(1, Math.ceil(shapes.length * built));
    shapes.forEach((one, n) => {
      if (one.moves && one.moves !== "none") return;                    // its moving parts: each frame
      playPieceShape(c, one, px(item.x + one.x), py(item.y + one.y + 1, one.up), n < shown);
    });
    if (shown < shapes.length) {
      const tall = Math.max(...shapes.map((one) => one.h + one.up));
      scaffold(px(item.x), py(item.y + h, tall), w * T, py(item.y + h));
    }
  }
  const made = { canvas, x0, y0, path };
  play.pieces.set(key, made);
  if (play.pieces.size > 40) play.pieces.delete(play.pieces.keys().next().value);
  return made;
}
function playPieceShape(c, one, x, bottom, solid) {
  const T = PLAY_TILE, w = one.w * T, h = one.h * T, top = bottom - h;
  c.globalAlpha = solid ? 1 : 0.25;
  c.fillStyle = one.colour; c.strokeStyle = "rgba(0, 0, 0, 0.45)"; c.lineWidth = 1;
  if (one.kind === "circle") { c.beginPath(); c.ellipse(x + w / 2, top + h / 2, w / 2, h / 2, 0, 0, Math.PI * 2); c.fill(); c.stroke(); }
  else if (one.kind === "triangle") { c.beginPath(); c.moveTo(x, bottom); c.lineTo(x + w / 2, top); c.lineTo(x + w, bottom); c.closePath(); c.fill(); c.stroke(); }
  else if (one.kind === "cylinder") {
    c.fillRect(x, top + 3, w, h - 3); c.strokeRect(x + 0.5, top + 3.5, w - 1, h - 4);
    c.beginPath(); c.ellipse(x + w / 2, top + 3, w / 2, 3, 0, 0, Math.PI * 2); c.fill(); c.stroke();
    c.fillStyle = "rgba(255, 255, 255, 0.18)"; c.fillRect(x, top + 3, w * 0.25, h - 3);
  } else { c.fillRect(x, top, w, h); c.strokeRect(x + 0.5, top + 0.5, w - 1, h - 1); c.fillStyle = "rgba(255, 255, 255, 0.18)"; c.fillRect(x, top, w * 0.25, h); }
  c.globalAlpha = 1;
}
function playPieceInView(item) {
  const size = PLAY_TILE * play.scale, canvas = play.canvas;
  const x = (item.x - 1) * size - play.camera.x, y = (item.y - 1 - PLAY_PIECE_UP) * size - play.camera.y;
  const w = item.w > 0 ? item.w : 1, h = item.h > 0 ? item.h : 1;
  return !(x > canvas.width || y > canvas.height || x + (w + 2) * size < 0 || y + (h + 2 + PLAY_PIECE_UP) * size < 0);
}
/* FINE PIXELS (the user, 2026-11-19: "怎么让图片更精细，而不是像素块 ... 有一个图片后在像素块里尽可能复制图片"; style A): a
   picture - a great piece's own drawing, a picture of the user's made large, a painting - copied into pixels four times
   finer than the world's (each tile 64 x 64): drawn at that size, its colours brought to a palette of its own (median
   cut, PLAY_FINE_COLOURS), its edges given a dark outline one pixel wide - pixel art still, the world's own look, with
   sixteen times the detail. Drawn over the world at the screen's own size, never through a tile of 16. */
const PLAY_FINE = 4, PLAY_FINE_COLOURS = 24;
/* THE FINE PIXELS' OWN SUMS, on a picture's pixels (RGBA, W x H) in place: its palette from a sample of its pixels
   (PLAY_FINE_SAMPLES at most - never all of them sorted, which held the page), or the one given; each pixel its
   nearest; a stone's grey; the dark outline. Its palette. */
const PLAY_FINE_SAMPLES = 20000;
function playFineQuantize(px, W, H, options = {}) {
  const n = W * H;
  // Its palette: the colours of its opaque pixels, cut in halves by their widest channel till there are enough.
  const colours = [];
  const step = Math.max(1, Math.floor(n / PLAY_FINE_SAMPLES));
  for (let i = 0; i < n; i += step) {
    if (px[i * 4 + 3] < 128) continue;
    colours.push([px[i * 4], px[i * 4 + 1], px[i * 4 + 2]]);
  }
  let boxes = options.palette ? [] : colours.length ? [colours] : [];
  while (boxes.length < (options.colours || PLAY_FINE_COLOURS)) {
    let pick = -1, wide = 0, channel = 0;
    boxes.forEach((box, b) => {
      if (box.length < 2) return;
      for (let k = 0; k < 3; k += 1) {
        let lo = 255, hi = 0;
        for (const one of box) { if (one[k] < lo) lo = one[k]; if (one[k] > hi) hi = one[k]; }
        if (hi - lo > wide) { wide = hi - lo; pick = b; channel = k; }
      }
    });
    if (pick < 0 || wide < 6) break;
    const box = boxes[pick].sort((a, b) => a[channel] - b[channel]);
    boxes.splice(pick, 1, box.slice(0, box.length >> 1), box.slice(box.length >> 1));
  }
  const palette = options.palette || boxes.map((box) => [0, 1, 2].map((k) => Math.round(box.reduce((s, one) => s + one[k], 0) / box.length)));
  const nearest = new Map();
  const near = (r, g, b) => {
    const key = (r >> 2) << 12 | (g >> 2) << 6 | (b >> 2);
    if (nearest.has(key)) return nearest.get(key);
    let best = palette[0] || [r, g, b], far = Infinity;
    for (const one of palette) { const d = (one[0] - r) ** 2 + (one[1] - g) ** 2 + (one[2] - b) ** 2; if (d < far) { far = d; best = one; } }
    nearest.set(key, best);
    return best;
  };
  const solid = new Uint8Array(n);
  for (let i = 0; i < n; i += 1) {
    if (px[i * 4 + 3] < 128) { px[i * 4 + 3] = 0; continue; }
    const [r, g, b] = near(px[i * 4], px[i * 4 + 1], px[i * 4 + 2]);
    px[i * 4] = r; px[i * 4 + 1] = g; px[i * 4 + 2] = b; px[i * 4 + 3] = 255; solid[i] = 1;
  }
  if (options.stone) {                                       // a statue: its picture in stone
    for (let i = 0; i < n; i += 1) if (solid[i]) { const v = Math.round(0.3 * px[i * 4] + 0.59 * px[i * 4 + 1] + 0.11 * px[i * 4 + 2]); px[i * 4] = v + 6; px[i * 4 + 1] = v + 4; px[i * 4 + 2] = v; }
  }
  if (options.outline !== false) {                           // a dark line round it, one pixel
    for (let i = 0; i < n; i += 1) {
      if (solid[i]) continue;
      const x = i % W, y = (i / W) | 0;
      const by = [[1, 0], [-1, 0], [0, 1], [0, -1]].find(([dx, dy]) => { const j = (y + dy) * W + x + dx; return x + dx >= 0 && x + dx < W && y + dy >= 0 && j < n && solid[j]; });
      if (!by) continue;
      const j = (y + by[1]) * W + x + by[0];
      px[i * 4] = px[j * 4] * 0.35; px[i * 4 + 1] = px[j * 4 + 1] * 0.35; px[i * 4 + 2] = px[j * 4 + 2] * 0.4; px[i * 4 + 3] = 255;
    }
  }
  return palette;
}
function playFinePixel(source, width, height, options = {}) {
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(width)); canvas.height = Math.max(1, Math.round(height));
  const c = canvas.getContext("2d");
  c.imageSmoothingEnabled = true; c.imageSmoothingQuality = "high";
  if (options.fit) {                                         // a picture of the user's: its middle, filling the frame
    const sw = source.naturalWidth || source.width, sh = source.naturalHeight || source.height;
    const k = Math.max(canvas.width / sw, canvas.height / sh), dw = sw * k, dh = sh * k;
    c.drawImage(source, (canvas.width - dw) / 2, (canvas.height - dh) / 2, dw, dh);
  } else c.drawImage(source, 0, 0, canvas.width, canvas.height);
  let data;
  try { data = c.getImageData(0, 0, canvas.width, canvas.height); } catch (_err) { return canvas; }
  canvas.palette = playFineQuantize(data.data, canvas.width, canvas.height, options);
  c.putImageData(data, 0, 0);
  return canvas;
}
/* A PICTURE (SVG) INTO AN IMAGE: when it is ready, `done(image)`. */
function playSvgImage(svg, done) {
  const image = new Image();
  image.onload = () => done(image);
  image.onerror = () => done(null);
  image.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}
/* A DRAWN PIECE (agent/playpieces.py read_svg): its picture as far as its work has come - the groups of the stages to
   come faint, scaffolding over them - its moving parts apart, each its own fine picture; its track's points. Made once
   for each stage it reaches, kept; null while it is being made. */
function playSvgPiece(item) {
  play.svgPieces = play.svgPieces || new Map();
  const spec = item.spec || {}, built = playPieceBuilt(item);
  const shown = spec.stages ? Math.max(1, Math.ceil(spec.stages * built - 1e-6)) : 99;
  const key = `${item.id}:${shown}:${spec.svg.length}`;
  if (play.svgPieces.has(key)) return play.svgPieces.get(key);
  play.svgPieces.set(key, null);
  const W = item.w * 16, H = (item.h + (PLAY_PIECE_UP_SVG)) * 16;
  let doc;
  try { doc = new DOMParser().parseFromString(spec.svg, "image/svg+xml"); } catch (_err) { return null; }
  const root = doc.documentElement;
  const moving = [];
  for (const one of Array.from(root.querySelectorAll(".spin, .blink, .smoke"))) {
    const kind = ["spin", "blink", "smoke"].find((k) => one.classList.contains(k));
    moving.push({ kind, cx: Number(one.getAttribute("data-cx")) || 0, cy: Number(one.getAttribute("data-cy")) || 0, xml: new XMLSerializer().serializeToString(one) });
    one.remove();
  }
  let unbuilt = false;
  for (const one of Array.from(root.querySelectorAll("[data-stage]"))) {
    if (Number(one.getAttribute("data-stage")) > shown) { one.setAttribute("opacity", "0.22"); unbuilt = true; }
  }
  // Its track: the points along it, in tiles of the world - sampled where the page can measure a path.
  let track = null;
  const path = root.querySelector("#track");
  if (path) {
    try {
      const holder = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      holder.setAttribute("style", "position:absolute;width:0;height:0;overflow:hidden");
      const copy = document.createElementNS("http://www.w3.org/2000/svg", "path");
      copy.setAttribute("d", path.getAttribute("d") || "");
      holder.appendChild(copy); document.body.appendChild(holder);
      const length = copy.getTotalLength();
      track = [];
      for (let k = 0; k < 240; k += 1) { const p = copy.getPointAtLength(length * k / 240); track.push({ x: item.x + p.x / 16, y: item.y - PLAY_PIECE_UP_SVG + p.y / 16 }); }
      holder.remove();
    } catch (_err) { track = null; }
  }
  const text = new XMLSerializer().serializeToString(root);
  const made = { canvas: null, moving: [], track, unbuilt };
  playSvgImage(text, (image) => {
    if (!image) return;
    made.canvas = playFinePixel(image, W * PLAY_FINE, H * PLAY_FINE);
    if (unbuilt) {                                           // what is still to come, under scaffolding
      const c = made.canvas.getContext("2d"), s = PLAY_FINE * 16;
      c.strokeStyle = "rgba(160, 122, 69, 0.85)"; c.lineWidth = PLAY_FINE;
      const top = Math.round(made.canvas.height * (1 - built) * 0.8);
      for (let x = s / 2; x < made.canvas.width; x += s / 2) { c.beginPath(); c.moveTo(x, top); c.lineTo(x, made.canvas.height - s * item.h / 2); c.stroke(); }
      for (let y = top; y < made.canvas.height - s * item.h / 2; y += s / 2) { c.beginPath(); c.moveTo(0, y); c.lineTo(made.canvas.width, y); c.stroke(); }
    }
    if (play.on) playDraw();
  });
  for (const one of moving) {
    const alone = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">${one.xml}</svg>`;
    playSvgImage(alone, (image) => { if (image) made.moving.push({ ...one, canvas: playFinePixel(image, W * PLAY_FINE, H * PLAY_FINE, { palette: made.canvas && made.canvas.palette }) }); });
  }
  play.svgPieces.set(key, made);
  if (play.svgPieces.size > 60) play.svgPieces.delete(play.svgPieces.keys().next().value);
  return made;
}
/* FINE THINGS OVER THE WORLD (the user, 2026-11-19: "用户的图片也可以放大变成一个Item"): a picture of the user's in its
   frame, on a statue, a banner or a poster, and a painting the agent drew (its "svg") - each copied into fine pixels
   where its picture shows (the same places playPictureThing and playThing give them), at the screen's own size. */
const PLAY_FINE_AREAS = { frame: [5, 6, 22, 20], statue_of: [2, 4, 12, 18], banner: [3, 5, 10, 14], poster: [3, 4, 26, 24] };
function playFineThing(item) {
  play.fineThings = play.fineThings || new Map();
  const scale = playItemScale(item), [iw, ih] = playItemSize(item);
  const madeSvg = !item.svg && play.made && ((play.made.items || {})[item.kind] || {}).svg;
  if (madeSvg) item = { ...item, svg: madeSvg };
  const area = item.svg ? [0, 0, iw * 16 / scale, ih * 16 / scale] : PLAY_FINE_AREAS[item.kind];
  if (!area) return null;
  const key = `${item.id}:${item.picture_url || ""}:${(item.svg || "").length}:${scale}`;
  if (play.fineThings.has(key)) return play.fineThings.get(key);
  const made = { area, scale, canvas: null };
  const finish = (image) => {
    if (!image) return;
    made.canvas = playFinePixel(image, area[2] * scale * PLAY_FINE, area[3] * scale * PLAY_FINE,
                                { fit: !item.svg, stone: item.kind === "statue_of", outline: Boolean(item.svg) });
    if (play.on) playDraw();
  };
  if (item.svg) { play.fineThings.set(key, made); playSvgImage(item.svg, finish); return made; }
  const picture = item.picture_url && play.pictures.get(item.picture_url);
  if (!(picture && picture.complete && picture.naturalWidth)) return null;      // its picture not here yet
  play.fineThings.set(key, made);
  finish(picture);
  if (play.fineThings.size > 120) play.fineThings.delete(play.fineThings.keys().next().value);
  return made;
}
function playFineThingsDraw(size) {
  const { ctx, canvas } = play;
  const u = size / 16;
  for (const item of (play.world && play.world.items) || []) {
    const madeThing = play.made && (play.made.items || {})[item.kind];
    if (!(item.svg || madeThing)) continue;        // their pictures: in the card only, never on the map
    const sx = item.x * size - play.camera.x, sy = (item.y - (item.hang ? 0.5 : item.on ? 0.31 : 0)) * size - play.camera.y;
    const [iw, ih] = playItemSize(item);
    if (sx > canvas.width || sy > canvas.height || sx + iw * size < 0 || sy + ih * size < 0) continue;
    const made = playFineThing(item);
    if (!made || !made.canvas) continue;
    const [ax, ay, aw, ah] = made.area, k = made.scale;
    ctx.save();
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(made.canvas, Math.round(sx + ax * u * k), Math.round(sy + ay * u * k), aw * u * k, ah * u * k);
    ctx.restore();
  }
}
/* A DRAWN PIECE OVER THE WORLD: its fine picture at the screen's size, its moving parts turning, blinking, smoking; a
   ride's cars round its track, the walker in the first when it is ridden. */
function playSvgPieceDraw(item, size, now) {
  const made = playSvgPiece(item);
  if (!made || !made.canvas) return;
  const { ctx } = play;
  const sx = item.x * size - play.camera.x, sy = (item.y - PLAY_PIECE_UP_SVG) * size - play.camera.y;
  const w = item.w * size, h = (item.h + PLAY_PIECE_UP_SVG) * size, k = size / (16 * PLAY_FINE);
  ctx.save();
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(made.canvas, Math.round(sx), Math.round(sy), w, h);
  for (const one of made.moving) {
    ctx.save();
    if (one.kind === "spin") {
      const cx = sx + one.cx / 16 * size, cy = sy + one.cy / 16 * size;
      ctx.translate(cx, cy); ctx.rotate(now / 700); ctx.translate(-cx, -cy);
    } else if (one.kind === "blink") ctx.globalAlpha = Math.sin(now / 300) > 0 ? 1 : 0.25;
    ctx.drawImage(one.canvas, Math.round(sx), Math.round(sy), w, h);
    ctx.restore();
    if (one.kind === "smoke") {
      for (let s = 0; s < 4; s += 1) {
        const u = ((now / 1600) + s / 4) % 1, x = sx + one.cx / 16 * size, y = sy + one.cy / 16 * size;
        ctx.fillStyle = `rgba(225, 225, 225, ${(0.6 * (1 - u)).toFixed(2)})`;
        ctx.beginPath(); ctx.arc(x + Math.sin(u * 6) * size * 0.2, y - u * size * 1.6, size * (0.15 + u * 0.3), 0, Math.PI * 2); ctx.fill();
      }
    }
  }
  if (made.track && playPieceBuilt(item) >= 1) {
    const path = made.track, n = path.length, riding = playRidingPiece(item);
    const lap = riding ? Math.min(0.999, (now - play.ride.at) / 1000 / PLAY_PIECE_LAP_S) : (now / 1000 / PLAY_PIECE_LAP_S) % 1;
    const lead = Math.floor(lap * n) % n;
    for (let j = 0; j < 4; j += 1) {
      const p = path[(lead - j * 5 + n * 4) % n], q = path[(lead - j * 5 + 1 + n * 4) % n];
      const px = p.x * size - play.camera.x, py = p.y * size - play.camera.y;
      ctx.save(); ctx.translate(px, py); ctx.rotate(Math.atan2(q.y - p.y, q.x - p.x));
      ctx.fillStyle = "#d9382f"; ctx.fillRect(-size * 0.32, -size * 0.28, size * 0.64, size * 0.36);
      ctx.fillStyle = "#222"; ctx.fillRect(-size * 0.28, size * 0.06, size * 0.56, size * 0.07);
      ctx.restore();
      if (riding && j === 0 && typeof playActMeOn === "function") playActMeOn(px, py - size * 0.2, size);
    }
    if (riding && now - play.ride.at > PLAY_PIECE_LAP_S * 1000) {
      play.ride = null;
      const gate = (item.spec.entrance || [Math.floor((item.w - 1) / 2)])[0];
      const spot = playActSpotNear(item.x + gate, item.y + item.h);
      if (spot) { play.me.x = spot[0] + 0.5; play.me.y = spot[1] + 0.6; }
      playWhere();
    }
  }
  ctx.restore();
  void k;
}
function playPieceMoves(item) {
  if (item.spec && item.spec.svg) return Boolean(item.spec.moving) || (item.spec.track && playPieceBuilt(item) >= 1);
  return (item.kit === "track" && playPieceBuilt(item) >= 1)
    || (item.kit === "composite" && ((item.spec || {}).shapes || []).some((one) => one.moves && one.moves !== "none"))
    || (item.kit === "water" && (item.spec || {}).boats);
}
function playPiecesDraw(size) {
  const { ctx } = play;
  const k = size / PLAY_TILE, now = performance.now();
  for (const item of (play.world && play.world.items) || []) {
    if (item.kind !== "piece" || !playPieceInView(item)) continue;
    if (!item.spec || !(item.w > 0 && item.h > 0)) {
      // NOT DESIGNED YET (agent/playworld.py _finish_pieces designs it as the world grows): its scaffold on its tile.
      const x = item.x * size - play.camera.x, y = item.y * size - play.camera.y;
      ctx.strokeStyle = "#a07a45"; ctx.lineWidth = Math.max(1, k);
      ctx.strokeRect(Math.round(x + k), Math.round(y - size * 0.5), size - 2 * k, size * 1.5 - k);
      ctx.beginPath(); ctx.moveTo(x + k, y - size * 0.5); ctx.lineTo(x + size - k, y + size - k);
      ctx.moveTo(x + size - k, y - size * 0.5); ctx.lineTo(x + k, y + size - k); ctx.stroke();
      continue;
    }
    if (item.spec && item.spec.svg) { playSvgPieceDraw(item, size, now); continue; }   // drawn first, built after its picture
    const made = playPiecePicture(item);
    const sx = made.x0 * size - play.camera.x, sy = made.y0 * size - play.camera.y;
    ctx.drawImage(made.canvas, Math.round(sx), Math.round(sy), made.canvas.width * k, made.canvas.height * k);
    const at = (x, y, up = 0) => [(x - made.x0) * size + sx, (y - up - made.y0) * size + sy];
    if (item.kit === "track" && made.path && playPieceBuilt(item) >= 1) {
      // its cars, round and round - slow up high, fast where it is low; the walker in the first when riding it
      const path = made.path, n = path.length, cars = Math.max(1, Math.min(6, (item.spec || {}).cars || 3));
      const riding = playRidingPiece(item);
      const lap = riding ? Math.min(0.999, (now - play.ride.at) / 1000 / PLAY_PIECE_LAP_S) : (now / 1000 / PLAY_PIECE_LAP_S) % 1;
      const lead = Math.floor(lap * n) % n;
      for (let j = 0; j < cars; j += 1) {
        const i = (lead - j * 3 + n * 4) % n, p = path[i], q = path[(i + 1) % n];
        const [px, py] = at(p.x, p.y, p.h), [qx, qy] = at(q.x, q.y, q.h);
        ctx.save(); ctx.translate(px, py); ctx.rotate(Math.atan2(qy - py, qx - px));
        ctx.fillStyle = (item.spec || {}).colour || "#d9382f"; ctx.fillRect(-size * 0.35, -size * 0.3, size * 0.7, size * 0.4);
        ctx.fillStyle = "#222"; ctx.fillRect(-size * 0.3, size * 0.08, size * 0.6, size * 0.08);
        ctx.restore();
        if (riding && j === 0) {
          play.me.x = p.x; play.me.y = p.y;
          if (typeof playActMeOn === "function") playActMeOn(px, py - size * 0.2, size);
        }
      }
      if (riding && now - play.ride.at > PLAY_PIECE_LAP_S * 1000) {      // round once: off at its station
        play.ride = null;
        const st = path[0], spot = playActSpotNear(Math.round(st.x), Math.round(item.y + item.h));
        if (spot) { play.me.x = spot[0] + 0.5; play.me.y = spot[1] + 0.6; }
        playWhere();
      }
    }
    if (item.kit === "composite") {
      for (const one of ((item.spec || {}).shapes || []).filter((s) => s.moves && s.moves !== "none")) {
        const [x, y] = at(item.x + one.x, item.y + one.y + 1, one.up);
        ctx.save();
        if (one.moves === "spin") {
          ctx.translate(x + one.w * size / 2, y - one.h * size / 2); ctx.rotate(now / 600);
          ctx.scale(k, k); playPieceShape(ctx, { ...one }, -one.w * PLAY_TILE / 2, one.h * PLAY_TILE / 2, true);
        } else {
          ctx.translate(x, y); ctx.scale(k, k);
          if (one.moves === "blink") ctx.globalAlpha = 0.4 + 0.6 * (Math.sin(now / 300) > 0 ? 1 : 0);
          playPieceShape(ctx, one, 0, 0, true);
          if (one.moves === "smoke") for (let s = 0; s < 4; s += 1) {
            const u = ((now / 1600) + s / 4) % 1;
            ctx.fillStyle = `rgba(220, 220, 220, ${(0.6 * (1 - u)).toFixed(2)})`;
            ctx.beginPath(); ctx.arc(one.w * PLAY_TILE / 2 + Math.sin(u * 6) * 3, -one.h * PLAY_TILE - u * 24, 3 + u * 5, 0, Math.PI * 2); ctx.fill();
          }
        }
        ctx.restore();
      }
    }
    if (item.kit === "water") {
      for (let b = 0; b < ((item.spec || {}).boats || 0); b += 1) {
        const u = ((now / 9000) + b / ((item.spec || {}).boats || 1)) % 1;
        const [x, y] = at(item.x + item.w * (0.2 + 0.6 * u), item.y + item.h * (0.3 + 0.4 * ((b % 2))));
        ctx.fillStyle = "#f7f4ec"; ctx.beginPath(); ctx.moveTo(x, y - size * 0.6); ctx.lineTo(x + size * 0.4, y); ctx.lineTo(x, y); ctx.fill();
        ctx.fillStyle = "#8b5a2b"; ctx.fillRect(x - size * 0.35, y, size * 0.8, size * 0.18);
      }
    }
  }
}
/* PRESSED E BY ONE: a track ridden round once from its station, a stack climbed - the view from its top a moment. */
function playPieceAct(item) {
  // An SVG piece is ridden only along a track its picture has (nothing else carries the walker round).
  const rides = item.spec && item.spec.svg ? item.interact === "ride" && Boolean((playSvgPiece(item) || {}).track) : item.kit === "track";
  const climbs = item.spec && item.spec.svg ? item.interact === "climb" : item.kit === "stack";
  if (rides && playPieceBuilt(item) >= 1) {
    play.ride = { piece: item, at: performance.now() };
    playSfx("door-close", 0.3);
    playRun();
    return true;
  }
  if (climbs && !play.climbing) {
    play.climbing = true;
    playZoom(-2);
    play.banner = { name: playWords("From the top of") + " " + (item.name || ""), since: "", at: performance.now(), words: null };
    setTimeout(() => { play.climbing = false; playZoom(2); }, 5000);
    return true;
  }
  return false;
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
  // NEVER LEFT THINKING IT RUNS (the user, 2026-11-18: "刚打开的时候没有焦点（可能是还没load完), 不能运动"): a key
  // pressed while the world was still loading threw in the step, no next frame came, and playRunning stayed true -
  // no key could start it again. A frame that throws now stops the run, and the next key starts it afresh.
  const tick = (now) => {
    if (!play.on) { playRunning = false; return; }
    try {
      const seconds = (now - play.last) / 1000;
      play.last = now;
      const moved = playStep(seconds);
      const busy = play.banner || playJumpHeight() > 0 || (play.said && performance.now() < play.said.until) || play.dust.length
        || play.leaves.length || (play.touched && performance.now() - play.touched.touchedAt < PLAY_SWAY_MS);
      const drawing = performance.now();
      if (moved || busy) playDraw();
      const took = performance.now() - drawing;
      if (took > PLAY_SLOW_FRAME_MS) playNote(`a slow frame: ${Math.round(took)}ms`);
      if (moved || busy || play.keys.size) requestAnimationFrame(tick);
      else { playDraw(); playRunning = false; }
    } catch (err) {
      playRunning = false;
      console.error("play frame", err);
      playNote(`a frame failed: ${String((err && err.message) || err).slice(0, 160)}`);
    }
  };
  requestAnimationFrame(tick);
}

const PLAY_KEYS = { ArrowLeft: "left", a: "left", A: "left", ArrowRight: "right", d: "right", D: "right",
                    ArrowUp: "up", w: "up", W: "up", ArrowDown: "down", s: "down", S: "down" };
window.addEventListener("keydown", (event) => {
  if (!play.on || event.altKey || event.metaKey) return;
  play.running = event.shiftKey;
  const typing = document.activeElement && /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
  if (!event.repeat && !playFirst.sent) {
    const was = [play.me.x, play.me.y];
    playNote(`key ${event.key}; keys at ${playNoteKeys()}; world ${play.map ? "laid" : "not yet"}; typing=${Boolean(typing)}`);
    setTimeout(() => playNote(`  after ${event.key}: moved ${Math.hypot(play.me.x - was[0], play.me.y - was[1]).toFixed(2)}, running=${playRunning}, sit=${Boolean(play.sit)}, ride=${Boolean(play.ride)}, menu=${Boolean(play.menu)}`), 400);
  }
  if (!typing && ["+", "=", "-", "_", "0"].includes(event.key)) {
    event.preventDefault();
    event.stopPropagation();                   // the world zoomed, not the panel
    playZoom(event.key === "0" ? 0 : ["+", "="].includes(event.key) ? 1 : -1);
    return;
  }
  if (event.ctrlKey) return;
  if (!typing && event.key === " ") {
    event.preventDefault();
    // ONE JUMP A PRESS (the user, 2026-11-19: "按住空格会一直跳跃"): held, it jumps once - the key's own repeat is not
    // always said so where the panel is hosted; let go, and it may jump again.
    if (!event.repeat && !play.spaceHeld) playJump();
    play.spaceHeld = true;
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
  play.lookAt = null;                          // a step: the view back on the walker (playMiniLook)
  play.keys.add(way);
  playRun();
});
window.addEventListener("keyup", (event) => {
  if (event.key === " ") play.spaceHeld = false;
  play.running = event.shiftKey;
  const way = PLAY_KEYS[event.key];
  if (way) play.keys.delete(way);
});
window.addEventListener("blur", () => { play.keys.clear(); play.running = false; play.spaceHeld = false; });
window.addEventListener("pagehide", () => { if (play.on) playKeepWhere(); });
document.addEventListener("visibilitychange", () => { if (document.hidden) play.keys.clear(); });
window.addEventListener("resize", () => { if (play.on) { playPlace(); playFit(); } });
