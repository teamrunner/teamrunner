from pathlib import Path
from textwrap import dedent

import pytest

from teamrunner.cli import main
from teamrunner.validate import NotATeam, validate

DEBATER = """\
---
description: Argues in favor
model: openai/gpt-5
---
You argue for: {{topic}}
"""


def make_team(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dedent(text), encoding="utf-8")
    return root


def debate(tmp_path, **changes) -> Path:
    files = {
        "team.yaml": 'name: debate\ndefault_inputs:\n  topic: "Cars downtown"\n',
        "chair.md": "rounds(*3) -> judge\n\nrounds = for -> against\n",
        "for.md": DEBATER,
        "against.md": DEBATER,
        "judge.md": """\
            ---
            model: openai/gpt-5
            output:
              winner: [for, against]
              reasoning: ""
            ---
            Judge the debate on {{topic}}.
            """,
    }
    files.update(changes)
    return make_team(tmp_path, {k: v for k, v in files.items() if v is not None})


def messages(report, level="error"):
    return [(p.file.name, p.line, p.message) for p in report.problems if p.level == level]


def test_a_clean_team_passes(tmp_path):
    report = validate(debate(tmp_path))
    assert report.ok
    assert report.problems == []


def test_not_a_team(tmp_path):
    with pytest.raises(NotATeam):
        validate(tmp_path)


def test_unknown_word_suggests_the_known_one(tmp_path):
    report = validate(debate(tmp_path, **{"for.md": DEBATER.replace("description", "descripton")}))
    [p] = report.problems.errors
    assert (p.file.name, p.line) == ("for.md", 2)
    assert "descripton" in p.message and p.fix == "did you mean `description`?"


def test_name_line_says_rename_the_file(tmp_path):
    report = validate(debate(tmp_path, **{"for.md": DEBATER.replace("---\n", "---\nname: for\n", 1)}))
    [p] = report.problems.errors
    assert "rename the file" in p.fix


def test_later_and_retired_words(tmp_path):
    report = validate(debate(tmp_path, **{
        "team.yaml": 'spec_version: "0.2"\nuses: []\ndefault_inputs:\n  topic: x\n'}))
    found = {p.message for p in report.problems.errors}
    assert "`spec_version` is no longer used in team.yaml" in found
    assert "`uses` arrives in TeamRunner 0.3" in found


def test_yaml_one_point_two_keeps_no_a_string(tmp_path):
    report = validate(debate(tmp_path, **{"team.yaml": "default_inputs:\n  topic: no\n"}))
    assert report.ok


def test_bad_yaml_has_a_line(tmp_path):
    report = validate(debate(tmp_path, **{"for.md": "---\nmodel: [openai/gpt-5\n---\nHi {{topic}}\n"}))
    [p] = report.problems.errors
    assert p.file.name == "for.md" and "not valid YAML" in p.message and p.line


def test_every_llm_worker_needs_a_model(tmp_path):
    report = validate(debate(tmp_path, **{"for.md": "---\ndescription: x\n---\n{{topic}}\n"}))
    [p] = report.problems.errors
    assert p.message == "`for` has no model"


def test_default_model_and_profiles(tmp_path):
    team = debate(tmp_path, **{
        "team.yaml": "default_model: careful.yaml\ndefault_inputs:\n  topic: x\n",
        "for.md": "---\ndescription: x\n---\n{{topic}}\n",
        "models/careful.yaml": "name: anthropic/claude-sonnet-4-5\nparams:\n  temperature: 0.2\n",
    })
    report = validate(team)
    assert report.ok
    assert report.models[team / "for.md"].source == "`default_model:` in team.yaml"


def test_missing_profile_and_unknown_provider(tmp_path):
    report = validate(debate(tmp_path, **{
        "for.md": DEBATER.replace("openai/gpt-5", "carefull.yaml"),
        "against.md": DEBATER.replace("openai/gpt-5", "opneai/gpt-5"),
        "models/careful.yaml": "name: openai/gpt-5\n",
    }))
    fixes = {p.file.name: p.fix for p in report.problems.errors}
    assert fixes == {"for.md": "did you mean `careful.yaml`?", "against.md": "did you mean `openai/…`?"}


