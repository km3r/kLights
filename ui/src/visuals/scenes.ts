/**
 * The built-in visuals' scenes (milestone 3): what a #visuals page draws, as
 * plain functions of the beat -- no state between frames, so a page opened
 * mid-song, or a jump in the track, draws exactly what continuous play would.
 *
 * Kept free of React so it can be tested directly, and so the chunk the phone
 * never loads stays small.
 */

/** In the chunk, so a test can check the phone's entry bundle never has it. */
export const VISUALS_CHUNK = "klights-visuals";

export const ROLES = ["primary", "secondary", "accent"] as const;

// -- time -----------------------------------------------------------------------

/** The show's beat as of a snapshot, and how fast it was running. */
export interface Anchor {
  beat: number;
  bpm: number;
  at: number;            // performance.now() when it was taken
}

/** After this long without a snapshot the page holds rather than running on:
 *  a dropped connection must not leave the screen racing ahead of the music. */
export const RUN_ON_S = 2;

/** The beat now, run on from the last snapshot at its tempo. */
export function beatNow(anchor: Anchor | null, nowMs: number): number | null {
  if (!anchor) return null;
  const s = Math.min(RUN_ON_S, Math.max(0, (nowMs - anchor.at) / 1000));
  return anchor.beat + (s * anchor.bpm) / 60;
}

/**
 * A new snapshot's beat. Close to where the page already was, it is eased in
 * -- the snapshot is a tenth of a second old and jitters, and correcting all
 * at once would judder every scene ten times a second (the same soft
 * correction the engine's clock makes). Far off -- a loop, a hot cue, a new
 * track -- it is taken at once.
 */
export function reanchor(old: Anchor | null, beat: number, bpm: number,
                         nowMs: number): Anchor {
  const predicted = beatNow(old, nowMs);
  if (predicted == null || Math.abs(beat - predicted) > 0.5) {
    return { beat, bpm, at: nowMs };
  }
  return { beat: predicted + (beat - predicted) * 0.3, bpm, at: nowMs };
}

// -- colour ---------------------------------------------------------------------

const HEX = /^#[0-9a-f]{6}$/i;

/** `@primary` and friends from the palette, `#rrggbb` as it is. */
export function resolveColor(value: unknown, palette: Record<string, string>,
                             fallback = "#ffffff"): string {
  if (typeof value === "string") {
    if (value.startsWith("@")) {
      const hex = palette[value.slice(1)];
      return hex && HEX.test(hex) ? hex : fallback;
    }
    if (HEX.test(value)) return value;
  }
  return fallback;
}

