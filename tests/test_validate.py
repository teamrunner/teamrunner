# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
from pathlib import Path
from textwrap import dedent

import os

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
    assert "`uses` comes in a later version of TeamRunner" in found


def test_yaml_one_point_two_keeps_no_a_string(tmp_path):
    report = validate(debate(tmp_path, **{"team.yaml": "default_inputs:\n  topic: no\n"}))
    assert report.ok


def test_bad_yaml_has_a_line(tmp_path):
    report = validate(debate(tmp_path, **{"for.md": "---\nmodel: [openai/gpt-5\n---\nHi {{topic}}\n"}))
    [p] = report.problems.errors
    assert p.file.name == "for.md" and "not valid YAML" in p.message and p.line


JUDGE = "---\nmodel: openai/gpt-5\noutput:\n  winner: [for, against]\n  reasoning: \"\"\n---\nJudge {{topic}}.\n"


def test_a_tab_between_words_reads_as_a_space(tmp_path):
    """YAML allows these tabs; ruamel stops at them, so TeamRunner reads each as a space."""
    report = validate(debate(tmp_path, **{
        "team.yaml": 'name:\tdebate\t\ndefault_inputs:\n  topic:\t"Cars\tdowntown"  \t# a comment\n\t\n',
        "for.md": DEBATER.replace("description: Argues in favor", "description: Argues\tin favor"),
        "judge.md": JUDGE.replace("  winner: [for, against]", "  winner:\t[for,\tagainst]")}))
    assert report.ok, messages(report)
    team = report.package.team
    assert team.doc.data["name"] == "debate"
    assert team.doc.data["default_inputs"]["topic"] == "Cars\tdowntown"  # in quotes, a tab stays a tab
    by_name = {m.name: m for m in team.members}
    assert by_name["for"].front.data["description"] == "Argues in favor"
    assert list(by_name["judge"].front.data["output"]["winner"]) == ["for", "against"]


@pytest.mark.parametrize("judge", [JUDGE.replace("  winner", "\twinner"), JUDGE.replace("  winner", "  \twinner")])
def test_a_tab_that_indents_a_member_line(tmp_path, judge):
    report = validate(debate(tmp_path, **{"judge.md": judge}))
    [p] = report.problems.errors
    assert (p.file.name, p.line) == ("judge.md", 4)
    assert p.message == "this line is indented with a tab; YAML needs spaces to indent"


def test_an_unreadable_team_yaml_is_the_only_problem(tmp_path):
    """Its `default_model:` and `default_inputs:` cannot be read, so a member's missing
    model or an input's missing default is not reported on top of it."""
    team = debate(tmp_path, **{"team.yaml": 'name: debate\ndefault_model: openai/gpt-5\ndefault_inputs:\n\ttopic: x\n',
                              "for.md": "---\ndescription: x\n---\n{{topic}}\n"})
    report = validate(team)
    assert messages(report) == [("team.yaml", 4, "this line is indented with a tab; YAML needs spaces to indent")]
    assert messages(report, "warning") == []


def test_a_task_file_indented_with_a_tab(tmp_path):
    from teamrunner.inputs import InputError, read_task_file
    task = tmp_path / "task.yaml"
    task.write_text("inputs:\n\ttopic: x\n", encoding="utf-8")
    with pytest.raises(InputError, match="line 2 is indented with a tab"):
        read_task_file(task)
    task.write_text("inputs:\n  topic:\tx\t\n", encoding="utf-8")
    assert read_task_file(task) == {"topic": "x"}


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


@pytest.mark.parametrize("written", ["sub/careful.yaml", "sub\\\\careful.yaml"])
def test_a_profile_in_a_folder_of_models_is_refused(tmp_path, written):
    """It passed validate, then `run --rehearse` crashed on the name (docs probe, 2026-11-19)."""
    team = debate(tmp_path, **{
        "team.yaml": f'default_model: "{written}"\ndefault_inputs:\n  topic: x\n',
        "for.md": "---\ndescription: x\n---\n{{topic}}\n",
        "models/sub/careful.yaml": "name: openai/gpt-5\n",
    })
    [p] = validate(team).problems.errors
    assert (p.file.name, p.line) == ("team.yaml", 1)
    assert "names a folder: model profiles sit directly in models/" in p.message
    assert p.fix == "move the file to models/careful.yaml and write `careful.yaml`"


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
      "web_search_brave: {}\n  run_code: {}"), "a toolkeeper holds one tool, and this one names"),
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
    assert any(m.startswith("`whiteboard.yaml` comes in a later version") for m in found)
    assert any(m.startswith("teams inside teams") for m in found)


