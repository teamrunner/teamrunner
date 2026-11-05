import io

from teamrunner.console import Console
from teamrunner.runs import all_runs, pick, print_list, replay, runs_folder

from test_run import home  # noqa: F401
from test_tools import LIBRARY, asks, go, pages  # noqa: F401
from test_turns import NAMED, fits
from test_validate import debate


def test_a_replay_prints_what_the_run_printed(tmp_path, pages):
    team = debate(tmp_path, **{**NAMED, "library.md": LIBRARY,
                               "chair.md": "rounds(*2) -> judge:research -> library -> judge:verdict\n"
                                           "rounds = for -> against\n"})
    outcome, _, _, printed = go(team, {
        "for": [asks("library", url="https://a.org"), "Pro."],
        "judge": [asks("library", "Checking.", url="https://b.org"), fits("Con.", winner="against", reasoning="r")]})
    assert outcome.status == "completed", outcome.reason
    [record] = all_runs(team / "runs")
    stream = io.StringIO()
    replay(record, Console(False, stream))
    replayed = stream.getvalue().splitlines()
    assert replayed[:-8] == printed.splitlines()[:-8]
    assert stream.getvalue().rstrip().endswith("- **reasoning:** r")


def test_the_list_and_picking_a_run(tmp_path):
    team = debate(tmp_path, **{**NAMED, "chair.md": "for -> against -> judge\n"})
    verdict = {"judge": [fits("Con.", winner="against", reasoning="long text that is not shown")]}
    go(team, verdict)
    go(team, {"judge": ["no"] * 3})  # fails
    top, runs = runs_folder(team)
    records = all_runs(runs)
    assert len(records) == 2 and top == team.resolve()
    stream = io.StringIO()
    print_list(top, records, records, Console(False, stream))
    lines = stream.getvalue().splitlines()
    assert lines[0].endswith("· 2 runs · ≈ $0.0080 spent at list prices")
    assert "failed" in lines[1] and "winner" not in lines[1]  # newest first; no result when failed
    assert "winner = against" in lines[2] and "long text" not in lines[2]
    assert "Cars downtown (default)" in lines[2]
    assert pick(records, "latest") == records[:1]
    assert pick(records, records[1].folder.name) == [records[1]]
    assert len(pick(records, records[0].shown_when)) in (1, 2)  # two runs may share a minute
