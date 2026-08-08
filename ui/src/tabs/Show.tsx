import { useState } from "react";
import { Card, Toggle } from "../components";
import type { Command, EngineState } from "../types";

/**
 * Show-level controls: what the whole rig is doing, not what any one part of it
 * looks like.
 *
 * Deliberately holds no look pickers. Colour, movement and level each live on
 * their own tab because they are independent slots — mixing them back in here
 * would rebuild the flat list the split was meant to retire. What is left is the
 * stuff that applies across all three: the clock, the automation, and presets
 * for recalling a whole picture at once.
 */
export function ShowTab({ state, send }: {
  state: EngineState; send: (c: Command) => void;
}) {
  return (
    <>
      <Now state={state} send={send} />
      <Presets state={state} send={send} />
      <Tempo state={state} send={send} />
      <Auto state={state} send={send} />
    </>
  );
}

const GROUP_LABELS: Record<string, string> = {
  "corner movers": "Movers", movers: "Movers", pinspots: "Pinspots",
  pars: "Pars", bars: "Bars",
};
const groupLabel = (g: string) => GROUP_LABELS[g] ?? g;

/**
 * What is currently loaded, and where to go to change it.
 *
 * One row per slot per fixture group, because a pinspot colour and a mover
 * colour are separate selections — collapsing them to one line would hide the
 * fact that both are up.
 */
function Now({ state, send }: { state: EngineState; send: (c: Command) => void }) {
  const slots: [string, string, string][] = [
    ["Move", "movement", "#move"],
    ["Colour", "color", "#color"],
    ["Bright", "level", "#bright"],
  ];
  type Row = { key: string; label: string; value: string | null; href: string };
  const rows: Row[] = slots.flatMap(([label, slot, href]): Row[] => {
    const loaded = state.selection[slot as keyof typeof state.selection] ?? {};
    const groups = state.groups.filter((g) => loaded[g]);
    if (groups.length === 0) return [{ key: label, label, value: null, href }];
    return groups.map((g) => ({
      key: `${label}:${g}`,
      // Only name the fixture type when there is more than one to confuse.
      label: state.groups.length > 1 ? `${label} · ${groupLabel(g)}` : label,
      value: loaded[g]!, href,
    }));
  });

  return (
    <Card title="On now" right={
      state.auto.held ? (
        <button className="small" onClick={() => send({ type: "release" })}>
          Release hold
        </button>
      ) : undefined
    }>
      <div className="grid two">
        {rows.map((r) => (
          <a key={r.key} href={r.href} className="slot">
            <span className="small muted">{r.label}</span>
            <span className={r.value ? "" : "muted"}>{r.value ?? "—"}</span>
          </a>
        ))}
      </div>
      <p className="small muted" style={{ margin: "0.6rem 0 0" }}>
        Every row is independent — changing one leaves the rest alone, including
        across fixture types.
      </p>
    </Card>
  );
}

/**
 * Named combinations of all three slots.
 *
 * The cost of making the slots independent is that a picture you liked takes
 * three taps to rebuild and is easy to lose. A preset is the answer: it stores
 * what each slot held, plus the speed and master it was built at.
 */
function Presets({ state, send }: { state: EngineState; send: (c: Command) => void }) {
  const [name, setName] = useState("");
  const save = () => {
    if (!name.trim()) return;
    send({ type: "preset_save", name: name.trim() });
    setName("");
  };

  return (
    <Card title={`Presets — ${state.presets.length}`}>
      {state.presets.length > 0 && (
        <div className="grid tiles">
          {state.presets.map((p) => (
            <button key={p.name} onClick={() => send({ type: "preset_apply", name: p.name })}>
              {p.name}
              <div className="small muted">
                {[p.movement, p.color, p.level]
                  .reduce((n, m) => n + Object.keys(m ?? {}).length, 0)} slot(s)
              </div>
            </button>
          ))}
        </div>
      )}

      <div className="row" style={{ marginTop: state.presets.length ? "0.6rem" : 0 }}>
        <input className="field" placeholder="name this picture…" value={name}
               aria-label="preset name"
               onChange={(e) => setName(e.target.value)}
               onKeyDown={(e) => { if (e.key === "Enter") save(); }}
               style={{
                 flex: "1 1 10rem", minWidth: 0, padding: "0.55rem", minHeight: 44,
                 background: "var(--panel-2)", border: "1px solid var(--line)",
                 borderRadius: 8,
               }} />
        <button onClick={save} disabled={!name.trim()}>Save</button>
      </div>

      {state.presets.length > 0 && (
        <details style={{ marginTop: "0.5rem" }}>
          <summary className="small muted">Delete a preset</summary>
          <div className="row" style={{ marginTop: "0.4rem" }}>
            {state.presets.map((p) => (
              <button key={p.name} className="small danger"
                      onClick={() => send({ type: "preset_delete", name: p.name })}>
                {p.name} ✕
              </button>
            ))}
          </div>
        </details>
      )}
      <p className="small muted" style={{ marginBottom: 0 }}>
        Saves the move, colour and level that are up now, with the speed and
        master. Stored with the event, so it survives a restart.
      </p>
    </Card>
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

  return (
    <Card title="Tempo" right={<span className="small muted">{state.clock.source}</span>}>
      <div className="row">
        <button style={{ flex: "1 1 8rem", minHeight: 64, fontSize: 18 }}
                onClick={() => send({ type: "tap" })}>
          TAP
          <div className="small muted">{state.clock.taps} tap(s)</div>
        </button>
        <div style={{ flex: "1 1 8rem", minWidth: 0 }}>
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

function Auto({ state, send }: { state: EngineState; send: (c: Command) => void }) {
  const auto = state.auto;
  return (
    <Card title="Auto">
      <div className="grid two">
        <Toggle label="Timing" hint="movement follows the clock"
                on={auto.axes.timing}
                onChange={(on) => send({ type: "auto", axis: "timing", on })} />
        <Toggle label="Move changes"
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

      {!auto.axes.timing && (
        <p className="small muted">
          Timing is off, so movement is frozen where it stands. Musical position
          keeps running underneath — turning it back on resumes rather than
          snapping forward.
        </p>
      )}
      {!state.clock.phrase_measured && auto.axes.look_changes && (
        <p className="small muted">
          No source is supplying phrase, so phrase position is counted from your
          last downbeat. Changes land on bars instead — one bar early is a small
          error, half a phrase out is a visible one.
        </p>
      )}

      {auto.axes.energy && <Energy state={state} send={send} />}

      <div className="spread small muted" style={{ marginTop: "0.5rem" }}>
        <span>{auto.changes} move change(s), {auto.palette_changes} palette</span>
        <span className="mono">rate {auto.rate.toFixed(2)}×</span>
      </div>
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
             aria-label="manual energy"
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
