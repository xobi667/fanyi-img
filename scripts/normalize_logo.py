#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import os
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageOps

from manifest_utils import (
    FileLock,
    atomic_bytes,
    atomic_json,
    is_legacy_read_only_manifest,
    load_manifest,
    logo_canvas_requires_review,
    now_iso,
    sha256_file,
    validate_auxiliary_json_path,
    write_report,
)


DEFAULT_ALPHA_THRESHOLD = 10
DEFAULT_COLOR_THRESHOLD = 18


def corner_background(image: Image.Image, sample: int) -> tuple[int, int, int]:
    rgb = image.convert("RGB")
    width, height = rgb.size
    size = max(1, min(sample, width, height))
    boxes = (
        (0, 0, size, size),
        (width - size, 0, width, size),
        (0, height - size, size, height),
        (width - size, height - size, width, height),
    )
    colors: list[tuple[int, int, int]] = []
    for box in boxes:
        patch = rgb.crop(box).resize((1, 1), Image.Resampling.BOX)
        colors.append(patch.getpixel((0, 0)))
    quantized = [tuple((channel // 8) * 8 for channel in color) for color in colors]
    winner = Counter(quantized).most_common(1)[0][0]
    matching = [color for color, bucket in zip(colors, quantized) if bucket == winner]
    return tuple(round(sum(color[index] for color in matching) / len(matching)) for index in range(3))


def color_content_bbox(
    image: Image.Image,
    background: tuple[int, int, int],
    threshold: int,
) -> tuple[int, int, int, int] | None:
    rgb = image.convert("RGB")
    flat = Image.new("RGB", rgb.size, background)
    diff = ImageChops.difference(rgb, flat)
    red, green, blue = diff.split()
    strongest = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    mask = strongest.point(lambda value: 255 if value > threshold else 0)
    return mask.getbbox()


def content_bbox(
    image: Image.Image,
    background_mode: str,
    alpha_threshold: int,
    color_threshold: int,
    sample: int,
) -> tuple[tuple[int, int, int, int], str, tuple[int, int, int] | None, str | None]:
    alpha = image.getchannel("A")
    alpha_bbox = alpha.point(lambda value: 255 if value >= alpha_threshold else 0).getbbox()
    if not alpha_bbox:
        raise ValueError("Logo has no visible pixels at the selected alpha threshold")
    alpha_reaches_full_canvas = alpha_bbox == (0, 0, image.width, image.height)
    has_transparency = alpha.getextrema()[0] < 255
    if background_mode == "transparent" or (
        background_mode == "auto" and has_transparency and not alpha_reaches_full_canvas
    ):
        return alpha_bbox, "transparent", None, None
    if background_mode == "auto":
        warning = (
            "edge-reaching Logo canvas preserved unchanged: automatic solid-border removal is ambiguous; "
            "visually confirm the exterior color, then use --background white or --background solid"
        )
        return (0, 0, image.width, image.height), "opaque-preserved", None, warning
    background = (255, 255, 255) if background_mode == "white" else corner_background(image, sample)
    bbox = color_content_bbox(image, background, color_threshold)
    if not bbox:
        raise ValueError("could not distinguish Logo content from the confirmed exterior background")
    return bbox, "solid", background, None


def main() -> int:
    parser = argparse.ArgumentParser(description="Conservatively trim transparent or visually confirmed solid-color Logo borders without redrawing pixels.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--background", choices=["auto", "white", "solid", "transparent"], default="auto")
    parser.add_argument("--alpha-threshold", type=int, default=DEFAULT_ALPHA_THRESHOLD)
    parser.add_argument("--color-threshold", type=int, default=DEFAULT_COLOR_THRESHOLD)
    parser.add_argument("--threshold", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--corner-sample", type=int, default=24)
    parser.add_argument("--horizontal-padding", type=float, default=0.06, help="Padding on trim-able left/right edges as a fraction of detected content width.")
    parser.add_argument("--vertical-padding", type=float, default=0.0, help="Padding on trim-able top/bottom edges as a fraction of detected content height.")
    parser.add_argument("--metadata-json", type=Path)
    parser.add_argument("--manifest", type=Path, help="Atomically register the normalized asset in this .xobi manifest.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    if args.threshold is not None:
        args.alpha_threshold = args.threshold
        args.color_threshold = args.threshold
    raw_source = args.input.expanduser().absolute()
    raw_output = args.output.expanduser().absolute()
    if raw_source.is_symlink() or bool(getattr(raw_source, "is_junction", lambda: False)()):
        parser.error("input Logo must not be a symlink or junction")
    if raw_output.exists() and (raw_output.is_symlink() or bool(getattr(raw_output, "is_junction", lambda: False)())):
        parser.error("output must not be a symlink or junction")
    source = raw_source.resolve()
    output = args.output.resolve()
    if not source.is_file() or source == output:
        parser.error("input must exist and output must differ")
    if (
        not 1 <= args.alpha_threshold <= 255
        or not 0 <= args.color_threshold <= 255
        or args.corner_sample < 1
        or not math.isfinite(args.horizontal_padding)
        or not math.isfinite(args.vertical_padding)
        or args.horizontal_padding < 0
        or args.vertical_padding < 0
    ):
        parser.error("invalid trim settings")
    if output.suffix.lower() != ".png":
        parser.error("normalized Logo output must use a .png suffix")
    if output.exists() and not args.overwrite and not args.dry_run:
        parser.error("output exists; use --overwrite")
    metadata_path = None
    if args.metadata_json:
        try:
            metadata_path = validate_auxiliary_json_path(args.metadata_json, {source, output})
        except ValueError as exc:
            parser.error(str(exc))
    if metadata_path:
        if metadata_path.suffix.lower() != ".json":
            parser.error("metadata-json must use a .json suffix")
        if metadata_path.exists() and not args.overwrite:
            parser.error("metadata-json exists; use --overwrite")
    manifest_path = args.manifest.resolve() if args.manifest else None
    if manifest_path is not None:
        if not manifest_path.is_file():
            parser.error(f"manifest not found: {manifest_path}")
        work_dir = manifest_path.parent / "work"
        try:
            output.relative_to(work_dir.resolve())
        except ValueError:
            parser.error("registered normalized Logo output must be inside .xobi/work")

    try:
        with Image.open(source) as raw:
            image = ImageOps.exif_transpose(raw).convert("RGBA")
        bbox, detected_mode, background, warning = content_bbox(
            image,
            args.background,
            args.alpha_threshold,
            args.color_threshold,
            args.corner_sample,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    left, top, right, bottom = bbox
    pad_x = round((right - left) * args.horizontal_padding)
    pad_y = round((bottom - top) * args.vertical_padding)
    crop = (
        max(0, left - pad_x) if left > 0 else 0,
        max(0, top - pad_y) if top > 0 else 0,
        min(image.width, right + pad_x) if right < image.width else image.width,
        min(image.height, bottom + pad_y) if bottom < image.height else image.height,
    )
    metadata: dict[str, Any] = {
        "schema_version": 1,
        "source": str(source),
        "source_sha256": sha256_file(source),
        "normalized": None,
        "normalized_sha256": None,
        "canvas": [image.width, image.height],
        "detected_mode": detected_mode,
        "background": list(background) if background else None,
        "content_bbox": [left, top, right, bottom],
        "crop_box": list(crop),
        "output_size": [crop[2] - crop[0], crop[3] - crop[1]],
        "alpha_threshold": args.alpha_threshold,
        "color_threshold": args.color_threshold,
        "settings": {
            "background": args.background,
            "alpha_threshold": args.alpha_threshold,
            "color_threshold": args.color_threshold,
            "corner_sample": args.corner_sample,
            "horizontal_padding": args.horizontal_padding,
            "vertical_padding": args.vertical_padding,
        },
        "warning": warning,
    }
    print(
        f"source={source} canvas={image.width}x{image.height} detected_mode={detected_mode} "
        f"background={background} content_bbox={left},{top},{right},{bottom} "
        f"crop_box={crop[0]},{crop[1]},{crop[2]},{crop[3]} output_size={crop[2]-crop[0]}x{crop[3]-crop[1]}"
    )
    if warning:
        print(f"warning={warning}")
    if detected_mode == "opaque-preserved" and not args.dry_run:
        parser.error("edge-reaching auto mode is review-only; choose --background white or --background solid before writing")
    if args.dry_run:
        if metadata_path:
            try:
                atomic_json(metadata_path, metadata)
            except OSError as exc:
                parser.error(f"could not write metadata JSON: {exc}")
        return 0
    temporary = output.with_name(f".{output.stem}.tmp-{os.getpid()}-{uuid.uuid4().hex}.png")
    report_path = manifest_path.parent / "report.md" if manifest_path else None
    try:
        lock_path = manifest_path.with_name(manifest_path.name + ".lock") if manifest_path else None
        lock = FileLock(lock_path) if lock_path else None
        if lock:
            lock.__enter__()
        try:
            output_snapshot = output.read_bytes() if output.is_file() else None
            metadata_snapshot = metadata_path.read_bytes() if metadata_path and metadata_path.is_file() else None
            manifest_snapshot = manifest_path.read_bytes() if manifest_path else None
            report_snapshot = report_path.read_bytes() if report_path and report_path.is_file() else None
            snapshots = {
                output: output_snapshot,
                **({metadata_path: metadata_snapshot} if metadata_path else {}),
                **({manifest_path: manifest_snapshot} if manifest_path else {}),
                **({report_path: report_snapshot} if report_path else {}),
            }
            changed: list[Path] = []
            try:
                manifest = load_manifest(manifest_path) if manifest_path else None
                if manifest is not None:
                    if is_legacy_read_only_manifest(manifest):
                        raise ValueError(
                            "legacy manifests are read-only; migrate before registering Logo normalization"
                        )
                    logo_record = manifest.get("logo")
                    if not isinstance(logo_record, dict) or not logo_record.get("enabled"):
                        raise ValueError("manifest does not contain an active Logo record")
                    logo_source = Path(str(logo_record.get("source") or "")).resolve()
                    if logo_source != source or logo_record.get("source_sha256") != metadata["source_sha256"]:
                        raise ValueError("normalization input does not match the manifest's original Logo and hash")
                    locked_alpha_threshold = int(
                        logo_record.get("alpha_threshold", DEFAULT_ALPHA_THRESHOLD)
                    )
                    if args.alpha_threshold != locked_alpha_threshold:
                        raise ValueError(
                            "normalization alpha threshold must match the manifest's locked Logo alpha threshold "
                            f"({locked_alpha_threshold})"
                        )
                    if logo_record.get("normalized") and not args.overwrite:
                        raise ValueError("manifest already has a normalized Logo; use --overwrite to replace it")

                output.parent.mkdir(parents=True, exist_ok=True)
                image.crop(crop).save(temporary, format="PNG")
                os.replace(temporary, output)
                changed.append(output)
                metadata["normalized"] = str(output)
                metadata["normalized_sha256"] = sha256_file(output)
                with Image.open(output) as normalized_image:
                    metadata["normalized_size"] = list(normalized_image.size)
                if metadata_path:
                    atomic_json(metadata_path, metadata)
                    changed.append(metadata_path)
                if manifest is not None and manifest_path is not None:
                    logo_record = manifest["logo"]
                    logo_record["normalized"] = str(output)
                    logo_record["normalized_sha256"] = metadata["normalized_sha256"]
                    logo_record["normalization"] = metadata
                    logo_record["opaque_review_required"] = logo_canvas_requires_review(
                        output,
                        int(logo_record.get("alpha_threshold", DEFAULT_ALPHA_THRESHOLD)),
                    )
                    timestamp = now_iso()
                    manifest["revision"] = int(manifest.get("revision", 0) or 0) + 1
                    manifest["updated_at"] = timestamp
                    atomic_json(manifest_path, manifest)
                    changed.append(manifest_path)
                    assert report_path is not None
                    write_report(report_path, manifest)
                    changed.append(report_path)
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
                        "normalization failed and rollback was incomplete: " + "; ".join(rollback_errors)
                    ) from exc
                raise
        finally:
            if lock:
                lock.__exit__(None, None, None)
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        parser.error(f"could not write/register normalized Logo: {exc}")
    finally:
        temporary.unlink(missing_ok=True)
    print(f"written={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
