"""DELEGATE WORK in Sublime Text (platform_layer/editor_menus.py; the user,
2026-11-01: "所有text...都是这个形式，只有直接在网址或者文件里才是右键delegate
work"):

* TEXT SELECTED: the agent's face, small, beside it for a few seconds;
  clicked, its menu - Delegate work, Learn - with the text, the file, the
  line and the lines around it.
* THE FILE: Delegate work on the right-click menu of the text and of the
  side bar - the file itself.

What was picked goes to the agent on this computer (`--delegate-picked -` /
`--learn-picked -`, on its standard input), which opens its box on it.
Nothing else is read, and nothing is sent anywhere else."""

import base64
import json
import os
import subprocess

import sublime
import sublime_plugin

AROUND_LINES, PAGE = 6, 8000
ICON_MS, MENU_MS, SETTLE_MS = 6000, 12000, 350
HERE = os.path.dirname(__file__)


def _command():
    with open(os.path.join(HERE, "agent.json"), encoding="utf-8") as f:
        return json.load(f)["command"]


def _words():
    try:
        with open(os.path.join(HERE, "agent.json"), encoding="utf-8") as f:
            said = json.load(f).get("words") or {}
    except (OSError, ValueError):
        said = {}
    return said.get("delegate") or "Delegate work", said.get("learn") or "Learn"


def _hand_over(picked, flag="--delegate-picked"):
    flags = 0
    if sublime.platform() == "windows":
        flags = 0x00000008 | 0x00000200          # detached, its own group
    try:
        child = subprocess.Popen(_command() + [flag, "-"], stdin=subprocess.PIPE,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 creationflags=flags)
        child.stdin.write(json.dumps(picked).encode("utf-8"))
        child.stdin.close()
    except OSError as exc:
        sublime.error_message("Delegate work: the agent could not be started ({})".format(exc))


def _selection(view, region):
    text = view.substr(region)
    row, last = view.rowcol(region.begin())[0], view.rowcol(region.end())[0]
    around = view.substr(sublime.Region(
        view.text_point(max(0, row - AROUND_LINES), 0),
        view.line(view.text_point(last + AROUND_LINES, 0)).end()))
    return {"where": "sublime", "app": "Sublime Text", "kind": "selection",
            "file": view.file_name() or "", "title": view.name() or "", "line": row + 1,
            "text": text, "around": around,
            "page": view.substr(sublime.Region(0, min(view.size(), PAGE)))}


def _icon():
    try:
        with open(os.path.join(HERE, "icon.png"), "rb") as f:
            return "data:image/png;base64," + base64.b64encode(f.read()).decode("ascii")
    except OSError:
        return ""


STYLE = ("<style>body{margin:0;padding:2px 4px;} a{text-decoration:none;display:block;"
         "padding:3px 6px;} img{width:22px;height:22px;}</style>")


class PersonalAgentSelectionIcon(sublime_plugin.EventListener):
    """The icon beside selected text - once the selection has settled."""

    shown = {}

    def on_selection_modified_async(self, view):
        if view.settings().get("is_widget"):
            return
        regions = view.sel()
        if len(regions) != 1 or regions[0].empty():
            return
        region = regions[0]
        sublime.set_timeout_async(lambda: self._settled(view, region), SETTLE_MS)

    def _settled(self, view, region):
        regions = view.sel()
        if len(regions) != 1 or regions[0] != region or not view.substr(region).strip():
            return
        if self.shown.get(view.id()) == (region.a, region.b):
            return
        self.shown[view.id()] = (region.a, region.b)
        picked = _selection(view, region)
        delegate, learn = _words()
        icon = _icon()
        face = '<img src="{}">'.format(icon) if icon else delegate

        def navigate(href):
            if href == "menu":
                view.update_popup(STYLE + '<a href="delegate">{}</a><a href="learn">{}</a>'
                                  .format(delegate, learn))
                sublime.set_timeout(view.hide_popup, MENU_MS)
            elif href in ("delegate", "learn"):
                view.hide_popup()
                _hand_over(picked, "--learn-picked" if href == "learn" else "--delegate-picked")
        view.show_popup(STYLE + '<a href="menu" title="{}">{}</a>'.format(delegate, face),
                        location=region.end(), on_navigate=navigate, max_width=240)
        sublime.set_timeout(lambda: view.hide_popup() if view.is_popup_visible() and
                            self.shown.get(view.id()) == (region.a, region.b) else None, ICON_MS)


class PersonalAgentDelegateCommand(sublime_plugin.TextCommand):
    """The text's right-click menu: the file itself."""

    def run(self, edit):
        if self.view.file_name():
            _hand_over({"where": "sublime", "app": "Sublime Text", "kind": "file",
                        "file": self.view.file_name()})

    def is_visible(self):
        return bool(self.view.file_name())


class PersonalAgentDelegateFilesCommand(sublime_plugin.WindowCommand):
    def run(self, files=None, dirs=None):
        for path in (files or []) + (dirs or []):
            _hand_over({"where": "sublime", "app": "Sublime Text", "file": path})

    def is_visible(self, files=None, dirs=None):
        return bool((files or []) + (dirs or []))
