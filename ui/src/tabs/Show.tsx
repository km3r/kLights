import { useRef, useState } from "react";
import { Card, Toggle } from "../components";
import type { Command, EngineState } from "../types";

export function ShowTab({ state, send }: {
  state: EngineState; send: (c: Command) => void;
}) {
  const auto = state.auto;

  return (
    <>
      <Card title="Looks" right={
        auto.held ? <button className="small" onClick={() => send({ type: "release" })}>
          Release hold
        </button> : undefined
      }>
        <div className="grid tiles">
          {state.looks.map((l) => (
            <button key={l.name}
                    className={auto.look === l.name ? "on" : ""}
                    onClick={() => send({ type: "select_look", name: l.name })}>
              {l.name}
              {l.manual_only && <div className="small muted">manual only</div>}
            </button>
          ))}
        </div>
        <p className="small muted" style={{ marginBottom: 0 }}>
          {auto.held
            ? "Held — auto mode will not change this until you release."
            : `Auto changes on ${auto.last_change}.`}
        </p>
      </Card>

      <Tempo state={state} send={send} />

      <Card title="Auto">
        <div className="grid two">
          <Toggle label="Look changes"
                  hint={state.clock.phrase_measured
                    ? "on phrase boundaries" : "on bars — phrase is counted"}
                  on={auto.axes.look_changes}
                  onChange={(on) => send({ type: "auto", axis: "look_changes", on })} />
          <Toggle label="Palette" hint="rotate colour over time"
                  on={auto.axes.palette}
                  onChange={(on) => send({ type: "auto", axis: "palette", on })} />
          <Toggle label="Energy" hint="drives level, rate and strobe"
                  on={auto.axes.energy}
                  onChange={(on) => send({ type: "auto", axis: "energy", on })} />
        </div>

        {!state.clock.phrase_measured && auto.axes.look_changes && (
          <p className="small muted">
            No source is supplying phrase, so phrase position is counted from
            your last downbeat. Changes land on bars instead — one bar early is
            a small error, half a phrase out is a visible one.
          </p>
        )}

        {auto.axes.energy && <Energy state={state} send={send} />}

        <div className="spread small muted" style={{ marginTop: "0.5rem" }}>
          <span>{auto.changes} look change(s), {auto.palette_changes} palette</span>
          <span className="mono">rate {auto.rate.toFixed(2)}×</span>
        </div>
      </Card>
    </>
  );
}

/**
 * Tempo, tap and live speed nudge.
 *
 * Speed is deliberately separate from tempo: tempo is what the music is doing,
 * speed is what you want the lights to do about it. Both re-anchor the timeline
 * before changing rate, so neither jumps the beat — you can nudge mid-look
 * without anything snapping.
 */
function Tempo({ state, send }: { state: EngineState; send: (c: Command) => void }) {
  const [bpmDraft, setBpmDraft] = useState<string | null>(null);
  const lastTap = useRef(0);

  const tap = () => {
    lastTap.current = Date.now();
    send({ type: "tap" });
  };

  return (
    <Card title="Tempo" right={<span className="small muted">{state.clock.source}</span>}>
      <div className="row">
        <button style={{ flex: "1 1 140px", minHeight: 64, fontSize: 18 }} onClick={tap}>
          TAP
          <div className="small muted">{state.clock.taps} tap(s)</div>
        </button>
        <div style={{ flex: "1 1 140px" }}>
          <label className="field">
            BPM
            <input type="number" min={40} max={250} step={0.5}
                   value={bpmDraft ?? state.clock.bpm.toFixed(1)}
                   onChange={(e) => setBpmDraft(e.target.value)}
                   onBlur={() => {
                     const v = Number(bpmDraft);
                     if (bpmDraft !== null && v >= 40 && v <= 250) {
                       send({ type: "bpm", value: v });
                     }
                     setBpmDraft(null);
                   }} />
          </label>
        </div>
      </div>

      <div className="row" style={{ marginTop: "0.5rem" }}>
        <button onClick={() => send({ type: "downbeat" })}>Downbeat</button>
        <button onClick={() => send({ type: "nudge_phase", beats: -0.25 })}>← nudge</button>
        <button onClick={() => send({ type: "nudge_phase", beats: 0.25 })}>nudge →</button>
      </div>

      <div className="row" style={{ marginTop: "0.5rem" }}>
        <span className="small muted" style={{ minWidth: "3.5em" }}>Speed</span>
        {[0.25, 0.5, 1, 2, 4].map((s) => (
          <button key={s}
                  className={Math.abs(state.clock.speed - s) < 0.01 ? "on" : ""}
                  onClick={() => send({ type: "speed", value: s })}>
            {s}×
          </button>
        ))}
      </div>
      <p className="small muted" style={{ marginBottom: 0 }}>
        Bar <span className="mono">{state.clock.bar.toFixed(2)}</span>,
        phrase <span className="mono">{state.clock.phrase.toFixed(2)}</span>
        {state.clock.phrase_measured ? " (measured)" : " (counted)"}
      </p>
    </Card>
  );
}

function Energy({ state, send }: { state: EngineState; send: (c: Command) => void }) {
  const [manual, setManual] = useState(0.5);
  return (
    <div style={{ marginTop: "0.6rem" }}>
      <div className="row tight">
        <button onClick={() => send({ type: "energy", source: "phrase" })}>
          From phrase
        </button>
        <button onClick={() => send({ type: "energy", source: "manual", value: manual })}>
          Manual
        </button>
        <span className="small mono muted">now {state.auto.energy.toFixed(2)}</span>
      </div>
      <input type="range" min={0} max={1} step={0.01} value={manual}
             style={{ width: "100%" }}
             onChange={(e) => {
               const v = Number(e.target.value);
               setManual(v);
               send({ type: "energy", source: "manual", value: v });
             }} />
      <p className="small muted" style={{ margin: 0 }}>
        Phrase energy is a guess about the music, not a measurement of it — it
        will be confidently wrong through a long breakdown. Manual is the only
        source that knows what the room is doing.
      </p>
    </div>
  );
}
