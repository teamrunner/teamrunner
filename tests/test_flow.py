# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
from pathlib import Path

import pytest

from teamrunner.build import load_team
from teamrunner.flow import parse_flow
from teamrunner.ir import DONE, STOP
from teamrunner.problems import Problems

from test_validate import DEBATER, debate

CHECKER = """\
---
model: openai/gpt-5
output:
  verdict: [pass, fail]
  reasons: ""
---
Check the latest speech on {{topic}}.
"""


def parse(text):
    problems = Problems()
    return parse_flow(text, Path("chair.md"), 1, problems), problems


def labels(block):
    return [s.label + (f"(*{s.repeat})" if s.repeat > 1 else "") for s in block.steps]


def errors(report):
    return [(p.line, p.message) for p in report.problems.errors]


# --- parsing ------------------------------------------------------------------

def test_blocks_repeats_stages_and_comments():
    flow, problems = parse("""\
# three rounds, then the verdict
rounds(*3) -> judge:research
  -> judge:verdict   # continues the line above

rounds = for -> against
""")
    assert problems == []
    assert labels(flow.main) == ["rounds(*3)", "judge:research", "judge:verdict"]
    assert [s.kind for s in flow.main.steps] == ["block", "member", "member"]
    assert labels(flow.blocks["rounds"]) == ["for", "against"]
    assert flow.main.steps[1].line == 2


def test_decisions_resolve_to_steps():
    flow, problems = parse("""\
host -> for:opening -> reviewer
reviewer --fail--> for:opening
reviewer --pass--> against:reply
reviewer --*3--> stop
""")
    assert problems == []
    block = flow.main
    assert labels(block) == ["host", "for:opening", "reviewer", "against:reply"]
    assert block.sequence_length == 3  # against:reply is reached by the decision only
    decision = block.decisions[2]
    assert decision.routes == {"fail": 1, "pass": 3}
    assert (decision.bound.count, decision.bound.target) == (3, STOP)


def test_an_option_and_a_count_on_one_arrow():
    flow, _ = parse("for -> checker\nchecker --fail--> for\nchecker --pass,*3--> done\n")
    decision = flow.main.decisions[1]
    assert decision.routes == {"fail": 0, "pass": DONE}
    assert (decision.bound.count, decision.bound.target) == (3, DONE)


@pytest.mark.parametrize("text, message", [
    ("a -> [b, c]\n", "members working at the same time (`[a, b]`) arrives in TeamRunner 0.3"),
    ("talk(deal is agreed, *12)\ntalk = a -> b\n", "a loop that leaves on a condition arrives in TeamRunner 0.3"),
    ("a -> -> b\n", "a step is missing between two arrows"),
    ("a(3) -> b\n", "`(3)` should be a count, such as `(*3)`"),
    ("a -> b\nc -> d\n", "a block has one line of steps"),
    ("-> a\n", "a line starting with `->` continues a line of steps"),
    ("a -> b\nb = c\nd -> e\n", "this line is outside every named block"),
    ("a -> stop -> b\n", "nothing after `stop` would ever run"),
    ("a -> a\na --x--> b -> c\n", "a decision points to one step"),
    ("a -> b\nb --x-> a\n", "this decision should read"),
    ("a -> b\nr = a\nr = b\n", "the block `r` is defined twice"),
    ("a -> b\nc --x--> a\n", "`c` does not take a turn in the main flow"),
    ("a -> b -> a\na --x--> b\n", "`a` takes more than one turn in the main flow"),
    ("# only a note\n", "this chair.md has no flow"),
])
def test_parse_errors(text, message):
    flow, problems = parse(text)
    assert flow is None
    assert any(p.message.startswith(message) for p in problems.errors), [p.message for p in problems]


# --- checks against the team --------------------------------------------------

def checked(tmp_path, chair, **files):
    return debate(tmp_path, **{"chair.md": chair, "fact_checker.md": CHECKER, **files})


def test_a_fact_checked_flow_passes(tmp_path):
    report = load_team(checked(tmp_path, """\
rounds(*3) -> judge
rounds = for_turn -> against_turn
for_turn = for -> fact_checker
           fact_checker --fail--> for
           fact_checker --pass,*3--> done
against_turn = against -> fact_checker
           fact_checker --fail--> against
           fact_checker --pass,*3--> done
"""))[1]
    assert report.problems == []
    assert report.flow.blocks["for_turn"].decisions[1].field == "verdict"


@pytest.mark.parametrize("chair, message", [
    ("for -> against -> jduge\n", "`jduge` is not a member of this team or a named block"),
    ("for -> fact_checker\nfact_checker --puss--> done\nfact_checker --fail--> for\nfact_checker --*3--> stop\n",
     "`puss` is not one of `fact_checker`'s options"),
    ("for -> fact_checker -> against -> judge\nfact_checker --pass--> against\n",
     "`fact_checker` can answer `fail`, but no arrow says where that goes"),
    ("for -> fact_checker -> against -> judge\nfact_checker --pass--> against\nfact_checker --fail--> for\n",
     "`fact_checker --fail-->` loops back, so `fact_checker` needs a counted way out"),
    ("for -> fact_checker -> against -> judge\nfact_checker --pass--> against\nfact_checker --fail,*3--> for\n",
     "the counted way out loops back too"),
    ("for -> against -> judge -> fact_checker\nagainst --pass--> judge\nagainst --fail--> judge\n",
     "`against` decides here, but its file declares no options"),
    ("for -> fact_checker -> against -> judge\nfact_checker --pass,fail--> against\nfact_checker --pass--> judge\n",
     "`fact_checker` has two arrows for `pass`"),
    ("go -> judge\ngo = for -> go\n", "a block runs itself: go → go"),
    ("rounds:first -> judge\nrounds = for -> against -> fact_checker\n", "`rounds:first`: a stage belongs"),
])
def test_flow_mistakes(tmp_path, chair, message):
    report = load_team(checked(tmp_path, chair))[1]
    assert any(p.message.startswith(message) for p in report.problems.errors), errors(report)


