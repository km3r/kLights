import {
  Fragment, createContext, useCallback, useContext, useEffect, useRef, useState,
  useSyncExternalStore,
} from "react";
import type { EngineState, FixtureState, LookInfo, Reply } from "../types";
import { apiFetch } from "../useEngine";
import type { Engine } from "./Designer";
import { WAVE_SHAPES } from "../blocks";
import { SideSection } from "./detail";
import {
  BEATS_PER_BAR, BLOCK_ARGS, NEW_COLOURS, VISUAL_PARAMS, VISUAL_SCENES, barBeat, curveValue,
  draftFromTemplate, hexColor, itemName, itemSub, laneValue, normalizeName, oscArgsText,
  parseOscArgs, uniqueId, waveLevel,
} from "./model";
import type {
  Item, OscMessage, PaletteSummary, Point, PointValue, RoutineSummary, Row, TemplateSetDoc,
  TimelineDoc, TrackDoc, VisualRule, WaveSpec,
} from "./model";

/**
 * Editing a timeline: an undo/redo history over the whole document, and the
 * pieces of the designer that change it.
 *
 * Every edit is a pure function of the document -- copy, change, push -- so
 * undo is a pointer move and nothing can half-apply. The engine stays the
 * judge of what is valid: every change is sent as a `timeline_draft` (debounced)
 * and the answer -- errors, warnings, what will not work on this rig -- is shown
 * beside Save. Save writes with the rev the document was read at, so a change
 * made elsewhere (another machine, MCP) is refused rather than overwritten.
 * The working copy is also kept in this browser, so a crash or a closed tab
 * does not lose an evening's work.
 */

export type Snap = "beat" | "bar" | "phrase";

export const AUTOMATION_RANGES: Record<string, [number, number]> = {
  master: [0, 1], size: [0, 3], spread: [-1, 1],
  "center.bearing": [-180, 180], "center.elevation": [-90, 90],
  "rate.movement": [0, 8], "rate.color": [0, 8], "rate.level": [0, 8],
};

// -- what an automation lane drives -----------------------------------------------

/**
 * One automation target, as a lane draws and edits it. The macros come from
 * AUTOMATION_RANGES; `param.<name>` from the routine that declares the
 * parameter -- each routine has its own range, so there is no table for them.
 *
 * `min`/`max` are what the engine ACCEPTS: the declaration, held to it by
 * `showfiles`, undefined where the routine left a side open. `lo`/`hi` are only
 * what the lane draws: the declaration where there is one, else the declared
 * range of the block argument the parameter feeds (`blocks.PARAMS`), so an
 * open-ended radius is still drawn on a sensible scale.
 */
export interface LaneSpec {
  label: string;
  unit: string;
  kind: "number" | "color";
  lo: number;
  hi: number;
  min?: number;
  max?: number;
  /** A new lane's first point: the macro's neutral, the parameter's default. */
  start: PointValue;
  /** In a track's timeline: the routines placed on it that the lane reaches. */
  reaches?: string[];
}

export type LaneSpecs = Record<string, LaneSpec>;

/** The two target prefixes besides the macros, as `showfiles` spells them:
 *  `param.<name>` and a routine's `arg.<item>.<argument>`. */
export const PARAM_TARGET = "param.";
export const ARG_TARGET = "arg.";

const NEUTRAL_ONE = new Set(["master", "size", "rate.movement", "rate.color", "rate.level"]);

function macroSpec(target: string): LaneSpec {
  const [lo, hi] = AUTOMATION_RANGES[target]!;
  const neutral = NEUTRAL_ONE.has(target) ? 1 : 0;
  return { label: target === "master" ? "Master" : target, unit: "", kind: "number",
           lo, hi, min: lo, max: hi, start: Math.min(hi, Math.max(lo, neutral)) };
}

/** A parameter declaration, as a routine file or the routine list carries it. */
interface ParamLike { type: string; default?: unknown; min?: number; max?: number; unit?: string }

/** A lane for one declared parameter; null for a look, which the engine cannot
 *  automate (it is chosen once, when the routine is built). */
export function paramSpec(name: string, def: ParamLike, argRange?: [number, number],
                          reaches?: string[]): LaneSpec | null {
  if (def.type === "look") return null;
  const unit = def.unit ?? "";
  if (def.type === "color") {
    return { label: name, unit, kind: "color", lo: 0, hi: 1,
             start: typeof def.default === "string" || Array.isArray(def.default)
               ? def.default as PointValue : "@primary", reaches };
  }
  // A rate is held to 0-8 whether or not it says so (`_param_value_problem`).
  const rate = def.type === "rate";
  const min = def.min ?? (rate ? 0 : undefined);
  const max = def.max ?? (rate ? 8 : undefined);
  const dflt = typeof def.default === "number" ? def.default : undefined;
  const lo = min ?? argRange?.[0] ?? Math.min(0, dflt ?? 0);
  let hi = max ?? argRange?.[1] ?? Math.max(1, 2 * Math.abs(dflt ?? 0));
  if (hi <= lo) hi = lo + 1;
  return { label: name, unit, kind: "number", lo, hi, min, max,
           start: Math.min(hi, Math.max(lo, dflt ?? lo)), reaches };
}

/** The parameters a routine can automate on its own lanes, each ranged by its
 *  declaration and, where that is open, by the block arguments it feeds. */
export function routineLaneSpecs(doc: { params?: Record<string, ParamLike>; rows: Row[] }): LaneSpecs {
  const fed: Record<string, [number, number]> = {};
  for (const row of doc.rows) {
    for (const it of row.items ?? []) {
      for (const [arg, value] of Object.entries(it.args ?? {})) {
        if (typeof value !== "string" || !value.startsWith("$")) continue;
        const spec = BLOCK_ARGS[it.block ?? ""]?.find((s) => s.name === arg);
        if (spec?.min === undefined || spec.max === undefined) continue;
        const name = value.slice(1);
        const had = fed[name];
        fed[name] = had ? [Math.min(had[0], spec.min), Math.max(had[1], spec.max)]
          : [spec.min, spec.max];
      }
    }
  }
  const out: LaneSpecs = {};
  for (const [name, def] of Object.entries(doc.params ?? {})) {
    const spec = paramSpec(name, def, fed[name]);
    if (spec) out[PARAM_TARGET + name] = spec;
  }
  Object.assign(out, argLaneSpecs(doc.rows));
  return out;
}

/** The kinds of block argument a lane can move (`showfiles.LANE_ARG_KINDS`):
 *  read per frame, with a halfway between two values. */
const LANE_ARG_KINDS = new Set(["number", "color"]);

/**
 * `arg.<item>.<argument>`: one item's argument, moved without declaring a
 * parameter. Offered for each number and colour argument of each block item,
 * except one already fed by a `$param` -- that parameter's lane is the way to
 * move it. Ranged by the block's own declaration; an absolute angle's range is
 * only the fallback for a rig the editor does not know, so it is drawn but not
 * enforced (the engine only warns past it).
 */
function argLaneSpecs(rows: Row[]): LaneSpecs {
  const out: LaneSpecs = {};
  for (const row of rows) {
    if (row.type !== "clips") continue;
    for (const it of row.items ?? []) {
      for (const spec of BLOCK_ARGS[it.block ?? ""] ?? []) {
        if (!LANE_ARG_KINDS.has(spec.kind)) continue;
        const literal = it.args?.[spec.name];
        if (typeof literal === "string" && literal.startsWith("$")) continue;
        const label = `${it.id}.${spec.name}`;
        const unit = spec.unit?.trim() ?? "";
        if (spec.kind === "color") {
          out[ARG_TARGET + label] = { label, unit, kind: "color", lo: 0, hi: 1,
                                  start: (literal ?? spec.default ?? "@primary") as PointValue };
          continue;
        }
        const lo = spec.min ?? 0;
        const hi = spec.max !== undefined && spec.max > lo ? spec.max : lo + 1;
        const value = typeof literal === "number" ? literal
          : typeof spec.default === "number" ? spec.default : lo;
        out[ARG_TARGET + label] = {
          label, unit, kind: "number", lo, hi,
          min: spec.reach ? undefined : spec.min, max: spec.reach ? undefined : spec.max,
          start: value };
      }
    }
  }
  return out;
}

/**
 * The parameters a TRACK's timeline can automate: those of the routines placed
 * on it, by name. A timeline's `param.radius` drives `$radius` on every routine
 * clip that has one, so one lane per name -- held to the narrowest of their
 * ranges, since the engine checks each point against every one of them.
 */
export function timelineLaneSpecs(doc: { rows: Row[] },
                                  routines: { id: string; params: Record<string, ParamLike> }[]): LaneSpecs {
  const placed = new Set(doc.rows.flatMap((r) => (r.items ?? [])
    .filter((i) => i.kind === "routine" && typeof i.routine === "string")
    .map((i) => i.routine as string)));
  const byName: Record<string, { def: ParamLike; from: string[] }> = {};
  for (const r of [...routines].sort((a, b) => a.id.localeCompare(b.id))) {
    if (!placed.has(r.id)) continue;
    for (const [name, def] of Object.entries(r.params ?? {})) {
      const had = byName[name];
      if (!had) { byName[name] = { def: { ...def }, from: [r.id] }; continue; }
      // A name that is a colour in one routine and a number in another: the
      // first routine decides what the lane is, and the engine's check warns
      // about the rest.
      if (had.def.type === "color" ? def.type !== "color" : def.type === "color") continue;
      had.from.push(r.id);
      if (def.min !== undefined) had.def.min = Math.max(had.def.min ?? def.min, def.min);
      if (def.max !== undefined) had.def.max = Math.min(had.def.max ?? def.max, def.max);
      had.def.unit ??= def.unit;
    }
  }
  const out: LaneSpecs = {};
  for (const [name, { def, from }] of Object.entries(byName)) {
    const spec = paramSpec(name, def, undefined, from);
    if (spec) out[PARAM_TARGET + name] = spec;
  }
  return out;
}

/** The parameter lanes the page being edited offers, keyed by target. */
export const ParamLanes = createContext<LaneSpecs>({});

/** How to draw a lane, whatever it targets -- including a `param.<name>` no
 *  routine declares any more, drawn from its own points so it can still be
 *  seen, fixed, and removed. */
export function laneSpec(target: string, params: LaneSpecs, points: Point[] = []): LaneSpec {
  if (target in AUTOMATION_RANGES) return macroSpec(target);
  const known = params[target];
  if (known) return known;
  const nums = points.map((p) => p[1]).filter((v): v is number => typeof v === "number");
  const colour = points.length > 0 && nums.length === 0;
  const lo = Math.min(0, ...nums);
  const hi = Math.max(lo + 1, ...nums);
  return { label: target.startsWith(PARAM_TARGET) ? target.slice(PARAM_TARGET.length) : target, unit: "",
           kind: colour ? "color" : "number", lo, hi, start: colour ? "@primary" : lo };
}

/** Another output's curve (an OSC or MIDI lane): sent as 0-1 -- MIDI scales it
 *  to 0-127, and `showfiles` refuses a MIDI point outside it. */
const EXTERNAL_CURVE: LaneSpec = { label: "curve", unit: "", kind: "number", lo: 0, hi: 1,
                                   min: 0, max: 1, start: 0 };

export function useLaneSpec(row: Row): LaneSpec {
  const params = useContext(ParamLanes);
  if (row.type === "external") return EXTERNAL_CURVE;
  return laneSpec(row.target ?? "", params, row.points ?? []);
}

export const FADES = [0, 1, 2, 4, 8, 16];
export const ROLES = ["primary", "secondary", "accent"] as const;

// -- history ------------------------------------------------------------------

/** Any document made of rows: a track's timeline, or a routine. */
export interface RowsDoc { rows: Row[]; [key: string]: unknown }

interface HistoryState<D> {
  past: D[];
  doc: D | null;
  future: D[];
  saved: D | null;
}

// Any document: a timeline and a routine are rows, a template set is not, and
// undo needs nothing of either.
export function useHistory<D extends object = TimelineDoc>() {
  const [h, setH] = useState<HistoryState<D>>({ past: [], doc: null, future: [], saved: null });
  const [snap, setSnap] = useState<Snap>("bar");
  const [phrases, setPhrases] = useState<number[]>([]);
  const [recording, setRecording] = useState(false);
  const [listView, setListView] = useState(false);

  const setBase = useCallback((doc: D) => {
    setH({ past: [], doc, future: [], saved: doc });
  }, []);

  /** One undoable edit: `change` mutates a copy. */
  const apply = useCallback((change: (draft: D) => void) => {
    setH((prev) => {
      if (!prev.doc) return prev;
      const next = structuredClone(prev.doc);
      change(next);
      return { past: [...prev.past.slice(-199), prev.doc], doc: next, future: [],
               saved: prev.saved };
    });
  }, []);

  const undo = useCallback(() => setH((prev) => {
    const last = prev.past[prev.past.length - 1];
    if (!last || !prev.doc) return prev;
    return { past: prev.past.slice(0, -1), doc: last, future: [prev.doc, ...prev.future],
             saved: prev.saved };
  }), []);
  const redo = useCallback(() => setH((prev) => {
    const next = prev.future[0];
    if (!next || !prev.doc) return prev;
    return { past: [...prev.past, prev.doc], doc: next, future: prev.future.slice(1),
             saved: prev.saved };
  }), []);
  const markSaved = useCallback((doc: D) => {
    setH((prev) => ({ ...prev, saved: doc }));
  }, []);

  /** A beat, snapped to the grid the toolbar says. */
  const snapBeat = useCallback((beat: number): number => {
    if (snap === "phrase" && phrases.length) {
      let best = phrases[0]!;
      for (const p of phrases) if (Math.abs(p - beat) < Math.abs(best - beat)) best = p;
      return best;
    }
    const step = snap === "beat" ? 1 : BEATS_PER_BAR;
    return Math.round(beat / step) * step;
  }, [snap, phrases]);

  return {
    doc: h.doc, setBase, apply, undo, redo, markSaved,
    canUndo: h.past.length > 0, canRedo: h.future.length > 0,
    dirty: h.doc !== h.saved,
    snap, setSnap, snapBeat, setPhrases, recording, setRecording, listView, setListView,
  };
}

