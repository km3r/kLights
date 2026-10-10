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
  /** A visuals cue: a scene, and its parameters in `params`. */
  scene?: string;
  [key: string]: unknown;
}

/** A number, or a color for a color parameter's lane: a palette role, a
 *  hex color, `[r, g, b]` from 0 to 1, or a color look's name. */
export type PointValue = number | string | number[];
export type Point = [number, PointValue] | [number, PointValue, string];

/** A musical shape added on top of an automation row's points (`waves.Wave`):
 *  `depth` times the shape over `bars`, or for a color lane a swing `toward`
 *  a color, `depth` (0-1) of the way. */
export interface WaveSpec {
  shape: string;
  bars: number;
  depth?: number;
  phase?: number;
  seed?: number;
  toward?: PointValue;
}

/** One frequency band of the track's own audio added on top of an automation
 *  row's points (`bands.Follow`): `depth` times the band's level, 0-1 of its
 *  loudest in the track. `floor` and `ceiling` pick the part of that range the
 *  lane listens to; `release` is the beats a full level takes to fall away. A
 *  track's timeline only -- a routine plays on any track. */
export interface AudioSpec {
  band: string;
  depth?: number;
  floor?: number;
  ceiling?: number;
  release?: number;
}

export interface Row {
  id: string;
  type: "clips" | "hits" | "automation" | "external";
  target?: string;
  gap?: "fill" | "exclusive";
  role?: string;
  label?: string;
  items?: Item[];
  points?: Point[];
  wave?: WaveSpec;
  audio?: AudioSpec;
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
  /** Where Studio files it. Nothing about how it plays. */
  folder?: string | null;
  /** Its rows in a line each: what each drives, with which blocks. */
  lanes?: { type: string; target?: string | null; role?: string | null;
            blocks: (string | null)[] }[];
  /** Everything that names it (showfiles.routine_usage). */
  used_by?: RoutineUsage;
  rev?: string;
}

export interface RoutineUsage {
  timelines: { track: string; title?: string | null; clips: number; variations: string[] }[];
  templates: { id: string; name?: string | null; where: string[] }[];
  show: string[];
}

/** How many places use a routine: each timeline, set and show setting once. */
export function usageCount(u: RoutineUsage | undefined): number {
  return u ? u.timelines.length + u.templates.length + u.show.length : 0;
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
  album?: string;
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
  /** rekordbox's phrases: [start beat, end beat, label]. */
  phrase_items?: [number, number, string][];
  /** Its timeline in a line: how big, the grid it was drawn on, and its rev
   *  for a delete to quote. */
  timeline?: { rows: number; items: number; grid_rev?: string | null;
               rev?: string | null } | null;
  /** The other descriptions it answers to: a guest's copy, linked by hand. */
  aliases?: { title: string; artist?: string; album?: string }[];
  /** When its show last changed on disk, seconds since the epoch. */
  edited?: number | null;
  /** A file the track names is on this machine (the named paths only). */
  audio_here?: boolean;
  rev?: string;
}

/** `GET /api/show`: show.json and the folder's counts. */
export interface ShowSummary {
  dir: string;
  rev: string;
  /** show.json's own rev, for a save of it to quote. */
  show_rev?: string | null;
  show: { template_set?: string; fallback?: string;
          pause?: { idle_routine?: string }; [key: string]: unknown } | null;
  errors: string[];
  warnings: string[];
  /** Files broken since they last loaded, running on their last good version:
   *  relative path, and why. */
  failed?: Record<string, string>;
  /** The errors and warnings again, a row each with the file it is about. */
  problems?: FolderProblem[];
}

/** One thing wrong in the show folder. `file` is relative to the folder
 *  ("timelines/x.json", "show.json"); null for the few about no file. */
export interface FolderProblem {
  level: "error" | "warning";
  file: string | null;
  text: string;
}

