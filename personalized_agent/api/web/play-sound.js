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
  library: { tempo: 62, instrument: "cello", accompaniment: "piano", style: "arpeggio", mode: "minor",
             melody: "D5 F5 A5 - G5 F5 E5 - D5 - A4 - C5 D5 - -", bass: "D3 A2 Bb2 A2" },
  screening: { tempo: 84, instrument: "strings", accompaniment: "pad", style: "pad", mode: "major",
               melody: "G4 B4 D5 G5 F#5 D5 B4 - C5 E5 G5 - F#5 - D5 -", bass: "G2 E2 C3 D3" },
  // A cafe at ease, a tea house's quiet (the user, 2026-11-20: "场景是等候茶馆，但是音乐很紧凑"): slow, soft, a bossa on
  // the vibraphone - no longer a quick marimba waltz.
  cafe: { tempo: 72, instrument: "vibraphone", accompaniment: "guitar", style: "bossa", mode: "major",
          melody: "F5 - A5 - C6 A5 G5 - E5 - D5 - F5 - - -", bass: "F2 D3 Bb2 C3" },
  studio: { tempo: 96, instrument: "piano", accompaniment: "strings", style: "pad", mode: "major",
            melody: "C5 D5 E5 G5 A5 G5 E5 D5 C5 - E5 - G5 - - -", bass: "C3 A2 D3 G2" },
  station: { tempo: 116, instrument: "marimba", accompaniment: "piano", style: "march", mode: "major",
             melody: "E5 E5 G5 - E5 D5 C5 - D5 D5 E5 - D5 - C5 -", bass: "C3 G2 F2 G2" },
  // A city's own building's ground (agent/playworld.py _civic): the street's busy cheer.
  civic: { tempo: 108, instrument: "piano", accompaniment: "bass", style: "bounce", mode: "major",
           melody: "C5 E5 G5 E5 F5 A5 G5 - E5 G5 C6 - B5 G5 E5 -", bass: "C3 A2 F2 G2" },
  // A line's station ground (agent/playworld.py _stations): the chime before an announcement, people coming and going.
  terminal: { tempo: 100, instrument: "vibraphone", accompaniment: "piano", style: "bossa", mode: "major",
              melody: "C5 E5 G5 - C6 - - - A5 G5 E5 - F5 E5 D5 -", bass: "C3 A2 F2 G2" },
  garden: { tempo: 80, instrument: "flute", accompaniment: "harp", style: "arpeggio", mode: "major",
            melody: "G5 E5 C5 E5 G5 A5 G5 - E5 G5 A5 - C6 - A5 -", bass: "C3 E3 F2 G2" },
  snow: { tempo: 70, instrument: "celesta", accompaniment: "pad", style: "pad", mode: "major",
          melody: "E6 - B5 - G5 - B5 C6 D6 - B5 - G5 - - -", bass: "E3 C3 G2 D3" },
  cavern: { tempo: 58, instrument: "organ", accompaniment: "organ", style: "pad", mode: "minor",
            melody: "A4 - - C5 B4 - A4 - E4 - - G4 A4 - - -", bass: "A2 F2 D2 E2" },
  city: { tempo: 92, instrument: "piano", accompaniment: "strings", style: "pad", mode: "major",
          melody: "E5 - D5 C5 D5 E5 G5 - A5 G5 E5 - D5 - C5 -", bass: "C3 G2 A2 F2" },
};
/* EACH KIND OF PLACE ITS OWN (the user, 2026-11-19: "把缺默认曲的地方类型补齐，不再退回'城镇'"): what a place plays when
   the agent wrote it none - a courtyard, a grove, a lake each its own, never the town's. */
Object.assign(PLAY_TUNES, {
  park: { tempo: 88, instrument: "flute", accompaniment: "guitar", style: "arpeggio", mode: "major",
          melody: "D5 F#5 A5 F#5 G5 E5 D5 - B4 D5 E5 F#5 E5 - D5 -", bass: "D3 B2 G2 A2" },
  pool: { tempo: 92, instrument: "marimba", accompaniment: "harp", style: "arpeggio", mode: "major",
          melody: "G5 B5 D6 B5 A5 G5 E5 - G5 A5 B5 - A5 - G5 -", bass: "G2 E3 C3 D3" },
  courtyard: { tempo: 76, instrument: "violin", accompaniment: "harp", style: "waltz", mode: "major",
               melody: "F5 A5 C6 A5 Bb5 G5 F5 - D5 F5 G5 A5 G5 - F5 -", bass: "F2 D3 Bb2 C3" },
  terrace: { tempo: 84, instrument: "guitar", accompaniment: "pad", style: "waltz", mode: "major",
             melody: "A4 C#5 E5 - F#5 E5 C#5 - B4 C#5 E5 - A4 - - -", bass: "A2 F#2 D3 E3" },
  lake: { tempo: 64, instrument: "harp", accompaniment: "pad", style: "pad", mode: "major",
          melody: "E5 - G#5 B5 - A5 G#5 - E5 - F#5 - E5 - - -", bass: "E3 C#3 A2 B2" },
  waterfall: { tempo: 104, instrument: "celesta", accompaniment: "harp", style: "arpeggio", mode: "major",
               melody: "C6 B5 A5 G5 A5 G5 E5 D5 E5 G5 A5 C6 B5 - G5 -", bass: "C3 A2 F2 G2" },
  plaza: { tempo: 108, instrument: "marimba", accompaniment: "guitar", style: "march", mode: "major",
           melody: "C5 E5 G5 E5 F5 A5 G5 - E5 F5 G5 C6 B5 - G5 -", bass: "C3 F2 G2 C3" },
  grove: { tempo: 66, instrument: "flute", accompaniment: "harp", style: "pad", mode: "major",
           melody: "D5 - F#5 A5 - G5 F#5 - E5 - D5 E5 F#5 - - -", bass: "D3 G2 A2 D3" },
  meadow: { tempo: 86, instrument: "flute", accompaniment: "guitar", style: "waltz", mode: "major",
            melody: "G5 A5 B5 - D6 B5 A5 - G5 E5 D5 - E5 - G5 -", bass: "G2 C3 D3 G2" },
  mountain: { tempo: 72, instrument: "strings", accompaniment: "pad", style: "pad", mode: "major",
              melody: "D5 A5 - G5 F#5 - E5 D5 E5 - A4 - D5 - - -", bass: "D3 A2 G2 A2" },
  volcano: { tempo: 96, instrument: "organ", accompaniment: "organ", style: "march", mode: "minor",
             melody: "D5 D5 F5 - E5 D5 C#5 - D5 - A4 - Bb4 A4 G4 A4", bass: "D2 Bb2 G2 A2" },
  peak: { tempo: 60, instrument: "celesta", accompaniment: "strings", style: "pad", mode: "major",
          melody: "A5 - E6 - D6 C#6 - B5 A5 - - E5 A5 - - -", bass: "A2 D3 E3 A2" },
  aquarium: { tempo: 70, instrument: "celesta", accompaniment: "pad", style: "arpeggio", mode: "major",
              melody: "E5 G#5 B5 - C#6 B5 G#5 - E5 F#5 G#5 - F#5 - E5 -", bass: "E3 C#3 A2 B2" },
  greenhouse: { tempo: 74, instrument: "harp", accompaniment: "flute", style: "arpeggio", mode: "major",
                melody: "F5 G5 A5 C6 - A5 G5 - F5 - D5 F5 G5 - - -", bass: "F2 Bb2 C3 F2" },
  classroom: { tempo: 98, instrument: "piano", accompaniment: "marimba", style: "arpeggio", mode: "major",
               melody: "C5 D5 E5 C5 E5 F5 G5 - A5 G5 F5 E5 D5 - C5 -", bass: "C3 F2 G2 C3" },
  lab: { tempo: 104, instrument: "musicbox", accompaniment: "pad", style: "arpeggio", mode: "minor",
         melody: "A5 E5 A5 C6 B5 E5 B5 D6 C6 A5 E5 C5 B4 - E5 -", bass: "A2 F2 G2 E2" },
  office: { tempo: 92, instrument: "piano", accompaniment: "strings", style: "pad", mode: "major",
            melody: "G4 B4 D5 - C5 B4 A4 - G4 A4 B4 D5 C5 - B4 -", bass: "G2 C3 D3 G2" },
  apartment: { tempo: 80, instrument: "guitar", accompaniment: "piano", style: "arpeggio", mode: "major",
               melody: "E5 D5 C5 - D5 E5 G5 - A5 G5 E5 - D5 - C5 -", bass: "C3 A2 F2 G2" },
  lecture_hall: { tempo: 70, instrument: "organ", accompaniment: "strings", style: "pad", mode: "major",
                  melody: "C5 - E5 G5 F5 - E5 D5 C5 - G4 - C5 - - -", bass: "C3 F2 G2 C3" },
  sea_tunnel: { tempo: 62, instrument: "pad", accompaniment: "celesta", style: "pad", mode: "minor",
                melody: "E5 - - G5 F#5 - E5 - B4 - - D5 E5 - - -", bass: "E2 C3 A2 B2" },
  roller_coaster: { tempo: 140, instrument: "marimba", accompaniment: "piano", style: "march", mode: "major",
                    melody: "C5 E5 G5 C6 G5 E5 C5 - D5 F5 A5 D6 B5 - G5 -", bass: "C3 G2 D3 G2" },
  funfair: { tempo: 126, instrument: "organ", accompaniment: "musicbox", style: "waltz", mode: "major",
             melody: "G5 E5 G5 C6 B5 G5 A5 - F5 D5 F5 B5 A5 - G5 -", bass: "C3 G2 G2 C3" },
  cemetery: { tempo: 54, instrument: "organ", accompaniment: "pad", style: "pad", mode: "minor",
              melody: "D5 - - F5 E5 - D5 - A4 - - C5 D5 - - -", bass: "D2 Bb2 G2 A2" },
  estate: { tempo: 82, instrument: "strings", accompaniment: "harp", style: "waltz", mode: "major",
            melody: "D5 F#5 A5 - B5 A5 F#5 - G5 F#5 E5 - D5 - - -", bass: "D3 G2 A2 D3" },
});

