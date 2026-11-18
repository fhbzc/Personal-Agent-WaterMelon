/* THE PLAY VIEW'S SOUND (the user, 2026-11-17: "可以带音乐，交互声音，走路声音之类，游戏里有设置调整音量"; "不同场景可以是不同
   音效（这个音效可以ai 生成吗，不能的话ai根据场景自己选吧)").

   All of it made here, by the browser's own synthesis - no recordings. THE TUNE is the place's: each room's as the
   agent wrote it with the room (agent/playworld.py "music"), else its kind's own; outdoors its region's. EVERY OTHER
   SOUND COMES FROM A THING THAT MAKES IT (the user: "每个其它的声音（流水，壁炉）都是挂靠在item上的，这个item有这个声音才会
   听到"): a fireplace's crackle, water's murmur, a bird in a tree, a market's voices - louder the nearer, little through a
   wall. A footstep by what is underfoot; a chime on entering a region; a hop. Volumes - all, music, sounds - and quiet,
   set in the world's corner and kept. Started only with the view (a press of the user's: a page may make no sound
   before one), stopped when it shuts or is hidden. */

const PLAY_SOUND_KEY = "playSound";
const PLAY_NOTE = /^([A-G])([#b]?)([1-7])$/;
const PLAY_SEMITONES = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };
/* Each kind's own tune - what a room plays when the agent wrote none. */
/* Each kind's own tune - what a place plays when the agent wrote none: its lead instrument, what accompanies it and
   how (solo, arpeggio, pad, waltz, march), its mode. A kind of land's tune by the same names (play-regions.js "tune"). */
const PLAY_TUNES = {
  town: { tempo: 96, instrument: "guitar", accompaniment: "harp", style: "arpeggio", mode: "major",
          melody: "E5 G5 A5 G5 E5 D5 C5 D5 E5 - G5 E5 D5 - C5 -", bass: "C3 A2 F2 G2" },
  home: { tempo: 76, instrument: "musicbox", accompaniment: "harp", style: "arpeggio", mode: "major",
          melody: "C5 E5 G5 E5 F5 A5 G5 - E5 D5 C5 D5 E5 - - -", bass: "C3 F2 G2 C3" },
  study: { tempo: 66, instrument: "strings", accompaniment: "strings", style: "pad", mode: "minor",
           melody: "A4 - C5 E5 D5 - C5 B4 A4 - - E5 D5 C5 A4 -", bass: "A2 F2 C3 E2" },
  library: { tempo: 62, instrument: "piano", accompaniment: "piano", style: "arpeggio", mode: "minor",
             melody: "D5 F5 A5 - G5 F5 E5 - D5 - A4 - C5 D5 - -", bass: "D3 A2 Bb2 A2" },
  screening: { tempo: 84, instrument: "strings", accompaniment: "pad", style: "pad", mode: "major",
               melody: "G4 B4 D5 G5 F#5 D5 B4 - C5 E5 G5 - F#5 - D5 -", bass: "G2 E2 C3 D3" },
  cafe: { tempo: 100, instrument: "marimba", accompaniment: "guitar", style: "waltz", mode: "major",
          melody: "F5 A5 C6 A5 G5 E5 C5 - D5 F5 A5 - G5 - E5 -", bass: "F2 D3 Bb2 C3" },
  studio: { tempo: 96, instrument: "piano", accompaniment: "strings", style: "pad", mode: "major",
            melody: "C5 D5 E5 G5 A5 G5 E5 D5 C5 - E5 - G5 - - -", bass: "C3 A2 D3 G2" },
  station: { tempo: 116, instrument: "marimba", accompaniment: "piano", style: "march", mode: "major",
             melody: "E5 E5 G5 - E5 D5 C5 - D5 D5 E5 - D5 - C5 -", bass: "C3 G2 F2 G2" },
  garden: { tempo: 80, instrument: "flute", accompaniment: "harp", style: "arpeggio", mode: "major",
            melody: "G5 E5 C5 E5 G5 A5 G5 - E5 G5 A5 - C6 - A5 -", bass: "C3 E3 F2 G2" },
  snow: { tempo: 70, instrument: "celesta", accompaniment: "pad", style: "pad", mode: "major",
          melody: "E6 - B5 - G5 - B5 C6 D6 - B5 - G5 - - -", bass: "E3 C3 G2 D3" },
  cavern: { tempo: 58, instrument: "organ", accompaniment: "organ", style: "pad", mode: "minor",
            melody: "A4 - - C5 B4 - A4 - E4 - - G4 A4 - - -", bass: "A2 F2 D2 E2" },
  city: { tempo: 92, instrument: "piano", accompaniment: "strings", style: "pad", mode: "major",
          melody: "E5 - D5 C5 D5 E5 G5 - A5 G5 E5 - D5 - C5 -", bass: "C3 G2 A2 F2" },
};
/* RECORDED MUSIC (the user, 2026-11-17: "你的音乐也可以自己去网上搜索"; CC0 tracks, play/ASSETS.txt): a place has one
   only where the agent chose it as the right fit (agent/playworld.py TRACKS) - else its own written tune; looped,
   faded across. */
const PLAY_TRACKS = {
  home: "calm-home.ogg", friends: "calm-friends.ogg", peaceful: "calm-peaceful.ogg", sand: "calm-sand.ogg",
  summer: "calm-summer.ogg", innocence: "calm-innocence.ogg", town: "quaint-town.mp3", piano: "jrpg-piano.mp3",
  "piano-calm": "calm-piano.mp3",
};

/* What a tune may be played on, and how it may be arranged (agent/playworld.py INSTRUMENTS, STYLES). */
const PLAY_INSTRUMENTS = ["piano", "strings", "flute", "harp", "guitar", "musicbox", "marimba", "organ", "celesta", "pad"];
const PLAY_ARRANGEMENTS = ["solo", "arpeggio", "pad", "waltz", "march"];
const PLAY_OLD_INSTRUMENTS = { triangle: "piano", sine: "flute", pluck: "harp", square: "marimba" };

const playSound = {
  ctx: null, out: null, music: null, effects: null, ambience: null, noise: null,
  volumes: { all: 0.6, music: 0.3, effects: 0.4, quiet: false },
  pending: null,
  tune: null, tuneKey: "", next: 0, index: 0, timer: 0, beds: null, heard: null, lastStep: -1,
};
try { Object.assign(playSound.volumes, JSON.parse(localStorage.getItem(PLAY_SOUND_KEY) || "{}")); } catch (_err) { /* as it is */ }

function playSoundStart() {
  if (!playSound.ctx) {
    const Context = window.AudioContext || window.webkitAudioContext;
    if (!Context) return;
    const ctx = playSound.ctx = new Context();
    playSound.out = ctx.createGain();
    playSound.out.connect(ctx.destination);
    playSound.music = ctx.createGain();
    playSound.effects = ctx.createGain();
    playSound.ambience = ctx.createGain();
    playSound.music.connect(playSound.out);
    playSound.effects.connect(playSound.out);
    playSound.ambience.connect(playSound.out);
    // Noise, made once, for steps and the ambiences.
    const buffer = ctx.createBuffer(1, ctx.sampleRate * 2, ctx.sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < data.length; i += 1) data[i] = Math.random() * 2 - 1;
    playSound.noise = buffer;
  }
  playSound.ctx.resume?.();
  playSoundLevels();
  playSoundRoomTone();
  if (!playSound.timer) playSound.timer = setInterval(playSoundAhead, 100);
}

function playSoundStop() {
  playTrackStop();
  playSound.trackName = "";
  clearInterval(playSound.timer);
  playSound.timer = 0;
  if (playSound.heard) playSoundHear({});                 // all from things quiet
  playSound.ctx?.suspend?.();
}

function playSoundLevels() {
  if (!playSound.ctx) return;
  const v = playSound.volumes, now = playSound.ctx.currentTime;
  playSound.out.gain.setTargetAtTime(v.quiet ? 0 : v.all, now, 0.05);
  playSound.music.gain.setTargetAtTime(v.music * 0.5, now, 0.05);
  playSound.effects.gain.setTargetAtTime(v.effects, now, 0.05);
  playSound.ambience.gain.setTargetAtTime(v.effects * 0.6, now, 0.05);
  try { localStorage.setItem(PLAY_SOUND_KEY, JSON.stringify(v)); } catch (_err) { /* this time only */ }
}

/* -- a room's tune, as the agent wrote it (checked: notes by name, a dash a rest), else its kind's ---------------- */
function playTuneOf(zone) {
  const own = zone && zone.music;
  const kind = (zone && zone.kind) || "town";
  const fallback = PLAY_TUNES[kind] || PLAY_TUNES.town;
  if (!own || typeof own !== "object") return { ...fallback, key: `kind:${kind}` };
  const notes = (text, most) => String(text || "").split(/\s+/).filter((one) => one === "-" || PLAY_NOTE.test(one)).slice(0, most);
  const melody = notes(own.melody, 32);
  if (melody.filter((one) => one !== "-").length < 4) return { ...fallback, key: `kind:${kind}` };
  const instrument = PLAY_OLD_INSTRUMENTS[own.instrument] || own.instrument;
  return { tempo: Math.max(50, Math.min(160, Number(own.tempo) || fallback.tempo)),
           instrument: PLAY_INSTRUMENTS.includes(instrument) ? instrument : fallback.instrument,
           accompaniment: PLAY_INSTRUMENTS.includes(own.accompaniment) ? own.accompaniment : fallback.accompaniment,
           style: PLAY_ARRANGEMENTS.includes(own.style) ? own.style : fallback.style,
           mode: own.mode === "minor" ? "minor" : own.mode === "major" ? "major" : fallback.mode,
           melody: melody.join(" "), bass: notes(own.bass, 16).filter((one) => one !== "-").join(" ") || fallback.bass,
           key: `zone:${zone.id}` };
}

function playFrequency(name) {
  const found = PLAY_NOTE.exec(name);
  if (!found) return 0;
  const semitone = PLAY_SEMITONES[found[1]] + (found[2] === "#" ? 1 : found[2] === "b" ? -1 : 0);
  return 440 * Math.pow(2, (semitone + (Number(found[3]) + 1) * 12 - 69) / 12);
}

/* The room changed: its tune (faded across) and its ambience. */
function playSoundRoom(zone) {
  if (!playSound.ctx) return;
  playSound.pending = { zone, at: performance.now() };
}

/* CALM, NOT A CLATTER (the user, 2026-11-17: "你刚刚生成的游戏声音很杂乱"): a place's tune and sound only once the walker has
   stayed in it a moment, faded across; never begun again for the same tune. */
const PLAY_STAY_MS = 800;
function playSoundSettle() {
  const pending = playSound.pending;
  if (!pending || performance.now() - pending.at < PLAY_STAY_MS) return;
  playSound.pending = null;
  playSoundRoomNow(pending.zone);
}

function playSoundRoomNow(zone) {
  playSound.lastZone = zone;
  // THE AGENT'S CHOICE (the user, 2026-11-17: "我觉得要模型选什么地方用什么，看匹配和合适度"): a recorded track where it
  // chose one for the place; else the tune it wrote for it; else its kind's own made tune.
  const named = zone && zone.music && typeof zone.music === "object" && zone.music.track;
  const track = PLAY_TRACKS[named] ? named : "";
  if (track && !(playSound.missing && playSound.missing.has(track))) {
    playTrack(track);
    return;
  }
  playTrackStop();
  const tune = playTuneOf(zone);
  if (tune.key !== playSound.tuneKey) {
    playSound.tuneKey = tune.key;
    playSound.tune = tune;
    playSound.index = 0;
    playSound.next = playSound.ctx.currentTime + 0.4;
    const now = playSound.ctx.currentTime;
    playSound.music.gain.cancelScheduledValues(now);
    playSound.music.gain.setValueAtTime(0.0001, now);
    playSound.music.gain.linearRampToValueAtTime(playSound.volumes.music * 0.5, now + 2.5);
  }
}

/* Notes scheduled a little ahead, in time - the melody over its bass, each an eighth note. */
function playSoundAhead() {
  playSoundSettle();
  if (typeof playHeard === "function" && performance.now() - (playSound.heardAt || 0) > 250) {
    playSound.heardAt = performance.now();
    playSoundHear(playHeard());
  }
  const tune = playSound.tune, ctx = playSound.ctx;
  if (!tune || !ctx || ctx.state !== "running") return;
  const melody = tune.melody.split(" "), bass = tune.bass.split(" ").filter((one) => one !== "-");
  const eighth = 30 / tune.tempo;
  if (playSound.next < ctx.currentTime - 0.2) playSound.next = ctx.currentTime + 0.05;
  // A chord on each bass note: its root, third (by the mode), fifth and octave, an octave or two up.
  const chordOf = (root) => {
    const f = playFrequency(root);
    const third = tune.mode === "minor" ? 3 : 4;
    return [1, 2 ** (third / 12), 2 ** (7 / 12), 2].map((k) => f * 2 * k);
  };
  const bar = tune.style === "waltz" ? 6 : 8;              // a bass note lasts a bar
  while (playSound.next < ctx.currentTime + 0.4) {
    const i = playSound.index, at = playSound.next;
    const note = melody[i % melody.length];
    if (note !== "-") playInstrument(tune.instrument, playFrequency(note), at, eighth * 1.6, 0.11);
    const root = bass[Math.floor(i / bar) % bass.length];
    const inBar = i % bar;
    if (root) {
      const chord = chordOf(root), low = playFrequency(root);
      const with_ = tune.accompaniment;
      if (inBar === 0) playInstrument(with_ === "organ" || with_ === "strings" || with_ === "pad" ? with_ : "bass", low, at, eighth * bar * 0.95, 0.07);
      if (tune.style === "arpeggio") {
        playInstrument(with_, chord[[0, 2, 3, 2, 1, 2, 3, 2][inBar % 8]], at, eighth * 2, 0.045);
      } else if (tune.style === "pad" && inBar === 0) {
        for (const f of chord.slice(0, 3)) playInstrument(with_ === "piano" ? "pad" : with_, f, at, eighth * bar * 0.98, 0.03);
      } else if (tune.style === "waltz" && (inBar === 2 || inBar === 4)) {
        for (const f of chord.slice(0, 3)) playInstrument(with_, f, at, eighth * 1.4, 0.03);
      } else if (tune.style === "march" && inBar % 4 === 2) {
        for (const f of chord.slice(0, 3)) playInstrument(with_, f, at, eighth * 1.2, 0.03);
      }
    }
    playSound.next += eighth;
    playSound.index += 1;
  }
}

/* A track: fetched once and kept, looped, faded in over the last one fading out. */
function playTrack(name) {
  const ctx = playSound.ctx;
  if (!ctx || playSound.trackName === name) return;
  playSound.trackName = name;
  playSound.tune = null;                                   // the made tune stops
  playSound.tracks = playSound.tracks || new Map();
  const start = (buffer) => {
    if (playSound.trackName !== name) return;
    playTrackStop();
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.loop = true;
    const gain = ctx.createGain();
    const now = ctx.currentTime;
    gain.gain.setValueAtTime(0.0001, now);
    gain.gain.linearRampToValueAtTime(0.9, now + 2.5);
    source.connect(gain).connect(playSound.music);
    source.start(now);
    playSound.trackNow = { source, gain };
  };
  const kept = playSound.tracks.get(name);
  if (kept) { start(kept); return; }
  fetch(`/play/music/${PLAY_TRACKS[name]}`).then((r) => { if (!r.ok) throw new Error(String(r.status)); return r.arrayBuffer(); })
    .then((data) => ctx.decodeAudioData(data))
    .then((buffer) => { playSound.tracks.set(name, buffer); start(buffer); })
    .catch(() => {                                         // this one not to be had: the made tune here
      (playSound.missing = playSound.missing || new Set()).add(name);
      if (playSound.trackName === name) { playSound.trackName = ""; playSound.tuneKey = ""; playSoundRoomNow(playSound.lastZone); }
    });
}
function playTrackStop() {
  const now = playSound.trackNow;
  if (!now || !playSound.ctx) return;
  const t = playSound.ctx.currentTime;
  now.gain.gain.cancelScheduledValues(t);
  now.gain.gain.setValueAtTime(now.gain.gain.value, t);
  now.gain.gain.linearRampToValueAtTime(0.0001, t + 2);
  now.source.stop(t + 2.1);
  playSound.trackNow = null;
}

/* RECORDED SOUNDS (Kenney's RPG sounds, CC0): a door on going in or out, a page at a shelf, a cloth in a bush. */
function playSfx(name, level = 0.5) {
  const ctx = playSound.ctx;
  if (!ctx) return;
  playSound.sfx = playSound.sfx || new Map();
  const go = (buffer) => {
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.playbackRate.value = 0.94 + Math.random() * 0.12;
    const gain = ctx.createGain();
    gain.gain.value = level;
    source.connect(gain).connect(playSound.effects);
    source.start();
  };
  const kept = playSound.sfx.get(name);
  if (kept) { if (kept !== "loading") go(kept); return; }
  playSound.sfx.set(name, "loading");
  fetch(`/play/sfx/${name}.ogg`).then((r) => r.arrayBuffer()).then((data) => ctx.decodeAudioData(data))
    .then((buffer) => { playSound.sfx.set(name, buffer); go(buffer); }).catch(() => playSound.sfx.delete(name));
}

/* -- THE INSTRUMENTS, made here (the user, 2026-11-17: "钢琴曲，弦乐之类") ------------------------------------------------
   piano: a hammer's knock, its partials dying away; strings: three bows a little apart, swelling in, a vibrato;
   flute: a breathy tone with a vibrato; harp and guitar: a pluck, bright then mellow; music box and celesta: a bell's
   partials; marimba: a wooden tone, short; organ: its stops together, held; pad: a soft swell; bass: a round low note. */
function playInstrument(name, f, at, length, level) {
  if (!f || !playSound.ctx) return;
  const ctx = playSound.ctx;
  const out = ctx.createGain();
  out.connect(playSound.musicIn || playSound.music);
  const tone = (type, freq, gain, detune = 0) => {
    const osc = ctx.createOscillator();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, at);
    osc.detune.value = detune;
    const g = ctx.createGain();
    g.gain.value = gain;
    osc.connect(g).connect(out);
    return osc;
  };
  const env = (attack, hold, release, peak = level) => {
    out.gain.setValueAtTime(0.0001, at);
    out.gain.exponentialRampToValueAtTime(peak, at + attack);
    if (hold > attack) out.gain.setValueAtTime(peak, at + hold);
    out.gain.exponentialRampToValueAtTime(0.0001, at + hold + release);
    return at + hold + release + 0.05;
  };
  const vibrato = (oscs, depth, from) => {
    const lfo = ctx.createOscillator();
    const amount = ctx.createGain();
    lfo.frequency.value = 5.2 + Math.random() * 0.6;
    amount.gain.setValueAtTime(0, at);
    amount.gain.linearRampToValueAtTime(depth, at + from);
    lfo.connect(amount);
    for (const o of oscs) amount.connect(o.detune);
    lfo.start(at);
    return lfo;
  };
  let oscs = [], end = at + length, extra = [];
  if (name === "piano") {
    oscs = [tone("triangle", f, 0.7), tone("sine", f * 2, 0.25), tone("sine", f * 3, 0.08), tone("sine", f * 1.002, 0.3)];
    end = env(0.006, 0.006, Math.max(0.6, Math.min(2.2, 260 / f)));
  } else if (name === "strings" || name === "pad") {
    const filter = ctx.createBiquadFilter();
    filter.type = "lowpass";
    filter.frequency.value = name === "pad" ? 900 : 2600;
    out.disconnect();
    out.connect(filter).connect(playSound.musicIn || playSound.music);
    oscs = [tone("sawtooth", f, 0.33, -7), tone("sawtooth", f, 0.33, 6), tone("sawtooth", f * 0.5, name === "pad" ? 0.2 : 0.12, 2)];
    end = env(name === "pad" ? 0.5 : 0.18, Math.max(0.2, length * 0.85), name === "pad" ? 0.8 : 0.35, level * 0.7);
    extra.push(vibrato(oscs, name === "pad" ? 3 : 9, 0.25));
  } else if (name === "flute") {
    oscs = [tone("sine", f, 0.85), tone("triangle", f * 2, 0.08)];
    end = env(0.07, Math.max(0.1, length * 0.8), 0.2);
    extra.push(vibrato(oscs, 12, 0.18));
    const breath = ctx.createBufferSource();
    breath.buffer = playSound.noise;
    const bp = ctx.createBiquadFilter();
    bp.type = "bandpass"; bp.frequency.value = f * 2; bp.Q.value = 6;
    const bg = ctx.createGain();
    bg.gain.setValueAtTime(level * 0.25, at);
    bg.gain.exponentialRampToValueAtTime(0.0001, at + 0.15);
    breath.connect(bp).connect(bg).connect(playSound.musicIn || playSound.music);
    breath.start(at, Math.random(), 0.2);
  } else if (name === "harp" || name === "guitar") {
    const filter = ctx.createBiquadFilter();
    filter.type = "lowpass";
    filter.frequency.setValueAtTime(f * (name === "harp" ? 8 : 6), at);
    filter.frequency.exponentialRampToValueAtTime(f * 1.5, at + 0.6);
    out.disconnect();
    out.connect(filter).connect(playSound.musicIn || playSound.music);
    oscs = [tone(name === "harp" ? "triangle" : "sawtooth", f, 0.6), tone("sine", f * 2, 0.2)];
    end = env(0.004, 0.004, name === "harp" ? 1.4 : 0.9);
  } else if (name === "musicbox" || name === "celesta") {
    oscs = [tone("sine", f * (name === "musicbox" ? 2 : 1), 0.7), tone("sine", f * 4.02, 0.18), tone("sine", f * 6.8, 0.06)];
    end = env(0.003, 0.003, name === "musicbox" ? 1.2 : 1.6);
  } else if (name === "marimba") {
    oscs = [tone("sine", f, 0.8), tone("sine", f * 4, 0.12), tone("sine", f * 10, 0.03)];
    end = env(0.004, 0.004, 0.45);
  } else if (name === "organ") {
    oscs = [tone("sine", f, 0.45), tone("sine", f * 2, 0.3), tone("sine", f * 3, 0.15), tone("sine", f * 4, 0.12), tone("sine", f * 0.5, 0.2)];
    end = env(0.03, Math.max(0.1, length * 0.9), 0.15, level * 0.7);
  } else {                                                     // bass: round and low
    oscs = [tone("sine", f, 0.8), tone("triangle", f * 2, 0.12)];
    end = env(0.02, Math.max(0.1, length * 0.6), 0.4);
  }
  for (const o of oscs) { o.start(at); o.stop(end); }
  for (const o of extra) o.stop(end);
}

