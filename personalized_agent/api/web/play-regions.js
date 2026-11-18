/* THE WORLD'S REGIONS, DRAWN (agent/playregions.py says what they are; the user, 2026-11-17: "每个人的世界 ... 一眼看上去就很
   不同"; "当模型对用户数据比较多之后，可以用不同风格的模板 ... 几个不同风格的中间用比较smooth的连接，确保衔接不要突兀 ... 布局，style,
   内容主题，都有不同的setting").

   Each region's six settings, each drawn its own way here, and any combination works:
     biome        its ground, its water, what grows on it, and the path's stones
     layout       its roads (and its river, or its islands)
     palette      its light - a colour the tiles are multiplied by and a wash laid over them
     architecture what its walls are made of (the tile set's four: sandstone, stone, marble, timber)
     theme        what stands beside its roads
     landmark     the one big thing in its middle cell, on a paved square
   BETWEEN TWO REGIONS the ground is one region's or the other's by a chance that moves from one to the other across
   the band between them, and the light changes there smoothly; a road joins each region to the one it came from.
   Everything is chosen from the place itself (a hash of where it is), so the same world is drawn the same each time. */

/* [sheet, column, row] of ground; a piece name -> its tile. */
const PLAY_MORE_GROUND = {
  none: ["#1b1d22"],
  // A toy town's (Kenney's Tiny Town): bright grass, a sandy path, a cobbled street.
  // The void of a land beyond its edge: the deep sea, the open sky, space (its stars drawn by play.js).
  voidSea: ["#0b2a4a"], voidSky: ["#a9dcf7"], voidSpace: ["#06070d"],
  // Snow, lava, cloud: made of colour.
  snow: ["#eef3f8"], snow2: ["#e3eaf2"], lava: ["#e8590c"], lava2: ["#f59f00"], cloud: ["#eef5fc"], cloud2: ["#e2eef9"],
  cracked: ["rpg", 16, 19], cracked2: ["rpg", 17, 20],
  toyGrass: ["town", 0, 0], toyGrass2: ["town", 1, 0], toyGrass3: ["town", 2, 0], toyPath: ["town", 1, 2], toyStone: ["town", 7, 3],
  // A waterfall (Kenney's cave set): its lip, its falling water and its foam, three wide.
  seaRockGround: ["rpg", 7, 1],
  tunnelGlass: ["drawn", 10, 0], coasterTrack: ["drawn", 11, 0], coasterTrackV: ["drawn", 12, 0],
  waterFallTL: ["cave", 0, 14], waterFallT: ["cave", 1, 14], waterFallTR: ["cave", 2, 14], waterFallL: ["cave", 0, 15],
  waterFallM: ["cave", 1, 15], waterFallR: ["cave", 2, 15], waterFallBL: ["cave", 0, 17], waterFallB: ["cave", 1, 17],
  waterFallBR: ["cave", 2, 17],
  sand: ["rpg", 8, 0], sand2: ["rpg", 8, 1], dirt: ["rpg", 6, 0], dirt2: ["rpg", 6, 1], rock: ["rpg", 7, 0],
  rock2: ["rpg", 7, 1], cobble: ["rpg", 9, 0], cobble2: ["rpg", 9, 1], autumn: ["rpg", 0, 18], autumn2: ["rpg", 1, 19],
  magic: ["rpg", 0, 21], magic2: ["rpg", 1, 22], water: ["rpg", 0, 0], water2: ["rpg", 1, 0], bridge: ["rpg", 5, 2],
  paving: ["rpg", 7, 2], cityGrass: ["city", 0, 24], cityGrass2: ["city", 1, 24], pavement: ["city", 2, 24],
  pavement2: ["city", 3, 24], asphalt: ["#3f4046"], caveFloor: ["cave", 16, 12], caveFloor2: ["cave", 17, 13],
  caveGrey: ["cave", 16, 10], caveSand: ["cave", 16, 14], field: ["city", 4, 24],
};
/* [sheet, column, row, width, height] of what stands about. */
const PLAY_SCENERY = {
  roundTree: ["rpg", 13, 10, 1, 1], smallTree: ["rpg", 13, 9, 1, 1], autumnTree: ["rpg", 14, 10, 1, 1],
  autumnSmall: ["rpg", 14, 9, 1, 1], darkTree: ["rpg", 15, 10, 1, 1], pine: ["rpg", 16, 10, 1, 1],
  autumnPine: ["rpg", 17, 10, 1, 1], darkPine: ["rpg", 18, 10, 1, 1], smallPine: ["rpg", 16, 9, 1, 1],
  bush: ["rpg", 19, 9, 1, 1], orangeBush: ["rpg", 20, 9, 1, 1], greenBush: ["rpg", 21, 9, 1, 1],
  cactus: ["rpg", 22, 9, 1, 1], cactusSmall: ["rpg", 22, 10, 1, 1], fruitTree: ["rpg", 23, 10, 1, 2],
  berryBush: ["rpg", 24, 10, 1, 1], flowerBush: ["rpg", 25, 10, 1, 1], orangeFruit: ["rpg", 26, 10, 1, 1],
  deadTree: ["rpg", 27, 9, 1, 1], blueFlower: ["rpg", 28, 9, 1, 1], redFlower: ["rpg", 29, 9, 1, 1],
  purpleFlower: ["rpg", 30, 9, 1, 1], whiteFlower: ["rpg", 31, 9, 1, 1], mushroom: ["rpg", 48, 3, 1, 1],
  mushroom2: ["rpg", 48, 5, 1, 1], brownRock: ["rpg", 54, 19, 1, 1], brownRock2: ["rpg", 55, 20, 1, 1],
  greyRock: ["rpg", 54, 21, 1, 1], greyRock2: ["rpg", 55, 21, 1, 1], mossRock: ["rpg", 54, 22, 1, 1],
  seaRock: ["rpg", 56, 21, 1, 1], cityTree: ["city", 31, 12, 1, 1], cityTree2: ["city", 33, 12, 1, 1],
  hydrant: ["city", 15, 14, 1, 1], bin: ["city", 15, 13, 1, 1], mailbox: ["city", 12, 16, 1, 1],
  caveRock: ["cave", 0, 0, 1, 1], caveRock2: ["cave", 4, 1, 1, 1], caveRock3: ["cave", 5, 1, 1, 1],
  ore: ["cave", 14, 12, 1, 1], ore2: ["cave", 15, 12, 1, 1], ore3: ["cave", 15, 13, 1, 1], caveShroom: ["cave", 1, 3, 1, 1],
  signpost: ["rpg", 19, 0, 1, 1], signpost2: ["rpg", 20, 0, 1, 1], barrel: ["rpg", 23, 0, 1, 1], barrel2: ["rpg", 24, 0, 1, 1],
  crate: ["rpg", 25, 0, 1, 1], anvil: ["rpg", 15, 0, 1, 1], lamp: ["rpg", 15, 7, 1, 1], torch: ["rpg", 17, 7, 1, 1],
  bench: ["rpg", 18, 4, 1, 1], pennant: ["rpg", 53, 0, 1, 1], shield: ["rpg", 51, 0, 1, 1], gold: ["rpg", 41, 11, 1, 1],
  rail: ["rpg", 44, 18, 1, 1], fence: ["rpg", 47, 23, 1, 1], fence2: ["rpg", 48, 23, 1, 1], scroll: ["rpg", 43, 15, 1, 1],
  campfire: ["rpg", 14, 8, 1, 1], tentGreen: ["rpg", 46, 10, 2, 2], tentWhite: ["rpg", 48, 10, 2, 2],
  statue: ["rpg", 50, 10, 1, 2], fountain: ["city", 26, 4, 3, 3], pondBig: ["rpg", 2, 0, 3, 3], stall: ["city", 34, 15, 1, 1],
  stall2: ["city", 35, 15, 1, 1], crystal: ["cave", 14, 11, 1, 1], streetLamp: ["urban", 7, 6, 1, 2],
  gasLamp: ["urban", 0, 6, 1, 2], torchPost: ["rpg", 17, 7, 1, 1], brazier: ["rpg", 14, 8, 1, 1],
  // Tiny Creatures (CC0): a zoo's animals, a sea's fish, pets, robots; Tiny Farm's; Pixel Shmup's ships (CC0).
  lion: ["creatures", 6, 15, 1, 1], tiger: ["creatures", 7, 15, 1, 1], elephant: ["creatures", 8, 15, 1, 1],
  giraffe: ["creatures", 9, 15, 1, 1], zebra: ["creatures", 5, 15, 1, 1], bear: ["creatures", 3, 16, 1, 1],
  polarBear: ["creatures", 4, 16, 1, 1], gorilla: ["creatures", 5, 16, 1, 1], monkey: ["creatures", 7, 16, 1, 1],
  rhino: ["creatures", 0, 17, 1, 1], hippo: ["creatures", 1, 17, 1, 1], camel: ["creatures", 9, 12, 1, 1],
  croc: ["creatures", 8, 14, 1, 1], turtle: ["creatures", 9, 14, 1, 1], owl: ["creatures", 1, 13, 1, 1], eagle: ["creatures", 4, 13, 1, 1],
  shark: ["creatures", 2, 17, 1, 1], dolphin: ["creatures", 3, 17, 1, 1], orca: ["creatures", 4, 17, 1, 1],
  fish1: ["creatures", 0, 6, 1, 1], fish2: ["creatures", 1, 6, 1, 1], fish3: ["creatures", 3, 6, 1, 1], fish4: ["creatures", 4, 6, 1, 1],
  smallFish: ["creatures", 0, 7, 1, 1], cat: ["creatures", 4, 9, 1, 1], dog: ["creatures", 9, 16, 1, 1], rabbit: ["creatures", 7, 17, 1, 1],
  squirrel: ["creatures", 5, 17, 1, 1], chicken: ["creatures", 0, 15, 1, 1], cow: ["creatures", 1, 15, 1, 1], sheep: ["creatures", 3, 15, 1, 1],
  goat: ["creatures", 2, 15, 1, 1], robot1: ["creatures", 0, 8, 1, 1], robot2: ["creatures", 1, 8, 1, 1], robot3: ["creatures", 2, 8, 1, 1],
  wisp: ["creatures", 9, 8, 1, 1], sunflower: ["farm", 11, 6, 1, 1], pumpkinCrate: ["farm", 11, 0, 1, 1],
  pillar: ["rpg", 13, 19, 1, 2], brokenPillar: ["rpg", 14, 19, 1, 2], greyPillar: ["rpg", 20, 19, 1, 2],
  ship: ["ships", 0, 0, 2, 2], ship2: ["ships", 2, 0, 2, 2], ship3: ["ships", 4, 0, 2, 2], deer: ["creatures", 1, 16, 1, 1],
  giantCat: ["creatures", 4, 9, 3, 3, true], giantDog: ["creatures", 9, 16, 3, 3, true],
  // Redrawn and new (2026-11-17, "你做好点"): the app's own (drawn), fish (sea), Valentine's and a jack-o'-lantern
  // (love), a graveyard in colour (yard) - sources in play/ASSETS.txt.
  lantern: ["drawn", 0, 0, 1, 1],
  firecrackers: ["drawn", 1, 0, 1, 1],
  fuSign: ["drawn", 2, 0, 1, 1],
  redEnvelope: ["drawn", 3, 0, 1, 1],
  balloons: ["drawn", 4, 0, 1, 1],
  queueRail: ["drawn", 5, 0, 1, 1],
  jellyfish: ["drawn", 6, 0, 1, 1],
  coral: ["drawn", 7, 0, 1, 1],
  anemone: ["drawn", 8, 0, 1, 1],
  bubbles: ["drawn", 9, 0, 1, 1],
  coasterCar: ["drawn", 13, 0, 1, 1],
  sparkle: ["drawn", 14, 0, 1, 1],
  seaweed: ["drawn", 15, 0, 1, 2],
  lanternString: ["drawn", 16, 0, 2, 1],
  bunting: ["drawn", 18, 0, 2, 1],
  fishTank: ["drawn", 20, 0, 2, 1],
  bigFishTank: ["drawn", 22, 0, 4, 2],
  coupletDoor: ["drawn", 26, 0, 2, 2],
  firework: ["drawn", 28, 0, 2, 2],
  lionHead: ["drawn", 30, 0, 2, 2],
  plumTree: ["drawn", 32, 0, 2, 2],
  ticketBooth: ["drawn", 34, 0, 2, 2],
  iceCreamStand: ["drawn", 36, 0, 2, 2],
  hotDogStand: ["drawn", 38, 0, 2, 2],
  candyStand: ["drawn", 40, 0, 2, 2],
  coasterStation: ["drawn", 42, 0, 4, 2],
  circusTent: ["drawn", 46, 0, 3, 3],
  heartArch: ["drawn", 49, 0, 3, 3],
  swingShip: ["drawn", 52, 0, 4, 3],
  carousel: ["drawn", 56, 0, 4, 4],
  dropTower: ["drawn", 60, 0, 2, 5],
  ferrisWheel: ["drawn", 62, 0, 5, 5],
  clownfish: ["sea", 0, 0, 1, 1],
  clownfish2: ["sea", 1, 0, 1, 1],
  butterflyfish2: ["sea", 2, 0, 1, 1],
  butterflyfish3: ["sea", 3, 0, 1, 1],
  yellowTang: ["sea", 4, 0, 1, 1],
  blueTang: ["sea", 5, 0, 1, 1],
  moorishIdol: ["sea", 6, 0, 1, 1],
  angelfish: ["sea", 7, 0, 2, 2],
  angelfish2: ["sea", 9, 0, 2, 1],
  parrotfish: ["sea", 11, 0, 2, 1],
  parrotfish2: ["sea", 13, 0, 2, 1],
  koi: ["sea", 15, 0, 2, 1],
  wrasse: ["sea", 17, 0, 1, 1],
  redSnapper: ["sea", 18, 0, 2, 1],
  triggerfish: ["sea", 20, 0, 1, 1],
  boxfish: ["sea", 21, 0, 1, 1],
  grouper: ["sea", 22, 0, 2, 1],
  redHeart: ["love", 0, 0, 1, 1],
  pinkHeart: ["love", 1, 0, 1, 1],
  goldHeart: ["love", 2, 0, 1, 1],
  blueHeart: ["love", 3, 0, 1, 1],
  rose: ["love", 4, 0, 1, 1],
  roseVine: ["love", 5, 0, 1, 1],
  lovePotion: ["love", 6, 0, 1, 1],
  loveLetter: ["love", 7, 0, 1, 1],
  bow: ["love", 8, 0, 1, 1],
  jackOLantern: ["love", 9, 0, 1, 1],
  runeStone: ["yard", 0, 0, 1, 1],
  runeStone2: ["yard", 1, 0, 1, 1],
  runeStone3: ["yard", 2, 0, 1, 1],
  greyStone: ["yard", 3, 0, 1, 1],
  greyStone2: ["yard", 4, 0, 1, 1],
  greyStone3: ["yard", 5, 0, 1, 1],
  mossRock2: ["yard", 6, 0, 1, 1],
  mossRock3: ["yard", 7, 0, 1, 1],
  greyBoulder: ["yard", 8, 0, 1, 1],
  greyBoulder2: ["yard", 9, 0, 1, 1],
  graveTall: ["yard", 10, 0, 1, 1],
  graveUrn: ["yard", 11, 0, 1, 2],
  graveBlock: ["yard", 12, 0, 1, 1],
  graveBlock2: ["yard", 13, 0, 1, 1],
  graveBlock3: ["yard", 14, 0, 1, 1],
  graveBlock4: ["yard", 15, 0, 1, 1],
  graveSquare: ["yard", 16, 0, 1, 1],
  graveSquare2: ["yard", 17, 0, 1, 1],
  graveSquare3: ["yard", 18, 0, 1, 1],
  graveSquare4: ["yard", 19, 0, 1, 1],
  graveSlab: ["yard", 20, 0, 1, 1],
  goldBush: ["yard", 21, 0, 1, 2],
  paleBush: ["yard", 22, 0, 1, 1],
  wreath: ["yard", 23, 0, 1, 1],
  wreath2: ["yard", 24, 0, 1, 1],
  wreath3: ["yard", 25, 0, 1, 1],
  graveCandles: ["yard", 26, 0, 1, 2],
  graveCandle: ["yard", 27, 0, 1, 1],
  graveCandle2: ["yard", 28, 0, 1, 1],
  redCandle: ["yard", 29, 0, 1, 1],
  redCandle2: ["yard", 30, 0, 1, 1],
  ropePost: ["yard", 31, 0, 1, 1],
  fern: ["yard", 32, 0, 1, 3],
  tallFern: ["yard", 33, 0, 1, 3],
  gloomTree: ["yard", 34, 0, 1, 2],
  gloomTree2: ["yard", 35, 0, 1, 2],
  gloomTree3: ["yard", 36, 0, 1, 2],
  gloomTree4: ["yard", 37, 0, 1, 2],
  gloomTree5: ["yard", 38, 0, 1, 2],
  gloomTree6: ["yard", 39, 0, 1, 2],
  gloomTree7: ["yard", 40, 0, 1, 2],
  gloomTree8: ["yard", 41, 0, 1, 2],
  heart: ["love", 1, 0, 1, 1],
  roses: ["love", 4, 0, 1, 1],
  giantHeart: ["love", 0, 0, 3, 3, true],
  giantJackOLantern: ["love", 10, 0, 3, 3, true],
  // Where people work, learn and live; a city's towers.
  whiteboard: ["drawn", 89, 0, 2, 1],
  schoolDesk: ["drawn", 91, 0, 1, 1],
  labBench: ["drawn", 92, 0, 2, 1],
  microscope: ["drawn", 94, 0, 1, 1],
  computer: ["drawn", 95, 0, 1, 1],
  cubicle: ["drawn", 96, 0, 2, 2],
  waterCooler: ["drawn", 98, 0, 1, 2],
  printer: ["drawn", 99, 0, 1, 1],
  lockers: ["drawn", 100, 0, 2, 2],
  bunkBed: ["drawn", 102, 0, 2, 2],
  kitchenette: ["drawn", 104, 0, 2, 1],
  serverRack: ["drawn", 106, 0, 1, 2],
  towerGlass: ["drawn", 107, 0, 2, 5],
  towerBrick: ["drawn", 109, 0, 2, 4],
  towerTall: ["drawn", 111, 0, 3, 6],
  // Travel (play-act.js): its vehicles and stops.
  trainEngine: ["drawn", 67, 0, 2, 1],
  trainCarriage: ["drawn", 69, 0, 2, 1],
  trainStation: ["drawn", 71, 0, 3, 2],
  ferry: ["drawn", 74, 0, 3, 2],
  pier: ["drawn", 77, 0, 2, 1],
  airplane: ["drawn", 79, 0, 3, 3],
  helipad: ["drawn", 82, 0, 2, 2],
  airship: ["drawn", 84, 0, 3, 2],
  skyDock: ["drawn", 87, 0, 2, 2],
  // A graveyard's and a haunted house's (haunted graveyard tileset, 1-Bit Graveyard Pack - CC0); festivals' (Holiday
  // Expansion Set, Chinese Icons - CC0); the app's own drawn (scripts/play_drawn_sprites.py); a botanic garden's (Tiny
  // Farm - CC0).
  hauntedStone: ["grave", 0, 0, 1, 1],
  hauntedCross: ["grave", 1, 0, 1, 1],
  hauntedTree: ["grave", 2, 0, 1, 2],
  hauntedTree2: ["grave", 3, 0, 1, 2],
  hauntedVine: ["grave", 4, 0, 1, 2],
  hauntedLamp: ["grave", 5, 0, 1, 2],
  ironFence: ["grave", 6, 0, 2, 2],
  hauntedMist: ["grave", 8, 0, 2, 1],
  candle: ["grave", 10, 0, 1, 1],
  candles: ["grave", 11, 0, 1, 1],
  graveStatue: ["grave", 12, 0, 1, 1],
  graveStatue2: ["grave", 13, 0, 1, 1],
  graveStatue3: ["grave", 14, 0, 1, 1],
  deadWeed: ["grave", 15, 0, 1, 1],
  deadWeed2: ["grave", 16, 0, 1, 1],
  graveRocks: ["grave", 17, 0, 1, 1],
  coffin: ["grave", 18, 0, 2, 2],
  vault: ["grave", 20, 0, 2, 2],
  vault2: ["grave", 22, 0, 2, 2],
  cypress: ["grave", 24, 0, 2, 2],
  mausoleum: ["grave", 26, 0, 4, 4],
  mausoleum2: ["grave", 30, 0, 4, 4],
  angelStatue: ["grave", 34, 0, 4, 4],
  skullStatue: ["grave", 38, 0, 4, 4],
  present: ["festive", 0, 0, 1, 1],
  present2: ["festive", 1, 0, 1, 1],
  present3: ["festive", 2, 0, 1, 1],
  present4: ["festive", 3, 0, 1, 1],
  xmasTree: ["festive", 4, 0, 1, 1],
  xmasTree2: ["festive", 5, 0, 1, 1],
  sleigh: ["festive", 6, 0, 2, 1],
  reindeer: ["festive", 8, 0, 1, 1],
  campfire: ["festive", 9, 0, 1, 1],
  santa: ["festive", 10, 0, 1, 1],
  goldIngot: ["festive", 11, 0, 1, 1],
  scroll: ["festive", 12, 0, 1, 1],
  peach: ["festive", 13, 0, 1, 1],
  blossom: ["festive", 14, 0, 1, 1],
  panda: ["festive", 15, 0, 1, 1],
  yinYang: ["festive", 16, 0, 1, 1],
  pumpkin: ["festive", 17, 0, 1, 1],
  pumpkin2: ["festive", 18, 0, 1, 1],
  pumpkin3: ["festive", 19, 0, 1, 1],
  giantXmasTree: ["festive", 4, 0, 3, 3, true],
  greenhouse: ["farm", 9, 7, 3, 4],
  barn: ["farm", 6, 7, 3, 4],
  cabbage: ["farm", 6, 4, 1, 1],
  tomato: ["farm", 8, 3, 1, 1],
  corn: ["farm", 8, 2, 1, 1],
  carrot: ["farm", 8, 0, 1, 1],
  turnip: ["farm", 8, 1, 1, 1],
  wheat: ["farm", 6, 5, 1, 1],
  seedling: ["farm", 7, 6, 1, 1],
  wateringCan: ["farm", 0, 7, 1, 1],
  hayBale: ["farm", 0, 8, 1, 1],
  // Kenney's Tiny Town, RPG Urban Pack and Tiny Dungeon (CC0).
  // The tall trees two tiles high, crown over trunk (the user, 2026-11-17: "这种树是不是也是没下好的" - one
  // tile of each was cut, the top or the bottom); the small ones whole in one.
  townPine: ["town", 4, 0, 1, 2], townAutumnPine: ["town", 3, 0, 1, 2], townBush: ["town", 5, 0, 1, 1], townTree: ["town", 4, 2, 1, 1],
  townAutumnTree: ["town", 3, 2, 1, 1], townSmallTree: ["town", 4, 2, 1, 1], townMushrooms: ["town", 5, 2, 1, 1],
  townSprout: ["town", 5, 1, 1, 1], beehive: ["town", 10, 7, 1, 1], sign: ["town", 11, 6, 1, 1], log: ["town", 10, 8, 1, 1],
  pot: ["town", 11, 8, 1, 1], target: ["town", 11, 7, 1, 1], uBench: ["urban", 0, 10, 1, 1], parkBench: ["urban", 3, 14, 1, 1],
  uTrash: ["urban", 9, 9, 1, 1], uTrash2: ["urban", 10, 10, 1, 1], uHydrant: ["urban", 8, 10, 1, 1], uMailbox: ["urban", 8, 11, 1, 1],
  planter: ["urban", 6, 10, 1, 1], planter2: ["urban", 7, 10, 1, 1], uTreeSmall: ["urban", 22, 9, 1, 1],
  uTreeSmall2: ["urban", 22, 10, 1, 1], uAutumnSmall: ["urban", 22, 13, 1, 1], uTree: ["urban", 16, 8, 1, 2],
  uAutumnTree: ["urban", 16, 11, 1, 2], barrier: ["urban", 5, 8, 1, 1], dBarrel: ["dungeon", 6, 5, 1, 1],
  parasol: ["city", 34, 14, 1, 1], parasol2: ["city", 35, 14, 1, 1],
};
/* THE LANDS: ground (a few, by chance), its path and street, what grows (and how thick), water (how much). */
const PLAY_BIOMES = {
  meadow: { ground: ["grass", "grass2", "grass2", "grass2"], path: ["dirt", "dirt2"], street: "cobble",
            grows: ["roundTree", "smallTree", "bush", "redFlower", "whiteFlower", "blueFlower", "greenBush", "townTree", "townBush",
                    "townSmallTree", "townSprout", "townMushrooms"], thick: 0.07, water: 0.04,
            tune: "garden", ambience: "birds" },
  autumn: { ground: ["autumn", "autumn2", "autumn2", "dirt"], path: ["dirt", "dirt2"], street: "cobble",
            grows: ["autumnTree", "autumnSmall", "autumnPine", "orangeBush", "brownRock", "townAutumnPine", "townAutumnTree",
                    "uAutumnSmall", "uAutumnTree"], thick: 0.09, water: 0.03,
            tune: "home", ambience: "wind" },
  enchanted: { ground: ["magic", "magic2", "magic2", "grass2"], path: ["cobble", "cobble2"], street: "paving",
               grows: ["darkPine", "darkTree", "mushroom", "mushroom2", "purpleFlower", "crystal", "townMushrooms"], thick: 0.09, water: 0.05,
               tune: "library", ambience: "none" },
  desert: { ground: ["sand", "sand2", "sand2", "rock"], path: ["rock", "rock2"], street: "paving",
            grows: ["cactus", "cactusSmall", "brownRock", "brownRock2", "deadTree"], thick: 0.035, water: 0.0,
            tune: "station", ambience: "wind" },
  seaside: { ground: ["sand", "sand2", "grass2", "grass2"], path: ["bridge"], street: "paving",
             grows: ["smallTree", "seaRock", "greyRock", "greenBush"], thick: 0.03, water: 0.42,
             tune: "cafe", ambience: "water" },
  forest: { ground: ["grass2", "grass", "grass", "dirt2"], path: ["dirt", "dirt2"], street: "cobble",
            grows: ["pine", "darkPine", "darkTree", "smallPine", "roundTree", "mushroom", "bush", "townPine", "uTree", "townTree"],
            thick: 0.22, water: 0.03,
            tune: "study", ambience: "birds" },
  highland: { ground: ["rock", "rock2", "grass2", "grass2"], path: ["cobble", "cobble2"], street: "paving",
              grows: ["greyRock", "greyRock2", "mossRock", "darkPine", "smallPine"], thick: 0.08, water: 0.02,
              tune: "library", ambience: "wind" },
  snow: { ground: ["rock2", "rock", "rock2", "rock2"], path: ["cobble"], street: "paving",
          grows: ["darkPine", "pine", "smallPine", "greyRock"], thick: 0.08, water: 0.03, wash: [255, 255, 255, 0.38],
          tune: "snow", ambience: "wind" },
  city: { ground: ["pavement", "pavement2", "cityGrass", "cityGrass2"], path: ["asphalt"], street: "asphalt",
          grows: ["cityTree", "cityTree2", "hydrant", "bin", "mailbox", "uTreeSmall", "uTreeSmall2", "uTrash", "uHydrant", "uMailbox",
                  "planter", "uTree"], thick: 0.04, water: 0.0,
          tune: "city", ambience: "crowd" },
  farmland: { ground: ["grass", "grass2", "field", "field"], path: ["dirt", "dirt2"], street: "dirt",
              grows: ["berryBush", "orangeFruit", "flowerBush", "fence", "fence2", "beehive", "log", "townSprout", "pot", "sign"],
              thick: 0.1, water: 0.02,
              tune: "garden", ambience: "birds" },
  cavern: { ground: ["caveFloor", "caveFloor2", "caveGrey", "caveGrey"], path: ["caveSand"], street: "caveSand",
            grows: ["caveRock", "caveRock2", "caveRock3", "ore", "ore2", "ore3", "caveShroom"], thick: 0.1, water: 0.05,
            tune: "cavern", ambience: "water" },
  toytown: { ground: ["toyGrass", "toyGrass2", "toyGrass3", "toyGrass2"], path: ["toyPath"], street: "toyStone",
             grows: ["townTree", "townPine", "townBush", "townMushrooms", "townSprout", "beehive", "target", "planter", "planter2",
                     "townAutumnTree", "pot"], thick: 0.1, water: 0.03, tune: "garden", ambience: "birds" },
  underwater: { ground: ["sand", "sand2", "sand2", "seaRockGround"], path: ["paving"], street: "paving",
                grows: ["clownfish", "butterflyfish2", "yellowTang", "blueTang", "moorishIdol", "angelfish", "parrotfish",
                        "wrasse", "koi", "shark", "dolphin", "turtle", "coral", "anemone", "seaweed", "jellyfish", "bubbles",
                        "seaRock", "orca"], thick: 0.1, water: 0.0, wash: [30, 110, 200, 0.32], void: "voidSea",
                tune: "study", ambience: "water" },
  skycity: { ground: ["cloud", "cloud2", "cloud", "cloud2"], path: ["paving"], street: "paving",
             grows: ["whiteFlower", "blueFlower", "townTree", "pennant", "statue", "sparkle", "eagle"], thick: 0.06, water: 0.0,
             void: "voidSky", tune: "screening", ambience: "none" },
  space: { ground: ["rock2", "rock", "rock2", "caveGrey"], path: ["asphalt"], street: "asphalt",
           grows: ["sparkle", "greyRock", "greyRock2", "crystal", "ore", "robot1", "robot2", "robot3", "wisp"], thick: 0.06,
           water: 0.0, wash: [20, 20, 50, 0.3], void: "voidSpace", tune: "cavern", ambience: "none" },
  scifi: { ground: ["pavement", "pavement2", "pavement", "asphalt"], path: ["asphalt"], street: "asphalt",
           grows: ["robot1", "robot2", "robot3", "wisp", "uTrash", "uHydrant", "planter", "uTreeSmall"], thick: 0.06, water: 0.0,
           wash: [60, 160, 220, 0.12], tune: "city", ambience: "crowd" },
  zoo: { ground: ["grass", "grass2", "grass", "dirt"], path: ["dirt", "dirt2"], street: "cobble",
         grows: ["lion", "tiger", "elephant", "giraffe", "zebra", "bear", "polarBear", "gorilla", "monkey", "rhino", "hippo",
                 "camel", "croc", "turtle", "owl", "fence", "fence2", "townTree", "bush"], thick: 0.1, water: 0.04,
         tune: "station", ambience: "birds" },
  primeval: { ground: ["grass2", "grass2", "dirt2", "grass2"], path: ["dirt", "dirt2"], street: "dirt",
              grows: ["gloomTree", "gloomTree2", "gloomTree3", "gloomTree4", "gloomTree5", "gloomTree8", "darkTree", "darkPine",
                      "pine", "fern", "tallFern", "hauntedVine", "mossRock2", "mossRock3", "mossRock", "townMushrooms", "greenBush",
                      "goldBush", "paleBush", "owl", "deer", "bear", "gloomTree6", "gloomTree7", "fern", "tallFern"],
              thick: 0.3, dense: true, water: 0.05, wash: [10, 60, 30, 0.22],
              tune: "garden", ambience: "birds" },
  graveyard: { ground: ["grass2", "dirt2", "grass2", "cracked2"], path: ["dirt", "dirt2"], street: "cobble",
               grows: ["graveBlock", "graveBlock2", "graveBlock3", "graveBlock4", "graveSquare", "graveSquare2", "graveTall",
                       "runeStone", "greyStone3", "hauntedCross", "hauntedTree", "hauntedTree2", "gloomTree5", "gloomTree7",
                       "wreath", "wreath2", "graveCandles", "graveCandle", "redCandle", "fern", "mossRock2", "hauntedMist"], thick: 0.11, water: 0.0, wash: [60, 40, 90, 0.3], tune: "cavern", ambience: "wind" },
  christmas: { ground: ["snow", "snow2", "snow", "snow2"], path: ["cobble", "cobble2"], street: "cobble",
               grows: ["xmasTree", "xmasTree2", "pine", "darkPine", "present", "present2", "present3", "present4", "reindeer",
                       "campfire", "townPine"], thick: 0.08, water: 0.0, tune: "home", ambience: "wind" },
  newyear: { ground: ["grass", "grass2", "grass", "paving"], path: ["paving"], street: "paving",
             grows: ["lantern", "lantern", "lanternString", "firecrackers", "fuSign", "redEnvelope", "plumTree", "blossom",
                     "goldIngot", "scroll", "peach", "panda", "redFlower", "townTree", "firework"], thick: 0.08, water: 0.03, wash: [230, 60, 50, 0.06], tune: "town", ambience: "crowd" },
  halloween: { ground: ["grass2", "dirt", "grass2", "dirt2"], path: ["dirt", "dirt2"], street: "cobble",
               grows: ["jackOLantern", "jackOLantern", "pumpkin", "pumpkin2", "graveCandle", "redCandle", "hauntedTree",
                       "gloomTree6", "deadTree", "graveTall", "townAutumnTree", "wreath3"], thick: 0.1, water: 0.0, wash: [120, 60, 20, 0.18], tune: "cavern",
               ambience: "wind" },
  valentine: { ground: ["grass", "grass", "grass2", "grass"], path: ["paving"], street: "paving",
               grows: ["redHeart", "pinkHeart", "rose", "roseVine", "loveLetter", "bow", "redFlower", "whiteFlower", "flowerBush",
                       "balloons", "bench", "townTree"],
               thick: 0.09, water: 0.04, wash: [255, 120, 170, 0.12], tune: "garden", ambience: "birds" },
  funfair: { ground: ["paving", "cobble", "paving", "grass"], path: ["paving"], street: "paving",
             grows: ["balloons", "bunting", "queueRail", "bench", "streetLamp", "redFlower", "planter", "uTrash", "flowerBush"], thick: 0.06,
             water: 0.0, tune: "city", ambience: "crowd" },
  botanical: { ground: ["grass", "grass2", "grass", "grass2"], path: ["dirt", "dirt2"], street: "cobble",
               grows: ["sunflower", "cabbage", "tomato", "corn", "carrot", "turnip", "seedling", "flowerBush", "redFlower",
                       "whiteFlower", "blueFlower", "fruitTree", "roundTree", "planter", "pot", "wateringCan"], thick: 0.12,
               water: 0.05, tune: "garden", ambience: "birds" },
  ruins: { ground: ["sand", "cracked", "sand2", "cracked2"], path: ["cobble", "cobble2"], street: "paving",
           grows: ["pillar", "brokenPillar", "greyPillar", "statue", "deadTree", "greyRock", "mossRock", "brownRock", "cactusSmall"],
           thick: 0.08, water: 0.0, tune: "library", ambience: "wind" },
  lakeside: { ground: ["grass", "grass2", "grass2", "grass2"], path: ["dirt", "dirt2"], street: "bridge",
              grows: ["roundTree", "smallTree", "greenBush", "whiteFlower", "blueFlower"], thick: 0.06, water: 0.22,
              tune: "garden", ambience: "water" },
};
/* THE LIGHT: multiplied by (r, g, b), then washed over (r, g, b, a). */
const PLAY_PALETTES = {
  day: [[255, 255, 255], [0, 0, 0, 0]], dawn: [[255, 226, 214], [255, 170, 150, 0.1]],
  dusk: [[255, 196, 160], [120, 60, 120, 0.12]], night: [[120, 140, 210], [10, 20, 60, 0.22]],
  spring: [[240, 255, 240], [255, 210, 230, 0.1]], summer: [[255, 250, 220], [255, 230, 120, 0.08]],
  autumn: [[255, 222, 180], [200, 110, 40, 0.08]], winter: [[220, 236, 255], [235, 245, 255, 0.18]],
  pastel: [[255, 255, 255], [255, 240, 250, 0.28]], sepia: [[240, 214, 170], [110, 80, 40, 0.12]],
  misty: [[225, 230, 235], [230, 235, 240, 0.3]],
};
/* What the walls are made of: the tile set's columns moved over to the next material. */
const PLAY_ARCH_SHIFT = { sandstone: 0, stone: 7, marble: 14, timber: 21 };
const PLAY_WALL_PIECES = new Set(["wallTL", "wallT", "wallTR", "wallSide", "faceHi", "faceLo", "wallBL", "wallBR",
                                  "outHiL", "outHiR", "outLoL", "outLoR", "doorL", "doorR"]);
