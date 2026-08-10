"""
Tests for the DJ tempo ingest seam.

Driven over a REAL UDP socket against a REAL listener, for the same reason
test_server drives a real WebSocket: the parts most likely to be wrong are the
parts a mock would skip. The OSC decoder in particular is byte-alignment code,
and a mock that hands it a dict proves nothing about it.

The whole point of this milestone is that it is provable with no CDJs in the
room, so nothing here needs hardware.

Run: python engine/tests/test_sync.py
"""

import socket
import struct
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine import clock as clockmod
from engine import sync as syncmod

failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


# -- 1. the bar grid, which is the thing a beat packet is for -----------------
print("\n1. align_bar")

c = clockmod.MasterClock(bpm=120.0, now=0.0)
# 2 beats/second at 120 bpm. At t=1.75 the clock is at beat 3.5.
check("the clock is where we think it is", abs(c.beat(1.75) - 3.5) < 1e-9,
      f"{c.beat(1.75)}")

# The source says that instant is beat 0 of a bar. The nearest beat-0 is 4, not
# 0, so the correction is +0.5 rather than -3.5.
c.align_bar(1.75, 0.0)
check("aligning moves to the NEAREST equivalent beat",
      abs(c.beat(1.75) - 4.0) < 1e-9, f"{c.beat(1.75)}")

# The property that matters: a correction can never exceed half a bar, however
# wrong the grid was. Three beats of correction in one frame is a visible lurch
# in every running move, and it would happen on the first packet of every set.
worst = 0.0
for start in [x / 16 for x in range(64)]:
    probe = clockmod.MasterClock(bpm=120.0, now=0.0)
    probe.nudge_phase(start, 0.0)
    before = probe.beat(1.0)
    probe.align_bar(1.0, 0.0)
    worst = max(worst, abs(probe.beat(1.0) - before))
check("and never by more than half a bar", worst <= 2.0 + 1e-9,
      f"worst correction {worst:.3f} beats")

# Tempo is untouched: a beat packet says WHERE, the tempo field says how fast.
c.set_bpm(128.0, 2.0)
c.align_bar(2.0, 1.0)
check("aligning does not touch tempo", abs(c.bpm - 128.0) < 1e-9, f"{c.bpm}")


# -- 2. sync, and the trap it documents ---------------------------------------
print("\n2. sync")

c = clockmod.MasterClock(bpm=120.0, now=0.0)
c.sync(1.0, bpm=126.0, source="prolink", phrase_measured=True,
       phrase_label="Build", phrase_ends_in=16.0, at=1000.0)
check("bpm, source and phrase all land",
      abs(c.bpm - 126.0) < 1e-9 and c.source == "prolink"
      and c.phrase_measured and c.phrase_label == "Build",
      f"{c.bpm} {c.source} {c.phrase_label}")
check("the phrase end is stored as an ABSOLUTE beat, so the UI can count down "
      "without the bridge re-sending",
      abs(c.phrase_ends_at - (c.beat(1.0) + 16.0)) < 1e-9,
      f"ends at {c.phrase_ends_at:.2f}, now {c.beat(1.0):.2f}")

# An empty label CLEARS. A bridge that has lost the phrase must be able to say
# so -- leaving the last one up has the console announcing a drop that ended
# minutes ago.
c.sync(1.0, phrase_label="")
check("an empty phrase label clears rather than being stored",
      c.phrase_label is None, f"{c.phrase_label!r}")

try:
    c.sync(1.0, bpm=2.0)
    check("an absurd bpm is refused", False, "it was accepted")
except ValueError as exc:
    # A bridge with a units bug should produce an error, not a show at 2 BPM.
    check("an absurd bpm is refused", True, str(exc)[:50])

# THE trap, and the reason `beat_in_bar` exists at all. A source firing every
# beat that passes absolute `beat` re-anchors the timeline every packet; the
# same source passing `beat_in_bar` corrects phase by at most half a bar and
# leaves cumulative position running.
free = clockmod.MasterClock(bpm=120.0, now=0.0)
for k in range(1, 21):
    free.sync(k * 0.5, bpm=120.0, beat_in_bar=float(k % 4))
check("a per-beat source using beat_in_bar keeps a monotonic position",
      free.beat(10.0) > free.beat(9.0) > free.beat(8.0),
      f"{free.beat(8.0):.2f} -> {free.beat(9.0):.2f} -> {free.beat(10.0):.2f}")


# -- 3. taking the clock back -------------------------------------------------
print("\n3. unsync")

c = clockmod.MasterClock(bpm=120.0, now=0.0)
c.sync(1.0, bpm=132.0, source="prolink", phrase_measured=True,
       phrase_label="Drop", at=1000.0)
