"""`teamrunner experiment <folder> --plan` (D138 (4)): what would run, in what
order, and about what it would cost — built and checked, and nothing spent.
"""

from __future__ import annotations

import os
from pathlib import Path

from .build import _team
from .estimate import REPLY_TOKENS, WALKS, Estimate, estimate
from .experiment import (LATER, Experiment, _draw, build, differences, plan_runs, read_experiment)
from .prices import load_prices
from .problems import ERROR

# D138 (6): how the estimate is shown against the ceiling, `max_cost_usd`.
HARD_AT = 0.6  # above this share, a line says the ceiling is hard and how to raise it
WARN_AT = 0.9  # above this share, a warning that the experiment may stop incomplete
RAISE = "To allow more: --max-cost <usd>"


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return Path(os.path.relpath(path, root)).as_posix()


def _money(x: float | None) -> str:
    if x is None:
        return "unknown"
    return f"${x:,.4f}" if x < 0.1 else f"${x:,.2f}"


def report_problems(exp: Experiment, out) -> bool:
    """Print what stops the experiment; True when nothing does."""
    root, say = exp.folder, out.say
    title = exp.name or exp.folder.name
    if exp.later:
        say(out.paint.red(f"✗ {title} needs TeamRunner {LATER}, so it cannot run yet:"))
        # experiment.md's own lines first, then the team's files
        for file, line, what in sorted(exp.later, key=lambda t: (t[0] != exp.path, str(t[0]), t[1] or 0)):
            where = _rel(file, root) + (f":{line}" if line else "")
            say(f"  - {what}  {out.paint.dim('(' + where + ')')}")
    errors = [p for p in exp.problems if p.level == ERROR]
    if errors:
        say(out.paint.red(f"✗ {title} has {len(errors)} problem{'s' if len(errors) != 1 else ''} to fix first:"))
        for p in sorted(errors, key=lambda p: (str(p.file), p.line or 0)):
            say(f"  {out.paint.red('✗')} {_where(p, root)}  {p.message}")
            if p.fix:
                say(f"      {out.paint.dim(p.fix)}")
    return not (exp.later or errors)


def _where(p, root: Path) -> str:
    return p.where(root) if p.file.is_relative_to(root) else _rel(p.file, root) + (f":{p.line}" if p.line else "")


def say_estimate(out, est: float | None, limit: float | None, spent: float = 0.0,
                 lead: str = "Estimated cost", brief: bool = False) -> bool:
    """The estimate against the ceiling, in one to three lines (`brief`: the first only).
    True when it is over the ceiling; the caller says what that means where it is."""
    say, paint = out.say, out.paint
    if est is None:
        say(f"{lead} unknown")
        return False
    if est <= 0:
        return False
    already = f" ({_money(spent)} already spent)" if spent else ""
    if limit is None:
        say(f"{lead} ≈ {_money(est)}{already}")
        if brief:
            return False
        say(paint.yellow("  ! no max_cost_usd: nothing caps what this spends"))
        return False
    share = est / limit
    say(f"{lead} ≈ {_money(est)}{already}. {share:.0%} of max ({_money(limit)})")
    if share > 1 or brief:
        return share > 1
    if share > HARD_AT:
        say(f"  The ceiling is hard: runs stop before passing it. {RAISE}")
    if share > WARN_AT:
        say(paint.yellow("  ! close to the ceiling: the experiment may stop incomplete"))
    return False


def litellm_max_lines(exp: Experiment) -> list[str]:
    """One line for each model some member uses with no `max_tokens`: LiteLLM's
    maximum is what the ceiling sets aside, and what "at most" counts."""
    from .ceiling import max_reply
    found: dict[str, list[str]] = {}
    for cond in exp.conditions:
        for m in _team(cond.report).members.values():
            if m.kind == "toolkeeper":
                continue
            for choice in m.choices:
                if "max_tokens" not in choice.params:
                    names = found.setdefault(choice.model.value, [])
                    if m.name not in names:
                        names.append(m.name)
    lines = []
    for model, names in found.items():
        most = max_reply(model, {})
        what = (f"LiteLLM's maximum is used: {most:,} tokens a reply" if most else
                f"LiteLLM does not know its maximum either: counted at ≈{REPLY_TOKENS}")
        lines.append(f"{', '.join(names)} ({model}): no max_tokens, so {what}")
    return lines


def ceiling_of(exp: Experiment, max_cost: float | None) -> tuple[float | None, str | None]:
    """The ceiling and where it was set: `--max-cost` replaces `max_cost_usd`."""
    if max_cost is not None:
        return max_cost, "--max-cost"
    limit = exp.limits.get("max_cost_usd")
    return limit, ("experiment.md" if limit is not None else None)


