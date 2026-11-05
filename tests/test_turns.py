import asyncio
import json

import pytest

from teamrunner.build import load_team
from teamrunner.engine import Engine
from teamrunner.events import Log
from teamrunner.model import Reply
from teamrunner.turns import Members, TurnFailed, read_reply

from test_engine import FACT_CHECKED
from test_flow import CHECKER
from test_validate import debate


class FakeModel:
    """Answers each member from a list; plain members get a numbered line."""

    def __init__(self, answers=None):
        self.answers = {k: list(v) for k, v in (answers or {}).items()}
        self.calls = []

    async def complete(self, model, params, messages, schema, tools=None):
        name = messages[0]["content"].split()[0]  # each fixture prompt starts with its name
        self.calls.append((name, model, params, list(messages), schema, tools))
        queue = self.answers.get(name)
        answer = queue.pop(0) if queue else f"{name} speaks ({len(self.calls)})"
        if isinstance(answer, Reply):
            return answer
        return Reply(answer, model + "-2026", {"input": 10, "output": 5}, 0.001, 20)


def fits(said, **output):
    return json.dumps({"said": said, "output": output})


NAMED = {
    "for.md": "---\nmodel: openai/gpt-5\n---\nfor argues for {{topic}}.\n",
    "against.md": "---\nmodel: openai/gpt-5\nparams:\n  temperature: 0.7\n---\nagainst argues against {{topic}}.\n",
    "judge.md": "---\nmodel: openai/gpt-5\noutput:\n  winner: [for, against]\n  reasoning: \"\"\n---\n"
                "judge decides on {{topic}}.\n",
    "fact_checker.md": CHECKER.replace("Check the latest", "fact_checker checks the latest"),
}


def run(folder, answers=None, inputs=None):
    team, report = load_team(folder)
    assert team is not None, [p.message for p in report.problems.errors]
    model, log = FakeModel(answers), Log()
    actor = Members(team, inputs or {"topic": "Remote work"}, model, log)
    asyncio.run(Engine(team, actor, log.emit).run())
    return model, log


def test_a_debate_runs_on_the_minutes(tmp_path):
    model, log = run(debate(tmp_path, **{**NAMED, "chair.md": "rounds(*2) -> judge\nrounds = for -> against\n"}),
                     {"judge": [fits("against wins.", winner="against", reasoning="clearer")]})
    assert [c[0] for c in model.calls] == ["for", "against", "for", "against", "judge"]

    # the judge reads everyone's words, labelled alike
    judge_minutes = model.calls[-1][3][1]["content"]
    assert judge_minutes.startswith("for:\nfor speaks (1)\n\nagainst:\nagainst speaks (2)")
    assert judge_minutes.endswith("It is your turn.")
    assert model.calls[-1][4]["properties"]["output"]["properties"]["winner"]["enum"] == ["for", "against"]
    assert model.calls[1][2] == {"temperature": 0.7}
    assert model.calls[0][3][0]["content"] == "for argues for Remote work."

    messages = log.of("agent_message")
    assert [m["read"] for m in messages] == [[], [1], [1, 2], [1, 2, 3], [1, 2, 3, 4]]
    last = messages[-1]
    assert (last["from"], last["content"], last["output"]) == ("judge", "against wins.",
                                                                {"winner": "against", "reasoning": "clearer"})
    assert last["model_resolved"] == "openai/gpt-5-2026" and last["instance_path"] == "judge"
    assert messages[2]["block"] == "rounds" and messages[2]["pass"] == {"number": 2, "of": 2}
    assert [e["seq"] for e in log.events] == [1, 2, 3, 4, 5]


def test_a_reply_that_does_not_fit_is_asked_again(tmp_path):
    model, log = run(debate(tmp_path, **NAMED), {"judge": [
        fits("Pro.", winner="the for side", reasoning="x"),
        "not json",
        fits("Pro.", winner="for", reasoning="x")]})
    retries = log.of("retry")
    assert [(r["kind"], r["attempt"]) for r in retries] == [("output", 1), ("output", 2)]
    assert retries[0]["reason"] == '`winner` was "the for side"; it must be one of `for`, `against`'
    third = model.calls[-1][3]
    assert third[-1]["content"].startswith("Your reply did not fit: the reply was not the JSON")
    assert log.of("agent_message")[-1]["output"]["winner"] == "for"


def test_three_tries_then_the_run_fails(tmp_path):
    with pytest.raises(TurnFailed, match="after 3 tries"):
        run(debate(tmp_path, **NAMED), {"judge": ["no"] * 3})


def test_decisions_follow_the_typed_output(tmp_path):
    model, log = run(debate(tmp_path, **{**NAMED, "chair.md": FACT_CHECKED}), {"fact_checker": [
        fits("Wrong figure.", verdict="fail", reasons="r"), *[fits("Fine.", verdict="pass", reasons="")] * 4],
        "judge": [fits("Pro.", winner="for", reasoning="")]})
    assert [c[0] for c in model.calls][:4] == ["for", "fact_checker", "for", "fact_checker"]
    decisions = log.of("route_decision")
    assert (decisions[0]["option"], decisions[0]["to"]) == ("fail", "for")
    assert log.of("agent_message")[2]["attempt"] == {"number": 2, "of": 3}


@pytest.mark.parametrize("text, wrong", [
    ('{"said": "x", "output": {"n": "4"}}', ['`n` was "4"; it must be a number']),
    ('{"said": "x", "output": {}}', ["`n` was missing"]),
    ('{"said": "x"}', ["the reply had no `output` with your fields"]),
])
def test_read_reply(text, wrong):
    from teamrunner.ir import OutputField
    assert read_reply(text, {"n": OutputField("n", "number")})[2] == wrong
