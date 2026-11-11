# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""AI-assisted authoring (D62, D106): one set of instructions, `AGENTS.md`,
used three ways — read by any assistant working in a folder, made into a
Claude Code skill, and sent to a model by `teamrunner new --describe`.
"""

from __future__ import annotations

from pathlib import Path

AGENTS = Path(__file__).with_name("AGENTS.md")

SKILL_NAME = "teamrunner-authoring"
SKILL_DESCRIPTION = (
    "Write a TeamRunner team folder (team.yaml, member .md files, chair.md) from a plain-language "
    "description of a team of AI agents, then check it with teamrunner validate, rehearse and explain. "
    "Use when asked to create, change or fix a TeamRunner team.")


MARK = "written by teamrunner"  # how a file TeamRunner may update is told from one it must not touch


def instructions() -> str:
    return AGENTS.read_text(encoding="utf-8")


def _stamp() -> str:
    from .. import __version__
    return f"<!-- {MARK} {__version__}; MIT-0, use it as you like; run `teamrunner skill` to update it -->\n\n"


def skill() -> str:
    """The skill: the same instructions under the Agent Skills header, which
    Claude Code and Codex both read (D117)."""
    return (f"---\nname: {SKILL_NAME}\ndescription: {SKILL_DESCRIPTION}\n---\n\n"
            + _stamp() + instructions())


def agents_md() -> str:
    return _stamp() + instructions()


# Where each assistant reads skills that apply in every project (D117).
SKILL_HOMES = {"Claude Code": Path(".claude") / "skills", "Codex": Path(".agents") / "skills"}


class NotOurs(Exception):
    """A file is there that TeamRunner did not write; it is never written over."""


def _write(path: Path, text: str) -> str:
    if path.exists():
        old = path.read_text(encoding="utf-8", errors="replace")
        if MARK not in old[:2000]:
            raise NotOurs(f"{path} is there already and TeamRunner did not write it; "
                          "move it aside, or add TeamRunner's instructions to it by hand")
        if old == text:
            return "already up to date"
        path.write_text(text, encoding="utf-8")
        return "updated"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return "installed"


def install_skill(home: Path | None = None) -> list[tuple[str, Path, str]]:
    """Install or update the skill for each assistant. Returns (assistant, path, what happened)."""
    home = home or Path.home()
    done = []
    for assistant, folder in SKILL_HOMES.items():
        path = home / folder / SKILL_NAME / "SKILL.md"
        try:
            done.append((assistant, path, _write(path, skill())))
        except NotOurs as e:
            done.append((assistant, path, f"left alone: {e}"))
    return done


def write_agents_md(folder: Path) -> tuple[Path, str]:
    folder = Path(folder)
    if not folder.is_dir():
        raise NotOurs(f"{folder} is not a folder")
    path = folder / "AGENTS.md"
    return path, _write(path, agents_md())