/** One phrase family's pick in a template set. */
export interface TemplatePick {
  routine: string;
  variation?: string;
  params?: Record<string, unknown>;
  /** Which tag (or fixture) each of the routine's roles plays on, where not
   *  its default -- as a clip's `bind`. */
  bind?: Record<string, string>;
  palette?: string;
  /** What the built-in visuals show while the pick plays (milestone 3). */
  visuals?: { scene: string; params?: Record<string, unknown> };
}

/** A template set: rekordbox phrase -> routine, for tracks with no timeline. */
export interface TemplateSetDoc {
  kind?: "klights.template_set";
  id: string;
  name?: string;
  phrases: Record<string, TemplatePick>;
  palettes?: Record<string, Record<string, unknown>>;
  palette?: string;
  bars?: { every: number; cycle: TemplatePick[] };
  transition?: { fade_beats?: number };
  [key: string]: unknown;
}

/** A palette of the show's library: `palettes/<id>.json`. */
export interface PaletteDoc {
  kind: "klights.palette";
  version: 1;
  id: string;
  /** What timelines and sets call it: copies are found by this name. */
  name: string;
  primary: string;
  secondary: string;
  accent: string;
  [key: string]: unknown;
}

/** A timeline or set that carries a palette of a given name, and its colors there. */
export interface PalettePlace {
  file: string;
  kind: "timeline" | "template_set";
  id: string;
  title?: string | null;
  colors: Partial<Record<"primary" | "secondary" | "accent", string | null>>;
}

/** A line of `GET /api/palettes`: a library palette and its copies. */
export interface PaletteSummary {
  id: string;
  name: string;
  primary: string;
  secondary: string;
  accent: string;
  rev?: string;
  copies: (PalettePlace & { same: boolean })[];
}

/** A palette that lives only inside timelines and sets: none in the library has its name. */
export interface FoundPalette { name: string; places: PalettePlace[] }

/** A line of `GET /api/templates`. */
export interface TemplateSummary {
  id: string;
  name?: string | null;
  phrases?: number;
  palettes?: string[];
  /** It is show.json's template set. */
  show?: boolean;
  rev?: string;
}

/** rekordbox's phrase families, in the order a track meets them. */
export const PHRASE_FAMILIES = ["Intro", "Verse", "Up", "Chorus", "Down", "Bridge", "Outro"];

/** The numbered labels rekordbox writes, which a set may pick for exactly. */
export const EXACT_LABELS = [
  ...[1, 2, 3, 4, 5, 6].map((n) => `Verse ${n}`), ...[1, 2, 3].map((n) => `Up ${n}`)];

/** The pick a template set makes for a phrase label: exact, then family,
 *  then `*` -- the engine's lookup order. */
export function pickFor(ts: Pick<TemplateSetDoc, "phrases">, label: string): TemplatePick | undefined {
  return ts.phrases[label] ?? ts.phrases[phraseFamily(label)] ?? ts.phrases["*"];
}

/** rekordbox's phrase colors, by family. */
export const PHRASE_HUE: Record<string, string> = {
  Intro: "#3b82f6", Verse: "#14b8a6", Up: "#f59e0b", Chorus: "#ef4444",
  Down: "#8b5cf6", Bridge: "#ec4899", Outro: "#64748b",
};

/** "Verse 2" is a Verse: the label without its number. */
export function phraseFamily(label: string): string {
  return label.replace(/\s*\d+$/, "");
}

/** An id not yet used by any row or item of a document, from a stem. */
export function uniqueId(doc: { rows: Row[] }, stem: string): string {
  const taken = new Set<string>();
  for (const r of doc.rows) {
    taken.add(r.id);
    for (const i of r.items ?? []) taken.add(i.id);
  }
  const base = stem.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "item";
  let id = base;
  let n = 2;
  while (taken.has(id)) id = `${base}-${n++}`;
  return id;
}

