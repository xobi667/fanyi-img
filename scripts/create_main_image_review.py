#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from manifest_utils import (
    atomic_bytes,
    canonical_path_key,
    is_link_or_junction,
    load_manifest,
    valid_task_id,
)


REVIEW_CONTRACT = "commerce-main-image-quality-review-v1"
EVIDENCE_CONTRACT = "commerce-main-image-review-evidence-v1"
ASSESSMENT_CONTRACT = "commerce-main-image-assessment-v1"
PLAN_CONTRACT = "commerce-main-image-plan-v1"
FROZEN_PLAN_CONTRACT = "frozen-commerce-main-image-plan-v1"
SCORES = (
    "visual_hierarchy",
    "product_fidelity",
    "material_realism",
    "typography",
    "spacing",
    "commercial_polish",
    "thumbnail_readability",
)
REQUIRED_CHECKS = (
    "single_focal_point",
    "product_priority",
    "clear_hierarchy",
    "safe_margins",
    "realistic_scale_and_shadow",
    "no_invented_claims",
)
HARD_REJECTS = (
    "cheap_banner",
    "random_badge",
    "thick_outline",
    "oval_sticker_collage",
    "clutter",
    "fake_3d",
    "oversaturation",
    "invented_claim",
)
THUMBNAIL_SIZES = (160, 256)
VIEW_NAMES = ("full", "256", "160")
ASSESSMENT_KEYS = {
    "schema_version",
    "producer",
    "contract",
    "manifest_id",
    "task_id",
    "operation",
    "candidate",
    "plan",
    "views",
    "scores",
    "required_checks",
    "hard_rejects",
    "reviewer",
    "notes",
}
VIEW_ASSESSMENT_KEYS = {"path", "sha256", "width", "height", "passed", "notes"}


@dataclass(frozen=True)
class ReviewContext:
    manifest_path: Path
    manifest: dict[str, Any]
    item: dict[str, Any]
    task_dir: Path
    work_dir: Path
    candidate: Path
    plan_path: Path


@dataclass(frozen=True)
class FileSnapshot:
    payload: bytes
    signature: tuple[int, int, int, int]


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonstandard_number(value: str) -> Any:
    raise ValueError(f"non-standard JSON number: {value}")


def parse_json_object(payload: bytes, label: str) -> dict[str, Any]:
    try:
        text = payload.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonstandard_number,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"invalid {label} JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} JSON must contain an object")
    return value


def _stat_signature(value: os.stat_result) -> tuple[int, int, int, int]:
    return (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_size),
        int(value.st_mtime_ns),
    )


def read_file_snapshot(path: Path, label: str) -> FileSnapshot:
    try:
        with path.open("rb") as handle:
            before = _stat_signature(os.fstat(handle.fileno()))
            payload = handle.read()
            after = _stat_signature(os.fstat(handle.fileno()))
        current = _stat_signature(path.stat())
    except OSError as exc:
        raise ValueError(f"could not read {label}: {exc}") from exc
    if before != after or after != current:
        raise ValueError(f"{label} changed while it was being read")
    return FileSnapshot(payload=payload, signature=current)


def ensure_snapshot_current(path: Path, snapshot: FileSnapshot, label: str) -> None:
    try:
        current = _stat_signature(path.stat())
    except OSError as exc:
        raise ValueError(f"{label} changed after it was read: {exc}") from exc
    if current != snapshot.signature:
        raise ValueError(f"{label} changed after it was read")


def path_chain_has_link(path: Path) -> Path:
    absolute = path.expanduser().absolute()
    for candidate in (absolute, *absolute.parents):
        if candidate.exists() and is_link_or_junction(candidate):
            raise ValueError(f"path must not traverse a symlink or junction: {candidate}")
    return absolute.resolve()


def inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def find_item(manifest: dict[str, Any], task_id: str) -> dict[str, Any]:
    if not valid_task_id(task_id):
        raise ValueError("task-id contains unsafe filename characters")
    raw_items = manifest.get("items")
    if not isinstance(raw_items, list) or any(not isinstance(item, dict) for item in raw_items):
        raise ValueError("manifest items must be a list of objects")
    matches = [item for item in raw_items if item.get("task_id") == task_id]
    if len(matches) != 1:
        raise ValueError("task-id must match exactly one manifest item")
    return matches[0]


