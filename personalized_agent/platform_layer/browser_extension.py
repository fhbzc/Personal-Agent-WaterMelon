"""THE AGENT'S BROWSER EXTENSION (the user, 2026-11-01: "build一个extension, 里面
之后可能会bundle很多功能; settings里有install extension的位置 (general里,
update下面), 点击之后跳转install界面").

One extension, for Chrome (and Brave, Vivaldi, Chromium) and Firefox, on
Windows, macOS and Linux - not Edge (the user, 2026-11-02). Today it puts
DELEGATE WORK on the right-click menu of every page (background.js); it is
the place for more of the agent's parts in the browser later.

It reaches the agent by the browser's NATIVE MESSAGING: the browser starts
this program (`--native-host`, platform_layer/native_host.py) and hands it
what was right-clicked on its standard input - no network, no port, and only
this extension may (the host names its ids: the unpacked one's, fixed by
KEY, and each store's, STORE_IDS; Firefox's FIREFOX_ID).

FROM THE STORES (the user, 2026-11-02: "install调用的是注册好的链接，而不是本地
链接 ... 用户不需要看extension的源代码"): the users get it from the Chrome Web
Store and Firefox Add-ons (STORES). Its sources, resources/browser_extension,
are in the private repository alone - the public one has none - and made
into the stores' packages there (`package`, scripts/package_extension.py,
its own version EXTENSION_VERSION).

Installed from Settings > General, on the user's word (`install`): THE HOST
made known to each browser - a manifest naming the launcher and the
extension, in the browser's own place for it: on Windows the user's part of
the registry (HKCU), elsewhere the browser's NativeMessagingHosts folder, for
each browser found on this computer. Then the store's page is opened
(`open_store`), and the user adds it there. Kept as the version has it at
each start (`keep_current`).

`remove` takes out exactly what `install` put in.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

#: The host's name, as the extension asks for it (background.js).
HOST = "com.watermelon.personal_agent"
#: The extension's public key: it fixes its id in the Chromium browsers,
#: unpacked or not, so the host can name it. (Its private key was never
#: kept: an unpacked extension needs none.)
KEY = ("MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAw4kTN1eE784znTzDPtGtEdq0YB2frB/0UisRvrsk"
       "dcwjc7MKXvcCtnqdYfgPNvvjvqk4oxG0bdO9lWFADitIqFD2TT0QniHTAqxOgHUfzqqQ5wPnap9h+BMttRDii"
       "0rrneO38btbAu2LaIvvl4PsPc0HpcMk5ubsjStho4XwI29GpeuLNjOFpJMOiqvmI1ud84aGuYzp95hlVV6H32"
       "pmK3AUdKi7gMK4wOXzgGzRR/q5NFMQHWqmfP8e74xqZ5pVJl30R0tKnwE7p6Edmyu8gqbpev2v8wycUhI7hYk"
       "eS+dolTAs2lb5OYAGuoR6dzO3unX0dQrKKCtpDM8lc2DiNwIDAQAB")
#: Its id in Firefox.
FIREFOX_ID = "delegate@personal-agent-watermelon"
#: THE STORES' PAGES - where Install sends the user (the user, 2026-11-02: "那个
#: install调用的是注册好的链接，而不是本地链接 ... 用户不需要看extension的源代码").
#: Chrome's for every Chromium browser here (Brave, Vivaldi, Chromium take it
#: too); Firefox's by the extension's own id, whatever the listing is called.
#: Not in Edge's store (the user, 2026-11-02).
STORES: Dict[str, str] = {
    "chrome": "https://chromewebstore.google.com/detail/ikdgdhidoljaomoicjlfdchckaklbcbh",
    "firefox": "https://addons.mozilla.org/firefox/addon/delegate@personal-agent-watermelon/",
}
#: ITS IDS IN THE STORES (the user, 2026-11-02: "在chrome和firefox上都release
#: 这个extension"). A store gives a Chromium extension an id of its own - the
#: KEY's is the unpacked one's alone (a store takes no `key`) - and the host
#: answers only the ids it names: each store's goes here once it is known
#: (shown in its dashboard after the first upload). Firefox keeps its id
#: (FIREFOX_ID) in its store too.
STORE_IDS: Tuple[str, ...] = (
    "ikdgdhidoljaomoicjlfdchckaklbcbh",      # the Chrome Web Store (2026-11-02)
)
#: THE EXTENSION'S OWN VERSION (the user, 2026-11-02) - not the agent's: raised
#: only when the extension itself changes (its files, its manifest, its words),
#: and only then uploaded to the stores again, which take a higher one each
#: time. The agent's updates leave it as it is.
EXTENSION_VERSION = "0.0.6"    # 0.0.2: says how it was installed ("here"),
                               # and says "here" every six hours (alarms)
                               # 0.0.3: says what can be seen of a page (visible.js);
                               # never in a private window
                               # 0.0.4: fills a form's boxes (fill.js)
                               # 0.0.5: follows a site's private mode (agent-private-mode):
                               # its pages never read, filled, or sent
                               # 0.0.6: chooses radios and a page's own lists' options (never a
                               # button); says the files to attach - never uploads them
#: The files of the extension, as they are in resources/browser_extension.
FILES = ("background.js", "help.html", "help.js", "opening.html", "opening.js", "visible.js",
         "fill.js")
FLAVORS = ("chromium", "firefox")
ICON_SIZES = (16, 32, 48, 128)

#: Its words, in each display language. Its "description" is the stores' summary:
#: the extension is the agent's place in the browser - Delegate work its first part
#: (the user, 2026-11-02: "之后要支持别的功能，所以delegate work 只是一个example").
WORDS: Dict[str, Dict[str, str]] = {
    "en": {"opening": "Opening…",
           "delegate": "Delegate work",
           "learn": "Learn",
           "description": "Your Personal Agent WaterMelon in the browser - hand it pages, text, links and pictures from the right-click menu.",
           "help_head": "The agent is not connected to this browser yet",
           "help_do": "Open Personal Agent WaterMelon, go to Settings, then General, and press "
                      "\"Install the browser extension\". It connects this browser to the agent "
                      "on this computer.",
           "help_then": "Then right-click the page again and pick Delegate work.",
           # A form's boxes filled (fill.js, 0.0.4).
           "fill_head": "Filled by your agent: {n} boxes, outlined. Check them, then submit it yourself.",
           "fill_missed": "Not filled - {field}",
           "fill_why_none": "no box found",
           "fill_why_never": "a box this never fills",
           "fill_why_yours": "it has your own words in it",
           "fill_why_choice": "no such choice",
           "fill_copy": "Copy", "fill_copied": "Copied", "fill_close": "Close",
           # Its files, told - never uploaded (2026-11-13).
           "fill_attach_head": "Attach these yourself:", "fill_copy_path": "Copy where it is"},
    "zh": {"opening": "正在打开…",
           "delegate": "委托工作",
           "learn": "学习",
           "description": "浏览器里的 Personal Agent WaterMelon：从右键菜单把网页、文字、链接和图片交给你的 agent。",
           "help_head": "agent 还没有连接到这个浏览器",
           "help_do": "打开 Personal Agent WaterMelon，进入 设置 > 通用，点“安装浏览器扩展”。"
                      "它会把这个浏览器连接到这台电脑上的 agent。",
           "help_then": "然后再在网页上右键，选“委托工作”。",
           "fill_head": "你的 agent 已填好 {n} 个框，已高亮。请检查后自己提交。",
           "fill_missed": "没有填 - {field}",
           "fill_why_none": "没找到对应的框",
           "fill_why_never": "这种框一律不填",
           "fill_why_yours": "里面已有你自己写的内容",
           "fill_why_choice": "没有这个选项",
           "fill_copy": "复制", "fill_copied": "已复制", "fill_close": "关闭",
           "fill_attach_head": "这些附件请自己上传：", "fill_copy_path": "复制文件位置"},
}


def extension_id(key: str = KEY) -> str:
    """The id the Chromium browsers give an extension of this key."""
    digest = hashlib.sha256(base64.b64decode(key)).hexdigest()[:32]
    return "".join(chr(ord("a") + int(ch, 16)) for ch in digest)


CHROMIUM_ID = extension_id()


def source() -> Path:
    from ..common.paths import resource_path
    return resource_path("resources", "browser_extension")


def folder(data_dir: Path) -> Path:
    return Path(data_dir) / "browser_extension"


def manifest(flavor: str, version: str = EXTENSION_VERSION) -> Dict[str, Any]:
    """The extension's manifest - Manifest V3 for both; Firefox's background
    is a script, the Chromium browsers' a service worker."""
    said: Dict[str, Any] = {
        "manifest_version": 3, "name": "Personal Agent WaterMelon",
        "short_name": "Personal Agent", "version": _version(version),
        "description": "__MSG_description__", "default_locale": "en",
        # "storage": where the agent's page is (its port), kept between
        # right-clicks - no warning is shown for it.
        # "alarms" (0.0.2): its word to the agent every six hours - a permission
        # the browser shows no one, so the update adding it is not paused.
        "permissions": ["contextMenus", "nativeMessaging", "activeTab", "scripting", "storage",
                        "alarms"],
        # A SCRIPT IN EVERY PAGE, FOR WHAT CAN BE SEEN OF IT (the user, 2026-11-05, in place of
        # 2026-11-01's none): it goes to the user's own agent on this computer, nowhere else.
        # 0.0.4: AND ITS BOXES FILLED when the agent opened it for a form it handed over (fill.js,
        # the user, 2026-11-08) - never pressing anything.
        "content_scripts": [{"matches": ["http://*/*", "https://*/*"], "js": ["visible.js", "fill.js"],
                             "run_at": "document_idle"}],
        "icons": {str(size): f"icons/{size}.png" for size in ICON_SIZES},
        # NEVER IN A PRIVATE WINDOW (the user, 2026-11-05): what is opened there is meant to leave
        # nothing behind, and with this the browser does not even ask.
        "incognito": "not_allowed",
    }
    if flavor == "firefox":
        said["background"] = {"scripts": ["background.js"]}
        # NOTHING COLLECTED - asked of every new extension by Firefox's store:
        # what is right-clicked goes to the user's own agent, on this
        # computer (native messaging), and nowhere else.
        said["browser_specific_settings"] = {"gecko": {
            "id": FIREFOX_ID, "strict_min_version": "115.0",
            "data_collection_permissions": {"required": ["none"]}}}
    else:
        said["background"] = {"service_worker": "background.js"}
        said["key"] = KEY
        said["minimum_chrome_version"] = "116"
        # THE BOX OPENED AT ONCE (the user, 2026-11-02): the agent's own page
        # - and only it - may ask the extension for its sign-in, once
        # (background.js, `onMessageExternal`). No access to it is asked.
        said["externally_connectable"] = {"matches": ["http://127.0.0.1/*",
                                                      "http://localhost/*"]}
    return said


