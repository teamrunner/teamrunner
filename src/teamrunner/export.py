# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""`teamrunner experiment <folder> --export <dir>` (D12, D138 (12), SPEC §5.5).

Five long-format CSV tables, joined by `run_id`, rebuilt from the run folders
every time — nothing here is stored anywhere else:

    runs.csv          one row per run
    turns.csv         one row per turn, with everything said in `content`
    outputs.csv       one row per typed output field a member gave
    metrics.csv       one row per metric × report (or evaluator field) × run
    evaluations.csv   one row per evaluation × field, the evaluator's reason included

Written with Python's own `csv` module: RFC 4180 quoting, UTF-8, a header
row, an empty cell for a missing value, `true`/`false` for yes and no. Every
real run of the experiment is exported, with `counts` saying whether it
counts in the study as it now stands; `--completed` keeps only those that
do. Rehearsal runs are never exported: they are never results.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

from .evaluate import current, lines_of
from .metrics import AnswerKey, Evaluator, OutputMeasure
from .runs import read_events

TABLES = ("runs", "turns", "outputs", "metrics", "evaluations")


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(round(value, 10))
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return value


class Table:
    def __init__(self, path: Path, columns: list[str]):
        self.stream = path.open("w", encoding="utf-8", newline="")
        self.writer = csv.writer(self.stream)  # RFC 4180: quotes when needed, CRLF between rows
        self.columns = columns
        self.writer.writerow(columns)
        self.rows = 0

    def add(self, **row) -> None:
        self.writer.writerow([_cell(row.get(c)) for c in self.columns])
        self.rows += 1

    def close(self) -> None:
        self.stream.close()


def export(exp, runs: list[tuple[Path, dict]], counting: set[str], dest: Path) -> dict[str, int]:
    """Write the five tables for `runs` into `dest`. `counting`: the run_ids that count.
    Returns each table's number of rows."""
    dest.mkdir(parents=True, exist_ok=True)
    factors = list(exp.factors or {})
    tables = {
        "runs": Table(dest / "runs.csv", [
            "run_id", "condition", *factors, "replicate", "round", "seed", "status", "stopped_by", "counts",
            "replaced_by", "continued_from", "study_hash", "started_at", "duration_s", "turns", "cost_usd",
            "cost_reused_usd", "cost_charged_usd", "tokens_in", "tokens_out", "folder"]),
        "turns": Table(dest / "turns.csv", [
            "run_id", "turn", "member", "stage", "component_id", "version_hash", "model_asked", "model_answered",
            "tokens_in", "tokens_out", "cost_usd", "cost_charged_usd", "duration_s", "reused", "content"]),
        "outputs": Table(dest / "outputs.csv", ["run_id", "turn", "member", "stage", "field", "value"]),
        "metrics": Table(dest / "metrics.csv", [
            "run_id", "metric", "report", "value", "n_evaluations", "spread", "evaluator_hash"]),
        "evaluations": Table(dest / "evaluations.csv", [
            "run_id", "metric", "evaluator_hash", "evaluation", "field", "value", "model_answered",
            "cost_usd", "cost_charged_usd"]),
    }
    try:
        for folder, record in runs:
            events = list(read_events(folder))
            _run(tables["runs"], exp, folder, record, factors, counting)
            _turns(tables["turns"], tables["outputs"], record, events)
            _metrics(tables["metrics"], tables["evaluations"], exp, folder, record, events)
    finally:
        for t in tables.values():
            t.close()
    return {name: t.rows for name, t in tables.items()}


