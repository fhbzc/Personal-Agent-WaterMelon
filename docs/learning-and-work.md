# Learning and Work: the agreed design

Version 1, 2026-10-21. This is the design agreed with the user in the 2026-10-21 discussion. It is kept so the build can be checked against it step by step.

To change it, add a dated note under the part it changes. The "Build status" section at the end says what is in the code.

---

## 0. What it is for

A Work run's builder makes something and its reviewer checks it. The reviewer stands in for the user, using what the user's own past work shows, in order to:

1. add or refine requirements the user would expect but did not say;
2. bring the work's form, style and way of working close to the user's.

LEARNING reads the user's work into CASES and REASONS. The reviewer asks its QUESTIONS of them, and applies what holds to the job in hand.

**Nothing the agents do is learned**, only what the user did. This includes:

- the reviewer's criteria;
- the "Added – not your words" items;
- anything on the Work tab.

## 1. What is kept

| Thing | What it holds |
|---|---|
| **Place list** | ONE list, for both learning and the local index, in the Learning panel under Work. The Settings "Search your files" card is gone. |
| **Unit** | What is read together. There are two kinds. (a) A Work project of the user's (a state-heartbeat project of the work kind): its states and tasks, its folders, and its agent records. (b) A group of folders found in a listed place (§2.2): read from disk only, never states or tasks. Each keeps how far each of its sources has been read. |
| **Case** `C-` | One deliverable, split into items. It has tags in the shared words, plus a date, where the deliverables are, its versions or attempts, what it looks like now, and how it was made. |
| **Judgment** `J-` | Something the user decided, changed, turned down, approved or let stand, in a case. It records an aspect, an object, a direction, the user's own words, why, the date and where it can be opened. The aspect is one of: an extra requirement, the form or style of the output, how a result is judged, or how the work is done. |
| **Reason** `R-` | Why the user judges so. It is either stated (the user said it) or inferred. It has a condition in the shared words (where it holds, plus exceptions, plus a text), the objects it bears on, predictions, and what would show it wrong. It records how each judgment bears on it (supports, boundary, contradicts, before a change), first and last seen, "since" for a change over time, a rival, overrides, and its history. |
| **Shared words** | Explained in §1.1. |
| **Local index** | Full text of the same places. It also provides the list of changed files. It is brought up to date at the start of every learning update - one switch and one clock for both - and never when the application starts. Every folder is walked once and only once (a folder inside another is walked with it). A JSON file is read up to 200 KB; a conversation record is kept as what was said and done (the user's words, the agent's text replies, one line per tool call: the tool and the file or pattern, never the code or a command, never what came back), bounded by what is kept of it (30 MB), not by the file's size. When the rules change, the next build keeps each file the new way, reading again only what is now kept differently, and the file is made small again (VACUUM) when more than a quarter of it is free room. |

### 1.1 The shared words

- **Fixed top levels**, written by us:
  - deliverable;
  - audience;
  - presentation (in person, read alone, used interactively, …);
  - purpose;
  - environment (tools);
  - object (what in the output a judgment is about).
- **New terms under the top**:
  - The model adds them under a parent, with a definition, down to a depth limit.
  - A new term is on trial. It is kept once a second case uses it.
  - It is folded back into its parent only after 90 days AND 100 new judgments in its branch without a second case. Nothing is folded while a place is being learned for the first time.
- **Keeping the list tidy**:
  - Duplicates are merged, and the old term stays as an alias.
  - A term that grows too wide is split.
- **What models see and use**:
  - A model sees only the part of the list it needs.
  - Matching is by these words only. Keywords and embeddings are not used.

### 1.2 Evidence strength, strongest first

1. A rule the user wrote down for the agents (`CLAUDE.md` or `AGENTS.md` they wrote).
2. A rule an agent noted from the user's words: Claude Code's memory, which can be traced back to the conversation.
3. Something the user changed or turned down.
4. Something the user approved.
5. Something the user let stand.

