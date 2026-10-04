/**
 * The show folder's documents as the designer sees them, and the pure helpers
 * every part of it shares: the beat grid, curves, what drives each lane at a
 * beat, names for items.
 *
 * Mirrors of the engine's own rules (`engine/tracktime.py`,
 * `engine/timeline.py`), kept small and tested against vectors the engine
 * produced -- a clip drawn on bar 41 has to be the clip the engine plays on bar
 * 41. Anything the engine decides that the designer only DISPLAYS (validation,
 * what a routine does to a fixture) is asked of the engine instead.
 */

/** Marks every designer page. It is only in the designer's own chunk: a build
 *  check greps the console's entry script to prove a phone never loads it. */
export const DESIGNER_CHUNK = "klights-designer";

export const BEATS_PER_BAR = 4;
export type Slot = "movement" | "color" | "level";
export const SLOTS: Slot[] = ["movement", "color", "level"];

// -- documents ---------------------------------------------------------------

export interface TrackDoc {
  id: string;
  identity: { title: string; artist?: string; album?: string;
              duration_s?: number; bpm?: number };
  grid: { rev?: string; segments: number[][] };
  phrases?: { mood?: string; items: [number, number, string][] };
  cues?: { beat: number; name?: string; kind?: string; slot?: string }[];
  [key: string]: unknown;
}

/** One OSC message an external item sends (milestone 3). Arguments are
 *  numbers and text, or `$beat`, `$bar`, `$phase`, `$progress`, `$value`. */
export interface OscMessage {
  address: string;
  args?: (number | string)[];
}

export interface Item {
  id: string;
  at: number;
  len: number;
  fade?: number;
  kind?: "routine" | "look" | "snapshot" | "palette";
  routine?: string;
  variation?: string;
  params?: Record<string, unknown>;
  bind?: Record<string, string>;
  look?: string;
  groups?: string[];
  preset?: string;
  palette?: string;
  hit?: "flash" | "strobe" | "blackout";
  role?: string;
  level?: number;
  envelope?: "hold" | "decay";
  block?: string;
  args?: Record<string, unknown>;
  /** An OSC cue: sent when it comes on, when it goes off, and while it plays. */
  on?: OscMessage;
  off?: OscMessage;
  while?: OscMessage;
  /** A MIDI cue: one of a note, a CC or a program change, on a channel 1-16. */
  channel?: number;
  note?: number;
  velocity?: number;
  cc?: number;
  value?: number;
  off_value?: number;
  pc?: number;
  [key: string]: unknown;
}

export type Point = [number, number | string] | [number, number | string, string];

export interface Row {
  id: string;
  type: "clips" | "hits" | "automation" | "external";
  target?: string;
  gap?: "fill" | "exclusive";
  role?: string;
  label?: string;
  items?: Item[];
  points?: Point[];
  /** An external row's output: osc, midi, visuals (`vj` is read as visuals). */
  output?: string;
  /** OSC: where a curve's value goes, and what it sends (default `$value`). */
  address?: string;
  args?: (number | string)[];
  /** MIDI: the lane's channel, and the CC a curve drives. */
  channel?: number;
  cc?: number;
  [key: string]: unknown;
}

export type Palette = Record<"primary" | "secondary" | "accent", unknown>;

export interface TimelineDoc {
  kind: "klights.timeline";
  version: 1;
  track: string;
  grid_rev?: string;
  palettes?: Record<string, Palette>;
  palette?: string;
  rows: Row[];
  [key: string]: unknown;
}

export interface RoutineSummary {
  id: string;
  name?: string;
  bars: number;
  loop: boolean;
  rig?: string | null;
  params: Record<string, { type: string; default?: unknown; min?: number;
                           max?: number; unit?: string }>;
  variations: string[];
  roles: Record<string, { default: string; optional?: boolean }>;
}

export const PARAM_TYPES = ["color", "number", "rate", "look"] as const;

export interface ParamDef {
  type: (typeof PARAM_TYPES)[number];
  default?: unknown;
  min?: number;
  max?: number;
  unit?: string;
}

/** A routine as the routine editor edits it: rows in its own beats, with roles
 *  instead of fixtures and open parameters instead of values. */
export interface RoutineDoc {
  kind: "klights.routine";
  version: 1;
  id: string;
  name?: string;
  bars: number;
  loop?: boolean;
  rig?: string;
  roles: Record<string, { default: string; optional?: boolean }>;
  params?: Record<string, ParamDef>;
  variations?: Record<string, Record<string, unknown>>;
  rows: Row[];
  [key: string]: unknown;
}

