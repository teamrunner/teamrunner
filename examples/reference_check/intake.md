---
description: The intake toolkeeper — reads the paper this run was given as a file
kind: toolkeeper
tools:
  read_file: {types: [pdf], max_chars: 200000, calls_per_turn: 1}   # only this run's inputs/
access:
  reader: standard                    # only the reader needs the paper itself
---
I read the paper this run was given as a file. Send me its name and I
return its text. PDFs only.
