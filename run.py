#!/usr/bin/env python3
"""Convenience launcher: ``python run.py [--root DATA_FOLDER] [--headless] [--status]``.

``--root`` is the data folder (workspace/ and internal/); without it, the
standard one in your home folder. A different folder is a different agent."""

from __future__ import annotations

import sys

from personalized_agent.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
