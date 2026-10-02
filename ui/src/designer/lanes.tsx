import { useEffect, useRef } from "react";
import { BEATS_PER_BAR, curveValue } from "./model";
import type { Grid, Row, TrackDoc, Wave } from "./model";
import { Editor } from "./edit";
import type { Edits } from "./edit";

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

const PHRASE_HUE: Record<string, string> = {
  Intro: "#3b82f6", Verse: "#14b8a6", Up: "#f59e0b", Chorus: "#ef4444",
  Down: "#8b5cf6", Bridge: "#ec4899", Outro: "#64748b",
};

export function Phrases({ track, x, width }: { track: TrackDoc; x: (b: number) => number; width: number }) {
  const items = track.phrases?.items ?? [];
  return (
    <div className="d-row d-phrases">
      <div className="d-head">Phrases <span className="muted small">rekordbox</span></div>
      <svg width={width} height={22} aria-label="phrases">
        {items.map(([start, end, label]) => {
          const family = label.replace(/\s*\d+$/, "");
          return (
            <g key={`${start}-${label}`}>
              <rect x={x(start)} y={2} width={Math.max(1, x(end) - x(start) - 1)} height={18}
                    rx={3} fill={PHRASE_HUE[family] ?? "#475569"} opacity={0.55} />
              <text x={x(start) + 4} y={15} className="d-label">{label}</text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

export function WaveLane({ wave, grid, duration, x, width }: {
  wave: Wave | null; grid: Grid; duration: number; x: (b: number) => number; width: number;
}) {
  const canvas = useRef<HTMLCanvasElement | null>(null);
  useEffect(() => {
    const el = canvas.current;
    // Null-safe: jsdom has no 2D context, and a browser can refuse one.
    const ctx = el?.getContext?.("2d") ?? null;
    if (!el || !ctx || !wave) return;
    ctx.clearRect(0, 0, el.width, el.height);
    const n = wave.heights.length;
    const secondsPer = wave.rate ? 1 / wave.rate : (duration || 1) / n;
    const mid = el.height / 2;
    for (let i = 0; i < n; i++) {
      const px = x(grid.beatAt(i * secondsPer));
      const h = wave.heights[i]! * mid;
      const c = wave.colors?.[i];
      ctx.fillStyle = c ? `rgb(${c[0] * 255},${c[1] * 255},${c[2] * 255})` : "#5aa9ff";
      ctx.fillRect(px, mid - h, Math.max(1, x(grid.beatAt((i + 1) * secondsPer)) - px), h * 2);
    }
  }, [wave, grid, duration, x, width]);
  return (
    <div className="d-row d-wave">
      <div className="d-head">Audio <span className="muted small">waveform</span></div>
      {wave
        ? <canvas ref={canvas} width={width} height={40} aria-label="waveform" />
        : <div className="d-empty muted small" style={{ width }}>
            no waveform -- prep the track from rekordbox to see one</div>}
    </div>
  );
}

// -- the lanes ------------------------------------------------------------------

const TARGET_LABEL: Record<string, string> = {
  scene: "Scene", movement: "Movement", color: "Colour", level: "Level",
  palette: "Palette",
};

export function Lane({ row, index, x, width, zoom, selected, onSelect, history, beat,
                      roles }: {
  row: Row; index: number; x: (b: number) => number; width: number; zoom: number;
  selected: string | null; onSelect: (id: string | null) => void;
  history: Edits; beat: number;
  /** A routine's lane: its rows play on a ROLE rather than owning a slot of
   *  the track, so the head picks the role and there is no gap mode. */
  roles?: string[];
}) {
  if (row.type === "automation") {
    return <AutoLane row={row} x={x} width={width} history={history} beat={beat} />;
  }
  if (row.type === "external") {
    return (
      <div className="d-row d-external">
        <div className="d-head">{row.label ?? `${row.output ?? "external"} · ${row.id}`}
          <span className="muted small"> (later)</span></div>
        <div className="d-empty muted small" style={{ width }}>
          Output: {row.output} -- carried in the file, played from milestone 3</div>
      </div>
    );
  }
  const hits = row.type === "hits";
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
                      onSelect={onSelect} history={history} />
    </div>
  );
}

export function AutoLane({ row, x, width, history, beat }: {
  row: Row; x: (b: number) => number; width: number;
  history: Edits; beat: number;
}) {
  const now = curveValue(row.points ?? [], beat);
  return (
    <div className="d-row d-auto">
      <div className="d-head">
        <span>{row.target === "master" ? "Master" : row.target}
          <span className="muted small mono"> {now == null ? "" : now.toFixed(2)}</span></span>
        <Editor.LaneMenu row={row} index={-1} history={history} />
      </div>
      <Editor.AutoSvg row={row} x={x} width={width} history={history} />
    </div>
  );
}

