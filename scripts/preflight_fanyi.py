#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps, ImageStat, UnidentifiedImageError


IMAGE_EXTS = {".jpg", ".jpeg", ".jfif", ".png", ".webp", ".bmp", ".tif", ".tiff"}
LANG_SUFFIXES = ["英语", "泰语", "印尼语", "越南语", "马来语", "西班牙语", "阿拉伯语"]
REPORT_NAME = "fanyi_preflight_report.txt"
DEFAULT_WORKERS = 4


@dataclass
class ImageInfo:
    source: Path
    rel: Path
    width: int = 0
    height: int = 0
    fmt: str = ""
    mode: str = ""
    bytes_size: int = 0
    has_alpha: bool = False
    luma_stddev: float = 0.0
    edge_score: float = 0.0
    blank_like: bool = False
    visual_content: bool = False
    error: str = ""


def parse_size(value: str) -> tuple[int, int]:
    raw = value.lower().replace("*", "x").strip()
    if "x" in raw:
        left, right = raw.split("x", 1)
        size = (int(left), int(right))
    else:
        side = int(raw)
        size = (side, side)
    if size[0] < 1 or size[1] < 1:
        raise argparse.ArgumentTypeError("size must be positive")
    return size


def is_generated_dir_name(name: str) -> bool:
    if name in {"generated_images", "__pycache__"}:
        return True
    if name.endswith("-原始生图"):
        return True
    return any(name.endswith(f"-{suffix}") or name.endswith(f"-{suffix}-原始生图") for suffix in LANG_SUFFIXES)


def iter_source_files(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]

    files: list[Path] = []
    for path in sorted(p for p in input_path.rglob("*") if p.is_file()):
        rel_parts = path.relative_to(input_path).parts[:-1]
        if any(is_generated_dir_name(part) for part in rel_parts):
            continue
        files.append(path)
    return files


def safe_rel(path: Path, root: Path) -> Path:
    try:
        return path.relative_to(root)
    except ValueError:
        return Path(path.name)


def inspect_image(path: Path, rel: Path) -> ImageInfo:
    info = ImageInfo(source=path, rel=rel, bytes_size=path.stat().st_size)
    try:
        with Image.open(path) as src:
            img = ImageOps.exif_transpose(src)
            info.width, info.height = img.size
            info.fmt = src.format or ""
            info.mode = img.mode
            info.has_alpha = img.mode in {"RGBA", "LA"} or (img.mode == "P" and "transparency" in img.info)

            small = ImageOps.contain(img.convert("RGB"), (160, 160))
            luma = small.convert("L")
            stat = ImageStat.Stat(luma)
            edge = luma.filter(ImageFilter.FIND_EDGES)
            edge_stat = ImageStat.Stat(edge)
            info.luma_stddev = float(stat.stddev[0])
            info.edge_score = float(edge_stat.mean[0])

            info.blank_like = info.luma_stddev < 3.0 and info.edge_score < 1.5
            info.visual_content = not info.blank_like and (info.luma_stddev >= 8.0 or info.edge_score >= 2.5)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        info.error = str(exc)
    return info


def default_dirs(input_path: Path, target_suffix: str, output_dir: Path | None, raw_dir: Path | None) -> tuple[Path, Path]:
    if input_path.is_dir():
        final = output_dir or input_path.parent / f"{input_path.name}-{target_suffix}"
        raw = raw_dir or input_path.parent / f"{input_path.name}-{target_suffix}-原始生图"
    else:
        final = output_dir or input_path.parent
        raw = raw_dir or input_path.parent
    return final.resolve(), raw.resolve()


def expected_final_path(info: ImageInfo, input_path: Path, final_dir: Path, target_suffix: str) -> Path:
    if input_path.is_dir():
        return (final_dir / info.rel).with_suffix(".jpg")
    return final_dir / f"{info.source.stem}-{target_suffix}.jpg"


def raw_exists(info: ImageInfo, input_path: Path, raw_dir: Path, target_suffix: str) -> bool:
    if input_path.is_dir():
        base = raw_dir / info.rel.with_suffix("")
    else:
        base = raw_dir / f"{info.source.stem}-{target_suffix}-原始生图"
    candidates = [base.with_suffix(ext) for ext in sorted(IMAGE_EXTS | {".png", ".jpg", ".jpeg"})]
    return any(path.exists() and path.stat().st_size > 0 for path in candidates)


