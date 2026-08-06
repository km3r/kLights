// Hand-curated grouping of despacio.qxw's virtual console widgets into a
// phone-friendly layout. Matched against the live workspace BY CAPTION (not
// widget ID) by gen_webui_config.py -- see that file's docstring.
//
// Adding a new routine in QLC+:
//   1. Build it, wire it into the right SoloFrame, save.
//   2. `python gen_webui_config.py` -- it will appear under "Unsorted" on
//      the phone immediately, and print an error here if it's a dim-conflict
//      routine that needs classifying below.
//   3. Move its caption into the right section here when convenient.
//
// `items` arrays and the `dimPark`/`dimParkExempt` keys are read by a
// regex scrape in gen_webui_config.py, not a JS parser -- keep those three
// constructs as plain, single-block literals (what's already below already
// is).
window.LAYOUT = {
  // Rendered above every tab, always visible.
  header: {
    items: ['Master Dimmer', 'Blackout'],
  },

  tabs: [
    {
      id: 'show', label: 'Show', icon: '★', // star
      sections: [
        { title: 'Looks', style: 'tile-lg',
          items: ['Warm Up', 'Idle', 'Deep', 'Peak', 'Spiral', 'Landing'] },
        { title: 'Night', style: 'cuelist',
          items: ['Night'] },
        { title: 'Tempo', style: 'tempo',
          items: ['Tempo (tap 1 per bar)'] },
        { title: 'One-shots', style: 'tile',
          items: ['Build', 'Drop'] },
      ],
    },
    {
      id: 'color', label: 'Color', icon: '●', // filled circle
      sections: [
        // Yellow / Green / Cyan added 2026-07-30: the wheel has confirmed slots
        // for all three, but they only existed inside Split scenes, so there was
        // no way to put all four heads on one of them.
        { title: 'Statics', style: 'swatch',
          items: ['White', 'Red', 'Yellow', 'Green', 'Blue', 'Cyan', 'Pink', 'Orange',
                   'Color Auto', 'Sunset'] },
        { title: 'Loops', style: 'tile',
          items: ['Color Drift', 'Color Spin', 'Rainbow Wheel', 'Wheel Walk'] },
        // Quad Spectrum gives each head its OWN slot -- four interleaved speck
        // fields off the ball, where a Split gives two.
        { title: 'Split / Duo / Quad', style: 'tile',
          items: ['Split Warm/Cool', 'Split Red/Cyan', 'Split Pink/Green', 'Split Yellow/Blue',
                   'Split White/Orange', 'Split Red/Green', 'Duo Cyan/Pink', 'Duo Green/Blue',
                   'Quad Spectrum'] },
        { title: 'Color Wheel', style: 'slider',
          items: ['Color Scroll'] },
      ],
    },
    {
      id: 'move', label: 'Move', icon: '↔', // left-right arrow
      sections: [
        // Wall Graze is the only hold that rakes a beam down the FULL length of
        // each wall; "To Walls" and the orbit quarter-turns all point into the
        // head's own corner and land under a metre away.
        { title: 'Hold positions', style: 'tile',
          items: ['To Ball', 'To Floor', 'To Walls', 'To Crowd', 'Circle', 'Wall Graze'] },
        { title: 'Aerial', style: 'tile',
          items: ['Apex', 'Cathedral', 'Zenith', 'Rise', 'Iris', 'Canopy Ring'] },
        { title: 'Sweeps & programs', style: 'tile',
          items: ['Lazy Circle', 'Slow Sweep', 'Cross Weave', 'Grand Sweep', 'Diagonal Pulse',
                   'Corner Chase', 'Ball Spiral', 'Ball Wave', 'Neighbor Scan', 'Prowl',
                   'Crowd Cascade', 'Floor Ring', 'Floor Wipe', 'Floor Cross', 'Floor Bounce',
                   'Floor Breathe'] },
        // These seven auto-park MH Dim to 0 (see dimPark below) -- the frame
        // is literally named "Dark Moves (MH Dim low)" in QLC+.
        { title: 'Dark Moves', style: 'tile', badge: 'dims',
          items: ['Teleport', 'Apparition', 'Freeze Frame', 'Stutter', 'Glitch',
                   'Ascension', 'Blink'] },
        { title: 'Layers (run on top of a pose)', style: 'tile',
          items: ['Drift', 'Counter-Orbit', 'Shiver', 'Figure Eight', 'Diamond Weave'] },
        { title: 'Layer width', style: 'slider',
          items: ['Drift Width'] },
        { title: 'Pan/Tilt speed', style: 'slider',
          items: ['PT Speed'] },
      ],
    },
    {
      id: 'dim', label: 'Dim / FX', icon: '☀', // sun
      sections: [
        { title: 'Master Head Dimmer', style: 'slider-lg', badge: 'park-target',
          items: ['MH Dim'] },
        { title: 'Beam', style: 'tile',
          items: ['Beam Open'] },
        { title: 'Flash', style: 'flash',
          items: ['White Bump', 'Strobe'] },
        { title: 'Strobe rate', style: 'slider',
          items: ['MH Strobe'] },
        { title: 'Pulse', style: 'tile',
          items: ['Slow Pulse', 'Medium Pulse'] },
        // These auto-park MH Dim to 0 -- see dimPark below.
        { title: 'Dim patterns', style: 'tile', badge: 'dims',
          items: ['Dim Chase', 'Spotlight', 'MH Breathe'] },
        { title: 'Gobo', style: 'slider',
          items: ['MH Gobo'] },
      ],
    },
    {
      id: 'pins', label: 'Pins', icon: '◆', // diamond
      sections: [
        // Pin Split is the first pin look where the two fixtures differ (warm
        // one side of the ball, cool the other -> two counter-coloured speck
        // fields).
        { title: 'Looks', style: 'tile',
          items: ['Pin Glow', 'Pin Drift', 'Pin Amber', 'Pin Rose', 'Pin Magenta',
                   'Pin Indigo', 'Pin Teal', 'Pin Sea', 'Pin Rainbow', 'Pin Split'] },
        // Both write the pinspots' channel 0, which is BANDED on this fixture
        // (0-8 off / 9-134 white dimmer / 135-239 RGBW strobe / 240-255 full on)
        // -- so they exclude each other, and neither is a separate strobe
        // channel. Channel 5 is Auto FX and stays parked at 0. Pin Dim is
        // range-limited to 9-134 so the fader can never stray into the strobe
        // band by accident; Pin Strobe is the deliberate way in.
        // Both pulse ABOVE Pin Dim rather than being erased by it -- see the
        // module docstring in gen_webui_config.py. Not part of the HTP
        // dim-park system (Pin Dim is LTP, not HTP); flagged live-untested.
        { title: 'Layer (channel 0 -- mutually exclusive)', style: 'tile',
          items: ['Pin Breathe', 'Pin Strobe'] },
        { title: 'Pin Dimmer', style: 'slider',
          items: ['Pin Dim'] },
      ],
    },
    {
      id: 'setup', label: 'Setup', icon: '⚙', // gear
      sections: [
        { title: 'Corner Test (aim calibration, not meshed into the show)', style: 'tile',
          items: ['Test Corner 1', 'Test Corner 2', 'Test Corner 3', 'Test Corner 4'] },
        // Writes the fixture's Reset channel (DMX 250-255, held) -- most
        // moving heads need this held ~5s to re-home the pan/tilt steppers.
        // A Flash button so holding it down keeps the channel written;
        // releasing stops it. Run after any power-cycle/bump, before
        // trusting Corner Test or a fresh calibration -- see despacio/README.md.
        { title: 'Reset Heads (hold ~5s to re-home pan/tilt)', style: 'flash',
          items: ['Reset Heads'] },
        // Sideways mount: these knobs are labeled Pan/Tilt (DMX channel
        // names) but per despacio/README.md physically move the heads
        // up/down (Pan) and left/right (Tilt). Manual aim only works while
        // no position preset/EFX is running -- routines rewrite pan/tilt
        // every tick and will fight a manual knob move.
        { title: 'Manual aim -- Pan knobs move heads UP/DOWN', style: 'knob',
          items: ['Pan MH1', 'Pan MH2', 'Pan MH3', 'Pan MH4'] },
        { title: 'Manual aim -- Tilt knobs move heads LEFT/RIGHT', style: 'knob',
          items: ['Tilt MH1', 'Tilt MH2', 'Tilt MH3', 'Tilt MH4'] },
      ],
    },
  ],

  // Swatch fill colors for the Color > Statics tiles. Purely cosmetic --
  // an unmatched caption here is harmless (not linted as an error).
  colors: {
    'White': '#f4f1e8',
    'Red': '#e0362f',
    'Blue': '#2f6fe0',
    'Pink': '#e04fb0',
    'Orange': '#e08a2f',
    'Yellow': '#e0d02f',
    'Green': '#3fb84f',
    'Cyan': '#2fc4e0',
    'Color Auto': '#8a5fe0',
    'Sunset': '#e0602f',
  },

  // Every routine gen_webui_config.py's HTP conflict scan detects against
  // MH Dim must appear in exactly one of these two, or `--check` fails.
  // See despacio/webui/gen_webui_config.py's module docstring for the
  // detection rule and why Pin Dim/Pin Breathe never reach this list at all
  // (Pin Dim is LTP, not HTP -- a different, order-dependent mechanism).
  dimPark: {
    // Patterns -- only some heads are ever written; needs 0 to read at all.
    'Dim Chase': 0,
    'Spotlight': 0,
    // "Dark Moves (MH Dim low)" -- the QLC+ frame's own caption says so;
    // these blink Dimmer 0/255 across all 4 heads in lockstep, invisible
    // under a high fader.
    'Teleport': 0,
    'Apparition': 0,
    'Freeze Frame': 0,
    'Stutter': 0,
    // Glitch reuses Teleport's own scenes (just a different chaser timing);
    // Ascension/Blink are new dark-travel cycles built the same way -- all
    // three are full 0/255 Dimmer blinks across all 4 heads, same as the
    // four routines above.
    'Glitch': 0,
    'Ascension': 0,
    'Blink': 0,
    // Ends held on "Spotlight Step 1" (one head only) -- README: "Pull MH
    // Dim under it."
    'Drop': 0,
    // Boosts -- one head pushed to 255 while the other three ride the
    // fader; reads fine at any mid value, only dies exactly at 255. Park
    // to ~40% so the boosted head still reads as brighter than its
    // neighbours instead of a hard spotlight.
    'Prowl': 96,
    'Crowd Cascade': 96,
    // A slow swell to full and back on all 4 heads at once (step 2 writes
    // nothing, so the fall IS the fader baseline showing through). Parked low
    // but NOT 0: at 0 the trough is a full blackout every cycle, which reads as
    // a fault rather than a breath in a show pitched this slow. 48 leaves a dim
    // floor to breathe up from.
    'MH Breathe': 48,
  },
  // (empty for now -- nothing currently detected needs an exemption; White
  // Bump doesn't even trigger detection because it writes ch7=255 uniformly
  // to all 4 heads on its one and only step.)
  dimParkExempt: [],

  // Buttons whose function is an EFX-style "layer" that runs ON TOP OF a
  // pose rather than being one itself (app.js's "a layer is running with no
  // pose under it" banner). EFX functions write no Scene FixtureVal at all,
  // so this -- unlike poseCaptions/colorCaptions in the generated
  // ui_config.js, see gen_webui_config.py's resolve_movement_and_color() --
  // isn't structurally derivable from the qxw; it stays a hand-kept list,
  // but gen_webui_config.py's lint() validates every entry against real
  // widget captions so a rename here fails preflight instead of quietly
  // going stale.
  layerCaptions: ['Drift', 'Counter-Orbit', 'Shiver', 'Figure Eight', 'Diamond Weave'],
};
