"""Pre-flight identity read (FC04 33000-33019: model word, versions, serial number)."""
from __future__ import annotations

from typing import Dict, List, Sequence

IDENTITY_START = 33000
IDENTITY_COUNT = 20


def decode_sn(regs: Sequence[int]) -> str:
    """SN 33004-33019. V3.5 says "hex display"; real devices return ASCII bytes.

    Printable bytes are decoded as ASCII (proven on site with the v1 app);
    when none are printable the words are shown as hex.
    """
    chars: List[str] = []
    for w in regs:
        for b in ((int(w) >> 8) & 0xFF, int(w) & 0xFF):
            if 32 <= b <= 126:
                chars.append(chr(b))
    s = "".join(chars).strip()
    if s:
        return s
    return "".join(f"{int(w) & 0xFFFF:04X}" for w in regs).rstrip("0") or ""


def read_identity(client) -> Dict:
    regs = client.read_registers(IDENTITY_START, IDENTITY_COUNT)
    model = int(regs[0]) & 0xFFFF
    return {
        "model_no": model,
        "model_no_display": f"0x{model:04X}",
        "dsp": int(regs[1]),
        "hmi": int(regs[2]),
        "protocol": int(regs[3]),
        "serial_number": decode_sn(regs[4:20]),
    }
