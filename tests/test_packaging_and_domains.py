"""Storage domain separation, packaging invariants and build tooling.

Covers sections 105-113: trusted application state is unreachable from agent
controlled code, generated scripts live in the workspace runtime directory, the
release version is consistent, and the build-time validation actually fails when
something is wrong.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import ROOT, TemporaryAgent

from personalized_agent.common import paths as paths_module
from personalized_agent.common.paths import (
    AGENT_RUNTIME_DIR,
    INTERNAL_DIR,
    WORKSPACE_DIR,
    AppPaths,
    default_root,
    resource_path,
)
from personalized_agent.runtime import filesystem as fs
from personalized_agent.runtime.permissions import SecurityViolation
from personalized_agent.version import APP_NAME, __version__


class StorageDomainTests(unittest.TestCase):
    """Sections 108-110: two domains, and agent code only reaches one."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.policy = self.app.policy
        self.runtime = self.app.runtime

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_application_state_is_outside_the_workspace(self) -> None:
        internal = self.app.paths.data_dir.resolve()
        workspace = self.policy.workspace_root.resolve()
        self.assertNotEqual(internal, workspace)
        self.assertFalse(fs.is_inside(internal, workspace))

    def test_internal_state_is_registered_as_protected(self) -> None:
        self.assertIn(self.app.paths.data_dir.resolve(),
                      [p.resolve() for p in self.policy.protected_paths])

    def test_agent_cannot_read_application_state(self) -> None:
        settings = self.app.paths.settings_file
        self.assertTrue(settings.exists())
        with self.assertRaises(SecurityViolation) as ctx:
            self.runtime.read_text(settings)
        self.assertEqual(ctx.exception.boundary, "application_state")

    def test_agent_cannot_read_the_credential_store(self) -> None:
        self.app.credentials.set("anthropic.api_key", "sk-ant-protected-value")
        for candidate in self.app.paths.credentials_dir.iterdir():
            with self.assertRaises(SecurityViolation):
                self.runtime.read_text(candidate)

    def test_agent_cannot_read_the_database(self) -> None:
        with self.assertRaises(SecurityViolation):
            self.runtime.read_text(self.app.paths.database_file)

    def test_read_file_tool_refuses_application_state(self) -> None:
        outcome = self.app.agent.tools.call(
            "read_file", {"path": str(self.app.paths.settings_file)}
        )
        self.assertEqual(outcome["error"], "security_violation")
        self.assertEqual(outcome["boundary"], "application_state")

    def test_protection_holds_even_if_state_sits_inside_the_workspace(self) -> None:
        """A misconfigured workspace must not expose trusted state."""
        from personalized_agent.runtime.permissions import WorkspacePolicy
        from personalized_agent.runtime.sandbox import SandboxRuntime

        nested_state = self.policy.workspace_root / "nested_state"
        nested_state.mkdir(exist_ok=True)
        (nested_state / "settings.json").write_text("{}", encoding="utf-8")
        policy = WorkspacePolicy.create(
            self.policy.workspace_root, protected_paths=[nested_state]
        )
        runtime = SandboxRuntime(policy, platform=self.app.platform, sandbox_enabled=False)
        with self.assertRaises(SecurityViolation):
            runtime.write_text(nested_state / "evil.txt", "x")
        with self.assertRaises(SecurityViolation):
            runtime.read_text(nested_state / "settings.json")

    def test_generated_script_cannot_read_application_state(self) -> None:
        target = self.app.paths.settings_file.as_posix()
        result = self.runtime.run_python_code(
            f"print(open({target!r}).read())\n", timeout=90
        )
        self.assertFalse(result.ok)
        self.assertIn("SandboxViolation", result.stderr)

    def test_generated_script_cannot_write_application_state(self) -> None:
        target = (self.app.paths.data_dir / "planted.txt").as_posix()
        result = self.runtime.run_python_code(
            f"open({target!r}, 'w').write('nope')\n", timeout=90
        )
        self.assertFalse(result.ok)
        self.assertFalse((self.app.paths.data_dir / "planted.txt").exists())

    def test_self_test_reports_every_invariant(self) -> None:
        report = self.runtime.self_test()
        names = {check["check"] for check in report["checks"]}
        self.assertEqual(
            names,
            {
                "workspace_write_allowed",
                "workspace_escape_blocked",
                "application_state_protected",
                "generated_script_runs",
                "generated_script_escape_blocked",
                # Section 32, as amended: the escape check cannot see this
                # one, because the user's material is inside the workspace.
                "user_material_not_writable",
                # Section 31, as amended.
                "process_creation_blocked",
            },
        )
        self.assertTrue(report["passed"], report)


