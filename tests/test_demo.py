"""Demo client/gateway integration and the public gateway's spending boundaries."""
import asyncio
import importlib.util
import json
import sys
import threading
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

from teamrunner.demo import api_base, complete
from teamrunner.keys import extra_settings
from teamrunner.model import TransientError
from teamrunner.new import EXAMPLES, copy_from
from teamrunner.run import Options, run_team
from teamrunner.validate import validate

spec = importlib.util.spec_from_file_location("demo_gateway", Path(__file__).parents[1] /
                                            "deploy/demo-gateway/gateway.py")
gateway = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = gateway
spec.loader.exec_module(gateway)


def answer(body, config):
    text = json.dumps({"said": "The supporting argument wins.",
                       "output": {"winner": "for", "reasoning": "Clearer reasons."}}) \
        if "response_format" in body else "A short argument."
    return {"model": config.model, "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 40, "completion_tokens": 20, "total_tokens": 60}}


@pytest.fixture
def server(tmp_path):
    config = gateway.Config(key="sk-secret-test", db=str(tmp_path / "quota.db"))
    app = gateway.Gateway(("127.0.0.1", 0), config, answer)
    app.pause = lambda delay: None
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    yield app, f"http://127.0.0.1:{app.server_port}/v1"
    app.shutdown()
    app.server_close()
    thread.join()


def request(base, **updates):
    return httpx.post(base + "/chat/completions", json={"model": "demoLLM",
        "messages": [{"role": "user", "content": "Hello"}], **updates}, trust_env=False)


@pytest.mark.parametrize("transient", [False, True])
def test_installed_example_runs_without_a_key_and_records_actual_model(server, tmp_path, monkeypatch, transient):
    app, base = server
    if transient:
        attempts = []
        def sometimes(body, config):
            attempts.append(True)
            if len(attempts) == 1:
                raise urllib.error.HTTPError("https://provider", 429, "busy", {}, None)
            return answer(body, config)
        app.call = sometimes
    monkeypatch.setenv("TEAMRUNNER_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TEAMRUNNER_DEMO_API_BASE", base)
    team, _ = copy_from(EXAMPLES / "demo", tmp_path / "debate")
    result = run_team(team, Options(quiet=True), waits=(0,))
    assert result.status == "completed", result.reason
    events = [json.loads(s) for s in (result.folder / "events.jsonl").read_text().splitlines()]
    turns = [e for e in events if e["event"] == "agent_message"]
    assert len(turns) == 3
    assert "openrouter/qwen/qwen3.7-flash" in json.dumps(turns)
    record = json.loads((result.folder / "record.json").read_text())
    assert record["keys"] == {} and record["cost_usd"] == 0
    assert "sk-secret" not in json.dumps(events)
    assert not [e for e in events if e["event"] == "retry"]
    assert not [e for e in events if e["event"] == "warning" and "free demo sends" in str(e)]


def test_demo_notice_is_once_per_validation_not_per_run(server, tmp_path, monkeypatch):
    from teamrunner.checks import online_check
    _, base = server
    monkeypatch.setenv("TEAMRUNNER_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TEAMRUNNER_DEMO_API_BASE", base)
    team, _ = copy_from(EXAMPLES / "demo", tmp_path / "debate")
    check, _ = online_check(validate(team), {"teamrunner": lambda key, settings: {"demoLLM"}})
    assert len([w for w in check.warnings if "free demo sends" in w]) == 1


def test_server_retries_respect_quotas_and_provider_retry_after(server):
    app, base = server
    calls, delays = [], []
    app.pause = delays.append
    def busy(body, config):
        calls.append(True)
        raise urllib.error.HTTPError("https://provider", 429, "busy", {"Retry-After": "2"}, None)
    app.call = busy
    response = request(base)
    assert response.status_code == 503 and "temporarily busy" in response.text
    assert len(calls) == 3 and delays == [2, 2]
    app.config.ip_minute = 3
    assert request(base).status_code == 429  # all three upstream attempts counted


def test_gateway_does_not_retry_earlier_than_long_provider_pause(server):
    app, base = server
    delays = []
    app.pause = delays.append
    def busy(body, config):
        raise urllib.error.HTTPError("https://provider", 429, "busy", {"Retry-After": "60"}, None)
    app.call = busy
    assert request(base).status_code == 503
    assert delays == []


def test_only_demo_alias_is_accepted(tmp_path):
    team, _ = copy_from(EXAMPLES / "demo", tmp_path / "debate")
    path = team / "team.yaml"
    path.write_text(path.read_text().replace("teamrunner/demoLLM", "teamrunner/expensive"))
    assert not validate(team).ok


def test_endpoint_configuration(tmp_path, monkeypatch):
    path = tmp_path / "keys"
    path.write_text("TEAMRUNNER_DEMO_API_BASE=https://demo.example/v1\n")
    assert extra_settings(path)["TEAMRUNNER_DEMO_API_BASE"] == "https://demo.example/v1"
    monkeypatch.setenv("TEAMRUNNER_DEMO_API_BASE", "http://localhost:8080/v1")
    assert api_base(extra_settings(path)) == "http://localhost:8080/v1"
    for bad in ("http://public.example/v1", "https://user:secret@example.com", "https://example.com?key=x"):
        with pytest.raises(ValueError):
            api_base({"TEAMRUNNER_DEMO_API_BASE": bad})


def test_clients_cannot_select_models_tools_or_expensive_settings(server):
    app, base = server
    called = []
    app.call = lambda body, config: called.append(body) or answer(body, config)
    for override in ({"model": "paid/expensive"}, {"tools": []}, {"stream": True},
                     {"max_tokens": -1}, {"temperature": "high"},
                     {"messages": [{"role": "user", "content": "x" * 17000}]}):
        assert request(base, **override).status_code == 400
    assert called == []
    assert request(base, max_tokens=999999).status_code == 200
    assert called[0]["max_tokens"] == app.config.max_output
    assert called[0]["model"] == "qwen/qwen3.7-flash"
    assert called[0]["reasoning"] == {"enabled": False}


def test_quotas_survive_restart_and_global_limit_is_atomic(tmp_path):
    config = gateway.Config(key="test", db=str(tmp_path / "q.db"), global_day=3, ip_minute=1)
    quota = gateway.Quotas(config)
    now = 1800000000
    assert quota.take("one", now)
    assert not gateway.Quotas(config).take("one", now)
    with ThreadPoolExecutor(max_workers=10) as pool:
        taken = list(pool.map(lambda n: quota.take(str(n), now), range(20)))
    assert sum(taken) == 2
    assert not quota.take("new", now + 61)  # global daily quota still spent
    config.global_month = 3
    assert not quota.take("new", now + 86400)  # monthly cap survives daily reset


def test_forwarding_headers_and_ipv6_cannot_bypass_ip_limit():
    assert gateway.client_ip("198.51.100.2", "1.2.3.4") == "198.51.100.2"
    assert gateway.client_ip("127.0.0.1", "198.51.100.2") == "198.51.100.2"
    assert gateway.client_ip("127.0.0.1", "2001:db8::1") == gateway.client_ip("127.0.0.1", "2001:db8::2")


def test_qwen_json_mode_preserves_the_requested_schema_and_bounds_it():
    config = gateway.Config(key="test")
    data = {"model": "demoLLM", "messages": [{"role": "user", "content": "judge"}],
            "response_format": {"type": "json_schema", "json_schema": {
                "schema": {"type": "object", "properties": {"winner": {"enum": ["for", "against"]}}}}}}
    result = gateway.payload(data, config)
    assert result["response_format"] == {"type": "json_object"}
    assert '"winner"' in result["messages"][-1]["content"]
    assert len(data["messages"]) == 1
    config.structured_output = "json_schema"
    assert gateway.payload(data, config)["response_format"]["type"] == "json_schema"
    config.structured_output = "json_object"
    config.max_prompt_bytes = 100
    with pytest.raises(ValueError, match="schema are too long"):
        gateway.payload(data, config)


def test_failed_upstream_attempts_count_and_errors_hide_secrets(server):
    app, base = server
    app.config.ip_minute = 1
    def fail(body, config):
        raise urllib.error.HTTPError("https://provider", 401, "sk-secret-test", {}, None)
    app.call = fail
    response = request(base)
    assert response.status_code == 502 and "sk-secret" not in response.text
    response = request(base)
    assert response.status_code == 429 and response.headers["Retry-After"]
    with pytest.raises(TransientError, match="allowance"):
        asyncio.run(complete("teamrunner/demoLLM", {}, [{"role": "user", "content": "Hello"}],
                             None, None, {"TEAMRUNNER_DEMO_API_BASE": base}))


def test_pause_busy_and_body_limits(server):
    app, base = server
    app.config.enabled = False
    assert request(base).status_code == 503
    assert httpx.get(base + "/models").status_code == 503
    app.config.enabled = True
    for _ in range(app.config.concurrency):
        app.slots.acquire()
    assert request(base).status_code == 503
    for _ in range(app.config.concurrency):
        app.slots.release()
    response = httpx.post(base + "/chat/completions", content="x" * 70000)
    assert response.status_code == 413


def test_tools_are_rejected_before_a_network_call():
    with pytest.raises(ValueError, match="without toolkeepers"):
        asyncio.run(complete("teamrunner/demoLLM", {}, [], None, [{"tool": "web"}], {}))
