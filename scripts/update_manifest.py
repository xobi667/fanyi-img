#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import uuid
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from manifest_utils import (
    COMMERCE_MAIN_IMAGE_ATTEMPT_STAGE,
    COMMERCE_MAIN_IMAGE_WORKFLOW,
    FileLock,
    LOGO_ALPHA_THRESHOLD,
    LOGO_ANCHOR_TOLERANCE,
    LOGO_REFERENCE_BOX,
    LOGO_REFERENCE_SHORT_SIDE,
    LOGO_SAFE_PADDING,
    LEGACY_REFERENCE_LOCALIZATION_MODE,
    PURE_GENERATION_LOCALIZATION_MODE,
    _localization_text_edit_mask,
    active_logo_asset,
    atomic_bytes,
    atomic_json,
    canonical_path_key,
    load_manifest,
    localization_visual_guard,
    logo_canvas_requires_review,
    now_iso,
    quality_attempts_for_stage,
    reference_edit_quality_failures,
    recompute_logo_relocation_validation,
    sha256_file,
    validate_manifest,
    validate_item_contract,
    validate_logo_conflict_attempt_contract,
    validate_logo_conflict_gate,
    is_legacy_read_only_manifest,
    validate_localization_attempt_contract,
    validate_localization_composition,
    validate_localization_plan_contract,
    validate_localization_plan_registration,
    validate_main_image_attempt_contract,
    validate_main_image_plan_contract,
    validate_main_image_plan_registration,
    validate_main_image_quality_review,
    validate_output,
    validate_pure_rebuild_approval,
    valid_task_id,
    write_report,
)


STATE_FIELDS = {
    "status",
    "output",
    "attempts",
    "prompt_summary",
    "error",
    "updated_at",
    "output_validation",
    "base_output",
    "localized_base",
    "localization_validation",
    "conflict_reference_base",
    "prepared_base",
    "logo_relocation_validation",
    "family_id",
    "logo_decision",
    "logo_geometry",
    "module_anchors",
    "localization_plan",
    "localization_plan_registration",
    "localization_execution_stage",
    "localization_composition",
    "main_image_plan",
    "main_image_plan_registration",
    "main_image_quality_review",
    "pure_rebuild_approval",
}


def read_json_argument(value: str | None, label: str) -> Any:
    if value is None:
        return None
    candidate = Path(value)
    try:
        if candidate.is_file():
            return json.loads(candidate.read_text(encoding="utf-8"))
        return json.loads(value)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid {label} JSON: {exc}") from exc


