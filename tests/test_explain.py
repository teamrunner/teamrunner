import json

from teamrunner.build import load_team
from teamrunner.cli import main
from teamrunner.explain import diagram_page, explain, mermaid, turn_range

from test_engine import FACT_CHECKED
from test_flow import CHECKER
from test_tools import LIBRARY
from test_turns import NAMED
from test_validate import DEBATER, debate


def team_of(folder):
    team, report = load_team(folder)
    assert team is not None, [p.message for p in report.problems.errors]
    return team


def test_explain_in_words(tmp_path):
    team = team_of(debate(tmp_path, **{**NAMED, "fact_checker.md": CHECKER, "library.md": LIBRARY,
                                       "chair.md": FACT_CHECKED.replace("-> judge", "-> judge:research -> library -> judge")}))
    text = explain(team)
    assert "fact_checker — no description · openai/gpt-5 · answers verdict: pass or fail" in text
    assert "library (toolkeeper) holds read_page" in text
    assert "judge may use it: 3 calls a turn, 4 calls a run, 1 s each" in text
    assert "others: no access" in text
    assert 'topic — used by against, fact_checker, for and judge; default: "Cars downtown" · --message fills it' in text
    assert "rounds, 2 times → judge:research → library (toolkeeper) → judge" in text
    assert "fact_checker answers fail: back to for" in text
    assert "the third time fact_checker decides here, a choice that would go back is overridden: the block ends" in text
    assert "judge:research must end its turn with a request to library; the reply goes to judge alone" in text
    assert "10 to 26 turns of members" in text  # 2 rounds of 4 to 12, then the judge's two


def test_turn_ranges(tmp_path):
    assert turn_range(team_of(debate(tmp_path / "a"))) == (7, 7, False)
    stops = debate(tmp_path / "b", **{**NAMED, "fact_checker.md": CHECKER,
                                      "chair.md": "fact_checker -> for -> against -> judge\n"
                                                  "fact_checker --pass--> for\nfact_checker --fail--> stop\n"})
    assert turn_range(team_of(stops)) == (4, 4, True)


def test_the_flow_as_a_picture(tmp_path):
    team = team_of(debate(tmp_path, **{**NAMED, "fact_checker.md": CHECKER, "library.md": LIBRARY,
                                       "chair.md": FACT_CHECKED.replace("-> judge", "-> judge:research -> library -> judge")}))
    text = mermaid(team)
    assert text.startswith("flowchart TD")
    assert 'subgraph n1["rounds, 2 times"]' in text
    assert '-- "pass, or the third time" -->' in text
    assert '{{"library (toolkeeper)"}}' in text  # toolkeeper steps in their own shape (D109)
    page = diagram_page(team)
    assert '<pre class="mermaid">flowchart TD' in page and "mermaid@11.4.1" in page


def test_validate_json(tmp_path, capsys):
    team = debate(tmp_path, **{"for.md": DEBATER.replace("description", "descripton")})
    assert main(["validate", str(team), "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False and data["team"] == "debate"
    [error] = data["errors"]
    assert error == {"file": f"{team.name}/for.md", "line": 3,
                     "message": "`descripton` is not a word TeamRunner knows in a member file",
                     "value": "descripton", "fix": "did you mean `description`?"}
    assert len(data["ids_added"]) == 4
