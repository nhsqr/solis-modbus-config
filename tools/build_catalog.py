"""Build data/registers.json from the extracted rows + appendices.

    python tools/extract_tables.py      # PDF -> data/raw/rows_*.json
    python tools/extract_appendices.py  # PDF -> data/appendices.json
    python tools/build_catalog.py       # -> data/registers.json + docs/COMPLETENESS.md

V3.5 is primary; V3.2 adds registers V3.5 does not define (marked
``source = "V3.2"``). For registers in both, the V3.5 row wins and the V3.2
description is kept as ``description_v32``.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.groups import monitor_group, setting_group  # noqa: E402

TYPE_RE = re.compile(r"\b(U16|S16|U32|S32|U6|ASCI ?I|ASCII|ASCI|Str)\b", re.I)


def clean(s: str) -> str:
    s = (s or "").replace("\u2011", "-").replace("\u2010", "-").replace("\u00a0", " ")
    return s.strip()


def one_line(s: str) -> str:
    return re.sub(r"\s+", " ", clean(s)).strip()


def parse_type(cell: str, notes: list) -> str:
    m = TYPE_RE.search(cell.replace("\n", " "))
    if not m:
        notes.append("type missing in doc, assumed U16")
        return "U16"
    t = m.group(1).upper().replace(" ", "")
    if t == "U6":
        notes.append("type printed as 'U6' in doc, read as U16")
        return "U16"
    if t.startswith("ASCI") or t == "STR":
        return "ASCII"
    return t


UNIT_FIX = {"时": "h", "分": "min", "hour": "h", "Hour": "h", "Min": "min", "S": "s", "sec": "s", "second": "s",
            "DegC": "°C", "Degc": "°C", "℃": "°C", "w": "W", "Kw": "kW", "AH": "Ah", "K": "kΩ", "day": "d"}


def parse_unit(cell: str, notes: list):
    """'0.1V' -> (0.1, 'V'); '0.01%(Actual 2%)' -> (0.01, '%'); '' -> (1, '')."""
    c = one_line(cell)
    if not c:
        return 1.0, ""
    if c.startswith("Explanation") or len(c) > 30:
        notes.append(f"unit cell unreadable in doc: {c[:30]}…")
        return 1.0, ""
    c = c.replace("分 1", "1 分")
    m = re.match(r"^([0-9]*\.?[0-9]+)?\s*([^\s(（0-9][^(（]*?)?\s*(?:[(（](.*)[)）])?$", c)
    if not m:
        notes.append(f"unit '{c}' not parsed")
        return 1.0, c
    num = float(m.group(1)) if m.group(1) else 1.0
    unit = (m.group(2) or "").strip()
    unit = UNIT_FIX.get(unit, unit)
    if m.group(3):
        notes.append(f"doc unit note: ({m.group(3)})")
    # cells where two rows bled together: '10ms20ms', '0.01Hz0.01s'
    mm = re.match(r"^([A-Za-z%/]+?)(?:[0-9.]+[A-Za-z%]+)$", unit)
    if mm:
        notes.append(f"unit cell '{c}' holds two units; first used")
        unit = mm.group(1)
    return num, unit


def parse_options(text: str):
    """Enumerated values in the remarks: '0: Off\n1: On' / '0xBE–Startup'."""
    t = clean(text)
    if re.search(r"\bBIT\s?\d", t):
        return None
    opts = {}
    for m in re.finditer(
            r"(?:(?<=^)|(?<=[\s,;，；(（]))(0x[0-9A-Fa-f]{1,4}|\d{1,5})\s*(?:[:：]|[–—]|-{1,3}(?!\d)|->|<-->)\s*"
            r"([A-Za-z][^,;，；\n]*)", t, re.M):
        code_s, label = m.group(1), m.group(2).strip().rstrip(".")
        if re.match(r"^(Range|range|Default|default)", label):
            continue
        code = int(code_s, 16) if code_s.lower().startswith("0x") else int(code_s)
        if re.search(r"<-->", t[m.start():m.end()]):
            return None  # scaling note '100<-->1.0', not an enum
        opts.setdefault(code, label[:90])
    if len(opts) < 2:
        return None
    # numeric scaling notes like '1<-->1W' would not reach here; ranges like
    # '3–65535: Reserved' are dropped (label must start with a letter)
    return {str(k): v for k, v in sorted(opts.items())}


BIT_HEAD = re.compile(r"BIT\s?(\d{1,2})(?:\s*(?:[–\-~]|to)\s*(?:BIT)?\s?(\d{1,2}))?\s*[:：]")


def parse_bits(text: str):
    t = one_line(text)
    heads = list(BIT_HEAD.finditer(t))
    if len(heads) < 1:
        return None
    bits = []
    for i, h in enumerate(heads):
        seg = t[h.end(): heads[i + 1].start() if i + 1 < len(heads) else len(t)].strip()
        lo = int(h.group(1)); hi = int(h.group(2)) if h.group(2) else lo
        if hi < lo or hi > 31:
            continue
        name = re.split(r"\s+[–—-]\s+|\s*[–—]\s*(?=\d)|(?<=[a-z\)])\s*-\s*(?=\d\s*[:：])|;|\.\s", seg, 1)[0].strip(" :.,")
        states = {}
        for m in re.finditer(r"(?<![\w.])(\d{1,2})\s*[:：]\s*([A-Za-z][^,;，；.]*)", seg):
            states.setdefault(m.group(1), m.group(2).strip()[:60])
        dm = re.search(r"[Dd]efault\s*[:：]?\s*(\d+)", seg)
        reserved = bool(re.match(r"(Reserved|Not used|Reserve)\b", name, re.I))
        bits.append({"lo": lo, "hi": hi, "name": name[:120] or seg[:80], "states": states,
                     "default": int(dm.group(1)) if dm else None, "reserved": reserved, "text": seg[:400]})
    return bits or None


NUM = r"[-−]?\d+(?:\.\d+)?"


def parse_range(text: str, scale: float, unit: str):
    t = one_line(text).replace("−", "-")
    m = re.search(r"[Rr]ange\s*(?:is)?\s*[:：]?\s*[（(]?\s*(" + NUM + r")\s*([A-Za-z%°℃/]*)\s*[）)]?\s*(?:~|–|—|-{1,3}|to)\s*[（(]?\s*(" + NUM + r")\s*([A-Za-z%°℃/]*)", t)
    if not m:
        return None
    lo, u1, hi, u2 = float(m.group(1)), m.group(2), float(m.group(3)), m.group(4)
    rest = t[m.end():m.end() + 40]
    if re.match(r"\s*[,，;；]?\s*[+-]?\d+(?:\.\d+)?\s*(?:~|–|—|-{1,3}|to)\s*[+-]?\d", rest):
        return {"min": None, "max": None, "basis": "none", "text": t[m.start():m.end() + 40][:120]}
    if lo > hi:
        lo, hi = hi, lo
    has_unit = bool(u1 or u2)
    decimals = "." in m.group(1) + m.group(3)
    if scale == 1 or decimals:
        basis = "eng"
    elif has_unit:
        basis = "eng"
    elif "<-->" in t or "->" in t:
        basis = "raw"
    else:
        basis = "unknown"
    return {"min": lo, "max": hi, "basis": basis, "text": t[max(0, m.start()):m.end()][:120]}


def parse_default(text: str):
    t = one_line(text)
    m = re.search(r"[Dd]efault(?: value)?(?: is)?\s*[:：]?\s*(" + NUM + r")\s*([A-Za-z%°℃/]*)", t)
    if not m:
        return None
    u = m.group(2) if m.group(2) in ("V", "A", "W", "kW", "%", "Hz", "s", "ms", "min", "h", "mA", "℃", "°C", "Ah", "kWh") else ""
    return {"value": float(m.group(1)), "unit": u, "text": t[m.start():m.end()][:80]}


DANGER_RE = re.compile(r"restore|reset|clear|calibrat|restart|reboot|upgrade|factory|self[- ]?check|self[- ]?test|"
                       r"\btest\b|startup/shutdown|one[- ]?click|one[- ]?key|initial|IV curve|scan|format|"
                       r"open loop|synchroni[sz]ation request|forced? (start|stop)", re.I)
NOT_SAVED_RE = re.compile(r"not (be )?(retained|saved)|power off not saving|not retained after power", re.I)


def load_rows(ver):
    rows = json.loads((ROOT / "data" / "raw" / f"rows_{ver}.json").read_text())
    out = {}
    for r in rows:
        a = r["addr"]
        if not a:
            continue
        if a in out:  # duplicate (e.g. V3.5 ch.6 repeats ch.5): keep first, remember page
            out[a].setdefault("dup_pages", []).append(r["page"])
            continue
        out[a] = r
    return out


APPENDIX_LINKS = {
    # register -> list of (doc, appendix, how)
    35000: [("V3.5", "A1", "enum")],
    33092: [("V3.5", "A2", "enum"), ("V3.2", "A3", "enum")],
    43068: [("V3.5", "A2", "enum"), ("V3.2", "A3", "enum")],
    33095: [("V3.5", "A3", "enum"), ("V3.2", "A2", "enum")],
    36024: [("V3.2", "A2", "enum")],
    33070: [("V3.5", "A3", "subcode")],
    33121: [("V3.5", "A4", "bits"), ("V3.2", "A5", "bits")],
    36026: [("V3.2", "A5", "bits:33121")],
    33339: [("V3.5", "A4", "bits")],
    43009: [("V3.5", "A5", "enum"), ("V3.5", "A6", "enum"), ("V3.2", "A11", "enum")],
    43803: [("V3.5", "A6", "enum"), ("V3.2", "A11", "enum")],
    33158: [("V3.2", "A11", "enum")],
    33160: [("V3.2", "A11", "enum")],
    43110: [("V3.5", "A7", "bits"), ("V3.2", "A6", "bits")],
    33132: [("V3.2", "A6", "bits:43110")],
    33112: [("V3.2", "A1", "bits")],
    33115: [("V3.2", "A7", "bits")],
    33122: [("V3.2", "A8", "bits")],
    33145: [("V3.2", "A9", "bits")],
    33146: [("V3.2", "A9", "bits")],
    33250: [("V3.2", "A10", "bits")],
    43073: [("V3.2", "A12", "bits")],
    43078: [("V3.2", "A13", "enum")],
    43226: [("V3.2", "A14", "enum")],
    43249: [("V3.2", "A15", "bits")],
    43302: [("V3.2", "A16", "bits")],
    34260: [("V3.2", "A17", "bits")],
    34261: [("V3.2", "A17", "bits")],
}
for _a in (33116, 33117, 33118, 33119, 33120, 33124, 33125):
    APPENDIX_LINKS[_a] = [("V3.2", "A4", "bits")]
for _a in range(34394, 34403):
    APPENDIX_LINKS[_a] = [("V3.2", "A18", "bits")]


def appendix_options(app, links, addr):
    """Enum options (code -> label) merged from the linked appendices (V3.5 first)."""
    opts = {}
    for doc, aid, how in links:
        if how != "enum":
            continue
        a = app[doc][aid]
        for code, e in a["codes"].items():
            if aid == "A2" and doc == "V3.5":
                lab = e.get("three_phase", "")
                sp = e.get("single_phase", "")
                if sp and sp != lab:
                    lab = f"{lab}  |  1ph: {sp}"
            elif aid == "A3" and doc == "V3.2":
                lab = e.get("three_phase_5_136k", "")
                sp = e.get("single_phase", "")
                if sp and sp != lab:
                    lab = f"{lab}  |  1ph: {sp}"
            else:
                lab = e.get("label", "") or e.get("remarks", "")
            if re.fullmatch(r"Reserve\d*|Reserved|/|", lab.strip()) or (lab.startswith("Reserve") and "|" not in lab):
                continue
            if code not in opts and doc != links[0][0]:
                lab = f"{lab} [{doc}]"
            opts.setdefault(code, lab)
    return {k: opts[k] for k in sorted(opts, key=int)} or None


def appendix_bits(app, links, addr):
    for doc, aid, how in links:
        if not how.startswith("bits"):
            continue
        key = how.split(":")[1] if ":" in how else str(addr)
        bits = app[doc][aid]["bits"].get(key)
        if bits:
            out = []
            for b in bits:
                nm = b["name"]
                out.append({"lo": b["lo"], "hi": b["hi"], "name": nm, "states": b.get("states", {}),
                            "default": None, "reserved": bool(re.match(r"Reserve", nm, re.I)),
                            "text": b.get("states_text", "")})
            return out, f"{doc}:{aid}"
    return None, None


def build():
    r5, r2 = load_rows("V3.5"), load_rows("V3.2")
    app = json.loads((ROOT / "data" / "appendices.json").read_text())
    addrs = sorted(set(r5) | set(r2))
    regs = []
    for a in addrs:
        p = r5.get(a) or r2.get(a)
        src = "V3.5" if a in r5 else "V3.2"
        notes: list = []
        cells = [clean(c) for c in p["cells"]]
        name = one_line(cells[1])
        dtype = parse_type(cells[2], notes)
        scale, unit = parse_unit(cells[3], notes)
        remarks = cells[4]
        words = 2 if dtype in ("U32", "S32") else 1
        if dtype == "ASCII" or (p.get("addr_end") and p["addr_end"] not in (None, "open")):
            end = p.get("addr_end")
            if isinstance(end, int) and end > a:
                words = max(words, end - a + 1)
        if a in r5 and a in r2:
            sources = [{"doc": "V3.5", "page": r5[a]["page"]}, {"doc": "V3.2", "page": r2[a]["page"]}]
        else:
            sources = [{"doc": src, "page": p["page"]}]
        holding = 40000 <= a < 50000
        reg = {
            "address": a, "words": words, "fc": 3 if holding else 4, "writable": holding,
            "type": dtype, "scale": scale, "unit": unit, "name": name,
            "source": src, "sources": sources, "section": p.get("section"),
            "description": remarks,
        }
        if a in r5 and a in r2:
            d2 = clean(r2[a]["cells"][4])
            if one_line(d2) != one_line(remarks):
                reg["description_v32"] = d2
            n2 = one_line(r2[a]["cells"][1])
            if n2.lower() != name.lower():
                reg["name_v32"] = n2
        reg["group"] = setting_group(name, a) if holding else monitor_group(name, a)
        links = APPENDIX_LINKS.get(a, [])
        kind = "value"
        options = None
        bits = None
        appendix = None
        if links:
            appendix = ", ".join(f"{d}:{x}" for d, x, _ in links)
            options = appendix_options(app, links, a)
            if options:
                kind = "enum"
            else:
                bits, _ = appendix_bits(app, links, a)
                if bits:
                    kind = "bits"
        if kind == "value":
            bits = parse_bits(remarks)
            if bits and len([b for b in bits if not b["reserved"]]) >= 1:
                kind = "bits"
            else:
                bits = None
                options = parse_options(remarks)
                if options:
                    kind = "enum"
        if kind == "value":
            if dtype == "ASCII":
                kind = "ascii"
            elif re.search(r"[Hh]igh[- ]?(order )?byte", remarks) and re.search(r"[Ll]ow[- ]?(order )?byte", remarks):
                kind = "bytes"
            elif re.search(r"[Hh]exadecimal|\(Hex\)|Hex Display", remarks + " " + cells[2]):
                kind = "hex"
        if kind == "enum" and options:
            ints = [int(k) for k in options]
            hi = [k for k in ints if k >= 256 and k % 256 == 0]
            lo = [k for k in ints if k < 256]
            if hi and lo and len(hi) + len(lo) == len(ints):
                kind = "bytes"
                reg["lo_options"] = {str(k): options[str(k)] for k in lo}
                reg["hi_options"] = {str(k // 256): options[str(k)] for k in hi}
                if "0" in options:
                    reg["hi_options"].setdefault("0", options["0"])
                options = None
        reg["kind"] = kind
        if options:
            reg["options"] = options
        if bits:
            reg["bits"] = bits
        if appendix:
            reg["appendix"] = appendix
        rng = parse_range(remarks, scale, unit) if kind in ("value", "hex") else None
        if rng:
            reg["range"] = rng
        dfl = parse_default(remarks)
        if dfl:
            reg["default"] = dfl
        if holding and DANGER_RE.search(name):
            reg["danger"] = True
        if NOT_SAVED_RE.search(one_line(remarks)):
            reg["volatile"] = True
        if p.get("dup_pages"):
            notes.append(f"row repeated in doc on page(s) {p['dup_pages']}")
        if notes:
            reg["notes"] = notes
        regs.append(reg)
    return regs, r5, r2, app


def completeness(regs, r5, r2, app):
    lines = ["# Register catalog completeness", "",
             "Generated by `tools/build_catalog.py`. Counts come from the PDF tables "
             "(`tools/extract_tables.py`) and are cross-checked against every 5-digit "
             "address printed in the address column of the register chapters.", ""]
    def sec_counts(rows, ver):
        c = Counter()
        for a, r in rows.items():
            c[(r["section"], "holding" if 40000 <= a < 50000 else "input")] += 1
        return c
    lines.append("## Rows per document chapter")
    lines.append("")
    lines.append("| Doc | Chapter | Kind | Rows |")
    lines.append("|---|---|---|---|")
    for ver, rows in (("V3.5", r5), ("V3.2", r2)):
        for (sec, k), n in sorted(sec_counts(rows, ver).items()):
            lines.append(f"| {ver} | {sec} | {k} | {n} |")
    lines.append("")
    both = set(r5) & set(r2)
    lines += ["## Overlap", "",
              f"- V3.5 rows: {len(r5)} (holding {sum(1 for a in r5 if a >= 40000)}, input {sum(1 for a in r5 if a < 40000)})",
              f"- V3.2 rows: {len(r2)} (holding {sum(1 for a in r2 if a >= 40000)}, input {sum(1 for a in r2 if a < 40000)})",
              f"- in both: {len(both)}; V3.5 only: {len(set(r5) - set(r2))}; V3.2 only (supplement): {len(set(r2) - set(r5))}",
              f"- catalog entries: {len(regs)} (writable {sum(r['writable'] for r in regs)}, read-only {sum(not r['writable'] for r in regs)})", ""]
    lines += ["## Catalog per group", "", "| Group | Writable (V3.5 / V3.2-only) | Read-only (V3.5 / V3.2-only) |", "|---|---|---|"]
    g = defaultdict(lambda: [0, 0, 0, 0])
    for r in regs:
        i = (0 if r["writable"] else 2) + (0 if r["source"] == "V3.5" else 1)
        g[(r["writable"], r["group"])][i] += 1
    for (w, gid), c in sorted(g.items(), key=lambda x: (not x[0][0], x[0][1])):
        if w:
            lines.append(f"| {gid} (settings) | {c[0]} / {c[1]} | |")
        else:
            lines.append(f"| {gid} (monitor) | | {c[2]} / {c[3]} |")
    lines.append("")
    kinds = Counter((r["writable"], r["kind"]) for r in regs)
    lines += ["## Editors (kind)", ""] + [f"- {'setting' if w else 'monitor'} {k}: {n}" for (w, k), n in sorted(kinds.items())] + [""]
    lines += ["## Appendices", "", "| Doc | Appendix | Kind | Entries | Title |", "|---|---|---|---|---|"]
    for ver, apps in app.items():
        for k, a in apps.items():
            n = len(a.get("codes", {})) if a["kind"] == "enum" else sum(len(b) for b in a["bits"].values())
            lines.append(f"| {ver} | {k} | {a['kind']} | {n} | {a['title']} |")
    lines.append("")
    notes = [r for r in regs if r.get("notes")]
    lines += ["## Parsing notes (doc quirks)", ""] + [f"- {r['address']} {r['name']}: {'; '.join(r['notes'])}" for r in notes] + [""]
    return "\n".join(lines)


def main():
    regs, r5, r2, app = build()
    out = {"generated_from": {"V3.5": "Solis_Energy_Storage_Modbus_Protocol_V3.5.pdf",
                              "V3.2": "Solis_RS485_MODBUS_RTU_Hybrid_Inverter_Protocol_Ver3.2_ESINV-33000ID.pdf"},
           "registers": regs}
    (ROOT / "data" / "registers.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    (ROOT / "docs").mkdir(exist_ok=True)
    (ROOT / "docs" / "COMPLETENESS.md").write_text(completeness(regs, r5, r2, app))
    c = Counter((r["writable"], r["group"]) for r in regs)
    for k, n in sorted(c.items()):
        print(("W " if k[0] else "R ") + k[1], n)
    print("total", len(regs))


if __name__ == "__main__":
    main()
