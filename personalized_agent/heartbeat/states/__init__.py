"""The state heartbeat: what the calendar keeps saying, said once.

Separate from the review that writes states (section 37) because it answers
a different question on a different clock. The review asks what is happening
now and runs as often as the agent wakes; this one asks what the states
already written have in common, and that question is only worth asking once
there are enough of them to have anything in common.
"""

from .heartbeat import StateHeartbeat

__all__ = ["StateHeartbeat"]
