# MIDI sidecar

kLights' MIDI cues, out of a real MIDI port -- to a lighting desk, a VJ app's
MIDI input, a drum machine, anything that takes MIDI.

The engine is stdlib-only and its output thread must never wait on a driver,
so it never opens a MIDI port. Each frame it sends that frame's MIDI messages
to this small separate program as one JSON datagram on local UDP; this program
owns the port. If it is not running, nothing happens to the lights.

```bash
pip install -r bridges/midi/requirements.txt     # mido + python-rtmidi, pinned

python bridges/midi/midi_out.py --list           # the MIDI outputs on this machine
python bridges/midi/midi_out.py --midi loopMIDI  # the first whose name contains this
python bridges/midi/midi_out.py --virtual kLights   # macOS/Linux: a port others open
python bridges/midi/midi_out.py --fake           # print instead; no MIDI library needed
```

On Windows, make a loopback port with [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html)
first; on macOS the IAC Driver does the same.

Then point the engine at it, in the show folder's `show.json` or this
machine's `klights.local.json`:

```json
"outputs": {"midi": {}}
```

`{}` is the sidecar on this machine at its default port, 9123; `host` and
`port` change that (`--port` on the sidecar to match).

## What plays

A MIDI lane in the designer (`+ lane` → **MIDI cues**) holds cues, each one of:

| cue | at its start | at its end |
|---|---|---|
| `note` (+ `velocity`, default 100) | note on | note off |
| `cc` (+ `value`, default 127) | that value | `off_value`, if given |
| `pc` | program change | -- |

Channels are 1-16: the cue's, else the lane's, else 1. A **MIDI curve** lane
drives one `cc` from a drawn 0-1 curve, sent as 0-127 when it changes, at most
30 times a second. Routines can carry MIDI lanes too, so a template set plays
them on any track. Cues follow the deck like the lights: a loop or hot cue
into a note starts it, out of one stops it, and disarming Follow stops
everything.

## The wire

```json
{"klights": "klights.midi/1", "messages": [
  {"type": "note_on", "channel": 1, "note": 60, "velocity": 100},
  {"type": "control_change", "channel": 2, "control": 7, "value": 127}]}
```

`note_on`, `note_off`, `control_change` and `program_change`; every number
0-127, channels 1-16. Any other shape rejects the whole datagram -- counted,
and said once per new reason -- so a malformed datagram is never half-played.

It listens on 127.0.0.1 unless `--bind` says otherwise; nothing authenticates
a datagram, so opening it to the network lets anyone who reaches the port play
notes. On the way out, every note it started is stopped.

`engine/tests/test_outputs.py` runs it with `--fake` against the engine's own
`MidiOut`; no MIDI hardware or library is needed for the tests.
