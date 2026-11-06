"""Read only: is the steadier WeChat avatar steady? (wechat_items.py `_steady_avatar`, recorded only.)

Run it against a data folder, a while after the new code has been reading. It prints counts, never
a name or an avatar: per chat, how many kinds of avatar each fingerprint read, and how many
conversations each would make with the name and the avatar both having to agree.

    python -X utf8 scripts/wechat_avatar_compare.py <path to agent.db> [--since 2026-11-05]
"""
from __future__ import annotations

import collections
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personalized_agent.data_manage.storage.wechat_store import _avatar_matches  # noqa: E402

#: Bits of the 36 allowed apart, and of each colour's eighth.
STEADY_BITS, STEADY_COLOUR = 6, 1


def steady_matches(a: str, b: str) -> bool:
    (ah, ac), (bh, bc) = a.split(":"), b.split(":")
    return ((int(ah, 16) ^ int(bh, 16)).bit_count() <= STEADY_BITS and
            max(abs(int(x) - int(y)) for x, y in zip(ac.split(","), bc.split(","))) <= STEADY_COLOUR)


def kinds(seen: list, same) -> int:
    """How many conversations the name and any avatar seen so far would make of these."""
    groups: list = []
    for one in seen:
        hit = next((g for g in groups if any(same(x, one) for x in g)), None)
        if hit is None:
            groups.append([one])
        elif one not in hit:
            hit.append(one)
    return len(groups)


def main(argv: list) -> int:
    since = argv[argv.index("--since") + 1] if "--since" in argv else "2026-11-05"
    path = next(one for one in argv if not one.startswith("--") and one != since)
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    old, new, readings = (collections.defaultdict(list), collections.defaultdict(list),
                          collections.Counter())
    for (raw,) in db.execute("SELECT payload FROM listener_wechat_observations "
                             "WHERE captured_at >= ? ORDER BY captured_at", (since,)):
        try:
            said = json.loads(raw or "{}")
        except ValueError:
            continue
        name = said.get("peer_name")
        if said.get("layout_status") != "recognized" or not name or "peer_avatar_steady" not in said:
            continue
        readings[name] += 1
        if said.get("peer_avatar"):
            old[name].append(said["peer_avatar"])
        if said.get("peer_avatar_steady"):
            new[name].append(said["peer_avatar_steady"])
    if not readings:
        print("No reading has the new fingerprint yet: is the new code running?")
        return 1
    total = sum(readings.values())
    print(f"chats {len(readings)} | readings {total} | old avatar {sum(map(len, old.values()))} | "
          f"new avatar {sum(map(len, new.values()))}")
    old_kinds = {n: kinds(old[n], _avatar_matches) for n in readings}
    new_kinds = {n: kinds(new[n], steady_matches) for n in readings}
    print(f"conversations with name and avatar: old {sum(old_kinds.values())} | new "
          f"{sum(new_kinds.values())} | names alone {len(readings)}")
    print("readings, old kinds, new kinds, the busiest chats:")
    for at, name in enumerate(sorted(readings, key=lambda n: -readings[n])[:20], start=1):
        print(f"  #{at:2} {readings[name]:5} {old_kinds[name]:4} {new_kinds[name]:4}")
    names = [n for n in new if new[n]]
    shared = sum(1 for i, a in enumerate(names) for b in names[i + 1:]
                 if any(steady_matches(x, y) for x in new[a][:6] for y in new[b][:6]))
    print(f"pairs of different names with a matching new avatar: {shared}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
