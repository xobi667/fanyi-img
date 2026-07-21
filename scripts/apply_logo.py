#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
DEFAULT_LOGO = Path(__file__).resolve().parent.parent / "assets" / "we-film-logo-template.png"


def clean_logo(path: Path, alpha_threshold: int) -> Image.Image:
    with Image.open(path) as source:
        logo = source.convert("RGBA")
    alpha = logo.getchannel("A").point(lambda value: 0 if value < alpha_threshold else value)
    logo.putalpha(alpha)
    return logo


def scaled_overlay(logo: Image.Image, canvas_size: tuple[int, int], reference_short_side: int) -> Image.Image:
    scale = min(canvas_size) / reference_short_side
    size = (max(1, round(logo.width * scale)), max(1, round(logo.height * scale)))
    return logo.resize(size, Image.Resampling.LANCZOS)


def safe_zone(overlay: Image.Image, padding: int, alpha_threshold: int) -> tuple[int, int, int, int]:
    alpha = overlay.getchannel("A").point(lambda value: 255 if value >= alpha_threshold else 0)
    bbox = alpha.getbbox() or (0, 0, 0, 0)
    return (0, 0, min(overlay.width, bbox[2] + padding), min(overlay.height, bbox[3] + padding))


def iter_inputs(path: Path) -> list[Path]:
    if path.is_file():
        return [path] if path.suffix.lower() in IMAGE_EXTS else []
    return sorted(item for item in path.rglob("*") if item.is_file() and item.suffix.lower() in IMAGE_EXTS)


def jpeg_bytes(image: Image.Image, quality: int) -> bytes:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, "JPEG", quality=quality, optimize=True, progressive=True, subsampling=0)
    return buffer.getvalue()


def save_image(image: Image.Image, output: Path, min_kb: int | None, max_kb: int | None) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix.lower() in {".jpg", ".jpeg"}:
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
        output.write_bytes(best)
    else:
        image.save(output)


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministically overlay the xobi-img logo template after visual safe-zone approval.")
    parser.add_argument("--input", required=True, type=Path, help="Approved base image or directory.")
    parser.add_argument("--output", required=True, type=Path, help="Output image or directory; must differ from input.")
    parser.add_argument("--logo", type=Path, default=DEFAULT_LOGO)
    parser.add_argument("--reference-short-side", type=int, default=4000)
    parser.add_argument("--alpha-threshold", type=int, default=10)
    parser.add_argument("--safe-padding", type=int, default=16)
    parser.add_argument("--safe-zone-approved", action="store_true", help="Confirm visual review found no text/product/important content in the printed safe zone.")
    parser.add_argument("--dry-run", action="store_true", help="Print scaled logo and safe-zone geometry without writing output.")
    parser.add_argument("--min-kb", type=int, help="Optional JPEG minimum size.")
    parser.add_argument("--max-kb", type=int, help="Optional JPEG maximum size.")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    input_path = args.input.resolve()
    output_path = args.output.resolve()
    logo_path = args.logo.resolve()
    if not input_path.exists() or not logo_path.is_file():
        parser.error("input or logo not found")
    if input_path == output_path:
        parser.error("output must differ from input")
    if args.reference_short_side < 1 or not 0 <= args.alpha_threshold <= 255 or args.safe_padding < 0:
        parser.error("invalid geometry settings")
    if args.min_kb and args.max_kb and args.max_kb < args.min_kb:
        parser.error("max-kb must be >= min-kb")
    if not args.dry_run and not args.safe_zone_approved:
        parser.error("refusing to add logo without --safe-zone-approved after visual review")

    files = iter_inputs(input_path)
    if not files:
        parser.error("no supported input images")
    logo = clean_logo(logo_path, args.alpha_threshold)
    root = input_path if input_path.is_dir() else input_path.parent
    written = 0
    skipped = 0
    for source in files:
        with Image.open(source) as raw:
            base = ImageOps.exif_transpose(raw).convert("RGBA")
        overlay = scaled_overlay(logo, base.size, args.reference_short_side)
        zone = safe_zone(overlay, args.safe_padding, args.alpha_threshold)
        print(f"source={source} canvas={base.width}x{base.height} logo_canvas={overlay.width}x{overlay.height} safe_zone={zone[0]},{zone[1]},{zone[2]},{zone[3]}")
        if args.dry_run:
            continue
        output = output_path if input_path.is_file() else output_path / source.relative_to(root)
        if output.exists() and not args.overwrite:
            skipped += 1
            continue
        base.alpha_composite(overlay, (0, 0))
        save_image(base, output, args.min_kb, args.max_kb)
        written += 1
    print(f"written={written} skipped={skipped} dry_run={args.dry_run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
