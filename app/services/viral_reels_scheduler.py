"""Small scheduler for Viral Reels collection callbacks."""
from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import Callable

_INTERVALS = {"6h": timedelta(hours=6), "12h": timedelta(hours=12), "daily": timedelta(days=1)}


class ViralReelsScheduler:
    def __init__(self, callback: Callable[[], None]) -> None:
        self.callback = callback
        self._timer: threading.Timer | None = None
        self._running = False
        self._lock = threading.Lock()
        self.last_success_at: datetime | None = None
        self.next_run_at: datetime | None = None

    def start(self, schedule: str) -> None:
        self.stop()
        interval = _INTERVALS.get(schedule)
        if interval is None:
            return
        self.next_run_at = datetime.now(UTC) + interval
        self._schedule(interval.total_seconds(), interval)

    def _schedule(self, seconds: float, interval: timedelta) -> None:
        self._timer = threading.Timer(seconds, self._run, args=(interval,))
        self._timer.daemon = True
        self._timer.start()

    def _run(self, interval: timedelta) -> None:
        with self._lock:
            if self._running:
                self._schedule(interval.total_seconds(), interval)
                return
            self._running = True
        try:
            self.callback()
            self.last_success_at = datetime.now(UTC)
        finally:
            with self._lock:
                self._running = False
            self.next_run_at = datetime.now(UTC) + interval
            self._schedule(interval.total_seconds(), interval)

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self.next_run_at = None
