"""THE AGENT'S BROWSER EXTENSION (the user, 2026-11-01: "build一个extension, 里面
之后可能会bundle很多功能; settings里有install extension的位置 (general里,
update下面), 点击之后跳转install界面").

One extension, for Chrome, Edge, Brave, Vivaldi, Chromium and Firefox, on
Windows, macOS and Linux - its sources in resources/browser_extension. Today
it puts DELEGATE WORK on the right-click menu of every page (background.js);
it is the place for more of the agent's parts in the browser later.

It reaches the agent by the browser's NATIVE MESSAGING: the browser starts
this program (`--native-host`, platform_layer/native_host.py) and hands it
what was right-clicked on its standard input - no network, no port, and only
this extension may (its id is fixed by KEY, and the host names it).

Installed from Settings > General, on the user's word (`install`):

* THE EXTENSION'S FOLDERS made in the agent's data folder - one for the
  Chromium browsers, one for Firefox - each with its manifest, its words in
  the display languages, and the avatar's face as its icon. Until it is in
  the browsers' stores, the user loads the folder: "Load unpacked" in
  Chrome, Edge and Brave; "Load Temporary Add-on" in Firefox.
* THE HOST made known to each browser: a manifest naming the launcher and
  the extension, in the browser's own place for it - on Windows the user's
  part of the registry (HKCU), elsewhere the browser's NativeMessagingHosts
  folder, for each browser found on this computer.

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
#: The stores' pages, once it is in them - none yet: loaded from its folder.
STORES: Dict[str, str] = {}
#: The files of the extension, as they are in resources/browser_extension.
FILES = ("background.js", "help.html", "help.js", "opening.html", "opening.js")
FLAVORS = ("chromium", "firefox")
ICON_SIZES = (16, 32, 48, 128)

#: Its words, in each display language.
WORDS: Dict[str, Dict[str, str]] = {
    "en": {"opening": "Opening…",
           "delegate": "Delegate work",
           "learn": "Learn",
           "description": "Right-click any page, selected text, a link or a picture - "
                          "and hand it to your agent as a piece of work.",
           "help_head": "The agent is not connected to this browser yet",
           "help_do": "Open Personal Agent WaterMelon, go to Settings, then General, and press "
                      "\"Install the browser extension\". It connects this browser to the agent "
                      "on this computer.",
           "help_then": "Then right-click the page again and pick Delegate work."},
    "zh": {"opening": "正在打开…",
           "delegate": "委托工作",
           "learn": "学习",
           "description": "在任何网页、选中的文字、链接或图片上右键，把它作为一项工作交给你的 agent。",
           "help_head": "agent 还没有连接到这个浏览器",
           "help_do": "打开 Personal Agent WaterMelon，进入 设置 > 通用，点“安装浏览器扩展”。"
                      "它会把这个浏览器连接到这台电脑上的 agent。",
           "help_then": "然后再在网页上右键，选“委托工作”。"},
    "ja": {"opening": "開いています…",
           "delegate": "作業を任せる",
           "learn": "学習",
           "description": "ページ、選択したテキスト、リンク、画像を右クリックして、作業としてエージェントに任せます。",
           "help_head": "エージェントはまだこのブラウザーにつながっていません",
           "help_do": "Personal Agent WaterMelon を開き、設定 > 全般 で「ブラウザー拡張機能をインストール」を"
                      "押してください。このブラウザーがこのコンピューターのエージェントにつながります。",
           "help_then": "そのあと、もう一度ページを右クリックして「作業を任せる」を選んでください。"},
    "ko": {"opening": "여는 중…",
           "delegate": "작업 맡기기",
           "learn": "학습",
           "description": "페이지, 선택한 텍스트, 링크, 그림을 오른쪽 클릭해 작업으로 에이전트에게 맡깁니다.",
           "help_head": "에이전트가 아직 이 브라우저에 연결되지 않았습니다",
           "help_do": "Personal Agent WaterMelon을 열고 설정 > 일반에서 \"브라우저 확장 프로그램 설치\"를 "
                      "누르세요. 이 브라우저가 이 컴퓨터의 에이전트에 연결됩니다.",
           "help_then": "그런 다음 페이지를 다시 오른쪽 클릭하고 \"작업 맡기기\"를 고르세요."},
    "de": {"opening": "Wird geöffnet…",
           "delegate": "Arbeit delegieren",
           "learn": "Lernen",
           "description": "Rechtsklick auf eine Seite, markierten Text, einen Link oder ein Bild - "
                          "und Ihr Agent bekommt es als Aufgabe.",
           "help_head": "Der Agent ist noch nicht mit diesem Browser verbunden",
           "help_do": "Öffnen Sie Personal Agent WaterMelon, dann Einstellungen > Allgemein, und "
                      "drücken Sie \"Browser-Erweiterung installieren\". Das verbindet diesen "
                      "Browser mit dem Agenten auf diesem Computer.",
           "help_then": "Dann klicken Sie die Seite wieder mit rechts an und wählen Arbeit delegieren."},
    "fr": {"opening": "Ouverture…",
           "delegate": "Déléguer une tâche",
           "learn": "Apprendre",
           "description": "Clic droit sur une page, un texte sélectionné, un lien ou une image - "
                          "et votre agent le reçoit comme une tâche.",
           "help_head": "L'agent n'est pas encore relié à ce navigateur",
           "help_do": "Ouvrez Personal Agent WaterMelon, puis Paramètres > Général, et appuyez sur "
                      "« Installer l'extension du navigateur ». Elle relie ce navigateur à l'agent "
                      "de cet ordinateur.",
           "help_then": "Puis faites de nouveau un clic droit sur la page et choisissez Déléguer une tâche."},
    "es": {"opening": "Abriendo…",
           "delegate": "Delegar trabajo",
           "learn": "Aprender",
           "description": "Haz clic derecho en una página, un texto seleccionado, un enlace o una "
                          "imagen, y pásaselo a tu agente como un trabajo.",
           "help_head": "El agente aún no está conectado a este navegador",
           "help_do": "Abre Personal Agent WaterMelon, ve a Ajustes > General y pulsa \"Instalar la "
                      "extensión del navegador\". Conecta este navegador con el agente de este equipo.",
           "help_then": "Luego vuelve a hacer clic derecho en la página y elige Delegar trabajo."},
    "it": {"opening": "Apertura…",
           "delegate": "Delega un lavoro",
           "learn": "Impara",
           "description": "Clic destro su una pagina, un testo selezionato, un link o un'immagine - "
                          "e il tuo agente lo riceve come un lavoro.",
           "help_head": "L'agente non è ancora collegato a questo browser",
           "help_do": "Apri Personal Agent WaterMelon, vai su Impostazioni > Generale e premi "
                      "\"Installa l'estensione del browser\". Collega questo browser all'agente su "
                      "questo computer.",
           "help_then": "Poi fai di nuovo clic destro sulla pagina e scegli Delega un lavoro."},
    "ru": {"opening": "Открывается…",
           "delegate": "Поручить работу",
           "learn": "Изучить",
           "description": "Щёлкните правой кнопкой по странице, выделенному тексту, ссылке или "
                          "картинке - и агент получит это как задачу.",
           "help_head": "Агент ещё не подключён к этому браузеру",
           "help_do": "Откройте Personal Agent WaterMelon, затем Настройки > Общие, и нажмите "
                      "«Установить расширение браузера». Оно подключит этот браузер к агенту на "
                      "этом компьютере.",
           "help_then": "Затем снова щёлкните страницу правой кнопкой и выберите «Поручить работу»."},
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


def manifest(flavor: str, version: str) -> Dict[str, Any]:
    """The extension's manifest - Manifest V3 for both; Firefox's background
    is a script, the Chromium browsers' a service worker."""
    said: Dict[str, Any] = {
        "manifest_version": 3, "name": "Personal Agent WaterMelon",
        "short_name": "Personal Agent", "version": _version(version),
        "description": "__MSG_description__", "default_locale": "en",
        # "storage": where the agent's page is (its port), kept between
        # right-clicks - no warning is shown for it.
        "permissions": ["contextMenus", "nativeMessaging", "activeTab", "scripting", "storage"],
        # NO SCRIPT IN EVERY PAGE, NO ACCESS TO EVERY SITE (the user,
        # 2026-11-01: selected text in a page is the right-click's too): the
        # page right-clicked is read once Delegate work is picked (activeTab).
        "icons": {str(size): f"icons/{size}.png" for size in ICON_SIZES},
    }
    if flavor == "firefox":
        said["background"] = {"scripts": ["background.js"]}
        said["browser_specific_settings"] = {"gecko": {"id": FIREFOX_ID,
                                                       "strict_min_version": "115.0"}}
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
        said = manifest(flavor, version)
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
        said["allowed_origins"] = [f"chrome-extension://{CHROMIUM_ID}/"]
    return said


