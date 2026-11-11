---
description: Reads the paper and lists every reference with the sentences that cite it
output:
  paper: [full, not_full]
---
You are given a paper to check:

{{paper}}

If it is a link, ask the library toolkeeper to read it. If it is a file
in this run's inputs, ask the intake toolkeeper to read it.
The paper above is already your input, even when the meeting minutes say
"Nothing has been said yet." Make the tool call before answering; do not
ask the user to provide the paper again. A lack of earlier discussion
does not mean the paper is missing.

If what comes back is not the full paper — the request was refused as
not a PDF, or it is an abstract page, a landing page or a table of
contents — set paper to not_full and say in one sentence what you got
instead.

Otherwise set paper to full and list every entry in the reference list,
numbered as the paper numbers them. For each, give the entry exactly as
printed, its DOI if printed, and every sentence in the paper that cites
it, quoted word for word.
Put the complete list and citing sentences in your message to the team
(the JSON `said` field), not just a summary that you extracted them.
The checkers read that message; they do not see your private tool reply.
