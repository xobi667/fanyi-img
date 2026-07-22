#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
REPORT_NAME = "xobi_img_optimize_report.md"


@dataclass
class Result:
    status: str
    source: Path
    output: Path
    before: int = 0
    after: int = 0
    reason: str = ""


def parse_size(value: str) -> tuple[int, int]:
    raw = value.lower().replace("*", "x").strip()
    if "x" not in raw:
        raise argparse.ArgumentTypeError("size must be WIDTHxHEIGHT")
    width, height = (int(part) for part in raw.split("x", 1))
    if width < 1 or height < 1:
        raise argparse.ArgumentTypeError("size must be positive")
    return width, height


def render(path: Path, size: tuple[int, int]) -> Image.Image:
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGBA")
        background = Image.new("RGBA", image.size, "white")
        background.alpha_composite(image)
        rgb = background.convert("RGB")
        contained = ImageOps.contain(rgb, size, Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", size, "white")
        canvas.paste(contained, ((size[0] - contained.width) // 2, (size[1] - contained.height) // 2))
        return canvas


def jpeg_data(image: Image.Image, quality: int) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=quality, optimize=True, progressive=True, subsampling=0)
    return buffer.getvalue()


def fit(image: Image.Image, min_bytes: int, max_bytes: int) -> bytes:
    low, high, best = 1, 100, jpeg_data(image, 1)
    while low <= high:
        quality = (low + high) // 2
        candidate = jpeg_data(image, quality)
        if len(candidate) <= max_bytes:
            best = candidate
            low = quality + 1
        else:
            high = quality - 1
    if len(best) > max_bytes:
        raise ValueError("cannot fit JPEG under maximum size")
    if len(best) < min_bytes:
        best += b"\0" * (min_bytes - len(best))
    return best


def is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def allocate_outputs(sources: list[Path], input_dir: Path, output_dir: Path) -> dict[Path, Path]:
    groups: dict[str, list[Path]] = {}
    for source in sources:
        relative = source.relative_to(input_dir).with_suffix(".jpg")
        key = os.path.normcase(relative.as_posix()).casefold()
        groups.setdefault(key, []).append(source)
    allocated: dict[Path, Path] = {}
    used: set[str] = set()
    for key in sorted(groups):
        group = sorted(groups[key], key=lambda path: path.suffix.lower() not in {".jpg", ".jpeg"})
        for source in group:
            relative = source.relative_to(input_dir).with_suffix(".jpg")
            output_key = os.path.normcase(relative.as_posix()).casefold()
            if output_key in used:
                suffix = source.suffix.lower().lstrip(".") or "source"
                relative = relative.with_name(f"{relative.stem}-{suffix}.jpg")
                output_key = os.path.normcase(relative.as_posix()).casefold()
            if output_key in used:
                raise ValueError(f"could not allocate a unique output for {source}")
            used.add(output_key)
            allocated[source] = output_dir / relative
    return allocated


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic xobi-img canvas/JPEG optimizer.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--preset", choices=["localization-square"])
    parser.add_argument("--size", type=parse_size)
    parser.add_argument("--min-kb", type=int)
    parser.add_argument("--max-kb", type=int)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    if args.preset == "localization-square":
        size = args.size or (800, 800)
        min_kb = args.min_kb or 900
        max_kb = args.max_kb or 1024
    else:
        if not args.size:
            parser.error("--size is required without a preset")
        size = args.size
        min_kb = args.min_kb or 1
        max_kb = args.max_kb or 10240
    if min_kb < 1 or max_kb < min_kb:
        parser.error("invalid size range")

    input_dir, output_dir = args.input.resolve(), args.output.resolve()
    if not input_dir.is_dir() or input_dir == output_dir:
        parser.error("input must be a directory and differ from output")
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[Result] = []
    sources = [
        path for path in sorted(input_dir.rglob("*"))
        if path.is_file()
        and path.suffix.lower() in IMAGE_EXTS
        and ".xobi" not in {part.casefold() for part in path.relative_to(input_dir).parts}
        and not (is_inside(output_dir, input_dir) and is_inside(path, output_dir))
    ]
    try:
        outputs = allocate_outputs(sources, input_dir, output_dir)
    except ValueError as exc:
        parser.error(str(exc))
    for source in sources:
        output = outputs[source]
        if output.exists() and not args.overwrite:
            results.append(Result("skipped", source, output, source.stat().st_size, output.stat().st_size))
            continue
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = fit(render(source, size), min_kb * 1024, max_kb * 1024)
            output.write_bytes(data)
            results.append(Result("success", source, output, source.stat().st_size, len(data)))
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            results.append(Result("failed", source, output, source.stat().st_size, 0, str(exc)))

    lines = ["# xobi-img Optimize Report", "", f"- Size: {size[0]}x{size[1]}", f"- Files: {len(results)}", "", "| Status | Source | Output | Bytes | Reason |", "|---|---|---|---:|---|"]
    for result in results:
        lines.append(f"| {result.status} | {result.source} | {result.output} | {result.after} | {result.reason} |")
    (output_dir / REPORT_NAME).write_text("\n".join(lines) + "\n", encoding="utf-8")
    failed = sum(result.status == "failed" for result in results)
    print(f"success={sum(result.status == 'success' for result in results)} failed={failed} output={output_dir}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
