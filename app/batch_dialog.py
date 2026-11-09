"""Apply a settings profile to multiple devices (CustomTkinter).

Linux/macOS CTkToplevel fix: withdraw → build → deiconify/lift. The worker
thread never touches Tk: it pushes events into a queue drained with after().
"""
from __future__ import annotations

import queue
import shutil
import threading
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import TYPE_CHECKING, Dict, List, Optional

import customtkinter as ctk

from app import theme
from app.appdata import load_settings, reports_dir, save_settings
from app.batch import (
    CANCELLED, DEFAULT_PARALLEL, DEFAULT_RETRIES, DEFAULT_TIMEOUT, DRY_RUN_OK,
    ERROR, MAX_PARALLEL, MAX_RETRIES, MISMATCH, OK, SKIPPED, BatchOptions,
    BatchRunner, BatchSummary, DeviceResult, default_report_name, validate_options,
    write_report_csv,
)
from app.busy import format_busy_text
from app.devices import (
    DEFAULT_PORT, DeviceListError, DeviceTarget, group_by_endpoint, load_device_file,
    parse_device_list, save_device_file,
)

if TYPE_CHECKING:  # pragma: no cover
    from app.gui import SolisConfigApp

_POLL_MS = 100
_STATUS_COLORS = {
    OK: theme.OK_GREEN,
    DRY_RUN_OK: theme.OK_GREEN,
    MISMATCH: ("#B35900", "#B35900"),
    ERROR: theme.DANGER,
    SKIPPED: theme.TXT_MUTED,
    CANCELLED: theme.TXT_MUTED,
}
_TABLE_COLS = (("IP", 130), ("Port", 50), ("ID", 36), ("SN", 170), ("Model", 70),
               ("Status", 110), ("Message", 420))

SETTINGS_KEY_LIST = "batch_device_list"
SETTINGS_KEY_VERIFY = "batch_verify"
SETTINGS_KEY_STOP = "batch_stop_on_error"
SETTINGS_KEY_PARALLEL = "batch_parallel"
SETTINGS_KEY_RETRIES = "batch_retries"
SETTINGS_KEY_TIMEOUT = "timeout"

HELP_TEXT = (
    "Един ред на група инвертори: IP[:порт] ID[,ID…] или диапазон — напр. "
    "„192.168.85.106 1-6“, „192.168.85.107:502 1,3,5“. # започва коментар. "
    "Различните IP:порт се обработват паралелно (по една TCP сесия); ID-тата "
    "на един и същ IP вървят едно след друго. Преди записа се чете серийният "
    "номер (33004–33019). „Само проверка“ чете профила и сравнява — БЕЗ запис. "
    "Timeout (0.5–60 s) важи за всички връзки и за главния прозорец.\n"
    "One line per group: IP[:port] IDs (1-6 or 1,3,5). Different IP:ports run "
    "in parallel (one TCP session each); IDs on the same IP run serially. "
    "“Verify only” reads and compares — NO writes."
)


def _int_setting(value, default: int, lo: int, hi: int) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return v if lo <= v <= hi else default


class _DeviceRow:
    def __init__(self, parent: ctk.CTkFrame, target: DeviceTarget) -> None:
        self.frame = ctk.CTkFrame(parent, fg_color=theme.FG_ROW)
        self.vars: Dict[str, ctk.StringVar] = {}
        values = {"IP": target.host, "Port": str(target.port), "ID": str(target.unit_id),
                  "SN": "", "Model": "", "Status": "waiting", "Message": ""}
        self.status_lbl: Optional[ctk.CTkLabel] = None
        for col, (title, width) in enumerate(_TABLE_COLS):
            var = ctk.StringVar(value=values[title])
            self.vars[title] = var
            lbl = ctk.CTkLabel(self.frame, textvariable=var, width=width, anchor="w",
                               text_color=theme.TXT, font=ctk.CTkFont(size=12))
            lbl.grid(row=0, column=col, padx=2, sticky="w")
            if title == "Status":
                self.status_lbl = lbl
        self.frame.grid_columnconfigure(len(_TABLE_COLS) - 1, weight=1)
        self.frame.pack(fill="x", pady=1)

    def update(self, r: DeviceResult) -> None:
        self.vars["SN"].set(r.sn or "")
        self.vars["Model"].set(r.model or "")
        self.vars["Status"].set(r.status_text())
        msg = r.message or ""
        self.vars["Message"].set(msg if len(msg) <= 90 else msg[:87] + "…")
        if self.status_lbl is not None:
            self.status_lbl.configure(text_color=_STATUS_COLORS.get(r.status, theme.TXT))