#: Where each browser looks for a host - Windows: its key in the user's part
#: of the registry; macOS and Linux: its folder, under the home folder (the
#: browser's own folder must be there: it is on this computer).
WINDOWS_KEYS: Tuple[Tuple[str, str, str], ...] = (
    ("Google Chrome", "chromium", r"Software\Google\Chrome\NativeMessagingHosts"),
    ("Microsoft Edge", "chromium", r"Software\Microsoft\Edge\NativeMessagingHosts"),
    ("Brave", "chromium", r"Software\BraveSoftware\Brave-Browser\NativeMessagingHosts"),
    ("Vivaldi", "chromium", r"Software\Vivaldi\NativeMessagingHosts"),
    ("Chromium", "chromium", r"Software\Chromium\NativeMessagingHosts"),
    ("Firefox", "firefox", r"Software\Mozilla\NativeMessagingHosts"))
MAC_FOLDERS: Tuple[Tuple[str, str, str], ...] = (
    ("Google Chrome", "chromium", "Library/Application Support/Google/Chrome"),
    ("Google Chrome Beta", "chromium", "Library/Application Support/Google/Chrome Beta"),
    ("Microsoft Edge", "chromium", "Library/Application Support/Microsoft Edge"),
    ("Brave", "chromium", "Library/Application Support/BraveSoftware/Brave-Browser"),
    ("Vivaldi", "chromium", "Library/Application Support/Vivaldi"),
    ("Chromium", "chromium", "Library/Application Support/Chromium"),
    ("Firefox", "firefox", "Library/Application Support/Mozilla"))
