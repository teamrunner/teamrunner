"""Read a package from disk: team folders, member files, model profiles.

Loading only reads and records what is there, with line numbers; deciding
whether it is right is validate's job. YAML is read as YAML 1.2, so `no`
and `on` stay strings (D21), and no tag ever constructs an object.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError

from .problems import Problems

TEAM_FILE = "team.yaml"
CHAIR_FILE = "chair.md"
MANAGER_FILE = "manager.md"
WHITEBOARD_FILE = "whiteboard.yaml"
# Reserved folders at the top of a package (D66, D69, D93).
TOOLS_DIR, MODELS_DIR, RUNS_DIR = "tools", "models", "runs"

_yaml = YAML(typ="rt")


@dataclass
class Doc:
    """A YAML mapping read from a file, with a way to find its lines."""

    path: Path
    data: CommentedMap
    line_offset: int  # file line of the YAML's first line, 1-based

    def line(self, *keys) -> int | None:
        """The file line of a key (or a nested key path), 1-based."""
        node, line = self.data, None
        for key in keys:
            if isinstance(node, CommentedMap) and key in node:
                line = node.lc.key(key)[0]
                node = node[key]
            elif isinstance(node, CommentedSeq) and isinstance(key, int) and key < len(node):
                line = node.lc.item(key)[0]
                node = node[key]
            else:
                break
        return None if line is None else line + self.line_offset


@dataclass
class Member:
    path: Path
    name: str
    front: Doc
    body: str
    body_line: int  # file line where the body starts

    @property
    def kind(self) -> str:
        return self.front.data.get("kind", "llm")


@dataclass
class Team:
    folder: Path
    doc: Doc | None  # team.yaml; None when it could not be read
    members: list[Member] = field(default_factory=list)
    chair: Path | None = None
    manager: Path | None = None
    whiteboard: Path | None = None
    subteams: list["Team"] = field(default_factory=list)
    retired_files: list[Path] = field(default_factory=list)
    broken: list[Path] = field(default_factory=list)  # member files that could not be read

    @property
    def name(self) -> str:
        if self.doc and isinstance(self.doc.data.get("name"), str):
            return self.doc.data["name"]
        return self.folder.name


@dataclass
class Package:
    top: Path
    team: Team  # the team that was asked for (the package top or a subteam)
    top_team: Team
    models: dict[str, Doc]  # profile filename -> its doc
    links_outside: list[tuple[Path, Path]]  # (link, where it points)
    has_tools_dir: bool


def member_name(path: Path) -> str:
    """A member's name is its file stem without a numeric prefix (D59, D87)."""
    return re.sub(r"^[0-9_]+", "", path.stem)


def find_top(folder: Path) -> Path:
    """Walk up while the parent folder is a team too (D69)."""
    folder = folder.resolve()
    while (folder.parent / TEAM_FILE).is_file() and folder.parent != folder:
        folder = folder.parent
    return folder


def read_yaml(path: Path, problems: Problems, text: str | None = None, line_offset: int = 1):
    """Parse YAML into a mapping Doc, or record why not and return None."""
    if text is None:
        text = path.read_text(encoding="utf-8")
    try:
        data = _yaml.load(text)
    except YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        line = mark.line + line_offset if mark else None
        problems.error(path, f"this is not valid YAML: {getattr(e, 'problem', None) or e}",
                       line=line, fix="check the indentation and quoting on this line")
        return None
    if data is None:
        data = CommentedMap()
    if not isinstance(data, CommentedMap):
        problems.error(path, "this file should hold `name: value` lines, one per setting",
                       line=line_offset)
        return None
    return Doc(path, data, line_offset)


def split_frontmatter(text: str):
    """Return (frontmatter text, body, body's first line), or None if there is
    no block. An unclosed block comes back with a body of None."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].rstrip() == "---":
            return "".join(lines[1:i]), "".join(lines[i + 1:]), i + 2
    return "".join(lines[1:]), None, len(lines) + 1


def read_member(path: Path, problems: Problems) -> Member | None:
    text = path.read_text(encoding="utf-8")
    parts = split_frontmatter(text)
    if parts is None:
        return None  # a .md without frontmatter is a note, not a member (D91)
    front_text, body, body_line = parts
    if body is None:
        problems.error(path, "the settings block at the top is never closed",
                       line=1, fix="add a line with just `---` after the settings")
        return None
    doc = read_yaml(path, problems, front_text, line_offset=2)
    if doc is None:
        return None
    return Member(path, member_name(path), doc, body, body_line)


def read_team(folder: Path, problems: Problems, is_top: bool) -> Team:
    doc = read_yaml(folder / TEAM_FILE, problems)
    team = Team(folder, doc)
    for entry in sorted(folder.iterdir()):
        name = entry.name
        if name.startswith(".") or (is_top and name in (TOOLS_DIR, MODELS_DIR, RUNS_DIR)):
            continue
        if entry.is_dir():
            if (entry / TEAM_FILE).is_file():
                team.subteams.append(read_team(entry, problems, is_top=False))
            continue
        if name == CHAIR_FILE:
            team.chair = entry
        elif name == MANAGER_FILE:
            team.manager = entry
        elif name == WHITEBOARD_FILE:
            team.whiteboard = entry
        elif name in ("pathways.yaml", "state.yaml"):
            team.retired_files.append(entry)
        elif entry.suffix == ".md":
            before = len(problems)
            member = read_member(entry, problems)
            if member:
                team.members.append(member)
            elif len(problems) > before:
                team.broken.append(entry)
    return team


def _links_outside(top: Path) -> list[tuple[Path, Path]]:
    found = []
    resolved_top = top.resolve()
    for path in sorted(top.rglob("*")):
        inside = path.relative_to(top).parts
        if inside[0] == RUNS_DIR or any(p.startswith(".") for p in inside[:-1]):
            continue
        if path.is_symlink():
            target = path.resolve()
            if not target.is_relative_to(resolved_top):
                found.append((path, target))
    return found


def _find_team(team: Team, folder: Path) -> Team | None:
    if team.folder.resolve() == folder:
        return team
    for sub in team.subteams:
        hit = _find_team(sub, folder)
        if hit:
            return hit
    return None


def load_package(folder: Path, problems: Problems) -> Package:
    """Load the package holding `folder`, which must be a team folder."""
    folder = folder.resolve()
    top = find_top(folder)
    top_team = read_team(top, problems, is_top=True)
    models = {}
    models_dir = top / MODELS_DIR
    if models_dir.is_dir():
        for path in sorted(models_dir.glob("*.yaml")):
            doc = read_yaml(path, problems)
            if doc is not None:
                models[path.name] = doc
    return Package(
        top=top,
        team=_find_team(top_team, folder) or top_team,
        top_team=top_team,
        models=models,
        links_outside=_links_outside(top),
        has_tools_dir=(top / TOOLS_DIR).is_dir(),
    )
