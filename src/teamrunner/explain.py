# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""`teamrunner explain` (D62, D96, D109): what a team folder will do, in words
a non-coder can check — who acts when, who sees what, who may use which
tool, what it takes, how many turns it can run — and its flow as a picture.

    teamrunner explain debate              # in words
    teamrunner explain debate --text       # the flow as Mermaid, to paste anywhere
    teamrunner explain debate --diagram    # an HTML page drawing the flow, opened in the browser
"""

from __future__ import annotations

import asyncio
import html
import json
from pathlib import Path

from . import ir
from .engine import Engine, Result
from .ir import DONE, STOP
from .runs import all_runs, runs_folder

MERMAID = "https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js"


def _join(names) -> str:
    names = list(names)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _calls(n: int) -> str:
    return f"{n} call{'s' if n != 1 else ''}"


def _terms(terms: ir.Terms) -> str:
    g = dict(terms.guardrails)
    parts = [f"{_calls(g.pop('calls_per_turn'))} a turn"]
    if "calls_per_run" in g:
        parts.append(f"{_calls(g.pop('calls_per_run'))} a run")
    for key, value in g.items():
        if key == "network":
            parts.append("internet on" if value else "no internet")
        elif key == "timeout":
            parts.append(f"{value} s each")
        elif isinstance(value, list):
            parts.append(f"{key.replace('_', ' ')}: {', '.join(map(str, value))}")
        else:
            parts.append(f"{key.replace('_', ' ')} {value}")
    return ", ".join(parts)


# --- how many turns ---------------------------------------------------------------------

def turn_range(team: ir.Team) -> tuple[int, int, bool]:
    """The fewest and most member turns the flow can take, found by running the
    engine twice: once always leaving, once always looping until the counts force it on.
    The bool says whether the flow can stop early."""
    choices = {}
    for block in [team.flow.main, *team.flow.blocks.values()]:
        for i, d in block.decisions.items():
            choices[(id(block), i)] = (block, i, d)

    def pick(block, i, d: ir.Decision, loop: bool) -> str:
        options = list(d.routes)
        looping = [o for o in options if isinstance(d.routes[o], int) and d.routes[o] <= i]
        leaving = [o for o in options if o not in looping and d.routes[o] != STOP]
        if loop and looping:
            return looping[0]
        return (leaving or options)[0]

    def count(loop: bool) -> int:
        turns = 0

        async def actor(turn):
            nonlocal turns
            if turn.member.kind != "toolkeeper":
                turns += 1
            output = {}
            for block, i, d in choices.values():
                if block.steps[i] is turn.step:
                    output[d.field] = pick(block, i, d, loop)
            return Result(turn, output, request={} if turn.must_ask else None)

        asyncio.run(Engine(team, actor, lambda e: None).run())
        return turns

    can_stop = any(STOP in d.routes.values() or (d.bound and d.bound.target == STOP)
                   for _, _, d in choices.values()) or any(
        s.kind == STOP for b in [team.flow.main, *team.flow.blocks.values()] for s in b.steps)
    return count(False), count(True), can_stop


# --- the flow in words ---------------------------------------------------------------------

def _step_words(team: ir.Team, step: ir.Step) -> str:
    if step.kind == "block":
        text = step.name
    elif step.kind in (DONE, STOP):
        return {"done": "done", "stop": "stop the run"}[step.kind]
    else:
        member = team.members[step.name]
        text = f"{step.name} (toolkeeper)" if member.kind == "toolkeeper" else step.label
    return text + (f", {step.repeat} times" if step.repeat > 1 else "")


def _target(block: ir.Block, route) -> str:
    if route == DONE:
        return "the block ends" if block.name else "the run ends"
    if route == STOP:
        return "the run stops"
    return block.steps[route].label


def _block_lines(team: ir.Team, block: ir.Block, indent: str) -> list[str]:
    lines = []
    seq = block.steps[:block.sequence_length]
    lines.append(indent + " → ".join(_step_words(team, s) for s in seq))
    for i, d in block.decisions.items():
        decider = block.steps[i].label
        for option, route in d.routes.items():
            back = isinstance(route, int) and route <= i
            lines.append(f"{indent}  {decider} answers {option}: "
                         + ("back to " + block.steps[route].label if back else _target(block, route)))
        if d.bound:
            then = (f"it goes on to {block.steps[d.bound.target].label}" if isinstance(d.bound.target, int)
                    else _target(block, d.bound.target))
            lines.append(f"{indent}  the {_nth(d.bound.count)} time {decider} decides here, "
                         f"a choice that would go back is overridden: {then}")
    for i, s in enumerate(block.steps):
        if s.kind == "member" and team.members[s.name].kind == "toolkeeper" and i > 0:
            asker = block.steps[i - 1].label
            lines.append(f"{indent}  {asker} must end its turn with a request to {s.name}; "
                         f"the reply goes to {block.steps[i - 1].name} alone")
    return lines


def _nth(n: int) -> str:
    return {1: "first", 2: "second", 3: "third"}.get(n, f"{n}th")


def explain(team: ir.Team) -> str:
    out = [f"{team.name}" + (f" — {team.description}" if team.description else ""), ""]

    workers = [m for m in team.members.values() if m.kind != "toolkeeper"]
    keepers = [m for m in team.members.values() if m.kind == "toolkeeper"]
    out.append("Members")
    for m in workers:
        decides = ", ".join(f"{f.name}: {' or '.join(f.options)}" for f in m.output.values() if f.type == "choice")
        line = f"  {m.name} — {m.description or 'no description'} · {m.model.value}"
        out.append(line + (f" · answers {decides}" if decides else ""))
    if keepers:
        out.append("")
        out.append("Toolkeepers (no model; each decides by its access table)")
        for k in keepers:
            out.append(f"  {k.name} (toolkeeper) holds {k.tool}")
            for name, t in k.access.items():
                out.append(f"    {name} may use it: {_terms(t)}" if t else f"    {name}: refused")
            out.append(f"    others: {'may use it: ' + _terms(k.others) if k.others else 'no access'}")

    out += ["", "What it takes"]
    if not team.inputs:
        out.append("  no inputs: it runs as it is")
    for spec in team.inputs.values():
        users = [m.name for m in workers if spec.name in m.inputs]
        default = " ".join(str(spec.default).split()) if spec.has_default else None
        shown = (default if len(default) <= 60 else default[:59] + "…") if default else None
        out.append(f"  {spec.name} — used by {_join(users)}; "
                   + (f"default: \"{shown}\"" if shown else "no default: every run must give it")
                   + (" · --message fills it" if team.message_goes_to == spec.name else ""))

    out += ["", "Who sees what",
            "  Everyone reads everything said. A toolkeeper's reply goes only to whoever asked."]

    out += ["", "Who acts when" + (" (chair.md)" if team.flow.source == "chair.md" else
                                   " (no chair.md: one turn each, in filename order)")]
    out += _block_lines(team, team.flow.main, "  ")
    for name, block in team.flow.blocks.items():
        out.append(f"  {name}:")
        out += _block_lines(team, block, "    ")

    fewest, most, can_stop = turn_range(team)
    out += ["", "How long"]
    span = f"{fewest} turns" if fewest == most else f"{fewest} to {most} turns"
    out.append(f"  {span} of members" + (", or fewer if the flow stops early" if can_stop else ""))
    try:
        _, runs = runs_folder(team.folder)
        past = [r for r in all_runs(runs) if r.run.get("team") == team.name
                and r.run.get("status") == "completed" and not r.run.get("rehearsal")
                and r.run.get("cost_usd") is not None]
    except Exception:
        past = []
    if past:
        cost = sum(r.run["cost_usd"] for r in past) / len(past)
        secs = sum(r.run.get("latency_ms", 0) for r in past) / len(past) / 1000
        out.append(f"  its {len(past)} completed run{'s' if len(past) != 1 else ''} so far averaged "
                   f"≈ ${cost:.4f} at list prices, and {secs:.0f} s")
    else:
        out.append("  no completed runs yet to say what it costs; `teamrunner run --rehearse` tries the flow for free")
    return "\n".join(out)


# --- the flow as a picture (Mermaid) -------------------------------------------------------

def mermaid(team: ir.Team) -> str:
    lines = ["flowchart TD"]
    counter = [0]

    def node_id() -> str:
        counter[0] += 1
        return f"n{counter[0]}"

    def label(text: str) -> str:
        return text.replace('"', "'")

    def draw(block: ir.Block) -> None:
        ids = []
        for i, s in enumerate(block.steps):
            nid = node_id()
            ids.append(nid)
            word = _step_words(team, s)
            if s.kind == "block":
                lines.append(f'  subgraph {nid}["{label(word)}"]')
                lines.append("  direction TB")
                draw(team.flow.blocks[s.name])
                lines.append("  end")
            elif s.kind in (DONE, STOP):
                lines.append(f'  {nid}(["{s.kind}"])')
            elif team.members[s.name].kind == "toolkeeper":
                lines.append(f'  {nid}{{{{"{label(word)}"}}}}')
            elif i in block.decisions:
                lines.append(f'  {nid}{{"{label(word)}"}}')
            else:
                lines.append(f'  {nid}["{label(word)}"]')
        end_id = None
        for i in range(block.sequence_length - 1):
            if i not in block.decisions and block.steps[i].kind not in (DONE, STOP):
                lines.append(f"  {ids[i]} --> {ids[i + 1]}")
        def to(route) -> str:
            nonlocal end_id
            if isinstance(route, int):
                return ids[route]
            end_id = end_id or node_id()
            return end_id

        for i, d in block.decisions.items():
            counted = False  # the count is written on the first arrow that shares its target
            for option, route in d.routes.items():
                tag = option
                if d.bound and d.bound.target == route and not counted:
                    tag, counted = f"{option}, or the {_nth(d.bound.count)} time", True
                lines.append(f'  {ids[i]} -- "{label(tag)}" --> {to(route)}')
            if d.bound and not counted:
                lines.append(f'  {ids[i]} -- "the {_nth(d.bound.count)} time" --> {to(d.bound.target)}')
        if end_id:
            word = "end" if block.name else "end of run"
            stop = any(r == STOP for d in block.decisions.values() for r in d.routes.values())
            lines.append(f'  {end_id}(["{"stop" if stop else word}"])')

    draw(team.flow.main)
    return "\n".join(lines)


def diagram_page(team: ir.Team) -> str:
    """A page that draws the flow (D96). Mermaid's script loads from a CDN, so
    the page needs the internet when it is opened; it can be saved as SVG from there."""
    text = html.escape(mermaid(team))
    title = html.escape(team.name)
    words = html.escape(explain(team))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{title} — flow</title>
<style>
 body {{ font: 15px/1.5 system-ui, sans-serif; margin: 2em auto; max-width: 980px; padding: 0 1em; }}
 pre.words {{ white-space: pre-wrap; background: #f6f6f4; padding: 1em; border-radius: 6px; }}
 .legend span {{ margin-right: 1.5em; }}
</style></head><body>
<h1>{title}</h1>
<p class="legend"><span>▭ a member's turn</span><span>◇ a member who decides</span>
<span>⬡ a toolkeeper step</span><span>⬭ done or stop</span></p>
<pre class="mermaid">{text}</pre>
<h2>In words</h2><pre class="words">{words}</pre>
<script src="{MERMAID}"></script>
<script>mermaid.initialize({{ startOnLoad: true, flowchart: {{ htmlLabels: false }} }});</script>
</body></html>
"""


def json_report(report) -> str:
    """`validate --json` (D62): every problem with its file, line, value and fix."""
    top = report.package.top

    def item(p):
        return {"file": p.file.relative_to(top.parent).as_posix() if p.file.is_relative_to(top.parent) else p.file.as_posix(),
                "line": p.line, "message": p.message, "value": p.value, "fix": p.fix}

    return json.dumps({
        "team": report.package.team.name,
        "folder": str(report.package.team.folder),
        "ok": report.ok,
        "errors": [item(p) for p in report.problems.errors],
        "warnings": [item(p) for p in report.problems.warnings],
        "ids_added": [str(path.relative_to(top.parent)) for path, _ in report.ids_added],
    }, indent=2, ensure_ascii=False)
