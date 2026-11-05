# The reader reads the paper and lists its references. If what it was
# given is not a full paper (an abstract page, a web page, not a PDF),
# the run stops there: nothing can be checked.
#
# Then two checks, ten references a turn, until the checker says it has
# done them all (at most ten turns, so a hundred references):
# the metadata checker asks the catalogue whether each reference exists
# and its details are right; the context checker reads each cited paper
# and asks whether it says what the citing sentence claims. The editor
# writes the report.

reader -> metadata_checker -> context_checker -> editor
reader --full--> metadata_checker
reader --not_full--> stop
metadata_checker --more--> metadata_checker
metadata_checker --done,*10--> context_checker
context_checker --more--> context_checker
context_checker --done,*10--> editor
