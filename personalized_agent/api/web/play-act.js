/* THE PLAY VIEW'S THINGS TO DO AND WAYS TO GO (2026-11-17, the user):
   - PORTALS (如果地图很大，移动会比较慢，允许有传送门（类似于冒险岛的光圈，可以用几种不同的传送门），进行快速传送): one by each land's
     heart, at the start of their story and at its newest place - five looks, by the land; E in one, a list of where to go.
     A land apart (an island, a lone cave) is reached by them alone ("孤岛，或者孤独山洞，反正只能用传送门到").
   - LINES TO RIDE (里面也要有道路和交通工具，比如可以搭乘飞机，天空之城的船，摆渡，还有rotate的火车等): a train round the
     mainland's lands, a ferry between lands by the water, an airship up to a city in the sky, a plane to the farthest -
     each running, a stop at each land; E at a stop, a list of where its line goes; ridden, the camera with it.
   - THINGS TO DO, ALL MOVING (场景要能更多交互，比如椅子可以坐下，可以手动开关灯/火把，搭乘交通工具等，都要有动画; 水里可以钓鱼，
     游泳之类): sitting on a chair, a bench, a sofa; a lamp, a torch, a candle put out and lit again; fishing where the
     water is; swimming in it.
   - LIFE ABOUT (加一些飞鸟，小动物之类的动态元素): birds flying over, small animals about that run from the walker.
   Drawn each frame over the world (play.js playDraw calls playActDraw); a few frames a second while any of it moves. */

/* -- where one can stand ---------------------------------------------------------------------------------------- */
function playActFree(x, y) {
  try { return !playBlocked(x + 0.5, y + 0.9) && !playActWater(x, y); } catch (_err) { return false; }
}
function playActSpotNear(x, y, most = 6) {
  for (let r = 0; r <= most; r += 1) {
    for (let dy = -r; dy <= r; dy += 1) {
      for (let dx = -r; dx <= r; dx += 1) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) !== r) continue;
        if (playActFree(x + dx, y + dy)) return [x + dx, y + dy];
      }
    }
  }
  return null;
}
function playActWater(x, y) {
  const [block, i] = playCell(play.map, Math.floor(x), Math.floor(y));
  const piece = String(block.ground[i] || "");
  return !block.none[i] && piece.startsWith("water") && !piece.startsWith("waterFall");
}
function playActInView(x, y, pad = 3) {
  const size = PLAY_TILE * play.scale;
  const sx = x * size - play.camera.x, sy = y * size - play.camera.y;
  return sx > -pad * size && sy > -pad * size && sx < play.canvas.width + pad * size && sy < play.canvas.height + pad * size;
}
/* What is in sight, in tiles - left, top, right, bottom - and `pad` tiles round it. */
function playActView(pad = 3) {
  const size = PLAY_TILE * play.scale;
  return [play.camera.x / size - pad, play.camera.y / size - pad,
          (play.camera.x + play.canvas.width) / size + pad, (play.camera.y + play.canvas.height) / size + pad];
}
function playActScreen(x, y) {
  const size = PLAY_TILE * play.scale;
  return [x * size - play.camera.x, y * size - play.camera.y];
}

/* -- PORTALS -------------------------------------------------------------------------------------------------------- */
const PLAY_PORTAL_LOOKS = { scifi: "pad", space: "pad", city: "pad", underwater: "pad", enchanted: "ring", skycity: "ring",
                            valentine: "ring", toytown: "ring", newyear: "ring", ruins: "arch", graveyard: "arch",
                            halloween: "arch", cavern: "arch", highland: "arch", desert: "arch", forest: "fairy",
                            primeval: "fairy", meadow: "fairy", botanical: "fairy", lakeside: "fairy", autumn: "fairy" };
const PLAY_PORTAL_FAR = 40;                    // the world this many tiles across, or a land apart: portals
function playPortals() {
  const map = play.map;
  if (!map) return [];
  if (map.portals) return map.portals;
  const spots = [];
  const add = (x, y, name, region) => {
    const at = playActSpotNear(Math.round(x), Math.round(y));
    if (!at || spots.some((one) => Math.hypot(one.x - at[0], one.y - at[1]) < 10)) return;
    spots.push({ x: at[0], y: at[1], name, region, look: PLAY_PORTAL_LOOKS[region && region.biome] || "light" });
  };
  map.regions.forEach((region, n) => {
    const lived = map.zones.some((one) => one.region === region) || map.landmarks.some((one) => one.region === region);
    if (lived) add(map.hearts[n][0] + 3, map.hearts[n][1], playWords(region.name || "") || region.biome, region);
  });
  const order = (map.life && map.life.order) || [];
  if (order.length) {
    const first = order[0], last = order[order.length - 1];
    add(first.out[0] + 2, first.out[1], playWords("Start of your story"), first.region);
    if (last !== first) add(last.out[0] + 2, last.out[1], last.zone.name || last.zone.kind, last.region);
  }
  // GATES AS DESIGNED (agent/playmuse.py read_transport): one at each end, in the look it was given.
  // NO PORTALS (the user, 2026-11-19: "传送门取消，必须是飞机轮船，宇宙飞船，火车，地铁，出租车，公交车，直升机之类"): every way a
  // vehicle (playLines); only another area, a world drawn apart, is flown to - from its airport.
  spots.length = 0;
  const design = play.world && play.world.transport;
  if (false && design && Array.isArray(design.portals)) {
    spots.length = 0;
    for (const one of design.portals) {
      for (const id of [one.from, one.to]) {
        const n = map.regions.findIndex((region) => region.id === id);
        if (n < 0) continue;
        const region = map.regions[n], before = spots.length;
        add(map.hearts[n][0] + 3, map.hearts[n][1], playWords(region.name || "") || region.biome, region);
        if (spots.length > before) spots[spots.length - 1].look = one.look || spots[spots.length - 1].look;
      }
    }
    map.portals = spots.length >= 2 ? spots : [];
  }
  const xs = map.hearts.map((one) => one[0]), ys = map.hearts.map((one) => one[1]);
  const far = Math.max(...xs) - Math.min(...xs) > PLAY_PORTAL_FAR || Math.max(...ys) - Math.min(...ys) > PLAY_PORTAL_FAR;
  const apart = map.regions.some((one) => one.apart);
  map.portals = [];
  void far; void apart;
  // AN AREA'S GATE (the user: "一个区域必须传送到下一个区域"): by its first land's heart, to every other area.
  const areas = playAreas(play.world);
  if (areas.length > 1 && map.hearts.length) {
    const at = playActSpotNear(map.hearts[0][0] - 3, map.hearts[0][1] + 1);
    if (at) map.portals.push({ x: at[0], y: at[1], name: playWords("Airport to other areas"), gate: true, look: "airport" });
  }
  return map.portals;
}
/* THE WAYS TO THE OTHER MAPS, AS DESIGNED (agent/playworld.py _design_crossings; the user, 2026-11-20: "让模型设计一个美观的，
   酷炫的，意想不到的到其它地图的入口（在另一个地图也有对应的入口）"): each end standing in this map, by its land's heart on
   ground clear for all of it - drawn as a great piece is (play.js playSvgPieceDraw), gone through with E. Found once a map. */
