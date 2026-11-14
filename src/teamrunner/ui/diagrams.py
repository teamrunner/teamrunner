# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""Historical diagrams and comparisons use recorded data, never current files."""

import difflib
import hashlib
import os
from pathlib import Path

from ..flowgraph import counted_graph, mermaid_graph
from ..runs import member_identity
from . import api, runviews


def observed_graph(events):
    """A fallback for old logs: completed messages, without invented control flow."""
    nodes, edges, path = {}, {}, []
    for event in events:
        if event["event"] != "agent_message":
            continue
        label = str(event.get("from") or event.get("instance_path") or "member")
        if event.get("stage"):
            label += " · " + str(event["stage"])
        node = "observed_" + hashlib.sha256(label.encode()).hexdigest()[:16]
        nodes[node] = {"id": node, "label": label, "kind": "worker"}
        path.append(node)
    for source, target in zip(path, path[1:]):
        edges[(source, target)] = {"source": source, "target": target, "labels": []}
    return counted_graph({"nodes": list(nodes.values()), "edges": list(edges.values())}, path)


def path_view(data):
    first = next((e for e in data["events"] if e["event"] == "run_started"), {})
    last = next((e for e in reversed(data["events"]) if e["event"] == "run_finished"), {})
    graph, path = first.get("flow_graph"), last.get("flow_path")
    note = ("Recorded engine path. Counts include entry into interrupted turns; "
            "dashed arrows were not taken.")
    try:
        if (not isinstance(graph, dict) or not isinstance(path, list)
                or not all(isinstance(node, str) for node in path)):
            raise ValueError()
        mermaid_graph(graph)
        nodes = {node["id"] for node in graph["nodes"]}
        arrows = {(edge["source"], edge["target"]) for edge in graph["edges"]}
        if (any(node not in nodes for node in path)
                or any(pair not in arrows for pair in zip(path, path[1:]))):
            raise ValueError()
        graph = counted_graph(graph, path)
    except (ValueError, KeyError, TypeError, AttributeError):
        note = ("Observed completed turns only. This log has no usable saved engine path; "
                "declared arrows, block positions and interrupted turns are unavailable.")
        graph = observed_graph(data["events"])
    return {"graph": graph, "mermaid": mermaid_graph(graph), "note": note}


def author(root, written):
    from ..build import load_team
    from ..explain import mermaid

    result = api.explain_team(root, written)
    team, _ = load_team(api.team_folder(root, written))
    return {**result, "mermaid": mermaid(team) if team else ""}


def recorded_source(member):
    """Only describe text as exact source when it matches the recorded hash."""
    text = member.get("source_text") if member else None
    if isinstance(text, str):
        fingerprint = "sha256:" + hashlib.sha256(text.encode()).hexdigest()
        if fingerprint == member.get("version_hash"):
            return text
    return None


def member_change(old, new):
    if old is None:
        status = "added"
    elif new is None:
        status = "removed"
    elif not old.get("version_hash") or not new.get("version_hash"):
        status = "unknown"
    elif old["version_hash"] == new["version_hash"]:
        status = "unchanged"
    else:
        status = "changed"
    before, after = recorded_source(old), recorded_source(new)
    exact = (old is None or before is not None) and (new is None or after is not None)
    diff = ""
    if exact:
        diff = "".join(difflib.unified_diff(
            (before or "").splitlines(True), (after or "").splitlines(True),
            fromfile="left source", tofile="right source"))
    return {"before": old, "after": new, "status": status, "diff": diff, "exact_source": exact}


def compare(root, left, right, versions_cache=None):
    a = runviews.detail(root, left, versions_cache)
    b = runviews.detail(root, right, versions_cache)
    ra, rb = runviews.selected(root, left), runviews.selected(root, right)
    before = {member_identity(member, ra): member for member in a["members"]}
    after = {member_identity(member, rb): member for member in b["members"]}
    changes = [member_change(before.get(identity), after.get(identity))
               for identity in sorted(before.keys() | after.keys(), key=str)]
    return {"left": a, "right": b, "members": changes,
            "left_path": path_view(a), "right_path": path_view(b)}


def restore(root: Path, written: str, destination: str):
    from ..new import copy_from

    record = runviews.selected(root, written)
    if not (record.folder / "resolved" / "team.yaml").is_file():
        raise FileNotFoundError("This run does not contain a saved team spec.")
    source = runviews.attachment(root, written, "resolved/team.yaml").parent
    # copy_from follows links: reject every link, including internal and hidden ones.
    for directory, dirs, names in os.walk(source, followlinks=False):
        for name in dirs + names:
            if (Path(directory) / name).is_symlink():
                raise api.OutsideRoot("Saved team links cannot be copied. "
                                      "Open a complete saved team without links.")
    target = api.inside(root, destination)
    if api.in_runs(root, target):
        raise api.OutsideRoot("Run records are read-only.")
    if api.generated(root, target):
        raise api.OutsideRoot(api.BUILT)
    parent = target.parent
    while parent.is_relative_to(root):
        if (parent / "team.yaml").exists():
            raise ValueError("Choose a workspace without a parent team.yaml. Open its parent "
                             "folder with teamrunner ui to create an independent team.")
        if parent == root:
            break
        parent = parent.parent
    folder, warnings = copy_from(source, target)
    return {"path": (folder / "team.yaml").relative_to(root).as_posix(), "warnings": warnings}
