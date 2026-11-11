"""Experiments, step 3 (D138 (4)–(6), (10)): running, the hard ceiling, record.json."""

import io
import json
from pathlib import Path

import pytest

from teamrunner.cli import main
from teamrunner.console import Console
from teamrunner.experiment import read_experiment, build, run_seed
from teamrunner.model import Reply
from teamrunner.keys import PROVIDER_KEYS
from teamrunner.runs import load_run

TEAM = {
    "team.yaml": "name: review\ndefault_model: openai/gpt-5\ndefault_inputs:\n  code: print(1)\n",
    "chair.md": "rounds(*2) -> alice:report\n\nrounds = alice -> bob\n",
    "alice.md": "---\noutput:\n  verdict: [ok, bad]\nparams:\n  max_tokens: 200\n---\nalice reviews {{code}}.\n",
    "bob.md": "---\nparams:\n  max_tokens: 200\n---\nbob reviews {{code}}.\n",
}
FRONT = """\
name: exp
conditions:
  flat: ../review
  led:
    from: ../review
    change:
      alice.prompt: file:variants/alice.md
replications: 3
seed: 7
limits: {max_concurrent: 2, max_cost_usd: 10}
"""
OPENAI = {"openai": lambda key, settings: {"gpt-5"}}


def write(folder: Path, files: dict) -> Path:
    for name, text in files.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return folder


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    write(home, {".env": "OPENAI_API_KEY=sk-test-from-key-file\n",
                 "prices.yaml": "models:\n  openai/gpt-5: {input: 1.0, output: 10.0}\n"})
    monkeypatch.setenv("TEAMRUNNER_HOME", str(home))
    for name in PROVIDER_KEYS.values():
        monkeypatch.delenv(name, raising=False)
    return home


@pytest.fixture
def exp(tmp_path):
    write(tmp_path / "review", TEAM)
    return write(tmp_path / "exp", {"experiment.md": f"---\n{FRONT}---\n## Question\nDoes it?\n",
                                    "variants/alice.md": "alice leads the review of {{code}}.\n"})


class Paid:
    """A model that answers every call and charges a fixed amount for it."""

    def __init__(self, cost=0.001):
        self.cost, self.calls = cost, 0

    async def complete(self, model, params, messages, schema, tools=None):
        self.calls += 1
        if schema:
            fields = schema["properties"]["output"]["properties"]
            text = json.dumps({"said": "fine", "output": {k: v["enum"][0] for k, v in fields.items()}})
        else:
            text = "fine"
        return Reply(text, model, {"input": 10, "output": 5}, self.cost, 1)


def go(exp, *, rehearse=False, max_cost=None, model=None, change=None):
    from teamrunner.experiment_run import run_experiment
    stream = io.StringIO()
    seams = {} if rehearse else {"model_factory": lambda keys, settings: model, "listers": OPENAI,
                                 "model_data": lambda m: {}, "waits": (0, 0, 0)}
    code = run_experiment(exp, Console(False, stream), rehearse=rehearse, max_cost=max_cost, change=change,
                          **seams)
    return code, stream.getvalue()


def ceiling(exp, usd, replications=None):
    text = (exp / "experiment.md").read_text().replace("max_cost_usd: 10", f"max_cost_usd: {usd}")
    if replications:
        text = text.replace("replications: 3", f"replications: {replications}")
    (exp / "experiment.md").write_text(text, encoding="utf-8")


def records(exp):
    out = []
    for folder in sorted((exp / "runs").iterdir()):
        out.append((folder, json.loads((folder / "record.json").read_text())))
    return out


def test_a_rehearsal_runs_every_slot_and_counts_none(exp):
    code, text = go(exp, rehearse=True)
    assert code == 0, text
    assert "rehearsal complete, nothing counted" in text
    found = records(exp)
    assert len(found) == 6
    for folder, record in found:
        block = record["experiment"]
        assert record["rehearsal"]["seed"] == block["seeds"]["run"] == run_seed(7, block["condition"], block["replicate"])
        assert block["round"] == block["replicate"] and block["body"] == "## Question\nDoes it?"
        assert block["study_hash"].startswith("sha256:") and block["replaced_by"] is None
        assert folder.name.endswith(f"_{block['condition']}_r{block['replicate']:02d}")
        assert (folder / "resolved" / "team.yaml").is_file() and (folder / "events.jsonl").is_file()
        assert (folder / "resolved" / "bob.md").read_text() == (exp.parent / "review" / "bob.md").read_text()
    led = next(f for f, r in found if r["experiment"]["condition"] == "led")
    assert "alice leads" in (led / "resolved" / "alice.md").read_text()
    assert {r["experiment"]["condition"] for _, r in found} == {"flat", "led"}
    code, text = go(exp, rehearse=True)  # rehearsals never fill a slot
    assert len(records(exp)) == 12


