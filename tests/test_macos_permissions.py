"""The macOS permissions on one page: each looked at without asking, Full
Disk Access first, and a button that asks where macOS lets a program ask
and opens the very pane of System Settings."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401

from personalized_agent.platform_layer import macos_permissions as mp
from test_panels import _javascript_engine

WEB = Path(__file__).resolve().parents[1] / "personalized_agent" / "api" / "web"


class _Config(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class LookedAtWithoutAskingTests(unittest.TestCase):

    def test_nothing_off_a_mac(self) -> None:
        with mock.patch.object(mp.sys, "platform", "win32"):
            self.assertEqual(mp.status(_Config()), {"applies": False, "items": []})
            self.assertFalse(mp.open_pane("full_disk"))

    def test_full_disk_access_first_and_what_is_missing(self) -> None:
        checks = {"full_disk": lambda: False, "accessibility": lambda: True,
                  "automation": lambda: None, "screen": lambda: False}
        with mock.patch.object(mp.sys, "platform", "darwin"), \
                mock.patch.dict(mp._CHECKS, checks):
            said = mp.status(_Config(listener_screenshots_enabled=False))
        self.assertEqual([one["key"] for one in said["items"]], list(mp.ORDER))
        self.assertEqual(said["items"][0]["key"], "full_disk")
        # Screen recording is not needed with screenshots off, so not missing.
        self.assertEqual(said["missing"], ["full_disk"])
        self.assertFalse(said["items"][3]["needed"])

    def test_only_what_is_needed_is_looked_at(self) -> None:
        looked = []
        checks = {key: (lambda key=key: looked.append(key) or True) for key in mp.ORDER}
        with mock.patch.object(mp.sys, "platform", "darwin"), \
                mock.patch.dict(mp._CHECKS, checks):
            said = mp.status(_Config(listener_screenshots_enabled=False))
        self.assertNotIn("screen", looked)
        self.assertIsNone(said["items"][3]["granted"])

    def test_system_events_is_waited_for_a_few_seconds_only(self) -> None:
        with mock.patch.object(mp.children, "run_short",
                               side_effect=TimeoutError("waited")) as ran:
            self.assertIsNone(mp._automation())
        self.assertEqual(ran.call_args.kwargs["timeout"], mp.AUTOMATION_WAIT)
        self.assertLessEqual(mp.AUTOMATION_WAIT, 3)

    def test_check_again_is_asked_once_at_a_time(self) -> None:
        from pathlib import Path
        script = (Path(__file__).resolve().parents[1] / "personalized_agent" / "api" / "web"
                  / "app.js").read_text(encoding="utf-8")
        start = script.index('$("#mac-permissions-check")')
        handler = script[start:start + 900]
        self.assertIn("button.disabled = true", handler)
        self.assertIn("Checking…", handler)
        self.assertIn("Checked at ${at}", handler)
        # Screen Recording applies after the agent is opened again.
        self.assertIn("/api/permissions/macos/start_again", script)

    def test_the_button_opens_the_very_pane(self) -> None:
        done = mock.Mock(returncode=0)
        with mock.patch.object(mp.sys, "platform", "darwin"), \
                mock.patch.object(mp, "_put_on_the_list") as asked, \
                mock.patch.object(mp.children, "run_short", return_value=done) as ran:
            self.assertTrue(mp.open_pane("screen"))
            self.assertFalse(mp.open_pane("camera"))
        asked.assert_called_once_with("screen")
        self.assertEqual(ran.call_args[0][0],
                         ["open", "x-apple.systempreferences:com.apple.preference."
                                  "security?Privacy_ScreenCapture"])

    def test_the_route_refuses_what_is_not_one(self) -> None:
        from support import TemporaryAgent
        from personalized_agent.api.errors import ApiError
        from personalized_agent.api.routes import build_routes
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        routes = build_routes(agent.app)

        class _Req:
            body = {"key": "camera"}

            def require(self, key):
                return self.body[key]
        with self.assertRaises(ApiError):
            routes[("POST", "/api/permissions/macos/open")](_Req())
        self.assertFalse(routes[("GET", "/api/permissions/macos")](_Req())["applies"])


PAGE = """<!doctype html><html><body>
<div id="mac-permissions-card" hidden><div id="mac-permissions-list"></div></div>
<div id="macperm-banner" hidden><span id="macperm-text"></span></div>
<script>window.__posts=[];
const $=(s)=>document.querySelector(s);const escapeHtml=(s)=>String(s);const toast=()=>{};
const api={get:async()=>({applies:false}),post:async(p,b)=>{window.__posts.push(p+':'+b.key);return window.__data;}};
window.__data={applies:true,missing:['full_disk','accessibility'],items:[
 {key:'full_disk',needed:true,granted:false},{key:'accessibility',needed:true,granted:false},
 {key:'automation',needed:true,granted:true},{key:'screen',needed:false,granted:null}]};
</script>
<script>%s</script>
<script>window.addEventListener('load',async()=>{let err='';const out={};
try{renderMacPermissions(window.__data);
out.card=!$('#mac-permissions-card').hidden;
out.rows=[...document.querySelectorAll('.mac-permission')].map(r=>r.dataset.permission);
out.buttons=[...document.querySelectorAll('.mac-permission button')].length;
out.banner=!$('#macperm-banner').hidden;out.said=$('#macperm-text').textContent;
document.querySelector('.mac-permission button').click();await new Promise(r=>setTimeout(r,100));
out.posted=window.__posts;
renderMacPermissions({applies:false});out.offAMac=$('#mac-permissions-card').hidden&&$('#macperm-banner').hidden;
}catch(e){err=String(e&&e.stack?e.stack:e);}
document.title=JSON.stringify({err,out});});</script></body></html>"""


class TheCardTests(unittest.TestCase):

    def _block(self) -> str:
        script = (WEB / "app.js").read_text(encoding="utf-8")
        start = script.index("const MAC_PERMISSIONS = {")
        end = script.index("$(\"#mac-permissions-check\")")
        return script[start:end]

    def test_listed_with_the_way_to_each_and_said_on_the_overview(self) -> None:
        kind, engine = _javascript_engine()
        if kind != "chrome":
            self.skipTest("no browser available to render the page")
        with tempfile.TemporaryDirectory() as work:
            page = Path(work) / "perm.html"
            page.write_text(PAGE % self._block(), encoding="utf-8")
            done = subprocess.run(
                [engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=5000",
                 "--dump-dom", page.as_uri()],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        said = json.loads(re.search(r"<title>(.*?)</title>", done.stdout, re.S).group(1)
                          .replace("&quot;", '"').replace("&amp;", "&"))
        self.assertEqual(said["err"], "")
        out = said["out"]
        self.assertTrue(out["card"])
        self.assertEqual(out["rows"], list(mp.ORDER))
        self.assertEqual(out["buttons"], 2, "a button only where one is needed and missing")
        self.assertTrue(out["banner"])
        self.assertIn("2 things", out["said"])
        self.assertEqual(out["posted"], ["/api/permissions/macos/open:full_disk"])
        self.assertTrue(out["offAMac"])

    def test_every_one_is_said_on_the_page(self) -> None:
        block = self._block()
        for key in mp.ORDER:
            self.assertIn(f"\n  {key}: {{ name:", block)
        page = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertLess(page.index('id="mac-permissions-card"'), page.index('id="privacy-card"'))


if __name__ == "__main__":
    unittest.main()