function playCrossings() {
  const map = play.map;
  if (!map) return [];
  if (map.crossings) return map.crossings;
  map.crossings = [];
  for (const crossing of (play.world && play.world.crossings) || []) {
    (crossing.ends || []).forEach((end, i) => {
      if (end.area !== play.area || !end.spec || !end.spec.svg) return;
      const n = map.regions.findIndex((region) => region.id === end.region);
      const heart = map.hearts[Math.max(0, n)];
      if (!heart) return;
      const at = playCrossingSpot(heart, end.w, end.h, map.crossings);
      if (!at) return;
      map.crossings.push({ id: `cross-${crossing.id}-${i}`, kind: "piece", crossing, end: i, x: at[0], y: at[1],
                           w: end.w, h: end.h, spec: end.spec, name: end.name });
    });
  }
  return map.crossings;
}
/* Ground clear for an end of `w` by `h` tiles and the tile before its middle, nearest the heart (south and east first). */
function playCrossingSpot(heart, w, h, taken) {
  const free = (x, y) => { try { return playActFree(x, y); } catch (_err) { return false; } };
  const clear = (x0, y0) => {
    if (taken.some((one) => x0 < one.x + one.w + 2 && x0 + w + 2 > one.x && y0 < one.y + one.h + 2 && y0 + h + 2 > one.y)) return false;
    for (let y = y0; y < y0 + h; y += 1) for (let x = x0; x < x0 + w; x += 1) if (!free(x, y)) return false;
    return free(x0 + Math.floor(w / 2), y0 + h);
  };
  const cx = Math.round(heart[0]) + 4, cy = Math.round(heart[1]) + 3;
  for (let r = 0; r <= 24; r += 1) {
    for (let dy = -r; dy <= r; dy += 1) {
      for (let dx = -r; dx <= r; dx += 1) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) !== r) continue;
        if (clear(cx + dx, cy + dy)) return [cx + dx, cy + dy];
      }
    }
  }
  return null;
}
/* Through an end: to the other map, out in front of its other end - what one sees going through said there. */
function playCrossingGo(one) {
  const other = (one.crossing.ends || [])[1 - one.end];
  if (!other) return;
  playTravelArea(other.area, { crossing: one.crossing.id, end: 1 - one.end });
  const end = one.crossing.ends[one.end];
  playActSay([other.name || one.crossing.name, end.going].filter(Boolean).join(" - "));
}
function playPortalDraw(one, size, now) {
  const { ctx } = play;
  const [sx, sy] = playActScreen(one.x + 0.5, one.y + 0.9);
  const t = now / 1000;
  ctx.save();
  if (one.look === "airport") {
    // AN AIRPORT, NOT A GATE: its pad, a plane waiting on it.
    for (const [name, w, h, dy] of [["helipad", 2, 2, 0], ["airplane", 2.2, 2.2, -0.4]]) {
      const spec = PLAY_SCENERY[name], sheet = spec && play.sheets[spec[0]];
      if (sheet) ctx.drawImage(sheet, spec[1] * 17, spec[2] * 17, spec[3] * 17 - 1, spec[4] * 17 - 1,
                               sx - size * w / 2, sy - size * (h - 0.2) + dy * size, size * w, size * h);
    }
  } else if (one.look === "light") {
    // A column of light, as on a maple island: an oval glow standing up, sparks rising through it.
    const g = ctx.createRadialGradient(sx, sy - size * 0.9, size * 0.1, sx, sy - size * 0.9, size * 1.1);
    g.addColorStop(0, "rgba(255, 255, 220, 0.95)"); g.addColorStop(0.45, "rgba(140, 220, 255, 0.55)"); g.addColorStop(1, "rgba(80, 160, 255, 0)");
    ctx.fillStyle = g;
    ctx.beginPath(); ctx.ellipse(sx, sy - size * 0.9, size * 0.55, size * 1.1, 0, 0, Math.PI * 2); ctx.fill();
    for (let k = 0; k < 7; k += 1) {
      const u = (t * 0.6 + k / 7) % 1;
      ctx.fillStyle = `rgba(255, 255, 255, ${(1 - u).toFixed(2)})`;
      ctx.fillRect(sx + Math.sin(k * 2.1 + t * 3) * size * 0.3, sy - u * size * 1.9, Math.max(1, size / 10), Math.max(1, size / 10));
    }
  } else if (one.look === "ring") {
    // A magic circle on the ground, turning, its runes glowing purple.
    ctx.translate(sx, sy - size * 0.2);
    ctx.scale(1, 0.45);
    ctx.rotate(t * 0.8);
    ctx.strokeStyle = "rgba(190, 120, 255, 0.95)"; ctx.lineWidth = Math.max(1, size / 12);
    ctx.beginPath(); ctx.arc(0, 0, size * 0.9, 0, Math.PI * 2); ctx.stroke();
    ctx.beginPath(); ctx.arc(0, 0, size * 0.65, 0, Math.PI * 2); ctx.stroke();
    for (let k = 0; k < 6; k += 1) {
      const a = k / 6 * Math.PI * 2;
      ctx.beginPath(); ctx.moveTo(Math.cos(a) * size * 0.65, Math.sin(a) * size * 0.65);
      ctx.lineTo(Math.cos(a + Math.PI * 2 / 3) * size * 0.65, Math.sin(a + Math.PI * 2 / 3) * size * 0.65); ctx.stroke();
    }
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    const g = ctx.createRadialGradient(sx, sy - size * 0.6, 0, sx, sy - size * 0.6, size);
    g.addColorStop(0, "rgba(220, 170, 255, 0.45)"); g.addColorStop(1, "rgba(160, 90, 255, 0)");
    ctx.fillStyle = g; ctx.fillRect(sx - size, sy - size * 1.6, size * 2, size * 2);
  } else if (one.look === "pad") {
    // A teleport pad: a steel disc, a beam flickering up from it.
    ctx.fillStyle = "#5d6675"; ctx.beginPath(); ctx.ellipse(sx, sy - size * 0.15, size * 0.7, size * 0.3, 0, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "#9fe8ff"; ctx.beginPath(); ctx.ellipse(sx, sy - size * 0.18, size * 0.45, size * 0.18, 0, 0, Math.PI * 2); ctx.fill();
    const flicker = 0.35 + 0.25 * Math.sin(t * 9) + 0.1 * Math.sin(t * 23);
    const g = ctx.createLinearGradient(0, sy - size * 2.2, 0, sy);
    g.addColorStop(0, "rgba(120, 230, 255, 0)"); g.addColorStop(1, `rgba(120, 230, 255, ${flicker.toFixed(2)})`);
    ctx.fillStyle = g; ctx.fillRect(sx - size * 0.45, sy - size * 2.2, size * 0.9, size * 2.05);
  } else if (one.look === "arch") {
    // A stone arch, a swirl of blue in it.
    ctx.fillStyle = "#8a8578";
    ctx.fillRect(sx - size * 0.75, sy - size * 1.7, size * 0.25, size * 1.7);
    ctx.fillRect(sx + size * 0.5, sy - size * 1.7, size * 0.25, size * 1.7);
    ctx.beginPath(); ctx.ellipse(sx, sy - size * 1.7, size * 0.75, size * 0.45, 0, Math.PI, 0); ctx.lineWidth = size * 0.25;
    ctx.strokeStyle = "#8a8578"; ctx.stroke();
    ctx.save(); ctx.beginPath(); ctx.ellipse(sx, sy - size * 0.95, size * 0.5, size * 0.95, 0, 0, Math.PI * 2); ctx.clip();
    for (let k = 0; k < 4; k += 1) {
      ctx.strokeStyle = `rgba(${100 + k * 30}, ${170 + k * 20}, 255, 0.8)`; ctx.lineWidth = Math.max(1, size / 10);
      ctx.beginPath(); ctx.ellipse(sx, sy - size * 0.95, size * (0.15 + k * 0.1), size * (0.3 + k * 0.18), t * (1.5 + k * 0.4), 0, Math.PI * 1.4); ctx.stroke();
    }
    ctx.restore();
  } else {
    // A fairy ring: small mushrooms in a circle, sparkles drifting up from it.
    for (let k = 0; k < 9; k += 1) {
      const a = k / 9 * Math.PI * 2;
      const mx = sx + Math.cos(a) * size * 0.75, my = sy - size * 0.25 + Math.sin(a) * size * 0.32;
      ctx.fillStyle = "#f2e6d0"; ctx.fillRect(mx - size * 0.04, my - size * 0.08, size * 0.08, size * 0.12);
      ctx.fillStyle = k % 2 ? "#d9443f" : "#e9a23b"; ctx.beginPath(); ctx.ellipse(mx, my - size * 0.1, size * 0.12, size * 0.07, 0, Math.PI, 0); ctx.fill();
    }
    for (let k = 0; k < 6; k += 1) {
      const u = (t * 0.5 + k / 6) % 1;
      ctx.fillStyle = `rgba(255, 245, 160, ${(1 - u).toFixed(2)})`;
      ctx.fillRect(sx + Math.cos(k * 1.7) * size * 0.5, sy - size * 0.3 - u * size * 1.4, Math.max(1, size / 10), Math.max(1, size / 10));
    }
  }
  ctx.restore();
}

/* -- LINES TO RIDE ---------------------------------------------------------------------------------------------------- */
const PLAY_WATERY = new Set(["seaside", "lakeside", "underwater"]);
const PLAY_LINE_LOOK = { train: "trainStation", ferry: "pier", airship: "skyDock", plane: "helipad", road: "signpost" };
const PLAY_LINE_SPEED = { train: 9, ferry: 5, airship: 6, plane: 22, road: 7 };      // tiles a second, ridden
const PLAY_LINE_WORDS = { train: "Train", ferry: "Ferry", airship: "Airship", plane: "Plane", road: "Bus" };
/* EACH LAND ITS OWN VEHICLES (agent/playmuse.py VEHICLES): what each is laid as (a train on its rails, a boat on the
   water, a ship in the sky, a plane above), and drawn as - its engine and its cars, or its body. */
const PLAY_VEHICLES = {
  train: { kind: "train", runs: "rails", engine: "trainEngine", car: "trainCarriage", words: "Train" },
  tram: { kind: "train", runs: "rails", engine: "trainEngine", car: "trainCarriage", words: "Tram" },
  sleigh: { kind: "train", runs: "rails", engine: "reindeer", car: "sleigh", words: "Sleigh" },
  caravan: { kind: "train", runs: "rails", engine: "camel", car: "camel", words: "Caravan" },
  coaster: { kind: "train", runs: "rails", engine: "coasterCar", car: "coasterCar", words: "Coaster" },
  mine_cart: { kind: "train", runs: "rails", engine: "coasterCar", car: "crate", words: "Mine cart" },
  ferry: { kind: "ferry", runs: "water", body: "ferry", words: "Ferry" },
  sailboat: { kind: "ferry", runs: "water", body: "ship", words: "Sailboat" },
  airship: { kind: "airship", runs: "sky", words: "Airship" },
  plane: { kind: "plane", runs: "air", words: "Plane" },
  metro: { kind: "train", runs: "rails", engine: "trainCarriage", car: "trainCarriage", words: "Metro", tint: "hue-rotate(170deg) saturate(1.3)" },
  bus: { kind: "road", runs: "road", body: "bus", words: "Bus" },
  taxi: { kind: "road", runs: "road", body: "taxi", words: "Taxi" },
  helicopter: { kind: "plane", runs: "air", body: "helicopter", words: "Helicopter" },
  spaceship: { kind: "plane", runs: "air", body: "spaceship", words: "Spaceship" },
  // Under the sea, in the sky, in space (the user, 2026-11-20: "交通工具").
  submarine: { kind: "ferry", runs: "water", body: "submarine", words: "Submarine" },
  balloon: { kind: "airship", runs: "sky", body: "balloon", words: "Hot-air balloon" },
  rocket: { kind: "plane", runs: "air", body: "spaceship", words: "Rocket" },
  ufo: { kind: "plane", runs: "air", body: "ufo", words: "Flying saucer" },
};
/* A line's own name for itself: its vehicle's words, or its kind's. */
function playLineWords(line) {
  return (PLAY_VEHICLES[line.look] || {}).words || PLAY_LINE_WORDS[line.kind];
}
/* WHICH LINE TAKES ONE FROM A LAND TO ANOTHER, AND WHERE TO GET OFF (the user, 2026-11-20: "指向车站/飞机/轮船要说明去哪
   下车"): the first line that stops in both - {line, on, off} - or null. */
function playRideBetween(fromRegion, toRegion) {
  if (!fromRegion || !toRegion || fromRegion.id === toRegion.id) return null;
  for (const line of playLines()) {
    const on = line.stops.find((s) => s.region && s.region.id === fromRegion.id);
    const off = line.stops.find((s) => s.region && s.region.id === toRegion.id);
    if (on && off) return { line, on, off };
  }
  return null;
}
function playLines() {
  const map = play.map;
  if (!map) return [];
  if (map.lines) return map.lines;
  const lines = [];
  const lands = map.regions.map((region, n) => ({ region, n, heart: map.hearts[n] }))
    .filter((one) => !one.region.apart && (map.zones.some((z) => z.region === one.region) || map.landmarks.some((l) => l.region === one.region)));
  const stop = (land, kind) => {
    const off = { train: [-5, 1], ferry: [0, 4], airship: [5, -2], plane: [-6, -4], road: [4, 3] }[kind] || [0, 3];
    const at = playActSpotNear(land.heart[0] + off[0], land.heart[1] + off[1]);
    return at && { x: at[0], y: at[1], name: playWords(land.region.name || "") || land.region.biome, region: land.region };
  };
  // THE WAYS AS DESIGNED (agent/playmuse.py read_transport; the user, 2026-11-19: "交通工具有一层专门的设计 ... 交通
  // 工具要符合场景"): each line its vehicle, its stops by what it runs on - a station, a landing on the shore, a dock in
  // the sky - a ring where it was made one. Without a design, the lines as before.
  const design = play.world.transport;
  const designed = Boolean(design && Array.isArray(design.lines));
  // AT ITS STATION (agent/playworld.py _stations; the user, 2026-11-20: "大的火车站，地铁站，飞机场之类"): a line stops at
  // its kind's station in the land - at its door - where there is one; a boat at the shore, an airship at the sky's edge.
  const terminal = (land, runs) => {
    const z = map.zones.find((one) => one.zone.terminal_of === `${land.region.id}:${runs}`);
    return z && { x: z.out[0], y: z.out[1] - 1, name: z.zone.name || playWords(land.region.name || ""), region: land.region,
                  station: z.zone };
  };
  for (const one of designed ? design.lines : []) {
    const look = PLAY_VEHICLES[one.vehicle] || PLAY_VEHICLES.train;
    const runs = one.vehicle === "metro" ? "metro" : look.runs;
    const mine = (one.stops || []).map((id) => lands.find((l) => l.region.id === id)).filter(Boolean);
    const stops = mine.map((l) => (look.runs === "water" ? playShoreNear(l.heart[0], l.heart[1], l.region)
      : look.runs === "sky" ? playSkyNear(l.heart[0], l.heart[1], l.region, 1)
      : terminal(l, runs) || stop(l, look.kind))).filter(Boolean);
    if (stops.length >= 2) lines.push({ kind: look.kind, look: one.vehicle, name: one.name || "", stops, loop: Boolean(one.ring) && stops.length >= 3 });
  }
  // A PROJECT'S FAR PLACES (agent/playmuse.py local_lines; the user, 2026-11-19: "project内相隔超过一段距离一定有一个交通
  // 工具"): its line through them, a stop by each one's door.
  for (const one of (design && design.local) || []) {
    const look = PLAY_VEHICLES[one.vehicle] || PLAY_VEHICLES.bus;
    // AFLOAT, ITS STOPS WHERE IT FLOATS (2026-11-21: a project's hot-air balloon - "airship", in the sky - had stops on
    // the ground with no sky tile kept, its way looked for from nowhere, and the page held on until it ran out of
    // memory): a boat's at the shore, a balloon's at the sky's edge, as the designed lines' - none, no line.
    const stops = (one.places || []).map((id) => map.zones.find((z) => z.zone.id === id)).filter(Boolean).map((z) => {
      if (look.runs === "water") return playShoreNear(z.out[0], z.out[1], z.region);
      if (look.runs === "sky") return playSkyNear(z.out[0], z.out[1], z.region, 1);
      const at = playActSpotNear(z.out[0] + 2, z.out[1] + 1);
      return at && { x: at[0], y: at[1], name: z.zone.name || z.zone.kind, region: z.region };
    }).filter(Boolean);
    if (stops.length >= 2) lines.push({ kind: look.kind, look: one.vehicle, name: "", stops, loop: false, local: true });
  }
  if (!designed) {
  // THE COMMUTE (the user, 2026-11-18: "如果是regular rotate, 就会有通勤公交车，火车之类"): a train between two lands whose
  // tasks shared many days (agent/playworld.py _lay_regions) - there and back. Without any, a loop round the lands.
  const commutes = (play.world.commutes || []).map((one) => [lands.find((l) => l.region.id === one.from), lands.find((l) => l.region.id === one.to)])
    .filter(([a, b]) => a && b);
  for (const [a, b] of commutes) {
    const stops = [stop(a, "train"), stop(b, "train")].filter(Boolean);
    if (stops.length === 2) lines.push({ kind: "train", stops, loop: false, commute: true });
  }
  const ground = commutes.length ? [] : lands.filter((one) => !["skycity", "underwater", "space"].includes(one.region.biome));
  if (ground.length >= 2) {
    const cx = ground.reduce((s, one) => s + one.heart[0], 0) / ground.length, cy = ground.reduce((s, one) => s + one.heart[1], 0) / ground.length;
    ground.sort((a, b) => Math.atan2(a.heart[1] - cy, a.heart[0] - cx) - Math.atan2(b.heart[1] - cy, b.heart[0] - cx));
    const stops = ground.map((one) => stop(one, "train")).filter(Boolean);
    if (stops.length >= 2) lines.push({ kind: "train", stops, loop: stops.length >= 3 });
  }
  // The ferry: between the lands by the water - ON THE WATER (the user, 2026-11-18: "你现在怎么轮船在路上开"): each stop
  // a landing on the shore with the water beside it; a land joined to the last by no water is left out, and with
  // fewer than two there is no ferry.
  const wet = lands.filter((one) => PLAY_WATERY.has(one.region.biome) || one.region.layout === "islands");
  if (wet.length >= 2) {
    const stops = wet.map((one) => playShoreNear(one.heart[0], one.heart[1], one.region)).filter(Boolean);
    if (stops.length >= 2) lines.push({ kind: "ferry", stops, loop: false });
  }
  // THE AIRSHIP IN THE SKY ONLY (the user, 2026-11-18: "你每个交通工具要有能运动的地方，比如轮船只能在水面，天空之船只能在
  // 云端"): its landings on the cloud edge of each city in the sky - one on each side of it - and its way over the open
  // sky between them; with fewer than two, or no sky between, no airship. (The ferry keeps to the water, the train to
  // its rails; the plane flies above all.)
  const skies = lands.filter((one) => one.region.biome === "skycity");
  const docks = skies.flatMap((one) => [-1, 1].map((side) => playSkyNear(one.heart[0], one.heart[1], one.region, side))).filter(Boolean)
    .filter((one, n, all) => all.findIndex((o) => Math.hypot(o.x - one.x, o.y - one.y) < 6) === n);
  if (docks.length >= 2) lines.push({ kind: "airship", stops: docks, loop: false });
  // The plane: from where it began to the farthest land, when far.
  if (lands.length >= 2) {
    const first = lands[0];
    const farthest = lands.slice(1).sort((a, b) => Math.hypot(b.heart[0] - first.heart[0], b.heart[1] - first.heart[1])
      - Math.hypot(a.heart[0] - first.heart[0], a.heart[1] - first.heart[1]))[0];
    if (Math.hypot(farthest.heart[0] - first.heart[0], farthest.heart[1] - first.heart[1]) > 120) {
      const stops = [stop(first, "plane"), stop(farthest, "plane")].filter(Boolean);
      if (stops.length === 2) lines.push({ kind: "plane", stops, loop: false });
    }
  }
  }
  // Each line's way: from stop to stop (round to the first again, on a loop), and how long it is. THE TRAIN'S ON THE
  // GROUND (the user, 2026-11-17: "火车轨道 ... 从房子和地面上方直接跨过去，看着比较粗糙"): round the buildings, by the way
  // the roads are found; over water and the dark between lands, on a viaduct.
  for (const line of lines.slice()) {
    // A BUS ON THE ROADS ONLY (the user, 2026-11-20: "所有公交车必须沿路开。你现在有公交车是按照飞机的路线规划的"): each
    // stop on the road nearest it, the way from stop to stop over road tiles alone - never across the grass, never a
    // straight line; with no road between two of its stops, no bus.
    const onRoad = line.kind === "road";
    // ON A STREET, NOT A FOOTPATH (the user, 2026-11-20: "我说的路不是能走的地面，而是真的道路"; "宽度一格是可以的"): a paved
    // way, a tile wide or more (playStreets) - never a path of earth or sand, a boardwalk, the grass.
    const streets = onRoad ? playStreets(map) : null;
    if (onRoad) {
      line.stops = line.stops.map((one) => {
        const at = streets ? playRoadNear(streets, one.x, one.y) : null;
        return at && Object.assign({}, one, { x: at[0], y: at[1] });
      }).filter(Boolean);
      if (line.stops.length < 2) { lines.splice(lines.indexOf(line), 1); continue; }
    }
    const ends = [...line.stops, ...(line.loop ? [line.stops[0]] : [])];
    const afloat = line.kind === "ferry" || line.kind === "airship";       // on the water, in the sky: its own way only
    const at = (one) => (afloat ? [one.wx, one.wy] : [one.x, one.y]);
    const ok = line.kind === "ferry" ? (x, y) => playIsWater(x, y) : line.kind === "airship" ? (x, y) => playIsSky(x, y)
      : onRoad ? (x, y) => streets.has(`${x},${y}`)
      : line.kind === "train" && map.blocked ? (x, y) => !map.blocked.has(`${x},${y}`) : null;
    const pts = [], starts = [];
    for (let i = 1; i < ends.length; i += 1) {
      const a = at(ends[i - 1]), b = at(ends[i]);
      let route = null;
      // A BOAT IN THE WATER ALL THE WAY (the user, 2026-11-20: "轮船要求一直在水里开"): out on open water - a tile of
      // water with water all round it, off the banks and out of narrow streams - from its landing to the next; only
      // where there is no such way, by any water at all; never over land.
      let wide = false;
      if (line.kind === "ferry") {
        route = playWaterRoute(a, b, (x, y) => playOpenWater(x, y) || (x === b[0] && y === b[1]));
        wide = Boolean(route);
      }
      if (afloat) { if (!route) route = playWaterRoute(a, b, ok); }
      else if (onRoad) {
        try { route = playRoadRoute(streets, a, b); } catch (_err) { route = null; }
      } else if (ok) { try { route = playRoute(map.blocked, a, b, 0, 211 + i, new Map()); } catch (_err) { route = null; } }
      let leg = route && route.length ? route.map(([x, y]) => [x + 0.5, y + 0.5]) : null;
      if (!leg && (afloat || onRoad)) { line.none = true; break; }   // no water, no sky, no road between: none
      leg = leg || [[a[0] + 0.5, a[1] + 0.5], [b[0] + 0.5, b[1] + 0.5]];
      // A BUS ON ITS STREETS TURNS SQUARE (the user, 2026-11-20: "bus的移动路线 ... 应该是拐直角弯的（和火车的弯曲铁轨是
      // 不一样的）"): its way tile by tile along the street - no corner cut, no curve.
      leg = onRoad ? playSquareLeg(leg) : ok ? playPulled(leg, wide ? (x, y) => playOpenWater(x, y) : ok) : leg;
      pts.push(...(pts.length ? leg.slice(1) : leg));
    }
    if (line.none || pts.length < 2) { lines.splice(lines.indexOf(line), 1); continue; }
    // Rounded as one - where its legs meet at a stop too - and each stop then found along it, in order.
    if (ok && line.kind !== "road") pts.splice(0, pts.length, ...playRounded(pts, ok));   // a bus: square (playSquareLeg)
    let from = 0;
    for (const one of ends) {
      const [sx, sy] = at(one).map((v) => v + 0.5);
      let best = from, far = Infinity;
      for (let i = from; i < pts.length; i += 1) {
        const d = Math.hypot(pts[i][0] - sx, pts[i][1] - sy);
        if (d < far) { far = d; best = i; }
        if (far < 0.8 && d > far + 6) break;
      }
      starts.push(best); from = best;
    }
    if (line.kind === "train") {
      line.bridge = pts.map(([x, y]) => { try { const [b, i] = playCell(map, Math.floor(x), Math.floor(y)); return Boolean(b.none[i]) || playActWater(x, y); } catch (_err) { return false; } });
    }
    const cum = [0];
    for (let i = 1; i < pts.length; i += 1) cum.push(cum[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
    line.pts = pts; line.cum = cum; line.length = cum[cum.length - 1];
    line.stopAt = line.stops.map((_one, i) => cum[starts[i]]);
  }
  map.lines = lines;
  return lines;
}
/* THE STREETS: the ways of a paved stuff, however wide - as the road is drawn (play-regions.js: its own
   material, else a grid's street, else the land's path) - kept with the map. */
const PLAY_PAVED = new Set(["asphalt", "paving", "pavement2", "cobble", "cobble2", "stone"]);
function playStreets(map) {
  if (map.streets) return map.streets;
  map.streets = new Map();
  for (const [key, one] of map.paths || []) {
    const region = (map.regions || [])[one.region] || {}, land = region.land || {};
    const layout = region.layout || "village";
    const stuff = one.material || (layout === "grid" ? land.street : layout === "islands" ? "bridge" : (land.path || [])[0]);
    if (PLAY_PAVED.has(stuff)) map.streets.set(key, one);
  }
  return map.streets;
}
/* THE WAY OVER THE ROADS ALONE from one road tile to another - the whole road network, however far round it goes
   (breadth first, the fewest tiles) - or null when no road joins them. */
function playRoadRoute(paths, a, b) {
  const goal = `${b[0]},${b[1]}`, start = `${a[0]},${a[1]}`;
  if (!paths.has(start) || !paths.has(goal)) return null;
  const came = new Map([[start, ""]]);
  let edge = [a];
  while (edge.length && !came.has(goal) && came.size < 400000) {
    const next = [];
    for (const [x, y] of edge) {
      for (const [nx, ny] of [[x + 1, y], [x - 1, y], [x, y + 1], [x, y - 1]]) {
        const key = `${nx},${ny}`;
        if (came.has(key) || !paths.has(key)) continue;
        came.set(key, `${x},${y}`);
        next.push([nx, ny]);
      }
    }
    edge = next;
  }
  if (!came.has(goal)) return null;
  const out = [];
  for (let key = goal; key; key = came.get(key)) out.push(key.split(",").map(Number));
  return out.reverse();
}
/* A leg of a street drawn square: the turns kept, the steps between them along a straight run dropped. */
function playSquareLeg(leg) {
  if (leg.length < 3) return leg;
  const out = [leg[0]];
  for (let i = 1; i < leg.length - 1; i += 1) {
    const [ax, ay] = out[out.length - 1], [bx, by] = leg[i], [cx, cy] = leg[i + 1];
    if (Math.sign(bx - ax) !== Math.sign(cx - bx) || Math.sign(by - ay) !== Math.sign(cy - by)) out.push(leg[i]);   // a turn
  }
  out.push(leg[leg.length - 1]);
  return out;
}
/* The road tile nearest a spot (within twelve tiles), or null. */
function playRoadNear(paths, hx, hy) {
  const x0 = Math.floor(hx), y0 = Math.floor(hy);
  for (let r = 0; r <= 12; r += 1) {
    for (let dy = -r; dy <= r; dy += 1) {
      for (let dx = -r; dx <= r; dx += 1) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) !== r) continue;
        if (paths.has(`${x0 + dx},${y0 + dy}`)) return [x0 + dx, y0 + dy];
      }
    }
  }
  return null;
}
/* Water with water on its four sides: out from the bank, where a boat drawn larger than a tile stays on the water. */
function playOpenWater(x, y) {
  return playIsWater(x, y) && playIsWater(x + 1, y) && playIsWater(x - 1, y) && playIsWater(x, y + 1) && playIsWater(x, y - 1);
}
function playIsWater(x, y) {
  try { return playActWater(x, y); } catch (_err) { return false; }
}
/* A landing for the ferry near a land's heart: a tile one can stand on with water beside it (the water kept with it). */
function playShoreNear(hx, hy, region) {
  for (let r = 2; r <= 30; r += 1) {
    for (let dy = -r; dy <= r; dy += 1) {
      for (let dx = -r; dx <= r; dx += 1) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) !== r) continue;
        const x = Math.floor(hx) + dx, y = Math.floor(hy) + dy;
        if (!playActFree(x, y)) continue;
        for (const [ox, oy] of [[0, 1], [1, 0], [-1, 0], [0, -1]]) {
          if (playIsWater(x + ox, y + oy) && playIsWater(x + 2 * ox, y + 2 * oy)) {
            return { x, y, wx: x + 2 * ox, wy: y + 2 * oy, name: playWords(region.name || "") || region.biome, region };
          }
        }
      }
    }
  }
  return null;
}
/* The open sky: the void round a city in the sky, and its clouds' edge - where the airship goes. */
function playIsSky(x, y) {
  try {
    const [block, i] = playCell(play.map, Math.floor(x), Math.floor(y));
    return String(block.ground[i] || "") === "voidSky";
  } catch (_err) { return false; }
}
/* A landing for the airship on a city in the sky, on one side of its heart (-1 west, 1 east): a tile one can stand on
   with the open sky beside it (the sky kept with it, a tile further out). */