def _version(version: str) -> str:
    """The agent's version as an extension's: up to four numbers."""
    parts = [part for part in str(version or "").split("+")[0].split("-")[0].split(".")
             if part.isdigit()][:4]
    return ".".join(parts) if parts else "0.1"


def prepare(data_dir: Path, version: str, icon: Any = None) -> Dict[str, str]:
    """The extension's folders made again - each flavor's, from the sources,
    with its manifest, its words and its icons (`icon`: the avatar's face, a
    picture; none: no icons). Only the agent's own folder is written."""
    out: Dict[str, str] = {}
    for flavor in FLAVORS:
        where = folder(data_dir) / flavor
        if where.exists():
            shutil.rmtree(where)
        where.mkdir(parents=True)
        for name in FILES:
            shutil.copyfile(source() / name, where / name)
        said = manifest(flavor, EXTENSION_VERSION)
        if icon is not None:
            (where / "icons").mkdir()
            for size in ICON_SIZES:
                icon.resize((size, size)).save(where / "icons" / f"{size}.png")
        else:
            said.pop("icons", None)
        for code, words in WORDS.items():
            locale = where / "_locales" / ("zh_CN" if code == "zh" else code)
            locale.mkdir(parents=True)
            _write_json(locale / "messages.json",
                        {key: {"message": value} for key, value in words.items()})
        _write_json(where / "manifest.json", said)
        out[flavor] = str(where)
    return out


