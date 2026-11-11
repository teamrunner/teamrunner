"""`teamrunner run`: a call to a team (D69), start to finish.

    validate → inputs → keys and models checked (D99, D100) → run folder →
    the flow, turn by turn → record.json and output.md

Everything that can stop a run is found before the first turn. Once it
starts, the log is written line by line, so a failure or Ctrl-C keeps
everything said so far (D100, D101).
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from typing import Callable

from . import __version__, ir
from .environment import environment
from .build import load_team
from .builtin_tools import Context
from .checks import Check, Problem, LISTERS, litellm_data, litellm_params, needed_keys, rehearsal_check, run_check
from .console import Console
from .engine import Engine, FlowError
from .events import Log
from .flowgraph import declared_graph
from .inputs import InputError, copy_files, resolve
from .keys import Ask, Keys, extra_settings, find_keys
from .minutes import MissingInput, instructions
from .model import CeilingReached, Model
from .prices import load_prices, model_price, tool_price
from .rehearsal import RehearsalModel
from .runfolder import RunFolder, fingerprint, version_hash
from .turns import Members, TurnFailed, instance_path
from .validate import Report

EXIT = {"completed": 0, "could_not_start": 1, "failed": 2, "stopped": 130}


@dataclass
class Options:
    message: str | None = None
    file: Path | None = None
    task_file: Path | None = None
    runs_dir: Path | None = None
    verbose: bool = False
    quiet: bool = False
    ask: Ask | None = None  # None when no one is at the keyboard
    rehearse: bool = False  # D115: no model; placeholder words and random answers
    # Experiments (D138): the run's seed, the folder made for it, and its `experiment` block
    seed: int | None = None
    run_folder: Path | None = None
    experiment: dict | None = None
    # D138 (6): a hard ceiling on what the run spends, and where it was set
    # ("--max-cost", or "experiment.md" for an experiment's runs)
    max_cost: float | None = None
    max_cost_from: str | None = None
    # D138 (8): a stopped run's folder, whose turns this run reuses before calling models
    continue_from: Path | None = None


@dataclass
class Outcome:
    status: str  # completed | failed | stopped | could_not_start
    exit_code: int
    folder: Path | None = None
    report: Report | None = None
    reason: str | None = None
    check: Check | None = None


class RunStopped(Exception):
    """Cooperative cancellation, with the same final record as Ctrl-C."""


async def _run_until_stopped(engine, cancel: Event | None):
    if cancel is None:
        return await engine.run()
    if cancel.is_set():
        raise RunStopped()
    task = asyncio.create_task(engine.run())

    async def watch():
        while not cancel.is_set():
            await asyncio.sleep(0.05)
        task.cancel()

    watcher = asyncio.create_task(watch())
    try:
        return await task
    except asyncio.CancelledError:
        if cancel.is_set():
            raise RunStopped() from None
        raise
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)


def entry_path(team: ir.Team) -> str:
    """The team that was run, as a path from the package top (D69)."""
    rel = team.folder.relative_to(team.package_top).parts
    return "/".join([team.package_top.name, *rel])


def _model_from(setting: ir.Setting) -> str:
    if setting.source.startswith("models/"):
        return setting.source.split(",")[0]
    return "default_model" if "default_model" in setting.source else "own"


def _members_record(team: ir.Team, inputs: dict) -> list[dict]:
    members = []
    for m in team.members.values():
        source = m.path.read_bytes()
        record = {"name": m.name, "instance_path": instance_path(team, m), "component_id": m.id,
                  "version_hash": version_hash(m.path, source), "kind": m.kind,
                  "source_file": m.path.relative_to(team.package_top).as_posix(),
                  "source_text": source.decode("utf-8")}
        if m.kind == "toolkeeper":
            record.update(tool=m.tool, terms=m.base.guardrails,
                          access={k: (v.guardrails if v else "deny") for k, v in m.access.items()},
                          others=m.others.guardrails if m.others else None, sign=m.body.strip())
        else:
            record.update(model=m.model.value, model_from=_model_from(m.model),
                          params={k: s.value for k, s in m.params.items()},
                          **({"model_choices": [c.written for c in m.choices],
                              "model_choice": len(m.skipped) + 1,
                              "models_skipped": m.skipped} if len(m.choices) > 1 else {}),
                          output={f.name: {"type": f.type, "options": list(f.options)}
                                  for f in m.output.values()},
                          prompt=instructions(m, inputs))
        members.append(record)
    return members


def _price_list(team: ir.Team, prices) -> dict:
    """Every price this run will be counted at, with where it came from (D120)."""
    from .builtin_tools import LIST_PRICES
    models = sorted({m.model.value for m in team.members.values() if m.model})
    tools = sorted({m.tool for m in team.members.values() if m.kind == "toolkeeper"})
    return {"models": {m: model_price(m, prices) for m in models},
            "tools": {t: tool_price(t, LIST_PRICES.get(t, 0.0), prices) for t in tools}}


def _deciders(team: ir.Team) -> dict[str, str]:
    """Each decider's step and the output its decision reads."""
    return {block.steps[i].label: d.field
            for block in [team.flow.main, *team.flow.blocks.values()] for i, d in block.decisions.items()}