*Added 2026-10-24:* **Endorsed** - someone else's work the user put in the learning list (§2.5) with what of it they endorse - ranks with "changed" in weight. In the user's own words when they typed the angle; when the heartbeat added it, only words found in a record and certainly the user's. One endorsement is enough for a rule adopted from that work.

An agent-generated `CLAUDE.md` counts as ordinary evidence.

*Note 2026-10-21 (built):* whether a line is the user's is decided by code, not by the model (`learning/authorship.py`):

- a line an agent's edit put in the file (including `/init`) is the agent's;
- in a repository, a line from a commit signed as made with an agent is the agent's, and other lines are the user's;
- where nothing is known of who wrote a file, no line counts as the user's;
- a README is never a rules file.

A rules file therefore only reaches level 1 for a quoted line that is the user's; otherwise it counts as level 5 at most.

A memory entry reaches level 2 only when the user's words it came from are found: the last thing the user said before the agent wrote that memory. Otherwise it counts as level 4.

## 2. Learning

### 2.1 Places and agent records

- **Adding places**
  - One-click "Add Claude Code records" and "Add Codex records".
  - Claude Code records are in `~/.claude/projects`. Codex records are in `$CODEX_HOME/sessions` (`~/.codex/sessions` by default).
- **A record folder brings its project folders**
  - It comes from each session's working directory.
  - Sometimes that directory holds everything, such as home or the desktop. Then it comes from the project roots of the files the session read or edited.
  - These are listed as "found via …", and each can be left out.
- **A listed folder brings its sessions**
  - These are every session started in it, or reading or editing its files.
- **Never read**
  - The application's own data or workspace. The app's own agent runs are not the user's work.

### 2.2 The folder map (listed places only)

- **Bottom up, by a model**
  1. Leaf folders are summarised.
  2. Each folder above is judged from its own files and its children's summaries:
     - **groups**: which children are one thing, whether attempts at it or small tasks of one larger piece of work;
     - **covers**: whether the folder as a whole is one piece of work that most of its content belongs to. This is judged by the amount of work, not the number of folders. The children it does not cover are exceptions.
  3. A folder that covers its children is compared again with its siblings and its parent. One that does not stops the climb, and its groups become units.
- **Fixed units**
  - Work projects' folders are fixed units. They are never regrouped, and they are left out of a covering folder.
- **Keeping it current and cheap**
  - Fingerprints mean only changed folders, and the chain above them, are redone.
  - Folders that are many runs of one thing are sampled.

### 2.3 An update

- **When it runs**
  - At the set times (several a day are allowed), or by **Update now**.
- **The cutoff**
  - Each update has a fixed **cutoff**.
  - A time missed while the computer was off starts one update when it is back, with that time as its cutoff.
- **One update at a time**
  - A set time that passes while an update is open is **cancelled**, and this is said on the update. The next update waits for the next set time.
- **Stopping and carrying on**
  - **Daily token limit**: when it is reached, the update waits until the next day.
  - **Pause / Continue.**
  - **Restart**: after the application restarts, the update carries on with the same cutoff.
- **Automatic updates**
  - These run only when the switch is ON and at least one time is set. The defaults are OFF and no times.
- **Steps**
  1. **Units and order**
     - Units with agent conversations or rules come first, then by most recent change.
     - A first reading covers the last 6 months first, and what is older at the end.
  2. **Reading each unit, in time order**
     - **Conversations**: the user's words in full, the agent's replies cut short, and the agent's edits.
     - **Rules**: `CLAUDE.md`, `AGENTS.md` and the agent's memory, in full when new and as a diff when changed.
     - **Files**:
       - the first time, the list of files and the start of the latest deliverables;
       - after that, a diff against the copy kept from the last reading.
     - **Who made a file change**:
       - lines that match an agent's edit are the AGENT'S;
       - everything else is the USER'S;
       - a file no agent touched was changed by the user.
     - **Git commits**, as context.
     - **Tasks**, for a Work project.
     - **Secrets**:
       - Secrets (keys, tokens, passwords, `.env` values) are removed before anything is sent or kept.
     - **Files are only read**:
       - Files are never written.
       - Files being written are read again later.
       - Append-only records are read exactly up to the cutoff.
     - **What the model writes**:
       - The model writes cases and the user's judgments.
       - Code checks them:
         - judgments by the agent are dropped;
         - a quote is kept only if it is really in the text;
         - "written" or "noted" is allowed only if the judgment rests on a rule or memory file.
  3. **Reasons**, once everything is read (§2.4).
