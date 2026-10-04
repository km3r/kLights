import { useMemo, useState } from "react";
import { Card, RateCard, rgbCss } from "../components";
import { DesignOnly } from "../mode";
import { LookPicker } from "../LookPicker";
import { ModulationCard, TweakCard } from "../Params";
import type { Command, EngineState, RGB } from "../types";

/**
 * Colour: a global quick palette plus a per-fixture picker.
 *
 * Both were asked for, and they are the same command with a different target —
 * so a colour set here is an OVERRIDE layer, not a rewritten look. That is what
 * lets a colour picked by hand survive an auto-mode look change, which is the
 * behaviour that makes "everyone controls everything" workable.
 *
 * The movers have a 14-slot mechanical wheel and no colour mixing, so the
 * engine snaps their colour to the nearest slot. The picker still shows the
 * exact colour you chose; the fixture card shows what it actually became.
 */
export function ColorTab({ state, send }: {
  state: EngineState; send: (c: Command) => void;
}) {
  const [target, setTarget] = useState<string>("all");

  // Groups and individual fixtures, kept APART. They used to be one flat row of
  // buttons, which is linear in fixture count — fine for six, unusable for
  // forty, and the review's finding #17. Groups are what anyone reaches for;
  // one head at a time is the exception, so it goes behind a disclosure.
  const groups = useMemo(() => {
    const tags = new Set<string>();
    state.fixtures.forEach((f) => f.tags.forEach((t) => tags.add(t)));
    return ["all", ...Array.from(tags).sort()];
  }, [state.fixtures]);

  const applied = state.color_overrides[target];
  const appliedWhite = state.white_overrides[target];
  // Opened by default when the current target IS a fixture, so a colour set on
  // one head does not appear to have been forgotten after a reload.
  const single = !groups.includes(target);

  // Whether ANY fixture the current target reaches has a white channel at
  // all — a tag or "all" can span RGB-only and RGBW fixtures at once, and the
  // white control should only appear when it would do something.
  const targetHasWhite = useMemo(() => {
    if (target === "all") return state.fixtures.some((f) => f.has_white);
    if (groups.includes(target)) {
      return state.fixtures.some((f) => f.tags.includes(target) && f.has_white);
    }
    return state.fixtures.find((f) => f.name === target)?.has_white ?? false;
  }, [target, groups, state.fixtures]);

  const swatch = (t: string) => (
    <button key={t} className={target === t ? "on" : ""} onClick={() => setTarget(t)}>
      {t}
      {state.color_overrides[t] && (
        <span className="chip" style={{
          marginLeft: 4,
          background: rgbCss(state.color_overrides[t]),
          borderColor: "transparent",
        }} />
      )}
    </button>
  );

  return (
    <>
      <LookPicker state={state} send={send} slot="color" title="Colour look"
                  empty="Nothing loaded — colour comes from the palette." />

      {/* The loaded routine's own knobs. Renders nothing when the slot
          holds only ported looks, which have no parameters to turn. */}
      <TweakCard state={state} send={send} slot="color" />

      <ModulationCard state={state} send={send} slot="color" />

      <RateCard state={state} send={send} slot="color" hint={
        <>
          How fast a colour chase steps, independently of the move underneath
          it. A slow colour drift under a fast sweep is the combination the old
          library needed a separately stored chase for. Only affects stepped
          colour looks — a held colour has nothing to step.
        </>
      } />

      <Card title="Applies to">
        <div className="grid small">{groups.map(swatch)}</div>
        <details open={single} style={{ marginTop: "0.5rem" }}>
          <summary className="small muted">
            One fixture at a time ({state.fixtures.length})
          </summary>
          <div className="grid small" style={{ marginTop: "0.4rem" }}>
            {state.fixtures.map((f) => swatch(f.name))}
          </div>
        </details>
      </Card>

      <Card title="Quick palette" right={
        // Present always, disabled with nothing overridden. Appearing the
        // instant a swatch is tapped, it made the heading taller and pushed the
        // palette down — out from under the finger that had just tapped it.
        <button className="small" disabled={!applied && appliedWhite === undefined}
                onClick={() => send({ type: "color", target, color: [1, 1, 1], clear: true })}>
          Clear
        </button>
      }>
        <div className="grid small">
          {state.palette.map((c, i) => (
            <button key={i}
                    className={state.palette_index === i ? "swatch on" : "swatch"}
                    style={{ background: rgbCss(c) }}
                    aria-label={`palette ${i}`}
                    // Tap sets the target's colour; long-press makes it the
                    // palette's own current entry, which is what auto-rotation
                    // then advances from. `palette_select` had a handler since
                    // F7 and no sender, so the palette could only be advanced
                    // by the timer.
                    onContextMenu={(e) => {
                      e.preventDefault();
                      send({ type: "palette_select", index: i });
                    }}
                    onClick={() => send({ type: "color", target, color: c })}>
              {state.palette_index === i && <span className="dot" />}
            </button>
          ))}
        </div>
        <p className="small muted" style={{ marginBottom: 0 }}>
          Tap sets <b>{target}</b>. Auto palette rotation drives the look's own
          colour; anything set here overrides it until cleared.
        </p>
      </Card>

      <Picker target={target} send={send} current={applied}
              hasWhite={targetHasWhite} currentWhite={appliedWhite} />

      {/* A readout, not a control — it changes nothing, so Perform mode does
          without it. The information is still one tap away in Design. */}
      <DesignOnly>
        <Card title="What the fixtures are actually doing">
          <div className="grid two">
            {state.fixtures.map((f) => (
              <div key={f.id} className="fixture">
                <div className="name">
                  <span className="grow">{f.name}</span>
                  <span className="chip" style={{
                    background: rgbCss(f.color), borderColor: "transparent",
                    minWidth: 22,
                  }} />
                </div>
                <div className="small muted">
                  {f.is_mover ? "colour wheel — snapped to nearest slot"
                    : f.has_white ? "RGBW" : "RGB"}
                </div>
              </div>
            ))}
          </div>
        </Card>
      </DesignOnly>
    </>
  );
}

