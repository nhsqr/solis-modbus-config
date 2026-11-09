"""Linux/X11 start-up speed fix.

On GNOME (Wayland + Xwayland) with IBus, ``XMODIFIERS=@im=ibus`` makes Tk
talk to the IBus X input method every time a widget window is mapped. Each
round-trip costs ~35-45 ms, and CustomTkinter maps ~1000+ X windows while it
builds the register tabs, so the GUI took ~45-50 s to appear. Disabling the
X input method for this process only brings start-up down to ~3-4 s.

Numeric entry and keyboard-layout switching (XKB) are unaffected; only IBus
IMEs (e.g. CJK input) are off inside this app. Set ``SOLIS_KEEP_XIM=1`` to
keep the desktop's input method.
"""

from __future__ import annotations

import os
import platform
from typing import MutableMapping, Optional


def disable_x11_input_method(
    environ: Optional[MutableMapping[str, str]] = None,
    system: Optional[str] = None,
) -> bool:
    """Set ``XMODIFIERS=@im=none`` on Linux before Tk starts. Returns True if set."""
    env = os.environ if environ is None else environ
    system = platform.system() if system is None else system
    if system != "Linux":
        return False
    if env.get("SOLIS_KEEP_XIM", "").strip() == "1":
        return False
    env["XMODIFIERS"] = "@im=none"
    return True