/* What stands beside its roads, by theme. */
const PLAY_THEMES = {
  scholar: ["lamp", "bench", "scroll", "signpost", "parkBench"], maker: ["barrel", "crate", "anvil", "barrel2", "log", "dBarrel"],
  traveller: ["signpost", "signpost2", "rail", "barrel", "sign", "log"], carer: ["redFlower", "whiteFlower", "blueFlower", "flowerBush", "planter"],
  performer: ["torch", "pennant", "shield", "lamp"], athlete: ["fence", "fence2", "pennant", "bench", "target"],
  dreamer: ["crystal", "mushroom", "purpleFlower", "lamp"], explorer: ["brownRock", "deadTree", "signpost", "rail"],
  merchant: ["crate", "barrel", "gold", "barrel2", "planter2", "dBarrel"], quiet: ["bush", "bench", "greenBush", "parkBench"],
};
/* THE LANDMARK in a region's middle: [what, x, y] from the square's middle. */
const PLAY_LANDMARKS = {
  fountain: [["fountain", -1, -1], ["bench", -3, 2], ["bench", 3, 2], ["lamp", -3, -2], ["lamp", 3, -2]],
  statue: [["statue", 0, -1], ["redFlower", -1, 1], ["redFlower", 1, 1], ["lamp", -2, 0], ["lamp", 2, 0]],
  great_tree: [["roundTree", 0, 0], ["roundTree", -1, 0], ["roundTree", 1, 0], ["darkTree", 0, -1], ["darkTree", -1, -1],
               ["darkTree", 1, -1], ["whiteFlower", -2, 1], ["whiteFlower", 2, 1], ["bench", 0, 2]],
  camp: [["tentGreen", -3, -2], ["tentWhite", 2, -2], ["campfire", 0, 0], ["barrel", -2, 1], ["crate", 2, 1]],
  station: [["rail", -4, 0], ["rail", -3, 0], ["rail", -2, 0], ["rail", -1, 0], ["rail", 0, 0], ["rail", 1, 0], ["rail", 2, 0],
            ["rail", 3, 0], ["rail", 4, 0], ["bench", -2, 2], ["bench", 2, 2], ["lamp", 0, 2], ["signpost", 4, -2]],
  stones: [["greyRock", 0, -2], ["greyRock2", 2, -1], ["greyRock", 2, 1], ["greyRock2", 0, 2], ["greyRock", -2, 1],
           ["greyRock2", -2, -1], ["crystal", 0, 0]],
  pond: [["pondBig", -1, -1], ["blueFlower", -3, 0], ["whiteFlower", 3, 0], ["bench", 0, 3]],
  market: [["stall", -3, -1], ["parasol", -3, -2], ["stall2", -1, -1], ["parasol2", -1, -2], ["stall", 1, -1], ["parasol", 1, -2],
           ["stall2", 3, -1], ["parasol2", 3, -2], ["crate", -2, 2], ["barrel", 2, 2], ["gold", 0, 2]],
  crystals: [["crystal", 0, 0], ["ore", -1, -1], ["ore2", 1, -1], ["ore3", -1, 1], ["crystal", 1, 1], ["caveRock", -2, 0],
             ["caveRock2", 2, 0]],
  // A giant pet (the user: "巨大猫猫狗狗"), a rocket on its pad, the ruins of something old.
  mausoleum: [["mausoleum", -2, -4], ["angelStatue", -7, -4], ["skullStatue", 3, -4], ["hauntedStone", -4, 1],
              ["hauntedCross", 3, 1], ["candle", -1, 1], ["candle", 2, 1]],
  fair: [["ferrisWheel", -9, -6], ["carousel", -3, -5], ["circusTent", 2, -5], ["ticketBooth", -1, 0], ["iceCreamStand", 3, 0],
         ["hotDogStand", -6, 0], ["balloons", 6, -1], ["queueRail", -3, 1], ["queueRail", -2, 1]],
  greenhouse: [["greenhouse", -1, -4], ["sunflower", -3, -1], ["sunflower", 3, -1], ["planter", -3, 1], ["planter", 3, 1]],
  christmas_tree: [["giantXmasTree", -1, -3], ["present", -2, 1], ["present2", 2, 1], ["present3", 0, 1], ["reindeer", 3, -1],
                   ["sleigh", -5, -1]],
  lantern_gate: [["coupletDoor", -1, -3], ["lanternString", -4, -4], ["lanternString", 2, -4], ["lionHead", -5, -1],
                 ["plumTree", 3, -2], ["firecrackers", -2, 0], ["fuSign", 1, 0], ["firework", -1, -6], ["redEnvelope", 2, 0]],
  giant_heart: [["heartArch", -1, -4], ["giantHeart", -6, -3], ["rose", -3, 1], ["rose", 3, 1], ["bench", -1, 1],
                ["balloons", 4, -1], ["lovePotion", 2, 1], ["loveLetter", -2, 1]],
  skyline: [["towerTall", -7, -6], ["towerGlass", -3, -5], ["towerBrick", 0, -4], ["towerGlass", 3, -5], ["towerTall", 6, -6],
            ["bench", -2, 1], ["streetLamp", -5, 0], ["streetLamp", 5, 0]],
  giant_pet: [["giantCat", -4, -2], ["giantDog", 1, -2], ["bench", -1, 2], ["redFlower", -3, 2], ["whiteFlower", 3, 2]],
  rocket: [["ship2", -1, -2], ["barrier", -3, 1], ["barrier", 2, 1], ["streetLamp", -3, -1], ["streetLamp", 3, -1]],
  ancient_ruins: [["pillar", -4, -3], ["brokenPillar", -2, -3], ["pillar", 2, -3], ["brokenPillar", 4, -3], ["statue", 0, -2],
                  ["greyRock", -3, 1], ["mossRock", 3, 1]],
};
const PLAY_SPAN = 5, PLAY_BAND = 14, PLAY_BLEND = 20;
/* A DOOR TWO TILES WIDE (the user, 2026-11-17: "你现在房间的门都太小了，弄大点") - agent/playworld.py DOOR_W the same. */
const PLAY_DOOR_W = 2;
/* WHAT MAKES A SOUND of what stands about (play.js playHeard): a fire, water, a crowd; a tree, one in a few, a bird. */
const PLAY_SCENERY_SOUND = { campfire: "fire", fountain: "water", pondBig: "water", stall: "crowd", stall2: "crowd" };
const PLAY_BIRD_TREES = new Set(["roundTree", "smallTree", "autumnTree", "autumnSmall", "darkTree", "pine", "autumnPine",
                                 "darkPine", "fruitTree", "cityTree", "cityTree2"]);
