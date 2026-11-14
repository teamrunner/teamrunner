# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""The Experiments tab (D141 (4)): the experiments in the opened folder, and one
experiment's plan, its runs by condition and its summary.

Read-only and free: the same figures `teamrunner experiment --plan` and
`--summary` print, from the same functions. Like `--plan`, opening one builds
its generated `conditions/` folders; nothing else is written and nothing is called.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..experiment import (EXPERIMENT_FILE, NotAnExperiment, build, differences, plan_runs,
                          read_experiment)
from ..experiment_run import Study, _paid, experiment_records, fills, left_out
from ..plan import ceiling_of, ceiling_words, costs, litellm_max_lines
from ..prices import load_prices
from ..problems import ERROR
from ..runs import read_events
from ..summary import COSTLY, costly, table
from .api import inside, visible
from .runviews import safe_folder

SKIP = {"runs", "conditions", "resolved", "node_modules", "__pycache__"}
DEPTH = 4  # the workspace's experiments/<name>/ is two down; leave room for grouping folders


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return Path(os.path.relpath(path, root)).as_posix()


def _found(root: Path):
    """Every folder under the opened one that holds an experiment.md."""
    stack = [(root, 0)]
    while stack:
        folder, depth = stack.pop()
        if (folder / EXPERIMENT_FILE).is_file():
            yield folder
            continue  # an experiment's own folders (variants/, tasks/, metrics/) hold no experiments
        if depth >= DEPTH:
            continue
        try:
            children = sorted((p for p in folder.iterdir() if p.is_dir() and not p.is_symlink()), reverse=True)
        except OSError:
            continue
        stack += [(p, depth + 1) for p in children if visible(Path(p.name)) and p.name not in SKIP]


def listing(root: Path) -> dict:
    found = []
    for folder in _found(root):
        item = {"path": _rel(folder, root), "name": folder.name, "conditions": None, "runs": None,
                "real": 0, "rehearsals": 0, "problem": None}
        try:
            exp = read_experiment(folder)
            item["name"] = exp.name or folder.name
            item["conditions"] = len(exp.conditions) or None
            item["runs"] = exp.runs or None
            if exp.problems.errors:
                item["problem"] = "experiment.md has problems to fix"
            records = experiment_records(exp)
            item["real"] = sum(1 for _, r in records if not r.get("rehearsal") and fills(r))
            item["rehearsals"] = sum(1 for _, r in records if r.get("rehearsal"))
        except (NotAnExperiment, OSError, ValueError) as error:
            item["problem"] = str(error)
        found.append(item)
    return {"experiments": sorted(found, key=lambda e: e["path"])}


def _problem(p, root: Path) -> dict:
    return {"where": p.where(root) if p.file.is_relative_to(root) else _rel(p.file, root)
            + (f":{p.line}" if p.line else ""), "message": p.message, "fix": p.fix,
            "level": "error" if p.level == ERROR else "warning"}


def check(root: Path, folder: Path) -> dict:
    """Validate, in an experiment's files: the checks `--plan` makes before it prints
    anything (the settings, every condition's team, every change and metric), in the shape
    the Files tab shows a team's check. Nothing is called and no key is needed."""
    exp = read_experiment(folder)
    build(exp)
    problems = [{"level": "error", "file": _rel(f, root), "line": line,
                 "message": f"needs a later TeamRunner: {what}", "value": None, "fix": None}
                for f, line, what in exp.later]
    problems += [{"level": "error" if p.level == ERROR else "warning", "file": _rel(p.file, root),
                  "line": p.line, "message": p.message, "value": p.value, "fix": p.fix}
                 for p in exp.problems]
    return {"ok": exp.ok, "team": "", "experiment": _rel(folder, root), "name": exp.name or folder.name,
            "runs": exp.runs if exp.ok else None, "problems": problems, "ids_added": [], "roles": {}}


