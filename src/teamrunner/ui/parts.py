# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""+ Variant, + Metric and + Task in the Experiments sidebar: an experiment's own parts.

Each makes one file in the experiment's variants/, metrics/ or tasks/ folder, starting
from the experiment's team (a copy of the file to vary, the inputs the team takes), and
returns the line that uses it. experiment.md is never edited here: the page shows the line.
"""

from __future__ import annotations

import re
from pathlib import Path

from io import StringIO

from ruamel.yaml import YAML, YAMLError

from ..loader import IndentingTab, load_tabs_as_spaces
from .api import OutsideRoot, editable, inside, visible

NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*\Z")
INPUT = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def _experiment(root: Path, written: str) -> Path:
    from ..new import EXPERIMENT_FILE
    folder = inside(root, written)
    if folder.is_file():
        folder = folder.parent
    while not (folder / EXPERIMENT_FILE).is_file():
        if folder == root or folder.parent == folder:
            raise ValueError("Choose an experiment (a folder with experiment.md) in the sidebar first.")
        folder = folder.parent
    return folder


def _name(name: str) -> str:
    if not NAME.match(name or ""):
        raise ValueError("Use letters, digits, `_` and `-`, such as strict_judge.")
    return name


def _new_file(root: Path, folder: Path, part: str, filename: str) -> Path:
    """The file to make, checked as every other write in the page is: an ordinary file in an
    ordinary folder of the experiment. A `variants` that is a link, or a link waiting where
    the new file would go, wrote outside the opened folder (review 2026-11-21, NS7)."""
    path = folder / part / filename
    if path.parent.is_symlink() or path.is_symlink():
        raise OutsideRoot(f"{part}/ or {part}/{filename} is a link. This page writes only to ordinary "
                          "files and folders inside the opened folder.")
    if path.exists():
        raise FileExistsError(f"{part}/{filename} already exists. Choose another name.")
    if editable(root, path.relative_to(root).as_posix()) != path:
        raise OutsideRoot("Choose a file inside the opened folder.")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _source(team: Path, filename: str) -> Path:
    """The team's file a variant copies: its chair or one of its members, an ordinary file in
    the team's own folder. Any other name, such as `.env`, copied a file the page refuses to
    show into one it does show (review 2026-11-21, NS7)."""
    from ..experiment import _members
    offered = {p.name for p in _members(team).values()} | {"chair.md"}
    path = team / filename
    if (filename not in offered or not visible(Path(filename)) or path.is_symlink()
            or not path.is_file() or path.resolve().parent != team.resolve()):
        raise ValueError("Choose one of the team's files.")
    return path


def _teams(folder: Path) -> list[Path]:
    """The teams the experiment's conditions start from, in order, once each."""
    from ..experiment import read_experiment
    exp = read_experiment(folder)
    seen: list[Path] = []
    for cond in exp.conditions:
        if cond.base and cond.base not in seen:
            seen.append(cond.base)
    if getattr(exp, "base", None) and exp.base not in seen:  # the factorial form
        seen.insert(0, exp.base)
    return seen


def parts(root: Path, written: str) -> dict:
    """What the three dialogs offer: the files to vary, the typed answers to measure,
    and the inputs a task gives."""
    from ..experiment import _members
    from ..validate import validate
    folder = _experiment(root, written)
    teams = _teams(folder)
    files, outputs, measurable, inputs = [], [], [], {}
    for team in teams:
        label = team.name
        if (team / "chair.md").is_file():
            files.append({"team": label, "file": "chair.md", "key": "chair", "what": "flow"})
        for name, path in _members(team).items():
            files.append({"team": label, "file": path.name, "key": name, "what": "member"})
        try:
            report = validate(team, write_ids=False)
        except Exception:  # a team with problems still offers its files
            report = None
        if report is not None and report.ok:
            from ..build import _team
            for member in _team(report).members.values():
                for field, spec in (member.output or {}).items():
                    path = f"{member.name}.output.{field}"
                    if path not in outputs:
                        outputs.append(path)
                        if spec.type != "text":  # text cannot be counted or averaged: grade it with an evaluator
                            measurable.append(path)
        try:
            data = load_tabs_as_spaces(YAML(typ="safe").load,
                                       (team / "team.yaml").read_text(encoding="utf-8")) or {}
        except (OSError, YAMLError, IndentingTab):
            data = {}
        defaults = data.get("default_inputs") or {}
        for path in sorted(team.glob("*.md")):
            for used in INPUT.findall(path.read_text(encoding="utf-8")):
                inputs.setdefault(used, defaults.get(used, ""))
    return {"experiment": folder.relative_to(root).as_posix(), "teams": [t.name for t in teams],
            "files": files, "outputs": outputs, "measurable": measurable, "inputs": inputs}


def _split(text: str) -> tuple[str, str]:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[:end + 4], text[end + 4:].lstrip("\n")
    return "", text