/** How many phrases, from the start, two tracks share: the same family over
 *  the same beats. A timeline copied from one to the other is right that far. */
export function phraseMatch(a: [number, number, string][], b: [number, number, string][]): number {
  let n = 0;
  while (n < a.length && n < b.length && a[n]![0] === b[n]![0] && a[n]![1] === b[n]![1]
         && phraseFamily(a[n]![2]) === phraseFamily(b[n]![2])) n++;
  return n;
}

/** JSON with every object's keys in order, so two values that are the same
 *  compare the same however their keys were written. A key holding nothing
 *  is left out, as JSON.stringify leaves it out. */
function canonicalJson(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map(canonicalJson).join(",")}]`;
  if (v && typeof v === "object") {
    const o = v as Record<string, unknown>;
    return `{${Object.keys(o).filter((k) => o[k] !== undefined).sort()
      .map((k) => `${JSON.stringify(k)}:${canonicalJson(o[k])}`).join(",")}}`;
  }
  return JSON.stringify(v);
}

/**
 * A template set from a track's timeline: for each phrase family, what its
 * scene lane plays most over that family's phrases (routine, variation,
 * parameters and role bindings together), and the palette clip most over them; for anything
 * else, what it plays most overall. The timeline's palettes come with it, and
 * the fade its routine clips most often use becomes the set's fade. Null if
 * the scene lane plays no routine on any phrase.
 */
export function templateFromTimeline(id: string, name: string, track: TrackDoc,
                                     tl: TimelineDoc): TemplateSetDoc | null {
  const phrases = track.phrases?.items ?? [];
  const scene = tl.rows.find((r) => r.type === "clips" && r.target === "scene");
  const palLane = tl.rows.find((r) => r.type === "clips" && r.target === "palette");
  const most = (items: Item[] | undefined, s: number, e: number, ok: (i: Item) => boolean) => {
    let best: Item | null = null;
    let cover = 0;
    for (const it of items ?? []) {
      if (!ok(it)) continue;
      const c = Math.min(e, it.at + it.len) - Math.max(s, it.at);
      if (c > cover) { best = it; cover = c; }
    }
    return { best, cover };
  };
  const tally = new Map<string, Map<string, number>>();
  const add = (fam: string, key: string, beats: number) => {
    const m = tally.get(fam) ?? new Map<string, number>();
    m.set(key, (m.get(key) ?? 0) + beats);
    tally.set(fam, m);
  };
  for (const [s, e, label] of phrases) {
    const { best, cover } = most(scene?.items, s, e, (i) => i.kind === "routine" && !!i.routine);
    if (!best) continue;
    const pal = most(palLane?.items, s, e, (i) => i.kind === "palette" && !!i.palette).best?.palette;
    const pick: TemplatePick = { routine: best.routine! };
    if (best.variation) pick.variation = best.variation;
    if (best.params && Object.keys(best.params).length) pick.params = { ...best.params };
    if (best.bind && Object.keys(best.bind).length) pick.bind = { ...best.bind };
    if (pal && tl.palettes?.[pal]) pick.palette = pal;
    // Counted as written however its parameters' or bindings' keys are ordered.
    const key = canonicalJson(pick);
    add(phraseFamily(label), key, cover);
    add("*", key, cover);
  }
  if (!tally.size) return null;
  const top = (m: Map<string, number>) => [...m].sort((x, y) => y[1] - x[1])[0]![0];
  const picks: Record<string, TemplatePick> = {};
  for (const [fam, m] of tally) picks[fam] = JSON.parse(top(m)) as TemplatePick;
  const doc: TemplateSetDoc = { kind: "klights.template_set", version: 1, id, name, phrases: picks };
  if (tl.palettes && Object.keys(tl.palettes).length) {
    doc.palettes = structuredClone(tl.palettes) as TemplateSetDoc["palettes"];
    if (tl.palette && tl.palettes[tl.palette]) doc.palette = tl.palette;
  }
  const fades = new Map<number, number>();
  for (const it of scene?.items ?? []) {
    if (it.kind === "routine" && (it.fade ?? 0) > 0) fades.set(it.fade!, (fades.get(it.fade!) ?? 0) + 1);
  }
  if (fades.size) doc.transition = { fade_beats: [...fades].sort((x, y) => y[1] - x[1])[0]![0] };
  return doc;
}

/** The engine's rule for an id, which is also the file's name (showfiles.ID_RE). */
export const ID_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/;

/** A new palette's colors, until they are changed. */
export const NEW_COLORS: Readonly<Record<"primary" | "secondary" | "accent", string>> =
  { primary: "#ffffff", secondary: "#888888", accent: "#ff0000" };

/** A palette color as lower-case #rrggbb, compared the way the engine
 *  compares copies (showfiles.hex_color): a hex string, or [r, g, b] of 0..1.
 *  Null for anything else. */
export function hexColor(v: unknown): string | null {
  if (typeof v === "string") return /^#[0-9a-f]{6}$/i.test(v) ? v.toLowerCase() : null;
  if (Array.isArray(v) && v.length === 3
      && v.every((c) => typeof c === "number" && c >= 0 && c <= 1)) {
    return `#${v.map((c: number) => Math.round(c * 255).toString(16).padStart(2, "0")).join("")}`;
  }
  return null;
}

