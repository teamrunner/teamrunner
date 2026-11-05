import asyncio

import pytest

from teamrunner.build import load_team
from teamrunner.engine import Engine, FlowError, Result

from test_flow import CHECKER, WEB
from test_validate import debate


class Scripted:
    """A fake actor: each member answers from its own list, in order."""

    def __init__(self, answers=None):
        self.answers = {k: list(v) for k, v in (answers or {}).items()}
        self.turns = []

    async def __call__(self, turn):
        self.turns.append(turn)
        queue = self.answers.get(turn.step.label) or self.answers.get(turn.step.name) or []
        return Result(turn, queue.pop(0) if queue else {})


def run(team_folder, answers=None):
    team, report = load_team(team_folder)
    assert team is not None, [p.message for p in report.problems.errors]
    actor, events = Scripted(answers), []
    outcome = asyncio.run(Engine(team, actor, events.append).run())
    return outcome, actor.turns, events


def names(turns):
    return [t.step.label for t in turns]


FACT_CHECKED = """\
rounds(*2) -> judge
rounds = for_turn -> against_turn
for_turn = for -> fact_checker
           fact_checker --fail--> for
           fact_checker --pass,*3--> done
against_turn = against -> fact_checker
           fact_checker --fail--> against
           fact_checker --pass,*3--> done
"""


def test_repeats_and_passes(tmp_path):
    outcome, turns, events = run(debate(tmp_path))
    assert names(turns) == ["for", "against", "for", "against", "for", "against", "judge"]
    assert [(p.block, p.number, p.of) for p in turns[2].passes] == [("rounds", 2, 3)]
    assert turns[-1].passes == ()
    assert outcome.status == "completed" and outcome.turns == 7 and events == []


def test_a_decider_sends_the_speaker_back_and_the_count_ends_it(tmp_path):
    team = debate(tmp_path, **{"chair.md": FACT_CHECKED, "fact_checker.md": CHECKER})
    fail, ok = {"verdict": "fail"}, {"verdict": "pass"}
    # round 1: for fails once, against passes; round 2: for fails three times
    _, turns, events = run(team, {"fact_checker": [fail, ok, ok, fail, fail, fail, ok]})
    assert names(turns) == [
        "for", "fact_checker", "for", "fact_checker", "against", "fact_checker",   # round 1
        "for", "fact_checker", "for", "fact_checker", "for", "fact_checker",   # round 2: for x3
        "against", "fact_checker",
        "judge"]
    assert [(t.attempt, t.attempts_of) for t in turns if t.step.name == "for"] == [
        (1, None), (2, 3), (1, None), (2, 3), (3, 3)]  # the count starts afresh each round
    applied = [e for e in events if e["bound_applied"]]
    assert len(applied) == 1
    e = applied[0]
    assert (e["decider"], e["option"], e["to"], e["count"], e["block"]) == (
        "fact_checker", "fail", "done", 3, "for_turn")
    assert [(p.block, p.number) for p in turns[6].passes] == [("rounds", 2), ("for_turn", 1)]


def test_a_looping_decider_is_forced_forward(tmp_path):
    team = debate(tmp_path, **{
        "chair.md": "for -> fact_checker -> judge\n"
                    "fact_checker --fail--> fact_checker\nfact_checker --pass,*4--> judge\n",
        "fact_checker.md": CHECKER, "against.md": None})
    _, turns, events = run(team, {"fact_checker": [{"verdict": "fail"}] * 9})
    assert names(turns) == ["for"] + ["fact_checker"] * 4 + ["judge"]
    assert [e["to"] for e in events] == ["fact_checker"] * 3 + ["judge"]


def test_stop_ends_the_run(tmp_path):
    team = debate(tmp_path, **{
        "chair.md": "fact_checker -> for -> against -> judge\n"
                    "fact_checker --pass--> for\nfact_checker --fail--> stop\n",
        "fact_checker.md": CHECKER})
    outcome, turns, _ = run(team, {"fact_checker": [{"verdict": "fail"}]})
    assert names(turns) == ["fact_checker"] and outcome.status == "stopped_by_flow"


def test_a_step_reached_only_by_a_decision_ends_its_block(tmp_path):
    team = debate(tmp_path, **{
        "chair.md": "for -> fact_checker -> judge\n"
                    "fact_checker --fail--> against:reply\nfact_checker --pass--> judge\n",
        "fact_checker.md": CHECKER})
    _, turns, _ = run(team, {"fact_checker": [{"verdict": "fail"}]})
    assert names(turns) == ["for", "fact_checker", "against:reply"]


def test_a_toolkeeper_step_knows_who_asked(tmp_path):
    team = debate(tmp_path, **{"chair.md": "for -> against -> judge:research -> web -> judge:verdict\n",
                               "web.md": WEB})
    _, turns, _ = run(team)
    assert names(turns) == ["for", "against", "judge:research", "web", "judge:verdict"]
    assert turns[3].asker.turn is turns[2]
    assert turns[4].asker is None


def test_round_robin(tmp_path):
    _, turns, _ = run(debate(tmp_path, **{"chair.md": None}))
    assert names(turns) == ["against", "for", "judge"]


def test_a_decider_without_an_answer_is_an_error(tmp_path):
    team = debate(tmp_path, **{"chair.md": FACT_CHECKED, "fact_checker.md": CHECKER})
    with pytest.raises(FlowError, match="gave no `verdict`"):
        run(team)
