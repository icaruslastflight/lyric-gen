"""Lyrics engine — LRCLIB synced lyric fetch and timed delivery."""

import os
import re
import time
import threading
import json
import difflib
import logging
import urllib.request
import urllib.parse

logger = logging.getLogger("lyricgen.lyrics")

LRCLIB_BASE = "https://lrclib.net/api"
USER_AGENT = "LyricGen/1.0"


def _timing_memory_path():
    """Path to per-song timing offset memory file."""
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(appdata, "LyricGen", "timing_memory.json")


def _load_timing_memory():
    path = _timing_memory_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_timing_memory(memory):
    path = _timing_memory_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(memory, f, indent=2)
    except Exception as e:
        logger.debug("Could not save timing memory: %s", e)


def _song_key(title, artist):
    """Normalize title+artist into a stable lookup key."""
    return f"{(title or '').strip().lower()}|{(artist or '').strip().lower()}"


class LyricsEngine:
    """Fetches time-synced lyrics from LRCLIB and delivers them in real-time.

    Callbacks:
        on_lyric(current: str, next_line: str, progress: float) — each line change.
        on_lyrics_cleared() — when lyrics are cleared (song change, gap, etc.).
        on_timing_changed(offset: float) — when timing offset changes (for GUI sync).
    """

    def __init__(self, prefs):
        self.prefs = prefs
        lyric_prefs = prefs["lyrics"]

        self.enabled = True
        self.running = False
        self._thread = None
        self.lyrics = []
        self.current_index = -1
        self.song_start_time = 0
        self.timing_offset = float(lyric_prefs["offset_sec"])
        self._default_offset = float(lyric_prefs["offset_sec"])
        self.alternate_clips = bool(lyric_prefs["ab_alternation"])
        self._fetch_timeout = float(lyric_prefs["fetch_timeout_sec"])
        self._clear_on_change = bool(lyric_prefs["clear_on_song_change"])
        self._sync_deadband_sec = float(lyric_prefs.get("sync_deadband_sec", 0.25))
        self._sync_max_step_sec = float(lyric_prefs.get("sync_max_step_sec", 0.35))
        self._song_title = None
        self._song_artist = None
        self._song_version = 0
        self._new_song_event = threading.Event()
        self._timing_memory = _load_timing_memory()

        # Callbacks
        self.on_lyric = None  # (current, next_line, progress) -> None
        self.on_lyrics_cleared = None  # () -> None
        self.on_timing_changed = None  # (offset: float) -> None

    def clear(self):
        """Reset lyrics state so the next song change triggers a fresh fetch."""
        self._song_title = None
        self._song_artist = None
        self._song_version += 1
        self.lyrics = []
        self.current_index = -1
        self.song_start_time = 0
        self._new_song_event.set()

    def toggle(self):
        self.enabled = not self.enabled
        logger.info("Lyrics %s", "enabled" if self.enabled else "disabled")
        if not self.enabled and self.on_lyrics_cleared:
            self.on_lyrics_cleared()

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("Lyrics engine started")

    def stop(self):
        self._save_song_timing()
        self.running = False
        self._new_song_event.set()
        if self._thread:
            self._thread.join(timeout=3)
        logger.info("Lyrics engine stopped")

    def on_song_changed(self, title, artist, play_offset_ms=0, keep_timing=False):
        """Called when a new song is identified. Triggers lyric fetch.

        Args:
            keep_timing: If True, preserve the current timing offset instead of
                loading from memory. Used when the user picks an alt match for
                the same underlying audio.
        """
        is_new = (title != self._song_title) or (artist != self._song_artist)
        if not is_new:
            return

        # Save timing for the outgoing song before switching
        if self._song_title and self._song_artist:
            self._save_song_timing()

        self._song_title = title
        self._song_artist = artist
        self._song_version += 1
        # Calculate when the song started based on ACRCloud's play_offset_ms.
        self.song_start_time = time.time() - (play_offset_ms / 1000.0)

        if keep_timing:
            logger.info("Keeping timing %+.1fs for alt match: %s - %s", self.timing_offset, artist, title)
        else:
            # Load learned timing for this track (or reset to default)
            key = _song_key(title, artist)
            learned = self._timing_memory.get(key)
            if learned is not None:
                self.timing_offset = float(learned)
                logger.info("Loaded timing %+.1fs for: %s - %s", self.timing_offset, artist, title)
            else:
                self.timing_offset = self._default_offset
        self.current_index = -1
        if self.on_timing_changed:
            self.on_timing_changed(self.timing_offset)

        if self._clear_on_change and self.on_lyrics_cleared:
            self.on_lyrics_cleared()
        self._new_song_event.set()

    def on_song_progress(self, title, artist, play_offset_ms=0):
        """
        Called on every successful Song ID scan for the current track.
        Uses play_offset_ms to keep lyric timeline anchored to the real position,
        handling DJ pitch changes, loops, and natural timing drift.
        """
        if title != self._song_title or artist != self._song_artist:
            return

        now = time.time()
        proposed_start = now - max(0.0, float(play_offset_ms) / 1000.0)
        current_start = self.song_start_time

        if not current_start:
            self.song_start_time = proposed_start
            return

        drift = proposed_start - current_start

        if abs(drift) <= self._sync_deadband_sec:
            return

        if abs(drift) > 5.0:
            logger.info("Lyric sync: large drift %.1fs — snapping to ACRCloud offset", drift)
            self.song_start_time = proposed_start
            self.current_index = -1
        else:
            step = max(-self._sync_max_step_sec, min(self._sync_max_step_sec, drift))
            self.song_start_time += step

    def set_sync_tuning(self, deadband_sec, max_step_sec):
        self._sync_deadband_sec = max(0.05, min(1.5, float(deadband_sec)))
        self._sync_max_step_sec = max(0.05, min(2.0, float(max_step_sec)))
        logger.info(
            "Lyric sync tuning updated: deadband=%.2fs, max_step=%.2fs",
            self._sync_deadband_sec,
            self._sync_max_step_sec,
        )

    def adjust_timing(self, seconds):
        self.timing_offset += seconds
        self.current_index = -1
        self._save_song_timing()
        logger.info("Timing offset: %+.1fs", self.timing_offset)

    def set_timing_offset(self, seconds):
        self.timing_offset = seconds
        self.current_index = -1
        self._save_song_timing()
        logger.info("Timing offset: %+.1fs", self.timing_offset)

    def _save_song_timing(self):
        """Persist current timing offset for the active track."""
        if not self._song_title or not self._song_artist:
            return
        key = _song_key(self._song_title, self._song_artist)
        # Only save if user changed it from default
        if abs(self.timing_offset - self._default_offset) > 0.01:
            self._timing_memory[key] = round(self.timing_offset, 2)
        elif key in self._timing_memory:
            del self._timing_memory[key]
        _save_timing_memory(self._timing_memory)

    # ----- Internal -----

    def _run(self):
        while self.running:
            self._new_song_event.wait()
            self._new_song_event.clear()
            if not self.running:
                break

            title, artist = self._song_title, self._song_artist
            version = self._song_version
            if not title:
                continue

            lyrics = self._fetch_synced_lyrics(title, artist)

            if version != self._song_version:
                continue  # Newer song arrived during fetch

            if lyrics:
                self.lyrics = lyrics
                self.current_index = -1
                elapsed = max(0.0, time.time() - self.song_start_time)
                logger.info(
                    "Loaded %d lines for: %s - %s (elapsed: %.1fs)",
                    len(lyrics), artist, title, elapsed,
                )
                self._deliver_lyrics()
            else:
                self.lyrics = []
                logger.info("No synced lyrics for: %s - %s", artist, title)

    def _deliver_lyrics(self):
        cleared = False
        while self.running and self.enabled and not self._new_song_event.is_set():
            elapsed = max(0.0, time.time() - self.song_start_time) + self.timing_offset
            new_index = self.current_index
            for i, (ts, _text) in enumerate(self.lyrics):
                if ts <= elapsed:
                    new_index = i
                else:
                    break

            if new_index != self.current_index and new_index >= 0:
                self.current_index = new_index
                cleared = False
                current_text = self.lyrics[new_index][1]
                next_text = (
                    self.lyrics[new_index + 1][1]
                    if new_index + 1 < len(self.lyrics)
                    else ""
                )
                progress = new_index / max(len(self.lyrics) - 1, 1)
                if self.on_lyric:
                    self.on_lyric(current_text, next_text, progress)

            # Clear after last line (10s grace) or during long gaps (>8s)
            if not cleared and self.current_index >= 0:
                last_ts = self.lyrics[self.current_index][0]
                if self.current_index == len(self.lyrics) - 1:
                    if elapsed > last_ts + 10.0:
                        if self.on_lyrics_cleared:
                            self.on_lyrics_cleared()
                        cleared = True
                else:
                    next_ts = self.lyrics[self.current_index + 1][0]
                    gap = next_ts - last_ts
                    if gap > 8.0 and elapsed > last_ts + gap * 0.8:
                        if self.on_lyrics_cleared:
                            self.on_lyrics_cleared()
                        cleared = True

            time.sleep(0.05)

    def _fetch_synced_lyrics(self, title, artist):
        """Search LRCLIB for synced lyrics with fuzzy matching."""
        title_clean = _normalize(title)
        artist_clean = _normalize(artist)

        query_pairs = [
            (title, artist),
            (title_clean, artist),
            (title_clean, artist_clean),
            (title, ""),
            (title_clean, ""),
        ]

        seen = set()
        candidates = []
        for q_title, q_artist in query_pairs:
            if not q_title:
                continue
            key = (q_title.lower().strip(), q_artist.lower().strip())
            if key in seen:
                continue
            seen.add(key)
            try:
                for entry in _lrclib_search(q_title, q_artist, self._fetch_timeout):
                    if entry.get("syncedLyrics"):
                        candidates.append(entry)
            except Exception as e:
                logger.warning("LRCLIB search error: %s", e)

        if not candidates:
            return None

        best = max(candidates, key=lambda e: _score_entry(e, title, artist))
        if best.get("instrumental"):
            logger.info("Track is instrumental — skipping lyrics")
            return None
        return _parse_lrc(best.get("syncedLyrics", ""))


