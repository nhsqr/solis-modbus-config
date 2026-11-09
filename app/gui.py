"""CustomTkinter GUI: all Solis V3.5/V3.2 settings + live monitoring.

Layout: connection bar, actions, mode switch (settings / monitor), group list,
ttk.Treeview of the current group, editor + Bulgarian description panel.
Connect opens the TCP session only — no Modbus request is sent.
"""
from __future__ import annotations

import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, List, Optional, Union

import customtkinter as ctk

from app import theme
from app.appdata import load_settings, save_settings
from app.batch import DEFAULT_TIMEOUT, parse_timeout
from app.busy import ProgressTracker, format_busy_text
from app.catalog import Catalog, Register, default_catalog
from app.codec import (
    ACCURACY_REGISTER,
    compose_bits,
    compose_bytes,
    effective_scale,
    eng_to_raw,
    fmt_num,
    format_value,
    parse_number,
    range_check,
    raw_to_eng,
)
from app.explain import explanation, tooltip as tooltip_text
from app.groups import MONITOR_GROUPS, SETTING_GROUPS, Group
from app.modbus_client import ModbusError, SolisModbusClient, connected_status, friendly_connect_status
from app.ops import read_one, read_registers, write_setting
from app.profiles import Profile, ProfileError, load_profile
from app.writer import WriteItem
from app.x11_fix import disable_x11_input_method

disable_x11_input_method()
theme.apply()


def _style_treeview(family: str = "DejaVu Sans") -> None:
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(
        "Solis.Treeview",
        background="#FFFFFF",
        foreground="#1D1D1F",
        fieldbackground="#FFFFFF",
        rowheight=24,
        borderwidth=0,
        font=(family, 10),
    )
    style.configure(
        "Solis.Treeview.Heading",
        background="#DCDCE2",
        foreground="#1D1D1F",
        relief="flat",
        font=(family, 10, "bold"),
    )
    style.map(
        "Solis.Treeview",
        background=[("selected", "#0A84FF")],
        foreground=[("selected", "#FFFFFF")],
    )


def _leading_int(text: str) -> int:
    t = (text or "").strip()
    if not t:
        raise ValueError("empty")
    token = t.replace("–", " ").replace("—", " ").split()[0]
    if token.lower().startswith("0x"):
        return int(token, 16)
    return int(float(token.replace(",", ".")))


class HoverTip:
    """Delayed tooltip for a ttk.Treeview (row → catalog tooltip)."""

    def __init__(self, tree: ttk.Treeview, getter, delay_ms: int = 450) -> None:
        self.tree = tree
        self.getter = getter
        self.delay_ms = delay_ms
        self._after: Optional[str] = None
        self.win: Optional[tk.Toplevel] = None
        tree.bind("<Motion>", self._motion, add="+")
        tree.bind("<Leave>", lambda _e: self.hide(), add="+")
        tree.bind("<ButtonPress>", lambda _e: self.hide(), add="+")

    def _motion(self, event) -> None:
        self.hide()
        self._after = self.tree.after(self.delay_ms, lambda e=event: self._popup(e))

    def hide(self) -> None:
        if self._after:
            try:
                self.tree.after_cancel(self._after)
            except Exception:
                pass
            self._after = None
        if self.win is not None:
            try:
                self.win.destroy()
            except Exception:
                pass
            self.win = None

    def _popup(self, event) -> None:
        text = self.getter(event)
        if not text:
            return
        self.show(text, event.x_root + 16, event.y_root + 12)

    def show(self, text: str, x: int, y: int) -> None:
        self.hide()
        win = tk.Toplevel(self.tree)
        win.wm_overrideredirect(True)
        win.wm_geometry(f"+{int(x)}+{int(y)}")
        win.configure(background="#1D1D1F")
        lbl = tk.Label(
            win, text=text, justify="left", wraplength=460, background="#FFF8DC",
            foreground="#1D1D1F", relief="solid", borderwidth=1,
            font=("DejaVu Sans", 9), padx=8, pady=6, anchor="nw",
        )
        lbl.pack()
        self.win = win


