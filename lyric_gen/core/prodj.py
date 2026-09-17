"""Pioneer Pro DJ Link bridge — optional CDJ/DJM integration."""

import time
import threading
import logging

logger = logging.getLogger("lyricgen.prodj")


class ProDJLinkBridge:
    """Reads BPM, beat, and track metadata from Pioneer CDJs via Pro DJ Link.

    Optional module — disabled by default. Requires python-prodj-link.
    When active, provides BPM from the DJ equipment (more accurate than
    audio analysis) and track metadata (can skip ACRCloud for song ID).

    Callbacks:
        on_bpm(bpm: float) — BPM from master player.
        on_beat(position: int) — beat position from master.
        on_track_changed(title: str, artist: str) — metadata from rekordbox.
    """

    def __init__(self, prefs):
        self.prefs = prefs
        prodj_prefs = prefs["prodj"]
        stab = prefs["bpm_stability"]

        self._player_number = prodj_prefs["player_number"]
        self._smoothing_alpha = float(stab["smoothing_alpha"])
        self._max_change = float(stab["max_change_per_update"])
        self._send_threshold = float(stab["send_threshold"])
        self._bpm_min = float(stab["min_bpm"])
        self._bpm_max = float(stab["max_bpm"])

        self.running = False
        self._thread = None
        self._prodj = None
        self.current_bpm = 0.0
        self.current_track = {}
        self.players = {}
        self._players_lock = threading.Lock()
        self._smoothed_bpm = None
        self._last_sent_bpm = None

        # Callbacks
        self.on_bpm = None
        self.on_beat = None
        self.on_track_changed = None

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False
        if self._prodj:
            try:
                self._prodj.stop()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=3)
        logger.info("Pro DJ Link stopped")

    def _apply_slew(self, current, target):
        step = max(0.1, self._max_change)
        delta = target - current
        if delta > step:
            return current + step
        if delta < -step:
            return current - step
        return target

    def _run(self):
        try:
            from prodj.core.prodj import ProDj
        except ImportError:
            logger.info("python-prodj-link not installed — Pro DJ Link disabled")
            return

        try:
            self._prodj = ProDj()
            self._prodj.set_client_keepalive_callback(self._on_keepalive)
            self._prodj.set_client_change_callback(lambda c: None)
            self._prodj.data.pdb_enabled = True
            self._prodj.vcdj_set_player_number(self._player_number)
            self._prodj.vcdj_enable()
            self._prodj.start()
            logger.info("Active as player #%d", self._player_number)
            logger.info("Waiting for CDJs on the network...")
            while self.running:
                self._poll_players()
                time.sleep(0.05)
        except Exception as e:
            logger.error("Pro DJ Link error: %s", e)

    def _on_keepalive(self, client):
        num = client.player_number
        with self._players_lock:
            is_new = num not in self.players
            self.players[num] = client
        if is_new:
            logger.info("Player %d connected: %s", num, client.model_name)

    def _poll_players(self):
        if not self._prodj:
            return
        with self._players_lock:
            player_numbers = list(self.players.keys())
        if not player_numbers:
            return
        try:
            master = None
            for num in player_numbers:
                status = self._prodj.cl.getStatus(num)
                if status is None:
                    continue
                if getattr(status, "is_master", False):
                    master = (num, status)
                    break
                if getattr(status, "play_state", "") == "playing" and not master:
                    master = (num, status)
            if not master:
                return
            player_num, status = master

            if hasattr(status, "bpm"):
                bpm = status.bpm
                if bpm > 1000:
                    bpm /= 100.0
                if bpm > 0 and abs(bpm - self.current_bpm) > 0.1:
                    self.current_bpm = bpm
                    effective = max(self._bpm_min, min(self._bpm_max, bpm * getattr(status, "pitch", 1.0)))
                    alpha = max(0.01, min(1.0, self._smoothing_alpha))
                    if self._smoothed_bpm is None:
                        self._smoothed_bpm = effective
                    else:
                        smoothed = (1.0 - alpha) * self._smoothed_bpm + alpha * effective
                        self._smoothed_bpm = self._apply_slew(self._smoothed_bpm, smoothed)
                    if self._last_sent_bpm is None or abs(self._smoothed_bpm - self._last_sent_bpm) >= self._send_threshold:
                        if self.on_bpm:
                            self.on_bpm(self._smoothed_bpm)
                        self._last_sent_bpm = self._smoothed_bpm

            if hasattr(status, "beat") and self.on_beat:
                self.on_beat(getattr(status, "beat_count", 0) % 4 + 1)

            self._poll_track_metadata(player_num)
        except Exception as exc:
            logger.debug("Pro DJ Link poll failed: %s", exc)

    def _poll_track_metadata(self, player_num):
        try:
            md = self._prodj.data.get_metadata(player_num)
            if not md:
                return
            title = getattr(md, "title", "") or ""
            artist = getattr(md, "artist", "") or ""
            if title and title != self.current_track.get("title", ""):
                self.current_track = {"title": title, "artist": artist}
                logger.info("Now playing: %s - %s", artist, title)
                if self.on_track_changed:
                    self.on_track_changed(title, artist)
        except Exception as exc:
            logger.debug("Pro DJ Link metadata poll failed: %s", exc)
