"""DELEGATE WORK ON THE RIGHT-CLICK MENU OF THE USER'S EDITORS (the user,
2026-11-01: "VS Code / Cursor, JetBrains IDEs, 包括sublime, text editor之类 -
如果system menu已经可以cover的，就用system menu, 不然用per program").

Where the system's own menu reaches - selected text in any program on macOS
(the Services menu), files in a file manager - that is used (Explorer's
menu, system_menus.py). Where it does not, each program gets its own:

* VS CODE AND ITS KIN - VS Code, Cursor, VSCodium, Windsurf: a small
  extension (resources/editor_menus/vscode), packed as a .vsix and given to
  the editor's own command (`code --install-extension`). On the editor's,
  its tab's and its file explorer's menus.
* SUBLIME TEXT: a package (resources/editor_menus/sublime) put in its
  Packages folder - the icon beside selected text, and the file on the
  text's and the side bar's menus.
* JETBRAINS IDEs - PyCharm, IntelliJ IDEA, WebStorm and the rest: an
  External Tool, in a tool group of the agent's own in each IDE's settings
  folder - on the editor's and the project view's menus, under "Personal
  Agent": the file itself (`--delegate-file`). Text selected there has no
  icon yet: an External Tool cannot show one - a plugin of its own could.

TEXT IS THE ICON'S, THE FILE IS THE RIGHT-CLICK'S (the user, 2026-11-01):
selected text gets the agent's face beside it, opening into Delegate work
and Learn (VS Code's own light bulb); the right-click menus hand over the
file.

Each hands the agent what was right-clicked (agent/work/picked.py) and the
Delegate work box opens on it. Installed on the user's word, from Settings;
each `remove` takes out what its `install` put in, and nothing else.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from xml.sax.saxutils import quoteattr

log = logging.getLogger(__name__)

#: The VS Code extension's name, as the editors list it.
VSCODE_ID = "watermelon.personal-agent-delegate"
#: The Sublime Text package's folder.
SUBLIME_PACKAGE = "Personal Agent WaterMelon"
#: The JetBrains tool group's file, in each IDE's tools folder.
JETBRAINS_TOOLS = "Personal Agent WaterMelon.xml"
WORDS = "Delegate work"


def source(*parts: str) -> Path:
    from ..common.paths import resource_path
    return resource_path("resources", "editor_menus", *parts)


def _app_data(platform: str, home: Path) -> Path:
    if platform == "win32":
        return Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    if platform == "darwin":
        return home / "Library" / "Application Support"
    return Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")


# -- VS Code and its kin ----------------------------------------------------------------------
#: Each editor: its name, its command, and where that command is when it is
#: not on the PATH.
VSCODE_KIN = (
    ("Visual Studio Code", "code", {
        "win32": ["%LOCALAPPDATA%/Programs/Microsoft VS Code/bin/code.cmd",
                  "%PROGRAMFILES%/Microsoft VS Code/bin/code.cmd"],
        "darwin": ["/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"],
        "linux": ["/usr/bin/code", "/usr/share/code/bin/code", "/snap/bin/code"]}),
    ("Cursor", "cursor", {
        "win32": ["%LOCALAPPDATA%/Programs/cursor/resources/app/bin/cursor.cmd"],
        "darwin": ["/Applications/Cursor.app/Contents/Resources/app/bin/cursor"],
        "linux": ["/usr/bin/cursor", "/opt/Cursor/resources/app/bin/cursor"]}),
    ("VSCodium", "codium", {
        "win32": ["%LOCALAPPDATA%/Programs/VSCodium/bin/codium.cmd"],
        "darwin": ["/Applications/VSCodium.app/Contents/Resources/app/bin/codium"],
        "linux": ["/usr/bin/codium"]}),
    ("Windsurf", "windsurf", {
        "win32": ["%LOCALAPPDATA%/Programs/Windsurf/bin/windsurf.cmd"],
        "darwin": ["/Applications/Windsurf.app/Contents/Resources/app/bin/windsurf"],
        "linux": ["/usr/bin/windsurf"]}),
)


def _vscode_command(name: str, platform: str,
                    which: Callable[[str], Optional[str]] = shutil.which) -> Optional[str]:
    for one, command, places in VSCODE_KIN:
        if one != name:
            continue
        found = which(command)
        if found:
            return found
        for place in places.get("linux" if platform.startswith("linux") else platform, []):
            path = Path(os.path.expandvars(place))
            if "%" not in str(path) and path.exists():
                return str(path)
    return None


def vscode_package(command: List[str], version: str) -> Dict[str, Any]:
    """The extension's package.json: one command, on three menus."""
    when = "resourceScheme == file || editorTextFocus"
    return {
        "name": "personal-agent-delegate", "publisher": "watermelon",
        "displayName": "Personal Agent WaterMelon - Delegate work",
        "description": "Right-click a file, or text you selected, and hand it to your agent "
                       "as a piece of work.",
        "version": _version(version), "engines": {"vscode": "^1.60.0"},
        # Started with the editor: its light bulb is there from the first
        # selection.
        "main": "./extension.js", "activationEvents": ["onStartupFinished"],
        "contributes": {
            "commands": [{"command": "personalAgent.delegateWork", "title": WORDS,
                          "category": "Personal Agent"},
                         {"command": "personalAgent.handOver", "title": WORDS,
                          "category": "Personal Agent"}],
            "menus": {
                "editor/context": [{"command": "personalAgent.delegateWork",
                                    "group": "navigation@99"}],
                "editor/title/context": [{"command": "personalAgent.delegateWork",
                                          "group": "navigation@99", "when": when}],
                "explorer/context": [{"command": "personalAgent.delegateWork",
                                      "group": "navigation@99"}],
                # The light bulb's alone - not in the command list.
                "commandPalette": [{"command": "personalAgent.handOver",
                                    "when": "false"}]}},
    }