def _run(table, exp, folder, record, factors, counting) -> None:
    block = record["experiment"]
    levels = block.get("levels") or {}
    tokens = record.get("tokens") or {}
    table.add(run_id=record.get("run_id"), condition=block.get("condition"),
              **{f: levels.get(f) for f in factors},
              replicate=block.get("replicate"), round=block.get("round"), seed=(block.get("seeds") or {}).get("run"),
              status=record.get("status"), stopped_by=record.get("stopped_by"),
              counts=record.get("run_id") in counting, replaced_by=block.get("replaced_by"),
              continued_from=block.get("continued_from"), study_hash=block.get("study_hash"),
              started_at=record.get("started_at"), duration_s=(record.get("latency_ms") or 0) / 1000,
              turns=record.get("turns"), cost_usd=record.get("cost_usd"),
              cost_reused_usd=record.get("cost_reused_usd"), cost_charged_usd=record.get("cost_charged_usd"),
              tokens_in=tokens.get("input"), tokens_out=tokens.get("output"),
              folder=folder.relative_to(exp.folder).as_posix())


def _turns(turns, outputs, record, events) -> None:
    started = next((e for e in events if e.get("event") == "run_started"), {})
    members = {m.get("name"): m for m in started.get("members") or []}
    n = 0
    for e in events:
        if e.get("event") != "agent_message":
            continue
        n += 1
        who = members.get(e.get("from"), {})
        usage = e.get("usage") or {}
        turns.add(run_id=record.get("run_id"), turn=n, member=e.get("from"), stage=e.get("stage"),
                  component_id=e.get("component_id"), version_hash=who.get("version_hash"),
                  model_asked=who.get("model"), model_answered=e.get("model_resolved"),
                  tokens_in=usage.get("input"), tokens_out=usage.get("output"), cost_usd=e.get("cost_usd"),
                  cost_charged_usd=e.get("cost_charged_usd"), duration_s=(e.get("latency_ms") or 0) / 1000,
                  reused=bool(e.get("reused_from")), content=e.get("content"))
        for field, value in (e.get("output") or {}).items():
            outputs.add(run_id=record.get("run_id"), turn=n, member=e.get("from"), stage=e.get("stage"),
                        field=field, value=value)


def _metrics(metrics, evaluations, exp, folder, record, events) -> None:
    rid = record.get("run_id")
    for m in exp.measures:
        if isinstance(m, AnswerKey):
            for report, value in m.score(events).items():
                metrics.add(run_id=rid, metric=m.name, report=report, value=value)
        elif isinstance(m, OutputMeasure):
            metrics.add(run_id=rid, metric=m.name, report="", value=m.value(events))
        elif isinstance(m, Evaluator):
            for line in lines_of(folder, m.name):  # every evaluation, every version
                for field, value in (line.get("output") or {}).items():
                    evaluations.add(run_id=rid, metric=m.name, evaluator_hash=line.get("evaluator_hash"),
                                    evaluation=line.get("evaluation"), field=field, value=value,
                                    model_answered=line.get("model_answered"), cost_usd=line.get("cost_usd"),
                                    cost_charged_usd=line.get("cost_charged_usd"))
            made = current(folder, m)  # the metric: the evaluator's current version only
            if not made:
                continue
            for name, f in m.output.items():
                if f.type == "text":
                    continue  # words are in evaluations.csv; a metric is a number or an answer
                values = [ln["output"][name] for ln in made if name in (ln.get("output") or {})]
                if not values:
                    continue
                value, spread = _combine(f.type, values)
                metrics.add(run_id=rid, metric=m.name, report=name, value=value, n_evaluations=len(values),
                            spread=spread, evaluator_hash=m.version)


def _combine(kind: str, values: list) -> tuple[object, float | None]:
    """A run's value from its evaluations: the mean (a yes/no: the share saying yes), with the
    SD; or for a choice the most common answer ("tie" on a tie), with the share agreeing."""
    n = len(values)
    if kind == "choice":
        top = Counter(values).most_common()
        tie = len(top) > 1 and top[0][1] == top[1][1]
        return ("tie" if tie else top[0][0]), top[0][1] / n
    numbers = [float(v) for v in values]
    mean = sum(numbers) / n
    if kind == "flag" and n == 1:
        return bool(values[0]), None
    sd = (sum((x - mean) ** 2 for x in numbers) / (n - 1)) ** 0.5 if n > 1 else None
    return mean, sd
