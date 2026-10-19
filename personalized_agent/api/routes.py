"""HTTP route table.

Each handler is a small adapter: it validates input and delegates to the owning
subsystem.  No business logic lives here.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Tuple

from ..build_flags import debugger_available
from ..common.reason import BY_USER
from ..common.events.models import ScheduledTask, parse_iso, utcnow
from ..common.paths import is_frozen
from ..models.base import AuthMethod
from ..protocol.authentication.service import AuthenticationError
from ..version import APP_NAME, __version__
from .errors import ApiError, Raw
from ..heartbeat.experience.store import description_of

log = logging.getLogger(__name__)


def build_routes(app: Any) -> Dict[Tuple[str, str], Callable[[Any], Any]]:
    routes: Dict[Tuple[str, str], Callable[[Any], Any]] = {}

    #: Section 14.33, as amended: a build made without the Debugger does not
    #: serve its routes. Enforced here rather than at each of the fifteen of
    #: them, so that a route added later is covered by having the prefix
    #: rather than by whoever adds it remembering.
    debugger_built_in = debugger_available()

    def route(method: str, path: str):
        def decorator(fn):
            if path.startswith("/api/debugger") and not debugger_built_in:
                return fn
            routes[(method, path)] = fn
            return fn

        return decorator

    # ------------------------------------------------------------------ core
    @route("GET", "/api/status")
    def status(_request: Any) -> Any:
        return app.status()

    @route("GET", "/api/runtime/selftest")
    def runtime_selftest(_request: Any) -> Any:
        """Verify the filesystem invariants in this installation (sections 107, 115)."""
        return app.runtime.self_test()

    @route("GET", "/api/capabilities")
    def capabilities(_request: Any) -> Any:
        return {
            "platform": app.platform.describe(),
            "runtime": app.runtime.describe(),
            "listener": app.listener.health(),
        }

    # -------------------------------------------------------------- settings
    def _primary_reasoning() -> Any:
        """The efforts the primary model takes, or None when it takes none.

        Only Codex is asked how hard to think, so for any other primary
        provider there is nothing to choose and the field is shown fixed at
        the default rather than offering levels that would be ignored.
        """
        primary = app.models.resolve("primary")
        codex = app.models.provider("codex")
        if primary.provider_id != "codex" or codex is None:
            return None
        return codex.reasoning_options(primary.model_id)

    @route("GET", "/api/settings")
    def get_settings(_request: Any) -> Any:
        fields = app.config.schema()
        if not debugger_built_in:
            # Section 14.33, as amended: a build without the Debugger shows no
            # switch for collecting what only the Debugger would show.
            fields = [field for field in fields if field["section"] != "Debugger"]
        reasoning = _primary_reasoning()
        for field in fields:
            if field["key"] == "primary_reasoning_effort":
                field["choices"] = reasoning["efforts"] if reasoning else ["default"]
                field["readonly"] = reasoning is None
        effort_notes: Dict[str, str] = {}
        if reasoning:
            effort_notes["default"] = (
                "Uses your Codex configuration, or the selected model's own "
                "default when that says nothing.")
            if reasoning.get("model_default"):
                effort_notes["default"] += (f" This model's default is "
                                            f"{reasoning['model_default']}.")
        return {
            "values": app.config.all(),
            "schema": fields,
            # Another copy of the program that already starts at sign-in:
            # this one's switch cannot be saved on while it does.
            "start_at_login_held_by": _start_held_by(),
            "choices": {
                "providers": [p.provider_id for p in app.models.providers()],
                "models": [
                    f"{p.provider_id}/{m.id}"
                    for p in app.models.providers()
                    for m in p.list_models()
                ],
                "characters": [c.get("name") for c in app.characters.list_characters()],
                # Every kind of local client, installed or not: choosing one
                # that is absent must say so, not hide the option (76.1).
                "local_clients": ["auto"] + [row["client"] for row in _local_clients()],
                # Section 18, as amended: the assistants found on this machine.
                "assistant_tools": [row["name"] for row in _assistant_tools()],
            },
            # Each option by its full name, where a code is what is stored.
            "choice_labels": {"character": {
                c.get("name"): str(c.get("display_name") or c.get("name"))
                for c in app.characters.list_characters()},
                "display_language": {
                code: _language_label(code) for code in _display_codes()}},
            "choice_notes": {"local_client": _local_client_notes(),
                             "listener_assistant_content_tools": _assistant_tool_notes(),
                             "primary_reasoning_effort": effort_notes},
        }

    def _start_held_by() -> str:
        startup = getattr(app, "autostart", None)
        try:
            return startup.holder() if startup is not None else ""
        except Exception:                    # the system's own, unreadable
            return ""

    def _language_label(code: str) -> str:
        from ..common.languages import label_of
        return label_of(code)

    def _display_codes() -> Any:
        from ..common.languages import DISPLAY_CODES
        return DISPLAY_CODES

    def _assistant_tools() -> Any:
        """The assistants this machine has, and whether each is actually here.

        Listed whether or not they are installed, like the local clients above:
        an option that is quietly missing is one the user cannot reason about,
        and "not found on this machine" is the useful answer.
        """
        from ..listener.providers.assistant import known_tools

        found = []
        for tool in known_tools():
            try:
                installed = tool.available()
            except Exception:
                installed = False
            found.append({"key": tool.key, "name": tool.display_name,
                          "installed": bool(installed),
                          "where": str(tool.sessions_root)})
        return found

    def _assistant_tool_notes() -> Any:
        return {row["name"]: ("found at " + row["where"] if row["installed"]
                              else "not found on this machine")
                for row in _assistant_tools()}

    def _local_clients() -> Any:
        provider = app.models.provider("claude_code")
        return provider.clients() if provider is not None else []

    def _local_client_notes() -> Any:
        """What to say about each choice, so it is said when it is chosen."""
        notes = {"auto": "Uses whichever Claude client is installed."}
        for row in _local_clients():
            notes[row["client"]] = f"{row['note']} {row['detail']}"
        return notes

    @route("GET", "/api/models/local-clients")
    def local_clients(_request: Any) -> Any:
        """Which Claude clients are on this machine, and which one answers."""
        provider = app.models.provider("claude_code")
        if provider is None:
            raise ApiError("no local client provider", 404)
        chosen, path, problem = provider._resolve()
        return {"clients": _local_clients(), "chosen": chosen,
                "path": path or "", "problem": problem or ""}

    @route("POST", "/api/settings/choose-directory")
    def choose_directory(request: Any) -> Any:
        """Section 12, as amended: a directory is chosen, not typed."""
        from ..common.chooser import choose_directory as open_chooser

        return open_chooser(title=str(request.get("title") or "Choose a directory"),
                            start_at=request.get("start_at"))

    @route("POST", "/api/settings/choose-application")
    def choose_application(request: Any) -> Any:
        """Section 12, as amended: an application is chosen, not typed."""
        from ..common.chooser import choose_application as open_chooser

        return open_chooser(title=str(request.get("title") or "Choose an application"))

    @route("POST", "/api/settings/check-directory")
    def check_directory(request: Any) -> Any:
        """A typed path is validated the same way a chosen one is."""
        from ..common.chooser import describe_directory

        path = str(request.require("path"))
        return {"path": path, **describe_directory(path)}

    @route("GET", "/api/agent-state")
    def agent_state(_request: Any) -> Any:
        """Section 7.5: what the agent is doing and what it is about to do.

        Read only, from state the application already holds: pointing at the
        agent must never cause work.
        """
        return app.agent_state()

    @route("POST", "/api/models/reauthentication/dismiss")
    def dismiss_reauthentication(_request: Any) -> Any:
        """Section 75.3: the prompt can be put away once it has been read."""
        return app.dismiss_reauthentication()

    @route("GET", "/api/privacy")
    def privacy_state(_request: Any) -> Any:
        """Section 25, as amended: the state must be visible without digging."""
        return {"monitoring_enabled": bool(app.config.get("listener_enabled"))}

    @route("GET", "/api/shortcuts")
    def shortcuts(request: Any) -> Any:
        """SETTINGS > SHORTCUTS: each shortcut, its keys, whether they are
        held - and the button beside selected text."""
        from ..platform_layer import hotkeys
        held = getattr(app, "shortcuts", None)
        said = held.describe() if held is not None else hotkeys.describe_without(app.config)
        said["selection_button"] = bool(app.config.get("delegate_selection_button", True))
        return said

    @route("POST", "/api/shortcuts")
    def shortcuts_change(request: Any) -> Any:
        """New keys for a shortcut ("" for none), held at once - refused,
        and the old ones kept, when they cannot be."""
        from ..platform_layer import hotkeys
        if request.get("selection_button") is not None:
            app.config.set("delegate_selection_button", bool(request.get("selection_button")))
        key = str(request.get("key") or "")
        if key:
            combo = str(request.get("combo") or "")
            held = getattr(app, "shortcuts", None)
            try:
                if held is not None:
                    held.change(key, combo)
                else:
                    if key not in hotkeys.BY_KEY:
                        raise hotkeys.ShortcutError("no such shortcut")
                    app.config.set(hotkeys.BY_KEY[key]["setting"],
                                   hotkeys.parse(combo)[0] if combo else "")
            except hotkeys.ShortcutError as exc:
                raise ApiError(str(exc), 409)
        return shortcuts(request)

    @route("GET", "/api/record_log")
    def record_log(request: Any) -> Any:
        """THE RECORD LOG: the latest things recorded, from every source,
        each with the Privacy switch that recorded it and where a password
        or a code was hidden in it; and what the filters kept out since the
        agent started, by which rule - never what was in it."""
        from ..listener.core import filtered
        try:
            limit = max(1, min(200, int(request.get("limit") or 100)))
        except (TypeError, ValueError):
            limit = 100
        events = app.raw_store.recent_events(limit=limit)
        # WHERE SOMETHING WAS HIDDEN: the record as it was kept, with the
        # fields that hold "[hidden]" - never what was there before, which
        # was never stored.
        changed = []
        for one in app.raw_store.recent_with_hidden(50):
            shown = _recorded(one)
            shown["fields"] = _hidden_fields(one.to_dict())
            shown.pop("detail", None)
            changed.append(shown)
        return {"events": [_recorded(one) for one in events], "changed": changed,
                "dropped": filtered.recent(100), "dropped_counts": filtered.counts(),
                "rules": filtered.WHY, "since": filtered.STARTED}

    #: Which Privacy switch records each kind of source.
    RECORDED_BY = {"application": "listener_application_enabled",
                   "screen": "listener_screen_enabled",
                   "screenshot": "listener_screenshots_enabled",
                   "browser": "listener_browser_enabled",
                   "filesystem": "listener_filesystem_enabled",
                   "assistant": "listener_assistant_enabled",
                   "integration": "listener_integrations_enabled"}

    def _hidden_fields(value: Any, path: str = "") -> List[Dict[str, str]]:
        """Each field of a kept record that holds "[hidden]", by its path
        ("data.title"), with what it says now."""
        out: List[Dict[str, str]] = []
        if isinstance(value, dict):
            for key, inner in value.items():
                out += _hidden_fields(inner, f"{path}.{key}" if path else str(key))
        elif isinstance(value, list):
            for at, inner in enumerate(value):
                out += _hidden_fields(inner, f"{path}[{at}]")
        elif isinstance(value, str) and "[hidden]" in value:
            # Around the first one, where the field is long.
            first = value.index("[hidden]")
            start = max(0, first - 200)
            text = ("..." if start else "") + value[start:start + 600] + (
                "..." if len(value) > start + 600 else "")
            out.append({"field": path, "text": text})
        return out[:10]

    def _recorded(event: Any) -> Dict[str, Any]:
        """One recorded event as the Record log shows it: a title, a line
        of what it holds, which switch recorded it, and the whole of it."""
        from ..common.config.schema import SCHEMA_BY_KEY
        providers = getattr(getattr(app, "listener", None), "_providers", {}) or {}
        provider = providers.get(event.source)
        category = getattr(provider, "category", "") or (
            "integration" if str(event.source).startswith("integration") else str(event.source))
        switch = RECORDED_BY.get(category)
        data = event.data if isinstance(event.data, dict) else {}

        def first(*keys: str) -> str:
            for key in keys:
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            return ""
        title = first("title", "window_title", "url", "path", "destination_path", "project",
                      "name") or str(event.event_type)
        if event.application and event.application.lower() not in title.lower():
            title = f"{event.application}: {title}"
        about = first("text", "content", "summary", "url", "path", "source_path")
        whole = json.dumps(event.to_dict(), ensure_ascii=False, default=str)
        return {"id": event.id, "at": event.to_dict().get("timestamp"),
                "source": event.source, "category": category, "type": event.event_type,
                "by_agent": str(event.source) == "agent" or data.get("by") == "agent",
                "switch": ({"key": switch, "label": SCHEMA_BY_KEY[switch].label}
                           if switch in SCHEMA_BY_KEY else None),
                "title": title[:200], "about": about[:300],
                "hidden": whole.count("[hidden]"),
                "detail": event.to_dict()}

    @route("GET", "/api/updates")
    def updates(_request: Any) -> Any:
        """Which version this is, the latest release, and what is being done."""
        return app.updater.status()

    @route("POST", "/api/updates/check")
    def updates_check(_request: Any) -> Any:
        """Looked for at once (on the updater's own thread)."""
        return app.updater.check_now()

    @route("POST", "/api/updates/seen")
    def updates_seen(_request: Any) -> Any:
        """The window of what is new in this version was closed."""
        return app.updater.seen()

    @route("POST", "/api/updates/install")
    def updates_install(request: Any) -> Any:
        """The user's "Install this version" - one of the newer ones listed."""
        version = str(request.get("version") or "")
        listed = {one.get("tag") for one in app.updater.status().get("versions") or []}
        if version and version not in listed:
            raise ApiError("that version is not one of the newer releases", 404)
        return app.updater.install_now(version)

    @route("GET", "/api/permissions")
    def system_permissions(_request: Any) -> Any:
        """What the operating system must allow, beside each switch that
        needs it - on every platform (platform_layer/permissions.py)."""
        from ..platform_layer import permissions
        return permissions.status(app.config)

    @route("POST", "/api/permissions/open")
    def system_permission_open(request: Any) -> Any:
        """The place in the system's settings where one is switched on,
        opened on the user's click; then how it stands."""
        from ..platform_layer import permissions
        key = str(request.require("key"))
        if key not in permissions.catalog():
            raise ApiError("no such permission here", 404)
        return {"opened": permissions.open_place(key), **permissions.status(app.config)}

    @route("GET", "/api/index")
    def local_index_status(_request: Any) -> Any:
        """THE LOCAL INDEX: the folders it reads - every Work project's and
        the user's own - how far each is, and whether a build is going."""
        return app.local_index.status()

    @route("POST", "/api/index")
    def local_index_change(request: Any) -> Any:
        """A folder added or removed, the conversation records added or
        not, the index on or off, or a build started now."""
        import os
        from pathlib import Path
        action = str(request.require("action"))
        index = app.local_index
        if action in ("add", "remove"):
            path = str(request.require("path")).strip()
            folders = index.added()
            if action == "add":
                where = Path(path).expanduser()
                if not where.is_dir():
                    raise ApiError(f"not a folder: {path}")
                data = os.path.normcase(os.path.abspath(str(app.paths.data_dir)))
                if os.path.normcase(os.path.abspath(str(where))).startswith(data):
                    raise ApiError("the application's own data is never read")
                if str(where) not in folders:
                    folders.append(str(where))
            else:
                folders = [one for one in folders if one != path]
            app.config.set("index_folders", folders)
            index.soon()
        elif action == "sessions":
            app.config.set("index_sessions", bool(request.get("on", True)))
            index.soon()
        elif action == "enable":
            app.config.set("index_enabled", bool(request.get("on", True)))
            if app.config.get("index_enabled", True):
                index.soon()
        elif action == "build":
            said = index.build_now()
            if said.get("error"):
                raise ApiError(said["error"])
        else:
            raise ApiError("action is add, remove, sessions, enable or build")
        return index.status()

    @route("GET", "/api/permissions/macos")
    def macos_permissions(_request: Any) -> Any:
        """What macOS allows the agent, of what its switches need - looked
        at, never asked (platform_layer/macos_permissions.py)."""
        from ..platform_layer import macos_permissions
        return macos_permissions.status(app.config)

    @route("POST", "/api/permissions/macos/open")
    def macos_permission_open(request: Any) -> Any:
        """The user's click: ask macOS where it lets a program ask, and open
        the pane of System Settings where it is switched on."""
        from ..platform_layer import macos_permissions
        key = request.require("key")
        if key not in macos_permissions.PANES:
            raise ApiError("there is no such permission")
        opened = macos_permissions.open_pane(str(key))
        return {"opened": opened, **macos_permissions.status(app.config)}

    @route("POST", "/api/permissions/macos/start_again")
    def macos_start_again(_request: Any) -> Any:
        """The user's click: quit and open again, so Screen Recording
        switched on meanwhile applies (platform_layer/updater.py)."""
        updater = getattr(app, "updater", None)
        if updater is None or not updater.start_again():
            raise ApiError("the agent cannot open itself again here - quit it and open it again")
        return {"starting_again": True}

    @route("GET", "/api/settings/defaults")
    def settings_defaults(_request: Any) -> Any:
        """Section 12.1: what this user has chosen, and what the program ships."""
        return {
            "builtin": app.config.builtin_defaults(),
            **app.config.describe_defaults(),
        }

    @route("POST", "/api/settings/reset")
    def settings_reset(request: Any) -> Any:
        """Undo a choice: forget the user default and restore the built-in."""
        key = request.get("key")
        if key:
            return {"reset": {key: app.config.reset(str(key))},
                    "values": app.config.all()}
        app.config.reset_all()
        return {"reset": "all", "values": app.config.all()}

    @route("GET", "/api/settings/schema")
    def settings_schema(_request: Any) -> Any:
        return {"schema": app.config.schema()}

    @route("POST", "/api/settings")
    def update_settings(request: Any) -> Any:
        values = request.body.get("values") if "values" in request.body else request.body
        if not isinstance(values, dict) or not values:
            raise ApiError("no settings supplied")
        effort = values.get("primary_reasoning_effort", "default")
        if effort != "default":
            # Checked against the model this edit leaves selected - which may
            # be changing in the same edit - and before anything is written,
            # so a refused effort does not leave half an edit saved.
            provider_id = str(values.get("primary_provider",
                                         app.config.get("primary_provider")))
            model_id = str(values.get("primary_model", app.config.get("primary_model")))
            if "/" in model_id:
                provider_id, model_id = model_id.split("/", 1)
            codex = app.models.provider(provider_id)
            if provider_id != "codex" or codex is None:
                raise ApiError("A reasoning effort can only be chosen when the "
                               "primary provider is Codex")
            if effort not in codex.reasoning_options(model_id)["efforts"]:
                raise ApiError(f"The selected Codex model does not take the "
                               f"reasoning effort {effort!r}")
        if values.get("start_at_login") is True and _start_held_by():
            # Before anything is written: the entry is another copy's.
            from ..platform_layer.autostart import HELD_BY_ANOTHER
            from ..version import APP_NAME
            raise ApiError(HELD_BY_ANOTHER.format(app=APP_NAME, holder=_start_held_by()),
                           status=409)
        applied = app.config.update(values)
        # A model belongs to a provider. Changing one without the other leaves
        # the agent asking a provider for a model it has never heard of, which
        # surfaces as a bare 404 rather than as the setting it really is.
        corrected = app.models.reconcile_primary()
        if corrected:
            applied.update({key: corrected[key] for key in ("primary_provider", "primary_model")
                            if key in corrected})
        # And an effort belongs to a model. One the newly chosen model does
        # not take - or any, when the new provider takes none - goes back to
        # the default, and says so, rather than failing every call later.
        if "primary_model" in values or "primary_provider" in values:
            current = str(app.config.get("primary_reasoning_effort") or "default")
            reasoning = _primary_reasoning()
            if current != "default" and (reasoning is None
                                         or current not in reasoning["efforts"]):
                app.config.set("primary_reasoning_effort", "default")
                corrected = dict(corrected or {})
                corrected["primary_reasoning_effort"] = "default"
                applied["primary_reasoning_effort"] = "default"
        app.models.invalidate()
        # Starting at sign-in: the system's entry made so at once.
        if "start_at_login" in values and getattr(app, "autostart", None) is not None:
            try:
                app.autostart.follow(bool(app.config.get("start_at_login")))
            except Exception as exc:
                raise ApiError(f"starting at sign-in could not be set: {exc}") from exc
        # Another avatar, or another name: the desktop one, its window's icon
        # and the tray's, changed at once.
        if "character" in values or "agent_name" in values:
            app.bus.publish("character.changed",
                            {"name": str(app.config.get("character", ""))})
        # Explorer's "Delegate work": on or off, its icon the avatar's face,
        # its words the display language's.
        if any(key in values for key in ("character", "display_language")):
            app.follow_explorer_menu()
        return {"applied": applied, "values": app.config.all(),
                "corrected": corrected}

    # ---------------------------------------------------------------- models
    @route("GET", "/api/models")
    def models(_request: Any) -> Any:
        return app.models.describe()

    @route("POST", "/api/models/codex/refresh")
    def refresh_codex_models(_request: Any) -> Any:
        """Ask the installed Codex for its model list, now.

        The one place a Codex process is started for its catalog: Settings
        reads only the client's cache. Nothing chosen is changed - the list
        of what can be chosen is.
        """
        provider = app.models.provider("codex")
        if provider is None:
            raise ApiError("Codex is not a provider in this build", 404)
        try:
            models = provider.refresh_models()
        except (OSError, RuntimeError) as exc:
            raise ApiError(str(exc), 502) from exc
        app.models.invalidate()
        return {"provider": "codex", "models": [model.to_dict() for model in models]}

    # ------------------------------------------------------------ web search
    @route("GET", "/api/search")
    def web_search_status(_request: Any) -> Any:
        """Which web search the agents use, and whether its key is set."""
        from ..agent.work.tools import search_status
        return {**search_status(), "with": app.config.get("web_search_with", "model")}

    @route("POST", "/api/search/with")
    def web_search_with(request: Any) -> Any:
        """The model's own web search ("model") or the app's ("app")."""
        from ..agent.work.tools import search_status
        chosen = str(request.require("with"))
        if chosen not in ("model", "app"):
            raise ApiError("model or app", 400)
        app.config.set("web_search_with", chosen)
        return {**search_status(), "with": chosen}

    @route("POST", "/api/search/key")
    def web_search_key(request: Any) -> Any:
        """A Brave Search key: tried with one search first, and kept only if
        Brave accepts it - in the credential store, never shown again."""
        from ..agent.work.tools import BRAVE_KEY, search, search_status
        key = str(request.require("api_key")).strip()
        if not key:
            raise ApiError("give the key", 400)
        tried = search("weather", limit=1, key=key)
        if tried.get("unauthorized"):
            raise ApiError("Brave did not accept this key", 400)
        app.credentials.set(BRAVE_KEY, key)
        return {**search_status(), "tried": tried.get("error") or "it answered"}

    @route("POST", "/api/search/key/remove")
    def web_search_key_remove(_request: Any) -> Any:
        from ..agent.work.tools import BRAVE_KEY, search_status
        app.credentials.delete(BRAVE_KEY)
        return search_status()

    @route("GET", "/api/modules")
    def modules_list(request: Any) -> Any:
        """Each module that runs by itself: on or off, what it does, what
        turning it off means - and what it spent in the last `days` days."""
        from ..common.modules import described, shares
        hours = max(1, min(24 * 30, request.int_param("hours", 72)))
        return dict(described(app.config, shares(app.db, hours)), hours=hours)

    @route("GET", "/api/pending")
    def pending_list(_request: Any) -> Any:
        """What is waiting for a time, and what was lately resolved or
        dropped - and what the rules that run at once lately did."""
        return {"open": app.pending.open(), "recent": app.pending.recent(20),
                "triggers": app.triggers.history[:20]}

    @route("POST", "/api/pending")
    def pending_change(request: Any) -> Any:
        """The user's say on one: drop it, reword it - or add one."""
        action = str(request.require("action"))
        if action == "add":
            app.pending.add(str(request.require("title")), str(request.get("detail") or ""),
                            by="user")
        elif action == "drop":
            if not app.pending.close(str(request.require("id")), "dropped",
                                     str(request.get("why") or "taken off by the user")):
                raise ApiError("no such open one", 404)
        elif action == "edit":
            if not app.pending.edit(str(request.require("id")), str(request.get("title") or ""),
                                    request.get("detail")):
                raise ApiError("no such one", 404)
        else:
            raise ApiError("action is add, drop or edit", 400)
        return {"open": app.pending.open(), "recent": app.pending.recent(20)}

    @route("GET", "/api/ocr")
    def ocr_status(_request: Any) -> Any:
        """Which of the five languages the words in a picture are read in
        here, on this computer - and which can be added."""
        from ..platform_layer.ocr import current
        return current().status()

    @route("POST", "/api/ocr/add")
    def ocr_add(request: Any) -> Any:
        """Add languages to this computer's OCR - asked by the user, with the
        button: on Windows, after the administrator's yes Windows asks for."""
        from ..platform_layer.ocr import LANGUAGES, current
        known = {code for code, _name in LANGUAGES}
        codes = [str(one) for one in request.get("codes") or [] if str(one) in known]
        if not codes:
            raise ApiError("no language to add", 400)
        ocr = current()
        return {**ocr.add(codes), **ocr.status()}

    @route("POST", "/api/models/sees_images")
    def models_sees_images(request: Any) -> Any:
        """Whether a model is given pictures, as the user says - over what
        its name suggests (models/vision.py)."""
        provider = str(request.require("provider"))
        model = str(request.require("model"))
        said = dict(app.config.get("model_sees_images", {}) or {})
        said[f"{provider}/{model}"] = bool(request.require("sees"))
        app.config.set("model_sees_images", said, internal=True)
        return app.models.describe()

    @route("POST", "/api/models/chain")
    def models_chain(request: Any) -> Any:
        """THE MODELS, IN ORDER, as the user arranged them: [{provider, model}]
        - each call goes to the first that works."""
        chain = request.get("chain")
        # EMPTY IS ALLOWED: the user may remove every model, and then nothing
        # that needs one runs until one is added.
        if not isinstance(chain, list):
            raise ApiError("the list of models is missing", 400)
        try:
            app.models.set_chain(chain)
        except ValueError as exc:
            raise ApiError(str(exc), 400)
        return app.models.describe()

    @route("POST", "/api/models/check")
    def check_model(request: Any) -> Any:
        role = request.get("role", "primary")
        return app.models.check_availability(role, force=True).to_dict()

    @route("POST", "/api/models/authenticate")
    def authenticate_model(request: Any) -> Any:
        provider_id = request.require("provider")
        provider = app.models.provider(provider_id)
        if provider is None:
            raise ApiError(f"unknown provider {provider_id}", 404)
        method = request.get("method", AuthMethod.API_KEY.value)
        config = {"method": method}
        if method == AuthMethod.API_KEY.value:
            config["api_key"] = request.require("api_key")
        config["model"] = request.get("model")
        if request.get("base_url") is not None:
            # A service at an address of the user's (an OpenAI-compatible API).
            config["base_url"] = str(request.get("base_url"))
        result = provider.authenticate(config)
        app.models.invalidate()
        # Authenticating with a provider is a way of choosing one, so the model
        # has to follow it here too (section 82, as amended).
        corrected = app.models.reconcile_primary()
        # The response deliberately carries state only, never credentials.
        return {"provider": provider_id, "auth_status": result.to_dict(),
                "corrected": corrected}

    # -- browser based account sign-in (sections 75.1, 75.2) ----------------
    def _login_provider(request: Any):
        provider = app.models.provider(request.require("provider"))
        if provider is None:
            raise ApiError("unknown provider", 404)
        if not hasattr(provider, "start_account_login"):
            raise ApiError("this provider has no account sign-in", 400)
        return provider

    @route("POST", "/api/models/login/start")
    def start_account_login(request: Any) -> Any:
        provider = _login_provider(request)
        result = provider.start_account_login(
            open_browser=bool(request.get("open_browser", True)))
        return {"provider": provider.provider_id, "login": result}

    @route("POST", "/api/models/login/complete")
    def complete_account_login(request: Any) -> Any:
        provider = _login_provider(request)
        result = provider.submit_account_login_code(request.require("code"))
        app.models.invalidate()
        return {"provider": provider.provider_id, "login": result,
                "auth_status": provider.get_auth_status().to_dict()}

    @route("POST", "/api/models/login/cancel")
    def cancel_account_login(request: Any) -> Any:
        provider = _login_provider(request)
        return {"provider": provider.provider_id, "login": provider.cancel_account_login()}

    @route("GET", "/api/models/login/tools")
    def account_login_tools(request: Any) -> Any:
        provider = app.models.provider(request.get("provider", "anthropic"))
        if provider is None or not hasattr(provider, "account_login_tools"):
            return {"tools": []}
        return {"provider": provider.provider_id, "tools": provider.account_login_tools()}

    @route("GET", "/api/models/login/status")
    def account_login_status(request: Any) -> Any:
        provider = app.models.provider(request.get("provider", "anthropic"))
        if provider is None or not hasattr(provider, "account_login_status"):
            return {"login": {"state": "idle", "detail": "", "authorization_url": None,
                              "awaiting_code": False, "finished": True}}
        return {"provider": provider.provider_id, "login": provider.account_login_status()}

    @route("POST", "/api/models/logout")
    def logout_model(request: Any) -> Any:
        provider = app.models.provider(request.require("provider"))
        if provider is None:
            raise ApiError("unknown provider", 404)
        result = provider.logout()
        app.models.invalidate()
        return {"auth_status": result.to_dict()}

    # -------------------------------------------------------------- overview
    @route("GET", "/api/overview")
    def overview(request: Any) -> Any:
        return app.overview.summary(hours=request.int_param("hours", 24))

    @route("GET", "/api/overview/history")
    def overview_history(request: Any) -> Any:
        return {"days": app.overview.history(days=request.int_param("days", 7))}

    @route("GET", "/api/overview/hourly")
    def overview_hourly(request: Any) -> Any:
        return app.overview.hourly(hours=request.int_param("hours", 24))

    @route("GET", "/api/overview/pages")
    def overview_pages(request: Any) -> Any:
        return app.overview.pages(hours=request.int_param("hours", 24),
                                  limit=request.int_param("limit", 40))

    @route("GET", "/api/overview/files")
    def overview_files(request: Any) -> Any:
        return app.overview.files(hours=request.int_param("hours", 24),
                                  limit=request.int_param("limit", 40))

    @route("GET", "/api/overview/assistants")
    def overview_assistants(request: Any) -> Any:
        return app.overview.assistants(hours=request.int_param("hours", 24))

    @route("GET", "/api/overview/file-activity")
    def overview_file_activity(request: Any) -> Any:
        """Section 19, as amended: what was read, and what was written."""
        return app.overview.file_activity(
            hours=request.int_param("hours", 24),
            limit=request.int_param("limit", 60),
        )

    @route("GET", "/api/overview/application")
    def overview_application(request: Any) -> Any:
        return app.overview.application_detail(
            str(request.require("name")), limit=request.int_param("limit", 100),
            # Section 9, as amended: one site inside an application is a
            # narrower version of the same question.
            site=request.get("site") or None,
            # The same window the rest of the tab is showing, so that
            # selecting an application does not quietly answer about a
            # different stretch of time from the list it was selected in.
            hours=request.int_param("hours", 24),
        )

    @route("GET", "/api/overview/page")
    def overview_page(request: Any) -> Any:
        """Section 9, as amended: a page can be asked about in its own right."""
        return app.overview.page_detail(str(request.require("url")))

    @route("GET", "/api/agent/progress")
    def agent_progress(request: Any) -> Any:
        """Section 15.1: what the agent is doing while someone waits on it.

        Restricted to the asking origin so that a person waiting on their own
        question is not shown a background heartbeat that happened to start
        while they waited.
        """
        observability = getattr(app, "observability", None)
        if observability is None or not hasattr(observability, "active_progress"):
            return {"active": False, "steps": []}
        progress = observability.active_progress(kind=request.get("origin", "user"))
        if not progress:
            return {"active": False, "steps": []}
        return {"active": True, "task_id": progress.get("task_id"),
                "origin": progress.get("kind"), "summary": progress.get("summary"),
                "started_at": progress.get("started_at"),
                "steps": progress.get("steps", [])}

    # ------------------------------------------------------------- states
    @route("GET", "/api/debugger/states")
    def states(request: Any) -> Any:
        """Section 34.1, as amended: states are internal, and shown here only.

        A state is what the agent believes is true over a stretch of time, and
        it is half of the context behind every answer. That makes it worth
        being able to look at - and it does not make it something to put on
        the user's calendar beside the things they scheduled.

        Two reasons it moved off the calendar. Every task now writes an
        accompanying state, so the calendar was showing the same thing twice,
        once as a task and once as a span saying the task was going to happen.
        And the rest of the states are the agent's inferences about the user -
        "quiet desk since 11:53" - which is material for the Debugger, not
        entries in a diary the user keeps.

        This route lives under the Debugger, so a build made without it
        (``build.py --no-debugger``) does not answer here at all.
        """
        from datetime import datetime, timedelta, timezone

        days = max(1, min(120, request.int_param("days", 35)))
        centre = parse_iso(str(request.get("around") or "")) or datetime.now(timezone.utc)
        start, end = centre - timedelta(days=days), centre + timedelta(days=days)
        return {"states": [s.to_dict() for s in app.states.between(start, end)],
                "covering_now": [s.to_dict() for s in app.states.covering()],
                "counts": app.states.counts()}

    # --------------------------------------------------------------- library
    # A record, by its id - what the Debugger's trail opens. There is no
    # listing to browse: the page has no memory tab, and nothing searches
    # the library as a whole.
    @route("GET", "/api/library/record")
    def library_record(request: Any) -> Any:
        """One record, by its id - read from its folder when that is given."""
        record = app.library.get(str(request.require("id")),
                                 folder=str(request.get("folder") or "") or None)
        if record is None:
            raise ApiError("record not found", 404)
        return record.to_dict()

    @route("GET", "/api/library/stats")
    def library_stats(_request: Any) -> Any:
        return app.library.stats()

    # ------------------------------------------------------------- scheduler
    @route("GET", "/api/tasks")
    def list_tasks(request: Any) -> Any:
        start = parse_iso(request.get("start"))
        end = parse_iso(request.get("end"))
        # A repeating task is stored once and drawn many times, so the window
        # has to be searched from before it: a task first scheduled in March
        # that repeats daily still has an occurrence today. Section 52.3.
        tasks = app.scheduler.store.list(status=request.get("status"), start=start, end=end,
                                         limit=request.int_param("limit", 300))
        if not request.get("expand"):
            return {"tasks": [t.to_dict() for t in tasks],
                    "status": app.scheduler.status()}
        repeating = [t for t in app.scheduler.store.list(limit=500)
                     if t.recurrence and t.status in ("pending", "running")]
        known = {t.id for t in tasks}
        drawn = [t.to_dict() for t in tasks if not t.recurrence]
        for task in repeating:
            drawn.extend(_occurrences_of(task, start, end,
                                         include_first=task.id in known))
        return {"tasks": drawn, "status": app.scheduler.status()}

    def _occurrences_of(task: Any, start: Any, end: Any,
                        include_first: bool) -> List[Dict[str, Any]]:
        """Draw one repeating task as the blocks it will actually run in.

        Nothing here is stored. Each block carries the series' own fields, its
        own time, and enough identification to get back to the one row that
        owns it - so acting on a block acts on the series.
        """
        from ..scheduler.core.recurrence import occurrences
        from ..scheduler.core import series

        blocks: List[Dict[str, Any]] = []
        # Occurrences are computed from the series anchor rather than from
        # scheduled_at, so that moving one block does not drag the rest of the
        # grid after it. They agree until something is overridden.
        anchor = series.anchor_of(task)
        for index, moment in occurrences(task.recurrence, anchor, 0, None, None):
            if index < task.runs:
                continue                      # already happened
            override = series.override_for(task, index)
            if override.get("skipped"):
                continue                      # the user deleted this one
            moment = parse_iso(override.get("scheduled_at")) or moment
            if not _within_window(moment, start, end):
                continue
            block = task.to_dict()
            # When the series runs next, and what it is called, whichever
            # block this is: editing a series edits the live row, and the
            # card's form has to show it rather than the block opened.
            block["series_at"] = block["scheduled_at"]
            block["series_title"] = task.title
            block["scheduled_at"] = moment.isoformat()
            block["occurrence_index"] = index
            block["occurrence_of"] = task.id
            series.apply_override(block, override)
            if index != task.runs:
                # Every block after the next one is a drawing of a run that
                # has not been reached; only the first is the live row.
                block["id"] = f"{task.id}#{index}"
                block["status"] = "scheduled"
            elif not include_first:
                continue
            blocks.append(block)
        return blocks

    def _within_window(moment: Any, start: Any, end: Any) -> bool:
        if start is not None and moment < start:
            return False
        if end is not None and moment > end:
            return False
        return True

    def _creation_payload(request: Any) -> Dict[str, Any]:
        """Section 52.2: a task the user makes themselves also states the choice.

        Silence is the right resting state for what the agent schedules for
        itself, but a reminder the user sat down and created is usually one
        they want to be reminded by. So the form's own default follows what
        they asked for - a notification notifies - and an explicit choice in
        the request always wins.
        """
        payload = dict(request.get("payload") or {})
        if "notify" in request.body:
            payload["notify_user"] = bool(request.body["notify"])
        elif "notify_user" not in payload:
            payload["notify_user"] = (
                str(request.get("execution_type", "notification")) == "notification")
        return payload

    def _creation_recurrence(request: Any) -> Dict[str, Any]:
        from ..scheduler.core.recurrence import RecurrenceError, parse_recurrence

        try:
            return parse_recurrence(request.get("recurrence"))
        except RecurrenceError as exc:
            raise ApiError(str(exc)) from exc

    @route("POST", "/api/tasks")
    def create_task(request: Any) -> Any:
        scheduled_at = parse_iso(request.require("scheduled_at"))
        if scheduled_at is None:
            raise ApiError("scheduled_at must be an ISO-8601 timestamp")
        task = ScheduledTask(
            title=str(request.require("title")),
            description=str(request.get("description", "")),
            scheduled_at=scheduled_at,
            execution_type=_only_notifications(request.get("execution_type")),
            script_path=request.get("script_path"),
            task_payload=_creation_payload(request),
            recurrence=_creation_recurrence(request),
            timezone_name=str(request.get("timezone_name", "UTC")),
            metadata={"created_by": "calendar_ui"},
            # Section 52.7: the user put it there, and that is the whole
            # reason. Asking them to justify their own calendar entry would be
            # the system demanding an explanation from the one participant who
            # does not owe it one - so anything they wrote is kept, and
            # nothing becomes "added by user".
            created_by=BY_USER,
            reason=str(request.get("reason", "")),
        )
        created = app.scheduler.create_task(task)
        app.calendar_nature.record("add", created,
                                   said=str(request.get("reason", "") or ""))
        # Section 60, as amended: the event exists now, holding what the user
        # typed. What they left blank is filled in behind them - waiting on a
        # model call before the event appeared would make the calendar feel
        # broken, and would lose the event entirely if the model were away.
        if app.task_completer is not None:
            created = app.task_completer.request(created) or created
        return created.to_dict()

    def _only_notifications(asked: Any) -> str:
        """Only a notification can be put on the calendar."""
        kind = str(asked or "notification")
        if kind != "notification":
            raise ApiError("only notifications can be scheduled - nothing on "
                           "the calendar executes anything")
        return kind

    def _series_id(raw: Any) -> str:
        """A drawn occurrence points back at the row that owns it."""
        from ..scheduler.core.series import parse_block_id
        return parse_block_id(raw)[0]

    def _scope_of(request: Any, task_id: str, index: Any) -> str:
        """"This event" or "all events", as every calendar asks.

        Defaulting matters more than it looks. A one-off task has only one
        meaning, so asking would be noise. A repeating one defaults to *this
        occurrence*, because that is the smaller claim: a user who meant the
        series and got one run has lost a click, and one who meant one run and
        got the series has lost the series.
        """
        from ..scheduler.core.series import SCOPES, THIS_OCCURRENCE, WHOLE_SERIES
        asked = str(request.get("scope") or "").strip().lower()
        if asked in SCOPES:
            return asked
        task = app.scheduler.store.get(task_id)
        if task is None or not task.recurrence:
            return WHOLE_SERIES
        return THIS_OCCURRENCE if index is not None else WHOLE_SERIES

    @route("POST", "/api/tasks/update")
    def update_task(request: Any) -> Any:
        from ..scheduler.core.series import (OVERRIDABLE, THIS_OCCURRENCE,
                                             parse_block_id)

        task_id, index = parse_block_id(request.require("id"))
        if _scope_of(request, task_id, index) == THIS_OCCURRENCE:
            # "This event": only a time, a title or a description can differ
            # per occurrence. Anything else - what it runs, how it repeats -
            # belongs to the series, and editing one block must not quietly
            # fork the task in two.
            wanted = {k: request.body[k] for k in OVERRIDABLE if k in request.body}
            rejected = sorted(set(request.body) - set(OVERRIDABLE) - {"id", "scope"})
            if rejected:
                raise ApiError(
                    f"{', '.join(rejected)} belongs to the whole series; "
                    f"send scope=all to change it")
            updated = app.scheduler.override_occurrence(task_id, index, wanted)
            if updated is None:
                raise ApiError("task not found", 404)
            return updated.to_dict()

        fields: Dict[str, Any] = {}
        for key in ("title", "description", "status", "execution_type", "script_path",
                    "timezone_name"):
            if key in request.body:
                fields[key] = request.body[key]
        if "scheduled_at" in request.body:
            when = parse_iso(request.body["scheduled_at"])
            if when is None:
                raise ApiError("scheduled_at must be an ISO-8601 timestamp")
            fields["scheduled_at"] = when
        if "payload" in request.body:
            fields["task_payload"] = request.body["payload"]
        if "notify" in request.body:
            # Section 52.2: flipping whether a task interrupts is a change to
            # one flag, not a rewrite of the payload it lives in - the rest of
            # the payload is what the task needs in order to run.
            existing = app.scheduler.store.get(task_id)
            if existing is None:
                raise ApiError("task not found", 404)
            payload = dict(fields.get("task_payload") or existing.task_payload or {})
            payload["notify_user"] = bool(request.body["notify"])
            fields["task_payload"] = payload
        if "metadata" in request.body:
            fields["metadata"] = request.body["metadata"]
        updated = app.scheduler.update_task(task_id, **fields)
        if updated is None:
            raise ApiError("task not found", 404)
        return updated.to_dict()

    @route("GET", "/api/questions")
    def list_questions(request: Any) -> Any:
        """Section 52.4: what the agent is waiting to hear from the user."""
        pending = app.questions.pending(limit=request.int_param("limit", 50))
        return {"questions": [q.to_dict() for q in pending]}

    @route("GET", "/api/questions/history")
    def question_history(request: Any) -> Any:
        answered = app.questions.recent(limit=request.int_param("limit", 50))
        return {"questions": [q.to_dict() for q in answered]}

    @route("POST", "/api/questions/answer")
    def answer_question(request: Any) -> Any:
        question_id = str(request.require("id"))
        answer = str(request.require("answer"))
        try:
            question = app.feedback.answer(question_id, answer,
                                           str(request.get("comment", "")))
        except ValueError as exc:
            raise ApiError(str(exc)) from exc
        if question is None:
            raise ApiError("question not found", 404)
        return question.to_dict()

    @route("GET", "/api/lessons")
    def lessons(_request: Any) -> Any:
        """The Lessons panel: each kind of lesson, with every record it rests
        on in full. Not experience - nothing here is grouped or retrieved."""
        # The notification lessons, then the connection lessons - the user's
        # own changes to their contacts (heartbeat/connections/store.py).
        return {"kinds": [app.calendar_nature.describe(),
                          app.connections.describe_lessons()]}

    @route("GET", "/api/lessons/cards")
    def lesson_cards(_request: Any) -> Any:
        """THE LESSON CARDS (heartbeat/experience/lesson_cards.py): each
        answered question of the user's work, newest first - its evidence,
        its versions, and how often it was used. None rests on the web."""
        from ..heartbeat.experience.lesson_cards import LessonCards
        cards = LessonCards(app.db)
        shown = []
        for card in cards.cards()[:200]:
            shown.append(dict(card, uses=len(cards.uses(card["id"])),
                              versions=len(cards.versions(card["id"]))))
        return {"cards": shown, "count": len(shown)}

    def _edit_task(request: Any, task_id: str, said: str) -> Dict[str, Any]:
        """The user editing an event from its card (scheduler/core/editing.py).

        Only what is sent is edited: title, message (the words the
        notification shows), at (the next run, ISO with its offset) and
        repeat (null or {} for once; {"every", "unit", "until"|"count"}).
        A repeating event is edited whole, whichever block was opened.

        The edit is a lesson: what the user wanted INSTEAD of what was there.
        It is recorded with the event as it was and as it is now, both
        whole, and why, if they said - and only when something did change.
        """
        from ..scheduler.core.editing import EditRefused

        asked = {key: request.body[key] for key in ("title", "message", "at")
                 if key in request.body}
        if "repeat" in request.body:
            asked["repeat"] = request.body["repeat"]
        try:
            before, after, changed = app.scheduler.edit_task(task_id, **asked)
        except EditRefused as exc:
            raise ApiError(str(exc), exc.status) from exc
        if after is None:
            raise ApiError("task not found", 404)
        if changed:
            app.calendar_nature.record("edit", before, said=said, after=after)
        return {**after.to_dict(), "changed": changed}

    @route("POST", "/api/tasks/action")
    def task_action(request: Any) -> Any:
        from ..scheduler.core.series import THIS_OCCURRENCE, parse_block_id

        task_id, index = parse_block_id(request.require("id"))
        action = str(request.require("action"))
        scope = _scope_of(request, task_id, index)

        # Deleting or cancelling one block of a series removes that run and
        # leaves the rest standing - the "this event" half of the choice every
        # calendar offers. Everything else acts on the series, because there is
        # nothing about a single future occurrence to enable or run now.
        # What the user did to it is written down, as it was before they did
        # it - after a delete there is nothing left to read - with why, if
        # they said.
        before = app.scheduler.store.get(task_id)
        said = str(request.get("why") or "").strip()

        if action == "edit":
            return _edit_task(request, task_id, said)

        if scope == THIS_OCCURRENCE and action in ("delete", "cancel"):
            removed = app.scheduler.skip_occurrence(task_id, index)
            if removed is None:
                raise ApiError("task not found", 404)
            if action == "delete":
                app.calendar_nature.record("delete", before or removed,
                                           said=said)
            return {"deleted": True, "scope": THIS_OCCURRENCE,
                    "occurrence_index": index, "task": removed.to_dict()}

        if action == "cancel":
            task = app.scheduler.cancel(
                task_id, by="you",
                reason=str(request.get("reason") or "cancelled from the calendar"))
        elif action == "disable":
            task = app.scheduler.disable(task_id)
            if task is not None:
                app.calendar_nature.record("disable", before or task, said=said)
        elif action == "enable":
            task = app.scheduler.enable(task_id)
        elif action == "run_now":
            task = app.scheduler.reschedule(task_id, utcnow())
        elif action == "delete":
            deleted = app.scheduler.delete(task_id)
            if deleted and before is not None:
                app.calendar_nature.record("delete", before, said=said)
            return {"deleted": deleted, "scope": "all"}
        else:
            raise ApiError(f"unknown action {action}")
        if task is None:
            raise ApiError("task not found", 404)
        return task.to_dict()

    # ---------------------------------------------------------------- agent
    @route("POST", "/api/chat")
    def chat(request: Any) -> Any:
        message = str(request.require("message"))
        return app.agent.handle_message(message, request.get("conversation_id"))

    @route("POST", "/api/chat/followup")
    def chat_followup(request: Any) -> Any:
        """A follow-up about one reply, asked on its own (agent_core)."""
        try:
            return app.agent.follow_up(str(request.require("message_id")),
                                       str(request.get("about") or ""),
                                       str(request.get("question") or ""))
        except ValueError as exc:
            raise ApiError(str(exc), 404)

    @route("POST", "/api/chat/followup/delete")
    def chat_followup_delete(request: Any) -> Any:
        return {"deleted": app.conversations.delete_followup(
            str(request.require("id")))}

    @route("GET", "/api/conversations")
    def conversations(_request: Any) -> Any:
        # Every one: the chat's sidebar lists them all, newest first.
        return {"conversations": app.conversations.conversations(limit=None)}

    @route("POST", "/api/conversations")
    def create_conversation(request: Any) -> Any:
        """A new, empty conversation - in a project, when one is named."""
        project_id = request.get("project_id")
        if project_id is not None and not isinstance(project_id, str):
            raise ApiError("project_id must be text")
        try:
            conversation_id = app.conversations.create(project_id=project_id or None)
        except ValueError as exc:
            raise ApiError(str(exc), 404) from exc
        return app.conversations.conversation(conversation_id)

    @route("POST", "/api/conversation/rename")
    def rename_conversation(request: Any) -> Any:
        """The user's own name for a conversation, from the chat's list."""
        conversation_id = request.require("id")
        title = request.require("title")
        if not isinstance(conversation_id, str) or not isinstance(title, str):
            raise ApiError("a conversation's id and its name must be text")
        if not title.strip():
            raise ApiError("a conversation's name cannot be empty")
        if not app.conversations.rename(conversation_id, title):
            raise ApiError("there is no such conversation", 404)
        return {"renamed": conversation_id, "title": title.strip()[:120]}

    @route("POST", "/api/conversation/pin")
    def pin_conversation(request: Any) -> Any:
        """Pinned to the top of the chat's list, or unpinned."""
        conversation_id = request.require("id")
        if not isinstance(conversation_id, str):
            raise ApiError("a conversation's id must be text")
        pinned = request.get("pinned", True) is not False
        if not app.conversations.pin(conversation_id, pinned):
            raise ApiError("there is no such conversation", 404)
        return {"pinned": pinned, "id": conversation_id}

    @route("POST", "/api/conversation/delete")
    def delete_conversation(request: Any) -> Any:
        """A conversation taken off the chat's list, with everything said in it."""
        conversation_id = request.require("id")
        if not isinstance(conversation_id, str):
            raise ApiError("a conversation's id must be text")
        if not app.conversations.delete(conversation_id):
            raise ApiError("there is no such conversation", 404)
        return {"deleted": conversation_id}

    @route("GET", "/api/chat/projects")
    def chat_projects(_request: Any) -> Any:
        return {"projects": app.conversations.projects()}

    @route("POST", "/api/chat/projects")
    def create_chat_project(request: Any) -> Any:
        name = request.require("name")
        if not isinstance(name, str):
            raise ApiError("a project's name must be text")
        try:
            return app.conversations.create_project(name)
        except ValueError as exc:
            raise ApiError(str(exc)) from exc

    @route("GET", "/api/conversation")
    def conversation(request: Any) -> Any:
        """A conversation whole, to be drawn again as it was: every message
        with its id, and the follow-ups asked about its replies."""
        conversation_id = str(request.require("id"))
        stored = app.conversations.conversation(conversation_id)
        if stored is None:
            raise ApiError("there is no such conversation", 404)
        messages = app.conversations.messages(conversation_id, limit=None)
        return {"conversation_id": conversation_id,
                "project_id": stored.get("project_id"),
                "title": stored.get("title") or "",
                "messages": messages,
                "followups": app.conversations.followups(
                    [message["id"] for message in messages])}

    # ------------------------------------------------------------ heartbeats
    @route("GET", "/api/heartbeat")
    def heartbeat(_request: Any) -> Any:
        return app.heartbeat.status()

    @route("POST", "/api/heartbeat/run")
    def heartbeat_run(_request: Any) -> Any:
        # Section 37: only the debugger trigger, never a conversation turn.
        # Run with the button: its calls the user's, not the function's own.
        from ..common.observability import started
        with started.now(("heartbeat:", "heartbeat_trigger")):
            return app.heartbeat.run_once(trigger="manual_debug")

    @route("GET", "/api/heartbeat/progress")
    def heartbeat_progress(_request: Any) -> Any:
        """Section 15.1: what the heartbeat is doing while somebody watches.

        Read-only, and safe to call while a run is in flight - it returns a
        copy of what the run has filled in so far.
        """
        return app.heartbeat.progress()

    @route("GET", "/api/heartbeat/checklist")
    def checklist(_request: Any) -> Any:
        # Section 35, as amended: four fixed steps. There is nothing to edit
        # here, so nothing writes.
        return {"items": [i.to_dict() for i in app.checklist.items()],
                "editable": False}

    # -- experience: how the work went, and how the user works --------------
    def _experience_of(_request: Any) -> Any:
        """The learned experience - there is one."""
        return app.state_heartbeat.experience

    @route("GET", "/api/experience")
    def experience_status(_request: Any) -> Any:
        experience = app.state_heartbeat.experience
        return {**experience.status(), "units": experience.units()}

    @route("GET", "/api/experience/nature")
    def experience_nature(request: Any) -> Any:
        """One nature, whole: its example, its pattern and principle, the
        lessons it is attached to."""
        said = app.state_heartbeat.experience.nature(str(request.require("id")))
        if said is None:
            raise ApiError("no such nature", 404)
        return said

    @route("GET", "/api/experience/natures/merge_preview")
    def experience_natures_merge_preview(request: Any) -> Any:
        """A DRY RUN: the natures within each pattern that are the same
        trait, and the one each set would become. Nothing is changed.
        ?mode=similarity (free, the default) or ?mode=model (a call per
        pattern, at most ?budget=); ?patterns=a,b for only those."""
        mode = str(request.get("mode") or "similarity")
        if mode not in ("similarity", "model"):
            raise ApiError("mode is similarity or model", 400)
        patterns = [one for one in str(request.get("patterns") or "").split(",") if one]
        return app.state_heartbeat.experience.merge_same_natures(
            dry_run=True, mode=mode, patterns=patterns,
            budget=request.int_param("budget", 200))

    @route("POST", "/api/experience/natures/merge")
    def experience_natures_merge(request: Any) -> Any:
        """Merge what the dry run proposed - or the `sets` sent, each
        {keep, merge: [ids], title, content, why}. The files touched are
        copied aside first."""
        from datetime import datetime
        sets = request.get("sets")
        if sets is not None and not isinstance(sets, list):
            raise ApiError("sets is a list", 400)
        backup = (app.data_dir / "backups"
                  / f"before-natures-merged-{datetime.now():%Y%m%d-%H%M%S}")
        try:
            said = app.state_heartbeat.experience.merge_same_natures(
                dry_run=False, sets=sets, backup=backup)
        except ValueError as exc:
            raise ApiError(str(exc), 400)
        if said.get("error"):
            raise ApiError(said["error"], 409)
        return said

    @route("POST", "/api/experience/learn")
    def experience_learn(request: Any) -> Any:
        """Start, or update, the learning of projects - several at once, as
        many as there is room for; the rest wait their turn. Stopped with
        ?stop=1. What was learned is never cleared."""
        experience = app.state_heartbeat.experience
        units = [str(one) for one in (request.get("units") or [])
                 if str(one).strip()]
        if request.get("unit"):
            units.append(str(request.get("unit")))
        if request.get("stop"):
            return {"stopping": [one for one in units if experience.stop_learning(one)]}
        if experience.auto_running():
            return {"error": "Automatic learning is running - wait for it to finish",
                    "started": []}
        return {"started": experience.learn(units),
                "learning": {one: experience.learning_of(one) for one in units}}

    @route("GET", "/api/pool")
    def pool_status(_request: Any) -> Any:
        """What is going side by side now, of every kind, and how many may."""
        return app.pool.status()

    @route("GET", "/api/experience/auto")
    def experience_auto(_request: Any) -> Any:
        """The automatic update: its times, the next one, the run going."""
        return app.learning_clock.status()

    @route("POST", "/api/experience/auto")
    def experience_auto_set(request: Any) -> Any:
        """Its times ({"times": "03:00, 15:00"}), or one project on or off
        ({"unit": ..., "on": false})."""
        clock = app.learning_clock
        if request.get("times") is not None:
            clock.set_times(str(request.get("times") or ""))
        if request.get("unit"):
            clock.set_opted(str(request.get("unit")), bool(request.get("on")))
        return clock.status()

    @route("POST", "/api/experience/remove")
    def experience_remove(request: Any) -> Any:
        """Remove a project, as the user confirmed on the page: what was
        learned from it is deleted, the natures it showed kept."""
        unit = str(request.get("unit") or "").strip()
        if not unit:
            return {"error": "which project?"}
        return app.state_heartbeat.experience.remove_unit(unit)

    @route("GET", "/api/experience/stage_detail")
    def experience_stage_detail(request: Any) -> Any:
        """One stage's natures and what it is found by - asked when its card
        is opened, not with every picture."""
        said = app.state_heartbeat.experience.stage_detail(str(request.require("stage")))
        if said is None:
            raise ApiError("no such stage", 404)
        return said

    @route("GET", "/api/experience/graph")
    def experience_graph(request: Any) -> Any:
        said = _experience_of(request).graph(str(request.require("unit")))
        if said.get("error"):
            raise ApiError(said["error"], 404)
        return said

    @route("GET", "/api/experience/all")
    def experience_all(request: Any) -> Any:
        return {"units": _experience_of(request).all_graphs()}

    @route("GET", "/api/experience/patterns")
    def experience_patterns(request: Any) -> Any:
        return _experience_of(request).pattern_tree()

    @route("GET", "/api/experience/retrieve")
    def experience_retrieve(request: Any) -> Any:
        from ..heartbeat.experience.retrieve import STEPS_AROUND
        kinds = [one for one in str(request.get("kinds") or "").split(",") if one]
        return app.state_heartbeat.experience.retriever().retrieve(
            str(request.require("q")), kinds=kinds,
            medium=str(request.get("medium") or ""),
            steps=request.int_param("steps", STEPS_AROUND),
            # Each kind's quota may be asked for: ?principle=5&nature=30.
            kept={kind: request.int_param(kind, 0) for kind in
                  ("principle", "pattern", "nature") if request.get(kind)})

    @route("POST", "/api/work/start")
    def work_start(request: Any) -> Any:
        unit = app.state_heartbeat.experience.unit(str(request.require("unit")))
        if unit is None:
            raise ApiError("no such project or task", 404)
        # packages: "ask" (the default) - each install waits for the user -
        # or "auto". sound: true plays one when the run's session ends.
        said = app.work.start(unit, str(request.get("instruction") or ""),
                              limits=request.get("limits") or {},
                              packages=request.get("packages"),
                              sound=request.get("sound"))
        if said.get("error"):
            raise ApiError(said["error"], 409)
        return said

    # ------------------------------------------------------- delegate work
    @route("POST", "/api/work/drafts")
    def work_draft_new(request: Any) -> Any:
        """A piece of work handed over - text, folders, files, the window it
        came from, or the project or task it is for - its context collected
        and its plan drafted, for the user to check before anything runs."""
        def listed(name):
            value = request.get(name) or []
            return [str(one) for one in (value if isinstance(value, list) else [value])]
        draft = app.drafts.create(
            text=str(request.get("text") or ""), folders=listed("folders"),
            files=listed("files"), window=request.get("window") or {},
            unit_id=str(request.get("unit") or ""),
            source=str(request.get("source") or "delegate"),
            # The instruction already typed (Experience's box): what the
            # Delegate work box opens with, to change before Create.
            options={k: request.get(k) for k in ("limits", "packages", "sound", "instruction")
                     if request.get(k) is not None},
            write_first=bool(request.get("write_first", True)))
        return {"draft": draft}

    @route("POST", "/api/work/drafts/compose")
    def work_draft_compose(request: Any) -> Any:
        """Create: the user's instruction taken, the context collected and the
        plan drafted - for them to approve."""
        said = app.drafts.compose(str(request.require("id")),
                                  str(request.get("instruction") or ""))
        if said.get("error"):
            raise ApiError(said["error"], 409)
        return said

    @route("POST", "/api/work/drafts/inbox")
    def work_draft_inbox(request: Any) -> Any:
        """What Explorer's "Delegate work" handed over (a note of paths, left
        by `--delegate`): taken, and made a piece of work."""
        from ..agent.work.handover import take
        said = take(app.data_dir, str(request.require("id")))
        if said is None:
            raise ApiError("nothing was handed over under that id", 404)
        return {"draft": app.drafts.create(folders=said["folders"], files=said["files"],
                                           source="explorer", write_first=True)}

    @route("GET", "/api/work/drafts")
    def work_drafts(request: Any) -> Any:
        ident = str(request.get("id") or "")
        if ident:
            draft = app.drafts.get(ident)
            if draft is None:
                raise ApiError("no such piece of work", 404)
            return {"draft": draft}
        return {"drafts": app.drafts.recent()}

    @route("POST", "/api/work/drafts/start")
    def work_draft_start(request: Any) -> Any:
        """Started with the plan as the user left it."""
        said = app.drafts.start(str(request.require("id")), plan=request.get("plan"),
                                folders=request.get("folders"))
        if said.get("error"):
            raise ApiError(said["error"], 409)
        return said

    @route("GET", "/api/avatar/icon")
    def avatar_icon(request: Any) -> Any:
        """The avatar's face, small, as each "Delegate work" wears it."""
        import base64
        import io
        from ..ui.tray import icon_image
        size = max(16, min(256, request.int_param("size", 32)))
        image = icon_image(app.characters, str(app.config.get("character", "watermelon")),
                           size)
        if image is None:
            return {"png": ""}
        out = io.BytesIO()
        image.save(out, format="PNG")
        return {"png": "data:image/png;base64," + base64.b64encode(out.getvalue()).decode(),
                "name": app.avatar_name()}

    @route("POST", "/api/work/drafts/cancel")
    def work_draft_cancel(request: Any) -> Any:
        return {"ok": app.drafts.cancel(str(request.require("id")))}

    @route("POST", "/api/work/projects")
    def work_project_new(request: Any) -> Any:
        """A new project for the agents: its name, what is wanted, the
        folders they may change, the limits - made, and the work started."""
        name = str(request.get("name") or "").strip()
        instruction = str(request.get("instruction") or "").strip()
        if not name:
            raise ApiError("give the project a name", 400)
        if not instruction:
            raise ApiError("say what you want done: this is new work", 400)
        # A run of the user's own work - the Experience debugger's runs go
        # side by side with theirs.
        # In this process, one run at a time; each in a process of its own,
        # as many as the computer has room for (agent/work/governor.py).
        if app.work.going(debug=False) and not app.work.in_subprocess:
            raise ApiError("a run is already going; stop it first", 409)
        experience = app.state_heartbeat.experience
        made = experience.store.create_work_project(name)
        unit_id = made["unit_id"]
        for path in request.get("folders") or []:
            said = app.work.grant(unit_id, str(path))
            if said.get("error"):
                experience.store.drop_workspace(unit_id)
                raise ApiError(said["error"], 400)
        unit = experience.unit(unit_id)
        said = app.work.start(unit, instruction, limits=request.get("limits") or {},
                              packages=request.get("packages"),
                              sound=request.get("sound"))
        if said.get("error"):
            raise ApiError(said["error"], 409)
        return dict(said, unit=unit_id)

    @route("GET", "/api/work/limits")
    def work_limits(_request: Any) -> Any:
        from ..agent.work.runner import DEFAULT_LIMITS
        # Zero or less is no limit. Tokens in and out are limited apart -
        # what went in counted whole, its cached part too; a caller that
        # sends one "tokens" limit, on both together, still has it honoured.
        return {"defaults": DEFAULT_LIMITS, "none": 0,
                "names": {"minutes": "minutes", "tokens_in": "input tokens",
                          "tokens_out": "output tokens", "calls": "model calls"}}

    @route("GET", "/api/work/folders")
    def work_folders(request: Any) -> Any:
        unit = str(request.require("unit"))
        # Whether the operating system confines the work's code - the panel
        # says "not OS-isolated: <why>" plainly, and Auto installs are off.
        from ..runtime.isolation import describe
        return {"folders": app.work.folders(unit), "warning": app.work.GRANT_WARNING,
                "isolation": describe()}

    @route("POST", "/api/work/folders")
    def work_folder_grant(request: Any) -> Any:
        said = app.work.grant(str(request.require("unit")), str(request.require("path")))
        if said.get("error"):
            raise ApiError(said["error"], 400)
        return said

    @route("POST", "/api/work/folders/remove")
    def work_folder_revoke(request: Any) -> Any:
        return {"removed": app.work.revoke(str(request.require("unit")),
                                           str(request.require("path")))}

    @route("GET", "/api/work/tree")
    def work_tree(request: Any) -> Any:
        unit = app.state_heartbeat.experience.unit(str(request.require("unit")))
        if unit is None:
            raise ApiError("no such project or task", 404)
        return app.work.tree(unit)

    @route("POST", "/api/work/stop")
    def work_stop(request: Any) -> Any:
        # Stopping wraps up - the deck of what exists, the to-do list -
        # unless it is to stop now.
        return {"stopping": app.work.stop(str(request.require("run")),
                                          now=bool(request.get("now")))}

    @route("POST", "/api/work/pause")
    def work_pause(request: Any) -> Any:
        """Pause a run that is going: it stops at its next safe point - before
        a model call, before a tool call, between rounds - and does nothing
        until resumed. The application holds where it was; closing the page
        changes nothing, closing the application interrupts it."""
        if not app.work.pause(str(request.require("run"))):
            raise ApiError("that run is not going, or is being stopped", 409)
        return {"paused": True}

    @route("POST", "/api/work/resume")
    def work_resume(request: Any) -> Any:
        """A paused run goes on exactly where it stopped."""
        if not app.work.resume(str(request.require("run"))):
            raise ApiError("that run is not going", 409)
        return {"paused": False}

    @route("GET", "/api/work/live")
    def work_live(request: Any) -> Any:
        """A run's live session, as the page follows it: its events after
        `since` (each with seq, at, who, kind, text, detail), its model
        calls numbered after `calls` (each with n, started, finished, who,
        part, tokens_in, tokens_out, failed - for the chart of them), where
        it stands against its limits, its state (going, paused,
        waiting_for_you, ended, interrupted) and the requests waiting for
        your answer. Asked again and again: an answer that has not changed
        is not sent again (the ETag)."""
        def number(name: str) -> int:
            try:
                return max(0, int(request.get(name) or 0))
            except (TypeError, ValueError):
                return 0
        said = app.work.live(str(request.require("run")), number("since"),
                             calls=number("calls"))
        if said is None:
            raise ApiError("no such run", 404)
        return said

    @route("GET", "/api/work/reading")
    def work_reading(_request: Any) -> Any:
        """How far the one-time grant that lets the agents' code read the
        home has got - per root: running, done or failed."""
        from ..runtime.isolation import reading_status
        return {"roots": reading_status()}

    @route("POST", "/api/work/continue")
    def work_continue(request: Any) -> Any:
        """A paused run, carried on from where it stopped with new limits -
        or, with from_version, from that version: an older one rolls the
        work back to it first, and goes on as a new branch. One the
        application closed on goes on from its last milestone."""
        run = app.work.run(str(request.require("run")))
        if run is None:
            raise ApiError("no such run", 404)
        from ..agent.work.debug_projects import is_debug_unit
        if is_debug_unit(run["unit_id"]):
            # One of the Experience debugger's: carried on as its own are.
            return debugger_experience_continue(request)
        unit = app.state_heartbeat.experience.unit(run["unit_id"])
        if unit is None:
            raise ApiError("its project is gone", 404)
        # Packages asked about as before, unless a choice is sent. With
        # instruction - what to change from here, in the user's words - the
        # contract is amended for the line it goes on, before any work.
        from ..agent.work.runner import INSTRUCTION_CHARS
        said = app.work.continue_run(run["id"], unit, limits=request.get("limits") or {},
                                     packages=request.get("packages"),
                                     from_version=str(request.get("from_version") or ""),
                                     instruction=str(request.get("instruction") or "")
                                     .strip()[:INSTRUCTION_CHARS],
                                     sound=request.get("sound"))
        if said.get("error"):
            raise ApiError(said["error"], 409)
        return said

    @route("POST", "/api/work/contract/take_out")
    def work_contract_take_out(request: Any) -> Any:
        """What was added to a run's contract and is not the user's words,
        taken out by them - on a run not working now."""
        run = app.work.run(str(request.require("run")))
        if run is None:
            raise ApiError("no such run", 404)
        from ..agent.work.debug_projects import DebugProjects, is_debug_unit, parse_unit
        unit = (DebugProjects(app.work).unit(*parse_unit(run["unit_id"]))
                if is_debug_unit(run["unit_id"])
                else app.state_heartbeat.experience.unit(run["unit_id"]))
        if unit is None:
            raise ApiError("its project is gone", 404)
        said = app.work.take_out(run["id"], unit, str(request.require("added")))
        if said.get("error"):
            raise ApiError(said["error"], 409)
        return said

    def _versions_of(call: Any) -> Any:
        """A version that is not there, or is damaged: not found."""
        from ..agent.work.versions import VersionError
        try:
            return call()
        except VersionError as exc:
            raise ApiError(str(exc), 404)

    @route("GET", "/api/work/versions")
    def work_versions(request: Any) -> Any:
        """A run's versions as a tree: each with its parent and line, what it
        was saved at, and in brief where the run stood; and the head."""
        run_id = str(request.require("run"))
        return _versions_of(lambda: app.work.versions(run_id))

    @route("GET", "/api/work/version")
    def work_version(request: Any) -> Any:
        """One version: its files, to-do list, contract goal, whether it
        has a deck - and its slides: where the whole work stood at it, as
        the work agent showed it then, rendered as a page ("made"), or why
        there are none ("not made", or "none" for a version saved before
        versions had slides)."""
        run_id = str(request.require("run"))
        version_id = str(request.require("id"))
        return _versions_of(lambda: app.work.version(run_id, version_id))

    @route("GET", "/api/work/going")
    def work_going(_request: Any) -> Any:
        """The runs going now, and how - going, paused, waiting for you, and
        for what - for the list of projects and the tab."""
        return {"going": app.work.run_states()}

    @route("POST", "/api/work/approve")
    def work_approve(request: Any) -> Any:
        """The user's answer to a run's request: to install packages, allow
        true installs them, false tells the agent it may not; to a run
        waiting on its model, true tries again now, false ends the run."""
        # Only a plain yes approves: anything unclear is a no.
        allow = request.get("allow")
        said = app.work.approve(str(request.require("run")),
                                str(request.require("request")),
                                allow is True or str(allow).lower() in ("true", "1", "yes"))
        if said is None:
            raise ApiError("no such request of a run that is going", 404)
        return {"request": said}

    @route("GET", "/api/work/presentation")
    def work_presentation(request: Any) -> Any:
        """The deck of a run's results, as a page - its figures served by
        the app, from the run's own folders only."""
        from urllib.parse import quote
        from ..agent.work.deck import render
        run = app.work.run(str(request.require("run")))
        version = str(request.get("version") or "")
        if run is not None and version:
            # The deck as it was at a version, its figures as that version
            # holds them.
            deck = _versions_of(lambda: app.work.version_deck(run["id"], version))
            if not deck:
                raise ApiError("that version has no deck", 404)
            page = render(deck, lambda path: (
                f"/api/work/file?run={quote(run['id'])}&path={quote(path)}"
                f"&version={quote(version)}"))
            return Raw(page.encode("utf-8"), "text/html; charset=utf-8")
        if run is None or not (run.get("plan") or {}).get("deck"):
            raise ApiError("this run has no deck", 404)
        page = render(run["plan"]["deck"], lambda path: (
            f"/api/work/file?run={quote(run['id'])}&path={quote(path)}"))
        return Raw(page.encode("utf-8"), "text/html; charset=utf-8")

    @route("GET", "/api/work/file")
    def work_file(request: Any) -> Any:
        """A file a run made - an image for its deck. Only from the run's
        folder and the folders it was granted."""
        import mimetypes
        version = str(request.get("version") or "")
        if version:
            # As a version holds it: from the run's own store only.
            run_id, path = str(request.require("run")), str(request.require("path"))
            kind = mimetypes.guess_type(path)[0]
            data = (_versions_of(lambda: app.work.version_file(run_id, version, path))
                    if (kind or "").startswith("image/") else None)
            if data is None:
                raise ApiError("not a file of this version", 404)
            return Raw(data, kind)
        found = app.work.readable_by(str(request.require("run")),
                                     str(request.require("path")))
        kind = mimetypes.guess_type(str(found))[0] if found else None
        if found is None or not found.is_file() or not (kind or "").startswith("image/"):
            raise ApiError("not a file of this run", 404)
        return Raw(found.read_bytes(), kind)

    @route("POST", "/api/work/reveal")
    def work_reveal(request: Any) -> Any:
        """Show where a deliverable is, in the machine's own file manager.
        Only a file of the run's folders."""
        import subprocess
        import sys
        found = app.work.readable_by(str(request.require("run")),
                                     str(request.require("path")))
        if found is None:
            raise ApiError("not a file of this run", 404)
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer", f"/select,{found}"])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(found)])
        else:
            subprocess.Popen(["xdg-open", str(found.parent)])
        return {"shown": str(found)}

    @route("POST", "/api/work/open_logs")
    def work_open_logs(request: Any) -> Any:
        """OPEN LOG FOLDER: the project's run logs - a folder per run, by
        when it started, each with its full transcript and live session -
        in the machine's own file manager. Made first if it is not there
        yet, inside the project's own work folder only."""
        try:
            opened = app.work.open_logs(str(request.require("run")))
        except PermissionError as exc:
            raise ApiError(str(exc), 403)
        if opened is None:
            raise ApiError("no such run", 404)
        return {"opened": str(opened)}

    @route("GET", "/api/work/runs")
    def work_runs(request: Any) -> Any:
        return {"runs": app.work.runs(str(request.get("unit") or ""))}

    # -- the Experience debugger: the same work done three ways ---------------
    # Its projects are never the user's - never in Experience - and each way
    # of one is a unit of its own (see agent/work/debug_projects.py). A run
    # once started is any run: stopped, paused, followed and opened through
    # the /api/work routes above, which go by the run alone.
    def _debug_projects() -> Any:
        from ..agent.work.debug_projects import DebugProjects
        return DebugProjects(app.work)

    @route("GET", "/api/debugger/experience/projects")
    def debugger_experience_projects(_request: Any) -> Any:
        from ..agent.work.debug_projects import (NO_LIMITS, VARIANT_ABOUT,
                                                 VARIANT_FOLDERS, VARIANT_LABELS, VARIANTS)
        return {"projects": _debug_projects().projects(),
                "variants": [{"variant": one, "label": VARIANT_LABELS[one],
                              "about": VARIANT_ABOUT[one], "folder": VARIANT_FOLDERS[one]}
                             for one in VARIANTS],
                "limits": NO_LIMITS}

    @route("POST", "/api/debugger/experience/projects")
    def debugger_experience_project_change(request: Any) -> Any:
        """create {name, description, folder} - the three subfolders made in
        it; the instruction is given when a way starts; update {id, name?,
        description?}; remove {id} - its records, never its files; clear
        {id, variant} - everything that one way wrote in its folder, and its
        runs, gone: its folder stays, empty, and the other two ways and the
        project as they are; clear_all {id} - the three ways cleared so, all
        or none (not while any goes); new_try {id} - the next try, its own
        folder and three ways, blind to every other try; use_try {id, try} -
        which try is shown and started from; remove_try {id} - that try's
        folder, everything in it, its runs and its sessions, deleted."""
        projects = _debug_projects()
        action = str(request.get("action") or "create")
        if action == "create":
            said = projects.create(str(request.get("name") or ""),
                                   str(request.get("description") or ""),
                                   str(request.get("instruction") or ""),
                                   str(request.get("folder") or ""))
        elif action == "update":
            said = projects.update(str(request.require("id")), name=request.get("name"),
                                   description=request.get("description"),
                                   instruction=request.get("instruction"))
        elif action == "remove":
            said = projects.remove(str(request.require("id")))
        elif action == "clear":
            said = projects.clear(str(request.require("id")),
                                  str(request.require("variant")))
        elif action == "clear_all":
            said = projects.clear_all(str(request.require("id")))
        elif action == "new_try":
            # The next try, blind to every other, and shown.
            said = projects.new_try(str(request.require("id")))
        elif action == "use_try":
            said = projects.use_try(str(request.require("id")), request.require("try"))
        elif action == "remove_try":
            # The try shown, its folder and all in it, deleted (confirmed on
            # the page first).
            said = projects.remove_try(str(request.require("id")))
        else:
            raise ApiError("action is create, update, remove, clear, clear_all, new_try, "
                           "use_try or remove_try", 400)
        if said.get("error"):
            raise ApiError(said["error"], 404 if said["error"] == "no such project" else 400)
        return said

    @route("POST", "/api/debugger/experience/start")
    def debugger_experience_start(request: Any) -> Any:
        """One way of a project started, with the instruction given now -
        with no limit unless one is given."""
        said = _debug_projects().start(str(request.require("project")),
                                       str(request.require("variant")),
                                       limits=request.get("limits") or {},
                                       packages=request.get("packages"),
                                       instruction=request.get("instruction"),
                                       sound=request.get("sound"))
        if said.get("error"):
            raise ApiError(said["error"], 409)
        return said

    @route("POST", "/api/debugger/experience/continue")
    def debugger_experience_continue(request: Any) -> Any:
        """One way's run carried on: with words (what to change from here),
        from a version, with new limits - none unless given."""
        from ..agent.work.debug_projects import parse_unit
        from ..agent.work.runner import INSTRUCTION_CHARS
        run = app.work.run(str(request.require("run")))
        if run is None:
            raise ApiError("no such run", 404)
        parsed = parse_unit(run["unit_id"])
        if parsed is None:
            raise ApiError("that run is not one of the Work debugger's", 409)
        project = str(request.get("project") or parsed[0])
        variant = str(request.get("variant") or parsed[1])
        words = str(request.get("words") or request.get("instruction") or "")
        said = _debug_projects().continue_run(
            project, variant, run["id"], words=words.strip()[:INSTRUCTION_CHARS],
            from_version=str(request.get("from_version") or ""),
            limits=request.get("limits") or {}, packages=request.get("packages"),
            sound=request.get("sound"))
        if said.get("error"):
            raise ApiError(said["error"], 409)
        return said

    @route("GET", "/api/debugger/experience/runs")
    def debugger_experience_runs(request: Any) -> Any:
        """One way's runs - each as /api/work/runs gives a run - and, while
        it waits for room to start, how it waits."""
        project, variant = str(request.require("project")), str(request.require("variant"))
        return {"runs": _debug_projects().runs(project, variant),
                "waiting": _debug_projects().waiting(project, variant)}

    @route("POST", "/api/debugger/experience/unwait")
    def debugger_experience_unwait(request: Any) -> Any:
        """A way waiting for room, not started after all."""
        said = _debug_projects().unwait(str(request.require("project")),
                                        str(request.require("variant")))
        if said.get("error"):
            raise ApiError(said["error"], 404)
        return said

    @route("GET", "/api/debugger/experience/tree")
    def debugger_experience_tree(request: Any) -> Any:
        """One way's own tree of cards and stages - as /api/work/tree gives a
        work's - so the debugger shows how its stages evolve as the
        Experience tab does. /api/work/tree finds its unit through the
        experience service, which knows nothing of the debugger's ways: the
        way is found here, through its project."""
        unit = _debug_projects().unit(str(request.require("project")),
                                      str(request.require("variant")))
        if unit is None:
            raise ApiError("no such project or way", 404)
        return app.work.tree(unit)

    @route("GET", "/api/experience/workspaces")
    def experience_workspaces(_request: Any) -> Any:
        return {"workspaces": app.state_heartbeat.experience.store.workspaces()}

    @route("POST", "/api/experience/workspaces")
    def experience_workspace_set(request: Any) -> Any:
        """Add a folder as a project; correct a project's folder; or remove
        one - its stages stay, it is not read again."""
        experience = app.state_heartbeat.experience
        unit = str(request.get("unit") or "").strip()
        path = str(request.get("path") or "").strip()
        if request.get("remove") and unit:
            experience.store.drop_workspace(unit)
            return {"removed": unit}
        if not path:
            raise ValueError("a folder is needed")
        try:
            if unit:
                from pathlib import Path as _Path
                folder = _Path(path).expanduser().resolve()
                if not folder.is_dir():
                    raise ValueError(f"not a folder: {folder}")
                held = experience.store.set_workspace(unit, str(folder), "chosen")
            else:
                held = experience.add_directory(path)
        except ValueError as exc:
            return {"error": str(exc)}
        return {"workspace": held}

    @route("GET", "/api/experience/prompts")
    def experience_prompts(request: Any) -> Any:
        """What the stage writer was given for a piece of work, call by call,
        whole: the system prompt, every message and round, and the answer."""
        experience = _experience_of(request)
        if request.get("id"):
            return {"prompt": experience.store.prompt(str(request.get("id")))}
        if request.get("unit"):
            return {"prompts": experience.store.prompts(str(request.get("unit")))}
        # Every piece of work's, listed without their text.
        names = {one["unit_id"]: one["name"]
                 for one in experience.store.workspaces() if one.get("name")}
        try:
            names.update({one["id"]: one["name"] for one in experience.units()})
        except Exception:        # pragma: no cover - names are a nicety
            pass
        units: Dict[str, Any] = {}
        for row in experience.store.prompt_index():
            row["written_by"] = json.loads(row.get("written_by") or "{}")
            units.setdefault(row["unit_id"], {
                "id": row["unit_id"], "name": names.get(row["unit_id"], row["unit_id"]),
                "prompts": []})["prompts"].append(row)
        return {"units": list(units.values())}

    @route("GET", "/api/experience/projects")
    def experience_projects(_request: Any) -> Any:
        """The projects the user made of their folders: each with when it was
        last processed, how many stages it has, and how its processing is
        going if it is."""
        experience = app.state_heartbeat.experience
        out = []
        for one in experience.store.workspaces(origin="chosen"):
            stages = experience.store.stages([one["unit_id"]])
            out.append({"id": one["unit_id"], "name": one["name"],
                        "paths": one["paths"], "summary": one["summary"],
                        "description": description_of(one),
                        "processed_until": one["processed_until"],
                        "processing_until": one.get("processing_until") or "",
                        "stages": len(stages),
                        "progress": dict(experience.project_progress.get(
                            one["unit_id"], {}))})
        return {"projects": out}

    @route("POST", "/api/experience/projects")
    def experience_project_change(request: Any) -> Any:
        """Make a project of folders, change its folders, change what the
        user says it is for (update: description, and name if given),
        remove it, or process it - the first time or as an update."""
        experience = app.state_heartbeat.experience
        action = str(request.get("action") or "")
        unit = str(request.get("unit") or "")
        paths = [str(one).strip() for one in (request.get("paths") or [])
                 if str(one).strip()]
        try:
            if action == "create":
                return {"project": experience.create_project(
                    str(request.get("name") or ""), paths,
                    str(request.get("description") or ""))}
            if action == "update" and unit:
                held = experience.store.set_description(
                    unit, str(request.get("description") or ""),
                    name=request.get("name"))
                if held is None:
                    raise ValueError("no such project")
                return {"project": dict(held, description=description_of(held))}
            if action == "set" and unit:
                # A project is its folders: the last one is not taken away.
                if not paths:
                    raise ValueError("a project needs at least one folder")
                for one in paths:
                    from pathlib import Path as _Path
                    if not _Path(one).expanduser().is_dir():
                        raise ValueError(f"not a folder: {one}")
                experience.store.set_paths(
                    unit, [str(__import__("pathlib").Path(one).expanduser().resolve())
                           for one in paths],
                    name=request.get("name"))
                return {"project": experience.store.workspace(unit)}
            if action == "remove" and unit:
                experience.store.drop_workspace(unit)
                return {"removed": unit}
            if action == "process" and unit:
                return {"started": experience.process_project(unit)}
            if action == "resume" and unit:
                return {"started": experience.process_project(unit, resume=True)}
        except ValueError as exc:
            return {"error": str(exc)}
        return {"error": "nothing to do"}

    @route("GET", "/api/state_heartbeat/picture")
    def state_heartbeat_picture(_request: Any) -> Any:
        """The last round, trimmed to what the drawing needs.

        The overview opens this on every visit, and the debugger's own
        report is megabytes of prompts and exchanges - the right payload
        for investigating a round and the wrong one for a tab somebody
        opens twenty times a day.
        """
        return app.state_heartbeat.the_picture()

    @route("GET", "/api/state_heartbeat")
    def state_heartbeat(_request: Any) -> Any:
        return app.state_heartbeat.status()

    @route("GET", "/api/state_heartbeat/not_this_work")
    def state_not_this_work(request: Any) -> Any:
        """WHAT THE MODEL SAID IS NOT THIS WORK, newest first: each task, the
        states it would take out, and the call that said so - and whether
        anything was taken out (only once it is switched on)."""
        on = bool(app.config.get("state_take_out_other_work", False))
        said = list(app.config.get("state_not_this_work" if on
                                   else "state_not_this_work_said") or [])[::-1][:100]
        ids = sorted({one for entry in said for one in entry.get("states") or []}
                     | {str(entry.get("task") or "") for entry in said})
        titles: Dict[str, Dict[str, str]] = {}
        try:
            for at in range(0, len(ids), 400):
                part = ids[at:at + 400]
                for row in app.db.query(
                        f"SELECT id, title, starts_at FROM states WHERE id IN "
                        f"({','.join('?' * len(part))})", tuple(part)):
                    titles[row["id"]] = {"title": str(row["title"] or ""),
                                         "at": str(row["starts_at"] or "")}
                for row in app.db.query(
                        f"SELECT id, json_extract(described, '$.title') AS title FROM "
                        f"state_tasks WHERE id IN ({','.join('?' * len(part))})", tuple(part)):
                    titles[row["id"]] = {"title": str(row["title"] or ""), "at": ""}
        except Exception:        # pragma: no cover - read what can be read
            pass
        return {"on": on, "said": [
            {"at": entry.get("at"), "by": entry.get("by"),
             "task": {"id": entry.get("task"),
                      "title": (titles.get(str(entry.get("task"))) or {}).get("title", "")},
             "states": [{"id": one, **titles.get(one, {"title": "", "at": ""})}
                        for one in entry.get("states") or []]}
            for entry in said]}

    @route("GET", "/api/state_heartbeat/next")
    def state_heartbeat_next(_request: Any) -> Any:
        """Whether a round runs, and when the next regular one is due."""
        return app.state_heartbeat.when_next()

    @route("POST", "/api/state_heartbeat/start")
    def state_heartbeat_start(_request: Any) -> Any:
        """Run now, from the Overview: begun, and answered at once."""
        return app.state_heartbeat.start_now()

    @route("POST", "/api/state_heartbeat/run")
    def state_heartbeat_run(request: Any) -> Any:
        """One round now. `scope` picks where it reads and writes.

        The sandbox runs the same algorithm over states somebody made up,
        in its own library and against its own task rows - so the shape
        the grouping finds can be judged against the shape that went in,
        which is not something real states can tell you.
        """
        wanted = str(request.get("scope") or "live")
        if wanted != "sandbox":
            from ..common.observability import started
            # Run with the button: its calls the user's.
            with started.now(("state_heartbeat",)):
                return app.state_heartbeat.run_once(trigger="manual_debug", scope="live")
        # A sandbox round empties and fills the sandbox first, so one
        # press is a whole demonstration rather than a round over
        # whatever somebody left there. `fresh: false` keeps what is in
        # it, which is what the make-some-up-and-look-at-it path wants.
        out = app.state_heartbeat.run_in_the_sandbox(
            how_many=max(0, min(1000, int(request.get("how_many") or 0))),
            days=max(1, min(365, int(request.get("days") or 14))),
            seed=int(request.get("seed") or 0),
            fresh=request.get("fresh", True) is not False)
        report = dict(out.get("report") or {})
        report["sandbox_filled"] = {
            "emptied": out.get("emptied"), "made_up": out.get("made_up")}
        return report

    @route("POST", "/api/state_heartbeat/sandbox/states")
    def state_heartbeat_make_up(request: Any) -> Any:
        return app.state_heartbeat.make_up_states(
            how_many=max(2, min(1000, int(request.get("how_many") or 60))),
            days=max(1, min(365, int(request.get("days") or 14))),
            seed=int(request.get("seed") or 0))

    @route("POST", "/api/state_heartbeat/sandbox/rehearse")
    def state_heartbeat_rehearse(request: Any) -> Any:
        """Both rounds of the appending mechanism, from empty, in one go.

        More states than the window holds, then a few more - which is the
        only way to see both halves at once: what the cap drops, and what
        appending does with what arrives after it.
        """
        return app.state_heartbeat.rehearse_append(
            first=max(0, min(5000, int(request.get("first") or 0))),
            then=max(1, min(1000, int(request.get("then") or 40))),
            after=max(1, min(1000, int(request.get("after") or 12))),
            days=max(1, min(365, int(request.get("days") or 30))),
            seed=int(request.get("seed") or 0))

    @route("POST", "/api/state_heartbeat/sandbox/clear")
    def state_heartbeat_clear_sandbox(_request: Any) -> Any:
        return app.state_heartbeat.clear_sandbox()

    @route("GET", "/api/state_heartbeat/task_history")
    def state_task_history(request: Any) -> Any:
        """A task's earlier versions and the tasks it came from."""
        said = app.state_heartbeat.task_history(str(request.require("id")))
        if said is None:
            raise ApiError("task not found", 404)
        return said

    @route("GET", "/api/state_heartbeat/sandbox")
    def state_heartbeat_sandbox(_request: Any) -> Any:
        """Where the sandbox is, and what is in it."""
        return app.state_heartbeat.sandbox_status()

    @route("GET", "/api/state_heartbeat/progress")
    def state_heartbeat_progress(_request: Any) -> Any:
        """What the round has filled in so far. Read-only, and safe while
        one is in flight."""
        return app.state_heartbeat.progress()

    # -------------------------------------------------------------- listener
    @route("GET", "/api/listener")
    def listener(_request: Any) -> Any:
        return app.listener.health()

    @route("GET", "/api/events")
    def events(request: Any) -> Any:
        rows = app.raw_store.recent_events(
            limit=request.int_param("limit", 100),
            application=request.get("application"),
            event_type=request.get("event_type"),
        )
        return {"events": [e.to_dict() for e in rows]}

    @route("GET", "/api/edits")
    def edits(request: Any) -> Any:
        rows = app.raw_store.recent_edits(limit=request.int_param("limit", 100),
                                          task_id=request.get("task_id"))
        return {"edits": [e.to_dict() for e in rows]}

    @route("POST", "/api/listener/ingest")
    def ingest(request: Any) -> Any:
        """External integrations push normalized events here (sections 24, 64)."""
        try:
            session = app.auth.authenticate(request.token, permission="ingest_events")
        except AuthenticationError as exc:
            raise ApiError(str(exc), 401) from exc
        provider_id = str(request.get("provider", "app_integration"))
        provider = None
        for candidate in app.listener._providers.values():  # noqa: SLF001 - manager lookup
            if candidate.provider_id == provider_id:
                provider = candidate
                break
        if provider is None or not hasattr(provider, "accept"):
            raise ApiError(f"unknown ingest provider {provider_id}", 404)
        payloads = request.get("events") or [request.body]
        accepted = []
        for payload in payloads:
            event = provider.accept(payload, agent_id=session.application_id)
            accepted.append(event.id)
        return {"accepted": accepted}

    # --------------------------------------------------------- notifications
    @route("GET", "/api/notifications")
    def notifications(request: Any) -> Any:
        return {
            "notifications": app.notifications.recent(
                limit=request.int_param("limit", 50),
                unread_only=str(request.get("unread", "false")).lower() == "true",
            ),
            # How long "Remind me in ..." waits, so the button can say it.
            "remind_later_minutes": app.REMIND_LATER_MINUTES,
        }

    @route("GET", "/api/debugger/languages")
    def debugger_languages(_request: Any) -> Any:
        """The Language debugger: each model output, and the language it is
        told to write in - English, the display language, mixed, or not said
        (common/output_languages.py)."""
        from ..common.languages import display_language, name_of
        from ..common.output_languages import described
        code = display_language(app.config)
        return described(f"{name_of(code)} ({code})", db=app.db, days=7)

    @route("GET", "/api/reading")
    def reading(_request: Any) -> Any:
        """What the user read, as the log keeps it: its pieces and topics."""
        return app.reading.describe()

    @route("POST", "/api/reading/run")
    def reading_run(_request: Any) -> Any:
        """Put what was read since the last time in the log, now."""
        from ..common.observability import started
        begun = app.reading.start()
        if begun:
            # The log made with the button: its calls the user's while it runs.
            started.while_running(("reading",), app.reading.running)
        return {"started": begun}

    @route("POST", "/api/lessons/learn")
    def lessons_learn(_request: Any) -> Any:
        """Learn now: what is due, without waiting for the state heartbeat's
        next round."""
        from ..common.observability import started
        due = app.lessons.due()
        app.lessons.learn_now()
        # Learned with the button: its calls the user's while it learns.
        started.while_running(("lessons_",), app.lessons.learning_now)
        return {"started": True, "due": due}

    @route("POST", "/api/lessons/rule")
    def lessons_rule(request: Any) -> Any:
        """The user's say over one learned lesson: confirm it (a lesson now),
        reject it (never again), pin it (never weakened by itself), unpin
        it, or edit its words."""
        try:
            said = app.lessons.set_state(str(request.require("id")),
                                         str(request.require("op")),
                                         str(request.get("statement") or ""))
        except ValueError as exc:
            raise ApiError(str(exc), 400) from exc
        if said is None:
            raise ApiError("lesson not found", 404)
        return said

    @route("POST", "/api/lessons/delete")
    def lessons_delete(request: Any) -> Any:
        """Delete one lesson; it is no longer given to the agent."""
        kind = str(request.get("kind") or "notifications")
        if kind == "connections":
            if not app.connections.forget_action(str(request.require("id"))):
                raise ApiError("lesson not found", 404)
            return {"deleted": True}
        if kind != "notifications":
            raise ApiError("unknown kind of lesson")
        if not app.calendar_nature.forget(str(request.require("id"))):
            raise ApiError("lesson not found", 404)
        return {"deleted": True}

    # ------------------------------------------------------ connections
    # THE USER'S CONNECTIONS (heartbeat/connections/): the people around
    # them, grouped in the Connections panel; each change the user makes is
    # one of their connection lessons, with its reason if they gave one.
    @route("GET", "/api/connections")
    def connections_list(_request: Any) -> Any:
        from ..heartbeat.connections.store import CLOSENESS, KEEP_ACTIONS
        store = app.connections
        people = store.people()
        counts = {row["person_id"]: row["n"] for row in app.db.query(
            "SELECT person_id, COUNT(*) AS n FROM connection_accounts GROUP BY person_id")}
        for person in people:
            person["accounts_count"] = counts.get(person["id"], 0)
        summary = store.lessons_summary()
        return {"people": people, "me": store.me(), "inbox": store.inbox("open"),
                # The spellings in use, for the boxes to suggest as one types.
                "labels": [one["name"] for one in store.names_in_use("label")],
                "orgs": [one["name"] for one in store.names_in_use("org")],
                "inbox_decided": store.inbox("decided", 20),
                # The usual relationships, then any other in use.
                "relationships": store.relationships_in_use(),
                "closeness": {str(k): v for k, v in CLOSENESS.items()},
                "sources": store.sources(), "briefing": store.briefing(),
                "progress": dict(app.connections_round.progress),
                "deleted": len(store.people(include_deleted=True)) - len(people),
                "lessons": {"kept": len(store.actions("user", KEEP_ACTIONS)),
                            "most": KEEP_ACTIONS, "summarised": int(summary.get("covers") or 0)}}

    @route("GET", "/api/connections/briefing")
    def connections_briefing(request: Any) -> Any:
        """What the Overview card needs, and polls: the last "what you need
        to know" - or the issue asked for by its id - every issue kept, and
        how the working-out of the next is going."""
        from ..heartbeat.connections.recommend import says_of
        briefing = app.connections.briefing(str(request.get("id") or ""))
        return {"briefing": briefing, "issues": app.connections.briefings(),
                # The user's preference on each story, as last said.
                "says": says_of(app.db, (briefing or {}).get("id") or ""),
                "progress": dict(app.connections_round.progress),
                # When the next issue is made, and in which time zone.
                "schedule": app.issue_clock.status()}

    @route("POST", "/api/recommend/feedback")
    def recommend_feedback(request: Any) -> Any:
        """The user's say on a story: more like this, not interested, already
        knew it - or that they opened it. For its topic, next time; and what
        they knew already is kept as known."""
        from ..heartbeat.connections.recommend import record_feedback
        try:
            return record_feedback(app.db, app.connections, str(request.require("issue")),
                                   str(request.require("story")),
                                   str(request.require("action")),
                                   embedder=app.connections_round.embedder)
        except (KeyError, ValueError) as exc:
            raise ApiError(str(exc), 400)

    @route("GET", "/api/connections/person")
    def connections_person(request: Any) -> Any:
        person = app.connections.get(str(request.require("id")), with_details=True)
        if person is None:
            raise ApiError("no such person", 404)
        return person

    @route("GET", "/api/connections/merge_preview")
    def connections_merge_preview(request: Any) -> Any:
        """What merging `other` into `id` would bring over - shown before the
        user confirms it."""
        try:
            return app.connections.merge_preview(str(request.require("id")),
                                                 str(request.require("other")))
        except KeyError as exc:
            raise ApiError(str(exc).strip("'"), 404)

    @route("GET", "/api/connections/briefing/picture")
    def connections_briefing_picture(request: Any) -> Any:
        """A story's picture, kept here when the story was written."""
        path = app.connections.briefing_picture_path(str(request.require("name")))
        if path is None:
            raise ApiError("no picture", 404)
        kinds = {".png": "image/png", ".jpg": "image/jpeg", ".gif": "image/gif",
                 ".webp": "image/webp"}
        return Raw(path.read_bytes(), kinds.get(path.suffix.lower(), "image/png"))

    @route("GET", "/api/connections/avatar")
    def connections_avatar(request: Any) -> Any:
        """A person's picture, as the image it is."""
        store = app.connections
        path = store.avatar_path(store.get(str(request.require("id"))))
        if path is None:
            raise ApiError("no picture", 404)
        kinds = {".png": "image/png", ".jpg": "image/jpeg", ".gif": "image/gif",
                 ".webp": "image/webp"}
        return Raw(path.read_bytes(), kinds.get(path.suffix.lower(), "image/png"))

    @route("POST", "/api/connections/avatar")
    def connections_avatar_fetch(request: Any) -> Any:
        """Look for a person's picture now, on their public profiles."""
        from ..heartbeat.connections.avatars import fetch_avatar
        said = fetch_avatar(app.connections, str(request.require("id")))
        return dict(said, person=app.connections.get(str(request.require("id"))))

    @route("GET", "/api/connections/lessons")
    def connections_lessons(_request: Any) -> Any:
        store = app.connections
        return {"actions": store.actions("user"), "summary": store.lessons_summary(),
                "deleted": [one for one in store.people(include_deleted=True)
                            if one["deleted"]]}

    @route("POST", "/api/connections/person")
    def connections_change(request: Any) -> Any:
        """add {name, fields...}, edit {id, fields...}, delete {id},
        restore {id}, merge {id, other} - each with an optional reason, and
        each one of the user's connection lessons."""
        store = app.connections
        action = str(request.get("action") or "")
        reason = str(request.get("reason") or "")
        fields = {key: request.get(key) for key in
                  ("relationship", "relationships", "closeness", "org", "role", "affiliations",
                   "profession", "professions", "location", "locations", "summary", "tags",
                   "aliases")
                  if request.get(key) is not None}
        try:
            if action == "add":
                person = store.add(str(request.require("name")), by="user", reason=reason,
                                   **fields)
                # With as many profile links as were given.
                for link in request.get("profiles") or []:
                    link = link if isinstance(link, dict) else {"url": str(link)}
                    if str(link.get("url") or link.get("handle") or "").strip():
                        store.add_profile(person["id"], str(link.get("url") or "").strip(),
                                          str(link.get("platform") or link.get("label") or ""),
                                          str(link.get("handle") or ""), by="user")
                return store.get(person["id"], with_details=True)
            ident = str(request.require("id"))
            if action == "edit":
                if request.get("name"):
                    fields["name"] = request.get("name")
                return store.edit(ident, by="user", reason=reason, **fields)
            if action == "delete":
                return store.delete(ident, by="user", reason=reason)
            if action == "restore":
                return store.restore(ident, by="user", reason=reason)
            if action == "merge":
                return store.merge(ident, str(request.require("other")), by="user",
                                   reason=reason)
            if action == "add_profile":
                return store.add_profile(ident, str(request.get("url") or "").strip(),
                                         str(request.get("platform") or request.get("label")
                                             or ""),
                                         str(request.get("handle") or ""), by="user",
                                         reason=reason)
            if action == "remove_profile":
                return store.remove_profile(ident, str(request.require("account")),
                                            by="user", reason=reason)
            if action == "move_account":
                # `id` is the person it goes to; `account`, the account.
                return store.move_account(str(request.require("account")), ident,
                                          by="user", reason=reason)
        except KeyError as exc:
            raise ApiError(str(exc).strip("'"), 404)
        except ValueError as exc:
            raise ApiError(str(exc), 400)
        raise ApiError("action is add, edit, delete, restore, merge, move_account, "
                       "add_profile or remove_profile", 400)

    @route("POST", "/api/connections/source")
    def connections_source(request: Any) -> Any:
        """One of the user's own public profiles, added - or taken off again
        (`/api/connections/person` add_profile / remove_profile, on them)."""
        store = app.connections
        ident = str(request.get("person_id") or "") or store.me()["id"]
        try:
            if str(request.get("action") or "add") == "remove":
                return store.remove_profile(ident, str(request.require("id")),
                                            reason=str(request.get("reason") or ""))
            return store.add_profile(ident, str(request.require("url")).strip(),
                                     str(request.get("label") or request.get("platform") or ""),
                                     reason=str(request.get("reason") or ""))
        except KeyError as exc:
            raise ApiError(str(exc).strip("'"), 404)
        except ValueError as exc:
            raise ApiError(str(exc), 400)

    @route("POST", "/api/connections/inbox")
    def connections_inbox(request: Any) -> Any:
        """The user's answer to an inbox item - a change to their own profile
        the agent found on a public page: accept (it is made) or dismiss."""
        try:
            return app.connections.decide(str(request.require("id")),
                                          bool(request.get("accept")),
                                          str(request.get("reason") or "").strip())
        except KeyError as exc:
            raise ApiError(str(exc).strip("'"), 404)
        except ValueError as exc:
            raise ApiError(str(exc), 409)

    @route("POST", "/api/connections/run")
    def connections_run(request: Any) -> Any:
        """What the user needs to know, worked out now rather than with the
        next state heartbeat round - `me`: the user looked up too, however
        lately they were."""
        from ..common.observability.started import while_running
        started = app.connections_round.start(look_me_up=bool(request.get("me")))
        if started:
            # An issue made with the button: its calls the user's while it is made.
            while_running(("connections", "reading"), app.connections_round.running)
        return {"started": started, "progress": dict(app.connections_round.progress)}

    # ------------------------------------------------------ testing browser
    # BROWSER TESTING, in Settings (runtime/browser.py): Google's Chrome for
    # Testing headless shell, downloaded only when the user asks.
    @route("GET", "/api/browser")
    def browser_state(_request: Any) -> Any:
        return app.testing_browser.status()

    @route("POST", "/api/browser/install")
    def browser_install(_request: Any) -> Any:
        return app.testing_browser.start()

    @route("POST", "/api/browser/cancel")
    def browser_cancel(_request: Any) -> Any:
        return app.testing_browser.cancel()

    @route("POST", "/api/browser/remove")
    def browser_remove(_request: Any) -> Any:
        try:
            return app.testing_browser.remove()
        except RuntimeError as exc:
            raise ApiError(str(exc), 409)

    # ------------------------------------------------------ lessons package
    # PACKAGE ALL LESSONS / LOAD LESSONS, on the Overview
    # (heartbeat/lessons_package.py): one at a time, in the background.
    @route("GET", "/api/lessons/package")
    def lessons_package_state(_request: Any) -> Any:
        """The package folder, the last packaging's stats and the ones
        before, what is under way, and the last load's report."""
        return app.lessons_package.describe()

    @route("POST", "/api/lessons/package")
    def lessons_package_start(_request: Any) -> Any:
        if not app.lessons_package.start_package():
            raise ApiError("lessons are already being packaged or loaded", 409)
        return app.lessons_package.describe()

    @route("POST", "/api/lessons/package/cancel")
    def lessons_package_cancel(_request: Any) -> Any:
        return {"cancelled": app.lessons_package.cancel()}

    @route("POST", "/api/lessons/package/open")
    def lessons_package_open(_request: Any) -> Any:
        """OPEN PACKAGE FOLDER, in the machine's own file manager - made
        first if no packaging has made it yet."""
        from ..agent.work.runner import open_in_file_manager
        folder = app.lessons_package.folder
        folder.mkdir(parents=True, exist_ok=True)
        open_in_file_manager(folder)
        return {"opened": str(folder)}

    @route("POST", "/api/lessons/load")
    def lessons_load(request: Any) -> Any:
        """LOAD LESSONS from a package: the path given, or one chosen - its
        zip ("pick": "zip", the default) or its folder ("pick": "folder")."""
        from pathlib import Path as _Path
        path = str(request.get("path") or "").strip()
        if not path:
            from ..common import chooser
            if str(request.get("pick") or "zip") == "folder":
                chosen = chooser.choose_directory(title="Choose a lessons package folder")
            else:
                chosen = chooser.choose_file(
                    title="Choose a lessons package",
                    filetypes=[("Lessons package", "*.zip"), ("All files", "*")])
            if not chosen.get("selected"):
                return {"started": False, "chooser": chosen}
            path = str(chosen["selected"])
        if not _Path(path).exists():
            raise ApiError(f"no such file or folder: {path}", 404)
        if not app.lessons_package.start_load(path):
            raise ApiError("lessons are already being packaged or loaded", 409)
        return {"started": True, "path": path, **app.lessons_package.describe()}

    @route("POST", "/api/notifications/later")
    def notifications_later(request: Any) -> Any:
        """Remind me in 30 minutes: the same reminder again then."""
        task = app.remind_later(str(request.require("id")))
        if task is None:
            raise ApiError("notification not found", 404)
        return {"ok": True, "task": task.to_dict()}

    @route("POST", "/api/notifications/todo")
    def notifications_todo(request: Any) -> Any:
        """Add to to-do: no more notifications about it, and on the list."""
        todo = app.notification_to_todo(str(request.require("id")))
        if todo is None:
            raise ApiError("notification not found", 404)
        return {"ok": True, "todo": todo}

    @route("GET", "/api/todos")
    def todos(_request: Any) -> Any:
        """The to-do list the Overview shows beside the progress bars."""
        return {"todos": app.todos.shown()}

    @route("POST", "/api/todos/tick")
    def todos_tick(request: Any) -> Any:
        done = request.get("done")
        todo = app.todos.tick(str(request.require("id")),
                              done=True if done is None else bool(done), by="user")
        if todo is None:
            raise ApiError("no such to-do", 404)
        return {"ok": True, "todo": todo}

    @route("POST", "/api/todos/delete")
    def todos_delete(request: Any) -> Any:
        if not app.todos.delete(str(request.require("id"))):
            raise ApiError("no such to-do", 404)
        return {"ok": True}

    @route("POST", "/api/notifications/bad")
    def notifications_bad(request: Any) -> Any:
        """The user marked a notification as a bad one: a lesson."""
        lesson = app.bad_notification(str(request.require("id")),
                                      said=str(request.get("why") or ""))
        if lesson is None:
            raise ApiError("notification not found", 404)
        return {"ok": True, "lesson": lesson}

    @route("POST", "/api/notifications/read")
    def notifications_read(request: Any) -> Any:
        if request.get("all"):
            app.notifications.mark_all_read()
        else:
            app.notifications.mark_read(str(request.require("id")))
        return {"ok": True}

    # -------------------------------------------------------------- debugger
    @route("GET", "/api/debugger/tokens_by_day")
    def debugger_tokens_by_day(request: Any) -> Any:
        """Tokens spent per day and kind of work, over the last ?days= days,
        cut at the reader's midnight (?east= minutes east of UTC)."""
        return app.model_calls.by_day(days=request.int_param("days", 30),
                                      minutes_east=request.int_param("east", 0))

    @route("GET", "/api/debugger")
    def debugger(_request: Any) -> Any:
        snapshot = app.observability.snapshot()
        # Section 105 - the release version is visible in the Debugger.
        snapshot["application"] = {
            "name": APP_NAME,
            "version": __version__,
            "packaged": is_frozen(),
            "paths": app.paths.describe(),
        }
        snapshot["agent_metrics"].update(
            {
                "heartbeat_executions": app.observability.get_state("heartbeat.last"),
                "scheduler": app.scheduler.status(),
                "listener_provider_state": app.listener.health()["providers"],
                "library": app.library.stats(),
            }
        )
        snapshot["models"] = app.models.describe()
        snapshot["runtime"] = app.runtime.describe()
        return snapshot

    @route("GET", "/api/bus")
    def bus(request: Any) -> Any:
        return {"events": app.bus.recent(limit=request.int_param("limit", 50))}

    # ------------------------------------------------- system debugger (§14)
    def _window(request: Any):
        """Live vs. historical mode (section 14.32.10)."""
        minutes = request.get("minutes")
        return app.observability_service.window(int(minutes)) if minutes else None

    @route("GET", "/api/debugger/modules")
    def debugger_modules(request: Any) -> Any:
        """Section 14.33: one panel per module - what it is, and what is in it."""
        from ..debugger.modules import has_detail

        service = app.observability_service
        components = service.registry.components()
        wanted = str(request.get("type") or "")
        modules = []
        for component in components:
            described = component.to_dict() if hasattr(component, "to_dict") else dict(component)
            if described.get("component_type") == "subsystem":
                continue          # a grouping, not a module with contents
            if wanted and described.get("component_type") != wanted:
                continue
            modules.append({
                "id": described.get("id"),
                "name": described.get("name"),
                "description": described.get("description") or "",
                "component_type": described.get("component_type"),
                "parent": described.get("parent_component"),
                "status": _safe_call(component, "status_provider"),
                "metrics": _safe_call(component, "metrics_provider"),
                "has_detail": has_detail(str(described.get("id"))),
            })
        modules.sort(key=lambda m: (not m["has_detail"], m["name"] or ""))
        return {"monitoring": service.monitoring_enabled, "modules": modules}

    def _safe_call(component: Any, attribute: str) -> Any:
        provider = getattr(component, attribute, None)
        if not callable(provider):
            return {}
        try:
            return provider() or {}
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    @route("GET", "/api/debugger/module")
    def debugger_module(request: Any) -> Any:
        """What one module actually holds (section 14.33)."""
        from ..debugger.modules import detail_for

        module_id = str(request.require("id"))
        options = {key: request.get(key) for key in
                   ("event_type", "source", "type", "status", "limit")
                   if request.get(key)}
        return {"id": module_id,
                "monitoring": app.observability_service.monitoring_enabled,
                "detail": detail_for(app, module_id, options)}

    @route("POST", "/api/debugger/monitoring")
    def debugger_monitoring(request: Any) -> Any:
        """The master switch (section 14.33).

        Written to settings as well as applied, so it survives a restart -
        a switch that silently turns itself back on is worse than none.
        """
        enabled = bool(request.require("enabled"))
        app.config.set("debugger_enabled", enabled)
        app.observability_service.set_monitoring(enabled)
        return {"monitoring": app.observability_service.monitoring_enabled}

    @route("GET", "/api/debugger/topology")
    def debugger_topology(_request: Any) -> Any:
        """Section 14.16 - generated from the runtime component registry."""
        return app.observability_service.topology()

    @route("GET", "/api/debugger/component")
    def debugger_component(request: Any) -> Any:
        detail = app.observability_service.component_detail(
            str(request.require("id")), since=_window(request)
        )
        if detail is None:
            raise ApiError("unknown component", 404)
        return detail

    @route("GET", "/api/debugger/connection")
    def debugger_connection(request: Any) -> Any:
        detail = app.observability_service.connection_detail(
            str(request.require("id")), since=_window(request)
        )
        if detail is None:
            raise ApiError("unknown connection", 404)
        return detail

    @route("GET", "/api/debugger/timeline")
    def debugger_timeline(request: Any) -> Any:
        """Section 14.31.5 / 14.31.6 - the trace timeline and its filters."""
        filters: Dict[str, Any] = {}
        for key in ("task_id", "trace_id", "component", "connection_id", "severity",
                    "event_type"):
            value = request.get(key)
            if value:
                filters[key] = value
        since = _window(request)
        if since:
            filters["since"] = since
        return {"traces": app.observability_service.timeline(
            limit=request.int_param("limit", 40), **filters)}

    @route("GET", "/api/debugger/trace")
    def debugger_trace(request: Any) -> Any:
        return app.observability_service.trace_detail(str(request.require("id")))

    @route("GET", "/api/debugger/tests")
    def debugger_tests(request: Any) -> Any:
        service = app.observability_service
        component_id = request.get("component")
        connection_id = request.get("connection")
        if component_id:
            tests = service.diagnostics.for_component(str(component_id))
        elif connection_id:
            tests = service.diagnostics.for_connection(str(connection_id))
        else:
            tests = service.diagnostics.all()
        return {"tests": [t.to_dict() for t in tests],
                "grouped": service.diagnostics.grouped(),
                "history": service.diagnostics.history(limit=25)}

    @route("POST", "/api/debugger/tests/run")
    def debugger_run_test(request: Any) -> Any:
        """Section 14.32.7 - runs inside the normal permission boundaries."""
        result = app.observability_service.diagnostics.run(str(request.require("test_id")))
        return result.to_dict()

    # ------------------------------------------------------- chat debugger
    # The chat, run with every step written down: scripted scenarios on a
    # throwaway application, or a prompt of the user's own on this one. Made
    # the first time its tab asks, and - like every route under this prefix -
    # not served at all by a build without the Debugger, which never imports
    # it.
    import threading as _threading

    chat_debugger_made = _threading.Lock()

    def _chat_debugger() -> Any:
        from ..debugger.chat import ChatDebugger

        with chat_debugger_made:
            if getattr(app, "chat_debugger", None) is None:
                app.chat_debugger = ChatDebugger(app)
            return app.chat_debugger

    # ---------------------------------------------------- resource debugger
    # What the application costs the computer, as it goes: its processes'
    # processors, memory and graphics, by what each is for, and its folders
    # on disk (debugger/resources.py). Sampled only while its tab asks.
    resources_made = _threading.Lock()

    def _resources() -> Any:
        from ..debugger.resources import ResourceWatch

        with resources_made:
            if getattr(app, "resource_watch", None) is None:
                app.resource_watch = ResourceWatch(app)
            return app.resource_watch

    @route("GET", "/api/debugger/resources")
    def resource_debugger_now(_request: Any) -> Any:
        return _resources().now()

    @route("POST", "/api/debugger/resources/disk")
    def resource_debugger_disk(_request: Any) -> Any:
        """The folders measured again, behind the page."""
        return _resources().measure_disk()

    @route("POST", "/api/debugger/resources/leftovers")
    def resource_debugger_leftovers(_request: Any) -> Any:
        """The processes an earlier run of the application left, ended -
        each with what it started."""
        return {"ended": app.end_leftovers()}

    @route("GET", "/api/debugger/chat")
    def chat_debugger_runs(_request: Any) -> Any:
        return _chat_debugger().snapshot()

    @route("GET", "/api/debugger/chat/run")
    def chat_debugger_run_detail(request: Any) -> Any:
        try:
            return _chat_debugger().detail(str(request.require("id")))
        except KeyError:
            raise ApiError("there is no such chat debugger run", 404)

    @route("POST", "/api/debugger/chat/run")
    def chat_debugger_run(request: Any) -> Any:
        from ..debugger.chat import ChatDebuggerBusy

        message = request.get("message")
        test_id = request.get("test_id")
        conversation_id = request.get("conversation_id")
        if message is not None:
            if not isinstance(message, str) or not message.strip():
                raise ApiError("the prompt is empty")
            if test_id:
                raise ApiError("run either a test or a prompt, not both")
        elif not isinstance(test_id, str) or not test_id:
            raise ApiError("name a test to run, or write a prompt")
        if conversation_id is not None and not isinstance(conversation_id, str):
            raise ApiError("conversation_id must be text")
        try:
            return _chat_debugger().run(test_id=test_id, message=message,
                                        conversation_id=conversation_id or None)
        except KeyError:
            raise ApiError("there is no such chat test", 404)
        except ChatDebuggerBusy as exc:
            raise ApiError(str(exc), 409)

    @route("GET", "/api/debugger/search")
    def debugger_search(request: Any) -> Any:
        return app.observability_service.search(str(request.get("q", "")),
                                                limit=request.int_param("limit", 30))

    @route("POST", "/api/debugger/export")
    def debugger_export(request: Any) -> Any:
        """Section 14.27 - redacted, and written inside the workspace only."""
        service = app.observability_service
        report = service.diagnostic_report(
            trace_ids=request.get("trace_ids"),
            platform=app.platform.describe(),
        )
        from ..common.events.models import new_id

        name = f"diagnostic-report-{new_id('rep')}.json"
        path = app.runtime.write_text(
            # Section 32, as amended: a produced artifact, so it lands where
            # produced artifacts land. It still goes through the validated
            # write, so the route cannot be talked into writing anywhere else.
            app.policy.agent_subdir("exports") / name,
            __import__("json").dumps(report, indent=2, default=str),
        )
        return {"written": str(path), "bytes": path.stat().st_size,
                "workspace_only": True}

    # ------------------------------------------------------------ characters
    @route("GET", "/api/characters")
    def characters(request: Any) -> Any:
        """The avatars, each as the Avatar panel draws it; `?layers=1` with
        the pictures a live face is drawn on (large: asked for once)."""
        from ..agent.character.manager import DEFAULT_CHARACTER, avatar_name
        selected = str(app.config.get("character", DEFAULT_CHARACTER))
        layers = str(request.get("layers") or "") in ("1", "true")
        return {"characters": [app.characters.described(one["name"], layers=layers)
                               for one in app.characters.list_characters()],
                "selected": selected, "name": avatar_name(app.config, app.characters),
                "preview": app.characters.preview(selected)}

    @route("POST", "/api/characters/avatar")
    def create_avatar(request: Any) -> Any:
        """Set the active avatar from an uploaded or pixel-painted image."""
        package = app.characters.create_avatar(
            str(request.require("image")), pixel_art=bool(request.get("pixel_art", False))
        )
        app.config.set("character", package["name"])
        app.bus.publish("character.changed", {"name": package["name"]})
        return {"selected": package, "preview": app.characters.preview(package["name"])}

    # -------------------------------------------------------------- protocol
    @route("GET", "/api/protocol/identity")
    def identity(_request: Any) -> Any:
        if app.identity is None:
            raise ApiError("the agent protocol is unavailable (cryptography missing)", 503)
        return app.identity.public_identity()

    @route("POST", "/api/protocol/sign")
    def sign(request: Any) -> Any:
        """Prove possession of the private key to an external application."""
        if app.identity is None:
            raise ApiError("the agent protocol is unavailable", 503)
        return app.identity.sign_challenge(str(request.require("challenge")))

    @route("GET", "/api/protocol/applications")
    def applications(_request: Any) -> Any:
        return {"applications": app.app_registry.list(),
                "sessions": app.auth.describe()}

    @route("POST", "/api/protocol/register")
    def register_application(request: Any) -> Any:
        application_id = str(request.require("application_id"))
        public_key = str(request.require("public_key"))
        permissions = list(request.get("permissions") or ["ingest_events"])
        if not getattr(request, "authorized", True):
            # WITHOUT THE KEY: an application connecting itself. It may ask
            # for what such an application does, pushing events, and nothing more;
            # and it may not take over an application already registered -
            # replace its key, or undo the user's revoking it - since
            # anything on this machine can call this. It may say again what
            # it already is, which is what a reconnect does.
            from .access import EXTERNAL_PERMISSIONS

            if not set(permissions) <= EXTERNAL_PERMISSIONS:
                raise ApiError("an application may register itself only to push "
                               "events; anything more is granted from the "
                               "agent's own page", 403)
            existing = next((a for a in app.app_registry.list()
                             if a.get("application_id") == application_id), None)
            if existing is not None and (existing.get("revoked")
                                         or existing.get("public_key") != public_key):
                raise ApiError("that application is registered already; it can "
                               "be changed from the agent's own page", 403)
        return app.app_registry.register(
            application_id=application_id,
            public_key=public_key,
            permissions=permissions,
            display_name=str(request.get("display_name", "")),
        )

    @route("POST", "/api/protocol/revoke")
    def revoke_application(request: Any) -> Any:
        application_id = str(request.require("application_id"))
        revoked = app.app_registry.revoke(application_id)
        sessions = app.auth.revoke_application_sessions(application_id)
        return {"revoked": revoked, "sessions_terminated": sessions}

    @route("POST", "/api/protocol/permissions")
    def set_permissions(request: Any) -> Any:
        return app.app_registry.set_permissions(
            str(request.require("application_id")), list(request.require("permissions"))
        )

    @route("POST", "/api/protocol/challenge")
    def challenge(request: Any) -> Any:
        try:
            return app.auth.begin(str(request.require("application_id")))
        except AuthenticationError as exc:
            raise ApiError(str(exc), 401) from exc

    @route("POST", "/api/protocol/authenticate")
    def protocol_authenticate(request: Any) -> Any:
        try:
            return app.auth.complete(
                str(request.require("application_id")),
                str(request.require("challenge")),
                str(request.require("signature")),
            )
        except AuthenticationError as exc:
            raise ApiError(str(exc), 401) from exc

    return routes
