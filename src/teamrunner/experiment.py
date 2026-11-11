"""Experiments (D138, SPEC §5): read `experiment.md`, build its conditions,
check each as a team, and plan the runs — spending nothing.

    experiment.md → conditions (as written, or built from `change:` into
    conditions/<name>/) → each validated as a team → study hash per
    condition → permuted rounds and run seeds from the master seed

Running them is the next step; this module only reads, builds and plans.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import shutil
from dataclasses import dataclass, field, replace
from pathlib import Path

from ruamel.yaml.comments import CommentedMap

from . import vocabulary as V
from .inputs import InputError, read_task_file
from .loader import (CHAIR_FILE, MANAGER_FILE, MODELS_DIR, RUNS_DIR, TEAM_FILE, WHITEBOARD_FILE,
                     Doc, ThreadYAML, find_top, member_name, read_yaml, split_frontmatter)
from .problems import Problems
from .schema_check import plain
from .validate import NotATeam, Report, validate

EXPERIMENT_FILE = "experiment.md"
CONDITIONS_DIR = "conditions"
# Each folder TeamRunner builds under conditions/ carries this note, which says
# it is rebuilt and is how a rebuild knows the folder is its own to replace.
# A .md without a settings block is a note, never a member (D91).
MARKER = "GENERATED.md"
MARKER_TEXT = """\
# Built by TeamRunner — do not edit

This folder is the condition `{name}` of the experiment in `{experiment}`.
TeamRunner builds it from `{base}` and the condition's `change:`, and builds
it again before every run, so an edit made here is lost.

