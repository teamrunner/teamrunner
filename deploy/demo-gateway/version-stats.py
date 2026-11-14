#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""How many TeamRunner copies check for updates, per day (D149). Run on the server:

    python3 version-stats.py                 # a table per day
    python3 version-stats.py --csv > s.csv   # one row per count, for a spreadsheet

Each line of the log is one check: at most one per install per day. "Installs" counts
distinct install ids; the version and platform columns count installs, not requests.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

DEFAULT_LOG = Path(__file__).resolve().parent / "version-logs" / "version-checks.jsonl"


def read(path: Path):
    with path.open(encoding="utf-8") as log:
        for text in log:
            try:
                line = json.loads(text)
                yield line["time"][:10], line["install"], line["version"], line["platform"]
            except (ValueError, KeyError, TypeError):
                continue


def tally(rows):
    days = defaultdict(lambda: {"requests": 0, "installs": {}})
    everyone = set()
    for day, install, version, platform in rows:
        days[day]["requests"] += 1
        days[day]["installs"][install] = (version, platform)  # an install's last check that day
        everyone.add(install)
    result = []
    for day in sorted(days):
        installs = days[day]["installs"]
        result.append({"date": day, "requests": days[day]["requests"], "installs": len(installs),
                       "versions": Counter(v for v, _ in installs.values()),
                       "platforms": Counter(p for _, p in installs.values())})
    return result, len(everyone)


def by_count(counter: Counter) -> str:
    return ", ".join(f"{name} {n}" for name, n in counter.most_common())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Daily active TeamRunner installs from the version-check log.")
    parser.add_argument("log", nargs="?", type=Path, default=DEFAULT_LOG, help=f"the log (default: {DEFAULT_LOG})")
    parser.add_argument("--csv", action="store_true", help="CSV rows: date, measure, key, count")
    args = parser.parse_args(argv)
    if not args.log.exists():
        print(f"no log at {args.log}", file=sys.stderr)
        return 1
    days, all_time = tally(read(args.log))
    if args.csv:
        out = csv.writer(sys.stdout)
        out.writerow(["date", "measure", "key", "count"])
        for d in days:
            out.writerow([d["date"], "requests", "", d["requests"]])
            out.writerow([d["date"], "installs", "", d["installs"]])
            for name, n in sorted(d["versions"].items()):
                out.writerow([d["date"], "version", name, n])
            for name, n in sorted(d["platforms"].items()):
                out.writerow([d["date"], "platform", name, n])
        out.writerow(["all", "installs", "", all_time])
        return 0
    print(f"{'date':<10}  {'requests':>8}  {'installs':>8}  versions (installs)  |  platforms (installs)")
    for d in days:
        print(f"{d['date']:<10}  {d['requests']:>8}  {d['installs']:>8}  "
              f"{by_count(d['versions'])}  |  {by_count(d['platforms'])}")
    print(f"\nAll time: {all_time} distinct installs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
