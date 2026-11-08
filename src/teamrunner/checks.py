"""The check before the first turn (D99, D100): every key found, every
model reachable — every problem at once, in one table, before any tokens.

A model is never swapped for one the team did not name: close names are
suggested, never used. A member that names several models, in order of
preference, runs on the first one available, and the check says so (D135).
"""

from __future__ import annotations

import difflib
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

from . import ir
from .keys import NO_KEY, PROVIDER_KEYS, SET_UP_ELSEWHERE, Keys

# provider -> a function (key, settings) -> the model names it serves, or None if it cannot say
Lister = Callable[[str | None, dict], "set[str] | None"]


def online_check(report):
    """The explicit CLI/browser online check, without printing or prompting."""
    from .build import load_team
    from .keys import extra_settings, find_keys
    team, _ = load_team(report.package.team.folder)
    keys = find_keys(needed_keys(team), team.package_top, None)
    return run_check(team, keys, LISTERS, extra_settings(), litellm_data, litellm_params), keys


@dataclass
class Problem:
    what: str  # the model or key
    who: list[str]  # the members who need it
    wrong: str
    fix: str


@dataclass
class Check:
    models: list[dict] = field(default_factory=list)
    keys: list[dict] = field(default_factory=list)
    tools: list[dict] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    backups: list[dict] = field(default_factory=list)  # members on a later choice, and why (D135)

    @property
    def ok(self) -> bool:
        return not self.problems

    def event(self) -> dict:
        return {"event": "check", "models": self.models, "keys": self.keys, "tools": self.tools,
                "problems": [p.__dict__ for p in self.problems], "warnings": self.warnings,
                "backups": self.backups}


def needed_keys(team: ir.Team) -> list[str]:
    from .builtin_tools import TOOL_KEYS
    names = []
    for model in _candidates(team):  # a backup's key too: it may be the one that runs (D135)
        name = PROVIDER_KEYS.get(model.split("/", 1)[0])
        if name and name not in names:
            names.append(name)
    for m in team.members.values():
        name = TOOL_KEYS.get(m.tool) if m.kind == "toolkeeper" else None
        if name and name not in names:
            names.append(name)
    return names


def _models(team: ir.Team) -> dict[str, list[str]]:
    used: dict[str, list[str]] = {}
    for m in team.members.values():
        if m.model is not None:
            used.setdefault(m.model.value, []).append(m.name)
    return used


def _candidates(team: ir.Team) -> list[str]:
    """Every model any member may run on, first choices first."""
    found: list[str] = []
    for depth in range(max((len(m.choices) for m in team.members.values()), default=0)):
        for m in team.members.values():
            if depth < len(m.choices) and m.choices[depth].model.value not in found:
                found.append(m.choices[depth].model.value)
    return found


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


DATED = re.compile(r"-(\d{8}|\d{4}-\d{2}-\d{2})$")


def served(name: str, available: set[str]) -> bool:
    """`claude-sonnet-4-5` is served when the list has it, or a dated version
    of it (`claude-sonnet-4-5-20250929`); `llama3` when Ollama has `llama3:latest`."""
    for a in available:
        if a == name or (a.startswith(name + "-") and DATED.fullmatch(a[len(name):])):
            return True
        if ":" not in name and a.startswith(name + ":"):
            return True
    return False


# member settings -> None if the model takes them, else the reason it refuses them
ParamCheck = Callable[[str, dict], "str | None"]


def litellm_params(model: str, params: dict) -> str | None:
    """Ask LiteLLM, offline, whether a model takes these settings: it refuses
    some before any request is sent (gpt-5 takes only temperature 1 while reasoning)."""
    import litellm
    litellm.suppress_debug_info = True
    try:
        litellm.completion(model=model, messages=[{"role": "user", "content": "check"}],
                           mock_response="ok", api_key="check", **params)
    except litellm.UnsupportedParamsError as e:
        reason = str(e).split("\n")[0].removeprefix("litellm.UnsupportedParamsError: ")
        return reason.split(" To drop unsupported params")[0]  # LiteLLM's advice to programmers
    except Exception:
        return None  # anything else is not about the settings; the run will say
    return None