def load_context(args: argparse.Namespace) -> ReviewContext:
    manifest_path = path_chain_has_link(args.manifest)
    candidate = path_chain_has_link(args.candidate)
    plan_path = path_chain_has_link(args.plan_json)
    if manifest_path.name.casefold() != "manifest.json" or not manifest_path.is_file():
        raise ValueError("manifest must name the task .xobi/manifest.json file")
    if not candidate.is_file():
        raise ValueError("candidate not found")
    if not plan_path.is_file():
        raise ValueError("main-image plan not found")

    manifest = load_manifest(manifest_path)
    if manifest.get("workflow") != "commerce_main_image":
        raise ValueError("manifest workflow must be commerce_main_image")
    manifest_id = manifest.get("manifest_id")
    if not isinstance(manifest_id, str) or not re.fullmatch(r"xobi-[0-9a-f]{32}", manifest_id):
        raise ValueError("manifest_id is invalid")
    item = find_item(manifest, args.task_id)

    task_dir_value = str(manifest.get("task_dir") or "")
    if not task_dir_value:
        raise ValueError("manifest task_dir is missing")
    task_dir = path_chain_has_link(Path(task_dir_value))
    if not task_dir.is_dir():
        raise ValueError("manifest task_dir is missing")
    metadata_dir = task_dir / ".xobi"
    if canonical_path_key(manifest_path.parent) != canonical_path_key(metadata_dir):
        raise ValueError("manifest must remain inside the task .xobi directory")
    work_dir = path_chain_has_link(metadata_dir / "work")
    if not work_dir.is_dir():
        raise ValueError("manifest work directory is missing")

    if not inside(candidate, task_dir) or inside(candidate, metadata_dir):
        raise ValueError("candidate must remain inside the task directory and outside .xobi")
    if canonical_path_key(candidate) != canonical_path_key(Path(str(item.get("output") or ""))):
        raise ValueError("candidate must use the task output path preallocated by the manifest")
    if not inside(plan_path, work_dir):
        raise ValueError("frozen main-image plan must remain inside .xobi/work")
    if "task-state" in {part.casefold() for part in plan_path.relative_to(work_dir).parts}:
        raise ValueError("frozen main-image plan must not be inside task-state")

    registration = item.get("main_image_plan_registration")
    if not isinstance(registration, dict):
        raise ValueError("task requires a frozen main_image_plan_registration")
    expected_registration = {
        "schema_version": 1,
        "producer": "xobi-img.update_manifest",
        "contract": FROZEN_PLAN_CONTRACT,
        "manifest_id": manifest_id,
        "task_id": item.get("task_id"),
        "source_sha256": item.get("source_sha256"),
    }
    for name, expected in expected_registration.items():
        if registration.get(name) != expected:
            raise ValueError(f"frozen main-image plan registration {name} does not match the task")
    if canonical_path_key(plan_path) != canonical_path_key(Path(str(registration.get("path") or ""))):
        raise ValueError("plan-json does not match the frozen task plan")

    return ReviewContext(
        manifest_path=manifest_path,
        manifest=manifest,
        item=item,
        task_dir=task_dir,
        work_dir=work_dir,
        candidate=candidate,
        plan_path=plan_path,
    )


def load_bound_snapshots(
    context: ReviewContext,
) -> tuple[FileSnapshot, str, Image.Image, FileSnapshot, str]:
    candidate_snapshot = read_file_snapshot(context.candidate, "candidate")
    candidate_digest = sha256_bytes(candidate_snapshot.payload)
    rendered = decode_static_candidate(candidate_snapshot.payload)

    plan_snapshot = read_file_snapshot(context.plan_path, "main-image plan")
    plan_digest = sha256_bytes(plan_snapshot.payload)
    registration = context.item["main_image_plan_registration"]
    if plan_digest != registration.get("sha256"):
        raise ValueError("frozen main-image plan hash changed")
    plan = parse_json_object(plan_snapshot.payload, "main-image plan")
    expected_plan = {
        "schema_version": 1,
        "contract": PLAN_CONTRACT,
        "task_id": context.item.get("task_id"),
        "operation": context.manifest.get("operation"),
    }
    for name, expected in expected_plan.items():
        if plan.get(name) != expected:
            raise ValueError(f"main-image plan {name} does not match the frozen task")
    return candidate_snapshot, candidate_digest, rendered, plan_snapshot, plan_digest


