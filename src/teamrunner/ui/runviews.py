# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""Read-only browser views over the same run records the CLI reads."""

import json
import os
from pathlib import Path
import subprocess
import sys
from collections import OrderedDict
from threading import RLock

from ..console import money, seconds
from ..runs import (NoRuns, all_runs, load_run, member_identity, member_versions,
                    read_events, runs_folder)
from ..runfolder import OLD_RECORD, RECORD, record_file
from .api import OutsideRoot, inside, visible

CORE = (RECORD, OLD_RECORD, "events.jsonl", "output.md")


class HistoryVersions:
    """A bounded, server-local cache; disk changes invalidate derived versions."""

    def __init__(self):
        self.entries = OrderedDict()
        self.lock = RLock()

    def get(self, root: Path, base: Path, warnings: list[str]):
        signature = []
        # Stat records without parsing every run or transcript. Include updates
        # within existing folders, not just newly added/deleted run directories.
        for folder in sorted(base.iterdir()):
            if folder.name.startswith("."):
                continue
            for path in (folder, folder / RECORD, folder / OLD_RECORD, folder / "events.jsonl"):
                try:
                    stat = path.lstat()
                    signature.append((str(path), stat.st_ino, stat.st_mtime_ns, stat.st_size))
                except OSError:
                    signature.append((str(path), None))
        key = (root, base)
        with self.lock:
            cached = self.entries.get(key)
            if cached is None or cached[0] != signature:
                notes = []
                history = all_runs(base, allowed=lambda p: safe_folder(root, p, notes))
                cached = (signature, member_versions(history), notes)
                self.entries[key] = cached
            self.entries.move_to_end(key)
            while len(self.entries) > 32:
                self.entries.popitem(last=False)
            warnings.extend(cached[2])
            return cached[1]


def safe_folder(root: Path, folder: Path, warnings: list[str]) -> bool:
    try:
        inside(root, folder.relative_to(root).as_posix())
        for name in CORE:
            path = inside(root, (folder / name).relative_to(root).as_posix())
            if not path.is_relative_to(folder.resolve()):
                raise OutsideRoot()
        return folder.is_dir()
    except (OutsideRoot, ValueError, OSError, RuntimeError):
        warnings.append(f"{folder.name}: a run folder or record points outside the opened folder; this run was skipped.")
        return False


def records(root: Path, written: str = "") -> tuple[list, list[str]]:
    folder = inside(root, written)
    folder = folder if folder.is_dir() else folder.parent
    warnings = []
    if record_file(folder) is not None:
        if not safe_folder(root, folder, warnings):
            raise OutsideRoot("This run points outside the opened folder.")
        record = load_run(folder, warnings)
        return ([record] if record else []), warnings
    if folder.name.lower() == "runs":
        base = folder
    else:
        try:
            _, base = runs_folder(folder)
        except NoRuns:
            raise ValueError("Choose a team on the left to browse its runs.") from None
    base = inside(root, base.relative_to(root).as_posix()) if base.is_relative_to(root) else None
    if base is None:
        raise OutsideRoot("Open the package's top folder to browse its runs.")
    found = all_runs(base, allowed=lambda p: not p.name.startswith(".") and safe_folder(root, p, warnings),
                     warnings=warnings)
    return found, warnings


def output_text(folder: Path, warnings: list[str]) -> str:
    path = folder / "output.md"
    if not path.is_file():
        warnings.append("This run has no output.md.")
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        warnings.append("This run's output.md could not be read.")
        return ""


def row(root: Path, record, warnings: list[str]) -> dict:
    try:
        result = record.result
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        result = ""
        warnings.append(f"{record.folder.name}: the result summary could not be read.")
    run = record.run
    return {"path": record.folder.relative_to(root).as_posix(), "when": record.shown_when,
            "team": record.team, "status": run.get("status") or "unknown",
            "message": record.input, "result": result, "turns": run.get("turns", 0),
            "length": seconds(run.get("latency_ms")), "cost": money(run.get("cost_usd")),
            "cost_known": money(run.get("cost_known_usd")),
            "rehearsal": bool(run.get("rehearsal")), "backup": bool(run.get("backup_models")),
            "experiment": ({k: block.get(k) for k in ("condition", "replicate", "round")}
                           if isinstance(block := run.get("experiment"), dict) else None)}