def package(out_dir: Path, version: str, icon: Any = None) -> Dict[str, str]:
    """THE STORES' PACKAGES: each flavor's folder as a zip to upload - the
    Chromium one without its `key` (a store refuses one, and gives its own
    id). Made in `out_dir`, from a fresh copy of the folders beside it."""
    import zipfile
    out_dir = Path(out_dir)
    made = prepare(out_dir / "staging", version, icon)
    zips: Dict[str, str] = {}
    for flavor, where in made.items():
        root = Path(where)
        said = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        said.pop("key", None)
        _write_json(root / "manifest.json", said)
        name = {"chromium": "chrome-edge", "firefox": "firefox"}[flavor]
        target = out_dir / f"personal-agent-watermelon-{name}-{EXTENSION_VERSION}.zip"
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as box:
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    box.write(path, path.relative_to(root).as_posix())
        zips[flavor] = str(target)
    shutil.rmtree(out_dir / "staging", ignore_errors=True)
    return zips


def _write_json(path: Path, value: Any) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


# -- the host, made known to the browsers -------------------------------------------------
def launcher(data_dir: Path, command: List[str], platform: str = sys.platform) -> Path:
    """What each browser starts: this program, as the agent is started, as
    the host. The browsers start a file, with no arguments of its own."""
    hosts = folder(data_dir) / "host"
    hosts.mkdir(parents=True, exist_ok=True)
    if platform == "win32":
        path = hosts / "native_host.cmd"
        line = subprocess.list2cmdline(list(command) + ["--native-host"]).replace("%", "%%")
        path.write_text(f"@echo off\r\n{line} %*\r\n", encoding="utf-8")
    else:
        path = hosts / "native_host.sh"
        line = " ".join(shlex.quote(one) for one in list(command) + ["--native-host"])
        path.write_text(f"#!/bin/sh\nexec {line} \"$@\"\n", encoding="utf-8")
        path.chmod(0o755)
    return path


