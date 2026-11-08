"""What a run prints while it runs (D101), read like minutes.

    run · multi_agent_debate · default input topic: "Remote work makes…"
    ✓ 1 model · 1 key
    rounds 1 of 3
      for                                               4.2s   $0.0031
        "Remote work saves two hours of commuting a day…"
    judge                  winner = against              3.1s   $0.0024

The console reads the same events the log writes, so a run's console can
be replayed from its `events.jsonl` (D102). Names, never ids (D88).
"""

from __future__ import annotations

import sys
import textwrap

QUOTE = 90


class Paint:
    def __init__(self, stream):
        on = hasattr(stream, "isatty") and stream.isatty()
        code = (lambda c: lambda s: f"\033[{c}m{s}\033[0m") if on else (lambda c: str)
        self.bold, self.dim, self.red = code("1"), code("2"), code("31")
        self.green, self.yellow = code("32"), code("33")


def money(cost: float | None) -> str:
    if cost is None:
        return "$?"
    return f"${cost:.4f}" if cost < 1 else f"${cost:.2f}"


def seconds(ms: int | None) -> str:
    return "" if ms is None else f"{ms / 1000:.1f}s"


class Console:
    def __init__(self, verbose: bool = False, stream=None):
        self.out = stream or sys.stdout
        self.paint = Paint(self.out)
        self.verbose = verbose
        self.headings: list[tuple] = []  # the pass headings printed so far, outermost first
        # Learnt from `run_started`, so a replay of events.jsonl prints the same (D102):
        self.deciders: dict[str, str] = {}  # member label -> the output its decision reads
        self.shown_outputs: dict[str, set[str]] = {}  # member -> its choice, number and flag outputs
        self.trips: list[dict] = []  # side trips of the turn in progress, shown after its line
        self.level = 0  # indentation of the last turn shown

    def say(self, text: str = "") -> None:
        print(text, file=self.out)

    # --- the header and the check ---

    def header(self, team_name: str, inputs: dict) -> None:
        parts = [f"run · {self.paint.bold(team_name)}"]
        for name, item in inputs.items():
            shown = item.value if isinstance(item.value, str) else str(item.value)
            shown = shown.replace("\n", " ").strip()
            shown = shown if len(shown) <= 60 else shown[:57] + "…"
            parts.append(f"{item.source} input {name}: \"{shown}\"")
        self.say(" · ".join(parts))

    def check(self, check, keys) -> None:
        p = self.paint
        for note in keys.notes:
            self.say(p.dim(note))
        if check.ok:
            n_models, n_keys = len(check.models), len(check.keys)
            line = f"✓ {n_models} model{'s' if n_models != 1 else ''}"
            if all(m.get("available") is None for m in check.models) and not check.keys:
                line += " (not called)"
            if n_keys:
                line += f" · {n_keys} key{'s' if n_keys != 1 else ''}"
            n_tools = len(check.tools)
            if n_tools:
                line += f" · {n_tools} tool{'s' if n_tools != 1 else ''}"
            self.say(p.green(line))
        else:
            self.say(p.red("✗ the run cannot start:"))
            for prob in check.problems:
                self.say(f"  {p.bold(prob.what):<28} {prob.wrong}")
                self.say(f"  {'':<28} {p.dim(prob.fix)}")
        for line in backup_lines(getattr(check, "backups", None) or []):
            self.say(p.yellow(line))
        for warning in check.warnings:
            self.say(p.yellow(f"! {warning}"))

    # --- events as they happen ---

    def on_event(self, e: dict) -> None:
        handler = getattr(self, "_" + e["event"], None)
        if handler:
            handler(e)

    def _run_started(self, e: dict) -> None:
        self.deciders = dict(e.get("deciders") or {})
        for m in e.get("members") or []:
            self.shown_outputs[m["name"]] = {k for k, f in (m.get("output") or {}).items()
                                             if f["type"] != "text"}

    def _agent_message(self, e: dict) -> None:
        p = self.paint
        repeated = [tuple(b.values()) for b in e.get("blocks") or [] if b["of"] > 1]
        level = len(repeated)
        for i, heading in enumerate(repeated):
            if i >= len(self.headings) or self.headings[i] != heading:
                self.headings = self.headings[:i] + [heading]
                self.say("  " * i + p.bold(f"{heading[0]} {heading[1]} of {heading[2]}"))
        self.headings = self.headings[:level] if len(self.headings) > level else self.headings

        name = e["from"] + (f":{e['stage']}" if e.get("stage") else "")
        what: list[tuple[str, str]] = []  # (as shown, as plain text) so columns line up in colour too
        output = e.get("output") or {}
        field = self.deciders.get(name)
        if field and field in output:
            option = str(output[field])
            paint = p.green if option in ("pass", "done", "approve") else \
                p.red if option in ("fail", "stop") else str
            what.append((paint(option), option))
        else:
            for k, v in output.items():
                if k in self.shown_outputs.get(e["from"], ()):
                    what.append((f"{k} = {v}",) * 2)
        attempt = e.get("attempt") or {}
        if attempt.get("number", 1) > 1:
            text = f"try {attempt['number']}" + (f" of {attempt['of']}" if attempt.get("of") else "")
            what.append((text, text))
        indent = "  " * level
        shown = "  ".join(w[0] for w in what)
        plain = "  ".join(w[1] for w in what)
        gap = " " * max(1, 28 - len(indent + name))
        tail = " " * max(1, 30 - len(plain))
        self.say(f"{indent}{p.bold(name)}{gap}{shown}{tail}"
                 f"{seconds(e.get('latency_ms')):>7} {money(e.get('cost_usd')):>9}")
        self.level = level
        for trip in self.trips:
            self._tool_line(trip, indent + "  ")
        self.trips = []
        content = (e.get("content") or "").strip()
        if self.verbose:
            for line in textwrap.wrap(content, 96) or [""]:
                self.say(f"{indent}    {line}")
            usage = e.get("usage") or {}
            self.say(p.dim(f"{indent}    {e.get('model_resolved')} · {usage.get('input', 0)} in, "
                           f"{usage.get('output', 0)} out"))
        elif content:
            quote = " ".join(content.split())
            quote = quote if len(quote) <= QUOTE else quote[:QUOTE - 1] + "…"
            self.say(f"{indent}    {p.dim('“' + quote + '”')}")

    def _tool_call(self, e: dict) -> None:
        if e.get("mandatory"):
            self._tool_line(e, "  " * self.level + "  ")
        else:
            self.trips.append(e)  # printed under the asker once its turn is shown

    def _tool_line(self, e: dict, indent: str) -> None:
        p = self.paint
        args = e.get("arguments")
        first = next(iter(args.values()), "") if isinstance(args, dict) and args else args
        request = " ".join(str(first).split())
        request = request if len(request) <= 50 else request[:49] + "…"
        verb = {"web_search_brave": "search", "read_page": "read", "read_file": "read",
                "run_code": "run", "read_email": "read", "save_draft": "draft"}.get(e["tool"], e["tool"])
        if e["access"] == "allowed" and e.get("result") is not None:
            allowance = e.get("allowance") or {}
            mark = p.green(f"✓ {allowance.get('used')} of {allowance.get('of')}")
        elif e["access"] == "allowed":
            mark = p.red("✗ the tool failed")
        else:
            mark = p.red(f"✗ {e['reason']}")
        asker = f"{e['from']} → " if e.get("mandatory") else ""
        self.say(f"{indent}└ {asker}{p.bold(e['toolkeeper'])}   {verb} “{request}”   {mark}")
        if self.verbose and e.get("result"):
            result = " ".join(str(e["result"]).split())
            self.say(p.dim(f"{indent}    {result[:160]}{'…' if len(result) > 160 else ''}"))

    def _route_decision(self, e: dict) -> None:
        if self.verbose:
            note = " (the count ran out)" if e.get("bound_applied") else ""
            self.say(self.paint.dim(f"    {e['decider']}: {e['option']} → {e['to']}{note}"))

    def _retry(self, e: dict) -> None:
        if self.verbose:
            who = e["instance_path"].rsplit("/", 1)[-1]
            if e["kind"] == "output":
                self.say(self.paint.yellow(f"    ↻ {who}'s reply did not fit: {e['reason']}"))
            else:
                self.say(self.paint.yellow(f"    ↻ {who}'s model failed ({e['reason']}); "
                                           f"trying again in {e['wait_ms'] / 1000:.0f}s"))

    def _warning(self, e: dict) -> None:
        """Validate's warnings, such as links outside the package (D94), every run. The
        check's own warnings carry no `where`; they were printed with the check."""
        if e.get("where"):
            self.say(self.paint.yellow(f"! {e['where']}  {e['message']}"))

    # --- the end ---

    def totals(self, record: dict, folder, output: str) -> None:
        p = self.paint
        status = record["status"]
        colour = p.green if status == "completed" else p.yellow if status == "stopped" else p.red
        self.say()
        cost = "≈ " + money(record["cost_usd"])  # D120: list prices, not what was charged
        if record.get("cost_tools_usd"):  # D118: show what the tools added
            cost += f" (models {money(record.get('cost_models_usd'))} · tools {money(record['cost_tools_usd'])})"
        self.say(" · ".join([colour(status), f"{record['turns']} turns", seconds(record["latency_ms"]), cost]))
        if record.get("reason"):
            self.say(colour(record["reason"]))
        self.say(p.dim(str(folder)))
        self.say()
        self.say(output.rstrip())


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def backup_lines(backups: list[dict]) -> list[str]:
    """Members on a later choice of model, grouped when they share it (D135):
    `↪ for and against run on anthropic/claude-sonnet-4-5, their 2nd choice — openai/gpt-6-luna: openai does not serve it`."""
    groups: dict[tuple, list[str]] = {}
    for b in backups:
        skipped = tuple((s["model"], s["reason"]) for s in b["skipped"])
        groups.setdefault((b["model"], b["choice"], skipped), []).append(b["member"])
    lines = []
    for (model, choice, skipped), who in groups.items():
        names = who[0] if len(who) == 1 else ", ".join(who[:-1]) + " and " + who[-1]
        verb, whose = ("runs", "its") if len(who) == 1 else ("run", "their")
        why = "; ".join(f"{m}: {reason}" for m, reason in skipped)
        lines.append(f"↪ {names} {verb} on {model}, {whose} {_ordinal(choice)} choice — {why}")
    return lines
