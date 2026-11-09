"""The user's own say over their work: a task or project named or put away
(section 34.5), each kept as a lesson the next run is told of; the same for a
list's groups; the chat bubble's keys; what is in front of them, for the chat;
and the latest outcome one press away from a to-do."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from support import ROOT, TemporaryAgent

from personalized_agent.agent.front_screen import front_note, front_now
from personalized_agent.heartbeat.states import task_projects as tp
from personalized_agent.heartbeat.states.project_round import ProjectRound
from personalized_agent.heartbeat.states.tasks import Kept, Task


class _WithAProject(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.hb = self.app.state_heartbeat
        self.tasks = tp.RunTasks(self.app.db, "live")
        self.kept = Kept(self.app.db, "live")
        row = Task(id="proj_a", number=1, seat=1)
        row.profile = {"scheme": tp.SCHEME}
        row.described = {"title": "Tide tables", "line": "A study of the tides",
                         "summary": "Plotting", "last_task": "t2", "minutes": 30.0}
        self.kept.save([row])
        self.tasks.add([{"id": one, "title": f"Task {one}", "title_display": f"Task {one}",
                         "summary": f"Did {one}", "minutes": 15.0, "states": [f"s_{one}"],
                         "ends_at": f"2026-11-0{n}T10:00:00+00:00", "project_id": "proj_a"}
                        for n, one in ((1, "t1"), (2, "t2"))])

    def tearDown(self) -> None:
        self.agent.cleanup()

    def row(self) -> Task:
        return self.kept.by_ids(["proj_a"])[0]

    def lessons(self):
        return self.app.db.query("SELECT action, summary, kind FROM lesson_evidence "
                                 "ORDER BY at")


class NamingTests(_WithAProject):

    def test_a_name_stays_through_the_models_naming(self) -> None:
        self.hb.rename_project("proj_a", "  My   tides ")
        row = self.row()
        self.assertEqual(row.described["title"], "My tides")
        self.assertIn("Tide tables", row.described["names_before"])
        run = ProjectRound(self.hb, self.hb.where("live"), {})
        described, english = dict(row.described), {}
        run._name(row, described, english, {"title": "Something else", "line": "Still the tides"},
                  40.0)
        self.assertEqual(described["title"], "My tides")
        self.assertEqual(described["line"], "Still the tides", "what it is is still written")

    def test_the_name_goes_with_the_work_to_the_project_carrying_it_on(self) -> None:
        self.hb.rename_project("proj_a", "My tides")
        heir: dict = {}
        ProjectRound._the_users_name(heir, [self.row()])
        self.assertEqual(heir["title"], "My tides")
        self.assertTrue(heir["named_by_user"])

    def test_a_task_is_renamed_in_both_copies(self) -> None:
        self.hb.rename_task("t1", "Check the tide gauge")
        one = self.tasks.one("t1")
        self.assertEqual((one["title"], one["title_display"]), ("Check the tide gauge",) * 2)

    def test_an_empty_name_and_an_unknown_id(self) -> None:
        with self.assertRaises(ValueError):
            self.hb.rename_project("proj_a", "   ")
        with self.assertRaises(KeyError):
            self.hb.rename_project("proj_none", "A name")
        with self.assertRaises(KeyError):
            self.hb.delete_task("t_none")

    def test_never_during_a_run_which_would_write_over_it(self) -> None:
        with mock.patch.object(self.hb, "_progress", {"running": True}):
            with self.assertRaises(ValueError):
                self.hb.rename_project("proj_a", "My tides")


class PuttingAwayTests(_WithAProject):

    def test_a_task_put_away_is_read_by_nothing_but_its_states_stay_held(self) -> None:
        self.hb.delete_task("t2")
        self.assertEqual([one["id"] for one in self.tasks.of_project("proj_a")], ["t1"])
        self.assertEqual([one["id"] for one in self.tasks.window()], ["t1"])
        self.assertEqual(self.tasks.held_states(["s_t2"]), {"s_t2"},
                         "else the next run writes them down as new")
        row = self.row()
        self.assertEqual(row.described["minutes"], 15.0)
        self.assertEqual(row.described["tasks"], 1)
        self.assertEqual(row.described["summary"], "Did t1", "it was saying the task put away")

    def test_the_last_task_put_away_ends_the_project(self) -> None:
        self.hb.delete_task("t1")
        self.hb.delete_task("t2")
        self.assertTrue(self.row().retired_at)

    def test_a_project_put_away_goes_with_its_tasks_and_is_looked_for_by_nothing(self) -> None:
        self.hb.delete_project("proj_a")
        self.assertEqual(self.tasks.window(), [], "else it is grouped again at the next run")
        self.assertEqual(self.kept.live(), [])
        row = self.row()
        self.assertTrue(row.retired_at and row.described["deleted_by_user"])
        picture = self.hb.projects_picture()
        shown = [one for one in picture["projects"]
                 if not one["ended"] or one.get("tasks")]
        self.assertEqual(shown, [], "the Overview draws one ended only with tasks of its own")


class KeptAsLessonsTests(_WithAProject):

    def test_each_of_the_four_is_a_lesson_saying_what_it_was(self) -> None:
        self.hb.rename_project("proj_a", "My tides")
        self.hb.rename_task("t1", "Check the gauge")
        self.hb.delete_task("t2")
        self.hb.delete_project("proj_a")
        said = {one["action"]: one["summary"] for one in self.lessons()}
        self.assertEqual(set(said), {"renamed_project", "renamed_task", "deleted_task",
                                     "deleted_project"})
        self.assertIn('"Tide tables" to "My tides"', said["renamed_project"])
        self.assertIn("A study of the tides", said["deleted_project"])
        self.assertTrue(all(one["kind"] == "general" for one in self.lessons()))

    def test_the_next_run_is_told_and_told_they_are_the_users_own(self) -> None:
        self.hb.delete_project("proj_a")
        told = ProjectRound(self.hb, self.hb.where("live"), {})._the_users_own("reading tide data")
        self.assertIn("WHAT THE USER HAS DONE THEMSELVES", told)
        self.assertIn('deleted the project "Tide tables"', told)
        self.assertIn("WHAT THE USER HAS DONE THEMSELVES", tp.WRITER_SYSTEM)

    def test_nothing_done_nothing_said(self) -> None:
        self.assertEqual(ProjectRound(self.hb, self.hb.where("live"), {})._the_users_own("x"), "")

    def test_a_lesson_that_cannot_be_kept_leaves_the_name_standing(self) -> None:
        with mock.patch.object(self.app.lessons, "the_user_did", side_effect=OSError("full")):
            self.assertEqual(self.hb.rename_project("proj_a", "My tides")["title"], "My tides")


class ListGroupsTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_a_change_is_a_lesson_and_a_grouping_is_told_of_the_others(self) -> None:
        from personalized_agent.agent.feedback.list_groups import ListGroups
        asked = []

        def invoke(request, role=None):
            asked.append(request.messages[0]["content"])
            return SimpleNamespace(text='{"groups": []}')
        items = [{"id": "a", "text": "Read the tide paper"}, {"id": "b", "text": "Book a room"}]
        lists = ListGroups(self.app.db, lambda: SimpleNamespace(invoke=invoke),
                           {"todo": lambda: items, "assigned": lambda: []})
        lists.lessons, lists.retriever = self.app.lessons, self.app.lesson_retriever
        lists._said("assigned", 'Renamed the group "Reviews" to "Tide reviews"')
        rows = self.app.db.query("SELECT action, summary FROM lesson_evidence")
        self.assertEqual(rows[0]["action"], "moved_list_groups")
        lists.group("todo")
        self.assertIn("Tide reviews", json.loads(asked[0])["what_the_user_did_elsewhere"])


class ChatBubbleKeysTests(unittest.TestCase):

    def _ui(self, **bubble):
        from personalized_agent.ui.desktop import DesktopAgent
        ui = DesktopAgent.__new__(DesktopAgent)
        ui._chat_bubble = SimpleNamespace(minimize=mock.Mock(), **bubble) if bubble else None
        ui.open_chat_bubble = mock.Mock()
        return ui

    def test_open_it_is_minimised_and_kept(self) -> None:
        ui = self._ui(is_open=True, minimized=False)
        ui.toggle_chat_bubble()
        ui._chat_bubble.minimize.assert_called_once()
        ui.open_chat_bubble.assert_not_called()

    def test_minimised_or_closed_it_is_opened(self) -> None:
        for ui in (self._ui(is_open=True, minimized=True), self._ui(is_open=False, minimized=False),
                   self._ui()):
            ui.toggle_chat_bubble()
            ui.open_chat_bubble.assert_called_once()

    def test_the_shortcut_pressed_again_closes_it_minimised_or_not(self) -> None:
        """The user, 2026-11-08: "打开chat的快捷键再嗯一下是关掉（而不是最小化)"."""
        for minimized in (False, True):
            ui = self._ui(is_open=True, minimized=minimized, close=mock.Mock())
            ui._chat_open_or_closed()
            ui._chat_bubble.close.assert_called_once()
            ui._chat_bubble.minimize.assert_not_called()
            ui.open_chat_bubble.assert_not_called()
        for ui in (self._ui(is_open=False, minimized=False, close=mock.Mock()), self._ui()):
            ui._chat_open_or_closed()
            ui.open_chat_bubble.assert_called_once()

    def test_it_is_a_shortcut_of_its_own_in_settings(self) -> None:
        from personalized_agent.common.config.schema import SCHEMA_BY_KEY
        from personalized_agent.platform_layer.hotkeys import BY_KEY
        self.assertEqual(BY_KEY["open_chat"]["default"], "Ctrl+Alt+C")
        self.assertEqual(SCHEMA_BY_KEY["shortcut_open_chat"].default, "Ctrl+Alt+C")


class FrontScreenTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.app.config.set("listener_enabled", True)
        self.n = 0

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _event(self, kind: str, application: str = "", **data) -> None:
        self.n += 1
        self.app.db.execute(
            "INSERT INTO raw_events (id, event_type, source, timestamp, application, data, "
            "metadata, processed) VALUES (?, ?, 'test', ?, ?, ?, '{}', 0)",
            (f"e{self.n}", kind, f"2026-11-04T10:00:{self.n:02d}+00:00", application,
             json.dumps(data)))

    def test_the_last_window_not_ours_with_its_address_and_what_it_shows(self) -> None:
        self._event("browser.navigation", title="Tide tables", url="https://example.com/tides")
        self._event("application.focused", "chrome", pid=1, window_title="Tide tables - Chrome")
        self._event("screen.text", window_title="Tide tables - Chrome", text="High water at noon")
        self._event("application.focused", "python", pid=os.getpid(), window_title="WaterMelon")
        said = front_now(self.app.db, self.app.config)
        self.assertEqual(said["application"], "chrome")
        self.assertEqual(said["address"], "https://example.com/tides")
        self.assertEqual(said["text"], "High water at noon")

    def test_a_file_by_its_name_in_the_title(self) -> None:
        self._event("filesystem.opened", path=r"C:\work\tides.docx")
        self._event("filesystem.opened", path=r"C:\work\other.docx")
        self._event("application.focused", "word", pid=1, window_title="tides.docx - Word")
        said = front_now(self.app.db, self.app.config)
        self.assertEqual(said["file"], r"C:\work\tides.docx")
        self.assertNotIn("address", said)

    def test_a_page_of_another_window_is_not_this_ones(self) -> None:
        self._event("browser.navigation", title="Tide tables", url="https://example.com/tides")
        self._event("application.focused", "code", pid=1, window_title="notes.py - Code")
        self.assertNotIn("address", front_now(self.app.db, self.app.config))

    def test_what_the_screen_showed_of_a_password_is_left_out(self) -> None:
        self._event("application.focused", "chrome", pid=1, window_title="Sign in")
        self._event("screen.text", window_title="Sign in", text="words")
        with mock.patch("personalized_agent.agent.front_screen.shows_credentials",
                        return_value=True):
            self.assertNotIn("text", front_now(self.app.db, self.app.config))

    def test_the_model_is_told_the_message_may_have_nothing_to_do_with_it(self) -> None:
        self._event("application.focused", "chrome", pid=1, window_title="Tides")
        note = front_note(self.app.db, self.app.config)
        self.assertIn("may have nothing to do with it", note)
        self.assertIn("do not mention it", note)

    def test_taken_off_in_the_bubble_it_does_not_go_with_the_message(self) -> None:
        from types import SimpleNamespace

        from personalized_agent.ui.desktop import DesktopAgent
        self._event("application.focused", "chrome", pid=1, window_title="Tides")
        desk = SimpleNamespace(app=self.app, _chat_bubble=SimpleNamespace(front_off=""))
        self.assertIn("Tides", DesktopAgent._front_for_chat(desk)[0])
        desk._chat_bubble.front_off = "Tides"
        self.assertEqual(DesktopAgent._front_for_chat(desk), ("", []))
        self._event("application.focused", "word", pid=1, window_title="Notes")
        self.assertIn("Notes", DesktopAgent._front_for_chat(desk)[0], "another window goes again")

    def test_the_picture_that_goes_is_handed_to_the_bubble_to_show(self) -> None:
        """The user, 2026-11-08: the picture sent with a message shown under it - at once, small,
        before its words are read ("你等识别，你可以先把图片截图发上来"); then said how it went."""
        import queue
        from personalized_agent.ui.desktop import DesktopAgent
        self._event("application.focused", "chrome", pid=1, window_title="Tides")
        shown = []
        bubble = SimpleNamespace(front_off="", picture_sent=lambda *said: shown.append(("shown",) + said),
                                 picture_settled=lambda *said: shown.append(("settled",) + said))
        order = []
        small = object()

        def for_a_message(title=""):
            order.append("waited")
            return {"path": "C:/shots/tides.png", "words": "High tide 6pm", "title": title}
        pictures = SimpleNamespace(
            again_for_a_message=lambda: order.append("taken"),
            preview=lambda: order.append("preview") or small, for_a_message=for_a_message,
            why="", blocks=lambda shot: [{"media_type": "image/jpeg", "data": "AAAA"}]
            if shot.get("path") else [])
        posted = queue.Queue()
        desk = SimpleNamespace(app=SimpleNamespace(db=self.app.db, config=self.app.config,
                                                   front_pictures=pictures),
                               _chat_bubble=bubble, queue=posted, _chat_model_sees=lambda: False)
        note, seen = DesktopAgent._front_for_chat(desk)
        self.assertEqual(order, ["taken", "preview", "waited"], "shown before it is waited for")
        self.assertIn("High tide 6pm", note)
        self.assertEqual(len(seen), 1)
        while not posted.empty():
            kind, call = posted.get_nowait()
            self.assertEqual(kind, "call")
            call()                               # on Tk's thread, in the application
        self.assertEqual(shown, [("shown", small), ("settled", "words", "")],
                         "the small one at once; then: its words only")
        shown.clear()
        pictures.for_a_message = lambda title="": {}
        pictures.why = "something like a password was on it"
        DesktopAgent._front_for_chat(desk)
        while not posted.empty():
            posted.get_nowait()[1]()
        self.assertEqual(shown, [("shown", small), ("settled", "not", pictures.why)])

    def test_nothing_while_the_listener_is_off_or_nothing_is_known(self) -> None:
        self.assertEqual(front_note(self.app.db, self.app.config), "")
        self._event("application.focused", "chrome", pid=1, window_title="Tides")
        self.assertIn("Window title: Tides", front_note(self.app.db, self.app.config))
        self.assertEqual(front_note(self.app.db, {"listener_enabled": False}), "")

    def test_told_to_both_models_and_never_written_down(self) -> None:
        said = []
        two = self.app.two_speed

        def invoke(request, role=None):
            said.append(request.messages[-1]["content"])
            return SimpleNamespace(text='{"reply": "Hello", "full_agent": "stop"}')
        with mock.patch.object(two, "models", SimpleNamespace(invoke=invoke)):
            done = two.send("What is this?", front="\n\n[Window title: Tides]")
        self.assertIn("[Window title: Tides]", said[0])
        kept = self.app.conversations.messages(done["conversation_id"], limit=None)
        self.assertEqual(kept[0]["content"], "What is this?")


class TheLatestOutcomeTests(unittest.TestCase):

    def test_the_newest_finished_else_the_newest_try_in_work(self) -> None:
        from test_panels import _javascript_engine
        kind, engine = _javascript_engine()
        if kind != "chrome":
            self.skipTest("no browser available to render the page")
        script = (ROOT / "personalized_agent" / "api" / "web" / "app.js").read_text(encoding="utf-8")
        part = script[script.index("/* THE LATEST OUTCOME"):script.index("/* The rows of a to-do's work")]
        # Its button, as each outcome's is made (`madeButtons` calls it).
        start = script.index("function ovTodoOpen(version, thing)")
        part += "\n" + script[start:script.index("\n}\n", start) + 3]
        page = """<!doctype html><html><head><meta charset='utf-8'></head><body><script>
