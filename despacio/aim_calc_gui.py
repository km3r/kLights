"""
Desktop GUI for despacio_config.json + aim_calc.py.

Presents the site-measured config (room/head/ball geometry, per-head DMX
calibration readings, mount facing overrides, invert flags) as a form instead
of hand-editing JSON, then runs aim_calc.py as a subprocess to recompute and
write despacio.qxw, showing its output (poses, [UNCALIBRATED] tags, or
self-test failures) in a log pane.

Run: python aim_calc_gui.py
"""
import json
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

HERE = Path(__file__).parent
CONFIG_PATH = HERE / "despacio_config.json"
AIM_CALC = HERE / "aim_calc.py"
PREFLIGHT = HERE / "preflight.py"

MODES = ["table", "venue", "hung"]
HEAD_LABELS = [
    "Head 1 (ID0, back-right, addr1)",
    "Head 2 (ID1, front-right, addr12)",
    "Head 3 (ID2, front-left, addr23)",
    "Head 4 (ID3, back-left, addr34)",
]


def load_config():
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    # Let aim_calc.py create it from its own DEFAULT_CONFIG on first run.
    subprocess.run([sys.executable, str(AIM_CALC)], cwd=HERE, capture_output=True, text=True)
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")


class Tooltip:
    """Small delayed popup shown on hover, standard Tk pattern (no ttk widget
    for this exists). One instance per widget; text can be multi-line."""

    def __init__(self, widget, text, delay_ms=500, wraplength=320):
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self.wraplength = wraplength
        self._after_id = None
        self._tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _cancel(self):
        if self._after_id is not None:
            self.widget.after_cancel(self._after_id)
            self._after_id = None

    def _show(self):
        if self._tip is not None:
            return
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        label = tk.Label(self._tip, text=self.text, justify="left", wraplength=self.wraplength,
                          background="#ffffe0", relief="solid", borderwidth=1,
                          font=("", 9), padx=6, pady=4)
        label.pack()

    def _hide(self, _event=None):
        self._cancel()
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


def tip(widget, text):
    Tooltip(widget, text)
    return widget


MODE_TIP = (
    '"table" = bench-test rig: heads sit base-down on a desk. This simulates hanging '
    'upside-down from a ceiling, so Pan/Tilt keep their normal bearing/elevation roles '
    'and default inverted.\n\n'
    '"venue" = real final install: base tipped onto its side (cables exit the top). '
    'Pan and Tilt trade roles -- Pan now carries elevation, Tilt now carries bearing -- '
    'and neither is inverted by default.\n\n'
    'Calibration data, and the invert flags, are stored separately per mode. Running '
    'aim_calc.py writes whichever mode is selected here into despacio.qxw -- it is a '
    'build-time selector, not a live in-show toggle.'
)

GEO_TIPS = {
    "room_width": "Room width in mm (default 9144 = 30 ft). Shared by both mount modes.",
    "room_depth": "Room depth in mm (default 9144 = 30 ft). Shared by both mount modes.",
    "head_inset": "How far each head sits in from its literal wall corner, in mm "
                  "(default 500 = ~0.5 m). Adjust to match the real bracket standoff.",
    "ball_height": "Disco ball height off the floor, in mm (default 3048 = 10 ft).",
    "ball_x": "Ball's horizontal position in the room (mm). Defaults to room center.",
    "ball_z": "Ball's depth position in the room (mm). Defaults to room center.",
}

HEAD_COL_TIPS = {
    "height": "This head's mounting height off the floor, in mm. Measure on site -- "
              "truss/ceiling attachment points are rarely perfectly even.",
    "cal_pan": "Pan knob reading (0-255) with this head visually aimed AT THE BALL by eye. "
               "Enter alongside the Tilt reading, in the order shown on the knobs. Leave "
               "both blank to fall back to an uncalibrated geometry guess (the script "
               "prints [UNCALIBRATED] for that head).",
    "cal_tilt": "Tilt knob reading (0-255) with this head visually aimed AT THE BALL by eye. "
                "Enter alongside the Pan reading. Leave both blank to fall back to an "
                "uncalibrated geometry guess.",
    "facing": "Only needed for a head you deliberately don't calibrate: a fallback mount-"
              "facing angle in degrees, shared across both modes. Leave blank for auto "
              "(assumes the head points at the ball).",
    "pan_invert": "Flips the Pan channel's direction for the current mode, if this head "
                  "moves the wrong way. 'table' mode defaults checked (True); 'venue' "
                  "mode defaults unchecked.",
    "tilt_invert": "Flips the Tilt channel's direction for the current mode, if this head "
                   "moves the wrong way. 'table' mode defaults checked (True); 'venue' "
                   "mode defaults unchecked.",
}

