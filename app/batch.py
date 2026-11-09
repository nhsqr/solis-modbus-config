"""Apply a settings profile to many inverters (GUI-agnostic).

Rules (same as v1):
  * devices are grouped by (host, port). One group = one worker = ONE Modbus
    TCP session (Solis loggers accept one session); the unit IDs of a group
    run strictly one after another over that session;
  * different IPs run in parallel (``parallel``, default 8, 1 = sequential);
  * per device: pre-flight identity read (FC04 33000-33019) → write the
    profile in order (FC06; FC16 for 32-bit) → read-back verify;
  * ``retries`` (default 2 → 3 attempts) for connect, pre-flight, every write
    and every verify read (a mismatch re-writes and re-reads); ~1 s back-off,
    after a failure the SAME session is closed and re-opened;
  * ``verify_only``: read + compare, the client is wrapped so that ANY write
    raises :class:`WriteForbidden`;
  * ``dry_run``: also read-only (same proxy) — reports what would change.
"""
from __future__ import annotations

import csv
import ipaddress
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Union

from app.devices import DeviceTarget, group_by_endpoint
from app.identity import read_identity
from app.writer import (VerifyIssue, WriteCancelled, WriteFailed, WriteItem, compare_items,
                        verify_items, words_text, write_items)

WAITING = "waiting"
CONNECTING = "connecting"
PREFLIGHT = "pre-flight"
WRITING = "writing"
VERIFYING = "verifying"
OK = "OK"
DRY_RUN_OK = "dry-run OK"
MISMATCH = "mismatch"
ERROR = "error"
SKIPPED = "skipped"
CANCELLED = "cancelled"
FINAL_STATES = {OK, DRY_RUN_OK, MISMATCH, ERROR, SKIPPED, CANCELLED}
FAILED_STATES = {MISMATCH, ERROR}

DEFAULT_PARALLEL = 8
MAX_PARALLEL = 36
DEFAULT_RETRIES = 2
MAX_RETRIES = 5
MIN_TIMEOUT = 0.5
MAX_TIMEOUT = 60.0
DEFAULT_TIMEOUT = 3.0


@dataclass
class BatchOptions:
    verify: bool = True
    stop_on_error: bool = False
    dry_run: bool = False
    verify_only: bool = False
    timeout: float = DEFAULT_TIMEOUT
    parallel: int = DEFAULT_PARALLEL
    retries: int = DEFAULT_RETRIES
    retry_delay: float = 1.0
    inter_device_delay: float = 0.0

    @property
    def read_only(self) -> bool:
        return self.verify_only or self.dry_run


def parse_timeout(text) -> float:
    v = float(str(text).strip().replace(",", "."))
    if v != v or not (MIN_TIMEOUT <= v <= MAX_TIMEOUT):
        raise ValueError(f"timeout must be {MIN_TIMEOUT:g}–{MAX_TIMEOUT:g} s")
    return v


def timeout_setting(value, default: float = DEFAULT_TIMEOUT) -> float:
    for v in (value, default):
        try:
            return parse_timeout(v)
        except (TypeError, ValueError):
            pass
    return DEFAULT_TIMEOUT


def validate_options(parallel_text, retries_text, timeout_text):
    """(parallel, retries, timeout) or an error message string."""
    try:
        parallel = int(str(parallel_text).strip())
        retries = int(str(retries_text).strip())
        if not (1 <= parallel <= MAX_PARALLEL) or not (0 <= retries <= MAX_RETRIES):
            raise ValueError
    except ValueError:
        return f"Паралелни връзки 1–{MAX_PARALLEL}, повторения 0–{MAX_RETRIES}."
    try:
        timeout = parse_timeout(timeout_text)
    except ValueError:
        return f"Timeout трябва да е {MIN_TIMEOUT:g}–{MAX_TIMEOUT:g} s."
    return parallel, retries, timeout


class WriteForbidden(RuntimeError):
    """A write was attempted during a read-only (verify-only / dry-run) run."""


class ReadOnlyClient:
    """Proxy: any write* attribute raises instead of reaching the bus."""

    def __init__(self, inner) -> None:
        object.__setattr__(self, "_inner", inner)

    def __getattr__(self, name):
        if name.startswith("write"):
            raise WriteForbidden(f"read-only run: {name} is not allowed")
        return getattr(self._inner, name)

    def __setattr__(self, name, value):
        setattr(self._inner, name, value)