const XP_HANDED_KIND={};const escapeHtml=(s)=>String(s);</script><script>%s</script>
<script>window.addEventListener('load',()=>{const finished=(run,version)=>({state:'finished',version,
run,unit:'u',result:'/out/'+run+'.txt'});
document.title=JSON.stringify({
both:ovTodoLatest([finished('r1',1),finished('r2',2)]),
going:ovTodoLatest([finished('r1',1),finished('r2',2),{state:'ongoing',run:'r3',unit:'u'}]),
none:ovTodoLatest([{state:'stopped',run:'r1',unit:'u1'},{state:'ongoing',run:'r2',unit:'u2'}]),
stopped:ovTodoLatest([{state:'stopped',run:'r1',unit:'u1',result:'/out/r1',result_kind:'folder'}]),
plan:ovTodoLatest([{state:'drafting'}])});});</script></body></html>""" % part
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "latest.html"
            path.write_text(page, encoding="utf-8")
            done = subprocess.run(
                [engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=3000",
                 "--dump-dom", path.as_uri()],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        said = json.loads(re.search(r"<title>(.*?)</title>", done.stdout, re.S).group(1)
                          .replace("&quot;", '"').replace("&amp;", "&")
                          .replace("&lt;", "<").replace("&gt;", ">"))
        self.assertIn('data-todo-run="r2"', said["both"])
        self.assertIn('data-todo-result="/out/r2.txt"', said["both"])
        self.assertIn("Open the latest outcome", said["both"])
        self.assertEqual(said["going"], "", "the latest not ended: carried on from its row "
                         "(the user, 2026-11-08), no earlier one's outcome")
        self.assertEqual(said["none"], "", "there is no Work panel to send them to")
        self.assertIn('data-todo-result="/out/r1"', said["stopped"], "what a stopped one made opens")
        self.assertEqual(said["plan"], "")

    def test_opening_says_so_and_opens_once_though_the_list_is_drawn_anew(self) -> None:
        from test_panels import _javascript_engine
        kind, engine = _javascript_engine()
        if kind != "chrome":
            self.skipTest("no browser available to render the page")
        script = (ROOT / "personalized_agent" / "api" / "web" / "app.js").read_text(encoding="utf-8")
        start = script.index('document.addEventListener("click", async (event) => {\n'
                             '  const going') if "\r\n" not in script else script.index(
            'document.addEventListener("click", async (event) => {\r\n  const going')
        part = script[start:script.index("/* DELEGATE WORK - and every start")]
        page = """<!doctype html><html><head><meta charset='utf-8'></head><body><div id="list"></div><script>