def test_output_shapes(tmp_path):
    report = validate(debate(tmp_path, **{"judge.md": """\
        ---
        model: openai/gpt-5
        output:
          winner: [for]
          score: {type: choice}
        ---
        {{topic}}
        """}))
    lines = sorted(p.line for p in report.problems.errors)
    assert lines == [4, 5]


def test_only_toolkeepers_have_tools(tmp_path):
    report = validate(debate(tmp_path, **{
        "for.md": DEBATER.replace("---\nYou", "tools:\n  web_search_brave: {}\n---\nYou")}))
    [p] = report.problems.errors
    assert p.message == "only a toolkeeper has `tools:`"


WEB = """\
---
kind: toolkeeper
tools:
  web_search_brave: {max_results: 5, calls_per_turn: 2}
access:
  judge: {calls_per_turn: 6}
  for: standard
---
I search the web.
"""


def test_a_toolkeeper_passes(tmp_path):
    assert validate(debate(tmp_path, **{"web.md": WEB})).ok


@pytest.mark.parametrize("change, message", [
    (("web_search_brave: {max_results: 5, calls_per_turn: 2}",
      "web_search_brave: {}\n  run_code: {}"), "a toolkeeper holds one tool in 0.1"),
    (("web_search_brave", "web_serch"), "`web_serch` is not a built-in tool"),
    (("max_results", "max_chars"), "`web_search_brave` has no guardrail `max_chars`"),
    (("calls_per_turn: 6", "max_calls: 6"), "`max_calls` is now `calls_per_turn`"),
    (("for: standard", "prof: standard"), "`prof` in `access:` is not a member of this team"),
    (("for: standard", "for: standrd"), "`access.for` should be"),
    (("calls_per_turn: 2", "calls_per_turn: 0"), "`tools.web_search_brave.calls_per_turn` should be"),
    (("kind: toolkeeper", "kind: toolkeeper\nmodel: openai/gpt-5"), "a toolkeeper has no `model:`"),
    (("kind: toolkeeper", "kind: gate"), "`kind: gate` is now `kind: toolkeeper`"),
])
def test_toolkeeper_mistakes(tmp_path, change, message):
    report = validate(debate(tmp_path, **{"web.md": WEB.replace(*change)}))
    assert any(p.message.startswith(message) for p in report.problems.errors), messages(report)


def test_inputs(tmp_path):
    report = validate(debate(tmp_path, **{
        "team.yaml": "message_goes_to: motion\ndefault_inputs:\n  topik: x\n"}))
    assert [p.message for p in report.problems.errors] == [
        "`message_goes_to: motion` names an input no prompt uses"]
    warnings = {p.message for p in report.problems.warnings}
    assert "no prompt uses the input `topik`" in warnings
    assert "the input `topic` has no default, so every run must give it" in warnings


def test_later_files_and_both_coordinators(tmp_path):
    report = validate(debate(tmp_path, **{
        "manager.md": "---\nmodel: openai/gpt-5\n---\nModerate.\n",
        "whiteboard.yaml": "said: {}\n",
        "sub/team.yaml": "name: sub\n",
    }))
    found = {p.message for p in report.problems.errors}
    assert "this team has both `chair.md` and `manager.md`; keep one" in found
    assert any(m.startswith("`whiteboard.yaml` arrives") for m in found)
    assert any(m.startswith("teams inside teams") for m in found)


def test_names_from_files(tmp_path):
    report = validate(debate(tmp_path, **{"01_for.md": DEBATER, "done.md": DEBATER}))
    found = {p.message for p in report.problems.errors}
    assert "two files give the member name `for`: 01_for.md and for.md" in found
    assert "`done` is a word the flow uses, so no member may have it" in found


