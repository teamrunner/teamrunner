# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
import re
from pathlib import Path

import pytest

from teamrunner.cli import main
from teamrunner.new import NewError, copy_from, create
from teamrunner.validate import validate

from test_tools import LIBRARY
from test_validate import debate

ID = re.compile(r"^id: ([0-9A-Z]{26})", re.MULTILINE)
PARENT = re.compile(r"^parent_id: ([0-9A-Z]{26})", re.MULTILINE)


def test_new_makes_a_team_that_validates(tmp_path):
    folder = create(tmp_path / "my_team")
    assert (folder / ".gitignore").read_text(encoding="utf-8").endswith("runs/\n")
    assert ID.search((folder / "team.yaml").read_text(encoding="utf-8"))
    assert validate(folder).ok
    with pytest.raises(NewError, match="already exists"):
        create(folder)
    with pytest.raises(NewError, match="cannot be a team's name"):
        create(tmp_path / "my team")


def test_new_and_ids_write_lf_and_keep_a_files_own_endings(tmp_path):
    from teamrunner.ids import write_member_id, write_team_id
    from teamrunner.new import EXAMPLES
    folder = create(tmp_path / "plain")
    copied, _ = copy_from(EXAMPLES / "round_robin", tmp_path / "copied")
    written = [folder / "team.yaml", folder / ".gitignore", *copied.glob("*.md"), copied / "team.yaml"]
    assert all(b"\r\n" not in path.read_bytes() for path in written)  # LF on every system
    crlf = tmp_path / "crlf.md"
    crlf.write_bytes(b"---\r\ndescription: x\r\n---\r\nBody\r\n")
    write_member_id(crlf, "agent")
    data = crlf.read_bytes()
    assert data.count(b"\r\n") == data.count(b"\n") == 5  # the id line matches, nothing changed
    team = tmp_path / "team.yaml"
    team.write_bytes(b"name: t\r\ndescription: x")
    write_team_id(team)
    assert team.read_bytes().count(b"\r\n") == team.read_bytes().count(b"\n") == 3


def test_new_from_copies_with_fresh_ids_and_lineage(tmp_path):
    source = debate(tmp_path / "debate", **{"library.md": LIBRARY, "README.md": "# notes\n"})
    validate(source, write_ids=True)  # give the source its ids
    (source / "runs" / "old").mkdir(parents=True)
    (source / ".env").write_text("OPENAI_API_KEY=sk-x\n", encoding="utf-8")
    folder, warnings = copy_from(source, tmp_path / "mine")

    assert not (folder / "runs").exists() and not (folder / ".env").exists()
    assert "was not copied" in warnings[0]
    assert re.search(r"^name: mine$", (folder / "team.yaml").read_text(encoding="utf-8"), re.MULTILINE)
    for name in ["team.yaml", "for.md", "library.md"]:
        old, new = (source / name).read_text(encoding="utf-8"), (folder / name).read_text(encoding="utf-8")
        assert ID.search(new)[1] != ID.search(old)[1]
        assert PARENT.search(new)[1] == ID.search(old)[1]
        assert len(ID.findall(new)) == 1
    assert "keeps this toolkeeper's history" in (folder / "library.md").read_text(encoding="utf-8")
    assert (folder / "README.md").read_text(encoding="utf-8") == "# notes\n"
    assert validate(folder).ok


def test_the_new_command(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["new", "fresh"]) == 0
    assert "✓ fresh made · fresh — 0 members, round-robin" in capsys.readouterr().out
    assert main(["new", "copy", "--from", "nowhere"]) == 1
    assert "the examples are demo, fact_checked, hiring_committee, multi_agent_debate" in capsys.readouterr().err
    assert main(["new", "debate", "--from", "fact_checked"]) == 0
    assert "✓ debate copied from fact_checked · debate — 6 members, chair.md, 2 toolkeepers" in capsys.readouterr().out