def decode_static_candidate(payload: bytes) -> Image.Image:
    try:
        with Image.open(BytesIO(payload)) as probe:
            frame_count = int(getattr(probe, "n_frames", 1) or 1)
            if frame_count != 1 or bool(getattr(probe, "is_animated", False)):
                raise ValueError("candidate must be a single-frame, non-animated image")
            probe.verify()
        with Image.open(BytesIO(payload)) as raw:
            rendered = ImageOps.exif_transpose(raw).convert("RGBA")
            rendered.load()
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise ValueError(f"candidate is not a readable static image: {exc}") from exc
    return rendered


def thumbnail_bytes(source: Image.Image, size: int) -> tuple[bytes, tuple[int, int]]:
    rendered = source.copy()
    rendered.thumbnail((size, size), Image.Resampling.LANCZOS, reducing_gap=3.0)
    if max(rendered.size) != size:
        raise ValueError(f"could not create a {size}px longest-edge thumbnail")
    encoded = BytesIO()
    rendered.save(encoded, format="PNG", optimize=False, compress_level=9)
    return encoded.getvalue(), rendered.size


def render_thumbnails(source: Image.Image) -> dict[str, tuple[bytes, tuple[int, int]]]:
    return {str(size): thumbnail_bytes(source, size) for size in (256, 160)}


def full_snapshot_path(context: ReviewContext, evidence_dir: Path) -> Path:
    suffix = context.candidate.suffix.casefold() or ".img"
    return evidence_dir / f"full-original{suffix}"


def build_evidence(
    context: ReviewContext,
    evidence_dir: Path,
    candidate_digest: str,
    rendered: Image.Image,
    plan_digest: str,
    thumbnails: dict[str, tuple[bytes, tuple[int, int]]],
) -> dict[str, Any]:
    candidate = {
        "path": str(context.candidate),
        "sha256": candidate_digest,
        "width": rendered.width,
        "height": rendered.height,
    }
    views: dict[str, dict[str, Any]] = {
        "full": {
            "path": str(full_snapshot_path(context, evidence_dir)),
            "sha256": candidate_digest,
            "width": rendered.width,
            "height": rendered.height,
        }
    }
    for name in ("256", "160"):
        payload, dimensions = thumbnails[name]
        views[name] = {
            "path": str(evidence_dir / f"thumbnail-{name}.png"),
            "sha256": sha256_bytes(payload),
            "width": dimensions[0],
            "height": dimensions[1],
        }
    return {
        "schema_version": 1,
        "producer": "xobi-img.create_main_image_review",
        "contract": EVIDENCE_CONTRACT,
        "manifest_id": context.manifest.get("manifest_id"),
        "task_id": context.item.get("task_id"),
        "source": context.item.get("source") or "",
        "source_sha256": context.item.get("source_sha256"),
        "operation": context.manifest.get("operation"),
        "candidate": candidate,
        "plan": {
            "path": str(context.plan_path),
            "sha256": plan_digest,
        },
        "thumbnail_sizes": list(THUMBNAIL_SIZES),
        "views": views,
    }


def build_assessment_template(evidence: dict[str, Any]) -> dict[str, Any]:
    views: dict[str, dict[str, Any]] = {}
    for name in VIEW_NAMES:
        views[name] = {
            **evidence["views"][name],
            "passed": None,
            "notes": "",
        }
    return {
        "schema_version": 1,
        "producer": "xobi-img.create_main_image_review",
        "contract": ASSESSMENT_CONTRACT,
        "manifest_id": evidence["manifest_id"],
        "task_id": evidence["task_id"],
        "operation": evidence["operation"],
        "candidate": evidence["candidate"],
        "plan": evidence["plan"],
        "views": views,
        "scores": {name: None for name in SCORES},
        "required_checks": {name: None for name in REQUIRED_CHECKS},
        "hard_rejects": {name: None for name in HARD_REJECTS},
        "reviewer": "visual-review",
        "notes": "",
    }