class WorkspaceLayoutTests(unittest.TestCase):
    """Section 111: generated code lives under the workspace runtime directory."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_runtime_directory_layout(self) -> None:
        # Section 111, as amended: inside the agent's own directory.
        runtime_root = self.app.policy.write_root / AGENT_RUNTIME_DIR
        for name in ("scripts", "tasks", "temp"):
            self.assertTrue((runtime_root / name).is_dir(), name)

    def test_generated_scripts_land_in_the_runtime_directory(self) -> None:
        script = self.app.runtime.store_generated_script("print('hi')\n")
        self.assertEqual(script.parent, self.app.policy.scripts_dir)
        self.assertIn(AGENT_RUNTIME_DIR, str(script))

    def test_generated_scripts_are_not_in_the_installation_directory(self) -> None:
        script = self.app.runtime.store_generated_script("print('hi')\n")
        install_root = Path(sys.argv[0]).resolve().parent
        package_root = Path(paths_module.__file__).resolve().parents[1]
        self.assertFalse(fs.is_inside(script.resolve(), package_root))
        self.assertFalse(fs.is_inside(script.resolve(), install_root))

    def test_temporary_files_stay_in_the_runtime_directory(self) -> None:
        temp = self.app.runtime.temp_path("scratch.txt")
        self.assertEqual(temp.parent, self.app.policy.temp_dir)

    def test_user_facing_directories_exist(self) -> None:
        for name in ("documents", "downloads", "exports", "generated"):
            self.assertTrue((self.app.policy.workspace_root / name).is_dir(), name)


class ApplicationPathTests(unittest.TestCase):
    """Section 110.1: one writable root holding both domains."""

    def test_default_root_is_a_single_user_directory(self) -> None:
        root = default_root()
        self.assertEqual(root.parent, Path.home())
        self.assertTrue(str(root).endswith("PersonalizedAgent"))

    def test_explicit_override_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["PERSONALIZED_AGENT_HOME"] = tmp
            try:
                self.assertEqual(paths_module.resolve_root(), Path(tmp).resolve())
                self.assertEqual(paths_module.default_data_dir(),
                                 Path(tmp).resolve() / INTERNAL_DIR)
            finally:
                os.environ.pop("PERSONALIZED_AGENT_HOME", None)

    def test_both_domains_live_inside_the_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = AppPaths.create(Path(tmp) / "AgentRoot")
            self.assertEqual(paths.workspace_dir, paths.root / WORKSPACE_DIR)
            self.assertEqual(paths.data_dir, paths.root / INTERNAL_DIR)
            for directory in (paths.settings_dir, paths.database_dir, paths.logs_dir,
                              paths.credentials_dir, paths.characters_dir,
                              paths.workspace_dir):
                self.assertTrue(directory.is_dir())
                self.assertTrue(fs.is_inside(directory.resolve(), paths.root.resolve()))
            self.assertTrue(paths.describe()["single_writable_root"])

    def test_the_two_domains_are_siblings_not_nested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = AppPaths.create(Path(tmp) / "AgentRoot")
            self.assertFalse(fs.is_inside(paths.data_dir, paths.workspace_dir))
            self.assertFalse(fs.is_inside(paths.workspace_dir, paths.data_dir))

    def test_nothing_is_written_outside_the_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "AgentRoot"
            sibling = Path(tmp) / "untouched"
            sibling.mkdir()
            paths = AppPaths.create(root)
            self.assertEqual(list(sibling.iterdir()), [])
            for entry in Path(tmp).iterdir():
                self.assertIn(entry.name, {"AgentRoot", "untouched"})
            self.assertEqual(sorted(p.name for p in paths.root.iterdir()),
                             [INTERNAL_DIR, WORKSPACE_DIR])

    def test_packaged_resources_resolve(self) -> None:
        self.assertTrue(resource_path("api", "web", "index.html").exists())
        self.assertTrue(
            resource_path("resources", "characters", "watermelon", "manifest.json").exists()
        )


class CharacterResourceTests(unittest.TestCase):
    """Section 112: packaged resources are read-only, installs go to app state."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_builtin_package_is_reported_read_only(self) -> None:
        package = self.app.characters.get("watermelon")
        self.assertIsNotNone(package)
        self.assertEqual(package["origin"], "builtin")
        self.assertTrue(package["read_only"])
        self.assertTrue(package["frames"]["idle"])

    def test_installed_packages_go_to_the_internal_data_directory(self) -> None:
        import zipfile

        archive = Path(self.agent.dir) / "custom.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("custom/manifest.json", json.dumps({"name": "custom"}))
            zf.writestr("custom/idle/frame_00.png", b"\x89PNG\r\n\x1a\n")
        installed = self.app.characters.install_package(archive)
        self.assertEqual(installed["origin"], "installed")
        self.assertTrue((self.app.paths.characters_dir / "custom").is_dir())
        # The packaged resource directory is untouched.
        self.assertFalse((self.app.paths.builtin_characters_dir / "custom").exists())

    def test_archive_members_cannot_escape(self) -> None:
        import zipfile

        archive = Path(self.agent.dir) / "evil.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("evil/../../escape.json", "{}")
        with self.assertRaises(ValueError):
            self.app.characters.install_package(archive)

    def test_custom_avatar_is_an_installed_character(self) -> None:
        from PIL import Image
        import base64
        import io

        source = Image.new("RGBA", (20, 10), (24, 80, 180, 255))
        buffer = io.BytesIO()
        source.save(buffer, format="PNG")
        data_url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
        avatar = self.app.characters.create_avatar(data_url)

        self.assertEqual(avatar["name"], "custom-avatar")
        self.assertEqual(avatar["origin"], "installed")
        self.assertTrue((self.app.paths.characters_dir / "custom-avatar" / "idle" / "frame_00.png").exists())
        self.assertTrue(self.app.characters.preview("custom-avatar").startswith("data:image/png;base64,"))

    def test_avatar_api_selects_the_custom_avatar(self) -> None:
        from PIL import Image
        import base64
        import io
        from personalized_agent.api.routes import build_routes

        image = Image.new("RGBA", (2, 2), (220, 80, 80, 255))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        test_case = self

        class Request:
            def get(self, _key, default=None):
                return default

            def require(self, key):
                test_case.assertEqual(key, "image")
                return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")

        response = build_routes(self.app)[("POST", "/api/characters/avatar")](Request())
        self.assertEqual(response["selected"]["name"], "custom-avatar")
        self.assertEqual(self.app.config.get("character"), "custom-avatar")

    def test_pixel_avatar_keeps_hard_edges(self) -> None:
        from PIL import Image
        import base64
        import io

        source = Image.new("RGBA", (2, 2))
        source.putdata([(255, 0, 0, 255), (0, 0, 255, 255),
                        (0, 255, 0, 255), (255, 255, 255, 255)])
        buffer = io.BytesIO()
        source.save(buffer, format="PNG")
        data_url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
        self.app.characters.create_avatar(data_url, pixel_art=True)
        with Image.open(self.app.paths.characters_dir / "custom-avatar" / "idle" / "frame_00.png") as avatar:
            self.assertEqual(avatar.getpixel((63, 63)), (255, 0, 0, 255))
            self.assertEqual(avatar.getpixel((64, 63)), (0, 0, 255, 255))