function playSkyNear(hx, hy, region, side) {
  for (let r = 2; r <= 40; r += 1) {
    for (let dy = -Math.min(r, 8); dy <= Math.min(r, 8); dy += 1) {
      const x = Math.floor(hx) + side * r, y = Math.floor(hy) + dy;
      if (!playActFree(x, y)) continue;
      if (playIsSky(x + side, y) && playIsSky(x + 2 * side, y)) {
        return { x, y, wx: x + 2 * side, wy: y, name: playWords(region.name || "") || region.biome, region };
      }
    }
  }
  return null;
}
/* The way on the water - or, given `wet`, in the sky - from one tile to another (four ways, in a box round them; none
   if they do not join). */
function playWaterRoute(from, to, wet) {
  // Only from a tile to a tile: an end not a whole number (undefined, NaN) gave no start in the box, and the way back
  // from the goal went round for ever (2026-11-21, the page out of memory) - none; the way back never longer than
  // the box.
  if (![from[0], from[1], to[0], to[1]].every(Number.isInteger)) return null;
  const open = wet || playIsWater;
  const pad = 30, minX = Math.min(from[0], to[0]) - pad, minY = Math.min(from[1], to[1]) - pad;
  const W = Math.abs(from[0] - to[0]) + 2 * pad + 1, H = Math.abs(from[1] - to[1]) + 2 * pad + 1;
  const came = new Int32Array(W * H).fill(-2), queue = new Int32Array(W * H);
  const start = (from[0] - minX) + (from[1] - minY) * W, goal = (to[0] - minX) + (to[1] - minY) * W;
  let head = 0, tail = 0;
  came[start] = -1; queue[tail++] = start;
  while (head < tail) {
    const i = queue[head++];
    if (i === goal) break;
    const x = i % W, y = (i / W) | 0;
    for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      const nx = x + dx, ny = y + dy;
      if (nx < 0 || ny < 0 || nx >= W || ny >= H) continue;
      const n = nx + ny * W;
      if (came[n] !== -2 || !open(nx + minX, ny + minY)) continue;
      came[n] = i; queue[tail++] = n;
    }
  }
  if (came[goal] === -2) return null;
  const route = [];
  for (let i = goal; i !== -1; i = came[i]) {
    route.push([(i % W) + minX, ((i / W) | 0) + minY]);
    if (route.length > W * H) return null;
  }
  return route.reverse();
}
/* A straight run is clear when it and a little to each side of it pass only where the line may go. */
function playClear(a, b, ok) {
  const d = Math.hypot(b[0] - a[0], b[1] - a[1]), n = Math.max(1, Math.ceil(d * 4));
  const px = d ? -(b[1] - a[1]) / d * 0.35 : 0, py = d ? (b[0] - a[0]) / d * 0.35 : 0;
  for (let k = 0; k <= n; k += 1) {
    const x = a[0] + (b[0] - a[0]) * k / n, y = a[1] + (b[1] - a[1]) * k / n;
    for (const s of [0, 1, -1]) if (!ok(Math.floor(x + px * s), Math.floor(y + py * s))) return false;
  }
  return true;
}
/* A tile route pulled straight: from each point on to the farthest it can see clear - no stairs of tiles. */
function playPulled(pts, ok) {
  if (pts.length < 3) return pts;
  const out = [pts[0]];
  let i = 0;
  while (i < pts.length - 1) {
    let j = Math.min(pts.length - 1, i + 60);
    while (j > i + 1 && !playClear(pts[i], pts[j], ok)) j -= 1;
    out.push(pts[j]);
    i = j;
  }
  return out;
}
/* EACH TURN A CURVE (the user, 2026-11-18: "火车铁轨转弯不够smooth"): a corner rounded as wide as its runs allow (up to
   four tiles), narrower while the curve would pass where the line may not; samples close enough to look smooth. */
