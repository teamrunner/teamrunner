# Changelog

TeamRunner is in preview: 0.0.x versions are a soft launch, and until 0.1.0
is announced a version may change in ways that break teams and run folders
made with an earlier one. Each such change is listed here.

## 0.0.2 — the first preview

What the roadmap calls 0.1, the org package: a folder of text files that gets
a job done.

- **Teams from files**: a folder with `team.yaml` is a team; each member is a
  Markdown file (settings on top, the prompt as prose); `chair.md` is the
  script of who acts when, with rounds and decisions; with no chair, members
  take turns in name order. Model profiles in `models/` share a model and its
  settings across a team.
- **Commands**: `new` (empty, `--from` an example or another team, or
  `--describe` for a team written by a model), `validate` (with `--online`
  to check the models), `explain`, `run` (with `--message`, `--file`,
  `--task-file`; `--rehearse` runs the whole flow with no model and no cost),
  `runs`, `show`, `gather`, `skill`.
- **Toolkeepers** hold the tools, with an access list and guardrails, and four
  built-in tools: `web_search_brave`, `read_page`, `read_file`, and `run_code`
  (Python in a WebAssembly sandbox).
- **Every run is logged** in the team's `runs/` folder: `run.json`,
  `events.jsonl` and `output.md`, with costs, and the Python, LiteLLM and Deno
  versions it ran on. Search results are read by the team but not kept,
  unless `keep_results: true`.
- **Six examples ship**: `round_robin`, `multi_agent_debate`,
  `orchestrators_lite`, `fact_checked`, `reference_check`, and
  `hiring_committee` (a hidden-profile task, after Stasser and Titus 1985).
- Models from OpenAI, Anthropic and Google, and local models through Ollama.
  macOS, Linux and Windows; Python 3.10 to 3.13.
- Licensed GPL-3.0-or-later; the examples, the files `new` writes and the
  authoring guide are MIT-0.

## 0.0.1

Reserved the name on PyPI. No working code.