export type History<D extends object = TimelineDoc> = ReturnType<typeof useHistory<D>>;

/** What the lanes need of a history -- the same for a timeline and a routine. */
export interface Edits {
  /** The document as it is now -- to name a new item before adding it. */
  readonly doc: RowsDoc | null;
  apply(change: (draft: RowsDoc) => void): void;
  snap: Snap;
  snapBeat(beat: number): number;
}

export { uniqueId };

// -- going between pages ----------------------------------------------------------

const BACK_KEY = "klights.designer.back";

/** Where this browser keeps the working copy of an unsaved document. Studio's
 *  library reads it too, to flag a track with unsaved work. */
export type DocKind = "timeline" | "routine" | "template";

export function draftKey(kind: DocKind, ident: string): string {
  return kind === "timeline" ? `klights.draft.${ident}` : `klights.draft.${kind}.${ident}`;
}

/** Remember this page, so the routine editor's back link returns to it. */
export function rememberBack(): void {
  try { sessionStorage.setItem(BACK_KEY, location.hash); } catch { /* fine */ }
}

/** Where the routine editor's back link goes: the track it was opened from,
 *  else Studio's routines. */
export function backHash(): string {
  try {
    const h = sessionStorage.getItem(BACK_KEY);
    if (h && /^#studio\/track\/[a-z0-9][a-z0-9_-]*$/.test(h)) return h;
  } catch { /* fine */ }
  return "#studio/routines";
}

// -- keys ----------------------------------------------------------------------

/** Whether a key went to something being typed in, where it is the field's. */
export function typing(e: KeyboardEvent): boolean {
  const el = e.target as HTMLElement | null;
  if (!el || !el.tagName) return false;
  return el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT"
    || el.isContentEditable;
}

/** The inputs that take no text: a button, a tick box, a slider. */
const NOT_TEXT = new Set(["button", "checkbox", "color", "file", "image", "radio", "range",
                          "reset", "submit"]);

/** Whether a key went to a field that takes text. Narrower than `typing`: a
 *  menu, a tick box or a slider is something to operate, not to type in. */
export function inText(e: KeyboardEvent): boolean {
  const el = e.target as HTMLElement | null;
  if (!el || !el.tagName) return false;
  if (el.tagName === "INPUT") return !NOT_TEXT.has((el as HTMLInputElement).type);
  return el.tagName === "TEXTAREA" || el.isContentEditable;
}

/** Whether a key is the one that plays and stops: Space on its own, anywhere
 *  but in a field that takes text. */
function playKey(e: KeyboardEvent): boolean {
  return e.key === " " && !e.metaKey && !e.ctrlKey && !e.altKey && !inText(e);
}

/** A key listener on the page that always runs the latest `handler`. */
export function useKeys(handler: (e: KeyboardEvent) => void,
                        type: "keydown" | "keyup" = "keydown"): void {
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => {
    const on = (e: KeyboardEvent) => ref.current(e);
    addEventListener(type, on);
    return () => removeEventListener(type, on);
  }, [type]);
}

/** The designer's plain keys, the same in both editors: Space plays and
 *  stops, Delete (or Backspace) removes what is selected, Escape lets go of it. */
export function useEditorKeys({ history, selected, setSelected, playPause, clip }: {
  history: Edits; selected: string | null; setSelected: (id: string | null) => void;
  playPause: () => void;
  /** Copy, cut, paste, duplicate (Ctrl/Cmd C X V D) and split at the
   *  playhead (S): what kind of document this is, and where the playhead is. */
  clip?: { kind: ClipKind; beat: () => number };
}): void {
  // Space is the transport's wherever the focus is, short of a text field:
  // the button just clicked, a menu or a tick box does not take it instead.
  // Held down, it is still one press.
  useKeys((e) => {
    if (!playKey(e)) return;
    e.preventDefault();
    if (!e.repeat) playPause();
  });
  // A button is pressed as the key comes back up, and in Firefox whether or
  // not the way down was refused -- so the way up is refused too.
  useKeys((e) => { if (playKey(e)) e.preventDefault(); }, "keyup");
  useKeys((e) => {
    if (typing(e) || e.altKey) return;
    const item = selected && !parsePointId(selected) ? selected : null;
    if (clip && (e.metaKey || e.ctrlKey)) {
      const k = e.key.toLowerCase();
      const at = () => Math.max(0, history.snapBeat(clip.beat()));
      if (k === "c" && item) { e.preventDefault(); clipOps.copy(history, item, clip.kind); }
      else if (k === "x" && item) { e.preventDefault(); clipOps.cut(history, item, clip.kind); setSelected(null); }
      else if (k === "v" && clipBoard()?.kind === clip.kind) {
        e.preventDefault();
        setSelected(clipOps.paste(history, clip.kind, at()));
      } else if (k === "d" && item) {
        e.preventDefault();
        setSelected(clipOps.duplicate(history, item, clip.kind) ?? item);
      }
      return;
    }
    if (e.metaKey || e.ctrlKey) return;
    if (clip && item && (e.key === "s" || e.key === "S")) {
      e.preventDefault();
      clipOps.split(history, item, history.snapBeat(clip.beat()));
      return;
    }
    if ((e.key === "Delete" || e.key === "Backspace") && selected) {
      e.preventDefault();
      const id = selected;
      history.apply((d) => removeSelected(d, id));
      setSelected(null);
    } else if (e.key === "Escape" && selected) {
      setSelected(null);
    }
  });
}

/** Remove an item, or an automation point, by its selection id. */
export function removeSelected(d: RowsDoc, id: string): void {
  const waved = parseWaveId(id);
  if (waved) {
    const r = rowOf(d, waved);
    if (r) delete r.wave;
    return;
  }
  const pt = parsePointId(id);
  if (pt) {
    const r = rowOf(d, pt.row);
    if (r) r.points = (r.points ?? []).filter((q) => q[0] !== pt.beat);
    return;
  }
  for (const r of d.rows) if (r.items) r.items = r.items.filter((i) => i.id !== id);
}

// -- the clipboard ------------------------------------------------------------------

export type ClipKind = "timeline" | "routine";

/** Clips copied from a document: each with the row it came from, at a beat
 *  counted from the start of what was copied, and how long that stretch is.
 *  Kept for the page, so a copy from one track pastes into the next -- but
 *  only into the same kind of document: a timeline's clips are not a
 *  routine's rows. */
export interface ClipBoard {
  kind: ClipKind;
  span: number;
  clips: { row: Pick<Row, "id" | "type" | "target" | "role" | "gap">; item: Item }[];
}

let board: ClipBoard | null = null;
const boardListeners = new Set<() => void>();
export function clipBoard(): ClipBoard | null { return board; }
export function setClipBoard(next: ClipBoard | null): void {
  board = next;
  boardListeners.forEach((l) => l());
}
function subscribeBoard(listener: () => void): () => void {
  boardListeners.add(listener);
  return () => { boardListeners.delete(listener); };
}
/** The clipboard, for a button that can only paste when it holds something. */
export function useClipBoard(): ClipBoard | null {
  return useSyncExternalStore(subscribeBoard, clipBoard);
}

const CLIP_ROWS = new Set(["clips", "hits"]);
function rowKey(row: Row): ClipBoard["clips"][number]["row"] {
  const key: ClipBoard["clips"][number]["row"] = { id: row.id, type: row.type };
  if (row.target !== undefined) key.target = row.target;
  if (row.role !== undefined) key.role = row.role;
  if (row.gap !== undefined) key.gap = row.gap;
  return key;
}

/** Take [start, end) out of a row: an item inside it goes, one across an
 *  edge is trimmed to the outside, and one across both is split in two. */
export function cutRange(d: RowsDoc, row: Row, start: number, end: number): void {
  if (!row.items || end <= start) return;
  const out: Item[] = [];
  for (const it of row.items) {
    const a = it.at;
    const b = it.at + it.len;
    if (b <= start || a >= end) { out.push(it); continue; }
    if (a < start) {
      const head: Item = { ...it, len: start - a };
      if ((head.fade ?? 0) > head.len) head.fade = head.len;
      out.push(head);
    }
    if (b > end) {
      // What carries on after the cut starts there, with no fade in of its own.
      const tail: Item = { ...structuredClone(it), id: a < start ? uniqueId(d, it.id) : it.id,
                           at: end, len: b - end };
      delete tail.fade;
      out.push(tail);
    }
  }
  row.items = out;
}

/** What overlaps [start, end) on the clip and hit rows, trimmed to it. */
export function copyRange(doc: RowsDoc, start: number, end: number, kind: ClipKind): ClipBoard {
  const clips: ClipBoard["clips"] = [];
  for (const row of doc.rows) {
    if (!CLIP_ROWS.has(row.type)) continue;
    for (const it of row.items ?? []) {
      const a = Math.max(it.at, start);
      const b = Math.min(it.at + it.len, end);
      if (b <= a) continue;
      const item: Item = { ...structuredClone(it), at: a - start, len: b - a };
      if (it.at < start) delete item.fade;
      else if ((item.fade ?? 0) > item.len) item.fade = item.len;
      clips.push({ row: rowKey(row), item });
    }
  }
  return { kind, span: end - start, clips };
}

export function copyItem(doc: RowsDoc, id: string, kind: ClipKind): ClipBoard | null {
  for (const row of doc.rows) {
    const it = (row.items ?? []).find((i) => i.id === id);
    if (it) return { kind, span: it.len, clips: [{ row: rowKey(row), item: { ...structuredClone(it), at: 0 } }] };
  }
  return null;
}

/** Paste at a beat: each clip onto the row it came from, else a row of the
 *  same kind, else a new one -- over what is there, which the stretch pasted
 *  clears first. Returns the new items' ids. */
export function pasteBoard(d: RowsDoc, b: ClipBoard, at: number): string[] {
  const rows = new Map<string, Row>();
  for (const { row: key } of b.clips) {
    if (rows.has(key.id)) continue;
    let row = d.rows.find((r) => r.id === key.id && r.type === key.type && r.target === key.target)
      ?? d.rows.find((r) => r.type === key.type && r.target === key.target
                     && (key.role === undefined || r.role === key.role));
    if (!row) {
      row = { ...key, id: uniqueId(d, key.target ?? key.type), items: [] };
      d.rows.push(row);
    }
    cutRange(d, row, at, at + b.span);
    rows.set(key.id, row);
  }
  const ids: string[] = [];
  for (const { row: key, item } of b.clips) {
    const id = uniqueId(d, item.id);
    (rows.get(key.id)!.items ??= []).push({ ...structuredClone(item), id, at: at + item.at });
    ids.push(id);
  }
  return ids;
}

/** Split an item at a beat inside it. Returns the second half's id. */
export function splitAt(d: RowsDoc, id: string, beat: number): string | null {
  for (const row of d.rows) {
    const it = (row.items ?? []).find((i) => i.id === id);
    if (!it) continue;
    if (!(it.at < beat && beat < it.at + it.len)) return null;
    const tail: Item = { ...structuredClone(it), id: uniqueId(d, it.id), at: beat,
                         len: it.at + it.len - beat };
    delete tail.fade;
    it.len = beat - it.at;
    if ((it.fade ?? 0) > it.len) it.fade = it.len;
    row.items!.push(tail);
    return tail.id;
  }
  return null;
}

/** The clip operations as one undoable edit each, for the keys, the clip's
 *  menu and the inspector alike. Each answers with what should be selected:
 *  a paste's first new clip, a duplicate, a split's second half. Ids are
 *  worked out on a copy first -- an edit is applied when React renders, too
 *  late to read them back from it -- and `uniqueId` gives the same answer on
 *  the same document. */
