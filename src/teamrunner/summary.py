# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""The summary (D138 (11), SPEC §5.5): it describes, it does not test.

Per measure and condition: n, mean, SD and a 95% interval (Student's t for
numbers; Wilson for yes/no measures and for each answer of a choice), plus
marginal means when there are two or more factors. Computed fresh from the
run folders every time, never stored. It says what it left out, and why.
No p-values: tests belong in the researcher's own analysis.
"""

from __future__ import annotations

import math
from collections import Counter
from pathlib import Path

from .evaluate import current
from .metrics import AnswerKey, Evaluator, OutputMeasure

Z = 1.959964
# Student's t, 97.5th percentile, by degrees of freedom; above 30, the normal value
T = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
     11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093,
     20: 2.086, 21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056, 27: 2.052, 28: 2.048,
     29: 2.045, 30: 2.042}


def mean_interval(values: list[float]) -> tuple[float, float | None, tuple[float, float] | None]:
    n = len(values)
    mean = sum(values) / n
    if n < 2:
        return mean, None, None
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))
    half = T.get(n - 1, Z) * sd / math.sqrt(n)
    return mean, sd, (mean - half, mean + half)


def wilson(k: int, n: int) -> tuple[float, float]:
    p = k / n
    centre = (p + Z * Z / (2 * n)) / (1 + Z * Z / n)
    half = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / (1 + Z * Z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


class Measure:
    """One column of numbers per run: a report of an answer key, an evaluator's field, or built in."""

    def __init__(self, label: str, kind: str, span: str = "", options: tuple = (),
                 bounds: tuple[float | None, float | None] = (None, None)):
        self.label, self.kind, self.span = label, kind, span  # kind: number | yes_no | choice
        self.options = tuple(options)  # a choice's answers in their declared order, so rows line up
        self.bounds = bounds  # the lowest and highest a value can be: an interval stays inside them
        self.values: dict[str, object] = {}  # run_id -> value (None: left out)
        self.why: Counter = Counter()


def measures_of(runs: list[tuple[Path, dict]], metrics, events_of) -> list[Measure]:
    out = []
    cost = Measure("cost_usd", "number", bounds=(0.0, None))
    took = Measure("duration_s", "number", bounds=(0.0, None))
    for folder, record in runs:
        rid = record.get("run_id")
        c = record.get("cost_usd")
        cost.values[rid] = c if c is not None else record.get("cost_known_usd")
        took.values[rid] = (record.get("latency_ms") or 0) / 1000
    for m in metrics:
        if isinstance(m, AnswerKey):
            scored = {record.get("run_id"): m.score(events_of(folder)) for folder, record in runs}
            for r in m.reports:
                yes_no = r == "correct"
                most = None if yes_no or r == "false_alarms" else float(len(m.answer))
                span = "" if most is None else f"(0–{len(m.answer)})"
                col = Measure(f"{m.name}.{r}", "yes_no" if yes_no else "number", span, bounds=(0.0, most))
                for rid, values in scored.items():
                    col.values[rid] = values.get(r)
                    if values.get(r) is None:
                        col.why["gave no answer"] += 1
                out.append(col)
        elif isinstance(m, OutputMeasure):
            col = Measure(m.name, {"number": "number", "flag": "yes_no", "choice": "choice"}[m.type],
                          options=m.options)
            for folder, record in runs:
                value = m.value(events_of(folder))
                col.values[record.get("run_id")] = value
                if value is None:
                    col.why[f"{m.source.who} never gave it"] += 1
            out.append(col)
        elif isinstance(m, Evaluator):
            made = {record.get("run_id"): current(folder, m) for folder, record in runs}
            for name, f in m.output.items():
                if f.type == "text":
                    continue
                kind = {"number": "number", "flag": "yes_no", "choice": "choice"}[f.type]
                if kind == "yes_no" and m.evaluations > 1:
                    kind = "number"  # the share of evaluations saying yes
                col = Measure(f"{m.name}.{name}", kind, options=f.options)
                for rid, lines in made.items():
                    values = [ln["output"].get(name) for ln in lines if name in (ln.get("output") or {})]
                    if not values:
                        col.values[rid] = None
                        col.why["not evaluated by its current version"] += 1
                    elif kind == "choice":
                        top = Counter(values).most_common()
                        tie = len(top) > 1 and top[0][1] == top[1][1]
                        col.values[rid] = "tie" if tie else top[0][0]
                    else:
                        col.values[rid] = sum(float(v) for v in values) / len(values)
                out.append(col)
    return out + [cost, took]


