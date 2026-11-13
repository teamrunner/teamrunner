# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""The flow: the body of `chair.md`, parsed and checked (SPEC §3.3, D96).

    rounds(*3) -> judge:research -> web -> judge:verdict
    rounds = for_turn -> against_turn
    for_turn = for -> fact_checker --pass,*3--> done
    fact_checker --fail--> for

Every line stands on its own: the order of the lines and their indentation
are for the reader (D147). A line is one of three things:

- a line of steps, `a -> b -> c`: the main flow;
- a line of steps after a name, `name = a -> b`: the named block `name`;
- a decision alone, `decider --option--> step`: it belongs wherever its
  decider takes a turn, and if that is in several places, with the nearest
  line above that holds the decider.

Several lines of one flow join where one ends and the next begins
(`a -> b` and `b -> c` are `a -> b -> c`). A labelled arrow inside a line
of steps is a decision too: `checker --pass--> c` goes on to `c` when the
checker says `pass`. A line starting with an arrow continues the line
above. A step is an occurrence, not a name: the same member may take
several turns in one line, and a joint or an arrow names a step by `name`
or `name:stage`, which must point to one of them.

Parsing reads the text alone. `check_flow` then looks the names up in the
team, reports what is wrong, and completes the flow (which output each
decision reads). Parallel work `[a, b]` and condition loops are 0.3.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .ir import DONE, STOP, Block, Bound, Decision, Flow, OutputField, Step
from .problems import Problems
from .vocabulary import suggest

WORD = r"[A-Za-z_][A-Za-z0-9_]*"
STEP = re.compile(rf"^(?P<name>{WORD})(?::(?P<stage>{WORD}))?(?:\((?P<args>[^)]*)\))?$")
BLOCK_DEF = re.compile(rf"^(?P<name>{WORD})\s*=(?!=)\s*(?P<rest>.*)$")
ARROW = re.compile(r"(--[^>]*?-->|->)")
COUNT = re.compile(r"^\*\s*(\d+)$")


@dataclass
class _Chain:
    """One line of steps as written (with the lines that continue it)."""
    block: str  # "" for the main flow
    line: int
    steps: list[Step] = field(default_factory=list)
    arrows: list[list[str] | None] = field(default_factory=list)  # between steps; None is `->`
    tail: tuple[list[str], str] | None = None  # `--option--> done` at the end: (labels, done|stop)


@dataclass
class _Lone:
    """A decision written on a line of its own."""
    decider: Step
    labels: list[str]
    target: Step
    line: int


@dataclass
class _Route:
    decider: int
    labels: list[str]
    where_to: int | str
    line: int


# --- parsing --------------------------------------------------------------------

def parse_flow(text: str, path, first_line: int, problems: Problems) -> Flow | None:
    """Parse a chair's script. `first_line` is the file line of the text's
    first line. Returns None if nothing usable could be read."""
    errors_before = len(problems.errors)
    written: list[tuple[str, list[tuple[str, int]]]] = []  # (block, [(text, line), ...])

    for i, raw in enumerate(text.splitlines()):
        line_no = first_line + i
        stripped = raw.split("#", 1)[0].strip()
        if not stripped:
            continue
        if stripped.startswith("-"):  # `-> b` or `--option--> b`: continues the line above
            if not written:
                problems.error(path, "a line starting with an arrow continues the line above, "
                               "but there is none", line=line_no)
                continue
            written[-1][1].append((stripped, line_no))
            continue
        definition = BLOCK_DEF.match(stripped)
        if definition:
            if not definition["rest"].strip():
                problems.error(path, f"the block `{definition['name']}` has no steps",
                               line=line_no, fix=f"write them after the `=`: `{definition['name']} = a -> b`")
                continue
            written.append((definition["name"], [(definition["rest"].strip(), line_no)]))
        else:
            written.append(("", [(stripped, line_no)]))

    chains: list[_Chain] = []
    lones: list[_Lone] = []
    for block, pieces in written:
        item = _read_line(block, pieces, path, problems)
        if isinstance(item, _Lone):
            lones.append(item)
        elif item is not None:
            chains.append(item)

    if len(problems.errors) > errors_before:
        return None
    if not any(c.block == "" for c in chains):
        problems.error(path, "this chair.md has no flow", line=first_line,
                       fix="write the steps, such as `for -> against -> judge`, "
                       "or delete the file and members take turns in filename order")
        return None

    names = list(dict.fromkeys(c.block for c in chains))  # in the order first written
    built: dict[str, Block] = {}
    routes: dict[str, list[_Route]] = {}
    for name in names:
        mine = [c for c in chains if c.block == name]
        line = first_line if name == "" else mine[0].line
        result = _join(Block(name, line=line), mine, path, problems)
        if result is not None:
            built[name], routes[name] = result
    if len(problems.errors) > errors_before:
        return None

    for lone in lones:
        _place(lone, chains, built, routes, path, problems)
    for name, block in built.items():
        _resolve(block, routes[name], path, problems)
    if len(problems.errors) > errors_before:
        return None

    blocks = {name: b for name, b in built.items() if name}
    flow = Flow(built[""], blocks, source="chair.md")
    for block in [flow.main, *blocks.values()]:
        for step in block.steps:
            if step.kind == "name":
                step.kind = "block" if step.name in blocks else "member"
    return flow


