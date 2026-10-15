"""Schema driven settings (section 12).

The Settings UI is generated from this schema, so adding a setting here is the
only change required to expose it in the frontend.
"""

from __future__ import annotations

import os

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional
from ..languages import CODES as _LANGUAGE_CODES
from ..languages import PINNED_LANGUAGES as _PINNED_LANGUAGES
from ..languages import system_language as _system_language
from ..languages import DISPLAY_CODES as _DISPLAY_CODES
from ..languages import default_display as _default_display

#: Every level of thinking a Codex model may be asked for, in order. Which of
#: them a given model takes is its catalog's to say (models/providers/
#: codex_provider.py); this is the vocabulary, not a promise.
REASONING_EFFORTS = ["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"]


@dataclass
class SettingField:
    key: str
    label: str
    type: str  # string | int | float | bool | choice | path | multiline
               # | dynamic_choice | multi_choice
    default: Any
    section: str = "General"
    description: str = ""
    choices: Optional[List[Any]] = None
    choices_source: Optional[str] = None  # resolved by the API at runtime
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    secret: bool = False
    restart_required: bool = False
    readonly: bool = False
    #: Pinned in this version, rather than derived from somewhere else.
    #:
    #: A different thing from ``readonly``, which means "this follows the
    #: agent root, change that instead". This one means "the machinery takes
    #: a choice here and this version does not offer one yet" - so it is
    #: shown, and it says so, and it cannot be set. Collapsing the two would
    #: make one of them lie about how to change it.
    fixed: bool = False
    #: "directory" when the value is a location on disk, so the interface
    #: offers the platform's chooser instead of a typed path (section 12).
    chooser: Optional[str] = None
    #: True for state the user flips as a control rather than configures. It is
    #: stored like any setting but is not shown in the settings list (25).
    hidden: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "type": self.type,
            "default": self.default,
            "section": self.section,
            "description": self.description,
            "choices": self.choices,
            "choices_source": self.choices_source,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "secret": self.secret,
            "restart_required": self.restart_required,
            "readonly": self.readonly or self.fixed,
            "fixed": self.fixed,
            "chooser": self.chooser,
            "hidden": self.hidden,
        }


def default_workspace() -> str:
    """The workspace subdirectory of the default root (section 110.1)."""
    from ..paths import WORKSPACE_DIR, default_root

    return str(default_root() / WORKSPACE_DIR)


def default_agent_root() -> str:
    from ..paths import default_root

    return str(default_root())


def default_watch_paths() -> str:
    """Everywhere, by default (section 19, as amended).

    The agent is meant to notice what the user does, and it cannot notice what
    it is not looking at. A narrow default means the feature reports itself as
    running while recording almost nothing, which is worse than being off -
    the user believes their work is being seen when it is not.

    So the default is every directory on the machine, and the user removes
    what they would rather it did not see. That way round is the honest one:
    what is watched is visible in one list, and narrowing it is a decision the
    user makes rather than one made quietly for them. The exclusions above
    carry the churn that would otherwise drown it.
    """
    import string
    from pathlib import Path

    roots = []
    if os.name == "nt":
        for letter in string.ascii_uppercase:
            drive = Path(f"{letter}:\\")
            try:
                if drive.exists():
                    roots.append(str(drive))
            except OSError:
                continue
    else:
        roots.append("/")
    return "\n".join(roots) or str(Path.home())


#: Directory names that are noise wherever they appear, which prefix based
#: exclusions cannot express: these sit inside every project (section 19).
DEFAULT_EXCLUDED_NAMES = (
    "node_modules", ".git", ".hg", ".svn", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", ".tox", ".venv", "venv", "env",
    "site-packages", ".gradle", ".idea", ".vs", "target", "build", "dist",
    ".next", ".nuxt", ".parcel-cache", ".terraform", "Pods", ".DS_Store",
    ".ipynb_checkpoints", "AppData", "Library", ".cache",
    # A coding assistant's own storage, which is the assistant working rather
    # than the user: its session transcripts are already read as conversation
    # by the assistant listener, and reading them twice - once as what was
    # said, once as files churning - records the same thing in two places.
    ".claude", ".codex", ".cursor",
    # An application's private storage, which is the application working
    # rather than the user. These sit in the home directory rather than under
    # AppData, so nothing above reaches them: a chat client rewriting its
    # message database is not the user writing a document, however many
    # events it makes - measured in one fifteen-minute window, a single
    # message index and its journals were most of what was left after
    # everything else had been excluded.
    "xwechat_files", "wechat files", "tencent files", "wechatfiles",
    "qq files", "dingtalk", "lark", "feishu",
)


def default_excluded_names() -> str:
    return chr(10).join(DEFAULT_EXCLUDED_NAMES)


#: Files that churn wherever they are, and are never anybody's work.
#:
#: A directory exclusion cannot reach these: the registry hive log sits in the
#: home directory itself, beside things worth watching. Measured on one
#: machine, that one file was 2,950 of the 3,499 file events in twenty-five
#: minutes - 84% of everything recorded, none of it activity.
#: Windows whose contents are never read, whatever else is switched on.
#:
#: Not a judgement about what anybody does - these are the places where a
#: single careless reading is the whole point of the thing being careless
#: about. A password manager shows credentials in plain text by design; a
#: banking page shows an account number; a private browsing window is
#: private by the user's explicit choice.
DEFAULT_UNREADABLE_WINDOWS = (
    "1password", "bitwarden", "lastpass", "keepass", "dashlane", "nordpass",
    "enpass", "keychain access", "credential manager",
    "private browsing", "incognito", "inprivate",
    "authenticator", "two-factor", "one-time password",
    "bank", "banking", "paypal", "wallet",
)

DEFAULT_EXCLUDED_FILE_PATTERNS = (
    "ntuser.dat*", "usrclass.dat*", "*.log1", "*.log2", "*.blf",
    "*.regtrans-ms", "*.lock", "*.tmp", "*.temp", "*.swp", "*.swx", "*~",
    "*.part", "*.crdownload", "*.partial", "thumbs.db", ".ds_store",
    "desktop.ini", "*.pyc", "*.pyo", "*.sqlite-journal", "*.sqlite-wal",
    "*.sqlite-shm", "*-journal", "*-wal", "*-shm",
    # A coding assistant's own configuration, rewritten every few seconds
    # while it runs. The directory exclusion cannot reach it: it sits in the
    # home directory, beside things worth watching.
    ".claude.json*", ".codex.json*", ".in_use",
)


def default_excluded_file_patterns() -> str:
    return chr(10).join(DEFAULT_EXCLUDED_FILE_PATTERNS)


