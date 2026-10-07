import { useState } from "react";
import { BLOCK_PARAMS, MACRO_PARAMS, MODULATOR_SHAPES } from "./blocks";
import { Card, rgbCss } from "./components";
import type {
  Command, EngineState, LookInfo, ModulatorSpec, ParamSpec, ParamValue, RGB,
  Slot,
} from "./types";

/** The kinds a phone renders. The rest are block arguments for authoring —
 *  a list of colors, room points, a look or preset name — and live in the
 *  routine editor, which has the room to do them justice. */
export const CONSOLE_KINDS: ParamSpec["kind"][] =
  ["number", "integer", "bool", "choice", "color"];

/**
 * One control, rendered from what the engine declared.
 *
 * The console used to hardcode a control per parameter — four hand-written
 * `row(...)` calls for the shape macros, a literal `RATES` array, a literal
 * speed list. That meant a range was written out in three places (the engine's
 * clamp, a config Spec, and the slider's arguments here) with nothing keeping
 * them in step, and it meant a new routine could not have a knob without
 * someone writing TypeScript for it.
 *
 * Here the engine declares a `ParamSpec` and this renders it. A block added to
 * `engine/blocks.py` gets a full control panel with no UI work at all, and
 * a range can only be changed in the one place that clamps it.
 *
 * Sliders send LIVE, on every input event, and that is deliberate: these are
 * performance controls and watching the rig respond is how you find the value
 * you want. Committing on release would make finding a value a series of
 * guesses. The local `drag` state exists for the same reason `Fader` has one —
 * a value replaced by a 10 Hz broadcast mid-drag jumps under the thumb.
 */
export function ParamControl({ spec, value, onChange }: {
  spec: ParamSpec;
  value: ParamValue | undefined;
  onChange: (v: ParamValue) => void;
}) {
  const [drag, setDrag] = useState<number | undefined>(undefined);
  const current = (value ?? spec.default) as ParamValue | null;
  const id = `param-${spec.name}`;

  if (spec.kind === "bool") {
    return (
      <div className="param">
        <button className={`toggle ${current ? "on" : ""}`}
                aria-pressed={Boolean(current)}
                onClick={() => onChange(!current)}>
          <span className="label">
            <span>{spec.label}</span>
            {spec.help && <small>{spec.help}</small>}
          </span>
          <span className="chip">{current ? "ON" : "off"}</span>
        </button>
      </div>
    );
  }

  if (spec.kind === "choice") {
    return (
      <div className="param">
        <div className="row tight">
          <span className="small grow">{spec.label}</span>
        </div>
        <div className="pills" role="group" aria-label={spec.label}>
          {(spec.choices ?? []).map((choice) => (
            <button key={choice} className={current === choice ? "on" : ""}
                    onClick={() => onChange(choice)}>
              {choice.replace(/_/g, " ")}
            </button>
          ))}
        </div>
        {spec.help && <p className="small muted" style={{ margin: 0 }}>{spec.help}</p>}
      </div>
    );
  }

  if (spec.kind === "color") {
    // Three sliders rather than a color picker. The Color tab already has a
    // full HSV picker for the thing an operator reaches for mid-set; a
    // block's color is part of authoring a look, and RGB is what the
    // engine stores, so showing anything else would round-trip through a
    // conversion for no gain.
    // A block color may also be a palette role ("@primary") or a hex color,
    // which follow the palette or the file rather than these sliders. Shown as
    // what it is, and the first touch of a slider replaces it with a color of
    // its own, starting from white.
    const literal = Array.isArray(current) ? (current as RGB) : null;
    const rgb: RGB = literal ?? [1, 1, 1];
    const channel = (index: 0 | 1 | 2, name: string) => (
      <div className="row tight" key={name}>
        <span className="small mono" style={{ width: "1.2em" }}>{name}</span>
        <input type="range" min={0} max={1} step={0.01} value={rgb[index]}
               style={{ flex: 1 }}
               aria-label={`${spec.label} ${name}`}
               onChange={(e) => {
                 const next: RGB = [rgb[0], rgb[1], rgb[2]];
                 next[index] = Number(e.target.value);
                 onChange(next);
               }} />
      </div>
    );
    return (
      <div className="param">
        <div className="row tight">
          <span className="small grow">{spec.label}</span>
          {literal ? (
            <span className="swatch" aria-hidden="true"
                  style={{ background: rgbCss(rgb), width: 22, height: 22,
                           borderRadius: 4, border: "1px solid var(--line)" }} />
          ) : (
            // A role follows the palette, so there is no fixed swatch to show.
            <span className="small muted mono">{String(current ?? "")}</span>
          )}
        </div>
        {channel(0, "R")}
        {channel(1, "G")}
        {channel(2, "B")}
        {spec.help && <p className="small muted" style={{ margin: 0 }}>{spec.help}</p>}
      </div>
    );
  }

  // number / integer
  const numeric = typeof current === "number" ? current
    : Number(spec.default ?? spec.min ?? 0);
  const shown = drag ?? numeric;
  const step = spec.step ?? (spec.kind === "integer" ? 1 : 0.01);
  return (
    <div className="param">
      <div className="row tight">
        <label className="small grow" htmlFor={id}>{spec.label}</label>
        <span className="small muted mono">{format(spec, shown)}</span>
      </div>
      <input id={id} type="range" style={{ width: "100%" }}
             min={spec.min ?? 0} max={spec.max ?? 1} step={step}
             value={shown} aria-label={spec.label}
             onChange={(e) => {
               const v = Number(e.target.value);
               setDrag(v);
               onChange(spec.kind === "integer" ? Math.round(v) : v);
             }}
             onPointerUp={() => setDrag(undefined)}
             onBlur={() => setDrag(undefined)} />
      {spec.help && <p className="small muted" style={{ margin: 0 }}>{spec.help}</p>}
    </div>
  );
}