def test_a_run_writes_record_json_and_resuming_skips_completed_slots(exp):
    model = Paid()
    code, text = go(exp, model=model)
    assert code == 0, text
    assert "6 completed of 6" in text and "spent $0.0300" in text
    assert model.calls == 30  # 6 runs × 5 turns
    assert all(not r["rehearsal"] and r["status"] == "completed" for _, r in records(exp))
    assert not list((exp / "runs").glob("*/run.json"))

    code, text = go(exp, model=model)
    assert code == 0 and "nothing to do" in text and model.calls == 30


    (exp / "variants" / "alice.md").write_text("alice leads, firmly, the review of {{code}}.\n", encoding="utf-8")
    code, text = go(exp, model=model)  # the study changed: nothing runs until told what to do
    assert code == 1 and model.calls == 30
    assert "The study has changed since 6 of its runs were made (led: alice.md (prompt changed))." in text
    assert "To start the changed study from round 1: --restart" in text
    assert "To keep the runs of conditions that did not change (flat): --keep-unchanged" in text

    code, text = go(exp, model=model, change="keep_unchanged")
    assert code == 0 and "3 of 6 slots already have a completed run" in text and "3 completed of 3" in text
    code, text = go(exp, model=model)  # the choice stands for this study
    assert code == 0 and "nothing to do" in text and model.calls == 45


def test_the_ceiling_is_never_passed(exp):
    ceiling(exp, 0.06, replications=2)  # the estimate is ≈ $0.05; each run really costs $0.05
    model = Paid(cost=0.01)
    code, out = go(exp, model=model)
    assert code == 2, out
    found = records(exp)
    spent = sum(r["cost_usd"] for _, r in found)
    assert spent <= 0.06 + 1e-9  # sums of floats
    stopped = [r for _, r in found if r["status"] == "stopped"]
    assert stopped and all("Stopped at the ceiling" in r["reason"] and "not sent" in r["reason"] for r in stopped)
    assert "the ceiling ($0.0600) was reached" in out and "incomplete" in out
    assert model.calls == sum(r["turns"] for _, r in found)  # a refused call never reached the model
    assert all(r["ceiling"] == {"max_cost_usd": 0.06, "from": "experiment.md"} for _, r in found)


def test_over_the_ceiling_nothing_starts_and_nothing_asks(exp):
    ceiling(exp, 0.05)  # the estimate is ≈ $0.0745
    code, out = go(exp, model=Paid(0.0))
    assert code == 1 and not (exp / "runs").exists()
    assert "Estimated cost ≈ $0.0745. 149% of max ($0.0500)" in out
    assert "✗ over the ceiling, so nothing was run. To allow more: --max-cost <usd>" in out

    code, out = go(exp, model=Paid(0.0), max_cost=1)  # --max-cost replaces max_cost_usd, and says so
    assert code == 0, out
    assert "max $1.00 from --max-cost, in place of $0.0500 in experiment.md" in out
    assert all(r["ceiling"] == {"max_cost_usd": 1, "from": "--max-cost"} for _, r in records(exp))


def test_after_round_1_the_rest_does_not_start_if_real_costs_pass_the_ceiling(exp):
    ceiling(exp, 0.1)  # ≈ $0.0745 estimated; round 1 really costs $0.04, so ≈ $0.12 in all
    model = Paid(cost=0.004)
    code, out = go(exp, model=model)
    assert code == 2, out
    assert len(records(exp)) == 2 and model.calls == 10
    assert "After round 1, from real costs: estimated cost ≈ $0.12. 120% of max ($0.10)" in out
    assert "the other rounds were not started. To allow more: --max-cost <usd>, then run again" in out


def test_the_estimate_says_more_only_as_it_nears_the_ceiling():
    from teamrunner.plan import say_estimate

    def lines(est, limit):
        stream = io.StringIO()
        say_estimate(Console(False, stream), est, limit)
        return stream.getvalue().splitlines()

    assert lines(1, 100) == ["Estimated cost ≈ $1.00. 1% of max ($100.00)"]
    assert lines(0, 100) == []
    assert lines(70, 100)[1] == "  The ceiling is hard: runs stop before passing it. To allow more: --max-cost <usd>"
    assert len(lines(60, 100)) == 1 and len(lines(70, 100)) == 2
    assert lines(95, 100)[2] == "  ! close to the ceiling: the experiment may stop incomplete"
    assert lines(1, None)[1] == "  ! no max_cost_usd: nothing caps what this spends"


