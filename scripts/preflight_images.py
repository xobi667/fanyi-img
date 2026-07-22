#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import sys
import unicodedata
import uuid
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from manifest_utils import SCHEMA_VERSION, atomic_json, expected_geometry, now_iso, sha256_file, write_report


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
UNSUPPORTED_IMAGE_EXTS = {".psd", ".psb"}
MODES = {"edit", "generate", "localization"}
ROLES = {"target", "style_reference", "logo", "asset", "layout_reference"}
WINDOWS_RESERVED_NAMES = {"con", "prn", "aux", "nul", *(f"com{index}" for index in range(1, 10)), *(f"lpt{index}" for index in range(1, 10))}
OUTPUT_FORMATS = {
    "png": (".png", "PNG", True),
    "jpg": (".jpg", "JPEG", False),
    "jpeg": (".jpg", "JPEG", False),
    "webp": (".webp", "WEBP", True),
    "bmp": (".bmp", "BMP", False),
    "tiff": (".tiff", "TIFF", True),
}


def safe_name(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", value.strip())
    cleaned = re.sub(r"\s+", "-", cleaned).strip("-. ")
    return cleaned[:64] or "image-task"


def normalized_text(value: str) -> str:
    return unicodedata.normalize("NFC", value.replace("\\", "/")).casefold()


def canonical_path_key(path: Path) -> str:
    return normalized_text(os.path.normcase(str(path.resolve())))


def is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def create_unique_task_dir(base: Path, name: str) -> Path:
    """Atomically reserve and create one task directory."""
    base.mkdir(parents=True, exist_ok=True)
    suffix = 0
    while True:
        candidate = base / (name if suffix == 0 else f"{name}-{suffix:02d}")
        try:
            candidate.mkdir(parents=False, exist_ok=False)
        except FileExistsError:
            suffix += 1
            continue
        return candidate


def inspect(path: Path) -> dict[str, object]:
    try:
        with Image.open(path) as raw:
            image = ImageOps.exif_transpose(raw)
            has_alpha = "A" in image.getbands() or (image.mode == "P" and "transparency" in image.info)
            has_transparency = has_alpha and image.convert("RGBA").getchannel("A").getextrema()[0] < 255
            return {
                "width": image.width,
                "height": image.height,
                "format": raw.format or "",
                "has_alpha": has_alpha,
                "has_transparency": has_transparency,
                "inspect_error": None,
            }
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        return {
            "width": 0,
            "height": 0,
            "format": "",
            "has_alpha": False,
            "has_transparency": False,
            "inspect_error": str(exc),
        }


def parse_roles_file(path: Path | None, source: Path) -> tuple[dict[str, str], list[dict[str, str]]]:
    if path is None:
        return {}, []
    data = json.loads(path.resolve().read_text(encoding="utf-8"))
    entries: list[tuple[str, str]] = []
    if isinstance(data, dict):
        for raw_path, role in data.items():
            entries.append((str(raw_path), str(role)))
    elif isinstance(data, list):
        for entry in data:
            if not isinstance(entry, dict) or "path" not in entry or "role" not in entry:
                raise ValueError("roles-file list entries require path and role")
            entries.append((str(entry["path"]), str(entry["role"])))
    else:
        raise ValueError("roles-file must contain an object or a list")

    base = source if source.is_dir() else source.parent
    mapping: dict[str, str] = {}
    records: list[dict[str, str]] = []
    for raw_path, role in entries:
        if role not in ROLES:
            raise ValueError(f"unsupported input role: {role}")
        candidate = Path(raw_path)
        resolved = candidate.resolve() if candidate.is_absolute() else (base / candidate).resolve()
        mapping[canonical_path_key(resolved)] = role
        mapping[normalized_text(raw_path)] = role
        records.append({"path": str(resolved), "role": role})
    return mapping, records


def role_for(path: Path, root: Path, mapping: dict[str, str]) -> str:
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        relative = path.name
    return mapping.get(canonical_path_key(path), mapping.get(normalized_text(relative), "target"))


def excluded_by(path: Path, root: Path, excluded_paths: list[Path], patterns: list[str]) -> bool:
    resolved = path.resolve()
    for excluded in excluded_paths:
        if resolved == excluded or (excluded.is_dir() and is_inside(resolved, excluded)):
            return True
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        relative = path.name
    return any(fnmatch.fnmatch(normalized_text(relative), normalized_text(pattern)) for pattern in patterns)


def scan_directory(
    root: Path,
    excluded_paths: list[Path],
    patterns: list[str],
    role_mapping: dict[str, str],
) -> tuple[list[Path], list[dict[str, str]], list[dict[str, str]]]:
    targets: list[Path] = []
    unsupported: list[dict[str, str]] = []
    excluded: list[dict[str, str]] = []
    candidates = [root] if root.is_file() else sorted(path for path in root.rglob("*") if path.is_file())
    role_root = root if root.is_dir() else root.parent
    for path in candidates:
        relative_parts = {part.casefold() for part in path.parts}
        if ".xobi" in relative_parts or "xobi-img-output" in relative_parts:
            continue
        if root.is_dir():
            task_container = False
            for parent in path.parents:
                if parent == root.parent:
                    break
                if (parent / ".xobi").is_dir():
                    task_container = True
                    break
            if task_container:
                continue
        if excluded_by(path, role_root, excluded_paths, patterns):
            excluded.append({"path": str(path.resolve()), "reason": "explicitly excluded"})
            continue
        role = role_for(path, role_root, role_mapping)
        if role != "target":
            excluded.append({"path": str(path.resolve()), "reason": f"input role is {role}"})
            continue
        suffix = path.suffix.lower()
        if suffix in IMAGE_EXTS:
            targets.append(path.resolve())
        elif suffix in UNSUPPORTED_IMAGE_EXTS:
            unsupported.append({"path": str(path.resolve()), "reason": "PSD/PSB is not supported; skipped without conversion"})
    return sorted(targets), unsupported, excluded


def safe_zip_relative(raw_name: str) -> Path | None:
    normalized = raw_name.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if not pure.parts or pure.is_absolute() or ".." in pure.parts or "__MACOSX" in pure.parts:
        return None
    for part in pure.parts:
        if (
            re.search(r'[<>:"|?*\x00-\x1f]', part)
            or part.endswith((" ", "."))
            or part.split(".", 1)[0].casefold() in WINDOWS_RESERVED_NAMES
        ):
            return None
    return Path(*pure.parts)


def extract_zip_inputs(
    archive: Path,
    destination: Path,
    patterns: list[str],
    role_mapping: dict[str, str],
) -> tuple[list[Path], list[dict[str, str]], list[dict[str, str]]]:
    selected: list[tuple[zipfile.ZipInfo, Path]] = []
    unsupported: list[dict[str, str]] = []
    excluded: list[dict[str, str]] = []
    seen: dict[str, str] = {}
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            if member.is_dir():
                continue
            relative = safe_zip_relative(member.filename)
            if relative is None:
                continue
            key = normalized_text(relative.as_posix())
            if key in seen:
                raise ValueError(f"ZIP contains duplicate/case-colliding paths: {seen[key]} and {member.filename}")
            seen[key] = member.filename
            if any(fnmatch.fnmatch(key, normalized_text(pattern)) for pattern in patterns):
                excluded.append({"path": member.filename, "reason": "explicitly excluded"})
                continue
            role = role_mapping.get(key, "target")
            if role != "target":
                excluded.append({"path": member.filename, "reason": f"input role is {role}"})
                continue
            suffix = relative.suffix.lower()
            if suffix in UNSUPPORTED_IMAGE_EXTS:
                unsupported.append({"path": member.filename, "reason": "PSD/PSB is not supported; skipped without conversion"})
                continue
            if suffix in IMAGE_EXTS:
                if member.flag_bits & 0x1:
                    raise ValueError(f"encrypted ZIP members are not supported: {member.filename}")
                selected.append((member, relative))

        destination_resolved = destination.resolve()
        extracted: list[Path] = []
        for member, relative in selected:
            target = (destination / relative).resolve()
            if not is_inside(target, destination_resolved):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            extracted.append(target)
    return sorted(extracted), unsupported, excluded


def output_suffix(path: Path, output_format: str) -> str:
    return path.suffix.lower() if output_format == "source" else OUTPUT_FORMATS[output_format][0]


def allocate_output_paths(files: list[Path], root: Path, output_dir: Path, output_format: str) -> dict[Path, Path]:
    relatives = {path: path.relative_to(root) for path in files}
    groups: dict[str, list[Path]] = {}
    for path, relative in relatives.items():
        key = normalized_text(relative.with_suffix(output_suffix(path, output_format)).as_posix())
        groups.setdefault(key, []).append(path)

    allocated: dict[Path, Path] = {}
    used: set[str] = set()
    for key in sorted(groups):
        group = sorted(
            groups[key],
            key=lambda item: (
                item.suffix.lower() != output_suffix(item, output_format),
                normalized_text(relatives[item].as_posix()),
            ),
        )
        for path in group:
            relative = relatives[path]
            suffix = output_suffix(path, output_format)
            candidate = relative.with_suffix(suffix)
            candidate_key = normalized_text(candidate.as_posix())
            if len(group) > 1 and (path.suffix.lower() != suffix or candidate_key in used):
                suffix_label = re.sub(r"[^a-z0-9]+", "", path.suffix.lower().lstrip(".")) or "source"
                candidate = relative.with_name(f"{relative.stem}-{suffix_label}{suffix}")
                candidate_key = normalized_text(candidate.as_posix())
            if candidate_key in used:
                digest = hashlib.sha256(normalized_text(relative.as_posix()).encode("utf-8")).hexdigest()[:8]
                candidate = candidate.with_name(f"{candidate.stem}-{digest}{candidate.suffix}")
                candidate_key = normalized_text(candidate.as_posix())
            if candidate_key in used:
                raise ValueError(f"could not allocate a unique output for {relative}")
            used.add(candidate_key)
            allocated[path] = (output_dir / candidate).resolve()
    if len(used) != len(files):
        raise ValueError("output allocation is not one-to-one")
    return allocated


def logo_record(path: Path) -> dict[str, Any]:
    info = inspect(path)
    if info["inspect_error"]:
        raise ValueError(f"Logo is not a readable image: {info['inspect_error']}")
    with Image.open(path) as raw:
        alpha = ImageOps.exif_transpose(raw).convert("RGBA").getchannel("A")
        alpha_extrema = alpha.getextrema()
        alpha_bbox = alpha.point(lambda value: 255 if value >= 10 else 0).getbbox()
        if not alpha_bbox:
            raise ValueError("Logo has no visible pixels at alpha threshold 10")
        alpha_reaches_full_canvas = alpha_bbox == (0, 0, alpha.width, alpha.height)
    return {
        "enabled": True,
        "source": str(path.resolve()),
        "source_sha256": sha256_file(path),
        "source_width": info["width"],
        "source_height": info["height"],
        "fully_opaque": alpha_extrema[0] == 255,
        "opaque_review_required": alpha_reaches_full_canvas,
        "normalized": None,
        "normalized_sha256": None,
        "normalization": None,
        "reference_short_side": 4000,
        "reference_box": [1036, 309],
        "anchor": [0, 0],
        "alpha_threshold": 10,
        "safe_padding": 80,
        "anchor_tolerance": 48,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Create an xobi-img manifest v3 with isolated worker assignments.")
    parser.add_argument("--input", type=Path, help="Input image, directory, ZIP, PSD, or PSB. Omit for generate mode.")
    parser.add_argument("--mode", required=True, choices=sorted(MODES))
    parser.add_argument("--operation", required=True, help="Confirmed operation summary.")
    parser.add_argument("--ratio", required=True, help="Confirmed ratio, dimensions, or 'original'.")
    parser.add_argument("--variants", type=int, default=1, help="Number of independent outputs for generate mode.")
    parser.add_argument("--target-language", help="Required for localization.")
    parser.add_argument("--output-format", choices=[*OUTPUT_FORMATS, "source"], default="png")
    parser.add_argument(
        "--alpha-policy",
        choices=["preserve", "required", "forbidden"],
        default="preserve",
        help="Preserve source transparency, require transparent pixels, or require fully opaque output.",
    )
    parser.add_argument("--output-root", type=Path, help="Parent output directory. Default: beside input/xobi-img-output.")
    parser.add_argument("--task-name", help="Safe task directory name.")
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=4, metavar="1-4")
    parser.add_argument("--logo", type=Path, help="Logo asset; excluded from target images and locked into the manifest.")
    parser.add_argument("--exclude", action="append", default=[], help="Path or relative glob to exclude; repeat as needed.")
    parser.add_argument("--roles-file", type=Path, help="UTF-8 JSON mapping paths to target/logo/reference/asset roles.")
    args = parser.parse_args()

    if args.variants < 1:
        parser.error("--variants must be a positive integer")
    if args.mode == "generate":
        if args.input is not None:
            parser.error("--input is not used in generate mode; use edit for reference-image work")
        if args.output_format == "source":
            parser.error("output format 'source' is unavailable in generate mode")
        if args.exclude or args.roles_file:
            parser.error("--exclude and --roles-file require an input")
        source: Path | None = None
    else:
        if args.input is None:
            parser.error("--input is required for edit and localization modes")
        if args.variants != 1:
            parser.error("--variants is only available in generate mode")
        source = args.input.resolve()
        if not source.exists():
            parser.error(f"input not found: {source}")
    if args.mode == "localization" and not args.target_language:
        parser.error("--target-language is required for localization")
    if not args.operation.strip() or not args.ratio.strip():
        parser.error("operation and ratio must be non-empty")
    normalized_ratio = args.ratio.strip().lower().replace(" ", "").replace("：", ":")
    if args.mode == "generate" and normalized_ratio in {"original", "keep-original", "保持原比例", "原比例"}:
        parser.error("generate mode requires an explicit ratio or dimensions; 'original' is not allowed")
    try:
        expected_geometry(args.ratio, 0 if args.mode == "generate" else 1, 0 if args.mode == "generate" else 1)
    except ValueError as exc:
        parser.error(str(exc))
    if args.alpha_policy == "required" and args.output_format != "source" and not OUTPUT_FORMATS[args.output_format][2]:
        parser.error(f"output format {args.output_format} cannot satisfy required transparency")

    logo_path = args.logo.resolve() if args.logo else None
    if logo_path and not logo_path.is_file():
        parser.error(f"logo not found: {logo_path}")
    if source is None:
        role_mapping, input_roles = {}, []
    else:
        try:
            role_mapping, input_roles = parse_roles_file(args.roles_file, source)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            parser.error(str(exc))

    excluded_paths: list[Path] = []
    patterns: list[str] = []
    if source is not None:
        base_for_relative = source if source.is_dir() else source.parent
        for value in args.exclude:
            if any(character in value for character in "*?["):
                patterns.append(value)
            else:
                candidate = Path(value)
                excluded_paths.append(candidate.resolve() if candidate.is_absolute() else (base_for_relative / candidate).resolve())
    if logo_path:
        excluded_paths.append(logo_path)
        input_roles.append({"path": str(logo_path), "role": "logo"})

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    task_label = safe_name(args.task_name or (f"{source.stem}-{args.mode}" if source else "generate"))
    default_parent = source.parent if source else Path.cwd()
    base = args.output_root.resolve() if args.output_root else default_parent / "xobi-img-output"
    if source is not None and source.is_dir() and base.resolve() != source.resolve() and is_inside(base, source):
        excluded_paths.append(base.resolve())
    task_dir: Path | None = None
    task_dir_created = False
    try:
        task_dir = create_unique_task_dir(base, f"{task_label}-{timestamp}").resolve()
        task_dir_created = True
        metadata_dir = task_dir / ".xobi"
        source_dir = metadata_dir / "source"
        output_dir = task_dir
        work_dir = metadata_dir / "work"
        source_dir.mkdir(parents=True, exist_ok=False)
        work_dir.mkdir(parents=True, exist_ok=False)
        (work_dir / "task-state").mkdir(parents=True, exist_ok=False)

        items: list[dict[str, object]] = []
        if source is None:
            unsupported: list[dict[str, str]] = []
            excluded: list[dict[str, str]] = []
            (source_dir / "source_paths.json").write_text("[]\n", encoding="utf-8")
            active_workers = min(args.workers, args.variants)
            dimensions, expected_ratio = expected_geometry(args.ratio, 0, 0)
            expected_format = OUTPUT_FORMATS[args.output_format][1]
            supports_alpha = OUTPUT_FORMATS[args.output_format][2]
            expected_alpha = None if args.alpha_policy == "preserve" else args.alpha_policy == "required"
            if expected_alpha and not supports_alpha:
                raise ValueError(
                    f"output format {expected_format} cannot preserve or provide required transparency"
                )
            suffix = OUTPUT_FORMATS[args.output_format][0]
            for index in range(1, args.variants + 1):
                variant_name = f"variant-{index:03d}"
                output = (output_dir / f"{variant_name}{suffix}").resolve()
                items.append({
                    "task_id": variant_name,
                    "worker_id": f"worker-{((index - 1) % active_workers) + 1}",
                    "variant_index": index,
                    "source": "",
                    "source_sha256": None,
                    "relative_path": "",
                    "role": "target",
                    "output": str(output),
                    "output_relative_path": output.relative_to(output_dir).as_posix(),
                    "output_key": canonical_path_key(output),
                    "status": "pending",
                    "attempts": 0,
                    "attempt_history": [],
                    "pure_rebuild_approval": None,
                    "prompt_summary": "",
                    "error": None,
                    "updated_at": None,
                    "output_validation": None,
                    "expected_dimensions": dimensions,
                    "expected_ratio": expected_ratio,
                    "expected_format": expected_format,
                    "expected_alpha": expected_alpha,
                    "base_output": None,
                    "localized_base": None,
                    "localization_validation": None,
                    "conflict_reference_base": None,
                    "prepared_base": None,
                    "logo_relocation_validation": None,
                    "family_id": None,
                    "logo_decision": None,
                    "logo_geometry": None,
                    "module_anchors": [],
                    "localization_plan": None,
                    "localization_plan_registration": None,
                    "localization_execution_stage": None,
                    "localization_composition": None,
                    "width": 0,
                    "height": 0,
                    "format": "",
                    "has_alpha": False,
                    "has_transparency": False,
                    "inspect_error": None,
                })
        else:
            if source.is_file() and source.suffix.lower() == ".zip":
                files, unsupported, excluded = extract_zip_inputs(source, source_dir, patterns, role_mapping)
                root = source_dir
            else:
                files, unsupported, excluded = scan_directory(source, excluded_paths, patterns, role_mapping)
                root = source if source.is_dir() else source.parent
            inspections: dict[Path, dict[str, object]] = {}
            readable_files: list[Path] = []
            for path in files:
                info = inspect(path)
                if info["inspect_error"]:
                    unsupported.append({"path": str(path), "reason": f"unreadable image skipped: {info['inspect_error']}"})
                else:
                    inspections[path] = info
                    readable_files.append(path)
            files = readable_files
            if not (source.is_file() and source.suffix.lower() == ".zip"):
                (source_dir / "source_paths.json").write_text(
                    json.dumps([str(path) for path in files], ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            if not files and not unsupported:
                raise ValueError("no supported or explicitly unsupported image inputs found")
            outputs = allocate_output_paths(files, root, output_dir, args.output_format)
            active_workers = min(args.workers, len(files)) if files else 0
            for index, path in enumerate(files, start=1):
                relative = path.relative_to(root)
                output = outputs[path]
                info = inspections[path]
                dimensions, expected_ratio = expected_geometry(args.ratio, int(info["width"]), int(info["height"]))
                if args.output_format == "source":
                    expected_format = str(info["format"]).upper()
                    supports_alpha = path.suffix.lower() in {".png", ".webp", ".tif", ".tiff"}
                else:
                    expected_format = OUTPUT_FORMATS[args.output_format][1]
                    supports_alpha = OUTPUT_FORMATS[args.output_format][2]
                expected_alpha = (
                    bool(info["has_transparency"])
                    if args.alpha_policy == "preserve"
                    else args.alpha_policy == "required"
                )
                if expected_alpha and not supports_alpha:
                    raise ValueError(
                        f"{path}: output format {expected_format} cannot preserve or provide required transparency"
                    )
                items.append({
                    "task_id": f"task-{index:06d}",
                    "worker_id": f"worker-{((index - 1) % active_workers) + 1}",
                    "source": str(path),
                    "source_sha256": sha256_file(path),
                    "relative_path": relative.as_posix(),
                    "role": "target",
                    "output": str(output),
                    "output_relative_path": output.relative_to(output_dir).as_posix(),
                    "output_key": canonical_path_key(output),
                    "status": "pending",
                    "attempts": 0,
                    "attempt_history": [],
                    "pure_rebuild_approval": None,
                    "prompt_summary": "",
                    "error": None,
                    "updated_at": None,
                    "output_validation": None,
                    "expected_dimensions": dimensions,
                    "expected_ratio": expected_ratio,
                    "expected_format": expected_format,
                    "expected_alpha": expected_alpha,
                    "base_output": None,
                    "localized_base": None,
                    "localization_validation": None,
                    "conflict_reference_base": None,
                    "prepared_base": None,
                    "logo_relocation_validation": None,
                    "family_id": None,
                    "logo_decision": None,
                    "logo_geometry": None,
                    "module_anchors": [],
                    "localization_plan": None,
                    "localization_plan_registration": None,
                    "localization_execution_stage": None,
                    "localization_composition": None,
                    **info,
                })

        manifest: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "manifest_id": f"xobi-{uuid.uuid4().hex}",
            "revision": 0,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "host": "auto",
            "mode": args.mode,
            "operation": args.operation.strip(),
            "ratio": args.ratio.strip(),
            "output_format": args.output_format,
            "alpha_policy": args.alpha_policy,
            "target_language": args.target_language,
            "input": str(source) if source else None,
            "variants": args.variants if args.mode == "generate" else None,
            "task_dir": str(task_dir),
            "output_dir": str(output_dir),
            "workers": active_workers,
            "workers_requested": args.workers,
            "workers_active": active_workers,
            "execution_mode": "parallel" if active_workers > 1 else "single",
            "degraded_to_single": False,
            "retry_policy": {
                "quality_attempts": 3,
                "infrastructure_retries": 3,
                "max_infrastructure_attempts": 4,
                "infrastructure_backoff_seconds": [2, 5, 10],
                "parallel_failure_probe_threshold": 2,
            },
            "localization_policy": {
                "mode": "text_only_reference_edit",
                "authorization_scope": "task",
                "pure_rebuild_allowed": False,
                "user_approval": None,
                "reference_edit_quality_attempts": 3,
                "pure_rebuild_quality_attempts_after_approval": 3,
            } if args.mode == "localization" else None,
            "style_lock": None,
            "layout_families": None,
            "logo": logo_record(logo_path) if logo_path else None,
            "logo_plan": None,
            "input_roles": input_roles,
            "excluded_inputs": excluded,
            "unsupported_inputs": unsupported,
            "items": items,
        }
        manifest_path = metadata_dir / "manifest.json"
        report_path = metadata_dir / "report.md"
        atomic_json(manifest_path, manifest)
        write_report(report_path, manifest)
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile, UnidentifiedImageError) as exc:
        if task_dir_created and task_dir is not None and is_inside(task_dir, base.resolve()):
            shutil.rmtree(task_dir, ignore_errors=True)
        parser.error(str(exc))

    print(f"task_dir={task_dir}")
    print(f"manifest={manifest_path}")
    print(f"report={report_path}")
    print(f"images={len(items)} workers={active_workers} unsupported={len(unsupported)} excluded={len(excluded)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
