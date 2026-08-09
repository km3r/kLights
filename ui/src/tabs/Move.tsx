import { useState } from "react";
import { Card, RateCard } from "../components";
import { DesignOnly } from "../mode";
import { LookPicker } from "../LookPicker";
import type { Command, EngineState } from "../types";

/**
 * Movement: the route, the rate, and what each head is doing about it.
 *
 * A route and a rate are separate here, which is the point. In the old console
 * every combination of the two was its own stored chase, which is how 179
 * accumulated. Now they multiply instead of being enumerated — and picking a
 * route leaves the colour and level slots untouched.
 */
export function MoveTab({ state, send }: {
  state: EngineState; send: (c: Command) => void;
}) {
  const movers = state.fixtures.filter((f) => f.is_mover);

  return (
    <>
      <LookPicker state={state} send={send} slot="movement" title="Route"
                  empty="Nothing loaded — the heads are holding still." />

      {/* This card used to be a second copy of the Show tab's global Speed,
          which was two controls doing one thing in two places. It is the
          MOVEMENT slot's own rate now: Speed is still on Show, where the tempo
          it belongs to lives. */}
      <RateCard state={state} send={send} slot="movement" hint={
        <>
          How fast the route runs, and <b>only</b> the route — the colours and
          levels keep their own. Slowing a move makes it <b>smoother</b>, not
          steppier: the route is a path sampled at the current phase, so a
          longer cycle just gets more frames. Changing it moves the phase on
          from where it is rather than recomputing it, so nothing jumps.
        </>
      } />

      <Shape state={state} send={send} />

      {/* Where every head is pointing and what the taper is doing about it —
          a readout, so Perform mode does without it. The banner that fires when
          a head is JOGGING is app-wide and is not part of this. */}
      <DesignOnly>
      <Card title="Heads">
        <div className="grid two">
          {movers.map((f) => {
            const taper = f.safety?.taper ?? 1;
            return (
              <div key={f.id} className="fixture">
                <div className="name">
                  <span className="grow">{f.name}</span>
                  {f.jogging && <span className="chip jog">JOG</span>}
                  {taper < 1 && (
                    <span className="chip taper">{Math.round(taper * 100)}%</span>
                  )}
                </div>

                <div className="bar">
                  <i style={{ width: `${Math.round((f.intensity ?? 0) * 100)}%` }}
                     className={taper < 1 ? "taper" : ""} />
                </div>

                {f.aim && (
                  <div className="small muted mono">
                    bearing {f.aim.bearing.toFixed(1)}° · elev {f.aim.elevation.toFixed(1)}°
                  </div>
                )}
                {f.lands_on && (
                  <div className="small muted">
                    lands on <b>{f.lands_on}</b>
                    {f.throw_mm != null && <> at {(f.throw_mm / 1000).toFixed(1)} m</>}
                  </div>
                )}
                {f.safety && taper < 1 && (
                  <div className="small" style={{ color: "var(--warn)" }}>
                    {f.safety.reason}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </Card>
      </DesignOnly>
    </>
  );
}

/**
 * Shape: the four knobs that make one route cover what a shelf of stored chases
 * used to.
 *
 * The old library holds 103 poses and 26 paths because QLC+ stored DMX values
 * and had no parameters, so every variation of a move had to be its own scene.
 * These are the variations that actually recurred: how far it travels, whether
 * the heads do it together, and where the whole thing sits. "Ball Wave" with the
 * centre dropped 40° IS the look that used to be a separate "Floor Wave" entry.
 *
 * They apply to whatever route is up and survive an auto look change, because
 * they live on the engine's context rather than in the composed look.
 */
function Shape({ state, send }: {
  state: EngineState; send: (c: Command) => void;
}) {
  const macro = state.macro;
  const [drag, setDrag] = useState<Partial<Record<string, number>>>({});
  const value = (key: "size" | "spread" | "bearing" | "elev") => {
    if (drag[key] !== undefined) return drag[key]!;
    if (key === "size") return macro.size;
    if (key === "spread") return macro.spread;
    return key === "bearing" ? macro.center[0] : macro.center[1];
  };

  const changed = macro.size !== 1 || macro.spread !== 0
    || macro.center[0] !== 0 || macro.center[1] !== 0;

  const row = (key: "size" | "spread" | "bearing" | "elev",
               label: string, min: number, max: number, step: number,
               fmt: (v: number) => string, hint: string) => (
    <div style={{ marginBottom: "0.6rem" }}>
      <div className="row tight">
        <label className="small grow" htmlFor={`macro-${key}`}>{label}</label>
        <span className="small muted mono">{fmt(value(key))}</span>
      </div>
      <input id={`macro-${key}`} type="range" style={{ width: "100%" }}
             min={min} max={max} step={step} value={value(key)}
             aria-label={label}
             onChange={(e) => {
               const v = Number(e.target.value);
               setDrag((d) => ({ ...d, [key]: v }));
               // Sent live, not on release: these are performance controls and
               // watching the rig respond is how you find the value you want.
               if (key === "size") send({ type: "macro", size: v });
               else if (key === "spread") send({ type: "macro", spread: v });
               else send({ type: "macro",
                           center: key === "bearing"
                             ? [v, value("elev")] : [value("bearing"), v] });
             }}
             onPointerUp={() => setDrag((d) => ({ ...d, [key]: undefined }))}
             onBlur={() => setDrag((d) => ({ ...d, [key]: undefined }))} />
      <p className="small muted" style={{ margin: 0 }}>{hint}</p>
    </div>
  );

  return (
    <Card title="Shape" right={
      <button className="small" disabled={!changed}
              onClick={() => send({ type: "macro", reset: true })}>Reset</button>
    }>
      {row("size", "Size", 0, 3, 0.05, (v) => `${v.toFixed(2)}×`,
           "How far the route travels. 0 parks every head on the mirror ball.")}
      {row("spread", "Spread", -1, 1, 0.02, (v) => v.toFixed(2),
           "Lags each head along its own route. 0 is unison, 1 spreads them evenly around one cycle.")}
      {row("bearing", "Centre —", -180, 180, 1, (v) => `${v.toFixed(0)}° round`,
           "Swings the whole look around the room.")}
      {row("elev", "Centre |", -90, 90, 1, (v) => `${v.toFixed(0)}° up/down`,
           "Drops or lifts the whole look. The safety taper still runs after this, so aiming down does not bypass it.")}
    </Card>
  );
}
