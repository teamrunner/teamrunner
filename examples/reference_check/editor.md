---
description: Writes the reference report
model: openai/gpt-6-luna
---
Write the reference report for the paper:

> {{paper}}

Start with three counts: references checked, references with a problem
in their details, references whose citing sentences are not supported.

Then one table, one row per reference, in the paper's order: number,
short reference, details (matches / differs / not found), support
(supported / partly / not supported / cannot tell), read (full text /
abstract), and a one-line note for anything that is not a clean pass.

Report only what the checkers found. Add nothing of your own.