To change this condition, edit `experiment.md` or the files in `variants/`.
"""

REMOVE = "remove"
FILE = "file:"
RESERVED_FILES = {"chair": CHAIR_FILE, "manager": MANAGER_FILE}
LARGE = 1 << 20  # unchanged files at least this big are hard-linked, not copied (D138 (4))
SPEC_SUFFIXES = (".md", ".yaml", ".yml")
NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_+-]*$")
LATER = "0.3"  # what the examples' whiteboards and subteams need

EXPERIMENT = V.Words(
    known=frozenset({"name", "conditions", "base", "factors", "task", "replications", "seed",
                     "metrics", "limits", "allow_single_run"}),
    later={},
    retired={"patch": "write `change:`, under each condition or level",
             "conditions_order": "the order is drawn from `seed:`"},
)
LIMITS = frozenset({"max_concurrent", "max_cost_usd"})
LEVEL_WORDS = frozenset({"label", "change"})
CONDITION_WORDS = frozenset({"from", "change"})

_yaml = ThreadYAML(width=4096)  # one per thread: an experiment's runs read files at once


# --- what an experiment is ------------------------------------------------------

@dataclass
class Condition:
    name: str
    base: Path  # the team folder it starts from, as named in experiment.md
    changes: dict[str, object]  # change key -> value, as written
    levels: dict[str, str] = field(default_factory=dict)  # factor -> level label
    line: int | None = None
    # Filled in by `build`:
    folder: Path | None = None  # the team folder that runs: the base itself, or inside conditions/<name>/
    generated: bool = False
    report: Report | None = None
    inputs: dict | None = None  # every input's value: the task's, else the team's defaults
    study_hash: str | None = None


@dataclass
class Experiment:
    folder: Path
    path: Path
    doc: Doc | None
    body: str = ""
    name: str | None = None
    conditions: list[Condition] = field(default_factory=list)
    factors: dict[str, list[str]] | None = None  # factor -> level labels, in the full form
    task: Path | None = None
    replications: int | None = None
    seed: int | None = None
    metrics: list[Path] = field(default_factory=list)
    limits: dict = field(default_factory=dict)
    problems: Problems = field(default_factory=Problems)
    # What this TeamRunner cannot do yet, found while building: (file, line, what)
    later: list[tuple[Path, int | None, str]] = field(default_factory=list)
    # Set by `write_ids` before a run: building then gives each whole-file variant an id
    writing_ids: bool = False
    measures: list = field(default_factory=list)  # the metrics, read and checked (metrics.py)
    outputs_measured: list = field(default_factory=list)  # (path, line): members' typed outputs under metrics:

    @property
    def ok(self) -> bool:
        return not self.problems.errors and not self.later

    @property
    def runs(self) -> int:
        return len(self.conditions) * (self.replications or 0)


class NotAnExperiment(Exception):
    def __init__(self, folder: Path, why: str | None = None):
        super().__init__(why or f"{folder} is not an experiment: it has no {EXPERIMENT_FILE}")
        self.folder = folder


# --- reading experiment.md --------------------------------------------------------

def read_experiment(folder: Path) -> Experiment:
    """Parse `experiment.md` and check its settings; nothing is built yet."""
    folder = Path(folder).resolve()
    path = folder / EXPERIMENT_FILE
    if not path.is_file():
        old = folder / "experiment.yaml"
        if old.is_file():
            raise NotAnExperiment(folder, f"{old} is the old form: an experiment is now `experiment.md`, "
                                  "its settings between `---` lines at the top and any notes below "
                                  "(D138); `patch:` is now `change:`")
        raise NotAnExperiment(folder)
    problems = Problems()
    text = path.read_text(encoding="utf-8")
    parts = split_frontmatter(text)
    if parts is None or parts[1] is None:
        problems.error(path, "the settings should be at the top, between two lines of `---`",
                       line=1, fix="start the file with `---`, the settings, then `---`")
        return Experiment(folder, path, None, problems=problems)
    front, body, _ = parts
    doc = read_yaml(path, problems, front, line_offset=2)
    exp = Experiment(folder, path, doc, body=body.strip(), problems=problems)
    if doc is None:
        return exp
    words = V.check_words(doc, EXPERIMENT, problems, EXPERIMENT_FILE)
    data = doc.data

    def need(key, fix):
        if key not in data:
            problems.error(path, f"`{key}:` is missing", line=1, fix=fix)

    need("name", "add `name:` with a short name for the experiment")
    if isinstance(data.get("name"), str):
        exp.name = data["name"]
    elif "name" in data:
        problems.error(path, "`name:` should be a short name", line=doc.line("name"))

    _read_replications(exp, data, doc, words)
    if "seed" in words:
        if isinstance(data["seed"], int) and not isinstance(data["seed"], bool) and data["seed"] >= 0:
            exp.seed = data["seed"]
        else:
            problems.error(path, "`seed:` should be a whole number, such as 7", line=doc.line("seed"),
                           value=str(data["seed"]))
    else:
        need("seed", "add `seed:` with any whole number; the order of runs and every run's seed come from it")

    has_conditions, has_factors = "conditions" in words, "factors" in words
    if has_conditions and (has_factors or "base" in words):
        problems.error(path, "this file has both `conditions:` and `base:` with `factors:`; use one form",
                       line=doc.line("conditions"),
                       fix="list each condition under `conditions:`, or give `base:` and `factors:`")
    elif has_conditions:
        _read_conditions(exp, data["conditions"], doc)
    elif has_factors or "base" in words:
        if not has_factors:
            problems.error(path, "`base:` needs `factors:` beside it", line=doc.line("base"),
                           fix="add `factors:`, or list the conditions under `conditions:`")
        elif "base" not in words:
            problems.error(path, "`factors:` needs `base:`, the team every condition starts from",
                           line=doc.line("factors"), fix="add `base: <folder>`")
        else:
            _read_factors(exp, data["base"], data["factors"], doc)
    else:
        problems.error(path, "an experiment needs `conditions:`, or `base:` with `factors:`", line=1,
                       fix="name each condition and its folder under `conditions:`")

    if "task" in words:
        exp.task = _task_path(exp, data["task"], doc)
    if "metrics" in words:
        _read_metrics(exp, data["metrics"], doc)
    if "limits" in words:
        _read_limits(exp, data["limits"], doc)
    return exp


def _read_replications(exp: Experiment, data, doc: Doc, words) -> None:
    path, problems = exp.path, exp.problems
    if "replications" not in words:
        if "replications" not in data:
            problems.error(path, "`replications:` is missing", line=1,
                           fix="add `replications:`, how many runs each condition gets (at least 2)")
        return
    n = data["replications"]
    line = doc.line("replications")
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        problems.error(path, "`replications:` should be a whole number of runs per condition",
                       line=line, value=str(n))
        return
    single = data.get("allow_single_run") is True
    if n == 1 and not single:
        problems.error(path, "one run per condition says little about a model that answers "
                       "differently each time (D7)", line=line, value="1",
                       fix="give 2 or more, or add `allow_single_run: true` to run each once anyway")
        return
    if "allow_single_run" in data and not isinstance(data["allow_single_run"], bool):
        problems.error(path, "`allow_single_run:` is `true` or `false`", line=doc.line("allow_single_run"))
    exp.replications = n


def _condition_name(exp: Experiment, name, line) -> bool:
    if not isinstance(name, str) or not NAME.match(name):
        exp.problems.error(exp.path, f"`{name}` cannot be a condition's name: it becomes a folder's name, "
                           "so use letters, digits, `_` and `-`", line=line, value=str(name))
        return False
    return True


def _folder(exp: Experiment, written, line, what: str) -> Path | None:
    if not isinstance(written, str) or not written.strip():
        exp.problems.error(exp.path, f"{what} should be a folder, as a path from this experiment's folder",
                           line=line, value=str(written))
        return None
    path = (exp.folder / written).resolve()
    if path.is_file() and path.suffix == ".md":
        exp.problems.error(exp.path, f"`{written}` is an agent file; TeamRunner runs team folders so far",
                           line=line, value=written,
                           fix="put the agent in a team folder (a folder with team.yaml) and name that")
        return None
    if not path.is_dir():
        exp.problems.error(exp.path, f"there is no folder `{written}`", line=line, value=written,
                           fix=f"the path is read from {exp.folder.name}/; check each `..`")
        return None
    if not (path / TEAM_FILE).is_file():
        exp.problems.error(exp.path, f"`{written}` is not a team folder: it has no {TEAM_FILE}",
                           line=line, value=written)
        return None
    top = find_top(path)
    if exp.folder == top or exp.folder.is_relative_to(top):
        exp.problems.error(exp.path, f"this experiment is inside the team it studies, `{top.name}`",
                           line=line, value=written,
                           fix="move the experiment folder out of the package; it changes nothing in it")
        return None
    return path


def _changes(exp: Experiment, value, line, where: str) -> dict | None:
    if value is None:
        return {}
    if not isinstance(value, dict):
        exp.problems.error(exp.path, f"`change:` {where} should be `what: new value` lines, or `{{}}` for none",
                           line=line)
        return None
    return dict(value)


def _read_conditions(exp: Experiment, conditions, doc: Doc) -> None:
    problems = exp.problems
    if not isinstance(conditions, dict) or not conditions:
        problems.error(exp.path, "`conditions:` should name each condition, one per line",
                       line=doc.line("conditions"))
        return
    for name, spec in conditions.items():
        line = doc.line("conditions", name)
        if not _condition_name(exp, name, line):
            continue
        if isinstance(spec, str):
            base = _folder(exp, spec, line, f"`{name}`")
            if base:
                exp.conditions.append(Condition(name, base, {}, line=line))
            continue
        if not isinstance(spec, dict):
            problems.error(exp.path, f"`{name}:` should be a folder, or `from:` a folder and `change:`",
                           line=line)
            continue
        for key in spec:
            if key not in CONDITION_WORDS:
                hint = "change" if key == "patch" else V.suggest(str(key), CONDITION_WORDS)
                problems.error(exp.path, f"`{key}` is not a word a condition has", line=doc.line("conditions", name, key),
                               value=str(key), fix=f"did you mean `{hint}`?" if hint else "a condition has `from:` and `change:`")
        if "from" not in spec:
            problems.error(exp.path, f"`{name}:` needs `from:`, the folder it starts from", line=line)
            continue
        base = _folder(exp, spec["from"], doc.line("conditions", name, "from"), f"`from:` in `{name}`")
        changes = _changes(exp, spec.get("change"), doc.line("conditions", name, "change"), f"in `{name}`")
        if base and changes is not None:
            exp.conditions.append(Condition(name, base, changes, line=line))


def _read_factors(exp: Experiment, base_written, factors, doc: Doc) -> None:
    problems = exp.problems
    base = _folder(exp, base_written, doc.line("base"), "`base:`")
    if not isinstance(factors, dict) or not factors:
        problems.error(exp.path, "`factors:` should name each factor, with its levels under it",
                       line=doc.line("factors"))
        return
    levels_of: dict[str, list[tuple[str, dict, int | None]]] = {}
    for factor, levels in factors.items():
        fline = doc.line("factors", factor)
        if not isinstance(factor, str) or not NAME.match(factor) or "+" in factor:
            problems.error(exp.path, f"`{factor}` cannot be a factor's name: use letters, digits and `_`",
                           line=fline, value=str(factor))
            continue
        if not isinstance(levels, list) or not levels:
            problems.error(exp.path, f"`{factor}:` should list its levels, each `- label: …` with `change:`",
                           line=fline)
            continue
        seen = []
        for i, level in enumerate(levels):
            lline = doc.line("factors", factor, i)
            if not isinstance(level, dict):
                problems.error(exp.path, f"each level of `{factor}` should have `label:` and `change:`",
                               line=lline)
                continue
            for key in level:
                if key not in LEVEL_WORDS:
                    hint = "change" if key == "patch" else V.suggest(str(key), LEVEL_WORDS)
                    problems.error(exp.path, f"`{key}` is not a word a level has",
                                   line=doc.line("factors", factor, i, key), value=str(key),
                                   fix=f"did you mean `{hint}`?" if hint else "a level has `label:` and `change:`")
            label = level.get("label", f"{factor}_{i + 1}")
            if not isinstance(label, str) or not NAME.match(label) or "+" in label:
                problems.error(exp.path, f"`{label}` cannot be a level's label: it becomes part of a folder's "
                               "name, so use letters, digits, `_` and `-`", line=lline, value=str(label))
                continue
            if label in seen:
                problems.error(exp.path, f"`{factor}` has two levels labelled `{label}`", line=lline, value=label,
                               fix="give each level its own label")
                continue
            seen.append(label)
            changes = _changes(exp, level.get("change"), doc.line("factors", factor, i, "change"),
                               f"in `{factor}: {label}`")
            if changes is not None:
                levels_of.setdefault(factor, []).append((label, changes, lline))
    if base is None or len(levels_of) != len(factors):
        return
    exp.factors = {f: [label for label, _, _ in levels] for f, levels in levels_of.items()}
    for combo in itertools.product(*levels_of.values()):
        changes, levels = {}, {}
        for factor, (label, change, lline) in zip(levels_of, combo):
            levels[factor] = label
            for key, value in change.items():
                if key in changes:
                    problems.error(exp.path, f"`{key}` is changed by two levels, "
                                   + " and ".join(f"`{l}`" for l in levels.values() if l), line=lline,
                                   value=str(key), fix="a thing may be changed by one factor only")
                changes[key] = value
        name = "+".join(label for label, _, _ in combo)
        exp.conditions.append(Condition(name, base, changes, levels, line=combo[0][2]))


def _task_path(exp: Experiment, written, doc: Doc) -> Path | None:
    line = doc.line("task")
    if not isinstance(written, str):
        exp.problems.error(exp.path, "`task:` should be a task file in `tasks/`", line=line)
        return None
    for candidate in (written, written + ".yaml", written + ".yml"):
        path = exp.folder / candidate
        if path.is_file():
            return path
    known = [p.relative_to(exp.folder).with_suffix("").as_posix()
             for p in sorted((exp.folder / "tasks").glob("*.y*ml"))] if (exp.folder / "tasks").is_dir() else []
    hint = V.suggest(written, known)
    exp.problems.error(exp.path, f"there is no task file `{written}`", line=line, value=written,
                       fix=f"did you mean `{hint}`?" if hint else "put the task in tasks/, as `inputs:` lines")
    return None


def _read_metrics(exp: Experiment, metrics, doc: Doc) -> None:
    if not isinstance(metrics, list):
        exp.problems.error(exp.path, "`metrics:` should list files in metrics/, one per line",
                           line=doc.line("metrics"))
        return
    for i, written in enumerate(metrics):
        line = doc.line("metrics", i)
        found = None
        if isinstance(written, str):
            for candidate in (written, written + ".yaml", written + ".yml", written + ".md"):
                if (exp.folder / candidate).is_file():
                    found = exp.folder / candidate
                    break
        if found is None and isinstance(written, str) and re.match(r"^\w[\w-]*\.output\.\w[\w-]*$", written):
            exp.outputs_measured.append((written, line))  # a member's typed output, by its path (D138 (13))
        elif found is None:
            exp.problems.error(exp.path, f"there is no metric file `{written}`", line=line, value=str(written),
                               fix="write it in metrics/ (an `answer_key` .yaml or a `kind: evaluator` .md), "
                               "or remove the line")
        else:
            exp.metrics.append(found)


def _read_limits(exp: Experiment, limits, doc: Doc) -> None:
    if not isinstance(limits, dict):
        exp.problems.error(exp.path, "`limits:` holds `max_concurrent:` and `max_cost_usd:`",
                           line=doc.line("limits"))
        return
    for key, value in limits.items():
        line = doc.line("limits", key)
        if key not in LIMITS:
            hint = V.suggest(str(key), LIMITS)
            exp.problems.error(exp.path, f"`{key}` is not a limit", line=line, value=str(key),
                               fix=f"did you mean `{hint}`?" if hint else "the limits are `max_concurrent` and `max_cost_usd`")
        elif key == "max_concurrent" and not (isinstance(value, int) and not isinstance(value, bool) and value >= 1):
            exp.problems.error(exp.path, "`max_concurrent:` should be a whole number, 1 or more", line=line)
        elif key == "max_cost_usd" and not (isinstance(value, (int, float)) and not isinstance(value, bool)
                                            and value > 0):
            exp.problems.error(exp.path, "`max_cost_usd:` should be an amount in dollars, such as 40.00", line=line)
        else:
            exp.limits[key] = value


# --- building conditions ------------------------------------------------------------

def build(exp: Experiment) -> Experiment:
    """Build every condition, validate each as a team, and hash each study.

    A condition with no changes runs from its folder as written; one with
    changes is built into conditions/<name>/, replacing what TeamRunner built
    there before and refusing to touch a folder it did not build.
    """
    if exp.doc is None:
        return exp
    task_inputs = None
    if exp.task is not None:
        try:
            task_inputs = read_task_file(exp.task)
        except InputError as e:
            exp.problems.error(exp.task, str(e))
    for cond in exp.conditions:
        if cond.changes:
            if not _build_condition(exp, cond):
                continue
        else:
            cond.folder = cond.base
        try:
            cond.report = validate(cond.folder, write_ids=False)  # never writes into a condition (D138 (2))
        except NotATeam as e:
            exp.problems.error(exp.path, str(e), line=cond.line)
            continue
        _sort_problems(exp, cond)
        if cond.report.ok:
            cond.inputs = _inputs(exp, cond, task_inputs)
        if cond.report.ok and cond.inputs is not None and exp.seed is not None:
            cond.study_hash = study_hash(find_top(cond.folder), cond.inputs, exp.seed)
    from .metrics import members_of, read_metrics
    members = members_of(exp.conditions) or None
    exp.measures = read_metrics(exp.metrics, exp.problems, members)
    from .metrics import read_outputs
    exp.measures += read_outputs(exp, members)
    return exp


def write_ids(exp: Experiment) -> Experiment:
    """Before an experiment runs (D64, as a single run does): give every file of
    each base team that lacks one an id, in the files themselves, and every
    whole-file variant one — the id of the member it replaces (a new version of
    the same member: same id, new content hash, D68), or a new one for a member
    it adds. Then build. The study's hash leaves ids out, so this resets nothing;
    every run's `resolved/` then carries the ids, and runs write none."""
    exp.writing_ids = True
    seen = set()
    for cond in exp.conditions:
        if cond.base is None or cond.base in seen or not (cond.base / TEAM_FILE).is_file():
            continue
        seen.add(cond.base)
        validate(cond.base, write_ids=True)
    return build(exp)


