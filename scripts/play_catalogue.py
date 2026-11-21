"""THE PLAY VIEW'S CATALOGUE OF THINGS (the user, 2026-11-17: "告诉模型现在已有哪些主题。模型可以根据主题找每个image(每个有
description), 然后根据description可以打开多个图片比较"): every picture the world can stand in a place - its id, what it
is in a few words, its size, the themes it belongs to (the lands it grows in, the landmarks and the styles' yards it is
part of) - written to api/web/play/catalogue.json, read by agent/playcatalogue.py.

Run after adding pictures to play-regions.js (node reads its tables):  python scripts/play_catalogue.py
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "personalized_agent" / "api" / "web"
OUT = WEB / "play" / "catalogue.json"

#: Each theme, in words for the model.
THEMES = {
    "meadow": "green meadows, flowers, round trees", "autumn": "autumn leaves, orange trees", "enchanted": "an enchanted, magical wood",
    "desert": "sand, cacti, dry rock", "seaside": "beach sand, palms, the sea", "forest": "deep pine forest",
    "highland": "rocky highlands", "snow": "snow and ice", "city": "a modern city: streets, lamps, benches, bins",
    "farmland": "farms, fields, crops, animals", "cavern": "caves, crystals, ore", "lakeside": "lakes and reeds",
    "toytown": "a toy town in pink and white: pink trees, sweets, toys, a white fence",
    "castle": "castles and towers of old: banners, knights, a well, hay, the training yard",
    "chinese": "old China: bamboo, willows, pagodas, pavilions, moon gates, stone lanterns, scholars' rocks",
    "castle_keep": "a castle keep: its gate between towers, banners, knights",
    "candy_garden": "a candy garden: pink trees, lollipops, candy canes, toys",
    "chinese_garden": "a Chinese garden: a pagoda, a moon gate, a pavilion, a willow", "underwater": "the sea floor: fish, sharks, turtles, coral",
    "skycity": "a city in the clouds", "space": "space: moon rock, robots, ships", "scifi": "the future: robots, neon",
    "zoo": "a zoo: lions, tigers, elephants, giraffes, bears", "ruins": "ancient ruins: pillars, broken stone",
    "graveyard": "a decaying graveyard: gravestones, dead trees, mist", "christmas": "Christmas: trees, presents, reindeer",
    "newyear": "(Lunar) New Year: red lanterns, firecrackers, gold ingots, blossom", "halloween": "Halloween: pumpkins, candles",
    "valentine": "Valentine's Day: hearts, roses, balloons", "funfair": "a funfair: rides, balloons, candy stands",
    "botanical": "a botanic garden: greenhouse, flowers, vegetables",
    "skyline": "a city skyline: skyscrapers", "school": "a school's yard", "office": "an office's yard",
    "apartment": "a block of flats' yard", "laboratory": "a laboratory's yard", "skyscraper": "a skyscraper's yard",
    "primeval": "a primeval forest: dense old trees, ferns, moss, deer and bears", "home": "a home's yard: flowers, benches, pets",
    "haunted": "a haunted house's yard", "futuristic": "a futuristic building's yard", "magic": "a magic building's yard",
    "toy": "a toy building's yard", "ruins_yard": "a ruined building's yard", "sky": "a sky building's yard",
    "harvest": "crops at every stage - sown, sprouting, growing, ripe, picked - fruits and vegetables: melons, a watermelon, grapes, corn",
    "robots": "robots and machines of many kinds and metals",
    "general": "anything else",
}

#: Themes a picture belongs to besides where it grows (produce.png: the harvest's and the robots').
EXTRA = {name: ["harvest", "farmland", "botanical"] if not name.startswith("metalBot") else ["robots", "scifi", "space"]
         for name in __import__("json").loads((Path(__file__).resolve().parent / "play_produce_names.json").read_text("utf-8"))}

#: What a picture is, where its name does not say it well enough.
WHAT = {
    # Three themes more (2026-11-21): castles and towers; a toy town in pink and white; old China.
    "castleTower": "a stone castle tower with battlements and a flag (large)", "castleWall": "a crenellated castle wall",
    "castleGate": "a castle gate between two towers, its portcullis down (large)", "bannerPole": "a red and gold banner on a pole",
    "knightStatue": "a knight in armour on a plinth", "stoneWell": "a stone well with a little roof",
    "trainingDummy": "a straw training dummy", "weaponRack": "a rack of spears and swords", "haystack": "a haystack",
    "horse": "a brown horse", "carriage": "a red and gold carriage",
    "pinkTree": "a round pink tree on a white trunk", "candyCane": "a giant candy cane", "lollipop": "a giant pink lollipop",
    "toyBlocks": "toy building blocks", "teddyBear": "a teddy bear with a pink bow", "toySoldier": "a pink and white toy soldier",
    "cupcake": "a giant pink cupcake", "whiteFence": "a white picket fence", "toyEngine": "a pink toy train engine",
    "toyCar": "a white toy train car",
    "bamboo": "a few bamboo stems", "bambooGrove": "a thicket of bamboo", "pagoda": "a red pagoda of four roofs (large)",
    "pavilion": "a Chinese pavilion with a green tiled roof (large)", "moonGate": "a round moon gate in a white wall (large)",
    "stoneLantern": "a stone lantern", "scholarRock": "a scholar's rock, full of holes", "willow": "a weeping willow (tall)",
    "paifang": "a red Chinese archway, its plaque gold (large)", "bonsai": "a pine bonsai in a blue pot",
    "dragonBoat": "a red dragon boat with rowers", "rickshaw": "a red rickshaw",
    **__import__("json").loads((Path(__file__).resolve().parent / "play_produce_names.json").read_text("utf-8")),
    "uTrash": "a city litter bin", "uHydrant": "a fire hydrant", "uTreeSmall": "a small city tree", "uTreeSmall2": "a small city tree",
    "uTree": "a tall city tree", "uAutumnTree": "a tall autumn city tree", "streetLamp": "a modern street lamp (tall)",
    "gasLamp": "an old gas street lamp (tall)", "torchPost": "a torch on a post", "brazier": "a fire brazier",
    "hauntedStone": "an old rounded gravestone", "hauntedCross": "a gravestone with a cross", "hauntedTree": "a twisted red dead tree (tall)",
    "hauntedTree2": "a twisted red dead tree (tall)", "hauntedVine": "creeping green vines (tall)", "hauntedLamp": "an iron graveyard lamp (tall)",
    "ironFence": "a wrought iron fence", "hauntedMist": "low purple mist", "candle": "a lit candle", "candles": "lit candles",
    "graveStatue": "a gargoyle on a plinth", "graveStatue2": "a skull on a plinth", "graveStatue3": "a small stone shrine",
    "deadWeed": "dead weeds", "deadWeed2": "dead weeds", "graveRocks": "grey stones", "coffin": "a wooden coffin",
    "vault": "a stone tomb door with a cross", "vault2": "a stone tomb door", "cypress": "a dark cypress tree",
    "mausoleum": "a pillared mausoleum (large)", "mausoleum2": "a stone mausoleum with a grille (large)",
    "angelStatue": "a winged angel statue (large)", "skullStatue": "a giant skull statue (large)",
    "present": "a wrapped gift box", "present2": "a wrapped gift box", "present3": "a wrapped gift box", "present4": "a wrapped gift box",
    "xmasTree": "a decorated Christmas tree", "xmasTree2": "a decorated Christmas tree with a star", "sleigh": "Santa's red sleigh",
    "reindeer": "a reindeer", "campfire": "a crackling campfire", "santa": "Santa Claus with his sack", "goldIngot": "a gold ingot (yuanbao)",
    "scroll": "a red scroll", "peach": "a peach of long life", "blossom": "a plum blossom", "panda": "a panda", "yinYang": "a yin-yang sign",
    "pumpkin": "a pumpkin", "pumpkin2": "a big pumpkin", "pumpkin3": "a squat pumpkin", "giantXmasTree": "a giant Christmas tree (large)",
    "lantern": "a red Chinese lantern", "firecrackers": "a string of red firecrackers", "heart": "a pink heart", "roses": "a bouquet of roses",
    "balloons": "red and blue balloons", "ticketBooth": "a fairground ticket booth", "candyStand": "a candy floss stand",
    "fishTank": "a fish tank with fish", "jellyfish": "a pink jellyfish", "coral": "orange and pink coral", "bubbles": "rising bubbles",
    "coasterCar": "a roller coaster car", "ferrisWheel": "a ferris wheel (large)", "carousel": "a carousel with horses (large)",
    "giantHeart": "a giant heart (large)", "greenhouse": "a glass greenhouse (large)", "barn": "a red barn (large)",
    "giantCat": "a giant cat (large)", "giantDog": "a giant dog (large)", "polarBear": "a polar bear", "croc": "a crocodile",
    "smallFish": "a small fish", "fish1": "a fish", "fish2": "a fish", "fish3": "a fish", "fish4": "a fish", "wisp": "a glowing wisp",
    "robot1": "a robot", "robot2": "a small robot", "robot3": "a tiny robot", "ship": "a spaceship", "ship2": "a rocket ship",
    "ship3": "a spaceship", "sparkle": "a twinkling star", "pillar": "a stone pillar (tall)", "brokenPillar": "a broken pillar (tall)",
    "greyPillar": "a grey pillar (tall)", "pumpkinCrate": "a crate of pumpkins", "wateringCan": "a watering can", "hayBale": "a hay bale",
    "seedling": "a seedling", "wheat": "wheat", "turnip": "a turnip", "carrot": "a carrot", "corn": "corn", "tomato": "a tomato plant",
    "cabbage": "a cabbage",
    # Redrawn and new (2026-11-17).
    "fuSign": "a red diamond with a gold 福 (fortune)", "redEnvelope": "a red envelope (hongbao)", "queueRail": "a queue line rail",
    "anemone": "a sea anemone", "seaweed": "tall seaweed (tall)", "lanternString": "a string of red lanterns",
    "bunting": "colourful bunting flags", "bigFishTank": "a big aquarium tank full of fish (large)",
    "coupletDoor": "a red door with New Year couplets", "firework": "a firework bursting", "lionHead": "a lion dance head",
    "plumTree": "a plum tree in blossom", "iceCreamStand": "an ice cream stand", "hotDogStand": "a hot dog stand",
    "coasterStation": "a roller coaster station", "circusTent": "a striped circus tent (large)", "heartArch": "an arch of roses and hearts",
    "swingShip": "a swinging pirate ship ride (large)", "dropTower": "a drop tower ride (tall)",
    "clownfish": "a clownfish", "clownfish2": "a clownfish", "butterflyfish2": "a butterflyfish", "butterflyfish3": "a butterflyfish",
    "yellowTang": "a yellow tang", "blueTang": "a blue tang", "moorishIdol": "a moorish idol", "angelfish": "an angelfish",
    "angelfish2": "a queen angelfish", "parrotfish": "a parrotfish", "parrotfish2": "a parrotfish", "koi": "a koi carp",
    "wrasse": "a wrasse", "redSnapper": "a red snapper", "triggerfish": "a triggerfish", "boxfish": "a boxfish", "grouper": "a red grouper",
    "redHeart": "a red heart", "pinkHeart": "a pink heart", "goldHeart": "a gold heart", "blueHeart": "a blue heart", "rose": "a red rose",
    "roseVine": "a climbing rose", "lovePotion": "a love potion", "loveLetter": "a love letter", "bow": "a ribbon bow",
    "jackOLantern": "a grinning jack-o'-lantern", "giantJackOLantern": "a giant jack-o'-lantern (large)",
    "runeStone": "a mossy rune stone", "runeStone2": "a mossy carved stone", "runeStone3": "a mossy spiral stone",
    "greyStone": "a grey standing stone", "greyStone2": "a grey carved stone", "greyStone3": "a grey spiral stone",
    "mossRock2": "a mossy boulder", "mossRock3": "a mossy boulder", "greyBoulder": "a grey boulder", "greyBoulder2": "a grey boulder",
    "graveTall": "a pointed gravestone", "graveUrn": "a gravestone with an urn (tall)", "graveBlock": "a square gravestone",
    "graveBlock2": "a square gravestone", "graveBlock3": "a square gravestone", "graveBlock4": "a square gravestone",
    "graveSquare": "a low gravestone", "graveSquare2": "a low gravestone", "graveSquare3": "a low gravestone",
    "graveSquare4": "a low gravestone", "graveSlab": "a grave slab", "goldBush": "a golden bush", "paleBush": "a pale bush",
    "wreath": "a wreath of flowers", "wreath2": "a wreath of yellow flowers", "wreath3": "a wreath of flowers",
    "graveCandles": "candles on a grave", "graveCandle": "a white candle", "graveCandle2": "a white candle",
    "redCandle": "a red candle", "redCandle2": "a red candle", "ropePost": "a rope post", "fern": "a fern (tall)",
    "tallFern": "a tall fern (tall)", "gloomTree": "a dark old tree (tall)", "gloomTree2": "a dark old tree (tall)",
    "gloomTree3": "a dark old tree with berries (tall)", "gloomTree4": "a dark old tree (tall)", "gloomTree5": "a deep green tree (tall)",
    "gloomTree6": "a deep green tree (tall)", "gloomTree7": "a deep green tree with berries (tall)", "gloomTree8": "a teal tree (tall)",
    "deer": "a deer",
    "trainEngine": "a steam train engine", "trainCarriage": "a train carriage", "trainStation": "a train station with a clock",
    "ferry": "a ferry boat", "pier": "a wooden pier", "airplane": "an airplane", "helipad": "a landing pad",
    "airship": "an airship", "skyDock": "a dock floating on a cloud",
    "whiteboard": "a whiteboard", "schoolDesk": "a school desk", "labBench": "a lab bench with flasks", "microscope": "a microscope",
    "computer": "a computer", "cubicle": "an office cubicle", "waterCooler": "a water cooler (tall)", "printer": "a printer",
    "lockers": "a row of lockers", "bunkBed": "a bunk bed", "kitchenette": "a small kitchen", "serverRack": "a server rack (tall)",
    "towerGlass": "a glass skyscraper (large)", "towerBrick": "a brick tower block (large)", "towerTall": "a tall glass skyscraper (large)",
}

DUMP = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[1], "utf8");
const take = (name) => {
  const m = src.indexOf("const " + name + " = {");
  let d = 0, i = src.indexOf("{", m), j = i;
  for (; j < src.length; j++) { if (src[j] === "{") d++; if (src[j] === "}") { d--; if (!d) break; } }
  return eval("(" + src.slice(i, j + 1) + ")");
};
console.log(JSON.stringify({ scenery: take("PLAY_SCENERY"), biomes: take("PLAY_BIOMES"), landmarks: take("PLAY_LANDMARKS"),
                             yards: take("PLAY_YARDS") }));
"""