def validate_final(path: Path, final_size: tuple[int, int], min_bytes: int, max_bytes: int) -> str:
    if not path.exists():
        return "missing"
    if path.stat().st_size <= 0:
        return "invalid: empty file"

    reasons: list[str] = []
    size = path.stat().st_size
    if not (min_bytes <= size <= max_bytes):
        reasons.append(f"bytes {size} outside {min_bytes}-{max_bytes}")
    try:
        with Image.open(path) as img:
            if img.format != "JPEG":
                reasons.append(f"format {img.format}, expected JPEG")
            if img.size != final_size:
                reasons.append(f"size {img.size[0]}x{img.size[1]}, expected {final_size[0]}x{final_size[1]}")
    except (UnidentifiedImageError, OSError) as exc:
        reasons.append(str(exc))

    return "valid" if not reasons else "invalid: " + "; ".join(reasons)


def suggest_action(info: ImageInfo, require_square: bool, final_status: str, raw_status: str) -> str:
    if info.error:
        return "failed_inspect: check source image manually"

    square_ok = info.width == info.height
    needs_ratio = require_square and not square_ok
    final_ok = final_status == "valid"

    if final_ok and not needs_ratio:
        return "skip_existing_valid_final"

    parts: list[str] = []
    if needs_ratio:
        if info.visual_content:
            parts.append("Codex 1:1 edit required; if no visible text, use no-text product prompt")
        else:
            parts.append("blank/simple image: copy raw only if clean, otherwise Codex clean to 1:1")
    elif info.visual_content:
        parts.append("Codex translate/edit one image; preflight cannot OCR, manually confirm visible text")
    else:
        parts.append("blank/simple image: copy raw if clean; final optimize still required")

    if raw_status == "missing":
        parts.append("raw intermediate missing")
    if final_status == "missing":
        parts.append("final JPG missing")
    elif final_status.startswith("invalid"):
        parts.append("final JPG invalid; rerun final optimizer")
    return " | ".join(parts)


