# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""The toolkeepers' desk: every request to a toolkeeper passes through here.

A toolkeeper decides by its `access:` table (D95), on the asker's terms
(D77). Limits are counted per member, like a library card (D86): this
turn's calls, and this run's. Each request is one `tool_call` event,
written when it is over, attributed to the toolkeeper (D103).
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field

from . import ir
from .builtin_tools import TOOLS, Answer, Context, Refused
from .events import Log
from .prices import tool_price
from .rehearsal import pretend_tool

DEFAULT_TIMEOUT = 60  # seconds, when a toolkeeper writes no `timeout`


@dataclass
class Allowance:
    """One member's use of one toolkeeper within one turn."""
    used: int = 0


@dataclass
class TurnCard:
    """The calls a member has made in the turn it is taking."""
    member: str
    by_toolkeeper: dict[str, Allowance] = field(default_factory=dict)


class Desk:
    def __init__(self, team: ir.Team, log: Log, ctx: Context, who: callable):
        self.team = team
        self.log = log
        self.ctx = ctx
        self.who = who  # member -> its component_id and instance_path
        self.run_use: dict[tuple[str, str], int] = {}  # (toolkeeper, member) -> calls this run

    def open_to(self, member: str) -> list[ir.Member]:
        """Every toolkeeper that would let this member in (D76)."""
        return [m for m in self.team.members.values()
                if m.kind == "toolkeeper" and m.terms_for(member) is not None]

    def tool_definition(self, toolkeeper: ir.Member) -> dict:
        """How a toolkeeper appears to a member's model: a callable tool,
        described by its sign on the door (D76)."""
        tool = TOOLS[toolkeeper.tool]
        sign = toolkeeper.body.strip()
        return {"type": "function", "function": {
            "name": toolkeeper.name,
            "description": (sign + "\n\n" if sign else "") + tool.summary,
            "parameters": tool.arguments,
        }}

    async def ask(self, toolkeeper: ir.Member, asker: str, arguments, card: TurnCard,
                  mandatory: bool) -> tuple[str, int]:
        """Handle one request. Returns what the asker is told, and the event's seq."""
        terms = toolkeeper.terms_for(asker)
        started = time.monotonic()
        allowance = card.by_toolkeeper.setdefault(toolkeeper.name, Allowance())
        event = {"event": "tool_call", **self.who(toolkeeper), "from": asker,
                 "toolkeeper": toolkeeper.name, "mandatory": mandatory, "tool": toolkeeper.tool,
                 "arguments": arguments}

        def refuse(reason: str) -> tuple[str, int]:
            seq = self.log.emit({**event, "access": "refused", "reason": reason,
                                 "allowance": allow, "result": None,
                                 "latency_ms": int((time.monotonic() - started) * 1000)})
            return f"Refused: {reason}.", seq

        if terms is None:
            allow = None
            listed = asker in toolkeeper.access
            return refuse("deny" if listed else f"{asker} is not on {toolkeeper.name}'s list")
        guardrails = terms.guardrails
        per_turn = guardrails.get("calls_per_turn", ir.CALLS_PER_TURN)
        allow = {"used": allowance.used, "of": per_turn}
        if not isinstance(arguments, dict):
            return refuse("the request was not in the form the tool takes")
        if allowance.used >= per_turn:
            return refuse("limit reached: " + f"{per_turn} call{'s' if per_turn != 1 else ''} a turn")
        per_run = guardrails.get("calls_per_run")
        used_run = self.run_use.get((toolkeeper.name, asker), 0)
        if per_run is not None and used_run >= per_run:
            return refuse(f"limit reached: {per_run} call{'s' if per_run != 1 else ''} a run")

        allowance.used += 1
        self.run_use[(toolkeeper.name, asker)] = used_run + 1
        allow = {"used": allowance.used, "of": per_turn}
        if per_run is not None:
            allow["run"] = {"used": used_run + 1, "of": per_run}
        timeout = guardrails.get("timeout", DEFAULT_TIMEOUT)
        try:
            answer: Answer = await asyncio.wait_for(
                (pretend_tool if self.ctx.rehearsal else TOOLS[toolkeeper.tool].run)(
                    arguments, guardrails, self.ctx), timeout)
        except Refused as e:
            return refuse(str(e))
        except asyncio.TimeoutError:
            return refuse(f"it took longer than {timeout} seconds")
        except Exception as e:  # a tool's own failure is told to the asker, not a crash
            seq = self.log.emit({**event, "access": "allowed", "allowance": allow, "result": None,
                                 "error": f"{type(e).__name__}: {e}", "timeout_s": timeout,
                                 "latency_ms": int((time.monotonic() - started) * 1000)})
            return f"The tool failed: {e}", seq
        cost, source = answer.cost_usd, None
        if answer.cost_usd and self.ctx.prices is not None:  # a billed call: priced as D120 says
            price = tool_price(toolkeeper.tool, answer.cost_usd, self.ctx.prices)
            cost, source = price["per_call"], price["source"]
        kept = {} if answer.logged is None else {"result_kept": False}
        seq = self.log.emit({**event, "access": "allowed", "allowance": allow,
                             "result": answer.text if answer.logged is None else answer.logged, **kept,
                             "cost_usd": cost, "cost_source": source,
                             "service": answer.service, "timeout_s": timeout,
                             "latency_ms": int((time.monotonic() - started) * 1000)})
        return answer.text, seq


def parse_arguments(text: str):
    try:
        return json.loads(text or "{}")
    except ValueError:
        return text
