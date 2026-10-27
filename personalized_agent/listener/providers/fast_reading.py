"""FAST SAMPLING: the windows read often, and how often (the user, 2026-10-26).

Chats, mail and social media change while the user is in them, and what
passes on their screens - a message arriving, a thread scrolled through -
is gone in seconds. Such a window, in front, is LOOKED AT every 3 seconds
(the user, 2026-10-26: every second was too often): a small picture of it,
and its fingerprint. Only when the picture changed is
the window READ - its text, or, a WeChat window on a Mac, its messages - so
the cost follows how much changes, not how many seconds pass. Measured on
the machine it was built on (2026-10-26): a look at a 2560x1356 window,
about 0.12 s; a read of WeChat's window, about 0.42 s, two thirds of it
spent inside WeChat answering.

The looks slow down while nothing changes - every 5 s after 30 s the same -
and are back to every 3 s at typing or at a change.
A read still under way is never overlapped: the next look waits for it. And
there are bounds: reads that took more than a share of one core over the
last half minute (25%, the setting) slow it to every 5 s until they are
under it again; on battery, never more often than every 5 s.

WHICH WINDOWS: the applications in `listener_fast_applications` (by name -
"Weixin.exe" and "WeChat" alike), and any window whose title holds one of
`listener_fast_titles` - a web mail or a chat or a social site in a browser,
known by the title its page gives the tab; any page whose title says it is
an inbox, too. Every other window is read as before, at
`listener_screen_interval_seconds`.
"""
from __future__ import annotations

import collections
import re
import time
from typing import Any, Deque, List, Optional, Tuple

#: Chat, mail and social applications, by name - lower case, without
#: ".exe" or ".app" (`_name`).
FAST_APPLICATIONS = (
    # Chat, in China
    "weixin", "wechat", "微信", "wxwork", "企业微信", "wecom", "qq", "qqnt", "dingtalk", "钉钉",
    "feishu", "飞书", "lark",
    # Chat, elsewhere
    "slack", "ms-teams", "teams", "microsoft teams", "discord", "telegram", "telegram desktop",
    "whatsapp", "whatsapp root", "signal", "line", "kakaotalk", "messenger", "messages", "信息",
    # Mail
    "outlook", "olk", "thunderbird", "mail", "邮件", "foxmail", "mailmaster", "网易邮箱大师",
    "spark", "spark desktop", "airmail", "mimestream", "postbox", "em client", "mailbird",
)

#: Words in a window's title that say it is a chat, mail or social page -
#: matched without regard to case. A browser's tab says what page it holds.
FAST_TITLES = (
    # Web mail
    "gmail", "outlook", "qq邮箱", "qq mail", "网易邮箱", "163邮箱", "126邮箱", "yeah.net",
    "新浪邮箱", "sina mail", "139邮箱", "阿里邮箱", "腾讯企业邮", "yahoo mail", "icloud mail",
    "proton mail", "protonmail", "zoho mail", "fastmail", "tutanota", "tuta mail", "gmx",
    "mail.com", "aol mail", "yandex mail", "mail.ru", "hey.com", "superhuman",
    "inbox", "收件箱", "收信", "webmail",
    # Chat on the web
    "slack", "discord", "telegram", "whatsapp", "messenger", "microsoft teams", "飞书", "钉钉",
    "微信网页版", "wechat web",
    # Social media
    "/ x", "twitter", "微博", "weibo", "小红书", "xiaohongshu", "rednote", "知乎", "facebook",
    "instagram", "linkedin", "reddit", "bluesky", "mastodon",
)

#: The looks at their fastest, and on battery at their fastest.
FASTEST_SECONDS = 3.0
BATTERY_SECONDS = 5.0
#: The looks and their slowing down: (seconds unchanged, seconds between).
SLOWER = ((30.0, 5.0),)
#: Typing in the last this many seconds is the user at work in the window.
TYPING_SECONDS = 5.0
#: Over budget, or on battery: never more often than these.
OVER_BUDGET_SECONDS = 5.0
#: The half minute the budget is counted over.
BUDGET_WINDOW_SECONDS = 30.0
#: Fingerprints this many bits apart (of 64) are the same picture.
SAME_PICTURE_BITS = 2


def _name(application: str) -> str:
    name = (application or "").strip().lower()
    return re.sub(r"\.(exe|app)$", "", name)


def _listed(value: Any, default: Tuple[str, ...]) -> List[str]:
    if value is None or value == "":
        return list(default)
    items = value if isinstance(value, (list, tuple)) else re.split(r"[\n,;]", str(value))
    return [str(one).strip().lower() for one in items if str(one).strip()]


