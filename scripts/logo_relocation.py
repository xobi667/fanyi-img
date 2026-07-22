#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps, UnidentifiedImageError


NORMALIZED_SIZE = (96, 96)
RELOCATION_THRESHOLDS: dict[str, float | int] = {
    "changed_pixel_delta": 16,
    "source_position_changed_fraction_min": 0.30,
    "source_position_mae_min": 0.06,
    "source_position_score_max": 0.55,
    "destination_score_min": 0.72,
    "destination_mae_max": 0.20,
    "destination_histogram_min": 0.50,
    "destination_aspect_log_delta_max": 0.20,
    "relocation_score_margin_min": 0.20,
    "assignment_margin_min": 0.08,
    "pixel_lock_feather_px": 2,
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_path(path: Path) -> str:
    return str(path.expanduser().resolve()).casefold()


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _pearson(first: Image.Image, second: Image.Image) -> float:
    first_values = list(first.tobytes())
    second_values = list(second.tobytes())
    if len(first_values) != len(second_values) or not first_values:
        return 0.0
    first_mean = sum(first_values) / len(first_values)
    second_mean = sum(second_values) / len(second_values)
    numerator = sum(
        (first_value - first_mean) * (second_value - second_mean)
        for first_value, second_value in zip(first_values, second_values)
    )
    first_energy = sum((value - first_mean) ** 2 for value in first_values)
    second_energy = sum((value - second_mean) ** 2 for value in second_values)
    denominator = math.sqrt(first_energy * second_energy)
    if denominator <= 1e-12:
        return 1.0 if abs(first_mean - second_mean) <= 8 else 0.0
    return _clamp(numerator / denominator, -1.0, 1.0)


def _edge_image(image: Image.Image) -> Image.Image:
    edges = image.filter(ImageFilter.GaussianBlur(1.0)).filter(ImageFilter.FIND_EDGES)
    if edges.width > 4 and edges.height > 4:
        return edges.crop((2, 2, edges.width - 2, edges.height - 2))
    return edges


def _normalize_crop(image: Image.Image, bbox: tuple[int, int, int, int]) -> Image.Image:
    return image.crop(bbox).convert("RGB").resize(NORMALIZED_SIZE, Image.Resampling.LANCZOS)


def _border_background(image: Image.Image, thickness: int = 4) -> tuple[int, int, int]:
    width, height = image.size
    pixels = image.convert("RGB").load()
    values: list[list[int]] = [[], [], []]
    for y in range(height):
        for x in range(width):
            if x >= thickness and x < width - thickness and y >= thickness and y < height - thickness:
                continue
            red, green, blue = pixels[x, y]
            values[0].append(red)
            values[1].append(green)
            values[2].append(blue)
    medians: list[int] = []
    for channel in values:
        channel.sort()
        medians.append(channel[len(channel) // 2] if channel else 0)
    return medians[0], medians[1], medians[2]


def _foreground_mask(reference: Image.Image) -> tuple[Image.Image, dict[str, Any]]:
    background = _border_background(reference)
    grayscale = ImageOps.grayscale(reference)
    edges = grayscale.filter(ImageFilter.GaussianBlur(1.0)).filter(ImageFilter.FIND_EDGES)
    reference_bytes = reference.tobytes()
    edge_bytes = edges.tobytes()
    mask_bytes = bytearray(reference.width * reference.height)
    for index in range(reference.width * reference.height):
        offset = index * 3
        color_distance = max(
            abs(reference_bytes[offset] - background[0]),
            abs(reference_bytes[offset + 1] - background[1]),
            abs(reference_bytes[offset + 2] - background[2]),
        )
        if color_distance > 24 or edge_bytes[index] > 18:
            mask_bytes[index] = 255
    mask = Image.frombytes("L", reference.size, bytes(mask_bytes)).filter(ImageFilter.MaxFilter(5))
    active = sum(1 for value in mask.tobytes() if value > 0)
    fraction = active / max(1, reference.width * reference.height)
    mode = "foreground"
    if fraction < 0.02:
        mask = Image.new("L", reference.size, 255)
        fraction = 1.0
        mode = "full_fallback"
    return mask, {
        "mode": mode,
        "fraction": round(fraction, 8),
        "background_rgb": list(background),
    }


def _histogram_intersection(
    first: Image.Image,
    second: Image.Image,
    mask: Image.Image,
) -> float:
    total = 0.0
    for first_channel, second_channel in zip(first.split(), second.split()):
        first_histogram = first_channel.histogram(mask)
        second_histogram = second_channel.histogram(mask)
        first_bins = [sum(first_histogram[index:index + 16]) for index in range(0, 256, 16)]
        second_bins = [sum(second_histogram[index:index + 16]) for index in range(0, 256, 16)]
        denominator = max(1, sum(first_bins))
        total += sum(
            min(first_value, second_value)
            for first_value, second_value in zip(first_bins, second_bins)
        ) / denominator
    return total / 3


def _masked_difference(
    first: Image.Image,
    second: Image.Image,
    mask: Image.Image,
) -> tuple[float, float, float]:
    first_bytes = first.tobytes()
    second_bytes = second.tobytes()
    mask_bytes = mask.tobytes()
    absolute = 0
    changed = 0
    full_changed = 0
    active = 0
    delta = int(RELOCATION_THRESHOLDS["changed_pixel_delta"])
    pixels = first.width * first.height
    for index in range(pixels):
        offset = index * 3
        differences = (
            abs(first_bytes[offset] - second_bytes[offset]),
            abs(first_bytes[offset + 1] - second_bytes[offset + 1]),
            abs(first_bytes[offset + 2] - second_bytes[offset + 2]),
        )
        pixel_changed = max(differences) > delta
        if pixel_changed:
            full_changed += 1
        if mask_bytes[index] <= 0:
            continue
        active += 1
        absolute += sum(differences)
        if pixel_changed:
            changed += 1
    normalized_mae = absolute / max(1, active * 3 * 255)
    return normalized_mae, changed / max(1, active), full_changed / max(1, pixels)


def _comparison_metrics(
    reference: Image.Image,
    candidate: Image.Image,
    mask: Image.Image,
    reference_size: tuple[int, int],
    candidate_size: tuple[int, int],
) -> dict[str, float]:
    reference_luma = ImageOps.autocontrast(ImageOps.grayscale(reference))
    candidate_luma = ImageOps.autocontrast(ImageOps.grayscale(candidate))
    luma_similarity = (_pearson(reference_luma, candidate_luma) + 1.0) / 2.0
    edge_similarity = (
        _pearson(_edge_image(reference_luma), _edge_image(candidate_luma)) + 1.0
    ) / 2.0
    histogram = _histogram_intersection(reference, candidate, mask)
    normalized_mae, changed_fraction, full_changed_fraction = _masked_difference(
        reference,
        candidate,
        mask,
    )
    mae_similarity = max(0.0, 1.0 - normalized_mae / 0.35)
    score = (
        0.30 * luma_similarity
        + 0.30 * edge_similarity
        + 0.25 * histogram
        + 0.15 * mae_similarity
    )
    reference_aspect = reference_size[0] / max(1, reference_size[1])
    candidate_aspect = candidate_size[0] / max(1, candidate_size[1])
    aspect_delta = abs(math.log(max(1e-12, candidate_aspect / reference_aspect)))
    return {
        "score": round(score, 8),
        "luma_similarity": round(luma_similarity, 8),
        "edge_similarity": round(edge_similarity, 8),
        "histogram_intersection": round(histogram, 8),
        "normalized_mae": round(normalized_mae, 8),
        "changed_fraction": round(changed_fraction, 8),
        "full_changed_fraction": round(full_changed_fraction, 8),
        "aspect_log_delta": round(aspect_delta, 8),
    }


def _bbox(
    value: Any,
    size: tuple[int, int],
    label: str,
    errors: list[str],
) -> tuple[int, int, int, int] | None:
    if not (
        isinstance(value, (list, tuple))
        and len(value) == 4
        and all(
            isinstance(component, (int, float))
            and not isinstance(component, bool)
            and math.isfinite(component)
            for component in value
        )
    ):
        errors.append(f"{label} must be a finite four-number bbox")
        return None
    left, top, right, bottom = (int(round(float(component))) for component in value)
    if not (0 <= left < right <= size[0] and 0 <= top < bottom <= size[1]):
        errors.append(f"{label} is outside its image canvas")
        return None
    return left, top, right, bottom


def _expanded_bbox(
    bbox: tuple[int, int, int, int],
    size: tuple[int, int],
    padding: int,
) -> tuple[int, int, int, int]:
    return (
        max(0, bbox[0] - padding),
        max(0, bbox[1] - padding),
        min(size[0], bbox[2] + padding),
        min(size[1], bbox[3] + padding),
    )


def _outside_relocation_pixel_lock(
    source: Image.Image,
    prepared: Image.Image,
    regions: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    """Require exact RGBA identity outside declared relocation ROIs and a tiny feather."""
    feather = int(RELOCATION_THRESHOLDS["pixel_lock_feather_px"])
    record: dict[str, Any] = {
        "schema_version": 1,
        "contract": "logo-relocation-outside-rgba-lock-v1",
        "mapping": "identity",
        "feather_px": feather,
        "source_size": list(source.size),
        "prepared_size": list(prepared.size),
        "allowed_regions": [],
        "allowed_pixels": 0,
        "changed_pixels": 0,
        "changed_pixels_outside_allowed": 0,
        "passed": False,
    }
    if source.size != prepared.size:
        error = (
            "logo relocation cannot prove unchanged non-module pixels across different canvas sizes "
            "without a recomputable whole-canvas mapping"
        )
        record["mapping"] = None
        record["errors"] = [error]
        return record, [error]

    allowed_mask = Image.new("L", source.size, 0)
    draw = ImageDraw.Draw(allowed_mask)
    normalized_regions: list[dict[str, Any]] = []
    for region in regions:
        raw_bbox = region.get("bbox")
        if not (
            isinstance(raw_bbox, tuple)
            and len(raw_bbox) == 4
            and all(isinstance(value, int) for value in raw_bbox)
        ):
            continue
        expanded = _expanded_bbox(raw_bbox, source.size, feather)
        draw.rectangle((expanded[0], expanded[1], expanded[2] - 1, expanded[3] - 1), fill=255)
        normalized_regions.append({
            "module_id": str(region.get("module_id") or ""),
            "role": str(region.get("role") or ""),
            "bbox": list(raw_bbox),
            "allowed_bbox": list(expanded),
        })

    source_rgba = source.convert("RGBA")
    prepared_rgba = prepared.convert("RGBA")
    difference = ImageChops.difference(source_rgba, prepared_rgba)
    changed = Image.new("L", source.size, 0)
    for channel in difference.split():
        changed = ImageChops.lighter(
            changed,
            channel.point(lambda value: 255 if value else 0),
        )
    changed_pixels = changed.histogram()[255]
    outside_allowed = ImageChops.multiply(changed, ImageOps.invert(allowed_mask))
    changed_outside = outside_allowed.histogram()[255]
    allowed_pixels = allowed_mask.histogram()[255]
    errors: list[str] = []
    if changed_outside:
        errors.append(
            "prepared_base changed "
            f"{changed_outside} RGBA pixel(s) outside declared relocation ROIs"
        )
    record.update({
        "allowed_regions": normalized_regions,
        "allowed_pixels": allowed_pixels,
        "changed_pixels": changed_pixels,
        "changed_pixels_outside_allowed": changed_outside,
        "passed": not errors,
        "errors": errors,
    })
    return record, errors


def _select_plan_item(
    item: dict[str, Any],
    plan: dict[str, Any],
    errors: list[str],
) -> dict[str, Any] | None:
    plan_items = plan.get("items")
    if plan_items is None:
        return plan
    if not isinstance(plan_items, list):
        errors.append("logo relocation plan items must be a list")
        return None
    task_id = str(item.get("task_id") or "")
    matches = [
        entry
        for entry in plan_items
        if isinstance(entry, dict) and str(entry.get("task_id") or "") == task_id
    ]
    if len(matches) != 1:
        errors.append("logo relocation plan must contain exactly one matching task item")
        return None
    return matches[0]


def validate_logo_relocation(
    item: dict[str, Any],
    plan: dict[str, Any],
    source: str | Path,
    prepared: str | Path,
    geometry: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Prove that every conflicted visual module moved from its source ROI to its declared anchor."""
    source_path = Path(source).expanduser().resolve()
    prepared_path = Path(prepared).expanduser().resolve()
    errors: list[str] = []
    if not isinstance(item, dict) or not isinstance(plan, dict) or not isinstance(geometry, dict):
        record = {
            "schema_version": 1,
            "producer": "xobi-img.logo-relocation",
            "contract": "logo-relocation-v1",
            "source": str(source_path),
            "prepared_base": str(prepared_path),
            "thresholds": dict(RELOCATION_THRESHOLDS),
            "modules": [],
            "assignment": {"score_matrix": {}, "errors": [], "passed": False},
            "passed": False,
            "errors": ["item, plan, and geometry must be objects"],
        }
        return record, list(record["errors"])
    record: dict[str, Any] = {
        "schema_version": 1,
        "producer": "xobi-img.logo-relocation",
        "contract": "logo-relocation-v1",
        "task_id": str(item.get("task_id") or ""),
        "source": str(source_path),
        "prepared_base": str(prepared_path),
        "thresholds": dict(RELOCATION_THRESHOLDS),
        "modules": [],
        "assignment": {"score_matrix": {}, "errors": [], "passed": False},
        "passed": False,
    }
    plan_item = _select_plan_item(item, plan, errors)
    if plan_item is None:
        record["errors"] = errors
        return record, errors
    decision = str(item.get("logo_decision") or plan_item.get("decision") or "")
    record["decision"] = decision
    if decision != "regenerate_for_conflict":
        record["applicable"] = False
        record["assignment"]["passed"] = True
        record["passed"] = True
        record["errors"] = []
        return record, []
    record["applicable"] = True

    try:
        with Image.open(source_path) as raw_source:
            source_rgba = ImageOps.exif_transpose(raw_source).convert("RGBA")
            source_rgba.load()
            source_image = source_rgba.convert("RGB")
        with Image.open(prepared_path) as raw_prepared:
            prepared_rgba = ImageOps.exif_transpose(raw_prepared).convert("RGBA")
            prepared_rgba.load()
            prepared_image = prepared_rgba.convert("RGB")
        record["source_sha256"] = sha256_file(source_path)
        record["prepared_sha256"] = sha256_file(prepared_path)
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        errors.append(f"logo relocation could not read source/prepared images: {exc}")
        record["errors"] = errors
        return record, errors

    source_size = source_image.size
    prepared_size = prepared_image.size
    record["source_size"] = list(source_size)
    record["prepared_size"] = list(prepared_size)
    canvas = geometry.get("canvas")
    if canvas != list(prepared_size):
        errors.append("logo relocation geometry canvas does not match prepared_base")

    explicit_reference = item.get("conflict_reference_base")
    if explicit_reference:
        if _canonical_path(Path(str(explicit_reference))) != _canonical_path(source_path):
            errors.append("logo relocation source does not match conflict_reference_base")
    else:
        for label, value in (("item source", item.get("source")), ("plan source", plan_item.get("source"))):
            if value and _canonical_path(Path(str(value))) != _canonical_path(source_path):
                errors.append(f"logo relocation {label} does not match the supplied source")
        if item.get("source_sha256") and item.get("source_sha256") != record.get("source_sha256"):
            errors.append("logo relocation source hash does not match the manifest item")
    if item.get("prepared_base") and _canonical_path(Path(str(item["prepared_base"]))) != _canonical_path(
        prepared_path
    ):
        errors.append("logo relocation prepared_base does not match the manifest item")

    conflicts = plan_item.get("conflicts")
    modules = plan_item.get("modules")
    plan_anchors = plan_item.get("module_anchors")
    item_anchors = item.get("module_anchors")
    if not (
        isinstance(conflicts, list)
        and conflicts
        and all(isinstance(value, str) and value for value in conflicts)
        and len(conflicts) == len(set(conflicts))
    ):
        errors.append("logo relocation conflicts must be a non-empty unique module-ID list")
        conflicts = []
    if not isinstance(modules, list):
        errors.append("logo relocation modules must be a list")
        modules = []
    if not isinstance(plan_anchors, list):
        errors.append("logo relocation module_anchors must be a list")
        plan_anchors = []
    if item_anchors != plan_anchors:
        errors.append("logo relocation item anchors must exactly match the plan anchors")

    module_by_id = {
        str(module.get("id")): module
        for module in modules
        if isinstance(module, dict) and str(module.get("id") or "")
    }
    anchor_by_id = {
        str(anchor.get("module_id")): anchor
        for anchor in plan_anchors
        if isinstance(anchor, dict) and str(anchor.get("module_id") or "")
    }
    if len(module_by_id) != len([
        module for module in modules if isinstance(module, dict) and str(module.get("id") or "")
    ]):
        errors.append("logo relocation module IDs must be unique")
    if len(anchor_by_id) != len([
        anchor for anchor in plan_anchors if isinstance(anchor, dict) and str(anchor.get("module_id") or "")
    ]):
        errors.append("logo relocation anchor module IDs must be unique")
    if set(conflicts) != set(anchor_by_id):
        errors.append("logo relocation requires exactly one anchor for every conflict")

    work: dict[str, dict[str, Any]] = {}
    for module_id in conflicts:
        module_errors: list[str] = []
        module = module_by_id.get(module_id)
        anchor = anchor_by_id.get(module_id)
        module_record: dict[str, Any] = {
            "module_id": module_id,
            "errors": module_errors,
            "passed": False,
        }
        record["modules"].append(module_record)
        if module is None:
            module_errors.append("conflict module is missing from the plan")
            continue
        if anchor is None:
            module_errors.append("conflict module is missing its prepared anchor")
            continue
        source_bbox = _bbox(
            module.get("source_bbox", module.get("bbox")),
            source_size,
            f"module {module_id} source_bbox",
            module_errors,
        )
        prepared_bbox = _bbox(
            anchor.get("prepared_bbox"),
            prepared_size,
            f"module {module_id} prepared_bbox",
            module_errors,
        )
        original_prepared_value = module.get("prepared_source_bbox")
        if original_prepared_value is None:
            if source_size != prepared_size:
                module_errors.append(
                    "source and prepared canvases differ; module requires explicit prepared_source_bbox"
                )
                old_bbox = None
            else:
                old_bbox = source_bbox
        else:
            old_bbox = _bbox(
                original_prepared_value,
                prepared_size,
                f"module {module_id} prepared_source_bbox",
                module_errors,
            )
        if source_bbox is not None:
            module_record["source_bbox"] = list(source_bbox)
        if old_bbox is not None:
            module_record["prepared_source_bbox"] = list(old_bbox)
        if prepared_bbox is not None:
            module_record["prepared_bbox"] = list(prepared_bbox)
        if source_bbox is None or old_bbox is None or prepared_bbox is None:
            continue

        reference = _normalize_crop(source_image, source_bbox)
        mask, mask_record = _foreground_mask(reference)
        old_candidate = _normalize_crop(prepared_image, old_bbox)
        old_metrics = _comparison_metrics(
            reference,
            old_candidate,
            mask,
            (source_bbox[2] - source_bbox[0], source_bbox[3] - source_bbox[1]),
            (old_bbox[2] - old_bbox[0], old_bbox[3] - old_bbox[1]),
        )
        module_record["mask"] = mask_record
        module_record["source_position"] = old_metrics
        work[module_id] = {
            "record": module_record,
            "errors": module_errors,
            "reference": reference,
            "mask": mask,
            "reference_size": (source_bbox[2] - source_bbox[0], source_bbox[3] - source_bbox[1]),
            "source_bbox": source_bbox,
            "prepared_bbox": prepared_bbox,
            "old_metrics": old_metrics,
        }

    allowed_regions: list[dict[str, Any]] = []
    for module_id, module_work in work.items():
        allowed_regions.extend((
            {
                "module_id": module_id,
                "role": "source",
                "bbox": module_work["source_bbox"],
            },
            {
                "module_id": module_id,
                "role": "destination",
                "bbox": module_work["prepared_bbox"],
            },
        ))
    pixel_lock_record, pixel_lock_errors = _outside_relocation_pixel_lock(
        source_rgba,
        prepared_rgba,
        allowed_regions,
    )
    record["outside_relocation_pixel_lock"] = pixel_lock_record
    errors.extend(pixel_lock_errors)

    score_matrix: dict[str, dict[str, float]] = {}
    destination_metrics: dict[str, dict[str, dict[str, float]]] = {}
    for source_id, source_work in work.items():
        score_matrix[source_id] = {}
        destination_metrics[source_id] = {}
        for destination_id, destination_work in work.items():
            destination_bbox = destination_work["prepared_bbox"]
            destination = _normalize_crop(prepared_image, destination_bbox)
            metrics = _comparison_metrics(
                source_work["reference"],
                destination,
                source_work["mask"],
                source_work["reference_size"],
                (
                    destination_bbox[2] - destination_bbox[0],
                    destination_bbox[3] - destination_bbox[1],
                ),
            )
            destination_metrics[source_id][destination_id] = metrics
            score_matrix[source_id][destination_id] = metrics["score"]
    record["assignment"]["score_matrix"] = score_matrix

    for module_id, module_work in work.items():
        module_record = module_work["record"]
        module_errors = module_work["errors"]
        old_metrics = module_work["old_metrics"]
        new_metrics = destination_metrics[module_id][module_id]
        module_record["destination"] = new_metrics
        module_record["relocation_score_margin"] = round(
            new_metrics["score"] - old_metrics["score"],
            8,
        )
        if old_metrics["changed_fraction"] < float(
            RELOCATION_THRESHOLDS["source_position_changed_fraction_min"]
        ):
            module_errors.append("source module ROI was not substantially cleared")
        if old_metrics["normalized_mae"] < float(RELOCATION_THRESHOLDS["source_position_mae_min"]):
            module_errors.append("source module ROI changed too little")
        if old_metrics["score"] > float(RELOCATION_THRESHOLDS["source_position_score_max"]):
            module_errors.append("source module is still visually present at its original position")
        if new_metrics["score"] < float(RELOCATION_THRESHOLDS["destination_score_min"]):
            module_errors.append("declared prepared_bbox does not contain the corresponding module")
        if new_metrics["normalized_mae"] > float(RELOCATION_THRESHOLDS["destination_mae_max"]):
            module_errors.append("destination module differs too much from the source module")
        if new_metrics["histogram_intersection"] < float(
            RELOCATION_THRESHOLDS["destination_histogram_min"]
        ):
            module_errors.append("destination module color/content fingerprint does not match")
        if new_metrics["aspect_log_delta"] > float(
            RELOCATION_THRESHOLDS["destination_aspect_log_delta_max"]
        ):
            module_errors.append("destination module aspect ratio drift is too large")
        if module_record["relocation_score_margin"] < float(
            RELOCATION_THRESHOLDS["relocation_score_margin_min"]
        ):
            module_errors.append("destination is not a stronger match than the original position")

    assignment_errors: list[str] = []
    valid_ids = list(work)
    if len(valid_ids) > 1:
        assignment_margin = float(RELOCATION_THRESHOLDS["assignment_margin_min"])
        for destination_id in valid_ids:
            ranked_sources = sorted(
                (
                    (score_matrix[source_id][destination_id], source_id)
                    for source_id in valid_ids
                ),
                reverse=True,
            )
            best_score, best_source = ranked_sources[0]
            runner_up = ranked_sources[1][0]
            if best_source != destination_id:
                message = (
                    f"destination {destination_id} best matches {best_source}, not its declared module"
                )
                assignment_errors.append(message)
                work[destination_id]["errors"].append(message)
            elif best_score - runner_up < assignment_margin:
                message = f"destination {destination_id} module assignment is visually ambiguous"
                assignment_errors.append(message)
                work[destination_id]["errors"].append(message)
        for source_id in valid_ids:
            ranked_destinations = sorted(
                (
                    (score_matrix[source_id][destination_id], destination_id)
                    for destination_id in valid_ids
                ),
                reverse=True,
            )
            best_score, best_destination = ranked_destinations[0]
            runner_up = ranked_destinations[1][0]
            if best_destination != source_id:
                message = (
                    f"source {source_id} best matches destination {best_destination}, not its declared anchor"
                )
                assignment_errors.append(message)
                work[source_id]["errors"].append(message)
            elif best_score - runner_up < assignment_margin:
                message = f"source {source_id} destination assignment is visually ambiguous"
                assignment_errors.append(message)
                work[source_id]["errors"].append(message)

    for module_record in record["modules"]:
        module_record["passed"] = not module_record["errors"]
        errors.extend(
            f"module {module_record['module_id']}: {message}"
            for message in module_record["errors"]
        )
    errors.extend(f"assignment: {message}" for message in assignment_errors)
    record["assignment"]["errors"] = assignment_errors
    record["assignment"]["passed"] = not assignment_errors and bool(valid_ids)
    record["passed"] = not errors
    record["errors"] = errors
    return record, errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate deterministic evidence that Logo-conflict modules moved.")
    parser.add_argument("--item-json", required=True, type=Path)
    parser.add_argument("--plan-json", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--prepared", required=True, type=Path)
    parser.add_argument("--geometry-json", required=True, type=Path)
    args = parser.parse_args()
    try:
        item = json.loads(args.item_json.read_text(encoding="utf-8"))
        plan = json.loads(args.plan_json.read_text(encoding="utf-8"))
        geometry = json.loads(args.geometry_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    record, errors = validate_logo_relocation(
        item,
        plan,
        args.source,
        args.prepared,
        geometry,
    )
    print(json.dumps({"record": record, "errors": errors}, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