function playRounded(pts, ok) {
  if (pts.length < 3) return pts;
  const out = [pts[0]];
  for (let i = 1; i < pts.length - 1; i += 1) {
    const [a, c, b] = [pts[i - 1], pts[i], pts[i + 1]];
    const la = Math.hypot(a[0] - c[0], a[1] - c[1]), lb = Math.hypot(b[0] - c[0], b[1] - c[1]);
    let r = Math.min(4, la * 0.48, lb * 0.48), curve = null;
    while (r >= 0.5 && !curve) {
      const p = [c[0] + (a[0] - c[0]) / la * r, c[1] + (a[1] - c[1]) / la * r];
      const q = [c[0] + (b[0] - c[0]) / lb * r, c[1] + (b[1] - c[1]) / lb * r];
      const steps = Math.max(4, Math.ceil(r * 3)), arc = [];
      for (let k = 0; k <= steps; k += 1) {
        const s = k / steps;
        arc.push([(1 - s) * (1 - s) * p[0] + 2 * (1 - s) * s * c[0] + s * s * q[0], (1 - s) * (1 - s) * p[1] + 2 * (1 - s) * s * c[1] + s * s * q[1]]);
      }
      if (arc.every(([x, y]) => ok(Math.floor(x), Math.floor(y)))) curve = arc;
      else r /= 2;
    }
    out.push(...(curve || [c]));
  }
  out.push(pts[pts.length - 1]);
  return out;
}
/* The point u tiles from the line's start (round on a loop; held at its ends on a line). */
function playLinePoint(line, u) {
  const L = line.length || 1, cum = line.cum;
  u = line.loop ? ((u % L) + L) % L : Math.max(0, Math.min(L, u));
  let lo = 1, hi = cum.length - 1;
  while (lo < hi) { const mid = (lo + hi) >> 1; if (cum[mid] < u) lo = mid + 1; else hi = mid; }
  const a = line.pts[lo - 1], b = line.pts[lo], seg = (cum[lo] - cum[lo - 1]) || 1, k = (u - cum[lo - 1]) / seg;
  return [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k];
}
/* The way the line runs at u, going dir (1 or -1): from a little behind to a little ahead - so it turns as the curve
   does, not in jumps at each of its points. */
function playLineHeading(line, u, dir, reach = 0.7) {
  const a = playLinePoint(line, u - reach), b = playLinePoint(line, u + reach);
  return Math.atan2((b[1] - a[1]) * dir, (b[0] - a[0]) * dir);
}
function playLineAt(line, d) {
  // A point d tiles of running along the line (round and round on a loop; back and forth on a line), its heading, how
  // far from the start it is and which way it is going.
  const L = line.length || 1;
  let u = d, dir = 1;
  if (line.loop) u = ((d % L) + L) % L;
  else { const m = ((d % (2 * L)) + 2 * L) % (2 * L); if (m > L) { u = 2 * L - m; dir = -1; } else u = m; }
  const [x, y] = playLinePoint(line, u);
  return [x, y, playLineHeading(line, u, dir), u, dir];
}
/* THE TRAIN ON ITS RAILS (the user, 2026-11-18: "你现在的火车旋转很不行"; "你现在的火车根本没有贴在铁轨上"): each car
   its own place along the track, a car's length apart - not pushed straight back from the engine, off the rails at
   every turn - its middle on the rails and turned the way they run between its two bogies, both on them too. On
   a line there and back an engine at each end, so it turns back without a jump; round a loop, one in front. */
