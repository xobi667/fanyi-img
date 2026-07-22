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
SCRIPTS_DIR = REPO_ROOT / "scripts"
PREFLIGHT = SCRIPTS_DIR / "preflight_images.py"
UPDATE = SCRIPTS_DIR / "update_manifest.py"
VERIFY = SCRIPTS_DIR / "verify_manifest.py"


class GenerateManifestTests(unittest.TestCase):
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

    def generate_preflight(self, variants: int = 6) -> tuple[Path, dict[str, object]]:
        completed = self.run_cli(
            PREFLIGHT,
            "--mode", "generate",
            "--operation", "生成蓝色水杯商品图",
            "--ratio", "1：1",
            "--variants", variants,
            "--workers", 4,
            "--output-root", self.output_root,
            "--task-name", "generate-test",
        )
        manifest_lines = [
            line.removeprefix("manifest=")
            for line in completed.stdout.splitlines()
            if line.startswith("manifest=")
        ]
        self.assertEqual(1, len(manifest_lines), completed.stdout)
        manifest_path = Path(manifest_lines[0])
        return manifest_path, json.loads(manifest_path.read_text(encoding="utf-8"))

    def test_generate_without_input_preallocates_variants_across_four_workers(self) -> None:
        manifest_path, manifest = self.generate_preflight(6)

        self.assertEqual("generate", manifest["mode"])
        self.assertIsNone(manifest["input"])
        self.assertEqual(6, manifest["variants"])
        self.assertEqual(4, manifest["workers_active"])
        self.assertEqual("parallel", manifest["execution_mode"])
        items = manifest["items"]
        self.assertEqual(
            [f"variant-{index:03d}" for index in range(1, 7)],
            [item["task_id"] for item in items],
        )
        self.assertEqual(
            [f"variant-{index:03d}.png" for index in range(1, 7)],
            [Path(item["output"]).name for item in items],
        )
        self.assertEqual(
            ["worker-1", "worker-2", "worker-3", "worker-4", "worker-1", "worker-2"],
            [item["worker_id"] for item in items],
        )
        self.assertTrue(all(item["source"] == "" for item in items))
        self.assertTrue(all(item["source_sha256"] is None for item in items))
        self.assertEqual(
            [],
            json.loads((manifest_path.parent / "source" / "source_paths.json").read_text(encoding="utf-8")),
        )

        verified = self.run_cli(VERIFY, "--manifest", manifest_path, "--allow-pending")
        verification = json.loads(verified.stdout)
        self.assertTrue(verification["valid"])
        self.assertEqual([], verification["errors"])

    def test_source_free_generate_tasks_can_update_and_verify(self) -> None:
        manifest_path, manifest = self.generate_preflight(2)
        for index, item in enumerate(manifest["items"], start=1):
            output = Path(item["output"])
            Image.new("RGB", (12, 12), (index * 70, 40, 220 - index * 50)).save(output, format="PNG")
            self.run_cli(
                UPDATE,
                "--manifest", manifest_path,
                "--task-id", item["task_id"],
                "--worker-id", item["worker_id"],
                "--status", "success",
                "--attempts", 1,
                "--output", output,
                "--prompt-summary", f"variant {index}",
            )

        verified = self.run_cli(VERIFY, "--manifest", manifest_path)
        verification = json.loads(verified.stdout)
        self.assertTrue(verification["valid"])
        self.assertEqual(2, verification["success"])
        self.assertEqual([], verification["errors"])

    def test_generate_rejects_original_ratio_and_source_output_format(self) -> None:
        for arguments, expected_error in (
            (("--ratio", "original"), "'original' is not allowed"),
            (("--ratio", "保持原比例"), "'original' is not allowed"),
            (("--ratio", "1:1", "--output-format", "source"), "unavailable in generate mode"),
        ):
            with self.subTest(arguments=arguments):
                completed = self.run_cli(
                    PREFLIGHT,
                    "--mode", "generate",
                    "--operation", "生成商品图",
                    *arguments,
                    "--output-root", self.output_root,
                    check=False,
                )
                self.assertNotEqual(0, completed.returncode)
                self.assertIn(expected_error, completed.stderr)
        self.assertFalse(self.output_root.exists())

    def test_input_modes_still_require_input_and_variants_are_generate_only(self) -> None:
        missing_input = self.run_cli(
            PREFLIGHT,
            "--mode", "edit",
            "--operation", "换背景",
            "--ratio", "1:1",
            "--output-root", self.output_root,
            check=False,
        )
        self.assertNotEqual(0, missing_input.returncode)
        self.assertIn("--input is required", missing_input.stderr)

        input_file = self.root / "source.png"
        Image.new("RGB", (8, 8), (20, 80, 140)).save(input_file, format="PNG")
        invalid_variants = self.run_cli(
            PREFLIGHT,
            "--input", input_file,
            "--mode", "edit",
            "--operation", "换背景",
            "--ratio", "1:1",
            "--variants", 2,
            "--output-root", self.output_root,
            check=False,
        )
        self.assertNotEqual(0, invalid_variants.returncode)
        self.assertIn("only available in generate mode", invalid_variants.stderr)


if __name__ == "__main__":
    unittest.main()
