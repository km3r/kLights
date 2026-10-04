import { useState } from "react";
import { Banner, Card } from "../components";
import { PatchSection } from "./Patch";
import { RigSection } from "./Rig";
import { VenueSection } from "./Venue";
import type { Command, EngineState, FixtureState } from "../types";

/**
 * The two capture targets that actually pin down handedness.
 *
 * The floor beneath each ADJACENT corner sits about 90° either side of the ball
 * from a head in a corner, which spans the room's whole usable bearing range.
 * The obvious targets — ball, far corner, far wall — are all roughly the same
 * direction from a corner head and span under 20°, which determines nothing.
 */
function corners(head: FixtureState | undefined, state: EngineState) {
  const p = head?.position;
  const w = state.venue.width, d = state.venue.depth;
  if (!p || w == null || d == null) return [];
  return [
    { label: "corner across", target: [p[0], 0, d - p[2]] },
    { label: "corner along wall", target: [w - p[0], 0, p[2]] },
  ];
}

/** A head's name as the Jog card's buttons show it. */
const short = (name: string) => name.replace("Moving Head ", "MH ");

/**
 * Everything about the room and the rig, in one place.
 *
 * Venue, calibration and patch were three separate tabs, which was three places
 * to look for one job: getting set up. They are one tab now, in the order the
 * work actually happens — who is on the desk, what the room is, aim the heads,
 * then the read-only patch and engine health at the bottom for when something
 * is wrong.
 *
 * Calibration here is what retires the Tkinter form and its workflow — eyeball
 * each head at the ball, read the faders, type eight numbers in, re-run a build
 * script, restart QLC+. The heads got nudged overnight at despacio and
 * recalibrating was slow and manual.
 *
 * Jog BYPASSES THE SAFETY TAPER, necessarily: the taper works from the aim, the
 * aim comes from the geometry, and the geometry is exactly what has not been
 * established yet. The app-wide banner says so whenever a head is jogging.
 */
