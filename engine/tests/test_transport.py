"""
Tests for the transport: which track, and where in it, between packets.

Pure and timestamped -- every packet is (fields, arrival time) and every reading
names its instant -- so each behaviour is a short, exact script rather than a
race against a real socket. The scripts imitate what the two sources really do:
rkbx_link's ~60 Hz position from a jittery Windows timer, silent while paused,
with the identity arriving a field at a time; beat-link-trigger's 25 Hz
position with an explicit playing flag and the identity in one message.

Run: python engine/tests/test_transport.py
"""

import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine import transport as tp  # noqa: E402

failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def rkbx_track(t: tp.TrackTransport, at: float, title: str, artist="A", album="B"):
    """rkbx_link's way: three messages a few milliseconds apart."""
    t.ingest({"title": title, "source": "rkbx"}, at)
    t.ingest({"artist": artist, "source": "rkbx"}, at + 0.002)
    t.ingest({"album": album, "source": "rkbx"}, at + 0.004)


def play(t: tp.TrackTransport, start_pos: float, start_at: float, seconds: float,
         rate=1.0, hz=60.0, jitter=0.004, seed=1):
    """Feed positions at `hz`, arriving with up to `jitter` seconds of delay.
    Returns the arrival time of the last packet."""
    rnd = random.Random(seed)
    n = int(seconds * hz)
    at = start_at
    for i in range(n):
        sent = start_at + i / hz
        at = sent + rnd.uniform(0, jitter)
        t.ingest({"track_time": start_pos + (sent - start_at) * rate, "source": "rkbx"}, at)
    return at


print("\n1. nothing, then a track")
t = tp.TrackTransport()
s = t.sample(0.0)
check("no track before anything arrives", s.state == tp.NO_TRACK and s.time_s is None)
rkbx_track(t, 1.0, "Night Drive")
check("the identity waits out its settle window",
      t.sample(1.05).state == tp.NO_TRACK)
t.ingest({"track_time": 30.0, "source": "rkbx"}, 1.08)
t.ingest({"track_time": 30.04, "source": "rkbx"}, 1.12)
s = t.sample(1.12)
check("then commits, all three fields together",
      s.identity.title == "Night Drive" and s.identity.artist == "A"
      and s.identity.album == "B" and s.track_seq == 1, f"{s.identity}")
check("and the packet held during the window became its position",
      s.state == tp.PLAYING and abs(s.time_s - 30.04) < 0.005, f"{s.time_s}")


print("\n2. playing, smoothed")
t = tp.TrackTransport()
rkbx_track(t, 0.0, "Steady")
rnd = random.Random(7)
errors = []
for i in range(300):
    sent = 0.2 + i / 60
    at = sent + rnd.uniform(0, 0.004)
    t.ingest({"track_time": 10.0 + (sent - 0.2), "source": "rkbx"}, at)
    probe = sent + 0.5 / 60                        # between this packet and the next
    if probe > at and i > 30:                      # after the line has settled
        errors.append(abs(t.sample(probe).time_s - (10.0 + probe - 0.2)))
last = at
check("60 Hz with 4 ms of jitter tracks within 5 ms, between packets",
      max(errors) < 0.005, f"worst {max(errors) * 1000:.2f} ms")
check("and never counts jitter as a jump", t.sample(last).jump_seq == 0)
before = t.sample(last + 0.05).time_s
check("between packets it extrapolates", before > t.sample(last).time_s)

t = tp.TrackTransport()
rkbx_track(t, 0.0, "Pitched")
t.ingest({"bpm": 132.0, "bpm_original": 124.0}, 0.1)
last = play(t, 0.0, 0.2, 3.0, rate=132 / 124)
s = t.sample(last)
check("with bpm and bpm_original, the rate is the pitch",
      abs(s.rate - 132 / 124) < 1e-9, f"{s.rate}")
t = tp.TrackTransport()
rkbx_track(t, 0.0, "Unknown pitch")
last = play(t, 0.0, 0.2, 4.0, rate=1.06)
check("without them, the rate is learned from the packets",
      abs(t.sample(last).rate - 1.06) < 0.02, f"{t.sample(last).rate:.4f}")


print("\n3. silence")
t = tp.TrackTransport(grace_s=4.0)
rkbx_track(t, 0.0, "Pause me")
last = play(t, 50.0, 0.2, 2.0, jitter=0.0)
held = t.sample(last + 0.15).time_s
check("a late packet is STALLED, not paused -- a dropped packet must not "
      "freeze the rig", t.sample(last + 0.5).state == tp.STALLED)
check("and the estimate stops at the stall limit rather than running on",
      abs(t.sample(last + 3.0).time_s - t.sample(last + 0.5).time_s) < 1e-9)
check("silence past the grace period is PAUSED -- rkbx_link's only pause signal",
      t.sample(last + 4.5).state == tp.PAUSED)
last2 = play(t, 52.0, last + 6.0, 1.0, jitter=0.0)
s = t.sample(last2)
check("resuming where it stopped is not a jump", s.state == tp.PLAYING
      and s.jump_seq == 0, f"{s.state} jumps={s.jump_seq}")

b = tp.TrackTransport()
b.ingest({"rekordbox_id": 412, "title": "BLT track", "source": "blt"}, 0.0)
b.ingest({"track_time": 20.0, "playing": True, "pitch": 1.0, "source": "blt"}, 0.01)
b.ingest({"track_time": 20.04, "playing": True, "source": "blt"}, 0.05)
b.ingest({"track_time": 20.08, "playing": False, "source": "blt"}, 0.09)
s = b.sample(2.0)
check("beat-link-trigger's playing flag pauses at once, where it stopped",
      s.state == tp.PAUSED and abs(s.time_s - 20.08) < 0.01, f"{s.state} {s.time_s}")
