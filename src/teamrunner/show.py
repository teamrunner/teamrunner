"""`teamrunner show <member>` (D93): one member exactly as it will run.

Every value is written out, and each one that is not in the member's own
file says where it came from (`# from models/careful.yaml`). Your files
stay short; this is the long form, for reading (explorations/validate.md).
"""

from __future__ import annotations

import json
from pathlib import Path

from . import ir
from .build import load_team
from .ids import short
from .loader import TEAM_FILE, member_name


class NotAMember(Exception):
    pass


def find_member(given: str, cwd: Path) -> tuple[Path, str]:
    """A member by its file (`judge.md`, `debate/judge.md`) or its name in this folder."""
    path = Path(given)
    if path.suffix == ".md" and path.is_file():
        return path.resolve().parent, member_name(path)
    folder, name = (cwd / path).parent, path.name
    if not (folder / TEAM_FILE).is_file():
        raise NotAMember(f"{folder.resolve()} is not a team folder")
    return folder.resolve(), name


def _value(v) -> str:
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False) if (not v or v != v.strip() or ":" in v or "#" in v) else v
    return json.dumps(v, ensure_ascii=False)


def _line(text: str, note: str | None = None) -> str:
    return f"{text:<40} # {note}" if note else text


def render(team: ir.Team, member: ir.Member) -> str:
    rel = member.path.relative_to(team.package_top.parent)
    lines = [f"# {member.name} — {rel}" + (f" ({short(member.id)})" if member.id else ""), "---"]
    if member.description:
        lines.append(f"description: {_value(member.description)}")
    lines.append(_line(f"kind: {member.kind}", None if "kind" in _own(member) else "default"))
    if member.kind == "toolkeeper":
        lines += _toolkeeper(member)
    else:
        own = _own(member)
        lines.append(_line(f"model: {member.model.value}",
                           None if member.model.source == "its own `model:`" else
                           "from " + member.model.source.replace("`", "")))
        if member.params:
            lines.append("params:")
            for key, setting in member.params.items():
                note = None if setting.source == "its own `params:`" else "from " + setting.source
                lines.append(_line(f"  {key}: {_value(setting.value)}", note))
        if member.output:
            lines.append("output:")
            for f in member.output.values():
                long = {"type": f.type, **({"options": list(f.options)} if f.options else {})}
                lines.append(_line(f"  {f.name}: {json.dumps(long)}", None if "output" in own else "default"))
    lines.append("---")
    body = member.body.strip()
    lines.append(body if body else "(no text)")
    if member.inputs:
        lines.append("")
        lines.append("# inputs its text uses:")
        for name in member.inputs:
            spec = team.inputs.get(name)
            if spec and spec.has_default:
                shown = " ".join(str(spec.default).split())
                lines.append(f"#   {name} — default from team.yaml: "
                             + (shown if len(shown) <= 60 else shown[:59] + "…"))
            else:
                lines.append(f"#   {name} — no default: every run must give it")
    return "\n".join(lines)


def _own(member: ir.Member) -> set[str]:
    from .loader import read_member
    from .problems import Problems
    m = read_member(member.path, Problems())
    return set(m.front.data) if m else set()


def _toolkeeper(member: ir.Member) -> list[str]:
    def terms(t: ir.Terms) -> str:
        return json.dumps(t.guardrails)

    base = dict(member.base.guardrails)
    lines = ["tools:", f"  {member.tool}: {json.dumps(base)}"]
    written = _written_tools(member)
    if "calls_per_turn" not in written:
        lines[-1] = _line(lines[-1], "calls_per_turn: 5 is the default (D86)")
    lines.append("access:")
    for name, t in member.access.items():
        lines.append(f"  {name}: {terms(t) if t else 'deny'}")
    lines.append(_line(f"others: {terms(member.others) if member.others else 'deny'}",
                       None if member.others else "anyone not listed is refused"))
    return lines


def _written_tools(member: ir.Member) -> dict:
    from .loader import read_member
    from .problems import Problems
    m = read_member(member.path, Problems())
    tools = (m.front.data.get("tools") or {}) if m else {}
    return dict(next(iter(tools.values()), {}) or {})


def show(given: str, cwd: Path) -> tuple[str | None, object]:
    folder, name = find_member(given, cwd)
    team, report = load_team(folder)
    if team is None:
        return None, report
    if name not in team.members:
        known = ", ".join(sorted(team.members))
        raise NotAMember(f"{team.name} has no member `{name}`; its members are {known}")
    return render(team, team.members[name]), report
