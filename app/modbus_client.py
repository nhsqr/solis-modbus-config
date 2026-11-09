"""Thin Modbus TCP client wrapper.

Solis protocol V3.5 (§3.3) / Ver3.2:
  FC 0x04 Input registers   — operating info 33000-36999 and 25000-25999
  FC 0x03 Holding registers — settings read 43000-44999
  FC 0x06                   — write one setting register (recommended, §3.4)
  FC 0x10                   — write several registers (used only for 32-bit /
                              multi-word settings, which must change atomically)
"""

from __future__ import annotations

import socket
from typing import List, Optional

try:
    from pymodbus.client import ModbusTcpClient
except ImportError:  # pragma: no cover
    ModbusTcpClient = None  # type: ignore


class ModbusError(RuntimeError):
    pass


def uses_input_registers(address: int) -> bool:
    """3xxxx operating info and 25xxx (FCAS, V3.5 §6.1) use FC04 input registers."""
    a = int(address)
    return 30000 <= a < 40000 or 25000 <= a < 26000


class SolisModbusClient:
    def __init__(
        self,
        host: str = "192.168.85.104",
        port: int = 502,
        unit_id: int = 1,
        timeout: float = 3.0,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.unit_id = int(unit_id)
        self.timeout = float(timeout)
        self._client: Optional[ModbusTcpClient] = None

    @property
    def connected(self) -> bool:
        return self._client is not None and bool(self._client.connected)

    def connect(self) -> None:
        if ModbusTcpClient is None:
            raise ModbusError("pymodbus is not installed")
        self.disconnect()
        self._client = ModbusTcpClient(
            host=self.host,
            port=self.port,
            timeout=self.timeout,
        )
        try:
            ok = self._client.connect()
        except (TimeoutError, socket.timeout) as exc:
            self._client = None
            raise ModbusError(f"Timeout connecting to {self.host}:{self.port}") from exc
        except OSError as exc:
            self._client = None
            msg = str(exc).lower()
            if "timed out" in msg or "timeout" in msg:
                raise ModbusError(f"Timeout connecting to {self.host}:{self.port}") from exc
            raise ModbusError(
                f"Could not connect to {self.host}:{self.port} ({exc})"
            ) from exc
        except Exception as exc:  # pragma: no cover
            self._client = None
            raise ModbusError(
                f"Could not connect to {self.host}:{self.port} ({exc})"
            ) from exc
        if not ok:
            self._client = None
            raise ModbusError(f"Could not connect to {self.host}:{self.port}")

    def disconnect(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    def _require(self) -> ModbusTcpClient:
        if not self.connected or self._client is None:
            raise ModbusError("Not connected")
        return self._client

    def _check_rr(self, rr, address: int, count: int, kind: str) -> List[int]:
        if rr is None:
            raise ModbusError(f"No response reading {address} ({kind})")
        if hasattr(rr, "isError") and rr.isError():
            raise ModbusError(f"Modbus error reading {address} ({kind}): {rr}")
        regs = list(rr.registers)
        if len(regs) < count:
            raise ModbusError(
                f"Short read at {address} ({kind}): got {len(regs)} of {count}"
            )
        return regs

    def read_holding(self, address: int, count: int = 1) -> List[int]:
        """FC03 — holding / settings registers (43xxx)."""
        client = self._require()
        rr = client.read_holding_registers(
            address=int(address),
            count=int(count),
            device_id=self.unit_id,
        )
        return self._check_rr(rr, address, count, "FC03")

    def read_input(self, address: int, count: int = 1) -> List[int]:
        """FC04 — input / operating-info registers (33xxx)."""
        client = self._require()
        rr = client.read_input_registers(
            address=int(address),
            count=int(count),
            device_id=self.unit_id,
        )
        return self._check_rr(rr, address, count, "FC04")

    def read_registers(self, address: int, count: int = 1) -> List[int]:
        """Auto-select FC04 for 33xxx info, FC03 for settings."""
        if uses_input_registers(address):
            return self.read_input(address, count)
        return self.read_holding(address, count)

    def write_single(self, address: int, value: int) -> None:
        client = self._require()
        value = int(value) & 0xFFFF
        wr = client.write_register(
            address=int(address),
            value=value,
            device_id=self.unit_id,
        )
        if wr is None:
            raise ModbusError(f"No response writing {address}")
        if hasattr(wr, "isError") and wr.isError():
            raise ModbusError(f"Modbus error writing {address}: {wr}")

    def write_multiple(self, address: int, values) -> None:
        """FC16 — used for 32-bit / multi-word settings."""
        client = self._require()
        vals = [int(v) & 0xFFFF for v in values]
        wr = client.write_registers(
            address=int(address),
            values=vals,
            device_id=self.unit_id,
        )
        if wr is None:
            raise ModbusError(f"No response writing {address}")
        if hasattr(wr, "isError") and wr.isError():
            raise ModbusError(f"Modbus error writing {address}: {wr}")

    def write_words(self, address: int, words) -> None:
        """One word → FC06; several → FC16 (high word first)."""
        words = list(words)
        if len(words) == 1:
            self.write_single(address, words[0])
        else:
            self.write_multiple(address, words)

    def read_u16(self, address: int) -> int:
        return self.read_registers(address, 1)[0]

    def read_u32(self, address: int) -> int:
        """Read U32: high word at address, low word at address+1 (Solis convention)."""
        regs = self.read_registers(address, 2)
        return ((int(regs[0]) & 0xFFFF) << 16) | (int(regs[1]) & 0xFFFF)

    def read_s32(self, address: int) -> int:
        """Read S32: high word at address, low word at address+1 (Solis convention)."""
        u = self.read_u32(address)
        return u - 0x100000000 if u >= 0x80000000 else u


def connected_status(client) -> str:
    """Status-bar text after Connect (no Modbus request is made on connect)."""
    return f"Connected to {client.host}:{client.port}, ID {client.unit_id}"


def friendly_connect_status(err: str) -> str:
    """Short status-bar message for a failed Connect."""
    low = (err or "").lower()
    if "timeout" in low or "timed out" in low:
        return "Timeout — could not reach the inverter"
    if "not installed" in low:
        return "Could not connect — pymodbus missing"
    if "could not connect" in low or "failed to connect" in low:
        return "Could not connect"
    return f"Could not connect — {err}"