export const clipOps = {
  copy(h: Edits, id: string, kind: ClipKind): boolean {
    const b = h.doc ? copyItem(h.doc, id, kind) : null;
    if (b) setClipBoard(b);
    return b != null;
  },
  cut(h: Edits, id: string, kind: ClipKind): void {
    if (clipOps.copy(h, id, kind)) h.apply((d) => removeSelected(d, id));
  },
  paste(h: Edits, kind: ClipKind, at: number): string | null {
    const b = clipBoard();
    if (!b || b.kind !== kind || !h.doc) return null;
    const ids = pasteBoard(structuredClone(h.doc), b, at);
    h.apply((d) => { pasteBoard(d, b, at); });
    return ids[0] ?? null;
  },
  duplicate(h: Edits, id: string, kind: ClipKind): string | null {
    if (!h.doc) return null;
    const b = copyItem(h.doc, id, kind);
    const it = itemOf(h.doc, id);
    if (!b || !it) return null;
    const at = it.at + it.len;
    const ids = pasteBoard(structuredClone(h.doc), b, at);
    h.apply((d) => { pasteBoard(d, b, at); });
    return ids[0] ?? null;
  },
  split(h: Edits, id: string, beat: number): string | null {
    if (!h.doc) return null;
    const tail = splitAt(structuredClone(h.doc), id, beat);
    if (tail) h.apply((d) => { splitAt(d, id, beat); });
    return tail;
  },
  /** Whether a beat falls inside an item, so it can be split there. */
  inside(doc: RowsDoc | null, id: string, beat: number): boolean {
    const it = doc ? itemOf(doc, id) : undefined;
    return !!it && it.at < beat && beat < it.at + it.len;
  },
};

/** The kind of thing a browser or a drop places, as dragged between them. */
export const PLACE_MIME = "application/x-klights-place";

/** What was dropped, if it is something this page can place: a drag can come
 *  from another window, so its data is read, not trusted. */
function placeable(raw: string): Placeable | null {
  try {
    const v = JSON.parse(raw) as Partial<Placeable> | null;
    if (v?.kind === "routine" && typeof v.id === "string") return v as Placeable;
    if (v?.kind === "palette" && typeof v.name === "string") return v as Placeable;
    if (v?.kind === "hit" && ["flash", "strobe", "blackout"].includes(v.hit as string)) return v as Placeable;
    if (v?.kind === "look" && typeof v.name === "string") return v as Placeable;
    if (v?.kind === "snapshot" && typeof v.preset === "string") return v as Placeable;
  } catch { /* not ours */ }
  return null;
}
export type Placeable =
  | { kind: "routine"; id: string }
  | { kind: "palette"; name: string; colours?: Record<"primary" | "secondary" | "accent", string> }
  | { kind: "hit"; hit: "flash" | "strobe" | "blackout" }
  /** This rig's own: a look from its library, or a preset as a snapshot. */
  | { kind: "look"; name: string }
  | { kind: "snapshot"; preset: string };

function rowOf(doc: RowsDoc, id: string): Row | undefined {
  return doc.rows.find((r) => r.id === id);
}

function itemOf(doc: RowsDoc, id: string): Item | undefined {
  for (const r of doc.rows) {
    const it = (r.items ?? []).find((i) => i.id === id);
    if (it) return it;
  }
  return undefined;
}

// -- the toolbar ------------------------------------------------------------------

interface DraftCheck { errors: string[]; warnings: string[]; problems: string[] }
interface Kept<D> { doc: D; rev: string; at: number }

function Toolbar<D extends object>({ history, rev, setRev, engine, kind, ident }: {
  history: History<D>; rev: string; setRev: (r: string) => void; engine: Engine;
  /** What is being edited, and which one: the commands and the recovery copy
   *  follow from it. */
  kind: DocKind; ident: string;
}) {
  const { doc } = history;
  const [check, setCheck] = useState<DraftCheck | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [showProblems, setShowProblems] = useState(false);
  const key = draftKey(kind, ident);
  // The lanes' tools mean nothing to a document without lanes.
  const lanes = kind !== "template";
  // `request` is stable; `engine` is a new object on every snapshot, and a
  // debounce keyed on it would be reset ten times a second and never fire.
  const { request } = engine;
  const connected = engine.status === "open";

  // A working copy this browser kept from last time (the tab died, the laptop
  // slept): offered once the document loads, and nothing is written over it
  // until it is restored or discarded. `undefined` = not looked yet.
  const [kept, setKept] = useState<Kept<D> | null | undefined>(undefined);
  useEffect(() => {
    if (!doc || kept !== undefined) return;
    let found: Kept<D> | null = null;
    try {
      const raw = localStorage.getItem(key);
      if (raw) found = JSON.parse(raw) as Kept<D>;
    } catch { /* unreadable: nothing to offer */ }
    setKept(found?.doc && JSON.stringify(found.doc) !== JSON.stringify(doc) ? found : null);
  }, [doc, key, kept]);

  // Ask the engine what it thinks of the working copy, a moment after typing
  // stops -- and once it is connected, so the first check is not lost to a
  // page that loaded faster than its socket. Keep the working copy in this
  // browser in case the tab dies.
  useEffect(() => {
    if (!doc) return;
    if (kept === null) {
      try {
        if (history.dirty) localStorage.setItem(key, JSON.stringify({ doc, rev, at: Date.now() }));
        else localStorage.removeItem(key);
      } catch { /* private mode: no recovery copy */ }
    }
    if (!connected) return;
    const timer = setTimeout(async () => {
      const reply = await request(kind === "timeline" ? { type: "timeline_draft", doc }
        : kind === "routine" ? { type: "routine_draft", doc } : { type: "template_draft", doc });
      if (reply.ok && reply.data) setCheck(reply.data as DraftCheck);
    }, 500);
    return () => clearTimeout(timer);
  }, [doc, request, connected, history.dirty, key, rev, kind, kept]);

  // Ctrl/Cmd+Z, Shift+Ctrl/Cmd+Z or Ctrl+Y, Ctrl/Cmd+S. Undo in a text field
  // is the field's own; Save is the page's wherever the cursor is, and the
  // browser's "save this page" never is.
  const errorCount = check?.errors.length ?? 0;
  // A file not written yet is unsaved as it stands: a start from New -- a
  // copy, a look, a draft -- is worth saving before anything is changed.
  const unsaved = !!doc && (history.dirty || rev === "");
  useKeys((e) => {
    if (!(e.metaKey || e.ctrlKey) || e.altKey) return;
    const k = e.key.toLowerCase();
    if (k === "s") {
      e.preventDefault();
      if (unsaved && !saving && errorCount === 0) void save();
      return;
    }
    if (typing(e)) return;
    if (k === "z" && !e.shiftKey) { e.preventDefault(); history.undo(); }
    else if ((k === "z" && e.shiftKey) || k === "y") { e.preventDefault(); history.redo(); }
  });

  const restore = () => {
    const copy = kept?.doc;
    setKept(null);
    if (!copy) return;
    // One undoable edit: Undo goes back to the file as saved.
    history.apply((d) => {
      const bag = d as Record<string, unknown>;
      for (const k of Object.keys(bag)) delete bag[k];
      Object.assign(d, structuredClone(copy));
    });
  };
  const discard = () => {
    try { localStorage.removeItem(key); } catch { /* fine */ }
    setKept(null);
  };

  const save = async () => {
    if (!doc) return;
    setSaving(true);
    setSaveError(null);
    const reply: Reply = await engine.request(
      kind === "timeline" ? { type: "timeline_save", doc, base_rev: rev }
        : kind === "routine" ? { type: "routine_save", doc, base_rev: rev }
          : { type: "template_save", doc, base_rev: rev });
    setSaving(false);
    if (reply.ok) {
      setRev((reply.data as { rev: string }).rev);
      history.markSaved(doc);
      try { localStorage.removeItem(key); } catch { /* fine */ }
    } else {
      setSaveError(reply.error ?? "the engine refused");
    }
  };

  const errors = check?.errors.length ?? 0;
  const problems = (check?.problems.length ?? 0) + (check?.warnings.length ?? 0);
  return (
    <span className="d-tools">
      {lanes && <label className="small muted">Snap{" "}
        <select value={history.snap} aria-label="snap"
                onChange={(e) => history.setSnap(e.target.value as Snap)}>
          <option value="beat">beat</option>
          <option value="bar">bar</option>
          <option value="phrase">phrase</option>
        </select>
      </label>}
      <button onClick={history.undo} disabled={!history.canUndo}>Undo</button>
      <button onClick={history.redo} disabled={!history.canRedo}>Redo</button>
      {lanes && <button className={history.listView ? "on" : ""}
              onClick={() => history.setListView(!history.listView)}>List</button>}
      <button className={check && errors ? "d-bad" : problems ? "d-warn" : ""}
              onClick={() => setShowProblems(!showProblems)}
              title="What the engine thinks of this draft">
        {!check ? "checking…" : errors ? `${errors} error(s)`
          : problems ? `${problems} note(s)` : "valid"}
      </button>
      <button onClick={() => void save()} disabled={!unsaved || saving || errors > 0}
              className={unsaved ? "d-primary" : ""}>
        {saving ? "Saving…" : unsaved ? "Save" : "Saved"}
      </button>
      {saveError && <span className="d-error small" role="alert">{saveError}</span>}
      {kept && (
        <div className="d-pop" role="alertdialog" aria-label="unsaved changes">
          <p className="small">
            Unsaved changes from {new Date(kept.at).toLocaleString()} were kept in this browser.
            {kept.rev !== rev && " The file has been saved since (another machine, MCP or "
              + "another tab), so they cannot simply be restored over it."}
          </p>
          <div className="d-chips">
            {kept.rev === rev && <button className="d-primary" onClick={restore}>Restore them</button>}
            <a className="d-link small" download={`${ident}.unsaved.json`}
               href={`data:application/json;charset=utf-8,${encodeURIComponent(
                 JSON.stringify(kept.doc, null, 2))}`}>Download them</a>
            <button onClick={discard}>Discard</button>
          </div>
        </div>
      )}
      {showProblems && check && (
        <div className="d-pop" role="dialog" aria-label="draft check">
          {[...check.errors.map((e) => ["error", e]),
            ...check.problems.map((p) => ["rig", p]),
            ...check.warnings.map((w) => ["note", w])].map(([k, t], i) => (
              <div key={i} className={`small d-${k}`}>{k}: {t}</div>))}
          {!errors && !problems && <div className="small">Nothing to report.</div>}
        </div>
      )}
    </span>
  );
}

// -- adding at a spot on a lane ----------------------------------------------------

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

/** The hits, as every place that adds one offers them. */
export const HITS = [
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

/** A menu that closes on a press anywhere outside it (`.d-ctx`), or Escape. */
export function useMenuDismiss(open: boolean, close: () => void): void {
  const closeRef = useRef(close);
  closeRef.current = close;
  useEffect(() => {
    if (!open) return;
    const handler = (e: Event) => {
      if (e instanceof KeyboardEvent && e.key !== "Escape") return;
      if (e instanceof MouseEvent && (e.target as Element | null)?.closest?.(".d-ctx")) return;
      closeRef.current();
    };
    addEventListener("mousedown", handler);
    addEventListener("keydown", handler);
    return () => { removeEventListener("mousedown", handler); removeEventListener("keydown", handler); };
  }, [open]);
}

/** One thing a lane's menu offers: what it shows, the words it is found by,
 *  the heading it is filed under, and what choosing it does. */
export interface PickEntry {
  key: string;
  label: React.ReactNode;
  text?: string;
  group?: string;
  add: () => void;
}

/** Past this many entries, the menu has a search box (and starts in it). */
const PICK_SEARCH = 12;

/**
 * What can go where a lane's empty space was clicked, as a menu at the
 * pointer: the entries under their headings, with a search box once there are
 * enough to need one -- a show's routines and its rig's looks run to
 * hundreds. Enter in the search box takes the first match.
 */
export function PickMenu({ label, head, x, y, entries, empty }: {
  label: string; head: React.ReactNode; x: number; y: number;
  entries: PickEntry[];
  /** Said when there is nothing at all to offer. */
  empty: string;
}) {
  const [query, setQuery] = useState("");
  const search = entries.length > PICK_SEARCH;
  const words = normalizeName(query).split(" ").filter(Boolean);
  const shown = entries.filter((e) => words.every((w) => normalizeName(e.text ?? e.key).includes(w)));
  // Placed for the whole list, so it stays put while a search narrows it.
  const height = Math.min(entries.length * 28 + (search ? 110 : 60), 420, innerHeight * 0.6);
  return (
    <div className="d-ctx d-pick" role="menu" aria-label={label}
         style={{ left: Math.max(8, Math.min(x, innerWidth - 240)),
                  top: Math.max(8, Math.min(y, innerHeight - height - 8)) }}>
      <span className="d-ctx-head small muted">{head}</span>
      {search && (
        <input type="search" value={query} placeholder="Search" aria-label={`search ${label}`}
               autoFocus onChange={(e) => setQuery(e.target.value)}
               onKeyDown={(e) => {
                 if (e.key !== "Enter" || !shown.length) return;
                 e.preventDefault();
                 shown[0]!.add();
               }} />)}
      <div className="d-pick-list">
        {shown.map((e, i) => (
          <Fragment key={e.key}>
            {e.group && e.group !== shown[i - 1]?.group && (
              <span className="d-ctx-group">{e.group}</span>)}
            <button role="menuitem" autoFocus={!search && i === 0} onClick={e.add}>{e.label}</button>
          </Fragment>
        ))}
      </div>
      {!shown.length && (
        <span className="small muted d-ctx-head">{entries.length ? "Nothing matches." : empty}</span>)}
    </div>
  );
}

// -- who a role plays on ------------------------------------------------------------

/** The fixtures a role's tag reaches, found the way the engine finds them
 *  (`Rigging.tagged`): carrying the tag, or named it. */
export function tagged(state: EngineState | null | undefined, tag: string): FixtureState[] {
  return (state?.fixtures ?? []).filter((f) => f.tags.includes(tag) || f.name === tag);
}

/** Every tag on the rig's fixtures, the widest first. Not the engine's
 *  `groups`: those fold tags that name the same fixtures into one (despacio's
 *  heads are both "movers" and "corner movers") to make filters, and a role
 *  may name any of them. */
export function rigTags(state: EngineState | null | undefined): string[] {
  const count = new Map<string, number>();
  for (const f of state?.fixtures ?? []) for (const t of f.tags) count.set(t, (count.get(t) ?? 0) + 1);
  return [...count].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([t]) => t);
}