export function SetupTab({ state, send, name, setName }: {
  state: EngineState; send: (c: Command) => void;
  name: string; setName: (n: string) => void;
}) {
  const movers = state.fixtures.filter((f) => f.is_mover);
  const [selected, setSelected] = useState(movers[0]?.name ?? "");
  const [pan, setPan] = useState(128);
  const [tilt, setTilt] = useState(128);

  const ball = state.venue.ball ?? [0, 0, 0];
  const head = state.fixtures.find((f) => f.name === selected);
  // What the drift check reads, in the engine's order. `head` is the index into
  // the rig's geometry, which is narrower than is_mover: a mover with no
  // position is not in it, and the engine does not ask for its reading.
  const heads = state.fixtures.filter((f) => f.head != null)
    .sort((a, b) => a.head! - b.head!);
  const unaimed = heads.filter((f) => !f.jogging);

  const jog = (p: number, t: number) => {
    const np = Math.max(0, Math.min(255, p));
    const nt = Math.max(0, Math.min(255, t));
    setPan(np); setTilt(nt);
    send({ type: "jog", fixture: selected, pan: np, tilt: nt });
  };

  return (
    <>
      <Card title="Who you are">
        <label className="field">
          Shown to everyone else on the rig
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <div className="presence" style={{ marginTop: "0.6rem" }}>
          {state.presence.map((p) => (
            <span key={p.id} className="peer">
              <b>{p.name}</b>
              {p.last_action && <span className="muted"> · {p.last_action}</span>}
            </span>
          ))}
        </div>
        <p className="small muted" style={{ marginBottom: 0 }}>
          Everyone controls everything — there is no locking. This is so you can
          see each other, which is how two people on a desk actually works.
        </p>
      </Card>

      <VenueSection state={state} send={send} />

      <Card title="Jog" right={
        <button className="small" onClick={() => send({ type: "jog_clear" })}>
          Stop all
        </button>
      }>
        <Banner kind="bad">
          Jog disables the safety taper for that head. Empty room only.
        </Banner>

        <div className="grid small" style={{ marginTop: "0.6rem" }}>
          {movers.map((f) => (
            <button key={f.id} className={selected === f.name ? "on" : ""}
                    onClick={() => setSelected(f.name)}>
              {short(f.name)}
              {f.captures ? <div className="small muted">{f.captures} cap</div> : null}
            </button>
          ))}
        </div>

        <div className="row" style={{ marginTop: "0.75rem" }}>
          <span className="small muted" style={{ minWidth: "2.6em" }}>Pan</span>
          {[-10, -1].map((d) => (
            <button key={d} onClick={() => jog(pan + d, tilt)}>{d}</button>
          ))}
          <span className="mono" style={{ minWidth: "2.5em", textAlign: "center" }}>{pan}</span>
          {[1, 10].map((d) => (
            <button key={d} onClick={() => jog(pan + d, tilt)}>+{d}</button>
          ))}
        </div>
        <input type="range" min={0} max={255} value={pan} style={{ width: "100%" }}
               onChange={(e) => jog(Number(e.target.value), tilt)} />

        <div className="row">
          <span className="small muted" style={{ minWidth: "2.6em" }}>Tilt</span>
          {[-10, -1].map((d) => (
            <button key={d} onClick={() => jog(pan, tilt + d)}>{d}</button>
          ))}
          <span className="mono" style={{ minWidth: "2.5em", textAlign: "center" }}>{tilt}</span>
          {[1, 10].map((d) => (
            <button key={d} onClick={() => jog(pan, tilt + d)}>+{d}</button>
          ))}
        </div>
        <input type="range" min={0} max={255} value={tilt} style={{ width: "100%" }}
               onChange={(e) => jog(pan, Number(e.target.value))} />
      </Card>

      <Card title="Capture" help={<>
        <p>A capture records where a head is pointing and which point in the room
          it's aimed at. Two or more, from different angles, let the solver work
          out the head's calibration.</p>
        <p><b>Solve (preview)</b> shows the result in <b>Notices</b>. The residual
          is how much the captures disagree, in degrees.</p>
        <p><b>Solve &amp; write</b> saves it and snapshots the old calibration. It
          won't save a head with a residual over 5°, which usually means a capture
          was taken before the head was jogged. Restart the engine to load it.</p>
      </>} right={
        <button className="small" onClick={() => send({ type: "capture_clear" })}>
          Clear all
        </button>
      }>
        <p className="small muted" style={{ marginTop: 0 }}>
          Aim <b>{selected}</b> at each target by eye, then capture. Three
          targets spanning bearing <i>and</i> elevation let the solver work out
          the invert flags instead of you guessing them — two aims at the same
          bearing determine nothing, and it will say so.
        </p>
        {/* A capture records where the head IS, and that number only exists
            once the head is being jogged. Capturing before then used to record
            (0, 0) silently, which the solver then fitted. Disabled rather than
            rejected on arrival, so the reason is visible before the press. */}
        {!head?.jogging && (
          <Banner kind="warn">
            {/* One span: a banner is a flex row, and bare text either side of
                the <b> would lay out as three columns. */}
            <span>
              Jog <b>{selected}</b> onto the target first — a capture records
              where the head is pointing, and until you move it there is
              nothing to record.
            </span>
          </Banner>
        )}
        <div className="grid two">
          <button disabled={!head?.jogging} onClick={() => send({
            type: "capture", fixture: selected, target: ball, label: "mirror ball",
          })}>Ball</button>
          {corners(head, state).map((c) => (
            <button key={c.label} disabled={!head?.jogging} onClick={() => send({
              type: "capture", fixture: selected, target: c.target, label: c.label,
            })}>
              {c.label}
              <div className="small muted mono">
                {(c.target[0]! / 1000).toFixed(1)}, {(c.target[2]! / 1000).toFixed(1)} m
              </div>
            </button>
          ))}
        </div>
        <p className="small muted">
          {head?.captures ?? 0} capture(s) for {selected}.
        </p>
        <div className="row">
          <button onClick={() => send({ type: "solve" })}>Solve (preview)</button>
          <button className="danger" onClick={() => send({ type: "solve", write: true })}>
            Solve &amp; write
          </button>
        </div>
        <p className="small muted" style={{ marginBottom: 0 }}>
          Writing snapshots the previous calibration first, so an overnight
          nudge stays a diff rather than a from-scratch re-aim.
        </p>
      </Card>

      {/* The readings are the jog positions, and the engine reads them itself:
          this tab holds one pan/tilt pair for whichever head is selected, not
          one per head, so it has nothing to send -- and a list it remembered
          would be wrong after a second phone jogged, a reload, or Stop all.
          Disabled until every head is jogging for the same reason capture is:
          a head nobody aimed has no reading. The engine refuses it too. */}
      <Card title="Drift check" help={<>
        <p>Checks whether any head has been knocked since it was calibrated. In{" "}
          <b>Jog</b>, aim each head at the mirror ball in turn, then tap{" "}
          <b>Check all heads</b>. It compares where each head is pointing with
          where the calibration says the ball is.</p>
        <p>Every head has to be jogging, because a head that hasn't been aimed
          has nothing to compare. Until then the button stays off and names the
          heads still to aim.</p>
        <p>A head that's moved 3° or more shows <b>MOVED</b> and needs re-aiming
          with <b>Capture</b>. Tap <b>Stop all</b> in Jog when you're done, to
          turn the safety taper back on.</p>
      </>}>
        <p className="small muted" style={{ marginTop: 0 }}>
          Park every head on the ball and compare against the stored
          calibration. A ten-second go/no-go instead of finding out mid-set.
        </p>
        {heads.length > 0 && unaimed.length > 0 && (
          <Banner kind="warn">
            <span>
              Jog <b>{unaimed.map((f) => short(f.name)).join(", ")}</b> onto the
              ball first — the check compares where each head is pointing, and{" "}
              {unaimed.length === 1 ? "that one hasn't" : "those haven't"} been
              aimed yet.
            </span>
          </Banner>
        )}
        <button style={{ width: "100%", marginBottom: "0.6rem" }}
                disabled={heads.length === 0 || unaimed.length > 0}
                onClick={() => send({ type: "drift" })}>
          Check all heads
        </button>
        {state.drift && (
          <div className="grid two">
            {state.drift.map((d) => (
              <div key={d.head} className="fixture">
                <div className="name">
                  <span className="grow">{d.head}</span>
                  <span className={`chip ${d.significant ? "jog" : ""}`}>
                    {d.significant ? "MOVED" : "ok"}
                  </span>
                </div>
                <div className="small mono muted">
                  {d.bearing >= 0 ? "+" : ""}{d.bearing.toFixed(2)}° bearing ·{" "}
                  {d.elevation >= 0 ? "+" : ""}{d.elevation.toFixed(2)}° elev
                </div>
              </div>
            ))}
          </div>
        )}
        <p className="small muted" style={{ marginBottom: 0 }}>
          Reported in degrees, not DMX — degrees compare across fixture types
          and carry the right sign for mirrored mounts.
        </p>
      </Card>

      <Card title="Notices">
        {state.notices.length === 0
          ? <p className="small muted" style={{ margin: 0 }}>Nothing yet.</p>
          : state.notices.slice().reverse().map((n, i) => (
              <div key={i} className="small mono muted">{n}</div>
            ))}
      </Card>

      <PatchSection state={state} send={send} />

      <RigSection state={state} />
      {/* Panic used to be the last card here. It moved to the bottom of Show
          when Perform mode arrived: Perform hides this whole tab, and a rig you
          cannot force to zero from the surface you are actually holding is the
          one thing that must never be behind a mode. */}
    </>
  );
}