def run_check(team: ir.Team, keys: Keys, listers: dict[str, Lister], settings: dict,
              model_data: Callable[[str], dict] | None = None,
              params_check: ParamCheck | None = None) -> Check:
    check = Check()
    for name, source in keys.sources.items():
        check.keys.append({"name": name, "source": source})
    check.warnings += keys.warnings

    lists: dict[str, object] = {}  # provider -> its model list, asked once
    probes: dict[str, Probe] = {}

    def probe(model: str) -> Probe:
        if model not in probes:
            probes[model] = _probe(model, keys, listers, settings, lists)
        return probes[model]

    runs_on: dict[str, list[str]] = {}  # model -> the members who will run on it
    stuck: dict[str, list[str]] = {}  # model -> members none of whose choices is available
    for m in team.members.values():
        if not m.choices:
            continue
        skipped = []
        for number, choice in enumerate(m.choices, 1):
            found = probe(choice.model.value)
            if found.problem is None:
                break
            skipped.append({"model": choice.model.value, "written": choice.written,
                            "reason": found.reason})
        else:
            for choice in m.choices:
                stuck.setdefault(choice.model.value, []).append(m.name)
            continue
        m.model, m.params, m.skipped = choice.model, dict(choice.params), skipped
        runs_on.setdefault(choice.model.value, []).append(m.name)
        if skipped:
            check.backups.append({"member": m.name, "model": choice.model.value, "choice": number,
                                  "of": len(m.choices), "skipped": skipped})

    for model in [*runs_on, *(m for m in stuck if m not in runs_on)]:
        who = runs_on.get(model, []) + [n for n in stuck.get(model, []) if n not in runs_on.get(model, [])]
        found = probe(model)
        check.models.append({"model": model, "members": who, **found.row})
        if found.problem is not None:
            if model in stuck:
                check.problems.append(found.problem(who))
            continue
        check.warnings += found.warnings
        data = (model_data or (lambda m: {}))(model) if found.row.get("available") else {}
        if data.get("deprecation_date"):
            check.models[-1]["retires_on"] = data["deprecation_date"]
            check.warnings.append(f"{model} retires on {data['deprecation_date']} ({_join(who)})")
        deciders = [m.name for m in team.members.values()
                    if m.model and m.model.value == model and m.output]
        if deciders and data and data.get("supports_response_schema") is False:
            check.warnings.append(f"{model} may not keep to a fixed answer form, which "
                                  f"{_join(deciders)} need{'s' if len(deciders) == 1 else ''}")
    _check_params(team, check, params_check)
    _check_tools(team, keys, check)
    return check


@dataclass
class Probe:
    """What the check found out about one model, before knowing who runs on it."""
    row: dict  # `available`, `retires_on`, and a local model's `digest`
    problem: Callable[[list[str]], Problem] | None = None  # who -> the problem, if it can't run
    reason: str = ""  # why it was passed over, in a few words (D135)
    warnings: list[str] = field(default_factory=list)


def _probe(model: str, keys: Keys, listers: dict[str, Lister], settings: dict, lists: dict) -> Probe:
    provider, _, name = model.partition("/")
    row = {"available": None, "retires_on": None}
    key_name = PROVIDER_KEYS.get(provider)
    if key_name in keys.missing:
        return Probe(row, lambda who: Problem(
            key_name, who, f"{_join(who)} use{'s' if len(who) == 1 else ''} {model}, which needs "
            f"{key_name}; not found in ~/.teamrunner/.env, your shell or the package",
            f"add `{key_name}=…` to ~/.teamrunner/.env"), f"no {key_name} was found")
    if provider in SET_UP_ELSEWHERE:
        return Probe(row, warnings=[f"{model} is not checked before the run; "
                                    f"{provider} is set up as LiteLLM documents it"])
    lister = listers.get(provider)
    if lister is None:
        return Probe(row, warnings=[f"{model} cannot be checked before the run"])
    if provider not in lists:
        try:
            lists[provider] = lister(keys.values.get(key_name) if key_name else None, settings)
        except Exception as e:
            lists[provider] = e
    available = lists[provider]
    if isinstance(available, Exception):
        e = available
        reason = str(e)
        for secret in keys.values.values():
            if secret:
                reason = reason.replace(secret, "[key hidden]")
        return Probe(row, lambda who: Problem(
            model, who, f"could not reach {provider} to check it: {reason}",
            "check your connection" + (", and that Ollama is running" if provider in NO_KEY else
                                       f", and that {key_name} is right")),
            f"{provider} could not be reached")
    if available is None:
        return Probe(row)
    row["available"] = served(name, available)
    if not row["available"]:
        close = difflib.get_close_matches(name, sorted(available), n=1, cutoff=0.6)
        local = provider in NO_KEY
        return Probe(row, lambda who: Problem(
            model, who, f"{provider} does not serve `{name}`" +
            (" (pulled models are listed by `ollama list`)" if local else ""),
            f"did you mean `{provider}/{close[0]}`?" if close else
            (f"pull it with `ollama pull {name}`" if local else "check the model's name")),
            "it is not pulled in Ollama" if local else f"{provider} does not serve it")
    if isinstance(available, dict):  # a local model's exact build (Ollama's digest)
        row["digest"] = next((available[a] for a in (name, name + ":latest") if a in available), None)
    return Probe(row)


