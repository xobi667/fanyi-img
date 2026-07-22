#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import uuid
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


def load_label_font(size: int = 18) -> tuple[ImageFont.ImageFont, bool]:
    candidates = [
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "msyh.ttc",
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "simhei.ttf",
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
        Path("/System/Library/Fonts/PingFang.ttc"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            try:
                return ImageFont.truetype(str(candidate), size=size), True
            except OSError:
                continue
    return ImageFont.load_default(), False


def iter_images(root: Path) -> list[Path]:
    return [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.suffix.lower() in IMAGE_EXTS
        and ".xobi" not in {part.casefold() for part in path.relative_to(root).parts}
        and "xobi-img-output" not in {part.casefold() for part in path.relative_to(root).parts}
    ]


def thumbnail(path: Path, size: int) -> Image.Image | None:
    try:
        with Image.open(path) as source:
            return ImageOps.contain(ImageOps.exif_transpose(source).convert("RGB"), (size, size))
    except (UnidentifiedImageError, OSError, ValueError):
        return None


def draw_thumbnail(
    sheet: Image.Image,
    draw: ImageDraw.ImageDraw,
    path: Path | None,
    left: int,
    top: int,
    size: int,
    label: str,
    font: ImageFont.ImageFont,
) -> bool:
    draw.rectangle((left, top, left + size, top + size), outline="#d0d0d0", width=1)
    rendered = thumbnail(path, size) if path else None
    if rendered:
        sheet.paste(rendered, (left + (size - rendered.width) // 2, top + (size - rendered.height) // 2))
    else:
        draw.text((left + 10, top + size // 2 - 10), "N/A", fill="#888888", font=font)
    draw.text((left, top + size + 7), label, fill="black", font=font)
    return rendered is not None


def standard_sheet(root: Path, thumb_size: int, columns: int, max_pixels: int) -> tuple[Image.Image, int, int]:
    images = iter_images(root)
    if not images:
        raise ValueError("no images found")
    label_height = 34
    padding = 16
    cell_width = thumb_size + padding * 2
    cell_height = thumb_size + label_height + padding * 2
    rows = math.ceil(len(images) / columns)
    sheet_size = (cell_width * columns, cell_height * rows)
    if sheet_size[0] * sheet_size[1] > max_pixels:
        raise ValueError(f"contact sheet would exceed max-pixels: {sheet_size[0]}x{sheet_size[1]}")
    sheet = Image.new("RGB", sheet_size, "white")
    draw = ImageDraw.Draw(sheet)
    font, cjk_labels = load_label_font()
    loaded = 0
    for index, path in enumerate(images):
        column = index % columns
        row = index // columns
        left = column * cell_width + padding
        top = row * cell_height + padding
        label = str(path.relative_to(root)).replace("\\", "/")
        if len(label) > 46:
            label = "..." + label[-43:]
        if not cjk_labels:
            label = f"image-{index + 1:04d} {path.suffix.lower()}"
        if draw_thumbnail(sheet, draw, path, left, top, thumb_size, label, font):
            loaded += 1
    return sheet, loaded, rows


def item_path(item: dict[str, Any], key: str) -> Path | None:
    value = item.get(key)
    if not value:
        return None
    path = Path(str(value)).resolve()
    return path if path.is_file() else None


def manifest_stage_titles(items: list[dict[str, Any]]) -> tuple[str, ...]:
    if any(item.get("conflict_reference_base") for item in items):
        return ("SOURCE", "CONFLICT BASE", "PREPARED", "FINAL")
    return ("SOURCE", "BASE", "FINAL")


def manifest_stage_cells(
    item: dict[str, Any],
    include_conflict_stage: bool,
) -> tuple[tuple[Path | None, str], ...]:
    task_id = str(item.get("task_id") or "")
    source = item_path(item, "source")
    final = item_path(item, "output")
    final_label = f"{task_id} final [{item.get('status', '')}]"
    if include_conflict_stage:
        prepared = item_path(item, "prepared_base")
        prepared_label = f"{task_id} prepared_base"
        if prepared is None:
            prepared = item_path(item, "localized_base") or item_path(item, "base_output")
            prepared_label = f"{task_id} base"
        return (
            (source, f"{task_id} source"),
            (item_path(item, "conflict_reference_base"), f"{task_id} conflict_reference_base"),
            (prepared, prepared_label),
            (final, final_label),
        )
    middle = (
        item_path(item, "prepared_base")
        or item_path(item, "localized_base")
        or item_path(item, "base_output")
    )
    return (
        (source, f"{task_id} source"),
        (middle, f"{task_id} base"),
        (final, final_label),
    )


def triptych_sheet(manifest_path: Path, thumb_size: int, max_pixels: int) -> tuple[Image.Image, int, int]:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = list(data.get("items", []))
    if not items:
        raise ValueError("manifest contains no target items")
    items.sort(key=lambda item: (str(item.get("family_id") or "ungrouped"), str(item.get("task_id") or "")))
    padding = 18
    label_height = 38
    header_height = 42
    family_height = 34
    cell_width = thumb_size + padding * 2
    row_height = thumb_size + label_height + padding * 2
    families: list[str] = []
    for item in items:
        family = str(item.get("family_id") or "ungrouped")
        if family not in families:
            families.append(family)
    stage_titles = manifest_stage_titles(items)
    include_conflict_stage = len(stage_titles) == 4
    width = cell_width * len(stage_titles)
    height = header_height + len(families) * family_height + len(items) * row_height
    if width * height > max_pixels:
        raise ValueError(f"contact sheet would exceed max-pixels: {width}x{height}")
    sheet = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(sheet)
    font, _ = load_label_font()
    header_font, _ = load_label_font(20)
    for column, title in enumerate(stage_titles):
        draw.text((column * cell_width + padding, 10), title, fill="black", font=header_font)
    y = header_height
    loaded = 0
    for family in families:
        members = [item for item in items if str(item.get("family_id") or "ungrouped") == family]
        draw.rectangle((0, y, width, y + family_height), fill="#f0f3f6")
        draw.text((padding, y + 7), f"FAMILY: {family}", fill="#202020", font=font)
        y += family_height
        for item in members:
            for column, (path, label) in enumerate(manifest_stage_cells(item, include_conflict_stage)):
                if draw_thumbnail(sheet, draw, path, column * cell_width + padding, y + padding, thumb_size, label, font):
                    loaded += 1
            y += row_height
    return sheet, loaded, len(items)


def atomic_save_jpeg(sheet: Image.Image, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}.jpg")
    try:
        sheet.save(temporary, format="JPEG", quality=90, optimize=True)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def manifest_protected_paths(manifest_path: Path) -> set[Path]:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    protected = {manifest_path.resolve()}

    def protect(value: object) -> None:
        if value:
            protected.add(Path(str(value)).resolve())

    logo = data.get("logo") or {}
    for key in ("source", "normalized"):
        protect(logo.get(key))
    for field in ("logo_plan", "layout_families", "style_lock"):
        registration = data.get(field) or {}
        if isinstance(registration, dict):
            protect(registration.get("path"))
    for item in data.get("items", []):
        for key in (
            "source",
            "base_output",
            "localized_base",
            "conflict_reference_base",
            "prepared_base",
            "output",
        ):
            protect(item.get(key))
        localization_registration = item.get("localization_plan_registration") or {}
        if isinstance(localization_registration, dict):
            protect(localization_registration.get("path"))
        composition = item.get("localization_composition") or {}
        if isinstance(composition, dict):
            protect(composition.get("artifact_path"))
            record = composition.get("record") or {}
            if isinstance(record, dict):
                protect(record.get("raw_edit_candidate"))
        logo_geometry = item.get("logo_geometry") or {}
        if isinstance(logo_geometry, dict):
            protect(logo_geometry.get("artifact_path"))
    return protected


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a final-only grid or family-grouped staged QA contact sheet.")
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--input", type=Path, help="Final image directory for a standard grid.")
    inputs.add_argument("--manifest", type=Path, help="Manifest for a family-grouped three- or four-stage QA sheet.")
    parser.add_argument("--output", required=True, type=Path, help="Output contact-sheet JPG path.")
    parser.add_argument("--thumb", default=320, type=int, help="Thumbnail box size. Default: 320.")
    parser.add_argument("--columns", default=4, type=int, help="Columns for standard grid mode. Default: 4.")
    parser.add_argument("--max-pixels", default=200_000_000, type=int, help="Maximum contact-sheet canvas pixels. Default: 200 million.")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    output = args.output.resolve()
    if args.thumb < 64 or args.columns < 1 or args.max_pixels < 1:
        parser.error("thumb must be >= 64, columns >= 1, and max-pixels positive")
    if output.suffix.lower() not in {".jpg", ".jpeg"}:
        parser.error("contact-sheet output must use a .jpg or .jpeg suffix")
    if output.exists() and not args.overwrite:
        parser.error("output exists; use --overwrite")
    try:
        if args.manifest:
            manifest = args.manifest.resolve()
            if not manifest.is_file():
                parser.error("manifest not found")
            if output in manifest_protected_paths(manifest):
                parser.error("output must not overwrite the manifest, Logo, source, base, or final image")
            sheet, loaded, rows = triptych_sheet(manifest, args.thumb, args.max_pixels)
            mode = "triptych"
        else:
            root = args.input.resolve()
            if not root.is_dir():
                parser.error("input must be a directory")
            try:
                relative_output = output.relative_to(root)
            except ValueError:
                relative_output = None
            if relative_output is not None and ".xobi" not in {part.casefold() for part in relative_output.parts}:
                parser.error("standard-mode output inside the input tree must be placed under .xobi")
            if output in {path.resolve() for path in iter_images(root)}:
                parser.error("output must not overwrite an input image")
            sheet, loaded, rows = standard_sheet(root, args.thumb, args.columns, args.max_pixels)
            mode = "standard"
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    if loaded == 0:
        parser.error("no readable images found")
    try:
        atomic_save_jpeg(sheet, output)
    except OSError as exc:
        parser.error(f"could not write contact sheet: {exc}")
    print(f"contact_sheet={output}")
    print(f"mode={mode} images_loaded={loaded} rows={rows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
