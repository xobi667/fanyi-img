#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path


SKILL_NAME = "xobi-img"
COPY_DIRS = ("references", "scripts", "assets")


def codex_root() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def openclaw_workspace(explicit: Path | None) -> Path | None:
    if explicit:
        return explicit.expanduser().resolve()
    candidates = [Path.home() / ".openclaw" / "openclaw.json", Path.home() / ".clawdbot" / "clawdbot.json"]
    for config in candidates:
        if not config.is_file():
            continue
        try:
            data = json.loads(config.read_text(encoding="utf-8"))
            workspace = data.get("agents", {}).get("defaults", {}).get("workspace")
            if workspace:
                return Path(workspace).expanduser().resolve()
        except (OSError, ValueError, TypeError):
            continue
    fallback = Path.home() / "clawd"
    return fallback.resolve() if fallback.is_dir() else None


def copy_skill(source: Path, destination: Path) -> None:
    if destination.name != SKILL_NAME:
        raise ValueError(f"refusing unexpected install destination: {destination}")
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / "SKILL.md", destination / "SKILL.md")
    for dirname in COPY_DIRS:
        src_dir = source / dirname
        dst_dir = destination / dirname
        if dst_dir.exists():
            shutil.rmtree(dst_dir)
        shutil.copytree(src_dir, dst_dir, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Install xobi-img into Codex and/or OpenClaw skill directories.")
    parser.add_argument("--target", choices=["auto", "codex", "openclaw", "both"], default="auto")
    parser.add_argument("--openclaw-workspace", type=Path)
    parser.add_argument("--remove-legacy", action="store_true", help="Remove the legacy Codex fanyi skill after xobi-img installs successfully.")
    args = parser.parse_args()

    source = Path(__file__).resolve().parent.parent
    if not (source / "SKILL.md").is_file():
        parser.error("SKILL.md not found beside scripts directory")
    workspace = openclaw_workspace(args.openclaw_workspace)
    if args.target == "auto":
        targets = ["codex"] + (["openclaw"] if workspace else [])
    elif args.target == "both":
        targets = ["codex", "openclaw"]
    else:
        targets = [args.target]

    installed: list[Path] = []
    for target in targets:
        if target == "codex":
            destination = codex_root() / "skills" / SKILL_NAME
        else:
            if not workspace:
                parser.error("OpenClaw workspace not found; pass --openclaw-workspace")
            destination = workspace / "skills" / SKILL_NAME
        copy_skill(source, destination)
        installed.append(destination)
        print(f"installed_{target}={destination}")

    if args.remove_legacy:
        legacy = (codex_root() / "skills" / "fanyi").resolve()
        new_codex = (codex_root() / "skills" / SKILL_NAME).resolve()
        if legacy.is_dir() and legacy != new_codex and new_codex in [path.resolve() for path in installed]:
            shutil.rmtree(legacy)
            print(f"removed_legacy={legacy}")
        if workspace:
            legacy_openclaw = (workspace / "skills" / "fanyi").resolve()
            new_openclaw = (workspace / "skills" / SKILL_NAME).resolve()
            if legacy_openclaw.is_dir() and legacy_openclaw != new_openclaw and new_openclaw in [path.resolve() for path in installed]:
                shutil.rmtree(legacy_openclaw)
                print(f"removed_legacy={legacy_openclaw}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