def _num(x: float) -> str:
    """Two decimals, or four for small values, so $0.0082 never reads as 0.01."""
    return f"{x:.4f}" if 0 < abs(x) < 0.1 else f"{x:.2f}"


def _fmt(x: float | None, width: int = 6) -> str:
    return f"{_num(x):>{width}}" if x is not None else f"{'—':>{width}}"


def _clip(ci: tuple[float, float], bounds) -> tuple[float, float]:
    """An interval kept to what the measure can be: a count is never below 0, nor a hit count
    above the number of answers."""
    lo, hi = ci
    if bounds[0] is not None:
        lo, hi = max(lo, bounds[0]), max(hi, bounds[0])
    if bounds[1] is not None:
        lo, hi = min(lo, bounds[1]), min(hi, bounds[1])
    return lo, hi


def cell(m: Measure, values: list) -> dict:
    """One condition's (or one level's) figures for a measure: n, and a mean with its
    interval, or a share for each answer of a choice."""
    n = len(values)
    if m.kind == "choice":
        counts = Counter(values)
        order = [o for o in m.options if o in counts] + sorted(str(a) for a in counts if a not in m.options)
        return {"n": n, "shares": [{"answer": a, "share": counts[a] / n, "ci": wilson(counts[a], n)}
                                   for a in order]}
    if m.kind == "yes_no":
        k = sum(1 for v in values if v)
        return {"n": n, "mean": k / n, "sd": None, "ci": wilson(k, n), "wilson": True}
    mean, sd, ci = mean_interval([float(v) for v in values])
    return {"n": n, "mean": mean, "sd": sd, "ci": _clip(ci, m.bounds) if ci else None}


COSTLY = 3  # a run is flagged when it cost more than this many times its condition's typical run
COSTLY_FROM = 3  # the fewest runs a condition needs before "typical" means anything


def _cost(record: dict) -> float | None:
    c = record.get("cost_usd")
    return c if c is not None else record.get("cost_known_usd")


def costly(runs: list[tuple[Path, dict]]) -> list[dict]:
    """Runs that cost far more than their condition's typical (median) run: flagged, never
    cut. A cost ceiling per run would drop the expensive condition's long runs and bias
    the summary; the flag only points at them."""
    by_cond: dict[str, list[tuple[Path, dict, float]]] = {}
    for folder, record in runs:
        c = _cost(record)
        if c is not None:
            by_cond.setdefault(record["experiment"]["condition"], []).append((folder, record, c))
    found = []
    for cond, items in by_cond.items():
        if len(items) < COSTLY_FROM:
            continue
        values = sorted(c for _, _, c in items)
        mid = len(values) // 2
        typical = values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2
        if typical <= 0:
            continue
        for folder, record, c in items:
            if c > COSTLY * typical:
                found.append({"folder": folder, "run_id": record.get("run_id"), "condition": cond,
                              "replicate": record["experiment"].get("replicate"),
                              "cost_usd": c, "typical_usd": typical})
    return sorted(found, key=lambda f: (f["condition"], f["replicate"] or 0))


def table(exp, runs: list[tuple[Path, dict]], metrics, events_of) -> list[dict]:
    """Every measure's figures by condition, and by factor level when there are two or more
    factors: what the summary prints and the browser page shows (D141 (4))."""
    by_cond = {c.name: [] for c in exp.conditions}
    for folder, record in runs:
        by_cond.setdefault(record["experiment"]["condition"], []).append(record.get("run_id"))
    levels_of = {r.get("run_id"): r["experiment"].get("levels") or {} for _, r in runs}
    out = []
    for m in measures_of(runs, metrics, events_of):
        rows = []
        for cond, ids in by_cond.items():
            values = [m.values[i] for i in ids if m.values.get(i) is not None]
            rows.append({"condition": cond, **(cell(m, values) if values else {"n": 0})})
        marginals = []
        if exp.factors and len(exp.factors) >= 2 and m.kind != "choice":
            for factor, levels in exp.factors.items():
                for level in levels:
                    values = [float(m.values[i]) for i, lv in levels_of.items()
                              if lv.get(factor) == level and m.values.get(i) is not None]
                    if values:
                        mean, sd, ci = mean_interval(values)
                        marginals.append({"factor": factor, "level": level, "n": len(values), "mean": mean,
                                          "sd": sd, "ci": _clip(ci, m.bounds) if ci else None})
        out.append({"label": m.label, "span": m.span, "kind": m.kind, "rows": rows,
                    "marginals": marginals, "left_out": dict(sorted(m.why.items()))})
    return out


