"""
The launcher window: start the engine, open its console, run the previz.

    python -m launcher          # or double-click kLights.pyw

A thin layer of Tk over launcher/core.py. Nothing slow runs on the Tk thread:
probing the engine, listing processes, loading a rig and the previz build all
happen on worker threads that post results to one queue, which the window
drains every 150 ms. A launcher that freezes while an engine starts looks
exactly like one that has crashed.
"""

from __future__ import annotations

import codecs
import os
import queue
import subprocess
import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import core

GREEN, AMBER, RED, GREY = "#1a7f37", "#9a6700", "#cf222e", "#57606a"
LOG_LINES = 3000
ARTNET_CHOICES = (
    "127.0.0.1",
    "255.255.255.255",
    "127.0.0.1,127.0.0.1:6455",
    "off",
)
BIND_CHOICES = {
    "0.0.0.0": "every network (phones can connect)",
    "127.0.0.1": "this computer only",
}


class LogView(ttk.Frame):
    """Read-only, follows the end unless scrolled away from it."""

    def __init__(self, master):
        super().__init__(master)
        self.text = tk.Text(self, wrap="none", height=10, font=("Consolas", 9),
                            state="disabled", relief="flat", borderwidth=4)
        ys = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        xs = ttk.Scrollbar(self, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

    def append(self, chunk: str) -> None:
        if not chunk:
            return
        following = self.text.yview()[1] >= 0.999
        self.text.configure(state="normal")
        self.text.insert("end", chunk)
        lines = int(self.text.index("end-1c").split(".")[0])
        if lines > LOG_LINES:
            self.text.delete("1.0", f"{lines - LOG_LINES}.0")
        self.text.configure(state="disabled")
        if following:
            self.text.see("end")

    def clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")


class Launcher:
    def __init__(self, root: tk.Tk, state_dir: Path = core.STATE_DIR):
        self.root = root
        self.state_dir = state_dir
        self.settings = core.load_settings(state_dir)
        self.engine = core.EngineHandle.resume(self.settings.engine)
        self.settings.engine = self.engine.record if self.engine else None
        self.stopping_since: Optional[float] = None
        self.last_exit: Optional[int] = None

        self.previz: Optional[subprocess.Popen] = None
        self.previz_found: list[int] = []
        self.build: Optional[subprocess.Popen] = None
        self.freshness = ("missing", "")

        self.probe = core.Probe("down")
        self.probe_port = 0
        self._probe_target = self.settings.port      # read by the poller thread
        self.event_info: Optional[core.EventInfo] = None
        self.lan: Optional[str] = None
        self.lock = ""
        self._lock_event = Path(self.settings.event)      # read by the poller thread
        self._info_generation = 0

        self.q: queue.Queue = queue.Queue()
        self._closing = threading.Event()
        self._wake = threading.Event()

        self._log_path: Optional[Path] = None
        self._log_pos = 0
        self._log_decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

        self._events: dict[str, Path] = {}
        self._build_ui()
        self._load_event_list()
        self._event_changed()
        threading.Thread(target=self._poll_loop, name="launcher-poll", daemon=True).start()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.after(150, self._pump)

    # -- layout ------------------------------------------------------------

    def _build_ui(self) -> None:
        root = self.root
        root.title("kLights")
        outer = ttk.Frame(root, padding=10)
        outer.grid(row=0, column=0, sticky="nsew")
        root.rowconfigure(0, weight=1)
        root.columnconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)

        # Event
        ev = ttk.LabelFrame(outer, text="Event", padding=8)
        ev.grid(row=0, column=0, sticky="ew")
        ev.columnconfigure(0, weight=1)
        self.event_var = tk.StringVar()
        self.event_box = ttk.Combobox(ev, textvariable=self.event_var)
        self.event_box.grid(row=0, column=0, sticky="ew")
        self.event_box.bind("<<ComboboxSelected>>", lambda e: self._event_changed())
        self.event_box.bind("<FocusOut>", lambda e: self._event_changed())
        self.event_box.bind("<Return>", lambda e: self._event_changed())
        self.browse_btn = ttk.Button(ev, text="Browse...", command=self._browse)
        self.browse_btn.grid(row=0, column=1, padx=(6, 0))
        self.event_label = ttk.Label(ev, text="", foreground=GREY)
        self.event_label.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.lock_label = ttk.Label(ev, text="", foreground=AMBER)
        self.lock_label.grid(row=2, column=0, columnspan=2, sticky="w")

        # Engine
        en = ttk.LabelFrame(outer, text="Engine", padding=8)
        en.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        en.columnconfigure(3, weight=1)
        ttk.Label(en, text="Port").grid(row=0, column=0, sticky="w")
        self.port_var = tk.StringVar(value=str(self.settings.port))
        self.port_box = ttk.Spinbox(en, from_=1024, to=65535, width=7, textvariable=self.port_var)
        self.port_box.grid(row=0, column=1, sticky="w", padx=(6, 16))
        self.port_var.trace_add("write", lambda *a: self._settings_changed())
        ttk.Label(en, text="Art-Net to").grid(row=0, column=2, sticky="e")
        self.artnet_var = tk.StringVar(value=self.settings.artnet or "off")
        self.artnet_box = ttk.Combobox(en, textvariable=self.artnet_var, values=ARTNET_CHOICES)
        self.artnet_box.grid(row=0, column=3, sticky="ew", padx=(6, 0))
        self.artnet_var.trace_add("write", lambda *a: self._settings_changed())

        ttk.Label(en, text="Console on").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.bind_var = tk.StringVar(value=BIND_CHOICES.get(self.settings.bind, self.settings.bind))
        self.bind_box = ttk.Combobox(en, textvariable=self.bind_var, state="readonly",
                                     values=list(BIND_CHOICES.values()), width=34)
        self.bind_box.grid(row=1, column=1, columnspan=2, sticky="w", padx=(6, 0), pady=(6, 0))
        self.bind_box.bind("<<ComboboxSelected>>", lambda e: self._settings_changed())
        self.token_var = tk.BooleanVar(value=self.settings.use_token)
        self.token_chk = ttk.Checkbutton(en, text="phones need the link's token to control",
                                         variable=self.token_var, command=self._settings_changed)
        self.token_chk.grid(row=1, column=3, sticky="w", padx=(6, 0), pady=(6, 0))
        ttk.Label(en, text="Extra flags").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.extra_var = tk.StringVar(value=self.settings.extra_args)
        self.extra_entry = ttk.Entry(en, textvariable=self.extra_var)
        self.extra_entry.grid(row=2, column=1, columnspan=3, sticky="ew", padx=(6, 0), pady=(6, 0))
        self.extra_var.trace_add("write", lambda *a: self._settings_changed())

        row = ttk.Frame(en)
        row.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        row.columnconfigure(2, weight=1)
        self.start_btn = ttk.Button(row, text="Start engine", command=self._start_engine)
        self.start_btn.grid(row=0, column=0)
        self.stop_btn = ttk.Button(row, text="Stop", command=self._stop_engine)
        self.stop_btn.grid(row=0, column=1, padx=(6, 0))
        self.engine_status = ttk.Label(row, text="")
        self.engine_status.grid(row=0, column=2, sticky="w", padx=(12, 0))
        self.artnet_problem = ttk.Label(en, text="", foreground=RED)
        self.artnet_problem.grid(row=4, column=0, columnspan=4, sticky="w")

        # Console
        co = ttk.LabelFrame(outer, text="Console", padding=8)
        co.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        co.columnconfigure(3, weight=1)
        self.console_btn = ttk.Button(co, text="Open console", command=lambda: self._open_console(None))
        self.console_btn.grid(row=0, column=0)
        self.setup_btn = ttk.Button(co, text="Open Setup", command=lambda: self._open_console("setup"))
        self.setup_btn.grid(row=0, column=1, padx=(6, 0))
        self.copy_btn = ttk.Button(co, text="Copy phone link", command=self._copy_link)
        self.copy_btn.grid(row=0, column=2, padx=(6, 0))
        self.link_var = tk.StringVar()
        self.link_entry = ttk.Entry(co, textvariable=self.link_var, state="readonly")
        self.link_entry.grid(row=0, column=3, sticky="ew", padx=(12, 0))
        self.console_note = ttk.Label(co, text="", foreground=GREY)
        self.console_note.grid(row=1, column=0, columnspan=4, sticky="w", pady=(4, 0))

        # Previz
        pv = ttk.LabelFrame(outer, text="Previz", padding=8)
        pv.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        pv.columnconfigure(5, weight=1)
        self.launch_btn = ttk.Button(pv, text="Launch previz", command=self._launch_previz)
        self.launch_btn.grid(row=0, column=0)
        self.close_btn = ttk.Button(pv, text="Close previz", command=self._close_previz)
        self.close_btn.grid(row=0, column=1, padx=(6, 0))
        self.build_btn = ttk.Button(pv, text="Build previz...", command=self._build_previz)
        self.build_btn.grid(row=0, column=2, padx=(6, 0))
        ttk.Label(pv, text="Art-Net port").grid(row=0, column=3, padx=(16, 0))
        self.pport_var = tk.StringVar(value=str(self.settings.previz_port))
        ttk.Spinbox(pv, from_=1024, to=65535, width=6, textvariable=self.pport_var).grid(
            row=0, column=4, sticky="w", padx=(6, 0))
        self.pport_var.trace_add("write", lambda *a: self._settings_changed())
        self.windowed_var = tk.BooleanVar(value=self.settings.previz_windowed)
        ttk.Checkbutton(pv, text="in a window", variable=self.windowed_var,
                        command=self._settings_changed).grid(row=0, column=5, sticky="w", padx=(12, 0))
        self.previz_status = ttk.Label(pv, text="")
        self.previz_status.grid(row=1, column=0, columnspan=6, sticky="w", pady=(6, 0))
        self.feed_warning = ttk.Label(pv, text="", foreground=AMBER)
        self.feed_warning.grid(row=2, column=0, columnspan=6, sticky="w")

        # Logs
        self.tabs = ttk.Notebook(outer)
        self.tabs.grid(row=4, column=0, sticky="nsew", pady=(10, 0))
        outer.rowconfigure(4, weight=1)
        self.engine_log = LogView(self.tabs)
        self.build_log = LogView(self.tabs)
        self.tabs.add(self.engine_log, text="Engine log")
        self.tabs.add(self.build_log, text="Previz build")

        # Wrap status text to the width it actually has. A fixed wraplength is
        # in pixels, so on a scaled display it wraps a sentence at half the
        # window -- and a status line is what someone reads at a glance.
        for label in (self.lock_label, self.artnet_problem, self.console_note,
                      self.previz_status, self.feed_warning):
            self._wrap_to(label, label.master)
        self._wrap_to(self.engine_status, row, reserve=lambda: self.stop_btn.winfo_x()
                      + self.stop_btn.winfo_width() + 24)

    @staticmethod
    def _wrap_to(label: ttk.Label, frame: tk.Misc, reserve=lambda: 0) -> None:
        frame.bind("<Configure>", lambda e: label.configure(
            wraplength=max(200, e.width - reserve() - 16)), add="+")

    # -- settings ----------------------------------------------------------

    def _load_event_list(self) -> None:
        self._events = {d.name: d for d in core.find_events()}
        self.event_box.configure(values=list(self._events))
        current = Path(self.settings.event)
        self.event_var.set(current.name if self._events.get(current.name) == current else str(current))

    def _event_path(self) -> Path:
        text = self.event_var.get().strip()
        return self._events.get(text, Path(text))

    def _browse(self) -> None:
        chosen = filedialog.askdirectory(title="An event folder (it has a rig.json)",
                                         initialdir=str(core.EVENTS))
        if chosen:
            path = Path(chosen)
            self.event_var.set(path.name if self._events.get(path.name) == path else str(path))
            self._event_changed()

    def _event_changed(self) -> None:
        path = self._event_path()
        self.settings.event = str(path)
        self._lock_event = path
        self._save()
        self._info_generation += 1
        generation = self._info_generation
        self.event_label.configure(text="reading the rig...", foreground=GREY)

        def work():
            self.q.put(("event", generation, core.describe_event(path)))
        threading.Thread(target=work, daemon=True).start()

    def _settings_changed(self) -> None:
        s = self.settings
        try:
            s.port = int(self.port_var.get())
        except ValueError:
            pass
        artnet = self.artnet_var.get().strip()
        s.artnet = "" if artnet.lower() == "off" else artnet
        bind = self.bind_var.get()
        s.bind = next((k for k, v in BIND_CHOICES.items() if v == bind), bind)
        s.use_token = bool(self.token_var.get())
        s.extra_args = self.extra_var.get()
        try:
            s.previz_port = int(self.pport_var.get())
        except ValueError:
            pass
        s.previz_windowed = bool(self.windowed_var.get())
        if not self._ours_alive():
            self._probe_target = s.port
            self._wake.set()
        self._save()

    def _save(self) -> None:
        try:
            core.save_settings(self.settings, self.state_dir)
        except OSError:
            pass

    # -- background --------------------------------------------------------

    def _poll_loop(self) -> None:
        """Once a second: who answers on the port, and is a previz running.
        The only place either is asked, so the window never blocks on them."""
        last_slow = 0.0
        while not self._closing.is_set():
            port = self._probe_target
            if port:
                self.q.put(("probe", port, core.probe(port)))
            event = self._lock_event
            self.q.put(("lock", event, core.read_lock(event)))
            if time.monotonic() - last_slow > 2.5:
                last_slow = time.monotonic()
                self.q.put(("previz", core.previz_pids(), core.previz_freshness()))
                self.q.put(("lan", core.lan_address()))
            self._wake.wait(1.0)
            self._wake.clear()

    def _pump(self) -> None:
        try:
            while True:
                self._handle(self.q.get_nowait())
        except queue.Empty:
            pass
        self._tail_engine_log()
        self._render()
        if not self._closing.is_set():
            self.root.after(150, self._pump)

    def _handle(self, item) -> None:
        kind = item[0]
        if kind == "probe":
            _, port, result = item
            self.probe_port, self.probe = port, result
        elif kind == "previz":
            _, pids, freshness = item
            self.previz_found, self.freshness = pids, freshness
        elif kind == "event":
            _, generation, info = item
            if generation == self._info_generation:
                self.event_info = info
                self.lock = info.lock
        elif kind == "lock":
            if item[1] == self._lock_event:
                self.lock = item[2]
        elif kind == "lan":
            self.lan = item[1]
        elif kind == "build":
            self.build_log.append(item[1])
        elif kind == "build_done":
            code = item[1]
            self.build = None
            self.build_log.append(f"\n[launcher] build {'finished' if code == 0 else f'FAILED (exit {code})'}\n")
            self.q.put(("previz", core.previz_pids(), core.previz_freshness()))

    def _tail_engine_log(self) -> None:
        path = Path(self.engine.record.log) if self.engine else self.state_dir / "engine.log"
        if path != self._log_path:
            self._log_path, self._log_pos = path, 0
            self._log_decoder.reset()
            self.engine_log.clear()
        try:
            size = path.stat().st_size
        except OSError:
            return
        if size < self._log_pos:                 # a new engine started over the file
            self._log_pos = 0
            self._log_decoder.reset()
            self.engine_log.clear()
        if size == self._log_pos:
            return
        try:
            with path.open("rb") as f:
                f.seek(self._log_pos)
                data = f.read(256 * 1024)
        except OSError:
            return
        self._log_pos += len(data)
        self.engine_log.append(self._log_decoder.decode(data).replace("\r", ""))

    # -- state -------------------------------------------------------------

    def _ours_alive(self) -> bool:
        if self.engine is None:
            return False
        if self.engine.alive():
            return True
        # Ours, and it has stopped: by request, or by itself.
        self.last_exit = self.engine.exit_code
        self.engine = None
        self.stopping_since = None
        self.settings.engine = None
        self._probe_target = self.settings.port
        self._save()
        return False

    def _current_probe(self, port: int) -> core.Probe:
        return self.probe if self.probe_port == port else core.Probe("down")

    def _render(self) -> None:
        ours = self._ours_alive()
        port = self.engine.record.port if ours else self.settings.port
        p = self._current_probe(port)
        serving = p.state == "engine"

        # Event
        info = self.event_info
        if info is not None:
            self.event_label.configure(text=info.summary(), foreground=RED if info.error else GREY)
            mine = ours and Path(self.engine.record.event) == info.path
            self.lock_label.configure(text="" if not self.lock or mine else
                                      f"Locked: {self.lock}. Another engine is running this "
                                      f"event -- or one crashed and left its lock behind.")

        # Engine
        if ours:
            pid = self.engine.pid
            if self.stopping_since is not None:
                text, colour = f"Stopping (pid {pid})...", AMBER
                if time.monotonic() - self.stopping_since > 10:
                    text, colour = "Not stopping -- press Stop again to force it", RED
            elif serving:
                text, colour = f"Running {p.event} on port {port}  (pid {pid}, scene {p.rev})", GREEN
                if p.detail:
                    text, colour = f"Running on port {port}, but {p.detail}", AMBER
            elif time.time() - self.engine.record.started > 30:
                text, colour = f"Running (pid {pid}) but not answering on port {port} -- see the log", RED
            else:
                text, colour = "Starting...", AMBER
        elif p.state == "engine":
            text, colour = (f"An engine started elsewhere is running {p.event} on port {port}. "
                            f"Stop it where it was started, or pick another port."), GREEN
        elif p.state == "other":
            text, colour = (f"Port {port} is taken ({p.detail}) -- an older engine, or "
                            f"something else. Pick another port."), AMBER
        elif self.last_exit not in (None, 0):
            text, colour = f"Stopped -- the engine exited with code {self.last_exit}. The log says why.", RED
        else:
            text, colour = "Stopped", GREY
        self.engine_status.configure(text=text, foreground=colour)
        problem = core.check_artnet(self.settings.artnet)
        self.artnet_problem.configure(text=problem)

        idle = not ours and p.state == "down"
        self.start_btn.state(["!disabled"] if idle and not problem else ["disabled"])
        self.stop_btn.state(["!disabled"] if ours else ["disabled"])
        self.stop_btn.configure(text="Force stop" if ours and self.stopping_since is not None
                                and time.monotonic() - self.stopping_since > 10 else "Stop")
        for widget in (self.event_box, self.browse_btn, self.port_box, self.artnet_box,
                       self.extra_entry, self.token_chk):
            widget.state(["disabled"] if ours else ["!disabled"])
        self.bind_box.state(["disabled"] if ours else ["!disabled", "readonly"])

        # Console
        token = self.engine.record.token if ours else None
        for btn in (self.console_btn, self.setup_btn):
            btn.state(["!disabled"] if serving else ["disabled"])
        lan = self.lan
        local_only = (self.engine.record.bind if ours else self.settings.bind) == "127.0.0.1"
        link = "" if not serving or local_only or not lan else core.console_url(port, token, host=lan)
        self.link_var.set(link)
        self.copy_btn.state(["!disabled"] if link else ["disabled"])
        if not serving:
            note = ""
        elif not ours:
            note = ("Opens view-only: the token for an engine started elsewhere is "
                    "in the window that started it.")
        elif local_only:
            note = "The console is on this computer only -- phones cannot reach it."
        else:
            note = ("The phone link carries the control token. Anyone with it can run "
                    "the rig." if token else "No token: anyone on this network can run the rig.")
        self.console_note.configure(text=note)

        # Previz
        ours_previz = self.previz is not None and self.previz.poll() is None
        if self.previz is not None and not ours_previz:
            self.previz = None
        state, detail = self.freshness
        building = self.build is not None
        if building:
            text, colour = "Building -- see the Previz build tab.", AMBER
        elif ours_previz:
            text, colour = f"Running (pid {self.previz.pid}), drawing the engine on port {port}.", GREEN
        elif self.previz_found:
            text, colour = "A previz started elsewhere is running.", GREEN
        elif state == "missing":
            text, colour = "Not built yet -- Build previz (needs Unreal 5.8).", AMBER
        elif state == "stale":
            text, colour = f"Built, but {detail}: Build previz to bring it up to date.", AMBER
        else:
            text, colour = f"Ready ({detail}).", GREY
        self.previz_status.configure(text=text, foreground=colour)
        warning = "" if (not ours and p.state == "engine") else core.previz_feed_warning(
            self.settings.artnet, self.settings.previz_port)
        self.feed_warning.configure(text=warning)
        running_any = ours_previz or bool(self.previz_found)
        self.launch_btn.state(["!disabled"] if state != "missing" and not building
                              and not running_any else ["disabled"])
        self.close_btn.state(["!disabled"] if running_any else ["disabled"])
        self.build_btn.configure(text="Cancel build" if building else "Build previz...")
        self.build_btn.state(["!disabled"] if building or not running_any else ["disabled"])

    # -- actions -----------------------------------------------------------

    def _start_engine(self) -> None:
        self._settings_changed()
        s = self.settings
        path = Path(s.event)
        if not (path / "rig.json").is_file():
            messagebox.showerror("Start engine", f"{path} is not an event: it has no rig.json.")
            return
        info = self.event_info
        if info is not None and info.error:
            messagebox.showerror("Start engine", f"The rig does not load, so the engine would not "
                                 f"start:\n\n{info.error}")
            return
        # Checked here and not left to the engine: on Windows a second server
        # can bind a port another is already listening on, and then which of
        # them answers is down to luck.
        if core.probe(s.port, timeout=0.4).state != "down":
            messagebox.showerror("Start engine", f"Something is already answering on port "
                                 f"{s.port}. Pick another port.")
            return
        lock = core.read_lock(path)
        if lock and not messagebox.askyesno(
                "Start engine", f"{path.name} is locked: {lock}.\n\nIf another engine is "
                "running this event, two engines will fight over the rig frame by frame. If "
                "one crashed, the lock is stale and starting is safe.\n\nStart anyway?"):
            return
        try:
            self.engine = core.start_engine(s, self.state_dir)
        except OSError as exc:
            messagebox.showerror("Start engine", f"Could not start Python: {exc}")
            return
        self.settings.engine = self.engine.record
        self.last_exit = None
        self._probe_target = s.port
        self._wake.set()
        self._save()
        self.tabs.select(self.engine_log)

    def _stop_engine(self) -> None:
        if self.engine is None:
            return
        if self.stopping_since is not None and time.monotonic() - self.stopping_since > 10:
            if messagebox.askyesno("Force stop", "The engine has not stopped. Kill it?\n\nIt "
                                   "will not tidy up after itself; the launcher removes its "
                                   "lock file for it."):
                self.engine.kill()
            return
        self.engine.request_stop()
        self.stopping_since = time.monotonic()

    def _open_console(self, tab: Optional[str]) -> None:
        ours = self._ours_alive()
        port = self.engine.record.port if ours else self.settings.port
        token = self.engine.record.token if ours else None
        webbrowser.open(core.console_url(port, token, tab))

    def _copy_link(self) -> None:
        link = self.link_var.get()
        if link:
            self.root.clipboard_clear()
            self.root.clipboard_append(link)

    def _launch_previz(self) -> None:
        ours = self._ours_alive()
        port = self.engine.record.port if ours else self.settings.port
        try:
            self.previz = core.start_previz(core.PREVIZ_EXE, port, self.settings.previz_port,
                                            self.settings.previz_windowed)
        except OSError as exc:
            messagebox.showerror("Launch previz", f"Could not start {core.PREVIZ_EXE.name}: {exc}")

    def _close_previz(self) -> None:
        pids = [self.previz.pid] if self.previz is not None else list(self.previz_found)

        def work():
            for pid in pids:
                core.stop_previz(pid)
            self.q.put(("previz", core.previz_pids(), core.previz_freshness()))
        threading.Thread(target=work, daemon=True).start()

    def _build_previz(self) -> None:
        if self.build is not None:
            if messagebox.askyesno("Cancel build", "Stop the previz build?"):
                if core.NT:
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.build.pid)],
                                   capture_output=True, creationflags=core.NO_WINDOW)
                else:
                    self.build.kill()
            return
        if not messagebox.askokcancel(
                "Build previz", "Compile, test and package the Unreal previz app.\n\nAbout four "
                "minutes, and it needs Unreal 5.8 and Visual Studio Build Tools 2022 on this "
                "machine. The engine keeps running while it builds."):
            return
        self.build_log.clear()
        self.tabs.select(self.build_log)
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        try:
            self.build = subprocess.Popen(core.build_previz_command(), cwd=core.REPO,
                                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                          stderr=subprocess.STDOUT, env=env,
                                          creationflags=core.NO_WINDOW)
        except OSError as exc:
            self.build = None
            messagebox.showerror("Build previz", str(exc))
            return
        proc = self.build

        def read():
            for raw in proc.stdout:
                self.q.put(("build", raw.decode("utf-8", errors="replace").replace("\r", "")))
            self.q.put(("build_done", proc.wait()))
        threading.Thread(target=read, daemon=True).start()

    # -- closing -----------------------------------------------------------

    def _on_close(self) -> None:
        if self.build is not None:
            if not messagebox.askokcancel("Close kLights", "A previz build is running. "
                                          "Closing stops it."):
                return
            if core.NT:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.build.pid)],
                               capture_output=True, creationflags=core.NO_WINDOW)
            else:
                self.build.kill()
        if self._ours_alive():
            answer = messagebox.askyesnocancel(
                "Close kLights", "The engine is still running.\n\nYes: stop it, then close.\n"
                "No: leave it running. Opening the launcher again finds it.\nCancel: stay open.")
            if answer is None:
                return
            if answer:
                self.engine.request_stop()
                self.stopping_since = time.monotonic()
                self._close_when_stopped(time.monotonic() + 12)
                return
        self._finish_close()

    def _close_when_stopped(self, deadline: float) -> None:
        if not self._ours_alive() or time.monotonic() > deadline:
            self._finish_close()
        else:
            self.root.after(200, lambda: self._close_when_stopped(deadline))

    def _finish_close(self) -> None:
        self._save()
        self._closing.set()
        self._wake.set()
        self.root.destroy()


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m launcher", description=__doc__.split("\n")[1])
    parser.add_argument("--event", type=Path, help="open with this event chosen")
    parser.add_argument("--state-dir", type=Path, default=core.STATE_DIR,
                        help="where settings and the engine log live (default .launcher/)")
    args = parser.parse_args(argv)
    if args.event is not None:
        settings = core.load_settings(args.state_dir)
        settings.event = str(args.event.resolve())
        core.save_settings(settings, args.state_dir)
    if core.NT:
        # Crisp text on a scaled display instead of a blurry bitmap stretch.
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    root = tk.Tk()

    # Under pythonw there is no console, so Tk's default -- print the traceback
    # to stderr -- would make every bug in a button silent.
    def report(kind, value, tb):
        import traceback
        messagebox.showerror("kLights launcher: something went wrong",
                             "".join(traceback.format_exception(kind, value, tb))[-1800:])
    root.report_callback_exception = report
    Launcher(root, args.state_dir)
    # No narrower than the controls need, or the right-hand ones are cut off.
    root.update_idletasks()
    root.minsize(root.winfo_reqwidth(), root.winfo_reqheight() // 2)
    root.mainloop()
    return 0
