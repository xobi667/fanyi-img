from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "create_contact_sheet.py"


class ContactSheetSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.environment = os.environ.copy()
        self.environment["PYTHONIOENCODING"] = "utf-8"
        self.environment["PYTHONUTF8"] = "1"

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def run_cli(self, *arguments: object) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *(str(argument) for argument in arguments)],
            cwd=REPO_ROOT,
            env=self.environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )

    def write_image(self, path: Path, color: str = "red") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (80, 60), color).save(path)

    def test_standard_mode_excludes_case_variant_metadata_and_counts_decodable_images(self) -> None:
        input_dir = self.root / "input"
        self.write_image(input_dir / "good.jpg")
        (input_dir / "broken.jpg").write_bytes(b"not-an-image")
        self.write_image(input_dir / ".XOBI" / "hidden.png", "blue")
        output = input_dir / ".xobi" / "work" / "sheet.jpg"

        completed = self.run_cli("--input", input_dir, "--output", output, "--thumb", 64)

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("images_loaded=1", completed.stdout)
        with Image.open(output) as sheet:
            self.assertEqual("JPEG", sheet.format)

    def test_standard_mode_cannot_overwrite_an_input_even_with_overwrite(self) -> None:
        input_dir = self.root / "input"
        source = input_dir / "source.jpg"
        self.write_image(source)
        before = source.read_bytes()

        completed = self.run_cli("--input", input_dir, "--output", source, "--overwrite")

        self.assertNotEqual(0, completed.returncode)
        self.assertEqual(before, source.read_bytes())

    def test_triptych_mode_cannot_overwrite_final_image(self) -> None:
        task_dir = self.root / "task"
        source = self.root / "source.png"
        final = task_dir / "final.jpg"
        self.write_image(source)
        self.write_image(final, "green")
        manifest = task_dir / ".xobi" / "manifest.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({
            "items": [{
                "task_id": "task-000001",
                "family_id": None,
                "source": str(source),
                "base_output": None,
                "output": str(final),
                "status": "success",
            }]
        }), encoding="utf-8")
        before = final.read_bytes()

        completed = self.run_cli("--manifest", manifest, "--output", final, "--overwrite")

        self.assertNotEqual(0, completed.returncode)
        self.assertEqual(before, final.read_bytes())

    def test_triptych_mode_protects_all_processing_logo_and_plan_artifacts(self) -> None:
        task_dir = self.root / "task"
        source = self.root / "source.jpg"
        base_output = task_dir / ".xobi" / "work" / "base.jpg"
        localized_base = task_dir / ".xobi" / "work" / "localized.jpg"
        conflict_reference_base = task_dir / ".xobi" / "work" / "conflict-reference.jpg"
        prepared_base = task_dir / ".xobi" / "work" / "prepared.jpg"
        final = task_dir / "final.jpg"
        logo_source = self.root / "logo.jpg"
        logo_normalized = task_dir / ".xobi" / "work" / "logo-normalized.jpg"
        logo_plan = task_dir / ".xobi" / "work" / "logo-plan.jpg"
        layout_families = task_dir / ".xobi" / "work" / "families.jpg"
        style_lock = task_dir / ".xobi" / "work" / "style-lock.jpg"
        localization_plan = task_dir / ".xobi" / "work" / "localization-plan.jpg"
        localization_composition = task_dir / ".xobi" / "work" / "localization-composition.jpg"
        raw_edit_candidate = task_dir / ".xobi" / "work" / "raw-edit-candidate.jpg"
        logo_geometry = task_dir / ".xobi" / "work" / "logo-geometry.jpg"
        protected = [
            source,
            base_output,
            localized_base,
            conflict_reference_base,
            prepared_base,
            final,
            logo_source,
            logo_normalized,
            logo_plan,
            layout_families,
            style_lock,
            localization_plan,
            localization_composition,
            raw_edit_candidate,
            logo_geometry,
        ]
        for index, path in enumerate(protected):
            self.write_image(path, "red" if index % 2 else "blue")

        manifest = task_dir / ".xobi" / "manifest.json"
        manifest.write_text(json.dumps({
            "logo": {
                "source": str(logo_source),
                "normalized": str(logo_normalized),
            },
            "logo_plan": {"path": str(logo_plan)},
            "layout_families": {"path": str(layout_families)},
            "style_lock": {"path": str(style_lock)},
            "items": [{
                "task_id": "task-000001",
                "family_id": None,
                "source": str(source),
                "base_output": str(base_output),
                "localized_base": str(localized_base),
                "conflict_reference_base": str(conflict_reference_base),
                "prepared_base": str(prepared_base),
                "output": str(final),
                "localization_plan_registration": {"path": str(localization_plan)},
                "localization_composition": {
                    "artifact_path": str(localization_composition),
                    "record": {"raw_edit_candidate": str(raw_edit_candidate)},
                },
                "logo_geometry": {"artifact_path": str(logo_geometry)},
                "status": "success",
            }],
        }), encoding="utf-8")

        for path in protected:
            with self.subTest(path=path.name):
                before = path.read_bytes()
                completed = self.run_cli("--manifest", manifest, "--output", path, "--overwrite")
                self.assertNotEqual(0, completed.returncode)
                self.assertEqual(before, path.read_bytes())

    def test_max_pixels_is_checked_before_allocating_sheet(self) -> None:
        input_dir = self.root / "input"
        self.write_image(input_dir / "source.jpg")
        output = input_dir / ".xobi" / "work" / "sheet.jpg"

        completed = self.run_cli(
            "--input", input_dir,
            "--output", output,
            "--thumb", 320,
            "--max-pixels", 100,
        )

        self.assertNotEqual(0, completed.returncode)
        self.assertIn("exceed max-pixels", completed.stderr)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
