# Three rounds. Each side speaks and the fact checker checks the speech;
# a failed speech gets two more tries, then the debate moves on (done ends
# that speaker's block, not the debate: after a third fail the speech
# stands, and the judge has heard the fact checker fail it). The fact
# checker searches the web when it wants to: a side trip, its own choice,
# not a step here. The judge MUST check the debate's most disputed claims
# before ruling, so that search is a step: the judge's research turn ends
# with a request to the web toolkeeper, which replies to the judge, who
# then rules (D85).

rounds(*3) -> judge:research -> web -> judge:verdict

rounds = for_turn -> against_turn

for_turn = for -> fact_checker
           fact_checker --fail--> for
           fact_checker --pass,*3--> done

against_turn = against -> fact_checker
           fact_checker --fail--> against
           fact_checker --pass,*3--> done
