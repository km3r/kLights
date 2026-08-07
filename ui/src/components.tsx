import type { ReactNode } from "react";
import type { RGB } from "./types";

export function rgbCss(c: RGB | undefined, fallback = "#333"): string {
  if (!c) return fallback;
  const to255 = (v: number) => Math.round(Math.max(0, Math.min(1, v)) * 255);
  return `rgb(${to255(c[0])}, ${to255(c[1])}, ${to255(c[2])})`;
}

export function Card({ title, right, children }: {
  title?: string; right?: ReactNode; children: ReactNode;
}) {
  return (
    <section className="card">
      {title && <h2><span className="grow">{title}</span>{right}</h2>}
      {children}
    </section>
  );
}

export function Toggle({ label, hint, on, onChange }: {
  label: string; hint?: string; on: boolean; onChange: (on: boolean) => void;
}) {
  return (
    <button className={`toggle ${on ? "on" : ""}`} onClick={() => onChange(!on)}
            aria-pressed={on}>
      <span className="label">
        <span>{label}</span>
        {hint && <small>{hint}</small>}
      </span>
      <span className="chip">{on ? "ON" : "off"}</span>
    </button>
  );
}

export function Banner({ kind, children }: {
  kind: "bad" | "warn" | "info"; children: ReactNode;
}) {
  return <div className={`banner ${kind}`}>{children}</div>;
}

/**
 * A slider that does not fight the server.
 *
 * The engine is authoritative, but a fader whose value is replaced by a
 * broadcast mid-drag jumps under the thumb. So it tracks locally while the
 * finger is down and defers to the server the moment it is lifted — the only
 * place the UI holds state, and only for as long as someone is touching it.
 */
export function Fader({ value, onInput, onCommit, label, format }: {
  value: number;
  onInput: (v: number) => void;
  onCommit?: (v: number) => void;
  label?: string;
  format?: (v: number) => string;
}) {
  return (
    <div className="master">
      {label && <span className="small muted" aria-hidden="true">{label}</span>}
      {/* The visible text is a sibling span, not a <label>, so the control
          needs its own accessible name — otherwise it is an anonymous slider
          to a screen reader, and unfindable by anything but position. */}
      <input type="range" min={0} max={1} step={0.01} value={value}
             aria-label={label}
             onChange={(e) => onInput(Number(e.target.value))}
             onPointerUp={(e) => onCommit?.(Number((e.target as HTMLInputElement).value))}
             onKeyUp={(e) => onCommit?.(Number((e.target as HTMLInputElement).value))} />
      <span className="small mono" style={{ minWidth: "3.2em", textAlign: "right" }}>
        {format ? format(value) : `${Math.round(value * 100)}%`}
      </span>
    </div>
  );
}

export function BeatDots({ beatInBar, beatsPerBar = 4 }: {
  beatInBar: number; beatsPerBar?: number;
}) {
  const current = Math.floor(beatInBar);
  return (
    <div className="beat" aria-label="beat">
      {Array.from({ length: beatsPerBar }, (_, i) => (
        <i key={i} className={i === current ? "on" : ""} />
      ))}
    </div>
  );
}
