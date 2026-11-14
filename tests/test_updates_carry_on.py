"""A download that breaks off carries on where it stopped (the user,
2026-10-30, a slow connection to GitHub): asked again from the byte it got
to, also after the agent is opened again; the page says it is downloading,
how far, how fast and how long is left; and a download under way is
finished first - a newer version is had the time after."""

from __future__ import annotations

import hashlib
import http.server
import json
import re
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import support  # noqa: F401

from personalized_agent.platform_layer import updater as up

ROOT = Path(__file__).resolve().parents[1]
NAME = "PersonalAgentWaterMelon-portable-windows-x64.zip"


class _Config(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class _Server:
    """A release on this computer: its checksums and its file, which breaks
    off after `cut` bytes of each answer - with the whole length said, as a
    connection that drops does. Asked with a Range, it answers 206."""

    def __init__(self, payload: bytes, cut=None) -> None:
        self.payload, self.cut, self.asked = payload, cut, []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path.endswith(up.SUMS):
                    body = f"{hashlib.sha256(outer.payload).hexdigest()}  {NAME}\n".encode()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                said = self.headers.get("Range") or ""
                outer.asked.append(said)
                start = int(re.match(r"bytes=(\d+)-", said).group(1)) if said else 0
                rest = outer.payload[start:]
                self.send_response(206 if said else 200)
                self.send_header("Content-Length", str(len(rest)))
                self.end_headers()
                cut = outer.cut(len(outer.asked)) if callable(outer.cut) else outer.cut
                self.wfile.write(rest if cut is None else rest[:cut])
                self.wfile.flush()
                self.close_connection = True

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def release(self, tag="v9.0.0"):
        return {"tag": tag, "page": f"https://example.org/{tag}", "published": "", "notes": "",
                "prerelease": False, "draft": False,
                "files": {NAME: {"url": f"{self.base}/{NAME}", "size": len(self.payload)},
                          up.SUMS: {"url": f"{self.base}/{up.SUMS}", "size": 1}}}

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class CarriedOnWhereItStoppedTests(unittest.TestCase):

    def setUp(self) -> None:
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.payload = bytes(range(256)) * 12000          # about 3 MB
        pauses = mock.patch.object(up, "RETRY_PAUSES", (0, 0, 0))
        pauses.start()
        self.addCleanup(pauses.stop)

    def _serve(self, cut):
        server = _Server(self.payload, cut)
        self.addCleanup(server.close)
        return server

    def test_broken_off_it_is_asked_again_from_where_it_got(self) -> None:
        server = self._serve(1 << 20)                       # a megabyte each time
        updater = up.Updater(_Config(), Path(self.work.name))
        target = updater.download(server.release(), "windows", name=NAME, shown=False)
        self.assertEqual(target.read_bytes(), self.payload, "whole, and checked")
        self.assertEqual(server.asked[0], "", "the first time from the start")
        self.assertEqual(server.asked[1:], [f"bytes={n << 20}-" for n in (1, 2)])
        self.assertIsNone(updater.status()["download"], "said no more once done")
        self.assertFalse((target.parent / up.UNDER_WAY).exists())

    def test_here_whole_already_it_is_not_fetched_again(self) -> None:
        server = self._serve(1 << 30)
        updater = up.Updater(_Config(), Path(self.work.name))
        target = updater.download(server.release(), "windows", name=NAME, shown=False)
        asked = len(server.asked)
        again = up.Updater(_Config(), Path(self.work.name))         # as after a restart
        self.assertEqual(again.download(server.release(), "windows", name=NAME, shown=False), target)
        self.assertEqual(len(server.asked), asked, "nothing came down")
        self.assertEqual(target.read_bytes(), self.payload)
        self.assertFalse((target.parent / up.UNDER_WAY).exists())
        self.assertIsNone(again.status().get("download"))

    def test_here_but_not_what_it_should_be_it_is_fetched_again(self) -> None:
        server = self._serve(1 << 30)
        updater = up.Updater(_Config(), Path(self.work.name))
        target = updater.download(server.release(), "windows", name=NAME, shown=False)
        target.write_bytes(b"something else")
        asked = len(server.asked)
        updater.download(server.release(), "windows", name=NAME, shown=False)
        self.assertGreater(len(server.asked), asked)
        self.assertEqual(target.read_bytes(), self.payload, "whole, and checked")

    def _made_ready(self, tag):
        """make_ready with what is around it made up: is what came down still there after?"""
        from personalized_agent.platform_layer import quiet_update as quiet
        folder = Path(self.work.name)
        updater = up.Updater(_Config(), folder)
        came = updater.folder / tag / "came.zip"
        came.parent.mkdir(parents=True)
        came.write_bytes(b"zip")

        def unpacked(_archive, _live, target, **_kept):
            target.mkdir(parents=True)
            (target / "program.exe").write_bytes(b"exe")
            return {"linked": 0, "written": 1}
        with mock.patch.object(updater, "download", return_value=came), \
                mock.patch.object(quiet, "live_root", return_value=folder / "live"), \
                mock.patch.object(quiet, "place", return_value=folder / ".update"), \
                mock.patch.object(quiet, "unpack_beside", side_effect=unpacked):
            updater.make_ready({"tag": tag, "notes": "", "page": ""}, "windows")
        return came.exists()

    def test_the_older_one_gone_back_to_stays_for_the_next_time(self) -> None:
        self.assertTrue(self._made_ready("v0.0.1"))

    def test_a_newer_one_goes_once_it_is_ready(self) -> None:
        self.assertFalse(self._made_ready("v9.9.9"))

    def test_stopped_it_is_kept_and_carried_on_after_the_agent_is_opened_again(self) -> None:
        # Half comes down, then nothing more: it stops, keeping what came.
        half = len(self.payload) // 2
        server = self._serve(lambda n: half if n == 1 else 0)
        updater = up.Updater(_Config(), Path(self.work.name))
        with self.assertRaises(up.DownloadStopped):
            updater.download(server.release(), "windows", name=NAME, shown=False)
        said = updater.status()["download"]
        self.assertTrue(said["stopped"])
        self.assertEqual(said["done"], half)
        here = updater.folder / "v9.0.0"
        self.assertEqual((here / (NAME + ".part")).stat().st_size, half, "kept")
        self.assertTrue((here / up.UNDER_WAY).exists())
        # Opened again: the page says how far it got before anything is asked.
        again = up.Updater(_Config(), Path(self.work.name))
        again._said_under_way()
        self.assertEqual(again.status()["download"]["done"], half)
        self.assertTrue(again.status()["download"]["stopped"])
        # And the connection back, it carries on from there - not from the start.
        server.cut = None
        server.asked.clear()
        target = again.download(server.release(), "windows", name=NAME, shown=False)
        self.assertEqual(target.read_bytes(), self.payload)
        self.assertEqual(server.asked, [f"bytes={half}-"])

    def test_a_server_that_sends_the_whole_again_is_taken_from_the_start(self) -> None:
        server = self._serve(None)
        updater = up.Updater(_Config(), Path(self.work.name))
        here = updater.folder / "v9.0.0"
        here.mkdir(parents=True)
        (here / (NAME + ".part")).write_bytes(b"x" * 1000)    # not this file's start
        real = up._get

        def no_ranges(url, timeout=30.0, start=0):
            return real(url, timeout=timeout)                 # Range never sent: 200
        with mock.patch.object(up, "_get", side_effect=no_ranges):
            target = updater.download(server.release(), "windows", name=NAME, shown=False)
        self.assertEqual(target.read_bytes(), self.payload)

    def test_a_file_that_is_not_there_is_not_asked_for_again_and_again(self) -> None:
        server = self._serve(None)
        updater = up.Updater(_Config(), Path(self.work.name))
        release = server.release()
        release["files"][NAME]["url"] = f"{server.base}/nothing-here"

        def missing(url, timeout=30.0, start=0):
            if url.endswith("nothing-here"):
                raise up.urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            return up.urllib.request.urlopen(url, timeout=timeout)
        with mock.patch.object(up, "_get", side_effect=missing):
            with self.assertRaises(RuntimeError) as said:
                updater.download(release, "windows", name=NAME, shown=False)
        self.assertNotIsInstance(said.exception, up.DownloadStopped)
        self.assertIsNone(updater._under_way(), "not carried on")

    def test_how_fast_and_how_long_is_left_are_said(self) -> None:
        server = self._serve(None)
        updater = up.Updater(_Config(), Path(self.work.name))
        seen = []
        original = updater._set

        def watch(**values):
            original(**values)
            if values.get("download"):
                seen.append(dict(values["download"]))
        # Two seconds go by at each look at the clock - this module's clock only.
        clock = iter(range(0, 10 ** 6, 2))
        ticking = SimpleNamespace(monotonic=lambda: float(next(clock)), strftime=time.strftime)
        with mock.patch.object(updater, "_set", side_effect=watch), \
                mock.patch.object(up, "PROGRESS_EVERY", 0.0), \
                mock.patch.object(up, "time", ticking):
            updater.download(server.release(), "windows", name=NAME, shown=False)
        part_way = [one for one in seen if 0 < one["done"] < one["total"]]
        self.assertTrue(part_way, "a share part way is said")
        self.assertTrue(all(one["tag"] == "v9.0.0" for one in seen))
        timed = [one for one in part_way if one["per_second"]]
        self.assertTrue(timed, "and how fast")
        self.assertTrue(all(one["left_seconds"] > 0 for one in timed), "and how long is left")


def _release(tag):
    return {"tag": tag, "page": f"https://example.org/{tag}", "published": "", "notes": "",
            "prerelease": False, "draft": False,
            "files": {NAME: {"url": "u", "size": 1}, up.SUMS: {"url": "s", "size": 1},
                      up.ASSET["windows"]: {"url": "i", "size": 1}}}


class TheOneUnderWayFirstTests(unittest.TestCase):
    """A download under way is finished and taken up first; a version newer
    still is had the time after (the user, 2026-10-30)."""

    def _look(self, mode, under_way=None, ready=None, asked=None):
        made = []
        with tempfile.TemporaryDirectory() as work:
            updater = up.Updater(_Config(updates=mode), Path(work))
            updater.quit = lambda: None
            if asked:
                updater._set(asked_to_install=asked)
            if under_way:
                here = updater.folder / under_way
                here.mkdir(parents=True)
                (here / up.UNDER_WAY).write_text(json.dumps({"tag": under_way, "name": NAME}))
            from personalized_agent.platform_layer import quiet_update as quiet
            with mock.patch.object(up, "published_releases",
                                   return_value=[_release("v9.1.0"), _release("v9.0.0")]), \
                    mock.patch.object(up, "how_installed", return_value="windows"), \
                    mock.patch.object(quiet, "can_make_ready", return_value=True), \
                    mock.patch.object(updater, "_ready", return_value=ready), \
                    mock.patch.object(updater, "make_ready",
                                      side_effect=lambda release, kind: made.append(release["tag"])):
                updater.once()
        return made

    def test_the_one_under_way_before_a_newer_one(self) -> None:
        self.assertEqual(self._look("install", under_way="v9.0.0"), ["v9.0.0"])

    def test_carried_on_when_asked_first_too(self) -> None:
        # Asked to install, then the agent opened again: no one asks twice.
        self.assertEqual(self._look("ask", under_way="v9.0.0"), ["v9.0.0"])

    def test_with_none_under_way_the_newest(self) -> None:
        self.assertEqual(self._look("install"), ["v9.1.0"])

    def test_one_made_ready_is_taken_up_before_a_newer_is_downloaded(self) -> None:
        self.assertEqual(self._look("install", ready={"tag": "v9.0.0"}), [])

    def test_a_version_the_user_chooses_is_theirs(self) -> None:
        self.assertEqual(self._look("ask", under_way="v9.0.0", asked="v9.1.0"), ["v9.1.0"])


class SaidAsDownloadingTests(unittest.TestCase):

    def test_check_now_while_downloading_does_not_say_looking(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            updater = up.Updater(_Config(), Path(work))
            updater._set(download={"tag": "v9.0.0", "done": 5, "total": 10, "stopped": False})
            updater._thread = mock.Mock(is_alive=lambda: True)   # the loop, busy downloading
            updater.check_now()
            self.assertNotEqual(updater.status()["doing"], "checking")

    def test_the_page_says_downloading_how_far_how_fast_and_how_long(self) -> None:
        from test_panels import _javascript_engine
        kind, engine = _javascript_engine()
        if kind != "chrome":
            self.skipTest("no browser available to render the page")
        script = (ROOT / "personalized_agent" / "api" / "web" / "app.js").read_text(encoding="utf-8")
        start = script.index("/* A DOWNLOAD UNDER WAY")
        end = script.index("/* MACOS PERMISSIONS (Settings > Privacy)")
        page = """<!doctype html><html><head><meta charset='utf-8'></head><body>
<p id="updates-status"></p><div id="updates-versions"></div>
<script>const $=(s)=>document.querySelector(s);const clock=(t)=>t.slice(0,10);
const toast=()=>{};const api={post:async()=>({}),get:async()=>({})};</script>
<script>%s</script>
<script>window.addEventListener('load',()=>{let err='';const out={};
const MB=1048576;const text=()=>$('#updates-status').textContent;
try{
renderUpdates({current:'0.2.11',mode:'ask',doing:'checking',versions:[],
  download:{tag:'v0.2.16',done:45*MB,total:313*MB,per_second:0.2*MB,left_seconds:1340,
            carried_on_from:30*MB,waiting:0,stopped:false}});
out.running=text();out.spans=$('#updates-status').children.length;
renderUpdates({current:'0.2.11',mode:'ask',versions:[],
  download:{tag:'v0.2.16',done:45*MB,total:313*MB,per_second:0.2*MB,left_seconds:5000,
            carried_on_from:0,waiting:0,stopped:false}});
out.hours=text();
renderUpdates({current:'0.2.11',mode:'ask',versions:[],
  download:{tag:'v0.2.16',done:45*MB,total:313*MB,per_second:null,left_seconds:null,
            carried_on_from:0,waiting:15,stopped:false}});
out.waiting=text();
renderUpdates({current:'0.2.11',mode:'ask',versions:[],newer:true,latest:'v0.2.16',
  download:{tag:'v0.2.16',done:45*MB,total:0,stopped:true}});
out.stopped=text();
}catch(e){err=String(e&&e.stack?e.stack:e);}
document.title=JSON.stringify({err,out});});</script></body></html>""" % script[start:end]
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "download.html"
            path.write_text(page, encoding="utf-8")
            done = subprocess.run(
                [engine, "--headless=new", "--disable-extensions", "--disable-gpu", "--no-first-run",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=3000",
                 "--dump-dom", path.as_uri()],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        said = json.loads(re.search(r"<title>(.*?)</title>", done.stdout, re.S).group(1)
                          .replace("&quot;", '"').replace("&amp;", "&")
                          .replace("&lt;", "<").replace("&gt;", ">"))
        self.assertEqual(said["err"], "")
        out = said["out"]
        self.assertIn("Downloading v0.2.16: 45.0 of 313.0 MB (14%).", out["running"])
        self.assertIn("0.20 MB/s, about 22 min left.", out["running"])
        self.assertIn("Carried on from 30.0 MB, where it had got to.", out["running"])
        self.assertNotIn("Looking for newer versions", out["running"], "downloading, not looking")
        self.assertGreater(out["spans"], 2, "a sentence each, each put in the language")
        self.assertIn("about 1 h 23 min left.", out["hours"])
        self.assertIn("The connection broke off: it carries on in 15 s.", out["waiting"])
        self.assertIn("The download of v0.2.16 stopped at 45.0 MB: it carries on from there "
                      "by itself.", out["stopped"])
        self.assertIn("Version v0.2.16 is available.", out["stopped"])



class EachStepSaidWithItsVersionTests(unittest.TestCase):
    """Making ready with how many of its files, installing, waiting for
    work, starting - each with its version (the user, 2026-10-31)."""

    def test_unpacking_says_how_many_files_are_done(self) -> None:
        import zipfile
        from personalized_agent.platform_layer import quiet_update as quiet
        with tempfile.TemporaryDirectory() as work:
            work = Path(work)
            archive = work / "portable.zip"
            with zipfile.ZipFile(archive, "w") as packed:
                for n in range(5):
                    packed.writestr(f"_internal/part{n}.dll", b"new" * n)
            told = []
            quiet.unpack_beside(archive, work / "program", work / "ready",
                                told=lambda done, total: told.append((done, total)))
        self.assertEqual(told[0], (0, 5))
        self.assertEqual(told[-1], (5, 5), "and when it is done")
        self.assertEqual([done for done, _ in told], sorted(done for done, _ in told))

    def test_making_ready_says_its_version_and_how_far(self) -> None:
        import zipfile
        from personalized_agent.platform_layer import quiet_update as quiet
        with tempfile.TemporaryDirectory() as work:
            work = Path(work)
            archive = work / "portable.zip"
            with zipfile.ZipFile(archive, "w") as packed:
                for n in range(3):
                    packed.writestr(f"part{n}.dll", b"new")
            (work / "program").mkdir()
            updater = up.Updater(_Config(), work / "data")
            seen = []
            original = updater._set

            def watch(**values):
                original(**values)
                seen.append(updater.state.get("now"))
            with mock.patch.object(updater, "_set", side_effect=watch), \
                    mock.patch.object(updater, "download", return_value=archive), \
                    mock.patch.object(up, "PROGRESS_EVERY", 0.0), \
                    mock.patch.object(quiet, "live_root", return_value=work / "program"), \
                    mock.patch.object(quiet, "place", return_value=work / "beside"):
                updater.make_ready(_release("v9.0.0"), "windows")
            steps = [one for one in seen if one]
            self.assertTrue(all(one["what"] == "making_ready" and one["tag"] == "v9.0.0"
                                for one in steps))
            self.assertIn({"what": "making_ready", "tag": "v9.0.0", "done": 3, "total": 3}, steps)
            self.assertIsNone(updater.status()["now"], "nothing under way once ready")
            self.assertEqual(updater.status()["ready"], "v9.0.0")

    def test_waiting_for_work_says_the_version(self) -> None:
        from personalized_agent.platform_layer import quiet_update as quiet
        with tempfile.TemporaryDirectory() as work:
            updater = up.Updater(_Config(updates="install"), Path(work), working=lambda: True)
            updater.quit = lambda: None
            with mock.patch.object(up, "published_releases", return_value=[_release("v9.0.0")]), \
                    mock.patch.object(up, "how_installed", return_value="windows"), \
                    mock.patch.object(quiet, "can_make_ready", return_value=False):
                updater.once()
            self.assertEqual(updater.status()["now"], {"what": "waiting_for_work", "tag": "v9.0.0"})

    def test_nothing_under_way_nothing_said(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            updater = up.Updater(_Config(), Path(work))
            updater._set(doing="installing v9.0.0", now={"what": "installing", "tag": "v9.0.0"})
            updater._set(doing="")
            self.assertIsNone(updater.status()["now"])

    def test_the_page_says_each_step_with_its_version(self) -> None:
        from test_panels import _javascript_engine
        kind, engine = _javascript_engine()
        if kind != "chrome":
            self.skipTest("no browser available to render the page")
        script = (ROOT / "personalized_agent" / "api" / "web" / "app.js").read_text(encoding="utf-8")
        start = script.index("/* A DOWNLOAD UNDER WAY")
        end = script.index("/* MACOS PERMISSIONS (Settings > Privacy)")
        page = """<!doctype html><html><head><meta charset='utf-8'></head><body>
<p id="updates-status"></p><div id="updates-versions"></div>
<script>const $=(s)=>document.querySelector(s);const clock=(t)=>t.slice(0,10);
const toast=()=>{};let asked=0;
const api={post:async()=>({}),get:async()=>{asked+=1;return {current:'0.2.11',mode:'ask',versions:[]};}};</script>
<script>%s</script>
<script>window.addEventListener('load',()=>{let err='';const out={};
const text=()=>$('#updates-status').textContent;
const at=(now,doing)=>{renderUpdates({current:'0.2.11',mode:'ask',versions:[],doing,now});return text();};
try{
out.ready=at({what:'making_ready',tag:'v0.2.16',done:1234,total:5678},'making v0.2.16 ready');
out.readyBare=at({what:'making_ready',tag:'v0.2.16'},'making v0.2.16 ready');
out.installing=at({what:'installing',tag:'v0.2.16'},'installing v0.2.16');
out.waiting=at({what:'waiting_for_work',tag:'v0.2.16'},'waiting for work to finish');
out.starting=at({what:'starting',tag:'v0.2.16'},'starting v0.2.16');
}catch(e){err=String(e&&e.stack?e.stack:e);}
setTimeout(()=>{out.asked=asked;document.title=JSON.stringify({err,out});},2500);});</script>
</body></html>""" % script[start:end]
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "steps.html"
            path.write_text(page, encoding="utf-8")
            done = subprocess.run(
                [engine, "--headless=new", "--disable-extensions", "--disable-gpu", "--no-first-run",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=5000",
                 "--dump-dom", path.as_uri()],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        said = json.loads(re.search(r"<title>(.*?)</title>", done.stdout, re.S).group(1)
                          .replace("&quot;", '"').replace("&amp;", "&")
                          .replace("&lt;", "<").replace("&gt;", ">"))
        self.assertEqual(said["err"], "")
        out = said["out"]
        self.assertIn("Making v0.2.16 ready: 1234 of 5678 files (21%).", out["ready"])
        self.assertIn("Making v0.2.16 ready…", out["readyBare"])
        self.assertIn("Installing v0.2.16…", out["installing"])
        self.assertIn("v0.2.16 is installed once the work running now has finished.", out["waiting"])
        self.assertIn("Starting v0.2.16…", out["starting"])
        self.assertTrue(all("Now:" not in one for one in out.values() if isinstance(one, str)))
        self.assertGreaterEqual(out["asked"], 1, "asked again while a step is under way")

if __name__ == "__main__":
    unittest.main()