/* WALKED THROUGH, NOT AROUND: what is small enough to step over (the user, 2026-11-17: "刚刚走的时候卡住了"). */
/* BUSHES, WALKED THROUGH (the user, 2026-11-17: "那些灌木丛，能不能变成我走过会有触碰的感觉（并且有音效）"): not in the
   way - each rustles as it is touched (play.js), drawn apart from its block so it can sway. */
const PLAY_RUSTLE = new Set(["bush", "greenBush", "orangeBush", "berryBush", "flowerBush", "orangeFruit"]);
const PLAY_UNDERFOOT = new Set([...PLAY_RUSTLE, "fern", "tallFern", "goldBush", "paleBush", "seaweed", "townMushrooms", "townSprout", "fish1", "fish2", "fish3", "fish4", "smallFish",
                                "sparkle", "wisp", "blueFlower", "redFlower", "purpleFlower", "whiteFlower", "mushroom", "mushroom2",
                                "caveShroom", "ore", "ore2", "ore3", "crystal", "gold", "scroll", "rail", "campfire", "parasol",
                                "parasol2"]);

/* A number in [0, 1) from a place - the same each time. */
function playHash(x, y, salt = 0) {
  let n = (x * 374761393 + y * 668265263 + salt * 1442695041) | 0;
  n = Math.imul(n ^ (n >>> 13), 1274126177);
  return ((n ^ (n >>> 16)) >>> 0) / 4294967296;
}
/* Smooth noise: a hash at every eighth tile, blended between - lakes and coasts, not speckles. */
function playNoise(x, y, salt, step = 8) {
  const gx = Math.floor(x / step), gy = Math.floor(y / step), fx = x / step - gx, fy = y / step - gy;
  const s = (t) => t * t * (3 - 2 * t);
  const a = playHash(gx, gy, salt), b = playHash(gx + 1, gy, salt), c = playHash(gx, gy + 1, salt), d = playHash(gx + 1, gy + 1, salt);
  return (a + (b - a) * s(fx)) + ((c + (d - c) * s(fx)) - (a + (b - a) * s(fx))) * s(fy);
}

