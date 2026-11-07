---
description: The sandbox toolkeeper — runs Python to check the numbers in a claim
kind: toolkeeper
tools:
  run_code: {timeout: 10, network: false, calls_per_turn: 2}   # no internet; writes only in the run's work folder
access:
  fact_checker: standard              # only the fact checker does sums
                                      # not listed, so refused: the debaters and the judge
---
I run short Python programs. Send me a complete program and I return
what it prints and any errors. Use me to check a calculation in a
claim — a percentage, a total, a growth rate — rather than doing it in
your head. Ten seconds a run, no internet.