SAVE_TIP = "Writes the form to despacio_config.json without recalculating despacio.qxw."
SAVE_RUN_TIP = ("Writes despacio_config.json, then reruns aim_calc.py to recompute every "
                "pose (Ball/Floor/Crowd/Diagonal/Chase) and update despacio.qxw. Output "
                "and any warnings or self-test failures show in the log below.")
PREFLIGHT_TIP = ("Runs preflight.py: the math self-test, the structural validator (patch / "
                 "VC mesh / MIDI), the active mount-mode + calibration guardrails, and the "
                 "fixture-def-installed check. Does NOT write anything. Run before leaving "
                 "home and again at the venue before going live -- a red RESULT means don't "
                 "go live until it's fixed.")


class ValidationError(Exception):
    pass


def parse_float(text, field_name):
    text = text.strip()
    if not text:
        raise ValidationError(f"{field_name} is required")
    try:
        return float(text)
    except ValueError:
        raise ValidationError(f"{field_name} must be a number, got {text!r}")


def parse_optional_float(text, field_name):
    text = text.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        raise ValidationError(f"{field_name} must be a number or blank, got {text!r}")


def parse_optional_dmx(text, field_name):
    text = text.strip()
    if not text:
        return None
    try:
        v = int(text)
    except ValueError:
        raise ValidationError(f"{field_name} must be an integer 0-255 or blank, got {text!r}")
    if not (0 <= v <= 255):
        raise ValidationError(f"{field_name} must be 0-255, got {v}")
    return v