/** How many fixtures a tag reaches on this rig -- or that it reaches none, so
 *  a role on it lights nothing. Nothing is said with no engine to ask. */
export function Reach({ state, tag, optional }: {
  state: EngineState | null | undefined; tag: string; optional?: boolean;
}) {
  if (!state || !tag) return null;
  const found = tagged(state, tag);
  if (found.length) {
    return <span className="small muted d-reach" title={found.map((f) => f.name).join(", ")}>
      {found.length} fixture{found.length === 1 ? "" : "s"}</span>;
  }
  return <span className={`small d-reach${optional ? " muted" : " none"}`}
               title="No fixture carries this tag or has this name. Tags are set on the console's Setup tab">
    {optional ? "none here (optional)" : "no fixtures on this rig"}</span>;
}

/** The looks a picker offers: none retired for something better, and no
 *  single step of a chase -- the console files those under their chase. */
export function offeredLooks(looks: LookInfo[] | undefined): LookInfo[] {
  return (looks ?? []).filter((l) => !l.retired && !l.step_of);
}

/** Where a fixture's tags are set: the console's Setup tab. */
export function EditTags() {
  return <a className="small d-link" href="#setup"
            title="Tags are set per fixture on the console's Setup tab (Design mode)">Edit tags</a>;
}

/**
 * Which fixtures each of a routine's roles plays on, for ONE use of it -- a
 * clip, a template set's pick: its default tag, or another tag, or one
 * fixture by name (`bind`). A binding the routine has no role for -- left by
 * an edit to the routine -- is shown so it can be taken off.
 */
export function RoleBinds({ label, roles, bind, state, onChange }: {
  label: string;
  roles: Record<string, { default: string; optional?: boolean }>;
  bind: Record<string, string> | undefined;
  state: EngineState | null | undefined;
  onChange: (bind: Record<string, string> | undefined) => void;
}) {
  const tags = rigTags(state);
  const names = (state?.fixtures ?? []).map((f) => f.name).filter((n) => !tags.includes(n));
  const setRole = (role: string, value: string) => {
    const next = { ...(bind ?? {}) };
    if (value) next[role] = value; else delete next[role];
    onChange(Object.keys(next).length ? next : undefined);
  };
  const stale = Object.keys(bind ?? {}).filter((r) => !(r in roles));
  return (
    <div className="d-binds" role="group" aria-label={`${label} roles`}>
      <span className="small muted">Plays on</span>
      {Object.entries(roles).map(([role, spec]) => {
        const value = bind?.[role] ?? "";
        const known = !value || tags.includes(value) || names.includes(value);
        return (
          <span key={role} className="d-bind">
            <span className="mono small">{role}</span>
            <select value={value} aria-label={`${role} plays on`}
                    onChange={(e) => setRole(role, e.target.value)}>
              <option value="">its default, {spec.default}</option>
              {!known && <option value={value}>{value} (not on this rig)</option>}
              {tags.length > 0 && (
                <optgroup label="Tags">{tags.map((t) => <option key={t} value={t}>{t}</option>)}</optgroup>)}
              {names.length > 0 && (
                <optgroup label="One fixture">
                  {names.map((n) => <option key={n} value={n}>{n}</option>)}</optgroup>)}
            </select>
            <Reach state={state} tag={value || spec.default} optional={spec.optional} />
          </span>
        );
      })}
      {stale.map((role) => (
        <span key={role} className="d-bind small d-error">
          binds {role}, which this routine has no role for
          <button className="small" aria-label={`drop the binding for ${role}`}
                  onClick={() => setRole(role, "")}>×</button>
        </span>
      ))}
      <EditTags />
    </div>
  );
}

// -- lanes ----------------------------------------------------------------------

const LANE_H = 40;

function LaneSvg({ row, x, width, zoom, selected, onSelect, history, onMenu, onDropItem, onAdd,
                   noun }: {
  row: Row; x: (b: number) => number; width: number; zoom: number;
  selected: string | null; onSelect: (id: string | null) => void; history: Edits;
  /** A clip's menu, asked for with the other mouse button. */
  onMenu?: (id: string, clientX: number, clientY: number) => void;
  /** Something dragged from the browser, let go at a beat on this lane. */
  onDropItem?: (what: Placeable, beat: number) => void;
  /** A click on the lane's empty space, to add something there: at the beat
   *  clicked, or null for the playhead (Enter, with the lane focused). */
  onAdd?: (beat: number | null, clientX: number, clientY: number) => void;
  /** What a click adds, for an empty lane to say: "a block", "a routine". */
  noun?: string;
}) {
  const menu = (e: React.MouseEvent, id: string) => {
    if (!onMenu) return;
    e.preventDefault();
    onSelect(id);
    onMenu(id, e.clientX, e.clientY);
  };
  // A drag is shown live from local state and committed as ONE edit on release.
  const [drag, setDrag] = useState<{ id: string; mode: "move" | "resize";
                                     start: number; at: number; len: number;
                                     dAt: number; dLen: number } | null>(null);
  const hits = row.type === "hits";
  const items = row.items ?? [];

  const begin = (e: React.PointerEvent, it: Item, mode: "move" | "resize") => {
    e.stopPropagation();
    onSelect(it.id);
    (e.target as Element).setPointerCapture?.(e.pointerId);
    setDrag({ id: it.id, mode, start: e.clientX, at: it.at, len: it.len, dAt: 0, dLen: 0 });
  };
  const move = (e: React.PointerEvent) => {
    if (!drag) return;
    const beats = (e.clientX - drag.start) / zoom;
    if (drag.mode === "move") {
      setDrag({ ...drag, dAt: history.snapBeat(drag.at + beats) - drag.at });
    } else {
      const end = history.snapBeat(drag.at + drag.len + beats);
      setDrag({ ...drag, dLen: Math.max(history.snap === "beat" ? 1 : 4, end - drag.at) - drag.len });
    }
  };
  const end = () => {
    if (!drag) return;
    const { id, dAt, dLen } = drag;
    setDrag(null);
    if (!dAt && !dLen) return;
    history.apply((d) => {
      const it = itemOf(d, id);
      if (!it) return;
      it.at = it.at + dAt;
      it.len = it.len + dLen;
      if ((it.fade ?? 0) > it.len) it.fade = it.len;
    });
  };

  const addable = noun ?? (hits ? "a hit" : "a block");
  return (
    <svg width={width} height={LANE_H} className={`d-lane${onAdd ? " d-addable" : ""}`}
         aria-label={`lane ${row.id}`}
         tabIndex={onAdd ? 0 : undefined}
         aria-keyshortcuts={onAdd ? "Enter" : undefined}
         onPointerMove={move} onPointerUp={end} onPointerCancel={end}
         onClick={(e) => {
           if (e.target !== e.currentTarget) return;
           onSelect(null);
           if (onAdd) {
             const r = e.currentTarget.getBoundingClientRect();
             onAdd(Math.max(0, (e.clientX - r.left) / zoom), e.clientX, e.clientY);
           }
         }}
         onKeyDown={(e) => {
           if (!onAdd || e.key !== "Enter" || e.target !== e.currentTarget) return;
           e.preventDefault();
           const r = e.currentTarget.getBoundingClientRect();
           onAdd(null, r.left, r.bottom);
         }}
         onDragOver={(e) => {
           if (onDropItem && e.dataTransfer.types.includes(PLACE_MIME)) e.preventDefault();
         }}
         onDrop={(e) => {
           const what = placeable(e.dataTransfer.getData(PLACE_MIME));
           if (!onDropItem || !what) return;
           e.preventDefault();
           const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
           onDropItem(what, Math.max(0, (e.clientX - r.left) / zoom));
         }}>
      {onAdd && !items.length && (
        <text x={8} y={LANE_H / 2 + 4} className="d-lane-hint" pointerEvents="none">
          Empty -- click to add {addable}</text>)}
      {items.map((it) => {
        const live = drag?.id === it.id ? drag : null;
        const at = it.at + (live?.dAt ?? 0);
        const len = it.len + (live?.dLen ?? 0);
        const sel = selected === it.id;
        if (hits) {
          const cx = x(at);
          return (
            <g key={it.id} className={`d-hit d-hit-${it.hit}${sel ? " sel" : ""}`}
               onPointerDown={(e) => begin(e, it, "move")} role="button"
               onContextMenu={(e) => menu(e, it.id)}
               aria-label={`${it.hit} at bar ${barBeat(at)}`}>
              <rect x={cx} y={LANE_H / 2 - 3} width={Math.max(2, x(len) - x(0))} height={6}
                    rx={3} className="d-hit-span" />
              <path d={`M${cx} ${LANE_H / 2 - 9} l7 9 l-7 9 l-7 -9 z`} />
            </g>
          );
        }
        const w = Math.max(3, x(len) - x(0) - 1);
        const fadeW = Math.min(w, x(it.fade ?? 0) - x(0));
        return (
          <g key={it.id} className={`d-clip d-clip-${it.kind ?? "block"}${sel ? " sel" : ""}`}
             role="button" aria-label={`${itemName(it)} at bar ${barBeat(at)}`}
             onContextMenu={(e) => menu(e, it.id)}>
            <rect x={x(at)} y={2} width={w} height={LANE_H - 4} rx={4}
                  onPointerDown={(e) => begin(e, it, "move")} />
            {fadeW > 0 && (
              <path d={`M${x(at)} ${LANE_H - 2} L${x(at) + fadeW} 2 L${x(at)} 2 z`}
                    className="d-fade" pointerEvents="none" />)}
            <text x={x(at) + 5} y={16} className="d-clip-name" pointerEvents="none">
              {itemName(it)}</text>
            <text x={x(at) + 5} y={30} className="d-clip-sub" pointerEvents="none">
              {itemSub(it)}</text>
            <rect x={x(at) + w - 6} y={2} width={6} height={LANE_H - 4}
                  className="d-resize" aria-label={`resize ${it.id}`}
                  onPointerDown={(e) => begin(e, it, "resize")} />
          </g>
        );
      })}
    </svg>
  );
}

/** A point's selection id: points have no ids of their own, so the row and
 *  the beat name one. */
export function pointId(rowId: string, beat: number): string {
  return `pt:${rowId}:${beat}`;
}

export function parsePointId(id: string | null): { row: string; beat: number } | null {
  const m = id ? /^pt:(.+):(-?[\d.]+(?:e-?\d+)?)$/.exec(id) : null;
  return m ? { row: m[1]!, beat: Number(m[2]) } : null;
}

/** A lane's wave's selection id: a row has at most one. */
export function waveId(rowId: string): string {
  return `wave:${rowId}`;
}

export function parseWaveId(id: string | null): string | null {
  return id?.startsWith("wave:") ? id.slice(5) : null;
}

/**
 * A new wave for a lane, sized to stay in range: a number lane's swings a
 * quarter of its drawn range, upward if its points leave more room above
 * than below, else downward -- and no further than that room, so the engine
 * accepts it as drawn. A colour lane's swings all the way to the accent.
 */
export function defaultWave(row: Row, spec: LaneSpec): WaveSpec {
  if (spec.kind === "color") return { shape: "sine", bars: 4, toward: "@accent", depth: 1 };
  const nums = (row.points ?? []).map((p) => p[1]).filter((v): v is number => typeof v === "number");
  const top = Math.max(spec.lo, ...nums);
  const bottom = Math.min(spec.hi, ...nums);
  const up = (spec.max ?? spec.hi) - top;
  const down = bottom - (spec.min ?? spec.lo);
  const quarter = (spec.hi - spec.lo) / 4;
  const depth = up >= down ? Math.min(up, quarter) : -Math.min(down, quarter);
  return { shape: "sine", bars: 4, depth: Math.round(depth * 100) / 100 };
}

const CURVES = ["linear", "step", "ease"] as const;

/** A colour value as CSS, or null for one only the show can resolve -- a
 *  palette role, a colour look -- which the lane names instead of painting. */