/** A reach-bounded spec with the rig's range in place of its fallback.
 *
 *  The engine clamps to exactly this (`params.bounds`), so the slider can
 *  neither offer somewhere no head can go nor stop short of somewhere one can.
 *  A spec that is not reach-bounded, or a rig with no moving heads, is
 *  returned as it is. */
export function withReach(spec: ParamSpec, reach?: EngineState["reach"]): ParamSpec {
  const span = spec.reach ? reach?.[spec.reach] : undefined;
  return span ? { ...spec, min: span[0], max: span[1] } : spec;
}

/** How many decimals a step of this size needs, so 0.05 does not read "1.2000001". */
function format(spec: ParamSpec, value: number): string {
  if (spec.kind === "integer") return `${Math.round(value)}${spec.unit ?? ""}`;
  const step = spec.step ?? 0.01;
  const places = step >= 1 ? 0 : step >= 0.1 ? 1 : 2;
  return `${value.toFixed(places)}${spec.unit ?? ""}`;
}

/**
 * A whole set of parameters, and whether any has been moved off its default.
 *
 * `changed` is what a Reset button needs to know, and working it out here keeps
 * "what counts as changed" in one place rather than in each caller.
 */
export function ParamList({ specs, values, onChange, reach }: {
  specs: ParamSpec[];
  values: Record<string, ParamValue>;
  onChange: (name: string, value: ParamValue) => void;
  /** The rig's reach, so an absolute angle's slider spans where the heads can
   *  actually go rather than a fixed guess. */
  reach?: EngineState["reach"];
}) {
  return (
    <>
      {specs.map((s) => withReach(s, reach)).map((spec) => (
        <ParamControl key={spec.name} spec={spec} value={values[spec.name]}
                      onChange={(v) => onChange(spec.name, v)} />
      ))}
    </>
  );
}

/**
 * The loaded parametric look's own knobs.
 *
 * This is the card the ported library cannot have. A stored look is a table of
 * DMX — there is nothing in "Ball Wave" to turn, which is exactly why 103 poses
 * and 26 paths accumulated as separate entries. A parametric look names a block, so
 * its radius, its cycle and its flattening are numbers, and one entry covers
 * what a shelf of stored chases used to.
 *
 * Renders nothing at all when the slot holds only ported looks, rather than an
 * empty card explaining itself. On the Move tab that is most of the library,
 * and a permanently-empty card is furniture.
 *
 * One section per loaded routine, because a slot is filled per fixture group —
 * the pinspots can be on their own routine while the movers are on another, and
 * both are tunable at once.
 */