def validate_assessment(
    value: dict[str, Any],
    evidence: dict[str, Any],
) -> tuple[dict[str, int], dict[str, bool], dict[str, bool], dict[str, bool]]:
    if set(value) != ASSESSMENT_KEYS:
        raise ValueError("assessment top-level keys do not match the v1 contract")
    expected_bindings = {
        "schema_version": 1,
        "producer": "xobi-img.create_main_image_review",
        "contract": ASSESSMENT_CONTRACT,
        "manifest_id": evidence["manifest_id"],
        "task_id": evidence["task_id"],
        "operation": evidence["operation"],
        "candidate": evidence["candidate"],
        "plan": evidence["plan"],
    }
    for name, expected in expected_bindings.items():
        if value.get(name) != expected:
            raise ValueError(f"assessment {name} does not match the prepared evidence")

    raw_scores = value.get("scores")
    raw_checks = value.get("required_checks")
    raw_rejects = value.get("hard_rejects")
    if not isinstance(raw_scores, dict) or set(raw_scores) != set(SCORES):
        raise ValueError("assessment score keys do not match the v1 contract")
    if not isinstance(raw_checks, dict) or set(raw_checks) != set(REQUIRED_CHECKS):
        raise ValueError("assessment required_checks keys do not match the v1 contract")
    if not isinstance(raw_rejects, dict) or set(raw_rejects) != set(HARD_REJECTS):
        raise ValueError("assessment hard_rejects keys do not match the v1 contract")

    scores: dict[str, int] = {}
    for name in SCORES:
        raw_score = raw_scores[name]
        if isinstance(raw_score, bool) or not isinstance(raw_score, int) or not 1 <= raw_score <= 5:
            raise ValueError(f"assessment score {name} must be an integer from 1 to 5")
        scores[name] = raw_score

    checks: dict[str, bool] = {}
    for name in REQUIRED_CHECKS:
        raw_check = raw_checks[name]
        if not isinstance(raw_check, bool):
            raise ValueError(f"assessment required check {name} must be boolean")
        checks[name] = raw_check

    rejects: dict[str, bool] = {}
    for name in HARD_REJECTS:
        raw_reject = raw_rejects[name]
        if not isinstance(raw_reject, bool):
            raise ValueError(f"assessment hard reject {name} must be boolean")
        rejects[name] = raw_reject

    raw_views = value.get("views")
    if not isinstance(raw_views, dict) or set(raw_views) != set(VIEW_NAMES):
        raise ValueError("assessment views must be exactly full, 256, and 160")
    view_passes: dict[str, bool] = {}
    for name in VIEW_NAMES:
        raw_view = raw_views[name]
        if not isinstance(raw_view, dict) or set(raw_view) != VIEW_ASSESSMENT_KEYS:
            raise ValueError(f"assessment view {name} keys do not match the v1 contract")
        expected_view = evidence["views"][name]
        for field in ("path", "sha256", "width", "height"):
            if raw_view.get(field) != expected_view.get(field):
                raise ValueError(f"assessment view {name} {field} does not match the prepared evidence")
        if not isinstance(raw_view.get("passed"), bool):
            raise ValueError(f"assessment view {name} passed must be boolean")
        if not isinstance(raw_view.get("notes"), str):
            raise ValueError(f"assessment view {name} notes must be a string")
        view_passes[name] = raw_view["passed"]

    if not isinstance(value.get("reviewer"), str) or not value["reviewer"].strip():
        raise ValueError("assessment reviewer must be a non-empty string")
    if not isinstance(value.get("notes"), str):
        raise ValueError("assessment notes must be a string")
    return scores, checks, rejects, view_passes


