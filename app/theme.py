"""Forced Light theme and shared colours (CustomTkinter 5.2.2).

CustomTkinter 6.x + macOS "System"/transparent often renders as a flat
single colour. Always force Light + solid surfaces — same policy as v1.
"""
from __future__ import annotations

import os
import platform

import customtkinter as ctk

os.environ.setdefault("TK_SILENCE_DEPRECATION", "1")

IS_MAC = platform.system() == "Darwin"

# Explicit Light-mode palette (second tuple unused — appearance is always Light)
FG_APP = ("#F5F5F7", "#F5F5F7")
FG_BAR = ("#E8E8ED", "#E8E8ED")
FG_HDR = ("#DCDCE2", "#DCDCE2")
FG_ROW = ("#F5F5F7", "#F5F5F7")
FG_TAB = ("#FFFFFF", "#FFFFFF")
FG_ENTRY = ("#FFFFFF", "#FFFFFF")
TXT = ("#1D1D1F", "#1D1D1F")
TXT_MUTED = ("#6E6E73", "#6E6E73")
BORDER = ("#C6C6C8", "#C6C6C8")
BTN = ("#0A84FF", "#0A84FF")
BTN_HOVER = ("#0066CC", "#0066CC")
BTN_2ND = ("#E5E5EA", "#E5E5EA")
BTN_2ND_HOVER = ("#D1D1D6", "#D1D1D6")
BTN_BUSY = ("#AEAEB2", "#AEAEB2")
BTN_BUSY_TXT = ("#F2F2F7", "#F2F2F7")
FG_STATUS_BUSY = ("#FFE9A8", "#FFE9A8")
TXT_STATUS_BUSY = ("#3D2B00", "#3D2B00")
PROG_TRACK = ("#D1D1D6", "#D1D1D6")
PROG_FILL = ("#0A84FF", "#0A84FF")
DANGER = ("#CF222E", "#CF222E")
OK_GREEN = ("#1A7F37", "#1A7F37")
BUSY_POLL_MS = 100


def apply() -> None:
    ctk.set_appearance_mode("Light")
    ctk.set_default_color_theme("blue")


def pick_ui_font() -> str:
    """Prefer a font that actually contains Cyrillic on this Tk build."""
    import tkinter.font as tkfont
    have = {f.lower() for f in tkfont.families()}
    for name in ("DejaVu Sans", "Noto Sans", "Liberation Sans", "cyrui",
                 "Segoe UI", "Roboto", "Helvetica"):
        if name.lower() in have:
            return name
    return "DejaVu Sans"


def apply_ui_fonts() -> str:
    """Point CTk + Tk named fonts at a Cyrillic-capable family (after Tk root exists)."""
    family = pick_ui_font()
    try:
        if "CTkFont" in ctk.ThemeManager.theme:
            ctk.ThemeManager.theme["CTkFont"]["family"] = family
    except Exception:
        pass
    import tkinter.font as tkfont
    for name in tkfont.names():
        try:
            tkfont.nametofont(name).configure(family=family)
        except Exception:
            pass
    return family


def primary_button(parent, text, command, width=130, **kw):
    return ctk.CTkButton(
        parent, text=text, command=command, width=width,
        fg_color=BTN, text_color="#FFFFFF", hover_color=BTN_HOVER,
        text_color_disabled=BTN_BUSY_TXT, **kw,
    )


def secondary_button(parent, text, command, width=130, **kw):
    return ctk.CTkButton(
        parent, text=text, command=command, width=width,
        fg_color=BTN_2ND, text_color=TXT, hover_color=BTN_2ND_HOVER,
        text_color_disabled=BTN_BUSY_TXT, **kw,
    )
