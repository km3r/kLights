import { Card, RateCard } from "../components";
import { DesignOnly } from "../mode";
import { LookPicker } from "../LookPicker";
import {
  anyChanged, ModulationCard, ParamList, StackCard, TweakCard,
} from "../Params";
import { MACRO_PARAMS } from "../blocks";
import { PlanView } from "../Plan";
import type { Command, EngineState, ParamValue } from "../types";

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
      {/* First thing on the tab, and in both modes. "Where is that beam going"
          is the question this whole tab exists to answer, and until now the
          only answer was a list of bearings in degrees. */}
      <PlanView state={state} />

      <LookPicker state={state} send={send} slot="movement" title="Route"
                  empty="Nothing loaded — the heads are holding still." />

      {/* The loaded routine's own knobs. Renders nothing when the slot
          holds only ported looks, which have no parameters to turn. */}
      <TweakCard state={state} send={send} slot="movement" />

      <ModulationCard state={state} send={send} slot="movement" />

      {/* Move only: the colour and level slots have nothing that adds. */}
      <StackCard state={state} send={send} />

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
                  {f.at_limit && (
                    <span className="chip limit"
                          title={`at the end of its ${f.at_limit.join(" and ")} travel`}>
                      LIMIT</span>
                  )}
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
 *
 * The four sliders used to be written out here by hand, with their ranges
 * repeated from the engine's clamp in `_cmd_macro`. They are now rendered from
 * `MACRO_PARAMS` — generated from the declarations the engine clamps against — so
 * the two cannot drift, and this card is the same generic control that renders
 * a routine's own parameters one section down.
 */
function Shape({ state, send }: {
  state: EngineState; send: (c: Command) => void;
}) {
  const macro = state.macro;
  const specs = MACRO_PARAMS;

  // The engine holds these as two scalars and a pair; the descriptors name four
  // flat parameters. Mapping between the two here rather than reshaping the
  // command keeps `macro` on the wire exactly as it was.
  const values: Record<string, ParamValue> = {
    size: macro.size, spread: macro.spread,
    bearing: macro.center[0], elev: macro.center[1],
  };

  const changed = anyChanged(specs, values);
  const limited = state.fixtures.filter((f) => f.at_limit?.length).map((f) => f.name);

  const apply = (name: string, value: ParamValue) => {
    const v = Number(value);
    if (name === "size") send({ type: "macro", size: v });
    else if (name === "spread") send({ type: "macro", spread: v });
    else if (name === "bearing") send({ type: "macro", center: [v, macro.center[1]] });
    else if (name === "elev") send({ type: "macro", center: [macro.center[0], v] });
  };

  return (
    <Card title="Shape" right={
      <button className="small" disabled={!changed}
              aria-label="reset shape"
              onClick={() => send({ type: "macro", reset: true })}>Reset</button>
    }>
      <ParamList specs={specs} values={values} onChange={apply}
                 reach={state.reach} />
      {/* The centre is bounded by the MOST capable head, so a less capable one
          can be asked for somewhere it cannot go. It stops at its rail, which
          is a sensible place to stop -- but the operator turning this knob is
          the one who needs to know, in either mode, so it is said here rather
          than only in the Design-mode heads readout. */}
      {limited.length > 0 && (
        <p className="small" style={{ color: "var(--warn)", marginBottom: 0 }}>
          {limited.length === 1 ? `${limited[0]} is` : `${limited.length} heads are`}{" "}
          at the end of {limited.length === 1 ? "its" : "their"} travel — they
          stop at the rail while the others go on.
        </p>
      )}
    </Card>
  );
}
