# The VISIBLE LEADER condition. Same five people, same three rounds. Two
# changes only: Emma opens with a kickoff that announces she leads the
# review, and her end-of-round turn becomes a summary she circulates under
# her own name. She still writes the final report, but now as the person
# who decides what goes in it.

emma:kickoff -> review_rounds(*3) -> emma:report

review_rounds = noah -> lily -> derek -> zoe -> emma:summary