@dataclass
class DeviceResult:
    target: DeviceTarget
    status: str = WAITING
    message: str = ""
    sn: str = ""
    model: str = ""
    written: int = 0
    total: int = 0
    verify_issues: List[VerifyIssue] = field(default_factory=list)
    error_address: Optional[int] = None
    started: Optional[float] = None
    finished: Optional[float] = None
    connect_attempts: int = 0
    preflight_attempts: int = 0
    write_attempts: Dict[int, int] = field(default_factory=dict)
    verify_attempts: Dict[int, int] = field(default_factory=dict)

    @property
    def done(self) -> bool:
        return self.status in FINAL_STATES

    @property
    def retries_used(self) -> int:
        return (max(0, self.connect_attempts - 1) + max(0, self.preflight_attempts - 1)
                + sum(a - 1 for a in self.write_attempts.values())
                + sum(a - 1 for a in self.verify_attempts.values()))

    def status_text(self) -> str:
        return f"writing {self.written}/{self.total}" if self.status == WRITING else self.status

    def snapshot(self) -> "DeviceResult":
        return replace(self, verify_issues=list(self.verify_issues), write_attempts=dict(self.write_attempts),
                       verify_attempts=dict(self.verify_attempts))


def target_sort_key(t: DeviceTarget):
    try:
        ip = ipaddress.ip_address(t.host)
        host_key = (0, ip.version, int(ip), "")
    except ValueError:
        host_key = (1, 0, 0, t.host.lower())
    return (host_key, t.port, t.unit_id)


@dataclass
class BatchSummary:
    results: List[DeviceResult]
    cancelled: bool
    stopped_on_error: bool
    started_at: datetime
    elapsed: float
    parallel: int = 1
    retries: int = 0
    mode: str = "write"

    def count(self, status: str) -> int:
        return sum(1 for r in self.results if r.status == status)

    def sorted_results(self) -> List[DeviceResult]:
        return sorted(self.results, key=lambda r: target_sort_key(r.target))

    def text(self) -> str:
        parts = [f"{len(self.results)} device(s)"]
        for st in (OK, DRY_RUN_OK, MISMATCH, ERROR, SKIPPED, CANCELLED):
            n = self.count(st)
            if n:
                parts.append(f"{st}: {n}")
        s = ", ".join(parts) + f" · {self.elapsed:.1f} s"
        retried = sum(1 for r in self.results if r.retries_used)
        if retried:
            s += f" · {retried} device(s) needed retries"
        if self.cancelled:
            s += " · CANCELLED"
        if self.stopped_on_error:
            s += " · stopped on first error"
        return s


ClientFactory = Callable[[str, int, int, float], object]


def default_client_factory(host: str, port: int, unit_id: int, timeout: float):
    from app.modbus_client import SolisModbusClient
    return SolisModbusClient(host=host, port=port, unit_id=unit_id, timeout=timeout)


class _AnyEvent:
    def __init__(self, *events: threading.Event) -> None:
        self._events = events

    def is_set(self) -> bool:
        return any(e.is_set() for e in self._events)

    def wait(self, timeout: float) -> bool:
        end = time.monotonic() + timeout
        while not self.is_set():
            left = end - time.monotonic()
            if left <= 0:
                return False
            time.sleep(min(0.05, left))
        return True


def _retry_note(n: int) -> str:
    return f" (OK after {n} retr{'y' if n == 1 else 'ies'})" if n else ""


