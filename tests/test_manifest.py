from __future__ import annotations

import json
import hashlib
import os
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
import zlib
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
PREFLIGHT = SCRIPTS_DIR / "preflight_images.py"
UPDATE = SCRIPTS_DIR / "update_manifest.py"
VERIFY = SCRIPTS_DIR / "verify_manifest.py"
NORMALIZE_LOGO = SCRIPTS_DIR / "normalize_logo.py"
RESAMPLE_IMAGE = SCRIPTS_DIR / "resample_image.py"
COMPOSE_LOCALIZATION = SCRIPTS_DIR / "compose_localization.py"


def png_bytes(red: int, green: int, blue: int, *, width: int = 4, height: int = 4) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        checksum = zlib.crc32(kind)
        checksum = zlib.crc32(payload, checksum) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    pixel = bytes((red, green, blue))
    scanlines = b"".join(b"\x00" + pixel * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(scanlines))
        + chunk(b"IEND", b"")
    )


class ManifestCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.input_dir = self.root / "input"
        self.output_root = self.root / "tasks"
        self.input_dir.mkdir()
        self.environment = os.environ.copy()
        self.environment["PYTHONIOENCODING"] = "utf-8"
        self.environment["PYTHONUTF8"] = "1"

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write_png(self, path: Path, color: tuple[int, int, int]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png_bytes(*color))

    def run_cli(
        self,
        script: Path,
        *arguments: object,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            [sys.executable, str(script), *(str(argument) for argument in arguments)],
            cwd=REPO_ROOT,
            env=self.environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        if check and completed.returncode != 0:
            self.fail(
                f"command failed ({completed.returncode}): {script.name}\n"
                f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
            )
        return completed

    def preflight(self, *extra_arguments: object) -> tuple[Path, dict[str, object]]:
        completed = self.run_cli(
            PREFLIGHT,
            "--input",
            self.input_dir,
            "--mode",
            "edit",
            "--operation",
            "test operation",
            "--ratio",
            "1:1",
            "--workers",
            "4",
            "--output-root",
            self.output_root,
            "--task-name",
            "manifest-test",
            *extra_arguments,
        )
        manifest_lines = [
            line.removeprefix("manifest=")
            for line in completed.stdout.splitlines()
            if line.startswith("manifest=")
        ]
        self.assertEqual(1, len(manifest_lines), completed.stdout)
        manifest_path = Path(manifest_lines[0])
        return manifest_path, json.loads(manifest_path.read_text(encoding="utf-8"))

    def update_command(
        self,
        manifest_path: Path,
        item: dict[str, object],
        *,
        status: str = "success",
    ) -> list[str]:
        return [
            sys.executable,
            str(UPDATE),
            "--manifest",
            str(manifest_path),
            "--task-id",
            str(item["task_id"]),
            "--worker-id",
            str(item["worker_id"]),
            "--status",
            status,
            "--attempts",
            "1",
        ]

    def register_localization_plan(
        self,
        manifest_path: Path,
        item: dict[str, object],
        plan_path: Path,
    ) -> subprocess.CompletedProcess[str]:
        return self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "pending",
            "--localization-plan-json", plan_path,
        )

    def compose_localization_artifact(
        self,
        manifest_path: Path,
        item: dict[str, object],
        plan_path: Path,
        candidate_source: Path,
        localized_output: Path,
    ) -> Path:
        work_dir = manifest_path.parent / "work"
        raw_candidate = work_dir / f"{item['task_id']}-raw-edit-candidate.png"
        provenance = work_dir / f"{item['task_id']}-localization-composition.json"
        raw_candidate.write_bytes(candidate_source.read_bytes())
        self.run_cli(
            COMPOSE_LOCALIZATION,
            "--source", item["source"],
            "--candidate", raw_candidate,
            "--output", localized_output,
            "--plan", plan_path,
            "--provenance-json", provenance,
            "--overwrite",
        )
        return provenance

    def test_colliding_stems_keep_png_name_and_allocate_unique_outputs(self) -> None:
        self.write_png(self.input_dir / "foo.jpg", (220, 30, 30))
        self.write_png(self.input_dir / "foo.png", (30, 30, 220))

        _, manifest = self.preflight()
        outputs = {
            str(item["relative_path"]): Path(str(item["output"])).name
            for item in manifest["items"]
        }

        self.assertEqual("foo.png", outputs["foo.png"])
        self.assertEqual("foo-jpg.png", outputs["foo.jpg"])
        self.assertEqual(2, len(set(outputs.values())))

    def test_logo_argument_excludes_logo_from_targets(self) -> None:
        self.write_png(self.input_dir / "target.png", (20, 120, 220))
        logo = self.input_dir / "logo.png"
        self.write_png(logo, (240, 180, 20))

        _, manifest = self.preflight("--logo", logo)

        self.assertEqual(["target.png"], [item["relative_path"] for item in manifest["items"]])
        self.assertEqual(str(logo.resolve()), manifest["logo"]["source"])
        self.assertTrue(
            any(
                Path(entry["path"]).resolve() == logo.resolve()
                for entry in manifest["excluded_inputs"]
            )
        )

    def test_near_opaque_logo_normalization_is_registered_with_provenance(self) -> None:
        target = self.input_dir / "target.png"
        logo = self.input_dir / "logo.png"
        Image.new("RGB", (200, 200), (20, 120, 220)).save(target)
        near_opaque = Image.new("RGBA", (160, 100), (255, 255, 255, 254))
        near_opaque.paste((230, 40, 20, 255), (45, 25, 115, 75))
        near_opaque.save(logo)

        manifest_path, manifest = self.preflight("--logo", logo)
        self.assertFalse(manifest["logo"]["fully_opaque"])
        self.assertTrue(manifest["logo"]["opaque_review_required"])
        normalized = manifest_path.parent / "work" / "normalized-logo.png"
        metadata = manifest_path.parent / "work" / "logo-normalization.json"
        threshold_rejected = self.run_cli(
            NORMALIZE_LOGO,
            "--input", logo,
            "--output", normalized,
            "--background", "solid",
            "--alpha-threshold", "200",
            "--metadata-json", metadata,
            "--manifest", manifest_path,
            check=False,
        )
        self.assertNotEqual(0, threshold_rejected.returncode)
        self.assertIn("must match the manifest's locked", threshold_rejected.stderr)
        self.assertFalse(normalized.exists())
        self.assertEqual(0, json.loads(manifest_path.read_text(encoding="utf-8"))["revision"])
        self.run_cli(
            NORMALIZE_LOGO,
            "--input", logo,
            "--output", normalized,
            "--background", "solid",
            "--metadata-json", metadata,
            "--manifest", manifest_path,
        )

        updated = json.loads(manifest_path.read_text(encoding="utf-8"))
        logo_record = updated["logo"]
        provenance = logo_record["normalization"]
        metadata_record = json.loads(metadata.read_text(encoding="utf-8"))
        self.assertEqual(1, updated["revision"])
        self.assertEqual(str(normalized.resolve()), logo_record["normalized"])
        self.assertEqual(logo_record["normalized_sha256"], metadata_record["normalized_sha256"])
        self.assertEqual(manifest["logo"]["source_sha256"], provenance["source_sha256"])
        self.assertEqual(str(logo.resolve()), provenance["source"])
        self.assertEqual(str(normalized.resolve()), provenance["normalized"])
        self.assertLess(provenance["normalized_size"][0], 160)
        verified = self.run_cli(VERIFY, "--manifest", manifest_path, "--allow-pending")
        self.assertEqual(0, verified.returncode)

    def test_psd_is_recorded_as_unsupported(self) -> None:
        self.write_png(self.input_dir / "target.png", (20, 180, 80))
        psd = self.input_dir / "design.psd"
        psd.write_bytes(b"8BPS\x00\x01test-placeholder")

        _, manifest = self.preflight()

        self.assertEqual(["target.png"], [item["relative_path"] for item in manifest["items"]])
        self.assertEqual(1, len(manifest["unsupported_inputs"]))
        self.assertEqual(str(psd.resolve()), manifest["unsupported_inputs"][0]["path"])
        self.assertIn("not supported", manifest["unsupported_inputs"][0]["reason"])

    def test_zip_case_colliding_paths_fail_before_extraction(self) -> None:
        archive = self.root / "collision.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("Folder/Foo.png", png_bytes(200, 20, 20))
            bundle.writestr("folder/foo.PNG", png_bytes(20, 20, 200))

        completed = self.run_cli(
            PREFLIGHT,
            "--input",
            archive,
            "--mode",
            "edit",
            "--operation",
            "test operation",
            "--ratio",
            "1:1",
            "--output-root",
            self.output_root,
            check=False,
        )

        self.assertNotEqual(0, completed.returncode)
        self.assertIn("duplicate/case-colliding paths", completed.stderr)
        self.assertFalse(any(self.output_root.iterdir()))

    def test_four_concurrent_updates_preserve_every_status_and_revision(self) -> None:
        colors = [(210, 20, 20), (20, 210, 20), (20, 20, 210), (210, 160, 20)]
        for index, color in enumerate(colors, start=1):
            self.write_png(self.input_dir / f"source-{index}.png", color)
        manifest_path, manifest = self.preflight()
        for item, color in zip(manifest["items"], colors, strict=True):
            self.write_png(Path(str(item["output"])), color)

        processes = [
            subprocess.Popen(
                self.update_command(manifest_path, item),
                cwd=REPO_ROOT,
                env=self.environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
            for item in manifest["items"]
        ]
        results = [process.communicate(timeout=30) for process in processes]
        for process, (stdout, stderr) in zip(processes, results, strict=True):
            self.assertEqual(0, process.returncode, f"stdout:\n{stdout}\nstderr:\n{stderr}")

        updated = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(4, updated["revision"])
        self.assertEqual(["success"] * 4, [item["status"] for item in updated["items"]])
        self.assertTrue(all(item["output_validation"] for item in updated["items"]))

        task_dir = Path(str(updated["task_dir"]))
        leftovers = [
            path
            for path in task_dir.rglob("*")
            if path.is_file() and ".tmp-" in path.name
        ]
        self.assertEqual([], leftovers)
        self.assertTrue((manifest_path.parent / "manifest.json.lock").is_file())

        verified = self.run_cli(VERIFY, "--manifest", manifest_path)
        verification = json.loads(verified.stdout)
        self.assertTrue(verification["valid"])
        self.assertEqual(4, verification["success"])
        self.assertEqual([], verification["errors"])

    def test_missing_success_output_does_not_change_revision(self) -> None:
        self.write_png(self.input_dir / "source.png", (20, 120, 220))
        manifest_path, manifest = self.preflight()
        before = manifest_path.read_bytes()

        completed = subprocess.run(
            self.update_command(manifest_path, manifest["items"][0]),
            cwd=REPO_ROOT,
            env=self.environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )

        self.assertNotEqual(0, completed.returncode)
        self.assertIn("output file does not exist", completed.stderr)
        self.assertEqual(before, manifest_path.read_bytes())
        unchanged = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(0, unchanged["revision"])
        self.assertEqual("pending", unchanged["items"][0]["status"])

    def test_duplicate_output_content_is_rejected_without_revision_change(self) -> None:
        self.write_png(self.input_dir / "first.png", (20, 120, 220))
        self.write_png(self.input_dir / "second.png", (220, 120, 20))
        manifest_path, manifest = self.preflight()
        duplicate = png_bytes(80, 80, 80)
        for item in manifest["items"]:
            output = Path(str(item["output"]))
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(duplicate)

        first = self.run_cli(
            UPDATE,
            *self.update_command(manifest_path, manifest["items"][0])[2:],
        )
        self.assertEqual(0, first.returncode)
        after_first = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(1, after_first["revision"])

        second = subprocess.run(
            self.update_command(manifest_path, manifest["items"][1]),
            cwd=REPO_ROOT,
            env=self.environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        self.assertNotEqual(0, second.returncode)
        self.assertIn("output content duplicates", second.stderr)

        after_second = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(1, after_second["revision"])
        self.assertEqual(["success", "pending"], [item["status"] for item in after_second["items"]])

    def test_schema_v2_is_read_and_promoted_on_safe_update(self) -> None:
        self.write_png(self.input_dir / "source.png", (20, 120, 220))
        manifest_path, manifest = self.preflight()
        manifest["schema_version"] = 2
        manifest.pop("revision", None)
        manifest.pop("retry_policy", None)
        manifest.pop("localization_policy", None)
        item = manifest["items"][0]
        for key in (
            "output_key",
            "source_sha256",
            "attempt_history",
            "updated_at",
            "output_validation",
            "expected_dimensions",
            "expected_ratio",
        ):
            item.pop(key, None)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.write_png(Path(str(item["output"])), (60, 160, 220))

        completed = self.run_cli(UPDATE, *self.update_command(manifest_path, item)[2:])

        self.assertEqual(0, completed.returncode)
        updated = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(3, updated["schema_version"])
        self.assertEqual(1, updated["revision"])
        self.assertEqual("success", updated["items"][0]["status"])
        self.assertTrue(updated["items"][0]["output_validation"]["sha256"])

    def test_preflight_uses_exif_transposed_source_geometry(self) -> None:
        source = self.input_dir / "rotated.jpg"
        exif = Image.Exif()
        exif[274] = 6
        Image.new("RGB", (120, 80), "orange").save(source, exif=exif)

        _, manifest = self.preflight("--ratio", "original")

        item = manifest["items"][0]
        self.assertEqual((80, 120), (item["width"], item["height"]))
        self.assertEqual([80, 120], item["expected_ratio"])

    def test_zero_ratio_is_rejected_cleanly(self) -> None:
        self.write_png(self.input_dir / "source.png", (20, 120, 220))

        completed = subprocess.run(
            [
                sys.executable,
                str(PREFLIGHT),
                "--input", str(self.input_dir),
                "--mode", "edit",
                "--operation", "test",
                "--ratio", "1:0",
                "--output-root", str(self.output_root),
            ],
            cwd=REPO_ROOT,
            env=self.environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

        self.assertNotEqual(0, completed.returncode)
        self.assertIn("ratio components must be positive", completed.stderr)

    def test_fullwidth_ratio_is_validated_and_unknown_ratio_is_rejected(self) -> None:
        self.write_png(self.input_dir / "source.png", (20, 120, 220))

        _, manifest = self.preflight("--ratio", "1：1")
        self.assertEqual([1_000_000, 1_000_000], manifest["items"][0]["expected_ratio"])

        invalid_root = self.root / "invalid-tasks"
        invalid = self.run_cli(
            PREFLIGHT,
            "--input", self.input_dir,
            "--mode", "edit",
            "--operation", "test",
            "--ratio", "banana",
            "--output-root", invalid_root,
            check=False,
        )
        self.assertNotEqual(0, invalid.returncode)
        self.assertIn("unsupported output ratio", invalid.stderr)
        self.assertFalse(invalid_root.exists())

    def test_output_format_and_transparency_contracts_are_enforced(self) -> None:
        self.write_png(self.input_dir / "source.png", (20, 120, 220))
        manifest_path, manifest = self.preflight(
            "--output-format", "webp",
            "--alpha-policy", "forbidden",
        )
        item = manifest["items"][0]
        self.assertEqual(".webp", Path(str(item["output"])).suffix)
        self.assertEqual("WEBP", item["expected_format"])
        self.assertFalse(item["expected_alpha"])
        self.write_png(Path(str(item["output"])), (60, 160, 220))

        wrong_encoding = self.run_cli(
            UPDATE,
            *self.update_command(manifest_path, item)[2:],
            check=False,
        )
        self.assertNotEqual(0, wrong_encoding.returncode)
        self.assertIn("requires WEBP", wrong_encoding.stderr)
        self.assertEqual(0, json.loads(manifest_path.read_text(encoding="utf-8"))["revision"])

        transparent_input = self.root / "transparent-input"
        transparent_input.mkdir()
        transparent_source = transparent_input / "transparent.png"
        Image.new("RGBA", (4, 4), (255, 0, 0, 0)).save(transparent_source)
        transparent_tasks = self.root / "transparent-tasks"
        transparent_preflight = self.run_cli(
            PREFLIGHT,
            "--input", transparent_input,
            "--mode", "edit",
            "--operation", "test",
            "--ratio", "1:1",
            "--output-root", transparent_tasks,
        )
        transparent_manifest_path = Path(next(
            line.split("=", 1)[1]
            for line in transparent_preflight.stdout.splitlines()
            if line.startswith("manifest=")
        ))
        transparent_manifest = json.loads(transparent_manifest_path.read_text(encoding="utf-8"))
        transparent_item = transparent_manifest["items"][0]
        self.assertTrue(transparent_item["expected_alpha"])
        self.write_png(Path(str(transparent_item["output"])), (10, 20, 30))
        opaque_result = self.run_cli(
            UPDATE,
            *self.update_command(transparent_manifest_path, transparent_item)[2:],
            check=False,
        )
        self.assertNotEqual(0, opaque_result.returncode)
        self.assertIn("transparent pixels", opaque_result.stderr)

        incompatible = self.run_cli(
            PREFLIGHT,
            "--input", self.input_dir,
            "--mode", "edit",
            "--operation", "test",
            "--ratio", "1:1",
            "--output-format", "jpg",
            "--alpha-policy", "required",
            "--output-root", self.root / "incompatible",
            check=False,
        )
        self.assertNotEqual(0, incompatible.returncode)
        self.assertIn("cannot satisfy required transparency", incompatible.stderr)

    def test_output_root_equal_to_input_does_not_exclude_sources(self) -> None:
        self.write_png(self.input_dir / "source.png", (20, 120, 220))

        _, manifest = self.preflight("--output-root", self.input_dir)

        self.assertEqual(["source.png"], [item["relative_path"] for item in manifest["items"]])

    def test_verify_fails_when_source_disappears(self) -> None:
        source = self.input_dir / "source.png"
        self.write_png(source, (20, 120, 220))
        manifest_path, manifest = self.preflight()
        item = manifest["items"][0]
        self.write_png(Path(str(item["output"])), (40, 140, 240))
        self.run_cli(UPDATE, *self.update_command(manifest_path, item)[2:])
        source.unlink()

        verified = self.run_cli(VERIFY, "--manifest", manifest_path, check=False)

        self.assertNotEqual(0, verified.returncode)
        self.assertIn("source file is missing", verified.stdout)

    def test_unvalidated_sidecar_success_cannot_bypass_update(self) -> None:
        self.write_png(self.input_dir / "source.png", (20, 120, 220))
        manifest_path, manifest = self.preflight()
        item = manifest["items"][0]
        state_path = manifest_path.parent / "work" / "task-state" / f"{item['task_id']}.json"
        state_path.write_text(json.dumps({
            "task_id": item["task_id"],
            "worker_id": item["worker_id"],
            "updated_at": datetime.now().astimezone().isoformat(timespec="microseconds"),
            "status": "success",
            "output": item["output"],
            "attempts": 1,
        }), encoding="utf-8")
        before = manifest_path.read_bytes()

        completed = subprocess.run(
            self.update_command(manifest_path, item, status="pending"),
            cwd=REPO_ROOT,
            env=self.environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

        self.assertNotEqual(0, completed.returncode)
        self.assertIn("output file does not exist", completed.stderr)
        self.assertEqual(before, manifest_path.read_bytes())

    def test_future_sidecar_timestamp_is_rejected(self) -> None:
        self.write_png(self.input_dir / "source.png", (20, 120, 220))
        manifest_path, manifest = self.preflight()
        item = manifest["items"][0]
        state_path = manifest_path.parent / "work" / "task-state" / f"{item['task_id']}.json"
        state_path.write_text(json.dumps({
            "task_id": item["task_id"],
            "worker_id": item["worker_id"],
            "updated_at": (datetime.now().astimezone() + timedelta(hours=1)).isoformat(timespec="microseconds"),
            "status": "failed",
            "output": item["output"],
            "attempts": 1,
            "error": "forged",
        }), encoding="utf-8")

        completed = subprocess.run(
            self.update_command(manifest_path, item, status="pending"),
            cwd=REPO_ROOT,
            env=self.environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

        self.assertNotEqual(0, completed.returncode)
        self.assertIn("timestamp is in the future", completed.stderr)
        self.assertEqual("pending", json.loads(manifest_path.read_text(encoding="utf-8"))["items"][0]["status"])

    def test_verify_json_cannot_overwrite_manifest(self) -> None:
        self.write_png(self.input_dir / "source.png", (20, 120, 220))
        manifest_path, _ = self.preflight()
        before = manifest_path.read_bytes()

        completed = self.run_cli(
            VERIFY,
            "--manifest", manifest_path,
            "--allow-pending",
            "--write-json", manifest_path,
            "--overwrite",
            check=False,
        )

        self.assertNotEqual(0, completed.returncode)
        self.assertEqual(before, manifest_path.read_bytes())

    def test_verify_json_cannot_overwrite_task_state_or_layout_files(self) -> None:
        self.write_png(self.input_dir / "source.png", (20, 120, 220))
        manifest_path, manifest = self.preflight()
        item = manifest["items"][0]
        state_path = manifest_path.parent / "work" / "task-state" / f"{item['task_id']}.json"
        state_path.write_text("{\"sentinel\": true}\n", encoding="utf-8")
        before = state_path.read_bytes()

        completed = self.run_cli(
            VERIFY,
            "--manifest", manifest_path,
            "--allow-pending",
            "--write-json", state_path,
            "--overwrite",
            check=False,
        )

        self.assertNotEqual(0, completed.returncode)
        self.assertEqual(before, state_path.read_bytes())

    def test_zip_windows_ads_member_is_not_extracted(self) -> None:
        archive = self.root / "bundle.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("target.png", png_bytes(10, 20, 30))
            bundle.writestr("target.png:metadata.png", png_bytes(40, 50, 60))
        completed = self.run_cli(
            PREFLIGHT,
            "--input", archive,
            "--mode", "edit",
            "--operation", "test",
            "--ratio", "1:1",
            "--output-root", self.output_root,
        )
        manifest_path = Path(next(line.split("=", 1)[1] for line in completed.stdout.splitlines() if line.startswith("manifest=")))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(["target.png"], [item["relative_path"] for item in manifest["items"]])

    def test_tampered_success_cannot_hide_a_new_duplicate(self) -> None:
        self.write_png(self.input_dir / "first.png", (20, 120, 220))
        self.write_png(self.input_dir / "second.png", (220, 120, 20))
        manifest_path, manifest = self.preflight()
        first_item, second_item = manifest["items"]
        self.write_png(Path(str(first_item["output"])), (30, 60, 90))
        self.write_png(Path(str(second_item["output"])), (90, 60, 30))
        self.run_cli(UPDATE, *self.update_command(manifest_path, first_item)[2:])
        self.write_png(Path(str(first_item["output"])), (90, 60, 30))

        completed = subprocess.run(
            self.update_command(manifest_path, second_item),
            cwd=REPO_ROOT,
            env=self.environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

        self.assertNotEqual(0, completed.returncode)
        self.assertIn("changed after validation", completed.stderr)
        updated = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(["success", "pending"], [item["status"] for item in updated["items"]])

    def test_verify_checks_registered_plan_and_logo_hashes(self) -> None:
        self.write_png(self.input_dir / "target.png", (20, 120, 220))
        logo = self.input_dir / "logo.png"
        self.write_png(logo, (220, 120, 20))
        manifest_path, manifest = self.preflight("--logo", logo)
        item = manifest["items"][0]
        plan = manifest_path.parent / "work" / "layout_families.json"
        plan.write_text("{\"families\": []}\n", encoding="utf-8")
        self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "pending",
            "--attempts", "0",
            "--layout-families-file", plan,
        )

        plan.write_text("{\"families\": [\"changed\"]}\n", encoding="utf-8")
        self.write_png(logo, (10, 200, 80))
        verified = self.run_cli(VERIFY, "--manifest", manifest_path, "--allow-pending", check=False)

        self.assertNotEqual(0, verified.returncode)
        result = json.loads(verified.stdout)
        messages = [entry["error"] for entry in result["errors"]]
        self.assertTrue(any("layout_families hash changed" in message for message in messages))
        self.assertTrue(any("Logo source hash changed" in message for message in messages))

    def test_localization_success_requires_matching_semantic_plan(self) -> None:
        source = self.input_dir / "source.png"
        self.write_png(source, (20, 120, 220))
        completed = self.run_cli(
            PREFLIGHT,
            "--input", self.input_dir,
            "--mode", "localization",
            "--operation", "translate only",
            "--ratio", "800x800",
            "--target-language", "Indonesian",
            "--output-root", self.output_root,
        )
        manifest_path = Path(next(
            line.split("=", 1)[1]
            for line in completed.stdout.splitlines()
            if line.startswith("manifest=")
        ))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        item = manifest["items"][0]
        output = Path(str(item["output"]))
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (800, 800), "blue").save(output, format="PNG")

        missing_plan = self.run_cli(
            UPDATE,
            *self.update_command(manifest_path, item)[2:],
            check=False,
        )
        self.assertNotEqual(0, missing_plan.returncode)
        self.assertIn("requires a frozen plan registered before the first attempt", missing_plan.stderr)
        self.assertEqual(0, json.loads(manifest_path.read_text(encoding="utf-8"))["revision"])

        plan_path = manifest_path.parent / "work" / "localization_plan.json"
        localized_base = manifest_path.parent / "work" / "localized_base.png"
        with Image.open(source) as raw_source:
            raw_source.save(localized_base, format="PNG")
        self.run_cli(
            RESAMPLE_IMAGE,
            "--input", localized_base,
            "--output", output,
            "--size", "800x800",
            "--output-format", "png",
            "--overwrite",
        )
        plan = {
            "task_id": item["task_id"],
            "mode": "text_only_reference_edit",
            "source": item["source"],
            "source_sha256": item["source_sha256"],
            "source_size": [item["width"], item["height"]],
            "target_language": "Indonesian",
            "output_ratio": "800x800",
            "target_size": [800, 800],
            "size_resample": {"required": True, "method": "whole_canvas_lanczos"},
            "ratio_adaptation": {"required": False, "allowed_changes": []},
            "text_blocks": [],
            "non_text_inventory": [
                {"id": "background", "kind": "background_surface", "scope": "canvas", "bbox": None},
            ],
            "pure_rebuild_allowed": False,
        }
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        first_plan_on_success = self.run_cli(
            UPDATE,
            *self.update_command(manifest_path, item)[2:],
            "--localization-plan-json", plan_path,
            "--localized-base", localized_base,
            check=False,
        )
        self.assertNotEqual(0, first_plan_on_success.returncode)
        self.assertIn("separate pending update before success", first_plan_on_success.stderr)
        self.register_localization_plan(manifest_path, item, plan_path)
        composition = self.compose_localization_artifact(
            manifest_path,
            item,
            plan_path,
            localized_base,
            localized_base,
        )
        with Image.open(output) as raw_output:
            tampered = raw_output.convert("RGB")
        ImageDraw.Draw(tampered).rectangle((600, 600, 799, 799), fill=(255, 0, 0))
        tampered.save(output, format="PNG")
        arbitrary_final = self.run_cli(
            UPDATE,
            *self.update_command(manifest_path, item)[2:],
            "--localized-base", localized_base,
            "--localization-composition-json", composition,
            check=False,
        )
        self.assertNotEqual(0, arbitrary_final.returncode)
        self.assertIn("not the deterministic whole-canvas resample", arbitrary_final.stderr)
        self.run_cli(
            RESAMPLE_IMAGE,
            "--input", localized_base,
            "--output", output,
            "--size", "800x800",
            "--output-format", "png",
            "--overwrite",
        )
        accepted = self.run_cli(
            UPDATE,
            *self.update_command(manifest_path, item)[2:],
            "--localized-base", localized_base,
            "--localization-composition-json", composition,
        )
        self.assertEqual(0, accepted.returncode)

        second_input = self.root / "second-localization"
        second_input.mkdir()
        second_source = second_input / "source.png"
        self.write_png(second_source, (40, 100, 200))
        second_tasks = self.root / "second-localization-tasks"
        second_preflight = self.run_cli(
            PREFLIGHT,
            "--input", second_input,
            "--mode", "localization",
            "--operation", "translate only",
            "--ratio", "800x800",
            "--target-language", "Indonesian",
            "--output-root", second_tasks,
        )
        second_manifest_path = Path(next(
            line.split("=", 1)[1]
            for line in second_preflight.stdout.splitlines()
            if line.startswith("manifest=")
        ))
        second_manifest = json.loads(second_manifest_path.read_text(encoding="utf-8"))
        second_item = second_manifest["items"][0]
        Image.new("RGB", (800, 800), "green").save(Path(str(second_item["output"])), format="PNG")
        bad_plan = dict(plan)
        bad_plan.update({
            "task_id": second_item["task_id"],
            "source": second_item["source"],
            "source_sha256": second_item["source_sha256"],
            "source_size": [second_item["width"], second_item["height"]],
            "target_size": [999, 999],
            "size_resample": {"required": False, "method": None},
        })
        bad_plan.pop("non_text_inventory")
        bad_plan_path = second_manifest_path.parent / "work" / "localization_plan.json"
        bad_plan_path.write_text(json.dumps(bad_plan, ensure_ascii=False), encoding="utf-8")
        rejected_registration = self.run_cli(
            UPDATE,
            "--manifest", second_manifest_path,
            "--task-id", second_item["task_id"],
            "--worker-id", second_item["worker_id"],
            "--status", "pending",
            "--localization-plan-json", bad_plan_path,
            check=False,
        )
        self.assertNotEqual(0, rejected_registration.returncode)
        self.assertIn("localization_plan is missing fields: non_text_inventory", rejected_registration.stderr)
        self.assertIn("target_size does not match", rejected_registration.stderr)

        valid_plan = dict(bad_plan)
        valid_plan["target_size"] = [800, 800]
        valid_plan["size_resample"] = {"required": True, "method": "whole_canvas_lanczos"}
        valid_plan["non_text_inventory"] = [
            {"id": "background", "kind": "background_surface", "scope": "canvas", "bbox": None},
        ]
        bad_plan_path.write_text(json.dumps(valid_plan, ensure_ascii=False), encoding="utf-8")
        self.register_localization_plan(second_manifest_path, second_item, bad_plan_path)

    def test_localization_visual_guard_rejects_full_frame_redesign(self) -> None:
        source = self.input_dir / "layout.png"
        source_image = Image.new("RGB", (256, 256), (244, 241, 230))
        draw = ImageDraw.Draw(source_image)
        colors = ((173, 92, 37), (205, 165, 89), (111, 145, 91), (185, 112, 98))
        for index, color in enumerate(colors):
            left = 18 + index * 58
            draw.rounded_rectangle((left, 24, left + 50, 220), radius=6, outline=color, width=3)
            draw.rectangle((left + 3, 27, left + 47, 64), fill=color)
            draw.rectangle((left + 10, 82, left + 40, 128), outline=(70, 70, 70), width=3)
            draw.rectangle((left + 8, 144, left + 42, 150), fill=(40, 40, 40))
            draw.rectangle((left + 8, 160, left + 35, 165), fill=(50, 50, 50))
        draw.rectangle((8, 229, 248, 248), outline=(160, 120, 70), width=2)
        source_image.save(source)

        completed = self.run_cli(
            PREFLIGHT,
            "--input", self.input_dir,
            "--mode", "localization",
            "--operation", "translate only",
            "--ratio", "original",
            "--target-language", "Indonesian",
            "--output-root", self.output_root,
        )
        manifest_path = Path(next(
            line.split("=", 1)[1]
            for line in completed.stdout.splitlines()
            if line.startswith("manifest=")
        ))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        item = manifest["items"][0]
        output = Path(str(item["output"]))
        plan = {
            "task_id": item["task_id"],
            "mode": "text_only_reference_edit",
            "source": item["source"],
            "source_sha256": item["source_sha256"],
            "source_size": [256, 256],
            "target_language": "Indonesian",
            "output_ratio": "original",
            "target_size": None,
            "size_resample": {"required": False, "method": None},
            "ratio_adaptation": {"required": False, "allowed_changes": []},
            "text_blocks": [{
                "id": "text-01",
                "source_bbox": [26, 142, 60, 168],
                "target_bbox": [26, 142, 60, 168],
                "source": "SCREEN TYPE",
                "translation": "JENIS KASA",
                "target_text_source": "translated",
                "requested_target_text": None,
                "role": "label",
                "text_layout_adaptation": {"required": False, "reason": None},
                "protected_non_text_regions": [],
            }],
            "non_text_inventory": [
                {"id": "background", "kind": "background_surface", "scope": "canvas", "bbox": None},
            ],
            "pure_rebuild_allowed": False,
        }
        plan_path = manifest_path.parent / "work" / "layout-plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        self.register_localization_plan(manifest_path, item, plan_path)
        revision_after_plan = json.loads(manifest_path.read_text(encoding="utf-8"))["revision"]

        bad = Image.new("RGB", (256, 256), (250, 246, 234))
        bad_draw = ImageDraw.Draw(bad)
        for index, color in enumerate(colors):
            top = 16 + index * 56
            bad_draw.rectangle((18, top, 238, top + 44), fill=color)
        bad.save(output)
        rejected = self.run_cli(
            UPDATE,
            *self.update_command(manifest_path, item)[2:],
            "--localized-base", output,
            check=False,
        )
        self.assertNotEqual(0, rejected.returncode)
        self.assertIn("rejected a full-image redesign", rejected.stderr)
        self.assertEqual(
            revision_after_plan,
            json.loads(manifest_path.read_text(encoding="utf-8"))["revision"],
        )

        accepted_image = source_image.copy()
        accepted_draw = ImageDraw.Draw(accepted_image)
        accepted_draw.rectangle((26, 142, 59, 167), fill=(244, 241, 230))
        accepted_draw.rectangle((28, 148, 58, 153), fill=(40, 40, 40))
        accepted_draw.rectangle((31, 158, 55, 163), fill=(40, 40, 40))
        accepted_image.save(output)
        missing_composition = self.run_cli(
            UPDATE,
            *self.update_command(manifest_path, item)[2:],
            "--localized-base", output,
            check=False,
        )
        self.assertNotEqual(0, missing_composition.returncode)
        self.assertIn("requires frozen composition provenance", missing_composition.stderr)
        composition = self.compose_localization_artifact(
            manifest_path,
            item,
            plan_path,
            output,
            output,
        )
        accepted = self.run_cli(
            UPDATE,
            *self.update_command(manifest_path, item)[2:],
            "--localized-base", output,
            "--localization-composition-json", composition,
        )
        self.assertEqual(0, accepted.returncode)

    def test_text_only_pixel_lock_rejects_non_text_changes_and_mask_bypass(self) -> None:
        source = self.input_dir / "pixel-lock.png"
        source_image = Image.new("RGB", (128, 128), (244, 241, 230))
        source_draw = ImageDraw.Draw(source_image)
        source_draw.rectangle((10, 10, 35, 35), fill=(205, 45, 40))
        source_draw.ellipse((17, 17, 28, 28), fill=(255, 255, 255))
        source_draw.rectangle((12, 60, 115, 115), outline=(40, 90, 130), width=4)
        source_draw.rectangle((70, 10, 117, 39), outline=(90, 70, 40), width=2)
        source_draw.rectangle((75, 17, 110, 21), fill=(30, 30, 30))
        source_draw.rectangle((78, 28, 106, 32), fill=(30, 30, 30))
        source_draw.rectangle((72, 12, 78, 18), fill=(210, 35, 35))
        source_image.save(source)

        completed = self.run_cli(
            PREFLIGHT,
            "--input", self.input_dir,
            "--mode", "localization",
            "--operation", "translate only",
            "--ratio", "original",
            "--target-language", "Indonesian",
            "--output-root", self.output_root,
        )
        manifest_path = Path(next(
            line.split("=", 1)[1]
            for line in completed.stdout.splitlines()
            if line.startswith("manifest=")
        ))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        item = manifest["items"][0]
        output = Path(str(item["output"]))
        plan = {
            "task_id": item["task_id"],
            "mode": "text_only_reference_edit",
            "source": item["source"],
            "source_sha256": item["source_sha256"],
            "source_size": [128, 128],
            "target_language": "Indonesian",
            "output_ratio": "original",
            "target_size": None,
            "size_resample": {"required": False, "method": None},
            "ratio_adaptation": {"required": False, "allowed_changes": []},
            "text_blocks": [{
                "id": "text-01",
                "source_bbox": [70, 10, 118, 40],
                "target_bbox": [70, 10, 118, 40],
                "source": "SCREEN TYPE",
                "translation": "JENIS KASA",
                "target_text_source": "translated",
                "requested_target_text": None,
                "role": "label",
                "text_layout_adaptation": {"required": False, "reason": None},
                "protected_non_text_regions": [{
                    "id": "inline-red-icon",
                    "bbox": [72, 12, 79, 19],
                }],
            }],
            "non_text_inventory": [
                {"id": "red-icon", "kind": "element", "scope": "region", "bbox": [10, 10, 36, 36]},
                {"id": "inline-red-icon", "kind": "element", "scope": "region", "bbox": [72, 12, 79, 19]},
                {"id": "blue-product-frame", "kind": "element", "scope": "region", "bbox": [12, 60, 116, 116]},
                {"id": "background", "kind": "background_surface", "scope": "canvas", "bbox": None},
            ],
            "pure_rebuild_allowed": False,
        }
        plan_path = manifest_path.parent / "work" / "pixel-lock-plan.json"
        full_canvas_plan = json.loads(json.dumps(plan))
        full_canvas_plan["text_blocks"][0]["source_bbox"] = [0, 0, 128, 128]
        full_canvas_plan["text_blocks"][0]["target_bbox"] = [0, 0, 128, 128]
        plan_path.write_text(json.dumps(full_canvas_plan), encoding="utf-8")
        rejected_mask = self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "pending",
            "--localization-plan-json", plan_path,
            check=False,
        )
        self.assertNotEqual(0, rejected_mask.returncode)
        self.assertIn("too large to prove a text-only edit", rejected_mask.stderr)

        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        self.register_localization_plan(manifest_path, item, plan_path)

        zero_attempt_success = self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "success",
            "--localized-base", output,
            check=False,
        )
        self.assertNotEqual(0, zero_attempt_success.returncode)
        self.assertIn("must increment --attempts by exactly one", zero_attempt_success.stderr)

        icon_deleted = source_image.copy()
        ImageDraw.Draw(icon_deleted).rectangle((10, 10, 35, 35), fill=(244, 241, 230))
        icon_deleted.save(output)
        rejected_icon = self.run_cli(
            UPDATE,
            *self.update_command(manifest_path, item)[2:],
            "--localized-base", output,
            check=False,
        )
        self.assertNotEqual(0, rejected_icon.returncode)
        self.assertIn("non-text pixel lock failed", rejected_icon.stderr)

        tinted = Image.blend(source_image, Image.new("RGB", source_image.size, (210, 225, 245)), 0.08)
        tinted.save(output)
        rejected_tint = self.run_cli(
            UPDATE,
            *self.update_command(manifest_path, item)[2:],
            "--localized-base", output,
            check=False,
        )
        self.assertNotEqual(0, rejected_tint.returncode)
        self.assertIn("non-text pixel lock failed", rejected_tint.stderr)

        inside_box_garbage = source_image.copy()
        garbage_draw = ImageDraw.Draw(inside_box_garbage)
        garbage_draw.rectangle((70, 10, 117, 39), fill=(230, 25, 30))
        garbage_draw.ellipse((82, 15, 107, 36), fill=(20, 210, 70))
        inside_box_garbage.save(output)
        garbage_candidate = manifest_path.parent / "work" / "garbage-raw-candidate.png"
        garbage_candidate.write_bytes(output.read_bytes())
        rejected_garbage = self.run_cli(
            COMPOSE_LOCALIZATION,
            "--source", item["source"],
            "--candidate", garbage_candidate,
            "--output", manifest_path.parent / "work" / "garbage-localized-base.png",
            "--plan", plan_path,
            "--provenance-json", manifest_path.parent / "work" / "garbage-composition.json",
            check=False,
        )
        self.assertNotEqual(0, rejected_garbage.returncode)
        self.assertIn("changed too much of its rectangle", rejected_garbage.stderr)

        low_delta_repaint = source_image.copy()
        low_delta_pixels = low_delta_repaint.load()
        for y in range(10, 40):
            for x in range(70, 118):
                pixel = low_delta_pixels[x, y]
                low_delta_pixels[x, y] = tuple(
                    channel - 19 if channel >= 19 else channel + 19
                    for channel in pixel
                )
        low_delta_draw = ImageDraw.Draw(low_delta_repaint)
        low_delta_draw.rectangle((84, 20, 91, 27), fill=(230, 25, 30))
        low_delta_draw.rectangle((86, 22, 89, 25), fill=(20, 210, 70))
        low_delta_candidate = manifest_path.parent / "work" / "low-delta-raw-candidate.png"
        low_delta_repaint.save(low_delta_candidate)
        rejected_low_delta = self.run_cli(
            COMPOSE_LOCALIZATION,
            "--source", item["source"],
            "--candidate", low_delta_candidate,
            "--output", manifest_path.parent / "work" / "low-delta-localized-base.png",
            "--plan", plan_path,
            "--provenance-json", manifest_path.parent / "work" / "low-delta-composition.json",
            check=False,
        )
        self.assertNotEqual(0, rejected_low_delta.returncode)
        self.assertIn("changed too many meaningful pixels inside its editable bbox union", rejected_low_delta.stderr)

        accepted_image = source_image.copy()
        accepted_draw = ImageDraw.Draw(accepted_image)
        accepted_draw.rectangle((70, 10, 117, 39), fill=(244, 241, 230))
        accepted_draw.rectangle((73, 16, 114, 21), fill=(30, 30, 30))
        accepted_draw.rectangle((78, 27, 109, 33), fill=(30, 30, 30))
        accepted_image.save(output)
        composition = self.compose_localization_artifact(
            manifest_path,
            item,
            plan_path,
            output,
            output,
        )
        accepted = self.run_cli(
            UPDATE,
            *self.update_command(manifest_path, item)[2:],
            "--localized-base", output,
            "--localization-composition-json", composition,
        )
        self.assertEqual(0, accepted.returncode)
        with Image.open(output) as accepted_output:
            accepted_pixels = accepted_output.convert("RGBA")
        source_pixels = source_image.convert("RGBA")
        for y in range(12, 19):
            for x in range(72, 79):
                self.assertEqual(source_pixels.getpixel((x, y)), accepted_pixels.getpixel((x, y)))
        verified = self.run_cli(VERIFY, "--manifest", manifest_path)
        self.assertEqual(0, verified.returncode)
        success_snapshot = manifest_path.read_bytes()
        missing_history = json.loads(success_snapshot.decode("utf-8"))
        missing_history["items"][0]["attempt_history"] = []
        manifest_path.write_text(
            json.dumps(missing_history, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        rejected_history = self.run_cli(VERIFY, "--manifest", manifest_path, check=False)
        self.assertNotEqual(0, rejected_history.returncode)
        history_errors = [entry["error"] for entry in json.loads(rejected_history.stdout)["errors"]]
        self.assertTrue(any("record every image call exactly once" in error for error in history_errors))
        manifest_path.write_bytes(success_snapshot)
        manifest_after_success = json.loads(manifest_path.read_text(encoding="utf-8"))
        raw_candidate = Path(
            manifest_after_success["items"][0]["localization_composition"]["record"]["raw_edit_candidate"]
        )
        raw_candidate.write_bytes(raw_candidate.read_bytes() + b"tampered")
        tampered_candidate = self.run_cli(
            VERIFY,
            "--manifest", manifest_path,
            check=False,
        )
        self.assertNotEqual(0, tampered_candidate.returncode)
        candidate_errors = [entry["error"] for entry in json.loads(tampered_candidate.stdout)["errors"]]
        self.assertTrue(any("raw_edit_candidate_sha256" in error for error in candidate_errors))

    def test_text_region_guard_uses_disjoint_bbox_union_not_bounding_envelope(self) -> None:
        source = self.input_dir / "disjoint-text-boxes.png"
        source_image = Image.new("RGB", (128, 128), (244, 241, 230))
        source_image.save(source)
        candidate = self.input_dir / "disjoint-text-boxes-candidate.png"
        candidate_image = source_image.copy()
        candidate_draw = ImageDraw.Draw(candidate_image)
        candidate_draw.rectangle((5, 5, 14, 14), fill=(30, 30, 30))
        candidate_draw.rectangle((100, 100, 109, 109), fill=(30, 30, 30))
        candidate_image.save(candidate)
        plan = {
            "task_id": "task-disjoint-bbox-union",
            "mode": "text_only_reference_edit",
            "source": str(source.resolve()),
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "source_size": [128, 128],
            "target_language": "Indonesian",
            "output_ratio": "original",
            "target_size": None,
            "size_resample": {"required": False, "method": None},
            "ratio_adaptation": {"required": False, "allowed_changes": []},
            "text_blocks": [{
                "id": "text-01",
                "source_bbox": [5, 5, 15, 15],
                "target_bbox": [100, 100, 110, 110],
                "source": "SALE",
                "translation": "DISKON",
                "target_text_source": "translated",
                "requested_target_text": None,
                "role": "label",
                "text_layout_adaptation": {
                    "required": True,
                    "reason": "move translated label",
                    "target_alignment": "left",
                    "writing_direction": "ltr",
                },
                "protected_non_text_regions": [],
            }],
            "non_text_inventory": [
                {"id": "background", "kind": "background_surface", "scope": "canvas", "bbox": None},
            ],
            "pure_rebuild_allowed": False,
        }
        plan_path = self.input_dir / "disjoint-text-boxes-plan.json"
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        rejected = self.run_cli(
            COMPOSE_LOCALIZATION,
            "--source", source,
            "--candidate", candidate,
            "--output", self.output_root / "disjoint-localized-base.png",
            "--plan", plan_path,
            "--provenance-json", self.output_root / "disjoint-composition.json",
            check=False,
        )
        self.assertNotEqual(0, rejected.returncode)
        self.assertIn("changed too many meaningful pixels inside its editable bbox union", rejected.stderr)

    def test_localization_plan_is_frozen_before_attempts(self) -> None:
        source = self.input_dir / "frozen-plan.png"
        source_image = Image.new("RGB", (64, 64), (244, 241, 230))
        ImageDraw.Draw(source_image).rectangle((36, 8, 58, 20), fill=(35, 35, 35))
        source_image.save(source)
        completed = self.run_cli(
            PREFLIGHT,
            "--input", self.input_dir,
            "--mode", "localization",
            "--operation", "translate only",
            "--ratio", "original",
            "--target-language", "Indonesian",
            "--output-root", self.output_root,
        )
        manifest_path = Path(next(
            line.split("=", 1)[1]
            for line in completed.stdout.splitlines()
            if line.startswith("manifest=")
        ))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        item = manifest["items"][0]
        plan = {
            "task_id": item["task_id"],
            "mode": "text_only_reference_edit",
            "source": item["source"],
            "source_sha256": item["source_sha256"],
            "source_size": [64, 64],
            "target_language": "Indonesian",
            "output_ratio": "original",
            "target_size": None,
            "size_resample": {"required": False, "method": None},
            "ratio_adaptation": {"required": False, "allowed_changes": []},
            "text_blocks": [{
                "id": "text-01",
                "source_bbox": [34, 6, 60, 22],
                "target_bbox": [34, 6, 60, 22],
                "source": "SCREEN",
                "translation": "KASA",
                "target_text_source": "translated",
                "requested_target_text": None,
                "role": "label",
                "text_layout_adaptation": {"required": False, "reason": None},
                "protected_non_text_regions": [],
            }],
            "non_text_inventory": [
                {"id": "background", "kind": "background_surface", "scope": "canvas", "bbox": None},
            ],
            "pure_rebuild_allowed": False,
        }
        plan_path = manifest_path.parent / "work" / "frozen-plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        self.register_localization_plan(manifest_path, item, plan_path)
        self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "pending",
            "--attempts", "1",
            "--error", "first reference candidate failed quality review",
            "--failure-type", "quality",
            "--attempt-stage", "reference_edit",
        )

        plan["text_blocks"][0]["source_bbox"] = [0, 0, 32, 32]
        plan["text_blocks"][0]["target_bbox"] = [0, 0, 32, 32]
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        changed_after_attempt = self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "pending",
            "--attempts", "2",
            "--error", "second reference candidate failed quality review",
            "--failure-type", "quality",
            "--attempt-stage", "reference_edit",
            check=False,
        )
        self.assertNotEqual(0, changed_after_attempt.returncode)
        self.assertIn("frozen localization plan artifact hash changed", changed_after_attempt.stderr)
        verified = self.run_cli(VERIFY, "--manifest", manifest_path, "--allow-pending", check=False)
        self.assertNotEqual(0, verified.returncode)
        verify_errors = [entry["error"] for entry in json.loads(verified.stdout)["errors"]]
        self.assertTrue(any("frozen localization plan artifact hash changed" in error for error in verify_errors))

    def test_localization_plan_registration_rejects_symlink(self) -> None:
        source = self.input_dir / "symlink-plan.png"
        self.write_png(source, (20, 120, 220))
        completed = self.run_cli(
            PREFLIGHT,
            "--input", self.input_dir,
            "--mode", "localization",
            "--operation", "translate only",
            "--ratio", "original",
            "--target-language", "Indonesian",
            "--output-root", self.output_root,
        )
        manifest_path = Path(next(
            line.split("=", 1)[1]
            for line in completed.stdout.splitlines()
            if line.startswith("manifest=")
        ))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        item = manifest["items"][0]
        real_plan = manifest_path.parent / "work" / "real-localization.json"
        linked_plan = manifest_path.parent / "work" / "linked-localization.json"
        plan = {
            "task_id": item["task_id"],
            "mode": "text_only_reference_edit",
            "source": item["source"],
            "source_sha256": item["source_sha256"],
            "source_size": [item["width"], item["height"]],
            "target_language": "Indonesian",
            "output_ratio": "original",
            "target_size": None,
            "size_resample": {"required": False, "method": None},
            "ratio_adaptation": {"required": False, "allowed_changes": []},
            "text_blocks": [],
            "non_text_inventory": [
                {"id": "background", "kind": "background_surface", "scope": "canvas", "bbox": None},
            ],
            "pure_rebuild_allowed": False,
        }
        real_plan.write_text(json.dumps(plan), encoding="utf-8")
        try:
            os.symlink(real_plan, linked_plan)
        except OSError as exc:
            self.skipTest(f"symlink creation unavailable: {exc}")
        rejected = self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "pending",
            "--localization-plan-json", linked_plan,
            check=False,
        )
        self.assertNotEqual(0, rejected.returncode)
        self.assertIn("must not traverse a symlink or junction", rejected.stderr)

    def test_compose_localization_copies_only_planned_text_pixels(self) -> None:
        source = self.root / "compose-source.png"
        candidate = self.root / "compose-candidate.png"
        output = self.root / "work" / "localized-base.png"
        provenance = self.root / "work" / "localized-composition.json"
        plan_path = self.root / "work" / "localization-plan.json"
        source_image = Image.new("RGB", (100, 100), (242, 239, 226))
        source_draw = ImageDraw.Draw(source_image)
        source_draw.rectangle((5, 55, 94, 94), fill=(40, 100, 160))
        source_draw.ellipse((10, 60, 35, 85), fill=(220, 60, 50))
        source_draw.rectangle((60, 10, 89, 29), outline=(80, 60, 35), width=2)
        source_image.save(source)
        candidate_image = source_image.resize((200, 200), Image.Resampling.NEAREST)
        candidate_draw = ImageDraw.Draw(candidate_image)
        candidate_draw.rectangle((120, 20, 178, 58), fill=(242, 239, 226))
        candidate_draw.rectangle((126, 28, 170, 38), fill=(20, 20, 20))
        candidate_draw.rectangle((132, 44, 164, 52), fill=(20, 20, 20))
        candidate_image.save(candidate)
        plan = {
            "task_id": "task-000001",
            "mode": "text_only_reference_edit",
            "source": str(source.resolve()),
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "source_size": [100, 100],
            "ratio_adaptation": {"required": False, "allowed_changes": []},
            "text_blocks": [{
                "id": "text-01",
                "source_bbox": [60, 10, 90, 30],
                "target_bbox": [60, 10, 90, 30],
                "protected_non_text_regions": [],
            }],
            "non_text_inventory": [
                {"id": "background", "kind": "background_surface", "scope": "canvas", "bbox": None},
            ],
        }
        plan_path.parent.mkdir(parents=True)
        plan_path.write_text(json.dumps(plan), encoding="utf-8")

        self.run_cli(
            COMPOSE_LOCALIZATION,
            "--source", source,
            "--candidate", candidate,
            "--output", output,
            "--plan", plan_path,
            "--provenance-json", provenance,
        )
        with Image.open(source) as raw_source, Image.open(output) as raw_output:
            source_pixels = raw_source.convert("RGBA")
            output_pixels = raw_output.convert("RGBA")
        changed_inside = 0
        for y in range(100):
            for x in range(100):
                if 60 <= x < 90 and 10 <= y < 30:
                    changed_inside += source_pixels.getpixel((x, y)) != output_pixels.getpixel((x, y))
                else:
                    self.assertEqual(source_pixels.getpixel((x, y)), output_pixels.getpixel((x, y)))
        self.assertGreater(changed_inside, 0)
        record = json.loads(provenance.read_text(encoding="utf-8"))
        self.assertEqual("text-bbox-composite-v1", record["contract"])
        self.assertEqual([200, 200], record["raw_candidate_size"])
        self.assertTrue(record["candidate_resampled_to_source"])
        self.assertEqual(hashlib.sha256(output.read_bytes()).hexdigest(), record["output_sha256"])

    def test_compose_localization_rolls_back_output_when_provenance_write_fails(self) -> None:
        sys.path.insert(0, str(SCRIPTS_DIR))
        try:
            import compose_localization as compose_module
        finally:
            sys.path.pop(0)

        source = self.root / "transaction-source.png"
        candidate = self.root / "transaction-candidate.png"
        output = self.root / "work" / "transaction-output.png"
        provenance = self.root / "work" / "transaction-provenance.json"
        plan_path = self.root / "work" / "transaction-plan.json"
        source_image = Image.new("RGB", (20, 20), (240, 235, 220))
        source_image.save(source)
        candidate_image = source_image.copy()
        candidate_image.putpixel((4, 4), (20, 20, 20))
        candidate_image.save(candidate)
        output.parent.mkdir(parents=True)
        old_output = b"previous-output"
        old_provenance = b"previous-provenance"
        output.write_bytes(old_output)
        provenance.write_bytes(old_provenance)
        plan = {
            "task_id": "task-000001",
            "mode": "text_only_reference_edit",
            "source": str(source.resolve()),
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "source_size": [20, 20],
            "ratio_adaptation": {"required": False, "allowed_changes": []},
            "text_blocks": [{
                "id": "text-01",
                "source_bbox": [4, 4, 8, 8],
                "target_bbox": [4, 4, 8, 8],
                "protected_non_text_regions": [],
            }],
            "non_text_inventory": [
                {"id": "background", "kind": "background_surface", "scope": "canvas", "bbox": None},
            ],
        }
        plan_path.write_text(json.dumps(plan), encoding="utf-8")

        from unittest import mock

        with mock.patch.object(compose_module, "atomic_json", side_effect=OSError("simulated failure")):
            with self.assertRaisesRegex(ValueError, "localized_base was rolled back"):
                compose_module.compose_localization(
                    source,
                    candidate,
                    output,
                    plan_path,
                    provenance,
                    overwrite=True,
                )
        self.assertEqual(old_output, output.read_bytes())
        self.assertEqual(old_provenance, provenance.read_bytes())

    def test_localization_same_size_jpeg_requires_deterministic_encoding(self) -> None:
        source = self.input_dir / "same-size-jpeg.png"
        source_image = Image.new("RGB", (64, 64), (242, 239, 226))
        source_draw = ImageDraw.Draw(source_image)
        source_draw.rectangle((4, 32, 59, 59), fill=(50, 105, 160))
        source_draw.ellipse((8, 36, 25, 53), fill=(210, 55, 45))
        source_draw.rectangle((35, 5, 59, 19), outline=(75, 60, 40), width=2)
        source_image.save(source)
        completed = self.run_cli(
            PREFLIGHT,
            "--input", self.input_dir,
            "--mode", "localization",
            "--operation", "translate only",
            "--ratio", "original",
            "--target-language", "Indonesian",
            "--output-format", "jpg",
            "--output-root", self.output_root,
        )
        manifest_path = Path(next(
            line.split("=", 1)[1]
            for line in completed.stdout.splitlines()
            if line.startswith("manifest=")
        ))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        item = manifest["items"][0]
        output = Path(str(item["output"]))
        localized_base = manifest_path.parent / "work" / "same-size-jpeg-base.png"
        localized = source_image.copy()
        localized_draw = ImageDraw.Draw(localized)
        localized_draw.rectangle((35, 5, 58, 18), fill=(242, 239, 226))
        localized_draw.rectangle((38, 8, 56, 11), fill=(25, 25, 25))
        localized_draw.rectangle((41, 14, 53, 16), fill=(25, 25, 25))
        localized.save(localized_base, format="PNG")
        plan = {
            "task_id": item["task_id"],
            "mode": "text_only_reference_edit",
            "source": item["source"],
            "source_sha256": item["source_sha256"],
            "source_size": [64, 64],
            "target_language": "Indonesian",
            "output_ratio": "original",
            "target_size": None,
            "size_resample": {"required": False, "method": None},
            "ratio_adaptation": {"required": False, "allowed_changes": []},
            "text_blocks": [{
                "id": "text-01",
                "source_bbox": [35, 5, 60, 20],
                "target_bbox": [35, 5, 60, 20],
                "source": "SCREEN",
                "translation": "KASA",
                "target_text_source": "translated",
                "requested_target_text": None,
                "role": "label",
                "text_layout_adaptation": {"required": False, "reason": None},
                "protected_non_text_regions": [],
            }],
            "non_text_inventory": [
                {"id": "blue-panel", "kind": "element", "scope": "region", "bbox": [4, 32, 60, 60]},
                {"id": "red-icon", "kind": "element", "scope": "region", "bbox": [8, 36, 26, 54]},
                {"id": "background", "kind": "background_surface", "scope": "canvas", "bbox": None},
            ],
            "pure_rebuild_allowed": False,
        }
        plan_path = manifest_path.parent / "work" / "same-size-jpeg-plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        self.register_localization_plan(manifest_path, item, plan_path)
        composition = self.compose_localization_artifact(
            manifest_path,
            item,
            plan_path,
            localized_base,
            localized_base,
        )
        self.run_cli(
            RESAMPLE_IMAGE,
            "--input", localized_base,
            "--output", output,
            "--size", "64x64",
            "--output-format", "jpg",
            "--overwrite",
        )
        with Image.open(output) as raw_output:
            second_generation = raw_output.convert("RGB")
        second_generation.save(output, format="JPEG", quality=80)
        rejected = self.run_cli(
            UPDATE,
            *self.update_command(manifest_path, item)[2:],
            "--localized-base", localized_base,
            "--localization-composition-json", composition,
            check=False,
        )
        self.assertNotEqual(0, rejected.returncode)
        self.assertIn("not the deterministic same-size encoding", rejected.stderr)

        self.run_cli(
            RESAMPLE_IMAGE,
            "--input", localized_base,
            "--output", output,
            "--size", "64x64",
            "--output-format", "jpg",
            "--overwrite",
        )
        accepted = self.run_cli(
            UPDATE,
            *self.update_command(manifest_path, item)[2:],
            "--localized-base", localized_base,
            "--localization-composition-json", composition,
        )
        self.assertEqual(0, accepted.returncode)

    def test_pure_rebuild_approval_is_task_scoped_and_failure_gated(self) -> None:
        for index in range(1, 4):
            self.write_png(self.input_dir / f"source-{index}.png", (20 * index, 80, 180))
        completed = self.run_cli(
            PREFLIGHT,
            "--input", self.input_dir,
            "--mode", "localization",
            "--operation", "translate only",
            "--ratio", "original",
            "--target-language", "Indonesian",
            "--output-root", self.output_root,
        )
        manifest_path = Path(next(
            line.split("=", 1)[1]
            for line in completed.stdout.splitlines()
            if line.startswith("manifest=")
        ))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        first, second, third = manifest["items"]

        def plan_for(item: dict[str, object]) -> dict[str, object]:
            return {
                "task_id": item["task_id"],
                "mode": "text_only_reference_edit",
                "source": item["source"],
                "source_sha256": item["source_sha256"],
                "source_size": [item["width"], item["height"]],
                "target_language": "Indonesian",
                "output_ratio": "original",
                "target_size": None,
                "size_resample": {"required": False, "method": None},
                "ratio_adaptation": {"required": False, "allowed_changes": []},
                "text_blocks": [],
                "non_text_inventory": [
                    {"id": "background", "kind": "background_surface", "scope": "canvas", "bbox": None},
                ],
                "pure_rebuild_allowed": False,
            }

        frozen_plan_paths: dict[str, Path] = {}
        for item in (first, second, third):
            plan_path = manifest_path.parent / "work" / f"{item['task_id']}-localization-plan.json"
            plan_path.write_text(json.dumps(plan_for(item), ensure_ascii=False), encoding="utf-8")
            self.register_localization_plan(manifest_path, item, plan_path)
            frozen_plan_paths[str(item["task_id"])] = plan_path
        revision_after_registration = json.loads(manifest_path.read_text(encoding="utf-8"))["revision"]

        early = self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", first["task_id"],
            "--worker-id", first["worker_id"],
            "--status", "pending",
            "--pure-rebuild-approval", "User explicitly approves pure reconstruction for this image.",
            check=False,
        )
        self.assertNotEqual(0, early.returncode)
        self.assertIn("requires three recorded reference-edit quality failures", early.stderr)
        self.assertEqual(
            revision_after_registration,
            json.loads(manifest_path.read_text(encoding="utf-8"))["revision"],
        )

        for attempt in range(1, 4):
            self.run_cli(
                UPDATE,
                "--manifest", manifest_path,
                "--task-id", first["task_id"],
                "--worker-id", first["worker_id"],
                "--status", "pending",
                "--attempts", attempt,
                "--error", f"reference edit quality failure {attempt}",
                "--failure-type", "quality",
                "--attempt-stage", "reference_edit",
            )

        exhausted = self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", first["task_id"],
            "--worker-id", first["worker_id"],
            "--status", "pending",
            "--attempts", 4,
            "--error", "unapproved fourth reference edit",
            "--failure-type", "quality",
            "--attempt-stage", "reference_edit",
            check=False,
        )
        self.assertNotEqual(0, exhausted.returncode)
        self.assertIn("quality attempt budget of 3 is exhausted", exhausted.stderr)

        exhausted_success = self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", first["task_id"],
            "--worker-id", first["worker_id"],
            "--status", "success",
            "--attempts", 4,
            check=False,
        )
        self.assertNotEqual(0, exhausted_success.returncode)
        self.assertIn("reference_edit quality attempt budget of 3 is exhausted", exhausted_success.stderr)

        self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", first["task_id"],
            "--worker-id", first["worker_id"],
            "--status", "pending",
            "--pure-rebuild-approval", "User explicitly approves pure reconstruction for this image.",
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        first, second, third = manifest["items"]
        approval = first["pure_rebuild_approval"]
        self.assertEqual("task", approval["scope"])
        self.assertEqual(manifest["manifest_id"], approval["manifest_id"])
        self.assertEqual(first["task_id"], approval["task_id"])
        self.assertEqual(first["source_sha256"], approval["source_sha256"])
        self.assertFalse(manifest["localization_policy"]["pure_rebuild_allowed"])
        self.assertIsNone(second["pure_rebuild_approval"])
        frozen_digest = hashlib.sha256(
            frozen_plan_paths[str(first["task_id"])].read_bytes()
        ).hexdigest()
        self.assertEqual(frozen_digest, first["localization_plan_registration"]["sha256"])

        approval_snapshot = manifest_path.read_bytes()
        first_failure_id = first["attempt_history"][0]["record_id"]
        tampered_approval = json.loads(approval_snapshot.decode("utf-8"))
        tampered_approval["items"][0]["pure_rebuild_approval"][
            "approved_after_attempt_record_id"
        ] = first_failure_id
        manifest_path.write_text(
            json.dumps(tampered_approval, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        wrong_failure_binding = self.run_cli(
            VERIFY,
            "--manifest", manifest_path,
            "--allow-pending",
            check=False,
        )
        self.assertNotEqual(0, wrong_failure_binding.returncode)
        binding_errors = [entry["error"] for entry in json.loads(wrong_failure_binding.stdout)["errors"]]
        self.assertTrue(any("third reference-edit quality failure" in error for error in binding_errors))
        manifest_path.write_bytes(approval_snapshot)

        for index, item in enumerate((first, second, third), start=1):
            Image.new("RGB", (4, 4), (20 * index + 5, 85, 175)).save(
                Path(str(item["output"])), format="PNG"
            )
        with Image.open(Path(str(third["source"]))) as third_source:
            third_source.save(Path(str(third["output"])), format="PNG")

        first_update = self.update_command(manifest_path, first)
        first_update[-1] = "4"
        invalid_pure_base = manifest_path.parent / "work" / "invalid-pure-base.jpg"
        Image.new("RGB", (8, 8), (25, 90, 175)).save(invalid_pure_base, format="JPEG")
        invalid_pure_result = self.run_cli(
            UPDATE,
            *first_update[2:],
            "--localized-base", invalid_pure_base,
            "--attempt-stage", "pure_rebuild",
            check=False,
        )
        self.assertNotEqual(0, invalid_pure_result.returncode)
        self.assertIn("localized_base must be a lossless PNG", invalid_pure_result.stderr)
        self.assertIn("localized_base must keep the source pixel dimensions", invalid_pure_result.stderr)
        unlabeled_pure_rebuild = self.run_cli(
            UPDATE,
            *first_update[2:],
            "--localized-base", first["output"],
            check=False,
        )
        self.assertNotEqual(0, unlabeled_pure_rebuild.returncode)
        self.assertIn("reference_edit quality attempt budget of 3 is exhausted", unlabeled_pure_rebuild.stderr)
        self.run_cli(
            UPDATE,
            *first_update[2:],
            "--localized-base", first["output"],
            "--attempt-stage", "pure_rebuild",
        )

        second_rejected = self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", second["task_id"],
            "--worker-id", second["worker_id"],
            "--status", "pending",
            "--attempts", "1",
            "--error", "unapproved pure rebuild",
            "--failure-type", "quality",
            "--attempt-stage", "pure_rebuild",
            check=False,
        )
        self.assertNotEqual(0, second_rejected.returncode)
        self.assertIn("pure rebuild attempt is not authorized", second_rejected.stderr)

        third_composition = self.compose_localization_artifact(
            manifest_path,
            third,
            frozen_plan_paths[str(third["task_id"])],
            Path(str(third["output"])),
            Path(str(third["output"])),
        )
        self.run_cli(
            UPDATE,
            *self.update_command(manifest_path, third)[2:],
            "--localized-base", third["output"],
            "--localization-composition-json", third_composition,
        )

        tampered = json.loads(manifest_path.read_text(encoding="utf-8"))
        tampered["items"][1]["pure_rebuild_approval"] = dict(approval)
        tampered["items"][1]["pure_rebuild_approval"]["manifest_id"] = "xobi-" + "0" * 32
        manifest_path.write_text(json.dumps(tampered, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        copied = self.run_cli(VERIFY, "--manifest", manifest_path, "--allow-pending", check=False)
        self.assertNotEqual(0, copied.returncode)
        copied_errors = [entry["error"] for entry in json.loads(copied.stdout)["errors"]]
        self.assertTrue(any("does not match this manifest" in error for error in copied_errors))
        self.assertTrue(any("does not match this task" in error for error in copied_errors))
        self.assertTrue(any("does not match the current source hash" in error for error in copied_errors))

    def test_user_exact_localization_text_must_remain_verbatim(self) -> None:
        source = self.input_dir / "source.png"
        Image.new("RGB", (20, 20), (20, 120, 220)).save(source)
        completed = self.run_cli(
            PREFLIGHT,
            "--input", self.input_dir,
            "--mode", "localization",
            "--operation", "replace with exact supplied copy",
            "--ratio", "original",
            "--target-language", "Indonesian",
            "--output-root", self.output_root,
        )
        manifest_path = Path(next(
            line.split("=", 1)[1]
            for line in completed.stdout.splitlines()
            if line.startswith("manifest=")
        ))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        item = manifest["items"][0]
        output = Path(str(item["output"]))
        with Image.open(source) as raw_source:
            candidate = raw_source.convert("RGB")
        candidate.putpixel((0, 0), (220, 30, 30))
        candidate.save(output, format="PNG")
        plan = {
            "task_id": item["task_id"],
            "mode": "text_only_reference_edit",
            "source": item["source"],
            "source_sha256": item["source_sha256"],
            "source_size": [item["width"], item["height"]],
            "target_language": "Indonesian",
            "output_ratio": "original",
            "target_size": None,
            "size_resample": {"required": False, "method": None},
            "ratio_adaptation": {"required": False, "allowed_changes": []},
            "text_blocks": [{
                "id": "text-01",
                "source_bbox": [0, 0, 2, 2],
                "target_bbox": [0, 0, 2, 2],
                "source": "限时大促销",
                "translation": "Diskon",
                "target_text_source": "user_exact",
                "requested_target_text": "Diskon Besar Waktu Terbatas",
                "role": "heading",
                "text_layout_adaptation": {"required": False, "reason": None},
                "protected_non_text_regions": [],
            }],
            "non_text_inventory": [
                {"id": "background", "kind": "background_surface", "scope": "canvas", "bbox": None},
            ],
            "pure_rebuild_allowed": False,
        }
        plan_path = manifest_path.parent / "work" / "user-exact-plan.json"
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        rejected_registration = self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "pending",
            "--localization-plan-json", plan_path,
            check=False,
        )
        self.assertNotEqual(0, rejected_registration.returncode)
        self.assertIn("preserve user_exact target text verbatim", rejected_registration.stderr)

        plan["text_blocks"][0]["translation"] = plan["text_blocks"][0]["requested_target_text"]
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        self.register_localization_plan(manifest_path, item, plan_path)

        plan["text_blocks"][0]["translation"] = "Diskon"
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        frozen_rejected = self.run_cli(
            UPDATE,
            *self.update_command(manifest_path, item)[2:],
            "--localized-base", output,
            check=False,
        )
        self.assertNotEqual(0, frozen_rejected.returncode)
        self.assertIn("frozen localization plan artifact hash changed", frozen_rejected.stderr)

    def test_logo_success_requires_registered_plan_and_geometry(self) -> None:
        target = self.input_dir / "target.png"
        logo = self.input_dir / "logo.png"
        Image.new("RGB", (400, 400), (20, 120, 220)).save(target)
        logo_image = Image.new("RGBA", (220, 90), (0, 0, 0, 0))
        logo_image.paste((220, 120, 20, 255), (10, 10, 210, 80))
        logo_image.save(logo)
        manifest_path, manifest = self.preflight("--logo", logo)
        item = manifest["items"][0]
        output = Path(str(item["output"]))
        prepared = manifest_path.parent / "work" / "prepared.png"
        prepared.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(target) as source_image:
            source_image.save(prepared)
        geometry_path = manifest_path.parent / "work" / "logo_geometry.json"
        self.run_cli(
            SCRIPTS_DIR / "apply_logo.py",
            "--input", prepared,
            "--output", output,
            "--logo", logo,
            "--dry-run",
            "--geometry-json", geometry_path,
        )
        self.run_cli(
            SCRIPTS_DIR / "apply_logo.py",
            "--input", prepared,
            "--output", output,
            "--logo", logo,
            "--safe-zone-approved",
        )
        wrapper = json.loads(geometry_path.read_text(encoding="utf-8"))
        geometry = wrapper["items"][0]

        rejected = self.run_cli(
            UPDATE,
            *self.update_command(manifest_path, item)[2:],
            check=False,
        )
        self.assertNotEqual(0, rejected.returncode)
        self.assertIn("registered logo_plan", rejected.stderr)
        self.assertEqual(0, json.loads(manifest_path.read_text(encoding="utf-8"))["revision"])

        plan_path = manifest_path.parent / "work" / "logo_plan.json"
        valid_plan = {
            "schema_version": 1,
            "logo": {
                "source": str(logo.resolve()),
                "sha256": manifest["logo"]["source_sha256"],
                "reference_short_side": 4000,
                "reference_box": [1036, 309],
            },
            "items": [{
                "task_id": item["task_id"],
                "source": item["source"],
                "final_size": geometry["canvas"],
                "family_id": "ungrouped",
                "visible_bbox": geometry["visible_bbox"],
                "safe_zone": geometry["safe_zone"],
                "modules": [],
                "conflicts": [],
                "decision": "direct_overlay",
                "family_reference": item["task_id"],
                "base_approved": True,
                "final_approved": True,
            }],
        }
        plan_path.write_text(json.dumps(valid_plan), encoding="utf-8")
        bare_geometry = manifest_path.parent / "work" / "bare_geometry.json"
        bare_geometry.write_text(json.dumps(geometry), encoding="utf-8")
        invalid = self.run_cli(
            UPDATE,
            *self.update_command(manifest_path, item)[2:],
            "--logo-plan-file", plan_path,
            "--logo-decision", "direct_overlay",
            "--logo-geometry-json", bare_geometry,
            "--prepared-base", prepared,
            "--family-id", "ungrouped",
            check=False,
        )
        self.assertNotEqual(0, invalid.returncode)
        self.assertIn("not bare geometry", invalid.stderr)
        self.assertEqual(0, json.loads(manifest_path.read_text(encoding="utf-8"))["revision"])

        conflict_plan = json.loads(json.dumps(valid_plan))
        conflict_plan["items"][0]["modules"] = [{
            "id": "module-covered",
            "type": "text",
            "bbox": geometry["visible_bbox"],
        }]
        plan_path.write_text(json.dumps(conflict_plan), encoding="utf-8")
        hidden_conflict = self.run_cli(
            UPDATE,
            *self.update_command(manifest_path, item)[2:],
            "--logo-plan-file", plan_path,
            "--logo-decision", "direct_overlay",
            "--logo-geometry-json", geometry_path,
            "--prepared-base", prepared,
            "--family-id", "ungrouped",
            check=False,
        )
        self.assertNotEqual(0, hidden_conflict.returncode)
        self.assertIn("conflicts must exactly equal modules intersecting visible_bbox", hidden_conflict.stderr)

        plan_path.write_text(json.dumps(valid_plan), encoding="utf-8")
        with Image.open(output) as raw_output:
            tampered = raw_output.convert("RGBA")
        tampered.putpixel((399, 399), (255, 255, 255, 255))
        tampered.save(output)
        pixel_rejected = self.run_cli(
            UPDATE,
            *self.update_command(manifest_path, item)[2:],
            "--logo-plan-file", plan_path,
            "--logo-decision", "direct_overlay",
            "--logo-geometry-json", geometry_path,
            "--prepared-base", prepared,
            "--family-id", "ungrouped",
            check=False,
        )
        self.assertNotEqual(0, pixel_rejected.returncode)
        self.assertIn("deterministic composite", pixel_rejected.stderr)
        self.run_cli(
            SCRIPTS_DIR / "apply_logo.py",
            "--input", prepared,
            "--output", output,
            "--logo", logo,
            "--safe-zone-approved",
            "--overwrite",
        )

        accepted = self.run_cli(
            UPDATE,
            *self.update_command(manifest_path, item)[2:],
            "--logo-plan-file", plan_path,
            "--logo-decision", "direct_overlay",
            "--logo-geometry-json", geometry_path,
            "--prepared-base", prepared,
            "--family-id", "ungrouped",
        )
        self.assertEqual(0, accepted.returncode)

    def test_schema_v1_is_read_without_traceback_and_requires_success_baseline(self) -> None:
        self.write_png(self.input_dir / "source.png", (20, 120, 220))
        manifest_path, manifest = self.preflight()
        item = manifest["items"][0]
        self.write_png(Path(str(item["output"])), (60, 160, 220))
        self.run_cli(UPDATE, *self.update_command(manifest_path, item)[2:])

        legacy = json.loads(manifest_path.read_text(encoding="utf-8"))
        legacy["schema_version"] = 1
        legacy["items"][0].pop("output_validation", None)
        manifest_path.write_text(json.dumps(legacy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        verified = self.run_cli(VERIFY, "--manifest", manifest_path, check=False)
        self.assertEqual("", verified.stderr)
        self.assertNotEqual(0, verified.returncode)
        result = json.loads(verified.stdout)
        self.assertEqual(3, result["schema_version"])
        self.assertTrue(any("missing output validation baseline" in entry["error"] for entry in result["errors"]))

    def test_verify_reports_each_output_hash_change_once(self) -> None:
        self.write_png(self.input_dir / "source.png", (20, 120, 220))
        manifest_path, manifest = self.preflight()
        item = manifest["items"][0]
        output = Path(str(item["output"]))
        self.write_png(output, (60, 160, 220))
        self.run_cli(UPDATE, *self.update_command(manifest_path, item)[2:])
        self.write_png(output, (220, 60, 160))

        verified = self.run_cli(VERIFY, "--manifest", manifest_path, check=False)
        result = json.loads(verified.stdout)
        hash_errors = [
            entry for entry in result["errors"]
            if "output sha256 changed after success validation" in entry["error"]
        ]
        self.assertEqual(1, len(hash_errors), result["errors"])

    @unittest.skipUnless(os.name == "nt", "Windows read-only replacement semantics")
    def test_update_rolls_back_state_manifest_and_report_on_write_failure(self) -> None:
        self.write_png(self.input_dir / "source.png", (20, 120, 220))
        manifest_path, manifest = self.preflight()
        item = manifest["items"][0]
        self.write_png(Path(str(item["output"])), (60, 160, 220))
        report_path = manifest_path.parent / "report.md"
        state_path = manifest_path.parent / "work" / "task-state" / f"{item['task_id']}.json"
        manifest_before = manifest_path.read_bytes()
        report_before = report_path.read_bytes()

        report_path.chmod(stat.S_IREAD)
        try:
            report_failure = self.run_cli(
                UPDATE,
                *self.update_command(manifest_path, item)[2:],
                check=False,
            )
        finally:
            report_path.chmod(stat.S_IWRITE)
        self.assertNotEqual(0, report_failure.returncode)
        self.assertEqual(manifest_before, manifest_path.read_bytes())
        self.assertEqual(report_before, report_path.read_bytes())
        self.assertFalse(state_path.exists())

        manifest_path.chmod(stat.S_IREAD)
        try:
            manifest_failure = self.run_cli(
                UPDATE,
                *self.update_command(manifest_path, item)[2:],
                check=False,
            )
        finally:
            manifest_path.chmod(stat.S_IWRITE)
        self.assertNotEqual(0, manifest_failure.returncode)
        self.assertEqual(manifest_before, manifest_path.read_bytes())
        self.assertEqual(report_before, report_path.read_bytes())
        self.assertFalse(state_path.exists())

    def test_encrypted_zip_fails_cleanly_without_task_residue(self) -> None:
        archive = self.root / "encrypted.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("target.png", png_bytes(10, 20, 30))
        payload = bytearray(archive.read_bytes())
        for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
            position = 0
            while True:
                position = payload.find(signature, position)
                if position < 0:
                    break
                flags = struct.unpack_from("<H", payload, position + offset)[0]
                struct.pack_into("<H", payload, position + offset, flags | 0x1)
                position += 4
        archive.write_bytes(payload)

        completed = self.run_cli(
            PREFLIGHT,
            "--input", archive,
            "--mode", "edit",
            "--operation", "test",
            "--ratio", "1:1",
            "--output-root", self.output_root,
            check=False,
        )

        self.assertNotEqual(0, completed.returncode)
        self.assertIn("encrypted ZIP members are not supported", completed.stderr)
        self.assertTrue(not self.output_root.exists() or not any(self.output_root.iterdir()))


if __name__ == "__main__":
    unittest.main()
