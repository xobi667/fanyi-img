#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import time
import unicodedata
import uuid
from collections.abc import Iterable
from contextlib import AbstractContextManager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps, ImageStat, UnidentifiedImageError


SCHEMA_VERSION = 4
TERMINAL_STATUSES = {"success", "skipped", "failed"}
LEGACY_SOURCE_SCHEMA_VERSIONS = {1, 2, 3}
PURE_GENERATION_LOCALIZATION_MODE = "pure_generation_localization"
LEGACY_REFERENCE_LOCALIZATION_MODE = "text_only_reference_edit"
CURRENT_IMAGE_MODEL_POLICY = {
    "default": "pure_generation",
    "reference_images_allowed": False,
    "logo_exception": ["deterministic_overlay", "conflict_relocation"],
}
LEGACY_IMAGE_MODEL_POLICY = {
    "default": "legacy",
    "reference_images_allowed": True,
    "logo_exception": ["deterministic_overlay", "conflict_relocation"],
}
PURE_GENERATION_RATIO_ALLOWED_CHANGES = frozenset({
    "minimal_canvas_adaptation",
    "proportional_subject_scaling",
    "necessary_text_reflow",
})


def is_legacy_read_only_manifest(data: dict[str, Any]) -> bool:
    compatibility = data.get("manifest_compatibility")
    if not isinstance(compatibility, dict):
        return False
    source_version = compatibility.get("source_schema_version")
    expected = {
        "source_schema_version": source_version,
        "image_execution": "read_only",
        "migration_required_for_new_attempts": True,
    }
    return source_version in LEGACY_SOURCE_SCHEMA_VERSIONS and compatibility == expected
FORMAT_BY_SUFFIX = {
    ".png": "PNG",
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".webp": "WEBP",
    ".bmp": "BMP",
    ".tif": "TIFF",
    ".tiff": "TIFF",
}
LOGO_REFERENCE_SHORT_SIDE = 4000
LOGO_REFERENCE_BOX = (1036, 309)
LOGO_ALPHA_THRESHOLD = 10
LOGO_SAFE_PADDING = 80
LOGO_ANCHOR_TOLERANCE = 48
LOSSLESS_PIXEL_FORMATS = {"PNG", "BMP", "TIFF"}
LOSSY_PIXEL_FORMATS = {"JPEG", "WEBP"}
LOCALIZATION_GUARD_THRESHOLDS = {
    "block_luma_correlation_min": 0.55,
    "low_frequency_mae_max": 0.085,
    "low_frequency_rms_max": 0.115,
    "edge_rms_max": 0.13,
}
LOCALIZATION_TEXT_ONLY_LAYOUT_THRESHOLDS = {
    **LOCALIZATION_GUARD_THRESHOLDS,
    "block_luma_correlation_min": 0.70,
}
LOCALIZATION_TEXT_BOX_FRACTION_MAX = 0.20
LOCALIZATION_TEXT_MASK_FRACTION_MAX = 0.60
LOCALIZATION_TEXT_MEANINGFUL_DELTA = 8
LOCALIZATION_TEXT_SIGNIFICANT_DELTA = 20
LOCALIZATION_TEXT_MEANINGFUL_FRACTION_MAX = 0.85
LOCALIZATION_TEXT_SIGNIFICANT_FRACTION_MAX = 0.85
LOCALIZATION_TEXT_REQUIRED_CHANGE_FRACTION_MIN = 0.001
_LOCALIZATION_INVENTORY_UNSET = object()


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="microseconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, data: dict[str, Any]) -> None:
    atomic_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def atomic_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    try:
        with temp.open("wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


class FileLock(AbstractContextManager["FileLock"]):
    def __init__(self, path: Path, timeout: float = 30.0, stale_after: float = 180.0) -> None:
        self.path = path
        self.timeout = timeout
        self.stale_after = stale_after
        self.acquired = False
        self.handle: Any = None

    def _try_lock(self) -> None:
        assert self.handle is not None
        self.handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(self) -> None:
        assert self.handle is not None
        self.handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)

    def __enter__(self) -> "FileLock":
        deadline = time.monotonic() + self.timeout
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            self.handle.write(b"\0")
            self.handle.flush()
            os.fsync(self.handle.fileno())
        while True:
            try:
                self._try_lock()
                self.acquired = True
                return self
            except (OSError, BlockingIOError):
                if time.monotonic() >= deadline:
                    self.handle.close()
                    self.handle = None
                    raise TimeoutError(f"timed out waiting for manifest lock: {self.path}")
                time.sleep(0.05)

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self.acquired and self.handle is not None:
            try:
                self._unlock()
            finally:
                self.acquired = False
                self.handle.close()
                self.handle = None


def expected_geometry(ratio: str, source_width: int, source_height: int) -> tuple[list[int] | None, list[int] | None]:
    value = ratio.strip().lower().replace(" ", "").replace("：", ":")
    dimensions = re.fullmatch(r"(\d+)[x×*](\d+)", value)
    if dimensions:
        width, height = int(dimensions.group(1)), int(dimensions.group(2))
        if width < 1 or height < 1:
            raise ValueError("output dimensions must be positive")
        return [width, height], [width, height]
    proportion = re.fullmatch(r"(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)", value)
    if proportion:
        width_ratio = float(proportion.group(1))
        height_ratio = float(proportion.group(2))
        if width_ratio <= 0 or height_ratio <= 0:
            raise ValueError("output ratio components must be positive")
        return None, [max(1, round(width_ratio * 1_000_000)), max(1, round(height_ratio * 1_000_000))]
    if value in {"original", "keep-original", "保持原比例", "原比例"}:
        if source_width < 1 or source_height < 1:
            raise ValueError("source dimensions must be positive when keeping the original ratio")
        return None, [source_width, source_height]
    raise ValueError(f"unsupported output ratio or dimensions: {ratio}")


def upgrade_manifest(data: dict[str, Any]) -> dict[str, Any]:
    """Read legacy manifests safely without granting them new image attempts."""
    version = int(data.get("schema_version", 2))
    if version not in {*LEGACY_SOURCE_SCHEMA_VERSIONS, SCHEMA_VERSION}:
        raise ValueError(f"unsupported manifest schema: {version}")
    if version in LEGACY_SOURCE_SCHEMA_VERSIONS:
        data["manifest_compatibility"] = {
            "source_schema_version": version,
            "image_execution": "read_only",
            "migration_required_for_new_attempts": True,
        }
    elif data.get("manifest_compatibility") is not None:
        raise ValueError("current schema manifests cannot self-declare legacy compatibility")
    data["schema_version"] = SCHEMA_VERSION
    data.setdefault("manifest_id", None)
    data.setdefault("revision", 0)
    data.setdefault("unsupported_inputs", [])
    data.setdefault("excluded_inputs", [])
    data.setdefault("input_roles", [])
    retry_policy = data.setdefault("retry_policy", {
        "quality_attempts": 3,
        "infrastructure_retries": 3,
        "max_infrastructure_attempts": 4,
        "infrastructure_backoff_seconds": [2, 5, 10],
        "parallel_failure_probe_threshold": 2,
    })
    if "infrastructure_attempts" in retry_policy and "infrastructure_retries" not in retry_policy:
        retry_policy["infrastructure_retries"] = int(retry_policy.pop("infrastructure_attempts"))
    retry_policy.setdefault("max_infrastructure_attempts", int(retry_policy.get("infrastructure_retries", 3)) + 1)
    workers = int(data.get("workers", data.get("workers_active", 1)) or 1)
    data.setdefault("workers_requested", workers)
    data.setdefault("workers_active", workers)
    data.setdefault("execution_mode", "parallel" if workers > 1 else "single")
    data.setdefault("degraded_to_single", False)
    if version in LEGACY_SOURCE_SCHEMA_VERSIONS and "image_model_policy" not in data:
        data["image_model_policy"] = dict(LEGACY_IMAGE_MODEL_POLICY)
    data.setdefault("logo", None)
    data.setdefault("logo_plan", None)
    data.setdefault("layout_families", None)
    logo_record = data.get("logo")
    if isinstance(logo_record, dict):
        logo_record.setdefault("normalized", None)
        logo_record.setdefault("normalized_sha256", None)
        logo_record.setdefault("normalization", None)
    localization_policy = data.get("localization_policy")
    if isinstance(localization_policy, dict):
        if localization_policy.get("mode") == PURE_GENERATION_LOCALIZATION_MODE:
            localization_policy.setdefault("quality_attempts", 3)
        else:
            localization_policy.setdefault("reference_edit_quality_attempts", 3)
            localization_policy.setdefault("pure_rebuild_quality_attempts_after_approval", 3)
    ratio = str(data.get("ratio", ""))
    for item in data.get("items", []):
        item.setdefault("role", "target")
        item.setdefault("source", "")
        item.setdefault("source_sha256", None)
        item.setdefault("output_relative_path", Path(str(item.get("output", ""))).name)
        item.setdefault("attempt_history", [])
        item.setdefault("pure_rebuild_approval", None)
        item.setdefault("localization_plan", None)
        item.setdefault("localization_plan_registration", None)
        item.setdefault("localization_execution_stage", None)
        item.setdefault("localization_composition", None)
        item.setdefault("updated_at", None)
        item.setdefault("output_validation", None)
        item.setdefault("base_output", None)
        item.setdefault(
            "localized_base",
            item.get("base_output") if data.get("mode") == "localization" and not data.get("logo") else None,
        )
        item.setdefault("localization_validation", None)
        item.setdefault("conflict_reference_base", None)
        item.setdefault(
            "prepared_base",
            item.get("base_output") if data.get("logo") and data.get("mode") != "localization" else None,
        )
        item.setdefault("logo_relocation_validation", None)
        item.setdefault("family_id", None)
        item.setdefault("logo_decision", None)
        item.setdefault("module_anchors", [])
        dimensions, expected_ratio = expected_geometry(
            ratio,
            int(item.get("width", 0) or 0),
            int(item.get("height", 0) or 0),
        )
        item.setdefault("expected_dimensions", dimensions)
        item.setdefault("expected_ratio", expected_ratio)
        item.setdefault("expected_format", FORMAT_BY_SUFFIX.get(Path(str(item.get("output", ""))).suffix.lower()))
        item.setdefault("expected_alpha", None)
    return data


def load_manifest(path: Path) -> dict[str, Any]:
    return upgrade_manifest(json.loads(path.read_text(encoding="utf-8")))


def canonical_path_key(path: Path) -> str:
    normalized = unicodedata.normalize("NFC", os.path.normcase(str(path.resolve())))
    return normalized.replace("\\", "/").casefold()


def logo_canvas_requires_review(path: Path, alpha_threshold: int = LOGO_ALPHA_THRESHOLD) -> bool:
    """Return true when visible alpha reaches every edge of the supplied Logo canvas."""
    if not 1 <= alpha_threshold <= 255:
        raise ValueError("alpha threshold must be between 1 and 255")
    with Image.open(path) as source:
        logo = ImageOps.exif_transpose(source).convert("RGBA")
    alpha_mask = logo.getchannel("A").point(lambda value: 255 if value >= alpha_threshold else 0)
    bbox = alpha_mask.getbbox()
    if not bbox:
        raise ValueError("Logo has no visible pixels at the selected alpha threshold")
    return bbox == (0, 0, logo.width, logo.height)


def active_logo_asset(manifest: dict[str, Any]) -> tuple[Path | None, str | None, list[str]]:
    """Resolve and verify the normalized Logo when registered, otherwise the original asset."""
    errors: list[str] = []
    record = manifest.get("logo")
    if not isinstance(record, dict) or not record.get("enabled"):
        return None, None, errors
    normalized = record.get("normalized")
    normalized_sha256 = record.get("normalized_sha256")
    source_value = record.get("source")
    source_digest = record.get("source_sha256")
    if not source_value or not source_digest:
        errors.append("Logo original source path and SHA-256 are required")
    else:
        source_path = Path(str(source_value)).resolve()
        if not source_path.is_file():
            errors.append("Logo original source file is missing")
        else:
            try:
                if sha256_file(source_path) != source_digest:
                    errors.append("Logo original source hash changed")
            except OSError as exc:
                errors.append(f"Logo original source is unreadable: {exc}")
    if bool(normalized) != bool(normalized_sha256):
        errors.append("Logo normalized path and hash must be registered together")
    path_value = normalized or record.get("source")
    digest = normalized_sha256 or record.get("source_sha256")
    if not path_value or not digest:
        errors.append("Logo active asset path and SHA-256 are required")
        return None, None, errors
    path = Path(str(path_value)).resolve()
    if not path.is_file():
        errors.append("Logo active asset file is missing")
        return path, str(digest), errors
    try:
        current_digest = sha256_file(path)
    except OSError as exc:
        errors.append(f"Logo active asset is unreadable: {exc}")
    else:
        if current_digest != digest:
            errors.append("Logo active asset hash changed")
    if normalized:
        normalization = record.get("normalization")
        if not isinstance(normalization, dict):
            errors.append("normalized Logo requires normalization provenance")
        else:
            if canonical_path_key(Path(str(normalization.get("source") or ""))) != canonical_path_key(
                Path(str(source_value or ""))
            ):
                errors.append("Logo normalization source does not match the original asset")
            if normalization.get("source_sha256") != source_digest:
                errors.append("Logo normalization source hash does not match the original asset")
            if canonical_path_key(Path(str(normalization.get("normalized") or ""))) != canonical_path_key(path):
                errors.append("Logo normalization output does not match the active asset")
            if normalization.get("normalized_sha256") != digest:
                errors.append("Logo normalization output hash does not match the active asset")
    return path, str(digest), errors


def standard_logo_overlay_and_geometry(
    logo_path: Path,
    canvas_size: tuple[int, int],
) -> tuple[Image.Image, dict[str, Any]]:
    """Recompute the locked Logo overlay and geometry from the active asset."""
    width, height = canvas_size
    if width < 1 or height < 1:
        raise ValueError("Logo canvas dimensions must be positive")
    with Image.open(logo_path) as source:
        logo = ImageOps.exif_transpose(source).convert("RGBA")
    alpha = logo.getchannel("A").point(
        lambda value: 0 if value < LOGO_ALPHA_THRESHOLD else value
    )
    logo.putalpha(alpha)
    content_bbox = alpha.point(
        lambda value: 255 if value >= LOGO_ALPHA_THRESHOLD else 0
    ).getbbox()
    if not content_bbox:
        raise ValueError("Logo has no visible pixels at the locked alpha threshold")
    logo = logo.crop(content_bbox)

    scale = min(canvas_size) / LOGO_REFERENCE_SHORT_SIDE
    box_width = max(1, round(LOGO_REFERENCE_BOX[0] * scale))
    box_height = max(1, round(LOGO_REFERENCE_BOX[1] * scale))
    fit = min(box_width / logo.width, box_height / logo.height)
    overlay_size = (
        max(1, round(logo.width * fit)),
        max(1, round(logo.height * fit)),
    )
    if overlay_size[0] > width or overlay_size[1] > height:
        raise ValueError("scaled Logo does not fit inside the final canvas")
    overlay = logo.resize(overlay_size, Image.Resampling.LANCZOS)
    overlay_alpha = overlay.getchannel("A").point(
        lambda value: 255 if value >= LOGO_ALPHA_THRESHOLD else 0
    )
    visible = overlay_alpha.getbbox()
    if not visible:
        raise ValueError("scaled Logo has no visible pixels")
    scaled_padding = max(0, round(LOGO_SAFE_PADDING * scale))
    scaled_tolerance = max(0, round(LOGO_ANCHOR_TOLERANCE * scale))
    zone = (
        0,
        0,
        min(width, visible[2] + scaled_padding),
        min(height, visible[3] + scaled_padding),
    )
    right_start = zone[2]
    below_start = zone[3]
    geometry = {
        "canvas": [width, height],
        "scale": scale,
        "logo_canvas": [overlay.width, overlay.height],
        "visible_bbox": list(visible),
        "safe_padding": scaled_padding,
        "safe_zone": list(zone),
        "right_module_anchor": [right_start, visible[1]],
        "right_module_start_range": [
            right_start,
            min(width, right_start + scaled_tolerance),
        ],
        "right_available": right_start < width,
        "below_module_anchor": [visible[0], below_start],
        "below_module_start_range": [
            below_start,
            min(height, below_start + scaled_tolerance),
        ],
        "below_available": below_start < height,
    }
    return overlay, geometry


def _mean_absolute_difference(first: Image.Image, second: Image.Image) -> tuple[float, float]:
    difference = ImageChops.difference(first.convert("RGB"), second.convert("RGB"))
    histogram = difference.histogram()
    samples = max(1, first.width * first.height * 3)
    absolute = 0
    squared = 0
    for band in range(3):
        offset = band * 256
        for value in range(256):
            count = histogram[offset + value]
            absolute += value * count
            squared += value * value * count
    return absolute / samples, math.sqrt(squared / samples)


def _images_pixel_equal(first: Image.Image, second: Image.Image) -> bool:
    if first.size != second.size:
        return False
    difference = ImageChops.difference(first.convert("RGBA"), second.convert("RGBA"))
    return all(channel.getbbox() is None for channel in difference.split())


def validate_logo_overlay_pixels(
    prepared_base: Path,
    final_output: Path,
    logo_path: Path,
    image_format: str,
) -> list[str]:
    """Prove that final is the deterministic active-Logo composite of prepared_base."""
    errors: list[str] = []
    try:
        with Image.open(prepared_base) as raw:
            prepared = ImageOps.exif_transpose(raw).convert("RGBA")
            prepared.load()
        with Image.open(final_output) as raw:
            final = ImageOps.exif_transpose(raw).convert("RGBA")
            final.load()
        overlay, geometry = standard_logo_overlay_and_geometry(logo_path, prepared.size)
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        return [f"could not verify final Logo pixels: {exc}"]
    if prepared.size != final.size:
        return ["final Logo output dimensions differ from prepared_base"]
    if _images_pixel_equal(prepared, final):
        errors.append("final Logo output is pixel-identical to prepared_base; no Logo was added")

    expected = prepared.copy()
    expected.alpha_composite(overlay, (0, 0))
    normalized_format = image_format.upper()
    if normalized_format in LOSSLESS_PIXEL_FORMATS:
        if not _images_pixel_equal(expected, final):
            errors.append("final Logo pixels do not exactly match the locked deterministic composite")
        return errors
    if normalized_format not in LOSSY_PIXEL_FORMATS:
        errors.append(f"Logo pixel verification does not support output format {image_format}")
        return errors
    try:
        encoded = io.BytesIO()
        if normalized_format == "JPEG":
            expected.convert("RGB").save(
                encoded,
                "JPEG",
                quality=95,
                optimize=True,
                progressive=True,
                subsampling=0,
            )
        else:
            expected.save(encoded, "WEBP")
        encoded.seek(0)
        with Image.open(encoded) as decoded:
            deterministic_expected = decoded.convert("RGBA")
            deterministic_expected.load()
    except (OSError, ValueError) as exc:
        errors.append(f"could not reproduce deterministic {normalized_format} Logo encoding: {exc}")
        return errors
    if not _images_pixel_equal(deterministic_expected, final):
        errors.append(
            f"final {normalized_format} pixels do not exactly match the deterministic Logo composite encoding"
        )
    return errors


