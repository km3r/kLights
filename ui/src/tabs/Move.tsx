import { Card } from "../components";
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

      <Card title="Speed">
        <div className="row">
          {[0.25, 0.5, 1, 2, 4].map((s) => (
            <button key={s} style={{ flex: 1 }}
                    className={Math.abs(state.clock.speed - s) < 0.01 ? "on" : ""}
                    onClick={() => send({ type: "speed", value: s })}>
              {s}×
            </button>
          ))}
        </div>
        <p className="small muted" style={{ marginBottom: 0 }}>
          Slowing a move down makes it <b>smoother</b>, not steppier — the route
          is a path sampled at the current phase, so a longer cycle just gets
          more frames. Changing speed re-anchors the timeline first, so nothing
          jumps.
        </p>
      </Card>

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
    </>
  );
}
