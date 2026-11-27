# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""Crashes (D165): the small automatic report, `teamrunner report`, and the browser page's
report button."""
import io
import json
import time
import urllib.request
from urllib.parse import parse_qs, urlsplit

import pytest

from teamrunner import __version__, crash, version_check
from teamrunner.cli import main
from teamrunner.crash import post as real_post
from teamrunner.new import EXAMPLES

from test_ui import get, post, running


class Response(io.BytesIO):
    status = 204

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TEAMRUNNER_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


@pytest.fixture
def reports(monkeypatch, home):
    """The real sender, with the network replaced: every request it makes is recorded."""
    monkeypatch.setattr(crash, "post", real_post)
    monkeypatch.delenv(crash.OFF, raising=False)
    sent = []

    def urlopen(request, timeout=None):
        sent.append(request)
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return sent


def broken(args):
    secret = {"/Users/someone/private/team": 1}
    return secret["a message with /Users/someone in it"]


def crashing(monkeypatch):
    from teamrunner import cli
    monkeypatch.setattr(cli, "cmd_validate", broken)
    return main(["validate", str(EXAMPLES / "multi_agent_debate")])


def test_a_crash_sends_three_fields_and_two_headers_and_nothing_else(reports, monkeypatch, capsys):
    assert crashing(monkeypatch) == 3
    assert len(reports) == 1
    request = reports[0]
    assert request.full_url == "https://version.teamrunner.ai/crash" and request.get_method() == "POST"
    body = json.loads(request.data)
    assert set(body) == {"type", "file", "line"}
    assert body["type"] == "KeyError" and body["file"] == "cli.py" and isinstance(body["line"], int)
    headers = {name.lower(): value for name, value in request.header_items()}
    assert set(headers) == {"user-agent", "x-teamrunner-install", "content-type"}
    assert headers["user-agent"] == version_check.user_agent()
    assert version_check._UUID4.fullmatch(headers["x-teamrunner-install"])
    everything = request.data.decode() + json.dumps(headers) + request.full_url
    assert "someone" not in everything and "multi_agent_debate" not in everything and "validate" not in everything
    err = capsys.readouterr().err
    assert "To report it: teamrunner report" in err
    assert "A crash report was sent" in err and "TEAMRUNNER_NO_CRASH_REPORTS=1 stops this" in err


def test_the_switch_sends_nothing_and_the_message_does_not_claim_a_report(reports, monkeypatch, capsys):
    monkeypatch.setenv(crash.OFF, "1")
    assert crashing(monkeypatch) == 3
    assert reports == []
    err = capsys.readouterr().err
    assert "To report it: teamrunner report" in err and "crash report was sent" not in err


def test_an_ordinary_mistake_sends_nothing(reports, tmp_path, capsys):
    assert main(["validate", str(tmp_path / "no-such-team")]) != 0
    assert reports == []


def test_at_most_five_reports_a_day(reports, monkeypatch, capsys):
    for _ in range(crash.DAILY + 3):
        crashing(monkeypatch)
    assert len(reports) == crash.DAILY
    monkeypatch.setattr(version_check, "today", lambda: "2099-01-01")
    crashing(monkeypatch)
    assert len(reports) == crash.DAILY + 1


def test_a_report_that_cannot_be_sent_is_silent_and_quick(monkeypatch, home, capsys):
    monkeypatch.setattr(crash, "post", real_post)
    monkeypatch.delenv(crash.OFF, raising=False)
    monkeypatch.setattr(crash, "TIMEOUT", 0.2)

    def hangs(request, timeout=None):
        time.sleep(5)

    monkeypatch.setattr(urllib.request, "urlopen", hangs)
    began = time.monotonic()
    assert crashing(monkeypatch) == 3
    assert time.monotonic() - began < 2
    err = capsys.readouterr().err
    assert "crash report was sent" not in err and "Traceback" not in err

    def refuses(request, timeout=None):
        raise OSError("no network")

    monkeypatch.setattr(urllib.request, "urlopen", refuses)
    assert crashing(monkeypatch) == 3
    assert "crash report was sent" not in capsys.readouterr().err


def test_a_type_defined_outside_python_and_packages_is_sent_as_other():
    class TheirSecretProject(Exception):
        pass

    TheirSecretProject.__module__ = "their_tools"
    assert crash.kind(TheirSecretProject()) == "other"
    assert crash.kind(KeyError("x")) == "KeyError"
    assert crash.kind(json.JSONDecodeError("m", "d", 0)) == "json.decoder.JSONDecodeError"
    from teamrunner.new import NewError
    assert crash.kind(NewError("x")) == "teamrunner.new.NewError"


def test_the_place_is_teamrunners_own_innermost_line_or_outside():
    try:
        json.loads("{")
    except ValueError as error:
        assert crash.fields(error) == {"type": "json.decoder.JSONDecodeError", "file": "outside", "line": 0}
    try:
        version_check.parse_version.__wrapped__  # an AttributeError raised here, in the tests
    except AttributeError as error:
        assert crash.place(error.__traceback__) == ("outside", 0)
    try:
        crash.what(None)
    except TypeError as error:
        file, line = crash.place(error.__traceback__)
        assert file == "crash.py" and line > 0


def test_report_opens_a_filled_in_issue_and_posts_nothing(reports, monkeypatch, capsys, home):
    import webbrowser
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)
    crashing(monkeypatch)
    sent_by_the_crash = len(reports)
    capsys.readouterr()
    assert main(["report"]) == 0
    assert len(reports) == sent_by_the_crash  # the command itself sends nothing
    out = capsys.readouterr().out
    assert "Nothing is posted until you press Submit" in out and "KeyError at cli.py line" in out
    assert str(home / "last-error.txt") in out
    query = parse_qs(urlsplit(opened[0]).query)
    assert opened[0].startswith("https://github.com/teamrunner/teamrunner/issues/new?")
    assert query["template"] == ["bug_report.yml"] and query["version"] == [f"teamrunner {__version__}"]
    assert "KeyError at cli.py line" in query["what"][0]
    assert "someone" not in opened[0]  # the error's message and the person's paths stay out
    assert "someone" in (home / "last-error.txt").read_text(encoding="utf-8")  # the details stay here


def test_report_with_no_crash_is_a_plain_bug_report(monkeypatch, capsys, home):
    assert main(["report", "--no-browser"]) == 0
    out = capsys.readouterr().out
    assert "https://github.com/teamrunner/teamrunner/issues/new?template=bug_report.yml" in out
    assert "what=" not in out


# ---------------------------------------------------------------- the browser page

def test_the_page_has_the_report_button():
    from teamrunner.ui.server import STATIC
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="crash-bar"' in page and ">Report this problem</button>" in page
    script = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "if (result.crash) showCrash(result.crash);" in script
    assert "showCrash(state.crash" in (STATIC / "run.js").read_text(encoding="utf-8")
    assert "showCrash(job.crash" in (STATIC / "experiments.js").read_text(encoding="utf-8")


def test_a_request_that_crashes_answers_with_what_the_button_needs(reports, tmp_path, monkeypatch, home):
    from teamrunner.ui import api

    def broken_save(*args):
        raise ZeroDivisionError("with /Users/someone in it")

    monkeypatch.setattr(api, "save_file", broken_save)
    with running(tmp_path) as server:
        status, answer = post(server, "/api/save", {"path": "a.md", "text": "x", "revision": ""})
    assert status == 500 and answer["error"] == "TeamRunner hit a problem it did not expect."
    shown = answer["crash"]
    assert shown["what"].startswith("ZeroDivisionError at ") or shown["what"] == "ZeroDivisionError outside TeamRunner's code"
    assert shown["report"].startswith("https://github.com/teamrunner/teamrunner/issues/new?")
    assert shown["sent"] is True and shown["details"] == str(home / "last-error.txt")
    assert "someone" not in json.dumps(shown)
    assert len(reports) == 1 and json.loads(reports[0].data)["type"] == "ZeroDivisionError"


def test_a_key_being_saved_never_reaches_the_kept_details(reports, tmp_path, monkeypatch, home):
    from teamrunner.ui import api

    def broken_key(name, value):
        raise ZeroDivisionError(f"could not keep {value}")

    monkeypatch.setattr(api, "set_key", broken_key)
    with running(tmp_path) as server:
        status, answer = post(server, "/api/key", {"name": "OPENAI_API_KEY", "value": "sk-typed-into-the-page"})
    assert status == 500 and "crash" in answer
    assert "sk-typed-into-the-page" not in (home / "last-error.txt").read_text(encoding="utf-8")


def test_a_refused_request_is_not_a_crash(reports, tmp_path):
    with running(tmp_path) as server:
        status, answer = post(server, "/api/save", {"path": "../outside.md", "text": "x", "revision": ""})
        assert status in (400, 403) and "crash" not in answer
        assert get(server, "/api/run-setup?path=nowhere")[0] in (400, 404)
    assert reports == []


def test_a_run_that_crashes_carries_the_report_in_its_status(reports, tmp_path, home):
    from teamrunner.new import copy_from
    copy_from(EXAMPLES / "round_robin", tmp_path / "debate")

    def broken_runner(folder, options, **seams):
        raise ZeroDivisionError("in the engine")

    with running(tmp_path) as server:
        server.jobs.runner = broken_runner
        status, job = post(server, "/api/start", {"path": "debate", "rehearse": True})
        assert status == 200
        server.jobs.get(job["id"]).done.wait(10)
        state = server.jobs.status(job["id"])
    assert state["state"] == "failed" and state["crash"]["report"].startswith("https://github.com/")
    assert len(reports) == 1


# --- review 2026-11-21, NS8 ---------------------------------------------------------------------

def test_the_kept_details_hide_a_key_that_is_only_in_the_shell_and_are_the_owners_alone(reports, monkeypatch, home):
    import os
    monkeypatch.setenv("OPENAI_API_KEY", "sk-only-in-the-shell-1234")
    monkeypatch.setenv("MY_SERVICE_TOKEN", "tok-also-secret-5678")
    monkeypatch.setenv("TEAMRUNNER_DEBUG", "1")  # short and not a key by its name: left alone
    try:
        raise RuntimeError("the provider said: bad key sk-only-in-the-shell-1234")
    except RuntimeError as error:
        found = crash.record(error, "run debate --message tok-also-secret-5678")
    text = found["kept"].read_text(encoding="utf-8")
    assert "sk-only-in-the-shell-1234" not in text and "tok-also-secret-5678" not in text
    assert "run debate --message" in text and "RuntimeError" in text
    if os.name != "nt":
        assert found["kept"].stat().st_mode & 0o077 == 0 and crash.last_path().stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("value, sends", [("1", False), ("on", False), ("true", False), ("off", False),
                                          ("yes", False), ("0", True), ("no", True), ("", True)])
def test_any_setting_of_the_switch_but_a_plain_no_sends_nothing(reports, monkeypatch, capsys, value, sends):
    monkeypatch.setenv(crash.OFF, value)
    assert crashing(monkeypatch) == 3
    assert bool(reports) is sends


def test_a_link_that_leads_nowhere_is_not_a_crash(tmp_path):
    """A broken link in a team's data folder stopped `experiment --plan` as a fault of
    TeamRunner's own, and sent a crash report. `validate` names the link; the copy leaves it out."""
    from teamrunner.experiment import build, read_experiment
    team = tmp_path / "t"
    (team / "data").mkdir(parents=True)
    (team / "team.yaml").write_text("name: t\ndefault_model: openai/gpt-5\n", encoding="utf-8")
    (team / "a.md").write_text("---\ndescription: a\n---\nhi\n", encoding="utf-8")
    try:
        (team / "data" / "gone.txt").symlink_to(tmp_path / "nowhere.txt")
    except OSError:
        pytest.skip("this system does not let the test make a link")
    exp = tmp_path / "e"
    exp.mkdir()
    (exp / "experiment.md").write_text("---\nname: e\nconditions:\n  a: ../t\n  b:\n    from: ../t\n    change:\n"
                                       "      a.model: openai/gpt-5-mini\nreplications: 2\nseed: 1\n---\n", encoding="utf-8")
    e = build(read_experiment(exp))
    assert e.ok and (exp / "conditions" / "b" / "a.md").is_file()
    assert not (exp / "conditions" / "b" / "data" / "gone.txt").exists()
