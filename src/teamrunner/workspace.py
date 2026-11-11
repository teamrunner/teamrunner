# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""The teamrunner workspace (D143, D144): the one folder that holds a person's
teams, in `teams/`, and experiments, in `experiments/`.

Plain `teamrunner ui` opens it from anywhere. It is remembered as
`TEAMRUNNER_WORKSPACE` in the key file, beside the keys and the other
machine settings (D99, D100); the same variable set in the shell wins.
"""

from __future__ import annotations

import os
from pathlib import Path

from .keys import LINE, key_file, read_env

NAME = "TEAMRUNNER_WORKSPACE"


def default() -> Path:
    return Path.home() / "teamrunner"


def shown(path: Path) -> str:
    home = Path.home()
    return f"~/{path.relative_to(home).as_posix()}" if path.is_relative_to(home) and path != home else str(path)


def remembered(env: dict | None = None) -> tuple[Path, str] | None:
    """The workspace and where it is written: the shell first, then the key file."""
    env = os.environ if env is None else env
    if env.get(NAME):
        return Path(env[NAME]).expanduser(), "your shell"
    stored = read_env(key_file()).get(NAME)
    return (Path(stored).expanduser(), shown(key_file())) if stored else None


def remember(folder: Path) -> None:
    """Write the one line, replacing an earlier one; the file stays readable only by its owner."""
    path = key_file()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    kept = [line for line in (path.read_text(encoding="utf-8").splitlines() if path.exists() else [])
            if not ((m := LINE.match(line)) and m[1] == NAME)]
    path.write_text("\n".join(kept + [f"{NAME}={folder}"]) + "\n", encoding="utf-8")
    path.chmod(0o600)


def make(folder: Path) -> None:
    """A new workspace starts with its two folders."""
    for part in (folder, folder / "teams", folder / "experiments"):
        part.mkdir(parents=True, exist_ok=True)


def is_workspace(folder: Path) -> bool:
    """A folder with `teams/` in it, or the remembered one; never a team itself."""
    folder = Path(folder)
    if (folder / "team.yaml").is_file():
        return False
    if (folder / "teams").is_dir():
        return True
    found = remembered()
    try:
        return bool(found) and found[0].resolve() == folder.resolve()
    except OSError:
        return False


def team_place(base: Path, name: str) -> Path:
    """Where a new team named on its own goes: in `teams/` when `base` is a workspace.
    A name with a slash is a path and is used as written."""
    if "/" in name or "\\" in name or not is_workspace(base):
        return Path(base) / name
    return Path(base) / "teams" / name