# ---- Module-level helpers ----

def _normalize(value):
    """Strip remix/feat/edit metadata from a track name for better matching."""
    s = (value or "").strip().lower()
    s = re.sub(r"\([^)]*(remix|mix|edit|version|live|radio|extended)[^)]*\)", "", s, flags=re.I)
    s = re.sub(r"\[[^\]]*(remix|mix|edit|version|live|radio|extended)[^\]]*\]", "", s, flags=re.I)
    s = re.sub(r"\b(feat\.?|ft\.?|featuring)\b.*$", "", s, flags=re.I)
    s = re.sub(r"\s*-\s*(remix|mix|edit|version|live|radio|extended).*$", "", s, flags=re.I)
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _lrclib_search(track_name, artist_name, timeout=10):
    """Query LRCLIB search API."""
    params = {"track_name": track_name}
    if artist_name:
        params["artist_name"] = artist_name
    url = f"{LRCLIB_BASE}/search?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8")) or []


def _score_entry(entry, target_title, target_artist):
    """Score a LRCLIB entry against the target using fuzzy string matching."""
    t_target = _normalize(target_title)
    a_target = _normalize(target_artist)
    t_entry = _normalize(entry.get("trackName", ""))
    a_entry = _normalize(entry.get("artistName", ""))
    t_ratio = difflib.SequenceMatcher(None, t_target, t_entry).ratio() if t_target and t_entry else 0.0
    a_ratio = difflib.SequenceMatcher(None, a_target, a_entry).ratio() if a_target and a_entry else 0.0
    score = (0.75 * t_ratio) + (0.25 * a_ratio)

    # Language/script bias:
    # If target metadata is mostly Latin-script, prefer Latin-script lyric text.
    # This avoids selecting non-English translations for English tracks.
    target_latin = _mostly_latin_text((target_title or "") + " " + (target_artist or ""))
    lyric_text = (entry.get("syncedLyrics") or "")[:800]
    if target_latin and lyric_text:
        if _mostly_latin_text(lyric_text):
            score += 0.06
        else:
            score -= 0.20

    return score


def _mostly_latin_text(text):
    """Heuristic: True if text is primarily ASCII/Latin-like letters."""
    letters = 0
    latin = 0
    for ch in text:
        if ch.isalpha():
            letters += 1
            code = ord(ch)
            if (
                0x0041 <= code <= 0x024F  # Basic Latin + Latin Extended
                or 0x1E00 <= code <= 0x1EFF  # Latin Extended Additional
            ):
                latin += 1
    if letters == 0:
        return True
    return (latin / letters) >= 0.70


def _parse_lrc(lrc_text):
    """Parse LRC format into list of (timestamp_seconds, text) tuples."""
    lines = []
    pattern = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\](.*)")
    for line in lrc_text.split("\n"):
        m = pattern.match(line.strip())
        if m:
            text = m.group(3).strip()
            if text:
                lines.append((int(m.group(1)) * 60 + float(m.group(2)), text))
    lines.sort(key=lambda x: x[0])
    return lines