class ValueEditor(ctk.CTkFrame):
    """Kind-aware editor: dropdown / bit checkboxes / hi-lo bytes / numeric entry."""

    def __init__(self, parent, **kw) -> None:
        super().__init__(parent, fg_color=theme.FG_TAB, **kw)
        self.reg: Optional[Register] = None
        self.accuracy_mode: Optional[int] = None
        self._raw: Union[int, str, None] = None
        self._bit_vars: Dict[int, Any] = {}
        self._on_change = None

        self.kind_lbl = ctk.CTkLabel(self, text="", anchor="w", text_color=theme.TXT_MUTED)
        self.kind_lbl.pack(fill="x", pady=(0, 4))

        self.enum_var = ctk.StringVar(value="")
        self.enum_menu = ctk.CTkOptionMenu(
            self, variable=self.enum_var, values=["—"], width=340, command=self._changed,
        )

        bytes_row = ctk.CTkFrame(self, fg_color=theme.FG_TAB)
        self.bytes_row = bytes_row
        ctk.CTkLabel(bytes_row, text="Старши байт", text_color=theme.TXT).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(bytes_row, text="Младши байт", text_color=theme.TXT).grid(row=0, column=1, sticky="w", padx=(12, 0))
        self.hi_var, self.lo_var = ctk.StringVar(value=""), ctk.StringVar(value="")
        self.hi_menu = ctk.CTkOptionMenu(bytes_row, variable=self.hi_var, values=["0"], width=160, command=self._changed)
        self.lo_menu = ctk.CTkOptionMenu(bytes_row, variable=self.lo_var, values=["0"], width=160, command=self._changed)
        self.hi_menu.grid(row=1, column=0, sticky="ew", pady=2)
        self.lo_menu.grid(row=1, column=1, sticky="ew", padx=(12, 0), pady=2)

        self.bits_box = ctk.CTkScrollableFrame(self, fg_color=theme.FG_TAB, height=180)

        val_row = ctk.CTkFrame(self, fg_color=theme.FG_TAB)
        self.val_row = val_row
        self.eng_var = ctk.StringVar(value="")
        self.eng_entry = ctk.CTkEntry(val_row, textvariable=self.eng_var, width=160,
                                      fg_color=theme.FG_ENTRY, text_color=theme.TXT, border_color=theme.BORDER)
        self.eng_entry.pack(side="left")
        self.eng_entry.bind("<KeyRelease>", self._changed)
        self.unit_lbl = ctk.CTkLabel(val_row, text="", text_color=theme.TXT, width=80, anchor="w")
        self.unit_lbl.pack(side="left", padx=6)

        raw_row = ctk.CTkFrame(self, fg_color=theme.FG_TAB)
        raw_row.pack(fill="x", pady=(6, 0))
        ctk.CTkLabel(raw_row, text="Raw", text_color=theme.TXT_MUTED, width=40, anchor="w").pack(side="left")
        self.raw_var = ctk.StringVar(value="")
        self.raw_entry = ctk.CTkEntry(raw_row, textvariable=self.raw_var, width=160,
                                      fg_color=theme.FG_ENTRY, text_color=theme.TXT, border_color=theme.BORDER)
        self.raw_entry.pack(side="left")
        self.raw_entry.bind("<KeyRelease>", self._changed_raw)
        self.warn_lbl = ctk.CTkLabel(self, text="", anchor="w", justify="left", wraplength=340,
                                     text_color=theme.DANGER)
        self.warn_lbl.pack(fill="x", pady=(4, 0))

        self._use_raw = False

    def set_on_change(self, fn) -> None:
        self._on_change = fn

    def load(self, reg: Register, raw=None, accuracy_mode: Optional[int] = None) -> None:
        self.reg, self._raw, self.accuracy_mode = reg, raw, accuracy_mode
        self._use_raw = False
        self._bit_vars.clear()
        for w in self.bits_box.winfo_children():
            w.destroy()
        self.enum_menu.pack_forget()
        self.bytes_row.pack_forget()
        self.bits_box.pack_forget()
        self.val_row.pack_forget()
        self.warn_lbl.configure(text="")

        kind = reg.kind
        self.kind_lbl.configure(text=f"{reg.type} · {reg.words} рег. · {kind}")

        if kind == "enum" and reg.options:
            labels = [f"{k} – {v}" for k, v in sorted(reg.options.items())]
            self.enum_menu.configure(values=labels or ["—"])
            pick = None
            if raw is not None and not isinstance(raw, str) and int(raw) in reg.options:
                pick = f"{int(raw)} – {reg.options[int(raw)]}"
            self.enum_var.set(pick or labels[0])
            self.enum_menu.pack(fill="x", pady=2)
        elif kind == "bytes" and (reg.hi_options or reg.lo_options):
            self._fill_byte_menu(self.hi_menu, self.hi_var, reg.hi_options or {},
                                 ((int(raw) >> 8) & 0xFF) if isinstance(raw, int) else None)
            self._fill_byte_menu(self.lo_menu, self.lo_var, reg.lo_options or {},
                                 (int(raw) & 0xFF) if isinstance(raw, int) else None)
            self.bytes_row.pack(fill="x", pady=2)
        elif kind == "bits" and reg.bits:
            self._fill_bits(reg, raw if isinstance(raw, int) else 0)
            self.bits_box.pack(fill="both", expand=True, pady=2)
        else:
            self.val_row.pack(fill="x", pady=2)
            if raw is None:
                self.eng_var.set("")
            elif isinstance(raw, str):
                self.eng_var.set(raw)
            else:
                sc = effective_scale(reg, accuracy_mode)
                self.eng_var.set(fmt_num(raw_to_eng(reg, int(raw), accuracy_mode), sc))
            self.unit_lbl.configure(text=reg.unit or "")

        if raw is None:
            self.raw_var.set("")
        elif isinstance(raw, str):
            self.raw_var.set(raw)
        else:
            self.raw_var.set(str(int(raw)))
        self._refresh_warn()

    def _fill_byte_menu(self, menu, var, options: Dict[int, str], current: Optional[int]) -> None:
        labels = [f"{k} – {v}" for k, v in sorted(options.items())]
        if current is not None and current not in options:
            labels = [f"{current} – (не е в списъка)"] + labels
        menu.configure(values=labels or ["0"])
        if current is not None and current in options:
            var.set(f"{current} – {options[current]}")
        elif current is not None:
            var.set(f"{current} – (не е в списъка)")
        else:
            var.set(labels[0] if labels else "0")

    def _fill_bits(self, reg: Register, raw: int) -> None:
        for b in reg.bits:
            if b.reserved:
                continue
            row = ctk.CTkFrame(self.bits_box, fg_color=theme.FG_TAB)
            row.pack(fill="x", pady=1)
            cur = b.get(raw)
            if b.width == 1:
                var = ctk.IntVar(value=1 if cur else 0)
                on_lab = (b.states or {}).get("1") or (b.states or {}).get(1) or "ON"
                off_lab = (b.states or {}).get("0") or (b.states or {}).get(0) or "OFF"
                text = f"{b.label()}  ({off_lab} / {on_lab})"
                cb = ctk.CTkCheckBox(row, text=text, variable=var, text_color=theme.TXT,
                                     command=self._changed)
                cb.pack(side="left", anchor="w")
                self._bit_vars[b.lo] = ("cb", var)
            elif b.states:
                var = ctk.StringVar()
                labels = [f"{k} – {v}" for k, v in b.states.items()]
                if str(cur) in b.states:
                    var.set(f"{cur} – {b.states[str(cur)]}")
                else:
                    var.set(labels[0] if labels else str(cur))
                    labels = [f"{cur} – ?"] + labels
                ctk.CTkLabel(row, text=b.label(), text_color=theme.TXT, width=180, anchor="w").pack(side="left")
                menu = ctk.CTkOptionMenu(row, variable=var, values=labels or ["0"], width=150, command=self._changed)
                menu.pack(side="left", padx=4)
                self._bit_vars[b.lo] = ("enum", var)
            else:
                var = ctk.StringVar(value=str(cur))
                ctk.CTkLabel(row, text=b.label(), text_color=theme.TXT, width=180, anchor="w").pack(side="left")
                ent = ctk.CTkEntry(row, textvariable=var, width=80, fg_color=theme.FG_ENTRY,
                                   text_color=theme.TXT, border_color=theme.BORDER)
                ent.pack(side="left")
                ent.bind("<KeyRelease>", self._changed)
                self._bit_vars[b.lo] = ("entry", var)

    def _changed(self, *_a) -> None:
        self._use_raw = False
        try:
            raw = self.get_raw()
            if not isinstance(raw, str):
                self.raw_var.set(str(int(raw)))
        except Exception:
            pass
        self._refresh_warn()
        if self._on_change:
            self._on_change()

    def _changed_raw(self, *_a) -> None:
        self._use_raw = True
        self._refresh_warn()
        if self._on_change:
            self._on_change()

    def _refresh_warn(self) -> None:
        if self.reg is None:
            return
        if not self.raw_var.get().strip() and not self.eng_var.get().strip() and self.reg.kind in ("value", "hex", "ascii"):
            self.warn_lbl.configure(text="")
            return
        try:
            raw = self.get_raw()
        except Exception as exc:
            self.warn_lbl.configure(text=str(exc))
            return
        if isinstance(raw, str):
            self.warn_lbl.configure(text="")
            return
        msg = range_check(self.reg, int(raw), self.accuracy_mode)
        self.warn_lbl.configure(text=msg or "")

    def get_raw(self) -> Union[int, str]:
        if self.reg is None:
            raise ValueError("no register")
        if self._use_raw:
            t = self.raw_var.get().strip()
            if self.reg.kind == "ascii" or self.reg.type == "ASCII":
                return t
            return int(round(parse_number(t)))
        kind, reg = self.reg.kind, self.reg
        if kind == "enum" and reg.options:
            return _leading_int(self.enum_var.get())
        if kind == "bytes" and (reg.hi_options or reg.lo_options):
            return compose_bytes(_leading_int(self.hi_var.get()), _leading_int(self.lo_var.get()))
        if kind == "bits" and reg.bits:
            old = self._raw if isinstance(self._raw, int) else 0
            changes = {}
            for lo, (typ, var) in self._bit_vars.items():
                if typ == "cb":
                    changes[lo] = int(var.get())
                else:
                    changes[lo] = _leading_int(var.get())
            return compose_bits(reg, old, changes)
        t = self.eng_var.get().strip()
        if kind == "ascii" or reg.type == "ASCII":
            return t
        if kind == "hex":
            return int(round(parse_number(t if t else self.raw_var.get())))
        return eng_to_raw(reg, parse_number(t), self.accuracy_mode)



