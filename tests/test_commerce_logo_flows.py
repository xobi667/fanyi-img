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
VERIFY = SCRIPTS_DIR / "verify_manifest.py"
APPLY_LOGO = SCRIPTS_DIR / "apply_logo.py"
MAIN_IMAGE_REVIEW = SCRIPTS_DIR / "create_main_image_review.py"


class CommerceLogoFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.output_root = self.root / "tasks"
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

    @staticmethod
    def write_json(path: Path, value: object) -> None:
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def make_logo(self) -> Path:
        logo = self.root / "active-logo.png"
        image = Image.new("RGBA", (220, 90), (0, 0, 0, 0))
        image.paste((224, 96, 24, 255), (10, 10, 210, 80))
        image.save(logo, format="PNG")
        return logo

    def preflight(self, logo: Path) -> tuple[Path, dict[str, object]]:
        completed = self.run_cli(
            PREFLIGHT,
            "--mode", "generate",
            "--operation", "制作完整电商产品主图并添加 Logo",
            "--ratio", "400x400",
            "--variants", 1,
            "--workers", 1,
            "--workflow", "commerce_main_image",
            "--platform-profile", "general-commerce",
            "--visual-direction", "clean premium product presentation",
            "--text-policy", "no_text",
            "--logo", logo,
            "--output-root", self.output_root,
            "--task-name", "commerce-logo",
        )
        manifest_paths = [
            Path(line.split("=", 1)[1])
            for line in completed.stdout.splitlines()
            if line.startswith("manifest=")
        ]
        self.assertEqual(1, len(manifest_paths), completed.stdout)
        manifest_path = manifest_paths[0]
        return manifest_path, json.loads(manifest_path.read_text(encoding="utf-8"))

    def main_image_plan(
        self,
        manifest: dict[str, object],
        item: dict[str, object],
    ) -> dict[str, object]:
        return {
            "schema_version": 1,
            "contract": "commerce-main-image-plan-v1",
            "task_id": item["task_id"],
            "creative_route": "commerce_main_image",
            "platform_profile": "general-commerce",
            "visual_direction": "clean premium product presentation",
            "output_ratio": manifest["ratio"],
            "text_policy": "no_text",
            "exact_text": "",
            "existing_text_inventory": [],
            "product_content_lock": (
                "preserve product identity, count, model, color, parts, proportions, "
                "packaging print, nameplate text, and any existing source Logo"
            ),
            "single_focus": "one unmistakable product focal point",
            "hero_occupancy": {
                "min_fraction": 0.65,
                "max_fraction": 0.85,
                "override_reason": "",
            },
            "safe_margin": {
                "min_short_edge_fraction": 0.05,
                "override_reason": "",
            },
            "information_hierarchy": ["product"],
            "composition": "one dominant centered product with deliberate breathing room",
            "camera_and_scale": "credible eye-level product scale and perspective",
            "lighting_and_shadow": "soft directional studio light with grounded contact shadow",
            "material_response": "category-correct texture, roughness, reflection, and detail scale",
            "background_and_color": "quiet neutral background with controlled contrast and saturation",
            "thumbnail_requirements": [160, 256],
            "forbidden_patterns": [
                "cheap_banner",
                "random_badge",
                "thick_outline",
                "oval_sticker_collage",
                "clutter",
                "fake_3d",
                "oversaturation",
                "invented_claim",
            ],
            "source": item["source"],
            "source_sha256": item["source_sha256"],
            "output": item["output"],
            "operation": manifest["operation"],
        }

    def register_main_image_plan(
        self,
        manifest_path: Path,
        manifest: dict[str, object],
        item: dict[str, object],
    ) -> Path:
        plan_path = manifest_path.parent / "work" / "main-image-plan.json"
        self.write_json(plan_path, self.main_image_plan(manifest, item))
        self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "pending",
            "--main-image-plan-json", plan_path,
        )
        return plan_path

    def render_candidate(self, path: Path, *, conflict: bool, tone: int = 0) -> None:
        background = (236 - tone, 232 - tone, 220 - tone)
        image = Image.new("RGB", (400, 400), background)
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((75, 80, 325, 350), radius=24, fill=(62, 128, 188))
        draw.rounded_rectangle((105, 110, 295, 320), radius=18, fill=(225, 230, 234))
        if conflict:
            draw.rectangle((4, 4, 60, 24), fill=(180, 35, 35))
        image.save(path, format="PNG")

    def record_main_image_candidate(
        self,
        manifest_path: Path,
        item: dict[str, object],
        *,
        attempt: int,
        review_path: Path,
        accepted_base: Path | None = None,
        failure: str | None = None,
    ) -> None:
        arguments: list[object] = [
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "pending",
            "--attempts", attempt,
            "--attempt-stage", "commerce_main_image",
            "--main-image-quality-review-json", review_path,
        ]
        if accepted_base is not None:
            arguments.extend(("--base-output", accepted_base))
        if failure is not None:
            arguments.extend(("--failure-type", "quality", "--error", failure))
        self.run_cli(UPDATE, *arguments)

    def finalized_review(
        self,
        manifest_path: Path,
        item: dict[str, object],
        plan_path: Path,
        *,
        passed: bool,
    ) -> Path:
        output = Path(str(item["output"]))
        prepared = self.run_cli(
            MAIN_IMAGE_REVIEW,
            "prepare",
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--candidate", output,
            "--plan-json", plan_path,
        )
        evidence_dir = Path(next(
            line.split("=", 1)[1]
            for line in prepared.stdout.splitlines()
            if line.startswith("evidence_dir=")
        ))
        assessment_path = evidence_dir / "assessment.json"
        assessment = json.loads(
            (evidence_dir / "assessment-template.json").read_text(encoding="utf-8")
        )
        assessment["scores"].update({key: 4 for key in assessment["scores"]})
        assessment["required_checks"].update(
            {key: True for key in assessment["required_checks"]}
        )
        assessment["hard_rejects"].update(
            {key: False for key in assessment["hard_rejects"]}
        )
        for view in assessment["views"].values():
            view["passed"] = True
            view["notes"] = "reviewed"
        if not passed:
            assessment["scores"]["commercial_polish"] = 2
        assessment["notes"] = (
            "full, 256 and 160 views passed"
            if passed
            else "full view failed commercial polish"
        )
        self.write_json(assessment_path, assessment)
        finalized = self.run_cli(
            MAIN_IMAGE_REVIEW,
            "finalize",
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--candidate", output,
            "--plan-json", plan_path,
            "--evidence-dir", evidence_dir,
            "--assessment-json", assessment_path,
        )
        return Path(next(
            line.split("=", 1)[1]
            for line in finalized.stdout.splitlines()
            if line.startswith("review=")
        ))

    def dry_run_logo(
        self,
        manifest_path: Path,
        item: dict[str, object],
        base: Path,
        logo: Path,
        *,
        name: str = "logo-geometry.json",
    ) -> tuple[Path, dict[str, object]]:
        geometry_path = manifest_path.parent / "work" / name
        self.run_cli(
            APPLY_LOGO,
            "--input", base,
            "--output", item["output"],
            "--logo", logo,
            "--dry-run",
            "--geometry-json", geometry_path,
        )
        geometry = json.loads(geometry_path.read_text(encoding="utf-8"))["items"][0]
        return geometry_path, geometry

    def write_logo_plan(
        self,
        manifest_path: Path,
        manifest: dict[str, object],
        item: dict[str, object],
        geometry: dict[str, object],
        *,
        decision: str,
        anchors: list[dict[str, object]] | None = None,
        family_id: str = "ungrouped",
    ) -> tuple[Path, Path | None]:
        anchors = anchors or []
        conflict = decision == "regenerate_for_conflict"
        plan = {
            "schema_version": 1,
            "logo": {
                "source": manifest["logo"]["source"],
                "sha256": manifest["logo"]["source_sha256"],
                "reference_short_side": 4000,
                "reference_box": [1036, 309],
            },
            "items": [{
                "task_id": item["task_id"],
                "source": item["source"],
                "final_size": geometry["canvas"],
                "family_id": family_id,
                "visible_bbox": geometry["visible_bbox"],
                "safe_zone": geometry["safe_zone"],
                "modules": ([{
                    "id": "module-01",
                    "type": "badge",
                    "bbox": [4, 4, 60, 24],
                    "members": ["badge-artwork"],
                }] if conflict else []),
                "conflicts": ["module-01"] if conflict else [],
                "decision": decision,
                "module_anchors": anchors,
                "family_reference": item["task_id"],
                "base_approved": True,
                "final_approved": True,
            }],
        }
        plan_path = manifest_path.parent / "work" / "logo-plan.json"
        self.write_json(plan_path, plan)
        anchors_path: Path | None = None
        if anchors:
            anchors_path = manifest_path.parent / "work" / "module-anchors.json"
            self.write_json(anchors_path, anchors)
        return plan_path, anchors_path

    def make_prepared_conflict_base(
        self,
        base: Path,
        prepared: Path,
        prepared_bbox: list[int],
    ) -> None:
        with Image.open(base) as raw:
            image = raw.convert("RGB")
        background = image.getpixel((0, 0))
        draw = ImageDraw.Draw(image)
        draw.rectangle((4, 4, 60, 24), fill=background)
        draw.rectangle(tuple(prepared_bbox), fill=(180, 35, 35))
        image.save(prepared, format="PNG")

    def overlay_logo(self, prepared: Path, output: Path, logo: Path) -> None:
        self.run_cli(
            APPLY_LOGO,
            "--input", prepared,
            "--output", output,
            "--logo", logo,
            "--safe-zone-approved",
            "--overwrite",
        )

    def test_commerce_main_image_direct_overlay_completes_without_a_logo_attempt(self) -> None:
        logo = self.make_logo()
        manifest_path, manifest = self.preflight(logo)
        item = manifest["items"][0]
        plan_path = self.register_main_image_plan(manifest_path, manifest, item)
        output = Path(str(item["output"]))
        self.render_candidate(output, conflict=False)
        candidate_review = self.finalized_review(
            manifest_path, item, plan_path, passed=True
        )
        accepted_base = manifest_path.parent / "work" / "accepted-main-image.png"
        accepted_base.write_bytes(output.read_bytes())
        self.record_main_image_candidate(
            manifest_path,
            item,
            attempt=1,
            review_path=candidate_review,
            accepted_base=accepted_base,
        )

        geometry_path, geometry = self.dry_run_logo(
            manifest_path, item, accepted_base, logo
        )
        logo_plan_path, _ = self.write_logo_plan(
            manifest_path,
            manifest,
            item,
            geometry,
            decision="direct_overlay",
        )
        self.overlay_logo(accepted_base, output, logo)
        self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "pending",
            "--logo-plan-file", logo_plan_path,
            "--logo-decision", "direct_overlay",
            "--logo-geometry-json", geometry_path,
            "--prepared-base", accepted_base,
            "--family-id", "ungrouped",
        )
        self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "success",
        )

        verified = self.run_cli(VERIFY, "--manifest", manifest_path)
        self.assertTrue(json.loads(verified.stdout)["valid"])
        stored = json.loads(manifest_path.read_text(encoding="utf-8"))["items"][0]
        self.assertEqual("direct_overlay", stored["logo_decision"])
        self.assertEqual(1, stored["attempts"])
        self.assertEqual(
            ["commerce_main_image"],
            [record["attempt_stage"] for record in stored["attempt_history"]],
        )
        self.assertEqual(self.sha256(accepted_base), stored["attempt_history"][0]["candidate_sha256"])
        self.assertNotEqual(self.sha256(accepted_base), self.sha256(output))
        self.assertIsNone(stored["logo_relocation_validation"])

    def test_commerce_and_logo_conflict_each_receive_three_quality_attempts(self) -> None:
        logo = self.make_logo()
        manifest_path, manifest = self.preflight(logo)
        item = manifest["items"][0]
        plan_path = self.register_main_image_plan(manifest_path, manifest, item)
        output = Path(str(item["output"]))

        for attempt, tone in ((1, 2), (2, 4)):
            self.render_candidate(output, conflict=True, tone=tone)
            failed_review = self.finalized_review(
                manifest_path, item, plan_path, passed=False
            )
            self.record_main_image_candidate(
                manifest_path,
                item,
                attempt=attempt,
                review_path=failed_review,
                failure=f"main-image candidate {attempt} rejected",
            )
        self.render_candidate(output, conflict=True)
        accepted_review = self.finalized_review(
            manifest_path, item, plan_path, passed=True
        )
        accepted_base = manifest_path.parent / "work" / "accepted-conflict-base.png"
        accepted_base.write_bytes(output.read_bytes())
        self.record_main_image_candidate(
            manifest_path,
            item,
            attempt=3,
            review_path=accepted_review,
            accepted_base=accepted_base,
        )

        geometry_path, geometry = self.dry_run_logo(
            manifest_path, item, accepted_base, logo
        )
        anchor_left = int(geometry["right_module_start_range"][0])
        prepared_bbox = [anchor_left, 4, anchor_left + 56, 24]
        anchors = [{
            "module_id": "module-01",
            "placement": "right",
            "prepared_bbox": prepared_bbox,
        }]
        logo_plan_path, anchors_path = self.write_logo_plan(
            manifest_path,
            manifest,
            item,
            geometry,
            decision="regenerate_for_conflict",
            anchors=anchors,
            family_id="family-01",
        )
        self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "pending",
            "--base-output", accepted_base,
            "--conflict-reference-base", accepted_base,
            "--logo-plan-file", logo_plan_path,
            "--logo-decision", "regenerate_for_conflict",
            "--logo-geometry-json", geometry_path,
            "--module-anchors-json", anchors_path,
            "--family-id", "family-01",
        )

        for attempt in (4, 5):
            failed_prepared = (
                manifest_path.parent / "work" / f"failed-logo-conflict-{attempt}.png"
            )
            self.make_prepared_conflict_base(accepted_base, failed_prepared, prepared_bbox)
            with Image.open(failed_prepared) as raw:
                failed_image = raw.convert("RGB")
            ImageDraw.Draw(failed_image).point(
                (prepared_bbox[0], prepared_bbox[1]),
                fill=(180 + attempt, 35, 35),
            )
            failed_image.save(failed_prepared, format="PNG")
            self.overlay_logo(failed_prepared, output, logo)
            self.run_cli(
                UPDATE,
                "--manifest", manifest_path,
                "--task-id", item["task_id"],
                "--worker-id", item["worker_id"],
                "--status", "pending",
                "--attempts", attempt,
                "--attempt-stage", "logo_conflict",
                "--failure-type", "quality",
                "--error", f"logo-conflict candidate {attempt} rejected",
                "--prepared-base", failed_prepared,
            )

        prepared = manifest_path.parent / "work" / "prepared-base.png"
        self.make_prepared_conflict_base(accepted_base, prepared, prepared_bbox)
        self.overlay_logo(prepared, output, logo)
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
        families_path = manifest_path.parent / "work" / "layout-families.json"
        self.write_json(families_path, families)
        self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "success",
            "--attempts", 6,
            "--attempt-stage", "logo_conflict",
            "--prepared-base", prepared,
            "--layout-families-file", families_path,
        )

        verified = self.run_cli(VERIFY, "--manifest", manifest_path)
        result = json.loads(verified.stdout)
        self.assertTrue(result["valid"], result["errors"])
        stored = json.loads(manifest_path.read_text(encoding="utf-8"))["items"][0]
        main_records = [
            record for record in stored["attempt_history"]
            if record["attempt_stage"] == "commerce_main_image"
        ]
        logo_records = [
            record for record in stored["attempt_history"]
            if record["attempt_stage"] == "logo_conflict"
        ]
        self.assertEqual([1, 2, 3], [record["attempt"] for record in main_records])
        self.assertEqual([4, 5, 6], [record["attempt"] for record in logo_records])
        self.assertEqual(3, len(main_records))
        self.assertEqual(3, len(logo_records))
        self.assertTrue(stored["logo_relocation_validation"]["passed"])
        self.assertTrue(
            stored["logo_relocation_validation"]["outside_relocation_pixel_lock"]["passed"]
        )

    def test_main_and_logo_infrastructure_budgets_are_independent(self) -> None:
        logo = self.make_logo()
        manifest_path, manifest = self.preflight(logo)
        item = manifest["items"][0]
        plan_path = self.register_main_image_plan(manifest_path, manifest, item)
        output = Path(str(item["output"]))

        for attempt in range(1, 5):
            self.run_cli(
                UPDATE,
                "--manifest", manifest_path,
                "--task-id", item["task_id"],
                "--worker-id", item["worker_id"],
                "--status", "pending",
                "--attempts", attempt,
                "--attempt-stage", "commerce_main_image",
                "--failure-type", "infrastructure",
                "--error", f"main provider failure {attempt}",
            )
        exhausted_main = self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "pending",
            "--attempts", 5,
            "--attempt-stage", "commerce_main_image",
            "--failure-type", "infrastructure",
            "--error", "fifth main provider failure",
            check=False,
        )
        self.assertNotEqual(0, exhausted_main.returncode)
        self.assertIn("infrastructure attempt budget of 4", exhausted_main.stderr)

        self.render_candidate(output, conflict=True)
        accepted_review = self.finalized_review(
            manifest_path,
            item,
            plan_path,
            passed=True,
        )
        accepted_base = manifest_path.parent / "work" / "accepted-after-infrastructure.png"
        accepted_base.write_bytes(output.read_bytes())
        self.record_main_image_candidate(
            manifest_path,
            item,
            attempt=5,
            review_path=accepted_review,
            accepted_base=accepted_base,
        )
        geometry_path, geometry = self.dry_run_logo(
            manifest_path, item, accepted_base, logo
        )
        anchor_left = int(geometry["right_module_start_range"][0])
        prepared_bbox = [anchor_left, 4, anchor_left + 56, 24]
        anchors = [{
            "module_id": "module-01",
            "placement": "right",
            "prepared_bbox": prepared_bbox,
        }]
        logo_plan_path, anchors_path = self.write_logo_plan(
            manifest_path,
            manifest,
            item,
            geometry,
            decision="regenerate_for_conflict",
            anchors=anchors,
        )
        self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "pending",
            "--base-output", accepted_base,
            "--conflict-reference-base", accepted_base,
            "--logo-plan-file", logo_plan_path,
            "--logo-decision", "regenerate_for_conflict",
            "--logo-geometry-json", geometry_path,
            "--module-anchors-json", anchors_path,
            "--family-id", "ungrouped",
        )

        for attempt in range(6, 10):
            self.run_cli(
                UPDATE,
                "--manifest", manifest_path,
                "--task-id", item["task_id"],
                "--worker-id", item["worker_id"],
                "--status", "pending",
                "--attempts", attempt,
                "--attempt-stage", "logo_conflict",
                "--failure-type", "infrastructure",
                "--error", f"logo provider failure {attempt}",
            )
        exhausted_logo = self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "pending",
            "--attempts", 10,
            "--attempt-stage", "logo_conflict",
            "--failure-type", "infrastructure",
            "--error", "fifth logo provider failure",
            check=False,
        )
        self.assertNotEqual(0, exhausted_logo.returncode)
        self.assertIn("logo_conflict infrastructure attempt budget of 4", exhausted_logo.stderr)

        prepared = manifest_path.parent / "work" / "logo-quality-candidate.png"
        self.make_prepared_conflict_base(accepted_base, prepared, prepared_bbox)
        self.overlay_logo(prepared, output, logo)
        self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "pending",
            "--attempts", 10,
            "--attempt-stage", "logo_conflict",
            "--failure-type", "quality",
            "--error", "first logo quality candidate rejected",
            "--prepared-base", prepared,
        )
        output.unlink()

        verified = self.run_cli(
            VERIFY,
            "--manifest", manifest_path,
            "--allow-pending",
        )
        result = json.loads(verified.stdout)
        self.assertTrue(result["valid"], result["errors"])
        history = json.loads(manifest_path.read_text(encoding="utf-8"))["items"][0][
            "attempt_history"
        ]
        counts = {
            (stage, failure): len([
                record for record in history
                if record["attempt_stage"] == stage and record["failure_type"] == failure
            ])
            for stage, failure in (
                ("commerce_main_image", "infrastructure"),
                ("commerce_main_image", None),
                ("logo_conflict", "infrastructure"),
                ("logo_conflict", "quality"),
            )
        }
        self.assertEqual(4, counts[("commerce_main_image", "infrastructure")])
        self.assertEqual(1, counts[("commerce_main_image", None)])
        self.assertEqual(4, counts[("logo_conflict", "infrastructure")])
        self.assertEqual(1, counts[("logo_conflict", "quality")])


if __name__ == "__main__":
    unittest.main()
