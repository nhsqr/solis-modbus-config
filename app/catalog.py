"""Register catalog: data/registers.json (generated from the PDFs) + Bulgarian names.

GUI-agnostic. ``Catalog.load()`` finds the data folder next to the package or
inside a PyInstaller bundle.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Sequence


def data_dir() -> Path:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        p = Path(base) / "data"
        if p.is_dir():
            return p
    return Path(__file__).resolve().parent.parent / "data"


_NUM_RE = re.compile(r"(?<![A-Za-z])\d+(?![A-Za-z%])")


def name_template(name: str) -> str:
    return _NUM_RE.sub("#", name.replace('"', "").strip())


def load_bg_names(path: Optional[Path] = None) -> Dict[str, str]:
    p = path or data_dir() / "bg_names.tsv"
    out: Dict[str, str] = {}
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            if "\t" in line:
                k, v = line.split("\t", 1)
                out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


def fill_template(bg: str, name: str) -> str:
    nums = _NUM_RE.findall(name.replace('"', ""))
    it = iter(nums)
    return re.sub("#", lambda m: next(it, "#"), bg)


@dataclass
class BitField:
    lo: int
    hi: int
    name: str
    states: Dict[str, str] = field(default_factory=dict)
    default: Optional[int] = None
    reserved: bool = False
    text: str = ""

    @property
    def width(self) -> int:
        return self.hi - self.lo + 1

    @property
    def mask(self) -> int:
        return ((1 << self.width) - 1) << self.lo

    def get(self, raw: int) -> int:
        return (int(raw) & self.mask) >> self.lo

    def set(self, raw: int, value: int) -> int:
        value = int(value)
        if not 0 <= value < (1 << self.width):
            raise ValueError(f"BIT{self.lo:02d}{'-' + str(self.hi) if self.hi != self.lo else ''}: "
                             f"value {value} does not fit in {self.width} bit(s)")
        return (int(raw) & ~self.mask) | (value << self.lo)

    def label(self) -> str:
        b = f"BIT{self.lo:02d}" if self.lo == self.hi else f"BIT{self.lo:02d}-{self.hi:02d}"
        return f"{b} {self.name}"


@dataclass
class Register:
    address: int
    words: int
    fc: int
    writable: bool
    type: str
    scale: float
    unit: str
    name: str
    group: str
    source: str
    sources: List[dict]
    description: str = ""
    kind: str = "value"
    options: Optional[Dict[int, str]] = None
    lo_options: Optional[Dict[int, str]] = None
    hi_options: Optional[Dict[int, str]] = None
    bits: List[BitField] = field(default_factory=list)
    appendix: str = ""
    range: Optional[dict] = None
    default: Optional[dict] = None
    danger: bool = False
    volatile: bool = False
    notes: List[str] = field(default_factory=list)
    description_v32: str = ""
    name_v32: str = ""
    name_bg: str = ""
    section: str = ""

    @property
    def signed(self) -> bool:
        return self.type in ("S16", "S32")

    @property
    def end(self) -> int:
        return self.address + self.words - 1

    @property
    def addr_text(self) -> str:
        return str(self.address) if self.words == 1 else f"{self.address}~{self.end}"

    @property
    def display_name(self) -> str:
        return self.name_bg or self.name

    @classmethod
    def from_json(cls, d: dict, bg: Dict[str, str]) -> "Register":
        def intkeys(m):
            return {int(k): v for k, v in m.items()} if m else None
        bits = [BitField(b["lo"], b["hi"], b.get("name", ""), dict(b.get("states") or {}), b.get("default"),
                         bool(b.get("reserved")), b.get("text", "")) for b in (d.get("bits") or [])]
        name = d.get("name", "")
        tmpl = bg.get(name_template(name))
        return cls(
            address=int(d["address"]), words=int(d.get("words", 1)), fc=int(d.get("fc", 3)),
            writable=bool(d.get("writable")), type=d.get("type", "U16"), scale=float(d.get("scale", 1.0)),
            unit=d.get("unit", ""), name=name, group=d.get("group", "other"), source=d.get("source", ""),
            sources=list(d.get("sources") or []), description=d.get("description", ""),
            kind=d.get("kind", "value"), options=intkeys(d.get("options")),
            lo_options=intkeys(d.get("lo_options")), hi_options=intkeys(d.get("hi_options")),
            bits=bits, appendix=d.get("appendix", ""), range=d.get("range"), default=d.get("default"),
            danger=bool(d.get("danger")), volatile=bool(d.get("volatile")), notes=list(d.get("notes") or []),
            description_v32=d.get("description_v32", ""), name_v32=d.get("name_v32", ""),
            name_bg=fill_template(tmpl, name) if tmpl else "", section=d.get("section") or "",
        )


class Catalog:
    def __init__(self, registers: Sequence[Register]) -> None:
        self.registers: List[Register] = sorted(registers, key=lambda r: r.address)
        self._by_addr = {r.address: r for r in self.registers}

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Catalog":
        p = path or data_dir() / "registers.json"
        data = json.loads(Path(p).read_text(encoding="utf-8"))
        bg = load_bg_names()
        return cls([Register.from_json(d, bg) for d in data["registers"]])

    def get(self, address: int) -> Optional[Register]:
        return self._by_addr.get(int(address))

    def __len__(self) -> int:
        return len(self.registers)

    def settings(self) -> List[Register]:
        return [r for r in self.registers if r.writable]

    def monitors(self) -> List[Register]:
        return [r for r in self.registers if not r.writable]

    def in_group(self, gid: str, writable: bool) -> List[Register]:
        return [r for r in self.registers if r.writable == writable and r.group == gid]

    def search(self, text: str, writable: Optional[bool] = None) -> List[Register]:
        t = text.strip().lower()
        out = []
        for r in self.registers:
            if writable is not None and r.writable != writable:
                continue
            if not t or t in str(r.address) or t in r.name.lower() or t in r.name_bg.lower():
                out.append(r)
        return out


@lru_cache(maxsize=1)
def default_catalog() -> Catalog:
    return Catalog.load()


def read_blocks(regs: Sequence[Register], max_count: int = 50, max_gap: int = 0):
    """Group registers of one FC into contiguous read blocks (Ver3.2: <=50 regs/frame).

    Returns [(fc, start, count, [Register...])]. With max_gap=0 only touching
    registers are merged, so no undefined addresses are read.
    """
    out = []
    for fc in (3, 4):
        rs = sorted((r for r in regs if r.fc == fc), key=lambda r: r.address)
        cur: list = []
        start = end = None
        for r in rs:
            if cur and r.address <= end + 1 + max_gap and (r.end - start + 1) <= max_count:
                cur.append(r)
                end = max(end, r.end)
            else:
                if cur:
                    out.append((fc, start, end - start + 1, cur))
                cur = [r]
                start, end = r.address, r.end
        if cur:
            out.append((fc, start, end - start + 1, cur))
    return out
