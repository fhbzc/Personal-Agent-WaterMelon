/* THE SKY OVER EACH REGION (the user, 2026-11-17: "这个地区要有阴晴圆缺，环境（环境可以是个概率函数，也可以是永夜，永冬什么
   的)").

   A region's weather is the agent's choice (agent/playregions.py WEATHERS): "changing" - fair, cloudy, rain, snow or fog,
   by its land's chances, new every three hours (the same for everyone at the same hour, from the place and the time) -
   or always one thing: fair, rainy, snowy, foggy; or an eternal night, winter, dusk or spring. The light follows the
   user's own clock - night, dawn, day, dusk - unless the region's is eternal; at night the real moon, its phase drawn in
   the corner, and a full moon's night less dark. Rain and snow fall outdoors only; inside a room the weather is only a
   little of the light through the windows. Weather makes no sound of its own: every sound comes from a thing. */

/* THE GAME'S OWN TIME (the user, 2026-11-17: "游戏里也是有时间的"): a game minute each real second - a day in 24 real
   minutes - the same for everyone at the same moment. Day and night by it; the weather by it. */
const PLAY_GAME_MINUTE_MS = 1000;
function playGameTime(now = Date.now()) {
  const minutes = Math.floor(now / PLAY_GAME_MINUTE_MS);
  return { day: Math.floor(minutes / 1440) % 1000 + 1, hour: (minutes % 1440) / 60, minutes };
}

/* Each land's chances for a changing sky: fair, cloudy, rain, snow, fog. */
const PLAY_SKY_CHANCES = {
  meadow: [0.55, 0.25, 0.15, 0, 0.05], autumn: [0.4, 0.3, 0.2, 0, 0.1], enchanted: [0.35, 0.2, 0.1, 0.05, 0.3],
  desert: [0.85, 0.1, 0.05, 0, 0], seaside: [0.5, 0.2, 0.15, 0, 0.15], forest: [0.4, 0.25, 0.25, 0, 0.1],
  highland: [0.4, 0.3, 0.1, 0.1, 0.1], snow: [0.3, 0.2, 0, 0.45, 0.05], city: [0.5, 0.3, 0.15, 0, 0.05],
  farmland: [0.6, 0.2, 0.15, 0, 0.05], cavern: [1, 0, 0, 0, 0], lakeside: [0.45, 0.2, 0.15, 0, 0.2],
  toytown: [0.8, 0.12, 0.08, 0, 0], underwater: [1, 0, 0, 0, 0], skycity: [0.85, 0.15, 0, 0, 0],
  castle: [0.45, 0.3, 0.15, 0, 0.1], chinese: [0.45, 0.15, 0.25, 0, 0.15],
  space: [1, 0, 0, 0, 0], scifi: [0.6, 0.2, 0.15, 0, 0.05], graveyard: [0.3, 0.5, 0.2, 0, 0],
  christmas: [0.4, 0.2, 0, 0.4, 0], newyear: [0.8, 0.15, 0.05, 0, 0], halloween: [0.35, 0.45, 0.2, 0, 0],
  valentine: [0.85, 0.1, 0.05, 0, 0], funfair: [0.8, 0.15, 0.05, 0, 0], botanical: [0.65, 0.15, 0.2, 0, 0],
  primeval: [0.35, 0.35, 0.3, 0, 0], zoo: [0.6, 0.2, 0.15, 0, 0.05], ruins: [0.6, 0.2, 0.05, 0, 0.15],
};
const PLAY_SKY_KINDS = ["fair", "cloudy", "rain", "snow", "fog"];
const PLAY_SKY_FIXED = { fair: "fair", rainy: "rain", snowy: "snow", foggy: "fog", eternal_winter: "snow" };

/* The weather of a region now: from its setting, or by its land's chances for these three hours. */
function playSkyWeather(region, now = Date.now()) {
  const setting = region.weather || "changing";
  if (PLAY_SKY_FIXED[setting]) return PLAY_SKY_FIXED[setting];
  if (setting.startsWith("eternal")) return "fair";
  // An overcast sky the rarer (the user: "少部分地区的阴天"): cloud, rain and fog at two fifths of the land's chances.
  const base = PLAY_SKY_CHANCES[region.biome] || PLAY_SKY_CHANCES.meadow;
  const dull = (base[1] + base[2] + base[4]) * 0.6;
  const chances = [base[0] + dull, base[1] * 0.4, base[2] * 0.4, base[3], base[4] * 0.4];
  const block = Math.floor(playGameTime(+now).minutes / 720);      // drawn again each half game day, by its chances
  const seed = [...String(region.id || "")].reduce((n, ch) => n * 31 + ch.charCodeAt(0), 7);
  let pick = playHash(block, seed, 53);
  for (let k = 0; k < chances.length; k += 1) { if (pick < chances[k]) return PLAY_SKY_KINDS[k]; pick -= chances[k]; }
  return "fair";
}