class SolisConfigApp(ctk.CTk):
    def __init__(self, catalog: Optional[Catalog] = None) -> None:
        super().__init__()
        self.title("Solis Modbus Config")
        self.geometry("1400x820")
        self.minsize(1100, 640)
        self.configure(fg_color=theme.FG_APP)

        self.catalog = catalog or default_catalog()
        settings = load_settings()
        host = str(settings.get("host", "192.168.85.104"))
        port = int(settings.get("port", 502) or 502)
        unit = int(settings.get("unit_id", 1) or 1)
        try:
            timeout = parse_timeout(settings.get("timeout", DEFAULT_TIMEOUT))
        except Exception:
            timeout = DEFAULT_TIMEOUT
        self.client = SolisModbusClient(host=host, port=port, unit_id=unit, timeout=timeout)
        self.accuracy_mode: Optional[int] = None
        self.dry_run = ctk.BooleanVar(value=False)
        self.mode = "settings"  # or "monitor"
        self.group_id = SETTING_GROUPS[0].id
        self._raw: Dict[int, Union[int, str]] = {}
        self._errors: Dict[int, str] = {}
        self._visible: List[Register] = []
        self._group_btns: Dict[str, ctk.CTkButton] = {}
        self.profile = Profile("profile")
        self.selected: Optional[Register] = None
        self._busy = False
        self._busy_status_frozen = False
        self._progress = ProgressTracker()
        self._busy_buttons: List[ctk.CTkButton] = []
        self._batch_dialog = None
        self._search_after: Optional[str] = None

        self._font_family = theme.apply_ui_fonts()
        _style_treeview(self._font_family)
        self._build_ui()
        self.set_status("Изключен — Connect не чете и не записва нищо.")
        self.select_group(self.group_id)

    # ---------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        self._build_connection()
        self._build_actions()
        body = ctk.CTkFrame(self, fg_color=theme.FG_APP)
        body.pack(fill="both", expand=True, padx=8, pady=4)
        body.grid_columnconfigure(1, weight=3)
        body.grid_columnconfigure(2, weight=2)
        body.grid_rowconfigure(0, weight=1)

        side = ctk.CTkFrame(body, fg_color=theme.FG_BAR, width=270)
        side.grid(row=0, column=0, sticky="nsw", padx=(0, 6))
        side.grid_propagate(False)
        ctk.CTkLabel(side, text="Групи", text_color=theme.TXT, font=ctk.CTkFont(weight="bold")).pack(
            fill="x", padx=8, pady=(8, 4)
        )
        self.group_scroll = ctk.CTkScrollableFrame(side, fg_color=theme.FG_BAR, width=250)
        self.group_scroll.pack(fill="both", expand=True, padx=4, pady=(0, 8))
        self._rebuild_group_buttons()

        mid = ctk.CTkFrame(body, fg_color=theme.FG_TAB)
        mid.grid(row=0, column=1, sticky="nsew", padx=2)
        mid.grid_rowconfigure(2, weight=1)
        mid.grid_columnconfigure(0, weight=1)
        self.group_title = ctk.CTkLabel(mid, text="", anchor="w", font=ctk.CTkFont(size=16, weight="bold"),
                                        text_color=theme.TXT)
        self.group_title.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 0))
        self.group_intro = ctk.CTkLabel(mid, text="", anchor="w", justify="left", wraplength=640,
                                        text_color=theme.TXT_MUTED, font=ctk.CTkFont(size=12))
        self.group_intro.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 4))

        tree_frame = tk.Frame(mid, background="#FFFFFF")
        tree_frame.grid(row=2, column=0, sticky="nsew", padx=4, pady=4)
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)
        cols = ("addr", "name", "type", "value", "raw")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings", style="Solis.Treeview",
                                 selectmode="browse")
        for cid, text, w, stretch in (
            ("addr", "Адрес", 90, False),
            ("name", "Име", 340, True),
            ("type", "Тип", 70, False),
            ("value", "Стойност", 280, True),
            ("raw", "Raw", 90, False),
        ):
            self.tree.heading(cid, text=text, anchor="w")
            self.tree.column(cid, width=w, stretch=stretch, anchor="w")
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._on_tree_select())
        self.tip = HoverTip(self.tree, self._tooltip_for_event)

        panel = ctk.CTkFrame(body, fg_color=theme.FG_TAB, width=400)
        panel.grid(row=0, column=2, sticky="nsew", padx=(6, 0))
        panel.grid_propagate(False)
        panel.grid_rowconfigure(3, weight=1)
        panel.grid_columnconfigure(0, weight=1)
        self.sel_bg = ctk.CTkLabel(panel, text="Изберете настройка", anchor="w", justify="left",
                                   wraplength=370, font=ctk.CTkFont(size=15, weight="bold"),
                                   text_color=theme.TXT)
        self.sel_bg.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 0))
        self.sel_en = ctk.CTkLabel(panel, text="", anchor="w", justify="left", wraplength=370,
                                   text_color=theme.TXT_MUTED)
        self.sel_en.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 6))
        self.editor = ValueEditor(panel)
        self.editor.grid(row=2, column=0, sticky="ew", padx=10, pady=4)
        self.desc = ctk.CTkTextbox(
            panel, wrap="word", fg_color=theme.FG_ENTRY, text_color=theme.TXT,
            border_color=theme.BORDER, border_width=1,
            scrollbar_button_color=theme.BORDER, font=ctk.CTkFont(size=13),
        )
        self.desc.grid(row=3, column=0, sticky="nsew", padx=10, pady=(4, 10))
        self.desc.insert("1.0", "Подробно обяснение на избраната настройка се показва тук.")
        self.desc.configure(state="disabled")

        self.status_bar = ctk.CTkFrame(self, fg_color=theme.FG_BAR, corner_radius=6)
        self.status_bar.pack(fill="x", padx=8, pady=(0, 8))
        self.status = ctk.CTkLabel(self.status_bar, text="", anchor="w", text_color=theme.TXT)
        self.status.pack(side="left", fill="x", expand=True, padx=(8, 4), pady=2)
        self.progress_bar = ctk.CTkProgressBar(
            self.status_bar, width=260, height=12, mode="indeterminate",
            fg_color=theme.PROG_TRACK, progress_color=theme.PROG_FILL, border_width=0,
        )
        self.progress_bar.set(0)

    def _build_connection(self) -> None:
        top = ctk.CTkFrame(self, fg_color=theme.FG_BAR)
        top.pack(fill="x", padx=8, pady=6)
        ctk.CTkLabel(top, text="Host", text_color=theme.TXT).pack(side="left", padx=(4, 2))
        self.host_entry = ctk.CTkEntry(top, width=140, fg_color=theme.FG_ENTRY, text_color=theme.TXT,
                                       border_color=theme.BORDER)
        self.host_entry.insert(0, self.client.host)
        self.host_entry.pack(side="left", padx=2)
        ctk.CTkLabel(top, text="Port", text_color=theme.TXT).pack(side="left", padx=(8, 2))
        self.port_entry = ctk.CTkEntry(top, width=56, fg_color=theme.FG_ENTRY, text_color=theme.TXT,
                                       border_color=theme.BORDER)
        self.port_entry.insert(0, str(self.client.port))
        self.port_entry.pack(side="left", padx=2)
        ctk.CTkLabel(top, text="Unit", text_color=theme.TXT).pack(side="left", padx=(8, 2))
        self.unit_entry = ctk.CTkEntry(top, width=44, fg_color=theme.FG_ENTRY, text_color=theme.TXT,
                                       border_color=theme.BORDER)
        self.unit_entry.insert(0, str(self.client.unit_id))
        self.unit_entry.pack(side="left", padx=2)
        ctk.CTkLabel(top, text="Timeout (s)", text_color=theme.TXT).pack(side="left", padx=(8, 2))
        self.timeout_entry = ctk.CTkEntry(top, width=48, fg_color=theme.FG_ENTRY, text_color=theme.TXT,
                                          border_color=theme.BORDER)
        self.timeout_entry.insert(0, f"{self.client.timeout:g}")
        self.timeout_entry.pack(side="left", padx=2)

        self.conn_btn = theme.primary_button(top, "Connect", self.toggle_connect, width=110)
        self.conn_btn.pack(side="left", padx=8)
        self._busy_buttons.append(self.conn_btn)
        self.conn_lbl = ctk.CTkLabel(top, text="● Изключен", text_color=theme.TXT_MUTED)
        self.conn_lbl.pack(side="left", padx=4)
        ctk.CTkLabel(top, text="43081", text_color=theme.TXT).pack(side="left", padx=(16, 2))
        self.acc_lbl = ctk.CTkLabel(top, text="(не е четен)", width=140, anchor="w", text_color=theme.TXT_MUTED)
        self.acc_lbl.pack(side="left", padx=2)
        ctk.CTkCheckBox(top, text="Dry-run (без запис)", variable=self.dry_run, text_color=theme.TXT).pack(
            side="right", padx=8
        )

    def _build_actions(self) -> None:
        wrap = ctk.CTkFrame(self, fg_color=theme.FG_BAR)
        wrap.pack(fill="x", padx=8, pady=2)
        bar = ctk.CTkFrame(wrap, fg_color=theme.FG_BAR)
        bar.pack(fill="x")
        self.mode_seg = ctk.CTkSegmentedButton(
            bar, values=["Настройки", "Мониторинг"], command=self._on_mode,
            selected_color=theme.BTN[0], selected_hover_color=theme.BTN_HOVER[0],
            unselected_color=theme.BTN_2ND[0], unselected_hover_color=theme.BTN_2ND_HOVER[0],
            text_color=theme.TXT,
        )
        self.mode_seg.set("Настройки")
        self.mode_seg.pack(side="left", padx=4, pady=4)
        ctk.CTkLabel(bar, text="Търсене", text_color=theme.TXT).pack(side="left", padx=(12, 2))
        self.search_var = ctk.StringVar(value="")
        self.search_entry = ctk.CTkEntry(bar, width=220, textvariable=self.search_var,
                                         fg_color=theme.FG_ENTRY, text_color=theme.TXT, border_color=theme.BORDER)
        self.search_entry.pack(side="left", padx=2)
        self.search_var.trace_add("write", lambda *_a: self._schedule_search())
        self.profile_lbl = ctk.CTkLabel(bar, text="Профил: 0", text_color=theme.TXT_MUTED)
        self.profile_lbl.pack(side="left", padx=10)

        bar2 = ctk.CTkFrame(wrap, fg_color=theme.FG_BAR)
        bar2.pack(fill="x")
        for text, cmd, w in [
            ("Прочети групата", self.read_group, 150),
            ("Прочети избраното", self.read_selected, 160),
            ("Запиши избраното", self.write_selected, 150),
            ("Добави в профил", self.add_to_profile, 140),
            ("Пакетно към много устройства...", self.open_batch_dialog, 230),
            ("Зареди профил...", self.load_profile_file, 140),
            ("Запиши профил...", self.save_profile_file, 140),
        ]:
            btn = theme.primary_button(bar2, text, cmd, width=w)
            btn.pack(side="left", padx=3, pady=4)
            self._busy_buttons.append(btn)

    def _rebuild_group_buttons(self) -> None:
        for w in self.group_scroll.winfo_children():
            w.destroy()
        self._group_btns.clear()
        groups = SETTING_GROUPS if self.mode == "settings" else MONITOR_GROUPS
        writable = self.mode == "settings"
        for g in groups:
            n = len(self.catalog.in_group(g.id, writable))
            btn = ctk.CTkButton(
                self.group_scroll, text=f"{g.title_bg}  ({n})", anchor="w",
                fg_color=theme.BTN_2ND, text_color=theme.TXT, hover_color=theme.BTN_2ND_HOVER,
                command=lambda gid=g.id: self.select_group(gid),
            )
            btn.pack(fill="x", pady=1)
            self._group_btns[g.id] = btn

    # ---------------------------------------------------------- navigation
    def _on_mode(self, value: str) -> None:
        self.select_mode("settings" if value.startswith("Настр") else "monitor")

    def select_mode(self, mode: str) -> None:
        mode = "monitor" if mode == "monitor" else "settings"
        if mode != self.mode:
            self.mode = mode
            self.mode_seg.set("Мониторинг" if mode == "monitor" else "Настройки")
            self._rebuild_group_buttons()
            groups = MONITOR_GROUPS if mode == "monitor" else SETTING_GROUPS
            self.select_group(groups[0].id)

    def current_groups(self) -> List[Group]:
        return SETTING_GROUPS if self.mode == "settings" else MONITOR_GROUPS

    def select_group(self, gid: str) -> None:
        self.group_id = gid
        for i, btn in self._group_btns.items():
            btn.configure(fg_color=theme.BTN if i == gid else theme.BTN_2ND,
                          text_color="#FFFFFF" if i == gid else theme.TXT)
        g = next((x for x in self.current_groups() if x.id == gid), None)
        writable = self.mode == "settings"
        n = len(self.catalog.in_group(gid, writable))
        self.group_title.configure(text=f"{g.title_bg if g else gid}  ·  {n}")
        self.group_intro.configure(text=g.intro_bg if g else "")
        self.populate_tree()

    def _matching(self) -> List[Register]:
        writable = self.mode == "settings"
        q = (self.search_var.get() or "").strip()
        if q:
            return [r for r in self.catalog.search(q, writable=writable)]
        return self.catalog.in_group(self.group_id, writable)

    def populate_tree(self) -> None:
        q = (self.search_var.get() or "").strip()
        if q:
            self.group_title.configure(text=f"Резултати за „{q}“  ·  {len(self._matching())}")
            self.group_intro.configure(text="Търсене във всички групи на текущия режим.")
        self._visible = self._matching()
        self.tree.delete(*self.tree.get_children())
        for r in self._visible:
            self.tree.insert("", "end", iid=str(r.address), values=self._row_values(r))

    def _row_values(self, r: Register) -> tuple:
        raw = self._raw.get(r.address)
        if r.address in self._errors:
            val, rw = self._errors[r.address], ""
        elif raw is None:
            val, rw = "—", ""
        else:
            val = format_value(r, raw, self.accuracy_mode)
            rw = raw if isinstance(raw, str) else str(int(raw))
        name = r.display_name
        if r.danger:
            name = "⚠ " + name
        return (r.addr_text, name, r.type, val, rw)

    def _refresh_row(self, r: Register) -> None:
        iid = str(r.address)
        if self.tree.exists(iid):
            self.tree.item(iid, values=self._row_values(r))

    def _tooltip_for_event(self, event) -> Optional[str]:
        iid = self.tree.identify_row(event.y)
        if not iid:
            return None
        try:
            reg = self.catalog.get(int(iid))
        except ValueError:
            return None
        if not reg:
            return None
        return tooltip_text(reg, raw=self._raw.get(reg.address), accuracy_mode=self.accuracy_mode)

    def _on_tree_select(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        try:
            reg = self.catalog.get(int(sel[0]))
        except ValueError:
            return
        if not reg:
            return
        self.show_register(reg)

    def show_register(self, reg: Register) -> None:
        self.selected = reg
        self.sel_bg.configure(text=reg.display_name or reg.name)
        extra = f"{reg.addr_text}  ·  {reg.name}"
        if reg.danger:
            extra += "  ·  ⚠ сервизна/командна"
        self.sel_en.configure(text=extra)
        raw = self._raw.get(reg.address)
        # editor only for writable settings; monitor stays read-only
        if reg.writable:
            self.editor.grid()
            self.editor.load(reg, raw, self.accuracy_mode)
        else:
            self.editor.grid_remove()
        text = explanation(reg, raw=raw, accuracy_mode=self.accuracy_mode, full=True)
        self.desc.configure(state="normal")
        self.desc.delete("1.0", "end")
        self.desc.insert("1.0", text)
        self.desc.configure(state="disabled")

    def select_address(self, address: int) -> bool:
        """Select a register (switches mode/group). Used by tests and screenshots."""
        reg = self.catalog.get(int(address))
        if not reg:
            return False
        self.search_var.set("")
        self.select_mode("monitor" if not reg.writable else "settings")
        self.select_group(reg.group)
        iid = str(reg.address)
        if not self.tree.exists(iid):
            return False
        self.tree.selection_set(iid)
        self.tree.see(iid)
        self.show_register(reg)
        return True

    def _schedule_search(self) -> None:
        if self._search_after:
            try:
                self.after_cancel(self._search_after)
            except Exception:
                pass
        self._search_after = self.after(200, self.populate_tree)

    # -------------------------------------------------------------- status
    def set_status(self, msg: str) -> None:
        self.status.configure(text=msg)
        if self._busy:
            self._busy_status_frozen = True

    def _update_acc_label(self) -> None:
        if self.accuracy_mode is None:
            self.acc_lbl.configure(text="(не е четен)", text_color=theme.TXT_MUTED)
        else:
            hint = "0.1 V / 10 ms / 0.01 Hz" if self.accuracy_mode == 1 else "1 V / 100 ms / 0.1 Hz"
            self.acc_lbl.configure(text=f"{self.accuracy_mode}  ({hint})", text_color=theme.TXT)

    def _note_accuracy(self, values: Dict[int, Any]) -> None:
        if ACCURACY_REGISTER in values and isinstance(values[ACCURACY_REGISTER], int):
            if values[ACCURACY_REGISTER] in (0, 1):
                self.accuracy_mode = int(values[ACCURACY_REGISTER])
                self._update_acc_label()

    # ------------------------------------------------------------- connect
    def toggle_connect(self) -> None:
        if self._busy:
            self.bell()
            return
        if self.client.connected:
            self.client.disconnect()
            self.conn_btn.configure(text="Connect")
            self.conn_lbl.configure(text="● Изключен", text_color=theme.TXT_MUTED)
            self.set_status("Изключен")
            return
        try:
            self.client.host = self.host_entry.get().strip()
            self.client.port = int(self.port_entry.get().strip())
            self.client.unit_id = int(self.unit_entry.get().strip())
            self.client.timeout = parse_timeout(self.timeout_entry.get())
        except ValueError:
            self.set_status("Невалиден порт, unit id или timeout")
            messagebox.showerror("Вход", "Невалиден порт, unit id или timeout (0.5–60 s)")
            return
        save_settings({"host": self.client.host, "port": self.client.port,
                       "unit_id": self.client.unit_id, "timeout": self.client.timeout})
        self.conn_lbl.configure(text="● Свързване…", text_color=("#C97800", "#C97800"))
        self._run_bg(self._connect_worker, f"Свързване към {self.client.host}:{self.client.port}")

    def _connect_worker(self) -> None:
        try:
            # Connect = open the TCP/Modbus session ONLY. No Modbus request.
            self.client.connect()
            self.after(0, self._on_connected)
        except ModbusError as exc:
            err = str(exc)
            self.after(0, lambda e=err: self._on_connect_fail(e))
        except Exception as exc:  # noqa: BLE001
            err = f"Could not connect ({exc})"
            self.after(0, lambda e=err: self._on_connect_fail(e))

    def _on_connected(self) -> None:
        self.conn_btn.configure(text="Disconnect")
        self.conn_lbl.configure(text=f"● {self.client.host}:{self.client.port} ID {self.client.unit_id}",
                                text_color=theme.OK_GREEN)
        self.set_status(connected_status(self.client) + " — no auto-read")

    def _on_connect_fail(self, err: str) -> None:
        self.conn_btn.configure(text="Connect")
        self.conn_lbl.configure(text="● Изключен", text_color=theme.TXT_MUTED)
        self.set_status(friendly_connect_status(err))

    def _disconnect_main(self) -> None:
        self.client.disconnect()
        self.conn_btn.configure(text="Connect")
        self.conn_lbl.configure(text="● Изключен", text_color=theme.TXT_MUTED)

    def _ensure_connected(self) -> bool:
        if not self.client.connected:
            messagebox.showwarning("Modbus", "Не сте свързани.")
            return False
        return True

    # --------------------------------------------------------------- busy
    def _run_bg(self, fn, label: str = "Working") -> None:
        if self._busy:
            self.bell()
            return
        self._begin_busy(label)

        def wrapper() -> None:
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                err = f"{label} — {exc}"
                self.after(0, lambda e=err: self.set_status(e))
            finally:
                self.after(0, self._clear_busy)

        threading.Thread(target=wrapper, daemon=True).start()

    def _begin_busy(self, label: str) -> None:
        self._busy = True
        self._busy_status_frozen = False
        self._progress.start(label)
        for btn in self._busy_buttons:
            try:
                btn.configure(state="disabled", fg_color=theme.BTN_BUSY)
            except Exception:
                pass
        self.status_bar.configure(fg_color=theme.FG_STATUS_BUSY)
        self.status.configure(text_color=theme.TXT_STATUS_BUSY, text=f"{label}…")
        self.progress_bar.configure(mode="indeterminate")
        self.progress_bar.pack(side="right", padx=(4, 10), pady=6)
        self.progress_bar.start()
        try:
            tk.Tk.configure(self, cursor="watch")
        except Exception:
            pass
        self.after(theme.BUSY_POLL_MS, self._poll_busy)

    def _report_progress(self, done: int, total: int, detail: str = "") -> None:
        self._progress.update(done, total, detail)

    def _poll_busy(self) -> None:
        if not self._busy:
            return
        snap = self._progress.snapshot()
        frac = snap.fraction
        if frac is not None:
            if self.progress_bar.cget("mode") != "determinate":
                self.progress_bar.stop()
                self.progress_bar.configure(mode="determinate")
            self.progress_bar.set(frac)
        if not self._busy_status_frozen:
            self.status.configure(text=format_busy_text(snap))
        self.after(theme.BUSY_POLL_MS, self._poll_busy)

    def _clear_busy(self) -> None:
        self._busy = False
        elapsed = self._progress.finish()
        if self._busy_status_frozen:
            cur = str(self.status.cget("text"))
            self.status.configure(text=f"{cur} · {elapsed:.1f} s")
        else:
            label = self._progress.snapshot().label
            self.status.configure(text=f"{label} — готово · {elapsed:.1f} s")
        try:
            self.progress_bar.stop()
        except Exception:
            pass
        self.progress_bar.pack_forget()
        self.progress_bar.configure(mode="indeterminate")
        self.progress_bar.set(0)
        self.status_bar.configure(fg_color=theme.FG_BAR)
        self.status.configure(text_color=theme.TXT)
        for btn in self._busy_buttons:
            try:
                btn.configure(state="normal", fg_color=theme.BTN)
            except Exception:
                pass
        try:
            tk.Tk.configure(self, cursor="")
        except Exception:
            pass
        self._busy_status_frozen = False

    # ---------------------------------------------------------------- read
    def _regs_plus_accuracy(self, regs: List[Register]) -> List[Register]:
        out = list(regs)
        if any(r.address in range(43090, 43106) for r in out) and not any(r.address == ACCURACY_REGISTER for r in out):
            extra = self.catalog.get(ACCURACY_REGISTER)
            if extra:
                out.append(extra)
        return out

    def read_group(self) -> None:
        if not self._ensure_connected():
            return
        regs = self._regs_plus_accuracy(self._matching())
        self._run_bg(lambda: self._read_worker(regs), f"Четене на {len(regs)} регистъра")

    def read_selected(self) -> None:
        if not self._ensure_connected() or self.selected is None:
            if self.selected is None:
                messagebox.showinfo("Четене", "Изберете ред от таблицата.")
            return
        regs = self._regs_plus_accuracy([self.selected])
        self._run_bg(lambda: self._read_worker(regs), f"Четене {self.selected.address}")

    def _read_worker(self, regs: List[Register]) -> None:
        result = read_registers(
            self.client, regs,
            on_progress=lambda d, t, s: self._report_progress(d, t, s),
        )
        self.after(0, lambda: self._apply_read(result, len(regs)))

    def _apply_read(self, result, n_asked: int) -> None:
        self._raw.update(result.values)
        self._errors.update(result.errors)
        for addr in result.values:
            self._errors.pop(addr, None)
        self._note_accuracy(result.values)
        for r in self._visible:
            self._refresh_row(r)
        if self.selected and (self.selected.address in result.values or self.selected.address in result.errors):
            self.show_register(self.selected)
        ok, bad = len(result.values), len(result.errors)
        self.set_status(f"Прочетени {ok}/{n_asked}" + (f", грешки {bad}" if bad else "")
                        + f" ({result.requests} заявки)")

    # --------------------------------------------------------------- write
    def write_selected(self) -> None:
        if not self._ensure_connected() or self.selected is None:
            if self.selected is None:
                messagebox.showinfo("Запис", "Изберете настройка.")
            return
        reg = self.selected
        if not reg.writable:
            messagebox.showinfo("Запис", "Този регистър е само за четене.")
            return
        try:
            raw = self.editor.get_raw()
        except Exception as exc:
            messagebox.showerror("Стойност", str(exc))
            return
        warn = None if isinstance(raw, str) else range_check(reg, int(raw), self.accuracy_mode)
        preview = format_value(reg, raw if isinstance(raw, str) else int(raw), self.accuracy_mode)
        lines = [f"{reg.addr_text}  {reg.display_name}", reg.name, f"→ {preview}  (raw {raw})"]
        if warn:
            lines.append("")
            lines.append("⚠ " + warn)
        if reg.danger:
            lines.append("")
            lines.append("⚠ Сервизна/командна настройка — записът изпълнява действие.")
        if self.dry_run.get():
            self.set_status(f"Dry-run: would write {reg.address} ← {raw} ({preview})")
            messagebox.showinfo("Dry-run", "\n".join(lines) + "\n\nНищо не е изпратено.")
            return
        title = "Потвърдете ЗАПИС" + (" (опасно)" if reg.danger else "")
        if not self._confirm_long("\n".join(lines), title=title, confirm_text="Запиши"):
            return

        def worker() -> None:
            out = write_setting(self.client, reg, raw, verify=True)
            self.after(0, lambda: self._after_write(reg, out))

        self._run_bg(worker, f"Запис {reg.address}")

    def _after_write(self, reg: Register, out) -> None:
        if out.readback is not None:
            from app.codec import words_to_raw
            self._raw[reg.address] = words_to_raw(reg, out.readback)
            self._refresh_row(reg)
            if self.selected and self.selected.address == reg.address:
                self.show_register(reg)
        self.set_status(f"{reg.address}: {out.message}")
        if not out.ok:
            messagebox.showwarning("Проверка след запис", out.message)

    # ------------------------------------------------------------- profile
    def _refresh_profile_lbl(self) -> None:
        self.profile_lbl.configure(text=f"Профил: {len(self.profile.items)}")

    def add_to_profile(self) -> None:
        if self.selected is None or not self.selected.writable:
            messagebox.showinfo("Профил", "Изберете записваема настройка.")
            return
        try:
            raw = self.editor.get_raw()
        except Exception as exc:
            messagebox.showerror("Стойност", str(exc))
            return
        from app.ops import make_item
        try:
            item = make_item(self.selected, raw, format_value(self.selected, raw, self.accuracy_mode))
        except Exception as exc:
            messagebox.showerror("Профил", str(exc))
            return
        self.profile.set(item)
        self._refresh_profile_lbl()
        self.set_status(f"Добавено в профила: {item.address} ← {item.text or item.raw}")

    def load_profile_file(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("Profile JSON", "*.json"), ("All", "*.*")])
        if not path:
            return
        try:
            self.profile = load_profile(path, self.catalog)
        except ProfileError as exc:
            messagebox.showerror("Профил", str(exc))
            return
        self._refresh_profile_lbl()
        self.set_status(f"Зареден профил „{self.profile.name}“: {len(self.profile.items)} настройки")

    def save_profile_file(self) -> None:
        if not self.profile.items:
            messagebox.showinfo("Профил", "Профилът е празен. Добавете настройки с „Добави в профил“.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".json", initialfile=f"{self.profile.name}.json",
            filetypes=[("Profile JSON", "*.json")],
        )
        if not path:
            return
        self.profile.save(path)
        self.set_status(f"Профилът е записан: {path}")

    def open_batch_dialog(self) -> None:
        if self._busy:
            self.bell()
            return
        if not self.profile.items:
            messagebox.showinfo("Пакет", "Профилът е празен. Добавете настройки или заредете файл.")
            return
        from app.batch_dialog import BatchDialog
        existing = getattr(self, "_batch_dialog", None)
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.deiconify()
                    existing.lift()
                    return
            except Exception:
                pass
        self._batch_dialog = BatchDialog(self)

    # ----------------------------------------------------------- confirm
    def _confirm_long(self, text: str, title: str, parent=None, confirm_text: str = "Потвърди") -> bool:
        owner = parent or self
        dlg = ctk.CTkToplevel(owner)
        dlg.withdraw()
        dlg.title(title)
        dlg.geometry("720x520")
        dlg.minsize(480, 320)
        dlg.configure(fg_color=theme.FG_APP)
        dlg.transient(owner)
        result = {"ok": False}
        ctk.CTkLabel(dlg, text=title, anchor="w", font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=theme.TXT).pack(fill="x", padx=12, pady=(12, 4))
        box = ctk.CTkTextbox(dlg, wrap="none", fg_color=theme.FG_ENTRY, text_color=theme.TXT,
                             border_color=theme.BORDER, border_width=1)
        box.pack(fill="both", expand=True, padx=12, pady=4)
        box.insert("1.0", text)
        box.configure(state="disabled")
        bf = ctk.CTkFrame(dlg, fg_color=theme.FG_BAR)
        bf.pack(fill="x", padx=12, pady=(4, 12))

        def ok() -> None:
            result["ok"] = True
            dlg.destroy()

        def cancel() -> None:
            dlg.destroy()

        theme.secondary_button(bf, "Отказ", cancel, width=110).pack(side="right", padx=4, pady=8)
        theme.primary_button(bf, confirm_text, ok, width=140).pack(side="right", padx=4, pady=8)
        dlg.update_idletasks()
        try:
            px, py = owner.winfo_rootx(), owner.winfo_rooty()
            pw, ph = owner.winfo_width(), owner.winfo_height()
            dlg.geometry(f"720x520+{px + max(0, (pw - 720) // 2)}+{py + max(0, (ph - 520) // 2)}")
        except Exception:
            pass
        dlg.deiconify()
        dlg.lift()
        dlg.focus_force()
        dlg.grab_set()
        dlg.after(50, lambda: (dlg.lift(), dlg.focus_force()))
        self.wait_window(dlg)
        if parent is not None:
            try:
                parent.grab_set()
                parent.lift()
            except Exception:
                pass
        return result["ok"]