before = c.beat(2.0)
c.unsync()
check("taking over leaves tempo and phase exactly where the bridge left them",
      abs(c.beat(2.0) - before) < 1e-9 and abs(c.bpm - 132.0) < 1e-9,
      f"beat {c.beat(2.0):.4f}, bpm {c.bpm}")
check("but the source, phrase and staleness all reset",
      c.source == "tap" and not c.phrase_measured
      and c.phrase_label is None and c.synced_at is None,
      f"{c.source} {c.phrase_measured} {c.phrase_label!r} {c.synced_at}")


# -- 4. the wire: JSON ---------------------------------------------------------
print("\n4. JSON datagrams")

check("a plain sync parses", syncmod.parse(b'{"bpm": 128.5, "beat_in_bar": 2}')
      == {"bpm": 128.5, "beat_in_bar": 2.0})
check("unknown keys are ignored, not rejected -- a bridge sending extras keeps "
      "working", syncmod.parse(b'{"bpm": 128, "hotcue": 3}') == {"bpm": 128.0})
check("a bpm outside the range is dropped and the rest kept",
      syncmod.parse(b'{"bpm": 2, "phrase_label": "Drop"}')
      == {"phrase_label": "Drop"})
check("a datagram with nothing usable in it is None",
      syncmod.parse(b'{"hotcue": 3}') is None)
check("malformed JSON is None, not an exception",
      syncmod.parse(b'{"bpm": ') is None)
check("a JSON array is refused -- it is not a field set",
      syncmod.parse(b'[1, 2, 3]') is None)
check("random bytes are None", syncmod.parse(b'\x00\x01\x02') is None)
check("a string where a number belongs does not poison the whole packet",
      syncmod.parse(b'{"bpm": "fast", "phrase_label": "Build"}')
      == {"phrase_label": "Build"})
check("a long label is truncated rather than stored whole",
      len(syncmod.parse(b'{"phrase_label": "' + b"x" * 200 + b'"}')
          ["phrase_label"]) == 64)


# -- 5. the wire: OSC ----------------------------------------------------------
#
# beat-link-trigger is the reference implementation of the CDJ protocol and it
# already has phrase triggers; speaking its wire format is what makes it a
# drop-in bridge rather than something needing a shim.
print("\n5. OSC datagrams")


def osc(address: str, tag: str, value) -> bytes:
    """An OSC message, encoded independently of the decoder under test."""
    def pad(b: bytes) -> bytes:
        return b + b"\0" * (4 - len(b) % 4)
    body = pad(address.encode()) + pad(b"," + tag.encode())
    if tag == "i":
        body += struct.pack(">i", value)
    elif tag == "f":
        body += struct.pack(">f", value)
    elif tag == "s":
        body += pad(value.encode())
    return body


check("a float tempo decodes",
      syncmod.parse(osc("/beat-link/bpm", "f", 128.0)) == {"bpm": 128.0})
check("an int beat-within-bar decodes",
      syncmod.parse(osc("/beat-link/beat", "i", 3)) == {"beat_in_bar": 3.0})
check("a string phrase decodes, and claims phrase is measured with it",
      syncmod.parse(osc("/beat-link/phrase", "s", "Chorus"))
      == {"phrase_label": "Chorus", "phrase_measured": True})
# Matched on the last path component, so renaming the namespace in
# beat-link-trigger is a configuration choice rather than a silent failure.
check("a renamed namespace still matches",
      syncmod.parse(osc("/klights/from-the-decks/bpm", "f", 124.0))
      == {"bpm": 124.0})
check("an address we do not model is ignored",
      syncmod.parse(osc("/beat-link/hotcue", "i", 2)) is None)

# rkbx_link -- the rekordbox path, and the DDJ path, since a DDJ-1000 is USB and
# never speaks Pro DJ Link. Its addresses are NESTED, which broke the first
# version of this: `/master/bpm/current` and `/master/phrase/current` both end
# in "current", so matching the last component alone read a phrase as a tempo.
print("\n5b. rkbx_link address shapes")
check("a nested bpm decodes",
      syncmod.parse(osc("/master/bpm/current", "f", 128.0)) == {"bpm": 128.0})
check("and a phrase sharing its last component does NOT become a bpm",
      syncmod.parse(osc("/master/phrase/current", "s", "Drop"))
      == {"phrase_label": "Drop", "phrase_measured": True},
      "this is the collision that would have shipped")
check("a phrase count-in becomes the countdown",
      syncmod.osc_fields("/master/phrase/countin", 12.0)
      == {"phrase_ends_in": 12.0})
check("the track title comes through",
      syncmod.osc_fields("/master/track/title", "Cosmic Slop")
      == {"track": "Cosmic Slop"})

