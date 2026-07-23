from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageChops, ImageOps


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "create_main_image_review.py"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class MainImageReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.task_dir = self.root / "task"
        self.metadata_dir = self.task_dir / ".xobi"
        self.work_dir = self.metadata_dir / "work"
        self.work_dir.mkdir(parents=True)
        self.output = self.task_dir / "final.png"
        self.source = self.root / "source.png"
        Image.new("RGB", (400, 200), "#5b8def").save(self.source)
        Image.new("RGB", (400, 200), "#e9c46a").save(self.output)
        self.plan = self.work_dir / "main-image-plan.json"
        self.plan.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "contract": "commerce-main-image-plan-v1",
                    "task_id": "task-000001",
                    "operation": "重做高级电商主图",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        self.manifest_id = "xobi-0123456789abcdef0123456789abcdef"
        self.task_id = "task-000001"
        self.manifest = self.metadata_dir / "manifest.json"
        self.write_manifest()
        self.assessment = self.work_dir / "assessment.json"
        self.environment = os.environ.copy()
        self.environment["PYTHONIOENCODING"] = "utf-8"
        self.environment["PYTHONUTF8"] = "1"

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write_manifest(self) -> None:
        source_digest = sha256_file(self.source)
        self.manifest.write_text(
            json.dumps(
                {
                    "schema_version": 4,
                    "manifest_id": self.manifest_id,
                    "workflow": "commerce_main_image",
                    "mode": "edit",
                    "operation": "重做高级电商主图",
                    "ratio": "2:1",
                    "output_format": "png",
                    "alpha_policy": "preserve",
                    "task_dir": str(self.task_dir),
                    "items": [
                        {
                            "task_id": self.task_id,
                            "source": str(self.source),
                            "source_sha256": source_digest,
                            "output": str(self.output),
                            "width": 400,
                            "height": 200,
                            "format": "PNG",
                            "has_alpha": False,
                            "has_transparency": False,
                            "main_image_plan_registration": {
                                "schema_version": 1,
                                "producer": "xobi-img.update_manifest",
                                "contract": "frozen-commerce-main-image-plan-v1",
                                "manifest_id": self.manifest_id,
                                "task_id": self.task_id,
                                "source_sha256": source_digest,
                                "path": str(self.plan),
                                "sha256": sha256_file(self.plan),
                            },
                        }
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def run_cli(
        self,
        phase: str,
        *arguments: object,
        candidate: Path | None = None,
        plan: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                phase,
                "--manifest",
                str(self.manifest),
                "--task-id",
                self.task_id,
                "--candidate",
                str(candidate or self.output),
                "--plan-json",
                str(plan or self.plan),
                *(str(argument) for argument in arguments),
            ],
            cwd=REPO_ROOT,
            env=self.environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )

    @staticmethod
    def path_from_stdout(stdout: str, key: str) -> Path:
        line = next(value for value in stdout.splitlines() if value.startswith(f"{key}="))
        return Path(line.split("=", 1)[1])

    def prepare(self, *arguments: object) -> tuple[subprocess.CompletedProcess[str], Path]:
        completed = self.run_cli("prepare", *arguments)
        self.assertEqual(0, completed.returncode, completed.stderr)
        return completed, self.path_from_stdout(completed.stdout, "evidence_dir")

    def completed_assessment(self, evidence_dir: Path) -> dict[str, object]:
        value = json.loads((evidence_dir / "assessment-template.json").read_text(encoding="utf-8"))
        value["scores"].update(  # type: ignore[union-attr]
            {
                "visual_hierarchy": 5,
                "product_fidelity": 4,
                "material_realism": 4,
                "typography": 4,
                "spacing": 5,
                "commercial_polish": 4,
                "thumbnail_readability": 5,
            }
        )
        value["required_checks"].update(  # type: ignore[union-attr]
            {
                "single_focal_point": True,
                "product_priority": True,
                "clear_hierarchy": True,
                "safe_margins": True,
                "realistic_scale_and_shadow": True,
                "no_invented_claims": True,
            }
        )
        value["hard_rejects"].update(  # type: ignore[union-attr]
            {
                "cheap_banner": False,
                "random_badge": False,
                "thick_outline": False,
                "oval_sticker_collage": False,
                "clutter": False,
                "fake_3d": False,
                "oversaturation": False,
                "invented_claim": False,
            }
        )
        for view in value["views"].values():  # type: ignore[union-attr]
            view["passed"] = True
            view["notes"] = "reviewed"
        value["notes"] = "full, 256 and 160 views reviewed"
        return value

    def write_assessment(self, value: dict[str, object], path: Path | None = None) -> Path:
        target = path or self.assessment
        target.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return target

    def finalize(
        self,
        evidence_dir: Path,
        assessment: Path | None = None,
        *arguments: object,
    ) -> subprocess.CompletedProcess[str]:
        return self.run_cli(
            "finalize",
            "--evidence-dir",
            evidence_dir,
            "--assessment-json",
            assessment or self.assessment,
            *arguments,
        )

    def test_prepare_and_finalize_create_hash_bound_proportional_review(self) -> None:
        candidate_before = self.output.read_bytes()

        prepared, evidence_dir = self.prepare()
        self.assertIn("assessment_template=", prepared.stdout)
        assessment = self.completed_assessment(evidence_dir)
        self.write_assessment(assessment)
        completed = self.finalize(evidence_dir)

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("passed=true", completed.stdout)
        review_path = self.path_from_stdout(completed.stdout, "review")
        review = json.loads(review_path.read_text(encoding="utf-8"))
        evidence = json.loads((evidence_dir / "evidence.json").read_text(encoding="utf-8"))
        self.assertEqual("commerce-main-image-quality-review-v1", review["contract"])
        self.assertEqual(sha256_file(self.output), review["output"]["sha256"])
        self.assertEqual(evidence["candidate"], review["output"])
        full_view = review["views"]["full"]
        full_path = Path(full_view["path"])
        self.assertNotEqual(review["output"]["path"], full_view["path"])
        self.assertEqual(candidate_before, full_path.read_bytes())
        self.assertEqual(review["output"]["sha256"], full_view["sha256"])
        self.assertTrue(full_view["passed"])
        self.assertEqual(sha256_file(self.plan), review["plan"]["sha256"])
        self.assertEqual([160, 256], review["thumbnail_sizes"])
        self.assertTrue(review["passed"])
        self.assertTrue(all(review["criteria"].values()))
        self.assertEqual(sha256_file(self.assessment), review["assessment"]["sha256"])
        self.assertEqual(sha256_file(evidence_dir / "evidence.json"), review["evidence"]["sha256"])
        self.assertEqual(candidate_before, self.output.read_bytes())
        self.assertEqual(evidence["candidate"]["sha256"], review["views"]["full"]["sha256"])
        for size, expected_dimensions in ((256, (256, 128)), (160, (160, 80))):
            view = review["views"][str(size)]
            thumbnail_path = Path(view["path"])
            self.assertTrue(thumbnail_path.is_file())
            self.assertEqual(sha256_file(thumbnail_path), view["sha256"])
            with Image.open(thumbnail_path) as thumbnail:
                self.assertEqual(expected_dimensions, thumbnail.size)
            self.assertTrue(view["passed"])

    def test_failed_visual_assessment_is_recorded_with_independent_view_results(self) -> None:
        _, evidence_dir = self.prepare()
        assessment = self.completed_assessment(evidence_dir)
        assessment["scores"]["commercial_polish"] = 2  # type: ignore[index]
        assessment["views"]["160"]["passed"] = False  # type: ignore[index]
        assessment["views"]["160"]["notes"] = "too small"  # type: ignore[index]
        self.write_assessment(assessment)

        completed = self.finalize(evidence_dir)

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("passed=false", completed.stdout)
        review = json.loads(self.path_from_stdout(completed.stdout, "review").read_text(encoding="utf-8"))
        self.assertFalse(review["passed"])
        self.assertFalse(review["views"]["full"]["passed"])
        self.assertTrue(review["views"]["256"]["passed"])
        self.assertFalse(review["views"]["160"]["passed"])

    def test_rejects_candidate_that_is_not_preallocated_output(self) -> None:
        other = self.task_dir / "other.png"
        Image.new("RGB", (400, 200), "red").save(other)

        completed = self.run_cli("prepare", candidate=other)

        self.assertNotEqual(0, completed.returncode)
        self.assertIn("preallocated", completed.stderr)

    def test_rejects_changed_frozen_plan_and_outside_evidence_directory(self) -> None:
        original_plan = self.plan.read_bytes()
        self.plan.write_text("{}\n", encoding="utf-8")

        changed_plan = self.run_cli("prepare")

        self.assertNotEqual(0, changed_plan.returncode)
        self.assertIn("plan hash changed", changed_plan.stderr)
        self.plan.write_bytes(original_plan)

        outside = self.root / "outside"
        rejected_output = self.run_cli("prepare", "--output-dir", outside)
        self.assertNotEqual(0, rejected_output.returncode)
        self.assertIn("inside .xobi/work", rejected_output.stderr)

    def test_rejects_incomplete_assessment_without_creating_review(self) -> None:
        _, evidence_dir = self.prepare()
        assessment = self.completed_assessment(evidence_dir)
        del assessment["scores"]["typography"]  # type: ignore[index]
        self.write_assessment(assessment)

        completed = self.finalize(evidence_dir)

        self.assertNotEqual(0, completed.returncode)
        self.assertIn("score keys", completed.stderr)
        self.assertFalse((evidence_dir / "review.json").exists())

    def test_rejects_multiframe_candidate_but_accepts_static_candidate(self) -> None:
        static_prepared, static_dir = self.prepare("--output-dir", self.work_dir / "static-review")
        self.assertEqual(0, static_prepared.returncode)
        self.assertTrue((static_dir / "evidence.json").is_file())

        first = Image.new("RGB", (400, 200), "green")
        second = Image.new("RGB", (400, 200), "red")
        first.save(
            self.output,
            format="PNG",
            save_all=True,
            append_images=[second],
            duration=1000,
            loop=0,
        )
        with Image.open(self.output) as animated:
            self.assertEqual(2, animated.n_frames)

        rejected = self.run_cli("prepare", "--output-dir", self.work_dir / "animated-review")

        self.assertNotEqual(0, rejected.returncode)
        self.assertIn("single-frame", rejected.stderr)
        self.assertFalse((self.work_dir / "animated-review").exists())

    def test_rejects_task_candidate_plan_and_view_binding_changes(self) -> None:
        _, evidence_dir = self.prepare()
        baseline = self.completed_assessment(evidence_dir)
        mutations = {
            "manifest": lambda value: value.__setitem__(
                "manifest_id", "xobi-ffffffffffffffffffffffffffffffff"
            ),
            "task": lambda value: value.__setitem__("task_id", "task-999999"),
            "candidate": lambda value: value["candidate"].__setitem__("sha256", "0" * 64),
            "plan": lambda value: value["plan"].__setitem__("sha256", "1" * 64),
            "view": lambda value: value["views"]["160"].__setitem__("sha256", "2" * 64),
        }
        for name, mutate in mutations.items():
            with self.subTest(binding=name):
                assessment = json.loads(json.dumps(baseline))
                mutate(assessment)
                self.write_assessment(assessment)
                rejected = self.finalize(evidence_dir)
                self.assertNotEqual(0, rejected.returncode)
                self.assertIn("does not match", rejected.stderr)
                self.assertFalse((evidence_dir / "review.json").exists())

    def test_rejects_assessment_replayed_against_new_candidate_evidence(self) -> None:
        _, first_dir = self.prepare()
        first_assessment = self.completed_assessment(first_dir)
        stale_assessment = self.write_assessment(
            first_assessment,
            self.work_dir / "stale-assessment.json",
        )

        Image.new("RGB", (400, 200), "#264653").save(self.output)
        _, second_dir = self.prepare()

        rejected = self.finalize(second_dir, stale_assessment)

        self.assertNotEqual(0, rejected.returncode)
        self.assertIn("assessment candidate does not match", rejected.stderr)
        self.assertFalse((second_dir / "review.json").exists())

    def test_rejects_tampered_thumbnail_and_keeps_snapshot_hashes_consistent(self) -> None:
        _, evidence_dir = self.prepare()
        evidence = json.loads((evidence_dir / "evidence.json").read_text(encoding="utf-8"))
        self.assertEqual(sha256_file(self.output), evidence["candidate"]["sha256"])
        full_view = evidence["views"]["full"]
        self.assertNotEqual(evidence["candidate"]["path"], full_view["path"])
        self.assertEqual(evidence["candidate"]["sha256"], full_view["sha256"])
        self.assertEqual(self.output.read_bytes(), Path(full_view["path"]).read_bytes())
        self.assertEqual(sha256_file(self.plan), evidence["plan"]["sha256"])

        with Image.open(self.output) as raw:
            expected_full = ImageOps.exif_transpose(raw).convert("RGBA")
            expected_full.load()
        for size in (256, 160):
            expected = expected_full.copy()
            expected.thumbnail((size, size), Image.Resampling.LANCZOS, reducing_gap=3.0)
            with Image.open(evidence_dir / f"thumbnail-{size}.png") as actual:
                actual_rgba = actual.convert("RGBA")
                self.assertIsNone(ImageChops.difference(expected, actual_rgba).getbbox())

        assessment = self.completed_assessment(evidence_dir)
        self.write_assessment(assessment)
        Image.new("RGB", (256, 128), "black").save(evidence_dir / "thumbnail-256.png")

        rejected = self.finalize(evidence_dir)

        self.assertNotEqual(0, rejected.returncode)
        self.assertIn("exact candidate-derived evidence", rejected.stderr)
        self.assertFalse((evidence_dir / "review.json").exists())

    def test_rejects_tampered_full_snapshot(self) -> None:
        _, evidence_dir = self.prepare()
        assessment = self.completed_assessment(evidence_dir)
        self.write_assessment(assessment)
        evidence = json.loads((evidence_dir / "evidence.json").read_text(encoding="utf-8"))
        full_path = Path(evidence["views"]["full"]["path"])
        full_path.write_bytes(b"tampered full-size evidence")

        rejected = self.finalize(evidence_dir)

        self.assertNotEqual(0, rejected.returncode)
        self.assertIn("full-size snapshot is not the exact candidate-derived evidence", rejected.stderr)
        self.assertFalse((evidence_dir / "review.json").exists())

    def test_failed_review_keeps_full_snapshot_after_candidate_is_overwritten(self) -> None:
        candidate_before = self.output.read_bytes()
        _, evidence_dir = self.prepare()
        assessment = self.completed_assessment(evidence_dir)
        assessment["scores"]["commercial_polish"] = 2  # type: ignore[index]
        assessment["views"]["full"]["passed"] = False  # type: ignore[index]
        assessment["views"]["full"]["notes"] = "candidate rejected"  # type: ignore[index]
        self.write_assessment(assessment)
        finalized = self.finalize(evidence_dir)
        self.assertEqual(0, finalized.returncode, finalized.stderr)

        review = json.loads(
            self.path_from_stdout(finalized.stdout, "review").read_text(encoding="utf-8")
        )
        full_path = Path(review["views"]["full"]["path"])
        self.assertEqual(candidate_before, full_path.read_bytes())
        self.assertEqual(hashlib.sha256(candidate_before).hexdigest(), review["views"]["full"]["sha256"])

        Image.new("RGB", (400, 200), "#264653").save(self.output)

        self.assertNotEqual(candidate_before, self.output.read_bytes())
        self.assertEqual(candidate_before, full_path.read_bytes())
        self.assertTrue((evidence_dir / "review.json").is_file())

    def test_prepare_and_finalize_never_overwrite_existing_outputs(self) -> None:
        _, evidence_dir = self.prepare()
        evidence_before = {
            path.name: path.read_bytes()
            for path in evidence_dir.iterdir()
            if path.is_file()
        }

        repeated_prepare = self.run_cli("prepare")

        self.assertNotEqual(0, repeated_prepare.returncode)
        self.assertIn("already exists", repeated_prepare.stderr)
        self.assertEqual(
            evidence_before,
            {
                path.name: path.read_bytes()
                for path in evidence_dir.iterdir()
                if path.is_file()
            },
        )

        self.write_assessment(self.completed_assessment(evidence_dir))
        finalized = self.finalize(evidence_dir)
        self.assertEqual(0, finalized.returncode, finalized.stderr)
        review_path = evidence_dir / "review.json"
        review_before = review_path.read_bytes()

        repeated_finalize = self.finalize(evidence_dir)

        self.assertNotEqual(0, repeated_finalize.returncode)
        self.assertIn("already exists", repeated_finalize.stderr)
        self.assertEqual(review_before, review_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