LINUX_FOLDERS: Tuple[Tuple[str, str, str], ...] = (
    ("Google Chrome", "chromium", ".config/google-chrome"),
    ("Google Chrome Beta", "chromium", ".config/google-chrome-beta"),
    ("Microsoft Edge", "chromium", ".config/microsoft-edge"),
    ("Brave", "chromium", ".config/BraveSoftware/Brave-Browser"),
    ("Vivaldi", "chromium", ".config/vivaldi"),
    ("Chromium", "chromium", ".config/chromium"),
    ("Firefox", "firefox", ".mozilla"))


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
        for _name, _flavor, key in WINDOWS_KEYS:
            if registry.get(f"{key}\\{HOST}"):
                registry.delete(f"{key}\\{HOST}")
    else:
        home = Path(home or Path.home())
        for _name, flavor, base in (MAC_FOLDERS if platform == "darwin" else LINUX_FOLDERS):
            (_host_folder(flavor, home / base) / f"{HOST}.json").unlink(missing_ok=True)
    if folder(data_dir).exists():
        shutil.rmtree(folder(data_dir))


def install(data_dir: Path, command: List[str], version: str, icon: Any = None,
            platform: str = sys.platform, home: Optional[Path] = None,
            registry: Any = None) -> Dict[str, Any]:
    """On the user's word (Settings > General): the folders made, and the
    host made known to the browsers."""
    folders = prepare(data_dir, version, icon)
    known = register(data_dir, command, platform, home, registry)
    return status(data_dir, platform, home, registry, folders=folders, known=known)


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
    return {"prepared": bool(folders), "folders": folders, "connected": known,
            "id": CHROMIUM_ID, "firefox_id": FIREFOX_ID, "stores": dict(STORES),
            "pages": EXTENSIONS_PAGES}


#: Where each browser lists its extensions - and loads one from a folder.
EXTENSIONS_PAGES = {"chromium": "chrome://extensions", "edge": "edge://extensions",
                    "brave": "brave://extensions",
                    "firefox": "about:debugging#/runtime/this-firefox"}


#: Each browser's program, as Windows names its process - for the icon
#: beside selected text, which leaves a browser with the extension to it.
PROCESSES = {"Google Chrome": "chrome.exe", "Microsoft Edge": "msedge.exe",
             "Brave": "brave.exe", "Vivaldi": "vivaldi.exe", "Opera": "opera.exe",
             "Firefox": "firefox.exe"}
#: The extension said it was there within this long: it still is.
SEEN_DAYS = 14


def mark_seen(data_dir: Path, browser: str) -> None:
    """The extension is in this browser: kept, with when (`seen_processes`)."""
    if browser not in PROCESSES:
        return
    import time
    path = folder(data_dir) / "seen.json"
    try:
        said = json.loads(path.read_text(encoding="utf-8"))
        said = said if isinstance(said, dict) else {}
    except (OSError, ValueError):
        said = {}
    said[browser] = time.time()
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, said)


def seen_processes(data_dir: Path) -> set:
    """The programs of the browsers the extension is in - its own icon is
    there, so the agent's is not."""
    import time
    try:
        said = json.loads((folder(data_dir) / "seen.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    if not isinstance(said, dict):
        return set()
    since = time.time() - SEEN_DAYS * 86400
    return {PROCESSES[name] for name, at in said.items()
            if name in PROCESSES and isinstance(at, (int, float)) and at >= since}


def open_extensions_page(browser: str, platform: str = sys.platform) -> bool:
    """The browser opened on its extensions page - only opened, nothing
    clicked in it. False when the browser is not found here."""
    page = {"chrome": EXTENSIONS_PAGES["chromium"], "edge": EXTENSIONS_PAGES["edge"],
            "brave": EXTENSIONS_PAGES["brave"],
            "firefox": EXTENSIONS_PAGES["firefox"]}.get(browser)
    program = _browser_program(browser, platform)
    if not page or not program:
        return False
    try:
        subprocess.Popen(program + [page], close_fds=True)
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


__all__ = ["HOST", "KEY", "CHROMIUM_ID", "FIREFOX_ID", "install", "remove", "status",
           "prepare", "register", "connected", "manifest", "host_manifest", "launcher",
           "open_extensions_page", "reveal", "extension_id", "WORDS"]