export function cssColour(v: unknown): string | null {
  if (typeof v === "string" && /^#[0-9a-fA-F]{6}$/.test(v)) return v;
  if (Array.isArray(v) && v.length === 3 && v.every((c) => typeof c === "number")) {
    return `rgb(${v.map((c) => Math.round(Math.max(0, Math.min(1, c as number)) * 255)).join(",")})`;
  }
  return null;
}

/** The point whose value a lane is leaving at a beat: the last at or before
 *  it, or the first, which holds before it. */
function pointBefore(points: Point[], beat: number): Point | undefined {
  let held = points[0];
  for (const p of points) if (p[0] <= beat) held = p;
  return held;
}

/**
 * A colour parameter's lane: a band of what it is at each beat. A colour is
 * not a height, so there is no curve -- each segment is painted from the point
 * it leaves to the point it arrives at (a step holds, then jumps), and a value
 * only the show can resolve, like "@primary", is written rather than painted.
 */
function ColourBand({ points, x, width, id }: {
  points: Point[]; x: (b: number) => number; width: number; id: string;
}) {
  if (!points.length) return null;
  const top = 8;
  const h = LANE_H - 16;
  const spans: { x0: number; x1: number; a: PointValue; b: PointValue; step: boolean }[] = [];
  const first = points[0]!;
  const last = points[points.length - 1]!;
  if (x(first[0]) > 0) spans.push({ x0: 0, x1: x(first[0]), a: first[1], b: first[1], step: true });
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1]!;
    const b = points[i]!;
    spans.push({ x0: x(a[0]), x1: x(b[0]), a: a[1], b: b[1], step: b[2] === "step" });
  }
  spans.push({ x0: x(last[0]), x1: width, a: last[1], b: last[1], step: true });
  return (
    <g pointerEvents="none">
      {spans.map((s, i) => {
        const ca = cssColour(s.a);
        const cb = cssColour(s.b);
        const w = Math.max(0, s.x1 - s.x0);
        if (!ca || (!s.step && !cb)) {
          return (
            <g key={i}>
              <rect x={s.x0} y={top} width={w} height={h} className="d-band-named" />
              {w > 40 && <text x={s.x0 + 8} y={top + h / 2 + 4} className="d-label">
                {String(s.a)}{!s.step && s.b !== s.a ? ` → ${String(s.b)}` : ""}</text>}
            </g>
          );
        }
        if (s.step || ca === cb) {
          return <rect key={i} x={s.x0} y={top} width={w} height={h} fill={ca} opacity={0.85} />;
        }
        const gid = `band-${id}-${i}`;
        return (
          <g key={i}>
            <defs>
              <linearGradient id={gid}>
                <stop offset="0" stopColor={ca} /><stop offset="1" stopColor={cb!} />
              </linearGradient>
            </defs>
            <rect x={s.x0} y={top} width={w} height={h} fill={`url(#${gid})`} opacity={0.85} />
          </g>
        );
      })}
    </g>
  );
}

/** A colour lane's wave: the colour it swings toward, laid over the band as
 *  strongly as the wave pulls at each beat. One it cannot paint (a palette
 *  role) is drawn in the band's own named-colour grey. */
function ColourWave({ wave, x, width }: { wave: WaveSpec; x: (b: number) => number; width: number }) {
  const perBeat = x(1) - x(0);
  const end = width / perBeat;
  const step = Math.max(end / 2000, Math.min(0.5, (wave.bars * BEATS_PER_BAR) / 16));
  const fill = cssColour(wave.toward);
  const strips: React.ReactNode[] = [];
  for (let b = 0, i = 0; b < end; b += step, i++) {
    const pull = Math.max(0, Math.min(1, waveLevel(wave, b + step / 2)));
    if (pull > 0.02) {
      strips.push(<rect key={i} x={x(b)} y={8} width={step * perBeat + 0.5} height={LANE_H - 16}
                        fill={fill ?? undefined} className={fill ? undefined : "d-band-named"}
                        opacity={fill ? pull * 0.85 : pull * 0.4} />);
    }
  }
  return <g pointerEvents="none" aria-label="wave">{strips}</g>;
}

/**
 * An automation lane. Click empty space to add a point there; click a point to
 * select it (the inspector edits its value and the curve that arrives at it);
 * drag a point to move it -- across to another beat, up and down to another
 * value -- as one edit when it is let go. A colour lane's points move only
 * across: up and down means nothing for a colour, which the inspector picks.
 */
function AutoSvg({ row, x, width, history, selected, onSelect }: {
  row: Row; x: (b: number) => number; width: number; history: Edits;
  selected?: string | null; onSelect?: (id: string | null) => void;
}) {
  const spec = useLaneSpec(row);
  const { lo, hi } = spec;
  const colour = spec.kind === "color";
  const points = row.points ?? [];
  const perBeat = x(1) - x(0);
  const y = (v: number) => LANE_H - 3 - ((v - lo) / (hi - lo)) * (LANE_H - 6);
  const [drag, setDrag] = useState<{ beat: number; x0: number; y0: number; v0: number | null;
                                     dBeat: number; value: number | null } | null>(null);

  // What is drawn: the points as they are, or as the drag has them.
  const shown: Point[] = drag
    ? points.map((p) => (p[0] === drag.beat
      ? [p[0] + drag.dBeat, drag.value ?? p[1], ...(p.length > 2 ? [p[2]] : [])] as Point
      : p)).sort((a, b) => a[0] - b[0])
    : points;
  const samples: string[] = [];
  if (shown.length && row.wave && !colour) {
    // A wave never settles, so the whole lane is sampled, finely enough to
    // show its shape and no more finely than a few thousand points.
    const end = width / perBeat;
    const step = Math.max(end / 4000, Math.min(0.5, (row.wave.bars * BEATS_PER_BAR) / 24));
    const waved = { ...row, points: shown };
    for (let b = 0; b <= end; b += step) {
      const v = laneValue(waved, b);
      if (v != null) samples.push(`${x(b)},${y(Math.max(lo, Math.min(hi, v)))}`);
    }
  } else if (shown.length) {
    const first = shown[0]![0];
    const last = shown[shown.length - 1]![0];
    const lead = curveValue(shown, first);
    if (lead != null) samples.push(`${x(Math.min(0, first))},${y(lead)}`);
    for (let b = first; b <= last; b += 0.5) {
      const v = curveValue(shown, b);
      if (v != null) samples.push(`${x(b)},${y(v)}`);
    }
    // After the last point the value holds, to the end of the lane.
    const tail = curveValue(shown, last);
    if (tail != null) samples.push(`${x(last)},${y(tail)}`, `${width},${y(tail)}`);
  }

  const begin = (e: React.PointerEvent, p: Point) => {
    e.stopPropagation();
    onSelect?.(pointId(row.id, p[0]));
    (e.target as Element).setPointerCapture?.(e.pointerId);
    const v0 = typeof p[1] === "number" ? p[1] : null;
    setDrag({ beat: p[0], x0: e.clientX, y0: e.clientY, v0, dBeat: 0, value: v0 });
  };
  const move = (e: React.PointerEvent) => {
    if (!drag) return;
    const beat = history.snapBeat(drag.beat + (e.clientX - drag.x0) / perBeat);
    const value = drag.v0 == null ? null : Math.round(Math.max(lo, Math.min(hi,
      drag.v0 - ((e.clientY - drag.y0) / (LANE_H - 6)) * (hi - lo))) * 100) / 100;
    setDrag({ ...drag, dBeat: beat - drag.beat, value });
  };
  const end = () => {
    if (!drag) return;
    const { beat, dBeat, value, v0 } = drag;
    setDrag(null);
    if (!dBeat && value === v0) return;
    const to = beat + dBeat;
    history.apply((d) => {
      const target = rowOf(d, row.id);
      const pt = target?.points?.find((q) => q[0] === beat);
      if (!target || !pt) return;
      const moved = [to, value ?? pt[1], ...(pt.length > 2 ? [pt[2]] : [])] as Point;
      target.points = [...(target.points ?? []).filter((q) => q[0] !== beat && q[0] !== to), moved]
        .sort((a, b) => a[0] - b[0]);
    });
    onSelect?.(pointId(row.id, to));
  };

  return (
    <svg width={width} height={LANE_H} className="d-lane d-auto-svg"
         aria-label={`automation ${row.target ?? row.id}`}
         onPointerMove={move} onPointerUp={end} onPointerCancel={end}
         onClick={(e) => {
           if (e.target !== e.currentTarget) return;
           const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
           const beat = history.snapBeat((e.clientX - r.left) / perBeat);
           const frac = 1 - (e.clientY - r.top - 3) / (LANE_H - 6);
           // A colour lane's new point starts as the colour it lands on, so
           // adding one changes nothing until the inspector picks another.
           const value: PointValue = colour
             ? pointBefore(points, beat)?.[1] ?? spec.start
             : Math.round((lo + Math.max(0, Math.min(1, frac)) * (hi - lo)) * 100) / 100;
           history.apply((d) => {
             const target = rowOf(d, row.id);
             if (!target) return;
             const pts = (target.points ?? []).filter((p) => p[0] !== beat);
             pts.push([beat, value]);
             pts.sort((a, b) => a[0] - b[0]);
             target.points = pts;
           });
           onSelect?.(pointId(row.id, beat));
         }}>
      {colour
        ? <ColourBand points={shown} x={x} width={width} id={row.id} />
        : <polyline points={samples.join(" ")} className="d-curve" pointerEvents="none" />}
      {colour && row.wave && <ColourWave wave={row.wave} x={x} width={width} />}
      {shown.map((p, i) => {
        const id = pointId(row.id, drag && p[0] === drag.beat + drag.dBeat ? drag.beat : p[0]);
        return (
          <circle key={i} cx={x(p[0])} cy={typeof p[1] === "number" ? y(p[1]) : LANE_H / 2}
                  r={selected === id ? 6 : 4}
                  style={colour ? { fill: cssColour(p[1]) ?? undefined } : undefined}
                  className={`d-point${colour ? " d-point-colour" : ""}${selected === id ? " sel" : ""}`}
                  role="button"
                  aria-label={`point at bar ${barBeat(p[0])}: ${String(p[1])}`}
                  onPointerDown={(e) => begin(e, p)}
                  onClick={(e) => e.stopPropagation()} />
        );
      })}
    </svg>
  );
}

/** The selected automation point: its place, its value, and the curve that
 *  arrives at it (it shapes the segment from the point before). */
function PointInspector({ row, beat, history, onSelect }: {
  row: Row; beat: number; history: Edits; onSelect: (id: string | null) => void;
}) {
  const spec = useLaneSpec(row);
  const pt = row.points?.find((p) => p[0] === beat);
  if (!pt) return null;
  // What the engine accepts, not what the lane draws: a parameter left open
  // on one side takes any value on that side.
  const { min, max } = spec;
  const step = (spec.hi - spec.lo) / 100;
  const curve = (pt[2] as string | undefined) ?? "linear";
  const set = (to: Point) => {
    history.apply((d) => {
      const target = rowOf(d, row.id);
      if (!target) return;
      target.points = [...(target.points ?? []).filter((q) => q[0] !== beat && q[0] !== to[0]), to]
        .sort((a, b) => a[0] - b[0]);
    });
    onSelect(pointId(row.id, to[0]));
  };
  const withCurve = (b: number, v: PointValue, c: string): Point =>
    (c === "linear" ? [b, v] : [b, v, c]);
  const role = typeof pt[1] === "string" && pt[1].startsWith("@") ? pt[1].slice(1) : null;
  return (
    <footer className="d-inspector" aria-label="inspector">
      <div className="d-insp-head">
        <b>{laneTitle(row.target ?? "", spec)}</b>
        <span className="muted"> · automation point on {row.id} · bar {barBeat(beat)}</span>
        <span className="grow" />
        <button onClick={() => {
          history.apply((d) => {
            const target = rowOf(d, row.id);
            if (target) target.points = (target.points ?? []).filter((q) => q[0] !== beat);
          });
          onSelect(null);
        }}>Delete</button>
      </div>
      <div className="d-insp-grid">
        <label className="small">Beat{" "}
          <input type="number" step={1} value={beat} aria-label="point beat" style={{ width: 70 }}
                 onChange={(e) => {
                   if (e.target.value === "") return;
                   set(withCurve(Number(e.target.value), pt[1], curve));
                 }} />
        </label>
        {spec.kind === "color" ? (
          <div>
            <span className="small muted">Colour{" "}
              <span className="mono">{Array.isArray(pt[1]) ? `[${pt[1].join(", ")}]` : String(pt[1])}</span></span>
            <div className="d-chips" role="group" aria-label="point colour">
              {ROLES.map((r) => (
                <button key={r} className={role === r ? "on" : ""}
                        onClick={() => set(withCurve(beat, `@${r}`, curve))}>{r}</button>))}
              <input type="color" aria-label="point direct colour"
                     value={typeof pt[1] === "string" && pt[1].startsWith("#") ? pt[1] : "#ffffff"}
                     onChange={(e) => set(withCurve(beat, e.target.value, curve))} />
            </div>
          </div>
        ) : typeof pt[1] === "number" ? (
          <label className="small">Value{" "}
            <input type="number" min={min} max={max} step={step} value={pt[1]}
                   aria-label="point value" style={{ width: 70 }}
                   onChange={(e) => {
                     const v = Number(e.target.value);
                     if (e.target.value !== "" && (min === undefined || v >= min)
                         && (max === undefined || v <= max)) set(withCurve(beat, v, curve));
                   }} />
            <span className="muted"> {min ?? "any"} to {max ?? "any"}{spec.unit ? ` ${spec.unit}` : ""}</span>
          </label>
        ) : (
          <label className="small">Value{" "}
            <input value={String(pt[1])} aria-label="point value"
                   onChange={(e) => set(withCurve(beat, e.target.value, curve))} />
          </label>
        )}
        <div>
          <span className="small muted">Arrives by</span>
          <div className="d-chips">
            {CURVES.map((c) => (
              <button key={c} className={curve === c ? "on" : ""}
                      title={c === "step" ? "holds the value before until this point"
                        : c === "ease" ? "eases out of the point before and into this one"
                          : "a straight line from the point before"}
                      onClick={() => set(withCurve(beat, pt[1], c))}>{c}</button>))}
          </div>
        </div>
      </div>
    </footer>
  );
}