def test_a_rehearsal_ignores_the_ceiling_and_ends_with_the_estimate(exp):
    code, out = go(exp, rehearse=True)
    assert out.rstrip().endswith("Estimated cost ≈ $0.0745. 1% of max ($10.00)")
    assert "ceiling is hard" not in out

    ceiling(exp, 0.004)
    code, out = go(exp, rehearse=True)
    assert code == 0 and "rehearsal complete" in out and "nothing spent" in out
    assert all(r["status"] == "completed" and r["cost_usd"] == 0 for _, r in records(exp))
    assert out.rstrip().splitlines()[-2:] == [
        "Estimated cost ≈ $0.0745. 1862% of max ($0.0040)",
        "  ! over the ceiling: a real run would not start. To allow more: --max-cost <usd>"]


def test_a_model_with_no_price_cannot_start_under_a_ceiling(exp, home):
    (home / "prices.yaml").write_text("models: {}\n", encoding="utf-8")
    (exp.parent / "review" / "team.yaml").write_text(TEAM["team.yaml"].replace("openai/gpt-5", "openai/gpt-nobody"),
                                                       encoding="utf-8")
    code, out = go(exp, model=Paid())
    assert code == 1 and "the ceiling cannot be kept" in out and "openai/gpt-nobody has no list price" in out
    assert not (exp / "runs").exists()


def test_runs_lists_an_experiment_by_condition(exp, capsys):
    go(exp, rehearse=True)
    assert main(["runs", str(exp)]) == 0
    listed = capsys.readouterr().out
    assert "flat r1" in listed and "led r3" in listed


def test_a_folder_from_before_0_2_is_still_read(tmp_path):
    folder = tmp_path / "runs" / "2026-11-01_120000_review"
    write(folder, {"run.json": json.dumps({"team": "review", "status": "completed", "inputs": {}})})
    assert load_run(folder).run["status"] == "completed"


def test_stopping_stops_every_run_and_starts_no_more(exp):
    import threading
    from teamrunner.experiment_run import run_experiment
    cancel = threading.Event()

    class Stopper(Paid):
        async def complete(self, *args, **kwargs):
            if self.calls == 7:
                cancel.set()  # as Ctrl-C does
            return await super().complete(*args, **kwargs)

    stream, model = io.StringIO(), Stopper()
    code = run_experiment(exp, Console(False, stream), model_factory=lambda k, s: model, listers=OPENAI,
                          model_data=lambda m: {}, waits=(0, 0, 0), cancel=cancel)
    assert code == 130, stream.getvalue()
    found = records(exp)
    assert len(found) < 6 and any(r["status"] == "stopped" for _, r in found)


def test_a_single_run_can_have_a_ceiling(exp):
    from teamrunner.run import Options, run_team
    team = exp.parent / "review"
    seams = {"model_factory": lambda keys, settings: Paid(0.01), "listers": OPENAI,
             "model_data": lambda m: {}, "waits": (0, 0, 0)}
    outcome = run_team(team, Options(quiet=True, max_cost=0.02, max_cost_from="--max-cost"), **seams)
    record = json.loads((outcome.folder / "record.json").read_text())
    assert outcome.status == "stopped" and "Stopped at the ceiling" in record["reason"]
    assert record["cost_usd"] <= 0.02 and record["ceiling"] == {"max_cost_usd": 0.02, "from": "--max-cost"}
    outcome = run_team(team, Options(quiet=True), **seams)
    record = json.loads((outcome.folder / "record.json").read_text())
    assert outcome.status == "completed" and record["ceiling"] is None
    outcome = run_team(team, Options(quiet=True, rehearse=True, max_cost=0.0001, max_cost_from="--max-cost"))
    assert outcome.status == "completed"  # a rehearsal checks the wiring, not the budget


def test_max_cost_must_be_an_amount(capsys):
    with pytest.raises(SystemExit):
        main(["experiment", ".", "--max-cost", "lots"])
    assert "give an amount in US dollars above 0" in capsys.readouterr().err