- **Spending**
  - Learning calls are counted as Work learning, split into folders / read / reasons / terms.

### 2.4 Reasons

**EXPLAIN.** Each new judgment is matched against shortlisted reasons, chosen by code:

- the same or a neighbouring object;
- a prediction about it;
- a condition its case falls in.

The model says which reasons explain it (and whether it was predicted) and which conflict. The reasons' wording is never changed here. Judgments that nothing explains go to the pool.

**REVISE.** This happens only when a conflict arises. The model chooses one of these:

- **a** the reason's condition does not hold here, so this is a boundary;
- **b** another reason wins here;
- **c** narrow the reason or replace it;
- **d** a one-off;
- **e** the user CHANGED from a date T onwards.

A rewrite (c or e) is checked against ALL of the reason's evidence. It must meet every one of these:

1. It handles the conflicting judgment.
2. None of the user's own words is lost. Under (e), words from before T may be set aside only if there is later strong evidence from the user against them.
3. At least 80% of the rest is kept. A confirmed prediction counts twice.
4. Under (e), these also apply:
   - T is named;
   - everything set aside is dated before T (code checks this);
   - after T, at least 2 cases support the new version in the same direction, or 1 of the user's own words does.

If the rewrite is refused, the model is asked again and told why. If it is refused twice, the rewrite becomes a **rival**:

- A reason has at most one rival at a time.
- The rivalry is settled in one of three ways:
  - one side leads by 3 weighted points;
  - after 90 days with no clear winner, the original stays;
  - if both are contradicted, they are revised jointly.

**ABDUCE.** From the pool, the model finds reasons that explain at least 2 judgments, or 1 where the user said why. Such a reason:

- is falsifiable;
- is as deep as can still be shown wrong;
- may cover different objects.

The model may also link a judgment to an existing reason.

**After a first learning**, duplicate reasons (sharing more than half their support) are merged.

**Time.** There is no fixed decay. Every judgment and reason carries its dates, and the model judges age; option (e) is how it says the user changed.

**Active** means the reviewer is given the reason as a rule. It needs any one of:

- 1 written or noted rule;
- changes or approvals in 2 cases;
- 3 cases the user let stand.

**Kept history.** Old versions, set-aside evidence and retired reasons are kept in history and never deleted.

### 2.5 The learning list (2026-10-24, the user's design)

- **Items beside the user's own folders**
  - A web page or site, a GitHub repository, a passage of text, a folder or a file.
  - Each has: **whose** (*mine* - learned in full, its decisions the user's judgments; *others* - learned as a reference the user endorses, never as their own), **what** (cases, rules, reference - any mix), **angle** (what of it to learn, the purpose), **scope** (a page, the pages under an address, a repository, a folder, a file, the passage), **cadence** (once, at every update, every N days), and a state (on or off) and status (new, waiting for the user, learned, unreachable).
  - Stored in `learn_items`; each is a unit of its own (`item:<id>`), learned in the same updates under the same daily limit.
- **The angle goes to the reading model**
  - For the user's own work it is an emphasis; for someone else's it bounds what is learned - they endorse that part, not the rest.
  - Someone else's work is read with its own prompt: the work as a case (its address as where it is, marked as someone else's, with what to take and not), what the user endorses as **endorsed** judgments, and - when asked - the rules it shows within the angle, kept as reasons with the source **adopted**.
  - When the heartbeat added it, its purpose is a hypothesis: the model is shown the evidence and learns what the material actually shows.