/* How light it is, by the user's clock: 0 deep night, 1 full day - and the warmth of dawn and dusk. */
function playSkyDaylight(region, now = Date.now()) {
  const setting = region.weather || "changing";
  if (setting === "eternal_night") return { light: 0, warm: 0 };
  if (setting === "eternal_dusk") return { light: 0.45, warm: 1 };
  if (setting === "eternal_spring") return { light: 1, warm: 0 };
  const hour = playGameTime(+now).hour;
  const up = (h, a, b) => Math.max(0, Math.min(1, (h - a) / (b - a)));
  const light = hour < 12 ? up(hour, 5, 8) : 1 - up(hour, 17.5, 20.5);
  const warm = Math.max(0, 1 - Math.abs(hour - 6.5) / 1.5, 1 - Math.abs(hour - 19) / 1.5);
  return { light, warm };
}

/* The moon's phase: 0 new, 0.5 full - by the game's days (no date in the game), a month of them. */
function playMoonPhase() {
  const days = playGameTime().minutes / 1440;
  return ((days % 29.530589) + 29.530589) % 29.530589 / 29.530589;
}

const playSky = { flakes: [], last: 0, timer: 0, kind: "fair" };

/* Laid over the view, after the ground and the walker: the light, the weather's wash, what falls, the moon. */
function playSkyDraw(ctx, canvas) {
  // Where the walker stands, even before its region is said - never a bright first look at night.
  const region = play.region || (play.map && playRegionAt(play.map, play.me.x, play.me.y));
  if (!region) return;
  const now = Date.now();
  const inside = Boolean(play.zone && !PLAY_OUTDOOR[play.zone.kind]);
  const weather = playSkyWeather(region, now);
  const { light, warm } = playSkyDaylight(region, now);
  const moon = playMoonPhase();
  ctx.save();
  // DAY AND NIGHT (the user: "白天晚上的景色要变，比如夜晚周围暗，但是有路灯什么的"): at night the world dark - less inside
  // a room - and lit where a lamp, a torch, a fire, a candle or a window is; the walker a little light of its own.
  // Dawn and dusk warmer.
  const night = (1 - light) * (inside ? 0.3 : 0.66);
  if (warm > 0.01 && light > 0.05) {
    ctx.fillStyle = `rgba(255, 140, 70, ${(0.12 * warm).toFixed(3)})`;
    ctx.fillRect(0, 0, canvas.width, canvas.height);
  }
  if (night > 0.02) playNightDraw(ctx, canvas, night);
  // NO DARKER OUTDOORS BY THE HOUR (the user, 2026-11-17: "我现在不是很喜欢你那个出了室外就变暗的设计，可以不要";
  // "除非是少部分地区的阴天之类"): day or night, inside or out, as light - only an overcast sky, rare, a little dimmer
  // outdoors. The hour and the moon are said in the status bar; the moon drawn at night.
  const wash = { cloudy: [90, 100, 115, 0.1], rain: [60, 75, 100, 0.14], fog: [225, 230, 235, 0.28] }[weather];
  if (wash && !inside) { ctx.fillStyle = `rgba(${wash[0]}, ${wash[1]}, ${wash[2]}, ${wash[3].toFixed(3)})`; ctx.fillRect(0, 0, canvas.width, canvas.height); }
  // What falls, outdoors.
  const falling = !inside && (weather === "rain" || weather === "snow");
  if (falling) {
    const t = performance.now();
    const step = Math.min(0.1, (t - (playSky.last || t)) / 1000);
    playSky.last = t;
    const want = weather === "rain" ? 90 : 70;
    if (playSky.kind !== weather) { playSky.flakes = []; playSky.kind = weather; }
    while (playSky.flakes.length < want) playSky.flakes.push({ x: Math.random(), y: Math.random(), s: 0.6 + Math.random() * 0.8 });
    const u = Math.max(2, Math.round(play.scale * 0.7));
    for (const f of playSky.flakes) {
      if (weather === "rain") {
        f.y += step * 1.6 * f.s; f.x += step * 0.25 * f.s;
        ctx.fillStyle = "rgba(190, 210, 240, 0.55)";
        ctx.fillRect(Math.round(f.x * canvas.width), Math.round(f.y * canvas.height), Math.max(1, u / 2), u * 3);
      } else {
        f.y += step * 0.22 * f.s; f.x += step * 0.06 * Math.sin(t / 700 + f.s * 9);
        ctx.fillStyle = "rgba(255, 255, 255, 0.9)";
        ctx.fillRect(Math.round(f.x * canvas.width), Math.round(f.y * canvas.height), u, u);
      }
      if (f.y > 1) { f.y -= 1; f.x = Math.random(); }
      if (f.x > 1) f.x -= 1;
    }
  } else playSky.last = 0;
  // The moon at night, top left: its lit part as the real one's tonight.
  if (light < 0.35 && !inside) playMoonDraw(ctx, moon, 1 - light / 0.35);
  ctx.restore();
}

