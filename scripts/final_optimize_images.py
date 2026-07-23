#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError


IMAGE_EXTS = {".jpg", ".jpeg", ".jfif", ".png", ".webp", ".bmp", ".tif", ".tiff"}
REPORT_NAME = "fanyi_optimize_report.txt"
FAILED_NAME = "fanyi_optimize_failed.txt"


@dataclass
class OptimizeResult:
    status: str
    source: Path
    output: Path
    before_bytes: int = 0
    after_bytes: int = 0
    quality: int | None = None
    padded_bytes: int = 0
    reason: str = ""


def parse_size(value: str) -> tuple[int, int]:
    value = value.lower().replace("*", "x").strip()
    if "x" in value:
        left, right = value.split("x", 1)
        width, height = int(left), int(right)
    else:
        width = height = int(value)
    if width < 1 or height < 1:
        raise argparse.ArgumentTypeError("size must be positive")
    return width, height


def iter_files(input_dir: Path) -> list[Path]:
    return sorted(p for p in input_dir.rglob("*") if p.is_file())


def image_to_canvas(path: Path, canvas_size: tuple[int, int]) -> Image.Image:
    with Image.open(path) as src:
        img = ImageOps.exif_transpose(src)
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            rgba = img.convert("RGBA")
            white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            white.alpha_composite(rgba)
            img = white.convert("RGB")
        else:
            img = img.convert("RGB")

        target_w, target_h = canvas_size
        scale = min(target_w / img.width, target_h / img.height)
        resized_w = max(1, round(img.width * scale))
        resized_h = max(1, round(img.height * scale))
        resized = img.resize((resized_w, resized_h), Image.Resampling.LANCZOS)

        canvas = Image.new("RGB", (target_w, target_h), (255, 255, 255))
        x = (target_w - resized_w) // 2
        y = (target_h - resized_h) // 2
        canvas.paste(resized, (x, y))
        return canvas


def jpeg_bytes(img: Image.Image, quality: int) -> bytes:
    buf = io.BytesIO()
    img.save(
        buf,
        format="JPEG",
        quality=quality,
        optimize=True,
        progressive=True,
        subsampling=0,
    )
    return buf.getvalue()


def best_jpeg_under_max(img: Image.Image, max_bytes: int) -> tuple[int, bytes]:
    lo, hi = 1, 100
    best_quality = 1
    best_data = jpeg_bytes(img, 1)

    while lo <= hi:
        mid = (lo + hi) // 2
        data = jpeg_bytes(img, mid)
        if len(data) <= max_bytes:
            best_quality = mid
            best_data = data
            lo = mid + 1
        else:
            hi = mid - 1

    return best_quality, best_data


def fit_min_size(data: bytes, min_bytes: int, max_bytes: int) -> tuple[bytes, int]:
    if len(data) >= min_bytes:
        return data, 0
    target = min(min_bytes + 128, max_bytes)
    pad = max(0, target - len(data))
    if pad:
        return data + (b"\0" * pad), pad
    return data, 0


def validate_existing_output(path: Path, canvas_size: tuple[int, int], min_bytes: int, max_bytes: int) -> str:
    size = path.stat().st_size
    if not (min_bytes <= size <= max_bytes):
        return f"Existing output size {size} is outside target range"
    try:
        with Image.open(path) as img:
            if img.format != "JPEG":
                return f"Existing output format is {img.format}, expected JPEG"
            if img.size != canvas_size:
                return f"Existing output size is {img.width}x{img.height}, expected {canvas_size[0]}x{canvas_size[1]}"
    except (UnidentifiedImageError, OSError) as exc:
        return str(exc)
    return ""


def optimize_one(
    source: Path,
    output: Path,
    canvas_size: tuple[int, int],
    min_bytes: int,
    max_bytes: int,
    overwrite: bool,
) -> OptimizeResult:
    if output.exists() and output.stat().st_size > 0 and not overwrite:
        reason = validate_existing_output(output, canvas_size, min_bytes, max_bytes)
        if reason:
            return OptimizeResult("failed", source, output, source.stat().st_size, output.stat().st_size, None, 0, reason)
        return OptimizeResult("skipped_existing", source, output, source.stat().st_size, output.stat().st_size)

    output.parent.mkdir(parents=True, exist_ok=True)
    before = source.stat().st_size

    try:
        canvas = image_to_canvas(source, canvas_size)
        quality, data = best_jpeg_under_max(canvas, max_bytes)
        if len(data) > max_bytes:
            output.write_bytes(data)
            return OptimizeResult(
                "failed",
                source,
                output,
                before,
                len(data),
                quality,
                0,
                "Cannot get under max size at JPEG quality 1",
            )

        data, padded = fit_min_size(data, min_bytes, max_bytes)
        output.write_bytes(data)
        after = output.stat().st_size
        status = "optimized" if min_bytes <= after <= max_bytes else "failed"
        reason = "" if status == "optimized" else "Output size outside target range"
        return OptimizeResult(status, source, output, before, after, quality, padded, reason)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        return OptimizeResult("failed", source, output, before, 0, None, 0, str(exc))