def default_directory_exclusions() -> str:
    """The noise that makes watching everything viable.

    None of this is the user's activity: it is the machine's, the toolchain's,
    or the agent's own. Shipped as a default so the feature works out of the
    box, and editable like any other setting.
    """
    import os
    from pathlib import Path

    from ..paths import default_root

    home = Path.home()
    candidates = [
        # Section 19, as amended: the agent's own records are not activity,
        # and there is no other way to tell from a file event. Ownership is
        # the right test and it cannot be applied here - the notification
        # arrives up to an hour after the write, long after any claim on it
        # has expired, and most of these notifications are not about writes
        # at all. So the place is the test.
        # THE WHOLE WORKSPACE: it holds only what the agents make and keep -
        # agent_work, the chat's files, the memory library, their scratch -
        # and each run writes from a process of its own, which no claim here
        # could cover. What the user does with what was made is seen by the
        # window and screen listeners all the same.
        default_root() / "workspace",
        default_root() / "internal",              # the agent's own churn
        home / "AppData" / "Local" / "Temp",
        home / "AppData" / "Local" / "Microsoft",
        home / "AppData" / "Roaming" / "Microsoft",
        home / "AppData" / "Local" / "Packages",
        home / "Library" / "Caches",
        home / ".cache",
        home / ".local" / "share" / "Trash",
        home / ".npm",
        home / ".gradle",
        home / ".m2",
        home / ".nuget",
        home / ".conda",
        home / ".vscode" / "extensions",
        home / "node_modules",
    ]
    for name in ("OneDriveTemp", "$RECYCLE.BIN"):
        candidates.append(home / name)
    # Section 19, as amended: watching every drive means the machine's own
    # churn arrives too, and there is a great deal of it - none of it the
    # user's activity. Excluded by default so that watching everything is
    # actually usable rather than nominally true.
    if os.name == "nt":
        system = Path(os.environ.get("SystemDrive", "C:") + "\\")
        candidates.extend([
            system / "Windows", system / "Program Files",
            system / "Program Files (x86)", system / "ProgramData",
            system / "$Recycle.Bin", system / "System Volume Information",
            system / "Recovery",
        ])
    else:
        candidates.extend([Path("/proc"), Path("/sys"), Path("/dev"),
                           Path("/run"), Path("/tmp"), Path("/var/log"),
                           Path("/System"), Path("/Library/Caches")])
    seen, lines = set(), []
    for path in candidates:
        text = str(path)
        key = os.path.normcase(text)
        if key in seen:
            continue
        seen.add(key)
        lines.append(text)
    return chr(10).join(lines)