def _version(version: str) -> str:
    parts = [part for part in str(version or "").split("+")[0].split("-")[0].split(".")
             if part.isdigit()][:3]
    while len(parts) < 3:
        parts.append("0")
    return ".".join(parts)


def build_vsix(where: Path, command: List[str], version: str) -> Path:
    """The extension packed as a .vsix - an editor's own installer takes it."""
    package = vscode_package(command, version)
    path = Path(where) / f"personal-agent-delegate-{package['version']}.vsix"
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = f"""<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011">
  <Metadata>
    <Identity Language="en-US" Id="{package['name']}" Version="{package['version']}" Publisher="{package['publisher']}"/>
    <DisplayName>{package['displayName']}</DisplayName>
    <Description xml:space="preserve">{package['description']}</Description>
    <Categories>Other</Categories>
    <Properties>
      <Property Id="Microsoft.VisualStudio.Code.Engine" Value="{package['engines']['vscode']}"/>
    </Properties>
  </Metadata>
  <Installation><InstallationTarget Id="Microsoft.VisualStudio.Code"/></Installation>
  <Dependencies/>
  <Assets>
    <Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true"/>
  </Assets>
</PackageManifest>
"""
    types = """<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension=".json" ContentType="application/json"/>
  <Default Extension=".js" ContentType="application/javascript"/>
  <Default Extension=".vsixmanifest" ContentType="text/xml"/>
</Types>
"""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as pack:
        pack.writestr("[Content_Types].xml", types)
        pack.writestr("extension.vsixmanifest", manifest)
        pack.writestr("extension/package.json", json.dumps(package, indent=2))
        pack.writestr("extension/agent.json", json.dumps({"command": list(command)}))
        pack.write(source("vscode", "extension.js"), "extension/extension.js")
    return path


# -- Sublime Text ---------------------------------------------------------------------------
def sublime_packages(platform: str, home: Path) -> List[Path]:
    """Sublime Text's Packages folders on this computer (4, then 3)."""
    base = _app_data(platform, home)
    names = ("sublime-text", "sublime-text-3") if platform.startswith("linux") \
        else ("Sublime Text", "Sublime Text 3")
    return [base / name / "Packages" for name in names if (base / name).is_dir()]