def test_names_from_files(tmp_path):
    report = validate(debate(tmp_path, **{"01_for.md": DEBATER, "done.md": DEBATER}))
    found = {p.message for p in report.problems.errors}
    assert "two files give the member name `for`: 01_for.md and for.md" in found
    assert "`done` is a word the flow uses, so no member may have it" in found


def test_two_members_with_one_id_are_refused(tmp_path):
    """A copied member file keeps its id, and the two would share one history."""
    same = "---\nid: 01JZY4D5E6F7G8H9JAKBMCNDPE\nmodel: openai/gpt-5\n---\n{{topic}}\n"
    report = validate(debate(tmp_path, **{"for.md": same, "against.md": same}))
    assert messages(report) == [("for.md", 2, "against.md and for.md have the same `id:`; each member needs its own")]
    assert "delete the `id:` line in for.md" in report.problems.errors[0].fix


def test_notes_are_not_members(tmp_path):
    report = validate(debate(tmp_path, **{"README.md": "# Notes\nnot a member\n"}))
    assert report.ok and len(report.package.team.members) == 3


def test_secrets_are_refused(tmp_path):
    key = "sk-proj-" + "a1B2c3D4e5F6g7H8i9J0k1L2"
    report = validate(debate(tmp_path, **{"for.md": DEBATER + f"\nMy key is {key}\n"}))
    [p] = report.problems.errors
    assert "API key" in p.message and key not in (p.value or "")


@pytest.mark.parametrize("name, key", [
    ("notes/keys.csv", "BSA" + "x7Kq2Lm9Pz4Rt6Vw8Yb1Nc3Df"),  # Brave, the tutorial's second key
    ("tools.py", "gsk_" + "a1B2c3D4e5" * 5),  # Groq
    ("data.txt", "xai-" + "a1B2c3D4e5" * 5),  # xAI
])
def test_more_kinds_of_key_in_more_kinds_of_file_are_refused(tmp_path, name, key):
    """Found in the 2026-11-16 review (D29): Brave, Groq and xAI keys, and .csv or .py files,
    were not looked at."""
    report = validate(debate(tmp_path, **{name: f"something {key}\n"}))
    assert any("API key" in p.message for p in report.problems.errors), report.problems.errors


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
    assert main(["validate", str(team), "--offline"]) == 0
    first = capsys.readouterr().out.splitlines()
    assert [line.split("  ")[0] for line in first[:-1]] == [
        "+ team.yaml", "+ against.md", "+ for.md", "+ judge.md"]
    assert first[-1] == "debate — 3 members, chair.md · OK · keys and models not checked (--offline)"
    assert main(["validate", str(team), "--offline"]) == 0  # ids are written once
    assert capsys.readouterr().out.strip() == "debate — 3 members, chair.md · OK · keys and models not checked (--offline)"
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


def test_a_list_of_models_is_checked_entry_by_entry(tmp_path):
    report = validate(debate(tmp_path, **{
        "team.yaml": "default_model: openai/gpt-5, careful.yaml\ndefault_inputs:\n  topic: x\n",
        "models/careful.yaml": "name: openai/gpt-5-mini\n",
        "for.md": DEBATER.replace("openai/gpt-5", "openai/gpt-5, opneai/gpt-5, openai/gpt-5"),
        "against.md": DEBATER.replace("openai/gpt-5", "openai/gpt-5, carefull.yaml"),
    }))
    messages = sorted((p.file.name, p.message) for p in report.problems.errors)
    assert ("for.md", "`openai/gpt-5` is named twice in this list of models") in messages
    assert ("for.md", "`opneai` is not a model provider TeamRunner knows") in messages
    assert ("against.md", "there is no model profile `carefull.yaml` in models/") in messages
    assert len(messages) == 3


