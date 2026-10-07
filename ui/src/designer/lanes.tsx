import { useEffect, useRef, useState } from "react";
import {
  BEATS_PER_BAR, PHRASE_HUE, VISUAL_SCENES, barBeat, curveValue, laneValue, phraseFamily,
} from "./model";
import type { Grid, Item, Row, TrackDoc, Wave } from "./model";
import {
  Editor, PickMenu, defaultWave, gapStart, laneTitle, newVisuals, useLaneSpec, useMenuDismiss,
  waveId,
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

export function WaveLane({ wave, grid, duration, x, width }: {
  wave: Wave | null; grid: Grid; duration: number; x: (b: number) => number; width: number;
}) {
  const canvas = useRef<HTMLCanvasElement | null>(null);
  const pixels = Math.max(1, Math.min(Math.round(width), MAX_CANVAS_PX));
  useEffect(() => {
    const el = canvas.current;
    // Null-safe: jsdom has no 2D context, and a browser can refuse one.
    const ctx = el?.getContext?.("2d") ?? null;
    if (!el || !ctx || !wave) return;
    ctx.clearRect(0, 0, el.width, el.height);
    const k = el.width / Math.max(1, width);
    const n = wave.heights.length;
    const secondsPer = wave.rate ? 1 / wave.rate : (duration || 1) / n;
    const mid = el.height / 2;
    for (let i = 0; i < n; i++) {
      const px = x(grid.beatAt(i * secondsPer)) * k;
      const h = wave.heights[i]! * mid;
      const c = wave.colors?.[i];
      ctx.fillStyle = c ? `rgb(${c[0] * 255},${c[1] * 255},${c[2] * 255})` : "#5aa9ff";
      ctx.fillRect(px, mid - h, Math.max(1, x(grid.beatAt((i + 1) * secondsPer)) * k - px), h * 2);
    }
  }, [wave, grid, duration, x, width]);
  return (
    <div className="d-row d-wave">
      <div className="d-head">Audio <span className="muted small">waveform</span></div>
      {wave
        ? <canvas ref={canvas} width={pixels} height={40} aria-label="waveform"
                  style={{ width, height: 40 }} />
        : <div className="d-empty muted small" style={{ width }}>
            no waveform -- prep the track from rekordbox to see one</div>}
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
  const points = row.points ?? [];
  // A color lane has no number to show; it says the color it last passed.
  const now = spec.kind === "color"
    ? [...points].reverse().find((p) => p[0] <= beat)?.[1] ?? points[0]?.[1] ?? null
    : laneValue(row, beat);
  return (
    <div className="d-row d-auto">
      <div className="d-head">
        <span title={spec.reaches ? `drives $${spec.label} on ${spec.reaches.join(", ")}` : undefined}>
          {laneTitle(row.target ?? "", spec)}
          <span className="muted small mono">
            {" "}{now == null ? "" : typeof now === "number" ? now.toFixed(2) : String(now)}</span></span>
        <button className={`small d-wave${row.wave ? " on" : ""}`}
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
        <Editor.LaneMenu row={row} index={-1} history={history} />
      </div>
      <Editor.AutoSvg row={row} x={x} width={width} history={history}
                      selected={selected} onSelect={onSelect} />
    </div>
  );
}