def host_manifest(flavor: str, path: Path) -> Dict[str, Any]:
    said: Dict[str, Any] = {"name": HOST, "description": "Personal Agent WaterMelon",
                            "path": str(path), "type": "stdio"}
    if flavor == "firefox":
        said["allowed_extensions"] = [FIREFOX_ID]
    else:
        said["allowed_origins"] = [f"chrome-extension://{one}/"
                                   for one in (CHROMIUM_ID,) + tuple(STORE_IDS)]
    return said


#: Where each browser looks for a host - Windows: its key in the user's part
#: of the registry; macOS and Linux: its folder, under the home folder (the
#: browser's own folder must be there: it is on this computer).
WINDOWS_KEYS: Tuple[Tuple[str, str, str], ...] = (
    ("Google Chrome", "chromium", r"Software\Google\Chrome\NativeMessagingHosts"),
    ("Brave", "chromium", r"Software\BraveSoftware\Brave-Browser\NativeMessagingHosts"),
    ("Vivaldi", "chromium", r"Software\Vivaldi\NativeMessagingHosts"),
    ("Chromium", "chromium", r"Software\Chromium\NativeMessagingHosts"),
    ("Firefox", "firefox", r"Software\Mozilla\NativeMessagingHosts"))
MAC_FOLDERS: Tuple[Tuple[str, str, str], ...] = (
    ("Google Chrome", "chromium", "Library/Application Support/Google/Chrome"),
    ("Google Chrome Beta", "chromium", "Library/Application Support/Google/Chrome Beta"),
    ("Brave", "chromium", "Library/Application Support/BraveSoftware/Brave-Browser"),
    ("Vivaldi", "chromium", "Library/Application Support/Vivaldi"),
    ("Chromium", "chromium", "Library/Application Support/Chromium"),
    ("Firefox", "firefox", "Library/Application Support/Mozilla"))
LINUX_FOLDERS: Tuple[Tuple[str, str, str], ...] = (
    ("Google Chrome", "chromium", ".config/google-chrome"),
    ("Google Chrome Beta", "chromium", ".config/google-chrome-beta"),
    ("Brave", "chromium", ".config/BraveSoftware/Brave-Browser"),
    ("Vivaldi", "chromium", ".config/vivaldi"),
    ("Chromium", "chromium", ".config/chromium"),
    ("Firefox", "firefox", ".mozilla"))


#: EDGE NO LONGER (the user, 2026-11-02): where an earlier version made the host
#: known to it - taken out by `remove`, never written again.
LEGACY_EDGE = {"win32": r"Software\Microsoft\Edge\NativeMessagingHosts",
               "darwin": "Library/Application Support/Microsoft Edge",
               "linux": ".config/microsoft-edge"}


def _host_folder(flavor: str, base: Path) -> Path:
    return base / ("native-messaging-hosts" if flavor == "firefox" and base.name == ".mozilla"
                   else "NativeMessagingHosts")


class Registry:
    """The user's part of the registry (HKCU) - the only part written."""

    def set(self, key: str, value: str) -> None:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as handle:
            winreg.SetValueEx(handle, "", 0, winreg.REG_SZ, value)

    def get(self, key: str) -> Optional[str]:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
                return str(winreg.QueryValueEx(handle, "")[0])
        except OSError:
            return None

    def delete(self, key: str) -> None:
        import winreg
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        except OSError:
            pass


def register(data_dir: Path, command: List[str], platform: str = sys.platform,
             home: Optional[Path] = None, registry: Any = None) -> List[str]:
    """The host made known to each browser on this computer: the browsers
    it was made known to."""
    path = launcher(data_dir, command, platform)
    manifests = folder(data_dir) / "host"
    known: List[str] = []
    if platform == "win32":
        registry = registry or Registry()
        for flavor in FLAVORS:
            _write_json(manifests / f"{HOST}.{flavor}.json", host_manifest(flavor, path))
        for name, flavor, key in WINDOWS_KEYS:
            registry.set(f"{key}\\{HOST}", str(manifests / f"{HOST}.{flavor}.json"))
            known.append(name)
        return known
    home = Path(home or Path.home())
    for name, flavor, base in (MAC_FOLDERS if platform == "darwin" else LINUX_FOLDERS):
        if not (home / base).is_dir():
            continue                        # not on this computer
        where = _host_folder(flavor, home / base)
        where.mkdir(parents=True, exist_ok=True)
        _write_json(where / f"{HOST}.json", host_manifest(flavor, path))
        known.append(name)
    return known


