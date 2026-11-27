# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""`teamrunner --update`: update TeamRunner the way it was installed."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from teamrunner import __version__, version_check
from teamrunner.cli import main
from teamrunner.version_check import how_installed


class Dist:
    """Just enough of an importlib.metadata distribution: its INSTALLER and direct_url.json."""
    def __init__(self, installer="pip", direct=None):
        self.files = {"INSTALLER": installer + "\n", "direct_url.json": json.dumps(direct) if direct else None}

    def read_text(self, name):
        return self.files.get(name)


@pytest.fixture
def tools(monkeypatch):
    monkeypatch.setattr(version_check.shutil, "which", lambda name: f"/bin/{name}")


def test_a_uv_tool(tmp_path, tools):
    (tmp_path / "uv-receipt.toml").write_text("[tool]\n")
    assert how_installed(str(tmp_path), Dist("uv")) == (["/bin/uv", "tool", "upgrade", "teamrunner"], "uv tool")


def test_pipx(tmp_path, tools):
    (tmp_path / "pipx_metadata.json").write_text("{}")
    assert how_installed(str(tmp_path), Dist("pip")) == (["/bin/pipx", "upgrade", "teamrunner"], "pipx")


def test_uv_pip_in_a_venv(tmp_path, tools):
    command, how = how_installed(str(tmp_path), Dist("uv"))
    assert how == "uv pip"
    assert command == ["/bin/uv", "pip", "install", "--upgrade", "--python", sys.executable, "teamrunner"]


def test_plain_pip(tmp_path, tools):
    assert how_installed(str(tmp_path), Dist("pip")) == (
        [sys.executable, *version_check.SAFE_PATH, "-m", "pip", "install", "--upgrade", "--quiet",
         "teamrunner"], "pip")


def test_a_source_folder_is_never_updated_even_as_a_uv_tool(tmp_path, tools):
    (tmp_path / "uv-receipt.toml").write_text("[tool]\n")
    folder = tmp_path / "src"
    command, how = how_installed(str(tmp_path), Dist("uv", {"url": folder.as_uri(), "dir_info": {"editable": True}}))
    assert command is None and str(folder) in how and "git pull" in how


def test_an_installer_it_does_not_know(tmp_path, tools):
    command, how = how_installed(str(tmp_path), Dist("conda"))
    assert command is None and "conda" in how


@pytest.fixture
def ran(monkeypatch):
    """subprocess.run recorded: the update answers `code`, the version question `now`."""
    calls, answers = [], {"code": 0, "now": "9.9.9"}

    def run(command, **kw):
        calls.append(command)
        answers.setdefault("cwd", []).append(kw.get("cwd"))
        if "-c" in command:
            return subprocess.CompletedProcess(command, 0, stdout=answers["now"] + "\n")
        return subprocess.CompletedProcess(command, answers["code"])
    monkeypatch.setattr(subprocess, "run", run)
    return calls, answers


def test_update_runs_the_command_and_says_the_new_version(ran, monkeypatch, capsys):
    calls, _ = ran
    monkeypatch.setattr(version_check, "how_installed", lambda: (["/bin/uv", "tool", "upgrade", "teamrunner"], "uv tool"))
    assert main(["--update"]) == 0
    assert calls[0] == ["/bin/uv", "tool", "upgrade", "teamrunner"]
    assert f"from {__version__} to 9.9.9" in capsys.readouterr().out


def test_already_the_newest(ran, monkeypatch, capsys):
    _, answers = ran
    answers["now"] = __version__
    monkeypatch.setattr(version_check, "how_installed", lambda: (["pip"], "pip"))
    assert main(["--update"]) == 0
    assert "still " + __version__ + ": pip found nothing newer" in capsys.readouterr().out


def test_a_failed_update_says_so(ran, monkeypatch, capsys):
    _, answers = ran
    answers["code"] = 2
    monkeypatch.setattr(version_check, "how_installed", lambda: (["pip"], "pip"))
    assert main(["--update"]) == 1
    assert "did not finish" in capsys.readouterr().err


