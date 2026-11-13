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


def is_name(text: str) -> bool:
    """A name alone: no slash, not `.` or `..`, not starting with `~`. Anything else is a path."""
    text = text.strip()
    return (bool(text) and text not in (".", "..") and not text.startswith("~")
            and not any(c in text for c in "/\\:"))


def find(text: str, kind: str, here: Path = Path(".")) -> Path:
    """The folder a command names (`kind` is "teams" or "experiments").

    A name alone is in your teamrunner workspace: `debate` is `teams/debate` there,
    from wherever it is typed. A path (`/…`, `~/…`, `./…`, `../…`, or anything with a
    slash, such as `teams/debate`) is used as written, from `here`. With no workspace
    remembered, a name alone is a folder here, as before."""
    path = Path(text).expanduser()
    if not is_name(text):
        return path if path.is_absolute() else Path(here) / path
    found = remembered()
    return found[0] / kind / text if found else Path(here) / path


def not_found(text: str, kind: str, here: Path = Path(".")) -> str | None:
    """Why `find` found nothing, with the other way to write it; None when the folder is there."""
    path = find(text, kind, here)
    if path.exists():
        return None
    what = kind[:-1]
    found = remembered()
    if is_name(text):
        if not found:
            return (f"there is no {what} `{text}` here, and no teamrunner workspace is remembered "
                    "to look in (`teamrunner ui` sets one)")
        message = f"there is no {what} `{text}` in your teamrunner workspace ({shown(found[0] / kind)}/)"
        if (Path(here) / text).is_dir():
            message += f"; for the folder here, write ./{text}"
        return message
    message = f"there is no folder {text} here"
    if found and (found[0] / kind / path.name).is_dir():
        message += f"; for the {what} in your teamrunner workspace, write `{path.name}`"
    return message


def team_place(base: Path, name: str) -> Path:
    """Where a new team named on its own goes: in `teams/` when `base` is a workspace.
    A name with a slash is a path and is used as written."""
    if "/" in name or "\\" in name or not is_workspace(base):
        return Path(base) / name
    return Path(base) / "teams" / name
