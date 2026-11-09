"""Catalog-aware Modbus operations: read registers in blocks, write + verify one setting."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple, Union

from app.catalog import Register, read_blocks
from app.codec import ACCURACY_REGISTER, CodecError, raw_to_words, words_to_raw
from app.writer import WriteItem, read_item, write_item

#: V3.5 §3: query interval > 300 ms
DEFAULT_REQUEST_INTERVAL = 0.3


@dataclass
class ReadResult:
    values: Dict[int, Union[int, str]] = field(default_factory=dict)   # address → raw value
    words: Dict[int, Tuple[int, ...]] = field(default_factory=dict)
    errors: Dict[int, str] = field(default_factory=dict)
    requests: int = 0

    @property
    def accuracy_mode(self) -> Optional[int]:
        v = self.values.get(ACCURACY_REGISTER)
        return int(v) if isinstance(v, int) and v in (0, 1) else None


def make_item(reg: Register, raw: Union[int, str], text: str = "") -> WriteItem:
    if not reg.writable:
        raise CodecError(f"{reg.address} is read-only")
    return WriteItem(reg.address, reg.name, tuple(raw_to_words(reg, raw)), text)


def read_registers(client, regs: Sequence[Register], *, max_count: int = 50,
                   interval: float = DEFAULT_REQUEST_INTERVAL, cancel=None,
                   on_progress: Optional[Callable[[int, int, str], None]] = None,
                   sleep: Callable[[float], None] = time.sleep) -> ReadResult:
    """Read in contiguous blocks; a failing block falls back to single-register reads.

    Never writes. Unsupported registers (Modbus exception) end up in ``errors``.
    """
    res = ReadResult()
    blocks = read_blocks(regs, max_count=max_count)
    total = len(blocks)
    first = True

    def pause():
        nonlocal first
        if not first and interval > 0:
            sleep(interval)
        first = False

    for bi, (fc, start, count, members) in enumerate(blocks):
        if cancel is not None and cancel.is_set():
            break
        if on_progress:
            on_progress(bi, total, f"FC{fc:02d} {start}+{count}")
        pause()
        res.requests += 1
        try:
            words = _read(client, fc, start, count)
            for r in members:
                w = tuple(words[r.address - start: r.address - start + r.words])
                res.words[r.address] = w
                res.values[r.address] = words_to_raw(r, w)
            continue
        except Exception as exc:  # noqa: BLE001
            if len(members) == 1:
                res.errors[members[0].address] = str(exc)
                continue
        for r in members:
            if cancel is not None and cancel.is_set():
                break
            pause()
            res.requests += 1
            try:
                w = tuple(_read(client, fc, r.address, r.words))
                res.words[r.address] = w
                res.values[r.address] = words_to_raw(r, w)
            except Exception as exc:  # noqa: BLE001
                res.errors[r.address] = str(exc)
    if on_progress:
        on_progress(total, total, "done")
    return res


def _read(client, fc: int, address: int, count: int) -> List[int]:
    if fc == 4:
        return list(client.read_input(address, count))
    return list(client.read_holding(address, count))


def read_one(client, reg: Register) -> Union[int, str]:
    w = _read(client, reg.fc, reg.address, reg.words)
    return words_to_raw(reg, w)


@dataclass
class WriteOutcome:
    ok: bool
    written: Tuple[int, ...]
    readback: Optional[Tuple[int, ...]]
    message: str


def write_setting(client, reg: Register, raw: Union[int, str], *, verify: bool = True,
                  verify_delay: float = 0.3, sleep: Callable[[float], None] = time.sleep) -> WriteOutcome:
    """Write one setting (FC06 / FC16 for 32-bit) and read it back."""
    item = make_item(reg, raw)
    write_item(client, item)
    if not verify:
        return WriteOutcome(True, item.words, None, "written (not verified)")
    if verify_delay > 0:
        sleep(verify_delay)
    rb = read_item(client, item)
    if rb == item.words:
        return WriteOutcome(True, item.words, rb, "written and verified")
    return WriteOutcome(False, item.words, rb, f"read-back {list(rb)} ≠ written {list(item.words)}")
