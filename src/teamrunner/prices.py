"""Prices (D118, D120): what a run's tokens and tool calls are counted at.

Providers report tokens, never prices, so every cost is an estimate: exact
counts times a list price. The price comes from, in order:

1. `~/.teamrunner/prices.yaml`, which the person edits (a negotiated rate,
   a new model, a changed list price);
2. for models, the price table of the installed LiteLLM (never downloaded:
   see `__init__`); for tools, TeamRunner's built-in price.

Every run records which price it used for what, and where it came from.

    # ~/.teamrunner/prices.yaml — US dollars
    models:                     # per million tokens
      openai/gpt-6-luna: {input: 0.10, output: 0.50}
    tools:                      # per call
      web_search_brave: 0.005
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.metadata import version
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from .keys import key_file


def prices_file() -> Path:
    return key_file().parent / "prices.yaml"


@dataclass
class Prices:
    models: dict[str, tuple[float, float]] = field(default_factory=dict)  # per million tokens: in, out
    tools: dict[str, float] = field(default_factory=dict)  # per call
    problems: list[str] = field(default_factory=list)
    path: Path | None = None

    @property
    def shown(self) -> str:
        path = self.path or prices_file()
        home = Path.home()
        return f"~/{path.relative_to(home).as_posix()}" if path.is_relative_to(home) else str(path)


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0


def load_prices(path: Path | None = None) -> Prices:
    path = path or prices_file()
    prices = Prices(path=path)
    if not path.is_file():
        return prices
    where = prices.shown
    try:
        data = YAML(typ="safe").load(path.read_text(encoding="utf-8")) or {}
    except YAMLError as e:
        prices.problems.append(f"{where} is not valid YAML: {e}")
        return prices
    if not isinstance(data, dict) or set(data) - {"models", "tools"}:
        prices.problems.append(f"{where} holds `models:` and `tools:`, and nothing else")
        return prices
    for part in ("models", "tools"):
        if not isinstance(data.get(part) or {}, dict):
            prices.problems.append(f"{where}: under `{part}:` write one `name: price` line each")
            return prices
    for model, price in (data.get("models") or {}).items():
        if (isinstance(price, dict) and set(price) == {"input", "output"}
                and _number(price["input"]) and _number(price["output"]) and "/" in str(model)):
            prices.models[str(model)] = (float(price["input"]), float(price["output"]))
        else:
            prices.problems.append(f"{where}: `{model}` should be `provider/name: {{input: 0.10, output: 0.50}}`, "
                                   "dollars per million tokens")
    for tool, price in (data.get("tools") or {}).items():
        if _number(price):
            prices.tools[str(tool)] = float(price)
        else:
            prices.problems.append(f"{where}: `{tool}` should be a price per call in dollars, such as 0.005")
    return prices


def litellm_version() -> str:
    try:
        return version("litellm")
    except Exception:
        return "?"


def model_price(model: str, prices: Prices) -> dict:
    """The price a model's tokens will be counted at, and where it comes from."""
    if model in prices.models:
        i, o = prices.models[model]
        return {"input_per_million": i, "output_per_million": o, "source": prices.shown}
    if model.startswith("ollama/"):
        return {"input_per_million": 0.0, "output_per_million": 0.0, "source": "a local model"}
    import litellm
    name = model.split("/", 1)[1]
    row = litellm.model_cost.get(model) or litellm.model_cost.get(name) or {}
    if "input_cost_per_token" not in row:
        return {"input_per_million": None, "output_per_million": None,
                "source": f"not in LiteLLM {litellm_version()}'s table; add it to {prices.shown}"}
    return {"input_per_million": round(row["input_cost_per_token"] * 1e6, 6),
            "output_per_million": round(row.get("output_cost_per_token", 0) * 1e6, 6),
            "source": f"LiteLLM {litellm_version()}'s price table"}


def tool_price(tool: str, built_in: float, prices: Prices) -> dict:
    if tool in prices.tools:
        return {"per_call": prices.tools[tool], "source": prices.shown}
    return {"per_call": built_in, "source": "TeamRunner's built-in price"}
