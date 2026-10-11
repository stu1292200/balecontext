"""Automatic local cache refresh for the lifetime of an agent connection."""
from __future__ import annotations

import math
import threading

from .store import Project


class IndexWatcher:
    """Poll locally without model calls or messages on the MCP stdout stream."""

    def __init__(self, project: Project, interval: float = 2.0):
        if not math.isfinite(interval) or interval < 0.2:
            raise ValueError("Watch interval must be finite and at least 0.2 seconds.")
        self.project = project
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_error: str | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="balecontext-watch", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.project.index()
                self.last_error = None
            except Exception:
                # Transient edits, file permissions and DB contention must not
                # kill the connection. Foreground requests still refresh and
                # report their own errors. Avoid exposing private paths here.
                self.last_error = "Background refresh failed; retrying. Requests still check for changes."
            if self._stop.wait(self.interval):
                break

    def status(self) -> dict:
        return {
            "active": self._thread is not None and self._thread.is_alive() and not self._stop.is_set(),
            "interval_seconds": self.interval,
            "last_error": self.last_error,
        }

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            # A large scan can finish in the background; a closing MCP process
            # must not wait indefinitely for it. SQLite commits remain atomic.
            self._thread.join(timeout=1.0)