def plan(folder: Path, out, prices=None, walks: int = WALKS, max_cost: float | None = None) -> int:
    """Print the plan. Returns the exit code: 0 ready, 1 not."""
    exp = read_experiment(folder)
    build(exp)  # even with problems in experiment.md, so every one is found at once
    root = exp.folder
    say = out.say
    title = exp.name or exp.folder.name
    if not report_problems(exp, out):
        return 1
    warnings = [p for p in exp.problems if p.level != ERROR]

    n_cond, reps = len(exp.conditions), exp.replications
    factors = ""
    if exp.factors:
        factors = " (" + " × ".join(f"{f}: {len(levels)}" for f, levels in exp.factors.items()) + ")"
    say(f"{title} · {n_cond} conditions{factors} × {reps} replications = {exp.runs} runs · seed {exp.seed}")
    say("")

    say("Conditions")
    width = max(len(c.name) for c in exp.conditions)
    for c in exp.conditions:
        if c.generated:
            n = len(c.changes)
            where = f"{_rel(c.folder, root)}/  built from {_rel(c.base, root)}, {n} change{'s' if n != 1 else ''}"
        else:
            where = f"{_rel(c.folder, root)}  as written (the folder is not changed)"
        say(f"  {c.name:<{width}}  {where}")
        say(f"  {'':<{width}}  {out.paint.dim('study hash ' + c.study_hash[:19] + '…')}")
    say("")

    say("Differences, file by file")
    first = exp.conditions[0]
    for c in exp.conditions[1:]:
        diffs = differences(first, c)
        say(f"  {first.name} → {c.name}:" + ("" if diffs else "  none — the same team"))
        for d in diffs:
            say(f"    {d.file:<24} {d.kind:<8} {out.paint.dim(d.detail)}".rstrip())
    say("")

    say(f"Order of runs: permuted rounds from seed {exp.seed}, each round one run of every condition")
    slots = plan_runs(exp)
    rounds = {}
    for s in slots:
        rounds.setdefault(s.round, []).append(s)
    for r, row in rounds.items():
        say(f"  round {r:>3}  " + " · ".join(s.condition for s in row))
    say("")

    _estimate(exp, out, prices if prices is not None else load_prices(), walks, ceiling_of(exp, max_cost)[0])
    for w in warnings:
        say(out.paint.yellow(f"! {_where(w, root)}  {w.message}"))
    say(out.paint.dim("Nothing was run and nothing was spent."))
    return 0


def per_run(exp: Experiment, prices, walks: int = WALKS, reply_tokens: int | None = REPLY_TOKENS
            ) -> dict[str, Estimate]:
    """Each condition's estimate for one run, from rehearsal walks of its flow."""
    out = {}
    for c in exp.conditions:
        seeds = [_draw("teamrunner/estimate/v1", exp.seed, c.name, i) % (2 ** 32) for i in range(walks)]
        out[c.name] = estimate(_team(c.report), c.inputs, seeds if reply_tokens else seeds[:2], prices, reply_tokens)
    return out


def _estimate(exp: Experiment, out, prices, walks: int, ceiling: float | None) -> None:
    say = out.say
    typical = per_run(exp, prices, walks)
    worst = per_run(exp, prices, walks, reply_tokens=None)
    reps = exp.replications

    def total(table):
        costs = [e.per_run_usd for e in table.values()]
        return None if any(x is None for x in costs) else sum(costs) * reps

    est, most = total(typical), total(worst)
    from .evaluate import estimate_one
    from .metrics import Evaluator
    evaluators = [m for m in exp.measures if isinstance(m, Evaluator)]
    evaluating = 0.0
    for ev in evaluators:
        for name, e in typical.items():
            one = estimate_one(ev, e.tokens_out, prices)
            evaluating = None if one is None or evaluating is None else evaluating + one * ev.evaluations * reps
    if est is not None:
        est = None if evaluating is None else est + evaluating
    if say_estimate(out, est, ceiling):
        say(out.paint.yellow(f"  ! over the ceiling: a run would not start. {RAISE}"))
    width = max(len(n) for n in typical)
    for name, e in typical.items():
        say(f"  {name:<{width}}  ≈ {_money(e.per_run_usd)} a run × {reps}"
            f"   {out.paint.dim(f'{e.calls:.0f} calls, ≈ {e.tokens_in:,} tokens in, {e.tokens_out:,} out')}")
    if evaluators:
        n = sum(ev.evaluations for ev in evaluators) * exp.runs
        say(f"  {'evaluating':<{width}}  ≈ {_money(evaluating)}   "
            + out.paint.dim(f"{n} evaluations: {', '.join(ev.name for ev in evaluators)}"))
    say(out.paint.dim(f"  from {walks} rehearsal walks of each flow, prompts as they "
                      f"would be sent, ≈{REPLY_TOKENS} tokens a reply, list prices (D120);"))
    say(out.paint.dim("  it is made again from real costs after round 1"))
    say(f"  at most ≈ {_money(most)} if every reply ran to its max_tokens")
    for line in litellm_max_lines(exp):
        say(f"    {line}")
    unpriced = sorted({m for e in typical.values() for m in e.unpriced})
    if unpriced:
        say(out.paint.yellow(f"! no list price for {', '.join(unpriced)}; add it to {prices.shown}"))
    say("")