def test_a_missing_installer_is_named_not_run(ran, monkeypatch, capsys):
    calls, _ = ran
    monkeypatch.setattr(version_check, "how_installed", lambda: (["uv", "tool", "upgrade", "teamrunner"], "uv tool"))
    assert main(["--update"]) == 1
    assert not calls and "not on your PATH" in capsys.readouterr().err


def test_a_source_folder_runs_nothing(ran, monkeypatch, capsys):
    calls, _ = ran
    monkeypatch.setattr(version_check, "how_installed", lambda: (None, "TeamRunner runs from the source folder x"))
    assert main(["--update"]) == 1
    assert not calls and "source folder" in capsys.readouterr().err


def test_a_copy_that_refuses_to_run_can_still_update(ran, monkeypatch):
    monkeypatch.setattr(version_check, "_load", lambda: {"checked": version_check.today(), "min_secure_version": "99.0",
                                                         "min_version": "0.0.0"})
    monkeypatch.setattr(version_check, "how_installed", lambda: (["pip"], "pip"))
    assert main(["--update"]) == 0


def test_update_takes_no_command_and_a_command_is_still_needed(capsys):
    with pytest.raises(SystemExit):
        main(["--update", "run"])
    assert "--update takes no command" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main([])
    assert "required: command" in capsys.readouterr().err


# --- a downloaded folder cannot take over the update (review 2026-11-21, NS3) -------------

def test_both_commands_run_from_an_empty_folder_not_the_one_you_are_in(ran, monkeypatch, tmp_path):
    calls, answers = ran
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(version_check, "how_installed", lambda: (["pip"], "pip"))
    assert main(["--update"]) == 0
    assert len(calls) == 2 and all(cwd and Path(cwd).resolve() != tmp_path.resolve() for cwd in answers["cwd"])
    assert answers["cwd"][0] == answers["cwd"][1] and not Path(answers["cwd"][0]).exists()  # and it is removed


def test_a_teamrunner_py_or_pip_py_in_the_current_folder_is_not_imported(tmp_path, monkeypatch):
    """The real commands, run for real: the planted files would leave a mark if Python read them."""
    mark = tmp_path / "ran"
    for name in ("teamrunner.py", "pip.py"):
        (tmp_path / name).write_text(f"import pathlib\npathlib.Path({str(mark)!r}).write_text('ran')\n"
                                     "__version__ = '6.6.6'\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    real = subprocess.run
    seen = []

    def run(command, **kw):
        if "pip" in command:  # the installer itself: ask pip its version instead of installing
            command = [*command[:command.index("pip") + 1], "--version"]
            kw.update(capture_output=True)
        seen.append(real(command, **kw))
        if "pip" in command:  # an environment made by uv has no pip; the planted pip.py must not stand in
            return subprocess.CompletedProcess(command, 0)
        return seen[-1]
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(version_check, "how_installed", lambda: how_installed(str(tmp_path / "none"), Dist("pip")))
    main(["--update"])
    assert len(seen) == 2 and not mark.exists()
    assert "6.6.6" not in (seen[1].stdout or "")


def test_on_windows_a_program_found_only_in_the_current_folder_is_not_used(tmp_path, monkeypatch):
    here, tools_dir = tmp_path / "team", tmp_path / "tools"
    here.mkdir(), tools_dir.mkdir()
    (here / "uv.EXE").write_text("planted")
    monkeypatch.chdir(here)
    monkeypatch.setenv("PATHEXT", ".EXE")
    monkeypatch.setattr(version_check.shutil, "which", lambda name: str(here / "uv.EXE"))  # as Windows answers
    monkeypatch.setenv("PATH", str(tools_dir))
    assert version_check._program("uv", "win32") is None
    (tools_dir / "uv.EXE").write_text("real")
    assert version_check._program("uv", "win32") == str(tools_dir / "uv.EXE")
    monkeypatch.setenv("PATH", str(here))  # you put this folder on PATH yourself: your choice
    assert version_check._program("uv", "win32") == str(here / "uv.EXE")
    assert version_check._program("uv", "linux") == str(here / "uv.EXE")  # elsewhere `which` reads PATH only
