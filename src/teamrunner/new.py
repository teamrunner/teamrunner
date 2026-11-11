# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""`teamrunner new` (D106): a new team folder, empty or copied from another.

    teamrunner new my_team                         # team.yaml and .gitignore
    teamrunner new my_team --from ../debate        # a copy, with fresh ids

A copy is a new component: every team and member file gets a fresh id,
and a `parent_id` naming the file it came from (D33's lineage). Only
files; no model is called. Nothing is ever written over.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from .ids import new_ulid, write_team_id
from .loader import MODELS_DIR, RUNS_DIR, TEAM_FILE, split_frontmatter

GITIGNORE = "# Runs are kept here, outside version control\nruns/\n"
ID_LINE = re.compile(r"^(id|parent_id):.*\n?", re.MULTILINE)
COMMENT = "  # added by teamrunner — keeps this {what}'s history together"


def _examples_folder() -> Path:
    """Installed, the examples sit inside the package; in the repository, at its top (D122)."""
    installed = Path(__file__).with_name("examples")
    return installed if installed.is_dir() else Path(__file__).resolve().parents[2] / "examples"


EXAMPLES = _examples_folder()  # the examples that ship (D116, D122)


class NewError(Exception):
    pass


def examples() -> list[str]:
    return sorted(p.name for p in EXAMPLES.iterdir() if (p / TEAM_FILE).is_file())


def find_source(given: str) -> Path:
    """A team folder by its path, or an example that ships with TeamRunner by its name."""
    path = Path(given).expanduser()
    if (path / TEAM_FILE).is_file():
        return path
    if (EXAMPLES / given / TEAM_FILE).is_file():
        return EXAMPLES / given
    raise NewError(f"{given} is neither a team folder nor an example; the examples are "
                   + ", ".join(examples()))


def _check_name(name: str) -> None:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise NewError(f"`{name}` cannot be a team's name: use letters, digits and underscores")


def create(folder: Path) -> Path:
    folder = Path(folder)
    _check_name(folder.name)
    if folder.exists():
        raise NewError(f"{folder} already exists; `new` never writes over anything")
    folder.mkdir(parents=True)
    (folder / TEAM_FILE).write_text(
        f"name: {folder.name}\n"
        "description: \"\"   # one line: what this team does\n", encoding="utf-8", newline="\n")
    write_team_id(folder / TEAM_FILE)
    (folder / ".gitignore").write_text(GITIGNORE, encoding="utf-8", newline="\n")
    return folder


def _old_id(text: str) -> str | None:
    m = re.search(r"^id:\s*([0-9A-HJKMNP-TV-Z]{26})", text, re.MULTILINE)
    return m[1] if m else None


def _restamp(text: str, what: str) -> str:
    """Fresh id, and parent_id naming the id it had, in place of any id lines."""
    old = _old_id(text)
    lines = f"id: {new_ulid()}{COMMENT.format(what=what)}\n"
    if old:
        lines += f"parent_id: {old}  # copied with `teamrunner new --from`\n"
    return lines + ID_LINE.sub("", text)


def _restamp_member(path: Path, what: str) -> None:
    text = path.read_text(encoding="utf-8")
    parts = split_frontmatter(text)
    if parts is None or parts[1] is None:
        return  # notes, not members
    front, body, _ = parts
    if re.search(r"^kind:\s*toolkeeper\b", front, re.MULTILINE):
        what = "toolkeeper"
    path.write_text("---\n" + _restamp(front, what) + "---\n" + body, encoding="utf-8", newline="\n")


def copy_from(source: Path, folder: Path) -> tuple[Path, list[str]]:
    """Copy a team folder into a new one. Returns it, and warnings to show."""
    source, folder = Path(source).resolve(), Path(folder)
    _check_name(folder.name)
    if not (source / TEAM_FILE).is_file():
        raise NewError(f"{source} is not a team folder: it has no {TEAM_FILE}")
    if folder.exists():
        raise NewError(f"{folder} already exists; `new` never writes over anything")
    warnings = []

    def skip(directory, names):
        here = Path(directory)
        left = {n for n in names if n == ".git" or n == ".DS_Store" or n == ".env"
                or (here == source and n == RUNS_DIR)}
        if ".env" in names:
            warnings.append(f"{(here / '.env').relative_to(source.parent)} was not copied: "
                            "keys live in ~/.teamrunner/.env")
        return left

    shutil.copytree(source, folder, ignore=skip, symlinks=False)  # links become real copies (D94)
    for path in sorted(folder.rglob("*")):
        inside = path.relative_to(folder).parts
        if path.name == TEAM_FILE:
            text = path.read_text(encoding="utf-8")
            if path.parent == folder:
                text = re.sub(r"^name:.*$", f"name: {folder.name}", text, count=1, flags=re.MULTILINE) \
                    if re.search(r"^name:", text, re.MULTILINE) else f"name: {folder.name}\n" + text
            path.write_text(_restamp(text, "team"), encoding="utf-8", newline="\n")
        elif path.suffix == ".md" and path.name not in ("chair.md", "manager.md") and inside[0] != MODELS_DIR:
            _restamp_member(path, "agent")
    ignore = folder / ".gitignore"
    if not ignore.exists() or "runs/" not in ignore.read_text(encoding="utf-8"):
        with ignore.open("a", encoding="utf-8", newline="\n") as f:
            f.write(("\n" if ignore.exists() and ignore.read_text(encoding="utf-8") else "") + GITIGNORE)
    return folder, warnings
