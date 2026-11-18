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
  const xs = map.hearts.map((one) => one[0]), ys = map.hearts.map((one) => one[1]);
  const far = Math.max(...xs) - Math.min(...xs) > PLAY_PORTAL_FAR || Math.max(...ys) - Math.min(...ys) > PLAY_PORTAL_FAR;
  const apart = map.regions.some((one) => one.apart);
  map.portals = spots.length >= 2 && (far || apart) ? spots : [];
  return map.portals;
}
function playPortalDraw(one, size, now) {
  const { ctx } = play;
  const [sx, sy] = playActScreen(one.x + 0.5, one.y + 0.9);
  const t = now / 1000;
  ctx.save();
  if (one.look === "light") {
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
const PLAY_LINE_LOOK = { train: "trainStation", ferry: "pier", airship: "skyDock", plane: "helipad" };
const PLAY_LINE_SPEED = { train: 9, ferry: 5, airship: 6, plane: 22 };      // tiles a second, ridden
const PLAY_LINE_WORDS = { train: "Train", ferry: "Ferry", airship: "Airship", plane: "Plane" };
function playLines() {
  const map = play.map;
  if (!map) return [];
  if (map.lines) return map.lines;
  const lines = [];
  const lands = map.regions.map((region, n) => ({ region, n, heart: map.hearts[n] }))
    .filter((one) => !one.region.apart && (map.zones.some((z) => z.region === one.region) || map.landmarks.some((l) => l.region === one.region)));
  const stop = (land, kind) => {
    const off = { train: [-5, 1], ferry: [0, 4], airship: [5, -2], plane: [-6, -4] }[kind];
    const at = playActSpotNear(land.heart[0] + off[0], land.heart[1] + off[1]);
    return at && { x: at[0], y: at[1], name: playWords(land.region.name || "") || land.region.biome, region: land.region };
  };
  // The train: round the mainland's lands, by their angle about the middle - a loop that turns for ever.
  const ground = lands.filter((one) => !["skycity", "underwater", "space"].includes(one.region.biome));
  if (ground.length >= 2) {
    const cx = ground.reduce((s, one) => s + one.heart[0], 0) / ground.length, cy = ground.reduce((s, one) => s + one.heart[1], 0) / ground.length;
    ground.sort((a, b) => Math.atan2(a.heart[1] - cy, a.heart[0] - cx) - Math.atan2(b.heart[1] - cy, b.heart[0] - cx));
    const stops = ground.map((one) => stop(one, "train")).filter(Boolean);
    if (stops.length >= 2) lines.push({ kind: "train", stops, loop: stops.length >= 3 });
  }
  // The ferry: between the lands by the water.
  const wet = lands.filter((one) => PLAY_WATERY.has(one.region.biome) || one.region.layout === "islands");
  if (wet.length >= 2) {
    const stops = wet.map((one) => stop(one, "ferry")).filter(Boolean);
    if (stops.length >= 2) lines.push({ kind: "ferry", stops, loop: false });
  }
  // The airship: up to each city in the sky, from the land nearest it.
  for (const sky of lands.filter((one) => one.region.biome === "skycity")) {
    const from = lands.filter((one) => one !== sky).sort((a, b) => Math.hypot(a.heart[0] - sky.heart[0], a.heart[1] - sky.heart[1])
      - Math.hypot(b.heart[0] - sky.heart[0], b.heart[1] - sky.heart[1]))[0];
    const stops = [from && stop(from, "airship"), stop(sky, "airship")].filter(Boolean);
    if (stops.length === 2) lines.push({ kind: "airship", stops, loop: false });
  }
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
  // Each line's way: from stop to stop (round to the first again, on a loop), and how long it is. THE TRAIN'S ON THE
  // GROUND (the user, 2026-11-17: "火车轨道 ... 从房子和地面上方直接跨过去，看着比较粗糙"): round the buildings, by the way
  // the roads are found; over water and the dark between lands, on a viaduct.
  for (const line of lines) {
    const ends = [...line.stops, ...(line.loop ? [line.stops[0]] : [])];
    let pts = ends.map((one) => [one.x + 0.5, one.y + 0.5]);
    if (line.kind === "train" && map.blocked) {
      pts = [];
      for (let i = 1; i < ends.length; i += 1) {
        let route = [];
        try { route = playRoute(map.blocked, [ends[i - 1].x, ends[i - 1].y], [ends[i].x, ends[i].y], 0, 211 + i, new Map()) || []; } catch (_err) { route = []; }
        const leg = route.length ? route.map(([x, y]) => [x + 0.5, y + 0.5]) : [[ends[i - 1].x + 0.5, ends[i - 1].y + 0.5], [ends[i].x + 0.5, ends[i].y + 0.5]];
        pts.push(...(pts.length ? leg.slice(1) : leg));
      }
      line.bridge = pts.map(([x, y]) => { try { const [b, i] = playCell(map, Math.floor(x), Math.floor(y)); return Boolean(b.none[i]) || playActWater(x, y); } catch (_err) { return false; } });
    }
    const cum = [0];
    for (let i = 1; i < pts.length; i += 1) cum.push(cum[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
    line.pts = pts; line.cum = cum; line.length = cum[cum.length - 1];
    line.stopAt = line.stops.map((_one, i) => cum[i]);
  }
  map.lines = lines;
  return lines;
}
function playLineAt(line, d) {
  // A point d tiles along the line (round and round on a loop; back and forth on a line), and its heading.
  const L = line.length || 1;
  let u = line.loop ? ((d % L) + L) % L : (() => { const m = ((d % (2 * L)) + 2 * L) % (2 * L); return m > L ? 2 * L - m : m; })();
  let i = 1;
  while (i < line.cum.length - 1 && line.cum[i] < u) i += 1;
  const a = line.pts[i - 1], b = line.pts[i], seg = (line.cum[i] - line.cum[i - 1]) || 1, k = (u - line.cum[i - 1]) / seg;
  return [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, Math.atan2(b[1] - a[1], b[0] - a[0])];
}
function playVehicleDraw(kind, x, y, heading, size, riding) {
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
    if (kind === "plane") {
      ctx.save(); ctx.translate(sx, sy - lift); ctx.rotate(heading + Math.PI / 2);
      const spec = PLAY_SCENERY.airplane, sheet = spec && play.sheets[spec[0]];
      if (sheet) ctx.drawImage(sheet, spec[1] * 17, spec[2] * 17, spec[3] * 17 - 1, spec[4] * 17 - 1, -size * 1.5, -size * 1.5, size * 3, size * 3);
      ctx.restore();
    } else draw("airship", 0, -lift + Math.sin(performance.now() / 600) * size * 0.1, size * 3, size * 2, !left);
  } else if (kind === "ferry") {
    draw("ferry", 0, -size * 0.6 + Math.sin(performance.now() / 500) * size * 0.06, size * 3, size * 2, left);
  } else {
    // The train: its engine and three carriages, one behind another along its heading.
    for (let k = 3; k >= 0; k -= 1) {
      const back = k * size * 2.05;
      ctx.save();
      ctx.translate(sx - Math.cos(heading) * back, sy - Math.sin(heading) * back - size * 0.5);
      // Turned the way it runs; going left, the right way up.
      if (left) { ctx.rotate(heading - Math.PI); ctx.scale(-1, 1); } else ctx.rotate(heading);
      const spec = PLAY_SCENERY[k ? "trainCarriage" : "trainEngine"], sheet = spec && play.sheets[spec[0]];
      if (sheet) ctx.drawImage(sheet, spec[1] * 17, spec[2] * 17, spec[3] * 17 - 1, spec[4] * 17 - 1, -size, -size / 2, size * 2, size);
      ctx.restore();
    }
  }
  if (riding) playActMeOn(sx, sy - (kind === "plane" || kind === "airship" ? size * 2.6 : size * 0.9), size);
}
function playActMeOn(sx, sy, size) {
  const sheet = play.sheets.me;
  if (sheet) play.ctx.drawImage(sheet, 0, (PLAY_ME_ROWS.down || 0) * 16, 16, 16, sx - size * 0.4, sy - size * 0.8, size * 0.8, size * 0.8);
}
function playLinesDraw(size, now) {
  const { ctx } = play;
  for (const line of playLines()) {
    if (line.kind === "train") {
      // Its track: gravel, sleepers, two rails - on the ground; over water or the dark, a viaduct on its piers.
      ctx.save();
      const n = line.pts.length;
      for (let i = 1; i < n; i += 1) {
        const [ax, ay] = line.pts[i - 1], [bx, by] = line.pts[i];
        if (!playActInView(ax, ay, 2) && !playActInView(bx, by, 2)) continue;
        const [sa, ta] = playActScreen(ax, ay), [sb, tb] = playActScreen(bx, by);
        const bridge = line.bridge && (line.bridge[i] || line.bridge[i - 1]);
        if (bridge) {                                        // a pier down from it, its deck's shadow
          ctx.fillStyle = "#7d7368"; ctx.fillRect(sb - size * 0.12, tb, size * 0.24, size * 0.9);
          ctx.fillStyle = "rgba(0, 0, 0, 0.2)"; ctx.fillRect(Math.min(sa, sb) - size * 0.3, Math.max(ta, tb) + size * 0.9, Math.abs(sb - sa) + size * 0.6, size * 0.15);
        }
        ctx.strokeStyle = bridge ? "#8c8274" : "#8a7b6a"; ctx.lineWidth = size * (bridge ? 0.62 : 0.5); ctx.lineCap = "round";
        ctx.beginPath(); ctx.moveTo(sa, ta); ctx.lineTo(sb, tb); ctx.stroke();
        const dx = sb - sa, dy = tb - ta, d = Math.hypot(dx, dy) || 1, px = -dy / d, py = dx / d;
        ctx.strokeStyle = "#5b3a24"; ctx.lineWidth = Math.max(1, size * 0.1);
        ctx.beginPath(); ctx.moveTo((sa + sb) / 2 - px * size * 0.22, (ta + tb) / 2 - py * size * 0.22); ctx.lineTo((sa + sb) / 2 + px * size * 0.22, (ta + tb) / 2 + py * size * 0.22); ctx.stroke();
        ctx.strokeStyle = "#c3c9d3"; ctx.lineWidth = Math.max(1, size * 0.06);
        for (const off of [-0.14, 0.14]) {
          ctx.beginPath(); ctx.moveTo(sa + px * size * off, ta + py * size * off); ctx.lineTo(sb + px * size * off, tb + py * size * off); ctx.stroke();
        }
      }
      ctx.restore();
    }
    for (const one of line.stops) {
      if (!playActInView(one.x, one.y, 4)) continue;
      const spec = PLAY_SCENERY[PLAY_LINE_LOOK[line.kind]], sheet = spec && play.sheets[spec[0]];
      if (!sheet) continue;
      const [sx, sy] = playActScreen(one.x + 0.5, one.y + 1);
      const w = spec[3] * size, h = spec[4] * size;
      ctx.drawImage(sheet, spec[1] * 17, spec[2] * 17, spec[3] * 17 - 1, spec[4] * 17 - 1, sx - w / 2, sy - h, w, h);
    }
    // Its vehicle, running when no one rides it.
    if (play.ride && play.ride.line === line) continue;
    const at = playLineAt(line, (now / 1000) * PLAY_LINE_SPEED[line.kind] * 0.35 + line.length * 0.37);
    if (playActInView(at[0], at[1], 8)) playVehicleDraw(line.kind, at[0], at[1], at[2], size, false);
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
  playSfx("door-close", 0.3);
  const tick = () => {
    const ride = play.ride;
    if (!ride || !play.on) return;
    const k = Math.min(1, (performance.now() - ride.at) / 1000 / ride.seconds);
    const eased = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
    const [x, y] = playLineAt(ride.line, ride.start + ride.along * eased);
    play.me.x = x; play.me.y = y;
    playDraw();
    if (k < 1) { requestAnimationFrame(tick); return; }
    play.ride = null;
    play.me.x = ride.to.x + 0.5; play.me.y = ride.to.y + 1.8;
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
const PLAY_CATCH = ["clownfish", "butterflyfish2", "yellowTang", "blueTang", "moorishIdol", "angelfish", "parrotfish", "koi",
                    "wrasse", "redSnapper", "triggerfish", "boxfish", "grouper", "fish1", "fish2", "fish3", "fish4"];
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
  if (now >= fish.bite && now < fish.bite + 1100) {
    const names = PLAY_CATCH.filter((one) => PLAY_SCENERY[one]);
    const name = names[Math.floor(Math.random() * names.length)];
    play.caught = { name, at: now };
    let log = {};
    try { log = JSON.parse(localStorage.getItem("playFishCaught") || "{}"); } catch (_err) { log = {}; }
    log[name] = (log[name] || 0) + 1;
    try { localStorage.setItem("playFishCaught", JSON.stringify(log)); } catch (_err) { /* this time */ }
    const total = Object.values(log).reduce((s, n) => s + n, 0);
    playActSay(`${playWords("Caught")}: ${String(name).replace(/([A-Z0-9])/g, " $1").toLowerCase()}! (${total})`);
    playSoundChime();
  } else {
    playActSay(playWords(now < fish.bite ? "Reeled in - nothing yet." : "It got away!"));
  }
  return true;
}
function playActSay(text) {
  play.said = { text, at: performance.now(), until: performance.now() + playSayFor(text), x: play.me.x, y: play.me.y };
}

/* -- E: what is near done - the rest looked at (play.js playLook) ------------------------------------------------- */
function playAct() {
  if (play.ride) return true;
  const tx = play.me.x, ty = play.me.y - 0.3;
  if (play.fish) return playFishPress();
  // A portal stood in: where it goes.
  const portal = playPortals().find((one) => Math.hypot(one.x + 0.5 - tx, one.y + 0.7 - ty) < 1.1);
  if (portal) {
    playMenuOpen(playWords("Portal"), playPortals().filter((one) => one !== portal).map((one) => ({
      label: one.name, go: () => playTeleport(one.x, one.y) })));
    return true;
  }
  // A stop: its line's other stops.
  for (const line of playLines()) {
    const here = line.stops.findIndex((one) => Math.hypot(one.x + 0.5 - tx, one.y + 0.5 - ty) < 1.8);
    if (here < 0) continue;
    playMenuOpen(`${playWords(PLAY_LINE_WORDS[line.kind])}`, line.stops.map((one, n) => ({ one, n })).filter(({ n }) => n !== here)
      .map(({ one, n }) => ({ label: one.name, go: () => playRide(line, here, n) })));
    return true;
  }
  const near = play.near;
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
/* What near is shown on the card: a portal or a stop. */
function playActNear(tx, ty) {
  const portal = playPortals().find((one) => Math.hypot(one.x + 0.5 - tx, one.y + 0.7 - ty) < 1.1);
  if (portal) return { at: [portal.x + 0.5, portal.y + 0.9], portal, name: playWords("Portal") };
  for (const line of playLines()) {
    const one = line.stops.find((s) => Math.hypot(s.x + 0.5 - tx, s.y + 0.5 - ty) < 1.8);
    if (one) return { at: [one.x + 0.5, one.y + 0.9], stop: one, line, name: playWords(PLAY_LINE_WORDS[line.kind]) };
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
const PLAY_CRITTERS_MOST = 6;
function playCrittersStep(seconds) {
  if (!play.map) return;
  play.critters = play.critters || [];
  const now = performance.now();
  // Those far from the walker let go; new ones where there is room, of the land's kinds.
  play.critters = play.critters.filter((one) => Math.hypot(one.x - play.me.x, one.y - play.me.y) < 18);
  if (play.critters.length < PLAY_CRITTERS_MOST && Math.random() < 0.05) {
    const a = Math.random() * Math.PI * 2, d = 6 + Math.random() * 8;
    const x = Math.floor(play.me.x + Math.cos(a) * d), y = Math.floor(play.me.y + Math.sin(a) * d);
    const region = playRegionAt(play.map, x, y);
    const kinds = region && PLAY_CRITTERS[region.biome];
    const swim = region && region.biome === "underwater";
    if (kinds && (swim || playActFree(x, y))) {
      const name = kinds[Math.floor(Math.random() * kinds.length)];
      if (PLAY_SCENERY[name]) play.critters.push({ name, x: x + 0.5, y: y + 0.8, tx: x + 0.5, ty: y + 0.8, hop: Math.random() * 6, swim, left: false });
    }
  }
  for (const one of play.critters) {
    const fear = Math.hypot(one.x - play.me.x, one.y - play.me.y) < 2.2;
    if (fear) {                                       // away from the walker, quick
      const d = Math.hypot(one.x - play.me.x, one.y - play.me.y) || 1;
      one.tx = one.x + (one.x - play.me.x) / d * 4; one.ty = one.y + (one.y - play.me.y) / d * 4;
    } else if (Math.hypot(one.tx - one.x, one.ty - one.y) < 0.1 && Math.random() < 0.02) {
      one.tx = one.x + (Math.random() - 0.5) * 5; one.ty = one.y + (Math.random() - 0.5) * 4;
    }
    const dx = one.tx - one.x, dy = one.ty - one.y, d = Math.hypot(dx, dy);
    if (d > 0.05) {
      const step = Math.min(d, (fear ? 3.2 : 0.9) * seconds);
      const nx = one.x + dx / d * step, ny = one.y + dy / d * step;
      if (one.swim || playActFree(Math.floor(nx), Math.floor(ny - 0.5))) { one.x = nx; one.y = ny; one.left = dx < 0; one.hop += seconds * (fear ? 14 : 7); }
      else { one.tx = one.x; one.ty = one.y; }
    }
  }
  // Birds: now and then a few across the sky.
  play.birds = (play.birds || []).filter((one) => now - one.at < one.ms);
  const region = play.region;
  if (play.birds.length === 0 && Math.random() < 0.004 && region && !["underwater", "space", "cavern"].includes(region.biome)) {
    const fromLeft = Math.random() < 0.5, count = 3 + Math.floor(Math.random() * 4);
    play.birds.push({ at: now, ms: 9000, fromLeft, y: Math.random() * 0.6 + 0.1, count });
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
    const k = (now - flock.at) / flock.ms;
    for (let i = 0; i < flock.count; i += 1) {
      const x = (flock.fromLeft ? k : 1 - k) * (canvas.width + 200) - 100 - i * size * 0.7 * (flock.fromLeft ? 1 : -1);
      const y = flock.y * canvas.height + Math.abs(i - flock.count / 2) * size * 0.4 + Math.sin(now / 300 + i) * size * 0.1;
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
    const biting = now >= fish.bite && now < fish.bite + 1100;
    if (now > fish.bite + 1100) { play.fish = null; playActSay(playWords("It got away!")); }
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
    if (sheet) ctx.drawImage(sheet, spec[1] * 17, spec[2] * 17, 16, 16, sx - size * 0.45, sy - size * 0.45 - Math.sin((now - play.caught.at) / 200) * size * 0.08, size * 0.9, size * 0.9);
  }
  // Riding: the vehicle with the walker on it.
  if (play.ride) {
    const at = playLineAt(play.ride.line, play.ride.start + play.ride.along * Math.min(1, (now - play.ride.at) / 1000 / play.ride.seconds));
    playVehicleDraw(play.ride.line.kind, play.me.x, play.me.y, at[2], size, true);
  }
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
    || playLines().some((line) => { const at = playLineAt(line, (now / 1000) * PLAY_LINE_SPEED[line.kind] * 0.35 + line.length * 0.37); return playActInView(at[0], at[1], 6); })
    || (play.critters || []).some((one) => playActInView(one.x, one.y, 1))
    || (play.birds || []).length || play.fish || play.swimming || (play.puffs || []).length
    || (play.caught && now - play.caught.at < 1800) || (play.sit && now - play.sit.at < 300)
    || play.world.zones.some((zone) => zone.kind === "roller_coaster" && playCoasterInView(zone));
  if (moving) playDraw();
}, 70);