def install_sublime(packages: Path, command: List[str], icon: Any = None,
                    words: Optional[Dict[str, str]] = None) -> Path:
    where = packages / SUBLIME_PACKAGE
    where.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source("sublime", "personal_agent.py"), where / "personal_agent.py")
    (where / "agent.json").write_text(json.dumps({"command": list(command),
                                                  "words": dict(words or {})},
                                                 ensure_ascii=False), encoding="utf-8")
    if icon is not None:
        icon.resize((44, 44)).save(where / "icon.png")    # shown at 22: sharp on a fine screen
    label = (words or {}).get("delegate") or WORDS
    (where / "Context.sublime-menu").write_text(json.dumps(
        [{"caption": "-"}, {"caption": label, "command": "personal_agent_delegate"}],
        indent=1, ensure_ascii=False), encoding="utf-8")
    (where / "Side Bar.sublime-menu").write_text(json.dumps(
        [{"caption": "-"}, {"caption": label, "command": "personal_agent_delegate_files",
                            "args": {"files": [], "dirs": []}}], indent=1, ensure_ascii=False),
        encoding="utf-8")
    return where


# -- JetBrains -------------------------------------------------------------------------------
#: An IDE's settings folder: its product's name, then its version.
JETBRAINS_FOLDER = re.compile(r"^(PyCharm|IntelliJIdea|IdeaIC|WebStorm|PhpStorm|GoLand|"
                              r"CLion|RubyMine|Rider|DataGrip|DataSpell|RustRover|"
                              r"AndroidStudio|Aqua|Fleet|PyCharmCE)\d{4}\.\d+$")


def jetbrains_folders(platform: str, home: Path) -> List[Path]:
    """Each JetBrains IDE's settings folder on this computer (Android
    Studio's is under Google)."""
    base = _app_data(platform, home)
    found: List[Path] = []
    for parent in (base / "JetBrains", base / "Google"):
        if parent.is_dir():
            found += sorted(one for one in parent.iterdir()
                            if one.is_dir() and JETBRAINS_FOLDER.match(one.name))
    return found


def jetbrains_tools(command: List[str]) -> str:
    """The tool group: one tool, on the editor's and the project view's
    menus - the file itself."""
    program, *args = list(command)
    params = " ".join(_quoted(one) for one in args) + (
        ' --delegate-file "$FilePath$" --at-app "$IDEName$"')
    return f"""<toolSet name="Personal Agent">
  <tool name={quoteattr(WORDS)} description="Hand the file, or the text you selected in it, to your agent" showInMainMenu="false" showInEditor="true" showInProject="true" showInSearchPopup="false" disabled="false" useConsole="false" showConsoleOnStdOut="false" showConsoleOnStdErr="false" synchronizeAfterRun="false">
    <exec>
      <option name="COMMAND" value={quoteattr(program)} />
      <option name="PARAMETERS" value={quoteattr(params.strip())} />
      <option name="WORKING_DIRECTORY" value="$ProjectFileDir$" />
    </exec>
  </tool>
</toolSet>
"""


def _quoted(one: str) -> str:
    return f'"{one}"' if (" " in one or not one) else one


# -- what is on this computer, and putting it in ------------------------------------------------
def programs(platform: str = sys.platform, home: Optional[Path] = None,
             which: Callable[[str], Optional[str]] = shutil.which) -> List[Dict[str, Any]]:
    """The editors found on this computer, each with whether Delegate work
    is in it."""
    home = Path(home or Path.home())
    found: List[Dict[str, Any]] = []
    for name, _command, _places in VSCODE_KIN:
        cli = _vscode_command(name, platform, which)
        if cli:
            found.append({"key": f"vscode:{name}", "name": name, "kind": "vscode",
                          "installed": _vscode_has(name, home)})
    for packages in sublime_packages(platform, home):
        found.append({"key": f"sublime:{packages}", "name": "Sublime Text", "kind": "sublime",
                      "where": str(packages),
                      "installed": (packages / SUBLIME_PACKAGE / "agent.json").exists()})
    for folder in jetbrains_folders(platform, home):
        found.append({"key": f"jetbrains:{folder}", "name": folder.name, "kind": "jetbrains",
                      "where": str(folder),
                      "installed": (folder / "tools" / JETBRAINS_TOOLS).exists()})
    return found


