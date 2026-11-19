# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""Where a run is kept: `runs/<YYYY-MM-DD_HHMMSS>_<entry-team>/` at the top
of the package (D69), holding `record.json`, `events.jsonl` and `output.md`,
and `inputs/` when the run was given a file (D108). Before 0.2 the record
was `run.json` (D138 (10)); older folders are read through `record_file`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

from .loader import RUNS_DIR

SPEC_SUFFIXES = (".md", ".yaml", ".yml")
RECORD, OLD_RECORD = "record.json", "run.json"


def record_file(folder: Path) -> Path | None:
    """A run folder's record: `record.json`, or `run.json` in a folder from before 0.0.5."""
    for name in (RECORD, OLD_RECORD):
        if (folder / name).is_file():
            return folder / name
    return None


def folder_word(text: str) -> str:
    """`text` as part of a folder name on any system: letters, digits, `_` and `-` only,
    at most 60 characters. A team's `name:` is free text, and `..\\x`, `:` or `?` in it
    must not reach the disk as written (review 2026-11-16, S10)."""
    word = re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_")[:60]
    return word or "team"


class RunFolder:
    def __init__(self, path: Path):
        self.path = path
        self._events = (path / "events.jsonl").open("a", encoding="utf-8")

    @classmethod
    def create(cls, package_top: Path, entry: str, runs_dir: Path | None = None,
               now: datetime | None = None) -> "RunFolder":
        base = Path(runs_dir) if runs_dir else package_top / RUNS_DIR
        stamp = (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")
        name = f"{stamp}_{folder_word(entry)}"
        base.mkdir(parents=True, exist_ok=True)
        path, n = base / name, 2
        while True:  # creating the folder is the check, so runs started in the same second never share one
            try:
                path.mkdir()
                return cls(path)
            except FileExistsError:
                path, n = base / f"{name}_{n}", n + 1

    def write_event(self, event: dict) -> None:
        """One line per event, flushed at once, so a crash keeps the log so far."""
        self._events.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        self._events.flush()

    def write_record(self, record: dict) -> None:
        (self.path / RECORD).write_text(json.dumps(record, indent=2, ensure_ascii=False, default=str)
                                            + "\n", encoding="utf-8")

    def write_output(self, text: str) -> None:
        (self.path / "output.md").write_text(text.rstrip() + "\n", encoding="utf-8")

    @property
    def inputs_dir(self) -> Path:
        return self.path / "inputs"

    def close(self) -> None:
        self._events.close()


# A team folder may come from someone else with its own .git/config, and git
# runs the commands some of its settings name. These switch off every such
# setting the two questions below could reach; `_git_dirty` covers the one
# kind that cannot be switched off by name.
SAFE_GIT = ("git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + os.devnull,
            "-c", "core.untrackedCache=false", "-c", "status.submoduleSummary=false",
            "-c", "safe.bareRepository=explicit", "--no-optional-locks")
# Settings in a repository's own config that run a command when files are
# compared (a content filter), or that bring in settings from another file.
UNSAFE_CONFIG = ("filter.", "include.", "includeif.", "extensions.worktreeconfig")


def _git(package_top: Path, *args: str):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    return subprocess.run([*SAFE_GIT, "-C", str(package_top), *args], env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, encoding="utf-8", timeout=5)


def _git_dirty(package_top: Path) -> bool | None:
    """Whether the package has uncommitted changes; None when asking is not safe.

    `git status` runs the content filters a repository's config names, so a
    repository that names any is not asked. The content hash still says
    whether two runs used the same files."""
    config = _git(package_top, "config", "--local", "--no-includes", "--name-only", "--list")
    if config.returncode != 0 or any(line.strip().lower().startswith(UNSAFE_CONFIG)
                                     for line in config.stdout.splitlines()):
        return None
    dirty = _git(package_top, "status", "--porcelain", "--ignore-submodules=all", "--", ".")
    return bool(dirty.stdout.strip()) if dirty.returncode == 0 else None


def fingerprint(package_top: Path) -> dict:
    """The package as it was run (D69): its git commit if it is in a
    repository, and always a hash over its spec files."""
    digest = hashlib.sha256()
    for path in sorted(package_top.rglob("*")):
        inside = path.relative_to(package_top).parts
        if inside[0] == RUNS_DIR or any(p.startswith(".") for p in inside):
            continue
        if path.is_file() and path.suffix in SPEC_SUFFIXES:
            digest.update("/".join(inside).encode() + b"\0" + path.read_bytes() + b"\0")
    result = {"content_hash": "sha256:" + digest.hexdigest(), "git_commit": None}
    try:
        commit = _git(package_top, "rev-parse", "HEAD")
        if commit.returncode == 0:
            result["git_commit"] = commit.stdout.strip()
            dirty = _git_dirty(package_top)
            if dirty is not None:
                result["git_dirty"] = dirty
    except (OSError, subprocess.SubprocessError):
        pass
    return result


def version_hash(path: Path, content: bytes | None = None) -> str:
    """A member file's fingerprint: the hash of its exact text (D90)."""
    return "sha256:" + hashlib.sha256(path.read_bytes() if content is None else content).hexdigest()