# A source that STATES the phrase is measuring it. OSC cannot send the flag
# separately, and without inferring it the rekordbox path would report phrases
# while auto look changes quietly kept landing on bars.
check("a phrase label implies phrase is measured",
      syncmod.osc_fields("/master/phrase/current", "Build")["phrase_measured"]
      is True)

# beat/subdiv is a 0..1 ramp looping every n beats -- the bar phase, and the
# most useful thing rkbx_link sends. Taking the raw 0..1 would squeeze every
# downbeat correction into the first beat of the bar.
check("beat/subdiv scales back up to beats",
      syncmod.osc_fields("/master/beat/subdiv/4", 0.5) == {"beat_in_bar": 2.0})
check("and a divisor of zero is refused rather than dividing the bar by nothing",
      syncmod.osc_fields("/master/beat/subdiv/0", 0.5) is None)

# The deck rule. Following per-deck addresses means two decks fighting over one
# clock mid-blend, and the resulting tempo belongs to neither.
check("only the master deck is followed",
      syncmod.osc_fields("/1/bpm/current", 128.0) is None
      and syncmod.osc_fields("/2/bpm/current", 174.0) is None,
      "a numeric deck is dropped")
check("but master itself drives",
      syncmod.osc_fields("/master/bpm/current", 128.0) == {"bpm": 128.0})
# Dropping them shows up as rejected packets, which is visible; following the
# last deck that spoke would be invisible and sound like a broken engine.
check("and beat-link-trigger's flat namespace still works alongside it",
      syncmod.osc_fields("/beat-link/bpm", 124.0) == {"bpm": 124.0})
check("a truncated OSC message is None, not an exception",
      syncmod.parse(b"/beat-link/bpm\0\0,f\0\0\x43") is None)
check("an OSC type we do not model is refused rather than misread",
      syncmod.parse(b"/beat-link/bpm\0\0,b\0\0\x00\x00\x00\x01\xff\0\0\0")
      is None)


# -- 6. the listener, over a real socket --------------------------------------
print("\n6. the UDP listener")

seen: list[dict] = []
listener = syncmod.SyncListener(on_sync=seen.append, port=0, bind="127.0.0.1")
listener.start()
port = listener.sock.getsockname()[1]

syncmod.send({"bpm": 127.0, "beat_in_bar": 1, "phrase_label": "Build"},
             port=port)
deadline = time.time() + 2
while not seen and time.time() < deadline:
    time.sleep(0.02)
check("a datagram reaches the callback",
      seen and seen[0]["bpm"] == 127.0, f"{seen}")

# The security property, and the reason it is structural rather than a promise:
# the listener parses into clock fields and the CALLER builds the command, so
# there is no path from this port to anything else the engine can do.
with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
    s.sendto(b'{"type": "patch_remove", "name": "Moving Head #1"}',
             ("127.0.0.1", port))
    s.sendto(b'{"type": "panic"}', ("127.0.0.1", port))
    s.sendto(b'garbage', ("127.0.0.1", port))
    s.sendto(b'{"bpm": 130}', ("127.0.0.1", port))
deadline = time.time() + 2
while len(seen) < 2 and time.time() < deadline:
    time.sleep(0.02)
check("a command sent to the tempo port cannot become a command",
      all("type" not in f for f in seen), f"{seen}")
check("and the fields that ARE clock fields still get through",
      any(f.get("bpm") == 130.0 for f in seen), f"{seen}")
check("unreadable datagrams are counted, so 'no bridge' and 'a bridge I cannot "
      "read' are distinguishable",
      listener.rejected >= 3, f"{listener.status()}")

# One bad callback must not take the listener down: the bridge keeps sending
# and the show keeps running.
boom = syncmod.SyncListener(
    on_sync=lambda f: (_ for _ in ()).throw(RuntimeError("nope")),
    port=0, bind="127.0.0.1")
boom.start()
boom_port = boom.sock.getsockname()[1]
syncmod.send({"bpm": 120.0}, port=boom_port)
time.sleep(0.3)
check("a raising callback does not kill the listener thread",
      boom.thread.is_alive() and boom.rejected == 1, f"{boom.status()}")
boom.stop()

listener.stop()
check("stopping releases the socket", listener.sock is None)
# Rebinding the same port proves it was really released rather than lingering.
again = syncmod.SyncListener(on_sync=seen.append, port=port, bind="127.0.0.1")
try:
    again.start()
    check("and the port can be bound again", True)
    again.stop()
except OSError as exc:
    check("and the port can be bound again", False, str(exc))


# -- 7. the bridge sidecar, against a real listener ---------------------------
#
# The reason --fake exists is that the whole downstream path has to be provable
# at a desk. If this section needs a CDJ, --fake has failed at its only job.
print("\n7. the fake bridge")