const $=(s)=>document.querySelector(s),$$=(s)=>[...document.querySelectorAll(s)];
const ovOpening=new Set(),experienceState={},calls=[];let done;
const gate=new Promise((go)=>{done=go;});
const toast=()=>{},openDelegateDraft=()=>{};
const api={post:async(url)=>{calls.push(url);await gate;return {};}};
const draw=()=>{$('#list').innerHTML='<button class="ov-todo-result" data-todo-run="r1" '
+'data-todo-handed="0">Open the latest outcome</button>';ovMarkOpening();};
</script><script>%s</script><script>
window.addEventListener('load',async()=>{draw();const said={};
$('.ov-todo-result').click();$('.ov-todo-result').click();
said.words=$('.ov-todo-result').textContent;said.frozen=$('.ov-todo-result').disabled;
draw();said.redrawn=$('.ov-todo-result').textContent;said.stillFrozen=$('.ov-todo-result').disabled;
$('.ov-todo-result').click();said.opened=calls.length;
done();await new Promise((go)=>setTimeout(go,50));
said.after=$('.ov-todo-result').textContent;said.free=!$('.ov-todo-result').disabled;
$('.ov-todo-result').click();said.again=calls.length;
document.title=JSON.stringify(said);});</script></body></html>""" % part
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "opening.html"
            path.write_text(page, encoding="utf-8")
            done = subprocess.run(
                [engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=3000",
                 "--dump-dom", path.as_uri()],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        said = json.loads(re.search(r"<title>(.*?)</title>", done.stdout, re.S).group(1)
                          .replace("&quot;", '"').replace("&amp;", "&")
                          .replace("&lt;", "<").replace("&gt;", ">"))
        self.assertEqual((said["words"], said["frozen"]), ("Opening...", True))
        self.assertEqual((said["redrawn"], said["stillFrozen"]), ("Opening...", True))
        self.assertEqual(said["opened"], 1, "pressed three times, opened once")
        self.assertEqual((said["after"], said["free"]), ("Open the latest outcome", True))
        self.assertEqual(said["again"], 2, "and it can be opened again when it is done")

    def test_it_comes_before_work_status(self) -> None:
        script = (ROOT / "personalized_agent" / "api" / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn('${delegate}${ovTodoLatest(versions)}<button type="button" class="small '
                      'ov-todo-track"', script)


class FaceTurnedTests(unittest.TestCase):

    def test_out_of_the_top_it_hangs_upside_down_its_mouth_above_its_eyes(self) -> None:
        from PIL import ImageDraw

        from personalized_agent.agent.character import watermelon
        rig = watermelon.Watermelon()
        rig.leaves = rig.leaves.copy()
        rig.leaves.putalpha(0)
        rig.body = rig.body.copy()
        rig.body.putalpha(0)

        def eye_over_mouth(body, scale, *_rest):
            pen = ImageDraw.Draw(body)
            pen.rectangle((40 * scale, 50 * scale, 46 * scale, 56 * scale), fill=(255, 0, 0, 255))
            pen.rectangle((40 * scale, 70 * scale, 46 * scale, 76 * scale), fill=(0, 0, 255, 255))

        def height_of(frame, channel):
            return frame.getchannel(channel).point(lambda v: 255 if v > 200 else 0).getbbox()[1]
        with mock.patch.object(watermelon, "draw_face", eye_over_mouth), \
                mock.patch.object(watermelon, "_extras", lambda *_a: None):
            upright = rig.frame(size=256, edge="top")
            hanging = rig.frame(size=256, edge="top", turn=1.0)
        self.assertLess(height_of(upright, "R"), height_of(upright, "B"), "drawn: eyes above")
        self.assertGreater(height_of(hanging, "R"), height_of(hanging, "B"), "hanging: mouth above")

    def test_on_end_it_is_taller_than_it_is_wide(self) -> None:
        from personalized_agent.agent.character.watermelon import Watermelon
        rig = Watermelon()
        rig.leaves = rig.leaves.copy()
        rig.leaves.putalpha(0)
        left, top, right, bottom = rig.frame(size=256).getchannel("A").getbbox()
        self.assertGreater(right - left, bottom - top, "lying, as it is drawn")
        left, top, right, bottom = rig.frame(size=256, turn=1.0).getchannel("A").getbbox()
        self.assertGreater(bottom - top, right - left)

    def test_its_eyes_one_above_the_other(self) -> None:
        from PIL import ImageDraw

        from personalized_agent.agent.character import watermelon
        rig = watermelon.Watermelon()
        eye = (255, 0, 255, 255)

        def two_eyes(body, scale, *_rest):
            pen = ImageDraw.Draw(body)
            for x in (40, 80):
                pen.rectangle((x * scale, 60 * scale, (x + 6) * scale, (60 + 6) * scale), fill=eye)

        with mock.patch.object(watermelon, "draw_face", two_eyes), \
                mock.patch.object(watermelon, "_extras", lambda *_a: None):
            leaves, rig.leaves = rig.leaves, rig.leaves.copy()
            rig.leaves.putalpha(0)
            rig.body = rig.body.copy()
            rig.body.putalpha(0)
            upright = rig.frame(size=256, turn=0.0)
            turned = rig.frame(size=256, turn=1.0)
            rig.leaves = leaves
        left, top, right, bottom = upright.getbbox()
        self.assertGreater(right - left, bottom - top)
        left, top, right, bottom = turned.getbbox()
        self.assertGreater(bottom - top, right - left)


class TheFruitPictureTests(unittest.TestCase):

    def test_a_fruit_at_the_end_of_its_latest_work_and_juice_along_the_row(self) -> None:
        from test_panels import _javascript_engine
        kind, engine = _javascript_engine()
        if kind != "chrome":
            self.skipTest("no browser available to render the page")
        script = (ROOT / "personalized_agent" / "api" / "web" / "app.js").read_text(encoding="utf-8")
        part = script[script.index("function pjHours"):script.index("/* The open project's card")]
        part = part[:part.index("function pjLeastBar")] + part[part.index("function pjWhen"):]
        page = """<!doctype html><html><head><meta charset='utf-8'></head><body><div id="host"></div><script>
