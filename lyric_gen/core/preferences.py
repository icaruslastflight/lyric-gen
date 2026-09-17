"""Lyric Gen preferences — JSON config load/save with defaults."""

import json
import os
import logging

logger = logging.getLogger("lyricgen.prefs")

PREFERENCES_FILE = "preferences.json"

DEFAULT_PREFERENCES = {
    "resolume": {
        "ip": "127.0.0.1",
        "osc_port": 7000,
        "listen_ip": "0.0.0.0",
        "listen_port": 7001,
        "bpm_min": 20.0,
        "bpm_max": 500.0,
        "tempo_osc_mode": "normalized",
        "send_bpm": True,
        "lyric_layer": 4,
        "lyric_clip": 4,
        "next_lyric_layer": 3,
        "next_lyric_clip": 4,
        "text_source_type": "blocktextgenerator",
    },
    "audio": {
        "device_index": None,
        "sample_rate": 44100,
        "channels": 1,
    },
    "acrcloud": {
        "host": "",
        "access_key": "",
        "access_secret": "",
        "interval": 15,
        "record_duration": 10,
        "confirm_scans": 2,
    },
    "beat": {
        "sensitivity": 0.5,
        "use_librosa_tempo": False,
    },
    "bpm_stability": {
        "history_size": 24,
        "smoothing_alpha": 0.15,
        "send_threshold": 0.8,
        "min_emit_interval_sec": 0.75,
        "jump_limit_bpm": 50.0,
        "enforce_jump_limit": True,
        "max_change_per_update": 2.0,
        "min_bpm": 60.0,
        "max_bpm": 200.0,
        "harmonic_snap": True,
    },
    "lyrics": {
        "offset_sec": 0.0,
        "ab_alternation": False,
        "fetch_timeout_sec": 10,
        "clear_on_song_change": True,
        "sync_deadband_sec": 0.25,
        "sync_max_step_sec": 0.35,
    },
    "prodj": {
        "enabled": False,
        "player_number": 5,
    },
    "ui": {
        "always_on_top": False,
    },
}


def _merge_defaults(user, defaults):
    """Deep merge user prefs over defaults, keeping all default keys."""
    merged = {}
    for key, default_value in defaults.items():
        user_value = user.get(key) if isinstance(user, dict) else None
        if isinstance(default_value, dict):
            merged[key] = _merge_defaults(
                user_value if isinstance(user_value, dict) else {}, default_value
            )
        else:
            merged[key] = user_value if user_value is not None else default_value
    return merged


def _prefs_path(base_dir=None):
    """Resolve preferences.json path.

    When *base_dir* is given (interconnect / headless mode) the file lives
    alongside the repo root as before.  For the normal GUI install the file
    lives in %APPDATA%\\LyricGen\\ so that writing to it never requires admin
    rights regardless of where the app is installed.
    """
    if base_dir:
        return os.path.join(base_dir, PREFERENCES_FILE)
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(appdata, "LyricGen", PREFERENCES_FILE)


def load_preferences(base_dir=None):
    """Load preferences.json, merging with defaults. Creates file if missing."""
    path = _prefs_path(base_dir)
    path = os.path.normpath(path)
    data = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            logger.info("Loaded preferences from %s", path)
        except Exception as e:
            logger.warning("Failed to read %s: %s — using defaults", path, e)
    merged = _merge_defaults(data, DEFAULT_PREFERENCES)
    if not os.path.exists(path):
        save_preferences(merged, base_dir)
        logger.info("Created default %s", path)
    return merged


def save_preferences(prefs, base_dir=None):
    """Write preferences dict to JSON file."""
    path = os.path.normpath(_prefs_path(base_dir))
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(prefs, f, indent=2)
        logger.info("Saved preferences to %s", path)
    except Exception as e:
        logger.error("Could not save %s: %s", path, e)


def update_preference(prefs, section, key, value, base_dir=None):
    """Update a single preference value and save."""
    if section in prefs and key in prefs[section]:
        prefs[section][key] = value
        save_preferences(prefs, base_dir)
    else:
        logger.warning("Unknown preference: %s.%s", section, key)
