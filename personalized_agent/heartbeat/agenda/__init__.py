"""THE DAY'S AGENDA (the user, 2026-11-09: "对所有我的activity, agent要进行安排。哪些它能做的，自己做，
让我审阅。哪些必须要我做的，进行task triagging ... agent给我安排线性的日程").

Two principles above the rest (the user, 2026-11-09): every deadline caught; and as few changes of
context as can be - the user's time in large blocks.

  items.py    everything on the user's plate, gathered from where it is kept - code, no model
  triage.py   each new or changed one judged: who does it, its deadline, how long, what kind
  check.py    the plan's hard rules, checked by code: deadlines, times, blocks, switches
  plan.py     the day planned: a builder with searches, the check, a reviewer judging as the user
              would - and when it is all done

PHASE 1, A PREVIEW (the user's go, 2026-11-09): planned and shown, nothing done - no to-do deleted,
no work started, nothing said.
"""