export interface TrackLine {
  id: string;
  title: string;
  artist?: string;
  duration_s?: number;
  bpm?: number;
  grid_rev?: string;
  has_timeline: boolean;
  has_waveform: boolean;
  has_audio: boolean;
  phrases: number;
  rev?: string;
}

// -- the grid ----------------------------------------------------------------

/** Beat <-> seconds for one track. A copy of `engine/tracktime.Grid`: linear
 *  between anchors, extrapolated at the stated bpm outside them. */
export class Grid {
  readonly beats: number[];
  readonly times: number[];
  readonly bpms: number[];

  constructor(segments: number[][]) {
    if (!segments.length) throw new Error("a grid needs at least one anchor");
    this.beats = segments.map((s) => s[0]!);
    this.times = segments.map((s) => s[1]! / 1000);
    this.bpms = segments.map((s) => s[2]!);
  }

  private index(values: number[], x: number): number {
    // bisect_right - 1
    let lo = 0;
    let hi = values.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (x < values[mid]!) hi = mid; else lo = mid + 1;
    }
    return lo - 1;
  }

  beatAt(seconds: number): number {
    const { times, beats, bpms } = this;
    const n = times.length;
    if (seconds <= times[0]! || n === 1) {
      return beats[0]! + (seconds - times[0]!) * bpms[0]! / 60;
    }
    if (seconds >= times[n - 1]!) {
      return beats[n - 1]! + (seconds - times[n - 1]!) * bpms[n - 1]! / 60;
    }
    const i = this.index(times, seconds);
    const span = (seconds - times[i]!) / (times[i + 1]! - times[i]!);
    return beats[i]! + span * (beats[i + 1]! - beats[i]!);
  }

  timeAt(beat: number): number {
    const { times, beats, bpms } = this;
    const n = beats.length;
    if (beat <= beats[0]! || n === 1) {
      return times[0]! + (beat - beats[0]!) * 60 / bpms[0]!;
    }
    if (beat >= beats[n - 1]!) {
      return times[n - 1]! + (beat - beats[n - 1]!) * 60 / bpms[n - 1]!;
    }
    const i = this.index(beats, beat);
    const span = (beat - beats[i]!) / (beats[i + 1]! - beats[i]!);
    return times[i]! + span * (times[i + 1]! - times[i]!);
  }

  bpmAt(seconds: number): number {
    const { times, beats, bpms } = this;
    const n = times.length;
    if (n === 1 || seconds < times[0]!) return bpms[0]!;
    if (seconds >= times[n - 1]!) return bpms[n - 1]!;
    const i = this.index(times, seconds);
    return (beats[i + 1]! - beats[i]!) / (times[i + 1]! - times[i]!) * 60;
  }
}

// -- curves --------------------------------------------------------------------

/** A numeric automation curve's value at a beat. The curve named on a point
 *  shapes the segment ARRIVING at it; values hold outside the points. */
export function curveValue(points: Point[], beat: number): number | null {
  const pts = points.filter((p) => typeof p[1] === "number");
  if (!pts.length) return null;
  if (beat <= (pts[0]![0])) return pts[0]![1] as number;
  const last = pts[pts.length - 1]!;
  if (beat >= last[0]) return last[1] as number;
  let i = 1;
  while (i < pts.length && pts[i]![0] <= beat) i++;
  const a = pts[i - 1]!;
  const b = pts[i]!;
  const x = (beat - a[0]) / (b[0] - a[0]);
  const shape = b[2] ?? "linear";
  const t = shape === "step" ? 0 : shape === "ease" ? x * x * (3 - 2 * x) : x;
  return (a[1] as number) + ((b[1] as number) - (a[1] as number)) * t;
}

/** What a row drives: a scene lane drives every slot. */
export function rowChannels(row: Row): string[] {
  if (row.type !== "clips" || !row.target) return [];
  return row.target === "scene" ? [...SLOTS] : [row.target];
}

/** The item a clips row shows at a beat: of those covering it, the one that
 *  started earliest. */
export function itemAt(row: Row, beat: number): Item | null {
  let best: Item | null = null;
  for (const it of row.items ?? []) {
    if (it.at <= beat && beat < it.at + it.len && (!best || it.at < best.at)) best = it;
  }
  return best;
}

export interface Driver {
  lane: string;
  row: string | null;
  item: Item | null;
  /** "clip", "blank" (an owning lane's gap) or "template" (nothing here). */
  source: "clip" | "blank" | "template";
}

/** Who drives each lane at a beat -- the "who drives" panel. The higher lane
 *  wins; a fill gap lets the next lane down show; an owning lane's gap is
 *  blank. Fades are the engine's business and not shown here. */