def _where(name: str) -> str:
    return f"the block `{name}`" if name else "the main flow"


def _read_line(block: str, pieces: list[tuple[str, int]], path, problems) -> _Chain | _Lone | None:
    """Read one line (and its continuations) into steps and the arrows between them."""
    tokens: list[tuple[str, int]] = []
    for n, (text, line_no) in enumerate(pieces):
        parts = [p.strip() for p in ARROW.split(text)]
        if n > 0:
            parts = parts[1:]  # a continuation starts with its arrow
        tokens += [(p, line_no) for p in parts]

    steps: list[Step] = []
    arrows: list[list[str] | None] = []
    for k, (token, line_no) in enumerate(tokens):
        if k % 2:  # an arrow
            if token == "->":
                arrows.append(None)
                continue
            labels = [label.strip() for label in token[2:-3].split(",")]
            if not any(labels) or "" in labels:
                problems.error(path, "an arrow label is empty: write `--option-->` or `--*3-->`",
                               line=line_no)
                return None
            arrows.append(labels)
            continue
        if "--" in token or token.endswith("-") or token.startswith(">"):
            problems.error(path, "this decision should read `decider --option--> step`",
                           line=line_no, value=token)
            return None
        step = _parse_step(token, path, line_no, problems, allow_repeat=True)
        if step is None:
            return None
        if steps and steps[-1].kind in (DONE, STOP):
            problems.error(path, f"nothing after `{steps[-1].kind}` would ever run", line=line_no,
                           value=step.label)
            return None
        steps.append(step)

    line = pieces[0][1]
    if len(steps) == 2 and not block and arrows[0] is not None:
        decider, target = steps
        if target.repeat > 1:
            problems.error(path, "a decision points to a step, not a repeat", line=line,
                           value=target.label, fix="repeat it in the flow instead")
            return None
        return _Lone(decider, arrows[0], target, line)

    chain = _Chain(block, line, steps, arrows)
    for k, labels in enumerate(arrows):
        if labels is None:
            continue
        if steps[k].kind in (DONE, STOP):
            problems.error(path, f"`{steps[k].name}` cannot decide anything", line=steps[k].line)
            return None
        if steps[k + 1].repeat > 1:
            problems.error(path, "a decision points to a step, not a repeat", line=steps[k + 1].line,
                           value=steps[k + 1].label, fix="repeat it in the flow instead")
            return None
    if arrows and arrows[-1] is not None and steps[-1].kind in (DONE, STOP):
        chain.tail = (arrows.pop(), steps.pop().kind)
    return chain