check("its identity needs no settle window -- it comes in one message",
      s.identity.rekordbox_id == 412 and s.track_seq == 1)


print("\n4. loops, hot cues and scratching")
t = tp.TrackTransport()
rkbx_track(t, 0.0, "Loopy")
last = play(t, 60.0, 0.2, 2.0, jitter=0.002)
seq = t.sample(last).jump_seq
t.ingest({"track_time": 58.0}, last + 0.017)           # a 2-second loop back
check("from rkbx_link a jump is held for a moment before it counts",
      t.sample(last + 0.02).jump_seq == seq)
t.ingest({"track_time": 58.017}, last + 0.034)
t.ingest({"track_time": 58.034}, last + 0.051)
s = t.sample(last + 0.052)
check("then counts, and lands where the deck went",
      s.jump_seq == seq + 1 and abs(s.time_s - 58.035) < 0.01, f"{s.time_s}")
for k, pos in enumerate((120.0, 120.017, 120.034)):    # a hot cue forward
    t.ingest({"track_time": pos}, last + 0.068 + 0.017 * k)
check("a hot cue forward is a jump too", t.sample(last + 0.11).jump_seq == seq + 2)
check("and neither is a track change", t.sample(last + 0.11).track_seq == 1)
b = tp.TrackTransport()
b.ingest({"rekordbox_id": 9, "title": "BLT", "source": "blt"}, 0.0)
b.ingest({"track_time": 30.0, "playing": True, "pitch": 1.0}, 0.04)
b.ingest({"track_time": 30.04}, 0.08)
b.ingest({"track_time": 10.0}, 0.12)
check("from beat-link-trigger, which names its track whole, a jump counts at once",
      b.sample(0.121).jump_seq == 1 and abs(b.sample(0.121).time_s - 10.0) < 0.01)

t = tp.TrackTransport()
rkbx_track(t, 0.0, "Scratchy")
last = play(t, 80.0, 0.2, 2.0, jitter=0.0)
began = t.sample(last).time_s
pos, at = began, last
for _ in range(6):                     # back-and-forth on the platter
    at += 0.05; pos -= 0.3
    t.ingest({"track_time": pos}, at)
    at += 0.05; pos += 0.15
    t.ingest({"track_time": pos}, at)
s = t.sample(at + 0.01)
check("scratching holds the position where it began",
      s.state == tp.REVERSE and abs(s.time_s - began) < 0.08, f"{s.state} {s.time_s} vs {began}")
last = play(t, pos, at + 0.02, 1.0, jitter=0.0)
check("and lets go once the deck runs forward again",
      t.sample(last).state == tp.PLAYING)


print("\n5. a master switch, the rkbx_link way")
t = tp.TrackTransport()
rkbx_track(t, 0.0, "Outgoing")
last = play(t, 200.0, 0.2, 2.0, jitter=0.0)
jumps = t.sample(last).jump_seq
# The new deck's position arrives BEFORE its title has finished arriving.
t.ingest({"track_time": 12.0}, last + 0.010)
rkbx_track(t, last + 0.012, "Incoming")
t.ingest({"track_time": 12.03}, last + 0.040)
check("a position from the new deck inside the settle window is held, "
      "not applied to the old track",
      t.sample(last + 0.05).identity.title == "Outgoing"
      and t.sample(last + 0.05).jump_seq == jumps)
last2 = play(t, 12.2, last + 0.2, 1.0, jitter=0.0)
s = t.sample(last2)
check("on commit it is a track change, not a jump",
      s.identity.title == "Incoming" and s.track_seq == 2 and s.jump_seq == jumps,
      f"{s.identity.title} track_seq={s.track_seq} jumps={s.jump_seq}")
check("and the new track's position is right", abs(s.time_s - (12.2 + 0.983)) < 0.02,
      f"{s.time_s}")
rkbx_track(t, last2 + 0.1, "Incoming")
t.ingest({"track_time": 13.4}, last2 + 0.2)
check("the same title re-sent is not a change", t.sample(last2 + 0.3).track_seq == 2)

t = tp.TrackTransport(min_track_change_s=2.0)
t.ingest({"title": "One"}, 0.0)
t.ingest({"track_time": 1.0}, 0.2)
t.ingest({"title": "Two"}, 0.3)
t.ingest({"track_time": 1.0}, 0.5)
check("track changes faster than min_track_change_s wait their turn",
      t.sample(0.5).identity.title == "One")
t.ingest({"track_time": 1.0}, 2.3)
check("and land once it has passed", t.sample(2.3).identity.title == "Two")


print("\n6. latency and labels")
t = tp.TrackTransport(latency_s={"rkbx": -0.05})
rkbx_track(t, 0.0, "Early")
t.ingest({"track_time": 10.0, "source": "rkbx", "deck": "2", "on_air": True,
          "beat_in_bar": 1.0}, 0.2)
t.ingest({"track_time": 10.0, "source": "rkbx"}, 0.2)
plain = tp.TrackTransport()
rkbx_track(plain, 0.0, "Early")
plain.ingest({"track_time": 10.0, "source": "rkbx"}, 0.2)
check("a source's latency offset shifts where it is sampled",
      abs((plain.sample(0.25).time_s - t.sample(0.25).time_s) - 0.05) < 1e-6,
      f"{plain.sample(0.25).time_s} vs {t.sample(0.25).time_s}")
s = t.sample(0.25)
check("the deck, on-air and bar phase ride along for the console and the "
      "phase check", s.deck == "2" and s.on_air is True and s.beat_in_bar == 1.0
      and s.source == "rkbx")
t.clear()
check("taking the clock back forgets the track", t.sample(0.3).state == tp.NO_TRACK)

print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("transport: all checks pass")
