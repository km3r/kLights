import { useState } from "react";
import { Card, Fader } from "../components";
import { LookPicker } from "../LookPicker";
import type { Command, EngineState } from "../types";

const GROUP_LABELS: Record<string, string> = {
  "corner movers": "Movers", movers: "Movers", pinspots: "Pinspots",
  pars: "Pars", bars: "Bars",
};
const groupLabel = (g: string) => GROUP_LABELS[g] ?? g;

/**
 * Hand dimming, per group and per fixture.
 *
 * Distinct from the Bright pattern above it, and deliberately so: a pattern is
 * a stored routine, this is the operator saying "that head is too hot right
 * now", which no stored look can anticipate. It is a MULTIPLIER over whatever
 * the pattern is doing, so the routine keeps running underneath rather than
 * being cancelled by the trim.
 *
 * The full chain, in the order it applies:
 *
 *     pattern  x  this trim  x  master  x  safety taper
 *
 * Like the colour picker, a trim is an override layer, so it survives an
 * auto-mode look change — a level set by hand should not be undone by the
 * timer.
 */
function Dimmers({ state, send }: { state: EngineState; send: (c: Command) => void }) {
  // Local only while a finger is down, same as the master fader: a value
  // replaced by a broadcast mid-drag jumps under the thumb.
  const [drag, setDrag] = useState<Record<string, number>>({});
  const level = (t: string) => drag[t] ?? state.level_overrides[t] ?? 1;
  const set = (t: string, v: number) => {
    setDrag((d) => ({ ...d, [t]: v }));
    send({ type: "level", target: t, value: v });
  };
  const commit = (t: string) => setDrag(({ [t]: _gone, ...rest }) => rest);
  const clear = (t: string) => {
    commit(t);
    send({ type: "level", target: t, clear: true });
  };

  const rows: { target: string; label: string; sub?: string }[] = [
    ...state.groups.map((g) => ({ target: g, label: groupLabel(g) })),
    ...state.fixtures.map((f) => ({
      target: f.name, label: f.name,
      sub: `now at ${Math.round((f.intensity ?? 0) * 100)}%`,
    })),
  ];
  const trimmed = Object.keys(state.level_overrides);

  return (
    <Card title="Dimmers" right={
      // Present always, disabled when there is nothing to reset. Rendered
      // conditionally it grew the heading the instant the first fader moved,
      // and the whole card stepped down the page under the finger.
      <button className="small" disabled={trimmed.length === 0}
              onClick={() => trimmed.forEach(clear)}>
        Reset all
      </button>
    }>
      {rows.map((r, i) => (
        <div key={r.target} className="dimmer"
             style={i === state.groups.length ? { marginTop: "0.6rem" } : undefined}>
          <div className={i < state.groups.length ? "" : "small"}>
            {r.label}
            {r.sub && <span className="small muted"> · {r.sub}</span>}
          </div>
          <div className="dimmer-row">
            <Fader value={level(r.target)} label={`${r.label} level`}
                   onInput={(v) => set(r.target, v)}
                   onCommit={() => commit(r.target)} />
            {/* Hidden rather than absent: the row is a fixed height whether or
                not this is showing, so nothing moves at the moment a trim
                starts existing. Inline because the reserved space has to hold
                in the tests too, where the stylesheet is not loaded. */}
            <button className="small reset"
                    style={{ visibility: state.level_overrides[r.target] != null
                               ? "visible" : "hidden" }}
                    onClick={() => clear(r.target)}>
              reset
            </button>
          </div>
        </div>
      ))}
      <p className="small muted" style={{ marginBottom: 0 }}>
        A trim over whatever the pattern is doing, not a replacement for it —
        pattern × trim × master × safety, in that order. Reset a row to hand it
        back to the pattern.
      </p>
    </Card>
  );
}

/**
 * Bright: brightness patterns, as a multiplier over whatever else is running.
 *
 * Its own tab because it is its own slot. A level pattern does not replace the
 * colour or the position — it scales them, and is then scaled itself by the
 * master and the safety taper. That is why "Spotlight" can rotate a bright head
 * around the room while the colour you picked stays exactly as you picked it.
 *
 * The chases here were unreachable until the porter learned about intensity
 * chasers: "Spotlight", "Dim Chase" and "Crowd Cascade" were skipped and only
 * their individual steps survived, which is why the levels used to read as a
 * pile of disconnected one-shot buttons.
 */
export function BrightTab({ state, send }: {
  state: EngineState; send: (c: Command) => void;
}) {
  const lit = state.fixtures.filter((f) => (f.intensity ?? 0) > 0.001);
  const policy = state.strobe_policy;
  const limited = policy.ceiling < 1 || policy.max_seconds > 0;

  return (
    <>
      <LookPicker state={state} send={send} slot="level" title="Bright pattern"
                  empty="Nothing loaded — every fixture is at its full level." />

      {/* Strobe is the one genuine medical risk on this rig — photosensitive
          epilepsy, which no aversion response protects anyone from — so the
          policy is stated where strobe is visible rather than left in a config
          file nobody opens. Read-only: it is a property of the room and the
          crowd in it, set per venue, not a control to reach for mid-set. */}
      <Card title="Strobe policy">
        <p className="small muted" style={{ marginBottom: 0 }}>
          {!policy.enabled ? (
            "Blocked entirely — nothing can drive the shutter."
          ) : limited ? (
            <>
              {policy.ceiling < 1 && (
                <>Capped at <b>{Math.round(policy.ceiling * 100)}%</b> of each
                  fixture&apos;s slow-to-fast band. </>
              )}
              {policy.max_seconds > 0 && (
                <>Cut off after <b>{policy.max_seconds}s</b> continuous, then
                  held open before it can restart. </>
              )}
              Not a frequency limit — the fixture profiles declare no Hz, so
              this caps a band position and a duration instead.
            </>
          ) : (
            <b>UNLIMITED — no ceiling and no duration cap. Photosensitive
              epilepsy is a real risk; set a policy in the venue file.</b>
          )}
        </p>
      </Card>

      <Dimmers state={state} send={send} />

      <Card title="What each fixture is actually at">
        <div className="grid two">
          {state.fixtures.map((f) => {
            const level = f.intensity ?? 0;
            const taper = f.safety?.taper ?? 1;
            return (
              <div key={f.id} className="fixture">
                <div className="name">
                  <span className="grow">{f.name}</span>
                  <span className="chip mono">{Math.round(level * 100)}%</span>
                </div>
                <div className="bar">
                  <i style={{ width: `${Math.round(level * 100)}%` }}
                     className={taper < 1 ? "taper" : ""} />
                </div>
                {taper < 1 && (
                  <div className="small muted">
                    safety taper is holding this at {Math.round(taper * 100)}%
                  </div>
                )}
                {f.strobe != null && f.strobe > 0 && (
                  <div className="small" style={{ color: "var(--warn)" }}>
                    strobing at {Math.round(f.strobe * 100)}% of its range
                  </div>
                )}
              </div>
            );
          })}
        </div>
        <p className="small muted" style={{ marginBottom: 0 }}>
          {lit.length} of {state.fixtures.length} lit. This is the final number
          after the level pattern, the master and the safety taper have all been
          applied — so it is what the fixture is really doing.
        </p>
      </Card>
    </>
  );
}