def _parse_step(token: str, path, line_no, problems, allow_repeat: bool) -> Step | None:
    if not token:
        problems.error(path, "a step is missing between two arrows", line=line_no)
        return None
    if token.startswith("["):
        problems.error(path, "members working at the same time (`[a, b]`) arrives in TeamRunner 0.3",
                       line=line_no, value=token, fix="write them one after another for now: `a -> b`")
        return None
    m = STEP.match(token)
    if not m:
        problems.error(path, f"`{token}` is not a step: write a name, `name:stage` or `name(*3)`",
                       line=line_no, value=token)
        return None
    name, stage, args = m["name"], m["stage"], m["args"]
    repeat = 1
    if args is not None:
        count = COUNT.match(args.strip())
        if " is " in f" {args} ":
            problems.error(path, "a loop that leaves on a condition arrives in TeamRunner 0.3",
                           line=line_no, value=token, fix="repeat it a fixed number of times: `name(*3)`")
            return None
        if not count or int(count[1]) < 1:
            problems.error(path, f"`({args})` should be a count, such as `(*3)`", line=line_no,
                           value=token)
            return None
        if not allow_repeat:
            problems.error(path, "a decision points to a step, not a repeat", line=line_no,
                           value=token, fix="repeat it in the flow instead")
            return None
        repeat = int(count[1])
    if name in (DONE, STOP):
        if stage or args is not None:
            problems.error(path, f"`{name}` takes no stage or count", line=line_no, value=token)
            return None
        return Step(name, name, line=line_no)
    return Step("name", name, stage, repeat, line_no)


def _joinable(step: Step) -> bool:
    return step.kind not in (DONE, STOP)


def _join(block: Block, chains: list[_Chain], path, problems) -> tuple[Block, list[_Route]] | None:
    """Join the lines of one flow into its sequence: a line that begins where
    another ends goes on from there."""
    where = _where(block.name)
    after: dict[int, list[int]] = {i: [] for i in range(len(chains))}
    before: dict[int, list[int]] = {i: [] for i in range(len(chains))}
    for i, a in enumerate(chains):
        if a.tail or not _joinable(a.steps[-1]):
            continue
        for j, b in enumerate(chains):
            if i != j and _joinable(b.steps[0]) and b.steps[0].label == a.steps[-1].label:
                after[i].append(j)
                before[j].append(i)

    ok = True
    for i, nexts in after.items():
        if len(nexts) > 1:
            joint = chains[i].steps[-1].label
            problems.error(path, f"two lines in {where} go on from `{joint}`", line=chains[nexts[1]].line,
                           fix=f"a step goes on to one next step; to choose between them, "
                           f"`{joint}` decides: `{joint} --option--> …`")
            ok = False
    for j, prevs in before.items():
        if len(prevs) > 1:
            joint = chains[j].steps[0].label
            problems.error(path, f"two lines in {where} lead into `{joint}`", line=chains[prevs[1]].line,
                           fix="steps run one after another: join the lines end to start, "
                           "or give each turn a stage (`name:first`, `name:second`)")
            ok = False
    if not ok:
        return None

    starts = [i for i in before if not before[i]]
    if not starts:
        problems.error(path, f"the lines of {where} go round in a circle, so it has no first step",
                       line=chains[0].line, fix="a loop is a decision: `checker --fail--> writer`")
        return None
    if len(starts) > 1:
        a, b = chains[starts[0]], chains[starts[1]]
        problems.error(path, f"the lines of {where} do not join up: one starts at "
                       f"`{a.steps[0].label}`, another at `{b.steps[0].label}`", line=b.line,
                       fix="a line that goes on from another starts with the step that one ends with: "
                       "`a -> b` and `b -> c`")
        return None

    order = [starts[0]]
    while after[order[-1]]:
        order.append(after[order[-1]][0])
    if len(order) < len(chains):
        rest = next(c for i, c in enumerate(chains) if i not in order)
        problems.error(path, f"some lines of {where} go round in a circle", line=rest.line,
                       fix="a loop is a decision: `checker --fail--> writer`")
        return None

    index_of: dict[int, list[int]] = {}
    for n, i in enumerate(order):
        chain = chains[i]
        if n == 0:
            index_of[i] = list(range(len(chain.steps)))
            block.steps.extend(chain.steps)
            continue
        joint = block.steps[-1]
        if chain.steps[0].repeat != joint.repeat:
            problems.error(path, f"`{joint.label}` is written with two different counts",
                           line=chain.line, value=chain.steps[0].label)
            return None
        start = len(block.steps) - 1
        index_of[i] = [start + k for k in range(len(chain.steps))]
        block.steps.extend(chain.steps[1:])
    block.sequence_length = len(block.steps)

    for i in order[1:]:
        joint = chains[i].steps[0].label
        if sum(s.label == joint for s in block.steps) > 1:
            problems.error(path, f"`{joint}` takes more than one turn in {where}, "
                           "so it is unclear where this line joins", line=chains[i].line,
                           value=joint,
                           fix="give each turn a stage (`name:first`, `name:second`) and use it here")
            return None

    routes: list[_Route] = []
    for i in order:
        chain, idx = chains[i], index_of[i]
        for k, labels in enumerate(chain.arrows):
            if labels is not None:
                routes.append(_Route(idx[k], labels, idx[k + 1], chain.steps[k].line))
        if chain.tail:
            labels, kind = chain.tail
            routes.append(_Route(idx[-1], labels, kind, chain.steps[-1].line))
    return block, routes


