"""A keychain that refuses a secret does not stop the agent: macOS refused
an application whose signature no longer matched it, and the agent quit on
start with its identity key unstored, before its window appeared."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - keeps the user's keychain out of reach

from personalized_agent.models import credentials as cred


class _Refusing:
    """A keychain like the one macOS gives an application it does not trust:
    it reads what it already holds and will not keep anything new."""

    def __init__(self, held=None):
        self.held = dict(held or {})
        self.asked_to_keep = 0
        self.asked_for = []

    def set_password(self, service, key, value):
        self.asked_to_keep += 1
        raise RuntimeError("Can't store password on keychain: (-67030, "
                           "'Security Auth Failure: make sure executable is "
                           "signed with codesign util')")

    def get_password(self, service, key):
        self.asked_for.append(key)
        return self.held.get((service, key))

    def delete_password(self, service, key):
        self.held.pop((service, key), None)


class KeptInTheFileInsteadTests(unittest.TestCase):

    def setUp(self) -> None:
        self.work = tempfile.TemporaryDirectory()
        self.folder = Path(self.work.name) / "credentials"
        self.keychain = _Refusing()
        patches = [mock.patch.object(cred, "keyring", self.keychain),
                   mock.patch.object(cred, "_KEYRING_AVAILABLE", True),
                   mock.patch.dict("os.environ", {cred.DISABLE_KEYRING_ENV: ""})]
        for one in patches:
            one.start()
            self.addCleanup(one.stop)

    def tearDown(self) -> None:
        self.work.cleanup()

    def test_the_secret_is_kept_and_the_keychain_not_asked_again(self) -> None:
        manager = cred.CredentialManager(self.folder)
        self.assertEqual(manager.backend, "keyring")
        manager.set("identity.private_key", "a-made-up-key")   # did raise
        self.assertNotEqual(manager.backend, "keyring")
        self.assertEqual(manager.get("identity.private_key"), "a-made-up-key")
        self.assertTrue((self.folder / cred.KEYCHAIN_REFUSED).exists())

        again = cred.CredentialManager(self.folder)
        self.assertNotEqual(again.backend, "keyring")
        self.assertEqual(again.get("identity.private_key"), "a-made-up-key")
        again.set("another", "value")
        self.assertEqual(self.keychain.asked_to_keep, 1, "asked once, then remembered")

    def test_what_the_keychain_held_before_is_still_read(self) -> None:
        manager = cred.CredentialManager(self.folder)
        self.keychain.held[(manager.service_name, "model.key")] = "kept-before"
        manager.set("new", "one")
        self.assertEqual(manager.get("model.key"), "kept-before")
        manager.delete("model.key")
        self.assertIsNone(manager.get("model.key"))

    def test_the_agent_starts_with_its_identity(self) -> None:
        from support import TemporaryAgent
        agent = TemporaryAgent()            # stored its identity key: did raise
        self.addCleanup(agent.cleanup)
        self.assertGreaterEqual(self.keychain.asked_to_keep, 1)
        self.assertNotEqual(agent.app.credentials.backend, "keyring")
        self.assertIsNotNone(agent.app.identity)


class NotSignedByADeveloperTests(unittest.TestCase):
    """A macOS build signed ad hoc is another program to the keychain after
    every update, which would ask the user again - and a Deny would leave
    the agent without its keys. So it keeps them in its file, and what the
    keychain held before is read once and moved there."""

    def setUp(self) -> None:
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.folder = Path(self.work.name) / "credentials"
        self.keychain = _Refusing()
        patches = [mock.patch.object(cred, "keyring", self.keychain),
                   mock.patch.object(cred, "_KEYRING_AVAILABLE", True),
                   mock.patch.object(cred, "keychain_unreliable",
                                     lambda: "this build is not signed by a developer"),
                   mock.patch.dict("os.environ", {cred.DISABLE_KEYRING_ENV: ""})]
        for one in patches:
            one.start()
            self.addCleanup(one.stop)

    def test_the_file_from_the_start_and_the_keychain_read_once(self) -> None:
        service = cred.service_name_for(self.folder)
        self.keychain.held[(service, "identity.private_key")] = "kept-by-1.0.0"
        manager = cred.CredentialManager(self.folder)
        self.assertNotEqual(manager.backend, "keyring")
        self.assertEqual(manager.get("identity.private_key"), "kept-by-1.0.0")
        manager.set("model.key", "new-one")
        self.assertEqual(self.keychain.asked_to_keep, 0, "nothing written to the keychain")

        again = cred.CredentialManager(self.folder)
        self.assertEqual(again.get("identity.private_key"), "kept-by-1.0.0")
        self.assertEqual(again.get("model.key"), "new-one")
        self.assertEqual(self.keychain.asked_for.count("identity.private_key"), 1,
                         "moved into the file, so asked about once")



class WhichBuildsAreSignedByNobodyTests(unittest.TestCase):

    def _said(self, platform, frozen, codesign_says):
        done = mock.Mock(stdout="", stderr=codesign_says)
        executable = "/Applications/Some Agent.app/Contents/MacOS/Some Agent"
        with mock.patch.object(cred.sys, "platform", platform), \
                mock.patch.object(cred.sys, "frozen", frozen, create=True), \
                mock.patch.object(cred.sys, "executable", executable), \
                mock.patch.object(cred.children, "run_short", return_value=done) as ran:
            return cred.keychain_unreliable(), ran

    def test_an_ad_hoc_macos_build(self) -> None:
        said, ran = self._said("darwin", True, "Executable=...\nSignature=adhoc\n")
        self.assertIn("not signed by a developer", said)
        self.assertEqual(ran.call_args[0][0][:2], ["codesign", "-dv"])
        self.assertTrue(ran.call_args[0][0][2].endswith("Some Agent.app"))
        said, _ = self._said("darwin", True, "code object is not signed at all\n")
        self.assertTrue(said)

    def test_not_a_signed_build_or_another_system(self) -> None:
        said, _ = self._said("darwin", True, "Authority=Developer ID Application: A Maker\n")
        self.assertEqual(said, "")
        said, ran = self._said("darwin", False, "Signature=adhoc")
        self.assertEqual(said, "", "run from source: the keychain is Python's to ask for")
        self.assertFalse(ran.called)
        said, ran = self._said("win32", True, "Signature=adhoc")
        self.assertEqual(said, "")
        self.assertFalse(ran.called)


class SignedAgainAfterTheInfoPlistTests(unittest.TestCase):
    """The macOS build writes Info.plist after PyInstaller signed the bundle;
    without a signing identity it is signed again, ad hoc, and checked."""

    def test_ad_hoc_when_there_is_no_identity(self) -> None:
        root = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(root))
        from build.macos import build_macos
        ran = []
        with mock.patch.dict("os.environ", {"MACOS_SIGN_IDENTITY": ""}), \
                mock.patch.object(build_macos, "run", lambda command, *a, **k: ran.append(command)):
            build_macos.sign_and_notarize(Path("Some.app"))
        self.assertEqual(ran[0][:5], ["codesign", "--force", "--deep", "--sign", "-"])
        self.assertEqual(ran[1][:2], ["codesign", "--verify"])


if __name__ == "__main__":
    unittest.main()