def _run(root: Path, folder: Path, record: dict, warnings: list[str]) -> dict | None:
    if not safe_folder(root, folder, warnings):
        return None
    block = record["experiment"]
    return {"path": folder.relative_to(root).as_posix(), "status": record.get("status") or "unknown",
            "stopped_by": record.get("stopped_by"), "fills": fills(record),
            "replicate": block.get("replicate"), "round": block.get("round"),
            "cost_usd": record.get("cost_usd") if record.get("cost_usd") is not None
            else record.get("cost_known_usd"),
            "duration_s": (record.get("latency_ms") or 0) / 1000}


def detail(root: Path, written: str) -> dict:
    folder = inside(root, written)
    if not folder.is_dir():
        folder = folder.parent
    if not (folder / EXPERIMENT_FILE).is_file():
        raise ValueError("Choose a folder that holds an experiment.md.")
    exp = read_experiment(folder)
    build(exp)  # as --plan does: every problem is found at once
    here = exp.folder
    shown = {"path": _rel(here, root), "file": _rel(exp.path, root), "name": exp.name or here.name,
             "question": exp.body, "problems": [_problem(p, root) for p in exp.problems],
             "later": [{"where": _rel(f, root) + (f":{line}" if line else ""), "what": what}
                       for f, line, what in exp.later]}
    if not exp.ok:
        shown["ready"] = False
        return shown
    limit, limit_from = ceiling_of(exp, None)
    shown.update({
        "ready": True, "seed": exp.seed, "replications": exp.replications, "runs": exp.runs,
        "factors": exp.factors or {}, "max_concurrent": exp.limits.get("max_concurrent"),
        "ceiling": {"max_cost_usd": limit, "from": limit_from, "line": _limit_line(exp, root),
                    "words": ceiling_words(limit, limit_from) if limit is not None else None},
        "conditions": [{"name": c.name, "folder": _rel(c.folder, root), "base": _rel(c.base, root),
                        "generated": c.generated, "changes": len(c.changes), "levels": c.levels,
                        "study_hash": c.study_hash} for c in exp.conditions],
        "differences": [{"from": exp.conditions[0].name, "to": c.name,
                         "files": [{"file": d.file, "kind": d.kind, "detail": d.detail}
                                   for d in differences(exp.conditions[0], c)]}
                        for c in exp.conditions[1:]],
        "order": [{"round": s.round, "position": s.position, "condition": s.condition,
                   "replicate": s.replicate} for s in plan_runs(exp)],
    })
    prices = load_prices()
    c = costs(exp, prices)
    shown["estimate"] = {
        "total_usd": c["estimate"], "most_usd": c["most"], "evaluating_usd": c["evaluating"],
        "evaluations": c["evaluations"], "evaluators": [ev.name for ev in c["evaluators"]],
        "per_condition": [{"condition": name, "per_run_usd": e.per_run_usd, "calls": e.calls,
                           "tokens_in": e.tokens_in, "tokens_out": e.tokens_out}
                          for name, e in c["typical"].items()],
        "unpriced": c["unpriced"], "prices": str(prices.shown), "no_max_tokens": litellm_max_lines(exp)}

    warnings: list[str] = []
    records = experiment_records(exp)
    study = Study(exp, records, None)
    filled, redo = ({}, {}) if study.changed else study.slots()
    # The latest rehearsal of each slot, of the conditions as they now stand.
    rehearsed = {}
    hashes = {c.name: c.study_hash for c in exp.conditions}
    for f, r in records:
        block = r["experiment"]
        if r.get("rehearsal") and hashes.get(block.get("condition")) == block.get("study_hash"):
            rehearsed[(block["condition"], block.get("replicate"))] = (f, r)
    slots = []
    for s in plan_runs(exp):
        key = (s.condition, s.replicate)
        real = filled.get(key) or redo.get(key)
        trial = rehearsed.get(key)
        slots.append({"condition": s.condition, "replicate": s.replicate, "round": s.round,
                      "run": _run(root, *real, warnings) if real else None,
                      "rehearsal": _run(root, *trial, warnings) if trial else None})
    shown["slots"] = slots
    shown["rehearsals"] = sum(1 for _, r in records if r.get("rehearsal"))
    shown["changed"] = None
    if study.changed:
        changed, same = study.what_changed()
        shown["changed"] = {"changed": changed, "same": same}

    runs = sorted(filled.values(), key=lambda fr: (fr[1]["experiment"].get("round") or 0, fr[0].name))
    from ..evaluate import spent_on
    from ..metrics import Evaluator
    evaluators = [m for m in exp.measures if isinstance(m, Evaluator)]
    from ..evaluate import pending
    to_evaluate = len(pending(runs, evaluators, True)[0]) if evaluators and runs else 0
    shown["summary"] = {
        "to_evaluate": to_evaluate,
        "completed": len(runs), "spent_runs_usd": sum(_paid(r) for _, r in runs),
        "spent_evaluating_usd": spent_on(runs, evaluators),
        "measures": table(exp, runs, exp.measures, lambda f: list(read_events(f))) if runs else [],
        "not_counted": dict(sorted(left_out(exp, study, filled).items())) if not study.changed else {},
        "costly": [{"path": _rel(f["folder"], root), "condition": f["condition"], "replicate": f["replicate"],
                    "cost_usd": f["cost_usd"], "typical_usd": f["typical_usd"]} for f in costly(runs)],
        "costly_times": COSTLY}
    flagged = {(f["condition"], f["replicate"]): f["typical_usd"] for f in shown["summary"]["costly"]}
    for slot in slots:
        if slot["run"]:
            slot["run"]["typical_usd"] = flagged.get((slot["condition"], slot["replicate"])) \
                if slot["run"]["fills"] else None
    shown["rehearsal_summary"] = None
    if not runs and rehearsed:  # placeholder values, as a rehearsal prints them: they check the wiring
        trial = sorted(rehearsed.values(), key=lambda fr: (fr[1]["experiment"].get("round") or 0, fr[0].name))
        shown["rehearsal_summary"] = {"completed": len(trial), "measures": table(
            exp, trial, exp.measures, lambda f: list(read_events(f)))}
    shown["warnings"] = list(dict.fromkeys(warnings))
    shown["commands"] = {"plan": f"teamrunner experiment {_quote(shown['path'])} --plan",
                         "rehearse": f"teamrunner experiment {_quote(shown['path'])} --rehearse",
                         "run": f"teamrunner experiment {_quote(shown['path'])}",
                         "summary": f"teamrunner experiment {_quote(shown['path'])} --summary"}
    return shown