_ID = re.compile(r"^id:\s*([0-9A-Z]{26})\b", re.M)


def _id_of(path: Path) -> str | None:
    parts = split_frontmatter(path.read_text(encoding="utf-8")) if path.is_file() else None
    found = _ID.search(parts[0]) if parts else None
    return found.group(1) if found else None


def _give_id(source: Path, target: Path, exists: bool) -> None:
    """A whole-file variant of a member takes that member's id; a new member, a new id."""
    parts = split_frontmatter(source.read_text(encoding="utf-8"))
    if parts is None or _ID.search(parts[0]):
        return  # not a member file (a chair's flow), or it has its id already
    from .ids import write_member_id
    what = "toolkeeper" if re.search(r"^kind:\s*toolkeeper\b", parts[0], re.M) else "agent"
    write_member_id(source, what, _id_of(target) if exists else None)


def _sort_problems(exp: Experiment, cond: Condition) -> None:
    """A team's problems, with what only a later TeamRunner can do set apart.
    A problem in a built file that is the same as the base's is shown at the
    base's file, so a problem every condition shares is listed once."""
    top = find_top(cond.folder)
    base_top = find_top(cond.base)
    for p in cond.report.problems:
        file = p.file
        if cond.generated and file.is_relative_to(top):
            original = base_top / file.relative_to(top)
            if original.is_file() and file.is_file() and original.read_bytes() == file.read_bytes():
                file = original
        if p.level == "error" and f"TeamRunner {LATER}" in p.message:
            _later(exp, file, p.line, p.message)
        elif not any(q.file == file and q.line == p.line and q.message == p.message for q in exp.problems):
            exp.problems.append(replace(p, file=file))