def test_a_run_says_when_the_ceiling_holds_litellms_maximum(exp):
    bob = exp.parent / "review" / "bob.md"
    bob.write_text("---\nparams:\n  temperature: 1\n---\nbob reviews {{code}}.\n", encoding="utf-8")  # no max_tokens
    code, out = go(exp, model=Paid(0.0), max_cost=50)
    assert code == 0, out
    assert "  bob (openai/gpt-5): no max_tokens, so LiteLLM's maximum is used: 128,000 tokens a reply" in out
    code, out = go(exp, rehearse=True)
    assert "LiteLLM's maximum" not in out  # a rehearsal holds nothing


def test_ids_are_written_into_the_files_once_and_every_run_carries_them(exp):
    import re
    from teamrunner.experiment import _id_of
    variant = exp / "variants" / "whole_bob.md"
    variant.write_text("---\nparams:\n  max_tokens: 200\n---\nbob reviews {{code}} closely.\n", encoding="utf-8")
    newcomer = exp / "variants" / "carol.md"
    newcomer.write_text("---\nparams:\n  max_tokens: 200\n---\ncarol reads {{code}}.\n", encoding="utf-8")
    text = (exp / "experiment.md").read_text().replace(
        "      alice.prompt: file:variants/alice.md\n",
        "      alice.prompt: file:variants/alice.md\n      bob: file:variants/whole_bob.md\n"
        "      carol: file:variants/carol.md\n")
    (exp / "experiment.md").write_text(text, encoding="utf-8")
    from teamrunner.experiment import read_experiment, build
    hashes = {c.name: c.study_hash for c in build(read_experiment(exp)).conditions}

    code, out = go(exp, rehearse=True)
    assert code == 0, out
    team = exp.parent / "review"
    alice, bob = _id_of(team / "alice.md"), _id_of(team / "bob.md")
    assert alice and bob and re.search(r"^id: ", (team / "team.yaml").read_text(), re.M)
    assert _id_of(variant) == bob  # a new version of bob: bob's id
    assert _id_of(newcomer) not in (None, alice, bob)  # a new member: an id of its own
    for folder, record in records(exp):
        resolved = folder / "resolved"
        assert _id_of(resolved / "alice.md") == alice and _id_of(resolved / "bob.md") == bob
        if record["experiment"]["condition"] == "led":
            assert _id_of(resolved / "carol.md") == _id_of(newcomer)
            assert "closely" in (resolved / "bob.md").read_text()
    # the ids are out of the study's hash, so writing them reset nothing
    assert {c.name: c.study_hash for c in build(read_experiment(exp)).conditions} == hashes


# --- step 4: resume and changes (D138 (7)–(9), amended) ---------------------------------------

def events_of(folder):
    return [json.loads(line) for line in (folder / "events.jsonl").read_text().splitlines() if line.strip()]


def test_a_cut_off_run_is_redone_in_its_slot_reusing_its_turns(exp):
    ceiling(exp, 0.06, replications=2)
    code, out = go(exp, model=Paid(cost=0.01))
    assert code == 2, out
    first = {f.name: r for f, r in records(exp)}
    cut = {n: r for n, r in first.items() if r["status"] == "stopped"}
    assert cut and all(r["stopped_by"] == "ceiling" for r in cut.values())
    assert any(r["turns"] > 0 for r in cut.values())  # some turns to reuse

    model = Paid(cost=0.01)
    code, out = go(exp, model=model, max_cost=10)
    assert code == 0, out
    assert f"{len(cut)} of them redo a run that failed or was cut off" in out
    found = {f.name: (f, r) for f, r in records(exp)}
    new = [(f, r) for name, (f, r) in found.items() if name not in first]
    assert len(new) == 4 - sum(1 for r in first.values() if r["status"] == "completed")
    by_id = {r["run_id"]: (f, r) for f, r in found.values()}
    redone = [(f, r) for f, r in new if r["experiment"]["continued_from"]]
    assert len(redone) == len(cut)
    for folder, record in redone:
        old_folder, old = by_id[record["experiment"]["continued_from"]]
        assert old["status"] == "stopped" and old["experiment"]["replaced_by"] == record["run_id"]
        assert record["experiment"]["seeds"] == old["experiment"]["seeds"]  # the same slot, the same seed
        reused = [e for e in events_of(folder) if e.get("reused_from")]
        assert len([e for e in reused if e["event"] == "agent_message"]) == old["turns"]
        assert all(e["reused_from"]["run_id"] == old["run_id"] for e in reused)
        assert record["status"] == "completed" and record["turns"] == 5
        assert record["cost_usd"] == pytest.approx(0.05)  # the whole run
        assert record["cost_reused_usd"] == pytest.approx(old["cost_usd"])
    fresh_calls = 5 * (len(new) - len(redone)) + sum(5 - by_id[r["experiment"]["continued_from"]][1]["turns"]
                                                     for _, r in redone)
    assert model.calls == fresh_calls  # reused turns called no model
    spent = sum(r["cost_usd"] - (r.get("cost_reused_usd") or 0) for _, r in found.values())
    from teamrunner.plan import _money
    assert f"spent {_money(model.calls * 0.01)}" in out and spent == pytest.approx((model.calls + sum(
        r["turns"] for r in first.values())) * 0.01)  # nothing paid for twice

    code, out = go(exp, model=model, max_cost=10)
    assert code == 0 and "nothing to do" in out