def copy_non_image(source: Path, output: Path, overwrite: bool) -> OptimizeResult:
    if output.exists() and output.stat().st_size > 0 and not overwrite:
        return OptimizeResult("skipped_existing", source, output)
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, output)
    return OptimizeResult("copied_non_image", source, output, source.stat().st_size, output.stat().st_size)


def write_reports(output_dir: Path, results: list[OptimizeResult], args: argparse.Namespace) -> None:
    optimized = [r for r in results if r.status == "optimized"]
    failed = [r for r in results if r.status == "failed"]
    skipped = [r for r in results if r.status == "skipped_existing"]
    copied = [r for r in results if r.status == "copied_non_image"]

    report = output_dir / REPORT_NAME
    lines = [
        f"Input dir: {args.input}",
        f"Output dir: {args.output}",
        f"Canvas size: {args.size[0]}x{args.size[1]}",
        f"Target size: {args.min_kb}KB-{args.max_kb}KB",
        f"Total files: {len(results)}",
        f"Optimized images: {len(optimized)}",
        f"Copied non-images: {len(copied)}",
        f"Skipped existing: {len(skipped)}",
        f"Failed: {len(failed)}",
        "",
        "status\tsource\toutput\tbefore_bytes\tafter_bytes\tquality\tpadded_bytes\treason",
    ]
    for r in results:
        lines.append(
            "\t".join(
                [
                    r.status,
                    str(r.source),
                    str(r.output),
                    str(r.before_bytes),
                    str(r.after_bytes),
                    "" if r.quality is None else str(r.quality),
                    str(r.padded_bytes),
                    r.reason,
                ]
            )
        )
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")

    failed_path = output_dir / FAILED_NAME
    if failed:
        failed_lines = ["source\toutput\treason"]
        failed_lines.extend(f"{r.source}\t{r.output}\t{r.reason}" for r in failed)
        failed_path.write_text("\n".join(failed_lines) + "\n", encoding="utf-8")
    elif failed_path.exists():
        failed_path.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description="Final image size optimizer for fanyi outputs.")
    parser.add_argument("--input", required=True, type=Path, help="Directory containing translated images.")
    parser.add_argument("--output", required=True, type=Path, help="Directory for optimized final files.")
    parser.add_argument("--size", type=parse_size, default=(800, 800), help="Output canvas size. Default: 800x800.")
    parser.add_argument("--min-kb", type=int, default=900, help="Minimum target file size in KB. Default: 900.")
    parser.add_argument("--max-kb", type=int, default=1024, help="Maximum target file size in KB. Default: 1024.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing optimized files.")
    parser.add_argument("--no-copy-non-images", action="store_true", help="Do not copy non-image files.")
    args = parser.parse_args()

    args.input = args.input.resolve()
    args.output = args.output.resolve()
    if not args.input.exists() or not args.input.is_dir():
        print(f"Input directory not found: {args.input}", file=sys.stderr)
        return 2
    if args.input == args.output:
        print("Input and output directories must be different.", file=sys.stderr)
        return 2
    if args.min_kb < 1 or args.max_kb < args.min_kb:
        print("Invalid target size range.", file=sys.stderr)
        return 2

    min_bytes = args.min_kb * 1024
    max_bytes = args.max_kb * 1024
    args.output.mkdir(parents=True, exist_ok=True)

    results: list[OptimizeResult] = []
    for source in iter_files(args.input):
        rel = source.relative_to(args.input)
        if rel.name in {REPORT_NAME, FAILED_NAME}:
            continue
        if source.suffix.lower() in IMAGE_EXTS:
            output = (args.output / rel).with_suffix(".jpg")
            results.append(optimize_one(source, output, args.size, min_bytes, max_bytes, args.overwrite))
        elif not args.no_copy_non_images:
            results.append(copy_non_image(source, args.output / rel, args.overwrite))

    write_reports(args.output, results, args)
    failed_count = sum(1 for r in results if r.status == "failed")
    optimized_count = sum(1 for r in results if r.status == "optimized")
    print(f"optimized={optimized_count} failed={failed_count} output={args.output}")
    return 1 if failed_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