class DespacioGUI:
    def __init__(self, root):
        self.root = root
        root.title("Despacio Aim Calc")

        self.cfg = load_config()
        self.mode_var = tk.StringVar(value=self.cfg["mount_mode"])

        self._build_widgets()
        self._load_form_from_cfg()

    def _build_widgets(self):
        pad = {"padx": 4, "pady": 2}

        top = ttk.Frame(self.root, padding=8)
        top.grid(row=0, column=0, sticky="ew")

        tip(ttk.Label(top, text="Mount mode:"), MODE_TIP).grid(row=0, column=0, sticky="w", **pad)
        mode_combo = ttk.Combobox(top, textvariable=self.mode_var, values=MODES,
                                   state="readonly", width=10)
        mode_combo.grid(row=0, column=1, sticky="w", **pad)
        mode_combo.bind("<<ComboboxSelected>>", self._on_mode_change)
        tip(mode_combo, MODE_TIP)

        geo = ttk.LabelFrame(self.root, text="Room / ball geometry (mm)", padding=8)
        geo.grid(row=1, column=0, sticky="ew", padx=8, pady=4)
        self.geo_vars = {}
        geo_fields = [
            ("room_width", "Room width"), ("room_depth", "Room depth"),
            ("head_inset", "Head inset"), ("ball_height", "Ball height"),
            ("ball_x", "Ball X"), ("ball_z", "Ball Z"),
        ]
        for col, (key, label) in enumerate(geo_fields):
            tip(ttk.Label(geo, text=label), GEO_TIPS[key]).grid(row=0, column=col, sticky="w", **pad)
            var = tk.StringVar()
            tip(ttk.Entry(geo, textvariable=var, width=10), GEO_TIPS[key]).grid(row=1, column=col, **pad)
            self.geo_vars[key] = var

        heads = ttk.LabelFrame(self.root, text="Per-head config", padding=8)
        heads.grid(row=2, column=0, sticky="ew", padx=8, pady=4)
        header_defs = [
            ("Head", None),
            ("Height (mm)", HEAD_COL_TIPS["height"]),
            ("Cal. Pan DMX", HEAD_COL_TIPS["cal_pan"]),
            ("Cal. Tilt DMX", HEAD_COL_TIPS["cal_tilt"]),
            ("Facing override (deg)", HEAD_COL_TIPS["facing"]),
            ("Pan invert", HEAD_COL_TIPS["pan_invert"]),
            ("Tilt invert", HEAD_COL_TIPS["tilt_invert"]),
        ]
        for col, (h, tip_text) in enumerate(header_defs):
            lbl = ttk.Label(heads, text=h, font=("", 9, "bold"))
            lbl.grid(row=0, column=col, sticky="w", **pad)
            if tip_text:
                tip(lbl, tip_text)

        self.head_height_vars = []
        self.cal_pan_vars = []
        self.cal_tilt_vars = []
        self.facing_vars = []
        self.pan_invert_vars = []
        self.tilt_invert_vars = []
        for i, label in enumerate(HEAD_LABELS):
            r = i + 1
            ttk.Label(heads, text=label).grid(row=r, column=0, sticky="w", **pad)

            hh = tk.StringVar()
            tip(ttk.Entry(heads, textvariable=hh, width=10), HEAD_COL_TIPS["height"]).grid(
                row=r, column=1, **pad)
            self.head_height_vars.append(hh)

            cp = tk.StringVar()
            tip(ttk.Entry(heads, textvariable=cp, width=10), HEAD_COL_TIPS["cal_pan"]).grid(
                row=r, column=2, **pad)
            self.cal_pan_vars.append(cp)

            ct = tk.StringVar()
            tip(ttk.Entry(heads, textvariable=ct, width=10), HEAD_COL_TIPS["cal_tilt"]).grid(
                row=r, column=3, **pad)
            self.cal_tilt_vars.append(ct)

            fo = tk.StringVar()
            tip(ttk.Entry(heads, textvariable=fo, width=12), HEAD_COL_TIPS["facing"]).grid(
                row=r, column=4, **pad)
            self.facing_vars.append(fo)

            pi = tk.BooleanVar()
            tip(ttk.Checkbutton(heads, variable=pi), HEAD_COL_TIPS["pan_invert"]).grid(
                row=r, column=5, **pad)
            self.pan_invert_vars.append(pi)

            ti = tk.BooleanVar()
            tip(ttk.Checkbutton(heads, variable=ti), HEAD_COL_TIPS["tilt_invert"]).grid(
                row=r, column=6, **pad)
            self.tilt_invert_vars.append(ti)

        btns = ttk.Frame(self.root, padding=8)
        btns.grid(row=3, column=0, sticky="ew")
        tip(ttk.Button(btns, text="Save Config", command=self.on_save),
            SAVE_TIP).grid(row=0, column=0, padx=4)
        tip(ttk.Button(btns, text="Save && Recalculate", command=self.on_save_and_run),
            SAVE_RUN_TIP).grid(row=0, column=1, padx=4)
        tip(ttk.Button(btns, text="Preflight check", command=self.on_preflight),
            PREFLIGHT_TIP).grid(row=0, column=2, padx=4)

        log_frame = ttk.LabelFrame(self.root, text="Output", padding=4)
        log_frame.grid(row=4, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self.root.rowconfigure(4, weight=1)
        self.root.columnconfigure(0, weight=1)
        self.log = tk.Text(log_frame, height=12, width=100, state="disabled", wrap="word")
        self.log.grid(row=0, column=0, sticky="nsew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        scroll = ttk.Scrollbar(log_frame, command=self.log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scroll.set)

    def _on_mode_change(self, _event=None):
        # Persist whatever's currently in the form for the mode we're leaving,
        # into self.cfg, before swapping the displayed mode's fields in.
        try:
            self._collect_active_mode_into_cfg(self._previous_mode)
        except ValidationError:
            pass  # don't block switching modes just to fix a typo first
        self._load_form_from_cfg()

    def _load_form_from_cfg(self):
        mode = self.mode_var.get()
        self._previous_mode = mode

        for key, var in self.geo_vars.items():
            var.set(str(self.cfg[key]))

        cal = self.cfg["calibrated_ball_dmx"][mode]
        pan_inv = self.cfg["pan_invert"][mode]
        tilt_inv = self.cfg["tilt_invert"][mode]
        facing = self.cfg["mount_facing_override"]
        heights = self.cfg["head_height"]

        for i in range(4):
            self.head_height_vars[i].set(str(heights[i]))
            c = cal[i]
            self.cal_pan_vars[i].set("" if c is None else str(c[0]))
            self.cal_tilt_vars[i].set("" if c is None else str(c[1]))
            f = facing[i]
            self.facing_vars[i].set("" if f is None else str(f))
            self.pan_invert_vars[i].set(bool(pan_inv[i]))
            self.tilt_invert_vars[i].set(bool(tilt_inv[i]))

    def _collect_active_mode_into_cfg(self, mode):
        """Reads the form into self.cfg for `mode`. Raises ValidationError
        (without mutating self.cfg) if any field is invalid."""
        room_width = parse_float(self.geo_vars["room_width"].get(), "Room width")
        room_depth = parse_float(self.geo_vars["room_depth"].get(), "Room depth")
        head_inset = parse_float(self.geo_vars["head_inset"].get(), "Head inset")
        ball_height = parse_float(self.geo_vars["ball_height"].get(), "Ball height")
        ball_x = parse_float(self.geo_vars["ball_x"].get(), "Ball X")
        ball_z = parse_float(self.geo_vars["ball_z"].get(), "Ball Z")

        heights, cal, facing, pan_inv, tilt_inv = [], [], [], [], []
        for i, label in enumerate(HEAD_LABELS):
            heights.append(parse_float(self.head_height_vars[i].get(), f"{label} height"))
            pan_dmx = parse_optional_dmx(self.cal_pan_vars[i].get(), f"{label} calibrated pan DMX")
            tilt_dmx = parse_optional_dmx(self.cal_tilt_vars[i].get(), f"{label} calibrated tilt DMX")
            if (pan_dmx is None) != (tilt_dmx is None):
                raise ValidationError(
                    f"{label}: calibrated pan/tilt DMX must both be set or both blank")
            cal.append(None if pan_dmx is None else [pan_dmx, tilt_dmx])
            facing.append(parse_optional_float(self.facing_vars[i].get(), f"{label} facing override"))
            pan_inv.append(bool(self.pan_invert_vars[i].get()))
            tilt_inv.append(bool(self.tilt_invert_vars[i].get()))

        # All validated -- now mutate self.cfg.
        self.cfg["room_width"] = room_width
        self.cfg["room_depth"] = room_depth
        self.cfg["head_inset"] = head_inset
        self.cfg["ball_height"] = ball_height
        self.cfg["ball_x"] = ball_x
        self.cfg["ball_z"] = ball_z
        self.cfg["head_height"] = heights
        self.cfg["calibrated_ball_dmx"][mode] = cal
        self.cfg["mount_facing_override"] = facing
        self.cfg["pan_invert"][mode] = pan_inv
        self.cfg["tilt_invert"][mode] = tilt_inv
        self.cfg["mount_mode"] = mode

    def _log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def on_save(self):
        mode = self.mode_var.get()
        try:
            self._collect_active_mode_into_cfg(mode)
        except ValidationError as e:
            messagebox.showerror("Invalid input", str(e))
            return
        save_config(self.cfg)
        self._log(f"Saved despacio_config.json (mount_mode={mode!r})\n")

    def on_save_and_run(self):
        mode = self.mode_var.get()
        try:
            self._collect_active_mode_into_cfg(mode)
        except ValidationError as e:
            messagebox.showerror("Invalid input", str(e))
            return
        save_config(self.cfg)
        self._log(f"Saved despacio_config.json (mount_mode={mode!r})\n")
        self._log("Running aim_calc.py...\n")
        result = subprocess.run([sys.executable, str(AIM_CALC)], cwd=HERE,
                                 capture_output=True, text=True)
        self._log(result.stdout)
        if result.stderr:
            self._log(result.stderr)
        self._log(f"(exit code {result.returncode})\n{'-' * 60}\n")
        if result.returncode != 0:
            messagebox.showerror("aim_calc.py failed",
                                  "See the Output log for details.")

    def on_preflight(self):
        # Read-only readiness check -- deliberately does NOT save the form first,
        # so it reports on what's actually on disk / in despacio.qxw right now,
        # not unsaved edits. Save (or Save && Recalculate) first if you want the
        # current form reflected.
        self._log("Running preflight.py...\n")
        result = subprocess.run([sys.executable, str(PREFLIGHT)], cwd=HERE,
                                 capture_output=True, text=True)
        self._log(result.stdout)
        if result.stderr:
            self._log(result.stderr)
        self._log(f"(exit code {result.returncode})\n{'-' * 60}\n")
        if result.returncode != 0:
            messagebox.showwarning("Preflight FAILED",
                                    "Not clear to go live -- see the Output log.")


def main():
    root = tk.Tk()
    DespacioGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
