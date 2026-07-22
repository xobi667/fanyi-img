from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import apply_logo  # noqa: E402
import manifest_utils  # noqa: E402
import normalize_logo  # noqa: E402


class ApplyLogoGeometryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.default_logo = apply_logo.clean_logo(apply_logo.DEFAULT_LOGO, 10)

    def geometry_for(
        self,
        logo: Image.Image,
        canvas: tuple[int, int],
        *,
        safe_padding: int = 80,
        anchor_tolerance: int = 48,
    ) -> tuple[Image.Image, dict[str, object]]:
        return apply_logo.geometry_for(
            logo,
            canvas,
            4000,
            apply_logo.DEFAULT_LOGO_REFERENCE_BOX,
            safe_padding,
            anchor_tolerance,
            10,
        )

    def test_default_template_is_about_325_by_96_on_1254_canvas(self) -> None:
        overlay, geometry = self.geometry_for(self.default_logo, (1254, 1254))

        self.assertEqual((325, 96), overlay.size)
        left, top, right, bottom = geometry["visible_bbox"]
        self.assertEqual((0, 0), (left, top))
        self.assertAlmostEqual(325, right - left, delta=1)
        self.assertAlmostEqual(96, bottom - top, delta=1)

    def test_equal_short_sides_produce_equal_logo_geometry(self) -> None:
        landscape_overlay, landscape = self.geometry_for(self.default_logo, (1600, 900))
        portrait_overlay, portrait = self.geometry_for(self.default_logo, (900, 1600))

        self.assertEqual(landscape_overlay.size, portrait_overlay.size)
        self.assertEqual(landscape["visible_bbox"], portrait["visible_bbox"])
        self.assertEqual(landscape["safe_zone"], portrait["safe_zone"])
        self.assertEqual(
            landscape["right_module_start_range"],
            portrait["right_module_start_range"],
        )
        self.assertEqual(
            landscape["below_module_start_range"],
            portrait["below_module_start_range"],
        )

    def test_transparent_border_does_not_change_visible_size(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            tight_path = temporary / "tight.png"
            padded_path = temporary / "padded.png"

            tight = Image.new("RGBA", (200, 60), (20, 40, 220, 255))
            padded = Image.new("RGBA", (500, 300), (0, 0, 0, 0))
            padded.alpha_composite(tight, (137, 111))
            tight.save(tight_path)
            padded.save(padded_path)

            tight_logo = apply_logo.clean_logo(tight_path, 10)
            padded_logo = apply_logo.clean_logo(padded_path, 10)
            tight_overlay, tight_geometry = self.geometry_for(tight_logo, (1254, 1254))
            padded_overlay, padded_geometry = self.geometry_for(padded_logo, (1254, 1254))

        self.assertEqual(tight_logo.size, padded_logo.size)
        self.assertEqual(tight_overlay.size, padded_overlay.size)
        self.assertEqual(tight_geometry["visible_bbox"], padded_geometry["visible_bbox"])
        self.assertEqual(tight_geometry["safe_zone"], padded_geometry["safe_zone"])

    def test_fully_transparent_logo_and_zero_threshold_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            transparent_path = Path(temporary_directory) / "transparent.png"
            Image.new("RGBA", (100, 50), (0, 0, 0, 0)).save(transparent_path)

            with self.assertRaisesRegex(ValueError, "no visible pixels"):
                apply_logo.clean_logo(transparent_path, 10)
            with self.assertRaisesRegex(ValueError, "between 1 and 255"):
                apply_logo.clean_logo(transparent_path, 0)

    def test_safe_zone_and_anchors_never_exceed_canvas(self) -> None:
        canvas = (32, 24)
        _, geometry = self.geometry_for(
            self.default_logo,
            canvas,
            safe_padding=100_000,
            anchor_tolerance=100_000,
        )

        self.assertEqual([0, 0, 32, 24], geometry["safe_zone"])
        self.assertFalse(geometry["right_available"])
        self.assertFalse(geometry["below_available"])

        safe_left, safe_top, safe_right, safe_bottom = geometry["safe_zone"]
        self.assertLessEqual(0, safe_left)
        self.assertLessEqual(safe_left, safe_right)
        self.assertLessEqual(safe_right, canvas[0])
        self.assertLessEqual(0, safe_top)
        self.assertLessEqual(safe_top, safe_bottom)
        self.assertLessEqual(safe_bottom, canvas[1])

        right_x, right_y = geometry["right_module_anchor"]
        below_x, below_y = geometry["below_module_anchor"]
        self.assertTrue(0 <= right_x <= canvas[0])
        self.assertTrue(0 <= right_y <= canvas[1])
        self.assertTrue(0 <= below_x <= canvas[0])
        self.assertTrue(0 <= below_y <= canvas[1])

        for key, limit in (
            ("right_module_start_range", canvas[0]),
            ("below_module_start_range", canvas[1]),
        ):
            start, end = geometry[key]
            self.assertTrue(0 <= start <= end <= limit)

    def test_directory_scan_excludes_logo_output_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "target.jpg"
            logo = root / "logo.png"
            metadata = root / ".xobi" / "work" / "base.png"
            old_output = root / "xobi-img-output" / "old.png"
            output = root / "final"
            output_image = output / "already.png"
            for path, color in (
                (target, "red"),
                (logo, "blue"),
                (metadata, "green"),
                (old_output, "yellow"),
                (output_image, "purple"),
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (40, 40), color).save(path)

            files = apply_logo.iter_inputs(root, output, logo, [])

        self.assertEqual([target.resolve()], files)

    def test_cli_never_overwrites_logo_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "target.png"
            logo = root / "logo.png"
            Image.new("RGB", (100, 100), "white").save(target)
            logo_image = Image.new("RGBA", (80, 30), (0, 0, 0, 0))
            for x in range(10, 70):
                for y in range(5, 25):
                    logo_image.putpixel((x, y), (255, 0, 0, 255))
            logo_image.save(logo)
            before = logo.read_bytes()

            completed = subprocess.run(
                [
                    sys.executable, str(SCRIPTS_DIR / "apply_logo.py"),
                    "--input", str(target),
                    "--output", str(logo),
                    "--logo", str(logo),
                    "--safe-zone-approved",
                    "--overwrite",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )

            self.assertNotEqual(0, completed.returncode)
            self.assertEqual(before, logo.read_bytes())

    def test_cli_requires_current_logo_or_explicit_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "target.png"
            output = root / "output.png"
            Image.new("RGB", (100, 100), "white").save(target)

            missing = subprocess.run(
                [
                    sys.executable, str(SCRIPTS_DIR / "apply_logo.py"),
                    "--input", str(target),
                    "--output", str(output),
                    "--dry-run",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            explicit_default = subprocess.run(
                [
                    sys.executable, str(SCRIPTS_DIR / "apply_logo.py"),
                    "--input", str(target),
                    "--output", str(output),
                    "--use-default-logo",
                    "--dry-run",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )

            self.assertNotEqual(0, missing.returncode)
            self.assertIn("one of the arguments --logo --use-default-logo is required", missing.stderr)
            self.assertEqual(0, explicit_default.returncode, explicit_default.stderr)

    def test_opaque_logo_requires_explicit_review_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "target.png"
            logo = root / "logo.jpg"
            output = root / "output.png"
            Image.new("RGB", (100, 100), "white").save(target)
            Image.new("RGB", (80, 30), "black").save(logo)

            completed = subprocess.run(
                [
                    sys.executable, str(SCRIPTS_DIR / "apply_logo.py"),
                    "--input", str(target),
                    "--output", str(output),
                    "--logo", str(logo),
                    "--safe-zone-approved",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )

            self.assertNotEqual(0, completed.returncode)
            self.assertIn("--opaque-approved", completed.stderr)
            self.assertFalse(output.exists())

    def test_near_opaque_full_canvas_cannot_bypass_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "target.png"
            logo = root / "logo.png"
            output = root / "output.png"
            Image.new("RGB", (100, 100), "white").save(target)
            near_opaque = Image.new("RGBA", (80, 30), (0, 0, 0, 254))
            near_opaque.paste((255, 255, 255, 255), (20, 8, 60, 22))
            near_opaque.save(logo)

            completed = subprocess.run(
                [
                    sys.executable, str(SCRIPTS_DIR / "apply_logo.py"),
                    "--input", str(target),
                    "--output", str(output),
                    "--logo", str(logo),
                    "--safe-zone-approved",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )

            self.assertNotEqual(0, completed.returncode)
            self.assertIn("--opaque-approved", completed.stderr)
            self.assertFalse(output.exists())

    def test_deterministic_jpeg_pixel_check_rejects_small_non_logo_edit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            prepared = root / "prepared.png"
            logo = root / "logo.png"
            output = root / "final.jpg"
            Image.new("RGB", (600, 600), (20, 120, 220)).save(prepared)
            logo_image = Image.new("RGBA", (220, 90), (0, 0, 0, 0))
            logo_image.paste((230, 80, 20, 255), (10, 10, 210, 80))
            logo_image.save(logo)
            completed = subprocess.run(
                [
                    sys.executable, str(SCRIPTS_DIR / "apply_logo.py"),
                    "--input", str(prepared),
                    "--output", str(output),
                    "--logo", str(logo),
                    "--safe-zone-approved",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertEqual([], manifest_utils.validate_logo_overlay_pixels(prepared, output, logo, "JPEG"))

            with Image.open(prepared) as raw:
                malicious = raw.convert("RGBA")
            overlay, _ = manifest_utils.standard_logo_overlay_and_geometry(logo, malicious.size)
            malicious.alpha_composite(overlay, (0, 0))
            malicious.paste((255, 255, 255, 255), (540, 540, 580, 580))
            malicious.convert("RGB").save(
                output,
                "JPEG",
                quality=95,
                optimize=True,
                progressive=True,
                subsampling=0,
            )
            errors = manifest_utils.validate_logo_overlay_pixels(prepared, output, logo, "JPEG")
            self.assertTrue(any("deterministic Logo composite encoding" in error for error in errors), errors)

    def test_directory_batch_validates_all_inputs_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            inputs = root / "inputs"
            output = root / "outputs"
            logo = root / "logo.png"
            inputs.mkdir()
            Image.new("RGB", (100, 100), "white").save(inputs / "good.png")
            (inputs / "broken.png").write_bytes(b"broken")
            logo_image = Image.new("RGBA", (80, 30), (0, 0, 0, 0))
            for x in range(10, 70):
                for y in range(5, 25):
                    logo_image.putpixel((x, y), (255, 0, 0, 255))
            logo_image.save(logo)

            completed = subprocess.run(
                [
                    sys.executable, str(SCRIPTS_DIR / "apply_logo.py"),
                    "--input", str(inputs),
                    "--output", str(output),
                    "--logo", str(logo),
                    "--safe-zone-approved",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )

            self.assertNotEqual(0, completed.returncode)
            self.assertFalse(output.exists())

    def test_geometry_json_is_limited_to_non_control_work_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "target.png"
            logo = root / "logo.png"
            output = root / "output.png"
            Image.new("RGB", (100, 100), "white").save(target)
            logo_image = Image.new("RGBA", (80, 30), (0, 0, 0, 0))
            logo_image.paste((255, 0, 0, 255), (10, 5, 70, 25))
            logo_image.save(logo)

            allowed = root / ".xobi" / "work" / "logo_geometry.json"
            accepted = subprocess.run(
                [
                    sys.executable, str(SCRIPTS_DIR / "apply_logo.py"),
                    "--input", str(target),
                    "--output", str(output),
                    "--logo", str(logo),
                    "--dry-run",
                    "--geometry-json", str(allowed),
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(0, accepted.returncode, accepted.stderr)
            self.assertTrue(allowed.is_file())

            blocked_paths = (
                root / ".xobi" / "manifest.json",
                root / ".xobi" / "source" / "geometry.json",
                root / ".xobi" / "work" / "logo_plan.json",
                root / ".xobi" / "work" / "layout_families.json",
                root / ".xobi" / "work" / "family-pilot.json",
                root / ".xobi" / "work" / "task-state" / "task-000001.json",
            )
            for blocked in blocked_paths:
                with self.subTest(blocked=blocked):
                    completed = subprocess.run(
                        [
                            sys.executable, str(SCRIPTS_DIR / "apply_logo.py"),
                            "--input", str(target),
                            "--output", str(output),
                            "--logo", str(logo),
                            "--dry-run",
                            "--geometry-json", str(blocked),
                            "--overwrite",
                        ],
                        cwd=REPO_ROOT,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        check=False,
                    )
                    self.assertNotEqual(0, completed.returncode)
                    self.assertFalse(blocked.exists())


class NormalizeLogoTests(unittest.TestCase):
    def test_auto_mode_trims_transparent_border(self) -> None:
        image = Image.new("RGBA", (120, 90), (0, 0, 0, 0))
        content = Image.new("RGBA", (70, 40), (240, 80, 30, 255))
        image.alpha_composite(content, (20, 15))

        bbox, mode, background, warning = normalize_logo.content_bbox(
            image,
            "auto",
            10,
            18,
            8,
        )

        self.assertEqual((20, 15, 90, 55), bbox)
        self.assertEqual("transparent", mode)
        self.assertIsNone(background)
        self.assertIsNone(warning)

    def test_auto_mode_preserves_opaque_logo_canvas(self) -> None:
        image = Image.new("RGBA", (300, 100), (0, 0, 0, 255))
        content = Image.new("RGBA", (100, 40), (255, 255, 255, 255))
        image.alpha_composite(content, (100, 30))

        bbox, mode, background, warning = normalize_logo.content_bbox(
            image,
            "auto",
            10,
            18,
            24,
        )

        self.assertEqual((0, 0, 300, 100), bbox)
        self.assertEqual("opaque-preserved", mode)
        self.assertIsNone(background)
        self.assertIsNotNone(warning)

    def test_auto_mode_treats_near_opaque_edge_reaching_canvas_as_review_only(self) -> None:
        image = Image.new("RGBA", (300, 100), (0, 0, 0, 254))
        image.paste((255, 255, 255, 255), (100, 30, 200, 70))

        bbox, mode, background, warning = normalize_logo.content_bbox(
            image,
            "auto",
            10,
            18,
            24,
        )

        self.assertEqual((0, 0, 300, 100), bbox)
        self.assertEqual("opaque-preserved", mode)
        self.assertIsNone(background)
        self.assertIsNotNone(warning)

    def test_opaque_auto_mode_is_review_only_and_output_must_be_png(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "logo.jpg"
            Image.new("RGB", (200, 80), "white").save(source)
            auto_output = root / "auto.png"
            wrong_suffix = root / "normalized.jpg"

            auto = subprocess.run(
                [
                    sys.executable, str(SCRIPTS_DIR / "normalize_logo.py"),
                    "--input", str(source),
                    "--output", str(auto_output),
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            suffix = subprocess.run(
                [
                    sys.executable, str(SCRIPTS_DIR / "normalize_logo.py"),
                    "--input", str(source),
                    "--output", str(wrong_suffix),
                    "--background", "white",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )

            self.assertNotEqual(0, auto.returncode)
            self.assertIn("review-only", auto.stderr)
            self.assertFalse(auto_output.exists())
            self.assertNotEqual(0, suffix.returncode)
            self.assertIn(".png suffix", suffix.stderr)
            self.assertFalse(wrong_suffix.exists())

    def test_metadata_json_cannot_overwrite_task_control_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "logo.png"
            output = root / "normalized.png"
            image = Image.new("RGBA", (120, 90), (0, 0, 0, 0))
            image.paste((240, 80, 30, 255), (20, 15, 90, 55))
            image.save(source)

            allowed = root / ".xobi" / "work" / "logo_metadata.json"
            accepted = subprocess.run(
                [
                    sys.executable, str(SCRIPTS_DIR / "normalize_logo.py"),
                    "--input", str(source),
                    "--output", str(output),
                    "--metadata-json", str(allowed),
                    "--dry-run",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(0, accepted.returncode, accepted.stderr)
            self.assertTrue(allowed.is_file())

            blocked_paths = (
                root / ".xobi" / "manifest.json",
                root / ".xobi" / "source" / "metadata.json",
                root / ".xobi" / "work" / "localization_plan.json",
                root / ".xobi" / "work" / "layout_families.json",
                root / ".xobi" / "work" / "family-pilot.json",
                root / ".xobi" / "work" / "task-state" / "task-000001.json",
            )
            for blocked in blocked_paths:
                with self.subTest(blocked=blocked):
                    completed = subprocess.run(
                        [
                            sys.executable, str(SCRIPTS_DIR / "normalize_logo.py"),
                            "--input", str(source),
                            "--output", str(output),
                            "--metadata-json", str(blocked),
                            "--dry-run",
                            "--overwrite",
                        ],
                        cwd=REPO_ROOT,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        check=False,
                    )
                    self.assertNotEqual(0, completed.returncode)
                    self.assertFalse(blocked.exists())


if __name__ == "__main__":
    unittest.main()