class VersionConsistencyTests(unittest.TestCase):
    """Section 105: one version, visible everywhere."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_status_reports_the_version(self) -> None:
        status = self.app.status()
        self.assertEqual(status["application"]["version"], __version__)
        self.assertEqual(status["application"]["name"], APP_NAME)

    def test_pyproject_matches_the_package_version(self) -> None:
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn(f'version = "{__version__}"', text)

    def test_cli_reports_the_version(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "personalized_agent", "--version"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(__version__, completed.stdout)


class BuildToolingTests(unittest.TestCase):
    """Sections 102, 103, 113: the build entry point and its validation."""

    def test_validation_passes_on_a_clean_checkout(self) -> None:
        from build.validate_dependencies import (
            check_assets,
            check_no_secrets,
            check_platform_providers,
        )
        from build.common import current_platform

        self.assertEqual(check_assets(), [])
        self.assertEqual(check_platform_providers(current_platform()), [])
        self.assertEqual(check_no_secrets(), [])

    def test_validation_detects_a_missing_asset(self) -> None:
        from build import validate_dependencies

        original = validate_dependencies.REQUIRED_ASSETS
        validate_dependencies.REQUIRED_ASSETS = [*original, "personalized_agent/does_not_exist"]
        try:
            errors = validate_dependencies.check_assets()
        finally:
            validate_dependencies.REQUIRED_ASSETS = original
        self.assertTrue(any("does_not_exist" in error for error in errors))

    def test_cross_platform_build_is_refused(self) -> None:
        from build.common import BuildError, current_platform, require_native_platform

        other = "macos" if current_platform() != "macos" else "linux"
        with self.assertRaises(BuildError):
            require_native_platform(other)

    def test_build_command_excludes_foreign_platform_modules(self) -> None:
        from build.common import current_platform, pyinstaller_command

        target = current_platform()
        command = pyinstaller_command(target, name="Test")
        joined = " ".join(command)
        self.assertIn(f"personalized_agent.platform_layer.{target}", joined)
        for other in ("windows", "macos", "linux"):
            if other == target:
                continue
            index = command.index(f"personalized_agent.platform_layer.{other}")
            self.assertEqual(command[index - 1], "--exclude-module")

    def test_build_entry_point_validates_only(self) -> None:
        completed = subprocess.run(
            [sys.executable, "build.py", "--validate-only"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=300,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("build validation passed", completed.stdout)

    def test_dist_root_is_overridable(self) -> None:
        from build.common import DIST_ENV, dist_root

        with tempfile.TemporaryDirectory() as tmp:
            os.environ[DIST_ENV] = tmp
            try:
                self.assertEqual(dist_root(), Path(tmp))
            finally:
                os.environ.pop(DIST_ENV, None)
        self.assertEqual(dist_root(), ROOT / "dist")

    def test_cloud_sync_folders_are_detected(self) -> None:
        from build.common import detect_cloud_sync

        self.assertEqual(detect_cloud_sync(Path.home() / "Dropbox" / "project" / "dist"),
                         "Dropbox")
        self.assertEqual(detect_cloud_sync(Path.home() / "OneDrive" / "dist"), "OneDrive")
        self.assertIsNone(detect_cloud_sync(Path(tempfile.gettempdir()) / "dist"))

    def test_staging_directory_is_outside_the_repository(self) -> None:
        from build.common import staging_dir

        staging = staging_dir("windows")
        try:
            self.assertFalse(fs.is_inside(staging.resolve(), ROOT.resolve()))
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def test_remove_tree_clears_read_only_files(self) -> None:
        from build.common import remove_tree

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "output"
            (target / "nested").mkdir(parents=True)
            locked = target / "nested" / "readonly.txt"
            locked.write_text("x", encoding="utf-8")
            locked.chmod(0o444)
            remove_tree(target)
            self.assertFalse(target.exists())

    def test_locked_output_error_names_the_cause(self) -> None:
        """A build failure has to say why and what to do (section 113)."""
        from build.common import _lock_message

        message = _lock_message(
            Path.home() / "Dropbox" / "project" / "dist" / "windows" / "App",
            PermissionError("Access is denied"),
            "previous build output",
        )
        self.assertIn("Dropbox", message)
        self.assertIn("--dist", message)
        self.assertIn("Access is denied", message)

    def test_publish_tree_replaces_the_previous_output(self) -> None:
        from build.common import publish_tree

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "staging" / "App"
            source.mkdir(parents=True)
            (source / "new.txt").write_text("new", encoding="utf-8")
            destination = Path(tmp) / "dist" / "App"
            destination.mkdir(parents=True)
            (destination / "old.txt").write_text("old", encoding="utf-8")
            published = publish_tree(source, destination)
            self.assertTrue((published / "new.txt").exists())
            self.assertFalse((published / "old.txt").exists())
            self.assertFalse(source.exists())

    def test_release_validation_script_is_executable(self) -> None:
        completed = subprocess.run(
            [sys.executable, "scripts/release_validation.py", "--help"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--executable", completed.stdout)


if __name__ == "__main__":
    unittest.main()


class DocumentAmendmentTests(unittest.TestCase):
    """The reader reproduces the PDF, except for clearly marked amendments."""

    def test_pipeline_files_exist(self) -> None:
        for name in ("pdf_to_sections.py", "markdown_doc.py", "build_reader.py",
                     "verify_fidelity.py", "reader_template.html", "sections.json",
                     "design-doc.md", "sections_source.json", "pdf_baseline.json"):
            self.assertTrue((ROOT / "docs" / name).exists(), name)

    def test_amendments_are_marked_and_explained(self) -> None:
        # The specification as revised on 2026-10-15 is the original now
        # (docs/pdf_baseline.json): an edit after it is marked, and explained.
        sections = json.loads((ROOT / "docs" / "sections.json").read_text(encoding="utf-8"))
        for section in [s for s in sections if s.get("amended")]:
            self.assertTrue(section.get("amendment_note"), section["number"])
            self.assertTrue(any(b.get("amended") for b in section["blocks"]))
        sys.path.insert(0, str(ROOT / "docs"))
        from build_reader import mark_amendments
        original = json.loads((ROOT / "docs" / "sections_source.json").read_text(encoding="utf-8"))
        edited = json.loads(json.dumps(original))
        edited[3]["blocks"][0] = dict(edited[3]["blocks"][0], text="Changed after the revision.")
        edited.append({"number": "999", "label": "999", "id": "s999", "title": "New",
                       "page": 0, "blocks": [{"type": "p", "text": "Added."}]})
        marked = mark_amendments(edited, original)
        self.assertEqual([s["number"] for s in marked if s.get("amended")],
                         [original[3]["number"], "999"])
        self.assertTrue(marked[3]["blocks"][0]["amended"])

    def test_section_numbers_are_unique(self) -> None:
        sections = json.loads((ROOT / "docs" / "sections.json").read_text(encoding="utf-8"))
        numbers = [s["number"] for s in sections]
        self.assertEqual(len(numbers), len(set(numbers)))

    def test_the_account_login_gap_is_closed(self) -> None:
        sections = json.loads((ROOT / "docs" / "sections.json").read_text(encoding="utf-8"))
        added = next(s for s in sections if s["number"] == "75.1")
        text = " ".join(b.get("text", "") + " ".join(b.get("items", []))
                        for b in added["blocks"])
        self.assertIn("must initiate an authorization flow", text)
        self.assertIn("must not present Account Login", text)

    def test_the_editable_source_round_trips(self) -> None:
        """An edit must survive parse -> render -> parse, or it is silently lost."""
        sys.path.insert(0, str(ROOT / "docs"))
        from markdown_doc import from_markdown, to_markdown

        text = (ROOT / "docs" / "design-doc.md").read_text(encoding="utf-8")
        parsed = from_markdown(text)
        published = json.loads((ROOT / "docs" / "sections.json").read_text(encoding="utf-8"))
        self.assertEqual(len(parsed), len(published))
        self.assertEqual(from_markdown(to_markdown(parsed)), parsed)

    def test_the_editable_source_matches_the_published_reader(self) -> None:
        """Editing the Markdown and not rebuilding must not go unnoticed."""
        sys.path.insert(0, str(ROOT / "docs"))
        from markdown_doc import from_markdown

        published = json.loads((ROOT / "docs" / "sections.json").read_text(encoding="utf-8"))
        stripped = [{**{k: v for k, v in s.items() if k != "amended"},
                     "blocks": [{k: v for k, v in b.items() if k != "amended"}
                                for b in s["blocks"]]}
                    for s in published]
        edited = from_markdown((ROOT / "docs" / "design-doc.md").read_text(encoding="utf-8"))
        self.assertEqual(edited, stripped,
                         "docs/design-doc.md has changed since the last "
                         "`python docs/build_reader.py`")

    def test_original_text_is_unchanged(self) -> None:
        completed = subprocess.run(
            [sys.executable, "docs/verify_fidelity.py"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=600,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("IDENTICAL", completed.stdout)


class RelocatableRootTests(unittest.TestCase):
    """Section 110.1: the root is moved by copying the directory."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="pa-relocate-")).resolve()
        self.old = self.base / "OldRoot"
        self.new = self.base / "NewRoot"

    def tearDown(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)

    def _populate(self):
        from datetime import timedelta

        from personalized_agent.app import AgentApplication
        from personalized_agent.common.events.models import ScheduledTask, utcnow

        app = AgentApplication(root=self.old)
        app.config.set("agent_name", "Copied Agent")
        app.config.set("listener_watch_paths", str(self.old / "workspace" / "documents"))
        from personalized_agent.data_manage.library import USER_FEEDBACK, Record
        app.library.write(Record(folder=USER_FEEDBACK, title="Portable note",
                                 body="kept", source="test"))
        script = app.runtime.store_generated_script("print('hi')\n", name="portable.py")
        task = app.scheduler_store.create(ScheduledTask(
            title="portable task", scheduled_at=utcnow() + timedelta(days=1),
            execution_type="python_script", script_path=str(script)))
        app.db.close()
        return task.id

    def _reopen(self, root: Path):
        from personalized_agent.app import AgentApplication

        return AgentApplication(root=root)

    def test_copying_the_root_preserves_the_installation(self) -> None:
        task_id = self._populate()
        shutil.copytree(self.old, self.new)
        moved = self._reopen(self.new)
        try:
            self.assertEqual(moved.config.get("agent_name"), "Copied Agent")
            self.assertEqual([r.title for r in moved.library.records(limit=5)],
                             ["Portable note"])
            self.assertIsNotNone(moved.scheduler_store.get(task_id))
        finally:
            moved.db.close()

    def test_stored_paths_are_rewritten_to_the_new_root(self) -> None:
        task_id = self._populate()
        shutil.copytree(self.old, self.new)
        moved = self._reopen(self.new)
        try:
            self.assertEqual(Path(moved.status()["relocated_from"]), self.old)
            watch = moved.config.lines("listener_watch_paths")[0]
            self.assertTrue(Path(watch).is_relative_to(self.new), watch)
            script_path = Path(moved.scheduler_store.get(task_id).script_path)
            self.assertTrue(script_path.is_relative_to(self.new), script_path)
            self.assertTrue(script_path.exists())
            self.assertNotIn(str(self.old), json.dumps(moved.config.all()))
        finally:
            moved.db.close()

    def test_the_agent_still_runs_from_the_copy(self) -> None:
        self._populate()
        shutil.copytree(self.old, self.new)
        moved = self._reopen(self.new)
        try:
            result = moved.agent.handle_message("after the move")
            self.assertTrue(result["answer"])
            written = moved.runtime.write_text("documents/after-move.txt", "ok")
            self.assertTrue(written.is_relative_to(self.new))
        finally:
            moved.db.close()

    def test_an_unmoved_root_is_not_reported_as_relocated(self) -> None:
        self._populate()
        reopened = self._reopen(self.old)
        try:
            self.assertIsNone(reopened.status()["relocated_from"])
        finally:
            reopened.db.close()