class BatchRunner:
    def __init__(self, devices: Sequence[DeviceTarget], items: Sequence[WriteItem], *,
                 options: Optional[BatchOptions] = None, client_factory: ClientFactory = default_client_factory,
                 identity_reader: Callable[[object], Dict] = read_identity, on_update=None, on_progress=None,
                 cancel: Optional[threading.Event] = None, sleep: Optional[Callable[[float], None]] = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.devices = list(devices)
        self.items = list(items)
        self.options = options or BatchOptions()
        self.client_factory = client_factory
        self.identity_reader = identity_reader
        self.on_update = on_update
        self.on_progress = on_progress
        self.cancel = cancel or threading.Event()
        self._stop = threading.Event()
        self._halt = _AnyEvent(self.cancel, self._stop)
        self._sleep = sleep
        self._clock = clock
        self._lock = threading.Lock()
        self.results = [DeviceResult(d, total=len(self.items)) for d in self.devices]
        self._index = {id(r.target): i for i, r in enumerate(self.results)}
        o = self.options
        self._ops_per_device = max(1, len(self.items) * (1 if o.read_only else (2 if o.verify else 1)))
        self._ops_total = max(1, self._ops_per_device * len(self.devices))
        self._dev_ops = [0] * len(self.devices)
        self._stopped = False

    # helpers
    def _emit(self, idx: int) -> None:
        if self.on_update:
            with self._lock:
                snap = self.results[idx].snapshot()
            self.on_update(idx, snap)

    def _set_ops(self, idx: int, n: int) -> None:
        with self._lock:
            self._dev_ops[idx] = max(0, min(n, self._ops_per_device))

    def _progress(self, detail: str) -> None:
        if self.on_progress:
            with self._lock:
                done = sum(self._dev_ops)
            self.on_progress(done, self._ops_total, detail)

    def _set(self, idx: int, status: str, message: str = "") -> None:
        with self._lock:
            r = self.results[idx]
            r.status = status
            r.message = message
            if status in FINAL_STATES:
                r.finished = self._clock()
        self._emit(idx)

    def _wait(self, delay: float) -> None:
        if delay <= 0:
            return
        if self._sleep is not None:
            self._sleep(delay)
        else:
            self._halt.wait(delay)

    def _sleep_fn(self):
        return self._sleep if self._sleep is not None else (lambda d: self._halt.wait(d))

    def _halt_reason(self) -> str:
        return "cancelled" if self.cancel.is_set() else "stopped (another device failed)"

    def _fail(self) -> None:
        if self.options.stop_on_error:
            with self._lock:
                self._stopped = True
            self._stop.set()

    def _finish_rest(self, status: str, message: str) -> None:
        for i, r in enumerate(self.results):
            if not r.done:
                self._set(i, status, message)

    # run
    def run(self) -> BatchSummary:
        t0 = self._clock()
        started_at = datetime.now()
        groups = group_by_endpoint(self.devices)
        workers = max(1, min(int(self.options.parallel or 1), MAX_PARALLEL, len(groups) or 1))
        if workers == 1:
            for hp, devs in groups:
                self._run_group(hp, devs)
        else:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="batch") as pool:
                for f in [pool.submit(self._run_group, hp, devs) for hp, devs in groups]:
                    f.result()
        cancelled = self.cancel.is_set()
        if cancelled:
            self._finish_rest(CANCELLED, "not started (cancelled)")
        elif self._stopped:
            self._finish_rest(SKIPPED, "not started (stopped on first error)")
        with self._lock:
            self._dev_ops = [self._ops_per_device] * len(self.devices)
        self._progress("done")
        o = self.options
        mode = "verify-only" if o.verify_only else "dry-run" if o.dry_run else "write"
        return BatchSummary(self.results, cancelled, self._stopped and not cancelled, started_at,
                            self._clock() - t0, parallel=workers, retries=o.retries, mode=mode)

    def _connect(self, client) -> int:
        n = 0
        while True:
            n += 1
            try:
                client.connect()
                return n
            except Exception:  # noqa: BLE001
                try:
                    client.disconnect()
                except Exception:  # noqa: BLE001
                    pass
                if n > self.options.retries or self._halt.is_set():
                    raise
                self._wait(self.options.retry_delay)

    def _make_recover(self, client):
        def recover() -> None:
            if self._halt.is_set():
                return
            try:
                client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            client.connect()
        return recover

    def _run_group(self, host_port, devs: List[DeviceTarget]) -> None:
        host, port = host_port
        if self._halt.is_set():
            return
        idxs = [self._index[id(d)] for d in devs]
        for i in idxs:
            self._set(i, CONNECTING if i == idxs[0] else WAITING)
        client = self.client_factory(host, port, devs[0].unit_id, self.options.timeout)
        if self.options.read_only:
            client = ReadOnlyClient(client)
        try:
            try:
                n = self._connect(client)
            except Exception as exc:  # noqa: BLE001
                tries = self.options.retries + 1
                msg = f"connect {host}:{port} failed after {tries} attempt(s) — {exc}"
                for i in idxs:
                    with self._lock:
                        self.results[i].connect_attempts = tries
                    self._set(i, ERROR, msg)
                    self._set_ops(i, self._ops_per_device)
                self._progress(msg)
                self._fail()
                return
            with self._lock:
                self.results[idxs[0]].connect_attempts = n
                for i in idxs[1:]:
                    self.results[i].connect_attempts = 1
            for k, i in enumerate(idxs):
                if self._halt.is_set():
                    break
                if k and self.options.inter_device_delay > 0:
                    self._wait(self.options.inter_device_delay)
                if not self._run_device(client, i):
                    self._fail()
                    if self.options.stop_on_error:
                        break
        finally:
            try:
                client.disconnect()
            except Exception:  # noqa: BLE001
                pass

    def _run_device(self, client, idx: int) -> bool:
        r = self.results[idx]
        d = r.target
        o = self.options
        r.started = self._clock()
        client.unit_id = d.unit_id
        recover = self._make_recover(client)
        self._set(idx, PREFLIGHT, "reading SN / model")
        self._progress(f"{d.label()} pre-flight")
        n = 0
        while True:
            n += 1
            try:
                ident = self.identity_reader(client)
                break
            except Exception as exc:  # noqa: BLE001
                if n > o.retries or self._halt.is_set():
                    r.preflight_attempts = n
                    self._set_ops(idx, self._ops_per_device)
                    self._set(idx, ERROR, f"skipped — pre-flight read failed after {n} attempt(s): {exc}")
                    return False
                self._wait(o.retry_delay)
                try:
                    recover()
                except Exception:  # noqa: BLE001
                    pass
        r.preflight_attempts = n
        r.sn = str(ident.get("serial_number") or "")
        r.model = str(ident.get("model_no_display") or "")
        kw = dict(retries=o.retries, retry_delay=o.retry_delay, sleep=self._sleep_fn(), recover=recover)
        if o.read_only:
            return self._compare_device(client, idx, kw)
        r.written = 0
        self._set(idx, WRITING, "")

        def on_written(_item) -> None:
            with self._lock:
                r.written += 1
            self._set_ops(idx, r.written)
            self._emit(idx)

        try:
            write_items(client, self.items, cancel=self._halt, on_written=on_written,
                        on_progress=lambda a, b, t: self._progress(f"{d.label()} {t}"),
                        attempts_out=r.write_attempts, **kw)
        except WriteCancelled as exc:
            self._set(idx, CANCELLED, f"{self._halt_reason()} after {exc.written}/{exc.total} writes")
            return True
        except WriteFailed as exc:
            r.error_address = exc.item.address
            self._set_ops(idx, self._ops_per_device)
            self._set(idx, ERROR, f"{exc} (after {o.retries + 1} attempt(s))")
            return False
        n_items = len(self.items)
        if not o.verify:
            self._set_ops(idx, self._ops_per_device)
            self._set(idx, OK, f"{n_items} written (not verified){_retry_note(r.retries_used)}")
            return True
        self._set(idx, VERIFYING, "")
        try:
            issues = verify_items(client, self.items, cancel=self._halt, attempts_out=r.verify_attempts,
                                  on_progress=lambda a, b, t: (self._set_ops(idx, n_items + a),
                                                               self._progress(f"{d.label()} {t}")), **kw)
        except WriteCancelled:
            self._set(idx, CANCELLED, f"all {n_items} written; verify {self._halt_reason()}")
            return True
        r.verify_issues = issues
        self._set_ops(idx, self._ops_per_device)
        if issues:
            self._set(idx, MISMATCH, _issues_text(issues) + f" after {o.retries + 1} attempt(s)")
            return False
        self._set(idx, OK, f"{n_items} written, verified{_retry_note(r.retries_used)}")
        return True

    def _compare_device(self, client, idx: int, kw) -> bool:
        r = self.results[idx]
        d = r.target
        o = self.options
        self._set(idx, VERIFYING, "reading registers (no writes)")
        try:
            issues = compare_items(client, self.items, cancel=self._halt, attempts_out=r.verify_attempts,
                                   on_progress=lambda a, b, t: (self._set_ops(idx, a),
                                                                self._progress(f"{d.label()} {t}")), **kw)
        except WriteCancelled as exc:
            self._set(idx, CANCELLED, f"{self._halt_reason()} after {exc.written}/{len(self.items)} reads")
            return True
        r.verify_issues = issues
        self._set_ops(idx, self._ops_per_device)
        errors = [i for i in issues if i.actual is None]
        if o.dry_run and not o.verify_only:
            if errors:
                self._set(idx, ERROR, f"dry-run: {len(errors)} read error(s): " + _issues_text(errors))
                return False
            self._set(idx, DRY_RUN_OK, f"dry-run: {len(issues)} of {len(self.items)} register(s) would change"
                      + (": " + ", ".join(str(i.address) for i in issues[:8]) if issues else "")
                      + ("…" if len(issues) > 8 else ""))
            return True
        if not issues:
            self._set(idx, OK, f"all {len(self.items)} registers match{_retry_note(r.retries_used)}")
            return True
        self._set(idx, MISMATCH, _issues_text(issues))
        return False