def _pearson_correlation(first: list[int], second: list[int]) -> float:
    if len(first) != len(second) or not first:
        return 0.0
    first_mean = sum(first) / len(first)
    second_mean = sum(second) / len(second)
    numerator = sum(
        (first_value - first_mean) * (second_value - second_mean)
        for first_value, second_value in zip(first, second)
    )
    first_energy = sum((value - first_mean) ** 2 for value in first)
    second_energy = sum((value - second_mean) ** 2 for value in second)
    denominator = math.sqrt(first_energy * second_energy)
    if denominator <= 1e-12:
        return 1.0 if abs(first_mean - second_mean) <= 32 else 0.0
    return numerator / denominator


def localization_layout_metrics(source: Path, candidate: Path) -> dict[str, Any]:
    """Measure low-frequency layout preservation without OCR or optional CV dependencies."""
    with Image.open(source) as raw_source:
        source_image = ImageOps.exif_transpose(raw_source).convert("RGB")
        source_image.load()
    with Image.open(candidate) as raw_candidate:
        candidate_image = ImageOps.exif_transpose(raw_candidate).convert("RGB")
        candidate_image.load()

    low_size = (32, 32)
    source_low = source_image.resize(low_size, Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(2))
    candidate_low = candidate_image.resize(low_size, Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(2))
    low_difference = ImageChops.difference(source_low, candidate_low)
    low_stats = ImageStat.Stat(low_difference)
    low_mae = sum(low_stats.mean) / (3 * 255)
    low_rms = math.sqrt(sum(value * value for value in low_stats.rms) / 3) / 255

    block_size = (16, 16)
    source_blocks = ImageOps.grayscale(
        source_image.resize(block_size, Image.Resampling.BOX)
    )
    candidate_blocks = ImageOps.grayscale(
        candidate_image.resize(block_size, Image.Resampling.BOX)
    )
    correlation = _pearson_correlation(
        list(source_blocks.tobytes()),
        list(candidate_blocks.tobytes()),
    )

    edge_size = (128, 128)
    source_edges = ImageOps.grayscale(
        source_image.resize(edge_size, Image.Resampling.LANCZOS)
    ).filter(ImageFilter.GaussianBlur(1)).filter(ImageFilter.FIND_EDGES)
    candidate_edges = ImageOps.grayscale(
        candidate_image.resize(edge_size, Image.Resampling.LANCZOS)
    ).filter(ImageFilter.GaussianBlur(1)).filter(ImageFilter.FIND_EDGES)
    edge_stats = ImageStat.Stat(ImageChops.difference(source_edges, candidate_edges))
    edge_rms = edge_stats.rms[0] / 255
    return {
        "source_size": [source_image.width, source_image.height],
        "candidate_size": [candidate_image.width, candidate_image.height],
        "block_luma_correlation": round(correlation, 8),
        "low_frequency_mae": round(low_mae, 8),
        "low_frequency_rms": round(low_rms, 8),
        "edge_rms": round(edge_rms, 8),
    }


def _normalized_localization_bbox(
    raw_box: Any,
    size: tuple[int, int],
) -> list[int] | None:
    if not (
        isinstance(raw_box, list)
        and len(raw_box) == 4
        and all(
            isinstance(value, (int, float)) and math.isfinite(value)
            for value in raw_box
        )
    ):
        return None
    width, height = size
    box = [
        math.floor(raw_box[0]),
        math.floor(raw_box[1]),
        math.ceil(raw_box[2]),
        math.ceil(raw_box[3]),
    ]
    if not (
        0 <= box[0] < box[2] <= width
        and 0 <= box[1] < box[3] <= height
    ):
        return None
    return box


def _localization_bbox_intersection(
    first: list[int],
    second: list[int],
) -> list[int] | None:
    intersection = [
        max(first[0], second[0]),
        max(first[1], second[1]),
        min(first[2], second[2]),
        min(first[3], second[3]),
    ]
    if intersection[0] >= intersection[2] or intersection[1] >= intersection[3]:
        return None
    return intersection


def _localization_bbox_contains(outer: list[int], inner: list[int]) -> bool:
    return (
        outer[0] <= inner[0]
        and outer[1] <= inner[1]
        and outer[2] >= inner[2]
        and outer[3] >= inner[3]
    )


def _localization_text_edit_mask(
    size: tuple[int, int],
    text_blocks: Any,
    non_text_inventory: Any = _LOCALIZATION_INVENTORY_UNSET,
) -> tuple[Image.Image, dict[str, Any], list[str]]:
    """Build the only pixel region a text-only localization may modify."""
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    errors: list[str] = []
    if not isinstance(text_blocks, list):
        return mask, {}, ["localization pixel lock requires a text_blocks list"]

    inventory_required = non_text_inventory is not _LOCALIZATION_INVENTORY_UNSET
    inventory_by_id: dict[str, dict[str, Any]] = {}
    normalized_inventory: list[dict[str, Any]] = []
    if inventory_required:
        if not isinstance(non_text_inventory, list):
            errors.append("localization non_text_inventory must be a structured list")
        elif not non_text_inventory:
            errors.append("localization non_text_inventory must not be empty")
        else:
            for inventory_index, entry in enumerate(non_text_inventory, start=1):
                if not isinstance(entry, dict):
                    errors.append(
                        f"localization non_text_inventory item {inventory_index} must be an object; "
                        "legacy string inventory is not accepted"
                    )
                    continue
                entry_id = str(entry.get("id") or "").strip()
                kind = str(entry.get("kind") or "").strip()
                if not entry_id:
                    errors.append(
                        f"localization non_text_inventory item {inventory_index} has an empty id"
                    )
                    continue
                if entry_id in inventory_by_id:
                    errors.append(
                        f"localization non_text_inventory id {entry_id} is duplicated"
                    )
                    continue
                if kind == "background_surface":
                    if entry.get("scope") != "canvas":
                        errors.append(
                            f"localization background_surface {entry_id} requires scope=canvas"
                        )
                    if "bbox" not in entry or entry.get("bbox") is not None:
                        errors.append(
                            f"localization background_surface {entry_id} requires bbox=null"
                        )
                    normalized = {
                        "id": entry_id,
                        "kind": kind,
                        "scope": "canvas",
                        "bbox": None,
                    }
                elif kind == "element":
                    element_box = _normalized_localization_bbox(entry.get("bbox"), size)
                    if element_box is None:
                        errors.append(
                            f"localization non-text element {entry_id} requires a valid canvas bbox"
                        )
                        continue
                    if entry.get("scope") not in {None, "", "region"}:
                        errors.append(
                            f"localization non-text element {entry_id} scope must be region when present"
                        )
                    normalized = {
                        "id": entry_id,
                        "kind": kind,
                        "scope": "region",
                        "bbox": element_box,
                    }
                else:
                    errors.append(
                        f"localization non_text_inventory item {entry_id} kind must be "
                        "background_surface or element"
                    )
                    continue
                inventory_by_id[entry_id] = normalized
                normalized_inventory.append(normalized)

    normalized_boxes: list[list[int]] = []
    protected_boxes: list[dict[str, Any]] = []
    width, height = size
    for index, block in enumerate(text_blocks, start=1):
        if not isinstance(block, dict):
            errors.append(f"localization text block {index} must be an object")
            continue
        current_block_boxes: list[list[int]] = []
        block_boxes: dict[str, list[int]] = {}
        for field in ("source_bbox", "target_bbox"):
            raw_box = block.get(field)
            box = _normalized_localization_bbox(raw_box, size)
            if box is None:
                errors.append(f"localization text block {index} {field} is outside the source canvas")
                continue
            left, top, right, bottom = box
            normalized_boxes.append(box)
            current_block_boxes.append(box)
            block_boxes[field] = box
            box_fraction = ((right - left) * (bottom - top)) / (width * height)
            if box_fraction > LOCALIZATION_TEXT_BOX_FRACTION_MAX:
                errors.append(
                    f"localization text block {index} {field} is too large to prove a text-only edit "
                    f"({box_fraction:.2%} > {LOCALIZATION_TEXT_BOX_FRACTION_MAX:.0%})"
                )
            draw.rectangle((left, top, right - 1, bottom - 1), fill=255)

        protected_regions = block.get("protected_non_text_regions")
        if not isinstance(protected_regions, list):
            errors.append(
                f"localization text block {index} requires a protected_non_text_regions list"
            )
            continue
        protected_by_id: dict[str, list[int]] = {}
        for protected_index, region in enumerate(protected_regions, start=1):
            if not isinstance(region, dict):
                errors.append(
                    f"localization text block {index} protected region {protected_index} must be an object"
                )
                continue
            region_id = str(region.get("id") or "").strip()
            raw_box = region.get("bbox")
            if not region_id:
                errors.append(
                    f"localization text block {index} protected region {protected_index} has an empty id"
                )
            protected_box = _normalized_localization_bbox(raw_box, size)
            if protected_box is None:
                errors.append(
                    f"localization text block {index} protected region {protected_index} has an invalid bbox"
                )
                continue
            if region_id in protected_by_id:
                errors.append(
                    f"localization text block {index} protected region id {region_id} is duplicated"
                )
            elif region_id:
                protected_by_id[region_id] = protected_box
            if not any(
                _localization_bbox_intersection(protected_box, box) is not None
                for box in current_block_boxes
            ):
                errors.append(
                    f"localization text block {index} protected region {region_id or protected_index} "
                    "does not intersect its editable bboxes"
                )
            if inventory_required and region_id:
                inventory_entry = inventory_by_id.get(region_id)
                if inventory_entry is None:
                    errors.append(
                        f"localization text block {index} protected region {region_id} "
                        "does not match a structured non_text_inventory item"
                    )
                elif inventory_entry.get("kind") == "background_surface":
                    errors.append(
                        f"localization text block {index} cannot protect exempt background_surface {region_id}"
                    )
                else:
                    element_box = inventory_entry.get("bbox")
                    if isinstance(element_box, list) and not _localization_bbox_contains(
                        element_box,
                        protected_box,
                    ):
                        errors.append(
                            f"localization text block {index} protected region {region_id} "
                            "extends outside its inventory element bbox"
                        )
            protected_boxes.append({"id": region_id, "bbox": protected_box})

        if inventory_required and len(block_boxes) == 2:
            source_box = block_boxes["source_bbox"]
            target_box = block_boxes["target_bbox"]
            block_id = str(block.get("id") or f"text-{index:02d}")
            for element_id, inventory_entry in inventory_by_id.items():
                if inventory_entry.get("kind") != "element":
                    continue
                element_box = inventory_entry.get("bbox")
                if not isinstance(element_box, list):
                    continue
                source_intersection = _localization_bbox_intersection(element_box, source_box)
                target_intersection = _localization_bbox_intersection(element_box, target_box)
                if target_intersection is not None and not _localization_bbox_contains(
                    source_box,
                    target_intersection,
                ):
                    errors.append(
                        f"localization text block {block_id} target_bbox intrudes into non-text "
                        f"element {element_id} outside source_bbox"
                    )
                required_intersections = [
                    intersection
                    for intersection in (source_intersection, target_intersection)
                    if intersection is not None
                ]
                if not required_intersections:
                    continue
                protected_box = protected_by_id.get(element_id)
                if protected_box is None:
                    errors.append(
                        f"localization text block {block_id} intersects non-text element {element_id} "
                        "but does not protect it"
                    )
                    continue
                if any(
                    not _localization_bbox_contains(protected_box, intersection)
                    for intersection in required_intersections
                ):
                    errors.append(
                        f"localization text block {block_id} protected region {element_id} "
                        "does not fully cover the element intersection"
                    )

    editable_before_protection = mask.histogram()[255]
    for region in protected_boxes:
        left, top, right, bottom = region["bbox"]
        draw.rectangle((left, top, right - 1, bottom - 1), fill=0)

    editable_pixels = mask.histogram()[255]
    total_pixels = width * height
    editable_fraction = editable_pixels / total_pixels if total_pixels else 1.0
    metrics = {
        "editable_boxes": normalized_boxes,
        "protected_non_text_regions": protected_boxes,
        "protected_pixels": editable_before_protection - editable_pixels,
        "editable_pixels": editable_pixels,
        "editable_fraction": round(editable_fraction, 8),
        "non_text_pixels": total_pixels - editable_pixels,
    }
    if inventory_required:
        metrics["non_text_inventory"] = normalized_inventory
    if editable_fraction > LOCALIZATION_TEXT_MASK_FRACTION_MAX:
        errors.append(
            "localization text masks cover too much of the canvas to prove a text-only edit "
            f"({editable_fraction:.2%} > {LOCALIZATION_TEXT_MASK_FRACTION_MAX:.0%})"
        )
    return mask, metrics, errors