class DependencyCompletenessTests(unittest.TestCase):
    """Section 113, as amended: a dependency counts only if it can run."""

    def test_packages_with_helper_binaries_are_collected_whole(self) -> None:
        sys.path.insert(0, str(ROOT))
        from build.common import COLLECT_ALL_PACKAGES, collect_arguments

        self.assertIn("winpty", COLLECT_ALL_PACKAGES.get("windows", []),
                      "pywinpty ships helper executables PyInstaller does not find")
        args = collect_arguments("windows")
        if importlib.util.find_spec("winpty") is not None:
            self.assertIn("--collect-all", args)
            self.assertIn("winpty", args)

    def test_a_missing_optional_package_does_not_break_the_build(self) -> None:
        sys.path.insert(0, str(ROOT))
        from build.common import collect_arguments

        from build.common import COLLECT_ALL_PACKAGES

        # Only what is installed on this machine is bundled; the rest is
        # skipped with a note rather than failing the build.
        installed = [name for name in COLLECT_ALL_PACKAGES.get("linux", [])
                     if importlib.util.find_spec(name) is not None]
        expected = [arg for name in installed for arg in ("--collect-all", name)]
        self.assertEqual(collect_arguments("linux"), expected)

    def test_the_availability_check_tests_what_the_feature_needs(self) -> None:
        """Importing the module is not the thing the sign-in relies on."""
        from personalized_agent.models.providers.account_login import PtyTransport

        spec = importlib.util.find_spec("winpty")
        if spec is None:
            self.assertFalse(PtyTransport.available())
            return
        package = Path(spec.origin).parent
        has_backend = ((package / "winpty-agent.exe").exists()
                       or ((package / "conpty.dll").exists()
                           and (package / "OpenConsole.exe").exists()))
        self.assertEqual(PtyTransport.available(),
                         has_backend and (package / "winpty.dll").exists())

    def test_release_validation_exercises_advertised_authentication(self) -> None:
        source = (ROOT / "scripts" / "release_validation.py").read_text(encoding="utf-8")
        self.assertIn("advertised_capabilities_work", source)
        self.assertIn("/api/models/login/start", source)


