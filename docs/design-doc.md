<!-- Personalized Agent - design specification.

     This file is the editable source of
     'Design doc for personalized agent (English).html'.
     Edit it, then run:  python docs/build_reader.py

     The metadata comment under each heading carries the section's
     number, anchor and original page; leave it in place when editing
     prose. To add a section, copy an existing heading and give it a
     new number and id. Anything that differs from the original
     specification is marked as an amendment in the reader
     automatically - there is no flag to set by hand.
-->

# Personalized Agent System Design and Implementation Specification
<!-- number:  | label:  | id: title | page: 1 -->

Document Status: Draft Architecture Specification

Target Platforms: Windows, macOS, Linux

Primary Backend Language: Python

Purpose: Implementation specification for an AI-powered personalized desktop agent

# 1. Introduction
<!-- number: 1 | label: 1. | id: s1 | page: 1 -->

# 1.1 Purpose
<!-- number: 1.1 | label: 1.1 | id: s1-1 | page: 1 -->

The Personalized Agent is a cross-platform desktop AI agent designed to continuously understand user activity, maintain structured long-term contextual knowledge, proactively assist the user, and execute immediate or delayed tasks.

The system combines:

- A desktop agent interface
- User activity monitoring
- Long-term memory kept as folders of readable files (the library)
- Periodic AI reasoning through heartbeat processes
- A delayed-task scheduler
- Extensible integrations with desktop applications through the local application protocol
- Work the user delegates, planned with the user and then carried out in a separate process
- Strong filesystem and execution security boundaries
- Cross-platform operating-system abstraction

The agent should behave as a persistent personal assistant while remaining observable, configurable, and controllable by the user.

# 2. Core Design Principles
<!-- number: 2 | label: 2. | id: s2 | page: 1 -->

The implementation must follow the principles below.

# 2.1 Modular Architecture
<!-- number: 2.1 | label: 2.1 | id: s2-1 | page: 2 -->

Major system responsibilities must be separated into independent modules.

At minimum:

- Frontend Agent
- Overview
- States / Settings
- Listener
- Heartbeat
- State Heartbeat
- Data Management (the library)
- Scheduler
- Work (delegated runs)
- Lessons
- Platform Abstraction
- Runtime / Sandbox
- External Application Protocol

Each function the agent performs by itself - task organisation, calendar management, connections, Customized Recommendation, experience learning - is a module the user can switch off in Settings > Functions; the core of activity recording and state writing is always on.

Modules should communicate through clearly defined interfaces rather than directly depending on each other's internal implementation.

# 2.2 Cross-Platform by Design
<!-- number: 2.2 | label: 2.2 | id: s2-2 | page: 2 -->

Windows, macOS, and Linux are first-class supported platforms.

Operating-system-specific behavior must be isolated behind a platform abstraction layer.

Higher-level modules must not contain scattered platform checks such as:

if platform == "windows":

```
...
```

elif platform == "macos":

```
...
```

unless located inside the platform-specific implementation layer.

# 2.3 Security by Enforcement
<!-- number: 2.3 | label: 2.3 | id: s2-3 | page: 3 -->

Security-sensitive requirements must be enforced programmatically.

They must never rely exclusively on model instructions such as:

Do not write outside the user workspace.

The most important system security invariant is:

> No process controlled by the Personalized Agent may write outside the user-authorized writable workspace.

The authorized area is the workspace plus any folder the user explicitly grants to a piece of work. A grant is made by the user, names one folder, can never be a whole drive, the home folder itself or the application's own state, and is enforced by the runtime exactly as the workspace is.

This requirement applies to:

- The main agent
- Model-generated scripts
- Scheduler scripts
- Subprocesses
- Temporary files
- Downloads
- Archive extraction
- Tool execution
- Model-generated code
- Child processes

Whenever technically possible, this should be enforced at both:

1. Application level
2. Operating-system or sandbox level

# 2.4 Event Collection and Interpretation Must Be Separate
<!-- number: 2.4 | label: 2.4 | id: s2-4 | page: 3 -->

The Listener records what happened.

The heartbeats determine what the event means: the agent heartbeat reads the recent window of activity and writes states, and the state heartbeat groups states into tasks.

Listener implementations must not directly modify the library.

Required flow:

User / Computer Activity

```
|
v
```

Listener Provider

```
|
v
```

Normalized Event

```
|
v
```

Raw Event Storage

```
|
v
```

Agent Heartbeat (writes states)

```
|
v
```

State Heartbeat (groups states into tasks)

```
|
v
```

Library

Raw activity is kept for eight days by default (`raw_activity_keep_days`) and then removed.

# 2.5 Expensive Knowledge Processing Must Be Asynchronous
<!-- number: 2.5 | label: 2.5 | id: s2-5 | page: 4 -->

Foreground agent interactions should remain responsive.

Expensive operations such as:

- Knowledge aggregation
- Relationship generation
- Large-content compression
- Grouping states into tasks, and embedding them

should normally be deferred to background work on its own clock - the state heartbeat, the Customized Recommendation clock - or to a child process, as the embedding model and every work run are. Work runs are admitted, slowed and paused by a resource governor so that the user's own programs come first.

# 2.6 User Control
<!-- number: 2.6 | label: 2.6 | id: s2-6 | page: 5 -->

The user must retain visibility and control over:

- Which activity sources are recorded (one at a time, in Privacy)
- Agent configuration
- Heartbeat configuration
- Models
- Scheduled tasks
- Agent identity
- What the agent remembers (the library's files, readable and editable)
- Which background functions run (Settings > Functions)
- Runtime resource consumption
- Listener permissions

# 3. High-Level Architecture
<!-- number: 3 | label: 3. | id: s3 | page: 5 -->

The system consists of the following major components:

```
                 +----------------------+
                 |      Desktop UI      |
                 |                      |
                 | Agent / Overview /   |
                 | Calendar /           |
                 | Connections /        |
                 | Experience / Lessons |
                 | / Chat / Settings    |
                 +----------+-----------+
                            |
                            v
                 +----------------------+
                 |      Agent Core      |
                 +----------+-----------+
                            |
       +--------------------+--------------------+
       |                    |                    |
       v                    v                    v
+-------------+      +-------------+      +-------------+
|  Scheduler  |      |  Heartbeat  |      |   Listener  |
+-------------+      +-------------+      +------+------+
                                                 |
+-------------+                                  v
|  Work runs  |                       +-------------------+
| (child      |                       | Normalized Events |
|  processes, |                       +---------+---------+
|  governor)  |                                 |
+-------------+                                 v
                                      +-------------------+
                                      | Raw Event / Edit  |
                                      |      Storage      |
                                      +---------+---------+
                                                |
                                                v
                                      +-------------------+
                                      | Agent Heartbeat / |
                                      | State Heartbeat   |
                                      +---------+---------+
                                                |
                                                v
                                      +-------------------+
                                      | Library (states,  |
                                      | tasks, user       |
                                      | nature, feedback) |
                                      +-------------------+
```

The page also carries a Customized Recommendation tab, and, in a build that carries the debuggers, the Debugger and the per-part debugger tabs (section 14).

```
Platform-specific behavior:
        |
        v
+-------------------+
| Platform Layer    |
| Win/macOS/Linux   |
+-------------------+
```

```
Execution:
        |
        v
+-------------------+
| Runtime / Sandbox |
+-------------------+
```

# 4. Recommended Project Structure
<!-- number: 4 | label: 4. | id: s4 | page: 7 -->

/

├── agent/

│   ├── character/

│   ├── overview/

│   ├── state/

│   ├── dialog/

│   └── core/

│

├── listener/

│   ├── core/

│   ├── browser/

│   ├── filesystem/

│   ├── application/

│   ├── integrations/

│   └── providers/

│

├── heartbeat/

│   ├── core/

│   ├── checklist/

│   └── models/

│

├── data_manage/

│   ├── graph/

│   ├── storage/

│   ├── retrieval/

│   ├── raw_edit/

│   └── data_heartbeat/

│

├── scheduler/

│   ├── core/

│   ├── tasks/

│   ├── storage/

│   └── execution/

│

├── platform/

│   ├── base/

│   ├── windows/

│   ├── macos/

│   └── linux/

│

├── runtime/

│   ├── sandbox/

│   ├── filesystem/

│   ├── process/

│   └── permissions/

│

├── protocol/

│   ├── identity/

│   ├── authentication/

│   └── permissions/

│

├── common/

│   ├── config/

│   ├── events/

│   ├── logging/

│   └── models/

│

├── tests/

│

├── requirements.txt

└── README.md

Additional modules may be introduced where necessary.

The architecture matters more than exact folder naming.

The current tree differs from this first sketch. Memory is `data_manage/library/`, beside `raw_edit/` and `storage/`; the graph is kept unused in the top-level `deprecated/`, and there is no retrieval package and no data heartbeat. The listener holds `core/`, `providers/` (application, assistant, browser, filesystem, screen, screenshot) and `integrations/`. The heartbeats live in `heartbeat/core` and `heartbeat/states`, beside `checklist/`, `connections/`, `experience/`, `lessons/` and `reading/`. Delegated work is `agent/work/`. The platform package is `platform_layer/`. The web interface and its local API are `api/`, and `debugger/`, `models/`, `ui/` (desktop window, tray, selection button) and `resources/` are packages of their own.

# 5. Frontend Agent Module
<!-- number: 5 | label: 5. | id: s5 | page: 10 -->

Location:

```
./agent/
```

The Agent module provides the user's primary interaction point.

# 6. Character System
<!-- number: 6 | label: 6. | id: s6 | page: 11 -->

Location:

```
./agent/character/
```

The agent may support multiple visual characters.

Character appearances should be constructed from image assets.

The initial implementation does not require dynamically generated 3D models.

The architecture should allow additional character packages to be installed later.

A character package may contain:

```
character_name/
    manifest.json
    idle/
    talking/
    thinking/
    notification/
    assets/
```

The exact animation system may vary depending on the frontend framework.

A package may instead carry a rig - layers plus face geometry - that the application animates itself. The shipped avatar, Watermelon, works this way, and it is the default and the only one shipped: its body and leaves are painted once and its face is drawn every frame, so its eyes follow the pointer, it blinks, and its face answers what the agent is doing - a smile on hover, a hop on a click, a moving mouth while talking, thought bubbles while thinking. Its cheeks turn pink only for a notification, not when it smiles on hover, so that the colour means something has arrived. The settings page draws the same face in SVG from the same rig.

What the agent and its avatar are called is the name the user gives; where they give none, it is the avatar's own name.

# 7. Agent Desktop Interaction
<!-- number: 7 | label: 7. | id: s7 | page: 11 -->

The desktop agent supports the following interactions.

# 7.1 Single Click
<!-- number: 7.1 | label: 7.1 | id: s7-1 | page: 11 -->

Single-clicking the agent opens the conversation interface.

The user may enter natural-language instructions.

The conversation the agent window opens is a conversation: consecutive messages belong to one thread, and the agent answers each with the previous ones in view.

It is the same conversation the rest of the application shows. There is one agent, one memory and one set of conversations; the window on the desktop and the conversation interface inside the application are two ways into the same thing, not two agents.

> A window that starts a new conversation for each message is not a conversation interface. It answers every message as though it were the first.

The conversation opens as the application's own conversation interface, in a window showing that and nothing else - not a second conversation interface built again in the desktop toolkit.

There is one conversation interface in the system, and building a second one in a different toolkit produces two that drift apart: different rendering, different behaviour, and defects that have to be found and fixed twice. It also inherits whatever that toolkit imposes - a borderless, always-on-top character window is not a window the system activates, so a dialog owned by it may open and then refuse the keyboard entirely.

The window opened from the agent shows the conversation alone. Reaching the rest of the application is the double click and the right-click menu; the conversation is what a single click is for.

Quitting is likewise not queried. A user who chooses Quit from the menu has already said what they want, and nothing is lost by it: state is persisted as it is produced, and starting the agent again restores it. A confirmation adds a step and no information.

# 7.2 Double Click
<!-- number: 7.2 | label: 7.2 | id: s7-2 | page: 12 -->

Double-clicking the agent opens the Overview interface.

# 7.3 Right Click
<!-- number: 7.3 | label: 7.3 | id: s7-3 | page: 12 -->

Right-clicking the agent opens a short menu: Delegate work, Chat, Overview, Hide avatar and Quit. The tray icon offers the same menu, which is how a hidden avatar is brought back. Settings and the other panels are tabs of the page that Overview opens.

# 7.4 Proactive Conversation
<!-- number: 7.4 | label: 7.4 | id: s7-4 | page: 12 -->

The agent may proactively open a conversation interface when it determines that relevant information should be communicated to the user.

Examples may include:

- Reminders
- Important detected events
- Suggestions
- Completed scheduled tasks
- Agent-generated recommendations

Proactive interruption behavior should be configurable.

# 7.4.1 A Bubble, and It Waits
<!-- number: 7.4.1 | label: 7.4.1 | id: s7-4-1 | page: 12 -->

Everything the agent raises on its own initiative appears as a bubble beside the character. It does not open a window and it does not take focus.

The shape is the whole point. A window arrives in front of whatever the user is doing and has to be dealt with before they can carry on; a bubble sits beside the character and can be left there. The agent's judgement about what is worth saying is not reliable enough to justify the first, and section 52.4 exists precisely because that judgement has to be checked rather than trusted.

**Bubbles queue.** One is shown at a time.

- A bubble carries the choices it is asking about and stays until one is chosen. It does not time out and does not dismiss itself, because a proactive message that vanishes unanswered is an interruption that bought nothing
- While one is waiting the rest are counted, and that count is kept current as more arrive - a number drawn once when the bubble opened is wrong by the time it is read
- The next bubble appears only when the current one has been answered

A bubble the agent raised by itself is marked as such, with a background of its own. "The agent noticed something" and "the thing you asked for is finished" deserve different reactions, and a user who cannot tell them apart gives both the same one.

# 7.5 Hovering
<!-- number: 7.5 | label: 7.5 | id: s7-5 | page: 12 -->

Resting the pointer on the agent shows its current state, without clicking and without opening a panel.

The agent runs on its own schedule and the user cannot otherwise see it. What is shown must therefore be what is happening now and what is about to happen:

- The agent's name
- The model in use, and whether it is currently usable
- When the state heartbeat last grouped what the user did, when it will next, and how many tasks it is working through
- When the agent heartbeat last ran and how long until the next one
- The next scheduled task, and when it is due
- Every piece of delegated work in progress: its name, where it stands, how long it has run and what it has spent
- Whether activity recording is off
- Anything currently requiring the user: an expired sign-in, a model that cannot be used

Times are shown as durations from now - "in 4 minutes", "12 minutes ago" - because that is the question being asked. A timestamp the user has to subtract from the current time does not answer it.

> A component that is disabled says so. Showing nothing where a countdown belongs is indistinguishable from a countdown that has stopped.

The display is placed where it can be read: beside the agent on whichever side has room, and always fully on screen. The agent is usually at an edge of the display - that is where such a thing is kept - so a fixed offset from it puts the readout off the screen.

The text in it is sized by the user. The same size governs everything the agent puts on screen in its own small windows - this readout, and what it says when it speaks - because they are read in the same conditions, at whatever distance and resolution the user's display happens to have. A size fixed by the application is a size that is right for some displays and unreadable on others, and the user cannot argue with it.

- The size is a setting, with a range that keeps the windows usable
- A window is sized from its text, not fixed: a larger size inside a fixed window only cuts the message off, which defeats setting it
- A new size applies to the next window shown, without restarting the application
- A value outside the range, however it got into the settings, is brought back into it rather than allowed to break the window

The display updates while it remains open, and disappears when the pointer leaves. It is built from state the application already holds and must not itself cause work - no model calls, no network, no processing - because pointing at the agent must never change what the agent does.

# 8. Overview Module
<!-- number: 8 | label: 8. | id: s8 | page: 12 -->
<!-- note: 2026-10-16: the user asked that what lives in the Debugger stay there: with no Debugger panel, computer usage (total time, activity by hour, usage by application) is not shown. -->

Location:

```
./agent/overview/
```

The Overview is a front page of what the user needs to know: the tasks the agent has grouped from their activity, with the to-dos beside them; what the models spent, by kind of work; and the lessons package. The Calendar is a tab of its own. The record of computer usage (section 9) sits in the Debugger in a build that carries the debuggers, and at the foot of the Overview in a build without them, such as the released installers.

*Amended 2026-10-16:* the record of computer usage is the Debugger's alone. A build without the Debugger shows it nowhere - not at the foot of the Overview - for the hours spent in each application are the agent's raw material, not something the user needs to know.

# 9. Activity Overview
<!-- number: 9 | label: 9. | id: s9 | page: 13 -->

The Activity Overview displays how the user has used the computer.

The panel should include:

- Total computer usage time
- Usage time by application
- Usage distribution
- Recent applications
- Historical activity

Selecting an application should allow the user to inspect related activity logs.

For example:

Google Chrome

```
2h 17m
```

VS Code

```
1h 42m
```

Slack

```
53m
```

Clicking an application can expose associated events such as:

- Active periods
- Window activity
- Browser navigation where applicable
- File interaction where available

The frontend must obtain these records from backend APIs rather than accessing Listener storage directly.

## A Client That Went Away

The panel refreshes on a timer and reloads whenever the user navigates, so a request still being answered when the tab closes is ordinary and happens several times an hour. Writing the answer to a socket nobody is holding fails, and that is not a fault.

What made it look like one was what happened next. The failed write was treated as a failed request, so the handler tried to send a 500 - down the same closed socket, which failed the same way, with nothing left to catch it. What reached the user was two stack traces, one nested inside the other, naming neither the route nor the reason.

So a write with nobody to write to ends the exchange rather than starting an error response, and is logged as what it is. The quiet is for that one named family of connection errors: anything else still reports, still returns a 500, and still gets its traceback in the log.

## Period Accounting

Every figure the Overview shows must state the period it covers, and must cover only that period.

> A per-day figure is that day's activity. A window bounded only at its start is a running total, and a series of such windows reports the same number for every entry.

Historical activity is reported per calendar day in the user's own timezone. A day with no recorded activity reports zero rather than being omitted, so a gap is visible as a gap.

## Activity Over Time

The Overview must show how activity was distributed through the day, not only what it totalled.

A time series over the last day is required, resolved to the hour, showing which applications were in use in each hour rather than a single undifferentiated total. Applications must be distinguishable from one another, and the distinction must be labelled.

A period shown by time of day must also say which day, because a window of the last twenty-four hours crosses midnight: an hour labelled only "09:00" is ambiguous between today and yesterday. The period covered must be stated, and where the series spans more than one date, the point at which the date changes must be identifiable within it.

## Pages and Files

The Overview reports what the user actually did, not only which application they did it in.

Where the Listener records browsing, the Overview must show the individual pages visited - their titles and their addresses - and not only that a browser was in use. Where it records filesystem activity, the Overview must show the individual files worked on - their names and their locations - and not only that an editor was open.

> "Chrome, 2h 17m" does not tell the user what they read. "VS Code, 1h 42m" does not tell them what they worked on. The panel is a record of activity, and an application name is the least specific thing it knows.

Each must be presented as its own view, covering the same period as the rest of the panel:

- Pages: title, address, how many visits, and when it was last visited
- Files: name, containing location, what happened to it, how many changes, and when it last changed
- Both ordered by how much they were used, not alphabetically or by chance
- Both able to say that nothing was recorded, distinguishably from having recorded nothing

This displays only what the Listener already collected. It grants no new collection: the settings of section 25 decide what exists to be shown, a source that is disabled contributes nothing, and a page or file excluded by an allowlist or denylist, or recorded while the user was away, must not appear here. Where a source is off, the panel must say that this is why it is empty, rather than presenting an empty list as though nothing happened.

## Selection

Selecting an application is a persistent choice, not a momentary one:

- The selected application must remain selected, and its inspected activity must remain displayed, when the panel refreshes on its own
- This applies to whatever the user has opened, not only to the application. A panel that refreshes on a timer and rebuilds the application's log over an open page takes the user back out of it while they are reading it - which, from where they sit, is the panel closing itself for no reason they can see
- The current selection must be visually distinguishable in the list it was made from
- Only elements that respond to selection may present themselves as selectable; a read-out must not offer the affordance of a control
- Inspected activity must be presented as readable rows, not as raw stored records
- A selection the user made is one they can undo. There must be a way to select nothing and return to looking at everything, without having to select something else instead
- What a selection reveals is shown beside the list it was made from, so that selecting and seeing the result are one glance rather than two. A result placed a screen away from its control reads as nothing having happened
- The two are given room in proportion to what they hold: the list is a column of names, the inspected activity is a log. Where the window is too narrow for both, they stack rather than being squeezed, and nothing in the interface refers to where the panel sits, because that changes with the width

## Opening a Row Is Not Selecting It

An application in the usage list can be opened where it stands, to show what it was used for, without becoming the selected one.

The two are different questions. "What sites was the browser on?" is a glance; "show me everything about the browser" is a commitment that replaces whatever the inspection panel was showing. A single control cannot serve both, because then every glance costs the user the thing they were already looking at.

- The row carries two controls: a small arrow that opens it in place, and the name, which selects it
- Opening a row changes nothing else on the panel. The inspection panel keeps whatever it was showing, and no selection is made
- What a row opens onto is what that application actually recorded - the sites for a browser, the files for an editor - and which of those it is follows the events, not a list of known program names, so an application nobody has classified still shows whatever it did record
- A row with nothing to open onto has no arrow. An arrow that opens onto nothing is a promise the row cannot keep
- Each entry says how much it accounts for, ordered by that, and a page listed there opens in its own right just as it does anywhere else

## One Site Inside an Application

A site opened from an application's row can itself be chosen, and the log then shows only what happened on it.

Asking about one site is a narrower version of the same question, not a different one, so the answer keeps its shape: the same stretches of use, carrying only what belongs to that site.

- The log says plainly that it is narrowed, names the site, and offers the way back to the whole application
- A stretch of use that never touched the site is absent from the answer, not shown as an empty stretch. An empty stretch would say the user was there and did nothing, which is not what happened
- Files belong to the application, not to the site, so a view of one site does not carry them
- The site being viewed is marked in the list it was chosen from
- Choosing the site already in view widens the log again, the same way choosing the selected application clears it
- A site with nothing recorded narrows to nothing, and says so

## Watched by Default, Narrowed by Choice

The filesystem monitor watches every drive on the machine by default, and the user removes what they would rather it did not see.

The agent cannot notice what it is not looking at. A narrow default leaves the feature reporting itself as running while recording almost nothing - which is worse than being off, because the user believes their work is being seen when it is not.

- The default is everything, and narrowing it is a decision the user makes rather than one made quietly for them
- What is watched is visible as one list, so what the agent can see is answerable at a glance
- Watching everything is only usable because the exclusions carry the churn that would otherwise drown it: the machine's own directories, package and build caches, and the agent's own files. None of that is the user's activity
- A directory is **browsed to, not typed**. A typed path can be misspelt, can name something that is not a directory, and can be silently wrong for as long as nobody checks. Typing reappears only on a machine where no chooser can run, and says that is why

> A stored setting does not change when its default does. A value written by an older version stays until something changes it, so a default corrected later leaves existing installations on the old value - visibly configured, invisibly wrong.

## Which Application a Page or a File Belongs To

Joining recorded activity to the application it belongs to is not a matter of comparing the names as they were stored, because the sources do not agree on them.

Time is counted under the process name the system reports. Browsing is recorded under the name of the history source it was read from. A file change names no application at all, because the filesystem reports a change, not a culprit. Joined literally, these never meet - every row would offer nothing to open, and the panel would look empty while holding the data.

- One application is one row whatever spelling reached it. The differences are of case and of the executable suffix, and nothing else, so they are removed before matching
- Only the names the usage list is actually keyed by are join targets. Matching against every name seen anywhere lets a differently-spelled source claim the row and file its activity under one that does not exist
- The same attribution governs the inspected activity, not only the usage list. Asking the store for one application by name returns only what was recorded under that exact name - the focus events, and nothing else - so a stretch of use gathered that way is always empty of the pages and files that made it up
- Activity that names no application belongs to whichever application was in focus when it happened. This is an inference, and it is the honest one available: it is right for the case that matters - the user was working in the editor when the file changed - and where nothing was in focus it declines to guess rather than attributing the activity at random

## What an Interaction Consisted Of

Inspecting an application shows the stretches of use it was actually made of, and what was open during each.

A list of durations answers "how long" and never "doing what", which is the question someone opens this panel with. The pages and files therefore belong to the stretch of use they happened in, rather than sitting in a separate list the user has to line timestamps up against by hand.

- Each stretch of use is shown with when it ran, how long it lasted, and how much was recorded during it
- What was recorded during a stretch is revealed on request, not all at once. A day's activity expanded in full is not a record anyone can read
- A stretch with nothing recorded in it says so, and does not offer to be opened onto nothing
- Work done through a coding assistant is the interaction, and it belongs to the stretch it ran in. A terminal is only where it happened: a stretch of terminal use reporting "nothing recorded" while a session ran inside it is describing the window rather than the work. What was said is shown where the conversation was recorded (section 18, as amended), and the shape of the session where it was not
- A session is re-reported each time it grows, so one stretch can hold the same session many times over. It is summarised once. What was *said* is different every time and is all kept
- An application that opens no pages and touches no watched file has still been doing something, and what it was doing is in its **window title**. A terminal, a chat window, a slide deck: the title names the project, the command or the document. Without it, such a stretch reports "nothing recorded" when the record existed and was simply not being looked at
- **A stretch ends when the user leaves the application, not when the window title changes.** The title was never the boundary: it is what varied *inside* the stretch, and it is listed there. Bounded by the title instead, switching tab ended one stretch of browsing and began another - measured on one machine, **127 stretches of the browser, 81% of them under thirty seconds, median six**, and seventeen stretches of a second browser for two minutes of use. Expanded, each held a line or two, which is not a record of an evening. Under the same rule they become eight and three
- **Stretches that overlap are joined.** A window's reported duration covers time the next window of the same application also reports, because the title changing is what ends one and begins the other: 90 of 217 consecutive stretches overlapped on that machine, so a rule that joined only stretches separated by a gap left nearly half of them apart for the one reason that is not a reason
- **The time spent is the union, and the span is a separate fact.** A stretch shared with another window spans the moments spent elsewhere in the middle of it, so end-minus-start counts another application's time as this one's, and adding the pieces counts overlapping seconds twice. Both are shown - "4m of 8m" - because the duration alone reads as the whole stretch and the span alone is not this application's
- A title that did not change during a stretch is one thing, not several. A title being animated - a glyph spun at the front of it several times a second while a task runs - is also one thing, and the leading decoration is stripped before comparing. Judged by character category rather than by a list of known glyphs, because the list of symbols people animate with is not one worth maintaining
- Activity since the last stretch ended has not been dropped: it is still going on, is shown as such, and belongs at the top - it is the most recent thing the user did, and the most likely thing they came to look for
- Which stretch is open is remembered by **the stretch**, not by its position in the list. The panel is rebuilt on every refresh and by then the list has moved: a newer stretch of use goes on the front and everything shifts down. Held by position, "the first row is open" comes to mean a different first row, so the stretch the user opened closes itself seconds later while another springs open in its place
- **The agent's own model calls are not the user's activity, and are not shown as any.** The provider answers by running a coding assistant on this machine, which files a transcript of every run in the same store the Listener reads. Those turns are dropped on the way out as well as on the way in, because the ones already recorded are what the user is looking at now (section 18, as amended)
- **A page belongs to a browser, and to no other kind of application.** A browser answers to two names - what it calls itself and what its process is called - and the rule that strips a `.exe` joins those for Chrome and Firefox but not for Edge, which calls itself Edge and runs as `msedge.exe`. The two never met, so pages read in Edge were attributed by focus instead: measured on one machine, **16 of 17 went to an SSH client**. The browser capture extension, since removed, had the same result for a different reason, naming itself "browser", which is not any application. Where the name can be joined it is used; where it cannot, the window in front is the honest guess **only if that window is a browser**, and otherwise there is no guess to make
- **An assistant session is placed by where it was worked in, not by what was on screen.** A session is started in a terminal and then runs for minutes or hours while the user reads something else, so the window in front when its transcript last grew says nothing about where the work was happening. Asked that way, a session of 14,646 exchanges appeared under the browser because a video was playing while it ran, and the same rule filed assistant work under the chat client and under the **lock screen**. What does say where a session lives is the turns the user typed, because typing into an assistant means being in its window; a session whose conversation was not recorded offers no such evidence and is not attributed at all, the browser being no better an answer than none. That is the default installation, where recording what was said is off until an assistant is ticked - and the answer to "a terminal that says nothing recorded while a session ran in it" is then the **window title**, which a coding assistant sets to the task it is working on. A title is a fact about the window; the attribution would have been a guess about the work
- **What the agent wrote to its own records is not activity either.** Every memory the agent writes arrives as a file change with no application on it, and was handed to whichever window was in front - one stretch of the browser held 217 of them. Ownership is what distinguishes these (section 39.1), and for changes already recorded the place is what is left to read it by: the memory folders and the internal storage, and never the rest of the workspace, where the user's own documents and downloads live
- **This application's own panel is not a site the user visited.** The interface is served over HTTP and opened in the ordinary browser, so every look at the Overview and every trip to Settings went into the browser history like any other page and came back as browsing: on one machine `127.0.0.1:8756` was the sixth most visited host in the record, ahead of the search engine. It is the least useful of these to record, because the agent already knows every interaction with its own interface - it served them. A loopback address alone is never enough to decide this, since people develop on the same machine; it is the configured port that says the address is ours. What is dropped is the navigation - the URL, the site it counts towards, the page in a list of pages. **The window title stays**, because the browser genuinely was showing that page, and what an application was doing is not something to blank out

## The Log Covers the Window the Tab Claims

Selecting an application read **the newest N events** and built its stretches of use out of whatever those happened to span.

A day is mostly file changes - on one machine, 17,301 of the 21,182 events in twenty-four hours - so a count-bounded read covers however long the newest N of those last: measured there, **213 minutes** of the day the tab said it was showing, with nothing said about the rest. "What was I doing yesterday evening" is the question this panel is opened with, and it was being answered about this afternoon.

It is bounded by **time**, the same window the rest of the tab is showing, and the answer says which window that was. A ceiling on the number of events remains, so that a pathological day cannot make the panel unbounded - and when it is reached the panel says the oldest is not shown, because a panel that shows a slice and calls it a day is worse than one that says it is a slice.

**What is not worth reading is excluded in the query.** A window of time builds every row in it into an object first, and every one of those 17,301 file changes was the agent writing its own memory: read, parsed, and thrown away by the filter that knows they are not activity. Excluded where the rows are selected, the same window costs **120 ms instead of 480**. A directory change goes the same way - it is recorded for the directory and never read for anything, because every file inside it arrives separately.

## Clicking Through It Is Not a Round Trip

Selecting an application, opening one of its stretches of use and showing more of them are three different weights of thing, and they were all one request.

The activity log is drawn from one answer. Expanding a stretch, showing the earlier ones and opening out a cut title change only how that answer is drawn, so each is a redraw of what is already here. Asked for again, they cost a round trip per click and the panel was laid out over the top of itself.

Opening an application's breakdown, choosing a site inside it, and opening out a cut label are the same kind of thing one level up: they change only how the answers already in hand are drawn, and each re-fetched all seven answers behind the tab and rebuilt every panel, both charts included. The caret cost a reload of the whole tab, which is what made clicking one feel like waiting for it. Narrowing to a site is the exception that proves it - that does change what the log answers, so the log is asked again and nothing else is.

**And a prefix is a range, not a pattern.** "The newest N events of this kind" was written with `LIKE 'browser.%'`, and `LIKE` is case-insensitive by default, which makes it unusable as a prefix scan - so the index on (kind, time) could not serve it and the query walked the whole table newest-first looking for the 851 browser events among fifty thousand. Written as a range it reads them off the index: **70 ms to 4 ms**, and opening a page went from 136 ms to 9 ms. Where the query also has a time bound the planner prefers the other index and loses about ten milliseconds; that is the trade, and it is worth it twice over.

Behind them, the query that reads the newest events had no index to read them from. Every index on raw activity leads with a column that query does not filter on - the event type, the application, whether it has been processed - so "the newest N events, whatever they are" scanned the whole table and sorted it in a temporary B-tree to hand back the first two thousand: on one installation, **49,522 rows and 271 ms**, on every click and every expansion. It is a plain index on the timestamp, and an accounting the reader should never have been able to feel.

## The Log Has a Length

The inspected activity is a column beside a list, not a page of its own, and it must stay a length someone can read.

A day of use is hundreds of entries. Laid out in full, the panel pushes everything below it off the screen and the user scrolls past their own activity looking for the end of it - which is the same as not showing it.

- A few of the most recent stretches are shown, and the rest are behind a single control that says how many there are
- That control works in both directions: what was unfolded can be folded again
- One stretch of use can itself hold hundreds of entries. It shows the most recent of them and states plainly how many it is not showing, rather than becoming unreadable the moment it is opened
- The same applies to any list of visits shown for a page
- How far one application's log was unfolded says nothing about the next. Selecting a different application, or clearing the selection, starts folded again

## A Cut Line Carries Its Own Handle

Where a single line is too long for the log - a window title, a page title, an address - it is cut where it stops being scannable, and the cut itself is the control that undoes it.

A cut with no handle is worse than either showing the line whole or leaving it out. It hides that there was ever more to see, and gives the reader no way to ask for it. So the remainder is not discarded and it is not hidden: the line ends in a small toggle that says how much more there is, and opens it in place.

- The toggle is part of the line it belongs to, not a control beside it, and it states the size of what it is holding back
- It works in both directions: an opened line offers to fold again
- It opens one line, not every line like it. Lines are opened one at a time because they are read one at a time
- Which lines were opened is remembered, so that a panel refreshing on its own does not fold up what the user was reading
- A line that already fits gets no toggle. A control that does nothing is noise

> This applies wherever such a line appears - inside a stretch of use, under an application in the usage list, and in a page's own record - and it is the same handle each time.

## The Arrow Says What It Is For

A list whose rows can be opened says so, once, until the first one is opened.

A small arrow at the head of a row is easy not to notice, and a feature nobody notices is one that is not there. So the list carries a single line saying what the arrow does, and stops carrying it as soon as any row is open - at that point the user has found it, and the reminder is clutter.

Selecting the row that is already selected clears the selection. The control that made a choice is the first place someone reaches to undo it, and letting it serve both costs nothing.

## A Page Is Also Something The User Did

A page recorded in an interaction can be asked about in its own right.

Having found the page they were looking for, the user's next question is about that page - when else they were on it, how often, in which application - and that question is not answerable from a list of applications. So a page is openable from wherever it appears, and shows its own record:

- Every recorded visit, most recent first, with the application it happened in
- How many visits in total, and which applications they were made from
- A way back to where the page was opened from, because opening a page is a step down into detail, not a move to somewhere else
- A page with nothing recorded says so, rather than presenting an empty table as an answer

# 10. Calendar Panel
<!-- number: 10 | label: 10. | id: s10 | page: 14 -->

The Calendar displays tasks and events maintained by the Scheduler.

Users must be able to:

- View scheduled tasks
- Edit scheduled time
- Reschedule tasks
- Edit task metadata
- Disable tasks
- Delete tasks

Calendar modifications must modify the actual Scheduler state.

The Calendar is not simply a visual representation.

Required synchronization:

Calendar UI

```
|
v
```

Scheduler API

```
|
v
```

Persistent Scheduler Storage

Similarly:

Agent creates delayed task

```
|
v
```

Scheduler

```
|
v
```

Calendar immediately reflects task

## Calendar Surface

The Calendar must present a calendar: a dated grid of days, in the conventional form users already know from a calendar application.

The grid is the panel's primary surface and is always present. It is drawn from the date, not from the data:

> An empty calendar shows an empty month, not an empty list. A period with nothing scheduled must still render its days, weekday headings and date numbers.

The following are required of the surface:

- A month view showing every day of the displayed month in weeks, including the leading and trailing days needed to complete the first and last week, visually distinguished from the days of the month itself
- Weekday headings, and a date number in every day cell
- The current day distinguished from every other day
- Navigation to the previous and next period, and a direct return to the current period, with the displayed period named
- Each scheduled task placed in the day it falls on, showing its time and title, and distinguishing its status
- A day with more tasks than its cell can show must say how many more, rather than silently truncating
- Selecting a day shows that day's tasks in full, and offers scheduling on that day

A single day, with hours as rows, is offered beside the month (see Zooming In below); there is no week view.

Below the grid is a list of what the user agreed to whose time is not yet known. When the agent sees the time, it puts the item on the calendar and takes it off the list.

**The count is a control, not a caption.** Saying "+13 more" satisfies the requirement not to truncate silently, and still leaves the thirteen unreachable from where the user is looking. Selecting the day lists them, but a number sitting in the cell reads as something to press, and pressing it should do what it looks like it does: the cell opens and shows all of them, and offers to fold back up. The day the agent put sixteen things on is exactly the day worth looking at, and it was the day the grid said least about.

The editing requirements above apply wherever a task is shown, and section 10's synchronization requirement is unchanged: an edit made on the calendar surface changes Scheduler state, not a view of it.

## A Time Is Something You Can Point At

The calendar draws hours, and clicking one means that hour.

Drawing a grid of times and then treating every click as "this day" wastes the grid: the reason to draw hours at all is so the user can aim at one. Pointing at four o'clock on Thursday says **when** - the user still says what.

- A slot is half an hour, which is fine enough to mean something and coarse enough to hit
- Clicking one fills the time into the form for a new task. It does not create anything: a stray click must not become an event
- The slot that was chosen is marked, so what the form is about to schedule is visible on the grid rather than only in a field
- A slot already holding a task is still a slot; clicking the task opens the task, and clicking beside it picks the time

## An Event Opens Beside Itself

Clicking an event shows the event, in a card next to the thing that was clicked.

A chip has to fit inside a slot, so it can carry a name and a time and nothing else. Everything else the task has - what kind it is, whether it repeats, when it last ran and what happened - would otherwise live only in a table somewhere else, and the user would have to know to go there.

- The detail opens **beside** the event, not centred on the screen. A card in the middle of the window covers the grid, points at nothing, and leaves the reader to find their place again when it closes. Beside it, the answer stays next to the question and the grid stays visible around it
- It moves only as far as it must to stay on screen: to the other side of the event when there is no room, and no further up than the window forces. A card that has drifted somewhere arbitrary no longer reads as belonging to what was clicked
- The screen behind it is not dimmed. The backdrop exists to catch the click that dismisses, not to hide the grid the user is still reading
- Escape closes it, and so does a click anywhere outside it
- The detail carries what the chip could not, and offers what can be done with the task: edit it, disable it, or delete it
- Opening an event changes nothing. Looking at a task must not act on it
- Deleting asks first, in the card itself, because it is the one action that cannot be undone - and asks why, optionally, because what the user removes is how the agent learns which notifications they want
- The detail opens from the chips on the grid; the Calendar tab has no separate task list

## The Grid Has to Be a Calendar

The day view is laid out the way the month view is: a grid whose cells are big enough to read.

An hour must be tall enough for an event to carry its own name. Below that, a title is truncated to an ellipsis - a chip that says nothing while taking the room of one that would say something, which is worse than showing no chip at all.

- A slot is tall enough for a line of text, and an event's name is what it shows
- In a time grid the chip does not repeat the time. The row it sits in already says when, and repeating it spends exactly the width the name needs
- In the month view the chip does show the time, because there is no hour row there to say it

## Zooming In

Two views: the month, and a single day.

A week has to fit seven columns and cannot give any one day the room to be read, let alone aimed at, so there is no week view; zooming in goes straight to the day.

- The day view is one column of the same half-hour slots, at a size the user can actually hit
- A day is reached by double-clicking it in the month view, or by choosing the day view directly
- The day view opens at the start of the working day, or at the day's first event if that is earlier, rather than at midnight. A day that opens at the top opens on the eight hours nobody schedules, and every visit would begin by scrolling

# 11. States Module
<!-- number: 11 | label: 11. | id: s11 | page: 15 -->

Location:

```
./agent/state/
```

The control surface is the page's Settings tab and, in a build that carries the debuggers, the Debugger and the per-part debuggers (experience, nature, reading, language, chat, heartbeat, state heartbeat). There is no graph view: memory is a library of files (section 39.1).

# 11.1 States Are Internal
<!-- number: 11.1 | label: 11.1 | id: s11-1 | page: 15 -->

A state is internal. The agent writes it, and nothing closes it: it stops describing the present when its end passes. No user action addresses one directly.

States remain inspectable in the Debugger, laid out the way the calendar is, with every state reachable and each one expanding to its detail. That is where an internal thing belongs - visible to whoever is working on the system and absent for everyone else. A build made from source carries the debuggers by default; `build.py --no-debugger` leaves them out, and the released installers and downloads are built that way. In a build made without the Debugger (section 14.34) there is no way to see states at all, which is correct.

What the user does act on is the two things that are theirs:

| | what it is | who makes it | who ends it |

|---|---|---|---|

| **Scheduled task** | something to be done at a time | the user or the agent | running to completion, or the user cancelling it |

| **Calendar entry** | a drawing of a task, on a grid | nobody - it is a view | it is redrawn |

| **State** | what is true over a span | the agent | its own end time |

What the user sees of states is what they add up to - the tasks on the Overview, which the state heartbeat groups from them.

Every scheduled task writes a state when the task is created, so that what the agent believes is going on always has something behind it. The reverse does not hold: a state needs no task, and most have none.

An edit on the calendar reaches the associated state as well as the task (section 60.1). The state is internal, not independent.

# 12. Settings
<!-- number: 12 | label: 12. | id: s12 | page: 15 -->

Initial settings include:

- Display language
- Character / avatar
- Font size
- Heartbeat interval
- The models the agent uses, in order of preference
- Writable workspace
- Listener configuration
- Notification preferences

The settings system should be schema-driven and extensible.

Settings are grouped into views - General, Display, Avatar, Functions, Privacy, Others - and edited where they stand: changing a value shows a Save bar on its card, which stays in view while the user scrolls, and nothing is stored until Save is pressed. The models are one ordered list: each call goes to the first that works, and moves to the next when one cannot answer.

## Setting Explanations

A label names a setting; it does not explain it. Every setting must therefore carry a short explanation, and the Settings screen must display that explanation beneath the setting's control, in a smaller and visually secondary style.

> No setting may be presented to the user without an explanation of what it does.

The explanation is part of the schema, not of the frontend, so that a setting cannot be added without one. An explanation should say what the value affects and what the consequence of changing it is - in particular for a setting whose effect is not evident from its label:

- What the setting controls, in the user's terms rather than the implementation's
- The practical effect of a higher or lower value, or of enabling or disabling it
- Any cost the user is trading against, such as accuracy against speed, completeness against token use, or capability against privacy
- Any condition that makes the setting inert, such as depending on another setting being enabled

Where a setting cannot be changed from the Settings screen - a value derived from the launch environment, for example - the explanation must say how it is changed instead, and the control must be presented as read-only rather than as an editable field that silently rejects input.

## Choosing Rather Than Typing

A setting whose value identifies something that exists on the machine - a directory, an application - is chosen with the operating system's own chooser, not typed.

This applies wherever such a value is configured, including a setting that holds several of them.

> A typed value is one the user has to get right unaided: it can be misspelt, can name something that does not exist, can be the wrong kind of thing, and gives no indication of any of that until something later fails.

An application is identified by choosing the application itself. The interface stores whatever form the rest of the system matches against, and shows the user what they picked - the user selects an application, not a string that has to correspond to one.

The requirements are:

- Selecting opens the platform's chooser, starting from the current value where there is one, and offering the kind of thing the setting holds
- What was chosen is shown, so the user can see what they selected
- Where the setting holds several values, each can be added by choosing it and removed individually
- A value that is unusable - it does not exist, or it is the wrong kind of thing - is reported when it is chosen, not when it is next used
- Typing remains possible for a user who prefers it, and is validated the same way; the chooser is the default path, not the only one

Where the platform provides no directory chooser, the interface says so and accepts a typed path, validated as above.

The requirement is verifiable: a settings schema in which any setting has no explanation is invalid.

# 12.1 User Defaults
<!-- number: 12.1 | label: 12.1 | id: s12-1 | page: 15 -->

There are two layers of default, and they belong to different owners.

The **built-in defaults** are declared by the program. They are identical on every installation and are not editable.

The **user's defaults** are the values this user has chosen. A setting the user changes becomes their default for that setting from then on, so a preference expressed once does not have to be expressed again.

The user's defaults are stored beside their settings inside the agent root, as their own file, and therefore travel when the root is copied (section 110.1). Copying the root to a new machine carries the user's configuration with it, without carrying the program's.

Resolution order, from lowest precedence to highest:

1. The built-in defaults
2. This user's defaults
3. The current value of the setting

A setting the application derives rather than the user chooses - the agent root and anything computed from it - is not a choice and is never recorded as a user default.

Resetting a setting means undoing the user's choice: their default for it is forgotten and the built-in value applies again. Resetting to the user's own default would do nothing, because their default is the value they last chose.

> The user must be able to see what they have chosen, distinguish it from what the program chose, and undo it for any one setting, with the button under its explanation. Undoing every choice at once is available through the API, not yet from the page.

# 12.2 The Language the User Works In
<!-- number: 12.2 | label: 12.2 | id: s12-2 | page: 15 -->

The agent's memory is kept in English, whatever language the user speaks: its notes to itself, the natures it learns and the keywords it finds things by. One language is what makes comparison possible - the embedding model reads English only, and a word is found only as it is written. The user's language is the display language (below), and what the user reads is written in it.

**The record's text is never translated.** What a record says stays exactly as it was written. Translating the content would be an agent quoting back its own translation of something it half-understood.

Where the display language is not English, a task's and a project's words carry an English copy beside them, written in the same call that writes the words, so what is compared stays comparable. A copy that is missing or stale - written for words that have since changed - is filled in at the end of a state round, all of them together in a few short calls.

The earlier design - keywords in up to eight chosen languages on every record, with a relabelling backlog worked through a batch a round by the data heartbeat - was removed with the data heartbeat on 2026-10-06. There is no relabeller and no index, and no backlog to work through.

Every model call states the language its output is to be written in: the display language for what the user will read, English for what the agent keeps for itself.

## Being Spoken To, and Being Able to Find Things

These are two questions, and one setting was answering both.

- **Display language** - what the user is *shown*. Every word the agent composes for them to read. One of Chinese, English, Spanish, Japanese or Korean, defaulting to this computer's language when it is one of them, and English otherwise. The application's own screens follow it: Chinese screens for Chinese, English otherwise. Saving a new display language reloads the page
- **Memory language** - what the user's memory is kept and *findable* in. Fixed to English in this version and not offered in Settings

They do not have to be the same. Someone whose machine is in English and who thinks in Chinese wants to be spoken to in Chinese; what their memory is kept in is a separate decision, and changing one does not change the other.

**Display language is not only for replies.** It is a property of the application, not of the conversation, so every prompt whose output the user will read carries it: the reply to a question, the note the heartbeat raises on its own, what a finished task reports, what goes on the calendar. A model writes in whatever language the material in front of it suggests, so a note composed from English source material comes out in English however this is set - and the user ends up reading half of their agent in a language they did not choose. Anywhere the instruction is missing is a place that reverts to the material's language, silently.

**What is quoted keeps its own words.** File paths, identifiers, error messages, code and anything quoted from a record stay exactly as they are. Translating an error message makes it useless for the thing it was quoted for, and a translated path does not exist.

Changing the display language does not rewrite anything already stored. A memory keeps the words it was written in (above), and so does a note that has already been raised.

## An Interval Is Set in the Unit You Think In

Intervals were stored in seconds, because that is what the loops reading them expected, and every one was also *shown* in seconds.

"How often should the agent wake?" answered as `3600`, under a label ending "(s)", is a unit conversion the reader performs in their head to check a value they never chose - and the two intervals that decide most of what the agent costs when nobody is talking to it are exactly those. A setting nobody can read is only nominally a setting.

The two intervals that decide most of what the agent costs - how often the agent heartbeat wakes and how often the state heartbeat groups - are both set in minutes, as plain number fields, whatever they are stored in (the agent heartbeat's in seconds, the state heartbeat's in hours). Other intervals stored in seconds keep the stored value in seconds and let the control convert. Such a control opens in the largest unit the value divides into evenly, because a value written as one hour was meant as an hour rather than as 3600 of something, and it says in words what the number means. Changing the unit does not change the interval: the same duration is re-expressed, converted from what the control is holding rather than from what it opened with, so a number typed before the unit was changed is not silently thrown away. A value below the setting's floor is brought up to it rather than stored as entered.

**And it takes effect now, not on the next wake.** The agent heartbeat and the state heartbeat read their interval when they decide how long to sleep, so a change was picked up when the sleep ended - and for the agent that end can be an hour away. Turning an hour down to five minutes then did nothing for up to an hour, which reads as a setting that does not work, while the panel went on reporting the next run an hour out because that is when it had been scheduled.

So the sleep is interruptible, and a change to one of these settings wakes the loop that depends on it. **A change does not trigger a run**: editing how often something happens is not asking for it to happen. The loop works out again when the run is due, counting from the same moment it was already counting from - so a shortened interval brings the next run forward, and one shortened past the time already spent waiting is due immediately. A setting nothing in the loop's timing depends on does not disturb it, and neither loop is woken by the other's setting.

## Showing a Setting the User Cannot Change

A value can be fixed in this version - the machinery takes a choice and this version does not offer one - and the interface has to say that without lying about how to change it.

Two different things get called read-only, and collapsing them makes one of them wrong:

- **derived** - this follows something else, go and change that. The workspace path follows the agent root
- **fixed** - the machinery takes a choice here and this version does not offer one

A pinned setting labelled "read-only" sends the reader looking for the setting it derives from, and there is not one. It is labelled "fixed in this version", and its description says why.

The memory language was the first such setting. It is now English only, and is not shown in Settings at all. When it was still two languages, Chinese and English, it was shown as what it was - two languages, both on - rather than as a value. A tick list stores one entry per line, and this setting was not written by a tick list: stored as `zh,en` and read a line at a time, it became a single entry named "zh,en", shown **ticked** and marked "not offered here", with the real zh and en sitting unticked beneath it. Every part of that is wrong about a setting whose entire content is that those two are on. So a stored line that is not an option, but is made entirely of options separated by commas, is read as those options - and a name that genuinely contains a comma is left alone, because it is an option itself.

What cannot be changed cannot be ticked, either. Disabling the control did nothing here, because the control is a group and the boxes inside it stayed clickable with nothing listening: a tick appeared to take and was silently discarded on the next render.

# 13. Graph Display
<!-- number: 13 | label: 13. | id: s13 | page: 16 -->

Removed. The knowledge graph was replaced by the library, a set of folders of readable files, on 2026-09-16 (section 39.1), and the graph view went with it, together with its relationship inspection and legibility rules: there is no graph tab, route or view. The graphs on the Experience tab are a different thing.

# 14. Debugger Overview
<!-- number: 14 | label: 14. | id: s14 | page: 16 -->

The Debugger should provide an interactive visualization of the entire Personalized Agent

system, rather than only displaying resource metrics.

Its purpose is to allow whoever develops the agent to understand:

- Which system components are currently running
- How components are connected
- What data or requests are flowing between components
- Which component produced a specific error
- What execution trace occurred inside a component
- What diagnostic tests are available for that component or connection
- Whether a subsystem is healthy, degraded, blocked, or unavailable

The Debugger should function as an interactive System Debug & Diagnostics Console.

The Debugger is for whoever develops the agent. A build made from source carries it, and every debugger tab, by default; `build.py --no-debugger` leaves them out, and the released installers and downloads are built that way, so a user's installation has no Debugger and its `/api/debugger` routes are not served.

Beside the System Map it has a tab for each part that needs one - experience, natures, reading, languages, chat, the agent heartbeat and the state heartbeat. The record of computer usage (section 9) is shown on the Debugger tab when the Debugger is present.

# 14.1 System Visualization
<!-- number: 14.1 | label: 14.1 | id: s14-1 | page: 17 -->

The primary Debugger interface should display an interactive topology of the running system.

Conceptually:

```
           +------------------+
            |    Desktop UI    |
            +--------+---------+
                     |
                     v
            +------------------+
            |    Agent Core    |
            +---+----------+---+
                |          |
      +---------+          +----------+
      |                               |
      v                               v
+-----------+                  +-------------+
| Heartbeat |                  |  Scheduler  |
+-----+-----+                  +------+------+
      |                               |
      +---------------+---------------+
                      |
                      v
            +------------------+
            |  Memory Library  |
            +------------------+
```

```
+----------------+
|    Listener    |
+-------+--------+
        |
        v
+----------------+
| Event Pipeline |
+-------+--------+
        |
        +----------------------> Raw Event Storage
```

```
+----------------+
| Model Provider |
+-------+--------+
        |
        +----------------------> Agent Core
        +----------------------> Heartbeat
        +----------------------> Scheduler
```

```
+----------------+
| Runtime /      |
| Sandbox        |
+-------+--------+
        |
        +----------------------> Scheduler
        +----------------------> Agent Execution
```

The visualization should reflect the actual runtime system, rather than being a static architecture diagram.

# 14.2 Debugger System Nodes
<!-- number: 14.2 | label: 14.2 | id: s14-2 | page: 20 -->

Each major system or subsystem should appear as a clickable node.

Examples include:

Agent Core

Conversations

Desktop UI

Local API

Listener Manager

Browser Listener

Filesystem Listener

Application Listener

Heartbeat

Scheduler

Memory Library

Raw Event Storage

Raw Edit Storage

Model Manager

Model Provider

Credential Manager

Runtime

Sandbox

Platform Layer

Event Bus

Notification Service

Observability Service

Agent Protocol

The state heartbeat and delegated work runs are inspected in their own debugger tabs rather than as nodes on the map.

Nested components may be collapsed into a parent system by default.

For example:

Listener

```
|
+-- Browser Provider
+-- Filesystem Provider
+-- Application Provider
+-- Integration Plugins
```

The user should be able to expand or collapse these components.

# 14.3 System Node Status
<!-- number: 14.3 | label: 14.3 | id: s14-3 | page: 21 -->

Each system node should expose its current runtime state.

Recommended states include:

```
starting
running
idle
busy
degraded
blocked
failed
stopped
unavailable
```

Additional information may include:

last activity

last successful operation

last error

active task count

queue size

CPU usage

memory usage

model usage

restart count

```
uptime
```

The system topology should update dynamically as component state changes.

# 14.4 Clicking a System Component
<!-- number: 14.4 | label: 14.4 | id: s14-4 | page: 22 -->

Clicking a system node should open a detailed diagnostics panel for that component.

The panel should contain several logical sections.

## Overview

Display:

- Component name
- Component ID
- Component type
- Runtime state
- Start time
- Uptime
- Version
- Platform implementation
- Current task
- Dependencies
- Consumers

## Runtime Metrics

Where relevant:

- CPU consumption
- Memory consumption
- Thread/process count
- Queue depth
- Request count
- Error count
- Average latency
- Model token usage
- Last execution duration

## Debug Trace

The user should be able to inspect the execution trace produced by the selected component.

For example:

16:31:02.143  Listener received filesystem event

16:31:02.145  Event normalized

16:31:02.147  Event submitted to event pipeline

16:31:02.152  Raw Event persisted

16:31:02.156  listener.event emitted

The trace should support filtering by:

```
time
task_id
severity
event_type
component
request_id
trace_id
```

# 14.5 Structured Trace Model
<!-- number: 14.5 | label: 14.5 | id: s14-5 | page: 24 -->

System execution should use structured tracing rather than relying only on plain-text logs.

A trace should conceptually contain:

```
TraceEvent:
    id
    trace_id
    span_id
    parent_span_id
```

```
timestamp
```

```
component
operation
```

```
task_id
event_type
severity
```

```
duration
```

```
input_summary
output_summary
```

```
status
error
```

```
metadata
```

Sensitive information must be removed before entering the Debugger trace system.

# 14.6 End-to-End Trace
<!-- number: 14.6 | label: 14.6 | id: s14-6 | page: 25 -->

A single agent task may pass through many components.

For example:

User Instruction

```
|
v
```

Agent Core

```
|
v
```

Model Provider

```
|
v
```

Runtime

```
|
v
```

Generated Task

```
|
v
```

Raw Edit

There is no retrieval step: the agent core searches no memory, and answers from the instruction and the conversation it was asked in. Library search was removed with the data heartbeat on 2026-10-06.

All operations belonging to the same logical execution should share a common:

```
trace_id
```

This allows the Debugger to reconstruct the complete task lifecycle.

The user should be able to select a task and see:

Agent Core

```
|
| 14 ms
v
```

Model Request

```
|
| 3.2 s
v
```

Task Execution

```
|
| 420 ms
v
```

Raw Edit Recording

# 14.7 System Connections
<!-- number: 14.7 | label: 14.7 | id: s14-7 | page: 27 -->

Connections between systems should also be first-class interactive objects.

A connection represents communication or dependency between two components.

Examples:

```
Listener -> Event Pipeline
```

```
Event Pipeline -> Raw Event Storage
```

```
Agent Core -> Model Provider
```

```
Heartbeat -> Scheduler
```

```
Scheduler -> Runtime
```

```
Runtime -> Sandbox
```

```
Scheduler -> Notification Service
```

Connections should be clickable independently from system nodes.

# 14.8 Connection Diagnostics
<!-- number: 14.8 | label: 14.8 | id: s14-8 | page: 28 -->

Clicking a connection should open information about that communication path.

The interface should display:

- Source component
- Destination component
- Connection type
- Interface/API used
- Current status
- Request count
- Failure count
- Last successful transfer
- Last failed transfer
- Average latency
- Queue depth where applicable
- Recent messages/events
- Relevant traces

For example:

Connection

Listener Manager

```
->
```

Event Pipeline

Type:

Internal Event Stream

Status:

Healthy

Recent Traffic:

1,245 events / 10 minutes

Average Latency:

2.1 ms

Last Error:

None

# 14.9 Connection Trace
<!-- number: 14.9 | label: 14.9 | id: s14-9 | page: 29 -->

A connection should expose its own debug trace.

For example:

Browser Provider

```
|
| browser.navigation
v
```

Event Pipeline

```
|
| normalized successfully
v
```

Raw Event Storage

The interface should allow the user to inspect individual events where appropriate.

Sensitive event content must obey privacy and redaction rules.

# 14.10 Debug Tests
<!-- number: 14.10 | label: 14.10 | id: s14-10 | page: 30 -->

Each component may expose diagnostic tests.

The available tests should depend on the selected component.

For example:

## Listener

Possible tests:

Test Browser Listener

Test Filesystem Listener

Test Active Application Detection

Test Event Normalization

Test Event Pipeline

Test Listener Permissions

## Scheduler

Possible tests:

Test Scheduler Database

Test Due-Task Detection

Test Task Persistence

Test Runtime Invocation

Test Calendar Synchronization

## Model Provider

Possible tests:

Test Authentication

Test Model Availability

Test API Access

Test OAuth Status

Test Lightweight Model Request

## Memory Library

Possible tests:

Test Database Connection

Test Node Read

Test Transaction

The library has no edges and no search, so there is nothing to traverse and no retrieval pipeline to test.

## Sandbox

Possible tests:

Test Workspace Write

Test Blocked External Write

Test Subprocess Isolation

Test Temporary File Restriction

Test Symlink Escape Protection

# 14.11 Test Interface
<!-- number: 14.11 | label: 14.11 | id: s14-11 | page: 32 -->

Components should register diagnostic tests through a common interface.

Conceptually:

```
class DiagnosticTest:
```

```
id: str
name: str
description: str
```

```
component_id: str
```

```
risk_level: str
```

```
async def run(self) -> DiagnosticResult:
    ...
```

A result may contain:

```
DiagnosticResult:
    test_id
    started_at
    completed_at
```

```
status
summary
```

```
details
trace_id
```

```
recommendations
```

Recommended result states:

```
passed
warning
failed
skipped
unsupported
```

# 14.12 Safe Diagnostic Tests
<!-- number: 14.12 | label: 14.12 | id: s14-12 | page: 33 -->

Diagnostic tests must not bypass normal system security controls.

Tests executed through the Debugger are subject to the same:

- Filesystem restrictions
- Credential restrictions
- Permission system
- Runtime sandbox
- Platform restrictions

as normal agent operations.

The Debugger must not become an alternative unrestricted execution interface.

# 14.13 Test Risk Levels
<!-- number: 14.13 | label: 14.13 | id: s14-13 | page: 34 -->

Diagnostic tests should declare their expected impact.

Recommended levels:

```
read_only
safe_write
external_request
state_changing
```

Read-only diagnostics may run directly.

Tests that modify persistent system state should clearly indicate this behavior.

Potentially destructive tests should not be included as normal diagnostics.

# 14.14 Connection Tests
<!-- number: 14.14 | label: 14.14 | id: s14-14 | page: 34 -->

Connections may also provide tests.

For example:

```
Agent Core -> Model Provider
```

Tests:

```
Check provider reachable
Check authentication
Check selected model available
```

or:

```
Scheduler -> Runtime
```

Tests:

```
Submit dry-run task
Verify runtime receives task
Verify sandbox initializes
```

or:

```
Listener -> Event Pipeline
```

Tests:

```
Emit synthetic event
Verify event normalization
Verify event delivery
```

Synthetic events used by tests should be clearly marked as diagnostic data and should not accidentally become normal user behavioral records.

# 14.15 Diagnostic Test Registry
<!-- number: 14.15 | label: 14.15 | id: s14-15 | page: 35 -->

The system should maintain a central Diagnostic Test Registry.

Conceptually:

Diagnostic Registry

```
|
+-- Agent Core Tests
|
+-- Listener Tests
|
+-- Scheduler Tests
|
+-- Heartbeat Tests
|
+-- Memory Library Tests
|
+-- Model Tests
|
+-- Runtime Tests
|
+-- Platform Tests
|
+-- Protocol Tests
```

This makes diagnostics extensible.

Listener plugins and other future integrations may register their own tests.

# 14.16 Runtime System Graph
<!-- number: 14.16 | label: 14.16 | id: s14-16 | page: 36 -->

The System Debugger should obtain its topology from a structured runtime component registry.

A component should conceptually register:

```
SystemComponent:
    id
    name
    component_type
```

```
parent_component
```

```
dependencies
connections
```

```
status_provider
metrics_provider
trace_provider
diagnostic_tests
```

The Debugger should generate the visualization from this registry rather than maintaining a manually hard-coded drawing.

This ensures that future components automatically appear in the System Debugger.

# 14.17 Dynamic Components
<!-- number: 14.17 | label: 14.17 | id: s14-17 | page: 37 -->

Some components exist only under certain configurations.

For example:

OAuth Provider

A desktop application connected through the application protocol

VS Code Integration

Linux-specific Listener

Specific Model Adapter

The System Debugger should show components that are:

- Loaded
- Configured
- Running
- Disabled
- Unavailable

where useful.

A disabled component may appear differently from a failed component.

# 14.18 System Hierarchy
<!-- number: 14.18 | label: 14.18 | id: s14-18 | page: 38 -->

The system visualization should support multiple levels of detail.

Initial view:

Frontend

Agent Core

Observation

Knowledge

Scheduling

Models

Runtime

Platform

Expanding a system reveals individual components.

For example:

Observation

```
|
+-- Listener Manager
|
+-- Browser Listener
|
+-- Filesystem Listener
|
+-- Application Listener
```

This prevents the visualization from becoming unreadable as the system grows.

# 14.19 Live Activity Visualization
<!-- number: 14.19 | label: 14.19 | id: s14-19 | page: 39 -->

Where practical, the Debugger may visualize active execution.

For example:

Agent Core

```
===> Model Provider
```

Scheduler

```
===> Runtime
```

An active connection may temporarily indicate that requests or events are flowing through it.

This visualization is intended for debugging and should not be required for core system execution.

Debugger rendering must not significantly affect runtime performance.

# 14.20 Historical System View
<!-- number: 14.20 | label: 14.20 | id: s14-20 | page: 40 -->

The Debugger should allow inspection of recent historical activity, not only current state.

For example, a user should be able to inspect:

Last 5 minutes

Last 30 minutes

Last hour

Specific task

Specific Heartbeat

Specific state heartbeat round (in its own debugger tab)

Specific Scheduler execution

This allows failures to be diagnosed after the execution has completed.

# 14.21 Heartbeat Debugging
<!-- number: 14.21 | label: 14.21 | id: s14-21 | page: 40 -->

Selecting the Heartbeat component should expose:

- Last Heartbeat time
- Next expected Heartbeat
- Model used
- Checklist version
- Checklist snapshot
- Records read (states opened by id)
- Actions generated
- Scheduled tasks created
- Token usage
- Execution duration
- Failure information

The interface should make it possible to inspect an individual historical Heartbeat execution.

A single run, step by step, with every prompt and reply, is inspected - and can be started - from the Heartbeat debugger tab.

# 14.22 Data Heartbeat Debugging
<!-- number: 14.22 | label: 14.22 | id: s14-22 | page: 41 -->

Removed. The data heartbeat and everything that read its output - the indexer, compaction, relabelling and search - were deleted on 2026-10-06, so there is no such component to select; raw activity is now let go by age after eight days. The rule that a model which cannot be used shows in the Debugger as the same failure that triggered the mandatory user notification still applies, to the agent heartbeat and the state heartbeat.

# 14.23 Scheduler Debugging
<!-- number: 14.23 | label: 14.23 | id: s14-23 | page: 41 -->

Selecting Scheduler should expose:

- Scheduler state
- Next scheduled event
- Pending task count
- Running tasks
- Completed tasks
- Failed tasks
- Disabled tasks
- Last scheduler loop
- Loop latency
- Task execution trace

Selecting a task should show the complete execution chain.

# 14.24 Runtime and Sandbox Debugging
<!-- number: 14.24 | label: 14.24 | id: s14-24 | page: 42 -->

The Runtime/Sandbox node should expose:

- Active restricted processes
- Workspace path
- Process IDs
- Allowed writable roots
- Blocked write attempts
- Exit codes
- Resource consumption
- Sandbox initialization errors

Security violations should be visually distinguishable from normal application errors.

For example:

SECURITY VIOLATION

Task:

```
task_1827
```

Process:

python task_1827.py

Attempted Write:

/Users/example/Desktop/output.txt

Authorized Workspace:

/Users/example/AgentWorkspace/

Action:

Blocked

# 14.25 Model Debugging
<!-- number: 14.25 | label: 14.25 | id: s14-25 | page: 43 -->

Selecting a model component should expose:

- Provider
- Model
- Authentication method
- Authentication status
- Availability
- Last successful invocation
- Recent failures
- Rate-limit status
- Quota state where available
- Input tokens
- Output tokens
- Invocation latency

Credentials must never be shown.

The Debugger may display:

OAuth:

Authenticated

but never:

Access Token:

...

# 14.26 Debugger Search
<!-- number: 14.26 | label: 14.26 | id: s14-26 | page: 44 -->

The Debugger should support search by:

task ID

trace ID

```
component
event
error
```

scheduled task

Heartbeat

```
timestamp
```

This allows a user to quickly locate a specific execution.

# 14.27 Exportable Diagnostic Report
<!-- number: 14.27 | label: 14.27 | id: s14-27 | page: 44 -->

The user should be able to export a diagnostic report for troubleshooting.

The exported report may contain:

- System topology
- Component states
- Selected traces
- Errors
- Test results
- Platform capabilities
- Application version

Before export, the system must apply secret and privacy redaction.

Exported reports generated by the agent must obey the authorized workspace write restriction.

# 14.28 Debugger Performance Requirement
<!-- number: 14.28 | label: 14.28 | id: s14-28 | page: 45 -->

Observability must not substantially interfere with normal system operation.

Trace collection should therefore support:

- Bounded retention
- Sampling where appropriate
- Log rotation
- Configurable verbosity
- Asynchronous persistence

High-volume Listener activity should not cause the Debugger database to grow without limits.

# 14.29 Updated Observability Architecture
<!-- number: 14.29 | label: 14.29 | id: s14-29 | page: 45 -->

The recommended architecture is:

System Components

```
|
| traces / metrics / health
v
```

Observability Service

```
|
+--------------------+
|                    |
v                    v
```

Trace Storage         Metrics Storage

```
  |                    |
  +---------+----------+
            |
            v
  System Debugger UI
            |
     +------+------+
     |             |
     v             v
System Node     Connection
 Inspector      Inspector
     |             |
     +------+------+
            |
            v
    Diagnostic Tests
```

System components should publish observability information through the common Observability Service rather than implementing their own independent Debugger UI.

# 14.30 Debugger Core Requirement
<!-- number: 14.30 | label: 14.30 | id: s14-30 | page: 46 -->

The System Debugger must satisfy the following core behavior:

> The user must be able to visually inspect the architecture of the currently running Personalized Agent, select either a system component or a connection between components, inspect its runtime/debug trace, and execute the diagnostic tests registered for that component or connection.

The visualization must be generated from actual runtime component registration and observability state whenever practical, rather than being purely static.

# 14.31 Debugger UI Layout
<!-- number: 14.31 | label: 14.31 | id: s14-31 | page: 47 -->

The System Debugger should use a three-region layout designed for interactive inspection of the running Personalized Agent architecture.

The primary regions are:

1. Interactive System Map
2. Component / Connection Inspector
3. Trace Timeline

Conceptually:

┌─────────────────────────────────────────────────────────── ───────────┐

│ System Debugger                                      Search: [     ] │

├───────────────────────────────────────────┬─────────────── ───────────┤

│                                           │ Inspector                │

│                                           │                          │

│          Interactive System Map           │ Selected: Agent Core     │

│                                           │ Status: Running          │

│   Listener ───────> Event Pipeline        │ CPU: ...                 │

│                         │                 │ Memory: ...              │

│                         ▼                 │                          │

│                   Data Management         │ [Overview] [Trace]       │

│                         │                 │ [Metrics]  [Tests]       │

│            ┌────────────┴────────────┐    │                          │

│            ▼                         ▼    │ Available Tests          │

> ▶

│      Memory Library            Raw Storage│  Health Check          │

│                                           │ ▶ Test Database Connection│

│   Heartbeat ───────> Agent Core            │                          │

│                         │                 │                          │

│                         ▼                 │                          │

│                   Model Provider          │                          │

│                                           │                          │

├───────────────────────────────────────────┴─────────────── ───────────┤

│ Trace Timeline                                                       │

│                                                                      │

│ 16:01:02  User → Agent Core → Model → Runtime → Raw Edit             │

└─────────────────────────────────────────────────────────── ───────────┘

The exact visual styling may vary, but the logical structure should remain consistent.

# 14.31.1 Interactive System Map
<!-- number: 14.31.1 | label: 14.31.1 | id: s14-31-1 | page: 48 -->

The Interactive System Map is the primary visualization area.

It should display the topology of the currently running Personalized Agent system.

Each system component is represented as a node.

Examples include:

Desktop UI

Local API

Agent Core

Conversations

Listener

Event Pipeline

Heartbeat

Scheduler

Model Manager

Model Provider

Memory Library

Raw Event Storage

Raw Edit Storage

Runtime

Sandbox

Platform Layer

Credential Manager

Notification Service

Observability Service

Event Bus

Agent Protocol

There is no Data Heartbeat any more; nothing processes raw activity in the background, and the agent heartbeat reads its own window of activity directly.

Connections between components should be represented as directed edges.

For example:

Listener

```
->
```

Event Pipeline

```
->
```

Raw Event Storage

or:

Agent Core

```
->
```

Model Provider

The map should support:

- Zoom
- Pan
- Node selection
- Edge selection
- Expand subsystem
- Collapse subsystem
- Automatic layout
- Manual repositioning where appropriate
- Search highlighting
- Runtime status visualization

The map must not be implemented as a static image.

It should be generated from the registered system components and connections whenever practical.

# 14.31.2 Hierarchical System View
<!-- number: 14.31.2 | label: 14.31.2 | id: s14-31-2 | page: 50 -->

The system topology should support multiple levels of abstraction.

At the highest level, the Debugger may display major systems such as:

Frontend

Agent Core

Observation

Knowledge

Scheduling

Models

Runtime

Platform

A user may expand a system to inspect its internal components.

For example:

Observation

```
|
+-- Listener Manager
|
+-- Application Listener
|
+-- Filesystem Listener
|
+-- Browser Listener
|
+-- Application Integrations
|
+-- Event Pipeline
```

The screen, assistant and screenshot listeners are not yet drawn on the map.

Similarly:

Models

```
|
+-- Model Manager
|
+-- Primary Model Provider
|
+-- Credential Manager
|
+-- Provider Authentication
```

This hierarchical view prevents the system map from becoming unreadable as additional components are added.

# 14.31.3 Component Inspector
<!-- number: 14.31.3 | label: 14.31.3 | id: s14-31-3 | page: 52 -->

When the user selects a system node, the right-side Inspector becomes a Component

Inspector.

The Component Inspector should provide tabs or equivalent sections for:

Overview

Trace

Metrics

Tests

Errors

Overview

The Overview section should show:

- Component name
- Component ID
- Component type
- Parent subsystem
- Runtime state
- Version
- Start time
- Uptime
- Platform implementation
- Dependencies
- Connected components
- Current task
- Last activity

Trace

The Trace section should display structured runtime events generated by the selected component.

Metrics

The Metrics section should display component-specific runtime metrics.

Tests

The Tests section should display diagnostic tests registered by the selected component.

Errors

The Errors section should display recent warnings, failures, degraded states, and security violations associated with that component.

# 14.31.4 Connection Inspector
<!-- number: 14.31.4 | label: 14.31.4 | id: s14-31-4 | page: 53 -->

When the user selects an edge, the same right-side Inspector should switch into a Connection

Inspector.

The Connection Inspector should display:

- Source component
- Destination component
- Connection type
- Interface or protocol
- Connection status
- Current activity
- Recent request/event count
- Average latency
- Failure count
- Last successful operation
- Last failed operation
- Queue depth where applicable
- Recent connection traces
- Available connection tests

For example:

Connection

Listener Manager

```
->
```

Event Pipeline

Type:

Internal Event Stream

Status:

Healthy

Traffic:

1,245 events / 10 min

Average Latency:

2.1 ms

Failures:

0

Tests:

▶

Emit Synthetic Event

▶

Test Event Delivery

▶

Test Event Normalization

This allows communication failures to be debugged independently from failures inside either endpoint.

# 14.31.5 Trace Timeline
<!-- number: 14.31.5 | label: 14.31.5 | id: s14-31-5 | page: 55 -->

The bottom section of the Debugger should provide a Trace Timeline.

The Trace Timeline displays the lifecycle of a selected task, request, Heartbeat, Scheduler execution, or other traceable operation.

For example:

16:01:02.101

User Instruction

```
↓ 2 ms
```

16:01:02.103

Agent Core

```
↓ 34 ms
```

16:01:02.137

Model Provider

```
↓ 418 ms
```

16:01:02.555

Runtime

```
↓ 7 ms
```

16:01:02.562

Raw Edit Recorded

A compact form may also be displayed:

User

→ Agent Core

→ Model

→ Runtime

→ Raw Edit

Each step should correspond to one or more structured trace spans.

Selecting a step should navigate to the corresponding component and trace information where possible.

# 14.31.6 Trace Timeline Filtering
<!-- number: 14.31.6 | label: 14.31.6 | id: s14-31-6 | page: 56 -->

The Trace Timeline offers these filters: user instruction, heartbeat, scheduled task, diagnostic test, errors only.

A trace can also be reached by its `task_id` or `trace_id`, by component, or by time range.

The user should be able to inspect historical traces after an operation has completed.

# 14.31.7 Search
<!-- number: 14.31.7 | label: 14.31.7 | id: s14-31-7 | page: 57 -->

The System Debugger should provide global search.

Search should support values such as:

component name

component ID

task ID

trace ID

event type

```
error
```

scheduled task

Heartbeat

model invocation

```
timestamp
```

Search results should highlight the relevant system node, connection, or trace.

# 14.31.8 Runtime Activity Visualization
<!-- number: 14.31.8 | label: 14.31.8 | id: s14-31-8 | page: 58 -->

The System Map may visually indicate live communication between components.

For example:

Agent Core

```
======> Model Provider
```

or:

Listener

```
======> Event Pipeline
```

This may be represented through animation or another lightweight visual indicator.

Live visualization must be derived from observability events and must not interfere with system execution.

The Debugger should remain functional if live animation is disabled.

# 14.31.9 Status Visualization
<!-- number: 14.31.9 | label: 14.31.9 | id: s14-31-9 | page: 58 -->

System nodes and connections should visually distinguish runtime state.

The UI should make it possible to quickly identify:

```
running
idle
busy
degraded
blocked
failed
stopped
unavailable
```

Security failures should be visually distinct from normal runtime failures.

For example, an external write blocked by the Sandbox should be identifiable as a security event rather than merely a generic execution failure.

# 14.31.10 Resizable Layout
<!-- number: 14.31.10 | label: 14.31.10 | id: s14-31-10 | page: 59 -->

The three primary regions should be resizable.

The user should be able to:

- Resize the Inspector
- Resize the Trace Timeline
- Collapse the Trace Timeline
- Collapse the Inspector
- Maximize the System Map
- Maximize the Trace view

This allows the same interface to support both high-level architecture inspection and detailed trace analysis.

# 14.32 Debugger Interaction Behavior
<!-- number: 14.32 | label: 14.32 | id: s14-32 | page: 59 -->

The Debugger must define consistent interaction behavior for system nodes, subsystem nodes, connections, traces, and diagnostic tests.

# 14.32.1 Single-Click Node
<!-- number: 14.32.1 | label: 14.32.1 | id: s14-32-1 | page: 59 -->

Single-clicking a component node should:

1. Select the node.
2. Highlight its direct incoming and outgoing connections.
3. Open the Component Inspector.
4. Display the component's current state.
5. Load recent traces and metrics associated with the component.
6. Display diagnostic tests registered for that component.

The System Map should preserve the user's current zoom and viewport.

# 14.32.2 Double-Click Node
<!-- number: 14.32.2 | label: 14.32.2 | id: s14-32-2 | page: 60 -->

Double-clicking a node that contains child components should expand that subsystem.

For example:

Listener

may expand into:

Listener Manager

Browser Listener

Filesystem Listener

Application Listener

Application Integrations

Double-clicking an already expanded subsystem may collapse it.

For leaf components with no children, double-clicking may focus the map on that component and its immediate neighborhood.

# 14.32.3 Single-Click Connection
<!-- number: 14.32.3 | label: 14.32.3 | id: s14-32-3 | page: 60 -->

Single-clicking a connection should:

1. Select and highlight the connection.
2. Highlight its source and destination components.
3. Open the Connection Inspector.
4. Display recent traffic and latency.
5. Display failures associated with the connection.
6. Display connection-level traces.
7. Display diagnostic tests registered for the connection.

# 14.32.4 Trace Selection
<!-- number: 14.32.4 | label: 14.32.4 | id: s14-32-4 | page: 61 -->

Selecting a trace in the Trace Timeline should highlight the participating components and connections in the System Map.

For example, a trace:

Agent Core

→ Model Provider

→ Runtime

should highlight those nodes and edges.

This provides a visual representation of the execution path.

# 14.32.5 Component-to-Trace Navigation
<!-- number: 14.32.5 | label: 14.32.5 | id: s14-32-5 | page: 61 -->

When viewing a Component Inspector, the user should be able to select a trace entry and open its full end-to-end trace.

For example:

Selected Component:

Model Provider

Trace Entry:

task_128 / model.invoke

Selecting that trace may reveal:

Agent Core

→ Model Provider

→ Raw Edit

The Debugger should allow navigation in both directions:

```
Component -> Trace
Trace -> Component
```

# 14.32.6 Error Navigation
<!-- number: 14.32.6 | label: 14.32.6 | id: s14-32-6 | page: 62 -->

Selecting an error should navigate to the component, connection, and trace responsible for that error whenever the relevant information is available.

For example:

Error:

Scheduler task failed

Navigate to:

Scheduler

```
->
```

Runtime

```
->
```

Sandbox

Trace:

```
trace_1827
```

This reduces the need to manually correlate independent logs.

# 14.32.7 Running Diagnostic Tests
<!-- number: 14.32.7 | label: 14.32.7 | id: s14-32-7 | page: 63 -->

When the user selects a test and chooses to execute it:

1. The Debugger creates a diagnostic test execution.
2. A unique task ID and trace ID are assigned.
3. The test executes through the normal runtime and permission boundaries.
4. Test progress may appear in the Trace Timeline.
5. The result is returned to the Inspector.
6. The result is stored in diagnostic history.

The result should contain:

Status

Summary

Execution time

Relevant trace

Relevant error

Recommendations where available

# 14.32.8 Tests Must Not Bypass Security
<!-- number: 14.32.8 | label: 14.32.8 | id: s14-32-8 | page: 63 -->

A diagnostic test must never receive additional permissions simply because it was launched

```
from the Debugger.
```

For example:

Sandbox Test

```
|
v
```

attempt external write

```
|
v
```

Runtime Sandbox

```
|
v
```

write blocked

The test verifies the restriction instead of temporarily disabling it.

# 14.32.9 Synthetic Diagnostic Events
<!-- number: 14.32.9 | label: 14.32.9 | id: s14-32-9 | page: 64 -->

Tests may generate synthetic events.

For example, the Listener diagnostic may create:

diagnostic.browser.navigation

or:

diagnostic.filesystem.modified

Synthetic events must be explicitly tagged as diagnostic.

They must not accidentally be interpreted as genuine user behavior by the Data Management system.

Recommended metadata:

```
{
    "diagnostic": true,
    "test_id": "...",
    "trace_id": "..."
}
```

# 14.32.10 Historical Mode
<!-- number: 14.32.10 | label: 14.32.10 | id: s14-32-10 | page: 64 -->

The Debugger should allow switching from live runtime state to historical inspection.

The View offers: live, last 5 minutes, last 30 minutes, last hour. A custom range is not offered.

When historical mode is active, the System Map may display component state and communication corresponding to the selected period.

# 14.32.11 Heartbeat Inspection
<!-- number: 14.32.11 | label: 14.32.11 | id: s14-32-11 | page: 65 -->

Selecting a historical Heartbeat should reconstruct its execution path where possible.

For example:

Heartbeat

```
|
v
```

Model

```
|
+------> Scheduler / Notification
|
v
```

Raw Edit

The Inspector should show:

- Heartbeat start time
- Checklist snapshot
- Model
- Nodes accessed
- Token usage
- Actions
- Scheduled tasks
- Failures
- Completion state

# 14.32.12 Data Heartbeat Inspection
<!-- number: 14.32.12 | label: 14.32.12 | id: s14-32-12 | page: 66 -->

Removed. The Data Heartbeat was deleted on 2026-10-06, so there is no run of it to inspect. Raw activity is read directly by the agent heartbeat for its own window, and is otherwise only kept and pruned by age.

# 14.32.13 Scheduler Task Inspection
<!-- number: 14.32.13 | label: 14.32.13 | id: s14-32-13 | page: 67 -->

Selecting a Scheduled Task should display its lifecycle:

Task Created

```
|
v
```

Waiting

```
|
v
```

Scheduler Due Check

```
|
v
```

Runtime

```
|
v
```

Execution

```
|
+----> Completed
|
+----> Failed
```

The user should be able to inspect both the Scheduler component and the corresponding execution trace.

# 14.32.14 System Map Focus Mode
<!-- number: 14.32.14 | label: 14.32.14 | id: s14-32-14 | page: 67 -->

The user should be able to focus on a selected subsystem.

For example:

```
Focus: Observation
```

may temporarily display only:

Listeners

```
->
```

Listener Manager

```
->
```

Event Pipeline

```
->
```

Raw Event Storage

Focus is chosen as the whole system or one subsystem.

Focus mode should not modify the underlying system topology.

It is only a visualization filter.

# 14.32.15 Debugger Interaction Requirement
<!-- number: 14.32.15 | label: 14.32.15 | id: s14-32-15 | page: 68 -->

The following behavior is a hard UI requirement:

> The user must be able to select any visible system component or connection, inspect its status and diagnostic information, navigate between component-level and end-to-end traces, and execute the diagnostic tests registered for that component or connection without leaving the System Debugger.

# 14.32.16 Showing the Work in Progress
<!-- number: 14.32.16 | label: 14.32.16 | id: s14-32-16 | page: 68 -->

While the agent is working on a request, the interface shows what it is doing.

A request that takes twenty seconds and says only "thinking" is indistinguishable from one that has hung. The user cannot tell whether to wait, and the one thing the application could tell them - that it is retrieving, or calling a tool, or on its third pass - it already knows.

The stages are recorded as they happen and shown as a list beside the conversation:

- Each stage says what is happening in words the user's own vocabulary would use, not the name of the component doing it
- A stage says what it found, not only that it ran. "Ran a script in your workspace, 2 files written" answers a question that "working" does not
- The stage now running is distinguishable from the ones already finished, and a finished stage says how long it took
- The elapsed time is shown, because the question being asked is whether to keep waiting

> The display describes work in progress, so it goes away when the answer arrives. Finished work is described by the answer itself, and a stale list of stages left underneath it says otherwise.

Two constraints on where the stages come from:

- Progress belongs to the request that is waiting. Background work - a heartbeat that happened to start meanwhile - must not appear in it, or the user is shown the agent doing something they did not ask for
- The record of stages is bounded. A long tool loop must not grow in memory without limit
- Recording progress must never be able to fail a turn. It is an aid to the person waiting, not part of producing the answer

# 14.32.17 Showing What an Answer Was Built From
<!-- number: 14.32.17 | label: 14.32.17 | id: s14-32-17 | page: 68 -->

Removed. Since 2026-10-06 an answer is no longer built from gathered memories: the chat answers from the instruction and the conversation alone, so there is nothing to show beyond the conversation itself. The context trace and the library search it described went with the data heartbeat, and the Debugger's leftover "Context assembled" panel, which could only show an error or nothing, is being removed.

# 14.33 A Panel for Each Module
<!-- number: 14.33 | label: 14.33 | id: s14-33 | page: 40 -->

Every module reports three things, and its panel shows all three:

- **what it is** - its name, and what it is for in a sentence
- **how it is** - the status and metrics it already provided
- **what is in it** - a detail view of its own shape

The third is the new one. A module that reports only a health state and a row count can be wrong in every way that matters while looking healthy: the events are arriving, and they are the wrong events.

## The Detail Is Per Module

Modules are not alike, and one generic table for all of them reproduces exactly the summary that was already unhelpful. An event store is interesting broken down by event type; an edit log by which component wrote each entry; a graph by what kinds of node it holds; a scheduler by what is waiting and what failed.

Each detail view therefore defines its own axis, and offers:

- a **summary** - the few numbers that characterise the whole module
- a **choice** - the axis, with every value and how many of each, so one can be picked out
- **groups** - the most recent entries of each value, at most ten

Ten of each, rather than the most recent ten overall, because a busy kind otherwise crowds out every other. A store holding four thousand focus events and two browser visits should show the two.

## The Map Is Not the Panels' Spare Room

The panels sit below the map, and both live in a shell the height of the

window. The map is the part that flexes, so every pixel the panels below it

wanted came out of the map - and once they added up to more than the shell,

the map was nought pixels tall. A canvas that size draws nothing and raises

nothing: the Debugger showed its panels, showed the states, and simply had no

component graph, with no error anywhere to say why.

**The map keeps a floor.** It is not allowed to be squeezed below the height

at which it is still a map, and the shell scrolls when what is in it does not

fit, rather than clipping the last panel out of reach. A panel added later

must not be able to silently evict the thing it was added beside.

The map is measured by what it draws, not by what it is told to be. An

element of the right size with nothing painted on it is the same failure

wearing the right dimensions.

## Rows Are Shown Whole

An entry is shown with every field it holds, not a chosen few. The reason to look at a stored row in a debugger is to see what was actually stored; a view that picks columns has already decided what the problem is not, which is the decision the reader came to make.

## The Master Switch

The Debugger's own monitoring has a single switch, and off means the monitoring does not run.

This distinction is the whole point of it. A switch that only stops the display leaves every span still being built, stored and evicted - the cost the user turned it off to avoid - while presenting an empty panel as though nothing were happening. So the switch is applied where the recording happens, at the point a span would be kept, and turning it off also drops what had been collected.

Two consequences follow, and both are stated in the panel rather than left to be discovered:

- with monitoring off the panels have nothing to show, and say so, distinguishing *nothing was collected* from *nothing happened*
- turning it back on starts collection from that moment. It cannot recover what was not gathered

The setting is written to settings as well as applied, because a switch that silently turns itself back on at the next launch is worse than no switch.

## Where the Panels Live

The panels sit beside the system map, not inside it. The map has a fixed-height container with its overflow hidden, so that it owns its area and can be panned within it; a grid of panels placed in that container competes with the map for a height neither can have. The map is squeezed to nothing - which makes every control that redraws it, the focus filter among them, appear to do nothing at all - and the panels below the fold are clipped away.

Focus narrows the panels as well as the map, for the same reason it exists at all: asking to see one subsystem means the whole of that subsystem, and a control that moves only the drawing is a control the reader will conclude is broken.

A live refresh must not destroy what is being read. The panels refresh on the same ten-second cycle as the rest of the Debugger, and rebuilding them wholesale each time collapses whatever entry the reader had expanded, loses their place in the list, and returns the type they had chosen to unfiltered - so the panel becomes unreadable exactly while it is being read, which is the only time it matters.

So a refresh writes the new numbers into the panels that are already there, and touches nothing else, whenever the set of modules is unchanged. Where a detail view is open it is re-read, and the entries the reader had expanded are expanded again, their place in the list kept, and the type they chose restored from what the interface itself recorded rather than from whatever the reply happened to echo back.

The panels are loaded when the Debugger is opened and refreshed with the rest of it, rather than at page load. A panel loaded at page load fetches for a tab that may never be opened, reads a focus control that has not been populated yet, and never updates afterwards. The agent's own work is unaffected either way: what stops is the recording of it.

# 14.34 The Debugger Is a Build Option
<!-- number: 14.34 | label: 14.34 | id: s14-34 | page: 40 -->
<!-- note: 2026-10-17: a Resource debugger - what the application and every process it started cost the computer, and what it keeps on disk. -->

Whether the Debugger exists is decided when the application is built. A build made from source carries the debuggers by default; `build.py --no-debugger` leaves them out, and the released installers and downloads are built that way. A build made without it does not contain it: not the panels, not the routes that serve them, not the entry that navigates to them.

This is a build-time choice rather than a setting, because a setting can be turned on. The point is not to hide the Debugger from a user who has not asked for it - it is for the shipped application not to have one.

Everything that depends on the Debugger must ask whether it is present rather than assume, and must behave correctly when it is not. A panel that is absent is not an error condition; it is the ordinary case.

*Amended 2026-10-17:* a RESOURCE DEBUGGER, a tab of its own and, as every debugger, left out of the released builds (`debugger/resources.py`). It says what the application costs the computer: its own process and every process it started, and what those started, each put under what it is for - the application itself, the embedding model, one of the user's runs of work, a way of the Work debugger, a model call outside any run, or anything else - found from the process that started it, the folder it runs in, or a run's folder on its command line. Every two seconds while the tab is open, and not at all while it is not: each process's share of the processors (of the whole computer, all its cores), its memory, and - on Windows, from the system's own counters as Task Manager reads them - its share of the graphics processor's busiest engine and its graphics memory; on Linux with an NVIDIA card the card's use and each process's graphics memory; on macOS it says graphics are not measured. The last fifteen minutes are drawn, a chart for each, the parts stacked so the top edge is all of the application, with the whole computer's own processor use beside it; the table of processes beneath has the same numbers. Then what it keeps ON DISK - its own folder part by part, each Work debugger project's folder, the folders the user's work runs in, and the models it fetched - measured behind the page when the tab opens, every ten minutes while it is open, and when asked. And any process an earlier run of the application LEFT GOING (section 120), with a way to end them, after the user confirms.

# 14.35 Token Usage Over Time
<!-- number: 14.35 | label: 14.35 | id: s14-35 | page: 40 -->
<!-- note: 2026-10-15: every kind of work that still runs has its own colour; only parts removed from the application are Other - they had taken three of the seven colours, and what runs now was put in Other for them. -->

The Debugger shows what the model calls spent, per day by default (over 7, 14, 30 or 90 days) or across the last 600 calls, grouped either by **kind of work** - each heartbeat step, conversation, work runs, scheduled tasks, lessons - or by function (Activity and states, Task organisation, Calendar management, Connections, Customized Recommendation, Experience learning, and "Started by you"), and measured in total, input or output tokens or in estimated cost. The cost is the API list price, even when the calls are made on a subscription, and the chart says so. The seven kinds that spent most each keep a colour; the rest are drawn together as Other.

Separating the lines is what makes the chart worth drawing. The loops run at different intervals and cost different amounts per run, and those two facts pull in opposite directions - something cheap every five minutes outspends something expensive once a day. Only the series shows that.

Each line can be hidden and shown independently, because one loop spending an order of magnitude more than the rest flattens every other line into the axis.

This does not duplicate the existing heartbeat inspection panel (section 14.32.11). That answers what one run did; this answers what the runs cost over time, which is a question about the shape of the whole system and cannot be asked of a single run.

## Over Time Means Across Restarts

The calls were held in memory - a window of the last few hundred, and running totals - so everything began again at zero whenever the application started. That makes the one question worth asking unanswerable: *is this getting more expensive?* A week of heartbeats against last week's, a model against the one before it, the cost of a busy Tuesday: none of that survives a restart, and the agent restarts whenever anything is fixed.

So each call is **written down**: when, which model, which provider, which kind of work, the tokens in and out, how long it took, and what it is estimated to have cost.

- The totals and the breakdowns are computed **in the database**, because the point of keeping every call is that there are eventually a great many of them
- The chart says **what window it is drawing** - the date of the earliest call it still has. "Since the agent started" was true before and was the problem, because it sounded like a claim about the agent rather than about the process
- Calls are kept for a bounded time, 400 days, rather than for ever, and a day is cut at the reader's own local midnight. A row is small; a year of heavy use is tens of megabytes, and a comparison against last month needs last month and not the first month
- What is about *this* run - what is executing now, what just failed - is still only in memory, because it means nothing from a process that has ended

*Amended 2026-10-15:* the parts removed from the application - the data heartbeat and the knowledge library, run until 2026-10-06 - count as Other (their calls stay in the chart's month), and take no colour from what still runs; a function only switched off keeps its own. Every kind that runs has its own: the seven that spent the most take the seven colours, the next seven the same colours striped (dashed, as a line); only past the fourteenth is a kind Other. A task in no module is shown by its own name (`common/modules.py`, `api/web/app.js`) Of the tokens read, what came from the provider's cache - at a fraction of the price - is the faded upper part of each bar. Calls made before learning and each kind of lessons had a name of their own are named when the database opens, once, and only where it can be told for certain: a learning's calls when every call in its window is its own and they come to what it counted; a lessons call by the one kind the engine processed that minute (`data_manage/storage/database.py`).

# 14.36 What Retrieval Costs, as the Library Grows
<!-- number: 14.36 | label: 14.36 | id: s14-36 | page: 40 -->

Removed. Retrieval timing and the library-wide search it measured were deleted on 2026-10-06. Nothing searches the library as a whole any more, so there is no retrieval cost to watch; what the model calls cost is shown by section 14.35.

# 15. Listener Module
<!-- number: 15 | label: 15. | id: s15 | page: 69 -->

Location:

```
./listener/
```

The Listener observes user activity and converts it into normalized events.

The Listener must not perform high-level semantic interpretation.

Its responsibilities are:

1. Observe activity
2. Normalize activity
3. Timestamp events
4. Submit events to the raw event pipeline

# 16. Initial Listener Scope
<!-- number: 16 | label: 16. | id: s16 | page: 69 -->

The first version must support:

1. Browser activity
2. Filesystem activity
3. Application usage

# 17. Browser Activity
<!-- number: 17 | label: 17. | id: s17 | page: 69 -->

The Listener should capture browser navigation context.

At minimum:

- Page title
- URL
- Browser name
- Timestamp

Example:

```
{
    "event_type": "browser.navigation",
    "timestamp": "...",
    "application": "Chrome",
    "data": {
        "title": "Example Page",
        "url": "https://example.com"
    }
}
```

Browser data collection implementation may differ across browsers and operating systems.

The architecture should therefore abstract browser collection from event normalization.

# 18. Future Browser Interaction Tracing
<!-- number: 18 | label: 18. | id: s18 | page: 70 -->

Detailed in-page tracing is not done. An earlier browser extension that captured page interactions - tab switches, clicks, forms, navigation sequences - was removed, together with its website integration and its web event types. What was on a page is known from reading the window in front (section 19.2) and from browser history.

## Recording What Was Said

The conversation itself is recorded, not only that a session happened.

Working through a coding assistant is where a large part of the user's thinking now happens. An agent that may read only the shape of that - which project, how long, how many exchanges - is guessing at the very work it exists to help with, and it will keep guessing however long it runs.

It is also the most personal thing this application holds, so every part of it is deliberate:

- It stays on this machine. Nothing about it leaves
- Unticking the assistant, or turning Assistant activity off in Settings > Privacy, stops it at once
- Switching it off stops it immediately, and **nothing said while it was off is picked up afterwards**. What was said while collection was off was said while collection was off
- The parts of a transcript that are bulk rather than conversation - the assistant's own reasoning, tool calls, tool results - stay out unless separately asked for. They are long, they mostly restate the reply, and a tool result can be an entire file
- A single turn is cut at a stated length rather than dropped, and says it was cut

> Transcripts are read forward from where reading last stopped, not re-read. A session that runs for hours is read once as it grows.

## Which Conversations, Not Whether

One switch for the whole machine is the wrong shape for this decision.

A coding assistant is used for everything its user does. On one real installation it had produced sessions in **31 separate projects** - the application being built, unrelated research, a ranking study, personal work - and the argument for recording what was said, that the agent should see the work it is helping with, does not apply to all of them equally. Offered only "record everything said to any assistant anywhere" or "record none of it", a user who wants the agent to understand one project has to hand it the other thirty.

So the recording is scoped, by three lists:

- **which assistants** - ticked from what is installed. What is ticked is recorded; nothing ticked records nothing, and this list is therefore the switch. A separate on/off beside it would be two controls for one decision, and they can disagree - on with nothing ticked, off with everything ticked - where neither state reads as anything
- **only these projects** - empty meaning all of the ticked assistant's projects
- **never these projects** - checked last, so it overrides the list above

A line names a project, or a directory containing many, or a glob. Separators and case do not have to match what the user typed, because which slash they used is not a rule they should have to know. The exclusions being checked last is what lets a whole research directory be recorded with one project inside it held back.

An empty list of projects means no restriction. An empty list of assistants is not the same thing and must not be read as one: it is what the user leaves when they want none of this recorded, so unticking all of them records nothing. By default every assistant found on the machine is ticked, stored as `*`, so one installed later is included too.

The assistants are detected rather than typed. Naming one in a text box means knowing what the application calls it and being told nothing when the spelling is wrong, so the list is what was found on this machine and the choice is recognition rather than recall. An assistant that is known but not installed is listed as absent rather than omitted - an option that is quietly missing is one the user cannot reason about - and a name chosen elsewhere that this machine does not offer is kept rather than discarded.

The stored value remains a list of names, so what changed is the editor and not the shape every reader of the setting already expects.

The decision is taken per session, and each session event records which way it went. So a session that was seen but deliberately not transcribed is visible as such afterwards, rather than being indistinguishable from one the provider never noticed.

## Not the Agent's Own Voice

A session this application started is the agent thinking, not work done at this desk, and it is not recorded as activity.

The provider answers by running the coding assistant on this machine, and the assistant writes a transcript of every run into the same session store this Listener reads. So each model call came back a moment later as something that had happened at the user's desk: expanding a one-minute stretch of the browser once showed the agent's own background work - asking for keywords, asking what some raw activity meant, folding old records away - all filed under the browser, because the browser was the window in front while the agent worked in the background. (Those background jobs have since been removed; the problem holds for every model call the agent makes.)

It is the loop section 39.1 closes for the agent's own writes, arriving by another road: there the agent heard its own files, here it hears its own voice. It is worse in one way, because interpreting that activity takes a model call, and that call's transcript is more activity. Measured on one installation, **888 of the 951 assistant events in recent history were the agent's own**.

It is told apart two ways, and either is enough:

- **The session is named.** The provider chooses the session identifier before the run and marks it, so the transcript says whose it is wherever it is read. The identifier is the one thing that passes from the run to the transcript, and is still there when a later process reads it - which matters, because the transcripts outlive the process that made them and nothing in memory remembers them after a restart. The mark occupies part of an otherwise random identifier, so a user's own session can collide with it about once in four billion, and the cost of that is one session of theirs going unrecorded
- **The session was run inside the agent's own storage.** Weaker, and the one that can be made about the past: it reads sessions recorded before there was a mark to read. Nobody runs a coding assistant inside the agent's storage on purpose

Turns already recorded from the agent's own sessions are skipped when transcripts are read and are not shown as the user's activity, because they never were. The agent's own calls also ask the assistant not to keep a session at all, so most of them leave nothing to skip.

A run must never reuse another run's identifier, and an unnamed run must not be silently allowed: both put the agent's own thinking back into the record of the user's day.

## What the Extension Captured Is Content

Removed. The browser capture extension was removed in October 2026, together with its website integration; conversations held in web pages are now seen only as screen text, read from the window in front (section 19.2).

# 19. Filesystem Activity
<!-- number: 19 | label: 19. | id: s19 | page: 71 -->

The Listener should monitor local filesystem events such as:

- Creation
- Modification
- Deletion
- Rename
- Move
- Directory creation
- Directory deletion

An event may contain:

```
id
event_type
timestamp
path
source_path
destination_path
application
process
metadata
```

Example:

```
{
    "event_type": "filesystem.modified",
    "timestamp": "...",
    "data": {
        "path": "/path/to/file"
    }
}
```

Listener permission failures must not terminate the entire Listener.

## What Is Watched

The monitor covers the user's own working space by default, not a location the user has to nominate before anything is recorded.

> A monitor watching only a directory the user never works in is indistinguishable, in what it records, from one that is switched off - but it reports itself as running.

The default scope is every drive on the machine (on Windows every existing drive letter, elsewhere the root). The user removes what they would rather it did not see, and may narrow, extend or replace the scope, but they must not have to configure it before the feature does anything; the shipped exclusions carry the churn.

Watching that much is only viable with exclusions, so the application ships them:

- Operating-system and program directories, which are not the user's activity
- Caches, temporary directories and trash
- Dependency and build directories - installed packages, build output, virtual environments
- Version-control internals
- The agent's own internal directory, whose churn is the agent's, not the user's, and the agent's whole workspace, which holds only what the agents make

The shipped exclusions are defaults, visible and editable like any other setting, and the user's own exclusions are added to them rather than replacing them.

A directory that cannot be watched - it does not exist, or permission is refused - is reported as such, and does not prevent the remaining directories from being watched.

The volume this produces is the implementation's problem, not the user's: the monitor must remain responsive with a home directory of ordinary size, and where it cannot keep up it must say so rather than silently dropping events.

## The Agent Does Not Hear Its Own Writing

The agent keeps its memory in the workspace, and the workspace was once watched, on the reasoning that the user's own documents and downloads were kept there too. So every record the agent wrote, and every rebuild of the index it then had, arrived back as a filesystem event, became raw activity, and cost a model call to interpret. Measured on one day: **12,022 of 14,892 events** were the agent listening to itself, `index/state.json` foremost among them, which was rewritten every round.

That is a loop rather than an expense. Interpreting its own bookkeeping produces records about its own bookkeeping, which are files, which are events.

**The workspace is not watched at all.** It holds only what the agents make and keep - delegated work in `agent_work/`, the chat's files (kept per conversation in `Chat/<conversation title>/`), the memory library - and each work run writes from a process of its own, which no claim made in the main process could cover. What the user does with what was made is still seen by the window and screen listeners.

**Ownership, everywhere else.** Writes the agent makes outside the workspace - into a folder the user granted a work run, for example - are claimed for a few seconds so the watcher does not hear them as the user's. What distinguishes that noise is not where the file is but who wrote it, and the agent knows that at the moment it writes.

**And briefly.** A disclaimer lasts seconds. The same file the agent wrote this morning may be edited by the user this afternoon, and that edit is theirs; a permanent list of "our files" would suppress it for ever, where a short claim suppresses only the event the write itself caused.

# 19.1 A Title That Is Only Being Animated
<!-- number: 19.1 | label: 19.1 | id: s19-1 | page: 74 -->

A window title that differs only in its decoration is the same title.

The Listener ends a period of focus when the title changes, because a changed title usually means the user moved to something else. But programs animate their own titles: a terminal running a task rewrites its title several times a second to spin a glyph at the front of it. Compared character by character, every frame is a different title.

The consequence is not cosmetic. One continuous hour of work is recorded as hundreds of stretches of a few seconds each, every one of them a separate row in the activity log, and the record of what the user actually did is destroyed at the point of collection.

- Leading decoration is stripped before two titles are compared
- The decoration is identified by character category rather than by a list of known glyphs. The list of symbols people animate with is not one worth maintaining - an implementation that enumerated braille frames and half-circles met a star in the very next program
- The same rule applies where titles are displayed, so a title is shown as what it says rather than as whichever frame it happened to be on
- Data already recorded does not repair itself, so adjacent stretches of the same window separated by no more than a poll interval are shown as the one stretch they were

> A real gap is still a real gap. Going away and coming back is not a title animation, and must not be merged into a single stretch that claims the user never left.

## Being Read Is Not Being Written

The operating system reports a directory change for several different reasons - the content was written, the file was **read**, an attribute changed, the security descriptor changed - and the watcher library collapses all of them into one "modified".

Recorded as written, a file the user only opened is a file they are told they edited. Measured on one machine: of 399 notifications, **four** were about a file written in the seconds before. The other 395 were files being *read*, reported up to an hour later, when the system got round to flushing the access time.

Which of the two it was is not in the notification, but it is in the file: content that changed has a new modification time, and content that did not, does not. So the two are told apart by remembering the shape of a file - its modification time and size - and asking, and the answer is recorded as a **different kind of event**.

- **Both are kept.** What the user opened is a thing that happened, and reading is most of what anybody does with a file. Dropping the notifications that are not writes would throw away the larger half of the record
- A file heard about for the first time is a write if it was written in the last minute, and a read otherwise. The notification arrives in well under a second; an access is reported up to an hour late, so there is no overlap to get wrong
- What is remembered is bounded, and the oldest goes first. A machine has more files than this is worth remembering about
- The words are separate all the way through: the templater has its own verb for it, in every language it claims, so a memory about a file that was read does not say it was edited

> This also explains a failure that looked like something else. The rule that the agent's own writes are not activity works by claiming a path for a few seconds around the write - and it caught about one in a hundred, because most of what it was trying to catch were not writes at all, and the rest were reported long after any claim had expired. Ownership is the right test and it cannot be applied to a notification that arrives an hour late about something nobody wrote. The memory library, and now the whole workspace, is therefore excluded **by place**, which is honest for that directory: it holds nothing but what the agent and its runs make.

## Only What a Person Did

Watched across the whole disk, the record fills with what the machine does on its own.

Measured over twenty-five minutes on one installation: **3,499 file events, of which 2,950 were a single Windows registry hive log** - 84% of everything recorded, none of it anybody's work. The rest was a chat client's cache, a coding assistant's session files and its configuration, rewritten every few seconds. The file the user had actually opened in that window was not there at all.

Three things separate the user's activity from the machine's, and none of them is sufficient alone.

**Was a person there.** The one signal that says a human did something: how long since the keyboard or the mouse was last touched. A machine left alone goes on writing - hives, caches, sync clients, indexers - and recording that as the user's activity is not a small inaccuracy, it is most of the record. Applied to file events only: a page visited or a window brought to the front is already somebody doing something.

> A platform that cannot answer says so, and silence is read as **yes**. Refusing to record because we cannot tell would turn an unsupported platform into one that quietly monitors nothing, which is the failure this section exists to prevent.

**Which files churn regardless.** A directory exclusion cannot reach these: the registry hive log sits in the home directory itself, beside things worth watching. They are excluded by name, as patterns - hive logs, lock files, half-written downloads, editor backups, database journals, and a coding assistant's own configuration. What is on that list is noise on every machine, not a judgement about anyone's work.

**And whose change it was.** Being at the keyboard says *somebody* was working; it does not say that this particular write was theirs, because a background program writes while they work. What was in front when a file changed is the evidence for which of them it was, and it is **recorded rather than judged**: no rule here could tell an editor saving a file from a compiler writing one the user asked for, and a panel that says "edited while you were in the editor" or "written while you were reading mail" lets the reader tell them apart themselves.

That question is asked at most once a second. A burst of ten thousand file events must not become ten thousand calls into the window manager.

**And the strictest test.** A file change can be required to be about the window in front - its own name, or the name of a folder it is inside, appearing in the title. This is the only test here that speaks about *this* write rather than about the moment it happened in: a chat client's message index never appears in any title and never will. A folder above it counts, which is what saves a build started from a terminal whose title names the project and which writes a hundred files the title never mentions. A name of three letters does not count, because "src" and "bin" are inside half the titles on a machine. This window-title test is now part of "Only what you did yourself", which is on by default and asks about the moment the change happened, alongside the desktop's own record of opened files (see Whose Doing It Was).

## A Rule Added On Tuesday, And Monday's Records

Excluding a file when it arrives does nothing about the days of raw activity already written down (eight are kept by default).

Measured on one machine the day after the exclusions of this section went in: of **24,610 file events in the past day, 10,653 were `ntuser.dat.LOG2`** - a Windows registry journal, ten thousand writes in ten minutes, none of it anybody's work. Every one of them was recorded in the minutes before the rule existed, and every one was shown under Firefox, because Firefox was in front while Windows flushed its registry. Across the whole table it was worse: **50,784 of 54,309 stored file events, 93%**, were caught by rules that already existed.

So the same question - *is this a file that changes wherever it is, whoever is at the keyboard?* - is asked in three places by one function, reading one set of settings:

- the **Listener** asks before recording, which is where it does the most good
- the **Overview** asks before showing, because the panel is where the user meets the kept days that predate the rule
- the **sweep** asks before deleting, for the same kept days

Asking it three different ways is how a filter and a panel come to disagree about what somebody did today.

### Showing And Deleting Are Both Needed

Deleting alone leaves every rule that has not been thought of yet, and the panel would show its results until someone added one. Showing alone leaves the rows in the table, where the agent heartbeat reads them as part of its window and spends tokens on a registry journal.

The sweep runs in the raw-activity housekeeping loop, every six hours (the first five minutes after start-up), beside the prune by age, and is bounded: the database narrows by `LIKE` first - deliberately **wider** than the real rule, never narrower, because a basename match is not something SQLite will do - and the exact question is asked only of what comes back. A year of history is cleared over several rounds rather than one long one. On the machine above it took four: 19,876 rows, then 17,622, then 13,286, then nothing left to find.

> Getting that prefilter wrong is quiet rather than loud. Leaving folder rules out of it, the first version examined 14,763 rows out of the 50,784 its own rules would have caught, reported them all as swept, and looked entirely successful - everything excluded by **where it was** rather than by **what it was called** was invisible to the query and so was never asked about.

## The Desktop Says So At Once

The filesystem's account of a read arrives when Windows flushes the access time. The desktop's account arrives as the file opens.

Measured on one machine, for one file: the Recent folder had it at **17:10:35**, its access time said **17:11:17**, and the filesystem had still reported nothing three minutes later. The user, reasonably, said the agent had missed it.

So the record that is read as *evidence* about who opened a file - the Recent folder, `kMDItemLastUsedDate`, `recently-used.xbel` - is read as a *source* as well. What it covers, it covers immediately and with no inference at all: a program calling `open()` never enters it, so an entry appearing is a person having opened something.

**One opening is one event however many ways we hear about it.** The two timestamps for a single opening are close but not equal - the desktop stamps its shortcut when the application asks, the filesystem reports the access time when Windows flushes - so they are matched by nearness rather than equality, and whichever arrives first is the one recorded.

What this does not cover is the same gap as before: an editor that does not register what it opens leaves no entry, and neither does a command line. Those still wait for the filesystem. But the files a person opens **the way a person opens files** now appear while they are still looking at them.

> Paths are carried as the filesystem spells them. Normalising case is for comparing, and doing it at the source once put `readme.md` in front of somebody who had opened `README.md`.

## A Thousand Files in One Second

With the machine's own churn excluded and somebody checked to be at the keyboard, what was left was still mostly not the user.

A person opens one file at a time. A program walks a tree: a commit reads every file in a repository, a build writes a directory, a search touches everything under a root. All of it arrives while the user is plainly there, in a window that is plainly theirs, and every test above says yes.

Measured over two hours on one machine: of **16,721 file events, 40 arrived alone** in their second. 4,874 arrived in seconds holding more than a hundred. The busiest second held 1,076 - one commit. Recorded file by file, a tree walk is not a small error in the record; it is the record, and it buries the one file the user actually opened.

So how many happened together is itself the evidence. Events wait a second and are then judged in groups - measured from each event to the one before it, not by cutting the clock into seconds. A tree walk that starts at 11:59.998 is one tree walk, and dividing it at the boundary leaves a tail of three files that looks exactly like somebody opening three files; that was found by a test which failed about one run in ten - a handful is a person, since opening a file touches it and perhaps a lock or a backup beside it and saving touches two or three, while a dozen or more in the same second is a program.

Grouped by **the second the file was touched in, not the second we were told about it**. For a write those are the same; for a read they are not, and judging the arrivals as one batch would throw away every read there is - see below.

- The delay costs nothing. Nothing here is read as it happens; a second's lag on a record of what happened today is not a lag anybody can perceive
- **That a sweep happened is counted**, even though its files are not. Dropping four hundred events without a trace would leave the record looking as though nothing occurred in a minute when a great deal did
- Each window is judged on its own, so a sweep does not silence the work that follows it
- What is still waiting when the listener stops is flushed, not dropped. Otherwise the last second of the user's day disappears every time the agent is closed

**And an event says what was in the file, not only that it moved.** A path and a timestamp say somebody touched `fetch.py`; they do not say whether they were fixing the rate limiting or reading it to remember what it did, and that difference is the whole of what a state is for. A read or a write of a text file now carries the beginning of it, bounded three ways, because this is the one provider that could turn a record of activity into a second copy of the disk: **how much of any one file** (twenty thousand characters, about eight pages - enough that a source file is whole and a paper recognisable), **which files are opened at all** (by suffix, because reading the first bytes of every file the operating system mentions in order to discover it was a `.dll` is the cost the list avoids), and **a setting that turns it off**, which can lower the cap and cannot raise it past the one in the code. An unbounded excerpt is not an excerpt.

> **A file whose name says it holds credentials is never opened**, and that check runs before the suffix one - `secrets.json` and `credentials.yaml` are text files by every test this code can apply, and `.env` is not in the suffix list at all, so putting the name check second skipped the commonest secret file for the wrong reason and left the event silent about why. The event is still recorded either way: that the file was opened is a fact about the afternoon, and it is recorded with the reason nothing from inside it was kept.

## When a Read Happened, and When We Heard

Windows keeps a file's last access time in memory and writes it to disk when it gets round to it. The notification comes with that write, not with the read.

Measured on one machine: three files read at 22:12:07 produced **no notification at all** in the following thirty-five seconds, while a single second - 03:02:35 - carried 47 files that had been read across the preceding hour. The lump is Windows flushing. It is not a person doing anything, and it is not the moment anything happened.

Dated by arrival, those 47 are all "read at 03:02:35", which is both wrong and useless: nobody opens forty-seven files in a second, and the hour in which they were opened one at a time is gone. The file itself knows when it was read, so it is asked, and the access time is what the event is dated by.

Two things follow, and both matter more than the timestamp:

- **A burst is a burst of the moment it happened in.** Sixty files a program walked at 21:40 and four the user opened across the morning arrive together; by arrival that is one sweep and all of it is discarded, by the second each was touched in it is one sweep and four files somebody opened
- **What is on screen now is no evidence about an hour ago.** An event dated further back than half a minute is not labelled with the window in front, because the alternative files a morning's reading under whatever happened to be open when Windows wrote

An access time from further back than six hours is not believed at all - beyond the flush it is describing something else, and a row dated to last Tuesday would land outside every window that would ever read it.

> This is a limit of the platform, not of the design: a file the user opens now appears when Windows says so, which may be within seconds or may be within the hour. **Writes are immediate.** What the user saves shows up at once; what they merely read shows up late, in the order it actually happened.

## A Directory Reports Everything Inside It

Windows reports a directory as modified whenever anything inside it changes. Writing one file therefore produced an event for the file and another for every directory above it - the same act, recorded two or three times.

Nothing ever read them. The Overview skips them, the templater discards them; they were a fifth of everything stored, carried through the filter, the database and the compaction to be dropped at the end by each reader in turn.

They are not mapped to an event type at all, so they are never emitted rather than emitted and discarded everywhere downstream, and the type is no longer declared as something the provider produces - a type nothing emits but every reader filters is a type everybody has to know about for no reason. A directory being **created**, renamed or deleted is still recorded: that is something the user did, not the contents reported a second time.

## Whose Doing It Was

Being at the keyboard says *somebody* was working. It does not say this file was theirs.

On a working machine it usually is not. An assistant, a build, a test run and a language server all read and write while the user sits there doing something else, and every test above says yes to all of it: a person is present, the files are not on any exclusion list, and the changes are spread out in time. Measured after the earlier filters were in place, the record was still mostly source files an assistant had read on the user's behalf - `templates.py`, `store.py` - which the user had never opened.

The notification cannot settle it. **On Windows it carries no process at all**, and the ways of getting one - ETW, object-access auditing, Sysmon - each need administrator rights and a permanent agent, which is not a reasonable thing to ask for a desktop application. So the question is answered from the side, by evidence that already exists.

**Two pieces, both positive.** Neither names an assistant, so neither needs updating when a different one is installed:

- **The desktop's own record of what its user opened.** Every system keeps one for its own purposes - the Recent folder on Windows, `kMDItemLastUsedDate` on macOS, `recently-used.xbel` on Linux - and what makes it worth reading is what never goes in it. A program calling `open()` does not appear. Measured: reading three files from Python added nothing to the Windows Recent folder at all, while the files the user had opened in Explorer that evening were each there to the second
- **The window that was in front at the moment it happened**, and whether its title was about this file. Every editor puts the filename in the title bar, which is what makes this work without knowing which editor it is; a folder above the file counts too, which is what saves a build started from a terminal whose title names the project

The two halves of that second test are not equally strong, and the weaker one has now failed **five** times. Each is worth naming, because every one of them was found in a real record rather than by reading the code, and because the shape repeats: **a string guaranteed to be in the title tells you nothing**, and each fix revealed the next guaranteed string.

1. **A folder named after the application in front.** `extensions/browser/firefox/manifest.json`, recorded three times as the user's reading, because the title said "Mozilla Firefox" and one of its folders is called `firefox`. Firefox *was* reading it - as a program checking its own extensions, which is the exact thing the test exists to exclude
2. **The file itself named after the application**, the same clash arriving through the *stronger* test. Fixing the folder rule alone left it: the directory `.../browser/firefox` has `firefox` as its own name, so it matched as a filename and never reached the folder rule
3. **A title that is itself a path**, which has already named its file. Some editors put the whole path in the title bar, and the folder rule exists for the opposite case - a title naming only the project and leaving the file unsaid. Applied to a precise title it stops narrowing and starts vouching: with an editor showing `...\personalized_agent_release\HANDOFF.md`, a source file an assistant was editing and a file in an unrelated folder were both recorded as the user's
4. **A folder whose name names nothing.** `agent` - five letters, long enough to pass the length test, and inside "Personalized Agent". A source file an assistant was reading was recorded as the user's because its path contained a folder called `agent`. `src`, `docs`, `api`, `build` and their like are too common to be evidence and too useful to forbid as folder names, so they do not count towards this one test
5. **A name too short to mean anything** - the length test that the fourth slipped past

So a name **carrying an extension** is compared against the title as it stands, because `firefox.html` open in Firefox is a real file and its name appearing in full is evidence of its own. Everything else - a bare name, a folder - is compared against the title with the application's own name taken out of it.

The obvious worry about the first of these is the terminal: an assistant runs in one, and if its title named the project then every source file the assistant read would match a folder above it and be recorded as the user's work. Measured against eighteen hours of one machine's focus events, it does not. A terminal running an assistant shows the **conversation's** title - about the task, not about the tree - so the files it reads on the user's behalf match nothing. A terminal running a command the user typed shows that command, and the file named in it matches, which is also correct: they typed it. A file manager shows the folder it is looking at, and the files in that folder match, which is correct for the same reason.

Both say "a person did this". **Neither can say the opposite.** A file opened from a command line enters no Recent list; a moment before the agent started has no history behind it. So an absence of evidence is not treated as evidence, and anything that cannot be judged is kept - an agent that read silence as a refusal would quietly record nothing, which is the failure this section opens by warning about.

### Judged Again On The Way Out

A rule improved here has to improve what is **shown**, or the panel goes on displaying what the Listener has by then learned not to record. Five fixes in one day, and each of them left the earlier records standing.

So the stored records are judged again when they are read, by the same function, and nothing is deleted to achieve it. Measured on one machine after the five fixes: of 172 stored file events carrying a window title, **170 stopped being shown** - and the two that remained were a file manager's window with the project folder open, which is somebody looking at their own files. The rest were an assistant's work on the machine.

Two reasons for showing rather than deleting, and the second is the important one:

- most records cannot be re-judged at all. Of 3,542 file events kept on that machine, only 172 carried a window - the others predate the field. An event that cannot be judged is kept, which is the same answer the Listener gives
- **the rules were still gaining a fix a day.** Deleting is a decision that cannot be revisited when the sixth failure turns up; filtering on the way out means every old record follows the rules as they stand, whenever they change

### What Was in Front, an Hour Ago

The second piece needs a history, because the Listener only ever knew what was in front *now*. That is enough for a write, reported as it happens, and evidence about nothing for a read, reported when the access time is flushed up to an hour later.

Nothing new is sampled for it. The Application provider already emits an event whenever the window or its title changes, and those events pass through the manager on their way to storage, so the trail is fed from a stream that exists - a few thousand entries a day, held for the same six hours that a read's own account of itself is believed within.

It is fed **before** the privacy filter rather than after it. A window the user has excluded from being *recorded* is still evidence about who touched a file while it was in front.

### Where Each Platform Can Do Better

Windows has no way to name the process without administrator rights. The other two do, and the design leaves room for it rather than levelling down to the weakest:

- **macOS** has the Endpoint Security framework, which reports file events with the originating process attached - its identity, its code signature, and the *responsible* process, so a helper an assistant spawned is attributed to the assistant rather than appearing as an anonymous interpreter. It needs an entitlement from Apple, a signed application and the user's consent, which makes it a separate piece of work rather than a flag
- **Linux** has fanotify, which reports the originating pid when it can be opened at mount-point scope. That needs `CAP_SYS_ADMIN`; without it the same code falls back to the general path

The general answer stays the same on all three, and a platform that can do better adds a layer rather than replacing one.

## A Watcher That Stopped Says So

Still an object is not still listening.

Watching a whole drive is the case that showed it: the notification buffer for a busy volume overflows, the emitter thread ends, and the observer goes on being an object with a root attached to it. The provider therefore reported **running**, **watching**, and no error, with its count frozen and nothing recorded at all - a monitor that is off and says it is on, which is the failure this section opens by warning about.

So the question asked is whether anything is still delivering, not whether an observer exists: the observer's thread and its emitters, each of which can end on its own. A root whose watcher has stopped is reported as stopped, and the reason says what to do about it, because "stopped" alone does not suggest the answer - the cause is almost always a directory too busy to watch, and the answer is to watch less of it.

## A List Without A Period Is A Number Without Units

Three hours of a browser is a morning or a fortnight depending on what the panel covers.

The Overview carried its window once, beside the total at the top, several panels above the lists it applies to. **Usage by application** is the panel people check against their own memory of the day, and by the time they reach it the period has scrolled away.

Every list drawn from that window now says so, beside its heading, in the window's own terms rather than the words "24 hours" - the period is a parameter, and a panel claiming a day while showing six hours is worse than one claiming nothing.

Marking one list and leaving the ones beside it bare would be worse than marking none: a reader takes the unmarked ones to be something else.

## What Was Read, and What Was Written

The Overview shows the recent file activity in the order it happened, with the two kinds apart.

This is a different question from the list of files worked on, which collapses everything about one file into a count and answers "what has been worked on most". This one answers "what have I just been doing", which is a sequence and has to keep its order.

- Repeated touches of one file in a sitting are **one row**, carrying how many and over how long. A file saved twenty times while it is being written is one stretch of work, not twenty things that happened
- Coming back to it hours later is a second row, because that is a second sitting
- Reading a file and editing it stay apart even when it is the same file in the same minute
- A single touch claims no span, and a span of under a second is not reported as one

# 19.2 What the Window Was Showing
<!-- number: 19.2 | label: 19.2 | id: s19-2 | page: 71 -->

Every other provider records **that** something happened. None records what was in it.

A desktop already publishes what its applications are showing, so that a screen reader can speak it. The same interface answers a question the agent cannot answer any other way, and it answers section 19's question for free as a side effect: what is on the screen in front of somebody is theirs by definition. An assistant reading a thousand files in the background appears in no window at all.

## The Number That Made It Look Impossible

`FindAll` takes a scope. `TreeScope_Descendants` is **4**; `TreeScope_Children` is 2.

Passing 2 returns a handful of shell elements and no content, from every application, with no error at all. Measured before the mistake was found: a text editor with a file open exposed **5 elements and 26 characters**, a browser showing a page exposed 34 elements and 42 characters, and the conclusion drawn from it was that the approach did not work on this platform. After: the editor exposed **61 elements and 951 characters**, including the text of the file.

The lesson is not about a constant. A wrong answer that arrives without an error, from every application, looks exactly like a fact about the world - and the way it was caught was a control: an application that certainly *does* expose its text. When Notepad returned nothing either, the reading was wrong rather than the platform.

## What It Costs, and Therefore How It Is Done

The cost follows the **number of elements**, not the amount of text. Measured on one machine:

| | elements | walked | asked for documents |

|---|---|---|---|

| terminal showing 19,000 characters | 22 | 32 ms | 68 ms |

| browser showing a busy page | 8,880 | **1,827 ms** | **294 ms** |

| file manager | 223 | 179 ms | 287 ms |

So a document-shaped element is asked for its text first - one call for the whole page - and walking every descendant is the fallback, capped at four hundred. The file manager is why the fallback exists: its list of files is a hundred names and not one text pattern between them.

## Only What Is On The Screen

A browser keeps **every open tab** in its tree. Asking a window for its documents returned twelve tabs and 64,371 characters, of which the user was looking at 1,617; one of the other eleven was a mail inbox that happened to be open behind it.

Elements the platform marks as offscreen are skipped, which brings that back to the one tab in front. This is not an optimisation - reading the other eleven is reading things the user is not looking at, which is not what was asked for and not what anybody would expect.

## What Keeps It From Being A Keylogger With Extra Steps

**It is on by default.** That is a product decision and its consequence is worth writing down rather than discovering: somebody who installs or upgrades begins recording the contents of their windows without doing anything. What has to make that defensible is everything that narrows it, and each of these is load-bearing:

- **Only the window in front, and only while somebody is at the keyboard.** A machine left alone still has a window in front of it
- **Never a password field.** The platform marks them, and a marked element is skipped wherever it appears
- **A list of windows never read at all**, matched against both the application and the title - password managers, private browsing, authenticators, banking. This list matters more than any other in the Listener: a filename recorded by mistake is a filename, and a window recorded by mistake is everything that was in it
- **Only what changed**, so a page left open is recorded once rather than several hundred times
- **One switch stops it**, in the same place as every other category, and it is the first one anybody will reach for

The default list cannot be complete, and it is worth being plain about what it is: a set of words that catch the obvious cases on any machine. It does not know which of *this* user's windows they would not want written down. That is the setting immediately below it, and it is the one to look at before deciding whether the default above is right for a given installation.

## Two Windows That Are Not The User

**Its own.** Section 19 already says the agent does not hear its own writing, and reading the screen gives it two new ways to. Its desktop window is the certain case and is recognised by the process rather than the title - the user names the character, so there is nothing in the title bar to match on. Its interface in a browser is a tab like any other, and the application's name in the title is what there is to go on.

Without this, a record of somebody's day fills up with the agent's own summaries read back to it, which is a record of nothing.

**A terminal running an assistant**, which is kept, on purpose, and the reasoning is worth writing down because the conclusion looks like an oversight.

An assistant runs in a terminal, the terminal is in front, and what it writes there looks exactly like something the user is reading - which it also is. It cannot be told apart from the user's own work in a terminal by looking at processes: measured on one machine, `claude.exe`'s parent is `cmd.exe` and its grandparent is `explorer.exe`, while the terminal window's own process has **no descendants at all**, because Windows Terminal hosts its shells through ConPTY rather than as children. The only signal left is the window title, which an assistant sets to the name of the conversation - and matching on that means keeping a list of assistants, which is exactly what the rest of this section is built to avoid.

So it is recorded. The same conversation also reaches the Assistant provider, which reads it from the assistant's own transcript with the speakers marked, so the overlap is between a precise record and a rough one covering the same ground. Reconciling them belongs to whoever reads them, not to a guess made here.

## Only What Changed

A page left open is read every few seconds and is the same page every time. Measured: five readings of one browser tab produced **one record and four findings of no change**.

So what is stored is the lines that were not there at the last reading, and returning to a window after half an hour starts a new sitting rather than being compared against what it said before lunch. Without this, a record of a day's reading is the same thousand words written down several hundred times.

## Reading Is Continuous, Events Are Not

Every other provider records something that happened once - a file changed, a window came to the front - and one event is the natural unit of it.

Reading is not like that. Sampled every few seconds it turns one person reading one page into a hundred events, each a few lines long, and **each of them needs a model** to be understood. Window contents cannot be summed up by their fields the way a path or a duration can: "firefox.exe showed 1,617 characters" is not anything anybody wants - the application and the title are already recorded by the Application provider, so a screen reading without its words is nothing at all.

Measured on one machine: **133 readings over eleven minutes produced 98 events, about 535 an hour** - more than a heartbeat window can usefully carry. Screen sittings are read by the agent heartbeat's steps within their window.

So what is read is gathered into a **sitting** and reported once. A sitting ends when:

- the user switches windows - the clearest signal that a stretch of reading is over, and it keeps the record one stretch per window rather than two interleaved
- enough has been gathered to be worth a record on its own
- it has been held too long, so that somebody reading slowly does not have their whole afternoon arrive in one lump when they finally switch away
- the agent is closing, because what they were reading then is the part of their day most likely to matter

The same words reach the record; there are simply far fewer records carrying them, and each one is a stretch of reading rather than a five-second slice of one. The newest title is the one kept: a browser tab that has been navigated is still the same sitting, and where it ended up is the useful half of where it was.

> Looking less often helps in the same direction and is the cheaper lever - fifteen seconds rather than five - but it is not a substitute. Sampling alone still produces one event per sample; gathering is what makes the number of events follow the number of *things read* rather than the length of the day.

## Where It Works, And Where It Does Not

Terminals and browsers give up everything; editors and file managers give up what they show; **a self-drawn interface gives up nothing at all**, and no amount of work here changes that - the application has no accessibility tree to read. On the machine these measurements come from, a chat client returned 65 characters, all of them the names of its own window classes.

This is the honest limit of the approach and it is worth stating plainly rather than discovering later: the record will be complete for some of what somebody does and empty for the rest, and which is which is decided by the applications, not by the agent.

> On Windows this needs `comtypes`, which generates its bindings from a type library the first time it is asked. A frozen application cannot write inside itself, so the generation happens at packaging time; without it the capability reports itself unavailable rather than failing, in the ordinary way of section 27.

# 19.3 Reading What Has Not Changed
<!-- number: 19.3 | label: 19.3 | id: s19-3 | page: 71 -->

Asked whether the agent had become slow and whether C would help, the honest way to answer was to measure. What the measurements found was three of the same mistake, and none of them was about how fast a line of code runs.

| | one call | how often |

|---|---|---|

| summarising assistant transcripts | **4.4 s**, 557 MB read | every 120 seconds |

| counting the library | 0.4–1.1 s | every status poll, every Memory tab, every heartbeat round |

| the Memory tab | 1.7–3.6 s | it asked for the count **twice** in one request |

(The Memory tab has since been removed; the library count is still read by the status poll and the Debugger.)

**Summarising a transcript means parsing every line of it as JSON**, and a poll re-read every transcript from the last thirty days to find out whether anything had been added. 2,277 files, 557 MB, four and a half seconds of a core, every two minutes - to learn that almost nothing had changed. A transcript only ever grows, so its size and modification time together say whether the copy already read is still the whole of it. The same pass now takes **267 ms**, and one `stat` answers both questions asked of each file: old enough to skip, and changed since it was last read.

> A directory's clock is coarse, and relying on it alone was wrong in a way a test caught rather than a reader: six files written in a loop moved it **four** times, because its resolution is a millisecond or two and writes inside one tick are one tick. A round that writes three records would have added three files the count could not see. So the library says so itself when it writes or removes one, and a short expiry catches what changed outside this process inside the same tick.

**Counting the library walked every file.** `Path.is_file()` is another `stat` per entry, and a directory entry already carries the answer - so the walk itself was twice the price it needed to be. But the walk did not need repeating at all: a folder's modification time moves when a file is added to it or removed from it, and stands still when one is merely rewritten, which is exactly the distinction a count needs. **Five stats replace seven thousand**, and the answer is never stale rather than being stale for a bounded time.

## Would C Have Helped

No, and the measurements say why. The 4.4 seconds was reading half a gigabyte and parsing it; the 0.4 to 1.1 seconds was seven thousand filesystem calls. Both are work the machine does in the kernel or on the disk, and a faster language pays the same price for them. What made them go away was **not doing them** - the same thing that made every earlier optimisation in this document work:

- asking a browser for its documents rather than walking 8,880 elements: 1,827 ms to 294 ms
- narrowing the Recent folder by time before sorting and parsing it: 220 ms to 13 ms
- writing a query the index can serve instead of one it cannot: 480 ms to 120 ms
- excluding what should never have been recorded: 54,309 stored file events to 3,525

A rewrite is worth considering when a profile points at a function that is genuinely computing something. Nothing here was. The shape of this program - waiting for a model, waiting for a disk, waiting for another process across COM - is one where the language is rarely the limit, and where the thing to look for is work that is being done more than once.

# 20. Application Usage
<!-- number: 20 | label: 20. | id: s20 | page: 72 -->

The Listener should capture application usage.

At minimum:

- Application name
- Application identifier where available
- Foreground/focus state
- Activation time
- Deactivation time
- Foreground usage duration

Where permitted, the system may also collect:

- Active window title
- Process identifier
- Executable identifier

The system must not assume that every operating system exposes identical metadata.

## The Current Period Counts, and a Refusal Is Not Silence

The Overview must count the application's current focus period up to the time of the request; it must not wait for the user to switch applications before showing that time. A period that is still running is still time spent, and an interface that shows it as nothing is wrong about the thing the user is doing *now*.

If the operating system refuses the foreground-application query, the Listener must say so in its status rather than present an empty activity history as though no activity occurred. An empty history and a refused permission look the same on screen and are not the same thing: one is a quiet day, the other is a setting the user can fix.

**Current activity does not wait behind churn.** There is no backlog: each heartbeat reads the window since the last one, whole, and a window too large for one review is read in turns, oldest first.

# 21. Normalized Listener Event Model
<!-- number: 21 | label: 21. | id: s21 | page: 73 -->

All Listener implementations must produce a common event representation.

Recommended conceptual schema:

```
ListenerEvent:
    id: str
    event_type: str
    source: str
    timestamp: datetime
    application: str | None
    data: dict
    metadata: dict
```

Example:

```
{
    "id": "event_...",
    "event_type": "application.focused",
    "source": "application",
    "timestamp": "...",
    "application": "Visual Studio Code",
    "data": {},
    "metadata": {}
}
```

The exact serialization format may change, but the normalized abstraction should remain stable.

# 22. Listener Provider Interface
<!-- number: 22 | label: 22. | id: s22 | page: 74 -->

The Listener must provide a generic extension interface.

Conceptually:

```
from abc import ABC, abstractmethod
```

```
class ListenerProvider(ABC):
```

```
@abstractmethod
def start(self) -> None:
    ...
```

```
@abstractmethod
def stop(self) -> None:
    ...
```

```
@abstractmethod
def get_status(self) -> dict:
    ...
```

```
@abstractmethod
def supported_event_types(self) -> list[str]:
    ...
```

```
@abstractmethod
def get_capabilities(self) -> dict:
    ...
```

Listener Providers should emit events through a shared event interface.

For example:

emit(event: ListenerEvent)

Providers must not directly modify Data Management storage or the memory library.

# 23. Listener Manager
<!-- number: 23 | label: 23. | id: s23 | page: 75 -->

A central Listener Manager should manage providers.

Conceptually:

Listener Manager

```
|
+-- Browser Provider (history)
|
+-- Filesystem Provider
|
+-- Application Provider
|
+-- Screen Text Provider
|
+-- Screenshot Provider
|
+-- Assistant Transcripts Provider
|
+-- Application Integrations (e.g. VS Code)
```

Responsibilities include:

- Provider discovery
- Provider initialization
- Provider configuration
- Starting providers
- Stopping providers
- Health checks
- Error isolation
- Event routing

# 24. Listener Plugin System
<!-- number: 24 | label: 24. | id: s24 | page: 76 -->

Future Listener functionality should be installable through a stable extension model.

Each plugin should describe:

Plugin ID

Plugin Version

Supported Platforms

Supported Event Types

Required Permissions

Configuration Schema

Start

Stop

Status

Capabilities

Example:

```
listener/
    integrations/
        ingest.py      (ships: the signed ingest for application integrations)
        custom_app/    (a third-party module exposing create_providers is discovered here)
```

A plugin failure must not terminate unrelated providers.

## What Became of the Extensions

Removed. The browser extension, its Debugger card and the code that reported on it were removed in October 2026. With nothing outside the process sending browsing data, there is nothing whose silence needs explaining; the browser is read from its own history.

# 25. Listener Privacy Controls
<!-- number: 25 | label: 25. | id: s25 | page: 77 -->
<!-- note: 2026-10-16: the user asked for a Record log before the Debugger - the latest hundred things recorded, each saying which privacy switch recorded it, as the evidence for choosing which to turn off - and asked whether the filtering removes a record or only hides what is important in it. -->

Monitoring categories must be configurable.

Each source is switched on or off by itself in Settings > Privacy, which says beside each what is lost without it and what it costs the computer:

- Application activity
- Filesystem activity
- Browser history
- Application integrations
- Assistant activity
- Screen text
- Screenshots

All are on by default. There is no single master switch; the sources are the switches.

Application and website allow/deny lists, directory and file-name exclusions, and a list of windows never read are all supported. Passwords, one-time codes and keys are cut out of everything recorded before it is kept (on by default).

*Amended 2026-10-16:* THE RECORD LOG, a tab of its own before the Debugger and in every build: the latest hundred things recorded, from every source, each with its time, a title, a line of what it holds, the switch in Settings > Privacy that recorded it and how many passwords or codes were hidden in it; opened, the whole record. Below it, what the rules kept out since the program started - by which rule and from which source, never what was in it, which is the point of keeping it out - with a count for each rule; the agent's own files, thousands an hour, are only counted. Two kinds of filtering are told apart there: a switch, an allow or deny list, an excluded folder or file name, "only while you are here" and "only what you did yourself" keep a record out WHOLE; hiding passwords and codes keeps the record and replaces only them with "[hidden]", except in a screenshot, which is not kept at all. Neither is decided by a model.

*Amended 2026-10-16:* and the Record log shows WHERE something was hidden: the latest records of the last week with "[hidden]" in them, as they were kept - each field it is in, by name, with the place marked - and every "[hidden]" marked in the records above. What was there before is not shown: it was never stored, and keeping it to show would undo the filtering it shows. What a rule kept out is shown by its source and its rule only.

## Private Mode Is a Control

Removed. Private mode was removed in October 2026; what is listened to is chosen one source at a time in Settings > Privacy.

## Assistant Activity

Work done through an assistant - a coding assistant, a desktop agent - is user activity and is a source in its own right.

Recording only that such an application was in use is not sufficient: the application name and a duration describe none of the work. Where such a tool keeps a local record of its sessions, the Listener may read it as it reads browser history, and record for each session:

- Which tool
- Which project or workspace it was working in
- When the session began and when it was last active
- How much exchange it contained

This source is on by default. Session metadata is always recorded; what was said is recorded for the assistants ticked in "Record conversations from" (every one found, by default) and limited by the project lists (section 18), with the assistant's own reasoning and tool calls left out unless asked for and each turn cut at a stated length. It stays on this machine, it is covered by the same exclusions as every other source, and switching it off in Settings > Privacy stops it at once.

*Amended 2026-10-17:* BESIDE EACH SWITCH, WHAT THE OPERATING SYSTEM MUST ALLOW for it, looked at without asking: allowed, not allowed, or that it cannot be told - with, where it is not allowed or cannot be told, a link that opens the very place it is switched on (`platform_layer/permissions.py`). On a Mac: Full Disk Access beside file and browser watching, Accessibility and Automation (System Events) beside the application in front and reading the screen, Screen Recording beside its pictures, and Notifications beside the notifications switch - which macOS lets no program read, only open. On Windows: notifications, off for every program or for this one, read from the user's own registry and never written; the link opens Settings at Notifications. On Linux: a Wayland session lets no program see the window in front nor picture it, chosen at sign-in rather than in a setting - said, with nothing to open. Looked at again whenever the page comes back into view, since that is where the user has just been. Nothing here changes a setting.

# 26. Platform Abstraction Layer
<!-- number: 26 | label: 26. | id: s26 | page: 78 -->

Location:

```
./platform_layer/   (base.py, windows.py, macos.py, linux.py)
```

The OS-level sandbox backends live in `runtime/isolation.py`.

The Platform module isolates operating-system-specific functionality.

Supported systems:

- Windows
- macOS
- Linux

Conceptually:

Common Platform Interfaces

```
|
+-- Windows
|
+-- macOS
|
+-- Linux
```

# 27. Platform Interfaces
<!-- number: 27 | label: 27. | id: s27 | page: 78 -->

The common abstraction may include components such as:

```
class PlatformActivityProvider:
    def get_active_application(self):
        ...
```

```
def get_active_window(self):
    ...
```

```
class PlatformNotificationProvider:
    def send_notification(self, message):
        ...
```

```
class PlatformProcessProvider:
    def get_process_info(self, pid):
        ...
```

```
class PlatformSandboxProvider:
    def create_restricted_process(self, config):
        ...
```

High-level components should depend on these abstractions rather than platform-specific code.

# 28. Platform Capability Detection
<!-- number: 28 | label: 28. | id: s28 | page: 79 -->

Not every operating system will expose identical functionality.

The system should maintain a capability map.

Example:

```
{
    "active_application": true,
    "active_window_title": true,
    "filesystem_events": true,
    "browser_history": true,
    "process_isolation": true,
    "native_notifications": true
}
```

Components should detect capabilities at runtime.

Unavailable optional capabilities must degrade gracefully.

# 29. Runtime and Sandbox Layer
<!-- number: 29 | label: 29. | id: s29 | page: 80 -->

Location:

```
./runtime/
```

This component enforces execution restrictions for agent-controlled code.

It should cover:

- Generated Python scripts
- Scheduler scripts
- External commands
- Subprocesses
- Temporary files
- Filesystem writes
- Work runs (each in a child process of its own, scheduled by the resource governor)

A run of delegated work is a separate process at lower priority; a governor decides how many may run at once from the free processors and memory, lowers them when the computer is busy and pauses the newest when memory is short.

# 30. User-Authorized Writable Workspace
<!-- number: 30 | label: 30. | id: s30 | page: 81 -->

The writable root defaults to a folder in the user's home and can be moved; the user does not have to configure it before anything works.

For example:

```
<AGENT_WORKSPACE>/
```

All agent-controlled writes must occur under this directory.

Examples:

```
<AGENT_WORKSPACE>/documents/
<AGENT_WORKSPACE>/tasks/
<AGENT_WORKSPACE>/generated/
<AGENT_WORKSPACE>/downloads/
<AGENT_WORKSPACE>/tmp/
```

Agent-controlled writes go to the workspace, with one exception the user makes deliberately: a folder they grant to a piece of delegated work becomes writable for that work's runs. A whole drive or the home folder itself cannot be granted.

The directory the user configures is the single writable root defined in section 110.1. The agent workspace is the workspace subdirectory of that root, and the application's internal state is a sibling subdirectory of the same root.

# 31. Filesystem Security Model
<!-- number: 31 | label: 31. | id: s31 | page: 81 -->

The following operations must be restricted:

- File creation
- File modification
- File deletion
- Directory creation
- Directory deletion
- Downloads
- Archive extraction
- Temporary-file creation
- Generated script output

The restriction also applies to indirect writes.

For example:

Agent

```
|
v
```

Generated Python Script

```
|
v
```

Subprocess

```
|
v
```

Filesystem

The subprocess must not gain additional write privileges.

**How this is enforced, and what it does not cover.** Generated scripts run

under an in-process guard that replaces the filesystem surface of the standard

library - `open`, `os.open`, the `os` and `shutil` operations that create,

move or delete - so that every path is checked against section 32 before the

call proceeds. Temporary files are redirected into the agent's runtime

directory by the same mechanism.

That guard confines a script only as long as the script keeps using the

functions it patched, and the requirement above - that a subprocess gains no

additional privileges - is precisely where it stopped holding. Three routes

went around it, each demonstrated writing a file outside both the workspace and

the user's home directory:

| Route | Why the filesystem guard did not see it |

| --- | --- |

| `subprocess`, `multiprocessing` | the child is a new process with the user's own authority and an unpatched standard library |

| `os.system`, `os.popen`, `os.startfile`, `os.exec*`, `os.spawn*` | the same, by way of the shell or a direct exec |

| `ctypes` | calls the operating system directly; no guarded function is involved |

Starting a process and loading a native library are therefore refused outright

inside generated scripts. The refusals sit at the lowest point each route

passes through - process creation itself, and the native library loader -

rather than on the familiar names, so that reloading `subprocess` or

re-importing `os` yields a module whose escape route is still closed.

This costs generated scripts the ability to run external programs. That is the

correct trade: a sandbox that permits an unguarded child process is not a

sandbox, and section 32 already records that path validation alone is

insufficient for arbitrary generated code.

What remains is a limit worth stating plainly. These blocks are applied inside

the interpreter that runs the script, so they constrain a script that is merely

careless or mistaken, and they raise the cost for one that is hostile without

reducing it to zero. The durable answer is the OS-level isolation section 32

asks for, and it now exists.

**Generated code now runs inside an operating-system sandbox where one is

available** - an AppContainer on Windows, Seatbelt on macOS, bubblewrap or

Landlock on Linux - which confines its files and its network whatever the code

calls. Inside such a sandbox the outright refusals above are relaxed: a script

may start Python again (with the guard loaded first) and load native libraries;

any other program is refused. It opens no network connection, except the

package installer, which reaches only the package index. Where no sandbox is

available, the in-process guard is all there is, the run says plainly that it

is not OS-isolated, and package installs cannot be automatic. There the guard

should be read as a strong constraint rather than a proof.

# 32. Path Validation
<!-- number: 32 | label: 32. | id: s32 | page: 82 -->

Application-level validation must account for:

- Absolute paths
- ..
- Symlinks
- Junctions
- Relative-path ambiguity
- Path normalization
- Case normalization where relevant
- Mount points
- Temporary directory redirection

Conceptually:

```
def validate_write_path(requested_path, workspace_root):
    resolved_path = resolve_securely(requested_path)
```

```
if not is_inside(resolved_path, workspace_root):
    raise SecurityViolation()
```

However:

> Path validation alone is insufficient for arbitrary generated code.

OS-level execution isolation should also be used whenever technically possible.

**The agent acts, and writes in one place.** The agent is meant to do things,

not only to describe them, so it holds a real write permission. That permission

is confined to a single subdirectory of the workspace, `<workspace>/agent/`,

created for it and shaped like the workspace around it:

```
<workspace>/
    documents/      the user's own
    downloads/
    exports/
    generated/
    agent/          the agent's, and the only place it may write
        documents/
        downloads/
        exports/
        generated/
        .agent_runtime/
```

Reading and writing are therefore scoped differently, and deliberately so. The

agent reads the whole workspace, because an agent that cannot see the work it

is helping with is not much help. It writes only its own directory, because the

workspace also holds the user's own material - what they put there, and what

they asked the agent to produce and then edited themselves - and an agent that

may write anywhere in the workspace may overwrite any of it. The cost of the

narrower permission is one directory level; the cost of the wider one is the

user's work.

Everything the agent produced is then in one place: the user can look at it,

keep it, or delete the lot, without first working out which files were theirs.

Delegated work is the second place: each piece of work writes only in its own

folder under `agent_work/` and in the folders the user granted it, and the

operating system keeps one work's code out of another's folder. The chat's

files are kept per conversation, in `<workspace>/Chat/<conversation title>/`.

**The workspace is layered, not flat.** Three layers, from most open to least:

| Layer | Read | Write |

| --- | --- | --- |

| `<workspace>/agent/` | yes | yes |

| the rest of the workspace - the user's own material | yes | no |

| `internal/` - settings, credentials, the event log | no | no |

The read-only layer is enforced on its own rather than left to follow from the

narrow write root. Both rules would refuse the same write, but only one of them

can say why it matters: *that is the user's own material* is a different fact

from *that is not your directory*, and it is the one worth reporting. Naming

the layer also means the protection survives any later change to where the

agent writes.

The event log sits in the internal state, not the workspace, and so is neither

readable nor writable by agent-controlled code. What was recorded about the

user is not something the agent can quietly revise.

Relative paths resolve against the agent's own directory, in reads as well as

writes, so `documents/notes.md` names one file rather than two. A read finds

nothing there falls back to the wider workspace, which is how the agent reads

the user's material by name.

# 33. Cross-Platform Sandbox
<!-- number: 33 | label: 33. | id: s33 | page: 83 -->

The implementation mechanism may differ between operating systems.

The common architecture should expose:

```
class SandboxProvider:
    def execute(
        self,
        command,
        writable_root,
        readable_paths=None,
        environment=None
    ):
        ...
```

Platform implementations should attempt to guarantee that:

write access = USER_DEFINED_WORKSPACE only

while read access may be broader according to user permissions and system policy.

Platform-specific implementations belong under:

```
platform_layer/   (base.py, windows.py, macos.py, linux.py)
runtime/isolation.py   (the OS-level sandbox backends)
```

The rest of the agent should not depend on the specific sandbox technology being used.

# 34. Agent Heartbeat
<!-- number: 34 | label: 34. | id: s34 | page: 84 -->

Location:

```
./heartbeat/
```

The Agent Heartbeat periodically activates a configured AI model.

The interval is user-configurable (fifteen minutes by default).

The model used for Heartbeat reasoning may also be configurable.

# 34.1 The State Calendar
<!-- number: 34.1 | label: 34.1 | id: s34-1 | page: 84 -->

A **state** is something that is true over a stretch of time.

A state is written by the agent heartbeat for the window of activity it has just read, and covers only that window; something longer-lived is the same situation written again in each window it survives. States are not extended or judged afterwards. The state heartbeat, on its own clock (every twelve hours by default), groups them into the tasks and projects the user sees. States are internal: they are looked at in the Debugger, and chat does not use them as context.

**Superseded: the floor.** States were once all put in front of the agent for every answer, and past a limit chosen among by keyword overlap with the question. Chat is no longer given states at all, and the index that overlap used no longer exists, so neither the floor nor the choosing remains.

Three things distinguish a state from a scheduled task:

- **A state is not executed.** It says what is the case, or what is meant to happen. The scheduler is what makes things happen. Showing an intention and acting on it are different things, and conflating them turns every note about the future into something that fires
- **A state covers a span, not an instant** - the stretch of the window it was written for, during which the user was, say, writing the report. A task has a moment; a state has a period
- **A state says where it came from.** What the user said, what the agent inferred from watching them, or what accompanies a task the agent scheduled. An answer that treats those alike will eventually tell the user they decided something they never decided

## A State Runs When It Actually Runs

The period is stated by whatever writes the state, because only it knows what the state is about.

Offering a choice between two fixed shapes - a short one and a long one - and then assigning a fixed window to each produces spans that are simply false: every long state becomes exactly a week, centred on the moment it was written, and therefore still running for days afterwards whatever it describes. A state that began on Tuesday and ended on Thursday cannot be said at all.

- A state carries when it began and when it ended, inside the window it was written for
- No horizon is asked for or computed. The dates say how long a state runs, and `span_hours` gives the same thing as a number
- A state does not claim the days after the moment it was written. A state written now is being written about now, and extending it forwards claims a future nobody described

## What Is Not a State

Something that has already happened is not a state.

"The decision was made", "they read the paper", "the meeting finished" are facts about the past. Written to the calendar they sit there claiming to be the user's current situation, days after they stopped being anything of the kind - and they are then read back as though they were still going on.

A finished thing is an observation, not a state. A state holds what is true over a period that includes the window it was written for.

## Two Horizons

Superseded. There is no long term and short term any more: every state covers the window it was written for, and what spans days is made by the state heartbeat, which groups the window states into tasks and projects.

## The Agent's Own Intentions Are States Too

Superseded. The state step does not record the agent's own intentions as the user's state; a calendar of the agent's intentions described back to the user as their state is a calendar about the wrong person. A notification the agent schedules still has a state accompanying it, which the scheduler closes when the task is done.

## A State Can Be Opened

A state can be opened wherever it is shown - in the Debugger's states grid, and from a trail that names it.

Being told that the working day is US Eastern, with no way to ask who decided that, when it applies, or whether it was inferred rather than said, is worse than not being told: the user cannot judge it, and cannot correct it.

- Opening a state shows its period, its status, how many times it has been extended, and its detail
- It says **where the state came from** in plain words - what the user said, what the agent inferred from watching them, or what accompanies a scheduled task. The difference between the agent's guess and the user's instruction is the whole reason the source is recorded
- It says that a state is shown and never run, because that is the one thing that distinguishes it from the tasks beside it on the same grid

## An Inference Carries Its Evidence

A state the agent inferred records what it inferred that from, and how.

The agent is describing the user's own situation back to them, and it can be wrong about it. "They are working on the report", offered with nothing behind it, is a claim the user can only accept or delete - they cannot argue with it, correct the reasoning, or tell a good inference from a lucky one.

- The evidence is what was actually seen - the activity, the file, the page, the message - not a restatement of the conclusion
- The reasoning says how that evidence gets to this state, which is the part the user can disagree with
- Both are shown when the state is opened
- An inferred state with no evidence recorded says so, rather than showing nothing where the reasoning should be. A missing justification that renders as empty space is indistinguishable from one that was never asked for
- The trace is bounded. A state is not a place to keep a transcript

## Ending a State

Superseded. No heartbeat step judges, extends or closes states any more. A state ends where the window it was written for ends, so it simply stops covering the present once its end has passed; something still going is written again in the next window. Only the scheduler closes a state, the one accompanying a task it has finished.

# 34.2 A State Is Also a File, and the File Is the State
<!-- number: 34.2 | label: 34.2 | id: s34-2 | page: 84 -->

Every state is a file in ``states/`` (section 39.1), and that file is rewritten whenever the state changes. Creation, extension, closing, and a move that came from the user editing the task on the calendar (section 60.1) all reach it.

**A state is the one thing in the library that is rewritten rather than added to.** Everywhere else a round writes new files and saying the same thing twice is how the library records that it happened twice (section 49.5). A state is not an observation - it is a claim about what is true *now*, and it has a status that supersedes whatever it said before. A folder holding one file per change would say a dozen contradictory things about one afternoon and leave the reader to work out which is current, which is the failure the state's own status field exists to prevent.

What changed is not thrown away: the history goes in the body, under the current text, so that opening the file shows what is true now and what it was before.

The file is a mirror, not the record. A state is written to the database first, and a failure to write its file is logged and otherwise ignored - the next change rewrites it. Losing a state because a disk was busy would be a far worse outcome than a file that is briefly out of date.

# 35. Heartbeat Checklist
<!-- number: 35 | label: 35. | id: s35 | page: 85 -->
<!-- note: 2026-10-18: the next-steps step is gone with the tasks' next steps (section 37); the user's own to-do list stays. -->

The initial checklist contains:

1. Analyze the user's current state.
2. Determine whether there is anything useful the agent can do for the user.
3. Determine whether the heartbeat checklist should be updated.

> **As amended, the checklist is five fixed steps and cannot be edited** - not by the user, and not by the agent.

>

> 1. **Analyse the user's current state** and write it as states for this window (section 34.1)

> 2. **Find the people in this activity** and what they are doing, for Connections

> 3. **Determine what kind of person the user is** - kept but paused, because the user's nature is now learned from their lessons

> 4. **Determine which notifications to add or disable**

> 5. **Determine which next steps of the user's projects are now done** - skipped when there are none open

>

> Steps 2, 4 and 5 belong to the Connections, Calendar management and Task organisation functions and stop when that function is switched off in Settings > Functions. The step about updating the checklist is gone, because there is no longer a checklist to update, and so is the step that judged the states. Customized Recommendation is not a step: it runs on its own clock, at the configured times (08:00 by default).

## Why It Cannot Be Edited

A heartbeat whose steps can be rewritten by the thing running them can quietly stop doing the one thing it exists for, and nothing shows that it happened.

The earlier design let the agent rewrite this list from its own output, and let the user retire steps from it. Both were wrong in the same way: the procedure that produces the agent's understanding is not one of the things that understanding may revise. What the agent may change is the *content* of what it knows - the states, the contacts, the notifications - never the procedure that produces it.

- The five steps are fixed in order and cannot be added to, removed or reordered. A step stops running only when the function it belongs to is switched off - or, for the nature step, while it is paused
- The order is the reasoning: work out what is going on, who else is in it, who it is happening to, what is worth interrupting the user for, and which of their next steps are now done
- Each step produces something a later step or another part of the application depends on. The states are read by the later steps and grouped into tasks and projects by the state heartbeat; the people go to Connections; the notifications go on the calendar; the ticks go to the projects' next steps
- Where the checklist is shown, it is shown as fixed, with what each step is for

The checklist was once mutable, with changes taking effect only at the next execution and each run working from a snapshot captured when it started. With a fixed list there is nothing to change; a run still takes its list at the start.

## A Step Can Finish

Removed. The checklist is now five fixed, read-only steps, so no step is added, retired or restored, by the agent or the user. A step stops running only when its function is switched off in Settings > Functions.

## The List Has a Bound

Removed. With five fixed, read-only steps the list cannot grow, so there is no bound to enforce and nothing to refuse.

## The Three Written-In Steps

Superseded. The checklist is no longer editable by anyone. It is a fixed list of five steps, in a fixed order: analyse the user's current state; find the people in this activity; determine what kind of person the user is (kept but paused); decide which notifications to add or disable; and tick the next steps of the user's work that are now done. A heartbeat whose steps could be rewritten by the thing running them could quietly stop doing the one thing it exists for. There is no per-step enable switch: a step stops running only when the function it belongs to is turned off in Settings > Functions.

## The Checklist Is Shown Beside the Graph

Removed. The knowledge graph panel was replaced by the library folders, and the checklist display went with it; a fixed checklist has nothing that needs showing beside the graph. The Heartbeat tab of the Debugger shows each step of a run.


*Amended 2026-10-18:* THE NEXT-STEPS STEP IS GONE. Tasks have no next steps any more (section 37, as amended): the review's fifth step, which ticked the finished ones, is removed with them, and so is everything that showed or edited them. The user's own to-do list (section 129) is a different thing and stays.

# 36. Heartbeat Lifecycle
<!-- number: 36 | label: 36. | id: s36 | page: 85 -->

Heartbeat Trigger

```
|
v
```

Load Checklist Snapshot

```
|
v
```

Read the Window of Activity Since the Last Review

```
|
v
```

Run Each Step in Turn, Each on Its Own Copy of That Window

```
|
v
```

Write States, Contacts, Notifications and Ticked Next Steps

```
|
v
```

Record Raw Edits

```
|
v
```

Exit

No graph or library is searched: the window of activity since the last review is read and folded once, and each step gets its own copy of it. The only things a run writes are states, contacts, notifications added or disabled, and next steps ticked off.

The model should terminate after the Heartbeat completes.

It should not remain permanently active.

# 36.1 An Agent That Never Acts
<!-- number: 36.1 | label: 36.1 | id: s36-1 | page: 87 -->

The instructions that govern what the agent does must name the cost of doing nothing, not only the cost of acting.

An instruction that says only "interrupting has a cost, decide against it freely" is answered correctly by never doing anything. The model is optimising the one cost it was given. The result is an agent that reports itself as running, produces a calendar with nothing on it, and cannot be told apart from one that is broken.

## Scheduling Is Not Interrupting

These are different acts with different costs, and treating them as one suppresses the harmless one along with the costly one.

- A **notification** interrupts. It takes the user away from what they are doing, so it must be worth that
- A **scheduled task** did not. It was an entry on a calendar the user reads when they choose, and removes in one action

The heartbeat no longer schedules silent tasks. The step that decides what to do is now the notification step, and what it can add is notifications; a notification pops up when its time comes, so it interrupts. The step is therefore told that an empty list is the right answer most of the time, and never to add a duplicate of a notification already ahead.

## Say What Would Be Worth Doing

An instruction that lists only what not to do leaves nothing to do.

Alongside the caution, the step is told what does justify a notification: above all, a promise the user made to do something later, set for the moment it becomes useful. It looks for those first. Given examples of the shape of a useful notification, it can recognise one.

# 36.2 Step Four Is Told What Has Run Out
<!-- number: 36.2 | label: 36.2 | id: s36-2 | page: 87 -->

Removed. The state-review step, with its prompt, its verdicts and its ended and running groups, was taken out of the heartbeat (commit 361a71b). Nothing closes a state any more: a state simply stops covering the present once its end has passed.

# 37. Heartbeat Invocation Restriction
<!-- number: 37 | label: 37. | id: s37 | page: 86 -->
<!-- note: 2026-10-16: the user asked that every task's grouping be checked against their labelled week, and that small subjects may get a little worse; reading only telling places put fewer states in the wrong task. -->

The Heartbeat subsystem is controlled by its background timing mechanism. A full heartbeat is still started only by its timer or by the Debugger.

Two narrower things also happen on their own. When the screen shows a meeting invitation or a time being agreed, the notification step alone runs at once on that window, in one call. After the agent has been closed, the stretch it missed is read in turns to write states, and nothing else.

A user message should not directly invoke the Heartbeat process.

Direct user interactions use the normal agent execution workflow instead.

## A Reason Written Where Nobody Reads It

Every state the heartbeat wrote logged `no reason recorded for state ... created by agent`, and stored the sentence that says it is a fault in whatever created it.

The model had given a reason. The prompt asks for "how that evidence gets you to this state" and it answered - and the answer was written to the state's metadata, beside the evidence, rather than to the field the calendar, the API and the Debugger all read.

Read back afterwards, **"no reason recorded" and "there was no reason" are the same sentence**, which is the confusion section 52.7 exists to prevent. The warning was not wrong; it was firing on every state, including all the ones the model had explained, which is the way a real fault becomes background noise.

## The Question Is Not In The Library

Removed. Each step used to search the library for names taken from the activity, because its checklist item was a question about the user and matched nothing written in the library. Since the library index and search were deleted (commit fcf74e1), nothing is searched for: each step reads the window of activity itself, and can open any item in it whole by its id.

**A state is a card, not a sentence.** It said what it was and never what it rested on or how much of the window went to it - and two states covering one window are different claims if one took forty minutes and the other took two, while the calendar showed them the same way. Each now carries **`minutes`**, how much of the window it accounts for, and **`sources`**: every file, page and window it rests on, written out in full. The sources are kept apart from the evidence, which is the *argument* for believing the state; these are the things themselves, because a reader who disagrees needs the page rather than a description of the page, and "a browser tab" is not something anybody can go and look at.

**No horizon is asked for or computed.** A field saying whether a state was short or long was the same fact as its dates said twice, and a second place for the two to disagree, so the period says it and nothing else does. The dates say how long a state runs, and `span_hours` gives the same thing as a number.

**At least one state, unless nobody was there.** Activity in front of the step means something was going on, and returning nothing says the opposite: it puts an empty stretch on the calendar for a stretch that was not empty, and everything reading it later reads it as nothing having happened. The exception is a window that really is idle - the machine locked, background events and nobody at the keyboard - where no state and a sentence saying so is the honest answer.

**The instruction carries three worked examples**, invented, from a single window: two activities and a brief third, whose minutes add up to no more than the window. Each carries every field it is meant to demonstrate, because an example missing one teaches that the field is optional.

**And the title, the detail and the numbers each do one job.** The title is **one complete sentence** - not a phrase, not a label - because it is the line the user sees on their calendar with nothing around it, and *"Replication work"* names a subject while saying nothing about it. The detail says what the state *is* - what they are doing and what it is in aid of - and does not repeat how long it lasted or when, because that is what the fields are for and prose that restates them is a second place to be wrong.

**The period a state can be given is the window it was read from.** A review reads a few minutes, and the examples in the instruction were teaching otherwise: one spanned a week, and two claimed more minutes than the window has. An example is the shape the answer takes, so an example that does not fit the window teaches that the window is not the limit - and a state dated across three days on the strength of fifteen minutes is a guess wearing a date. Something longer-lived is either **already on the calendar**, in which case this window carries it on and its end moves - which is how a long state gets long, one window at a time - or it is a **habit rather than a situation**, and belongs to the step after.

### 34.3 The state heartbeat

**A fortnight of one project is a hundred states.** The review that writes them reads a few minutes at a time, so the same situation is written again every window it survives - each true, each worded slightly differently, and nothing anywhere saying *this is one thing*. A reader of the calendar sees a hundred lines; a retrieval over it matches whichever four happen to share the most words with the question. So a **second review runs on its own clock** - every twelve hours by default, against the first one's quarter of an hour - and asks the other question: which of the states already written are about the same thing, and what is that thing?

**States are compared by what they mean.** The grouping was word overlap, and on this calendar that measures the wrong thing: one model writes every state, in a stable vocabulary, about a handful of projects, so two states picked at random already share a sixth of their words and the words they share are the project's rather than the work's. *"Rebuilding the SWR replication package"* and *"rebuilding the replication package for the SWR paper"* are one thing said twice; overlap saw 0.74 of it and an embedding sees 0.93, against 0.06 for an evening of YouTube. **Local, English and small**: ninety megabytes, 384 dimensions, four milliseconds a state on the processor, and nothing leaves the machine - which matters more here than a larger model's accuracy would, because the material is a record of everything the user does. This is why states are written in English (section 34.1, as amended): the English-only model is a fifth the size of a multilingual one of the same quality. The vectors are kept against the text they were read from, so an edited state is read again and one merely seen again is not; and like everything else in this application that needs more than the standard library, with no model on disk it compares words, **says so on its page**, and carries on. **Only the title and the detail are read** - evidence and sources are paths and URLs, and a language model reading `C:/Research/.../fetch.py` is reading punctuation.

**And what it builds is a tree, not a list.** A flat grouping has to choose an altitude before it has seen anything, which is what the similarity threshold was doing quietly for every group at once. A fortnight on one paper is a handful of sittings on the scripts, a few on the figures and two on the letter: one thing at one altitude and three at another. So every state is joined to its nearest neighbour, those pairs to theirs, up to one root - **agglomerative clustering, average linkage**, average rather than single (which strings unrelated things into a chain through one near-duplicate) or complete (which refuses to join a large group to anything). Built with the **nearest-neighbour chain algorithm**: exact, and n-1 merges in the square of the state count rather than the cube. Cutting the tree at a height would give the flat grouping back; what is done instead is to read it as layers, described below.

**The tree is read as layers of tasks.** A cut is one height for the whole calendar, and the reason for having a tree at all is that a fortnight on one paper is one thing at one height and four at another. So the page is what a reader would ask for out loud - a few things I have been doing, and what was inside each - built from the tree by two rules.

**A task holds at least two hours of work or it is not a task**, counting how long each state says it lasted (capped at the window it was read from) rather than how many states there are. A piece smaller than that is not made into a task of one, and it is not hidden either: its states are listed on the task they sit in, as **states that fall outside every sub-task**. That is a finding about those states - they grouped with nothing - and saying it plainly is the only honest way to show it. The first version made every piece a task and then, when a layer came out with three hundred and fifty of them, gathered the smallest into one row labelled *"the smallest 347 pieces of this task"*, which told a reader nothing about what those 347 were.

**Collapse.** Every join is a pair, so a task whose five states came together one at a time is five nested pairs where it should be one layer of five. A join within **a fifth of the tallest join** of the top of its layer happened at the same distance as the layer itself, so its children are lifted into that layer rather than nested under it. Measured against the top of the layer and not against each join's own children: a tight clump is itself a chain of joins at one height, so a rule about a join and its children cannot tell the chain at the top of the tree from the chain inside a clump, and descends through both. A fraction rather than a distance because the tree sets the scale: on real states the joins climb in a smooth ramp - 0.07, 0.18, 0.25, 0.32, 0.36, 0.37, 0.52, 0.61, 0.74, 0.81 - where a fixed five hundredths collapses nothing, every layer is a pair, and eleven states come out as one task.

**Split.** A task is opened into sub-tasks only where its own join **stands out**: at least 1.15 standard deviations above the joins beneath it, counting a state on its own as a join at nothing. That is the inconsistency criterion, and 1.15 is the value scipy's own documentation uses for cutting a tree this way. It is measured against **every join in the subtree**, not against the heads of the parts: the heads are by definition the tallest things underneath, so a population of those has almost no spread and the score lands near the same value whatever the tree looks like - a number that decides nothing. Against the whole subtree it reads 5.9 for twenty states with a stranger among them and 1.3 for four states assembled at 0.10, 0.11, 0.12.

**And it stops for three different reasons, which it names.** Fewer than five states, so the states are shorter than a tree of them; nothing underneath but single states, so opening it would turn four states into four tasks of one; or a join that does not stand out, so what is under it is not separate from it. A layer showing more than eight sub-tasks keeps the eight largest and lets the rest join the loose states, because past that the layer is the list it was meant to replace.

**The picture is the tree.** A dendrogram: every state a leaf, every join a bracket at the height its two halves were apart, the leaves ordered by the tree rather than by when they were written - which is what stops the lines crossing. The cut is drawn **across** it rather than described beside it, colour is the group it leaves behind, grey is a group too small to summarise, and clicking a leaf opens the state. **Summaries are on.** A task gets one call when it is first found, and almost none after that: only a task whose membership has turned over by a third is written again.

**It had three parts before that, and only the last needed a model** - kept here because the reasons it was replaced are the reasons the shape above is what it is. *How alike two states are* was read off their words, twice: what they **said** (title and detail) and what they **rested on** (evidence and sources). Two measures rather than one, because two states can describe the same afternoon in different words or use the same words about different files, and a single score cannot tell those apart - so both were computed, reported and combined under weights. That is gone with the words: an embedding reads the first, and the second was a structural zero for half the calendar, since a third of it is task-accompanying states that carry no evidence at all. *Which of them formed a group* was label propagation over the graph those scores make: not clustering into a fixed number of parts, because how many things the user has been doing is exactly what is being asked and a method that must be told the answer in advance cannot report it; and not connected components, because one shared filename would then join two unrelated months - components answer "is there a path" and the question is "is there a centre". *What each group is* is one model call, and **only for groups of five states or more**: a handful of states is a handful of states, and summarising four spends a call to say roughly what the four of them already say. Smaller groups are still found and still shown, with the reason they were not written up.

**The summary is a record one level above the states it covers**, in the same folder they are mirrored to, naming them - so a reader who doubts it can open what it was made from - and carrying the term-to-state index that makes a summary something to search rather than a label.

**A task outlives the round that found it.** Everything above is how the tasks were *found*, and it used to run again from nothing every hour - which was right every hour and worthless over two. The tasks were numbered one to five in order of size, so task 3 on Monday and task 3 on Tuesday were unrelated things: nothing could be said about a task over time, not that it grew, not that it went quiet, not that the user had been at it for three weeks, because there was no *it*. And every summary was paid for again, in a model call, to describe a task that had gained one state. So a task is now a row that persists, and each state written since the last round is **appended** to the task it is nearest - one dot product per task - joining it if it is within **one standard deviation** of how far that task's own states already sit from its centre. Not a fixed distance: the same one is generous for a task whose states sit at 0.1 and mean for a task whose states sit at 0.4. A state no task will take is left **loose**, and offered again next round.

**The grouping is found again only when appending has stopped being honest**, which is five questions asked every round and answered on the page whether or not they fire - because the useful question on a quiet round is not *did it rebuild* but *how close was it*. Have the loose states reached **five**, the size at which they could hold a task at all - and does a group among them hold together as tightly as the tasks already accepted? Five states that each match nothing are five strangers, so the pile is clustered by itself and must pass the same test. Has a task's centre **drifted** more than **0.05** from where the last rebuild left it, which is the twist the whole mechanism guards against: a task fed one state at a time until it is about something else. Has it **widened** past 1.6 times its old spread, absorbing rather than matching. Do two tasks keep wanting the same states, so the boundary between them is stale. And the **backstop**: has the calendar grown by **a fifth** since the last rebuild, whatever the others say - drift is measured against a centre that drifting moves, so it can creep without ever tripping.

**And a rebuild is not a renumbering.** The new grouping is lined up against the old one **by who is in it**: a group sharing half its membership with a task that was already there *is* that task, and keeps its id, its age and its summary. What split, merged, arrived or ended is recorded as that, rather than reaching the reader as a change of numbers they would have to reverse-engineer from the sizes. A task that ends is kept and marked retired, not deleted - a task that stopped being a task is a fact about the user's months - and whatever replaced it points back at it. A summary is written again only where the membership under it moved by **a third**: a task that gained four states is the same task and says so, and rewriting it would pay a model call to produce the sentence it already has, worded differently, so that a reader watching the panel would see the description of unchanged work change every hour.

**Every one of those numbers was measured, not chosen.** Two hundred and forty states written the way the review writes them, embedded by the model that actually reads them - so the geometry is real even where the sentences are invented - replayed in arrival order, with three steady subjects and one project deliberately walking from the code to the writing-up in fifteen steps, because that last one is the case the mechanism exists for and it cannot be tested with two separate subjects: those make a new task, not a drifting one. At each step the appended grouping was compared with what a full rebuild would have said, as an **adjusted** Rand index - the plain one sits near 1 whatever happens when there are five groups, since most pairs are in different groups either way. Each limit was swept with the others switched off, because swept together they hide each other: the first attempt had the loose trigger firing every batch, so every other parameter was measured against a grouping that had just been rebuilt anyway and every curve came out flat.

**At the rate the heartbeat actually runs** - a hundred and twenty states already grouped, then three an hour, sixty rounds - appending alone agrees 0.90 with rebuilding, rebuilding every round is 1.00 by definition, and these limits give **0.986 with four rebuilds in sixty**. Ninety-three per cent of rounds are a few dot products, and seven eighths of the distance is recovered. One honest note on that table: in this regime the backstop was the only signal that fired, and turning the drift and loose checks off changed nothing. They are there for the shapes an hourly replay does not contain - a project that walks somewhere else over a month, a subject arriving all at once - and they earned their limits in the batch sweeps, where those shapes were forced. They are not carrying that result.

> The obvious way to count the backstop - adding up each round's arrivals - is wrong, and was wrong here first. A state that no task will take is offered again every round, so a handful of states that fit nowhere were counted over and over and would have forced rebuilds nothing had asked for. It is the calendar's own growth that is counted, which cannot double-count.

**And the round only pays for what it is doing.** Scoring every pair is the square of the calendar; appending a state is one dot product per task. Those were being paid together - every round joined every state to every other and *then* decided it had nothing to rebuild - which is the whole saving thrown away at the last moment. Now the pairs are scored, the tree is built and the layers are read **only on a round that rebuilds**. An appending round scores the new states against the task centres and nothing else: at a thousand states that is a few dozen comparisons against half a million. The dendrogram is kept from the last rebuild and shown labelled *as of* then, because a picture that vanished on every round but the rare one would be worse than one that is honest about its age; the colours on it are the tasks as they stand now, so a state that has joined a task since is coloured for it without having moved.

**The grouping covers a window, and says which.** With no tasks yet it reads **the last month, and at most a thousand states** - a user who has run this for a year has thousands, and a first grouping over all of them is a quarter of a million pairs spent describing months they are not asking about. Afterwards each round reads from that line forward, and when the count reaches the cap the line moves up and the tasks let go of what falls behind it. Four hundred was the old bound and it was the square that had to be paid *every hour*; a thousand is the square paid when the grouping is found again, which is four times in sixty rounds. And a thousand now costs less than four hundred used to: **eighteen seconds and a hundred and twenty megabytes**, against three seconds at four hundred, where the old code took two minutes and three hundred and sixty megabytes for the same thousand. Two things were paying for nothing. The pair scoring left the tensor library to walk half a million pairs in a Python loop - the docstring above it said a matrix multiply was milliseconds and a loop would be half a minute, and then wrote the loop - and each pair was described into a nine-field record, with a set intersection computed for it, so that sixty of them could be printed.

**Two limits, and only one of them excludes.** The day limit shapes the FIRST grouping and defers rather than excludes: a state older than a month is not read now, and is read on the next round if it is among the newest thousand by then. What holds a state out permanently is the COUNT - being outside the newest thousand - and while it is outside, the task that held it has let it go. The distinction matters because the page was stating the other thing: that anything older than the first round's window was "treated as seen, not a backlog, and never read again", which was true of an earlier design and false of this one. A page that is wrong about which of the user's states were looked at is worse than a page that says nothing.

> It was an earlier design, and the reason it went is worth keeping. The first version drew a line at the first grouping and read forward from it, which froze the window at whatever the count had been when the line was written: raising the setting from four hundred to a thousand changed nothing, because there were only four hundred states after the line. The window is now the most recent thousand, always, and the line is a record of how far back a round reached rather than a rule about what the next one may read.

**What is older than the line is treated as seen.** This is the part that is cheap to get wrong and expensive to have wrong. Those states are not a backlog waiting to be noticed: if they were left as unread, every later round would find four thousand states in no task, conclude that what fits nowhere has become a task of its own, rebuild on them - and then find that they are months of unrelated work, and do it again the next hour, forever. So the line is written down with each round and what is before it is before the beginning of this grouping. The same mechanism does both jobs: a state that falls outside the window is forgotten by the task holding it, which is the only honest thing to do with an id nobody can open any more.

**The states are written in English; the summaries are written in the language the application is set to.** Those are two different decisions and they were one for a while. English for the states is a decision about what the MACHINE reads: one small model has to read all of them, and an English-only model is a fifth the size of a multilingual one of the same quality. A summary is what the USER reads, and a model handed English states will answer in English however the application is configured - so the line that every user-facing prompt carries is carried here too, and a reader is not left with half their agent in a language they did not choose.

> The prompt that writes those summaries still told the model the states had been "grouped because their words overlap", a method that had already been replaced by the embeddings - so it was asking for a judgement about evidence that no longer existed. It now says they were grouped by meaning, and warns about the failure that measure actually has: that five sittings which are all *reading something* share a subject and not a purpose.

> And the first round used to run inside `start()`. That was harmless while a round was a handful of states, and became the application feeling stuck the moment it was not: the first round is the expensive one by a distance - the whole window, every pair, and the embedding model loaded from disk - and it was being paid while the window was still opening. It was worse on the way out, where a quit during that round could not interrupt it, so every heartbeat's join spent its full five seconds and closing took fifteen. Measured at three hundred states, with nothing else changed: **five and a third seconds to quit with the round inside start, against under one second with it deferred**. The first round now waits a minute after start-up, and also until a hundred states have been written that the grouping has never read. Fewer than that would be a dozen tasks of one, which is a list, and the panel says it is waiting rather than looking broken. A round that is asked to stop while a rebuild is due keeps what it has and lets the next round do it, because the signal that asked will ask again.

**A task is not a state, and does not live with them.** The summary was written into the states folder, one level above what it covered and distinguished by nothing else - so a reader holding a file could not tell which of the two they had, and a search could not ask for either. They are different claims: a state is about a stretch of time and is true or not; a task is the claim that a number of states are one thing, and it can be wrong in a way no individual state is, because the states can each be right while the grouping that joined them is not. Tasks now have **their own folder**, beside the states rather than among them, still naming what they cover so the claim can be checked against it.

**And the debugger runs the same code somewhere else.** Real states can only show what the grouping did; made-up ones show what it does to a shape you chose, which is the only way to tell those two apart. So there is a **sandbox**: a second library, laid out exactly like the real one - made-up states in `states/`, the tasks found from them in `state_tasks/` - and a round is handed one place or the other and does the same thing either way. Not a second implementation and not a branch inside the algorithm: a debugger that runs different code from the thing it is debugging is only testing itself, and there is a test asserting the grouping's own body never mentions the sandbox at all. The task rows are separated by a column rather than a second pair of tables, for the same reason: two schemas to keep in step is how a debugger stops testing what it claims to test.

**The sandbox is internal, and says so on the page.** It lives beside the database rather than in the workspace, because the workspace library is what the user reads - a made-up state answering a real question would be a fabrication however clearly the folder it came from was named. A sandbox round does not become the last report the status bar counts from, and its result carries a banner saying what it is, because the page looks exactly like a real round otherwise, which is the point of it and the risk in it. It summarises, as the real round does: a summary is the thing somebody opening the sandbox came to see, and the round is a press of a button rather than a timer, so the calls are asked for.

> Summaries written before this change are still in the states folder. They are not moved: rewriting several hundred of the user's files to tidy a layout is not a migration anyone asked for, and the records carry what they are, so they can be found where they are.

**The sandbox has one press that shows the whole mechanism**, because the parts only mean something together. It puts in **more states than the window holds**, so the cap is doing something rather than being a number in the settings; runs a round; adds a few and runs again; adds a few more and runs a third time. Three rounds and not two, because two do not show it: the second round over a fresh grouping nearly always rebuilds, and that is worth seeing rather than arranging away. Measured on a window of a thousand, with twelve hundred states written:

| | round one | round two | round three |

|---|---|---|---|

| states read | 1,000 of 1,201 | 1,000 | 1,000 |

| pairs scored | 499,180 | 499,180 | **96** |

| rebuilt | yes, the first grouping | yes, the loose pile | **no** |

| model calls | 8 | 1 | **0** |

| took | 12.1s | 2.3s | **0.8s** |

Round two rebuilt because a first grouping leaves states in no task - thirty-eight of them here - and those are offered again every round, so forty new arrivals became seventy-eight and forty-six of them matched nothing, over the eight at which a loose pile counts as a task nobody has noticed. It cost nothing in identity: all eight tasks kept their ids and seven of the eight summaries were kept, because their membership had not moved by a third. Round three is the ordinary hour: ninety-six comparisons against half a million, and no model at all.

**And the cap is the one place this loses something, so it says so.** With no tasks yet the grouping reads the most recent thousand; if more than a thousand arrive before the next round, it is the most recent thousand again and **the rest are never read - not then and not later**. The round says that in plain words when it happens rather than leaving it to be inferred from a count, and the setting says the arithmetic: at a few states an hour a thousand is about ten days of catching up, so a fortnight away is fine and a month away is not. It is a floor to raise rather than lower.

**Each summary is drawn over the whole calendar, not over what it read.** A list of tasks cannot show either of the two things that are missing from it - the states outside the window, which the grouping will never see, and the states inside it that matched no task, which it had nothing to say about. So above each summary is one picture: **when** each state happened along the bottom, **how many** happened in that stretch up the side, and each column drawn in three parts - held in a task, read and in none, not read at all. Underneath, one bar per task showing the stretch it drew on.

The numbers on it are **the folder's own**. It is drawn from every state in the source with no limit and no window, so the totals can be checked by counting the files, and two rounds an append apart show the same columns plus the new ones: ninety states, then a hundred and fifteen after twenty-five arrive. If the picture were drawn from the round's own reading instead, it would agree with itself and tell the reader nothing.

**The columns are the resolution, not the rule.** Forty across whatever the calendar spans, so a month reads as most of a day each. What is *read* is decided by two thresholds - a month back, and at most a thousand states - and both are drawn on the same axis as dashed edges, with a line saying which of them actually cut. Which one it is has to be read from what EXISTS rather than from what was read: asking whether the oldest state read is inside the limits answers nothing, because it always is, by construction. Twelve hundred states in a month means the count cut; forty states over ninety days means the month cut; and after the first round there is no month limit to name at all, because the window starts where that round drew its line.

**A round processes what it has not processed, and nothing else.** This was the whole intent and the first version failed it in a way that hid behind its own output. A state that fits nowhere looks exactly like a state nobody has looked at - neither is in a task - so every round offered the entire pile again. That re-processed what had already been processed, and, worse, held the count of *states that fit nowhere* permanently above the limit at which that means a task nobody has noticed: a first grouping that left thirty-eight states loose therefore forced a rebuild of all thousand on the next round, and on every round after it. The appending mechanism was paying the full price of a rebuild every hour to discover the same thirty-eight states.

So **being in no task is now written down**. Offered, taken by nothing, and not to be offered again - the pile is a record rather than something rediscovered. What arrives is only what has genuinely never been looked at.

**And the pile is clustered by itself.** Asking whether the leftovers have become a thing is a question about the leftovers, and answering it by rebuilding the calendar is a hundred times the work for a hundredth of the states - and throws away the appending just done, renumbering, rematching and resummarising every task to answer something none of them were asked. The pile is now grouped among its own members: what comes out big enough is a new task, with no history and no pretence of being anything that came before, and the rest go back to waiting. Measured, three hundred states and then forty at a time:

**What makes the pile a task is not how many there are.** Five states that each matched nothing are five strangers as easily as they are a new subject, so the count decides only when to *look*; whether there is a task in there is decided by whether a group is about as tight as the tasks this calendar has already accepted - a standard it set itself rather than a number chosen here. The looking is now nearly free, so it happens as soon as the pile could hold a task at all: the same five that makes a task anywhere else. Eight was the old threshold, from when finding out meant rebuilding a thousand states and the question therefore cost a hundred times what it was worth.

> That test was got wrong twice, in opposite directions, and both were found by a test rather than by reading the code. Reading the pile as a hierarchy lost the answer: when the pile did not divide, the whole of it came back as one candidate, failed for being about several things, and took the one real subject inside it down with it. Judging a candidate by its MIDDLE then let a tight core swallow strangers - four identical states sit at nothing from each other, which pulls the average down far enough to take in two states about gardening and volcanoes. The tree is now cut where it is tight, and tightness is the middle PLUS the spread, which is the same statistic a task uses to decide whether to take a state. The four identical ones give that group a small middle and an enormous spread, and it is the spread that says it is not one thing.

| | arrivals | pairs scored | rebuilt |

|---|---|---|---|

| round 2 | 40 | **360** | no - one new task out of the pile |

| round 3 | 40 | 72,010 | yes - the backstop, which is the one signal that does need everything |

| round 4 | 40 | **360** | no - one new task out of the pile |

Three hundred and sixty is forty arrivals against nine task centres. The same reasoning applies to the sub-tasks drawn inside each task: a task that took nothing has the same ones, and finding them again was sixty thousand comparisons an hour to redraw what had not moved, so they are kept until the task changes.

> A round that only appends can still make one model call: when the pile becomes a task, that task is new and has nothing written about it yet. The claim that appending costs no model call at all was wrong, and was written into both the rehearsal's own description of itself and the test that checked it.

**And the page follows a round rather than being refreshed.** There was a Refresh button, which is the wrong shape for the problem twice over: it asks the reader to keep pressing something to find out whether anything is happening, and it can only be pressed by someone who is already looking. A round now writes **named stages** into the report it is filling - reading the states, reading what they mean, scoring every pair, joining them into a tree, summarising task three of eight - and the page reads that same object every four hundred milliseconds **for as long as a round is in flight, and then stops**. Not a standing timer: a page that reloads itself every fifteen seconds is working when nobody is watching and still out of date when somebody is. Opening the tab takes one look and attaches to whatever is running, including a round the hour started, which the button never did.

Named stages rather than a bar that fills, because *scoring every pair* and *summarising task three of eight* are different kinds of waiting and a fraction says neither. Each stage keeps what it cost, and the first thing that bought was a surprise worth having: on a forty-state sandbox round, **thirty-four of the thirty-four seconds were the embedding model loading from disk**, and every other stage was hundredths. Nothing about the grouping was slow at all.

> Every button on that panel now says in brackets whose data it touches - one is `(real data)` and four are `(sandbox)` - because the two are the same code over different folders and their results look identical. That is the point of the arrangement and the hazard in it, so the labels carry the difference and a test counts them.

> The first version of that line asked the wrong question and reported "nothing was cut" for a folder where twenty-six of forty states had been left outside the month.

**The page no longer draws a force-directed graph.** An earlier page drew one node per state, four edges per node and the edges that did not join dashed, laid out with Yifan Hu's force model. It was superseded by the tree ("The picture is the tree", above): the page now shows the dendrogram, beside the timeline of tasks.

**The instruction is on the page beside the message.** A panel that shows what the model was *sent* and not what it was *told to do with it* shows half of the call, and the half that varies: the message is the states, and an answer that reads oddly is usually explained by the instruction. It is carried once rather than repeated under every group - it is the same text each time, which is also what it costs, since it is sent again with every call, and each group's line says both numbers.

**The table of pairs says what it is a table of.** It read *"every pair that shared anything, highest first (60 shown)"*, which names neither what was counted nor what was left out. It now says how many pairs were possible, how many had no word in common at all and were never scored, how many were, which of those are in the table, and that a grey row is a pair that scored below the threshold and did not join - which is the row to read when two groups should have been one.

**The words shown to a reader are the files, not the pieces they were broken into.** The grouping runs on words split at their punctuation, which is right for matching and wrong to read: a group whose shared evidence prints as `01749, 023, com, https` has named nothing anybody can open. Where every member of a group names the same file or page, that is what is shown - in the panel and in the message the model is sent.

**A node opens the state it stands for.** A tooltip answers *which state is this* and none of the questions that follow - what it rests on, when it ran, which group it landed in, what it nearly joined instead - and the picture is where somebody is standing when they want those. So clicking a node opens a card carrying the state in full, its group, and its strongest few edges by weight.

**The arithmetic is written out as steps, and then done on two real states.** It was one sentence of set notation - *`|shared| / |smaller| x sqrt(|smaller| / |bigger|)`* - which says what the arithmetic is to somebody who already knows it and nothing at all to anybody else. It is now four numbered steps: where the two sets of words come from and how they are split, how each half is scored, how the halves are weighted together, and what makes a pair an edge. **Beside them, the same arithmetic done once on this run's own strongest pair**: the two titles, the words each one has, the words they share, each half as `7 / 9 × √(9 / 10) = 0.74`, and the two combined against the threshold. A formula is a claim about what the numbers would be; somebody deciding whether 0.25 is the right threshold needs the numbers. That also makes the page checkable - the sizes are reported on every pair, so the worked example can be recomputed from them rather than believed.

**And the rest of the method is written beside it**: how an edge is weighed and how the groups are found, in words, built from the settings the run actually used. This is where somebody is standing when they ask why two states are joined, and a page that describes a formula it does not compute is a page that will describe the old one after the next change.

**A task is described in four parts, and they are updated one at a time.** A title and a paragraph are what the grouping produces; they are not what somebody asks when they come back to something after a week, which is *where was I doing this*, *what was it for*, *what is it*, and *how far did I get*. One paragraph answers all four at once and none of them well. So a task carries **`where`** (the folder, file, repository or site a person would open to carry on), **`goal`** (what will be true when it is over), **`doing`** (the substance - the one field where naming several parts is right) and **`status`** (where it stands and what is left). Each may be empty, and an empty one is a statement that the states did not say, which is better than an invented one.

> **They are amended, not rewritten.** A task that has been described before is shown its own description, under a heading, *after* the states rather than before them - a description read first is a conclusion, and everything after it gets read as evidence for it - and the instruction is to return only the parts that are now wrong. Anything left out is kept exactly as it was. A round where four states arrived usually moves `status` and nothing else, and rewriting the other three would be three claims restated by something with no new reason to restate them, in different words, so that a reader watching the page sees the description of unchanged work change every hour. The page marks which parts this round wrote, because otherwise four paragraphs give no way to tell this hour's claim from one that has stood for a fortnight. Stored as one JSON column and not four: which parts a task is described in is a property of the instruction that writes them and will move again, and a schema change per revision of a prompt is a schema nobody will revise.

**And whether it searched is said on the task.** It was recorded, and it was inside the cost block, which is a collapsed section inside a collapsed section - so the answer to *did the model look anything up* was two clicks down on a page whose whole purpose is to answer that. The statement is now on the task itself: searched, with how many times, or not. Where it searched, each search shows what was asked, the reply that asked for it, what came back, the follow-up prompt, and **whether it went on to search again** - *searched twice* and *searched, was not satisfied, and searched again* are the same number and different findings. Where it did not, the page says so and shows the answer it gave instead, because *it did not search* and *it was never offered a search* read the same otherwise.

**The grouping reads what was observed, not what the agent scheduled.** A state written to accompany a scheduled task - “Scheduled: check the FieldConf 2027 deadline” - is a real record and belongs on the calendar, and it is not something the user did: nobody did it, there is no evidence under it, and its words are the words of a task the agent wrote for itself. Twenty of them say one thing in twenty wordings, because the agent rescheduled one reminder twenty times, so they group tightly and perfectly - and the result was the agent's own backlog handed back to the user as a list of what they had been working on. The round now reads the `user` and `agent` sources only. **Filtered in the query rather than after it**, because these reads have a limit on them: taking the newest thousand and then dropping a third leaves seven hundred, and the caller asked for a thousand. The counts under the overview and the timeline it is drawn from use the same filter, so the picture matches what was actually grouped.

**Where a task stands is asked every round its states change, and the other three parts are not.** A summary is rewritten only when the task has changed size by a third, which is right for three of the four parts and wrong for the fourth. Where the work lives, what it is in aid of and what it consists of do not change because four states arrived; `status` is the part whose whole job is to move, and under the same rule it sat still for hours at a time while the work it described went on happening. So a task whose membership moved at all - **gained or lost** - gets one small call that answers `status` alone. A departure counts because it is the grouping deciding a state belongs somewhere else: the task is about less than it was, and a status describing the larger thing is wrong.

> **The call is small on purpose**: the description the task already has, and only the states that moved - eight each way at most - rather than the forty it holds. It is told it may answer that **nothing changed**, and that matters as much as the rest: another sitting on the same obstacle is not movement, and a status rewritten in new words every hour is precisely what the turnover rule was protecting the reader from. It is also told not to return the other three, and to put any suspicion that one of them has gone wrong into `why` for the full summary to judge - that call has every state in front of it, and this one has four.

> The turnover rule itself measures the **count**, not the membership: `|size now - size when written| / size when written`. A task of twenty states with ten of them swapped for ten different ones scores zero and is never rewritten - and the rebuild explicitly permits that, since a cluster keeps a task's identity down to a half of its membership. The docstring said it answered whether the summary still described what the task held; it answers whether the task changed size. The status call covers the part of that gap that moves most, and does not close it.

**The sandbox copies a whole state, not the two fields it reads.** A copy took a state's title and its detail and left the rest behind, and `_MadeUp` handed back an empty evidence list and an empty source list to every caller. So in the debugger every state rested on no file, recorded no evidence, had no reasoning behind it and no clock - which made the sandbox a test of a thinner state than the real round reads. What a state rests on, the evidence it recorded and the reasoning the review wrote it with are three of the things a summary is made from, and a search asking for a state *in full* was handing back four fields and three apologies. The copy now carries all of them, with **a local clock computed for the state's new moment** - the sandbox moves states onto a calendar of its own, so keeping the original's wall-clock time would put a Tuesday evening on a Friday morning.

> Both halves of that went through a record file, and a record's front matter holds scalars and lists. A nested map came back as its own `repr` and raised the moment anything treated it as a map; a LIST came back as a repr too, and iterating a string gives one character at a time - forty `rests on` lines spelling out a path, one letter each. Both are written as JSON now and read back tolerantly, so a record written by the older build does not break either.

**A search's answer says what was asked for.** The follow-up prompt said *“you asked for this, and here it is”* and then the states, which leaves the model to remember its own question across a turn and to work out whether what arrived answers it. It now names the words or the ids it asked for, says it looked in this group's states and nowhere else, and says what came back - and when nothing did, it says **the search ran and came back empty**, because a heading with nothing under it cannot be told from a search that never happened. The closing line tells it that an empty search is itself a finding it may write down, rather than a reason to guess.

**A summary that was not rewritten still has its words.** The kept branch built a summary out of a record id, a count and a reason - everything except the title and the sentence, which lived only in the library record. So a task came out of the next round with its name blank although nothing about it had changed, on every round its summary was reused, which is most rounds. The words are now kept beside the four parts, where the page can read them.

**And a task is named on the comparison by what it is, not by where it sat.** `task_3` is a POSITION, and positions are filled by different work in different rounds, so a table keyed on them showed two rows both called `task_3` - which is the confusion the stable ids exist to end, reappearing in the one place built to compare tasks across rounds. Rows are named by the title the task was first given, with its stable id under it. The number in each column is **what the task held at the end of that round**, with what it gained or lost in brackets, because a bare number answers neither question on its own; and a round in which it did not exist says *not yet* or *gone* rather than nought.

**The states that moved are shown as the model read them.** The comparison listed six titles and then *“and 8 more”*, and the states left out of a truncated list are in no particular order - so the ones hidden are as likely as not to be the ones worth reading. Every state that arrived or left is now shown whole, in the same fields and the same order the model was given them: what it said, why the review wrote it, when it ran on the user's own clock, what it rests on and what it recorded, with each empty field SAID to be empty. What the model was given about a state is the only thing that explains what it made of it.

**The tree can be zoomed and moved.** A hundred states is a hundred rows, and at that size the names are a few pixels tall and the part worth looking at - which joins fall left of the cut - is a smudge. The picture is an SVG with a `viewBox`, so zooming is arithmetic on four numbers and needs no library. **Plain scrolling is left alone**: a figure that swallows the wheel inside a page this long is a trap, so the wheel zooms only with Ctrl or Command held, and there are buttons for the readers who will never discover that. It is bounded both ways - never wider than the drawing, never tighter than a fiftieth of it - and the box is remembered across a redraw, for the same reason the open blocks are.

**The debugger does not wait on the user's own round, and says which it is waiting for.** There was one lock for both scopes, so an hourly grouping over the real states blocked every press in the sandbox. That alone would be a wait; what made it a puzzle is that the page then gave the wrong reason. Emptying the sandbox asks whatever is running to stop before it deletes anything - right, when a person presses the button - and the round takes that request back only once it holds the lock, which it never got. So the flag was still set when the rehearsal looked, and the rehearsal reported that the sandbox had been emptied underneath it: two true statements, neither of them the reason. There is now a lock per scope, which is right on its own terms - a rehearsal reads made-up files in its own folder and writes its own rows, and has nothing to collide with in the real round but the lock. Two SANDBOX rounds still exclude each other, because those do share a folder.

> And a round that **never began** is no longer reported as one that **gave up**. The two have different answers - one is wait and try again, the other is that the data went away - and a page that gives the second when the first is true sends the reader looking for a fault that is not there. A rehearsal also stops treating its own first step as an interruption of itself: it empties the sandbox, so it takes the stop-request back before it starts.

> **A task is described by two objects, and that has now caused six bugs.** The drawing works from PLAN NODES - an id, a span, a size - and the round's answers live on LAYERS, which carry the summary, the weights and the pieces. The ids are different in kind: a layer's is positional, so `task_1` means "the first row this round", while the stored id belongs to a task for life. Every reader that took a field from the wrong one of the two produced a different-looking bug: the colour that disagreed with the panel, the card that went blank, the open task that collapsed on the next click, the sort that did nothing because every weight read as the default, the pulse that never appeared because a list of stored ids was searched for a positional one, and the emblems that swapped over when the order changed - a badge seeded from "the first row" follows the ROW. Each is now resolved by id at the point of use, and the lasting id travels with the node for the things that must not move. The lesson is the one the rest of this document keeps arriving at from other directions: two records of one fact will disagree, and the only question is which reader finds out first.

**A round that is resumed keeps what it already named.** The marking step tells every task a round will describe to say *loading*, and the loop asks about each in turn - so a round interrupted after naming nine of eleven came back, told all eleven to say *loading* again, and paid for the nine a second time. The round now records each name as it is written, before anything can interrupt, and that list is cleared only when a NEW line is drawn: a name written and not recorded is a name that gets written again.

**And a task waiting its turn still shows the name it had.** The words are kept beside the marker precisely so they are not lost, and the picture was not using them - so a task that had a name went blank the moment a round decided to describe it again, which reads as the name having been thrown away rather than as it being rewritten. The marker is the literal word *loading*, which is why the first attempt at this drew the word and never reached the name beside it. The row stays flagged as waiting, so the pulse and the panel can say the name on it is last round's.

> **An empty picture is the one thing that panel must not show.** A guard against the saved tasks being served mid-round - added because the stored copy says *loading* - left nothing at all on screen between a round starting and its shape being built, at the exact moment the reader had just been told a round was running. The guard is gone and the cause is fixed where it belongs, in which name gets drawn.

**What is not shown is not drawn.** The model decides whether a group is a task with an end or a way the user works, and only the first belongs in a picture of what somebody is in the middle of. The judgement comes OUT of the summary, so a task that has not been asked about yet is drawn and says it is loading; after the answer, a way of working disappears. It is kept with the task, because without that a restart draws every one of them again - including the ones already judged not worth drawing.

> **Animation that has to be waited out is not helping.** The limb of a branch drew itself, end to end, and the rows were staggered forty milliseconds apart; each was meant to say "this came out of the thing you clicked" and together they made expanding look like the picture being painted again from the left, which with ten rows is most of half a second. They fade in together now. The rows sliding down to make room already says where the new ones came from. Nor is a redraw an update: replacing the drawing when a name lands restarts every animation, loses the scroll position and rebuilds anything unfolded - so a name is written into the elements that are already there, each open task's branches are kept in their own group and left alone unless what they are drawn from changes, and a zoom resizes rather than redraws, because a zoom changes nothing about where anything is.

**The picture is the first card on the overview, and it is drawn from the saved tasks when no round is in memory.** The tasks are rows in a table - that is what keeping them is for - and the report that found them dies with the process, so after a restart the panel was saying *no grouping to draw yet* over a database holding nine tasks and five hundred states. What a round works out and does NOT save is each task's own layers, so those are saved with it now: a circle that opens onto *nothing inside this one divides* is a finding, and a circle that opens onto nothing because the working was thrown away is a bug wearing a finding's words.

**Every layer of one task is named before the next task begins.** Depth-first, because the alternative - all the second layers, then all the third - finishes no task until it has finished every task, and a reader wants one piece of work readable all the way down rather than every piece half readable. Only the immediate children were named before this, so with three layers configured the third was never named at all.

**What the user will see is summarised first, as far as that can be known - and it cannot be known in advance.** Whether a task is shown at all is `concrete`, and `concrete` comes OUT of its summary: the model is what decides that *the user keeps checking the same three sites* is a way of working rather than a thing with an end. So the order is every top task, then the pieces of the ones that ARE shown, and only then the hunt inside the ones that are not. That hunt is a search rather than a description and nothing on the page waits for it; it used to run second, so calls were spent on tasks the reader would never see while the ones in front of them still said *loading*.

> **An update must not cost the reader their place.** Three separate ways it did. The panel redraws every time a name lands, which is several times a minute while a round runs: it put the frame back to the beginning of a drawing the reader had dragged halfway across, until the scroll position was read BEFORE the HTML was replaced rather than after, when the old frame is already gone. It collapsed whatever they had open, because the branches are built into the drawing by hand rather than being part of its HTML - and the one place that rebuilt them looked only inside the debugger's copy, never the overview's. And clicking a task threw the picture to its right edge: every circle carries `tabindex` so it can be reached from the keyboard, a click focuses a focusable element, and the browser then scrolls it into view - and the discs sit at the MOST RECENT end of their lines, which on a long calendar is far off to the right. The focus is still given, with the scrolling refused.

> **A measurement left behind outlives the thing it measured.** The bottom layer was once the states themselves, drawn with their words to the LEFT of their circle, so the drawing asked for a left margin and the frame was scrolled right to keep the clicked row under the cursor. That layer is gone; the measurement stayed, counting a NOTE as leftward although a note is drawn to the right like everything else - so a task with no pieces, which draws exactly one note, asked for hundreds of pixels of margin it never used and the compensation threw the view to the right edge. Nothing is drawn left of the axis now and the box always starts at zero.

**The axis covers the whole calendar and the drawing is as wide as it needs to be.** It showed a fortnight with a dropdown and arrows to move it, because three months squeezed into the width of a panel puts a week inside four pixels - but the squeezing was the problem, not the three months. A window also hid everything older than itself behind a control, so a task that ran six weeks ago could not be seen without first working out that the control existed. Dragging and zooming were already there; the picture is simply allowed to be bigger than its frame.

**A row says what it means.** Its two weights were printed as *3/3*, which reads as three out of three and says neither which number is which nor what either is out of; they are named and scaled now, the way the card beside the picture already said them. The width is measured from THAT line as well as from the name - it is usually the longer of the two, was drawn at the same place, and ran off the right-hand edge where the furthest task's words could not be reached at all. Both are measured through the same function that draws them, because measuring `title` while drawing *loading...* is two lengths for one string.

**A task waiting its turn says `loading`; the one being summarised says `working`.** All of them saying the same thing is true and useless: a reader watching a round that takes minutes wants to know which task is costing the time. The round records which task it is on where it already records what it is doing, and it records the LAYER's id rather than the clustering's - `group_1` and `task_1` are two names for the same work, and comparing the wrong one meant nothing ever matched.

**A round is not a tick, and it is not on the hourly timer.** It is one model call per task, so on a busy folder it is minutes of work - and the other heartbeats are a few calls on a fixed schedule. An hourly timer either runs this when there is nothing new to find or collides with the round that writes the states it would read. So it is asked for **when an agent heartbeat finishes**: an unfinished round is resumed first; otherwise, if the last round that finished was longer ago than the configured interval (twelve hours by default), the next one starts. **There is ONE number for this, in hours** - the grouping's own loop sleeps on the same value the gate measures against, and both go through that gate. Two numbers for one interval is a bug waiting to be written: whichever the loop slept on, the other would be what the panel showed. The rename from seconds carries what was chosen rather than dropping it, because resetting somebody's deliberate ten minutes to six hours under cover of a tidy-up is a change to how their agent behaves that they would have no way to notice.

**The waiting is counted from the end, not the start, and an interrupted round outranks the clock.** Half a round leaves tasks reading *loading*, and the way out of that is to finish it rather than to wait six hours and start again. So an unfinished round is resumed before the interval is consulted at all.

**A round draws a line and reads up to it.** States keep arriving while it runs, and a round reading a moving window would summarise a task, have three more states land in it, and finish holding a description that was already out of date - with nothing to tell that apart from one that was never written. **A resumed round keeps the line the first attempt drew**, because moving it would pull in states that arrived during the part already done, leaving the tasks summarised early as the only ones that had not seen them. A state with no recorded time is kept rather than dropped: the line is there to stop a round reading its own tail, and a missing field is not evidence of that.

**A task waiting its turn says so.** The panel is read while a round runs, and the alternative is showing the words from six hours ago as though they were this round's - a reader cannot tell a name just written from one that is stale, and the whole point of a round is that something changed. What it said before is kept and offered AS that, last round's words, rather than silently reused. Only tasks that will actually be asked about are marked, by the same rule the summarising loop decides with: marking them from a guess at that rule told a task whose summary was about to be REUSED to say *loading*, and then never asked about it, so it would have said *loading* until something in it changed - which on a quiet task is forever.

**The top layer is finished before anything below it starts, and then the insides go one task at a time, most urgent first.** Both halves of that are for the reader watching it happen: finishing the top layer first means the picture has every task in it, named, within the first few calls - a tree that grows a branch at a time tells you nothing until it is done - and urgency order after that means an interrupted round leaves the pieces of the work that needs doing soonest rather than the pieces of whichever task happened to be first in the list. Urgency is known by then because the top layer wrote it.

> **A blank where a drawing goes is read as a broken drawing.** The picture returned an empty string whenever there was no plan to draw, and there is no way to tell *this round made no plan* from *the picture is broken* by looking at nothing. It now says which: the round is still going and the plan is the last thing it works out; or every summary was reused so no plan was asked for; or there is one task and a dependency picture of one task is a circle with nothing to depend on. **And the picture is the one block on the page allowed to open itself** - the rule is that nothing does, because every block had a good local reason and the sum was a page that unrolled everything it had, but a drawing behind a closed summary reads exactly like no drawing at all. That exception is written down as a test asserting AT MOST ONE, so the next block with a good local reason still fails.

**Everything running now, as one picture.** A disc per task on a time axis, at the most recent end of its own span with the history trailing back - because a reader scanning this is looking at what is happening NOW, and where a task started is the least interesting fact about one that is still going. Its whole name sits past the disc, wrapped onto a second line rather than cut: two tasks called *Answer reviewer 2 on the weighting paper's method section* and *...the weighting paper's figures* are the same string for their first forty-three characters, and the naming rules spend paragraphs getting those particulars in. Arrows are dependencies - the task an arrow points at could not be started until the other was done, which is not the same as coming later - and the same call that orders the pieces of one task is asked of the tasks themselves.

**A disc carries an emblem generated from the task's id**, which is the one thing about a task that lasts: appending, re-summarising, growing and being found again by a rebuild all leave it alone, so *the one with the spokes* stays the one with the spokes from week to week, and a new emblem means a genuinely new task. It is a handle and not a reading - nothing about the work chooses the motif, and the code says so, because a picture that looks meaningful will be read as meaningful.

**Clicking unfolds one layer, in the picture.** A task opens onto the tasks inside it, and those onto the tasks inside those: **every layer is summarised work, and a state is never a row.** The bottom layer is the bottom because nothing inside it divided again, not because it is a list of what was recorded - ending the tree in raw states put two different kinds of thing under one gesture, so the same click gave a summary in one place and a transcript in another, and nothing in the shape of a row told the reader which they were about to get. A layer with nothing under it says so in the splitter's own words rather than opening onto a gap, which reads as a drawing that failed. Which nodes are open is a SET and not the last one clicked, because three layers deep means a piece can be open inside an open piece. A circle is hollow until everything in it is finished and solid when it is, said once in a legend rather than spelled out on every row, where there is least space for it. The rows below slide down to make room, the node clicked does not move, and the drawing mutates rather than being redrawn - a redraw replaces the elements, and an element that did not exist a frame ago has nothing to move from.

**A panel under the picture reads out whatever was clicked** - any node at any depth, with its four parts, how long its own states cover, how many of them are finished, what is inside it or the reason nothing is, and its weight. A piece shows the weight of the TASK and says whose it is: the weighing is of the task, and bare numbers on a piece would read as a judgement of the piece that nothing in the round ever made. The picture selects and the panel reads, which is the arrangement a strategy game uses for the same reason: there is room beside a circle for a name and a count and for nothing else.

> **The picture is never fitted to its box**, and getting that wrong three times is worth recording. An SVG told to fit a box scales its whole drawing to do it, so opening a task made every row SMALLER instead of making the frame longer. The first fix set the pixel size and left the viewBox, which is the same instruction in another form. The second set both - correctly - in a rule that sat ABOVE the dendrogram's `.sb-graph`, and two single-class rules carry the same weight, so the later one wins and the override did nothing. What finally held it was asserting the COMPUTED style in the browser, which needed the test harness to load the stylesheet at all: it had been rendering the page unstyled, which is why a suite that draws the real page in a real browser had nothing to say about a cascade bug in it.

**The axis covers the whole calendar.** The drawing grows wider rather than squeezing - a fixed width per day - so a week never shrinks into a few pixels, and the frame scrolls in both directions. The drawing can be zoomed with Ctrl and the wheel - which is the gesture that works, because Ctrl with the plus and minus KEYS is a browser accelerator a page cannot reliably cancel, and offering it as if it could would be a promise the code cannot keep.

> **A 4 is an answer and not a blank**, and three faults of one shape hid that. The weight was kept only when the model volunteered a number in range - so a task summarised a second ago read *not weighed yet*, which is a claim about storage wearing the clothes of a claim about the task. The reason was written under one name by the summary and another by the status call, and the panel read the second, so the sentence explaining the numbers never appeared. And the status call updated the task's own copy while the ordering read the summary's, which would have sorted the tree by last round's urgency beside a card showing this round's. ONE NUMBER, ALWAYS KEPT, with a record of whether it was read off the states or stood in for their silence: a middling number with a reason and a middling number meaning nothing was said are different claims, and only the first can be checked.

**Ordered by urgency, or by importance, and the model is asked for both** on the 1-to-7 scale the traits already use, with every point anchored and six worked examples. A task the states say nothing about gets a 4 and has to say so: a middling number with *the states give no sign* is honest, and a 7 chosen because the subject sounds weighty is not. **Ties keep the order they had**, falling back to the seat - so sorting rearranges what the numbers actually separate and leaves everything else where the reader last saw it, which a tie-break reaching for size or recency would quietly undo.

**The grouping anchors before it scores anything.** What a state SAYS is the model's paraphrase of a quarter of an hour: two states about one afternoon are two summaries, written separately, of different slices, and their similarity is how alike the model happened to word them. It has a high noise floor for the reason given above - one model writes every state, in a stable vocabulary, about a handful of projects. **A path is not a paraphrase.** `survey-weights/scripts/fetch.py` is the same string every time or it is absent, and two states naming it are on one piece of work whatever either of them says about it. So a first pass joins those outright, and the tree runs with them already joined.

**The object, not the instrument.** What identifies work is what was open - the file, the page, the document - never what it was open in. One script edited in Sublime, run in a terminal and diffed in a git client is one workspace and three applications; a page is the same page in Firefox and in Chrome. Keying on the application does not merely add noise, it INVERTS the structure: it merges Gmail with YouTube for both being Firefox, and splits one paper across three tools. Applications are still read and still shown, and they identify nothing. There is no list of which ones to ignore - a list goes stale - only the question of whether a piece of evidence names a thing or a way of handling things. The one exception is a bare window title, where *Personalized Agent* (a repository page) and *Mozilla Firefox* (a browser) are both two capitalised words: nothing in the shape tells them apart, so the CORPUS does, since one of them also turns up as the application half of other titles.

*Amended 2026-10-16:* when grouping, a state's places are only those that tell which work it was: a folder or a file on disk, a document by its name, and a page at least two levels into a site that is not visited for everything (a site with forty different pages in what one round reads is a search engine, a video site or the mail). A window's title, an application's name, a conversation's title and a site's front page are no longer places at all: each turns up beside every kind of work, and credited as a shared place it put a day of one piece of work into another. Replayed over a week of the user's labelled states, the share put in the wrong task fell from 24% to 22% and no routine state went into a task of work, at the price of more states left in no task - the cheaper mistake. A new reading of places applies from the next state on; it never regroups what is kept.

*Amended 2026-10-16:* NO UPGRADE REGROUPS THE TASKS. For a few hours one build counted how places are read among the settings the tasks were built with, so starting it rebuilt every task from the tree; on the user's calendar of 2,843 states the rebuild merged twelve different pieces of work into one and left most states in none. How places are read is no such setting, and a setting the record of the last build does not have is no change: an upgrade adds settings, and must not regroup anyone. That one rebuild is undone once, at the next round, where it happened: the tasks it retired are live again, with their names, summaries and projects, the two it made are retired, and its record no longer counts as the last build. And a task is one of leisure only when most of ALL its states are said to be leisure - not most of the few whose kind was said: two hundred states, fourteen of them said, eight of those leisure, had made a piece of work leisure.

*Amended 2026-10-17:* NOT THIS WORK, AND THE PILE OFFERED AGAIN - both built, both switched off until measured and approved, because both move the user's states. The call that already sums a task up, and the one that says where it stands when states arrive, also name - by the numbers of the states shown - those plainly about something else (`not_this_work`): no call of its own. Switched on, they go to the pile; a task smaller than six states, or one that would lose more than half of itself, keeps them; each removal is recorded. Switched off, what the model named is only kept and shown, in the State heartbeat debugger, for the user to judge. A re-check by meaning and places alone was measured first, against the user's labelled week, and rejected: of what it would move, only five to six in ten were really in the wrong task. And the pile - states no task took - is offered to the tasks again each round, as new states are: offered is not taken, a state goes in only where it fits; a state the model took out of a task is offered back to that task only in a round the task gains others, so it rides on the status call made for them and never brings a call of its own.

*Amended 2026-10-16:* ROUTINE IS NOT A TASK. The review that writes a state says, as its `kind`, when it is the part of a day that is no piece of work - meals, rest, entertainment, the news, chatting with friends and family, errands; anything done towards something, however personal, is work. Those states stay on the calendar and are never grouped: grouped with the rest, they made tasks of their own and their piles took work in. In the replayed week most of the states put in the wrong task went into a task that was mostly routine, and with the routine left out the share of work states in the wrong task fell from 36% to 25%. States written before the review said so carry no kind and are grouped as before.

*Amended 2026-10-16:* LEISURE IS A TASK, OF ITS OWN KIND. The `kind` is one of three: work, leisure - what is done for enjoyment: films, shows, music, games, sport, the news, chatting with friends, browsing for pleasure - and routine, now only meals, rest, errands and chores. Leisure is grouped and summed up like work, for what is not summed up is never recommended from; but never with work. A state joins only a task of its own kind, what fits no task is made into tasks kind by kind, and a task read off the tree holding a task's worth of both has its smaller part made a task of its own. A task is of the kind most of its states were said to be; states that carry no kind go where they fit, as before.

*Amended 2026-10-16:* the Overview's picture of the tasks has two views, chosen with the button beneath it: WORK ACTIVITY - the tasks of work alone, as the page opens - and FULL ACTIVITY, everything done: the tasks of leisure as well, and the groupings a summary judged not to be a task. A task is drawn as one of leisure when most of its states whose kind was said were said to be leisure, a project when all its tasks are.

*Amended 2026-10-16:* RUN NOW, beside the Overview's tasks: a round of the state heartbeat begun at once, answered straight away, its progress drawn as it goes, with a line saying when the next regular round is due. The regular rounds are counted from when the last round FINISHED, whoever started it, so a round run now puts the next regular one off by as long. One round at a time: while a round is going - regular or run now - Run now does nothing and says so, and while one run now is going the timer begins no other; its visits only carry that round on, and two visits never run at once. And the first grouping waits for 20 states, not 100: with fewer, its first tasks - five states at least, so at most two - would be broad, and as tasks are never regrouped they would go on taking in whatever came near.

*Amended 2026-10-16:* the button is the card's own, as "Make an issue now" is - full size, under the Summary task heading, called "Update tasks now", and "Updating tasks..." while a round runs.

**A second kind, for work that names no file.** A conversation about a paper, a week of reading, an exchange with a reviewer - none of them name a path, and all of them say *FieldNotes* or *survey-weights* over and over. So states sharing **three or more words that almost nothing else in the window uses** are anchored too. Three and not one, because one rare word in common is a coincidence - two states each mentioning Lisbon may be a trip and a restaurant - and an anchor is meant to be the part that is certain. This is not what the embedding does, and could not be: the vectors are trained to know that *paper* and *manuscript* mean the same, which is exactly wrong for a proper noun, whose whole value is that nothing else is called that. The two kinds run over disjoint states and are never joined to each other - two kinds of evidence chained together is how a chain gets long - and the debugger shows them as two.

> **Anchors do not chain, and this is the whole of the difficulty.** The first version was union-find over any shared key, and on four hundred real states it produced one anchor of a hundred and forty-seven with NOTHING in common across it: A shared a file with B, B shared a page with C, and three unrelated months became one task. So a workspace anchor is the set of states sharing ONE key - a state naming several joins the rarest, which is the most specific thing it was open on - and a word anchor whose members share no word between all of them is refused whole rather than trimmed, because there is no honest way to choose which link of a chain was the real one.

> **And an anchor is bounded at both ends.** A key on a tenth of the window is where the work lives rather than what it is; but a bare fraction rounds to one on a small window, where nothing could ever anchor, and is unbounded on a large one - and an anchored group merges at distance nought, which puts it beyond the splitter's reach, so an anchor of two hundred states would be one task nothing could divide. Hence a floor and a cap of forty, which is the size of a large task.

**A shared ANCESTOR only pulls.** `Research/AA/BB` and `Research/AA/CC` may be one task with two parts or two papers on one shelf, and nothing in the path says which - so a shared folder shortens the distance in proportion to how much of the path agrees, at most by half, and leaves the question to the splitting rule. It never pushes apart: a shared workspace is nearly conclusive that two states are one task, while sharing none is almost no evidence that they are not, because one task spans a browser, an editor and a terminal. Only one of those directions is worth acting on. **A state that names nothing takes no part at all** - absence is not agreement, and letting two states with no evidence look alike to each other is what made the original two-measure design a structural zero.

> Read back out of the states' own prose for now, because the listener knows the paths exactly and the state-writing step dissolves them into sentences. That is a fault upstream, and until it is fixed this covers about two states in five.

**A task keeps its place on the page for as long as it exists.** The reading order was the age of the task and then its size, which is almost right and fails on the case that matters most: every task from the first rebuild shares one `created_at` to the second, so the tie-break decided the whole page and the tie-break was size. Two tasks that swapped sizes swapped places, and *the third row* was a different piece of work each hour - which is precisely the instability the persistent ids were introduced to remove, returning in the reading order after being fixed in the data. A task is now given a **seat** when it first appears and never reassigned one. Anything new sits after everything that already existed, so the page grows downwards and nothing above it moves. **A split keeps one piece in place**: the piece that inherits the old task's identity inherits its seat with it, and the others are new tasks that happen to have a parent, so they go where new tasks go. The tasks that existed before seats did are seated in the order they were already being shown in - a fix for an unstable order that reordered the page once on the way in would be a poor joke.

**And the two ways a task can have no search are different facts.** The page said *no call* on one row and *no search* on another, which read as two shades of one absence and left the reader to work out which absence. They are not related: *no call* is the round not asking the model anything, because the summary the task already has still describes what it holds; *no search* is the model being asked and answering out of the states it was shown. So the badge leads with the number of calls, which is the thing that differs - **0 calls - summary reused**, **1 call, no search**, **1 call, 2 searches** - and the sentence under it says which of the two happened in full.

**A prompt can be taken off the page whole.** Reading one is not having it: what a person does with a prompt that produced an odd summary is paste it somewhere and change a line, and selecting sixteen thousand characters out of a scrolling box is a minute of dragging that the panel then interrupts. A copy button sits in the summary row of each prompt block, so the prompt can be taken without opening it - which means the click has to be stopped from toggling the block it sits in. It copies **the instruction and the message and nothing else**: the block also holds what came back, and a button labelled `copy` beside the words *the prompt it was sent* must not hand over the model's replies as though they were part of it.

**And a rehearsal is compared task by task, not only round by round.** The table of sizes answers *did it grow* and nothing else, while the question a rehearsal exists to answer is what appending DID. So every task that existed in **any** round gets a row per round: how many states it held, which ones arrived - **named, not counted** - and how it was described, with each of the four parts marked *changed this round*, *unchanged*, or *written for the first time*. A round in which a task did not exist says so rather than leaving the cell empty, because a blank cell reads as *nothing changed*, which is the opposite of what happened. A task absent from the first round is a finding about the first round, and leaving it out of the table would hide the arrival the rehearsal was run to watch.

**Nothing on the debugger opens itself.** Each block had a good local reason to start open - the step's prompt, so it could be read while the answer was awaited; the top layer of tasks, because a page that runs something and then shows a row of closed rows has not shown anything; the comparison, because it is the point of a rehearsal - and the sum of those reasons was a page that unrolled everything it had the moment it finished. The rule that made the top layer open was written when a task was a title and a list of states. A task now carries its description in four parts, what the call cost, whether it searched, every reply it got and a tech tree; eight of those at once is not a result but a wall, with the one thing the reader came for somewhere inside it. So everything is closed, and the row itself says the title, the size and whether it searched - which is what choosing between them needs. **And every block remembers being opened**, because one that does not closes again on the next redraw, which while a round is running is about twice a second.

> Tested as one rule and not one block at a time, since testing them one at a time is how the page came to unroll itself: after a round, how many blocks are open, and how many have no key to be remembered by. Both answers are zero.

**A redraw that changes nothing does nothing, and never happens over a selection.** Following a round means asking every four hundred milliseconds and drawing what comes back, which is right for a progress bar and was quietly hostile to the one thing this page is for. The prompt is shown *before* the answer arrives - that is the point of it - so the reader is looking at it during exactly the seconds the panel is rewriting itself, and replacing the HTML drops the selection: text could not be copied out of the page at all while a round ran. Two rules, in order. **Nothing changed, nothing written** - a summary takes ten or fifteen seconds and the step list does not move between them, so most of those polls were rewriting identical HTML, and one string comparison removes the great majority of the redraws. **And never over a selection**: when something has changed and the reader is mid-drag, the new HTML is held and goes in when they let go, so the panel is at most one beat behind and only while somebody is holding onto it.

> The comparison has to be against what was last **written**, not against what the element reads back. `innerHTML` returns the DOM re-serialised - attributes in the parser's order, entities normalised - so comparing it with the string that produced it is false every time, and the first version of this guard did nothing whatsoever.

**And an id belongs to one element.** Three panels were sharing two names: the month-by-month states browser and the state heartbeat both answered to `dbg-states-body`, and the heartbeat's own body had taken the *review* heartbeat's `dbg-beat-body`. A lookup by id returns whichever element comes first in the document, so this is not a tidiness complaint - the browser was writing its states into the heartbeat's panel, binding its click handler there, and hiding it with its own collapse button. The existing test asked that every id the script reaches for exists, which was true throughout; what was missing was asking that no id answers twice.

**A summary is one statement at one altitude, not the states with their dates taken off.** Shown forty states, the easiest sentence to write is all forty of them: *“fix the census fetch step, re-run the scripts, check the figures against the published ones, and write back to the reviewer”*. It is accurate, it is the failure that looks most like success, and it says nothing a reader could not get by reading the forty titles - which is the thing the summary exists to save them from. What is missing is the only thing that had to be worked out: what all four were **for**. So the instruction now names the symptom rather than asking for concision, because concision is advice and this is a test the model can apply to its own draft: if the sentence joins clauses with *and*, *then* or *as well as*, it is listing, and the fix is to go up a level until the list becomes a single noun. A particular step may be named where it is the one thing the whole group is about, never as an item in a series.

> **The prompt's own GOOD example was written that way**, which is how the habit was being taught rather than merely tolerated - a model shown a worked example follows its shape over any rule stated beside it. And the shape is now **counted on every summary**: how many things the first sentence joins, with two or more marked on the debugger. Counted and not corrected, because going up a level is not something that can be done to a finished sentence - and counted per sentence rather than added up, since *“the visa and expense paperwork”* is one errand with two names and a rule that flags it is a rule that gets ignored.

**There are three layers by default - a task, the tasks inside it, and the tasks inside those - and the setting allows up to six.** The descent once stopped for reasons of its own and could go as deep as the tree allowed, which meant the page's depth was a property of the user's month, and a reader could not learn the shape of the page because it did not have one. So the depth is a setting. Every layer is summarised work: the bottom layer is the bottom because nothing inside it divided again, not because it is a list of raw states. **The limit is also information the cut can use**: knowing how many levels remain below it, the descent is choosing where to put its boundaries rather than seeding an open-ended recursion.

**Above tasks sit projects, and each carries its next steps.** Tasks that are one larger piece of work are grouped into a project: a project is found from where the work lives, and kept only once the model agrees the tasks are one piece of work. Each project, and each task that belongs to no project, keeps a short list of at most eight next steps. The model writes it, the user may edit it, and the agent heartbeat's to-do step ticks a step off when the activity shows it done. After each round the work is also written as stages, for the user's experience. A task left alone for three days is drawn as quiet, and after thirty days it is archived and no longer drawn; a state that fits nothing for thirty days is let go; a task's centre is a decaying mean with a half-life of fifty states. Calls in a round may run a few at a time - three by default - but never more than the computer can spare while the user is at it, on battery, or short of memory or processor. The whole of it can be turned off with the "Task organisation" Function in Settings.

**And the last layer is drawn as a tech tree.** A bulleted list of sub-tasks answers *what was inside this* and none of the questions a reader actually has next, which are *when* and *what had to happen first*. So the pieces are laid out left to right on a time axis at the point each one started, a circle each, sized by how many states it holds, with the finished ones marked as finished and dependencies drawn as arrows. **The times are arithmetic and the arrows are a judgement**, and they are got separately for that reason: when a piece ran is a minimum and a maximum over its states, while *which piece could not have been started until another was done* is not in the states at all - order of occurrence is not dependency, and a graph drawn from the timestamps alone is a chain through five things that merely followed one another. So the times and the closures are handed over as facts and the model is asked only for the edges, and told plainly that most pairs have none, because a model asked for a graph will draw one.

> That call was raising `NameError` on every round from the day it was written: it referred to a name nothing had bound, the round's own `except` turned that into the report's `error` field, and the tree simply never appeared. Six hundred and forty-one tests passed over it, because a round that catches its own exceptions still returns a full report and nothing was checking that field. A round now has to finish clean for the suite to pass, which is the assertion that was missing rather than the one that failed.

**And “the whole prompt” means both halves of it.** The step list showed each call's message under a heading reading *the whole prompt*, with a token line under that reading *0 of instruction, 0 of states* - so a reader following a round saw the states and concluded the model had never been told the output format, what counts as a task, or that it was allowed to search. The amendment above had put the instruction beside the message in one place and not in the other, which is the worse of the two failures: a panel that shows nothing invites a question, and a panel that shows half of something under the word *whole* answers one. One function now writes a prompt onto a step, it writes both halves, and it counts them apart - the instruction is identical on every call of a round and is what a cache hit is made of, while the message is the states and is different every time, so one number over the pair would hide which of them a round is paying for.

**Everything the model said is kept, whole, beside the task it produced.** What was left on disk was the summary: a title, four sentences, a list of ids - the part that is right when nothing went wrong. Everything that answers *why is this summary odd* lived in the report, which is in memory and lasts until the next round overwrites it: the reply that was not JSON and left every field empty, the search that came back with nothing and the summary written anyway, the state ids that had to be stripped out of a title before the user saw it. So each summary now writes a transcript - the instruction, every call in order with its reply unaltered, **whether it chose to search** and what it asked for, what the search returned, and what was kept from the answer against what arrived - and the record points at it. Whether it searched is written in words and written either way, because *no search* and *no record of a search* are different facts and a missing field reads as both. It goes in a sub-directory as `.json`, which is how the library comes to ignore it: a transcript is a log of how something was decided, not something the agent knows, and one coming back from a search would put the model's own draft answer in front of the user as a fact.

> **And then it was not.** The paragraph above is what was built, and it was measured after a day of real use: seventy files and two and a half megabytes a day, nothing ever deleting one, and about half of each file the same text stored twice - the searches repeated the prompts the exchange had already recorded, and the instruction was written once per call although it is identical in every call of a round. The argument for keeping it was that the report is in memory and lasts only until the next round overwrites it. The answer is that what a round PRODUCES is the task, its summary and its four parts, and those are records in the library written as they always were; how the model arrived at them is a thing to look at once, while looking at it. So none of it is written to disk. **Everything above is still collected and still reaches the panel whole** - every prompt, every reply unaltered, each search's question, result and follow-up, whether it chose to search, and what was stripped out of the answer. Only the file is gone, and the page says so in as many words, because a reader told nothing assumes it is filed somewhere.

**It is watched from a page of its own.** Not a card on the heartbeat page: that one is a single run of the review that writes states, step by step, and this is what the states already written add up to, read all at once. What a reader has to judge here is not the order things happened in but *why these were put together* - so the page is the groups, each opening into the states under it and the words they share, with every scored pair underneath, **including the ones that did not join**: the pair that nearly joined is what answers "is the threshold wrong".

**Everything the agent adds says when it was added.** A state, a trait and a scheduled task each carry `created_at`: the moment the agent wrote it down. It is not the same fact as any other time they hold - a state has a period, a task has an hour it runs at, and a trait is about a person rather than a moment - and without it a state written this morning about a fortnight of work reads exactly like one written a fortnight ago, which is a very different claim about how long the agent has been watching. All three recorded it already; the **state card did not show it**, while the task card and the record page had all along.

**Stopping a loop does not wait out its interval.** Each heartbeat sleeps on the event that says *your interval setting changed*, with the whole interval as the timeout; `stop()` set a different event, so the thread slept through it and the join that follows spent its full five seconds failing. Three loops did that one after another, so quitting the agent took **fifteen seconds and looked like a hang** - the threads are daemons, so the process did eventually go, and what the user saw was fifteen seconds of nothing. Stopping now sets both events: the sleep returns at once, sees the stop and ends. Measured at 15.04s before and 0.001s after, with no threads left behind.

**The review is four steps that run** - states, people, notifications and next steps, the last only when there are open next steps - **plus a fifth, who the user is, that is kept but paused**: what the user is like is now learned from their lessons instead. There was once another step, and it judged what was on the calendar: it read every state whose end had passed and said *finished*, *extended* or *retired*. It is gone, with the verdicts, the two groups it was shown and the cap on how many it could apply. **What follows from that** is that nothing closes a state any more. A state stops describing the present when its end goes by - `covering` reads the dates, so what an answer is built from stays correct - but its status stays `active` for good, and the count of extensions, which existed to find a state nobody ever finishes, is no longer written by anything. The store keeps the queries; the heartbeat no longer asks them.

**How many notifications one review adds is not fixed.** None is the usual answer, because a notification interrupts; but a promise the user made to do something later is worth one, set for the moment it becomes useful. A notification invented to fill the list is an interruption the user has to dismiss, and a run that skips an obvious one because it had already named another has lost it.

**The step that decides what to do adds notifications, and disables ones whose thing is already done.** It reads the same window as the steps before it, the states this review has just written - which are not on the calendar yet, because nothing is written until every step has answered - every notification already ahead, what is waiting for a time, and the lessons learned from how the user treated earlier notifications. It can open anything in the window by its id, like the steps before it. **A notification is not a state**: a state says what is true and is only ever read, a notification is a thing that reaches the user at a time, and sharing a calendar is the whole of what they have in common.

**The confidence scale explains all seven of its points.** It ran 1 to 7 and described five of them, and the two it left out were 6 and 3 - either side of the middle, which is where most honest answers land.

**A step is not told how it was started.** *"You run on a timer, not in a conversation"* stood at the top of every call and named nothing the call could act on. Nor is a step told what it *itself* concluded a moment ago: each is its own call with its own context, so what an earlier step worked out arrives as material handed over - **what has already been worked out from this activity** - rather than as a memory of a conversation it was not in.

**The step that reads the window is not shown the calendar.** It used to be given every state running now, so that it could avoid writing one twice - and that is a review of the evidence that begins with last hour's conclusions, which is a review that mostly confirms them. The window is the evidence; the calendar is what earlier runs made of earlier windows. The section is not shown empty either: a heading reading *"what you already believe"* with nothing under it invites the model to account for the emptiness instead of reading what is in front of it, so the context is numbered by the sections it actually has. **What follows from it** is that the step can no longer name a running state to carry on: `continues` needed an id it is no longer shown, so the instruction and the field are gone from the prompt, and something still going is written for this window as this window saw it. The step after it still gets states - not these, but the ones this step has just concluded.

**The pass that rewrites them reads the states folder once.** Finding a state's file by its id searches the whole library - every folder, every file - so doing it once per state was thirteen hundred scans of twelve thousand files on a real library, on the startup path, and the application did not finish starting. It now reads the folder once, rewrites only the files that carry a horizon, and says how many before it begins: a few seconds of silence during startup is indistinguishable from the thing having hung.

**Every state says which stretch of activity it was read from.** A state that took forty minutes is a different claim depending on whether the review saw fifteen minutes or four hours, and the state carried no way to tell them apart - so each one written now carries the range the heartbeat covered: the bounds it asked for, the first and last event that actually fell inside them, and how many there were. Both halves earn their place, because a quiet quarter of an hour holding two events and a busy one holding three hundred are not the same evidence and the clock alone cannot separate them. **The model is never asked for it.** It is a fact about how the review was set up rather than a reading of anything, already known to the code that writes the state, and a number the model copies back is a number that can come back wrong - so the script stamps it and a range the model volunteers is ignored.

**Every state says where the user was standing.** Everything here is stored in UTC, which is right - one clock, no ambiguity, arithmetic that survives a move - and it is not what anybody's week looks like. A state running from 23:00 to 01:00 UTC is a Tuesday evening in Lisbon and a Wednesday morning in Shanghai, and every question a person asks about their own calendar - *what do my Mondays look like*, *do I work late*, *was that the weekend* - is a question about the second one. So each state carries the offset that was in force, the name the system gave the zone, both ends of its span in local time, and the day of the week. The day it is filed under is the day it **began**: a state that runs past midnight belongs to the evening it started in, which is the sitting a person remembers.

**And the grouping reads them in that week, not in UTC.** A `ran:` line put in front of the model is now the user's own clock, with the day of the week named and the zone in brackets - *Sunday, 18:30 to 20:00 [WEST]* - because *a Tuesday evening* and *a Sunday afternoon* are different facts about a person and `2026-09-27T17:30Z` is neither of them. A span that crosses midnight names both days. A state with no local time recorded says **UTC** in so many words rather than being shown in the same shape as the others: presenting UTC as though it were somebody's own clock is how a Wednesday morning becomes a Tuesday night, and the model has no way to notice. Both renderers go through one function, because the sample and the full state a search returns had drifted apart once already.

**The states written before any of this carry an assumed zone.** They cannot carry a real one: where somebody was in July is not something that can be read off a record, and the machine's zone today is not evidence about it. So they are read, once, as a zone the user names - and **marked as assumed, with the zone that was assumed**, because a state stamped as it was written knows where the machine was while these know only what they were told afterwards, and a reader deciding whether to trust a Tuesday should be able to tell the two apart. A calendar where two thirds of the entries can be placed in a week and the rest cannot is worse than either: every question about the user's Mondays would silently be a question about the last fortnight.

> **Named zone, not a fixed offset**, and this is the part that is cheap to get wrong: a calendar of any length spans a daylight-saving change, so half these states are on one side of it. The first version took the offset at the moment of the PASS rather than at the moment of the STATE, and a January state came out labelled `WEST` with `+01:00` beside local times reading `+00:00` - the record contradicting itself inside one dictionary. The offset is the one that was in force when the state ran.

> Stamped on the way in, at the store, rather than asked of whoever writes the state - it is a fact about the machine's clock, every writer would have to remember it, and one that forgot would leave a state that cannot be placed in anybody's week. And **not recomputed on the way out**: the user may be in another zone by then, and the honest answer is the zone they were in at the time. The zone is recorded as the name the system gives and an offset, not as an IANA key - there is no way to get one from the standard library on every platform, and a guessed `Asia/Shanghai` that is wrong is worse than `CST`.

**There is no horizon.** A state used to carry "short" or "long" beside its dates, read off the span by a threshold - which is the span again in a word, and a word beside the dates is a second place for the two to disagree. It was also the first thing on the line the agent read (*"(short term, the user) ..."*) and a keyword the file was filed under, so the least of what a state says led everything that quoted it. The period says all of it, and `span_hours` says it as a number. Nothing chooses between two fixed shapes any more either: a state that gives no dates at all falls back to **about now**, which is the only thing it can honestly mean, and something longer-lived is a state already on the calendar carried on one window at a time. **The files written before it was dropped are rewritten once**, because a folder holding two kinds of state file - where only the old ones answer to a word the agent has stopped using - is worse than either.

**And what a step wrote is reported as the thing it wrote.** `states_written` said `"sources": 2, "evidence": 3` - a count of a list nobody could then read, which is the one part of a state that cannot be checked. It now carries each state in full: the period, how much of the window went to it, the files and pages themselves, the argument, and the stretch the review read it from.

**An outcome nobody has reached yet is not an outcome of nothing.** What each step wrote is recorded after all four have answered, so for about a minute of every run step one had answered and had nothing on it - and that was shown as *"Nothing came of this step"*, which is the opposite conclusion and the one a reader watching the run was most likely to see. An empty outcome now says which kind of empty it is, and the panel waits rather than concluding.

**A trait has the shape a state has.** It was a title and a paragraph. It now carries the same three things, for the same reason: what it rests on, how it was reached, and **how sure the agent is** on a scale of one to seven. A trait is a claim about a person that they never made, and one whose basis they cannot see is one they cannot argue with. The scale matters more than a flag would: *"I watched this hold for a month"* and *"this looked like a pattern once"* are both worth writing down and are not the same claim, and the second written as the first is something the agent will act on and keep acting on. A confidence left unsaid is the middle, one off the scale is brought onto it, and nonsense in the field does not lose the trait. **The step that writes traits is now paused**: its prompt and code are kept, but it does not run. What kind of person the user is now comes from the lessons their own actions teach, not from guesses based on a few minutes of activity.

**A window is usually two or three states, and each is a summary rather than an event.** Opening a file is not a state; what the file was being opened *for* is. The instruction carries examples from both sides of that line - *"working on the survey-weights replication"* covering an editor, a paper and a terminal, and *"watching videos on YouTube"* covering nine video pages as one state - because the altitude is easier to see from two sides than from a definition, and the failures either side of it are "opened search.py" and "using the computer".

**The state step is about the user.** Its instruction said otherwise - *what you intend to do for them is also a state* - so a calendar meant to describe the user's situation back to them filled with the agent's own intentions, described as though they were the user's. What the agent means to do is a scheduled task and a record of one; it is not the user's situation.

**And it covers everything in the window.** The first attempt told it not to write a state that was already there, and read together with "write several states" that means *say nothing about anything you are already tracking*. Seen in a test run: the analysis named four activities - preparing slides, a code review, reading, a message exchange - and wrote one state, explaining in its own words that the other three were already on the calendar. A window where somebody carried on with three things they were already doing is a window with three states in it, and one state describes a different evening.

Leaving them out is the thing that loses information: the window then says they never happened. The step once marked anything that was one of the running states with **`continues`**, the id of the state it carried on, so that the state's end moved rather than a second state being written. That is superseded by "The step that reads the window is not shown the calendar", above: the step is no longer shown the running states, so something still going is written for this window as this window saw it, and it names no running state. The code that handled `continues` is kept, but nothing reaches it.

### The Step After It Gathers For Itself Too

The step after the state step wrote who the user is. It was first handed a context built from a query of names in the recent activity, which is the wrong material for *who is this*: a habit is the same afternoon four times, and the other three afternoons are not in this window. It was then given the same floor as the state step, with the states that step had just answered in place of the running ones, and told to write nothing rather than a habit it had seen once.

**That step is now paused.** Its prompt and code are kept, but it does not run, and its slot went to the people step, which writes the user's contacts. What the user is like is learned from the lessons their own actions teach.

Every step now gathers for itself: each gets its own copy of the same window, and the steps after the first are also shown the states this review has just written.

## The Step Decides What It Needs To See

Choosing in code what to look up, before the question is asked, is a guess: a fixed share of a fixed budget per folder is an allocation decided before anything is known about the morning being allocated for.

So every step is given a floor and left to decide what more it needs. Since the library index and search were removed (commit fcf74e1), there is nothing to search beyond the window:

**Each part of that context says what it is.** The headings were labels - `WHAT IS HAPPENING`, `STATES RUNNING NOW` - which name a section without saying what is in it or where it came from, and the parts are read very differently: one is an observation of the user, and one is the agent's own conclusions. So each is introduced: the activity as recorded by the listener and *evidence*, and the states this review has just written as conclusions that can be wrong.

- **what is happening**, which is every event since the last review ended, folded - not a fixed number of the most recent, because four events in a window and four hundred are different situations and a `limit` reports the same figure for both
- for the steps after the first, **the states this review has just written**. The state step is not shown what is already on the calendar

**The window is everything since the last review ended.** It began as a fixed few minutes and that is a second number saying what the interval already says: the heartbeat wakes every so often and asks what is going on, so the honest window for that question is *since it last asked*. Five minutes on a fifteen-minute interval throws away two thirds of what happened, every time, and nothing in the answer says that it did. A run started by hand uses the same window - it stands in for the run that would have happened, and a different window would make it a different thing. If the agent was closed for more than thirty minutes, the stretch it missed is read on its own, soon after start-up, in turns of at most three hundred events, oldest first.

That also removes a tool. The step used to be able to **look further back**, because the floor did not reach the beginning of what it was looking at; with the gap itself as the window there is nothing behind it that belongs to this review. What it can still ask for is to read one item in the window whole:

- **open it by its id.** The window is folded, so a step sees each item briefly; what the item says in full is a second act, and it costs more, so it is asked for separately. Anything in the window can be opened this way - something the user did, a change the agent made, a state, a notification. A step may come back for more up to six times before it must answer.

**And each call carries the message it sent**, whole. The row that ends the loop - the one that says the step answered rather than asking again - carried the answer cut at four hundred characters, so the row meant to show what the step concluded showed its first sentence and stopped mid-word.

A row that counts what went into it - *20 lines of activity, 146 states, 0 records* - says how much and never what, and what was sent is the only thing that explains what came back. A later round carries **what was added for it**, with how many messages it stands on, because the call holds every round before it as well and printing the whole conversation on each row is the same text four times. The standing instruction is there too: it is sent every round and appears in none of the messages, so a reader rebuilding the call from the message alone is missing it.

**A message that offers tools must not end by forbidding them.** This one did: after the paragraph inviting it to ask for more, and the context, and the time, it closed with *"Return ONLY this JSON object"* - an instruction not to call a tool, in the last and strongest position. The step read the nearest instruction and answered from its floor, so the tools it had been given went unused on exactly the round where using them was the point. It now ends with the two things it can do: call a tool and be asked again, or answer. Every step now has the same tool, so every step's message ends this way.

**So are the tools it was offered.** There is no separate call asking whether to look further - the message that asks for the answer carries the tools, and the model answers by using one or by not using one. The offer is therefore half of that ask, and it travels in the request beside the messages rather than inside them: a reader rebuilding the call from the message alone sees the step being told it may ask for things, and never what it could ask for or what each takes.

**Every step says what it cost, start to finish.** The header read *"1 memories, 41 ms"*, and the 41 ms was the gathering - the cheap half. The minute somebody waits through is the model calls, and it was reported nowhere; nor was what the step spent, which is the number anybody deciding about the interval actually needs. Per step, because a total for the run says a heartbeat is expensive and not which part of it is, and the state step's cost is not one message but the sum of several growing ones. Both clocks are kept: the gathering and the calls are different costs with different remedies, and one number covering both hides which is which.

**Sizes are in tokens.** They read *132,769 characters*, which is a fact about a string. What is capped, paid for and worth deciding about is tokens, and nobody does that division in their head. The counts come from the agent, using the same estimator the budgets use.

**The trail records the calls as well as their results.** Without them it reads `start, fold, search` - which looks like a search that happened before any model was asked, when the search is the *result* of the first call and is the only thing explaining why those words and not others. Each call is a row, numbered, with how much context it carried; the row that ends the loop says the step answered rather than asking again.

The panel's labels followed from the same confusion. "The prompt it was sent" is the **first** message of several, so it says so, and the block that used to read "the text the model was given" is **the context as it stood at the end** - which is not what the first call saw, and is the thing a reader compares against the trail.

**It stops when it says it has enough**, and a cap stops it when it does not. An answer given because the rounds ran out is not the same as one given because the step was satisfied, so the trail says which - a step that was still asking when it was cut off is a step that answered on less than it wanted.

> The cost is what it decides to spend. A step that has enough answers in one call, as before; a step that does not spends a few more, on the run where that was worth doing. The floor is small on purpose, which is what makes the asking affordable.

### A Name Is Not What Someone Is Doing

The feed arrived with the text of every event stripped out of it.

`content` is the reading of the window - what the page actually said - and it was dropped on the way to the heartbeat, along with the rest of the address, leaving `firefox.exe` and a hostname. So a morning reading one paper and a morning buying a fridge reach the step as the same three words, and the step that is supposed to say what the user is doing was answering from the name of the program it was done in. Nobody works on a program.

So each event carries its title, its whole address and what it said, and the events are shown **as events** rather than as a slice of JSON - one to a line, with what does not fit dropped whole rather than the feed being cut off mid-string, which used to remove the text first and the oldest half of the window with it.

**The text has its own budget, spent newest first.** One reading can run to four thousand characters and a busy window is hundreds of them, so this is the difference between a step that sees what was on the screen and one handed a transcript of the afternoon in place of a question. An event whose text did not fit says so, rather than looking like an event that had nothing to say.

### The Same Thing Forty Times Is One Line And A Number

An editor saving on a timer writes the same event forty times in a quarter of an hour. A window read every fifteen seconds writes sixty readings of one page. None of that is forty or sixty facts about the afternoon, and the step was being charged for every copy of it.

So the feed is **folded before anything is spent on it**: events with the same shape - the same application, the same kind of event, the same file or page - become one entry carrying **how many there were and the stretch they cover**. That is structure the model would otherwise have to infer from a list, worked out in code where it is free. Measured on a quarter of an hour of ordinary work, 6,556 tokens became 606.

A group carries **each of its own times**, not the stretch they span. A range was the first thing tried and it is an answer to a question nobody asked: `x3 over 02:20-02:21` reads identically for three clicks in four seconds and three visits spread over a minute, and those are a double-click and a person going back and forth. A dozen times are listed and the rest counted, which is enough to see the rhythm of a group.

**What folding must not do is flatten what changed.** Sixty readings of one page are not one reading: a page that was scrolled and a page that was stared at are the same row under a naive fold, and which of those happened is the one thing those readings are evidence about. So a group keeps the newest reading and **a few behind it that differ**, with the repetitions counted rather than kept - and the rest reported as a number. Identical readings are not kept twice, because they were never two things.

The budget then buys **distinct things** rather than sixty copies of one paragraph, which is the difference between a window whose shape the step can see and one whose first tenth it reads in full.

> **It says that it was folded**, and reports both numbers - what happened and what was sent. A step told "12 events" that is really looking at a fold of a hundred and forty has been given the wrong idea of how busy the window was, and how busy it was is part of the answer it is being asked for. A compression that reports only its own output is one nobody can check.

**It is a step in the trail, and the trail says code did it.** Everything else in a step-1 trail is somebody choosing something - the model deciding to search, the agent having been configured to look back fifteen minutes. Folding is neither: it is a transform, run in Python, the same way every time, with no model asked anything. That is a different sort of claim and it is labelled as one, because from a result alone the three are indistinguishable.

So the row carries **the same feed rendered the same way on both sides of it**, with the event count and the token count for each - on the line that opens each side, because "before folding" on its own says nothing about what is behind it. The row states what was saved rather than leaving two numbers next to each other for the reader to subtract, since what folding bought is the point of it. The two renderings differ in the folding and nothing else, or the comparison would be measuring the renderer. That is the whole of what a reader needs to check the compression rather than take it: what went in, what came out, and what it saved.

### An Event With No Text Of Its Own

Only the screen provider reads text. The application provider says a window came to the front and no more, so a focus event arrived as `firefox.exe application.focused, Personalized Agent` - which names the window and not one thing in it, and reads as an event that had nothing to say rather than one whose text is kept somewhere else.

The reading of that same window **is** that text, so it is lent: the newest reading of the same application and title, shown against the event that has none. **Marked as borrowed, with the time it was taken**, because it is evidence of a different kind - what the window held at another moment, which is usually the same thing and is never the same claim. A window nothing ever read stays as it was, because there is genuinely nothing to show.

### A Window That Says It Is Working Is Still One Window

The first thing folding missed was a spinner.

Two entries from the live feed, fifty seconds apart, one terminal, one session, identical but for the character at the front: `◐ Upload instruction` and `✳ Upload instruction`. A window title carries decoration that ticks while the window does not - a spinner, an elapsed count, a percentage, a step out of a total - and each tick was a new thing to be told about.

So the title is reduced to **what is left when the parts that tick are taken out**, for the purpose of deciding what is the same thing and for nothing else: the step is still shown the title the window actually had. Digits are not stripped generally, because a title is mostly names and *Chapter 3* and *Chapter 4* are two files and two afternoons.

> Measured on the live feed afterwards: four hundred real events fold to fifty.

> **A burst shares a timestamp**, and the query that reads these events ordered by time alone left the order within each second to the query plan - so "spend the budget on the most recent first" was picking arbitrarily among the readings written in the same second. Ordered by insertion as well, which is the order they were observed.

## What The Phrase Became

Removed. Search rows, the words a search was reduced to, the words that matched, their scores, the field each was found in and re-indexing all went with the library index and search (commit fcf74e1). Nothing searches the library any more, so there is no match to explain: a step's trail shows the window it was given and the items it opened by id.

## Which Word Found It

Removed, with the search it explained (see above).

### And Where, And Whose Word It Was

Removed, with the search it explained (see above).

## Four Questions, Asked Four Times

The four steps were four sections of one prompt, answered in one call.

That is one act of attention divided four ways. Four gatherings were poured into a single context and told apart only by a label, so whichever question was being answered had the other three's memories in front of it as noise. And it could not do the obvious thing: show the later steps **the states step 1 had just written in the same run**, because all the answers had to be composed at once.

Each step is now asked on its own:

- its **own question**, and not the other three
- its **own copy of the same window** of activity
- **what the earlier steps concluded** - one line each, not their whole answers, because a step reading a second prompt's worth of JSON to find one fact is back where it started
- and, for the steps after the first, **the states the first has just written**, which a single call could not show them

**A call is not told that it is one of four.** It was: the preamble said so, the instructions were headed `STEP 2 of 4`, one referred the reader to another step by number, and what earlier steps had worked out arrived as `Step 1 concluded:`. None of that is usable by a call that cannot see the list, and naming it invites answering the list rather than the job. Each call now gets its own job and the findings that already exist - *already established in this review* - with nothing in it that has an ordinal.

**The cost is real.** The preamble is repeated four times and the measured input roughly doubles. That buys three things the single call could not have: attention that is not divided, context that is not shared out, and a step that can see what the step before it decided.

**A model that is away ends the run rather than failing four times.** Asking three more times is three more failures and the same answer - and the steps are filled in before any call is made, so what every step had gathered is still there to look at.

## Running One, And Seeing All Of It

The heartbeat is the part of the agent nobody can watch. It runs on a timer, decides what is worth doing, and all that is visible afterwards is a calendar that has changed - so "why did it do that" had no answer short of reading a log.

The Debugger can run one on demand and show each step, including the calls it made:

- **the window it was given**, and **what it chose to open in full**
- **the text the model was actually given**, in full
- **the prompt it was sent and the answer it gave**, since each step now has its own
- **what came of it** - states written, contacts written, notifications added or disabled, next steps ticked - sorted back to the step that asked for it

The second of those is the one that earns the panel. What a step was *given* is already recorded and was already visible; what the model was *handed* is a different question, and the gap between them is where an answer that reads oddly is usually explained.

### Watched, Not Waited On

Four calls take about a minute between them, and a panel that shows nothing until the last of them returns looks broken for that minute - which is section 15.1's point about a request that says only "thinking".

The gatherings all finish **before** the first model is asked, so within a second or two of the button every step is showing the window it was given. What remains is four calls, and each appears as it answers: the step being asked says so, the ones behind it say they are waiting.

### One Run, Not A Description Of One

The panel is only worth having if it shows the thing itself.

The button posts to the same `run_once` the timer calls. The trigger is checked once - only the timer and the Debugger may start a full run; the notification step alone also runs from on-screen triggers (section 37) - and then written down; **nothing branches on it**. Same gathering, same folding, same prompts, same tools, same window, since the window is fixed by the last review rather than by a number a debug path could choose differently. The two genuine differences are *when* the run happens, and that whoever asked for it gets the whole report while the polled copy has the events and the context stripped.

This is worth stating because the alternative fails quietly. A debug path that assembled its own context, or asked its own prompts, would be a panel describing a heartbeat rather than showing one - and it would drift in exactly the direction that keeps looking fine.

### Numbered, So A Reader Knows Where They Are

The four steps were numbered and everything inside them was not.

A step holds several sections and a trail that can run to a dozen rows, so somebody scrolling met *"the context, as it stood at the end"* with nothing saying which step it belonged to or how far through it they were - and the four numbers they had been following were by then far above. Each section is numbered within its step, `1.1` to `1.7`, which carries both facts in the space of a heading. A label nested inside a section is not numbered, because it is not one.

**And a section the step has not reached does not report itself as reached.** The sections are stages, so a step on its second round has not arrived at most of them - and they were filling themselves in anyway. The summary said *"the model chose what to look at, asked for more twice, and wrote the answer"* while it had written nothing, which is not a summary of what happened but a guess at how it will end; the context was labelled *"at the end"* of a step that had not ended, beside a figure still growing. While a step is unfinished, the stages it has not reached say so and show nothing - including the context, which is not worth a running total: it changes with every round, so the number is out of date by the time the line it is on has been read.

**A step whose turn has not come says only that.** All four are gathered for before any of them is asked, so steps two, three and four had a context, a token count and a trail before the first was touched - and sat there reporting what they were holding while step one was still on its first round. They run one after another; until its turn comes, a step has one thing to say.

**Every row of a trail prints what it cost**, in time and in tokens. Without it a trail is a list of things that happened with no way to weigh them: the folding and a model call read alike, and somebody looking for where a minute went has nowhere to look. The zeros are printed too - a tool call spends no tokens, the folding spends none, the floor is a database read - because that is the fact rather than a gap in the measuring, and it is the fact the row exists to establish.

> A call's row is written before the call it stands for, so what it cost is filled in when the answer comes back. The row that ends a loop is the moment it stopped rather than a cost of its own: the answer arrived in the call above it and is charged there, not twice.

**And a step that has finished always prints what it cost**, including when that is nothing. A step that made no call spent no tokens and no time waiting for one, and a blank where the number goes reads as a step that was never measured - which is the one thing it must not be confused with.

**Nothing that happened inside the loop is shown above it.** No step is handed a context gathered from a query built before it ran: each starts from its window, and what it chooses to open is chosen during the loop, one row each, which is where it happened.

**The two levels are told apart by looking, not by parsing the number.** `1.2.7` and `1.3` are four characters apart and one of them is a step further in while the other is a step back out, so a reader meeting `1.3` after a trail has to work out which. The headings sit at the margin; everything belonging to a heading is indented under it behind a rule, and the outdent is then visible rather than inferred.

**Including the trail's own rows.** Two numbering schemes were running down the same column - the sections, and the round each call is on - so *"answer round 3"* sat directly above *"1.3"* and the two read as one counter disagreeing with itself. Every row of a trail carries its place inside its section, `1.2.1` to `1.2.7`, so the only numbers on the page are one sequence and the section after a trail follows its last row.

**And the numbers are a sequence.** They were not: the list opened with a summary of who decided what, then the gathering, then the context - which is what the gathering produced - so the numbers counted up while the story went back and forth. They now run in the order the step ran: the window it was given, what it did round by round, what that left it holding, the call, and what came of it. The two summaries are summaries, so they come after the thing they summarise and say that they are ones.

> The tab is **Heartbeat debugger**, not Heartbeat. Beside the user's own tabs - Overview, Calendar and the rest - a tab called Heartbeat reads as another of those rather than as a tool for looking at one.

### A Page Of Its Own, That Waits To Be Asked

Two things followed from putting this inside the Debugger, and both of them got worse as it grew.

**It is a page, not a panel.** Reading one run means reading four prompts, four contexts, every event behind them and the trail of what each step decided - and it was sharing a scroll with the timeline, the states and the token usage. So it is a tab beside the Debugger rather than a panel within it. A build without the Debugger has no heartbeat page either: it is the same kind of door, and removing one while leaving the other is how a build ships an internal tool it meant to leave out.

**And it stopped refreshing itself.** Watching the run meant asking for it twice a second for the length of a run, and every answer now carries every event every step has read. That is a minute of the interface sharing its connection with the largest request it makes, which is what turned watching a heartbeat and using the Overview into two separate sittings.

The run is still watchable - a half-finished one comes back with the steps it has answered, and there is a button that asks. What is gone is the page deciding for itself when to want it. **It says so on the page**, because a run that is still going and a run that has stalled look identical on a display that does not move.

### What The Gathering Did, Round By Round

Removed. The gathering trail - the plan, a search per folder, the fallback, the ranking, and taking or stopping against a budget - went with the library index and search (commit fcf74e1). A step now reads its window and opens items in it by id, and its trail shows exactly that.

**A step that is still going is not a step that concluded.** Almost everything shown about a step is written as a conclusion - *it did not search*, *nothing came of it*, *what it did* - and a step being watched has not decided yet. The state step read "it did not search" right up to the moment it searched, which is most of the minute somebody is actually looking at it. While a step is unfinished it says so and draws no conclusion.

**The steps are filled in before the model is asked**, so a run the model refuses still shows each step's window rather than an error and nothing - which is most of what anybody opening this panel wants to see.

### What A Step Could Have Done

An outcome shows what happened, and cannot be read for what did not.

"Nothing" covers three different things: a step that considered something and decided against, a step that had nothing to decide about, and a step that was **never able** to do the thing the reader had in mind. The third is the common one - somebody looking at the state step for a notification is looking at the wrong step, and an empty outcome does not tell them so.

So each step lists **everything it is able to do**, with how many of each it did, including the zeroes - and ends with what it cannot do at all, because that is the question an empty list actually raises. The state step writes states and nothing else; notifications belong to one step, and only that one.

**The things that arrive in one list are counted apart.** The notification step's two acts - adding a notification and disabling one whose thing is already done - are counted apart, because *3 actions* answers neither of the questions anybody has about them. Each step's list is fixed: the state step writes states, the people step adds or updates contacts, the notification step adds or disables notifications, and the to-do step ticks next steps. No step judges states.

**And "nothing" is said in the step's own terms.** One sentence was shown for all four - *"Nothing. Deciding against is an outcome - interrupting has a cost."* - which is true of the step that interrupts and of no other. A step that writes states was explaining itself by the cost of a notification it is not able to send. Each now says what nothing means where it stands: no state written, no one in this window, nothing worth interrupting for, no next step finished.

### Who Decided, And Why

Two kinds of trail now run through the same panel and read identically.

No step chooses how far back to look or which folder to search: the window is fixed by the interval and the last review, and there is nothing to search beyond it. What every step chooses for itself, during the run, is which items in the window to open in full. From the results alone a choice and a setting read as the same list, and *why did it look there* has a different answer in each case: one is a decision to examine, the other is a setting.

So **every row says when it was decided and by what** - `the model chose, during this run`, `fixed before the run` with the name of the setting that fixed it (the window), `code, no choice and no model` (the folding) - and **each step says how much of it was the model's**.

The middle label said "the agent chose", which was worse than vague: the model is part of the agent and so is the code, so it named the thing all three have in common. The axis that distinguishes them is **when the decision happened and whether anything could have gone otherwise** - during the run by the model, before it by a setting, or not at all. And a row that was fixed in advance **names the setting**, because "the agent chose" is not something a reader who thinks it is wrong can act on. That last part is not the same as the rows, because "the model did this step" is true of all four and distinguishes none of them: what differs is whether the model chose what it was looking at or only what to say about it, and a reader should not have to reconstruct that from a list.

**And what the model said when it asked.** The arguments say what it wanted; they do not say why it wanted it. A list of ids opened is a list, and the question a reader has is what made those the ones worth reading - so whatever the model wrote in the same turn as the call is kept beside it. That sentence is the only account of the decision there is.

### The Events, Not How Many There Were

The trail said `12 events`, in a panel whose whole purpose is to say what the step read.

The events are the answer to almost every question somebody opens this with, and a count is the one part of them that answers none. Each is now shown as itself - the time, the application, the window title, the whole address, and what it said - with the text of each capped, the list capped, and the remainder reported as a number rather than dropped silently.

**The states a later step starts from are the same failure one line down.** The state step starts from no states; the later steps start from the ones this review has just written, and a count of them is a count of the thing the step was reading. Each is named, and each says **how long it runs** - a state of forty minutes and one that has held for a fortnight are different claims about the user, and a title says neither. Each opens the card the Debugger's states grid opens: what it is, its period, whose it is, where it came from and on what judgement the agent believes it. The question a reader has here is the same question, so it is the same card rather than a second one that drifts.

> This is most of a run's size, so it goes to whoever asked for the run and not into the status polled every few seconds - the same split the context already has. The rows keep their shape either way, so nothing downstream has to know which copy it is holding.

> Step 1's floor is timed like any other gathering, although it is not a search. The call it feeds is charged against that row, and without it the step's tokens were attributed to nothing and the heartbeat reported three gatherings where it had made four.

> The text of one step runs to tens of thousands of characters. It is shown in a window that scrolls and opens out in place, because scrolling a window is not the same as reading a page.

> The context of four steps is tens of kilobytes of record text, and the status carrying the last run is polled every few seconds by the interface. Whoever asked for the run gets the text in the answer to that request; the polled copy carries everything else about the run and none of the text.

### Kept, Not Redrawn From Nothing

The run was held in the page. Reloading the Debugger threw it away, and the panel came back empty with its button waiting to be pressed again - which is four model calls and a minute to see something that had already been worked out.

What was lost is the part that cannot be reconstructed by looking anywhere else: the prompts, and the text each step was handed. So the agent keeps the last run whole, and the panel asks for it when it opens. A reload now costs a request rather than a run.

The two readers stay two readers. The status polled every few seconds is still the light copy without the text (above); the whole one is fetched once, by a panel that is being opened. Before anything has run, that request answers that nothing has, rather than failing.


*Amended 2026-10-18:* TASKS AND PROJECTS, MADE PER RUN (section 34.5 in the code, `heartbeat/states/task_projects.py`, `project_round.py`). The grouping of states into tasks is replaced, the user's decision, measured first on a labelled week. EACH RUN that finds states written since the last one - twelve hours on the schedule, sooner when run by hand, none when nothing is new - makes ONE model call over them: the states are written down as TASKS, the distinct things the user did in that stretch, each with a title and a summary in English and a copy in the display language, its kind (work, leisure, or routine - kept, and grouped into nothing), the states it holds, how long it was actually worked on (the states' own minutes, each held to its span and the stretch's together to the stretch, scaled down alike when over) and where (its folders and pages). The writer is shown the user's PROJECTS - those with a task in the last seven days - each as one line saying what it IS, one level above any occasion, and may say a task continues one: when it serves the same thing - the same paper, the same product, the same application - whatever the activity, writing, submitting, presenting or discussing it; a shared topic, field or site is not continuing. The examples it is given are made up: one taken from the user's own subjects is an answer, and voided a measurement. PROJECTS are the week's tasks grouped by one partition over two layers - what the tasks mean (cosine of their English text, from 0.6) and what the writers said they continue - in an implementation of our own (the usual libraries are GPL). A project keeps its id while its main body goes on, measured in WORKED MINUTES, not in counts: two substantial parts (a fifth each) of different projects coming together is a merge, one coming apart in two is a split - each a new project, the old ones ended and pointing at where their work went - and a small piece coming or going changes nothing. A project's line is written when it is new, merged or split; its title is its most typical task of its latest run. A project with no task in seven days is dormant and is not continued: new work on the same thing later is new. No hierarchy and no pieces inside a task: the partition is flat. The projects are rows of `state_tasks` marked with this scheme, so Learning, Work, the English copies and the rest read them unchanged; the tasks are rows of `state_run_tasks`. The path of a task's folders was measured as a third layer and left out: one research folder pulled unrelated work together. Measured on the labelled week, written stretch by stretch as the runs would: 24 projects, the paper and its talks in one, the big subjects clean. THE SWITCH is on for everyone, this machine included: the first run marks every task and project of the earlier scheme as history - their content and their states untouched, never regrouped - and writes the last week in twelve-hour stretches, one after another, so the projects are there from the start; any state is enough to begin (the wait for twenty is gone). The Overview draws only the projects, as the tasks were drawn - a row each, with bars where it was worked on - with how long each was worked on, filtered by days and by at least 15 minutes, an hour, six hours or a day, longest worked on first, and its leisure ones shown only with full activity, under the work and headed apart from it; a project opens onto its card - what it is, what is being worked on in it, since when, where - not onto a list of its tasks (a project of one task is that task); the debugger's task panel shows each stretch written, with what was sent and what came back, and each project as a row over time with its tasks along it.

*Amended 2026-10-19:* HOW A PROJECT CARRIES ON, the user's decision after a read-only replay of the week. THE WINDOW is no longer seven days but the latest tasks until 48 HOURS OF WORK (work and leisure), however many days that takes - days the computer was off do not count; its tasks are grouped again each run, the writer is shown the projects in it, and a project with no task in it is dormant. Over the window's tasks only, each earlier project is CARRIED ON by the new project that took 70% of its worked minutes, or by none: the one carrying it on takes all its tasks, those before the window too, so the Overview draws its history as its own, and takes its stages, its cards and which of its states were already read, and goes on learning from there. The new project keeps the id of the one it carries on that is also 70% of what it was made of - its tasks grouped before, not the new ones - and is the same project, renamed when it has grown; otherwise it has a new id, carrying on what it carries on all the same. An earlier project carried on by none ends: its tasks before the window and what was learned from it stay its own, as history, and the Overview still draws them; a new project carrying none on is learned from the start. A split is nothing of its own: neither half took 70%. NAMES are the model's, not a task's title: a project is named - a short title and the line saying what it is - when it is new, and again when its worked time has grown by 30% and by half an hour since it was last named, never otherwise; all the names a run needs are asked in the call that writes its tasks or in one call after them (twenty a call), and the names a project had, and those of the ones it carries on, are kept and shown in Work. A learning queued for a project that ends while it waits ends quietly: nothing is learned in its place, and the one carrying it on is learned when a run finds it changed.
<!-- note: 2026-10-19: the project grouping now carries projects on by 70% of their time over a 48-hour-of-work window; ids, stages, the Overview's history and names follow that rule; a learning whose project ended while it waited no longer fails. -->

*Amended 2026-10-19:* A PROJECT COMING BACK. Work picked up again after days away is not in the window, so nothing carries it on there. So every project named or renamed in a run is looked for among the dormant projects and the ended ones that kept their history, of its kind (work or leisure): its English name and line against each of theirs and each name they had before, from a cosine of 0.55, the ten most alike - measured on the labelled record, named blind: from 0.55 the subject it came back to was among them 35 times in 40 by day and 5 in 8 by project, with a fifth of a wrong one beside it; shared folders found nothing the meaning did not, and are not used. One call shows the model the new projects and these look-alikes, each with some of its tasks, and it says which earlier one each new one IS - one at most, and none for a project that already kept an earlier id - and which it CARRIES ON, any number. An earlier project goes to one new one, being the same before carrying on, then the most alike. Being the same revives the earlier id - the new project's tasks, and what it carried on, go to it, and its learning and its setting go on; carrying on takes the earlier project's tasks, its names and, when learning next follows the tasks, its stages and what of it was read - to the end of a chain, when the one it went into went on into another. Each link keeps the model's reason, shown in the debugger.
<!-- note: 2026-10-19: projects named in a run are matched against dormant and ended ones (cosine 0.55, model decides same / carries on). -->

# 38. Data Management
<!-- number: 38 | label: 38. | id: s38 | page: 86 -->

Location:

```
./data_manage/
```

What the agent keeps lives in two places: the raw activity the listener and the agent record, held in the local database for a few days (section 48.2), and a small library of files under the user's workspace (section 39.1). The directed knowledge graph this section first described is kept, unused, in deprecated/.

# 39. Knowledge Components
<!-- number: 39 | label: 39. | id: s39 | page: 87 -->

Superseded by section 39.1. A memory is now one file in one of the library's folders, not a graph node. The list and the schema below are kept as the record of the original design.

A Knowledge Component may represent:

- Raw activity
- User behavior
- Agent activity
- Conversation
- Task
- Application usage
- Aggregated information
- Observation
- Summary
- Project information
- User-created information
- Agent-created information

Recommended conceptual schema:

```
KnowledgeNode:
    id
    type
    title
    summary
    content
    created_at
    updated_at
    source
    metadata
```

The exact schema should remain extensible.

# 39.1 The Library: Five Folders of Files
<!-- number: 39.1 | label: 39.1 | id: s39-1 | page: 87 -->

What the agent knows is kept as **files in folders**, under the user's workspace, where the user can read them and correct them.

```
data/
  user_nature/      who the user is
  user_feedback/    what they thought of what the agent did
  states/           what was true over a span
  state_tasks/      what a run of states adds up to
```

One record is one file: a short front matter block, and then the text. The folders `others/` and `agent-lesson/` are names only now - nothing writes or reads either any more - and an `index/` directory is still created empty, but nothing builds or reads an index. Nothing searches the folders as a whole: a record is read by its id, or a folder listed, by whatever wrote it.

**Every record says where it came from.** A record without that is an assertion with nothing behind it, and putting the agent's memory in files that anyone can open fixes nothing on its own if the files do not say where their contents came from - that was the graph's structural failure, not an accident of its storage. Three different questions, so three fields:

| | |

|---|---|

| `source` | which part of the system wrote it - the agent heartbeat, the state heartbeat, the feedback service, the user |

| `references` | which particular things it was drawn from, concretely enough to go back and look: a file path, an application, a URL, the id of a raw event, a task or question id |

| `created_at`, `updated_at`, `observed` | when it was written, when the file last changed, and what stretch of activity it is about |

The last of those three is not one fact. When something happened and when it was written down are different, and a round can write about yesterday - so a record that carries only the moment it was created is a pile of undated statements in which "the deadline moved" from August and from September are indistinguishable, both sounding true and one of them wrong.

Whatever writes a record fills these in, and where a model writes one, the round adds its own pointers on top of whatever the model cited - so a record whose model forgot to cite anything can still be traced back to the activity it came out of.

**Each folder carries an ``intro.txt``** saying where its contents come from and what belongs in it, so that opening the folder explains it. This is not decoration: a folder of four hundred files explains itself no better than a database did. An intro is written when it is missing and never overwritten: a user who rewrites one has said something about how they want that folder used.

**The parser is forgiving, because a file the user edited by hand is the normal case.** A missing field, a stray line, a key they invented - all are survived. A file with no front matter at all is still a record: its first line becomes the title. Refusing to read a note somebody dropped into a folder would be the wrong answer to a reasonable thing to do with a folder.

What made the graph a graph is not carried over:

- Its weights, its evidence and its twelve probabilistic walks are gone. A number nobody can check, that decides what the agent remembers, is worse than no number

**A record is not rewritten for convenience.** The exceptions are a state, which is rewritten while it changes (section 34.2), and a nature, which gains examples as the same trait is seen again and can be merged with another nature that turns out to be the same trait.

# 39.2 Lessons the Agent Writes for Itself
<!-- number: 39.2 | label: 39.2 | id: s39-2 | page: 87 -->

Removed. Nothing writes or reads `agent-lesson/` any more, and the chat has no memory tools, so the tool that wrote a lesson is gone. What the agent learns is now held by the lessons engine, which learns only from the user's own actions and covers notifications, connections and the natures of the user's work.

# 40. Knowledge Graph Edges
<!-- number: 40 | label: 40. | id: s40 | page: 88 -->

Removed. Graph edges went to deprecated/graph/ with the rest of the knowledge graph (commit a4d08c6), when the library of files (section 39.1) replaced it. Nothing in the application creates or reads an edge; the old `nodes` table survives only as schema.

# 40.1 A Weight Is a Likelihood, Not a Rating
<!-- number: 40.1 | label: 40.1 | id: s40-1 | page: 88 -->

Removed. Edge weights went with the graph (commit a4d08c6). Nothing in the application weights, judges or reads an edge.

# 41. Graph-Based Context Retrieval
<!-- number: 41 | label: 41. | id: s41 | page: 89 -->

Removed. Retrieval by graph traversal went with the graph, and since commit fcf74e1 (2026-10-06) nothing retrieves over the library at all.

# 41.1 Searching, and Searching Again
<!-- number: 41.1 | label: 41.1 | id: s41-1 | page: 89 -->

Removed. Nothing searches the library as a whole (commit fcf74e1, 2026-10-06). The chat answers from what it is asked and the conversation so far; the scheduler runs notifications and gathers nothing; the agent heartbeat reads its own window of activity and opens anything in it by its id.

# 41.2 The Agent Searches For Itself
<!-- number: 41.2 | label: 41.2 | id: s41-2 | page: 89 -->

Removed. The chat has no search tool and gathers nothing before the model is asked. The floor, searching under several phrasings, summary expansion, rare-term weighting and the setting that turned agentic search on all went with the library search in commit fcf74e1.

# 41.3 What Is Retrieved Together, Counted Before It Is Used
<!-- number: 41.3 | label: 41.3 | id: s41-3 | page: 88 -->

Removed. Counting what was retrieved together went with the data heartbeat that ran it and the usage log it read (commit fcf74e1). Nothing is retrieved, so there is nothing to count.

# 42. Retrieval Lifecycle
<!-- number: 42 | label: 42. | id: s42 | page: 89 -->

Removed. There is no graph and no traversal (commits a4d08c6 and fcf74e1), so there is no retrieval lifecycle. What each part of the agent reads is described where it is used: the chat in section 67, the agent heartbeat in sections 36 and 37.

# 43. Global Graph Escape
<!-- number: 43 | label: 43. | id: s43 | page: 90 -->

Removed, with graph traversal (see section 42).

# 44. Retrieval Budget
<!-- number: 44 | label: 44. | id: s44 | page: 91 -->

Removed, with graph traversal (see section 42). There is no traversal to budget.

# 44.1 Walking the Graph, and Learning from It
<!-- number: 44.1 | label: 44.1 | id: s44-1 | page: 92 -->

Removed. The nine probabilistic walks, the state-first gathering, the learned weights, and the judgement and shortening passes done in the data heartbeat went with the graph and the data heartbeat (commits a4d08c6 and fcf74e1). Nothing gathers before every model call any more: the chat is answered from the conversation alone, the scheduler runs only notifications, and only the agent heartbeat reads context before it asks - its own window of activity.

# 44.2 A Walk Is Recorded Whole or Not at All
<!-- number: 44.2 | label: 44.2 | id: s44-2 | page: 92 -->

Removed. No walks are made and no round judges them, so there is no trace of walks to record. The general rule behind it - bound the data, not its serialised form - still holds wherever a record is cut to fit.

# 44.3 An Answer Shows What It Rests On
<!-- number: 44.3 | label: 44.3 | id: s44-3 | page: 88 -->

The chat no longer answers from memory: it is answered from the instruction and the conversation it was asked in, so an answer has nothing under it to cite, and there is no "built from" panel and no citations in a reply.

The card described here lives on in the Debugger. A record or a state named in a heartbeat's trail opens a card beside it showing what it says, when it was written, what it was read from and, where the trail recorded them, the words that found it on that occasion. The card shows the record's title and, quietly, its id, because a title is not a handle and a reader who wants to come back to a record needs something to copy. It shows the text itself, scrolling rather than cut and opened at the first marked word, because the question a card is opened with is usually about a word in the body. What the record was read from is laid out as labelled facts, with the raw items it was built from counted rather than listed. A line about a folder or a window with nothing behind it is not a button.

## A Call That Left Something Out Says So

A tool schema marks arguments required, and under a text protocol that is a description of what should arrive rather than a check on what did.

The client has no tool protocol of its own here, so a call is whatever the model wrote, and the required list is something it may simply not have followed. A call arrived with no id; every tool met the absence in its own way; and what reached the user was `expand_memory finished {error: no record None}` (from a memory tool since removed) - naming neither the tool, nor the argument, nor what to do next, and reading like a memory that had gone missing rather than a call that was never complete.

So the check is made once, where calls are dispatched, and the answer names the tool, the argument and the next step. An argument supplied empty counts as left out, because a tool that takes an id can do nothing with `""` and a search given an empty list has not been asked anything; a number or a boolean is never empty, since `0` and `false` are answers. What a tool requires is read from its schema rather than declared again, so a tool added later is covered by having a schema rather than by remembering to check.

It answers in general terms - which tool, which argument, call it again - because most tools met a missing argument however they happened to. A tool that refuses in its own words says more than that: scheduling explains what each field is *for* - what tells a task from every other one, whether it interrupts - and the general answer would replace that with less. Such a tool says so where it is registered, and is left to refuse. The default is the general check, so the cost of forgetting is a plainer message rather than no message.

Where a tool takes **either** of two arguments, the schema cannot say so and the tool must say it itself. The case that taught this was the chat's memory search, which accepted a list of phrasings or a single one and, called with a limit and nothing else, answered *"nothing matched - try other words"* to a question that was never asked. That tool has since been removed with the chat's other memory tools, and no chat tool now takes either of two arguments.

An answer that says what was actually wrong costs one line and ends it.

## A Progress Line Says What Is Being Asked

The line under a reply exists to tell the reader what the agent is doing while they wait.

It showed the first two arguments in the order they were written, and that order is not reliably what a call is *about*: a limit is how much of something, and the reader cannot see the something. Arguments that name the subject - the phrasings searched for, what was asked about, which record - are shown first, and a list reads as its words rather than as a list. A call carrying nothing but a limit still shows the limit: the line reports the call that was made, including when that call was a poor one.

# 45. Raw Events and Raw Edits
<!-- number: 45 | label: 45. | id: s45 | page: 91 -->

The system distinguishes between:

## Raw Event

Primarily produced by Listener components.

Represents observed external activity.

## Raw Edit

Records what the agent did: an instruction received, a tool called, a model invoked, a task scheduled or run, a heartbeat run.

Examples:

- Information accessed
- Agent action
- Task execution
- File modification
- Scheduler modification
- Model operation

Raw events and raw edits are read as they are - by the agent heartbeat, the Overview and the Debugger - and are removed by age (section 48.2). Nothing turns them into memories.

# 46. Raw Edit Schema
<!-- number: 46 | label: 46. | id: s46 | page: 92 -->

Recommended conceptual schema:

```
RawEdit:
    id
    timestamp
    task_id
    source
    event_type
    instruction
    accessed_information
    action
    result
    related_nodes
    metadata
    processed
```

The format should be optimized for cheap and reliable foreground writes. `processed` and `related_nodes` are kept in the table but are no longer used: nothing processes raw activity into records.

It does not need to be fully semantically organized immediately.

# 47. Agent Activity Recording
<!-- number: 47 | label: 47. | id: s47 | page: 93 -->

Every meaningful agent action should be recorded.

Examples:

- User instruction received
- Files read
- Files created
- Files modified
- Scripts generated
- Scripts executed
- Scheduler tasks created
- Scheduler tasks edited
- Agent responses
- Heartbeat actions

These records provide:

- Traceability
- Debugging
- Future learning

# 48. Data Management Heartbeat
<!-- number: 48 | label: 48. | id: s48 | page: 93 -->

Removed. The data heartbeat (./data_manage/data_heartbeat/, every five minutes) was deleted in commit fcf74e1 (2026-10-06), with the indexer, compaction, folding and relabelling. Its one remaining job, removing old raw activity, is done by a small housekeeping loop that first runs five minutes after start-up and then every six hours (section 48.2).

# 48.1 Reading, Interpreting, and the Difference
<!-- number: 48.1 | label: 48.1 | id: s48-1 | page: 90 -->

Removed. Raw events are no longer read into library records, by template or by model, and there is no index: the reader and the indexer were deleted with the data heartbeat in commit fcf74e1. Raw activity is read as it is, by the agent heartbeat, the Overview and the Debugger.

# 48.2 Raw Activity Is Material, Not a Second Copy
<!-- number: 48.2 | label: 48.2 | id: s48-2 | page: 90 -->

Raw activity is **material**, not a memory: a state copies what it relies on when it is written, so the raw rows behind it are needed only for a while.

- Raw activity is kept for a set number of days, eight by default, and then removed by age, whether or not anything was made from it
- Only the agent heartbeat (its last few hours), the Overview (the last day and the past week) and page visit history read it, so older rows are read by nothing
- File events that were never anyone's activity are swept out as soon as they are noticed
- The window is a setting, and setting it to zero keeps everything

# 49. Data Heartbeat Workflow
<!-- number: 49 | label: 49. | id: s49 | page: 94 -->

Removed. The data heartbeat round was deleted in commit fcf74e1 (2026-10-06), and the graph it updated is kept, unused, in deprecated/.

# 49.5 Every Round Writes New Nodes
<!-- number: 49.5 | label: 49.5 | id: s49-5 | page: 94 -->

Removed, with the data heartbeat (see section 49). No round writes nodes.

# 49.6 What a Round Is Offered to Connect To
<!-- number: 49.6 | label: 49.6 | id: s49-6 | page: 94 -->

Removed, with the data heartbeat and the graph (see section 49). No round draws edges.

# 49.7 The Index Is Built Here
<!-- number: 49.7 | label: 49.7 | id: s49-7 | page: 94 -->

Removed. The library index - the term index, the per-folder listings, the resident index, the stat guard and the splitting of compound terms - was deleted with the search in commit fcf74e1. The `index/` folder is still created empty, but nothing writes or reads it.

# 50. Data Compression
<!-- number: 50 | label: 50. | id: s50 | page: 94 -->

Removed. Graph compaction went with the graph (commit a4d08c6), and the library's compaction was deleted in commit fcf74e1.

# 50.1 Fitting a Node as It Is Written
<!-- number: 50.1 | label: 50.1 | id: s50-1 | page: 94 -->

Removed, with compaction (see section 50).

# 50.2 Folding What Has Gone Cold
<!-- number: 50.2 | label: 50.2 | id: s50-2 | page: 94 -->

Removed. The index ceiling, folding cold records into summaries and the fold queue were deleted in commit fcf74e1, and the "Data management" settings group is now empty. A record still carries the `level` and `covers` fields that folding wrote, so older files still load.

# 50.3 Being Used Brings a Record Back
<!-- number: 50.3 | label: 50.3 | id: s50-3 | page: 94 -->

Removed, with folding (see section 50.2). Nothing is folded, so nothing needs bringing back.

# 50.4 A Summary Is an Index
<!-- number: 50.4 | label: 50.4 | id: s50-4 | page: 94 -->

Removed, with folding (see section 50.2). A record still parses a summary's own index fields, so older files still load, but nothing reads them.

# 50.5 How Far a Retrieval Will Walk
<!-- number: 50.5 | label: 50.5 | id: s50-5 | page: 94 -->

Removed, with folding and retrieval (see sections 50.2 and 42). There is no walk to limit.

# 51. Data Heartbeat Invocation Restriction
<!-- number: 51 | label: 51. | id: s51 | page: 95 -->

Removed. There is no data heartbeat to restrict (see section 48).

# 51.1 Keywords, Maintained Each Round
<!-- number: 51.1 | label: 51.1 | id: s51-1 | page: 99 -->

Removed. Neither the data heartbeat nor any walks remain, so no round revises keywords to choose where a walk begins. Records still carry a `keywords` field.

# 51.2 The Interval Is How Often to Look, Not How Often to Run
<!-- number: 51.2 | label: 51.2 | id: s51-2 | page: 99 -->

Removed. The data heartbeat's backlog loop, its five-minute idle check and the rule that an excess must shrink went with it in commit fcf74e1. The agent's own review keeps its own interval, fifteen minutes by default: its steps - states, people, notifications and, when there are open next steps, the to-do step - are asked one at a time, each with its own question, and the first is a loop that may ask for more up to six times, so a run is several model calls. That is the setting that decides most of what the agent costs when nobody is talking to it.

# 52. Scheduler
<!-- number: 52 | label: 52. | id: s52 | page: 95 -->

Location:

```
./scheduler/
```

The Scheduler keeps the user's notifications: reminders and notices that appear at a set time. It runs nothing else. No script, agent run, model task or check can be put on the calendar, and a task of any other kind that turns up there is cancelled with a note saying why, not run.

Conceptually, it executes a scheduling loop approximately once per minute.

# 52.1 A Task Says What It Is
<!-- number: 52.1 | label: 52.1 | id: s52-1 | page: 101 -->

Every task the agent creates carries a title and a detail, written for the user, or it is not created. The user's own calendar form needs only a title: the event is created at once, and anything the user left blank is filled in afterwards (section 60.1).

The title is what the user sees in a list beside everything else scheduled. The detail is what will happen when it runs and why it was scheduled. A task with neither is one the user cannot tell apart from any other, and cannot decide whether to keep.

## The Detail Is the Plan; the Reasoning Goes Somewhere Else

Asking one field for both the plan and the reasoning was a mistake, and the agent answered it exactly as written. Real descriptions ran from 488 to 905 characters - the evidence, the prior, what a negative result would rule out, what other task depended on the answer - written agent to agent:

> Runs tomorrow at 18:15Z, 45 minutes before the afternoon-gap test. Compares the day's activity envelope against Lisbon and Madrid boundaries. Narrowed from an open investigation to a confirmation: a +0100 offset has already been observed on 3 Sep [...]

Read in a calendar, that is not a task. It also opens by restating the scheduled time, which the calendar is already showing.

The two are therefore separate fields:

- **description** - what will happen when it runs, in plain sentences. This is what the calendar shows. It carries a ceiling of 2000 tokens, which is a stop against a runaway rather than a style rule: what keeps the detail readable is knowing that the reasoning has a home of its own, not a tight character count.
- **rationale** - why the agent scheduled it, and on what evidence. As long as it needs to be, stored with the task and shown folded away, so the user opens it only when they want it.

The reasoning is worth keeping. It is the same material section 34.1 keeps as an inference trace for a state, and for the same reason: the user should be able to ask *why do you think that* and get a real answer. It just is not the thing they open a task to find out.

## A Placeholder Is Worse Than a Refusal

Substituting "Scheduled task" for a missing title does not rescue the call. It produces an entry nobody can identify, in a list where every such entry looks the same, and from that moment the mistake is invisible: nothing distinguishes a task that was named badly from one that was never named at all.

- A creation by the agent's own tool without a title or a detail is refused, and the refusal says what was wanted, so the caller can supply it
- The user's form needs only a title. An event the user creates is never refused for leaving the other fields empty; they are filled in afterwards, so the calendar still shows no placeholder
- The refusal reaches the agent as an ordinary tool error, which it can act on. It is not a crash and it is not silent

## The Detail Is Shown, Including When It Is Missing

Where a task is displayed, its detail is displayed with it.

An empty detail rendered as empty space is invisible, and that is precisely how a task that says nothing about itself goes unnoticed. Where a task has no detail, the interface says so in as many words rather than leaving a gap that reads as though nothing was omitted.

# 52.2 Scheduling Is Not Interrupting
<!-- number: 52.2 | label: 52.2 | id: s52-2 | page: 101 -->

Putting something on the calendar and interrupting the user are two decisions, and a task states them separately.

- A **silent** notification only leaves its message on the calendar when its time comes. The user reads it when they choose. This costs them nothing at the moment it runs.
- An **interrupting** notification takes the user away from what they are doing. It has to be worth that.

Everything on the calendar is now a notification for the user; `notify` says which of the two it is.

Every task therefore carries an explicit choice, `notify`, and it is required. There is no default, because a default is how the question stops being asked: an agent that is not made to answer it will let the flag sit wherever it was initialised, and on real data that meant every task interrupted.

## Silence Is the Resting State

The agent no longer queues checks or any other work for itself: only notifications can be scheduled. The agent heartbeat's notification step proposes the notifications to add and the ones to switch off, and is shown the user's notification lessons when it does so. A task run by the scheduler is not given the tool to schedule more.

`notify` is justified when the answer is something the user would want put in front of them at that moment, and would be annoyed to have missed. A recurring break reminder qualifies. "Did the build advance" does not.

## A Notification Obeys the User's Settings

A scheduled task's notification is an ordinary notification. It respects the global setting, the quiet hours, and the duplicate suppression, like every other.

Forcing delivery past those is reserved for the cases where the application cannot function until the user acts - an expired sign-in, and errors of that kind. A task the agent scheduled for itself is not one of them. Using the same escape hatch for routine output makes the user's own preference decorative.

## The Choice Is Visible and Reversible

Which tasks will interrupt is shown on the calendar itself, not only inside each task, because that is the property the user most wants to read off a calendar somebody else filled in. Opening an event states it in words - it interrupts you, it stays on the calendar, or it does not run because the user disabled it. The card can edit the event (title, message, time, repeat), disable it or delete it; whether it interrupts is no longer switched from the card.

A setting the user cannot see is not a setting; it is a behaviour they have to reverse-engineer from being interrupted.

## Tasks That Predate the Choice

Tasks scheduled before `notify` existed do not carry it, and must not simply fall to the new default: that would silently switch off the ones that exist in order to interrupt. Each is decided once, on the best evidence available - a task whose whole purpose is the notification keeps notifying, and the rest become silent, which is what the agent was already trying to express through the payload keys nothing read.

# 52.3 Repeating Tasks
<!-- number: 52.3 | label: 52.3 | id: s52-3 | page: 101 -->
<!-- note: 2026-10-20: a time is kept on a clock by its zone's name and its moment worked out by code, daylight saving included; 2026-10-21: a zone the model writes must be a place's name, and the chat gives its times the same way. -->

A task repeats on an interval and stops on a limit. The rule lives in one field, `recurrence`, and is empty for a task that runs once - which is what every task was before this.

```
{"every_seconds": 2700}                       every 45 minutes, indefinitely
{"every_seconds": 2700, "count": 8}           eight times, then finished
{"every_seconds": 86400, "until": "2026-12-01T00:00:00Z"}   daily until a date
{}                                            runs once
```

The interval may also be given as `every` plus `unit` - minutes, hours, days or weeks - so a caller can say "every 45 minutes" without arithmetic. Where both `count` and `until` are given, whichever arrives first ends the run.

## A Closed Shape, Not Another Open One

This is a dictionary, like `task_payload`, and the difference is that its keys are fixed and checked on the way in. `task_payload` accepts anything, and across one real database it accumulated more than fifty distinct keys - most used once, none read by any code. `retry_policy` is a dictionary too, and has held exactly two keys since it was written, because nothing else is allowed in it.

A rule that cannot be honoured is refused when it is written, with the reason, rather than discovered when it fires:

- an interval below a minute is a loop rather than a schedule, and would spend the scheduler's tick on one task
- a count below one run is not a schedule
- a repeat with no interval is not a repeat

A repeat with no end is allowed, because some things genuinely do continue, but it has to be stated rather than fallen into.

## The Same Task, Moved Forward

When a repeating task finishes a run, that row is moved to its next time and set back to pending. It is not copied.

A fresh row per run would bury the rest of the calendar within a day, and each copy would begin again with none of the history: the notification choice the user made, the reasoning the agent recorded, how many times it has run. One task that has run eleven times is a thing the user can look at and judge; eleven tasks are a mess they have to reconstruct.

`runs` counts the completed runs, and is separate from the rule, the way `attempts` is separate from `retry_policy`.

## Time Is Measured From the Schedule, Not From the Run

The next occurrence is calculated from the previous *scheduled* time, not from when the task actually ran. A task that runs three minutes late would otherwise be three minutes later on every cycle, and a daily task would walk around the clock within a month.

Where the application was closed and several occurrences have passed, the schedule advances by whole intervals to the next one in the future. It does not run the missed ones. Coming back from three days away to ninety-six queued reminders is worse than having missed them: the point of a reminder is the moment it names, and that moment is gone.

## Every Run Is Drawn on the Calendar

One row is the right shape for the data and the wrong shape for a calendar. A reminder every 45 minutes has to appear every 45 minutes, or the calendar is not showing the day the user is going to have - it shows one block and leaves them to do the arithmetic for the rest.

So the calendar draws a repeating task as each of the runs it will have. The blocks are computed from the rule when the calendar asks for a window, and nothing is written down: the stored row is still the one task, and each block carries its own time, its number in the series, and the identity of the row that owns it.

Acting on a block acts on the series. There is one task here however many times it is drawn, so on the calendar Edit, Disable and Delete on the third block act on the whole reminder. A new time or a new repeat starts the series again from its next run, its count of runs restarts, and any earlier change to a single occurrence is dropped. The API can still skip or change a single occurrence, recorded as an override against the series, but the calendar does not offer it.

A run that has not been reached is drawn differently from the one that is next, because the calendar should not claim more than it knows. Only the next run is a real pending row; the rest are the rule, drawn out.

## A Repeat With No End Needs a Horizon

A repeat that never stops has no last occurrence to draw, so something has to bound the drawing. Thirty is enough to see the shape of the thing - a 45 minute repeat fills the day, a daily one fills the month - and because the window moves with the calendar, reaching the end of one batch reveals the next. There is nothing to top up and no batch to run out of, which is what drawing from the rule buys over writing thirty rows and maintaining them.

Where the repeat does have an end - a count or a date - it is drawn to that end, or to the horizon if the end is further away than thirty runs.

*Amended 2026-10-20:* A TIME IS KEPT ON A CLOCK, BY ZONE NAME, and the offset worked out for its date by code, never by the model (scheduler/core/zones.py). The notification step gives a time as a clock shows it ("at_local") and whose clock ("zone": the user's, or the IANA name of a place - a time a source gives in another place's time, or one it gives with no zone but clearly the sender's, is kept on that place's clock), with the time as the source wrote it; the moment is worked out from the zone's rules for that date, daylight saving included, because an offset taken today and stamped on a date after the clocks change is an hour wrong. A wall time the clocks skip is taken as long after the jump as it was meant to be; one that comes twice, the first time. The user's own time is the zone this computer is in WHEN THE TIME IS MADE, kept by its name - the browser's, sent with each request, while its offsets are this computer's; else one with this computer's offsets over two years - so a reminder made in Lisbon for 9:00 stays 9:00 Lisbon time, and the calendar, always in the zone the computer is in now, shows it at 4:00 in New York. A REPEAT OF WHOLE DAYS KEEPS ITS CLOCK: it steps by days on the clock of its zone - a weekly meeting at 10:00 Paris time stays at 10:00 there, and so lands an hour apart for someone in New York in the week between Europe's change and America's; a repeat of minutes or hours is elapsed time, as before, because a dose every eight hours is due eight hours later on that night too. The series grid steps the same way. The calendar card says what a time was taken from - "written as 'Thursday at 3pm', taken as Europe/Berlin time, 14:50 there" - and when the clocks change that night, so a wrong guess of the zone is seen and moved with Edit.
<!-- note: 2026-10-20: notification times are given as wall time plus zone and converted by code with daylight saving; repeats of days and weeks keep their zone's clock time; your own reminders keep the zone they were made in; the card shows what the time was taken from. -->

*Amended 2026-10-21:* A ZONE THE MODEL WRITES IS A PLACE'S NAME, AND A NOTIFICATION IS NEVER DROPPED FOR ITS ZONE. Accepted are an IANA name of the form Region/City ("Europe/Paris"), "UTC", and "user" for the user's own time. Refused are an abbreviation ("PST"), an offset, a city alone, and the old names the time-zone database also knows without a slash or under Etc/ - because some are not what a person writing them means: "EST" is UTC-05:00 all year, so "3pm EST" in July, meaning New York's time, would land an hour off unseen, and "Etc/GMT-8" is UTC+08:00. One refused is sent back to the model once, told why it was refused and what is accepted, with what the notification step was shown, to name the place; its answer is held to the same rule. Still refused, or no answer, and the time is taken as the user's own - and the calendar card says so: "the time's zone could not be read ('PST'); taken as your own time". What was written and what it was taken as are kept with the time, and in the run's report. THE CHAT GIVES ITS TIMES THE SAME WAY, read by the same reader with the same rule (scheduler/core/given.py): its schedule_task no longer takes a UTC moment the model worked out itself, but "at_local", "zone" and "as_written", and its prompt says the time now on the user's clock, by name, instead of in UTC. A zone refused there is the tool's error, with why and what is accepted - the asking once more; refused again for the same task, the time is the user's own, the card says so, and the tool's answer tells the model to tell the user. This computer's own zone is not held to the rule: it is the system's, not a guess.
<!-- note: 2026-10-21: a zone the model writes must be Region/City, UTC or "user"; one refused goes back once with the reason and the rule; still refused, the time is the user's own and the card says so - never dropped. The chat's scheduling gives and reads times the same way. -->

# 52.4 Asking the User, and Keeping the Answer
<!-- number: 52.4 | label: 52.4 | id: s52-4 | page: 101 -->

The agent schedules on its own judgement, and what the user does with what it scheduled tests that judgement.

- An **approval**, asked *before* a task ran, has been withdrawn: since only notifications run, nothing is held for one.
- The **answers** a notification carries are offered *after* it has reached the user. Nothing waits on them.

Both end in the same place: a judgement of the agent's own decision, kept where the agent will meet it again.

## Asking Before Acting

Removed. Nothing asks first any more. Since only notifications run, nothing is held for approval: `approval` is stripped from a new task, the status `awaiting_approval` is never set, and every task runs at its time. A notification the user does not want is disabled or deleted on the calendar, and that action is kept as a lesson.

## Asking After Interrupting

Every notification that actually reaches the user carries its own answers. They are offered at delivery rather than at scheduling, because until the notification has gone out nobody has been interrupted and there is nothing to judge.

A reminder offers **Got it** (no more notifications), **Remind me later** (with how long) and **Add to to-do** (no more notifications; listed in To do on the Overview). A notice offers **OK** and **Add to to-do**. Every notification also has **Bad notification**, which dismisses it and asks for fewer like it in future, and a plain notification with no question has a button to be reminded in thirty minutes. A great deal of what an agent gets wrong is not the decision but the timing, and Remind me later is how the user says so without saying the notification was wrong. Bad notification is how the user says it was not worth it: it becomes a notification lesson, so fewer like it are scheduled.

## The Answer Becomes a Memory

A judgement is only worth collecting if the agent meets it again, so an answered question is kept as a `user_feedback` record in the library, carrying three things together:

- what the task was going to do - the thing the user actually judged
- how the agent arranged it, in the agent's own words
- what the user answered, and anything they added

None of the three means much alone. The answer without the reasoning records that the user disliked something, with no way to tell what about it; the reasoning without the answer is what the agent already thought.

## A Third Kind of Memory

These records have their own folder, `user_feedback`, alongside `user_nature` and `states`.

They are kept apart because they are the only memory that records a judgement of the agent rather than a fact about the user or their world. Mixed in with everything else, a past misjudgement is reached only by accident - and an agent that cannot reach its own misjudgements repeats them.

Nothing walks the library to find them any more. What the agent meets again is the notification lessons: each add, edit, disable, delete or Bad notification is read by a model for what it says about the notifications the user wants, and the agent heartbeat gets those lessons before it adds more.

## Scheduling Something Is Also a State

A task is a moment and is executed; a state is a span and is only shown. Something the agent has undertaken to do is both - there is the instant it runs, and there is the stretch of time over which it is true that the agent means to do it. Writing only the task leaves the calendar silent about the second, which is the half the user reads.

So creating a scheduled task also writes a short state that accompanies it, with `source` of `task` and `task_id` naming the task. The span is deliberately small: it says *something is queued*, not *the agent is busy with this all week*, and it stops claiming the calendar shortly after the task has run.

This is also the first thing to write `State.task_id`. The field and the `task` source were specified in section 34.1 and had never been set by anything, so a state could claim to accompany a scheduled task while naming none - and three did.

# 52.5 Three Ways a Run Can End
<!-- number: 52.5 | label: 52.5 | id: s52-5 | page: 101 -->

A run ends in one of exactly three ways, and the scheduler must record which:

- **Completed.** The task is done and the thing it was for has happened. It is settled and nothing follows
- **Delayed.** It could not be done now and should be tried again later. The task keeps its place and its identity
- **To be checked.** It was carried out, and whether it achieved its purpose is not yet known. The task is not settled

The third was the one that was missing, and it was once the common case. In practice a run now ends completed or delayed. **To be checked** is still a recognised outcome, but a delivered notification is taken as finished, so it settles the task as completed: no check is scheduled, because a check would be a model task and only notifications run. *Got it* also settles the task directly. An outcome that cannot be recorded is an outcome that gets recorded as one of the others.

A delay must not consume the task's retry budget. Claiming a task for a run and delaying it are different events, and counting them together means a task the user keeps deferring runs out of attempts and disappears - the opposite of what deferring it asked for.

The same three endings apply after a user's answer (section 52.4). A question interrupts a task; the answer returns it to exactly this choice - settled or delayed.

# 52.6 Two Templates, and What Each Answer Does
<!-- number: 52.6 | label: 52.6 | id: s52-6 | page: 101 -->

A question put to the user takes one of **three forms**, and no others:

- A **reminder** - the agent is telling the user something it believes needs doing
- A **permission** - the agent asking to do something before it does it. It is kept so that older questions can still be answered, but nothing raises it today: only the reminder and the notice are raised
- A **passive notice** - the agent is telling the user something and wants no decision from them. *OK* closes the task, and *Add to to-do* closes it and lists it in To do on the Overview

Every option on every question declares what it does to the task: settle it, bring it back after the stated interval, or settle it and put it on the to-do list. A reminder offers *Got it*, *Remind me later* and *Add to to-do*; a notice offers *OK* and *Add to to-do*.

The passive form exists because the other two both ask something. A note the agent only wanted to say had to borrow a reminder's buttons and ask "shall I remind you again?" about a sentence that needed no answer - or carry no buttons at all, which leaves it in the list for ever. Dismissing is not the same as judging, and a notice that cannot be dismissed is a notice that accumulates.

This is what makes an answer worth keeping. An answer is evidence about the agent's judgement only if what it meant was fixed in advance - otherwise the record says the user chose the second option and nothing about what the second option was.

A user who wants no more of a notification says *Got it* to end this one, *Bad notification* to end it and ask for fewer like it, or disables or deletes it on the calendar. There must be a way of saying so that is not silence, because silence is indistinguishable from not having seen it. Only the permission form, which nothing raises any more, carries *Stop asking about this*; a reminder deliberately has no *Stop reminding me*.

**Every interruption the agent raises is one of the templates.** Not only the ones attached to a task on the calendar: a notification the agent chose to send is a decision it made about the user's attention, and it arrives with the options for answering it. The caller says which template it is, because only the caller knows whether it is telling, asking or merely mentioning - and a notice dressed as a reminder asks the user to decide something nobody meant to ask. A caller that names no template, or names one that does not exist, gets a reminder and the omission is logged: losing the interruption would be worse than choosing for it.

This does not extend to what the *system* raises about itself - a model being unavailable, a sign-in expiring. Those are not judgements the user is being asked to weigh, and giving them buttons would suggest the user had a decision to make where they have only a fact to read.

One interruption raises one question. A repeating reminder that fires again while the last one is still unanswered does not stack a second copy; the question already standing is the question.

**And one prompt.** The same applies to what is put on screen, for the same reason and one step later. A reminder that fires twice raises two notifications pointing at that one standing question, and answering either answers both - so showing both makes the user dismiss the same words twice, and the second tells them nothing they have not just read. A notification that names a question already waiting merges into it rather than queueing behind it, and the later wording wins, because a repeating reminder may have something new to say each time.

**An interruption is shown where it can be answered, and nowhere else.** Every one of them is a template with options attached, and the operating system's notification centre can carry none of them: it cannot be answered, and it is drawn by something this application does not control. Sending both put the same words on screen twice - once answerable, once not - and the copy the user reached first was usually the one they could do nothing with.

So nothing is handed to the system notifier at all, and there is no setting to turn it back on. A switch whose "on" position produces an interruption the user cannot answer is not a preference; it is a way of breaking the rule above. What the agent has to say appears in the agent, which also means there is one place to look for it.

Questions asked under an earlier naming must still be found and answered. A question already on screen when the software changes is not an acceptable thing to lose.

**The label travels with the option.** An option is a key, a label and an effect decided together, and whatever draws the buttons reads the label off the option rather than keeping its own table of keys to words. A second table drifts in one direction only: the templates are renamed, the table is not, and it goes on printing the old wording for the keys it knows and the bare key for the ones it does not. Worse, a screen that holds its own vocabulary tends to send its own vocabulary back, and an answer the server does not recognise is rejected - so the question stays, and the task behind it stays parked, which is the one outcome section 52.4 exists to prevent.

The same applies to the two template names. A question carries **which template it is** alongside the kind it was stored under, resolved by the one place that knows the old names, so that nothing downstream has to carry the mapping a second time in order to tell a permission from a reminder.

# 52.7 Nothing Exists Without Saying Why
<!-- number: 52.7 | label: 52.7 | id: s52-7 | page: 101 -->

Every task, state and calendar entry carries **who decided it should exist** and **the judgement that led to it**. Neither is ever empty.

An agent that acts on its own judgement and leaves no account of that judgement gives the user two choices: accept everything, or switch it off. A reminder that turns out to be unwelcome is a different conversation when the user can read why the agent thought it was worth it, and it is the only way the agent's own decisions can be reviewed at all - by them, or by anything else.

**A user's own entry needs no justification.** They put it there; that is the reason. Asking them to type one would be the system demanding an explanation from the only participant who does not owe one, so anything they wrote is kept and anything they left blank becomes `added by user`.

**An agent-created thing with nothing recorded is different in kind.** It is not a thing with no reason - it is a thing whose reason was not written down, and the two read identically if both are blank. So it says so, and the omission is logged as what it is: a fault in whatever created it.

This is filled in where the thing is created rather than trusted to each caller, because a field that is optional is missing precisely where it matters.

Two consequences worth stating:

- **A state written to accompany a task carries that task's reason.** They are two records of one decision, and letting them drift apart would leave the calendar and the agent's own context disagreeing about why something is happening
- **The reason is part of the context an answer is built from.** A state is the agent describing the user's situation back to them and it can be wrong, so what the model reads includes the grounds and not only the claim (section 34.1)

Where it is shown: folded, in the entry's detail, not expanded. The requirement is that a reason is recorded and reachable, not that it is the first thing in the way - an earlier version showed it open and pushed everything else out of the card.

# 53. Task Classification
<!-- number: 53 | label: 53. | id: s53 | page: 95 -->

After reasoning about a task, the Agent Core decides whether the action is:

1. Immediate
2. Delayed

# 54. Immediate Tasks
<!-- number: 54 | label: 54. | id: s54 | page: 95 -->

Immediate tasks execute during the current agent invocation.

Example:

```
User: Summarize this file.
```

Agent:

```
reads file
generates summary
returns result
```

# 55. Delayed Tasks
<!-- number: 55 | label: 55. | id: s55 | page: 96 -->

If something should happen later, the agent schedules a notification: what to say, when, and whether it interrupts.

Work that has to be done is not scheduled. There is no Python in the chat and no script can be scheduled; longer work is proposed as a delegated work run, which starts only after the user approves its draft and plan.

# 56. Scheduled Task Schema
<!-- number: 56 | label: 56. | id: s56 | page: 96 -->

Recommended conceptual schema:

```
ScheduledTask:
    id
    title
    description
    created_at
    scheduled_at
    timezone
    status
    execution_type
    script_path
    model_config
    task_payload
    retry_policy
    recurrence
    runs
    attempts
    last_run_at
    created_by
    reason
    metadata
```

The only execution type is `notification`. A task's payload says whether it interrupts (`notify_user`) and which form it takes (`template`: reminder or notice). The executor still has handlers for the earlier types - python_script, agent_task, model_task, system_action, integration_action - but nothing can create or run them: the scheduler refuses them at creation, and cancels with a note any that turn up.

# 56.1 The Verification Plan Is Made When the Task Is
<!-- number: 56.1 | label: 56.1 | id: s56-1 | page: 96 -->

A task may carry, from the moment it is created, what would count as it being finished. It is kept for the record only: nothing checks it afterwards, because a check would be a model task and only notifications run.

- what **completion** looks like, stated so that it can be recognised later
- how long to wait before checking, which now sets only how long *Remind me later* waits before the notification comes back

Deciding this at creation is what makes it possible at all. Whoever creates the task knows why it exists; a later round knows only that a task ran.

A task with no such plan completes when it runs. That is the right default for the tasks it is true of - reporting something, showing something - and stating it as a default rather than leaving it as an assumption is what keeps it from being applied to the tasks it is false of.

# 57. Scheduler Loop
<!-- number: 57 | label: 57. | id: s57 | page: 97 -->

Conceptually:

while scheduler_running:

```
load_due_tasks()
```

```
for task in due_tasks:
    execute(task)
```

```
sleep_until_next_check()
```

The target check frequency is approximately once per minute.

The implementation should avoid unnecessary model calls when no model reasoning is required.

# 57.1 A Check Is Itself a Task
<!-- number: 57.1 | label: 57.1 | id: s57-1 | page: 97 -->

Removed. A run that ends "to be checked" is completed, and no checking task is created: a check is a model task, and only notifications run (section 52). The handler that ran checks survives in the executor, but nothing can reach it.

# 58. Model-Based Scheduled Tasks
<!-- number: 58 | label: 58. | id: s58 | page: 98 -->

Removed. Model-based scheduled tasks and their knowledge-graph retrieval are gone: only notifications are scheduled or run (section 52), and there is no graph retrieval anywhere (commit fcf74e1). Work that needs a model is proposed as a delegated work run, which starts only after the user approves its plan.

# 59. Scheduler Reliability
<!-- number: 59 | label: 59. | id: s59 | page: 99 -->

Scheduled tasks must be persisted.

Restarting the Personalized Agent should not silently delete scheduled events.

The Scheduler should support:

- Persistent storage
- Task status
- Failed-task detection
- Retry policies
- Duplicate execution prevention
- Cancellation
- Rescheduling
- Agent restart recovery

Recommended states include:

```
pending
running
completed
failed
cancelled
disabled
closed
```

`closed` is a notification the agent heartbeat decided is no longer needed: it stays on the calendar with the reason and never runs. `disabled` is shown as disabled by the user, and is never run either. A `cancelled` task records who cancelled it, when and why. After a restart, tasks left running by the previous process are recovered rather than lost.

# 60. Calendar Synchronization
<!-- number: 60 | label: 60. | id: s60 | page: 100 -->

Scheduler state and frontend Calendar state must use a single authoritative backend representation.

Do not maintain two independent schedules.

Required architecture:

```
        +------------------+
         | Scheduler Store  |
         +---------+--------+
                   |
      +------------+------------+
      |                         |
      v                         v
Scheduler Loop             Calendar UI
```

# 60.1 The Calendar and the Scheduler Edit Each Other
<!-- number: 60.1 | label: 60.1 | id: s60-1 | page: 100 -->

The calendar is an interface to the scheduler in both directions.

**An edit on the calendar reaches the task.** An event can be edited from its card - title, message, time and repeat - while it is pending or disabled; one that is running, completed, closed or cancelled is refused. It can be disabled, which keeps it on the calendar marked as disabled by the user and never runs it, or deleted, which takes it off the calendar after the user confirms and optionally says why. There is no dragging to move an event. Each add, edit, disable and delete, with the user's optional reason, is kept as a notification lesson the agent heartbeat learns from. The associated state (section 11.1) follows the same edit, because a state that still describes the old time is a record of something that is no longer true.

**A repeating event is edited as a whole series.** The calendar does not ask: Edit, Disable and Delete on any occurrence act on the series. The API can still act on **this occurrence** alone:

- *This occurrence* - skipped or changed - is recorded as an override against the one occurrence. The series keeps its shape and the occurrences after it are unaffected
- *All occurrences* changes the series; a new time or repeat starts it again from its next run and drops earlier overrides

A skipped or moved occurrence must not move the ones after it. What the series repeats on and when it next runs are separate facts, and treating an override as the new starting point drags the whole grid along with it.

**An event created on the calendar is a task.** An entry the user adds has the same properties a scheduled task has, and a user filling in a calendar form will not supply most of them.

So the event is created immediately, with what the user gave, and the rest is filled in afterwards by the agent from the context it can gather. The user is not made to wait for that and is not shown a form asking for a rationale or a verification plan. What is missing is filled; what the user stated is never overwritten.

# 61. Agent Identity and Application Protocol
<!-- number: 61 | label: 61. | id: s61 | page: 100 -->

Location:

```
./protocol/
```

Each Personalized Agent has a persistent identity consisting of:

- Agent ID
- Public key
- Private key

The Agent ID and public key can be read from the local API (`/api/protocol/identity`). No screen shows them yet.

The private key must remain secret.

# 62. Private Key Requirements
<!-- number: 62 | label: 62. | id: s62 | page: 101 -->

The private key must never be:

- Displayed in the UI
- Included in model prompts
- Written to normal logs
- Included in Raw Edits
- Included in the library (memory records)
- Returned to applications

Secure OS-backed credential storage should be used where possible.

# 63. Application Registration
<!-- number: 63 | label: 63. | id: s63 | page: 101 -->

Applications supporting the Personalized Agent protocol may allow users to register:

Agent ID

Public Key

Permissions

Permissions may include:

```
read
write
execute
```

application-specific operations

Permissions belong to the external application's authorization model.

# 64. Authentication Concept
<!-- number: 64 | label: 64. | id: s64 | page: 102 -->

The application protocol should use cryptographic challenge-response authentication.

Conceptually:

Agent

```
|
| Agent ID
v
```

Application

```
|
| random challenge
v
```

Agent

```
|
| signed proof
v
```

Application

```
|
| verify with registered public key
v
```

Authenticated Session

The system should prove possession of the private key rather than transmitting it.

# 64.1 The Browser Extension
<!-- number: 64.1 | label: 64.1 | id: s64-1 | page: 124 -->

Removed. The browser extension, its website integrations and the setting that switched them on were taken out (October 2026), together with private mode, which the extension's status reported. The browser is read from its own history only. The signed ingest endpoint of the integration protocol (sections 61-65) remains for other external applications.

# 65. Cryptographic Design Requirement
<!-- number: 65 | label: 65. | id: s65 | page: 103 -->

A custom unreviewed cryptographic algorithm must not be invented.

Before implementation, the protocol must formally define:

- Signing algorithm
- Public-key format
- Private-key storage
- Challenge generation
- Replay prevention
- Session establishment
- Key rotation
- Key revocation
- Permission revocation

Standard, well-reviewed cryptographic libraries and protocols should be preferred.

# 66. Agent Core Execution Workflow
<!-- number: 66 | label: 66. | id: s66 | page: 103 -->

The model is called from:

1. The user's direct instructions, in the chat
2. The agent heartbeat
3. Work runs the user has approved, each in its own process
4. Customized Recommendation, on its own schedule
5. The lessons engine, which reads the user's own actions

There is no data management heartbeat any more (commit fcf74e1).

# 67. Direct User Instruction Workflow
<!-- number: 67 | label: 67. | id: s67 | page: 104 -->

User Instruction

```
|
v
```

Agent Core (with the conversation so far, older turns summarised when it is long)

```
|
v
```

Reason About Task

```
|
+------------+
|            |
v            v
```

Immediate       Delayed

Execution       Scheduler

```
|            |
+------+-----+
       |
       v
  Record Raw Edit
       |
       v
 Respond to User
```

Nothing is retrieved from memory before the model is asked: the chat answers from the instruction and the conversation it was asked in. What is delayed is a notification; work that needs doing later is proposed as a work run instead.

# 68. Heartbeat Workflow
<!-- number: 68 | label: 68. | id: s68 | page: 105 -->

Heartbeat Timer

```
|
v
```

Load Checklist Snapshot (leaving out paused steps and the steps of functions that are switched off)

```
|
v
```

Read the Window: the activity since the last review that got an answer, plus the states running now. Anything in it can be opened by its id; nothing beyond it is searched.

```
|
v
```

Step 1: Analyse User State (write states)

```
|
v
```

Step: People

```
|
v
```

Step: Notify (immediate or scheduled actions)

```
|
v
```

Step: To-dos (tick what has finished; skipped when none are open)

```
|
v
       Raw Edits
            |
            v
Move "summarised until" forward, only if step 1 answered
            |
            v
           Exit
```

Each step is one model call, and each gets its own copy of the same window. Step 2, who the user is, is paused: the user's nature is learned from their lessons instead. A late look at a stretch that was never read runs step 1 alone.

An instant trigger on new on-screen text (a meeting invitation, an agreed time) runs the Notify step alone, for that window, outside the timer. Customized Recommendation and the state heartbeat, which groups states into tasks, run on their own clocks and are not part of this workflow.

# 69. Data Heartbeat Workflow
<!-- number: 69 | label: 69. | id: s69 | page: 106 -->

Removed. The Data Heartbeat, with its node and edge updating, compaction and "mark processed" step, was deleted on 2026-10-06 together with the knowledge graph's indexer and library search. Raw activity is now read only by the agent heartbeat (its window), the Overview and the page-visit history, and is removed by age after the raw-activity keeping period (eight days by default), whether or not it was read.

# 70. Model Management
<!-- number: 70 | label: 70. | id: s70 | page: 107 -->
<!-- note: 2026-10-16: the user asked that the last model on the list can be removed too. -->

The Personalized Agent must support multiple Large Language Model providers and let the user choose which models it thinks with, and in what order.

The models are kept as one ordered list in Settings > General > Models. The first entry is the Primary Model.

*Amended 2026-10-16:* the list may be emptied: the last model is removed like any other. Emptied, it stays empty - the model chosen before there was a list does not come back in its place - and every call that needs a model is refused with "there is no model on your list" until one is added; the page says so where the list was.

Different model providers may use different authentication mechanisms. The architecture must therefore separate:

- Model selection
- Model provider
- Authentication method
- Credential state
- Model availability
- Usage/quota state

The system must not assume that all model providers use API keys.

# 71. Primary Model
<!-- number: 71 | label: 71. | id: s71 | page: 108 -->

The user keeps an ordered list of models in Settings > General > Models. The Primary Model is simply the first entry on that list. Each entry carries its own reasoning effort and a switch saying whether it sees pictures or reads text only.

Conceptually, each entry is:

Settings

```
|
v
```

Model Configuration

```
|
+-- Provider
+-- Model
+-- Authentication Method
+-- Credential / Login Status
+-- Availability Status
```

For example:

Primary Model

Provider: <provider>

Model: <model>

Authentication:

```
Account Login / OAuth
```

Status:

```
Connected
```

or:

Primary Model

Provider: <provider>

Model: <model>

Authentication:

```
API Key
```

Status:

```
Available
```

The exact list of supported providers and models may evolve over time and should not be hard-coded into the frontend.

# 72. Model Usage Hierarchy
<!-- number: 72 | label: 72. | id: s72 | page: 109 -->

Every part of the agent uses the model list. A call goes to the first model on the list that can answer, so the Primary Model is the default reasoning model for:

- Direct user instructions
- Agent reasoning
- Heartbeat reasoning
- Scheduled model-based tasks

The roles that resolve a model are the primary role, the heartbeat role and the scheduled-task role. There is no separate data-heartbeat role.

The heartbeat and scheduled-task roles may still be pinned to one model through hidden settings, and a pinned role uses only that model, not the list. The interface does not offer these overrides.

# 73. Authentication Methods
<!-- number: 73 | label: 73. | id: s73 | page: 110 -->

The Model Provider layer must support multiple authentication mechanisms.

At minimum, the architecture must support:

1. API Key Authentication
2. Account Login / OAuth Authentication

Additional authentication mechanisms may be added later through the same provider abstraction.

Supporting a mechanism means a user can complete it end to end for at least one provider the application ships with, not only that the provider abstraction has a place for it.

# 74. API Key Authentication
<!-- number: 74 | label: 74. | id: s74 | page: 111 -->
<!-- note: 2026-10-16: the user installed the macOS release; it showed no icon and quit at once. Run from Terminal it said the keychain refused it (-67030, executable not signed). -->

Some model providers require the user to provide an API key.

The Settings interface should support:

Authentication Method:

```
API Key
```

API Key:

```
[••••••••••••••••]
```

Status:

```
Connected / Invalid / Unavailable
```

API keys must be stored securely.

They must never be:

- Stored in the Knowledge Graph
- Included in Raw Events
- Included in Raw Edits
- Included in model prompts
- Printed in logs
- Displayed in plaintext after initial entry
- Included in Debugger output

Where available, operating-system secure credential storage should be used.

Examples include the appropriate OS credential/keychain service rather than normal configuration files.

*Amended 2026-10-16:* a keychain that refuses to keep a secret does not stop the agent. macOS refuses an application whose signature does not match it, and the 1.0.0 macOS build was such an application - its Info.plist was written after it was signed - so it quit on start, its identity key unstored, before its window or its menu-bar icon appeared. The refusal is logged and remembered, and from then on that installation keeps its secrets in the encrypted file used where there is no keychain at all; what the keychain held before is still read from it. The macOS build signs the bundle again after the Info.plist is written - ad hoc when there is no signing identity - and the release check verifies that signature, since it runs with the keychain out of reach and could not see the refusal itself.

*Amended 2026-10-16:* and a macOS build signed by nobody does not use the keychain at all. Signed ad hoc - all a build without a developer's certificate can be - it is known to the keychain by the exact bytes of one version, so after every update macOS would ask the user again before handing back what the last version kept, and a "Deny" would leave the agent without its keys and with a new identity. Such a build keeps its secrets in the encrypted file from its first start; anything the keychain already held is read from it once and moved into the file, so the user is asked at most once. Run from source, or signed by a developer, the keychain is used as before.

# 75. Account Login / OAuth Authentication
<!-- number: 75 | label: 75. | id: s75 | page: 112 -->

Some model providers may authenticate through a user account rather than a manually supplied API key.

The Settings interface must therefore support account-based authentication.

Conceptually:

Authentication Method:

```
Account Login
```

Account:

```
user@example.com
```

Status:

```
Logged In
```

[Log Out]

If authentication is required:

Authentication Method:

```
Account Login
```

Status:

```
Not Logged In
```

[Log In]

The authentication process may use OAuth or another provider-supported account authorization mechanism.

The exact implementation depends on the provider.

The core agent must interact with a common authentication abstraction rather than directly implementing provider-specific login behavior throughout the application.

# 75.1 Account Login Initiation Requirement
<!-- number: 75.1 | label: 75.1 | id: s75-1 | page: 112 -->

Displaying account authentication state is not sufficient.

Selecting Log In must initiate an authorization flow. It must not only report whether a credential already exists.

For a provider whose account authentication is browser based, the application must:

- Open the provider's authorization page in the user's browser
- Receive the authorization result through a loopback redirect, a pasted authorization code, or another completion mechanism the provider supports
- Exchange that result for the provider's tokens
- Store the tokens through the Credential Manager
- Update the credential state to authenticated without requiring an application restart

For a provider whose account authentication is delegated to an external tool, the application must:

- Detect whether the tool is present on this machine
- Offer to run the tool's login command on the user's behalf
- Report the outcome in the Settings interface
- Detect the resulting credential without requiring an application restart

If a provider offers no account authentication mechanism that the application is able to complete on the current machine, the Settings interface must not present Account Login as a selectable method for that provider. It must still disclose the method, showing why it cannot be completed here and what the user can do about it, as required by section 77.

> Silently omitting a method the provider does in fact support is its own failure: the user is left believing the provider offers only the methods that remain visible.

> An implementation that reports that no credential was found and asks the user to configure the environment by hand does not satisfy this requirement.

Instructing the user to set an environment variable or to run a command in a terminal may be offered as a fallback, but never as the only outcome of selecting Log In.

Section 84 continues to apply to any tokens obtained through this flow.

# 75.2 Browser-Based Account Login
<!-- number: 75.2 | label: 75.2 | id: s75-2 | page: 112 -->

Account login must be completed from within the application.

The user presses one control and finishes in their browser. They must not be required to open a terminal, run a command, edit an environment variable, or leave the application to read instructions.

The application hosts the flow:

1. Obtain the authorization URL for the provider
2. Open that URL in the user's default browser
3. Display the same URL in the application, so a user whose browser did not open can follow it
4. Accept the completion - a loopback redirect the application listens for, or a code the user pastes back into the application's own window
5. Complete the exchange and record the resulting credential
6. Report the outcome in the same place the sign-in was started, without requiring the user to press the control a second time and without an application restart

> Opening a terminal for the user to finish the sign-in in does not satisfy this requirement.

Where the provider's account credential is issued by an external tool rather than by an endpoint the application may call directly, the application drives that tool as a subprocess: it captures the authorization URL the tool emits, opens the browser itself, collects the completion in its own interface, and passes it back to the tool. The tool's console must not become the user's interface.

A tool that renders only to a terminal is driven through a pseudo-terminal the application allocates and owns. Handing the user to a console the application does not control is the failure this requirement exists to prevent, and a tool being interactive is not an exemption from it.

> Instructing the user to run a command themselves and paste the result back is not an implementation of this section. The application runs the command.

## Completion Is Not One Shape

A sign-in may finish in more than one way, and the application must accept whichever way this one did.

The same tool, on the same machine, may complete either by handing the result back through the browser - in which case the user is told they are done and is never shown a code - or by asking for a code the user brings back. Which happens is not the application's choice to make.

The application must therefore watch for completion from the moment the authorization begins, rather than only when the user submits something:

- A sign-in that completed on its own must be recognised without further user action, and reported as signed in
- The interface must not continue to ask for a code that no longer exists
- A code submitted after the sign-in already completed is not an error
- A tool that returns a credential and then keeps running must not be waited on to exit; the credential arriving is the completion

> A sign-in that has in fact succeeded must never be reported as one that is no longer running.

More than one such tool may be able to complete the sign-in. The application must use whichever is present on the machine rather than requiring one particular tool, and must name the tool it is using in the interface, because the choice determines which account and which billing the resulting credential draws on.

Where the tool returns a credential to the application rather than storing one itself, that credential is a secret under sections 83 and 84: it is placed in the Credential Manager immediately, and is never displayed, logged, written to the Knowledge Graph, or returned to any caller - including in the tool output the application surfaces when a sign-in fails.

The interface must state, before the user signs in:

- Which tool will perform the sign-in
- Which account and billing arrangement the resulting credential uses
- Where the credential is stored, and whether it travels when the agent root is copied (section 110.1)

The application must not present itself as another application in order to obtain a credential. It uses a client identity the provider has issued to it, or an authorization mechanism the provider documents for this purpose; reusing a client identity belonging to a different application is not an acceptable implementation of this section.

The sign-in must be cancellable, must time out rather than wait indefinitely, and must report a failure with the reason.

Where the resulting credential is stored outside the agent root - for example by an external tool that owns its own credential store - section 110.1 applies: the application must make clear that this credential does not travel when the root is copied.

# 75.3 Using a Stored Sign-In
<!-- number: 75.3 | label: 75.3 | id: s75-3 | page: 112 -->

A sign-in is performed once. After that the stored credential is used without asking.

The application must not prompt for authentication that it already has. Where a credential exists and works, the provider is simply used.

The application must, however, know when that stops being true:

- The stored credential is verified when the application starts, so that an expired sign-in is reported while the user is present and can act, rather than discovered later by a background task
- Where verification finds that the credential is no longer accepted, the user is asked to sign in again - once, through the notification requirements of sections 87 and 88, naming the provider and the reason
- The prompt is not repeated for the same failure, and it is withdrawn when the credential works again (sections 91, 92)
- A credential that is merely rate limited or temporarily unreachable is not a failed sign-in and must not produce one

> Asking a user to sign in when the application is already signed in is an error, and so is failing quietly when it is not.

## Being Told Is Not the Same as Being Nagged

The prompt can be dismissed.

Dismissing it fixes nothing, and the application must not pretend otherwise: the credential is still unusable, and that remains visible where state is shown. What dismissing ends is the interruption, which has done its work once the user has read it.

- A dismissed prompt does not return for the same condition
- It returns when the condition is genuinely new: after the credential works again, a later failure is news and is raised
- Dismissing is not fixing, so nothing about the credential changes, and every other report of its state is unaffected

> A message the user cannot put away is one they learn to ignore, including the next time it matters.

# 76. Model Provider Interface
<!-- number: 76 | label: 76. | id: s76 | page: 113 -->

Model providers should implement a common interface.

Conceptually:

```
class ModelProvider:
```

```
def list_models(self) -> list:
    ...
```

```
def get_auth_methods(self) -> list:
    ...
```

```
def get_auth_status(self):
    ...
```

```
def authenticate(self, config):
    ...
```

```
def logout(self):
    ...
```

```
def check_availability(self):
    ...
```

```
def invoke(self, request):
    ...
```

Provider-specific implementations may support different capabilities.

For example:

```
ModelProvider
    |
    +-- Provider A
    |      └── API Key
    |
    +-- Provider B
    |      └── OAuth
    |
    +-- Provider C
           ├── API Key
           └── Account Login
```

The Agent Core must not need to know how a particular provider authenticates.

# 76.1 A Local Client as a Provider
<!-- number: 76.1 | label: 76.1 | id: s76-1 | page: 113 -->
<!-- note: 2026-10-17: Codex now runs with none of the user's own Codex setup, its own tools kept only where the agent's permissions still hold, and the agent's tools served to it over MCP, as to Claude Code. 2026-10-18: a client's own web tools are held to the agent's checks on the web (Claude Code by hooks, Codex's search cached only), and the checks on a page are wider. -->

A provider need not be an API this application calls. It may be a program on the user's machine that is already licensed to answer.

The application may ship adapters for more than one such client. For example, a Codex adapter invokes the installed client non-interactively, uses its own sign-in rather than extracting a credential, uses the model selected by that signed-in client rather than assuming every account can select the same model identifier, and runs each request in an ephemeral read-only sandbox. The agent still decides whether any requested action runs through its own tool layer and sandbox.

Where such a client exists, is signed in, and can answer non-interactively, the application may use it as a provider: it sends the request, and uses what comes back.

This is how a subscription is used without borrowing its credential. The application holds no credential for such a provider, stores none, and the work is billed wherever that client's account is billed.

> A subscription licenses a client, not this application. Taking that client's credential and calling the API with it is not using the subscription - it is impersonating the client, and the provider refuses it.

Requirements:

- The client is used only if it is installed and reports itself signed in; both are checked locally, without a request that costs anything
- Where the user's account is a subscription and no usable client is installed, the application says which client would let them use it, rather than reporting that nothing can be configured
- The client acts only through the agent. What the agent may do is decided by the agent's tool layer and its sandbox (sections 31, 107), and routing actions through another program goes around both. Where the client can take tools natively, it is given the agent's tools and nothing else: for the command-line Claude client, the application serves the request's tools from a private local MCP server (on 127.0.0.1, at an address holding a secret) for the length of that one call, refuses every other MCP server, and runs each call the client makes through the agent's own tool layer and sandbox. The client's own tools stay denied, with two exceptions: its web search and fetch, when the user has chosen the model's own web search (the default), and its file-reading tools, limited by deny rules to the places the agent may read
- Each call is run without keeping a session of its own, and ends when the client falls silent rather than at a fixed timeout
- It runs inside the agent workspace, so that nothing it does can reach outside the root (section 110.1)
- Its answer is subject to the same handling as any provider's: the same normalized availability states, the same failure classification, the same reporting of what it cost
- The interface says answers are coming from that client, and whose account they are billed to

A client that cannot take tools natively - Codex, and the command-line Claude client when a call comes with no tool runner - is offered a convention of its own for the tool-call protocol, and its reply is read from the front rather than whole.

**Strictly is not the same as wholly, and the difference reached the user as fact.** Reading it whole means requiring the reply to be exactly one call and treating anything longer as an answer. What that meets in practice is a reply that opens with a perfectly good call and then keeps going - the model continuing the transcript by writing the tool's reply for it, because a transcript that shows tool results is a transcript a model can continue. Read whole, that is "not a call", so it became the answer: a raw JSON call followed by invented records, four of whose six ids existed nowhere in the library.

So the front of the reply decides. If it opens with a call, it is a call, and whatever follows is discarded - the tool is run and the real result supplied. **Nothing a model writes after its own call can be a tool result**, because tool results do not come from the model; and the client is told so, since the mistake is invisible once it has been made.

Discarding it is only half. What goes back into the transcript as *what you said last turn* must be the call and not the whole reply, or an invented result is read back to the model a turn later as its own words, sitting immediately above the real one - teaching it turn by turn that writing results is what it does here.

**And the client is told what its own refusal means.** A borrowed client has tools of its own, none of which are the agent's, so reaching for an agent tool that way is answered with "no such tool". Left unexplained, the model reports that to the user as a missing tool: the user goes looking for a fault, and the tool is sitting in the list the model was given. The instruction says what the message means and what to do instead, and says plainly not to report it as absence.

**The client acts through the agent, or not at all.** A borrowed client can schedule, remind, notify and delegate for itself, and to the model those look like perfectly good ways to do what the user asked. They are not: they last as long as one invocation and then stop existing, while the agent has a scheduler that survives restarts. Asked to give a reminder at two o'clock, the model made one inside a subprocess that had already exited, and reported - accurately and uselessly - that it was "session-only". Anything that must outlive the reply is denied to the client by name and told to go through the agent's own tools, and a promise made about something that does not persist is worse than a refusal.

**It runs with none of the user's own connectors.** A client reads the MCP configuration of whoever installed it, so the agent's model calls were inheriting document connectors and anything else the user had set up in their own copy - tools this application never granted, cannot see and does not sandbox. The model reached for them, and described its abilities in terms of them. What the agent may do is decided by the agent's tool layer, and that decision is worth nothing if another program's configuration can add to it.

**The client answers as the agent, not as itself.** A borrowed client is a whole assistant with a tool set of its own, and that tool set is switched off here. So it must be told that the tools offered in the convention are its tools for this conversation, that calling one really runs it, and that it is not to describe what it can and cannot do in terms of the program it happens to be running inside. Without that it answers about the wrong thing: it reported that its session had no way to list scheduled tasks, in a reply written after it had listed them, because the empty set it was describing was the host's and not the agent's. A caveat like that is worse than no caveat - it is the agent telling the user, in good faith, that a working part of itself does not exist.

**A tool result is not the user speaking.** The API carries a tool result on a user turn. That is the API's convention and invisible inside it, but a client that takes a transcript has to be given roles in text, and a result rendered as the user's words reads as the user pasting data in. The model then disowns the answer it builds on it - crediting its own memory for what it has just this moment looked up. What a tool returned is labelled as what it is.

*Amended 2026-10-17:* CODEX TOO RUNS WITH NONE OF THE USER'S OWN, AND IS GIVEN THE AGENT'S TOOLS AS TOOLS. It had been called with the user's Codex configuration loaded, and so with what they installed for their own use - a browser, computer use, a Node REPL server, plugins, hooks - which the agent never granted; and it was offered the agent's tools only as words. Codex reaches for tools natively all the same: a real run found its reviewer trying its own shell instead of the agent's `read_file` (on that machine the shell could not even start - Codex's helper sat at a path of 260 characters), so the work was judged without being opened, and asked to write a file it answered that it could not. Now every call skips the user's configuration (`--ignore-user-config`, `--ignore-rules`; a Codex too old for that has its MCP servers emptied) with their sign-in still theirs, and, as for Claude Code, the agent's tools are served to it for the call by the agent's own MCP server (`mcp_bridge`), run by the agent under its guard, the call ended when it falls silent or the run is stopped rather than at a fixed time. ITS OWN TOOLS ARE KEPT WHERE THE AGENT'S PERMISSIONS STILL HOLD, AND ONLY THERE (`codex_provider.OWN_TOOLS_OFF`): its web search, when the settings say the model's own (as Claude Code's); its code mode, through which it calls every tool, the agent's too - measured, not assumed: inside it there is no `require`, no `process`, no `fetch`, no file of any folder, only the tools it is given; and its `apply_patch`, which stays in that list, is refused by the read-only sandbox ("writing is blocked"). Its shell, file editing and picture viewing stay off: Codex's sandbox cannot be kept out of the places the agents may not read, as Claude Code's reading tools are by rules naming them. And NO AGENT OF ITS OWN: its multi-agent tools stayed in code mode's list with the feature switched off, and a helper agent was started from there - outside the agent's sight, guard and count of what the run spent - so the depth of agents below the call is set to none (`agents.max_depth=0`), and the tools are gone. The model and the effort the user's configuration names are still read from it - only those two - so "Codex default" means what it did.

*Amended 2026-10-18:* WHICH TOOLS CODEX HAS IS ASKED OF IT, NOT ASSUMED. Its own tools are turned off by name, and a name not on the list - a tool a later Codex adds - stays on; its multi-agent tools stayed with their feature off. So before a call whose reading must stay sealed (a way or a try of the Work debugger) is given to Codex, the installed program is asked what its code mode can call - once per program file, version and model, since which tools a model is given differs - and only what is measured to read nothing may be there: the MCP resource tools, which reach only the agent's own server, its patch tool, which the read-only sandbox refuses, the clock, and the agent's own tools (`CodexProvider.confined`, `OWN_TOOLS_ALLOWED`). Anything else, or no list that can be read, and that Codex is not used there - the call goes to the next model on the list, or is refused saying which tool it was - and its row in Settings says so. Found confined, it is used like Claude Code.

*Amended 2026-10-18:* A CLIENT'S OWN WEB TOOLS ARE HELD TO THE AGENT'S CHECKS, AND THE CHECKS ARE WIDER. The agent's own web tools check two things (`runtime/web_guard.py`): a search carries words, not data - nothing longer than a sentence, nothing that looks like a key, a token or something encoded - and a page is opened only when nothing read can travel in its address, since asking for a page sends its address to the site and a page read may tell the model to send what it read that way. The model's own search, the default, had bypassed both: Claude Code's WebFetch could open any address the model made up, and so could Codex's live search. NOW CLAUDE CODE'S WebSearch AND WebFetch ARE HOOKED: for each call they are offered with, the call's own server (the one that serves the agent's tools) answers a hook before and after each search and page - this application started as the hook (`--web-hook`), asking the call's guard - refused before it runs when the check fails, its results known after it, so a page its search found can be opened. Neither tool is allowed by the command: each is let through by the hook's answer alone, so a hook that cannot run, or a guard that cannot be asked, lets nothing through - measured with the client itself, as was that a flag taking names, left with none, swallows the next option (the hooks were lost so, and are no longer). A call with no guard to hold them to is offered the agent's own web tools instead. CODEX'S OWN SEARCH RUNS ON OPENAI'S SERVERS, where no hook of Codex's reaches (its own hooks guide says so of hosted tools): it is offered from OpenAI's cached index only (`web_search="cached"`), which opens no live page, beside the agent's own web tools, which read the live web under the checks. THE CHECKS ON A PAGE ARE WIDER, the user's decision: besides an address that exists - found in a search, on a page read, in the user's instruction, files or records - or its site's front page, a page may be opened on a site the user named, on a reference site (documentation, package registries, encyclopaedias, whose owners see nobody's visits), at a known address made shorter, or, on a site already seen, at a short address with no query none of whose words is the user's alone: each either never appeared in what was read of the user's, or appeared on a public page of another site too - a page cannot make the user's words public for its own site by listing them - and no number of four digits or more of the user's at all. A site made to collect what is sent to it (request bins, tunnels) is opened only at an address that exists. The Connections round, whose web tools had no check at all, is held to the same guard, and so are the pictures it fetches from addresses the model names.

## Which Client, Chosen Explicitly

A machine may have more than one licensed client - a command-line client and a desktop application - and they can be signed in to different accounts. Which one answers is therefore the user's explicit choice, not a search order they cannot see.

Being told "the client is signed in" means nothing if the user cannot tell which client was asked, or whose account will be billed for the answer.

- Each kind of client is a provider of its own - the Claude Code CLI and the Claude desktop app - and the user adds the one they mean to the model list as a model of its own. There is no "whichever is installed" choice
- Every kind of client the application can use is offered, whether or not it is installed
- A client that is not installed is not hidden. It is offered, and choosing it says plainly that it is not installed and where to get it - which is the answer the user was looking for
- A provider runs only its own client and never substitutes the other. Falling back quietly means the account that gets billed is not the one the user picked
- A client installed somewhere the application does not know to look is named directly, by choosing its program file rather than typing a path
- Wherever the application reports that this provider is signed in, it names the client it asked

## Asking Whether It Is Signed In

Asking a client whether it is signed in has three answers, not two: signed in, not signed in, and **could not ask**.

Reporting the third as the second is worse than saying nothing. It sends the user to fix a sign-in that was never broken, while the real fault - that this application could not run the client - goes unmentioned. Each of the three is reported as itself, and the reason a probe failed is kept and shown.

- A probe that could not be run, timed out, or answered unreadably is reported as exactly that, naming the client and the reason
- A client that ran and said it is signed out is reported as signed out, which is the case the user can act on
- Failing to ask is not grounds to refuse the user an answer. The client itself is the authority on its own sign-in, so the request is attempted anyway and the client's own refusal, if it comes, is what gets reported
- The answer is remembered for a short while, because asking starts a process and the panels that read it refresh on a timer
- What is remembered is tied to the client it describes. An answer about one client must never be reported for another - two clients can be signed in to different accounts, and the bill follows the one that answers

## How Such a Client Is Actually Invoked

A local client is a command-line program, and a command line is not a neutral pipe. Four of its properties decide whether this works at all, and each of them fails quietly rather than loudly:

- **The request goes in on standard input, never as an argument.** A conversation grows with every turn - history, tool results, retrieved context - and an operating system caps how long a command line may be. Passing the prompt as an argument works in testing, where conversations are short, and then fails for exactly the users who have talked to the agent the longest.
- **A tool the client must not use is named explicitly, and every name is one the client recognises.** A name the client does not know can make it discard the restriction entirely and grant everything - the failure is silent, and it fails open. The restriction is therefore the second layer, not the first: the first is that the client is run in a mode where no tool can be approved, so a tool needing approval is refused whatever the list says.
- **The text is decoded as UTF-8, with undecodable bytes replaced rather than fatal.** A client's output carries whatever the user's own material contained. A decoding error must not be indistinguishable from the model failing to answer.
- **Failure is read from both output streams.** Such clients report some failures - an expired sign-in among them - on standard output with a non-zero exit. Reading only the error stream reports these as an empty failure, which tells the user nothing and hides the one condition they could have fixed.

> These are not defensive extras. Each one was a real failure before it was a rule, and each presented as the agent simply not answering.

# 77. Authentication Capability Model
<!-- number: 77 | label: 77. | id: s77 | page: 114 -->

Each provider should expose supported authentication mechanisms.

Conceptually:

```
{
    "provider": "example_provider",
    "authentication_methods": [
        "api_key",
        "oauth"
    ]
}
```

The Settings UI should dynamically show only authentication options supported by that provider.

For example, if a provider supports only OAuth, the UI must not display an API Key input.

If a provider supports both, the user may select between the available methods where permitted by that provider.

A provider should advertise an authentication method only when the application can complete that method on the current machine.

Capability means supported and completable, not merely named. A method that can never succeed in the current environment should be omitted rather than offered and then refused.

An authentication method that the provider supports but that cannot be completed on this machine must still be disclosed. The interface must show it, mark it as unavailable, and state both the reason it cannot be completed and the remedy - rather than omitting it, which misrepresents what the provider supports.

> A method that is merely blocked by a missing prerequisite is not the same as a method the provider does not offer, and the interface must not render the two identically.

# 78. Credential State
<!-- number: 78 | label: 78. | id: s78 | page: 115 -->

Authentication state should use a normalized representation.

Recommended states include:

```
not_configured
authentication_required
authenticating
authenticated
expired
invalid
revoked
error
```

Authentication state must be accessible to:

- Settings
- Agent Core
- The agent heartbeat
- The state heartbeat
- Scheduler
- Debugger

without exposing the underlying credential.

# 79. Model Availability State
<!-- number: 79 | label: 79. | id: s79 | page: 116 -->

Authentication success does not necessarily mean that a model is usable.

The system should independently maintain model availability state.

Recommended states include:

```
available
authentication_required
credential_invalid
quota_exhausted
rate_limited
model_unavailable
provider_unavailable
network_unavailable
permission_denied
unknown_error
```

This distinction is important.

For example:

```
Authenticated: Yes
```

Model Available: No

```
Reason: quota_exhausted
```

is different from:

```
Authenticated: No
Reason: authentication_required
```

# 80. Provider Availability Check
<!-- number: 80 | label: 80. | id: s80 | page: 117 -->

The model abstraction should expose a normalized availability check.

Conceptually:

```
class ModelAvailability:
```

```
available: bool
reason: str | None
```

```
authentication_valid: bool
```

```
quota_status: str | None
rate_limit_status: str | None
```

```
provider_status: str | None
```

```
checked_at: datetime
```

Not every provider exposes exact quota information.

Therefore, the implementation should use the strongest information available from each provider.

For example, availability may be inferred from:

- Provider quota APIs
- Account usage APIs
- Authentication status
- Provider error responses
- Rate-limit responses
- Failed lightweight requests
- Model permission responses

The provider adapter is responsible for translating provider-specific failures into normalized system states.

## A Limit the Provider States

The one failure certain to end by itself must not be filed as the one nobody knows what to do about.

A provider that runs as a local client says it has stopped serving in its own words, and those words are not an error code: *"You've hit your session limit · resets 11:50pm (Europe/Lisbon)"*. Matched against a list of known phrases it said nothing - it does not contain "rate" - and a wait-and-retry became `unknown_error`.

It is recognised by the shape of what it says rather than by one spelling, because it is the client's own wording and has already been written more than one way. It is temporary, it asks nothing of the user, and **when it comes back is part of the answer**: the stated reset is kept where a caller deciding whether to wait can read it, instead of being parsed again out of a sentence.

**And a failure is reported in the provider's words, not in its telemetry.** The client prints its envelope on standard output even when it exits non-zero, and reading only the exit code threw that away - so the reason became the raw JSON, cut off mid-field by the length limits between there and the log. What the user saw was a wall of zeroed counters that did not say, and could not be made to say, that there had been no answer. Where there is genuinely nothing to quote, the report says the exit code and that there was no message.

# 81. API Usage and Quota
<!-- number: 81 | label: 81. | id: s81 | page: 118 -->

For API-based models, the system should track usage information where technically available.

Possible fields include:

```
request_count
input_tokens
output_tokens
total_tokens
estimated_cost
remaining_quota
rate_limit_remaining
quota_reset_time
```

Providers may expose only some of these values.

Missing provider information must not be fabricated.

The Debugger should distinguish between:

Known Provider Value

and:

Unavailable / Not Reported by Provider

# 82. Model Settings Interface
<!-- number: 82 | label: 82. | id: s82 | page: 119 -->

Settings > General > Models shows the models the agent thinks with, in order.

Each row names its provider and model, how hard it thinks (its reasoning effort), whether it sees pictures or reads text only, and its state - answering, ready, not connected, or why it cannot answer. Each row can be moved up, moved down or removed, and a provider whose models are read from the client offers to refresh them.

```
Models, in order
    1. <provider> / <model>   effort   [answering]   Up  Down  Remove
    2. <provider> / <model>   effort   [ready]       Up  Down  Remove
    + Add a model
```

Adding a model is one form: what kind it is (an API with a key, or a service on this computer), which one, the authentication method, the key or sign-in, and the model. It then joins the list. A table of every provider and its credential remains available below, collapsed. There are no per-subsystem override controls.

When a model billed by the token sits below a subscription service, the card warns ("Mind the order") that work will go on, and be billed, whenever the service cannot answer.

The system should validate a selected model before treating the configuration as operational.

## Provider and Model Are One Choice

A model belongs to a provider. The two settings are not independent, and the interface must not allow a combination that cannot work.

- Changing the provider must carry the model with it: a model that the newly selected provider does not offer is replaced with one that it does
- The models offered for selection are the models of the selected provider, in a form that can be stored and read back without ambiguity
- A configured model that the provider does not offer must be reported as what it is - a setting to correct, naming the model, the provider and the models available - and the request must not be sent

> Sending a model a provider does not offer produces an error from the provider naming a model the user never chose. That is a fault in the application, not an explanation for the user.

## Choosing Is Done Where Providers Are Shown

The interface that lists the providers must also be where one is chosen, and must show which one is in use.

Listing a provider, reporting it as signed in and ready, and offering no way to use it leaves the user to discover that the choice is made somewhere else entirely - in a settings list, under a name they have to know to look for. They will reasonably conclude the thing they just set up does not work.

- A provider joins the model list in one action, and its place on the list is what decides when it answers
- The model currently answering is marked as such
- Registering a credential and choosing the model are one act: the form that takes the key or sign-in ends with "Add to the list", so a provider the user has just set up is one the agent uses

## Registering and Choosing Are One Form

An earlier interface showed registering a credential and choosing the provider that answers as two separate panels. A user who completed a credential and saw the word "authenticated" reasonably believed the agent now used it, when they had only registered it.

The model list removes the gap: adding a model registers its credential and puts it on the list in the same form, and the list is the only place where what answers is decided.

- The list says in plain words that each call goes to the first model that works, and what makes it pass to the next
- The table of every provider and its credential - by what method each is registered, whether it is ready, and what it has cost - stays available, collapsed under the list
- The control that adds is worded as adding to the list, not as saving a setting

# 83. Credential Storage
<!-- number: 83 | label: 83. | id: s83 | page: 120 -->

Credentials must be managed by a dedicated Credential Manager.

Conceptually:

Agent Components

```
|
v
```

Credential Manager

```
|
+-- Windows secure storage
|
+-- macOS secure storage
|
+-- Linux secure storage
```

Other modules should retrieve authorization capability through the Credential Manager or Model Provider rather than reading raw secrets directly.

The Knowledge Graph must never be used as credential storage.

# 84. OAuth Token Handling
<!-- number: 84 | label: 84. | id: s84 | page: 121 -->

OAuth implementations may involve:

- Access tokens
- Refresh tokens
- Expiration timestamps
- Account identifiers
- Provider scopes

These values must be treated as credentials.

OAuth refresh tokens must never be included in:

- Model prompts
- Knowledge Graph nodes
- Raw Edits
- Application logs
- Debugger output

Token refresh should be handled by the provider authentication layer.

If automatic refresh fails, the model state should transition to:

```
authentication_required
```

or another appropriate normalized error state.

# 85. Authentication Events
<!-- number: 85 | label: 85. | id: s85 | page: 122 -->

Authentication state changes should generate internal system events.

The events published are:

model.available

model.authentication_required

model.authentication_failed

model.quota_exhausted

model.rate_limited

model.unavailable

model.recovered

An expired sign-in is reported as model.authentication_required; there is no separate "authenticated" or "authentication expired" event. model.recovered is published when a model that had a failure state works again.

These events are consumed by:

- Settings
- Debugger
- The sign-in prompt
- The state heartbeat, which resumes its open round at once when a model recovers

Credentials themselves must never be included in these events.

# 86. Data Heartbeat Model Availability Requirement
<!-- number: 86 | label: 86. | id: s86 | page: 122 -->

There is no longer a Data Heartbeat (section 69). Every background component that needs a model - the agent heartbeat, the state heartbeat, scheduled model tasks - goes through the model manager, and the manager checks the model before the call.

The sequence is:

Background Component Needs a Model

```
|
v
```

Model Manager Resolves the Model (the first on the list)

```
|
v
```

Check Authentication, Model Availability and API / Usage Availability

```
|
+----------------------+
|                      |
| Available            | User must act
v                      v
```

Make the Call            Refuse; pass the call to the next model on the list

```
                       |
                       v
               No model on the list can answer
                       |
                       v
               The heartbeat stops, tells the user,
               and leaves its unread activity unread
```

The agent heartbeat stops at the first refused step, notifies the user when the user must act, and does not move its "summarised until" mark forward, so the stretch is read later. The state heartbeat keeps its round open and tries again after at most two minutes, or at once when the model recovers. The stored sign-in is also checked when the application starts, so an expired sign-in is reported when the user can act on it.

This check is a hard system requirement.

# 87. Mandatory Data Heartbeat Authentication Warning
<!-- number: 87 | label: 87. | id: s87 | page: 124 -->

When any part of the agent finds that an account-based model requires login or re-authentication, the system must notify the user. The user is asked once to sign in to that provider again, whichever component found it.

The failure must not remain silent.

Examples include:

- User has never logged in
- OAuth authorization expired
- Refresh token is invalid
- Account authorization was revoked
- Required account permission is missing

The notification should clearly identify:

- Which model/provider is affected
- Why the agent cannot use it
- What action the user needs to take

For example:

Sign in to <provider> again. The agent could not use the stored <provider> sign-in. Reason: the session has expired.

Open Settings > General > Models and sign in again.

The system must not expose internal tokens or authentication secrets in the notification.

# 88. Mandatory Data Heartbeat API Availability Warning
<!-- number: 88 | label: 88. | id: s88 | page: 125 -->

When a background heartbeat cannot continue because an API-based model's capacity is unavailable, and no later model on the list can answer, the heartbeat that needed it must tell the user that it is paused and why.

This includes conditions such as:

- API key missing
- API key invalid
- API key revoked
- Account quota exhausted
- Credit/billing limit reached where reported by the provider
- Model access not available under the account
- Other provider responses that indicate continued background processing cannot occur

For example, from the agent heartbeat:

Heartbeat paused. The heartbeat model is unavailable: API quota exhausted.

Open Settings > General > Models.

The state heartbeat records why its round is blocked and when it will try again.

This notification requirement is mandatory.

# 88.1 A Block That Is a Sign-In Asks for a Sign-In
<!-- number: 88.1 | label: 88.1 | id: s88-1 | page: 130 -->

Where processing stops because a sign-in has expired or been refused, the user is asked to sign in - not told that something is paused.

Both statements are true. Only one of them names the thing the user can do. "Data processing is paused, open Settings to review the model configuration" sends someone to read a screen; "sign in to this provider again" is the single action that fixes it.

- A block whose cause is authentication raises the same sign-in request that any other component raises for the same cause, so it reaches the same prompt with the same way out
- A block whose cause is anything else - a rate limit, an exhausted quota, a provider that is down - does **not** ask for a sign-in. Signing in again fixes none of those, and asking sends the user to do pointless work
- One problem produces one message. A pause notice *and* a sign-in request for the same cause is being told twice, in two different sets of words, pointing at two different remedies

## The Prompt Does the Navigating

Offering to sign in and then leaving the user in a settings screen to find the right provider among the others leaves most of the work still to do.

- The prompt names the provider that is failing and which part of the agent it is stopping
- Acting on it opens the sign-in for **that** provider, already selected, and shows where it is on the screen
- Dismissing it stops the message, not the problem. The state remains visible where state belongs

# 89. Rate Limiting vs. Persistent Unavailability
<!-- number: 89 | label: 89. | id: s89 | page: 125 -->

Temporary rate limiting should be distinguished from persistent model unavailability.

For example:

HTTP/provider state:

```
temporary_rate_limit
```

Behavior:

the same model is tried again later according to provider policy; the call is not passed to another model (the same holds for a timeout)

versus:

state:

```
quota_exhausted
```

or a usage limit the provider states (for example a session limit and when it resets)

Behavior:

the call passes to the next model on the list

if no model on the list can answer, background work pauses and the user is told (sections 86, 88)

The provider adapter should determine whether an error is:

- Temporary/retryable
- Persistent/user-action-required
- Unknown

Persistent user-action-required failures must trigger a notification.

## What the Classification Is For

The distinction decides what the application does, not merely what it displays.

A condition the user must act on - no credential, one that is not accepted, a model the provider does not offer, an exhausted quota - means the request cannot succeed. The application says so, and does not send it.

A temporary condition is not grounds for refusing a request the user has just made. It must be attempted:

> An availability check is evidence about the past. The request is the authoritative answer, and it costs what the check costs.

- A request the user initiated is attempted when the only objection is a temporary condition, and the provider's answer is what is reported
- A successful attempt clears the condition: what is known about the model is what last happened, not what a probe once saw
- Work the application schedules for itself is different: it may defer, because deferring loses nothing and retrying immediately spends the same exhausted quota
- Where a temporary condition is met by waiting, the waiting must be bounded and visible, and must not itself generate the traffic it is waiting out

## A Status Code Is Not a Diagnosis

A response is classified by what it says, not only by its code.

A rate limit states what remains and when to return - in its quota headers, in a retry-after, or at least in its message. A refusal carrying none of that is not a rate limit merely because it arrives as one, and treating it as temporary promises the user a retry that can never succeed.

> Telling a user that the agent will retry, when nothing about the condition will change, is worse than telling them nothing: they wait instead of acting.

Where a credential is turned away for the use being made of it, that is a condition the user must act on, and the application says which credential and what to do instead.

# 90. Data Heartbeat Failure Behavior
<!-- number: 90 | label: 90. | id: s90 | page: 126 -->

Nothing marks raw data as pending or processed any more. The agent heartbeat keeps a "summarised until" mark instead, and moves it forward only when step one of a review answered.

If the heartbeat's model is unavailable:

Activity since the last answered review

```
|
v
```

Model unavailable

```
|
v
```

"Summarised until" stays where it was

```
|
v
```

Once the model answers again, the missed stretch is read in turns, oldest first, until the reviews have caught up

This prevents contextual data from being lost because of temporary authentication or quota issues. Raw activity is still removed after the keeping period (eight days by default) whether or not it was read, so activity older than that when the model returns is not read.

# 91. Data Heartbeat Notification Deduplication
<!-- number: 91 | label: 91. | id: s91 | page: 127 -->

Background heartbeats run every few minutes - the agent heartbeat every fifteen minutes by default, and the state heartbeat on its own clock - and the sign-in prompt can be raised by any of them.

The system must not display an identical warning at every run indefinitely.

The notification subsystem should deduplicate repeated model-availability warnings.

Conceptually:

First detection:

```
Notify user
```

Subsequent heartbeat:

```
Same unresolved problem
Do not repeatedly interrupt
```

State changes:

```
Notify again when materially useful
```

A new notification may be appropriate when:

- The error type changes
- A configurable reminder interval has elapsed
- The model briefly recovered and later failed again
- User action is newly required

Warnings are deduplicated by key and signature. The reminder interval is the "Repeat warning cooldown" setting (one hour by default, from one minute to one week). A dismissed sign-in prompt stays dismissed until the model works again.

# 92. Model Recovery
<!-- number: 92 | label: 92. | id: s92 | page: 128 -->

When a previously unavailable model becomes operational again, the system should update its state automatically.

For example:

```
quota_exhausted
      |
      v
```

API available again

```
      |
      v
available
```

When a model that had a failure state works again, a recovery event (model.recovered) is published. The state heartbeat resumes its unfinished round at once, the sign-in prompt is withdrawn and any dismissal of it is lifted, and the agent heartbeat catches up on the activity it could not read at its next run.

Optionally, the system may inform the user that background data processing has resumed.

# 93. Data Heartbeat Hard Invariant
<!-- number: 93 | label: 93. | id: s93 | page: 129 -->

The following is a system-level invariant:

> No background heartbeat may silently skip model-dependent work because its model lacks valid authentication, login state, API availability, or required usage capacity.

If user action is required, the user must be informed.

Activity that was not read stays unread and is read when the model returns, within the raw-activity keeping period.

# 94. Model Fallback Policy
<!-- number: 94 | label: 94. | id: s94 | page: 129 -->

The user's model list is the fallback policy. A model is used only if the user put it on the list, and in the order they chose.

The system must not replace the user's models with any provider the user did not list.

Models, in order:

```
1. Model A
2. Model B
```

Fallback Conditions (the call moves to the next model on the list):

```
A reason only the user can fix: sign-in, key, credit,
    a model not offered, a client not installed
A usage limit the provider states
```

A timeout or a busy moment is retried on the same model, not passed on. The call returns to the first model as soon as that model works.

With a single model on the list:

Selected model unavailable

```
|
v
```

Nothing changes provider

```
|
v
```

Notify user when action is required

The older single fallback setting is hidden, and is used only when the list is empty.

This preserves user control over:

- Provider choice
- Privacy
- Billing
- Model behavior
- Data routing

# 95. Model Privacy Boundary
<!-- number: 95 | label: 95. | id: s95 | page: 130 -->

Changing model providers may change where user context is sent.

Therefore, the system must treat provider selection as a privacy-sensitive configuration.

Context, including what the agent draws from its memory library (section 39.1), is sent only to models the user has put on the list.

Adding a second model is consenting to its receiving the agent's context whenever the first cannot answer. The settings card says so when the order changes who is billed.

# 96. Updated Data Heartbeat Workflow
<!-- number: 96 | label: 96. | id: s96 | page: 130 -->

Removed. There is no Data Heartbeat (section 69), and no node or edge update, compaction or "mark processed" step; they were deleted on 2026-10-06. The model check now lives in the model manager, which checks a model before each call and passes a refused call down the model list (sections 86, 94), and the waiting and retrying live in each heartbeat (sections 86-92).

# 97. Updated Security Invariants
<!-- number: 97 | label: 97. | id: s97 | page: 133 -->

The model subsystem introduces the following additional system invariants:

## Credential Isolation

API keys, access tokens, refresh tokens, and private authentication material must never enter the Knowledge Graph or model context.

## Explicit Provider Choice

The user's models must not be silently replaced by another provider. A call moves only down the ordered list the user made (section 94).

## Heartbeat Visibility

Authentication, login, or API-capacity failures that stop a background heartbeat must not fail silently.

## Data Preservation

Raw Events and Raw Edits are never discarded because a model is unavailable. They are kept for the configured period (eight days by default), removed by age whatever the model's state, and read when the model returns within that time.

## Authentication Separation

Model authentication implementation must remain separate from Agent reasoning logic.

# 98. Final Application Deliverable
<!-- number: 98 | label: 98. | id: s98 | page: 134 -->

The final Personalized Agent must be distributed as a platform-native executable application.

Providing only Python source code is not considered a complete implementation.

The project must support independent builds for:

- Windows
- macOS
- Linux

Each platform should produce an artifact that can be launched by a normal end user without requiring the user to manually install Python or run Python scripts from a terminal.

# 99. Platform Build Outputs
<!-- number: 99 | label: 99. | id: s99 | page: 134 -->
<!-- note: 2026-10-15: Russian, French, German and Italian added to the display languages - nine in all - and the whole interface shown in them from fixed tables (section 134). -->

The expected release artifacts are conceptually:

```
dist/
    windows/
    macos/
    linux/
```

Platform-specific output formats should follow normal conventions for the target operating system.

## Windows

The project must produce a Windows executable application.

The Windows build produces:

- dist/windows/PersonalizedAgent/ - the application folder, with PersonalizedAgent.exe
- PersonalizedAgent-Setup.exe - a per-user installer (Inno Setup)
- PersonalizedAgent-<version>-windows-portable.zip - the application folder as a portable archive

A release gives these files stable names (section 117).

The installer first asks which of the five display languages to use (English, Chinese, Spanish, Japanese, Korean), speaks it from then on, and starts the installed program in it. Its last page asks whether the agent starts when the user signs in, ticked by default.

*Amended 2026-10-15:* there are nine display languages now - English, Chinese, Spanish, Japanese, Korean, Russian, French, German, Italian - and the whole interface is shown in the one chosen (section 134).

The installed application should support normal Windows application behavior including:

- Start Menu entries, including one that runs the agent without a window
- Application icon
- User-level configuration storage
- Background services/processes where required
- Clean uninstall: uninstalling removes the program, its start at sign-in and its "Delegate work" entry in Explorer's right-click menu, and leaves the user's agent root in place

Released installers do not include the Debugger (section 116).

*Amended 2026-10-16:* the application is called **Personal Agent WaterMelon** wherever the user reads its name - windows, page titles, the tray, the installer, the Start menu, the uninstall list, the menu-bar name on macOS. What only the system reads keeps the old one, so a 1.0.0 installation upgrades in place: the program file `PersonalizedAgent.exe`, the installer's identity, the sign-in entry, the data folder's name, the download file names and the macOS bundle folder. Its own windows are still recognised as its own under either name. On screen the Experience tab is called Work, its debugger the Work debugger, and the module Work learning - in the tabs, the token chart and Settings - while the code, settings and addresses keep their names. The Start menu has no entry that runs the agent without a window; an upgrade removes the old Start menu folder and desktop shortcut that carried the former name.

*Amended 2026-10-16:* and its files carry the name too: the program is `PersonalAgentWaterMelon.exe`, the downloads `PersonalAgentWaterMelon-Setup-windows-x64.exe`, `PersonalAgentWaterMelon-macos.dmg` and `PersonalAgentWaterMelon-linux-x86_64.AppImage`, the macOS application `Personal Agent WaterMelon.app`, the Linux package `personal-agent-watermelon`, and a new installation's data folder `~/PersonalAgentWaterMelon`. An upgrade loses nothing: the installer's identity is kept, the old program file is removed and an entry at sign-in that starts it is counted as this program's own, an existing `~/PersonalizedAgent` is used as it is, an update installed on macOS removes the old application, and the Linux package replaces the old one.
<!-- note: 2026-10-16: the user asked for the name "personal agent watermelon" and for the Start menu entry without a window to go. -->

## macOS

The project must produce a native macOS application bundle.

Expected primary artifact:

Personalized Agent.app

Where the tools exist, the build also produces:

Personalized-Agent.dmg

and:

Personalized-Agent.pkg

The .pkg is an installer whose Installation Type page asks whether the agent starts when the user signs in (ticked by default, through a LaunchAgent). A release gives both stable names (section 117).

*Amended 2026-10-16:* the .dmg is the macOS download offered first: the application dragged from it into Applications is the user's own, so it can replace itself with a newer version, where the one the .pkg installs belongs to the system and could not without a password. Starting at sign-in is then the program's own setting, on by default, as it is for the portable folders. And the permissions macOS asks for one at a time - Full Disk Access, Accessibility, control of System Events, Screen Recording - are listed together in Settings > Privacy, each looked at without asking, each with what it is for and a button to its own pane of System Settings, Full Disk Access first because it also covers the Desktop, Documents and Downloads folders; the Overview says so while one that a switch which is on needs is missing.

*Amended 2026-10-16:* UPDATES. Once a day, and when asked, the program reads this project's published releases - never a commit, a merge, a draft or a pre-release, so a release marked pre-release reaches nobody. By default (Settings > General > Updates) the user is told, and shown every version newer than theirs with what each says of itself, and installs the one they choose - never an older one, which may not read what a newer one wrote. Installing by itself is off by default, in Settings and in the Windows installer's box, because a release that turned out broken would otherwise reach everyone within a day; chosen, the newest is installed by itself. A version being installed is downloaded into the data folder, checked against the release's own SHA256SUMS.txt - one that does not match is deleted and never run - and installed, never while a piece of work is running: on Windows the installer runs silently once the program has quit, keeping the program folder, the data folder, the language and the user's own start at sign-in, and starts the new version; on macOS the application from the .dmg is replaced; a Linux AppImage replaces itself. Or nothing is looked for at all. A copy that cannot replace itself - run from source, a portable folder, a .deb, the .pkg's application - is only told. The new version opens with what is new: the release's own description, in a window shown until it is closed.

The application bundle should contain all required runtime components.

The user should not need to manually install Python.

Where required for normal distribution, the build architecture should support:

- Code signing
- Hardened Runtime
- Notarization
- Application entitlements
- Permission declarations

The application must correctly request permissions required for functionality such as activity monitoring or accessibility APIs instead of assuming those permissions already exist.

## Linux

The project must produce a launchable Linux desktop application.

At least one broadly usable packaging format should be supported.

The Linux build always produces a tar.gz of the application:

personalized-agent-<version>-linux-x86_64.tar.gz

and, when their tools are installed, an AppImage and a .deb package:

PersonalizedAgent.AppImage

personalized-agent_<version>_<arch>.deb

No .rpm is built. A release gives these files stable names (section 117). The architecture must not require the user to manually invoke Python scripts.

# 100. Self-Contained Runtime
<!-- number: 100 | label: 100. | id: s100 | page: 136 -->

Release applications should bundle the runtime dependencies required by the Personalized Agent.

The user should not be required to manually install:

Python

```
pip
virtualenv
```

backend Python packages

before launching the application.

The build system should package the Python backend and required runtime libraries into the final application.

> remains mandatory for development and reproducibility even when

requirements.txt

dependencies are bundled into release artifacts.

# 101. Source and Release Separation
<!-- number: 101 | label: 101. | id: s101 | page: 137 -->

The project should distinguish between:

Development Environment

and:

Packaged Runtime Environment

Development may use:

Python

virtual environments

requirements.txt

development tools

debug tooling

while release builds must provide a self-contained application suitable for normal users.

The application must behave consistently in both environments.

# 102. Build System
<!-- number: 102 | label: 102. | id: s102 | page: 137 -->

The repository must provide automated build scripts for all supported operating systems.

Recommended structure:

```
build/
    windows/
    macos/
    linux/
```

or equivalent scripts such as:

```
scripts/
    build_windows.py
    build_macos.py
    build_linux.py
```

One entry point builds for the current system:

python build.py

with --platform to name it:

python build.py --platform windows

python build.py --platform macos

python build.py --platform linux

It also takes --validate-only (run the build-time validation and stop), --skip-tests, --no-installer (Windows), --dist (where to publish the artifacts), --fast (a build for trying the application out: no tests, PyInstaller's cache kept, no installer or portable archive) and --kill-running (end a running built application that holds its own files).

A build from source carries the Debugger and its tabs by default. python build.py --no-debugger leaves them out entirely, because a build for a user should not carry the agent's internals; the released downloads are built that way (section 116).

A developer packaging a build locally can do so from a dedicated environment made by build/make_build_env.py. It lives outside the repository, holds only the application's requirements and PyInstaller, pins every package to the version installed in the development Python, and uses a CPU-only torch, so the build carries neither the developer's extra packages nor a CUDA torch.

The application's icons are made from the Watermelon avatar.

However, the build architecture must recognize that certain platform artifacts can only be correctly produced on their respective operating systems.

# 103. Native Platform Builds
<!-- number: 103 | label: 103. | id: s103 | page: 138 -->

The system should not assume that one operating system can reliably generate release packages for every other target operating system.

The release pipeline should support:

Windows runner

```
-> Windows application
```

macOS runner

```
-> macOS application
```

Linux runner

```
-> Linux application
```

This is particularly important for:

- Native libraries
- Code signing
- Platform-specific packaging
- macOS notarization
- Windows installers
- Platform-specific Listener components
- Sandbox components

# 104. Build Reproducibility
<!-- number: 104 | label: 104. | id: s104 | page: 139 -->

Builds should be reproducible from the repository.

At minimum, the project must contain:

- Python dependency versions
- Build configuration
- Packaging configuration
- Required frontend build configuration
- Platform-specific build scripts
- Version metadata

A clean build environment should be able to generate the application without undocumented manual steps.

In current practice, requirements.txt gives minimum versions only. A local packaging build takes exact versions from the tested development environment, through build/make_build_env.py (section 102). The release workflow installs from requirements.txt, so its builds are not pinned to exact versions.

PyInstaller builds are not byte-for-byte reproducible, so a released file cannot be checked by rebuilding it. What ties a released file to the code it was built from is its build-provenance attestation (sections 116, 117).

# 105. Application Versioning
<!-- number: 105 | label: 105. | id: s105 | page: 139 -->

The Personalized Agent should use a consistent application version.

Example:

1.0.0

The same release version should be represented consistently in:

- Executable metadata
- Application UI
- Installer metadata
- Logs
- Build artifacts
- The Debugger, in builds that carry it

The single source of the version is personalized_agent/version.py. Released downloads carry no Debugger, so in a release the version is shown in the executable and installer metadata, the application UI and the logs.

# 106. Platform-Specific Components
<!-- number: 106 | label: 106. | id: s106 | page: 139 -->

Each build must include only the platform-specific implementations required for that operating system where practical.

For example:

Each build packages its own platform module and leaves out the other two:

Windows Build

```
personalized_agent/platform_layer/windows.py
```

macOS Build

```
personalized_agent/platform_layer/macos.py
```

Linux Build

```
personalized_agent/platform_layer/linux.py
```

Higher-level code must remain shared. Sandboxing and listeners are shared code that ask the platform module for what differs: the operating system's isolation of agent code (AppContainer on Windows, Seatbelt on macOS, bubblewrap or Landlock on Linux) lives in runtime/isolation.py, and the activity providers are shared under listener/providers/.

# 107. Packaged Runtime Security
<!-- number: 107 | label: 107. | id: s107 | page: 140 -->

Packaging the application must not weaken the filesystem security model.

The following invariant remains mandatory in packaged applications:

> No agent-controlled process may write outside the user-authorized workspace.

The packaged runtime must apply the same restrictions to:

- Main executable
- Embedded Python runtime
- Generated scripts
- Scheduler tasks
- Child processes
- External commands
- Temporary files

The fact that the application is running from an installed executable must not grant generated code unrestricted filesystem write access.

# 108. Internal Application Files
<!-- number: 108 | label: 108. | id: s108 | page: 141 -->

The application itself may require limited writes outside the user's agent workspace for operating-system-managed application state.

These writes must be narrowly defined and must not be accessible as arbitrary agent-controlled output.

Examples may include:

- Application configuration
- Credential storage
- Database state
- Application logs
- Crash information
- Installation/update metadata

A strict distinction must exist between:

Trusted Internal Application State

and:

Agent-Controlled Writes

The user-authorized workspace restriction applies to agent-controlled task output and generated execution.

Internal application components must never provide generated model code with arbitrary access to the application's trusted state directories.

Superseded in part by section 110.1: the application must not write outside the user-specified root, including for operating-system-managed application state. Internal application files are written to the internal subdirectory of that root.

The distinction this section draws between trusted internal application state and agent-controlled writes is unchanged; it is now a distinction between two subdirectories of one root rather than between two separate locations.

# 109. Internal Data Directory
<!-- number: 109 | label: 109. | id: s109 | page: 141 -->

Each platform may maintain an application-owned internal data directory for persistent system state.

For example, conceptually:

```
APPLICATION_INTERNAL_DATA/
    settings/
    database/
    scheduler/
    graph/
    credentials-reference/
    logs/
```

The exact path should follow operating-system conventions.

This directory is controlled by trusted application code.

It must not be treated as part of the agent's general writable workspace.

Model-generated scripts must not receive unrestricted write access to this location.

Superseded in part by section 110.1: the internal data directory is the internal subdirectory of the user-specified root. It does not follow an operating-system conventional application data path.

Everything else this section requires is unchanged: the directory is controlled by trusted application code, it is not part of the agent's writable workspace, and model-generated scripts receive no access to it.

# 110. User Workspace vs. Application State
<!-- number: 110 | label: 110. | id: s110 | page: 142 -->

The architecture must explicitly separate two storage domains.

## Trusted Application State

Used internally by the Personalized Agent implementation.

Examples, all under internal/:

- The database: Raw Events, Raw Edits, the scheduler and conversations
- Configuration
- Credentials
- Model usage metrics
- Internal logs

Access is restricted to trusted application components.

The agent's memory library (section 39.1) - user_nature, user_feedback, states and state_tasks - replaced the knowledge graph and is kept in workspace/data/, so that the user can read it. Only the application writes it. Agent-controlled code may read it, but may write only inside workspace/agent/.

## User-Authorized Agent Workspace

Used for files generated or modified as part of user or agent tasks.

Examples:

generated documents

generated code

```
downloads
exports
```

task output

agent-generated files

All untrusted/model-directed writes must remain in this workspace.

The runtime sandbox must enforce this distinction.

Both domains live inside the single writable root defined in section 110.1, as sibling subdirectories. Their separation is enforced by the runtime sandbox and by path validation, not by their being in different parts of the filesystem.

# 110.1 Single Writable Root
<!-- number: 110.1 | label: 110.1 | id: s110-1 | page: 142 -->
<!-- note: 2026-10-15: the data folder is chosen when installing, and a different folder is a different agent - nothing shared, nothing taken from elsewhere, and never listened to. -->

The user configures exactly one writable root directory.

Both storage domains of section 110 live inside that root, as separate subdirectories:

```
<AGENT_ROOT>/
    workspace/            user-authorized agent workspace
        documents/        the user's own (read-only to the agent)
        downloads/        the user's own (read-only to the agent)
        exports/          the user's own (read-only to the agent)
        generated/        the user's own (read-only to the agent)
        data/             the memory library (written by the application only)
        agent/            the only place agent-controlled code may write
            documents/
            downloads/
            exports/
            generated/
            .agent_runtime/   scripts, tasks, temp
    internal/             trusted application state
        settings/
        database/
        logs/
        credentials/
        characters/
        cache/
        guard/            the guard copy that child processes load
        work_children/    each work run's spec, snapshot and control files
        state_sandbox/    the Debugger's test library for the state heartbeat
```

The application's own files are written only inside the root. This applies to the application itself as well as to agent-controlled execution:

- Configuration, databases, logs, credential storage and installed packages are written under the internal subdirectory, not to an operating-system conventional application data location
- Task output, downloads, exports and generated code are written under the workspace subdirectory
- Two operating-system registrations are the exception, made the way each system expects and only in the user's own part of it: the entry that starts the agent at sign-in, when that setting is on (it is on by default) - the Run key under HKCU on Windows, a LaunchAgent in ~/Library/LaunchAgents on macOS, an autostart entry in ~/.config/autostart on Linux - and, on Windows, the "Delegate work" entry in Explorer's right-click menu, under HKCU\Software\Classes
- Neither registration holds any of the user's data. Both are written again at each start - the sign-in entry according to its setting; the Explorer entry is always present and cannot be turned off - the uninstaller removes them, and they do not travel with a copy of the root

The separation of trust is unchanged and is now expressed by subdirectory:

> Agent-controlled code may write only inside the workspace subdirectory, and may neither read nor write the internal subdirectory, even though both are inside the same root.

Because the internal subdirectory is a sibling of the workspace rather than a separate location, path validation must treat it as a protected region rather than relying on it being outside the writable area.

The root must be resolvable before any state is read, so it is supplied by the launch environment - a command line argument, an environment variable, or a documented default - and never read from a configuration file that itself lives inside the root.

Packaged applications follow the same rule: installing the application creates program files, but running it writes only inside the user's root (section 107).

## Relocatable Root

Consolidating both domains into one root exists so that the installation can be moved by copying a single directory.

> Copying the root to another path, or to another machine, and launching with the new root must restore the same installation: the same settings, knowledge graph, scheduled tasks, conversations and workspace contents.

Nothing inside the root may depend on the root's absolute location. In particular:

- Stored configuration values that point inside the root must be rewritten to the new location when the root moves, rather than left pointing at the old path
- Scheduled task script paths and any other persisted references into the root must be rewritten the same way
- Databases and packaged content inside the root must not embed the root's absolute path as an identifier

State the application deliberately keeps outside the root cannot travel with a copy. Operating-system credential storage is the expected case (section 83), so the application must make clear which state a copy does not carry, and must offer a storage mode whose credentials live inside the root for users who need the directory to be self-contained.

## External Tools and the Write Boundary

The single-root rule governs what the application writes and what agent-controlled code is permitted to write. It does not extend to a separate program that the user directs the application to run on their behalf.

Such a tool - an operating-system keychain service, a provider's command line tool performing a sign-in (section 75.2) - owns its own state, in its own location, under its own rules. The application neither writes there nor reads there beyond what the tool hands back.

> This is not an exception to the write boundary. It is the boundary: the application's own writes stay inside the root, and agent-controlled code is confined to the workspace subdirectory whether or not any external tool is involved.

The distinction the implementation must preserve:

- Agent-controlled code may never invoke such a tool, and gains nothing from one being installed. Path validation and the runtime sandbox are unchanged by its presence
- Only trusted application code, acting on an explicit user action, may run one
- What the tool returns is stored by the application in the usual place - the Credential Manager for a secret - not left wherever the tool put it

Because that state lives outside the root, it does not travel when the root is copied, and the requirement above applies: the application must say so before the user relies on it.

## The Data Outlives the Version That Made It

A database made by an earlier version of this application must keep working when a later one opens it.

Declaring a table only if it does not already exist does nothing to a table that does, so a version that adds a column finds the old table unchanged and every query naming the new column fails. The user's data is theirs and it outlasts any one version; they are not expected to delete it to install an update.

- On opening, the application brings an existing store up to the current shape, adding what is missing
- What is already there is left alone. A migration that rewrites existing rows can destroy data that the new version merely did not understand
- Adding a column is not the same as filling it. A row written before the column existed has no value for it, and reading such a row must succeed

## Writing Inside a Directory Other Things Watch

Every file the application rewrites - its settings, the user's defaults, the credential store - is written beside the original and then moved over it, so that an interruption leaves the old file whole rather than half a new one.

That final move can be refused. On some systems, any other process holding the file open - even for the instant it takes to read it - makes the move fail rather than wait. Backup and sync clients, search indexers and virus scanners all open files they did not create, and the root is a directory the user is expressly invited to put wherever they like, including inside a synced folder, because being able to move it is the point.

So the refusal is expected, and it is waited out rather than reported:

- A move that fails this way is retried for a short while before it is treated as a fault
- The user is not asked to act on it. There is nothing they could do, and the file is saved a moment later
- A refusal that outlasts the retries is a real fault and is raised, not silently swallowed - a setting the user changed must never be quietly lost

*Amended 2026-10-15:* the root - the DATA FOLDER - is chosen when the agent is installed and written beside the program (`data_root.txt`); run from source it is given with `python run.py --root <folder>`. It is found in that order: `--root`, `PERSONALIZED_AGENT_HOME`, the installer's choice, then `~/PersonalizedAgent`. A different data folder is a different agent: its own calendar, contacts, lessons and settings, and its own keys in the system's credential store - only the standard folder keeps the plain service name, so a chosen one never reads or overwrites another's keys. State from the pre-110.1 locations is moved only into the standard folder, never into one the user chose. The data folder is never listened to, whatever the directory exclusions say (`common/file_noise.py`, `excluded_folders`), and Settings shows it locked, first among the folders not listened to. A data folder must be empty, or hold only an earlier agent's data (workspace and internal): everything in it is the agent's and never listened to, so a folder with other things in it - Documents, a drive, a project - would hide the work kept there; the program refuses to start in one, and the installer refuses to choose one (`common/paths.py`, `data_folder_problem`). Only one agent runs at a time, whichever folder it uses (110.2).

# 110.2 Single Running Instance
<!-- number: 110.2 | label: 110.2 | id: s110-2 | page: 141 -->

One agent runs at a time.

Starting a second one must be refused, and the refusal must say that an agent is already running rather than failing obscurely or starting anyway.

The guard is acquired before any application state is opened - before the database, the scheduler, the Listener or the credential store - because those are what a second instance would damage:

- One database, written by two processes
- One scheduler, firing every task twice
- One Listener, recording the same activity twice
- One credential store, written by two processes

The refusal must identify the running instance as precisely as the guard can: at minimum that one is running, and where it can be seen - its process and the root it holds - so the user can find it rather than hunt for it.

> Reporting that the agent is already running is part of the requirement. Exiting silently, or failing with a message about a locked file or a port in use, does not satisfy it.

Requirements of the guard itself:

- It is released when the process ends, including when the process is killed, so a crash does not leave the application permanently unable to start
- It is not defeated by pointing the second instance at a different root
- It writes nothing outside the root (section 110.1)
- A read-only operation that does not run the agent - printing a status snapshot, printing the version - is not subject to it
- Where the user cannot see a console, the refusal is shown where they can

The running agent may start its own child processes on the same root - one for each run of the user's work (Delegate work). They are part of that one agent: started, scheduled and ended by it, and never started by the user. They do not take the guard, and they write the database only through the same code the agent uses, with each run's spec, snapshot and control files kept in internal/work_children/. A resource governor schedules them - about one run per four processor cores and per 1.5 GB of free memory, at most four at once - lowers their priority when the processor is busy and pauses them when memory is short. They end when the agent ends.

The two parts of the guard are not equally waivable. The lock on the root is what prevents two agents from writing one user's state, and nothing may waive it. The machine-wide part exists so that a second agent is refused even when pointed at a different root, and a release validation harness - which runs a throwaway instance against a disposable root, on a machine where the user's own agent may legitimately be running - may waive that part alone, explicitly and visibly.

> A validation run that cannot run while the user has the application open is a validation run that will not be run.

# 111. Generated Script Location
<!-- number: 111 | label: 111. | id: s111 | page: 143 -->

All model-generated executable scripts must be created under the authorized workspace.

Recommended structure:

```
<AGENT_WORKSPACE>/
    .agent_runtime/
        tasks/
        scripts/
        temp/
```

For example:

```
<AGENT_WORKSPACE>/.agent_runtime/scripts/task_123.py
```

**As amended by section 32**, the runtime directory sits inside the agent's own

directory rather than at the workspace root:

```
<AGENT_WORKSPACE>/agent/.agent_runtime/scripts/task_123.py
```

Generated scripts and their temporary files are agent-controlled output like

everything else the agent makes, and section 32 leaves nowhere else for the

agent to write; keeping them at the workspace root would have required a second

exception to the one rule about where the agent writes. It also puts the whole

of what the agent produced under one directory the user can inspect or remove

in one action. Both statements above still hold: the runtime directory remains

inside the authorized workspace, and never in the installation directory.

The  directory may be hidden from normal user views if appropriate, but .agent_runtime

remains inside the authorized workspace.

Generated code must never be stored inside the application's installation directory.

# 112. Packaged Application Resource Access
<!-- number: 112 | label: 112. | id: s112 | page: 143 -->

Packaged static resources should normally be read-only at runtime.

Examples:

character assets

UI resources

default configuration

```
schemas
```

built-in prompt templates

platform binaries

Runtime code should not modify packaged application resources.

Updates to packaged resources should occur through application update mechanisms rather than normal agent execution.

# 113. Build-Time Dependency Validation
<!-- number: 113 | label: 113. | id: s113 | page: 144 -->

Before producing a release artifact, the build pipeline should verify:

- Required dependencies exist
- Required platform binaries exist
- No development-only secrets are included
- Test configuration is not shipped as production configuration
- Private developer credentials are not bundled
- Platform-specific providers can load
- Required application assets exist

Build failures should be explicit.

## Dependency Completeness

A dependency counts as present only if it can run.

Bundling a dependency's importable modules is not sufficient. The packaged artifact must also contain the helper executables, native libraries and data files that dependency launches or loads at runtime, and the build must verify this rather than assume the packaging tool discovered them.

> A dependency that imports successfully and then fails when used is indistinguishable, to the user, from a broken feature.

Where a capability is offered on the basis of a dependency being present, the check that decides whether to offer it must test the same thing the capability will later rely on. Testing that a module can be imported, while the capability needs a subprocess that module spawns, will advertise a capability that cannot work.

Release validation (section 114) must exercise, on the packaged artifact, at least the start of every capability the interface advertises, so that a capability which is unavailable in the packaged build is discovered by the build pipeline rather than by the user.

# 114. Release Validation
<!-- number: 114 | label: 114. | id: s114 | page: 144 -->

A release artifact is not considered valid until it passes a clean-machine launch test.

Testing should verify that a user can:

1. Install or launch the application.
2. Complete initial configuration.
3. Select a writable workspace.
4. Configure a model.
5. Authenticate using API Key or OAuth where supported.
6. Start every authentication method the interface advertises, at least as far as it can be started.
7. Launch the desktop agent.
8. Open the Overview.
9. Open Settings.
10. Start Listener components.
11. Run an immediate agent task.
12. Create a scheduled task.
13. Restart the application and verify persistent state.
14. Verify filesystem sandbox restrictions.

The test machine should not rely on a developer Python environment.

scripts/release_validation.py walks these fourteen steps against a built artifact, not a developer checkout, using a throwaway data directory and workspace. In the release pipeline the artifact it validates is the release build, which carries no Debugger.

# 115. Platform Release Testing
<!-- number: 115 | label: 115. | id: s115 | page: 145 -->

Release testing must be performed separately on:

- Windows
- macOS
- Linux

A successful build on one platform does not imply that another platform is working correctly.

Platform-specific tests should cover:

- Application startup
- Listener permissions
- Active application detection
- Browser monitoring
- Filesystem monitoring
- Notifications
- Scheduler persistence
- Background Heartbeats
- Runtime sandbox
- OAuth callbacks where applicable
- Secure credential storage

# 116. Build Automation and CI
<!-- number: 116 | label: 116. | id: s116 | page: 145 -->

The repository should support automated release builds using CI or an equivalent build pipeline.

Conceptually:

Git Commit / Release Tag

```
          |
          v
+-----------------------------+
```

|      Automated Tests        |

```
+-------------+---------------+
              |
     +--------+--------+
     |        |        |
     v        v        v
```

Windows    macOS    Linux

Runner     Runner    Runner

```
  |        |        |
  v        v        v
OS isolation tests, for real
  |        |        |
  v        v        v
python build.py --no-debugger
  |        |        |
  v        v        v
Release validation on a clean machine
  |        |        |
  v        v        v
.exe      .pkg    .deb
.zip      .dmg    .AppImage
                  .tar.gz
```

Stable names, build-provenance attestation, SHA256SUMS.txt

```
|        |        |
+--------+--------+
         |
         v
   Release Artifacts
```

The pipeline is GitHub Actions (.github/workflows/release.yml). It runs on release tags (v*), on manual dispatch and on pull requests that touch code or build files.

- The test suite runs once, on Ubuntu, followed by python build.py --validate-only
- Each native runner - Windows, macOS and Linux - then exercises the operating system's isolation of agent code for real (AppContainer, Seatbelt, bubblewrap), builds the application from the commit with --no-debugger, so no released download carries the Debugger, and runs scripts/release_validation.py against the packaged application
- On a release tag, the files are given stable names (section 117), each receives a GitHub build-provenance attestation (actions/attest-build-provenance) that ties it to the tagged commit and the workflow run that built it, and a SHA256SUMS.txt listing every file's checksum is published with them

PyInstaller builds are not byte-for-byte reproducible, so a released file cannot be checked by rebuilding it from the source. The attestation, not a rebuild, is what ties a file to the code. Anyone can check it with:

```
gh attestation verify <file> --repo fhbzc/Personal-Agent-WaterMelon
```

The builds are not code-signed, so Windows SmartScreen and macOS Gatekeeper warn once when a downloaded file is first opened. Production signing credentials, if they are added, must be handled by the CI secret-management mechanism and must never be committed to the repository.

# 117. Release Deliverables
<!-- number: 117 | label: 117. | id: s117 | page: 146 -->

A complete project delivery must include:

Source Code

requirements.txt

README.md

Build Scripts

Automated Tests

Platform Packaging Configuration

and successfully generated platform release artifacts:

Windows:

```
PersonalizedAgent-Setup-windows-x64.exe       installer
PersonalizedAgent-portable-windows-x64.zip    portable archive
```

macOS:

```
PersonalizedAgent-macos.pkg                   installer
PersonalizedAgent-macos.dmg                   disk image with the .app
```

Linux:

```
PersonalizedAgent-linux-x86_64.deb            package
PersonalizedAgent-linux-x86_64.AppImage       single-file application
PersonalizedAgent-portable-linux-x86_64.tar.gz   portable archive
```

and, with them:

```
SHA256SUMS.txt                                the checksum of every file
```

The released downloads are built by GitHub Actions on Windows, macOS and Linux runners, from the tagged commit, with --no-debugger (section 116). Each carries a GitHub build-provenance attestation, which can be checked with gh attestation verify <file> --repo fhbzc/Personal-Agent-WaterMelon. The files are not code-signed, so SmartScreen and Gatekeeper warn once.

The open-source release repository is github.com/fhbzc/Personal-Agent-WaterMelon, under the MIT licence.

The project is not considered complete if only development source code is provided.

# 118. Updated Final Deliverable Requirement
<!-- number: 118 | label: 118. | id: s118 | page: 147 -->

The final implementation must satisfy all of the following:

Source code available

```
+
```

Python dependencies versioned

```
+
```

Windows build succeeds

```
+
```

macOS build succeeds

```
+
```

Linux build succeeds

```
+
```

Applications launch without system Python

```
+
```

Filesystem sandbox remains enforced

```
+
```

Model authentication works

```
+
```

Listener works

```
+
```

Agent heartbeat and state heartbeat work

```
+
```

Scheduler survives restart

Only after these requirements are met should the implementation be considered a complete Personalized Agent release.

# 119. Delegating Work
<!-- number: 119 | label: 119. | id: s119 | page: 148 -->
<!-- note: 2026-10-18: a run is done only when every core promise was tried and held, and when two independent final checks - quality and privacy - both pass; the Work tab shows the agent's own work, not what was learned; a run's own notes are in English, what it delivers in the display language; the full review is the reviewer's to ask for and a version is a checkpoint; what the user did not say is added as added. -->

Work comes to mind wherever the user is: looking at a folder, reading a page, scanning their to-do list. **Delegate work** is available in every one of those places, and every route ends at the same draft: the context is collected, a plan is drafted, and nothing runs until the user has read it and pressed Start.

The subsections say how a start is drafted (119.1), how what bears on the work is found (119.2), where work is handed over from (119.3), and what every run that made something leaves behind it (119.4).

*Amended 2026-10-18:* NOT DONE UNTIL EACH CORE PROMISE WAS TRIED AND HELD. A final review that found nothing to fix ended a run done even when the tester had just said a core promise was "untried" - seen with a client whose tester could not open the file. Now a core promise that did not hold, or that nobody could try, is a high finding of the final review, named by its promise (`Run._final`), and goes to the same rounds of fixes; still unverified after them, the run ends not finished, saying which promise was never tried for real. One only the user can check stays theirs, on their list.

*Amended 2026-10-18:* TWO FINAL CHECKS, BOTH TO PASS. After the final review, two reviewers who never saw how the work was built look at it, each in a context of its own (`Run._final_checks`): the QUALITY CHECK opens and tries it and says what the result should be, never how to write it; the PRIVACY CHECK looks through everything it made - code, documents, slides, data - for the user's private information or another real person's that the task did not ask for, for anything a program sends away that was not asked for, and for anything it installs or leaves outside its own folder (a startup entry, a service, a browser setting, a page reachable from the network). Each passes only when it could open what it checks and found nothing high or medium; one that could not look has not passed. What they find goes to a round of fixes, at most three for each, and both look again; one still failing after its three ends the run "failed a final check", saying which and what it found - and it can be continued. Every run is checked so: the user's own, and each way of the Work debugger, the single model too, which is given what they found and goes on until both pass. Each check is a kind of its own in the token charts - a line of its own in a run's chart of calls, and a task of its own in the application's (`work_quality_check`, `work_privacy_check`).

*Amended 2026-10-18:* THE WORK TAB SHOWS THE AGENT'S OWN WORK. What was learned of the user's own work on a project - its stages and what they show of the user - is no longer drawn there: it is kept, and the agents use it, as the lessons are (section 124). The panel beside the projects shows the agent's own work on the one chosen, its runs and their stages; each project is still learned from its row.

*Amended 2026-10-18:* A RUN'S OWN NOTES IN ENGLISH, WHAT IT DELIVERS IN THE USER'S LANGUAGE. The builder and the reviewers write their own notes on the work in English - the cards, the stages, the questions and their answers, the findings, the summaries, the version descriptions and the reports the Work tab shows - as everything else the agent keeps for itself is (section 119.2, as amended). What a run delivers is in the user's display language: the documents, pages, slides, messages and other files it makes, the words inside its programs that their users read, and the README (section 119.4) - unless the user's instruction asks for another language, and then in that one. Code itself is written as code is. It is said once, at the top of every call of the run (`WORK_LANGUAGE`), the display language first and the English notes as the exception to it, so the Language debugger shows it as sent.

*Amended 2026-10-18:* THE FULL REVIEW IS THE REVIEWER'S TO ASK FOR, AND ONLY A CHECKPOINT IS A VERSION - the user's decisions. There is no full review every five rounds any more: with each round's review the reviewer says whether one is due now, at a milestone - a deliverable done, a core promise that now holds, the work's shape changed, a point worth coming back to, or a doubt that work done before still holds - and the application gives it what it can see as hints: the last checkpoint and the rounds since, whether the work agent says a card or the whole goal is done, the core promises not holding before the round, the deliverables done since; the call is the reviewer's. A full review covers EVERYTHING SINCE THE LAST CHECKPOINT, not the round alone: the rounds since, each with what it set out to do and came to, the files changed since (against the checkpoint's version), the cards done before it - each card those rounds touched checked against its criteria, every core promise tried again, a done card whose files changed checked again, and a card that no longer holds opened again; it is the only review that may end the run. It is one answer with room for it (36 rounds of tools), or, when more than six cards were touched, an answer a card and one that tries the promises and concludes. PASSED, IT IS A CHECKPOINT, and a version is kept; not passed, none is, and the work goes on with what it found. The versions a run keeps are its checkpoints alone: the contract approved, the contract amended by the user's new words, each full review passed, and the run done - not each round, not the final review or the deck (kept with the run done), not a pause, a stop or a limit. A run cut off - the application closed under it - goes back to its last checkpoint, as it went back to its last milestone before, and work since is done again; a pause goes on from where it stopped, its to-do list in its plan. Only a checkpoint can be gone back to. One model on its own has no reviewer, and keeps a version a turn as before.

*Amended 2026-10-18:* WHAT THE USER WOULD EXPECT AND DID NOT SAY IS ADDED, AS ADDED - the user's decision. Reviewing the contract, the reviewer lists what the work should hold that the contract leaves out, from the user's past work and what they asked, each with what it rests on, in three kinds: "implied" - what their own words already call for (a payment that fails charges nothing) - joins that core promise as a failure it must survive; "needed" - without which what they asked would not serve them - is a supporting deliverable, done with the core; "nice" is a polish deliverable, done once every core promise holds. None is a core promise of its own: those are the user's words alone. The work agent says what the work showed the user would need, with what showed it - a file, a record, a result - and the reviewer decides whether it is added and as which kind. Each is recorded in the contract as added, by whom and on what, shown on the Work tab as "Added - not your words", and the user may take each out (on a run not working at the time): its deliverable is set aside, or the failure it added to a promise dropped, and the record keeps that it was. A finding the reviewer queues on a deliverable is now a part of it - before, it became a deliverable of its own, outside the contract and with no tier; one queued on the goal itself is added as a polish deliverable, as above. The work agent still never sees the user's past work: only the plain criteria the reviewer writes from it.

# 119.1 Every Start of Work Goes Through a Draft
<!-- number: 119.1 | label: 119.1 | id: s119-1 | page: 148 -->

*Modules: `agent/work/delegate.py`, `api/routes.py` (`/api/work/drafts*`), the Delegate work box in `api/web/app.js`.*

No work starts directly any more. Every start, from whatever place, makes a **draft** (a `work_drafts` row) and goes through four steps:

1. **The context is collected**, in a process of its own (section 120). It holds what was handed over; the window it came from, when text was selected in one (its title, its address, and the words the listener last read off the screen, never by signing in); the public page at that address, read as anyone could read it; the folders given (their entries and a README if there is one); the start of each file given; the project or task the work is for; and what was **found** of the user's own records by the work's words (section 119.2).
2. **A high-level plan is drafted** from that context in one model call, in the display language. It is not the detailed plan, which stays the run's contract. It is only enough for the user to see at a glance whether the task was understood: a title, what the agent understands them to want, the goal, three to seven main steps, the deliverables, what is out of scope, and any question that would change the plan. The plan is drafted from what was collected and nothing else, so it comes back quickly.
3. **The user reads it, edits any part, and presses Start, or cancels.** Nothing runs before that.
4. **The run is started** with the approved plan given to it word for word, as the user's own (`instruction_of`), together with what was handed over and the references found.

Two rules apply to every plan, whatever the model returned (`plan_of`). A plan with no title takes the first words of what was handed over. A plan whose deliverables do not mention a README has one added, because every run that makes something writes one (section 119.4). The draft also records whether the plan that started was the agent's or one the user changed (`plan_by`).

The Add work button on a project or task, the to-do list, the avatar's menu, Explorer and selected text all open the same box. The page never calls the old direct start route. Only continuing an interrupted run skips the draft, because that run already has an approved plan and a contract. Work started from a project or task goes on that one. Work handed over from anywhere else becomes a project of its own (`create_work_project`, then `mark_delegated`). If granting a folder or starting the run fails, that new project is taken away again (`drop_workspace`), so a failed start does not leave an empty project behind.

# 119.2 What Bears on the Work Is Found, Not Guessed
<!-- number: 119.2 | label: 119.2 | id: s119-2 | page: 148 -->
<!-- note: 2026-10-15: Russian, French, German and Italian added to the display languages - nine in all - and the whole interface shown in them from fixed tables (section 134). -->

*Modules: `agent/work/context_search.py`, `agent/work/tools.py` (`search_records`), `agent/work/delegate.py` (`collect`, `instruction_of`).*

The records that bear on a piece of work are the ones that match it, from any time. Matching is harder than it looks: the user may write in any of the five display languages, but what the agent keeps is in English - states, the English copies of tasks, the keywords of stages. So the search has two steps:

*Amended 2026-10-15:* there are nine display languages now - English, Chinese, Spanish, Japanese, Korean, Russian, French, German, Italian - and the whole interface is shown in the one chosen (section 134).

1. **One model call turns what was handed over into English search words** (`english_words`): the text, the names of the folders and files, the page title and the name of the project. It returns 8 to 14 English keywords and phrases plus a one-sentence summary. Names of files, people, products and code keep their own spelling. Without a model, the words of what was handed over are used as they are.
2. **The records are searched with BM25** (`Index`, with the usual constants k1 = 1.2 and b = 0.75). The index covers every kept state, every live task (its English copy, falling back to its own words) and every live stage (its English keywords).

A match counts only if its **relevance**, meaning the share of the query's weight that the record matches, is at least **0.12**. That threshold was set on real queries of 8 to 14 words: records that clearly bore on the work scored 0.2 to 0.3, related records 0.12 to 0.2, and records that only shared a common word scored under 0.1. The matches that pass are ranked by relevance multiplied by **recency**. Recency halves every 21 days but never drops below a quarter, so an old record that is clearly on the subject still counts. At most twenty states, five tasks and five stages are kept.

What was found appears in the draft beside the plan, each item with its relevance and recency. The run receives it as a list of references in its instruction, most relevant and most recent first. A one-off search is not enough once the run starts on the details, so the run also gets a tool, **`search_records`**, which searches the same records by English keywords whenever it needs to. The tool builds its index once per run and returns at most twenty matches. It is offered only where a run has the user's records: the single-model variant of the Experience debugger has none, so it is not given the tool.

*Amended 2026-10-16:* the Work debugger (the Experience debugger as it was called) tries the same instruction again in TRIES: the folder the user chose holds `try_1`, `try_2`, ..., each with its three ways inside, and a new try is made with a button and shown. Each try is blind to every other: its ways may not read any other try's folder or anything under it - an evaluation the user holds in one try's folder included - nor what Claude Code or Codex sessions started anywhere under another try kept of themselves (`~/.claude/projects/<the folder>`, and each Codex session file that ran there), refused by the run's own checks and, on Windows, by the operating system. Each try's ways have their own runs, trees and versions; the first try keeps the names projects had before tries, and a project made before tries has its ways moved into `try_1` - with its runs' records - the first time it is shown with none of them going. One gap is known: Codex, as the model, runs its own commands in a sandbox that keeps it from writing but not from reading, so what it reads by itself is not confined. Until Codex runs inside the run's own isolation, no call of a Work debugger run is given to it: the next model on the user's list answers, and with none but Codex the run says why it cannot go on.

*Amended 2026-10-17:* a try is measured, so it is kept from everything that would let it tune itself to the measuring: beyond the other tries and its try's other ways, it may not read the rest of its own try (what the user writes at the top of a try is their evaluation of its ways), nor the sessions Claude Code and Codex kept of work in any try but its own way; nor the agent itself - its code (the checkout, or an installed copy's whole folder), its data folder's workspace (what it learned of the user, as files), and the sessions of work in either, among them the user's sessions with the assistant that builds the agent. The top of the project's folder stays readable: it is where the user puts what the work is to be done with. A try still draws on the user's experience through the agent's tools, never as files. None of this applies to real work.

*Amended 2026-10-17:* and every run - real work as much as a try - may read the record of the user's Claude Code and Codex sessions (`~/.claude/projects`, `~/.codex/sessions`): a lesson can come from one. The rest of those folders - their sign-ins, settings, history and caches - stays closed with every other store of sign-ins in the home. Those records hold whatever was said in them.

*Amended 2026-10-17:* and a try's way installs its packages with a download cache in its own folder, not one beside the ways: a shared cache told one way what another had installed. Real work keeps one cache for all its runs.

*Amended 2026-10-17:* a try can be REMOVED, after the user confirms: its folder and everything in it - what its three ways made and what the user wrote at its top - is deleted, its runs and its ways' trees are forgotten, and the sessions Claude Code and Codex kept of work in it are deleted too, so a try later given the same number is not handed them as its own. The other tries keep their numbers; the only try left cannot be removed (Clear all empties it).

*Amended 2026-10-17:* what a step of the work is given from the user's experience is held to what bears on it. A past stage counts only if it shares at least two of the step's words that tell work apart: a word found in more than a tenth of all stages counts for nothing (once there are fifty stages to judge that by), and so do a few that say nothing ("per", "via", "app"). Its words decide whether it counts at all; what kind of stage it is, its place and the rest only order those that do. Around each stage found, one stage before and one after, not more. A nature comes with it when the step shares two of its telling words; a pattern or principle only when it bears on the step as much as a third of the best one. No principle is handed over just for being the most shown. And a stage from a PROJECT LIKE THIS ONE ranks higher: this work's goal, in English words, is put against each past project's stages as a whole, with each word counted by how rare it is and how many of that project's stages say it. The projects scoring at least half as much as the closest one count as alike, more so the closer they come. Measured again on the five questions of a real run: about a third of the tokens it was given before (4 to 7 thousand rather than 16), with the stages of the like project moved up.

*Amended 2026-10-17:* a project's learning is updated BY ITSELF at the times of day the user chooses, in this computer's time zone ("03:00" unless changed; none for never), as an issue of the Newspaper is made. The run takes every project that has changed since it was last learned: a task of it has states not yet learned, or a file in one of its folders (or one of its sessions with a coding agent) is newer than how far the folder was read. Each project has its own tick, on by default. While the run goes, a project's Start and Update wait. Cut short by the application closing, the run carries on when it starts again, each project from where its learning stopped. And a project can be REMOVED, after the user confirms: its stages and cards, and their links, are deleted, found by no search again, and it is not listed or learned again. The natures, patterns and principles stay, because another project may rest on them and what they say of the user is still true.

*Amended 2026-10-17:* the projects of an automatic update are learned side by side, as many as the one pool gives places (section 120). When the run is over and its projects wrote natures of the user's work, the patterns and principles are built again from them at once - the state heartbeat's step, asked for then rather than when enough new ones have come in.

*Amended 2026-10-16:* "Try the retrieval" - what a run would be given of the user's experience for a step - is the Work debugger's, at its foot, and no longer in Work: it is a tool for whoever develops the agent, and a build without the Debugger has none of it.

*Amended 2026-10-17:* a run's token chart is drawn across either the clock or the run's OWN TIME - what it had run when each call ended: the time it was held paused and the time it waited for the user's answers left out, and the time between its sessions too, each session's own time carried on to the next. Each call is kept with that time (`active`); for calls kept before it was, it is estimated by leaving out every gap longer than a pause, and the chart says so. Across its own time there are no breaks to draw, and the ticks say how long it had run. Which of the two is one choice, kept in the browser.

*Amended 2026-10-17:* a slide's notes sit beside it as sticky notes, as a note sits beside a reply in Chat - one for what it shows, its key terms, how to read it and what to take from it, each folded from its title - in a frame of the page as when shown alone; the frame is as tall as the slide or its notes, whichever is taller, so nothing in it scrolls. And every drawing has its own arrowheads: one shared by all of them took every arrow's head away whenever the first drawing on the page was hidden.

*Amended 2026-10-17:* THE SAME CARD IS GIVEN THE SAME PAST WORK. What a run's reviewer is given of the user's experience for a card - when its criteria are written, and when a round on it is reviewed - is kept the first time, in the application's database (never in the run's plan, which goes with its versions into the work's own folder), and given again when the card is opened again, reviewed again, or carried on after the application closed, even while learning changes the record meanwhile. And nothing handed over for one card is held against another: a stage given before no longer counts half - each reviewer's call is new and holds none of what an earlier one was given, so the half only gave later cards weaker stages.

*Amended 2026-10-18:* THE WORK DEBUGGER'S SECOND WAY DIFFERS FROM THE FULL ONLY BY THE CONTEXT. Its reviewer had instructions of its own, shorter than the full way's, and among what they left out was what the full way's reviewer is held to first: what the user asked comes first - the core promises - and no work is added that they did not ask for. The difference measured was then partly a different way of working. Now the second way's reviewer has the same rules, without those about the user's past work; it searches the web as the full way's does, and searches nothing of the user's own record, and is given none of it. When the reviewer comes to ask its questions and answer them, the second way will ask them too, answering from the web alone.

*Amended 2026-10-18:* WHAT LEARNING WRITES IS IN ENGLISH. Every stage and card learned of the user's work, the links between them and the natures written with them are in English, whatever the user reads in (`stages.STAGE_LANGUAGE`): they are the agent's own - no longer shown to the user - read by its reviewers, matched and searched in one language, and the reviewer's own search of them was already asked in English. The user's own words, where a stage quotes them, stay as they wrote them. What was learned before stays as it was written until the project is learned again; what a run of Work writes for the user to read is in their display language, as before.

*Amended 2026-10-18:* AND STILL FOUND BY THEIR WORDS. Written in English, the stages could be read by the small English model the states are grouped with (section 37), and finding them by meaning was built and measured on the user's own record - 774 stages, asked with the steps of 187 of its own cards and of 93 cards of new work: words found something every time, meaning left four of the five best in place for new work and found nothing words had not, and it cost a second and a half a search, three minutes to read every stage the first time and the model's own process beside each run. The user decided against it: stages are found by their words alone, no embedding model is used for them, and nothing of it was kept.

*Amended 2026-10-18:* LESSON CARDS - WHAT WAS ASKED AND ANSWERED (`heartbeat/experience/lesson_cards.py`), the first part. A card is one answered question of the user's work: the question in English, the context it came up in (the project, the run, the card of its work, why it was asked), the specific answer, and the evidence it rests on, each piece typed - what the user said, the user's own code, a decision the user made, or the model's own knowledge. Its tier follows: from the user, or an assumption. NOTHING RESTING ON THE WEB IS KEPT, even partly: a page may be written to steer whoever reads it, and a lesson is read by every later run - so such an answer is used in the run that found it and gone, and a package from another computer cannot bring one in either. A card is never edited: a change is a new version that supersedes it, with the same lineage, the old one kept and marked. There is no fixed set of tags: what kinds of file and which stack the evidence points to, and the project, are worked out from the evidence; the topics are free words the model proposes. A card is found by its QUESTION, against the question asked - each word worth what it is rare among the questions kept, Chinese, Japanese and Korean in pairs of characters - one from the same project counting for more; the questions alone can be given too, to prompt questions a reviewer would not think of. Each use is recorded: which run, which card of its work, what it decided, which files. The cards are carried in the Lessons package with every version, and shown in the Lessons debugger. NOT YET: the reviewer writing and reading them as it works, and their upkeep with the state heartbeat's round - both change how a run is reviewed, and wait for the user's word.

*Amended 2026-10-18:* LESSON CARDS IN A RUN - the user's decision. Before it writes criteria - of the contract, of the contract amended by the user's words, of each card as it opens - the reviewer is asked, IN AN ANSWER OF ITS OWN WITH NO WEB, what it must know of the user to judge the work as they would: its tools to read the user's files, records and past stages, and no web search, no page and no client's own web tool. It asks what will serve later work too - their preferences, conventions, constraints, environment and the decisions they make again and again, in any kind of work, not only code - and not facts of this work alone, what their instruction already says, or guesses. WHAT IT ANSWERS THERE MAY BE KEPT, each piece of evidence checked by the application: the user's words only when they are in the instruction, their file only when it was read in that answer with the words quoted in it, a decision of theirs only on a stage it was shown or opened - anything else is the model's own, an assumption. What it leaves open it answers as it writes the criteria, the web too, for that work only: nothing found there is ever kept. ONLY THE USER'S OWN WORK KEEPS THEM: each answer a candidate card - none resting on the web, none for a question a card shown already answers - and a card it names as replaced gets a new version (`Run._what_to_know`, `Run._keep_lessons`). The full way's reviewer is SHOWN the cards whose question is most like what it is asked, after the experience retrieved for it and kept with it, so the same card is given the same ones every time; each the criteria cite is recorded as a use, by the user's own work only. A Work debugger way of the whole system reads the user's cards in the same way and keeps nothing - no card, no new version, no use; the way with no context takes the same two steps with no card shown and nothing kept; one model on its own has no reviewer. The questions and answers are the agent's own notes, in English. Still to come: their upkeep with the state heartbeat's round.

*Amended 2026-10-18:* THE USER'S OWN FILES ARE SEARCHED BY THEIR WORDS - THE LOCAL INDEX (`index/`). What was found of the user's past work was what learning had made of it; the files themselves - how they actually wrote a thing before - could only be listed and opened by name. Now the folders the user works in are kept searchable: every Work project's folders by default, the folders the user adds in Settings > Search your files (each with everything under it), and, on one click, the records of their Claude Code and Codex conversations; no other folder of theirs is read, and the agent's own data never. Each text file is kept as the words of each forty lines (SQLite FTS5: Latin words, and Chinese, Japanese and Korean in pairs of characters, so no dictionary or model is needed), every other file by its name; caches, environments, version control and the places keys are kept are never gone into, a file named like a secret is never read, and a file only in the cloud is kept by its name without being downloaded. It is built in a process of its own at the lowest priority the system has - background mode on Windows, nice 19 with the disk idle or throttled on Linux and macOS - waits while the computer is on its battery, and reads again only what changed: a few minutes after the application starts, every few hours, and soon after the folders change. The agents search it with `search_files` - the full way and the user's own runs; not the single model, nor the way with no context, which are compared by having none of the user's context (that way's builder no longer has the search of the user's records either). Only what that agent may read is ever given back - the same rules as `read_file` - and what it finds and opens goes to the model it works with, as every file it reads does; the Settings card says so.

*Amended 2026-10-21:* LEARNING STARTS AGAIN, AS CASES AND REASONS (`learning/`, the user's decision; the agreed design is kept beside this document, `docs/learning-and-work.md`). What was learned before - the stages each project was learned into, the natures written from them, their patterns and principles, the lesson cards - is backed up once, whole (the database and the natures' folder, in `internal/backups/before-new-learning-<time>`), and no longer read for the user's work; nothing is made from it. A project is no longer learned by its own button. LEARNING reads the user's work - their Work projects (the state heartbeat's projects of the work kind, with their folders) and the PLACES they list in Work's Learning panel: folders, and with one click their Claude Code or Codex records; a record folder brings the project folders its conversations worked in (where a session started - or, started in a folder that holds everything, the project folders of the files it read and edited), and a folder brings every conversation that worked in it. A listed place is cut into units by a model, bottom up: each folder with no folders in it summarised, then each folder above judged from its own files and its children's summaries - which children are one thing (attempts at it, or the small tasks of one larger piece of work), and whether the folder covers most of what is in it; a folder that does not stops the climb. A unit is read in the order things happened - the user's words in their conversations whole, the agents' replies short, the agents' edits; the rules written for the agents (CLAUDE.md, AGENTS.md, Claude Code's memory of the project); the files, the first time as what is there and the start of the latest deliverables, after as each change against the copy kept when last read, a change the agent's where the lines it added are lines an agent's edit put there and the user's otherwise; the commits; for a Work project, its tasks - every word with keys, tokens and passwords taken out first - into CASES (a deliverable: what it is for and who for, in the shared words; where it is; what it looks like; how it was made) and the user's JUDGMENTS in them (what they decided, changed, turned down, approved or let stand, with their words and the date). What an agent decided, and anything a Work run does, is never learned. Then REASONS (`learning/reasons.py`): each new judgment is explained by the reasons that bear on it, picked by the shared words, without a word of them changed; a reason a judgment conflicts with is revised - a boundary, another reason winning there, narrowed or replaced, a one-off, or the user having changed from a date on - and a rewrite is held to all its evidence (none of the user's own words lost but what a change over time set aside, at least four fifths of the rest kept, a prediction that came true counting twice), refused twice it stands as a rival until the evidence tells them apart; what no reason explains is read together for a new one, falsifiable and as deep as can still be shown wrong. No weight fades by age: every judgment carries its date, and the model judges what is old. The shared words have a fixed top in each dimension (what the deliverable is, who it is for, how it reaches them, its purpose, its tools, and what in it a judgment is about); a word a model adds is on trial until a second case uses it, and folded back only after ninety days and a hundred judgments in its branch.
<!-- note: 2026-10-21: learning starts again (old lessons backed up, nothing extracted): the user's Work projects and listed places with their Claude Code / Codex records, read into cases, judgments and reasons; no per-project button. -->

*Amended 2026-10-21:* AN UPDATE, AND ITS CLOCK. Learning is updated at the times of day set in Work's Learning panel - several may be set - ONLY WHILE ITS SWITCH IS ON AND A TIME IS SET (off and none at first), or with Update now. An update reads up to its cutoff and keeps that cutoff through a pause, a day's limit (`learning_daily_tokens`) or the application closing; a time missed while the computer was off starts one when it is back, with that time as its cutoff; a time that passes while an update is open is skipped, and said so, and the next waits for the next time. One update at a time: Update now is greyed while one is open, Pause stops it until Continue. A first reading of a unit goes back six months first and reads what is older at the end. The panel shows how far it is, by the amount read, the places and what they bring (each found folder can be left out), and a bar a day of what learning spent.
<!-- note: 2026-10-21: Work's Learning panel: switch + times (off, none by default), Update now / Pause / Continue, a daily token limit, the places, the spending. -->

*Amended 2026-10-21:* WHAT A RUN'S REVIEWER IS GIVEN: ITS QUESTIONS, ANSWERED. Before it writes criteria - of the contract, an amended contract, each card - the reviewer writes, with no tools, the job's terms in the shared words and its QUESTIONS, each of a kind: an extra requirement the user would expect, the output's form and style, how a result is judged, how the work is done, a fact of the project. The work agent may ask too, with its round, and the reviewer answers. Then (`learning/find.py`): the reasons whose condition covers the job by its terms - or misses by one neighbouring term, or depends on what the job does not say - each read by a model against the job (holds, does not, or a question for the reviewer); a reason the user stated first, an inferred one only on two observations and said to be inferred; and, whatever reason they bear on, the cases most like the job - three in full (where the deliverables are, what they look like, every judgment, how they were made), diverse and covering the same deliverable and the same purpose, the next as an index - and what in them no reason explains yet. It may open more (`open_case`, `open_reason`) and search the user's files. It answers each question with where the answer comes from, strongest first: a reason the user stated, one inferred, common practice from the web chosen by the user's related preferences, common practice alone, its own assumption; what the record cannot answer is searched on the web, the queries framed by the user's situation and related preferences in general public words. A rule a funder or journal sets wins over a preference; general practice gives way to the user's reasons. What it writes is marked as its own. Every question and answer is logged with the run (`work_run_questions`), for reading back, and nothing of it is learned. Lesson cards are no longer written or given; those kept are read-only. The debugger's way with no context asks the same questions and answers them itself; the work agent never sees what was learned.
<!-- note: 2026-10-21: the reviewer asks questions first and is given reasons and cases for them; tiers of answers; web search framed by the user's context; lesson cards stopped; the builder may ask. -->

*Amended 2026-10-21:* THE LOCAL INDEX READS WHERE LEARNING READS: one list, the Learning panel's - the Work projects' folders, the places listed and what they bring, the agents' records when listed. The Settings card "Search your files" is gone.
<!-- note: 2026-10-21: the local index's folders are the learning's places; its Settings card removed. -->

*Amended 2026-10-21:* A RULE IS THE USER'S ONLY ON EVIDENCE (`learning/authorship.py`). The two strongest kinds of evidence are held by code, not the model's say: "a rule the user wrote" only for a line of a rules file itself (CLAUDE.md, AGENTS.md - never a README) that is quoted and is the user's - a line an agent's edit put there (its `/init` among them) is the agent's, in a repository each line is its commit's (one signed as made with an agent is the agent's), and where nothing at all is known of who wrote a file its lines are no one's; any of those is at most what the user let stand. "What an agent noted from the user's words" only for an entry of an agent's memory whose source is found - the last thing the user said before the agent wrote it - else as something they said yes to.
<!-- note: 2026-10-21: a rules file's line counts as the user's only on evidence (agent edits, /init, git history); README never; an agent's memory counts only with the user's words it came from. -->

# 119.3 Handing Work Over From Anywhere
<!-- number: 119.3 | label: 119.3 | id: s119-3 | page: 148 -->
<!-- note: 2026-10-16: the user asked for a keyboard shortcut, Ctrl+Alt+D, set in Settings > Shortcuts, that opens the Delegate work box empty, to be filled in; right-click entries in other programs (a browser extension, macOS Services, file managers, editors) were planned and put off, the stores and signing not yet bought. -->

*Modules: `platform_layer/explorer_menu.py`, `agent/work/handover.py`, `__main__.py` (`--delegate`), `ui/selection.py`, `ui/desktop.py`, `heartbeat/experience/store.py` (`mark_delegated`), `agent/work/delegate.py` (`output_folder_for`).*

- **Explorer's right-click menu (Windows).** A "Delegate work" entry appears on files, folders and the empty part of a folder, labelled in the display language and with the avatar's face as its icon. It is written only under the user's own `HKCU\Software\Classes`, so no administrator is needed, and it is written again at every start so that the face and the words stay current. The uninstaller removes it (`--explorer-menu off`). When several items are chosen together, Explorer starts the program once for each of them (`--delegate PATH`). The first process leaves a note in `delegate_inbox` and waits 1.5 seconds while the others add their paths to it under a lock. It then starts the agent if the agent is not running and opens the page through a single-use sign-in link. The page *takes* the note, which reads and deletes it, and makes a draft from it. In the draft, a folder becomes one the work may change and a file is named in the request, with its folder as the place the work is done. The note holds only paths, and a note left untaken for an hour is discarded.
- **A button beside selected text (Windows).** When the user finishes a drag of at least six pixels or a double-click, the focused control is asked what it has selected **through UI Automation**, the interface screen readers use. The clipboard is never touched and no key is pressed: Ctrl+C would overwrite what the user had copied, and in a terminal it would stop the running program. Password fields and the agent's own windows are never read. A small button appears for six seconds without taking the focus. The text is held in memory only while the button shows and is dropped when it disappears, unless the user clicks it.
- **The avatar's menu** opens the box empty, for the user to say what they want done.
- **The to-do list** on the front page has a delegate button on each item, which hands over that to-do's text.

*Amended 2026-10-16:* A KEYBOARD SHORTCUT, Ctrl+Alt+D unless the user sets others in Settings > Shortcuts, opens a QUICK DELEGATE WORK BOX, empty, on the screen at once, from whatever program is in front: the agent's own window, drawn as the avatar is, with a Title and a Description, Add folder and Add files, and Create - for the page's box, opened in a browser window of its own, took three and a half seconds of the browser starting before anything showed. Create makes the piece of work and opens the page on it, its context already being collected and its plan drafted; the title the user gave is the work's, said to the planner with the description and kept as the plan's title (and so its folder's name) in place of the one the planner would have given it. Nothing is read from the program that was in front. The keys are held with the system while the agent runs (Windows, RegisterHotKey): a combination another program already holds is refused and said so, and the keys before are kept; a shortcut needs Ctrl, Alt or Win, can be turned off, and goes back to its default with a button. The same page turns the button beside selected text on or off. On a Mac and on Linux the page says it is not available yet.

Delegated work is listed under its own heading, **"Delegated work"**, in the Experience tab, separate from the projects the agent found from the user's activity.

Delegated work given no folder still needs somewhere to write. Its output folder is `agent_work/<the work's name>`, using the plan's title with the characters a folder name cannot hold removed. If a folder of that name already exists, the date and time are appended to the name, with a counter if that name is taken too. Two pieces of work with the same name are told apart by when they were made, and neither writes into the other's folder.

# 119.4 Every Deliverable Comes With a README
<!-- number: 119.4 | label: 119.4 | id: s119-4 | page: 148 -->

*Module: `agent/work/runner.py` (`_readme`, `README_PROMPT`, `README_MARK`).*

**Every run that made something ends by writing a README** in the work's folder, in the display language. The work agent writes it with its reading tools, from the files themselves, in short headed sections: **what was made**; **where it is**, each file or change by its path; **how to check it**, meaning what to open or run and what the user should see, which is how they accept the work; **how to use it**, with exact commands where there are any; and **what is not done**, and what would come next. The prompt tells the agent to write only what the files show and not to give a step it has not checked.

The README is written at the wrap-up of any run that has a contract and changed files that still exist. That includes runs that end unfinished, whose README says what exists so far and what is left. The single-model variant has no tree of cards, so it is given what was asked, its report and the files it changed, and writes the same README from those.

**The user's own README is never overwritten.** Every README a run writes begins with a fixed marker line (`README_MARK`). If the folder already has a `README.md` that does not begin with the marker, the run's README is written beside it as `README-deliverable.md`. A README the run wrote earlier is replaced, because it is the run's own. The plan the user approves always lists the README among its deliverables (section 119.1), so the user knows to expect it.

# 120. Each Run in a Process of Its Own, Within What the Computer Has Free
<!-- number: 120 | label: 120. | id: s120 | page: 148 -->
<!-- note: 2026-10-17: one pool for everything the agent runs side by side - balanced between kinds, then by priority; no console window for a child on Windows. -->

*Modules: `agent/work/child.py`, `agent/work/governor.py`, `common/children.py`, `agent/work/runner.py` (`_apart`, `start_waiting`).*

**Each run is now a child process** (`work_in_subprocess`, on by default). The agent writes a **spec** (`spec.json`) into a folder of its own under `work_children/` and starts the program with `--work-child`. The child builds a smaller application on the same root, with no listeners, heartbeats, page or desktop, and does the whole start itself: the checks, any rollback, then the run. It writes a **reply** (`reply.json`) saying that the run started, or why it did not, and the agent waits up to 90 seconds for that reply. After that the two sides talk only through files:

- every half second the child writes a **snapshot** (`snapshot.json`) of where the run stands, while its events go to the run's session log as before. The page shows a live run from these files in the same way it shows a run from before the agent last started;
- pause, resume, stop and the user's answers are appended to a **control** file (`control.jsonl`), which the child reads on the same beat.

The child is started below normal priority. It cannot outlive the agent: on Windows every child is in a job object that the system closes when the agent ends, elsewhere a watcher does the same job (`common/children.py`), and the child itself exits when the agent's process is gone. A run therefore never keeps going with nobody to show it, and it can be continued later from its last milestone. Drafts are collected and planned in a child of the same kind. Runs of the Experience debugger stay in the agent's process, because they seal the application's own code while they run.

## A Governor Decides How Many

Because runs no longer share the agent's process, several can go at once, one per piece of work. How many is decided by a **governor** that looks at the computer every five seconds:

- **How many runs may go:** one for every four processor cores and every 1.5 GB of free memory, never more than four. One run may always start when none is going, unless free memory is under 0.75 GB.
- **A waiting queue.** A run asked for while there is no room is not refused. It waits, with the reason shown ("the computer is busy", "short of memory", or how many runs are going already), and the first one asked for is the first one started when room appears.
- **Busy processors.** When the average of the last three looks is 85% or more, every process of every run, child processes included, is set to the lowest priority so the user's own programs come first. The runs return to below normal once the average falls to 60%.
- **Short memory.** When less than 8% of memory is free, the newest run is paused, with the reason shown. The governor resumes the runs it paused once more than a fifth of memory is free.

*Amended 2026-10-17:* ONE POOL FOR EVERYTHING SIDE BY SIDE (`common/pool.py`). The runs of work were not the only thing going in parallel: the state heartbeat's summaries and status calls, the learning of projects (by the button and by itself) and an issue of the Newspaper each counted only their own, so together they could take the machine. Now each takes a place in one pool before it starts, and the runs and an issue are counted in it by asking them. How many places there are is decided from everything going, together, by the rules that decided the heartbeat's calls (whether the user is at the computer, the processor, memory, the battery, a provider refusing): never fewer than one. A place that comes free goes to the kind waiting with the fewest going - with room for four and four kinds waiting, one each, not all four to the runs - and between kinds with as many going, by priority: a run of the user's work, then a project they asked to learn, then the grouping of states, then the Newspaper, then the learning updated by itself. With room for only one, priority alone decides; nothing going is stopped for it. The governor above still decides, for a run, the memory and the busy processors. And no child the agent starts on Windows has a console window of its own unless it asks for one: from a process without one - the installed application, tests run in the background - every git call had opened a window.

*Amended 2026-10-17:* the WORK DEBUGGER'S WAYS take their turn too. A way still runs in the agent's own process, but it is started only when the governor and the pool have room, as a run of the user's work is; while there is none it waits in the same queue - first asked, first started - and the way says so, why and how far along, with a way not to start it after all; it starts by itself when its turn comes. Every run going counts, the debugger's included, and the newest run of either kind is the one paused when memory is short. EVERY PROCESS THE AGENT STARTS runs a step below the user's own programs - on Windows its priority class, which what it starts inherits; elsewhere the watcher's niceness. It is not a limit: with nothing else wanting the processors they have all of them, and only while the user's own programs want them do those go first. A full run of the tests is started the same way. And should a process ever outlive the application - the job that ends them with it could not be made, or a watcher was killed with it - those carrying the marks of the application's own folders (the guard its children load, the folder each run in a process of its own is told about) are found and ended when it starts again.

*Amended 2026-10-18:* THE WORK DEBUGGER'S WAYS GO EACH IN A PROCESS OF THEIR OWN TOO, as the user's runs do: a way run in the agent's process shared its time with everything else there, and a long one made the page and the other ways slow. Its process is started where the way would have started - when its turn in the same queue comes - finds its way of its project by the way's own name, and is followed, paused, answered and stopped through the same files as a run of the user's. The code seal goes with it: the AGENT holds the application's own code sealed for the way from before its process starts, waiting until the seal is on the disk, and gives it back when that process ends, however it ends; it alone counts every way that goes, so it alone changes the seal. The way's process is told the code is held for it: it takes it as sealed, and nothing it does - its own start or end - ever opens it while another way still runs.

# 120.1 A Run's Own Loopback, and a Browser to Test With
<!-- number: 120.1 | label: 120.1 | id: s120-1 | page: 148 -->

*Modules: `runtime/isolation.py` (`own_loopback`, `run_container`), `runtime/guard.py` (`_own_loopback_only`), `runtime/browser.py`, `runtime/agent_browser.py`, `runtime/sandbox.py`, `agent/work/runner.py` (`network_rules`).*

Without loopback an agent could only call the framework's test client, which shows nothing of what a person would see. Several runs now go at once, and the Experience debugger's three ways run side by side, so loopback must belong to one run at a time. **Each run's code gets a loopback of its own, where the operating system can provide one.**

- **Windows:** each run's code runs in an AppContainer of its own, named from a hash of the run's folder. Windows lets processes in the same container reach each other on loopback, and blocks other containers and the computer itself. It was measured here and needs no administrator change. A container's profile is deleted when the run's last child process ends.
- **Linux with bubblewrap:** the run gets its own network namespace, with an empty network and a loopback of its own.
- **macOS (Seatbelt) and Landlock:** no loopback at all, because these can open loopback only to every program at once, or share ports between all processes.

The in-process guard enforces the same rule from inside the run (`own_loopback`). On the two backends above, connections are allowed only to loopback addresses. No name other than localhost is looked up, and Unix sockets are refused, because a Unix socket is a door to another program. The system keeps everything else out. The prompts tell agents which rule holds on this machine. Where loopback is open, they also tell the agent to take a free port rather than a fixed one, since other runs may be going, and to start the server, wait for it and test it with a real client in the same script.

## A Browser of the Run's Own

Testing what a person would see needs a browser. The browser is not the user's Chrome, which holds the user's accounts and whose crash reporter cannot start inside a container. It is Google's **Chrome for Testing headless shell**, downloaded only when the user asks for it in Settings (Browser testing). It comes only from Google's own storage, its checksum is verified, and it is kept in the application's protected state, where no run can write.

A run is given it only where it has a loopback of its own. There the browser is the one program besides Python that the run's code may start, and it reaches only the servers that run started. Runs drive it through a small standard-library helper (`from agent_browser import Browser`) to load a page, click, type, wait and take screenshots. Each run starts from a **fresh, empty profile**, created in the run's runtime folder and deleted when the browser closes. The screenshots stay in the work's folder as evidence the user and the reviewer can open.

# 120.2 A Run Waits on the Model Rather Than Failing
<!-- number: 120.2 | label: 120.2 | id: s120-2 | page: 148 -->
<!-- note: 2026-10-17: a call's input is in a file before the client starts (Claude Code gave up after three seconds without it), and that failure is retried. -->

*Module: `agent/work/runner.py` (`_call`, `model_trouble`, `_wait_for_model`, `answer`).*

A failed call now asks the run for one of three things, decided by `model_trouble`:

- **Temporary**: a timeout, or the provider unavailable, overloaded or out of reach. The call is made again by itself after 5 and 20 seconds, then after 60, 120, 300 and 600 seconds, the last wait repeating, and the run carries on. Each attempt is shown in the live session. Retrying cannot go on forever, though, because each attempt may spend usage before it is cut off. A call that has failed this way for an hour becomes **stuck** and waits for the user.
- **A usage limit**, which resets by itself. The run waits, shows when the limit resets, and looks again a minute after that time. If the provider did not say when, it looks every ten minutes.
- **Something only the user can mend**: a sign-in, a key, a model not offered, credit. The run waits, says what failed and where to mend it, and looks again after a minute and then every ten minutes. A fix made in Settings, or outside the application, is picked up without the user having to tell the run.

While a run waits, its status is *waiting for you*, and the list of runs shows that it is waiting on the model rather than on a package. The wait is recorded in the run's plan as one of the user's requests (the last fifty are kept). The time spent waiting does not count against the run's time limit. However a wait is scheduled, the run never looks at the model again sooner than a minute after the last look.

The waiting run offers two buttons. **End the run** stops it as not finished, makes no further model call in that session, and lets the user continue it later. **Try again now** lets the call go out at once. Before that call is made, the models manager's **availability cache is cleared** (`invalidate`). Without that step, the cache could answer "try again now" with the failure it remembered from the last check instead of asking the model, and the button would appear to do nothing.

*Amended 2026-10-17:* A CALL'S INPUT IS IN A FILE BEFORE THE CLIENT STARTS, not written to it by a thread of the agent's once it has (`common/children._input_file`). Claude Code waits three seconds for its input and then gives up ("no stdin data received in 3s"); with the agent's process busy - two debugger runs, a learning and a heartbeat at once on a loaded computer - that thread was not given its turn in time, five calls failed in a quarter of an hour, and a run of work ended with one of them. The file holds the same bytes the pipe carried and is removed when the call ends. And that failure is now one for the moment: the call is made again, not the run ended.

# 121. Models in Order, and What Each Call Cost
<!-- number: 121 | label: 121. | id: s121 | page: 148 -->

*Modules: `models/manager.py` (`chain`, `set_chain`, `invoke`, `switches`), `models/prices.py`, `common/observability/model_calls.py`, `common/observability/started.py`.*

**The models are now a list, in the user's order** (`model_chain`, in Settings > General > Models), each entry with its own reasoning effort. Each call goes to the first model on the list that works. Not every failure passes the call on. A model that **cannot answer as things stand** passes the call to the next model (`switches`). That covers anything only the user can mend (a sign-in, a key, credit, a model not offered, a client not installed) and a usage limit. A timeout or a busy moment does **not** pass the call on: that failure is raised so the caller can try the same model again (section 120.2). The reasoning is that a run should not change models mid-task because of a single slow answer. If no model on the list can answer, the failure of the **first** model is raised, because that is the one the user chose first. A role with its own override model (heartbeat, scheduled task) uses only that model. An installation from before the list behaves as before: the primary model, then the fallback if it was turned on.

## What Each Call Cost

**Each call records which model answered and what it cost.** The model is recorded under its own name where the provider gives one, for example the full model id when the request said only "opus". The cost comes from the first of these that is available:

1. the cost the provider reports, as some providers do for each call;
2. the model's list price from `prices.py`, which counts input read from the provider's cache and input written to it separately. A write costs twice the input price for providers whose cache lasts an hour and a quarter more for a five-minute cache;
3. the price in the provider's own model catalog.

If none of these exists, the cost is left empty rather than guessed. On a subscription, the cost shown is what the same calls would have cost at list price, since the subscription itself is what is actually paid.

Calls are kept on disk for 400 days, so a trend survives restarts. Calls made before costs were counted get theirs filled in from the list prices (`fill_costs`). Cached input tokens are now passed through as well. Before that, one machine recorded 11,084 input tokens across 4,139 calls, because the cached input that mattered was never written down. Each call also records the language it was told to write in (section 122) and **whether the user started it** with a button (`by_user`). Without that flag, a function the user ran by hand would be charged to that function's background running cost, and the function would look more expensive to keep switched on than it is.

# 122. Languages Said at the Call
<!-- number: 122 | label: 122. | id: s122 | page: 148 -->
<!-- note: 2026-10-15: Russian, French, German and Italian added to the display languages - nine in all - and the whole interface shown in them from fixed tables (section 134). -->

*Modules: `common/languages.py` (`Language`, `in_display`, `in_english`), `models/manager.py`, `common/output_languages.py`, `heartbeat/states/english_copies.py`, `heartbeat/connections/round.py`.*

The agent speaks to the user in one of five display languages (Chinese, English, Spanish, Japanese, Korean), but much of what it writes is for its own use and must be in English: keywords, states, search words.

*Amended 2026-10-15:* there are nine display languages now - English, Chinese, Spanish, Japanese, Korean, Russian, French, German, Italian - and the whole interface is shown in the one chosen (section 134).

**A model call now says its language in one way only, at the call:** `language=in_display(...) + in_english(...)`. Each part names what it covers, for example "the plan" in the display language plus "the keywords" in English. The models manager turns this into the instruction the model receives and **puts it first in the system prompt**, whole (`Language.compose`): the display language and what is written in it, followed immediately by each English exception, explained as being "kept for the agent's own use and compared with one another". Saying it first means the examples that follow cannot outvote it. Putting each exception straight after the rule means the model reads the exception together with the rule it qualifies, instead of after it has already applied the rule.

**Each call records the language it carried** (`model_calls.language`, stored as JSON rows). The **Language debugger** tab sets that record beside the code. Every time it is opened, it reads the source with Python's own parser (`output_languages.py`) and lists every place a language is stated: internal, display, or mixed. It lists every model call that states none, and every prompt that still names a language in its own words, which the rest cannot see. It also shows, for each task over the last seven days, the language its calls actually carried. A language added, changed or removed in the code appears there with nothing maintained by hand.

## English Copies

Comparison and measurement are done in English, because the embedding model reads only English, while the user reads their own language. So where the display language is not English, a task's words (title, summary, where, goal, doing, status) and a project's name and status carry an English copy (`in_english`). The copy is written in the same call that writes the words. Each copied field is stamped with a short hash of the words it was copied from. A field with no copy, or with a copy of different words, is translated in a backfill (`english_copies.fill`).

The backfill runs at the end of a state round, while no part of the round can write, and before a Customized Recommendation issue is made (section 126). It translates up to 40 fields per call and at most six calls at a time, leaving the rest for the next time. A copy is kept only if the source words are still the ones that were translated. Each recommendation story likewise carries its English in the same call, and that English is what "more like this" and "not interested" are measured by. When the user reads English, none of this runs, because the words they read are already the copy.

# 123. The Local API Answers Only Its Own Key
<!-- number: 123 | label: 123. | id: s123 | page: 148 -->

*Modules: `api/access.py`, `api/server.py`, `common/own_interface.py`, `__main__.py` (`--sign-in`).*

Once a run's code could reach local ports, the rule that an agent writes only in its own folder would have been one request away from failing. So **every `/api/` request now carries a secret the agents cannot read.**

- **The key.** It is 32 random bytes, made once per installation and kept in the protected credentials folder, which the agents' tools refuse to read, the guard refuses inside a run's code, and the operating system hides where it isolates a run. The file is restricted to the user, and the key is registered so that no log ever prints it. The application's own clients (the desktop, tests, the user's scripts) send it in the `X-PA-Key` header.
- **The browser's sign-in.** A page cannot be handed the key, because anything the server puts in a page another program could fetch the same way. Instead the desktop opens the page through a **sign-in link carrying a single-use ticket** that lives two minutes. A link printed for a run without a window (`--sign-in`), or opened from Explorer while the agent is still starting, lives fifteen minutes. The ticket is an expiry time and a random nonce, signed with the key, so it needs no stored state. Spent nonces are remembered until they would have expired, so a ticket works only once. The server answers the link by setting a cookie and redirecting to the page asked for, and only to a page of its own. The cookie is HttpOnly, SameSite=Strict and lasts only for the session, and its value is derived from the key rather than being the key. A cookie was chosen over the page holding the key because a page would keep the key in plain text in the browser profile, which an agent may read. Also, the run's deck is shown in a frame, and frames and images can send a cookie but not a header.
- **The checks a cookie needs.** A browser sends a cookie to every port of 127.0.0.1, so a page an agent serves on its own port could make the user's browser call the API with the cookie attached. The cookie is therefore honoured only when the browser's own `Sec-Fetch-Site` header says the request came from this page. A request that names another origin is refused even with the key. A request must name this server as its host, which defeats DNS rebinding.

Tickets appear in URLs, and URLs end up in browser history, which the listener reads. The listener already skips this application's own pages, and every event it records has any ticket cut out (`scrub_sign_in`). In any case, a ticket copied from the history was spent within a second of being made.

The few routes other applications use to connect, through the external protocol, stay open without the key, because those applications prove who they are with key pairs of their own. Registering through them is limited to pushing events.

# 124. One Lessons Engine
<!-- number: 124 | label: 124. | id: s124 | page: 148 -->
<!-- note: 2026-10-17: the Lessons tab became the Lessons debugger - for whoever develops the agent, not shown in an installed build; the lessons package stays on the Overview. -->

Everything the agent learns about how the user wants things done is learned by one engine, `heartbeat/lessons/engine.py`. There are three kinds: **notification lessons**, **connection lessons** and **how you work** (the natures of their work, `heartbeat/lessons/natures.py`). One algorithm for all three means a fix in one is a fix in all of them.

## Evidence First, Then a Lesson

A lesson is never written from one event. It is a rule with explicit conditions, and beside it the evidence that supports it and the evidence that contradicts it. A cycle (`learn`) runs over whatever is new:

- **Ingest** - each new piece of evidence is given features (its source, the kind of event, the person, English keywords) in one batched call
- **Match** - code shortlists up to 20 rules per piece by shared features; the model then judges each pair as supports, contradicts or unrelated, and the judgement is kept with its reason
- **Induce** - evidence nothing explains goes to a pool; three or more alike pieces are shown together and the model may propose one rule for them
- **Score** - each piece weighs 1 for an action, 2 with a reason the user gave, 1.5 for a correction of the agent, halving every 60 days; confidence is (S + 1) / (S + C + 2)
- **Decide** - a candidate is promoted at support 3 and confidence 0.75; a lesson below 0.6 is revised, narrowed or retired from its evidence side by side; lessons resting on the same evidence are merged; one with no support for 120 days retires
- **Publish** - the agent is given only the lessons in force, each with its id, how sure it is and how many cases back it - never the raw actions

The work kind is promoted sooner (support 1.5, confidence 0.7), because one stage of the user's work is a whole episode rather than one click. Its natures stay in the library where they were; the engine keeps a rule with the same id and writes back onto each nature whether it is in force and how sure it is, for the reviewer to weigh.

## Only the User's Own Actions Move a Lesson

What counts as evidence is what the user did: added, deleted, edited, disabled, called a notification bad - with their reason when they gave one. The user undoing something the agent did on a lesson (`used`, then `outcome`) is a correction against each lesson it followed.

What the agents do is never evidence. A decision the user left alone for three days stops being waited on (`settle_uses`), but leaving something alone is not something the user did, and it moves nothing; nor does the reviewer reopening a piece of work. Such evidence recorded by earlier versions is removed once (`forget_the_agents_own`) and every lesson scored again. Otherwise the agent would grow surer of whatever it already did.

The user's own say over a lesson - confirm, reject, pin, edit its words - always wins over the scoring.

## When It Runs

Never on the user's click: an edit is written down, and the cycle runs with the state heartbeat's round for each kind with something new (`due`, `learn_due`), or when the user presses Learn now. A kind whose function is turned off is not learned. Patterns and principles over the lessons in force are grouped by experience's own builder (`heartbeat/lessons/groups.py`) when the lessons changed.

*Amended 2026-10-17:* the lessons are no longer shown to the user. Their tab is now the LESSONS DEBUGGER, among the debuggers, and like them it is left out of an installed build: the lessons are how the agent reads what the user did - for whoever develops it. The lessons package stays on the Overview, for the user: everything learned about how they work - the lessons, what Work learned, the natures - carried to another computer and loaded there. On Connections, a change the user makes is said to teach the agent how they want their connections kept, not to be "a lesson".

The lessons package (`heartbeat/lessons_package.py`) does not carry the engine's rules but the user's actions themselves; loaded on another computer, each becomes evidence there (`App.lessons_catch_up`) and is learned from again.

# 125. Connections
<!-- number: 125 | label: 125. | id: s125 | page: 148 -->
<!-- note: 2026-10-21: the people step is told that different names or organisations are another person, and may split an entry; the Connection debugger shows every change with what the model was asked and answered. -->

`heartbeat/connections/` keeps the user's contacts: people, their accounts and public profiles, and their dated activity (`store.py`).

## Where People Come From

From three places only:

- **What the user had in front of them** - step 2 of the agent heartbeat (`people.py`) reads the same window step 1 read and finds who the user *deals with*: wrote to, met, works with on a document or a project. A name read in the news, an author of a paper, a public figure, a name in code - none of these is a contact unless the window also shows the user dealing with them. When in doubt, they are left out.
- **The public web** - looked up by the round (`round.py`) for a person whose news is older than 30 days and matters to the user's current work, and for an important one (closeness 4 or 5) unseen for 45 days, at most 3 a round. Only a search and public pages: the agent never signs in, never posts and never contacts anyone; a page that needs a sign-in is not read. Pictures come the same way (`avatars.py`): a GitHub account's own picture, or a public page's sharing image, fetched with no cookie, at most 2 MB.
- **The user** - adding, editing, merging and deleting in the Connections panel.

Every fact carries when it happened, when it was found and when it was written, so the model can judge what has gone out of date.

## The User's Changes Are Lessons

Each add, edit, delete and merge is recorded with who did it, the before and after, and the reason if one was given. The user's own become evidence for the connection lessons (section 124), and the last 100 are kept word for word; older ones are folded into one summary by the model. A deleted person is kept, marked deleted, so a person the user removed is not added back on a hunch - only on stronger, newer evidence, said why.

On a contact's profile, what the agent found on the web may replace what the user wrote: it is newer than their memory of it, and their change is still in the history.

## The User's Own Profile

The user is a person of their own (`is_self`), looked up as a contact is - at most once in 20 hours - and never listed among the contacts. Their profile is protected the other way round: nothing the agent finds changes it. A finding about their name, organisation, role, place, profession or profile links is put in an **inbox** (`propose_for_me`) to accept or dismiss; accepted, the change is made as the user's; dismissed, the same change is never asked again. Changes to the user's own profile are not connection lessons - they say nothing of how the user deals with other people.

## Merges and One Spelling

Two entries that are one person are merged after a preview (`merge_preview`) that shows every account, piece of activity, other name and tag that will move and every empty field that will be filled; the kept person's main relationship and organisation stay main. Organisations and profile labels that mean the same are written one way from then on (`canonical`, `merge_names`), so a later finding is spelled as the first.

*Amended 2026-10-21:* A DIFFERENT PERSON IS NOT AN UPDATE, AND AN ENTRY CAN BE SPLIT. The people step was told only to avoid duplicates, and one contact took the names of five people - a co-author first, then a professor met by email (both academic, seen in one stretch), then those copied on his email - each new name written as another name of the first. It is now told the other side too: names that cannot be one person's, or a different organisation with nothing tying them, are someone else; two people seen together - one window, one email, one organisation, one topic - are two; when it cannot tell, it adds, since a duplicate is merged later and two people made one is worse. And it may SPLIT an entry that is more than one person: each other person in it, with their names, accounts and activity (each piece of activity is listed with its id), made an entry of their own; never one the user merged.
<!-- note: 2026-10-21: the people step is told that different names or organisations are another person, and may split an entry; the Connection debugger shows every change with what the model was asked and answered. -->

*Amended 2026-10-21:* THE CONNECTION DEBUGGER. Every change to the contacts, newest first - who made it, when, what it was before and after - and, for a change the agent made from a model's answer (the people step, a Newspaper round's web look-up), the start of what the model was asked and answered; the whole of each call is kept in a file of its own (`connections/debug/<day>/`) and opens from there. A debugger: an installed build has none of it.

# 126. Customized Recommendation
<!-- number: 126 | label: 126. | id: s126 | page: 148 -->
<!-- note: 2026-10-15: a reading topic gets a page only when it is an interest - one-off glances (a product page for 12 seconds, a 4-second video, a fix looked up for an error) each made a page the user never wanted. -->

Customized Recommendation is a newspaper made for the user: a page for each thing they are doing, each searched on the public web for what they have not seen. It has its own tab.

## The Reading Log

After each agent heartbeat, what was read since the last time - the browser's pages, how long each window was in front, the screen text and the words of screenshots - is put together into **pieces**, one per thing read or watched (`heartbeat/reading/log.py`). One call triages them; at most 8 thin public pages are fetched again in full - never an address whose opening does something (unsubscribe, confirm, sign in), one carrying a key, or a site of mail, files or shared documents. Each piece is extracted, in English, into its kind, topics, entities and points, and placed in the nearest **topic** (`topics.py`) when within 0.55, else it starts one. A topic's weight is not stored; it is worked out from its pieces' engagement, halving every 14 days.

## From Subjects to Pages

`heartbeat/connections/recommend.py` starts from what the user is doing now: each task or project with something in it in the last 14 days, and each reading topic. A topic whose centre is within 0.5 of a task goes onto that task's page. At most 8 pages; beyond that the two nearest are put together. A subject the user said "not interested" to at least twice, more often than anything for it, is left out.

Each page is searched **from its detail** - the states of its tasks, the titles and points of what was read - never from its heading. Someone who watches restaurant reviews wants restaurant reviews, not food prices.

## Checked in Code, Not by the Model

Each story is measured against the page's own detail on this machine's embedding model. Below 0.35 it is not printed; from 0.42 it is close; between, broad - and a page carries at most 2 broad stories, never more than half its close ones. The thresholds were measured: stories the user found closely related came out 0.45-0.58; food prices for restaurant reviews, 0.30. A story within 0.78 of something already read, or 0.85 of one shown in a recent issue, is the same story and is left out. News of someone the user knows is kept even when it is near no page, on a page of its own.

The score is gain x relevance x freshness (halving every 7 days) - not how long something was watched, so a lot of one kind of video does not drown the rest.

## The User's Say

Each story offers "more like this", "not interested" and "already knew" - one per story; changing it takes the previous one back. "Already knew" counts *for* the topic and is kept as a known piece, so what is like it is not news next time. Stories near ones the user wanted more of score 1.25 times; near ones they turned away, 0.6.

## Its Own Clock and Front Page

Issues are made by their own clock (`schedule.py`), apart from the heartbeats: at the times of day the user chose, 08:00 by default, in this computer's time zone, checked once a minute. Times missed while the computer was off make one issue on return, looking back to the last issue (a day at least, a week at most). Every time can be removed; then no issue is made by itself, only with "Make an issue now". The times are edited where they stand in the tab and saved as they change. Each issue has a front page - the best story of each page, at most 6 - and the newest 100 issues are kept.

*Amended 2026-10-15:* a reading topic is a subject only when it is an INTEREST: read on two days and for a minute at least, or for ten minutes in all, or three different things read in it (an evening of papers, each open less than a minute between other windows, is one). A piece on screen less than three seconds does not count. What was looked up to get one thing done - how to fix an error, how to install something, an assistant's help with a problem - is logged as the kind `help` and is never an interest. A merged page keeps the whole list of what it was made of, so a story can be traced to each part (`heartbeat/connections/recommend.py`).

*Amended 2026-10-16:* a story begins with how it ties to what the user is doing: its first sentence, written with it, links it in passing to something specific - the task, the file, the talk, the question they are stuck on, or what they read or watched on that page's subject - the way a friend who knows their work would put it, so they want to read on; never a label like "why this matters", and none at all when there is no real link. A story that has one is not also given the labelled reasons beneath it.

*Amended 2026-10-16:* what is enjoyed and what is worked at are interests by different rules. The reading log says of each thing read or watched why it was - work (a job, studies, research, a task, however personal) or leisure (films, music, shows, games, recaps, sport, browsing for pleasure). Work keeps the rule above. A topic mostly of leisure is an interest only when it lasts: on three different days at least a week apart from first to last, within a month, with something of it lately - one film recap watched once for seventeen minutes had made a page of an actor's next film. A page of something enjoyed is told to recommend more of the pleasure in the form it is enjoyed - someone who listens to a great deal of piano gets pieces, performances and concerts, not lessons, unless what they watched was lessons - and enjoyed and worked-at subjects share a page only as a last resort. A story on a topic's page is labelled with the page's name alone, as a section is; its opening says why it is there.

*Amended 2026-10-17:* on screen Customized Recommendation is called **Newspaper** - its tab, its masthead, its function in Settings and its settings - as each issue is laid out as one; the code and the settings keep their names (`recommend`, `module_recommend_enabled`, `recommend_times`).

*Amended 2026-10-16:* a task is a subject of the recommendations by the same two rules. A task of leisure is labelled so, and reaches the recommendations even where its summary judged it "not a task" - which had kept every evening's films from them - but only when it lasts, by the rule for leisure above; a project is one of leisure only when every task of it is.


*Amended 2026-10-18:* ONLY CLOSE STORIES. The pages carried a few BROAD stories - a step to the side, the same field - among the close ones; the user decided against them. A story is printed only from `CLOSE_AT` (it was printed from 0.35, now 0.42), and the search is asked for nothing else. The ranking is unchanged: gain x relevance x freshness. WHAT THE PAGES ARE MADE FROM, with tasks and projects made per run (section 37, as amended) - the user's rule: the user's STATES, newest first, until their events add up to twenty-four hours, or there are no more - at nine in the morning with one state of ten minutes, the days before are read back until the twenty-four hours are full - each given with its time and length (twelve hours at first; the user made it a day). A state already written into a task comes under that task's PROJECT, with the project's line and what is being worked on in it; one no run has read yet comes alone, its project not looked for. Not the latest run's tasks: a run started by hand may hold two hours. Routine counts toward the hours and makes no page; a project of leisure still makes one only when it lasts. A project's weight is the hours of its events among them. EVERY PROJECT IS A PAGE OF ITS OWN - the user's rule: a page is about a project, its states the detail under it, and two projects never share one; what was read and watched goes together, and onto the nearest project's page only if it must. A project is a page only with an hour of its events among the twenty-four hours: the states of one touched for less, and those no run has read yet, go each under the project page nearest it in meaning - one of leisure only under a project of leisure, one of work only under one of work, as a page is searched as one or the other - or are left out. THE PAGES ARE SEARCHED FOUR AT A TIME: one after another, a dozen pages of two to five minutes each made an issue take twenty minutes (measured on the user's record).

*Amended 2026-10-21:* AN ISSUE BY ITSELF ONLY WITH ITS SWITCH ON. Beside its times, Newspaper has a switch, "Make an issue by itself" (`recommend_auto_on`): an issue is made by itself only while it is on and a time is set - off and none at first, as Work's learning. Where issues were made before, the switch is turned on once, and the times kept (08:00 where none was ever set).
<!-- note: 2026-10-21: Newspaper's switch beside its times; on for an install that made issues before. -->

# 127. Pictures and Documents
<!-- number: 127 | label: 127. | id: s127 | page: 148 -->
<!-- note: 2026-10-15: Russian, French, German and Italian added to the display languages - nine in all - and the whole interface shown in them from fixed tables (section 134). -->
<!-- note: 2026-10-16: the user asked that a work run can read video - with a model's own tool where there is one, a common one where not - keeping what it makes in the run's own folder. -->

`runtime/documents.py` reads what is not text - pictures, PDFs, Word, PowerPoint and Excel - so every model can use it, whether it sees pictures or not.

## Read on This Computer

Words in a picture are read by the operating system's own OCR (`platform_layer/ocr.py`), and no picture is sent anywhere for it:

- **Windows** - Windows.Media.Ocr, through PowerShell; adding a language is one of Windows' optional parts and asks for an administrator once
- **macOS** - the Vision framework, through PyObjC
- **Linux** - which has no OCR of its own: RapidOCR (English and Chinese) added to the application's own Python, or Tesseract if the system has it

A picture is read in every one of the five display languages the machine has, and the reading most in its own script is kept. Settings > Display shows which languages are present, with a button to add the rest.

*Amended 2026-10-15:* there are nine display languages now - English, Chinese, Spanish, Japanese, Korean, Russian, French, German, Italian - and the whole interface is shown in the one chosen (section 134).

A PDF gives each page's text; a page with fewer than 30 characters is taken for a scan and its words read off its picture, for at most 40 pages.

## One Reading per File

What was read of a file is kept as a copy named by the SHA-256 of its bytes, in the application's own folder, never beside the file. Read again, the copy is used; a file whose bytes changed is a different file. At most 5,000 copies are kept, the least recently used going first. A screenshot is read in the background as it is taken, so its copy exists before anything asks for it.

## Only to Models That See Pictures

A read_file tool answers with the words - always - and the picture itself, at most 1568 pixels on its longest side. Whether a model sees pictures is known from its provider and its name (`models/vision.py`), and the user can say otherwise for any model. A model that sees pictures gets the picture; one that reads text only gets the words and a line saying a picture was not shown. Codex is given its pictures with `--image`.

*Amended 2026-10-16:* VIDEO. None of the models the agent runs on - the Claude API, Claude Code, Codex, the OpenAI-compatible ones - takes a video as it is, so a work run watches one with a tool of the agent's own, the same for every model (`read_video`, `runtime/video.py`; read_file passes a video to it): frames taken evenly over the part asked for, each a picture with the words read in it, and what is said, line by line with its time, recognised on the processor by faster-whisper, whose model is fetched once, the first time, runs on half the processor at most - it shares the agent's own process - and is let go two minutes after it was last used, as the embedding model is. A start, an end and how many frames narrow it to a moment; speech is read thirty minutes at a time. Everything it makes is written into the run's own folder, under `videos/`, and read from there when the video is asked for again; nothing is written beside the video. A frame whose words hold a password or a code is not kept and not shown, its words given with it hidden, as is what is said.

## Passwords and Codes Hidden Before Anything Is Kept

`common/credentials_guard.py` cuts passwords, one-time codes and keys out of what the listener records - screen text, titles, addresses, assistant conversations - and out of the words read off a picture. It works by keywords in the five display languages and by the shapes keys and tokens have; no model decides, so no password is ever put before a model to ask whether it is one. The value is replaced with "[hidden]" and the label stays, so a sign-in can still be seen to have happened. The account is kept: what was done is of no use without whose account it was done in. A screenshot whose words show a credential is not kept at all, and a picture whose words held one is never given to a model. The protection is the Privacy setting "Hide passwords and codes", on by default.

*Amended 2026-10-15:* there are nine display languages now - English, Chinese, Spanish, Japanese, Korean, Russian, French, German, Italian - and the whole interface is shown in the one chosen (section 134).

# 128. Triggers and What Waits for a Time
<!-- number: 128 | label: 128. | id: s128 | page: 148 -->

## Acting at Once

`heartbeat/core/triggers.py` watches the listener's events and, when what is on the screen matches a rule, runs the one step that rule is for on that window alone - not a whole heartbeat. Two rules exist:

- **Invitation** - a meeting invitation is in front ("Yes No Maybe", an .ics, "Accept Decline", and their Chinese equivalents)
- **Time agreed** - a time is being agreed ("works for me", "see you on", and Chinese equivalents), though the message with the time may be folded away

Both run the notification step only, as one model call, given the window read whole (its text and the words in its latest picture), the notifications already on the calendar, what is waiting for a time and the notification lessons. The model is told what *just appeared* separately and to act on that alone; the rest of the window is history.

Matching is a look at words, with no model, in the listener's own thread. What makes it safe to run unattended is what it refuses to match:

- **Only what is new in that window** - a line shown in the last 12 hours does not match again, so a conversation scrolled back, or a terminal where these words were discussed hours ago, is not a reason
- **A window is its handle** while it is open, not its title, which a terminal changes as it works
- **The first look after start-up only learns** what a window shows: after a restart everything on screen reads as new, and none of it is
- **Once in ten minutes** per window, and 12 seconds after a match for the page to settle
- **Nobody there, nothing runs** - no keyboard or mouse for 180 seconds
- Never the agent's own windows

Triggers belong to the Calendar management function and stop when it is turned off.

## Waiting for a Time

`heartbeat/core/pending.py` keeps what the user agreed to whose time is not known yet. The notification step writes one rather than a notification at a guessed time; every one open is given to every later notification step. When the time is seen, the step adds the notification and resolves the waiting item in the same answer, saying where the time came from; when it is called off, it drops it, saying why. An open item with the same title is not written twice. They are listed on the Calendar, where the user can take one off or reword it.

# 129. Notifications That Say What Happens
<!-- number: 129 | label: 129. | id: s129 | page: 148 -->
<!-- note: 2026-10-18: no step ticks next steps of tasks any more; the To do list is the user's own. -->

## Every Button Says What Happens

The fixed templates of `agent/feedback/templates.py` now carry their consequence in the label:

- **Got it (no more notifications)**
- **Remind me in N minutes** - the agent's own interval when it chose one, said in minutes, hours or days; 900 seconds when it chose none. The pop-up's own button reminds in 30 minutes (`App.REMIND_LATER_MINUTES`)
- **Add to to-do (no more notifications; listed in To do on the Overview)** - offered on every template
- **Bad notification (no more notifications + fewer similar ones in future)** - dismissed, and recorded as evidence for the notification lessons

A notice that asks nothing (the passive template) has only "OK (no more notifications)" and Add to to-do, and its pop-up offers no remind button.

## Remind Me Later Moves the Event

`App.remind_later` dismisses the notification and moves the event itself to half an hour later - a one-off is put back on the calendar at its new time, not copied. A repeating one's series is not moved: it gets one postponed reminder, made the first time and moved again on each later press. One reminder stays one row on the calendar however often it is postponed.

## The To-Do List

`App.notification_to_todo` dismisses the notification, answers its question (if one is pending) as "put on the to-do list", and adds the item to `todo_items` (`agent/feedback/todo_list.py`) - once per notification. The list is shown as **To do** on the Overview, beside the Summary task's progress bars, with the open items only.

Only the user removes an item, by hand: the × turns into "Remove it? Yes / No". The agent never marks one done - an item the user chose to keep is not the agent's to judge finished. The Overview also offers to hand an item to the agent (Delegate work, section 119), which gathers the context and drafts a plan for the user to check.

This is distinct from the **next steps** of the user's tasks and projects, which the agent heartbeat's to-do step (`step_todo`) still ticks when the activity shows one finished - the message sent, the change deployed - each with a sentence saying why, and which the user can untick.


*Amended 2026-10-18:* There are no next steps of tasks any more (section 37, as amended), and no step ticks them; the To do list here is the user's own, unchanged.

# 130. Settings in Views
<!-- number: 130 | label: 130. | id: s130 | page: 148 -->

Settings is divided into six views, and the last one open is opened again:

- **General** - the models in order (section 121), Startup (start when I sign in), the heartbeats in minutes, and web search
- **Display** - the language the agent writes and speaks in, text size, and which OCR languages this computer has
- **Avatar** - the avatar, alive as on the desktop, with buttons to show it waiting, talking, thinking and notifying, and what it is called
- **Functions** - what the agent does by itself (below)
- **Privacy** - what is listened to, one switch per source, each saying what the agent loses without it and what it costs the computer; and Hide passwords and codes
- **Others** - browser testing and every other setting

## Edited in Place

Every value is a live control. The first change in a card shows a bar - "Not saved yet", Save, Cancel - that stays in sight under the page's sticky header while the user scrolls; typing shows it with the first letter. Nothing is written until Save; Cancel reads the settings again. A change held in one card survives saving another.

## Functions and What They Cost

`common/modules.py` lists what runs by itself - Task organisation, Calendar management, Connections, Customized Recommendation, Experience learning - with Activity and states as the core that cannot be turned off. Each says what it does and what turning it off means. Every model call records its task, and `module_of` maps the task to a function, so each function shows its calls, tokens and estimated cost over the last 72 hours, its share of what the automatic functions spent and its share of everything. Shares are by cost, or by tokens when nothing had a cost. A bar beside each is pale for little and deep red for the most.

What the user starts - work runs, chat, scheduled tasks, lessons, Experience's tasks into stages - is counted apart and has no switch. A function can also be run with a button, and its calls carry the same task either way; `common/observability/started.py` opens a window while a run the user started is going, so those calls are counted as started by the user. Otherwise a function would look dearer to keep on than it is.

# 131. The Watermelon Avatar
<!-- number: 131 | label: 131. | id: s131 | page: 148 -->

The first avatar, Watermelon (`agent/character/watermelon.py`), is drawn in code rather than kept as pictures: a melon with stripes that follow its curve and a gloss, on a bed of leaves. The body and leaves are painted once; the face is drawn on them every frame, so it can respond:

- **idle** - breathing slowly, blinking every few seconds, eyes on the pointer
- **hover** - eyes closed in a smile
- **click** - a hop and a squash
- **talking** - the mouth opening and closing
- **thinking** - eyes up, a row of thought bubbles
- **notification** - brows up, cheeks pink, a red "!" and a hop

The cheeks are pink only for a notification, not when it smiles on hover, so a blush always means there is something for the user.

## Drawn Live on the Desktop

The desktop window (`ui/desktop.py`) aims the eyes from between them toward the pointer, fully turned at 150 pixels; with the pointer still for 12 seconds it looks ahead. It redraws often while something moves and seldom while it only breathes. Hover is decided from where the pointer is, not from enter and leave events, which come and go as the pointer crosses see-through pixels and made the smile flicker. Edges are made fully opaque or fully clear, because a half-transparent edge takes on the colour the window keys out and shows as a fringe.

It **faces the middle of the screen** (`facing_for`): on the right half it faces left, on the left half right, and within 40 pixels of the middle it keeps its direction, so dragging it across does not flicker.

The settings page draws the same face in SVG over the same body and leaves, from the shared description `RIG`, so the preview in Settings > Avatar is the same character.

## Its Name and Icons

What the avatar - and so the agent - is called (`avatar_name` in `agent/character/manager.py`) is the name the user gave it; with none, the avatar's own display name. It is used for the windows' titles, the tray's tooltip and the hover text. Once, on upgrade, the old default avatar is replaced by Watermelon and its old default name, which nobody chose, is cleared.

The taskbar and title-bar icons and the tray icon (`ui/tray.py`) are drawn from the live avatar at their own size and cut to the figure (`fitted`), with a 3% margin, because a frame is mostly empty around the figure and the icon looked smaller than other programs'.

# 132. The Workspace Is Not the User's Activity
<!-- number: 132 | label: 132. | id: s132 | page: 148 -->

The file listener now leaves the whole workspace unheard by default (`default_directory_exclusions` in `common/config/schema.py`), not only its memory folder. Everything there is made and kept by the agents - work runs, the chat's files (section 132.1), the memory library, scratch - and each run writes from its own process (section 120), which no ownership claim in the listener could cover. Nothing about the user is lost: what the user does with what was made is seen by the window and screen listeners.

Existing installations are moved once (`ConfigStore._workspace_unheard_once`): the line for the memory folder becomes the line for the workspace, in the settings and the user's own defaults, and the user's other lines are kept. The move is marked as done, so a user who takes the workspace out of the exclusions in Settings keeps it out.

The exclusion is a default like any other, editable in Settings, and the agent's internal folder - its settings, database and logs - stays excluded beside it. The reasoning that kept the rest of the workspace audible earlier, that the user's documents might live there, no longer holds: the workspace is the agents' own place, and a run's thousands of writes recorded as the user's activity would bury what the user actually did.

# 132.1 Where Chat's Files Are Kept
<!-- number: 132.1 | label: 132.1 | id: s132-1 | page: 148 -->
<!-- note: 2026-10-16: the user asked that each conversation in the chat's list can be deleted with a cross. -->

Files a chat makes are kept per conversation, in `workspace/Chat/<conversation title>/`. The folder is chosen when the conversation first writes a file and kept from then on, even if the conversation's title changes, so its files stay in one place. When two conversations share a title, a timestamp is added to the later one's folder name, and neither writes into the other's.

Scratch - generated scripts and temporary files - is kept apart, in `workspace/agent/.agent_runtime` (section 111), so a conversation's folder holds only what was made for the user.

Each conversation's folder is set per tool call, not once for the chat as a whole, so two conversations going at once never mix their files.

*Amended 2026-10-16:* each conversation in Chat's list has its own menu, as in other chat applications: Pin, Rename, Delete. Pinned ones are listed apart, above the rest, and are never left out of the list. A rename is the user's name for it and nothing renames it afterwards. A deletion is asked once, in the row itself, and then the conversation goes with its messages, what was summed up of them and the follow-up questions asked about them. The files it made stay where they are: they were made for the user and are the user's.

# 133. Packaging
<!-- number: 133 | label: 133. | id: s133 | page: 148 -->
<!-- note: 2026-10-15: Russian, French, German and Italian added to the display languages - nine in all - and the whole interface shown in them from fixed tables (section 134). -->

## The Debugger Carried Unless Left Out

A build from source carries the Debugger by default, as a build from source is a developer's; `build.py --no-debugger` leaves it out. `build/common.py` excludes the `personalized_agent.debugger` package from PyInstaller when the no-debugger environment flag is set. Leaving it out of the build, rather than switching it off, is the point (section 14.34): a setting can be turned back on; a module that was never packaged cannot. Run from source, the debuggers are always there.

The releases users download are built by GitHub Actions with `--no-debugger`, and published with a `SHA256SUMS.txt` of their files and a build-provenance attestation, so a download can be checked against what the workflow built from the tagged source.

`build_flags.debugger_available` asks whether the package can be imported, so the answer cannot drift from what was packaged. Without it, no route under `/api/debugger` is registered, and the page removes every debugger tab - the Debugger, Experience, Heartbeat, Chat, nature, reading and language debuggers - moving the computer-usage panel back to the Overview.

## The Installer's Language

The Windows installer (`build/windows/installer.iss`) asks first which of the five display languages to use - English, Chinese, Spanish, Japanese, Korean - and speaks it from then on. After installing it always runs the program with `--display-language <code>`. That respects the user's own choice (`__main__._set_display_language`): if they chose a language themselves - kept in user-defaults.json in the agent root, which a reinstall leaves alone - it stays. Otherwise the installer's language is set as the installer's, not the user's, so a later install in another language may still change it until they choose.

*Amended 2026-10-15:* there are nine display languages now - English, Chinese, Spanish, Japanese, Korean, Russian, French, German, Italian - and the whole interface is shown in the one chosen (section 134).

## Starting at Sign-In

The installer's last page asks whether the agent starts at sign-in, ticked by default, and runs `--autostart on` as the user who installed it. Left unticked, it runs `--autostart off`, so the program's own default (on) does not turn it back on at first start. The uninstaller removes the entry and Explorer's Delegate work menu.

`platform_layer/autostart.py` uses each system's own mechanism in the user's own area, needing no administrator: the HKCU Run key on Windows, a LaunchAgent on macOS, an XDG autostart entry on Linux. The setting `start_at_login` is the truth: each start puts the system's entry back in line with it, with the program as it was started now - the installed executable, or Python with run.py, the same root and headless if headless - so a moved installation is followed and one switched off stays off.

*Amended 2026-10-15:* after where the program goes, the installer asks WHERE THE DATA IS KEPT - `%USERPROFILE%\PersonalizedAgent` by default, the previous choice on an upgrade, never inside Program Files - and writes it beside the program. A folder that already holds an agent's data is used only if the user says so; No keeps them on the page to choose another. Uninstalling removes the program and leaves the data folder.

*Amended 2026-10-16:* the data folder is `<program folder>\data` by default, following the program folder while the user has not typed one of their own; an upgrade keeps the one chosen before. A folder that needs administrator rights to write to - Program Files, the Windows folder, ProgramData, or any folder this user cannot write to - is refused on the page itself: it says why, and Next stays off until another is chosen, for the program works as the user and never as an administrator. Uninstalling empties the program's folder and keeps the data folder inside it. And starting at sign-in is one entry for every copy of the program: an entry that starts another copy still on this machine is that copy's - never overwritten or removed by this one - so the installer's last page will not finish with it ticked, and Settings will not save it on, until it is unticked; an entry whose program is gone is nobody's and is taken. The installed program's own folder is never listened to, wherever it was installed.
<!-- note: 2026-10-16: the user asked for the data folder under the program folder by default, refused where it needs an administrator, and for one copy only to start at sign-in - refused, not overwritten, in the installer and in Settings. -->

# 134. The Interface in the Display Language
<!-- number: 134 | label: 134. | id: s134 | page: 148 -->
<!-- note: 2026-10-15: new - the user asked that every menu change with the display language, from a table, never translated on the fly. -->

*Modules: `api/web/lang/<code>.json`, `api/web/i18n.js`, `common/interface_words.py`, `scripts/interface_words.py`, `tests/test_interface_languages.py`.*

Every word the application itself shows - its tabs, headings, buttons, labels, the explanations under its settings, the states it names, the avatar's and the tray's menus, the notification's buttons and the avatar's hover panel - is shown in the display language. Nothing is translated on the fly: each language has a fixed table, English on the left and the language's own words on the right, and every one of the interface's texts is in every table, a word a language writes as English included.

A text with a number, a name or another phrase inside it is one entry with numbered places ("{0} states read"). It is found in a text left to right, its words in order, in one pass - a pattern with several open places, tried against a long explanation, never finishes - and what fills each place is itself shown in the language when it is one of the table's texts. Dates and times the page writes follow the language too.

The page translates what it draws as it draws it and leaves alone what is not the application's: what the agent wrote, the user's own records, names, paths, code, and what is typed in a text box (its placeholder is the application's, and is translated). The desktop reads the same tables, so a menu says what the page says.

The tables are kept whole by a check, not by memory: `scripts/interface_words.py` reads every text the page's sources can show - their string and template literals, split at the markup, each inserted value a numbered place - and the server's settings, and lists what is not in every table; the test fails until it is. A text the page puts together at run time can still slip past, so a change to what a screen says is followed by a look at it in a language other than English. A text is written so a table can hold it: one whole phrase, not pieces glued together with an English plural.
