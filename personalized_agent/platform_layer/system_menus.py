"""DELEGATE WORK ON THE SYSTEM'S OWN RIGHT-CLICK MENUS, beyond Windows'
Explorer (explorer_menu.py) - the user, 2026-11-01: "如果system menu已经可以
cover的，就用system menu".

* macOS - THE SERVICES MENU, which every program that shows text offers on
  its right-click menu: "Delegate work" on text selected in any program
  (`--delegate-text`, the text on its standard input), and on files and
  folders in Finder, as a Quick Action (`--delegate PATH`). Two Automator
  workflows in ~/Library/Services, made here - the system lists them at
  once (`pbs -update`).
* Linux - THE FILE MANAGERS': Nautilus (GNOME Files, its Scripts), Nemo
  (its actions), Dolphin (its service menus), Caja (its scripts) and Thunar
  (its custom actions) - each found on this computer. Text selected in a
  program has no system menu on Linux: there, the programs' own
  (editor_menus.py, the browser extension).

Always there, as Explorer's is - each start of the agent puts them in place
again, in the display language's words; `remove` takes out what was put in,
and nothing of anyone else's.
"""

from __future__ import annotations

import logging
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from xml.etree import ElementTree

log = logging.getLogger(__name__)

#: The workflows' names (the menu's words come from the display language).
TEXT_WORKFLOW = "Personal Agent - Delegate work.workflow"
FILES_WORKFLOW = "Personal Agent - Delegate work (files).workflow"
#: Thunar's action, by the id it is kept under.
THUNAR_ID = "personal-agent-watermelon-delegate"
NAME = "personal-agent-delegate"


def available(platform: str = sys.platform) -> bool:
    return platform == "darwin" or platform.startswith("linux")


def _menu_label(code: str) -> str:
    """The menu's words, in the display language `code` - no model's."""
    from .explorer_menu import LABELS
    return LABELS.get(code, LABELS["en"])


def _line(command: List[str]) -> str:
    return " ".join(shlex.quote(one) for one in command)


# -- macOS: the Services menu ---------------------------------------------------------------
def _shell_action(script: str, as_arguments: bool) -> Dict[str, Any]:
    """Automator's "Run Shell Script", as a workflow keeps it."""
    kinds = ["com.apple.cocoa.path"] if as_arguments else ["com.apple.cocoa.string"]
    argument = lambda n, name, default: {"default value": default, "name": name,  # noqa: E731
                                         "required": "0", "type": "0", "uuid": str(n)}
    return {"action": {
        "AMAccepts": {"Container": "List", "Optional": True, "Types": kinds},
        "AMActionVersion": "2.0.3", "AMApplication": ["Automator"],
        "AMParameterProperties": {key: {} for key in ("COMMAND_STRING",
                                                      "CheckedForUserDefaultShell",
                                                      "inputMethod", "shell", "source")},
        "AMProvides": {"Container": "List", "Types": kinds},
        "ActionBundlePath": "/System/Library/Automator/Run Shell Script.action",
        "ActionName": "Run Shell Script",
        "ActionParameters": {"COMMAND_STRING": script, "CheckedForUserDefaultShell": True,
                             "inputMethod": 1 if as_arguments else 0, "shell": "/bin/sh",
                             "source": ""},
        "BundleIdentifier": "com.apple.RunShellScript", "CFBundleVersion": "2.0.3",
        "CanShowSelectedItemsWhenRun": False, "CanShowWhenRun": True,
        "Category": ["AMCategoryUtilities"], "Class Name": "RunShellScriptAction",
        "InputUUID": str(uuid.uuid4()).upper(), "OutputUUID": str(uuid.uuid4()).upper(),
        "UUID": str(uuid.uuid4()).upper(),
        "Keywords": ["Shell", "Script", "Command", "Run", "Unix"],
        "UnlocalizedApplications": ["Automator"],
        "arguments": {"0": argument(0, "inputMethod", 0), "1": argument(1, "source", ""),
                      "2": argument(2, "CheckedForUserDefaultShell", True),
                      "3": argument(3, "COMMAND_STRING", ""),
                      "4": argument(4, "shell", "")},
        "isViewVisible": 1, "location": "309.000000:253.000000",
        "nibPath": "/System/Library/Automator/Run Shell Script.action/Contents/Resources/"
                   "Base.lproj/main.nib"},
        "isViewVisible": 1}