sys.path.insert(0, str(REPO / "bridges" / "prolink"))
import bridge as bridgemod                            # noqa: E402

got: list[dict] = []
sink = syncmod.SyncListener(on_sync=got.append, port=0, bind="127.0.0.1")
sink.start()
sink_port = sink.sock.getsockname()[1]

# 17 packets: four bars plus one, so the bar wrap and a second downbeat both
# happen. `run` sleeps by the beat interval, so a high BPM keeps this quick.
sent = bridgemod.run(bridgemod.fake(240.0), "127.0.0.1", sink_port,
                     verbose=False, limit=17)
deadline = time.time() + 3
while len(got) < sent and time.time() < deadline:
    time.sleep(0.02)
check("every packet the bridge sent arrived readable", len(got) == sent,
      f"{len(got)}/{sent}, {sink.rejected} rejected")

# The discipline a per-beat source has to keep: bpm and beat_in_bar, never
# absolute `beat`. A generator that cheated here would prove the seam works in a
# way a real bridge could not reproduce.
check("it sends beat_in_bar and never absolute beat",
      all("beat" not in f for f in got)
      and all("beat_in_bar" in f for f in got), f"{got[0]}")
check("bar phase actually cycles",
      sorted({f["beat_in_bar"] for f in got}) == [0.0, 1.0, 2.0, 3.0],
      f"{sorted({f['beat_in_bar'] for f in got})}")
check("phrase is claimed as measured, which is the whole point",
      all(f.get("phrase_measured") for f in got))

# The label rides on downbeats only. Sparse on purpose: the engine stores an
# absolute end beat so a chatty sender is not required, and a bridge that
# re-sent every beat would never exercise that.
labelled = [f for f in got if "phrase_label" in f]
check("the phrase label rides on downbeats, not every beat",
      len(labelled) == len([f for f in got if f["beat_in_bar"] == 0.0])
      and 0 < len(labelled) < len(got),
      f"{len(labelled)} of {len(got)}")
check("and the countdown shrinks bar by bar",
      [f["phrase_ends_in"] for f in labelled]
      == sorted((f["phrase_ends_in"] for f in labelled), reverse=True),
      f"{[f['phrase_ends_in'] for f in labelled]}")

# The scripted timeline has to actually reach a Drop, or nothing downstream of
# a phrase can be tested with it.
timeline = [name for name, _ in bridgemod.FAKE_PHRASES]
check("the scripted night contains the phrases the cue list cares about",
      {"Build", "Drop", "Outro"} <= set(timeline), f"{timeline}")

sink.stop()

# --replay, on a capture written here so the format is proved rather than
# assumed.
capture = REPO / "engine" / "tests" / "data" / "sync-replay.jsonl"
capture.parent.mkdir(parents=True, exist_ok=True)
capture.write_text(
    '# a two-packet capture\n'
    '{"dt": 0.0, "bpm": 126.0, "beat_in_bar": 0, "phrase_label": "Intro"}\n'
    'not json at all\n'
    '{"dt": 0.0, "bpm": 126.0, "beat_in_bar": 1}\n', encoding="utf-8")
replayed = list(bridgemod.replay(capture))
check("a capture replays, comments and all",
      len(replayed) == 2 and replayed[0][1]["phrase_label"] == "Intro",
      f"{replayed}")
check("and a corrupt line is skipped rather than ending the replay",
      replayed[1][1]["beat_in_bar"] == 1)
check("--live refuses honestly instead of half-working",
      bridgemod.main(["--live"]) == 2)
capture.unlink()

# --osc: the same feed shaped as rkbx_link, which is the only way the rekordbox
# path's decoder gets exercised without a DDJ, rekordbox and a licensed copy of
# rkbx_link in one room.
osc_got: list[dict] = []
osc_sink = syncmod.SyncListener(on_sync=osc_got.append, port=0,
                                bind="127.0.0.1")
osc_sink.start()
bridgemod.run(bridgemod.fake(240.0), "127.0.0.1",
              osc_sink.sock.getsockname()[1], verbose=False, limit=5,
              use_osc=True)
deadline = time.time() + 3
while len(osc_got) < 5 and time.time() < deadline:
    time.sleep(0.02)
merged: dict = {}
for f in osc_got:
    merged.update(f)
check("the fake bridge's OSC round-trips through the engine's decoder",
      merged.get("bpm") == 240.0 and "beat_in_bar" in merged
      and merged.get("phrase_label") == "Intro"
      and merged.get("phrase_measured") is True,
      f"{merged}")
check("and nothing it sent was unreadable", osc_sink.rejected == 0,
      f"{osc_sink.status()}")
osc_sink.stop()


print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("sync: all checks pass")