def words(name: str) -> str:
    """A name in words: "townAutumnTree" -> "town autumn tree"."""
    return " ".join(part.lower() for part in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", name)) or name


def main() -> None:
    said = json.loads(subprocess.run(["node", "-e", DUMP, str(WEB / "play-regions.js")], capture_output=True, text=True,
                                     check=True).stdout)
    themes: dict = {}
    for biome, land in said["biomes"].items():
        for name in land.get("grows", []):
            themes.setdefault(name, set()).add(biome)
    for mark, pieces in said["landmarks"].items():
        for piece in pieces:
            themes.setdefault(piece[0], set()).add(mark)
    for name, mine in EXTRA.items():
        themes.setdefault(name, set()).update(mine)
    for style, names in said["yards"].items():
        for name in names:
            themes.setdefault(name, set()).add("ruins_yard" if style == "ruins" else style)
    things = {}
    for name, spec in said["scenery"].items():
        mine = sorted(themes.get(name) or {"general"})
        things[name] = {"what": WHAT.get(name) or words(name), "themes": mine, "spec": spec}
    used = {t for one in things.values() for t in one["themes"]}
    out = {"themes": {k: v for k, v in THEMES.items() if k in used}, "things": things}
    for theme in sorted(used - set(THEMES)):
        out["themes"][theme] = words(theme)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=0), encoding="utf-8")
    print(len(things), "things,", len(out["themes"]), "themes")


if __name__ == "__main__":
    main()
