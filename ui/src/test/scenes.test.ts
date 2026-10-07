/**
 * The built-in visuals' scenes as plain functions (visuals/scenes.ts), held to
 * their promises across every input rather than at a few hand-picked ones.
 *
 * The one that matters most is a safety limit: a whole screen flashing is the
 * strongest trigger there is for photosensitive epilepsy, and the projector
 * must never pass three flashes in any second, whatever a scene asks. So the
 * flashes are COUNTED here -- the strobe sampled every millisecond, rising
 * edges counted in every one-second window -- across the tempos a DJ plays and
 * the rates a timeline can hold, rather than trusting the formula that is
 * meant to guarantee it.
 */
import { describe, expect, it } from "vitest";
import {
  MAX_FLASH_HZ, SCENES, beatNow, flashesPerBeat, hash, pulsePhase, reanchor, resolveColor,
  strobeLevel, type Paint,
} from "../visuals/scenes";

const ON = { enabled: true, ceiling: 1, max_seconds: 0 };   // no cut-off: worst case
const TEMPOS = [40, 60, 89, 90, 120, 128, 140, 174, 179.9, 180, 181, 200, 250, 300, 600];
const RATES = [0.25, 0.5, 1, 1.5, 2, 3, 4, 8, 16, 64, 1e6];

/** The most flashes in any one-second window over `seconds`, sampling `level`
 *  every millisecond. A flash is a rise from dark to lit. */
function worstFlashesPerSecond(level: (tMs: number) => number, seconds: number): number {
  const rises: number[] = [];
  let lit = false;
  for (let t = 0; t <= seconds * 1000; t++) {
    const now = level(t) > 0;
    if (now && !lit) rises.push(t);
    lit = now;
  }
  let worst = 0;
  for (let i = 0, j = 0; i < rises.length; i++) {
    while (rises[i]! - rises[j]! >= 1000) j++;
    worst = Math.max(worst, i - j + 1);
  }
  return worst;
}