def test_new_typed_in_a_workspace_goes_in_teams(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("TEAMRUNNER_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("TEAMRUNNER_WORKSPACE", raising=False)
    (tmp_path / "teams").mkdir()
    monkeypatch.chdir(tmp_path)
    assert main(["new", "fresh"]) == 0 and (tmp_path / "teams/fresh/team.yaml").exists()
    assert "teams/fresh made" in capsys.readouterr().out.replace("\\", "/")
    assert main(["new", "./here"]) == 0 and (tmp_path / "here/team.yaml").exists()  # a path is used as written


SHIPPED = ["demo", "fact_checked", "hiring_committee", "multi_agent_debate", "orchestrators_lite", "reference_check", "round_robin"]


def test_the_examples_ship():
    from teamrunner.new import examples
    assert examples() == SHIPPED  # D116, D122


@pytest.mark.parametrize("name", sorted(SHIPPED))
def test_every_shipped_example_validates_clean(name, tmp_path):
    from teamrunner.new import EXAMPLES
    import shutil
    copy = tmp_path / name
    shutil.copytree(EXAMPLES / name, copy)  # validate on a copy: the package's files are never written
    assert validate(copy).problems == []
    from teamrunner.build import load_team
    from teamrunner.checks import litellm_params, needed_keys
    team, _ = load_team(copy)
    simple = name in {"demo", "round_robin", "multi_agent_debate"}
    expected = "teamrunner/demoLLM" if simple else "anthropic/claude-haiku-5-5"
    assert needed_keys(team) == ([] if simple else ["ANTHROPIC_API_KEY"])
    assert {m.model.value for m in team.members.values() if m.model} == {expected}
    for m in team.members.values():  # every model takes its member's settings (gpt-5: no temperature)
        if m.model and m.params:
            assert litellm_params(m.model.value, {k: s.value for k, s in m.params.items()}) is None, m.name


def test_a_name_alone_is_in_the_workspace_and_a_path_is_used_as_written(tmp_path, monkeypatch):
    from teamrunner.workspace import find, not_found
    ws = tmp_path / "ws"
    (ws / "teams" / "debate").mkdir(parents=True)
    (ws / "experiments" / "study").mkdir(parents=True)
    here = tmp_path / "here"
    (here / "debate").mkdir(parents=True)
    monkeypatch.chdir(here)
    assert find("debate", "teams") == Path("debate")  # nothing remembered: here, as before
    monkeypatch.setenv("TEAMRUNNER_WORKSPACE", str(ws))
    assert find("debate", "teams") == ws / "teams" / "debate"  # even with a debate/ here
    assert find("study", "experiments") == ws / "experiments" / "study"
    for path in (".", "./debate", "../x", "teams/debate"):
        assert find(path, "teams") == Path(path)
    assert find("~/x", "teams") == Path.home() / "x"
    assert find(str(ws), "teams") == ws
    assert not_found("debate", "teams") is None
    assert "write `debate`" in not_found("teams/debate", "teams")  # the slash makes it a path from here
    (here / "mine").mkdir()
    assert "for the folder here, write ./mine" in not_found("mine", "teams")


def test_new_puts_a_named_team_in_the_workspace_and_run_finds_it_from_anywhere(tmp_path, capsys, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("TEAMRUNNER_WORKSPACE", str(ws))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert main(["new", "fresh"]) == 0 and (ws / "teams/fresh/team.yaml").exists()
    assert "teams/fresh made" in capsys.readouterr().out.replace("\\", "/")
    assert main(["new", "./here"]) == 0 and (elsewhere / "here/team.yaml").exists()  # ./ keeps it here
    capsys.readouterr()
    assert main(["validate", "fresh"]) in (0, 1)
    assert "there is no" not in capsys.readouterr().err
    assert main(["validate", "here"]) == 2
    assert "for the folder here, write ./here" in capsys.readouterr().err
    assert main(["run", "missing", "--rehearse"]) == 1
    assert "there is no team `missing` in your teamrunner workspace" in capsys.readouterr().err


def test_new_asks_where_the_workspace_is_the_first_time(tmp_path, capsys, monkeypatch):
    from teamrunner import workspace
    asked = []
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "me")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: asked.append(prompt) or "")
    monkeypatch.chdir(tmp_path)
    assert main(["new", "fresh"]) == 0
    assert asked == ["Where should your teamrunner workspace be? [~/teamrunner] "]
    assert (tmp_path / "me/teamrunner/teams/fresh/team.yaml").exists()
    assert workspace.remembered()[0] == (tmp_path / "me/teamrunner").resolve()
    assert "~/teamrunner/teams/fresh made" in capsys.readouterr().out.replace("\\", "/")
    assert main(["new", "second"]) == 0 and len(asked) == 1  # never asked again