def _quote(path: str) -> str:
    return path if path and all(ch.isalnum() or ch in "/._-" for ch in path) else f'"{path or "."}"'


# --- a real run's confirmation (D151) ---------------------------------------------------------

CHANGES = {None: [], "restart": ["--restart"], "keep_unchanged": ["--keep-unchanged"]}
EVALUATING = {"both": [], "only": ["--evaluate"]}


class Refused(ValueError):
    """Something would stop the run, or there is a choice to make first: the quote says what."""

    def __init__(self, quote: dict):
        first = (quote["blockers"] or [{"message": "the study has changed: choose what to do first"}])[0]["message"]
        super().__init__(f"Nothing was started: {first}.")
        self.quote = quote


class Stale(ValueError):
    """The plan changed since the confirmation was shown: show the new one."""

    def __init__(self, quote: dict):
        super().__init__("The experiment or its runs changed since this was shown. Check the new figures.")
        self.quote = quote


def quote(root: Path, written: str, change: str | None = None, evaluating: str = "both",
          running: dict | None = None, listers=None) -> dict:
    """What a real run (or `--evaluate`) would do and cost, what would stop it, and a
    fingerprint of everything it depends on: the page's confirmation holds only for it."""
    import hashlib
    import io
    import json
    from ..console import Console
    from ..experiment_run import Runner
    if change not in CHANGES or evaluating not in EVALUATING:
        raise ValueError("Choose what to do: run, start over, keep unchanged runs, or evaluate.")
    folder = inside(root, written)
    if not (folder / EXPERIMENT_FILE).is_file():
        raise ValueError("Choose a folder that holds an experiment.md.")
    exp = read_experiment(folder)
    build(exp)  # as --plan does: every condition's team, checked
    if not exp.ok:
        raise ValueError("experiment.md has problems to fix first; the Plan shows them.")
    runner = Runner(exp, Console(False, io.StringIO()), rehearse=False, max_cost=None, prices=load_prices(),
                    change=change, evaluating=evaluating)
    q = runner.quote()
    path = _rel(folder, root)
    q.update({"path": path, "change": change, "evaluating": evaluating,
              "ceiling": None if q["limit_usd"] is None else ceiling_words(q["limit_usd"], q["limit_from"]),
              "command": " ".join(["teamrunner", "experiment", _quote(path)] + CHANGES[change] + EVALUATING[evaluating]),
              "line": _limit_line(exp, root), "checks": [], "shared": []})
    if q["changed"] is None and (q["todo"] or q["evaluations"]):
        q["checks"] = _model_checks(exp, runner, evaluating, listers)
        q["blockers"] += [{"kind": "check", "message": f"{c['what']}: {c['wrong']}", "fix": c["fix"]}
                          for c in q["checks"]]
    models = {m for _, m, _ in runner._uses(evaluators_only=evaluating == "only")}
    for other, used in (running or {}).items():
        if other != path:
            q["shared"] += [{"model": m, "experiment": other} for m in sorted(models & used)]
    q["models"] = sorted(models)
    records = [(f.name, r.get("status"), (r.get("experiment") or {}).get("replaced_by"))
               for f, r in experiment_records(exp) if not r.get("rehearsal")]
    evaluations = sorted((p.relative_to(exp.folder).as_posix(), p.stat().st_size)
                         for p in (exp.folder / "runs").glob("*/evaluations/*.jsonl"))
    seen = {"md": exp.path.read_text(encoding="utf-8"), "hashes": [c.study_hash for c in exp.conditions],
            "evaluators": [ev.path.read_text(encoding="utf-8") for ev in runner.evaluators],
            "records": records, "evaluations": evaluations,
            "quote": {k: q[k] for k in ("todo", "redo", "done", "evaluations", "limit_usd", "change", "evaluating")},
            "estimate": None if q["estimate_usd"] is None else round(q["estimate_usd"], 6)}
    q["fingerprint"] = hashlib.sha256(json.dumps(seen, sort_keys=True, default=str).encode()).hexdigest()[:32]
    q["can_run"] = not q["blockers"] and q["changed"] is None and bool(q["todo"] or q["evaluations"])
    return q