def _later(exp: Experiment, file: Path, line, what: str) -> None:
    if not any(f == file and w == what for f, _, w in exp.later):
        exp.later.append((file, line, what))


def _inputs(exp: Experiment, cond: Condition, task_inputs: dict | None) -> dict | None:
    """Every input's value, as `run` will give it: the task's, else the defaults."""
    data = plain(cond.report.package.team.doc.data)
    defaults = data.get("default_inputs") or {}
    used = []
    for m in cond.report.package.team.members:
        if m.kind != "toolkeeper":
            for name in re.findall(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}", m.body):
                if name not in used:
                    used.append(name)
    values = {}
    for name, value in (task_inputs or {}).items():
        if name not in used:
            exp.problems.error(exp.task, f"the task gives `{name}`, but no prompt in `{cond.name}` uses it",
                               value=name, fix="its inputs are " + ", ".join(f"`{n}`" for n in used)
                               if used else "this team takes no inputs")
            return None
        values[name] = value
    for name in used:
        if name not in values:
            if name in defaults:
                values[name] = defaults[name]
            else:
                exp.problems.error(exp.task or exp.path, f"`{cond.name}` needs the input `{name}`, "
                                   "which neither the task nor the team gives",
                                   fix=f"add `{name}:` under `inputs:` in the task file")
                return None
    return values


