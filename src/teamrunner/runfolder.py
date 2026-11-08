"""Where a run is kept: `runs/<YYYY-MM-DD_HHMMSS>_<entry-team>/` at the top
of the package (D69), holding `run.json`, `events.jsonl` and `output.md`,
and `inputs/` when the run was given a file (D108).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path

from .loader import RUNS_DIR

SPEC_SUFFIXES = (".md", ".yaml", ".yml")


class RunFolder:
    def __init__(self, path: Path):
        self.path = path
        self._events = (path / "events.jsonl").open("a", encoding="utf-8")

    @classmethod
    def create(cls, package_top: Path, entry: str, runs_dir: Path | None = None,
               now: datetime | None = None) -> "RunFolder":
        base = Path(runs_dir) if runs_dir else package_top / RUNS_DIR
        stamp = (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")
        name = f"{stamp}_{entry.replace('/', '_')}"
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

    def write_run_json(self, record: dict) -> None:
        (self.path / "run.json").write_text(json.dumps(record, indent=2, ensure_ascii=False, default=str)
                                            + "\n", encoding="utf-8")

    def write_output(self, text: str) -> None:
        (self.path / "output.md").write_text(text.rstrip() + "\n", encoding="utf-8")

    @property
    def inputs_dir(self) -> Path:
        return self.path / "inputs"

    def close(self) -> None:
        self._events.close()


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
        commit = subprocess.run(["git", "-C", str(package_top), "rev-parse", "HEAD"],
                                capture_output=True, text=True, encoding="utf-8", timeout=5)
        if commit.returncode == 0:
            dirty = subprocess.run(["git", "-C", str(package_top), "status", "--porcelain", "--", "."],
                                   capture_output=True, text=True, encoding="utf-8", timeout=5)
            result["git_commit"] = commit.stdout.strip()
            result["git_dirty"] = bool(dirty.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return result


def version_hash(path: Path, content: bytes | None = None) -> str:
    """A member file's fingerprint: the hash of its exact text (D90)."""
    return "sha256:" + hashlib.sha256(path.read_bytes() if content is None else content).hexdigest()
