"""A simple Tkinter window: choose folders, start/stop monitoring, follow progress and errors.

All OCR work runs in the Monitor's background thread; the window only polls its
status twice a second, so the interface never freezes.
"""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from logging.handlers import QueueHandler
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import __version__
from .config import ConfigError, load_config, update_ini_values
from .logging_setup import make_formatter, setup_logging
from .monitor import COMPLETED, FAILED, PROCESSING, WAITING, Monitor

logger = logging.getLogger("pdf_ocr.gui")

POLL_MS = 500
MAX_LOG_LINES = 3000
PHASE_TEXT = {
    "starting": "Starting…",
    "scanning": "Running — scanning the source folder",
    "processing": "Running — processing",
    "idle": "Running — watching for new or changed PDFs",
    "stopping": "Stopping…",
    "stopped": "Stopped",
    "error": "Could not start (see the error message)",
}


def _open_in_explorer(path: Path) -> None:
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path)])


class App:
    def __init__(self, root: tk.Tk, config_path: Path) -> None:
        self.root = root
        self.config_path = config_path
        self.monitor: Monitor | None = None
        self.log_queue: queue.Queue = queue.Queue()
        self.queue_handler = QueueHandler(self.log_queue)
        self._iids: dict[str, str] = {}
        self._rows: dict[str, tuple[str, str]] = {}
        self._next_iid = 0
        self._closing = False
        self._error_shown = False
        self.log_dir: Path | None = None

        self.source_var = tk.StringVar()
        self.dest_var = tk.StringVar()
        self.lang_var = tk.StringVar(value="eng+ara")
        self.interval_var = tk.StringVar(value="15")
        self.status_var = tk.StringVar(value="Stopped")
        self.current_var = tk.StringVar(value="")
        self.count_vars = {s: tk.StringVar(value="0") for s in (WAITING, PROCESSING, COMPLETED, FAILED)}

        self._build_ui()
        self._load_initial_config()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(POLL_MS, self._poll)

    # ------------------------------------------------------------------ layout

    def _build_ui(self) -> None:
        self.root.title(f"PDF OCR {__version__}")
        self.root.geometry("1000x700")
        self.root.minsize(780, 520)

        main = ttk.Frame(self.root, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        box = ttk.LabelFrame(main, text="Folders and options", padding=8)
        box.pack(fill=tk.X)
        box.columnconfigure(1, weight=1)
        self.inputs: list[tk.Widget] = []
        for row, (label, var) in enumerate((("Source folder (scanned PDFs):", self.source_var),
                                            ("Destination folder (searchable PDFs):", self.dest_var))):
            ttk.Label(box, text=label).grid(row=row, column=0, sticky=tk.W, pady=2)
            entry = ttk.Entry(box, textvariable=var)
            entry.grid(row=row, column=1, sticky=tk.EW, padx=6, pady=2)
            browse = ttk.Button(box, text="Browse…", command=lambda v=var: self._browse(v))
            browse.grid(row=row, column=2, padx=2)
            ttk.Button(box, text="Open", command=lambda v=var: self._open_folder(v)).grid(row=row, column=3, padx=2)
            self.inputs += [entry, browse]

        options = ttk.Frame(box)
        options.grid(row=2, column=0, columnspan=4, sticky=tk.W, pady=(6, 0))
        ttk.Label(options, text="OCR languages:").pack(side=tk.LEFT)
        lang_entry = ttk.Entry(options, textvariable=self.lang_var, width=16)
        lang_entry.pack(side=tk.LEFT, padx=(6, 4))
        ttk.Label(options, text="(Tesseract codes joined with +, e.g. eng+ara)", foreground="#666").pack(side=tk.LEFT)
        ttk.Label(options, text="      Scan every (seconds):").pack(side=tk.LEFT)
        interval = ttk.Spinbox(options, from_=2, to=3600, textvariable=self.interval_var, width=6)
        interval.pack(side=tk.LEFT, padx=6)
        self.inputs += [lang_entry, interval]

        controls = ttk.Frame(main)
        controls.pack(fill=tk.X, pady=(10, 4))
        self.start_btn = ttk.Button(controls, text="▶ Start monitoring", command=self.start)
        self.start_btn.pack(side=tk.LEFT)
        self.stop_btn = ttk.Button(controls, text="■ Stop", command=self.stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=6)
        self.retry_btn = ttk.Button(controls, text="Retry failed", command=self.retry_failed, state=tk.DISABLED)
        self.retry_btn.pack(side=tk.LEFT)
        ttk.Button(controls, text="Open log folder", command=self._open_logs).pack(side=tk.LEFT, padx=6)
        ttk.Label(controls, textvariable=self.status_var, font=("Segoe UI", 10, "bold")).pack(side=tk.RIGHT)

        counts = ttk.Frame(main)
        counts.pack(fill=tk.X, pady=4)
        for status, title, colour in ((WAITING, "Waiting", "#8a6d00"), (PROCESSING, "Processing", "#0b57d0"),
                                      (COMPLETED, "Completed", "#1e7e34"), (FAILED, "Failed", "#b00020")):
            ttk.Label(counts, text=f"{title}:").pack(side=tk.LEFT)
            ttk.Label(counts, textvariable=self.count_vars[status], foreground=colour,
                      font=("Segoe UI", 10, "bold"), width=6).pack(side=tk.LEFT)

        self.progress = ttk.Progressbar(main, mode="determinate")
        self.progress.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(main, textvariable=self.current_var).pack(fill=tk.X, pady=(2, 6))

        notebook = ttk.Notebook(main)
        notebook.pack(fill=tk.BOTH, expand=True)

        files_tab = ttk.Frame(notebook)
        notebook.add(files_tab, text="Files")
        self.tree = ttk.Treeview(files_tab, columns=("status", "details"), show="tree headings")
        self.tree.heading("#0", text="File")
        self.tree.heading("status", text="Status")
        self.tree.heading("details", text="Details")
        self.tree.column("#0", width=280, stretch=False)
        self.tree.column("status", width=100, stretch=False)
        self.tree.column("details", width=560)
        for status, colour in ((FAILED, "#b00020"), (PROCESSING, "#0b57d0"), (WAITING, "#8a6d00"),
                               (COMPLETED, "#1e7e34")):
            self.tree.tag_configure(status, foreground=colour)
        scroll = ttk.Scrollbar(files_tab, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<Double-1>", self._show_row_details)

        log_tab = ttk.Frame(notebook)
        notebook.add(log_tab, text="Log")
        self.log_text = ScrolledText(log_tab, wrap=tk.WORD, font=("Consolas", 9), state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.tag_configure("ERROR", foreground="#b00020")
        self.log_text.tag_configure("CRITICAL", foreground="#b00020")
        self.log_text.tag_configure("WARNING", foreground="#a15c00")

    # ------------------------------------------------------------------ config

    def _load_initial_config(self) -> None:
        try:
            config = load_config(self.config_path)
        except ConfigError as exc:
            self._attach_fallback_logging()
            messagebox.showerror("Configuration error", f"{exc}\n\nFix config.ini or choose folders here.")
            return
        self.source_var.set(str(config.source_folder))
        self.dest_var.set(str(config.destination_folder))
        self.lang_var.set(config.ocr.languages)
        self.interval_var.set(str(config.scan_interval_seconds))
        try:
            setup_logging(config, console=False, extra_handlers=[self.queue_handler])
            self.log_dir = config.log_dir
        except ConfigError as exc:
            self._attach_fallback_logging()
            messagebox.showwarning("Logging", str(exc))

    def _attach_fallback_logging(self) -> None:
        root = logging.getLogger()
        if self.queue_handler not in root.handlers:
            self.queue_handler.setFormatter(make_formatter())
            root.addHandler(self.queue_handler)
            root.setLevel(logging.INFO)

    def _save_settings(self) -> bool:
        source = self.source_var.get().strip().strip('"')
        dest = self.dest_var.get().strip().strip('"')
        langs = self.lang_var.get().strip()
        if not source or not dest:
            messagebox.showerror("Folders", "Please choose both a source and a destination folder.")
            return False
        try:
            interval = int(self.interval_var.get())
            if interval < 2:
                raise ValueError
        except ValueError:
            messagebox.showerror("Scan interval", "The scan interval must be a whole number of at least 2 seconds.")
            return False
        try:
            update_ini_values(self.config_path, "folders", {"source_folder": source, "destination_folder": dest})
            update_ini_values(self.config_path, "ocr", {"languages": langs or "eng+ara"})
            update_ini_values(self.config_path, "monitor", {"scan_interval_seconds": str(interval)})
        except OSError as exc:
            messagebox.showerror("Configuration", f"Could not save the settings to {self.config_path}:\n{exc}")
            return False
        return True

    # ------------------------------------------------------------------ actions

    def start(self) -> None:
        if self.monitor is not None and self.monitor.is_running():
            return
        if not self._save_settings():
            return
        try:
            config = load_config(self.config_path)
            setup_logging(config, console=False, extra_handlers=[self.queue_handler])
        except ConfigError as exc:
            messagebox.showerror("Configuration error", str(exc))
            return
        self.log_dir = config.log_dir
        self.tree.delete(*self.tree.get_children())
        self._iids.clear()
        self._rows.clear()
        self._error_shown = False
        logger.info("PDF OCR %s (GUI) starting monitoring.", __version__)
        self.monitor = Monitor(config)
        self.monitor.start()
        self._set_running_ui(True)

    def stop(self) -> None:
        if self.monitor is not None:
            self.status_var.set(PHASE_TEXT["stopping"])
            self.stop_btn.configure(state=tk.DISABLED)
            self.monitor.stop()

    def retry_failed(self) -> None:
        if self.monitor is not None and self.monitor.is_running():
            self.monitor.request_retry_failed()

    def on_close(self) -> None:
        if self.monitor is not None and self.monitor.is_running():
            if not messagebox.askyesno(
                "Exit", "Monitoring is running. Stop it and exit?\n\n"
                        "A file being processed right now will be processed again next time."):
                return
            self._closing = True
            self.stop()
        else:
            self.root.destroy()

    def _set_running_ui(self, running: bool) -> None:
        for widget in self.inputs:
            widget.configure(state=tk.DISABLED if running else tk.NORMAL)
        self.start_btn.configure(state=tk.DISABLED if running else tk.NORMAL)
        self.stop_btn.configure(state=tk.NORMAL if running else tk.DISABLED)
        self.retry_btn.configure(state=tk.NORMAL if running else tk.DISABLED)

    def _browse(self, var: tk.StringVar) -> None:
        current = var.get().strip().strip('"')
        chosen = filedialog.askdirectory(initialdir=current if current and os.path.isdir(current) else None,
                                         mustexist=False)
        if chosen:
            var.set(os.path.normpath(chosen))

    def _open_folder(self, var: tk.StringVar) -> None:
        path = Path(var.get().strip().strip('"'))
        if not str(path) or not path.is_dir():
            messagebox.showerror("Open folder", f"The folder does not exist:\n{path}")
            return
        try:
            _open_in_explorer(path)
        except OSError as exc:
            messagebox.showerror("Open folder", str(exc))

    def _open_logs(self) -> None:
        if self.log_dir is None:
            messagebox.showinfo("Logs", "The log folder is not known yet (check config.ini).")
            return
        self.log_dir.mkdir(parents=True, exist_ok=True)
        _open_in_explorer(self.log_dir)

    def _show_row_details(self, _event) -> None:
        selected = self.tree.selection()
        if selected:
            item = self.tree.item(selected[0])
            status, details = item["values"][0], item["values"][1]
            messagebox.showinfo(item["text"], f"Status: {status}\n\n{details}")

    # ------------------------------------------------------------------ polling

    def _poll(self) -> None:
        try:
            self._drain_log()
            self._refresh_status()
        except Exception:
            logger.exception("GUI refresh failed")
        if self._closing and (self.monitor is None or not self.monitor.is_running()):
            self.root.destroy()
            return
        self.root.after(POLL_MS, self._poll)

    def _drain_log(self) -> None:
        records = []
        try:
            for _ in range(500):
                records.append(self.log_queue.get_nowait())
        except queue.Empty:
            pass
        if not records:
            return
        self.log_text.configure(state=tk.NORMAL)
        for record in records:
            self.log_text.insert(tk.END, f"{record.msg}\n", record.levelname)
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > MAX_LOG_LINES:
            self.log_text.delete("1.0", f"{lines - MAX_LOG_LINES}.0")
        self.log_text.configure(state=tk.DISABLED)
        self.log_text.see(tk.END)

    def _refresh_status(self) -> None:
        monitor = self.monitor
        if monitor is None:
            return
        snap = monitor.snapshot()
        for status, var in self.count_vars.items():
            var.set(str(snap.counts.get(status, 0)))

        for key, view in snap.files.items():
            row = (view.status, view.message)
            iid = self._iids.get(key)
            if iid is None:
                self._next_iid += 1
                iid = f"row{self._next_iid}"
                self._iids[key] = iid
                self.tree.insert("", tk.END, iid=iid, text=view.name,
                                 values=(view.status.capitalize(), view.message), tags=(view.status,))
            elif self._rows.get(key) != row:
                self.tree.item(iid, values=(view.status.capitalize(), view.message), tags=(view.status,))
            self._rows[key] = row
        for key in [k for k in self._iids if k not in snap.files]:
            self.tree.delete(self._iids.pop(key))
            self._rows.pop(key, None)

        if snap.batch_total:
            self.progress.configure(maximum=snap.batch_total, value=snap.batch_done)
        else:
            self.progress.configure(maximum=1, value=0)
        if snap.current_file:
            elapsed = int(time.time() - (snap.current_started or time.time()))
            self.current_var.set(
                f"Processing: {snap.current_file}   ({elapsed // 60:02d}:{elapsed % 60:02d})   "
                f"— file {snap.batch_done + 1} of {max(snap.batch_total, 1)} in this batch")
        else:
            self.current_var.set("")

        self.status_var.set(PHASE_TEXT.get(snap.phase, snap.phase))
        if not snap.running:
            self._set_running_ui(False)
            if snap.fatal_error and not self._error_shown:
                self._error_shown = True
                messagebox.showerror("Cannot start monitoring", snap.fatal_error)


def run_gui(config_path: Path) -> int:
    """Open the window and run the Tk event loop until it is closed."""
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # sharp text on high-DPI screens
        except Exception:
            pass

    def log_uncaught(exc_type, exc, tb) -> None:
        logger.critical("Unhandled error", exc_info=(exc_type, exc, tb))

    sys.excepthook = log_uncaught
    threading.excepthook = lambda args: log_uncaught(args.exc_type, args.exc_value, args.exc_traceback)

    root = tk.Tk()
    style = ttk.Style(root)
    if "vista" in style.theme_names():
        style.theme_use("vista")

    def report_tk_error(exc_type, exc, tb) -> None:
        log_uncaught(exc_type, exc, tb)
        messagebox.showerror("Unexpected error", f"{exc_type.__name__}: {exc}")

    root.report_callback_exception = report_tk_error
    App(root, config_path)
    root.mainloop()
    return 0