def _limit_line(exp, root: Path) -> str:
    """Where `max_cost_usd` is, or where to add it: experiment.md at its line."""
    lines = exp.path.read_text(encoding="utf-8").splitlines()
    for n, text in enumerate(lines, 1):
        if text.strip().startswith("max_cost_usd:"):
            return f"{_rel(exp.path, root)}:{n}"
    for n, text in enumerate(lines, 1):
        if text.strip().startswith("limits:"):
            return f"{_rel(exp.path, root)}:{n}"
    return f"{_rel(exp.path, root)}:1"


def _model_checks(exp, runner, evaluating: str, listers) -> list[dict]:
    """The check `run` makes before its first turn (D99, D100), for every condition's team,
    and the evaluators' keys: missing keys and models that cannot be reached, in its words."""
    from ..checks import LISTERS, online_check
    from ..keys import PROVIDER_KEYS, find_keys
    from .api import remembering
    found, seen = [], set()
    if evaluating != "only":
        for cond in exp.conditions:
            check, _ = online_check(cond.report, listers=listers or remembering(LISTERS, False))
            for p in check.problems:
                key = (p.what, p.wrong)
                if key not in seen:
                    seen.add(key)
                    found.append({"what": p.what, "who": p.who, "wrong": p.wrong, "fix": p.fix})
    names = sorted({PROVIDER_KEYS[e.model.split("/", 1)[0]] for e in runner.evaluators
                    if e.model.split("/", 1)[0] in PROVIDER_KEYS})
    keys = find_keys(names, None, None)
    for name in names:
        if not keys.values.get(name) and (name, "missing") not in seen:
            found.append({"what": name, "who": [e.name for e in runner.evaluators], "wrong": "no key was found",
                          "fix": "add it with Set up keys, or to ~/.teamrunner/.env"})
    return found