describe("scenes: the flash limit, counted", () => {
  it("never puts more than three flashes in any second, at any tempo and rate", () => {
    const offenders: string[] = [];
    for (const bpm of TEMPOS) {
      for (const rate of RATES) {
        const worst = worstFlashesPerSecond(
          (t) => strobeLevel((t / 1000) * (bpm / 60), bpm, rate, ON, 0), 6);
        if (worst > MAX_FLASH_HZ) offenders.push(`${bpm} bpm x${rate}: ${worst}/s`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it("and the whole-screen wash pulse is held to the same limit", () => {
    const offenders: string[] = [];
    for (const bpm of TEMPOS) {
      // A pulse is a flash where its phase wraps from bright back to dim.
      const worst = worstFlashesPerSecond((t) => {
        const phase = pulsePhase((t / 1000) * (bpm / 60), bpm);
        return phase < 0.2 ? 1 : 0;
      }, 6);
      if (worst > MAX_FLASH_HZ) offenders.push(`${bpm} bpm: ${worst}/s`);
    }
    expect(offenders).toEqual([]);
  });

  it("stays on the music: the rate is the scene's, halved, never some other number", () => {
    for (const bpm of TEMPOS) {
      for (const rate of RATES) {
        const perBeat = flashesPerBeat(rate, bpm);
        if (perBeat === 0) continue;
        const halvings = Math.log2(Math.max(0.25, rate) / perBeat);
        expect(Number.isInteger(Math.round(halvings * 1e9) / 1e9), `${bpm}x${rate}`).toBe(true);
        expect(perBeat * bpm / 60).toBeLessThanOrEqual(MAX_FLASH_HZ);
      }
    }
  });

  it("flashes nothing, and always answers, for a rate or tempo that is not a number", () => {
    for (const [rate, bpm] of [[Infinity, 120], [NaN, 120], [1, NaN], [1, Infinity],
                               [-Infinity, 120], [-4, 120], [1, -120], [1, 0]]) {
      const perBeat = flashesPerBeat(rate!, bpm!);
      expect(Number.isFinite(perBeat), `${rate} @ ${bpm}`).toBe(true);
      if (Number.isFinite(bpm) && bpm! > 0) {
        expect(perBeat * bpm! / 60).toBeLessThanOrEqual(MAX_FLASH_HZ);
      }
      const level = strobeLevel(0.01, bpm!, rate!, ON, 0);
      expect(level >= 0 && level <= 1, `${rate} @ ${bpm}: ${level}`).toBe(true);
    }
    expect(strobeLevel(0.01, 120, Infinity, ON, 0)).toBe(0);
    expect(strobeLevel(0.01, NaN, 1, ON, 0)).toBe(0);
  });

  it("is never brighter than the ceiling, and dark once strobe has run its max_seconds", () => {
    for (const ceiling of [0, 0.3, 0.75, 1, 2, -1]) {
      for (let b = 0; b < 8; b += 0.01) {
        const level = strobeLevel(b, 128, 1, { enabled: true, ceiling, max_seconds: 8 }, 0);
        expect(level).toBeGreaterThanOrEqual(0);
        expect(level).toBeLessThanOrEqual(Math.max(0, Math.min(1, ceiling)));
      }
    }
    expect(strobeLevel(0.01, 128, 1, { enabled: true, ceiling: 1, max_seconds: 8 }, 7.99)).toBe(1);
    expect(strobeLevel(0.01, 128, 1, { enabled: true, ceiling: 1, max_seconds: 8 }, 8)).toBe(0);
    // max_seconds 0 is "no cap", not "always off".
    expect(strobeLevel(0.01, 128, 1, { enabled: true, ceiling: 1, max_seconds: 0 }, 1e6)).toBe(1);
  });
});

describe("scenes: time and color", () => {
  it("a page opened mid-song draws what continuous play would: beatNow is linear in time", () => {
    const a = { beat: 32, bpm: 128, at: 5000 };
    for (const ms of [0, 100, 250, 999, 1500, 2000]) {
      expect(beatNow(a, 5000 + ms)).toBeCloseTo(32 + (ms / 1000) * (128 / 60), 9);
    }
    expect(beatNow(a, 4000)).toBe(32);            // a clock behind the snapshot holds
    expect(beatNow(null, 0)).toBeNull();
  });

  it("re-anchoring repeatedly on a steady feed converges on it, and never overshoots", () => {
    let anchor = reanchor(null, 0, 120, 0);
    for (let ms = 100; ms <= 3000; ms += 100) {
      const truth = (ms / 1000) * 2 + 0.2;        // the feed is 0.2 beats ahead
      anchor = reanchor(anchor, truth, 120, ms);
      expect(anchor.beat).toBeLessThanOrEqual(truth + 1e-9);
    }
    expect(Math.abs(anchor.beat - (3 * 2 + 0.2))).toBeLessThan(0.01);
  });

  it("resolves only palette roles and #rrggbb; everything else is the fallback", () => {
    const palette = { primary: "#ABCDEF", secondary: "not a color", accent: "#fff" };
    expect(resolveColor("@primary", palette)).toBe("#ABCDEF");
    expect(resolveColor("@secondary", palette, "#000000")).toBe("#000000");
    expect(resolveColor("@accent", palette, "#000000")).toBe("#000000");   // 3 digits: no
    for (const junk of [null, undefined, 7, {}, [], "red", "#12345", "#1234567", "@"]) {
      expect(resolveColor(junk, palette, "#010203")).toBe("#010203");
    }
  });

  it("hash is a fixed number in 0..1 for each index: particles do not depend on frames", () => {
    for (let i = -50; i < 500; i++) {
      const h = hash(i);
      expect(h).toBeGreaterThanOrEqual(0);
      expect(h).toBeLessThan(1);
      expect(hash(i)).toBe(h);
    }
  });
});

/** A canvas that records each rectangle and its style. */
function canvas() {
  const rects: { op: "fill" | "stroke"; x: number; y: number; w: number; h: number; style: string }[] = [];
  const ctx = {
    fillStyle: "" as string, strokeStyle: "" as string, lineWidth: 1,
    fillRect(x: number, y: number, w: number, h: number) {
      rects.push({ op: "fill", x, y, w, h, style: ctx.fillStyle });
    },
    strokeRect(x: number, y: number, w: number, h: number) {
      rects.push({ op: "stroke", x, y, w, h, style: ctx.strokeStyle });
    },
  };
  return { ctx: ctx as unknown as CanvasRenderingContext2D, rects };
}

const PALETTE = { primary: "#ff0000", secondary: "#00ff00", accent: "#0000ff" };

function paint(scene: string, params: Record<string, unknown>, extra: Partial<Paint> = {}) {
  const { ctx, rects } = canvas();
  SCENES[scene]!({ ctx, w: 400, h: 200, beat: 17.3, bpm: 128, elapsed: 3, params,
                   palette: PALETTE, strobe: 0, ...extra });
  return rects;
}

const finite = (r: { x: number; y: number; w: number; h: number }) =>
  [r.x, r.y, r.w, r.h].every(Number.isFinite);
const styled = (r: { style: string }) => /^rgba\(\d+, \d+, \d+, (0|1|0\.\d+)(\.\d+)?\)$/.test(r.style);

describe("scenes: each painter", () => {
  it("wash fills the whole screen in its color", () => {
    const [r, ...rest] = paint("wash", { color: "@secondary", opacity: 0.5 });
    expect(rest).toEqual([]);
    expect(r).toMatchObject({ op: "fill", x: 0, y: 0, w: 400, h: 200, style: "rgba(0, 255, 0, 0.500)" });
  });

  it("bars draws one bar per count, inside the screen, rising from the floor", () => {
    const rects = paint("bars", { count: 5 });
    expect(rects).toHaveLength(5);
    for (const r of rects) {
      expect(r.x).toBeGreaterThanOrEqual(0);
      expect(r.x + r.w).toBeLessThanOrEqual(400);
      expect(r.y + r.h).toBeCloseTo(200);
      expect(r.h).toBeGreaterThan(0);
    }
  });

  it("tunnel draws its rings centred, none bigger than the screen", () => {
    const rects = paint("tunnel", { depth: 6, color: "@accent" });
    expect(rects).toHaveLength(6);
    for (const r of rects) {
      expect(r.op).toBe("stroke");
      expect(r.x + r.w / 2).toBeCloseTo(200);
      expect(r.w).toBeLessThanOrEqual(400);
    }
  });

  it("particles draws its count, the same picture for the same beat", () => {
    const a = paint("particles", { count: 40 });
    const b = paint("particles", { count: 40 });
    expect(a).toHaveLength(40);
    expect(a).toEqual(b);
  });

  it("strobe draws nothing while its policed level is zero, and a full screen when lit", () => {
    expect(paint("strobe", {}, { strobe: 0 })).toEqual([]);
    expect(paint("strobe", {}, { strobe: 0.6 })).toEqual([
      { op: "fill", x: 0, y: 0, w: 400, h: 200, style: "rgba(255, 255, 255, 0.600)" }]);
  });

  it("every painter, given hostile params, still draws finite shapes in valid colors", () => {
    const hostile = [{}, { count: NaN, depth: Infinity, speed: "fast", opacity: 7, color: "@nope" },
                     { count: -3, depth: -1, burst: NaN, opacity: -2, pulse: Infinity },
                     { count: 1e9 > 0 ? 3 : 0, color: 42, speed: -1e9 }];
    for (const scene of Object.keys(SCENES)) {
      for (const params of hostile) {
        const rects = paint(scene, params, { strobe: scene === "strobe" ? 1 : 0 });
        expect(rects.every(finite), `${scene} ${JSON.stringify(params)}`).toBe(true);
        expect(rects.every(styled), `${scene} ${JSON.stringify(params)}: ${rects[0]?.style}`).toBe(true);
      }
    }
  });
});