def test_flow_warnings(tmp_path):
    report = load_team(checked(tmp_path, "for -> against -> judge\nunused = for\n"))[1]
    assert report.ok
    warnings = {p.message for p in report.problems.warnings}
    assert warnings == {"the block `unused` is never used",
                        "`fact_checker` never takes a turn in this flow"}


WEB = """\
---
kind: toolkeeper
tools:
  web_search_brave: {}
access:
  judge: standard
---
I search the web.
"""


@pytest.mark.parametrize("chair, message", [
    ("web -> for -> against -> judge -> fact_checker\n", "`web` is a toolkeeper, so it cannot be the first step"),
    ("for -> web -> against -> judge -> fact_checker\n", "`for` must ask `web` here, but `web`'s `access:` does not let `for` in"),
    ("for -> against -> fact_checker -> judge\nfact_checker --pass--> done\nfact_checker --fail--> web\n",
     "a decision cannot point to the toolkeeper `web`"),
])
def test_toolkeeper_steps(tmp_path, chair, message):
    report = load_team(checked(tmp_path, chair, **{"web.md": WEB}))[1]
    assert any(p.message.startswith(message) for p in report.problems.errors), errors(report)


def test_the_reply_goes_to_the_asker(tmp_path):
    report = load_team(checked(tmp_path, "for -> against -> fact_checker -> judge -> web -> for\n"
                               "fact_checker --pass--> judge\nfact_checker --fail--> judge\n",
                               **{"web.md": WEB}))[1]
    assert report.ok
    assert any("`web`'s reply goes to `judge`, who asked, not to `for`" in p.message
               for p in report.problems.warnings)


def test_a_broken_member_is_reported_once(tmp_path):
    report = load_team(debate(tmp_path, **{"for.md": "---\nmodel: [x\n---\n{{topic}}\n"}))[1]
    assert len(report.problems.errors) == 1


# --- the IR -------------------------------------------------------------------

def test_round_robin_without_a_chair(tmp_path):
    team, _ = load_team(debate(tmp_path, **{"chair.md": None, "01_web.md": WEB}))
    assert team.flow.source == "round-robin"
    assert labels(team.flow.main) == ["against", "for", "judge"]  # toolkeepers take no turns


def test_ir_resolves_models_inputs_and_terms(tmp_path):
    team, report = load_team(debate(tmp_path, **{
        "team.yaml": "default_model: careful.yaml\ndefault_inputs:\n  topic: Cars\n",
        "models/careful.yaml": "name: openai/gpt-5\nparams:\n  temperature: 0.2\n  max_tokens: 500\n",
        "for.md": "---\nparams:\n  temperature: 0.9\n---\nArgue {{topic}}, {{ side }}.\n",
        "web.md": WEB.replace("judge: standard", "judge: {calls_per_turn: 6}\n  for: deny")
                     .replace("web_search_brave: {}", "web_search_brave: {max_results: 3}"),
    }))
    assert report.ok, errors(report)
    speaker = team.members["for"]
    assert speaker.model.value == "openai/gpt-5"
    assert speaker.model.source == "models/careful.yaml, named by `default_model:` in team.yaml"
    assert {k: (s.value, s.source) for k, s in speaker.params.items()} == {
        "temperature": (0.9, "its own `params:`"), "max_tokens": (500, "models/careful.yaml")}
    assert speaker.inputs == ("topic", "side")
    assert team.inputs["topic"].default == "Cars" and not team.inputs["side"].has_default
    assert team.message_goes_to is None  # two inputs, neither named `message`

    web = team.members["web"]
    assert web.base.guardrails == {"calls_per_turn": 5, "max_results": 3}
    assert web.terms_for("judge").guardrails == {"calls_per_turn": 6, "max_results": 3}
    assert web.terms_for("for") is None and web.terms_for("against") is None
    judge = team.members["judge"]
    assert judge.output["winner"].options == ("for", "against")
    assert judge.output["reasoning"].type == "text"


def test_no_ir_while_errors_stand(tmp_path):
    team, report = load_team(debate(tmp_path, **{"for.md": DEBATER.replace("model", "modle")}))
    assert team is None and not report.ok


def test_reference_check_ir():
    from teamrunner.new import EXAMPLES
    team, _ = load_team(EXAMPLES / "reference_check")
    main = team.flow.main
    reader, meta = main.decisions[0], main.decisions[1]
    assert reader.field == "paper" and reader.routes == {"full": 1, "not_full": STOP}
    assert meta.routes == {"more": 1, "done": 2} and (meta.bound.count, meta.bound.target) == (10, 2)
