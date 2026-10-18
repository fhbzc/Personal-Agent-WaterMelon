"""The Record log: the latest things recorded, each with the Privacy switch
that recorded it, and what the filters kept out - by which rule, never what
was in it."""

from __future__ import annotations

import unittest
from pathlib import Path

from support import TemporaryAgent

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "personalized_agent" / "api" / "web"


class _Req:
    def __init__(self, body=None):
        self.body = body or {}

    def get(self, key, default=None):
        return self.body.get(key, default)


class RecordLogTests(unittest.TestCase):

    def setUp(self) -> None:
        from personalized_agent.listener.core import filtered
        filtered.reset()
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _routes(self):
        from personalized_agent.api.routes import build_routes
        return build_routes(self.app)

    def test_each_record_says_which_switch_recorded_it(self) -> None:
        from personalized_agent.common.events.models import ListenerEvent
        self.app.raw_store.add_event(ListenerEvent(
            event_type="application.focused", source="application",
            application="Notepad", data={"title": "garden notes.txt - Notepad"}))
        self.app.raw_store.add_event(ListenerEvent(
            event_type="browser.navigation", source="browser",
            data={"url": "https://example.org/soil", "title": "Soil types",
                  "note": "Password: [hidden]"}))
        said = self._routes()[("GET", "/api/record_log")](_Req())
        by_type = {one["type"]: one for one in said["events"]}
        app = by_type["application.focused"]
        self.assertEqual(app["switch"]["key"], "listener_application_enabled")
        self.assertIn("garden notes", app["title"])
        self.assertEqual(app["detail"]["data"]["title"], "garden notes.txt - Notepad")
        page = by_type["browser.navigation"]
        self.assertEqual(page["switch"]["key"], "listener_browser_enabled")
        self.assertEqual(page["hidden"], 1, "where a password was hidden is marked")

    def test_where_something_was_hidden_as_it_was_kept(self) -> None:
        from personalized_agent.common.events.models import ListenerEvent
        self.app.raw_store.add_event(ListenerEvent(
            event_type="screen.text", source="screen",
            data={"text": "Sign in to the garden club. Password: [hidden]",
                  "title": "Garden club"}))
        self.app.raw_store.add_event(ListenerEvent(
            event_type="screen.text", source="screen", data={"text": "Nothing secret."}))
        said = self._routes()[("GET", "/api/record_log")](_Req())
        self.assertEqual(len(said["changed"]), 1)
        fields = said["changed"][0]["fields"]
        self.assertEqual([one["field"] for one in fields], ["data.text"])
        self.assertIn("Password: [hidden]", fields[0]["text"])
        # Only what was kept: there is no "before" to show.
        self.assertNotIn("before", str(said["changed"][0]))

    def test_at_most_the_latest_hundred(self) -> None:
        from personalized_agent.common.events.models import ListenerEvent
        self.app.raw_store.add_events([ListenerEvent(
            event_type="screen.text", source="screen", data={"text": f"line {n}"})
            for n in range(130)])
        said = self._routes()[("GET", "/api/record_log")](_Req())
        self.assertEqual(len(said["events"]), 100)

    def test_what_was_kept_out_is_told_by_rule_not_by_content(self) -> None:
        from personalized_agent.common.events.models import ListenerEvent
        from personalized_agent.listener.core.privacy import PrivacyFilter
        self.app.config.set("listener_enabled", True)
        self.app.config.set("listener_browser_enabled", True)
        self.app.config.set("listener_website_denylist", "example.org")
        privacy = PrivacyFilter(self.app.config)
        event = ListenerEvent(event_type="browser.navigation", source="browser",
                              data={"url": "https://example.org/private-page"})
        self.assertEqual(privacy.refusal(event, "browser"), "website_list")
        self.assertFalse(privacy.allows(event, "browser"))
        self.app.config.set("listener_browser_enabled", False)
        self.assertEqual(PrivacyFilter(self.app.config).refusal(event, "browser"), "switch_off")
        self.app.config.set("listener_browser_enabled", True)
        # Through the listener: noted, and nothing of it kept.
        self.app.listener._on_event(event)
        said = self._routes()[("GET", "/api/record_log")](_Req())
        self.assertEqual(said["dropped_counts"].get("website_list"), 1)
        self.assertEqual(said["dropped"][0]["why"], "website_list")
        self.assertNotIn("private-page", str(said["dropped"]))
        self.assertFalse(any("private-page" in str(one["detail"]) for one in said["events"]))

    def test_the_agents_own_files_are_only_counted(self) -> None:
        from personalized_agent.listener.core import filtered
        filtered.note("filesystem", "filesystem", "filesystem.modified", "agents_own")
        self.assertEqual(filtered.counts()["agents_own"], 1)
        self.assertEqual(filtered.recent(), [])


class RecordLogPageTests(unittest.TestCase):

    def test_a_tab_before_the_debugger_in_every_build(self) -> None:
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertLess(html.index('data-tab="recordlog"'), html.index('data-tab="debugger"'))
        self.assertIn('<section id="recordlog" class="tab">', html)
        js = (WEB / "app.js").read_text(encoding="utf-8")
        # Not one of the tabs a build without the Debugger takes away.
        self.assertNotIn('[data-tab="recordlog"]\')?.remove()', js)
        self.assertIn('if (activeTab === "recordlog") await loadRecordLog();', js)

    def test_try_the_retrieval_is_in_the_work_debugger(self) -> None:
        html = (WEB / "index.html").read_text(encoding="utf-8")
        start = html.index('<section id="xdebug" class="tab">')
        end = html.index("</section>", start)
        self.assertIn('id="xp-search-form"', html[start:end])
        self.assertEqual(html.count('id="xp-search-form"'), 1)


if __name__ == "__main__":
    unittest.main()