def _turns(block: Block, label: str) -> list[int]:
    return [i for i, s in enumerate(block.steps[:block.sequence_length]) if s.label == label]


def _place(lone: _Lone, chains: list[_Chain], built: dict[str, Block], routes, path, problems) -> None:
    """A decision on its own line goes where its decider takes a turn."""
    decider, target = lone.decider, lone.target
    if decider.kind in (DONE, STOP):
        problems.error(path, f"`{decider.name}` cannot decide anything", line=lone.line)
        return
    places = [name for name, block in built.items() if _turns(block, decider.label)]
    if not places:
        problems.error(path, f"`{decider.label}` does not take a turn anywhere in this flow",
                       line=lone.line, value=decider.label,
                       fix="a decision goes with the line where its decider takes a turn")
        return
    if len(places) > 1:
        above = [c for c in chains if c.line < lone.line and c.block in places
                 and any(s.label == decider.label for s in c.steps)]
        if not above:
            problems.error(path, f"`{decider.label}` takes a turn in "
                           + " and ".join(_where(p) for p in places)
                           + ", so it is unclear which this decision belongs to", line=lone.line,
                           fix="put it below the line it belongs to")
            return
        places = [max(above, key=lambda c: c.line).block]
    name = places[0]
    block, where = built[name], _where(name)

    hits = _turns(block, decider.label)
    if len(hits) > 1:
        problems.error(path, f"`{decider.label}` takes more than one turn in {where}, "
                       "so this decision could belong to either",
                       line=lone.line, value=decider.label,
                       fix="give each turn a stage (`name:first`, `name:second`) and use it here")
        return
    if target.kind in (DONE, STOP):
        where_to: int | str = target.kind
    else:
        found = [i for i, s in enumerate(block.steps) if s.label == target.label]
        if len(found) > 1:
            problems.error(path, f"`{target.label}` takes more than one turn in {where}, "
                           "so this arrow could point to either", line=lone.line,
                           value=target.label,
                           fix="give each turn a stage (`name:first`, `name:second`) and use it here")
            return
        if found:
            where_to = found[0]
        else:  # a step reached only by this decision (SPEC §3.3's `con:reply`)
            block.steps.append(target)
            where_to = len(block.steps) - 1
    routes[name].append(_Route(hits[0], lone.labels, where_to, lone.line))


def _resolve(block: Block, routes: list[_Route], path, problems) -> None:
    for route in routes:
        step = block.steps[route.decider]
        if step.repeat > 1:
            problems.error(path, f"`{step.label}` is repeated with `(*N)`, so it cannot also decide",
                           line=route.line)
            continue
        decision = block.decisions.setdefault(route.decider, Decision("", {}, line=route.line))
        for label in route.labels:
            count = COUNT.match(label)
            if count:
                if decision.bound is not None:
                    problems.error(path, f"`{step.label}` has two counted ways out; keep one",
                                   line=route.line, value=label)
                elif int(count[1]) < 1:
                    problems.error(path, "a count starts at 1", line=route.line, value=label)
                else:
                    decision.bound = Bound(int(count[1]), route.where_to)
            elif not re.fullmatch(r"[A-Za-z0-9_]+", label):
                problems.error(path, f"`{label}` is not an option: options are single words",
                               line=route.line, value=label)
            elif label in decision.routes:
                problems.error(path, f"`{step.label}` has two arrows for `{label}`; keep one",
                               line=route.line, value=label)
            else:
                decision.routes[label] = route.where_to


