# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""Experiments, step 5 (D138 (11), (13), amended): metrics, evaluators, the summary."""

import io
import json
from pathlib import Path

import pytest

from teamrunner.cli import main
from teamrunner.console import Console
from teamrunner.metrics import AnswerKey, Source, read_metrics
from teamrunner.model import Reply
from teamrunner.problems import Problems
from teamrunner.summary import mean_interval, wilson

from test_experiment_run import FRONT, OPENAI, TEAM, records, write  # noqa: F401  (fixtures below)
from test_experiment_run import home  # noqa: F401  the key file and prices, for every test here

RUBRIC = """---
kind: evaluator
model: openai/gpt-5
params:
  temperature: 0
  max_tokens: 100
output:
  score: 0
  sound: true
  verdict: [good, poor]
  reason: ""
---
Grade this review of {{inputs.code}}.

{{output}}

What alice said: {{alice.output.verdict}}

{{transcript}}
"""


class Grader:
    """Answers a team's turns and an evaluator's questions, for a fixed price."""

    def __init__(self, cost=0.001):
        self.cost, self.calls, self.prompts = cost, 0, []

    async def complete(self, model, params, messages, schema, tools=None):
        self.calls += 1
        self.prompts.append(messages[0]["content"])
        if schema:
            fields = schema["properties"]["output"]["properties"]
            value = {"number": 7, "boolean": True, "string": "ok"}
            out = {k: v["enum"][0] if "enum" in v else value[v["type"]] for k, v in fields.items()}
            text = json.dumps({"said": "fine", "output": out})
        else:
            text = "fine"
        return Reply(text, model, {"input": 10, "output": 5}, self.cost, 1)


@pytest.fixture
def exp(tmp_path):
    write(tmp_path / "review", TEAM)
    front = FRONT.replace("seed: 7\n", "seed: 7\nmetrics:\n  - metrics/quality\n")
    return write(tmp_path / "exp", {"experiment.md": f"---\n{front}---\n",
                                    "variants/alice.md": "alice leads the review of {{code}}.\n",
                                    "metrics/quality.md": RUBRIC})


def go(exp, model, **kw):
    from teamrunner.experiment_run import run_experiment
    stream = io.StringIO()
    code = run_experiment(exp, Console(False, stream), model_factory=lambda k, s: model, listers=OPENAI,
                          model_data=lambda m: {}, waits=(0, 0, 0), **kw)
    return code, stream.getvalue()


def lines(folder, name="quality"):
    path = folder / "evaluations" / f"{name}.jsonl"
    return [json.loads(x) for x in path.read_text().splitlines()] if path.is_file() else []


# --- answer keys ------------------------------------------------------------------------------

def said(who, stage=None, **output):
    return {"event": "agent_message", "from": who, "stage": stage, "content": "…", "output": output}


def test_an_answer_key_counts_hits_misses_and_false_alarms():
    key = AnswerKey("bugs", Path("m.yaml"), frozenset({"10", "12", "15"}), ["hits", "misses", "false_alarms"],
                    source=Source("emma", "flagged_lines", "report"), parse="line_numbers")
    events = [said("emma", "discuss", flagged_lines="10, 12, 15"), said("emma", "report", flagged_lines="10, 4"),
              said("emma", "discuss", flagged_lines="12")]
    assert key.score(events) == {"hits": 1, "misses": 2, "false_alarms": 1}  # the answer at `report`
    assert key.score([said("emma", "discuss", flagged_lines="10")]) == {"hits": None, "misses": None,
                                                                       "false_alarms": None}


def test_suppressed_counts_what_was_raised_and_not_reported():
    key = AnswerKey("suppressed", Path("m.yaml"), frozenset({"10", "12", "15"}), ["suppressed"],
                    raised=Source("everyone", "flagged_lines"), reported=Source("emma", "flagged_lines", "report"),
                    parse="line_numbers")
    events = [said("derek", flagged_lines="12"), said("lily", flagged_lines="15, 3"),
              said("emma", "report", flagged_lines="15")]
    assert key.score(events) == {"suppressed": 1}  # 12 was raised by derek and dropped


def test_the_shipped_answer_keys_read_cleanly():
    folder = Path(__file__).parents[2] / "examples" / "experiments" / "leader_vs_flat" / "metrics"
    if not folder.is_dir():
        pytest.skip("the docs repo's examples are not beside this checkout")
    problems = Problems()
    found = read_metrics(sorted(folder.glob("*.yaml")), problems)
    assert not problems.errors and [m.name for m in found] == ["bugs_found", "suppressed"]