def applications(config: Any) -> List[str]:
    return [_name(one) for one in _listed(config.get("listener_fast_applications", None),
                                          FAST_APPLICATIONS)]


def titles(config: Any) -> List[str]:
    return _listed(config.get("listener_fast_titles", None), FAST_TITLES)


def wanted(config: Any, application: str, title: str) -> bool:
    """Whether the window in front is one read often."""
    if not bool(config.get("listener_fast_enabled", True)):
        return False
    if _name(application) in applications(config):
        return True
    said = (title or "").casefold()
    return bool(said) and any(word.casefold() in said for word in titles(config))


def _on_battery() -> bool:
    try:
        import psutil
        battery = psutil.sensors_battery()
    except Exception:
        return False
    return battery is not None and battery.power_plugged is False


def fingerprint(image: Any) -> Optional[int]:
    """64 bits that change little when the picture does: a difference hash
    of a small grey copy of it."""
    if image is None:
        return None
    try:
        grey = image.convert("L")
        grey.thumbnail((160, 160))
        tiny = grey.resize((9, 8))
        values = list(tiny.getdata())
    except Exception:
        return None
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (values[row * 9 + col] < values[row * 9 + col + 1])
    return bits


def same_picture(one: Optional[int], other: Optional[int]) -> bool:
    return one is not None and other is not None \
        and bin(one ^ other).count("1") <= SAME_PICTURE_BITS


class Pace:
    """How often to look at a window read often, and whether to read it now."""

    def __init__(self, config: Any) -> None:
        self.config = config
        self.window: Any = None
        self.print: Optional[int] = None
        self.changed_at = 0.0
        self.reads: Deque[Tuple[float, float]] = collections.deque()
        self.looks = 0
        self.looks_unchanged = 0
        self.held_by_budget = 0

    def _setting(self, key: str, default: float, low: float, high: float) -> float:
        try:
            return max(low, min(high, float(self.config.get(key, default))))
        except (TypeError, ValueError):
            return default

    def fastest(self) -> float:
        return self._setting("listener_fast_interval_seconds", FASTEST_SECONDS, FASTEST_SECONDS, 15.0)

    def budget_share(self) -> float:
        return self._setting("listener_fast_cpu_percent", 25.0, 5.0, 100.0) / 100.0

    def battery_floor(self) -> float:
        return self._setting("listener_fast_battery_seconds", BATTERY_SECONDS, FASTEST_SECONDS, 30.0)

    def spent(self, now: float) -> float:
        """Seconds reading took over the last half minute."""
        while self.reads and self.reads[0][0] < now - BUDGET_WINDOW_SECONDS:
            self.reads.popleft()
        return sum(took for _at, took in self.reads)

    def over_budget(self, now: float) -> bool:
        return self.spent(now) > self.budget_share() * BUDGET_WINDOW_SECONDS

    def looked(self, window: Any, picture: Optional[int], now: float) -> bool:
        """A look taken: whether the window changed since the last - a new
        window, or a different picture (or none to compare)."""
        self.looks += 1
        changed = (window != self.window or picture is None
                   or not same_picture(picture, self.print))
        if changed:
            self.window, self.print, self.changed_at = window, picture, now
        else:
            self.looks_unchanged += 1
        return changed

    def read_now(self, changed: bool, typing: bool, now: float) -> bool:
        """Whether to read the window now: it changed, or the user is typing
        in it - and the reads are within their budget."""
        if not (changed or typing):
            return False
        if self.over_budget(now):
            self.held_by_budget += 1
            return False
        return True

    def took(self, now: float, seconds: float) -> None:
        self.reads.append((now, max(0.0, seconds)))

    def interval(self, typing: bool, now: float) -> float:
        """Seconds to the next look."""
        between = self.fastest()
        if not typing:
            unchanged = now - self.changed_at if self.changed_at else 0.0
            for after, slower in SLOWER:
                if unchanged >= after:
                    between = max(between, slower)
                    break
        if self.over_budget(now):
            between = max(between, OVER_BUDGET_SECONDS)
        if _on_battery():
            between = max(between, self.battery_floor())
        return between

    def forget(self) -> None:
        self.window, self.print, self.changed_at = None, None, 0.0

    def status(self, now: Optional[float] = None) -> dict:
        now = time.monotonic() if now is None else now
        return {"fastest_seconds": self.fastest(), "slower": [list(one) for one in SLOWER],
                "cpu_budget_percent": round(self.budget_share() * 100),
                "battery_floor_seconds": self.battery_floor(),
                "on_battery": _on_battery(),
                "reading_seconds_last_30s": round(self.spent(now), 2),
                "over_budget": self.over_budget(now), "looks": self.looks,
                "looks_unchanged": self.looks_unchanged,
                "held_by_budget": self.held_by_budget}
