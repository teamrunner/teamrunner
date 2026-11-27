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
import re
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


# TeamRunner never runs the git program. A team folder may come from someone
# else with its own .git/config, and git runs the commands some of its
# settings name (review 2026-11-16 S3, 2026-11-21 NS1). The commit is two small
# text files, so it is read as text; it works without git installed, too.
COMMIT = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
REF = re.compile(r"refs(?:/[A-Za-z0-9._+@-]+)+")
SMALL = 4096


def _small(path: Path) -> str:
    """The first lines of a small text file inside .git; empty when it cannot be read."""
    try:
        if not path.is_file():
            return ""
        with path.open("rb") as f:
            return f.read(SMALL).decode("utf-8", "replace").strip()
    except OSError:
        return ""


def _git_dir(package_top: Path) -> Path | None:
    """The .git folder of the repository `package_top` is in, if any."""
    for folder in (package_top, *package_top.parents):
        dot = folder / ".git"
        if dot.is_dir():
            return dot
        if dot.is_file():  # a worktree or a submodule: one line, `gitdir: <folder>`
            line = _small(dot)
            where = (folder / line[len("gitdir:"):].strip()) if line.startswith("gitdir:") else None
            return where if where is not None and where.is_dir() else None
    return None


def git_commit(package_top: Path) -> str | None:
    """The commit `package_top` is checked out at, read from .git as text; None when it is
    not in a repository or the files do not say. Only a commit id is ever returned."""
    try:
        git_dir = _git_dir(Path(package_top).resolve())
        if git_dir is None:
            return None
        shared = _small(git_dir / "commondir")  # a worktree keeps its branches in the main .git
        places = [git_dir] + ([(git_dir / shared)] if shared else [])
        value = _small(git_dir / "HEAD")
        for _ in range(5):  # HEAD names a branch; a branch may name another
            if COMMIT.fullmatch(value):
                return value
            ref = value[len("ref:"):].strip() if value.startswith("ref:") else ""
            if not REF.fullmatch(ref) or ".." in ref:
                return None
            value = next((text for text in (_small(place / ref) for place in places) if text), "")
            if not value:
                return _packed(places, ref)
    except (OSError, ValueError):
        pass
    return None


def _packed(places: list[Path], ref: str) -> str | None:
    """A branch git has packed away is one line of `packed-refs`: `<commit> <name>`."""
    for place in places:
        try:
            if not (place / "packed-refs").is_file():
                continue
            with (place / "packed-refs").open(encoding="utf-8", errors="replace") as lines:
                for line in lines:
                    commit, _, name = line.strip().partition(" ")
                    if name == ref and COMMIT.fullmatch(commit):
                        return commit
        except OSError:
            continue
    return None


def fingerprint(package_top: Path) -> dict:
    """The package as it was run (D69): its git commit if it is in a
    repository, and always a hash over its spec files. The hash is what says
    whether two runs used the same files, committed or not."""
    digest = hashlib.sha256()
    for path in sorted(package_top.rglob("*")):
        inside = path.relative_to(package_top).parts
        if inside[0] == RUNS_DIR or any(p.startswith(".") for p in inside):
            continue
        if path.is_file() and path.suffix in SPEC_SUFFIXES:
            digest.update("/".join(inside).encode() + b"\0" + path.read_bytes() + b"\0")
    return {"content_hash": "sha256:" + digest.hexdigest(), "git_commit": git_commit(package_top)}


def version_hash(path: Path, content: bytes | None = None) -> str:
    """A member file's fingerprint: the hash of its exact text (D90)."""
    return "sha256:" + hashlib.sha256(path.read_bytes() if content is None else content).hexdigest()
