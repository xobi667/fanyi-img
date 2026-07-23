from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
from typing import Iterator
import unittest

from PIL import Image, ImageDraw

from tests import test_commerce_logo_flows as logo_flows
from tests import test_commerce_main_image as main_image


class AttemptBypassRegressionTests(unittest.TestCase):
    @contextmanager
    def logo_fixture(self) -> Iterator[logo_flows.CommerceLogoFlowTests]:
        fixture = logo_flows.CommerceLogoFlowTests(
            "test_commerce_main_image_direct_overlay_completes_without_a_logo_attempt"
        )
        fixture.setUp()
        try:
            yield fixture
        finally:
            fixture.tearDown()

    @contextmanager
    def main_image_fixture(self) -> Iterator[main_image.CommerceMainImageTests]:
        fixture = main_image.CommerceMainImageTests(
            "test_preflight_requires_explicit_route_and_creative_authorization"
        )
        fixture.setUp()
        try:
            yield fixture
        finally:
            fixture.tearDown()

    def prepare_logo_conflict(
        self,
        fixture: logo_flows.CommerceLogoFlowTests,
    ) -> dict[str, object]:
        logo = fixture.make_logo()
        manifest_path, manifest = fixture.preflight(logo)
        item = manifest["items"][0]
        plan_path = fixture.register_main_image_plan(manifest_path, manifest, item)
        output = Path(str(item["output"]))
        fixture.render_candidate(output, conflict=True)
        review_path = fixture.finalized_review(
            manifest_path,
            item,
            plan_path,
            passed=True,
        )
        accepted_base = manifest_path.parent / "work" / "accepted-main-image.png"
        accepted_base.write_bytes(output.read_bytes())
        fixture.record_main_image_candidate(
            manifest_path,
            item,
            attempt=1,
            review_path=review_path,
            accepted_base=accepted_base,
        )

        geometry_path, geometry = fixture.dry_run_logo(
            manifest_path,
            item,
            accepted_base,
            logo,
        )
        anchor_left = int(geometry["right_module_start_range"][0])
        prepared_bbox = [anchor_left, 4, anchor_left + 56, 24]
        anchors = [{
            "module_id": "module-01",
            "placement": "right",
            "prepared_bbox": prepared_bbox,
        }]
        logo_plan_path, anchors_path = fixture.write_logo_plan(
            manifest_path,
            manifest,
            item,
            geometry,
            decision="regenerate_for_conflict",
            anchors=anchors,
        )
        fixture.run_cli(
            logo_flows.UPDATE,
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
        return {
            "manifest_path": manifest_path,
            "item": item,
            "logo": logo,
            "accepted_base": accepted_base,
            "prepared_bbox": prepared_bbox,
        }

    def prepare_accepted_main_image(
        self,
        fixture: main_image.CommerceMainImageTests,
    ) -> dict[str, object]:
        manifest_path, manifest = fixture.preflight()
        item = manifest["items"][0]
        plan_path = fixture.write_plan(manifest_path, manifest, item)
        fixture.register_plan(manifest_path, item, plan_path)
        output = Path(str(item["output"]))
        Image.new("RGB", (320, 320), (230, 224, 210)).save(output, format="PNG")
        review_path = fixture.make_review(
            manifest_path,
            manifest,
            item,
            passed=True,
        )
        fixture.run_cli(
            main_image.UPDATE,
            "--manifest", manifest_path,
            "--task-id", item["task_id"],
            "--worker-id", item["worker_id"],
            "--status", "success",
            "--attempts", 1,
            "--attempt-stage", "commerce_main_image",
            "--main-image-quality-review-json", review_path,
        )
        return {
            "manifest_path": manifest_path,
            "manifest": manifest,
            "item": item,
            "output": output,
        }

    def test_logo_conflict_candidate_attempts_require_prepared_base_evidence(self) -> None:
        for failure_type in ("quality", None):
            with self.subTest(failure_type=failure_type):
                with self.logo_fixture() as fixture:
                    context = self.prepare_logo_conflict(fixture)
                    manifest_path = context["manifest_path"]
                    item = context["item"]
                    before = manifest_path.read_bytes()
                    arguments: list[object] = [
                        "--manifest", manifest_path,
                        "--task-id", item["task_id"],
                        "--worker-id", item["worker_id"],
                        "--status", "pending",
                        "--attempts", 2,
                        "--attempt-stage", "logo_conflict",
                    ]
                    if failure_type is not None:
                        arguments.extend([
                            "--failure-type", failure_type,
                            "--error", "candidate rejected",
                        ])
                    rejected = fixture.run_cli(
                        logo_flows.UPDATE,
                        *arguments,
                        check=False,
                    )
                    self.assertNotEqual(0, rejected.returncode)
                    self.assertEqual(before, manifest_path.read_bytes())

    def test_logo_combined_main_image_acceptance_requires_frozen_base_output(self) -> None:
        with self.logo_fixture() as fixture:
            logo = fixture.make_logo()
            manifest_path, manifest = fixture.preflight(logo)
            item = manifest["items"][0]
            plan_path = fixture.register_main_image_plan(manifest_path, manifest, item)
            output = Path(str(item["output"]))
            fixture.render_candidate(output, conflict=False)
            review_path = fixture.finalized_review(
                manifest_path,
                item,
                plan_path,
                passed=True,
            )

            before = manifest_path.read_bytes()
            rejected = fixture.run_cli(
                logo_flows.UPDATE,
                "--manifest", manifest_path,
                "--task-id", item["task_id"],
                "--worker-id", item["worker_id"],
                "--status", "pending",
                "--attempts", 1,
                "--attempt-stage", "commerce_main_image",
                "--main-image-quality-review-json", review_path,
                check=False,
            )
            self.assertNotEqual(0, rejected.returncode)
            self.assertIn("--base-output", rejected.stderr)
            self.assertEqual(before, manifest_path.read_bytes())

    def test_logo_conflict_acceptance_requires_passing_relocation_evidence(self) -> None:
        with self.logo_fixture() as fixture:
            context = self.prepare_logo_conflict(fixture)
            manifest_path = context["manifest_path"]
            item = context["item"]
            accepted_base = context["accepted_base"]
            prepared_bbox = context["prepared_bbox"]

            destructive = manifest_path.parent / "work" / "destructive-red-candidate.png"
            Image.new("RGB", (400, 400), (220, 20, 20)).save(destructive, format="PNG")
            before = manifest_path.read_bytes()
            rejected = fixture.run_cli(
                logo_flows.UPDATE,
                "--manifest", manifest_path,
                "--task-id", item["task_id"],
                "--worker-id", item["worker_id"],
                "--status", "pending",
                "--attempts", 2,
                "--attempt-stage", "logo_conflict",
                "--prepared-base", destructive,
                check=False,
            )
            self.assertNotEqual(0, rejected.returncode)
            self.assertIn("relocation guard", rejected.stderr)
            self.assertEqual(before, manifest_path.read_bytes())

            prepared = manifest_path.parent / "work" / "accepted-logo-relocation.png"
            fixture.make_prepared_conflict_base(accepted_base, prepared, prepared_bbox)
            fixture.run_cli(
                logo_flows.UPDATE,
                "--manifest", manifest_path,
                "--task-id", item["task_id"],
                "--worker-id", item["worker_id"],
                "--status", "pending",
                "--attempts", 2,
                "--attempt-stage", "logo_conflict",
                "--prepared-base", prepared,
            )
            Path(str(item["output"])).unlink()
            verified = fixture.run_cli(
                logo_flows.VERIFY,
                "--manifest", manifest_path,
                "--allow-pending",
            )
            self.assertTrue(json.loads(verified.stdout)["valid"])
            stored = json.loads(manifest_path.read_text(encoding="utf-8"))["items"][0]
            accepted_record = stored["attempt_history"][-1]
            self.assertTrue(stored["logo_relocation_validation"]["passed"])
            self.assertEqual(
                stored["logo_relocation_validation"],
                accepted_record["logo_relocation_validation"],
            )

            late_attempt = fixture.run_cli(
                logo_flows.UPDATE,
                "--manifest", manifest_path,
                "--task-id", item["task_id"],
                "--worker-id", item["worker_id"],
                "--status", "pending",
                "--attempts", 3,
                "--attempt-stage", "logo_conflict",
                "--failure-type", "infrastructure",
                "--error", "late provider retry",
                check=False,
            )
            self.assertNotEqual(0, late_attempt.returncode)
            self.assertIn("stage is closed", late_attempt.stderr)

    def test_logo_conflict_infrastructure_cannot_smuggle_a_fourth_candidate(self) -> None:
        with self.logo_fixture() as fixture:
            context = self.prepare_logo_conflict(fixture)
            manifest_path = context["manifest_path"]
            item = context["item"]
            logo = context["logo"]
            accepted_base = context["accepted_base"]
            prepared_bbox = context["prepared_bbox"]
            output = Path(str(item["output"]))

            for attempt in (2, 3, 4):
                prepared = (
                    manifest_path.parent
                    / "work"
                    / f"logo-quality-candidate-{attempt}.png"
                )
                fixture.make_prepared_conflict_base(
                    accepted_base,
                    prepared,
                    prepared_bbox,
                )
                with Image.open(prepared) as raw:
                    candidate = raw.convert("RGB")
                draw = ImageDraw.Draw(candidate)
                draw.rectangle(
                    tuple(prepared_bbox),
                    fill=(180, 35 + attempt, 35),
                )
                candidate.save(prepared, format="PNG")
                fixture.overlay_logo(prepared, output, logo)
                fixture.run_cli(
                    logo_flows.UPDATE,
                    "--manifest", manifest_path,
                    "--task-id", item["task_id"],
                    "--worker-id", item["worker_id"],
                    "--status", "pending",
                    "--attempts", attempt,
                    "--attempt-stage", "logo_conflict",
                    "--failure-type", "quality",
                    "--error", f"logo candidate {attempt} rejected",
                    "--prepared-base", prepared,
                )

            smuggled = manifest_path.parent / "work" / "smuggled-candidate.png"
            fixture.make_prepared_conflict_base(
                accepted_base,
                smuggled,
                prepared_bbox,
            )
            with Image.open(smuggled) as raw:
                candidate = raw.convert("RGB")
            ImageDraw.Draw(candidate).rectangle(
                tuple(prepared_bbox),
                fill=(180, 90, 35),
            )
            candidate.save(smuggled, format="PNG")
            fixture.overlay_logo(smuggled, output, logo)

            before = manifest_path.read_bytes()
            rejected = fixture.run_cli(
                logo_flows.UPDATE,
                "--manifest", manifest_path,
                "--task-id", item["task_id"],
                "--worker-id", item["worker_id"],
                "--status", "pending",
                "--attempts", 5,
                "--attempt-stage", "logo_conflict",
                "--failure-type", "infrastructure",
                "--error", "provider failed after returning a candidate",
                "--prepared-base", smuggled,
                check=False,
            )
            self.assertNotEqual(0, rejected.returncode)
            self.assertEqual(before, manifest_path.read_bytes())
            stored = json.loads(manifest_path.read_text(encoding="utf-8"))["items"][0]
            logo_attempts = [
                record
                for record in stored["attempt_history"]
                if record["attempt_stage"] == "logo_conflict"
            ]
            self.assertEqual([2, 3, 4], [record["attempt"] for record in logo_attempts])
            self.assertTrue(
                all(record["failure_type"] == "quality" for record in logo_attempts)
            )

    def test_verify_rejects_removed_main_base_and_logo_candidate_evidence(self) -> None:
        with self.logo_fixture() as fixture:
            context = self.prepare_logo_conflict(fixture)
            manifest_path = context["manifest_path"]
            Path(str(context["item"]["output"])).unlink()
            baseline = fixture.run_cli(
                logo_flows.VERIFY,
                "--manifest", manifest_path,
                "--allow-pending",
            )
            self.assertTrue(json.loads(baseline.stdout)["valid"])
            tampered = json.loads(manifest_path.read_text(encoding="utf-8"))
            tampered_item = tampered["items"][0]
            tampered_item["base_output"] = None
            tampered_item["attempt_history"][0].pop("accepted_base", None)
            fixture.write_json(manifest_path, tampered)
            rejected = fixture.run_cli(
                logo_flows.VERIFY,
                "--manifest", manifest_path,
                "--allow-pending",
                check=False,
            )
            self.assertNotEqual(0, rejected.returncode)
            self.assertIn("base_output", rejected.stdout)

        with self.logo_fixture() as fixture:
            context = self.prepare_logo_conflict(fixture)
            manifest_path = context["manifest_path"]
            item = context["item"]
            Path(str(item["output"])).unlink()
            accepted_base = context["accepted_base"]
            prepared_bbox = context["prepared_bbox"]
            prepared = manifest_path.parent / "work" / "logo-candidate-evidence.png"
            fixture.make_prepared_conflict_base(accepted_base, prepared, prepared_bbox)
            fixture.run_cli(
                logo_flows.UPDATE,
                "--manifest", manifest_path,
                "--task-id", item["task_id"],
                "--worker-id", item["worker_id"],
                "--status", "pending",
                "--attempts", 2,
                "--attempt-stage", "logo_conflict",
                "--failure-type", "quality",
                "--error", "candidate rejected",
                "--prepared-base", prepared,
            )
            baseline = fixture.run_cli(
                logo_flows.VERIFY,
                "--manifest", manifest_path,
                "--allow-pending",
            )
            self.assertTrue(json.loads(baseline.stdout)["valid"])
            tampered = json.loads(manifest_path.read_text(encoding="utf-8"))
            tampered["items"][0]["attempt_history"][-1].pop("candidate_width", None)
            fixture.write_json(manifest_path, tampered)
            rejected = fixture.run_cli(
                logo_flows.VERIFY,
                "--manifest", manifest_path,
                "--allow-pending",
                check=False,
            )
            self.assertNotEqual(0, rejected.returncode)
            self.assertIn("candidate_width", rejected.stdout)

    def test_accepted_main_image_closes_all_later_main_image_attempts(self) -> None:
        for attempt_kind in ("quality", "infrastructure"):
            with self.subTest(attempt_kind=attempt_kind):
                with self.main_image_fixture() as fixture:
                    context = self.prepare_accepted_main_image(fixture)
                    manifest_path = context["manifest_path"]
                    manifest = context["manifest"]
                    item = context["item"]
                    output = context["output"]
                    arguments: list[object] = [
                        "--manifest", manifest_path,
                        "--task-id", item["task_id"],
                        "--worker-id", item["worker_id"],
                        "--status", "pending",
                        "--attempts", 2,
                        "--attempt-stage", "commerce_main_image",
                        "--failure-type", attempt_kind,
                        "--error", f"unexpected {attempt_kind} attempt",
                    ]
                    if attempt_kind == "quality":
                        Image.new("RGB", (320, 320), (190, 205, 220)).save(
                            output,
                            format="PNG",
                        )
                        review_path = fixture.make_review(
                            manifest_path,
                            manifest,
                            item,
                            passed=False,
                        )
                        arguments.extend([
                            "--main-image-quality-review-json",
                            review_path,
                        ])
                    before = manifest_path.read_bytes()
                    rejected = fixture.run_cli(
                        main_image.UPDATE,
                        *arguments,
                        check=False,
                    )
                    self.assertNotEqual(0, rejected.returncode)
                    self.assertEqual(before, manifest_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
