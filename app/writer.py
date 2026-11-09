"""Generic register write / verify / compare helpers (GUI-agnostic).

A :class:`WriteItem` carries the raw Modbus words of one catalog register:
one word → FC06 (V3.5 §3.4 recommends FC06), several words (U32/S32) → FC16
so both halves change in one frame.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from app.modbus_client import ModbusError

ProgressFn = Callable[[int, int, str], None]
RecoverFn = Callable[[], None]
SleepFn = Callable[[float], None]


@dataclass(frozen=True)
class WriteItem:
    address: int
    name: str
    words: Tuple[int, ...]
    text: str = ""  # human readable target (e.g. "230.0 V")

    @property
    def count(self) -> int:
        return len(self.words)

    @property
    def raw(self) -> int:
        v = 0
        for w in self.words:
            v = (v << 16) | (int(w) & 0xFFFF)
        return v


@dataclass(frozen=True)
class VerifyIssue:
    address: int
    name: str
    expected: Tuple[int, ...]
    actual: Optional[Tuple[int, ...]]
    message: str = ""

    @property
    def kind(self) -> str:
        return "read error" if self.actual is None else "mismatch"


def words_text(words) -> str:
    if words is None:
        return "?"
    return "/".join(str(int(w)) for w in words)


class WriteCancelled(Exception):
    def __init__(self, written: int, total: int) -> None:
        super().__init__(f"cancelled after {written}/{total}")
        self.written = written
        self.total = total


class WriteFailed(ModbusError):
    def __init__(self, item: WriteItem, written: int, total: int, cause: Exception) -> None:
        super().__init__(f"failed at {item.address} {item.name} — {written}/{total} written. {cause}")
        self.item = item
        self.written = written
        self.total = total
        self.cause = cause


def _wait(sleep: Optional[SleepFn], delay: float, cancel) -> None:
    if delay <= 0:
        return
    if sleep is not None:
        sleep(delay)
    elif cancel is not None and hasattr(cancel, "wait"):
        cancel.wait(delay)
    else:  # pragma: no cover
        import time
        time.sleep(delay)


def attempt(fn, *, retries: int, retry_delay: float, cancel=None, sleep=None, recover=None):
    """Run fn() up to 1 + retries times; returns (result, attempts)."""
    n = 0
    while True:
        n += 1
        try:
            return fn(), n
        except Exception:  # noqa: BLE001
            if n > retries or (cancel is not None and cancel.is_set()):
                raise
            _wait(sleep, retry_delay, cancel)
            if recover is not None:
                try:
                    recover()
                except Exception:  # noqa: BLE001
                    pass


def write_item(client, item: WriteItem) -> None:
    if item.count == 1:
        client.write_single(item.address, item.words[0])
    else:
        client.write_multiple(item.address, list(item.words))


def read_item(client, item: WriteItem) -> Tuple[int, ...]:
    regs = client.read_registers(item.address, item.count)
    return tuple(int(x) & 0xFFFF for x in regs[: item.count])


def write_items(client, items: Sequence[WriteItem], *, on_progress: Optional[ProgressFn] = None,
                cancel: Optional[threading.Event] = None, on_written=None, retries: int = 0,
                retry_delay: float = 1.0, sleep: Optional[SleepFn] = None,
                recover: Optional[RecoverFn] = None, attempts_out: Optional[Dict[int, int]] = None) -> int:
    """Write in order; cancel is checked only between registers (never mid-write)."""
    total = len(items)
    written = 0
    for item in items:
        if cancel is not None and cancel.is_set():
            raise WriteCancelled(written, total)
        if on_progress:
            on_progress(written, total, f"{item.address} {item.name} ← {words_text(item.words)}")
        try:
            _r, n = attempt(lambda it=item: write_item(client, it), retries=retries, retry_delay=retry_delay,
                            cancel=cancel, sleep=sleep, recover=recover)
        except Exception as exc:  # noqa: BLE001
            if attempts_out is not None:
                attempts_out[item.address] = 1 + retries
            raise WriteFailed(item, written, total, exc) from exc
        if n > 1 and attempts_out is not None:
            attempts_out[item.address] = n
        written += 1
        if on_written:
            on_written(item)
    if on_progress:
        on_progress(total, total, "done")
    return written


def verify_items(client, items: Sequence[WriteItem], *, on_progress: Optional[ProgressFn] = None,
                 cancel: Optional[threading.Event] = None, retries: int = 0, retry_delay: float = 1.0,
                 sleep: Optional[SleepFn] = None, recover: Optional[RecoverFn] = None,
                 attempts_out: Optional[Dict[int, int]] = None, rewrite: bool = True) -> List[VerifyIssue]:
    """Read back each item and compare. With retries, a mismatch re-writes and re-reads."""
    issues: List[VerifyIssue] = []
    total = len(items)
    for i, item in enumerate(items):
        if cancel is not None and cancel.is_set():
            raise WriteCancelled(total, total)
        if on_progress:
            on_progress(i, total, f"verify {item.address} {item.name}")
        issue: Optional[VerifyIssue] = None
        n = 0
        while True:
            n += 1
            try:
                if n > 1 and rewrite and issue is not None and issue.actual is not None:
                    write_item(client, item)
                actual = read_item(client, item)
                issue = None if actual == tuple(item.words) else VerifyIssue(
                    item.address, item.name, tuple(item.words), actual,
                    f"read {words_text(actual)}, expected {words_text(item.words)}")
            except Exception as exc:  # noqa: BLE001
                issue = VerifyIssue(item.address, item.name, tuple(item.words), None, str(exc))
            if issue is None or n > retries or (cancel is not None and cancel.is_set()):
                break
            _wait(sleep, retry_delay, cancel)
            if recover is not None and issue.actual is None:
                try:
                    recover()
                except Exception:  # noqa: BLE001
                    pass
        if n > 1 and attempts_out is not None:
            attempts_out[item.address] = max(attempts_out.get(item.address, 1), n)
        if issue is not None:
            issues.append(issue)
    if on_progress:
        on_progress(total, total, "verify done")
    return issues


def compare_items(client, items: Sequence[WriteItem], *, on_progress: Optional[ProgressFn] = None,
                  cancel: Optional[threading.Event] = None, retries: int = 0, retry_delay: float = 1.0,
                  sleep: Optional[SleepFn] = None, recover: Optional[RecoverFn] = None,
                  attempts_out: Optional[Dict[int, int]] = None) -> List[VerifyIssue]:
    """READ-ONLY compare (verify-only / dry-run). Only read errors are retried."""
    issues: List[VerifyIssue] = []
    total = len(items)
    for i, item in enumerate(items):
        if cancel is not None and cancel.is_set():
            raise WriteCancelled(i, total)
        if on_progress:
            on_progress(i, total, f"read {item.address} {item.name}")
        try:
            actual, n = attempt(lambda it=item: read_item(client, it), retries=retries,
                                retry_delay=retry_delay, cancel=cancel, sleep=sleep, recover=recover)
        except Exception as exc:  # noqa: BLE001
            n = 1 + retries
            issues.append(VerifyIssue(item.address, item.name, tuple(item.words), None, str(exc)))
        else:
            if actual != tuple(item.words):
                issues.append(VerifyIssue(item.address, item.name, tuple(item.words), actual,
                                          f"read {words_text(actual)}, expected {words_text(item.words)}"
                                          + (f" ({item.text})" if item.text else "")))
        if n > 1 and attempts_out is not None:
            attempts_out[item.address] = n
    if on_progress:
        on_progress(total, total, "read done")
    return issues