def test_metric_files_are_checked(tmp_path):
    files = write(tmp_path, {
        "a.yaml": "kind: answer_key\nanswer: [1]\nsource: emma.output.flagged_lines\nreports: [hits, score]\n",
        "b.yaml": "kind: answer_kee\n",
        "c.md": "---\nkind: evaluator\noutput:\n  score: 0\n---\nThe condition was {{condition}}.\n",
        "d.yaml": "kind: answer_key\nanswer: [1]\nsource: nobody.output.x\nreports: [hits]\n",
    })
    problems = Problems()
    read_metrics([files / n for n in ("a.yaml", "b.yaml", "c.md", "d.yaml")], problems,
                 {"emma": {"flagged_lines"}})
    text = "\n".join(p.message for p in problems.errors)
    assert "`score` is not something this metric can report" in text
    assert "`kind:` is `answer_key` or `evaluator`, not `answer_kee`" in text
    assert "an evaluator pins its own `model:`" in text
    assert "`{{condition}}` is not something an evaluator may read" in text
    assert "no condition has a member `nobody`" in text


# --- evaluators --------------------------------------------------------------------------------

def test_runs_are_evaluated_blind_then_summarised(exp):
    model = Grader()
    code, out = go(exp, model)
    assert code == 0, out
    found = records(exp)
    assert model.calls == 6 * 5 + 6  # the runs, then one evaluation each
    for folder, record in found:
        made = lines(folder)
        assert len(made) == 1 and made[0]["output"] == {"score": 7, "sound": True, "verdict": "good", "reason": "ok"}
        assert made[0]["evaluator_hash"].startswith("sha256:") and made[0]["source"] == RUBRIC
        assert "cost_charged_usd" in made[0] and made[0]["cost_charged_usd"] is None  # Grader does not say
        prompt = made[0]["prompt"]
        assert "print(1)" in prompt and "What alice said: ok" in prompt and "alice:\nfine" in prompt
        for secret in ("variants", "seed", "openai/gpt-5", str(record["experiment"]["seeds"]["run"])):
            assert secret not in prompt  # blind: nothing about the condition, seed or models
    assert "evaluating · 6 evaluations (quality), in an order drawn from seed 7" in out
    assert "evaluated · 6 made" in out
    assert "exp · 6 of 6 runs completed" in out and "quality.score" in out and "quality.verdict" in out
    assert "quality.sound" in out and "(Wilson)" in out

    code, out = go(exp, model)  # nothing new: nothing called
    assert model.calls == 36 and "evaluating ·" not in out


def test_an_edited_evaluator_is_evaluated_again_only_with_evaluate(exp):
    model = Grader()
    go(exp, model)
    (exp / "metrics" / "quality.md").write_text(RUBRIC.replace("Grade this", "Grade, strictly, this"),
                                                encoding="utf-8")
    code, out = go(exp, model)
    assert model.calls == 36 and "quality.md changed since 6 runs were evaluated" in out and "--evaluate" in out
    assert "left out: 6 runs not evaluated by its current version" in out
    code, out = go(exp, model, evaluating="only")
    assert code == 0 and model.calls == 42 and "evaluated · 6 made" in out
    for folder, _ in records(exp):
        made = lines(folder)
        assert len(made) == 2 and made[0]["evaluator_hash"] != made[1]["evaluator_hash"]  # the old one kept
    code, out = go(exp, model, evaluating="only")
    assert "every run that counts is evaluated" in out and model.calls == 42


def test_no_evaluate_only_runs(exp):
    model = Grader()
    code, out = go(exp, model, evaluating="none")
    assert code == 0 and model.calls == 30 and "--no-evaluate: no evaluator was called" in out
    assert not any(lines(f) for f, _ in records(exp))
    code, out = go(exp, model)  # the runs are done; evaluating is what is left
    assert model.calls == 36 and "nothing to do" in out and "evaluated · 6 made" in out


def test_summary_calls_nothing(exp, capsys):
    model = Grader()
    go(exp, model)
    assert main(["experiment", str(exp), "--summary"]) == 0
    text = capsys.readouterr().out
    assert "exp · 6 of 6 runs completed" in text and "quality.score" in text and model.calls == 36


def test_a_rehearsal_evaluates_with_placeholders(exp):
    from teamrunner.experiment_run import run_experiment
    stream = io.StringIO()
    assert run_experiment(exp, Console(False, stream), rehearse=True) == 0
    out = stream.getvalue()
    assert "evaluated · 6 made" in out and "rehearsal: placeholder values, not results" in out
    assert all(len(lines(f)) == 1 for f, _ in records(exp))


