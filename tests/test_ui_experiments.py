# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""The Experiments tab (D141 (4)): the same plan and summary as the CLI, read-only and free."""

import io
import json
from collections import Counter
from urllib.parse import urlencode

import pytest

from teamrunner.console import Console
from teamrunner.experiment import build, read_experiment
from teamrunner.experiment_run import Study, experiment_records, run_experiment
from teamrunner.keys import PROVIDER_KEYS
from teamrunner.runs import read_events
from teamrunner.ui.jobs import command
from teamrunner.summary import summary
from teamrunner.ui import experiments
from test_experiment_run import FRONT, OPENAI, TEAM, Paid, write
from test_ui import get, post, running


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
def workspace(tmp_path):
    """A workspace: teams/review and experiments/exp, whose conditions name ../../teams/review."""
    root = tmp_path / "ws"
    write(root / "teams" / "review", TEAM)
    front = FRONT.replace("../review", "../../teams/review")
    write(root / "experiments" / "exp", {"experiment.md": f"---\n{front}---\n## Question\nDoes it?\n",
                                         "variants/alice.md": "alice leads the review of {{code}}.\n"})
    return root


def ran(root, model=None, rehearse=False):
    stream = io.StringIO()
    code = run_experiment(root / "experiments" / "exp", Console(False, stream), rehearse=rehearse,
                          model_factory=(lambda k, s: model) if model else None, listers=OPENAI)
    assert code == 0, stream.getvalue()


def detail(server, path="experiments/exp"):
    status, _, body = get(server, "/api/experiment?" + urlencode({"path": path}))
    return status, json.loads(body)


def test_the_list_finds_every_experiment_and_nothing_inside_one(workspace):
    ran(workspace, rehearse=True)  # runs/ and conditions/ now hold copies of the team, not experiments
    write(workspace / "experiments" / "group" / "deeper", {"experiment.md": "no settings\n"})
    with running(workspace) as server:
        status, _, body = get(server, "/api/experiments")
    assert status == 200
    found = json.loads(body)["experiments"]
    assert [e["path"] for e in found] == ["experiments/exp", "experiments/group/deeper"]
    exp, broken = found
    assert (exp["name"], exp["conditions"], exp["runs"], exp["real"], exp["rehearsals"]) == ("exp", 2, 6, 0, 6)
    assert broken["problem"]


def test_the_plan_is_what_plan_prints_and_nothing_is_spent(workspace):
    with running(workspace) as server:
        status, data = detail(server)
    assert status == 200 and data["ready"]
    assert (data["seed"], data["replications"], data["runs"]) == (7, 3, 6)
    assert data["ceiling"] == {"max_cost_usd": 10, "from": "experiment.md", "line": "experiments/exp/experiment.md:11", "words": "$10.00 for the whole experiment, running and judging (experiment.md)"}
    assert data["question"] == "## Question\nDoes it?"
    flat, led = data["conditions"]
    assert (flat["name"], flat["generated"], flat["folder"]) == ("flat", False, "teams/review")
    assert (led["name"], led["generated"], led["folder"]) == ("led", True, "experiments/exp/conditions/led")
    assert data["differences"] == [{"from": "flat", "to": "led", "files": [
        {"file": "alice.md", "kind": "changed", "detail": "prompt from variants/alice.md"}]}]
    assert sorted((s["round"], s["condition"]) for s in data["order"]) == [
        (r, c) for r in (1, 2, 3) for c in ("flat", "led")]
    assert data["estimate"]["total_usd"] > 0 and {c["condition"] for c in data["estimate"]["per_condition"]} == {"flat", "led"}
    assert all(s["run"] is None and s["rehearsal"] is None for s in data["slots"])
    assert data["summary"]["not_counted"] == {"not run yet": 6}
    assert not (workspace / "experiments" / "exp" / "runs").exists()


