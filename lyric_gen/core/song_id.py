"""Song identification via ACRCloud (BYOK — user provides own API credentials)."""

import hashlib
import hmac
import base64
import time
import threading
import io
import wave
import logging

import numpy as np

logger = logging.getLogger("lyricgen.songid")


class SongIdentifier:
    """Identifies currently playing songs using ACRCloud audio fingerprinting.

    Users must create a free ACRCloud account and provide their own
    host, access_key, and access_secret in preferences.

    Callbacks:
        on_song_changed(title, artist, play_offset_ms) — called when a new song is detected.
    """

    def __init__(self, prefs):
        self.prefs = prefs
        acr = prefs["acrcloud"]
        self._host = acr["host"]
        self._access_key = acr["access_key"]
        self._access_secret = acr["access_secret"]
        self._interval = acr["interval"]
        self._record_duration = acr["record_duration"]

        self.running = False
        self._thread = None
        self.current_song = None
        self.current_artist = None
        self._force_scan = False
        self._manual_override = False  # True when user picked an alt match
        self._pending_song = None      # (title, artist) awaiting confirmation
        self._pending_count = 0        # consecutive scans matching pending song
        self._confirm_scans = int(acr.get("confirm_scans", 2))  # scans needed to confirm change

        # Callbacks
        self.on_song_changed = None  # (title, artist, play_offset_ms) -> None
        self.on_song_info = None  # (title, artist) -> None  (every scan, even re-sync)
        self.on_song_progress = None  # (title, artist, play_offset_ms) -> None (every successful scan)
        self.on_candidates = None  # (candidates: list[dict]) -> None  (all ACRCloud matches)

    @property
    def configured(self):
        return bool(self._host and self._access_key and self._access_secret)

    def start(self):
        if self.running:
            return
        if not self.configured:
            logger.warning("ACRCloud credentials not configured — song ID disabled")
            return
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("Song identifier started (ACRCloud: %s)", self._host)

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Song identifier stopped")

    def force_scan(self):
        self.current_song = None
        self.current_artist = None
        self._manual_override = False
        self._pending_song = None
        self._pending_count = 0
        self._force_scan = True

    def _run(self):
        import requests

        while self.running:
            try:
                audio_data, record_start = self._record_audio()
                if audio_data is None:
                    self._wait(self._interval)
                    continue

                wav_bytes = self._to_wav(audio_data)
                result = self._identify(wav_bytes, requests)
                # Compensate from when audio actually started flowing,
                # not from before PyAudio init/stream setup.
                time_since_record_start = time.time() - record_start

                if result and result.get("status", {}).get("code") == 0:
                    music = result.get("metadata", {}).get("music", [])
                    if music:
                        match = music[0]
                        title = match.get("title", "Unknown")
                        artists = match.get("artists", [{}])
                        artist = artists[0].get("name", "Unknown") if artists else "Unknown"
                        raw_offset_ms = match.get("play_offset_ms", 0)
                        offset_ms = raw_offset_ms + int(time_since_record_start * 1000)

                        # Build candidates list from all ACRCloud matches
                        if self.on_candidates:
                            candidates = []
                            for m in music:
                                m_title = m.get("title", "Unknown")
                                m_artists = m.get("artists", [{}])
                                m_artist = m_artists[0].get("name", "Unknown") if m_artists else "Unknown"
                                m_raw = m.get("play_offset_ms", 0)
                                candidates.append({
                                    "title": m_title,
                                    "artist": m_artist,
                                    "offset_ms": m_raw + int(time_since_record_start * 1000),
                                    "score": m.get("score", 0),
                                })
                            self.on_candidates(candidates)

                        # If user manually picked an alt match, only clear override
                        # when a confirmed new song arrives.
                        acr_top_changed = (title != self.current_song or artist != self.current_artist)

                        if self._manual_override and not acr_top_changed:
                            # Same underlying track — user's alt pick stays active
                            continue

                        if acr_top_changed:
                            # Require consecutive scans to confirm a real song change
                            # (filters out one-off sample/interpolation detections)
                            pending = (title, artist)
                            if pending == self._pending_song:
                                self._pending_count += 1
                            else:
                                self._pending_song = pending
                                self._pending_count = 1
                                logger.debug(
                                    "Pending song change: %s - %s (need %d scans)",
                                    artist, title, self._confirm_scans,
                                )

                            if self._pending_count < self._confirm_scans:
                                # Not confirmed yet — keep current song active
                                if self.on_song_info:
                                    self.on_song_info(self.current_song, self.current_artist)
                                if self.on_song_progress and self.current_song:
                                    self.on_song_progress(
                                        self.current_song, self.current_artist, offset_ms,
                                    )
                                continue

                            # Confirmed — accept the change
                            self._pending_song = None
                            self._pending_count = 0
                            self._manual_override = False
                            self.current_song = title
                            self.current_artist = artist
                            logger.info(
                                "Now playing: %s - %s (offset: %dms, raw: %dms, scan: %.1fs)",
                                artist, title, offset_ms, raw_offset_ms, time_since_record_start,
                            )
                            if self.on_song_changed:
                                self.on_song_changed(title, artist, offset_ms)
                        else:
                            # Same song — reset any pending change
                            self._pending_song = None
                            self._pending_count = 0

                        if self.on_song_info:
                            self.on_song_info(title, artist)
                        if self.on_song_progress:
                            self.on_song_progress(title, artist, offset_ms)
                else:
                    code = result.get("status", {}).get("code", -1) if result else -1
                    if code == 1001:
                        logger.debug("No match found")
                    elif code == 3003:
                        logger.warning("ACRCloud daily limit reached")
                    else:
                        msg = result.get("status", {}).get("msg", "Unknown") if result else "No response"
                        logger.warning("ACRCloud error %d: %s", code, msg)

            except Exception as e:
                logger.error("Song ID error: %s", e)

            self._wait(self._interval)

    def _wait(self, seconds):
        waited = 0
        while waited < seconds and self.running:
            if self._force_scan:
                self._force_scan = False
                return
            time.sleep(1)
            waited += 1

    def _record_audio(self):
        """Record audio and return (audio_bytes, record_start_time).

        record_start_time is the wall-clock time when the first audio
        sample was captured — used for precise offset compensation.
        """
        try:
            import pyaudio
        except ImportError:
            logger.error("PyAudio not installed")
            return None, 0.0

        device_index = self.prefs["audio"]["device_index"]
        sample_rate = self.prefs["audio"]["sample_rate"]
        channels = self.prefs["audio"]["channels"]

        pa = pyaudio.PyAudio()
        stream = None
        try:
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=sample_rate,
                input=True,
                input_device_index=device_index,
                frames_per_buffer=1024,
            )
            frames = []
            num_frames = int(sample_rate / 1024 * self._record_duration)
            record_start = time.time()  # timestamp after stream open, before first read
            for _ in range(num_frames):
                if not self.running:
                    break
                frames.append(stream.read(1024, exception_on_overflow=False))
            return b"".join(frames), record_start
        except Exception as e:
            logger.error("Recording error: %s", e)
            return None, 0.0
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
            pa.terminate()

    def _to_wav(self, raw_audio):
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(self.prefs["audio"]["channels"])
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(self.prefs["audio"]["sample_rate"])
            wf.writeframes(raw_audio)
        return buf.getvalue()

    def _identify(self, audio_bytes, requests):
        """Send audio to ACRCloud and return the JSON response."""
        timestamp = str(int(time.time()))
        string_to_sign = (
            "POST\n/v1/identify\n"
            + self._access_key + "\n"
            + "audio\n"
            + "1\n"
            + timestamp
        )
        sign = base64.b64encode(
            hmac.new(
                self._access_secret.encode("utf-8"),
                string_to_sign.encode("utf-8"),
                hashlib.sha1,
            ).digest()
        ).decode("utf-8")

        url = f"https://{self._host}/v1/identify"
        data = {
            "access_key": self._access_key,
            "sample_bytes": str(len(audio_bytes)),
            "timestamp": timestamp,
            "signature": sign,
            "data_type": "audio",
            "signature_version": "1",
        }
        files = {"sample": ("audio.wav", audio_bytes, "audio/wav")}

        resp = requests.post(url, data=data, files=files, timeout=15)
        resp.raise_for_status()
        return resp.json()