def _build_condition(exp: Experiment, cond: Condition) -> bool:
    """Write conditions/<name>/: the base's package, every change applied."""
    top = find_top(cond.base)
    inside = cond.base.relative_to(top)
    out = exp.folder / CONDITIONS_DIR / cond.name
    if out.exists() and not (out / MARKER).is_file():
        exp.problems.error(out, f"conditions/{cond.name}/ was not built by TeamRunner, so it is left alone",
                           fix=f"move or rename that folder; TeamRunner builds `{cond.name}` there from "
                           "experiment.md")
        return False
    errors_before = len(exp.problems.errors)
    later_before = len(exp.later)
    building = exp.folder / CONDITIONS_DIR / f".{cond.name}.building"
    if building.exists():
        shutil.rmtree(building)
    copy_package(top, building)
    team_dir = building / inside
    # Whole files first, then fields, so a field change reads the file it lands in.
    ordered = sorted(cond.changes.items(), key=lambda kv: _is_field(kv[0]))
    for key, value in ordered:
        _apply(exp, cond, team_dir, str(key), value)
    if len(exp.problems.errors) > errors_before or len(exp.later) > later_before:
        shutil.rmtree(building)
        return False
    rel = Path(os.path.relpath(cond.base, exp.folder)).as_posix()
    (building / MARKER).write_text(MARKER_TEXT.format(name=cond.name, experiment=EXPERIMENT_FILE, base=rel),
                                   encoding="utf-8")
    if out.exists():
        shutil.rmtree(out)
    building.rename(out)
    cond.folder = out / inside
    cond.generated = True
    return True


def copy_package(top: Path, dest: Path) -> None:
    """Spec files are copied; large unchanged data files are hard-linked.
    runs/, the generated note and hidden files (a package's own .env among
    them) stay behind."""
    def ignore(folder, names):
        skip = {n for n in names if n.startswith(".") and n != ".gitignore"}
        if Path(folder) == top:
            skip |= {RUNS_DIR, MARKER}
        return skip

    def link_or_copy(src, dst):
        if not src.endswith(SPEC_SUFFIXES) and os.path.getsize(src) >= LARGE:
            try:
                os.link(src, dst)
                return dst
            except OSError:
                pass
        return shutil.copy2(src, dst)

    shutil.copytree(top, dest, ignore=ignore, copy_function=link_or_copy, symlinks=False)


def _is_field(key: str) -> bool:
    last = key.rsplit("/", 1)[-1]
    return "." in last