def _grab(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import mss
        with mss.mss() as sct:
            sct.shot(mon=-1, output=str(path))
        return
    except Exception:
        pass
    try:
        from PIL import ImageGrab
        ImageGrab.grab().save(path)
        return
    except Exception:
        pass
    import subprocess
    for cmd in (
        ["import", "-window", "root", str(path)],
        ["scrot", "-z", str(path)],
    ):
        try:
            if subprocess.call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
                return
        except FileNotFoundError:
            continue
    raise RuntimeError(f"could not capture screenshot to {path}")


def run_screenshots(app: SolisConfigApp, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    app.update()
    app.update_idletasks()
    app.geometry("1400x820+40+30")
    app.update()

    def shot(name: str) -> None:
        app.update()
        app.update_idletasks()
        app.after(80)
        app.update()
        _grab(dest / name)

    app.select_mode("settings")
    app.select_group("system")
    shot("01-settings-system.png")

    app.select_address(43068)
    shot("02-settings-grid-standard-dropdown.png")
    # open the CTk dropdown so the list is visible
    try:
        app.editor.enum_menu._clicked(None)  # type: ignore[attr-defined]
        app.update()
        shot("03-settings-grid-standard-open.png")
        try:
            app.editor.enum_menu._dropdown_menu.destroy()  # type: ignore[attr-defined]
        except Exception:
            pass
    except Exception:
        shot("03-settings-grid-standard-open.png")

    app.select_address(43110)
    shot("04-settings-storage-bits.png")

    app.select_address(43009)
    shot("05-settings-battery-bytes.png")

    app.select_address(43020)
    try:
        item = app.tree.bbox(str(43020), "name")
        if item:
            x, y, w, h = item
            rx, ry = app.tree.winfo_rootx(), app.tree.winfo_rooty()
            app.tip.show(
                tooltip_text(app.catalog.get(43020), raw=None, accuracy_mode=None),
                rx + x + w // 2, ry + y + h + 4,
            )
            app.update()
    except Exception:
        pass
    shot("06-settings-tooltip.png")
    app.tip.hide()

    app.select_mode("monitor")
    app.select_group("status")
    app.select_address(33095) if app.catalog.get(33095) else None
    shot("07-monitor-status.png")

    app.select_group("info")
    app.select_address(33004) if app.catalog.get(33004) else None
    shot("08-monitor-info.png")

    app.select_group("ac_grid")
    shot("09-monitor-ac.png")

    app.select_group("battery")
    shot("10-monitor-battery.png")


def main(argv: Optional[List[str]] = None) -> None:
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    dest = os.environ.get("SOLIS_SCREENSHOT_DIR")
    if "--screenshot" in args:
        i = args.index("--screenshot")
        dest = args[i + 1] if i + 1 < len(args) else dest
    theme.apply()
    app = SolisConfigApp()
    if dest:
        def go() -> None:
            try:
                run_screenshots(app, Path(dest))
            finally:
                app.destroy()
        app.after(700, go)
    app.mainloop()


if __name__ == "__main__":
    main()
