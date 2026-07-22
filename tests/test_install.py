from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import install_skill  # noqa: E402


class CopySkillTests(unittest.TestCase):
    @staticmethod
    def make_source(root: Path) -> Path:
        source = root / "source"
        source.mkdir()
        (source / "SKILL.md").write_text("# fixture skill\n", encoding="utf-8")

        expected_files = {
            "references/reference.md": "reference\n",
            "scripts/tool.py": "print('tool')\n",
            "assets/logo.txt": "asset\n",
            "agents/openai.yaml": "name: fixture\n",
        }
        for relative_path, content in expected_files.items():
            path = source / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        ignored_files = {
            "scripts/__pycache__/tool.cpython-313.pyc": b"cached",
            "scripts/direct.pyc": b"compiled",
            "references/nested/__pycache__/reference.pyc": b"cached",
            "agents/nested/agent.pyc": b"compiled",
        }
        for relative_path, content in ignored_files.items():
            path = source / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        return source

    def test_copy_skill_copies_resources_and_filters_bytecode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            source = self.make_source(temporary)
            destination = temporary / "installed" / install_skill.SKILL_NAME

            install_skill.copy_skill(source, destination)

            expected_files = {
                "SKILL.md": "# fixture skill\n",
                "references/reference.md": "reference\n",
                "scripts/tool.py": "print('tool')\n",
                "assets/logo.txt": "asset\n",
                "agents/openai.yaml": "name: fixture\n",
            }
            for relative_path, expected_content in expected_files.items():
                with self.subTest(relative_path=relative_path):
                    copied = destination / relative_path
                    self.assertTrue(copied.is_file())
                    self.assertEqual(
                        expected_content,
                        copied.read_text(encoding="utf-8"),
                    )

            self.assertFalse(any(
                path.name == "__pycache__"
                for path in destination.rglob("*")
            ))
            self.assertEqual([], list(destination.rglob("*.pyc")))

    def test_copy_skill_replaces_destination_instead_of_leaving_old_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            source = self.make_source(temporary)
            destination = temporary / "installed" / install_skill.SKILL_NAME
            (destination / "references").mkdir(parents=True)
            (destination / "old.txt").write_text("obsolete\n", encoding="utf-8")
            (destination / "references" / "removed.md").write_text(
                "obsolete\n",
                encoding="utf-8",
            )

            install_skill.copy_skill(source, destination)

            self.assertFalse((destination / "old.txt").exists())
            self.assertFalse((destination / "references" / "removed.md").exists())
            self.assertTrue((destination / "SKILL.md").is_file())
            self.assertEqual(
                [],
                list(destination.parent.glob(f".{install_skill.SKILL_NAME}.*-*")),
            )

    def test_copy_skill_source_equal_to_destination_is_safe_noop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / install_skill.SKILL_NAME
            destination.mkdir()
            skill_file = destination / "SKILL.md"
            sentinel = destination / "keep-me.txt"
            skill_file.write_text("# original\n", encoding="utf-8")
            sentinel.write_text("must remain\n", encoding="utf-8")

            install_skill.copy_skill(destination, destination)

            self.assertTrue(destination.is_dir())
            self.assertEqual("# original\n", skill_file.read_text(encoding="utf-8"))
            self.assertEqual("must remain\n", sentinel.read_text(encoding="utf-8"))
            self.assertEqual(
                {"SKILL.md", "keep-me.txt"},
                {path.name for path in destination.iterdir()},
            )

    def test_copy_skill_rejects_overlapping_source_and_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            destination = root / install_skill.SKILL_NAME
            destination.mkdir()
            nested_source = self.make_source(destination)
            sentinel = destination / "keep-me.txt"
            sentinel.write_text("must remain\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "must not contain"):
                install_skill.copy_skill(nested_source, destination)

            self.assertTrue(nested_source.is_dir())
            self.assertEqual("must remain\n", sentinel.read_text(encoding="utf-8"))

            separate = root / "separate"
            separate.mkdir()
            source = self.make_source(separate)
            nested_destination = source / "nested" / install_skill.SKILL_NAME
            with self.assertRaisesRegex(ValueError, "must not contain"):
                install_skill.copy_skill(source, nested_destination)
            self.assertTrue(source.is_dir())

    def test_copy_skill_rejects_a_dangling_destination_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = self.make_source(root)
            install_root = root / "installed"
            install_root.mkdir()
            destination = install_root / install_skill.SKILL_NAME
            missing_target = root / "missing-target"
            try:
                os.symlink(missing_target, destination, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("directory symlinks are unavailable on this platform")

            with self.assertRaisesRegex(ValueError, "symlink/junction"):
                install_skill.copy_skill(source, destination)
            self.assertFalse(missing_target.exists())

    def test_openclaw_workspace_supports_modern_default_and_json5(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            home = Path(temporary_directory)
            modern_root = home / ".openclaw"
            modern_root.mkdir()
            with mock.patch.object(Path, "home", return_value=home):
                self.assertEqual(
                    (modern_root / "workspace").resolve(),
                    install_skill.openclaw_workspace(None),
                )

            custom = home / "自定义工作区"
            config = modern_root / "openclaw.json"
            config.write_text(
                "{\n  agents: { defaults: { workspace: "
                + repr(str(custom))
                + ", }, },\n}\n",
                encoding="utf-8",
            )
            with mock.patch.object(Path, "home", return_value=home):
                self.assertEqual(custom.resolve(), install_skill.openclaw_workspace(None))

    def test_version_parser_accepts_release_suffixes_without_packaging(self) -> None:
        self.assertEqual((9, 1, 0), install_skill.parse_version_prefix("9.1.0"))
        self.assertEqual((12, 0, 0), install_skill.parse_version_prefix("12.0.0.post1"))
        self.assertEqual((10, 2, 0), install_skill.parse_version_prefix("10.2"))
        self.assertIsNone(install_skill.parse_version_prefix("unknown"))

    def test_installer_rejects_old_pillow_with_actionable_error(self) -> None:
        old_pillow = types.ModuleType("PIL")
        old_pillow.__version__ = "8.4.0"
        stderr = io.StringIO()
        with mock.patch.dict(sys.modules, {"PIL": old_pillow}), mock.patch.object(
            sys, "argv", ["install_skill.py"]
        ), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                install_skill.main()

        self.assertEqual(2, raised.exception.code)
        self.assertIn("Pillow 9.1 or newer", stderr.getvalue())
        self.assertIn("8.4.0", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
