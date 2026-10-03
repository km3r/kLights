import { useState } from "react";
import { GuideBody } from "../Guide";
import type { Guide } from "../Guide";
import { typing, useKeys } from "./edit";

/**
 * The designer's own guides, in its own chunk: they are about a desk surface
 * and a phone never needs them, any more than it downloads the designer.
 *
 * A column, not a page and not an overlay. The designer is learned by doing,
 * and a guide that replaced the timeline would mean reading a step, closing it,
 * doing the step and opening it again. An overlay was tried first: on a laptop
 * it covered Save and Drive the rig, the two buttons its own steps name. So it
 * opens as a third column and the lanes give up the width.
 */

export type DesignerGuideId = "designer" | "routines";

export const DESIGNER_GUIDES: (Guide & { id: DesignerGuideId })[] = [
  {
    id: "designer",
    label: "Track",
    title: "Design a track's show",
    lead: <>Build a track's show as lanes against the music. Phrases and the
      waveform run along the top, your lanes sit below, and the rig preview is
      on the right.</>,
    sections: [
      {
        title: "A first pass",
        steps: [
          <>Pick a track. Tracks are added with{" "}
            <code>bridges/rekordbox/prep.py</code>.</>,
          <>Under <b>Draft from template</b>, pick a set. It fills the scene
            lane with one routine per phrase.</>,
          <>Press <b>Play</b> or Space. If there's no audio, open the file from
            this computer. It isn't uploaded.</>,
          <>Drag a clip to move it, or drag its right edge to resize it. Clips
            snap to the <b>Snap</b> setting. Click one to edit it in the panel
            below.</>,
          <><b>Drive the rig</b> plays this page on the real lights. Every
            console shows a banner while it's on. <b>Release the rig</b> when
            you're done.</>,
          <><b>Save</b> (Ctrl+S). The button next to it says whether the draft
            is valid. Errors block saving.</>,
        ],
      },
      {
        title: "Adding to it",
        notes: [
          <>Click a routine under <b>Routines</b> to add it at the
            playhead.</>,
          <>Arm <b>Record</b>, play, and tap <b>Flash</b>, <b>Strobe</b>,{" "}
            <b>Blackout</b> or <b>Next scene</b> in time with the music.</>,
          <><b>+ lane</b> adds a lane. <b>+ automation</b> adds a curve for
            master, size, spread, centre or rate.</>,
          <>On a curve, click to add a point and drag to move it. Select a
            point to choose how it arrives: linear, step or ease.</>,
          <><b>List</b> shows every item in order, with nudge buttons for exact
            timing.</>,
        ],
      },
      {
        title: "How lanes combine",
        notes: [
          <>Higher lanes win. A movement lane above the scene lane only
            overrides its movement. Use <b>↑</b> and <b>↓</b> to reorder.</>,
          <><b>fills gaps</b>: where the lane is empty, the lanes below and the
            template show through. <b>owns track</b>: where it's empty,
            nothing drives that slot.</>,
          <><b>Fade in</b> is in beats, and <b>cut</b> means no fade. A clip
            that ends in a gap fades out the same way.</>,
          <><b>At the playhead</b> shows which lane is in control right
            now.</>,
        ],
      },
      {
        title: "Good to know",
        notes: [
          <>Undo and Redo cover every edit. Delete removes the selection and
            Escape deselects it.</>,
          <>Unsaved work is kept in this browser, so a crashed tab doesn't lose
            it.</>,
          <>If someone else saved the file after you opened it, your save is
            refused rather than overwriting theirs.</>,
          <><b>this rig only</b> marks anything that uses this event's own
            looks or presets.</>,
          <>Press <b>?</b> to open or close this guide.</>,
        ],
      },
    ],
  },
  {
    id: "routines",
    label: "Routine",
    title: "Build a routine",
    lead: <>A routine is a reusable chunk of a show: a few bars written for roles
      like movers or pinspots, not for specific fixtures, so it works on any
      rig.</>,
    sections: [
      {
        title: "A first routine",
        steps: [
          <>On the designer's front page, type an id and press{" "}
            <b>New routine</b>. Or click <b>Open routine</b> on a clip.</>,
          <>Set the number of <b>Bars</b>, and whether it <b>loops</b>.</>,
          <>Click a block under <b>Blocks</b> to add it at the playhead. Its
            settings appear below.</>,
          <><b>Play</b> loops it at the <b>Tempo</b> you set here. To see it on
            the rig, put it on a track and drive the rig from there.</>,
          <><b>Save</b> (Ctrl+S).</>,
        ],
      },
      {
        title: "Making it reusable",
        notes: [
          <>Each <b>role</b> maps to a rig tag, and a clip can remap it.{" "}
            <b>optional</b> means it's fine if no fixture has the tag.</>,
          <>Add an <b>open parameter</b> and set a block's value to{" "}
            <code>$name</code>. Each clip, or a <b>variation</b>, can then
            choose the value.</>,
          <>A colour can be a palette role, a fixed colour or a parameter. Use
            roles and the routine follows the track's palette.</>,
          <>Blocks under <b>This rig only</b> use this event's own looks or
            presets, so the routine only works here.</>,
          <>The check button next to Save tells you what won't work on this
            rig.</>,
        ],
      },
    ],
  },
];

const SEEN_KEY = "klights.guide.designer";

function readSeen(): boolean {
  try { return localStorage.getItem(SEEN_KEY) === "1"; } catch { return false; }
}

/**
 * The Guide button, the drawer it opens, and the once-per-browser offer of it.
 * Each designer page places the three where its own layout wants them.
 */
export function useDesignerGuide(page: DesignerGuideId) {
  const [open, setOpen] = useState<DesignerGuideId | null>(null);
  const [seen, setSeen] = useState(readSeen);
  const dismiss = () => {
    try { localStorage.setItem(SEEN_KEY, "1"); } catch { /* offered again next time */ }
    setSeen(true);
  };
  // Opening it answers the offer, the same as dismissing it.
  const show = (id: DesignerGuideId) => {
    setOpen(id);
    if (!seen) dismiss();
  };

  useKeys((e) => {
    if (typing(e) || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === "?") {
      e.preventDefault();
      if (open) setOpen(null); else show(page);
    } else if (e.key === "Escape" && open) {
      setOpen(null);
    }
  });

  const guide = DESIGNER_GUIDES.find((g) => g.id === open);
  return {
    open: guide != null,
    button: (
      <button className={open ? "on" : ""} aria-pressed={open != null}
              title="How the designer works (?)"
              onClick={() => (open ? setOpen(null) : show(page))}>
        Guide
      </button>
    ),
    banner: !seen && !open ? (
      <div className="d-banner d-info" role="note">
        New to the designer? The guide walks you through building a show and a
        routine.
        <button className="d-primary" onClick={() => show(page)}>Open the guide</button>
        <button onClick={dismiss}>Not now</button>
      </div>
    ) : null,
    drawer: guide ? (
      <aside className="d-guide" aria-label="guide">
        <div className="d-guide-head">
          <b>Guide</b>
          <span className="d-chips">
            {DESIGNER_GUIDES.map((g) => (
              <button key={g.id} className={g.id === guide.id ? "on" : ""}
                      aria-pressed={g.id === guide.id}
                      onClick={() => setOpen(g.id)}>{g.label}</button>
            ))}
          </span>
          <span className="grow" />
          <button aria-label="close the guide" onClick={() => setOpen(null)}>×</button>
        </div>
        <GuideBody guide={guide} />
      </aside>
    ) : null,
  };
}
