"""A member's turn: the minutes in, its words and typed outputs out.

`Members` is the engine's actor. For each turn it builds the request from
the minutes (D111), calls the model, checks the typed outputs against the
member's `output:` (D45) and, if they do not fit, says what was wrong and
asks again — three tries in all (D112). The words go into the minutes;
the turn is logged as an `agent_message` (D103).

During its turn a member may ask any toolkeeper that would let it in — a
side trip (D76). When a toolkeeper step follows, the member must end its
turn with a request to it; the toolkeeper replies to it alone (D85).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from . import ir
from .builtin_tools import Context
from .desk import Desk, TurnCard, parse_arguments
from .engine import Result, Turn
from .events import Log
from .minutes import Entry, Minutes, request_for
from .model import Model, Reply, TransientError

MAX_TRIES = 3  # D112
PROVIDER_WAITS = (2, 8, 30)  # seconds before each retry after an outage or rate limit (D100)


class TurnFailed(Exception):
    """A member could not give a reply that fits, or its model failed."""


def output_schema(fields: dict[str, ir.OutputField]) -> dict:
    """The JSON a member with typed outputs replies in: its words, then the fields."""
    props = {}
    for f in fields.values():
        if f.type == "choice":
            props[f.name] = {"type": "string", "enum": list(f.options)}
        else:
            props[f.name] = {"type": {"text": "string", "number": "number", "flag": "boolean"}[f.type]}
    return {
        "type": "object",
        "properties": {
            "said": {"type": "string", "description": "what you say to the team"},
            "output": {"type": "object", "properties": props, "required": list(props),
                       "additionalProperties": False},
        },
        "required": ["said", "output"],
        "additionalProperties": False,
    }


def read_reply(text: str, fields: dict[str, ir.OutputField]) -> tuple[str, dict, list[str]]:
    """Split a reply into its words and typed outputs. The last item lists
    what did not fit, in words the member is shown on its next try."""
    if not fields:
        return text.strip(), {}, []
    try:
        data = json.loads(text)
    except ValueError:
        return text, {}, ["the reply was not the JSON asked for"]
    if not isinstance(data, dict) or not isinstance(data.get("output"), dict):
        return text, {}, ["the reply had no `output` with your fields"]
    said = data.get("said") if isinstance(data.get("said"), str) else ""
    given, output, wrong = data["output"], {}, []
    for f in fields.values():
        if f.name not in given:
            wrong.append(f"`{f.name}` was missing")
            continue
        value = given[f.name]
        if f.type == "choice":
            ok = isinstance(value, str) and value.strip() in f.options
            value = value.strip() if ok else value
            expected = "one of " + ", ".join(f"`{o}`" for o in f.options)
        elif f.type == "number":
            ok = isinstance(value, (int, float)) and not isinstance(value, bool)
            expected = "a number"
        elif f.type == "flag":
            ok = isinstance(value, bool)
            expected = "`true` or `false`"
        else:
            ok = isinstance(value, str)
            expected = "text"
        if ok:
            output[f.name] = value
        else:
            wrong.append(f"`{f.name}` was {json.dumps(value)}; it must be {expected}")
    return said.strip(), output, wrong


def instance_path(team: ir.Team, member: ir.Member) -> str:
    """A member's path from the package top, by names (D34)."""
    folders = member.path.parent.relative_to(team.package_top).parts
    return "/".join([*folders, member.name])


class Spend:
    """What a turn's model calls cost, reported once: in a retry or the agent_message."""

    def __init__(self):
        self.usage, self.cost, self.latency, self.unknown = {"input": 0, "output": 0}, 0.0, 0, False
        self.sources: set[str] = set()

    def add(self, reply: Reply) -> None:
        self.usage["input"] += reply.usage.get("input", 0)
        self.usage["output"] += reply.usage.get("output", 0)
        self.latency += reply.latency_ms
        if reply.cost_source:
            self.sources.add(reply.cost_source)
        if reply.cost_usd is None:
            self.unknown = True
        else:
            self.cost += reply.cost_usd

    def take(self) -> dict:
        out = {"usage": dict(self.usage), "cost_usd": None if self.unknown else round(self.cost, 8),
               "cost_source": "; ".join(sorted(self.sources)) or None, "latency_ms": self.latency}
        self.__init__()
        return out