def write_report(report_path: Path, rows: list[dict[str, str]]) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "kind",
        "task_id",
        "worker_id",
        "source",
        "relative_path",
        "width",
        "height",
        "format",
        "bytes",
        "square_ok",
        "blank_like",
        "visual_content",
        "raw_status",
        "final_status",
        "expected_final",
        "suggested_action",
        "notes",
    ]
    with report_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Preflight scanner for fanyi image translation batches.")
    parser.add_argument("--input", required=True, type=Path, help="Input image or directory.")
    parser.add_argument("--target-suffix", default="英语", help="Chinese output suffix. Default: 英语.")
    parser.add_argument("--output-dir", type=Path, help="Final output directory. Default: sibling project-suffix directory.")
    parser.add_argument("--raw-dir", type=Path, help="Raw generated image directory. Default: sibling project-suffix-原始生图 directory.")
    parser.add_argument("--final-size", type=parse_size, default=(800, 800), help="Expected final JPG size. Default: 800x800.")
    parser.add_argument("--min-kb", type=int, default=900, help="Expected final minimum size in KB. Default: 900.")
    parser.add_argument("--max-kb", type=int, default=1024, help="Expected final maximum size in KB. Default: 1024.")
    parser.add_argument("--require-square", action="store_true", help="Flag non-square source images as needing 1:1 handling.")
    parser.add_argument("--report", type=Path, help=f"Report path. Default: final output directory/{REPORT_NAME}.")
    parser.add_argument("--show-limit", type=int, default=20, help="Number of suggested actions to print. Default: 20.")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, choices=range(1, DEFAULT_WORKERS + 1), metavar="1-4", help="Number of isolated worker assignments. Default: 4.")
    args = parser.parse_args()

    input_path = args.input.resolve()
    if not input_path.exists():
        print(f"Input not found: {input_path}", file=sys.stderr)
        return 2
    if args.min_kb < 1 or args.max_kb < args.min_kb:
        print("Invalid target size range.", file=sys.stderr)
        return 2

    final_dir, raw_dir = default_dirs(input_path, args.target_suffix, args.output_dir, args.raw_dir)
    report_path = (args.report.resolve() if args.report else final_dir / REPORT_NAME)
    min_bytes = args.min_kb * 1024
    max_bytes = args.max_kb * 1024

    files = iter_source_files(input_path)
    image_files = [p for p in files if p.suffix.lower() in IMAGE_EXTS]
    non_images = [p for p in files if p.suffix.lower() not in IMAGE_EXTS]

    root = input_path if input_path.is_dir() else input_path.parent
    rows: list[dict[str, str]] = []
    printed: list[dict[str, str]] = []
    next_task = 0
    worker_counts = {worker_id: 0 for worker_id in range(1, args.workers + 1)}

    counts = {
        "images": 0,
        "non_images": len(non_images),
        "blank_like": 0,
        "visual_content": 0,
        "non_square": 0,
        "raw_missing": 0,
        "final_valid": 0,
        "final_missing": 0,
        "final_invalid": 0,
        "needs_codex_ratio": 0,
    }

    for path in image_files:
        rel = safe_rel(path, root)
        info = inspect_image(path, rel)
        final_path = expected_final_path(info, input_path, final_dir, args.target_suffix)
        raw_status = "exists" if raw_exists(info, input_path, raw_dir, args.target_suffix) else "missing"
        final_status = validate_final(final_path, args.final_size, min_bytes, max_bytes)
        action = suggest_action(info, args.require_square, final_status, raw_status)
        if action == "skip_existing_valid_final":
            task_id = ""
            worker_id = ""
        else:
            task_id = f"task-{next_task + 1:06d}"
            assigned_worker = (next_task % args.workers) + 1
            worker_id = f"worker-{assigned_worker}"
            worker_counts[assigned_worker] += 1
            next_task += 1

        counts["images"] += 1
        counts["blank_like"] += int(info.blank_like)
        counts["visual_content"] += int(info.visual_content)
        counts["non_square"] += int(info.width != info.height)
        counts["raw_missing"] += int(raw_status == "missing")
        counts["final_valid"] += int(final_status == "valid")
        counts["final_missing"] += int(final_status == "missing")
        counts["final_invalid"] += int(final_status.startswith("invalid"))
        counts["needs_codex_ratio"] += int(args.require_square and info.width != info.height and info.visual_content)

        row = {
            "kind": "image",
            "task_id": task_id,
            "worker_id": worker_id,
            "source": str(path),
            "relative_path": str(rel),
            "width": str(info.width),
            "height": str(info.height),
            "format": info.fmt,
            "bytes": str(info.bytes_size),
            "square_ok": "yes" if info.width == info.height else "no",
            "blank_like": "yes" if info.blank_like else "no",
            "visual_content": "yes" if info.visual_content else "no",
            "raw_status": raw_status,
            "final_status": final_status,
            "expected_final": str(final_path),
            "suggested_action": action,
            "notes": info.error,
        }
        rows.append(row)
        if len(printed) < args.show_limit and action != "skip_existing_valid_final":
            printed.append(row)

    for path in non_images:
        rel = safe_rel(path, root)
        rows.append(
            {
                "kind": "non_image",
                "task_id": "",
                "worker_id": "",
                "source": str(path),
                "relative_path": str(rel),
                "width": "",
                "height": "",
                "format": "",
                "bytes": str(path.stat().st_size),
                "square_ok": "",
                "blank_like": "",
                "visual_content": "",
                "raw_status": "",
                "final_status": "copy_to_final_output_by_default",
                "expected_final": str(final_dir / rel),
                "suggested_action": "copy non-image unless user requested images only",
                "notes": "",
            }
        )

    write_report(report_path, rows)

    print("fanyi preflight complete")
    print(f"input={input_path}")
    print(f"final_dir={final_dir}")
    print(f"raw_dir={raw_dir}")
    print(f"report={report_path}")
    print(f"images={counts['images']} non_images={counts['non_images']}")
    print(f"workers={args.workers} assigned_tasks={next_task}")
    print("worker_assignments=" + ",".join(f"worker-{worker_id}:{worker_counts[worker_id]}" for worker_id in worker_counts))
    print(f"blank_like={counts['blank_like']} visual_content={counts['visual_content']} non_square={counts['non_square']}")
    print(f"raw_missing={counts['raw_missing']} final_valid={counts['final_valid']} final_missing={counts['final_missing']} final_invalid={counts['final_invalid']}")
    if args.require_square:
        print(f"needs_codex_ratio_edit={counts['needs_codex_ratio']}")
    print("note=preflight does not perform OCR; visible text must still be locked per image before Codex generation.")

    if printed:
        print("")
        print(f"first_suggested_actions={len(printed)}")
        for row in printed:
            print(
                f"- {row['task_id']} {row['worker_id']} {row['relative_path']} {row['width']}x{row['height']} "
                f"square={row['square_ok']} blank={row['blank_like']} final={row['final_status']} "
                f"action={row['suggested_action']}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