function playRegionRect(region) {
  const half = Math.floor(PLAY_SPAN / 2);
  return [region.ox - half * PLAY_CELL[0], region.oy - half * PLAY_CELL[1],
          region.ox + (half + 1) * PLAY_CELL[0], region.oy + (half + 1) * PLAY_CELL[1]];
}
function playRectDistance(rect, x, y) {
  const dx = Math.max(rect[0] - x, 0, x - (rect[2] - 1)), dy = Math.max(rect[1] - y, 0, y - (rect[3] - 1));
  return Math.hypot(dx, dy);
}
function playRegions(world) {
  const regions = (world.regions && world.regions.length) ? world.regions
    : [{ id: "r1", starter: true, name: "Starting Village", ox: 0, oy: 0, biome: "meadow", layout: "village", palette: "day",
         architecture: "sandstone", theme: "quiet", landmark: "" }];
  return regions.map((one) => ({ ...one, rect: playRegionRect(one), land: PLAY_BIOMES[one.biome] || PLAY_BIOMES.meadow }));
}

/* THE WORLD LAID A BLOCK AT A TIME, ONLY WHERE THE WALKER IS (the user, 2026-11-17: "出于优化需要，你不要一次性load整个
   世界，用户走到哪里load附近的内容就行了"). What the world is - its regions, rooms, things, landmarks, the roads between -
   is a small index made once; its tiles are worked out a block of 32 by 32 at a time, when that block is first drawn or
   walked near, and the longest unvisited let go of. Every tile is decided by where it is and the world's index alone -
   never by which block was laid first - so a world is the same however it is walked: what grows stands where a hash
   says, and where none nearer has a stronger claim (a solid thing alone, so there is always a way round it). */
const PLAY_LAY_MARGIN = 4;                // tiles worked round a block, for what depends on its neighbours
const PLAY_LAID_MOST = 96;                // blocks kept laid

/* PATHS, NOT A GRID; DARK WHERE NOTHING IS (the user, 2026-11-17: "你不要这种网状的，你找一下过山车大亨等游戏大家的layout
   设计"; "也看看比如冒险岛的场景layout设计"; "整体地图要像英雄无敌的战争迷雾一样，没有生成的地方就是黑的").

   As a theme park's: each region has a heart - its landmark, or the village's home - and every place is joined to the
   rest by a path that finds its own way round the buildings, winding as the region's layout has it, sharing a path
   already there where it can; the paths make a tree, one more joining it into a loop so few ways end. As a town's hub:
   each region joined to the one it was opened from, heart to heart. Land only round what is there - the places, the
   paths, the landmarks - its edge wavering; past it, dark. Islands: a little land round each place, water between,
   the paths over it as boardwalks. Worked out once for the world (a few routes); the tiles are laid block by block. */
