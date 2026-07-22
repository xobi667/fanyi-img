from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "resample_image.py"


class ResampleImageTests(unittest.TestCase):
    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *arguments],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

    def test_transparent_png_1200_to_800_preserves_alpha_and_icc(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source.png"
            output = root / "output.png"
            image = Image.new("RGBA", (1200, 1200), (25, 80, 140, 255))
            image.paste((220, 40, 20, 0), (0, 0, 300, 1200))
            image.save(source, icc_profile=b"xobi-img-test-icc")
            source_before = source.read_bytes()

            completed = self.run_cli(
                "--input", str(source),
                "--output", str(output),
                "--size", "800x800",
                "--output-format", "png",
            )

            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertEqual(source_before, source.read_bytes())
            with Image.open(output) as result:
                self.assertEqual("PNG", result.format)
                self.assertEqual((800, 800), result.size)
                self.assertEqual("RGBA", result.mode)
                self.assertLess(result.getchannel("A").getextrema()[0], 255)
                self.assertEqual(b"xobi-img-test-icc", result.info.get("icc_profile"))

    def test_rejects_ratio_change_without_touching_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source.png"
            output = root / "output.png"
            Image.new("RGB", (1200, 800), "red").save(source)
            output.write_bytes(b"existing-output")

            completed = self.run_cli(
                "--input", str(source),
                "--output", str(output),
                "--size", "800x800",
                "--output-format", "png",
                "--overwrite",
            )

            self.assertNotEqual(0, completed.returncode)
            self.assertIn("only same-ratio resampling is allowed", completed.stderr)
            self.assertEqual(b"existing-output", output.read_bytes())

    def test_rejects_transparent_png_to_jpeg(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source.png"
            output = root / "output.jpg"
            Image.new("RGBA", (120, 80), (255, 0, 0, 80)).save(source)

            completed = self.run_cli(
                "--input", str(source),
                "--output", str(output),
                "--size", "90x60",
                "--output-format", "jpg",
            )

            self.assertNotEqual(0, completed.returncode)
            self.assertIn("cannot preserve transparent pixels", completed.stderr)
            self.assertFalse(output.exists())

    def test_output_format_controls_real_encoding(self) -> None:
        cases = (
            ("png", ".png", "PNG"),
            ("jpg", ".jpg", "JPEG"),
            ("jpeg", ".jpg", "JPEG"),
            ("webp", ".webp", "WEBP"),
            ("bmp", ".bmp", "BMP"),
            ("tiff", ".tiff", "TIFF"),
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source.png"
            Image.new("RGB", (120, 80), "navy").save(source)
            for requested, suffix, expected in cases:
                with self.subTest(requested=requested):
                    output = root / f"output-{requested}{suffix}"
                    completed = self.run_cli(
                        "--input", str(source),
                        "--output", str(output),
                        "--size", "90x60",
                        "--output-format", requested,
                    )
                    self.assertEqual(0, completed.returncode, completed.stderr)
                    with Image.open(output) as result:
                        self.assertEqual(expected, result.format)
                        self.assertEqual((90, 60), result.size)

    def test_source_format_and_same_path_guard(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source.webp"
            output = root / "output.webp"
            Image.new("RGB", (120, 80), "green").save(source, format="WEBP", lossless=True)

            completed = self.run_cli(
                "--input", str(source),
                "--output", str(output),
                "--size", "90x60",
                "--output-format", "source",
            )
            same_path = self.run_cli(
                "--input", str(source),
                "--output", str(source),
                "--size", "90x60",
                "--output-format", "source",
                "--overwrite",
            )

            self.assertEqual(0, completed.returncode, completed.stderr)
            with Image.open(output) as result:
                self.assertEqual("WEBP", result.format)
            self.assertNotEqual(0, same_path.returncode)
            self.assertIn("source images are read-only", same_path.stderr)


if __name__ == "__main__":
    unittest.main()