def test_runs_fill_the_grid_and_the_summary_matches_the_cli(workspace):
    ran(workspace, model=Paid())
    folder = workspace / "experiments" / "exp"
    failed = sorted((folder / "runs").iterdir())[-1]
    record = json.loads((failed / "record.json").read_text())
    record["status"] = "failed"
    (failed / "record.json").write_text(json.dumps(record))
    with running(workspace) as server:
        status, data = detail(server)
        assert status == 200
        cells = [s["run"] for s in data["slots"]]
        assert all(cells) and sum(1 for c in cells if not c["fills"]) == 1
        bad = next(c for c in cells if not c["fills"])
        assert bad["path"] == failed.relative_to(workspace).as_posix() and bad["status"] == "failed"
        # Each mark opens in the Run tab, through the same route as Run history.
        status, _, body = get(server, "/api/run?" + urlencode({"path": bad["path"]}))
        assert status == 200
        assert json.loads(body)["summary"]["experiment"]["condition"] == record["experiment"]["condition"]
    assert data["summary"]["completed"] == 5 and data["summary"]["not_counted"] == {"failed": 1}
    labels = [m["label"] for m in data["summary"]["measures"]]
    assert labels == ["cost_usd", "duration_s"]
    cost = data["summary"]["measures"][0]
    assert sum(r["n"] for r in cost["rows"]) == 5 and all(r["mean"] == pytest.approx(record["cost_usd"]) for r in cost["rows"])
    # The CLI prints from the same table.
    exp = read_experiment(folder)
    build(exp)
    filled = Study(exp, experiment_records(exp), None).slots()[0]
    stream = io.StringIO()
    summary(exp, sorted(filled.values(), key=lambda fr: fr[0].name), Counter({"failed": 1}), exp.measures,
            Console(False, stream), lambda f: list(read_events(f)))
    printed = stream.getvalue()
    assert "exp · 5 of 6 runs completed" in printed and "Not counted: 1 failed." in printed
    for row in cost["rows"]:
        assert f"  {row['condition']}" in printed


def test_rehearsals_show_apart_and_their_summary_is_placeholder(workspace):
    ran(workspace, rehearse=True)
    with running(workspace) as server:
        _, data = detail(server)
    assert all(s["run"] is None and s["rehearsal"]["status"] == "completed" for s in data["slots"])
    assert data["rehearsals"] == 6 and data["summary"]["completed"] == 0
    assert data["rehearsal_summary"]["completed"] == 6


def test_an_experiment_with_problems_lists_them_with_their_lines(workspace):
    write(workspace / "experiments" / "broken", {"experiment.md": "---\nname: broken\nconditions:\n  a: ../../teams/nope\n"
                                                 "replications: 2\n---\n"})
    with running(workspace) as server:
        status, data = detail(server, "experiments/broken")
    assert status == 200 and data["ready"] is False
    where = {p["where"] for p in data["problems"]}
    assert "experiments/broken/experiment.md:4" in where and "experiments/broken/experiment.md:1" in where
    assert "slots" not in data


def test_experiment_routes_need_the_token_and_stay_inside_the_folder(workspace, tmp_path):
    write(tmp_path / "outside", {"experiment.md": "---\nname: x\n---\n"})
    with running(workspace) as server:
        assert get(server, "/api/experiments", token=False)[0] == 403
        assert get(server, "/api/experiment?path=experiments/exp", token=False)[0] == 403
        assert detail(server, "../outside")[0] == 403
        assert detail(server, "teams/review")[0] == 400
        status, headers, body = get(server, "/experiments.js?token=" + server.token)
        assert status == 200 and headers["Content-Type"].startswith("text/javascript") and b"openExperiment" in body
        status, _, page = get(server, "/?token=" + server.token)
        assert b'id="experiments-view"' in page and b"/experiments.js?token=" in page


def test_validate_in_an_experiment_checks_it_as_plan_does(workspace):
    with running(workspace) as server:
        status, _, body = get(server, "/api/files?" + urlencode({"path": "experiments"}))
        assert [(e["name"], e["experiment"], e["team"]) for e in json.loads(body)["entries"]] == [("exp", True, False)]
        for path in ("experiments/exp/experiment.md", "experiments/exp/variants/alice.md", "experiments/exp"):
            status, report = post(server, "/api/validate", {"path": path, "offline": False})
            assert status == 200, report
            assert (report["ok"], report["experiment"], report["name"], report["runs"], report["team"]) == (
                True, "experiments/exp", "exp", 6, "")
            assert report["problems"] == [] and "online" not in report  # no keys, no providers asked
        # A condition's generated folder is a team of its own, checked as a team.
        status, report = post(server, "/api/validate", {"path": "experiments/exp/conditions/led/alice.md",
                                                        "offline": True})
        assert status == 200 and report["team"] == "experiments/exp/conditions/led" and "experiment" not in report
        (workspace / "experiments" / "exp" / "experiment.md").write_text(
            "---\nname: exp\nconditions:\n  flat: ../../teams/nope\nreplications: 2\n---\n")
        status, report = post(server, "/api/validate", {"path": "experiments/exp/experiment.md"})
    assert status == 200 and report["ok"] is False and report["runs"] is None
    lines = {(p["file"], p["line"], p["level"]) for p in report["problems"]}
    assert ("experiments/exp/experiment.md", 4, "error") in lines and ("experiments/exp/experiment.md", 1, "error") in lines