/* WHAT GIVES LIGHT AT NIGHT, and how far: things in the rooms, what stands about, windows (a warm glow below them). */
const PLAY_ITEM_LIGHTS = { lamp: 4.5, lamp_post: 5.5, wall_torch: 4, candelabra: 3.5, candle: 2.2, fireplace: 4.5, stove: 2.5,
                           window: 2.5 };
const PLAY_DECO_LIGHTS = { lamp: 5.5, torch: 4.5, campfire: 5.5, streetLamp: 6, gasLamp: 5.5, torchPost: 4.5, brazier: 5, stoneLantern: 4.5 };
function playNightDraw(ctx, canvas, night) {
  const size = PLAY_TILE * play.scale;
  const off = playSky.night || (playSky.night = document.createElement("canvas"));
  if (off.width !== canvas.width || off.height !== canvas.height) { off.width = canvas.width; off.height = canvas.height; }
  const c = off.getContext("2d", PLAY_OFF_PAGE);
  c.globalCompositeOperation = "source-over";
  c.clearRect(0, 0, off.width, off.height);
  c.fillStyle = `rgba(8, 12, 36, ${night.toFixed(3)})`;
  c.fillRect(0, 0, off.width, off.height);
  const lights = [[play.me.x, play.me.y - 0.4, 2.6]];
  const view = [play.camera.x / size - 8, play.camera.y / size - 8, (play.camera.x + canvas.width) / size + 8, (play.camera.y + canvas.height) / size + 8];
  const inView = (x, y) => x > view[0] && x < view[2] && y > view[1] && y < view[3];
  // EACH LAND'S NIGHT, AS ITS ART DIRECTOR SAID (agent/playworld.py _aesthetics; the user, 2026-11-20: "不同地形的灯/照明的
  // 样子，照明的亮度，扩散范围，也不一样。晚上要形成比较好的夜景"): its lights' colour, brightness and reach; how many of
  // its windows glow.
  const nightOf = (regionId) => ((play.world.regions || []).find((r) => r.id === regionId) || {}).night || null;
  const zoneRegion = new Map((play.world.zones || []).map((z) => [z.id, z.region]));
  for (const item of play.world.items) {
    let reach = PLAY_ITEM_LIGHTS[item.kind];
    if (!reach || !inView(item.x, item.y)) continue;
    if (play.lightsOff && play.lightsOff.has(`i:${item.id}`)) continue;          // put out (play-act.js)
    const own = nightOf(zoneRegion.get(item.zone));
    if (own && item.kind === "window" && playHash(item.x, item.y, 95) > (own.windows ?? 1)) continue;
    if (own && own.reach) reach *= own.reach / 5;
    const [iw, ih] = playItemSize(item);
    lights.push([item.x + iw / 2, item.y + (item.kind === "window" ? 1.6 : ih / 2), reach * Math.max(1, Number(item.scale) || 1), own]);
  }
  for (const zone of play.world.zones) {
    if (zone.kind === "volcano" && inView(zone.x + zone.w / 2, zone.y + zone.h / 2)) lights.push([zone.x + zone.w / 2, zone.y + 1 + zone.h / 2, 7]);
  }
  // ONLY WHAT IS LAID ALREADY (the user, 2026-11-20: "音乐在view最大的时候一卡一卡的"): the lamps standing about were read
  // from every block in sight, each laid for it - far out, the whole world's, 3 to 4 s at a time, again and again as
  // the night was drawn. Those laid (close up, every block in sight is) give their lamps; far out, a lamp is a speck.
  const chunkTiles = PLAY_CHUNK, laid = (play.map && play.map.laid) || new Map();
  for (let cy = Math.floor(view[1] / chunkTiles); cy <= Math.floor(view[3] / chunkTiles); cy += 1) {
    for (let cx = Math.floor(view[0] / chunkTiles); cx <= Math.floor(view[2] / chunkTiles); cx += 1) {
      const block = laid.get(`${cx},${cy}`);
      if (!block) continue;
      for (const one of block.deco) {
        const reach = PLAY_DECO_LIGHTS[one.name];
        if (reach && inView(one.x, one.y) && Math.floor(one.x / chunkTiles) === cx && Math.floor(one.y / chunkTiles) === cy
            && !(play.lightsOff && play.lightsOff.has(`d:${one.x},${one.y}`))) {
          const own = (one.region && one.region.night) || null;
          lights.push([one.x + 0.5, one.y + 0.3, own && own.reach ? own.reach : reach, own]);
        }
      }
    }
  }
  // Each light a hole in the dark, soft at its edge.
  c.globalCompositeOperation = "destination-out";
  for (const [x, y, reach, own] of lights) {
    const px = x * size - play.camera.x, py = y * size - play.camera.y, r = reach * size;
    const g = c.createRadialGradient(px, py, 0, px, py, r);
    const strength = own && own.brightness ? 0.55 + 0.45 * own.brightness : 1;
    g.addColorStop(0, `rgba(0, 0, 0, ${strength.toFixed(2)})`);
    g.addColorStop(0.55, `rgba(0, 0, 0, ${(0.7 * strength).toFixed(2)})`);
    g.addColorStop(1, "rgba(0, 0, 0, 0)");
    c.fillStyle = g;
    c.fillRect(px - r, py - r, 2 * r, 2 * r);
  }
  ctx.drawImage(off, 0, 0);
  // And a warm glow where it is lit (not round the walker).
  ctx.globalCompositeOperation = "lighter";
  for (const [x, y, reach, own] of lights.slice(1)) {
    const px = x * size - play.camera.x, py = y * size - play.camera.y, r = reach * size * 0.8;
    const g = ctx.createRadialGradient(px, py, 0, px, py, r);
    const hex = own && /^#[0-9a-f]{6}$/i.test(own.colour || "") ? own.colour : "#ffbe6e";
    const [cr, cg, cb] = [1, 3, 5].map((k) => parseInt(hex.slice(k, k + 2), 16));
    const glow = 0.12 * night * (own && own.brightness ? 0.6 + own.brightness : 1);
    g.addColorStop(0, `rgba(${cr}, ${cg}, ${cb}, ${glow.toFixed(3)})`);
    g.addColorStop(1, `rgba(${cr}, ${cg}, ${cb}, 0)`);
    ctx.fillStyle = g;
    ctx.fillRect(px - r, py - r, 2 * r, 2 * r);
  }
  ctx.globalCompositeOperation = "source-over";
}