def _members(team_dir: Path) -> dict[str, Path]:
    found = {}
    for path in sorted(team_dir.glob("*.md")):
        if path.name in (CHAIR_FILE, MANAGER_FILE, MARKER):
            continue
        parts = split_frontmatter(path.read_text(encoding="utf-8"))
        if parts is not None:
            found[member_name(path)] = path
    return found


def _apply(exp: Experiment, cond: Condition, team_dir: Path, key: str, value) -> None:
    line = _change_line(exp, cond, key)
    problems = exp.problems
    folders, _, last = key.rpartition("/")
    where = team_dir / folders if folders else team_dir
    thing, dot, rest = last.partition(".")

    if folders == MODELS_DIR or folders.endswith("/" + MODELS_DIR):
        # A model profile (D93): a whole file, named with or without `.yaml`
        name = last[:-len(".yaml")] if last.endswith(".yaml") else last
        profiles = sorted(p.stem for p in where.glob("*.yaml")) if where.is_dir() else []
        if "." in name:
            problems.error(exp.path, f"`{key}`: a model profile is a whole file; change it with "
                           f"`{folders}/{name.split('.')[0]}: file:variants/…`", line=line, value=key)
            return
        target = where / f"{name}.yaml"
        if not target.is_file() and value != REMOVE and profiles:
            hint = V.suggest(name, profiles)
            if hint:
                problems.error(exp.path, f"`{key}`: there is no model profile `{name}` in {folders}/",
                               line=line, value=key, fix=f"did you mean `{folders}/{hint}`?")
                return
        if value != REMOVE:
            where.mkdir(parents=True, exist_ok=True)  # a team with no models/ yet
        _whole_file(exp, line, key, target, value, exists=target.is_file())
        return

    if thing == "whiteboard" or key.startswith("whiteboard"):
        _later(exp, exp.path, line, f"`{key}` changes the whiteboard (whiteboard.yaml), "
               f"which arrives in TeamRunner {LATER}")
        return
    if not where.is_dir():
        hint = V.suggest(folders, [p.relative_to(team_dir).as_posix() for p in team_dir.rglob("*")
                                   if p.is_dir() and (p / TEAM_FILE).is_file()])
        problems.error(exp.path, f"`{key}`: there is no folder `{folders}` in the team",
                       line=line, value=key, fix=f"did you mean `{hint}`?" if hint else None)
        return
    if folders and (where / TEAM_FILE).is_file():
        _later(exp, exp.path, line, f"`{key}` changes a subteam, `{folders}`; teams inside teams "
               f"arrive in TeamRunner {LATER}")
        # still applied, so validate can say the rest

    if thing in RESERVED_FILES:
        if dot:
            problems.error(exp.path, f"`{key}`: `{thing}` is a whole file; it has no fields to change",
                           line=line, value=key, fix=f"write `{thing}: file:variants/…`")
            return
        _whole_file(exp, line, key, where / RESERVED_FILES[thing], value, exists=None)
        return

    members = _members(where)
    if thing in members:
        path = members[thing]
    else:
        path = None
    if not dot:
        _whole_file(exp, line, key, path or where / f"{thing}.md", value, exists=path is not None)
        return
    if path is None:
        hint = V.suggest(thing, list(members) + list(RESERVED_FILES))
        problems.error(exp.path, f"`{key}`: there is no member `{thing}` in the team",
                       line=line, value=key,
                       fix=f"did you mean `{hint}`?" if hint else
                       "a field is changed on a member that exists; add a whole member with `name: file:variants/…`")
        return
    if rest == "prompt":
        _prompt(exp, line, key, path, value)
    else:
        _field(exp, line, key, path, rest.split("."), value)


def _change_line(exp: Experiment, cond: Condition, key: str) -> int | None:
    """The line where a change is written: under its condition or its level."""
    doc = exp.doc
    if "conditions" in doc.data:
        return doc.line("conditions", cond.name, "change", key) or cond.line
    for factor, label in cond.levels.items():
        levels = doc.data["factors"][factor]
        for i, level in enumerate(levels):
            if level.get("label", f"{factor}_{i + 1}") == label and key in (level.get("change") or {}):
                return doc.line("factors", factor, i, "change", key)
    return cond.line


def _variant(exp: Experiment, line, key: str, value) -> Path | None:
    if not isinstance(value, str) or not value.startswith(FILE):
        return None
    written = value[len(FILE):].strip()
    path = (exp.folder / written).resolve()
    if path.is_file() and path.is_relative_to(exp.folder):
        return path
    known = [p.relative_to(exp.folder).as_posix() for p in sorted((exp.folder / "variants").rglob("*"))
             if p.is_file()] if (exp.folder / "variants").is_dir() else []
    hint = V.suggest(written, known)
    exp.problems.error(exp.path, f"`{key}`: there is no file `{written}` in this experiment",
                       line=line, value=value,
                       fix=f"did you mean `file:{hint}`?" if hint else "put the file in variants/ and name it from there")
    return None