- **Online**
  - Only the item's scope; public pages only, signed in to nothing; robots.txt kept to, one request a second to a site.
  - A page that addresses an assistant, or hides such words from a reader (comments, styled away, invisible characters), is set aside and said so on the item.
  - The reading model has no tools and is told that what it reads is material, never an instruction.
  - A page up to 200,000 characters is kept as a copy (address, time, hash); a larger one only as its address - the reviewer reads it on the web.
  - A repository of the user's own GitHub account (from their profile in Connections) is theirs, and their own review comments are read too.
  - A page that can no longer be read is not updated: what was learned from it stays, and the item says since when.
- **Adding** - the Learning tab's "Add to learning", a "Learn" button beside "Delegate work" on selected text (with the page it was selected on), Explorer's "Add to learning" (`--learn PATH`).
  - As Delegate work: a first look, then a **learning plan** drafted by a model with no tools (title, whose and why, what to learn, angle, scope, cadence, what is left out, and the tokens a reading takes, estimated by code); the user changes it and adds it - now, or at the next update. An item over 500,000 tokens is marked large.
- **The heartbeat adds what the user endorses** (`learning/interests.py`)
  - Once a day after a round, while the learning's switch is on.
  - The model judges whether the evidence is enough, told: visiting is not endorsing; a statement counts only when it is certainly the user's.
  - Code holds that each piece of evidence is a record that exists, a quote is kept only where those words are in it, the thing is one the records name, and nothing on the never list or already listed is added. No daily cap.
- **Waiting for the user**
  - The first reading of the user's own work on the web, and of anything the heartbeat added, is given to no run until the user keeps it; not kept, what was learned goes and the item is turned off.
- **Turning off, deleting, the never list**
  - Any lesson - a project, a folder, an item - can be turned off: not read, and nothing learned from it given (`LeaveOut.holding`).
  - Deleted, everything learned from it goes (its cases, judgments and their links; the reasons are weighed again, and go with nothing left), and its source - folders, address, or the unit - goes on the never list.
  - The never list is shown and each entry can be taken off; the heartbeat cannot add past it (code, not the prompt).
- **Given to a run as what it is**
  - A case of someone else's work says so, with what the user endorses; a reason adopted from it says where it comes from; the reviewer's system prompt tells it to weigh the user's own and what they endorse for what each is.
  - A web page or a kept passage can be a reference, given only as it was a candidate; the pages in the list may be opened by the reviewer and the work agent.

## 3. A Work run

### 3.1 Questions

1. The reviewer first sees only the instruction. It writes the job's terms from the shared words, and its QUESTIONS. Each question is of one kind:
   - an extra requirement;
   - form or style;
   - how a result is judged;
   - how the work is done;
   - a fact or limit of this project.
   Some questions are fixed for each deliverable type; the reviewer adds the job's own.
2. The builder can ask questions while it works. They go to the reviewer, which answers them.

### 3.2 Finding

- **Reasons**
  1. Code first checks whether the job falls inside each reason's condition, by the shared words:
     - **covered**;
     - **near**: one dimension misses only by a sibling term;
     - **unknown**: the job says nothing about a dimension the reason needs;
     - **outside**: dropped.
  2. A model then reads each remaining condition and says whether it holds for this job: yes (with one sentence on what it asks of this job), no, or unknown. An unknown becomes a question for the reviewer. Why a reason does not hold is written in a debugger run only: such a reason reaches no reviewer.
  3. The same call says which of the reviewer's questions each reason answers (3 answers it, 2 bears on it, 1 touches it), and whether one that answers none is still expected of this job.
  4. Which reasons are given, and how:
     - for each question, at most 5 that answer it or bear on it; up to 5 expected ones in a section of their own;
     - stated reasons always come first;
     - inferred reasons are given only with 2 or more observations, labelled as inferred;
     - each comes with its last-seen date, a one-line rival if it has one, and one short example.