def test_a_run_far_dearer_than_its_condition_is_flagged_not_cut(workspace):
    ran(workspace, model=Paid())
    folder = workspace / "experiments" / "exp"
    dear = sorted((folder / "runs").iterdir())[0]
    record = json.loads((dear / "record.json").read_text())
    typical = record["cost_usd"]
    record["cost_usd"] = typical * 10
    (dear / "record.json").write_text(json.dumps(record))
    with running(workspace) as server:
        _, data = detail(server)
    s = data["summary"]
    assert s["completed"] == 6 and s["costly_times"] == 3  # still counted
    assert s["costly"] == [{"path": dear.relative_to(workspace).as_posix(),
                            "condition": record["experiment"]["condition"],
                            "replicate": record["experiment"]["replicate"],
                            "cost_usd": pytest.approx(typical * 10), "typical_usd": pytest.approx(typical)}]
    marked = [s["run"]["path"] for s in data["slots"] if s["run"]["typical_usd"]]
    assert marked == [dear.relative_to(workspace).as_posix()]
    # The CLI's summary names the same run.
    stream = io.StringIO()
    from teamrunner.experiment_run import print_summary
    assert print_summary(folder, Console(False, stream)) == 0
    printed = stream.getvalue()
    assert "1 run cost over 3× the typical run of its condition" in printed and dear.name in printed


def test_costly_needs_three_runs_and_uses_the_median():
    from pathlib import Path
    from teamrunner.summary import costly

    def runs(*costs):
        return [(Path(f"r{i}"), {"run_id": str(i), "cost_usd": c,
                                 "experiment": {"condition": "a", "replicate": i + 1}})
                for i, c in enumerate(costs)]
    assert costly(runs(1.0, 10.0)) == []  # two runs: no typical yet
    assert [f["replicate"] for f in costly(runs(1.0, 1.0, 1.0, 3.5))] == [4]  # median 1.0
    assert costly(runs(1.0, 1.0, 1.0, 3.0)) == []  # exactly 3× is not over
    assert costly(runs(0.0, 0.0, 0.0, 1.0)) == []  # nothing to compare with


def wait_done(server, path="experiments/exp", seconds=30):
    import time
    end = time.time() + seconds
    while time.time() < end:
        status, _, body = get(server, "/api/experiment-job?" + urlencode({"path": path}))
        assert status == 200
        job = json.loads(body)
        if job.get("done"):
            return job
        time.sleep(0.1)
    raise AssertionError("the experiment did not finish")


def test_rehearse_from_the_page_prints_what_the_cli_prints_and_fills_the_rehearsal_grid(workspace):
    with running(workspace) as server:
        status, job = post(server, "/api/experiment-start", {"path": "experiments/exp", "rehearse": True})
        assert status == 200 and job["command"] == command(["teamrunner", "experiment", "experiments/exp", "--rehearse"])
        job = wait_done(server)
        assert job["state"] == "finished" and job["finished"] == 6 and job["running"] == []
        printed = "\n".join(job["lines"])
        assert "rehearsal: no model is called, nothing is spent, nothing counts" in printed
        assert printed.count("✓ round") == 6 and "\033[" not in printed
        _, data = detail(server)
        assert all(s["rehearsal"]["status"] == "completed" and s["run"] is None for s in data["slots"])
        status, _, body = get(server, "/api/experiment-job")
        assert json.loads(body)["jobs"]["experiments/exp"]["done"] is True
        # A real run needs the quote the page showed (D151): without it, the quote comes back.
        server.experiment_jobs.seams = {"listers": OPENAI}
        status, report = post(server, "/api/experiment-start", {"path": "experiments/exp", "rehearse": False})
        assert status == 409 and report["quote"]["command"] == "teamrunner experiment experiments/exp", report
        assert post(server, "/api/experiment-start", {"path": "teams/review"})[0] == 400
        assert post(server, "/api/experiment-start", {"path": "experiments/exp"}, token=False)[0] == 403


