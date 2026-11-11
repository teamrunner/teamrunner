# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""From files to the IR: validate, then resolve everything (D57).

`load_team` is the one way in. It returns no IR while any error stands, so
nothing downstream ever sees a package validate would reject.
"""

from __future__ import annotations

from pathlib import Path

from . import ir
from .loader import TEAM_FILE, Member, Package
from .schema_check import plain
from .validate import PLACEHOLDER, WORD, Report, validate


def load_team(folder: Path, write_ids: bool = False) -> tuple[ir.Team | None, Report]:
    report = validate(Path(folder), write_ids)
    if not report.ok:
        return None, report
    return _team(report), report


def _team(report: Report) -> ir.Team:
    package = report.package
    team = package.team
    data = plain(team.doc.data)
    default_model = data.get("default_model")
    members = {m.name: _member(m, default_model, package) for m in team.members}

    defaults = data.get("default_inputs") or {}
    used = []
    for member in members.values():
        for name in member.inputs:
            if name not in used:
                used.append(name)
    inputs = {name: ir.Input(name, defaults.get(name), name in defaults) for name in used}

    return ir.Team(
        name=team.name,
        folder=team.folder,
        package_top=package.top,
        id=data.get("id"),
        description=data.get("description"),
        members=members,
        flow=report.flow,
        inputs=inputs,
        message_goes_to=_message_goes_to(data.get("message_goes_to"), inputs),
    )


def _message_goes_to(written: str | None, inputs: dict) -> str | None:
    """SPEC §2: as written; else an input named `message`; else the only input."""
    if written:
        return written
    if "message" in inputs:
        return "message"
    if len(inputs) == 1:
        return next(iter(inputs))
    return None


def _member(member: Member, default_model, package: Package) -> ir.Member:
    data = plain(member.front.data)
    out = ir.Member(
        name=member.name,
        path=member.path,
        kind=data.get("kind", "llm"),
        id=data.get("id"),
        description=data.get("description"),
        body=member.body,
    )
    if out.kind == "toolkeeper":
        _toolkeeper(out, data)
        return out

    if "model" in data:
        written, source = data["model"], "its own `model:`"
    else:
        written, source = default_model, f"`default_model:` in {TEAM_FILE}"
    out.choices = [_choice(entry, data, package, source) for entry in ir.model_list(written)]
    first = out.choices[0]
    out.model, out.params = first.model, dict(first.params)
    out.output = ir.output_fields(data.get("output"))
    names = [m.group(1) for m in PLACEHOLDER.finditer(member.body)]
    out.inputs = tuple(dict.fromkeys(n for n in names if WORD.match(n)))
    return out


def _choice(entry: str, data: dict, package: Package, source: str) -> ir.Choice:
    """One entry of `model:`, its profile's settings and the agent's own on top (D93, D135)."""
    params: dict[str, ir.Setting] = {}
    if entry.endswith(".yaml"):
        profile = plain(package.models[entry].data)
        where = f"models/{entry}"
        model = ir.Setting(profile["name"], f"{where}, named by {source}")
        params = {k: ir.Setting(v, where) for k, v in (profile.get("params") or {}).items()}
    else:
        model = ir.Setting(entry, source)
    for key, value in (data.get("params") or {}).items():
        params[key] = ir.Setting(value, "its own `params:`")  # key by key (D93)
    return ir.Choice(model, params, entry)


def _toolkeeper(out: ir.Member, data: dict) -> None:
    (tool, base), = data["tools"].items()
    base = {"calls_per_turn": ir.CALLS_PER_TURN, **(base or {})}
    out.tool = tool
    out.base = ir.Terms(base)

    def terms(value):
        if value == "deny":
            return None
        if value == "standard":
            return ir.Terms(dict(base))
        return ir.Terms({**base, **value})  # changed terms replace the base ones (D95)

    out.access = {name: terms(value) for name, value in (data.get("access") or {}).items()}
    out.others = terms(data["others"]) if "others" in data else None
