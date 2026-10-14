import { useContext, useMemo, useState } from "react";
import { WAVE_SHAPES } from "../blocks";
import { AUTOMATION_RANGES, CURVES } from "../showfiles.generated";
import { rowOf } from "./clipboard";
import type { Edits, RowsDoc } from "./history";
import { ROLES } from "./items";
import { ARG_TARGET, LaneAudio, PARAM_TARGET, laneSpec, useLaneSpec } from "./laneSpecs";
import type { LaneSpec, LaneSpecs } from "./laneSpecs";
import { LANE_H } from "./laneSvg";
import {
  AUDIO_BANDS, AUDIO_STEP, BEATS_PER_BAR, barBeat, curveValue, laneValue, uniqueId, waveLevel,
} from "./model";
import type { AudioBand, AudioSpec, Point, PointValue, Row, WaveSpec } from "./model";

/**
 * An automation lane: its points, its wave and the band of the track's
 * audio it follows, drawn and edited, and the menu of what a lane can drive.
 */
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
 * accepts it as drawn. A color lane's swings all the way to the accent.
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

/** A lane's audio band's selection id: a row has at most one. */
export function audioId(rowId: string): string {
  return `audio:${rowId}`;
}

export function parseAudioId(id: string | null): string | null {
  return id?.startsWith("audio:") ? id.slice(6) : null;
}

/** How far a row's wave and audio band can lower its points, and lift them
 *  (`showfiles.swing`): each adds between nothing and its depth, so together
 *  they reach the sum of the depths on each side. */
export function swing(row: Pick<Row, "wave" | "audio">): [number, number] {
  let down = 0;
  let up = 0;
  for (const depth of [row.wave?.depth, row.audio?.depth]) {
    if (typeof depth !== "number") continue;
    down += Math.min(0, depth);
    up += Math.max(0, depth);
  }
  return [down, up];
}

/**
 * A new audio band for a lane, sized to stay in range as the engine checks it
 * -- its wave's swing counted too: half the lane's drawn range, upward if its
 * points leave more room above than below, else downward (a master resting at
 * full ducks on the kick). The bass, where the track's analysis has it, falling
 * away over half a beat.
 */
export function defaultAudio(row: Row, spec: LaneSpec,
                             bands: readonly string[] = AUDIO_BANDS): AudioSpec {
  const nums = (row.points ?? []).map((p) => p[1]).filter((v): v is number => typeof v === "number");
  const [waveDown, waveUp] = swing({ wave: row.wave });
  const up = (spec.max ?? spec.hi) - (Math.max(spec.lo, ...nums) + waveUp);
  const down = Math.min(spec.hi, ...nums) + waveDown - (spec.min ?? spec.lo);
  const half = (spec.hi - spec.lo) / 2;
  const depth = up >= down ? Math.min(up, half) : -Math.min(down, half);
  return { band: bands.includes("low") ? "low" : bands[0] ?? "all",
           depth: Math.round(Math.max(-half, depth) * 100) / 100, release: 0.5 };
}

/** A color value as CSS, or null for one only the show can resolve -- a
 *  palette role, a color look -- which the lane names instead of painting. */
