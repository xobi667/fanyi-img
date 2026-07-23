from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sys
from typing import Iterator
import unittest

from PIL import Image, ImageDraw

from tests import test_logo_contract as logo_contract


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
try:
    from manifest_utils import operation_authorizes_commerce_main_image
finally:
    sys.path.pop(0)


class LocalizationDirectLogoFlowTests(unittest.TestCase):
    @contextmanager
    def logo_fixture(self) -> Iterator[logo_contract.LogoManifestContractTests]:
        fixture = logo_contract.LogoManifestContractTests(
            "test_localization_logo_conflict_uses_frozen_pre_conflict_reference"
        )
        fixture.setUp()
        try:
            yield fixture
        finally:
            fixture.tearDown()

    def prepare_third_attempt_localized_base(
        self,
        fixture: logo_contract.LogoManifestContractTests,
    ) -> dict[str, object]:
        target = fixture.input_dir / "target.png"
        source_image = Image.new("RGB", (400, 400), (236, 232, 220))
        source_draw = ImageDraw.Draw(source_image)
        source_draw.rounded_rectangle(
            (95, 85, 305, 300),
            radius=20,
            fill=(62, 128, 188),
        )
        source_draw.rectangle((235, 330, 365, 372), outline=(25, 25, 25), width=2)
        source_draw.rectangle((250, 340, 350, 347), fill=(25, 25, 25))
        source_draw.rectangle((265, 355, 335, 362), fill=(25, 25, 25))
        source_image.save(target, format="PNG")

        logo = fixture.make_logo()
        manifest_path, manifest = fixture.manifest_from_preflight(
            "--input", target,
            "--mode", "localization",
            "--operation", "仅翻译现有文字并添加 Logo",
            "--ratio", "original",
            "--target-language", "Indonesian",
            "--workers", 1,
            "--logo", logo,
            "--output-root", fixture.output_root,
            "--task-name", "localization-direct-logo",
        )
        item = manifest["items"][0]
        work_dir = manifest_path.parent / "work"
        plan_path = work_dir / "localization-plan.json"
        plan = {
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
                "source_bbox": [235, 330, 366, 373],
                "target_bbox": [235, 330, 366, 373],
                "source": "SCREEN",
                "translation": "KASA",
                "target_text_source": "translated",
                "requested_target_text": None,
                "role": "label",
                "text_layout_adaptation": {"required": False, "reason": None},
                "protected_non_text_regions": [],
            }],
            "non_text_inventory": [
                {
                    "id": "product-01",
                    "kind": "element",
                    "scope": "region",
                    "bbox": [95, 85, 306, 301],
                },
                {
                    "id": "background",
                    "kind": "background_surface",
                    "scope": "canvas",
                    "bbox": None,
                },
            ],
        }
        fixture.write_json(plan_path, plan)
        fixture.run_cli(
            logo_contract.UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "pending",
            "--localization-plan-json", plan_path,
        )

        for attempt in (1, 2):
            fixture.run_cli(
                logo_contract.UPDATE,
                "--manifest", manifest_path,
                "--task-id", item["task_id"],
                "--worker-id", item["worker_id"],
                "--status", "pending",
                "--attempts", attempt,
                "--attempt-stage", "pure_generation",
                "--failure-type", "quality",
                "--error", f"localization candidate {attempt} changed an unapproved visual",
            )

        localized_base = work_dir / "localized-base-attempt-3.png"
        localized_image = source_image.copy()
        localized_draw = ImageDraw.Draw(localized_image)
        localized_draw.rectangle((236, 331, 364, 371), fill=(236, 232, 220))
        localized_draw.rectangle((250, 340, 350, 347), fill=(25, 25, 25))
        localized_draw.rectangle((275, 355, 325, 362), fill=(25, 25, 25))
        localized_image.save(localized_base, format="PNG")
        fixture.run_cli(
            logo_contract.UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "pending",
            "--attempts", 3,
            "--attempt-stage", "pure_generation",
            "--localized-base", localized_base,
        )

        stored = json.loads(manifest_path.read_text(encoding="utf-8"))["items"][0]
        self.assertEqual(3, stored["attempts"])
        self.assertEqual([1, 2, 3], [
            record["attempt"] for record in stored["attempt_history"]
        ])
        self.assertEqual(["quality", "quality", None], [
            record["failure_type"] for record in stored["attempt_history"]
        ])
        self.assertEqual(["pure_generation"] * 3, [
            record["attempt_stage"] for record in stored["attempt_history"]
        ])
        self.assertEqual("pending", stored["attempt_history"][-1]["status"])
        self.assertEqual(
            {
                "kind": "localized_base",
                "policy": "no_reference_pure_generation",
                "path": str(localized_base.resolve()),
                "sha256": fixture.sha256(localized_base),
            },
            stored["attempt_history"][-1]["accepted_base"],
        )
        return {
            "manifest_path": manifest_path,
            "manifest": manifest,
            "item": item,
            "logo": logo,
            "localized_base": localized_base,
        }

    def test_third_localization_candidate_direct_overlay_finishes_without_logo_attempt(
        self,
    ) -> None:
        with self.logo_fixture() as fixture:
            context = self.prepare_third_attempt_localized_base(fixture)
            manifest_path = context["manifest_path"]
            manifest = context["manifest"]
            item = context["item"]
            logo = context["logo"]
            localized_base = context["localized_base"]
            output = Path(str(item["output"]))
            work_dir = manifest_path.parent / "work"

            geometry_path = work_dir / "logo-geometry.json"
            fixture.run_cli(
                logo_contract.APPLY_LOGO,
                "--input", localized_base,
                "--output", output,
                "--logo", logo,
                "--dry-run",
                "--geometry-json", geometry_path,
            )
            geometry = json.loads(geometry_path.read_text(encoding="utf-8"))["items"][0]
            logo_plan_path = work_dir / "logo-plan.json"
            fixture.write_json(
                logo_plan_path,
                {
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
                        "family_id": "ungrouped",
                        "visible_bbox": geometry["visible_bbox"],
                        "safe_zone": geometry["safe_zone"],
                        "modules": [
                            {
                                "id": "product-01",
                                "type": "product",
                                "bbox": [95, 85, 306, 301],
                            },
                            {
                                "id": "text-01",
                                "type": "text",
                                "bbox": [235, 330, 366, 373],
                            },
                        ],
                        "conflicts": [],
                        "decision": "direct_overlay",
                        "module_anchors": [],
                        "family_reference": item["task_id"],
                        "base_approved": True,
                        "final_approved": True,
                    }],
                },
            )
            fixture.run_cli(
                logo_contract.APPLY_LOGO,
                "--input", localized_base,
                "--output", output,
                "--logo", logo,
                "--safe-zone-approved",
            )
            fixture.run_cli(
                logo_contract.UPDATE,
                "--manifest", manifest_path,
                "--task-id", item["task_id"],
                "--worker-id", item["worker_id"],
                "--status", "pending",
                "--logo-plan-file", logo_plan_path,
                "--logo-decision", "direct_overlay",
                "--logo-geometry-json", geometry_path,
                "--prepared-base", localized_base,
                "--family-id", "ungrouped",
            )

            fixture.run_cli(
                logo_contract.UPDATE,
                "--manifest", manifest_path,
                "--task-id", item["task_id"],
                "--worker-id", item["worker_id"],
                "--status", "success",
            )

            verified = fixture.run_cli(
                logo_contract.VERIFY,
                "--manifest", manifest_path,
            )
            result = json.loads(verified.stdout)
            self.assertTrue(result["valid"], result["errors"])
            stored = json.loads(manifest_path.read_text(encoding="utf-8"))["items"][0]
            self.assertEqual("success", stored["status"])
            self.assertEqual("pure_generation", stored["localization_execution_stage"])
            self.assertEqual("direct_overlay", stored["logo_decision"])
            self.assertEqual(3, stored["attempts"])
            self.assertEqual(3, len(stored["attempt_history"]))
            self.assertEqual(["pure_generation"] * 3, [
                record["attempt_stage"] for record in stored["attempt_history"]
            ])
            self.assertEqual(fixture.sha256(localized_base), (
                stored["attempt_history"][-1]["accepted_base"]["sha256"]
            ))
            self.assertNotEqual(fixture.sha256(localized_base), fixture.sha256(output))
            self.assertIsNone(stored["logo_relocation_validation"])

            before = manifest_path.read_bytes()
            late_retry = fixture.run_cli(
                logo_contract.UPDATE,
                "--manifest", manifest_path,
                "--task-id", item["task_id"],
                "--worker-id", item["worker_id"],
                "--status", "pending",
                "--attempts", 4,
                "--attempt-stage", "pure_generation",
                "--failure-type", "infrastructure",
                "--error", "late provider retry after final success",
                check=False,
            )
            self.assertNotEqual(0, late_retry.returncode)
            self.assertEqual(before, manifest_path.read_bytes())

    def test_accepted_localization_candidate_closes_pure_generation_before_logo(
        self,
    ) -> None:
        with self.logo_fixture() as fixture:
            context = self.prepare_third_attempt_localized_base(fixture)
            manifest_path = context["manifest_path"]
            item = context["item"]
            before = manifest_path.read_bytes()

            late_retry = fixture.run_cli(
                logo_contract.UPDATE,
                "--manifest", manifest_path,
                "--task-id", item["task_id"],
                "--worker-id", item["worker_id"],
                "--status", "pending",
                "--attempts", 4,
                "--attempt-stage", "pure_generation",
                "--failure-type", "infrastructure",
                "--error", "late provider retry after accepted candidate",
                check=False,
            )

            self.assertNotEqual(0, late_retry.returncode)
            self.assertIn("stage is closed", late_retry.stderr)
            self.assertEqual(before, manifest_path.read_bytes())

    def test_deterministic_logo_finalize_rejects_every_lineage_mutation(
        self,
    ) -> None:
        with self.logo_fixture() as fixture:
            context = self.prepare_third_attempt_localized_base(fixture)
            manifest_path = context["manifest_path"]
            manifest = context["manifest"]
            item = context["item"]
            logo = context["logo"]
            localized_base = context["localized_base"]
            output = Path(str(item["output"]))
            work_dir = manifest_path.parent / "work"

            geometry_path = work_dir / "logo-geometry.json"
            fixture.run_cli(
                logo_contract.APPLY_LOGO,
                "--input", localized_base,
                "--output", output,
                "--logo", logo,
                "--dry-run",
                "--geometry-json", geometry_path,
            )
            geometry = json.loads(
                geometry_path.read_text(encoding="utf-8")
            )["items"][0]
            logo_plan_path = work_dir / "logo-plan.json"
            fixture.write_json(
                logo_plan_path,
                {
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
                        "family_id": "ungrouped",
                        "visible_bbox": geometry["visible_bbox"],
                        "safe_zone": geometry["safe_zone"],
                        "modules": [
                            {
                                "id": "product-01",
                                "type": "product",
                                "bbox": [95, 85, 306, 301],
                            },
                            {
                                "id": "text-01",
                                "type": "text",
                                "bbox": [235, 330, 366, 373],
                            },
                        ],
                        "conflicts": [],
                        "decision": "direct_overlay",
                        "module_anchors": [],
                        "family_reference": item["task_id"],
                        "base_approved": True,
                        "final_approved": True,
                    }],
                },
            )
            fixture.run_cli(
                logo_contract.APPLY_LOGO,
                "--input", localized_base,
                "--output", output,
                "--logo", logo,
                "--safe-zone-approved",
            )
            fixture.run_cli(
                logo_contract.UPDATE,
                "--manifest", manifest_path,
                "--task-id", item["task_id"],
                "--worker-id", item["worker_id"],
                "--status", "pending",
                "--logo-plan-file", logo_plan_path,
                "--logo-decision", "direct_overlay",
                "--logo-geometry-json", geometry_path,
                "--prepared-base", localized_base,
                "--family-id", "ungrouped",
            )

            replacement = work_dir / "unrecorded-replacement.png"
            replacement.write_bytes(localized_base.read_bytes())
            arbitrary_json = work_dir / "arbitrary.json"
            fixture.write_json(arbitrary_json, {})
            mutation_arguments = (
                ("--output", output),
                ("--prompt-summary", "changed during finalization"),
                ("--error", "not an image attempt"),
                ("--failure-type", "quality"),
                ("--base-output", replacement),
                ("--localized-base", replacement),
                ("--conflict-reference-base", replacement),
                ("--prepared-base", replacement),
                ("--family-id", "changed-family"),
                ("--logo-decision", "direct_overlay"),
                ("--logo-geometry-json", geometry_path),
                ("--module-anchors-json", "[]"),
                ("--localization-plan-json", arbitrary_json),
                ("--localization-composition-json", arbitrary_json),
                ("--logo-plan-file", logo_plan_path),
                ("--layout-families-file", arbitrary_json),
                ("--style-lock-file", arbitrary_json),
                ("--pure-rebuild-approval", "late approval"),
            )
            before = manifest_path.read_bytes()
            for option, value in mutation_arguments:
                with self.subTest(option=option):
                    rejected = fixture.run_cli(
                        logo_contract.UPDATE,
                        "--manifest", manifest_path,
                        "--task-id", item["task_id"],
                        "--worker-id", item["worker_id"],
                        "--status", "success",
                        option, value,
                        check=False,
                    )
                    self.assertNotEqual(0, rejected.returncode)
                    self.assertIn("status-only transition", rejected.stderr)
                    self.assertEqual(before, manifest_path.read_bytes())

            rejected_degrade = fixture.run_cli(
                logo_contract.UPDATE,
                "--manifest", manifest_path,
                "--task-id", item["task_id"],
                "--worker-id", item["worker_id"],
                "--status", "success",
                "--degrade-to-single",
                check=False,
            )
            self.assertNotEqual(0, rejected_degrade.returncode)
            self.assertIn("status-only transition", rejected_degrade.stderr)
            self.assertEqual(before, manifest_path.read_bytes())