def test_restart_starts_the_changed_study_and_undoing_the_edit_counts_the_old_runs(exp):
    model = Paid()
    go(exp, model=model)
    (exp / "variants" / "alice.md").write_text("alice leads, firmly, the review of {{code}}.\n", encoding="utf-8")
    code, out = go(exp, model=model, change="restart")
    assert code == 0 and "6 completed of 6" in out and model.calls == 60
    assert sum(r["experiment"]["study_change"] == "restart" for _, r in records(exp)) == 6
    code, out = go(exp, model=model)
    assert "nothing to do" in out
    (exp / "variants" / "alice.md").write_text("alice leads the review of {{code}}.\n", encoding="utf-8")
    code, out = go(exp, model=model)  # back as it was: the first six count again
    assert code == 0 and "nothing to do" in out and model.calls == 60


def test_a_run_its_flow_ended_at_stop_fills_its_slot(tmp_path):
    write(tmp_path / "review", {**TEAM, "chair.md": "alice -> bob\nalice --ok--> stop\nalice --bad--> bob\n"})
    exp = write(tmp_path / "exp", {"experiment.md": f"---\n{FRONT}---\n",
                                   "variants/alice.md": "alice leads the review of {{code}}.\n"})
    model = Paid()
    code, out = go(exp, model=model)
    assert code == 0, out
    assert all(r["status"] == "stopped" and r["stopped_by"] == "flow" for _, r in records(exp))
    assert "6 ended at the flow's stop of 6" in out and "· complete" in out
    code, out = go(exp, model=model)
    assert "nothing to do" in out


def test_a_single_run_says_why_it_stopped(tmp_path):
    from teamrunner.run import Options, run_team
    write(tmp_path / "review", {**TEAM, "chair.md": "alice -> bob\nalice --ok--> stop\nalice --bad--> bob\n"})
    seams = {"model_factory": lambda keys, settings: Paid(), "listers": OPENAI,
             "model_data": lambda m: {}, "waits": (0, 0, 0)}
    outcome = run_team(tmp_path / "review", Options(quiet=True), **seams)
    record = json.loads((outcome.folder / "record.json").read_text())
    assert record["status"] == "stopped" and record["stopped_by"] == "flow" and record["cost_reused_usd"] == 0


def test_a_continued_run_reuses_toolkeeper_steps_too(tmp_path):
    import shutil
    import threading
    from teamrunner.run import Options, run_team
    team = tmp_path / "fact_checked"
    shutil.copytree(Path(__file__).parents[1] / "examples" / "fact_checked", team,
                    ignore=shutil.ignore_patterns("runs"))
    cancel, turns = threading.Event(), []

    def stop_after_web(turn):
        turns.append(turn["member"])
        if turns.count("web") == 1 and turn["member"] != "web" and len(turns) > turns.index("web") + 1:
            cancel.set()  # as Ctrl-C does, a turn after the web step

    first = run_team(team, Options(quiet=True, rehearse=True, seed=5), cancel=cancel, on_turn=stop_after_web)
    old = json.loads((first.folder / "record.json").read_text())
    assert first.status == "stopped" and old["stopped_by"] == "user"
    again = run_team(team, Options(quiet=True, rehearse=True, seed=5, continue_from=first.folder))
    assert again.status == "completed"
    reused = [e for e in events_of(again.folder) if e.get("reused_from")]
    assert any(e["event"] == "tool_call" and e.get("mandatory") for e in reused)  # the web step
    assert len([e for e in reused if e["event"] == "agent_message"]) == old["turns"]