export function TweakCard({ state, send, slot }: {
  state: EngineState; send: (c: Command) => void; slot: Slot;
}) {
  const loaded = state.selection[slot] ?? {};
  const byName = new Map(state.looks.map((l) => [l.name, l]));
  // De-duplicated: a routine covering two groups fills both, and showing its
  // parameters twice would offer two controls for one number.
  const names = [...new Set(Object.values(loaded))];
  const tunable = names
    .map((n) => byName.get(n))
    .filter((l): l is LookInfo => Boolean(l?.block));

  if (tunable.length === 0) return null;

  return (
    <>
      {tunable.map((look) => {
        const declared = BLOCK_PARAMS[look.block!];
        if (!declared) return null;
        // Only what a phone can sensibly turn. A color list, room points and
        // a look or preset name are authoring, done in the routine editor.
        const specs = declared.filter((s) => CONSOLE_KINDS.includes(s.kind));
        const overrides = state.look_params?.[look.name] ?? {};
        // Three layers, innermost first: the block's declared default, what
        // parametric_looks.json authored over it, and what is dialled in now.
        const values: Record<string, ParamValue> = {
          ...Object.fromEntries(declared
            .filter((s) => s.default !== null && s.default !== undefined)
            .map((s) => [s.name, s.default as ParamValue])),
          ...(look.args ?? {}),
          ...overrides,
        };
        const changed = Object.keys(overrides).length > 0;
        return (
          <Card key={look.name} title={`Tweak · ${look.name}`} right={
            <span className="row tight" style={{ gap: "0.3rem" }}>
              {/* Somewhere else, but still sensible. Only possible because
                  every parameter declares a range — without one there is no
                  answer to "how far is too far". Seeded, so a variation worth
                  keeping can be written down. */}
              <button className="small" aria-label={`vary ${look.name}`}
                      onClick={() => send({ type: "vary", name: look.name,
                                            amount: 0.35 })}>
                Vary
              </button>
              <button className="small" disabled={!changed}
                      aria-label={`reset ${look.name}`}
                      onClick={() => send({ type: "look_params",
                                            name: look.name, reset: true })}>
                Reset
              </button>
            </span>
          }>
            <p className="small muted" style={{ marginTop: 0 }}>
              The <b>{look.block}</b> block — the same one a show folder's
              routines are built from.
            </p>
            <ParamList
              specs={specs} values={values} reach={state.reach}
              onChange={(name, value) => send({
                type: "look_params", name: look.name, values: { [name]: value },
              })} />
          </Card>
        );
      })}
    </>
  );
}

/**
 * Movement routines stacked over the base route.
 *
 * Free, and new. Every movement layer ADDS a degree offset — only the base pose
 * layer assigns — so a slow orbit under a fast small jitter is just two layers.
 * The old console could store the sum of two moves as a third scene, but only
 * at one relative phase, and that phase was baked in.
 *
 * Move tab only: the color and level slots have nothing that adds.
 */
export function StackCard({ state, send }: {
  state: EngineState; send: (c: Command) => void;
}) {
  const [open, setOpen] = useState(false);
  const stacked = state.movement_extra ?? [];
  const max = state.movement_stack_max ?? 3;
  const base = Object.values(state.selection.movement ?? {})[0];
  const candidates = state.looks.filter(
    (l) => l.slot === "movement" && !l.step_of && !l.retired
           && l.name !== base && !stacked.includes(l.name));

  return (
    <Card title="Layers" right={
      <button className="small" disabled={stacked.length === 0}
              aria-label="clear stacked routines"
              onClick={() => send({ type: "movement_remove", all: true })}>
        Clear
      </button>
    }>
      <p className="small muted" style={{ marginTop: 0 }}>
        {stacked.length === 0
          ? "Just the one route. Stack another on top and their offsets add — "
            + "a slow orbit under a fast wobble is two layers, not a third "
            + "stored scene."
          : `${base ?? "the route"} + ${stacked.length} on top.`}
      </p>

      {stacked.map((name) => (
        <div key={name} className="row tight">
          <span className="small grow">{name}</span>
          <button className="small" aria-label={`unstack ${name}`}
                  onClick={() => send({ type: "movement_remove", name })}>
            Remove
          </button>
        </div>
      ))}

      {stacked.length < max ? (
        <>
          <button className="small" style={{ width: "100%", marginTop: "0.4rem" }}
                  onClick={() => setOpen(!open)}>
            {open ? "Cancel" : "Stack another route"}
          </button>
          {open && (
            <div className="grid tiles" style={{ marginTop: "0.4rem" }}>
              {candidates.map((l) => (
                <button key={l.name}
                        onClick={() => {
                          send({ type: "movement_add", name: l.name });
                          setOpen(false);
                        }}>
                  {l.name}
                  {l.block && <span className="chip tunable">tune</span>}
                </button>
              ))}
            </div>
          )}
        </>
      ) : (
        // A cap for legibility, not a limit the maths needs — offsets would
        // keep adding fine, and nobody could tell which layer was doing what.
        <p className="small muted" style={{ marginBottom: 0 }}>
          {max} is the limit. Past that nobody can tell which layer is doing
          what.
        </p>
      )}
    </Card>
  );
}

/**
 * Parameters that are moving on their own.
 *
 * The single thing the old console could not say at all. A stored scene has no
 * parameters, so "the same look, slowly widening" was a chase of twenty scenes
 * that stepped between them — visibly. Here it is one routine and one waveform.
 *
 * Shown as a rack of what is currently running rather than as a control beside
 * every parameter. There are five parameters on a scatter and four macros, and
 * a shape/period/range picker under each one would bury the sliders that do the
 * work. Binding is one button; the rack is where you see and stop them.
 */
