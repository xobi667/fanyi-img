#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import io
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from manifest_utils import (
    LOGO_ALPHA_THRESHOLD,
    LOGO_ANCHOR_TOLERANCE,
    LOGO_REFERENCE_BOX,
    LOGO_REFERENCE_SHORT_SIDE,
    LOGO_SAFE_PADDING,
    atomic_json,
    sha256_file,
    validate_auxiliary_json_path,
)


IMAGE_EXTS = {".jpg", ".jpeg", ".jfif", ".png", ".webp", ".bmp", ".tif", ".tiff"}
DEFAULT_LOGO = Path(__file__).resolve().parent.parent / "assets" / "we-film-logo-template.png"
DEFAULT_LOGO_REFERENCE_BOX = LOGO_REFERENCE_BOX
DEFAULT_ANCHOR_TOLERANCE = LOGO_ANCHOR_TOLERANCE


def clean_logo(path: Path, alpha_threshold: int) -> Image.Image:
    if not 1 <= alpha_threshold <= 255:
        raise ValueError("alpha threshold must be between 1 and 255")
    with Image.open(path) as source:
        logo = ImageOps.exif_transpose(source).convert("RGBA")
    alpha = logo.getchannel("A").point(lambda value: 0 if value < alpha_threshold else value)
    logo.putalpha(alpha)
    bbox = alpha.point(lambda value: 255 if value >= alpha_threshold else 0).getbbox()
    if not bbox:
        raise ValueError("Logo has no visible pixels at the selected alpha threshold")
    return logo.crop(bbox)


def logo_canvas_requires_review(path: Path, alpha_threshold: int) -> bool:
    with Image.open(path) as source:
        logo = ImageOps.exif_transpose(source).convert("RGBA")
    alpha = logo.getchannel("A").point(lambda value: 255 if value >= alpha_threshold else 0)
    return alpha.getbbox() == (0, 0, logo.width, logo.height)


def scaled_overlay(
    logo: Image.Image,
    canvas_size: tuple[int, int],
    reference_short_side: int,
    reference_box: tuple[int, int],
) -> Image.Image:
    if logo.width < 1 or logo.height < 1:
        raise ValueError("Logo dimensions must be positive")
    scale = min(canvas_size) / reference_short_side
    box_width = max(1, round(reference_box[0] * scale))
    box_height = max(1, round(reference_box[1] * scale))
    fit = min(box_width / logo.width, box_height / logo.height)
    size = (max(1, round(logo.width * fit)), max(1, round(logo.height * fit)))
    if size[0] > canvas_size[0] or size[1] > canvas_size[1]:
        raise ValueError("scaled Logo does not fit inside the final canvas")
    return logo.resize(size, Image.Resampling.LANCZOS)


def visible_bbox(overlay: Image.Image, alpha_threshold: int) -> tuple[int, int, int, int]:
    alpha = overlay.getchannel("A").point(lambda value: 255 if value >= alpha_threshold else 0)
    bbox = alpha.getbbox()
    if not bbox:
        raise ValueError("scaled Logo has no visible pixels")
    return bbox


def safe_zone(
    overlay: Image.Image,
    padding: int,
    alpha_threshold: int,
    canvas_size: tuple[int, int],
) -> tuple[int, int, int, int]:
    bbox = visible_bbox(overlay, alpha_threshold)
    return (0, 0, min(canvas_size[0], bbox[2] + padding), min(canvas_size[1], bbox[3] + padding))


def geometry_for(
    logo: Image.Image,
    canvas_size: tuple[int, int],
    reference_short_side: int,
    reference_box: tuple[int, int],
    safe_padding: int,
    anchor_tolerance: int,
    alpha_threshold: int,
) -> tuple[Image.Image, dict[str, Any]]:
    overlay = scaled_overlay(logo, canvas_size, reference_short_side, reference_box)
    scale = min(canvas_size) / reference_short_side
    scaled_padding = max(0, round(safe_padding * scale))
    scaled_tolerance = max(0, round(anchor_tolerance * scale))
    bbox = visible_bbox(overlay, alpha_threshold)
    zone = safe_zone(overlay, scaled_padding, alpha_threshold, canvas_size)
    right_start = zone[2]
    below_start = zone[3]
    right_end = min(canvas_size[0], right_start + scaled_tolerance)
    below_end = min(canvas_size[1], below_start + scaled_tolerance)
    geometry = {
        "canvas": [canvas_size[0], canvas_size[1]],
        "scale": scale,
        "logo_canvas": [overlay.width, overlay.height],
        "visible_bbox": [bbox[0], bbox[1], bbox[2], bbox[3]],
        "safe_padding": scaled_padding,
        "safe_zone": [zone[0], zone[1], zone[2], zone[3]],
        "right_module_anchor": [right_start, bbox[1]],
        "right_module_start_range": [right_start, right_end],
        "right_available": right_start < canvas_size[0],
        "below_module_anchor": [bbox[0], below_start],
        "below_module_start_range": [below_start, below_end],
        "below_available": below_start < canvas_size[1],
    }
    return overlay, geometry


