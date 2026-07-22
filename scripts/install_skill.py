#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import stat
import sys
import uuid
from pathlib import Path


SKILL_NAME = "xobi-img"
COPY_DIRS = ("references", "scripts", "assets", "agents")
MIN_PILLOW_VERSION = (9, 1, 0)


def parse_version_prefix(value: str) -> tuple[int, int, int] | None:
    match = re.match(r"^\s*(\d+)(?:\.(\d+))?(?:\.(\d+))?", value)
    if not match:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor or 0), int(patch or 0)


def is_link_or_junction(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except (FileNotFoundError, OSError):
        return path.is_symlink()
    attributes = int(getattr(metadata, "st_file_attributes", 0) or 0)
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag)


def is_inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def codex_root(explicit: Path | None = None) -> Path:
    if explicit:
        return explicit.expanduser().resolve()
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
            raw = config.read_text(encoding="utf-8")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                match = re.search(
                    r"(?:['\"]?workspace['\"]?)\s*:\s*(?P<quoted>'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\")",
                    raw,
                    re.DOTALL,
                )
                if not match:
                    raise
                workspace_literal = match.group("quoted")
                workspace = ast.literal_eval(workspace_literal)
                if not isinstance(workspace, str):
                    raise ValueError("OpenClaw workspace must be a string")
                return Path(workspace).expanduser().resolve()
            workspace = data.get("agents", {}).get("defaults", {}).get("workspace")
            if workspace:
                return Path(workspace).expanduser().resolve()
        except (OSError, ValueError, TypeError):
            continue
    modern_root = Path.home() / ".openclaw"
    if modern_root.is_dir():
        return (modern_root / "workspace").resolve()
    fallback = Path.home() / "clawd"
    return fallback.resolve() if fallback.is_dir() else None


def copy_skill(source: Path, destination: Path) -> None:
    raw_destination = destination.expanduser().absolute()
    if raw_destination.name != SKILL_NAME:
        raise ValueError(f"refusing unexpected install destination: {destination}")
    if is_link_or_junction(raw_destination):
        raise ValueError(f"refusing symlink/junction install destination: {raw_destination}")
    source = source.resolve()
    destination = raw_destination.resolve()
    if destination.name != SKILL_NAME:
        raise ValueError(f"resolved install destination is not {SKILL_NAME}: {destination}")
    if source == destination:
        return
    if is_inside(source, destination) or is_inside(destination, source):
        raise ValueError("skill source and install destination must not contain one another")
    required = [source / "SKILL.md", *(source / dirname for dirname in COPY_DIRS)]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise ValueError("skill source is incomplete: " + ", ".join(missing))
    staging = destination.parent / f".{SKILL_NAME}.install-{uuid.uuid4().hex}"
    backup = destination.parent / f".{SKILL_NAME}.backup-{uuid.uuid4().hex}"
    backup_created = False
    installed_new = False
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging.mkdir(parents=False, exist_ok=False)
        shutil.copy2(source / "SKILL.md", staging / "SKILL.md")
        for dirname in COPY_DIRS:
            shutil.copytree(source / dirname, staging / dirname, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        if destination.exists():
            destination.rename(backup)
            backup_created = True
        staging.rename(destination)
        installed_new = True
    except Exception as exc:
        if backup_created and not destination.exists() and backup.exists():
            try:
                backup.rename(destination)
            except OSError as restore_exc:
                raise RuntimeError(
                    f"install failed and automatic restore also failed; backup retained at {backup}: {restore_exc}"
                ) from exc
        elif backup_created and installed_new and backup.exists():
            failed_install = destination.parent / f".{SKILL_NAME}.failed-{uuid.uuid4().hex}"
            try:
                destination.rename(failed_install)
                backup.rename(destination)
                shutil.rmtree(failed_install, ignore_errors=True)
            except OSError as restore_exc:
                raise RuntimeError(
                    f"install failed and automatic restore also failed; backup retained at {backup}: {restore_exc}"
                ) from exc
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    if backup.exists():
        try:
            shutil.rmtree(backup)
        except OSError:
            print(f"warning_backup_retained={backup}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Install xobi-img into Codex and/or OpenClaw skill directories.")
    parser.add_argument("--target", choices=["auto", "codex", "openclaw", "both"], default="auto")
    parser.add_argument("--codex-root", type=Path, help="Optional Codex root; defaults to CODEX_HOME or ~/.codex.")
    parser.add_argument("--openclaw-workspace", type=Path)
    parser.add_argument("--remove-legacy", action="store_true", help="Remove the legacy Codex fanyi skill after xobi-img installs successfully.")
    args = parser.parse_args()

    if sys.version_info < (3, 10):
        parser.error("xobi-img requires Python 3.10 or newer")
    try:
        import PIL
    except ImportError:
        parser.error("xobi-img requires Pillow 9.1 or newer in the Python environment used by the host")
    pillow_version = str(getattr(PIL, "__version__", ""))
    parsed_pillow_version = parse_version_prefix(pillow_version)
    if parsed_pillow_version is None or parsed_pillow_version < MIN_PILLOW_VERSION:
        parser.error(
            "xobi-img requires Pillow 9.1 or newer; "
            f"found {pillow_version or 'an unknown Pillow version'}"
        )

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
            destination = codex_root(args.codex_root) / "skills" / SKILL_NAME
        else:
            if not workspace:
                parser.error("OpenClaw workspace not found; pass --openclaw-workspace")
            destination = workspace / "skills" / SKILL_NAME
        try:
            copy_skill(source, destination)
        except (OSError, RuntimeError, ValueError) as exc:
            parser.error(str(exc))
        installed.append(destination)
        print(f"installed_{target}={destination}")

    if args.remove_legacy:
        raw_legacy = (codex_root(args.codex_root) / "skills" / "fanyi").absolute()
        if raw_legacy.exists() and is_link_or_junction(raw_legacy):
            parser.error(f"refusing to remove legacy symlink/junction: {raw_legacy}")
        legacy = raw_legacy.resolve()
        new_codex = (codex_root(args.codex_root) / "skills" / SKILL_NAME).resolve()
        if legacy.name != "fanyi":
            parser.error(f"refusing unexpected legacy path: {legacy}")
        if legacy.is_dir() and legacy != new_codex and new_codex in [path.resolve() for path in installed]:
            shutil.rmtree(legacy)
            print(f"removed_legacy={legacy}")
        if workspace:
            raw_legacy_openclaw = (workspace / "skills" / "fanyi").absolute()
            if raw_legacy_openclaw.exists() and is_link_or_junction(raw_legacy_openclaw):
                parser.error(f"refusing to remove legacy symlink/junction: {raw_legacy_openclaw}")
            legacy_openclaw = raw_legacy_openclaw.resolve()
            new_openclaw = (workspace / "skills" / SKILL_NAME).resolve()
            if legacy_openclaw.name != "fanyi":
                parser.error(f"refusing unexpected legacy path: {legacy_openclaw}")
            if legacy_openclaw.is_dir() and legacy_openclaw != new_openclaw and new_openclaw in [path.resolve() for path in installed]:
                shutil.rmtree(legacy_openclaw)
                print(f"removed_legacy={legacy_openclaw}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