export function cssColor(v: unknown): string | null {
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
 * A color parameter's lane: a band of what it is at each beat. A color is
 * not a height, so there is no curve -- each segment is painted from the point
 * it leaves to the point it arrives at (a step holds, then jumps), and a value
 * only the show can resolve, like "@primary", is written rather than painted.
 */
function ColorBand({ points, x, width, id }: {
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
        const ca = cssColor(s.a);
        const cb = cssColor(s.b);
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

/** A color lane's wave: the color it swings toward, laid over the band as
 *  strongly as the wave pulls at each beat. One it cannot paint (a palette
 *  role) is drawn in the band's own named-color grey. */
function ColorWave({ wave, x, width }: { wave: WaveSpec; x: (b: number) => number; width: number }) {
  const perBeat = x(1) - x(0);
  const end = width / perBeat;
  const step = Math.max(end / 2000, Math.min(0.5, (wave.bars * BEATS_PER_BAR) / 16));
  const fill = cssColor(wave.toward);
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
 * value -- as one edit when it is let go. A color lane's points move only
 * across: up and down means nothing for a color, which the inspector picks.
 */
export function AutoSvg({ row, x, width, history, selected, onSelect }: {
  row: Row; x: (b: number) => number; width: number; history: Edits;
  selected?: string | null; onSelect?: (id: string | null) => void;
}) {
  const spec = useLaneSpec(row);
  const { lo, hi } = spec;
  const color = spec.kind === "color";
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
  // The band the lane follows, as where it carries the lane to: from its
  // resting value (the points, and the wave) out to the loudest level under
  // each pixel. A cell is a thirty-second of a beat -- far finer than a pixel
  // -- so a line through every cell would be a smear; the reach is what reads.
  const audio = useContext(LaneAudio);
  const cells = !color && row.audio && audio ? audio.cells(row.audio) : null;
  const depth = row.audio?.depth ?? 0;
  const { wave } = row;
  const heard = useMemo(() => {
    if (!cells || !audio || !shown.length || !depth) return null;
    const resting = { id: "", type: "automation" as const, points: shown, wave };
    const step = Math.max(1, width / 6000);
    const at = (v: number) => LANE_H - 3
      - ((Math.max(lo, Math.min(hi, v)) - lo) / (hi - lo)) * (LANE_H - 6);
    const rest: string[] = [];
    const reach: string[] = [];
    for (let px = 0; px < width; px += step) {
      const from = Math.floor(px / perBeat / AUDIO_STEP) - audio.first;
      const to = Math.max(from + 1, Math.ceil((px + step) / perBeat / AUDIO_STEP) - audio.first);
      let level = 0;
      for (let j = Math.max(0, from); j < Math.min(cells.length, to); j++) {
        if (cells[j]! > level) level = cells[j]!;
      }
      const base = laneValue(resting, (px + step / 2) / perBeat) ?? lo;
      rest.push(`${px},${at(base)}`);
      reach.push(`${px},${at(base + depth * level)}`);
    }
    return { line: reach.join(" "), area: [...reach, ...rest.reverse()].join(" ") };
  }, [cells, audio, shown, wave, depth, width, perBeat, lo, hi]);
  const samples: string[] = [];
  if (shown.length && row.wave && !color) {
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
           // A color lane's new point starts as the color it lands on, so
           // adding one changes nothing until the inspector picks another.
           const value: PointValue = color
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
      {heard && (
        <g pointerEvents="none" aria-label="audio">
          <polygon points={heard.area} className="d-heard" />
          <polyline points={heard.line} className="d-heard-line" />
        </g>)}
      {color
        ? <ColorBand points={shown} x={x} width={width} id={row.id} />
        : <polyline points={samples.join(" ")} className="d-curve" pointerEvents="none" />}
      {color && row.wave && <ColorWave wave={row.wave} x={x} width={width} />}
      {shown.map((p, i) => {
        const id = pointId(row.id, drag && p[0] === drag.beat + drag.dBeat ? drag.beat : p[0]);
        return (
          <circle key={i} cx={x(p[0])} cy={typeof p[1] === "number" ? y(p[1]) : LANE_H / 2}
                  r={selected === id ? 6 : 4}
                  style={color ? { fill: cssColor(p[1]) ?? undefined } : undefined}
                  className={`d-point${color ? " d-point-color" : ""}${selected === id ? " sel" : ""}`}
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
export function PointInspector({ row, beat, history, onSelect }: {
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
            <span className="small muted">Color{" "}
              <span className="mono">{Array.isArray(pt[1]) ? `[${pt[1].join(", ")}]` : String(pt[1])}</span></span>
            <div className="d-chips" role="group" aria-label="point color">
              {ROLES.map((r) => (
                <button key={r} className={role === r ? "on" : ""}
                        onClick={() => set(withCurve(beat, `@${r}`, curve))}>{r}</button>))}
              <input type="color" aria-label="point direct color"
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
export function WaveInspector({ row, history, onSelect }: {
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
              <input type="color" aria-label="wave toward direct color"
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

const BAND_HINT: Record<AudioBand, string> = {
  low: "the kick and the bass", mid: "vocals, synths, snares",
  high: "hats and cymbals", all: "the track's overall level",
};

/**
 * The band of the track's audio a lane follows: which band, how far it carries
 * the lane, the part of the band's range it listens to, and how fast it lets
 * go. Like a wave it rides on the points -- they are where the lane rests --
 * so its reach is checked against the lane's range at every point, with the
 * wave's on top, as the engine checks it.
 */
export function AudioInspector({ row, history, onSelect }: {
  row: Row; history: Edits; onSelect: (id: string | null) => void;
}) {
  const spec = useLaneSpec(row);
  const track = useContext(LaneAudio);
  const audio = row.audio;
  if (!audio) return null;
  const set = (patch: Partial<AudioSpec>) => history.apply((d) => {
    const r = rowOf(d, row.id);
    if (r?.audio) r.audio = { ...r.audio, ...patch };
  });
  const floor = audio.floor ?? 0;
  const ceiling = audio.ceiling ?? 1;
  const num = (label: string, key: "depth" | "floor" | "ceiling" | "release", step: number,
               value: number | undefined, ok: (v: number) => boolean) => (
    <label className="small">{label}{" "}
      <input type="number" step={step} value={value ?? ""} aria-label={`audio ${key}`}
             style={{ width: 64 }}
             onChange={(e) => {
               if (e.target.value === "") return;
               const v = Number(e.target.value);
               if (Number.isFinite(v) && ok(v)) set({ [key]: v });
             }} />
    </label>
  );
  // Where the lane would go past what it accepts: said here, before the
  // engine refuses the draft, with the point that does it.
  let over: string | null = null;
  if (spec.kind === "number") {
    for (const p of row.points ?? []) {
      if (typeof p[1] !== "number") continue;
      const value = p[1];
      const reach = swing(row).map((by) => value + by)
        .find((v) => (spec.max !== undefined && v > spec.max) || (spec.min !== undefined && v < spec.min));
      if (reach !== undefined) {
        over = `At bar ${barBeat(p[0])} it reaches ${Math.round(reach * 100) / 100}`
          + `${row.wave ? " with the wave" : ""}, outside ${spec.min ?? "any"} to ${spec.max ?? "any"}.`;
        break;
      }
    }
  }
  const missing = track && !track.bands.includes(audio.band as AudioBand);
  return (
    <footer className="d-inspector" aria-label="inspector">
      <div className="d-insp-head">
        <b>{laneTitle(row.target ?? "", spec)}</b>
        <span className="muted"> · follows the audio on {row.id}</span>
        <span className="grow" />
        <button onClick={() => {
          history.apply((d) => { const r = rowOf(d, row.id); if (r) delete r.audio; });
          onSelect(null);
        }}>Stop following</button>
      </div>
      <div className="d-insp-grid">
        <div>
          <span className="small muted">Band</span>
          <div className="d-chips" role="group" aria-label="audio band">
            {AUDIO_BANDS.map((b) => (
              <button key={b} className={audio.band === b ? "on" : ""} title={BAND_HINT[b]}
                      disabled={!!track && !track.bands.includes(b)}
                      onClick={() => set({ band: b })}>{b}</button>))}
          </div>
          <div className="small muted">{BAND_HINT[audio.band as AudioBand] ?? ""}</div>
        </div>
        <div>
          {num(`Depth${spec.unit ? ` (${spec.unit})` : ""}`, "depth", (spec.hi - spec.lo) / 100,
               audio.depth, () => true)}
          <div className="small muted">At the band's loudest; negative goes below the points.</div>
        </div>
        <div>
          {num("Listens from", "floor", 0.05, floor, (v) => v >= 0 && v < ceiling)}{" "}
          {num("to", "ceiling", 0.05, ceiling, (v) => v <= 1 && v > floor)}
          <div className="small muted">
            Of the band's loudest in this track: quieter is silence, louder is all the way.</div>
        </div>
        <div>
          {num("Release (beats)", "release", 0.25, audio.release ?? 0, (v) => v >= 0 && v <= 64)}
          <div className="small muted">How long a full level takes to fall away. 0 follows every hit.</div>
        </div>
        {over && <div className="small d-error" role="alert">{over}</div>}
        {track === null && (
          <div className="small d-error" role="status">
            This track has no waveform to read its audio from, so the lane plays its points
            alone. Prep the track from rekordbox.</div>)}
        {missing && (
          <div className="small d-error" role="status">
            This track's analysis has only its overall level: follow "all", or analyse it in
            rekordbox with the color waveform and prep it again.</div>)}
        {track && !track.exact && !missing && audio.band !== "all" && (
          <div className="small muted" role="status">
            Estimated from the color waveform. Prep the track again to follow rekordbox's own
            three-band analysis.</div>)}
      </div>
    </footer>
  );
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