def test_the_estimate_includes_evaluating(exp):
    from teamrunner.plan import plan
    from teamrunner.prices import load_prices
    stream = io.StringIO()
    assert plan(exp, Console(False, stream), prices=load_prices(), walks=2) == 0
    assert "evaluating" in stream.getvalue() and "6 evaluations: quality" in stream.getvalue()


# --- the statistics ---------------------------------------------------------------------------

def test_intervals():
    mean, sd, (lo, hi) = mean_interval([1.0, 2.0, 3.0])
    assert (mean, sd) == (2.0, 1.0) and lo == pytest.approx(2 - 4.303 / 3 ** 0.5, abs=1e-3)
    assert wilson(5, 10) == pytest.approx((0.2366, 0.7634), abs=1e-3)
    assert wilson(0, 10)[0] == 0.0 and wilson(10, 10)[1] == pytest.approx(1.0)


def test_the_ceiling_covers_evaluating(exp):
    go(exp, Grader(), evaluating="none")  # the runs spend $0.03
    code, out = go(exp, Grader(), evaluating="only", max_cost=0.0302)
    assert code == 1 and "over the ceiling, so nothing was evaluated" in out  # the estimate is ≈ $0.045
    assert not any(lines(f) for f, _ in records(exp))

    dear = Grader(cost=0.01)  # each evaluation really costs more than estimated
    code, out = go(exp, dear, evaluating="only", max_cost=0.05)
    assert code == 2, out
    assert "not made: the ceiling ($0.0500) was reached; they are made next time" in out
    spent = sum(r["cost_usd"] for _, r in records(exp)) + sum(
        ln["cost_usd"] for f, _ in records(exp) for ln in lines(f))
    assert spent <= 0.05 + 1e-9 and dear.calls == 2
    code, out = go(exp, Grader(), evaluating="only", max_cost=1)  # the rest, next time
    assert code == 0 and all(len(lines(f)) == 1 for f, _ in records(exp))


# --- a member's typed output as a metric (D138 (13), amended) -----------------------------

def test_a_member_s_typed_output_is_a_metric_by_its_path(tmp_path):
    write(tmp_path / "review", TEAM)
    front = FRONT.replace("seed: 7\n", "seed: 7\nmetrics:\n  - alice.output.verdict\n")
    exp = write(tmp_path / "exp", {"experiment.md": f"---\n{front}---\n",
                                   "variants/alice.md": "alice leads the review of {{code}}.\n"})
    code, out = go(exp, Grader())
    assert code == 0, out
    assert "alice.output.verdict" in out and "share by answer" in out and "ok 100% [" in out
    assert main(["experiment", str(exp), "--export", str(tmp_path / "csv")]) == 0
    import csv
    rows = list(csv.DictReader((tmp_path / "csv" / "metrics.csv").open(encoding="utf-8", newline="")))
    assert len(rows) == 6 and {(r["metric"], r["report"], r["value"]) for r in rows} == {
        ("alice.output.verdict", "", "ok")}


def test_an_output_metric_is_checked(tmp_path, capsys):
    write(tmp_path / "review", {**TEAM, "alice.md": TEAM["alice.md"].replace(
        "verdict: [ok, bad]", "verdict: [ok, bad]\n  notes: \"\"")})
    front = FRONT.replace("seed: 7\n", "seed: 7\nmetrics:\n  - alice.output.verdikt\n  - bobb.output.x\n"
                          "  - alice.output.notes\n  - everyone.output.verdict\n")
    exp = write(tmp_path / "exp", {"experiment.md": f"---\n{front}---\n",
                                   "variants/alice.md": "alice leads the review of {{code}}.\n"})
    assert main(["experiment", str(exp), "--plan"]) == 1
    text = capsys.readouterr().out
    assert "alice has no typed output `verdikt`" in text and "did you mean `alice.output.verdict`?" in text
    assert "no condition has a member `bobb`" in text and "did you mean `bob`?" in text
    assert "`alice.output.notes` is text, which cannot be counted or averaged" in text
    assert "a metric reads one member's answer; name the member" in text



def test_small_numbers_and_intervals_inside_the_possible_range():
    from teamrunner.summary import _clip, _num
    assert _num(0.0082) == "0.0082" and _num(3.0) == "3.00" and _num(0.0) == "0.00" and _num(47.71) == "47.71"
    assert _clip((-0.36, 0.76), (0.0, None)) == (0.0, 0.76)  # a count is never below zero
    assert _clip((2.24, 3.36), (0.0, 3.0)) == (2.24, 3.0)  # nor above the number of answers
    assert _clip((1.0, 2.0), (None, None)) == (1.0, 2.0)