def _whole_file(exp: Experiment, line, key: str, target: Path, value, exists: bool | None) -> None:
    exists = target.is_file() if exists is None else exists
    if value == REMOVE:
        if not exists:
            members = _members(target.parent)
            hint = V.suggest(target.stem, list(members) + list(RESERVED_FILES))
            exp.problems.error(exp.path, f"`{key}: remove`: the team has no {target.name} to remove",
                               line=line, value=key, fix=f"did you mean `{hint}`?" if hint else None)
            return
        target.unlink()
        return
    source = _variant(exp, line, key, value)
    if source is None:
        if not (isinstance(value, str) and value.startswith(FILE)):
            exp.problems.error(exp.path, f"`{key}` is a whole file, so its change is `file:variants/…` or `remove`",
                               line=line, value=str(value),
                               fix=f"to change one setting, write `{key}.<setting>: …`")
        return
    if exp.writing_ids:
        _give_id(source, target, exists)
    shutil.copyfile(source, target)


def _prompt(exp: Experiment, line, key: str, path: Path, value) -> None:
    source = _variant(exp, line, key, value)
    if source is None:
        if not (isinstance(value, str) and value.startswith(FILE)):
            exp.problems.error(exp.path, f"`{key}` is a member's whole prompt, so its change is `file:variants/…`",
                               line=line, value=str(value),
                               fix="write the new prompt in a file in variants/ and name it here")
        return
    text = source.read_text(encoding="utf-8")
    if split_frontmatter(text) is not None:
        exp.problems.error(source, f"`{key}` takes a prompt, but this file starts with a settings block",
                           line=1, fix="keep only the prompt's text in this file, or change the whole member "
                           f"with `{key.rsplit('.', 1)[0]}: file:…`")
        return
    front, _, _ = split_frontmatter(path.read_text(encoding="utf-8"))
    path.write_text(f"---\n{front}---\n{text}", encoding="utf-8")


def _field(exp: Experiment, line, key: str, path: Path, names: list[str], value) -> None:
    field_name = names[0]
    if field_name not in V.MEMBER.known:
        choices = sorted(V.MEMBER.known | {"prompt"})
        hint = V.suggest(field_name, choices)
        exp.problems.error(exp.path, f"`{key}`: `{field_name}` is not a member's setting",
                           line=line, value=key,
                           fix=f"did you mean `{hint}`?" if hint else
                           "a member's settings are " + ", ".join(f"`{c}`" for c in choices))
        return
    if field_name in ("id", "parent_id"):
        exp.problems.error(exp.path, f"`{key}`: a member's `{field_name}` says who it is, not how it works; "
                           "it is not something a condition changes", line=line, value=key)
        return
    if value == REMOVE:
        exp.problems.error(exp.path, f"`{key}: remove`: `remove` takes out a whole file",
                           line=line, value=key, fix="to change a setting, give its new value")
        return
    source = _variant(exp, line, key, value)
    if isinstance(value, str) and value.startswith(FILE):
        if source is None:
            return
        value = source.read_text(encoding="utf-8")
    text = path.read_text(encoding="utf-8")
    front, body, _ = split_frontmatter(text)
    data = _yaml.load(front) or CommentedMap()
    node = data
    for name in names[:-1]:
        if not isinstance(node.get(name), dict):
            node[name] = CommentedMap()
        node = node[name]
    node[names[-1]] = value
    from io import StringIO
    stream = StringIO()
    _yaml.dump(data, stream)
    path.write_text(f"---\n{stream.getvalue()}---\n{body}", encoding="utf-8")


# --- the study's hash (D138 (9)) ------------------------------------------------------

def canonical_files(top: Path) -> dict[str, object]:
    """A package's files as they matter to a run: settings as data (comments and
    spacing left out), prompts as their text, flows without comments, any other
    file by its hash. Ids are left out: `validate` writes them in (D64), and a
    run of the base must not change every study built on it."""
    files = {}
    for path in sorted(top.rglob("*")):
        inside = path.relative_to(top).parts
        if inside[0] == RUNS_DIR or any(p.startswith(".") for p in inside) or not path.is_file():
            continue
        if len(inside) == 1 and inside[0] == MARKER:
            continue
        files["/".join(inside)] = _canonical(path)
    return {k: v for k, v in files.items() if v is not None}


def _without_ids(data):
    if isinstance(data, dict):
        return {k: _without_ids(v) for k, v in data.items() if k not in ("id", "parent_id")}
    return data


