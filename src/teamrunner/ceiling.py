# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""An experiment's hard ceiling (D138 (6)): `max_cost_usd`, never passed.

Before every model call, the most it can cost — its prompt, counted by
LiteLLM's token counter, plus `max_tokens` of reply, at list prices (D120)
— is set aside from what is left. A call that does not fit is never sent:
`CeilingReached` ends that run `stopped`. After the reply, the reservation
is replaced by the call's real cost at list prices.

One `Ceiling` is shared by every run of a session, across threads; it starts
from what the experiment's earlier runs already spent.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass

from .model import CeilingReached, Model, Reply
from .prices import Prices, model_price


class NoWorstCase(Exception):
    """A model's worst case cannot be priced, so a hard ceiling cannot be kept."""


def max_reply(model: str, params: dict) -> int | None:
    """The longest reply a call may get: its `max_tokens`, else the model's own limit."""
    given = params.get("max_tokens")
    if isinstance(given, int) and not isinstance(given, bool) and given > 0:
        return given
    try:
        import litellm
        info = litellm.get_model_info(model)
        return info.get("max_output_tokens") or info.get("max_tokens")
    except Exception:
        return None


def prompt_tokens(model: str, messages: list[dict], schema: dict | None, tools: list[dict] | None) -> int:
    import litellm
    try:
        n = litellm.token_counter(model=model, messages=messages, tools=tools)
    except Exception:  # an unknown tokenizer: about four characters to a token
        n = len(json.dumps(messages, ensure_ascii=False)) // 4 + (len(json.dumps(tools)) // 4 if tools else 0)
    if schema:
        n += len(json.dumps(schema)) // 4
    return n


def worst_case(model: str, params: dict, messages, schema, tools, prices: Prices) -> float:
    price = model_price(model, prices)
    if price["input_per_million"] is None:
        raise NoWorstCase(f"{model} has no list price; add it to {prices.shown}")
    reply = max_reply(model, params)
    if reply is None:
        raise NoWorstCase(f"{model} has no known longest reply; give its members `params: {{max_tokens: …}}`")
    return (prompt_tokens(model, messages, schema, tools) * price["input_per_million"]
            + reply * price["output_per_million"]) / 1e6


@dataclass
class Ceiling:
    limit: float | None  # None: no ceiling, nothing is set aside
    spent: float = 0.0  # every earlier session's runs, then this one's calls as they come back
    reserved: float = 0.0
    refused: int = 0

    def __post_init__(self):
        self._lock = threading.Lock()

    @property
    def left(self) -> float | None:
        return None if self.limit is None else self.limit - self.spent - self.reserved

    def reserve(self, amount: float) -> bool:
        with self._lock:
            if self.limit is not None and self.spent + self.reserved + amount > self.limit + 1e-12:
                self.refused += 1
                return False
            self.reserved += amount
            return True

    def settle(self, reserved: float, actual: float) -> None:
        with self._lock:
            self.reserved -= reserved
            self.spent += actual


class Guarded:
    """Wraps a run's model so every call is set aside first. Never used in a rehearsal,
    which checks the wiring, not the budget."""

    def __init__(self, inner: Model, ceiling: Ceiling, prices: Prices):
        self.inner, self.ceiling, self.prices = inner, ceiling, prices

    async def complete(self, model, params, messages, schema, tools=None) -> Reply:
        if self.ceiling.limit is None:
            return await self.inner.complete(model, params, messages, schema, tools)
        try:
            worst = worst_case(model, params, messages, schema, tools, self.prices)
        except NoWorstCase as e:  # an experiment checks this before its first run; a single run stops here
            raise CeilingReached(f"Stopped: the ceiling cannot be kept, so no call was sent: {e}.")
        if not self.ceiling.reserve(worst):
            raise CeilingReached(
                f"Stopped at the ceiling: this call could cost up to ${worst:.4f}, and only "
                f"${max(self.ceiling.left, 0):.4f} of max_cost_usd (${self.ceiling.limit:,.4g}) is left; "
                "it was not sent.")
        try:
            reply = await self.inner.complete(model, params, messages, schema, tools)
        except BaseException:
            self.ceiling.settle(worst, 0.0)  # an unanswered call is not charged
            raise
        actual = reply.cost_usd if reply.cost_usd is not None else worst  # unknown: keep the worst case
        self.ceiling.settle(worst, actual)
        return reply
