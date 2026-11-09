"""Writable app folder (settings.json, reports/) for normal and frozen runs."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

SETTINGS_NAME = "settings.json"
REPORTS_DIR = "reports"


def _candidate_dirs():
    env = os.environ.get("SOLIS_APP_DIR")
    if env:
        yield Path(env)
    if getattr(sys, "frozen", False):
        yield Path(sys.executable).resolve().parent
    else:
        yield Path(__file__).resolve().parent.parent
    yield Path.home() / ".solis-modbus-config"


def app_dir() -> Path:
    """First writable candidate: $SOLIS_APP_DIR, exe/repo folder, ~/.solis-…"""
    last: Optional[Path] = None
    for d in _candidate_dirs():
        last = d
        try:
            d.mkdir(parents=True, exist_ok=True)
            probe = d / ".write_probe"
            probe.write_text("", encoding="utf-8")
            probe.unlink()
            return d
        except OSError:
            continue
    assert last is not None
    return last


def reports_dir() -> Path:
    d = app_dir() / REPORTS_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def settings_path() -> Path:
    return app_dir() / SETTINGS_NAME


def load_settings(path: Optional[Path] = None) -> Dict[str, Any]:
    p = path or settings_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(updates: Dict[str, Any], path: Optional[Path] = None) -> None:
    """Merge updates into settings.json (best effort, never raises)."""
    p = path or settings_path()
    data = load_settings(p)
    data.update(updates)
    try:
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