/** A name not taken by any of `names`: "Hot copy", then "Hot copy 2"... */
export function freeName(names: Iterable<string>, stem: string): string {
  const taken = new Set(names);
  let name = stem;
  for (let n = 2; taken.has(name); n++) name = `${stem} ${n}`;
  return name;
}

/** An id not taken by any of `ids`, from a stem: "fan-drop-copy", then
 *  "fan-drop-copy-2"... */
export function freeId(ids: Iterable<string>, stem: string): string {
  const taken = new Set(ids);
  const base = stem.toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 58)
    || "item";
  let id = base;
  for (let n = 2; taken.has(id); n++) id = `${base}-${n}`;
  return id;
}

/** A new timeline for a track: one scene lane, empty, on the track's grid. */
export function newTimeline(track: Pick<TrackDoc, "id" | "grid">): TimelineDoc {
  const doc: TimelineDoc = { kind: "klights.timeline", version: 1, track: track.id, rows: [] };
  if (track.grid?.rev) doc.grid_rev = track.grid.rev;
  doc.rows.push({ id: "scene", type: "clips", target: "scene", gap: "fill", items: [] });
  return doc;
}

function itemFor(d: { rows: Row[] }, pick: TemplatePick, stem: string, at: number,
                 len: number, fade: number): Item {
  const item: Item = { id: uniqueId(d, stem), kind: "routine", routine: pick.routine, at, len };
  if (pick.variation) item.variation = pick.variation;
  if (pick.params) item.params = { ...pick.params };
  if (pick.bind && Object.keys(pick.bind).length) item.bind = { ...pick.bind };
  if (fade > 0) item.fade = Math.min(fade, len);
  return item;
}

/**
 * Draft from template: a template set laid onto a timeline's scene lane, one
 * routine per rekordbox phrase -- the exact label (Verse 2), then the family
 * (Verse), then `*` -- its palettes onto the palette lane, and its picks'
 * visuals onto the visuals lane: live, a timeline with a visuals lane of its
 * own silences the set's, so a draft that left them behind would go dark on
 * the projector. A track with a
 * grid but no phrases gets the set's bar cycle instead, the way the engine
 * plays it live. Changes `d` in place (inside an undoable edit, or on a new
 * document); returns why it could not, or null.
 */
