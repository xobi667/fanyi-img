#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


def iter_images(root: Path, output: Path) -> list[Path]:
    output_resolved = output.resolve()
    return [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.suffix.lower() in IMAGE_EXTS
        and path.resolve() != output_resolved
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a deterministic contact sheet for batch visual QA.")
    parser.add_argument("--input", required=True, type=Path, help="Final image directory.")
    parser.add_argument("--output", required=True, type=Path, help="Output contact-sheet JPG path.")
    parser.add_argument("--thumb", default=320, type=int, help="Thumbnail box size. Default: 320.")
    parser.add_argument("--columns", default=4, type=int, help="Number of columns. Default: 4.")
    args = parser.parse_args()

    root = args.input.resolve()
    output = args.output.resolve()
    if not root.is_dir() or args.thumb < 64 or args.columns < 1:
        parser.error("input must be a directory; thumb >= 64 and columns >= 1")

    images = iter_images(root, output)
    if not images:
        parser.error("no images found")

    label_height = 34
    padding = 16
    cell_width = args.thumb + padding * 2
    cell_height = args.thumb + label_height + padding * 2
    rows = math.ceil(len(images) / args.columns)
    sheet = Image.new("RGB", (cell_width * args.columns, cell_height * rows), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()

    loaded = 0
    for index, path in enumerate(images):
        try:
            with Image.open(path) as source:
                thumb = ImageOps.contain(ImageOps.exif_transpose(source).convert("RGB"), (args.thumb, args.thumb))
        except (UnidentifiedImageError, OSError):
            continue
        column = index % args.columns
        row = index // args.columns
        left = column * cell_width + padding + (args.thumb - thumb.width) // 2
        top = row * cell_height + padding + (args.thumb - thumb.height) // 2
        sheet.paste(thumb, (left, top))
        label = str(path.relative_to(root)).replace("\\", "/")
        if len(label) > 46:
            label = "..." + label[-43:]
        draw.text((column * cell_width + padding, row * cell_height + padding + args.thumb + 8), label, fill="black", font=font)
        loaded += 1

    if loaded == 0:
        parser.error("no readable images found")
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, format="JPEG", quality=90, optimize=True)
    print(f"contact_sheet={output}")
    print(f"images={loaded}")
    print(f"columns={args.columns} rows={rows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
