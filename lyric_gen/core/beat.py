"""Beat detection — energy-based FFT with optional librosa tempo estimation."""

import time
import threading
import logging

import numpy as np

logger = logging.getLogger("lyricgen.beat")


class BeatTracker:
    """Real-time beat detector using low-frequency energy peaks.

    Ported from resolume-sync fallback detector. Suitable for electronic/dance
    music with strong, consistent kick patterns. No PyTorch dependency.

    Callbacks:
        on_beat(position: int, bpm: float) — called on each detected beat.
            position is 1-4 (cycling), bpm is the current smoothed estimate.
    """

    def __init__(self, prefs):
        self.prefs = prefs
        stab = prefs["bpm_stability"]

        # Tuning from preferences
        self.sensitivity = prefs["beat"]["sensitivity"]
        self._history_size = max(3, int(stab["history_size"]))
        self._smoothing_alpha = float(stab["smoothing_alpha"])
        self._send_threshold = float(stab["send_threshold"])
        self._min_emit_interval = float(stab.get("min_emit_interval_sec", 0.75))
        self._jump_limit = float(stab["jump_limit_bpm"])
        self._enforce_jump_limit = bool(stab["enforce_jump_limit"])
        self._max_change = float(stab["max_change_per_update"])
        self._bpm_min = float(stab["min_bpm"])
        self._bpm_max = float(stab["max_bpm"])
        # Output clamp can be wider than detection clamp (for manual x2 / /2 controls).
        res = prefs.get("resolume", {})
        self._out_bpm_min = float(res.get("bpm_min", self._bpm_min))
        self._out_bpm_max = float(res.get("bpm_max", self._bpm_max))
        self._harmonic_snap = bool(stab["harmonic_snap"])

        # State
        self.current_bpm = 120.0
        self.bpm_locked = False
        self.bpm_multiplier = 1.0
        self.running = False
        self._thread = None
        self._bpm_history = []
        self._last_beat_time = 0
        self._beat_count = 0
        self._last_sent_bpm = None
        self._last_emit_time = 0.0
        self._bpm_initialized = False
        self._force_downbeat = False

        # Callbacks
        self.on_beat = None  # (position: int, bpm: float) -> None
        self.on_bpm_update = None  # (bpm: float) -> None

    def start(self):
        if self.running:
            return
        try:
            import pyaudio  # noqa: F401
        except ImportError:
            logger.warning(
                "PyAudio not installed — beat detection disabled. "
                "From the lyric-gen folder run: pip install -r requirements.txt "
                "(or requirements-min.txt). "
                "(Windows: if build fails, use a PyAudio wheel matching your Python version)."
            )
            return
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("Beat tracker started")

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join(timeout=2)
        logger.info("Beat tracker stopped")

    def lock_bpm(self):
        self.bpm_locked = True
        logger.info("BPM locked at %.1f", self.current_bpm)

    def unlock_bpm(self):
        self.bpm_locked = False
        logger.info("BPM unlocked")

    def halve_bpm(self):
        self.bpm_multiplier *= 0.5
        self._emit_bpm(force=True)
        logger.info("Multiplier: %.1fx", self.bpm_multiplier)

    def double_bpm(self):
        self.bpm_multiplier *= 2.0
        self._emit_bpm(force=True)
        logger.info("Multiplier: %.1fx", self.bpm_multiplier)

    def set_sensitivity(self, v):
        self.sensitivity = max(0.0, min(1.0, v))

    def resync_beat(self):
        self._force_downbeat = True
        self._beat_count = 0
        logger.info("Manual beat resync")

    # ----- Internal -----

    def _clamp(self, bpm):
        return max(self._bpm_min, min(self._bpm_max, float(bpm)))

    def _clamp_output(self, bpm):
        return max(self._out_bpm_min, min(self._out_bpm_max, float(bpm)))

    def _normalize_harmonic(self, bpm):
        if not self._harmonic_snap or self.current_bpm <= 0:
            return bpm
        candidate = float(bpm)
        ref = float(self.current_bpm)
        while candidate < ref * 0.67:
            candidate *= 2.0
        while candidate > ref * 1.5:
            candidate *= 0.5
        return candidate

    def _apply_slew(self, target):
        delta = target - self.current_bpm
        step = max(0.1, self._max_change)
        if delta > step:
            return self.current_bpm + step
        if delta < -step:
            return self.current_bpm - step
        return target

    def _emit_bpm(self, force: bool = False):
        effective = self._effective_bpm()
        now = time.time()
        if not force and now - self._last_emit_time < max(0.0, self._min_emit_interval):
            return
        if self._last_sent_bpm is None or abs(effective - self._last_sent_bpm) >= self._send_threshold:
            if self.on_bpm_update:
                self.on_bpm_update(effective)
            self._last_sent_bpm = effective
            self._last_emit_time = now

    def _effective_bpm(self):
        """Apply multiplier then clamp to output range."""
        return self._clamp_output(self.current_bpm * self.bpm_multiplier)

    def _update_bpm(self, now):
        if self._last_beat_time > 0:
            interval = now - self._last_beat_time
            if 0.2 < interval < 2.0:
                candidate = self._clamp(self._normalize_harmonic(60.0 / interval))
                self._bpm_history.append(candidate)
                if len(self._bpm_history) > self._history_size:
                    self._bpm_history.pop(0)
                if not self.bpm_locked:
                    median = float(np.median(self._bpm_history))
                    if not self._bpm_initialized:
                        if len(self._bpm_history) >= max(4, self._history_size // 2):
                            self.current_bpm = self._clamp(median)
                            self._bpm_initialized = True
                            self._emit_bpm()
                        self._last_beat_time = now
                        return
                    if self._enforce_jump_limit and abs(median - self.current_bpm) > self._jump_limit:
                        self._last_beat_time = now
                        return
                    alpha = max(0.01, min(1.0, self._smoothing_alpha))
                    smoothed = self._clamp((1.0 - alpha) * self.current_bpm + alpha * median)
                    self.current_bpm = self._clamp(self._apply_slew(smoothed))
                    self._emit_bpm()
        self._last_beat_time = now

    def _run(self):
        try:
            import pyaudio
        except ImportError:
            logger.warning(
                "PyAudio not available in beat thread — beat detection disabled. "
                "From the lyric-gen folder run: pip install -r requirements.txt "
                "(Windows: if build fails, use a PyAudio wheel matching your Python version)."
            )
            self.running = False
            return
        except OSError as e:
            logger.warning(
                "PyAudio failed to load in beat thread (%s) — beat detection disabled.",
                e,
            )
            self.running = False
            return
        except Exception as e:
            logger.error("Unexpected error loading PyAudio in beat thread: %s", e)
            self.running = False
            return

        device_index = self.prefs["audio"]["device_index"]
        sample_rate = self.prefs["audio"]["sample_rate"]
        channels = self.prefs["audio"]["channels"]

        pa = pyaudio.PyAudio()
        try:
            stream = pa.open(
                format=pyaudio.paFloat32,
                channels=channels,
                rate=sample_rate,
                input=True,
                input_device_index=device_index,
                frames_per_buffer=1024,
            )
        except Exception as e:
            logger.error("Failed to open audio device %s: %s", device_index, e)
            pa.terminate()
            return

        energy_history = []
        energy_window = 43
        logger.info("Energy beat detection running (device %s)", device_index)

        try:
            while self.running:
                try:
                    data = stream.read(1024, exception_on_overflow=False)
                    samples = np.frombuffer(data, dtype=np.float32)
                    fft = np.abs(np.fft.rfft(samples))
                    low_energy = float(np.sum(fft[:10]))
                    energy_history.append(low_energy)
                    if len(energy_history) > energy_window:
                        energy_history.pop(0)
                    if len(energy_history) >= energy_window:
                        avg = np.mean(energy_history)
                        if low_energy > avg * (1.0 + self.sensitivity):
                            now = time.time()
                            if now - self._last_beat_time > 0.2:
                                self._update_bpm(now)
                                if self._force_downbeat:
                                    self._force_downbeat = False
                                    self._beat_count = 0
                                    pos = 1
                                else:
                                    self._beat_count += 1
                                    pos = (self._beat_count % 4) + 1
                                if self.on_beat:
                                    self.on_beat(pos, self._effective_bpm())
                except OSError as e:
                    # PortAudio (Windows): e.g. -9988 stream closed, -9999 host error — stop
                    # reading a dead stream (avoids log spam every 100 ms).
                    if self.running:
                        no = getattr(e, "errno", None)
                        logger.error(
                            "Beat detection stopped (%s). "
                            "Pick another input device in the GUI or close apps using this mic, then START again.",
                            e if no is None else f"errno {no}: {e}",
                        )
                    break
                except Exception as e:
                    if self.running:
                        logger.error("Beat error: %s", e)
                        time.sleep(0.1)
        finally:
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
