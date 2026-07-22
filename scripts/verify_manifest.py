#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

from manifest_utils import (
    FileLock,
    atomic_json,
    canonical_path_key,
    load_manifest,
    sha256_file,
    validate_auxiliary_json_path,
    validate_manifest,
    valid_task_id,
)


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify xobi-img manifest integrity, output files, hashes, dimensions, omissions, and duplicates.")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--allow-pending", action="store_true")
    parser.add_argument("--write-json", type=Path, help="Optional machine-readable verification report.")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    manifest_path = args.manifest.resolve()
    if not manifest_path.is_file():
        parser.error(f"manifest not found: {manifest_path}")
    write_path = args.write_json.resolve() if args.write_json else None
    if write_path and write_path.suffix.lower() != ".json":
        parser.error("write-json must use a .json suffix")
    errors: list[dict[str, str]] = []
    try:
        lock = FileLock(manifest_path.with_name(manifest_path.name + ".lock"))
        lock.__enter__()
    except (OSError, TimeoutError) as exc:
        parser.error(str(exc))
    try:
        try:
            data = load_manifest(manifest_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            parser.error(f"could not read manifest: {exc}")
        errors.extend(validate_manifest(data, check_files=True))
        items = list(data.get("items", []))
        if not args.allow_pending:
            for item in items:
                if item.get("status") == "pending":
                    errors.append({"task_id": str(item.get("task_id")), "error": "task is still pending"})
        expected_files: set[str] = set()
        for item in items:
            if item.get("status") != "success":
                continue
            output = Path(str(item.get("output"))).resolve()
            expected_files.add(canonical_path_key(output))

        logo = data.get("logo") or {}
        if logo.get("source"):
            logo_path = Path(str(logo["source"])).resolve()
            if not logo_path.is_file():
                errors.append({"task_id": "<logo>", "error": "Logo source file is missing"})
            elif not logo.get("source_sha256"):
                errors.append({"task_id": "<logo>", "error": "Logo source hash baseline is missing"})
            elif sha256_file(logo_path) != logo.get("source_sha256"):
                errors.append({"task_id": "<logo>", "error": "Logo source hash changed after preflight"})

        registered_plan_paths: set[Path] = set()
        for field in ("logo_plan", "layout_families", "style_lock"):
            entry = data.get(field) or {}
            if not isinstance(entry, dict) or not entry.get("path"):
                continue
            plan_path = Path(str(entry["path"])).resolve()
            registered_plan_paths.add(plan_path)
            if not plan_path.is_file():
                errors.append({"task_id": f"<{field}>", "error": f"registered {field} file is missing"})
            elif not entry.get("sha256"):
                errors.append({"task_id": f"<{field}>", "error": f"registered {field} hash baseline is missing"})
            elif sha256_file(plan_path) != entry.get("sha256"):
                errors.append({"task_id": f"<{field}>", "error": f"registered {field} hash changed"})

        items_by_id = {str(item.get("task_id")): item for item in items}
        state_dir = manifest_path.parent / "work" / "task-state"
        for state_path in sorted(state_dir.glob("*.json")):
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                errors.append({"task_id": state_path.stem, "error": f"unreadable task state: {exc}"})
                continue
            task_id = str(state.get("task_id") or "")
            if not valid_task_id(task_id) or state_path.name != f"{task_id}.json":
                errors.append({"task_id": task_id or state_path.stem, "error": "invalid task-state filename or task_id"})
                continue
            item = items_by_id.get(task_id)
            if item is None:
                errors.append({"task_id": task_id or state_path.stem, "error": "orphan task state"})
            else:
                try:
                    state_time = datetime.fromisoformat(str(state.get("updated_at") or ""))
                    item_time = datetime.fromisoformat(str(item.get("updated_at") or ""))
                    if state_time.tzinfo is None or item_time.tzinfo is None:
                        raise ValueError("timestamp lacks timezone")
                    if state_time > datetime.now().astimezone() + timedelta(seconds=5):
                        errors.append({"task_id": task_id, "error": "task state timestamp is in the future"})
                    elif state_time > item_time:
                        errors.append({"task_id": task_id, "error": "task state is newer than the shared manifest"})
                except ValueError as exc:
                    errors.append({"task_id": task_id, "error": f"invalid task-state timestamp: {exc}"})

        task_dir = Path(str(data["task_dir"])).resolve()
        actual_files = {
            canonical_path_key(path)
            for path in task_dir.rglob("*")
            if path.is_file()
            and path.suffix.lower() in IMAGE_EXTS
            and ".xobi" not in {part.casefold() for part in path.relative_to(task_dir).parts}
        }
        for key in sorted(actual_files - expected_files):
            errors.append({"task_id": "<disk>", "error": f"unexpected final image: {key}"})
        for key in sorted(expected_files - actual_files):
            errors.append({"task_id": "<disk>", "error": f"missing final image: {key}"})
    finally:
        lock.__exit__(None, None, None)

    result = {
        "manifest": str(manifest_path),
        "schema_version": data.get("schema_version"),
        "revision": data.get("revision"),
        "targets": len(data.get("items", [])),
        "success": sum(item.get("status") == "success" for item in data.get("items", [])),
        "skipped": sum(item.get("status") == "skipped" for item in data.get("items", [])),
        "failed": sum(item.get("status") == "failed" for item in data.get("items", [])),
        "pending": sum(item.get("status") == "pending" for item in data.get("items", [])),
        "errors": errors,
        "valid": not errors,
    }
    if write_path:
        protected = {manifest_path, (manifest_path.parent / "report.md").resolve()}
        if logo.get("source"):
            protected.add(Path(str(logo["source"])).resolve())
        for item in data.get("items", []):
            for key in (
                "source",
                "base_output",
                "localized_base",
                "conflict_reference_base",
                "prepared_base",
                "output",
            ):
                if item.get(key):
                    protected.add(Path(str(item[key])).resolve())
            composition = item.get("localization_composition") or {}
            if isinstance(composition, dict):
                if composition.get("artifact_path"):
                    protected.add(Path(str(composition["artifact_path"])).resolve())
                record = composition.get("record") or {}
                if isinstance(record, dict) and record.get("raw_edit_candidate"):
                    protected.add(Path(str(record["raw_edit_candidate"])).resolve())
        protected.update(registered_plan_paths)
        try:
            write_path = validate_auxiliary_json_path(args.write_json, protected)
        except ValueError as exc:
            parser.error(str(exc))
        if write_path.exists() and not args.overwrite:
            parser.error("write-json exists; use --overwrite")
        try:
            atomic_json(write_path, result)
        except OSError as exc:
            parser.error(f"could not write verification JSON: {exc}")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
