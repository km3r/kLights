import { useId, useState } from "react";
import type { ReactNode } from "react";
import type { Command, EngineState, RGB, Slot } from "./types";

export function rgbCss(c: RGB | undefined, fallback = "#333"): string {
  if (!c) return fallback;
  const to255 = (v: number) => Math.round(Math.max(0, Math.min(1, v)) * 255);
  return `rgb(${to255(c[0])}, ${to255(c[1])}, ${to255(c[2])})`;
}

/**
 * A "?" that opens an explanation in place, for the controls whose label does
 * not say what they do.
 *
 * A tap, not a tooltip. The console had a few `title` tooltips and a phone has
 * no hover, so on the surface this is built for they did not exist. Opened in
 * the flow of the card rather than floated over it, so it never covers the
 * control it is explaining.
 *
 * Closed by default and remembered only while mounted: help that reopened
 * itself on every reload would be clutter on the fortieth night to save one tap
 * on the first.
 */
export function useHelp(topic: string) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return {
    button: (
      <button type="button" className="help-btn" aria-expanded={open}
              aria-controls={id} aria-label={`Help: ${topic}`}
              title="What this does" onClick={() => setOpen(!open)}>
        ?
      </button>
    ),
    panel: (children: ReactNode) => open
      ? <div className="help" id={id} role="note">{children}</div>
      : null,
  };
}

/** A heading with a "?" after it, for the designer's side panels -- the same
 *  explanation as a card's, under a plain heading. */
export function HelpHeading({ topic, help, children }: {
  topic: string; help: ReactNode; children: ReactNode;
}) {
  const explain = useHelp(topic);
  return (
    <>
      <h3>{children}{explain.button}</h3>
      {explain.panel(help)}
    </>
  );
}

export function Card({ title, right, help, children }: {
  title?: string; right?: ReactNode;
  /** An explanation behind a "?" by the title, for a card whose controls do
   *  not explain themselves. */
  help?: ReactNode;
  children: ReactNode;
}) {
  const explain = useHelp(title ?? "");
  return (
    <section className="card">
      {title && (
        <h2>
          <span className="grow">{title}{help != null && explain.button}</span>
          {right}
        </h2>
      )}
      {help != null && explain.panel(help)}
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

const RATES = [0, 0.25, 0.5, 1, 2, 4];

/**
 * How fast one slot's chase runs. One card, three tabs — Move, Color, Bright.
 *
 * NOT the same as Speed, which is the clock: speed changes what the music is
 * doing as far as the whole show is concerned, including cue holds and auto
 * boundaries, and it is on the Show tab beside the tempo it belongs to. A rate
 * moves only this slot's phase, which is why a colour chase at 0.5× under a
 * move at 2× is now something that can be said at all. The old console needed a
 * separately stored chase per combination, and that is a large part of how it
 * accumulated 206 looks.
 *
 * Each rate lives on the tab that owns the slot rather than in one panel of
 * three faders, because the question "how fast should the colours run" is one
 * you ask while looking at the colours.
 */
export function RateCard({ state, send, slot, hint }: {
  state: EngineState; send: (c: Command) => void; slot: Slot; hint: ReactNode;
}) {
  const rate = state.auto.slot_rates[slot] ?? 1;
  return (
    <Card title="Rate" right={
      // Named for what it resets. There is a bare "Reset" on the Shape card one
      // section down the same tab, and two controls whose accessible name is
      // the identical word are ambiguous to anyone not reading the layout.
      <button className="small" disabled={rate === 1}
              aria-label={`reset ${slot} rate`}
              onClick={() => send({ type: "rate", slot, value: 1 })}>
        Reset
      </button>
    }>
      <div className="rates">
        {RATES.map((r) => (
          <button key={r}
                  className={Math.abs(rate - r) < 0.01 ? "on" : ""}
                  aria-label={`${slot} rate ${r}`}
                  onClick={() => send({ type: "rate", slot, value: r })}>
            {/* 0 is a hold, not a speed, and calling it "0x" reads as a
                mistake. It parks this slot on its current frame while
                everything else keeps running, which is a real thing to want. */}
            {r === 0 ? "hold" : `${r}×`}
          </button>
        ))}
      </div>
      <p className="small muted" style={{ marginBottom: 0 }}>{hint}</p>
    </Card>
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
