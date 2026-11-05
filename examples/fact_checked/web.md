---
description: The web toolkeeper — searches the web, on different terms for different members
kind: toolkeeper
tools:
  web_search_brave: {max_results: 5, calls_per_turn: 2}   # base: the strictest terms
access:
  fact_checker: {calls_per_turn: 3}   # a few claims a speech may need checking
  judge: standard                     # base terms: two searches (one is required, D85)
                                      # not listed, so refused: the debaters argue from what they know
---
I search the web. Send me a search query and I return the top five
results with their sources. The fact checker gets three searches a turn,
the judge two — counted per turn, each member on their own card (D86).