const PLAY_CARS = 4, PLAY_CAR_GAP = 2.05, PLAY_BOGIE = 0.85;
function playTrainCars(line, d) {
  const L = line.length || 1, g = PLAY_CAR_GAP, n = PLAY_CARS;
  if (line.loop) {
    const u = ((d % L) + L) % L;
    return Array.from({ length: n }, (_x, k) => ({ u: u - k * g, dir: 1, engine: k === 0 }));
  }
  const span = Math.max(0.01, L - (n - 1) * g), m = ((d % (2 * span)) + 2 * span) % (2 * span);
  const dir = m > span ? -1 : 1, rear = m > span ? 2 * span - m : m;
  return Array.from({ length: n }, (_x, k) => ({ u: rear + k * g, dir, engine: k === 0 || k === n - 1 }));
}
function playTrainDraw(line, cars, size) {
  const { ctx } = play;
  const placed = cars.map((car) => {
    const front = playLinePoint(line, car.u + car.dir * PLAY_BOGIE), back = playLinePoint(line, car.u - car.dir * PLAY_BOGIE);
    const [x, y] = playLinePoint(line, car.u);
    return { car, x, y, heading: Math.atan2(front[1] - back[1], front[0] - back[0]) };
  }).sort((a, b) => a.y - b.y);                                // the nearer over the farther
  for (const one of placed) {
    const [sx, sy] = playActScreen(one.x, one.y);
    const look = PLAY_VEHICLES[line.look] || PLAY_VEHICLES.train;
    const spec = PLAY_SCENERY[one.car.engine ? look.engine : look.car], sheet = spec && play.sheets[spec[0]];
    if (!sheet) continue;
    const w = size * Math.min(2, spec[3]), h = size * Math.min(2, spec[4]);   // a camel, a reindeer: a tile; a carriage: two
    ctx.save();
    ctx.translate(sx, sy);                                   // on the rails, its middle on the way
    // Turned the way it runs; going left, the right way up.
    if (Math.cos(one.heading) < 0) { ctx.rotate(one.heading - Math.PI); ctx.scale(-1, 1); } else ctx.rotate(one.heading);
    if (look.tint) ctx.filter = look.tint;                   // a metro: the carriages in its own colours
    ctx.drawImage(sheet, spec[1] * 17, spec[2] * 17, spec[3] * 17 - 1, spec[4] * 17 - 1, -w / 2, -h / 2, w, h);
    ctx.restore();
  }
}
function playVehicleDraw(kind, x, y, heading, size, riding, look = "") {
  const { ctx } = play;
  const [sx, sy] = playActScreen(x, y);
  const draw = (name, dx, dy, w, h, flip) => {
    const spec = PLAY_SCENERY[name], sheet = spec && play.sheets[spec[0]];
    if (!sheet) return;
    ctx.save();
    ctx.translate(sx + dx, sy + dy);
    if (flip) ctx.scale(-1, 1);
    ctx.drawImage(sheet, spec[1] * 17, spec[2] * 17, spec[3] * 17 - 1, spec[4] * 17 - 1, -w / 2, -h / 2, w, h);
    ctx.restore();
  };
  const left = Math.cos(heading) < 0;
  if (kind === "plane" || kind === "airship") {
    const lift = size * 2.2;
    ctx.fillStyle = "rgba(0, 0, 0, 0.18)";                  // its shadow on the ground below
    ctx.beginPath(); ctx.ellipse(sx, sy, size * 1.1, size * 0.35, 0, 0, Math.PI * 2); ctx.fill();
    const body = (PLAY_VEHICLES[look] || {}).body || "";
    if (body === "helicopter") playHelicopterDraw(sx, sy - lift, heading, size);
    else if (body === "balloon") playBalloonDraw(sx, sy - lift + Math.sin(performance.now() / 700) * size * 0.15, size);
    else if (body === "ufo") playUfoDraw(sx, sy - lift + Math.sin(performance.now() / 300) * size * 0.08, size);
    else if (body === "spaceship") {
      ctx.save(); ctx.translate(sx, sy - lift * 1.3); ctx.rotate(heading + Math.PI / 2);
      const spec = PLAY_SCENERY.rocket, sheet = spec && play.sheets[spec[0]];
      if (sheet) ctx.drawImage(sheet, spec[1] * 17, spec[2] * 17, spec[3] * 17 - 1, spec[4] * 17 - 1, -size * 0.8, -size * 1.6, size * 1.6, size * 3.2);
      ctx.fillStyle = "rgba(255, 190, 80, 0.85)";            // its flame
      ctx.beginPath(); ctx.ellipse(0, size * 1.75, size * 0.25, size * (0.4 + 0.15 * Math.sin(performance.now() / 60)), 0, 0, Math.PI * 2); ctx.fill();
      ctx.restore();
    } else if (kind === "plane") {
      ctx.save(); ctx.translate(sx, sy - lift); ctx.rotate(heading + Math.PI / 2);
      const spec = PLAY_SCENERY.airplane, sheet = spec && play.sheets[spec[0]];
      if (sheet) ctx.drawImage(sheet, spec[1] * 17, spec[2] * 17, spec[3] * 17 - 1, spec[4] * 17 - 1, -size * 1.5, -size * 1.5, size * 3, size * 3);
      ctx.restore();
    } else draw("airship", 0, -lift + Math.sin(performance.now() / 600) * size * 0.1, size * 3, size * 2, !left);
  } else if (kind === "road") {
    playRoadVehicleDraw(sx, sy, heading, size, (PLAY_VEHICLES[look] || {}).body || "bus");
  } else if (kind === "ferry" && (PLAY_VEHICLES[look] || {}).body === "submarine") {
    playSubmarineDraw(sx, sy - size * 0.3 + Math.sin(performance.now() / 600) * size * 0.05, heading, size);
  } else if (kind === "ferry") {
    const body = (PLAY_VEHICLES[look] || {}).body || "ferry";   // a ferry, a sailing boat
    draw(body, 0, -size * 0.6 + Math.sin(performance.now() / 500) * size * 0.06, body === "ship" ? size * 2 : size * 3, size * 2, left);
  }                                                          // the train: playTrainDraw, car by car on its rails
  if (riding) playActMeOn(sx, sy - (kind === "plane" || kind === "airship" ? size * 2.6 : kind === "train" ? size * 0.3 : size * 0.9), size);
}
/* A SUBMARINE, A HOT-AIR BALLOON, A FLYING SAUCER - drawn here (2026-11-20: under the sea, in the sky, in space). */
function playSubmarineDraw(sx, sy, heading, size) {
  const { ctx } = play;
  ctx.save(); ctx.translate(sx, sy); if (Math.cos(heading) < 0) ctx.scale(-1, 1);
  ctx.fillStyle = "#f2c230"; ctx.beginPath(); ctx.ellipse(0, 0, size * 1.2, size * 0.45, 0, 0, Math.PI * 2); ctx.fill();
  ctx.fillRect(-size * 0.25, -size * 0.75, size * 0.5, size * 0.4);                  // its tower
  ctx.fillStyle = "#555"; ctx.fillRect(size * 0.05, -size * 1.05, size * 0.06, size * 0.32);   // its periscope
  ctx.fillStyle = "#9fd8f0";
  for (const at of [-0.55, 0, 0.55]) { ctx.beginPath(); ctx.arc(at * size, 0, size * 0.14, 0, Math.PI * 2); ctx.fill(); }
  ctx.fillStyle = "#c9961f"; ctx.fillRect(-size * 1.35, -size * 0.25, size * 0.2, size * 0.5);   // its propeller
  ctx.fillStyle = "rgba(255,255,255,0.6)";
  for (let k = 0; k < 3; k += 1) { ctx.beginPath(); ctx.arc(-size * (1.5 + k * 0.25), -size * (0.2 + k * 0.25), size * 0.06, 0, Math.PI * 2); ctx.fill(); }
  ctx.restore();
}
function playBalloonDraw(sx, sy, size) {
  const { ctx } = play;
  ctx.save(); ctx.translate(sx, sy - size * 0.6);
  const stripes = ["#e74c3c", "#f2c94c", "#3e8fc9", "#f2c94c", "#e74c3c"];
  stripes.forEach((colour, k) => {
    ctx.fillStyle = colour; ctx.beginPath();
    ctx.ellipse(0, 0, size * (0.9 - k * 0.18), size * 1.05, 0, 0, Math.PI * 2); ctx.fill();
  });
  ctx.strokeStyle = "#6b4a2b"; ctx.lineWidth = Math.max(1, size / 16);
  ctx.beginPath(); ctx.moveTo(-size * 0.5, size * 0.85); ctx.lineTo(-size * 0.25, size * 1.45);
  ctx.moveTo(size * 0.5, size * 0.85); ctx.lineTo(size * 0.25, size * 1.45); ctx.stroke();
  ctx.fillStyle = "#8b5a2b"; ctx.fillRect(-size * 0.3, size * 1.45, size * 0.6, size * 0.4);   // its basket
  ctx.restore();
}
function playUfoDraw(sx, sy, size) {
  const { ctx } = play;
  ctx.save(); ctx.translate(sx, sy);
  ctx.fillStyle = "rgba(160, 255, 200, 0.22)";                                       // its beam
  ctx.beginPath(); ctx.moveTo(-size * 0.35, size * 0.2); ctx.lineTo(size * 0.35, size * 0.2); ctx.lineTo(size * 0.8, size * 2.0); ctx.lineTo(-size * 0.8, size * 2.0); ctx.fill();
  ctx.fillStyle = "#9fe3ff"; ctx.beginPath(); ctx.ellipse(0, -size * 0.25, size * 0.45, size * 0.38, 0, Math.PI, 0); ctx.fill();
  ctx.fillStyle = "#9aa3ad"; ctx.beginPath(); ctx.ellipse(0, 0, size * 1.2, size * 0.32, 0, 0, Math.PI * 2); ctx.fill();
  const t = performance.now() / 200;
  for (let k = 0; k < 5; k += 1) {
    ctx.fillStyle = (Math.floor(t) + k) % 2 ? "#f2c94c" : "#6fe39a";
    ctx.beginPath(); ctx.arc((k - 2) * size * 0.45, size * 0.06, size * 0.08, 0, Math.PI * 2); ctx.fill();
  }
  ctx.restore();
}
/* A BUS, A TAXI - drawn here (no picture of one among the packs): its body the way it runs, windows, wheels. */
function playRoadVehicleDraw(sx, sy, heading, size, body) {
  const { ctx } = play;
  const bus = body === "bus";
  const w = size * (bus ? 2.2 : 1.4), h = size * (bus ? 0.95 : 0.75);
  ctx.save();
  ctx.translate(sx, sy - size * 0.45);
  if (Math.cos(heading) < 0) ctx.scale(-1, 1);
  ctx.fillStyle = "rgba(0, 0, 0, 0.2)";
  ctx.fillRect(-w / 2, h / 2 - size * 0.05, w, size * 0.12);
  ctx.fillStyle = bus ? "#d9482b" : "#f2c230";
  ctx.fillRect(-w / 2, -h / 2, w, h);
  ctx.fillStyle = "#cfe8f5";                                  // its windows
  const panes = bus ? 4 : 2;
  for (let k = 0; k < panes; k += 1) ctx.fillRect(-w / 2 + size * 0.12 + k * (w - size * 0.24) / panes, -h / 2 + size * 0.1,
                                                  (w - size * 0.24) / panes - size * 0.06, h * 0.35);
  if (!bus) { ctx.fillStyle = "#222"; ctx.fillRect(-size * 0.2, -h / 2 - size * 0.14, size * 0.4, size * 0.14); }   // its sign
  ctx.fillStyle = "#222";                                     // its wheels
  for (const at of [-w / 2 + size * 0.3, w / 2 - size * 0.3]) { ctx.beginPath(); ctx.arc(at, h / 2, size * 0.16, 0, Math.PI * 2); ctx.fill(); }
  ctx.restore();
}
/* A HELICOPTER - drawn here: its body, its tail, its rotor turning. */
function playHelicopterDraw(sx, sy, heading, size) {
  const { ctx } = play;
  ctx.save();
  ctx.translate(sx, sy);
  if (Math.cos(heading) < 0) ctx.scale(-1, 1);
  ctx.fillStyle = "#3a7bd5";
  ctx.beginPath(); ctx.ellipse(0, 0, size * 0.6, size * 0.38, 0, 0, Math.PI * 2); ctx.fill();
  ctx.fillRect(-size * 1.3, -size * 0.08, size * 0.8, size * 0.14);
  ctx.fillStyle = "#cfe8f5";
  ctx.beginPath(); ctx.ellipse(size * 0.28, -size * 0.06, size * 0.22, size * 0.18, 0, 0, Math.PI * 2); ctx.fill();
  ctx.strokeStyle = "#222"; ctx.lineWidth = Math.max(1, size / 10);
  const spin = performance.now() / 40;
  ctx.beginPath();
  ctx.moveTo(-Math.cos(spin) * size * 1.1, -size * 0.45); ctx.lineTo(Math.cos(spin) * size * 1.1, -size * 0.45);
  ctx.moveTo(-size * 0.5, size * 0.42); ctx.lineTo(size * 0.5, size * 0.42);
  ctx.stroke();
  ctx.restore();
}
function playActMeOn(sx, sy, size) {
  const sheet = play.sheets.me;
  if (sheet) play.ctx.drawImage(sheet, 0, (PLAY_ME_ROWS.down || 0) * 16, 16, 16, sx - size * 0.4, sy - size * 0.8, size * 0.8, size * 0.8);
}
function playTrackDraw(line, size) {
  // ITS TRACK AS ONE (the user, 2026-11-18: "你现在的火车铁轨也非常不行"): a gravel bed, two unbroken rails beside the way
  // and sleepers evenly along it, across the way it turns; over water or the dark, a viaduct on piers. Only the runs in
  // sight are drawn.
  const { ctx } = play;
  const pts = line.pts, n = pts.length;
  // EACH PIECE IN SIGHT BY ITSELF (the user, 2026-11-18: "你现在的铁轨，有的时候踩上去就不显示了"): a long straight piece
  // whose two ends are both out of sight still crosses it - asked of the piece, not of its ends, it is drawn.
  const view = playActView(3);
  const crosses = (a, b) => Math.max(a[0], b[0]) >= view[0] && Math.min(a[0], b[0]) <= view[2]
    && Math.max(a[1], b[1]) >= view[1] && Math.min(a[1], b[1]) <= view[3];
  const runs = [];
  for (let i = 1; i < n; i += 1) {
    if (!crosses(pts[i - 1], pts[i])) continue;
    if (!runs.length || runs[runs.length - 1][1] !== i - 1) runs.push([i - 1, i]);
    else runs[runs.length - 1][1] = i;
  }
  const normal = (i) => {
    const a = pts[Math.max(0, i - 1)], b = pts[Math.min(n - 1, i + 1)];
    const dx = b[0] - a[0], dy = b[1] - a[1], d = Math.hypot(dx, dy) || 1;
    return [-dy / d, dx / d];
  };
  ctx.save();
  ctx.lineCap = "round"; ctx.lineJoin = "round";
  for (const [i0, i1] of runs) {
    const screen = [];
    for (let i = i0; i <= i1; i += 1) screen.push(playActScreen(pts[i][0], pts[i][1]));
    // Piers and the deck's shadow, where it bridges.
    for (let i = i0; i <= i1; i += 1) {
      if (!line.bridge || !line.bridge[i] || i % 3) continue;
      const [sx, sy] = screen[i - i0];
      ctx.fillStyle = "#7d7368"; ctx.fillRect(sx - size * 0.12, sy, size * 0.24, size * 0.9);
      ctx.fillStyle = "rgba(0, 0, 0, 0.18)"; ctx.fillRect(sx - size * 0.4, sy + size * 0.9, size * 0.8, size * 0.14);
    }
    const stroke = (colour, width, off) => {
      ctx.strokeStyle = colour; ctx.lineWidth = width;
      ctx.beginPath();
      for (let i = i0; i <= i1; i += 1) {
        const [nx, ny] = normal(i), [sx, sy] = screen[i - i0];
        const x = sx + nx * size * off, y = sy + ny * size * off;
        if (i === i0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      ctx.stroke();
    };
    stroke("#8a7b6a", size * 0.6, 0);                                     // the bed
    // Sleepers: every 0.5 tile along the way, across it.
    ctx.strokeStyle = "#5b3a24"; ctx.lineWidth = Math.max(1, size * 0.12);
    ctx.beginPath();
    for (let i = Math.max(1, i0); i <= i1; i += 1) {
      const from = line.cum[i - 1], to = line.cum[i], seg = to - from;
      if (seg <= 0) continue;
      for (let d = Math.ceil(from / 0.5) * 0.5; d < to; d += 0.5) {
        const k = (d - from) / seg, x = pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * k, y = pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * k;
        const nx = -(pts[i][1] - pts[i - 1][1]) / seg, ny = (pts[i][0] - pts[i - 1][0]) / seg;
        const [sx, sy] = playActScreen(x, y);
        ctx.moveTo(sx - nx * size * 0.26, sy - ny * size * 0.26); ctx.lineTo(sx + nx * size * 0.26, sy + ny * size * 0.26);
      }
    }
    ctx.stroke();
    for (const off of [-0.15, 0.15]) stroke("#c3c9d3", Math.max(1, size * 0.07), off);   // the rails
  }
  ctx.restore();
}
function playLinesDraw(size, now) {
  const { ctx } = play;
  for (const line of playLines()) {
    if (line.kind === "train") playTrackDraw(line, size);
    for (const one of line.stops) {
      if (one.station || !playActInView(one.x, one.y, 4)) continue;   // a station stands there: drawn as its own piece
      const spec = PLAY_SCENERY[PLAY_LINE_LOOK[line.kind]], sheet = spec && play.sheets[spec[0]];
      if (!sheet) continue;
      const [sx, sy] = playActScreen(one.x + 0.5, one.y + 1);
      const w = spec[3] * size, h = spec[4] * size;
      ctx.drawImage(sheet, spec[1] * 17, spec[2] * 17, spec[3] * 17 - 1, spec[4] * 17 - 1, sx - w / 2, sy - h, w, h);
    }
    // Its vehicle, running when no one rides it.
    if (play.ride && play.ride.line === line) continue;
    const d = (now / 1000) * PLAY_LINE_SPEED[line.kind] * 0.35 + line.length * 0.37;
    if (line.kind === "train") {
      const cars = playTrainCars(line, d);
      if (cars.some((car) => { const [x, y] = playLinePoint(line, car.u); return playActInView(x, y, 3); })) playTrainDraw(line, cars, size);
      continue;
    }
    const at = playLineAt(line, d);
    if (playActInView(at[0], at[1], 8)) playVehicleDraw(line.kind, at[0], at[1], at[2], size, false, line.look);
  }
}

/* -- THE MENU: where to go ------------------------------------------------------------------------------------------- */
function playMenuOpen(title, options) {
  playMenuClose();
  const box = document.createElement("div");
  box.className = "side-play-menu";
  box.innerHTML = `<strong>${escapeHtml(title)}</strong>${options.map((one, n) =>
    `<button type="button" data-n="${n}"><span>${n + 1}</span>${escapeHtml(one.label)}</button>`).join("")}
    <small>${escapeHtml(playWords("Press a number, or Esc"))}</small>`;
  box.addEventListener("click", (event) => {
    const at = event.target.closest("[data-n]");
    if (at) { const one = options[Number(at.dataset.n)]; playMenuClose(); one.go(); }
  });
  play.el.appendChild(box);
  play.menu = { box, options };
}
function playMenuClose() {
  if (play.menu) { play.menu.box.remove(); play.menu = null; }
}
window.addEventListener("keydown", (event) => {
  if (!play.on || !play.menu) return;
  const n = Number(event.key);
  if (event.key === "Escape" || PLAY_KEYS[event.key]) { playMenuClose(); if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); } return; }
  if (n >= 1 && n <= play.menu.options.length) {
    event.preventDefault(); event.stopPropagation();
    const one = play.menu.options[n - 1];
    playMenuClose();
    one.go();
  }
}, true);