class BatchDialog(ctk.CTkToplevel):
    def __init__(self, app: "SolisConfigApp") -> None:
        super().__init__(app)
        self.withdraw()
        self.app = app
        self.title("Прилагане на профил към много устройства")
        self.geometry("1100x720")
        self.minsize(900, 560)
        self.configure(fg_color=theme.FG_APP)
        self.transient(app)

        self._events: "queue.Queue" = queue.Queue()
        self._cancel = threading.Event()
        self._running = False
        self._rows: List[_DeviceRow] = []
        self._summary: Optional[BatchSummary] = None
        self._report_path: Optional[Path] = None
        self._restore: Optional[tuple] = None
        self._items = list(app.profile.items)
        self._label = "Batch"

        settings = load_settings()
        self.verify_var = ctk.BooleanVar(value=bool(settings.get(SETTINGS_KEY_VERIFY, True)))
        self.stop_var = ctk.BooleanVar(value=bool(settings.get(SETTINGS_KEY_STOP, False)))
        self.parallel_var = ctk.StringVar(value=str(_int_setting(
            settings.get(SETTINGS_KEY_PARALLEL), DEFAULT_PARALLEL, 1, MAX_PARALLEL)))
        self.retries_var = ctk.StringVar(value=str(_int_setting(
            settings.get(SETTINGS_KEY_RETRIES), DEFAULT_RETRIES, 0, MAX_RETRIES)))
        from app.batch import timeout_setting
        self.timeout_var = ctk.StringVar(value=f"{timeout_setting(settings.get(SETTINGS_KEY_TIMEOUT), getattr(app.client, 'timeout', DEFAULT_TIMEOUT)):g}")

        self._build(settings.get(SETTINGS_KEY_LIST))
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.update_idletasks()
        try:
            px, py = app.winfo_rootx(), app.winfo_rooty()
            pw, ph = app.winfo_width(), app.winfo_height()
            self.geometry(f"1100x720+{px + max(0, (pw - 1100) // 2)}+{py + max(0, (ph - 720) // 2)}")
        except Exception:
            pass
        self.deiconify()
        self.lift()
        self.focus_force()
        self.after(50, lambda: (self.lift(), self.focus_force()))

    def _btn(self, parent, text, cmd, primary=False, width=110):
        return (theme.primary_button if primary else theme.secondary_button)(parent, text, cmd, width=width)

    def _build(self, saved_list: Optional[str]) -> None:
        n = len(self._items)
        ctk.CTkLabel(self, text=f"Профил „{self.app.profile.name}“ — {n} настройк{'а' if n == 1 else 'и'}",
                     anchor="w", font=ctk.CTkFont(size=15, weight="bold"),
                     text_color=theme.TXT).pack(fill="x", padx=12, pady=(12, 0))
        ctk.CTkLabel(self, text=HELP_TEXT, anchor="w", justify="left", wraplength=1060,
                     font=ctk.CTkFont(size=12), text_color=theme.TXT_MUTED).pack(fill="x", padx=12, pady=(2, 6))

        top = ctk.CTkFrame(self, fg_color=theme.FG_BAR)
        top.pack(fill="x", padx=12, pady=2)
        self.list_box = ctk.CTkTextbox(
            top, height=130, wrap="none", fg_color=theme.FG_ENTRY, text_color=theme.TXT,
            border_color=theme.BORDER, border_width=1, font=ctk.CTkFont(family="monospace", size=13),
        )
        self.list_box.pack(side="left", fill="both", expand=True, padx=(6, 4), pady=6)
        self.list_box.insert("1.0", saved_list if saved_list and saved_list.strip() else self._prefill())

        side = ctk.CTkFrame(top, fg_color=theme.FG_BAR)
        side.pack(side="left", fill="y", padx=(4, 6), pady=6)
        self.load_btn = self._btn(side, "Зареди…", self._load)
        self.load_btn.pack(pady=2)
        self.save_btn = self._btn(side, "Запиши списъка…", self._save_list, width=140)
        self.save_btn.pack(pady=2)
        self.check_btn = self._btn(side, "Провери списъка", self._check_list, width=140)
        self.check_btn.pack(pady=2)

        opts = ctk.CTkFrame(self, fg_color=theme.FG_BAR)
        opts.pack(fill="x", padx=12, pady=4)
        self.verify_cb = ctk.CTkCheckBox(opts, text="Проверка с обратно четене", variable=self.verify_var,
                                         text_color=theme.TXT)
        self.verify_cb.pack(side="left", padx=(8, 4), pady=6)
        self.stop_cb = ctk.CTkCheckBox(opts, text="Спри при първа грешка", variable=self.stop_var,
                                       text_color=theme.TXT)
        self.stop_cb.pack(side="left", padx=(12, 4))
        ctk.CTkLabel(opts, text="Паралелни връзки", text_color=theme.TXT).pack(side="left", padx=(16, 2))
        self.parallel_entry = ctk.CTkEntry(opts, width=44, textvariable=self.parallel_var,
                                           fg_color=theme.FG_ENTRY, text_color=theme.TXT, border_color=theme.BORDER)
        self.parallel_entry.pack(side="left", padx=2)
        ctk.CTkLabel(opts, text="Повторения", text_color=theme.TXT).pack(side="left", padx=(12, 2))
        self.retries_entry = ctk.CTkEntry(opts, width=36, textvariable=self.retries_var,
                                          fg_color=theme.FG_ENTRY, text_color=theme.TXT, border_color=theme.BORDER)
        self.retries_entry.pack(side="left", padx=2)
        ctk.CTkLabel(opts, text="Timeout (s)", text_color=theme.TXT).pack(side="left", padx=(12, 2))
        self.timeout_entry = ctk.CTkEntry(opts, width=44, textvariable=self.timeout_var,
                                          fg_color=theme.FG_ENTRY, text_color=theme.TXT, border_color=theme.BORDER)
        self.timeout_entry.pack(side="left", padx=2)
        dry = " · DRY-RUN е включен в главния прозорец" if self.app.dry_run.get() else ""
        ctk.CTkLabel(opts, text=dry, text_color=theme.TXT_MUTED, font=ctk.CTkFont(size=12)).pack(side="left", padx=12)

        tbl = ctk.CTkFrame(self, fg_color=theme.FG_TAB)
        tbl.pack(fill="both", expand=True, padx=12, pady=4)
        hdr = ctk.CTkFrame(tbl, fg_color=theme.FG_HDR)
        hdr.pack(fill="x", padx=(0, 16))
        for col, (title, width) in enumerate(_TABLE_COLS):
            ctk.CTkLabel(hdr, text=title, width=width, anchor="w", text_color=theme.TXT,
                         font=ctk.CTkFont(weight="bold")).grid(row=0, column=col, padx=2, pady=2, sticky="w")
        hdr.grid_columnconfigure(len(_TABLE_COLS) - 1, weight=1)
        self.table = ctk.CTkScrollableFrame(tbl, fg_color=theme.FG_TAB)
        self.table.pack(fill="both", expand=True)

        prog = ctk.CTkFrame(self, fg_color=theme.FG_BAR)
        prog.pack(fill="x", padx=12, pady=2)
        self.progress = ctk.CTkProgressBar(prog, height=12, mode="determinate",
                                           fg_color=theme.PROG_TRACK, progress_color=theme.PROG_FILL, border_width=0)
        self.progress.set(0)
        self.progress.pack(fill="x", padx=8, pady=(8, 2))
        self.status_lbl = ctk.CTkLabel(prog, text="Готово. Проверете списъка, после Старт.",
                                       anchor="w", text_color=theme.TXT)
        self.status_lbl.pack(fill="x", padx=8, pady=(0, 6))

        bf = ctk.CTkFrame(self, fg_color=theme.FG_BAR)
        bf.pack(fill="x", padx=12, pady=(4, 12))
        self.close_btn = self._btn(bf, "Затвори", self._on_close)
        self.close_btn.pack(side="right", padx=4, pady=8)
        self.report_btn = self._btn(bf, "Запиши отчета…", self._save_report, width=140)
        self.report_btn.pack(side="right", padx=4, pady=8)
        self.report_btn.configure(state="disabled")
        self.cancel_btn = self._btn(bf, "Отказ", self._cancel_run)
        self.cancel_btn.pack(side="right", padx=4, pady=8)
        self.cancel_btn.configure(state="disabled")
        self.start_btn = self._btn(bf, "Старт…", self._start, primary=True, width=130)
        self.start_btn.pack(side="right", padx=4, pady=8)
        self.verify_only_btn = self._btn(bf, "Само проверка", self._start_verify_only, width=150)
        self.verify_only_btn.pack(side="right", padx=4, pady=8)
        self._input_widgets = [self.timeout_entry, self.verify_only_btn, self.load_btn, self.save_btn,
                               self.check_btn, self.verify_cb, self.stop_cb, self.parallel_entry,
                               self.retries_entry, self.start_btn, self.close_btn]
        self._check_list(quiet=True)

    def _prefill(self) -> str:
        try:
            host = self.app.host_entry.get().strip()
            port = int(self.app.port_entry.get().strip() or DEFAULT_PORT)
            uid = int(self.app.unit_entry.get().strip() or 1)
        except ValueError:
            host, port, uid = self.app.client.host, self.app.client.port, self.app.client.unit_id
        ep = host if port == DEFAULT_PORT else f"{host}:{port}"
        return f"# IP[:port] ID[,ID...] или диапазон, напр. 192.168.85.106 1-6\n{ep} {uid}\n"

    def _list_text(self) -> str:
        return self.list_box.get("1.0", "end").rstrip("\n") + "\n"

    def _set_rows(self, devices: List[DeviceTarget]) -> None:
        for r in self._rows:
            r.frame.destroy()
        self._rows = [_DeviceRow(self.table, d) for d in devices]

    def _check_list(self, quiet: bool = False):
        devices, issues = parse_device_list(self._list_text())
        self._set_rows(devices)
        n_ep = len(group_by_endpoint(devices))
        if issues:
            msg = f"{len(issues)} проблем(а) в списъка:\n" + "\n".join(map(str, issues[:15]))
            self.status_lbl.configure(text=f"Списък: {len(devices)} OK, {len(issues)} проблем(а)")
            if not quiet:
                messagebox.showerror("Списък", msg, parent=self)
        else:
            self.status_lbl.configure(text=f"Списък: {len(devices)} устройств(а) на {n_ep} IP/порт(а).")
        return devices, issues

    def _load(self) -> None:
        path = filedialog.askopenfilename(
            parent=self, filetypes=[("Device list", "*.txt *.csv"), ("All files", "*.*")])
        if not path:
            return
        try:
            text = load_device_file(path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Зареждане", str(exc), parent=self)
            return
        self.list_box.delete("1.0", "end")
        self.list_box.insert("1.0", text)
        self._check_list()

    def _save_list(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self, defaultextension=".txt", initialfile="devices.txt",
            filetypes=[("Text", "*.txt"), ("CSV (host,port,unit_id)", "*.csv")])
        if not path:
            return
        try:
            save_device_file(path, self._list_text())
        except (DeviceListError, OSError) as exc:
            messagebox.showerror("Запис", str(exc), parent=self)
            return
        self.status_lbl.configure(text=f"Списъкът е записан: {path}")

    def _read_options(self):
        if self._running or self.app._busy:
            self.bell()
            return None
        devices, issues = self._check_list()
        if issues or not devices:
            if not devices and not issues:
                messagebox.showwarning("Списък", "Списъкът е празен.", parent=self)
            return None
        err = validate_options(self.parallel_var.get(), self.retries_var.get(), self.timeout_var.get())
        if isinstance(err, str):
            messagebox.showerror("Опции", err, parent=self)
            return None
        parallel, retries, timeout = err
        self._items = list(self.app.profile.items)
        verify, stop = bool(self.verify_var.get()), bool(self.stop_var.get())
        save_settings({SETTINGS_KEY_LIST: self._list_text(), SETTINGS_KEY_VERIFY: verify,
                       SETTINGS_KEY_STOP: stop, SETTINGS_KEY_PARALLEL: parallel,
                       SETTINGS_KEY_RETRIES: retries, SETTINGS_KEY_TIMEOUT: timeout})
        self.app.client.timeout = timeout
        return devices, parallel, retries, timeout, verify, stop

    def _start_verify_only(self) -> None:
        opt = self._read_options()
        if opt is None:
            return
        devices, parallel, retries, timeout, _v, stop = opt
        self._launch(devices, BatchOptions(verify_only=True, stop_on_error=stop, dry_run=False,
                                           timeout=timeout, parallel=parallel, retries=retries),
                     "Само проверка")

    def _start(self) -> None:
        opt = self._read_options()
        if opt is None:
            return
        devices, parallel, retries, timeout, verify, stop = opt
        dry = bool(self.app.dry_run.get())
        groups = group_by_endpoint(devices)
        workers = min(parallel, len(groups))
        lines = [f"{h}:{p}  IDs {', '.join(str(d.unit_id) for d in ds)}" for (h, p), ds in groups]
        n = len(self._items)
        head = (
            ("DRY-RUN: само четене и сравнение. НЯМА да се изпращат записи.\n"
             if dry else
             f"⚠ ЩЕ БЪДАТ ЗАПИСАНИ {n} РЕГИСТЪРА ВЪВ ВСЕКИ ОТ {len(devices)} ИНВЕРТОРА "
             f"({n * len(devices)} записа общо).\n")
            + f"\nПрофил „{self.app.profile.name}“ · {n} настройки\n"
            f"Проверка с обратно четене: {'да' if verify else 'не'} · Спри при грешка: {'да' if stop else 'не'}"
            f" · Повторения: {retries} ({retries + 1} опита)\n"
            f"{len(groups)} IP/порт групи, до {workers} паралелно; една TCP сесия на IP/порт.\n\n"
            "Устройства:\n  " + "\n  ".join(lines) + "\n\nНастройки (ред на запис):\n"
        )
        regs = "\n".join(f"  {it.address}  {it.name}: {it.text or it.words}" for it in self._items)
        title = "Потвърдете (dry-run)" if dry else f"Потвърдете ЗАПИС към {len(devices)} устройства"
        if not self.app._confirm_long(head + regs, title=title, parent=self,
                                      confirm_text="Dry-run" if dry else "ЗАПИШИ към всички"):
            return
        self._launch(devices, BatchOptions(verify=verify, stop_on_error=stop, dry_run=dry,
                                           timeout=timeout, parallel=parallel, retries=retries),
                     "Пакетно прилагане")

    def _launch(self, devices, options: BatchOptions, label: str) -> None:
        self._label = label
        self._restore = None
        if self.app.client.connected:
            c = self.app.client
            self._restore = (c.host, c.port, c.unit_id)
            self.app._disconnect_main()
        self._set_rows(devices)
        self._cancel.clear()
        self._running = True
        self._summary = None
        self._report_path = None
        for w in self._input_widgets:
            w.configure(state="disabled")
        self.list_box.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.report_btn.configure(state="disabled")
        self.app._begin_busy(label)
        runner = BatchRunner(
            devices, self._items, options=options,
            on_update=lambda i, r: self._events.put(("row", i, r.status_text(), r)),
            on_progress=lambda d, t, s: self.app._report_progress(d, t, s),
            cancel=self._cancel,
        )

        def worker() -> None:
            try:
                self._events.put(("done", runner.run()))
            except Exception as exc:  # noqa: BLE001
                self._events.put(("crash", str(exc), runner))

        threading.Thread(target=worker, daemon=True).start()
        self.after(_POLL_MS, self._poll)

    def _poll(self) -> None:
        try:
            while True:
                ev = self._events.get_nowait()
                if ev[0] == "row":
                    _, i, _st, r = ev
                    if 0 <= i < len(self._rows):
                        self._rows[i].update(r)
                elif ev[0] == "done":
                    self._finish(ev[1])
                    return
                elif ev[0] == "crash":
                    runner = ev[2]
                    summ = BatchSummary(runner.results, self._cancel.is_set(), False, datetime.now(), 0.0)
                    for i, r in enumerate(runner.results):
                        if i < len(self._rows):
                            self._rows[i].update(r)
                    messagebox.showerror("Пакет", f"Неочаквана грешка: {ev[1]}", parent=self)
                    self._finish(summ)
                    return
        except queue.Empty:
            pass
        snap = self.app._progress.snapshot()
        if snap.fraction is not None:
            self.progress.set(snap.fraction)
        self.status_lbl.configure(text=format_busy_text(snap))
        self.after(_POLL_MS, self._poll)

    def _cancel_run(self) -> None:
        if self._running:
            self._cancel.set()
            self.cancel_btn.configure(state="disabled")
            self.status_lbl.configure(text="Отказ след текущия запис на регистър…")

    def _finish(self, summary: BatchSummary) -> None:
        self._running = False
        self._summary = summary
        for i, r in enumerate(summary.results):
            if i < len(self._rows):
                self._rows[i].update(r)
        self.progress.set(1.0)
        try:
            self._report_path = write_report_csv(
                reports_dir() / default_report_name(summary.started_at, summary.mode), summary,
                profile_name=self.app.profile.name, items=self._items)
            saved = f" · отчет: {self._report_path}"
        except OSError as exc:
            saved = f" · отчетът НЕ е записан ({exc})"
        text = summary.text()
        self.status_lbl.configure(text=text + saved)
        self.app.set_status(f"{self._label}: {text}")
        self.app._clear_busy()
        for w in self._input_widgets:
            w.configure(state="normal")
        self.list_box.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        self.report_btn.configure(state="normal")
        restore = self._restore
        self._restore = None
        if restore:
            host, port, uid = restore
            self.app.client.host, self.app.client.port, self.app.client.unit_id = host, port, uid
            self.app.after(200, self.app.toggle_connect)
        bad = summary.count(ERROR) + summary.count(MISMATCH)
        lines = [text, ""]
        for r in summary.sorted_results():
            if r.status not in (OK, DRY_RUN_OK) or r.retries_used:
                lines.append(f"{r.target.label()}: {r.status} — {r.message}")
        if self._report_path:
            lines += ["", f"Отчет: {self._report_path}"]
        show = messagebox.showwarning if bad or summary.cancelled else messagebox.showinfo
        title = "Проверката приключи" if summary.mode == "verify-only" else "Пакетът приключи"
        show(title, "\n".join(lines[:40]), parent=self)

    def _save_report(self) -> None:
        if not self._summary:
            return
        path = filedialog.asksaveasfilename(
            parent=self, defaultextension=".csv",
            initialfile=default_report_name(self._summary.started_at, self._summary.mode),
            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        try:
            if self._report_path and self._report_path.exists():
                shutil.copyfile(self._report_path, path)
            else:
                write_report_csv(path, self._summary, profile_name=self.app.profile.name, items=self._items)
        except OSError as exc:
            messagebox.showerror("Отчет", str(exc), parent=self)
            return
        self.status_lbl.configure(text=f"Отчетът е записан: {path}")

    def _on_close(self) -> None:
        if self._running:
            if messagebox.askyesno("Пакетът работи",
                                   "Пакетът все още работи. Да се отмени след текущия запис?",
                                   parent=self):
                self._cancel_run()
            return
        updates = {SETTINGS_KEY_LIST: self._list_text(),
                   SETTINGS_KEY_VERIFY: bool(self.verify_var.get()),
                   SETTINGS_KEY_STOP: bool(self.stop_var.get())}
        for key, var, lo, hi in ((SETTINGS_KEY_PARALLEL, self.parallel_var, 1, MAX_PARALLEL),
                                 (SETTINGS_KEY_RETRIES, self.retries_var, 0, MAX_RETRIES)):
            try:
                v = int(var.get().strip())
                if lo <= v <= hi:
                    updates[key] = v
            except ValueError:
                pass
        save_settings(updates)
        self.app._batch_dialog = None
        self.destroy()