def _localization_text_region_change_guard(
    source_image: Image.Image,
    candidate_image: Image.Image,
    text_blocks: Any,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Reject no-op translations and near-total replacement inside each text-box union."""
    if source_image.size != candidate_image.size:
        return [], ["localization text-region guard requires source-size candidate pixels"]
    if not isinstance(text_blocks, list):
        return [], ["localization text-region guard requires a text_blocks list"]

    metrics: list[dict[str, Any]] = []
    errors: list[str] = []
    width, height = source_image.size
    for index, block in enumerate(text_blocks, start=1):
        if not isinstance(block, dict):
            continue
        boxes: list[list[int]] = []
        valid = True
        for field in ("source_bbox", "target_bbox"):
            raw_box = block.get(field)
            if not (
                isinstance(raw_box, list)
                and len(raw_box) == 4
                and all(isinstance(value, (int, float)) and math.isfinite(value) for value in raw_box)
            ):
                valid = False
                break
            box = [
                math.floor(raw_box[0]),
                math.floor(raw_box[1]),
                math.ceil(raw_box[2]),
                math.ceil(raw_box[3]),
            ]
            if not (0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height):
                valid = False
                break
            boxes.append(box)
        if not valid:
            continue
        union_box = [
            min(boxes[0][0], boxes[1][0]),
            min(boxes[0][1], boxes[1][1]),
            max(boxes[0][2], boxes[1][2]),
            max(boxes[0][3], boxes[1][3]),
        ]
        source_crop = source_image.crop(tuple(union_box)).convert("RGBA")
        candidate_crop = candidate_image.crop(tuple(union_box)).convert("RGBA")
        block_mask = Image.new("L", source_crop.size, 0)
        block_draw = ImageDraw.Draw(block_mask)
        for box in boxes:
            block_draw.rectangle(
                (
                    box[0] - union_box[0],
                    box[1] - union_box[1],
                    box[2] - union_box[0] - 1,
                    box[3] - union_box[1] - 1,
                ),
                fill=255,
            )
        protected_regions = block.get("protected_non_text_regions")
        if isinstance(protected_regions, list):
            for region in protected_regions:
                if not isinstance(region, dict):
                    continue
                raw_box = region.get("bbox")
                if not (
                    isinstance(raw_box, list)
                    and len(raw_box) == 4
                    and all(
                        isinstance(value, (int, float)) and math.isfinite(value)
                        for value in raw_box
                    )
                ):
                    continue
                protected_box = [
                    math.floor(raw_box[0]),
                    math.floor(raw_box[1]),
                    math.ceil(raw_box[2]),
                    math.ceil(raw_box[3]),
                ]
                intersection = (
                    max(protected_box[0], union_box[0]),
                    max(protected_box[1], union_box[1]),
                    min(protected_box[2], union_box[2]),
                    min(protected_box[3], union_box[3]),
                )
                if intersection[0] < intersection[2] and intersection[1] < intersection[3]:
                    block_draw.rectangle(
                        (
                            intersection[0] - union_box[0],
                            intersection[1] - union_box[1],
                            intersection[2] - union_box[0] - 1,
                            intersection[3] - union_box[1] - 1,
                        ),
                        fill=0,
                    )
        difference = ImageChops.difference(source_crop, candidate_crop)
        changed = difference.getchannel("R").point(lambda value: 255 if value else 0)
        meaningful = difference.getchannel("R").point(
            lambda value: 255 if value >= LOCALIZATION_TEXT_MEANINGFUL_DELTA else 0
        )
        significant = difference.getchannel("R").point(
            lambda value: 255 if value >= LOCALIZATION_TEXT_SIGNIFICANT_DELTA else 0
        )
        for channel in ("G", "B", "A"):
            changed = ImageChops.lighter(
                changed,
                difference.getchannel(channel).point(lambda value: 255 if value else 0),
            )
            meaningful = ImageChops.lighter(
                meaningful,
                difference.getchannel(channel).point(
                    lambda value: 255 if value >= LOCALIZATION_TEXT_MEANINGFUL_DELTA else 0
                ),
            )
            significant = ImageChops.lighter(
                significant,
                difference.getchannel(channel).point(
                    lambda value: 255 if value >= LOCALIZATION_TEXT_SIGNIFICANT_DELTA else 0
                ),
            )
        changed_pixels = ImageChops.multiply(changed, block_mask).histogram()[255]
        meaningful_pixels = ImageChops.multiply(meaningful, block_mask).histogram()[255]
        significant_pixels = ImageChops.multiply(significant, block_mask).histogram()[255]
        area = block_mask.histogram()[255]
        changed_fraction = changed_pixels / area if area else 1.0
        meaningful_fraction = meaningful_pixels / area if area else 1.0
        significant_fraction = significant_pixels / area if area else 1.0
        block_id = str(block.get("id") or f"text-{index:02d}")
        source_text = str(block.get("source") or "")
        target_text = str(block.get("translation") or "")
        text_changed = bool(source_text and target_text and source_text != target_text)
        metrics.append({
            "id": block_id,
            "union_bbox": union_box,
            "editable_union_pixels": area,
            "changed_pixels": changed_pixels,
            "changed_fraction": round(changed_fraction, 8),
            "meaningful_delta": LOCALIZATION_TEXT_MEANINGFUL_DELTA,
            "meaningful_changed_pixels": meaningful_pixels,
            "meaningful_changed_fraction": round(meaningful_fraction, 8),
            "significant_delta": LOCALIZATION_TEXT_SIGNIFICANT_DELTA,
            "significant_changed_pixels": significant_pixels,
            "significant_changed_fraction": round(significant_fraction, 8),
            "target_text_differs_from_source": text_changed,
        })
        if area == 0:
            errors.append(
                f"localization text block {block_id} has no editable pixels after non-text protection"
            )
        if meaningful_fraction > LOCALIZATION_TEXT_MEANINGFUL_FRACTION_MAX:
            errors.append(
                f"localization text block {block_id} changed too many meaningful pixels inside its editable bbox union "
                f"({meaningful_fraction:.2%} > {LOCALIZATION_TEXT_MEANINGFUL_FRACTION_MAX:.0%})"
            )
        if significant_fraction > LOCALIZATION_TEXT_SIGNIFICANT_FRACTION_MAX:
            errors.append(
                f"localization text block {block_id} changed too much of its rectangle to prove a glyph-only edit "
                f"({significant_fraction:.2%} > {LOCALIZATION_TEXT_SIGNIFICANT_FRACTION_MAX:.0%})"
            )
        if text_changed and significant_fraction < LOCALIZATION_TEXT_REQUIRED_CHANGE_FRACTION_MIN:
            errors.append(
                f"localization text block {block_id} did not visibly change despite a different locked translation"
            )
    return metrics, errors


def localization_non_text_pixel_lock(
    source: Path,
    candidate: Path,
    plan: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Require every decoded pixel outside planned text boxes to equal the source."""
    errors: list[str] = []
    ratio_adaptation = plan.get("ratio_adaptation")
    ratio_adaptation_required = (
        isinstance(ratio_adaptation, dict) and ratio_adaptation.get("required") is True
    )
    if ratio_adaptation_required:
        errors.append(
            "text-only localization ratio adaptation is fail-closed because no structured, "
            "recomputable coordinate mapping is registered"
        )

    with Image.open(source) as raw_source:
        source_image = ImageOps.exif_transpose(raw_source).convert("RGBA")
        source_image.load()
    with Image.open(candidate) as raw_candidate:
        candidate_image = ImageOps.exif_transpose(raw_candidate).convert("RGBA")
        candidate_image.load()

    mask, mask_metrics, mask_errors = _localization_text_edit_mask(
        source_image.size,
        plan.get("text_blocks"),
        plan.get("non_text_inventory"),
    )
    errors.extend(mask_errors)
    changed_outside = 0
    text_region_metrics: list[dict[str, Any]] = []
    if candidate_image.size != source_image.size:
        errors.append(
            "text-only localized_base dimensions must exactly match the source before deterministic resampling"
        )
    else:
        difference = ImageChops.difference(source_image, candidate_image)
        changed = difference.getchannel("R").point(lambda value: 255 if value else 0)
        for channel in ("G", "B", "A"):
            changed = ImageChops.lighter(
                changed,
                difference.getchannel(channel).point(lambda value: 255 if value else 0),
            )
        outside_mask = ImageOps.invert(mask)
        changed_outside = ImageChops.multiply(changed, outside_mask).histogram()[255]
        if changed_outside:
            errors.append(
                "localization non-text pixel lock failed: "
                f"{changed_outside} pixel(s) changed outside planned text bboxes"
            )
        text_region_metrics, text_region_errors = _localization_text_region_change_guard(
            source_image,
            candidate_image,
            plan.get("text_blocks"),
        )
        errors.extend(text_region_errors)

    record = {
        "schema_version": 3,
        "producer": "xobi-img.localization-guard",
        "contract": "translation-non-text-pixel-lock-v3",
        "source": str(source),
        "source_sha256": sha256_file(source),
        "candidate": str(candidate),
        "candidate_sha256": sha256_file(candidate),
        "source_size": [source_image.width, source_image.height],
        "candidate_size": [candidate_image.width, candidate_image.height],
        "ratio_adaptation_required": ratio_adaptation_required,
        "mask": mask_metrics,
        "text_region_changes": text_region_metrics,
        "non_text_changed_pixels": changed_outside,
        "text_box_fraction_max": LOCALIZATION_TEXT_BOX_FRACTION_MAX,
        "text_mask_fraction_max": LOCALIZATION_TEXT_MASK_FRACTION_MAX,
        "text_meaningful_delta": LOCALIZATION_TEXT_MEANINGFUL_DELTA,
        "text_meaningful_fraction_max": LOCALIZATION_TEXT_MEANINGFUL_FRACTION_MAX,
        "text_significant_fraction_max": LOCALIZATION_TEXT_SIGNIFICANT_FRACTION_MAX,
        "text_required_change_fraction_min": LOCALIZATION_TEXT_REQUIRED_CHANGE_FRACTION_MIN,
        "passed": not errors,
    }
    return record, errors


def validate_localization_plan_contract(
    item: dict[str, Any],
    manifest: dict[str, Any],
    plan: Any,
) -> list[str]:
    """Validate the complete immutable localization plan before its first image attempt."""
    if not isinstance(plan, dict):
        return ["localization_plan must be an object"]

    errors: list[str] = []
    policy = manifest.get("localization_policy")
    policy_mode = str(policy.get("mode") or "") if isinstance(policy, dict) else ""
    pure_generation_plan = policy_mode == PURE_GENERATION_LOCALIZATION_MODE
    expected_plan_mode = (
        PURE_GENERATION_LOCALIZATION_MODE
        if pure_generation_plan
        else LEGACY_REFERENCE_LOCALIZATION_MODE
    )
    required = {
        "task_id",
        "mode",
        "source",
        "source_sha256",
        "source_size",
        "target_language",
        "output_ratio",
        "target_size",
        "size_resample",
        "ratio_adaptation",
        "text_blocks",
        "non_text_inventory",
    }
    if not pure_generation_plan:
        required.add("pure_rebuild_allowed")
    missing = sorted(required - set(plan))
    if missing:
        errors.append("localization_plan is missing fields: " + ", ".join(missing))

    if str(plan.get("task_id") or "") != str(item.get("task_id") or ""):
        errors.append("localization_plan task_id does not match the manifest item")
    if canonical_path_key(Path(str(plan.get("source") or ""))) != canonical_path_key(
        Path(str(item.get("source") or ""))
    ):
        errors.append("localization_plan source does not match the manifest item")
    if plan.get("source_sha256") != item.get("source_sha256"):
        errors.append("localization_plan source hash does not match preflight")
    source_size = [item.get("width"), item.get("height")]
    if plan.get("source_size") != source_size:
        errors.append("localization_plan source_size does not match preflight")
    if str(plan.get("target_language") or "").casefold() != str(
        manifest.get("target_language") or ""
    ).casefold():
        errors.append("localization_plan target language does not match the manifest")
    if plan.get("mode") != expected_plan_mode:
        errors.append(
            f"the frozen localization_plan mode must remain {expected_plan_mode}"
        )
    if pure_generation_plan:
        rebuild_flag = plan.get("pure_rebuild_allowed")
        if rebuild_flag is not None and rebuild_flag is not False:
            errors.append("pure-generation localization must not carry a rebuild approval flag")
    elif plan.get("pure_rebuild_allowed") is not False:
        errors.append("the frozen localization_plan cannot pre-authorize pure rebuild")
    if "unresolved_text" in plan and not isinstance(plan.get("unresolved_text"), list):
        errors.append("localization_plan unresolved_text must be a list when present")

    text_blocks = plan.get("text_blocks")
    inventory = plan.get("non_text_inventory")
    if not isinstance(text_blocks, list):
        errors.append("localization_plan text_blocks must be a list")
        text_blocks = []
    if not isinstance(inventory, list):
        errors.append("localization_plan non_text_inventory must be a structured list")
    inventory_ids = {
        str(value.get("id") or "").strip()
        for value in inventory
        if isinstance(value, dict) and str(value.get("id") or "").strip()
    } if isinstance(inventory, list) else set()

    block_ids: list[str] = []
    for index, block in enumerate(text_blocks, start=1):
        if not isinstance(block, dict):
            errors.append(f"localization_plan text block {index} must be an object")
            continue
        block_required = {
            "id",
            "source_bbox",
            "target_bbox",
            "source",
            "translation",
            "target_text_source",
            "role",
            "text_layout_adaptation",
            "protected_non_text_regions",
        }
        missing_block = sorted(block_required - set(block))
        if missing_block:
            errors.append(
                f"localization_plan text block {index} is missing fields: " + ", ".join(missing_block)
            )
        block_id = str(block.get("id") or "").strip()
        if not block_id:
            errors.append(f"localization_plan text block {index} has an empty id")
        else:
            block_ids.append(block_id)
        if not isinstance(block.get("source"), str) or not block["source"].strip():
            errors.append(f"localization_plan text block {index} has empty source text")
        if not isinstance(block.get("translation"), str) or not block["translation"].strip():
            errors.append(f"localization_plan text block {index} has empty translated text")
        target_text_source = block.get("target_text_source")
        if target_text_source not in {"translated", "user_exact"}:
            errors.append(f"localization_plan text block {index} has invalid target_text_source")
        elif target_text_source == "user_exact":
            requested = block.get("requested_target_text")
            if not isinstance(requested, str) or not requested.strip():
                errors.append(
                    f"localization_plan text block {index} user_exact text requires requested_target_text"
                )
            elif block.get("translation") != requested:
                errors.append(
                    f"localization_plan text block {index} must preserve user_exact target text verbatim"
                )
        elif block.get("requested_target_text") not in {None, ""}:
            errors.append(f"localization_plan text block {index} requested_target_text requires user_exact")
        if not isinstance(block.get("role"), str) or not block["role"].strip():
            errors.append(f"localization_plan text block {index} has an empty role")

        protected_regions = block.get("protected_non_text_regions")
        block_protected_ids: list[str] = []
        if not isinstance(protected_regions, list):
            errors.append(
                f"localization_plan text block {index} protected_non_text_regions must be a list"
            )
        else:
            for protected_index, region in enumerate(protected_regions, start=1):
                if not isinstance(region, dict):
                    errors.append(
                        f"localization_plan text block {index} protected region {protected_index} "
                        "must be an object"
                    )
                    continue
                region_id = str(region.get("id") or "").strip()
                if not region_id:
                    errors.append(
                        f"localization_plan text block {index} protected region {protected_index} "
                        "has an empty id"
                    )
                else:
                    block_protected_ids.append(region_id)
                    if isinstance(inventory, list) and region_id not in inventory_ids:
                        errors.append(
                            f"localization_plan text block {index} protected region {region_id} "
                            "is missing from non_text_inventory"
                        )
                if "bbox" not in region:
                    errors.append(
                        f"localization_plan text block {index} protected region {region_id or protected_index} "
                        "is missing bbox"
                    )
            if len(block_protected_ids) != len(set(block_protected_ids)):
                errors.append(
                    f"localization_plan text block {index} protected region ids must be unique"
                )

        source_bbox = block.get("source_bbox")
        target_bbox = block.get("target_bbox")
        layout = block.get("text_layout_adaptation")
        if not isinstance(layout, dict) or not isinstance(layout.get("required"), bool):
            errors.append(
                f"localization_plan text block {index} text_layout_adaptation.required must be boolean"
            )
        else:
            box_changed = source_bbox != target_bbox
            if layout["required"] != box_changed:
                errors.append(
                    f"localization_plan text block {index} text_layout_adaptation does not match its bboxes"
                )
            if box_changed and not str(layout.get("reason") or "").strip():
                errors.append(f"localization_plan text block {index} requires an expansion reason")
            alignment = layout.get("target_alignment")
            if alignment not in {None, "left", "center", "right", "start", "end"}:
                errors.append(f"localization_plan text block {index} has invalid target_alignment")
            direction = layout.get("writing_direction")
            if direction not in {None, "ltr", "rtl", "vertical"}:
                errors.append(f"localization_plan text block {index} has invalid writing_direction")
    if len(block_ids) != len(set(block_ids)):
        errors.append("localization_plan text block ids must be unique")

    width, height = int(item.get("width", 0) or 0), int(item.get("height", 0) or 0)
    _, _, mask_errors = _localization_text_edit_mask(
        (width, height),
        text_blocks,
        inventory,
    )
    errors.extend(mask_errors)

    size_resample = plan.get("size_resample")
    ratio_adaptation = plan.get("ratio_adaptation")
    if not isinstance(size_resample, dict) or not isinstance(ratio_adaptation, dict):
        errors.append("localization_plan size_resample and ratio_adaptation must be objects")
        return errors
    if not isinstance(size_resample.get("required"), bool):
        errors.append("localization_plan size_resample.required must be boolean")
    if not isinstance(ratio_adaptation.get("required"), bool):
        errors.append("localization_plan ratio_adaptation.required must be boolean")

    try:
        plan_dimensions, plan_ratio = expected_geometry(str(plan.get("output_ratio") or ""), width, height)
    except ValueError as exc:
        errors.append(f"localization_plan output_ratio is invalid: {exc}")
        return errors
    if plan_dimensions != item.get("expected_dimensions") or plan_ratio != item.get("expected_ratio"):
        errors.append("localization_plan output_ratio does not match the manifest output geometry")
    expected_dimensions = item.get("expected_dimensions")
    if expected_dimensions:
        if plan.get("target_size") != expected_dimensions:
            errors.append("localization_plan target_size does not match expected dimensions")
        target_width, target_height = int(expected_dimensions[0]), int(expected_dimensions[1])
        same_ratio = target_width * height == target_height * width
        same_size = [width, height] == [target_width, target_height]
    else:
        if plan.get("target_size") is not None:
            errors.append("localization_plan target_size must be null without exact output dimensions")
        expected_ratio = item.get("expected_ratio") or [width, height]
        same_ratio = int(expected_ratio[0]) * height == int(expected_ratio[1]) * width
        same_size = True
    if pure_generation_plan:
        if size_resample.get("required") is not False:
            errors.append("pure-generation localization size_resample.required must be false")
        if size_resample.get("method") not in {None, ""}:
            errors.append("pure-generation localization size_resample.method must be empty")
        ratio_required = ratio_adaptation.get("required")
        if ratio_required != (not same_ratio):
            errors.append("localization_plan ratio_adaptation.required does not match the geometry change")
        allowed_changes = ratio_adaptation.get("allowed_changes")
        if not same_ratio:
            if not isinstance(allowed_changes, list) or not allowed_changes:
                errors.append("pure-generation ratio adaptation requires explicit allowed_changes")
            else:
                normalized_changes = [str(value) for value in allowed_changes]
                if len(normalized_changes) != len(set(normalized_changes)):
                    errors.append("pure-generation ratio adaptation allowed_changes must be unique")
                invalid_changes = sorted(
                    set(normalized_changes) - PURE_GENERATION_RATIO_ALLOWED_CHANGES
                )
                if invalid_changes:
                    errors.append(
                        "pure-generation ratio adaptation contains forbidden changes: "
                        + ", ".join(invalid_changes)
                    )
                if "minimal_canvas_adaptation" not in normalized_changes:
                    errors.append(
                        "pure-generation ratio adaptation requires minimal_canvas_adaptation"
                    )
        elif allowed_changes != []:
            errors.append("same-ratio localization ratio_adaptation.allowed_changes must be empty")
    else:
        needs_size_resample = bool(expected_dimensions) and same_ratio and not same_size
        if bool(size_resample.get("required")) != needs_size_resample:
            errors.append("localization_plan size_resample.required does not match the geometry change")
        expected_method = "whole_canvas_lanczos" if needs_size_resample else None
        actual_method = size_resample.get("method")
        if (
            actual_method != expected_method
            and not (expected_method is None and actual_method == "")
        ):
            errors.append("localization_plan size_resample.method does not match the required deterministic method")
        if not same_ratio:
            errors.append("localization_plan output ratio differs from the source and must fail closed")
    return errors


def validate_localization_plan_registration(
    item: dict[str, Any],
    manifest: dict[str, Any],
) -> list[str]:
    """Verify that a localization plan was registered before attempts and never changed."""
    plan = item.get("localization_plan")
    registration = item.get("localization_plan_registration")
    try:
        attempts = int(item.get("attempts", 0) or 0)
    except (TypeError, ValueError):
        attempts = 0
    history = item.get("attempt_history")
    has_history = isinstance(history, list) and bool(history)
    if plan is None and registration is None:
        if attempts > 0 or has_history or item.get("status") == "success":
            return ["localization attempts require a frozen plan registered before the first attempt"]
        return []
    if not isinstance(plan, dict):
        return ["localization_plan must be a registered object"]
    if not isinstance(registration, dict):
        return ["localization_plan requires a frozen artifact registration"]

    errors = validate_localization_plan_contract(item, manifest, plan)
    expected_fields = {
        "schema_version": 1,
        "producer": "xobi-img.update_manifest",
        "contract": "frozen-localization-plan-v1",
        "manifest_id": manifest.get("manifest_id"),
        "task_id": item.get("task_id"),
        "source_sha256": item.get("source_sha256"),
        "attempts_at_registration": 0,
        "attempt_history_count_at_registration": 0,
    }
    for field, expected in expected_fields.items():
        if registration.get(field) != expected:
            errors.append(f"localization plan registration {field} does not match its frozen contract")
    registered_at = str(registration.get("registered_at") or "")
    try:
        registered_time = datetime.fromisoformat(registered_at)
        if registered_time.tzinfo is None:
            raise ValueError("timestamp lacks timezone")
    except ValueError:
        errors.append("localization plan registration timestamp is invalid")

    path_value = str(registration.get("path") or "")
    digest = str(registration.get("sha256") or "")
    if not path_value or not re.fullmatch(r"[0-9a-f]{64}", digest):
        errors.append("localization plan registration path or sha256 is invalid")
        return errors
    raw_plan_path = Path(path_value).expanduser().absolute()
    for candidate in (raw_plan_path, *raw_plan_path.parents):
        if is_link_or_junction(candidate):
            errors.append(
                f"frozen localization plan artifact must not traverse a symlink or junction: {candidate}"
            )
            return errors
    plan_path = raw_plan_path.resolve()
    work_root = Path(str(manifest.get("task_dir") or "")).resolve() / ".xobi" / "work"
    try:
        plan_path.relative_to(work_root)
    except ValueError:
        errors.append("frozen localization plan artifact must remain inside .xobi/work")
    if not plan_path.is_file():
        errors.append("frozen localization plan artifact is missing")
        return errors
    if sha256_file(plan_path) != digest:
        errors.append("frozen localization plan artifact hash changed after registration")
        return errors
    try:
        artifact_plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"frozen localization plan artifact is unreadable: {exc}")
    else:
        if artifact_plan != plan:
            errors.append("manifest localization_plan differs from its frozen artifact")
    return errors