/* -- GOING: through a portal, or riding ------------------------------------------------------------------------------ */
function playAreaName(area) {
  const first = (play.world.regions || []).find((one) => playAreaOf(one) === area);
  return first ? playWords(first.name || "") || area : area;
}
function playTravelArea(area, through = null) {
  // Another area laid, this one let go of: its map, its blocks, its minimap; the walker at its gate - or, come through a
  // way between the maps, out in front of its end here.
  play.area = area;
  play.map = playLay(play.world, area);
  playChunksClear();
  play.miniBase = null;                        // another area: its own map, drawn as it comes
  play.miniBuilding = null;
  play.coasters = null;
  play.critters = [];
  const gate = playPortals().find((one) => one.gate);
  const came = through ? playCrossings().find((one) => one.crossing.id === through.crossing && one.end === through.end) : null;
  const at = came ? [came.x + Math.floor(came.w / 2), came.y + came.h]
    : gate ? [gate.x, gate.y + 1] : (playActSpotNear(play.map.hearts[0][0], play.map.hearts[0][1] + 2) || play.map.hearts[0]);
  play.flash = performance.now();
  play.me.x = at[0] + 0.5; play.me.y = at[1] + 0.6;
  play.region = null;
  playWhere();
  playSoundChime();
  playDraw();
}
function playTeleport(x, y) {
  play.flash = performance.now();
  play.me.x = x + 0.5; play.me.y = y + 1.6;
  playWhere();
  playSoundChime();
  playDraw();
}
function playRide(line, from, to) {
  // Along the line from one stop to the other - forward on a loop; the camera with it.
  const a = line.stopAt[from], b = line.stopAt[to];
  let along = b - a;
  if (line.loop && along < 0) along += line.length;
  play.ride = { line, start: a, along, at: performance.now(), seconds: Math.max(2.5, Math.abs(along) / PLAY_LINE_SPEED[line.kind]), to: line.stops[to] };
  play.sit = null;
  // THE VIEW WITH THE RIDE (the user, 2026-11-20: "agent坐上去了之后，没有自动追踪agent的位置"): looked elsewhere before
  // (W A S D, the map), the view stayed there while the walker rode off - on the walker again now.
  play.lookAt = null;
  if (play.pan) play.pan.clear();
  playSfx("door-close", 0.3);
  const tick = () => {
    const ride = play.ride;
    if (!ride || !play.on) return;
    const k = Math.min(1, (performance.now() - ride.at) / 1000 / ride.seconds);
    const eased = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
    ride.u = ride.start + ride.along * eased;                // where it is drawn too (playActOver)
    const [x, y] = playLineAt(ride.line, ride.u);
    play.me.x = x; play.me.y = y;
    playDraw();
    if (k < 1) { requestAnimationFrame(tick); return; }
    play.ride = null;
    const land = ride.line.kind === "ferry" ? [ride.to.x, ride.to.y] : (playActSpotNear(ride.to.x, ride.to.y + 1, 3) || [ride.to.x, ride.to.y + 1]);
    play.me.x = land[0] + 0.5; play.me.y = land[1] + 0.8;
    playWhere(); playSoundChime(); playDraw();
  };
  requestAnimationFrame(tick);
}

/* -- SITTING, LIGHTS ------------------------------------------------------------------------------------------------- */
const PLAY_SEATS = new Set(["chair", "bench", "sofa", "armchair", "stool", "dining_chair", "lounger", "throne", "swing"]);
const PLAY_LIGHT_THINGS = new Set(["lamp", "lamp_post", "wall_torch", "candelabra", "candle", "fireplace", "stove", "lantern"]);
function playLightsOff() {
  if (!play.lightsOff) {
    try { play.lightsOff = new Set(JSON.parse(localStorage.getItem("playLightsOff") || "[]")); } catch (_err) { play.lightsOff = new Set(); }
  }
  return play.lightsOff;
}
function playLightToggle(key, x, y) {
  const off = playLightsOff();
  if (off.has(key)) off.delete(key); else off.add(key);
  try { localStorage.setItem("playLightsOff", JSON.stringify([...off].slice(-400))); } catch (_err) { /* this time */ }
  play.puffs = play.puffs || [];
  play.puffs.push({ x, y, at: performance.now(), lit: !off.has(key) });
  playSoundNear();
}
function playDecoNear(test, tx, ty, most = 1.3) {
  const block = playBlock(play.map, Math.floor(tx / PLAY_CHUNK), Math.floor(ty / PLAY_CHUNK));
  let best = null, bestD = most;
  for (const one of block.deco) {
    if (!test(one)) continue;
    const d = Math.hypot(one.x + 0.5 - tx, one.y + 0.5 - ty);
    if (d < bestD) { bestD = d; best = one; }
  }
  return best;
}

/* -- WATER: fishing, swimming -------------------------------------------------------------------------------------- */
/* WHAT EACH WATER GIVES (the user, 2026-11-18: "你现在fish是不是钓不上鱼，要改成可以钓到鱼（不同的鱼或者别的什么东西）";
   "不同位置的鱼塘可能钓出来的东西是不一样的，这个也是你的设计的一部分"): the sea by the coast its reef fish, a lake or a pond
   its fresh-water ones, a waterfall's pool its small quick ones - and, now and then, a thing of the land it is in (a
   lantern of the graveyard, a love letter of Valentine's, a robot's part of the future). What the world's design said
   a water holds (its zone's, or its land's, "catch") comes first. */
const PLAY_CATCH_WATERS = {
  sea: ["clownfish", "clownfish2", "blueTang", "yellowTang", "moorishIdol", "butterflyfish2", "butterflyfish3", "parrotfish",
        "wrasse", "triggerfish", "boxfish", "redSnapper", "grouper"],
  pond: ["koi", "fish1", "smallFish", "angelfish", "koi"],
  falls: ["smallFish", "fish1", "smallFish", "koi"],
};
const PLAY_CATCH_ODD = { sea: ["jellyfish", "seaweed", "shark", "coral"], pond: ["seaweed", "goldIngot"], falls: ["runeStone"] };
const PLAY_CATCH_LANDS = {
  graveyard: ["graveCandle", "runeStone2"], halloween: ["jackOLantern", "pumpkin"], christmas: ["present", "present2"],
  newyear: ["redEnvelope", "goldIngot"], valentine: ["loveLetter", "redHeart", "lovePotion"], underwater: ["coral", "anemone"],
  scifi: ["metalBot5", "metalBot11"], space: ["metalBot17"], farmland: ["turnipRoot", "tomatoFruit"],
  botanical: ["cherries"], enchanted: ["sparkle", "wisp"], ruins: ["runeStone3", "goldIngot"], funfair: ["balloons"],
};
const PLAY_CATCH_ODD_SHARE = 0.18;
/* The water at a tile: "pond" in a lake, a pond or a fountain; "falls" in a waterfall's pool; the sea else. */
function playCatchWater(x, y) {
  const index = play.map;
  const one = index && index.zones.find((z) => x >= z.rect[0] && x < z.rect[2] && y >= z.rect[1] && y < z.rect[3]);
  const kind = one && one.zone.kind;
  return { water: kind === "waterfall" ? "falls" : one ? "pond" : "sea", zone: one ? one.zone : null };
}
/* What may be caught there, each as often as it is listed. */
function playCatchHere(x, y) {
  const { water, zone } = playCatchWater(x, y);
  const region = play.map ? playRegionAt(play.map, x, y) : null;
  const drawn = (names) => (names || []).filter((one) => PLAY_SCENERY[one]);
  const designed = drawn((zone && zone.catch) || (region && region.catch));
  const usual = designed.length ? designed : drawn(PLAY_CATCH_WATERS[water]);
  const odd = drawn([...(PLAY_CATCH_ODD[water] || []), ...(PLAY_CATCH_LANDS[region && region.biome] || [])]);
  return { usual, odd, water };
}
function playCatchPick(x, y, roll = Math.random(), pick = Math.random()) {
  const { usual, odd } = playCatchHere(x, y);
  const pool = odd.length && roll < PLAY_CATCH_ODD_SHARE ? odd : usual.length ? usual : odd;
  return pool.length ? pool[Math.floor(pick * pool.length) % pool.length] : null;
}
/* Long enough to see the float go under and press (it had been 1.1 s, and was often missed). */
const PLAY_BITE_MS = 2200;
function playFishAhead() {
  const step = { left: [-1, 0], right: [1, 0], up: [0, -1], down: [0, 1] }[play.me.facing] || [0, 1];
  for (let d = 1; d <= 3; d += 1) {
    const x = play.me.x + step[0] * d, y = play.me.y - 0.3 + step[1] * d;
    if (playActWater(x, y)) return [x, y];
  }
  return null;
}
function playFishPress() {
  const fish = play.fish, now = performance.now();
  if (!fish) {
    const at = playFishAhead();
    if (!at) return false;
    play.fish = { x: at[0], y: at[1], at: now, bite: now + 1500 + Math.random() * 3500 };
    playSfx("cloth", 0.3);
    return true;
  }
  play.fish = null;
  const name = now >= fish.bite && now < fish.bite + PLAY_BITE_MS ? playCatchPick(fish.x, fish.y) : null;
  if (name) {
    play.caught = { name, at: now };
    let log = {};
    try { log = JSON.parse(localStorage.getItem("playFishCaught") || "{}"); } catch (_err) { log = {}; }
    log[name] = (log[name] || 0) + 1;
    try { localStorage.setItem("playFishCaught", JSON.stringify(log)); } catch (_err) { /* this time */ }
    const total = Object.values(log).reduce((s, n) => s + n, 0);
    const kind = Object.keys(log).length;
    playActSay(`${playWords("Caught")}: ${playWords(playCatchName(name))}! (${total}, ${kind} ${playWords(kind === 1 ? "kind" : "kinds")})`);
    playSoundChime();
  } else {
    playActSay(playWords(now < fish.bite ? "Reeled in - nothing yet." : "It got away!"));
  }
  return true;
}
/* A catch's name as said: "yellowTang" a "yellow tang", "metalBot5" a "robot part". */
function playCatchName(name) {
  if (/^metalBot/.test(name)) return "robot part";
  return String(name).replace(/[0-9]+$/, "").replace(/([A-Z])/g, " $1").toLowerCase();
}
function playActSay(text) {
  play.said = { text, at: performance.now(), until: performance.now() + playSayFor(text), x: play.me.x, y: play.me.y };
}