export function whoDrives(doc: TimelineDoc, beat: number): Driver[] {
  const out: Driver[] = [];
  for (const lane of [...SLOTS, "palette"]) {
    let found: Driver = { lane, row: null, item: null, source: "template" };
    for (const row of doc.rows) {
      if (!rowChannels(row).includes(lane)) continue;
      const it = itemAt(row, beat);
      if (it) { found = { lane, row: row.id, item: it, source: "clip" }; break; }
      if (row.gap === "exclusive") {
        found = { lane, row: row.id, item: null, source: "blank" };
        break;
      }
    }
    out.push(found);
  }
  return out;
}

/** An item by id, and the row it is on. */
export function findItem(doc: { rows: Row[] }, id: string | null): { row: Row; item: Item } | null {
  if (!id) return null;
  for (const row of doc.rows) {
    const item = (row.items ?? []).find((i) => i.id === id);
    if (item) return { row, item };
  }
  return null;
}

/** A clip's name on a lane: what it IS, in a word or two. */
/** The end of an OSC address, which is the part that says what it does:
 *  `/composition/layers/1/clips/3/connect` is `clips/3/connect`. */
export function shortAddress(address: string): string {
  const parts = address.split("/").filter(Boolean);
  return parts.length > 3 ? parts.slice(-3).join("/") : address;
}

/** OSC arguments as typed in a text field: comma separated, numbers as
 *  numbers, anything else as text. */
export function parseOscArgs(text: string): (number | string)[] {
  return text.split(",").map((a) => a.trim()).filter((a) => a !== "")
    .map((a) => (/^-?\d+(\.\d+)?$/.test(a) ? Number(a) : a));
}

export function oscArgsText(args?: (number | string)[]): string {
  return (args ?? []).map(String).join(", ");
}

export function itemName(it: Item): string {
  if (it.hit) return it.hit;
  const osc = it.on ?? it.while ?? it.off;
  if (osc?.address) return shortAddress(osc.address);
  if (it.note != null) return `note ${it.note}`;
  if (it.cc != null) return `cc ${it.cc}`;
  if (it.pc != null) return `program ${it.pc}`;
  if (it.kind === "routine") return it.routine ?? "routine";
  if (it.kind === "look") return it.look ?? "look";
  if (it.kind === "snapshot") return it.preset ?? "snapshot";
  if (it.kind === "palette") return it.palette ?? "palette";
  if (it.block) return it.block;
  return it.id;
}

/** The line under a clip's name: variation and parameter values. */
export function itemSub(it: Item): string {
  const parts: string[] = [];
  if (it.on?.args?.length) parts.push(oscArgsText(it.on.args));
  if (it.while) parts.push("while");
  if (it.off) parts.push("off");
  if (it.note != null) parts.push(`vel ${it.velocity ?? 100}`);
  if (it.cc != null) {
    parts.push(`→ ${it.value ?? 127}${it.off_value != null ? `, then ${it.off_value}` : ""}`);
  }
  if (it.channel != null) parts.push(`ch ${it.channel}`);
  if (it.variation) parts.push(it.variation);
  for (const [k, v] of Object.entries(it.params ?? {})) parts.push(`${k} ${String(v)}`);
  for (const [k, v] of Object.entries(it.args ?? {})) {
    parts.push(`${k} ${Array.isArray(v) ? v.map((c) => (Array.isArray(c) ? "·" : String(c))).join(" ") : String(v)}`);
  }
  if (it.fade) parts.push(`fade ${it.fade}`);
  return parts.join(" · ");
}

/** Bar and beat for display: bar 41.1 is beat 160. */
export function barBeat(beat: number): string {
  const bar = Math.floor(beat / BEATS_PER_BAR) + 1;
  const inBar = Math.floor(((beat % BEATS_PER_BAR) + BEATS_PER_BAR) % BEATS_PER_BAR) + 1;
  return `${bar}.${inBar}`;
}

export function clock(seconds: number): string {
  const s = Math.max(0, seconds);
  const m = Math.floor(s / 60);
  return `${m}:${(s - m * 60).toFixed(1).padStart(4, "0")}`;
}

// -- waveforms ---------------------------------------------------------------

export interface Wave {
  /** Column heights 0..1. */
  heights: number[];
  /** Per column [r, g, b] 0..1, when the analysis has colour. */
  colors?: [number, number, number][];
  /** Columns per second, or null when the columns span the whole track. */
  rate: number | null;
}

