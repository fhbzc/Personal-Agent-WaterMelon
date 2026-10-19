"""What a packaged build on a Mac met at its first start (2026-10-19): every
https request refused for want of certificates, and a window reader the
platform does not have logged as an ERROR with its traceback each start."""
import logging
import os
import sys
import unittest
from unittest import mock

from personalized_agent import __main__ as entry
from personalized_agent.listener.core.manager import ListenerManager
from personalized_agent.listener.core.provider import NotOnThisMachine


class CertificatesCarriedTests(unittest.TestCase):
    def setUp(self):
        self.saved = os.environ.pop("SSL_CERT_FILE", None)

    def tearDown(self):
        os.environ.pop("SSL_CERT_FILE", None)
        if self.saved is not None:
            os.environ["SSL_CERT_FILE"] = self.saved

    def test_a_packaged_build_on_a_mac_trusts_the_certificates_it_carries(self):
        import certifi
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "platform", "darwin"):
            entry._certificates_carried()
        self.assertEqual(os.environ.get("SSL_CERT_FILE"), certifi.where())

    def test_windows_a_source_run_and_the_users_own_file_are_left_alone(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "platform", "win32"):
            entry._certificates_carried()
        self.assertNotIn("SSL_CERT_FILE", os.environ)
        with mock.patch.object(sys, "platform", "darwin"):
            if hasattr(sys, "frozen"):
                self.skipTest("this interpreter is itself a packaged build")
            entry._certificates_carried()
        self.assertNotIn("SSL_CERT_FILE", os.environ)
        os.environ["SSL_CERT_FILE"] = "their-own.pem"
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "platform", "darwin"):
            entry._certificates_carried()
        self.assertEqual(os.environ["SSL_CERT_FILE"], "their-own.pem")


class NothingToObserveHereTests(unittest.TestCase):
    def test_a_provider_with_nothing_here_is_one_line_not_an_error(self):
        provider = mock.Mock(provider_id="screen", category="screen")
        provider.start.side_effect = NotOnThisMachine("this platform cannot read windows")
        manager = ListenerManager.__new__(ListenerManager)
        manager.privacy = mock.Mock(category_enabled=lambda _c: True)
        manager.observability = mock.Mock()
        manager._states = {"screen": {}}
        with self.assertLogs("personalized_agent.listener.core.manager", "INFO") as logs:
            manager._start_provider("screen", provider)
        self.assertEqual([r.levelno for r in logs.records], [logging.INFO])
        self.assertIsNone(logs.records[0].exc_info)
        self.assertEqual(manager._states["screen"]["state"], "failed")
        manager.observability.record_error.assert_not_called()


if __name__ == "__main__":
    unittest.main()