/**
 * A lane's wave: its shape, its cycle in bars, how far it swings, where in its
 * cycle it starts. It rides on the points -- they are where the lane rests,
 * the wave lifts it by up to `depth` (lowers it, negative) -- so the swing is
 * checked against the lane's range at every point, as the engine checks it.
 */
function WaveInspector({ row, history, onSelect }: {
  row: Row; history: Edits; onSelect: (id: string | null) => void;
}) {
  const spec = useLaneSpec(row);
  const wave = row.wave;
  if (!wave) return null;
  const set = (patch: Partial<WaveSpec>) => history.apply((d) => {
    const r = rowOf(d, row.id);
    if (!r?.wave) return;
    const next: WaveSpec = { ...r.wave, ...patch };
    for (const k of Object.keys(next) as (keyof WaveSpec)[]) {
      if (next[k] === undefined) delete next[k];
    }
    r.wave = next;
  });
  const num = (label: string, key: "bars" | "depth" | "phase" | "seed", step: number,
               lo?: number, hi?: number) => (
    <label className="small">{label}{" "}
      <input type="number" step={step} min={lo} max={hi} value={wave[key] ?? ""}
             aria-label={`wave ${key}`} style={{ width: 64 }}
             onChange={(e) => {
               if (e.target.value === "") return;
               const v = Number(e.target.value);
               if ((lo === undefined || v >= lo) && (hi === undefined || v <= hi)) set({ [key]: v });
             }} />
    </label>
  );
  // Where the swing would go past what the lane accepts: said here, before the
  // engine refuses the draft, with the point that does it.
  let over: string | null = null;
  if (spec.kind === "number" && typeof wave.depth === "number") {
    for (const p of row.points ?? []) {
      if (typeof p[1] !== "number") continue;
      const reach = p[1] + wave.depth;
      if ((spec.max !== undefined && reach > spec.max) || (spec.min !== undefined && reach < spec.min)) {
        over = `At bar ${barBeat(p[0])} it reaches ${Math.round(reach * 100) / 100}, `
          + `outside ${spec.min ?? "any"} to ${spec.max ?? "any"}.`;
        break;
      }
    }
  }
  const towardRole = typeof wave.toward === "string" && wave.toward.startsWith("@")
    ? wave.toward.slice(1) : null;
  return (
    <footer className="d-inspector" aria-label="inspector">
      <div className="d-insp-head">
        <b>{laneTitle(row.target ?? "", spec)}</b>
        <span className="muted"> · wave on {row.id}</span>
        <span className="grow" />
        <button onClick={() => {
          history.apply((d) => { const r = rowOf(d, row.id); if (r) delete r.wave; });
          onSelect(null);
        }}>Remove wave</button>
      </div>
      <div className="d-insp-grid">
        <div>
          <span className="small muted">Shape</span>
          <div className="d-chips" role="group" aria-label="wave shape">
            {WAVE_SHAPES.map((s) => (
              <button key={s} className={wave.shape === s ? "on" : ""}
                      onClick={() => set({ shape: s })}>{s}</button>))}
          </div>
        </div>
        {num("Cycle (bars)", "bars", 0.25, 0.25, 256)}
        {spec.kind === "color" ? (
          <div>
            <span className="small muted">Toward</span>
            <div className="d-chips" role="group" aria-label="wave toward">
              {ROLES.map((r) => (
                <button key={r} className={towardRole === r ? "on" : ""}
                        onClick={() => set({ toward: `@${r}` })}>{r}</button>))}
              <input type="color" aria-label="wave toward direct colour"
                     value={typeof wave.toward === "string" && wave.toward.startsWith("#")
                       ? wave.toward : "#ffffff"}
                     onChange={(e) => set({ toward: e.target.value })} />
            </div>
            {num("How far (0-1)", "depth", 0.05, 0, 1)}
          </div>
        ) : (
          <div>
            {num(`Depth${spec.unit ? ` (${spec.unit})` : ""}`, "depth", (spec.hi - spec.lo) / 100)}
            <div className="small muted">Above the points; negative swings below.</div>
          </div>
        )}
        {num("Starts at (cycles)", "phase", 0.05, 0, 1)}
        {wave.shape === "hold" && num("Seed", "seed", 1)}
        {over && <div className="small d-error" role="alert">{over}</div>}
      </div>
    </footer>
  );
}

function GapToggle({ row, history }: { row: Row; history: Edits }) {
  const owns = row.gap === "exclusive";
  return (
    <button className={`small d-gap${owns ? " on" : ""}`}
            title={owns ? "This lane owns the track: in its gaps nothing drives it"
              : "Template fills gaps: the lanes below and the template show through"}
            onClick={() => history.apply((d) => {
              const r = rowOf(d, row.id);
              if (r) r.gap = owns ? "fill" : "exclusive";
            })}>
      {owns ? "owns track" : "fills gaps"}
    </button>
  );
}

function LaneMenu({ row, index, history }: { row: Row; index: number; history: Edits }) {
  const move = (delta: number) => history.apply((d) => {
    const i = d.rows.findIndex((r) => r.id === row.id);
    const j = i + delta;
    if (i < 0 || j < 0 || j >= d.rows.length) return;
    const [r] = d.rows.splice(i, 1);
    d.rows.splice(j, 0, r!);
  });
  return (
    <span className="d-lane-menu">
      {index >= 0 && <>
        <button className="small" aria-label={`move ${row.id} up`} onClick={() => move(-1)}>↑</button>
        <button className="small" aria-label={`move ${row.id} down`} onClick={() => move(1)}>↓</button>
      </>}
      <button className="small" aria-label={`remove lane ${row.id}`}
              onClick={() => history.apply((d) => { d.rows = d.rows.filter((r) => r.id !== row.id); })}>
        ×</button>
    </span>
  );
}

const NEW_LANES: [string, string][] = [
  ["scene", "Scene"], ["movement", "Movement"], ["color", "Colour"], ["level", "Level"],
  ["palette", "Palette"], ["hits", "Hits"], ["osc", "OSC cues"], ["osc-curve", "OSC curve"],
  ["midi", "MIDI cues"], ["midi-curve", "MIDI curve"], ["visuals", "Visuals"],
];

/** A new lane for another output (milestone 3), or undefined for a lights lane. */
export function externalRow(d: RowsDoc, kind: string): Row | undefined {
  if (kind === "osc") return { id: uniqueId(d, "osc"), type: "external", output: "osc", items: [] };
  if (kind === "osc-curve") {
    return { id: uniqueId(d, "osc-curve"), type: "external", output: "osc",
             address: "/composition/layers/1/video/opacity", args: ["$value"],
             points: [[0, 1]] };
  }
  if (kind === "midi") return { id: uniqueId(d, "midi"), type: "external", output: "midi", items: [] };
  if (kind === "visuals") {
    return { id: uniqueId(d, "visuals"), type: "external", output: "visuals", items: [] };
  }
  if (kind === "midi-curve") {
    return { id: uniqueId(d, "midi-curve"), type: "external", output: "midi",
             channel: 1, cc: 1, points: [[0, 0]] };
  }
  return undefined;
}

/** A lane's name as its head, its menu entry and the inspector show it: a
 *  parameter as `$name` with its unit, the way a block argument refers to it. */
export function laneTitle(target: string, spec: LaneSpec): string {
  const unit = spec.unit ? ` (${spec.unit})` : "";
  if (target.startsWith(PARAM_TARGET)) return `$${spec.label}${unit}`;
  if (target.startsWith(ARG_TARGET)) return `${spec.label}${unit}`;
  return spec.label;
}

/** A new automation lane for `target`, starting where it already is: a macro
 *  at its neutral value, a parameter at its default -- so adding the lane
 *  changes nothing until a point is drawn. */
export function automationRow(d: RowsDoc, target: string, params: LaneSpecs = {}): Row {
  return { id: uniqueId(d, target), type: "automation", target,
           points: [[0, laneSpec(target, params).start]] };
}

/**
 * "+ automation": a lane for each macro and each open parameter not already
 * automated here. Parameters are listed by name and unit, and in a track's
 * timeline with the routines on it that have them, since one lane drives the
 * parameter on all of them.
 */
export function AutomationMenu({ history, params }: {
  history: Edits & { doc: RowsDoc | null }; params: LaneSpecs;
}) {
  const doc = history.doc;
  if (!doc) return null;
  const automated = new Set(doc.rows.filter((r) => r.type === "automation").map((r) => r.target));
  const macros = Object.keys(AUTOMATION_RANGES).filter((t) => !automated.has(t));
  const free = Object.entries(params).filter(([t]) => !automated.has(t));
  const open = free.filter(([t]) => t.startsWith(PARAM_TARGET))
    .sort(([a], [b]) => a.localeCompare(b));
  const args = free.filter(([t]) => t.startsWith(ARG_TARGET));
  return (
    <select aria-label="add automation" value=""
            onChange={(e) => {
              const target = e.target.value;
              if (target) history.apply((d) => { d.rows.push(automationRow(d, target, params)); });
            }}>
      <option value="">+ automation</option>
      <optgroup label="Macros">
        {macros.map((t) => <option key={t} value={t}>{t}</option>)}
      </optgroup>
      {open.length > 0 && (
        <optgroup label="Parameters">
          {open.map(([t, spec]) => (
            <option key={t} value={t}>
              {laneTitle(t, spec)}{spec.reaches ? ` · ${spec.reaches.join(", ")}` : ""}</option>))}
        </optgroup>
      )}
      {args.length > 0 && (
        <optgroup label="Block arguments">
          {args.map(([t, spec]) => <option key={t} value={t}>{laneTitle(t, spec)}</option>)}
        </optgroup>
      )}
    </select>
  );
}

function AddLane({ history }: { history: History }) {
  const params = useContext(ParamLanes);
  const doc = history.doc;
  if (!doc) return null;
  return (
    <div className="d-row d-add">
      <div className="d-head">
        <select aria-label="add lane" value=""
                onChange={(e) => {
                  const target = e.target.value;
                  if (!target) return;
                  history.apply((d) => {
                    const external = externalRow(d, target);
                    if (external) { d.rows.push(external); return; }
                    const id = uniqueId(d, target);
                    d.rows.push(target === "hits"
                      ? { id, type: "hits", items: [] }
                      : { id, type: "clips", target, gap: target === "palette" ? "exclusive" : "fill",
                          items: [] });
                  });
                }}>
          <option value="">+ lane</option>
          {NEW_LANES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <AutomationMenu history={history} params={params} />
      </div>
    </div>
  );
}

// -- the shelf ------------------------------------------------------------------

function Shelf({ history, routines, beat, track }: {
  history: History; routines: RoutineSummary[]; beat: number; track?: TrackDoc | null;
}) {
  const doc = history.doc;
  if (!doc) return null;
  // The routines to place are in the browser, on the left; this keeps what
  // works on the whole track: its palettes, then the tools that write lanes.
  void routines;
  return (
    <>
      <Palettes history={history} />
      <Templates history={history} track={track ?? null} />
      <RecordPads history={history} beat={beat} />
    </>
  );
}

/** Draft from template: a template set's phrase -> routine mapping, laid onto
 *  the scene lane from rekordbox's phrases, as an editable starting point. */
function Templates({ history, track }: { history: History; track: TrackDoc | null }) {
  const [sets, setSets] = useState<{ id: string; name?: string }[]>([]);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    apiFetch<{ templates: { id: string; name?: string }[] }>("/api/templates")
      .then((r) => setSets(r.templates)).catch(() => setSets([]));
  }, []);
  if (!sets.length) return null;
  const draft = async (id: string) => {
    setError(null);
    try {
      const { doc: ts } = await apiFetch<{ doc: TemplateSetDoc }>(`/api/templates/${id}`);
      if (!track) return;
      // Checked on a copy first: an edit that only says "no phrases" would
      // still be a step on the undo stack.
      const problem = draftFromTemplate(structuredClone(history.doc!), track, ts);
      if (problem) { setError(problem); return; }
      history.apply((d) => { draftFromTemplate(d, track, ts); });
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <SideSection id="timeline-draft" title="Draft from template" help={<>
      <p>Fills the scene lane with one routine per rekordbox phrase. It looks
        for the exact label (Verse 2), then the family (Verse), then{" "}
        <code>*</code>. If the set has palettes, it fills the palette lane
        too, and if its picks have visuals, the visuals lane.</p>
      <p>It replaces what's there, but it's only a starting point. Undo puts the
        lanes back.</p>
    </>}>
      <div className="d-templates">
        {sets.map((s) => (
          <button key={s.id} onClick={() => void draft(s.id)}
                  title="Replace the scene lane with this template's routines, phrase by phrase">
            {s.name ?? s.id}</button>))}
      </div>
      {error && <p className="d-error small">{error}</p>}
    </SideSection>
  );
}

/** Record pads: tap along while the track plays, and each tap lands as an
 *  item at the snapped playhead. */
