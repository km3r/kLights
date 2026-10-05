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

import { BLOCK_PARAMS, BLOCK_SLOTS } from "../blocks";
import type { ParamSpec } from "../types";

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
  output?: string;
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
  /** The rekordbox rows this track is, each in its own database. */
  rekordbox?: { db: string; id: number }[];
  /** How many beat-link signatures it answers to: CDJs playing a USB stick. */
  signatures?: number;
  rev?: string;
}

/** `GET /api/rekordbox`: the DJ's collection, as the prep bridge read it. */
export interface Catalogue {
  kind: "klights.rekordbox_catalogue";
  /** What the ids belong to, as prepped tracks record it. */
  db: string;
  path: string;
  rekordbox: string | null;
  read_at: string;
  playlists: CataloguePlaylist[];
  tracks: CatalogueTrack[];
}

export interface CataloguePlaylist {
  id: string;
  name: string;
  parent: string | null;
  kind: "playlist" | "folder" | "smart";
  tracks: number[];
}

export interface CatalogueTrack {
  id: number;
  title: string;
  artist: string;
  album: string;
  genre: string;
  key: string;
  bpm: number | null;
  duration_s: number | null;
  /** A file on the rekordbox machine; false for a streaming service's track. */
  local: boolean;
  analysed: boolean;
  added: string;
}

/** The bridge's answer to `rekordbox_prep`. */
export interface PrepSummary {
  results: { status: string; track_id: string; rekordbox_ids: number[]; title: string;
             artist: string; signature: boolean; notes: string[] }[];
  skipped: { rekordbox_id: number; title: string; artist: string; reason: string }[];
}

/** A form of a name for comparing, the way engine/tracks.normalize compares:
 *  case, accents, "&" and punctuation forgiven. */
export function normalizeName(text: string): string {
  return text.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase()
    .replace(/&/g, " and ").replace(/\b(?:featuring|feat|ft)\b\.?/g, " feat ")
    .replace(/[^\p{L}\p{N}]+/gu, " ").trim();
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
export function itemName(it: Item): string {
  if (it.hit) return it.hit;
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
  label: string;
  kind: "number" | "color" | "colors" | "choice" | "points" | "bool"
      | "look" | "preset";
  /** What the engine uses when the argument is left out. Undefined where it
   *  has no fixed default, so the field says "auto". */
  default?: unknown;
  step?: number;
  min?: number;
  max?: number;
  unit?: string;
  choices?: string[];
  help?: string;
}

/** One engine declaration as an editor field. Integers are numbers to a field;
 *  a unit that only repeats the argument's name ("bars (bars)") is dropped. */
function argSpec(p: ParamSpec): ArgSpec {
  const unit = p.unit?.trim();
  return {
    name: p.name, label: p.label,
    kind: p.kind === "integer" ? "number" : p.kind,
    default: p.default ?? undefined,
    step: p.step ?? (p.kind === "integer" ? 1 : undefined),
    min: p.min, max: p.max,
    unit: unit && unit !== p.name ? unit : undefined,
    choices: p.choices, help: p.help,
  };
}

/** The blocks `engine/blocks.py` builds, with the arguments each reads and the
 *  defaults it uses -- DERIVED from the engine's own declarations
 *  (`blocks.PARAMS`, generated into `blocks.generated.json`).
 *
 *  This used to be a hand-typed table, held to the engine only on argument
 *  NAMES; the defaults, steps and units were copies that could drift. Now there
 *  is one declaration, and a block added to the engine appears here with its
 *  controls and no editor change. */
export const BLOCK_ARGS: Record<string, ArgSpec[]> = Object.fromEntries(
  Object.entries(BLOCK_PARAMS).map(([block, params]) => [block, params.map(argSpec)]));

/** The slot each block drives; null for the rig-bound adapters, which drive
 *  whichever slot their lane is. */
export const BLOCK_SLOT: Record<string, Slot | null> = BLOCK_SLOTS;

const choicesOf = (block: string, arg: string): string[] =>
  BLOCK_PARAMS[block]?.find((p) => p.name === arg)?.choices ?? [];

export const CHASE_ORDERS = choicesOf("chase", "order");
export const EASINGS = choicesOf("aim_points", "easing");
export const RIG_BOUND = ["look", "snapshot"];

/** The blocks a lane of this slot can hold. */
export function blocksFor(slot: string): string[] {
  return Object.keys(BLOCK_ARGS).filter((b) => BLOCK_SLOT[b] === slot || BLOCK_SLOT[b] === null);
}
