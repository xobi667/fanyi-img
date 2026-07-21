#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path

from PIL import Image, UnidentifiedImageError


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
MODES = {"edit", "localization"}
SCHEMA_VERSION = 1


def safe_name(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", value.strip())
    cleaned = re.sub(r"\s+", "-", cleaned).strip("-. ")
    return cleaned[:64] or "image-task"


def image_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root] if root.suffix.lower() in IMAGE_EXTS else []
    return sorted(
        p for p in root.rglob("*")
        if p.is_file()
        and p.suffix.lower() in IMAGE_EXTS
        and "xobi-img-output" not in p.parts
    )


def unique_task_dir(base: Path, name: str) -> Path:
    candidate = base / name
    suffix = 1
    while candidate.exists():
        candidate = base / f"{name}-{suffix:02d}"
        suffix += 1
    return candidate


def extract_zip_images(archive: Path, destination: Path) -> list[Path]:
    extracted: list[Path] = []
    destination_resolved = destination.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            if member.is_dir():
                continue
            raw = member.filename.replace("\\", "/")
            parts = Path(raw).parts
            if not parts or raw.startswith("/") or ".." in parts or "__MACOSX" in parts:
                continue
            if Path(raw).suffix.lower() not in IMAGE_EXTS:
                continue
            target = (destination / Path(*parts)).resolve()
            if destination_resolved not in target.parents:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            extracted.append(target)
    return sorted(extracted)


def inspect(path: Path) -> dict[str, object]:
    try:
        with Image.open(path) as image:
            return {
                "width": image.width,
                "height": image.height,
                "format": image.format or "",
                "has_alpha": image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info),
                "inspect_error": None,
            }
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        return {"width": 0, "height": 0, "format": "", "has_alpha": False, "inspect_error": str(exc)}


def atomic_json(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def write_report(path: Path, manifest: dict[str, object]) -> None:
    items = manifest["items"]
    counts: dict[str, int] = {}
    for item in items:
        status = str(item["status"])
        counts[status] = counts.get(status, 0) + 1
    lines = [
        "# xobi-img Task Report",
        "",
        f"- Mode: {manifest['mode']}",
        f"- Operation: {manifest['operation']}",
        f"- Ratio: {manifest['ratio']}",
        f"- Target language: {manifest.get('target_language') or 'N/A'}",
        f"- Input: {manifest['input']}",
        f"- Task directory: {manifest['task_dir']}",
        f"- Total: {len(items)}",
        f"- Pending: {counts.get('pending', 0)}",
        f"- Success: {counts.get('success', 0)}",
        f"- Skipped: {counts.get('skipped', 0)}",
        f"- Failed: {counts.get('failed', 0)}",
        "",
        "## Items",
        "",
        "| Task | Worker | Source | Output | Status | Error |",
        "|---|---|---|---|---|---|",
    ]
    for item in items:
        source = str(item["source"]).replace("|", "\\|")
        output = str(item["output"]).replace("|", "\\|")
        error = str(item.get("error") or "").replace("|", "\\|")
        lines.append(f"| {item['task_id']} | {item['worker_id']} | {source} | {output} | {item['status']} | {error} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Create an xobi-img batch task, manifest, report, and isolated worker assignments.")
    parser.add_argument("--input", required=True, type=Path, help="Input image or directory.")
    parser.add_argument("--mode", required=True, choices=sorted(MODES))
    parser.add_argument("--operation", required=True, help="Confirmed operation summary.")
    parser.add_argument("--ratio", required=True, help="Confirmed ratio, dimensions, or 'original'.")
    parser.add_argument("--target-language", help="Required for localization.")
    parser.add_argument("--output-root", type=Path, help="Parent output directory. Default: beside input/xobi-img-output.")
    parser.add_argument("--task-name", help="Safe task directory name.")
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=4, metavar="1-4")
    args = parser.parse_args()

    source = args.input.resolve()
    if not source.exists():
        parser.error(f"input not found: {source}")
    if args.mode == "localization" and not args.target_language:
        parser.error("--target-language is required for localization")
    if not args.operation.strip() or not args.ratio.strip():
        parser.error("operation and ratio must be non-empty")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    task_label = safe_name(args.task_name or f"{source.stem}-{args.mode}")
    base = args.output_root.resolve() if args.output_root else source.parent / "xobi-img-output"
    task_dir = unique_task_dir(base, f"{task_label}-{timestamp}")
    source_dir = task_dir / "source"
    output_dir = task_dir / "output"
    work_dir = task_dir / "work"
    for directory in (source_dir, output_dir, work_dir):
        directory.mkdir(parents=True, exist_ok=False)

    if source.is_file() and source.suffix.lower() == ".zip":
        files = extract_zip_images(source, source_dir)
        root = source_dir
    else:
        files = image_files(source)
        root = source if source.is_dir() else source.parent
        (source_dir / "source_paths.json").write_text(
            json.dumps([str(path) for path in files], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if not files:
        shutil.rmtree(task_dir)
        parser.error("no supported images found")
    active_workers = min(args.workers, len(files))
    items: list[dict[str, object]] = []
    for index, path in enumerate(files, start=1):
        rel = path.relative_to(root)
        output = (output_dir / rel).with_suffix(".png")
        info = inspect(path)
        items.append({
            "task_id": f"task-{index:06d}",
            "worker_id": f"worker-{((index - 1) % active_workers) + 1}",
            "source": str(path),
            "relative_path": rel.as_posix(),
            "role": "target",
            "output": str(output),
            "status": "pending",
            "attempts": 0,
            "prompt_summary": "",
            "error": None,
            **info,
        })

    manifest: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "host": "auto",
        "mode": args.mode,
        "operation": args.operation.strip(),
        "ratio": args.ratio.strip(),
        "target_language": args.target_language,
        "input": str(source),
        "task_dir": str(task_dir),
        "output_dir": str(output_dir),
        "workers": active_workers,
        "style_lock": None,
        "items": items,
    }
    atomic_json(task_dir / "manifest.json", manifest)
    write_report(task_dir / "report.md", manifest)
    print(f"task_dir={task_dir}")
    print(f"manifest={task_dir / 'manifest.json'}")
    print(f"report={task_dir / 'report.md'}")
    print(f"images={len(items)} workers={active_workers}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
