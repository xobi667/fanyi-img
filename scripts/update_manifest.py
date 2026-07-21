#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from preflight_images import atomic_json, write_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Atomically update one xobi-img task and regenerate report.md.")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--status", required=True, choices=["pending", "success", "skipped", "failed"])
    parser.add_argument("--output")
    parser.add_argument("--attempts", type=int)
    parser.add_argument("--prompt-summary")
    parser.add_argument("--error")
    args = parser.parse_args()

    manifest_path = args.manifest.resolve()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    matches = [item for item in data.get("items", []) if item.get("task_id") == args.task_id]
    if len(matches) != 1:
        parser.error(f"task-id must match exactly one item: {args.task_id}")
    item = matches[0]
    item["status"] = args.status
    if args.output is not None:
        item["output"] = str(Path(args.output).resolve())
    if args.attempts is not None:
        if args.attempts < 0:
            parser.error("attempts must be non-negative")
        item["attempts"] = args.attempts
    if args.prompt_summary is not None:
        item["prompt_summary"] = args.prompt_summary
    item["error"] = args.error if args.status == "failed" else None
    atomic_json(manifest_path, data)
    write_report(manifest_path.parent / "report.md", data)
    print(f"updated={args.task_id} status={args.status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
