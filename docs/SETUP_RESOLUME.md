# Connect Lyric Gen to Resolume

These settings follow `preferences_default.json` and `lyric_gen/core/osc_bridge.py`. They describe the current source defaults; verify the target OSC addresses in your installed Resolume composition before a show.

## Prepare a test composition

1. Create a Text Block source in layer 4, clip 4 for the current line.
2. Create a second Text Block source in layer 3, clip 4 for the next line.
3. In Lyric Gen preferences, change those layer/clip numbers if your composition uses different slots. The default source identifier is `blocktextgenerator`.
4. Set Resolume's OSC input port to `7000`, matching Lyric Gen's target port. The default target IP is `127.0.0.1`; use the Resolume computer's address for a network setup.

## Confirm the return path

Lyric Gen listens for feedback and commands on UDP `7001` by default. If that port is already occupied, the bridge tries `7020` and records the change in its log. Point your feedback/command sender at the port actually reported in the log.

Do not send incoming feedback to Resolume's input port by mistake: `7000` is the outgoing target from Lyric Gen, while `7001` or the fallback `7020` is Lyric Gen's receiver.

## Configure input and recognition

Choose an audio device in Preferences and inspect its input monitor. Automatic song recognition needs your own ACRCloud host, access key and secret. Without those credentials, the GUI and configuration workflow are available, but automatic identification is not a completed demo.

LRCLIB provides synchronized lyrics when it has a matching entry. Missing lyrics, remixes and alternate edits can require timing adjustments; do not treat recognition as a guarantee of lyric alignment.

## Test before performing

- Confirm the destination clips exist and use the configured text source type.
- Press **START** and inspect the log for the send destination and listen port.
- Confirm current and next lines reach the intended clips.
- Adjust lyric offset and check timing against the actual track.
- Press **STOP** and verify the services stop before changing routing.

The primary text address is:

```text
/composition/layers/{layer}/clips/{clip}/video/source/{type}/text/params/lines
```

Tempo uses `/composition/tempocontroller/tempo`. The default `normalized` mode maps BPM into 0–1 using the configured 20–500 BPM range; `raw_bpm` sends the numeric BPM. Match that choice to the receiver's actual behavior.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| No text | Destination IP/port, clip numbers, source type, and the source's actual OSC address |
| Commands do nothing | Receiver port in the log; another app may have caused the 7020 fallback |
| No input activity | Selected audio device and Windows audio routing |
| No recognized track | ACRCloud credentials, connectivity and quota |
| Lyrics drift | Track/version match and lyric offset |

Hardware audio capture, ACRCloud recognition and real Resolume playback must be checked on the target show machine. Source inspection or an offline check does not establish those results.