SETTINGS_SCHEMA: List[SettingField] = [
    # Section 12, as amended: every setting carries an explanation of what it
    # does, and the Settings screen shows it beneath the control.  A label names
    # a setting; only the description tells the user what changing it costs.

    # --- General -----------------------------------------------------------
    # Section 12.2, as amended: one setting, one meaning. There are two
    # questions here and they were being answered by one setting - what the
    # user is *shown*, and what their memory is *findable* in. Keeping them
    # apart is what lets each have a single meaning.
    # THE MODELS, IN ORDER (Settings > General > Models): each call goes to
    # the first that works. Kept as a list of {provider, model}; edited there,
    # not in the form.
    SettingField("model_chain", "Models, in order", "list", [], "Models",
                 "The models the agent thinks with, in order: each call goes to the "
                 "first that works, and passes to the next when one cannot answer - "
                 "a sign-in expired, a key refused, credit or a usage limit used up, "
                 "a client not installed.", hidden=True),
    # WHAT THE AGENTS SEARCH THE WEB WITH: the model's own search where the
    # model has one (Claude Code), else the app's (Brave with a key, or
    # DuckDuckGo's page). Set on the Web search card.
    SettingField("web_search_with", "Web search with", "choice", "model", "Others",
                 "The model's own web search where it has one, or the app's own "
                 "(Brave Search with your key, else DuckDuckGo's page).",
                 choices=["model", "app"], hidden=True),
    SettingField("interface_language", "Interface language", "choice", "en", "General",
                 "The language of this application's own screens. Follows the "
                 "display language now: Chinese screens for Chinese, English "
                 "otherwise.", choices=["en", "zh"], hidden=True),
    SettingField("start_at_login", "Start when I sign in", "bool", True, "General",
                 "Start the agent when you sign in to this computer, the way it was "
                 "last started (the installed program, or run.py). Each system's own "
                 "way, in your own part of it: the Run key on Windows, a LaunchAgent "
                 "on macOS, an autostart entry on Linux. On by default; the installer "
                 "asks on its last page."),
    SettingField("display_language", "Display language", "choice",
                 _default_display(), "General",
                 "The language you see: this application's screens, and "
                 "everything the agent writes for you to read - its replies, "
                 "the notes it raises on its own, its reports and slides. "
                 "Defaults to this computer's language. The agent's own work - "
                 "its notes to itself, the natures it learns, the keywords "
                 "it finds things by - is kept in English, whatever this is. "
                 "What is already stored keeps the words it was written in, "
                 "and quoted material, file paths and code keep their "
                 "original wording. Chinese, English, Spanish, Japanese, Korean, Russian, French, German or Italian.",
                 choices=list(_DISPLAY_CODES)),
    SettingField("language", "Memory language", "multi_choice",
                 ",".join(_PINNED_LANGUAGES), "General",
                 "What the agent's memory is kept and found in: English, fixed "
                 "for now. Not to be set.",
                 choices=list(_PINNED_LANGUAGES), fixed=True, hidden=True),
    SettingField("character", "Avatar", "dynamic_choice", "watermelon", "Avatar",
                 "Which avatar sits on the desktop - how the agent looks, moves and "
                 "follows your pointer with its eyes.",
                 choices_source="characters"),
    SettingField("font_size", "Font size", "int", 14, "General",
                 "Base text size in points for the agent's windows. Larger values "
                 "enlarge every panel's text, not only the conversation.",
                 minimum=8, maximum=32),
    SettingField("agent_name", "Avatar name", "string", "", "Avatar",
                 "What you call your avatar - and what the agent calls itself and "
                 "answers to, in notifications and in conversation. Left empty, it is "
                 "the avatar's own name."),

    # --- Workspace / security ---------------------------------------------
    # Section 110.1: one writable root, chosen at launch, containing both domains.
    SettingField("agent_root", "Data folder", "path", default_agent_root(), "Workspace",
                 "Where everything this agent keeps is: workspace/ (what it makes) and "
                 "internal/ (settings, database, logs, keys). Chosen when it was "
                 "installed - or, run from source, with python run.py --root <folder> - "
                 "and fixed: it is read before any setting is. A different data folder "
                 "is a different agent, with its own calendar, contacts and keys. It is "
                 "never listened to: nothing the agent writes counts as your activity.",
                 readonly=True, restart_required=True, chooser="directory"),
    SettingField("workspace_root", "Agent workspace", "path", default_workspace(), "Workspace",
                 "The only directory any agent-controlled process may write to - "
                 "generated scripts, downloads and task output all land here. "
                 "Read-only: it is always the workspace subdirectory of the agent "
                 "root, so it moves when the root does.",
                 readonly=True, restart_required=True, chooser="directory"),
    SettingField("sandbox_enabled", "OS level sandbox", "bool", True, "Workspace",
                 "Run generated code inside the operating system's own sandbox in "
                 "addition to the agent's path checks. Turning this off leaves only "
                 "the application-level checks between generated code and your files; "
                 "keep it on unless a task genuinely cannot run sandboxed."),
    SettingField("script_timeout_seconds", "Script timeout (s)", "int", 120, "Workspace",
                 "How long a generated script may run before it is killed. Raise it "
                 "for genuinely long jobs; a low value stops a runaway script sooner "
                 "but can cut short work that was still progressing.",
                 minimum=5, maximum=3600),

    # --- Models ------------------------------------------------------------
    SettingField("primary_provider", "Primary provider", "dynamic_choice", "offline", "Models",
                 "Which model service the agent thinks with. 'offline' runs entirely "
                 "on this machine with no account and limited reasoning; other "
                 "providers need credentials configured below.",
                 choices_source="providers", hidden=True),
    SettingField("primary_model", "Primary model", "dynamic_choice", "offline-1", "Models",
                 "The specific model used for everything that has no override below. "
                 "Larger models reason better and cost more per request.",
                 choices_source="models", hidden=True),
    SettingField("primary_reasoning_effort", "Primary reasoning effort", "choice", "default",
                 "Models", hidden=True, description=
                 "How much the primary Codex model thinks before it answers. More "
                 "effort can help with hard questions and takes longer and more "
                 "tokens. 'Use Codex default' leaves it to your Codex configuration, "
                 "or to the model's own default when that says nothing. It goes "
                 "with the primary model: a role with a model of its own, and the "
                 "fallback, keep that model's defaults. Only offered when the "
                 "primary provider is Codex, and reset when the model changes to "
                 "one that does not take the chosen level.",
                 choices=["default", *REASONING_EFFORTS]),
    SettingField("listener_assistant_content_tools", "Record conversations from",
                 "multi_choice", "*", "Listener",
                 "Tick the assistants whose conversations the agent may read: "
                 "what is ticked is recorded, and nothing ticked records nothing. "
                 "By default every assistant found is (stored as *), one installed "
                 "later too. The agent's own calls to an assistant are never read. "
                 "The list is what was found installed on this machine. Recording "
                 "is not all-or-nothing: an assistant used for one kind of work "
                 "can be recorded while another is not. This is the most personal "
                 "thing the application holds; it stays on this machine, and "
                 "turning activity monitoring off stops it like any other collection.",
                 choices_source="assistant_tools"),
    SettingField("listener_assistant_content_projects", "Only these projects",
                 "multiline", "", "Listener",
                 "Which projects' conversations are recorded, one path per line. "
                 "Empty means all of them. A line matches a project that is equal "
                 "to it or inside it, and * may be used - so one line can name a "
                 "whole research directory. This is the setting that keeps work "
                 "the agent has no business reading out of it.",
                 chooser="directory"),
    SettingField("listener_assistant_content_exclusions", "Never these projects",
                 "multiline", "", "Listener",
                 "Projects whose conversations are never recorded, one path per "
                 "line, in the same form as above. Checked after the list of "
                 "projects to record and overrides it, so a directory can be "
                 "recorded with one project inside it held back.",
                 chooser="directory"),
    SettingField("listener_assistant_include_thinking",
                 "Include assistant reasoning", "bool", False, "Listener",
                 "Also record the assistant's thinking blocks, where it has them. "
                 "These are long and mostly restate the reply, so they are excluded "
                 "unless asked for."),
    SettingField("listener_assistant_include_tool_calls",
                 "Include tool calls and results", "bool", False, "Listener",
                 "Also record the tools the assistant ran and what they returned. "
                 "Tool results carry file contents and command output, which is the "
                 "bulkiest and least conversational part of a session."),
    SettingField("listener_assistant_max_chars", "Longest turn recorded", "int", 4000,
                 "Listener",
                 "How much of a single message is kept, in characters. A long turn "
                 "is cut at this point and marked as cut, rather than dropped.",
                 minimum=200, maximum=100000),
    SettingField("states_zone_before", "Time zone before this version",
                 "str", "", "Listener",
                 "Every state now records the time zone you were in when it "
                 "happened. The ones written before that feature existed do "
                 "not, and where you were cannot be read off them - so they "
                 "are read as this zone, once, and marked as assumed. An "
                 "IANA name such as Europe/Lisbon or Asia/Shanghai; empty, "
                 "this computer's own time zone."),
    SettingField("listener_file_excerpt_chars", "Keep file contents", "int",
                 20000, "Listener",
                 "How much of a text file the agent keeps when you read or "
                 "write one, in characters. A path and a time say somebody "
                 "touched fetch.py; they do not say whether they were fixing "
                 "it or reading it to remember what it did. Set to 0 to keep "
                 "no contents at all. Files whose names suggest credentials "
                 "are never read inside, whatever this says.",
                 minimum=0, maximum=20000),
    SettingField("popup_font_size", "Popup text size", "int", 9, "General",
                 "Size of the text in the small windows the agent puts on screen: "
                 "what it says when it speaks to you, and the status readout when "
                 "you hover over it. Larger values are easier to read on a high "
                 "resolution display. Between 7 and 24; the windows grow to fit.",
                 minimum=7, maximum=24),
    SettingField("local_client", "Local Claude client", "dynamic_choice", "auto", "Models",
                 "Which installed Claude client answers when the provider is "
                 "'Claude Code (local)'. A machine can have more than one, and they "
                 "may be signed in to different accounts, so this is chosen rather "
                 "than guessed. 'auto' uses whichever is found. Picking one that is "
                 "not installed tells you so instead of quietly using another. "
                 "Now chosen as a model of its own - Claude Code CLI, or the Claude "
                 "desktop app - in Settings > General > Models.",
                 choices_source="local_clients", hidden=True),
    SettingField("local_client_path", "Local Claude client program", "path", "", "Models",
                 "The client's program file, for one installed somewhere this "
                 "application does not know to look. Empty means: use the client "
                 "chosen above. Setting this overrides that choice.",
                 chooser="application"),
    SettingField("heartbeat_model_override", "Heartbeat model override", "string", "", "Models",
                 "Model used for the periodic background heartbeat instead of the "
                 "primary model. A cheaper model here reduces the cost of work you "
                 "never see. Empty means: use the primary model. Format: provider/model.",
                 hidden=True),
    SettingField("scheduled_task_model_override", "Scheduled task model override", "string", "",
                 "Models",
                 "Model used when a scheduled task runs on its own. Empty means: use "
                 "the primary model. Format: provider/model.", hidden=True),
    SettingField("model_fallback_enabled", "Enable model fallback", "bool", False, "Models",
                 "Allow the agent to switch to the fallback model when the primary one "
                 "is unavailable. Off by default and deliberately so (section 94): with "
                 "it off you are told the model is unavailable rather than being "
                 "silently answered by a different model.", hidden=True),
    SettingField("model_fallback_target", "Fallback model", "string", "", "Models",
                 "The model to fall back to. Has no effect unless model fallback is "
                 "enabled above. Format: provider/model.", hidden=True),
    SettingField("max_output_tokens", "Max output tokens", "int", 4096, "Models",
                 "Ceiling on the length of a single model reply. Raising it allows "
                 "longer answers and costs more per request; too low a value truncates "
                 "long answers mid-thought.",
                 minimum=256, maximum=128000),
    SettingField("credential_storage", "Credential storage", "choice", "keychain", "Models",
                 "Where API keys and tokens are kept. 'keychain' uses the operating "
                 "system's credential store (section 83), which is the safer option but "
                 "does NOT travel when you copy the agent root to another machine. "
                 "'agent_root' keeps them encrypted inside internal/credentials so the "
                 "directory is self-contained and moves with a copy.",
                 choices=["keychain", "agent_root"], restart_required=True),

    # --- Heartbeat ---------------------------------------------------------
    SettingField("heartbeat_enabled", "Enable agent heartbeat", "bool", True, "Heartbeat",
                 "Let the agent wake periodically to review what it knows and decide "
                 "whether anything is worth telling you. Turning this off makes the "
                 "agent purely reactive: it will only ever respond when you speak to it."),
    SettingField("heartbeat_interval_seconds", "How often the agent wakes", "int", 900, "Heartbeat",
                 "How often that review runs. Shorter intervals make the agent notice "
                 "things sooner and cost more. It is also the window the first "
                 "step reads: a review asks what is going on, and the honest "
                 "answer covers everything since it last asked. A run is "
                 "four steps asked one at a "
                 "time, because they ask different questions and answering "
                 "them together divides one act of attention four ways. The "
                 "first of them is a loop rather than a call - it decides "
                 "what it needs to see and asks until it has it - so a run "
                 "is four calls at least and a few more when that step goes "
                 "looking. This is the setting that decides most "
                 "of what the agent costs when nobody is talking to it, and every "
                 "doubling of it halves that. Unlike the reading interval below "
                 "there is no cheap path here: a review is a judgement, and a "
                 "judgement needs a model whether or not anything has changed.",
                 minimum=60, maximum=86400),
    SettingField("heartbeat_state_max_rounds",
                 "How many times it may ask for more", "int", 6, "Heartbeat",
                 "The first step decides for itself whether it has enough to "
                 "answer. It starts with everything that happened since the "
                 "last review - the interval above - and every state running "
                 "now, and it can then search its memory in any folder for "
                 "any words and read records in full. This is how many times "
                 "it may come back for more before it has to answer with "
                 "what it has. A loop that can always ask once more never "
                 "answers; a cap that is too low is an answer given on less "
                 "than the step wanted, which it says when it happens."),
    # --- the state heartbeat (section 34.3) --------------------------------
    SettingField("state_heartbeat_enabled", "Enable state heartbeat", "bool", True,
                 "Heartbeat",
                 "Group the states already on the calendar by what they are about, "
                 "and write one summary per group. The review above reads a few "
                 "minutes at a time, so a fortnight of one project is a hundred "
                 "states, each true and each worded differently - this is what "
                 "notices they are one thing. With it off the states stay as they "
                 "are written and nothing ever says what they add up to."),
    SettingField("state_heartbeat_at_once", "Work on several at once, at most",
                 "int", 3, "Heartbeat",
                 "How many model calls the state heartbeat may have going at "
                 "once - task and project summaries, and each project's stages "
                 "- when they do not depend on each other. The most, not the "
                 "usual: before each call it looks at the computer and leaves "
                 "room for you. At the computer, at most two; half the processor "
                 "left free for you (a quarter while you are away); at least two "
                 "gigabytes or a fifth of memory left free; on battery at most "
                 "two, and one below 30%; one at a time for ten minutes after the "
                 "model refuses a call. 1 is one at a time, always.",
                 minimum=1, maximum=8),
    SettingField("experience_enabled", "Learn from your work", "bool", True,
                 "Heartbeat",
                 "After each state round, write each piece of your work as the "
                 "stages of its loop - goal, plan, execution, evaluation, "
                 "adjustment, delivery - and group what the agent has learnt "
                 "about you into patterns and principles. This is what the agent "
                 "draws on when you start it working on something. A few model "
                 "calls per round."),
    SettingField("experience_reference_keep_days",
                 "Keep unused reference copies for (days)", "int", 30,
                 "Heartbeat",
                 "What the agent saves of a stage's references - the text of an "
                 "online document as you saw it, a screenshot - is kept while a "
                 "stage cites it. One no stage cites any more is deleted once it "
                 "is this many days old, after each round's stages are written, "
                 "so the copies do not fill the disk. Only the agent's own "
                 "copies in the references folder are ever deleted, never your "
                 "files. Set to 0 to keep them all.",
                 minimum=0, maximum=3650),
    SettingField("state_task_min_hours", "Least work a task holds",
                 "float", 2.0, "Heartbeat",
                 "How much work a group of states must add up to before it is a "
                 "task, in hours. Each state counts how long it says it lasted - "
                 "the minutes written with it when it was summarised, at most the "
                 "window it was read from - not the whole span it covers. A state "
                 "from before minutes were written counts its window once. A "
                 "group with less work than this is not made a task, not "
                 "summarised and not shown; this much or more is.",
                 minimum=0.25, maximum=48),
    SettingField("state_community_min_nodes", "Smallest group worth summarising",
                 "int", 5, "Heartbeat",
                 "How many states a group needs before a summary is written for "
                 "it. A handful of states is a handful of states: summarising "
                 "three spends a call to say roughly what the three of them "
                 "already say, and the summary is only worth having once there "
                 "is more of a group than a reader would just read. Smaller "
                 "groups are still found and still shown; they are simply not "
                 "written up.",
                 minimum=2, maximum=50),
    SettingField("state_task_stands_out", "When a task splits in two",
                 "float", 1.15, "Heartbeat",
                 "How far out of line a join has to be, in standard deviations "
                 "of the layer beneath it, before the two things it joined are "
                 "shown as separate sub-tasks. Not a distance: the states are "
                 "joined into a tree whatever this is set to, and this decides "
                 "where that tree is read as one task and where as several. "
                 "1.15 is the value scipy's own documentation uses for cutting a "
                 "tree this way. Lower splits more, until every slight rise is a "
                 "sub-task; higher gives one flat list again. Every task in the "
                 "State heartbeat panel shows its own score, which is where to "
                 "look before changing this.",
                 minimum=0.1, maximum=5.0),
    SettingField("state_task_accept_at", "When a new state joins a task",
                 "float", 1.0, "Heartbeat",
                 "The tasks are not found again from nothing every hour. Each "
                 "state written since the last round joins the task it is "
                 "nearest, if it is within this many standard deviations of how "
                 "far that task's own states already sit from its centre. Not a "
                 "distance: the same distance is generous for a tight task and "
                 "mean for a broad one. 1.0 was chosen by replaying arrivals "
                 "against what rebuilding would have said - at 1.0 the two agree "
                 "0.99 of the time, and every value above 1.5 doubles how far a "
                 "task drifts, which is how a task walks somewhere else without "
                 "any single state ever looking wrong. Higher takes in more and "
                 "blurs the tasks; lower leaves more states matching nothing, "
                 "which eventually forces a rebuild of its own.",
                 minimum=0.25, maximum=5.0),
    SettingField("state_task_drift_limit", "When a task has become something else",
                 "float", 0.05, "Heartbeat",
                 "How far a task's centre may move from where the last rebuild "
                 "left it before the grouping is found again. This is the twist "
                 "that appending is guarding against: a task fed one state at a "
                 "time until it is about something else. Measured against the "
                 "last rebuild rather than against last hour, because drift "
                 "measured against a centre that the drifting itself moves can "
                 "creep forever. Above 0.08 this signal never fired at all in "
                 "the replay, and one task quietly became wrong while the "
                 "average stayed respectable.",
                 minimum=0.01, maximum=1.0),
    SettingField("state_task_loose_is_a_task", "When to look at what fits nowhere",
                 "int", 5, "Heartbeat",
                 "How many states must match no task before the pile is looked "
                 "at. Eight was right when finding out meant rebuilding the "
                 "whole calendar, so the question cost a hundred times what it "
                 "was worth and was worth asking rarely; the pile is now "
                 "clustered among its own members, which is nearly free, so the "
                 "honest threshold is the point at which it could hold a task "
                 "at all - the same five that makes a task anywhere else. What "
                 "stops five strangers becoming a task is no longer this "
                 "number: a candidate has to be about as tight as the tasks "
                 "already accepted, which is the right test and this never was.",
                 minimum=2, maximum=100),
    SettingField("state_task_rebuild_after", "The backstop",
                 "float", 0.2, "Heartbeat",
                 "Find the grouping again when the calendar has grown by this "
                 "share since the last time, whatever the other signals say. It "
                 "is the only signal that cannot be fooled by gradual change, so "
                 "it is set where it still costs a rebuild rather than where it "
                 "is cheapest: on an hourly run over a hundred and twenty states "
                 "it fired four times in sixty rounds, and those four recovered "
                 "seven eighths of the distance between never rebuilding and "
                 "rebuilding every time.",
                 minimum=0.02, maximum=1.0),
    SettingField("state_task_resummarise_after", "When a summary is written again",
                 "float", 0.3, "Heartbeat",
                 "How much of a task's membership must turn over before its "
                 "summary is written again. A task that gained four states is "
                 "the same task and says so; writing its summary again would pay "
                 "a model call to produce the sentence it already has, and word "
                 "it differently - so a reader watching the panel would see the "
                 "description of unchanged work change every hour. Only matters "
                 "when state summaries are on.",
                 minimum=0.05, maximum=2.0),
    SettingField("state_embedding_enabled", "Read states by meaning",
                 "bool", True, "Heartbeat",
                 "Compare states by what they mean rather than by which words "
                 "they share, using a small English model that runs on this "
                 "machine and never sends anything anywhere. One model writes "
                 "every state, in a stable vocabulary, about a handful of "
                 "projects - so two states picked at random already share a "
                 "sixth of their words, and word overlap mostly measures that. "
                 "With this off, or with no model on disk, the grouping falls "
                 "back to word overlap and the panel says so."),
    SettingField("state_embedding_model", "Which model reads them",
                 "str", "sentence-transformers/all-MiniLM-L6-v2", "Heartbeat",
                 "The sentence model used for that comparison. Ninety megabytes, "
                 "English, 384 dimensions, downloaded once and then read from "
                 "disk. English-only on purpose: a multilingual model of the same "
                 "quality is five times the size, and states are written in "
                 "English so that this one is enough."),
    SettingField("state_summaries_enabled", "Summarise each task",
                 "bool", True, "Heartbeat",
                 "Whether a task gets a sentence saying what it is, written by "
                 "the model, or only a list of the states in it. On: a task "
                 "without one is a number and a size, and the whole reason for "
                 "grouping a hundred states was to be told what they add up "
                 "to. It was off while the grouping itself was what was being "
                 "worked on - the tree needs no model at all, and a call per "
                 "task was a minute of waiting for text nobody was reading "
                 "yet - and that is no longer true. What it costs now is one "
                 "call per task on the first grouping and then almost none: a "
                 "task that gained four states keeps the summary it has, and "
                 "only one whose membership has turned over by a third is "
                 "written again.",
                 ),
SettingField("state_heartbeat_max_states", "How many states it groups",
                 "int", 1000, "Heartbeat",
                 "The window the grouping covers. It starts at the first round "
                 "and moves forward: when there are more states than this, the "
                 "oldest fall outside it and the tasks holding them let them go. "
                 "THIS IS THE ONE PLACE THE GROUPING LOSES SOMETHING, so the "
                 "number matters: if more states arrive between two rounds than "
                 "this, the oldest of them are never grouped at all. At the rate "
                 "the review writes them - a few an hour - a thousand is about "
                 "ten days of catching up, so a fortnight away is fine and a "
                 "month away is not. Below a thousand it is easy to lose a "
                 "week; it is a floor to raise, not to lower. "
                 "Every pair is scored when the grouping is found again - the "
                 "square of this, so a thousand is half a million comparisons - "
                 "but that happens four times in sixty rounds, not every hour. "
                 "A thousand takes about eighteen seconds and a hundred and "
                 "twenty megabytes, measured; four hundred, the old value, "
                 "takes three. Raising it is the square: two thousand is four "
                 "times that and five thousand is twenty-five times, which is "
                 "minutes and gigabytes.",
                 minimum=20, maximum=5000),
    SettingField("state_heartbeat_first_delay", "How long the first grouping waits",
                 "int", 60, "Heartbeat",
                 "Seconds after start-up before the first round runs. It used to "
                 "run immediately, which was harmless while a round was a handful "
                 "of states and became the application feeling stuck once it was "
                 "not: the first round is the expensive one - the whole window, "
                 "every pair, and the embedding model loaded from disk - and it "
                 "was competing with the window that was still opening. Worse on "
                 "the way out, where a quit during that round could not interrupt "
                 "it and every heartbeat spent its full join timeout. Short "
                 "rather than the full interval, so a fresh install does not have "
                 "an empty panel until the top of the hour.",
                 minimum=5, maximum=3600),
    SettingField("state_first_round_states",
                 "States needed before the first grouping", "int", 100,
                 "Heartbeat",
                 "WHILE THERE IS NOTHING TO SHOW, this is the whole rule: "
                 "the first grouping waits for this many states the "
                 "grouping has never read, and runs as soon as they are "
                 "there rather than waiting the usual hours. Below it "
                 "nothing runs at all, and the hours do not change that - "
                 "a dozen states grouped into a dozen tasks of one is a "
                 "drawing of a list, which is what grouping exists to "
                 "replace. Once there IS a picture, this stops applying "
                 "and the hours take over.",
                 minimum=1, maximum=100000),
    SettingField("greet_on_start", "Say hello when the agent is up",
                 "bool", False, "General",
                 "A notification when the agent has finished starting. It "
                 "is worth having because the WINDOW now appears before the "
                 "machinery behind it is awake - a listener's first act is "
                 "to ask the operating system what is in front of it, which "
                 "crosses into other processes and takes as long as it takes "
                 "- so the window is no longer evidence that anything is "
                 "being recorded. This is: it is sent when the last part has "
                 "started, and it says how many tasks there are and how many "
                 "states are in none of them, because a greeting that "
                 "answers 'what have you got' is worth reading twice. OFF "
                 "by default: an application that greets you every time you "
                 "open it is one more thing to dismiss, and the window "
                 "appearing is already the news. A failure to start is "
                 "still reported, because that is news the window cannot "
                 "give you - it is up either way."),
    # THE MODULES (common/modules.py): what runs by itself, each of which can
    # be turned off to spend fewer tokens - shown on Settings > Modules.
    SettingField("module_tasks_enabled", "Task organisation", "bool", True, "Modules",
                 "The state heartbeat's tasks and projects, and next steps ticked.",
                 hidden=True),
    SettingField("module_calendar_enabled", "Calendar management", "bool", True, "Modules",
                 "Notifications put on your calendar by the agent, and acted on at once.",
                 hidden=True),
    SettingField("module_connections_enabled", "Connections", "bool", True, "Modules",
                 "Contacts kept from what is on your screen.", hidden=True),
    SettingField("module_recommend_enabled", "Customized Recommendation", "bool", True,
                 "Modules", "The reading log, and an issue at the times you chose - your contacts "
                 "looked up on the web with it.", hidden=True),
    SettingField("module_experience_enabled", "Experience learning", "bool", True, "Modules",
                 "Work natures grouped and learned from by themselves.", hidden=True),
    SettingField("work_in_subprocess", "Each run in a process of its own", "bool", True,
                 "Work",
                 "Each run of your work goes in a process of its own, within what the "
                 "computer has free; off, in the agent's own process.", hidden=True),
    SettingField("recommend_times", "Customized Recommendation, each day at", "string",
                 "08:00", "Heartbeat",
                 "When a new issue of Customized Recommendation is made: one or more "
                 "times of day, in this computer's time zone. Times missed while the "
                 "computer was off make one issue when it is back. None at all: no "
                 "issue by itself, only when asked for.", hidden=True),
    SettingField("state_heartbeat_every_hours", "How often states are grouped",
                 "float", 12.0, "Heartbeat",
                 "How often that grouping runs - THE ONLY NUMBER FOR IT. "
                 "There was a second one, an interval in seconds, and two "
                 "numbers for one thing is a bug waiting to be written: "
                 "whichever the loop slept on, the other would be what the "
                 "panel showed. A round summarises every task it finds, one "
                 "model call at a time, so on a busy folder it is minutes of "
                 "work rather than seconds, and it can afford to be this far "
                 "apart: a group worth summarising six hours ago is still "
                 "worth it now, and a round that finds what the last one "
                 "found has spent its calls saying what it already said. The "
                 "waiting is counted from the last round that FINISHED, and "
                 "an interrupted round is resumed before this is consulted "
                 "at all - half a round leaves tasks reading 'loading', and "
                 "the way out of that is to finish it. It is checked when an "
                 "agent heartbeat ends, and by the grouping's own loop, "
                 "which both go through the same gate.",
                 minimum=0.0167, maximum=168.0),
    SettingField("state_task_deepest_layers", "How many layers of tasks",
                 "int", 3, "Heartbeat",
                 "A task, the tasks inside it, and the tasks inside those. "
                 "EVERY LAYER IS SUMMARISED WORK - the bottom one is the "
                 "bottom because nothing inside it divided again, not "
                 "because it is a list of raw states. Two layers ending in "
                 "the states themselves was the earlier answer, and it put "
                 "two different kinds of thing under one gesture: the same "
                 "click gave a summary in one place and a transcript in "
                 "another, and nothing in the shape of the picture told the "
                 "reader which they were about to get. Three is a tree a "
                 "reader can hold - it also bounds what 'when a task splits "
                 "in two' has to be good at, since that number answers one "
                 "question at each level and not a different one per depth."),
    SettingField("state_first_round_days", "How far back the first grouping looks",
                 "int", 30, "Heartbeat",
                 "With no tasks yet, the first grouping reads this many days back "
                 "and no further. A month, because the question a first grouping "
                 "answers is what have I been doing, and a year of states is not "
                 "that question. What is older is never read and is treated as "
                 "seen - it is before the beginning of this grouping, not a "
                 "backlog waiting to be noticed. Raising this after the first "
                 "round changes nothing; the window has already started.",
                 minimum=1, maximum=3650),
    SettingField("raw_activity_keep_days", "Keep raw activity for (days)",
                 "int", 8, "Heartbeat",
                 "How long the raw activity - what the listener recorded, and "
                 "what the agent did - is kept before it is removed by age. "
                 "Only three things read it: the agent heartbeat (the last few "
                 "hours), the Overview (the last day, and its chart of the "
                 "past seven days) and page visit history. A state keeps its "
                 "own copies of what it relies on when it is written, so rows "
                 "older than this are read by nothing. Eight days is the "
                 "week's chart and a day to spare for time zones. Set to 0 to "
                 "keep everything.",
                 minimum=0, maximum=3650),

    # --- Retrieval (sections 41.1, 44) ------------------------------------

    # --- Retrieval budget (section 44) ------------------------------------

    # --- Listener privacy (section 25) ------------------------------------
    SettingField("listener_enabled", "Enable activity monitoring", "bool", True, "Listener",
                 "The master switch for everything the agent observes. Always on - "
                 "not offered in Settings: what is listened to is chosen one source "
                 "at a time, in Privacy.", hidden=True),
    SettingField("listener_hide_credentials", "Hide passwords and codes", "bool", True,
                 "Listener",
                 "Before anything is kept, passwords, one-time codes and keys are cut "
                 "out of what is recorded - the text on the screen, titles, addresses, "
                 "what was said with an assistant, the words read off a picture - and a "
                 "screenshot showing one is not kept at all. The account they belong to "
                 "is kept, so it can be told whose it was. Decided by keywords and the "
                 "shapes such things have, on this computer: no model is asked, so none "
                 "is ever shown a password."),
    SettingField("listener_application_enabled", "Application monitoring", "bool", True,
                 "Listener",
                 "Record which applications you use and their window titles. This is "
                 "what lets the agent notice what you have been working on."),
    SettingField("listener_filesystem_enabled", "Filesystem monitoring", "bool", True, "Listener",
                 "Watch the directories listed below for files being created, changed "
                 "or removed. File contents are not read; only the fact that a file "
                 "changed is recorded."),
    SettingField("listener_browser_enabled", "Browser monitoring", "bool", True, "Listener",
                 "Read your local browser history so the agent knows what you have been "
                 "reading - the most revealing source there is; only works where the "
                 "platform allows it."),
    SettingField("listener_integrations_enabled", "Application integrations", "bool", True,
                 "Listener",
                 "Allow application-specific plugins, such as the VS Code integration, "
                 "to report richer detail than window titles alone. Each integration "
                 "still has to be installed separately."),
    SettingField("listener_assistant_enabled", "Assistant activity", "bool", True, "Listener",
                 "Record work done through a coding assistant such as Claude Code: which "
                 "tool, which project, when, and how much exchange a session contained. "
                 "What was said is not read unless the assistant is ticked in "
                 "'Record conversations from'; with this alone, prompts and replies "
                 "are not opened."),
    # Section 25, as amended: a control, reached from the agent itself, not a
    # row in a settings list. Stored here so it survives a restart and so the
    # privacy filter reads it like any other value.
    SettingField("listener_watch_paths", "Watched directories", "multiline",
                 default_watch_paths(), "Listener",
                 "Directories the filesystem monitor watches, including everything "
                 "inside them. Defaults to every drive on this machine: the agent "
                 "cannot notice what it is not looking at, and a narrow default would "
                 "leave it reporting itself as running while seeing almost nothing. "
                 "Remove what you would rather it did not see, and add directories "
                 "with the picker - locations are browsed to, not typed.",
                 chooser="directory"),
    SettingField("listener_application_allowlist", "Application allowlist", "multiline", "",
                 "Listener",
                 "If this list is non-empty, only these applications are observed and "
                 "every other application is ignored. Choose applications with the "
                 "picker. Empty means: observe everything that is not denied.",
                 chooser="application"),
    SettingField("listener_application_denylist", "Application denylist", "multiline", "",
                 "Listener",
                 "Applications that are never observed. Choose them with the picker - "
                 "use this for password managers, banking apps or anything else the "
                 "agent should stay out of. The denylist wins over the allowlist.",
                 chooser="application"),
    SettingField("listener_website_allowlist", "Website allowlist", "multiline", "", "Listener",
                 "If this list is non-empty, only these sites are recorded from your "
                 "browsing and everything else is ignored. One domain per line."),
    SettingField("listener_website_denylist", "Website denylist", "multiline", "", "Listener",
                 "Sites that are never recorded, one domain per line. The denylist wins "
                 "over the allowlist."),
    SettingField("listener_directory_exclusions", "Directory exclusions", "multiline",
                 default_directory_exclusions(), "Listener",
                 "Directories the filesystem monitor ignores even when they sit inside "
                 "a watched directory. Comes pre-filled with caches, package and build "
                 "directories and the agent's own files - churn that is not your "
                 "activity - which is what makes watching a whole home directory "
                 "practical. Add your own with the picker.",
                 chooser="directory"),
    SettingField("listener_only_while_present", "Only while you are here", "bool",
                 True, "Listener",
                 "Record file activity only when the keyboard or mouse has been "
                 "used recently. A machine left alone goes on writing files - "
                 "registry hives, caches, sync clients, search indexers - and "
                 "none of that is your activity, however faithfully it is "
                 "recorded. Measured on one installation, 2,950 of the 3,499 file "
                 "events in twenty-five minutes were one Windows registry log. "
                 "Turn this off to record everything the machine does, including "
                 "while you are asleep."),
    SettingField("listener_presence_seconds", "Counted as here for", "int", 300,
                 "Listener",
                 "How long after the last keystroke or mouse movement your "
                 "activity is still assumed to be yours. Long enough to cover "
                 "reading a page without touching anything; short enough that a "
                 "machine left alone stops recording.",
                 minimum=10, maximum=7200),
    SettingField("listener_screen_enabled", "Read what is on the screen",
                 "bool", True, "Listener",
                 "Record the text of the window you are working in, not only "
                 "that you had it open - what the page said, not that a "
                 "browser was open for two hours. This is the only setting "
                 "here that records what was *in* a window rather than which "
                 "window it was, so it is the one to turn off first if you "
                 "would rather it did not. Only the window in front, only "
                 "while you are at the keyboard, never a password field, "
                 "never a window on the list below, and only what changed "
                 "since the last look.", restart_required=True),
    SettingField("listener_screen_interval_seconds", "How often to look",
                 "float", 15.0, "Listener",
                 "Seconds between readings. Reading a busy web page costs "
                 "about a third of a second, and what is read has to be "
                 "understood by a model afterwards - so looking often is "
                 "expensive twice over, and nothing downstream reads any of "
                 "it in real time. What is read between looks is gathered "
                 "into one record per stretch of reading, so a longer gap "
                 "costs detail rather than whole pages.",
                 minimum=2.0, maximum=120.0),
    SettingField("listener_screen_max_chars", "Most to keep from one look",
                 "int", 4000, "Listener",
                 "A long page holds more text than is worth storing every "
                 "few seconds. Past this the end is kept rather than the "
                 "beginning, because what a window shows last is what you "
                 "have just got to.", minimum=200, maximum=40000),
    SettingField("listener_screenshots_enabled",
                 "Take pictures of slides and figures", "bool", True, "Listener",
                 "When the window in front holds more than its text - a slide, a "
                 "figure, a design, a canvas like Google Slides - take a picture "
                 "of that window alone, so what you saw can be a reference for "
                 "your work. Decided by measuring the window, never by a model. "
                 "Never with activity monitoring off or of a window you have excluded from "
                 "being read; pictures no stage of your work cites are deleted "
                 "after a month."),
    SettingField("listener_screenshots_per_day", "Pictures a day, at most",
                 "int", 80, "Listener",
                 "The most pictures of windows taken in one day.",
                 minimum=0, maximum=1000),
    SettingField("listener_screen_excluded_applications",
                 "Windows never to read", "multiline",
                 "\n".join(DEFAULT_UNREADABLE_WINDOWS), "Listener",
                 "One per line, matched anywhere in the application name or "
                 "the window title. This list matters more than any other "
                 "here: a filename recorded by mistake is a filename, and a "
                 "window recorded by mistake is everything that was in it."),
    SettingField("listener_only_what_the_user_did", "Only what you did yourself",
                 "bool", True, "Listener",
                 "Record a file change only when there is evidence a person "
                 "made it: your desktop's own record of the files you opened, "
                 "or a window in front whose title was about that file when it "
                 "happened. Without this, an assistant, a build or a test run "
                 "reading a thousand files is recorded as a thousand things "
                 "you did. Neither test can say a change was *not* yours - a "
                 "file opened from a command line leaves no trace in either - "
                 "so anything that cannot be judged is kept."),
    SettingField("listener_excluded_file_patterns", "Ignored file names",
                 "multiline", default_excluded_file_patterns(), "Listener",
                 "File names the monitor never records, as patterns, one per "
                 "line. Directories are excluded above; this is for the files "
                 "that churn wherever they are - a registry hive log rewritten "
                 "every few seconds, a lock file, an editor's backup. What is "
                 "here is noise on every machine, not a judgement about your "
                 "work."),
    SettingField("listener_excluded_names", "Ignored folder names", "multiline",
                 default_excluded_names(), "Listener",
                 "Folder names the filesystem monitor ignores wherever they appear, "
                 "one per line. These sit inside every project - dependency folders, "
                 "build output, version-control internals, caches - and are churn "
                 "rather than your activity. Removing a name here starts recording "
                 "that folder's changes everywhere it occurs."),
    SettingField("listener_poll_interval_seconds", "Application poll interval (s)", "float", 2.0,
                 "Listener",
                 "How often the active application is checked. Shorter intervals catch "
                 "brief switches between applications and use slightly more CPU.",
                 minimum=0.5, maximum=60.0),

    # --- Notifications -----------------------------------------------------
    SettingField("notifications_enabled", "Enable notifications", "bool", True, "Notifications",
                 "The master switch for the agent telling you something unprompted. "
                 "With this off the agent still does its work, but you will only see "
                 "the result when you open a conversation yourself."),
    SettingField("proactive_conversation_enabled", "Allow proactive messages", "bool", True,
                 "Notifications",
                 "Let the agent raise something on its own when it has something worth "
                 "saying (section 7.4), rather than only replying to you. It says it in "
                 "a bubble beside the character, in a warmer colour than an ordinary "
                 "one; it never opens a window over what you are doing. Switched off, "
                 "such messages still appear in the notification list - you simply are "
                 "not shown them at the time."),
    SettingField("notification_cooldown_seconds", "Repeat warning cooldown (s)", "int", 3600,
                 "Notifications",
                 "How long the agent must wait before telling you the same thing again. "
                 "Raising it makes a persistent problem less repetitive; lowering it "
                 "risks the same warning arriving repeatedly. Section 91 deduplication "
                 "window.",
                 minimum=60, maximum=604800),
    SettingField("quiet_hours", "Quiet hours", "string", "", "Notifications",
                 "A daily period during which nothing is delivered; anything raised "
                 "then waits until the period ends. Example: 22:00-08:00. Empty "
                 "disables quiet hours."),

    # --- Scheduler ---------------------------------------------------------
    SettingField("scheduler_enabled", "Enable scheduler", "bool", True, "Scheduler",
                 "Run tasks at their scheduled time. With this off, scheduled tasks are "
                 "kept but never fire, and anything due while it was off is not run "
                 "retroactively."),
    SettingField("scheduler_tick_seconds", "Scheduler tick (s)", "int", 60, "Scheduler",
                 "How often the scheduler checks whether a task is due, which also sets "
                 "how late a task can start. Section 57 target frequency: about once "
                 "per minute.",
                 minimum=5, maximum=3600),

    # --- Data management ---------------------------------------------------
    # Section 50.2, as amended: folding what has gone cold into summaries that
    # point back at it.

    # --- API / UI ----------------------------------------------------------
    SettingField("api_host", "API host", "string", "127.0.0.1", "System",
                 "Network address the local API listens on. 127.0.0.1 means this "
                 "machine only; changing it exposes the agent to your network, which "
                 "also exposes everything it knows.",
                 restart_required=True),
    SettingField("api_port", "API port", "int", 8756, "System",
                 "Port the local API and the web interface listen on. Change it only "
                 "if another program already uses this port.",
                 restart_required=True, minimum=1024, maximum=65535),
    SettingField("log_level", "Log level", "choice", "INFO", "System",
                 "How much detail is written to the log files in internal/logs. DEBUG "
                 "is for diagnosing a problem and produces very large logs; secrets are "
                 "redacted at every level.",
                 choices=["DEBUG", "INFO", "WARNING", "ERROR"]),

    # --- Debugger ----------------------------------------------------------
    SettingField("debugger_enabled", "Collect debugger data", "bool", True, "Debugger",
                 "The master switch for the Debugger's own monitoring. With this off "
                 "no traces are recorded and the panels have nothing to show, because "
                 "nothing was collected - the agent goes on working exactly as before. "
                 "Turning it back on starts collection again; it cannot recover what "
                 "was not gathered while it was off."),
]