function Picker({ target, send, current, hasWhite, currentWhite }: {
  target: string; send: (c: Command) => void; current?: RGB;
  hasWhite: boolean; currentWhite?: number;
}) {
  const [hue, setHue] = useState(210);
  const [sat, setSat] = useState(1);
  const [val, setVal] = useState(1);
  const [white, setWhite] = useState(0);
  const rgb = hsvToRgb(hue, sat, val);

  return (
    <Card title={`Picker — ${target}`}>
      <div style={{
        height: 48, borderRadius: 12, marginBottom: "0.6rem",
        background: rgbCss(rgb), border: "1px solid var(--line)",
      }} />
      <label className="field">
        Hue
        <input type="range" min={0} max={360} value={hue}
               onChange={(e) => setHue(Number(e.target.value))}
               style={{
                 background: "linear-gradient(90deg,#f00,#ff0,#0f0,#0ff,#00f,#f0f,#f00)",
                 borderRadius: 6,
               }} />
      </label>
      <label className="field">
        Saturation
        <input type="range" min={0} max={1} step={0.01} value={sat}
               onChange={(e) => setSat(Number(e.target.value))} />
      </label>
      <label className="field">
        Brightness
        <input type="range" min={0} max={1} step={0.01} value={val}
               onChange={(e) => setVal(Number(e.target.value))} />
      </label>
      {hasWhite && (
        // Only shown when the target reaches at least one RGBW fixture — an
        // RGB-only fixture has no channel this could ever move, so a visible
        // slider there would be a control that lies about doing something.
        <label className="field">
          White
          <input type="range" min={0} max={1} step={0.01} value={white}
                 onChange={(e) => setWhite(Number(e.target.value))} />
        </label>
      )}
      <div className="row" style={{ marginTop: "0.5rem" }}>
        <button className="on" style={{ flex: 1 }}
                onClick={() => send({
                  type: "color", target, color: rgb,
                  ...(hasWhite ? { white } : {}),
                })}>
          Apply to {target}
        </button>
      </div>
      {current && (
        <p className="small muted" style={{ marginBottom: 0 }}>
          Currently overridden to <span className="mono">
            {current.map((c) => c.toFixed(2)).join(", ")}
          </span>
          {currentWhite !== undefined && (
            <>, white <span className="mono">{currentWhite.toFixed(2)}</span></>
          )}.
        </p>
      )}
    </Card>
  );
}

function hsvToRgb(h: number, s: number, v: number): RGB {
  const c = v * s;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = v - c;
  const [r, g, b] =
    h < 60 ? [c, x, 0] : h < 120 ? [x, c, 0] : h < 180 ? [0, c, x] :
    h < 240 ? [0, x, c] : h < 300 ? [x, 0, c] : [c, 0, x];
  return [r + m, g + m, b + m];
}
