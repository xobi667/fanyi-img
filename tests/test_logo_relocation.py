from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from logo_relocation import validate_logo_relocation  # noqa: E402


BACKGROUND = (236, 232, 220)
CANVAS = (400, 240)


class LogoRelocationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    @staticmethod
    def module_a(size: tuple[int, int] = (120, 60)) -> Image.Image:
        image = Image.new("RGB", size, (185, 30, 35))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            (4, 4, size[0] - 5, size[1] - 5),
            radius=max(3, size[1] // 7),
            outline=(255, 220, 80),
            width=max(2, size[1] // 15),
        )
        draw.rectangle((18, 15, size[0] - 35, 21), fill="white")
        draw.rectangle((18, 29, size[0] - 18, 36), fill="white")
        draw.ellipse((size[0] - 32, 10, size[0] - 10, 32), fill=(20, 70, 210))
        return image

    @staticmethod
    def module_b(size: tuple[int, int] = (120, 60)) -> Image.Image:
        image = Image.new("RGB", size, (25, 80, 185))
        draw = ImageDraw.Draw(image)
        draw.polygon(
            [(8, size[1] - 8), (size[0] // 2, 6), (size[0] - 8, size[1] - 8)],
            fill=(245, 175, 25),
        )
        draw.rectangle((35, 25, size[0] - 35, 33), fill=(15, 20, 30))
        draw.rectangle((48, 39, size[0] - 48, 47), fill="white")
        return image

    def write_fixture(
        self,
        source: Image.Image,
        prepared: Image.Image,
        modules: list[dict[str, object]],
        anchors: list[dict[str, object]],
    ) -> tuple[dict[str, object], dict[str, object], Path, Path, dict[str, object]]:
        source_path = self.root / "source.png"
        prepared_path = self.root / "prepared.png"
        source.save(source_path)
        prepared.save(prepared_path)
        task_id = "task-000001"
        conflicts = [str(module["id"]) for module in modules]
        item = {
            "task_id": task_id,
            "source": str(source_path.resolve()),
            "prepared_base": str(prepared_path.resolve()),
            "logo_decision": "regenerate_for_conflict",
            "module_anchors": anchors,
        }
        plan = {
            "schema_version": 1,
            "items": [{
                "task_id": task_id,
                "source": str(source_path.resolve()),
                "modules": modules,
                "conflicts": conflicts,
                "decision": "regenerate_for_conflict",
                "module_anchors": anchors,
            }],
        }
        geometry = {
            "canvas": list(CANVAS),
            "visible_bbox": [0, 0, 140, 160],
            "safe_zone": [0, 0, 150, 170],
            "right_module_start_range": [150, 170],
            "below_module_start_range": [170, 190],
        }
        return item, plan, source_path, prepared_path, geometry

    def validate(
        self,
        source: Image.Image,
        prepared: Image.Image,
        modules: list[dict[str, object]],
        anchors: list[dict[str, object]],
    ) -> tuple[dict[str, object], list[str]]:
        item, plan, source_path, prepared_path, geometry = self.write_fixture(
            source,
            prepared,
            modules,
            anchors,
        )
        return validate_logo_relocation(
            item,
            plan,
            source_path,
            prepared_path,
            geometry,
        )

    @staticmethod
    def source_with_a() -> tuple[Image.Image, list[dict[str, object]], list[dict[str, object]]]:
        source = Image.new("RGB", CANVAS, BACKGROUND)
        source.paste(LogoRelocationTests.module_a(), (10, 10))
        modules = [{"id": "module-a", "type": "text", "bbox": [10, 10, 130, 70]}]
        anchors = [{
            "module_id": "module-a",
            "placement": "right",
            "prepared_bbox": [160, 10, 280, 70],
        }]
        return source, modules, anchors

    def test_exact_relocation_passes_with_serializable_evidence(self) -> None:
        source, modules, anchors = self.source_with_a()
        prepared = Image.new("RGB", CANVAS, BACKGROUND)
        prepared.paste(self.module_a(), (160, 10))

        record, errors = self.validate(source, prepared, modules, anchors)

        self.assertEqual([], errors)
        self.assertTrue(record["passed"])
        self.assertTrue(record["modules"][0]["passed"])
        self.assertTrue(record["outside_relocation_pixel_lock"]["passed"])
        self.assertEqual(
            0,
            record["outside_relocation_pixel_lock"]["changed_pixels_outside_allowed"],
        )
        self.assertLessEqual(record["modules"][0]["source_position"]["score"], 0.55)
        self.assertGreaterEqual(record["modules"][0]["destination"]["score"], 0.72)
        json.dumps(record, ensure_ascii=False, allow_nan=False)

    def test_light_jpeg_resize_and_blur_noise_still_passes(self) -> None:
        source, modules, _ = self.source_with_a()
        prepared = Image.new("RGB", CANVAS, BACKGROUND)
        candidate = self.module_a().resize((126, 63), Image.Resampling.LANCZOS)
        candidate = ImageEnhance.Brightness(candidate).enhance(1.025)
        candidate = candidate.filter(ImageFilter.GaussianBlur(0.35))
        buffer = io.BytesIO()
        candidate.save(buffer, "JPEG", quality=88)
        buffer.seek(0)
        with Image.open(buffer) as decoded:
            candidate = decoded.convert("RGB")
        prepared.paste(candidate, (160, 10))
        anchors = [{
            "module_id": "module-a",
            "placement": "right",
            "prepared_bbox": [160, 10, 286, 73],
        }]

        record, errors = self.validate(source, prepared, modules, anchors)

        self.assertEqual([], errors)
        self.assertGreater(record["modules"][0]["destination"]["score"], 0.80)

    def test_unrelated_one_pixel_change_with_fake_anchor_is_rejected(self) -> None:
        source, modules, anchors = self.source_with_a()
        prepared = source.copy()
        prepared.putpixel((399, 239), (235, 232, 220))

        record, errors = self.validate(source, prepared, modules, anchors)

        self.assertFalse(record["passed"])
        self.assertTrue(any("still visually present" in error for error in errors))
        self.assertTrue(any("does not contain the corresponding module" in error for error in errors))

    def test_valid_move_that_deletes_unrelated_product_is_rejected(self) -> None:
        source, modules, anchors = self.source_with_a()
        source_draw = ImageDraw.Draw(source)
        source_draw.rounded_rectangle((300, 105, 380, 215), radius=8, fill=(25, 150, 70))
        source_draw.rectangle((315, 125, 365, 195), outline=(245, 245, 235), width=4)

        prepared = source.copy()
        prepared_draw = ImageDraw.Draw(prepared)
        prepared_draw.rectangle((10, 10, 129, 69), fill=BACKGROUND)
        prepared.paste(self.module_a(), (160, 10))
        prepared_draw.rectangle((300, 105, 380, 215), fill=BACKGROUND)

        record, errors = self.validate(source, prepared, modules, anchors)

        self.assertFalse(record["passed"])
        self.assertFalse(record["outside_relocation_pixel_lock"]["passed"])
        self.assertGreater(
            record["outside_relocation_pixel_lock"]["changed_pixels_outside_allowed"],
            0,
        )
        self.assertTrue(any("outside declared relocation ROIs" in error for error in errors))

    def test_copy_to_destination_without_clearing_source_is_rejected(self) -> None:
        source, modules, anchors = self.source_with_a()
        prepared = source.copy()
        prepared.paste(self.module_a(), (160, 10))

        record, errors = self.validate(source, prepared, modules, anchors)

        self.assertFalse(record["passed"])
        self.assertTrue(any("not substantially cleared" in error for error in errors))
        self.assertGreater(record["modules"][0]["destination"]["score"], 0.90)

    def test_clear_source_without_destination_module_is_rejected(self) -> None:
        source, modules, anchors = self.source_with_a()
        prepared = Image.new("RGB", CANVAS, BACKGROUND)

        record, errors = self.validate(source, prepared, modules, anchors)

        self.assertFalse(record["passed"])
        self.assertTrue(any("does not contain the corresponding module" in error for error in errors))

    def test_wrong_module_at_destination_is_rejected(self) -> None:
        source, modules, anchors = self.source_with_a()
        prepared = Image.new("RGB", CANVAS, BACKGROUND)
        prepared.paste(self.module_b(), (160, 10))

        record, errors = self.validate(source, prepared, modules, anchors)

        self.assertFalse(record["passed"])
        self.assertTrue(any("fingerprint does not match" in error for error in errors))

    def test_declared_bbox_offset_from_actual_module_is_rejected(self) -> None:
        source, modules, _ = self.source_with_a()
        prepared = Image.new("RGB", CANVAS, BACKGROUND)
        prepared.paste(self.module_a(), (160, 10))
        anchors = [{
            "module_id": "module-a",
            "placement": "right",
            "prepared_bbox": [205, 10, 325, 70],
        }]

        record, errors = self.validate(source, prepared, modules, anchors)

        self.assertFalse(record["passed"])
        self.assertTrue(any("prepared_bbox does not contain" in error for error in errors))

    def multi_fixture(
        self,
        *,
        swapped: bool,
    ) -> tuple[Image.Image, Image.Image, list[dict[str, object]], list[dict[str, object]]]:
        source = Image.new("RGB", CANVAS, BACKGROUND)
        source.paste(self.module_a(), (10, 10))
        source.paste(self.module_b(), (10, 90))
        prepared = Image.new("RGB", CANVAS, BACKGROUND)
        prepared.paste(self.module_b() if swapped else self.module_a(), (160, 10))
        prepared.paste(self.module_a() if swapped else self.module_b(), (160, 90))
        modules = [
            {"id": "module-a", "type": "text", "bbox": [10, 10, 130, 70]},
            {"id": "module-b", "type": "gift", "bbox": [10, 90, 130, 150]},
        ]
        anchors = [
            {"module_id": "module-a", "placement": "right", "prepared_bbox": [160, 10, 280, 70]},
            {"module_id": "module-b", "placement": "right", "prepared_bbox": [160, 90, 280, 150]},
        ]
        return source, prepared, modules, anchors

    def test_multiple_conflicts_have_unique_one_to_one_matches(self) -> None:
        source, prepared, modules, anchors = self.multi_fixture(swapped=False)

        record, errors = self.validate(source, prepared, modules, anchors)

        self.assertEqual([], errors)
        self.assertTrue(record["assignment"]["passed"])
        self.assertGreater(
            record["assignment"]["score_matrix"]["module-a"]["module-a"],
            record["assignment"]["score_matrix"]["module-b"]["module-a"],
        )

    def test_multiple_conflicts_swapped_destinations_are_rejected(self) -> None:
        source, prepared, modules, anchors = self.multi_fixture(swapped=True)

        record, errors = self.validate(source, prepared, modules, anchors)

        self.assertFalse(record["passed"])
        self.assertFalse(record["assignment"]["passed"])
        self.assertTrue(any("best matches" in error for error in errors))

    def test_different_canvases_require_explicit_original_position_mapping(self) -> None:
        source = Image.new("RGB", (300, 200), BACKGROUND)
        source.paste(self.module_a(), (10, 10))
        prepared = Image.new("RGB", CANVAS, BACKGROUND)
        prepared.paste(self.module_a(), (160, 10))
        modules = [{"id": "module-a", "type": "text", "source_bbox": [10, 10, 130, 70]}]
        anchors = [{
            "module_id": "module-a",
            "placement": "right",
            "prepared_bbox": [160, 10, 280, 70],
        }]

        record, errors = self.validate(source, prepared, modules, anchors)

        self.assertFalse(record["passed"])
        self.assertTrue(any("requires explicit prepared_source_bbox" in error for error in errors))
        self.assertTrue(any("recomputable whole-canvas mapping" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
