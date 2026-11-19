# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""The daily version check (D148, D149): the client."""
import io
import json
import socket
import time
import urllib.error
import urllib.request

import pytest

from teamrunner import __version__, version_check
from teamrunner.cli import main
from teamrunner.version_check import fetch as real_fetch

UUID4 = version_check._UUID4


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def lookup(monkeypatch):
    """The real lookup, with the network replaced: records each request, answers with `served`."""
    monkeypatch.setattr(version_check, "fetch", real_fetch)
    sent = []
    state = {"served": {"min_version": "0.0.0", "min_secure_version": "0.0.0", "message": ""}}

    def urlopen(req, timeout=None):
        sent.append(req)
        served = state["served"]
        if isinstance(served, BaseException):
            raise served
        if callable(served):
            return served()
        return Response(served if isinstance(served, bytes) else json.dumps(served).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.delenv("TEAMRUNNER_NO_VERSION_CHECK", raising=False)
    return sent, state


def run(**kw):
    err = io.StringIO()
    code = version_check.check(err=err, **kw)
    return code, err.getvalue()


def test_install_id_is_made_once_and_reused(lookup):
    sent, _ = lookup
    first = version_check.install_id()
    assert UUID4.fullmatch(first)
    assert version_check.install_id() == first
    assert version_check.install_id_path().parent == version_check.cache_path().parent
    run()
    assert sent[0].get_header("X-teamrunner-install") == first


def test_deleting_the_id_makes_a_new_one():
    first = version_check.install_id()
    version_check.install_id_path().unlink()
    assert version_check.install_id() != first


def test_the_request_is_a_plain_get_with_exactly_two_headers(lookup):
    sent, _ = lookup
    run()
    req = sent[0]
    assert req.get_method() == "GET" and req.data is None
    assert req.full_url == "https://version.teamrunner.ai/version.json"
    assert "?" not in req.full_url
    assert {name.lower() for name, _ in req.header_items()} == {"user-agent", "x-teamrunner-install"}
    python = f"{__import__('sys').version_info[0]}.{__import__('sys').version_info[1]}"
    assert req.get_header("User-agent") == (
        f"TeamRunner/{__version__} (python {python}; {__import__('sys').platform})")


def test_the_off_switch_hides_the_nudge_but_the_request_still_goes(lookup, monkeypatch):
    sent, state = lookup
    state["served"] = {"min_version": "99.0", "min_secure_version": "0.0.0", "message": "Please update."}
    monkeypatch.setenv("TEAMRUNNER_NO_VERSION_CHECK", "1")
    assert run() == (None, "")
    assert len(sent) == 1


def test_below_min_version_nudges_and_runs(lookup):
    _, state = lookup
    state["served"] = {"min_version": "99.0", "min_secure_version": "0.0.0", "message": "New \x1b[31mfeatures"}
    code, said = run()
    assert code is None
    assert "99.0 or later" in said and "teamrunner --update" in said
    assert "\x1b" not in said


def test_no_second_request_the_same_day(lookup):
    sent, _ = lookup
    run()
    run()
    assert len(sent) == 1


def test_a_new_day_asks_again(lookup, monkeypatch):
    sent, _ = lookup
    run()
    monkeypatch.setattr(version_check, "today", lambda: "2099-01-01")
    run()
    assert len(sent) == 2


def slow():
    time.sleep(5)
    return Response(b"{}")


def unwritable(monkeypatch):
    monkeypatch.setattr(version_check, "_home", lambda: version_check.Path("/dev/null/nope"))


FAILURES = {
    "offline": urllib.error.URLError("no network"),
    "dns": urllib.error.URLError(socket.gaierror("no such host")),
    "timeout": TimeoutError(),
    "slow": slow,
    "http error": urllib.error.HTTPError("https://x", 503, "down", {}, None),
    "bad json": b"{not json",
    "not an object": b"[1, 2]",
    "missing fields": json.dumps({"min_version": "0.1"}).encode(),
    "bad floor": json.dumps({"min_version": "x", "min_secure_version": "0.0.0"}).encode(),
    "unwritable cache": "unwritable",
}


@pytest.mark.parametrize("failure", FAILURES)
def test_every_failure_is_silent_and_the_run_proceeds(lookup, monkeypatch, capsys, failure):
    _, state = lookup
    monkeypatch.setattr(version_check, "TIMEOUT", 0.2)
    if FAILURES[failure] == "unwritable":
        unwritable(monkeypatch)
    else:
        state["served"] = FAILURES[failure]
    started = time.monotonic()
    assert run() == (None, "")
    assert time.monotonic() - started < 1
    assert capsys.readouterr() == ("", "")


def test_a_broken_cache_file_is_silent(lookup):
    version_check.cache_path().parent.mkdir(parents=True, exist_ok=True)
    version_check.cache_path().write_text("{oops")
    assert run() == (None, "")


def test_below_min_secure_version_refuses(lookup):
    _, state = lookup
    state["served"] = {"min_version": "0.0.0", "min_secure_version": "99.0", "message": "A key could leak."}
    code, said = run()
    assert code == 1
    assert "known security problem" in said and "A key could leak." in said
    assert "teamrunner --update" in said


def test_the_off_switch_does_not_turn_off_the_refusal(lookup, monkeypatch):
    _, state = lookup
    state["served"] = {"min_version": "0.0.0", "min_secure_version": "99.0"}
    monkeypatch.setenv("TEAMRUNNER_NO_VERSION_CHECK", "1")
    assert run()[0] == 1


def test_a_remembered_secure_floor_refuses_offline(lookup, monkeypatch):
    sent, state = lookup
    state["served"] = {"min_version": "0.0.0", "min_secure_version": "99.0", "message": "A key could leak."}
    assert run()[0] == 1
    state["served"] = urllib.error.URLError("offline")
    monkeypatch.setattr(version_check, "today", lambda: "2099-01-01")
    code, said = run()
    assert len(sent) == 2 and code == 1
    assert "known security problem" in said
    for word in ("check", "lookup", "offline", "reach", "network"):
        assert word not in said.lower()


def test_no_check_ever_succeeded_fails_open(lookup):
    _, state = lookup
    state["served"] = urllib.error.URLError("offline")
    assert run() == (None, "")


def test_the_cli_refuses_before_doing_anything(lookup, tmp_path, capsys):
    _, state = lookup
    state["served"] = {"min_version": "0.0.0", "min_secure_version": "99.0"}
    assert main(["new", str(tmp_path / "t")]) == 1
    assert not (tmp_path / "t").exists()
    assert "known security problem" in capsys.readouterr().err
