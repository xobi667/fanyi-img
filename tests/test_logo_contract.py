from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
PREFLIGHT = SCRIPTS_DIR / "preflight_images.py"
UPDATE = SCRIPTS_DIR / "update_manifest.py"
APPLY_LOGO = SCRIPTS_DIR / "apply_logo.py"
RESAMPLE_IMAGE = SCRIPTS_DIR / "resample_image.py"
COMPOSE_LOCALIZATION = SCRIPTS_DIR / "compose_localization.py"
VERIFY = SCRIPTS_DIR / "verify_manifest.py"


class LogoManifestContractTests(unittest.TestCase):
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

    def manifest_from_preflight(self, *arguments: object) -> tuple[Path, dict[str, object]]:
        completed = self.run_cli(PREFLIGHT, *arguments)
        manifests = [
            Path(line.removeprefix("manifest="))
            for line in completed.stdout.splitlines()
            if line.startswith("manifest=")
        ]
        self.assertEqual(1, len(manifests), completed.stdout)
        manifest_path = manifests[0]
        return manifest_path, json.loads(manifest_path.read_text(encoding="utf-8"))

    def make_logo(self) -> Path:
        logo = self.input_dir / "logo.png"
        image = Image.new("RGBA", (220, 90), (0, 0, 0, 0))
        image.paste((220, 120, 20, 255), (10, 10, 210, 80))
        image.save(logo)
        return logo

    @staticmethod
    def write_json(path: Path, value: object) -> None:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def build_regenerate_pilot(
        self,
        *,
        moved: bool = True,
        anchor_case: str = "valid",
        delete_unrelated_product: bool = False,
        register_base: bool = True,
        freeze_plan: bool = True,
        freeze_geometry: bool = True,
        freeze_reference: bool = True,
        declare_conflicts: bool = True,
    ) -> dict[str, object]:
        target = self.input_dir / "target.png"
        source = Image.new("RGB", (400, 400), (236, 232, 220))
        source_draw = ImageDraw.Draw(source)
        source_draw.rectangle((4, 4, 60, 24), fill=(180, 35, 35))
        if delete_unrelated_product:
            source_draw.rounded_rectangle(
                (300, 270, 380, 380),
                radius=8,
                fill=(25, 150, 70),
            )
            source_draw.rectangle((315, 290, 365, 360), outline=(245, 245, 235), width=4)
        source.save(target)
        logo = self.make_logo()

        manifest_path, manifest = self.manifest_from_preflight(
            "--input", target,
            "--mode", "edit",
            "--operation", "添加 Logo 并重排冲突模块",
            "--ratio", "1:1",
            "--workers", 1,
            "--logo", logo,
            "--output-root", self.output_root,
            "--task-name", "logo-regenerate",
        )
        item = manifest["items"][0]
        work_dir = manifest_path.parent / "work"
        conflict_reference = work_dir / "conflict-reference.png"
        source.save(conflict_reference)
        if register_base:
            self.run_cli(
                UPDATE,
                "--manifest", manifest_path,
                "--task-id", item["task_id"],
                "--worker-id", item["worker_id"],
                "--status", "pending",
                "--attempts", 1,
                "--base-output", conflict_reference,
            )
        geometry_path = work_dir / "logo_geometry.json"
        output = Path(str(item["output"]))
        self.run_cli(
            APPLY_LOGO,
            "--input", conflict_reference,
            "--output", output,
            "--logo", logo,
            "--dry-run",
            "--geometry-json", geometry_path,
        )
        geometry = json.loads(geometry_path.read_text(encoding="utf-8"))["items"][0]
        right_start, right_end = geometry["right_module_start_range"]
        safe_right = geometry["safe_zone"][2]
        if anchor_case == "valid":
            anchor_left = right_start
        elif anchor_case == "out_of_range":
            anchor_left = right_end + 1
        elif anchor_case == "safe_zone":
            anchor_left = safe_right - 1
        else:
            self.fail(f"unknown anchor case: {anchor_case}")
        prepared_bbox = [anchor_left, 4, anchor_left + 56, 24]

        prepared = work_dir / "prepared.png"
        prepared_image = source.copy()
        if moved:
            prepared_draw = ImageDraw.Draw(prepared_image)
            prepared_draw.rectangle((4, 4, 60, 24), fill=(236, 232, 220))
            prepared_draw.rectangle(tuple(prepared_bbox), fill=(180, 35, 35))
            if delete_unrelated_product:
                prepared_draw.rectangle((300, 270, 380, 380), fill=(236, 232, 220))
        prepared_image.save(prepared)
        self.run_cli(
            APPLY_LOGO,
            "--input", prepared,
            "--output", output,
            "--logo", logo,
            "--safe-zone-approved",
        )

        anchors = [{
            "module_id": "module-01",
            "placement": "right",
            "prepared_bbox": prepared_bbox,
        }]
        plan = {
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
                "family_id": "family-01",
                "visible_bbox": geometry["visible_bbox"],
                "safe_zone": geometry["safe_zone"],
                "modules": [{
                    "id": "module-01",
                    "type": "text",
                    "bbox": [4, 4, 60, 24],
                    "members": ["headline"],
                }],
                "conflicts": ["module-01"] if declare_conflicts else [],
                "decision": "regenerate_for_conflict",
                "module_anchors": anchors,
                "family_reference": item["task_id"],
                "base_approved": True,
                "final_approved": True,
            }],
        }
        plan_path = work_dir / "logo_plan.json"
        self.write_json(plan_path, plan)
        families = {
            "schema_version": 1,
            "families": [{
                "family_id": "family-01",
                "members": [item["task_id"]],
                "pilot_task_id": item["task_id"],
                "requires_pilot": True,
                "pilot_approved": True,
                "pilot_output_sha256": self.sha256(output),
                "lock": {
                    "title_direction": "horizontal",
                    "module_anchor": "right-safe-zone",
                    "type_hierarchy": "title>benefit>detail",
                    "product_scale_range": "0.72-0.78 canvas width",
                    "module_spacing": "one safe-zone gap",
                },
                "variants": [],
            }],
        }
        families_path = work_dir / "layout_families.json"
        self.write_json(families_path, families)
        anchors_path = work_dir / "module_anchors.json"
        self.write_json(anchors_path, anchors)
        freeze_arguments: list[object] = [
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "pending",
            "--base-output", conflict_reference,
            "--logo-decision", "regenerate_for_conflict",
            "--module-anchors-json", anchors_path,
            "--family-id", "family-01",
        ]
        if freeze_reference:
            freeze_arguments.extend(("--conflict-reference-base", conflict_reference))
        if freeze_plan:
            freeze_arguments.extend(("--logo-plan-file", plan_path))
        if freeze_geometry:
            freeze_arguments.extend(("--logo-geometry-json", geometry_path))
        self.run_cli(UPDATE, *freeze_arguments, check=freeze_reference)
        return {
            "manifest_path": manifest_path,
            "manifest": manifest,
            "item": item,
            "conflict_reference": conflict_reference,
            "prepared": prepared,
            "geometry_path": geometry_path,
            "plan_path": plan_path,
            "families_path": families_path,
            "anchors_path": anchors_path,
            "conflict_attempt": 2 if register_base else 1,
        }

    def update_regenerate_pilot(
        self,
        fixture: dict[str, object],
        *,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        item = fixture["item"]
        return self.run_cli(
            UPDATE,
            "--manifest", fixture["manifest_path"],
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "success",
            "--attempts", fixture["conflict_attempt"],
            "--attempt-stage", "logo_conflict",
            "--layout-families-file", fixture["families_path"],
            "--prepared-base", fixture["prepared"],
            check=check,
        )

    def test_valid_regenerate_pilot_with_locked_module_anchor_succeeds(self) -> None:
        fixture = self.build_regenerate_pilot()

        completed = self.update_regenerate_pilot(fixture, check=False)

        self.assertEqual(0, completed.returncode, completed.stderr)
        updated = json.loads(Path(fixture["manifest_path"]).read_text(encoding="utf-8"))
        self.assertEqual("success", updated["items"][0]["status"])
        self.assertEqual("regenerate_for_conflict", updated["items"][0]["logo_decision"])
        self.assertEqual("module-01", updated["items"][0]["module_anchors"][0]["module_id"])
        self.assertTrue(
            updated["items"][0]["logo_relocation_validation"]
            ["outside_relocation_pixel_lock"]["passed"]
        )
        verified = self.run_cli(
            VERIFY,
            "--manifest", fixture["manifest_path"],
            check=False,
        )
        self.assertEqual(0, verified.returncode, verified.stdout)

    def test_logo_conflict_gate_rejects_missing_or_unproven_prerequisites(self) -> None:
        cases = (
            ("accepted base", {"register_base": False}, "accepted no-reference pure-generation base"),
            ("logo plan", {"freeze_plan": False}, "frozen logo_plan"),
            ("geometry", {"freeze_geometry": False}, "frozen logo geometry"),
            ("reference", {"freeze_reference": False}, "conflict_reference_base"),
            ("real conflicts", {"declare_conflicts": False}, "real non-empty conflicts"),
        )
        for label, options, expected_error in cases:
            with self.subTest(label=label):
                fixture = self.build_regenerate_pilot(**options)
                completed = self.update_regenerate_pilot(fixture, check=False)
                self.assertNotEqual(0, completed.returncode)
                self.assertIn(expected_error, completed.stderr)

    def test_logo_conflict_gate_rejects_manifest_without_logo(self) -> None:
        target = self.input_dir / "no-logo-target.png"
        Image.new("RGB", (128, 128), (30, 90, 160)).save(target)
        manifest_path, manifest = self.manifest_from_preflight(
            "--input", target,
            "--mode", "edit",
            "--operation", "edit without Logo",
            "--ratio", "original",
            "--workers", 1,
            "--output-root", self.output_root,
            "--task-name", "no-logo-conflict",
        )
        item = manifest["items"][0]

        rejected = self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "pending",
            "--attempts", 1,
            "--attempt-stage", "logo_conflict",
            check=False,
        )

        self.assertNotEqual(0, rejected.returncode)
        self.assertIn("active Logo", rejected.stderr)

    def test_regenerate_rejects_valid_move_that_deletes_unrelated_product(self) -> None:
        fixture = self.build_regenerate_pilot(delete_unrelated_product=True)

        completed = self.update_regenerate_pilot(fixture, check=False)

        self.assertNotEqual(0, completed.returncode)
        self.assertIn("outside declared relocation ROIs", completed.stderr)

    def test_verify_recomputes_outside_relocation_pixel_lock(self) -> None:
        fixture = self.build_regenerate_pilot()
        accepted = self.update_regenerate_pilot(fixture, check=False)
        self.assertEqual(0, accepted.returncode, accepted.stderr)

        prepared = Path(fixture["prepared"])
        with Image.open(prepared) as raw:
            tampered = raw.convert("RGBA")
        tampered.putpixel((399, 399), (235, 232, 220, 255))
        tampered.save(prepared)

        verified = self.run_cli(
            VERIFY,
            "--manifest", fixture["manifest_path"],
            check=False,
        )

        self.assertNotEqual(0, verified.returncode)
        self.assertIn("outside declared relocation ROIs", verified.stdout)

    def test_regenerate_rejects_pixel_identical_prepared_base(self) -> None:
        fixture = self.build_regenerate_pilot(moved=False)

        completed = self.update_regenerate_pilot(fixture, check=False)

        self.assertNotEqual(0, completed.returncode)
        self.assertIn("source module is still visually present", completed.stderr)

    def test_regenerate_rejects_unrelated_one_pixel_change_with_fake_anchor(self) -> None:
        fixture = self.build_regenerate_pilot(moved=False)
        prepared = Path(fixture["prepared"])
        with Image.open(prepared) as raw:
            image = raw.convert("RGB")
        image.putpixel((399, 399), (235, 232, 220))
        image.save(prepared)
        manifest = fixture["manifest"]
        item = fixture["item"]
        output = Path(str(item["output"]))
        logo = Path(str(manifest["logo"]["source"]))
        self.run_cli(
            APPLY_LOGO,
            "--input", prepared,
            "--output", output,
            "--logo", logo,
            "--safe-zone-approved",
            "--overwrite",
        )
        families_path = Path(fixture["families_path"])
        families = json.loads(families_path.read_text(encoding="utf-8"))
        families["families"][0]["pilot_output_sha256"] = self.sha256(output)
        self.write_json(families_path, families)

        completed = self.update_regenerate_pilot(fixture, check=False)

        self.assertNotEqual(0, completed.returncode)
        self.assertIn("source module is still visually present", completed.stderr)
        self.assertIn("prepared_bbox does not contain the corresponding module", completed.stderr)

    def test_regenerate_rejects_module_anchor_outside_locked_start_range(self) -> None:
        fixture = self.build_regenerate_pilot(anchor_case="out_of_range")

        completed = self.update_regenerate_pilot(fixture, check=False)

        self.assertNotEqual(0, completed.returncode)
        self.assertIn("outside the locked right start range", completed.stderr)

    def test_regenerate_rejects_module_anchor_intersecting_safe_zone(self) -> None:
        fixture = self.build_regenerate_pilot(anchor_case="safe_zone")

        completed = self.update_regenerate_pilot(fixture, check=False)

        self.assertNotEqual(0, completed.returncode)
        self.assertIn("prepared_bbox intersects safe_zone", completed.stderr)

    def test_generate_with_logo_accepts_empty_source_happy_path(self) -> None:
        logo = self.make_logo()
        manifest_path, manifest = self.manifest_from_preflight(
            "--mode", "generate",
            "--operation", "生成商品图并添加 Logo",
            "--ratio", "400x400",
            "--variants", 1,
            "--workers", 1,
            "--logo", logo,
            "--output-root", self.output_root,
            "--task-name", "generate-logo",
        )
        item = manifest["items"][0]
        self.assertEqual("", item["source"])
        work_dir = manifest_path.parent / "work"
        prepared = work_dir / "prepared.png"
        Image.new("RGB", (400, 400), (50, 130, 210)).save(prepared)
        output = Path(str(item["output"]))
        geometry_path = work_dir / "logo_geometry.json"
        self.run_cli(
            APPLY_LOGO,
            "--input", prepared,
            "--output", output,
            "--logo", logo,
            "--dry-run",
            "--geometry-json", geometry_path,
        )
        self.run_cli(
            APPLY_LOGO,
            "--input", prepared,
            "--output", output,
            "--logo", logo,
            "--safe-zone-approved",
        )
        geometry = json.loads(geometry_path.read_text(encoding="utf-8"))["items"][0]
        plan = {
            "schema_version": 1,
            "logo": {
                "source": str(logo.resolve()),
                "sha256": manifest["logo"]["source_sha256"],
                "reference_short_side": 4000,
                "reference_box": [1036, 309],
            },
            "items": [{
                "task_id": item["task_id"],
                "source": "",
                "final_size": geometry["canvas"],
                "family_id": "ungrouped",
                "visible_bbox": geometry["visible_bbox"],
                "safe_zone": geometry["safe_zone"],
                "modules": [],
                "conflicts": [],
                "decision": "direct_overlay",
                "module_anchors": [],
                "family_reference": item["task_id"],
                "base_approved": True,
                "final_approved": True,
            }],
        }
        plan_path = work_dir / "logo_plan.json"
        self.write_json(plan_path, plan)

        self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "pending",
            "--attempts", 1,
            "--base-output", prepared,
        )
        self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "pending",
            "--logo-plan-file", plan_path,
            "--logo-decision", "direct_overlay",
            "--logo-geometry-json", geometry_path,
            "--prepared-base", prepared,
            "--family-id", "ungrouped",
        )
        forbidden_conflict = self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "pending",
            "--attempts", 2,
            "--attempt-stage", "logo_conflict",
            check=False,
        )
        self.assertNotEqual(0, forbidden_conflict.returncode)
        self.assertIn("regenerate_for_conflict", forbidden_conflict.stderr)

        completed = self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "success",
            "--attempts", 2,
            check=False,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        updated = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual("", updated["items"][0]["source"])
        self.assertEqual("success", updated["items"][0]["status"])

    def test_generate_conflict_regeneration_requires_and_uses_reference_base(self) -> None:
        logo = self.make_logo()
        manifest_path, manifest = self.manifest_from_preflight(
            "--mode", "generate",
            "--operation", "生成商品图、避让信息模块并添加 Logo",
            "--ratio", "400x400",
            "--variants", 1,
            "--workers", 1,
            "--logo", logo,
            "--output-root", self.output_root,
            "--task-name", "generate-logo-conflict",
        )
        item = manifest["items"][0]
        work_dir = manifest_path.parent / "work"
        conflict_reference = work_dir / "conflict-reference.png"
        reference_image = Image.new("RGB", (400, 400), (236, 232, 220))
        ImageDraw.Draw(reference_image).rectangle((4, 4, 60, 24), fill=(180, 35, 35))
        reference_image.save(conflict_reference)
        output = Path(str(item["output"]))
        preliminary_path = work_dir / "preliminary-geometry.json"
        self.run_cli(
            APPLY_LOGO,
            "--input", conflict_reference,
            "--output", output,
            "--logo", logo,
            "--dry-run",
            "--geometry-json", preliminary_path,
        )
        preliminary = json.loads(preliminary_path.read_text(encoding="utf-8"))["items"][0]
        anchor_left = preliminary["right_module_start_range"][0]
        prepared_bbox = [anchor_left, 4, anchor_left + 56, 24]
        prepared = work_dir / "prepared.png"
        prepared_image = Image.new("RGB", (400, 400), (236, 232, 220))
        ImageDraw.Draw(prepared_image).rectangle(tuple(prepared_bbox), fill=(180, 35, 35))
        prepared_image.save(prepared)
        geometry_path = work_dir / "logo-geometry.json"
        self.run_cli(
            APPLY_LOGO,
            "--input", prepared,
            "--output", output,
            "--logo", logo,
            "--dry-run",
            "--geometry-json", geometry_path,
        )
        self.run_cli(
            APPLY_LOGO,
            "--input", prepared,
            "--output", output,
            "--logo", logo,
            "--safe-zone-approved",
        )
        geometry = json.loads(geometry_path.read_text(encoding="utf-8"))["items"][0]
        anchors = [{
            "module_id": "module-01",
            "placement": "right",
            "prepared_bbox": prepared_bbox,
        }]
        anchors_path = work_dir / "module-anchors.json"
        self.write_json(anchors_path, anchors)
        logo_plan = {
            "schema_version": 1,
            "logo": {
                "source": str(logo.resolve()),
                "sha256": manifest["logo"]["source_sha256"],
                "reference_short_side": 4000,
                "reference_box": [1036, 309],
            },
            "items": [{
                "task_id": item["task_id"],
                "source": "",
                "final_size": geometry["canvas"],
                "family_id": "ungrouped",
                "visible_bbox": geometry["visible_bbox"],
                "safe_zone": geometry["safe_zone"],
                "modules": [{"id": "module-01", "type": "badge", "bbox": [4, 4, 60, 24]}],
                "conflicts": ["module-01"],
                "decision": "regenerate_for_conflict",
                "module_anchors": anchors,
                "family_reference": item["task_id"],
                "base_approved": True,
                "final_approved": True,
            }],
        }
        logo_plan_path = work_dir / "logo-plan.json"
        self.write_json(logo_plan_path, logo_plan)

        self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "pending",
            "--attempts", 1,
            "--base-output", conflict_reference,
        )
        self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "pending",
            "--base-output", conflict_reference,
            "--conflict-reference-base", conflict_reference,
            "--logo-plan-file", logo_plan_path,
            "--logo-decision", "regenerate_for_conflict",
            "--logo-geometry-json", preliminary_path,
            "--module-anchors-json", anchors_path,
            "--family-id", "ungrouped",
        )

        completed = self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "success",
            "--attempts", 2,
            "--attempt-stage", "logo_conflict",
            "--prepared-base", prepared,
            check=False,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        updated = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertTrue(updated["items"][0]["logo_relocation_validation"]["passed"])

    def test_localization_logo_conflict_uses_frozen_pre_conflict_reference(self) -> None:
        target = self.input_dir / "target.png"
        source_image = Image.new("RGB", (400, 400), (236, 232, 220))
        source_draw = ImageDraw.Draw(source_image)
        source_draw.rectangle((4, 4, 60, 24), fill=(180, 35, 35))
        source_draw.rectangle((250, 20, 310, 40), outline=(25, 25, 25), width=2)
        source_image.save(target)
        logo = self.make_logo()
        manifest_path, manifest = self.manifest_from_preflight(
            "--input", target,
            "--mode", "localization",
            "--operation", "仅翻译并添加 Logo",
            "--ratio", "original",
            "--target-language", "Indonesian",
            "--workers", 1,
            "--logo", logo,
            "--output-root", self.output_root,
            "--task-name", "localization-logo-conflict",
        )
        item = manifest["items"][0]
        work_dir = manifest_path.parent / "work"
        localized = work_dir / "localized-base.png"
        source_image.save(localized)
        localization_plan = {
            "task_id": item["task_id"],
            "mode": "pure_generation_localization",
            "source": item["source"],
            "source_sha256": item["source_sha256"],
            "source_size": [400, 400],
            "target_language": "Indonesian",
            "output_ratio": "original",
            "target_size": None,
            "size_resample": {"required": False, "method": None},
            "ratio_adaptation": {"required": False, "allowed_changes": []},
            "text_blocks": [{
                "id": "text-01",
                "source_bbox": [250, 20, 311, 41],
                "target_bbox": [250, 20, 311, 41],
                "source": "SCREEN",
                "translation": "KASA",
                "target_text_source": "translated",
                "requested_target_text": None,
                "role": "label",
                "text_layout_adaptation": {"required": False, "reason": None},
                "protected_non_text_regions": [],
            }],
            "non_text_inventory": [
                {"id": "red-badge", "kind": "element", "scope": "region", "bbox": [4, 4, 61, 25]},
                {"id": "background", "kind": "background_surface", "scope": "canvas", "bbox": None},
            ],
        }
        localization_plan_path = work_dir / "localization-plan.json"
        self.write_json(localization_plan_path, localization_plan)
        self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "pending",
            "--localization-plan-json", localization_plan_path,
        )
        raw_candidate = work_dir / "raw-edit-candidate.png"
        raw_candidate_image = source_image.copy()
        raw_candidate_draw = ImageDraw.Draw(raw_candidate_image)
        raw_candidate_draw.rectangle((258, 27, 300, 30), fill=(25, 25, 25))
        raw_candidate_draw.rectangle((263, 34, 294, 37), fill=(25, 25, 25))
        raw_candidate_image.save(raw_candidate)
        raw_candidate_image.save(localized)
        self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "pending",
            "--attempts", 1,
            "--attempt-stage", "pure_generation",
            "--localized-base", localized,
        )
        conflict_reference = work_dir / "conflict-reference.png"
        self.run_cli(
            RESAMPLE_IMAGE,
            "--input", localized,
            "--output", conflict_reference,
            "--size", "400x400",
            "--output-format", "png",
        )
        preliminary_geometry = work_dir / "preliminary-geometry.json"
        output = Path(str(item["output"]))
        self.run_cli(
            APPLY_LOGO,
            "--input", conflict_reference,
            "--output", output,
            "--logo", logo,
            "--dry-run",
            "--geometry-json", preliminary_geometry,
        )
        preliminary = json.loads(preliminary_geometry.read_text(encoding="utf-8"))["items"][0]
        anchor_left = preliminary["right_module_start_range"][0]
        prepared_bbox = [anchor_left, 4, anchor_left + 56, 24]
        prepared = work_dir / "prepared.png"
        with Image.open(conflict_reference) as raw_conflict_reference:
            prepared_image = raw_conflict_reference.convert("RGB")
        prepared_draw = ImageDraw.Draw(prepared_image)
        prepared_draw.rectangle((4, 4, 60, 24), fill=(236, 232, 220))
        prepared_draw.rectangle(tuple(prepared_bbox), fill=(180, 35, 35))
        prepared_image.save(prepared)
        geometry_path = work_dir / "logo-geometry.json"
        self.run_cli(
            APPLY_LOGO,
            "--input", prepared,
            "--output", output,
            "--logo", logo,
            "--dry-run",
            "--geometry-json", geometry_path,
        )
        self.run_cli(
            APPLY_LOGO,
            "--input", prepared,
            "--output", output,
            "--logo", logo,
            "--safe-zone-approved",
        )
        geometry = json.loads(geometry_path.read_text(encoding="utf-8"))["items"][0]
        anchors = [{
            "module_id": "module-01",
            "placement": "right",
            "prepared_bbox": prepared_bbox,
        }]
        anchors_path = work_dir / "module-anchors.json"
        self.write_json(anchors_path, anchors)
        logo_plan = {
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
                "modules": [{"id": "module-01", "type": "badge", "bbox": [4, 4, 60, 24]}],
                "conflicts": ["module-01"],
                "decision": "regenerate_for_conflict",
                "module_anchors": anchors,
                "family_reference": item["task_id"],
                "base_approved": True,
                "final_approved": True,
            }],
        }
        logo_plan_path = work_dir / "logo-plan.json"
        self.write_json(logo_plan_path, logo_plan)
        freeze_common = [
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "pending",
            "--logo-plan-file", logo_plan_path,
            "--logo-decision", "regenerate_for_conflict",
            "--logo-geometry-json", preliminary_geometry,
            "--module-anchors-json", anchors_path,
            "--family-id", "ungrouped",
        ]
        missing_reference = self.run_cli(UPDATE, *freeze_common, check=False)
        self.assertNotEqual(0, missing_reference.returncode)
        self.assertIn("requires conflict_reference_base", missing_reference.stderr)

        self.run_cli(
            UPDATE,
            *freeze_common,
            "--conflict-reference-base", conflict_reference,
        )
        accepted = self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "success",
            "--attempts", 2,
            "--attempt-stage", "logo_conflict",
            "--prepared-base", prepared,
            check=False,
        )
        self.assertEqual(0, accepted.returncode, accepted.stderr)
        updated = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertTrue(updated["items"][0]["logo_relocation_validation"]["passed"])
        verified = self.run_cli(VERIFY, "--manifest", manifest_path, check=False)
        self.assertEqual(0, verified.returncode, verified.stdout)

        reference_before = conflict_reference.read_bytes()
        protected = self.run_cli(
            VERIFY,
            "--manifest", manifest_path,
            "--write-json", conflict_reference,
            "--overwrite",
            check=False,
        )
        self.assertNotEqual(0, protected.returncode)
        self.assertEqual(reference_before, conflict_reference.read_bytes())

        with Image.open(conflict_reference) as raw:
            tampered = raw.convert("RGB")
        tampered.putpixel((399, 399), (235, 232, 220))
        tampered.save(conflict_reference)
        rejected = self.run_cli(VERIFY, "--manifest", manifest_path, check=False)
        self.assertNotEqual(0, rejected.returncode)
        self.assertIn("conflict_reference_base", rejected.stdout)


if __name__ == "__main__":
    unittest.main()
