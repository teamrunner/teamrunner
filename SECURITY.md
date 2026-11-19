# Security

## Reporting a problem

Please report security problems privately, by email to
**security@teamrunner.ai**, not in a public issue. Say what you found, how to
reproduce it, and which version (`teamrunner --version`). You will hear back
within a week.

TeamRunner is an early release (0.0.x): fixes go into the next version, and
only the latest version is supported.

## What TeamRunner protects, and how

**Your API keys.** Keys live in `~/.teamrunner/.env`, with access restricted
to your user account, outside every team; TeamRunner creates the file readable
only by you (on Windows, the profile folder's own permissions do that). TeamRunner does not deliberately
record configured key values: `record.json` (`run.json` in older folders) records
only where each key came from, and an error a provider sends back is recorded
with any key in it replaced by `[key hidden]`. Inputs, prompts and tool results are logged, so
secrets pasted into them can still appear in run folders. `validate` and `run` refuse a team whose files
contain something that looks like a key. The sandbox is not given the host environment. Do not grant toolkeepers
access to your key file or other secret-bearing folders.

**`run_code`, the sandbox.** Python runs in WebAssembly (Pyodide), inside
Deno, as a separate process. Before the code starts, Deno gives up reading
and writing files, the environment, and starting programs; the internet too,
unless the toolkeeper says `network: true`. Even then the code has no network
of its own: its requests are handed to TeamRunner, which makes them with
`read_page`'s rule and never reaches this computer, your local network (IPv6
included) or a cloud machine's metadata service. The code works on its own copy of
files in memory: only the folders the toolkeeper lists in `paths`, and the
run's work folder, are copied in, and only what is in the work folder when
the code ends is copied back. A toolkeeper's `timeout` stops the code.

**`read_page`.** It reads `http` and `https` links only, and refuses any
address on your computer, your local network, or a cloud machine's metadata
service — checked before every request, redirects included. A toolkeeper's
`domains` limits it to the sites you name; pages over 25 MB are refused.

**`read_file`.** It reads only the files the run was given with `--file`.

**Toolkeepers.** Agents never hold tools. Every tool call goes through a
toolkeeper, which checks the asker against its access list and enforces its
limits; every call, allowed or refused, is logged.

## Known limits

- **A team you did not write can tell its models anything, and choose
  their tools.** Read a team before you run it, as you would a script:
  `teamrunner explain` shows who may use which tool, on what terms. A team's
  files can choose the models your keys pay for and grant tools; from the
  command line, `run_code`'s `paths` and an email toolkeeper's `mailbox` may
  name folders anywhere on your computer, and a `run_code` toolkeeper with
  both `paths` and `network: true` lets code read those folders and send what
  it reads anywhere. The browser page refuses folders outside the one it
  opened. A team's files cannot change where your keys are sent (`params:`
  take model settings only), write outside the team and its runs, reach
  local services from the sandbox, or run programs on your computer.
- **Pages and search results can carry instructions** aimed at the models
  reading them (prompt injection). The toolkeepers' limits bound what a
  misled model can do; they cannot stop it from being misled.
- **`read_page`, and `run_code` with `network: true`, check an address when
  they look it up**, and the connection looks it up again; a site that changes
  its address in between (DNS rebinding) could reach a local address.
- **Exported tables hold what the models wrote.** A text cell that Excel
  would run as a formula is written with a `'` in front; open the tables in
  R or pandas, or in Excel with Data → From Text/CSV.
- **Hosted model requests leave your machine.** They go to your configured
  endpoint, or, for the sponsored demo, through the Serenity Partners LLC
  gateway to the demo's model provider (currently Anthropic). The gateway keeps
  IP-based quota counters. Do not send private or sensitive data to the demo.
  See [LEGAL.md](LEGAL.md) and the README data disclosures.
- **Budget checks do not cap provider billing.** All monetary costs are
  estimates; actual charges can exceed estimates or configured budgets.
  Monitor your provider account and its spending controls. See [LEGAL.md](LEGAL.md).