def build_review(
    context: ReviewContext,
    evidence: dict[str, Any],
    assessment: dict[str, Any],
    assessment_path: Path,
    assessment_digest: str,
    evidence_path: Path,
    evidence_digest: str,
) -> dict[str, Any]:
    scores, checks, rejects, requested_views = validate_assessment(assessment, evidence)
    score_passed = all(score >= 4 for score in scores.values())
    checks_passed = all(checks.values())
    rejects_passed = not any(rejects.values())
    full_gate = (
        all(scores[name] >= 4 for name in SCORES if name != "thumbnail_readability")
        and checks_passed
        and rejects_passed
    )
    thumbnail_gate = (
        scores["thumbnail_readability"] >= 4
        and checks["single_focal_point"]
        and checks["product_priority"]
        and checks["clear_hierarchy"]
        and checks["safe_margins"]
        and rejects_passed
    )
    criteria = {
        "single_focal_point": checks["single_focal_point"],
        "product_priority": checks["product_priority"],
        "clear_information_hierarchy": checks["clear_hierarchy"],
        "safe_margins": checks["safe_margins"],
        "realistic_material_scale_and_shadow": checks["realistic_scale_and_shadow"],
        "thumbnail_readability": scores["thumbnail_readability"] >= 4,
        "typography_quality": scores["typography"] >= 4,
        "commercial_polish": scores["commercial_polish"] >= 4,
        "no_cheap_collage_or_decorations": not any(
            rejects[name]
            for name in (
                "cheap_banner",
                "random_badge",
                "thick_outline",
                "oval_sticker_collage",
                "clutter",
                "fake_3d",
                "oversaturation",
            )
        ),
        "no_invented_claims": checks["no_invented_claims"] and not rejects["invented_claim"],
    }
    views = {
        name: {
            **evidence["views"][name],
            "passed": requested_views[name] and (full_gate if name == "full" else thumbnail_gate),
        }
        for name in VIEW_NAMES
    }
    passed = (
        score_passed
        and checks_passed
        and rejects_passed
        and all(criteria.values())
        and all(view["passed"] for view in views.values())
    )
    return {
        "schema_version": 1,
        "producer": "xobi-img.create_main_image_review",
        "contract": REVIEW_CONTRACT,
        "manifest_id": evidence["manifest_id"],
        "task_id": evidence["task_id"],
        "source": evidence["source"],
        "source_sha256": evidence["source_sha256"],
        "operation": evidence["operation"],
        "output": evidence["candidate"],
        "plan": evidence["plan"],
        "thumbnail_sizes": list(THUMBNAIL_SIZES),
        "views": views,
        "criteria": criteria,
        "scores": scores,
        "hard_rejects": rejects,
        "passed": passed,
        "reviewer": assessment["reviewer"],
        "notes": assessment["notes"],
        "assessment": {
            "path": str(assessment_path),
            "sha256": assessment_digest,
        },
        "evidence": {
            "path": str(evidence_path),
            "sha256": evidence_digest,
        },
    }


def validate_dedicated_path(path: Path, work_dir: Path, label: str) -> None:
    if not inside(path, work_dir) or path == work_dir:
        raise ValueError(f"{label} must be a dedicated directory inside .xobi/work")
    relative_parts = {part.casefold() for part in path.relative_to(work_dir).parts}
    if "task-state" in relative_parts:
        raise ValueError(f"{label} must not be inside task-state")


def protected_path_keys(context: ReviewContext, extra: tuple[Path, ...] = ()) -> set[str]:
    values = [
        context.manifest_path,
        context.candidate,
        context.plan_path,
        *extra,
    ]
    for name in ("source", "output"):
        if context.item.get(name):
            values.append(Path(str(context.item[name])).expanduser().resolve())
    logo = context.manifest.get("logo") or {}
    if isinstance(logo, dict):
        for name in ("source", "normalized"):
            if logo.get(name):
                values.append(Path(str(logo[name])).expanduser().resolve())
    return {canonical_path_key(path) for path in values}