/* A little room for the music: a reverb made of decaying noise, mixed under it. */
function playSoundRoomTone() {
  const ctx = playSound.ctx;
  if (!ctx || playSound.musicIn) return;
  const seconds = 2.2, rate = ctx.sampleRate, buffer = ctx.createBuffer(2, rate * seconds, rate);
  for (let ch = 0; ch < 2; ch += 1) {
    const data = buffer.getChannelData(ch);
    for (let i = 0; i < data.length; i += 1) data[i] = (Math.random() * 2 - 1) * (1 - i / data.length) ** 2.5;
  }
  const verb = ctx.createConvolver();
  verb.buffer = buffer;
  const wet = ctx.createGain();
  wet.gain.value = 0.28;
  const input = ctx.createGain();
  input.connect(playSound.music);
  input.connect(verb).connect(wet).connect(playSound.music);
  playSound.musicIn = input;
}

/* -- sounds of what happens --------------------------------------------------------------------------------------- */
/* A STEP BY WHAT IS UNDERFOOT (the user, 2026-11-17: "在不同材质的路上走路声音不一样（在家里也有不同的材质）"): each ground
   a material, each material its own step - grass a soft brush, dirt and sand a gritty crunch, wood a hollow knock,
   stone a hard click, snow a squeaking crunch, a rug a muffled pad. Each step a little different from the last. */