/* A pixel moon: a disc, its dark part shaped by the phase (waxing lit on the right, waning on the left). */
function playMoonDraw(ctx, phase, strength) {
  const r = Math.max(10, Math.round(play.scale * 5));                  // under the corner's buttons, on the right
  const cx = play.canvas.width - r - 18, cy = Math.round(play.scale * 14) + r;   // below the corner's buttons
  const u = Math.max(1, Math.round(play.scale * 0.6));
  const lit = Math.cos(phase * 2 * Math.PI);                // 1 new, -1 full
  ctx.globalAlpha = Math.min(1, strength);
  for (let y = -r; y <= r; y += u) {
    for (let x = -r; x <= r; x += u) {
      if (x * x + y * y > r * r) continue;
      const edge = Math.sqrt(Math.max(0, r * r - y * y));
      const terminator = edge * lit;
      const litHere = phase < 0.5 ? x > terminator : x < -terminator;
      ctx.fillStyle = litHere ? "#f4f0d8" : "rgba(60, 66, 92, 0.85)";
      ctx.fillRect(cx + x, cy + y, u, u);
    }
  }
  ctx.globalAlpha = 1;
}

/* What falls keeps falling while nothing else moves - a light, slow loop, only while it rains or snows outdoors. */
function playSkyTick() {
  if (!play.on || !play.region) return;
  if (!playRunning && Date.now() - (playSky.drawnAt || 0) > 5000) { playSky.drawnAt = Date.now(); playDraw(); return; }
  const inside = Boolean(play.zone && !PLAY_OUTDOOR[play.zone.kind]);
  const weather = playSkyWeather(play.region);
  if (!inside && (weather === "rain" || weather === "snow") && !playRunning) playDraw();
}
setInterval(playSkyTick, 66);