def test_experiments_run_side_by_side_but_each_only_once_and_stop_is_ctrl_c(workspace):
    from threading import Event, Lock
    from teamrunner.ui.experiment_jobs import ExperimentJobs
    write(workspace / "experiments" / "other", {"experiment.md": (workspace / "experiments" / "exp" / "experiment.md")
                                               .read_text(), "variants/alice.md": "alice leads.\n"})
    release, seen = Event(), {}

    def runner(folder, out, *, rehearse, cancel, on_slot, copy_lock):
        seen[folder.name] = cancel
        out.say("experiment · started")
        while not (cancel.is_set() or release.is_set()):
            cancel.wait(0.01)
        return 130 if cancel.is_set() else 0

    jobs = ExperimentJobs(workspace, Lock(), runner=runner)
    jobs.start({"path": "experiments/exp"})
    jobs.start({"path": "experiments/other"})  # another experiment: alongside
    with pytest.raises(Exception, match="already running"):
        jobs.start({"path": "experiments/exp"})  # the same one: twice would fill the same slots
    assert jobs.busy()
    stopped = jobs.stop("experiments/exp")
    assert stopped["state"] == "stopping" and seen["exp"].is_set() and not seen["other"].is_set()
    jobs.jobs["experiments/exp"].done.wait(5)
    assert jobs.status("experiments/exp")["state"] == "stopped"
    assert jobs.status("experiments/exp")["lines"] == ["experiment · started"]
    release.set()
    jobs.jobs["experiments/other"].done.wait(5)
    assert jobs.status("experiments/other")["state"] == "finished" and not jobs.busy()
    jobs.start({"path": "experiments/exp"})  # once stopped, it can start again
    jobs.close()
    assert not jobs.busy()


# --- real runs from the page (D151) ------------------------------------------------------------

def seams(server, model):
    server.experiment_jobs.seams = {"model_factory": lambda k, s: model, "listers": OPENAI,
                                    "model_data": lambda m: {}, "waits": (0, 0, 0)}


def quote(server, path="experiments/exp", **kw):
    status, q = post(server, "/api/experiment-quote", {"path": path, **kw})
    assert status == 200, q
    return q


def test_a_real_run_starts_only_on_the_quote_it_showed(workspace):
    with running(workspace) as server:
        seams(server, Paid())
        q = quote(server)
        assert (q["todo"], q["redo"], q["done"], q["spent_usd"]) == (6, 0, 0, 0)
        assert q["can_run"] and q["blockers"] == [] and q["checks"] == [] and q["changed"] is None
        assert q["estimate_usd"] > 0 and q["most_usd"] >= q["estimate_usd"]
        assert q["ceiling"] == "$10.00 for the whole experiment, running and judging (experiment.md)"
        assert q["command"] == "teamrunner experiment experiments/exp" and q["models"] == ["openai/gpt-5"]
        assert quote(server)["fingerprint"] == q["fingerprint"]  # the same plan, the same quote
        # Without the quote, or with another one, nothing starts.
        status, report = post(server, "/api/experiment-start", {"path": "experiments/exp", "rehearse": False})
        assert status == 409 and report["quote"]["fingerprint"] == q["fingerprint"]
        assert not (workspace / "experiments" / "exp" / "runs").exists()
        status, job = post(server, "/api/experiment-start", {"path": "experiments/exp", "rehearse": False,
                                                             "fingerprint": q["fingerprint"]})
        assert status == 200 and job["command"] == command(["teamrunner", "experiment", "experiments/exp"]), job
        job = wait_done(server)
        assert job["state"] == "finished" and job["finished"] == 6 and job["spent_usd"] > 0
        _, data = detail(server)
        assert data["summary"]["completed"] == 6
        after = quote(server)
        assert after["todo"] == 0 and not after["can_run"] and after["fingerprint"] != q["fingerprint"]