/* -- E: what is near done - the rest looked at (play.js playLook) ------------------------------------------------- */
function playAct() {
  if (play.ride) return true;
  const tx = play.me.x, ty = play.me.y - 0.3;
  if (play.fish) return playFishPress();
  // A way to another map stood at: through it (playCrossings).
  const way = playCrossings().find((one) => tx >= one.x - 0.6 && tx <= one.x + one.w + 0.6 && ty >= one.y - 0.6 && ty <= one.y + one.h + 1.2);
  if (way) { playCrossingGo(way); return true; }
  // A portal stood in: where it goes.
  const portal = playPortals().find((one) => Math.hypot(one.x + 0.5 - tx, one.y + 0.7 - ty) < 1.1);
  if (portal && portal.gate) {
    playMenuOpen(playWords("Airport to other areas"), playAreas(play.world).filter((one) => one !== play.area).map((area) => ({
      label: playAreaName(area), go: () => playTravelArea(area) })));
    return true;
  }
  if (portal) {
    playMenuOpen(playWords("Portal"), playPortals().filter((one) => one !== portal && !one.gate).map((one) => ({
      label: one.name, go: () => playTeleport(one.x, one.y) })));
    return true;
  }
  // A stop: its line's other stops.
  for (const line of playLines()) {
    const here = line.stops.findIndex((one) => Math.hypot(one.x + 0.5 - tx, one.y + 0.5 - ty) < 1.8);
    if (here < 0) continue;
    playMenuOpen(`${playWords(playLineWords(line))}`, line.stops.map((one, n) => ({ one, n })).filter(({ n }) => n !== here)
      .map(({ one, n }) => ({ label: one.name, go: () => playRide(line, here, n) })));
    return true;
  }
  const near = play.near;
  // A GREAT PIECE (play.js playPieceAct; the user, 2026-11-19: "允许交互"): a track ridden, a stack climbed.
  if (near && near.kind === "piece" && typeof playPieceAct === "function" && playPieceAct(near)) return true;
  // A seat: sat on (and what it is said, as looking does).
  const seatThing = near && !near.at && PLAY_SEATS.has(near.kind) ? near : null;
  const seatDeco = !seatThing && playDecoNear((one) => /bench/i.test(one.name), tx, ty);
  if (seatThing || seatDeco) {
    const sx = seatThing ? seatThing.x + (PLAY_SIZES[seatThing.kind] || [1, 1])[0] / 2 : seatDeco.x + 0.5;
    const sy = seatThing ? seatThing.y + 0.75 : seatDeco.y + 0.75;
    play.sit = { x: sx, y: sy, fromX: play.me.x, fromY: play.me.y, at: performance.now() };
    playSfx("cloth", 0.25);
    playRun();
    return Boolean(seatDeco);                         // a thing's words are said too
  }
  // A light: out, or lit again.
  if (near && !near.at && PLAY_LIGHT_THINGS.has(near.kind)) {
    playLightToggle(`i:${near.id}`, near.x + 0.5, near.y + 0.3);
    return true;
  }
  const lamp = playDecoNear((one) => typeof PLAY_DECO_LIGHTS !== "undefined" && PLAY_DECO_LIGHTS[one.name], tx, ty, 1.5);
  if (lamp) { playLightToggle(`d:${lamp.x},${lamp.y}`, lamp.x + 0.5, lamp.y + 0.2); return true; }
  // Water ahead: a line cast.
  if (!play.swimming && playFishAhead()) return playFishPress();
  return false;
}
/* WHAT E WOULD DO HERE, SHOWN (the user, 2026-11-18: "所有可交互的东西，agent在附近都要提示可以交互"): asked as playAct
   asks, in its order, nothing done - a gate or a portal, a stop, fishing, a seat, a light, water ahead, a picture to
   see, a thing or a sign to hear about. Nothing to do: no hint. */
function playActHint() {
  if (play.ride || play.sit || play.menu || !play.map) return null;
  if (play.fish) return playWords("Reel in");
  const tx = play.me.x, ty = play.me.y - 0.3;
  const portal = playPortals().find((one) => Math.hypot(one.x + 0.5 - tx, one.y + 0.7 - ty) < 1.1);
  if (portal) return playWords(portal.gate ? "Go to another area" : "Go through the portal");
  for (const line of playLines()) {
    if (line.stops.some((one) => Math.hypot(one.x + 0.5 - tx, one.y + 0.5 - ty) < 1.8)) {
      return `${playWords("Ride")}: ${playWords(playLineWords(line))}`;
    }
  }
  const near = play.near;
  if ((near && !near.at && PLAY_SEATS.has(near.kind)) || playDecoNear((one) => /bench/i.test(one.name), tx, ty)) return playWords("Sit");
  const lightKey = near && !near.at && PLAY_LIGHT_THINGS.has(near.kind) ? `i:${near.id}` : (() => {
    const lamp = playDecoNear((one) => typeof PLAY_DECO_LIGHTS !== "undefined" && PLAY_DECO_LIGHTS[one.name], tx, ty, 1.5);
    return lamp ? `d:${lamp.x},${lamp.y}` : null;
  })();
  if (lightKey) return playWords(playLightsOff().has(lightKey) ? "Light it" : "Put the light out");
  if (!play.swimming && playFishAhead()) return playWords("Fish");
  if (near && near.picture_url) return playWords("See the picture");
  if (near) return playWords("Look");
  return null;
}
/* The hint over the walker's head: a key cap "E" and what it does - not while something is being said. */
function playActHintDraw(size) {
  const words = playActHint();
  if (!words || (play.said && performance.now() < play.said.until)) return;
  const { ctx, canvas } = play;
  const [cx, cy] = playActScreen(play.me.x, play.me.y - 1.55);
  const fontPx = Math.max(11, Math.round(size * 0.42)), pad = Math.round(fontPx * 0.45);
  ctx.save();
  ctx.font = `600 ${fontPx}px "Microsoft YaHei", "PingFang SC", system-ui, sans-serif`;
  ctx.textBaseline = "middle";
  const key = fontPx + pad, text = ctx.measureText(words).width;
  const w = key + pad + text + pad * 2, h = fontPx + pad * 2;
  const x = Math.max(4, Math.min(canvas.width - w - 4, cx - w / 2)), y = Math.max(4, cy - h);
  ctx.fillStyle = "rgba(28, 24, 20, 0.78)";
  ctx.beginPath(); ctx.roundRect(x, y, w, h, h / 2); ctx.fill();
  ctx.fillStyle = "#fffdf6";                                 // the key
  ctx.beginPath(); ctx.roundRect(x + pad * 0.6, y + pad * 0.5, key, h - pad, Math.round(fontPx * 0.25)); ctx.fill();
  ctx.fillStyle = "#3b2414";
  ctx.textAlign = "center"; ctx.fillText("E", x + pad * 0.6 + key / 2, y + h / 2 + 1);
  ctx.fillStyle = "#fffdf6";
  ctx.textAlign = "left"; ctx.fillText(words, x + pad * 0.6 + key + pad, y + h / 2 + 1);
  ctx.restore();
}
/* What near is shown on the card: a portal or a stop. */
function playActNear(tx, ty) {
  const portal = playPortals().find((one) => Math.hypot(one.x + 0.5 - tx, one.y + 0.7 - ty) < 1.1);
  if (portal) return { at: [portal.x + 0.5, portal.y + 0.9], portal, name: playWords("Portal") };
  for (const line of playLines()) {
    const one = line.stops.find((s) => Math.hypot(s.x + 0.5 - tx, s.y + 0.5 - ty) < 1.8);
    if (one) return { at: [one.x + 0.5, one.y + 0.9], stop: one, line, name: playWords(playLineWords(line)) };
  }
  return null;
}
function playActCard(near) {
  if (near.portal) {
    return `<div class="side-play-card-words"><strong class="chat-message">${escapeHtml(playWords("Portal"))} · ${escapeHtml(near.portal.name)}</strong>
      <span class="side-play-card-when">${escapeHtml(playWords("Press E to go to another portal"))}</span></div>`;
  }
  return `<div class="side-play-card-words"><strong class="chat-message">${escapeHtml(near.name)} · ${escapeHtml(near.stop.name)}</strong>
    <span class="side-play-card-when">${escapeHtml(`${playWords("Press E to ride to")}: ${near.line.stops.filter((one) => one !== near.stop).map((one) => one.name).join(" · ")}`)}</span></div>`;
}

/* -- LIFE ABOUT: birds over, small animals that run --------------------------------------------------------------- */
const PLAY_CRITTERS = { meadow: ["rabbit", "squirrel", "chicken"], farmland: ["chicken", "cow", "sheep", "goat"],
                        forest: ["squirrel", "rabbit", "deer"], primeval: ["deer", "squirrel", "rabbit"], autumn: ["squirrel", "rabbit"],
                        city: ["cat", "dog"], toytown: ["cat", "dog", "rabbit"], zoo: ["monkey", "zebra", "giraffe"],
                        underwater: ["clownfish", "yellowTang", "blueTang", "moorishIdol"], lakeside: ["rabbit", "chicken"],
                        botanical: ["rabbit", "squirrel"], seaside: ["chicken"], valentine: ["rabbit", "cat"] };
// FEW (the user, 2026-11-18: "小动物可以不要加这么多，减少对资源的消耗"; "你限制动物的数量就行 ... 动作还是正常速率"): two at
// most, moving as they always did - and only one moving asks for frames.
const PLAY_CRITTERS_MOST = 2;
/* A LAND'S OWN ANIMALS (play-regions.js: where a land lays a field animal it lives): those of the blocks laid near the
   walker, each at the spot it was laid, walking about it - as many as were laid, within 20 tiles; let go farther off,
   and back at their spots when the walker comes near again. Looked for twice a second. */
