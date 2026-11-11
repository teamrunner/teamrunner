"""D120: where a run's prices come from, and that they are recorded as estimates."""

import asyncio
import json
import os

import pytest

from teamrunner.prices import load_prices, model_price, tool_price

from test_run import OPENAI, TEAM, VERDICT, go, home, read_run  # noqa: F401
from test_tools import LIBRARY, asks, brave, calls  # noqa: F401
from test_turns import NAMED, fits
from test_validate import debate


def test_litellm_never_downloads_its_price_table():
    import teamrunner  # noqa: F401
    assert os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] == "True"


def test_the_prices_file(tmp_path):
    path = tmp_path / "prices.yaml"
    path.write_text("models:\n  openai/gpt-6-luna: {input: 0.2, output: 1}\n"
                    "  bad_model: {input: 1}\ntools:\n  web_search_brave: 0.004\n  read_page: free\n", encoding="utf-8")
    prices = load_prices(path)
    assert prices.models == {"openai/gpt-6-luna": (0.2, 1.0)}
    assert prices.tools == {"web_search_brave": 0.004}
    assert len(prices.problems) == 2
    assert model_price("openai/gpt-6-luna", prices)["input_per_million"] == 0.2
    assert model_price("ollama/qwen3:8b", prices)["source"] == "a local model"
    table = model_price("openai/gpt-5.6-luna", prices)
    assert table["source"].startswith("LiteLLM ") and table["input_per_million"] == pytest.approx(0.2)
    assert tool_price("web_search_brave", 0.005, prices)["per_call"] == 0.004
    assert tool_price("web_search_brave", 0.005, load_prices(tmp_path / "none.yaml"))["source"] == \
        "TeamRunner's built-in price"


def test_a_bad_prices_file_stops_the_run_before_it_starts(tmp_path, home):
    (home / "prices.yaml").write_text("models: [oops]\n", encoding="utf-8")
    outcome, model, printed = go(debate(tmp_path, **TEAM))
    assert outcome.exit_code == 1 and model.calls == []
    assert "prices.yaml" in printed


def test_a_run_records_its_prices_and_says_they_are_estimates(tmp_path, home, brave):
    (home / ".env").write_text("OPENAI_API_KEY=sk-test\nBRAVE_API_KEY=brv-test\n", encoding="utf-8")
    (home / "prices.yaml").write_text("tools:\n  web_search_brave: 0.004\n", encoding="utf-8")
    web = LIBRARY.replace("read_page: {calls_per_turn: 2, timeout: 1}", "web_search_brave: {}")
    team = debate(tmp_path, **{**NAMED, "web.md": web, "chair.md": "for -> against -> judge\n"})
    from test_tools import go as tools_go
    outcome, _, events, printed = tools_go(team, {"for": [asks("web", query="q"), "Pro."],
                                                  "judge": [fits("Pro.", winner="for", reasoning="")]})
    assert outcome.status == "completed", outcome.reason
    [call] = calls(events)
    assert call["cost_usd"] == 0.004 and call["cost_source"].endswith("prices.yaml")
    run = json.loads((outcome.folder / "record.json").read_text(encoding="utf-8"))
    assert run["cost_is_estimate"] is True
    assert run["prices"]["tools"]["web_search_brave"]["per_call"] == 0.004
    assert "openai/gpt-5" in run["prices"]["models"]
    assert "≈ $" in printed


def test_a_prices_file_sets_a_models_price(tmp_path):
    import litellm
    from teamrunner.litellm_model import LiteLLMModel
    path = tmp_path / "prices.yaml"
    path.write_text("models:\n  openai/gpt-6-luna: {input: 1.0, output: 2.0}\n", encoding="utf-8")
    model = LiteLLMModel({"OPENAI_API_KEY": "sk-test"}, {}, load_prices(path))
    reply = asyncio.run(model.complete("openai/gpt-6-luna", {"mock_response": "ok"},
                                       [{"role": "user", "content": "hi"}], None))
    expected = (reply.usage["input"] * 1.0 + reply.usage["output"] * 2.0) / 1e6
    assert reply.cost_usd == pytest.approx(expected) and reply.cost_source.endswith("prices.yaml")