function RecordPads({ history, beat }: { history: History; beat: number }) {
  const add = (hit: HitKind) => history.apply((d) => {
    let lane = d.rows.find((r) => r.type === "hits");
    if (!lane) {
      lane = { id: uniqueId(d, "hits"), type: "hits", items: [] };
      d.rows.push(lane);
    }
    const at = Math.max(0, Math.round(beat));
    (lane.items ??= []).push({ id: uniqueId(d, `${hit}-${at}`), at, ...newHit(hit) });
  });
  const nextScene = () => history.apply((d) => {
    const lane = d.rows.find((r) => r.type === "clips" && r.target === "scene");
    const at = history.snapBeat(beat);
    const it = lane?.items?.find((i) => i.at < at && at < i.at + i.len);
    if (!lane || !it) return;
    const tail: Item = { ...structuredClone(it), id: uniqueId(d, it.id), at,
                         len: it.at + it.len - at };
    it.len = at - it.at;
    lane.items!.push(tail);
  });
  return (
    <SideSection id="timeline-record" title="Record pads" topic="Record" help={<>
      <p>Arm it and play the track. Each pad lands at the playhead:{" "}
        <b>Flash</b>, <b>Strobe</b> and <b>Blackout</b> as hits, and{" "}
        <b>Next scene</b> splits the scene clip there.</p>
    </>} aside={
      <button className={`small${history.recording ? " on" : ""}`}
              onClick={() => history.setRecording(!history.recording)}>
        {history.recording ? "● Recording" : "Record"}</button>
    }>
      {history.recording ? (
        <div className="d-pads">
          <button onClick={() => add("flash")}>Flash</button>
          <button onClick={() => add("strobe")}>Strobe</button>
          <button onClick={() => add("blackout")}>Blackout</button>
          <button onClick={nextScene}>Next scene</button>
        </div>
      ) : <span className="small muted">Tap hits in while the track plays.</span>}
    </SideSection>
  );
}

/** Where a document's palette came from, said beside it: a copy of the
 *  library's (and whether it still matches), or one that lives only here.
 *  Editing it here never changes the library -- this says so, in place. */
export function PaletteOrigin({ library, name, colours, here, onUseLibrary }: {
  library: PaletteSummary[]; name: string; colours: Record<string, unknown>;
  /** "track" or "set": whose copy this is. */
  here: string;
  onUseLibrary: (colours: { primary: string; secondary: string; accent: string }) => void;
}) {
  const lib = library.find((p) => p.name === name);
  if (!lib) {
    return <span className="d-origin" title={`Made in this ${here}: no library palette is called ${name}`}>
      only here</span>;
  }
  const same = ROLES.every((r) => hexColor(colours[r]) != null
    && hexColor(colours[r]) === hexColor(lib[r]));
  const tip = `A copy of the library's ${name}. Changing it here changes this ${here} only; `
    + "the library's is changed on Studio's Palettes page.";
  return same ? <span className="d-origin lib" title={tip}>copy of library</span> : (
    <span className="d-origin differs" title={tip}>differs from library
      <button className="small" aria-label={`use the library's colours for ${name}`}
              onClick={() => onUseLibrary({ primary: lib.primary, secondary: lib.secondary,
                                            accent: lib.accent })}>use library's</button>
    </span>
  );
}

function Palettes({ history }: { history: History }) {
  const doc = history.doc;
  // The show's library, to copy a palette in from.
  const [library, setLibrary] = useState<PaletteSummary[]>([]);
  useEffect(() => {
    apiFetch<{ palettes: PaletteSummary[] }>("/api/palettes")
      .then((r) => setLibrary(r.palettes)).catch(() => setLibrary([]));
  }, []);
  if (!doc) return null;
  const palettes = doc.palettes ?? {};
  const hex = (v: unknown) => (typeof v === "string" && v.startsWith("#") ? v : "#ffffff");
  return (
    <SideSection id="timeline-palettes" title="This track's palettes" topic="Palettes"
                 count={Object.keys(palettes).length} help={<>
      <p>Each palette has three colours: primary, secondary and accent.
        Routines use these roles instead of fixed colours, so they change with
        the palette.</p>
      <p>The selected palette plays wherever the palette lane is empty. A
        palette clip switches it for its length.</p>
      <p>These are this track's own copies. Changing a colour here changes
        this track only; the library's palettes are on Studio's Palettes
        page, which can bring copies up to date.</p>
    </>} aside={<a className="small d-link" href="#studio/palettes"
                   title="The show's palette library: changing a colour here changes this track only">
      Library</a>}>
      {Object.entries(palettes).map(([name, pal]) => (
        <div key={name} className="d-palette">
          <label className="small">
            <input type="radio" name="default-palette" checked={doc.palette === name}
                   onChange={() => history.apply((d) => { d.palette = name; })} />
            {name}
          </label>
          <PaletteOrigin library={library} name={name} colours={pal as Record<string, unknown>}
                         here="track"
                         onUseLibrary={(c) => history.apply((d) => { d.palettes![name] = c; })} />
          {ROLES.map((role) => (
            <input key={role} type="color" aria-label={`${name} ${role}`} value={hex(pal[role])}
                   onChange={(e) => {
                     const value = e.target.value;
                     history.apply((d) => { d.palettes![name]![role] = value; });
                   }} />
          ))}
        </div>
      ))}
      <div className="d-form">
        <FromLibrary library={library} has={Object.keys(palettes)}
                     onPick={(name, colours) => history.apply((d) => {
                       d.palettes = { ...(d.palettes ?? {}), [name]: colours };
                       if (!d.palette) d.palette = name;
                     })} />
        <button className="small" onClick={() => history.apply((d) => {
          const name = `Palette ${Object.keys(d.palettes ?? {}).length + 1}`;
          d.palettes = { ...(d.palettes ?? {}),
                         [name]: { ...NEW_COLOURS } };
          if (!d.palette) d.palette = name;
        })}>+ palette</button>
      </div>
    </SideSection>
  );
}

/** A select of library palettes, for an editor that keeps its own copies: the
 *  picked one is copied in under its library name. */