def validate_localization_composition(
    item: dict[str, Any],
    manifest: dict[str, Any],
) -> list[str]:
    """Recompute the text-box composition instead of trusting provenance claims."""
    execution_stage = str(item.get("localization_execution_stage") or "")
    registration = item.get("localization_composition")
    if execution_stage in {"pure_generation", "pure_rebuild"}:
        return [] if registration is None else [
            f"{execution_stage} must not claim text-box composition provenance"
        ]
    if execution_stage != "reference_edit":
        return ["localization composition requires a recorded reference_edit execution stage"]
    if not isinstance(registration, dict):
        return ["reference_edit localization success requires frozen composition provenance"]

    errors: list[str] = []
    expected_registration = {
        "schema_version": 1,
        "producer": "xobi-img.update_manifest",
        "contract": "frozen-localization-composition-v1",
    }
    for field, expected in expected_registration.items():
        if registration.get(field) != expected:
            errors.append(f"localization composition registration {field} is invalid")
    artifact_value = str(registration.get("artifact_path") or "")
    artifact_digest = str(registration.get("artifact_sha256") or "")
    record = registration.get("record")
    if not artifact_value or not re.fullmatch(r"[0-9a-f]{64}", artifact_digest):
        errors.append("localization composition artifact path or sha256 is invalid")
        return errors
    if not isinstance(record, dict):
        errors.append("localization composition registration is missing its provenance record")
        return errors

    raw_artifact = Path(artifact_value).expanduser().absolute()
    for candidate_path in (raw_artifact, *raw_artifact.parents):
        if is_link_or_junction(candidate_path):
            errors.append(
                f"localization composition artifact must not traverse a symlink or junction: {candidate_path}"
            )
            return errors
    artifact = raw_artifact.resolve()
    work_root = Path(str(manifest.get("task_dir") or "")).resolve() / ".xobi" / "work"
    try:
        artifact.relative_to(work_root)
    except ValueError:
        errors.append("localization composition artifact must remain inside .xobi/work")
    if not artifact.is_file():
        errors.append("localization composition artifact is missing")
        return errors
    if sha256_file(artifact) != artifact_digest:
        errors.append("localization composition artifact hash changed after registration")
        return errors
    try:
        artifact_record = json.loads(artifact.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"localization composition artifact is unreadable: {exc}")
        return errors
    if artifact_record != record:
        errors.append("registered localization composition differs from its frozen artifact")

    if record.get("schema_version") != 1:
        errors.append("localization composition provenance schema_version is invalid")
    if record.get("producer") != "xobi-img.compose_localization":
        errors.append("localization composition provenance producer is invalid")
    if record.get("contract") != "text-bbox-composite-v1":
        errors.append("localization composition provenance contract is invalid")

    source = Path(str(item.get("source") or "")).resolve()
    localized = Path(str(item.get("localized_base") or "")).resolve()
    plan_registration = item.get("localization_plan_registration")
    if not isinstance(plan_registration, dict):
        errors.append("localization composition requires frozen plan registration")
        return errors
    plan_path = Path(str(plan_registration.get("path") or "")).resolve()
    candidate_value = str(record.get("raw_edit_candidate") or "")
    if not candidate_value:
        errors.append("localization composition provenance is missing raw_edit_candidate")
        return errors
    raw_candidate = Path(candidate_value).expanduser().absolute()
    for candidate_path in (raw_candidate, *raw_candidate.parents):
        if is_link_or_junction(candidate_path):
            errors.append(
                f"raw edit candidate must not traverse a symlink or junction: {candidate_path}"
            )
            return errors
    candidate = raw_candidate.resolve()
    try:
        candidate.relative_to(work_root)
    except ValueError:
        errors.append("raw edit candidate must remain inside .xobi/work")

    path_contracts = (
        ("source", source),
        ("localization_plan", plan_path),
        ("output", localized),
        ("raw_edit_candidate", candidate),
    )
    for field, expected_path in path_contracts:
        value = str(record.get(field) or "")
        if not value or canonical_path_key(Path(value)) != canonical_path_key(expected_path):
            errors.append(f"localization composition {field} path does not match its task stage")
    hash_contracts = (
        ("source_sha256", source, item.get("source_sha256")),
        ("localization_plan_sha256", plan_path, plan_registration.get("sha256")),
        ("output_sha256", localized, None),
        ("raw_edit_candidate_sha256", candidate, None),
    )
    for field, path, separately_expected in hash_contracts:
        if not path.is_file():
            errors.append(f"localization composition input is missing: {path}")
            continue
        actual_digest = sha256_file(path)
        if record.get(field) != actual_digest:
            errors.append(f"localization composition {field} does not match the current file")
        if separately_expected is not None and actual_digest != separately_expected:
            errors.append(f"localization composition {field} does not match the frozen task contract")
    if errors:
        return errors

    plan = item.get("localization_plan")
    if not isinstance(plan, dict):
        return ["localization composition requires the frozen localization plan"]
    try:
        with Image.open(source) as raw_source:
            source_image = ImageOps.exif_transpose(raw_source).convert("RGBA")
            source_image.load()
        with Image.open(candidate) as raw_candidate_image:
            candidate_image = ImageOps.exif_transpose(raw_candidate_image).convert("RGBA")
            candidate_image.load()
        with Image.open(localized) as raw_localized:
            localized_format = raw_localized.format
            localized_image = ImageOps.exif_transpose(raw_localized).convert("RGBA")
            localized_image.load()
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        return [f"localization composition could not read its images: {exc}"]
    if localized_format != "PNG":
        errors.append("localized_base produced by text-box composition must be a lossless PNG")
    if candidate_image.width * source_image.height != candidate_image.height * source_image.width:
        errors.append("raw edit candidate aspect ratio differs from the source")
        return errors
    raw_size = [candidate_image.width, candidate_image.height]
    candidate_resampled = candidate_image.size != source_image.size
    if candidate_resampled:
        candidate_image = candidate_image.resize(source_image.size, Image.Resampling.LANCZOS)
    mask, mask_metrics, mask_errors = _localization_text_edit_mask(
        source_image.size,
        plan.get("text_blocks"),
        plan.get("non_text_inventory"),
    )
    errors.extend(mask_errors)
    expected = Image.composite(candidate_image, source_image, mask)
    if not _images_pixel_equal(expected, localized_image):
        errors.append(
            "localized_base is not the recomputed source + raw candidate text-box composition"
        )
    if record.get("source_size") != [source_image.width, source_image.height]:
        errors.append("localization composition source_size is invalid")
    if record.get("raw_candidate_size") != raw_size:
        errors.append("localization composition raw_candidate_size is invalid")
    if record.get("candidate_resampled_to_source") is not candidate_resampled:
        errors.append("localization composition resample flag is invalid")
    if record.get("mask") != mask_metrics:
        errors.append("localization composition mask metrics differ from the frozen plan")
    return errors


def localization_visual_guard(
    item: dict[str, Any],
    manifest: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[str]]:
    """Reject localization candidates whose large-scale layout was regenerated or redesigned."""
    source_value = str(item.get("source") or "")
    candidate_value = str(item.get("localized_base") or "")
    if not source_value or not candidate_value:
        return None, ["localization visual guard requires source and localized_base"]
    source = Path(source_value).resolve()
    candidate = Path(candidate_value).resolve()
    if not source.is_file() or not candidate.is_file():
        return None, ["localization visual guard source or localized_base is missing"]
    plan = item.get("localization_plan")
    if not isinstance(plan, dict):
        return None, ["localization visual guard requires a per-image localization_plan"]
    plan_mode = str(plan.get("mode") or "")
    try:
        metrics = localization_layout_metrics(source, candidate)
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        return None, [f"localization visual guard could not read its images: {exc}"]

    execution_stage = str(item.get("localization_execution_stage") or "pure_generation")
    reference_edit_active = (
        plan_mode == LEGACY_REFERENCE_LOCALIZATION_MODE
        and execution_stage == "reference_edit"
    )
    if reference_edit_active:
        try:
            record, errors = localization_non_text_pixel_lock(source, candidate, plan)
        except (OSError, UnidentifiedImageError, ValueError) as exc:
            return None, [f"localization pixel lock could not read its images: {exc}"]
        record["layout_metrics"] = metrics
        record["execution_stage"] = execution_stage
        thresholds = dict(LOCALIZATION_TEXT_ONLY_LAYOUT_THRESHOLDS)
        record["layout_thresholds"] = thresholds
        layout_failures: list[str] = []
        if metrics["block_luma_correlation"] < thresholds["block_luma_correlation_min"]:
            layout_failures.append("large information blocks moved or changed order")
        if metrics["low_frequency_mae"] > thresholds["low_frequency_mae_max"]:
            layout_failures.append("large color/image regions changed")
        if metrics["low_frequency_rms"] > thresholds["low_frequency_rms_max"]:
            layout_failures.append("low-frequency composition drift is too large")
        if metrics["edge_rms"] > thresholds["edge_rms_max"]:
            layout_failures.append("non-text structure and boundaries drifted")
        if layout_failures:
            errors.append(
                "localization visual guard rejected a full-image redesign: "
                + "; ".join(layout_failures)
            )
        record["passed"] = not errors
        return record, errors

    thresholds = dict(LOCALIZATION_GUARD_THRESHOLDS)
    failures: list[str] = []
    if metrics["block_luma_correlation"] < thresholds["block_luma_correlation_min"]:
        failures.append("large information blocks moved or changed order")
    if metrics["low_frequency_mae"] > thresholds["low_frequency_mae_max"]:
        failures.append("large color/image regions changed")
    if metrics["low_frequency_rms"] > thresholds["low_frequency_rms_max"]:
        failures.append("low-frequency composition drift is too large")
    if metrics["edge_rms"] > thresholds["edge_rms_max"]:
        failures.append("non-text structure and boundaries drifted")
    record = {
        "schema_version": 1,
        "producer": "xobi-img.localization-guard",
        "contract": "translation-layout-lock-v1",
        "source": str(source),
        "source_sha256": sha256_file(source),
        "candidate": str(candidate),
        "candidate_sha256": sha256_file(candidate),
        "metrics": metrics,
        "thresholds": thresholds,
        "execution_stage": execution_stage,
        "passed": not failures,
    }
    errors = [
        "localization visual guard rejected a full-image redesign: " + "; ".join(failures)
    ] if failures else []
    return record, errors


def validate_localization_stage_derivation(
    item: dict[str, Any],
    manifest: dict[str, Any],
    plan: dict[str, Any],
) -> list[str]:
    """Prove that downstream delivery stages derive only from the guarded localized_base."""
    localized_value = str(item.get("localized_base") or "")
    if not localized_value:
        return ["localization stage derivation requires localized_base"]
    localized = Path(localized_value).resolve()
    if manifest.get("logo") and item.get("logo_decision") == "regenerate_for_conflict":
        stage_value = str(item.get("conflict_reference_base") or "")
        stage_name = "conflict_reference_base"
    elif manifest.get("logo"):
        stage_value = str(item.get("prepared_base") or "")
        stage_name = "prepared_base"
    else:
        stage_value = str(item.get("output") or "")
        stage_name = "final"
    if not stage_value:
        return [f"localization stage derivation requires {stage_name}"]
    stage = Path(stage_value).resolve()
    if not localized.is_file() or not stage.is_file():
        return [f"localized_base or {stage_name} is missing"]
    size_resample = plan.get("size_resample")
    requires_resample = isinstance(size_resample, dict) and size_resample.get("required") is True
    try:
        with Image.open(localized) as raw:
            icc_value = raw.info.get("icc_profile")
            icc_profile = bytes(icc_value) if isinstance(icc_value, (bytes, bytearray)) else None
            localized_image = ImageOps.exif_transpose(raw)
            localized_image.load()
            localized_image = localized_image.copy()
        with Image.open(stage) as raw:
            actual_stage = ImageOps.exif_transpose(raw).convert("RGBA")
            actual_stage.load()
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        return [f"localization stage derivation could not read its images: {exc}"]

    if not requires_resample and canonical_path_key(localized) == canonical_path_key(stage):
        return []

    if requires_resample:
        target_size = plan.get("target_size")
        if not (
            isinstance(target_size, list)
            and len(target_size) == 2
            and all(isinstance(value, int) and value > 0 for value in target_size)
        ):
            return ["size_resample requires a valid target_size for derivation validation"]
        deterministic_size = (target_size[0], target_size[1])
        derivation_label = "deterministic whole-canvas resample"
    else:
        deterministic_size = localized_image.size
        derivation_label = "deterministic same-size encoding"
    encoded_format = str(item.get("expected_format") or "").upper()
    try:
        from resample_image import ICC_FORMATS, prepare_mode, save_options

        prepared, icc_compatible = prepare_mode(localized_image, encoded_format)
        expected = prepared.resize(deterministic_size, Image.Resampling.LANCZOS)
        preserved_icc = icc_profile if icc_compatible and encoded_format in ICC_FORMATS else None
        encoded = io.BytesIO()
        expected.save(
            encoded,
            format=encoded_format,
            **save_options(encoded_format, preserved_icc),
        )
        encoded.seek(0)
        with Image.open(encoded) as decoded:
            deterministic_stage = decoded.convert("RGBA")
            deterministic_stage.load()
    except (ImportError, OSError, UnidentifiedImageError, ValueError) as exc:
        return [f"could not reproduce {derivation_label} of localized_base: {exc}"]
    if not _images_pixel_equal(deterministic_stage, actual_stage):
        return [
            f"{stage_name} is not the {derivation_label} of localized_base"
        ]
    return []