def _output_md(team: ir.Team, log: Log, status: str, reason: str | None) -> str:
    said = log.of("agent_message")
    lines = []
    if status == "failed":
        lines.append(f"**The run failed.** {reason}")
        lines.append("Everything said before that is in events.jsonl.")
        return "\n\n".join(lines)
    if not said:
        return reason or "Nothing was said."
    last = said[-1]
    if status == "stopped" and reason:
        lines.append(f"**Stopped.** {reason}")
    lines.append(f"**{last['from']}**")
    if last["content"]:
        lines.append(last["content"])
    if last["output"]:
        lines.append("\n".join(f"- **{k}:** {v}" for k, v in last["output"].items()))
    return "\n\n".join(lines)


def run_team(folder: Path, options: Options, model_factory: Callable[..., Model] | None = None,
             listers=None, model_data=None, console: Console | None = None,
             waits=None, *, cancel: Event | None = None,
             on_folder: Callable[[Path], None] | None = None,
             on_event: Callable[[dict], None] | None = None,
             on_turn: Callable[[dict], None] | None = None,
             wrap: Callable[[Model], Model] | None = None) -> Outcome:
    """`wrap`, when given, wraps whatever model the run uses — an experiment's
    ceiling sees every call this way (D138 (6)). A rehearsal has no ceiling."""
    started = time.monotonic()
    team, report = load_team(Path(folder), write_ids=True)
    if team is None:
        return Outcome("could_not_start", EXIT["could_not_start"], report=report,
                       reason="the team's files have errors; `teamrunner validate` lists them")
    if console is None and not options.quiet:
        console = Console(options.verbose)

    try:
        given = resolve(team, options.message, options.file, options.task_file)
    except InputError as e:
        if console:
            console.say(console.paint.red(f"✗ {e}"))
        return Outcome("could_not_start", EXIT["could_not_start"], report=report, reason=str(e))

    if console:
        console.header(team.name, given)
    ask = None if options.quiet else options.ask
    settings = extra_settings()
    if options.rehearse:
        keys, check = Keys(), rehearsal_check(team)
        seed = options.seed if options.seed is not None else random.SystemRandom().randrange(2**32)
        model_factory = lambda keys, settings: RehearsalModel(seed)  # noqa: E731
    else:
        keys = find_keys(needed_keys(team), team.package_top, ask)
        check = run_check(team, keys, LISTERS if listers is None else listers, settings,
                          litellm_data if model_data is None else model_data,
                          litellm_params if listers is None else None)
        seed = None
    prices = None if options.rehearse else load_prices()
    for problem in prices.problems if prices else []:
        check.problems.append(Problem("prices.yaml", [], problem, "fix the line, or remove it to use the default"))
    rehearsal = {"seed": seed} if options.rehearse else None
    if console:
        if options.rehearse:
            console.say(console.paint.yellow("rehearsal · no model is called; the words are placeholders "
                                             "and the answers random"))
        console.check(check, keys)
    if check.ok and not options.rehearse and any(
            m.kind == "toolkeeper" and m.tool == "run_code" for m in team.members.values()):
        from .sandbox import READY, deno_dir, prepare
        if console and not (deno_dir() / READY).exists():
            console.say(console.paint.dim("preparing the code sandbox — the first time only…"))
        failed = prepare()
        if failed:
            check.problems.append(Problem("run_code", [], failed, "connect to the internet and run again"))
            if console:
                console.say(console.paint.red(f"✗ {failed}"))
    if not check.ok:
        if console:  # no run folder, so no log to print them from
            for warning in report.problems.warnings:
                console.say(console.paint.yellow(f"! {warning.where(team.package_top)}  {warning.message}"))
        return Outcome("could_not_start", EXIT["could_not_start"], report=report,
                       reason="the check before the first turn found problems", check=check)

    if options.run_folder is not None:
        options.run_folder.mkdir(parents=True, exist_ok=True)
        run = RunFolder(options.run_folder)
    else:
        run = RunFolder.create(team.package_top, team.name, options.runs_dir)
    if on_folder:
        on_folder(run.path)
    copy_files(given, run.inputs_dir)
    values = {name: item.value for name, item in given.items()}
    log = Log(on_event=lambda e: (run.write_event(e), console.on_event(e) if console else None,
                                 on_event(e) if on_event else None))
    started_at = datetime.now(timezone.utc)
    inputs_record = {name: {"value": item.value, "source": item.source,
                            **({"file": item.file} if item.file else {})} for name, item in given.items()}
    print_fp = fingerprint(team.package_top)
    price_list = _price_list(team, prices) if prices is not None else None
    try:
        members_record = _members_record(team, values)
    except MissingInput as e:  # validate and resolve make this unreachable; kept as a guard
        run.close()
        return Outcome("could_not_start", EXIT["could_not_start"], run.path, report, str(e))
    log.emit({"event": "run_started", "teamrunner_version": __version__, "team": team.name,
              "entry": entry_path(team),
              "inputs": inputs_record, "fingerprint": print_fp, "flow": team.flow.source,
              "deciders": _deciders(team), "rehearsal": rehearsal,
              "prices": price_list, "members": members_record, "flow_graph": declared_graph(team)})
    log.emit(check.event())
    for warning in report.problems.warnings:
        log.emit({"event": "warning", "message": warning.message,
                  "where": warning.where(team.package_top)})
    for warning in check.warnings:
        log.emit({"event": "warning", "message": warning})

    status, reason, stopped_by = "completed", None, None
    flow_path = []
    try:
        if model_factory is None:
            from .litellm_model import LiteLLMModel
            model = LiteLLMModel(keys.values, settings, prices)
        else:
            model = model_factory(keys.values, settings)
        if wrap is not None:
            model = wrap(model)
        elif options.max_cost is not None and not options.rehearse:  # a single run's own ceiling
            from .ceiling import Ceiling, Guarded
            model = Guarded(model, Ceiling(options.max_cost), prices)
        context = Context(run.inputs_dir, run.path / "work", keys.values, team.package_top,
                          rehearsal=options.rehearse, prices=prices, inputs=values)
        context.work_dir.mkdir(exist_ok=True)
        replay = None
        if options.continue_from is not None:
            from .runs import read_events
            from .turns import Replay
            old = list(read_events(options.continue_from))
            started_old = next((e for e in old if e.get("event") == "run_started"), {})
            replay = Replay(started_old.get("run_id") or options.continue_from.name, old)
        actor = Members(team, values, model, log, context=context, replay=replay,
                        **({"waits": waits} if waits is not None else {}))

        async def take_turn(turn):
            await asyncio.sleep(0)  # Rehearsals must also yield to cancellation.
            if cancel is not None and cancel.is_set():
                raise RunStopped()
            if on_turn:
                on_turn({"member": turn.member.name, "stage": turn.step.stage,
                         "kind": turn.member.kind})
            if cancel is not None and cancel.is_set():
                raise RunStopped()
            return await actor(turn)

        engine = Engine(team, take_turn, log.emit, trace=flow_path.append)
        outcome = asyncio.run(_run_until_stopped(engine, cancel))
        if outcome.status == "stopped_by_flow":
            status, stopped_by = "stopped", "flow"  # the team's own result (D138 (7), amended)
            last = log.of("route_decision")[-1] if log.of("route_decision") else None
            reason = (f"The flow stopped after {last['decider']} answered `{last['option']}`."
                      if last else "The flow reached `stop`.")
    except RunStopped:
        status, reason, stopped_by = "stopped", "Stopped by the user; the turn in progress was abandoned.", "user"
    except CeilingReached as e:
        status, reason, stopped_by = "stopped", str(e), "ceiling"
    except (TurnFailed, FlowError) as e:
        status, reason = "failed", str(e)
    except KeyboardInterrupt:
        status, reason, stopped_by = "stopped", "Stopped with Ctrl-C; the turn in progress was abandoned.", "user"
    except Exception as e:
        status, reason = "failed", f"{type(e).__name__}: {e}"
        for secret in keys.values.values():
            if secret:
                reason = reason.replace(secret, "[key hidden]")

    said = log.of("agent_message")
    retries = log.of("retry")
    costs = [e.get("cost_usd") for e in said + retries]  # the models'
    tools_cost = round(sum(e.get("cost_usd") or 0 for e in log.of("tool_call")), 6)  # D118
    record = {
        "run_id": log.run_id,
        "teamrunner_version": __version__,
        "environment": environment(),  # D14c: what a rerun needs to match
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "team": team.name,
        "entry": entry_path(team),
        "inputs": inputs_record,
        "fingerprint": print_fp,
        "status": status,
        "stopped_by": stopped_by,  # flow | user | ceiling, when stopped (D138 (7), amended)
        "reason": reason,
        "turns": len(said),
        "tool_calls": len(log.of("tool_call")),
        "latency_ms": int((time.monotonic() - started) * 1000),
        "cost_usd": None if any(c is None for c in costs) else round(sum(costs) + tools_cost, 6),
        "cost_known_usd": round(sum(c for c in costs if c is not None) + tools_cost, 6),
        "cost_models_usd": None if any(c is None for c in costs) else round(sum(costs), 6),
        "cost_tools_usd": tools_cost,
        # D120, amended: what the providers say they charged for the model calls; null if any did not say
        "cost_charged_usd": _charged_total(said + retries),
        # D138 (8), amended: of the cost, what reused turns carried over from the run continued
        "cost_reused_usd": round(sum(e.get("cost_usd") or 0 for e in log.events if e.get("reused_from")), 6),
        "tokens": {"input": sum(e["usage"]["input"] for e in said + retries if "usage" in e),
                   "output": sum(e["usage"]["output"] for e in said + retries if "usage" in e)},
        "keys": keys.sources,
        "rehearsal": rehearsal,
        "backup_models": {b["member"]: b["model"] for b in check.backups},  # D135: not each first choice
        "cost_is_estimate": True,  # D120: exact token counts times list prices, not what was charged
        "prices": price_list,
        "experiment": options.experiment,  # D138 (10): null for a single run
        "ceiling": ({"max_cost_usd": options.max_cost, "from": options.max_cost_from}
                    if options.max_cost is not None else None),
    }
    log.emit({"event": "run_finished", "status": status, "reason": reason,
              "flow_path": flow_path,
              "turns": record["turns"], "tool_calls": record["tool_calls"],
              "latency_ms": record["latency_ms"], "cost_usd": record["cost_usd"],
              "cost_models_usd": record["cost_models_usd"], "cost_tools_usd": record["cost_tools_usd"],
              "tokens": record["tokens"]})
    run.write_record(record)
    output = _output_md(team, log, status, reason)
    if options.rehearse:
        output = (f"*A rehearsal (seed {seed}): placeholder words and random answers, "
                  "to check the flow, not the content.*\n\n" + output)
    run.write_output(output)
    run.close()
    if console:
        shown = record if record["cost_usd"] is not None else {**record, "cost_usd": record["cost_known_usd"]}
        console.totals(shown, _shown_path(run.path), output)
    return Outcome(status, EXIT[status], run.path, report, reason)


def _charged_total(events: list[dict]) -> float | None:
    charges = [e.get("cost_charged_usd") for e in events if e.get("usage")]
    if not charges or any(c is None for c in charges):
        return None
    return round(sum(charges), 6)


def _shown_path(path: Path) -> Path:
    try:
        return path.relative_to(Path.cwd())
    except ValueError:
        return path