class Members:
    """The actor: takes each member's turn when the engine says so."""

    def __init__(self, team: ir.Team, inputs: dict[str, object], model: Model, log: Log,
                 waits: tuple[float, ...] = PROVIDER_WAITS, context: Context | None = None):
        self.team = team
        self.inputs = inputs
        self.model = model
        self.log = log
        self.waits = waits
        self.minutes = Minutes()
        self.desk = Desk(team, log, context or Context(Path("inputs"), Path("work")), self._who)

    async def __call__(self, turn: Turn) -> Result:
        if turn.member.kind == "toolkeeper":
            return await self._toolkeeper_step(turn)
        return await self._turn(turn)

    async def _turn(self, turn: Turn) -> Result:
        member, must = turn.member, turn.must_ask
        request = request_for(member, self.inputs, self.minutes, turn.step.stage,
                              must.name if must else None)
        offered = self.desk.open_to(member.name)
        tools = [self.desk.tool_definition(t) for t in offered] or None
        # A turn that ends with a request to a toolkeeper step gives no typed outputs:
        # its answer is the request (D85).
        fields = {} if must else member.output
        schema = output_schema(fields) if fields else None
        params = {k: s.value for k, s in member.params.items()}
        messages = list(request.messages)
        card, spend = TurnCard(member.name), Spend()
        # Side trips end: each toolkeeper's calls_per_turn, then the tools are no longer offered.
        trips = sum(t.terms_for(member.name).guardrails.get("calls_per_turn", ir.CALLS_PER_TURN)
                    for t in offered) + 2
        attempt, pending, said, output, reply = 1, None, "", {}, None

        while True:
            reply = await self._call(member, params, messages, schema, tools if trips > 0 else None)
            spend.add(reply)
            if reply.tool_calls:
                trips -= 1
                messages.append(_assistant(reply))
                for call in reply.tool_calls:
                    arguments = parse_arguments(call.arguments)
                    if must and call.name == must.name and pending is None:
                        pending = arguments
                        text = f"Sent to {must.name}; you will read the reply at your next turn."
                    elif call.name in self.team.members and self.team.members[call.name].kind == "toolkeeper":
                        text, _ = await self.desk.ask(self.team.members[call.name], member.name,
                                                      arguments, card, mandatory=False)
                    else:
                        text = f"There is no toolkeeper called {call.name} that you may ask."
                    messages.append({"role": "tool", "tool_call_id": call.id, "content": text})
                if pending is not None:
                    said = reply.text.strip()
                    break
                continue
            if must:
                said, output, wrong = reply.text.strip(), {}, [f"your turn must end with a request to {must.name}"]
            else:
                said, output, wrong = read_reply(reply.text, fields)
            if not wrong:
                break
            self.log.emit({"event": "retry", "kind": "output", **self._who(member),
                           "attempt": attempt, "reason": "; ".join(wrong), "reply": reply.text,
                           **spend.take()})
            if attempt == MAX_TRIES:
                raise TurnFailed(f"{member.name}'s reply did not fit after {MAX_TRIES} tries: "
                                 + "; ".join(wrong))
            attempt += 1
            messages += [
                {"role": "assistant", "content": reply.text},
                {"role": "user", "content": "Your reply did not fit: " + "; ".join(wrong)
                 + ". Answer again."},
            ]

        inner = turn.passes[-1] if turn.passes else None
        seq = self.log.emit({
            "event": "agent_message", **self._who(member),
            "from": member.name,
            "to": "everyone",
            "block": inner.block if inner else None,
            "pass": {"number": inner.number, "of": inner.of} if inner else None,
            "blocks": [{"block": p.block, "number": p.number, "of": p.of} for p in turn.passes],
            "stage": turn.step.stage,
            "attempt": {"number": turn.attempt, "of": turn.attempts_of},
            "read": request.read,
            "content": said,
            "output": output,
            "asks": must.name if must else None,
            "model_resolved": reply.model_resolved,
            **spend.take(),
            "prompt_hash": request.prompt_hash,
            "cached": False,
        })
        self.minutes.add(Entry(seq, member.name, said))
        return Result(turn, output, request=pending, card=card)

    async def _toolkeeper_step(self, turn: Turn) -> Result:
        """A toolkeeper step: check, call, reply — to whoever asked (D85)."""
        keeper, asker = turn.member, turn.asker
        if asker is None or asker.request is None:
            raise TurnFailed(f"{keeper.name}'s step came, but no member asked it")
        name = asker.turn.member.name
        text, seq = await self.desk.ask(keeper, name, asker.request, asker.card, mandatory=True)
        self.minutes.add(Entry(seq, keeper.name, text, to=name))
        return Result(turn, {})

    async def _call(self, member: ir.Member, params, messages, schema, tools=None) -> Reply:
        """One model call, tried again after a wait if the provider fails (D100)."""
        for retry in range(len(self.waits) + 1):
            try:
                return await self.model.complete(member.model.value, params, messages, schema, tools)
            except TransientError as e:
                if retry == len(self.waits):
                    self._emit_error(member, e)
                    raise TurnFailed(f"{member.model.value} still failed after {retry + 1} tries "
                                     f"at {member.name}'s turn: {e}") from e
                self.log.emit({"event": "retry", "kind": "provider", **self._who(member),
                               "attempt": retry + 1, "reason": str(e), "wait_ms": int(self.waits[retry] * 1000)})
                await asyncio.sleep(self.waits[retry])
            except Exception as e:
                self._emit_error(member, e)
                raise TurnFailed(f"{member.model.value} failed at {member.name}'s turn: {e}") from e

    def _who(self, member: ir.Member) -> dict:
        return {"component_id": member.id, "instance_path": instance_path(self.team, member)}

    def _emit_error(self, member: ir.Member, error: Exception) -> None:
        self.log.emit({"event": "error", **self._who(member), "what": "model call",
                       "message": str(error)})


def _assistant(reply: Reply) -> dict:
    return {"role": "assistant", "content": reply.text or None,
            "tool_calls": [{"id": c.id, "type": "function",
                            "function": {"name": c.name, "arguments": c.arguments}}
                           for c in reply.tool_calls]}