export function FromLibrary({ library, has, onPick }: {
  library: PaletteSummary[]; has: string[];
  onPick: (name: string, colours: { primary: string; secondary: string; accent: string }) => void;
}) {
  const offer = library.filter((p) => !has.includes(p.name));
  if (!library.length) return null;
  return (
    <select value="" aria-label="add a palette from the library" disabled={!offer.length}
            onChange={(e) => {
              const p = library.find((x) => x.id === e.target.value);
              if (p) onPick(p.name, { primary: p.primary, secondary: p.secondary, accent: p.accent });
            }}>
      <option value="">{offer.length ? "+ From the library…" : "Every library palette is here"}</option>
      {offer.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
    </select>
  );
}

// -- the inspector ----------------------------------------------------------------

function Inspector({ history, item, routines, engine, onDeleted, beat, onSelect }: {
  history: History; item: { row: Row; item: Item } | null; routines: RoutineSummary[];
  engine: Engine; onDeleted: () => void;
  /** The playhead, for Split, and how to select what an operation made. */
  beat?: number; onSelect?: (id: string | null) => void;
}) {
  if (history.listView) return <EventList history={history} />;
  if (!item) {
    return (
      <footer className="d-inspector muted small">
        Select a clip, hit or point to edit it. Drag a clip to move it; drag its
        right edge to change its length. Positions snap to the {history.snap}.
      </footer>
    );
  }
  const { row, item: it } = item;
  const set = (fields: Partial<Item>) => history.apply((d) => {
    const target = itemOf(d, it.id);
    if (target) Object.assign(target, fields);
  });
  const routine = it.kind === "routine" ? routines.find((r) => r.id === it.routine) : undefined;
  const rigBound = it.kind === "look" || it.kind === "snapshot" || !!routine?.rig;
  const looks = offeredLooks(engine.state?.looks).map((l) => l.name);
  // A hit's "role" is read as a role's tag is: any tag, or one fixture's name.
  const hitTargets = [...new Set([...rigTags(engine.state),
                                  ...(engine.state?.fixtures ?? []).map((f) => f.name)])];
  const palettes = Object.keys(history.doc?.palettes ?? {});

  return (
    <footer className="d-inspector" aria-label="inspector">
      <div className="d-insp-head">
        <b>{itemName(it)}</b>
        <span className="muted"> · {it.hit ? "hit" : it.kind ?? "item"} on {row.id}</span>
        {rigBound && <span className="d-badge">this rig only</span>}
        <span className="muted mono"> · bar {barBeat(it.at)} → {barBeat(it.at + it.len)}
          {" "}({it.len} beats)</span>
        <span className="grow" />
        <span className="d-clip-tools" role="group" aria-label="clip">
          <button title="Ctrl+C" onClick={() => clipOps.copy(history, it.id, "timeline")}>Copy</button>
          <button title="Ctrl+X" onClick={() => { clipOps.cut(history, it.id, "timeline"); onDeleted(); }}>
            Cut</button>
          <button title="Ctrl+D: a copy straight after it"
                  onClick={() => onSelect?.(clipOps.duplicate(history, it.id, "timeline"))}>
            Duplicate</button>
          <button title="S: in two, at the playhead"
                  disabled={beat == null || !clipOps.inside(history.doc, it.id, history.snapBeat(beat))}
                  onClick={() => onSelect?.(clipOps.split(history, it.id, history.snapBeat(beat ?? 0)))}>
            Split</button>
        </span>
        <button onClick={() => {
          history.apply((d) => {
            for (const r of d.rows) if (r.items) r.items = r.items.filter((i) => i.id !== it.id);
          });
          onDeleted();
        }}>Delete</button>
      </div>
      <div className="d-insp-grid">
        {row.type === "external" && row.output === "osc" && (
          <OscCue item={it} set={set} />
        )}
        {row.type === "external" && row.output === "midi" && (
          <MidiCue item={it} set={set} />
        )}
        {row.type === "external" && (row.output === "visuals" || row.output === "vj") && (
          <VisualCue item={it} set={set} />
        )}
        {!it.hit && row.type !== "external" && (
          <div>
            <span className="small muted">Fade in</span>
            <div className="d-chips">
              {FADES.filter((f) => f <= it.len).map((f) => (
                <button key={f} className={(it.fade ?? 0) === f ? "on" : ""}
                        onClick={() => set({ fade: f })}>{f ? `${f}` : "cut"}</button>))}
            </div>
          </div>
        )}
        {it.kind === "routine" && (
          <>
            <label className="small">Routine{" "}
              <select value={it.routine} aria-label="routine"
                      onChange={(e) => set({ routine: e.target.value, variation: undefined,
                                             params: {}, bind: undefined })}>
                {routines.map((r) => <option key={r.id} value={r.id}>{r.name ?? r.id}</option>)}
              </select>
            </label>
            {routine && routine.variations.length > 0 && (
              <div>
                <span className="small muted">Variation</span>
                <div className="d-chips">
                  <button className={!it.variation ? "on" : ""}
                          onClick={() => set({ variation: undefined })}>default</button>
                  {routine.variations.map((v) => (
                    <button key={v} className={it.variation === v ? "on" : ""}
                            onClick={() => set({ variation: v })}>{v}</button>))}
                </div>
              </div>
            )}
            {routine && Object.entries(routine.params).map(([name, p]) => (
              <Param key={name} name={name} param={p} value={it.params?.[name]}
                     onChange={(v) => {
                       const params = { ...(it.params ?? {}) };
                       if (v === undefined) delete params[name]; else params[name] = v;
                       set({ params });
                     }} />
            ))}
            {routine && (
              <RoleBinds label={it.id} roles={routine.roles} bind={it.bind} state={engine.state}
                         onChange={(bind) => set({ bind })} />)}
            {routine && (
              <a className="small d-link" href={`#studio/routine/${routine.id}`}
                 onClick={() => rememberBack()}>
                Open routine · {routine.bars} bars{routine.loop ? ", loops" : ""}</a>)}
          </>
        )}
        {it.kind === "look" && (
          <>
            <label className="small">Look{" "}
              <input list="d-looks" value={it.look ?? ""} aria-label="look"
                     onChange={(e) => set({ look: e.target.value })} />
              <datalist id="d-looks">{looks.map((l) => <option key={l} value={l} />)}</datalist>
            </label>
            <LookGroups groups={it.groups} state={engine.state}
                        onChange={(groups) => set({ groups })} />
          </>
        )}
        {it.kind === "snapshot" && (
          <label className="small">Preset{" "}
            <select value={it.preset ?? ""} aria-label="preset"
                    onChange={(e) => set({ preset: e.target.value })}>
              <option value="">(none)</option>
              {engine.state?.presets.map((p) => <option key={p.name} value={p.name}>{p.name}</option>)}
            </select>
          </label>
        )}
        {it.kind === "palette" && (
          <label className="small">Palette{" "}
            <select value={it.palette ?? ""} aria-label="palette"
                    onChange={(e) => set({ palette: e.target.value })}>
              {palettes.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>
        )}
        {it.hit && (
          <>
            <div className="d-chips">
              {(["flash", "strobe", "blackout"] as const).map((h) => (
                <button key={h} className={it.hit === h ? "on" : ""}
                        onClick={() => set({ hit: h })}>{h}</button>))}
            </div>
            <label className="small">Level{" "}
              <input type="range" min={0} max={1} step={0.05} value={it.level ?? 1}
                     aria-label="hit level"
                     onChange={(e) => set({ level: Number(e.target.value) })} /></label>
            <label className="small">Who{" "}
              <select value={it.role ?? ""} aria-label="hit role"
                      onChange={(e) => set({ role: e.target.value || undefined })}>
                <option value="">everything</option>
                {it.role && !hitTargets.includes(it.role) && (
                  <option value={it.role}>{it.role} (not on this rig)</option>)}
                {hitTargets.map((g) => <option key={g} value={g}>{g}</option>)}
              </select>
            </label>
            {it.hit === "flash" && (
              <button className={it.envelope === "decay" ? "on" : ""}
                      onClick={() => set({ envelope: it.envelope === "decay" ? "hold" : "decay" })}>
                {it.envelope === "decay" ? "decays" : "holds"}</button>)}
          </>
        )}
      </div>
    </footer>
  );
}

/** Which fixtures a look clip plays on: every one the look covers, or only
 *  those with the tags picked (`groups`, read as the engine reads a role's
 *  tag: a tag, or one fixture's name). */
function LookGroups({ groups, state, onChange }: {
  groups: string[] | undefined; state: EngineState | null | undefined;
  onChange: (groups: string[] | undefined) => void;
}) {
  const tags = rigTags(state);
  const on = groups ?? [];
  const offered = [...tags, ...on.filter((g) => !tags.includes(g))];
  const toggle = (tag: string) => {
    const next = on.includes(tag) ? on.filter((g) => g !== tag) : [...on, tag];
    onChange(next.length ? next : undefined);
  };
  if (!offered.length) return null;
  return (
    <div>
      <span className="small muted">Only on</span>
      <div className="d-chips" role="group" aria-label="look plays on">
        <button className={on.length ? "" : "on"} onClick={() => onChange(undefined)}>
          all it covers</button>
        {offered.map((t) => (
          <button key={t} className={on.includes(t) ? "on" : ""} aria-pressed={on.includes(t)}
                  title={tagged(state, t).map((f) => f.name).join(", ") || "nothing on this rig"}
                  onClick={() => toggle(t)}>{t}</button>))}
        <EditTags />
      </div>
    </div>
  );
}

const OSC_WHEN: Record<"on" | "while" | "off", string> = {
  on: "When it starts", while: "While it plays (on change, 30/s at most)",
  off: "When it ends",
};

/** An OSC cue's three messages, for the track and routine inspectors. */
function OscCue({ item, set }: { item: Item; set: (fields: Partial<Item>) => void }) {
  return (
    <>
      {(["on", "while", "off"] as const).map((k) => (
        <OscField key={`${item.id}-${k}`} which={k} message={item[k]}
                  onChange={(m) => set({ [k]: m })} />))}
    </>
  );
}

const ROLE_COLORS = ["@primary", "@secondary", "@accent"];

/** A visuals cue: its scene, and that scene's parameters. A video's file is
 *  picked from the show folder's media/. */
function VisualCue({ item, set }: { item: Item; set: (fields: Partial<Item>) => void }) {
  const [media, setMedia] = useState<string[] | null>(null);
  const scene = item.scene ?? "wash";
  useEffect(() => {
    if (scene !== "video") return;
    let live = true;
    apiFetch<{ media: { file: string }[] }>("/api/media")
      .then((r) => { if (live) setMedia(r.media.map((m) => m.file)); })
      .catch(() => { if (live) setMedia([]); });
    return () => { live = false; };
  }, [scene]);
  const params = item.params ?? {};
  const setParam = (name: string, value: unknown) => {
    const next = { ...params };
    if (value === undefined || value === "") delete next[name]; else next[name] = value;
    set({ params: next });
  };
  const rules: Record<string, VisualRule> = { ...(VISUAL_PARAMS[scene] ?? {}), opacity: [0, 1] };
  return (
    <>
      <div className="d-chips" role="group" aria-label="visuals scene">
        {VISUAL_SCENES.map((s) => (
          <button key={s} className={scene === s ? "on" : ""}
                  onClick={() => set(newVisuals(s))}>
            {s}</button>))}
      </div>
      {Object.entries(rules).map(([name, rule]) => {
        const value = params[name];
        const label = `visuals ${name}`;
        if (rule === "color") {
          const role = typeof value === "string" && value.startsWith("@") ? value : "";
          return (
            <label key={name} className="small">{name}{" "}
              <select aria-label={label} value={role || (typeof value === "string" ? "hex" : "")}
                      onChange={(e) => setParam(name, e.target.value === "hex"
                        ? "#ffffff" : e.target.value || undefined)}>
                <option value="">(default)</option>
                {ROLE_COLORS.map((r) => <option key={r} value={r}>{r}</option>)}
                <option value="hex">a colour…</option>
              </select>
              {typeof value === "string" && value.startsWith("#") && (
                <input type="color" aria-label={`${label} colour`} value={value}
                       onChange={(e) => setParam(name, e.target.value)} />)}
            </label>
          );
        }
        if (rule === "file") {
          const files = media ?? [];
          return (
            <label key={name} className="small">file{" "}
              <select aria-label={label} value={typeof value === "string" ? value : ""}
                      onChange={(e) => setParam(name, e.target.value || undefined)}>
                <option value="">{media == null ? "…" : files.length ? "(pick one)" : "(media/ is empty)"}</option>
                {typeof value === "string" && value && !files.includes(value) && (
                  <option value={value}>{value} (not in media/)</option>)}
                {files.map((f) => <option key={f} value={f}>{f}</option>)}
              </select>
            </label>
          );
        }
        if (rule === "bool") {
          return (
            <label key={name} className="small">
              <input type="checkbox" aria-label={label} checked={value !== false}
                     onChange={(e) => setParam(name, e.target.checked)} /> {name}
            </label>
          );
        }
        if (typeof rule[0] === "string") {
          return (
            <label key={name} className="small">{name}{" "}
              <select aria-label={label} value={typeof value === "string" ? value : ""}
                      onChange={(e) => setParam(name, e.target.value || undefined)}>
                <option value="">(default)</option>
                {(rule as string[]).map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </label>
          );
        }
        const [lo, hi] = rule as [number, number];
        return (
          <label key={name} className="small">{name}{" "}
            <input type="number" className="d-num" min={lo} max={hi} step={hi <= 1 ? 0.05 : 1}
                   aria-label={label} placeholder="default"
                   value={typeof value === "number" ? value : ""}
                   onChange={(e) => setParam(name, e.target.value === ""
                     ? undefined : Number(e.target.value))} />
          </label>
        );
      })}
    </>
  );
}

const MIDI_KINDS = { note: "Note", cc: "CC", pc: "Program" } as const;

/** A MIDI cue: a note held for its length, a CC (and the value it leaves
 *  behind), or a program change -- on a channel. */
function MidiCue({ item, set }: { item: Item; set: (fields: Partial<Item>) => void }) {
  const kind = item.note != null ? "note" : item.cc != null ? "cc" : "pc";
  const num = (label: string, field: keyof Item, value: number | undefined,
               lo: number, hi: number, optional = false) => (
    <label className="small">{label}{" "}
      <input type="number" className="d-num" min={lo} max={hi} aria-label={`midi ${field}`}
             value={value ?? ""} placeholder={optional ? "none" : undefined}
             onChange={(e) => set({ [field]: e.target.value === "" && optional
               ? undefined : Number(e.target.value) })} />
    </label>
  );
  return (
    <>
      <div className="d-chips" role="group" aria-label="midi kind">
        {(Object.keys(MIDI_KINDS) as (keyof typeof MIDI_KINDS)[]).map((k) => (
          <button key={k} className={kind === k ? "on" : ""}
                  onClick={() => set({
                    note: k === "note" ? 60 : undefined, velocity: k === "note" ? 100 : undefined,
                    cc: k === "cc" ? 1 : undefined, value: k === "cc" ? 127 : undefined,
                    off_value: undefined, pc: k === "pc" ? 0 : undefined })}>
            {MIDI_KINDS[k]}</button>))}
      </div>
      {num("Channel", "channel", item.channel ?? 1, 1, 16)}
      {kind === "note" && <>{num("Note", "note", item.note, 0, 127)}
        {num("Velocity", "velocity", item.velocity ?? 100, 1, 127)}</>}
      {kind === "cc" && <>{num("CC", "cc", item.cc, 0, 127)}
        {num("Value", "value", item.value ?? 127, 0, 127)}
        {num("Then", "off_value", item.off_value, 0, 127, true)}</>}
      {kind === "pc" && num("Program", "pc", item.pc, 0, 127)}
    </>
  );
}

/** One OSC message of a cue: its address and arguments, or none. */
function OscField({ which, message, onChange }: {
  which: "on" | "while" | "off"; message?: OscMessage;
  onChange: (m: OscMessage | undefined) => void;
}) {
  if (!message) {
    return (
      <div>
        <span className="small muted">{OSC_WHEN[which]}</span>
        <div><button className="small" onClick={() => onChange({ address: "/", args: [] })}>
          + {which} message</button></div>
      </div>
    );
  }
  return (
    <div className="d-osc">
      <span className="small muted">{OSC_WHEN[which]}</span>
      <input className="mono" aria-label={`${which} address`} value={message.address}
             onChange={(e) => onChange({ ...message, address: e.target.value })} />
      <input className="mono" aria-label={`${which} args`}
             defaultValue={oscArgsText(message.args)}
             placeholder="1, $bar, $progress"
             onBlur={(e) => onChange({ ...message, args: parseOscArgs(e.target.value) })} />
      <button className="small" aria-label={`remove ${which} message`}
              onClick={() => onChange(undefined)}>×</button>
    </div>
  );
}

function Param({ name, param, value, onChange }: {
  name: string; param: { type: string; default?: unknown; min?: number; max?: number; unit?: string };
  value: unknown; onChange: (v: unknown) => void;
}) {
  const shown = value ?? param.default;
  if (param.type === "color") {
    const role = typeof shown === "string" && shown.startsWith("@") ? shown.slice(1) : null;
    return (
      <div>
        <span className="small muted">${name}: open parameter</span>
        <div className="d-chips">
          {ROLES.map((r) => (
            <button key={r} className={role === r ? "on" : ""}
                    onClick={() => onChange(`@${r}`)}>{r}</button>))}
          <input type="color" aria-label={`${name} direct colour`}
                 value={typeof shown === "string" && shown.startsWith("#") ? shown : "#ffffff"}
                 onChange={(e) => onChange(e.target.value)} />
          {value !== undefined && <button onClick={() => onChange(undefined)}>default</button>}
        </div>
      </div>
    );
  }
  if (param.type === "number" || param.type === "rate") {
    const lo = param.min ?? 0;
    const hi = param.max ?? (param.type === "rate" ? 8 : 100);
    const num = typeof shown === "number" ? shown : lo;
    return (
      <label className="small">${name} · {num}{param.unit === "deg" ? "°" : ""}{" "}
        <input type="range" min={lo} max={hi} step={param.type === "rate" ? 0.25 : 1}
               value={num} aria-label={`${name}`}
               onChange={(e) => onChange(Number(e.target.value))} />
      </label>
    );
  }
  return (
    <label className="small">${name}{" "}
      <input value={typeof shown === "string" ? shown : ""} aria-label={name}
             onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

/** The same document as a list: every item by position, with nudges. */
function EventList({ history }: { history: Edits & { doc: RowsDoc | null } }) {
  const doc = history.doc;
  if (!doc) return null;
  const rows = doc.rows.flatMap((r) => (r.items ?? []).map((it) => ({ r, it })))
    .sort((a, b) => a.it.at - b.it.at || a.r.id.localeCompare(b.r.id));
  const nudge = (id: string, beats: number) => history.apply((d) => {
    const it = itemOf(d, id);
    if (it) it.at = Math.max(-64, it.at + beats);
  });
  return (
    <footer className="d-inspector d-list" aria-label="event list">
      <table>
        <thead><tr><th>bar</th><th>lane</th><th>what</th><th>len</th><th>fade</th><th /></tr></thead>
        <tbody>
          {rows.map(({ r, it }) => (
            <tr key={it.id}>
              <td className="mono">{barBeat(it.at)}</td>
              <td>{r.id}</td>
              <td>{itemName(it)} <span className="muted small">{itemSub(it)}</span></td>
              <td className="mono">{it.len}</td>
              <td className="mono">{it.fade ?? 0}</td>
              <td>
                <button className="small" aria-label={`${it.id} back a bar`}
                        onClick={() => nudge(it.id, -BEATS_PER_BAR)}>−bar</button>
                <button className="small" aria-label={`${it.id} back a beat`}
                        onClick={() => nudge(it.id, -1)}>−1</button>
                <button className="small" aria-label={`${it.id} on a beat`}
                        onClick={() => nudge(it.id, 1)}>+1</button>
                <button className="small" aria-label={`${it.id} on a bar`}
                        onClick={() => nudge(it.id, BEATS_PER_BAR)}>+bar</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </footer>
  );
}

export const Editor = {
  Toolbar, LaneSvg, AutoSvg, GapToggle, LaneMenu, AddLane, Shelf, Inspector, EventList, Param,
  PointInspector, WaveInspector, uniqueId, OscCue, MidiCue, VisualCue,
};
