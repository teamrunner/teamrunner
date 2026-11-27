---
name: leader-vs-flat
conditions:
  flat: orchestrators_lite          # the team as it ships: five peers, Emma takes notes
  visible_leader:
    from: orchestrators_lite
    change:
      chair: file:variants/chair_leader.md
      emma.prompt: file:variants/emma_leader.md
task: tasks/order_summary     # the team's own inputs, pinned here: editing the team cannot change the task

replications: 5   # 2 conditions x 5 = 10 runs: a first look, kept small
seed: 1

metrics:
  - metrics/bugs_found
  - metrics/suppressed

limits:
  max_concurrent: 5
  max_cost_usd: 5.00
---

## Question

Does a visible leader change what a team's review catches? Same five engineers, same code, same three rounds. The only difference is the org structure: who frames the discussion and who decides the report.

## Hypothesis

With a leader framing the discussion, more planted bugs reach the final report (`bugs_found.hits`), but fewer findings raised by others survive into it (`suppressed.suppressed`).

## Source

A lite recast of Act 2 of Hiroki Fukui (2026), "Invisible Orchestrators Suppress Protective Behavior and Dissociate Power-Holders" (arXiv 2605.13851, CC BY 4.0). Five runs a condition shows the method, not a finding: raise `replications:` before drawing conclusions.