const escapeHtml=(s)=>String(s);const pjBusy=()=>false;</script><script>%s</script><script>
window.addEventListener('load',()=>{const day=86400000,now=Date.now();
const task=(from,to)=>({title:'T',minutes:60,starts_at:new Date(now-from*day).toISOString(),
ends_at:new Date(now-to*day).toISOString()});
const one={id:'proj_a',title:'A',minutes:90,kind:'work',tasks:[task(6,5),task(3,2)]};
const none={id:'proj_b',title:'B',minutes:60,kind:'work',tasks:[]};
document.querySelector('#host').innerHTML=pjChart([one,none],now-7*day,'');
const row=document.querySelector('[data-pj-open="proj_a"]');
const fruit=row.querySelector('.pj-fruit');const strokes=[...row.querySelectorAll('.pj-dab')];
document.title=JSON.stringify({rows:document.querySelectorAll('[data-pj-open]').length,
fruits:document.querySelectorAll('.pj-fruit').length,left:parseFloat(fruit.style.left),
ends:strokes.map((r)=>parseFloat(r.getAttribute('x'))+parseFloat(r.getAttribute('width'))),
strokes:strokes.length,filter:row.querySelector('.pj-smear g').getAttribute('filter'),
defined:!!document.querySelector('filter#pj-juice'),same:pjFruit('proj_a')===pjFruit('proj_a'),
colour:pjColour('proj_a')===pjFruit('proj_a').juice,kinds:PJ_FRUITS.length,
name:row.querySelector('.pj-cname').textContent.includes('A'),
art:new Set(PJ_FRUITS.map((f)=>f.art)).size,
...(()=>{const many=(n)=>[...Array(n)].map((_,i)=>({id:'same'+(i%%3)+'p'+i,first_seen:'2026-01-01'}));
const shown=(n)=>{const all=many(n);pjShareFruits(all);return new Set(all.map((o)=>pjFruit(o.id))).size;};
const fits=shown(PJ_FRUITS.length),over=shown(PJ_FRUITS.length+5);
const ids=[{id:'x',first_seen:'1'},{id:'y',first_seen:'2'}];pjShareFruits(ids);const a=pjFruit('y');
pjShareFruits(ids.slice().reverse());
const fresh=(n)=>{pjFruitsShown.clear();const all=[...Array(n)].map((_,i)=>({id:'f'+i}));
pjShareFruits(all);return all.map((o)=>pjFruit(o.id));};
const steady=a===pjFruit('y');
const m0=new Map();const m1=pjFoldUnder(m0,'b','a');const m2=pjFoldUnder(m1,'c','b');
const m3=pjFoldUnder(m2,'a','d');const m4=pjFoldUnder(m1,'a','b');
const shownHeld=(()=>{window.stateBeat={folded:m1};const two=[{id:'a',title:'A',minutes:9,kind:'work',tasks:[]},
{id:'b',title:'B',minutes:90,kind:'work',tasks:[]},{id:'e',title:'E',minutes:5,kind:'work',tasks:[]}];
document.querySelector('#host').innerHTML=pjChart(two,now-7*day,'');
const rows=[...document.querySelectorAll('[data-pj-open]')];delete window.stateBeat;
return {order:rows.map((r)=>r.dataset.pjOpen),under:rows.filter((r)=>r.classList.contains('pj-under')).map((r)=>r.dataset.pjOpen)};})();
const folds={empty:m0.size,one:[...m1],chain:[...m2],moved:[...m3],self:[...m4],groups:pjMergeGroups(m2),shownHeld};
const colours=(n)=>fresh(n).map(pjColourOf);const runs=[...Array(50)].map(()=>colours(6));
return {fits,over,steady,folds,apart:new Set(fresh(20)).size,
kindsOfColour:new Set(PJ_FRUITS.map(pjColourOf)).size,
fewestColours:Math.min(...runs.map((one)=>new Set(one).size)),
sideBySide:runs.concat([...Array(20)].map(()=>colours(20))).some((one)=>one.some((c,i)=>i&&c===one[i-1])),
chance:new Set([...Array(600)].map(()=>fresh(1)[0])).size};})()});});</script></body></html>""" % part
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "fruit.html"
            path.write_text(page, encoding="utf-8")
            done = subprocess.run(
                [engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=3000",
                 "--dump-dom", path.as_uri()],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        said = json.loads(re.search(r"<title>(.*?)</title>", done.stdout, re.S).group(1)
                          .replace("&quot;", '"').replace("&amp;", "&")
                          .replace("&lt;", "<").replace("&gt;", ">"))
        self.assertEqual((said["rows"], said["fruits"]), (2, 1), "no work shown, no fruit")
        self.assertEqual(said["strokes"], 2, "a stroke for each task")
        self.assertAlmostEqual(said["left"], max(said["ends"]), delta=0.05)
        self.assertEqual(said["filter"], "url(#pj-juice)")
        self.assertTrue(said["defined"] and said["same"] and said["colour"] and said["name"])
        self.assertGreaterEqual(said["kinds"], 100)
        self.assertEqual(said["art"], said["kinds"], "each drawn apart")
        self.assertEqual(said["fits"], said["kinds"], "none twice while there are enough")
        self.assertEqual(said["over"], said["kinds"], "more projects than fruits: only then twice")
        self.assertTrue(said["steady"], "a project keeps its fruit from one drawing to the next")
        self.assertEqual(said["apart"], 20)
        folds = said["folds"]
        self.assertEqual(folds["empty"], 0, "a drop leaves what it was given as it was")
        self.assertEqual(folds["one"], [["b", "a"]])
        self.assertEqual(sorted(folds["chain"]), [["b", "a"], ["c", "a"]], "under the top one")
        self.assertEqual(sorted(folds["moved"]), [["a", "d"], ["b", "d"], ["c", "d"]],
                         "what was under it goes with it")
        self.assertEqual(folds["self"], [["b", "a"]], "not on what is under it")
        self.assertEqual(folds["groups"], [{"into": "a", "ids": ["b", "c"]}])
        self.assertEqual(folds["shownHeld"]["order"], ["a", "b", "e"], "right under the one it was put on")
        self.assertEqual(folds["shownHeld"]["under"], ["b"])
        self.assertGreaterEqual(said["kindsOfColour"], 6, "colours to choose from")
        self.assertGreaterEqual(said["fewestColours"], 6, "six projects, six colours")
        self.assertFalse(said["sideBySide"], "no row the colour of the row above")
        self.assertGreater(said["chance"], said["kinds"] // 2,
                           "the same project, drawn anew: any fruit, not one from its id")


class JumpingOutTests(unittest.TestCase):

    def test_it_draws_back_flies_past_and_lands_where_it_was_going(self) -> None:
        from personalized_agent.ui.avatar_tuck import LEAP_GATHER, LEAP_PAST, leap
        self.assertEqual(leap(0.0).out, 0.0)
        gathered = leap(LEAP_GATHER - 1e-6)
        self.assertLess(gathered.out, 0)
        self.assertLess(gathered.squash, 0.85, "flat against the side")
        steps = [leap(n / 100) for n in range(101)]
        self.assertGreater(max(one.lift for one in steps), 0.99)
        self.assertAlmostEqual(max(one.out for one in steps), 1 + LEAP_PAST, places=2)
        self.assertAlmostEqual(leap(1.0).out, 1.0)
        self.assertEqual(leap(1.0).lift, 0.0)
        self.assertAlmostEqual(leap(1.0).squash, 1.0)

    def test_it_has_no_jump_in_it(self) -> None:
        from personalized_agent.ui.avatar_tuck import leap
        steps = [leap(n / 200) for n in range(201)]
        for one, other in zip(steps, steps[1:]):
            self.assertLess(abs(one.out - other.out), 0.05)
            self.assertLess(abs(one.squash - other.squash), 0.15)


if __name__ == "__main__":
    unittest.main()