def connected(data_dir: Path, platform: str = sys.platform, home: Optional[Path] = None,
              registry: Any = None) -> List[str]:
    """The browsers the host is known to, as it stands now."""
    if platform == "win32":
        registry = registry or Registry()
        return [name for name, _flavor, key in WINDOWS_KEYS
                if registry.get(f"{key}\\{HOST}")
                and Path(str(registry.get(f"{key}\\{HOST}"))).exists()]
    home = Path(home or Path.home())
    return [name for name, flavor, base in (MAC_FOLDERS if platform == "darwin"
                                            else LINUX_FOLDERS)
            if (_host_folder(flavor, home / base) / f"{HOST}.json").exists()]


def remove(data_dir: Path, platform: str = sys.platform, home: Optional[Path] = None,
           registry: Any = None) -> None:
    """Exactly what `install` put in, taken out: the keys or files that name
    the host - only those naming this one - and the agent's own folder."""
    if platform == "win32":
        registry = registry or Registry()
        for key in [key for _name, _flavor, key in WINDOWS_KEYS] + [LEGACY_EDGE["win32"]]:
            if registry.get(f"{key}\\{HOST}"):
                registry.delete(f"{key}\\{HOST}")
    else:
        home = Path(home or Path.home())
        legacy = LEGACY_EDGE["darwin" if platform == "darwin" else "linux"]
        for _name, flavor, base in [*(MAC_FOLDERS if platform == "darwin" else LINUX_FOLDERS),
                                    ("Microsoft Edge", "chromium", legacy)]:
            (_host_folder(flavor, home / base) / f"{HOST}.json").unlink(missing_ok=True)
    if folder(data_dir).exists():
        shutil.rmtree(folder(data_dir))


def keep_current(data_dir: Path, command: List[str], version: str, icon: Any = None,
                 platform: str = sys.platform, home: Optional[Path] = None,
                 registry: Any = None) -> List[str]:
    """INSTALLED ALREADY, KEPT AS THIS VERSION HAS IT - at each start (the
    store's id, 2026-11-02: a host made known before it did not name it,
    and the store's extension was refused until the user installed again).
    Only what `install` wrote is written again, where it is: the host's
    manifests (on Windows the files the registry names - the registry itself
    untouched; elsewhere only those already in a browser's folder) and the
    extension's folders. Never installed: nothing. The browsers it is known
    to, as it stands."""
    if not folder(data_dir).exists():
        return []
    known = connected(data_dir, platform, home, registry)
    if not known:
        return []
    path = launcher(data_dir, command, platform)
    if platform == "win32":
        for flavor in FLAVORS:
            manifest_file = folder(data_dir) / "host" / f"{HOST}.{flavor}.json"
            if manifest_file.exists():
                _write_json(manifest_file, host_manifest(flavor, path))
    else:
        home = Path(home or Path.home())
        for name, flavor, base in (MAC_FOLDERS if platform == "darwin" else LINUX_FOLDERS):
            target = _host_folder(flavor, home / base) / f"{HOST}.json"
            if name in known and target.exists():
                _write_json(target, host_manifest(flavor, path))
    # Folders loaded by hand (a developer's): made again - where the sources
    # are here; the public copy has none (the stores' extension is the users').
    if source().is_dir() and any((folder(data_dir) / flavor / "manifest.json").exists()
                                 for flavor in FLAVORS):
        prepare(data_dir, version, icon)
    return known


def install(data_dir: Path, command: List[str], version: str, icon: Any = None,
            platform: str = sys.platform, home: Optional[Path] = None,
            registry: Any = None) -> Dict[str, Any]:
    """On the user's word (Settings > General): the host made known to the
    browsers here - the extension itself comes from the stores (STORES), not
    from a folder here (the user, 2026-11-02)."""
    known = register(data_dir, command, platform, home, registry)
    return status(data_dir, platform, home, registry, known=known)


