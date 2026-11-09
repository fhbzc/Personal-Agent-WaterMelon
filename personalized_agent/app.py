"""Application composition root.

Every module is constructed here and wired through interfaces: the event bus,
the stores, the model manager and the platform layer.  Nothing below this file
imports a sibling subsystem's internals.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from .agent.character.manager import CharacterManager
from .agent.core.agent_core import AgentCore
from .agent.dialog.store import ConversationStore
from .agent.overview.service import OverviewService
from .common.config.schema import SCHEMA_BY_KEY
from .heartbeat.states.heartbeat import SANDBOX_FIXTURES
from .common.config.store import ConfigStore
from .common.events.bus import EventBus
from .common.logging.setup import configure_logging
from .common.notifications import NotificationService
from .common.observability.metrics import Observability
from .common.observability.service import ObservabilityService
from .common.paths import AppPaths
from .data_manage.raw_edit.recorder import RawEditRecorder
from .data_manage.library import DataLibrary
from .data_manage.storage.database import Database
from .data_manage.storage.raw_store import RawStore
from .heartbeat.checklist.store import ChecklistStore
from .heartbeat.core.heartbeat import AgentHeartbeat
from .listener.core.manager import ListenerManager
from .models.credentials import CredentialManager
from .models.manager import ModelManager
from .platform_layer import get_platform
from .protocol.authentication.service import AuthenticationService
from .protocol.identity.keys import AgentIdentity, CryptographyUnavailable
from .protocol.permissions.registry import ApplicationRegistry
from .runtime.permissions import WorkspacePolicy
from .runtime.sandbox import SandboxRuntime
from .scheduler.core.completion import TaskCompleter
from .scheduler.core.scheduler import Scheduler
from .scheduler.execution.executors import TaskExecutor
from .scheduler.storage.store import SchedulerStore
from .build_flags import debugger_available
from .version import APP_NAME, __version__

log = logging.getLogger(__name__)


class AgentApplication:
    def __init__(self, data_dir: Optional[Path] = None,
                 root: Optional[Path] = None, *, configure_logs: bool = True,
                 use_keyring: Optional[bool] = None) -> None:
        """`configure_logs` and `use_keyring` are for an application built
        inside another one - the chat debugger's throwaway copy. Logging is
        process-wide, so a second application configuring it would take the
        running one's log away into a folder about to be deleted; and the
        keychain is the user's, shared with the real installation."""
        self._configures_logs = configure_logs
        # Section 110.1: one user-specified root holds both storage domains as
        # sibling subdirectories, and nothing outside it is written.
        self.paths = AppPaths.create(root if root is not None else data_dir)
        self.root = self.paths.root
        self.data_dir = self.paths.data_dir

        self.bus = EventBus()
        self.config = ConfigStore(self.data_dir, bus=self.bus, path=self.paths.settings_file)
        if configure_logs:
            configure_logging(self.paths.logs_dir, str(self.config.get("log_level", "INFO")))
        # Section 14.29: components publish through one Observability Service,
        # which owns metrics, traces, the system registry and diagnostics.
        self.observability_service = ObservabilityService(Observability())
        self.observability = self.observability_service.metrics
        self.traces = self.observability_service.traces
        self.ui_attached = False
        self.platform = get_platform()
        if sys.platform == "win32":
            # Programs seen standing still while their windows are read, kept
            # in the agent's own folder (platform_layer/window_text.py).
            from .platform_layer.window_text import reader as window_reader

            window_reader().remember_in(self.paths.cache_dir / "stands_still_when_read.json")
        log.info("%s %s starting", APP_NAME, __version__)
        log.info("platform: %s", self.platform.describe())
        log.info("agent root: %s", self.root)
        log.info("  workspace: %s", self.paths.workspace_dir)
        log.info("  internal : %s", self.data_dir)
        # Section 110.1: the root is copyable, so a root that moved has its
        # stored absolute paths rewritten instead of pointing at the old place.
        self.relocated_from = self._relocate_if_moved()
        self.config.set("agent_root", str(self.root), internal=True)
        self.config.set("workspace_root", str(self.paths.workspace_dir), internal=True)

        # -- storage --------------------------------------------------------
        # Asked before it is made: there being none yet is what a new install is.
        new_here = not self.paths.database_file.exists()
        self.db = Database(self.paths.database_file)
        # Section 14.35, as amended: what the agent spends is written down, so
        # that "is this getting more expensive" survives a restart. Attached
        # here because the metrics are built before the database is.
        from .common.observability.model_calls import ModelCalls

        self.model_calls = ModelCalls(self.db)
        self.observability.store = self.model_calls
        # Calls from before their cost was counted: counted, out of the way.
        threading.Thread(target=self.model_calls.fill_costs, name="fill-call-costs",
                         daemon=True).start()
        self.raw_store = RawStore(self.db)
        # Section 39.1, as amended: what the agent knows lives in five
        # folders of files, not in a graph in this database. The folders are
        # under the workspace so that the user can read and correct them.
        self.library = DataLibrary(self.paths.workspace_dir / "data")
        self.library.ensure()
        # Section 34.1, as amended: what is true over a stretch of time. Shown
        # on the calendar, used as the first half of any answer's context, and
        # never executed - the scheduler is what runs things.
        from .agent.states.store import StateStore

        self.states = StateStore(self.db, library=self.library)
        # Once: the horizon out of the files written before it was dropped.
        # A folder holding two kinds of state file, where only the old ones
        # carry a word the agent no longer uses, is worse than either.
        if not bool(self.config.get("states_horizon_dropped", False)):
            try:
                self.states.drop_horizons()
            except Exception:            # pragma: no cover - a tidy, not a load
                log.debug("could not rewrite the older state files",
                          exc_info=True)
            self.config.set("states_horizon_dropped", True, internal=True)
        # And the states written before a state carried a clock. A calendar
        # where two thirds of the entries can be placed in a week and the
        # rest cannot is worse than either: every question about the user's
        # Mondays would silently be a question about the last fortnight.
        if not bool(self.config.get("states_local_time_stamped", False)):
            try:
                self.states.stamp_local_times(
                    str(self.config.get("states_zone_before", "") or ""))
            except Exception:            # pragma: no cover - a tidy, not a load
                log.debug("could not stamp the older states with a local time",
                          exc_info=True)
            self.config.set("states_local_time_stamped", True, internal=True)
        # Once: the two numbers that changed meaning when the grouping
        # stopped being computed from nothing every hour.
        #
        # A stored setting outlives the default it was written from, so an
        # install made before this keeps four hundred states and a pile of
        # eight for ever - the values from when every round paid the square
        # of the window and finding out whether the leftovers were a task
        # meant rebuilding all of it. Neither is true any more.
        #
        # ONLY WHERE THE USER NEVER CHOSE. A stored value that matches the
        # old default was never a decision, it was the absence of one;
        # anything else is a decision and is left alone, which is why this
        # compares rather than overwrites.
        self._move_stale_defaults()
        self._forget_the_sandbox_settings()
        # How often states are grouped was renamed from seconds to hours,
        # and a rename carries what was chosen rather than dropping it.
        self._carry_the_state_interval()
        # NOTHING SEARCHES THE WHOLE LIBRARY. Every reader has its own window
        # - the agent heartbeat its last few minutes and the states running,
        # the state heartbeat its tasks' states - with anything in it read
        # whole by id; the chat answers from what it is asked. Raw activity
        # is kept for a while and then let go, by age.
        from .data_manage.storage.housekeeping import RawHousekeeping

        from .agent.work import mail_pages
        self.housekeeping = RawHousekeeping(
            self.config, self.raw_store,
            # The mail pages the user opened, kept longer, before the records go.
            before=lambda: mail_pages.keep(self.db))
        self.recorder = RawEditRecorder(self.raw_store, bus=self.bus,
                                        observability=self.observability)
        tracer = self.observability_service.tracer_for
        self.conversations = ConversationStore(self.db)

        # -- models and credentials ----------------------------------------
        # Section 110.1: credentials in the OS keychain do not travel with a
        # copied root, so the user can choose to keep them inside it instead.
        self.credentials = CredentialManager(
            self.paths.credentials_dir,
            use_keyring=(str(self.config.get("credential_storage")) == "keychain"
                         if use_keyring is None else use_keyring),
        )
        # The local API's key, beside the other credentials in the protected
        # state: what every request to /api/ must carry, and what the desktop
        # signs the browser in with (api/access.py). Made on first use.
        from .api.access import ApiAccess, key_file_for

        self.api_access = ApiAccess(key_file_for(self.paths.credentials_dir))
        self.models = ModelManager(self.config, self.credentials, bus=self.bus,
                                   observability=self.observability,
                                   tracer=tracer("model_provider"))
        self.notifications = NotificationService(self.db, self.config, bus=self.bus,
                                                 platform=self.platform)
        # A local client answers from inside the workspace, so nothing it might
        # do could reach outside the root (sections 76.1, 110.1).
        for provider_id in ("claude_code", "claude_desktop", "codex"):
            local = self.models.provider(provider_id)
            if local is not None:
                local.workspace = self.paths.workspace_dir

        # -- runtime --------------------------------------------------------
        self.policy = self._build_policy()
        self.runtime = SandboxRuntime(
            self.policy,
            platform=self.platform,
            observability=self.observability,
            bus=self.bus,
            sandbox_enabled=bool(self.config.get("sandbox_enabled", True)),
            default_timeout=float(self.config.get("script_timeout_seconds", 120)),
            tracer=tracer("runtime"),
        )

        # -- listener -------------------------------------------------------
        self.listener = ListenerManager(
            config=self.config, raw_store=self.raw_store, bus=self.bus,
            observability=self.observability, platform=self.platform,
            tracer=tracer("listener"),
        )

        # -- scheduler ------------------------------------------------------
        # Section 52.4: where the agent's questions about its own decisions go.
        from .agent.feedback.store import TaskQuestionStore

        # Kept for the answers already given, and for answering one still
        # standing. Nothing asks new ones: every event is on the calendar,
        # and a task that wants approval is approved there - so the store is
        # handed to nothing that would ask (no notification, no run, no hold).
        self.questions = TaskQuestionStore(self.db)
        self.scheduler_store = SchedulerStore(self.db)
        if self.relocated_from:
            self._relocate_task_paths(self.relocated_from)
        self.executor = TaskExecutor(
            runtime=self.runtime, models=self.models, notifications=self.notifications,
            store=self.scheduler_store,
            recorder=self.recorder, config=self.config, bus=self.bus,
        )
        self.scheduler = Scheduler(
            store=self.scheduler_store, executor=self.executor, config=self.config,
            bus=self.bus, observability=self.observability, recorder=self.recorder,
            tracer=tracer("scheduler"),
            notifications=self.notifications, states=self.states,
        )

        # -- agent ----------------------------------------------------------
        self.agent = AgentCore(
            config=self.config,
            runtime=self.runtime, scheduler=self.scheduler, notifications=self.notifications,
            models=self.models, recorder=self.recorder, conversations=self.conversations,
            bus=self.bus, observability=self.observability, tracer=tracer("agent_core"),
        )
        self.executor.agent_runner = self.agent.scheduled_runner
        # Section 52.4: answers become nodes, and release the tasks they held.
        from .agent.feedback.service import FeedbackService

        from .agent.feedback.todo_list import TodoList
        #: What the user took off a notification to do later (Overview, To do).
        self.todos = TodoList(self.db)
        from .heartbeat.states.item_links import ItemLinks
        self.item_links = ItemLinks(self.db)
        self.feedback = FeedbackService(
            questions=self.questions, library=self.library, scheduler=self.scheduler,
            recorder=self.recorder, bus=self.bus, todos=self.todos,
        )
        self.overview = OverviewService(self.raw_store, self.library, self.config)
        self.characters = CharacterManager(
            builtin_dir=self.paths.builtin_characters_dir,
            installed_dir=self.paths.characters_dir,
        )

        # -- heartbeats -----------------------------------------------------
        self.checklist = ChecklistStore(self.db)
        self.heartbeat = AgentHeartbeat(
            config=self.config, checklist=self.checklist,
            models=self.models, scheduler=self.scheduler, notifications=self.notifications,
            recorder=self.recorder, raw_store=self.raw_store, bus=self.bus,
            observability=self.observability, tracer=tracer("heartbeat"),
            states=self.states, library=self.library,
        )
        self.task_completer = TaskCompleter(
            scheduler=self.scheduler, models=self.models,
            config=self.config, bus=self.bus, recorder=self.recorder,
        )

        # Section 34.3: the second review, on its own clock. It writes no
        # states and reads no activity - it groups the states already
        # written and says what each group is about.
        from .heartbeat.states import StateHeartbeat

        self.state_heartbeat = StateHeartbeat(
            config=self.config, states=self.states, library=self.library,
            # For the vectors it keeps: a cache of what each state means,
            # recomputed whenever the state's words or the model change.
            db=self.db,
            models=self.models, recorder=self.recorder, bus=self.bus,
            observability=self.observability, tracer=tracer("state_heartbeat"),
        )
        # The agent round asks for a state round when it finishes, so it
        # needs a handle on one. Set here rather than passed in, because
        # the state heartbeat is built after the agent heartbeat and
        # neither owns the other - this is the one line that joins them.
        self.heartbeat.state_heartbeat = self.state_heartbeat
        # Notification lessons: what the user's own adds, disables, deletes
        # and "bad notification"s say about the notifications they want. Read
        # by the agent heartbeat when it schedules notifications, and by
        # nothing else.
        from .heartbeat.core.calendar_nature import CalendarNature
        from .heartbeat.lessons import LessonEngine
        from .common.languages import display_language, name_of

        # EVERY KIND OF LESSON, ONE SYSTEM (heartbeat/lessons/engine.py):
        # learned from evidence - the user's own edits - never from one
        # action alone; what the agent is given is what it has learned.
        self.lessons = LessonEngine(
            self.db, self.models, self.config,
            language=lambda: name_of(display_language(self.config)))
        self.heartbeat.lessons = self.lessons
        # WHAT WAS READ OF A PICTURE OR A DOCUMENT, kept as a copy in the
        # application's own folder - read once (runtime/documents.py).
        from .runtime import documents as _documents
        _documents.configure(Path(self.paths.data_dir) / "extractions",
                             hiding=lambda: bool(self.config.get("listener_hide_credentials",
                                                                 True)))
        # WHAT THE USER READ, kept after each agent heartbeat - for
        # Newspaper. Sites the listener keeps out stay out.
        from .heartbeat.reading import ReadingLog
        self.reading = ReadingLog(
            self.db, self.raw_store, self.models, self.config,
            embedder=getattr(self.state_heartbeat, "embedder", None),
            allowed=self.listener.privacy.website_allowed)
        self.heartbeat.reading = self.reading
        # WHAT IS WAITING FOR A TIME, given to the notification step; and the
        # rules that run that step at once, on one window, when the screen
        # calls for it (heartbeat/core/pending.py, triggers.py).
        from .heartbeat.core.pending import PendingStore
        from .heartbeat.core.triggers import Triggers
        self.pending = PendingStore(self.db)
        self.heartbeat.pending = self.pending
        # Nobody at the keyboard or mouse: nothing is triggered.
        activity = getattr(self.platform, "activity", None)
        self.triggers = Triggers(self.heartbeat, self.bus, self.raw_store, self.config,
                                 idle=getattr(activity, "seconds_since_input", None))
        # THE NATURES OF THE WORK, learned the same way; and every kind's
        # patterns and principles grouped as the natures' are.
        from .heartbeat.lessons.natures import NatureLessons
        from .heartbeat.lessons.groups import LessonPatternBuilder
        self.lessons.natures = NatureLessons(self.db, self.library, self.config)
        self.lessons.grouper = lambda kind: LessonPatternBuilder(
            self.lessons, self.db, self.library,
            getattr(getattr(self.state_heartbeat, "experience", None), "embedder", None),
            self.models, kind).build(budget=20)
        # Learned with each state heartbeat round, when something is new.
        self.state_heartbeat.lessons = self.lessons
        self.calendar_nature = CalendarNature(self.db, self.models, self.config,
                                              lessons=self.lessons)
        self.heartbeat.calendar_nature = self.calendar_nature
        # THE MONITOR (heartbeat/monitor/, the user, 2026-11-03): the real-time
        # signals, the Activators the model writes and code runs on them - the
        # two rules above among them, built in and protected - and what they
        # ask the model; the heartbeat's step_monitor judges what they queue.
        # The lessons are given by what a moment is like (the user: "做context
        # retrieval") - they are written in English, and read by the state
        # heartbeat's own embedding model; a window is placed by its words.
        from .heartbeat.monitor.decide import Decider
        from .heartbeat.monitor.engine import Monitor
        from .heartbeat.monitor.signals import Signals as MonitorSignals
        embedder = getattr(self.state_heartbeat, "embedder", None)
        from .heartbeat.lessons.retrieve import LessonRetriever
        self.lesson_retriever = LessonRetriever(self.lessons, embedder, self.models)
        self.calendar_nature.retriever = self.lesson_retriever
        self.state_heartbeat.retriever = self.lesson_retriever
        # THE CHAT'S search_lessons, by meaning (the user, 2026-11-07: search tools for the chat).
        self.agent.lesson_retriever = self.lesson_retriever
        self.state_heartbeat.item_links = self.item_links
        self.state_heartbeat.active_items = self._active_items
        # What the plan of a piece of work may read and do (heartbeat/states/task_plan.py).
        self.state_heartbeat.open_records = lambda ids: self.heartbeat._open_by_ids(ids)
        self.state_heartbeat.schedule_reminder = lambda args: self.agent.tools.call(
            "schedule_task", args)
        self.state_heartbeat.cancel_reminder = lambda ident, why: self.scheduler.cancel(
            ident, reason=why, by="the agent")
        self.state_heartbeat.close_notification = self.notifications.mark_read
        self.state_heartbeat.recent_stories = self._recent_stories
        self.heartbeat.item_links = self.item_links
        # The lessons made again, once, as examples and rules in English.
        self.lessons.rebuild_once()
        self.monitor_signals = MonitorSignals(
            self.db, self.config, states=self.states,
            idle=getattr(activity, "seconds_since_input", None))
        self.monitor_decider = Decider(self.db, self.config, self.models, self.monitor_signals,
                                       lessons=self.calendar_nature,
                                       experience=self._monitor_experience,
                                       tasks=self._monitor_tasks)
        self.monitor = Monitor(self.db, self.config, self.monitor_signals,
                               notifications=self.notifications, questions=self.questions,
                               decider=self.monitor_decider, heartbeat=self.heartbeat,
                               pictures=self.paths.data_dir / "notification_pictures")
        self.monitor_decider.item_links = self.item_links
        self.monitor_decider.today = self._monitor_today
        self.monitor.keeper = self._keep_recommended
        self.triggers.rules = lambda: [*self.monitor.text_rules(), self.reserve_rule]
        self.heartbeat.monitor_step = lambda: self.monitor_decider.step(self.monitor)
        from .heartbeat.monitor.reserve import Reserve
        self.reserve = Reserve(self.db, self.config)
        self.monitor_decider.reserve = self.reserve
        self.notifications.moment_now = self._moment_now
        self.lessons.moment_now = self._moment_now
        self.heartbeat.reserve_step = lambda asks=True: self.reserve.step(self.monitor_decider,
                                                                          asks=asks)
        from .heartbeat.monitor.match import ReserveRule
        self.reserve_rule = ReserveRule(self.reserve, self.monitor, self.monitor_decider,
                                        self.data_dir, self.platform.activity)
        self.bus.subscribe("feedback.answered", self.monitor.on_answered)
        # EVERY ANSWER LEARNED FROM (the user, 2026-11-03): a question's here;
        # a notification's own buttons where they are pressed (`learn_answer`).
        self.bus.subscribe("feedback.answered", self._question_answered)
        # THE USER'S CONNECTIONS (heartbeat/connections/): the people around
        # them - written by the agent heartbeat's step 2 from what the user
        # had in front of them, and by the user in the Connections panel -
        # and, with each state heartbeat round, what the user's current work
        # needs to know of them, for the Overview.
        from .heartbeat.connections import ConnectionsStore
        from .heartbeat.connections.round import ConnectionsRound

        # Their pictures in the protected state, beside nothing of the user's.
        self.connections = ConnectionsStore(self.db, self.paths.data_dir / "connections")
        self.connections.lessons = self.lessons
        self.connections.retriever = self.lesson_retriever
        self.connections_round = ConnectionsRound(self.connections, self.models, self.db)
        # Its contacts' part only while Connections is on (the user,
        # 2026-10-30): with it off, an issue is the news alone.
        from .common.modules import enabled as module_on
        self.connections_round.people_on = lambda: module_on(self.config, "connections")
        self.heartbeat.connections = self.connections
        self.state_heartbeat.connections_round = self.connections_round
        self.connections_round.state_heartbeat = self.state_heartbeat
        # CUSTOMIZED RECOMMENDATION: an issue recommends from what was read,
        # at the times of day the user chose - its own clock.
        self.connections_round.reading = self.reading
        self.connections_round.reserve = self.reserve
        self.connections_round.embedder = getattr(self.state_heartbeat, "embedder", None)
        self.connections_round.retriever = self.lesson_retriever
        from .heartbeat.connections.schedule import IssueClock
        self.issue_clock = IssueClock(self.config, self.connections_round, self.connections)
        self.state_heartbeat.pool.counts("newspaper", lambda: (
            ["an issue"] if self.connections_round.running() else []))
        # The old learning's clock: kept, never started (the learning
        # started again, 2026-10-21 - learning/).
        from .heartbeat.experience.auto import LearningClock
        self.learning_clock = LearningClock(self.config, self.state_heartbeat.experience)
        # LEARNING (learning/): the user's work - their Work projects and the
        # places they list in Work's Learning panel, with their coding agents'
        # records - read into cases and reasons, by updates at the times they
        # chose (when they switched it on) or when they ask. The local index
        # reads the same places.
        from .learning.service import Learning
        self.learning = Learning(self.db, self.config, self.models, self.paths.data_dir,
                                 never=[self.paths.data_dir, self.paths.workspace_dir])
        self.learning.places.work_tab_folders = self._work_tab_folders
        # The learning reads its reasons by meaning too, to find the same one learned twice.
        self.learning.embedder = getattr(self.state_heartbeat, "embedder", None)
        # THE LOCAL INDEX: the user's own files searchable by their words -
        # every Work project's folders and the folders the user adds - built
        # in a process of its own at the lowest priority (index/service.py),
        # at the start of every learning update: one switch and one clock.
        from .index.service import LocalIndex
        self.local_index = LocalIndex(self)
        self.learning.before_update = lambda: self.local_index.build_now(wait=True)
        # What the user endorses, looked for by the state heartbeat once a day
        # and added to the learning list (learning/interests.py).
        self.state_heartbeat.interests = self.learning.interests
        # PACKAGE ALL LESSONS and LOAD LESSONS (Overview): every lesson -
        # the experience, each work project's tree, the notification lessons,
        # the natures - packed into one folder in the workspace and a zip of
        # it, and a package from another computer merged in. Never reads the
        # protected state; writes only in the workspace.
        from .heartbeat.lessons_package import LessonsPackage
        self.lessons_package = LessonsPackage(
            self.db, self.library, self.models, self.config,
            self.paths.workspace_dir,
            protected=[*self.paths.protected_paths(), self.paths.credentials_dir],
            calendar_nature=self.calendar_nature)
        # The browser work runs test with - downloaded only when the user
        # asks, from Settings (runtime/browser.py). Looked for now, so its
        # folders are readable to the runs' code and the helper is current.
        from .runtime import browser as testing_browser
        self.testing_browser = testing_browser.Installer(self.paths.data_dir)
        try:
            testing_browser.installed(self.paths.data_dir)
        except Exception:                     # pragma: no cover - no browser, then
            log.warning("could not look for the testing browser", exc_info=True)
        # The agent working on a piece of the user's work - only when the user
        # starts it, and writing only in that work's own folder.
        from .agent.work.runner import WorkRuns
        self.work = WorkRuns(self.db, self.policy, self.models,
                             self.state_heartbeat.experience,
                             platform=self.platform,
                             # What the agents do is recorded as theirs.
                             raw_store=self.raw_store,
                             sandbox_enabled=bool(self.config.get(
                                 "sandbox_enabled", True)))
        # EACH RUN IN A PROCESS OF ITS OWN, within what the computer has free
        # (agent/work/child.py, governor.py).
        from .agent.work.governor import Governor
        self.work.paths = self.paths
        # Its settings: where the user's mail and calendar are (agent/work/handed.py).
        self.work.app_config = self.config
        self.work.in_subprocess = bool(self.config.get("work_in_subprocess", True))
        self.work.governor = Governor(self.work)
        # ONE POOL FOR EVERYTHING SIDE BY SIDE (common/pool.py): the state
        # heartbeat's, made with its room; the runs of work and an issue of
        # the Newspaper counted in it by asking them.
        self.pool = self.state_heartbeat.pool
        self.work.pool = self.pool
        # The user's own files, searched by their words: for the runs that may.
        self.work.local_index = self.local_index
        self.pool.counts("work", lambda: [
            run.id for run in list(self.work._running.values()) if run.alive()])
        # Work handed over, and every piece of work started: context, a plan
        # the user checks, then the run (agent/work/delegate.py).
        from .agent.work.delegate import Drafts
        self.drafts = Drafts(self)

        # SOCIETY (society/service.py): the agent among other people's
        # agents - its identity, its inbox, its drafts; only the user sends.
        from .society.service import SocietyService
        self.society = SocietyService(self)
        from .society.tools import add_tools as _society_tools
        _society_tools(self.agent.tools, self.society)
        # THE TO-DO LIST from the chat (agent/feedback/todo_tools.py): added
        # when the user asks; never taken off by the agent.
        from .agent.feedback.todo_tools import add_tools as _todo_tools
        _todo_tools(self.agent.tools, self.todos, self.item_links)
        # THE MONITOR FROM CHAT (heartbeat/monitor/tools.py): its signals, its
        # Activators - added, edited, deleted only when it must; never a protected one.
        from .heartbeat.monitor.tools import add_tools as _monitor_tools
        _monitor_tools(self.agent.tools, self.monitor,
                       lambda: getattr(self, "lesson_retriever", None))
        # WHAT WAS ON THE SCREEN, by time: asked for, never given with a message.
        from .agent.screen_history import add_tools as _screen_history_tools
        _screen_history_tools(self.agent.tools, self.db, self.config)
        # FILES AND PICTURES ATTACHED IN CHAT, kept in the agent's own folder.
        from .agent.chat_files import ChatFiles, add_tools as _chat_file_tools
        self.chat_files = ChatFiles(self.paths.data_dir / "chat_files", prepares=True)
        self.agent.chat_files = self.chat_files
        # A PICTURE OF THE SCREEN when the chat or Delegate work is opened, for the
        # model alone (agent/front_picture.py).
        from .agent.front_picture import FrontPictures
        from .agent.front_screen import front_now
        root = self.config.get("workspace_root", "")
        self.front_pictures = FrontPictures(
            self.config, getattr(self.platform, "activity", None),
            Path(root) / "data" / "references" / "front" if root else None,
            lambda: front_now(self.db, self.config))
        _chat_file_tools(self.agent.tools, self.chat_files)
        from .agent.two_speed import TwoSpeed
        self.two_speed = TwoSpeed(self.agent, self.conversations, self.agent.models, self.config,
                                  self.chat_files, self.db)
        self.chat_files.forget_unsent(self.conversations.attachments())
        # LESSONS FROM THE CHAT (heartbeat/lessons/tools.py): what the user says
        # to remember, kept like every other lesson.
        from .heartbeat.lessons.tools import add_tools as _lesson_tools
        _lesson_tools(self.agent.tools, self.lessons, self.lesson_retriever)
        # A NEWSPAPER SPECIAL ISSUE FROM CHAT (the user, 2026-11-03, from PR
        # #8): the brief agreed in the chat handed to the newspaper.
        self._add_special_issue_tool()
        self._add_assign_work_tool()
        # THE TWO LISTS IN GROUPS (agent/feedback/list_groups.py): grouped by
        # a model whenever an item comes or changes - Task organisation's
        # calls - the user's own moves told to it first.
        from .agent.feedback.list_groups import ListGroups
        from .common import modules as _modules
        self.list_groups = ListGroups(
            self.db, lambda: self.models,
            {"todo": lambda: [one for one in self.todos.shown() if not one.get("done_at")],
             "assigned": lambda: self.drafts.assigned(),
             "collection": lambda: self.collection_items()},
            allowed=lambda: _modules.enabled(self.config, "tasks"))
        self.list_groups.lessons = self.lessons
        self.list_groups.retriever = self.lesson_retriever
        self.list_groups.item_links = self.item_links
        from .agent.feedback.after_todo import AfterTodo
        self.after_todo = AfterTodo(self.db, lambda: self.models, self.scheduler, self.todos,
                                    allowed=lambda: _modules.enabled(self.config, "tasks"))

        # UPDATES from the project's published releases (platform_layer/
        # updater.py): never while a piece of work runs. `quit` is set by
        # whatever runs the agent - the desktop, or the headless loop.
        from .platform_layer.updater import Updater
        self.updater = Updater(self.config, self.data_dir,
                               notify=self.notifications.notify,
                               working=lambda: bool(self.work.going_now()),
                               away=self._seconds_away)
        if new_here:
            self.updater.installed_just_now()

        # -- protocol -------------------------------------------------------
        self.app_registry = ApplicationRegistry(self.db)
        self.auth = AuthenticationService(self.app_registry)
        try:
            self.identity: Optional[AgentIdentity] = AgentIdentity(self.credentials)
        except CryptographyUnavailable as exc:
            log.warning("agent protocol disabled: %s", exc)
            self.identity = None

        # Section 14.16: the Debugger topology comes from this registration -
        # in a build that carries it. A build made with --no-debugger (every
        # released download) has no such package, and importing it here
        # stopped the application before it started.
        if debugger_available():
            from .debugger import register_builtin_tests, register_system

            register_system(self)
            register_builtin_tests(self)
        # Section 14.33: the master switch is applied at startup, so a user who
        # turned monitoring off does not find it collecting again next launch.
        self.observability_service.set_monitoring(
            bool(self.config.get("debugger_enabled", True)))

        self.api = None
        self._started = False
        self._lock = threading.RLock()
        self.bus.subscribe("settings.changed", self._on_settings_changed)
        # Section 75.3: a stored sign-in is used without asking, and the user is
        # only interrupted when it has actually stopped working.
        self.reauthentication_required: Optional[Dict[str, Any]] = None
        #: Conditions the user has already been told about and dismissed. Being
        #: told twice about the same thing is nagging, not informing (75.3).
        self._dismissed_signatures: set = set()
        self.bus.subscribe("model.authentication_required", self._on_credential_lost)
        self.bus.subscribe("model.authentication_failed", self._on_credential_lost)
        self.bus.subscribe("model.available", self._on_credential_restored)

    # -- sign-in state (section 75.3) ---------------------------------------
    REAUTH_DEDUPE_KEY = "model.reauthentication_required"

    def _on_credential_lost(self, _topic: str, payload: Dict[str, Any]) -> None:
        """The stored sign-in stopped working, so ask for it once."""
        provider_id = str(payload.get("provider") or "")
        reason = str(payload.get("reason") or "the stored sign-in is no longer accepted")
        signature = f"{provider_id}:{payload.get('state')}"
        if signature in self._dismissed_signatures:
            return                                 # already said, already dismissed
        self.reauthentication_required = {
            "signature": signature,
            "provider": provider_id,
            "role": payload.get("role"),
            "reason": reason,
            "state": payload.get("state"),
        }
        provider = self.models.provider(provider_id)
        name = provider.display_name if provider is not None else provider_id
        self.notifications.notify(
            f"Sign in to {name} again",
            f"The agent could not use the stored {name} sign-in. Reason: {reason}",
            kind="warning",
            dedupe_key=self.REAUTH_DEDUPE_KEY,
            signature=f"{provider_id}:{payload.get('state')}",
            action_hint="Open Settings > Models and sign in again.",
            metadata={"provider": provider_id},
        )

    def dismiss_reauthentication(self) -> Dict[str, Any]:
        """Put the prompt away (section 75.3).

        Dismissing does not fix anything, and the model stays unusable - the
        state is still visible where state belongs. What it stops is being told
        the same thing again, which the user has now read.
        """
        request = self.reauthentication_required
        if request is not None:
            signature = request.get("signature")
            if signature:
                self._dismissed_signatures.add(signature)
            self.notifications.clear_dedupe(self.REAUTH_DEDUPE_KEY)
        self.reauthentication_required = None
        return {"dismissed": bool(request)}

    def _on_credential_restored(self, _topic: str, payload: Dict[str, Any]) -> None:
        provider_id = str(payload.get("provider") or "")
        # It works again, so a later failure is news even if the last one was
        # dismissed. This runs whether or not a prompt is currently showing:
        # dismissing removes the prompt, and the dismissal must lift too.
        self._dismissed_signatures = {
            signature for signature in self._dismissed_signatures
            if not signature.startswith(f"{provider_id}:")
        }
        request = self.reauthentication_required
        if request is None or request.get("provider") != provider_id:
            return
        self.reauthentication_required = None
        self.notifications.clear_dedupe(self.REAUTH_DEDUPE_KEY)

    def verify_sign_in(self) -> Optional[Dict[str, Any]]:
        """Check the configured provider's credential without being asked.

        Doing this at startup means an expired sign-in is reported when the
        user can act on it, rather than when some background task happens to
        need the model.
        """
        try:
            self.models.check_availability("primary", force=True)
        except Exception:                          # pragma: no cover - defensive
            log.debug("could not verify the stored sign-in", exc_info=True)
        return self.reauthentication_required

    # -- relocation (section 110.1) -----------------------------------------
    def _relocate_if_moved(self) -> Optional[str]:
        """Rewrite settings that pointed inside a previous root."""
        previous = str(self.config.get("agent_root") or "").strip()
        if not previous or Path(previous) == self.root:
            return None
        previous_root = Path(previous)
        rewritten = []
        for key, value in self.config.all().items():
            if not isinstance(value, str) or not value:
                continue
            field = SCHEMA_BY_KEY.get(key)
            if field is None or field.type not in ("path", "multiline"):
                continue
            lines = value.splitlines() or [value]
            updated = [self.paths.rewrite_path(line, previous_root) for line in lines]
            new_value = "\n".join(updated) if len(lines) > 1 else updated[0]
            if new_value != value:
                self.config.set(key, new_value, internal=True)
                rewritten.append(key)
        log.warning("agent root moved from %s to %s; rewrote %s",
                    previous_root, self.root, ", ".join(rewritten) or "no settings")
        return previous

    def _relocate_task_paths(self, previous: str) -> int:
        """Repoint scheduled task scripts that lived under the previous root."""
        previous_root = Path(previous)
        moved = 0
        for task in self.scheduler_store.list(limit=1000):
            if not task.script_path:
                continue
            updated = self.paths.rewrite_path(task.script_path, previous_root)
            if updated != task.script_path:
                self.scheduler_store.update(task.id, script_path=updated)
                moved += 1
        if moved:
            log.info("rewrote %s scheduled task script path(s) for the new root", moved)
        return moved

    # -- workspace ----------------------------------------------------------
    def _build_policy(self) -> WorkspacePolicy:
        """Workspace is writable; the sibling internal directory is off limits.

        The chat writes in the agent's own folder and in `workspace/Chat`,
        where each conversation keeps its files (agent/dialog/files.py). Work
        runs build policies of their own and are not given it."""
        from .agent.dialog.files import grant_chat
        policy = WorkspacePolicy.create(
            self.paths.workspace_dir,
            readable_paths=[Path.home()],
            protected_paths=self.paths.protected_paths(),
        )
        # Read anywhere but what is excluded (the user, 2026-11-08): the agents' own tools.
        policy.read_anywhere = True
        try:
            grant_chat(policy)
        except OSError:
            log.warning("could not make the chat's folder", exc_info=True)
        return policy

    def rebuild_workspace(self) -> WorkspacePolicy:
        """Re-apply the workspace setting without restarting the process."""
        self.policy = self._build_policy()
        self.runtime.policy = self.policy
        if getattr(self, "work", None) is not None:
            self.work.app_policy = self.policy
        log.info("workspace root is now %s", self.policy.workspace_root)
        return self.policy

    # -- settings reactions -------------------------------------------------
    def _on_settings_changed(self, topic: str, payload: Any) -> None:
        key = (payload or {}).get("key")
        if key is None:
            return
        if str(key).startswith("listener_"):
            self.listener.apply_settings()
        if str(key).startswith(("primary_", "model_", "heartbeat_model",
                                "scheduled_task_model")):
            self.models.invalidate()
        if key == "sandbox_enabled":
            self.runtime.sandbox_enabled = bool(self.config.get("sandbox_enabled", True))
        if key == "log_level" and self._configures_logs:
            configure_logging(self.paths.logs_dir, str(self.config.get("log_level", "INFO")))
        if key in ("workspace_root", "agent_root"):
            self.rebuild_workspace()

    # -- lifecycle ----------------------------------------------------------
    def _verify_sign_in_soon(self) -> None:
        """Check the stored sign-in in the background (section 75.3).

        Startup must not wait on the network, and the user must not have to
        wait for a background task to discover an expired sign-in.
        """
        thread = threading.Thread(target=self.verify_sign_in,
                                  name="verify-sign-in", daemon=True)
        thread.start()

    def _index_stages(self) -> None:
        from .heartbeat.experience.index import INDEX_VERSION, index_everything
        try:
            # Built a different way now: every stage again, once.
            again = int(self.config.get("stage_index_version", 0) or 0) != INDEX_VERSION
            made = index_everything(self.db, again=again)
            self.config.set("stage_index_version", INDEX_VERSION, internal=True)
            # And when each pattern and principle was last active.
            self.state_heartbeat.experience.store.refresh_last_active(self.library)
            if made:
                log.info("indexed the stages written before there was an "
                         "index: %s", made)
        except Exception:        # pragma: no cover - looked up again later
            log.warning("could not index the stages", exc_info=True)

    #: How much later "Remind me later" reminds again.
    REMIND_LATER_MINUTES = 30

    def remind_later(self, notification_id: str) -> Optional[Any]:
        """"Remind me later": dismissed now, and the same reminder again in
        half an hour - THE EVENT MOVED, not another one made: a one-off is
        put back on the calendar at its new time. A repeating one's series
        is not moved: it has ONE postponed reminder, made the first time and
        moved again each time after. Every move is written down on the
        reminder (`postponed`): 7:00 -> 7:30 -> 8:00."""
        from datetime import timedelta

        from .common.events.models import ScheduledTask, iso, utcnow

        shown = self.notifications.get(notification_id)
        if shown is None:
            return None
        self.notifications.mark_read(notification_id)
        self.learn_answer(shown, "remind_later")
        meta = shown.get("metadata") or {}
        when = utcnow() + timedelta(minutes=self.REMIND_LATER_MINUTES)
        note = f"Remind me later, pressed on \"{shown.get('title') or ''}\""
        source = (self.scheduler.store.get(str(meta.get("task_id") or ""))
                  if meta.get("task_id") else None)
        if source is not None and not source.recurrence:
            self.scheduler.store.postpone(source.id, when, note)
            return self.scheduler.store.get(source.id)
        source_id = str(meta.get("task_id") or "")
        if source_id:
            held = self.db.query_one(
                "SELECT id FROM scheduled_tasks WHERE status = 'pending' AND "
                "json_extract(metadata, '$.reminded_again_from') = ?", (source_id,))
            if held is not None:
                self.scheduler.store.postpone(held["id"], when, note)
                return self.scheduler.store.get(held["id"])
        return self.scheduler.create_task(ScheduledTask(
            title=str(shown.get("title") or "Reminder"),
            description=str(shown.get("message") or ""),
            # The time the log says it went to - read once, not again, or the
            # next move's "from" is not this one's "to".
            scheduled_at=when,
            execution_type="notification",
            task_payload={"message": str(shown.get("message") or ""),
                          "notify_user": True, "template": "reminder"},
            metadata={"created_by": "remind_later",
                      "reminded_again_from": str(meta.get("task_id") or ""),
                      # From the time the series' reminder was shown.
                      "postponed": [{"from": str(shown.get("created_at") or iso(utcnow())),
                                     "to": iso(when), "at": iso(utcnow())}]},
            created_by="user",
            reason=f"Remind me later, pressed on \"{shown.get('title') or ''}\""))

    def follow_explorer_menu(self) -> bool:
        """Explorer's "Delegate work" in line with the setting - with the
        avatar's face as its icon and the display language's words."""
        from .platform_layer import explorer_menu
        if not explorer_menu.available():
            return False
        from .common.languages import display_language
        from .platform_layer.autostart import launch_command
        icon = self.data_dir / "delegate.ico"
        try:
            from .ui.tray import icon_image
            face = icon_image(self.characters, str(self.config.get("character", "watermelon")),
                              256)
            if face is not None:
                face.save(icon, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (256, 256)])
        except Exception:                      # the words alone, then
            log.debug("could not make the Delegate work icon", exc_info=True)
        command = getattr(self, "launch_command", None) or launch_command(self.root)
        # Always there - not a setting.
        return explorer_menu.follow(True, command, icon if icon.exists() else None,
                                    display_language(self.config))

    def follow_system_menus(self) -> list:
        """DELEGATE WORK ON THE SYSTEM'S OWN MENUS beyond Explorer's
        (platform_layer/system_menus.py): macOS's Services - selected text
        in any program, files in Finder - and the Linux file managers'. Put
        in place again at each start, in the display language's words."""
        from .platform_layer import system_menus
        if not system_menus.available():
            return []
        from .common.languages import display_language
        from .platform_layer.autostart import launch_command
        command = getattr(self, "launch_command", None) or launch_command(self.root)
        return system_menus.follow(self.data_dir, command, display_language(self.config))

    def avatar_name(self) -> str:
        """What the avatar, and so the agent, is called: the name the user gave
        it, or the avatar's own."""
        from .agent.character.manager import avatar_name
        return avatar_name(self.config, getattr(self, "characters", None))

    def _add_assign_work_tool(self) -> None:
        """WORK HANDED OVER FROM THE CHAT (the user, 2026-11-08: "chat里(full model)要能add work (加入work
        assigned)，直接执行"): under Work assigned, started as Assign at once starts it - its
        context collected, its plan drafted, and begun with nothing more to approve."""
        from .models.base import ToolSpec

        def assign(args: Dict[str, Any]) -> Any:
            words = str(args.get("instruction") or "").strip()
            if not words:
                return {"error": "say what is to be done, in full"}
            folders = [str(one) for one in (args.get("folders") or []) if str(one).strip()][:12]
            try:
                draft = self.drafts.create(text=words, folders=folders, source="chat",
                                           write_first=True)
                self.drafts.compose(draft["id"], words, str(args.get("title") or ""))
                way = "auto" if getattr(self.work, "thorough_open", True) else "quick"
                said = self.drafts.start_apart(draft["id"], plan={"way": way},
                                               options={"sound": False, "unconfined": False,
                                                        "see_tries": True})
            except Exception as exc:             # said to the user, in its words
                return {"error": f"it could not be assigned: {exc}"[:300]}
            if said.get("error"):
                return {"error": said["error"]}
            return {"assigned": draft["id"],
                    "said": "Assigned - it is under Work assigned on the Overview, and starts once "
                            "its context is collected and its plan drafted. Tell the user so."}
        self.agent.tools.add(ToolSpec(
            "assign_work",
            "HAND A PIECE OF WORK TO THE AGENT TO DO, AT ONCE, when the user asks for something "
            "done that takes work - research, a document, code, a draft to send - rather than an "
            "answer here. It goes under Work assigned on the Overview and starts by itself: its "
            "context collected, its plan drafted, nothing more to approve. The instruction is "
            "everything the user wants, in full, as they said it and as agreed in this chat "
            "(the work reads nothing of the chat but this); a title only when they gave one; "
            "folders only the ones they named for it. Nothing is sent or published by it - what "
            "it makes is handed back to the user to check.",
            {"type": "object", "properties": {
                "instruction": {"type": "string", "description": "what is to be done, in full"},
                "title": {"type": "string", "description": "only a title the user gave"},
                "folders": {"type": "array", "items": {"type": "string"},
                            "description": "folders the user named for it, if any"}},
             "required": ["instruction"], "additionalProperties": False}), assign)

    def _add_special_issue_tool(self) -> None:
        from .common import modules as _modules
        from .models.base import ToolSpec

        def make(args: Dict[str, Any]) -> Any:
            if not _modules.enabled(self.config, "recommend"):
                return {"error": "Newspaper is switched off in Settings: no special issue "
                                 "can be made. Tell the user."}
            return self.connections_round.start_special(str(args.get("requirements") or ""),
                                                        str(args.get("title") or ""))
        self.agent.tools.add(ToolSpec(
            "make_special_issue",
            "Hand a SPECIAL ISSUE's brief to the Newspaper, which researches it on the web "
            "and publishes it there. In the chat you only make the brief clear with the user "
            "- ask, offer options - and call this once they want it made; you never write "
            "the issue here, nor copy the conversations into it as stories. The requirements "
            "carry everything agreed (audience, depth, time range, sources) and what of the "
            "chosen conversations it is about. A title only when the user gave one. Say that "
            "it was started, not that it is done.",
            {"type": "object", "properties": {
                "requirements": {"type": "string", "description": "the brief, in full"},
                "title": {"type": "string", "description": "only a title the user gave"}},
             "required": ["requirements"], "additionalProperties": False}), make)

    # -- EVERY ANSWER IS LEARNED FROM (the user, 2026-11-03: "所有notification,
    #    包括question, 的每个answer (包括add to do)都进行一个学习") -----------------------
    #: What each of a notification's own buttons says, for the model.
    ANSWERS = {"got_it": "saw it, with no complaint", "remind_later": "wanted it later: the "
               "moment was wrong, not necessarily the content",
               "to_todo": "worth doing: put it on their to-do list"}
    #: "Got it" says little: a weak sign.
    WEAK_ANSWERS = {"got_it": 0.5, "acknowledged": 0.5}

    def learn_answer(self, notification: Optional[Dict[str, Any]], answer: str, meaning: str = "",
                     said: str = "", question: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """One answer, an example for the lessons: what was said or asked, why
        it was made, what the user answered, in their words."""
        shown = notification or {}
        title = str(shown.get("title") or (question or {}).get("question") or "")
        message = str(shown.get("message") or (question or {}).get("task_detail") or "")
        if not (title or message) or (shown.get("kind") not in (None, "proactive")
                                      and answer != "chat_reply"):
            return None
        meaning = meaning or self.ANSWERS.get(answer, answer)
        summary = f'The agent said "{title}": {message[:600]} - the user answered "{answer}": {meaning}.'
        part_of = self.item_links.note("notification", str(shown.get("id") or ""))
        if part_of:
            summary += f" {part_of}"
        if (shown.get("metadata") or {}).get("recommended"):
            from .heartbeat.monitor.reserve import chain_line
            summary += f" {chain_line(shown.get('metadata'))}"
        try:
            source = str(shown.get("id") or (question or {}).get("id") or "")
            kind = "recommendations" if (shown.get("metadata") or {}).get("recommended") \
                else "notifications"
            return self.lessons.record(
                kind, f"answer:{source}:{answer}", summary, action=answer,
                moment=(shown.get("metadata") or {}).get("moment"),
                reason=str(said or "")[:1000], weight=None if said else self.WEAK_ANSWERS.get(answer),
                content=json.dumps({"notification": shown or None, "question": question,
                                    "answer": answer, "means": meaning}, ensure_ascii=False,
                                   default=str))
        except Exception:
            log.warning("an answer could not be learned from", exc_info=True)
            return None

    def _question_answered(self, _topic: str, payload: Any = None) -> None:
        """A question answered: learned from, with its notification."""
        if not isinstance(payload, dict):
            return
        from .agent.feedback.templates import meaning_of
        answer = str(payload.get("answer") or "")
        notification_id = str((payload.get("metadata") or {}).get("notification_id") or "")
        shown = self.notifications.get(notification_id) if notification_id else None
        if shown is None:
            row = self.db.query_one("SELECT id FROM notifications WHERE metadata LIKE ? "
                                    "ORDER BY created_at DESC LIMIT 1",
                                    (f'%"question_id": "{payload.get("id")}"%',))
            shown = self.notifications.get(row["id"]) if row else None
        self.learn_answer(shown, answer, meaning_of(str(payload.get("kind") or ""), answer),
                          str(payload.get("comment") or ""), payload)

    def notification_got_it(self, notification_id: str) -> None:
        """Got it: read, and an answer like any other."""
        shown = self.notifications.get(notification_id)
        self.notifications.mark_read(notification_id)
        if shown is not None and not (shown.get("metadata") or {}).get("question_id"):
            self.learn_answer(shown, "got_it")

    def chat_reply_notice(self, answer: str, conversation: str = "") -> None:
        """A REPLY NOBODY WAS THERE TO READ (the user, 2026-11-04): the chat closed before
        the model was done. Said as a notification, opened in the chat; a blocker holds
        it like any other."""
        from .common.interface_words import shown
        answer = str(answer or "").strip()
        if answer:
            self.notifications.notify(shown("Reply from your chat", self.config), answer[:900],
                                      kind="info", metadata={"template": "passive",
                                                             "chat_conversation": conversation})

    def notification_chat(self, notification_id: str, said: str) -> bool:
        """WRITTEN ON A NOTIFICATION (the user, 2026-11-04: "所有notification 底下有一个可以chat的
        东西 ... 这个agent也会记为lesson", "我chat回复之后就关闭", "改成不回复"): dismissed, and a
        lesson in their own words. Nothing answers it."""
        said = str(said or "").strip()
        shown = self.notifications.get(notification_id)
        if shown is None or not said:
            return False
        self.notifications.mark_read(notification_id)
        meta = shown.get("metadata") or {}
        if meta.get("question_id"):
            self.questions.expire(str(meta["question_id"]))   # this is its answer
        self.learn_answer(shown, "chat_reply", "the user answered it in their own words",
                          said=said)
        return True

    def notification_to_todo(self, notification_id: str) -> Optional[Dict[str, Any]]:
        """"Add to to-do": no more notifications about it - dismissed, and
        the question it asked, if any, answered so - and on the to-do list,
        shown in To do on the Overview until it is done."""
        shown = self.notifications.get(notification_id)
        if shown is None:
            return None
        self.notifications.mark_read(notification_id)
        meta = shown.get("metadata") or {}
        question_id = str(meta.get("question_id") or "")
        question = self.questions.get(question_id) if question_id else None
        if question is not None and question.status == "pending":
            from .agent.feedback.templates import TO_TODO
            self.feedback.answer(question_id, TO_TODO, notification_id=notification_id)
            found = self.db.query_one("SELECT id FROM todo_items WHERE notification_id = ? "
                                      "AND deleted_at = ''", (notification_id,))
            if found is not None:
                return self.todos.get(found["id"])
        else:
            self.learn_answer(shown, "to_todo")      # a question's answer is learned as one
        return self.todos.add(str(shown.get("title") or ""), str(shown.get("message") or ""),
                              notification_id=notification_id,
                              task_id=str(meta.get("task_id") or ""))

    def bad_notification(self, notification_id: str,
                         said: str = "") -> Optional[str]:
        """The user said a notification was a bad one: it is dismissed, and
        it is a lesson - the notification as it was shown and the event it
        came from, whole."""
        shown = self.notifications.get(notification_id)
        if shown is None:
            return None
        task_id = str((shown.get("metadata") or {}).get("task_id") or "")
        task = self.scheduler.store.get(task_id) if task_id else None
        self.notifications.mark_read(notification_id)
        if (shown.get("metadata") or {}).get("recommended"):
            from .heartbeat.monitor.reserve import chain_line
            self.lessons.record("recommendations", f"bad:{notification_id}",
                                f'The agent recommended "{shown.get("title") or ""}": '
                                f'{str(shown.get("message") or "")[:400]} - the user called it a '
                                f"bad notification. {chain_line(shown.get('metadata'))}",
                                action="bad_notification", reason=said,
                                moment=(shown.get("metadata") or {}).get("moment"))
        # One of the Monitor's: kept whole with why it was made (engine.on_bad).
        try:
            self.monitor.on_bad(shown, said)
        except Exception:
            log.warning("the Monitor could not keep a bad notification", exc_info=True)
        return self.calendar_nature.record("bad_notification", task, shown,
                                           said=said)

    #: What each choice on a story means, for the lessons.
    CHOICES = {"more": "wanted more like it", "less": "was not interested",
               "knew": "already knew it", "add_to_collection": "kept it in their collection",
               "taken_out": "took it out of their collection"}

    def learn_choice(self, story: Dict[str, Any], action: str) -> Optional[str]:
        """A choice on a story of the newspaper, an example for the lessons: found
        by the next issue and by what the Monitor recommends."""
        title = str(story.get("title") or "")
        said = str(story.get("english") or story.get("detail") or "")[:400]
        return self.lessons.record(
            "recommendations", f"story:{story.get('id') or ''}:{action}",
            f'The newspaper showed "{title}": {said} - the user {self.CHOICES.get(action, action)}.',
            action=action, content=json.dumps(story, ensure_ascii=False, default=str))

    def collection_items(self) -> List[Dict[str, Any]]:
        """The collection as a list: what the Collections panel shows and groups."""
        from .heartbeat.connections.recommend import collection
        out = []
        for one in collection(self.db)["items"]:
            story = one["story"]
            sources = [str(url) for url in story.get("sources") or [] if url]
            out.append({"id": one["story_id"], "text": str(story.get("title") or ""),
                        "detail": str(story.get("detail") or story.get("opening") or ""),
                        "because": str(story.get("reason") or story.get("because") or ""),
                        "url": str(story.get("url") or (sources[0] if sources else "")),
                        "from": "recommended" if story.get("recommended") else "newspaper",
                        "page": str(story.get("page_title") or ""),
                        "issue_no": story.get("issue_no"), "at": one["collected_at"]})
        return out

    def local_post(self, path: str, body: Dict[str, Any]) -> Any:
        """One of the application's own routes, asked as its page asks it: what a
        panel beside the avatar does is what the Overview does, by the same code."""
        import urllib.error
        import urllib.request
        request = urllib.request.Request(
            f"http://127.0.0.1:{int(self.config.get('api_port', 8756))}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", **self.api_access.headers()})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            said = json.loads(exc.read().decode("utf-8") or "{}")
            raise ValueError(str(said.get("error") or said or exc)) from exc

    def _work_actions(self, versions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """What came of a piece of work, as the Overview offers it: the newest outcome there is,
        of one finished or stopped."""
        from .common.interface_words import shown
        for one in reversed(versions):
            if one.get("state") not in ("finished", "stopped"):
                continue
            label = shown("Open the latest outcome", self.config)
            if one.get("items"):
                return [{"key": "open", "label": label, "path": "/api/work/handed/open",
                         "body": {"run": one.get("run"), "n": one["items"][0].get("n")}}]
            if one.get("result"):
                return [{"key": "open", "label": label, "path": "/api/work/reveal",
                         "body": {"run": one.get("run"), "path": one["result"], "open": True}}]
        return []

    def _asks_of(self, run: str) -> List[Dict[str, Any]]:
        """What a run going asks of the user now: to approve a command or an install, or what to
        do about its model. A run is in a process of its own: it is asked."""
        said = self.work.live(run, 1 << 31, calls=1 << 31)
        return list((said or {}).get("pending") or [])

    def _work_asks(self, versions: List[Dict[str, Any]]) -> Tuple[List[str], List[Dict[str, Any]]]:
        """WORK STATUS SAYS WHAT IT WAITS FOR (the user, 2026-11-06): what a piece of work going
        asks, in words, and the buttons that answer it. Answered, the work goes on."""
        from .common.interface_words import shown
        notes: List[str] = []
        actions: List[Dict[str, Any]] = []
        for one in versions:
            if one.get("state") != "ongoing" or not one.get("run"):
                continue
            for ask in self._asks_of(str(one["run"])):
                said = ": ".join(str(part) for part in (ask.get("title"), ask.get("what")) if part)
                notes.append(shown("Waiting for you: {0}", self.config).replace("{0}", said))
                if ask.get("why"):
                    notes.append(shown("Why: {0}", self.config).replace("{0}", str(ask["why"])))
                model = ask.get("kind") == "model"
                for allow, words in ((True, "Try again now" if model else "Approve"),
                                     (False, "End the run" if model else "Deny")):
                    actions.append({"key": f"ask:{ask.get('id')}:{int(allow)}", "changes": True,
                                    "label": shown(words, self.config), "path": "/api/work/approve",
                                    "body": {"run": one["run"], "request": ask.get("id"),
                                             "allow": allow}})
        return notes, actions

    def tell_work_waiting(self) -> None:
        """WORK STOPPED ON THE USER IS NOT LEFT SILENT: told once for each thing it asks, whichever
        window is open (the user's order, 2026-11-06: what the agent is stuck on pops). Answered
        under Work status."""
        from .common.interface_words import shown
        told = self.__dict__.setdefault("_work_waits_told", set())
        for one in self.work.run_states():
            if one.get("debug") or one.get("state") != "waiting_for_you":
                continue
            for ask in self._asks_of(str(one["run"])):
                ident = str(ask.get("id") or "")
                if ident in told:
                    continue
                told.add(ident)
                said = ": ".join(str(part) for part in (ask.get("title"), ask.get("what")) if part)
                self.notifications.notify(
                    shown("Work is waiting for you", self.config),
                    f'{said}\n{shown("Answer it under Work status, in To do.", self.config)}',
                    kind="approval", dedupe_key=f"work-waits-{ident}")

    def _waiting_sections(self) -> List[Dict[str, Any]]:
        """THE WAITING LIST (the user, 2026-11-06: "用列表，显示各种标题，我可以expand"): each by its
        title, and opened, its words, why it was told and its answers."""
        from .agent.feedback.templates import BAD_LABEL, GOT_IT
        from .common.interface_words import shown
        from .common.notifications import CATEGORIES, category_of, told_from, what_set_it_off
        parts: Dict[str, List[Dict[str, Any]]] = {name: [] for name in CATEGORIES}
        for one in self.notifications.waiting():
            ident, meta = str(one["id"]), one["metadata"]
            asked = str(meta.get("question_id") or "")
            question = self.questions.get(asked) if asked else None
            if question is not None and question.status == "pending":
                actions = [{"key": f"answer:{option.get('key')}", "changes": True,
                            "label": shown(str(option.get("label") or ""), self.config),
                            "path": "/api/notifications/answer",
                            "body": {"id": ident, "question": asked, "answer": option.get("key")}}
                           for option in question.options]
            else:
                actions = [{"key": "got_it", "label": shown(GOT_IT, self.config), "changes": True,
                            "path": "/api/notifications/read", "body": {"id": ident}}]
            actions.append({"key": "bad", "label": shown(BAD_LABEL, self.config), "changes": True,
                            "path": "/api/notifications/bad", "body": {"id": ident}})
            # IN THEIR OWN WORDS, a recommendation too (the user, 2026-11-08: "recommendation 也允许
            # 我用语言回复"): as under a bubble - a lesson in their words, and it is done with.
            actions.append({"key": "reply", "label": shown("Reply", self.config), "writes": True,
                            "changes": True, "fields": [shown("Tell the agent for next time...",
                                                              self.config)],
                            "keys": ["text"], "yes": shown("Send", self.config),
                            "path": "/api/notifications/chat", "body": {"id": ident}})
            why = str(meta.get("reason") or "")
            parts[category_of(one)].append({
                "id": ident, "title": str(one.get("title") or ""), "folds": True,
                "detail": str(one.get("message") or ""), "url": str(meta.get("url") or ""),
                "why": shown("Why: {0}", self.config).replace("{0}", why) if why else "",
                "note": shown("Trigger: {0}", self.config).replace(
                    "{0}", shown(told_from(meta), self.config)
                    + (f" - {what_set_it_off(meta)}" if what_set_it_off(meta) else "")),
                "actions": actions})
        # ONE ALONE IS OPEN (the user, 2026-11-06): there is nothing to choose between.
        waiting = [row for rows in parts.values() for row in rows]
        if len(waiting) == 1:
            waiting[0]["folds"] = False
        return [{"name": shown(name, self.config), "rows": rows}
                for name, rows in parts.items() if rows]

    def quick_sections(self, which: str) -> List[Dict[str, Any]]:
        """What a panel beside the avatar lists (ui/avatar_buttons.py), in the groups
        the list has: "todo" - with the work assigned, as the Overview has it beside
        To do - or "collection"."""
        from .common.interface_words import shown
        if which == "waiting":
            return self._waiting_sections()
        todo = which == "todo"
        items = [dict(one) for one in self.todos.open()] if todo else self.collection_items()
        part_of = self.item_links.of_many("todo", [one["id"] for one in items]) if todo else {}
        came = self.drafts.of_todos() if todo else {}
        moved = {"key": "move", "label": shown("Move", self.config), "moves": True, "list": which}

        def row(one: Dict[str, Any], kind: str = which) -> Dict[str, Any]:
            links = part_of if kind != "assigned" else self.item_links.of_many(
                "assigned", [one["id"]])
            names = [link["title"] for link in links.get(one["id"], [])]
            note = shown("Part of: {0}", self.config).replace("{0}", ", ".join(names)) if names \
                else str(one.get("because") or "")
            said = {"id": str(one["id"]), "title": str(one.get("text") or ""),
                    "detail": str(one.get("detail") or ""), "note": note,
                    "url": str(one.get("url") or "")}
            if kind == "todo":
                versions = (came.get(one["id"]) or {}).get("versions") or []
                notes, asks = self._work_asks(versions)
                said["note"] = "\n".join(filter(None, [*notes, note]))
                said["actions"] = asks + self._work_actions(versions) + [
                    {"key": "delegate", "label": shown("Delegate work", self.config),
                     "todo": one["id"], "text": "\n".join(
                         part for part in (one.get("text"), one.get("detail")) if part)},
                    {"key": "edit", "label": shown("Edit", self.config), "edits": True,
                     "changes": True, "path": "/api/todos/edit", "keys": ["text", "detail"],
                     "fields": [shown("What to do", self.config),
                                shown("Details (optional)", self.config)],
                     "body": {"id": one["id"]}},
                    {"key": "remove", "label": shown("Remove", self.config), "confirm": True,
                     "changes": True, "path": "/api/todos/delete", "body": {"id": one["id"]}},
                    moved]
            elif kind == "assigned":
                versions = (one.get("work") or {}).get("versions") or []
                notes, asks = self._work_asks(versions)
                said["note"] = "\n".join(filter(None, [*notes, note]))
                said["actions"] = asks + self._work_actions(versions)
            else:
                said["actions"] = ([{"key": "read", "label": shown("Read the source", self.config),
                                     "url": said["url"]}] if said["url"] else []) + [
                    {"key": "remove", "label": shown("Remove", self.config), "confirm": True,
                     "changes": True, "path": "/api/recommend/collection",
                     "body": {"story": one["id"], "keep": False}}, moved]
            return said
        by_id = {str(one["id"]): row(one) for one in items}
        view = self.list_groups.view(which, items)
        out: List[Dict[str, Any]] = []
        placed = set()
        for group in view.get("groups") or []:
            ids = [one for one in group.get("items") or [] if one in by_id]
            placed.update(ids)
            name = shown("Others", self.config) if group.get("others") else group.get("name")
            # Empty it stays, as on the Overview, and can be moved to.
            out.append({"name": str(name or ""), "rows": [by_id[one] for one in ids],
                        "group": str(group.get("name") or ""), "keep": not group.get("others")})
        rest = [by_id[one] for one in by_id if one not in placed]
        if rest:
            out.append({"name": "", "rows": rest})
        if todo:
            assigned = [row(one, "assigned") for one in self.drafts.assigned()]
            if assigned:
                out.append({"name": shown("Work assigned", self.config), "rows": assigned})
        return out

    def collection_changed(self) -> None:
        """Added or taken out: grouped again, as a list is on a change."""
        try:
            self.list_groups.notice("collection", self.collection_items())
        except Exception:
            log.warning("the collection could not be grouped", exc_info=True)

    def _keep_recommended(self, outcome: Dict[str, Any]) -> None:
        """"Add to collection", on what the model recommended."""
        from .heartbeat.connections.recommend import collect_recommended
        collect_recommended(self.db, str(outcome.get("notification_id") or ""),
                            {"title": outcome.get("title"), "detail": outcome.get("message"),
                             "url": outcome.get("url"), "reason": outcome.get("reason")})
        self.collection_changed()

    def _recent_stories(self) -> List[Dict[str, str]]:
        from .heartbeat.connections.recommend import recent_stories
        return recent_stories(self.connections)

    def _active_items(self) -> List[Dict[str, Any]]:
        """What is still open, the newest first in each kind: notifications not
        answered, to-dos, work assigned (heartbeat/states/item_links.py)."""
        out = [{"kind": "notification", "id": one["id"], "text": one.get("title") or "",
                "detail": one.get("message") or ""}
               for one in self.notifications.recent(30, unread_only=True)
               if one.get("kind") == "proactive"]
        out += [{"kind": "todo", "id": one["id"], "text": one.get("text") or "",
                 "detail": one.get("detail") or ""} for one in reversed(self.todos.open())]
        try:
            out += [{"kind": "assigned", "id": one["id"], "text": one.get("text") or "",
                     "detail": one.get("detail") or ""} for one in self.drafts.assigned()]
        except Exception:        # pragma: no cover - the rest are still linked
            log.debug("the work assigned could not be read", exc_info=True)
        return out

    # -- what the Monitor's decisions are given (heartbeat/monitor/decide.py) ----------
    def _moment_now(self) -> Dict[str, Any]:
        """What the user is doing, as a lesson keeps it (heartbeat/lessons/moment.py)."""
        from .heartbeat.lessons.moment import moment_of
        return moment_of(self.monitor.latest, self.monitor.recommended_today())

    def _monitor_tasks(self) -> List[str]:
        """The work going on: the tasks worked on lately, with where each stands."""
        from .heartbeat.states.tasks import Kept
        tasks = sorted(Kept(self.db).live(), key=lambda one: str(getattr(one, "updated_at", "")
                                                                 or ""), reverse=True)
        out = []
        for task in tasks[:12]:
            said = task.described or {}
            title = str(said.get("title") or "").strip()
            if title and not said.get("deleted_by_user"):
                status = str(said.get("status") or said.get("where_it_stands") or "").strip()
                out.append(title + (f" - {status[:160]}" if status else ""))
        return out

    def _monitor_today(self, until: Any) -> Dict[str, List[str]]:
        """WHAT TODAY HOLDS (the day ahead, 2026-11-07): the notifications on the calendar from now
        to `until`, each with its time; the open to-dos; what is agreed and waits for a time."""
        from .common.events.models import utcnow
        out: Dict[str, List[str]] = {"scheduled": [], "todos": [], "waiting": []}
        store = getattr(self.scheduler, "store", None)
        if store is not None:
            for task in store.list(status="pending", start=utcnow(), end=until, limit=60):
                if task.execution_type != "notification":
                    continue
                at = task.scheduled_at.astimezone().strftime("%H:%M") if task.scheduled_at else ""
                details = (task.metadata or {}).get("details") or {}
                line = f"- {at} {task.title}: {str(task.description or '')[:300]}"
                if details:
                    line += " (" + "; ".join(f"{k}: {v}" for k, v in
                                             list(details.items())[:6])[:300] + ")"
                out["scheduled"].append(line)
        for one in self.todos.open()[:20]:
            out["todos"].append(f"- {one['text']}" + (f": {str(one['detail'])[:200]}"
                                                      if one.get("detail") else ""))
        try:
            out["waiting"] = list(self.pending.lines())[:12]
        except Exception:                                # pragma: no cover - read without it
            pass
        return out

    def _monitor_experience(self, query: str) -> List[str]:
        """What was learned of how the user works, like the moment (Work learning)."""
        experience = getattr(self.state_heartbeat, "experience", None)
        if experience is None or not query.strip():
            return []
        found = experience.retriever().retrieve(query)
        return [str(rule.get("statement") or rule.get("title") or rule.get("detail") or "")
                for rule in found.get("rules") or []
                if rule.get("statement") or rule.get("title") or rule.get("detail")]

    #: Set once the lessons were started afresh in the engine.
    LESSONS_FRESH = "lessons_engine_started"

    def lessons_fresh_start(self) -> bool:
        """ONCE: the lessons learned the old way deleted - the notification
        lessons and the summary of the connection changes - and every action
        the user took kept, and given to the lessons engine as evidence, to
        learn from again. False when it was done before."""
        if self.config.get(self.LESSONS_FRESH, False):
            return False
        self.db.execute("DELETE FROM calendar_nature WHERE kind = 'lesson'")
        self.db.execute("DELETE FROM calendar_lesson_changes")
        self.db.execute("DELETE FROM connection_lessons")
        self.lessons_catch_up()
        self.config.set(self.LESSONS_FRESH, True, internal=True)
        return True

    def lessons_catch_up(self) -> int:
        """Every action the user took that is not evidence yet - from before
        the lessons engine, or loaded from a lessons package - made so, to be
        learned from. How many."""
        from .heartbeat.core.calendar_nature import _raw_of, evidence_text
        known = {row["source_id"] for row in self.db.query(
            "SELECT source_id FROM lesson_evidence")}
        made = 0
        for row in self.db.query("SELECT id, acted_at, action, event FROM calendar_actions "
                                 "ORDER BY acted_at, rowid"):
            if row["id"] in known:
                continue
            raw = _raw_of(row.get("event"))
            self.lessons.record("notifications", row["id"],
                                evidence_text(row["action"], raw), action=row["action"],
                                reason=raw.get("user_said") or "", at=row["acted_at"],
                                learn=False)
            made += 1
        for one in reversed(self.connections.actions("user", 10_000)):
            if one["id"] in known:
                continue
            self.lessons.record("connections", one["id"], self.connections.evidence_text(one),
                                action=one["action"], reason=one.get("reason") or "",
                                at=one["at"], learn=False)
            made += 1
        return made

    def leftover_marks(self) -> list:
        """What only this application's own processes carry on their
        command line: the folder the agents' code loads its guard from, and
        the one each run in a process of its own is told about."""
        from .agent.work.child import children_dir
        return [str(self.paths.guard_dir), str(children_dir(self.paths))]

    def keep_extension_current(self) -> None:
        """THE BROWSER EXTENSION, INSTALLED ALREADY, KEPT AS THIS VERSION HAS IT
        (platform_layer/browser_extension.py, `keep_current`): its host names
        the stores' extensions too, and its folders are this version's -
        nothing where it was never installed."""
        try:
            from .platform_layer import browser_extension
            from .platform_layer.autostart import launch_command
            from .version import __version__
            try:
                from .ui.tray import icon_image
                icon = icon_image(self.characters, str(self.config.get("character", "watermelon")),
                                  128)
            except Exception:
                icon = None
            command = getattr(self, "launch_command", None) or launch_command(self.root)
            browser_extension.keep_current(Path(self.paths.data_dir), command, __version__, icon)
        except Exception:     # pragma: no cover - as it was; installed again from Settings
            log.info("could not keep the browser extension current", exc_info=True)

    def end_leftovers(self) -> int:
        try:
            from .common.children import end_leftovers
            return end_leftovers(self.leftover_marks())
        except Exception:    # pragma: no cover - looked at next start
            log.warning("could not look for processes left from before", exc_info=True)
            return 0

    def _work_tab_folders(self) -> list:
        """The folders of the projects the user made in Work for their own
        folders - not the agents' own projects, nor the debugger's."""
        experience = getattr(self.state_heartbeat, "experience", None)
        store = getattr(experience, "store", None)
        if store is None:
            return []
        removed = set(self.config.get("experience_removed_units") or [])
        out = []
        try:
            for row in store.workspaces():
                unit = str(row.get("unit_id") or "")
                if unit in removed or unit.startswith("dbg_") or row.get("origin") == "work":
                    continue
                for one in row.get("paths") or []:
                    out.append({"path": str(one), "project": row.get("name") or ""})
        except Exception:                           # pragma: no cover - no projects
            log.debug("could not read the Work projects' folders", exc_info=True)
        return out

    def start(self, with_api: bool = True) -> None:
        with self._lock:
            if self._started:
                return
            log.info("starting the personalized agent")
            # ONCE, before anything reads the experience: what the Build now
            # test learned becomes the user's (experience/promote.py).
            try:
                from .heartbeat.experience.promote import (
                    keep_only_lesson_natures, promote_the_test)
                promote_the_test(self.db, self.config,
                                 Path(self.library.root), Path(self.paths.data_dir))
                # And, once, only the natures the lessons taught are kept.
                keep_only_lesson_natures(self.db, self.config,
                                         Path(self.library.root),
                                         Path(self.paths.data_dir))
            except Exception:    # pragma: no cover - the old state stands
                log.warning("could not make the test's experience the user's",
                            exc_info=True)
            # ONCE: the old learning backed up, and learning started again.
            try:
                from .learning.fresh import start_afresh
                start_afresh(self.db, self.config, Path(self.library.root),
                             Path(self.paths.data_dir))
            except Exception:    # pragma: no cover - tried again next start
                log.warning("could not back up the old learning", exc_info=True)
            # ONCE MORE: what the first learning read in passing files and in
            # programs' conversations - backed up, emptied, learned again.
            try:
                from .learning.fresh import learn_again
                learn_again(self.db, self.config, Path(self.paths.data_dir))
            except Exception:    # pragma: no cover - tried again next start
                log.warning("could not empty the learning to learn again", exc_info=True)
            # ONCE: Newspaper's switch - on where it made issues by itself.
            try:
                from .heartbeat.connections.schedule import carry_over
                carry_over(self.config, bool(self.connections.briefing()))
            except Exception:    # pragma: no cover - tried again next start
                log.warning("could not carry the Newspaper's times over", exc_info=True)
            # TIMED, for the same reason the way out is: a phase that does
            # not say what it cost is a phase somebody will guess about,
            # and the guess will be wrong. Startup is the half of this
            # nobody had numbers for.
            import time as _time

            # LEFT FROM BEFORE: whatever an earlier run of the application
            # left going - its job or watcher could not end it - is ended
            # (common/children.py), behind the start.
            threading.Thread(target=self.end_leftovers, name="leftovers",
                             daemon=True).start()
            # THE BROWSER EXTENSION, as this version has it - behind the start.
            threading.Thread(target=self.keep_extension_current, name="extension",
                             daemon=True).start()
            spent = {}
            for name, begin in (("ListenerManager", self.listener.start),
                                ("Scheduler", self.scheduler.start),
                                ("AgentHeartbeat", self.heartbeat.start),
                                ("RawHousekeeping", self.housekeeping.start),
                                ("StateHeartbeat", self.state_heartbeat.start),
                                ("Monitor", self.monitor.start),
                                ("IssueClock", self.issue_clock.start),
                                ("Learning", self.learning.begin),
                                ("Triggers", self.triggers.start),
                                ("WorkGovernor", self.work.governor.start),
                                ("LocalIndex", self.local_index.begin),
                                ("Society", self.society.begin)):
                began = _time.perf_counter()
                begin()
                spent[name] = round(_time.perf_counter() - began, 3)
            # Stages written before they were indexed: indexed now, in the
            # background - every one written from here on is indexed as it
            # is written.
            threading.Thread(target=self._index_stages, name="stage-index",
                             daemon=True).start()
            # The guard every child loads, copied out of the application's
            # code - which a debugger run may not read - into the protected
            # state, where no run can change it (sandbox.ensure_guard).
            try:
                from .runtime.sandbox import ensure_guard
                ensure_guard(self.paths.guard_dir)
            except Exception:    # pragma: no cover - made again before each child
                log.warning("could not copy the guard out of the code", exc_info=True)
            # The application's code open again to the agents' code, should
            # debugger runs the application ended with have left it sealed:
            # none is alive now (isolation.CodeSeal - on a thread of its own).
            if getattr(self, "work", None) is not None:
                self.work.settle_code()
            # What the agents' code may read - the home - made readable once,
            # here in the background, never inside a run (isolation.py).
            from .runtime.isolation import prepare_reading
            threading.Thread(target=prepare_reading,
                             args=(list(self.policy.readable_paths),
                                   list(self.policy.protected_paths)),
                             name="read-grant", daemon=True).start()
            # THE LESSONS, LEARNED AGAIN FROM THE START once (the user's
            # decision): the old lessons cleared, every action kept and
            # given to the engine as its evidence; then whatever is new.
            try:
                self.lessons_fresh_start()
                # Only what the user does is evidence: what the agents' own
                # doing had added, taken out once.
                if not self.config.get("lessons_only_the_user", False):
                    self.lessons.forget_the_agents_own()
                    self.config.set("lessons_only_the_user", True, internal=True)
            except Exception:    # pragma: no cover - the evidence waits
                log.warning("could not start the lessons afresh", exc_info=True)
            # Learned with the state heartbeat's next round - or now, asked.
            if with_api:
                from .api.server import ApiServer

                began = _time.perf_counter()
                self.api = ApiServer(self)
                self.api.start()
                spent["ApiServer"] = round(_time.perf_counter() - began, 3)
            log.info("start took %.3fs: %s", sum(spent.values()),
                     ", ".join(f"{name} {held:.3f}s" for name, held
                               in sorted(spent.items(),
                                         key=lambda one: -one[1])))
            self._start_spent = spent
            # Only a packaged program updates: run from source, the code is
            # the developer's checkout and is updated with git.
            if getattr(sys, "frozen", False):
                self.updater.start()
            self._started = True
            self.observability.set_state("application.started", True)
            self.bus.publish("application.started", self.status())
        self._verify_sign_in_soon()

    #: Settings whose default moved, as (key, the old default, why).
    #:
    #: Kept as a list rather than done inline because there will be more
    #: of them, and because each one needs saying: a number that changed
    #: because the thing it measured changed is a different case from a
    #: number that was simply wrong.
    #:
    #: APPEND ONLY. The mark is how many of these have been applied, so an
    #: entry inserted in the middle is counted as already done and never
    #: runs - which is what happened the first time one was added here,
    #: silently, with the test that proved it passing for the wrong reason.
    MOVED_DEFAULTS = (
        ("max-states-400", "state_heartbeat_max_states", 400,
         "four hundred was the square paid every hour; it is now paid when "
         "the grouping is found again, which is a handful of times in a "
         "hundred rounds"),
        ("loose-is-a-task-8", "state_task_loose_is_a_task", 8,
         "eight was how long to wait before rebuilding the whole calendar "
         "to find out whether the leftovers were a task; the leftovers are "
         "now grouped among themselves, which is nearly free"),
        ("summaries-enabled-off", "state_summaries_enabled", False,
         "off was right while the grouping itself was what was being worked "
         "on; a task without a sentence saying what it is is a number and a "
         "size, and being told what a hundred states add up to is the whole "
         "reason for grouping them"),
        ("community-min-3", "state_community_min_nodes", 3,
         "three was the first answer to how small a group can be and still "
         "be worth a sentence, and it was too small: three states are three "
         "states, shorter read as themselves than as a summary of "
         "themselves, and a model call spent saying roughly what the three "
         "of them already say"),
        ("deepest-layers-2", "state_task_deepest_layers", 2,
         "two layers ended at a list of the states themselves, which put "
         "two different kinds of thing under one gesture: the same click "
         "gave a summary in one place and a transcript in another. Every "
         "layer is now summarised work, and the bottom one is the bottom "
         "because nothing inside it divided again"),
        # APPENDED, NEVER INSERTED, and never added before the default it
        # describes has actually changed.
        #
        # The mark is a COUNT of how many entries have been applied, so an
        # entry added in the middle is skipped by everyone whose count is
        # already past it - and an entry added while the schema still held
        # the old value does nothing, advances the count, and is never
        # looked at again. Both happened to this one: it was added in the
        # middle, on an afternoon when the default was still True, and a
        # running install moved its count to 6 without changing anything.
        ("greet-on-start-off-2", "greet_on_start", True,
         "a greeting every time the application opens is one more thing to "
         "dismiss, and the window appearing is already the news. The -2 is "
         "a second attempt: the first was counted as done on an afternoon "
         "when the default had not yet changed, so it did nothing and was "
         "never looked at again"),
    )

    #: What the sandbox's own numbers are, now that they are not settings.
    #:
    #: They were settings for an afternoon, which was long enough for an
    #: install to write them down: a stored value beats a schema default
    #: for ever, so the sandbox kept reading a window that had been
    #: replaced. They are constants in the code that uses them now, and
    #: these are only here to clear what got stored.
    SANDBOX_SETTINGS = ("state_sandbox_max_states", "state_sandbox_summaries",
                        "state_sandbox_root")

    def _forget_the_sandbox_settings(self) -> None:
        """Take the debugger's fixtures out of the user's settings.

        The sandbox is where the state heartbeat is tried on made-up data.
        How much of it to read, and whether to summarise it, are facts
        about a debugging tool - they change nothing the user's agent
        does, they do not belong on a settings page, and having them there
        meant every adjustment to the tool was frozen into whichever
        installs happened to restart that day.
        """
        if bool(self.config.get("state_sandbox_settings_forgotten", False)):
            return
        held = self.config.all()
        for key in self.SANDBOX_SETTINGS:
            if key not in held:
                continue
            # There is no way to unset one, so it is set to the value the
            # code now uses: the same thing, for behaviour.
            self.config.set(key, SANDBOX_FIXTURES[key], internal=True)
            log.info("%s is no longer a setting; it belongs to the debugger",
                     key)
        self.config.set("state_sandbox_settings_forgotten", True,
                        internal=True)

    #: The seconds-based interval the state heartbeat used to run on, and
    #: what it defaulted to. A stored value EQUAL to the default was never
    #: chosen by anybody; anything else was.
    STATE_INTERVAL_WAS = ("state_heartbeat_interval_seconds", 3600)

    def _carry_the_state_interval(self) -> None:
        """Move a chosen grouping interval onto the setting that replaced it.

        How often states are grouped was an interval in seconds and is now
        a gap in hours - one number instead of two, because two numbers
        for one thing meant whichever the loop slept on, the other was
        what the panel showed.

        A RENAME MUST CARRY WHAT WAS CHOSEN. Dropping the old key would
        silently reset somebody who had deliberately set ten minutes back
        to six hours, and they would have no way to know that a tidy-up
        had changed how their agent behaves. A stored value that equals
        the old default was nobody's choice and is left to take the new
        default instead - the same rule `_move_stale_defaults` uses.
        """
        key, was_default = self.STATE_INTERVAL_WAS
        if bool(self.config.get("state_interval_carried", False)):
            return
        held = self.config.all()
        if key in held:
            try:
                seconds = int(held[key])
            except (TypeError, ValueError):
                seconds = was_default
            if seconds != was_default:
                hours = round(max(1.0 / 60.0, seconds / 3600.0), 4)
                self.config.set("state_heartbeat_every_hours", hours,
                                internal=True)
                log.info("%s was set to %ss; grouping now runs every %sh",
                         key, seconds, hours)
            else:
                log.info("%s held its old default; grouping now runs on the "
                         "new one", key)
        self.config.set("state_interval_carried", True, internal=True)

    def _move_stale_defaults(self) -> None:
        """Bring settings nobody chose up to what they now default to.

        THE MARK IS WHICH ONES, BY NAME. It was a count, and a count is
        wrong in two ways that both happened here. An entry added in the
        middle of the list is skipped by every install whose count is
        already past it. And an entry added BEFORE the default it talks
        about has actually changed does nothing, advances the count, and
        is never looked at again - which is how an install carried on
        greeting its owner every morning after the greeting was turned
        off, with the setting marked as already dealt with.

        A name cannot be reached by accident. An entry whose default has
        not moved yet is left unmarked, so it applies when it becomes
        real rather than being spent on a release where it meant nothing.
        """
        done = self._defaults_already_moved()
        changed = False
        for tag, key, was, why in self.MOVED_DEFAULTS:
            if tag in done:
                continue
            field = SCHEMA_BY_KEY.get(key)
            if field is None:
                continue
            if field.default == was:
                # NOT YET A CHANGE, so not yet anything to record. Marking
                # it here is exactly the mistake above.
                continue
            try:
                held = self.config.get(key, field.default)
            except Exception:            # pragma: no cover - a read, not a load
                continue
            if held == was:
                self.config.set(key, field.default, internal=True)
                log.info("%s was %s, the old default, and is now %s: %s",
                         key, was, field.default, why)
            done.add(tag)
            changed = True
        if changed:
            self.config.set("state_defaults_done", json.dumps(sorted(done)),
                            internal=True)

    def _defaults_already_moved(self) -> set:
        """The names applied so far, counting what the old mark implies.

        Installs in the field carry a COUNT from the previous scheme. The
        first so many entries were considered then, so their names are
        taken as done - except any whose default had not moved at the
        time, which by definition did nothing and is left to apply now.
        """
        try:
            held = json.loads(
                str(self.config.get("state_defaults_done", "[]")) or "[]")
            if isinstance(held, list):
                return {str(one) for one in held}
        except (TypeError, ValueError):
            pass
        try:
            many = int(self.config.get("state_defaults_moved", 0) or 0)
        except (TypeError, ValueError):
            many = 0
        return {tag for tag, _key, _was, _why in self.MOVED_DEFAULTS[:many]}

    def stop(self) -> None:
        with self._lock:
            if not self._started:
                return
            log.info("stopping the personalized agent")
            self.chat_files.stop()
            try:
                self.society.stop()
            except Exception:                    # pragma: no cover - never in the way
                pass
            # EACH PHASE TIMES ITSELF. Shutdown was eight seconds and the
            # log said only when each component finished, which is not the
            # same as how long it took - and one of them, the listener,
            # logged nothing at all. Three of those seconds were read off
            # the gap before "api stopped" and spent on a theory about the
            # api, because the component actually sitting in that gap was
            # invisible. A phase that does not say what it cost is a phase
            # somebody will misattribute.
            import time as _time

            spent = {}
            # WORK GOING STOPS CLEANLY (the user, 2026-10-28): each run keeps
            # where it stands now - continued, it goes on from there, not
            # from a point before; continued by hand, as always. Begun first,
            # beside the rest of the closing, and waited for at its end.
            import threading as _threading
            from .agent.work.runner import CLOSE_SECONDS

            why = "for an update" if self.updater.updating() else "it was quit"
            began_work = _time.perf_counter()
            closing = _threading.Thread(target=self._close_work, args=(why,),
                                        name="close-work", daemon=True)
            closing.start()
            # ALL TOLD AT ONCE (the user, 2026-11-05: quitting held for 18 seconds): each was told
            # only when the one before it had been waited for, up to five seconds each.
            for loop in (self.state_heartbeat, self.heartbeat):
                loop._stop.set()
                loop._interval_changed.set()

            before: Dict[str, float] = {}

            def timed(name: str, part: Any) -> None:
                began_part = _time.perf_counter()
                part()
                before[name] = round(_time.perf_counter() - began_part, 3)

            timed("Updater", self.updater.stop)
            timed("Governor", self.work.governor.stop)
            # The learning clock told first, at once: it must not read a run
            # the closing stops as finished - that run is carried on at the
            # next start. Then the state heartbeat, first to be waited for:
            # a round in flight holds the way out.
            timed("Learning", self.learning.halt)
            # A build going is ended: what it read is kept, and the next one
            # does not read it again.
            timed("LocalIndex", self.local_index.halt)
            for component in (self.state_heartbeat, self.monitor,
                              self.issue_clock, self.triggers,
                              self.housekeeping,
                              self.heartbeat, self.scheduler, self.listener):
                name = type(component).__name__
                began = _time.perf_counter()
                try:
                    component.stop()
                except Exception:
                    log.exception("component shutdown failed")
                spent[name] = round(_time.perf_counter() - began, 3)
            spent.update(before)
            closing.join(CLOSE_SECONDS + 5)
            spent["WorkRuns"] = round(_time.perf_counter() - began_work, 3)
            if self.api is not None:
                began = _time.perf_counter()
                try:
                    self.api.stop()
                except Exception:
                    log.exception("api shutdown failed")
                spent["ApiServer"] = round(_time.perf_counter() - began, 3)
            # THE TIME IT TOOK, AS A CLOCK SAYS IT (the user, 2026-11-07): the parts were added up,
            # though the work's closing runs beside the rest and its time holds theirs - five
            # seconds read as ten. Each part's own time is still said, overlapping as they do.
            log.info("shutdown took %.3fs (the parts overlap): %s",
                     _time.perf_counter() - began_work,
                     ", ".join(f"{name} {held:.3f}s" for name, held
                               in sorted(spent.items(), key=lambda one: -one[1])))
            self._shutdown_spent = spent
            self._started = False
            self.bus.publish("application.stopped", {})

    def _close_work(self, why: str) -> None:
        try:
            self.work.close(why)
        except Exception:
            log.exception("the work going could not stop cleanly")

    def _heartbeats_now(self) -> Dict[str, Any]:
        """Which heartbeats are mid-round, in one shape.

        Each answers the same question in the same words, because the
        thing reading this is a line with room for a few of them and does
        not care which one spoke.
        """
        out: Dict[str, Any] = {}
        for name, beat in (("agent", self.heartbeat),
                           ("states", self.state_heartbeat)):
            try:
                out[name] = beat.what_it_is_doing()
            except Exception:    # pragma: no cover - a status, not the work
                out[name] = {"busy": False, "doing": "", "stage": "",
                             "detail": "", "since": ""}
        out["any"] = any(one.get("busy") for one in out.values()
                         if isinstance(one, dict))
        return out

    # -- debugger helpers ---------------------------------------------------
    @property
    def listener_running(self) -> bool:
        try:
            return bool(self.listener.health().get("running"))
        except Exception:
            return False

    # -- status -------------------------------------------------------------
    @property
    def api_base_url(self) -> str:
        return f"http://{self.config.get('api_host')}:{self.config.get('api_port')}"

    def sign_in_url(self, path: str = "/", seconds: Optional[int] = None,
                    for_extension: bool = False) -> str:
        """A link that opens ``path`` of the page with the browser signed in:
        what the desktop opens, never the bare address (api/access.py).
        `for_extension`: for the browser extension's own window alone."""
        from .api.access import TICKET_SECONDS

        return self.api_access.sign_in_url(self.api_base_url, path,
                                           seconds or TICKET_SECONDS, for_extension)

    def agent_state(self) -> Dict[str, Any]:
        """A compact picture of what the agent is doing (section 7.5).

        Everything here is already in memory: no model call, no network, no
        processing. Hovering over the agent must not change what it does.
        """
        resolved = self.models.resolve("primary")
        cached = self.models.cached_availability(resolved)
        upcoming = self.scheduler.store.next_due() if hasattr(self.scheduler.store,
                                                              "next_due") else None
        return {
            "agent_name": self.avatar_name(),
            "model": {
                "label": resolved.label,
                "available": None if cached is None else cached.available,
                "state": None if cached is None else cached.state.value,
                "reason": None if cached is None else cached.reason,
            },
            # The third heartbeat was missing from here entirely, so the
            # status bar could not have shown it however it was written.
            # `last_report` is left out for the same reason `checklist`
            # is: this is read on every hover, and it holds the whole of
            # the last round - every state, every pair it printed, the
            # tree. What belongs on a hover is how many tasks there are.
            "state_heartbeat": {
                **{key: value for key, value
                   in self.state_heartbeat.status().items()
                   if key != "last_report"},
                "tasks": len((self.state_heartbeat.last_report
                              or {}).get("layers") or []),
            },
            "heartbeat": {key: value for key, value in self.heartbeat.status().items()
                          if key != "checklist"},
            "next_task": upcoming,
            # Every work run going, the Experience debugger's too: they go
            # on with their panels closed, and this is where that shows.
            "work": self.work.going_now() if getattr(self, "work", None) is not None else [],
            "privacy": {
                "monitoring_enabled": bool(self.config.get("listener_enabled")),
            },
            "needs_attention": self.reauthentication_required,
        }

    def _seconds_away(self) -> Optional[float]:
        """How long since the user last touched the computer - None where the
        system does not say. An update made ready waits for this
        (platform_layer/updater.py)."""
        try:
            return self.platform.activity.seconds_since_input()
        except Exception:
            return None

    def status(self) -> Dict[str, Any]:
        return {
            "running": self._started,
            "application": {"name": APP_NAME, "version": __version__},
            # Section 14.33, as amended: whether this build carries the
            # Debugger at all, so the interface can leave it out rather than
            # offering a tab whose every request would answer 404.
            "debugger_built_in": debugger_available(),
            # Whether the bubble beside the avatar shows notifications: when
            # it does, the page does not show them again (ui/desktop.py).
            "speaks_in_bubble": bool(getattr(self, "speaks_in_bubble", False)),
            "root": str(self.root),
            "relocated_from": self.relocated_from,
            "data_dir": str(self.data_dir),
            "paths": self.paths.describe(),
            "api": self.api_base_url,
            "platform": self.platform.describe(),
            "runtime": self.runtime.describe(),
            "listener": {"providers": self.listener.provider_ids()},
            "scheduler": self.scheduler.status(),
            # WHAT IS HAPPENING RIGHT NOW, for the status line. A round
            # can take minutes - the grouping is one model call per task -
            # and an application that looks idle while it is working is one
            # somebody quits in the middle of, or waits in front of without
            # knowing there is anything to wait for.
            "heartbeats": self._heartbeats_now(),
            # AN UPDATE UNDER WAY, followed by the page to the new version
            # (platform_layer/updater.py) - None when there is none.
            "updating": self.updater.updating() if getattr(self, "updater", None) else None,
            "library": self.library.stats(),
            "models": {
                role: self.models.resolve(role).to_dict()
                for role in ("primary", "heartbeat", "scheduled_task")
            },
            "identity": self.identity.public_identity() if self.identity else None,
            # Section 75.3: the interface needs to know when to ask, and only then.
            "reauthentication_required": self.reauthentication_required,
            # Section 25, as amended: visible without opening anything.
            "privacy": {
                "monitoring_enabled": bool(self.config.get("listener_enabled")),
            },
        }