function playResidents(now) {
  if (now - (play.residentsAt || 0) < 500 || !play.map || !play.map.laid) return;
  play.residentsAt = now;
  const here = new Set(play.critters.filter((one) => one.home).map((one) => one.key));
  for (const block of play.map.laid.values()) {
    for (const one of block.animals || []) {
      const key = `${one.x},${one.y}`;
      if (here.has(key) || Math.floor(one.x / PLAY_CHUNK) !== block.cx || Math.floor(one.y / PLAY_CHUNK) !== block.cy) continue;
      if (Math.hypot(one.x - play.me.x, one.y - play.me.y) > 20 || !PLAY_SCENERY[one.name]) continue;
      here.add(key);
      play.critters.push({ name: one.name, key, home: [one.x + 0.5, one.y + 0.8], x: one.x + 0.5, y: one.y + 0.8,
                           tx: one.x + 0.5, ty: one.y + 0.8, hop: Math.random() * 6, swim: false, left: playHash(one.x, one.y, 5) < 0.5 });
    }
  }
}
function playCrittersStep(seconds) {
  if (!play.map) return;
  play.critters = play.critters || [];
  const now = performance.now();
  // Those far from the walker let go - a land's own back where they were laid, when the walker comes near again; new
  // ones where there is room, of the land's kinds (PLAY_CRITTERS_MOST of those, the land's own besides).
  play.critters = play.critters.filter((one) => Math.hypot(one.x - play.me.x, one.y - play.me.y) < (one.home ? 26 : 18));
  playResidents(now);
  if (play.critters.filter((one) => !one.home).length < PLAY_CRITTERS_MOST && Math.random() < 0.05) {
    const a = Math.random() * Math.PI * 2, d = 6 + Math.random() * 8;
    const x = Math.floor(play.me.x + Math.cos(a) * d), y = Math.floor(play.me.y + Math.sin(a) * d);
    const region = playRegionAt(play.map, x, y);
    // ITS OWN ANIMALS (the user, 2026-11-19: "不同场景的动物也是unique的"): those the agent chose for its scene.
    const kinds = region && ((region.animals || []).length ? region.animals : PLAY_CRITTERS[region.biome]);
    const swim = region && region.biome === "underwater";
    if (kinds && (swim || playActFree(x, y))) {
      const name = kinds[Math.floor(Math.random() * kinds.length)];
      if (PLAY_SCENERY[name]) play.critters.push({ name, x: x + 0.5, y: y + 0.8, tx: x + 0.5, ty: y + 0.8, hop: Math.random() * 6, swim, left: false });
    }
  }
  for (const one of play.critters) {
    const fear = Math.hypot(one.x - play.me.x, one.y - play.me.y) < 2.2;
    // ITS OWN CRY AS IT STARTS TO RUN (the user, 2026-11-19: "动物也是要有被追赶的音效（不同动物不一样）"; play-sound.js
    // PLAY_CRIES) - not again for a little while.
    if (fear && !one.afraid && now - (one.criedAt || 0) > 2500 && typeof playSoundCry === "function") {
      one.criedAt = now;
      playSoundCry(one.name, one.x - play.me.x, Math.hypot(one.x - play.me.x, one.y - play.me.y));
    }
    one.afraid = fear;
    if (fear) {                                       // away from the walker, quick
      const d = Math.hypot(one.x - play.me.x, one.y - play.me.y) || 1;
      one.tx = one.x + (one.x - play.me.x) / d * 4; one.ty = one.y + (one.y - play.me.y) / d * 4;
    } else if (Math.hypot(one.tx - one.x, one.ty - one.y) < 0.1 && Math.random() < 0.02) {
      const [hx, hy] = one.home || [one.x, one.y];           // a land's own keeps near where it was laid
      one.tx = (one.home ? hx : one.x) + (Math.random() - 0.5) * 5; one.ty = (one.home ? hy : one.y) + (Math.random() - 0.5) * 4;
    }
    const dx = one.tx - one.x, dy = one.ty - one.y, d = Math.hypot(dx, dy);
    const was = one.moving;
    one.moving = d > 0.05;
    if (one.moving) {
      const step = Math.min(d, (fear ? 3.2 : 0.9) * seconds);
      const nx = one.x + dx / d * step, ny = one.y + dy / d * step;
      if (one.swim || playActFree(Math.floor(nx), Math.floor(ny - 0.5))) { one.x = nx; one.y = ny; one.left = dx < 0; one.hop += seconds * (fear ? 14 : 7); }
      else { one.tx = one.x; one.ty = one.y; one.moving = false; }
    }
    // STOPPED: on its feet again, and drawn once so - not left in the air of its last hop.
    if (was && !one.moving) { one.hop = 0; play.critterLanded = true; }
  }
  // Birds: now and then a few across the sky.
  play.birds = (play.birds || []).filter((one) => now - one.at < one.ms);
  const region = play.region;
  if (play.birds.length === 0 && Math.random() < 0.004 && region && !["underwater", "space", "cavern"].includes(region.biome)) {
    // ACROSS THE WORLD, NOT THE SCREEN (the user, 2026-11-20: "飞鸟也是 ... 我移动view ... 也会跟着移"): where it sets
    // off and its height are places in the world, in tiles; the view moving leaves it flying where it was.
    const size = PLAY_TILE * play.scale, canvas = play.canvas;
    const left = play.camera.x / size, top = play.camera.y / size;
    const across = canvas.width / size, down = canvas.height / size;
    const fromLeft = Math.random() < 0.5, count = 2 + Math.floor(Math.random() * 2);
    play.birds.push({ at: now, ms: 9000, fromLeft, count, x: fromLeft ? left - 4 : left + across + 4,
                      y: top + (Math.random() * 0.6 + 0.1) * down, speed: (across + 8) / 9 });
  }
}
function playCrittersDraw(size, now) {
  const { ctx, canvas } = play;
  for (const one of play.critters || []) {
    if (!playActInView(one.x, one.y)) continue;
    const spec = PLAY_SCENERY[one.name], sheet = spec && play.sheets[spec[0]];
    if (!sheet) continue;
    const [sx, sy] = playActScreen(one.x, one.y);
    const up = one.swim ? Math.sin(one.hop) * size * 0.08 : Math.abs(Math.sin(one.hop)) * size * 0.18;
    ctx.save();
    ctx.fillStyle = "rgba(0, 0, 0, 0.18)";
    if (!one.swim) { ctx.beginPath(); ctx.ellipse(sx, sy, size * 0.28, size * 0.08, 0, 0, Math.PI * 2); ctx.fill(); }
    ctx.translate(sx, sy - up);
    if (one.left) ctx.scale(-1, 1);
    ctx.drawImage(sheet, spec[1] * 17, spec[2] * 17, 16, 16, -size * 0.4, -size * 0.8, size * 0.8, size * 0.8);
    ctx.restore();
  }
  for (const flock of play.birds || []) {
    const gone = (now - flock.at) / 1000 * (flock.speed || 10) * (flock.fromLeft ? 1 : -1);    // tiles flown
    const [fx, fy] = playActScreen((flock.x || 0) + gone, flock.y || 0);
    for (let i = 0; i < flock.count; i += 1) {
      const x = fx - i * size * 0.7 * (flock.fromLeft ? 1 : -1);
      const y = fy + Math.abs(i - flock.count / 2) * size * 0.4 + Math.sin(now / 300 + i) * size * 0.1;
      const flap = Math.sin(now / 90 + i) > 0;
      ctx.strokeStyle = "#2d2a33"; ctx.lineWidth = Math.max(1, size / 12);
      ctx.beginPath();
      ctx.moveTo(x - size * 0.25, y + (flap ? -size * 0.12 : size * 0.06)); ctx.lineTo(x, y); ctx.lineTo(x + size * 0.25, y + (flap ? -size * 0.12 : size * 0.06));
      ctx.stroke();
      ctx.fillStyle = "rgba(0, 0, 0, 0.1)";                 // its shadow, far below
      ctx.fillRect(x - size * 0.15, y + size * 2.2, size * 0.3, size * 0.08);
    }
  }
}

/* -- DRAWN EACH FRAME, under the walker (playActDraw) and over it (playActOver) ------------------------------------- */
function playActDraw(size) {
  if (!play.map) return;
  const now = performance.now();
  playLinesDraw(size, now);
  for (const one of playPortals()) if (playActInView(one.x, one.y, 3)) playPortalDraw(one, size, now);
  playCrittersDraw(size, now);
  // A line cast: from the walker to its float, which bobs - and dips when a fish bites.
  const fish = play.fish;
  if (fish) {
    const [bx, by] = playActScreen(fish.x, fish.y);
    const [mx, my] = playActScreen(play.me.x, play.me.y - 0.9);
    const biting = now >= fish.bite && now < fish.bite + PLAY_BITE_MS;
    if (now > fish.bite + PLAY_BITE_MS) { play.fish = null; playActSay(playWords("It got away!")); }
    const dip = biting ? size * 0.18 * Math.abs(Math.sin(now / 60)) : Math.sin(now / 400) * size * 0.05;
    play.ctx.strokeStyle = "rgba(255, 255, 255, 0.8)"; play.ctx.lineWidth = 1;
    play.ctx.beginPath(); play.ctx.moveTo(mx + size * 0.3, my); play.ctx.quadraticCurveTo((mx + bx) / 2, Math.min(my, by) - size, bx, by + dip); play.ctx.stroke();
    play.ctx.fillStyle = "#e5484d"; play.ctx.fillRect(bx - size * 0.08, by + dip - size * 0.08, size * 0.16, size * 0.16);
    play.ctx.strokeStyle = "rgba(255, 255, 255, 0.5)";
    play.ctx.beginPath(); play.ctx.ellipse(bx, by + size * 0.1, size * (0.2 + ((now / 900) % 1) * 0.3), size * 0.08, 0, 0, Math.PI * 2); play.ctx.stroke();
    if (biting) { play.ctx.fillStyle = "#fff"; play.ctx.font = `bold ${Math.round(size * 0.6)}px system-ui`; play.ctx.fillText("!", mx - size * 0.1, my - size * 0.9); }
  }
}
function playActOver(size) {
  const { ctx, canvas } = play;
  const now = performance.now();
  // A light put out or lit: a puff of smoke, or a spark.
  play.puffs = (play.puffs || []).filter((one) => now - one.at < 700);
  for (const one of play.puffs) {
    const k = (now - one.at) / 700, [sx, sy] = playActScreen(one.x, one.y);
    ctx.fillStyle = one.lit ? `rgba(255, 210, 120, ${(1 - k).toFixed(2)})` : `rgba(170, 170, 170, ${(0.8 * (1 - k)).toFixed(2)})`;
    for (let i = 0; i < 3; i += 1) {
      ctx.beginPath(); ctx.arc(sx + Math.sin(i * 2 + k * 3) * size * 0.15, sy - k * size * (0.8 + i * 0.3), size * (0.08 + k * 0.12), 0, Math.PI * 2); ctx.fill();
    }
  }
  // A fish caught: held up a moment.
  if (play.caught && now - play.caught.at < 1800) {
    const spec = PLAY_SCENERY[play.caught.name], sheet = spec && play.sheets[spec[0]];
    const [sx, sy] = playActScreen(play.me.x, play.me.y - 1.9);
    // Whole, as wide and tall as it is drawn (a koi two tiles long), at most a tile and a half across.
    const k = Math.min(0.9, 1.4 / Math.max(spec ? spec[3] : 1, spec ? spec[4] : 1));
    const w = (spec ? spec[3] : 1) * size * k, h = (spec ? spec[4] : 1) * size * k;
    const bob = Math.sin((now - play.caught.at) / 200) * size * 0.08;
    if (sheet) ctx.drawImage(sheet, spec[1] * 17, spec[2] * 17, spec[3] * 17 - 1, spec[4] * 17 - 1, sx - w / 2, sy - h / 2 - bob, w, h);
  }
  // Riding: the vehicle with the walker on it.
  if (play.ride && play.ride.line) {                         // (a great piece's ride: drawn with it, play.js)
    const ride = play.ride, line = ride.line, u = ride.u === undefined ? ride.start : ride.u, dir = ride.along < 0 ? -1 : 1;
    if (line.kind === "train") {                             // the walker in its engine, the cars behind on the rails
      playTrainDraw(line, Array.from({ length: PLAY_CARS }, (_x, k) => ({ u: u - dir * k * PLAY_CAR_GAP, dir,
        engine: k === 0 || (!line.loop && k === PLAY_CARS - 1) })), size);
      const [sx, sy] = playActScreen(play.me.x, play.me.y);
      playActMeOn(sx, sy - size * 0.3, size);
    } else playVehicleDraw(line.kind, play.me.x, play.me.y, playLineHeading(line, u, dir), size, true, line.look);
  }
  playActHintDraw(size);
  // A portal gone through: the screen white a moment.
  if (play.flash && now - play.flash < 450) {
    ctx.fillStyle = `rgba(255, 255, 255, ${(0.85 * (1 - (now - play.flash) / 450)).toFixed(2)})`;
    ctx.fillRect(0, 0, canvas.width, canvas.height);
  }
}

/* While any of it moves - a portal in sight, a vehicle, an animal, a bird, a line cast, a swim, a coaster - a frame now
   and then; nothing when the view is hidden. */
function playCoasterInView(zone) {
  const [fx, fy, fw, fh] = playFloorOf(zone);
  return playActInView(fx, fy - 8, 2) || playActInView(fx + fw, fy + fh, 2) || playActInView(fx, fy + fh, 2) || playActInView(fx + fw, fy - 8, 2)
    || (play.me.x > fx - 10 && play.me.x < fx + fw + 10 && play.me.y > fy - 14 && play.me.y < fy + fh + 10);
}
let playActLast = performance.now();
setInterval(() => {
  if (!play.on || !play.world || !play.map || document.hidden) return;
  const now = performance.now(), seconds = Math.min(0.2, (now - playActLast) / 1000);
  playActLast = now;
  playCrittersStep(seconds);
  if (play.sit) {                                         // sliding onto the seat, then sitting
    const k = Math.min(1, (now - play.sit.at) / 250);
    play.me.x = play.sit.fromX + (play.sit.x - play.sit.fromX) * k;
    play.me.y = play.sit.fromY + (play.sit.y - play.sit.fromY) * k;
  }
  if (playRunning || play.ride || !play.canvas) return;
  // ONLY WHAT MOVES IN SIGHT IS DRAWN AGAIN (the user, 2026-11-17: "是的，只有会动的东西才会重画"): a portal, a running
  // vehicle, an animal, a coaster in view; a flock passing; a line cast, a swim, a puff, a fish held up, a seat slid
  // onto. Nothing in sight moving: no frame at all.
  const moving = playPortals().some((one) => playActInView(one.x, one.y, 2))
    || playLines().some((line) => {
      const d = (now / 1000) * PLAY_LINE_SPEED[line.kind] * 0.35 + line.length * 0.37;
      if (line.kind !== "train") { const at = playLineAt(line, d); return playActInView(at[0], at[1], 6); }
      return playTrainCars(line, d).some((car) => { const [x, y] = playLinePoint(line, car.u); return playActInView(x, y, 3); });
    })
    || (play.critters || []).some((one) => one.moving && playActInView(one.x, one.y, 1)) || play.critterLanded
    || (play.birds || []).length || play.fish || play.swimming || (play.puffs || []).length
    || (play.caught && now - play.caught.at < 1800) || (play.sit && now - play.sit.at < 300)
    || play.world.zones.some((zone) => zone.kind === "roller_coaster" && playCoasterInView(zone))
    || (play.ride && play.ride.piece)
    || (play.world.items || []).some((one) => one.kind === "piece" && playPieceMoves(one) && playPieceInView(one));
  play.critterLanded = false;
  if (moving) playDraw();
}, 70);
