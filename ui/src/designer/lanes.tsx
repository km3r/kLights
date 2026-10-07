import { useContext, useEffect, useRef, useState } from "react";
import {
  BEATS_PER_BAR, PHRASE_HUE, VISUAL_SCENES, barBeat, curveValue, laneValue, phraseFamily,
} from "./model";
import type { AudioBand, Grid, Item, Row, TrackDoc, Wave } from "./model";
import {
  Editor, LaneAudio, PickMenu, audioId, defaultAudio, defaultWave, gapStart, laneTitle,
  newVisuals, useLaneSpec, useMenuDismiss, waveId,
} from "./edit";
import type { Edits, PickEntry, Placeable } from "./edit";

/**
 * The designer's lanes and bands, shared by the track designer and the routine
 * editor: a routine is the same rows in its own beats, so it is edited with the
 * same lanes (F19l, in loop mode).
 */

// -- the bands ------------------------------------------------------------------

export function Ruler({ totalBeats, x, width, onSeek }: {
  totalBeats: number; x: (b: number) => number; width: number;
  onSeek: (beat: number) => void;
}) {
  const bars = Math.ceil(totalBeats / BEATS_PER_BAR);
  const every = x(BEATS_PER_BAR) < 18 ? 4 : 1;
  return (
    <div className="d-row d-ruler">
      <div className="d-head">Bar</div>
      <svg width={width} height={24} role="slider" aria-label="seek"
           aria-valuemin={0} aria-valuemax={totalBeats} aria-valuenow={0}
           onClick={(e) => {
             const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
             onSeek((e.clientX - r.left) / x(1));
           }}>
        {Array.from({ length: bars }, (_, i) => i).filter((i) => i % every === 0).map((i) => (
          <g key={i}>
            <line x1={x(i * 4)} x2={x(i * 4)} y1={14} y2={24} className="d-tick" />
            <text x={x(i * 4) + 2} y={12} className="d-label">{i + 1}</text>
          </g>
        ))}
      </svg>
    </div>
  );
}