def test_after_ctrl_c_and_after_a_failure_the_slots_are_redone(exp):
    import threading
    from teamrunner.experiment_run import run_experiment
    cancel = threading.Event()

    class Stopper(Paid):
        async def complete(self, *args, **kwargs):
            if self.calls == 7:
                cancel.set()
            return await super().complete(*args, **kwargs)

    stream, stopper = io.StringIO(), Stopper()
    run_experiment(exp, Console(False, stream), model_factory=lambda k, s: stopper, listers=OPENAI,
                   model_data=lambda m: {}, waits=(0, 0, 0), cancel=cancel)
    assert any(r.get("stopped_by") == "user" for _, r in records(exp))

    class Breaks(Paid):
        async def complete(self, *args, **kwargs):
            if self.calls == 3:
                self.calls += 1
                raise RuntimeError("the provider broke")
            return await super().complete(*args, **kwargs)

    code, out = go(exp, model=Breaks())
    assert code == 2 and any(r["status"] == "failed" for _, r in records(exp))
    code, out = go(exp, model=Paid())
    assert code == 0, out
    filled = [r for _, r in records(exp) if r["status"] == "completed"]
    assert sorted((r["experiment"]["condition"], r["experiment"]["replicate"]) for r in filled) == \
        sorted((c, n) for c in ("flat", "led") for n in (1, 2, 3))
    assert all(r["experiment"]["replaced_by"] for _, r in records(exp) if r["status"] != "completed")


def test_files_are_read_safely_from_many_threads_at_once():
    """An experiment's runs read their files at the same time; ruamel's parser keeps
    its state on the YAML object, so each thread has its own (CI, Windows, 3.10)."""
    import sys
    import threading
    from teamrunner.experiment import _yaml as experiment_yaml
    from teamrunner.loader import _yaml
    text = TEAM["team.yaml"] + "more:\n  - a\n  - {b: 1, c: [1, 2, 3]}\n"
    errors = []

    def work(reader):
        for _ in range(200):
            try:
                assert reader.load(text)["default_inputs"]["code"] == "print(1)"
            except Exception as e:  # a shared parser fails here in many ways
                errors.append(e)

    before = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)  # switch threads as often as possible
    try:
        threads = [threading.Thread(target=work, args=(r,)) for r in [_yaml, experiment_yaml] * 4]
        [t.start() for t in threads]
        [t.join() for t in threads]
    finally:
        sys.setswitchinterval(before)
    assert not errors


# --- what the provider says it charged (D120, amended) -------------------------------------

def test_the_provider_s_charge_is_recorded_beside_the_list_price(exp):
    class Billed(Paid):
        async def complete(self, *args, **kwargs):
            reply = await super().complete(*args, **kwargs)
            reply.cost_charged_usd = 0.0008  # a little under the list price
            return reply

    code, out = go(exp, model=Billed())
    assert code == 0, out
    for folder, record in records(exp):
        assert record["cost_usd"] == pytest.approx(0.005) and record["cost_charged_usd"] == pytest.approx(0.004)
        said = [e for e in events_of(folder) if e["event"] == "agent_message"]
        assert all(e["cost_charged_usd"] == pytest.approx(0.0008) for e in said)
    assert "spent $0.0300, the provider says $0.0240" in out


def test_a_charge_not_reported_is_null_never_guessed(exp):
    code, out = go(exp, model=Paid())
    assert all(r["cost_charged_usd"] is None for _, r in records(exp)) and "the provider says" not in out


def test_litellm_s_hidden_charge_is_read():
    from types import SimpleNamespace
    from teamrunner.litellm_model import _charged
    response = SimpleNamespace(_hidden_params={"additional_headers": {"llm_provider-x-litellm-response-cost": "0.0012"}})
    assert _charged(response) == pytest.approx(0.0012)
    assert _charged(SimpleNamespace()) is None and _charged(SimpleNamespace(_hidden_params={})) is None



def test_litellm_s_idle_logging_makes_no_noise_across_threads(capfd, caplog):
    """Each run has its own event loop in its own thread; LiteLLM's one logging worker
    printed "Task was destroyed" and "bound to a different event loop" for them."""
    import asyncio
    import logging
    import threading
    import litellm
    from teamrunner.litellm_model import LiteLLMModel
    LiteLLMModel({}, {})

    async def calls():
        for _ in range(3):
            await litellm.acompletion(model="openai/gpt-5", messages=[{"role": "user", "content": "hi"}],
                                      mock_response="fine", api_key="sk-mock")

    caplog.set_level(logging.WARNING)
    threads = [threading.Thread(target=lambda: asyncio.run(calls())) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    said = capfd.readouterr().err + caplog.text
    assert "Task was destroyed" not in said and "different event loop" not in said
    assert "event loop changed" not in said
