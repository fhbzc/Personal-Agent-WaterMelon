"""Listener privacy controls (section 25).

Monitoring categories are independently switchable, allow/deny lists and
directory exclusions are honoured, and turning monitoring off suspends
collection without unloading providers.
"""

from __future__ import annotations

from fnmatch import fnmatch
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from ...common.agent_writes import was_written_by_agent
from ...common.events.models import ListenerEvent
from ...common.file_noise import churns_regardless
from ...common.titles import about_this_file
from .focus_trail import FocusTrail
from .user_opened import WhatTheUserOpened

CATEGORY_SETTINGS = {
    "browser": "listener_browser_enabled",
    "filesystem": "listener_filesystem_enabled",
    "application": "listener_application_enabled",
    "integration": "listener_integrations_enabled",
    "assistant": "listener_assistant_enabled",
    # Section 19.2. The one category that records what was *in* a window
    # rather than that it was there, which makes it the first one anybody
    # will want to switch off - so it switches off like any other.
    "screen": "listener_screen_enabled",
    "screenshot": "listener_screenshots_enabled",
}


class PrivacyFilter:
    def __init__(self, config: Any, platform: Any = None,
                 focus_trail: Any = None) -> None:
        #: Asked one question: has a person touched this machine
        #: recently. Optional, because a filter without one still
        #: filters - it simply cannot tell whether anyone is here.
        self.platform = platform
        self.config = config
        #: What was in front at a moment that has already passed. Fed from
        #: the focus events that pass through the manager; empty until one
        #: arrives, which is read as "no evidence" and never as "not them".
        self.focus_trail = focus_trail if focus_trail is not None else FocusTrail()
        #: The desktop's own record of what its user opened.
        self.user_opened = WhatTheUserOpened(platform)

    # -- category level -----------------------------------------------------
    def monitoring_enabled(self) -> bool:
        return bool(self.config.get("listener_enabled", True))

    def category_enabled(self, category: str) -> bool:
        if not self.monitoring_enabled():
            return False
        key = CATEGORY_SETTINGS.get(category)
        if key is None:
            return True
        return bool(self.config.get(key, False))

    # -- list matching ------------------------------------------------------
    @staticmethod
    def _matches(value: str, patterns: List[str]) -> bool:
        lowered = (value or "").lower()
        for pattern in patterns:
            pattern = pattern.lower()
            if pattern in lowered or fnmatch(lowered, pattern):
                return True
        return False

    def application_allowed(self, application: Optional[str]) -> bool:
        if not application:
            return True
        deny = self.config.lines("listener_application_denylist")
        if deny and self._matches(application, deny):
            return False
        allow = self.config.lines("listener_application_allowlist")
        if allow:
            return self._matches(application, allow)
        return True

    def website_allowed(self, url: Optional[str]) -> bool:
        if not url:
            return True
        host = urlparse(url).netloc or url
        deny = self.config.lines("listener_website_denylist")
        if deny and self._matches(host, deny):
            return False
        allow = self.config.lines("listener_website_allowlist")
        if allow:
            return self._matches(host, allow)
        return True

    def excluded_names(self) -> List[str]:
        return [name.lower() for name in self.config.lines("listener_excluded_names")]

    def file_name_allowed(self, path: Optional[str]) -> bool:
        """Whether this file is one that churns wherever it is.

        A directory exclusion cannot reach these: the registry hive log sits
        in the home directory itself, beside things worth watching. Measured
        on one machine it was 2,950 of the 3,499 file events in twenty-five
        minutes - 84% of everything recorded, none of it anybody's work.
        """
        return not churns_regardless(
            path, patterns=self.config.lines("listener_excluded_file_patterns"))

    def someone_is_here(self) -> bool:
        """Whether a person has touched this machine recently.

        The one signal that separates what the user did from what the machine
        did on its own. A machine left alone goes on writing files - hives,
        caches, sync clients, indexers - and recording that as the user's
        activity is not a small inaccuracy: it is most of the record.

        A platform that cannot answer says so, and silence is read as *yes*.
        Refusing to record because we cannot tell would turn an unsupported
        platform into one that quietly monitors nothing.
        """
        if not bool(self.config.get("listener_only_while_present", True)):
            return True
        platform = self.platform
        if platform is None:
            return True
        try:
            idle = platform.activity.seconds_since_input()
        except Exception:                # pragma: no cover - defensive
            return True
        if idle is None:
            return True
        try:
            window = float(self.config.get("listener_presence_seconds", 300))
        except (TypeError, ValueError):
            window = 300.0
        return idle <= window

    #: How short a directory name may be and still count as naming something.
    #: "src" and "bin" appear inside unrelated words and in half the titles on
    #: a machine; a name this short matching is not evidence.
    MEANINGFUL_NAME = 4

    def on_screen(self, path: Optional[str], showing: Optional[str],
                  application: Optional[str] = None) -> bool:
        """Whether the window in front was about this file.

        The judgement itself lives with the titles it reads (section 19, as
        amended), because the Overview asks the same question of the same
        records. A rule improved here has to improve what is shown as well,
        or the panel goes on displaying what the Listener has by then learned
        not to record.
        """
        return about_this_file(path, showing, application)

    def remember_what_was_in_front(self, event: ListenerEvent) -> None:
        """Feed the focus trail from the stream that already carries it.

        Nothing is sampled for this. The Application provider emits a focus
        event whenever the window or its title changes, and they pass through
        here on their way to storage, so the trail costs one comparison per
        event and a few thousand entries a day.

        Before the filter rather than after it, because what a file event
        needs is the window that was in front - including a window the user
        has excluded from being *recorded*, which is still evidence about who
        did something.
        """
        if str(event.event_type) != "application.focused":
            return
        data = event.data or {}
        try:
            when = event.timestamp.timestamp()
        except (AttributeError, ValueError, OSError):    # pragma: no cover
            return
        self.focus_trail.remember(event.application, data.get("window_title"), when)

    def _what_was_in_front(self, data: Dict[str, Any], when: float) -> tuple:
        """The window title and application at the moment this happened.

        An event that arrived promptly carries it already. One that did not -
        a read, reported when Windows got round to flushing the access time -
        is looked up in the trail, because what is on screen *now* is
        evidence about nothing that happened an hour ago.

        A title of ``None`` means the trail does not reach that moment: the
        agent was not running then, or had only just started. That is not the
        same as "no window was in front", and the caller must not read it as
        one.
        """
        showing = data.get("while_showing")
        if showing is not None:
            return str(showing), data.get("while_using")
        if data.get("while_using"):
            # In front, but nothing in the title bar. Known, and not about
            # any file.
            return "", data.get("while_using")
        application, title = self.focus_trail.at(when)
        return title, application

    def the_user_did_this(self, path: Optional[str], data: Dict[str, Any],
                          when: float) -> bool:
        """Whether a person did this, as opposed to something running for them.

        Being at the keyboard says *somebody* was working. It does not say
        this file was theirs, and on a working machine it usually is not: an
        assistant, a build, a test run and a language server all read and
        write while the user sits there doing something else.

        The notification itself cannot answer it - on Windows it does not
        carry a process at all - so two pieces of **positive** evidence are
        used instead, neither of which knows or cares which assistant is
        installed:

        - the desktop's own record of what the user opened, which a program
          calling ``open()`` never enters
        - the window that was in front *at the moment this happened*, and
          whether its title was about this file

        Both say "a person did this". Neither can say the opposite: a file
        opened from a command line enters no Recent list, and a moment before
        the agent started has no trail. So an absence of evidence is not read
        as evidence, and what cannot be judged is kept.
        """
        if self.user_opened.opened_by_the_user(path, when):
            return True
        showing, application = self._what_was_in_front(data, when)
        if showing is None:
            return True
        return self.on_screen(path, showing, application)

    def path_allowed(self, path: Optional[str]) -> bool:
        # A name based exclusion applies wherever it occurs: the directories
        # that make a home directory unwatchable are inside every project, not
        # at a fixed location (section 19, as amended).
        from ...common.file_noise import excluded_folders
        return not churns_regardless(
            path, names=self.excluded_names(), folders=excluded_folders(self.config))

    # -- event level --------------------------------------------------------
    def screenshot_evidence_allowed(self, event: ListenerEvent,
                                    trusted_root: Optional[Path]) -> bool:
        """Only a fresh capture vouched for by the manager's native producer.

        The root and file must stay physically inside the workspace. No
        symlink, traversal, non-image file or untrusted event gets a path
        exception. The caller supplies the root only during native emit.
        """
        if trusted_root is None or event.source != "screenshot":
            return False
        raw = (event.data or {}).get("path")
        workspace = self.config.get("workspace_root", "")
        if not isinstance(raw, str) or not raw or not workspace:
            return False
        try:
            path, root, workspace = Path(raw), Path(trusted_root), Path(workspace)
            if ".." in path.parts or ".." in root.parts or not path.is_absolute():
                return False
            relative_root = root.absolute().relative_to(workspace.absolute())
            relative_file = path.relative_to(root.absolute())
            physical_root = workspace.resolve(strict=True) / relative_root
            if root.resolve(strict=True) != physical_root:
                return False
            if path.resolve(strict=True) != physical_root / relative_file:
                return False
            return path.suffix.lower() == ".jpg" and path.is_file()
        except (OSError, ValueError, RuntimeError):
            return False

    def allows(self, event: ListenerEvent, category: str, *,
               trusted_screenshot_root: Optional[Path] = None) -> bool:
        return self.refusal(event, category, trusted_screenshot_root=trusted_screenshot_root) is None

    def refusal(self, event: ListenerEvent, category: str, *,
                trusted_screenshot_root: Optional[Path] = None) -> Optional[str]:
        """The blocking rule, without retaining content the user excluded."""
        if not self.category_enabled(category):
            return "switch_off"
        if not self.application_allowed(event.application):
            return "application_list"
        data: Dict[str, Any] = event.data or {}
        if not self.website_allowed(data.get("url")):
            return "website_list"
        picture = event.event_type == "screen.shot"
        if picture and (category != "screenshot" or not self.screenshot_evidence_allowed(
                event, trusted_screenshot_root)):
            return "untrusted_capture"
        # Section 19, as amended: a file event is the user's only if the user
        # was there. Applied to files alone - a page visited or a window
        # brought to the front is already somebody doing something.
        if str(event.event_type).startswith("filesystem."):
            if not self.someone_is_here():
                return "not_here"
            # And whose doing it was, asked where there is a path to ask about.
            if bool(self.config.get("listener_only_what_the_user_did", True)):
                where = (data.get("path") or data.get("destination_path")
                         or data.get("source_path"))
                if where:
                    try:
                        when = event.timestamp.timestamp()
                    except (AttributeError, ValueError, OSError):  # pragma: no cover
                        when = time.time()
                    if not self.the_user_did_this(where, data, when):
                        return "not_yours"
        for key in ("path", "source_path", "destination_path"):
            if key in data:
                if picture and key == "path":
                    if not self.file_name_allowed(data[key]):
                        return "ignored_name"
                    continue
                # The agent writing its own memory is not the user doing
                # something (section 19, as amended). Ownership rather than
                # location, because the workspace also holds the documents
                # and downloads the user keeps there, and those are exactly
                # the activity worth seeing.
                if was_written_by_agent(data.get(key)):
                    return "agents_own"
                if not self.path_allowed(data.get(key)):
                    return "excluded_folder"
                if not self.file_name_allowed(data.get(key)):
                    return "ignored_name"
        return None

    def describe(self) -> Dict[str, Any]:
        return {
            "monitoring_enabled": bool(self.config.get("listener_enabled", True)),
            "categories": {name: self.category_enabled(name) for name in CATEGORY_SETTINGS},
            "application_allowlist": self.config.lines("listener_application_allowlist"),
            "application_denylist": self.config.lines("listener_application_denylist"),
            "website_allowlist": self.config.lines("listener_website_allowlist"),
            "website_denylist": self.config.lines("listener_website_denylist"),
            "directory_exclusions": self.config.lines("listener_directory_exclusions"),
            # Never listened to, whatever the setting says: the agent's data
            # folder (common/file_noise.py, excluded_folders).
            "data_folder": str(self.config.get("agent_root") or ""),
            "excluded_names": self.config.lines("listener_excluded_names"),
            "only_what_the_user_did": bool(
                self.config.get("listener_only_what_the_user_did", True)),
            "focus_trail": self.focus_trail.describe(),
            "what_the_user_opened": self.user_opened.describe(),
        }