/* EACH SCENE ITS OWN MUSIC (the user, 2026-11-19: "不同场景应该有对应不同的音乐"; "给 43 个场景各配一首保底曲，按场景定乐器、
   调式和速度 ... 模型没选时用它"): what a land of that scene (agent/playmuse.py MOOD_SCENES) plays out in it when the
   feeling's call gave it none - snow on the celesta, slow and minor; a market on the marimba, quick and major. */
const PLAY_SCENE_TUNES = {
  spring_meadow: { tempo: 92, instrument: "flute", accompaniment: "harp", style: "arpeggio", mode: "major",
                   melody: "C5 E5 G5 A5 G5 E5 D5 - E5 G5 C6 - B5 A5 G5 -", bass: "C3 F2 G2 C3" },
  sunny_islands: { tempo: 100, instrument: "steeldrum", accompaniment: "guitar", style: "bossa", mode: "major",
                   melody: "F5 A5 C6 - A5 G5 F5 D5 C5 - D5 F5 G5 - F5 -", bass: "F2 Bb2 C3 F2" },
  summer_coast: { tempo: 108, instrument: "guitar", accompaniment: "marimba", style: "waltz", mode: "major",
                  melody: "D5 F#5 A5 - F#5 E5 D5 - B4 D5 E5 - F#5 - D5 -", bass: "D3 G2 A2 D3" },
  blossom_garden: { tempo: 76, instrument: "harp", accompaniment: "flute", style: "arpeggio", mode: "major",
                    melody: "G5 - B5 D6 C6 B5 A5 - G5 - E5 G5 A5 - - -", bass: "G2 C3 D3 G2" },
  golden_farmland: { tempo: 90, instrument: "guitar", accompaniment: "strings", style: "waltz", mode: "major",
                     melody: "G4 B4 D5 - E5 D5 B4 - C5 B4 A4 - G4 - - -", bass: "G2 C3 D3 G2" },
  sky_city: { tempo: 112, instrument: "celesta", accompaniment: "strings", style: "arpeggio", mode: "major",
              melody: "E5 G#5 B5 E6 - D#6 B5 - C#6 B5 G#5 - F#5 - E5 -", bass: "E3 A2 B2 E3" },
  festival: { tempo: 132, instrument: "organ", accompaniment: "marimba", style: "march", mode: "major",
              melody: "C5 C5 E5 G5 E5 G5 C6 - A5 G5 F5 E5 D5 - C5 -", bass: "C3 G2 F2 G2" },
  toy_town: { tempo: 120, instrument: "chiptune", accompaniment: "marimba", style: "bounce", mode: "major",
              melody: "G5 E5 C5 E5 G5 - G5 - A5 F5 D5 F5 A5 - G5 -", bass: "C3 G2 F2 G2" },
  new_year_lanterns: { tempo: 84, instrument: "koto", accompaniment: "harp", style: "folk", mode: "major",
                       melody: "D5 E5 G5 A5 - G5 E5 - D5 - C5 D5 E5 - D5 -", bass: "D3 A2 G2 A2" },
  christmas_village: { tempo: 96, instrument: "bells", accompaniment: "strings", style: "waltz", mode: "major",
                       melody: "G5 - E5 - G5 A5 G5 - E5 - C5 - D5 - - -", bass: "C3 F2 G2 C3" },
  valentine_garden: { tempo: 70, instrument: "piano", accompaniment: "strings", style: "arpeggio", mode: "major",
                      melody: "A5 - F5 - G5 A5 Bb5 - A5 - F5 - G5 - - -", bass: "F2 Bb2 C3 F2" },
  misty_lake: { tempo: 58, instrument: "pad", accompaniment: "harp", style: "pad", mode: "minor",
                melody: "E5 - - G5 - F#5 E5 - B4 - - D5 - E5 - -", bass: "E2 C3 G2 B2" },
  still_lake_dawn: { tempo: 64, instrument: "harp", accompaniment: "pad", style: "arpeggio", mode: "major",
                     melody: "D5 - A5 - G5 F#5 E5 - D5 - E5 F#5 A5 - - -", bass: "D3 G2 A2 D3" },
  rainy_forest: { tempo: 62, instrument: "piano", accompaniment: "pad", style: "pad", mode: "minor",
                  melody: "A4 - C5 - E5 D5 C5 - B4 - G4 - A4 - - -", bass: "A2 F2 G2 E2" },
  quiet_grove: { tempo: 68, instrument: "flute", accompaniment: "harp", style: "pad", mode: "major",
                 melody: "F5 - A5 - G5 F5 D5 - C5 - D5 F5 - - - -", bass: "F2 Bb2 C3 F2" },
  waterfall_valley: { tempo: 116, instrument: "harp", accompaniment: "strings", style: "arpeggio", mode: "major",
                      melody: "B5 A5 G5 F#5 G5 A5 B5 D6 C6 B5 A5 G5 A5 - G5 -", bass: "G2 C3 D3 G2" },
  library_town_rain: { tempo: 60, instrument: "piano", accompaniment: "strings", style: "arpeggio", mode: "minor",
                       melody: "D5 - F5 A5 G5 - F5 E5 D5 - - C5 D5 - - -", bass: "D3 Bb2 G2 A2" },
  botanic_glasshouse: { tempo: 66, instrument: "celesta", accompaniment: "pad", style: "pad", mode: "major",
                        melody: "C6 - G5 - A5 - E5 - F5 G5 A5 - G5 - - -", bass: "F2 C3 Bb2 C3" },
  autumn_valley: { tempo: 72, instrument: "guitar", accompaniment: "strings", style: "arpeggio", mode: "minor",
                   melody: "E5 G5 B5 - A5 G5 F#5 - E5 - D5 E5 G5 - E5 -", bass: "E2 C3 D3 B2" },
  enchanted_dusk: { tempo: 74, instrument: "celesta", accompaniment: "harp", style: "arpeggio", mode: "minor",
                    melody: "A5 C6 E6 - D6 C6 B5 - G#5 - A5 B5 C6 - A5 -", bass: "A2 F2 D3 E2" },
  starry_space: { tempo: 56, instrument: "choir", accompaniment: "bells", style: "ambient", mode: "major",
                  melody: "E5 - B5 - - A5 G#5 - E5 - - C#6 B5 - - -", bass: "E2 A2 B2 E2" },
  deep_sea_calm: { tempo: 54, instrument: "pad", accompaniment: "harp", style: "pad", mode: "major",
                   melody: "D5 - - F#5 - E5 - D5 A4 - - B4 - A4 - -", bass: "D2 G2 A2 D2" },
  // UNDER THE SEA, IN THE SKY, IN SPACE - each scene its own (the user, 2026-11-20: "加上专属音乐").
  coral_reef: { tempo: 96, instrument: "steeldrum", accompaniment: "marimba", style: "bossa", mode: "major",
                melody: "G5 B5 D6 B5 A5 - G5 E5 - G5 A5 B5 - A5 G5 -", bass: "G2 E2 C3 D3" },
  kelp_forest: { tempo: 62, instrument: "harp", accompaniment: "pad", style: "arpeggio", mode: "minor",
                 melody: "E5 - G5 - B5 - A5 G5 - E5 - D5 E5 - - -", bass: "E2 C3 A2 B2" },
  sunken_city: { tempo: 58, instrument: "choir", accompaniment: "harp", style: "pad", mode: "minor",
                 melody: "D5 - F5 - A5 - G5 F5 E5 - - D5 - - - -", bass: "D2 Bb2 F2 A2" },
  cloud_kingdom: { tempo: 84, instrument: "celesta", accompaniment: "harp", style: "waltz", mode: "major",
                   melody: "C6 - G5 E5 - G5 A5 - G5 - E5 D5 E5 - - -", bass: "C3 F2 G2 C3" },
  floating_isles: { tempo: 92, instrument: "flute", accompaniment: "harp", style: "arpeggio", mode: "major",
                    melody: "D5 F#5 A5 - B5 A5 F#5 - E5 F#5 A5 - D6 - - -", bass: "D3 G2 A2 D3" },
  sunset_sky: { tempo: 66, instrument: "violin", accompaniment: "piano", style: "ballad", mode: "major",
                melody: "A4 - C#5 E5 - D5 C#5 - B4 - A4 - E5 - - -", bass: "A2 F#2 D3 E3" },
  storm_clouds: { tempo: 108, instrument: "strings", accompaniment: "organ", style: "march", mode: "minor",
                  melody: "E5 E5 G5 - F#5 E5 D#5 - E5 - B4 - E5 G5 B5 -", bass: "E2 C3 A2 B2" },
  moon_base: { tempo: 76, instrument: "chiptune", accompaniment: "pad", style: "arpeggio", mode: "minor",
               melody: "A4 C5 E5 A5 - G5 E5 - F5 - E5 C5 D5 - - -", bass: "A2 F2 G2 E2" },
  nebula_garden: { tempo: 60, instrument: "vibraphone", accompaniment: "choir", style: "ambient", mode: "major",
                   melody: "F5 - A5 - C6 - - B5 A5 - G5 - F5 - - -", bass: "F2 D2 Bb2 C3" },
  ringed_planet: { tempo: 52, instrument: "organ", accompaniment: "bells", style: "ambient", mode: "major",
                   melody: "C5 - - G5 - - E5 - F5 - - C6 - - - -", bass: "C2 F2 G2 C2" },
  downtown: { tempo: 116, instrument: "piano", accompaniment: "bass", style: "stride", mode: "major",
              melody: "E5 G5 A5 C6 - B5 A5 G5 - E5 D5 E5 G5 - - -", bass: "C3 A2 D3 G2" },
  harbour_city: { tempo: 98, instrument: "accordion", accompaniment: "guitar", style: "waltz", mode: "major",
                  melody: "G5 - B5 D6 - C6 B5 A5 - G5 - E5 F#5 G5 - -", bass: "G2 C3 D3 G2" },
  quiet_suburbs: { tempo: 80, instrument: "guitar", accompaniment: "piano", style: "folk", mode: "major",
                   melody: "D5 E5 F#5 - A5 - F#5 E5 D5 - B4 - D5 - - -", bass: "D3 G2 A2 D3" },
  neon_metropolis: { tempo: 112, instrument: "chiptune", accompaniment: "bass", style: "bounce", mode: "minor",
                     melody: "A5 - C6 A5 G5 - E5 - G5 A5 - E5 D5 - C5 -", bass: "A2 F2 G2 E2" },
  orbital_station: { tempo: 104, instrument: "kalimba", accompaniment: "pad", style: "bounce", mode: "major",
                     melody: "E5 G5 B5 G5 E5 - D5 - E5 G5 A5 - G5 - E5 -", bass: "E2 C3 D3 B2" },
  windy_highlands: { tempo: 88, instrument: "flute", accompaniment: "strings", style: "pad", mode: "minor",
                     melody: "D5 E5 F5 A5 - G5 F5 E5 D5 - A4 - C5 D5 - -", bass: "D3 C3 Bb2 A2" },
  snowy_peaks: { tempo: 66, instrument: "strings", accompaniment: "celesta", style: "pad", mode: "minor",
                 melody: "B4 - D5 F#5 - E5 D5 - C#5 - B4 - A4 B4 - -", bass: "B2 G2 D3 F#2" },
  volcanoes: { tempo: 100, instrument: "organ", accompaniment: "organ", style: "march", mode: "minor",
               melody: "C5 C5 Eb5 - D5 C5 B4 - C5 - G4 - Ab4 G4 F4 G4", bass: "C2 Ab2 F2 G2" },
  primeval_jungle: { tempo: 104, instrument: "marimba", accompaniment: "flute", style: "march", mode: "minor",
                     melody: "E5 G5 E5 D5 E5 - B4 - D5 E5 G5 A5 G5 - E5 -", bass: "E2 D3 C3 B2" },
  night_city: { tempo: 94, instrument: "vibraphone", accompaniment: "piano", style: "bossa", mode: "minor",
                melody: "F5 Ab5 C6 - Bb5 Ab5 G5 - F5 - Eb5 F5 G5 - C5 -", bass: "F2 Db3 Eb2 C3" },
  future_city: { tempo: 118, instrument: "musicbox", accompaniment: "pad", style: "arpeggio", mode: "major",
                 melody: "A5 E5 A5 C#6 B5 E5 B5 D6 C#6 A5 E5 A5 B5 - A5 -", bass: "A2 F#2 D3 E3" },
  busy_market_plaza: { tempo: 124, instrument: "accordion", accompaniment: "guitar", style: "tango", mode: "major",
                       melody: "D5 F#5 A5 F#5 D5 F#5 A5 - B5 A5 G5 F#5 E5 - D5 -", bass: "D3 A2 G2 A2" },
  launch_site: { tempo: 110, instrument: "strings", accompaniment: "organ", style: "march", mode: "major",
                 melody: "C5 G5 - G5 A5 G5 E5 - F5 G5 A5 C6 - B5 C6 -", bass: "C3 G2 F2 G2" },
  desert: { tempo: 64, instrument: "flute", accompaniment: "pad", style: "pad", mode: "minor",
            melody: "D5 - Eb5 - F#5 G5 F#5 - Eb5 D5 - C5 D5 - - -", bass: "D2 G2 C3 D2" },
  desert_oasis: { tempo: 78, instrument: "harp", accompaniment: "flute", style: "arpeggio", mode: "major",
                  melody: "E5 F5 G#5 - A5 G#5 F5 E5 - D5 E5 - F5 - E5 -", bass: "E3 A2 D3 E3" },
  snowfield: { tempo: 56, instrument: "celesta", accompaniment: "pad", style: "pad", mode: "minor",
               melody: "E6 - - B5 - C6 B5 - A5 - - G5 - E5 - -", bass: "E3 C3 A2 B2" },
  long_winter_village: { tempo: 70, instrument: "musicbox", accompaniment: "strings", style: "waltz", mode: "minor",
                         melody: "A5 - E5 - A5 B5 C6 - B5 - G#5 - A5 - - -", bass: "A2 D3 E2 A2" },
  grey_rain_town: { tempo: 58, instrument: "piano", accompaniment: "strings", style: "pad", mode: "minor",
                    melody: "G4 - Bb4 - D5 C5 Bb4 - A4 - G4 - F#4 G4 - -", bass: "G2 Eb2 C3 D2" },
  foggy_ruins: { tempo: 60, instrument: "strings", accompaniment: "pad", style: "pad", mode: "minor",
                 melody: "B4 - D5 - C#5 B4 - F#4 G4 - A4 - B4 - - -", bass: "B2 G2 E2 F#2" },
  dark_sea_floor: { tempo: 50, instrument: "pad", accompaniment: "organ", style: "pad", mode: "minor",
                    melody: "C5 - - - Eb5 - D5 - G4 - - - C5 - - -", bass: "C2 Ab2 F2 G2" },
  cavern_depths: { tempo: 56, instrument: "organ", accompaniment: "pad", style: "pad", mode: "minor",
                   melody: "E4 - - G4 F#4 - E4 - B3 - - D4 E4 - - -", bass: "E2 C2 A2 B2" },
  graveyard_mist: { tempo: 52, instrument: "strings", accompaniment: "organ", style: "pad", mode: "minor",
                    melody: "F5 - - Ab5 G5 - F5 - C5 - - Eb5 F5 - - -", bass: "F2 Db3 Bb2 C3" },
  haunted_autumn: { tempo: 86, instrument: "musicbox", accompaniment: "organ", style: "waltz", mode: "minor",
                    melody: "E5 - G5 F#5 E5 - B4 - C5 - B4 A4 B4 - - -", bass: "E2 A2 B2 E2" },
  lone_island: { tempo: 58, instrument: "guitar", accompaniment: "pad", style: "pad", mode: "minor",
                 melody: "A4 - C5 E5 - D5 C5 - B4 - - G4 A4 - - -", bass: "A2 F2 C3 E2" },
  zoo_park: { tempo: 112, instrument: "marimba", accompaniment: "piano", style: "march", mode: "major",
              melody: "F5 A5 C6 A5 G5 E5 C5 - D5 E5 F5 G5 A5 - F5 -", bass: "F2 C3 Bb2 C3" },
  river_journey: { tempo: 96, instrument: "guitar", accompaniment: "harp", style: "arpeggio", mode: "major",
                   melody: "A4 C#5 E5 A5 - G#5 E5 - F#5 E5 C#5 - B4 - A4 -", bass: "A2 D3 E3 A2" },
};
/* RECORDED MUSIC (the user, 2026-11-17: "你的音乐也可以自己去网上搜索"; CC0 tracks, play/ASSETS.txt): a place has one
   only where the agent chose it as the right fit (agent/playworld.py TRACKS) - else its own written tune; looped,
   faded across. */