@pytest.mark.parametrize("written", ["openai/gpt-5,", "openai/gpt-5 anthropic/x"])
def test_a_list_must_be_separated_by_commas(tmp_path, written):
    report = validate(debate(tmp_path, **{"for.md": DEBATER.replace("openai/gpt-5", written)}))
    assert not report.ok


def test_a_profile_names_one_model(tmp_path):
    report = validate(debate(tmp_path, **{
        "team.yaml": "default_model: careful.yaml\ndefault_inputs:\n  topic: x\n",
        "for.md": "---\ndescription: x\n---\n{{topic}}\n",
        "models/careful.yaml": "name: openai/gpt-5, openai/gpt-5-mini\n",
    }))
    assert any("a profile names one model" in (p.message + " " + (p.fix or "")) for p in report.problems.errors)


def test_only_input_must_name_an_input_a_prompt_uses(tmp_path):
    def mailroom(name):
        return make_team(tmp_path / name, {
            "team.yaml": "name: mail\ndefault_model: openai/gpt-5\ndefault_inputs:\n  email: m001\n",
            "reader.md": "---\ndescription: sorts\n---\nSort the email {{email}}.\n",
            "mailroom.md": f"---\nkind: toolkeeper\ntools:\n  read_email: {{only_input: {name}}}\n"
                           "access:\n  reader: standard\n---\nI hand over one email.\n"})
    assert messages(validate(mailroom("email"))) == []
    [(file, line, message)] = messages(validate(mailroom("emial")))
    assert (file, line) == ("mailroom.md", 4) and message == "`only_input: emial` names an input no prompt uses"


def test_model_lookup_reads_litellm_s_table_without_importing_it():
    """validate looks up retirement dates in LiteLLM's installed table (D120). Importing
    LiteLLM for that took seconds per run; reading the file gives the same answer."""
    import json
    import subprocess
    import sys
    script = ("import sys, json; from teamrunner.checks import litellm_data; "
              "row = litellm_data('anthropic/claude-haiku-4-5'); "
              "print(json.dumps({'row': row, 'imported': 'litellm' in sys.modules}))")
    found = json.loads(subprocess.run([sys.executable, "-c", script], capture_output=True,
                                      text=True, check=True).stdout)
    assert found["imported"] is False
    import litellm
    assert found["row"] and found["row"] == (litellm.model_cost.get("anthropic/claude-haiku-4-5")
                                             or litellm.model_cost.get("claude-haiku-4-5"))


# --- what a team file may set on a model -------------------------------------------

@pytest.mark.parametrize("setting", [
    "api_base: https://collector.example/v1", "base_url: https://collector.example/v1",
    "api_key: sk-x", "mock_response: I agree", "model: openai/other", "extra_headers: {X-A: b}",
    "custom_llm_provider: openai",
])
def test_params_take_model_settings_only(tmp_path, setting):
    """A team someone else wrote cannot say where a call goes or invent its reply."""
    name = setting.split(":")[0]
    member = f"---\ndescription: x\nmodel: openai/gpt-5\nparams:\n  temperature: 0.2\n  {setting}\n---\n{{{{topic}}}}\n"
    report = validate(debate(tmp_path / "a", **{"for.md": member}))
    assert [(p.file.name, p.line, p.message) for p in report.problems.errors] == [
        ("for.md", 6, f"`{name}` is not a model setting a file may give under `params:`")]

    profile = debate(tmp_path / "b", **{"for.md": "---\nmodel: careful.yaml\n---\n{{topic}}\n",
                                        "models/careful.yaml": f"name: openai/gpt-5\nparams:\n  {setting}\n"})
    [p] = validate(profile).problems.errors
    assert (p.file.name, p.line) == ("careful.yaml", 3) and f"`{name}`" in p.message


def test_a_misspelt_model_setting_is_suggested(tmp_path):
    member = "---\nmodel: openai/gpt-5\nparams:\n  temprature: 0.2\n---\n{{topic}}\n"
    [p] = validate(debate(tmp_path, **{"for.md": member})).problems.errors
    assert p.fix == "did you mean `temperature`?"


