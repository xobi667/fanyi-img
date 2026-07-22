from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import normalize_logo  # noqa: E402


class NormalizeConcurrencyTests(unittest.TestCase):
    def test_concurrent_commit_is_snapshot_and_restored_while_lock_is_held(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            task_dir = root / "task"
            metadata_dir = task_dir / ".xobi"
            work_dir = metadata_dir / "work"
            work_dir.mkdir(parents=True)
            source = root / "logo.png"
            output = work_dir / "normalized-logo.png"
            metadata_path = work_dir / "normalization.json"
            manifest_path = metadata_dir / "manifest.json"
            report_path = metadata_dir / "report.md"

            logo = Image.new("RGBA", (80, 40), (0, 0, 0, 0))
            logo.paste((220, 80, 30, 255), (10, 8, 70, 32))
            logo.save(source)
            Image.new("RGBA", (5, 5), (10, 20, 30, 255)).save(output)
            metadata_path.write_text('{"state":"old"}\n', encoding="utf-8")
            report_path.write_text("old report\n", encoding="utf-8")
            source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
            manifest = {
                "schema_version": 3,
                "manifest_id": "xobi-00000000000000000000000000000000",
                "revision": 0,
                "mode": "edit",
                "ratio": "original",
                "task_dir": str(task_dir),
                "items": [],
                "logo": {
                    "enabled": True,
                    "source": str(source.resolve()),
                    "source_sha256": source_digest,
                    "alpha_threshold": 10,
                    "normalized": None,
                    "normalized_sha256": None,
                    "normalization": None,
                },
            }
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            lock_state = {"held": False}
            expected_after_concurrent_commit: dict[str, bytes] = {}

            def concurrent_commit() -> None:
                Image.new("RGBA", (7, 7), (40, 150, 210, 255)).save(output)
                metadata_path.write_text('{"state":"concurrent"}\n', encoding="utf-8")
                concurrent_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                concurrent_manifest["revision"] = 7
                concurrent_manifest["concurrent_marker"] = "committed"
                manifest_path.write_text(
                    json.dumps(concurrent_manifest, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                report_path.write_text("concurrent report\n", encoding="utf-8")
                expected_after_concurrent_commit.update({
                    "output": output.read_bytes(),
                    "metadata": metadata_path.read_bytes(),
                    "manifest": manifest_path.read_bytes(),
                    "report": report_path.read_bytes(),
                })

            class InjectingLock:
                def __init__(self, _path: Path) -> None:
                    pass

                def __enter__(self) -> "InjectingLock":
                    self.acquire()
                    return self

                def acquire(self) -> None:
                    self._entered = True
                    lock_state["held"] = True
                    concurrent_commit()

                def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
                    lock_state["held"] = False

            original_atomic_bytes = normalize_logo.atomic_bytes
            rollback_lock_states: list[bool] = []

            def tracked_atomic_bytes(path: Path, value: bytes) -> None:
                rollback_lock_states.append(lock_state["held"])
                original_atomic_bytes(path, value)

            def fail_report(_path: Path, _manifest: dict[str, object]) -> None:
                self.assertTrue(lock_state["held"])
                raise OSError("forced report failure")

            arguments = [
                "normalize_logo.py",
                "--input",
                str(source),
                "--output",
                str(output),
                "--background",
                "transparent",
                "--metadata-json",
                str(metadata_path),
                "--manifest",
                str(manifest_path),
                "--overwrite",
            ]
            with mock.patch.object(normalize_logo, "FileLock", InjectingLock), mock.patch.object(
                normalize_logo, "atomic_bytes", tracked_atomic_bytes
            ), mock.patch.object(normalize_logo, "write_report", fail_report), mock.patch.object(
                sys, "argv", arguments
            ), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    normalize_logo.main()

            self.assertEqual(2, raised.exception.code)
            self.assertTrue(rollback_lock_states)
            self.assertTrue(all(rollback_lock_states))
            self.assertFalse(lock_state["held"])
            self.assertEqual(expected_after_concurrent_commit["output"], output.read_bytes())
            self.assertEqual(expected_after_concurrent_commit["metadata"], metadata_path.read_bytes())
            self.assertEqual(expected_after_concurrent_commit["manifest"], manifest_path.read_bytes())
            self.assertEqual(expected_after_concurrent_commit["report"], report_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
