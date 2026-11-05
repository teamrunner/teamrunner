---
description: Reads the paper and lists every reference with the sentences that cite it
model: openai/gpt-6-luna
output:
  paper: [full, not_full]
  references: ""
---
You are given a paper to check:

> {{paper}}

If it is a link, ask the library toolkeeper to read it. If it is a file
in this run's inputs, ask the intake toolkeeper to read it.

If what comes back is not the full paper — the request was refused as
not a PDF, or it is an abstract page, a landing page or a table of
contents — set paper to not_full and say in one sentence what you got
instead.

Otherwise set paper to full and list every entry in the reference list,
numbered as the paper numbers them. For each, give the entry exactly as
printed, its DOI if printed, and every sentence in the paper that cites
it, quoted word for word.