# --- the default flow -----------------------------------------------------------

def round_robin(workers: list[str]) -> Flow:
    """No chair.md: members take one turn each in filename order (D59)."""
    steps = [Step("member", name) for name in workers]
    return Flow(Block("", steps, sequence_length=len(steps)), source="round-robin")


# --- checking against the team ----------------------------------------------------

@dataclass
class Cast:
    """What the flow checks need to know about the team's members."""
    kinds: dict[str, str]  # name -> llm | toolkeeper | team | unreadable (already reported)
    outputs: dict[str, dict[str, OutputField]]
    may_ask: dict[str, object]  # toolkeeper name -> callable(asker) -> bool


def _loops(index: int, route) -> bool:
    """An arrow back to the decider or an earlier step is a loop (D96)."""
    return isinstance(route, int) and route <= index


def check_flow(flow: Flow, cast: Cast, path, problems: Problems) -> None:
    """Report every flow problem, and fill in which output each decision reads."""
    blocks = flow.blocks
    used_blocks: set[str] = set()
    on_stage: set[str] = set()
    names = sorted(set(cast.kinds) | set(blocks))

    for block in [flow.main, *blocks.values()]:
        for i, step in enumerate(block.steps):
            if step.kind == "block":
                used_blocks.add(step.name)
                if step.stage:
                    problems.error(path, f"`{step.label}`: a stage belongs to a member's turn, "
                                   f"and `{step.name}` is a block", line=step.line)
            elif step.kind == "member":
                if step.name not in cast.kinds:
                    hint = suggest(step.name, names)
                    problems.error(path, f"`{step.name}` is not a member of this team or a named block",
                                   line=step.line, value=step.name,
                                   fix=f"did you mean `{hint}`?" if hint else
                                   "members are named by their file names")
                    continue
                on_stage.add(step.name)
                if cast.kinds[step.name] == "toolkeeper":
                    _check_toolkeeper_step(block, i, cast, path, problems)
        for index, decision in block.decisions.items():
            _check_decision(block, index, decision, cast, path, problems)

    for name, block in blocks.items():
        if name in cast.kinds:
            problems.error(path, f"the block `{name}` has the same name as a member", line=block.line,
                           fix="rename the block")
        elif name not in used_blocks:
            problems.warning(path, f"the block `{name}` is never used", line=block.line)
    _check_recursion(flow, path, problems)

    has_subteams = "team" in cast.kinds.values()  # members may act inside them (0.3)
    for name, kind in cast.kinds.items():
        if kind == "llm" and name not in on_stage and not has_subteams:
            problems.warning(path, f"`{name}` never takes a turn in this flow",
                             fix=f"add `{name}` to the flow, or move the file out of the team")


def _check_toolkeeper_step(block: Block, i: int, cast: Cast, path, problems) -> None:
    """D85: the member just before a toolkeeper step asks it."""
    step = block.steps[i]
    if step.repeat > 1:
        problems.error(path, f"a toolkeeper step cannot be repeated: `{step.label}`", line=step.line)
    if i == 0:
        problems.error(path, f"`{step.name}` is a toolkeeper, so it cannot be the first step: "
                       "someone must ask it", line=step.line)
        return
    if i >= block.sequence_length:
        problems.error(path, f"a decision cannot point to the toolkeeper `{step.name}`: "
                       "the member before it must ask it", line=step.line)
        return
    asker = block.steps[i - 1]
    if asker.kind != "member" or asker.name not in cast.kinds:
        problems.error(path, f"the step before the toolkeeper `{step.name}` should be the member who asks it",
                       line=step.line)
        return
    if cast.kinds[asker.name] == "unreadable":
        return
    if i - 1 in block.decisions:
        problems.error(path, f"`{asker.label}` decides here, so its turn cannot also end with "
                       f"a request to `{step.name}`", line=step.line,
                       fix=f"give the request its own turn: `{asker.name}:ask -> {step.name} -> {asker.label}`")
        return
    if cast.kinds[asker.name] == "toolkeeper":
        problems.error(path, f"`{step.name}` follows another toolkeeper, `{asker.name}`; "
                       "a member must ask it", line=step.line)
        return
    if not cast.may_ask[step.name](asker.name):
        problems.error(path, f"`{asker.name}` must ask `{step.name}` here, but `{step.name}`'s "
                       f"`access:` does not let `{asker.name}` in", line=step.line,
                       fix=f"add `{asker.name}: standard` under `access:` in {step.name}.md")
    after = block.steps[i + 1] if i + 1 < block.sequence_length else None
    if after is not None and after.kind == "member" and after.name != asker.name:
        problems.warning(path, f"`{step.name}`'s reply goes to `{asker.name}`, who asked, "
                         f"not to `{after.name}`, who speaks next", line=step.line,
                         fix=f"if `{after.name}` needs the result, let `{asker.name}` pass it on: "
                         f"`{asker.label} -> {step.name} -> {asker.name}:… -> {after.label}`")