export function ModulationCard({ state, send, slot }: {
  state: EngineState; send: (c: Command) => void; slot: Slot;
}) {
  const [pick, setPick] = useState<{ look?: string; param: string } | null>(null);
  const running = state.modulators ?? [];
  const shapes = MODULATOR_SHAPES;

  // Everything bindable on this tab: the loaded routines' own numeric
  // parameters, plus — on Move — the four shape macros.
  const byName = new Map(state.looks.map((l) => [l.name, l]));
  const loaded = [...new Set(Object.values(state.selection[slot] ?? {}))]
    .map((n) => byName.get(n))
    .filter((l): l is LookInfo => Boolean(l?.block));

  const targets: { look?: string; param: string; label: string }[] = [];
  if (slot === "movement") {
    for (const spec of MACRO_PARAMS) {
      if (spec.kind === "number" || spec.kind === "integer") {
        targets.push({ param: spec.name, label: `Shape · ${spec.label}` });
      }
    }
  }
  for (const look of loaded) {
    for (const spec of BLOCK_PARAMS[look.block!] ?? []) {
      // Only numbers can be modulated — there is no halfway between two
      // choices, and the engine refuses it rather than rounding.
      if (spec.kind === "number" || spec.kind === "integer") {
        targets.push({ look: look.name, param: spec.name,
                       label: `${look.name} · ${spec.label}` });
      }
    }
  }

  const mine = running.filter((m) =>
    targets.some((t) => t.look === m.look && t.param === m.param));

  if (targets.length === 0) return null;

  const labelFor = (m: ModulatorSpec) =>
    targets.find((t) => t.look === m.look && t.param === m.param)?.label
    ?? m.param;

  return (
    <Card title="Modulation" right={
      <button className="small" disabled={running.length === 0}
              aria-label="stop all modulation"
              onClick={() => send({ type: "modulate_clear", all: true })}>
        Stop all
      </button>
    }>
      {mine.length === 0 ? (
        <p className="small muted" style={{ marginTop: 0 }}>
          Nothing is moving on its own. Bind a parameter to a waveform and it
          swings between the ends of its own range — a size that breathes over
          sixteen bars, a radius that tracks the room's energy.
        </p>
      ) : (
        <div style={{ marginBottom: "0.6rem" }}>
          {mine.map((m) => (
            <div key={`${m.look ?? ""}:${m.param}`} className="row tight">
              <span className="small grow">{labelFor(m)}</span>
              <span className="small muted mono">
                {m.shape}
                {m.shape !== "energy" && ` · ${m.bars}b`}
                {` · ${m.low}–${m.high}`}
              </span>
              <button className="small"
                      aria-label={`stop modulating ${labelFor(m)}`}
                      onClick={() => send({ type: "modulate_clear",
                                            look: m.look, param: m.param })}>
                Stop
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="pills" role="group" aria-label="parameter to modulate">
        {targets.map((t) => (
          <button key={`${t.look ?? ""}:${t.param}`}
                  className={pick && pick.look === t.look
                             && pick.param === t.param ? "on" : ""}
                  onClick={() => setPick(pick && pick.look === t.look
                                         && pick.param === t.param
                                         ? null : { look: t.look, param: t.param })}>
            {t.label}
          </button>
        ))}
      </div>

      {pick && (
        <>
          <p className="small muted" style={{ marginBottom: "0.3rem" }}>
            {/* No range here on purpose: with none given the engine sweeps the
                target's whole declared range, which is the sensible default and
                means binding one is a single tap. */}
            Pick a waveform. It will swing across the parameter's full range;
            tune the ends afterwards by stopping it and setting them by hand.
          </p>
          <div className="pills" role="group" aria-label="waveform">
            {shapes.map((shape) => (
              <button key={shape}
                      onClick={() => {
                        send({ type: "modulate", look: pick.look,
                               param: pick.param, shape, bars: 16 });
                        setPick(null);
                      }}>
                {shape}
              </button>
            ))}
          </div>
        </>
      )}
    </Card>
  );
}

export function anyChanged(specs: ParamSpec[],
                           values: Record<string, ParamValue>): boolean {
  return specs.some((spec) => {
    const value = values[spec.name];
    if (value === undefined) return false;
    if (Array.isArray(value) && Array.isArray(spec.default)) {
      return value.some((c, i) => c !== (spec.default as RGB)[i]);
    }
    return value !== spec.default;
  });
}