def workflow(label: str, script: str, files: bool) -> Dict[str, bytes]:
    """A Services workflow's two files: its Info.plist (the menu's words,
    what it takes) and its document.wflow (the script it runs)."""
    service: Dict[str, Any] = {"NSMenuItem": {"default": label},
                               "NSMessage": "runWorkflowAsService"}
    meta: Dict[str, Any] = {
        "serviceOutputTypeIdentifier": "com.apple.Automator.nothing",
        "serviceProcessesInput": False, "useAutomaticInputType": False,
        "workflowTypeIdentifier": "com.apple.Automator.servicesMenu",
        "systemImageName": "NSActionTemplate"}
    if files:
        service.update({"NSSendFileTypes": ["public.item"],
                        "NSRequiredContext": {"NSApplicationIdentifier": "com.apple.finder"}})
        meta.update({"serviceInputTypeIdentifier": "com.apple.Automator.fileSystemObject",
                     "serviceApplicationBundleID": "com.apple.finder",
                     "serviceApplicationPath": "/System/Library/CoreServices/Finder.app"})
    else:
        service["NSSendTypes"] = ["public.utf8-plain-text"]
        meta["serviceInputTypeIdentifier"] = "com.apple.Automator.text"
    document = {"AMApplicationBuild": "523", "AMApplicationVersion": "2.10",
                "AMDocumentVersion": "2", "actions": [_shell_action(script, files)],
                "connectors": {}, "workflowMetaData": meta}
    return {"Contents/Info.plist": plistlib.dumps({"NSServices": [service]}),
            "Contents/document.wflow": plistlib.dumps(document)}


def mac_scripts(command: List[str]) -> Dict[str, str]:
    line = _line(command)
    return {"text": f"exec {line} --delegate-text -\n",
            "files": f'for f in "$@"; do nohup {line} --delegate "$f" >/dev/null 2>&1 & done\n'}


def install_mac(home: Path, command: List[str], language: str = "en",
                refresh: bool = True) -> List[str]:
    services = home / "Library" / "Services"
    services.mkdir(parents=True, exist_ok=True)
    scripts = mac_scripts(command)
    made = []
    for name, script, files in ((TEXT_WORKFLOW, scripts["text"], False),
                                (FILES_WORKFLOW, scripts["files"], True)):
        where = services / name
        for relative, body in workflow(_menu_label(language), script, files).items():
            (where / relative).parent.mkdir(parents=True, exist_ok=True)
            (where / relative).write_bytes(body)
        made.append(str(where))
    if refresh and Path("/System/Library/CoreServices/pbs").exists():
        try:                                   # listed at once, not at the next sign-in
            subprocess.run(["/System/Library/CoreServices/pbs", "-update"], timeout=20,
                           capture_output=True)
        except (OSError, subprocess.SubprocessError):
            pass
    return made


def remove_mac(home: Path) -> None:
    for name in (TEXT_WORKFLOW, FILES_WORKFLOW):
        shutil.rmtree(home / "Library" / "Services" / name, ignore_errors=True)


# -- Linux: the file managers -----------------------------------------------------------------
def linux_script(command: List[str]) -> str:
    """Hands each chosen file or folder over - chosen together, they come as
    one piece of work (agent/work/handover.py gathers them). Nautilus names
    them in its own variable too."""
    line = _line(command)
    return ("#!/bin/sh\n"
            "if [ -n \"$NAUTILUS_SCRIPT_SELECTED_FILE_PATHS\" ]; then\n"
            "  printf '%s\\n' \"$NAUTILUS_SCRIPT_SELECTED_FILE_PATHS\" | while IFS= read -r f; do\n"
            f"    [ -n \"$f\" ] && nohup {line} --delegate \"$f\" >/dev/null 2>&1 &\n"
            "  done\n"
            "  exit 0\n"
            "fi\n"
            "for f in \"$@\"; do\n"
            f"  nohup {line} --delegate \"$f\" >/dev/null 2>&1 &\n"
            "done\n")


def _executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def linux_places(home: Path, which: Callable[[str], Optional[str]] = shutil.which
                 ) -> Dict[str, Path]:
    """Each file manager on this computer, and where its entry goes."""
    share = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    config = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    found: Dict[str, Path] = {}
    if which("nautilus") or (share / "nautilus").is_dir():
        found["nautilus"] = share / "nautilus" / "scripts"
    if which("nemo") or (share / "nemo").is_dir():
        found["nemo"] = share / "nemo" / "actions" / f"{NAME}.nemo_action"
    if which("dolphin"):
        found["dolphin"] = share / "kio" / "servicemenus" / f"{NAME}.desktop"
    if which("caja") or (config / "caja").is_dir():
        found["caja"] = config / "caja" / "scripts"
    if which("thunar") or (config / "Thunar").is_dir():
        found["thunar"] = config / "Thunar" / "uca.xml"
    return found


