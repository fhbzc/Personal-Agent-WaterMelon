"""The user's connections: the people around them, what they are doing, and
what of it the user needs to know (heartbeat/connections/).

  store.py   the people, their accounts and dated activity, the user's own
             changes to them (the connection lessons), the public profiles
             the user gave, and each round's "what you need to know"
  people.py  step 2 of the agent heartbeat: the people in a window of
             activity - who they are, what they are doing - matched to the
             ones known, the user's lessons in front of it
  round.py   with each state heartbeat round: what the user's current work
             needs to know, found by searching the people (and the public
             web, for one long unseen), ranked for the Overview
"""

from .store import ConnectionsStore, RELATIONSHIPS  # noqa: F401