const PLAY_MATERIALS = [
  [/^(grass|flowers|field|autumn|magic|cityGrass)/, "grass"], [/^(dirt|road|sand|caveSand|caveFloor)/, "grit"],
  [/^(wood|bridge)/, "wood"], [/^(stone|tiles|paving|cobble|pavement|asphalt|#|rock|caveGrey|wall|face|out|door)/, "stone"],
];
function playMaterialOf(piece, snowy) {
  const name = String(piece || "").split("~")[0];
  if (snowy && /^(rock|grass)/.test(name)) return "snow";
  for (const [test, material] of PLAY_MATERIALS) if (test.test(name)) return material;
  return "grit";
}

function playStepNoise(at, type, frequency, q, level, length, sweepTo) {
  const ctx = playSound.ctx;
  const source = ctx.createBufferSource();
  source.buffer = playSound.noise;
  const filter = ctx.createBiquadFilter();
  filter.type = type;
  filter.frequency.setValueAtTime(frequency, at);
  if (sweepTo) filter.frequency.exponentialRampToValueAtTime(sweepTo, at + length);
  filter.Q.value = q;
  const gain = ctx.createGain();
  gain.gain.setValueAtTime(0.0001, at);
  gain.gain.exponentialRampToValueAtTime(level, at + 0.004);
  gain.gain.exponentialRampToValueAtTime(0.0001, at + length);
  source.connect(filter).connect(gain).connect(playSound.effects);
  source.start(at, Math.random() * 1.6, length + 0.02);
}

function playStepTone(at, frequency, level, length) {
  const ctx = playSound.ctx;
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.type = "sine";
  osc.frequency.setValueAtTime(frequency, at);
  osc.frequency.exponentialRampToValueAtTime(frequency * 0.6, at + length);
  gain.gain.setValueAtTime(0.0001, at);
  gain.gain.exponentialRampToValueAtTime(level, at + 0.004);
  gain.gain.exponentialRampToValueAtTime(0.0001, at + length);
  osc.connect(gain).connect(playSound.effects);
  osc.start(at);
  osc.stop(at + length + 0.02);
}

function playSoundStep(material) {
  if (!playSound.ctx || !playSound.noise) return;
  const at = playSound.ctx.currentTime, r = () => 0.85 + Math.random() * 0.3;
  if (material === "grass") {
    playStepNoise(at, "highpass", 2400 * r(), 0.7, 0.05, 0.07, 1400);
  } else if (material === "grit") {
    playStepNoise(at, "bandpass", 1600 * r(), 1.1, 0.07, 0.05);
    for (let n = 0; n < 3; n += 1) playStepNoise(at + 0.008 + n * (0.008 + Math.random() * 0.01), "bandpass", 3200 * r(), 2, 0.035, 0.012);
  } else if (material === "wood") {
    playStepTone(at, 150 * r(), 0.12, 0.09);
    playStepNoise(at, "bandpass", 900 * r(), 1.4, 0.05, 0.04);
  } else if (material === "stone") {
    playStepNoise(at, "bandpass", 3400 * r(), 3, 0.09, 0.025);
    playStepTone(at, 520 * r(), 0.025, 0.05);
  } else if (material === "snow") {
    playStepNoise(at, "bandpass", 1100 * r(), 1.2, 0.08, 0.11, 2200);
    playStepNoise(at + 0.03, "bandpass", 2600 * r(), 4, 0.03, 0.05);
  } else if (material === "rug") {
    playStepNoise(at, "lowpass", 500 * r(), 0.7, 0.07, 0.05);
  } else {
    playStepNoise(at, "lowpass", 1200 * r(), 0.7, 0.08, 0.04);
  }
}

function playSoundChime() {
  if (!playSound.ctx) return;
  const now = playSound.ctx.currentTime;
  ["C6", "E6", "G6", "C7"].forEach((note, n) => playEffectNote(playFrequency(note), now + n * 0.07, 0.25, "triangle", 0.12));
}

/* A BUSH TOUCHED: a soft swish of leaves - a short breath of noise, its band sliding down, and a second, quieter. */
function playSoundRustle() {
  if (!playSound.ctx || !playSound.noise) return;
  const ctx = playSound.ctx;
  for (const [delay, level, length] of [[0, 0.16, 0.22], [0.09, 0.08, 0.16]]) {
    const at = ctx.currentTime + delay;
    const source = ctx.createBufferSource();
    source.buffer = playSound.noise;
    const filter = ctx.createBiquadFilter();
    filter.type = "bandpass";
    filter.Q.value = 0.8;
    filter.frequency.setValueAtTime(4200 + Math.random() * 1200, at);
    filter.frequency.exponentialRampToValueAtTime(1800, at + length);
    const gain = ctx.createGain();
    gain.gain.setValueAtTime(0.0001, at);
    gain.gain.exponentialRampToValueAtTime(level, at + 0.03);
    gain.gain.exponentialRampToValueAtTime(0.0001, at + length);
    source.connect(filter).connect(gain).connect(playSound.effects);
    source.start(at, Math.random() * 1.5, length + 0.02);
  }
}

/* A jump: a soft note sliding up. */
function playSoundHop() {
  if (!playSound.ctx) return;
  const ctx = playSound.ctx, at = ctx.currentTime;
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.type = "sine";
  osc.frequency.setValueAtTime(380, at);
  osc.frequency.exponentialRampToValueAtTime(760, at + 0.12);
  gain.gain.setValueAtTime(0.0001, at);
  gain.gain.exponentialRampToValueAtTime(0.07, at + 0.015);
  gain.gain.exponentialRampToValueAtTime(0.0001, at + 0.16);
  osc.connect(gain).connect(playSound.effects);
  osc.start(at);
  osc.stop(at + 0.2);
}

function playSoundNear() {
  if (!playSound.ctx) return;
  const now = playSound.ctx.currentTime;
  playEffectNote(playFrequency("A5"), now, 0.12, "sine", 0.1);
  playEffectNote(playFrequency("E6"), now + 0.06, 0.16, "sine", 0.08);
}

function playEffectNote(frequency, at, length, type, level) {
  const ctx = playSound.ctx;
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.type = type;
  osc.frequency.setValueAtTime(frequency, at);
  gain.gain.setValueAtTime(0.0001, at);
  gain.gain.exponentialRampToValueAtTime(level, at + 0.01);
  gain.gain.exponentialRampToValueAtTime(0.0001, at + length);
  osc.connect(gain).connect(playSound.effects);
  osc.start(at);
  osc.stop(at + length + 0.05);
}

/* -- AMBIENCES, made of noise: shaped, filtered, moved slowly ------------------------------------------------------ */
/* SOUNDS FROM THINGS ONLY (the user, 2026-11-17: "背景旋律是场景可以自带，但是每个其它的声音（流水，壁炉）都是挂靠在item上的，
   这个item有这个声音才会听到"): a fire, water, birds, a crowd - each heard only from a thing that makes it (play.js
   playHeard: a fireplace, a stove, a campfire; water, a fountain, a pond; a tree with a bird in it; a market's stall),
   louder the nearer, little through a wall. Each sound made once and kept going, silent until something near makes it;
   its loudness eased to what is heard. */
const PLAY_HEARD = { water: 0.05, fire: 0.035, crowd: 0.03 };

function playSoundSources() {
  if (playSound.beds || !playSound.ctx) return;
  const ctx = playSound.ctx, now = ctx.currentTime;
  const bed = (type, frequency, q, wobble = 0) => {
    const source = ctx.createBufferSource();
    source.buffer = playSound.noise;
    source.loop = true;
    const filter = ctx.createBiquadFilter();
    filter.type = type;
    filter.frequency.value = frequency;
    filter.Q.value = q;
    const gain = ctx.createGain();
    gain.gain.setValueAtTime(0, now);
    source.connect(filter).connect(gain).connect(playSound.ambience);
    if (wobble) {
      const lfo = ctx.createOscillator();
      const depth = ctx.createGain();
      lfo.frequency.value = wobble;
      depth.gain.value = frequency * 0.35;
      lfo.connect(depth).connect(filter.frequency);
      lfo.start();
    }
    source.start(now, Math.random() * 1.5);
    return gain;
  };
  // Water: a band of noise, its middle wandering - a stream's murmur. A fire: a low, soft roar (its crackle apart).
  // A crowd: voices' band, moving.
  playSound.beds = { water: bed("bandpass", 650, 0.9, 0.23), fire: bed("lowpass", 220, 0.7), crowd: bed("bandpass", 600, 1.5, 0.4) };
  playSound.heard = { water: 0, fire: 0, birds: 0, crowd: 0 };
  // A fire's crackle - irregular, sometimes two or three close together, each a short dry snap - as near as it is.
  const crackle = () => {
    if (!playSound.ctx) return;
    const near = playSound.heard.fire;
    if (near > 0.04 && playSound.ctx.state === "running") {
      const now2 = playSound.ctx.currentTime;
      const pops = Math.random() < 0.25 ? 2 + Math.floor(Math.random() * 2) : 1;
      for (let n = 0; n < pops; n += 1) playSnap(now2 + n * (0.02 + Math.random() * 0.05), near);
    }
    playSound.fireTimer = setTimeout(crackle, 200 + Math.random() * Math.random() * 1500);
  };
  playSound.fireTimer = setTimeout(crackle, 300);
  // A bird's phrase now and then, from a tree with a bird in it.
  playSound.birds = setInterval(() => {
    const near = playSound.heard.birds;
    if (!playSound.ctx || near < 0.04 || playSound.ctx.state !== "running" || Math.random() > 0.14) return;
    const start2 = playSound.ctx.currentTime, high = 2600 + Math.random() * 1400, chirps = 2 + Math.floor(Math.random() * 3);
    for (let n = 0; n < chirps; n += 1) {
      const at = start2 + n * (0.11 + Math.random() * 0.05), top = high * (1 + (Math.random() - 0.5) * 0.08);
      const osc = playSound.ctx.createOscillator();
      const gain = playSound.ctx.createGain();
      osc.type = "sine";
      osc.frequency.setValueAtTime(top * 0.75, at);
      osc.frequency.exponentialRampToValueAtTime(top * 1.25, at + 0.035);
      osc.frequency.exponentialRampToValueAtTime(top * 0.85, at + 0.075);
      gain.gain.setValueAtTime(0.0001, at);
      gain.gain.exponentialRampToValueAtTime(0.0001 + 0.025 * near, at + 0.01);
      gain.gain.exponentialRampToValueAtTime(0.0001, at + 0.08);
      osc.connect(gain).connect(playSound.ambience);
      osc.start(at);
      osc.stop(at + 0.1);
    }
  }, 450);
}

/* What is heard now, from 0 to 1 for each (play.js playHeard), eased to. */
function playSoundHear(levels) {
  if (!playSound.ctx) return;
  playSoundSources();
  const now = playSound.ctx.currentTime;
  for (const key of Object.keys(playSound.heard)) {
    const level = Math.max(0, Math.min(1, Number(levels[key]) || 0));
    playSound.heard[key] = level;
    const bed = playSound.beds[key];
    if (bed) bed.gain.setTargetAtTime(level * PLAY_HEARD[key], now, 0.35);
  }
}

/* A fire's snap: a few milliseconds of noise, high and dry, its pitch and strength its own. */
function playSnap(at, near = 1) {
  const ctx = playSound.ctx;
  const source = ctx.createBufferSource();
  source.buffer = playSound.noise;
  const filter = ctx.createBiquadFilter();
  filter.type = "bandpass";
  filter.frequency.value = 1800 + Math.random() * 3500;
  filter.Q.value = 0.9;
  const gain = ctx.createGain();
  const length = 0.004 + Math.random() * 0.012, level = (0.05 + Math.random() * 0.12) * near;
  gain.gain.setValueAtTime(0.0001, at);
  gain.gain.exponentialRampToValueAtTime(level, at + 0.001);
  gain.gain.exponentialRampToValueAtTime(0.0001, at + length);
  source.connect(filter).connect(gain).connect(playSound.ambience);
  source.start(at, Math.random() * 1.8, length + 0.01);
}

/* -- THE SOUND'S SETTINGS, in the world's corner -------------------------------------------------------------------- */
function playSoundPanel(box) {
  const open = box.querySelector(".side-play-sound");
  if (open) { open.remove(); return; }
  const v = playSound.volumes;
  const panel = document.createElement("div");
  panel.className = "side-play-sound";
  const slider = (key, label) => `<label><span>${escapeHtml(label)}</span><input type="range" min="0" max="100"
    value="${Math.round(v[key] * 100)}" data-play-volume="${key}"></label>`;
  panel.innerHTML = `${slider("all", "All")}${slider("music", "Music")}${slider("effects", "Sounds")}
    <label class="side-play-quiet"><input type="checkbox" data-play-quiet${v.quiet ? " checked" : ""}><span>${escapeHtml("Quiet")}</span></label>`;
  panel.addEventListener("input", (event) => {
    const key = event.target.dataset.playVolume;
    if (key) v[key] = Number(event.target.value) / 100;
    if (event.target.matches("[data-play-quiet]")) v.quiet = event.target.checked;
    playSoundLevels();
  });
  box.appendChild(panel);
}