def install_linux(home: Path, data_dir: Path, command: List[str], language: str = "en",
                  which: Callable[[str], Optional[str]] = shutil.which) -> List[str]:
    label = _menu_label(language)
    script = Path(data_dir) / "system_menus" / "delegate.sh"
    _executable(script, linux_script(command))
    made = []
    for manager, where in linux_places(home, which).items():
        if manager in ("nautilus", "caja"):
            for old in where.glob("*") if where.is_dir() else []:
                if old.is_file() and _is_ours(old):
                    old.unlink()             # in another language before
            _executable(where / label, f"#!/bin/sh\n# {NAME}\nexec {_line([str(script)])} "
                                        "\"$@\"\n")
        elif manager == "nemo":
            where.parent.mkdir(parents=True, exist_ok=True)
            where.write_text(f"[Nemo Action]\nName={label}\nComment={label}\n"
                             f"Exec={_line([str(script)])} %F\nSelection=any\n"
                             "Extensions=any;\nIcon-Name=system-run\n", encoding="utf-8")
        elif manager == "dolphin":
            _executable(where, "[Desktop Entry]\nType=Service\nMimeType=all/all;\n"
                               "X-KDE-ServiceTypes=KonqPopupMenu/Plugin\nActions=delegate\n\n"
                               f"[Desktop Action delegate]\nName={label}\nIcon=system-run\n"
                               f"Exec={_line([str(script)])} %F\n")
        elif manager == "thunar":
            _thunar_add(where, label, script)
        made.append(manager)
    return made


def _is_ours(path: Path) -> bool:
    try:
        return f"# {NAME}" in path.read_text(encoding="utf-8", errors="replace")[:200]
    except OSError:
        return False


def _thunar_add(path: Path, label: str, script: Path) -> None:
    """The action put in Thunar's list of the user's own - theirs kept."""
    try:
        tree = ElementTree.parse(path)
        root = tree.getroot()
    except (OSError, ElementTree.ParseError):
        root = ElementTree.Element("actions")
        tree = ElementTree.ElementTree(root)
    for action in list(root.findall("action")):
        if (action.findtext("unique-id") or "") == THUNAR_ID:
            root.remove(action)
    action = ElementTree.SubElement(root, "action")
    for key, value in (("icon", "system-run"), ("name", label), ("unique-id", THUNAR_ID),
                       ("command", f"{_line([str(script)])} %F"), ("description", label),
                       ("patterns", "*")):
        ElementTree.SubElement(action, key).text = value
    for kind in ("directories", "audio-files", "image-files", "other-files", "text-files",
                 "video-files"):
        ElementTree.SubElement(action, kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(path, encoding="unicode", xml_declaration=True)


def remove_linux(home: Path, data_dir: Path,
                 which: Callable[[str], Optional[str]] = shutil.which) -> None:
    for manager, where in linux_places(home, which).items():
        if manager in ("nautilus", "caja") and where.is_dir():
            for old in where.glob("*"):
                if old.is_file() and _is_ours(old):
                    old.unlink()
        elif manager in ("nemo", "dolphin"):
            where.unlink(missing_ok=True)
        elif manager == "thunar" and where.exists():
            try:
                tree = ElementTree.parse(where)
            except ElementTree.ParseError:
                continue
            for action in list(tree.getroot().findall("action")):
                if (action.findtext("unique-id") or "") == THUNAR_ID:
                    tree.getroot().remove(action)
            tree.write(where, encoding="unicode", xml_declaration=True)
    shutil.rmtree(Path(data_dir) / "system_menus", ignore_errors=True)


# -- both -------------------------------------------------------------------------------------
def follow(data_dir: Path, command: List[str], language: str = "en",
           platform: str = sys.platform, home: Optional[Path] = None) -> List[str]:
    """Put in place again (each start of the agent): what was put in."""
    home = Path(home or Path.home())
    if platform == "darwin":
        return install_mac(home, command, language)
    if platform.startswith("linux"):
        return install_linux(home, data_dir, command, language)
    return []


def remove(data_dir: Path, platform: str = sys.platform, home: Optional[Path] = None) -> None:
    home = Path(home or Path.home())
    if platform == "darwin":
        remove_mac(home)
    elif platform.startswith("linux"):
        remove_linux(home, data_dir)


__all__ = ["follow", "remove", "workflow", "install_mac", "install_linux", "linux_places",
           "available"]
