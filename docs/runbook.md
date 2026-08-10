# Show-night runbook

What to do, in order, on the day. Written to be followed by someone who did not
build this — including you, at 1am, tired.

The QLC+ era had a runbook and the engine era did not, which is the gap this
closes. Read [`SAFETY.md`](SAFETY.md) once before your first show; it is short
and two of the things in it are not obvious.

---

## Before you leave

```bash
python scripts/preflight.py
```

Green means: every test suite passes, the schemas match the code, the patch
sheet validates, the rig loads, the venue checks out, QLC+ parity holds, and the
committed UI bundle matches its source. **Do not leave on a red preflight.** It
takes half a minute and it exists because every one of those has broken a show
setup at least once.

Pack list beyond the rig itself:

- the show laptop, its charger, and a **spare charger**
- a network switch and enough cable to reach the DMX node
- the DMX node / interface, and a spare DMX cable
- a phone with the console already bookmarked (see below — the token changes)
- a tape measure, for the one calibration input nobody remembers

## At the venue

### 1. Network first

The engine broadcasts Art-Net over UDP. Everything — the rig, the previz, the
phones — is on one flat network.

- Prefer a **dedicated switch** over the venue's wifi. Venue wifi has client
  isolation more often than not, and the symptom is a console that connects and
  then quietly does nothing.
- If you must use wifi, confirm a phone can reach the laptop's IP before doors.
- Note the laptop's IP. You will need it, and DHCP may have moved it since last
  time.

### 2. Power up the rig, then start the engine

```bash
python -m engine.server --artnet 255.255.255.255
```

Read the banner it prints. It states, every run:

- the **event** and how many fixtures
- the **output** — Art-Net destination, or `null (no wire)`
- the **safety taper** and its crowd level, or `TAPER DISABLED`
- the **strobe policy**, or `UNLIMITED`
- the **timing** settings that were applied
- **access** — the token, or `OPEN`
- the **URL** to open, token included

If the taper line says disabled, or the strobe line says unlimited, decide on
purpose whether that is what you want tonight.

### 3. Open the console

Use the printed URL **including its `?token=`**. The token is regenerated every
run — a bookmark from last week will connect and be read-only, and the console
says so in a banner rather than silently ignoring you.

The phone defaults to **Perform** mode: cue list, presets, master, tempo,
blackout, panic. Switch to **Design** in the header for setup and diagnostics.

### 4. Calibrate

The heads get nudged. Overnight, in transit, by someone leaning on the truss.

```bash
python -m engine.calibrate drift
```

This tells you whether anything has moved since the stored calibration. If it
has, re-aim on the **Setup** tab: jog a head onto the mirror ball, capture, then
capture two more targets, then solve.

> **Jog bypasses the safety taper**, necessarily — the taper works from the aim,
> the aim comes from the geometry, and the geometry is what you are establishing.
> The console shows a standing red banner whenever a head is jogging. Empty room
> only.

### 5. Check the room, not the numbers

Open the **Move** tab. The plan view draws the room from above with every lit
beam going where it actually goes. Look for beams landing somewhere you did not
intend before anyone is standing there.

Then walk the floor with a look up. The taper is a comfort feature, not a
guarantee.

## During the show

| you want | do this |
|---|---|
| the room dark, now | **Blackout** in the header. The show keeps running underneath, so releasing picks up where it got to. |
| something has gone wrong | **Panic**, bottom of the Show tab. Forces zeros onto the wire and stops evaluating at all — it does not need the show to be healthy. |
| one group too bright | **Bright** tab, pull its dimmer. It is a trim over the pattern, not a replacement. |
| a bump | **Flash**, held not latched. It sets rather than multiplies, so it works from a group you pulled to zero. |
| the whole night, hands-off-ish | **Show** tab, GO down the cue list. |
| the colours to crawl under a fast move | **Rate** on the Color tab. |

**Blackout is the one you reach for.** Panic is for when the engine itself is
wrong.

## When it goes wrong

### The console connects but nothing happens

Check the banner. In order:

1. **VIEW ONLY** — you opened the URL without its token.
2. **Master is at zero** or **Blackout** — it says so.
3. **PANIC** — it says so, and offers a one-tap release.
4. Nothing on screen? Then the engine is fine and the wire is not — go to the
   next section.

### The rig is not moving

- Is the engine printing frames? Check `stats` on the Setup tab: fps, dropped
  frames, evaluation errors.
- Is anything else sending Art-Net? **Only one sender.** Two both emitting to
  6454 means the rig takes whichever packet landed last, which looks like
  stuttering or like nothing.
- Is the node's universe right? The engine sends universe 0 by default.

### The engine died mid-set

The rig **holds its last frame** — DMX has no keepalive, so fixtures stay where
they were rather than going dark. You have time.

1. Restart it: `python -m engine.server --artnet 255.255.255.255`
2. **The token changes.** Re-open the console from the new URL.
3. The show restarts at its default look, not where you were. If you had a
   preset up, re-apply it; that is what presets are for.

To avoid the token churn during a rough night, restart with a fixed one:

```bash
python -m engine.server --artnet 255.255.255.255 --token tonight
```

### Failing back

There is no automatic fallback and that is deliberate — a second system that can
take over is a second system that can take over *by mistake*. If the engine
cannot be made to run, the honest fallback is house lights and an apology.
Blackout and panic both work as long as the engine is alive; if it is not, pull
the DMX node's power to blank the rig.

## After

- If you re-calibrated, the new numbers are already written. Commit them.
- If you built presets you want to keep, they are in
  `events/<event>/presets.json`. Commit them.
- If anything surprised you, write it down in the event's own README while it is
  still fresh. Every good comment in this repo started that way.
