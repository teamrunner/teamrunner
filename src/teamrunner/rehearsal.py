"""`teamrunner run --rehearse` (D115): a run with no model.

Every member answers with placeholder words, and each typed answer is
picked at random from what its file allows, so decisions, loops and `*N`
counts really happen. Requests to toolkeepers are pretend: the desk still
checks access and limits, but no tool runs. Seeded, and the seed is logged.
"""

from __future__ import annotations

import json
import random
import re

from .builtin_tools import Answer
from .model import Reply, ToolCall

SIDE_TRIP_CHANCE = 0.3
MUST_ASK = re.compile(r"End your turn with a request to (\w+)\.$")


class RehearsalModel:
    def __init__(self, seed: int):
        self.random = random.Random(seed)
        self.turn = 0

    async def complete(self, model, params, messages, schema, tools=None):
        last = messages[-1]
        offered = [t["function"] for t in tools or []]
        if last["role"] != "tool":
            self.turn += 1
        must = MUST_ASK.search(last["content"] or "") if last["role"] == "user" else None
        if must and offered:
            return self._ask(next((t for t in offered if t["name"] == must[1]), offered[0]))
        if offered and last["role"] != "tool" and self.random.random() < SIDE_TRIP_CHANCE:
            return self._ask(self.random.choice(offered))
        words = f"[rehearsal] placeholder words, turn {self.turn}."
        if schema:
            fields = schema["properties"]["output"]["properties"]
            return self._reply(json.dumps({"said": words,
                                           "output": {k: self._value(v) for k, v in fields.items()}}))
        return self._reply(words)

    def _value(self, spec: dict):
        if "enum" in spec:
            return self.random.choice(spec["enum"])
        return {"number": self.random.randint(0, 10), "boolean": self.random.choice([True, False])
                }.get(spec["type"], "[rehearsal]")

    def _ask(self, tool: dict) -> Reply:
        props = tool["parameters"].get("properties", {})
        arguments = {k: f"[rehearsal] {k}" for k in props}
        call = ToolCall(f"rehearsal_{self.turn}_{self.random.randrange(10**6)}", tool["name"],
                        json.dumps(arguments))
        return Reply(f"[rehearsal] asking {tool['name']}.", "rehearsal", tool_calls=[call],
                     cost_usd=0.0)

    def _reply(self, text: str) -> Reply:
        return Reply(text, "rehearsal", cost_usd=0.0)


async def pretend_tool(args: dict, guardrails: dict, ctx) -> Answer:
    return Answer("[rehearsal] what the tool would have returned.", service="rehearsal")
