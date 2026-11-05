"""WHAT IS RUNNING AND WHAT HOLDS THE SCREEN, as real-time signals (the user,
2026-11-04: "锁屏信号，和当前在运行的程序也是real-time会提供的signal之一 ... 安静的mask,
也要根据当前运行的东西是不是一个presentation, 或者zoom meeting之类的来定").

Told from the program and title in front, the programs running, what the system
says of the screen, and who has the microphone. A meeting program merely open is
no meeting: it takes its window, its meeting process, or the microphone. A browser
with the microphone is a meeting only on a meeting's own address (the user,
2026-11-04: "浏览器这个要看具体网址，比如google meet网址，不能只看浏览器"): voice
typing in a page is not one.
"""

from __future__ import annotations

import getpass
import logging
import re
from typing import Iterable, List

log = logging.getLogger(__name__)

#: The most program names a look keeps.
MOST_PROGRAMS = 120
#: Programs that show slides.
SLIDE_PROGRAMS = ("powerpnt", "keynote", "soffice", "impress", "wpp")
#: A slide show's or presenter's window, in the display languages.
SLIDE_TITLES = ("slide show", "slideshow", "presenter view", "幻灯片放映", "演示者视图", "投影片放映",
                "スライド ショー", "発表者ツール", "슬라이드 쇼", "발표자 보기",
                "bildschirmpräsentation", "referentenansicht", "diaporama", "mode présentateur",
                "presentación con diapositivas", "vista del moderador", "presentazione",
                "visualizzazione relatore", "показ слайдов", "режим докладчика")
#: Over the whole screen, these are slides being shown from a browser.
SLIDE_PAGES = ("google slides", "google 幻灯片", "google 簡報", "powerpoint", "keynote", "prezi")
#: A meeting's own window.
MEETING_TITLES = ("zoom meeting", "zoom webinar", "zoom 会议", "zoom会议", "zoom 會議", "zoom ミーティング",
                  "zoom 회의", "webex meeting", "google meet", "腾讯会议 -", "飞书会议", "lark meetings")
#: Google Meet's own address in a title: Meet - abc-defg-hij.
MEET_CODE = re.compile(r"\bmeet\s*[-–—]\s*[a-z]{3}-[a-z]{4}-[a-z]{3}\b", re.I)
#: Runs only while Zoom is in a meeting.
MEETING_PROCESSES = ("cpthost",)
#: With the microphone, these are a call.
CALL_PROGRAMS = ("zoom", "teams", "wemeet", "webex", "ciscocollab", "lark", "feishu", "dingtalk",
                 "slack", "skype", "discord", "facetime", "wechat", "weixin", "whatsapp", "telegram")
BROWSERS = ("chrome", "msedge", "firefox", "safari", "brave", "opera", "vivaldi", "arc")
#: A browser with the microphone, lately on one of these, is in a meeting.
MEETING_ADDRESSES = ("meet.google.com/", "teams.microsoft.com/", "teams.live.com/", "zoom.us/wc/",
                     "zoom.us/j/", "webex.com/", "meeting.tencent.com/", "voovmeeting.com/",
                     "whereby.com/", "meet.jit.si/", "feishu.cn/meeting", "larksuite.com/meeting")


def program(name: str) -> str:
    """A program as the signals say it: lower case, no ".exe"."""
    name = str(name or "").strip().lower()
    return name[:-4] if name.endswith(".exe") else name


def running_programs() -> List[str]:
    """The programs running under the user's own name - never what they hold."""
    try:
        import psutil
        user = getpass.getuser().lower()
        names = set()
        for one in psutil.process_iter(["name", "username"]):
            owner = str(one.info.get("username") or "").lower()
            if one.info.get("name") and owner.rsplit("\\", 1)[-1] == user:
                names.add(program(one.info["name"]))
        return sorted(names)[:MOST_PROGRAMS]
    except Exception:
        log.debug("the programs running could not be read", exc_info=True)
        return []


def presenting(application: str, title: str, held: str) -> bool:
    """Whether slides are being shown: `held` is the system's word for the screen."""
    said, name = str(title or "").lower(), program(application)
    if held == "presentation":
        return True
    if any(one in name for one in SLIDE_PROGRAMS) and any(one in said for one in SLIDE_TITLES):
        return True
    return bool(held) and any(one in said for one in SLIDE_PAGES)


def in_meeting(application: str, title: str, running: Iterable[str],
               microphone: Iterable[str], pages: Iterable[str] = ()) -> bool:
    """`pages`: the addresses a browser was on lately."""
    said = str(title or "").lower()
    if any(one in said for one in MEETING_TITLES) or MEET_CODE.search(said):
        return True
    if any(program(one) in MEETING_PROCESSES for one in running):
        return True
    users = [program(one) for one in microphone]
    if any(call in one for one in users for call in CALL_PROGRAMS):
        return True
    return any(browser in one for one in users for browser in BROWSERS) and any(
        address in str(page).lower() for page in pages for address in MEETING_ADDRESSES)


__all__ = ["in_meeting", "presenting", "program", "running_programs"]
