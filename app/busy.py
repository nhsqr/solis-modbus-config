"""Thread-safe progress state for long Modbus operations (GUI-agnostic).

The worker thread calls :meth:`ProgressTracker.update`; the Tk main loop
polls :meth:`ProgressTracker.snapshot` via ``after()`` and renders it with
:func:`format_busy_text`. No Tk calls happen off the main thread.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class ProgressSnapshot:
    label: str
    done: int
    total: int
    detail: str
    elapsed: float
    active: bool

    @property
    def fraction(self) -> Optional[float]:
        """0..1 when the total is known, else None (indeterminate)."""
        if self.total <= 0:
            return None
        return max(0.0, min(1.0, self.done / self.total))


class ProgressTracker:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._label = ""
        self._done = 0
        self._total = 0
        self._detail = ""
        self._t0 = clock()
        self._active = False

    def start(self, label: str) -> None:
        with self._lock:
            self._label = label
            self._done = 0
            self._total = 0
            self._detail = ""
            self._t0 = self._clock()
            self._active = True

    def update(self, done: int, total: int, detail: str = "") -> None:
        with self._lock:
            self._done = int(done)
            self._total = int(total)
            self._detail = detail

    def finish(self) -> float:
        """Mark inactive; return elapsed seconds."""
        with self._lock:
            self._active = False
            return self._clock() - self._t0

    def snapshot(self) -> ProgressSnapshot:
        with self._lock:
            return ProgressSnapshot(
                label=self._label,
                done=self._done,
                total=self._total,
                detail=self._detail,
                elapsed=self._clock() - self._t0,
                active=self._active,
            )


def format_busy_text(snap: ProgressSnapshot) -> str:
    """e.g. ``Reading all… 37/180 (21%) — 43099 OV-G-F-T 01 · 2.1 s``."""
    parts = [f"{snap.label}…"]
    if snap.total > 0:
        pct = int(round(100 * (snap.fraction or 0.0)))
        parts.append(f" {snap.done}/{snap.total} ({pct}%)")
    if snap.detail:
        parts.append(f" — {snap.detail}")
    parts.append(f" · {snap.elapsed:.1f} s")
    return "".join(parts)