SCHEMA_BY_KEY: Dict[str, SettingField] = {f.key: f for f in SETTINGS_SCHEMA}

_COERCERS: Dict[str, Callable[[Any], Any]] = {
    "int": lambda v: int(v),
    "float": lambda v: float(v),
    "bool": lambda v: v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "on"),
    "string": lambda v: "" if v is None else str(v),
    "multiline": lambda v: "" if v is None else str(v),
    "path": lambda v: str(v),
    "choice": lambda v: v,
    "dynamic_choice": lambda v: v,
    # Stored as one comma-separated string, which is what the multi-select
    # writes. Normalised on the way in so that ordering, spacing and
    # duplicates cannot make two equal settings look different.
    "multi_choice": lambda v: (
        "" if v is None
        else ",".join(dict.fromkeys(
            piece.strip() for piece in
            (v if isinstance(v, str) else ",".join(str(x) for x in v))
            .replace(";", ",").split(",") if piece.strip()))),
}


def coerce(key: str, value: Any) -> Any:
    field_def = SCHEMA_BY_KEY.get(key)
    if field_def is None:
        return value
    coercer = _COERCERS.get(field_def.type, lambda v: v)
    coerced = coercer(value)
    if field_def.minimum is not None and isinstance(coerced, (int, float)):
        coerced = max(coerced, type(coerced)(field_def.minimum))
    if field_def.maximum is not None and isinstance(coerced, (int, float)):
        coerced = min(coerced, type(coerced)(field_def.maximum))
    if field_def.type == "choice" and field_def.choices and coerced not in field_def.choices:
        raise ValueError(f"{key}: {coerced!r} is not one of {field_def.choices}")
    return coerced


def defaults() -> Dict[str, Any]:
    return {f.key: f.default for f in SETTINGS_SCHEMA}


def schema_as_dicts() -> List[Dict[str, Any]]:
    return [f.to_dict() for f in SETTINGS_SCHEMA]
