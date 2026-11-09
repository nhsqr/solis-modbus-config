"""Device list for batch apply (GUI-agnostic, no Modbus).

Text format, one line per IP (or several lines per IP):

    # comment
    192.168.85.106 1-6          # IDs 1..6 on port 502
    192.168.85.107:5020 1,3,5   # explicit port
    192.168.85.108 2 4 7-9      # mixed: spaces, commas, ranges

CSV files are also accepted (``host,port,id`` with optional header; the id
column may itself contain ``1-6`` or ``1,3``).
"""

from __future__ import annotations

import csv
import io
import ipaddress
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple, Union

DEFAULT_PORT = 502
MIN_UNIT_ID = 1
MAX_UNIT_ID = 247

_HOSTNAME_RE = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,62})(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}))*$")


@dataclass(frozen=True)
class DeviceTarget:
    host: str
    port: int
    unit_id: int
    line: int = 0

    @property
    def endpoint(self) -> Tuple[str, int]:
        return (self.host, self.port)

    def label(self) -> str:
        return f"{self.host}:{self.port} ID {self.unit_id}"


@dataclass(frozen=True)
class ParseIssue:
    line: int
    text: str
    message: str

    def __str__(self) -> str:
        return f"line {self.line}: {self.message} — {self.text.strip()!r}"


class DeviceListError(ValueError):
    pass


