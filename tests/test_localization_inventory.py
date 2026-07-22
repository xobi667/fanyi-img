from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from compose_localization import compose_localization
from manifest_utils import (
    localization_non_text_pixel_lock,
    validate_localization_plan_contract,
)


class LocalizationInventoryContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.source = self.root / "source.png"
        self.candidate = self.root / "candidate.png"
        self.output = self.root / "localized.png"
        self.provenance = self.root / "composition.json"
        self.plan_path = self.root / "plan.json"
        self.source_image = Image.new("RGB", (100, 100), (240, 240, 230))
        source_draw = ImageDraw.Draw(self.source_image)
        source_draw.rectangle((24, 14, 31, 21), fill=(220, 30, 30))
        source_draw.rectangle((40, 15, 60, 18), fill=(30, 30, 30))
        self.source_image.save(self.source)

        self.candidate_image = self.source_image.copy()
        candidate_draw = ImageDraw.Draw(self.candidate_image)
        candidate_draw.rectangle((24, 14, 31, 21), fill=(20, 200, 70))
        candidate_draw.rectangle((40, 15, 60, 18), fill=(240, 240, 230))
        candidate_draw.rectangle((38, 20, 63, 23), fill=(30, 30, 30))
        self.candidate_image.save(self.candidate)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    @staticmethod
    def background_inventory() -> dict[str, object]:
        return {
            "id": "canvas-background",
            "kind": "background_surface",
            "scope": "canvas",
            "bbox": None,
        }

    @staticmethod
    def icon_inventory() -> dict[str, object]:
        return {
            "id": "inline-icon",
            "kind": "element",
            "bbox": [24, 14, 32, 22],
        }

    def plan(self) -> dict[str, object]:
        return {
            "task_id": "task-000001",
            "mode": "text_only_reference_edit",
            "source": str(self.source.resolve()),
            "source_sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
            "source_size": [100, 100],
            "target_language": "Indonesian",
            "output_ratio": "original",
            "target_size": None,
            "size_resample": {"required": False, "method": None},
            "ratio_adaptation": {"required": False, "allowed_changes": []},
            "text_blocks": [{
                "id": "text-01",
                "source_bbox": [20, 10, 70, 30],
                "target_bbox": [20, 10, 70, 30],
                "source": "SALE",
                "translation": "DISKON",
                "target_text_source": "translated",
                "requested_target_text": None,
                "role": "heading",
                "text_layout_adaptation": {
                    "required": False,
                    "reason": None,
                    "target_alignment": None,
                    "writing_direction": None,
                },
                "protected_non_text_regions": [],
            }],
            "unresolved_text": [],
            "non_text_inventory": [self.background_inventory(), self.icon_inventory()],
            "pure_rebuild_allowed": False,
        }

    def item_and_manifest(self) -> tuple[dict[str, object], dict[str, object]]:
        item = {
            "task_id": "task-000001",
            "source": str(self.source.resolve()),
            "source_sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
            "width": 100,
            "height": 100,
            "expected_dimensions": None,
            "expected_ratio": [100, 100],
        }
        return item, {"target_language": "Indonesian"}

    def write_plan(self, plan: dict[str, object]) -> None:
        self.plan_path.write_text(json.dumps(plan), encoding="utf-8")

    def test_legacy_string_inventory_fails_closed(self) -> None:
        plan = self.plan()
        plan["non_text_inventory"] = ["inline-icon", "background"]
        item, manifest = self.item_and_manifest()

        errors = validate_localization_plan_contract(item, manifest, plan)
        self.assertTrue(any("legacy string inventory" in error for error in errors))
        self.write_plan(plan)
        with self.assertRaisesRegex(ValueError, "legacy string inventory"):
            compose_localization(
                self.source,
                self.candidate,
                self.output,
                self.plan_path,
                self.provenance,
            )

    def test_background_surface_is_the_only_unprotected_canvas_item(self) -> None:
        source = Image.new("RGB", (100, 100), (240, 240, 230))
        ImageDraw.Draw(source).rectangle((40, 15, 60, 18), fill=(30, 30, 30))
        source.save(self.source)
        candidate = source.copy()
        candidate_draw = ImageDraw.Draw(candidate)
        candidate_draw.rectangle((40, 15, 60, 18), fill=(240, 240, 230))
        candidate_draw.rectangle((38, 20, 63, 23), fill=(30, 30, 30))
        candidate.save(self.candidate)
        plan = self.plan()
        plan["source_sha256"] = hashlib.sha256(self.source.read_bytes()).hexdigest()
        plan["non_text_inventory"] = [self.background_inventory()]
        item, manifest = self.item_and_manifest()

        self.assertEqual([], validate_localization_plan_contract(item, manifest, plan))
        self.write_plan(plan)
        compose_localization(
            self.source,
            self.candidate,
            self.output,
            self.plan_path,
            self.provenance,
        )
        with Image.open(self.source) as raw_source, Image.open(self.output) as raw_output:
            self.assertNotEqual(
                raw_source.convert("RGBA").getpixel((42, 16)),
                raw_output.convert("RGBA").getpixel((42, 16)),
            )

    def test_missing_element_protection_is_rejected_everywhere(self) -> None:
        plan = self.plan()
        item, manifest = self.item_and_manifest()

        plan_errors = validate_localization_plan_contract(item, manifest, plan)
        self.assertTrue(any("intersects non-text element inline-icon" in error for error in plan_errors))
        _, pixel_errors = localization_non_text_pixel_lock(self.source, self.candidate, plan)
        self.assertTrue(any("intersects non-text element inline-icon" in error for error in pixel_errors))
        self.write_plan(plan)
        with self.assertRaisesRegex(ValueError, "intersects non-text element inline-icon"):
            compose_localization(
                self.source,
                self.candidate,
                self.output,
                self.plan_path,
                self.provenance,
            )

    def test_shrunken_protection_is_rejected(self) -> None:
        plan = self.plan()
        plan["text_blocks"][0]["protected_non_text_regions"] = [{
            "id": "inline-icon",
            "bbox": [24, 14, 28, 18],
        }]
        item, manifest = self.item_and_manifest()

        errors = validate_localization_plan_contract(item, manifest, plan)
        self.assertTrue(any("does not fully cover the element intersection" in error for error in errors))
        self.write_plan(plan)
        with self.assertRaisesRegex(ValueError, "does not fully cover the element intersection"):
            compose_localization(
                self.source,
                self.candidate,
                self.output,
                self.plan_path,
                self.provenance,
            )

    def test_complete_protection_preserves_candidate_changed_icon(self) -> None:
        plan = self.plan()
        plan["text_blocks"][0]["protected_non_text_regions"] = [{
            "id": "inline-icon",
            "bbox": [24, 14, 32, 22],
        }]
        item, manifest = self.item_and_manifest()

        self.assertEqual([], validate_localization_plan_contract(item, manifest, plan))
        self.write_plan(plan)
        record = compose_localization(
            self.source,
            self.candidate,
            self.output,
            self.plan_path,
            self.provenance,
        )
        self.assertEqual("background_surface", record["mask"]["non_text_inventory"][0]["kind"])
        with Image.open(self.source) as raw_source, Image.open(self.output) as raw_output:
            self.assertEqual(
                raw_source.convert("RGBA").getpixel((25, 15)),
                raw_output.convert("RGBA").getpixel((25, 15)),
            )

    def test_target_bbox_cannot_intrude_into_an_element(self) -> None:
        plan = self.plan()
        block = plan["text_blocks"][0]
        block["source_bbox"] = [40, 10, 70, 30]
        block["target_bbox"] = [20, 10, 70, 30]
        block["text_layout_adaptation"] = {
            "required": True,
            "reason": "long translation",
            "target_alignment": "left",
            "writing_direction": "ltr",
        }
        block["protected_non_text_regions"] = [{
            "id": "inline-icon",
            "bbox": [24, 14, 32, 22],
        }]
        item, manifest = self.item_and_manifest()

        errors = validate_localization_plan_contract(item, manifest, plan)
        self.assertTrue(any("target_bbox intrudes into non-text element inline-icon" in error for error in errors))
        self.write_plan(plan)
        with self.assertRaisesRegex(ValueError, "target_bbox intrudes into non-text element inline-icon"):
            compose_localization(
                self.source,
                self.candidate,
                self.output,
                self.plan_path,
                self.provenance,
            )


if __name__ == "__main__":
    unittest.main()
