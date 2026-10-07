import type { Guide } from "./Guide";

/**
 * What the console's guides say.
 *
 * Written for someone holding a phone who did not build this. Every control is
 * named in bold exactly as its label reads, so a step can be followed by
 * looking for the word. Nothing here is a second source of truth about
 * behaviour: where a guide says what a control does, the engine's own docstring
 * says why, and a change to one is a change to the other.
 *
 * Rig-agnostic on purpose. "Spotlight" and "the Night cue list" are despacio's;
 * a guide that named them would be wrong at the next event.
 */

export type GuideId = "start" | "show" | "color" | "move" | "bright" | "setup" | "designer";

export interface ConsoleGuide extends Guide {
  id: GuideId;
  /** The tab it is about, which it offers to open. */
  tab?: string;
}

export const CONSOLE_GUIDES: ConsoleGuide[] = [
  {
    id: "start",
    label: "Start",
    title: "The console in two minutes",
    lead: <>Run the lights from your phone. Everyone on the console sees the same
      live show, so there's nothing to save or sync.</>,
    sections: [
      {
        title: "Always on screen",
        notes: [
          <>The <b>dot</b> shows the connection: green is live, amber is
            connecting, red is lost. If it drops, the rig holds its last
            frame.</>,
          <>Next to the tempo, four <b>beat lights</b> count the bar.</>,
          <><b>Master</b> scales everything. <b>Blackout</b> takes it to zero,
            but the show keeps running, so it picks up where it was.</>,
          <><b>Perform</b> shows only what makes light. <b>Design</b> adds
            setup and readouts. It's per device, not a lock.</>,
          <><b>Banners</b> under the header tell you why the lights aren't
            doing what you expect. Check them first.</>,
        ],
      },
      {
        title: "Three ideas",
        notes: [
          <><b>Movement, colour and level are separate.</b> Move, Color and
            Bright each change one without touching the others.</>,
          <><b>Presets stay put.</b> Show has pages of eight pads, and saving
            over a preset keeps its pad.</>,
          <><b>The cue list is the night.</b> Press GO to step through it.
            Anyone can run a show this way.</>,
        ],
      },
      {
        title: "Try it",
        steps: [
          <>On <b>Color</b>, tap a colour.</>,
          <>On <b>Move</b>, tap a route. The colour doesn't change.</>,
          <>On <b>Bright</b>, pull a dimmer down a little.</>,
          <>On <b>Show</b>, type a name under the presets and tap <b>Save</b>.
            That pad now brings back all three.</>,
          <>Tap <b>Blackout</b>, wait, then tap it again. The move kept going
            in the dark.</>,
        ],
      },
      {
        title: "Good to know",
        notes: [
          <>Several people can use it at once. Nothing locks; in Design, Setup
            shows who's connected.</>,
          <>A link without its <code>?token=</code> is view-only. Use the full
            link the engine printed.</>,
          <>Tap <b>?</b> in the header for the guide to the tab you're on. A{" "}
            <b>?</b> next to a card's title explains that card.</>,
          <>Read <code>docs/SAFETY.md</code> before pointing this at people.
            The show-night checklist is <code>docs/runbook.md</code>.</>,
        ],
      },
    ],
  },
  {
    id: "show",
    label: "Show",
    title: "Show: running the night",
    tab: "show",
    lead: <>Controls for the whole rig: cues, presets, tempo and auto mode.
      Colours and moves have their own tabs.</>,
    sections: [
      {
        title: "Try it",
        steps: [
          <>If there's a cue list, it's at the top. <b>GO</b> takes the next
            cue, <b>Back</b> the previous one, and <b>Show all</b> jumps to
            any cue.</>,
          <><b>On now</b> shows what's loaded. Tap a row to jump to its
            tab.</>,
          <>Tap a preset to recall it. To save the current look, tap an empty
            pad, type a name and tap <b>Save</b>.</>,
          <>Tap <b>TAP</b> in time with the music, then <b>Downbeat</b> on the
            one.</>,
          <>In <b>Auto</b>, turn on <b>Move changes</b>. The route now changes
            by itself every few phrases.</>,
        ],
      },
      {
        title: "Good to know",
        notes: [
          <>Cue fades and holds are in beats, so they follow the tempo. A cue
            with a hold moves on by itself; without one, it waits for GO.</>,
          <>Picking a move by hand holds it, so auto mode won't change it.{" "}
            <b>Release hold</b> hands it back.</>,
          <><b>Speed</b> speeds up or slows down the whole show. To change just
            one slot, use <b>Rate</b> on its tab.</>,
          <>In Design, <b>Edit</b> lets you move, swap, delete and tag
            presets.</>,
          <><b>Track</b> and <b>DJ sync</b> only appear when the engine has a
            show folder or a sync port.</>,
          <><b>Panic</b>, at the bottom, forces everything to zero. For a dark
            room mid-set, use Blackout.</>,
        ],
      },
    ],
  },
  {
    id: "color",
    label: "Color",
    title: "Color: picking colours",
    tab: "color",
    lead: <>Pick a colour look, then paint over it by hand if you want to.</>,
    sections: [
      {
        title: "Try it",
        steps: [
          <>Under <b>Colour look</b>, tap a look. With more than one type of
            fixture, the pills filter the list, and each type keeps its own
            colour.</>,
          <>Under <b>Applies to</b>, choose what to colour: everything, a
            group, or one fixture.</>,
          <>Tap a <b>Quick palette</b> swatch, or mix one in the{" "}
            <b>Picker</b> and tap <b>Apply</b>.</>,
          <><b>Clear</b> removes the hand-picked colour.</>,
        ],
      },
      {
        title: "Good to know",
        notes: [
          <>Hand-picked colours stay on top until you clear them, even when
            auto mode changes the look.</>,
          <>Long-press a swatch (right-click with a mouse) to set the palette's
            current colour instead. Looks and auto rotation use that one.</>,
          <>Colour looks marked <b>tune</b> have their own knobs under{" "}
            <b>Tweak</b>, and <b>Modulation</b> can move them in time.</>,
          <>Movers with a colour wheel can't mix, so they snap to the nearest
            colour on the wheel.</>,
          <><b>Rate</b> changes how fast a colour chase steps, without
            touching the movement.</>,
        ],
      },
    ],
  },
  {
    id: "move",
    label: "Move",
    title: "Move: aiming the beams",
    tab: "move",
    lead: <>The plan at the top shows the room from above, with each beam drawn
      where it lands.</>,
    sections: [
      {
        title: "Try it",
        steps: [
          <>Pick a <b>Route</b>. Positions stay still; Moves travel.</>,
          <>Under <b>Shape</b>, drag <b>Size</b> to make the move bigger or
            smaller. At 0, every head points at the ball.</>,
          <>Drag <b>Spread</b> to stagger the heads. At 0 they move
            together.</>,
          <><b>Centre —</b> turns the whole look around the room.{" "}
            <b>Centre |</b> raises or lowers it.</>,
          <><b>Rate</b> speeds up or slows down just the movement. <b>hold</b>{" "}
            freezes it.</>,
          <>Routes marked <b>tune</b> have knobs of their own. Pick one and
            the <b>Tweak</b> card shows them: how big, how fast, how flat.</>,
          <><b>Vary</b> nudges those knobs somewhere nearby. Press it again for
            somewhere else, or <b>Reset</b> to go back.</>,
        ],
      },
      {
        title: "Good to know",
        notes: [
          <>Shape and Rate stay set when the route changes. <b>Reset</b> puts
            them back.</>,
          <><b>Modulation</b> makes a knob move on its own, in time with the
            music: Size breathing, say. Pick the knob, then a wave.{" "}
            <b>Stop all</b> ends it.</>,
          <><b>Layers</b> adds another route on top of this one. Their movements
            add together, so a slow circle plus a small fast wobble is two
            layers.</>,
          <><b>Centre</b> goes as far as your heads can actually reach. If a head
            runs out of travel it stops there, and the Shape card says so.</>,
          <>Some old routes are hidden because a tunable one replaced them. The
            <b>retired</b> button at the bottom of the list brings them back.</>,
          <>There's no Clear here. Without a route, the heads would just stay
            where they are.</>,
          <>A beam ringed in amber is being dimmed by the safety taper, because
            it crosses the crowd at head height.</>,
          <><b>travels dark</b> means the heads switch off while they move
            between positions.</>,
        ],
      },
    ],
  },
  {
    id: "bright",
    label: "Bright",
    title: "Bright: setting levels",
    tab: "bright",
    lead: <>A level pattern, dimmers to trim it, and buttons to flash.</>,
    sections: [
      {
        title: "Try it",
        steps: [
          <>Tap a <b>Bright pattern</b>. Chases move brightness around the rig
            without touching the colour.</>,
          <>Under <b>Dimmers</b>, pull a group down. This trims the pattern
            rather than replacing it.</>,
          <>Hold a <b>Flash</b> button. Let go and it stops.</>,
          <><b>reset</b> or <b>Reset all</b> hands control back to the
            pattern.</>,
        ],
      },
      {
        title: "Good to know",
        notes: [
          <>Brightness is pattern × dimmer × master × safety taper. In Design,
            the last card shows the result for each fixture.</>,
          <>Flash works even on a group dimmed to zero. The safety taper still
            applies.</>,
          <>Strobe isn't limited unless the venue file sets a policy, and it
            can trigger photosensitive epilepsy. <b>Strobe policy</b> shows
            what's set.</>,
          <><b>Rate</b> here only affects level chases. Moves that travel dark
            keep their own timing.</>,
          <>Patterns marked <b>tune</b> have their own knobs under{" "}
            <b>Tweak</b>: how deep, how fast.</>,
        ],
      },
    ],
  },
  {
    id: "setup",
    label: "Setup",
    title: "Setup: the room and the rig",
    tab: "setup",
    lead: <>Load-in jobs, in order: the room, the safety taper, calibration and
      the patch. It's Design-only because it's the one tab that can stop the
      rig working.</>,
    sections: [
      {
        title: "Check for drift",
        steps: [
          <>Clear the room. In <b>Jog</b>, aim every head at the mirror ball,
            one at a time.</>,
          <>In <b>Drift check</b>, tap <b>Check all heads</b>. It stays off
            until every head is jogging.</>,
          <>A head marked <b>MOVED</b> is off by 3° or more: re-aim it, below.
            Then tap <b>Stop all</b>.</>,
        ],
      },
      {
        title: "Re-aim a head",
        steps: [
          <>Clear the room first. Jogging a head turns off its safety
            taper.</>,
          <>In <b>Jog</b>, pick a head and adjust <b>Pan</b> and <b>Tilt</b>{" "}
            until it hits the mirror ball.</>,
          <>In <b>Capture</b>, tap <b>Ball</b>. Then aim at each corner target
            and capture those too.</>,
          <>Tap <b>Solve (preview)</b> and check <b>Notices</b>. A residual of
            a degree or two is good.</>,
          <>Tap <b>Solve &amp; write</b> to save, then <b>Apply now</b> to
            load the new calibration into the running show, then{" "}
            <b>Stop all</b>.</>,
        ],
      },
      {
        title: "The room",
        notes: [
          <>Changes to <b>Safety taper</b>, <b>Crowd zone</b> and{" "}
            <b>Canopy</b> apply straight away, so you can watch the beams
            respond. <b>Save to venue.json</b> keeps them.</>,
          <>A smaller crowd zone means fewer beams get dimmed.</>,
          <>The ball position and mount mode can't be changed here, because
            every head's calibration depends on them.</>,
        ],
      },
      {
        title: "The patch",
        notes: [
          <>Tap <b>Unlock to edit</b> first. Changes save to rig.json but
            don't take effect until you tap <b>Apply now</b>.</>,
          <>Removing a moving head shifts the heads after it, which breaks
            their calibration.</>,
          <>Positions are in millimetres from the room's corner, the same axes
            as the crowd zone. A moved head was calibrated somewhere else, so
            run a drift check once it's applied.</>,
          <><b>Autopatch</b> readdresses every fixture, so you'll have to
            re-dial every unit.</>,
        ],
      },
      {
        title: "When something's wrong",
        notes: [
          <><b>Notices</b> lists what the engine did and what it refused.</>,
          <><b>Not driven</b> lists anything the engine isn't controlling.{" "}
            <b>Engine</b> shows frame rate, dropped frames and errors.</>,
        ],
      },
    ],
  },
  {
    id: "designer",
    label: "Studio",
    title: "Studio",
    lead: <>Studio is where shows are made, on a computer: tracks added from
      rekordbox, timelines drawn bar by bar against them, and the routines they
      play. On the night they follow the DJ live. The engine needs a show
      folder.</>,
    sections: [
      {
        title: "Where it is",
        notes: [
          <>Press <b>Studio</b> in the header (in Design mode), or the link on
            the <b>Track</b> card on Show. It opens in a tab of its own.</>,
          <>Studio is a place, not a mode: <b>Perform</b> and <b>Design</b>{" "}
            only change how much of this console is on screen.</>,
          <>Its <b>Guide</b> button walks you through it.</>,
          <>On the night, arm <b>Follow</b> on the Track card and the playing
            track's timeline drives the rig.</>,
        ],
      },
    ],
  },
];
