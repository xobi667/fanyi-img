#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import uuid
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from manifest_utils import (
    _localization_text_edit_mask,
    atomic_bytes,
    atomic_json,
    canonical_path_key,
    localization_non_text_pixel_lock,
    now_iso,
    sha256_file,
)


def load_plan(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read localization plan: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("localization plan must be a JSON object")
    return value


def checked_output_path(path: Path, label: str, overwrite: bool) -> Path:
    absolute = path.expanduser().absolute()
    if absolute.exists() and (absolute.is_symlink() or not absolute.is_file()):
        raise ValueError(f"{label} must be a regular file path")
    if absolute.exists() and not overwrite:
        raise ValueError(f"{label} exists; use --overwrite")
    absolute.parent.mkdir(parents=True, exist_ok=True)
    return absolute


def compose_localization(
    source: Path,
    candidate: Path,
    output: Path,
    plan_path: Path,
    provenance_path: Path,
    overwrite: bool = False,
) -> dict[str, Any]:
    source = source.expanduser().resolve()
    candidate = candidate.expanduser().resolve()
    plan_path = plan_path.expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"source image not found: {source}")
    if not candidate.is_file():
        raise ValueError(f"raw edit candidate not found: {candidate}")
    if not plan_path.is_file():
        raise ValueError(f"localization plan not found: {plan_path}")

    output = checked_output_path(output, "output", overwrite)
    provenance_path = checked_output_path(provenance_path, "provenance", overwrite)
    if output.suffix.lower() != ".png":
        raise ValueError("localized_base output must be a lossless .png")
    resolved_output = output.resolve()
    resolved_provenance = provenance_path.resolve()
    protected = (source, candidate, plan_path)
    if any(canonical_path_key(resolved_output) == canonical_path_key(path) for path in protected):
        raise ValueError("output must differ from source, candidate, and localization plan")
    if any(canonical_path_key(resolved_provenance) == canonical_path_key(path) for path in protected):
        raise ValueError("provenance must differ from source, candidate, and localization plan")
    if canonical_path_key(resolved_output) == canonical_path_key(resolved_provenance):
        raise ValueError("output and provenance must use different paths")

    try:
        output_snapshot = output.read_bytes() if output.is_file() else None
        provenance_snapshot = provenance_path.read_bytes() if provenance_path.is_file() else None
    except OSError as exc:
        raise ValueError(f"could not snapshot existing composition outputs: {exc}") from exc

    plan = load_plan(plan_path)
    if plan.get("mode") != "text_only_reference_edit":
        raise ValueError("deterministic composition requires mode=text_only_reference_edit")
    ratio_adaptation = plan.get("ratio_adaptation")
    if not isinstance(ratio_adaptation, dict) or ratio_adaptation.get("required") is not False:
        raise ValueError(
            "ratio adaptation cannot be deterministically composed without a structured coordinate mapping"
        )
    if canonical_path_key(Path(str(plan.get("source") or ""))) != canonical_path_key(source):
        raise ValueError("localization plan source does not match --source")
    if plan.get("source_sha256") != sha256_file(source):
        raise ValueError("localization plan source hash does not match --source")

    try:
        with Image.open(source) as raw_source:
            icc_value = raw_source.info.get("icc_profile")
            icc_profile = bytes(icc_value) if isinstance(icc_value, (bytes, bytearray)) else None
            source_image = ImageOps.exif_transpose(raw_source).convert("RGBA")
            source_image.load()
        with Image.open(candidate) as raw_candidate:
            candidate_image = ImageOps.exif_transpose(raw_candidate).convert("RGBA")
            candidate_image.load()
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise ValueError(f"could not read source or raw edit candidate: {exc}") from exc

    if plan.get("source_size") != [source_image.width, source_image.height]:
        raise ValueError("localization plan source_size does not match --source")
    if candidate_image.width * source_image.height != candidate_image.height * source_image.width:
        raise ValueError("raw edit candidate must have the same aspect ratio as the source")

    raw_candidate_size = [candidate_image.width, candidate_image.height]
    mask, mask_metrics, mask_errors = _localization_text_edit_mask(
        source_image.size,
        plan.get("text_blocks"),
        plan.get("non_text_inventory"),
    )
    if mask_errors:
        raise ValueError("invalid localization text masks: " + "; ".join(mask_errors))
    candidate_resampled = candidate_image.size != source_image.size
    if candidate_resampled:
        candidate_image = candidate_image.resize(source_image.size, Image.Resampling.LANCZOS)
    localized = Image.composite(candidate_image, source_image, mask)

    temporary = output.with_name(
        f".{output.stem}.tmp-{os.getpid()}-{uuid.uuid4().hex}.png"
    )
    try:
        save_options: dict[str, Any] = {"compress_level": 9, "optimize": False}
        if icc_profile is not None:
            save_options["icc_profile"] = icc_profile
        localized.save(temporary, format="PNG", **save_options)
        _, pixel_errors = localization_non_text_pixel_lock(source, temporary, plan)
        if pixel_errors:
            raise ValueError("deterministic composition failed its pixel lock: " + "; ".join(pixel_errors))
        os.replace(temporary, output)
    except (OSError, ValueError) as exc:
        raise ValueError(f"could not write localized_base: {exc}") from exc
    finally:
        temporary.unlink(missing_ok=True)

    try:
        provenance = {
            "schema_version": 1,
            "producer": "xobi-img.compose_localization",
            "contract": "text-bbox-composite-v1",
            "created_at": now_iso(),
            "source": str(source),
            "source_sha256": sha256_file(source),
            "raw_edit_candidate": str(candidate),
            "raw_edit_candidate_sha256": sha256_file(candidate),
            "localization_plan": str(plan_path),
            "localization_plan_sha256": sha256_file(plan_path),
            "output": str(output),
            "output_sha256": sha256_file(output),
            "source_size": [source_image.width, source_image.height],
            "raw_candidate_size": raw_candidate_size,
            "candidate_resampled_to_source": candidate_resampled,
            "mask": mask_metrics,
        }
        atomic_json(provenance_path, provenance)
    except (OSError, TypeError, ValueError) as exc:
        rollback_errors: list[str] = []
        for path, snapshot in (
            (provenance_path, provenance_snapshot),
            (output, output_snapshot),
        ):
            try:
                if snapshot is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_bytes(path, snapshot)
            except OSError as rollback_exc:
                rollback_errors.append(f"{path}: {rollback_exc}")
        if rollback_errors:
            raise ValueError(
                "could not write composition provenance and rollback was incomplete: "
                + "; ".join(rollback_errors)
            ) from exc
        raise ValueError(
            f"could not write composition provenance; localized_base was rolled back: {exc}"
        ) from exc
    return provenance


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create a lossless localized_base by copying only planned text boxes from a raw edit "
            "candidate onto the immutable source."
        )
    )
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--provenance-json", required=True, type=Path)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    try:
        record = compose_localization(
            args.source,
            args.candidate,
            args.output,
            args.plan,
            args.provenance_json,
            args.overwrite,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(
        f"written={record['output']} provenance={args.provenance_json.expanduser().absolute()} "
        f"sha256={record['output_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
