"""Extract the appendix tables (enums, bit definitions) of both Solis PDFs.

Usage: python tools/extract_appendices.py  -> data/appendices.json

Each appendix becomes {"kind": "enum"|"bits", "registers": [...], ...}.
Enums: {"codes": {"<int>": {"label": str, ...}}}; bits: {"bits": {"<reg>":
[{"lo": n, "hi": n, "name": str, "states": str}]}}.
The table → appendix mapping below is explicit (page / table index), because
the PDFs number and lay out the appendices differently (V3.5 A2 = grid
standards, V3.2 A3 = grid standards, ...).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).resolve().parents[1]
DOCS = Path("/workspace/solis-docs")
FILES = {
    "V3.5": "Solis_Energy_Storage_Modbus_Protocol_V3.5.pdf",
    "V3.2": "Solis_RS485_MODBUS_RTU_Hybrid_Inverter_Protocol_Ver3.2_ESINV-33000ID.pdf",
}
FIRST_APPENDIX_PAGE = {"V3.5": 133, "V3.2": 139}


def clean(s) -> str:
    s = (s or "").replace("\u2011", "-").replace("\u2010", "-")
    return re.sub(r"\s+", " ", s).strip()


def load_tables(ver: str):
    pdf = pdfplumber.open(DOCS / FILES[ver])
    out = {}
    for pn in range(FIRST_APPENDIX_PAGE[ver], len(pdf.pages) + 1):
        tabs = pdf.pages[pn - 1].extract_tables()
        out[pn] = [[[clean(c) for c in r if c is not None] for r in t] for t in tabs]
    return out


HEX_H = re.compile(r"^([0-9A-F]{2,4})H$")
HEX_0X = re.compile(r"^0x([0-9A-Fa-f]{4})[：:]?$")
BIT = re.compile(r"^BIT\s?(\d{1,2}):?(?:\s*[-~–]\s*(?:BIT)?\s?(\d{1,2}))?$")


def rows_of(tabs, sel):
    """sel: list of (page, table_index | None=all)."""
    for pn, ti in sel:
        ts = tabs.get(pn, [])
        for i, t in enumerate(ts):
            if ti is None or i == ti or (isinstance(ti, tuple) and i in ti):
                for r in t:
                    yield pn, r


# ----------------------------------------------------------------- enums
def enum_table(tabs, sel, code_re, ncols, names, *, code_col=0, multi_split="/"):
    codes: dict[int, dict] = {}
    last = None
    for pn, r in rows_of(tabs, sel):
        if not r or all(not c for c in r):
            continue
        c0 = r[code_col] if len(r) > code_col else ""
        parts = [p.strip() for p in c0.split(multi_split)] if multi_split else [c0]
        mm = [code_re.match(p) for p in parts]
        vals = r[code_col + 1:]
        if all(mm) and parts and parts[0]:
            last = []
            for m in mm:
                code = int(m.group(1), 16)
                entry = {"page": pn}
                for k, v in zip(names, vals[:ncols]):
                    entry[k] = v
                codes[code] = entry
                last.append(code)
        elif last and len(r) and not code_re.match(c0 or "x"):
            # continuation row: append text to the matching columns
            vv = r if (not c0) else r  # cells align from col 0 when code col empty
            if c0:
                vv = [""] + r[:]  # code column missing: shift right
            vals2 = vv[code_col + 1:]
            for code in last:
                for k, v in zip(names, vals2[:ncols]):
                    if v and v not in ("\\", "/"):
                        codes[code][k] = clean((codes[code].get(k, "") + " " + v))
    return codes


# ------------------------------------------------------------------ bits
def parse_states(text: str) -> dict:
    """'0—No 1—Yes' / '0 Enable，1 Disable，Default 0' -> {0: 'No', 1: 'Yes'}"""
    t = clean(text)
    st = {}
    for m in re.finditer(r"(?<![\w.])([0-7])\s*(?:—|–|-{1,3}|:|：|\s)\s*([A-Za-z][\w /()\-_'.]{0,40}?)(?=\s*(?:[,，;；]|\s[0-7]\s*(?:—|–|-|:|：|\s)|Default|default|$))", t):
        st.setdefault(int(m.group(1)), m.group(2).strip())
    return st


def bit_table(tabs, sel, *, name_col=1, state_col=2):
    bits = []
    for pn, r in rows_of(tabs, sel):
        if not r:
            continue
        m = BIT.match(r[0])
        if not m:
            if bits and r[0] == "" and len(r) > name_col and r[name_col]:
                bits[-1]["name"] = clean(bits[-1]["name"] + " " + r[name_col])
            continue
        lo = int(m.group(1)); hi = int(m.group(2)) if m.group(2) else lo
        name = r[name_col] if len(r) > name_col else ""
        states = " ".join(r[state_col:]) if len(r) > state_col else ""
        bits.append({"lo": lo, "hi": hi, "name": clean(name), "states_text": clean(states),
                     "states": {str(k): v for k, v in parse_states(states).items()}, "page": pn})
    return bits


def split_pairs(tabs, sel, width):
    """Side-by-side bit tables: header row ['', '33116', ..., '33117', ...]."""
    regs_bits: dict[int, list] = {}
    cur = None
    for pn, r in rows_of(tabs, sel):
        nums = [int(c) for c in r if re.fullmatch(r"3\d{4}", c or "")]
        if nums:
            cur = nums
            for n in nums:
                regs_bits.setdefault(n, [])
            continue
        if cur is None:
            continue
        halves = [r[i:i + width] for i in range(0, len(r), width)]
        for reg, h in zip(cur, halves):
            if h and BIT.match(h[0] or ""):
                m = BIT.match(h[0]); lo = int(m.group(1)); hi = int(m.group(2)) if m.group(2) else lo
                regs_bits[reg].append({"lo": lo, "hi": hi, "name": " / ".join(x for x in h[1:width - (width > 2 and width != 3) ] if x) if width > 3 else h[1],
                                       "states_text": h[-1] if width == 3 else "",
                                       "states": {str(k): v for k, v in parse_states(h[-1] if width == 3 else "").items()},
                                       "page": pn})
    return regs_bits


def by_reg_header(tabs, sel, header_re):
    """Consecutive bit tables each headed by e.g. '34394-BIT' or 'SPH Fault Status 01'."""
    out: dict[str, list] = {}
    cur = None
    for pn, r in rows_of(tabs, sel):
        if r and re.match(header_re, r[0] or ""):
            cur = r[0]
            out[cur] = []
            continue
        if cur is None or not r:
            continue
        m = BIT.match(r[0] or "")
        if m:
            lo = int(m.group(1)); hi = int(m.group(2)) if m.group(2) else lo
            out[cur].append({"lo": lo, "hi": hi, "name": r[1] if len(r) > 1 else "",
                             "states_text": r[2] if len(r) > 2 else "",
                             "states": {str(k): v for k, v in parse_states(r[2] if len(r) > 2 else "").items()},
                             "page": pn})
    return out


def a4_v35(tabs):
    """V3.5 Appendix 4: 33121 then 33339 (BIT00 restarts the list)."""
    regs = {33121: [], 33339: []}
    cur = 33121
    for pn, r in rows_of(tabs, [(145, 0), (146, 0)]):
        idx = next((i for i, c in enumerate(r) if BIT.match(c or "")), None)
        if idx is None:
            continue
        m = BIT.match(r[idx]); lo = int(m.group(1))
        if lo == 0 and regs[cur]:
            cur = 33339
        if regs[cur] and lo <= regs[cur][-1]["lo"]:
            continue  # stray duplicate row (see AMBIGUITIES)
        name = r[idx + 1] if len(r) > idx + 1 else ""
        st = " ".join(r[idx + 2:])
        regs[cur].append({"lo": lo, "hi": lo, "name": name, "states_text": st,
                          "states": {str(k): v for k, v in parse_states(st).items()}, "page": pn})
    return regs


def fix_bits_text(bits):
    for b in bits:
        b["name"] = clean(re.sub(r"^BIT\d+\s*:\s*", "", b["name"]))
    return bits


def build():
    res = {}
    # ------------------------------------------------------------ V3.5
    t = load_tables("V3.5")
    v = {}
    v["A1"] = {"title": "Inverter model (M35000)", "kind": "enum", "registers": [35000],
               "codes": enum_table(t, [(133, 0)], HEX_0X, 2, ["label", "remarks"])}
    v["A2"] = {"title": "National (grid) standard (M33092 / 43068)", "kind": "enum",
               "registers": [33092, 43068],
               "codes": enum_table(t, [(p, None) for p in range(134, 140)], HEX_H, 2,
                                   ["three_phase", "single_phase"], multi_split=None)}
    a3 = {}
    last = None
    for pn, r in rows_of(t, [(p, None) for p in range(140, 145)]):
        if not r:
            continue
        m = re.match(r"^([0-9A-F]{4})H\s*[（(]\s*([0-9A-Fx]{4})\s*[）)]$", r[0]) or re.match(r"^([0-9A-F]{4})H$", r[0])
        if not m:
            continue
        code = int(m.group(1), 16)
        sub = m.group(2) if m.lastindex and m.lastindex >= 2 else None
        e = a3.setdefault(code, {"page": pn, "label": "", "cn": "", "sub": {}})
        cn = r[1] if len(r) > 1 else ""
        en = r[2] if len(r) > 2 else ""
        if sub is None or sub in ("0000", "xx00"):
            e["label"] = e["label"] or en
            e["cn"] = e["cn"] or cn
        if sub is not None:
            e["sub"][sub] = {"label": en, "cn": cn}
    for code, e in a3.items():
        if not e["label"] and e["sub"]:
            first = next(iter(e["sub"].values()))
            e["label"] = first["label"]; e["cn"] = first["cn"]
    v["A3"] = {"title": "Inverter status / alarm code (M33095, sub-code 33070)", "kind": "enum",
               "registers": [33095, 33070], "codes": a3,
               "note": "Codes with '(xx01)' style sub-codes: 33095 gives the main code, 33070 the sub-code (bit mask for 0003H limits)."}
    v["A4"] = {"title": "Status bit definitions (33121, 33339)", "kind": "bits",
               "registers": [33121, 33339], "bits": {str(k): b for k, b in a4_v35(t).items()}}
    lv = enum_table(t, [(147, 0), (148, 0)], HEX_0X, 2, ["label", "remarks"], code_col=1)
    # row '63~82 0x003E~0x0052' lists two real codes inside the name cell
    for pn, r in rows_of(t, [(148, 0)]):
        if len(r) > 2 and "0x003E" in r[1]:
            for m in re.finditer(r"0x([0-9A-F]{4})\s*->\s*([A-Za-z_][\w\- ]*?)(?=\s*0x|\s*Reserve|$)", r[2]):
                lv[int(m.group(1), 16)] = {"label": m.group(2).strip(), "remarks": "", "page": pn}
    for pn, r in rows_of(t, [(148, 0)]):
        pass
    v["A5"] = {"title": "Low-voltage battery codes (43009 low byte)", "kind": "enum",
               "registers": [43009], "codes": lv}
    v["A6"] = {"title": "High-voltage battery codes (43009 high byte, 43803)", "kind": "enum",
               "registers": [43009, 43803],
               "codes": enum_table(t, [(148, 1), (149, None)], HEX_0X, 2, ["label", "remarks"], code_col=1)}
    a7 = []
    for pn, r in rows_of(t, [(150, 0)]):
        idx = next((i for i, c in enumerate(r) if BIT.match(c or "")), None)
        if idx is None:
            continue
        lo = int(BIT.match(r[idx]).group(1))
        st = " ".join(r[idx + 2:])
        a7.append({"lo": lo, "hi": lo, "name": r[idx + 1], "states_text": st,
                   "states": {str(k): vv for k, vv in parse_states(st).items()}, "page": pn})
    v["A7"] = {"title": "Energy storage control switch (43110)", "kind": "bits",
               "registers": [43110], "bits": {"43110": a7}}
    res["V3.5"] = v

    # ------------------------------------------------------------ V3.2
    t = load_tables("V3.2")
    w = {}
    w["A1"] = {"title": "Inverter initial setting state (33112)", "kind": "bits", "registers": [33112],
               "bits": {"33112": bit_table(t, [(139, 0)], name_col=1, state_col=9)}}
    a2 = {}
    for pn, r in rows_of(t, [(139, 1), (140, None), (141, None), (142, 0)]):
        if not r or not HEX_H.match(r[0] or ""):
            continue
        code = int(HEX_H.match(r[0]).group(1), 16)
        cells = [c for c in r[1:]]
        if len(cells) >= 4:
            lab = cells[3] if cells[3] not in ("\\", "") else cells[2]
            a2[code] = {"label": lab if lab != "\\" else cells[1], "status_1p2g": cells[0],
                        "status_other": cells[1], "display_1p2g": cells[2], "display_other": cells[3], "page": pn}
        elif len(cells) >= 2:
            a2[code] = {"label": cells[1] or cells[0], "status": cells[0], "page": pn}
    w["A2"] = {"title": "Inverter current status (33095)", "kind": "enum", "registers": [33095, 36024], "codes": a2}
    w["A3"] = {"title": "Grid standard (33092 / 43068)", "kind": "enum", "registers": [33092, 43068],
               "codes": enum_table(t, [(142, 1)] + [(p, None) for p in range(143, 152)], HEX_H, 4,
                                   ["three_phase_5_136k", "single_phase", "three_phase_125k", "three_phase_225k"],
                                   multi_split=None)}
    a4 = split_pairs(t, [(152, None), (153, None), (154, 0)], 3)
    w["A4"] = {"title": "Fault code bits (33116-33120, 33124, 33125)", "kind": "bits",
               "registers": sorted(a4), "bits": {str(k): b for k, b in a4.items()}}
    w["A5"] = {"title": "Operating status bits (33121, 36026)", "kind": "bits", "registers": [33121, 36026],
               "bits": {"33121": bit_table(t, [(154, 1)])}}
    w["A6"] = {"title": "Storage control switching (33132 / 43110)", "kind": "bits", "registers": [33132, 43110],
               "bits": {"43110": bit_table(t, [(155, 0)])}}
    w["A7"] = {"title": "Setting flag bits (33115)", "kind": "bits", "registers": [33115],
               "bits": {"33115": bit_table(t, [(155, 1), (156, 0)])}}
    w["A8"] = {"title": "Operating mode bits (33122)", "kind": "bits", "registers": [33122],
               "bits": {"33122": fix_bits_text(bit_table(t, [(156, 1)]))}}
    a9 = {}
    cur = None
    for pn, r in rows_of(t, [(156, 2), (157, None)]):
        if r and any(c == "33145" for c in r):
            cur = [33145, 33146]; a9 = {33145: [], 33146: []}; continue
        if cur and len(r) >= 6 and BIT.match(r[0] or ""):
            for reg, h in zip(cur, (r[0:3], r[3:6])):
                lo = int(BIT.match(h[0]).group(1))
                a9[reg].append({"lo": lo, "hi": lo, "name": f"LV: {h[1]} / HV: {h[2]}", "lv": h[1], "hv": h[2],
                                "states_text": "", "states": {}, "page": pn})
        elif cur and BIT.match((r or [""])[0] or "") is None and r and r[0] == "LV hybrid & AC coupled：":
            break
    w["A9"] = {"title": "Battery (BMS) fault status bits (33145, 33146)", "kind": "bits",
               "registers": [33145, 33146], "bits": {str(k): b for k, b in a9.items()}}
    w["A10"] = {"title": "Meter/CT position bits (33250)", "kind": "bits", "registers": [33250],
                "bits": {"33250": bit_table(t, [(157, 0)])}}
    lvh = {"lv": {}, "hv": {}}
    for pn, r in rows_of(t, [(157, 1), (158, 0)]):
        if len(r) >= 4 and HEX_0X.match(r[0] or ""):
            lvh["lv"][int(HEX_0X.match(r[0]).group(1), 16)] = {"label": r[1], "page": pn}
            if HEX_0X.match(r[2] or ""):
                lvh["hv"][int(HEX_0X.match(r[2]).group(1), 16)] = {"label": r[3], "page": pn}
    codes = dict(lvh["lv"]); codes.update({k: v for k, v in lvh["hv"].items() if k})
    w["A11"] = {"title": "Battery model codes (43009, 33158, 33160, 43803)", "kind": "enum",
                "registers": [43009, 33158, 33160, 43803], "codes": codes}
    w["A12"] = {"title": "Meter/CT placement bits (43073)", "kind": "bits", "registers": [43073],
                "bits": {"43073": bit_table(t, [(158, 1), (159, 0)])}}
    a13 = {}
    for pn, r in rows_of(t, [(159, 1), (160, 0)]):
        if len(r) >= 2 and re.fullmatch(r"\d{2}", r[0] or ""):
            a13[int(r[0])] = {"label": r[1], "page": pn}
    w["A13"] = {"title": "Italy single protection test (43078)", "kind": "enum", "registers": [43078],
                "codes": a13}
    w["A14"] = {"title": "Frequency derating mode (43226)", "kind": "enum", "registers": [43226],
                "codes": enum_table(t, [(160, 1), (161, 0)], HEX_H, 1, ["label"], multi_split=None)}
    w["A15"] = {"title": "Special settings bits (43249)", "kind": "bits", "registers": [43249],
                "bits": {"43249": bit_table(t, [(161, 1)])}}
    w["A16"] = {"title": "Special function control word bits (43302)", "kind": "bits", "registers": [43302],
                "bits": {"43302": bit_table(t, [(161, 2), (162, 0)])}}
    sph = by_reg_header(t, [(162, 1), (162, 2), (163, 0)], r"^SPH")
    w["A17"] = {"title": "SPH fault bits (34260, 34261)", "kind": "bits", "registers": [34260, 34261],
                "bits": {"34260": sph.get("SPH Fault Status 01", []), "34261": sph.get("SPH fault code 02", [])}}
    a18 = by_reg_header(t, [(p, None) for p in range(163, 168)], r"^34\d{3}-BIT")
    w["A18"] = {"title": "Inverter fault code bits (34394-34402)", "kind": "bits",
                "registers": sorted(int(k[:5]) for k in a18),
                "bits": {k[:5]: b for k, b in a18.items()}}
    res["V3.2"] = w

    # stringify int keys
    for ver in res.values():
        for a in ver.values():
            if "codes" in a:
                a["codes"] = {str(k): a["codes"][k] for k in sorted(a["codes"])}
    return res


def main():
    res = build()
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / "appendices.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
    for ver, apps in res.items():
        for k, a in apps.items():
            n = len(a.get("codes", {})) if a["kind"] == "enum" else {r: len(b) for r, b in a["bits"].items()}
            print(ver, k, a["kind"], n, "-", a["title"])


if __name__ == "__main__":
    main()