def test_a_changed_file_since_the_quote_shows_a_new_one(workspace):
    with running(workspace) as server:
        seams(server, Paid())
        q = quote(server)
        (workspace / "experiments" / "exp" / "variants" / "alice.md").write_text("alice leads, firmly.\n")
        status, report = post(server, "/api/experiment-start", {"path": "experiments/exp", "rehearse": False,
                                                                "fingerprint": q["fingerprint"]})
        assert status == 409 and "changed since this was shown" in report["error"]
        assert report["quote"]["fingerprint"] != q["fingerprint"] and report["quote"]["can_run"]


def test_no_budget_line_no_run_unless_every_model_is_free(workspace):
    md = workspace / "experiments" / "exp" / "experiment.md"
    md.write_text(md.read_text().replace(", max_cost_usd: 10", ""))
    with running(workspace) as server:
        seams(server, Paid())
        q = quote(server)
        assert not q["can_run"] and q["ceiling"] is None and q["line"] == "experiments/exp/experiment.md:11"
        assert [b["kind"] for b in q["blockers"]] == ["no_ceiling"]
        status, report = post(server, "/api/experiment-start", {"path": "experiments/exp", "rehearse": False,
                                                                "fingerprint": q["fingerprint"]})
        assert status == 400 and "budget line" in report["error"]
        team = workspace / "teams" / "review" / "team.yaml"
        team.write_text(team.read_text().replace("openai/gpt-5", "ollama/llama3"))
        server.experiment_jobs.seams["listers"] = {"ollama": lambda key, settings: {"llama3"}}
        free = quote(server)
        assert free["free"] and free["blockers"] == [] and free["can_run"], free


def test_what_would_stop_the_run_is_shown_before_it_starts(workspace, home):
    (home / ".env").write_text("")
    md = workspace / "experiments" / "exp" / "experiment.md"
    with running(workspace) as server:
        seams(server, Paid())
        q = quote(server)
        assert not q["can_run"] and q["blockers"][0]["kind"] == "check" and "OPENAI_API_KEY" in q["blockers"][0]["message"]
        (home / ".env").write_text("OPENAI_API_KEY=sk-test-from-key-file\n")
        md.write_text(md.read_text().replace("max_cost_usd: 10", "max_cost_usd: 0.0001"))
        over = quote(server)
        assert [b["kind"] for b in over["blockers"]] == ["over"] and not over["can_run"]
        assert over["line"].startswith("experiments/exp/experiment.md:")


def test_a_changed_study_offers_start_over_and_keep_unchanged(workspace):
    ran(workspace, model=Paid())
    (workspace / "experiments" / "exp" / "variants" / "alice.md").write_text("alice leads, firmly.\n")
    with running(workspace) as server:
        seams(server, Paid())
        q = quote(server)
        assert q["changed"] and q["changed"]["same"] == ["flat"] and not q["can_run"]
        keep = quote(server, change="keep_unchanged")
        restart = quote(server, change="restart")
        assert (keep["todo"], restart["todo"]) == (3, 6) and keep["can_run"] and restart["can_run"]
        assert keep["command"] == "teamrunner experiment experiments/exp --keep-unchanged"
        status, _ = post(server, "/api/experiment-start", {"path": "experiments/exp", "rehearse": False,
                                                           "change": "keep_unchanged",
                                                           "fingerprint": keep["fingerprint"]})
        assert status == 200 and wait_done(server)["finished"] == 3
        _, data = detail(server)
        assert data["summary"]["completed"] == 6 and data["changed"] is None