def json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def atomic_bytes_no_replace(path: Path, value: bytes) -> None:
    if not path.parent.is_dir():
        raise ValueError("review output parent directory is missing")
    temp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    linked = False
    try:
        with temp.open("xb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp, path)
            linked = True
        except FileExistsError as exc:
            raise ValueError("review-json already exists and will not be overwritten") from exc
        except OSError:
            # Some otherwise supported filesystems do not provide hard links.
            # Preserve the no-overwrite contract with an exclusive fallback.
            try:
                with path.open("xb") as handle:
                    handle.write(value)
                    handle.flush()
                    os.fsync(handle.fileno())
                linked = True
            except FileExistsError as exc:
                raise ValueError("review-json already exists and will not be overwritten") from exc
            except OSError:
                path.unlink(missing_ok=True)
                raise
        if os.name != "nt":
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            if not linked:
                raise


def prepare(args: argparse.Namespace) -> int:
    context = load_context(args)
    candidate_snapshot, candidate_digest, rendered, plan_snapshot, plan_digest = load_bound_snapshots(context)
    thumbnails = render_thumbnails(rendered)

    default_dir = context.work_dir / f"main-image-review-{args.task_id}-{candidate_digest[:12]}"
    evidence_dir = path_chain_has_link(args.output_dir or default_dir)
    validate_dedicated_path(evidence_dir, context.work_dir, "output-dir")
    if not evidence_dir.parent.is_dir():
        raise ValueError("output-dir parent directory is missing")
    if evidence_dir.exists():
        raise ValueError("output-dir already exists; use a new evidence directory for each candidate")

    full_path = full_snapshot_path(context, evidence_dir)
    final_paths = {
        evidence_dir / "evidence.json",
        evidence_dir / "assessment-template.json",
        full_path,
        evidence_dir / "thumbnail-256.png",
        evidence_dir / "thumbnail-160.png",
    }
    protected = protected_path_keys(context)
    if canonical_path_key(evidence_dir) in protected or any(
        canonical_path_key(path) in protected for path in final_paths
    ):
        raise ValueError("review evidence must not overwrite a manifest, source, Logo, plan, or candidate")

    evidence = build_evidence(
        context,
        evidence_dir,
        candidate_digest,
        rendered,
        plan_digest,
        thumbnails,
    )
    assessment_template = build_assessment_template(evidence)
    staging = evidence_dir.parent / f".{evidence_dir.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}"
    if staging.exists():
        raise ValueError("temporary evidence directory already exists")
    staging.mkdir(parents=False, exist_ok=False)
    try:
        atomic_bytes(staging / full_path.name, candidate_snapshot.payload)
        for name in ("256", "160"):
            atomic_bytes(staging / f"thumbnail-{name}.png", thumbnails[name][0])
        atomic_bytes(staging / "evidence.json", json_bytes(evidence))
        atomic_bytes(staging / "assessment-template.json", json_bytes(assessment_template))
        ensure_snapshot_current(context.candidate, candidate_snapshot, "candidate")
        ensure_snapshot_current(context.plan_path, plan_snapshot, "main-image plan")
        if evidence_dir.exists():
            raise ValueError("output-dir appeared during preparation and will not be overwritten")
        staging.rename(evidence_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    print(f"evidence_dir={evidence_dir}")
    print(f"evidence={evidence_dir / 'evidence.json'}")
    print(f"assessment_template={evidence_dir / 'assessment-template.json'}")
    print(f"full={full_path}")
    print(f"thumbnail_256={evidence_dir / 'thumbnail-256.png'}")
    print(f"thumbnail_160={evidence_dir / 'thumbnail-160.png'}")
    return 0


def finalize(args: argparse.Namespace) -> int:
    context = load_context(args)
    candidate_snapshot, candidate_digest, rendered, plan_snapshot, plan_digest = load_bound_snapshots(context)
    thumbnails = render_thumbnails(rendered)

    evidence_dir = path_chain_has_link(args.evidence_dir)
    validate_dedicated_path(evidence_dir, context.work_dir, "evidence-dir")
    if not evidence_dir.is_dir():
        raise ValueError("evidence-dir is missing")
    evidence_path = path_chain_has_link(evidence_dir / "evidence.json")
    if evidence_path.parent != evidence_dir or not evidence_path.is_file():
        raise ValueError("evidence-dir is missing evidence.json")
    evidence_snapshot = read_file_snapshot(evidence_path, "review evidence")
    evidence = parse_json_object(evidence_snapshot.payload, "review evidence")

    expected_evidence = build_evidence(
        context,
        evidence_dir,
        candidate_digest,
        rendered,
        plan_digest,
        thumbnails,
    )
    if evidence != expected_evidence:
        raise ValueError("prepared evidence does not match the current manifest, task, candidate, plan, or views")

    full_path = path_chain_has_link(full_snapshot_path(context, evidence_dir))
    if full_path.parent != evidence_dir or not full_path.is_file():
        raise ValueError("prepared full-size snapshot is missing")
    full_snapshot = read_file_snapshot(full_path, "full-size snapshot")
    if full_snapshot.payload != candidate_snapshot.payload:
        raise ValueError("prepared full-size snapshot is not the exact candidate-derived evidence")

    thumbnail_snapshots: dict[str, FileSnapshot] = {}
    for name in ("256", "160"):
        thumbnail_path = path_chain_has_link(evidence_dir / f"thumbnail-{name}.png")
        if thumbnail_path.parent != evidence_dir or not thumbnail_path.is_file():
            raise ValueError(f"prepared {name}px thumbnail is missing")
        snapshot = read_file_snapshot(thumbnail_path, f"{name}px thumbnail")
        thumbnail_snapshots[name] = snapshot
        if snapshot.payload != thumbnails[name][0]:
            raise ValueError(f"prepared {name}px thumbnail is not the exact candidate-derived evidence")

    assessment_path = path_chain_has_link(args.assessment_json)
    if (
        not inside(assessment_path, context.work_dir)
        or "task-state" in {part.casefold() for part in assessment_path.relative_to(context.work_dir).parts}
        or assessment_path.suffix.casefold() != ".json"
        or not assessment_path.is_file()
    ):
        raise ValueError("assessment-json must be an existing .json file inside .xobi/work and outside task-state")
    assessment_snapshot = read_file_snapshot(assessment_path, "assessment")
    assessment = parse_json_object(assessment_snapshot.payload, "assessment")
    validate_assessment(assessment, evidence)

    review_path = path_chain_has_link(args.review_json or (evidence_dir / "review.json"))
    if review_path.parent != evidence_dir or review_path.suffix.casefold() != ".json":
        raise ValueError("review-json must be a .json file directly inside evidence-dir")
    if review_path.exists():
        raise ValueError("review-json already exists and will not be overwritten")
    protected = protected_path_keys(context, (assessment_path, evidence_path))
    if canonical_path_key(review_path) in protected:
        raise ValueError("review output must not overwrite the manifest, source, Logo, plan, assessment, evidence, or candidate")

    evidence_digest = sha256_bytes(evidence_snapshot.payload)
    assessment_digest = sha256_bytes(assessment_snapshot.payload)
    review = build_review(
        context,
        evidence,
        assessment,
        assessment_path,
        assessment_digest,
        evidence_path,
        evidence_digest,
    )
    ensure_snapshot_current(context.candidate, candidate_snapshot, "candidate")
    ensure_snapshot_current(context.plan_path, plan_snapshot, "main-image plan")
    ensure_snapshot_current(evidence_path, evidence_snapshot, "review evidence")
    ensure_snapshot_current(assessment_path, assessment_snapshot, "assessment")
    ensure_snapshot_current(full_path, full_snapshot, "full-size snapshot")
    for name, snapshot in thumbnail_snapshots.items():
        ensure_snapshot_current(evidence_dir / f"thumbnail-{name}.png", snapshot, f"{name}px thumbnail")
    atomic_bytes_no_replace(review_path, json_bytes(review))

    print(f"review={review_path}")
    print(f"passed={str(review['passed']).lower()}")
    print(f"full={full_path}")
    print(f"thumbnail_256={evidence_dir / 'thumbnail-256.png'}")
    print(f"thumbnail_160={evidence_dir / 'thumbnail-160.png'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare immutable full/256/160 commerce main-image evidence, then finalize a "
            "task-, candidate-, plan-, and view-bound visual assessment."
        )
    )
    phases = parser.add_subparsers(dest="phase", required=True)

    def add_common_arguments(target: argparse.ArgumentParser) -> None:
        target.add_argument("--manifest", required=True, type=Path)
        target.add_argument("--task-id", required=True)
        target.add_argument("--candidate", required=True, type=Path)
        target.add_argument("--plan-json", required=True, type=Path)

    prepare_parser = phases.add_parser(
        "prepare",
        help="Create an exact full-size snapshot, 256/160 evidence, and an assessment template.",
        description=(
            "Create an immutable full-size original-byte snapshot, proportional long-edge "
            "256/160 evidence, evidence.json, and a bound assessment template."
        ),
    )
    add_common_arguments(prepare_parser)
    prepare_parser.add_argument(
        "--output-dir",
        type=Path,
        help="New dedicated directory under .xobi/work (default is candidate-hash-derived).",
    )

    finalize_parser = phases.add_parser(
        "finalize",
        help="Validate a completed bound assessment and atomically create review.json.",
        description=(
            "Recompute the full-size snapshot and proportional 256/160 evidence, validate the "
            "bound assessment, and atomically create review.json."
        ),
    )
    add_common_arguments(finalize_parser)
    finalize_parser.add_argument("--evidence-dir", required=True, type=Path)
    finalize_parser.add_argument("--assessment-json", required=True, type=Path)
    finalize_parser.add_argument("--review-json", type=Path)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.phase == "prepare":
            return prepare(args)
        if args.phase == "finalize":
            return finalize(args)
        raise ValueError("unsupported review phase")
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
