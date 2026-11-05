# Security

## Reporting a problem

Please report security problems privately, by email to
**security@teamrunner.ai**, not in a public issue. Say what you found, how to
reproduce it, and which version (`teamrunner --version`). You will hear back
within a week.

TeamRunner is in preview (0.0.x): fixes go into the next version, and only
the latest version is supported.

## What TeamRunner protects, and how

**Your API keys.** Keys live in `~/.teamrunner/.env`, readable only by you,
outside every team. A run folder never contains a key: `run.json` records only
where each key came from. `validate` and `run` refuse a team whose files
contain something that looks like a key. Code run by `run_code` never sees
them.

**`run_code`, the sandbox.** Python runs in WebAssembly (Pyodide), inside
Deno, as a separate process. Before the code starts, Deno gives up reading
and writing files, the environment, and starting programs; the internet too,
unless the toolkeeper says `network: true`. The code works on its own copy of
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

- **A team you did not write can tell its models anything.** Read a team
  before you run it, as you would a script: `teamrunner explain` shows who
  may use which tool, on what terms. In particular, a `run_code` toolkeeper
  with both `paths` and `network: true` lets code read those folders and
  send what it reads anywhere.
- **Pages and search results can carry instructions** aimed at the models
  reading them (prompt injection). The toolkeepers' limits bound what a
  misled model can do; they cannot stop it from being misled.
- **`read_page` checks an address when it looks it up**, and the connection
  looks it up again; a site that changes its address in between (DNS
  rebinding) could reach a local address.
- **What you send a hosted model goes to its provider.** See the README,
  "What your runs keep, and where your words go".