def test_evaluate_from_the_page_makes_only_what_is_due(workspace):
    from test_metrics import RUBRIC, Grader
    md = workspace / "experiments" / "exp" / "experiment.md"
    md.write_text(md.read_text().replace("seed: 7\n", "seed: 7\nmetrics:\n  - metrics/quality\n"))
    write(workspace / "experiments" / "exp", {"metrics/quality.md": RUBRIC})
    stream = io.StringIO()
    run_experiment(workspace / "experiments" / "exp", Console(False, stream), model_factory=lambda k, s: Grader(),
                   listers=OPENAI, model_data=lambda m: {}, waits=(0, 0, 0), evaluating="none")
    with running(workspace) as server:
        seams(server, Grader())
        _, data = detail(server)
        assert data["summary"]["to_evaluate"] == 6
        q = quote(server, evaluating="only")
        assert q["evaluations"] == 6 and q["todo"] == 0 and q["can_run"], q
        assert q["command"] == "teamrunner experiment experiments/exp --evaluate"
        status, _ = post(server, "/api/experiment-start", {"path": "experiments/exp", "rehearse": False,
                                                           "evaluating": "only", "fingerprint": q["fingerprint"]})
        assert status == 200
        job = wait_done(server)
        assert job["state"] == "finished" and any("evaluated · 6 made" in line for line in job["lines"]), job["lines"]
        _, data = detail(server)
        assert data["summary"]["to_evaluate"] == 0
        assert not quote(server, evaluating="only")["can_run"]


def test_an_evaluator_setting_its_model_refuses_stops_the_run_before_anything_is_spent(workspace):
    from test_metrics import RUBRIC, Grader
    md = workspace / "experiments" / "exp" / "experiment.md"
    md.write_text(md.read_text().replace("seed: 7\n", "seed: 7\nmetrics:\n  - metrics/quality\n"))
    write(workspace / "experiments" / "exp", {"metrics/quality.md": RUBRIC.replace("params:\n", "params:\n  temperature: 0\n")})
    with running(workspace) as server:
        seams(server, Grader())
        for evaluating in ("both", "only"):
            q = quote(server, evaluating=evaluating)
            refused = [b for b in q["blockers"] if b["kind"] == "settings"]
            assert not q["can_run"] and len(refused) == 1, q["blockers"]
            assert refused[0]["message"].startswith("quality.md on openai/gpt-5: gpt-5 doesn't support temperature=0")
            assert "`params:`" in refused[0]["fix"]
    stream = io.StringIO()
    called = []
    code = run_experiment(workspace / "experiments" / "exp", Console(False, stream),
                          model_factory=lambda k, s: called.append(1) or Grader(), listers=OPENAI,
                          model_data=lambda m: {}, waits=(0, 0, 0),
                          params_check=__import__("teamrunner.checks").checks.litellm_params)
    assert code == 1 and not called and "nothing was run" in stream.getvalue(), stream.getvalue()
    assert not (workspace / "experiments" / "exp" / "runs").exists()


def test_a_model_another_running_experiment_uses_is_named(workspace):
    with running(workspace) as server:
        seams(server, Paid())
        server.experiment_jobs.models_in_use = lambda: {"experiments/other": {"openai/gpt-5"}}
        assert quote(server)["shared"] == [{"model": "openai/gpt-5", "experiment": "experiments/other"}]


def test_a_conditions_generated_folder_is_read_only(workspace):
    led = "experiments/exp/conditions/led"
    with running(workspace) as server:
        detail(server)  # opening the experiment builds conditions/
        status, _, body = get(server, "/api/file?" + urlencode({"path": f"{led}/alice.md"}))
        opened = json.loads(body)
        assert status == 200 and opened["readonly"] is True
        assert opened["generated"] == {"condition": "led", "experiment": "experiments/exp"}
        refused = [
            post(server, "/api/save", {"path": f"{led}/alice.md", "text": "x", "revision": opened["revision"]}),
            post(server, "/api/member", {"path": f"{led}/carol.md"}),
            post(server, "/api/chair", {"path": led}),
            post(server, "/api/rename", {"path": f"{led}/alice.md", "destination": f"{led}/al.md",
                                         "revision": opened["revision"]}),
            post(server, "/api/trash", {"path": f"{led}/alice.md", "revision": opened["revision"]}),
            post(server, "/api/trash-team", {"path": led}),
            post(server, "/api/new", {"path": f"{led}/inner"}),
        ]
        assert [s for s, _ in refused] == [403] * len(refused), refused
        assert all("built from experiment.md" in r["error"] for _, r in refused)
        # Its base team, and the experiment's own files, stay editable.
        for path in ("teams/review/alice.md", "experiments/exp/variants/alice.md"):
            status, _, body = get(server, "/api/file?" + urlencode({"path": path}))
            assert json.loads(body)["readonly"] is False and json.loads(body)["generated"] is None
    assert "x" != (workspace / led / "alice.md").read_text()