def _check_params(team: ir.Team, check: Check, params_check: ParamCheck | None) -> None:
    """Every member's model and settings together, so a refusal costs nothing (D100)."""
    if params_check is None:
        return
    seen: dict[tuple, list[str]] = {}
    for m in team.members.values():
        if m.model is None or not m.params:
            continue
        params = {k: s.value for k, s in m.params.items()}
        seen.setdefault((m.model.value, json.dumps(params, sort_keys=True)), []).append(m.name)
    for (model, params), who in seen.items():
        refused = params_check(model, json.loads(params))
        if refused:
            check.problems.append(Problem(
                model, who, f"{_join(who)}'s settings: {refused}",
                "change or remove the setting in the member's params (or its model profile)"))


def rehearsal_check(team: ir.Team) -> Check:
    """A rehearsal calls no model and needs no key (D115); the tools are still checked."""
    check = Check()
    check.models = [{"model": model, "members": who, "available": None, "retires_on": None}
                    for model, who in _models(team).items()]
    _check_tools(team, Keys(), check)
    return check


def _check_tools(team: ir.Team, keys: Keys, check: Check) -> None:
    from .builtin_tools import NOT_BUILT, TOOL_KEYS
    for keeper in team.members.values():
        if keeper.kind != "toolkeeper":
            continue
        check.tools.append({"toolkeeper": keeper.name, "tool": keeper.tool})
        users = sorted(set(keeper.access) | ({"others"} if keeper.others else set()))
        key_name = TOOL_KEYS.get(keeper.tool)
        if key_name in keys.missing:
            check.problems.append(Problem(
                key_name, users, f"{keeper.name} holds `{keeper.tool}`, which needs {key_name}; "
                "not found in ~/.teamrunner/.env, your shell or the package",
                f"add `{key_name}=…` to ~/.teamrunner/.env (a Brave Search API key)"))
        if keeper.tool in NOT_BUILT:
            check.problems.append(Problem(
                keeper.tool, users, f"{keeper.name} holds `{keeper.tool}`, which this version "
                f"of TeamRunner cannot run yet: {NOT_BUILT[keeper.tool]}",
                "run a team without it for now"))


# --- asking providers which models they serve (free, no tokens) ---------------------

def _get(url: str, headers: dict) -> dict:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def _openai(key, settings):
    data = _get("https://api.openai.com/v1/models", {"Authorization": f"Bearer {key}"})
    return {m["id"] for m in data["data"]}


def _anthropic(key, settings):
    data = _get("https://api.anthropic.com/v1/models?limit=1000",
                {"x-api-key": key, "anthropic-version": "2023-06-01"})
    return {m["id"] for m in data["data"]}


def _gemini(key, settings):
    data = _get(f"https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000&key={key}", {})
    return {m["name"].removeprefix("models/") for m in data["models"]}


def _ollama(key, settings):
    """The pulled models, each with its digest: the build that will run, recorded in the check."""
    base = settings.get("OLLAMA_API_BASE", "http://localhost:11434").rstrip("/")
    data = _get(f"{base}/api/tags", {})
    return {m["name"]: m.get("digest") for m in data["models"]}


LISTERS: dict[str, Lister] = {"openai": _openai, "anthropic": _anthropic, "gemini": _gemini,
                              "ollama": _ollama}


def litellm_data(model: str) -> dict:
    import litellm
    name = model.split("/", 1)[1]
    return litellm.model_cost.get(model) or litellm.model_cost.get(name) or {}
