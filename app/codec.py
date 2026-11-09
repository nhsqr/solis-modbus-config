"""Raw Modbus words <-> engineering values for catalog registers.

* U16/S16: one word; U32/S32: high word first (V3.5 §3.1).
* ASCII: two characters per word, high byte first.
* scale: engineering = raw * scale (e.g. 0.1 V).
* 43090-43105 depend on 43081 "Grid standard parameters accuracy" (Ver3.2):
  mode 0: V = 1 V, T = 100 ms, F = 0.1 Hz; mode 1: V = 0.1 V, T = 10 ms,
  F = 0.01 Hz (= the V3.5 table). The scale used is mode 1 unless 43081 was
  read and returned 0.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Union

from app.catalog import Register

ACCURACY_REGISTER = 43081
_ACC_V = {43090, 43092, 43094, 43096}
_ACC_T = {43091, 43093, 43095, 43097, 43099, 43101, 43103, 43105}
_ACC_F = {43098, 43100, 43102, 43104}
ACCURACY_SCALES = {0: {"V": 1.0, "T": 0.1, "F": 0.1}, 1: {"V": 0.1, "T": 0.01, "F": 0.01}}


class CodecError(ValueError):
    pass


def type_limits(reg: Register):
    return {"U16": (0, 0xFFFF), "S16": (-0x8000, 0x7FFF), "U32": (0, 0xFFFFFFFF),
            "S32": (-0x80000000, 0x7FFFFFFF)}.get(reg.type, (0, (1 << (16 * reg.words)) - 1))


def effective_scale(reg: Register, accuracy_mode: Optional[int] = None) -> float:
    if accuracy_mode in ACCURACY_SCALES:
        s = ACCURACY_SCALES[accuracy_mode]
        if reg.address in _ACC_V:
            return s["V"]
        if reg.address in _ACC_F:
            return s["F"]
        if reg.address in _ACC_T:
            # catalog unit is "10ms" (scale 10, unit ms) → keep ms as unit
            return s["T"] * 1000.0 if reg.unit == "ms" else s["T"]
    return reg.scale


def words_to_raw(reg: Register, words: Sequence[int]) -> Union[int, str]:
    w = [int(x) & 0xFFFF for x in words]
    if len(w) < reg.words:
        raise CodecError(f"{reg.address}: need {reg.words} word(s), got {len(w)}")
    if reg.kind == "ascii" or reg.type == "ASCII":
        chars = []
        for x in w[: reg.words]:
            for b in ((x >> 8) & 0xFF, x & 0xFF):
                if 32 <= b <= 126:
                    chars.append(chr(b))
        return "".join(chars).strip()
    v = 0
    for x in w[: reg.words]:
        v = (v << 16) | x
    bits = 16 * reg.words
    if reg.signed and v >= 1 << (bits - 1):
        v -= 1 << bits
    return v


def raw_to_words(reg: Register, raw: Union[int, str]) -> List[int]:
    if reg.kind == "ascii" or reg.type == "ASCII":
        s = str(raw).encode("ascii", "replace")[: 2 * reg.words].ljust(2 * reg.words, b"\0")
        return [(s[i] << 8) | s[i + 1] for i in range(0, len(s), 2)]
    lo, hi = type_limits(reg)
    raw = int(raw)
    if not lo <= raw <= hi:
        raise CodecError(f"{reg.address}: raw {raw} outside {reg.type} range {lo}…{hi}")
    bits = 16 * reg.words
    v = raw & ((1 << bits) - 1)
    return [(v >> (16 * (reg.words - 1 - i))) & 0xFFFF for i in range(reg.words)]


def raw_to_eng(reg: Register, raw: int, accuracy_mode: Optional[int] = None) -> float:
    return int(raw) * effective_scale(reg, accuracy_mode)


def eng_to_raw(reg: Register, eng: float, accuracy_mode: Optional[int] = None) -> int:
    sc = effective_scale(reg, accuracy_mode)
    if sc == 0:
        raise CodecError("scale 0")
    raw = int(round(float(eng) / sc))
    lo, hi = type_limits(reg)
    if not lo <= raw <= hi:
        raise CodecError(f"{reg.address}: {eng} {reg.unit} → raw {raw} outside {reg.type} {lo}…{hi}")
    return raw


def parse_number(text: str) -> float:
    t = str(text).strip().replace(",", ".").replace(" ", "")
    if t.lower().startswith("0x"):
        return float(int(t, 16))
    v = float(t)
    if math.isnan(v) or math.isinf(v):
        raise ValueError("not a number")
    return v


def fmt_num(v: float, scale: float) -> str:
    if scale >= 1 or float(v).is_integer() and scale >= 1:
        return str(int(round(v)))
    dec = max(0, min(4, -int(math.floor(math.log10(scale))) if scale > 0 else 0))
    return f"{v:.{dec}f}"


def format_value(reg: Register, raw: Union[int, str], accuracy_mode: Optional[int] = None) -> str:
    """Human-readable value (enum label, bits, bytes, hex, ascii or number+unit)."""
    if isinstance(raw, str):
        return raw
    if reg.kind == "enum" and reg.options:
        lab = reg.options.get(int(raw) & 0xFFFF if not reg.signed else int(raw))
        return f"{raw} – {lab}" if lab else f"{raw} (не е в списъка)"
    if reg.kind == "bits" and reg.bits:
        on = []
        for b in reg.bits:
            if b.reserved:
                continue
            v = b.get(raw)
            if b.width == 1 and v and not b.states:
                on.append(f"BIT{b.lo:02d}")
            elif v or b.states:
                st = b.states.get(str(v))
                if v or (st and b.width > 1):
                    on.append(f"BIT{b.lo:02d}={v}" + (f" {st}" if st else ""))
        return f"0x{int(raw) & 0xFFFF:04X}" + (f" [{', '.join(on)}]" if on else "")
    if reg.kind == "bytes":
        h, l = (int(raw) >> 8) & 0xFF, int(raw) & 0xFF
        if reg.hi_options or reg.lo_options:
            hl = (reg.hi_options or {}).get(h, str(h))
            ll = (reg.lo_options or {}).get(l, str(l))
            return f"0x{int(raw) & 0xFFFF:04X} (high {h}: {hl}; low {l}: {ll})"
        return f"0x{int(raw) & 0xFFFF:04X} (high {h}, low {l})"
    if reg.kind == "hex":
        return f"0x{int(raw) & ((1 << (16 * reg.words)) - 1):0{4 * reg.words}X}"
    sc = effective_scale(reg, accuracy_mode)
    eng = int(raw) * sc
    unit = reg.unit
    return f"{fmt_num(eng, sc)} {unit}".strip()


def bit_values(reg: Register, raw: int) -> Dict[int, int]:
    return {b.lo: b.get(raw) for b in reg.bits}


def compose_bits(reg: Register, old_raw: int, changes: Dict[int, int]) -> int:
    """Read-modify-write: keep every bit of old_raw except the changed fields."""
    raw = int(old_raw) & ((1 << (16 * reg.words)) - 1)
    by_lo = {b.lo: b for b in reg.bits}
    for lo, v in changes.items():
        b = by_lo.get(lo)
        if b is None:
            raise CodecError(f"{reg.address}: no bit field at BIT{lo:02d}")
        raw = b.set(raw, v)
    return raw


def compose_bytes(high: int, low: int) -> int:
    if not (0 <= int(high) <= 255 and 0 <= int(low) <= 255):
        raise CodecError("byte values must be 0…255")
    return (int(high) << 8) | int(low)


def range_check(reg: Register, raw: int, accuracy_mode: Optional[int] = None) -> Optional[str]:
    """Message when raw is outside the documented range (soft check), else None."""
    rg = reg.range
    if not rg or rg.get("min") is None or reg.kind not in ("value", "hex"):
        return None
    lo, hi, basis = rg["min"], rg["max"], rg.get("basis")
    if basis == "raw":
        v, unit = raw, "raw"
    elif basis == "eng":
        v, unit = raw_to_eng(reg, raw, accuracy_mode), reg.unit
    else:
        return None
    if lo - 1e-9 <= v <= hi + 1e-9:
        return None
    return f"{fmt_num(v, 0.0001)} {unit} е извън документирания обхват {lo:g}…{hi:g} ({rg.get('text', '')})"