function rgba(hex: string, alpha: number): string {
  const n = parseInt(hex.slice(1), 16);
  const a = Math.max(0, Math.min(1, alpha));
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a.toFixed(3)})`;
}

// -- strobe ---------------------------------------------------------------------

export interface StrobePolicy { enabled: boolean; ceiling: number; max_seconds: number }

/** A whole screen flashing is the strongest trigger there is for
 *  photosensitive epilepsy, so the projector never goes past the broadcast
 *  limit -- three flashes in any second -- whatever the scene asks. */
export const MAX_FLASH_HZ = 3;
const DUTY = 0.2;

/** Flashes per beat: the scene's rate, halved until it fits under
 *  MAX_FLASH_HZ at this tempo, so it stays on the music. */
export function flashesPerBeat(rate: number, bpm: number): number {
  let perBeat = Math.max(0.25, rate);
  const cap = (MAX_FLASH_HZ * 60) / Math.max(bpm, 1);
  while (perBeat > cap && perBeat > 0.25) perBeat /= 2;
  return perBeat > cap ? 0 : perBeat;
}

/**
 * How bright the strobe scene is at `beat`, 0-1. It obeys the engine's strobe
 * policy as the lights do: nothing when strobe is off, never brighter than the
 * ceiling, and nothing once it has run `max_seconds` without a break.
 */
export function strobeLevel(beat: number, bpm: number, rate: number,
                            policy: StrobePolicy, onForS: number): number {
  if (!policy.enabled || bpm <= 0) return 0;
  if (policy.max_seconds > 0 && onForS >= policy.max_seconds) return 0;
  const perBeat = flashesPerBeat(rate, bpm);
  if (perBeat <= 0) return 0;
  const phase = ((beat * perBeat) % 1 + 1) % 1;
  return phase < DUTY ? Math.max(0, Math.min(1, policy.ceiling)) : 0;
}

// -- the scenes -------------------------------------------------------------------

export interface Paint {
  ctx: CanvasRenderingContext2D;
  w: number;
  h: number;
  beat: number;
  /** The show's tempo, for anything that flashes. */
  bpm: number;
  /** Beats into the item. */
  elapsed: number;
  params: Record<string, unknown>;
  palette: Record<string, string>;
  /** For the strobe scene: its level now, already policed. */
  strobe: number;
}

function num(params: Record<string, unknown>, name: string, fallback: number): number {
  const v = params[name];
  return typeof v === "number" && Number.isFinite(v) ? v : fallback;
}

/** A fixed pseudo-random number in 0-1 for each `i`: particles are where
 *  they are because of the beat, not because of the frames before. */
export function hash(i: number): number {
  const x = Math.sin(i * 12.9898 + 78.233) * 43758.5453;
  return x - Math.floor(x);
}

const frac = (x: number) => ((x % 1) + 1) % 1;

/** How far into its pulse a whole-screen wash is, 0-1: once a beat, or once
 *  every two or four at a tempo where a beat each would pass MAX_FLASH_HZ --
 *  a full-screen pulse is a flash like the strobe's. */
export function pulsePhase(beat: number, bpm: number): number {
  const perBeat = flashesPerBeat(1, bpm);
  return perBeat > 0 ? frac(beat * perBeat) : 0;
}

function wash(p: Paint) {
  const color = resolveColor(p.params.color ?? "@primary", p.palette);
  const pulse = num(p.params, "pulse", 0);
  p.ctx.fillStyle = rgba(color, num(p.params, "opacity", 1)
                                * (1 - pulse * pulsePhase(p.beat, p.bpm)));
  p.ctx.fillRect(0, 0, p.w, p.h);
}

function bars(p: Paint) {
  const color = resolveColor(p.params.color ?? "@primary", p.palette);
  const count = Math.max(1, Math.round(num(p.params, "count", 8)));
  const speed = num(p.params, "speed", 1);
  const width = p.w / count;
  p.ctx.fillStyle = rgba(color, num(p.params, "opacity", 1));
  for (let i = 0; i < count; i++) {
    const level = 0.5 + 0.5 * Math.sin(2 * Math.PI * (p.beat * speed / 4 + i / count));
    const height = p.h * (0.15 + 0.85 * level);
    p.ctx.fillRect(i * width + width * 0.1, p.h - height, width * 0.8, height);
  }
}

function tunnel(p: Paint) {
  const color = resolveColor(p.params.color ?? "@primary", p.palette);
  const speed = num(p.params, "speed", 1);
  const depth = Math.max(2, Math.round(num(p.params, "depth", 12)));
  const opacity = num(p.params, "opacity", 1);
  const cx = p.w / 2, cy = p.h / 2;
  p.ctx.lineWidth = Math.max(2, p.h / 120);
  for (let k = 0; k < depth; k++) {
    const t = frac((k + p.beat * speed) / depth);       // a ring a beat, at speed 1
    const scale = t * t;
    p.ctx.strokeStyle = rgba(color, opacity * t);
    p.ctx.strokeRect(cx - (p.w / 2) * scale, cy - (p.h / 2) * scale,
                     p.w * scale, p.h * scale);
  }
}

function particles(p: Paint) {
  const color = resolveColor(p.params.color ?? "@accent", p.palette);
  const count = Math.max(1, Math.round(num(p.params, "count", 300)));
  const burst = num(p.params, "burst", 0.5);
  const opacity = num(p.params, "opacity", 1);
  const cx = p.w / 2, cy = p.h / 2;
  const reach = Math.hypot(cx, cy);
  // Each beat throws them out from the middle; `burst` is how hard.
  const kick = 1 + burst * (1 - frac(p.beat)) * 0.6;
  for (let i = 0; i < count; i++) {
    const angle = hash(i) * Math.PI * 2;
    const speed = 0.3 + 0.7 * hash(i + 7919);
    const r = frac(p.beat * 0.25 * speed + hash(i + 104729)) * kick;
    const size = 1 + 3 * (1 - Math.min(1, r));
    p.ctx.fillStyle = rgba(color, opacity * (1 - Math.min(1, r)) + 0.15);
    p.ctx.fillRect(cx + Math.cos(angle) * r * reach, cy + Math.sin(angle) * r * reach,
                   size, size);
  }
}

function strobe(p: Paint) {
  if (p.strobe <= 0) return;
  const color = resolveColor(p.params.color ?? "#ffffff", p.palette);
  p.ctx.fillStyle = rgba(color, p.strobe * num(p.params, "opacity", 1));
  p.ctx.fillRect(0, 0, p.w, p.h);
}

/** Every scene drawn on the canvas. `video` is an element, not a painter. */
export const SCENES: Record<string, (p: Paint) => void> = {
  wash, bars, tunnel, particles, strobe,
};