def is_link_or_junction(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def validate_auxiliary_json_path(path: Path, protected_paths: Iterable[Path] = ()) -> Path:
    """Resolve a helper JSON path without allowing it to replace task control data."""
    absolute = path.expanduser().absolute()
    for candidate in (absolute, *absolute.parents):
        if is_link_or_junction(candidate):
            raise ValueError(f"auxiliary JSON path must not traverse a symlink or junction: {candidate}")

    resolved = absolute.resolve()
    protected = {candidate.expanduser().resolve() for candidate in protected_paths}
    if resolved in protected:
        raise ValueError("auxiliary JSON must not overwrite an input, Logo, output, or protected task file")

    xobi_roots: set[Path] = set()
    for candidate in (absolute, resolved):
        parts = list(candidate.parts)
        folded = [part.casefold() for part in parts]
        for index, part in enumerate(folded):
            if part != ".xobi":
                continue
            if index + 1 >= len(parts) or folded[index + 1] != "work":
                raise ValueError("auxiliary JSON inside .xobi must be placed under .xobi/work")
            xobi_roots.add(Path(*parts[: index + 1]).resolve())
            remainder = folded[index + 2 :]
            if "task-state" in remainder:
                raise ValueError("auxiliary JSON must not overwrite task-state")
            stem_tokens = {
                token
                for token in re.split(r"[-_.\s]+", Path(parts[-1]).stem.casefold())
                if token
            }
            if stem_tokens & {"manifest", "report", "plan", "state", "lock", "families", "pilot"}:
                raise ValueError(
                    "auxiliary JSON must not overwrite manifest, report, plan, family, pilot, state, or lock data"
                )
    for xobi_root in xobi_roots:
        manifest_path = xobi_root / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot safely inspect task control paths in {manifest_path}: {exc}") from exc
        for field in ("logo_plan", "layout_families", "style_lock"):
            entry = manifest.get(field) or {}
            if isinstance(entry, dict) and entry.get("path"):
                if resolved == Path(str(entry["path"])).expanduser().resolve():
                    raise ValueError(f"auxiliary JSON must not overwrite registered {field} data")
    return resolved


def valid_task_id(task_id: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", task_id))


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def validate_output(item: dict[str, Any], manifest: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    output_value = str(item.get("output") or "")
    if not output_value:
        return None, ["output path is empty"]
    output = Path(output_value).resolve()
    task_dir = Path(str(manifest.get("task_dir") or output.parent)).resolve()
    metadata_dir = task_dir / ".xobi"
    if not _inside(output, task_dir) or _inside(output, metadata_dir):
        errors.append("output must be inside the task directory and outside .xobi")
    source_value = str(item.get("source") or "")
    if source_value and output == Path(source_value).resolve():
        errors.append("output must not overwrite the source")
    logo = manifest.get("logo") or {}
    if logo.get("source") and output == Path(str(logo["source"])).resolve():
        errors.append("output must not overwrite the Logo asset")
    if logo.get("normalized") and output == Path(str(logo["normalized"])).resolve():
        errors.append("output must not overwrite the normalized Logo asset")
    if not output.is_file():
        errors.append("output file does not exist")
        return None, errors
    try:
        with Image.open(output) as raw:
            image_format = raw.format or ""
            raw.verify()
        with Image.open(output) as raw:
            image = ImageOps.exif_transpose(raw)
            image.load()
            width, height = image.size
            has_alpha_channel = "A" in image.getbands() or (image.mode == "P" and "transparency" in image.info)
            has_transparency = False
            if has_alpha_channel:
                has_transparency = image.convert("RGBA").getchannel("A").getextrema()[0] < 255
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        errors.append(f"output is not a readable image: {exc}")
        return None, errors

    suffix_format = FORMAT_BY_SUFFIX.get(output.suffix.lower())
    if suffix_format and image_format.upper() != suffix_format:
        errors.append(
            f"output suffix {output.suffix.lower()} requires {suffix_format} but the encoded format is "
            f"{image_format or 'unknown'}"
        )
    expected_format = str(item.get("expected_format") or "").upper()
    if expected_format and image_format.upper() != expected_format:
        errors.append(f"output encoded format {image_format or 'unknown'} does not match expected {expected_format}")
    expected_alpha = item.get("expected_alpha")
    if expected_alpha is not None and has_transparency != bool(expected_alpha):
        expectation = "transparent pixels" if expected_alpha else "no transparent pixels"
        errors.append(f"output transparency does not match expected contract: {expectation}")

    expected_dimensions = item.get("expected_dimensions")
    if expected_dimensions and [width, height] != [int(expected_dimensions[0]), int(expected_dimensions[1])]:
        errors.append(
            f"output dimensions {width}x{height} do not match expected "
            f"{expected_dimensions[0]}x{expected_dimensions[1]}"
        )
    expected_ratio = item.get("expected_ratio")
    if expected_ratio and width > 0 and height > 0:
        target = float(expected_ratio[0]) / float(expected_ratio[1])
        actual = width / height
        if target <= 0 or abs(actual - target) / target > 0.01:
            errors.append(f"output ratio {width}:{height} does not match expected {expected_ratio[0]}:{expected_ratio[1]}")
    record = {
        "path": str(output),
        "bytes": output.stat().st_size,
        "width": width,
        "height": height,
        "format": image_format,
        "has_alpha": has_alpha_channel,
        "has_transparency": has_transparency,
        "sha256": sha256_file(output),
        "validated_at": now_iso(),
    }
    return record, errors


def quality_failures_for_stage(item: dict[str, Any], stage: str) -> list[dict[str, Any]]:
    """Return distinct, auditable quality failures for one execution stage."""
    failures: list[dict[str, Any]] = []
    seen_attempts: set[int] = set()
    history = item.get("attempt_history")
    if not isinstance(history, list):
        return failures
    for record in history:
        if not isinstance(record, dict):
            continue
        if record.get("failure_type") != "quality" or record.get("attempt_stage") != stage:
            continue
        if not str(record.get("error") or "").strip() or not str(record.get("record_id") or "").strip():
            continue
        try:
            attempt = int(record.get("attempt", 0) or 0)
        except (TypeError, ValueError):
            continue
        if attempt < 1 or attempt in seen_attempts:
            continue
        seen_attempts.add(attempt)
        failures.append(record)
    return failures


def quality_attempts_for_stage(item: dict[str, Any], stage: str) -> list[dict[str, Any]]:
    """Return distinct candidate-producing quality attempts, including accepted candidates."""
    records: list[dict[str, Any]] = []
    seen_attempts: set[int] = set()
    history = item.get("attempt_history")
    if not isinstance(history, list):
        return records
    for record in history:
        if not isinstance(record, dict) or record.get("attempt_stage") != stage:
            continue
        if record.get("failure_type") not in {None, "quality"}:
            continue
        try:
            attempt = int(record.get("attempt", 0) or 0)
        except (TypeError, ValueError):
            continue
        if attempt < 1 or attempt in seen_attempts:
            continue
        seen_attempts.add(attempt)
        records.append(record)
    return records


def _registered_logo_conflict_plan(
    item: dict[str, Any],
    manifest: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    registration = manifest.get("logo_plan")
    if not isinstance(registration, dict) or not registration.get("path") or not registration.get("sha256"):
        return None, None, ["logo_conflict requires a separately frozen logo_plan"]
    try:
        registered_revision = int(registration.get("revision_at_registration", -1))
        current_revision = int(manifest.get("revision", 0) or 0)
    except (TypeError, ValueError):
        registered_revision = -1
        current_revision = 0
    if registered_revision < 0 or registered_revision >= current_revision:
        errors.append("logo_conflict logo_plan must be frozen in a separate prior update")
    plan_path = Path(str(registration["path"])).resolve()
    if not plan_path.is_file():
        return None, None, errors + ["logo_conflict frozen logo_plan is missing"]
    try:
        digest = sha256_file(plan_path)
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, None, errors + [f"logo_conflict frozen logo_plan is unreadable: {exc}"]
    if digest != registration.get("sha256"):
        errors.append("logo_conflict frozen logo_plan hash changed")
    if not isinstance(plan, dict) or plan.get("schema_version") != 1:
        return None, None, errors + ["logo_conflict frozen logo_plan must be a schema_version 1 object"]
    plan_items = plan.get("items")
    matches = [
        entry
        for entry in plan_items
        if isinstance(plan_items, list)
        and isinstance(entry, dict)
        and entry.get("task_id") == item.get("task_id")
    ] if isinstance(plan_items, list) else []
    if len(matches) != 1:
        return None, plan, errors + ["logo_conflict frozen logo_plan must contain exactly one matching task item"]
    return matches[0], plan, errors


def _accepted_no_reference_base(
    item: dict[str, Any],
    manifest: dict[str, Any],
    before_attempt: int,
) -> tuple[Path | None, dict[str, Any] | None, list[str]]:
    mode = str(manifest.get("mode") or "")
    base_field = "localized_base" if mode == "localization" else "base_output"
    base_value = str(item.get(base_field) or "")
    history = item.get("attempt_history")
    if not base_value or not isinstance(history, list):
        return None, None, ["logo_conflict requires a prior accepted no-reference pure-generation base"]
    expected_stage: str | None = "pure_generation" if mode == "localization" else None
    candidates: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    for record in history:
        if not isinstance(record, dict):
            continue
        try:
            attempt = int(record.get("attempt", 0) or 0)
        except (TypeError, ValueError):
            continue
        accepted_base = record.get("accepted_base")
        if (
            attempt < 1
            or attempt >= before_attempt
            or record.get("failure_type") is not None
            or record.get("status") not in {"pending", "success"}
            or record.get("attempt_stage") != expected_stage
            or not isinstance(accepted_base, dict)
        ):
            continue
        if (
            accepted_base.get("kind") != base_field
            or accepted_base.get("policy") != "no_reference_pure_generation"
            or not str(accepted_base.get("path") or "")
            or not re.fullmatch(r"[0-9a-f]{64}", str(accepted_base.get("sha256") or ""))
        ):
            continue
        candidates.append((attempt, record, accepted_base))
    if not candidates:
        return None, None, ["logo_conflict requires a prior accepted no-reference pure-generation base"]
    _, record, accepted = max(candidates, key=lambda value: value[0])
    base = Path(base_value).resolve()
    accepted_path = Path(str(accepted["path"])).resolve()
    errors: list[str] = []
    if canonical_path_key(base) != canonical_path_key(accepted_path):
        errors.append(f"logo_conflict {base_field} does not match its accepted pure-generation attempt")
    if not base.is_file():
        errors.append(f"logo_conflict accepted {base_field} is missing")
    elif sha256_file(base) != accepted.get("sha256"):
        errors.append(f"logo_conflict accepted {base_field} hash changed")
    return (base if not errors else None), record, errors


def validate_logo_conflict_gate(
    item: dict[str, Any],
    manifest: dict[str, Any],
    *,
    before_attempt: int,
) -> list[str]:
    """Recompute every prerequisite for the sole reference-image exception."""
    errors: list[str] = []
    active_logo, active_logo_sha256, active_errors = active_logo_asset(manifest)
    if active_logo is None or active_logo_sha256 is None or active_errors:
        errors.append(
            "logo_conflict requires a valid active Logo: "
            + "; ".join(active_errors or ["active Logo is missing"])
        )
        return errors
    if item.get("logo_decision") != "regenerate_for_conflict":
        errors.append("logo_conflict requires logo_decision=regenerate_for_conflict")

    conflict_reference_value = str(item.get("conflict_reference_base") or "")
    conflict_reference = Path(conflict_reference_value).resolve() if conflict_reference_value else None
    if conflict_reference is None or not conflict_reference.is_file():
        errors.append("logo_conflict requires conflict_reference_base frozen before the attempt")

    plan_item, plan, plan_errors = _registered_logo_conflict_plan(item, manifest)
    errors.extend(plan_errors)
    geometry = item.get("logo_geometry")
    if not isinstance(geometry, dict):
        errors.append("logo_conflict requires separately frozen logo geometry")
    else:
        try:
            geometry_revision = int(geometry.get("revision_at_registration", -1))
            current_revision = int(manifest.get("revision", 0) or 0)
        except (TypeError, ValueError):
            geometry_revision = -1
            current_revision = 0
        if geometry_revision < 0 or geometry_revision >= current_revision:
            errors.append("logo_conflict logo geometry must be frozen in a separate prior update")
        artifact_value = str(geometry.get("artifact_path") or "")
        artifact = Path(artifact_value).resolve() if artifact_value else None
        if artifact is None or not artifact.is_file():
            errors.append("logo_conflict frozen logo geometry artifact is missing")
        else:
            artifact_sha256: str | None = None
            try:
                wrapper = json.loads(artifact.read_text(encoding="utf-8"))
                artifact_sha256 = sha256_file(artifact)
            except (OSError, json.JSONDecodeError) as exc:
                errors.append(f"logo_conflict frozen logo geometry is unreadable: {exc}")
                wrapper = None
            if artifact_sha256 is not None and artifact_sha256 != geometry.get("artifact_sha256"):
                errors.append("logo_conflict frozen logo geometry hash changed")
            if isinstance(wrapper, dict):
                if wrapper.get("producer") != "xobi-img.apply_logo" or wrapper.get("contract") != "locked-logo-v1":
                    errors.append("logo_conflict geometry must come from the locked apply_logo wrapper")
                if wrapper.get("logo_sha256") != active_logo_sha256:
                    errors.append("logo_conflict geometry Logo hash does not match the active Logo")
                wrapper_logo = str(wrapper.get("logo") or "")
                if not wrapper_logo or canonical_path_key(Path(wrapper_logo)) != canonical_path_key(active_logo):
                    errors.append("logo_conflict geometry does not use the active Logo")
                artifact_items = wrapper.get("items")
                geometry_source = str(geometry.get("source") or "")
                matches = [
                    entry
                    for entry in artifact_items
                    if isinstance(artifact_items, list)
                    and isinstance(entry, dict)
                    and str(entry.get("source") or "")
                    and geometry_source
                    and canonical_path_key(Path(str(entry["source"])))
                    == canonical_path_key(Path(geometry_source))
                ] if isinstance(artifact_items, list) else []
                if len(matches) != 1:
                    errors.append("logo_conflict geometry artifact must contain its frozen reference item")
                else:
                    for field, value in matches[0].items():
                        if geometry.get(field) != value:
                            errors.append("logo_conflict frozen geometry differs from its artifact")
                            break
        if conflict_reference is not None and geometry.get("source"):
            if canonical_path_key(Path(str(geometry["source"]))) != canonical_path_key(conflict_reference):
                errors.append("logo_conflict geometry source must be conflict_reference_base")

    if isinstance(plan, dict):
        plan_logo = plan.get("logo")
        if not isinstance(plan_logo, dict):
            errors.append("logo_conflict frozen logo_plan is missing its Logo record")
        else:
            plan_logo_source = str(plan_logo.get("source") or "")
            if not plan_logo_source or canonical_path_key(Path(plan_logo_source)) != canonical_path_key(active_logo):
                errors.append("logo_conflict logo_plan does not use the active Logo")
            if plan_logo.get("sha256") != active_logo_sha256:
                errors.append("logo_conflict logo_plan Logo hash does not match the active Logo")

    if isinstance(plan_item, dict) and isinstance(geometry, dict):
        if plan_item.get("decision") != "regenerate_for_conflict":
            errors.append("logo_conflict frozen logo_plan decision must be regenerate_for_conflict")
        item_source = str(item.get("source") or "")
        plan_source = str(plan_item.get("source") or "")
        if manifest.get("mode") == "generate":
            if plan_source:
                errors.append("generate logo_conflict logo_plan source must remain empty")
        elif not plan_source or canonical_path_key(Path(plan_source)) != canonical_path_key(Path(item_source)):
            errors.append("logo_conflict logo_plan source does not match the task")
        for field, geometry_field in (
            ("final_size", "canvas"),
            ("visible_bbox", "visible_bbox"),
            ("safe_zone", "safe_zone"),
        ):
            if plan_item.get(field) != geometry.get(geometry_field):
                errors.append(f"logo_conflict logo_plan {field} does not match frozen geometry")
        visible = geometry.get("visible_bbox")
        modules = plan_item.get("modules")
        declared_conflicts = plan_item.get("conflicts")
        expected_conflicts: set[str] = set()
        module_ids: set[str] = set()
        if not isinstance(modules, list) or not isinstance(visible, list) or len(visible) != 4:
            errors.append("logo_conflict requires real non-empty conflicts from explicit modules and geometry")
        else:
            for module in modules:
                if not isinstance(module, dict):
                    continue
                module_id = str(module.get("id") or "")
                bbox = module.get("bbox")
                if (
                    not module_id
                    or module_id in module_ids
                    or not isinstance(bbox, list)
                    or len(bbox) != 4
                    or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in bbox)
                ):
                    continue
                module_ids.add(module_id)
                if max(bbox[0], visible[0]) < min(bbox[2], visible[2]) and max(
                    bbox[1], visible[1]
                ) < min(bbox[3], visible[3]):
                    expected_conflicts.add(module_id)
            if (
                not expected_conflicts
                or not isinstance(declared_conflicts, list)
                or not declared_conflicts
                or len(declared_conflicts) != len(set(declared_conflicts))
                or set(declared_conflicts) != expected_conflicts
            ):
                errors.append("logo_conflict requires real non-empty conflicts matching visible Logo intersections")

    accepted_base, _, base_errors = _accepted_no_reference_base(
        item,
        manifest,
        before_attempt,
    )
    errors.extend(base_errors)
    if accepted_base is not None and conflict_reference is not None:
        if manifest.get("mode") == "localization":
            plan_data = item.get("localization_plan")
            if not isinstance(plan_data, dict):
                errors.append("localization logo_conflict requires its frozen pure-generation localization plan")
            else:
                probe = dict(item)
                probe["localization_execution_stage"] = "pure_generation"
                guard_record, guard_errors = localization_visual_guard(probe, manifest)
                errors.extend(guard_errors)
                if guard_record is not None and item.get("localization_validation") != guard_record:
                    errors.append("localization logo_conflict requires the accepted localized_base visual guard")
                errors.extend(validate_localization_stage_derivation(probe, manifest, plan_data))
        else:
            if canonical_path_key(accepted_base) != canonical_path_key(conflict_reference):
                errors.append("edit/generate logo_conflict requires conflict_reference_base to equal accepted base_output")
    return errors


def validate_logo_conflict_attempt_contract(
    item: dict[str, Any],
    manifest: dict[str, Any],
) -> list[str]:
    history = item.get("attempt_history")
    if not isinstance(history, list):
        return ["logo_conflict attempt validation requires an attempt_history list"]
    conflict_records = [
        record
        for record in history
        if isinstance(record, dict) and record.get("attempt_stage") == "logo_conflict"
    ]
    if not conflict_records:
        return []
    errors: list[str] = []
    seen_attempts: set[int] = set()
    total_attempts = int(item.get("attempts", 0) or 0)
    for record in conflict_records:
        try:
            attempt = int(record.get("attempt", 0) or 0)
        except (TypeError, ValueError):
            attempt = 0
        if attempt < 1 or attempt > total_attempts or attempt in seen_attempts:
            errors.append("logo_conflict attempts must use unique positive attempt numbers within the task total")
            continue
        seen_attempts.add(attempt)
        errors.extend(validate_logo_conflict_gate(item, manifest, before_attempt=attempt))
        try:
            attempt_time = datetime.fromisoformat(str(record.get("recorded_at") or ""))
            plan_time = datetime.fromisoformat(str((manifest.get("logo_plan") or {}).get("registered_at") or ""))
            geometry_time = datetime.fromisoformat(str((item.get("logo_geometry") or {}).get("registered_at") or ""))
            if plan_time.tzinfo is None or geometry_time.tzinfo is None or attempt_time.tzinfo is None:
                raise ValueError("timestamp lacks timezone")
            if plan_time >= attempt_time or geometry_time >= attempt_time:
                errors.append("logo_conflict plan and geometry must be frozen before its attempt")
        except (AttributeError, ValueError):
            errors.append("logo_conflict requires valid plan, geometry, and attempt timestamps")
    return errors


def reference_edit_quality_failures(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Return distinct, auditable reference-edit quality failures for one task."""
    return quality_failures_for_stage(item, "reference_edit")


def validate_localization_attempt_contract(
    item: dict[str, Any],
    manifest: dict[str, Any] | None = None,
) -> list[str]:
    """Require one monotonic history record per localization image call and budgeted success."""
    errors: list[str] = []
    try:
        attempts = int(item.get("attempts", 0) or 0)
    except (TypeError, ValueError):
        return ["localization attempts must be an integer"]
    history = item.get("attempt_history")
    if not isinstance(history, list):
        return ["localization attempt_history must be a list"]

    seen_ids: set[str] = set()
    seen_attempts: set[int] = set()
    parsed_records: list[tuple[int, dict[str, Any]]] = []
    parsed_times: list[tuple[int, datetime]] = []
    for index, record in enumerate(history, start=1):
        if not isinstance(record, dict):
            errors.append(f"localization attempt history record {index} must be an object")
            continue
        record_id = str(record.get("record_id") or "")
        if not record_id or record_id in seen_ids:
            errors.append("localization attempt history record_id is missing or duplicated")
        seen_ids.add(record_id)
        try:
            attempt = int(record.get("attempt", 0) or 0)
        except (TypeError, ValueError):
            attempt = 0
        if attempt < 1 or attempt > attempts or attempt in seen_attempts:
            errors.append("localization attempt history must use unique positive attempts within the task total")
        seen_attempts.add(attempt)
        parsed_records.append((attempt, record))
        failure_type = record.get("failure_type")
        if failure_type not in {None, "quality", "infrastructure"}:
            errors.append("localization attempt history has an invalid failure_type")
        if failure_type is not None and not str(record.get("error") or "").strip():
            errors.append("failed localization attempt history records require an error")
        if failure_type is not None and record.get("status") == "success":
            errors.append("failed localization attempts cannot have success status")
        if failure_type is None and str(record.get("error") or "").strip():
            errors.append("accepted localization attempts must not contain an error")
        if failure_type is None and record.get("status") not in {"pending", "success"}:
            errors.append("accepted localization attempts require pending or success status")
        if record.get("attempt_stage") not in {
            "pure_generation",
            "reference_edit",
            "pure_rebuild",
            "logo_conflict",
        }:
            errors.append("localization attempt history requires a valid attempt_stage")
        try:
            recorded_time = datetime.fromisoformat(str(record.get("recorded_at") or ""))
            if recorded_time.tzinfo is None:
                raise ValueError("timestamp lacks timezone")
            if recorded_time > datetime.now().astimezone() + timedelta(seconds=5):
                raise ValueError("timestamp is in the future")
        except ValueError:
            errors.append("localization attempt history timestamp is invalid")
        else:
            parsed_times.append((attempt, recorded_time))

    expected_attempts = set(range(1, attempts + 1))
    if seen_attempts != expected_attempts:
        errors.append("localization attempt history must record every image call exactly once")
    ordered_times = [value for _, value in sorted(parsed_times)]
    if any(later < earlier for earlier, later in zip(ordered_times, ordered_times[1:])):
        errors.append("localization attempt history timestamps must be monotonic")

    for stage in ("pure_generation", "reference_edit", "pure_rebuild", "logo_conflict"):
        if len(quality_attempts_for_stage(item, stage)) > 3:
            errors.append(f"localization {stage} quality attempt budget exceeds 3")
        infrastructure_attempts = {
            attempt
            for attempt, record in parsed_records
            if record.get("attempt_stage") == stage
            and record.get("failure_type") == "infrastructure"
        }
        if len(infrastructure_attempts) > 4:
            errors.append(f"localization {stage} infrastructure attempt budget exceeds 4")

    pure_attempts = sorted(
        attempt
        for attempt, record in parsed_records
        if record.get("attempt_stage") == "pure_rebuild"
    )
    if pure_attempts:
        first_pure = pure_attempts[0]
        if any(
            attempt > first_pure and record.get("attempt_stage") == "reference_edit"
            for attempt, record in parsed_records
        ):
            errors.append("localization cannot return to reference_edit after pure_rebuild starts")
        if manifest is not None:
            approval_valid, approval_errors = validate_pure_rebuild_approval(item, manifest)
            if not approval_valid:
                errors.append(
                    "localization pure_rebuild attempts require valid task-scoped approval: "
                    + "; ".join(approval_errors or ["approval is missing"])
                )
            approval = item.get("pure_rebuild_approval")
            try:
                approval_time = datetime.fromisoformat(str((approval or {}).get("recorded_at") or ""))
                pure_times = [
                    value
                    for attempt, value in parsed_times
                    if attempt in set(pure_attempts)
                ]
                if approval_time.tzinfo is None or any(value < approval_time for value in pure_times):
                    raise ValueError("pure rebuild predates approval")
            except (AttributeError, ValueError):
                errors.append("localization pure_rebuild attempt timestamp must follow its approval")

    policy = manifest.get("localization_policy") if manifest is not None else None
    policy_mode = str(policy.get("mode") or "") if isinstance(policy, dict) else ""
    if policy_mode == PURE_GENERATION_LOCALIZATION_MODE:
        invalid_new_stages = sorted({
            str(record.get("attempt_stage") or "")
            for _, record in parsed_records
            if record.get("attempt_stage") not in {"pure_generation", "logo_conflict"}
        })
        if invalid_new_stages:
            errors.append(
                "new pure-generation localization manifests cannot use legacy attempt stages: "
                + ", ".join(invalid_new_stages)
            )
        if item.get("pure_rebuild_approval") is not None:
            errors.append("new pure-generation localization manifests must not record rebuild approval")

    if item.get("status") == "success":
        execution_stage = str(item.get("localization_execution_stage") or "")
        matching_success: list[dict[str, Any]] = []
        for record in history:
            if not isinstance(record, dict):
                continue
            try:
                record_attempt = int(record.get("attempt", 0) or 0)
            except (TypeError, ValueError):
                continue
            if (
                record.get("status") == "success"
                and record.get("failure_type") is None
                and record_attempt == attempts
                and record.get("attempt_stage") in {execution_stage, "logo_conflict"}
            ):
                matching_success.append(record)
        if attempts < 1 or len(matching_success) != 1:
            errors.append("localization success requires one recorded positive final image attempt")
        if execution_stage in {"pure_generation", "reference_edit", "pure_rebuild"}:
            execution_attempts = quality_attempts_for_stage(item, execution_stage)
            if not execution_attempts or len(execution_attempts) > 3:
                errors.append(
                    f"localization {execution_stage} success requires one of at most three quality attempts"
                )
            if matching_success and matching_success[0].get("attempt_stage") == "logo_conflict":
                final_attempt = int(matching_success[0].get("attempt", 0) or 0)
                if not any(
                    int(record.get("attempt", 0) or 0) < final_attempt
                    and record.get("failure_type") is None
                    for record in execution_attempts
                ):
                    errors.append(
                        "localization logo_conflict success requires a prior accepted localization candidate"
                    )
    return errors


def validate_pure_rebuild_approval(
    item: dict[str, Any],
    manifest: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Validate a task-scoped pure-rebuild approval without trusting another task or manifest."""
    approval = item.get("pure_rebuild_approval")
    if approval is None:
        return False, []
    if not isinstance(approval, dict):
        return False, ["pure rebuild approval must be a task-scoped object"]

    errors: list[str] = []
    manifest_id = str(manifest.get("manifest_id") or "")
    task_id = str(item.get("task_id") or "")
    if approval.get("scope") != "task":
        errors.append("pure rebuild approval scope must be task")
    if not manifest_id or approval.get("manifest_id") != manifest_id:
        errors.append("pure rebuild approval does not match this manifest")
    if approval.get("task_id") != task_id:
        errors.append("pure rebuild approval does not match this task")
    if not item.get("source_sha256") or approval.get("source_sha256") != item.get("source_sha256"):
        errors.append("pure rebuild approval does not match the current source hash")
    if not str(approval.get("evidence") or "").strip():
        errors.append("pure rebuild approval evidence is empty")

    failures = reference_edit_quality_failures(item)
    if len(failures) < 3:
        errors.append("pure rebuild approval requires three reference-edit quality failures")
    failure_by_id = {str(record.get("record_id")): record for record in failures}
    approved_after = str(approval.get("approved_after_attempt_record_id") or "")
    matched_failure = failure_by_id.get(approved_after)
    if matched_failure is None:
        errors.append("pure rebuild approval is not bound to a recorded reference-edit failure")
    if len(failures) >= 3:
        ordered_failures = sorted(
            failures,
            key=lambda record: (
                int(record.get("attempt", 0) or 0),
                str(record.get("recorded_at") or ""),
            ),
        )
        third_failure_id = str(ordered_failures[2].get("record_id") or "")
        if approved_after != third_failure_id:
            errors.append("pure rebuild approval must be bound to the third reference-edit quality failure")

    try:
        approval_time = datetime.fromisoformat(str(approval.get("recorded_at") or ""))
        if approval_time.tzinfo is None:
            raise ValueError("timestamp lacks timezone")
        if approval_time > datetime.now().astimezone() + timedelta(seconds=5):
            raise ValueError("timestamp is in the future")
        if matched_failure is not None:
            failure_time = datetime.fromisoformat(str(matched_failure.get("recorded_at") or ""))
            if failure_time.tzinfo is None or approval_time < failure_time:
                raise ValueError("approval predates its triggering failure")
    except ValueError as exc:
        errors.append(f"pure rebuild approval timestamp is invalid: {exc}")
    return not errors, errors


def validate_logo_geometry_contract(
    item: dict[str, Any],
    manifest: dict[str, Any],
    output_record: dict[str, Any] | None,
    active_logo: Path,
    active_logo_sha256: str,
    prepared_base: Path | None,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Validate an apply_logo schema-v1 artifact and recompute its selected geometry."""
    errors: list[str] = []
    geometry = item.get("logo_geometry")
    required_top = {
        "artifact_path",
        "artifact_sha256",
        "wrapper",
        "source",
        "canvas",
        "scale",
        "logo_canvas",
        "visible_bbox",
        "safe_zone",
    }
    if not isinstance(geometry, dict) or not required_top.issubset(geometry):
        return None, ["Logo success requires a registered apply_logo schema-v1 geometry artifact"]
    wrapper_contract = geometry.get("wrapper")
    if not isinstance(wrapper_contract, dict):
        return None, ["Logo geometry requires its preserved wrapper contract"]
    if wrapper_contract.get("schema_version") != 1:
        errors.append("Logo geometry artifact schema_version must be 1")
    if wrapper_contract.get("producer") != "xobi-img.apply_logo" or wrapper_contract.get("contract") != "locked-logo-v1":
        errors.append("Logo geometry must come from the official locked apply_logo wrapper")
    locked_values = {
        "reference_short_side": LOGO_REFERENCE_SHORT_SIDE,
        "reference_box": list(LOGO_REFERENCE_BOX),
        "alpha_threshold": LOGO_ALPHA_THRESHOLD,
        "safe_padding": LOGO_SAFE_PADDING,
        "anchor_tolerance": LOGO_ANCHOR_TOLERANCE,
    }
    for field, expected in locked_values.items():
        if wrapper_contract.get(field) != expected:
            errors.append(f"Logo geometry {field} must use the locked standard {expected}")
    logo_value = str(wrapper_contract.get("logo") or "")
    if not logo_value or canonical_path_key(Path(logo_value)) != canonical_path_key(active_logo):
        errors.append("Logo geometry asset does not match the active manifest Logo")
    if wrapper_contract.get("logo_sha256") != active_logo_sha256:
        errors.append("Logo geometry hash does not match the active manifest Logo")
    try:
        review_required = logo_canvas_requires_review(active_logo, LOGO_ALPHA_THRESHOLD)
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        errors.append(f"active Logo opacity review could not be verified: {exc}")
        review_required = True
    if review_required and wrapper_contract.get("opaque_review_approved") is not True:
        errors.append("Logo geometry requires an explicit opaque/edge-reaching canvas review approval")
    elif not isinstance(wrapper_contract.get("opaque_review_approved"), bool):
        errors.append("Logo geometry opaque_review_approved must be boolean")

    artifact_value = str(geometry.get("artifact_path") or "")
    artifact_data: dict[str, Any] | None = None
    if not artifact_value:
        errors.append("Logo geometry artifact path is missing")
    else:
        artifact_path = Path(artifact_value).resolve()
        work_dir = Path(str(manifest.get("task_dir") or "")).resolve() / ".xobi" / "work"
        if not _inside(artifact_path, work_dir):
            errors.append("Logo geometry artifact must remain inside .xobi/work")
        if not artifact_path.is_file():
            errors.append("Logo geometry artifact is missing")
        else:
            try:
                artifact_digest = sha256_file(artifact_path)
                artifact_data = json.loads(artifact_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                errors.append(f"Logo geometry artifact is unreadable: {exc}")
            else:
                if artifact_digest != geometry.get("artifact_sha256"):
                    errors.append("Logo geometry artifact hash changed")
    if artifact_data is not None:
        if not isinstance(artifact_data, dict) or artifact_data.get("schema_version") != 1:
            errors.append("Logo geometry artifact must be a schema_version 1 wrapper")
        else:
            if artifact_data.get("producer") != "xobi-img.apply_logo" or artifact_data.get("contract") != "locked-logo-v1":
                errors.append("Logo geometry artifact is not the official locked apply_logo wrapper")
            for field, expected in {
                "logo": str(active_logo),
                "logo_sha256": active_logo_sha256,
                **locked_values,
            }.items():
                actual = artifact_data.get(field)
                if field == "logo":
                    if not actual or canonical_path_key(Path(str(actual))) != canonical_path_key(active_logo):
                        errors.append("Logo geometry wrapper uses the wrong active Logo path")
                elif actual != expected:
                    errors.append(f"Logo geometry wrapper {field} does not match the locked contract")
            if artifact_data.get("opaque_review_approved") != wrapper_contract.get("opaque_review_approved"):
                errors.append("Logo geometry opaque review approval differs from its artifact")

            allowed_sources: set[str] = set()
            for candidate in (
                item.get("source"),
                item.get("base_output"),
                item.get("localized_base"),
                item.get("conflict_reference_base"),
                item.get("prepared_base"),
            ):
                if candidate:
                    allowed_sources.add(canonical_path_key(Path(str(candidate))))
            canvas = [
                int((output_record or {}).get("width", 0) or 0),
                int((output_record or {}).get("height", 0) or 0),
            ]
            artifact_items = artifact_data.get("items")
            matches: list[dict[str, Any]] = []
            if isinstance(artifact_items, list):
                for entry in artifact_items:
                    if not isinstance(entry, dict) or not entry.get("source"):
                        continue
                    if canonical_path_key(Path(str(entry["source"]))) in allowed_sources and entry.get("canvas") == canvas:
                        matches.append(entry)
            else:
                errors.append("Logo geometry wrapper items must be a list")
            if len(matches) != 1:
                errors.append("Logo geometry wrapper must contain exactly one frozen base and canvas match")
            elif any(geometry.get(key) != value for key, value in matches[0].items()):
                errors.append("registered Logo geometry differs from the selected wrapper item")

    canvas_value = geometry.get("canvas")
    if not (
        isinstance(canvas_value, list)
        and len(canvas_value) == 2
        and all(isinstance(value, int) and value > 0 for value in canvas_value)
    ):
        errors.append("Logo geometry canvas must contain two positive integers")
        return geometry, errors
    if output_record and canvas_value != [output_record.get("width"), output_record.get("height")]:
        errors.append("Logo geometry canvas does not match the final output dimensions")
    geometry_source = str(geometry.get("source") or "")
    allowed_source_values = {
        canonical_path_key(Path(str(value)))
        for value in (
            item.get("source"),
            item.get("base_output"),
            item.get("localized_base"),
            item.get("conflict_reference_base"),
            item.get("prepared_base"),
        )
        if value
    }
    if not geometry_source or canonical_path_key(Path(geometry_source)) not in allowed_source_values:
        errors.append("Logo geometry source does not match a frozen base stage")

    try:
        _, expected_geometry = standard_logo_overlay_and_geometry(
            active_logo,
            (int(canvas_value[0]), int(canvas_value[1])),
        )
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        errors.append(f"locked Logo geometry could not be recomputed: {exc}")
    else:
        for key, expected in expected_geometry.items():
            if geometry.get(key) != expected:
                errors.append(f"Logo geometry {key} does not match the locked active-Logo calculation")
    if prepared_base is not None and output_record:
        errors.extend(
            validate_logo_overlay_pixels(
                prepared_base,
                Path(str(item.get("output") or "")).resolve(),
                active_logo,
                str(output_record.get("format") or ""),
            )
        )
    return geometry, errors


def validate_layout_families_contract(
    manifest: dict[str, Any],
    plan_data: dict[str, Any],
    current_item: dict[str, Any],
    current_output: dict[str, Any] | None,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Validate layout_families schema v1 and bind every family to its real pilot and lock."""
    errors: list[str] = []
    registered = manifest.get("layout_families")
    if not isinstance(registered, dict) or not registered.get("path") or not registered.get("sha256"):
        return {}, ["grouped Logo success requires a registered layout_families file"]
    path = Path(str(registered["path"])).resolve()
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        digest = sha256_file(path)
    except (OSError, json.JSONDecodeError) as exc:
        return {}, [f"registered layout_families is unreadable: {exc}"]
    if digest != registered.get("sha256"):
        errors.append("registered layout_families hash changed")
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        return {}, errors + ["layout_families must be a schema_version 1 object"]
    families = data.get("families")
    if not isinstance(families, list) or not families:
        return {}, errors + ["layout_families families must be a non-empty list"]

    task_by_id = {
        str(entry.get("task_id")): entry
        for entry in manifest.get("items", [])
        if isinstance(entry, dict)
    }
    plan_items = plan_data.get("items")
    plan_by_id = {
        str(entry.get("task_id")): entry
        for entry in plan_items
        if isinstance(plan_items, list) and isinstance(entry, dict)
    } if isinstance(plan_items, list) else {}
    required_lock_fields = {
        "title_direction",
        "module_anchor",
        "type_hierarchy",
        "product_scale_range",
        "module_spacing",
    }
    family_by_id: dict[str, dict[str, Any]] = {}
    seen_members: set[str] = set()
    for family in families:
        if not isinstance(family, dict):
            errors.append("layout_families entries must be objects")
            continue
        family_id = str(family.get("family_id") or "")
        if not family_id or family_id == "ungrouped" or family_id in family_by_id:
            errors.append("layout_families family_id must be unique, non-empty, and not ungrouped")
            continue
        family_by_id[family_id] = family
        members = family.get("members")
        if not isinstance(members, list) or not members or any(not isinstance(value, str) or not value for value in members):
            errors.append(f"layout family {family_id} members must be a non-empty task-id list")
            continue
        if len(set(members)) != len(members):
            errors.append(f"layout family {family_id} contains duplicate members")
        for member in members:
            if member in seen_members:
                errors.append(f"layout family member {member} appears in more than one family")
            seen_members.add(member)
            task = task_by_id.get(member)
            plan = plan_by_id.get(member)
            if task is None or plan is None:
                errors.append(f"layout family {family_id} member {member} is missing from manifest or logo_plan")
            elif task.get("family_id") not in {None, family_id} or plan.get("family_id") != family_id:
                errors.append(f"layout family {family_id} member {member} has a mismatched family_id")

        pilot_id = str(family.get("pilot_task_id") or "")
        if pilot_id not in members:
            errors.append(f"layout family {family_id} pilot_task_id must identify a member")
        requires_pilot = family.get("requires_pilot")
        if requires_pilot is not True:
            errors.append(f"layout family {family_id} with conflict regeneration must require a pilot")
        if family.get("pilot_approved") is not True:
            errors.append(f"layout family {family_id} pilot must be approved")
        pilot_plan = plan_by_id.get(pilot_id)
        if not isinstance(pilot_plan, dict) or pilot_plan.get("decision") != "regenerate_for_conflict":
            errors.append(f"layout family {family_id} pilot must itself be regenerate_for_conflict")
        elif not pilot_plan.get("base_approved") or not pilot_plan.get("final_approved"):
            errors.append(f"layout family {family_id} pilot approvals are incomplete")
        expected_members = {
            task_id for task_id, plan in plan_by_id.items() if plan.get("family_id") == family_id
        }
        if set(members) != expected_members:
            errors.append(f"layout family {family_id} members do not exactly match logo_plan")
        for task_id in members:
            plan = plan_by_id.get(task_id) or {}
            if plan.get("decision") == "regenerate_for_conflict" and plan.get("family_reference") != pilot_id:
                errors.append(f"layout family {family_id} member {task_id} does not reference the frozen pilot")

        pilot_task = task_by_id.get(pilot_id) or {}
        pilot_digest = None
        if pilot_id == current_item.get("task_id") and current_output:
            pilot_digest = current_output.get("sha256")
        else:
            pilot_validation = pilot_task.get("output_validation")
            if pilot_task.get("status") == "success" and isinstance(pilot_validation, dict):
                pilot_digest = pilot_validation.get("sha256")
        if not pilot_digest or family.get("pilot_output_sha256") != pilot_digest:
            errors.append(f"layout family {family_id} pilot_output_sha256 is missing or stale")
        lock = family.get("lock")
        if not isinstance(lock, dict) or not required_lock_fields.issubset(lock):
            errors.append(f"layout family {family_id} lock is incomplete")
        elif any(value is None or value == "" or value == [] or value == {} for value in (lock[field] for field in required_lock_fields)):
            errors.append(f"layout family {family_id} lock fields must be non-empty")
        if not isinstance(family.get("variants"), list):
            errors.append(f"layout family {family_id} variants must be a list")
    return family_by_id, errors


def recompute_logo_relocation_validation(
    item: dict[str, Any],
    manifest: dict[str, Any],
    plan_data: dict[str, Any] | None = None,
    geometry: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Recompute deterministic evidence that every Logo-conflict module actually moved."""
    if item.get("logo_decision") != "regenerate_for_conflict":
        return None, []
    errors: list[str] = []
    if plan_data is None:
        registration = manifest.get("logo_plan") or {}
        if not isinstance(registration, dict) or not registration.get("path"):
            return None, ["Logo relocation validation requires a registered logo_plan"]
        try:
            plan_data = json.loads(Path(str(registration["path"])).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return None, [f"Logo relocation could not read registered logo_plan: {exc}"]
    if not isinstance(plan_data, dict):
        return None, ["Logo relocation validation requires a logo_plan object"]
    if geometry is None:
        raw_geometry = item.get("logo_geometry")
        geometry = raw_geometry if isinstance(raw_geometry, dict) else None
    if not isinstance(geometry, dict):
        return None, ["Logo relocation validation requires locked Logo geometry"]
    prepared_value = str(item.get("prepared_base") or "")
    if not prepared_value:
        return None, ["Logo relocation validation requires prepared_base"]
    reference_value = str(item.get("conflict_reference_base") or "")
    mode = str(manifest.get("mode") or "")
    if not reference_value:
        if mode in {"localization", "generate"}:
            return None, [f"{mode} conflict regeneration requires conflict_reference_base"]
        reference_value = str(item.get("source") or "")
    if not reference_value:
        return None, ["Logo relocation validation requires a pre-conflict reference image"]
    reference = Path(reference_value).resolve()
    prepared = Path(prepared_value).resolve()
    if not reference.is_file() or not prepared.is_file():
        return None, ["Logo relocation conflict_reference_base/source or prepared_base is missing"]
    try:
        from logo_relocation import validate_logo_relocation

        record, relocation_errors = validate_logo_relocation(
            item,
            plan_data,
            reference,
            prepared,
            geometry,
        )
    except (ImportError, OSError, UnidentifiedImageError, ValueError) as exc:
        return None, [f"Logo relocation validation failed to run: {exc}"]
    errors.extend(relocation_errors)
    return record, errors


def validate_item_contract(
    item: dict[str, Any],
    manifest: dict[str, Any],
    output_record: dict[str, Any] | None = None,
) -> list[str]:
    if item.get("status") != "success":
        return []
    errors: list[str] = []
    if manifest.get("mode") == "localization":
        errors.extend(validate_localization_plan_registration(item, manifest))
        plan = item.get("localization_plan")
        if not isinstance(plan, dict):
            errors.append("localization success requires a per-image localization_plan")
        else:
            raw_policy = manifest.get("localization_policy")
            policy = raw_policy if isinstance(raw_policy, dict) else {}
            policy_mode = str(policy.get("mode") or "")
            pure_generation_plan = policy_mode == PURE_GENERATION_LOCALIZATION_MODE
            expected_plan_mode = (
                PURE_GENERATION_LOCALIZATION_MODE
                if pure_generation_plan
                else LEGACY_REFERENCE_LOCALIZATION_MODE
            )
            required = {
                "task_id",
                "mode",
                "source",
                "source_sha256",
                "source_size",
                "target_language",
                "output_ratio",
                "target_size",
                "size_resample",
                "ratio_adaptation",
                "text_blocks",
                "non_text_inventory",
            }
            if not pure_generation_plan:
                required.add("pure_rebuild_allowed")
            missing = sorted(required - set(plan))
            if missing:
                errors.append("localization_plan is missing fields: " + ", ".join(missing))
            if str(plan.get("task_id") or "") != str(item.get("task_id") or ""):
                errors.append("localization_plan task_id does not match the manifest item")
            if canonical_path_key(Path(str(plan.get("source") or ""))) != canonical_path_key(
                Path(str(item.get("source") or ""))
            ):
                errors.append("localization_plan source does not match the manifest item")
            if plan.get("source_sha256") != item.get("source_sha256"):
                errors.append("localization_plan source hash does not match preflight")
            if plan.get("source_size") != [item.get("width"), item.get("height")]:
                errors.append("localization_plan source_size does not match preflight")
            if str(plan.get("target_language") or "").casefold() != str(
                manifest.get("target_language") or ""
            ).casefold():
                errors.append("localization_plan target language does not match the manifest")
            if not isinstance(plan.get("text_blocks"), list) or not isinstance(plan.get("non_text_inventory"), list):
                errors.append("localization_plan text_blocks and non_text_inventory must be lists")
            elif isinstance(plan.get("text_blocks"), list):
                for index, block in enumerate(plan["text_blocks"], start=1):
                    if not isinstance(block, dict):
                        errors.append(f"localization_plan text block {index} must be an object")
                        continue
                    block_required = {
                        "id",
                        "source_bbox",
                        "target_bbox",
                        "source",
                        "translation",
                        "target_text_source",
                        "role",
                        "text_layout_adaptation",
                    }
                    missing_block = sorted(block_required - set(block))
                    if missing_block:
                        errors.append(
                            f"localization_plan text block {index} is missing fields: " + ", ".join(missing_block)
                        )
                        continue
                    if not str(block.get("id") or "").strip():
                        errors.append(f"localization_plan text block {index} has an empty id")
                    if not isinstance(block.get("source"), str) or not block["source"].strip():
                        errors.append(f"localization_plan text block {index} has empty source text")
                    if not isinstance(block.get("translation"), str) or not block["translation"].strip():
                        errors.append(f"localization_plan text block {index} has empty translated text")
                    target_text_source = block.get("target_text_source")
                    if not isinstance(target_text_source, str) or target_text_source not in {
                        "translated",
                        "user_exact",
                    }:
                        errors.append(f"localization_plan text block {index} has invalid target_text_source")
                    elif target_text_source == "user_exact":
                        requested = block.get("requested_target_text")
                        if not isinstance(requested, str) or not requested.strip():
                            errors.append(
                                f"localization_plan text block {index} user_exact text requires requested_target_text"
                            )
                        elif block.get("translation") != requested:
                            errors.append(
                                f"localization_plan text block {index} must preserve user_exact target text verbatim"
                            )
                    elif block.get("requested_target_text") is not None and block.get("requested_target_text") != "":
                        errors.append(
                            f"localization_plan text block {index} requested_target_text requires user_exact"
                        )
                    if not isinstance(block.get("role"), str) or not block["role"].strip():
                        errors.append(f"localization_plan text block {index} has an empty role")
                    source_bbox = block.get("source_bbox")
                    target_bbox = block.get("target_bbox")
                    layout = block.get("text_layout_adaptation")
                    boxes_valid = all(
                        isinstance(box, list)
                        and len(box) == 4
                        and all(isinstance(value, (int, float)) and math.isfinite(value) for value in box)
                        and 0 <= box[0] < box[2]
                        and 0 <= box[1] < box[3]
                        for box in (source_bbox, target_bbox)
                    )
                    if not boxes_valid:
                        errors.append(f"localization_plan text block {index} has an invalid bbox")
                    else:
                        source_width = int(item.get("width", 0) or 0)
                        source_height = int(item.get("height", 0) or 0)
                        if source_bbox[2] > source_width or source_bbox[3] > source_height:
                            errors.append(f"localization_plan text block {index} source_bbox is outside the source")
                        adaptation = plan.get("ratio_adaptation")
                        if not (isinstance(adaptation, dict) and adaptation.get("required")) and (
                            target_bbox[2] > source_width or target_bbox[3] > source_height
                        ):
                            errors.append(f"localization_plan text block {index} target_bbox is outside the base")
                    if not isinstance(layout, dict):
                        errors.append(f"localization_plan text block {index} requires text_layout_adaptation")
                    else:
                        if not isinstance(layout.get("required"), bool):
                            errors.append(
                                f"localization_plan text block {index} text_layout_adaptation.required must be boolean"
                            )
                        box_changed = source_bbox != target_bbox
                        if bool(layout.get("required")) != box_changed:
                            errors.append(
                                f"localization_plan text block {index} text_layout_adaptation does not match its bboxes"
                            )
                        if box_changed and not str(layout.get("reason") or "").strip():
                            errors.append(f"localization_plan text block {index} requires an expansion reason")
                block_ids = [
                    str(block.get("id"))
                    for block in plan["text_blocks"]
                    if isinstance(block, dict) and str(block.get("id") or "").strip()
                ]
                if len(block_ids) != len(set(block_ids)):
                    errors.append("localization_plan text block ids must be unique")
                inventory = plan.get("non_text_inventory")
                if not inventory or any(not isinstance(value, dict) for value in inventory):
                    errors.append(
                        "localization_plan non_text_inventory must contain structured non-text items"
                    )
            size_resample = plan.get("size_resample")
            ratio_adaptation = plan.get("ratio_adaptation")
            if not isinstance(size_resample, dict) or not isinstance(ratio_adaptation, dict):
                errors.append("localization_plan size_resample and ratio_adaptation must be objects")
            else:
                if not isinstance(size_resample.get("required"), bool):
                    errors.append("localization_plan size_resample.required must be boolean")
                if not isinstance(ratio_adaptation.get("required"), bool):
                    errors.append("localization_plan ratio_adaptation.required must be boolean")
                try:
                    plan_dimensions, plan_ratio = expected_geometry(
                        str(plan.get("output_ratio") or ""),
                        int(item.get("width", 0) or 0),
                        int(item.get("height", 0) or 0),
                    )
                except ValueError as exc:
                    errors.append(f"localization_plan output_ratio is invalid: {exc}")
                else:
                    if plan_dimensions != item.get("expected_dimensions") or plan_ratio != item.get("expected_ratio"):
                        errors.append("localization_plan output_ratio does not match the manifest output geometry")
                expected_dimensions = item.get("expected_dimensions")
                target_size = plan.get("target_size")
                if expected_dimensions:
                    if target_size != expected_dimensions:
                        errors.append("localization_plan target_size does not match expected dimensions")
                    target_width, target_height = int(expected_dimensions[0]), int(expected_dimensions[1])
                    source_width = int(item.get("width", 0) or 0)
                    source_height = int(item.get("height", 0) or 0)
                    same_ratio = target_width * source_height == target_height * source_width
                    same_size = [source_width, source_height] == [target_width, target_height]
                else:
                    if target_size is not None:
                        errors.append("localization_plan target_size must be null without exact output dimensions")
                    source_width = int(item.get("width", 0) or 0)
                    source_height = int(item.get("height", 0) or 0)
                    expected_ratio = item.get("expected_ratio") or [source_width, source_height]
                    same_ratio = int(expected_ratio[0]) * source_height == int(expected_ratio[1]) * source_width
                    same_size = True
                needs_size_resample = bool(expected_dimensions) and same_ratio and not same_size
                needs_ratio_adaptation = not same_ratio
                if pure_generation_plan:
                    if size_resample.get("required") is not False:
                        errors.append("pure-generation localization size_resample.required must be false")
                    if size_resample.get("method") not in {None, ""}:
                        errors.append("pure-generation localization size_resample.method must be empty")
                elif bool(size_resample.get("required")) != needs_size_resample:
                    errors.append("localization_plan size_resample.required does not match the geometry change")
                if bool(ratio_adaptation.get("required")) != needs_ratio_adaptation:
                    errors.append("localization_plan ratio_adaptation.required does not match the geometry change")
                if not pure_generation_plan and needs_size_resample and not str(size_resample.get("method") or "").strip():
                    errors.append("localization_plan size_resample method is required")
                if needs_ratio_adaptation and not (
                    isinstance(ratio_adaptation.get("allowed_changes"), list)
                    and ratio_adaptation.get("allowed_changes")
                ):
                    errors.append("localization_plan ratio_adaptation requires explicit allowed_changes")
            if not isinstance(raw_policy, dict):
                errors.append("localization manifest requires a localization_policy object")
            approval_valid, approval_errors = validate_pure_rebuild_approval(item, manifest)
            if item.get("pure_rebuild_approval") is not None:
                if pure_generation_plan:
                    errors.append("new pure-generation localization must not record rebuild approval")
                else:
                    errors.extend(approval_errors)
            execution_stage = item.get("localization_execution_stage")
            allowed_execution_stages = (
                {"pure_generation"}
                if pure_generation_plan
                else {"reference_edit", "pure_rebuild"}
            )
            if execution_stage not in allowed_execution_stages:
                errors.append(
                    "localization success requires an execution stage allowed by its policy: "
                    + ", ".join(sorted(allowed_execution_stages))
                )
            elif execution_stage == "pure_rebuild" and not approval_valid:
                errors.append("pure_rebuild localization success requires valid task-scoped approval")
            errors.extend(validate_localization_composition(item, manifest))
            budget_contracts = (
                (("quality_attempts", "localization pure-generation quality attempt budget must be 3"),)
                if pure_generation_plan
                else (
                    ("reference_edit_quality_attempts", "localization reference-edit quality attempt budget must be 3"),
                    (
                        "pure_rebuild_quality_attempts_after_approval",
                        "localization pure-rebuild quality attempt budget must be 3 after approval",
                    ),
                )
            )
            for field, message in budget_contracts:
                try:
                    valid_budget = int(policy.get(field, 0) or 0) == 3
                except (TypeError, ValueError):
                    valid_budget = False
                if not valid_budget:
                    errors.append(message)
            plan_mode = str(plan.get("mode") or "")
            if plan_mode != expected_plan_mode:
                errors.append(
                    f"the frozen localization_plan mode must remain {expected_plan_mode}"
                )
            if pure_generation_plan:
                rebuild_flag = plan.get("pure_rebuild_allowed")
                if rebuild_flag is not None and rebuild_flag is not False:
                    errors.append("pure-generation localization must not carry a rebuild approval flag")
            elif not isinstance(plan.get("pure_rebuild_allowed"), bool):
                errors.append("localization_plan pure_rebuild_allowed must be boolean")
            elif plan["pure_rebuild_allowed"]:
                errors.append("the frozen localization_plan cannot pre-authorize pure rebuild")
            localized_base_value = item.get("localized_base")
            if not localized_base_value:
                errors.append("localization success requires a recorded localized_base")
            else:
                localized_base = Path(str(localized_base_value)).resolve()
                if canonical_path_key(localized_base) == canonical_path_key(Path(str(item.get("source") or ""))):
                    errors.append("localized_base must be a new candidate path, not the read-only source")
                if not localized_base.is_file():
                    errors.append("localized_base file is missing")
                else:
                    try:
                        with Image.open(localized_base) as raw_localized:
                            localized_format = raw_localized.format
                            localized = ImageOps.exif_transpose(raw_localized)
                            localized.load()
                            localized_size = [localized.width, localized.height]
                    except (UnidentifiedImageError, OSError, ValueError) as exc:
                        errors.append(f"localized_base is not a readable image: {exc}")
                    else:
                        if localized_format != "PNG":
                            errors.append("localization localized_base must be a lossless PNG")
                        if pure_generation_plan:
                            expected_dimensions = item.get("expected_dimensions")
                            if expected_dimensions:
                                if localized_size != expected_dimensions:
                                    errors.append(
                                        "pure-generation localized_base must match the exact target dimensions"
                                    )
                            else:
                                expected_ratio = item.get("expected_ratio") or [
                                    item.get("width"),
                                    item.get("height"),
                                ]
                                if (
                                    localized_size[0] * int(expected_ratio[1])
                                    != localized_size[1] * int(expected_ratio[0])
                                ):
                                    errors.append(
                                        "pure-generation localized_base must match the target aspect ratio"
                                    )
                        else:
                            expected_base_size = [item.get("width"), item.get("height")]
                            if localized_size != expected_base_size:
                                errors.append("localization localized_base must keep the source pixel dimensions")
            guard_record, guard_errors = localization_visual_guard(item, manifest)
            errors.extend(guard_errors)
            registered_guard = item.get("localization_validation")
            if guard_record is not None and registered_guard != guard_record:
                errors.append("localization success requires the current strict visual-guard record")
            if isinstance(plan, dict):
                errors.extend(validate_localization_stage_derivation(item, manifest, plan))

    if manifest.get("logo"):
        logo_plan = manifest.get("logo_plan") or {}
        if not isinstance(logo_plan, dict) or not logo_plan.get("path") or not logo_plan.get("sha256"):
            errors.append("Logo success requires a registered logo_plan file")
            plan_data = None
        else:
            plan_path = Path(str(logo_plan["path"])).resolve()
            try:
                plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
                plan_digest = sha256_file(plan_path)
            except (OSError, json.JSONDecodeError) as exc:
                errors.append(f"registered logo_plan is unreadable: {exc}")
                plan_data = None
            else:
                if plan_digest != logo_plan.get("sha256"):
                    errors.append("registered logo_plan hash changed")
                if not isinstance(plan_data, dict) or plan_data.get("schema_version") != 1:
                    errors.append("logo_plan must be a schema_version 1 object")

        active_logo, active_logo_sha256, active_errors = active_logo_asset(manifest)
        errors.extend(active_errors)
        decision = item.get("logo_decision")
        if decision not in {"direct_overlay", "regenerate_for_conflict"}:
            errors.append("Logo success requires a valid logo_decision")
        prepared_base: Path | None = None
        if not item.get("prepared_base"):
            errors.append("Logo success requires a recorded prepared_base for source/prepared/final review")
        else:
            prepared_base = Path(str(item["prepared_base"])).resolve()
            if canonical_path_key(prepared_base) == canonical_path_key(Path(str(item.get("output") or ""))):
                errors.append("Logo prepared_base must differ from the final Logo output")
            if decision == "regenerate_for_conflict" and canonical_path_key(prepared_base) == canonical_path_key(
                Path(str(item.get("source") or ""))
            ):
                errors.append("a conflict-regenerated Logo prepared_base must differ from the source")
            if not prepared_base.is_file():
                errors.append("Logo prepared_base file is missing")
                prepared_base = None
            else:
                try:
                    with Image.open(prepared_base) as base:
                        base = ImageOps.exif_transpose(base)
                        base.load()
                        prepared_size = [base.width, base.height]
                except (UnidentifiedImageError, OSError, ValueError) as exc:
                    errors.append(f"Logo prepared_base is not a readable image: {exc}")
                    prepared_base = None
                else:
                    if output_record and prepared_size != [output_record.get("width"), output_record.get("height")]:
                        errors.append("Logo prepared_base dimensions do not match the final output")
                    source_value = str(item.get("source") or "")
                    if decision == "regenerate_for_conflict" and source_value:
                        source_path = Path(source_value).resolve()
                        if source_path.is_file():
                            try:
                                with Image.open(source_path) as source_image:
                                    source_pixels = ImageOps.exif_transpose(source_image).convert("RGBA")
                                    source_pixels.load()
                                with Image.open(prepared_base) as prepared_image:
                                    prepared_pixels = ImageOps.exif_transpose(prepared_image).convert("RGBA")
                                    prepared_pixels.load()
                            except (OSError, UnidentifiedImageError, ValueError) as exc:
                                errors.append(f"could not compare regenerated prepared_base with source: {exc}")
                            else:
                                if _images_pixel_equal(source_pixels, prepared_pixels):
                                    errors.append(
                                        "regenerate_for_conflict prepared_base is pixel-identical to source; "
                                        "no conflict module was moved"
                                    )
        geometry: dict[str, Any] | None = None
        if active_logo is not None and active_logo_sha256 is not None:
            geometry, geometry_errors = validate_logo_geometry_contract(
                item,
                manifest,
                output_record,
                active_logo,
                active_logo_sha256,
                prepared_base,
            )
            errors.extend(geometry_errors)
        if not item.get("family_id"):
            errors.append("Logo success requires a layout family or explicit ungrouped family_id")

        if isinstance(plan_data, dict) and active_logo is not None and active_logo_sha256 is not None:
            plan_logo = plan_data.get("logo")
            if not isinstance(plan_logo, dict):
                errors.append("logo_plan Logo record must be an object")
            else:
                plan_logo_source = str(plan_logo.get("source") or "")
                if not plan_logo_source or canonical_path_key(Path(plan_logo_source)) != canonical_path_key(active_logo):
                    errors.append("logo_plan Logo source does not match the active manifest Logo")
                if plan_logo.get("sha256") != active_logo_sha256:
                    errors.append("logo_plan Logo hash does not match the active manifest Logo")
                if plan_logo.get("reference_short_side") != LOGO_REFERENCE_SHORT_SIDE:
                    errors.append("logo_plan reference_short_side must use the locked standard")
                if plan_logo.get("reference_box") != list(LOGO_REFERENCE_BOX):
                    errors.append("logo_plan reference_box must use the locked standard")
            plan_items = plan_data.get("items")
            if not isinstance(plan_items, list):
                errors.append("logo_plan items must be a list")
            else:
                matches = [
                    entry
                    for entry in plan_items
                    if isinstance(entry, dict) and entry.get("task_id") == item.get("task_id")
                ]
                if len(matches) != 1:
                    errors.append("logo_plan must contain exactly one matching task item")
                else:
                    plan_item = matches[0]
                    plan_source = str(plan_item.get("source") or "")
                    item_source = str(item.get("source") or "")
                    if manifest.get("mode") == "generate":
                        if plan_source:
                            errors.append("generate logo_plan source must remain empty")
                    elif not plan_source or canonical_path_key(Path(plan_source)) != canonical_path_key(
                        Path(item_source)
                    ):
                        errors.append("logo_plan source does not match the manifest item")
                    if plan_item.get("decision") != decision:
                        errors.append("logo_plan decision does not match the manifest item")
                    if plan_item.get("family_id") != item.get("family_id"):
                        errors.append("logo_plan family_id does not match the manifest item")
                    conflicts = plan_item.get("conflicts")
                    modules = plan_item.get("modules")
                    expected_conflicts: set[str] | None = None
                    if not isinstance(modules, list):
                        errors.append("logo_plan modules must be an explicit list")
                    elif geometry is not None:
                        canvas = geometry.get("canvas")
                        visible = geometry.get("visible_bbox")
                        expected_conflicts = set()
                        module_ids: set[str] = set()
                        for module in modules:
                            if not isinstance(module, dict):
                                errors.append("logo_plan modules must contain objects")
                                continue
                            module_id = str(module.get("id") or "")
                            bbox = module.get("bbox")
                            if not module_id or module_id in module_ids:
                                errors.append("logo_plan module IDs must be unique and non-empty")
                                continue
                            module_ids.add(module_id)
                            valid_bbox = (
                                isinstance(canvas, list)
                                and len(canvas) == 2
                                and isinstance(bbox, list)
                                and len(bbox) == 4
                                and all(isinstance(value, (int, float)) and math.isfinite(value) for value in bbox)
                                and 0 <= bbox[0] < bbox[2] <= canvas[0]
                                and 0 <= bbox[1] < bbox[3] <= canvas[1]
                            )
                            if not valid_bbox:
                                errors.append(f"logo_plan module {module_id} bbox is invalid or outside the canvas")
                                continue
                            if (
                                isinstance(visible, list)
                                and len(visible) == 4
                                and max(bbox[0], visible[0]) < min(bbox[2], visible[2])
                                and max(bbox[1], visible[1]) < min(bbox[3], visible[3])
                            ):
                                expected_conflicts.add(module_id)
                        if not (
                            isinstance(conflicts, list)
                            and all(isinstance(value, str) and value for value in conflicts)
                            and len(conflicts) == len(set(conflicts))
                        ):
                            errors.append("logo_plan conflicts must be a unique module-ID list")
                        elif set(conflicts) != expected_conflicts:
                            errors.append("logo_plan conflicts must exactly equal modules intersecting visible_bbox")
                    if decision == "direct_overlay" and conflicts != []:
                        errors.append("direct_overlay requires an explicit empty conflicts list")
                    if decision == "regenerate_for_conflict" and not (
                        isinstance(conflicts, list)
                        and conflicts
                        and all(isinstance(value, str) and value for value in conflicts)
                    ):
                        errors.append("regenerate_for_conflict requires one or more explicit conflict module IDs")
                    if expected_conflicts is not None:
                        expected_decision = "regenerate_for_conflict" if expected_conflicts else "direct_overlay"
                        if decision != expected_decision:
                            errors.append("logo_decision does not match visible_bbox/module intersections")
                    anchors = item.get("module_anchors")
                    plan_anchors = plan_item.get("module_anchors", [])
                    if anchors != plan_anchors:
                        errors.append("registered module_anchors must exactly match logo_plan")
                    if decision == "direct_overlay":
                        if anchors != []:
                            errors.append("direct_overlay must not define regenerated module anchors")
                    elif geometry is not None and isinstance(conflicts, list):
                        if not isinstance(anchors, list):
                            errors.append("regenerate_for_conflict module_anchors must be a list")
                        else:
                            anchor_ids: set[str] = set()
                            valid_anchor_ids: set[str] = set()
                            canvas = geometry.get("canvas")
                            zone = geometry.get("safe_zone")
                            for anchor in anchors:
                                if not isinstance(anchor, dict):
                                    errors.append("module_anchors entries must be objects")
                                    continue
                                module_id = str(anchor.get("module_id") or "")
                                placement = anchor.get("placement")
                                prepared_bbox = anchor.get("prepared_bbox")
                                if not module_id or module_id in anchor_ids:
                                    errors.append("module_anchors module_id values must be unique and non-empty")
                                    continue
                                anchor_ids.add(module_id)
                                valid_bbox = (
                                    isinstance(canvas, list)
                                    and len(canvas) == 2
                                    and isinstance(prepared_bbox, list)
                                    and len(prepared_bbox) == 4
                                    and all(
                                        isinstance(value, (int, float)) and math.isfinite(value)
                                        for value in prepared_bbox
                                    )
                                    and 0 <= prepared_bbox[0] < prepared_bbox[2] <= canvas[0]
                                    and 0 <= prepared_bbox[1] < prepared_bbox[3] <= canvas[1]
                                )
                                if not valid_bbox:
                                    errors.append(f"module anchor {module_id} prepared_bbox is invalid")
                                    continue
                                if (
                                    isinstance(zone, list)
                                    and max(prepared_bbox[0], zone[0]) < min(prepared_bbox[2], zone[2])
                                    and max(prepared_bbox[1], zone[1]) < min(prepared_bbox[3], zone[3])
                                ):
                                    errors.append(f"module anchor {module_id} prepared_bbox intersects safe_zone")
                                    continue
                                if placement == "right":
                                    start_range = geometry.get("right_module_start_range")
                                    aligned = (
                                        isinstance(start_range, list)
                                        and len(start_range) == 2
                                        and start_range[0] <= prepared_bbox[0] <= start_range[1]
                                    )
                                elif placement == "below":
                                    start_range = geometry.get("below_module_start_range")
                                    aligned = (
                                        isinstance(start_range, list)
                                        and len(start_range) == 2
                                        and start_range[0] <= prepared_bbox[1] <= start_range[1]
                                    )
                                else:
                                    errors.append(f"module anchor {module_id} placement must be right or below")
                                    continue
                                if not aligned:
                                    errors.append(f"module anchor {module_id} is outside the locked {placement} start range")
                                    continue
                                valid_anchor_ids.add(module_id)
                            if set(conflicts) != anchor_ids or anchor_ids != valid_anchor_ids:
                                errors.append("module_anchors must contain one valid prepared placement for every conflict")
                    if geometry is not None:
                        if plan_item.get("final_size") != geometry.get("canvas"):
                            errors.append("logo_plan final_size does not match Logo geometry")
                        if plan_item.get("visible_bbox") != geometry.get("visible_bbox"):
                            errors.append("logo_plan visible_bbox does not match Logo geometry")
                        if plan_item.get("safe_zone") != geometry.get("safe_zone"):
                            errors.append("logo_plan safe_zone does not match Logo geometry")
                    if not plan_item.get("base_approved") or not plan_item.get("final_approved"):
                        errors.append("logo_plan requires base_approved and final_approved before success")
                    family_id = str(item.get("family_id") or "")
                    if family_id != "ungrouped" and decision == "regenerate_for_conflict":
                        family_by_id, family_errors = validate_layout_families_contract(
                            manifest,
                            plan_data,
                            item,
                            output_record,
                        )
                        errors.extend(family_errors)
                        if family_id not in family_by_id:
                            errors.append("layout_families is missing the current regenerated family")
        if decision == "regenerate_for_conflict":
            relocation_record, relocation_errors = recompute_logo_relocation_validation(
                item,
                manifest,
                plan_data if isinstance(plan_data, dict) else None,
                geometry,
            )
            errors.extend(relocation_errors)
            if relocation_record is not None and item.get("logo_relocation_validation") != relocation_record:
                errors.append("Logo success requires the current recomputed relocation validation record")
        elif item.get("logo_relocation_validation") is not None:
            errors.append("direct_overlay must not retain Logo relocation validation")
    return errors


def validate_manifest(data: dict[str, Any], check_files: bool = True) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    seen_task_ids: set[str] = set()
    seen_sources: dict[str, str] = {}
    seen_outputs: dict[str, str] = {}
    seen_hashes: dict[str, str] = {}
    mode = str(data.get("mode") or "")
    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        return [{"task_id": "<manifest>", "error": "manifest items must be a list"}]
    items = raw_items
    if mode not in {"edit", "generate", "localization"}:
        errors.append({"task_id": "<manifest>", "error": f"invalid manifest mode: {mode or '<missing>'}"})
    compatibility = data.get("manifest_compatibility")
    legacy_read_only = is_legacy_read_only_manifest(data)
    if compatibility is not None and not legacy_read_only:
        errors.append({
            "task_id": "<manifest>",
            "error": "manifest_compatibility must be an exact read-only legacy migration record",
        })
    image_model_policy = data.get("image_model_policy")
    expected_image_policy = LEGACY_IMAGE_MODEL_POLICY if legacy_read_only else CURRENT_IMAGE_MODEL_POLICY
    if image_model_policy != expected_image_policy:
        errors.append({
            "task_id": "<manifest>",
            "error": (
                "legacy manifests require the exact read-only legacy image policy"
                if legacy_read_only
                else "current manifests require the exact pure-generation policy with only Logo exceptions"
            ),
        })
    if mode == "generate":
        try:
            variants = int(data.get("variants", 0))
        except (TypeError, ValueError):
            variants = 0
        if variants < 1:
            errors.append({"task_id": "<manifest>", "error": "generate manifest variants must be positive"})
        elif variants != len(items):
            errors.append({"task_id": "<manifest>", "error": "generate manifest variants do not match item count"})
    policy = data.get("localization_policy") or {}
    if mode == "localization" and not isinstance(policy, dict):
        errors.append({"task_id": "<manifest>", "error": "localization_policy must be an object"})
        policy = {}
    localization_policy_mode = str(policy.get("mode") or "") if isinstance(policy, dict) else ""
    allowed_localization_modes = (
        {LEGACY_REFERENCE_LOCALIZATION_MODE}
        if legacy_read_only
        else {PURE_GENERATION_LOCALIZATION_MODE}
    )
    if mode == "localization" and localization_policy_mode not in allowed_localization_modes:
        errors.append({
            "task_id": "<manifest>",
            "error": (
                "legacy localization manifests are read-only and require their legacy policy"
                if legacy_read_only
                else "current localization manifests require pure_generation_localization"
            ),
        })
    if mode == "localization" and not legacy_read_only:
        try:
            quality_budget_valid = int(policy.get("quality_attempts", 0) or 0) == 3
        except (TypeError, ValueError):
            quality_budget_valid = False
        if not quality_budget_valid:
            errors.append({"task_id": "<manifest>", "error": "pure-generation localization quality budget must be 3"})
        if image_model_policy != CURRENT_IMAGE_MODEL_POLICY:
            errors.append({"task_id": "<manifest>", "error": "new localization requires the pure-generation image model policy"})
    if mode == "localization" and policy.get("authorization_scope") == "task":
        manifest_id = str(data.get("manifest_id") or "")
        if not re.fullmatch(r"xobi-[0-9a-f]{32}", manifest_id):
            errors.append({"task_id": "<manifest>", "error": "task-scoped localization requires a valid manifest_id"})
        if bool(policy.get("pure_rebuild_allowed")):
            errors.append({"task_id": "<manifest>", "error": "task-scoped pure rebuild must not use a global allow flag"})
    try:
        workers_requested = int(data.get("workers_requested", data.get("workers", 1)) or 1)
    except (TypeError, ValueError):
        workers_requested = 0
    if not 1 <= workers_requested <= 4:
        errors.append({"task_id": "<manifest>", "error": "workers_requested must be between 1 and 4"})
    if check_files and data.get("logo"):
        _, _, logo_errors = active_logo_asset(data)
        for message in logo_errors:
            errors.append({"task_id": "<manifest>", "error": message})
    for item_index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            errors.append({"task_id": f"<item-{item_index}>", "error": "manifest item must be an object"})
            continue
        task_id = str(item.get("task_id") or "")
        if not task_id or task_id in seen_task_ids:
            errors.append({"task_id": task_id or "<missing>", "error": "task_id is missing or duplicated"})
        elif not valid_task_id(task_id):
            errors.append({"task_id": task_id, "error": "task_id contains unsafe filename characters"})
        seen_task_ids.add(task_id)
        status = str(item.get("status") or "")
        if status not in {"pending", "success", "skipped", "failed"}:
            errors.append({"task_id": task_id, "error": f"invalid task status: {status}"})
        if status == "failed" and not str(item.get("error") or "").strip():
            errors.append({"task_id": task_id, "error": "failed task is missing an error message"})
        try:
            if int(item.get("attempts", 0) or 0) < 0:
                errors.append({"task_id": task_id, "error": "attempts must be non-negative"})
        except (TypeError, ValueError):
            errors.append({"task_id": task_id, "error": "attempts must be an integer"})
        source_value = str(item.get("source") or "")
        if mode == "generate":
            expected_variant = f"variant-{item_index:03d}"
            if task_id != expected_variant or item.get("variant_index") != item_index:
                errors.append({"task_id": task_id, "error": "generate task id/index does not match its variant allocation"})
            expected_name = expected_variant + Path(str(item.get("output") or "")).suffix.lower()
            if (
                Path(str(item.get("output") or "")).name != expected_name
                or Path(str(item.get("output_relative_path") or "")).as_posix() != expected_name
            ):
                errors.append({"task_id": task_id, "error": "generate output path does not match its variant allocation"})
            if source_value:
                errors.append({"task_id": task_id, "error": "generate tasks must not define a source path"})
            if item.get("source_sha256"):
                errors.append({"task_id": task_id, "error": "generate tasks must not define a source hash"})
        elif not source_value:
            errors.append({"task_id": task_id, "error": "source path is missing"})
        else:
            source_key = canonical_path_key(Path(source_value))
            if source_key in seen_sources:
                errors.append({"task_id": task_id, "error": f"source path duplicates {seen_sources[source_key]}"})
            else:
                seen_sources[source_key] = task_id
            if check_files:
                source_path = Path(source_value)
                if not source_path.is_file():
                    errors.append({"task_id": task_id, "error": "source file is missing"})
                elif item.get("source_sha256") and sha256_file(source_path) != item.get("source_sha256"):
                    errors.append({"task_id": task_id, "error": "source hash changed after preflight"})
        output_key = canonical_path_key(Path(str(item.get("output") or "")))
        if output_key in seen_outputs:
            errors.append({"task_id": task_id, "error": f"output path duplicates {seen_outputs[output_key]}"})
        else:
            seen_outputs[output_key] = task_id
        recorded_output_key = item.get("output_key")
        if recorded_output_key and str(recorded_output_key) != output_key:
            errors.append({"task_id": task_id, "error": "recorded output_key does not match output path"})
        worker_id = str(item.get("worker_id") or "")
        match = re.fullmatch(r"worker-(\d+)", worker_id)
        if not match or int(match.group(1)) < 1 or int(match.group(1)) > workers_requested:
            errors.append({"task_id": task_id, "error": f"invalid worker assignment: {worker_id}"})
        if mode == "localization":
            for message in validate_localization_attempt_contract(item, data):
                errors.append({"task_id": task_id, "error": message})
            for message in validate_localization_plan_registration(item, data):
                errors.append({"task_id": task_id, "error": message})
            if item.get("status") != "success" and item.get("pure_rebuild_approval") is not None:
                _, approval_errors = validate_pure_rebuild_approval(item, data)
                for message in approval_errors:
                    errors.append({"task_id": task_id, "error": message})
        for message in validate_logo_conflict_attempt_contract(item, data):
            errors.append({"task_id": task_id, "error": message})
        if check_files and item.get("status") == "success":
            record, item_errors = validate_output(item, data)
            for message in item_errors:
                errors.append({"task_id": task_id, "error": message})
            for message in validate_item_contract(item, data, record):
                errors.append({"task_id": task_id, "error": message})
            if record:
                recorded = item.get("output_validation") or {}
                required_fields = ("sha256", "bytes", "width", "height", "format", "has_transparency")
                missing_fields = [field for field in required_fields if field not in recorded]
                if missing_fields:
                    errors.append({
                        "task_id": task_id,
                        "error": "success is missing output validation baseline: " + ", ".join(missing_fields),
                    })
                for field in required_fields:
                    if field in recorded and recorded.get(field) != record.get(field):
                        errors.append({"task_id": task_id, "error": f"output {field} changed after success validation"})
                digest = str(record["sha256"])
                if digest in seen_hashes:
                    errors.append({"task_id": task_id, "error": f"output content duplicates {seen_hashes[digest]}"})
                else:
                    seen_hashes[digest] = task_id
    return errors


def write_report(path: Path, manifest: dict[str, Any]) -> None:
    items = list(manifest.get("items", []))
    counts: dict[str, int] = {}
    for item in items:
        status = str(item.get("status", "pending"))
        counts[status] = counts.get(status, 0) + 1
    lines = [
        "# xobi-img Task Report",
        "",
        f"- Schema: {manifest.get('schema_version', 'N/A')}",
        f"- Manifest ID: {manifest.get('manifest_id') or 'N/A'}",
        f"- Mode: {manifest.get('mode', 'N/A')}",
        f"- Operation: {manifest.get('operation', 'N/A')}",
        f"- Ratio: {manifest.get('ratio', 'N/A')}",
        f"- Target language: {manifest.get('target_language') or 'N/A'}",
        f"- Input: {manifest.get('input', 'N/A')}",
        f"- Task directory: {manifest.get('task_dir', 'N/A')}",
        f"- Workers: {manifest.get('workers_active', manifest.get('workers', 1))}",
        f"- Execution mode: {manifest.get('execution_mode', 'N/A')}",
        f"- Total targets: {len(items)}",
        f"- Pending: {counts.get('pending', 0)}",
        f"- Success: {counts.get('success', 0)}",
        f"- Skipped: {counts.get('skipped', 0)}",
        f"- Failed: {counts.get('failed', 0)}",
        f"- Unsupported inputs: {len(manifest.get('unsupported_inputs', []))}",
        f"- Excluded inputs: {len(manifest.get('excluded_inputs', []))}",
        "",
        "## Items",
        "",
        "| Task | Worker | Source | Output | Status | Attempts | Error |",
        "|---|---|---|---|---|---:|---|",
    ]
    for item in items:
        source = str(item.get("source", "")).replace("|", "\\|")
        output = str(item.get("output", "")).replace("|", "\\|")
        error = str(item.get("error") or "").replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {item.get('task_id', '')} | {item.get('worker_id', '')} | {source} | {output} | "
            f"{item.get('status', '')} | {item.get('attempts', 0)} | {error} |"
        )
    unsupported = manifest.get("unsupported_inputs", [])
    if unsupported:
        lines.extend(["", "## Unsupported inputs", ""])
        for entry in unsupported:
            lines.append(f"- `{entry.get('path', '')}`: {entry.get('reason', 'unsupported')}")
    approvals = [
        (item, item.get("pure_rebuild_approval"))
        for item in items
        if isinstance(item.get("pure_rebuild_approval"), dict)
    ]
    if approvals:
        lines.extend(["", "## Task-scoped pure rebuild approvals", ""])
        for item, approval in approvals:
            evidence = str(approval.get("evidence") or "").replace("\n", " ").strip()
            lines.append(
                f"- `{item.get('task_id', '')}` source `{approval.get('source_sha256', '')}` after "
                f"`{approval.get('approved_after_attempt_record_id', '')}`: {evidence}"
            )
    localization_items = [
        item for item in items if item.get("localization_execution_stage")
    ]
    if localization_items:
        lines.extend(["", "## Localization execution stages", ""])
        for item in localization_items:
            composition = item.get("localization_composition") or {}
            provenance = composition.get("artifact_path") if isinstance(composition, dict) else None
            lines.append(
                f"- `{item.get('task_id', '')}`: `{item.get('localization_execution_stage')}`; "
                f"composition: `{provenance or 'not-applicable'}`"
            )
    atomic_text(path, "\n".join(lines) + "\n")
