"""In-memory, thread-safe fake of SolisModbusClient for tests (no network)."""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Dict, List, Set, Tuple

from app.modbus_client import ModbusError


class FakeBus:
    def __init__(self, latency: float = 0.0) -> None:
        self.latency = latency
        self.lock = threading.RLock()
        self.regs: Dict[Tuple[str, int, int], Dict[int, int]] = {}
        self.sn: Dict[Tuple[str, int, int], str] = {}
        self.unreachable_hosts: Set[Tuple[str, int]] = set()
        self.dead_units: Set[Tuple[str, int, int]] = set()
        self.fail_write: Dict[Tuple[str, int, int], int] = {}
        self.ignore_write: Dict[Tuple[str, int, int], int] = {}
        self.connect_failures: Dict[Tuple[str, int], int] = defaultdict(int)
        self.preflight_failures: Dict[Tuple[str, int, int], int] = defaultdict(int)
        self.write_failures: Dict[Tuple[str, int, int, int], int] = defaultdict(int)
        self.read_failures: Dict[Tuple[str, int, int, int], int] = defaultdict(int)
        self.ignore_write_times: Dict[Tuple[str, int, int, int], int] = defaultdict(int)
        self.drop_on_fail = False
        self.log: List[tuple] = []
        self.open_sessions = 0
        self.max_open_sessions = 0
        self.open_per_endpoint: Dict[Tuple[str, int], int] = defaultdict(int)
        self.max_per_endpoint: Dict[Tuple[str, int], int] = defaultdict(int)
        self.intervals: Dict[Tuple[str, int], List[List[float]]] = defaultdict(list)
        self.on_write = None

    def add(self, host: str, port: int, unit: int, sn: str = "SN0001", regs=None) -> None:
        self.regs[(host, port, unit)] = dict(regs or {})
        self.sn[(host, port, unit)] = sn

    def client(self, host: str, port: int, unit_id: int, timeout: float) -> "FakeClient":
        return FakeClient(self, host, port, unit_id)

    def _log(self, *entry) -> None:
        with self.lock:
            self.log.append(entry)


def _sn_regs(sn: str) -> List[int]:
    b = sn.encode("ascii").ljust(32, b"\0")
    return [(b[i] << 8) | b[i + 1] for i in range(0, 32, 2)]


class FakeClient:
    def __init__(self, bus: FakeBus, host: str, port: int, unit_id: int) -> None:
        self.bus, self.host, self.port, self.unit_id = bus, host, port, unit_id
        self.timeout = 3.0
        self._open = False

    @property
    def ep(self) -> Tuple[str, int]:
        return (self.host, self.port)

    @property
    def connected(self) -> bool:
        return self._open

    def connect(self) -> None:
        b = self.bus
        b._log("connect", self.host, self.port, None, None, None)
        with b.lock:
            if self.ep in b.unreachable_hosts:
                raise ModbusError(f"Timeout connecting to {self.host}:{self.port}")
            if b.connect_failures[self.ep] > 0:
                b.connect_failures[self.ep] -= 1
                raise ModbusError(f"Timeout connecting to {self.host}:{self.port}")
            if self._open:
                raise AssertionError("connect() on an already open client")
            self._open = True
            b.open_sessions += 1
            b.max_open_sessions = max(b.max_open_sessions, b.open_sessions)
            b.open_per_endpoint[self.ep] += 1
            b.max_per_endpoint[self.ep] = max(b.max_per_endpoint[self.ep], b.open_per_endpoint[self.ep])
            b.intervals[self.ep].append([time.monotonic(), None])

    def disconnect(self) -> None:
        b = self.bus
        with b.lock:
            if self._open:
                b.open_sessions -= 1
                b.open_per_endpoint[self.ep] -= 1
                b.intervals[self.ep][-1][1] = time.monotonic()
                b.log.append(("close", self.host, self.port, None, None, None))
            self._open = False

    def _key(self) -> Tuple[str, int, int]:
        if not self._open:
            raise ModbusError("Not connected")
        key = (self.host, self.port, self.unit_id)
        if key not in self.bus.regs or key in self.bus.dead_units:
            raise ModbusError(f"No response from unit {self.unit_id}")
        return key

    def _delay(self) -> None:
        if self.bus.latency:
            time.sleep(self.bus.latency)

    def _transient(self, table, k, what: str) -> None:
        with self.bus.lock:
            if table[k] > 0:
                table[k] -= 1
                if self.bus.drop_on_fail:
                    self.disconnect()
                raise ModbusError(f"No response {what} (transient)")

    def _read(self, address: int, count: int) -> List[int]:
        key = self._key()
        self._delay()
        self.bus._log("read", self.host, self.port, self.unit_id, address, count)
        if address == 33000 and count >= 20:
            self._transient(self.bus.preflight_failures, key, "pre-flight")
            base = [0x3105, 1, 2, 3] + _sn_regs(self.bus.sn[key])
            extra = [self.bus.regs[key].get(33000 + i, 0) for i in range(20, count)]
            return (base + extra)[:count]
        self._transient(self.bus.read_failures, key + (address,), f"reading {address}")
        with self.bus.lock:
            return [self.bus.regs[key].get(address + i, 0) for i in range(count)]

    def read_registers(self, address: int, count: int = 1) -> List[int]:
        return self._read(address, count)

    def read_holding(self, address: int, count: int = 1) -> List[int]:
        return self._read(address, count)

    def read_input(self, address: int, count: int = 1) -> List[int]:
        return self._read(address, count)

    def read_u16(self, address: int) -> int:
        return self.read_registers(address, 1)[0]

    def write_single(self, address: int, value: int) -> None:
        self._write(address, [value])

    def write_multiple(self, address: int, values) -> None:
        self._write(address, list(values))

    def write_words(self, address: int, words) -> None:
        words = list(words)
        if len(words) == 1:
            self.write_single(address, words[0])
        else:
            self.write_multiple(address, words)

    def _write(self, address: int, values) -> None:
        key = self._key()
        self._delay()
        self.bus._log("write", self.host, self.port, self.unit_id, address, list(values))
        if self.bus.on_write:
            self.bus.on_write(self.bus, key, address)
        if self.bus.fail_write.get(key) == address:
            raise ModbusError(f"Modbus error writing {address}: illegal data value")
        self._transient(self.bus.write_failures, key + (address,), f"writing {address}")
        if self.bus.ignore_write.get(key) == address:
            return
        with self.bus.lock:
            if self.bus.ignore_write_times[key + (address,)] > 0:
                self.bus.ignore_write_times[key + (address,)] -= 1
                return
            for i, v in enumerate(values):
                self.bus.regs[key][address + i] = int(v) & 0xFFFF