def _valid_host(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    # Reject things that look like a broken IPv4 (e.g. 192.168.1.300)
    if re.fullmatch(r"[0-9.]+", host):
        return False
    return bool(_HOSTNAME_RE.match(host))


def _split_host_port(token: str, default_port: int) -> Tuple[str, int]:
    if token.count(":") == 1:
        host, port_s = token.split(":", 1)
        if not port_s.isdigit():
            raise DeviceListError(f"invalid port '{port_s}'")
        port = int(port_s)
    else:
        host, port = token, default_port
    if not (1 <= port <= 65535):
        raise DeviceListError(f"port {port} out of range 1–65535")
    if not host or not _valid_host(host):
        raise DeviceListError(f"invalid IP/host '{host}'")
    return host, port


def parse_id_spec(spec: str) -> List[int]:
    """'1-6, 8 10' → [1..6, 8, 10] (order kept, duplicates removed)."""
    out: List[int] = []
    tokens = [t for t in re.split(r"[\s,;]+", spec.strip()) if t]
    if not tokens:
        raise DeviceListError("no unit IDs")
    for tok in tokens:
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", tok)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a > b:
                raise DeviceListError(f"range '{tok}' is reversed")
            ids = range(a, b + 1)
        elif tok.isdigit():
            ids = [int(tok)]
        else:
            raise DeviceListError(f"invalid unit ID '{tok}'")
        for i in ids:
            if not (MIN_UNIT_ID <= i <= MAX_UNIT_ID):
                raise DeviceListError(f"unit ID {i} out of range {MIN_UNIT_ID}–{MAX_UNIT_ID}")
            if i not in out:
                out.append(i)
    return out


def _normalise_range_spaces(s: str) -> str:
    # "1 - 6" → "1-6" so whitespace splitting keeps ranges intact
    return re.sub(r"(\d)\s*-\s*(\d)", r"\1-\2", s)


def parse_device_list(
    text: str, default_port: int = DEFAULT_PORT
) -> Tuple[List[DeviceTarget], List[ParseIssue]]:
    """Parse the device list text. Returns (devices, issues).

    Duplicate host:port/ID pairs are reported as issues and kept only once.
    """
    devices: List[DeviceTarget] = []
    issues: List[ParseIssue] = []
    seen: Dict[Tuple[str, int, int], int] = {}
    for lineno, raw in enumerate((text or "").splitlines(), start=1):
        body = raw.split("#", 1)[0].strip()
        if not body:
            continue
        body = _normalise_range_spaces(body)
        parts = body.split(None, 1)
        if len(parts) < 2:
            issues.append(ParseIssue(lineno, raw, "expected 'IP[:port] ID[,ID…]' (missing unit IDs)"))
            continue
        try:
            host, port = _split_host_port(parts[0].rstrip(","), default_port)
            ids = parse_id_spec(parts[1])
        except DeviceListError as exc:
            issues.append(ParseIssue(lineno, raw, str(exc)))
            continue
        for uid in ids:
            key = (host, port, uid)
            if key in seen:
                issues.append(
                    ParseIssue(lineno, raw, f"duplicate {host}:{port} ID {uid} (first on line {seen[key]})")
                )
                continue
            seen[key] = lineno
            devices.append(DeviceTarget(host, port, uid, lineno))
    return devices, issues


def group_by_endpoint(
    devices: Iterable[DeviceTarget],
) -> List[Tuple[Tuple[str, int], List[DeviceTarget]]]:
    """Group by (host, port), keeping first-appearance order of endpoints and IDs."""
    groups: Dict[Tuple[str, int], List[DeviceTarget]] = {}
    for d in devices:
        groups.setdefault(d.endpoint, []).append(d)
    return list(groups.items())


def _compress_ids(ids: Sequence[int]) -> str:
    """[1,2,3,5,7,8] → '1-3,5,7-8'."""
    out: List[str] = []
    i = 0
    ids = list(ids)
    while i < len(ids):
        j = i
        while j + 1 < len(ids) and ids[j + 1] == ids[j] + 1:
            j += 1
        out.append(str(ids[i]) if i == j else f"{ids[i]}-{ids[j]}")
        i = j + 1
    return ",".join(out)


def format_device_list(devices: Iterable[DeviceTarget], default_port: int = DEFAULT_PORT) -> str:
    lines = []
    for (host, port), devs in group_by_endpoint(devices):
        ep = host if port == default_port else f"{host}:{port}"
        lines.append(f"{ep} {_compress_ids([d.unit_id for d in devs])}")
    return "\n".join(lines) + ("\n" if lines else "")


# ------------------------------------------------------------------ files
def csv_to_text(content: str, default_port: int = DEFAULT_PORT) -> str:
    """Convert CSV (host,port,id | host,id | host:port,id) to list text."""
    sample = content[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = [r for r in csv.reader(io.StringIO(content), dialect) if any(c.strip() for c in r)]
    if not rows:
        return ""
    header = [c.strip().lower() for c in rows[0]]
    col_host = col_port = col_id = None
    if any(h in ("ip", "host", "address") for h in header):
        for i, h in enumerate(header):
            if h in ("ip", "host", "address") and col_host is None:
                col_host = i
            elif h == "port":
                col_port = i
            elif h in ("id", "unit", "unit_id", "slave", "slave_id", "ids"):
                col_id = i
        rows = rows[1:]
    lines: List[str] = []
    for r in rows:
        r = [c.strip() for c in r]
        if r and r[0].startswith("#"):
            lines.append(",".join(r))
            continue
        if col_host is not None:
            host = r[col_host] if col_host < len(r) else ""
            port = r[col_port] if col_port is not None and col_port < len(r) else ""
            ids = r[col_id] if col_id is not None and col_id < len(r) else ""
        elif len(r) >= 3:
            host, port, ids = r[0], r[1], ",".join(r[2:])
        elif len(r) == 2:
            host, port, ids = r[0], "", r[1]
        else:
            host, port, ids = r[0], "", ""
        ep = f"{host}:{port}" if port and ":" not in host else host
        lines.append(f"{ep} {ids}".rstrip())
    return "\n".join(lines) + "\n"


def load_device_file(path: Union[str, Path]) -> str:
    p = Path(path)
    content = p.read_text(encoding="utf-8-sig")
    if p.suffix.lower() == ".csv":
        return csv_to_text(content)
    return content


def save_device_file(path: Union[str, Path], text: str) -> None:
    """.csv → one row per device (host,port,unit_id); otherwise the raw text."""
    p = Path(path)
    if p.suffix.lower() == ".csv":
        devices, issues = parse_device_list(text)
        if issues:
            raise DeviceListError("fix the device list first:\n" + "\n".join(map(str, issues)))
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["host", "port", "unit_id"])
        for d in devices:
            w.writerow([d.host, d.port, d.unit_id])
        p.write_text(buf.getvalue(), encoding="utf-8")
    else:
        p.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