def test_the_model_call_refuses_what_validate_refuses():
    """The last line of defence: nothing but a model setting reaches LiteLLM, whatever built the team."""
    import asyncio
    from teamrunner.checks import litellm_params
    from teamrunner.litellm_model import LiteLLMModel
    model = LiteLLMModel({"OPENAI_API_KEY": "sk-test"}, {})
    seen = {}

    async def acompletion(**kwargs):
        seen.update(kwargs)
        raise RuntimeError("no call in a test")
    model.litellm = type("L", (), {"acompletion": staticmethod(acompletion)})()
    for params in ({"api_base": "http://127.0.0.1:9"}, {"mock_response": "x"}, {"model": "openai/other"}):
        with pytest.raises(ValueError, match="cannot be set from a file's `params:`"):
            asyncio.run(model.complete("openai/gpt-5", params, [{"role": "user", "content": "hi"}], None))
        assert "cannot be set" in litellm_params("openai/gpt-5", params)
    assert seen == {}


@pytest.mark.parametrize("name, content, said", [
    ("for.md", b"---\ndescription: x\n---\nHello \xff there\n", "for.md is not UTF-8 text"),
    ("for.md", b"---\nkind: [x]\n---\nHello\n", "`kind:` is one word"),
    ("web.md", b"---\nkind: toolkeeper\ntools: 5\naccess:\n  judge: standard\n---\nI search.\n",
     "`tools:` names the tool this toolkeeper holds"),
    ("web.md", b"---\nkind: toolkeeper\ntools:\n  read_email: {only_input: [a]}\naccess:\n  judge: standard\n---\nMail.\n",
     "`only_input` should be one name, not a list"),
    ("web.md", b"---\nkind: toolkeeper\ntools:\n  read_page: {calls_per_turn: lots, network: 1}\naccess:\n  judge: standard\n---\nPages.\n",
     "`calls_per_turn` should be a whole number, 1 or more"),
])
def test_broken_files_are_problem_lines_not_crashes(tmp_path, name, content, said):
    """Found in the 2026-11-16 review (C5): each of these printed a Python traceback."""
    team = debate(tmp_path)
    (team / name).write_bytes(content)
    report = validate(team, write_ids=False)
    assert any(said in p.message for p in report.problems.errors), [p.message for p in report.problems.errors]


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="file modes are POSIX; root reads anything")
def test_an_unreadable_file_is_a_problem_line(tmp_path):
    team = debate(tmp_path)
    (team / "for.md").chmod(0)
    try:
        report = validate(team, write_ids=False)
    finally:
        (team / "for.md").chmod(0o644)
    assert any(p.message.startswith("for.md cannot be read") for p in report.problems.errors)


def test_a_byte_order_mark_is_ignored_and_kept(tmp_path):
    """Found in the 2026-11-16 review (C6): Notepad's byte-order mark made a member silently not a member."""
    team = debate(tmp_path)
    for name in ("for.md", "team.yaml"):
        (team / name).write_bytes(b"\xef\xbb\xbf" + (team / name).read_bytes())
    report = validate(team, write_ids=True)
    assert report.ok, [p.message for p in report.problems.errors]
    assert "for" in {m.name for m in report.package.team.members}
    for name in ("for.md", "team.yaml"):
        data = (team / name).read_bytes()
        assert data.startswith(b"\xef\xbb\xbf") and b"id: " in data  # kept, and the id went in after it
    assert validate(team).ok