def _check_decision(block: Block, index: int, decision: Decision, cast: Cast, path, problems) -> None:
    step = block.steps[index]
    if step.kind != "member" or step.name not in cast.kinds:
        if step.kind == "block":
            problems.error(path, f"`{step.name}` is a block, and only a member decides",
                           line=decision.line)
        return
    if cast.kinds[step.name] == "unreadable":
        return
    if cast.kinds[step.name] == "toolkeeper":
        problems.error(path, f"`{step.name}` is a toolkeeper, and a toolkeeper does not decide",
                       line=decision.line)
        return
    choices = {f.name: f for f in cast.outputs.get(step.name, {}).values() if f.type == "choice"}
    labels = set(decision.routes)
    if not choices:
        problems.error(path, f"`{step.name}` decides here, but its file declares no options",
                       line=decision.line,
                       fix=f"add, for example, `verdict: [pass, fail]` under `output:` in {step.name}.md")
        return
    if not labels:
        problems.error(path, f"`{step.label}` has a counted way out but no options",
                       line=decision.line,
                       fix=f"add an arrow for each option, such as `{step.label} --pass--> …`")
        return
    fitting = [f for f in choices.values() if labels <= set(f.options)]
    if len(fitting) > 1:
        problems.error(path, f"the options on `{step.label}`'s arrows fit more than one of its outputs: "
                       + ", ".join(f"`{f.name}`" for f in fitting), line=decision.line,
                       fix="give the outputs different options")
        return
    if not fitting:
        every = sorted({o for f in choices.values() for o in f.options})
        for label in sorted(labels - set(every)) or sorted(labels):
            hint = suggest(label, every)
            problems.error(path, f"`{label}` is not one of `{step.name}`'s options",
                           line=decision.line, value=label,
                           fix=f"did you mean `{hint}`?" if hint else
                           "its options are " + ", ".join(f"`{o}`" for o in every))
        return
    out = fitting[0]
    decision.field = out.name
    for option in out.options:
        if option not in decision.routes:
            problems.error(path, f"`{step.label}` can answer `{option}`, but no arrow says where that goes",
                           line=decision.line, value=option,
                           fix=f"add `{step.label} --{option}--> …`")
    looping = [o for o, r in decision.routes.items() if _loops(index, r)]
    if looping and decision.bound is None:
        problems.error(path, f"`{step.label} --{looping[0]}-->` loops back, so `{step.label}` needs "
                       "a counted way out", line=decision.line,
                       fix=f"add a count to an arrow that leaves, such as `{step.label} --*3--> stop`")
    if decision.bound is not None and _loops(index, decision.bound.target):
        problems.error(path, "the counted way out loops back too, so the loop would never end",
                       line=decision.line, fix="point the counted arrow forward, to `done` or to `stop`")


def _check_recursion(flow: Flow, path, problems) -> None:
    reported: set[frozenset[str]] = set()

    def visit(name: str, trail: list[str]) -> None:
        if name in trail:
            cycle = trail[trail.index(name):]
            if frozenset(cycle) not in reported:
                reported.add(frozenset(cycle))
                problems.error(path, "a block runs itself: " + " → ".join(cycle + [name]),
                               line=flow.blocks[name].line,
                               fix="a block cannot contain itself; use `(*N)` to repeat it")
            return
        for step in flow.blocks[name].steps:
            if step.kind == "block":
                visit(step.name, trail + [name])

    for name in flow.blocks:
        visit(name, [])
