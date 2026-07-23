from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
PREFLIGHT = SCRIPTS_DIR / "preflight_images.py"
UPDATE = SCRIPTS_DIR / "update_manifest.py"
VERIFY = SCRIPTS_DIR / "verify_manifest.py"
MAIN_IMAGE_REVIEW = SCRIPTS_DIR / "create_main_image_review.py"

class CommerceMainImageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.output_root = self.root / "tasks"
        self.environment = os.environ.copy()
        self.environment["PYTHONIOENCODING"] = "utf-8"
        self.environment["PYTHONUTF8"] = "1"
        self.review_counter = 0

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

    def manifest_from(self, completed: subprocess.CompletedProcess[str]) -> tuple[Path, dict[str, object]]:
        paths = [
            Path(line.split("=", 1)[1])
            for line in completed.stdout.splitlines()
            if line.startswith("manifest=")
        ]
        self.assertEqual(1, len(paths), completed.stdout)
        return paths[0], json.loads(paths[0].read_text(encoding="utf-8"))

    def preflight(
        self,
        *,
        mode: str = "generate",
        operation: str = "制作电商产品主图",
        workflow: bool = True,
        logo: bool = False,
    ) -> tuple[Path, dict[str, object]]:
        arguments: list[object] = [
            "--mode", mode,
            "--operation", operation,
            "--ratio", "1:1" if mode == "generate" else "original",
            "--output-root", self.output_root,
            "--task-name", f"commerce-{mode}",
        ]
        if workflow:
            arguments.extend([
                "--workflow", "commerce_main_image",
                "--platform-profile", "general-commerce",
                "--visual-direction", "clean premium product presentation",
                "--text-policy", "no_text",
            ])
        if mode != "generate":
            source = self.root / f"{mode}-source.png"
            Image.new("RGB", (96, 64), (40, 110, 190)).save(source, format="PNG")
            arguments.extend(["--input", source])
        if mode == "localization":
            arguments.extend(["--target-language", "English"])
        if logo:
            logo_path = self.root / "active-logo.png"
            Image.new("RGBA", (40, 16), (220, 60, 30, 255)).save(logo_path, format="PNG")
            arguments.extend(["--logo", logo_path])
        return self.manifest_from(self.run_cli(PREFLIGHT, *arguments))

    def plan_for(self, manifest: dict[str, object], item: dict[str, object]) -> dict[str, object]:
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
                "preserve product identity, count, model, color, parts, proportions, packaging print, "
                "nameplate text, and any existing source Logo"
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
                "cheap_banner", "random_badge", "thick_outline", "oval_sticker_collage",
                "clutter", "fake_3d", "oversaturation", "invented_claim",
            ],
            "source": item["source"],
            "source_sha256": item["source_sha256"],
            "output": item["output"],
            "operation": manifest["operation"],
        }

    def write_plan(
        self,
        manifest_path: Path,
        manifest: dict[str, object],
        item: dict[str, object],
        plan: dict[str, object] | None = None,
        *,
        name: str = "main-image-plan.json",
    ) -> Path:
        plan_path = manifest_path.parent / "work" / name
        plan_path.write_text(
            json.dumps(plan or self.plan_for(manifest, item), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return plan_path

    def register_plan(
        self,
        manifest_path: Path,
        item: dict[str, object],
        plan_path: Path,
        *,
        check: bool = True,
        attempts: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        arguments: list[object] = [
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "pending",
            "--main-image-plan-json", plan_path,
        ]
        if attempts is not None:
            arguments.extend(["--attempts", attempts])
        return self.run_cli(UPDATE, *arguments, check=check)

    def make_review(
        self,
        manifest_path: Path,
        manifest: dict[str, object],
        item: dict[str, object],
        *,
        passed: bool = True,
    ) -> Path:
        output = Path(str(item["output"]))
        if not output.is_file():
            Image.new("RGB", (320, 320), (230, 224, 210)).save(output, format="PNG")
        current = json.loads(manifest_path.read_text(encoding="utf-8"))
        current_item = current["items"][0]
        plan_path = Path(current_item["main_image_plan_registration"]["path"])
        self.review_counter += 1
        evidence_dir = manifest_path.parent / "work" / (
            f"review-{item['task_id']}-{self.review_counter:03d}"
        )
        self.run_cli(
            MAIN_IMAGE_REVIEW,
            "prepare",
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--candidate", output,
            "--plan-json", plan_path,
            "--output-dir", evidence_dir,
        )
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
            assessment["scores"]["commercial_polish"] = 3
            assessment["notes"] = "candidate rejected for commercial polish"
        else:
            assessment["notes"] = "full, 256 and 160 views reviewed"
        assessment_path = evidence_dir / "assessment.json"
        assessment_path.write_text(
            json.dumps(assessment, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
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
        return Path(
            next(
                line.split("=", 1)[1]
                for line in finalized.stdout.splitlines()
                if line.startswith("review=")
            )
        )

    def success(
        self,
        manifest_path: Path,
        item: dict[str, object],
        review_path: Path | None,
        *,
        attempts: int = 1,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        arguments: list[object] = [
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "success",
            "--attempts", attempts,
            "--attempt-stage", "commerce_main_image",
        ]
        if review_path is not None:
            arguments.extend(["--main-image-quality-review-json", review_path])
        return self.run_cli(UPDATE, *arguments, check=check)

    def test_preflight_requires_explicit_route_and_creative_authorization(self) -> None:
        rejected = self.run_cli(
            PREFLIGHT,
            "--mode", "generate",
            "--operation", "检查主图是否清晰",
            "--ratio", "1:1",
            "--workflow", "commerce_main_image",
            "--platform-profile", "general-commerce",
            "--visual-direction", "clean premium product presentation",
            "--text-policy", "no_text",
            "--output-root", self.output_root,
            check=False,
        )
        self.assertNotEqual(0, rejected.returncode)
        self.assertIn("creative authorization", rejected.stderr)

        localization = self.run_cli(
            PREFLIGHT,
            "--input", self.root,
            "--mode", "localization",
            "--operation", "优化主图并翻译",
            "--ratio", "original",
            "--target-language", "English",
            "--workflow", "commerce_main_image",
            "--platform-profile", "general-commerce",
            "--visual-direction", "clean premium product presentation",
            "--text-policy", "no_text",
            "--output-root", self.output_root,
            check=False,
        )
        self.assertNotEqual(0, localization.returncode)
        self.assertIn("only available for edit and generate", localization.stderr)

        missing_route = self.run_cli(
            PREFLIGHT,
            "--mode", "generate",
            "--operation", "制作电商主图",
            "--ratio", "1:1",
            "--output-root", self.output_root,
            check=False,
        )
        self.assertNotEqual(0, missing_route.returncode)
        self.assertIn("requires --workflow commerce_main_image", missing_route.stderr)

        for operation in (
            "不要优化主图，只翻译",
            "检查主图是否清晰",
            "检查如何优化主图",
            "评估优化主图的方案",
            "分析优化主图的可行性",
            "审核重新设计主图的提案",
            "我已经优化了主图，帮我检查效果",
            "主图已经重做，请检查是否清晰",
            "对比优化前后的主图",
            "优化颜色。主图仅用于参考",
            "只优化主图左下角文字",
            "修改主图背景",
            "替换主图Logo",
            "制作主图背景",
            "做主图文字替换",
            "Create a background for the main image",
            "Make the text on the main image larger",
            "Review how to optimize the main image",
            "Review options to optimize the main image",
            "Assess a plan to redesign the main image",
            "Check recommendations to improve the main image",
        ):
            with self.subTest(operation=operation):
                rejected_local = self.run_cli(
                    PREFLIGHT,
                    "--mode", "generate",
                    "--operation", operation,
                    "--ratio", "1:1",
                    "--workflow", "commerce_main_image",
                    "--platform-profile", "general-commerce",
                    "--visual-direction", "clean premium product presentation",
                    "--text-policy", "no_text",
                    "--output-root", self.output_root,
                    check=False,
                )
                self.assertNotEqual(0, rejected_local.returncode)
                self.assertIn("creative authorization", rejected_local.stderr)

        for operation in (
            "把这张图优化成适合 Shopee 的商品主图",
            "请基于这些素材重新设计一张电商主图",
            "重做整张主图",
            "优化整张主图整体构图",
            "这张主图帮我重做",
            "电商主图请重新设计",
            "制作电商产品主图",
            "不要保留旧背景，重做整张主图",
            "制作整张蓝色背景的产品主图",
            "制作一张带精确文案的电商主图",
            "制作电商主图并添加 Logo",
            "Redesign the entire main image",
            "Create a main image with the exact text SALE",
            "Design a product hero image featuring the supplied copy",
            "Create a product main image and add the supplied logo",
        ):
            with self.subTest(authorized_operation=operation):
                self.preflight(operation=operation)

    def test_user_exact_can_be_loaded_from_a_strict_utf8_file_verbatim(self) -> None:
        exact_text = "夏日限定\n第二行原样保留  ！"
        exact_file = self.root / "精确文案.txt"
        exact_file.write_text(exact_text, encoding="utf-8")
        completed = self.run_cli(
            PREFLIGHT,
            "--mode", "generate",
            "--operation", "制作电商主图",
            "--ratio", "1:1",
            "--workflow", "commerce_main_image",
            "--platform-profile", "general-commerce",
            "--visual-direction", "克制高级",
            "--text-policy", "user_exact",
            "--exact-text-file", exact_file,
            "--output-root", self.output_root,
        )
        _, manifest = self.manifest_from(completed)
        self.assertEqual(exact_text, manifest["main_image_policy"]["exact_text"])

    def test_generate_and_edit_routes_create_locked_fields_without_policy_exception(self) -> None:
        for mode, operation in (("generate", "做主图"), ("edit", "优化产品主图")):
            with self.subTest(mode=mode):
                _, manifest = self.preflight(mode=mode, operation=operation)
                self.assertEqual("commerce_main_image", manifest["workflow"])
                self.assertEqual(
                    {
                        "default": "pure_generation",
                        "reference_images_allowed": False,
                        "logo_exception": ["deterministic_overlay", "conflict_relocation"],
                    },
                    manifest["image_model_policy"],
                )
                item = manifest["items"][0]
                self.assertIsNone(item["main_image_plan"])
                self.assertIsNone(item["main_image_plan_registration"])
                self.assertIsNone(item["main_image_quality_review"])

    def test_historical_v4_main_image_wording_without_workflow_remains_readable(self) -> None:
        manifest_path, manifest = self.preflight(
            workflow=False,
            operation="adjust product image",
        )
        manifest["operation"] = "优化产品主图"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        verified = self.run_cli(VERIFY, "--manifest", manifest_path, "--allow-pending")

        self.assertTrue(json.loads(verified.stdout)["valid"])

    def test_plan_is_commerce_only_complete_separate_and_frozen(self) -> None:
        standard_path, standard = self.preflight(workflow=False, operation="adjust product image")
        standard_item = standard["items"][0]
        standard_plan = self.write_plan(standard_path, standard, standard_item)
        rejected_standard = self.register_plan(standard_path, standard_item, standard_plan, check=False)
        self.assertNotEqual(0, rejected_standard.returncode)
        self.assertIn("only valid for commerce_main_image", rejected_standard.stderr)

        manifest_path, manifest = self.preflight()
        item = manifest["items"][0]
        incomplete = self.plan_for(manifest, item)
        incomplete.pop("lighting_and_shadow")
        incomplete_path = self.write_plan(manifest_path, manifest, item, incomplete)
        rejected_incomplete = self.register_plan(manifest_path, item, incomplete_path, check=False)
        self.assertNotEqual(0, rejected_incomplete.returncode)
        self.assertIn("missing fields", rejected_incomplete.stderr)

        missing_ban = self.plan_for(manifest, item)
        missing_ban["forbidden_patterns"].remove("invented_claim")
        missing_ban_path = self.write_plan(manifest_path, manifest, item, missing_ban)
        rejected_ban = self.register_plan(manifest_path, item, missing_ban_path, check=False)
        self.assertNotEqual(0, rejected_ban.returncode)
        self.assertIn("missing hard bans", rejected_ban.stderr)

        valid_path = self.write_plan(manifest_path, manifest, item)
        simultaneous = self.register_plan(manifest_path, item, valid_path, attempts=1, check=False)
        self.assertNotEqual(0, simultaneous.returncode)
        self.assertIn("separate pre-attempt update", simultaneous.stderr)
        self.register_plan(manifest_path, item, valid_path)

        replacement = self.write_plan(
            manifest_path,
            manifest,
            item,
            self.plan_for(manifest, item),
            name="replacement-plan.json",
        )
        rejected_replacement = self.register_plan(manifest_path, item, replacement, check=False)
        self.assertNotEqual(0, rejected_replacement.returncode)
        self.assertIn("cannot be replaced or re-registered", rejected_replacement.stderr)

    def test_plan_bindings_are_exact(self) -> None:
        for field, value in (
            ("task_id", "another-task"),
            ("source", "not-empty-for-generate.png"),
            ("operation", "优化另一个主图"),
        ):
            with self.subTest(field=field):
                manifest_path, manifest = self.preflight()
                item = manifest["items"][0]
                plan = self.plan_for(manifest, item)
                plan[field] = value
                plan_path = self.write_plan(manifest_path, manifest, item, plan)
                rejected = self.register_plan(manifest_path, item, plan_path, check=False)
                self.assertNotEqual(0, rejected.returncode)
                self.assertIn(field, rejected.stderr)

    def test_attempts_require_a_frozen_plan_are_contiguous_and_stop_at_three(self) -> None:
        manifest_path, manifest = self.preflight()
        item = manifest["items"][0]
        before_plan = self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "pending",
            "--attempts", 1,
            "--attempt-stage", "commerce_main_image",
            "--failure-type", "quality",
            "--error", "candidate rejected",
            check=False,
        )
        self.assertNotEqual(0, before_plan.returncode)
        self.assertIn("frozen plan", before_plan.stderr)

        plan_path = self.write_plan(manifest_path, manifest, item)
        self.register_plan(manifest_path, item, plan_path)
        skipped = self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "pending",
            "--attempts", 2,
            "--attempt-stage", "commerce_main_image",
            "--failure-type", "quality",
            "--error", "candidate rejected",
            check=False,
        )
        self.assertNotEqual(0, skipped.returncode)
        self.assertIn("increment", skipped.stderr)

        Image.new("RGB", (320, 320), (20, 80, 190)).save(
            Path(str(item["output"])), format="PNG"
        )
        unreviewed = self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "pending",
            "--attempts", 1,
            "--attempt-stage", "commerce_main_image",
            "--failure-type", "quality",
            "--error", "candidate rejected without review",
            check=False,
        )
        self.assertNotEqual(0, unreviewed.returncode)
        self.assertIn("finalized quality review", unreviewed.stderr)

        for attempt in range(1, 4):
            Image.new("RGB", (320, 320), (40 * attempt, 80, 190)).save(
                Path(str(item["output"])), format="PNG"
            )
            review_path = self.make_review(
                manifest_path,
                manifest,
                item,
                passed=False,
            )
            self.run_cli(
                UPDATE,
                "--manifest", manifest_path,
                "--task-id", item["task_id"],
                "--worker-id", item["worker_id"],
                "--status", "failed" if attempt == 3 else "pending",
                "--attempts", attempt,
                "--attempt-stage", "commerce_main_image",
                "--failure-type", "quality",
                "--error", f"candidate {attempt} rejected",
                "--main-image-quality-review-json", review_path,
            )
        self.assertFalse(Path(str(item["output"])).exists())
        rejected_candidates = list((manifest_path.parent / "work" / "rejected").glob("*.png"))
        self.assertEqual(1, len(rejected_candidates))
        terminal_verified = self.run_cli(VERIFY, "--manifest", manifest_path)
        self.assertTrue(json.loads(terminal_verified.stdout)["valid"])
        terminal_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        candidate_records = [
            record
            for record in terminal_manifest["items"][0]["attempt_history"]
            if record["failure_type"] == "quality"
        ]
        self.assertEqual(3, len(candidate_records))
        self.assertTrue(all(record["quality_review"]["passed"] is False for record in candidate_records))
        first_full = Path(candidate_records[0]["quality_review"]["record"]["views"]["full"]["path"])
        self.assertTrue(first_full.is_file())
        preserved_full = first_full.read_bytes()
        first_full.write_bytes(preserved_full + b"tampered")
        tampered_history = self.run_cli(VERIFY, "--manifest", manifest_path, check=False)
        self.assertNotEqual(0, tampered_history.returncode)
        self.assertTrue(
            any(
                "full evidence" in entry["error"]
                for entry in json.loads(tampered_history.stdout)["errors"]
            )
        )
        first_full.write_bytes(preserved_full)
        restored_history = self.run_cli(VERIFY, "--manifest", manifest_path)
        self.assertTrue(json.loads(restored_history.stdout)["valid"])

        exhausted = self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "pending",
            "--attempts", 4,
            "--attempt-stage", "commerce_main_image",
            "--failure-type", "quality",
            "--error", "fourth candidate",
            check=False,
        )
        self.assertNotEqual(0, exhausted.returncode)
        self.assertIn("terminal", exhausted.stderr)

        infrastructure_after_terminal = self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "pending",
            "--attempts", 4,
            "--failure-type", "infrastructure",
            "--error", "late infrastructure retry",
            check=False,
        )
        self.assertNotEqual(0, infrastructure_after_terminal.returncode)
        self.assertIn("terminal", infrastructure_after_terminal.stderr)

        forged = json.loads(manifest_path.read_text(encoding="utf-8"))
        forged_item = forged["items"][0]
        forged_item["attempts"] = 4
        forged_item["attempt_history"].append({
            "record_id": f"{item['task_id']}:4:forged",
            "attempt": 4,
            "status": "failed",
            "failure_type": "infrastructure",
            "attempt_stage": None,
            "error": "late forged retry",
            "recorded_at": forged_item["attempt_history"][-1]["recorded_at"],
        })
        manifest_path.write_text(
            json.dumps(forged, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        verified = self.run_cli(VERIFY, "--manifest", manifest_path, check=False)
        self.assertNotEqual(0, verified.returncode)
        verification_errors = [
            entry["error"] for entry in json.loads(verified.stdout)["errors"]
        ]
        self.assertTrue(
            any("terminal" in error and "final task attempt" in error for error in verification_errors),
            verification_errors,
        )

    def test_success_requires_current_passing_review_and_a_positive_attempt(self) -> None:
        manifest_path, manifest = self.preflight()
        item = manifest["items"][0]
        plan_path = self.write_plan(manifest_path, manifest, item)
        self.register_plan(manifest_path, item, plan_path)
        Image.new("RGB", (320, 320), (230, 224, 210)).save(Path(str(item["output"])), format="PNG")

        without_review = self.success(manifest_path, item, None, check=False)
        self.assertNotEqual(0, without_review.returncode)
        self.assertIn("quality review", without_review.stderr)

        review_path = self.make_review(manifest_path, manifest, item)
        review = json.loads(review_path.read_text(encoding="utf-8"))
        review["views"].pop("160")
        review_path.write_text(json.dumps(review, indent=2) + "\n", encoding="utf-8")
        missing_view = self.success(manifest_path, item, review_path, check=False)
        self.assertNotEqual(0, missing_view.returncode)
        self.assertIn("exact result", missing_view.stderr)

        review_path = self.make_review(manifest_path, manifest, item)
        review = json.loads(review_path.read_text(encoding="utf-8"))
        review["criteria"]["commercial_polish"] = False
        review_path.write_text(json.dumps(review, indent=2) + "\n", encoding="utf-8")
        failed_criterion = self.success(manifest_path, item, review_path, check=False)
        self.assertNotEqual(0, failed_criterion.returncode)
        self.assertIn("exact result", failed_criterion.stderr)

        review_path = self.make_review(manifest_path, manifest, item)
        review = json.loads(review_path.read_text(encoding="utf-8"))
        review.pop("assessment")
        review.pop("evidence")
        review_path.write_text(json.dumps(review, indent=2) + "\n", encoding="utf-8")
        script_free_review = self.success(manifest_path, item, review_path, check=False)
        self.assertNotEqual(0, script_free_review.returncode)
        self.assertIn("top-level keys", script_free_review.stderr)

    def test_infrastructure_attempts_are_stage_bound_candidate_free_and_capped_at_four(self) -> None:
        manifest_path, manifest = self.preflight()
        item = manifest["items"][0]
        plan_path = self.write_plan(manifest_path, manifest, item)
        self.register_plan(manifest_path, item, plan_path)

        for attempt in range(1, 5):
            self.run_cli(
                UPDATE,
                "--manifest", manifest_path,
                "--task-id", item["task_id"],
                "--worker-id", item["worker_id"],
                "--status", "pending",
                "--attempts", attempt,
                "--failure-type", "infrastructure",
                "--error", f"temporary provider failure {attempt}",
            )

        current = json.loads(manifest_path.read_text(encoding="utf-8"))
        history = current["items"][0]["attempt_history"]
        self.assertEqual(4, len(history))
        self.assertTrue(
            all(record["attempt_stage"] == "commerce_main_image" for record in history)
        )
        self.assertTrue(
            all("candidate_sha256" not in record for record in history)
        )
        verified = self.run_cli(
            VERIFY,
            "--manifest", manifest_path,
            "--allow-pending",
        )
        self.assertTrue(json.loads(verified.stdout)["valid"])

        exhausted = self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "pending",
            "--attempts", 5,
            "--attempt-stage", "commerce_main_image",
            "--failure-type", "infrastructure",
            "--error", "fifth provider failure",
            check=False,
        )
        self.assertNotEqual(0, exhausted.returncode)
        self.assertIn("budget of 4", exhausted.stderr)

    def test_candidate_hash_cannot_be_reused_and_logo_stages_keep_their_own_contract(self) -> None:
        manifest_path, manifest = self.preflight()
        item = manifest["items"][0]
        plan_path = self.write_plan(manifest_path, manifest, item)
        self.register_plan(manifest_path, item, plan_path)
        output = Path(str(item["output"]))
        Image.new("RGB", (320, 320), (80, 100, 180)).save(output, format="PNG")
        first_review = self.make_review(manifest_path, manifest, item, passed=False)
        self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "pending",
            "--attempts", 1,
            "--attempt-stage", "commerce_main_image",
            "--failure-type", "quality",
            "--error", "first candidate rejected",
            "--main-image-quality-review-json", first_review,
        )
        duplicate_review = self.make_review(manifest_path, manifest, item, passed=False)
        duplicate = self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "pending",
            "--attempts", 2,
            "--attempt-stage", "commerce_main_image",
            "--failure-type", "quality",
            "--error", "unchanged candidate submitted again",
            "--main-image-quality-review-json", duplicate_review,
            check=False,
        )
        self.assertNotEqual(0, duplicate.returncode)
        self.assertIn("reuse the same candidate hash", duplicate.stderr)

        logo_stage = self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "pending",
            "--attempts", 2,
            "--attempt-stage", "logo_conflict",
            "--failure-type", "quality",
            "--error", "Logo conflict candidate rejected",
            check=False,
        )
        self.assertNotEqual(0, logo_stage.returncode)
        self.assertNotIn("require the commerce_main_image stage", logo_stage.stderr)

    def test_logo_final_review_hash_bridges_through_the_preserved_accepted_base(self) -> None:
        sys.path.insert(0, str(SCRIPTS_DIR))
        try:
            from manifest_utils import validate_main_image_attempt_contract
        finally:
            sys.path.pop(0)

        manifest_path, manifest = self.preflight(logo=True)
        item = manifest["items"][0]
        plan_path = self.write_plan(manifest_path, manifest, item)
        self.register_plan(manifest_path, item, plan_path)
        output = Path(str(item["output"]))
        Image.new("RGB", (320, 320), (70, 110, 180)).save(output, format="PNG")
        accepted_base = manifest_path.parent / "work" / "accepted-main-image-base.png"
        accepted_base.write_bytes(output.read_bytes())
        accepted_review = self.make_review(manifest_path, manifest, item)
        self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "pending",
            "--attempts", 1,
            "--attempt-stage", "commerce_main_image",
            "--base-output", accepted_base,
            "--main-image-quality-review-json", accepted_review,
        )

        current = json.loads(manifest_path.read_text(encoding="utf-8"))
        current_item = current["items"][0]
        logo = self.root / "真实-logo.png"
        Image.new("RGBA", (40, 16), (220, 60, 30, 255)).save(logo, format="PNG")
        current["logo"] = {
            "enabled": True,
            "source": str(logo.resolve()),
            "source_sha256": hashlib.sha256(logo.read_bytes()).hexdigest(),
        }
        Image.new("RGB", (320, 320), (75, 115, 185)).save(output, format="PNG")
        current_item["status"] = "success"
        current_item["logo_decision"] = "direct_overlay"
        current_item["prepared_base"] = str(accepted_base.resolve())
        errors = validate_main_image_attempt_contract(current_item, current)
        self.assertFalse(any("derive from an accepted candidate hash" in error for error in errors), errors)

    def test_commerce_candidate_is_a_valid_logo_conflict_reference_base(self) -> None:
        sys.path.insert(0, str(SCRIPTS_DIR))
        try:
            from manifest_utils import _accepted_no_reference_base
        finally:
            sys.path.pop(0)

        manifest_path, manifest = self.preflight(logo=True)
        item = manifest["items"][0]
        plan_path = self.write_plan(manifest_path, manifest, item)
        self.register_plan(manifest_path, item, plan_path)
        output = Path(str(item["output"]))
        Image.new("RGB", (320, 320), (70, 110, 180)).save(output, format="PNG")
        accepted_base = manifest_path.parent / "work" / "accepted-commerce-base.png"
        accepted_base.write_bytes(output.read_bytes())
        accepted_review = self.make_review(manifest_path, manifest, item)
        self.run_cli(
            UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "pending",
            "--attempts", 1,
            "--attempt-stage", "commerce_main_image",
            "--base-output", accepted_base,
            "--main-image-quality-review-json", accepted_review,
        )

        current = json.loads(manifest_path.read_text(encoding="utf-8"))
        base, record, errors = _accepted_no_reference_base(
            current["items"][0],
            current,
            before_attempt=2,
        )

        self.assertEqual(accepted_base.resolve(), base)
        self.assertEqual("commerce_main_image", record["attempt_stage"] if record else None)
        self.assertEqual([], errors)

    def test_valid_success_verifies_and_detects_plan_review_and_output_tampering(self) -> None:
        manifest_path, manifest = self.preflight()
        item = manifest["items"][0]
        plan_path = self.write_plan(manifest_path, manifest, item)
        self.register_plan(manifest_path, item, plan_path)
        review_path = self.make_review(manifest_path, manifest, item)
        self.success(manifest_path, item, review_path)

        verified = self.run_cli(VERIFY, "--manifest", manifest_path)
        self.assertTrue(json.loads(verified.stdout)["valid"])
        stored = json.loads(manifest_path.read_text(encoding="utf-8"))["items"][0]
        self.assertEqual(1, stored["attempts"])
        self.assertEqual([1], [record["attempt"] for record in stored["attempt_history"]])
        self.assertEqual("success", stored["attempt_history"][0]["status"])

        for path, expected_fragment in (
            (plan_path, "plan artifact hash changed"),
            (review_path, "review artifact hash changed"),
            (Path(str(item["output"])), "output"),
        ):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.write_bytes(original + b"tampered")
                rejected = self.run_cli(
                    VERIFY,
                    "--manifest", manifest_path,
                    check=False,
                )
                self.assertNotEqual(0, rejected.returncode)
                errors = [entry["error"] for entry in json.loads(rejected.stdout)["errors"]]
                self.assertTrue(any(expected_fragment in error for error in errors), errors)
                path.write_bytes(original)

    def test_real_cli_end_to_end_preflight_plan_review_success_and_verify(self) -> None:
        manifest_path, manifest = self.preflight()
        item = manifest["items"][0]
        plan_path = self.write_plan(manifest_path, manifest, item)
        self.register_plan(manifest_path, item, plan_path)

        output = Path(str(item["output"]))
        Image.new("RGB", (320, 320), (230, 224, 210)).save(output, format="PNG")
        prepared = self.run_cli(
            MAIN_IMAGE_REVIEW,
            "prepare",
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--candidate", output,
            "--plan-json", plan_path,
        )
        evidence_dir = Path(
            next(
                line.split("=", 1)[1]
                for line in prepared.stdout.splitlines()
                if line.startswith("evidence_dir=")
            )
        )
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
        assessment["notes"] = "full, 256 and 160 views reviewed"
        assessment_path.write_text(
            json.dumps(assessment, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

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
        review_path = Path(
            next(
                line.split("=", 1)[1]
                for line in finalized.stdout.splitlines()
                if line.startswith("review=")
            )
        )
        self.success(manifest_path, item, review_path)

        verified = self.run_cli(VERIFY, "--manifest", manifest_path)
        verification = json.loads(verified.stdout)
        self.assertTrue(verification["valid"], verification["errors"])
        stored = json.loads(manifest_path.read_text(encoding="utf-8"))["items"][0]
        self.assertEqual("success", stored["status"])
        self.assertEqual(1, stored["attempts"])
        self.assertEqual(
            hashlib.sha256(plan_path.read_bytes()).hexdigest(),
            stored["main_image_plan_registration"]["sha256"],
        )
        self.assertEqual(
            hashlib.sha256(review_path.read_bytes()).hexdigest(),
            stored["main_image_quality_review"]["sha256"],
        )
        self.assertEqual(
            hashlib.sha256(output.read_bytes()).hexdigest(),
            stored["output_validation"]["sha256"],
        )

    def test_future_tolerated_item_timestamp_cannot_silently_drop_a_reviewed_attempt(self) -> None:
        manifest_path, manifest = self.preflight()
        item = manifest["items"][0]
        plan_path = self.write_plan(manifest_path, manifest, item)
        self.register_plan(manifest_path, item, plan_path)
        Image.new("RGB", (320, 320), (80, 120, 175)).save(
            Path(str(item["output"])), format="PNG"
        )
        review_path = self.make_review(manifest_path, manifest, item)

        skewed = json.loads(manifest_path.read_text(encoding="utf-8"))
        current_time = datetime.fromisoformat(skewed["items"][0]["updated_at"])
        skewed["items"][0]["updated_at"] = (
            current_time + timedelta(seconds=4)
        ).isoformat(timespec="microseconds")
        manifest_path.write_text(
            json.dumps(skewed, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        self.success(manifest_path, item, review_path)
        stored = json.loads(manifest_path.read_text(encoding="utf-8"))["items"][0]
        self.assertEqual(1, stored["attempts"])
        self.assertEqual("success", stored["status"])
        self.assertEqual(1, len(stored["attempt_history"]))
        self.assertIsNotNone(stored["main_image_quality_review"])

    def test_localization_rejects_commerce_plan_and_review_flags(self) -> None:
        manifest_path, manifest = self.preflight(
            mode="localization",
            operation="translate image",
            workflow=False,
        )
        item = manifest["items"][0]
        bogus = manifest_path.parent / "work" / "bogus.json"
        bogus.write_text("{}\n", encoding="utf-8")
        for flag in ("--main-image-plan-json", "--main-image-quality-review-json"):
            with self.subTest(flag=flag):
                rejected = self.run_cli(
                    UPDATE,
                    "--manifest", manifest_path,
                    "--task-id", item["task_id"],
                    "--worker-id", item["worker_id"],
                    "--status", "pending",
                    flag, bogus,
                    check=False,
                )
                self.assertNotEqual(0, rejected.returncode)
                self.assertIn("only valid for commerce_main_image", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