def _issues_text(issues: Sequence[VerifyIssue]) -> str:
    nm = sum(1 for i in issues if i.kind == "mismatch")
    ne = len(issues) - nm
    parts = ([f"{nm} mismatch"] if nm else []) + ([f"{ne} read error"] if ne else [])
    first = ", ".join(f"{i.address}={words_text(i.actual)}≠{words_text(i.expected)}" for i in issues[:4])
    return f"{' + '.join(parts)}: {first}{'…' if len(issues) > 4 else ''}"


REPORT_HEADER = ["host", "port", "unit_id", "sn", "model", "device_status", "device_message", "written",
                 "total", "retries", "address", "register", "expected", "actual", "attempts", "issue"]
_REPORT_LOCK = threading.Lock()


def write_report_csv(path: Union[str, Path], summary: BatchSummary, *, profile_name: str = "",
                     items: Optional[Sequence[WriteItem]] = None) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    names = {i.address: i.name for i in (items or [])}
    exp = {i.address: words_text(i.words) for i in (items or [])}
    with _REPORT_LOCK, p.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([f"# Solis Modbus Config batch ({summary.mode}) {summary.started_at:%Y-%m-%d %H:%M:%S}"
                    + (f" profile={profile_name}" if profile_name else "")
                    + f" parallel={summary.parallel} retries={summary.retries} — {summary.text()}"])
        w.writerow(REPORT_HEADER)
        for r in summary.sorted_results():
            d = r.target
            base = [d.host, d.port, d.unit_id, r.sn, r.model, r.status, r.message, r.written, r.total,
                    r.retries_used]
            w.writerow(base + [""] * 6)
            if r.connect_attempts > 1:
                w.writerow(base + ["", "connect", "", "", r.connect_attempts,
                                   "connect failed" if r.status == ERROR and not r.sn
                                   else f"OK after {r.connect_attempts - 1} retries"])
            if r.preflight_attempts > 1:
                w.writerow(base + ["33000", "pre-flight SN/model", "", "", r.preflight_attempts,
                                   "pre-flight failed" if not r.sn else f"OK after {r.preflight_attempts - 1} retries"])
            bad = {vi.address for vi in r.verify_issues}
            if r.status == ERROR and r.error_address is not None:
                bad.add(r.error_address)
                w.writerow(base + [r.error_address, names.get(r.error_address, ""), exp.get(r.error_address, ""),
                                   "", r.write_attempts.get(r.error_address, ""), "write failed"])
            for addr in sorted(set(r.write_attempts) | set(r.verify_attempts)):
                if addr in bad:
                    continue
                n = max(r.write_attempts.get(addr, 1), r.verify_attempts.get(addr, 1))
                what = "write" if r.write_attempts.get(addr, 1) > 1 else "verify"
                w.writerow(base + [addr, names.get(addr, ""), exp.get(addr, ""), "", n,
                                   f"OK after {n - 1} retries ({what})"])
            for vi in r.verify_issues:
                w.writerow(base + [vi.address, vi.name, words_text(vi.expected),
                                   "" if vi.actual is None else words_text(vi.actual),
                                   r.verify_attempts.get(vi.address, 1),
                                   ("would change" if summary.mode == "dry-run" and vi.actual is not None
                                    else vi.kind) + (f": {vi.message}" if vi.actual is None else "")])
    return p


def default_report_name(when: Optional[datetime] = None, mode: str = "write") -> str:
    return f"{'batch' if mode == 'write' else mode}_{(when or datetime.now()):%Y%m%d_%H%M%S}.csv"