export function draftFromTemplate(d: TimelineDoc, track: TrackDoc,
                                  ts: TemplateSetDoc): string | null {
  const phrases = track.phrases?.items ?? [];
  let spans: [number, number, TemplatePick | undefined, string][] = [];
  if (phrases.length) {
    spans = phrases.map(([start, end, label]) => {
      const family = phraseFamily(label);
      return [start, end, ts.phrases[label] ?? ts.phrases[family] ?? ts.phrases["*"], family];
    });
  } else if (ts.bars?.cycle.length && track.grid?.segments?.length) {
    const grid = new Grid(track.grid.segments);
    const end = track.identity.duration_s ? grid.beatAt(track.identity.duration_s) : 0;
    const step = ts.bars.every * BEATS_PER_BAR;
    for (let at = 0, i = 0; at < end; at += step, i++) {
      spans.push([at, Math.min(end, at + step), ts.bars.cycle[i % ts.bars.cycle.length],
                  `bar-${at / BEATS_PER_BAR + 1}`]);
    }
  }
  if (!spans.length) {
    return phrases.length || !ts.bars ? "this track has no phrases to draft from"
      : "this track has no phrases, and no grid long enough for the bar cycle";
  }
  let lane = d.rows.find((x) => x.type === "clips" && x.target === "scene");
  if (!lane) {
    lane = { id: uniqueId(d, "scene"), type: "clips", target: "scene", gap: "fill", items: [] };
    d.rows.unshift(lane);
  }
  lane.items = [];
  let palLane: Row | null = null;
  if (ts.palettes) {
    palLane = d.rows.find((x) => x.target === "palette") ?? null;
    if (!palLane) {
      palLane = { id: uniqueId(d, "palette"), type: "clips", target: "palette",
                  gap: "exclusive", items: [] };
      d.rows.push(palLane);
    }
    d.palettes = { ...(d.palettes ?? {}), ...(ts.palettes as TimelineDoc["palettes"]) };
    if (ts.palette && !d.palette) d.palette = ts.palette;
    palLane.items = [];
  }
  let visLane: Row | null = null;
  if (spans.some(([, , pick]) => pick?.visuals)) {
    visLane = d.rows.find((x) => x.type === "external" && x.points == null
      && (x.output === "visuals" || x.output === "vj")) ?? null;
    if (!visLane) {
      visLane = { id: uniqueId(d, "visuals"), type: "external", output: "visuals", items: [] };
      d.rows.push(visLane);
    }
    visLane.items = [];
  }
  // The set's change between phrases, as each clip's own fade in: the first
  // clip comes in from nothing with it, and the rest crossfade on it.
  const fade = ts.transition?.fade_beats ?? 0;
  for (const [start, end, pick, stem] of spans) {
    if (!pick) continue;
    lane.items.push(itemFor(d, pick, `${stem}-${start}`, start, end - start, fade));
    if (pick.palette && palLane) {
      (palLane.items ??= []).push({ id: uniqueId(d, `pal-${start}`), kind: "palette",
                                    palette: pick.palette, at: start, len: end - start });
    }
    if (pick.visuals && visLane) {
      (visLane.items ??= []).push({ id: uniqueId(d, `vis-${start}`), at: start, len: end - start,
                                    scene: pick.visuals.scene,
                                    params: structuredClone(pick.visuals.params ?? {}) });
    }
  }
  return null;
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

// -- waves ---------------------------------------------------------------------
//
// The engine's shapes (`engine/waves.py`), copied because the designer draws a
// lane's wave as it is edited. `wave-vectors.json`, written from the engine,
// holds the copy to the original -- `hold` included, whose levels are a 64-bit
// hash and so need BigInt to come out the same.

const MASK = (1n << 64n) - 1n;

/** `waves.sampled`: splitmix64's finalizer, a stable -1..1 per key. */
export function sampled(seed: number, ...key: number[]): number {
  let x = BigInt(seed) & MASK;
  for (const k of key) {
    x = (x * 0x9E3779B97F4A7C15n + (BigInt(k) & MASK) + 0x165667B19E3779F9n) & MASK;
    x ^= x >> 30n;
    x = (x * 0xBF58476D1CE4E5B9n) & MASK;
    x ^= x >> 27n;
    x = (x * 0x94D049BB133111EBn) & MASK;
    x ^= x >> 31n;
  }
  return (Number(x) / Number(MASK)) * 2 - 1;
}

/** A shape at `p` cycles, 0..1. */
export function waveUnit(shape: string, p: number, seed = 0): number {
  const f = p - Math.floor(p);
  switch (shape) {
    case "sine": return 0.5 - 0.5 * Math.cos(2 * Math.PI * f);
    case "triangle": return f < 0.5 ? 2 * f : 2 - 2 * f;
    case "ramp": return f;
    case "saw": return 1 - f;
    case "square": return f < 0.5 ? 0 : 1;
    case "hold": return (sampled(seed, Math.floor(p)) + 1) / 2;
    default: return 0;
  }
}

/** What a wave adds at a beat: depth times its shape. */
export function waveLevel(wave: WaveSpec, beat: number): number {
  const cycles = beat / (wave.bars * BEATS_PER_BAR) + (wave.phase ?? 0);
  return (wave.depth ?? 1) * waveUnit(wave.shape, cycles, wave.seed ?? 0);
}

/** A numeric lane's value at a beat: its points, plus its wave, plus -- given
 *  the track's audio -- the band it follows. */
export function laneValue(row: Row, beat: number, audio?: TrackAudio | null): number | null {
  const base = curveValue(row.points ?? [], beat);
  if (base == null) return base;
  return base + (row.wave ? waveLevel(row.wave, beat) : 0)
    + (row.audio && audio ? audio.level(row.audio, beat) : 0);
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
/** The built-in visuals' scenes and what each reads -- a copy of
 *  `engine/showfiles.VISUAL_PARAMS`: "color", "file", "bool", a list of
 *  choices, or a [min, max] range. Every scene also takes `opacity`. */
export const VISUAL_SCENES = ["wash", "bars", "tunnel", "particles", "strobe", "video"] as const;
export type VisualRule = "color" | "file" | "bool" | string[] | [number, number];
export const VISUAL_PARAMS: Record<string, Record<string, VisualRule>> = {
  wash: { color: "color", pulse: [0, 1] },
  bars: { color: "color", count: [1, 64], speed: [0, 8] },
  tunnel: { color: "color", speed: [0, 8], depth: [2, 40] },
  particles: { color: "color", count: [1, 2000], burst: [0, 1] },
  strobe: { color: "color", rate: [0.25, 16] },
  video: { file: "file", loop: "bool", rate: ["beat", "normal"], bpm: [20, 400] },
};

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
  if (it.scene) return it.scene === "video" && typeof it.params?.file === "string"
    ? `video ${it.params.file}` : it.scene;
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

/** A track's length as a DJ reads it: 6:48. */
export function mmss(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "";
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
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
  /** Per column [r, g, b] 0..1, when the analysis has color. */
  colors?: [number, number, number][];
  /** Columns per second, or null when the columns span the whole track. */
  rate: number | null;
  /** A level per column for each frequency band the analysis has, on whatever
   *  scale its format used: what a lane follows, and the lane's band view.
   *  Absent for the preview alone, which is too coarse for either. */
  bands?: Partial<Record<AudioBand, Uint8Array>>;
  /** The bands are rekordbox's three-band analysis, not read off its colors. */
  exact?: boolean;
}

/** The bands a lane can follow (`bands.BANDS`): three of the spectrum, and the
 *  track's overall level. */
export const AUDIO_BANDS = ["low", "mid", "high", "all"] as const;
export type AudioBand = (typeof AUDIO_BANDS)[number];

function b64(text: string): Uint8Array {
  const raw = atob(text);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

export interface WaveformDoc {
  preview?: string;
  detail?: { format: string; rate?: number; data: string };
  bands?: { format: string; rate?: number; data: string };
}

/** rekordbox's three-band analysis (PWV7): a byte each for low, mid and high
 *  per column, 0-127. `bands.decode`, for the same bytes. */
function threeBands(doc: WaveformDoc): Wave["bands"] | null {
  if (doc.bands?.format !== "pwv7") return null;
  const bytes = b64(doc.bands.data);
  const n = Math.floor(bytes.length / 3);
  if (!n) return null;
  const low = new Uint8Array(n);
  const mid = new Uint8Array(n);
  const high = new Uint8Array(n);
  const all = new Uint8Array(n);
  for (let i = 0; i < n; i++) {
    low[i] = bytes[3 * i]!;
    mid[i] = bytes[3 * i + 1]!;
    high[i] = bytes[3 * i + 2]!;
    all[i] = Math.max(low[i]!, mid[i]!, high[i]!);
  }
  return { low, mid, high, all };
}

/** rekordbox's waveforms, as the prep tool stored them: the 400-column
 *  preview (PWAV), the scrolling detail (PWV3: a byte per column, PWV5: two,
 *  with color) and the three-band detail (PWV7). What is DRAWN is the detail;
 *  the bands are what a lane follows -- the three-band analysis where the
 *  track has it, else the color waveform's colors times its height (red is
 *  low, green mid, blue high), else the height alone. */
export function decodeWave(doc: WaveformDoc): Wave | null {
  const exact = threeBands(doc);
  if (doc.detail) {
    const bytes = b64(doc.detail.data);
    const rate = doc.detail.rate ?? 150;
    if (doc.detail.format === "pwv5") {
      const n = bytes.length >> 1;
      const heights: number[] = [];
      const colors: [number, number, number][] = [];
      const est = { low: new Uint8Array(n), mid: new Uint8Array(n), high: new Uint8Array(n),
                    all: new Uint8Array(n) };
      for (let i = 0; i < n; i++) {
        const v = (bytes[2 * i]! << 8) | bytes[2 * i + 1]!;
        const [r, g, b, h] = [(v >> 13) & 7, (v >> 10) & 7, (v >> 7) & 7, (v >> 2) & 31];
        colors.push([r / 7, g / 7, b / 7]);
        heights.push(h / 31);
        est.low[i] = r * h;
        est.mid[i] = g * h;
        est.high[i] = b * h;
        est.all[i] = h;
      }
      return { heights, colors, rate, bands: exact ?? est, exact: !!exact };
    }
    const heights = Array.from(bytes, (b) => (b & 31) / 31);
    if (doc.detail.format === "pwv3") {
      return { heights, rate, bands: exact ?? { all: Uint8Array.from(bytes, (b) => b & 31) },
               exact: true };
    }
    return { heights, rate, ...(exact ? { bands: exact, exact: true } : {}) };
  }
  if (exact) {
    return { heights: Array.from(exact.all!, (v) => v / 127), rate: doc.bands!.rate ?? 150,
             bands: exact, exact: true };
  }
  if (doc.preview) {
    return { heights: Array.from(b64(doc.preview), (b) => (b & 31) / 31), rate: null };
  }
  return null;
}

// -- a track's audio, on its beats ---------------------------------------------
//
// The engine's `bands.py`, copied because the designer draws a lane that
// follows the audio as it is edited. `audio-vectors.json`, written from the
// engine, holds the copy to the original: the same cells, from the same bytes.

/** Beats to a cell (`bands.STEP`). */
export const AUDIO_STEP = 1 / 32;

/** One track's levels laid on its beats (`bands.Audio`): for each band, a cell
 *  every 1/32 of a beat holding its loudest column, as a fraction of the
 *  band's loudest in the whole track. */
export class TrackAudio {
  readonly bands: AudioBand[];
  readonly exact: boolean;
  /** The cell the audio's first column falls in: negative for a pickup. */
  readonly first: number;
  private readonly edges: number[];
  private readonly cache = new Map<string, Float64Array>();

  /** Null for a waveform with nothing to follow -- the preview alone. */
  static from(wave: Wave | null, grid: Grid): TrackAudio | null {
    return wave?.bands && wave.rate ? new TrackAudio(wave.bands, wave.rate, !!wave.exact, grid)
      : null;
  }

  private constructor(private readonly columns: NonNullable<Wave["bands"]>, rate: number,
                      exact: boolean, grid: Grid) {
    this.bands = AUDIO_BANDS.filter((b) => columns[b]);
    this.exact = exact;
    const n = Math.max(0, ...this.bands.map((b) => columns[b]!.length));
    this.first = Math.floor(grid.beatAt(0) / AUDIO_STEP);
    const last = Math.max(this.first, Math.ceil(grid.beatAt(n / rate) / AUDIO_STEP));
    // Each cell's first column; one more, for where the last cell ends.
    this.edges = [];
    for (let j = 0; j <= last - this.first; j++) {
      this.edges.push(Math.min(n, Math.max(0, Math.trunc(
        grid.timeAt((this.first + j) * AUDIO_STEP) * rate))));
    }
  }

  /** A band on the beats, before any shaping. */
  pooled(band: AudioBand): Float64Array {
    const key = band;
    let got = this.cache.get(key);
    if (!got) {
      const raw = this.columns[band] ?? new Uint8Array(0);
      let peak = 0;
      for (const v of raw) if (v > peak) peak = v;
      got = new Float64Array(Math.max(0, this.edges.length - 1));
      for (let j = 0; j < got.length; j++) {
        const a = this.edges[j]!;
        const b = this.edges[j + 1]!;
        // A cell narrower than a column still has the column it is in.
        let top = b > a ? 0 : a < raw.length ? raw[a]! : 0;
        for (let i = a; i < b; i++) if (raw[i]! > top) top = raw[i]!;
        got[j] = peak ? top / peak : 0;
      }
      this.cache.set(key, got);
    }
    return got;
  }

  /** A band shaped as a lane listens to it: 0-1 per cell (`Audio.envelope`). */
  envelope(band: AudioBand, floor = 0, ceiling = 1, release = 0): Float64Array {
    const key = `${band}/${floor}/${ceiling}/${release}`;
    let got = this.cache.get(key);
    if (!got) {
      const pooled = this.pooled(band);
      const span = ceiling - floor;
      const fall = release > 0 ? AUDIO_STEP / release : Infinity;
      got = new Float64Array(pooled.length);
      let held = 0;
      for (let j = 0; j < pooled.length; j++) {
        held = Math.max(Math.min(1, Math.max(0, (pooled[j]! - floor) / span)), held - fall);
        got[j] = held;
      }
      this.cache.set(key, got);
    }
    return got;
  }

  /** The cells a row's `audio` listens to, or null for a band this track's
   *  analysis does not have (or one that is no band at all). */
  cells(spec: AudioSpec): Float64Array | null {
    const band = spec.band as AudioBand;
    if (!this.bands.includes(band)) return null;
    const floor = spec.floor ?? 0;
    const ceiling = spec.ceiling ?? 1;
    if (!(floor < ceiling)) return null;
    return this.envelope(band, floor, ceiling, spec.release ?? 0);
  }

  /** What a row's `audio` adds at a beat: depth times the band's level. */
  level(spec: AudioSpec, beat: number): number {
    const cells = this.cells(spec);
    const j = Math.floor(beat / AUDIO_STEP) - this.first;
    return cells && j >= 0 && j < cells.length ? (spec.depth ?? 0) * cells[j]! : 0;
  }
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
  /** An absolute angle, really bounded by the playing rig's reach. */
  reach?: string;
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
    choices: p.choices, help: p.help, reach: p.reach,
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
