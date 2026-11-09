"""Extract register rows from the Solis protocol PDFs (word geometry + table rules).

Usage: python tools/extract_tables.py  -> writes data/raw/*.json

Each register table in both PDFs has 5 fixed columns (address, name, type,
unit, remarks).  Rows are delimited by horizontal rules that cross the
address column; text in a band without an address continues the previous
register (page breaks, merged cells).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).resolve().parents[1]
DOCS = Path("/workspace/solis-docs")

DOCS_CFG = {
    "V3.5": {
        "file": "Solis_Energy_Storage_Modbus_Protocol_V3.5.pdf",
        "cols": [91, 146, 260, 295, 338, 502],
        # (first_page, last_page, section, default fc)
        "sections": [(7, 43, "5.1", 4), (44, 106, "5.2", 3), (107, 131, "6", None)],
    },
    "V3.2": {
        "file": "Solis_RS485_MODBUS_RTU_Hybrid_Inverter_Protocol_Ver3.2_ESINV-33000ID.pdf",
        "cols": [41, 82, 181, 217, 274, 554],
        "sections": [(4, 4, "5.1", 4), (5, 62, "5.2", 4), (63, 136, "5.3", 3)],
    },
}

ADDR_RE = re.compile(r"^\s*(\d{4,5})(?:\s*[~\-–]\s*(\d{4,5}))?\s*$")


def _col(x: float, cols: list[float]) -> int | None:
    for i in range(len(cols) - 1):
        if cols[i] - 3 <= x < cols[i + 1] - 1:
            return i
    return None


def _band_text(words, cols):
    out = [[] for _ in range(len(cols) - 1)]
    for w in words:
        c = _col((w["x0"] + min(w["x1"], w["x0"] + 8)) / 2, cols)
        if c is not None:
            out[c].append(w)
    res = []
    for ws in out:
        ws.sort(key=lambda w: (round(w["top"] / 3), w["x0"]))
        lines, cur, last = [], [], None
        for w in ws:
            t = round(w["top"] / 3)
            if last is not None and abs(w["top"] - last) > 3:
                lines.append(" ".join(cur)); cur = []
            cur.append(w["text"]); last = w["top"]
        if cur:
            lines.append(" ".join(cur))
        res.append("\n".join(lines))
    return res


def _split_band(bw, cols):
    """A band without inner rules may hold several registers (V3.5 p.113)."""
    addr = sorted((w for w in bw if _col(w["x0"] + 2, cols) == 0), key=lambda w: w["top"])
    starts, prev_tilde = [], False
    for w in addr:
        if re.fullmatch(r"\d{4,5}[~\-–]?", w["text"]):
            if not prev_tilde:
                starts.append(w["top"])
            prev_tilde = w["text"][-1] in "~-–"
        else:
            prev_tilde = False
    if len(starts) <= 1:
        return [bw]
    cuts = [-1e9] + [t - 1.5 for t in starts[1:]] + [1e9]
    return [[w for w in bw if lo <= w["top"] < hi] for lo, hi in zip(cuts, cuts[1:])]


def parse_addr(cell: str):
    lines = [l.strip() for l in cell.split("\n") if l.strip()]
    if not lines:
        return None
    first = lines[0]
    if re.fullmatch(r"\d{4,5}\s*[~\-–]", first) and len(lines) > 1 and re.fullmatch(r"\d{4,5}", lines[1]):
        first = first + lines[1]
    m = re.fullmatch(r"(\d{4,5})(?:\s*[~\-–]\s*(\d{4,5}))?", first)
    if not m:
        m2 = re.fullmatch(r"(\d{4,5})\s*[~\-–]", first)
        if m2:
            return int(m2.group(1)), "open"
        return None
    return int(m.group(1)), (int(m.group(2)) if m.group(2) else None)


def extract(ver: str) -> list[dict]:
    cfg = DOCS_CFG[ver]
    pdf = pdfplumber.open(DOCS / cfg["file"])
    cols = cfg["cols"]
    rows: list[dict] = []
    for first, last, sec, fc in cfg["sections"]:
        for pn in range(first, last + 1):
            page = pdf.pages[pn - 1]
            # horizontal rules crossing the address column
            ys = sorted({round(e["top"], 1) for e in page.edges
                         if e["orientation"] == "h" and e["x0"] <= cols[0] + 4 and e["x1"] >= cols[1] - 4})
            words = page.extract_words(keep_blank_chars=False, use_text_flow=False, x_tolerance=1.5)
            words = [w for w in words if cols[0] - 3 <= w["x0"] < cols[-1] + 2
                     and w["top"] < page.height * 0.925]
            if not ys:
                continue
            # text above the first rule continues the previous page's row
            bounds = [0.0] + ys + [page.height - 40]
            for a, b in zip(bounds, bounds[1:]):
                bw = [w for w in words if a <= w["top"] + 1 < b]
                if not bw:
                    continue
                for sub in _split_band(bw, cols):
                    cells = _band_text(sub, cols)
                    if not any(c.strip() for c in cells):
                        continue
                    rows.append({"page": pn, "section": sec, "fc": fc, "top": a, "cells": cells})
    return rows


def merge(rows: list[dict]) -> list[dict]:
    """Join continuation bands to the previous addressed row."""
    out: list[dict] = []
    for r in rows:
        a = r["cells"][0].strip()
        pa = parse_addr(a)
        if out and out[-1]["addr_end"] == "open":
            prev = out[-1]
            m = re.match(r"\s*(\d{4,5})", a)
            if m and (not pa or pa[1] is None) and 0 < int(m.group(1)) - prev["addr"] <= 3 \
                    and (not r["cells"][1].strip() or not r["cells"][2].strip()):
                prev["addr_end"] = int(m.group(1))
            elif m:
                prev["addr_end"] = None   # "33261-" alone: width comes from the type
            else:
                prev["addr_end"] = None
            if not (m and prev["addr_end"] is None):
                for i, c in enumerate(r["cells"][1:], 1):
                    if c.strip():
                        prev["cells"][i] = (prev["cells"][i] + "\n" + c).strip("\n")
                continue
        if pa:
            out.append({"page": r["page"], "section": r["section"], "fc": r["fc"],
                        "addr": pa[0], "addr_end": pa[1], "cells": list(r["cells"])})
        elif out and a == "" :
            prev = out[-1]
            for i, c in enumerate(r["cells"]):
                if c.strip():
                    prev["cells"][i] = (prev["cells"][i] + "\n" + c).strip("\n")
        else:
            out.append({"page": r["page"], "section": r["section"], "fc": r["fc"], "addr": None,
                        "addr_end": None, "cells": list(r["cells"])})
    return out


def main() -> None:
    outdir = ROOT / "data" / "raw"
    outdir.mkdir(parents=True, exist_ok=True)
    for ver in (sys.argv[1:] or DOCS_CFG):
        rows = merge(extract(ver))
        (outdir / f"rows_{ver}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
        n = sum(1 for r in rows if r["addr"])
        print(ver, "addressed rows:", n, "unaddressed:", len(rows) - n)


if __name__ == "__main__":
    main()
