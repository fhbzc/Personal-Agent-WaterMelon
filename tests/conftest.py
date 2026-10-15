"""Settings for every test, whichever file it is in."""

import os

# No real dialog is ever opened by a test: the folder and application
# choosers report that there is none (common/chooser.py), instead of putting
# windows on the desktop of whoever runs the tests.
os.environ["PA_NO_DIALOGS"] = "1"

# And no real sound is ever played: a run that ends with one asked for
# reaches a player that plays nothing (common/sound.py) - a test that wants
# to know it was asked gives the run's owner a fake one.
os.environ["PA_NO_SOUND"] = "1"
