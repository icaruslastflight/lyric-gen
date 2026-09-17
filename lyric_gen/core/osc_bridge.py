"""OSC bridge — bidirectional Resolume Arena communication."""

import socket
import threading
import logging

from pythonosc import udp_client, dispatcher, osc_server

logger = logging.getLogger("lyricgen.osc")


class OSCBridge:
    """Sends BPM, beats, lyrics, and song info to Resolume via OSC.
    Receives control commands from Resolume or external OSC sources.

    Resolume text path format (confirmed via research):
        /composition/layers/{L}/clips/{C}/video/source/{type}/text/params/lines
    where {type} is 'blocktextgenerator' or 'textgenerator'.
    The target clip MUST have a Text Block or Text Animator source already in the composition.
    """

    def __init__(self, prefs):
        self.prefs = prefs
        res = prefs["resolume"]

        self._resolume_ip = res["ip"]
        self._resolume_port = res["osc_port"]
        self._listen_ip = res["listen_ip"]
        self._listen_port = res["listen_port"]
        self._bpm_min = float(res["bpm_min"])
        self._bpm_max = float(res["bpm_max"])
        self._send_bpm = bool(res["send_bpm"])
        self._tempo_osc_mode = str(res.get("tempo_osc_mode", "normalized")).strip().lower()
        self._lyric_layer = int(res["lyric_layer"])
        self._lyric_clip = int(res["lyric_clip"])
        self._next_lyric_layer = int(res["next_lyric_layer"])
        self._next_lyric_clip = int(res["next_lyric_clip"])
        self._text_source_type = res.get("text_source_type", "blocktextgenerator")

        self._client = None
        self._server = None
        self._server_thread = None

        # Command callbacks — set by app orchestrator
        self.on_cmd_start = None
        self.on_cmd_stop = None
        self.on_cmd_bpm_lock = None
        self.on_cmd_bpm_unlock = None
        self.on_cmd_bpm_half = None
        self.on_cmd_bpm_double = None
        self.on_cmd_bpm_resync = None
        self.on_cmd_lyrics_toggle = None
        self.on_cmd_rescan = None
        self.on_cmd_sensitivity = None

    def start(self):
        self._client = udp_client.SimpleUDPClient(self._resolume_ip, self._resolume_port)

        disp = dispatcher.Dispatcher()
        disp.map("/lyricgen/cmd/start", self._handle_cmd("start", "on_cmd_start"))
        disp.map("/lyricgen/cmd/stop", self._handle_cmd("stop", "on_cmd_stop"))
        disp.map("/lyricgen/cmd/bpm_lock", self._handle_cmd("bpm_lock", "on_cmd_bpm_lock"))
        disp.map("/lyricgen/cmd/bpm_unlock", self._handle_cmd("bpm_unlock", "on_cmd_bpm_unlock"))
        disp.map("/lyricgen/cmd/bpm_half", self._handle_cmd("bpm_half", "on_cmd_bpm_half"))
        disp.map("/lyricgen/cmd/bpm_double", self._handle_cmd("bpm_double", "on_cmd_bpm_double"))
        disp.map("/lyricgen/cmd/bpm_resync", self._handle_cmd("bpm_resync", "on_cmd_bpm_resync"))
        disp.map("/lyricgen/cmd/lyrics_toggle", self._handle_cmd("lyrics_toggle", "on_cmd_lyrics_toggle"))
        disp.map("/lyricgen/cmd/rescan", self._handle_cmd("rescan", "on_cmd_rescan"))
        disp.map("/lyricgen/cmd/sensitivity", self._on_sensitivity)
        # Native Resolume tempo command paths (2-way comms)
        disp.map("/composition/tempocontroller/tempo/divide", self._handle_cmd("bpm_half", "on_cmd_bpm_half"))
        disp.map("/composition/tempocontroller/tempo/multiply", self._handle_cmd("bpm_double", "on_cmd_bpm_double"))
        disp.map("/composition/tempocontroller/resync", self._handle_cmd("bpm_resync", "on_cmd_bpm_resync"))
        disp.map("/composition/tempocontroller/tempotap", self._handle_cmd("bpm_resync", "on_cmd_bpm_resync"))
        disp.map("/composition/tempocontroller/tapbpm", self._handle_cmd("bpm_resync", "on_cmd_bpm_resync"))

        def _bind_server(port: int):
            srv = osc_server.ThreadingOSCUDPServer(
                (self._listen_ip, port), disp,
                bind_and_activate=False,
            )
            srv.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.server_bind()
            srv.server_activate()
            return srv

        try:
            self._server = _bind_server(self._listen_port)
        except OSError as exc:
            # Common in mixed setups: 7001 already in use by another OSC listener.
            if self._listen_port == 7001:
                fallback = 7020
                logger.warning(
                    "OSC listen port %d busy (%s). Falling back to %d for /lyricgen/cmd/*.",
                    self._listen_port,
                    exc,
                    fallback,
                )
                self._server = _bind_server(fallback)
                self._listen_port = fallback
            else:
                raise

        self._server_thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._server_thread.start()

        logger.info("OSC listening on port %d", self._listen_port)
        logger.info("OSC sending to Resolume at %s:%d", self._resolume_ip, self._resolume_port)

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        logger.info("OSC bridge stopped")

    # ---- Send methods ----

    def send_bpm(self, bpm):
        """Send BPM to Resolume tempo controller.

        Default mode is normalized 0-1 (`tempo_osc_mode=normalized`).
        Optional mode (`tempo_osc_mode=raw_bpm`) sends raw BPM values.
        """
        if not self._send_bpm or not self._client:
            return
        if self._tempo_osc_mode == "normalized":
            span = self._bpm_max - self._bpm_min
            if span <= 0:
                value = max(0.0, min(1.0, bpm / max(self._bpm_max, 1.0)))
            else:
                value = (bpm - self._bpm_min) / span
                value = max(0.0, min(1.0, value))
        else:
            value = float(bpm)
        self._client.send_message("/composition/tempocontroller/tempo", float(value))

    def send_beat(self, position):
        """Send beat position (1-4) and downbeat pulse."""
        if not self._client:
            return
        self._client.send_message("/lyricgen/beat", float(position))
        if position == 1:
            self._client.send_message("/lyricgen/downbeat", 1.0)

    def send_song_info(self, title, artist):
        if not self._client:
            return
        self._client.send_message("/lyricgen/song/title", str(title))
        self._client.send_message("/lyricgen/song/artist", str(artist))

    def send_tempo_divide(self):
        if self._client:
            self._client.send_message("/composition/tempocontroller/tempo/divide", 1.0)

    def send_tempo_multiply(self):
        if self._client:
            self._client.send_message("/composition/tempocontroller/tempo/multiply", 1.0)

    def send_tempo_resync(self):
        if self._client:
            self._client.send_message("/composition/tempocontroller/resync", 1.0)

    def send_tempo_tap(self):
        if self._client:
            self._client.send_message("/composition/tempocontroller/tempotap", 1.0)
            self._client.send_message("/composition/tempocontroller/tapbpm", 1.0)

    def send_lyric(self, current_line, next_line="", progress=0.0):
        """Send lyric text to Resolume Text Block sources via OSC."""
        if not self._client:
            return
        # Send to user-configured layer/clip text block
        self._send_text_to_clip(self._lyric_layer, self._lyric_clip, current_line)
        self._send_text_to_clip(self._next_lyric_layer, self._next_lyric_clip, next_line)
        # Also send on lyricgen namespace for Interconnect/external consumers
        self._client.send_message("/lyricgen/lyrics/current", str(current_line))
        self._client.send_message("/lyricgen/lyrics/next", str(next_line))
        self._client.send_message("/lyricgen/lyrics/progress", float(progress))

    def clear_lyrics(self):
        self.send_lyric("", "", 0.0)

    def _send_text_to_clip(self, layer, clip, text):
        """Send string to Resolume text targets for a specific layer/clip.

        Resolume projects vary: some use text as a source, others as a video effect.
        We emit to both common address families so either setup works.
        """
        base = f"/composition/layers/{layer}/clips/{clip}"
        targets = [
            # Text source paths (project-configurable type)
            f"{base}/video/source/{self._text_source_type}/text/params/lines",
            # Common effect paths
            f"{base}/video/effects/textblock/params/lines",
            f"{base}/video/effects/textblock/effect/text/params/lines",
            f"{base}/video/effects/textgenerator/params/lines",
        ]
        payload = str(text)
        for path in targets:
            self._client.send_message(path, payload)

    # ---- Receive handlers ----

    def _handle_cmd(self, name, callback_attr):
        def handler(addr, *args):
            logger.info("OSC received: %s", name.upper())
            cb = getattr(self, callback_attr, None)
            if cb:
                cb()
        return handler

    def _on_sensitivity(self, addr, *args):
        if args:
            try:
                v = float(args[0])
            except (TypeError, ValueError):
                logger.warning("OSC sensitivity ignored: invalid value %r", args[0])
                return
            logger.info("OSC received: SENSITIVITY %.2f", v)
            if self.on_cmd_sensitivity:
                self.on_cmd_sensitivity(v)