class CommerceMainImageRoutingNegativeTests(unittest.TestCase):
    def test_local_edits_translation_and_review_do_not_authorize_full_main_image(
        self,
    ) -> None:
        operations = [
            "优化主图清晰度",
            "优化主图分辨率",
            "优化主图中的商品阴影",
            "优化主图左侧小图位置",
            "优化主图里的二维码",
            "美化主图边框",
            "把重新设计的主图翻译成日语",
            "翻译优化后的主图为印尼语",
            "我优化了主图，帮我看看",
            "优化主图，只把文字放大",
            "优化主图，但只调整清晰度",
            "优化主图，文字放大",
            "Improve the main image resolution",
            "Enhance the shadow in the main image",
            "Improve the QR code in the main image",
            "Optimize the typography in the main image",
            "Improve the main image; only make the text larger",
        ]

        for operation in operations:
            with self.subTest(operation=operation):
                self.assertFalse(
                    operation_authorizes_commerce_main_image(operation),
                    operation,
                )

    def test_explicit_full_main_image_creation_is_authorized(self) -> None:
        operations = [
            "制作电商主图并添加 Logo",
            "制作完整电商产品主图并添加 Logo",
            "制作完整产品主图",
            "制作完整主图",
            "制作完全不同的产品主图",
            "Create a product main image and add the supplied logo",
            "制作一张带精确文案的电商主图",
        ]

        for operation in operations:
            with self.subTest(operation=operation):
                self.assertTrue(
                    operation_authorizes_commerce_main_image(operation),
                    operation,
                )

    def test_completed_main_image_description_is_not_authorized(self) -> None:
        operations = [
            "制作完了的电商主图",
            "制作完主图后帮我检查",
            "做完了主图以后添加 Logo",
            "做完的产品主图",
        ]

        for operation in operations:
            with self.subTest(operation=operation):
                self.assertFalse(
                    operation_authorizes_commerce_main_image(operation),
                    operation,
                )


if __name__ == "__main__":
    unittest.main()