def listing(root: Path, written: str = "", *, team: str = "", status: str = "",
            search: str = "", limit: int = 50, offset: int = 0) -> dict:
    found, warnings = records(root, written)
    matching = []
    for record in found:
        if team and record.team != team:
            continue
        if status and (record.run.get("status") or "unknown") != status:
            continue
        if search:
            message = json.dumps(record.run.get("inputs") or {}, ensure_ascii=False)
            output = output_text(record.folder, [])
            if search.casefold() not in (message + "\n" + output).casefold():
                continue
        matching.append(record)
    return {"runs": [row(root, r, warnings) for r in matching[offset:offset + limit]],
            "total": len(matching), "all_total": len(found), "offset": offset,
            "teams": sorted({r.team for r in found}),
            "statuses": sorted({r.run.get("status") or "unknown" for r in found}),
            "warnings": warnings}


def selected(root: Path, written: str):
    folder = inside(root, written)
    warnings = []
    if not safe_folder(root, folder, warnings):
        raise OutsideRoot("This run points outside the opened folder.")
    record = load_run(folder, warnings)
    if record is None:
        raise FileNotFoundError("This run's record could not be found or read.")
    return record


def attachment(root: Path, run: str, written: str) -> Path:
    record = selected(root, run)
    path = inside(root, (record.folder.relative_to(root) / written).as_posix())
    if not path.is_relative_to(record.folder) or not path.is_file():
        raise OutsideRoot("Choose a file inside this run folder.")
    return path


def inventory(root: Path, folder: Path, warnings: list[str]) -> list[dict]:
    result = []
    for base in ("inputs", "work", "resolved"):
        start = folder / base
        if not start.exists():
            continue
        try:
            resolved = inside(root, start.relative_to(root).as_posix())
            if not resolved.is_relative_to(folder):
                raise OutsideRoot()
        except (OutsideRoot, OSError, RuntimeError):
            warnings.append(f"{base}/ points outside this run folder and is not shown.")
            continue
        for directory, dirs, names in os.walk(start, followlinks=False):
            dirs[:] = [name for name in dirs if not name.startswith(".") and not (Path(directory) / name).is_symlink()]
            for name in sorted(names):
                path = Path(directory) / name
                if not visible(path.relative_to(folder)):
                    continue
                try:
                    safe = attachment(root, folder.relative_to(root).as_posix(), path.relative_to(folder).as_posix())
                    result.append({"path": path.relative_to(folder).as_posix(), "size": safe.stat().st_size})
                except (OutsideRoot, OSError, RuntimeError):
                    warnings.append(f"{path.relative_to(folder)} could not be opened inside this run folder.")
    return sorted(result, key=lambda item: item["path"])


def detail(root: Path, written: str, versions_cache: HistoryVersions | None = None) -> dict:
    record = selected(root, written)
    warnings = []
    events = list(read_events(record.folder, warnings))
    if not (record.folder / "events.jsonl").is_file():
        warnings.append("This run has no events.jsonl.")
    first = next((e for e in events if e["event"] == "run_started"), {})
    if not first:
        warnings.append("This run's log has no run_started event; member details are unavailable.")
    # Use the whole available package history, before any list filters, for D90.
    if versions_cache is not None and record.folder.parent.is_relative_to(root):
        versions = versions_cache.get(root, record.folder.parent, warnings)
    elif record.folder.parent.is_relative_to(root):
        history = all_runs(record.folder.parent,
                           allowed=lambda p: safe_folder(root, p, warnings))
        versions = member_versions(history)
    else:
        versions = member_versions([record])
    members = []
    recorded_members = first.get("members")
    if recorded_members is not None and not isinstance(recorded_members, list):
        warnings.append("This run's member inventory could not be read.")
    for member in recorded_members if isinstance(recorded_members, list) else []:
        if not isinstance(member, dict):
            continue
        fingerprint = member.get("version_hash")
        version = (versions.get(member_identity(member, record), {}).get(fingerprint)
                   if isinstance(fingerprint, str) else None)
        members.append({**member, "version": version})
    artifacts = inventory(root, record.folder, warnings)
    output = output_text(record.folder, warnings)
    return {"summary": row(root, record, warnings), "record": record.run,
            "events": events, "members": members, "output": output,
            "artifacts": artifacts, "raw_files": [name for name in CORE if (record.folder / name).is_file()],
            "has_spec": any(item["path"] == "resolved/team.yaml" for item in artifacts),
            "warnings": list(dict.fromkeys(warnings))}


def open_folder(root: Path, written: str) -> dict:
    folder = selected(root, written).folder
    if sys.platform == "win32":
        os.startfile(str(folder))
    else:
        subprocess.run(["open" if sys.platform == "darwin" else "xdg-open", str(folder)],
                       check=True, timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"ok": True}
