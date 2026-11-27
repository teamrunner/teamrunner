# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""`teamrunner new` (D106): a new team folder, empty or copied from another.

    teamrunner new my_team                         # team.yaml and .gitignore
    teamrunner new my_team --from ../debate        # a copy, with fresh ids
    teamrunner new my_study --from leader_vs_flat  # an example experiment, and its team (D159)

A copy is a new component: every team and member file gets a fresh id,
and a `parent_id` naming the file it came from (D33's lineage). Only
files; no model is called. Nothing is ever written over.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from .ids import insert_at_top, new_ulid, reads_with_id, write_team_id
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
                   + ", ".join(examples() + example_experiments()))


EXPERIMENT_FILE = "experiment.md"
EXAMPLE_EXPERIMENTS = EXAMPLES / "experiments"  # experiments that ship, each on an example team (D159)


def example_experiments() -> list[str]:
    if not EXAMPLE_EXPERIMENTS.is_dir():
        return []
    return sorted(p.name for p in EXAMPLE_EXPERIMENTS.iterdir() if (p / EXPERIMENT_FILE).is_file())


def find_experiment(given: str) -> Path | None:
    """An experiment folder by its path, or an example experiment by its name; None if neither."""
    path = Path(given).expanduser()
    if (path / EXPERIMENT_FILE).is_file():
        return path
    if (EXAMPLE_EXPERIMENTS / given / EXPERIMENT_FILE).is_file():
        return EXAMPLE_EXPERIMENTS / given
    return None


def _teams_named(source: Path) -> list[str]:
    """The teams an experiment names alone in `from:` or `base:`."""
    from .experiment import _bare, _yaml
    parts = split_frontmatter(source.joinpath(EXPERIMENT_FILE).read_text(encoding="utf-8"))
    data = (_yaml.load(parts[0]) if parts else None) or {}
    written = [data.get("base")]
    for spec in (data.get("conditions") or {}).values():
        written.append(spec if isinstance(spec, str) else (spec or {}).get("from"))
    return list(dict.fromkeys(w for w in written if isinstance(w, str) and _bare(w)))


def copy_experiment(source: Path, folder: Path) -> tuple[Path, list[str], list[Path]]:
    """Copy an experiment folder into a new one: its files, never its runs or built
    conditions. A team it names that is a shipped example and is not where the
    experiment will look is copied there too, with fresh ids (D159). Returns the
    folder, warnings, and the teams copied."""
    from . import workspace
    source, folder = Path(source).resolve(), Path(folder)
    _check_name(folder.name)
    if folder.exists():
        raise NewError(f"{folder} already exists; `new` never writes over anything")
    found = workspace.remembered()
    teams = found[0] / "teams" if found else (
        folder.resolve().parent.parent / "teams" if folder.resolve().parent.name == "experiments" else None)
    warnings, copied, plans = [], [], []
    for name in _teams_named(source):
        if teams is not None and (teams / name).is_dir():
            continue  # the experiment finds it there
        if (EXAMPLES / name / TEAM_FILE).is_file() and teams is not None:
            plans.append((name, teams / name))
        else:
            warnings.append(f"the experiment names the team `{name}`, which is not in "
                            + (f"{workspace.shown(teams)}/" if teams else "a teamrunner workspace")
                            + "; put it there, or change `from:` to its path")

    def skip(directory, names):
        here = Path(directory)
        return {n for n in names if n in (".git", ".DS_Store", ".env")
                or (here == source and n in (RUNS_DIR, "conditions"))}

    shutil.copytree(source, folder, ignore=skip, symlinks=False)
    for name, place in plans:
        place.parent.mkdir(parents=True, exist_ok=True)
        copy_from(EXAMPLES / name, place)
        copied.append(place)
    return folder, warnings, copied


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
    return insert_at_top(ID_LINE.sub("", text), lines)


def _restamp_member(path: Path, what: str) -> None:
    text = path.read_text(encoding="utf-8-sig")  # a mark Windows Notepad leaves is not part of the text
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
    # A copy is whole or not there: a file that cannot be read stops it, and what was made so
    # far is removed, so trying again does not say the copy "already exists" (NC6).
    try:
        for path in sorted(folder.rglob("*")):
            inside = path.relative_to(folder).parts
            if path.name == TEAM_FILE:
                text = path.read_text(encoding="utf-8-sig")
                if path.parent == folder:
                    text = re.sub(r"^name:.*$", f"name: {folder.name}", text, count=1, flags=re.MULTILINE) \
                        if re.search(r"^name:", text, re.MULTILINE) else f"name: {folder.name}\n" + text
                stamped = _restamp(text, "team")
                if not reads_with_id(stamped, _old_id(stamped) or ""):
                    shutil.rmtree(folder)
                    raise NewError(f"{path.relative_to(folder)} in {source.name} could not be given the copy's "
                                   "own `id:` without breaking it, so nothing was made; check that it reads as "
                                   "`name: value` lines (`teamrunner validate` says where it does not)")
                path.write_text(stamped, encoding="utf-8", newline="\n")
            elif path.suffix == ".md" and path.name not in ("chair.md", "manager.md") and inside[0] != MODELS_DIR:
                _restamp_member(path, "agent")
    except (UnicodeDecodeError, OSError) as e:
        shutil.rmtree(folder, ignore_errors=True)
        where = path.relative_to(folder)
        why = "is not UTF-8 text; save it as UTF-8" if isinstance(e, UnicodeDecodeError) else f"could not be read ({e})"
        raise NewError(f"{where} in {source.name} {why}, so nothing was made") from None
    ignore = folder / ".gitignore"
    if not ignore.exists() or "runs/" not in ignore.read_text(encoding="utf-8"):
        with ignore.open("a", encoding="utf-8", newline="\n") as f:
            f.write(("\n" if ignore.exists() and ignore.read_text(encoding="utf-8") else "") + GITIGNORE)
    return folder, warnings