const PLAY_WOBBLE = { grid: 0, village: 2.2, scattered: 3.4, plaza: 1.2, river: 1.6, islands: 1.4 };
const PLAY_LAND_MOST = 9;                  // how far the land may reach from what is there

/* The way from one tile to another, round what stands in the way: cheapest by its tiles' costs (A*). */
function playRoute(blocked, from, to, wobble, salt, paths) {
  const pad = 14;
  const minX = Math.min(from[0], to[0]) - pad, minY = Math.min(from[1], to[1]) - pad;
  const W = Math.abs(from[0] - to[0]) + 2 * pad + 1, H = Math.abs(from[1] - to[1]) + 2 * pad + 1;
  const index = (x, y) => (x - minX) + (y - minY) * W;
  const g = new Float32Array(W * H).fill(Infinity), came = new Int32Array(W * H).fill(-1);
  const heap = [];
  const push = (f, i) => { heap.push([f, i]); let n = heap.length - 1; while (n > 0) { const p = (n - 1) >> 1; if (heap[p][0] <= heap[n][0]) break; [heap[p], heap[n]] = [heap[n], heap[p]]; n = p; } };
  const pop = () => { const top = heap[0]; const last = heap.pop(); if (heap.length) { heap[0] = last; let n = 0; for (;;) { const l = 2 * n + 1, r = l + 1; let m = n; if (l < heap.length && heap[l][0] < heap[m][0]) m = l; if (r < heap.length && heap[r][0] < heap[m][0]) m = r; if (m === n) break; [heap[m], heap[n]] = [heap[n], heap[m]]; n = m; } } return top; };
  const start = index(from[0], from[1]), goal = index(to[0], to[1]);
  g[start] = 0;
  push(0, start);
  while (heap.length) {
    const [, i] = pop();
    if (i === goal) break;
    const x = minX + (i % W), y = minY + Math.floor(i / W);
    for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      const nx = x + dx, ny = y + dy;
      if (nx < minX || ny < minY || nx >= minX + W || ny >= minY + H) continue;
      const key = `${nx},${ny}`;
      if (blocked.has(key) && !(nx === to[0] && ny === to[1])) continue;
      const step = paths.has(key) ? 0.35 : 1 + wobble * playNoise(nx, ny, salt, 5);
      const j = index(nx, ny);
      if (g[i] + step < g[j]) {
        g[j] = g[i] + step;
        came[j] = i;
        push(g[j] + 0.35 * (Math.abs(nx - to[0]) + Math.abs(ny - to[1])), j);
      }
    }
  }
  if (came[goal] < 0 && goal !== start) return [];
  const out = [];
  for (let i = goal; i >= 0; i = came[i]) { out.push([minX + (i % W), minY + Math.floor(i / W)]); if (i === start) break; }
  return out.reverse();
}

function playLay(world) {
  const regions = playRegions(world);
  const byId = Object.fromEntries(regions.map((one, n) => [one.id, n]));
  const regionOf = (zone) => regions[byId[zone.region]] || regions[0];
  // What stands in the way of a path: every building, every open place (a path comes to its edge).
  const blocked = new Set();
  const zones = world.zones.map((zone) => {
    const region = regionOf(zone);
    let rect, out;
    if (PLAY_OUTDOOR[zone.kind]) {
      const [fx, fy, fw, fh] = playFloorOf(zone);
      rect = [fx - 1, fy - 1, fx + fw + 1, fy + fh + 1];
      out = [fx + Math.floor(fw / 2), fy + fh + 1];
    } else {
      const yb = zone.y + 3 + zone.h;
      rect = [zone.x, zone.y, zone.x + zone.w + 2, yb + 3];
      out = [playDoor(zone)[0], yb + 3];
    }
    for (let y = rect[1]; y < rect[3]; y += 1) for (let x = rect[0]; x < rect[2]; x += 1) blocked.add(`${x},${y}`);
    return { zone, region, garden: Boolean(PLAY_OUTDOOR[zone.kind]), door: playDoor(zone)[0], rect, out };
  });
  const landmarks = regions.filter((region) => !region.starter && PLAY_LANDMARKS[region.landmark]).map((region) => {
    const cx = region.ox + Math.floor(PLAY_CELL[0] / 2) - 1, cy = region.oy + Math.floor(PLAY_CELL[1] / 2) - 1;
    return { region, cx, cy, square: [cx - 6, cy - 4, cx + 7, region.oy + PLAY_CELL[1] - 1],
             paving: region.land.street === "asphalt" ? "pavement" : "paving" };
  });
  // Each region's heart: its landmark's square (just below it), or its first place's door.
  const hearts = regions.map((region) => {
    const mark = landmarks.find((one) => one.region === region);
    if (mark) return [mark.cx, mark.square[3]];
    const first = zones.find((one) => one.region === region);
    return first ? first.out : [region.ox + Math.floor(PLAY_CELL[0] / 2), region.oy + PLAY_CELL[1] - 1];
  });
  const paths = new Map();             // "x,y" -> { region: n, wide: bool }
  const doorways = new Set();
  const lay = (route, n, wide) => {
    for (const [x, y] of route) {
      const key = `${x},${y}`;
      if (!paths.has(key)) paths.set(key, { region: n, wide });
      if (wide && !blocked.has(`${x + 1},${y}`) && !paths.has(`${x + 1},${y}`)) paths.set(`${x + 1},${y}`, { region: n, wide });
    }
  };
  regions.forEach((region, n) => {
    const layout = region.layout || "village";
    const wobble = PLAY_WOBBLE[layout] ?? 2;
    const nodes = [hearts[n], ...zones.filter((one) => one.region === region).map((one) => one.out)]
      .filter((one, k, all) => all.findIndex((other) => other[0] === one[0] && other[1] === one[1]) === k);
    // A tree joining them, nearest first (Prim), then one more edge for a loop.
    const inTree = [0], edges = [];
    const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
    while (inTree.length < nodes.length) {
      let best = null;
      for (const a of inTree) for (let b = 0; b < nodes.length; b += 1) {
        if (inTree.includes(b)) continue;
        const d = dist(nodes[a], nodes[b]);
        if (!best || d < best[0]) best = [d, a, b];
      }
      edges.push([best[1], best[2]]);
      inTree.push(best[2]);
    }
    if (nodes.length >= 4) {
      let loop = null;
      for (let a = 0; a < nodes.length; a += 1) for (let b = a + 1; b < nodes.length; b += 1) {
        if (edges.some(([p, q]) => (p === a && q === b) || (p === b && q === a))) continue;
        const d = dist(nodes[a], nodes[b]);
        if (!loop || d < loop[0]) loop = [d, a, b];
      }
      if (loop) edges.push([loop[1], loop[2]]);
    }
    edges.sort((p, q) => dist(nodes[p[0]], nodes[p[1]]) - dist(nodes[q[0]], nodes[q[1]]));
    for (const [a, b] of edges) {
      const route = playRoute(blocked, nodes[b], nodes[a], wobble, 71 + n, paths);
      route.slice(0, 3).forEach(([x, y]) => doorways.add(`${x},${y}`));     // the way from a door: kept clear
      lay(route, n, layout === "plaza" || a === 0);
    }
    // Joined to the region it was opened from, heart to heart - a wide way.
    const from = region.from ? byId[region.from] : undefined;
    if (typeof from === "number" && from !== n) lay(playRoute(blocked, hearts[from], hearts[n], wobble, 97 + n, paths), n, true);
  });
  // THE ROAD OF THEIR LIFE: each place joined to those it follows in time, its signposts along the way.
  const life = playLifeRoad(world, zones, regions, byId, hearts, blocked, paths, doorways, lay);
  // The paths by block, for laying a block quickly.
  const pathBlocks = new Map();
  for (const [key, one] of paths) {
    const [x, y] = key.split(",").map(Number);
    const bk = `${Math.floor(x / PLAY_CHUNK)},${Math.floor(y / PLAY_CHUNK)}`;
    if (!pathBlocks.has(bk)) pathBlocks.set(bk, []);
    pathBlocks.get(bk).push([x, y, one]);
  }
  return { regions, byId, zones, landmarks, items: world.items, laid: new Map(), paths, pathBlocks, doorways, hearts, life, blocked };
}

/* THE ORDER OF THEIR LIFE - the same rule as agent/playplaces.py life_road: each place on the road by when it began,
   following those that had ended before it began; begun while another went on, it follows what that one followed (a
   fork); begun after several ended, it follows all that ended lately (the branches joined). */
const PLAY_LIFE_JOIN_DAYS = 21, PLAY_LIFE_NATURAL = new Set(["lake", "waterfall", "plaza", "grove", "meadow", "mountain", "volcano", "peak"]);
function playLivedOf(world, zone) {
  if (zone.days && zone.days[0]) return [zone.days[0], zone.days[1] || zone.days[0]];
  const growth = (world.growths || []).find((one) => one.at === zone.made && one.days);
  return growth ? growth.days : null;
}
function playLifeOf(world, zones) {
  const day = (text) => Math.floor(Date.parse(`${text}T00:00:00Z`) / 86400000);
  const mains = [];
  for (const one of zones) {
    const zone = one.zone, days = playLivedOf(world, zone);
    if (!days || zone.grounds_of || zone.between || PLAY_LIFE_NATURAL.has(zone.kind)) continue;
    const a = day(days[0]), b = day(days[1]);
    if (Number.isFinite(a) && Number.isFinite(b)) mains.push([a, b, zone.id, one]);
  }
  mains.sort((p, q) => p[0] - q[0] || p[1] - q[1] || (p[2] < q[2] ? -1 : p[2] > q[2] ? 1 : 0));
  const follows = new Map(), hasNext = new Set(), done = [];
  for (const [a, b, id, one] of mains) {
    const before = done.filter((x) => x[1] < a);
    if (!before.length) follows.set(id, []);
    else {
      const latest = Math.max(...before.map((x) => x[1]));
      let ends = before.filter((x) => !hasNext.has(x[2]) && x[1] >= latest - PLAY_LIFE_JOIN_DAYS).map((x) => x[2]);
      if (!ends.length) ends = [before.reduce((p, q) => (q[1] > p[1] ? q : p))[2]];
      follows.set(id, ends);
      ends.forEach((x) => hasNext.add(x));
    }
    done.push([a, b, id, one]);
  }
  return { order: mains.map((x) => x[3]), follows };
}

/* The road laid between them, wide, and its signposts: one a little way along each stretch and one every
   PLAY_POST_EVERY tiles, beside the road, its arrow the way the road goes on, naming the place it goes to; the first
   ("Start of your story") where it begins. */
const PLAY_POST_EVERY = 22;
function playLifeRoad(world, zones, regions, byId, hearts, blocked, paths, doorways, lay) {
  const { order, follows } = playLifeOf(world, zones);
  const posts = [], byId2 = new Map(order.map((one) => [one.zone.id, one]));
  if (order.length < 2) return { order, follows, posts };
  const taken = new Set();
  const postBeside = (route, k, to, from) => {
    const [x, y] = route[k], ahead = route[Math.min(route.length - 1, k + 3)];
    const dx = Math.sign(ahead[0] - x), dy = Math.sign(ahead[1] - y);
    for (const [px, py] of [[x - dy, y + dx], [x + dy, y - dx], [x - dy * 2, y + dx * 2]]) {
      const key = `${px},${py}`;
      if (blocked.has(key) || paths.has(key) || doorways.has(key) || taken.has(key)) continue;
      if ([...taken].some((one) => { const [qx, qy] = one.split(",").map(Number); return Math.abs(qx - px) + Math.abs(qy - py) < 7; })) return;
      taken.add(key);
      posts.push({ x: px, y: py, dx: dx || 0, dy: dx ? 0 : dy, to, from });
      return;
    }
  };
  const regionN = (one) => (typeof byId[one.zone.region] === "number" ? byId[one.zone.region] : 0);
  // Where it begins: from the first's region's heart to each beginning (more than one begun at once: a fork there).
  const roots = order.filter((one) => !(follows.get(one.zone.id) || []).length);
  const start = hearts[regionN(roots[0])];
  for (const one of roots) {
    if (one.region.apart) continue;                                // a land apart: reached by its portal alone
    const route = playRoute(blocked, start, one.out, 1, 131, paths);
    lay(route, regionN(one), true);
    if (route.length > 6) postBeside(route, 2, one, null);
  }
  posts.push({ x: start[0] + 2, y: start[1], dx: 0, dy: 0, start: true, to: roots[0], from: null });
  taken.add(`${start[0] + 2},${start[1]}`);
  for (const one of order) {
    for (const id of follows.get(one.zone.id) || []) {
      const from = byId2.get(id);
      if (!from || from.region.apart || one.region.apart) continue;
      const route = playRoute(blocked, from.out, one.out, 1, 151, paths);
      route.slice(0, 3).forEach(([x, y]) => doorways.add(`${x},${y}`));
      route.slice(-3).forEach(([x, y]) => doorways.add(`${x},${y}`));
      lay(route, regionN(one), true);
      if (route.length < 8) { if (route.length > 3) postBeside(route, Math.floor(route.length / 2), one, from); continue; }
      for (let k = 4; k < route.length - 4; k += PLAY_POST_EVERY) postBeside(route, k, one, from);
    }
  }
  return { order, follows, posts };
}

