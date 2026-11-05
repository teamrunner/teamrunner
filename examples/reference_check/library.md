---
description: The library toolkeeper — reads full papers from their links, PDFs only
kind: toolkeeper
tools:
  read_page: {types: [pdf], max_chars: 30000, calls_per_turn: 1}   # base: the strictest terms
access:
  reader: {max_chars: 200000}         # the whole paper under review
  context_checker: {calls_per_turn: 10}   # one cited paper per reference in a batch
                                      # not listed, so refused: the metadata checker and the editor
---
I read full papers. Send me a link to a PDF and I return its text.
Anything that is not a PDF — an abstract page, a publisher's landing
page — I refuse, so you know you do not have the full paper.
