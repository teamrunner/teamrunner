# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""The daily version check (D148, D149): client and gateway."""
import io
import json
import socket
import threading
import time
import urllib.error
import urllib.request

import httpx
import pytest

from teamrunner import __version__, version_check
from teamrunner.cli import main
from teamrunner.version_check import fetch as real_fetch
from test_demo import answer, gateway, request, server  # noqa: F401 (server is a fixture)

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
    assert "99.0 or later" in said and "pip install -U teamrunner" in said
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
    assert "pip install -U teamrunner" in said


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


# The gateway


@pytest.fixture
def version_server(tmp_path):
    (tmp_path / "version.json").write_text('{"min_version": "0.0.6", "min_secure_version": "0.0.5", "message": ""}')
    config = gateway.Config(key="test", db=str(tmp_path / "q.db"), version_domain="version.test",
                            version_file=str(tmp_path / "version.json"),
                            version_log=str(tmp_path / "checks.jsonl"))
    app = gateway.Gateway(("127.0.0.1", 0), config, answer)
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    yield app, f"http://127.0.0.1:{app.server_port}", tmp_path / "checks.jsonl"
    app.shutdown()
    app.server_close()
    thread.join()


ID = "3f2b8c1e-4d5a-4b6c-8d7e-9f0a1b2c3d4e"
UA = "TeamRunner/0.0.6 (python 3.12; darwin)"


def get(base, path="/version.json", host="version.test", **headers):
    return httpx.get(base + path, headers={"Host": host, **headers}, trust_env=False)


def test_gateway_serves_the_file_and_logs_one_valid_line(version_server):
    app, base, log = version_server
    r = get(base, **{"User-Agent": UA, "X-TeamRunner-Install": ID, "X-Forwarded-For": "203.0.113.9"})
    assert r.status_code == 200 and r.json()["min_secure_version"] == "0.0.5"
    [line] = [json.loads(s) for s in log.read_text().splitlines()]
    assert set(line) == {"time", "version", "python", "platform", "install"}
    assert line["version"] == "0.0.6" and line["python"] == "3.12" and line["platform"] == "darwin"
    assert line["install"] == ID and line["time"].endswith("Z")
    assert "203.0.113" not in log.read_text() and "127.0.0.1" not in log.read_text()


@pytest.mark.parametrize("headers", [
    {},
    {"User-Agent": UA},
    {"X-TeamRunner-Install": ID},
    {"User-Agent": "curl/8.0", "X-TeamRunner-Install": ID},
    {"User-Agent": UA, "X-TeamRunner-Install": "not-a-uuid"},
    {"User-Agent": UA, "X-TeamRunner-Install": ID.upper()},
    {"User-Agent": 'TeamRunner/0.0.6 (python 3.12; darwin") {"x": 1}', "X-TeamRunner-Install": ID},
    {"User-Agent": "TeamRunner/0.0.6 (python 3.12; " + "x" * 200 + ")", "X-TeamRunner-Install": ID},
])
def test_gateway_drops_malformed_headers_but_still_serves(version_server, headers):
    app, base, log = version_server
    assert get(base, **headers).status_code == 200
    assert not log.exists() or log.read_text() == ""


def test_gateway_version_host_serves_nothing_else(version_server):
    app, base, _ = version_server
    assert get(base, "/v1/models").status_code == 404
    assert httpx.post(base + "/v1/chat/completions", headers={"Host": "version.test"},
                      json={}, trust_env=False).status_code == 404
    assert get(base, host="demollm.test").status_code == 404  # the demo host does not serve the file


def test_the_log_line_never_holds_an_ip():
    line = gateway.version_check_line(UA, ID, now=0)
    assert line == {"time": "1970-01-01T00:00:00Z", "version": "0.0.6", "python": "3.12",
                    "platform": "darwin", "install": ID}


def test_a_real_client_request_is_logged(version_server, monkeypatch):
    app, base, log = version_server
    monkeypatch.setattr(version_check, "fetch", real_fetch)
    monkeypatch.setattr(version_check, "URL", base + "/version.json")
    real_request = urllib.request.Request

    def with_host(url, headers=None, **kw):
        return real_request(url, headers={**(headers or {}), "Host": "version.test"}, **kw)
    monkeypatch.setattr(urllib.request, "Request", with_host)
    monkeypatch.setattr(urllib.request, "urlopen",
                        urllib.request.build_opener(urllib.request.ProxyHandler({})).open)
    assert version_check.fetch() == {"min_version": "0.0.6", "min_secure_version": "0.0.5", "message": ""}
    [line] = [json.loads(s) for s in log.read_text().splitlines()]
    assert line["install"] == version_check.install_id() and line["version"] == __version__


# The demo's own floor (D148 (11), D149)


def test_demo_answers_every_version_while_its_floor_is_zero(server):
    _, base = server
    assert request(base).status_code == 200  # no User-Agent of TeamRunner's at all


def test_demo_refuses_versions_below_its_floor(server):
    app, base = server
    app.config.min_version_for_demo = "0.0.6"
    def ask(ua):
        return httpx.post(base + "/chat/completions", headers={"User-Agent": ua}, trust_env=False,
                          json={"model": "demoLLM", "messages": [{"role": "user", "content": "Hi"}]})
    old = ask("TeamRunner/0.0.5.1 (python 3.12; darwin)")
    assert old.status_code == 426 and "pip install -U teamrunner" in old.json()["error"]["message"]
    assert ask("curl/8").status_code == 426
    assert ask("TeamRunner/0.0.6 (python 3.12; linux)").status_code == 200
    assert ask("TeamRunner/0.1 (python 3.13; win32)").status_code == 200