const PLAY_TRACKS = {
  home: "calm-home.ogg", friends: "calm-friends.ogg", peaceful: "calm-peaceful.ogg", sand: "calm-sand.ogg",
  summer: "calm-summer.ogg", innocence: "calm-innocence.ogg", town: "quaint-town.mp3", piano: "jrpg-piano.mp3",
  "piano-calm": "calm-piano.mp3",
  // More, CC0 (2026-11-19; play/ASSETS.txt):
  "frogtown": "frogtown.mp3",
  "calm-loop": "calm-loop.mp3",
  "the-hope": "the-hope.mp3",
  "snow-fest": "snow-fest.mp3",
  "brand-new-wisdom": "brand-new-wisdom.ogg",
  "just-saying-tho": "just-saying-tho.ogg",
  "winter-dust": "winter-dust.ogg",
  "swinging-sweet": "swinging-sweet.ogg",
  "treasure-hunter": "treasure-hunter.mp3",
  "magic-puzzle": "magic-puzzle.ogg",
  "busy-calm-street": "busy-calm-street.ogg",
};

/* What a tune may be played on, and how it may be arranged (agent/playworld.py INSTRUMENTS, STYLES). */
// More to choose from (the user, 2026-11-19: "你的音乐可选再加多") - agent/playworld.py INSTRUMENTS, ARRANGEMENTS the same.
const PLAY_INSTRUMENTS = ["piano", "strings", "flute", "harp", "guitar", "musicbox", "marimba", "organ", "celesta", "pad",
                          "bells", "kalimba", "vibraphone", "clarinet", "accordion", "koto", "choir", "chiptune", "steeldrum", "ocarina", "bass", "violin", "cello"];