def test_notes_are_not_members(tmp_path):
    report = validate(debate(tmp_path, **{"README.md": "# Notes\nnot a member\n"}))
    assert report.ok and len(report.package.team.members) == 3


def test_secrets_are_refused(tmp_path):
    key = "sk-proj-" + "a1B2c3D4e5F6g7H8i9J0k1L2"
    report = validate(debate(tmp_path, **{"for.md": DEBATER + f"\nMy key is {key}\n"}))
    [p] = report.problems.errors
    assert "API key" in p.message and key not in (p.value or "")


def test_links_outside_are_flagged(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "against.md").write_text(DEBATER, encoding="utf-8")
    team = debate(tmp_path / "debate", **{"against.md": None})
    (team / "against.md").symlink_to(shared / "against.md")
    report = validate(team)
    assert report.ok
    [w] = report.problems.warnings
    assert "outside the package" in w.message


def test_cli_exit_codes(tmp_path, capsys):
    team = debate(tmp_path)
    assert main(["validate", str(team)]) == 0
    first = capsys.readouterr().out.splitlines()
    assert [line.split("  ")[0] for line in first[:-1]] == [
        "+ team.yaml", "+ against.md", "+ for.md", "+ judge.md"]
    assert first[-1] == "debate — 3 members, chair.md · OK"
    assert main(["validate", str(team)]) == 0  # ids are written once
    assert capsys.readouterr().out.strip() == "debate — 3 members, chair.md · OK"
    (team / "for.md").write_text(DEBATER.replace("model", "modle"), encoding="utf-8")
    assert main(["validate", str(team)]) == 1
    assert main(["validate", str(tmp_path / "nowhere")]) == 2


def test_ids_are_written_before_the_first_setting(tmp_path):
    team = debate(tmp_path, **{
        "team.yaml": "# a note on top\nname: debate\ndefault_inputs:\n  topic: x\n",
        "web.md": "---\n# who may search\nkind: toolkeeper\ntools:\n  web_search_brave: {}\n"
                  "others: standard\n---\nI search.\n",
    })
    speaker_before = (team / "for.md").read_text(encoding="utf-8")
    report = validate(team, write_ids=True)
    assert report.ok and len(report.ids_added) == 5
    ids = dict(report.ids_added)
    assert len(set(ids.values())) == 5

    team_lines = (team / "team.yaml").read_text(encoding="utf-8").splitlines()
    assert team_lines[0] == "# a note on top"
    assert team_lines[1] == (f"id: {ids[team / 'team.yaml']}  "
                             "# added by teamrunner — keeps this team's history together")
    assert (team / "web.md").read_text(encoding="utf-8").splitlines()[2].endswith("keeps this toolkeeper's history together")
    speaker = (team / "for.md").read_text(encoding="utf-8")
    assert speaker.replace(speaker.splitlines()[1] + "\n", "", 1) == speaker_before  # nothing else changed

    again = validate(team, write_ids=True)
    assert again.ids_added == [] and again.problems == []


def test_ids_are_ulids():
    from teamrunner.ids import new_ulid, short
    from teamrunner.schema_check import validator
    ulid = new_ulid()
    assert validator("team").is_valid({"id": ulid})
    assert short(ulid) == "…" + ulid[-8:]
    assert new_ulid() > ulid or new_ulid()[:10] >= ulid[:10]  # time comes first


def test_an_existing_id_is_kept(tmp_path):
    team = debate(tmp_path, **{"team.yaml": "id: 01JZY4D5E6F7G8H9JAKBMCNDPE\ndefault_inputs:\n  topic: x\n"})
    report = validate(team, write_ids=True)
    assert (team / "team.yaml") not in dict(report.ids_added)


def test_validate_without_writing_leaves_files_alone(tmp_path):
    team = debate(tmp_path)
    before = (team / "for.md").read_text(encoding="utf-8")
    validate(team)
    assert (team / "for.md").read_text(encoding="utf-8") == before
