from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import preflight_images  # noqa: E402


class FixedDateTime:
    @classmethod
    def now(cls) -> datetime:
        return datetime(2025, 1, 2, 3, 4, 5)


class PreflightConcurrencyTests(unittest.TestCase):
    def test_atomic_task_directory_reservation_is_unique(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            base = Path(temporary_directory) / "tasks"
            with ThreadPoolExecutor(max_workers=8) as executor:
                paths = list(
                    executor.map(
                        lambda _: preflight_images.create_unique_task_dir(base, "same-task"),
                        range(12),
                    )
                )

            self.assertEqual(12, len({path.resolve() for path in paths}))
            self.assertTrue(all(path.is_dir() for path in paths))

    def test_failed_preflight_removes_only_its_owned_reservation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            empty_input = root / "empty-input"
            output_root = root / "tasks"
            empty_input.mkdir()
            reserved_name = "batch-20250102-030405"
            existing = output_root / reserved_name
            existing.mkdir(parents=True)
            sentinel = existing / "keep.txt"
            sentinel.write_text("owned by another preflight\n", encoding="utf-8")
            arguments = [
                "preflight_images.py",
                "--input",
                str(empty_input),
                "--mode",
                "edit",
                "--operation",
                "test",
                "--ratio",
                "1:1",
                "--output-root",
                str(output_root),
                "--task-name",
                "batch",
            ]

            with mock.patch.object(preflight_images, "datetime", FixedDateTime), mock.patch.object(
                sys, "argv", arguments
            ), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    preflight_images.main()

            self.assertEqual(2, raised.exception.code)
            self.assertEqual("owned by another preflight\n", sentinel.read_text(encoding="utf-8"))
            self.assertEqual([reserved_name], sorted(path.name for path in output_root.iterdir()))


if __name__ == "__main__":
    unittest.main()