def is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def is_link_or_junction(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def iter_inputs(input_path: Path, output_path: Path, logo_path: Path, excludes: list[str]) -> list[Path]:
    input_path = input_path.resolve()
    output_path = output_path.resolve()
    logo_path = logo_path.resolve()
    if input_path.is_file():
        return [input_path] if input_path.suffix.lower() in IMAGE_EXTS and input_path != logo_path else []
    exclude_paths: list[Path] = [logo_path]
    patterns: list[str] = []
    if is_inside(output_path, input_path):
        exclude_paths.append(output_path)
    for value in excludes:
        if any(character in value for character in "*?["):
            patterns.append(value.replace("\\", "/").casefold())
        else:
            candidate = Path(value)
            exclude_paths.append(candidate.resolve() if candidate.is_absolute() else (input_path / candidate).resolve())

    files: list[Path] = []
    for item in sorted(input_path.rglob("*")):
        if not item.is_file() or item.suffix.lower() not in IMAGE_EXTS:
            continue
        if is_link_or_junction(item):
            continue
        relative = item.relative_to(input_path)
        parts = {part.casefold() for part in relative.parts}
        if ".xobi" in parts or "xobi-img-output" in parts:
            continue
        resolved = item.resolve()
        if any(resolved == excluded or (excluded.is_dir() and is_inside(resolved, excluded)) for excluded in exclude_paths):
            continue
        relative_key = relative.as_posix().casefold()
        if any(fnmatch.fnmatch(relative_key, pattern) for pattern in patterns):
            continue
        files.append(item.absolute())
    return files


def jpeg_bytes(image: Image.Image, quality: int) -> bytes:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, "JPEG", quality=quality, optimize=True, progressive=True, subsampling=0)
    return buffer.getvalue()