/* Whose ground a tile is - the nearest region, the second nearest, how far into the band between. */
function playWhose(index, x, y) {
  const regions = index.regions;
  let a = 0, da = Infinity, b = -1, db = Infinity;
  for (let n = 0; n < regions.length; n += 1) {
    const d = playRectDistance(regions[n].rect, x, y);
    if (d < da) { b = a; db = da; a = n; da = d; } else if (d < db) { b = n; db = d; }
  }
  const t = b < 0 ? 1 : Math.min(1, (db - da) / PLAY_BLEND);
  return { a, b, toB: b < 0 ? 0 : 0.5 * (1 - t) };
}

/* How far each tile of an area is from what is there (a place, a path, a landmark's square) - a step at a time, up to
   PLAY_LAND_MOST + 1; and whether what is nearest is a place on an island. */
function playReach(index, x0, y0, W) {
  const P = PLAY_LAND_MOST + 1, X0 = x0 - P, Y0 = y0 - P, WW = W + 2 * P;
  const dist = new Uint8Array(WW * WW).fill(255);
  const queue = [];
  const seed = (x, y) => {
    if (x < X0 || y < Y0 || x >= X0 + WW || y >= Y0 + WW) return;
    const i = (y - Y0) * WW + (x - X0);
    if (dist[i]) { dist[i] = 0; queue.push(i); }
  };
  const area = [X0, Y0, X0 + WW, Y0 + WW];
  for (const one of index.zones) {
    if (!intersects(one.rect, ...area)) continue;
    for (let y = Math.max(one.rect[1], Y0); y < Math.min(one.rect[3], Y0 + WW); y += 1) {
      for (let x = Math.max(one.rect[0], X0); x < Math.min(one.rect[2], X0 + WW); x += 1) seed(x, y);
    }
  }
  for (const mark of index.landmarks) {
    const s = mark.square;
    if (!intersects(s, ...area)) continue;
    for (let y = Math.max(s[1], Y0); y < Math.min(s[3], Y0 + WW); y += 1) for (let x = Math.max(s[0], X0); x < Math.min(s[2], X0 + WW); x += 1) seed(x, y);
  }
  const bx0 = Math.floor(X0 / PLAY_CHUNK), by0 = Math.floor(Y0 / PLAY_CHUNK);
  const bx1 = Math.floor((X0 + WW) / PLAY_CHUNK), by1 = Math.floor((Y0 + WW) / PLAY_CHUNK);
  const islandPaths = [];
  for (let by = by0; by <= by1; by += 1) {
    for (let bx = bx0; bx <= bx1; bx += 1) {
      for (const [x, y, one] of index.pathBlocks.get(`${bx},${by}`) || []) {
        if ((index.regions[one.region].layout || "") === "islands") islandPaths.push([x, y]);   // boardwalks: no land
        else seed(x, y);
      }
    }
  }
  for (let head = 0; head < queue.length; head += 1) {
    const i = queue[head], d = dist[i];
    if (d >= P) continue;
    const x = i % WW, y = Math.floor(i / WW);
    for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [1, -1], [-1, 1], [-1, -1]]) {
      const nx = x + dx, ny = y + dy;
      if (nx < 0 || ny < 0 || nx >= WW || ny >= WW) continue;
      const j = ny * WW + nx;
      if (dist[j] > d + 1) { dist[j] = d + 1; queue.push(j); }
    }
  }
  return (x, y) => {
    const i = (y - Y0) * WW + (x - X0);
    return i >= 0 && i < dist.length ? dist[i] : 255;
  };
}

/* THE WALLS INSIDE A BUILDING LARGER THAN A CELL - the same rule as agent/playplaces.py inner_walls: one down it a third
   across, one across it half way down, doorways two wide. */
function playInnerWalls(zone) {
  const out = new Set();
  if (PLAY_OUTDOOR[zone.kind]) return out;
  const left = zone.x + 1, top = zone.y + 3, w = zone.w, h = zone.h;
  const fl = Math.floor;
  if (w > 16) {
    const vx = left + fl(w / 3);
    const gaps = h > 10 ? [top + fl(h / 4), top + fl(h / 4) + 1, top + fl(3 * h / 4), top + fl(3 * h / 4) + 1]
      : [top + fl(h / 2) - 1, top + fl(h / 2)];
    for (let y = top; y < top + h; y += 1) if (!gaps.includes(y)) out.add(`${vx},${y}`);
  }
  if (h > 10) {
    const hy = top + fl(h / 2);
    const gaps = [left + fl(w / 6), left + fl(w / 6) + 1, left + fl(2 * w / 3), left + fl(2 * w / 3) + 1, left + fl(w / 2), left + fl(w / 2) + 1];
    for (let x = left; x < left + w; x += 1) if (!gaps.includes(x)) out.add(`${x},${hy}`);
  }
  return out;
}

/* What stands in a building's yard, by the kind of place it is. */
const PLAY_YARDS = {
  home: ["redFlower", "whiteFlower", "bush", "flowerBush", "bench", "streetLamp", "berryBush", "planter", "townSprout", "pot",
         "cat", "dog", "rabbit"],
  futuristic: ["robot1", "robot2", "robot3", "streetLamp", "uTrash", "wisp"],
  skyscraper: ["streetLamp", "uTrash", "bench", "planter", "uTreeSmall"], office: ["streetLamp", "bench", "planter", "uTrash"],
  apartment: ["bench", "planter", "bush", "cat", "streetLamp"], school: ["bench", "flowerBush", "uTreeSmall", "streetLamp"],
  laboratory: ["streetLamp", "planter", "bench", "robot3"],
  haunted: ["hauntedStone", "hauntedTree", "cypress", "candle", "deadWeed", "hauntedLamp", "pumpkin"],
  ruins: ["brokenPillar", "greyRock", "mossRock", "deadTree", "statue"], sky: ["whiteFlower", "blueFlower", "sparkle", "pennant"],
  study: ["bench", "streetLamp", "bush", "signpost", "blueFlower", "parkBench"],
  library: ["bench", "streetLamp", "bush", "scroll", "whiteFlower", "parkBench"],
  screening: ["streetLamp", "bench", "pennant", "bush", "uTrash"], cafe: ["barrel", "crate", "bench", "redFlower", "barrel2", "planter2", "parkBench"],
  studio: ["barrel", "crate", "anvil", "bush", "barrel2", "log", "dBarrel"],
  magic: ["crystal", "mushroom", "purpleFlower", "torchPost", "townMushrooms", "mushroom2"],
  toy: ["target", "planter", "planter2", "townSprout", "townMushrooms", "beehive"],
  station: ["bench", "signpost", "streetLamp", "barrel", "uBench", "uTrash", "uMailbox"],
};
/* The yard a tile is in: two or three tiles out from a building - its kind's things - else none. */
function playYardOf(index, x, y) {
  for (const one of index.zones) {
    if (one.garden) continue;
    const r = one.rect;
    const d = Math.max(r[0] - x, x - (r[2] - 1), r[1] - y, y - (r[3] - 1));
    if (d >= 2 && d <= 3) return PLAY_YARDS[one.zone.style] || PLAY_YARDS[one.zone.kind] || PLAY_YARDS.home;
  }
  return null;
}

/* A NATURAL PLACE'S OWN TILES - the same rule as agent/playplaces.py natural_blocked: "x,y" -> [piece, solid].
   A lake: water in an ellipse, an islet in its middle (a tree on it), a wooden bridge to it; a waterfall: rock above,
   three wide falls, a pool below; a grove: trees close, a way through its middle; a meadow: flowers underfoot. */
function playNatural(zone) {
  const out = new Map();
  const left = zone.x, top = zone.y + 1, w = zone.w, h = zone.h;
  if (zone.kind === "lake") {
    const cx = left + w / 2, cy = top + h / 2, rx = w / 2 - 1, ry = h / 2 - 1;
    for (let y = top; y < top + h; y += 1) for (let x = left; x < left + w; x += 1) {
      const dx = (x + 0.5 - cx) / rx, dy = (y + 0.5 - cy) / ry;
      const islet = ((x + 0.5 - cx) / (rx * 0.3)) ** 2 + ((y + 0.5 - cy) / (ry * 0.3)) ** 2 <= 1;
      const bridge = x === Math.floor(cx) && y + 0.5 > cy;
      if (dx * dx + dy * dy > 1) continue;
      if (islet) out.set(`${x},${y}`, [x === Math.floor(cx) && y === Math.floor(cy) ? "grass" : "grass2", false]);
      else if (bridge) out.set(`${x},${y}`, ["bridge", false]);
      else out.set(`${x},${y}`, [playHash(x, y, 5) < 0.5 ? "water" : "water2", true]);
    }
  } else if (zone.kind === "waterfall") {
    const cx = left + Math.floor(w / 2);
    for (let y = top; y < top + h; y += 1) for (let x = left; x < left + w; x += 1) {
      const cliff = y < top + 2;
      const falls = x >= cx - 1 && x <= cx + 1 && y < top + h - 3;
      const pool = y >= top + h - 4 && ((x + 0.5 - (left + w / 2)) / (w / 3)) ** 2 + ((y + 0.5 - (top + h - 2)) / 2) ** 2 <= 1;
      if (falls) {
        const col = x === cx - 1 ? "L" : x === cx + 1 ? "R" : "";
        const part = y === top ? `waterFallT${col}` : y === top + h - 4 ? `waterFallB${col}` : `waterFall${col || "M"}`;
        out.set(`${x},${y}`, [part === "waterFallT" ? "waterFallT" : part, true]);
      } else if (cliff) out.set(`${x},${y}`, [y === top ? "faceHi~stone" : "faceLo~stone", true]);
      else if (pool) out.set(`${x},${y}`, [playHash(x, y, 5) < 0.5 ? "water" : "water2", true]);
    }
  } else if (zone.kind === "sea_tunnel") {
    // A glass way through the sea: water on both sides (fish about it, as its things), the way across its middle.
    const mid = top + Math.floor(h / 2);
    for (let y = top; y < top + h; y += 1) for (let x = left; x < left + w; x += 1) {
      const way = y === mid || y === mid - 1;
      out.set(`${x},${y}`, way ? ["tunnelGlass", false] : ["water", true]);
    }
  } else if (zone.kind === "roller_coaster") {
    // Its track round the edge (drawn high over it each frame - play.js playCoasterDraw): the ground under it not walked
    // on, but for its way in at the middle of its foot, under the track; the middle, grass, to walk about in.
    const gate = left + Math.floor(w / 2) - 1;
    for (let y = top; y < top + h; y += 1) for (let x = left; x < left + w; x += 1) {
      const edge = y === top || y === top + h - 1 || x === left || x === left + w - 1;
      if (!edge) continue;
      const way = y === top + h - 1 && (x === gate || x === gate + 1);
      out.set(`${x},${y}`, way ? ["paving", false] : ["grass2", true]);
    }
  } else if (zone.kind === "mountain" || zone.kind === "volcano" || zone.kind === "peak") {
    const cx = left + w / 2, cy = top + h / 2;
    const rx = zone.kind === "peak" ? Math.max(1.5, w / 4) : w / 2 - 0.5, ry = h / 2 - 0.5;
    for (let y = top; y < top + h; y += 1) for (let x = left; x < left + w; x += 1) {
      const d = ((x + 0.5 - cx) / rx) ** 2 + ((y + 0.5 - cy) / ry) ** 2;
      if (d > 1) continue;
      const high = (y + 0.5 - top) / h;                       // 0 at its top
      let piece = playHash(x, y, 7) < 0.5 ? "rock" : "rock2";
      if (zone.kind === "volcano" && d < 0.3) piece = playHash(x, y, 9) < 0.6 ? "lava" : "lava2";
      else if (zone.kind === "volcano" && d < 0.45) piece = "caveGrey";
      else if (zone.kind !== "volcano" && high < (zone.kind === "peak" ? 0.3 : 0.38)) piece = playHash(x, y, 11) < 0.6 ? "snow" : "snow2";
      out.set(`${x},${y}`, [piece, true]);
    }
  } else if (zone.kind === "meadow") {
    for (let y = top; y < top + h; y += 1) for (let x = left; x < left + w; x += 1) {
      if (playHash(x, y, 101) < 0.45) out.set(`${x},${y}`, ["flowers", false]);
    }
  }
  return out;
}