- **Cases**, chosen as the reasons are:
  1. Code takes the 10 with most of the job's terms: in each dimension, the share of the job's terms the case has (a case of all three of the job's deliverables comes before one of one). How many judgments a case holds decides only between equals.
  2. The same model call is shown each one's terms, what it looks like, how it was made, how many judgments it holds and when it was last worked on. It says how alike each is to the job (0–3), which questions it helps answer, and which cases are the same project.
  3. The 3 most alike are given in full, one place a project (its other cases with it); none said not to be alike. Each in full: where the deliverables are, versions, what it looks like, every judgment, how it was made.
  4. When those in full are long, a second call picks, of each, the judgments that bear on the questions (at most 10; a project's cases together).
  5. The rest are given as a short index. A fact or a way of working no reason answers points to the cases said to answer it.
  6. With no model: by their terms, as before.
- **Also given**:
  - judgments in those cases that no reason explains yet;
  - for facts of the project: its files (search).
- **Size**
  - There is one budget for the run. A case is expanded only once; after that it is referred to by its id.
- **Tools**: `open_case`, `open_reason` (paged, closest first), `search_files`, `read_file`.

### 3.3 Answering

- **Which source wins**, strongest first:
  1. a reason the user stated that holds here;
  2. an inferred reason that holds here;
  3. **common practice found on the web and chosen by the user's related preferences**: neighbouring objects, the same deliverable or audience, how similar cases ended up, the user's field and tools;
  4. common practice alone, or the reviewer's own assumption.
- **Searching the web for what the record cannot answer**
  - The queries are framed by the user's relevant context: the situation and related reasons.
  - The queries use general public words. Names, paths, unpublished data or anything only the user knows are never included.
  - Queries are checked by the web guard.
  - A hard external rule (a funder's or journal's) wins over a preference. General practice gives way to the user's reasons, and any conflict is said.
  - Answers come with their source URLs.
- **The user's past work is evidence, not a standard**: the reviewer follows the user's ways and uses its own judgement too. What they did before shows what they knew and wanted then; where it falls short for this work, the reviewer says so and does not carry it over. What counts is a result the user is satisfied with.
- **References**: after the contract, a call of its own proposes, for each deliverable, up to 3 files or folders from the user's past work (what each is, why it fits, what might be taken). The reviewer, in its own context, keeps at most 2 a deliverable, or none, with what to take from each and what not to. Only those reach the card the builder reads, each checked to exist, be readable by the builder and lie in no folder left out.
- **What the reviewer writes**
  - The reviewer writes criteria or "Added – not your words" (core / needed / nice).
  - **Each is marked as the reviewer's**, with its grounds.
  - Nothing from a run is kept as learning.
- **The trace (`work_run_questions`)**
  - It logs, for analysis only:
    - the question;
    - its kind and who asked it;
    - the source tier;
    - the reason, case and judgment ids;
    - coverage and hold verdicts;
    - the queries and which context shaped them;
    - the URLs and the reasoning;
    - which criterion it became, and its outcome.
  - It never changes learning.
- **Lesson cards**: no longer written. The existing ones are kept read-only and are not given to the reviewer.

### 3.4 The debugger's ways

| Way | What it gets |
|---|---|
| Full | Everything above. |
| B | The same steps: questions, terms, builder questions. NO context and NO tools: no reasons, cases or index. The reviewer answers its own questions, each marked as its own assumption. It may search the web, but without the user's context. Nothing is kept. |
| Single | Unchanged. |
| Builder (all ways) | Never sees the learning (it asks the reviewer); in the full way, only the references the reviewer chose, on its cards. |

## 4. The panel (the Learning tab, after Work - 2026-10-24)

*Amended 2026-10-24:* Learning is a tab of its own. Besides what is below: what waits for the user to look at, then every lesson in three groups - **your own folders and projects**, **added by you**, **added by the heartbeat** (with its evidence and purpose) - each with what was learned of it, and Turn off / Turn on, What was learned, Delete (asks first); an item also Change its plan. Then the **never list**, each entry taken off. The local index and the learning each have a progress bar.

- **Updates**
  - The auto-learning switch and update times.
  - **While an update runs**: Update now is grey and Pause is shown.
  - **When none runs**: Update now; a manual update can also be paused.
  - **When paused**: Continue. Set times are skipped until then.
  - **When the daily limit is used up**: Update now is grey, with "Today's token limit is used up".
- **Progress**
  - The cutoff, the share read (by amount of text), the unit being read, tokens today against the limit, and cancelled times.
- **Places**
  - Folders.
  - The one-click agent records.
  - Found folders, with "leave out".
- **Spending**
  - The learning token chart.
- **Removed**
  - Each project's Start / Update learning button.

**Newspaper** follows the same logic: a switch beside its times. It makes issues by itself only when the switch is ON and at least one time is set.

## 5. Fresh start

- The old lessons are BACKED UP once:
  - the database copy;
  - the `user_nature` folder.
- No cases are extracted from the old stages; learning starts from nothing.
- What is no longer used:
  - live stages as a source;
  - work natures;
  - patterns and principles of the work kind;
  - the old auto-learning clock;
  - lesson cards in runs;
  - `retrieve.py` in runs.
- What stays as it was:
  - a Work run's own card and stage tree (`work:` scope);
  - notification and connection lessons, and their pattern grouping;
  - personal natures.

## 6. Before switching (to do)

Test whether the case package or the stage package gives the reviewer more, on:

1. requirements added or refined;
2. style and visual preferences.

The test:

- Use 10–15 past projects, each starting from its first instruction.
- The ground truth is what the user later added. The user checks the uncertain items.
- Only data from before each project is used.
- Three ways are compared: A (stages), B (case + reasons), C (nothing).
- Scoring is blind. It counts:
  - recall per kind;
  - harmful items;
  - irrelevant items;
  - tokens.

---

## Build status

- **2026-10-21: built, untested.**
  - **The learning package** (`personalized_agent/learning/`), with its tables in `database.py`:
    - `vocab`, `scrub`, `records`, `store`, `places`, `ask`, `folders`, `reading`, `match`, `reasons`, `service`, `find`, `fresh`.
  - **App wiring** (`app.py`):
    - `app.learning` is started in place of the old learning clock;
    - the one-time backup (`learning_fresh_start`);
    - the Newspaper carry-over.
  - **Work runner** (`agent/work/runner.py`):
    - the questions step replaces the "what to know" step and lesson cards;
    - the reviewer has `open_case` / `open_reason`;
    - the builder can ask questions;
    - `work_run_questions` logs the questions and answers;
    - the way with no context answers its own questions;
    - the builder no longer has the stage tools (it keeps `search_records` and `search_files`).
  - **Local index**: its folders, its switch and its times are the learning's. `POST /api/index` takes only "build".
  - **Routes**: `GET` and `POST /api/learning`.
  - **Interface**:
    - the Learning panel at the bottom of Work;
    - the per-project learning buttons and `#xp-auto` are removed;
    - the Settings card "Search your files" is removed;
    - Newspaper has its "Make an issue by itself" switch.
  - **Words**: 66 texts merged into the 8 language tables.
  - **Docs**:
    - the design doc is amended in §119.2 and §126 (the check says IDENTICAL);
    - the v0.2.0 notes and the public README log are updated.
  - **Tests**:
    - `tests/test_learning.py` is new;
    - the older tests that pinned the replaced behaviour were updated;
    - nothing has been run.
- **Not done yet**:
  - §6, the test of the case package against the stage package;
  - the panel has not been seen in a browser.