LF = chr(10)


class ExternalToolBoundaryTests(unittest.TestCase):
    """Section 110.1, as amended: an external tool's own store is not ours.

    The sign-in of section 75.2 runs a provider tool that keeps state in the
    user's home directory. That is the tool's business - but agent-controlled
    code must gain nothing from it being there.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.target = Path.home() / ".claude"

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_agent_code_may_not_write_to_an_external_tool_store(self) -> None:
        from personalized_agent.runtime.filesystem import validate_write_path

        for name in ("probe.txt", ".credentials.json"):
            with self.subTest(name):
                with self.assertRaises(Exception):
                    validate_write_path(self.target / name, self.app.policy)

    def test_agent_code_may_not_read_an_external_tool_store(self) -> None:
        from personalized_agent.runtime.filesystem import validate_read_path

        with self.assertRaises(Exception):
            validate_read_path(self.target / ".credentials.json", self.app.policy)

    def test_a_generated_script_cannot_reach_it_either(self) -> None:
        probe = self.target / "agent_probe_should_not_exist.txt"
        code = LF.join([
            "from pathlib import Path",
            f"p = Path(r'{probe}')",
            "try:",
            "    p.write_text('agent was here')",
            "    print('WROTE')",
            "except Exception as exc:",
            "    print('REFUSED', type(exc).__name__)",
        ])
        result = self.app.runtime.run_python_code(code, timeout=90)
        output = (result.stdout or "") + (result.stderr or "")
        self.assertNotIn("WROTE", output)
        self.assertFalse(probe.exists(), "agent-controlled code wrote outside the root")

    def test_no_agent_tool_can_start_a_sign_in(self) -> None:
        """Only trusted application code, on an explicit user action, may."""
        tools = self.app.agent.tools
        names = list(getattr(tools, "names", lambda: [])())
        if not names and hasattr(tools, "describe"):
            names = [entry.get("name", "") for entry in tools.describe()]
        offending = [n for n in names
                     if any(word in n.lower() for word in ("login", "auth", "credential"))]
        self.assertEqual(offending, [])


class SingleInstanceTests(unittest.TestCase):
    """Section 110.2: one agent at a time, and it says so."""

    def setUp(self) -> None:
        from personalized_agent.common.instance import SingleInstance

        self.factory = SingleInstance
        self.base = Path(tempfile.mkdtemp(prefix="pa-instance-"))
        self.root = self.base / "Root"
        self.held = []

    def tearDown(self) -> None:
        for guard in self.held:
            guard.release()
        shutil.rmtree(self.base, ignore_errors=True)

    def _acquire(self, root, system_wide=False):
        # The root lock is what these tests are about. Taking the real
        # system-wide guard would make the suite fail whenever the user happens
        # to have the agent running, which is not a defect in the code.
        guard = self.factory(root, system_wide=system_wide).acquire()
        self.held.append(guard)
        return guard

    def test_a_second_instance_on_the_same_root_is_refused(self) -> None:
        from personalized_agent.common.instance import AlreadyRunning

        self._acquire(self.root)
        with self.assertRaises(AlreadyRunning):
            self.factory(self.root, system_wide=False).acquire()

    def test_the_refusal_names_the_running_instance(self) -> None:
        from personalized_agent.common.instance import AlreadyRunning

        self._acquire(self.root)
        with self.assertRaises(AlreadyRunning) as caught:
            self.factory(self.root, system_wide=False).acquire()
        message = str(caught.exception)
        self.assertIn("already running", message)
        self.assertIn(str(os.getpid()), message, "the message must identify the holder")
        self.assertIn(str(self.root), message)

    def test_a_different_root_does_not_defeat_the_guard(self) -> None:
        from personalized_agent.common.instance import AlreadyRunning

        try:
            self._acquire(self.root, system_wide=True)
        except AlreadyRunning:
            self.skipTest("an agent is already running on this machine")
        with self.assertRaises(AlreadyRunning):
            self.factory(self.base / "Elsewhere").acquire()

    def test_releasing_lets_the_next_instance_start(self) -> None:
        guard = self.factory(self.root, system_wide=False).acquire()
        guard.release()
        second = self.factory(self.root, system_wide=False).acquire()
        self.assertTrue(second.held)
        second.release()

    def test_the_guard_writes_only_inside_the_root(self) -> None:
        """Section 110.1 still holds: the guard is not an exception to it."""
        guard = self._acquire(self.root)
        self.assertTrue(guard.lock_path.is_relative_to(self.root))
        self.assertEqual(guard.lock_path.parent.name, "internal")

    def test_a_lock_left_by_a_dead_process_is_recognised(self) -> None:
        from personalized_agent.common.instance import stale_lock_holder

        lock = self.root / "internal" / "agent.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(json.dumps({"pid": 2 ** 30, "root": str(self.root)}),
                        encoding="utf-8")
        self.assertIsNotNone(stale_lock_holder(self.root))
        # ... and does not stop a new instance from starting
        self._acquire(self.root)

    def test_a_status_snapshot_is_not_guarded(self) -> None:
        """A read-only snapshot does not run the agent, so it must still work."""
        source = (ROOT / "personalized_agent" / "__main__.py").read_text(encoding="utf-8")
        self.assertIn("if not args.status:", source)


class SchemaMigrationTests(unittest.TestCase):
    """Section 110.1, as amended: the data outlives the version that made it.

    CREATE TABLE IF NOT EXISTS does nothing to a table that already exists, so
    a database written by an earlier version keeps its old columns and every
    query naming a new one fails.
    """

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="pa-migrate-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def _old_database(self) -> Path:
        path = self.dir / "old.db"
        con = sqlite3.connect(path)
        con.execute(
            "CREATE TABLE checklist_items (id TEXT PRIMARY KEY, position INTEGER NOT NULL, "
            "text TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, "
            "origin TEXT NOT NULL DEFAULT 'system', updated_at TEXT NOT NULL)")
        con.execute("INSERT INTO checklist_items VALUES ('c1', 0, 'an old step', 1, "
                    "'system', '2026-01-01T00:00:00+00:00')")
        con.commit()
        con.close()
        return path

    def test_an_older_database_gains_what_it_is_missing(self) -> None:
        from personalized_agent.data_manage.storage.database import Database

        db = Database(self._old_database())
        columns = {row["name"] for row in db.query("PRAGMA table_info(checklist_items)")}
        self.assertIn("retired_at", columns)
        self.assertIn("retired_reason", columns)

    def test_what_was_already_there_is_left_alone(self) -> None:
        from personalized_agent.data_manage.storage.database import Database

        db = Database(self._old_database())
        row = db.query_one("SELECT * FROM checklist_items WHERE id = 'c1'")
        self.assertEqual(row["text"], "an old step")
        self.assertIsNone(row["retired_at"], "a row written before the column has no value")

    def test_a_store_can_read_a_row_written_before_its_column_existed(self) -> None:
        """A row from an older version has to read, not raise.

        The original example was an edge written before weights were learned,
        which has no evidence recorded. Edges went with the graph, so this now
        uses a state written before it counted its own extensions - the same
        shape of problem on a table the system still writes to, and one that
        will keep happening as long as columns keep being added.
        """
        from personalized_agent.agent.states.store import StateStore
        from personalized_agent.data_manage.storage.database import Database

        db = Database(self._old_database())
        db.execute(
            "INSERT INTO states (id, title, detail, source, about, status, "
            "task_id, created_at, updated_at) "
            "VALUES ('s1', 'an old state', '', 'agent', 'user', 'active', '', "
            "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')")

        state = StateStore(db).get("s1")
        self.assertIsNotNone(state, "a row from an older version must still read")
        self.assertEqual(state.title, "an old state")
        self.assertIsNone(state.starts_at, "an absent span reads as absent")
        self.assertEqual(state.span_hours, 0.0,
                         "and everything derived from it still answers")

    def test_opening_a_current_database_twice_changes_nothing(self) -> None:
        from personalized_agent.data_manage.storage.database import Database

        path = self.dir / "fresh.db"
        first = {row["name"] for row in Database(path).query(
            "PRAGMA table_info(checklist_items)")}
        second = {row["name"] for row in Database(path).query(
            "PRAGMA table_info(checklist_items)")}
        self.assertEqual(first, second)
