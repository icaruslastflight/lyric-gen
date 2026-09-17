"""Lyric Gen — Standalone Tkinter GUI with dark theme."""

import logging
import os
import time
import threading
import webbrowser
import tkinter as tk
from tkinter import ttk

from lyric_gen import __version__
from lyric_gen.core.app import LyricGenApp
from lyric_gen.core.preferences import load_preferences, save_preferences

logger = logging.getLogger("lyricgen.gui")

# Preferences dialog palette (match main window)
_PREFS_BG = "#1b1d20"
_PREFS_PANEL = "#2a2d31"
_PREFS_RAISED = "#31353a"
_PREFS_ACCENT = "#59c9b5"
_PREFS_TEXT = "#d7dbe0"
_PREFS_DIM = "#8a919a"


def _prefs_open_url(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception as exc:
        logger.warning("Could not open browser: %s", exc)


class AudioFFTMonitor(tk.Toplevel):
    """Small live input monitor to verify selected audio device has signal."""

    def __init__(self, master: tk.Misc, gui: "LyricGenGUI"):
        super().__init__(master)
        self.gui = gui
        self.title("Lyric Gen — Audio FFT Monitor")
        self.configure(bg=_PREFS_BG)
        self.geometry("540x280")
        self.minsize(460, 240)
        self.resizable(True, True)

        self._chunk = 1024
        self._sample_rate = int(gui.prefs["audio"].get("sample_rate", 44100) or 44100)
        self._bins = 28
        self._running = True
        self._lock = threading.Lock()
        self._fft_data = [0.0] * self._bins
        self._rms_level = 0.0
        self._status_text = "Starting..."

        outer = tk.Frame(self, bg=_PREFS_BG, padx=10, pady=10)
        outer.pack(fill="both", expand=True)

        top = tk.Frame(outer, bg=_PREFS_BG)
        top.pack(fill="x")
        self._status_var = tk.StringVar(value="Initializing audio monitor...")
        self._level_var = tk.StringVar(value="Level: 0.000")
        tk.Label(top, textvariable=self._status_var, bg=_PREFS_BG, fg=_PREFS_DIM, font=("Segoe UI", 9)).pack(
            side="left"
        )
        tk.Label(top, textvariable=self._level_var, bg=_PREFS_BG, fg=_PREFS_TEXT, font=("Segoe UI", 9, "bold")).pack(
            side="right"
        )

        self._canvas = tk.Canvas(
            outer,
            bg="#16181b",
            highlightthickness=1,
            highlightbackground="#3a4048",
            relief="flat",
        )
        self._canvas.pack(fill="both", expand=True, pady=(8, 0))

        self._bars = []
        for _ in range(self._bins):
            bar = self._canvas.create_rectangle(0, 0, 0, 0, fill=_PREFS_ACCENT, width=0)
            self._bars.append(bar)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.gui._fft_win = self

        self._worker = threading.Thread(target=self._capture_loop, daemon=True)
        self._worker.start()
        self.after(80, self._draw_loop)

    def _set_status(self, text: str) -> None:
        with self._lock:
            self._status_text = text

    def _resolve_device_index(self):
        combo = self.gui._get_live_device_combo()
        if combo is not None and getattr(self.gui, "devices", None):
            try:
                cur = combo.current()
                if cur >= 0:
                    return int(self.gui.devices[cur][0])
            except tk.TclError:
                self.gui.device_combo = None
        return self.gui.prefs["audio"].get("device_index")

    def _capture_loop(self):
        try:
            import pyaudio
            import numpy as np
        except Exception as exc:
            self._set_status(f"PyAudio unavailable: {exc}")
            return

        pa = pyaudio.PyAudio()
        stream = None
        device_index = self._resolve_device_index()
        device_text = "default" if device_index is None else str(device_index)
        try:
            stream = pa.open(
                format=pyaudio.paFloat32,
                channels=int(self.gui.prefs["audio"].get("channels", 1) or 1),
                rate=self._sample_rate,
                input=True,
                input_device_index=device_index,
                frames_per_buffer=self._chunk,
            )
            self._set_status(f"Listening on input device {device_text}")
            while self._running:
                try:
                    data = stream.read(self._chunk, exception_on_overflow=False)
                    samples = np.frombuffer(data, dtype=np.float32)
                    if samples.size == 0:
                        continue
                    window = np.hanning(samples.size)
                    spectrum = np.abs(np.fft.rfft(samples * window))[1:]
                    if spectrum.size == 0:
                        continue
                    spec_log = np.log1p(spectrum)
                    groups = np.array_split(spec_log, self._bins)
                    vals = np.array([float(g.mean()) if g.size else 0.0 for g in groups], dtype=float)
                    vmax = float(vals.max()) if vals.size else 0.0
                    if vmax > 0:
                        vals = vals / vmax
                    rms = float(np.sqrt(np.mean(samples * samples)))
                    with self._lock:
                        self._fft_data = vals.tolist()
                        self._rms_level = rms
                except OSError as exc:
                    self._set_status(f"Input stream stopped: {exc}")
                    break
                except Exception as exc:
                    self._set_status(f"Audio monitor error: {exc}")
                    break
        except Exception as exc:
            self._set_status(f"Could not open input device {device_text}: {exc}")
        finally:
            if stream is not None:
                try:
                    stream.stop_stream()
                except Exception:
                    pass
                try:
                    stream.close()
                except Exception:
                    pass
            try:
                pa.terminate()
            except Exception:
                pass

    def _draw_loop(self):
        if not self._running:
            return
        with self._lock:
            bars = list(self._fft_data)
            rms = float(self._rms_level)
            status = self._status_text
        self._status_var.set(status)
        self._level_var.set(f"Level: {rms:.4f}")

        w = max(1, self._canvas.winfo_width())
        h = max(1, self._canvas.winfo_height())
        n = max(1, len(self._bars))
        gap = 2
        bar_w = max(2, int((w - (n + 1) * gap) / n))
        x = gap
        for i, rid in enumerate(self._bars):
            v = bars[i] if i < len(bars) else 0.0
            v = max(0.0, min(1.0, float(v)))
            y0 = h - int(v * (h - 6))
            self._canvas.coords(rid, x, y0, x + bar_w, h - 2)
            x += bar_w + gap

        self.after(80, self._draw_loop)

    def _on_close(self):
        self._running = False
        self.gui._fft_win = None
        self.destroy()


class PreferencesWindow(tk.Toplevel):
    """Tabbed preferences: Resolume, song ID / lyrics sources, audio."""

    def __init__(self, master: tk.Misc, gui: "LyricGenGUI"):
        super().__init__(master)
        self.gui = gui
        self.title("Lyric Gen — Preferences")
        self.configure(bg=_PREFS_BG)
        self.geometry("580x560")
        self.minsize(520, 480)
        self.resizable(True, True)

        outer = tk.Frame(self, bg=_PREFS_BG, padx=12, pady=10)
        outer.pack(fill="both", expand=True)

        # Keep primary actions visible without resizing.
        btns = tk.Frame(outer, bg=_PREFS_BG)
        btns.pack(fill="x", pady=(0, 8))
        ttk.Button(btns, text="Save all", command=self._save_all, style="Small.TButton").pack(
            side="left", padx=4
        )
        ttk.Button(btns, text="Refresh device list", command=self._refresh_devices, style="Small.TButton").pack(
            side="left", padx=4
        )
        ttk.Button(
            btns, text="Open preferences.json", command=self.gui._open_preferences_json, style="Small.TButton"
        ).pack(side="left", padx=4)
        ttk.Button(btns, text="Close", command=self.destroy, style="Small.TButton").pack(side="right", padx=4)

        nb = ttk.Notebook(outer)
        nb.pack(fill="both", expand=True)

        tab_res = tk.Frame(nb, bg=_PREFS_PANEL)
        tab_ai = tk.Frame(nb, bg=_PREFS_PANEL)
        tab_aud = tk.Frame(nb, bg=_PREFS_PANEL)
        nb.add(tab_res, text="  Resolume  ")
        nb.add(tab_ai, text="  Song ID  ")
        nb.add(tab_aud, text="  Audio  ")

        self._build_resolume_tab(tab_res)
        self._build_song_id_tab(tab_ai)
        self._build_audio_tab(tab_aud)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.gui._prefs_win = self

    def _on_close(self):
        self.gui.device_combo = None
        self.gui._prefs_win = None
        self.destroy()

    def _save_all(self):
        self.gui._sync_prefs_from_gui()
        self.gui._log("Saved all preferences.")

    def _refresh_devices(self):
        self.gui._refresh_device_list()

    def _link(self, parent, text: str, url: str):
        lbl = tk.Label(
            parent,
            text=text,
            fg=_PREFS_ACCENT,
            bg=_PREFS_PANEL,
            cursor="hand2",
            font=("Segoe UI", 9, "underline"),
        )
        lbl.pack(anchor="w", padx=12, pady=2)
        lbl.bind("<Button-1>", lambda _e, u=url: _prefs_open_url(u))

    def _build_resolume_tab(self, tab):
        tk.Label(
            tab,
            text="Resolume OSC (must match Arena incoming OSC and allow local network access).",
            bg=_PREFS_PANEL,
            fg=_PREFS_DIM,
            font=("Segoe UI", 9),
        ).pack(anchor="w", padx=12, pady=(8, 4))

        row_c = tk.Frame(tab, bg=_PREFS_PANEL)
        row_c.pack(fill="x", padx=12, pady=4)
        tk.Label(row_c, text="IP", bg=_PREFS_PANEL, fg=_PREFS_DIM, font=("Segoe UI", 9)).pack(side="left")
        tk.Entry(
            row_c,
            textvariable=self.gui.resolume_ip_var,
            width=16,
            bg=_PREFS_RAISED,
            fg=_PREFS_TEXT,
            insertbackground=_PREFS_TEXT,
        ).pack(side="left", padx=4)
        tk.Label(row_c, text="Port", bg=_PREFS_PANEL, fg=_PREFS_DIM).pack(side="left", padx=(8, 0))
        tk.Entry(
            row_c,
            textvariable=self.gui.resolume_port_var,
            width=6,
            bg=_PREFS_RAISED,
            fg=_PREFS_TEXT,
            insertbackground=_PREFS_TEXT,
        ).pack(side="left", padx=4)
        tk.Label(row_c, text="Listen", bg=_PREFS_PANEL, fg=_PREFS_DIM).pack(side="left", padx=(8, 0))
        tk.Entry(
            row_c,
            textvariable=self.gui.listen_port_var,
            width=6,
            bg=_PREFS_RAISED,
            fg=_PREFS_TEXT,
            insertbackground=_PREFS_TEXT,
        ).pack(side="left", padx=4)
        tk.Label(row_c, text="(/lyricgen/cmd)", bg=_PREFS_PANEL, fg=_PREFS_DIM).pack(side="left", padx=4)
        tk.Checkbutton(
            row_c,
            text="Send BPM",
            variable=self.gui.chk_send_bpm,
            command=self.gui._save_connection_fields,
            bg=_PREFS_PANEL,
            fg=_PREFS_TEXT,
            selectcolor=_PREFS_RAISED,
            activebackground=_PREFS_PANEL,
            activeforeground=_PREFS_TEXT,
        ).pack(side="left", padx=8)

        tk.Label(
            tab,
            text="Text target — layer/clip must use a Text Block (or compatible) source.",
            bg=_PREFS_PANEL,
            fg=_PREFS_DIM,
            font=("Segoe UI", 9),
        ).pack(anchor="w", padx=12, pady=(12, 4))

        target_row = tk.Frame(tab, bg=_PREFS_PANEL)
        target_row.pack(padx=12, pady=4)
        tk.Label(target_row, text="Current line — Layer", bg=_PREFS_PANEL, fg=_PREFS_DIM).pack(side="left")
        tk.Spinbox(
            target_row,
            from_=1,
            to=99,
            width=3,
            textvariable=self.gui.lyric_layer_var,
            bg=_PREFS_RAISED,
            fg=_PREFS_TEXT,
            buttonbackground=_PREFS_RAISED,
            command=self.gui._save_resolume_target,
        ).pack(side="left", padx=2)
        tk.Label(target_row, text="Clip", bg=_PREFS_PANEL, fg=_PREFS_DIM).pack(side="left", padx=(8, 0))
        tk.Spinbox(
            target_row,
            from_=1,
            to=99,
            width=3,
            textvariable=self.gui.lyric_clip_var,
            bg=_PREFS_RAISED,
            fg=_PREFS_TEXT,
            buttonbackground=_PREFS_RAISED,
            command=self.gui._save_resolume_target,
        ).pack(side="left", padx=2)

        target_row2 = tk.Frame(tab, bg=_PREFS_PANEL)
        target_row2.pack(padx=12, pady=2)
        tk.Label(target_row2, text="Next line — Layer", bg=_PREFS_PANEL, fg=_PREFS_DIM).pack(side="left")
        tk.Spinbox(
            target_row2,
            from_=1,
            to=99,
            width=3,
            textvariable=self.gui.next_layer_var,
            bg=_PREFS_RAISED,
            fg=_PREFS_TEXT,
            buttonbackground=_PREFS_RAISED,
            command=self.gui._save_resolume_target,
        ).pack(side="left", padx=2)
        tk.Label(target_row2, text="Clip", bg=_PREFS_PANEL, fg=_PREFS_DIM).pack(side="left", padx=(8, 0))
        tk.Spinbox(
            target_row2,
            from_=1,
            to=99,
            width=3,
            textvariable=self.gui.next_clip_var,
            bg=_PREFS_RAISED,
            fg=_PREFS_TEXT,
            buttonbackground=_PREFS_RAISED,
            command=self.gui._save_resolume_target,
        ).pack(side="left", padx=2)

    def _build_song_id_tab(self, tab):
        tk.Label(
            tab,
            text="Song ID setup (optional): add your ACRCloud keys to enable automatic track recognition.",
            bg=_PREFS_PANEL,
            fg=_PREFS_DIM,
            font=("Segoe UI", 9),
            wraplength=520,
            justify="left",
        ).pack(anchor="w", padx=12, pady=(8, 2))

        tk.Label(
            tab,
            text=(
                "If these fields are blank, Lyric Gen still runs for BPM/OSC, but automatic song lookup is disabled.\n"
                "Lyrics can still appear when title/artist is already known."
            ),
            bg=_PREFS_PANEL,
            fg=_PREFS_DIM,
            font=("Segoe UI", 8),
            wraplength=520,
            justify="left",
        ).pack(anchor="w", padx=12, pady=(0, 6))

        tk.Label(
            tab,
            text="Step 1) In ACRCloud Console, create a recognition project:",
            bg=_PREFS_PANEL,
            fg=_PREFS_TEXT,
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", padx=12, pady=(2, 2))

        self._link(tab, "ACRCloud — create project & credentials", "https://console.acrcloud.com/")
        self._link(tab, "ACRCloud — product / docs", "https://www.acrcloud.com/")
        self._link(tab, "LRCLIB — synced lyrics source (free, no key)", "https://lrclib.net/")

        tk.Label(
            tab,
            text=(
                "1) Open Products -> Audio & Video Recognition.\n"
                "2) Create a project (or open an existing one).\n"
                "3) Recommended project options for Lyric Gen:\n"
                "   - Audio Source: Recorded Audio (best for laptop mic / noisy room capture)\n"
                "   - Audio Engine: Audio Fingerprinting + Cover Song (humming) Identification\n"
                "     (use Audio Fingerprinting only if you want strict exact-version matching)\n"
                "   - 3rd Party IDs: Enable ISRC only (leave Spotify/Deezer/YouTube/UPC off for v1)\n"
                "   - Include Works: OFF\n"
                "4) In that project, copy Host, Access Key, and Access Secret.\n"
                "5) Do NOT use Personal Access Token or Billing token fields for Lyric Gen."
            ),
            bg=_PREFS_PANEL,
            fg=_PREFS_DIM,
            font=("Segoe UI", 8),
            wraplength=520,
            justify="left",
        ).pack(anchor="w", padx=12, pady=(2, 6))

        tk.Label(
            tab,
            text="Step 2) Paste credentials below:",
            bg=_PREFS_PANEL,
            fg=_PREFS_TEXT,
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", padx=12, pady=(8, 2))

        for label, var, pwd in (
            ("ACR host", self.gui.acr_host_var, False),
            ("Access key", self.gui.acr_key_var, False),
            ("Access secret", self.gui.acr_secret_var, True),
        ):
            row = tk.Frame(tab, bg=_PREFS_PANEL)
            row.pack(fill="x", padx=12, pady=3)
            tk.Label(row, text=f"{label:12}", bg=_PREFS_PANEL, fg=_PREFS_DIM, width=12, anchor="w").pack(
                side="left"
            )
            tk.Entry(
                row,
                textvariable=var,
                bg=_PREFS_RAISED,
                fg=_PREFS_TEXT,
                insertbackground=_PREFS_TEXT,
                show="•" if pwd else "",
            ).pack(side="left", fill="x", expand=True, padx=4)

        tk.Label(
            tab,
            text=(
                "Step 3) Click Save all, then restart the engine (STOP -> START).\n"
                "Host format example: identify-us-west-2.acrcloud.com (no https://).\n"
                "If Song ID still does not work, verify your project is active, has available credits,\n"
                "and uses the same region as the host value you pasted."
            ),
            bg=_PREFS_PANEL,
            fg=_PREFS_DIM,
            font=("Segoe UI", 8),
            wraplength=520,
            justify="left",
        ).pack(anchor="w", padx=12, pady=8)

        tk.Label(
            tab,
            text="Lyric timeline follow (live drift correction):",
            bg=_PREFS_PANEL,
            fg=_PREFS_TEXT,
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", padx=12, pady=(4, 2))

        tune_row = tk.Frame(tab, bg=_PREFS_PANEL)
        tune_row.pack(fill="x", padx=12, pady=(0, 4))
        tk.Label(tune_row, text="Deadband (s)", bg=_PREFS_PANEL, fg=_PREFS_DIM).pack(side="left")
        deadband_spin = tk.Spinbox(
            tune_row,
            from_=0.05,
            to=1.50,
            increment=0.05,
            width=6,
            textvariable=self.gui.lyric_sync_deadband_var,
            bg=_PREFS_RAISED,
            fg=_PREFS_TEXT,
            buttonbackground=_PREFS_RAISED,
            command=self.gui._apply_lyric_sync_tuning,
        )
        deadband_spin.pack(side="left", padx=(8, 12))
        deadband_spin.bind("<FocusOut>", lambda _e: self.gui._apply_lyric_sync_tuning())

        tk.Label(tune_row, text="Max correction step (s)", bg=_PREFS_PANEL, fg=_PREFS_DIM).pack(side="left")
        max_step_spin = tk.Spinbox(
            tune_row,
            from_=0.05,
            to=2.00,
            increment=0.05,
            width=6,
            textvariable=self.gui.lyric_sync_max_step_var,
            bg=_PREFS_RAISED,
            fg=_PREFS_TEXT,
            buttonbackground=_PREFS_RAISED,
            command=self.gui._apply_lyric_sync_tuning,
        )
        max_step_spin.pack(side="left", padx=(8, 0))
        max_step_spin.bind("<FocusOut>", lambda _e: self.gui._apply_lyric_sync_tuning())

        tk.Label(
            tab,
            text=(
                "Lower deadband = tighter lock (more reactive). Higher deadband = fewer micro-adjustments.\n"
                "Max correction step limits how much each re-sync can move lyrics to avoid visible jumps."
            ),
            bg=_PREFS_PANEL,
            fg=_PREFS_DIM,
            font=("Segoe UI", 8),
            wraplength=520,
            justify="left",
        ).pack(anchor="w", padx=12, pady=(0, 6))

    def _build_audio_tab(self, tab):
        walk = """Audio input — quick walkthrough (Windows)

1) Pick the device that actually hears your mix: microphone, stereo mix, or a virtual
   cable (Voicemeeter OUT / VB-Audio) that carries DJ or room audio.

2) If the list is empty or wrong, install PyAudio and reconnect devices, then click
   “Refresh device list” at the bottom of this window.

3) Exclusive mode: close other apps that might lock the device (some DAWs, voice chat).

4) If beat detection stops with “errno -9988” or “host error”, choose another device,
   save, then STOP → START on the main window.

5) Beat sensitivity: higher = more kick peaks count as beats (tune to your genre).

6) Librosa tempo: optional smoother tempo estimate (adds processing; experimental).
"""
        wrap = tk.Frame(tab, bg=_PREFS_PANEL)
        wrap.pack(fill="both", expand=True, padx=8, pady=8)
        tk.Label(wrap, text="Walkthrough", bg=_PREFS_PANEL, fg=_PREFS_TEXT, font=("Segoe UI", 10, "bold")).pack(
            anchor="w"
        )
        txt = tk.Text(
            wrap,
            height=11,
            width=64,
            bg="#16181b",
            fg=_PREFS_DIM,
            font=("Segoe UI", 9),
            wrap="word",
            relief="flat",
            highlightthickness=1,
            highlightbackground="#3a4048",
        )
        txt.pack(fill="both", expand=True, pady=4)
        txt.insert("1.0", walk)
        txt.configure(state="disabled")

        dev_fr = tk.Frame(tab, bg=_PREFS_PANEL)
        dev_fr.pack(fill="x", padx=12, pady=6)
        tk.Label(dev_fr, text="Input device", bg=_PREFS_PANEL, fg=_PREFS_DIM).pack(side="left")
        self.gui.device_combo = ttk.Combobox(dev_fr, state="readonly", width=48)
        self.gui.device_combo.pack(side="left", padx=8, fill="x", expand=True)
        self.gui.device_combo.bind("<<ComboboxSelected>>", self.gui._on_device_selected)
        self.gui._apply_device_combo_values()

        fft_row = tk.Frame(tab, bg=_PREFS_PANEL)
        fft_row.pack(fill="x", padx=12, pady=(0, 6))
        ttk.Button(
            fft_row,
            text="Open FFT input monitor",
            command=self.gui._open_fft_monitor,
            style="Small.TButton",
        ).pack(side="left")
        tk.Label(
            fft_row,
            text="Use this to confirm the selected input device is receiving audio.",
            bg=_PREFS_PANEL,
            fg=_PREFS_DIM,
            font=("Segoe UI", 8),
        ).pack(side="left", padx=8)

        sens_fr = tk.Frame(tab, bg=_PREFS_PANEL)
        sens_fr.pack(fill="x", padx=12, pady=4)
        tk.Label(sens_fr, text="Beat sensitivity", bg=_PREFS_PANEL, fg=_PREFS_DIM).pack(side="left")
        ttk.Scale(
            sens_fr,
            from_=0.0,
            to=1.0,
            variable=self.gui.sensitivity_var,
            orient="horizontal",
            command=self.gui._on_sensitivity,
        ).pack(side="left", fill="x", expand=True, padx=8)

        tk.Checkbutton(
            tab,
            text="Librosa tempo (optional)",
            variable=self.gui.chk_librosa,
            command=self.gui._on_librosa_toggle,
            bg=_PREFS_PANEL,
            fg=_PREFS_TEXT,
            selectcolor=_PREFS_RAISED,
            activebackground=_PREFS_PANEL,
            activeforeground=_PREFS_TEXT,
        ).pack(anchor="w", padx=12, pady=6)


class LyricGenGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(f"Lyric Gen v{__version__}")
        self.root.configure(bg="#1a1a2e")
        self.root.geometry("580x820")
        self.root.minsize(480, 720)
        self.root.resizable(True, True)

        self.prefs = load_preferences()
        self.app = None
        self.running = False
        self._backend_thread = None
        self.device_combo = None
        self._prefs_win = None
        self._fft_win = None

        # Tracked state
        self.bpm_var = tk.StringVar(value="---.--")
        self.detected_bpm_var = tk.StringVar(value="Detected: ---.--")
        self.output_bpm_var = tk.StringVar(value="Output: ---.--")
        self.song_var = tk.StringVar(value="No song detected")
        self.artist_var = tk.StringVar(value="")
        self._candidates = []  # ACRCloud alternative matches
        self.candidate_var = tk.StringVar(value="")
        self.lyric_var = tk.StringVar(value="")
        self.lyric_next_var = tk.StringVar(value="")
        self.lyric_progress = tk.DoubleVar(value=0.0)
        self.status_var = tk.StringVar(value="Ready — press START")
        self.sensitivity_var = tk.DoubleVar(value=self.prefs["beat"]["sensitivity"])
        self.timing_offset_var = tk.DoubleVar(value=self.prefs["lyrics"]["offset_sec"])
        self.timing_label_var = tk.StringVar(value=f'{self.prefs["lyrics"]["offset_sec"]:+.1f}s')
        self.chk_alternate = tk.BooleanVar(value=self.prefs["lyrics"]["ab_alternation"])
        self.lyric_sync_deadband_var = tk.DoubleVar(value=float(self.prefs["lyrics"].get("sync_deadband_sec", 0.25)))
        self.lyric_sync_max_step_var = tk.DoubleVar(value=float(self.prefs["lyrics"].get("sync_max_step_sec", 0.35)))
        self.chk_librosa = tk.BooleanVar(value=self.prefs["beat"].get("use_librosa_tempo", False))

        # Resolume connection
        r = self.prefs["resolume"]
        self.resolume_ip_var = tk.StringVar(value=r.get("ip", "127.0.0.1"))
        self.resolume_port_var = tk.StringVar(value=str(int(r.get("osc_port", 7000))))
        self.listen_port_var = tk.StringVar(value=str(int(r.get("listen_port", 7020))))
        self.chk_send_bpm = tk.BooleanVar(value=bool(r.get("send_bpm", True)))

        # Resolume target vars (layers / clips)
        self.lyric_layer_var = tk.IntVar(value=self.prefs["resolume"]["lyric_layer"])
        self.lyric_clip_var = tk.IntVar(value=self.prefs["resolume"]["lyric_clip"])
        self.next_layer_var = tk.IntVar(value=self.prefs["resolume"]["next_lyric_layer"])
        self.next_clip_var = tk.IntVar(value=self.prefs["resolume"]["next_lyric_clip"])

        # ACRCloud (song ID)
        acr = self.prefs["acrcloud"]
        self.acr_host_var = tk.StringVar(value=acr.get("host", "") or "")
        self.acr_key_var = tk.StringVar(value=acr.get("access_key", "") or "")
        self.acr_secret_var = tk.StringVar(value=acr.get("access_secret", "") or "")

        # Feature toggles
        self.chk_beats = tk.BooleanVar(value=True)
        self.chk_songid = tk.BooleanVar(value=True)
        self.chk_lyrics = tk.BooleanVar(value=True)
        self.chk_prodj = tk.BooleanVar(value=self.prefs["prodj"]["enabled"])
        self.chk_topmost = tk.BooleanVar(value=bool(self.prefs.get("ui", {}).get("always_on_top", False)))

        # Apply topmost immediately on startup if configured.
        self.root.wm_attributes("-topmost", bool(self.chk_topmost.get()))

        self._build_ui()
        self._refresh_device_list()
        self._setup_logging()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    @staticmethod
    def _parse_port(val: str, default: int) -> int:
        try:
            p = int(str(val).strip())
            return p if 1 <= p <= 65535 else default
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _parse_float(val, default: float, low: float | None = None, high: float | None = None) -> float:
        try:
            v = float(val)
        except (TypeError, ValueError):
            v = float(default)
        if low is not None:
            v = max(float(low), v)
        if high is not None:
            v = min(float(high), v)
        return v

    def _sync_prefs_from_gui(self) -> None:
        """Write all GUI fields into `prefs` and persist."""
        self.prefs["resolume"]["ip"] = self.resolume_ip_var.get().strip() or "127.0.0.1"
        self.prefs["resolume"]["osc_port"] = self._parse_port(self.resolume_port_var.get(), 7000)
        self.prefs["resolume"]["listen_port"] = self._parse_port(self.listen_port_var.get(), 7020)
        self.prefs["resolume"]["send_bpm"] = bool(self.chk_send_bpm.get())
        self.prefs["resolume"]["lyric_layer"] = int(self.lyric_layer_var.get())
        self.prefs["resolume"]["lyric_clip"] = int(self.lyric_clip_var.get())
        self.prefs["resolume"]["next_lyric_layer"] = int(self.next_layer_var.get())
        self.prefs["resolume"]["next_lyric_clip"] = int(self.next_clip_var.get())
        self.prefs["acrcloud"]["host"] = self.acr_host_var.get().strip()
        self.prefs["acrcloud"]["access_key"] = self.acr_key_var.get().strip()
        self.prefs["acrcloud"]["access_secret"] = self.acr_secret_var.get().strip()
        self.prefs["beat"]["use_librosa_tempo"] = bool(self.chk_librosa.get())
        self.prefs["beat"]["sensitivity"] = float(self.sensitivity_var.get())
        self.prefs["lyrics"]["ab_alternation"] = bool(self.chk_alternate.get())
        self.prefs["lyrics"]["offset_sec"] = float(self.timing_offset_var.get())
        self.prefs["lyrics"]["sync_deadband_sec"] = self._parse_float(
            self.lyric_sync_deadband_var.get(), 0.25, 0.05, 1.5
        )
        self.prefs["lyrics"]["sync_max_step_sec"] = self._parse_float(
            self.lyric_sync_max_step_var.get(), 0.35, 0.05, 2.0
        )
        self.prefs["prodj"]["enabled"] = bool(self.chk_prodj.get())
        self.prefs.setdefault("ui", {})["always_on_top"] = bool(self.chk_topmost.get())
        save_preferences(self.prefs)

    def _save_connection_fields(self) -> None:
        self.prefs["resolume"]["send_bpm"] = bool(self.chk_send_bpm.get())
        save_preferences(self.prefs)

    def _on_librosa_toggle(self) -> None:
        self.prefs["beat"]["use_librosa_tempo"] = bool(self.chk_librosa.get())
        save_preferences(self.prefs)

    def _set_lyric_ui(self, cur: str, nxt: str, prog: float) -> None:
        self.lyric_var.set(cur)
        self.lyric_next_var.set(nxt)
        try:
            p = float(prog)
        except (TypeError, ValueError):
            p = 0.0
        self.lyric_progress.set(max(0.0, min(1.0, p)))

    # --------------------------------------------------------
    # UI LAYOUT
    # --------------------------------------------------------

    def _build_ui(self):
        style = ttk.Style()
        style.theme_use("clam")

        # Dark UI palette
        bg = "#1b1d20"
        panel = "#2a2d31"
        panel_raised = "#31353a"
        accent = "#59c9b5"
        accent_alt = "#6ce0cc"
        text = "#d7dbe0"
        dim = "#8a919a"
        border = "#3a4048"

        style.configure("Card.TFrame", background=panel)
        style.configure("BG.TFrame", background=bg)
        style.configure("Title.TLabel", background=bg, foreground=accent_alt,
                        font=("Segoe UI", 15, "bold"))
        style.configure("BPM.TLabel", background=panel, foreground=accent_alt,
                        font=("Consolas", 50, "bold"))
        style.configure("Song.TLabel", background=panel, foreground=text,
                        font=("Segoe UI", 14, "bold"))
        style.configure("Artist.TLabel", background=panel, foreground=dim,
                        font=("Segoe UI", 11))
        style.configure("Lyric.TLabel", background=panel, foreground="#f4f4f4",
                        font=("Segoe UI", 13))
        style.configure("LyricNext.TLabel", background=panel, foreground="#95a0ac",
                        font=("Segoe UI", 10))
        style.configure("Status.TLabel", background=bg, foreground=dim,
                        font=("Segoe UI", 9))
        style.configure("Label.TLabel", background=panel, foreground=dim,
                        font=("Segoe UI", 9))
        style.configure("Beat.TLabel", background=panel, foreground="#4a5058",
                        font=("Consolas", 18, "bold"))
        style.configure("BeatOn.TLabel", background=panel, foreground=accent_alt,
                        font=("Consolas", 18, "bold"))
        style.configure("Accent.TButton",
                        font=("Segoe UI", 10, "bold"),
                        foreground="#101214", background=accent,
                        bordercolor=border, lightcolor=accent,
                        darkcolor=accent, relief="flat", padding=6)
        style.map("Accent.TButton",
                  background=[("active", accent_alt), ("pressed", accent)],
                  foreground=[("disabled", "#57606a")])
        style.configure("Small.TButton",
                        font=("Segoe UI", 9), foreground=text,
                        background=panel_raised, bordercolor=border,
                        lightcolor=panel_raised, darkcolor=panel_raised,
                        relief="flat", padding=4)
        style.map("Small.TButton",
                  background=[("active", "#3a3f45"), ("pressed", "#30343a")],
                  foreground=[("disabled", "#57606a")])
        style.configure(
            "TProgressbar",
            troughcolor=panel_raised,
            background=accent,
            thickness=10,
            borderwidth=0,
        )

        main = ttk.Frame(self.root, style="BG.TFrame")
        main.pack(fill="both", expand=True, padx=12, pady=12)

        # -- Header --
        head = ttk.Frame(main, style="BG.TFrame")
        head.pack(fill="x", pady=(0, 8))
        ttk.Label(head, text="LYRIC GEN", style="Title.TLabel").pack(side="left")
        ttk.Label(head, text=f"v{__version__}", style="Status.TLabel").pack(side="right")

        # -- BPM Card --
        bpm_card = ttk.Frame(main, style="Card.TFrame")
        bpm_card.pack(fill="x", pady=4, ipady=8)
        ttk.Label(bpm_card, text="BPM", style="Label.TLabel").pack()
        ttk.Label(bpm_card, textvariable=self.bpm_var, style="BPM.TLabel").pack()
        bpm_meta = ttk.Frame(bpm_card, style="Card.TFrame")
        bpm_meta.pack(pady=(0, 2))
        ttk.Label(
            bpm_meta,
            textvariable=self.detected_bpm_var,
            style="Label.TLabel",
        ).pack(side="left", padx=8)
        ttk.Label(
            bpm_meta,
            textvariable=self.output_bpm_var,
            style="Label.TLabel",
        ).pack(side="left", padx=8)

        beat_frame = ttk.Frame(bpm_card, style="Card.TFrame")
        beat_frame.pack(pady=(0, 4))
        self.beat_labels = []
        for _ in range(4):
            lbl = ttk.Label(beat_frame, text="\u25cf", style="Beat.TLabel")
            lbl.pack(side="left", padx=8)
            self.beat_labels.append(lbl)

        bpm_ctrl = ttk.Frame(bpm_card, style="Card.TFrame")
        bpm_ctrl.pack(pady=4)
        self.lock_btn = ttk.Button(bpm_ctrl, text="BPM Lock",
                                   command=self._toggle_lock, style="Small.TButton")
        self.lock_btn.pack(side="left", padx=4)
        ttk.Button(bpm_ctrl, text="Resync", command=self._bpm_resync,
                   style="Small.TButton").pack(side="left", padx=4)
        ttk.Button(bpm_ctrl, text="\u00f72", command=self._bpm_half,
                   style="Small.TButton").pack(side="left", padx=4)
        ttk.Button(bpm_ctrl, text="\u00d72", command=self._bpm_double,
                   style="Small.TButton").pack(side="left", padx=4)

        # -- Song Card --
        song_card = ttk.Frame(main, style="Card.TFrame")
        song_card.pack(fill="x", pady=4, ipady=8)
        ttk.Label(song_card, text="NOW PLAYING", style="Label.TLabel").pack()
        ttk.Label(song_card, textvariable=self.song_var, style="Song.TLabel",
                  wraplength=500).pack(padx=8)
        ttk.Label(song_card, textvariable=self.artist_var, style="Artist.TLabel").pack()

        # Alternative matches dropdown
        alt_frame = ttk.Frame(song_card, style="Card.TFrame")
        alt_frame.pack(fill="x", padx=12, pady=(6, 0))
        ttk.Label(alt_frame, text="Alt match:", style="Label.TLabel").pack(side="left")
        self.candidate_combo = ttk.Combobox(
            alt_frame,
            textvariable=self.candidate_var,
            state="readonly",
            width=50,
        )
        self.candidate_combo.pack(side="left", padx=(4, 0), fill="x", expand=True)
        self.candidate_combo.bind("<<ComboboxSelected>>", self._on_candidate_selected)

        # -- Lyrics Card --
        lyric_card = ttk.Frame(main, style="Card.TFrame")
        lyric_card.pack(fill="x", pady=4, ipady=12)
        ttk.Label(lyric_card, text="LYRICS", style="Label.TLabel").pack()
        ttk.Label(lyric_card, textvariable=self.lyric_var, style="Lyric.TLabel",
                  wraplength=500).pack(padx=8, pady=(4, 0))
        ttk.Label(lyric_card, textvariable=self.lyric_next_var, style="LyricNext.TLabel",
                  wraplength=500).pack(padx=8)
        ttk.Progressbar(
            lyric_card,
            variable=self.lyric_progress,
            maximum=1.0,
            length=480,
            mode="determinate",
        ).pack(fill="x", padx=12, pady=(8, 0))

        lyric_btn = ttk.Frame(lyric_card, style="Card.TFrame")
        lyric_btn.pack(pady=(4, 0))
        ttk.Button(lyric_btn, text="Toggle Lyrics", command=self._toggle_lyrics,
                   style="Small.TButton").pack(side="left", padx=4)
        tk.Checkbutton(lyric_btn, text="A/B Alternate",
                       variable=self.chk_alternate, command=self._on_alternate,
                       bg=panel, fg=dim, selectcolor=panel,
                       activebackground=panel, activeforeground=text).pack(side="left", padx=4)

        # Timing
        timing_frame = ttk.Frame(lyric_card, style="Card.TFrame")
        timing_frame.pack(fill="x", padx=12, pady=(6, 0))
        ttk.Label(timing_frame, text="Timing:", style="Label.TLabel").pack(side="left")
        ttk.Label(timing_frame, textvariable=self.timing_label_var, style="Label.TLabel").pack(side="left", padx=(4, 8))
        for label, delta in [("-2s", -2), ("-1s", -1), ("-0.5s", -0.5)]:
            ttk.Button(timing_frame, text=label, width=4,
                       command=lambda d=delta: self._adjust_timing(d),
                       style="Small.TButton").pack(side="left", padx=1)
        ttk.Button(timing_frame, text="Reset", width=5,
                   command=self._reset_timing, style="Small.TButton").pack(side="left", padx=4)
        for label, delta in [("+0.5s", 0.5), ("+1s", 1), ("+2s", 2)]:
            ttk.Button(timing_frame, text=label, width=4,
                       command=lambda d=delta: self._adjust_timing(d),
                       style="Small.TButton").pack(side="left", padx=1)

        ttk.Label(
            main,
            text="Resolume, song ID, and audio: Preferences…",
            style="Status.TLabel",
        ).pack(anchor="w", pady=(0, 4))

        # -- Engine toggles (quick access) --
        ctrl_card = ttk.Frame(main, style="Card.TFrame")
        ctrl_card.pack(fill="x", pady=4, ipady=8)

        chk_frame = ttk.Frame(ctrl_card, style="Card.TFrame")
        chk_frame.pack(fill="x", padx=12, pady=4)
        tk.Checkbutton(chk_frame, text="Beats", variable=self.chk_beats,
                       bg=panel, fg=text, selectcolor=panel,
                       activebackground=panel, activeforeground=text).pack(side="left", padx=4)
        tk.Checkbutton(chk_frame, text="Song ID", variable=self.chk_songid,
                       bg=panel, fg=text, selectcolor=panel,
                       activebackground=panel, activeforeground=text).pack(side="left", padx=4)
        tk.Checkbutton(chk_frame, text="Lyrics", variable=self.chk_lyrics,
                       bg=panel, fg=text, selectcolor=panel,
                       activebackground=panel, activeforeground=text).pack(side="left", padx=4)
        tk.Checkbutton(chk_frame, text="DJ Link", variable=self.chk_prodj,
                       bg=panel, fg=text, selectcolor=panel,
                       activebackground=panel, activeforeground=text).pack(side="left", padx=4)
        tk.Checkbutton(
            chk_frame, text="Always on top",
            variable=self.chk_topmost,
            command=self._on_topmost_toggle,
            bg=panel, fg=text, selectcolor=panel,
            activebackground=panel, activeforeground=text,
        ).pack(side="left", padx=4)

        # -- Action buttons --
        btn_frame = ttk.Frame(main, style="BG.TFrame")
        btn_frame.pack(fill="x", pady=8)
        self.start_btn = ttk.Button(btn_frame, text="\u25b6  START", command=self._start,
                                    style="Accent.TButton")
        self.start_btn.pack(side="left", expand=True, fill="x", padx=4)
        self.stop_btn = ttk.Button(btn_frame, text="\u25a0  STOP", command=self._stop,
                                   style="Accent.TButton", state="disabled")
        self.stop_btn.pack(side="left", expand=True, fill="x", padx=4)
        ttk.Button(btn_frame, text="Preferences…", command=self._open_preferences_dialog,
                   style="Small.TButton").pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Rescan", command=self._rescan,
                   style="Small.TButton").pack(side="left", padx=4)
        ttk.Button(btn_frame, text="About", command=self._show_about,
                   style="Small.TButton").pack(side="left", padx=4)

        # -- Status + Log --
        ttk.Label(main, textvariable=self.status_var, style="Status.TLabel").pack(pady=(4, 0))
        self.log_text = tk.Text(main, height=4, bg="#16181b", fg="#8e96a0",
                                font=("Consolas", 8), state="disabled", wrap="word",
                                borderwidth=1, insertbackground="#c8ced6",
                                highlightthickness=1, highlightbackground=border,
                                highlightcolor=border)
        self.log_text.pack(fill="x", pady=(4, 0))

    # --------------------------------------------------------
    # DEVICES
    # --------------------------------------------------------

    def _refresh_device_list(self) -> None:
        devices = []
        try:
            import pyaudio

            pa = pyaudio.PyAudio()
            for i in range(pa.get_device_count()):
                info = pa.get_device_info_by_index(i)
                if info["maxInputChannels"] > 0:
                    devices.append((i, info["name"]))
            pa.terminate()
        except Exception:
            devices = [(0, "Default")]

        self.devices = devices
        self._apply_device_combo_values()

    def _get_live_device_combo(self):
        """Return the audio device combobox only if the Tk widget still exists."""
        combo = self.device_combo
        if combo is None:
            return None
        try:
            if not combo.winfo_exists():
                self.device_combo = None
                return None
        except tk.TclError:
            self.device_combo = None
            return None
        return combo

    def _apply_device_combo_values(self) -> None:
        combo = self._get_live_device_combo()
        if combo is None:
            return
        devices = getattr(self, "devices", [])
        try:
            combo["values"] = [f"[{i}] {name}" for i, name in devices]
        except tk.TclError:
            self.device_combo = None
            return
        if devices:
            configured = self.prefs["audio"]["device_index"]
            default_pos = next(
                (pos for pos, (idx, _) in enumerate(devices) if idx == configured), 0
            )
            try:
                combo.current(default_pos)
            except tk.TclError:
                self.device_combo = None

    def _on_device_selected(self, _evt=None) -> None:
        combo = self._get_live_device_combo()
        if combo is None or not getattr(self, "devices", None):
            return
        try:
            cur = combo.current()
        except tk.TclError:
            self.device_combo = None
            return
        if cur >= 0:
            self.prefs["audio"]["device_index"] = int(self.devices[cur][0])
            save_preferences(self.prefs)

    def _open_preferences_dialog(self) -> None:
        win = self._prefs_win
        if win is not None and win.winfo_exists():
            win.lift()
            win.focus_force()
            return
        PreferencesWindow(self.root, self)

    def _open_fft_monitor(self) -> None:
        win = self._fft_win
        if win is not None and win.winfo_exists():
            win.lift()
            win.focus_force()
            return
        AudioFFTMonitor(self.root, self)

    # --------------------------------------------------------
    # ACTIONS
    # --------------------------------------------------------

    def _start(self):
        if self.running:
            return

        self._sync_prefs_from_gui()
        combo = self._get_live_device_combo()
        if combo is not None and self.devices:
            try:
                cur = combo.current()
            except tk.TclError:
                self.device_combo = None
                cur = -1
            if cur >= 0:
                idx = int(self.devices[cur][0])
                self.prefs["audio"]["device_index"] = idx
                save_preferences(self.prefs)

        self.app = LyricGenApp(prefs=self.prefs)
        self._hook_callbacks()

        self.running = True
        self._backend_thread = threading.Thread(target=self._run_backend, daemon=True)
        self._backend_thread.start()

        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        combo = self._get_live_device_combo()
        if combo is not None:
            try:
                combo.configure(state="disabled")
            except tk.TclError:
                self.device_combo = None
        self.status_var.set("Running — audio + OSC active")
        self._log("Started")

    def _run_backend(self):
        try:
            self.app.start_all(
                beats=self.chk_beats.get(),
                song_id=self.chk_songid.get(),
                lyrics=self.chk_lyrics.get(),
                prodj=self.chk_prodj.get(),
            )
            while self.running:
                time.sleep(0.5)
        except Exception as exc:
            logger.exception("Backend failed to start: %s", exc)

            def _mark_failed():
                self.running = False
                self.start_btn.configure(state="normal")
                self.stop_btn.configure(state="disabled")
                combo = self._get_live_device_combo()
                if combo is not None:
                    try:
                        combo.configure(state="readonly")
                    except tk.TclError:
                        self.device_combo = None
                self.status_var.set("Failed to start — check log")
                self._log(f"Start failed: {exc}")

            self.root.after(0, _mark_failed)

    def _stop(self, blocking: bool = False):
        if not self.running:
            return
        self.running = False
        app = self.app
        if app:
            if blocking:
                try:
                    app.stop_all()
                except Exception as exc:
                    logger.error("Error while stopping services: %s", exc)
            else:
                threading.Thread(target=app.stop_all, daemon=True).start()
        if self._backend_thread and self._backend_thread.is_alive():
            self._backend_thread.join(timeout=2.0)
        self.app = None
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        combo = self._get_live_device_combo()
        if combo is not None:
            try:
                combo.configure(state="readonly")
            except tk.TclError:
                self.device_combo = None
        self.bpm_var.set("---.--")
        self.detected_bpm_var.set("Detected: ---.--")
        self.output_bpm_var.set("Output: ---.--")
        self.song_var.set("No song detected")
        self.artist_var.set("")
        self.lyric_var.set("")
        self.lyric_next_var.set("")
        self.lyric_progress.set(0.0)
        self._reset_beats()
        self.status_var.set("Stopped — press START")
        self._log("Stopped")

    def _toggle_lock(self):
        if not self.app:
            return
        bt = self.app.beat_tracker
        if bt.bpm_locked:
            bt.unlock_bpm()
            self.lock_btn.configure(text="BPM Lock")
        else:
            bt.lock_bpm()
            self.lock_btn.configure(text="BPM Unlock")

    def _bpm_half(self):
        if self.app:
            self.app.beat_tracker.halve_bpm()
            self.root.after(0, self._refresh_bpm_meta_ui)

    def _bpm_double(self):
        if self.app:
            self.app.beat_tracker.double_bpm()
            self.root.after(0, self._refresh_bpm_meta_ui)

    def _bpm_resync(self):
        if self.app:
            self.app.beat_tracker.resync_beat()
            self.app.osc.send_tempo_resync()
            self.root.after(0, self._refresh_bpm_meta_ui)

    def _toggle_lyrics(self):
        if self.app:
            self.app.lyrics_engine.toggle()

    def _rescan(self):
        if self.app:
            self.song_var.set("Rescanning…")
            self.artist_var.set("")
            self.lyric_var.set("")
            self.lyric_next_var.set("")
            self.lyric_progress.set(0.0)
            self._candidates = []
            self.candidate_combo["values"] = []
            self.candidate_var.set("")
            self.app.lyrics_engine.clear()
            self.app.osc.clear_lyrics()
            self.app.song_id.force_scan()

    def _on_candidates(self, candidates):
        """Called from song_id thread with all ACRCloud matches."""
        self._candidates = candidates
        display = [f"{c.get('score', 0)}%  {c['artist']} — {c['title']}" for c in candidates]
        self.root.after(0, lambda d=display: self._update_candidates_ui(d))

    def _update_candidates_ui(self, display_list):
        self.candidate_combo["values"] = display_list
        if display_list:
            self.candidate_var.set(display_list[0])

    def _on_candidate_selected(self, event=None):
        """User picked an alternative track from the dropdown."""
        idx = self.candidate_combo.current()
        if idx < 0 or idx >= len(self._candidates):
            return
        c = self._candidates[idx]
        title, artist, offset_ms = c["title"], c["artist"], c["offset_ms"]
        logger.info("Manual track select: %s - %s", artist, title)
        self.song_var.set(title)
        self.artist_var.set(artist)
        if self.app:
            # Lock in manual pick until ACRCloud detects a genuinely different song
            self.app.song_id._manual_override = True
            # Trigger lyrics for the selected track, keeping current timing
            self.app.lyrics_engine.clear()
            self.app.osc.clear_lyrics()
            self.app.lyrics_engine.on_song_changed(title, artist, offset_ms, keep_timing=True)
            self.app.osc.send_song_info(title, artist)

    def _on_sensitivity(self, val):
        v = float(val)
        self.prefs["beat"]["sensitivity"] = v
        save_preferences(self.prefs)
        if self.app:
            self.app.beat_tracker.set_sensitivity(v)

    def _on_alternate(self):
        self.prefs["lyrics"]["ab_alternation"] = bool(self.chk_alternate.get())
        save_preferences(self.prefs)
        if self.app:
            self.app.lyrics_engine.alternate_clips = self.chk_alternate.get()

    def _on_topmost_toggle(self):
        enabled = bool(self.chk_topmost.get())
        self.root.wm_attributes("-topmost", enabled)
        self.prefs.setdefault("ui", {})["always_on_top"] = enabled
        save_preferences(self.prefs)

    def _apply_lyric_sync_tuning(self):
        deadband = self._parse_float(self.lyric_sync_deadband_var.get(), 0.25, 0.05, 1.5)
        max_step = self._parse_float(self.lyric_sync_max_step_var.get(), 0.35, 0.05, 2.0)
        self.lyric_sync_deadband_var.set(round(deadband, 2))
        self.lyric_sync_max_step_var.set(round(max_step, 2))
        self.prefs["lyrics"]["sync_deadband_sec"] = deadband
        self.prefs["lyrics"]["sync_max_step_sec"] = max_step
        save_preferences(self.prefs)
        if self.app:
            self.app.lyrics_engine.set_sync_tuning(deadband, max_step)

    def _adjust_timing(self, delta):
        if self.app:
            self.app.lyrics_engine.adjust_timing(delta)
            self.timing_offset_var.set(self.app.lyrics_engine.timing_offset)
        else:
            self.timing_offset_var.set(self.timing_offset_var.get() + delta)
        self._update_timing_label()

    def _reset_timing(self):
        if self.app:
            self.app.lyrics_engine.set_timing_offset(0.0)
        self.timing_offset_var.set(0.0)
        self._update_timing_label()

    def _on_timing_changed(self, offset):
        """Called from lyrics engine when timing offset changes (e.g. per-song memory load)."""
        self.root.after(0, lambda o=offset: self._apply_timing_ui(o))

    def _apply_timing_ui(self, offset):
        self.timing_offset_var.set(offset)
        self._update_timing_label()

    def _update_timing_label(self):
        v = self.timing_offset_var.get()
        self.timing_label_var.set(f"{v:+.1f}s")

    def _save_resolume_target(self):
        try:
            self.prefs["resolume"]["lyric_layer"] = int(self.lyric_layer_var.get())
            self.prefs["resolume"]["lyric_clip"] = int(self.lyric_clip_var.get())
            self.prefs["resolume"]["next_lyric_layer"] = int(self.next_layer_var.get())
            self.prefs["resolume"]["next_lyric_clip"] = int(self.next_clip_var.get())
        except (tk.TclError, ValueError):
            # Restore persisted values if a spinbox has invalid typed input.
            res = self.prefs["resolume"]
            self.lyric_layer_var.set(int(res["lyric_layer"]))
            self.lyric_clip_var.set(int(res["lyric_clip"]))
            self.next_layer_var.set(int(res["next_lyric_layer"]))
            self.next_clip_var.set(int(res["next_lyric_clip"]))
            return
        save_preferences(self.prefs)

    def _open_preferences_json(self) -> None:
        from lyric_gen.core.preferences import _prefs_path

        path = os.path.normpath(_prefs_path())
        try:
            os.startfile(path)
            self._log(f"Opened: {path}")
        except Exception as e:
            self._log(f"Failed to open preferences file: {e}")

    def _show_about(self):
        about = tk.Toplevel(self.root)
        about.title("About Lyric Gen")
        about.configure(bg="#1b1d20")
        about.geometry("400x300")
        about.resizable(False, False)

        text = (
            f"Lyric Gen v{__version__}\n\n"
            "Real-time song lyrics for Resolume Arena.\n"
            "Identifies songs via ACRCloud, fetches synced lyrics\n"
            "from LRCLIB, and sends them to Resolume via OSC.\n\n"
            "Tip: Preferences → Save all after editing keys.\n"
            "New ACRCloud credentials apply on next START.\n\n"
            "--- Credits ---\n"
            "Song recognition: ACRCloud (user account)\n"
            "Synced lyrics: LRCLIB (lrclib.net)\n"
            "python-prodj-link: Apache 2.0\n"
            "python-osc: Public Domain\n"
            "PyAudio/PortAudio: MIT\n"
            "librosa: ISC\n"
        )
        tk.Label(about, text=text, bg="#1b1d20", fg="#d7dbe0",
                 font=("Segoe UI", 9), justify="left").pack(padx=16, pady=16)
        ttk.Button(about, text="Close", command=about.destroy,
                   style="Small.TButton").pack(pady=8)

    # --------------------------------------------------------
    # HOOK CALLBACKS FOR GUI UPDATES
    # --------------------------------------------------------

    def _hook_callbacks(self):
        osc = self.app.osc
        orig_bpm = osc.send_bpm
        orig_beat = osc.send_beat
        orig_song = osc.send_song_info
        orig_lyric = osc.send_lyric
        orig_clear = osc.clear_lyrics

        def hooked_bpm(bpm):
            orig_bpm(bpm)
            self.root.after(0, lambda b=float(bpm): self._refresh_bpm_meta_ui(b))

        def hooked_beat(pos):
            orig_beat(pos)
            self.root.after(0, lambda: self._flash_beat(int(pos)))

        def hooked_song(title, artist):
            orig_song(title, artist)
            self.root.after(
                0,
                lambda t=title, a=artist: (self.song_var.set(t), self.artist_var.set(a)),
            )

        def hooked_lyric(cur, nxt="", prog=0.0):
            orig_lyric(cur, nxt, prog)
            self.root.after(
                0,
                lambda c=cur, n=nxt, p=prog: self._set_lyric_ui(c, n, p),
            )

        def hooked_clear():
            orig_clear()
            self.root.after(0, lambda: (self.lyric_var.set(""), self.lyric_next_var.set("")))

        osc.send_bpm = hooked_bpm
        osc.send_beat = hooked_beat
        osc.send_song_info = hooked_song
        osc.send_lyric = hooked_lyric
        osc.clear_lyrics = hooked_clear

        # Also refresh UI immediately when tempo controls come in from Resolume OSC.
        orig_cmd_half = osc.on_cmd_bpm_half
        orig_cmd_double = osc.on_cmd_bpm_double
        orig_cmd_resync = osc.on_cmd_bpm_resync

        def hooked_cmd_half():
            if orig_cmd_half:
                orig_cmd_half()
            self.root.after(0, self._refresh_bpm_meta_ui)

        def hooked_cmd_double():
            if orig_cmd_double:
                orig_cmd_double()
            self.root.after(0, self._refresh_bpm_meta_ui)

        def hooked_cmd_resync():
            if orig_cmd_resync:
                orig_cmd_resync()
            self.root.after(0, self._refresh_bpm_meta_ui)

        osc.on_cmd_bpm_half = hooked_cmd_half
        osc.on_cmd_bpm_double = hooked_cmd_double
        osc.on_cmd_bpm_resync = hooked_cmd_resync

        # Rebind module callbacks to the newly hooked OSC methods.
        # App wiring stores callable refs (not late-bound attributes), so if we don't
        # refresh here, song/BPM/lyrics UI labels won't receive updates.
        self.app.beat_tracker.on_bpm_update = osc.send_bpm
        self.app.song_id.on_song_info = osc.send_song_info
        self.app.lyrics_engine.on_lyric = osc.send_lyric
        self.app.lyrics_engine.on_lyrics_cleared = osc.clear_lyrics
        self.app.lyrics_engine.on_timing_changed = self._on_timing_changed
        self.app.prodj.on_bpm = osc.send_bpm
        self.app.prodj.on_beat = lambda pos: osc.send_beat(pos)
        self.app.song_id.on_candidates = self._on_candidates

    def _refresh_bpm_meta_ui(self, shown_bpm: float | None = None):
        if shown_bpm is not None:
            self.bpm_var.set(f"{shown_bpm:.1f}")
        bt = self.app.beat_tracker if self.app else None
        if not bt:
            return
        detected = float(bt.current_bpm)
        output = float(detected * bt.bpm_multiplier)
        self.detected_bpm_var.set(f"Detected: {detected:.1f}")
        self.output_bpm_var.set(f"Output: {output:.1f} (x{bt.bpm_multiplier:.1f})")

    def _flash_beat(self, pos):
        self._reset_beats()
        idx = (pos - 1) % 4
        if 0 <= idx < len(self.beat_labels):
            self.beat_labels[idx].configure(style="BeatOn.TLabel")
            self.root.after(150, self._reset_beats)

    def _reset_beats(self):
        for lbl in self.beat_labels:
            lbl.configure(style="Beat.TLabel")

    # --------------------------------------------------------
    # LOGGING
    # --------------------------------------------------------

    def _setup_logging(self):
        handler = _GUILogHandler(self)
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logging.getLogger("lyricgen").addHandler(handler)
        logging.getLogger("lyricgen").setLevel(logging.INFO)

    def _log(self, msg):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 100:
            self.log_text.delete("1.0", f"{lines - 100}.0")
        self.log_text.configure(state="disabled")

    # --------------------------------------------------------
    # RUN
    # --------------------------------------------------------

    def _on_close(self):
        win = self._fft_win
        if win is not None and win.winfo_exists():
            try:
                win.destroy()
            except Exception:
                pass
        self._stop(blocking=True)
        self.root.destroy()

    def run(self):
        self.root.mainloop()


class _GUILogHandler(logging.Handler):
    """Routes Python logging to the GUI log panel."""

    def __init__(self, gui):
        super().__init__()
        self.gui = gui

    def emit(self, record):
        msg = self.format(record)
        if msg.strip():
            try:
                self.gui.root.after(0, lambda: self.gui._log(msg))
            except Exception:
                pass


def run_gui():
    """Entry point for standalone GUI mode."""
    LyricGenGUI().run()
