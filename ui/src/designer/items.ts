import { PALETTE_ROLES } from "../showfiles.generated";
import type { HIT_KINDS } from "../showfiles.generated";
import type { Edits } from "./history";
import { BEATS_PER_BAR } from "./model";
import type { Item } from "./model";

/**
 * New items, and where on a lane they land: snapping, the gap after a clip,
 * and what a new hit, fade or visuals cue starts as.
 */
export const FADES = [0, 1, 2, 4, 8, 16];

export const ROLES = PALETTE_ROLES;

/** The grid line at or before a beat: a click on a lane adds in the cell it
 *  lands in, not at the next line when it lands past the middle of one. */
export function snapDown(history: Pick<Edits, "snap" | "snapBeat">, beat: number): number {
  // Back a step at a time until the nearest line is not past the click: one
  // step for beats and bars, more for phrases, whose lines are far apart.
  const step = history.snap === "beat" ? 1 : BEATS_PER_BAR;
  for (let b = beat; b > -step; b -= step) {
    const snapped = history.snapBeat(b);
    if (snapped <= beat) return Math.max(0, snapped);
  }
  return 0;
}

/** Where something added by a click on a lane's empty space starts: the grid
 *  line at or before the click -- but not inside the item before it, whose
 *  end is where the gap that was clicked begins. */
export function gapStart(history: Pick<Edits, "snap" | "snapBeat">, items: Item[],
                         beat: number): number {
  const before = Math.max(0, ...items.map((i) => i.at + i.len).filter((end) => end <= beat));
  return Math.max(snapDown(history, beat), before);
}

/** The hits, as every place that adds one offers them: one for each kind the
 *  format has (HIT_KINDS). */
export const HITS: readonly { hit: (typeof HIT_KINDS)[number]; label: string; text: string }[] = [
  { hit: "flash", label: "Flash", text: "a burst, decaying" },
  { hit: "strobe", label: "Strobe", text: "for a bar" },
  { hit: "blackout", label: "Blackout", text: "a beat of dark" },
] as const;

export type HitKind = (typeof HITS)[number]["hit"];

/** A new hit's length and envelope: a flash decays over two beats, a strobe
 *  runs a bar, a blackout is one beat. */
export function newHit(hit: HitKind): Pick<Item, "hit" | "len" | "envelope"> {
  return { hit, len: hit === "strobe" ? 4 : hit === "flash" ? 2 : 1,
           ...(hit === "flash" ? { envelope: "decay" as const } : {}) };
}

/** What the built-in visuals show when a scene is first picked: a video
 *  loops, anything else takes the palette's primary. */
export function newVisuals(scene: string = "wash"): { scene: string; params: Record<string, unknown> } {
  return { scene, params: scene === "video" ? { loop: true } : { color: "@primary" } };
}
