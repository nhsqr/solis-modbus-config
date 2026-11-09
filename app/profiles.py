"""Settings profiles: JSON {"name", "items": [{"address", "words", "text", "name"}]}.

Saved from the Settings page ("Add to profile") and applied by the batch dialog.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Union

from app.catalog import Catalog
from app.writer import WriteItem

FORMAT = "solis-modbus-config/profile-1"


@dataclass
class Profile:
    name: str = "profile"
    items: List[WriteItem] = field(default_factory=list)

    def set(self, item: WriteItem) -> None:
        self.items = [i for i in self.items if i.address != item.address] + [item]

    def remove(self, address: int) -> None:
        self.items = [i for i in self.items if i.address != int(address)]

    def to_json(self) -> dict:
        return {"format": FORMAT, "name": self.name,
                "items": [{"address": i.address, "name": i.name, "words": list(i.words), "text": i.text}
                          for i in self.items]}

    def save(self, path: Union[str, Path]) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")
        return p


class ProfileError(ValueError):
    pass


def load_profile(path: Union[str, Path], catalog: Optional[Catalog] = None) -> Profile:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProfileError(f"cannot read profile: {exc}") from exc
    if not isinstance(d, dict) or not isinstance(d.get("items"), list):
        raise ProfileError("not a profile file (missing 'items')")
    items: List[WriteItem] = []
    for k, it in enumerate(d["items"]):
        try:
            addr = int(it["address"])
            words = tuple(int(w) & 0xFFFF for w in it["words"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProfileError(f"item {k + 1}: {exc}") from exc
        if catalog is not None:
            reg = catalog.get(addr)
            if reg is None or not reg.writable:
                raise ProfileError(f"item {k + 1}: {addr} is not a writable register in the catalog")
            if reg.words != len(words):
                raise ProfileError(f"item {k + 1}: {addr} needs {reg.words} word(s), profile has {len(words)}")
        items.append(WriteItem(addr, str(it.get("name", "")), words, str(it.get("text", ""))))
    return Profile(str(d.get("name") or Path(path).stem), items)