def load_main_image_plan_artifact(
    value: str,
    item: dict[str, Any],
    manifest: dict[str, Any],
    work_dir: Path,
    registered_at: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    raw_path = Path(value).expanduser().absolute()
    for candidate in (raw_path, *raw_path.parents):
        if candidate.exists() and (
            candidate.is_symlink() or bool(getattr(candidate, "is_junction", lambda: False)())
        ):
            raise ValueError(f"main-image plan path must not traverse a symlink or junction: {candidate}")
    artifact = raw_path.resolve()
    try:
        artifact.relative_to(work_dir.resolve())
    except ValueError as exc:
        raise ValueError("main-image plan artifact must be inside .xobi/work") from exc
    if artifact.suffix.lower() != ".json" or not artifact.is_file():
        raise ValueError("main-image plan must name an existing .json artifact file")
    try:
        plan = json.loads(artifact.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid main-image plan artifact: {exc}") from exc
    if not isinstance(plan, dict):
        raise ValueError("main-image plan artifact must contain a JSON object")
    plan_errors = validate_main_image_plan_contract(item, manifest, plan)
    if plan_errors:
        raise ValueError("invalid main-image plan contract: " + "; ".join(plan_errors))
    for other in manifest.get("items", []):
        if other.get("task_id") == item.get("task_id"):
            continue
        other_registration = other.get("main_image_plan_registration")
        if isinstance(other_registration, dict) and str(other_registration.get("path") or ""):
            if canonical_path_key(Path(str(other_registration["path"]))) == canonical_path_key(artifact):
                raise ValueError("each commerce main-image task requires its own frozen plan artifact")
    registration = {
        "schema_version": 1,
        "producer": "xobi-img.update_manifest",
        "contract": "frozen-commerce-main-image-plan-v1",
        "manifest_id": manifest.get("manifest_id"),
        "task_id": item.get("task_id"),
        "source_sha256": item.get("source_sha256"),
        "path": str(artifact),
        "sha256": sha256_file(artifact),
        "registered_at": registered_at,
        "attempts_at_registration": 0,
        "attempt_history_count_at_registration": 0,
    }
    return plan, registration


def load_main_image_quality_review_artifact(
    value: str,
    item: dict[str, Any],
    manifest: dict[str, Any],
    work_dir: Path,
    registered_at: str,
    attempt: int,
) -> dict[str, Any]:
    raw_path = Path(value).expanduser().absolute()
    for candidate in (raw_path, *raw_path.parents):
        if candidate.exists() and (
            candidate.is_symlink() or bool(getattr(candidate, "is_junction", lambda: False)())
        ):
            raise ValueError(
                f"main-image quality review path must not traverse a symlink or junction: {candidate}"
            )
    artifact = raw_path.resolve()
    try:
        artifact.relative_to(work_dir.resolve())
    except ValueError as exc:
        raise ValueError("main-image quality review artifact must be inside .xobi/work") from exc
    if artifact.suffix.lower() != ".json" or not artifact.is_file():
        raise ValueError("main-image quality review must name an existing .json artifact file")
    try:
        review = json.loads(artifact.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid main-image quality review artifact: {exc}") from exc
    if not isinstance(review, dict):
        raise ValueError("main-image quality review artifact must contain a JSON object")
    review_passed = review.get("passed")
    review_output = review.get("output")
    review_candidate_digest = (
        review_output.get("sha256") if isinstance(review_output, dict) else None
    )
    if not isinstance(review_passed, bool):
        raise ValueError("main-image quality review artifact must contain a boolean passed result")
    if not isinstance(review_candidate_digest, str) or not re.fullmatch(
        r"[0-9a-f]{64}", review_candidate_digest
    ):
        raise ValueError("main-image quality review artifact must bind a candidate sha256")
    for other in manifest.get("items", []):
        if other.get("task_id") == item.get("task_id"):
            continue
        other_review = other.get("main_image_quality_review")
        if isinstance(other_review, dict) and str(other_review.get("path") or ""):
            if canonical_path_key(Path(str(other_review["path"]))) == canonical_path_key(artifact):
                raise ValueError("each commerce main-image task requires its own quality review artifact")
    return {
        "schema_version": 1,
        "producer": "xobi-img.update_manifest",
        "contract": "frozen-commerce-main-image-quality-review-v1",
        "manifest_id": manifest.get("manifest_id"),
        "task_id": item.get("task_id"),
        "attempt": attempt,
        "attempt_stage": COMMERCE_MAIN_IMAGE_ATTEMPT_STAGE,
        "passed": review_passed,
        "candidate_sha256": review_candidate_digest,
        "path": str(artifact),
        "sha256": sha256_file(artifact),
        "registered_at": registered_at,
        "record": review,
    }


def load_localization_plan_artifact(
    value: str,
    item: dict[str, Any],
    manifest: dict[str, Any],
    work_dir: Path,
    registered_at: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    raw_path = Path(value).expanduser().absolute()
    for candidate in (raw_path, *raw_path.parents):
        if candidate.is_symlink() or bool(getattr(candidate, "is_junction", lambda: False)()):
            raise ValueError(
                f"localization plan path must not traverse a symlink or junction: {candidate}"
            )
    artifact = raw_path.resolve()
    try:
        artifact.relative_to(work_dir.resolve())
    except ValueError as exc:
        raise ValueError("localization plan artifact must be inside .xobi/work") from exc
    if artifact.suffix.lower() != ".json" or not artifact.is_file():
        raise ValueError("localization plan must name an existing .json artifact file")
    try:
        plan = json.loads(artifact.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid localization plan artifact: {exc}") from exc
    if not isinstance(plan, dict):
        raise ValueError("localization plan artifact must contain a JSON object")
    if plan.get("task_id") != item.get("task_id"):
        raise ValueError("localization plan task_id does not match this task")
    if canonical_path_key(Path(str(plan.get("source") or ""))) != canonical_path_key(
        Path(str(item.get("source") or ""))
    ):
        raise ValueError("localization plan source does not match this task")
    if plan.get("source_sha256") != item.get("source_sha256"):
        raise ValueError("localization plan source hash does not match preflight")
    if plan.get("source_size") != [item.get("width"), item.get("height")]:
        raise ValueError("localization plan source_size does not match preflight")
    if plan.get("target_language") != manifest.get("target_language"):
        raise ValueError("localization plan target language does not match the manifest")
    policy = manifest.get("localization_policy")
    policy_mode = str(policy.get("mode") or "") if isinstance(policy, dict) else ""
    expected_plan_mode = (
        PURE_GENERATION_LOCALIZATION_MODE
        if policy_mode == PURE_GENERATION_LOCALIZATION_MODE
        else LEGACY_REFERENCE_LOCALIZATION_MODE
    )
    if plan.get("mode") != expected_plan_mode:
        raise ValueError(
            f"the frozen pre-attempt localization plan must use {expected_plan_mode}"
        )
    if policy_mode == PURE_GENERATION_LOCALIZATION_MODE:
        rebuild_flag = plan.get("pure_rebuild_allowed")
        if rebuild_flag is not None and rebuild_flag is not False:
            raise ValueError("pure-generation localization plans cannot carry rebuild approval")
    elif plan.get("pure_rebuild_allowed") is not False:
        raise ValueError("legacy reference-edit plans cannot pre-authorize pure rebuild")
    ratio_adaptation = plan.get("ratio_adaptation")
    if not isinstance(ratio_adaptation, dict):
        raise ValueError("localization plan ratio_adaptation must be an object")
    if (
        policy_mode != PURE_GENERATION_LOCALIZATION_MODE
        and ratio_adaptation.get("required") is not False
    ):
        raise ValueError(
            "legacy localization plan ratio adaptation is fail-closed without a structured recomputable mapping"
        )
    _, _, mask_errors = _localization_text_edit_mask(
        (int(item.get("width", 0) or 0), int(item.get("height", 0) or 0)),
        plan.get("text_blocks"),
    )
    if mask_errors:
        raise ValueError("invalid localization text masks: " + "; ".join(mask_errors))
    plan_errors = validate_localization_plan_contract(item, manifest, plan)
    if plan_errors:
        raise ValueError("invalid localization plan contract: " + "; ".join(plan_errors))
    for other in manifest.get("items", []):
        if other.get("task_id") == item.get("task_id"):
            continue
        other_registration = other.get("localization_plan_registration")
        if isinstance(other_registration, dict) and canonical_path_key(
            Path(str(other_registration.get("path") or ""))
        ) == canonical_path_key(artifact):
            raise ValueError("each localization task requires its own frozen plan artifact")
    digest = sha256_file(artifact)
    registration = {
        "schema_version": 1,
        "producer": "xobi-img.update_manifest",
        "contract": "frozen-localization-plan-v1",
        "manifest_id": manifest.get("manifest_id"),
        "task_id": item.get("task_id"),
        "source_sha256": item.get("source_sha256"),
        "path": str(artifact),
        "sha256": digest,
        "registered_at": registered_at,
        "attempts_at_registration": 0,
        "attempt_history_count_at_registration": 0,
    }
    return plan, registration


def load_localization_composition_artifact(
    value: str,
    item: dict[str, Any],
    manifest: dict[str, Any],
    work_dir: Path,
) -> dict[str, Any]:
    raw_path = Path(value).expanduser().absolute()
    for candidate in (raw_path, *raw_path.parents):
        if candidate.is_symlink() or bool(getattr(candidate, "is_junction", lambda: False)()):
            raise ValueError(
                f"localization composition path must not traverse a symlink or junction: {candidate}"
            )
    artifact = raw_path.resolve()
    try:
        artifact.relative_to(work_dir.resolve())
    except ValueError as exc:
        raise ValueError("localization composition artifact must be inside .xobi/work") from exc
    if artifact.suffix.lower() != ".json" or not artifact.is_file():
        raise ValueError("localization composition must name an existing .json artifact")
    try:
        record = json.loads(artifact.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid localization composition artifact: {exc}") from exc
    if not isinstance(record, dict):
        raise ValueError("localization composition artifact must contain a JSON object")
    registration = {
        "schema_version": 1,
        "producer": "xobi-img.update_manifest",
        "contract": "frozen-localization-composition-v1",
        "artifact_path": str(artifact),
        "artifact_sha256": sha256_file(artifact),
        "record": record,
    }
    proposed = deepcopy(item)
    proposed["localization_composition"] = registration
    validation_errors = validate_localization_composition(proposed, manifest)
    if validation_errors:
        raise ValueError("invalid localization composition: " + "; ".join(validation_errors))
    return registration


def load_logo_geometry_artifact(
    value: str,
    item: dict[str, Any],
    manifest: dict[str, Any],
    work_dir: Path,
) -> dict[str, Any]:
    """Select one canonical item from apply_logo's official schema-v1 wrapper."""
    artifact = Path(value).expanduser().resolve()
    try:
        artifact.relative_to(work_dir.resolve())
    except ValueError as exc:
        raise ValueError("logo-geometry artifact must be inside .xobi/work") from exc
    if artifact.suffix.lower() != ".json" or not artifact.is_file():
        raise ValueError("logo-geometry must name an existing .json artifact file")
    if artifact.is_symlink() or bool(getattr(artifact, "is_junction", lambda: False)()):
        raise ValueError("logo-geometry artifact must not be a symlink or junction")
    try:
        wrapper = json.loads(artifact.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid logo-geometry JSON artifact: {exc}") from exc
    if not isinstance(wrapper, dict) or wrapper.get("schema_version") != 1:
        raise ValueError("logo-geometry must be the apply_logo schema_version 1 wrapper, not bare geometry")
    if wrapper.get("producer") != "xobi-img.apply_logo" or wrapper.get("contract") != "locked-logo-v1":
        raise ValueError("logo-geometry must be the official locked apply_logo wrapper")

    active_logo, active_digest, active_errors = active_logo_asset(manifest)
    if active_errors or active_logo is None or active_digest is None:
        raise ValueError("active Logo validation failed: " + "; ".join(active_errors or ["asset is missing"]))
    locked = {
        "reference_short_side": LOGO_REFERENCE_SHORT_SIDE,
        "reference_box": list(LOGO_REFERENCE_BOX),
        "alpha_threshold": LOGO_ALPHA_THRESHOLD,
        "safe_padding": LOGO_SAFE_PADDING,
        "anchor_tolerance": LOGO_ANCHOR_TOLERANCE,
    }
    for field, expected in locked.items():
        if wrapper.get(field) != expected:
            raise ValueError(f"logo-geometry wrapper {field} must use the locked standard {expected}")
    logo_value = str(wrapper.get("logo") or "")
    if not logo_value or canonical_path_key(Path(logo_value)) != canonical_path_key(active_logo):
        raise ValueError("logo-geometry wrapper does not use the active manifest Logo")
    if wrapper.get("logo_sha256") != active_digest:
        raise ValueError("logo-geometry wrapper Logo hash does not match the active manifest Logo")
    opaque_approval = wrapper.get("opaque_review_approved")
    if not isinstance(opaque_approval, bool):
        raise ValueError("logo-geometry wrapper opaque_review_approved must be boolean")
    if logo_canvas_requires_review(active_logo, LOGO_ALPHA_THRESHOLD) and not opaque_approval:
        raise ValueError("logo-geometry wrapper requires explicit opaque/edge-reaching review approval")

    allowed_sources = {
        canonical_path_key(Path(str(candidate)))
        for candidate in (
            item.get("source"),
            item.get("base_output"),
            item.get("localized_base"),
            item.get("conflict_reference_base"),
            item.get("prepared_base"),
        )
        if candidate
    }
    if not allowed_sources:
        raise ValueError("logo-geometry registration requires a frozen base image")
    wrapper_items = wrapper.get("items")
    if not isinstance(wrapper_items, list):
        raise ValueError("logo-geometry wrapper items must be a list")
    matches = [
        entry
        for entry in wrapper_items
        if isinstance(entry, dict)
        and entry.get("source")
        and canonical_path_key(Path(str(entry["source"]))) in allowed_sources
    ]
    if len(matches) != 1:
        raise ValueError("logo-geometry wrapper must contain exactly one frozen base image match")
    selected = matches[0]
    selected_source = Path(str(selected["source"])).resolve()
    if not selected_source.is_file():
        raise ValueError("logo-geometry frozen base image is missing")
    try:
        with Image.open(selected_source) as raw:
            selected_image = ImageOps.exif_transpose(raw)
            selected_image.load()
            canvas = [selected_image.width, selected_image.height]
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise ValueError(f"logo-geometry frozen base is not a readable image: {exc}") from exc
    if selected.get("canvas") != canvas:
        raise ValueError("logo-geometry canvas does not match its frozen base image")
    geometry_fields = {
        "source",
        "canvas",
        "scale",
        "logo_canvas",
        "visible_bbox",
        "safe_padding",
        "safe_zone",
        "right_module_anchor",
        "right_module_start_range",
        "right_available",
        "below_module_anchor",
        "below_module_start_range",
        "below_available",
    }
    missing = sorted(geometry_fields - selected.keys())
    if missing:
        raise ValueError("logo-geometry wrapper item is incomplete: " + ", ".join(missing))
    record = {
        "artifact_path": str(artifact),
        "artifact_sha256": sha256_file(artifact),
        "wrapper": {
            "schema_version": 1,
            "producer": wrapper["producer"],
            "contract": wrapper["contract"],
            "logo": str(active_logo),
            "logo_sha256": active_digest,
            **locked,
            "opaque_review_approved": opaque_approval,
        },
    }
    record.update({field: selected[field] for field in geometry_fields})
    return record


def merge_task_states(data: dict[str, Any], state_dir: Path) -> int:
    items = {str(item.get("task_id")): item for item in data.get("items", [])}
    merged = 0
    for path in sorted(state_dir.glob("*.json")):
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"unreadable task state {path}: {exc}") from exc
        task_id = str(state.get("task_id") or "")
        if not valid_task_id(task_id) or path.name != f"{task_id}.json":
            raise ValueError(f"invalid task state filename or task_id: {path}")
        item = items.get(task_id)
        if item is None:
            raise ValueError(f"orphan task state {path}: {task_id or '<missing task_id>'}")
        state_updated = str(state.get("updated_at") or "")
        item_updated = str(item.get("updated_at") or "")
        item_time = None
        try:
            state_time = datetime.fromisoformat(state_updated)
            if state_time.tzinfo is None:
                raise ValueError("timestamp lacks timezone")
            if state_time > datetime.now().astimezone() + timedelta(seconds=5):
                raise ValueError("timestamp is in the future")
            if item_updated:
                item_time = datetime.fromisoformat(item_updated)
                if item_time.tzinfo is None or item_time > datetime.now().astimezone() + timedelta(seconds=5):
                    raise ValueError("manifest item timestamp is invalid")
        except ValueError as exc:
            raise ValueError(f"invalid task state {path}: {exc}") from exc
        if item_time is not None and state_time <= item_time:
            continue
        if item.get("status") == "success":
            raise ValueError(f"invalid task state {path}: successful tasks are immutable")
        if state.get("worker_id") != item.get("worker_id"):
            raise ValueError(f"invalid task state {path}: worker assignment mismatch")
        proposed = deepcopy(item)
        for key in STATE_FIELDS - {"output_validation"}:
            if key in state:
                proposed[key] = state[key]
        if canonical_path_key(Path(str(proposed.get("output") or ""))) != canonical_path_key(Path(str(item.get("output") or ""))):
            raise ValueError(f"invalid task state {path}: output path changed")
        try:
            proposed_attempts = int(proposed.get("attempts", 0) or 0)
            current_attempts = int(item.get("attempts", 0) or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid task state {path}: attempts must be an integer") from exc
        if proposed_attempts < current_attempts:
            raise ValueError(f"invalid task state {path}: attempts decreased")
        attempt_record = state.get("attempt_record")
        attempt_candidate = deepcopy(proposed)
        if isinstance(attempt_record, dict):
            attempt_candidate.setdefault("attempt_history", []).append(attempt_record)
        if data.get("mode") == "localization":
            if proposed_attempts not in {current_attempts, current_attempts + 1}:
                raise ValueError(
                    f"invalid task state {path}: localization attempts must increment by exactly one"
                )
            if proposed_attempts == current_attempts + 1:
                if not isinstance(attempt_record, dict) or int(
                    attempt_record.get("attempt", 0) or 0
                ) != proposed_attempts:
                    raise ValueError(
                        f"invalid task state {path}: incremented attempt requires its matching record"
                    )
            elif attempt_record is not None:
                raise ValueError(
                    f"invalid task state {path}: attempt record requires a new attempt number"
                )
            for frozen_field in ("localization_plan", "localization_plan_registration"):
                if (
                    item.get(frozen_field) is not None
                    and frozen_field in state
                    and state.get(frozen_field) != item.get(frozen_field)
                ):
                    raise ValueError(
                        f"invalid task state {path}: frozen {frozen_field} cannot be replaced"
                    )
            if (
                item.get("pure_rebuild_approval") is not None
                and "pure_rebuild_approval" in state
                and state.get("pure_rebuild_approval") != item.get("pure_rebuild_approval")
            ):
                raise ValueError(
                    f"invalid task state {path}: pure_rebuild_approval cannot be replaced"
                )
            attempt_errors = validate_localization_attempt_contract(attempt_candidate, data)
            if attempt_errors:
                raise ValueError(f"invalid task state {path}: " + "; ".join(attempt_errors))
            plan_errors = validate_localization_plan_registration(attempt_candidate, data)
            if plan_errors:
                raise ValueError(f"invalid task state {path}: " + "; ".join(plan_errors))
        if data.get("workflow") == COMMERCE_MAIN_IMAGE_WORKFLOW:
            if proposed_attempts not in {current_attempts, current_attempts + 1}:
                raise ValueError(
                    f"invalid task state {path}: commerce attempts must increment by exactly one"
                )
            if proposed_attempts == current_attempts + 1:
                if not isinstance(attempt_record, dict) or int(
                    attempt_record.get("attempt", 0) or 0
                ) != proposed_attempts:
                    raise ValueError(
                        f"invalid task state {path}: incremented commerce attempt requires its matching record"
                    )
            elif attempt_record is not None:
                raise ValueError(
                    f"invalid task state {path}: commerce attempt record requires a new attempt number"
                )
            registering_plan = (
                item.get("main_image_plan") is None
                and item.get("main_image_plan_registration") is None
                and (
                    proposed.get("main_image_plan") is not None
                    or proposed.get("main_image_plan_registration") is not None
                )
            )
            if registering_plan:
                if (
                    current_attempts != 0
                    or item.get("attempt_history")
                    or proposed_attempts != current_attempts
                    or attempt_record is not None
                    or proposed.get("status") != "pending"
                    or proposed.get("main_image_quality_review") is not None
                ):
                    raise ValueError(
                        f"invalid task state {path}: main-image plan registration must be a separate pending pre-attempt update"
                    )
            for frozen_field in ("main_image_plan", "main_image_plan_registration"):
                if (
                    item.get(frozen_field) is not None
                    and frozen_field in state
                    and state.get(frozen_field) != item.get(frozen_field)
                ):
                    raise ValueError(
                        f"invalid task state {path}: frozen {frozen_field} cannot be replaced"
                    )
            if (
                item.get("main_image_quality_review") is not None
                and "main_image_quality_review" in state
                and state.get("main_image_quality_review") != item.get("main_image_quality_review")
            ):
                raise ValueError(
                    f"invalid task state {path}: frozen main_image_quality_review cannot be replaced"
                )
            plan_errors = validate_main_image_plan_registration(attempt_candidate, data)
            if plan_errors:
                raise ValueError(f"invalid task state {path}: " + "; ".join(plan_errors))
            attempt_errors = validate_main_image_attempt_contract(attempt_candidate, data)
            if attempt_errors:
                raise ValueError(f"invalid task state {path}: " + "; ".join(attempt_errors))
        if isinstance(attempt_record, dict) and attempt_record.get("attempt_stage") == "logo_conflict":
            if proposed_attempts != current_attempts + 1:
                raise ValueError(
                    f"invalid task state {path}: logo_conflict attempts must increment by exactly one"
                )
        conflict_errors = validate_logo_conflict_attempt_contract(attempt_candidate, data)
        if conflict_errors:
            raise ValueError(f"invalid task state {path}: " + "; ".join(conflict_errors))
        status = str(proposed.get("status") or "")
        if status not in {"pending", "success", "skipped", "failed"}:
            raise ValueError(f"invalid task state {path}: unsupported status")
        if status == "failed" and not str(proposed.get("error") or "").strip():
            raise ValueError(f"invalid task state {path}: failed status requires an error")
        ensure_unique_output(proposed, data)
        if status == "success":
            validation, validation_errors = validate_output(proposed, data)
            if validation_errors:
                raise ValueError(f"invalid task state {path}: " + "; ".join(validation_errors))
            assert validation is not None
            contract_errors = validate_item_contract(proposed, data, validation)
            if contract_errors:
                raise ValueError(f"invalid task state {path}: " + "; ".join(contract_errors))
            ensure_unique_hash(proposed, validation, data)
            proposed["output_validation"] = validation
        else:
            proposed["output_validation"] = None
        for key in STATE_FIELDS:
            if key in proposed:
                item[key] = proposed[key]
        if attempt_record:
            history = item.setdefault("attempt_history", [])
            record_id = attempt_record.get("record_id")
            if not any(existing.get("record_id") == record_id for existing in history):
                history.append(attempt_record)
        merged += 1
    return merged


def ensure_unique_output(item: dict[str, Any], data: dict[str, Any]) -> None:
    output_key = canonical_path_key(Path(str(item["output"])))
    for other in data.get("items", []):
        if other.get("task_id") == item.get("task_id"):
            continue
        if canonical_path_key(Path(str(other.get("output") or ""))) == output_key:
            raise ValueError(f"output path duplicates {other.get('task_id')}")


def ensure_unique_hash(item: dict[str, Any], record: dict[str, Any], data: dict[str, Any]) -> None:
    digest = str(record["sha256"])
    for other in data.get("items", []):
        if other.get("task_id") == item.get("task_id") or other.get("status") != "success":
            continue
        current_record, errors = validate_output(other, data)
        if errors or current_record is None:
            raise ValueError(
                f"existing success {other.get('task_id')} is no longer valid: " + "; ".join(errors)
            )
        recorded = other.get("output_validation") or {}
        if not recorded.get("sha256"):
            raise ValueError(f"existing success {other.get('task_id')} is missing an output validation baseline")
        if recorded.get("sha256") and recorded.get("sha256") != current_record.get("sha256"):
            raise ValueError(f"existing success {other.get('task_id')} changed after validation")
        if current_record.get("sha256") == digest:
            raise ValueError(f"output content duplicates {other.get('task_id')}")


def inspect_main_image_candidate(item: dict[str, Any]) -> dict[str, Any]:
    candidate = Path(str(item.get("output") or "")).resolve()
    if not candidate.is_file():
        raise ValueError("commerce main-image quality attempt requires a candidate at the preallocated output path")
    try:
        with Image.open(candidate) as raw:
            rendered = ImageOps.exif_transpose(raw)
            rendered.load()
            width, height = rendered.size
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise ValueError(f"commerce main-image candidate is not a readable image: {exc}") from exc
    return {
        "candidate_path": str(candidate),
        "candidate_sha256": sha256_file(candidate),
        "candidate_width": width,
        "candidate_height": height,
    }


def inspect_logo_conflict_candidate(
    item: dict[str, Any],
    work_dir: Path,
) -> dict[str, Any]:
    candidate_value = str(item.get("prepared_base") or "")
    if not candidate_value:
        raise ValueError("logo_conflict candidate attempts require --prepared-base")
    candidate = Path(candidate_value).resolve()
    try:
        candidate.relative_to(work_dir.resolve())
    except ValueError as exc:
        raise ValueError("logo_conflict prepared_base candidates must remain inside .xobi/work") from exc
    if not candidate.is_file():
        raise ValueError("logo_conflict candidate attempt requires a readable prepared_base file")
    try:
        with Image.open(candidate) as raw:
            if int(getattr(raw, "n_frames", 1) or 1) != 1:
                raise ValueError("logo_conflict prepared_base candidate must be a single-frame image")
            rendered = ImageOps.exif_transpose(raw)
            rendered.load()
            width, height = rendered.size
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise ValueError(f"logo_conflict prepared_base candidate is not a readable static image: {exc}") from exc
    return {
        "candidate_path": str(candidate),
        "candidate_sha256": sha256_file(candidate),
        "candidate_width": width,
        "candidate_height": height,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Safely update one xobi-img task through an independent state file and locked manifest merge.")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--status", required=True, choices=["pending", "success", "skipped", "failed"])
    parser.add_argument("--worker-id", help="Must match the worker assigned by preflight.")
    parser.add_argument("--output")
    parser.add_argument("--attempts", type=int)
    parser.add_argument("--prompt-summary")
    parser.add_argument("--error")
    parser.add_argument("--failure-type", choices=["quality", "infrastructure"])
    parser.add_argument(
        "--attempt-stage",
        choices=[
            "pure_generation",
            "reference_edit",
            "pure_rebuild",
            "logo_conflict",
            COMMERCE_MAIN_IMAGE_ATTEMPT_STAGE,
        ],
    )
    parser.add_argument("--base-output")
    parser.add_argument("--localized-base")
    parser.add_argument("--conflict-reference-base")
    parser.add_argument("--prepared-base")
    parser.add_argument("--family-id")
    parser.add_argument("--logo-decision", choices=["direct_overlay", "regenerate_for_conflict"])
    parser.add_argument("--logo-geometry-json")
    parser.add_argument("--module-anchors-json")
    parser.add_argument("--localization-plan-json")
    parser.add_argument("--localization-composition-json")
    parser.add_argument("--main-image-plan-json")
    parser.add_argument(
        "--main-image-quality-review-json",
        help=(
            "Finalized review required with every candidate-producing commerce_main_image "
            "attempt (passed=false for quality failure; passed=true for acceptance)."
        ),
    )
    parser.add_argument("--logo-plan-file", type=Path)
    parser.add_argument("--layout-families-file", type=Path)
    parser.add_argument("--style-lock-file", type=Path)
    parser.add_argument("--degrade-to-single", action="store_true")
    parser.add_argument(
        "--pure-rebuild-approval",
        help="After three reference-edit quality failures, bind explicit approval to this manifest task and source.",
    )
    args = parser.parse_args()

    if args.attempts is not None and args.attempts < 0:
        parser.error("attempts must be non-negative")
    if args.status == "failed" and not (args.error or "").strip():
        parser.error("failed status requires --error")
    if args.pure_rebuild_approval is not None and not args.pure_rebuild_approval.strip():
        parser.error("pure-rebuild-approval must contain the user's explicit approval evidence")
    if not valid_task_id(args.task_id):
        parser.error("task-id contains unsafe filename characters")
    try:
        module_anchors = read_json_argument(args.module_anchors_json, "module-anchors")
    except ValueError as exc:
        parser.error(str(exc))

    manifest_path = args.manifest.resolve()
    if not manifest_path.is_file():
        parser.error(f"manifest not found: {manifest_path}")
    state_dir = manifest_path.parent / "work" / "task-state"
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = manifest_path.with_name(manifest_path.name + ".lock")

    try:
        with FileLock(lock_path):
            manifest_snapshot = manifest_path.read_bytes()
            report_path = manifest_path.parent / "report.md"
            report_snapshot = report_path.read_bytes() if report_path.is_file() else None
            data = load_manifest(manifest_path)
            merge_task_states(data, state_dir)
            structural_errors = validate_manifest(data, check_files=False)
            if structural_errors:
                raise ValueError("manifest integrity error: " + "; ".join(entry["error"] for entry in structural_errors))
            if is_legacy_read_only_manifest(data):
                raise ValueError(
                    "legacy manifests are read-only; migrate to the current pure-generation policy "
                    "before recording any new task update or image attempt"
                )
            matches = [item for item in data.get("items", []) if item.get("task_id") == args.task_id]
            if len(matches) != 1:
                raise ValueError(f"task-id must match exactly one item: {args.task_id}")
            current = matches[0]
            timestamp_value = datetime.fromisoformat(now_iso())
            current_updated_value = str(current.get("updated_at") or "")
            if current_updated_value:
                try:
                    current_updated_time = datetime.fromisoformat(current_updated_value)
                    if current_updated_time.tzinfo is None:
                        raise ValueError("timestamp lacks timezone")
                except ValueError as exc:
                    raise ValueError("current task updated_at is invalid") from exc
                if current_updated_time >= timestamp_value:
                    timestamp_value = current_updated_time + timedelta(microseconds=1)
            if timestamp_value > datetime.now().astimezone() + timedelta(seconds=5):
                raise ValueError("cannot create a monotonic task timestamp within the allowed clock-skew window")
            timestamp = timestamp_value.isoformat(timespec="microseconds")
            work_dir = manifest_path.parent / "work"
            if args.worker_id and current.get("worker_id") != args.worker_id:
                raise ValueError(f"task belongs to {current.get('worker_id')}, not {args.worker_id}")
            if current.get("status") == "success":
                raise ValueError("successful tasks are immutable; do not rerun or overwrite them")
            current_attempts = int(current.get("attempts", 0) or 0)
            if args.attempts is not None and args.attempts < current_attempts:
                raise ValueError("attempts must not decrease")
            commerce_main_image = data.get("workflow") == COMMERCE_MAIN_IMAGE_WORKFLOW
            if commerce_main_image and current.get("status") == "failed":
                terminal_quality_failures = [
                    record
                    for record in current.get("attempt_history", [])
                    if isinstance(record, dict)
                    and record.get("attempt_stage") == COMMERCE_MAIN_IMAGE_ATTEMPT_STAGE
                    and record.get("failure_type") == "quality"
                ]
                if len(terminal_quality_failures) >= 3:
                    raise ValueError(
                        "commerce main-image task is terminal after three quality failures; start a new task to redo it"
                    )
            if not commerce_main_image and (
                args.main_image_plan_json is not None
                or args.main_image_quality_review_json is not None
                or args.attempt_stage == COMMERCE_MAIN_IMAGE_ATTEMPT_STAGE
            ):
                raise ValueError(
                    "main-image plan, review, and attempt stage are only valid for commerce_main_image"
                )
            if commerce_main_image:
                has_frozen_main_image_plan = isinstance(current.get("main_image_plan"), dict) and isinstance(
                    current.get("main_image_plan_registration"), dict
                )
                if args.main_image_plan_json is not None:
                    if args.status != "pending":
                        raise ValueError(
                            "main-image plan must be registered in a separate pending update before success"
                        )
                    if (
                        has_frozen_main_image_plan
                        or current.get("main_image_plan") is not None
                        or current.get("main_image_plan_registration") is not None
                    ):
                        raise ValueError("the frozen main-image plan cannot be replaced or re-registered")
                    if current_attempts != 0 or current.get("attempt_history"):
                        raise ValueError("main-image plan must be registered before the first attempt")
                    if args.attempts is not None:
                        raise ValueError("main-image plan registration must be a separate pre-attempt update")
                    if any(
                        value is not None
                        for value in (
                            args.main_image_quality_review_json,
                            args.output,
                            args.failure_type,
                            args.attempt_stage,
                            args.base_output,
                            args.localized_base,
                            args.conflict_reference_base,
                            args.prepared_base,
                            args.logo_decision,
                            args.logo_geometry_json,
                            args.module_anchors_json,
                            args.localization_plan_json,
                            args.localization_composition_json,
                            args.pure_rebuild_approval,
                            args.logo_plan_file,
                            args.layout_families_file,
                            args.style_lock_file,
                        )
                    ):
                        raise ValueError("main-image plan registration must be a separate pre-attempt update")
                existing_main_image_records = [
                    record
                    for record in current.get("attempt_history", [])
                    if isinstance(record, dict)
                    and record.get("attempt_stage") == COMMERCE_MAIN_IMAGE_ATTEMPT_STAGE
                ]
                increments_attempt = args.attempts == current_attempts + 1
                if args.attempts is not None and args.attempt_stage is None:
                    args.attempt_stage = COMMERCE_MAIN_IMAGE_ATTEMPT_STAGE
                main_stage_attempt = (
                    args.attempts is not None
                    and args.attempt_stage == COMMERCE_MAIN_IMAGE_ATTEMPT_STAGE
                )
                main_candidate_attempt = (
                    main_stage_attempt and args.failure_type != "infrastructure"
                )
                main_infrastructure_attempt = (
                    main_stage_attempt and args.failure_type == "infrastructure"
                )
                commerce_activity = (
                    main_stage_attempt
                    or args.status == "success"
                    or args.attempts is not None
                    or args.failure_type is not None
                    or args.attempt_stage == "logo_conflict"
                    or args.main_image_quality_review_json is not None
                )
                if commerce_activity and args.main_image_plan_json is None and not has_frozen_main_image_plan:
                    raise ValueError(
                        "commerce main-image attempt requires a frozen plan registered before the first attempt"
                    )
                if main_stage_attempt:
                    if not increments_attempt:
                        raise ValueError("commerce main-image attempts must increment --attempts by exactly one")
                    accepted_main_candidates = [
                        record
                        for record in existing_main_image_records
                        if record.get("failure_type") is None
                        and record.get("status") in {"pending", "success"}
                    ]
                    if accepted_main_candidates:
                        raise ValueError(
                            "commerce main-image stage is closed after its accepted reviewed candidate"
                        )
                    existing_main_candidates = [
                        record
                        for record in existing_main_image_records
                        if record.get("failure_type") != "infrastructure"
                    ]
                    existing_main_infrastructure = [
                        record
                        for record in existing_main_image_records
                        if record.get("failure_type") == "infrastructure"
                    ]
                    if main_candidate_attempt and len(existing_main_candidates) >= 3:
                        raise ValueError("commerce main-image quality attempt budget of 3 is exhausted")
                    if main_infrastructure_attempt and len(existing_main_infrastructure) >= 4:
                        raise ValueError("commerce main-image infrastructure attempt budget of 4 is exhausted")
                    if args.failure_type == "quality":
                        prior_quality_failures = sum(
                            record.get("failure_type") == "quality"
                            for record in existing_main_candidates
                        )
                        failure_number = prior_quality_failures + 1
                        required_status = "failed" if failure_number == 3 else "pending"
                        if args.status != required_status:
                            raise ValueError(
                                f"commerce main-image quality failure {failure_number} requires {required_status} status"
                            )
                if main_candidate_attempt and args.main_image_quality_review_json is None:
                    raise ValueError(
                        "every commerce main-image candidate attempt requires its finalized quality review"
                    )
                if main_candidate_attempt and args.failure_type is None:
                    if data.get("logo"):
                        if args.status != "pending" or args.base_output is None:
                            raise ValueError(
                                "Logo-combined accepted main-image candidate requires pending status and "
                                "--base-output in the same attempt"
                            )
                    elif args.status != "success":
                        raise ValueError(
                            "accepted main-image candidate without Logo must complete as success in the same attempt"
                        )
                if main_infrastructure_attempt and args.main_image_quality_review_json is not None:
                    raise ValueError(
                        "commerce main-image infrastructure attempts cannot include a quality review"
                    )
                if args.main_image_quality_review_json is not None and not main_candidate_attempt:
                    raise ValueError(
                        "a main-image quality review must be registered with its candidate-producing commerce attempt"
                    )
                if (
                    args.status == "success"
                    and args.main_image_quality_review_json is None
                    and current.get("main_image_quality_review") is None
                ):
                    raise ValueError(
                        "commerce main-image success requires a previously registered passing quality review"
                    )
            success_localization_execution_stage: str | None = None
            deterministic_logo_finalize = False
            if data.get("mode") == "localization":
                localization_policy = data.get("localization_policy")
                policy_mode = (
                    str(localization_policy.get("mode") or "")
                    if isinstance(localization_policy, dict)
                    else ""
                )
                pure_generation_manifest = policy_mode == PURE_GENERATION_LOCALIZATION_MODE
                deterministic_logo_finalize = bool(
                    args.status == "success"
                    and args.attempts is None
                    and args.attempt_stage is None
                    and data.get("logo")
                    and current.get("logo_decision") == "direct_overlay"
                )
                if deterministic_logo_finalize:
                    status_only_forbidden = {
                        "--output": args.output,
                        "--prompt-summary": args.prompt_summary,
                        "--error": args.error,
                        "--failure-type": args.failure_type,
                        "--base-output": args.base_output,
                        "--localized-base": args.localized_base,
                        "--conflict-reference-base": args.conflict_reference_base,
                        "--prepared-base": args.prepared_base,
                        "--family-id": args.family_id,
                        "--logo-decision": args.logo_decision,
                        "--logo-geometry-json": args.logo_geometry_json,
                        "--module-anchors-json": args.module_anchors_json,
                        "--localization-plan-json": args.localization_plan_json,
                        "--localization-composition-json": args.localization_composition_json,
                        "--main-image-plan-json": args.main_image_plan_json,
                        "--main-image-quality-review-json": args.main_image_quality_review_json,
                        "--logo-plan-file": args.logo_plan_file,
                        "--layout-families-file": args.layout_families_file,
                        "--style-lock-file": args.style_lock_file,
                        "--degrade-to-single": True if args.degrade_to_single else None,
                        "--pure-rebuild-approval": args.pure_rebuild_approval,
                    }
                    supplied_mutations = [
                        option
                        for option, value in status_only_forbidden.items()
                        if value is not None
                    ]
                    if supplied_mutations:
                        raise ValueError(
                            "deterministic Logo finalization is a status-only transition and "
                            "cannot change task, image, or Logo lineage fields: "
                            + ", ".join(supplied_mutations)
                        )
                if (
                    args.status == "success"
                    and args.attempt_stage is None
                    and not deterministic_logo_finalize
                ):
                    args.attempt_stage = (
                        "pure_generation" if pure_generation_manifest else "reference_edit"
                    )
                if pure_generation_manifest and args.attempt_stage not in {
                    None,
                    "pure_generation",
                    "logo_conflict",
                }:
                    raise ValueError(
                        "new pure-generation localization manifests cannot use legacy attempt stages"
                    )
                if pure_generation_manifest and args.pure_rebuild_approval is not None:
                    raise ValueError(
                        "new pure-generation localization manifests do not use rebuild approval"
                    )
                has_frozen_plan = isinstance(current.get("localization_plan"), dict) and isinstance(
                    current.get("localization_plan_registration"), dict
                )
                if args.localization_plan_json is not None:
                    if args.status != "pending":
                        raise ValueError(
                            "localization plan must be registered in a separate pending update before success"
                        )
                    if has_frozen_plan or current.get("localization_plan") is not None or current.get(
                        "localization_plan_registration"
                    ) is not None:
                        raise ValueError("the frozen localization plan cannot be replaced or re-registered")
                    if int(current.get("attempts", 0) or 0) != 0 or current.get("attempt_history"):
                        raise ValueError("localization plan must be registered before the first attempt")
                    if args.attempts is not None:
                        raise ValueError("localization plan registration cannot also record an image attempt")
                    if any(
                        value is not None
                        for value in (
                            args.localized_base,
                            args.base_output,
                            args.conflict_reference_base,
                            args.prepared_base,
                            args.failure_type,
                            args.attempt_stage,
                            args.pure_rebuild_approval,
                            args.localization_composition_json,
                        )
                    ):
                        raise ValueError("localization plan registration must be a separate pre-attempt update")
                elif (
                    args.status == "success"
                    or args.failure_type is not None
                    or args.attempt_stage is not None
                    or args.attempts is not None
                ) and not has_frozen_plan:
                    raise ValueError(
                        "localization attempt requires a frozen plan registered before the first attempt"
                    )
                if args.failure_type == "quality" and args.attempt_stage is None:
                    raise ValueError("localization quality failures require --attempt-stage")
                records_image_attempt = (
                    not deterministic_logo_finalize
                    and (
                        args.status == "success"
                        or args.failure_type is not None
                        or args.attempts is not None
                    )
                )
                if records_image_attempt:
                    if args.attempts != current_attempts + 1:
                        raise ValueError(
                            "localization image attempts must increment --attempts by exactly one"
                        )
                    if args.attempt_stage is None:
                        raise ValueError("localization image attempts require --attempt-stage")
                    if args.attempt_stage != "logo_conflict" and any(
                        isinstance(record, dict)
                        and record.get("failure_type") is None
                        and record.get("status") in {"pending", "success"}
                        and record.get("attempt_stage") == args.attempt_stage
                        for record in current.get("attempt_history", [])
                    ):
                        raise ValueError(
                            f"localization {args.attempt_stage} stage is closed after its accepted candidate"
                        )
                    if args.failure_type in {None, "quality"} and len(
                        quality_attempts_for_stage(current, args.attempt_stage)
                    ) >= 3:
                        raise ValueError(
                            f"{args.attempt_stage} quality attempt budget of 3 is exhausted"
                        )
                    if (
                        args.attempt_stage == "reference_edit"
                        and any(
                            isinstance(record, dict)
                            and record.get("attempt_stage") == "pure_rebuild"
                            for record in current.get("attempt_history", [])
                        )
                    ):
                        raise ValueError("cannot return to reference_edit after pure_rebuild starts")
                if deterministic_logo_finalize:
                    accepted_candidates = [
                        record
                        for record in current.get("attempt_history", [])
                        if isinstance(record, dict)
                        and record.get("failure_type") is None
                        and record.get("status") in {"pending", "success"}
                        and record.get("attempt_stage") in {
                            "pure_generation",
                            "reference_edit",
                            "pure_rebuild",
                        }
                    ]
                    if len(accepted_candidates) != 1:
                        raise ValueError(
                            "deterministic Logo finalization requires exactly one prior accepted localization candidate"
                        )
                    accepted_candidate = accepted_candidates[0]
                    if int(accepted_candidate.get("attempt", 0) or 0) != current_attempts:
                        raise ValueError(
                            "deterministic Logo finalization requires the accepted localization candidate "
                            "to remain the latest image attempt"
                        )
                    success_localization_execution_stage = str(
                        accepted_candidate.get("attempt_stage") or ""
                    )
                elif args.status == "success":
                    allowed_success_stages = (
                        {"pure_generation", "logo_conflict"}
                        if pure_generation_manifest
                        else {"reference_edit", "pure_rebuild", "logo_conflict"}
                    )
                    if args.attempt_stage not in allowed_success_stages:
                        raise ValueError(
                            "localization success uses an attempt stage forbidden by its manifest policy"
                        )
                    if args.attempt_stage in {"pure_generation", "reference_edit", "pure_rebuild"}:
                        success_localization_execution_stage = args.attempt_stage
                    else:
                        accepted_candidates = [
                            record
                            for record in current.get("attempt_history", [])
                            if isinstance(record, dict)
                            and record.get("failure_type") is None
                            and record.get("attempt_stage") in {
                                "pure_generation",
                                "reference_edit",
                                "pure_rebuild",
                            }
                        ]
                        if not accepted_candidates:
                            raise ValueError(
                                "logo_conflict success requires a prior accepted localization candidate"
                            )
                        accepted_candidates.sort(key=lambda record: int(record.get("attempt", 0) or 0))
                        success_localization_execution_stage = str(
                            accepted_candidates[-1].get("attempt_stage")
                        )
                if args.attempt_stage == "pure_rebuild":
                    approval_valid, approval_errors = validate_pure_rebuild_approval(current, data)
                    if not approval_valid:
                        detail = "; ".join(approval_errors) or "task-scoped approval is missing"
                        raise ValueError("pure rebuild attempt is not authorized: " + detail)
            elif args.pure_rebuild_approval is not None:
                raise ValueError("pure rebuild controls are only valid for localization manifests")
            elif args.attempt_stage not in (
                {None, "logo_conflict", COMMERCE_MAIN_IMAGE_ATTEMPT_STAGE}
                if commerce_main_image
                else {None, "logo_conflict"}
            ):
                raise ValueError(
                    "edit/generate ordinary visual attempts do not use localization stages; "
                    "only logo_conflict may be recorded explicitly"
                )
            if args.attempt_stage == "logo_conflict":
                if args.attempts != current_attempts + 1:
                    raise ValueError("logo_conflict attempts must increment --attempts by exactly one")
                logo_stage_records = [
                    record
                    for record in current.get("attempt_history", [])
                    if isinstance(record, dict)
                    and record.get("attempt_stage") == "logo_conflict"
                ]
                if any(
                    record.get("failure_type") is None
                    and record.get("status") in {"pending", "success"}
                    for record in logo_stage_records
                ):
                    raise ValueError("logo_conflict stage is closed after its accepted candidate")
                if args.failure_type == "infrastructure":
                    if any(
                        value is not None
                        for value in (
                            args.prepared_base,
                            args.output,
                            args.layout_families_file,
                            args.style_lock_file,
                        )
                    ):
                        raise ValueError(
                            "logo_conflict infrastructure attempts cannot claim prepared_base, output, "
                            "layout-family, or style-lock candidate artifacts"
                        )
                    prior_logo_infrastructure = [
                        record
                        for record in logo_stage_records
                        if record.get("failure_type") == "infrastructure"
                    ]
                    if len(prior_logo_infrastructure) >= 4:
                        raise ValueError("logo_conflict infrastructure attempt budget of 4 is exhausted")
                else:
                    prior_logo_candidates = [
                        record
                        for record in logo_stage_records
                        if record.get("failure_type") != "infrastructure"
                    ]
                    if len(prior_logo_candidates) >= 3:
                        raise ValueError("logo_conflict quality attempt budget of 3 is exhausted")
                if any(
                    value is not None
                    for value in (
                        args.logo_plan_file,
                        args.logo_geometry_json,
                        args.logo_decision,
                        args.conflict_reference_base,
                        args.base_output,
                        args.localized_base,
                        args.module_anchors_json,
                    )
                ):
                    raise ValueError(
                        "logo_conflict plan, geometry, decision, reference, anchors, and accepted base "
                        "must be frozen in separate prior updates"
                    )
                gate_errors = validate_logo_conflict_gate(
                    current,
                    data,
                    before_attempt=int(args.attempts),
                )
                if gate_errors:
                    raise ValueError("logo_conflict gate failed: " + "; ".join(gate_errors))
                if args.failure_type != "infrastructure" and args.prepared_base is None:
                    raise ValueError(
                        "logo_conflict candidate attempts require --prepared-base evidence"
                    )
            if data.get("mode") != "localization" and args.localization_composition_json is not None:
                raise ValueError("localization composition is only valid for localization manifests")
            if args.localization_composition_json is not None and args.status != "success":
                raise ValueError("localization composition can only be registered with success")
            if args.pure_rebuild_approval is not None and (
                args.status != "pending"
                or args.localization_plan_json is not None
                or args.localization_composition_json is not None
                or args.failure_type is not None
                or args.attempt_stage is not None
            ):
                raise ValueError("pure rebuild approval must be recorded as a separate pending task update")
            if args.failure_type is not None:
                if args.status == "success" or not str(args.error or "").strip():
                    raise ValueError("failed quality/infrastructure attempts require a non-success status and --error")
                if args.attempts is None or args.attempts < 1:
                    raise ValueError("failed quality/infrastructure attempts require a positive --attempts value")
                history = current.get("attempt_history")
                prior_attempts: set[int] = set()
                if isinstance(history, list):
                    for record in history:
                        if not isinstance(record, dict) or record.get("failure_type") != args.failure_type:
                            continue
                        if (
                            (data.get("mode") == "localization" or commerce_main_image)
                            and record.get("attempt_stage") != args.attempt_stage
                        ):
                            continue
                        try:
                            prior_attempts.add(int(record.get("attempt", 0) or 0))
                        except (TypeError, ValueError):
                            continue
                limit = 3 if args.failure_type == "quality" else 4
                if args.attempts not in prior_attempts and len({value for value in prior_attempts if value > 0}) >= limit:
                    raise ValueError(f"{args.failure_type} attempt budget of {limit} is exhausted")

            proposed = deepcopy(current)
            proposed["status"] = args.status
            if args.output is not None:
                supplied_output = Path(args.output).resolve()
                if canonical_path_key(supplied_output) != canonical_path_key(Path(str(current["output"]))):
                    raise ValueError("output must use the unique path preallocated by the manifest")
                proposed["output"] = str(supplied_output)
            if args.attempts is not None:
                proposed["attempts"] = args.attempts
            if args.prompt_summary is not None:
                proposed["prompt_summary"] = args.prompt_summary
            proposed["error"] = args.error.strip() if args.error else None
            if args.base_output is not None:
                proposed["base_output"] = str(Path(args.base_output).resolve())
            if args.localized_base is not None:
                proposed["localized_base"] = str(Path(args.localized_base).resolve())
            if args.conflict_reference_base is not None:
                proposed["conflict_reference_base"] = str(Path(args.conflict_reference_base).resolve())
            if args.prepared_base is not None:
                proposed["prepared_base"] = str(Path(args.prepared_base).resolve())
            if args.family_id is not None:
                proposed["family_id"] = args.family_id
            if args.logo_decision is not None:
                proposed["logo_decision"] = args.logo_decision
            if args.logo_geometry_json is not None:
                if current.get("logo_geometry") is not None:
                    raise ValueError("frozen logo geometry cannot be replaced or re-registered")
                if (
                    proposed.get("logo_decision") == "regenerate_for_conflict"
                    and not proposed.get("conflict_reference_base")
                ):
                    raise ValueError(
                        "regenerate_for_conflict geometry registration requires conflict_reference_base"
                    )
                proposed["logo_geometry"] = load_logo_geometry_artifact(
                    args.logo_geometry_json,
                    proposed,
                    data,
                    manifest_path.parent / "work",
                )
                proposed["logo_geometry"]["registered_at"] = timestamp
                proposed["logo_geometry"]["revision_at_registration"] = int(
                    data.get("revision", 0) or 0
                )
            if module_anchors is not None:
                proposed["module_anchors"] = module_anchors
            if args.main_image_plan_json is not None:
                main_image_plan, main_image_registration = load_main_image_plan_artifact(
                    args.main_image_plan_json,
                    current,
                    data,
                    work_dir,
                    timestamp,
                )
                proposed["main_image_plan"] = main_image_plan
                proposed["main_image_plan_registration"] = main_image_registration
            if args.localization_plan_json is not None:
                localization_plan, localization_registration = load_localization_plan_artifact(
                    args.localization_plan_json,
                    current,
                    data,
                    work_dir,
                    timestamp,
                )
                proposed["localization_plan"] = localization_plan
                proposed["localization_plan_registration"] = localization_registration
            attempt_main_image_review_registration: dict[str, Any] | None = None
            if args.main_image_quality_review_json is not None:
                attempt_main_image_review_registration = load_main_image_quality_review_artifact(
                    args.main_image_quality_review_json,
                    proposed,
                    data,
                    work_dir,
                    timestamp,
                    int(proposed.get("attempts", 0) or 0),
                )
                expected_review_passed = args.failure_type is None
                if attempt_main_image_review_registration.get("passed") is not expected_review_passed:
                    outcome = "passing" if expected_review_passed else "failing"
                    raise ValueError(
                        f"commerce main-image candidate outcome requires a {outcome} finalized review"
                    )
                if expected_review_passed:
                    if current.get("main_image_quality_review") is not None:
                        raise ValueError(
                            "the accepted main-image quality review cannot be replaced or re-registered"
                        )
                    proposed["main_image_quality_review"] = attempt_main_image_review_registration
            if args.status == "success" and data.get("mode") == "localization":
                policy = data.get("localization_policy")
                pure_generation_manifest = (
                    isinstance(policy, dict)
                    and policy.get("mode") == PURE_GENERATION_LOCALIZATION_MODE
                )
                execution_stage = success_localization_execution_stage or (
                    "pure_generation" if pure_generation_manifest else "reference_edit"
                )
                if execution_stage == "pure_rebuild":
                    approval_valid = validate_pure_rebuild_approval(proposed, data)[0]
                    if not approval_valid:
                        raise ValueError("pure rebuild success requires valid task-scoped approval")
                    if (
                        args.attempt_stage == "pure_rebuild"
                        and int(proposed.get("attempts", 0) or 0) <= int(current.get("attempts", 0) or 0)
                    ):
                        raise ValueError(
                            "a pure rebuild result must use a new attempt after reference-edit failures"
                        )
                proposed["localization_execution_stage"] = execution_stage
                if proposed["localization_execution_stage"] == "reference_edit":
                    if args.localization_composition_json is not None:
                        proposed["localization_composition"] = load_localization_composition_artifact(
                            args.localization_composition_json,
                            proposed,
                            data,
                            work_dir,
                        )
                elif args.localization_composition_json is not None:
                    raise ValueError(
                        f"{proposed['localization_execution_stage']} success must not use text-box composition provenance"
                    )

            accepted_base_registration: dict[str, Any] | None = None
            if (
                args.attempts is not None
                and args.failure_type is None
                and args.status in {"pending", "success"}
            ):
                accepted_base_kind: str | None = None
                if data.get("mode") == "localization" and args.attempt_stage == "pure_generation":
                    accepted_base_kind = "localized_base"
                elif (
                    data.get("mode") in {"edit", "generate"}
                    and args.attempt_stage in {None, COMMERCE_MAIN_IMAGE_ATTEMPT_STAGE}
                    and args.base_output is not None
                ):
                    accepted_base_kind = "base_output"
                if accepted_base_kind is not None:
                    accepted_base_value = str(proposed.get(accepted_base_kind) or "")
                    if not accepted_base_value:
                        raise ValueError(
                            f"accepted pure-generation attempt requires --{accepted_base_kind.replace('_', '-')}"
                        )
                    accepted_base_path = Path(accepted_base_value).resolve()
                    if not accepted_base_path.is_file():
                        raise ValueError("accepted pure-generation base image is missing")
                    try:
                        with Image.open(accepted_base_path) as raw_accepted_base:
                            accepted_image = ImageOps.exif_transpose(raw_accepted_base)
                            accepted_image.load()
                    except (OSError, UnidentifiedImageError, ValueError) as exc:
                        raise ValueError(f"accepted pure-generation base is not a readable image: {exc}") from exc
                    accepted_base_registration = {
                        "kind": accepted_base_kind,
                        "policy": "no_reference_pure_generation",
                        "path": str(accepted_base_path),
                        "sha256": sha256_file(accepted_base_path),
                    }
                    if (
                        commerce_main_image
                        and args.attempt_stage == COMMERCE_MAIN_IMAGE_ATTEMPT_STAGE
                        and sha256_file(Path(str(proposed.get("output") or "")).resolve())
                        != accepted_base_registration["sha256"]
                    ):
                        raise ValueError(
                            "commerce main-image accepted base must be byte-identical to the recorded candidate"
                        )
                    if data.get("mode") == "localization":
                        guard_probe = deepcopy(proposed)
                        guard_probe["localization_execution_stage"] = "pure_generation"
                        guard_record, guard_errors = localization_visual_guard(guard_probe, data)
                        if guard_errors:
                            raise ValueError(
                                "accepted localization base failed its visual guard: "
                                + "; ".join(guard_errors)
                            )
                        proposed["localization_validation"] = guard_record

            for argument, field in (
                (args.logo_plan_file, "logo_plan"),
                (args.layout_families_file, "layout_families"),
                (args.style_lock_file, "style_lock"),
            ):
                if argument is None:
                    continue
                plan_path = argument.resolve()
                try:
                    plan_path.relative_to(work_dir.resolve())
                except ValueError as exc:
                    raise ValueError(f"{field} file must be inside .xobi/work") from exc
                if not plan_path.is_file():
                    raise ValueError(f"{field} file not found: {plan_path}")
                json.loads(plan_path.read_text(encoding="utf-8"))
                plan_digest = sha256_file(plan_path)
                existing_registration = data.get(field)
                if (
                    field == "logo_plan"
                    and isinstance(existing_registration, dict)
                    and existing_registration.get("sha256")
                ):
                    raise ValueError("frozen logo_plan cannot be replaced or re-registered")
                if (
                    field == "layout_families"
                    and isinstance(existing_registration, dict)
                    and existing_registration.get("sha256")
                    and any(entry.get("status") == "success" for entry in data.get("items", []))
                    and (
                        existing_registration.get("sha256") != plan_digest
                        or canonical_path_key(Path(str(existing_registration.get("path") or "")))
                        != canonical_path_key(plan_path)
                    )
                ):
                    raise ValueError("layout_families is frozen after the first approved pilot success")
                data[field] = {
                    "path": str(plan_path),
                    "sha256": plan_digest,
                    "registered_at": timestamp,
                    "revision_at_registration": int(data.get("revision", 0) or 0),
                }
            if args.pure_rebuild_approval is not None:
                policy = data.get("localization_policy")
                if policy is None:
                    raise ValueError("pure reconstruction approval is only valid for localization manifests")
                if policy.get("mode") == PURE_GENERATION_LOCALIZATION_MODE:
                    raise ValueError(
                        "new pure-generation localization manifests do not use rebuild approval"
                    )
                failures = reference_edit_quality_failures(current)
                if len(failures) < 3:
                    raise ValueError(
                        "pure reconstruction approval requires three recorded reference-edit quality failures"
                    )
                manifest_id = str(data.get("manifest_id") or "")
                if not manifest_id:
                    manifest_id = f"xobi-{uuid.uuid4().hex}"
                    data["manifest_id"] = manifest_id
                latest_failure = max(failures, key=lambda record: str(record.get("recorded_at") or ""))
                proposed["pure_rebuild_approval"] = {
                    "scope": "task",
                    "manifest_id": manifest_id,
                    "task_id": args.task_id,
                    "source_sha256": current.get("source_sha256"),
                    "evidence": args.pure_rebuild_approval.strip(),
                    "recorded_at": timestamp,
                    "approved_after_attempt_record_id": latest_failure.get("record_id"),
                }
                policy["authorization_scope"] = "task"
                policy["pure_rebuild_allowed"] = False
                policy["user_approval"] = None

            accepted_logo_relocation_validation: dict[str, Any] | None = None
            if args.attempt_stage == "logo_conflict" and args.failure_type is None:
                relocation_record, relocation_errors = recompute_logo_relocation_validation(
                    proposed,
                    data,
                )
                if relocation_errors or not isinstance(relocation_record, dict) or relocation_record.get(
                    "passed"
                ) is not True:
                    detail = "; ".join(relocation_errors) or "relocation evidence did not pass"
                    raise ValueError(
                        "accepted Logo conflict candidate failed its relocation guard: " + detail
                    )
                accepted_logo_relocation_validation = relocation_record
                proposed["logo_relocation_validation"] = relocation_record

            ensure_unique_output(proposed, data)
            if args.status == "success":
                source_value = str(proposed.get("source") or "")
                if data.get("mode") != "generate":
                    source_path = Path(source_value).resolve()
                    if not source_path.is_file():
                        raise ValueError("success validation failed: source file is missing")
                    if proposed.get("source_sha256") and sha256_file(source_path) != proposed.get("source_sha256"):
                        raise ValueError("success validation failed: source hash changed after preflight")
                logo_record = data.get("logo") or {}
                if logo_record.get("source"):
                    logo_path = Path(str(logo_record["source"])).resolve()
                    if not logo_path.is_file():
                        raise ValueError("success validation failed: Logo source file is missing")
                    if logo_record.get("source_sha256") and sha256_file(logo_path) != logo_record.get("source_sha256"):
                        raise ValueError("success validation failed: Logo source hash changed after preflight")
                validation, validation_errors = validate_output(proposed, data)
                if validation_errors:
                    raise ValueError("success validation failed: " + "; ".join(validation_errors))
                assert validation is not None
                if (
                    data.get("mode") == "localization"
                    and isinstance(proposed.get("localization_plan"), dict)
                    and proposed.get("localized_base")
                ):
                    localization_record, localization_errors = localization_visual_guard(proposed, data)
                    proposed["localization_validation"] = localization_record
                    if localization_errors:
                        raise ValueError("success localization guard failed: " + "; ".join(localization_errors))
                if proposed.get("logo_decision") == "regenerate_for_conflict":
                    relocation_record = accepted_logo_relocation_validation
                    relocation_errors: list[str] = []
                    if relocation_record is None:
                        relocation_record, relocation_errors = recompute_logo_relocation_validation(
                            proposed,
                            data,
                        )
                    proposed["logo_relocation_validation"] = relocation_record
                    if relocation_errors:
                        raise ValueError("success Logo relocation guard failed: " + "; ".join(relocation_errors))
                else:
                    proposed["logo_relocation_validation"] = None
                contract_errors = validate_item_contract(proposed, data, validation)
                if contract_errors:
                    raise ValueError("success contract failed: " + "; ".join(contract_errors))
                ensure_unique_hash(proposed, validation, data)
                proposed["output_validation"] = validation
            else:
                proposed["output_validation"] = None

            proposed["updated_at"] = timestamp
            attempt_number = int(proposed.get("attempts", 0) or 0)
            attempt_record = None
            if args.attempts is not None or args.error or args.failure_type or args.attempt_stage:
                attempt_record = {
                    "record_id": f"{args.task_id}:{attempt_number}:{timestamp}",
                    "attempt": attempt_number,
                    "status": args.status,
                    "failure_type": args.failure_type,
                    "attempt_stage": args.attempt_stage,
                    "error": args.error,
                    "recorded_at": timestamp,
                }
                if commerce_main_image and args.attempt_stage == COMMERCE_MAIN_IMAGE_ATTEMPT_STAGE:
                    plan_registration = proposed.get("main_image_plan_registration")
                    if isinstance(plan_registration, dict):
                        attempt_record["main_image_plan_sha256"] = plan_registration.get("sha256")
                    if args.failure_type != "infrastructure":
                        attempt_record.update(inspect_main_image_candidate(proposed))
                        if attempt_main_image_review_registration is not None:
                            attempt_record["quality_review"] = attempt_main_image_review_registration
                if args.attempt_stage == "logo_conflict" and args.failure_type != "infrastructure":
                    attempt_record.update(inspect_logo_conflict_candidate(proposed, work_dir))
                    if args.failure_type is None:
                        attempt_record["logo_relocation_validation"] = deepcopy(
                            accepted_logo_relocation_validation
                        )
                if accepted_base_registration is not None:
                    attempt_record["accepted_base"] = accepted_base_registration
            if attempt_record is not None and args.attempt_stage == "logo_conflict":
                attempt_candidate = deepcopy(proposed)
                attempt_candidate.setdefault("attempt_history", []).append(attempt_record)
                conflict_errors = validate_logo_conflict_attempt_contract(attempt_candidate, data)
                if conflict_errors:
                    raise ValueError(
                        "logo_conflict attempt contract failed: " + "; ".join(conflict_errors)
                    )
            terminal_candidate_source: Path | None = None
            terminal_candidate_archive: Path | None = None
            terminal_candidate_payload: bytes | None = None
            terminal_candidate_digest: str | None = None
            if (
                attempt_record is not None
                and commerce_main_image
                and args.attempt_stage == COMMERCE_MAIN_IMAGE_ATTEMPT_STAGE
                and args.failure_type == "quality"
                and args.status == "failed"
            ):
                terminal_candidate_source = Path(str(proposed.get("output") or "")).resolve()
                terminal_candidate_payload = terminal_candidate_source.read_bytes()
                candidate_digest = str(attempt_record.get("candidate_sha256") or "")
                if hashlib.sha256(terminal_candidate_payload).hexdigest() != candidate_digest:
                    raise ValueError("terminal rejected candidate changed before it could be archived")
                terminal_candidate_digest = candidate_digest
                suffix = terminal_candidate_source.suffix.lower() or ".img"
                terminal_candidate_archive = (
                    work_dir
                    / "rejected"
                    / f"{args.task_id}-attempt-{attempt_number:03d}-{candidate_digest[:12]}{suffix}"
                ).resolve()
                attempt_record["candidate_archive_path"] = str(terminal_candidate_archive)
            state = {"task_id": args.task_id, "worker_id": current.get("worker_id"), "updated_at": timestamp}
            for key in STATE_FIELDS:
                if key in proposed:
                    state[key] = proposed[key]
            if attempt_record:
                state["attempt_record"] = attempt_record
            state_path = state_dir / f"{args.task_id}.json"
            state_snapshot = state_path.read_bytes() if state_path.is_file() else None
            snapshots = {
                state_path: state_snapshot,
                manifest_path: manifest_snapshot,
                report_path: report_snapshot,
            }
            if terminal_candidate_archive is not None:
                snapshots[terminal_candidate_archive] = None
            changed: list[Path] = []
            try:
                if terminal_candidate_archive is not None and terminal_candidate_payload is not None:
                    terminal_candidate_archive.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        with terminal_candidate_archive.open("xb") as archive_handle:
                            archive_handle.write(terminal_candidate_payload)
                            archive_handle.flush()
                            os.fsync(archive_handle.fileno())
                    except FileExistsError as exc:
                        raise ValueError("terminal rejected-candidate archive already exists") from exc
                    changed.append(terminal_candidate_archive)
                atomic_json(state_path, state)
                changed.append(state_path)
                merge_task_states(data, state_dir)
                merged_matches = [
                    entry for entry in data.get("items", []) if entry.get("task_id") == args.task_id
                ]
                if len(merged_matches) != 1:
                    raise ValueError("the just-written task state did not resolve to exactly one manifest item")
                merged_item = merged_matches[0]
                if merged_item.get("updated_at") != timestamp:
                    raise ValueError("the just-written task state was not merged; refusing false success")
                for field in STATE_FIELDS - {"output_validation"}:
                    if field in state and merged_item.get(field) != state.get(field):
                        raise ValueError(
                            f"the just-written task state field {field} was not merged exactly"
                        )
                if attempt_record is not None:
                    matching_records = [
                        record
                        for record in merged_item.get("attempt_history", [])
                        if isinstance(record, dict)
                        and record.get("record_id") == attempt_record.get("record_id")
                    ]
                    if matching_records != [attempt_record]:
                        raise ValueError(
                            "the just-written attempt record was not merged exactly; refusing false success"
                        )

                if args.degrade_to_single:
                    data["workers_active"] = 1
                    data["workers"] = 1
                    data["execution_mode"] = "single"
                    data["degraded_to_single"] = True
                    data["degraded_at"] = timestamp
                data["revision"] = int(data.get("revision", 0) or 0) + 1
                data["updated_at"] = timestamp
                atomic_json(manifest_path, data)
                changed.append(manifest_path)
                write_report(report_path, data)
                changed.append(report_path)
                if terminal_candidate_source is not None:
                    if sha256_file(terminal_candidate_source) != terminal_candidate_digest:
                        raise ValueError("terminal rejected candidate changed before final archival")
                    terminal_candidate_source.unlink()
            except Exception as exc:
                rollback_errors: list[str] = []
                for path in reversed(changed):
                    try:
                        snapshot = snapshots[path]
                        if snapshot is None:
                            path.unlink(missing_ok=True)
                        else:
                            atomic_bytes(path, snapshot)
                    except OSError as rollback_exc:
                        rollback_errors.append(f"{path}: {rollback_exc}")
                if rollback_errors:
                    raise RuntimeError(
                        "manifest update failed and rollback was incomplete: " + "; ".join(rollback_errors)
                    ) from exc
                raise RuntimeError(f"manifest update failed; all file changes were rolled back: {exc}") from exc
    except (OSError, RuntimeError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    print(f"updated={args.task_id} status={args.status} revision={data['revision']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
