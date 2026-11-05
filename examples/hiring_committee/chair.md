# How the meeting is run. This is the line the experiment changes.
#
# Open discussion (as written): three rounds in which each member speaks
# in turn, then the clerk records the decision.
meeting(*3) -> clerk:record
#
# Share first: the chair asks each member to report what they know
# before anyone gives an opinion, then two rounds of discussion. Put a
# `#` in front of the line above and remove it from the line below.
# chen:share_facts -> diaz:share_facts -> okafor:share_facts -> meeting(*2) -> clerk:record
#
# Both versions give each member three turns.

meeting = chen -> diaz -> okafor
