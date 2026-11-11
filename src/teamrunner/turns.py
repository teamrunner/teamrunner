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
from .model import CeilingReached, Model, Reply, TransientError

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
        self.charged: float | None = 0.0  # None once any call's charge is not reported

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
        charged = getattr(reply, "cost_charged_usd", None)
        self.charged = None if charged is None or self.charged is None else self.charged + charged

    def take(self) -> dict:
        out = {"usage": dict(self.usage), "cost_usd": None if self.unknown else round(self.cost, 8),
               "cost_source": "; ".join(sorted(self.sources)) or None, "latency_ms": self.latency,
               "cost_charged_usd": None if self.charged is None else round(self.charged, 8)}
        self.__init__()
        return out


# Events a turn writes besides its last one; the run's own events are not part of any turn
_IN_A_TURN = {"retry", "tool_call"}
_ENVELOPE = ("seq", "ts", "run_id")


class Replay:
    """A stopped run's turns, to be reused in order by the run that continues it
    (D138 (8)): each unit is one member's turn — its retries and side trips, then
    its `agent_message` — or one toolkeeper step, its `tool_call`. A turn that
    did not finish is not a unit, so the new run takes it afresh."""

    def __init__(self, run_id: str, events):
        self.run_id = run_id
        self.units: list[list[dict]] = []
        pending: list[dict] = []
        for e in events:
            kind = e.get("event")
            if kind == "agent_message" or (kind == "tool_call" and e.get("mandatory")):
                self.units.append(pending + [e])
                pending = []
            elif kind in _IN_A_TURN:
                pending.append(e)
        self.next = 0
        self.on = bool(self.units)

    def peek(self, ahead: int = 0) -> list[dict] | None:
        i = self.next + ahead
        return self.units[i] if i < len(self.units) else None


class Members:
    """The actor: takes each member's turn when the engine says so."""

    def __init__(self, team: ir.Team, inputs: dict[str, object], model: Model, log: Log,
                 waits: tuple[float, ...] = PROVIDER_WAITS, context: Context | None = None,
                 replay: Replay | None = None):
        self.team = team
        self.inputs = inputs
        self.model = model
        self.log = log
        self.waits = waits
        self.minutes = Minutes()
        self.desk = Desk(team, log, context or Context(Path("inputs"), Path("work")), self._who)
        self.replay = replay

    async def __call__(self, turn: Turn) -> Result:
        if self.replay is not None and self.replay.on:
            reused = self._reuse(turn)
            if reused is not None:
                return reused
            self.replay.on = False  # from here on, every turn is taken afresh
        if turn.member.kind == "toolkeeper":
            return await self._toolkeeper_step(turn)
        return await self._turn(turn)

    # --- reusing a stopped run's turns (D138 (8)) ------------------------------------------

    def _reuse(self, turn: Turn) -> Result | None:
        """The stopped run's next turn, if it is this one: same member, same prompt
        (and for a toolkeeper step, the same request). None ends the replay."""
        unit = self.replay.peek()
        if unit is None:
            return None
        last = unit[-1]
        member = turn.member
        if member.kind == "toolkeeper":
            asker = turn.asker
            if (last["event"] != "tool_call" or last.get("toolkeeper") != member.name or asker is None
                    or last.get("from") != asker.turn.member.name or last.get("arguments") != asker.request):
                return None
            text = _told(last)
            if text is None:
                return None
            self.replay.next += 1
            seq = self._copy(unit)[-1]
            self.minutes.add(Entry(seq, member.name, text, to=asker.turn.member.name))
            return Result(turn, {})
        must = turn.must_ask
        if last["event"] != "agent_message" or last.get("from") != member.name:
            return None
        request = request_for(member, self.inputs, self.minutes, turn.step.stage, must.name if must else None)
        if last.get("prompt_hash") != request.prompt_hash:
            return None
        pending = None
        if must:  # its request is the next unit's: the toolkeeper step that follows
            step = self.replay.peek(1)
            if not step or step[-1]["event"] != "tool_call" or step[-1].get("toolkeeper") != must.name:
                return None
            pending = step[-1].get("arguments")
        self.replay.next += 1
        seq = self._copy(unit, read=request.read, prompt_hash=request.prompt_hash)[-1]
        self.minutes.add(Entry(seq, member.name, last.get("content") or ""))
        return Result(turn, dict(last.get("output") or {}), request=pending, card=TurnCard(member.name))

    def _copy(self, unit: list[dict], **fresh) -> list[int]:
        """Write a reused unit into this run's log, each event marked with where it came from;
        the cost stays, so the run's cost is the whole run's (D138 (8), amended)."""
        seqs = []
        for e in unit:
            event = {k: v for k, v in e.items() if k not in _ENVELOPE}
            if e["event"] == "agent_message":
                event.update(fresh)
            if e["event"] == "tool_call" and e.get("access") == "allowed":  # limits count reused calls too
                key = (e.get("toolkeeper"), e.get("from"))
                self.desk.run_use[key] = self.desk.run_use.get(key, 0) + 1
            event["reused_from"] = {"run_id": self.replay.run_id, "seq": e.get("seq")}
            seqs.append(self.log.emit(event))
        return seqs

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
            "flow_node": turn.flow_node,
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
                sponsored = ({"cost_usd": 0.0, "cost_charged_usd": 0.0,
                              "cost_source": "TeamRunner-sponsored demo (free to you)"}
                             if member.model.value == "teamrunner/demoLLM" else {})
                self.log.emit({"event": "retry", "kind": "provider", **self._who(member), **sponsored,
                               "attempt": retry + 1, "reason": str(e), "wait_ms": int(self.waits[retry] * 1000)})
                await asyncio.sleep(self.waits[retry])
            except CeilingReached:
                raise  # not a failure of the model: the call was never sent
            except Exception as e:
                self._emit_error(member, e)
                raise TurnFailed(f"{member.model.value} failed at {member.name}'s turn: {e}") from e

    def _who(self, member: ir.Member) -> dict:
        return {"component_id": member.id, "instance_path": instance_path(self.team, member)}

    def _emit_error(self, member: ir.Member, error: Exception) -> None:
        self.log.emit({"event": "error", **self._who(member), "what": "model call",
                       "message": str(error)})


def _told(event: dict) -> str | None:
    """What a toolkeeper step told the asker, rebuilt from its event — or None
    when the event does not hold it word for word."""
    if event.get("access") == "refused":
        return f"Refused: {event.get('reason')}."
    if event.get("access") == "allowed" and event.get("error") is None and event.get("result_kept") is not False \
            and isinstance(event.get("result"), str):
        return event["result"]
    return None


def _assistant(reply: Reply) -> dict:
    return {"role": "assistant", "content": reply.text or None,
            "tool_calls": [{"id": c.id, "type": "function",
                            "function": {"name": c.name, "arguments": c.arguments}}
                           for c in reply.tool_calls]}