def test_windows_line_endings_read_as_plain_newlines(tmp_path):
    """Reading bytes (for the byte-order mark) dropped Python's newline translation, so a
    file saved on Windows sent `\\r\\n` in its member's prompt; found by CI on Windows."""
    team = debate(tmp_path)
    for name in ("for.md", "team.yaml"):
        (team / name).write_bytes((team / name).read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    report = validate(team)
    assert report.ok, [p.message for p in report.problems.errors]
    [member] = [m for m in report.package.team.members if m.name == "for"]
    assert "\r" not in member.body and "\n" in member.body


def test_a_md_file_with_no_settings_block_is_named_as_a_note(tmp_path, capsys):
    """Review 2026-11-21, ND3: such a file is silently not a member. `validate` said OK and
    counted one member fewer, and a chair naming it said "not a member" with the file there."""
    from teamrunner.cli import main
    team = tmp_path / "t"
    team.mkdir()
    (team / "team.yaml").write_text("name: t\ndefault_model: openai/gpt-5\n", encoding="utf-8")
    (team / "writer.md").write_text("---\ndescription: writes\n---\nWrite.\n", encoding="utf-8")
    (team / "judge.md").write_text("Judge the writing.\n", encoding="utf-8")
    (team / "README.md").write_text("About this team.\n", encoding="utf-8")
    assert main(["validate", str(team), "--offline"]) == 0
    said = capsys.readouterr().out
    assert "judge.md has no settings block" in said and "note, not a member" in said and "README.md has" not in said
    assert "1 member" in said and "OK" in said and "warning" not in said  # allowed (D91): said, not warned
    (team / "chair.md").write_text("writer -> judge\n", encoding="utf-8")
    assert main(["validate", str(team), "--offline"]) == 1
    said = capsys.readouterr().out
    assert "`judge` is not a member of this team" in said
    assert "`judge.md` has no settings block, so it is a note, not a member" in said


# --- review 2026-11-21: the rest of the engine list ----------------------------------------------

def _messages(report) -> str:
    return "\n".join(f"{p.message} {p.fix or ''}" for p in report.problems)


def test_a_model_profile_is_named_exactly(tmp_path):
    """NC4: on a disk that ignores capitals, `Careful.yaml` passed `validate` for the file
    `careful.yaml`, and everything after it failed."""
    team = debate(tmp_path, **{"models/careful.yaml": "name: openai/gpt-5\n",
                               "for.md": "---\nmodel: Careful.yaml\n---\nfor argues for {{topic}}.\n"})
    said = _messages(validate(team))
    assert "there is no model profile `Careful.yaml`" in said and "did you mean `careful.yaml`?" in said


@pytest.mark.parametrize("flow, said", [
    ("rounds(*50000000) -> judge\n\nrounds = for -> against\n", "repeats more than 1,000 times"),
    ("rounds(*" + "9" * 5000 + ") -> judge\n\nrounds = for -> against\n", "should be a count"),
    ("rounds(*2) -> judge\n\nrounds = for -> against\ndone = for -> against\n", "a block cannot be called `done`"),
])
def test_a_count_has_a_limit_and_a_block_is_not_called_done(tmp_path, flow, said):
    """NC5 and a smaller item: `(*50000000)` validated and `explain` ran for 20 seconds."""
    assert said in _messages(validate(debate(tmp_path, **{"chair.md": flow})))


def test_a_link_back_to_the_team_s_own_folder_is_an_error_not_a_hang(tmp_path):
    """NC7: such a link was read as a subteam, and so on without end."""
    team = debate(tmp_path / "t")
    try:
        (team / "again").symlink_to(team, target_is_directory=True)
        (team / "and_again").symlink_to(team, target_is_directory=True)
    except OSError:
        pytest.skip("this system does not let the test make a link")
    assert "the team would contain itself" in _messages(validate(team))


def test_a_team_with_no_member_that_speaks_is_warned(tmp_path):
    team = tmp_path / "t"
    team.mkdir()
    (team / "team.yaml").write_text("name: t\n", encoding="utf-8")
    report = validate(team)
    assert report.ok and "no member that speaks yet" in _messages(report)


def test_an_id_is_written_so_that_the_file_is_never_seen_half_made(tmp_path):
    """NC10: of eight first runs at once, one read team.yaml while another was writing it. The
    id is now written beside the file and moved into place."""
    import os
    from teamrunner.ids import write_team_id
    path = tmp_path / "team.yaml"
    path.write_text("name: t\n", encoding="utf-8")
    os.chmod(path, 0o640)
    before = path.stat().st_ino
    ulid = write_team_id(path)
    assert f"id: {ulid}" in path.read_text(encoding="utf-8") and path.stat().st_ino != before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["team.yaml"]  # nothing left beside it
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o640