def summary(exp, runs: list[tuple[Path, dict]], left_out: Counter, metrics, out, events_of,
            spent_runs: float | None = None, spent_evaluating: float | None = None, note: str = "") -> None:
    """Print the summary of the runs that count."""
    from .plan import _money
    say, paint = out.say, out.paint
    money = ""
    if spent_runs is not None:
        money = f" · {_money(spent_runs)} runs" + (f" + {_money(spent_evaluating)} evaluating"
                                                   if spent_evaluating else "")
    title = exp.name or exp.folder.name
    say(f"{title} · {len(runs)} of {exp.runs} runs completed{money}{note}")
    measures = table(exp, runs, metrics, events_of)
    width = max([len(r["condition"]) for r in measures[0]["rows"]] + [12]) + 2  # cost_usd is always there
    for m in measures:
        say("")
        head = f"{m['label']} {m['span']}".strip()
        w = max(width, len(head) - 1)  # every row's first column, so `n` lines up
        if m["kind"] == "choice":
            say(f"{head:<{w + 2}}{'n':>4}   share by answer, 95% interval")
        else:
            say(f"{head:<{w + 2}}{'n':>4}  {'mean':>7} {'sd':>7}   95% interval")
        for row in m["rows"]:
            cond, n = row["condition"], row["n"]
            if not n:
                say(f"  {cond:<{w}}{0:>4}")
            elif m["kind"] == "choice":
                parts = [f"{s['answer']} {s['share']:.0%} [{s['ci'][0]:.0%}, {s['ci'][1]:.0%}]"
                         for s in row["shares"]]
                say(f"  {cond:<{w}}{n:>4}   " + " · ".join(parts))
            elif m["kind"] == "yes_no":
                lo, hi = row["ci"]
                say(f"  {cond:<{w}}{n:>4}  {row['mean']:>6.2f} {'':>6}   [{lo:.2f}, {hi:.2f}]  (Wilson)")
            else:
                ci = row["ci"]
                interval = f"[{_num(ci[0])}, {_num(ci[1])}]" if ci else ""
                say(f"  {cond:<{w}}{n:>4}  {_fmt(row['mean'], 7)} {_fmt(row['sd'], 7)}   {interval}")
        if m["marginals"]:
            say(f"  {'marginal means':<{w}}")
        for row in m["marginals"]:
            ci = row["ci"]
            interval = f"[{_num(ci[0])}, {_num(ci[1])}]" if ci else ""
            say(f"    {row['factor']}={row['level']:<{max(w - len(row['factor']) - 3, 1)}}{row['n']:>4}  "
                f"{_fmt(row['mean'], 7)} {_fmt(row['sd'], 7)}   {interval}")
        for why, k in m["left_out"].items():
            say(paint.dim(f"  left out: {k} run{'s' if k != 1 else ''} {why}"))
    flagged = costly(runs)
    if flagged:
        say("")
        say(paint.yellow(f"! {len(flagged)} run{'s' if len(flagged) != 1 else ''} cost over {COSTLY}× "
                         "the typical run of its condition (counted; worth reading):"))
        for f in flagged:
            say(f"  {f['condition']} replicate {f['replicate']}  {_money(f['cost_usd'])}, typical "
                f"{_money(f['typical_usd'])}  " + paint.dim(f["folder"].name))
    if left_out:
        say("")
        parts = [f"{k} {why}" for why, k in sorted(left_out.items())]
        say(paint.dim(f"Not counted: {', '.join(parts)}."))