def open_store(browser: str, platform: str = sys.platform) -> bool:
    """The extension's page in its store, opened in that browser - "chrome"
    or "firefox"; in the default browser where that one is not found here.
    Only opened: nothing clicked in it."""
    which = "firefox" if browser == "firefox" else "chrome"
    url = STORES.get(which)
    if not url:
        return False
    program = _browser_program(which, platform)
    try:
        if program:
            # ITS OWN OUTPUT KEPT OUT OF THE APPLICATION'S (the user, 2026-11-08: a browser started
            # here wrote "libpng warning: iCCP: known incorrect sRGB profile" in the run log).
            subprocess.Popen(program + [url], close_fds=True, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        import webbrowser
        return bool(webbrowser.open(url))
    except OSError:
        log.debug("could not open the store for %s", browser, exc_info=True)
        return False


def status(data_dir: Path, platform: str = sys.platform, home: Optional[Path] = None,
           registry: Any = None, folders: Optional[Dict[str, str]] = None,
           known: Optional[List[str]] = None) -> Dict[str, Any]:
    if folders is None:
        folders = {flavor: str(folder(data_dir) / flavor) for flavor in FLAVORS
                   if (folder(data_dir) / flavor / "manifest.json").exists()}
    if known is None:
        try:
            known = connected(data_dir, platform, home, registry)
        except Exception:
            known = []
    return {"prepared": bool(known), "folders": folders, "connected": known,
            "browsers": browsers(data_dir),
            "id": CHROMIUM_ID, "firefox_id": FIREFOX_ID, "stores": dict(STORES),
            "pages": EXTENSIONS_PAGES}


#: Where each browser lists its extensions - and loads one from a folder.
EXTENSIONS_PAGES = {"chromium": "chrome://extensions", "edge": "edge://extensions",
                    "brave": "brave://extensions",
                    "firefox": "about:debugging#/runtime/this-firefox",
                    # Where a store's extension, paused, is turned on again.
                    "firefox_addons": "about:addons"}


#: Each browser's program, as Windows names its process - for the icon
#: beside selected text, which leaves a browser with the extension to it.
PROCESSES = {"Google Chrome": "chrome.exe", "Microsoft Edge": "msedge.exe",
             "Brave": "brave.exe", "Vivaldi": "vivaldi.exe", "Opera": "opera.exe",
             "Firefox": "firefox.exe"}
#: The extension said it was there within this long: it still is.
SEEN_DAYS = 14


def from_store(caller: str, install: str = "") -> bool:
    """WHETHER IT IS THE STORES' EXTENSION that said it is here (the user,
    2026-11-02: "应该只check browser public 版本的install"): by its own word
    where it gives it ("install" - the browser's installType: "normal" from a
    store, "development" loaded by hand); else, in a Chromium browser, by the
    origin it called from - a store's id or not. Firefox's own id is the same
    either way: an extension too old to say is taken at its word."""
    if install:
        return install in ("normal", "admin")
    if caller.startswith("chrome-extension://"):
        return caller[len("chrome-extension://"):].strip("/") in STORE_IDS
    return True


def mark_seen(data_dir: Path, browser: str, store: bool = True) -> None:
    """The extension is in this browser: kept, with when (`seen_processes`) -
    and, the stores' one, apart (`browsers`: Settings' rows)."""
    if browser not in PROCESSES:
        return
    import time
    for name in (["seen.json", "store_seen.json"] if store else ["seen.json"]):
        path = folder(data_dir) / name
        try:
            said = json.loads(path.read_text(encoding="utf-8"))
            said = said if isinstance(said, dict) else {}
        except (OSError, ValueError):
            said = {}
        said[browser] = time.time()
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(path, said)


#: THE TWO ROWS of Settings (the user, 2026-11-02: "直接就应该列出2个版本
#: (chrome, firefox), 然后检查状态是否安装"): each store's browser, and the
#: browsers whose word counts for it - Chrome's store serves the others too.
ROWS = (("chrome", "Chrome", ("Google Chrome", "Brave", "Vivaldi", "Opera")),
        ("firefox", "Firefox", ("Firefox",)))


def _browser_homes(key: str, platform: str = sys.platform) -> List[Path]:
    """Where each browser of a row keeps its profiles."""
    home = Path.home()
    if platform == "win32":
        local, roaming = Path(os.environ.get("LOCALAPPDATA") or home), Path(os.environ.get("APPDATA") or home)
        if key == "firefox":
            return [roaming / "Mozilla" / "Firefox" / "Profiles"]
        return [local / "Google" / "Chrome" / "User Data",
                local / "BraveSoftware" / "Brave-Browser" / "User Data",
                local / "Vivaldi" / "User Data", roaming / "Opera Software"]
    if platform == "darwin":
        support = home / "Library" / "Application Support"
        if key == "firefox":
            return [support / "Firefox" / "Profiles"]
        return [support / "Google" / "Chrome", support / "BraveSoftware" / "Brave-Browser",
                support / "Vivaldi", support / "com.operasoftware.Opera"]
    if key == "firefox":
        return [home / ".mozilla" / "firefox"]
    config = home / ".config"
    return [config / "google-chrome", config / "BraveSoftware" / "Brave-Browser",
            config / "vivaldi", config / "opera"]


def _on_disk(key: str) -> Optional[bool]:
    """WHETHER THE STORES' EXTENSION IS STILL IN ITS BROWSER'S FOLDERS (the user, 2026-11-05: removed
    from Chrome, the page still called it installed - a removed extension says nothing). Only that
    its folder is there; nothing in it is read. None: no such browser's folders to look in."""
    homes = [home for home in _browser_homes(key) if home.is_dir()]
    if not homes:
        return None
    if key == "firefox":
        return any(next(home.glob(f"*/extensions/{FIREFOX_ID}.xpi"), None) for home in homes)
    return any(next(home.glob(f"*/Extensions/{one}"), None) or (home / "Extensions" / one).is_dir()
               for home in homes for one in STORE_IDS)


#: A FORM'S ORDER TO FILL, left by the agent as it opens the page (the user, 2026-11-08): given to the
#: pages of that site within so many seconds - room for a sign-in first - until one fills a box of it.
FILL_ORDER_SECONDS = 600


def leave_fill_order(data_dir: Path, url: str, parts: Any, attach: Any = None) -> str:
    """The boxes of the page about to be opened, for the extension to fill (fill.js) - its id. `attach`:
    the files it wants, said on the page for the user to attach - never uploaded (2026-11-13)."""
    import time
    import uuid
    from urllib.parse import urlsplit
    host = urlsplit(str(url or "")).hostname or ""
    kept = [{"field": str(one.get("field") or "")[:200], "text": str(one.get("text") or "")[:20000]}
            for one in (parts or []) if isinstance(one, dict)][:500]
    files = [{"field": str(one.get("field") or "")[:200], "name": str(one.get("name") or "")[:200],
              "path": str(one.get("path") or "")[:1000]}
             for one in (attach or []) if isinstance(one, dict) and one.get("path")][:20]
    if not host or not kept:
        return ""
    ident = uuid.uuid4().hex[:16]
    where = folder(data_dir) / "fill"
    where.mkdir(parents=True, exist_ok=True)
    _write_json(where / f"{ident}.json", {"id": ident, "url": str(url), "host": host,
                                         "parts": kept, "attach": files, "at": time.time()})
    return ident


def take_fill_order(data_dir: Path, url: str, keep: bool = False) -> Optional[Dict[str, Any]]:
    """The order left for a page of this one's site, not too old. `keep`: given and kept - a site's
    sign-in page, opened first, fills none of it and the form after it is given it still (a side
    agent's note, 2026-11-08); let go of by `finish_fill_order` once a page fills a box of it.
    Not kept: gone once given. Old ones let go of as they are looked at."""
    import time
    from urllib.parse import urlsplit
    host = urlsplit(str(url or "")).hostname or ""
    where = folder(data_dir) / "fill"
    found = None
    for path in sorted(where.glob("*.json")) if where.is_dir() else []:
        try:
            order = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            order = {}
        old = time.time() - float(order.get("at") or 0) > FILL_ORDER_SECONDS
        mine = not old and found is None and host and order.get("host") == host
        if old or (mine and not keep):
            try:
                path.unlink()
            except OSError:
                continue
        if mine:
            found = {"id": order.get("id"), "parts": order.get("parts") or [],
                     "attach": order.get("attach") or []}
    return found


def finish_fill_order(data_dir: Path, ident: str) -> bool:
    """A page filled boxes of this order: let go of - no other page is given it."""
    if not ident or not str(ident).isalnum():
        return False
    path = folder(data_dir) / "fill" / f"{ident}.json"
    try:
        path.unlink()
        return True
    except OSError:
        return False


def keep_visible(data_dir: Path, title: str, text: str, url: str = "") -> None:
    """What can be seen of the page in front, as the extension says: the last word only, on this
    computer, for the recommendations' match (heartbeat/monitor/match.py)."""
    import time
    from ..common import privacy_folders as private
    path = folder(data_dir) / "visible.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        among = json.loads((folder(data_dir) / private.ADDRESSES_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        among = []
    if private.site_signal(title) or (url and private.address_in(url, list(among or []))):
        # A PRIVATE PAGE - its site asking (agent-private-mode), or at a private address: nothing
        # of it kept, and what was kept before let go of; its title told, its window known by it.
        _write_json(path, {"at": time.time(), "title": "", "text": ""})
        if title and not private.site_signal(title):
            pages = folder(data_dir) / private.PAGES_FILE
            try:
                told = json.loads(pages.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                told = {}
            told = {key: at for key, at in (told or {}).items()
                    if time.time() - float(at) < private.PAGES_FOR_SECONDS}
            told[str(title)[:300]] = time.time()
            _write_json(pages, dict(list(told.items())[-500:]))
        return
    _write_json(path, {"at": time.time(), "title": str(title)[:300], "text": str(text)[:6000]})


def visible_report(data_dir: Path) -> Dict[str, Any]:
    try:
        said = json.loads((folder(data_dir) / "visible.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return said if isinstance(said, dict) else {}


def _seen(data_dir: Path, name: str = "store_seen.json") -> Dict[str, float]:
    try:
        said = json.loads((folder(data_dir) / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(name): float(at) for name, at in (said.items() if isinstance(said, dict) else [])
            if isinstance(at, (int, float))}


def browsers(data_dir: Path) -> List[Dict[str, Any]]:
    """WHETHER THE STORES' EXTENSION IS IN EACH: it says so itself - as it
    is installed, as its browser starts, as it is used ("here",
    native_host.py) - the stores' one alone counted (`from_store`). Its
    STATE: "installed", heard from within SEEN_DAYS; "quiet", heard from
    once and not since - PAUSED (an update asking for more is held until
    the user turns it on again) or removed (the user, 2026-11-02: "extension
    如果被暂停了，也要提醒用户"); "absent", never. Its store's page to get it."""
    import time
    seen, since = _seen(data_dir), time.time() - SEEN_DAYS * 86400
    out = []
    for key, name, said_as in ROWS:
        last = max([seen[one] for one in said_as if one in seen] or [0.0])
        if last and _on_disk(key) is False:
            last = 0.0
        state = "installed" if last >= since else "quiet" if last else "absent"
        out.append({"key": key, "name": name, "installed": state == "installed",
                    "state": state, "seen_at": last or None,
                    "store": STORES.get(key, "")})
    return out


def seen_processes(data_dir: Path) -> set:
    """The programs of the browsers the extension is in - any of it, the
    stores' or one loaded by hand: its own icon is there, so the agent's is
    not."""
    import time
    said = _seen(data_dir, "seen.json")
    since = time.time() - SEEN_DAYS * 86400
    return {PROCESSES[name] for name, at in said.items()
            if name in PROCESSES and isinstance(at, (int, float)) and at >= since}


def open_extensions_page(browser: str, platform: str = sys.platform) -> bool:
    """The browser opened on its extensions page - only opened, nothing
    clicked in it. False when the browser is not found here."""
    page = {"chrome": EXTENSIONS_PAGES["chromium"], "edge": EXTENSIONS_PAGES["edge"],
            "brave": EXTENSIONS_PAGES["brave"],
            "firefox": EXTENSIONS_PAGES["firefox_addons"]}.get(browser)
    program = _browser_program(browser, platform)
    if not page or not program:
        return False
    try:
        subprocess.Popen(program + [page], close_fds=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        log.debug("could not open %s", browser, exc_info=True)
        return False


def _browser_program(browser: str, platform: str) -> Optional[List[str]]:
    if platform == "darwin":
        app = {"chrome": "Google Chrome", "edge": "Microsoft Edge", "brave": "Brave Browser",
               "firefox": "Firefox"}.get(browser)
        if app and Path(f"/Applications/{app}.app").exists():
            return ["open", "-a", app]
        return None
    if platform == "win32":
        exe = {"chrome": "chrome.exe", "edge": "msedge.exe", "brave": "brave.exe",
               "firefox": "firefox.exe"}.get(browser)
        if not exe:
            return None
        try:
            import winreg
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(hive, "Software\\Microsoft\\Windows\\CurrentVersion"
                                              f"\\App Paths\\{exe}") as key:
                        found = str(winreg.QueryValueEx(key, "")[0]).strip('"')
                        if found and Path(found).exists():
                            return [found]
                except OSError:
                    continue
        except ImportError:
            return None
        return None
    names = {"chrome": ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"),
             "edge": ("microsoft-edge", "microsoft-edge-stable"),
             "brave": ("brave-browser", "brave"), "firefox": ("firefox",)}.get(browser, ())
    for name in names:
        found = shutil.which(name)
        if found:
            return [found]
    return None


def reveal(path: str, platform: str = sys.platform) -> bool:
    """The extension's folder shown in the file manager - to pick it in
    "Load unpacked"."""
    if not path or not Path(path).is_dir():
        return False
    try:
        if platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        elif platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return True
    except OSError:
        return False


__all__ = ["HOST", "KEY", "CHROMIUM_ID", "FIREFOX_ID", "STORE_IDS", "EXTENSION_VERSION", "package", "keep_current", "open_store", "from_store", "install", "remove", "status",
           "prepare", "register", "connected", "manifest", "host_manifest", "launcher",
           "open_extensions_page", "reveal", "extension_id", "WORDS", "keep_visible",
           "visible_report"]