def save_image(image: Image.Image, output: Path, min_kb: int | None, max_kb: int | None) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.stem}.tmp-{os.getpid()}-{uuid.uuid4().hex}{output.suffix}")
    try:
        if output.suffix.lower() in {".jpg", ".jpeg", ".jfif"}:
            if max_kb:
                low, high, best = 1, 100, jpeg_bytes(image, 1)
                while low <= high:
                    quality = (low + high) // 2
                    candidate = jpeg_bytes(image, quality)
                    if len(candidate) <= max_kb * 1024:
                        best = candidate
                        low = quality + 1
                    else:
                        high = quality - 1
                if len(best) > max_kb * 1024:
                    raise ValueError("cannot fit JPEG under maximum size")
            else:
                best = jpeg_bytes(image, 95)
            if min_kb and len(best) < min_kb * 1024:
                best += b"\0" * (min_kb * 1024 - len(best))
            temporary.write_bytes(best)
        else:
            image.save(temporary)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministically overlay a real Logo after visual information-module approval.")
    parser.add_argument("--input", required=True, type=Path, help="Approved base image or directory.")
    parser.add_argument("--output", required=True, type=Path, help="Output image or directory; must differ from input.")
    logo_source = parser.add_mutually_exclusive_group(required=True)
    logo_source.add_argument("--logo", type=Path, help="Current task's explicitly selected Logo asset.")
    logo_source.add_argument(
        "--use-default-logo",
        action="store_true",
        help="Use the bundled template only after the user explicitly requested the default Logo.",
    )
    parser.add_argument("--reference-short-side", type=int, default=LOGO_REFERENCE_SHORT_SIDE)
    parser.add_argument("--logo-reference-width", type=int, default=DEFAULT_LOGO_REFERENCE_BOX[0])
    parser.add_argument("--logo-reference-height", type=int, default=DEFAULT_LOGO_REFERENCE_BOX[1])
    parser.add_argument("--alpha-threshold", type=int, default=LOGO_ALPHA_THRESHOLD)
    parser.add_argument("--safe-padding", type=int, default=LOGO_SAFE_PADDING, help="Padding in 4000px reference coordinates.")
    parser.add_argument("--anchor-tolerance", type=int, default=DEFAULT_ANCHOR_TOLERANCE)
    parser.add_argument("--safe-zone-approved", action="store_true", help="Confirm no information-bearing module intersects the actual Logo overlay.")
    parser.add_argument("--dry-run", action="store_true", help="Print geometry without writing output.")
    parser.add_argument("--geometry-json", type=Path, help="Write machine-readable per-image geometry, normally under .xobi/work.")
    parser.add_argument("--opaque-approved", action="store_true", help="Confirm an edge-reaching visible-alpha Logo canvas was visually reviewed/normalized and is intentional.")
    parser.add_argument("--exclude", action="append", default=[], help="Path or relative glob to exclude; repeat as needed.")
    parser.add_argument("--min-kb", type=int, help="Optional JPEG minimum size.")
    parser.add_argument("--max-kb", type=int, help="Optional JPEG maximum size.")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    raw_input = args.input.expanduser().absolute()
    raw_logo = (DEFAULT_LOGO if args.use_default_logo else args.logo).expanduser().absolute()
    if (raw_input.exists() and is_link_or_junction(raw_input)) or (raw_logo.exists() and is_link_or_junction(raw_logo)):
        parser.error("input and Logo must not be symlinks or junctions")
    input_path = raw_input.resolve()
    output_path = args.output.resolve()
    logo_path = raw_logo.resolve()
    if not input_path.exists() or not logo_path.is_file():
        parser.error("input or Logo not found")
    if input_path == output_path or output_path == logo_path:
        parser.error("output must differ from input and Logo")
    if input_path.is_file() and output_path.exists() and not output_path.is_file():
        parser.error("file input requires a file output")
    if input_path.is_dir() and output_path.exists() and not output_path.is_dir():
        parser.error("directory input requires a directory output")
    if input_path.is_file() and output_path.suffix.lower() not in IMAGE_EXTS:
        parser.error("file output must use a supported image suffix")
    if (
        args.reference_short_side < 1
        or args.logo_reference_width < 1
        or args.logo_reference_height < 1
        or not 1 <= args.alpha_threshold <= 255
        or args.safe_padding < 0
        or args.anchor_tolerance < 0
    ):
        parser.error("invalid geometry settings")
    locked_geometry = (
        args.reference_short_side == LOGO_REFERENCE_SHORT_SIDE
        and (args.logo_reference_width, args.logo_reference_height) == LOGO_REFERENCE_BOX
        and args.alpha_threshold == LOGO_ALPHA_THRESHOLD
        and args.safe_padding == LOGO_SAFE_PADDING
        and args.anchor_tolerance == LOGO_ANCHOR_TOLERANCE
    )
    if not locked_geometry:
        parser.error(
            "Logo geometry is locked to short-side 4000, box 1036x309, alpha 10, "
            "safe padding 80, and anchor tolerance 48"
        )
    if (args.min_kb is not None and args.min_kb < 1) or (args.max_kb is not None and args.max_kb < 1):
        parser.error("min-kb and max-kb must be positive")
    if args.min_kb and args.max_kb and args.max_kb < args.min_kb:
        parser.error("max-kb must be >= min-kb")
    if args.min_kb is not None or args.max_kb is not None:
        parser.error(
            "locked Logo outputs use deterministic encoding; optimize prepared_base before the final Logo overlay"
        )
    if not args.dry_run and not args.safe_zone_approved:
        parser.error("refusing to add Logo without --safe-zone-approved after visual review")

    files = iter_inputs(input_path, output_path, logo_path, args.exclude)
    if not files:
        parser.error("no supported input images after excluding Logo/output/metadata paths")
    try:
        source_canvas_requires_review = logo_canvas_requires_review(logo_path, args.alpha_threshold)
        logo = clean_logo(logo_path, args.alpha_threshold)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        parser.error(str(exc))
    if source_canvas_requires_review and not args.opaque_approved:
        parser.error(
            "Logo visible alpha reaches the full canvas; visual normalization review and --opaque-approved are required"
        )
    root = input_path if input_path.is_dir() else input_path.parent
    written = 0
    skipped = 0
    geometries: list[dict[str, Any]] = []
    tasks: list[tuple[Path, Path, Image.Image]] = []
    for source in files:
        try:
            with Image.open(source) as raw:
                base = ImageOps.exif_transpose(raw).convert("RGBA")
            overlay, geometry = geometry_for(
                logo,
                base.size,
                args.reference_short_side,
                (args.logo_reference_width, args.logo_reference_height),
                args.safe_padding,
                args.anchor_tolerance,
                args.alpha_threshold,
            )
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            parser.error(f"{source}: {exc}")
        geometry["source"] = str(source)
        geometries.append(geometry)
        output = output_path if input_path.is_file() else output_path / source.relative_to(root)
        if output.resolve() == logo_path or output.resolve() in {path.resolve() for path in files}:
            parser.error("an output path would overwrite the Logo or an input image")
        tasks.append((source, output, overlay))
        print(
            f"source={source} canvas={base.width}x{base.height} scale={geometry['scale']:.6f} "
            f"logo_canvas={overlay.width}x{overlay.height} "
            f"visible_bbox={','.join(str(value) for value in geometry['visible_bbox'])} "
            f"safe_zone={','.join(str(value) for value in geometry['safe_zone'])} "
            f"right_module_start_range={geometry['right_module_start_range'][0]}..{geometry['right_module_start_range'][1]} "
            f"below_module_start_range={geometry['below_module_start_range'][0]}..{geometry['below_module_start_range'][1]}"
        )
    geometry_path = None
    if args.geometry_json:
        try:
            geometry_path = validate_auxiliary_json_path(
                args.geometry_json,
                {
                    input_path,
                    logo_path,
                    output_path,
                    *(path.resolve() for path in files),
                    *(path.resolve() for _, path, _ in tasks),
                },
            )
        except ValueError as exc:
            parser.error(str(exc))
    if geometry_path:
        if geometry_path.suffix.lower() != ".json":
            parser.error("geometry-json must use a .json suffix")
        if geometry_path.exists() and not args.overwrite:
            parser.error("geometry-json exists; use --overwrite")
    if geometry_path:
        try:
            atomic_json(geometry_path, {
                "schema_version": 1,
                "producer": "xobi-img.apply_logo",
                "contract": "locked-logo-v1",
                "logo": str(logo_path),
                "logo_sha256": sha256_file(logo_path),
                "reference_short_side": args.reference_short_side,
                "reference_box": [args.logo_reference_width, args.logo_reference_height],
                "alpha_threshold": args.alpha_threshold,
                "safe_padding": args.safe_padding,
                "anchor_tolerance": args.anchor_tolerance,
                "opaque_review_approved": args.opaque_approved,
                "items": geometries,
            })
        except OSError as exc:
            parser.error(f"could not write geometry JSON: {exc}")
    if args.dry_run:
        print(f"written=0 skipped=0 dry_run=True")
        return 0

    pending_tasks: list[tuple[Path, Path, Image.Image]] = []
    for task in tasks:
        if task[1].exists() and not args.overwrite:
            skipped += 1
        else:
            pending_tasks.append(task)
    try:
        if input_path.is_file():
            source, output, overlay = pending_tasks[0] if pending_tasks else tasks[0]
            if pending_tasks:
                with Image.open(source) as raw:
                    base = ImageOps.exif_transpose(raw).convert("RGBA")
                base.alpha_composite(overlay, (0, 0))
                save_image(base, output, args.min_kb, args.max_kb)
                written = 1
        elif pending_tasks:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".xobi-logo-stage-", dir=output_path.parent) as temporary:
                stage_root = Path(temporary)
                staged: list[tuple[Path, Path]] = []
                for source, output, overlay in pending_tasks:
                    with Image.open(source) as raw:
                        base = ImageOps.exif_transpose(raw).convert("RGBA")
                    base.alpha_composite(overlay, (0, 0))
                    stage = stage_root / source.relative_to(root)
                    save_image(base, stage, args.min_kb, args.max_kb)
                    staged.append((stage, output))
                for stage, output in staged:
                    output.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(stage, output)
                    written += 1
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        parser.error(f"could not write Logo output batch: {exc}")

    print(f"written={written} skipped={skipped} dry_run={args.dry_run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