/* A REGION'S STREET LIGHTS (the user, 2026-11-17: "路灯除了灯之外还有火把什么可以选"): the agent's choice for the region
   (agent/playregions.py LIGHTS), else its land's. */
const PLAY_LIGHT_SPRITES = { lamp: "streetLamp", gaslamp: "gasLamp", torch: "torchPost", brazier: "brazier" };
const PLAY_LAND_LIGHTS = { primeval: "torch", graveyard: "torch", christmas: "gaslamp", newyear: "lamp", halloween: "torch",
                           valentine: "gaslamp", funfair: "lamp", botanical: "gaslamp", underwater: "torch", skycity: "lamp", space: "lamp", scifi: "lamp", zoo: "gaslamp",
                           ruins: "brazier", toytown: "gaslamp", city: "lamp", meadow: "gaslamp", farmland: "gaslamp", lakeside: "gaslamp", autumn: "torch",
                           forest: "torch", enchanted: "torch", desert: "torch", seaside: "torch", cavern: "torch",
                           highland: "brazier", snow: "brazier" };
function playLightOf(region) {
  return PLAY_LIGHT_SPRITES[region.lights] || PLAY_LIGHT_SPRITES[PLAY_LAND_LIGHTS[region.biome]] || "streetLamp";
}

const intersects = (r, x0, y0, x1, y1) => r[0] < x1 && r[2] > x0 && r[1] < y1 && r[3] > y0;

/* One block laid: the block's tiles and a margin round them. */
function playLayBlock(index, cx, cy) {
  const M = PLAY_LAY_MARGIN, W = PLAY_CHUNK + 2 * M;
  const x0 = cx * PLAY_CHUNK - M, y0 = cy * PLAY_CHUNK - M, x1 = x0 + W, y1 = y0 + W, count = W * W;
  const regions = index.regions;
  const ground = new Array(count), under = new Array(count), solid = new Uint8Array(count), kept = new Uint8Array(count);
  const doorway = new Uint8Array(count), none = new Uint8Array(count), owner = new Uint8Array(count);
  const nearest = new Uint8Array(count), light = new Float32Array(count * 7), inside = new Uint8Array(count);
  const at = (x, y) => (x >= x0 && x < x1 && y >= y0 && y < y1 ? (y - y0) * W + (x - x0) : -1);
  // 1. Whose ground, its patches, its light; beyond the edge, nothing.
  const reach = playReach(index, x0, y0, W);
  for (let y = y0; y < y1; y += 1) {
    for (let x = x0; x < x1; x += 1) {
      const { a, b, toB } = playWhose(index, x, y);
      const i = at(x, y);
      const mine = playHash(x, y, 7) < toB ? b : a;
      owner[i] = mine;
      nearest[i] = a;
      const land = regions[mine].land;
      // Its ground in patches, not squares scattered: the list's first most of it, each next where smooth noise is
      // higher - a park in the city, a bare patch in the forest.
      const shade = playNoise(x, y, 1, 6);
      const pick = shade < 0.55 ? 0 : Math.min(land.ground.length - 1, 1 + Math.floor((shade - 0.55) / 0.45 * (land.ground.length - 1)));
      ground[i] = under[i] = land.ground[pick];
      const pa = PLAY_PALETTES[regions[a].palette] || PLAY_PALETTES.day;
      const pb = b < 0 ? pa : PLAY_PALETTES[regions[b].palette] || PLAY_PALETTES.day;
      const wa = regions[a].land.wash || [0, 0, 0, 0], wb = b < 0 ? wa : regions[b].land.wash || [0, 0, 0, 0];
      const mix = (p, q) => p + (q - p) * toB;
      for (let k = 0; k < 3; k += 1) light[i * 7 + k] = mix(pa[0][k], pb[0][k]);
      const washA = [...pa[1]], washB = [...pb[1]];             // the palette's wash and the land's - the stronger
      if (wa[3] > washA[3]) washA.splice(0, 4, ...wa);
      if (wb[3] > washB[3]) washB.splice(0, 4, ...wb);
      for (let k = 0; k < 4; k += 1) light[i * 7 + 3 + k] = mix(washA[k], washB[k]);
      // LAND ONLY ROUND WHAT IS THERE, its edge wavering; past it, dark - or, round an island, the sea.
      const d = reach(x, y);
      const islands = (regions[a].layout || "") === "islands";
      const landReach = islands ? 2 + playNoise(x, y, 43, 5) * 2 : 4 + playNoise(x, y, 41, 6) * 6;
      if (d > landReach) {
        if (islands && d <= landReach + 7) { ground[i] = playHash(x, y, 5) < 0.5 ? "water" : "water2"; solid[i] = 1; }
        else { none[i] = 1; ground[i] = under[i] = regions[a].land.void || "none"; solid[i] = 1; }
      }
    }
  }
  const put = (x, y, piece, block = false, keep = 0) => {
    const i = at(x, y);
    if (i < 0) return;
    ground[i] = piece; solid[i] = block ? 1 : 0;
    if (keep) kept[i] = keep;
  };
  const pathOf = (x, y) => { const i = at(x, y); const land = regions[i < 0 ? 0 : owner[i]].land; return land.path[Math.floor(playHash(x, y, 3) * land.path.length)]; };
  const road = (x, y, piece) => {
    const i = at(x, y);
    if (i < 0 || kept[i] === 1 || kept[i] === 3) return;
    const wet = ground[i].startsWith("water");
    none[i] = 0;                                    // a road reaches on, into the dark if it must
    put(x, y, wet ? "bridge" : (piece || pathOf(x, y)), false, 2);
  };
  // 2. Water: the land's lakes and coasts, by smooth noise.
  for (let y = y0; y < y1; y += 1) {
    for (let x = x0; x < x1; x += 1) {
      const i = at(x, y), land = regions[owner[i]].land;
      if (!none[i] && !solid[i] && land.water && playNoise(x, y, 11) < land.water * 0.9) put(x, y, playHash(x, y, 5) < 0.5 ? "water" : "water2", true);
    }
  }
  // The rooms' footprints first known (the islands' water stays off them).
  const rooms = index.zones.filter((one) => intersects(one.rect, x0, y0, x1, y1));
  for (const one of rooms) {
    const [rx0, ry0, rx1, ry1] = one.garden ? one.rect : [one.zone.x, one.zone.y, one.zone.x + one.zone.w + 2, one.zone.y + one.zone.h + 6];
    for (let y = Math.max(ry0, y0); y < Math.min(ry1, y1); y += 1) for (let x = Math.max(rx0, x0); x < Math.min(rx1, x1); x += 1) kept[at(x, y)] = 1;
  }
  // 3. A river through a river region's row below its middle (on its land).
  for (const region of regions) {
    if ((region.layout || "") !== "river") continue;
    const r = region.rect, ry = region.oy + PLAY_CELL[1] + 4;
    for (let x = Math.max(r[0] - 8, x0); x < Math.min(r[2] + 8, x1); x += 1) {
      for (let dy = 0; dy < 6; dy += 1) {
        const i = at(x, ry + dy);
        if (i >= 0 && !kept[i] && !none[i]) put(x, ry + dy, playHash(x, ry + dy, 5) < 0.5 ? "water" : "water2", true);
      }
    }
  }
  // 4. THE PATHS (playLay): as each region's layout lays them - a street in a grid, a boardwalk round islands, a
  //    bridge over water; the first steps from a door kept clear.
  for (let by = Math.floor(y0 / PLAY_CHUNK); by <= Math.floor((y1 - 1) / PLAY_CHUNK); by += 1) {
    for (let bx = Math.floor(x0 / PLAY_CHUNK); bx <= Math.floor((x1 - 1) / PLAY_CHUNK); bx += 1) {
      for (const [x, y, one] of index.pathBlocks.get(`${bx},${by}`) || []) {
        const i = at(x, y);
        if (i < 0) continue;
        const region = regions[one.region], layout = region.layout || "village";
        road(x, y, layout === "grid" ? region.land.street : layout === "islands" ? "bridge" : null);
        if (index.doorways.has(`${x},${y}`)) doorway[i] = 1;
      }
    }
  }
  // 5. The rooms: walls in the region's material, floors, doors - and the way from each door down to its road.
  for (const one of rooms) {
    const zone = one.zone;
    const style = PLAY_STYLES[zone.style];
    const arch = `~${(style && style[0]) || one.region.architecture || "sandstone"}`;
    const floor = (style && style[1]) || PLAY_FLOORS[zone.kind] || "wood";
    if (one.garden) {
      // AN OPEN PLACE: its edge, its ground; a pool's water two in from its edge; a park's path round it.
      const [fx, fy, fw, fh] = playFloorOf(zone);
      const ground2 = PLAY_FLOORS[zone.kind] || "grass", edgeOf = PLAY_OUTDOOR[zone.kind] || "flowers";
      const pool = zone.kind === "pool" && fw >= 7 && fh >= 6;
      const nature = playNatural(zone);
      for (let y = fy - 1; y <= fy + fh; y += 1) {
        for (let x = fx - 1; x <= fx + fw; x += 1) {
          const edge = y === fy - 1 || y === fy + fh || x === fx - 1 || x === fx + fw;
          const ring = zone.kind === "park" && !edge && (y === fy || y === fy + fh - 1 || x === fx || x === fx + fw - 1);
          const wet = pool && x >= fx + 2 && x < fx + fw - 2 && y >= fy + 2 && y < fy + fh - 2;
          const made = !edge && nature.get(`${x},${y}`);
          if (made) put(x, y, made[0], made[1], 1);
          else put(x, y, wet ? (playHash(x, y, 5) < 0.5 ? "water" : "water2") : edge ? edgeOf : ring ? "dirt" : ground2, wet, 1);
          const i = at(x, y);
          if (i >= 0) none[i] = 0;
        }
      }
      continue;
    }
    const zx0 = zone.x, zy0 = zone.y, zx1 = zone.x + zone.w + 1, yb = zone.y + 3 + zone.h;
    const door = one.door;
    for (let x = zx0; x <= zx1; x += 1) {
      const end = x === zx0 ? "L" : x === zx1 ? "R" : "";
      put(x, zy0, (end === "L" ? "wallTL" : end === "R" ? "wallTR" : "wallT") + arch, true, 1);
      put(x, zy0 + 1, (end ? "wallSide" : "faceHi") + arch, true, 1);
      put(x, zy0 + 2, (end ? "wallSide" : "faceLo") + arch, true, 1);
      for (let y = zy0 + 3; y < yb; y += 1) {
        put(x, y, end ? "wallSide" + arch : floor, Boolean(end), 1);
        const i = at(x, y);
        if (i >= 0 && !end) inside[i] = 1;
      }
      if (x >= door && x < door + PLAY_DOOR_W) {                // the door, two wide
        for (let y = yb; y <= yb + 2; y += 1) { put(x, y, floor, false, 1); const i = at(x, y); if (i >= 0) inside[i] = 1; }
      } else {
        const capped = x === door - 1 ? "doorL" : x === door + PLAY_DOOR_W ? "doorR" : "";
        put(x, yb, (end === "L" ? "wallBL" : end === "R" ? "wallBR" : capped || "wallT") + arch, true, 1);
        put(x, yb + 1, (end === "L" ? "outHiL" : end === "R" ? "outHiR" : "faceHi") + arch, true, 1);
        put(x, yb + 2, (end === "L" ? "outLoL" : end === "R" ? "outLoR" : "faceLo") + arch, true, 1);
      }
      for (let y = zy0; y <= yb + 2; y += 1) { const i = at(x, y); if (i >= 0) none[i] = 0; }
    }
    // The walls inside a building of more than a cell, in its material.
    const tint = PLAY_STYLE_WALLS[zone.style];
    if (tint) {
      for (let y = zy0; y <= yb + 2; y += 1) for (let x = zx0; x <= zx1; x += 1) {
        const i = at(x, y);
        if (i >= 0 && solid[i] && !inside[i]) { const k = i * 7; light[k + 3] = tint[0]; light[k + 4] = tint[1]; light[k + 5] = tint[2]; light[k + 6] = tint[3]; }
      }
    }
    const insideWalls = playInnerWalls(zone);
    for (const key of insideWalls) {
      const [wx, wy] = key.split(",").map(Number);
      const i = at(wx, wy);
      // A wall down the building drawn as a wall's side; one across it as a wall's top seen from above.
      const across = insideWalls.has(`${wx - 1},${wy}`) || insideWalls.has(`${wx + 1},${wy}`);
      if (i >= 0) { ground[i] = (across ? "wallT" : "wallSide") + arch; solid[i] = 1; inside[i] = 1; }
    }
  }
  // The things in the rooms stand in the way (the user: "agent家居什么没有碰撞阻拦") - all but what hangs or lies flat.
  for (const item of index.items) {
    if (PLAY_WALL_KINDS.has(item.kind) || PLAY_UNDER_KINDS.has(item.kind)) continue;
    const [iw, ih] = PLAY_SIZES[item.kind] || [1, 1];
    for (let dy = 0; dy < ih; dy += 1) for (let dx = 0; dx < iw; dx += 1) { const i = at(item.x + dx, item.y + dy); if (i >= 0) solid[i] = 1; }
  }
  // 6. The landmarks, on their paved squares.
  const deco = [];
  const place = (name, x, y, region) => {
    const spec = PLAY_SCENERY[name];
    if (!spec) return;
    const underfoot = PLAY_UNDERFOOT.has(name);
    for (let dy = 0; dy < spec[4]; dy += 1) {
      for (let dx = 0; dx < spec[3]; dx += 1) { const i = at(x + dx, y + dy); if (i >= 0) { if (!underfoot) solid[i] = 1; kept[i] = kept[i] || 4; } }
    }
    const sound = PLAY_SCENERY_SOUND[name] || (PLAY_BIRD_TREES.has(name) && playHash(x, y, 29) < 0.3 ? "birds" : "");
    deco.push({ spec, x, y, region, name, sound });
  };
  for (const mark of index.landmarks) {
    const [sx0, sy0, sx1, sy1] = mark.square;
    if (!intersects([sx0 - 3, sy0 - 3, sx1 + 3, sy1 + 3], x0, y0, x1, y1)) continue;
    for (let y = sy0; y < sy1; y += 1) for (let x = sx0; x < sx1; x += 1) { put(x, y, mark.paving, false, 3); const i = at(x, y); if (i >= 0) none[i] = 0; }
    for (const [name, dx, dy] of PLAY_LANDMARKS[mark.region.landmark]) place(name, mark.cx + dx, mark.cy + dy, mark.region);
  }
  // 7. WHAT GROWS, decided tile by tile from where it is: a tile may hold something if it is free and a step from every
  //    room, door's way and solid thing; by a road, the theme's, else the land's; a solid one only where no neighbour
  //    has a stronger claim - so two never stand side by side, and it is the same whichever block is laid first.
  const near = (x, y, test) => { for (let dy = -1; dy <= 1; dy += 1) for (let dx = -1; dx <= 1; dx += 1) { const j = at(x + dx, y + dy); if (j >= 0 && test(j)) return true; } return false; };
  const claim = (x, y) => {
    const i = at(x, y);
    if (i < 0 || kept[i] || solid[i] || none[i]) return null;
    if (near(x, y, (j) => kept[j] === 1 || doorway[j] || kept[j] === 3 || (solid[j] && !ground[j].startsWith("water")))) return null;
    const region = regions[owner[i]];
    // A YARD ROUND EVERY BUILDING (the user: the world as rich as the life): two or three tiles out, what suits the place.
    const yard = playYardOf(index, x, y);
    if (yard && playHash(x, y, 89) < 0.3) {
      let name = yard[Math.floor(playHash(x, y, 97) * yard.length)];
      if (name === "streetLamp") name = playLightOf(region);
      const spec = PLAY_SCENERY[name];
      if (spec && spec[3] === 1 && spec[4] <= 2) return { name, region, strength: playHash(x, y, 61), underfoot: PLAY_UNDERFOOT.has(name) };
    }
    const byRoad = [[1, 0], [-1, 0], [0, 1], [0, -1]].some(([dx, dy]) => { const j = at(x + dx, y + dy); return j >= 0 && kept[j] === 2; });
    let name = "";
    if (byRoad) {
      const props = PLAY_THEMES[region.theme] || PLAY_THEMES.quiet;
      // A street lamp by the way now and then (the user: "夜晚周围暗，但是有路灯"), else the theme's.
      if (playHash(x, y, 83) < 0.045) name = "streetLamp";
      else if (playHash(x, y, 13) < 0.09) name = props[Math.floor(playHash(x, y, 17) * props.length)];
    } else if (playHash(x, y, 19) < region.land.thick * 1.5) {
      name = region.land.grows[Math.floor(playHash(x, y, 23) * region.land.grows.length)];
    }
    if (name === "streetLamp") name = playLightOf(region);
    const spec = name && PLAY_SCENERY[name];
    if (!spec || spec[3] !== 1 || spec[4] > 2) return null;
    return { name, region, strength: playHash(x, y, 61), underfoot: PLAY_UNDERFOOT.has(name) };
  };
  for (let y = y0 + 2; y < y1 - 2; y += 1) {
    for (let x = x0 + 2; x < x1 - 2; x += 1) {
      const mine = claim(x, y);
      if (!mine) continue;
      if (!mine.underfoot) {
        let beaten = false;
        // A DENSE LAND (the primeval forest - the user: "另外还有原始森林type"): only a neighbour beside it, not one
        // across its corner, keeps a tree from growing - the trees close, corner to corner.
        const dense = mine.region.land.dense;
        for (let dy = -1; dy <= 1 && !beaten; dy += 1) {
          for (let dx = -1; dx <= 1; dx += 1) {
            if (!dx && !dy) continue;
            if (dense && dx && dy) continue;
            const other = claim(x + dx, y + dy);
            if (other && !other.underfoot && other.strength > mine.strength) { beaten = true; break; }
          }
        }
        if (beaten) continue;
      }
      deco.push({ spec: PLAY_SCENERY[mine.name], x, y, region: mine.region, name: mine.name, tall: PLAY_SCENERY[mine.name][4] > 1,
                  sound: PLAY_SCENERY_SOUND[mine.name] || (PLAY_BIRD_TREES.has(mine.name) && playHash(x, y, 29) < 0.3 ? "birds" : "") });
    }
  }
  for (const one of deco) {                 // what grows stands in the way too, unless stepped over
    if (PLAY_UNDERFOOT.has(one.name)) continue;
    const i = at(one.x, one.y);
    if (i >= 0) solid[i] = 1;
  }
  // 8. The land fades into the dark near the edge: the nearer, the darker.
  for (let y = y0 + 3; y < y1 - 3; y += 1) {
    for (let x = x0 + 3; x < x1 - 3; x += 1) {
      const i = at(x, y);
      if (none[i]) continue;
      let dist = 4;
      for (let dy = -3; dy <= 3 && dist > 1; dy += 1) {
        for (let dx = -3; dx <= 3; dx += 1) { const j = at(x + dx, y + dy); if (none[j]) dist = Math.min(dist, Math.max(Math.abs(dx), Math.abs(dy))); }
      }
      if (dist < 4) {
        const k = i * 7, fade = [0.55, 0.38, 0.2][dist - 1] || 0;
        if (fade > light[k + 6]) { light[k + 3] = 27; light[k + 4] = 29; light[k + 5] = 34; light[k + 6] = fade; }
      }
    }
  }
  deco.sort((p, q) => p.y - q.y);
  // Each bush by where it stands, for a step to find it.
  const bushes = new Map();
  for (const one of deco) if (PLAY_RUSTLE.has(one.name)) { one.rustle = true; bushes.set(`${one.x},${one.y}`, one); }
  return { x0, y0, W, cx, cy, ground, under, solid, none, owner, nearest, light, inside, deco, bushes };
}

