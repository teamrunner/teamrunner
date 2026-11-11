# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""The flow engine: walks a team's flow and says whose turn it is (D110).

It knows the flow's rules and nothing else. Taking a turn — the prompt,
the model, the tools — is the `Actor`'s job; writing events is the sink's.
The rules it follows are written in `ir.py`'s docstring. Every routing
choice it makes is a `route_decision` event: no hidden if-statements (D53).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Protocol

from . import ir
from .ir import DONE, STOP
from .flowgraph import done_id, entry_id, step_id


@dataclass(frozen=True)
class Pass:
    """One level of where a turn sits: block `rounds`, pass 2 of 3."""
    block: str
    number: int
    of: int


@dataclass
class Turn:
    """Everything the actor needs to know about the turn it is asked to take."""
    step: ir.Step
    member: ir.Member
    passes: tuple[Pass, ...]  # the blocks it sits in, outermost first; empty in the main flow
    attempt: int  # 1, or more when a decider sent the flow back to this step
    attempts_of: int | None  # the counted bound that limits the tries, if any
    asker: "Result | None" = None  # for a toolkeeper step: the turn that must ask it
    must_ask: ir.Member | None = None  # the toolkeeper step right after this turn (D85)
    flow_node: str | None = None


@dataclass
class Result:
    """What came of a turn."""
    turn: Turn
    output: dict = field(default_factory=dict)  # typed fields (D45)
    request: object = None  # the request for the toolkeeper step that follows, if any
    card: object = None  # this turn's calls to toolkeepers, which that request counts in (D86)


class Actor(Protocol):
    async def __call__(self, turn: Turn) -> Result: ...


Sink = Callable[[dict], None]


class FlowError(Exception):
    """The actor returned something the flow cannot follow."""


@dataclass
class Outcome:
    status: str  # completed | stopped_by_flow
    turns: int


class Engine:
    def __init__(self, team: ir.Team, actor: Actor | Callable[[Turn], Awaitable[Result]], sink: Sink,
                 trace: Callable[[str], None] | None = None):
        self.team = team
        self.actor = actor
        self.emit = sink
        self.turns = 0
        self.trace = trace or (lambda node: None)

    async def run(self) -> Outcome:
        end = await self._block(self.team.flow.main, ())
        return Outcome("stopped_by_flow" if end == STOP else "completed", self.turns)

    async def _block(self, block: ir.Block, passes: tuple[Pass, ...]) -> str:
        """Run one pass of a block to its end. Returns DONE or STOP."""
        self.trace(entry_id(block))
        counts: dict[int, int] = {}  # times each decider has decided in this run of the block
        tries: dict[int, int] = {}  # the try a step is on: 1, plus each time a decider sent the flow back to it
        bound_into: dict[int, int] = {}  # step index -> the count limiting its tries
        last: Result | None = None
        i = 0
        while i < len(block.steps):
            step = block.steps[i]
            if step.kind in (DONE, STOP):
                self.trace(step_id(block,i))
                return step.kind

            for number in range(1, step.repeat + 1):
                self.trace(step_id(block,i))
                if step.kind == "block" or step.repeat > 1:
                    inner = passes + (Pass(step.name, number, step.repeat),)
                else:
                    inner = passes
                if step.kind == "block":
                    if await self._block(self.team.flow.blocks[step.name], inner) == STOP:
                        return STOP
                    last = None
                else:
                    member = self.team.members[step.name]
                    turn = Turn(step, member, inner, tries.get(i, 1), bound_into.get(i),
                                asker=last if member.kind == "toolkeeper" else None,
                                must_ask=self._toolkeeper_after(block, i), flow_node=step_id(block,i))
                    self.turns += 1
                    last = await self.actor(turn)

            decision = block.decisions.get(i)
            if decision is not None:
                counts[i] = counts.get(i, 0) + 1
                route = self._decide(block, i, decision, counts[i], last, passes)
                if route in (DONE, STOP):
                    self.trace("stop" if route == STOP else done_id(block))
                    return route
                if route <= i:
                    tries[route] = tries.get(route, 1) + 1
                    if decision.bound is not None:
                        bound_into[route] = decision.bound.count
                i = route
                continue
            if i + 1 >= block.sequence_length:  # end of the line, or a step reached by a decision only
                self.trace(done_id(block))
                return DONE
            i += 1
        self.trace(done_id(block))
        return DONE

    def _toolkeeper_after(self, block: ir.Block, i: int) -> ir.Member | None:
        if i + 1 < block.sequence_length and i not in block.decisions:
            after = block.steps[i + 1]
            if after.kind == "member" and self.team.members[after.name].kind == "toolkeeper":
                return self.team.members[after.name]
        return None

    def _decide(self, block, i, decision: ir.Decision, count: int, result: Result | None, passes) -> int | str:
        step = block.steps[i]
        if result is None or decision.field not in result.output:
            raise FlowError(f"{step.label} decides here but gave no `{decision.field}`")
        option = result.output[decision.field]
        if option not in decision.routes:
            raise FlowError(f"{step.label} answered `{option}`, which is not one of its options")
        route = decision.routes[option]
        bound_applied = False
        if (decision.bound is not None and count >= decision.bound.count
                and isinstance(route, int) and route <= i):
            route = decision.bound.target  # the Nth time, a looping choice is overridden (D96)
            bound_applied = True
        self.emit({
            "event": "route_decision",
            "decider": step.label,
            "block": block.name or None,
            "pass": _pass_field(passes),
            "field": decision.field,
            "option": option,
            "to": route if isinstance(route, str) else block.steps[route].label,
            "count": count,
            "bound_applied": bound_applied,
        })
        return route


def _pass_field(passes: tuple[Pass, ...]):
    if not passes:
        return None
    p = passes[-1]
    return {"block": p.block, "number": p.number, "of": p.of}
