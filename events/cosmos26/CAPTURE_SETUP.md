# Capture (Student Edition) — Live Previs Setup

QLC+ → Art-Net (Universe 0) → **Capture**. The pipe is already proven (Capture's DMX
activity tracks QLC+). This doc is the rebuild script (SE can't save).

SE limits that matter: **no save**, **90-min sessions**, **no motion/effects/lasers**,
and a **tiny fixture library**. So we represent the rig with the closest SE fixtures —
for previs, only the **channel footprint** (count + order) needs to line up, not the brand.

---

## The SE fixtures we'll use

- **Chauvet COLORdash Par-Quad 7** — RGBW par. The workhorse: stands in for **all
  color washes** (4 Pars, 2 Pinspots) and as a placeholder blob for the effects.
- **JTE Parcan 64** — 1-channel dimmer. For the **Dimmer/LED-strip** channels.
- **Showtec Sunstrip Active DMX** — a light bar (only if you add one to the rig).
- No laser / no derby in SE → Scorpion is skipped; Derby/Mini Kinta become placeholder
  COLORdash blobs (they'll show color/dimmer, just no spin or beams).

---

## Phase 1 — Network ✅ DONE
Capture owns UDP 6454, universe input = Art-Net Universe 0, activity tracks QLC+.
(Keep Blender's Art-Net receiver OFF so it doesn't fight for the port.)

## Phase 2a — Patch table (Universe 0, 1-based addresses)

| QLC+ fixture | Capture SE fixture | Addr | Notes |
|---|---|---|---|
| Par #1 | COLORdash Par-Quad 7 | **1** | red-test the color order |
| Par #2 | COLORdash Par-Quad 7 | **7** | |
| Par #3 | COLORdash Par-Quad 7 | **13** | |
| Par #4 | COLORdash Par-Quad 7 | **19** | |
| Pinspot #1 | COLORdash Par-Quad 7 | **25** | same fixture, smaller/tighter |
| Pinspot #2 | COLORdash Par-Quad 7 | **31** | |
| DerbyLaserParty | COLORdash (placeholder) or skip | **37** | no derby in SE |
| Scorpion (laser) | **skip** | (50) | no laser in SE — that's fine |
| Mini Kinta | COLORdash (placeholder) | **61** | shows color, no spin |
| Dimmer #1 (LED strip) | JTE Parcan 64 | **70** | exact (1ch) |
| Dimmer #2–4 (spare) | JTE Parcan 64 | **71, 72, 73** | |
| (optional NEW light bar) | Showtec Sunstrip Active | **80** | add to QLC+ too, at a free address |

### TWO things that decide whether colors look right
1. **Dimmer-first channel order.** Pick a COLORdash **mode whose channel 1 is Dimmer,
   then R, G, B** (so it lines up with how QLC+ sends Dim/R/G/B). If you pick an
   RGB-first mode, QLC+'s dimmer value drives "red" and everything's wrong.
2. **Keep the mode ≤ 6 channels.** Your Pars are spaced 6 apart (1→7→13→19). If the
   COLORdash mode uses 7+ channels, fixture #1 reads into fixture #2's address and you
   get cross-talk glitches. A 4–6-channel Dimmer+RGB(W) mode fits cleanly.

→ A COLORdash mode like **Dim, R, G, B, W (5ch)** is ideal: maps Dim/R/G/B perfectly,
the extra W just reads your unused strobe channel (harmless), and it fits the 6-ch slot.

## Phase 2b — Tracer bullet (do ONE before all 13)
1. Add **one COLORdash Par-Quad 7**, pick a Dimmer-first ≤6ch mode, patch **Universe 0,
   addr 1**.
2. In QLC+, fire solid **RED** on Par #1 (Red full, dimmer up).
3. Capture shows **red** → order is right, replicate. Shows white/green/dark → change
   the COLORdash mode and retry. Find the bug on #1, not #13.

## Phase 2c — Fan out
Patch the other COLORdashes (7/13/19/25/31), the Parcans (70–73), placeholders for
Derby/Kinta (37/61), and the Sunstrip if you're adding one. **Patch first, position last.**

## Phase 2d — Positions (real-world, stage center = origin, crowd in front)
15×15 ft stage, 30×60 ft dancefloor, front truss ≈ 11.5 ft (3.5 m):
- 4 COLORdash "Pars" across the front-top truss, aimed down-forward at the DJ/center.
- 2 COLORdash "Pinspots" between them, tighter, cross-aimed on the DJ.
- Derby/Kinta placeholders on the audience truss, aimed into the crowd.
- Parcans/LED strip along the front edge of the stage floor.

## Phase 3 — Verify live
1. QLC+ Operate.
2. Solid red → all "Pars" red (not white/green).
3. Dimmer submaster fades them smoothly.
4. Run a chase → colors animate live with Capture's haze/beams.