const PLAY_ARRANGEMENTS = ["solo", "arpeggio", "pad", "waltz", "march", "lullaby", "bossa", "ballad", "bounce", "folk", "tango", "ambient", "stride"];
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
/* HEARD AGAIN AT THE FIRST PRESS (the user, 2026-11-19: "我现在突然没有音乐了"): a page may make no sound before a press of
   the user's - the Play view opened by itself (drawn again after its language changed) had its sound held, and nothing
   let it go. Any press or key while it is open lets it go. */
for (const kind of ["pointerdown", "keydown"]) {
  window.addEventListener(kind, () => {
    const ctx = playSound.ctx;
    if (ctx && ctx.state === "suspended" && typeof play !== "undefined" && play.on && !document.hidden) {
      ctx.resume?.();
      playSoundLevels();
    }
  }, { capture: true });
}

function playSoundStop() {
  if (typeof playNote === "function") playNote("sound stopped (the view hidden or shut)");
  playTrackStop();
  playSound.trackName = "";
  clearInterval(playSound.timer);
  playSound.timer = 0;
  if (playSound.heard) playSoundHear({});                 // all from things quiet
  if (playSound.passing) { for (const voice of playSound.passing.values()) playPassingLetGo(voice); playSound.passing.clear(); }
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
  // Out in a land: its scene's own (PLAY_SCENE_TUNES); a place: its kind's.
  const scene = zone && zone.scene && PLAY_SCENE_TUNES[zone.scene] ? zone.scene : "";
  const fallback = scene ? PLAY_SCENE_TUNES[scene] : PLAY_TUNES[kind] || PLAY_TUNES.town;
  const fallbackKey = scene ? `scene:${scene}` : `kind:${kind}`;
  if (!own || typeof own !== "object") return { ...fallback, key: fallbackKey };
  const notes = (text, most) => String(text || "").split(/\s+/).filter((one) => one === "-" || PLAY_NOTE.test(one)).slice(0, most);
  const melody = notes(own.melody, 32);
  if (melody.filter((one) => one !== "-").length < 4) return { ...fallback, key: fallbackKey };
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
  if (tune.key !== playSound.tuneKey || !playSound.tune) {
    if (typeof playNote === "function") playNote(`music: the tune ${tune.key}`);
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
const PLAY_MUSIC_AHEAD = 1.5;
function playSoundAhead() {
  playSoundSettle();
  if (typeof playHeard === "function" && performance.now() - (playSound.heardAt || 0) > 250) {
    playSound.heardAt = performance.now();
    playSoundHear(playHeard());
  }
  playSoundPassing();                                     // what goes by, coming and going
  const tune = playSound.tune, ctx = playSound.ctx;
  if (!tune || !ctx || ctx.state !== "running") return;
  const melody = tune.melody.split(" "), bass = tune.bass.split(" ").filter((one) => one !== "-");
  const eighth = 30 / tune.tempo;
  if (playSound.next < ctx.currentTime - 0.2) playSound.next = ctx.currentTime + 0.05;
  // FAR AHEAD (the user, 2026-11-20: "音乐在view最大的时候一卡一卡的"): 0.4 s ahead, a page held a little longer than
  // that left a gap; notes are set PLAY_MUSIC_AHEAD s ahead now - the sound card plays them on its own.
  // A chord on each bass note: its root, third (by the mode), fifth and octave, an octave or two up.
  const chordOf = (root) => {
    const f = playFrequency(root);
    const third = tune.mode === "minor" ? 3 : 4;
    return [1, 2 ** (third / 12), 2 ** (7 / 12), 2].map((k) => f * 2 * k);
  };
  const bar = ["waltz", "lullaby", "folk"].includes(tune.style) ? 6 : 8;    // a bass note lasts a bar
  while (playSound.next < ctx.currentTime + PLAY_MUSIC_AHEAD) {
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
      } else if (tune.style === "lullaby" && inBar % 3 === 0) {                 // rocking: root, then fifth
        playInstrument(with_, chord[inBar === 0 ? 0 : 2], at, eighth * 2.6, 0.035);
      } else if (tune.style === "bossa" && [0, 3, 6].includes(inBar)) {          // its syncopation
        for (const f of chord.slice(0, 3)) playInstrument(with_, f, at, eighth * 1.1, 0.028);
      } else if (tune.style === "ballad" && inBar % 4 === 0) {                   // held chords, twice a bar
        for (const f of chord.slice(0, 4)) playInstrument(with_, f, at, eighth * 3.8, 0.026);
      } else if (tune.style === "bounce") {                                     // bass leaping, chords off the beat
        if (inBar % 2 === 1) for (const f of chord.slice(0, 3)) playInstrument(with_, f, at, eighth * 0.8, 0.026);
        else if (inBar === 4) playInstrument("bass", low * 1.5, at, eighth * 1.5, 0.06);
      } else if (tune.style === "folk" && (inBar === 0 || inBar === 3)) {        // a drone and a jig's lilt
        playInstrument(with_, chord[inBar === 0 ? 0 : 2], at, eighth * 2.8, 0.035);
      } else if (tune.style === "tango" && [0, 3, 5, 6].includes(inBar)) {       // its sharp accents
        for (const f of chord.slice(0, 3)) playInstrument(with_, f, at, eighth * 0.7, inBar === 0 ? 0.034 : 0.026);
      } else if (tune.style === "ambient" && inBar === 0 && (i / bar) % 2 < 1) {  // a chord now and then, held long
        for (const f of chord.slice(0, 3)) playInstrument("pad", f, at, eighth * bar * 1.9, 0.024);
      } else if (tune.style === "stride" && inBar % 2 === 0) {                   // stride: bass, chord, bass, chord
        if (inBar % 4 === 0) playInstrument("bass", inBar === 0 ? low : low * 1.5, at, eighth * 1.6, 0.06);
        else for (const f of chord.slice(0, 3)) playInstrument(with_, f, at, eighth * 1.2, 0.028);
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
  // The made tune stops - and is forgotten, so coming back to it starts it again (the user, 2026-11-19: "我刚刚有音乐，
  // 突然就没有了": its key was kept, the way back thought it still played, and nothing played at all).
  playSound.tune = null;
  playSound.tuneKey = "";
  if (typeof playNote === "function") playNote(`music: the recorded "${name}"`);
  playSound.tracks = playSound.tracks || new Map();
  const start = (buffer) => {
    if (playSound.trackName !== name) return;
    playTrackStop(true);                                   // the one before it faded - this one's name kept
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
function playTrackStop(keepName = false) {
  // Its name let go with it (the user, 2026-11-19: "突然就没有了"): kept, the next time it was wanted it was thought
  // still playing, and nothing was.
  if (!keepName) playSound.trackName = "";
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
  } else if (name === "violin" || name === "cello") {          // A BOWED STRING ALONE (the user: "你怎么没有弦乐，小提琴那种")
    // its bow drawn in, its body's resonance, its vibrato coming in late - the cello darker and warmer
    const body = ctx.createBiquadFilter();
    body.type = "lowpass"; body.Q.value = 1.2;
    body.frequency.setValueAtTime(name === "violin" ? Math.min(7000, f * 7) : Math.min(2600, f * 5), at);
    const formant = ctx.createBiquadFilter();
    formant.type = "peaking"; formant.frequency.value = name === "violin" ? 2800 : 600; formant.gain.value = 5;
    out.disconnect(); out.connect(body).connect(formant).connect(playSound.musicIn || playSound.music);
    oscs = [tone("sawtooth", f, 0.5), tone("sawtooth", f, 0.25, name === "violin" ? 4 : 3)];
    end = env(name === "violin" ? 0.09 : 0.14, Math.max(0.15, length * 0.9), name === "violin" ? 0.25 : 0.4, level * 0.85);
    extra.push(vibrato(oscs, name === "violin" ? 14 : 10, name === "violin" ? 0.22 : 0.3));
    const bow = ctx.createBufferSource();                     // the bow's hiss as it starts
    bow.buffer = playSound.noise;
    const bp = ctx.createBiquadFilter();
    bp.type = "bandpass"; bp.frequency.value = f * 3; bp.Q.value = 3;
    const bg = ctx.createGain();
    bg.gain.setValueAtTime(level * 0.12, at);
    bg.gain.exponentialRampToValueAtTime(0.0001, at + 0.18);
    bow.connect(bp).connect(bg).connect(playSound.musicIn || playSound.music);
    bow.start(at, Math.random(), 0.2);
  } else if (name === "bells") {                               // bells, a glockenspiel: bright, long, inharmonic
    oscs = [tone("sine", f * 2, 0.6), tone("sine", f * 5.4, 0.2), tone("sine", f * 8.9, 0.08)];
    end = env(0.002, 0.002, 2.2);
  } else if (name === "kalimba") {                             // a thumb piano: a soft pluck, a wooden ring
    oscs = [tone("sine", f, 0.8), tone("sine", f * 5.9, 0.12), tone("triangle", f * 2, 0.08)];
    end = env(0.003, 0.003, 0.8);
  } else if (name === "vibraphone") {                          // a vibraphone: its tremolo
    oscs = [tone("sine", f, 0.75), tone("sine", f * 4, 0.1)];
    const trem = ctx.createOscillator(), depth = ctx.createGain();
    trem.frequency.value = 6; depth.gain.value = 0.35;
    trem.connect(depth).connect(out.gain); trem.start(at); extra.push(trem);
    end = env(0.004, 0.004, 1.6);
  } else if (name === "clarinet" || name === "ocarina") {      // woodwinds: hollow, odd harmonics; the ocarina rounder
    const filter = ctx.createBiquadFilter();
    filter.type = "lowpass"; filter.frequency.value = name === "clarinet" ? f * 5 : f * 2.5;
    out.disconnect(); out.connect(filter).connect(playSound.musicIn || playSound.music);
    oscs = name === "clarinet" ? [tone("square", f, 0.35), tone("sine", f, 0.4)] : [tone("sine", f, 0.8), tone("triangle", f, 0.15)];
    end = env(0.05, Math.max(0.1, length * 0.85), 0.18);
    extra.push(vibrato(oscs, name === "clarinet" ? 6 : 10, 0.2));
  } else if (name === "accordion") {                           // reeds a little apart: its shimmer
    const filter = ctx.createBiquadFilter();
    filter.type = "lowpass"; filter.frequency.value = 3200;
    out.disconnect(); out.connect(filter).connect(playSound.musicIn || playSound.music);
    oscs = [tone("sawtooth", f, 0.25, -9), tone("sawtooth", f, 0.25, 9), tone("square", f * 2, 0.08)];
    end = env(0.04, Math.max(0.1, length * 0.9), 0.12, level * 0.8);
  } else if (name === "koto") {                                // plucked silk: a quick bright attack, a slight bend
    const filter = ctx.createBiquadFilter();
    filter.type = "lowpass";
    filter.frequency.setValueAtTime(f * 10, at); filter.frequency.exponentialRampToValueAtTime(f * 2, at + 0.5);
    out.disconnect(); out.connect(filter).connect(playSound.musicIn || playSound.music);
    oscs = [tone("sawtooth", f, 0.45), tone("triangle", f * 2, 0.2)];
    for (const o of oscs) { o.frequency.setValueAtTime(f * 1.012, at); o.frequency.exponentialRampToValueAtTime(f, at + 0.12); }
    end = env(0.002, 0.002, 1.1);
  } else if (name === "choir") {                               // voices: "aah" - a vowel's formants over a soft pad
    const formant = ctx.createBiquadFilter();
    formant.type = "bandpass"; formant.frequency.value = 800; formant.Q.value = 1.4;
    out.disconnect(); out.connect(formant).connect(playSound.musicIn || playSound.music);
    oscs = [tone("sawtooth", f, 0.4, -5), tone("sawtooth", f, 0.4, 5), tone("sawtooth", f * 2, 0.1)];
    end = env(0.35, Math.max(0.3, length * 0.9), 0.6, level * 0.9);
    extra.push(vibrato(oscs, 7, 0.3));
  } else if (name === "chiptune") {                            // an old game console: a square wave, crisp
    oscs = [tone("square", f, 0.3)];
    end = env(0.002, Math.max(0.05, length * 0.7), 0.05, level * 0.7);
  } else if (name === "steeldrum") {                           // a steel pan: bright, its overtones bent
    oscs = [tone("sine", f, 0.6), tone("sine", f * 2.01, 0.3), tone("sine", f * 3.9, 0.12)];
    end = env(0.003, 0.003, 0.7);
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

/* ONE PRESS, ALL QUIET (the user, 2026-11-20: "专门的静音按钮，就是按一下就全部静音"): the same switch as the panel's
   Quiet box - pressed again, as it was. */
function playSoundMute() {
  playSound.volumes.quiet = !playSound.volumes.quiet;
  playSoundLevels();
  playSoundMuteShown();
  const box = document.querySelector("[data-play-quiet]");
  if (box) box.checked = playSound.volumes.quiet;
}
function playSoundMuteShown() {
  const button = document.querySelector("[data-play-mute]");
  if (!button) return;
  const quiet = Boolean(playSound.volumes.quiet);
  button.textContent = quiet ? "🔇" : "🔊";
  button.setAttribute("aria-pressed", String(quiet));
  const words = typeof playWords === "function" ? playWords(quiet ? "Unmute" : "Mute") : (quiet ? "Unmute" : "Mute");
  button.title = words;
  button.setAttribute("aria-label", words);
}

/* -- THE SOUND'S SETTINGS, in the world's corner -------------------------------------------------------------------- */
function playSoundPanel(box) {
  const open = box.querySelector(".side-play-sound");
  if (open) { open.remove(); return; }
  const v = playSound.volumes;
  const panel = document.createElement("div");
  panel.className = "side-play-sound";
  const said = (text) => (typeof playWords === "function" ? playWords(text) : text);     // in Play's own language
  const slider = (key, label) => `<label><span>${escapeHtml(said(label))}</span><input type="range" min="0" max="100"
    value="${Math.round(v[key] * 100)}" data-play-volume="${key}"></label>`;
  // NO QUIET BOX HERE (the user, 2026-11-20: "静音这个按钮可以取消（已经在外面了)"): the mute button beside it.
  panel.innerHTML = `${slider("all", "All")}${slider("music", "Music")}${slider("effects", "Sounds")}`;
  panel.addEventListener("input", (event) => {
    const key = event.target.dataset.playVolume;
    if (key) v[key] = Number(event.target.value) / 100;
    if (event.target.matches("[data-play-quiet]")) v.quiet = event.target.checked;
    playSoundLevels();
    playSoundMuteShown();
  });
  box.appendChild(panel);
}

/* -- WHAT GOES BY, HEARD COMING AND GOING (the user, 2026-11-19: "你的交通工具驶过（飞机，火车，轮船等）要有由远及近的音效") --
   Each vehicle running its line its own sound, made here: a train's rumble and the clack of its wheels, a tram's bell,
   a sleigh's bells, a caravan's camel bells, a coaster's rattle, a ferry's engine and its wash, a sailboat's wash, an
   airship's propellers, a plane's roar - faint far off, louder as it comes, fading as it goes, from the side it is on,
   its pitch a little up coming and down going; a horn or a bell once as it comes near. Ridden, heard as from inside.
   Looked at ten times a second with the rest of the sound (playSoundAhead), each made only while it is in hearing. */
const PLAY_PASSING = {
  train: { rumble: ["lowpass", 170, 0.8, 0.9], beat: [2.4, 0.55], horn: [311, 370], far: 36 },
  tram: { rumble: ["lowpass", 240, 0.8, 0.7], beat: [3.2, 0.45], bell: 1568, far: 30 },
  sleigh: { rumble: ["highpass", 5200, 0.7, 0.25], jingle: true, beat: [5.5, 0.7], far: 28 },
  caravan: { rumble: ["lowpass", 260, 0.7, 0.25], beat: [1.6, 0.6], bell: 523, far: 26 },
  coaster: { rumble: ["bandpass", 900, 1.2, 0.8], beat: [13, 0.6], far: 32 },
  mine_cart: { rumble: ["bandpass", 600, 1.1, 0.8], beat: [9, 0.55], far: 30 },
  ferry: { rumble: ["bandpass", 480, 0.7, 0.6], tone: ["sawtooth", 52, 0.35, 320], horn: [98, 147], far: 40 },
  sailboat: { rumble: ["bandpass", 520, 0.6, 0.65], beat: [0.35, 0.5], far: 30 },
  airship: { rumble: ["lowpass", 380, 0.7, 0.35], tone: ["sawtooth", 72, 0.4, 420], beat: [9, 0.5], far: 44 },
  plane: { rumble: ["lowpass", 1500, 0.6, 1.0], tone: ["sine", 1900, 0.06, 4000], far: 64 },
  metro: { rumble: ["lowpass", 300, 0.9, 0.8], tone: ["sine", 620, 0.05, 1200], beat: [4.2, 0.35], bell: 988, far: 30 },
  bus: { rumble: ["lowpass", 220, 0.8, 0.55], tone: ["sawtooth", 46, 0.25, 260], horn: [392, 494], far: 26 },
  taxi: { rumble: ["lowpass", 320, 0.8, 0.45], tone: ["sawtooth", 62, 0.18, 300], horn: [523], far: 22 },
  helicopter: { rumble: ["lowpass", 260, 0.7, 0.6], tone: ["sawtooth", 55, 0.3, 500], beat: [11, 0.75], far: 50 },
  spaceship: { rumble: ["lowpass", 700, 0.5, 1.0], tone: ["sine", 140, 0.2, 2000], far: 70 },
  submarine: { rumble: ["lowpass", 160, 0.9, 0.5], tone: ["sine", 440, 0.08, 900], beat: [0.6, 0.4], far: 30 },
  balloon: { rumble: ["bandpass", 900, 0.5, 0.25], beat: [0.25, 0.6], far: 26 },
  rocket: { rumble: ["lowpass", 500, 0.6, 1.0], tone: ["sawtooth", 90, 0.25, 1600], far: 70 },
  ufo: { rumble: ["highpass", 1800, 0.5, 0.2], tone: ["sine", 980, 0.15, 3000], beat: [6, 0.5], far: 40 },
};
const PLAY_PASSING_LEVEL = 0.22, PLAY_SOUND_SPEED = 22;   // the sound's speed, in tiles a second: what bends its pitch
function playPassingSpec(line) {
  return PLAY_PASSING[line.look] || PLAY_PASSING[line.kind] || (line.kind === "road" ? PLAY_PASSING.bus : PLAY_PASSING.train);
}
function playPassingVoice(spec) {
  const ctx = playSound.ctx, now = ctx.currentTime;
  const out = ctx.createGain();
  out.gain.setValueAtTime(0, now);
  const pan = ctx.createStereoPanner ? ctx.createStereoPanner() : null;
  if (pan) out.connect(pan).connect(playSound.effects); else out.connect(playSound.effects);
  const body = ctx.createGain();                       // the beat (wheels, propellers) on all of it
  body.gain.value = 1;
  body.connect(out);
  const voice = { out, pan, body, spec, sources: [], filters: [], tones: [] };
  if (spec.rumble) {
    const [type, f, q, level] = spec.rumble;
    const source = ctx.createBufferSource();
    source.buffer = playSound.noise;
    source.loop = true;
    const filter = ctx.createBiquadFilter();
    filter.type = type; filter.frequency.value = f; filter.Q.value = q;
    const gain = ctx.createGain();
    gain.gain.value = level;
    source.connect(filter).connect(gain).connect(body);
    source.start(now, Math.random() * 1.5);
    voice.sources.push(source);
    voice.filters.push([filter, f]);
  }
  if (spec.tone) {
    const [wave, f, level, lp] = spec.tone;
    const osc = ctx.createOscillator();
    osc.type = wave; osc.frequency.value = f;
    const filter = ctx.createBiquadFilter();
    filter.type = "lowpass"; filter.frequency.value = lp;
    const gain = ctx.createGain();
    gain.gain.value = level;
    osc.connect(filter).connect(gain).connect(body);
    osc.start(now);
    voice.sources.push(osc);
    voice.tones.push([osc, f]);
    voice.filters.push([filter, lp]);
  }
  if (spec.beat) {
    const [rate, depth] = spec.beat;
    const lfo = ctx.createOscillator();
    lfo.type = "square"; lfo.frequency.value = rate;
    const amount = ctx.createGain();
    amount.gain.value = depth / 2;
    body.gain.value = 1 - depth / 2;
    lfo.connect(amount).connect(body.gain);
    lfo.start(now);
    voice.sources.push(lfo);
    voice.tones.push([lfo, rate]);
  }
  return voice;
}
function playPassingLetGo(voice) {
  const ctx = playSound.ctx, now = ctx.currentTime;
  voice.out.gain.setTargetAtTime(0, now, 0.2);
  setTimeout(() => {
    for (const one of voice.sources) { try { one.stop(); } catch (_err) { /* stopped */ } }
    try { voice.out.disconnect(); } catch (_err) { /* gone */ }
  }, 1200);
}
/* A bell, a horn, a sleigh's bells: once, at the vehicle's loudness. */
function playPassingCall(spec, level, k) {
  const ctx = playSound.ctx, now = ctx.currentTime;
  const ring = (f, at, length, type, peak) => {
    const osc = ctx.createOscillator(), gain = ctx.createGain();
    osc.type = type; osc.frequency.value = f * k;
    gain.gain.setValueAtTime(0.0001, at);
    gain.gain.exponentialRampToValueAtTime(0.0001 + peak, at + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, at + length);
    osc.connect(gain).connect(playSound.effects);
    osc.start(at); osc.stop(at + length + 0.05);
  };
  if (spec.horn) {                                     // two notes together, held, then a second blast
    for (const [start, length] of [[0, 0.55], [0.7, 0.9]]) for (const f of spec.horn) ring(f, now + start, length, "square", 0.05 * level);
  } else if (spec.bell) {
    for (const start of [0, 0.32]) { ring(spec.bell, now + start, 0.9, "sine", 0.12 * level); ring(spec.bell * 2.76, now + start, 0.4, "sine", 0.04 * level); }
  }
}
function playSoundPassing() {
  const ctx = playSound.ctx;
  if (!ctx || ctx.state !== "running" || typeof playLines !== "function" || !play.on || !play.map) {
    if (playSound.passing) { for (const voice of playSound.passing.values()) playPassingLetGo(voice); playSound.passing.clear(); }
    return;
  }
  playSound.passing = playSound.passing || new Map();
  const nowMs = performance.now(), now = ctx.currentTime;
  const seen = new Set();
  playLines().forEach((line, n) => {
    const key = `${n}:${line.kind}:${line.look || ""}`;
    const spec = playPassingSpec(line);
    const riding = Boolean(play.ride && play.ride.line === line);
    // Where its vehicle is: as it is drawn (play-act.js playLinesDraw) - the engine's place along its line.
    const at = riding ? [play.me.x, play.me.y] : playLineAt(line, (nowMs / 1000) * PLAY_LINE_SPEED[line.kind] * 0.35 + line.length * 0.37);
    const dx = at[0] - play.me.x, dy = at[1] - play.me.y, dist = Math.hypot(dx, dy);
    let voice = playSound.passing.get(key);
    if (dist > spec.far + 6) { if (voice) { playPassingLetGo(voice); playSound.passing.delete(key); } return; }
    seen.add(key);
    if (!voice) { voice = playPassingVoice(spec); voice.dist = dist; voice.at = nowMs; playSound.passing.set(key, voice); }
    // Coming or going: the pitch bent by how fast it nears - as the sound of a thing going by is.
    const seconds = Math.max(0.05, (nowMs - voice.at) / 1000);
    const nearing = riding ? 0 : (voice.dist - dist) / seconds;
    const k = Math.max(0.75, Math.min(1.3, PLAY_SOUND_SPEED / (PLAY_SOUND_SPEED - Math.max(-PLAY_SOUND_SPEED / 2, Math.min(PLAY_SOUND_SPEED / 2, nearing)))));
    voice.dist = dist; voice.at = nowMs;
    const fade = Math.max(0, 1 - dist / spec.far);
    const level = riding ? 0.55 : fade * fade / (1 + (dist / 9) ** 2) * 1.6;
    voice.out.gain.setTargetAtTime(PLAY_PASSING_LEVEL * Math.min(1, level), now, 0.15);
    if (voice.pan) voice.pan.pan.setTargetAtTime(riding ? 0 : Math.max(-0.9, Math.min(0.9, dx / 10)), now, 0.15);
    for (const [osc, f] of voice.tones) osc.frequency.setTargetAtTime(f * k, now, 0.15);
    for (const [filter, f] of voice.filters) filter.frequency.setTargetAtTime(f * k, now, 0.15);
    for (const source of voice.sources) if (source.playbackRate) source.playbackRate.setTargetAtTime(k, now, 0.15);
    // Its call once as it comes near (a horn, a bell) - not again for a while.
    if (!riding && nearing > 0.5 && dist < 14 && nowMs - (voice.called || 0) > 25000) {
      voice.called = nowMs;
      playPassingCall(spec, Math.min(1, level * 1.4), k);
    }
    if (spec.jingle && level > 0.05 && Math.random() < 0.6) {       // the sleigh's bells, shaken as it runs
      for (let n2 = 0; n2 < 3; n2 += 1) {
        const osc = ctx.createOscillator(), gain = ctx.createGain(), t = now + Math.random() * 0.1;
        osc.type = "sine"; osc.frequency.value = (2600 + Math.random() * 900) * k;
        gain.gain.setValueAtTime(0.0001, t);
        gain.gain.exponentialRampToValueAtTime(0.0001 + 0.03 * Math.min(1, level), t + 0.005);
        gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.12);
        osc.connect(gain).connect(playSound.effects);
        osc.start(t); osc.stop(t + 0.15);
      }
    }
  });
  for (const [key, voice] of playSound.passing) if (!seen.has(key)) { playPassingLetGo(voice); playSound.passing.delete(key); }
}

/* -- AN ANIMAL CHASED, CRYING ITS OWN CRY (the user, 2026-11-19: "动物也是要有被追赶的音效（不同动物不一样）") -----------
   Made here, as all the rest: a cat's meow, a dog's barks, a hen's clucks, a cow's moo, a sheep's baa, a goat's bleat,
   a lion's roar, an owl's hoot, a dolphin's whistle, a robot's beeps... - once as it starts to run from the walker, not
   again for a little while; from the side it is on. Each a few syllables: a wave, its pitch gliding through the given
   notes, through a filter shaping the voice, with a vibrato; or a puff of noise. */
const PLAY_CRIES = {
  cat: [{ wave: "sawtooth", f: [520, 820, 470], len: 0.55, band: 1400, q: 2, vib: [6, 14] }],
  // CRISP (the user, 2026-11-19: "狗的叫声：汪汪 不够清脆"): each bark a sharp start - a click of breath - its pitch
  // falling fast through a bright mouth, short; two, quick.
  dog: [{ noise: true, band: 2600, q: 1.5, len: 0.025, level: 1.2 },
        { wave: "sawtooth", f: [760, 560, 330], len: 0.085, band: 1500, q: 2.2, level: 1.4 },
        { at: 0.16, noise: true, band: 2700, q: 1.5, len: 0.025, level: 1.2 },
        { at: 0.16, wave: "sawtooth", f: [800, 590, 340], len: 0.08, band: 1550, q: 2.2, level: 1.4 }],
  chicken: [0, 0.1, 0.2, 0.3].map((at) => ({ at, wave: "square", f: [900, 760], len: 0.06, band: 1500, q: 2 }))
    .concat([{ at: 0.45, wave: "sawtooth", f: [600, 1150, 800], len: 0.32, band: 1600, q: 2 }]),
  cow: [{ wave: "sawtooth", f: [150, 178, 118], len: 1.0, band: 480, q: 1.1, vib: [4, 4] }],
  sheep: [{ wave: "sawtooth", f: [330, 305], len: 0.6, band: 1100, q: 1.5, vib: [8, 28] }],
  goat: [{ wave: "sawtooth", f: [480, 445], len: 0.5, band: 1300, q: 1.6, vib: [11, 48] }],
  rabbit: [{ wave: "sine", f: [110, 55], len: 0.08 }, { at: 0.13, wave: "sine", f: [110, 55], len: 0.08 },
           { at: 0.3, wave: "sine", f: [1800, 2500], len: 0.07 }],
  squirrel: [0, 0.055, 0.11, 0.165, 0.22, 0.275].map((at) => ({ at, wave: "square", f: [2300, 2000], len: 0.03, band: 2600, q: 3 })),
  deer: [{ wave: "sawtooth", f: [720, 430], len: 0.25, band: 950, q: 1.4 }],
  monkey: [{ wave: "sine", f: [380, 540], len: 0.12 }, { at: 0.18, wave: "sine", f: [380, 540], len: 0.12 },
           { at: 0.36, wave: "sawtooth", f: [620, 1150], len: 0.3, band: 1500, q: 2 }],
  tiger: [{ wave: "sawtooth", f: [115, 150, 80], len: 1.1, band: 320, q: 0.8, vib: [22, 16], level: 1.3 }],
  lion: [{ wave: "sawtooth", f: [100, 140, 70], len: 1.3, band: 300, q: 0.8, vib: [18, 14], level: 1.4 }],
  bear: [{ wave: "sawtooth", f: [88, 98, 70], len: 0.9, band: 260, q: 0.9, vib: [28, 10], level: 1.3 }],
  polarBear: [{ wave: "sawtooth", f: [80, 92, 66], len: 1.0, band: 240, q: 0.9, vib: [26, 10], level: 1.3 }],
  gorilla: [0, 0.11, 0.22, 0.33, 0.44, 0.55].map((at) => ({ at, wave: "sine", f: [95, 60], len: 0.09, level: 1.4 }))
    .concat([{ at: 0.7, wave: "sawtooth", f: [135, 100], len: 0.3, band: 380, q: 1 }]),
  elephant: [{ wave: "sawtooth", f: [430, 640, 390], len: 0.9, band: 1200, q: 3, vib: [5, 30] }],
  giraffe: [{ noise: true, band: 700, q: 1.2, len: 0.18 }, { at: 0.3, wave: "sine", f: [150, 125], len: 0.4 }],
  zebra: [{ wave: "sawtooth", f: [720, 920, 520], len: 0.35, band: 1500, q: 1.8, vib: [14, 60] },
          { at: 0.42, wave: "sawtooth", f: [700, 880, 500], len: 0.3, band: 1500, q: 1.8, vib: [14, 60] }],
  hippo: [0, 0.2, 0.4, 0.6].map((at) => ({ at, wave: "sawtooth", f: [125, 100], len: 0.15, band: 420, q: 1, level: 1.2 })),
  rhino: [{ noise: true, band: 520, q: 1, len: 0.25 }, { at: 0.28, wave: "sawtooth", f: [92, 70], len: 0.3, band: 300, q: 1 }],
  camel: [{ wave: "sawtooth", f: [185, 245, 150], len: 0.8, band: 620, q: 1.4, vib: [9, 22] }],
  croc: [{ noise: true, band: 3200, q: 0.7, len: 0.6 }],
  turtle: [{ noise: true, band: 2600, q: 0.8, len: 0.35, level: 0.5 }],
  dolphin: [0, 0.03, 0.06, 0.09].map((at) => ({ at, noise: true, band: 4000, q: 4, len: 0.012 }))
    .concat([{ at: 0.15, wave: "sine", f: [5000, 9000, 6200], len: 0.35, level: 0.6 }]),
  orca: [{ wave: "sine", f: [1500, 2600, 1200], len: 0.6, level: 0.7 }],
  shark: [{ noise: true, band: 700, q: 0.6, len: 0.35 }],
  fish1: [0, 0.09, 0.18].map((at) => ({ at, wave: "sine", f: [300, 900], len: 0.06, level: 0.6 })),
  eagle: [{ wave: "sawtooth", f: [2600, 2200, 1800], len: 0.5, band: 2800, q: 4, vib: [20, 80] }],
  owl: [{ wave: "sine", f: [430, 385], len: 0.25 }, { at: 0.45, wave: "sine", f: [410, 370], len: 0.42 }],
  wisp: [{ wave: "sine", f: [1760], len: 0.5, level: 0.6 }, { at: 0.12, wave: "sine", f: [2349], len: 0.5, level: 0.5 },
         { at: 0.24, wave: "sine", f: [2637], len: 0.6, level: 0.45 }],
  robot1: [{ wave: "square", f: [880], len: 0.08, band: 3000, q: 0.7 }, { at: 0.12, wave: "square", f: [660], len: 0.08, band: 3000, q: 0.7 },
           { at: 0.24, wave: "square", f: [1320], len: 0.1, band: 3000, q: 0.7 }],
};
for (const [name, like] of [["fish2", "fish1"], ["fish3", "fish1"], ["fish4", "fish1"], ["clownfish", "fish1"],
  ["yellowTang", "fish1"], ["blueTang", "fish1"], ["moorishIdol", "fish1"], ["robot2", "robot1"], ["robot3", "robot1"]]) {
  PLAY_CRIES[name] = PLAY_CRIES[like];
}
/* Its own, else one of its kind by name (a "brownCow" a cow, "kitten" a cat), else a small squeak. */
function playCryOf(name) {
  if (PLAY_CRIES[name]) return PLAY_CRIES[name];
  const lower = String(name || "").toLowerCase();
  for (const known of Object.keys(PLAY_CRIES)) if (lower.includes(known.toLowerCase())) return PLAY_CRIES[known];
  if (/kitten/.test(lower)) return PLAY_CRIES.cat;
  if (/puppy/.test(lower)) return PLAY_CRIES.dog;
  if (/hen|rooster|chick/.test(lower)) return PLAY_CRIES.chicken;
  if (/fish|tang|idol/.test(lower)) return PLAY_CRIES.fish1;
  if (/robot|bot/.test(lower)) return PLAY_CRIES.robot1;
  return [{ wave: "sine", f: [1500, 2200], len: 0.1, level: 0.7 }];
}
function playSoundCry(name, dx = 0, dist = 1) {
  const ctx = playSound.ctx;
  if (!ctx || ctx.state !== "running") return;
  const start = ctx.currentTime + 0.02, near = 1 / (1 + (dist / 4) ** 2);
  const pan = ctx.createStereoPanner ? ctx.createStereoPanner() : null;
  if (pan) { pan.pan.value = Math.max(-0.8, Math.min(0.8, dx / 4)); pan.connect(playSound.effects); }
  const into = pan || playSound.effects;
  const pitch = 0.94 + Math.random() * 0.12;              // never quite the same twice
  for (const one of playCryOf(name)) {
    const at = start + (one.at || 0), len = one.len, peak = 0.09 * (one.level || 1) * near;
    const gain = ctx.createGain();
    gain.gain.setValueAtTime(0.0001, at);
    gain.gain.exponentialRampToValueAtTime(0.0001 + peak, at + Math.min(0.03, len / 4));
    gain.gain.setValueAtTime(0.0001 + peak, at + len * 0.7);
    gain.gain.exponentialRampToValueAtTime(0.0001, at + len);
    let head;
    if (one.noise) {
      const source = ctx.createBufferSource();
      source.buffer = playSound.noise;
      head = source;
      source.start(at, Math.random() * 1.5, len + 0.05);
    } else {
      const osc = ctx.createOscillator();
      osc.type = one.wave;
      const f = one.f.map((v) => v * pitch);
      osc.frequency.setValueAtTime(f[0], at);
      f.slice(1).forEach((v, i) => osc.frequency.linearRampToValueAtTime(v, at + len * (i + 1) / (f.length - 1 || 1)));
      if (one.vib) {
        const lfo = ctx.createOscillator(), depth = ctx.createGain();
        lfo.frequency.value = one.vib[0]; depth.gain.value = one.vib[1];
        lfo.connect(depth).connect(osc.frequency);
        lfo.start(at); lfo.stop(at + len + 0.05);
      }
      head = osc;
      osc.start(at); osc.stop(at + len + 0.05);
    }
    if (one.band) {
      const filter = ctx.createBiquadFilter();
      filter.type = "bandpass"; filter.frequency.value = one.band * pitch; filter.Q.value = one.q || 1;
      head.connect(filter).connect(gain);
    } else head.connect(gain);
    gain.connect(into);
  }
}
