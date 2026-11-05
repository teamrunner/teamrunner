"""`teamrunner validate`: everything that can be wrong with a package, found
offline and before any tokens are spent (D26, D89).

Validate does not rewrite your files (explorations/validate.md), with one
exception: it writes an `id:` into a team or member file that has none
(D64). The flow in `chair.md` is parsed and checked here too (flow.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import vocabulary as V
from .flow import Cast, check_flow, parse_flow, round_robin
from .ids import write_member_id, write_team_id
from .ir import Flow, output_fields
from .loader import (CHAIR_FILE, MODELS_DIR, TEAM_FILE, Member, Package, Team,
                     load_package, member_name, read_yaml, split_frontmatter)
from .problems import Problems
from .schema_check import check_schema
from .tool_registry import TOOLS, guardrails_of

PLACEHOLDER = re.compile(r"\{\{\s*([^}]*?)\s*\}\}")
WORD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Things that look like API keys (D27). Better a false alarm than a leak.
SECRET = re.compile(
    r"\b(sk-ant-[A-Za-z0-9_-]{20,}|sk-(?:proj-)?[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{35}"
    r"|gh[pousr]_[A-Za-z0-9]{36,}|xox[abpr]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}"
    r"|tvly-[A-Za-z0-9]{20,}|hf_[A-Za-z0-9]{30,})"
)


@dataclass
class ModelChoice:
    model: str | None
    source: str  # where it came from, for --verbose


@dataclass
class Report:
    package: Package
    problems: Problems
    models: dict[Path, ModelChoice] = field(default_factory=dict)
    flow: Flow | None = None  # the validated team's flow, checked
    ids_added: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems.errors


def validate(folder: Path, write_ids: bool = False) -> Report:
    """Check the team in `folder`. With `write_ids`, first give every file of
    the team that lacks an id one (D64), then check the files as written."""
    problems = Problems()
    folder = Path(folder)
    if not (folder / TEAM_FILE).is_file():
        raise NotATeam(folder)
    package = load_package(folder, problems)
    added, failed = _add_ids(package.team) if write_ids else ([], [])
    if added:
        problems = Problems()
        package = load_package(folder, problems)  # line numbers as the files now stand
    for path, error in failed:
        problems.warning(path, f"could not write an id into this file: {error}")
    report = Report(package, problems, ids_added=added)
    _check_package(package, problems)
    _check_team(package.team, package, report)
    return report


def _add_ids(team: Team):
    added, failed = [], []
    targets = []
    if team.doc is not None and "id" not in team.doc.data:
        targets.append((team.doc.path, write_team_id, ()))
    for m in team.members:
        if "id" not in m.front.data:
            what = "toolkeeper" if m.kind == "toolkeeper" else "agent"
            targets.append((m.path, write_member_id, (what,)))
    for path, write, args in targets:
        try:
            added.append((path, write(path, *args)))
        except OSError as e:
            failed.append((path, e.strerror or str(e)))
    return added, failed


class NotATeam(Exception):
    def __init__(self, folder: Path):
        super().__init__(f"{folder} is not a team folder: it has no {TEAM_FILE}")
        self.folder = folder


# --- the package as a whole ------------------------------------------------

def _check_package(package: Package, problems: Problems) -> None:
    for link, target in package.links_outside:
        problems.warning(link, f"this is a link to {target}, outside the package; "
                         "the package will break if it is copied as it is",
                         fix="run `teamrunner gather` to replace it with a real copy")
    if package.has_tools_dir:
        problems.error(package.top / "tools", "a package's own tools come after 0.1",
                       fix="toolkeepers in 0.1 use the built-in tools: "
                       + ", ".join(f"`{t}`" for t in TOOLS) + "; remove the tools/ folder")
    for name, doc in package.models.items():
        if check_schema(doc, "model_profile", problems,
                        V.check_words(doc, V.MODEL_PROFILE, problems, "a model profile")):
            _check_provider(doc.path, doc.data.get("name"), doc.line("name"), problems)
    _scan_secrets(package.top, problems)


def _scan_secrets(top: Path, problems: Problems) -> None:
    for path in sorted(top.rglob("*")):
        inside = path.relative_to(top).parts
        if inside[0] == "runs" or any(p.startswith(".") for p in inside):
            continue  # a package .env is the run's business (D99), not validate's
        if not path.is_file() or path.suffix not in (".md", ".yaml", ".yml", ".json", ".txt"):
            continue
        for number, text in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for hit in SECRET.finditer(text):
                key = hit.group(0)
                problems.error(path, "this looks like an API key, and a package is meant to be shared",
                               line=number, value=key[:6] + "…",
                               fix="remove it; keys live in ~/.teamrunner/.env")


def _check_provider(path, model, line, problems) -> None:
    if not isinstance(model, str) or "/" not in model:
        return
    provider = model.split("/", 1)[0]
    if provider not in V.PROVIDERS:
        hint = V.suggest(provider, V.PROVIDERS)
        problems.error(path, f"`{provider}` is not a model provider TeamRunner knows",
                       line=line, value=model,
                       fix=f"did you mean `{hint}/…`?" if hint else
                       "write the model as `provider/name`, such as `openai/gpt-5`")


# --- a team --------------------------------------------------------------------

def _check_team(team: Team, package: Package, report: Report) -> None:
    problems = report.problems
    team_file = team.folder / TEAM_FILE
    words = set()
    if team.doc is not None:
        words = V.check_words(team.doc, V.TEAM, problems, TEAM_FILE)
        check_schema(team.doc, "team", problems, words)

    for sub in team.subteams:
        problems.error(sub.folder / TEAM_FILE, "teams inside teams (subteams) arrive in TeamRunner 0.3",
                       fix="run this subteam on its own, or keep its members in one team for now")
    for path in team.retired_files:
        instead = "chair.md (the flow as a script)" if path.name == "pathways.yaml" else \
            "whiteboard.yaml (in 0.3)"
        problems.error(path, f"`{path.name}` is no longer used", fix=f"its job is now {instead}")
    if team.chair and team.manager:
        problems.error(team.manager, "this team has both `chair.md` and `manager.md`; keep one")
    elif team.manager:
        problems.error(team.manager, "`manager.md` (a team run by a model's judgment) arrives in TeamRunner 0.3",
                       fix="use `chair.md` with a script, or no file for round-robin")
    flow = None
    if team.chair:
        flow = _read_chair(team.chair, problems)
    elif not team.manager:
        flow = round_robin([m.name for m in team.members if m.kind != "toolkeeper"])
    if team.whiteboard:
        problems.error(team.whiteboard, "`whiteboard.yaml` arrives in TeamRunner 0.3; "
                       "in 0.1 everyone reads everything said",
                       fix="remove it for now")

    _check_names(team, problems)
    by_name = {m.name: m for m in team.members}
    default_model = team.doc.data.get("default_model") if team.doc and "default_model" in words else None
    if isinstance(default_model, str):
        _check_model_ref(team_file, default_model, team.doc.line("default_model"), package, problems)

    for member in team.members:
        _check_member(member, team, by_name, default_model, package, report)

    _check_inputs(team, words, problems)

    if flow is not None:
        check_flow(flow, _cast(team), team.chair or team_file, problems)
        report.flow = flow


def _cast(team: Team) -> Cast:
    kinds = {m.name: m.kind for m in team.members}
    kinds.update({sub.folder.name: "team" for sub in team.subteams})
    kinds.update({member_name(path): "unreadable" for path in team.broken})
    outputs = {}
    may_ask = {}
    for m in team.members:
        try:
            outputs[m.name] = output_fields(m.front.data.get("output"))
        except (AttributeError, KeyError, TypeError, IndexError):
            outputs[m.name] = {}  # already reported by the schema check
        if m.kind == "toolkeeper":
            access = m.front.data.get("access")
            access = access if isinstance(access, dict) else {}
            others = "others" in m.front.data and m.front.data["others"] != "deny"
            may_ask[m.name] = (lambda name, access=access, others=others:
                               access[name] != "deny" if name in access else others)
    return Cast(kinds, outputs, may_ask)


def _read_chair(path: Path, problems: Problems) -> Flow | None:
    text = path.read_text(encoding="utf-8")
    parts = split_frontmatter(text)
    if parts is None:
        return parse_flow(text, path, 1, problems)
    front, body, body_line = parts
    doc = read_yaml(path, problems, front, line_offset=2)
    if doc is None:
        return None
    if doc.data.get("kind") == "rules":
        problems.error(path, "a rules chair (`kind: rules`) arrives in TeamRunner 0.3",
                       line=doc.line("kind"), fix="write the flow as a script instead")
        return None
    for key in doc.data:
        problems.error(path, f"`{key}` is not a setting `chair.md` has in 0.1",
                       line=doc.line(key), value=str(key),
                       fix="in 0.1 the chair is only its script; remove the settings block")
    return parse_flow(body or "", path, body_line, problems)


def _check_names(team: Team, problems: Problems) -> None:
    seen: dict[str, Member] = {}
    for member in team.members:
        name = member.name
        if not WORD.match(name):
            problems.error(member.path, f"`{name}` cannot be a member's name: "
                           "names are letters, digits and underscores",
                           fix="rename the file")
        elif name in V.RESERVED_NAMES:
            problems.error(member.path, f"`{name}` is a word the flow uses, so no member may have it",
                           fix="rename the file")
        if name in seen:
            problems.error(member.path, f"two files give the member name `{name}`: "
                           f"{seen[name].path.name} and {member.path.name}",
                           fix="rename one of them")
        seen.setdefault(name, member)
    for sub in team.subteams:
        if sub.folder.name in seen:
            problems.error(sub.folder, f"the subteam `{sub.folder.name}` has the same name as "
                           f"{seen[sub.folder.name].path.name}", fix="rename one of them")


def _check_model_ref(path, model: str, line, package: Package, problems: Problems) -> None:
    if model.endswith(".yaml"):
        if model not in package.models and not (package.top / MODELS_DIR / model).is_file():
            known = list(package.models)
            hint = V.suggest(model, known)
            problems.error(path, f"there is no model profile `{model}` in models/",
                           line=line, value=model,
                           fix=f"did you mean `{hint}`?" if hint else
                           f"create {MODELS_DIR}/{model} at the top of the package, or name a model `provider/name`")
    else:
        _check_provider(path, model, line, problems)


# --- a member ------------------------------------------------------------------

def _check_member(member: Member, team: Team, by_name, default_model, package, report) -> None:
    problems = report.problems
    doc = member.front
    words = V.check_words(doc, V.MEMBER, problems, "a member file")

    kind = doc.data.get("kind", "llm")
    if "kind" in words:
        if kind in V.KINDS_RETIRED:
            problems.error(member.path, f"`kind: {kind}` is now `kind: {V.KINDS_RETIRED[kind]}`",
                           line=doc.line("kind"), value=kind,
                           fix=f"write `kind: {V.KINDS_RETIRED[kind]}`")
            return
        if kind in V.KINDS_LATER:
            problems.error(member.path, f"`kind: {kind}` arrives in TeamRunner {V.KINDS_LATER[kind]}",
                           line=doc.line("kind"), value=kind)
            return
    if not check_schema(doc, "member", problems, words) and kind not in V.KINDS:
        return

    if kind == "toolkeeper":
        _check_toolkeeper(member, words, by_name, problems)
    else:
        _check_worker(member, words, default_model, package, report)


def _check_worker(member: Member, words, default_model, package, report) -> None:
    problems, doc = report.problems, member.front
    for key in ("tools", "access", "others"):
        if key in words:
            problems.error(member.path, f"only a toolkeeper has `{key}:`",
                           line=doc.line(key),
                           fix="give the tool to a toolkeeper (`kind: toolkeeper`) and let this member ask it")
    model = doc.data.get("model") if "model" in words else None
    if isinstance(model, str):
        _check_model_ref(member.path, model, doc.line("model"), package, problems)
        report.models[member.path] = ModelChoice(model, "its own `model:`")
    elif isinstance(default_model, str):
        report.models[member.path] = ModelChoice(default_model, "`default_model:` in team.yaml")
    elif "model" not in doc.data:
        problems.error(member.path, f"`{member.name}` has no model", line=1,
                       fix="add `model: provider/name` here, or `default_model:` to team.yaml")
    for name in _placeholders(member):
        if name.startswith("whiteboard."):
            problems.error(member.path, f"`{{{{{name}}}}}` reads the whiteboard, which arrives in TeamRunner 0.3",
                           line=_body_line(member, name))
        elif not WORD.match(name):
            problems.error(member.path, f"`{{{{{name}}}}}` is not an input name: "
                           "names are letters, digits and underscores",
                           line=_body_line(member, name), value=name)


def _check_toolkeeper(member: Member, words, by_name, problems: Problems) -> None:
    doc = member.front
    for key in ("model", "params", "output"):
        if key in words:
            problems.error(member.path, f"a toolkeeper has no `{key}:`; it decides by its `access:` table, "
                           "with no model", line=doc.line(key), fix="remove this line")
    tools = doc.data.get("tools") if "tools" in words else None
    if not tools:
        if "tools" not in doc.data:
            problems.error(member.path, "a toolkeeper needs `tools:`, naming the tool it holds",
                           line=1, fix="add, for example, `tools:` with `web_search_brave: {}` under it")
        return
    if len(tools) > 1:
        problems.error(member.path, "a toolkeeper holds one tool in 0.1, and this one names "
                       f"{len(tools)}: " + ", ".join(f"`{t}`" for t in tools),
                       line=doc.line("tools"),
                       fix="split this file into one toolkeeper per tool")
        return
    tool = next(iter(tools))
    if tool not in TOOLS:
        hint = V.suggest(tool, TOOLS)
        problems.error(member.path, f"`{tool}` is not a built-in tool",
                       line=doc.line("tools", tool), value=tool,
                       fix=f"did you mean `{hint}`?" if hint else
                       "0.1 has " + ", ".join(f"`{t}`" for t in TOOLS))
        return
    allowed = guardrails_of(tool)

    def check_terms(terms, *keys):
        if not isinstance(terms, dict):
            return
        for g in terms:
            line = doc.line(*keys, g)
            if g in V.GUARDRAILS_RETIRED:
                problems.error(member.path, f"`{g}` is now `{V.GUARDRAILS_RETIRED[g]}`",
                               line=line, value=g, fix=f"write `{V.GUARDRAILS_RETIRED[g]}`")
            elif g not in allowed:
                hint = V.suggest(g, allowed)
                problems.error(member.path, f"`{tool}` has no guardrail `{g}`",
                               line=line, value=g,
                               fix=f"did you mean `{hint}`?" if hint else
                               "its guardrails are " + ", ".join(f"`{a}`" for a in sorted(allowed)))

    check_terms(tools[tool], "tools", tool)
    access = doc.data.get("access") if "access" in words else None
    if isinstance(access, dict):
        for name, terms in access.items():
            if name == member.name:
                problems.error(member.path, "a toolkeeper does not list itself in `access:`",
                               line=doc.line("access", name), value=name)
            elif name not in by_name:
                hint = V.suggest(name, by_name)
                problems.error(member.path, f"`{name}` in `access:` is not a member of this team",
                               line=doc.line("access", name), value=name,
                               fix=f"did you mean `{hint}`?" if hint else
                               "access is given to members by their file names")
            elif by_name[name].kind == "toolkeeper":
                problems.error(member.path, f"`{name}` is a toolkeeper; toolkeepers do not ask each other",
                               line=doc.line("access", name), value=name)
            check_terms(terms, "access", name)
    if "others" in words:
        check_terms(doc.data["others"], "others")
    if not access and "others" not in words:
        problems.warning(member.path, "no one may use this toolkeeper",
                         fix="list members under `access:`, or add `others: standard`")


# --- inputs ---------------------------------------------------------------------

def _placeholders(member: Member) -> list[str]:
    return [m.group(1) for m in PLACEHOLDER.finditer(member.body)]


def _body_line(member: Member, name: str) -> int | None:
    for i, text in enumerate(member.body.splitlines()):
        if name in text and "{{" in text:
            return member.body_line + i
    return None


def _check_inputs(team: Team, words, problems: Problems) -> None:
    """D70: inputs are the placeholders the workers' prompts use."""
    used: dict[str, Member] = {}
    for member in team.members:
        if member.kind != "toolkeeper":
            for name in _placeholders(member):
                if WORD.match(name):
                    used.setdefault(name, member)
    team_file = team.folder / TEAM_FILE
    doc = team.doc
    defaults = doc.data.get("default_inputs") if doc and "default_inputs" in words else None
    defaults = defaults if isinstance(defaults, dict) else {}

    for name in defaults:
        if name not in used:
            hint = V.suggest(str(name), used)
            problems.warning(team_file, f"no prompt uses the input `{name}`",
                             line=doc.line("default_inputs", name), value=str(name),
                             fix=f"did you mean `{hint}`?" if hint else
                             f"use `{{{{{name}}}}}` in a prompt, or remove the line")
    for name, member in used.items():
        if name not in defaults:
            problems.warning(member.path, f"the input `{name}` has no default, so every run must give it",
                             line=_body_line(member, name),
                             fix=f"add `{name}:` under `default_inputs:` in team.yaml, "
                             "or give it with `--message` or `--task-file`")

    if doc and "message_goes_to" in words:
        target = doc.data["message_goes_to"]
        if isinstance(target, str) and target not in used:
            hint = V.suggest(target, used)
            problems.error(team_file, f"`message_goes_to: {target}` names an input no prompt uses",
                           line=doc.line("message_goes_to"), value=target,
                           fix=f"did you mean `{hint}`?" if hint else
                           f"use `{{{{{target}}}}}` in a prompt, or remove the line")
