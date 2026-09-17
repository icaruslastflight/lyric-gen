# Lyric Gen Beta (1.0.0-beta.1)

Lyric Gen is a Windows sidecar for Resolume Arena that sends beat/tempo and synced lyric text over OSC for live VJ sets.

## What this beta includes

- Real-time BPM and beat pulse output over OSC
- Song ID using ACRCloud (bring your own keys)
- Synced lyric fetch from LRCLIB
- Current and next lyric text output to Resolume text sources/effects
- GUI preferences for Resolume routing, song ID credentials, and audio input
- Manual lyric timing controls and rescan workflow

## System requirements

- Windows 10 or Windows 11
- Python 3.10+ (`3.11` recommended)
- Resolume Arena on the same machine or reachable over network
- Internet access for ACRCloud and LRCLIB APIs
- Optional: ACRCloud project credentials for automatic song ID

## What the installer installs

The installer places Lyric Gen into `Program Files\\Lyric Gen` with:

- The app source/runtime files
- `Lyric Gen.bat` launcher
- Default preferences template (`preferences_default.json`)
- Documentation (`README.md`, `docs/SETUP_RESOLUME.md`, `CREDITS.txt`)
- App and installer icons

The installer does **not** bundle your ACRCloud credentials, your local `preferences.json`, or interconnect-only bridge tooling.

## First run

1. Launch `Lyric Gen` from Start Menu or desktop shortcut.
2. If dependencies are missing, open Command Prompt in the install folder and run:

```bash
py -3 -m pip install -r requirements-min.txt
```

Optional (experimental librosa tempo path):

```bash
py -3 -m pip install -r requirements-full.txt
```

## First-time Resolume setup

Detailed guide: `docs/SETUP_RESOLUME.md`

Quick version:

1. Create two text clips in Resolume (current line and next line).
2. In Lyric Gen > Preferences > Resolume, set target layer/clip pairs.
3. Confirm Resolume OSC target (`ip`, `osc_port`) and command listen port (`7020` default).
4. Start Lyric Gen and confirm text appears in your target clips.

## ACRCloud setup (for song ID)

1. Create an ACRCloud recognition project in the console.
2. Copy **Host**, **Access Key**, and **Access Secret** from the project.
3. Paste them into Lyric Gen > Preferences > Song ID.
4. Save preferences and restart Lyric Gen services (STOP -> START).

## Beta test checklist

Please test and report results for:

- START/STOP cycles (no crashes, clean shutdown)
- BPM/beat behavior with your audio setup
- Song identification accuracy
- Lyric timing alignment (ahead/behind/on-time)
- Lyrics appearing in the configured Resolume clips
- Timing +/- controls and reset behavior
- Rescan behavior (song/lyrics clear then repopulate)
- Preferences save/load round-trip
- Window close stops all background activity
- Always-on-top toggle behavior

## Known limitations

- LRCLIB does not have synced lyrics for every track.
- ACRCloud free tiers have quota limits.
- Audio device behavior depends on drivers and Windows audio routing.
- Some live remix/edit versions may map to close but imperfect lyric entries.

## How to report beta feedback

Please include:

- Track name + artist
- What you expected
- What happened instead
- Steps to reproduce
- Screenshot/video if possible
- Any relevant lines from the Lyric Gen log panel

## OSC reference

- Tempo path: `/composition/tempocontroller/tempo`
  - Default mode: `tempo_osc_mode = normalized`
  - Optional mode: `tempo_osc_mode = raw_bpm`
- Utility output:
  - `/lyricgen/beat`
  - `/lyricgen/downbeat`
  - `/lyricgen/song/title`
  - `/lyricgen/song/artist`
  - `/lyricgen/lyrics/current`
  - `/lyricgen/lyrics/next`
  - `/lyricgen/lyrics/progress`
- Command input (default listen port `7020`):
  - `/lyricgen/cmd/start`
  - `/lyricgen/cmd/stop`
  - `/lyricgen/cmd/rescan`
  - `/lyricgen/cmd/lyrics_toggle`
  - `/lyricgen/cmd/sensitivity` (float)

## Licenses

See `CREDITS.txt`.
