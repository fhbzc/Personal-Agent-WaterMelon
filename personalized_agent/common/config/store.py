"""Persistent, observable settings store."""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..atomic import replace_atomically
from ..paths import default_data_dir  # re-exported for callers
from . import schema

log = logging.getLogger(__name__)


USER_DEFAULTS_FILENAME = "user-defaults.json"


class ConfigStore:
    """JSON backed settings with atomic writes and change notification.

    Settings are trusted application state and live in the internal data
    directory (section 109), never in the agent's writable workspace.

    There are two layers of default (section 12.1):

    * the built-in defaults declared in the schema, which are the same for
      every installation and belong to the program;
    * this user's own defaults, which are whatever they have chosen, kept
      beside their settings inside the agent root so that they travel when the
      root is copied (section 110.1).

    Resetting a setting returns it to the user's default, not to the program's:
    a preference the user has expressed once should not have to be expressed
    again.
    """

    def __init__(self, data_dir: Optional[Path] = None, bus: Any = None,
                 path: Optional[Path] = None) -> None:
        self.data_dir = Path(data_dir) if data_dir else default_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = Path(path) if path else self.data_dir / "settings.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._bus = bus
        self._lock = threading.RLock()
        self.user_defaults_path = self.path.parent / USER_DEFAULTS_FILENAME
        self._user_defaults: Dict[str, Any] = {}
        self._load_user_defaults()
        self._values: Dict[str, Any] = self.defaults()
        self._load()
        self._every_assistant_once()
        self._watermelon_once()
        self._workspace_unheard_once()

    # -- persistence --------------------------------------------------------
    def _load(self) -> None:
        if not self.path.exists():
            self._save()
            return
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.exception("settings file unreadable, falling back to defaults")
            return
        with self._lock:
            for key, value in stored.items():
                if key in self.RETIRED:
                    log.info("setting %s no longer exists and was dropped", key)
                    continue
                try:
                    self._values[key] = schema.coerce(key, value)
                except (TypeError, ValueError):
                    log.warning("ignoring invalid stored setting %s", key)
            # Section 12.2, as amended: display language was split out of the
            # one setting that used to mean both. Someone who had chosen a
            # language was choosing, among other things, to be spoken to in
            # it - and defaulting the new setting to the system's language
            # would have taken that away on upgrade, silently, in the one
            # place they would notice least until the next reply arrived.
            if "display_language" not in stored and stored.get("language"):
                try:
                    self._values["display_language"] = schema.coerce(
                        "display_language", stored["language"])
                    log.info("display language follows the language already "
                             "chosen (%s)", stored["language"])
                except (TypeError, ValueError):      # pragma: no cover
                    pass
            # A default the program has since changed. Every value is written
            # to the settings file, the defaults included, so the old default
            # is there as if it had been chosen - and would stay for ever. It
            # was chosen only if it is among the user's own defaults, which
            # is where a choice is remembered; otherwise the new default
            # applies. ONCE: the move is remembered (MOVED_MARK), so the old
            # value chosen again later, in Settings, is kept.
            moved = list(stored.get(self.MOVED_MARK) or [])
            for key, was in self.MOVED_DEFAULTS.items():
                if key in moved:
                    continue
                if (key in stored and self._values.get(key) == was
                        and key not in self._user_defaults):
                    self._values[key] = schema.SCHEMA_BY_KEY[key].default
                    log.info("%s was the old default (%s); now %s", key, was,
                             self._values[key])
                moved.append(key)
            if moved != list(stored.get(self.MOVED_MARK) or []):
                self._values[self.MOVED_MARK] = moved
                self._save()

    #: Defaults that have changed, as {key: the default it had before}.
    #:
    #: ``raw_activity_keep_days`` was 14 when raw activity was waiting to be
    #: turned into memories. Nothing does that now, and nothing reads further
    #: back than the Overview's week, so it is 8.
    #:
    #: ``state_heartbeat_every_hours`` was 6: states are grouped every 12
    #: hours.
    #: ``state_first_round_states`` was 100, which kept a new installation's
    #: Overview empty for a day or two of use; it is 20.
    MOVED_DEFAULTS = {"raw_activity_keep_days": 14, "state_heartbeat_every_hours": 6.0,
                      "state_first_round_states": 100}
    #: The settings whose moved default has been applied, kept in the file.
    MOVED_MARK = "defaults_moved"

    #: Done once, and remembered here.
    ALL_ASSISTANTS_MARK = "assistant_conversations_all"

    def _every_assistant_once(self) -> None:
        """Conversations recorded from every assistant found - ONCE.

        "Record conversations from" was empty by default, nothing recorded;
        it is every assistant now (*), as every listening is on by default.
        An empty one - the old default, even kept among the user's own
        defaults - becomes every assistant, once: emptied again later in
        Settings, it stays empty.
        """
        key = "listener_assistant_content_tools"
        with self._lock:
            if self._values.get(self.ALL_ASSISTANTS_MARK):
                return
            if not str(self._values.get(key) or "").strip():
                self._values[key] = "*"
                log.info("conversations are recorded from every assistant found")
            if key in self._user_defaults and not str(self._user_defaults[key] or "").strip():
                self._user_defaults[key] = "*"
                self._save_user_defaults()
            self._values[self.ALL_ASSISTANTS_MARK] = True
            self._save()

    #: Set once the whole workspace was left out of the file listener.
    WORKSPACE_UNHEARD_MARK = "listener_workspace_unheard"

    def _workspace_unheard_once(self) -> None:
        """The whole workspace left unheard by the file listener - ONCE: the
        line for its memory folder becomes the line for the workspace, and the
        user's own lines are kept. Taken out again later in Settings, it stays
        out."""
        key = "listener_directory_exclusions"
        with self._lock:
            if self._values.get(self.WORKSPACE_UNHEARD_MARK):
                return
            workspace = str(self._values.get("workspace_root") or "").strip()
            for held in (self._values, self._user_defaults):
                value = held.get(key)
                if not isinstance(value, str) or not workspace:
                    continue
                lines = [line for line in value.splitlines() if line.strip()]
                memory = os.path.normcase(os.path.join(workspace, "data"))
                kept = [line for line in lines
                        if os.path.normcase(line.strip().rstrip("\\/")) != memory]
                if not any(os.path.normcase(line.strip().rstrip("\\/"))
                           == os.path.normcase(workspace) for line in kept):
                    kept.insert(0, workspace)
                held[key] = "\n".join(kept)
            if self._user_defaults:
                self._save_user_defaults()
            self._values[self.WORKSPACE_UNHEARD_MARK] = True
            self._save()

    #: Set once the old default avatar gave way to Watermelon.
    WATERMELON_MARK = "avatar_watermelon"

    def _watermelon_once(self) -> None:
        """The old default avatar is gone - ONCE: one that was showing it
        shows Watermelon, and its old default name, "Aria", which nobody
        gave it, goes - so the avatar is called by its own name until the
        user names it."""
        with self._lock:
            if self._values.get(self.WATERMELON_MARK):
                return
            for held in (self._values, self._user_defaults):
                if str(held.get("character") or "") in ("", "default"):
                    held["character"] = "watermelon"
                if str(held.get("agent_name") or "") == "Aria":
                    held["agent_name"] = ""
            if self._user_defaults:
                self._save_user_defaults()
            self._values[self.WATERMELON_MARK] = True
            self._save()

    #: Settings that no longer exist, and are dropped rather than carried.
    #:
    #: ``listener_only_what_is_on_screen`` asked whether the window in front
    #: *now* named the file. It was replaced by a question that can be asked
    #: about the moment a change actually happened, and that reads the
    #: desktop's own record of what the user opened. Someone who left the old
    #: one off was declining a test that could not see either; carrying that
    #: "no" across would turn it into a refusal of something they were never
    #: offered (section 19, as amended).
    #:
    #: ``listener_website_integrations_enabled`` switched what the browser
    #: extension reported; the extension was removed, so it switches nothing.
    RETIRED = ("listener_only_what_is_on_screen",
               "listener_website_integrations_enabled",
               # Delegate work is always there: not a setting (the user, 2026-10-14).
               "delegate_in_explorer", "delegate_beside_selection")

    def _save(self) -> None:
        # THE WRITE AND THE MOVE UNDER THE LOCK, not only the reading of the
        # values: every save goes through the one .tmp file, and two threads
        # saving at once - the round and the patterns built beside it - had
        # one move the file away while the other was still writing or moving
        # it, and that save failed.
        tmp = self.path.with_suffix(".tmp")
        with self._lock:
            payload = json.dumps(self._values, indent=2, ensure_ascii=False, sort_keys=True)
            tmp.write_text(payload, encoding="utf-8")
            replace_atomically(tmp, self.path)

    # -- defaults (section 12.1) --------------------------------------------
    def _load_user_defaults(self) -> None:
        if not self.user_defaults_path.exists():
            return
        try:
            stored = json.loads(self.user_defaults_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.warning("user defaults file unreadable; using the built-in defaults")
            return
        if not isinstance(stored, dict):
            return
        for key, value in stored.items():
            if key not in schema.SCHEMA_BY_KEY:
                continue                       # a setting this version dropped
            try:
                self._user_defaults[key] = schema.coerce(key, value)
            except (TypeError, ValueError):
                log.warning("ignoring invalid user default %s", key)

    def _save_user_defaults(self) -> None:
        tmp = self.user_defaults_path.with_suffix(".tmp")
        with self._lock:
            payload = json.dumps(self._user_defaults, indent=2, ensure_ascii=False,
                                 sort_keys=True)
            # Under the lock, as `_save` is, for the same reason.
            tmp.write_text(payload, encoding="utf-8")
            replace_atomically(tmp, self.user_defaults_path)

    def builtin_defaults(self) -> Dict[str, Any]:
        """What the program ships with, identical on every installation."""
        return schema.defaults()

    def user_defaults(self) -> Dict[str, Any]:
        """What this user has chosen, which travels with the agent root."""
        with self._lock:
            return dict(self._user_defaults)

    def defaults(self) -> Dict[str, Any]:
        """The effective defaults: the user's choices over the program's."""
        merged = self.builtin_defaults()
        merged.update(self.user_defaults())
        return merged

    def default_for(self, key: str) -> Any:
        with self._lock:
            if key in self._user_defaults:
                return self._user_defaults[key]
        field = schema.SCHEMA_BY_KEY.get(key)
        return field.default if field is not None else None

    def reset(self, key: str) -> Any:
        """Return a setting to the value the program ships with.

        The user's own default is whatever they last chose, so resetting to it
        would do nothing. Resetting therefore means undoing the choice: the
        user's default for this setting is forgotten and the built-in value
        applies again.
        """
        field = schema.SCHEMA_BY_KEY.get(key)
        builtin = field.default if field is not None else None
        with self._lock:
            self._user_defaults.pop(key, None)
        self._save_user_defaults()
        # set() would record it as a new user default, so write it directly.
        coerced = schema.coerce(key, builtin)
        with self._lock:
            old = self._values.get(key)
            self._values[key] = coerced
            self._user_defaults.pop(key, None)
        self._save()
        self._save_user_defaults()
        if old != coerced and self._bus is not None:
            self._bus.publish("settings.changed",
                              {"key": key, "old": old, "new": coerced, "reset": True})
        return coerced

    def reset_all(self) -> Dict[str, Any]:
        """Forget every choice this user has made."""
        for key in list(self.user_defaults()):
            self.reset(key)
        return self.all()

    def describe_defaults(self) -> Dict[str, Any]:
        user = self.user_defaults()
        return {
            "user_defaults": user,
            "user_default_count": len(user),
            "path": str(self.user_defaults_path),
            "travels_with_root": True,
        }

    # -- access -------------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            if key in self._values:
                return self._values[key]
        field_def = schema.SCHEMA_BY_KEY.get(key)
        if field_def is not None:
            return field_def.default
        return default

    def all(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._values)

    def set(self, key: str, value: Any, internal: bool = False) -> Any:
        field = schema.SCHEMA_BY_KEY.get(key)
        if field is not None and field.readonly and not internal:
            raise ValueError(
                f"{key} is derived from the agent root and cannot be edited here; "
                "launch with --root <path> or set PERSONALIZED_AGENT_HOME"
            )
        if field is not None and field.fixed and not internal:
            # A different refusal from the one above, and it has to read
            # differently: telling somebody to relaunch with --root would be
            # advice that does not work.
            raise ValueError(
                f"{key} is fixed in this version and cannot be changed"
            )
        coerced = schema.coerce(key, value)
        with self._lock:
            old = self._values.get(key)
            self._values[key] = coerced
            # Section 12.1: a value the user chose is their default from now on.
            # Derived values are not a choice, so they are not remembered.
            remember = not (field is not None
                            and (field.readonly or field.fixed)) and not internal
            if remember and self._user_defaults.get(key) != coerced:
                self._user_defaults[key] = coerced
                changed_default = True
            else:
                changed_default = False
        self._save()
        if changed_default:
            self._save_user_defaults()
        if old != coerced and self._bus is not None:
            self._bus.publish("settings.changed", {"key": key, "old": old, "new": coerced})
        return coerced

    def update(self, values: Dict[str, Any]) -> Dict[str, Any]:
        applied: Dict[str, Any] = {}
        errors: Dict[str, str] = {}
        for key, value in values.items():
            try:
                applied[key] = self.set(key, value)
            except (TypeError, ValueError) as exc:
                errors[key] = str(exc)
        if errors:
            raise ValueError(json.dumps(errors))
        return applied

    # -- helpers ------------------------------------------------------------
    def lines(self, key: str) -> List[str]:
        """Read a multiline setting as a list of trimmed, non empty lines."""
        raw = self.get(key) or ""
        return [line.strip() for line in str(raw).splitlines() if line.strip()]

    def schema(self) -> List[Dict[str, Any]]:
        return schema.schema_as_dicts()

    @property
    def workspace_root(self) -> Path:
        return Path(str(self.get("workspace_root"))).expanduser()
