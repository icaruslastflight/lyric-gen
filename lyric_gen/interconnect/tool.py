"""Lyric Gen as an Interconnect ToolBase bridge."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_LYRIC_GEN_ROOT = Path(__file__).resolve().parent.parent.parent


def _ensure_interconnect_on_path() -> Path | None:
    ic = os.environ.get("INTERCONNECT_ROOT", "").strip()
    if not ic:
        return None
    p = Path(ic).expanduser().resolve()
    if p.is_dir():
        root_s = str(p)
        if root_s not in sys.path:
            sys.path.insert(0, root_s)
        return p
    return None


_ensure_interconnect_on_path()
from tool_template import ToolBase  # noqa: E402

from lyric_gen.core.app import LyricGenApp  # noqa: E402
from lyric_gen.core.preferences import load_preferences  # noqa: E402


def _apply_interconnect_runtime_prefs(prefs: dict) -> None:
    """Align Lyric Gen networking with Interconnect profile; avoid clashing with hub on 7001."""
    if not os.environ.get("INTERCONNECT_ROOT"):
        return
    try:
        from config import DEFAULT_RUNTIME_PROFILE, resolve_runtime_profile
    except ImportError as exc:
        logger.warning("[LYRICGEN] Interconnect config not importable: %s", exc)
        return

    profile_name = os.environ.get("INTERCONNECT_PROFILE", DEFAULT_RUNTIME_PROFILE)
    _, pdata = resolve_runtime_profile(profile_name)
    r = pdata.get("resolume", {})
    if r.get("ip"):
        prefs["resolume"]["ip"] = r["ip"]
    if r.get("osc_port") is not None:
        prefs["resolume"]["osc_port"] = int(r["osc_port"])

    # Hub / Resolume bridge usually binds 7020+ or 7001; lyric-gen commands need their own port.
    listen = os.getenv("LYRICGEN_LISTEN_PORT", "").strip()
    prefs["resolume"]["listen_port"] = int(listen) if listen else 7020


class LyricGenTool(ToolBase):
    """Runs LyricGenApp headless; forwards `/resolume-sync/*` to the Interconnect listen port."""

    name = "Lyric Gen"

    def __init__(self):
        super().__init__()
        self._app: LyricGenApp | None = None
        self._hub_client = None

    def _make_hub_forwarder(self, prefs: dict):
        """Send hub-namespace OSC so Resolume bridge + TouchOSC feedback receive lyrics."""
        if not os.environ.get("INTERCONNECT_ROOT"):
            return
        try:
            from config import RESOLUME_IP, RESOLUME_LISTEN_PORT
        except ImportError:
            return

        hub_ip = os.getenv("LYRICGEN_HUB_IP", RESOLUME_IP).strip() or RESOLUME_IP
        hub_port = int(os.getenv("LYRICGEN_HUB_PORT", str(RESOLUME_LISTEN_PORT)))

        try:
            from pythonosc import udp_client

            self._hub_client = udp_client.SimpleUDPClient(hub_ip, hub_port)
            logger.info("[LYRICGEN] Hub forward %s:%s (/resolume-sync/*)", hub_ip, hub_port)
        except Exception as exc:
            logger.warning("[LYRICGEN] Hub forward disabled: %s", exc)
            self._hub_client = None

        osc = self._app.osc
        orig_send_lyric = osc.send_lyric
        orig_send_song = osc.send_song_info
        orig_send_beat = osc.send_beat
        orig_send_bpm = osc.send_bpm
        hub = self._hub_client

        def send_lyric(current_line, next_line="", progress=0.0):
            orig_send_lyric(current_line, next_line, progress)
            if hub:
                hub.send_message("/resolume-sync/lyrics/current", str(current_line))
                hub.send_message("/resolume-sync/lyrics/next", str(next_line))
                hub.send_message("/resolume-sync/lyrics/progress", float(progress))

        def send_song_info(title, artist):
            orig_send_song(title, artist)
            if hub:
                hub.send_message("/resolume-sync/song/title", str(title))
                hub.send_message("/resolume-sync/song/artist", str(artist))

        def send_beat(position):
            orig_send_beat(position)
            if hub:
                hub.send_message("/resolume-sync/beat", float(position))
                if int(position) == 1:
                    hub.send_message("/resolume-sync/downbeat", 1.0)

        def send_bpm(bpm):
            orig_send_bpm(bpm)
            if hub:
                hub.send_message("/resolume-sync/bpm", float(bpm))

        osc.send_lyric = send_lyric
        osc.send_song_info = send_song_info
        osc.send_beat = send_beat
        osc.send_bpm = send_bpm

    def on_start(self):
        base_dir = str(_LYRIC_GEN_ROOT)
        prefs = load_preferences(base_dir)
        _apply_interconnect_runtime_prefs(prefs)

        self._app = LyricGenApp(prefs, base_dir=base_dir)
        self._make_hub_forwarder(prefs)
        self._app.start_all()
        logger.info(
            "[LYRICGEN] OSC commands on %s:%s → Resolume %s:%s",
            prefs["resolume"]["listen_ip"],
            prefs["resolume"]["listen_port"],
            prefs["resolume"]["ip"],
            prefs["resolume"]["osc_port"],
        )

    def on_stop(self):
        if self._app:
            self._app.stop_all()
            self._app = None
        self._hub_client = None