export function Phrases({ track, x, width, picked, onPick }: {
  track: TrackDoc; x: (b: number) => number; width: number;
  /** The phrase selected as a section, by its start beat. */
  picked?: number | null;
  /** Select a phrase as a section (or let go of it, picked again). */
  onPick?: (start: number, end: number, label: string) => void;
}) {
  const items = track.phrases?.items ?? [];
  return (
    <div className="d-row d-phrases">
      <div className="d-head">Phrases <span className="muted small">rekordbox</span></div>
      <svg width={width} height={22} aria-label="phrases">
        {items.map(([start, end, label]) => {
          const family = phraseFamily(label);
          const on = picked === start;
          return (
            <g key={`${start}-${label}`} className={`d-phrase${onPick ? " pickable" : ""}${on ? " on" : ""}`}
               role={onPick ? "button" : undefined} tabIndex={onPick ? 0 : undefined}
               aria-pressed={onPick ? on : undefined}
               aria-label={onPick ? `select ${label}, bars ${barNo(start)} to ${barNo(end) - 1}` : undefined}
               onClick={() => onPick?.(start, end, label)}
               onKeyDown={(e) => {
                 // Enter only: Space is the transport's, here as everywhere.
                 if (onPick && e.key === "Enter") {
                   e.preventDefault();
                   onPick(start, end, label);
                 }
               }}>
              <rect x={x(start)} y={2} width={Math.max(1, x(end) - x(start) - 1)} height={18}
                    rx={3} fill={PHRASE_HUE[family] ?? "#475569"} opacity={on ? 0.9 : 0.55} />
              <text x={x(start) + 4} y={15} className="d-label">{label}</text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/** The bar a beat falls in, counting from 1. */
function barNo(beat: number): number { return Math.floor(beat / BEATS_PER_BAR) + 1; }

/** Browsers refuse a canvas wider than about 32k pixels -- a long track at the
 *  closest zoom is wider than that, and would draw nothing at all. Past this,
 *  the canvas keeps this many pixels and is stretched to the lane's width. */
export const MAX_CANVAS_PX = 16384;

/** How tall the waveform lane may be dragged, and where it starts. Double the
 *  default per band is where three stacked bands become comfortable to read. */
export const WAVE_HEIGHT = { min: 40, max: 400, start: 40, step: 16 };

const WAVE_KEY = "klights.studio.wave";
type WaveView = "mix" | "bands";

/** The waveform lane's height and view, as this browser last left them. */
function storedWave(): { height: number; view: WaveView } {
  try {
    const v = JSON.parse(localStorage.getItem(WAVE_KEY) ?? "{}") as Record<string, unknown>;
    const h = typeof v.height === "number" && Number.isFinite(v.height) ? v.height : WAVE_HEIGHT.start;
    return { height: Math.max(WAVE_HEIGHT.min, Math.min(WAVE_HEIGHT.max, Math.round(h))),
             view: v.view === "bands" ? "bands" : "mix" };
  } catch {
    return { height: WAVE_HEIGHT.start, view: "mix" };
  }
}

/** The three bands as the lane stacks them: the highs on top, the bass at the
 *  bottom, in rekordbox's own three-band colors. */
const BAND_STRIPS: { band: AudioBand; label: string; fill: string }[] = [
  { band: "high", label: "high", fill: "#e8edf5" },
  { band: "mid", label: "mid", fill: "#f59e0b" },
  { band: "low", label: "low", fill: "#3b82f6" },
];

/**
 * The track's waveform, under its phrases. Its bottom edge drags to make it
 * taller -- the lanes are laid out for a whole timeline, and a kick's shape or
 * where a break really ends needs more than forty pixels -- and where the
 * analysis has frequency bands, it can show them one above another: what a
 * lane that follows the audio is listening to.
 *
 * Drawn a pixel column at a time, each the loudest of the analysis columns
 * under it: at any zoom a timeline is edited at there are several to a pixel.
 */
export function WaveLane({ wave, grid, duration, x, width }: {
  wave: Wave | null; grid: Grid; duration: number; x: (b: number) => number; width: number;
}) {
  const canvas = useRef<HTMLCanvasElement | null>(null);
  const pixels = Math.max(1, Math.min(Math.round(width), MAX_CANVAS_PX));
  const [{ height, view }, setLook] = useState(storedWave);
  const [grip, setGrip] = useState<{ y0: number; h0: number } | null>(null);
  const remember = (next: { height: number; view: WaveView }) => {
    setLook(next);
    try { localStorage.setItem(WAVE_KEY, JSON.stringify(next)); } catch { /* not kept */ }
  };
  const sized = (h: number) => Math.max(WAVE_HEIGHT.min, Math.min(WAVE_HEIGHT.max, Math.round(h)));
  const split = wave?.bands?.low && wave.bands.mid && wave.bands.high ? wave.bands : null;
  const banded = view === "bands" && !!split;
  // `x` is a new function on every render of the page; the scale is what it is.
  const perBeat = x(1) - x(0);
  useEffect(() => {
    const el = canvas.current;
    // Null-safe: jsdom has no 2D context, and a browser can refuse one.
    const ctx = el?.getContext?.("2d") ?? null;
    if (!el || !ctx || !wave) return;
    ctx.clearRect(0, 0, el.width, el.height);
    const k = el.width / Math.max(1, width);
    const n = wave.heights.length;
    const secondsPer = wave.rate ? 1 / wave.rate : (duration || 1) / n;
    // Where each analysis column starts, in canvas pixels; one more, for the end.
    const edge = new Float64Array(n + 1);
    for (let i = 0; i <= n; i++) edge[i] = grid.beatAt(i * secondsPer) * perBeat * k;
    /** The loudest column under each pixel, and which column that was. */
    const pool = (level: (i: number) => number) => {
      const top = new Float32Array(el.width);
      const from = new Int32Array(el.width).fill(-1);
      for (let i = 0; i < n; i++) {
        const v = level(i);
        if (v <= 0) continue;
        const a = Math.max(0, Math.floor(edge[i]!));
        const b = Math.min(el.width, Math.max(a + 1, Math.ceil(edge[i + 1]!)));
        for (let px = a; px < b; px++) {
          if (v > top[px]!) { top[px] = v; from[px] = i; }
        }
      }
      return { top, from };
    };
    if (banded) {
      const strip = el.height / BAND_STRIPS.length;
      BAND_STRIPS.forEach(({ band, fill }, row) => {
        const raw = split[band]!;
        let peak = 1;
        for (const v of raw) if (v > peak) peak = v;
        const { top } = pool((i) => raw[i]! / peak);
        const floor = (row + 1) * strip;
        ctx.fillStyle = fill;
        for (let px = 0; px < el.width; px++) {
          const h = top[px]! * (strip - 2);
          if (h > 0) ctx.fillRect(px, floor - h, 1, h);
        }
        ctx.fillStyle = "rgba(255, 255, 255, 0.08)";
        if (row) ctx.fillRect(0, row * strip, el.width, 1);
      });
      return;
    }
    const mid = el.height / 2;
    const { top, from } = pool((i) => wave.heights[i]!);
    for (let px = 0; px < el.width; px++) {
      const h = top[px]! * mid;
      if (h <= 0) continue;
      const c = wave.colors?.[from[px]!];
      ctx.fillStyle = c ? `rgb(${c[0] * 255},${c[1] * 255},${c[2] * 255})` : "#5aa9ff";
      ctx.fillRect(px, mid - h, 1, h * 2);
    }
  }, [wave, grid, duration, perBeat, width, height, banded, split]);
  return (
    <div className="d-row d-wave-row">
      <div className="d-head">
        {/* By band, the head's right edge is the strips' labels. */}
        <span>Audio{!banded && <span className="muted small"> waveform</span>}</span>
        {split && (
          <button className={`small${banded ? " on" : ""}`} aria-pressed={banded}
                  title={banded ? "Show the waveform as one"
                    : "Show its low, mid and high bands one above another"}
                  onClick={() => remember({ height, view: banded ? "mix" : "bands" })}>
            Bands</button>)}
        {banded && (
          <div className="d-wave-bands muted small" aria-hidden="true">
            {BAND_STRIPS.map((b) => <span key={b.band}>{b.label}<i style={{ background: b.fill }} /></span>)}
          </div>)}
      </div>
      {wave
        ? <canvas ref={canvas} width={pixels} height={height}
                  aria-label={banded ? "waveform, by band" : "waveform"}
                  style={{ width, height }} />
        : <div className="d-empty muted small" style={{ width }}>
            no waveform -- prep the track from rekordbox to see one</div>}
      {wave && (
        <div className={`d-wave-grip${grip ? " on" : ""}`} role="separator" tabIndex={0}
             aria-orientation="horizontal" aria-label="waveform height"
             aria-valuemin={WAVE_HEIGHT.min} aria-valuemax={WAVE_HEIGHT.max} aria-valuenow={height}
             title="Drag to make the waveform taller; double-click to put it back"
             onPointerDown={(e) => {
               (e.target as Element).setPointerCapture?.(e.pointerId);
               setGrip({ y0: e.clientY, h0: height });
             }}
             onPointerMove={(e) => {
               if (grip) setLook({ view, height: sized(grip.h0 + e.clientY - grip.y0) });
             }}
             onPointerUp={() => { if (grip) { setGrip(null); remember({ height, view }); } }}
             onPointerCancel={() => { if (grip) { setGrip(null); remember({ height, view }); } }}
             onDoubleClick={() => remember({ view, height: WAVE_HEIGHT.start })}
             onKeyDown={(e) => {
               const by = (e.shiftKey ? 4 : 1) * WAVE_HEIGHT.step;
               const to = e.key === "ArrowDown" ? height + by : e.key === "ArrowUp" ? height - by
                 : e.key === "Home" ? WAVE_HEIGHT.min : e.key === "End" ? WAVE_HEIGHT.max : null;
               if (to == null) return;
               e.preventDefault();
               remember({ view, height: sized(to) });
             }} />)}
    </div>
  );
}

// -- the lanes ------------------------------------------------------------------

const TARGET_LABEL: Record<string, string> = {
  scene: "Scene", movement: "Movement", color: "Color", level: "Level",
  palette: "Palette",
};

export function Lane({ row, index, x, width, zoom, selected, onSelect, history, beat,
                      roles, onMenu, onDropItem, onAdd }: {
  row: Row; index: number; x: (b: number) => number; width: number; zoom: number;
  selected: string | null; onSelect: (id: string | null) => void;
  history: Edits; beat: number;
  /** A clip's menu (the timeline's), and a drop from its browser. */
  onMenu?: (id: string, clientX: number, clientY: number) => void;
  onDropItem?: (rowId: string, what: Placeable, beat: number) => void;
  /** A routine's lane: its rows play on a ROLE rather than owning a slot of
   *  the track, so the head picks the role and there is no gap mode. */
  roles?: string[];
  /** A click on a clips or hits lane's empty space, to offer what goes
   *  there: at a beat, or null for the playhead. */
  onAdd?: (rowId: string, beat: number | null, clientX: number, clientY: number) => void;
}) {
  if (row.type === "automation") {
    return <AutoLane row={row} x={x} width={width} history={history} beat={beat}
                     selected={selected} onSelect={onSelect} />;
  }
  if (row.type === "external") {
    if (row.output === "osc" || row.output === "midi" || row.output === "visuals"
        || row.output === "vj") {
      return <ExternalLane row={row} index={index} x={x} width={width} zoom={zoom}
                           selected={selected} onSelect={onSelect} history={history}
                           beat={beat} addable={!!onAdd} />;
    }
    return (
      <div className="d-row d-external">
        <div className="d-head">{row.label ?? `${row.output ?? "external"} · ${row.id}`}
          <Editor.LaneMenu row={row} index={index} history={history} /></div>
        <div className="d-empty muted small" style={{ width }}>
          Output: {row.output} -- kept in the file; not editable here</div>
      </div>
    );
  }
  const hits = row.type === "hits";
  // What a click on the empty lane adds, for the lane to say so.
  const noun = hits ? "a hit" : roles ? "a block" : row.target === "palette" ? "a palette"
    : "a routine or look";
  return (
    <div className={`d-row ${hits ? "d-hits" : "d-clips"}`}>
      <div className="d-head">
        <span>{hits ? "Hits" : TARGET_LABEL[row.target ?? ""] ?? row.target}
          <span className="muted small"> · {row.id}</span></span>
        {roles && !hits && (
          <select className="small" value={row.role ?? ""} aria-label={`${row.id} role`}
                  onChange={(e) => {
                    const role = e.target.value;
                    history.apply((d) => {
                      const r = d.rows.find((q) => q.id === row.id);
                      if (r) r.role = role;
                    });
                  }}>
            {!roles.includes(row.role ?? "") && <option value={row.role ?? ""}>{row.role ?? "(none)"}</option>}
            {roles.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        )}
        {!roles && !hits && <Editor.GapToggle row={row} history={history} />}
        <Editor.LaneMenu row={row} index={index} history={history} />
      </div>
      <Editor.LaneSvg row={row} x={x} width={width} zoom={zoom} selected={selected}
                      onSelect={onSelect} history={history} onMenu={onMenu}
                      onDropItem={onDropItem && ((what, at) => onDropItem(row.id, what, at))}
                      onAdd={onAdd && ((at, cx, cy) => onAdd(row.id, at, cx, cy))} noun={noun} />
    </div>
  );
}

/** A new cue on an OSC or MIDI lane, before its author says what it sends. */
function newCue(output: string): Partial<Item> {
  if (output === "midi") return { note: 60, velocity: 100 };
  if (output === "visuals" || output === "vj") return newVisuals();
  return { on: { address: "/composition/layers/1/clips/1/connect", args: [1] } };
}

const OUTPUT_LABEL: Record<string, string> = {
  osc: "OSC", midi: "MIDI", visuals: "Visuals", vj: "Visuals",
};

/** An OSC or MIDI lane (milestone 3): cues as the track plays -- or, with
 *  points, a curve sent to one OSC address or one MIDI controller. A cue goes
 *  in with "+ cue" at the playhead, or from the menu a click on the lane's
 *  empty space opens (a visuals lane's lists its scenes) -- a menu, so a click
 *  meant only to let go of a selection adds nothing. */
function ExternalLane({ row, index, x, width, zoom, selected, onSelect, history, beat, addable }: {
  row: Row; index: number; x: (b: number) => number; width: number; zoom: number;
  selected: string | null; onSelect: (id: string | null) => void;
  history: Edits; beat: number;
  /** Whether a click on the empty lane adds a cue there, as in the editors. */
  addable?: boolean;
}) {
  const curve = row.points != null;
  const now = curve ? curveValue(row.points ?? [], beat) : null;
  const set = (fields: Partial<Row>) => history.apply((d) => {
    const r = d.rows.find((q) => q.id === row.id);
    if (r) Object.assign(r, fields);
  });
  const midi = row.output === "midi";
  /** A new cue at a beat, selected so its inspector opens. */
  const addCue = (at: number, cue: Partial<Item> = newCue(row.output ?? "")) => {
    if (!history.doc) return;
    // Named first: the edit itself runs later, inside React's update.
    const id = Editor.uniqueId(history.doc, "cue");
    history.apply((d) => {
      const r = d.rows.find((q) => q.id === row.id);
      if (!r) return;
      r.items = [...(r.items ?? []), { id, at, len: 16, ...cue }];
    });
    onSelect(id);
  };
  // The menu a click on the empty lane opens: where, and the beat it starts on.
  const [adding, setAdding] = useState<{ at: number; x: number; y: number } | null>(null);
  useMenuDismiss(!!adding, () => setAdding(null));
  const add = (cue?: Partial<Item>) => () => {
    if (adding) addCue(adding.at, cue);
    setAdding(null);
  };
  const visuals = row.output === "visuals" || row.output === "vj";
  const entries: PickEntry[] = visuals
    ? VISUAL_SCENES.map((s) => ({ key: s, label: s, add: add(newVisuals(s)) }))
    : [{ key: "cue", label: midi ? "A MIDI note" : "An OSC cue", add: add() }];
  return (
    <div className={`d-row ${curve ? "d-auto" : "d-clips"} d-external`}>
      <div className="d-head">
        <span>{OUTPUT_LABEL[row.output ?? ""] ?? row.output}
          <span className="muted small"> · {row.label ?? row.id}</span>
          {now != null && <span className="muted small mono"> {now.toFixed(2)}</span>}</span>
        {curve && !midi && (
          <input className="small mono d-osc-address" aria-label={`${row.id} address`}
                 value={row.address ?? ""} placeholder="/address"
                 onChange={(e) => set({ address: e.target.value })} />)}
        {curve && midi && (
          <span className="small">
            cc <input type="number" className="d-num" min={0} max={127}
                      aria-label={`${row.id} cc`} value={row.cc ?? 0}
                      onChange={(e) => set({ cc: Number(e.target.value) })} />
            {" "}ch <input type="number" className="d-num" min={1} max={16}
                           aria-label={`${row.id} channel`} value={row.channel ?? 1}
                           onChange={(e) => set({ channel: Number(e.target.value) })} />
          </span>)}
        {!curve && (
          <button className="small" aria-label={`add a cue to ${row.id}`}
                    onClick={() => addCue(Math.max(0, history.snapBeat(beat)))}>+ cue</button>)}
        <Editor.LaneMenu row={row} index={index} history={history} />
      </div>
      {curve
        ? <Editor.AutoSvg row={row} x={x} width={width} history={history}
                          selected={selected} onSelect={onSelect} />
        : <Editor.LaneSvg row={row} x={x} width={width} zoom={zoom} selected={selected}
                          onSelect={onSelect} history={history} noun="a cue"
                          onAdd={addable ? (at, cx, cy) => setAdding({
                            at: at == null ? Math.max(0, history.snapBeat(beat))
                              : gapStart(history, row.items ?? [], at),
                            x: cx, y: cy }) : undefined} />}
      {adding && (
        <PickMenu label={`add to ${row.id}`} x={adding.x} y={adding.y} entries={entries}
                  empty="Nothing goes on this lane."
                  head={`Add a cue at bar ${barBeat(adding.at)}`} />)}
    </div>
  );
}

export function AutoLane({ row, x, width, history, beat, selected, onSelect }: {
  row: Row; x: (b: number) => number; width: number;
  history: Edits; beat: number;
  selected?: string | null; onSelect?: (id: string | null) => void;
}) {
  const spec = useLaneSpec(row);
  const audio = useContext(LaneAudio);
  const points = row.points ?? [];
  // A color lane has no number to show; it says the color it last passed.
  const now = spec.kind === "color"
    ? [...points].reverse().find((p) => p[0] <= beat)?.[1] ?? points[0]?.[1] ?? null
    : laneValue(row, beat, audio);
  // Following the audio is a track's own: offered on a timeline's number
  // lanes, and on any lane that already follows, so it can be seen and removed.
  const canFollow = audio !== undefined && spec.kind === "number";
  return (
    <div className="d-row d-auto">
      <div className="d-head">
        <span title={spec.reaches ? `drives $${spec.label} on ${spec.reaches.join(", ")}` : undefined}>
          {laneTitle(row.target ?? "", spec)}
          <span className="muted small mono">
            {" "}{now == null ? "" : typeof now === "number" ? now.toFixed(2) : String(now)}</span></span>
        <button className={`small d-wave-btn${row.wave ? " on" : ""}`}
                aria-label={`wave on ${row.id}`}
                title={row.wave ? `${row.wave.shape} every ${row.wave.bars} bars -- edit it`
                  : "Add a wave on top of the points"}
                onClick={() => {
                  if (!row.wave) {
                    history.apply((d) => {
                      const r = d.rows.find((q) => q.id === row.id);
                      if (r) r.wave = defaultWave(r, spec);
                    });
                  }
                  onSelect?.(waveId(row.id));
                }}>∿</button>
        {(canFollow || row.audio) && (
          <button className={`small d-follow${row.audio ? " on" : ""}`}
                  aria-label={`audio on ${row.id}`}
                  disabled={!row.audio && !audio}
                  title={row.audio ? `follows the audio's ${row.audio.band} band -- edit it`
                    : audio ? "Follow a band of the track's audio, on top of the points"
                      : "This track has no waveform to follow: prep it from rekordbox"}
                  onClick={() => {
                    if (!row.audio) {
                      history.apply((d) => {
                        const r = d.rows.find((q) => q.id === row.id);
                        if (r) r.audio = defaultAudio(r, spec, audio?.bands);
                      });
                    }
                    onSelect?.(audioId(row.id));
                  }}>♪</button>)}
        <Editor.LaneMenu row={row} index={-1} history={history} />
      </div>
      <Editor.AutoSvg row={row} x={x} width={width} history={history}
                      selected={selected} onSelect={onSelect} />
    </div>
  );
}

