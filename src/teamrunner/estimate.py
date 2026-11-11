"""The first cost estimate for an experiment's runs (D138 (6)), made without a model.

The flow is walked as a rehearsal walks it (D115): every member takes its
turns, decisions are random among what each file allows, `*N` counts and
loops really happen. Each prompt is measured as it would be sent — the
member's instructions, the minutes so far, the output schema — at about
four characters to a token, and each reply is taken to be `REPLY_TOKENS`
long, or its `max_tokens` if that is shorter. Tokens times list prices (D120) give the cost. A few walks are
averaged, since decisions can make runs longer or shorter.

A second figure takes every reply at its longest — `max_tokens`, else the
model's own maximum from LiteLLM, as the hard ceiling sets aside before each
call: the most a run can cost if every member writes all it may.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

from . import ir
from .builtin_tools import Context
from .engine import Engine
from .events import Log
from .model import Reply
from .prices import Prices, model_price
from .rehearsal import MUST_ASK, RehearsalModel
from .turns import Members

REPLY_TOKENS = 400  # a typical turn's reply; replaced by real costs after round 1 (step 3)
CHARS_PER_TOKEN = 4
WALKS = 8


def tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


class Counter(RehearsalModel):
    """A rehearsal model that counts the tokens a real call would use."""

    def __init__(self, seed: int, reply_tokens: int | None):
        super().__init__(seed)
        self.reply_tokens = reply_tokens  # None: each call's longest reply
        self.usage: dict[str, list[int]] = {}  # model -> [input, output, calls]
        self.no_max: set[str] = set()

    async def complete(self, model, params, messages, schema, tools=None):
        given = params.get("max_tokens")
        given = given if isinstance(given, int) and not isinstance(given, bool) and given > 0 else None
        if self.reply_tokens is None:  # the most: every reply at its longest, as the ceiling takes it
            from .ceiling import max_reply
            size = given or max_reply(model, {})
            if size is None:  # no max_tokens, and LiteLLM does not know the model's own maximum
                self.no_max.add(model)
                size = REPLY_TOKENS
        else:  # typical: REPLY_TOKENS, but never more than the member may write
            size = min(self.reply_tokens, given) if given else self.reply_tokens
        prompt = tokens(json.dumps(messages, ensure_ascii=False)
                        + (json.dumps(schema) if schema else "")
                        + (json.dumps(tools) if tools else ""))
        last = messages[-1]
        must = MUST_ASK.search(last["content"] or "") if last["role"] == "user" else None
        if must and tools:  # a turn that must end with a request: answer as a rehearsal does
            reply = await super().complete(model, params, messages, schema, tools)
        else:
            if last["role"] != "tool":
                self.turn += 1
            words = "word " * (size * CHARS_PER_TOKEN // 5)
            if schema:
                fields = schema["properties"]["output"]["properties"]
                text = json.dumps({"said": words, "output": {k: self._value(v) for k, v in fields.items()}})
            else:
                text = words
            reply = Reply(text, "estimate", cost_usd=0.0)
        used = self.usage.setdefault(model, [0, 0, 0])
        used[0] += prompt
        used[1] += size
        used[2] += 1
        return reply


@dataclass
class Estimate:
    per_run_usd: float | None  # mean over the walks; None if a model has no known price
    tokens_in: int
    tokens_out: int
    calls: float
    unpriced: list[str] = field(default_factory=list)  # models with no known price
    no_max: list[str] = field(default_factory=list)  # no max_tokens and no known maximum (worst case only)


def walk(team: ir.Team, inputs: dict, seed: int, reply_tokens: int | None) -> Counter:
    counter = Counter(seed, reply_tokens)
    context = Context(Path("inputs"), Path("work"), {}, team.package_top, rehearsal=True, inputs=inputs)
    actor = Members(team, inputs, counter, Log(), context=context, waits=())
    engine = Engine(team, actor, lambda event: None)
    asyncio.run(engine.run())
    return counter


def estimate(team: ir.Team, inputs: dict, seeds: list[int], prices: Prices,
             reply_tokens: int | None = REPLY_TOKENS) -> Estimate:
    totals: dict[str, list[int]] = {}
    no_max: set[str] = set()
    for seed in seeds:
        counter = walk(team, inputs, seed, reply_tokens)
        no_max |= counter.no_max
        for model, (i, o, c) in counter.usage.items():
            t = totals.setdefault(model, [0, 0, 0])
            t[0] += i
            t[1] += o
            t[2] += c
    n = len(seeds)
    cost, unpriced = 0.0, []
    for model, (i, o, _) in totals.items():
        price = model_price(model, prices)
        if price["input_per_million"] is None:
            unpriced.append(model)
            continue
        cost += (i * price["input_per_million"] + o * price["output_per_million"]) / 1e6
    return Estimate(None if unpriced else cost / n,
                    sum(t[0] for t in totals.values()) // n, sum(t[1] for t in totals.values()) // n,
                    sum(t[2] for t in totals.values()) / n, sorted(unpriced), sorted(no_max))