def _canonical(path: Path):
    if path.suffix in (".yaml", ".yml"):
        try:
            return {"settings": _without_ids(plain(_yaml.load(path.read_text(encoding="utf-8"))))}
        except Exception:
            pass
    elif path.suffix == ".md":
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        if path.name in (CHAIR_FILE, MANAGER_FILE):
            parts = split_frontmatter(text)
            body = parts[1] if parts and parts[1] is not None else text
            settings = _without_ids(plain(_yaml.load(parts[0]))) if parts else None
            # Flow words are joined by `->`, `=` and `--…-->`, so spacing never matters.
            lines = [re.sub(r"\s+", "", ln.split("#", 1)[0]) for ln in body.splitlines()]
            return {"settings": settings, "flow": [ln for ln in lines if ln]}
        parts = split_frontmatter(text)
        if parts is None:
            return None  # a note, not part of the team (D91)
        try:
            settings = _without_ids(plain(_yaml.load(parts[0])))
        except Exception:
            settings = parts[0]
        return {"settings": settings, "prompt": (parts[1] or "").strip()}
    return {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def study_hash(top: Path, inputs: dict, seed: int) -> str:
    """Per condition: the resolved team, the task inputs and the seed (D138 (9))."""
    blob = json.dumps({"team": canonical_files(top), "inputs": inputs, "seed": seed},
                      sort_keys=True, ensure_ascii=False, default=str)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def experiment_hash(exp: Experiment) -> str | None:
    """The whole study: every condition's study hash. A run counts toward the study
    it was made in; when this changes, the command stops until --restart or
    --keep-unchanged says what to do (D138 (9), amended)."""
    hashes = {c.name: c.study_hash for c in exp.conditions}
    if any(h is None for h in hashes.values()):
        return None
    blob = json.dumps(hashes, sort_keys=True)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


# --- seeds and rounds (D14a, D138 (5)) --------------------------------------------------
# Both are derived with SHA-256 over plain text, never Python's `random`, so
# they stay the same across Python versions: changing them would change every
# study's plan.

def _draw(*parts) -> int:
    text = "\0".join(str(p) for p in parts)
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")


def run_seed(master: int, condition: str, replicate: int) -> int:
    """A run's seed, from (master, condition, replicate): a redone slot gets the same one."""
    return _draw("teamrunner/run-seed/v1", master, condition, replicate) % (2 ** 32)


def round_order(master: int, conditions: list[str], round_number: int) -> list[str]:
    """One round: every condition once, in an order drawn from the master seed
    (a Fisher–Yates shuffle whose every draw is a hash)."""
    order = list(conditions)
    for i in range(len(order) - 1, 0, -1):
        j = _draw("teamrunner/round/v1", master, round_number, i) % (i + 1)
        order[i], order[j] = order[j], order[i]
    return order


@dataclass(frozen=True)
class Slot:
    round: int
    position: int  # 1-based, within the round
    condition: str
    replicate: int
    seed: int


def plan_runs(exp: Experiment) -> list[Slot]:
    """Every run in the order it starts: round by round. Replicate r is in round r."""
    names = [c.name for c in exp.conditions]
    slots = []
    for r in range(1, (exp.replications or 0) + 1):
        for pos, name in enumerate(round_order(exp.seed, names, r), 1):
            slots.append(Slot(r, pos, name, r, run_seed(exp.seed, name, r)))
    return slots


# --- differences between conditions ---------------------------------------------------

@dataclass
class Difference:
    file: str  # path from the package top
    kind: str  # added | removed | changed
    detail: str = ""


def differences(a: Condition, b: Condition) -> list[Difference]:
    """What differs between two conditions' teams, file by file."""
    fa, fb = canonical_files(find_top(a.folder)), canonical_files(find_top(b.folder))
    out = []
    for name in sorted(set(fa) | set(fb)):
        if name not in fb:
            out.append(Difference(name, "removed"))
        elif name not in fa:
            out.append(Difference(name, "added", _detail(b, name, None, fb[name])))
        elif fa[name] != fb[name]:
            out.append(Difference(name, "changed", _detail(b, name, fa[name], fb[name])))
    return out


def _sources(cond: Condition, name: str) -> tuple[str | None, str | None]:
    """The variant a file came from, whole (`chair: file:…`) or as its prompt."""
    path = Path(name)
    whole = prompt = None
    for key, value in cond.changes.items():
        if not (isinstance(value, str) and value.startswith(FILE)):
            continue
        folders, _, last = str(key).rpartition("/")
        thing, _, rest = last.partition(".")
        if folders and not path.parent.as_posix().endswith(folders):
            continue
        same = (RESERVED_FILES.get(thing) == path.name) or (
            thing not in RESERVED_FILES and member_name(path) == thing and path.suffix == ".md")
        if not same:
            continue
        if not rest:
            whole = value[len(FILE):].strip()
        elif rest == "prompt":
            prompt = value[len(FILE):].strip()
    return whole, prompt


def _detail(cond: Condition, name: str, a, b) -> str:
    whole, prompt = _sources(cond, name)
    what = _what_changed(a, b) if a is not None else ""
    if prompt:
        what = what.replace("prompt changed", f"prompt from {prompt}")
    if whole:
        if a is None:
            return f"from {whole}"
        return f"replaced by {whole}" + (f" ({what})" if what else "")
    return what


def _what_changed(a, b) -> str:
    if not (isinstance(a, dict) and isinstance(b, dict)):
        return ""
    parts = []
    if "flow" in a and a.get("flow") != b.get("flow"):
        parts.append("flow changed")
    if a.get("prompt") != b.get("prompt") and "prompt" in a:
        parts.append("prompt changed")
    sa, sb = a.get("settings"), b.get("settings")
    if isinstance(sa, dict) and isinstance(sb, dict):
        for key in sorted(set(sa) | set(sb)):
            if key not in sb:
                parts.append(f"`{key}` removed")
            elif key not in sa:
                parts.append(f"`{key}` added: {_short(sb[key])}")
            elif sa[key] != sb[key]:
                parts.append(f"`{key}`: {_short(sa[key])} → {_short(sb[key])}")
    elif sa != sb:
        parts.append("settings changed")
    return ", ".join(parts)


def _short(value) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = " ".join(text.split())
    return text if len(text) <= 40 else text[:39] + "…"
