# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
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
from .ir import Flow, model_list, output_fields
from .loader import (CHAIR_FILE, MODELS_DIR, TEAM_FILE, Member, Package, Team, read_file,
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
    r"|tvly-[A-Za-z0-9]{20,}|hf_[A-Za-z0-9]{30,}|gsk_[A-Za-z0-9]{40,}|xai-[A-Za-z0-9]{40,}"
    r"|BSA[A-Za-z0-9_-]{20,})"
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
        problems.error(package.top / "tools", f"a package's own tools {V.LATER}",
                       fix="for now toolkeepers use the built-in tools: "
                       + ", ".join(f"`{t}`" for t in TOOLS) + "; remove the tools/ folder")
    for name, doc in package.models.items():
        if check_schema(doc, "model_profile", problems,
                        V.check_words(doc, V.MODEL_PROFILE, problems, "a model profile")):
            _check_provider(doc.path, doc.data.get("name"), doc.line("name"), problems)
        V.check_params(doc.path, doc.data.get("params"), problems, lambda key, doc=doc: doc.line("params", key))
    _scan_secrets(package.top, problems)


# Text a package or an experiment may hold, where a key could be pasted by mistake.
SCANNED = (".md", ".yaml", ".yml", ".json", ".txt", ".csv", ".tsv", ".py", ".toml", ".ini", ".cfg")


def _scan_secrets(top: Path, problems: Problems, skip: tuple[str, ...] = ("runs",)) -> None:
    """Every text file in `top` but `skip`ped folders and hidden ones (a package .env is the
    run's business, D99): a key in a file that is shared goes with it."""
    for path in sorted(top.rglob("*")):
        inside = path.relative_to(top).parts
        if inside[0] in skip or any(p.startswith(".") for p in inside):
            continue
        if not path.is_file() or path.suffix.lower() not in SCANNED:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue  # a member or setting file that cannot be read is reported where it is read
        for number, text in enumerate(content.splitlines(), 1):
            for hit in SECRET.finditer(text):
                key = hit.group(0)
                problems.error(path, "this looks like an API key, and a package is meant to be shared",
                               line=number, value=key[:6] + "…",
                               fix="remove it; keys live in ~/.teamrunner/.env")


def _check_provider(path, model, line, problems) -> None:
    if not isinstance(model, str) or "/" not in model:
        return
    provider = model.split("/", 1)[0]
    if provider == "teamrunner" and model != "teamrunner/demoLLM":
        problems.error(path, f"`{model}` is not a TeamRunner model", line=line, value=model,
                       fix="use `teamrunner/demoLLM` for the free hosted demo")
        return
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
        problems.error(sub.folder / TEAM_FILE, f"teams inside teams (subteams) {V.LATER}",
                       fix="run this subteam on its own, or keep its members in one team for now")
    for path in team.retired_files:
        instead = "chair.md (the flow as a script)" if path.name == "pathways.yaml" else \
            "whiteboard.yaml, in a later version"
        problems.error(path, f"`{path.name}` is no longer used", fix=f"its job is now {instead}")
    if team.chair and team.manager:
        problems.error(team.manager, "this team has both `chair.md` and `manager.md`; keep one")
    elif team.manager:
        problems.error(team.manager, f"`manager.md` (a team run by a model's judgment) {V.LATER}",
                       fix="use `chair.md` with a script, or no file for round-robin")
    flow = None
    if team.chair:
        flow = _read_chair(team.chair, problems)
    elif not team.manager:
        flow = round_robin([m.name for m in team.members if m.kind != "toolkeeper"])
    if team.whiteboard:
        problems.error(team.whiteboard, f"`whiteboard.yaml` {V.LATER}; "
                       "for now everyone reads everything said",
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


# What each guardrail's value must be; a value of another shape is an error, not a crash
# later (review 2026-11-16, C5).
_WHOLE = ("calls_per_turn", "calls_per_run", "max_results", "max_chars")
_FLAGS = ("network", "keep_results", "reply_only")
_LISTS = ("domains", "paths", "types")
_WORDS = ("mailbox", "only_input")


def _guardrail_value(name: str, value) -> str | None:
    """What `value` should be, if it is not; None when it fits."""
    if name in _WHOLE and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
        return "a whole number, 1 or more"
    if name == "timeout" and (isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0):
        return "a number of seconds, more than 0"
    if name in _FLAGS and not isinstance(value, bool):
        return "`true` or `false`"
    if name in _LISTS and not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
        return "a list, such as `[example.org, example.com]`"
    if name in _WORDS and not isinstance(value, str):
        return "one name, not a list"
    return None


def _read_chair(path: Path, problems: Problems) -> Flow | None:
    text = read_file(path, problems)
    if text is None:
        return None
    parts = split_frontmatter(text)
    if parts is None:
        return parse_flow(text, path, 1, problems)
    front, body, body_line = parts
    doc = read_yaml(path, problems, front, line_offset=2)
    if doc is None:
        return None
    if doc.data.get("kind") == "rules":
        problems.error(path, f"a rules chair (`kind: rules`) {V.LATER}",
                       line=doc.line("kind"), fix="write the flow as a script instead")
        return None
    for key in doc.data:
        problems.error(path, f"`{key}` is not a setting `chair.md` has",
                       line=doc.line(key), value=str(key),
                       fix="the chair is only its script; remove the settings block")
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
    # An id keeps one member's history together; two files with one id would mix theirs.
    ids: dict[str, Member] = {}
    for member in team.members:
        written = member.front.data.get("id")
        if not isinstance(written, str):
            continue
        if written in ids:
            problems.error(member.path, f"{ids[written].path.name} and {member.path.name} have the same `id:`; "
                           "each member needs its own", line=member.front.line("id"), value=written,
                           fix=f"delete the `id:` line in {member.path.name} (a copied file?) "
                           "and TeamRunner will write a new one")
        ids.setdefault(written, member)
    for sub in team.subteams:
        if sub.folder.name in seen:
            problems.error(sub.folder, f"the subteam `{sub.folder.name}` has the same name as "
                           f"{seen[sub.folder.name].path.name}", fix="rename one of them")


def _check_model_ref(path, written: str, line, package: Package, problems: Problems) -> None:
    """`model:` or `default_model:`: one model, or several tried in order (D135)."""
    seen = []
    for model in model_list(written):
        if model in seen:
            problems.error(path, f"`{model}` is named twice in this list of models",
                           line=line, value=model, fix="keep the first and remove the other")
            continue
        seen.append(model)
        _check_one_model(path, model, line, package, problems)


def _check_one_model(path, model: str, line, package: Package, problems: Problems) -> None:
    if model.endswith(".yaml"):
        if "/" in model or "\\" in model:  # profiles are read from models/ itself, not its folders
            name = model.replace("\\", "/").rsplit("/", 1)[1]
            problems.error(path, f"`{model}` names a folder: model profiles sit directly in models/, "
                           "and its folders are not read", line=line, value=model,
                           fix=f"move the file to {MODELS_DIR}/{name} and write `{name}`")
        elif model not in package.models and not (package.top / MODELS_DIR / model).is_file():
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
    if "kind" in words and not isinstance(kind, str):  # `kind: [x]` (review 2026-11-16, C5)
        problems.error(member.path, "`kind:` is one word: `llm` (the default) or `toolkeeper`",
                       line=doc.line("kind"), value=str(kind))
        return
    if "kind" in words:
        if kind in V.KINDS_RETIRED:
            problems.error(member.path, f"`kind: {kind}` is now `kind: {V.KINDS_RETIRED[kind]}`",
                           line=doc.line("kind"), value=kind,
                           fix=f"write `kind: {V.KINDS_RETIRED[kind]}`")
            return
        if kind in V.KINDS_LATER:
            problems.error(member.path, f"`kind: {kind}` {V.LATER}",
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
    if "params" in words:
        V.check_params(member.path, doc.data.get("params"), problems, lambda key: doc.line("params", key))
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
            problems.error(member.path, f"`{{{{{name}}}}}` reads the whiteboard, which {V.LATER}",
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
    if tools and not isinstance(tools, dict):  # `tools: 5` (review 2026-11-16, C5)
        problems.error(member.path, "`tools:` names the tool this toolkeeper holds, with its terms under it",
                       line=doc.line("tools"), value=str(tools),
                       fix="write, for example, `tools:` and under it `  web_search_brave: {max_results: 5}`")
        return
    if not tools:
        if "tools" not in doc.data:
            problems.error(member.path, "a toolkeeper needs `tools:`, naming the tool it holds",
                           line=1, fix="add, for example, `tools:` with `web_search_brave: {}` under it")
        return
    if len(tools) > 1:
        problems.error(member.path, "a toolkeeper holds one tool, and this one names "
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
                       "the built-in tools are " + ", ".join(f"`{t}`" for t in TOOLS))
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
            elif (wrong := _guardrail_value(g, terms[g])) is not None:
                problems.error(member.path, f"`{g}` should be {wrong}", line=line, value=str(terms[g]))

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

    for member in team.members:  # `only_input:` names the input that holds the one item a toolkeeper hands over
        if member.kind != "toolkeeper":
            continue
        data = member.front.data
        tools = data.get("tools") if isinstance(data.get("tools"), dict) else {}
        access = data.get("access") if isinstance(data.get("access"), dict) else {}
        places = [(("tools", n), g) for n, g in tools.items()] + \
                 [(("access", n), g) for n, g in access.items()] + [(("others",), data.get("others"))]
        for keys, terms in places:
            if isinstance(terms, dict) and isinstance(terms.get("only_input"), str) and terms["only_input"] not in used:
                named = str(terms["only_input"])
                hint = V.suggest(named, used)
                problems.error(member.path, f"`only_input: {named}` names an input no prompt uses",
                               line=member.front.line(*keys, "only_input"), value=named,
                               fix=f"did you mean `{hint}`?" if hint else
                               "name an input a prompt uses as `{{name}}`")

    if doc and "message_goes_to" in words:
        target = doc.data["message_goes_to"]
        if isinstance(target, str) and target not in used:
            hint = V.suggest(target, used)
            problems.error(team_file, f"`message_goes_to: {target}` names an input no prompt uses",
                           line=doc.line("message_goes_to"), value=target,
                           fix=f"did you mean `{hint}`?" if hint else
                           f"use `{{{{{target}}}}}` in a prompt, or remove the line")
