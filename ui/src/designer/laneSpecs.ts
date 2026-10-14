import { createContext, useContext } from "react";
import { AUTOMATION_RANGES } from "../showfiles.generated";
import { BLOCK_ARGS } from "./model";
import type { Point, PointValue, Row, TrackAudio } from "./model";

/** The height every lane is drawn at, clips and automation alike. */
export const LANE_H = 40;

/**
 * What an automation lane drives, as a lane draws and edits it: the macros'
 * fixed ranges, a routine's open parameters, and the block arguments an `arg.`
 * lane reaches -- with the range the engine accepts and the one the lane draws.
 */
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
 * parameter. Offered for each number and color argument of each block item,
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
      // A name that is a color in one routine and a number in another: the
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

/** The audio a lane on this page can follow: the track's, laid on its beats;
 *  null for a track with no waveform to read it from; undefined where there
 *  is no track at all -- a routine plays on any -- so following is not offered. */
export const LaneAudio = createContext<TrackAudio | null | undefined>(undefined);

/** How to draw a lane, whatever it targets -- including a `param.<name>` no
 *  routine declares any more, drawn from its own points so it can still be
 *  seen, fixed, and removed. */
export function laneSpec(target: string, params: LaneSpecs, points: Point[] = []): LaneSpec {
  if (target in AUTOMATION_RANGES) return macroSpec(target);
  const known = params[target];
  if (known) return known;
  const nums = points.map((p) => p[1]).filter((v): v is number => typeof v === "number");
  const color = points.length > 0 && nums.length === 0;
  const lo = Math.min(0, ...nums);
  const hi = Math.max(lo + 1, ...nums);
  return { label: target.startsWith(PARAM_TARGET) ? target.slice(PARAM_TARGET.length) : target, unit: "",
           kind: color ? "color" : "number", lo, hi, start: color ? "@primary" : lo };
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