function b64(text: string): Uint8Array {
  const raw = atob(text);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

/** rekordbox's waveforms, as the prep tool stored them: the 400-column
 *  preview (PWAV) or the scrolling detail (PWV3: a byte per column, PWV5: two,
 *  with colour). */
export function decodeWave(doc: { preview?: string;
                                   detail?: { format: string; rate?: number; data: string } }
                           ): Wave | null {
  if (doc.detail) {
    const bytes = b64(doc.detail.data);
    const rate = doc.detail.rate ?? 150;
    if (doc.detail.format === "pwv5") {
      const heights: number[] = [];
      const colors: [number, number, number][] = [];
      for (let i = 0; i + 1 < bytes.length; i += 2) {
        const v = (bytes[i]! << 8) | bytes[i + 1]!;
        colors.push([((v >> 13) & 7) / 7, ((v >> 10) & 7) / 7, ((v >> 7) & 7) / 7]);
        heights.push(((v >> 2) & 31) / 31);
      }
      return { heights, colors, rate };
    }
    return { heights: Array.from(bytes, (b) => (b & 31) / 31), rate };
  }
  if (doc.preview) {
    return { heights: Array.from(b64(doc.preview), (b) => (b & 31) / 31), rate: null };
  }
  return null;
}

// -- blocks --------------------------------------------------------------------

/** What one argument of a block is, for the routine editor's fields. */
export interface ArgSpec {
  name: string;
  kind: "number" | "color" | "colors" | "order" | "points" | "bool" | "easing"
      | "look" | "preset";
  /** What the engine uses when the argument is left out. */
  default?: unknown;
  step?: number;
  unit?: string;
  help?: string;
}

const n = (name: string, def: number, step = 1, unit?: string, help?: string): ArgSpec =>
  ({ name, kind: "number", default: def, step, unit, help });

/** The blocks `engine/blocks.py` builds, in the order the editor offers them,
 *  with the arguments each reads and the defaults it uses. Held to the engine's
 *  own lists by a test (`__fixtures__/blocks.json`). */
export const BLOCK_ARGS: Record<string, ArgSpec[]> = {
  orbit: [n("radius", 20, 1, "°"), n("bars", 8, 0.25), n("elongation", 1, 0.1),
          n("spread", 1, 0.05, "", "phase offset across the heads, in cycles")],
  pendulum: [n("width", 30, 1, "°"), n("bars", 4, 0.25),
             { name: "vertical", kind: "bool", default: false }, n("spread", 0, 0.05)],
  fan_sweep: [n("width", 40, 1, "°"), n("bars", 4, 0.25),
              { name: "sweep", kind: "number", step: 1, unit: "°",
                help: "how far the fan swings; half the width when left out" },
              n("spread", 0, 0.05), n("rate", 1, 0.25)],
  aim_points: [{ name: "points", kind: "points", default: [[0.5, 0.5, 0]],
                 help: "room fractions, 0..1 on each axis, so it works in any room" },
               n("bars", 8, 0.25), { name: "easing", kind: "easing", default: "ease_in_out" }],
  solid: [{ name: "color", kind: "color", default: "@primary" }],
  color_chase: [{ name: "colors", kind: "colors", default: ["@primary", "@secondary"] },
                n("bars", 2, 0.25), n("spread", 0, 0.05),
                n("fade", 0, 0.05, "", "how much of each step blends into the next")],
  chase: [{ name: "order", kind: "order", default: "index" }, n("bars", 1, 0.25),
          n("width", 0.5, 0.05)],
  pulse: [n("depth", 1, 0.05), n("bars", 1, 0.25), n("spread", 0, 0.05)],
  dim: [n("level", 1, 0.05)],
  strobe: [n("level", 0.5, 0.05)],
  look: [{ name: "look", kind: "look", help: "a look from THIS rig's library" }],
  snapshot: [{ name: "preset", kind: "preset", help: "a preset from THIS event" }],
};

/** The slot each block drives; null for the rig-bound adapters, which drive
 *  whichever slot their lane is. */
export const BLOCK_SLOT: Record<string, Slot | null> = {
  orbit: "movement", pendulum: "movement", fan_sweep: "movement", aim_points: "movement",
  solid: "color", color_chase: "color",
  chase: "level", pulse: "level", dim: "level", strobe: "level",
  look: null, snapshot: null,
};

export const CHASE_ORDERS = ["x", "-x", "y", "-y", "z", "-z", "index"];
export const EASINGS = ["linear", "ease_in_out", "ease_out"];
export const RIG_BOUND = ["look", "snapshot"];

/** The blocks a lane of this slot can hold. */
export function blocksFor(slot: string): string[] {
  return Object.keys(BLOCK_ARGS).filter((b) => BLOCK_SLOT[b] === slot || BLOCK_SLOT[b] === null);
}