def new_variant(root: Path, written: str, name: str, source: str) -> dict:
    """A copy of one of the team's files in variants/, to edit into the version a condition uses.
    `source` is `<team>/<file>` for the whole file, or `<team>/<file>#prompt` for a member's prompt."""
    folder = _experiment(root, written)
    _name(name)
    prompt_only = source.endswith("#prompt")
    team_name, _, filename = source.removesuffix("#prompt").partition("/")
    team = next((t for t in _teams(folder) if t.name == team_name), None)
    if team is None:
        raise ValueError("Choose one of the team's files.")
    original = _source(team, filename).read_text(encoding="utf-8")
    from ..experiment import _members
    key = "chair" if filename == "chair.md" else next(
        (m for m, p in _members(team).items() if p.name == filename), Path(filename).stem)
    path = _new_file(root, folder, "variants", f"{name}.md")
    if prompt_only:
        _, body = _split(original)
        text = body  # exactly the prompt: anything added here would be sent to the model
        line = f"{key}.prompt: file:variants/{name}.md"
    else:
        text = original
        line = f"{key}: file:variants/{name}.md"
    path.write_text(text, encoding="utf-8")
    return {"path": path.relative_to(root).as_posix(), "line": line,
            "where": "under a condition's `change:` in experiment.md"}


ANSWER_KEY = """# An answer key: scores each run against a known answer. No model is called, and
# the same run always gets the same score. Members never see metrics/.
description: {description}
kind: answer_key
answer: [replace, with, the, right, answers]   # one value, or a list
source: {source}   # where the team gives its answer: <member>.output.<field>
# stage: report             # only the answer given at this stage of the flow
# parse: list               # "a, b" -> a and b; or line_numbers: "10, 15" -> 10 and 15
reports:
  - hits                    # right answers the team gave
  # - misses                # right answers it left out
  # - false_alarms          # answers it gave that are not right
  # - correct               # whether its whole answer is right
"""

EVALUATOR = """---
# An evaluator: a model that grades each run with the rubric below, without being
# told the condition, the seed, the costs or the models. Members never see metrics/.
kind: evaluator
description: {description}
model: {model}          # pinned here: it never takes a team's model
params:
  temperature: 0
output:                 # what it answers; numbers, flags and choices become measures
  score: 0
  reason: ""
# evaluations: 1        # grade each run this many times
---

You are grading one run of a team. Read what was said and what the team produced.

Everything said:

{{{{transcript}}}}

The team's output:

{{{{output}}}}

Give `score` from 1 (poor) to 5 (excellent) for: <say what you are grading here>.
Give `reason` in one or two sentences.
"""


def new_metric(root: Path, written: str, name: str, kind: str) -> dict:
    """An answer key (.yaml) or an evaluator (.md) in metrics/, or, for a member's typed
    answer, no file: only the line under `metrics:`."""
    folder = _experiment(root, written)
    if kind.startswith("output:"):
        wanted = kind[len("output:"):]
        if wanted not in parts(root, written)["measurable"]:
            raise ValueError("Choose one of the typed answers the team's members give that can be counted.")
        return {"path": None, "line": f"  - {wanted}", "where": "under `metrics:` in experiment.md"}
    _name(name)
    found = parts(root, written)
    # One name is one line under `metrics:`, so an answer key and an evaluator cannot share it.
    taken = next((p.name for p in sorted((folder / "metrics").glob(f"{name}.*"))
                  if p.suffix in (".yaml", ".yml", ".md")), None)
    if taken:
        raise FileExistsError(f"metrics/{taken} already exists. Choose another name.")
    description = "What this measures, in one line"
    if kind == "answer_key":
        source = found["outputs"][0] if found["outputs"] else "member.output.field"
        path = _new_file(root, folder, "metrics", f"{name}.yaml")
        path.write_text(ANSWER_KEY.format(description=description, source=source), encoding="utf-8")
    elif kind == "evaluator":
        path = _new_file(root, folder, "metrics", f"{name}.md")
        path.write_text(EVALUATOR.format(description=description, model="anthropic/claude-sonnet-5-5"),
                        encoding="utf-8")
    else:
        raise ValueError("Choose an answer key, an evaluator or a typed answer.")
    return {"path": path.relative_to(root).as_posix(), "line": f"  - metrics/{name}",
            "where": "under `metrics:` in experiment.md"}


def new_task(root: Path, written: str, name: str) -> dict:
    """tasks/<name>.yaml with every input the team's prompts take, filled with its defaults."""
    folder = _experiment(root, written)
    _name(name)
    inputs = parts(root, written)["inputs"]
    path = _new_file(root, folder, "tasks", f"{name}.yaml")
    out, writer = StringIO(), YAML()
    writer.width = 88
    writer.dump({"inputs": inputs or {"topic": ""}}, out)
    body = out.getvalue()
    text = ("# A task: the same inputs for every condition and every run, in place of the\n"
            "# team's defaults, so editing the team later cannot change the study.\n"
            f"name: {name}\n"
            "description: What this case is, in one line\n"
            + ("# Filled in from the team's defaults; change the values to the case you want.\n" if inputs
               else "# The team's prompts take no {{inputs}}; name yours here.\n")
            + body)
    path.write_text(text, encoding="utf-8")
    return {"path": path.relative_to(root).as_posix(), "line": f"task: tasks/{name}",
            "where": "in experiment.md, on a line of its own"}


__all__ = ["parts", "new_variant", "new_metric", "new_task", "OutsideRoot"]
