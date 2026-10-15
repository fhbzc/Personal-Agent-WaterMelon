"""Raw activity kept for a while, then let go.

What the listener records is what the agent heartbeat reads its window
from, what the Overview draws its day and its week from, and what a state's
references are copied from when the state is written. None of them reaches
back further than a week, and nothing reads the whole of it: kept for ever
it is only a table that grows. So it is kept for `raw_activity_keep_days`
(eight days by default) and then removed by age - and the file
events that were never anybody's activity (a registry journal written ten
thousand times in ten minutes) are swept out as soon as they are noticed.

A small loop of its own, a few times a day; the first look a few minutes
after start-up, so opening the application does not begin with it.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional

from ...common.file_noise import rules_from

log = logging.getLogger(__name__)

#: How often it looks, and how long after start-up the first look waits.
EVERY_SECONDS = 6 * 3600
FIRST_AFTER_SECONDS = 300


class RawHousekeeping:
    def __init__(self, config: Any, raw_store: Any) -> None:
        self.config = config
        self.raw_store = raw_store
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.last: Dict[str, Any] = {}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="raw-housekeeping",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        if self._stop.wait(FIRST_AFTER_SECONDS):
            return
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:            # pragma: no cover - housekeeping, not the work
                log.warning("raw activity housekeeping failed", exc_info=True)
            if self._stop.wait(EVERY_SECONDS):
                return

    def run_once(self) -> Dict[str, Any]:
        """Activity older than the keeping window removed, and the noise."""
        keep_days = int(self.config.get("raw_activity_keep_days", 8) or 0)
        said: Dict[str, Any] = {"pruned": self.raw_store.prune_older_than(keep_days)}
        swept = self.raw_store.prune_noise(**rules_from(self.config))
        if swept.get("removed"):
            said["swept"] = swept
            log.info("dropped %d stored file events that were noise",
                     swept["removed"])
        self.last = said
        return said
