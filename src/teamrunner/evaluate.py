"""Evaluating an experiment's runs (D138 (13), amended; SPEC §5.4).

Each evaluator evaluates each run that fills a slot, `evaluations:` times,
in fresh calls made in an order drawn from the master seed and mixed across
conditions, so no condition is evaluated all at once. Every evaluation is a
line in `runs/<run>/evaluations/<metric>.jsonl`, appended, never rewritten,
keyed by the evaluator's version hash, and reused while that hash holds.

A plain `teamrunner experiment` evaluates only what was never evaluated; an
evaluator edited since is reported, with what evaluating again would cost,
and `--evaluate` does it. Every call goes through the ceiling.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .estimate import CHARS_PER_TOKEN, REPLY_TOKENS
from .metrics import Evaluator
from .model import CeilingReached, TransientError
from .turns import MAX_TRIES, output_schema, read_reply

FOLDER = "evaluations"
ASK = "Give your evaluation now."


def lines_of(run: Path, metric: str) -> list[dict]:
    path = run / FOLDER / f"{metric}.jsonl"
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def current(run: Path, ev: Evaluator) -> list[dict]:
    """The run's evaluations by this version of the evaluator, one per evaluation number."""
    seen = {}
    for line in lines_of(run, ev.name):
        if line.get("evaluator_hash") == ev.version and line.get("output") is not None:
            seen.setdefault(line.get("evaluation"), line)
    return [seen[k] for k in sorted(seen, key=lambda k: (k is None, k))]


@dataclass
class Job:
    run: Path
    record: dict
    evaluator: Evaluator
    number: int  # which of its `evaluations:`
    order: int = 0


def pending(runs: list[tuple[Path, dict]], evaluators: list[Evaluator], again: bool
            ) -> tuple[list[Job], dict[str, int]]:
    """(the evaluations to make, and per evaluator how many runs are out of date and skipped).
    Out of date: evaluated only by earlier versions. `again` (--evaluate) makes those too."""
    jobs, stale = [], {}
    for ev in evaluators:
        for folder, record in runs:
            lines = lines_of(folder, ev.name)
            done = {line.get("evaluation") for line in current(folder, ev)}
            if lines and not done and not again:
                stale[ev.name] = stale.get(ev.name, 0) + 1
                continue
            jobs += [Job(folder, record, ev, k) for k in range(1, ev.evaluations + 1) if k not in done]
    return jobs, stale


def shuffle(jobs: list[Job], seed: int) -> list[Job]:
    """An order drawn from the master seed (D138 (13)): conditions mixed, never in blocks."""
    from .experiment import _draw
    for job in jobs:
        job.order = _draw("teamrunner/evaluate/v1", seed, job.record.get("run_id"), job.evaluator.name, job.number)
    return sorted(jobs, key=lambda j: j.order)


def estimate_one(ev: Evaluator, run_tokens_out: float, prices) -> float | None:
    """One evaluation's cost: the rubric, what it reads (≈ the run's words), a typical reply."""
    from .prices import model_price
    price = model_price(ev.model, prices)
    if price["input_per_million"] is None:
        return None
    reads = 0
    for name in ev.placeholders:
        reads += run_tokens_out if name == "transcript" else REPLY_TOKENS if name == "output" else 20
    prompt = len(ev.body) // CHARS_PER_TOKEN + reads + 60
    given = ev.params.get("max_tokens")
    reply = min(REPLY_TOKENS, given) if isinstance(given, int) and given > 0 else REPLY_TOKENS
    return (prompt * price["input_per_million"] + reply * price["output_per_million"]) / 1e6


async def _one(job: Job, model, waits) -> dict:
    """One evaluation: a fresh call, tried again if its reply does not fit (D112)."""
    from .runs import read_events
    ev = job.evaluator
    events = list(read_events(job.run))
    prompt = job.evaluator.prompt(job.run, job.record, events)
    messages = [{"role": "system", "content": prompt}, {"role": "user", "content": ASK}]
    schema = output_schema(ev.output)
    started = time.monotonic()
    usage, cost, unknown, answered, output, said, wrong = {"input": 0, "output": 0}, 0.0, False, None, None, "", []
    charged = 0.0  # None once a call's charge is not reported (D120, amended)
    for attempt in range(1, MAX_TRIES + 1):
        for retry in range(len(waits) + 1):
            try:
                reply = await model.complete(ev.model, dict(ev.params), messages, schema)
                break
            except TransientError:
                if retry == len(waits):
                    raise
                await asyncio.sleep(waits[retry])
        usage["input"] += reply.usage.get("input", 0)
        usage["output"] += reply.usage.get("output", 0)
        if reply.cost_usd is None:
            unknown = True
        else:
            cost += reply.cost_usd
        charged = None if charged is None or reply.cost_charged_usd is None else charged + reply.cost_charged_usd
        answered = reply.model_resolved
        said, out, wrong = read_reply(reply.text, ev.output)
        if not wrong:
            output = out
            break
        messages += [{"role": "assistant", "content": reply.text},
                     {"role": "user", "content": "Your reply did not fit: " + "; ".join(wrong) + ". Answer again."}]
    return {"run_id": job.record.get("run_id"), "metric": ev.name, "evaluator_hash": ev.version,
            "evaluation": job.number, "prompt": prompt, "model_asked": ev.model, "model_answered": answered,
            "output": output, "said": said, "problem": "; ".join(wrong) if output is None else None,
            "usage": usage, "cost_usd": None if unknown else round(cost, 8),
            "cost_charged_usd": None if charged is None else round(charged, 8),
            "latency_ms": int((time.monotonic() - started) * 1000),
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")}


def save(job: Job, line: dict) -> None:
    folder = job.run / FOLDER
    folder.mkdir(exist_ok=True)
    path = folder / f"{job.evaluator.name}.jsonl"
    if not any(old.get("evaluator_hash") == job.evaluator.version and old.get("source")
               for old in lines_of(job.run, job.evaluator.name)):
        line = {**line, "source": job.evaluator.source}  # the evaluator's text, the first time this version appears
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")


def run_jobs(jobs: list[Job], model, workers: int, waits=(2, 8, 30), cancel=None) -> dict:
    """Make the evaluations, up to `workers` at once. Returns what happened."""
    done = {"made": 0, "failed": 0, "refused": 0, "cost": 0.0}

    async def main():
        gate = asyncio.Semaphore(max(1, workers))

        async def go(job):
            async with gate:
                if cancel is not None and cancel.is_set():
                    return
                try:
                    line = await _one(job, model, waits)
                except CeilingReached:
                    done["refused"] += 1
                    return
                except Exception as e:  # a provider failure: kept out, tried again next time
                    done["failed"] += 1
                    done.setdefault("errors", []).append(f"{job.evaluator.name}: {type(e).__name__}: {e}")
                    return
                save(job, line)
                done["made" if line["output"] is not None else "failed"] += 1
                done["cost"] += line["cost_usd"] or 0.0

        await asyncio.gather(*(go(j) for j in jobs))

    asyncio.run(main())
    return done


def spent_on(runs: list[tuple[Path, dict]], evaluators) -> float:
    """Everything evaluating has cost in these runs, every version: it counts in the ceiling."""
    total = 0.0
    for folder, _ in runs:
        for path in (folder / FOLDER).glob("*.jsonl") if (folder / FOLDER).is_dir() else []:
            for line in lines_of(folder, path.stem):
                total += line.get("cost_usd") or 0.0
    return total