/* A block, laid when first wanted; the longest unwanted let go of. */
function playBlock(index, cx, cy) {
  const key = `${cx},${cy}`;
  let block = index.laid.get(key);
  if (block) { index.laid.delete(key); index.laid.set(key, block); return block; }
  block = playLayBlock(index, cx, cy);
  index.laid.set(key, block);
  if (index.laid.size > PLAY_LAID_MOST) index.laid.delete(index.laid.keys().next().value);
  return block;
}

/* A tile, wherever it is: its block (laid if need be) and its place in it. */
function playCell(map, x, y) {
  const cx = Math.floor(x / PLAY_CHUNK), cy = Math.floor(y / PLAY_CHUNK);
  const block = playBlock(map, cx, cy);
  return [block, (y - block.y0) * block.W + (x - block.x0)];
}

/* A tile's picture: a ground piece, or a wall piece in its region's material ("wallT~stone"); a piece that is only a
   colour (the city's asphalt - every tile of the set has a line painted on it) is ["#rrggbb"]. */
function playPieceSpec(piece) {
  const [name, arch] = piece.split("~");
  const spec = PLAY_GROUND[name] || PLAY_MORE_GROUND[name];
  if (!spec || !arch || !PLAY_WALL_PIECES.has(name)) return spec;
  return [spec[0], spec[1] + (PLAY_ARCH_SHIFT[arch] || 0), spec[2]];
}

/* Each tile's light, once, over a block drawn: multiplied, then washed - softer inside the rooms (lit within). */
function playLight(context, block) {
  const M = PLAY_LAY_MARGIN;
  for (let ty = 0; ty < PLAY_CHUNK; ty += 1) {
    for (let tx = 0; tx < PLAY_CHUNK; tx += 1) {
      const i = (ty + M) * block.W + (tx + M);
      if (block.none[i]) continue;
      const soft = block.inside[i] ? 0.35 : 1;
      const l = block.light, k = i * 7;
      const r = 255 - (255 - l[k]) * soft, g = 255 - (255 - l[k + 1]) * soft, b = 255 - (255 - l[k + 2]) * soft;
      if (r < 254.5 || g < 254.5 || b < 254.5) {
        context.globalCompositeOperation = "multiply";
        context.fillStyle = `rgb(${r | 0},${g | 0},${b | 0})`;
        context.fillRect(tx * 16, ty * 16, 16, 16);
      }
      const a = l[k + 6] * soft;
      if (a > 0.005) {
        context.globalCompositeOperation = "source-over";
        context.fillStyle = `rgba(${l[k + 3] | 0},${l[k + 4] | 0},${l[k + 5] | 0},${a.toFixed(3)})`;
        context.fillRect(tx * 16, ty * 16, 16, 16);
      }
    }
  }
  context.globalCompositeOperation = "source-over";
}

/* The region a place is in (the nearest's), for its name shown on entering and its sound. */
function playRegionAt(map, x, y) {
  const [block, i] = playCell(map, Math.floor(x), Math.floor(y));
  return map.regions[block.nearest[i]] || map.regions[0];
}
