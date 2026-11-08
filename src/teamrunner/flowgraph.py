"""A declared flow graph and stable locations for the engine's recorded path.

Control nodes make block entry, return, repeat and termination explicit. The
engine records these locations while executing; the viewer never replays choices.
"""

import hashlib
import html

from .ir import DONE, STOP


def block_id(name):
    return "b" + hashlib.sha256(name.encode()).hexdigest()[:12]


def entry_id(block):
    return block_id(block.name) + "_entry"


def done_id(block):
    return block_id(block.name) + "_done"


def step_id(block, index):
    step = block.steps[index]
    if step.kind == DONE:
        return done_id(block)
    if step.kind == STOP:
        return "stop"
    return block_id(block.name) + f"_s{index}"


def declared_graph(team):
    nodes, edges = {}, {}
    def node(id, label, kind, **fields):
        nodes.setdefault(id, {"id":id,"label":label,"kind":kind, **fields})
    def edge(source, target, label=""):
        item = edges.setdefault((source,target), {"source":source,"target":target,"labels":[]})
        if label and label not in item["labels"]:
            item["labels"].append(label)
    def target(block, route):
        return step_id(block,route) if isinstance(route,int) else "stop" if route == STOP else done_id(block)
    for block in [team.flow.main, *team.flow.blocks.values()]:
        node(entry_id(block), "Start of " + (block.name or team.name), "entry")
        node(done_id(block), "End of " + (block.name or "run"), "end")
        for i, step in enumerate(block.steps):
            id = step_id(block,i)
            if step.kind == DONE:
                continue
            if step.kind == STOP:
                node(id,"Stop the run","stop")
                continue
            kind = ("block" if step.kind == "block" else "toolkeeper" if team.members[step.name].kind == "toolkeeper"
                    else "decider" if i in block.decisions else "worker")
            label = step.label + (f", {step.repeat} times" if step.repeat > 1 else "")
            node(id,label,kind,member=step.name if step.kind == "member" else None,
                 block=block.name,step=i)
        edge(entry_id(block),step_id(block,0) if block.steps else done_id(block))
        for i, step in enumerate(block.steps):
            if step.kind in (DONE,STOP):
                continue
            source = step_id(block,i)
            next_node = step_id(block,i+1) if i+1 < block.sequence_length else done_id(block)
            if step.kind == "block":
                child = team.flow.blocks[step.name]
                edge(source,entry_id(child),"enter")
                if step.repeat > 1:
                    edge(done_id(child),source,"repeat")
                edge(done_id(child),next_node,"return")
            else:
                if step.repeat > 1:
                    edge(source,source,"repeat")
                decision = block.decisions.get(i)
                if decision:
                    for option, route in decision.routes.items():
                        edge(source,target(block,route),option)
                    if decision.bound:
                        edge(source,target(block,decision.bound.target),f"limit {decision.bound.count}")
                else:
                    edge(source,next_node)
    if any(e["target"] == "stop" for e in edges.values()):
        node("stop","Stop the run","stop")
    return {"schema":1,"source":team.flow.source,"nodes":list(nodes.values()),"edges":list(edges.values())}


def counted_graph(graph, path):
    """Count only recorded transitions, including interrupted turns' entry."""
    nodes = {n["id"] for n in graph["nodes"]}
    visits = {id:0 for id in nodes}
    counts = {}
    for id in path:
        if id in visits:
            visits[id] += 1
    for source,target in zip(path,path[1:]):
        counts[(source,target)] = counts.get((source,target),0) + 1
    return {**graph,"nodes":[{**n,"visits":visits[n["id"]]} for n in graph["nodes"]],
            "edges":[{**e,"count":counts.get((e["source"],e["target"]),0)} for e in graph["edges"]]}


def mermaid_graph(graph, counts=True):
    """Generate diagram text from data, never concatenate recorded Mermaid code."""
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes,list) or not isinstance(edges,list) or len(nodes)>1000 or len(edges)>5000:
        raise ValueError("This run's flow graph is missing or too large to draw.")
    if any(not isinstance(n,dict) or not isinstance(n.get('id'),str) for n in nodes):
        raise ValueError('Invalid recorded flow nodes.')
    mapping = {n["id"]:f"n{i}" for i,n in enumerate(nodes)}
    if len(mapping) != len(nodes) or any(not isinstance(e,dict) or
            e.get('source') not in mapping or e.get('target') not in mapping or
            not isinstance(e.get('labels',[]),list) for e in edges):
        raise ValueError('Invalid recorded flow arrows.')
    def label(value):
        return html.escape(str(value)[:400], quote=True).replace("\n"," ").replace("\r"," ")
    lines = ["flowchart TD"]
    for item in nodes:
        id, text, kind = mapping[item["id"]],label(item.get("label","step")),item.get("kind")
        if kind in ("entry","end","stop"):
            lines.append(f'  {id}(["{text}"])')
        elif kind == "decider":
            lines.append(f'  {id}{{"{text}"}}')
        elif kind == "toolkeeper":
            lines.append(f'  {id}{{{{"{text}"}}}}')
        else:
            lines.append(f'  {id}["{text}"]')
    for i,item in enumerate(edges):
        text = " / ".join(str(v) for v in item.get("labels",[]))
        if counts:
            text += (" · " if text else "") + f'{int(item.get("count",0))} ×'
        source,target = mapping[item["source"]],mapping[item["target"]]
        lines.append(f'  {source} -- "{label(text)}" --> {target}' if text else f'  {source} --> {target}')
        if counts:
            style = "stroke:#546be0,stroke-width:3px" if item.get("count",0) else "stroke:#a3aba7,stroke-dasharray:4 4"
            lines.append(f"  linkStyle {i} {style}")
    return "\n".join(lines)
