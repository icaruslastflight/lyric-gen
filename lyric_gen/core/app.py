"""Lyric Gen application orchestrator — wires all core modules together."""

import logging

from lyric_gen import __version__
from lyric_gen.core.preferences import load_preferences
from lyric_gen.core.osc_bridge import OSCBridge
from lyric_gen.core.beat import BeatTracker
from lyric_gen.core.song_id import SongIdentifier
from lyric_gen.core.lyrics import LyricsEngine
from lyric_gen.core.prodj import ProDJLinkBridge

logger = logging.getLogger("lyricgen.app")


class LyricGenApp:
    """Main application — initializes and wires all modules via callbacks.

    Designed to be driven by either the standalone GUI or the Interconnect tool.
    Does not own the main loop — the caller (GUI or ToolBase) does.
    """

    def __init__(self, prefs=None, base_dir=None):
        self.prefs = prefs or load_preferences(base_dir)
        self.base_dir = base_dir

        # Initialize modules
        self.osc = OSCBridge(self.prefs)
        self.beat_tracker = BeatTracker(self.prefs)
        self.song_id = SongIdentifier(self.prefs)
        self.lyrics_engine = LyricsEngine(self.prefs)
        self.prodj = ProDJLinkBridge(self.prefs)

        # Wire callbacks
        self._wire_callbacks()

    def _wire_callbacks(self):
        """Connect module callbacks to create the data flow."""
        # Beat tracker -> OSC
        self.beat_tracker.on_beat = self._on_beat
        self.beat_tracker.on_bpm_update = self.osc.send_bpm

        # Song ID -> lyrics engine + OSC
        self.song_id.on_song_changed = self._on_song_changed
        self.song_id.on_song_info = self.osc.send_song_info
        self.song_id.on_song_progress = self._on_song_progress

        # Lyrics engine -> OSC
        self.lyrics_engine.on_lyric = self.osc.send_lyric
        self.lyrics_engine.on_lyrics_cleared = self.osc.clear_lyrics

        # Pro DJ Link -> OSC + lyrics
        self.prodj.on_bpm = self.osc.send_bpm
        self.prodj.on_beat = lambda pos: self.osc.send_beat(pos)
        self.prodj.on_track_changed = lambda t, a: self.lyrics_engine.on_song_changed(t, a, 0)

        # OSC commands -> modules
        self.osc.on_cmd_start = self.start_all
        self.osc.on_cmd_stop = self.stop_all
        self.osc.on_cmd_bpm_lock = self.beat_tracker.lock_bpm
        self.osc.on_cmd_bpm_unlock = self.beat_tracker.unlock_bpm
        self.osc.on_cmd_bpm_half = self.beat_tracker.halve_bpm
        self.osc.on_cmd_bpm_double = self.beat_tracker.double_bpm
        self.osc.on_cmd_bpm_resync = self.beat_tracker.resync_beat
        self.osc.on_cmd_lyrics_toggle = self.lyrics_engine.toggle
        self.osc.on_cmd_rescan = self.song_id.force_scan
        self.osc.on_cmd_sensitivity = self.beat_tracker.set_sensitivity

    def _on_beat(self, position, bpm):
        self.osc.send_beat(position)

    def _on_song_changed(self, title, artist, play_offset_ms):
        self.lyrics_engine.on_song_changed(title, artist, play_offset_ms)

    def _on_song_progress(self, title, artist, play_offset_ms):
        self.lyrics_engine.on_song_progress(title, artist, play_offset_ms)

    def start_all(self, beats=True, song_id=True, lyrics=True, prodj=None):
        """Start all enabled services."""
        logger.info("=" * 50)
        logger.info("  LYRIC GEN v%s", __version__)
        logger.info("=" * 50)

        self.osc.start()

        if prodj is None:
            prodj = self.prefs["prodj"]["enabled"]

        if prodj:
            self.prodj.start()

        if beats:
            self.beat_tracker.start()

        if song_id and self.song_id.configured:
            self.song_id.start()
        elif song_id:
            logger.warning("ACRCloud not configured — song ID disabled")

        if lyrics:
            self.lyrics_engine.start()

        logger.info("=" * 50)
        logger.info("  Ready! Listening to audio...")
        logger.info("  OSC commands on port %d", self.prefs["resolume"]["listen_port"])
        logger.info("=" * 50)

    def stop_all(self):
        """Stop all services cleanly."""
        logger.info("Shutting down...")
        # Ensure Resolume text targets are cleared when stopping/exiting.
        try:
            self.osc.clear_lyrics()
        except Exception as exc:
            logger.debug("Could not clear lyrics on shutdown: %s", exc)
        self.prodj.stop()
        self.beat_tracker.stop()
        self.song_id.stop()
        self.lyrics_engine.stop()
        self.osc.stop()
        logger.info("Goodbye!")