#: Where each keeps its extensions, under the home folder.
VSCODE_EXTENSIONS = {"Visual Studio Code": ".vscode", "Cursor": ".cursor",
                     "VSCodium": ".vscode-oss", "Windsurf": ".windsurf"}


def _vscode_has(name: str, home: Path) -> bool:
    """Whether the extension is in that editor - by its folder, not by
    asking the editor (which takes seconds)."""
    folder = home / VSCODE_EXTENSIONS.get(name, ".vscode") / "extensions"
    return folder.is_dir() and any(one.name.lower().startswith(VSCODE_ID + "-")
                                   for one in folder.iterdir())


def _quiet() -> Dict[str, Any]:
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


def install(key: str, data_dir: Path, command: List[str], version: str,
            platform: str = sys.platform, home: Optional[Path] = None,
            which: Callable[[str], Optional[str]] = shutil.which,
            run: Callable[..., Any] = subprocess.run, icon: Any = None,
            words: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Delegate work put in one editor (a key of `programs`)."""
    kind, _, rest = key.partition(":")
    home = Path(home or Path.home())
    if kind == "vscode":
        cli = _vscode_command(rest, platform, which)
        if not cli:
            return {"error": f"{rest} was not found here"}
        vsix = build_vsix(Path(data_dir) / "editor_menus", command, version)
        said = run([cli, "--install-extension", str(vsix), "--force"], capture_output=True,
                   text=True, timeout=120, **_quiet())
        if getattr(said, "returncode", 1) != 0:
            return {"error": (getattr(said, "stderr", "") or "it was not installed")[:300]}
        return {"installed": rest, "restart": f"Reload the window in {rest} to see it"}
    if kind == "sublime":
        packages = Path(rest)
        if packages not in sublime_packages(platform, home):
            return {"error": "Sublime Text was not found here"}
        install_sublime(packages, command, icon, words)
        return {"installed": "Sublime Text"}
    if kind == "jetbrains":
        folder = Path(rest)
        if folder not in jetbrains_folders(platform, home):
            return {"error": "that IDE was not found here"}
        (folder / "tools").mkdir(parents=True, exist_ok=True)
        (folder / "tools" / JETBRAINS_TOOLS).write_text(jetbrains_tools(command),
                                                        encoding="utf-8")
        return {"installed": folder.name, "restart": f"Restart {folder.name} to see it"}
    return {"error": "no such program"}


def remove(key: str, platform: str = sys.platform, home: Optional[Path] = None,
           which: Callable[[str], Optional[str]] = shutil.which,
           run: Callable[..., Any] = subprocess.run) -> Dict[str, Any]:
    kind, _, rest = key.partition(":")
    home = Path(home or Path.home())
    if kind == "vscode":
        cli = _vscode_command(rest, platform, which)
        if cli:
            run([cli, "--uninstall-extension", VSCODE_ID], capture_output=True, text=True,
                timeout=120, **_quiet())
        return {"removed": rest}
    if kind == "sublime" and Path(rest) in sublime_packages(platform, home):
        shutil.rmtree(Path(rest) / SUBLIME_PACKAGE, ignore_errors=True)
        return {"removed": "Sublime Text"}
    if kind == "jetbrains" and Path(rest) in jetbrains_folders(platform, home):
        (Path(rest) / "tools" / JETBRAINS_TOOLS).unlink(missing_ok=True)
        return {"removed": Path(rest).name}
    return {"error": "no such program"}


__all__ = ["programs", "install", "remove", "build_vsix", "vscode_package",
           "jetbrains_tools", "install_sublime", "VSCODE_ID"]
