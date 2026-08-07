import { useState } from "react";
import { Banner, Card, Toggle } from "../components";
import type { Command, EngineState } from "../types";

/**
 * The room and the safety policy, editable from the phone at load-in.
 *
 * Everything here takes effect on the NEXT FRAME, which is the whole reason it
 * is worth having on a phone: you can stand in the room, move the crowd zone,
 * and watch the beams respond. Tuning it by editing JSON and restarting is the
 * workflow this replaces.
 *
 * The ball position and the mount mode are deliberately absent. Both are baked
 * into every head's calibration back-solve, so changing one means re-deriving
 * the rig — and doing that mid-show would move every aim at once. Those stay in
 * venue.json behind a restart.
 */
export function VenueTab({ state, send }: {
  state: EngineState; send: (c: Command) => void;
}) {
  const crowd = state.venue.crowd;
  const canopy = state.venue.canopy;
  const taper = state.taper;

  return (
    <>
      <Card title="Safety taper" right={
        <span className="small muted">
          {taper.enabled ? `floor ${Math.round(taper.crowd_level * 100)}%` : "OFF"}
        </span>
      }>
        {!taper.enabled && (
          <Banner kind="bad">
            The safety taper is OFF. Beams are no longer dimmed over the crowd.
          </Banner>
        )}

        <Toggle label="Taper enabled"
                hint="dim beams that cross the crowd at head height"
                on={taper.enabled}
                onChange={(on) => send({ type: "taper", enabled: on })} />

        <label className="field" style={{ marginTop: "0.6rem" }}>
          Crowd level — what a beam over people is dimmed to
          <input type="range" min={0} max={1} step={0.05}
                 aria-label="crowd level"
                 value={taper.crowd_level}
                 onChange={(e) => send({
                   type: "taper", crowd_level: Number(e.target.value),
                 })} />
        </label>
        <p className="small muted" style={{ marginTop: 0 }}>
          <b>{Math.round(taper.crowd_level * 100)}%</b>.{" "}
          {taper.crowd_level === 0
            ? "A hard guard — and it costs every floor-sweep pose, since a head aiming at the dancefloor necessarily crosses eye height on the way down."
            : "A glare guard, not a hard optical-safety guarantee: a beam aimed straight into an eye still emits at this level. Set it to 0 for the guarantee."}
        </p>

        <label className="field">
          Soft edge — {taper.margin_deg.toFixed(0)}°
          <input type="range" min={0} max={20} step={1}
                 aria-label="soft edge"
                 value={taper.margin_deg}
                 onChange={(e) => send({
                   type: "taper", margin_deg: Number(e.target.value),
                 })} />
        </label>
        <label className="field">
          Smoothing — {taper.slew_per_second.toFixed(1)} per second
          <input type="range" min={0} max={8} step={0.5}
                 aria-label="smoothing"
                 value={taper.slew_per_second}
                 onChange={(e) => send({
                   type: "taper", slew_per_second: Number(e.target.value),
                 })} />
        </label>
      </Card>

      {crowd && (
        <Card title="Crowd zone">
          <p className="small muted" style={{ marginTop: 0 }}>
            Where people stand, and how high their eyes are. The head band is a
            judgement call, not a measurement — generous at the top because
            someone on someone's shoulders is exactly who gets hit.
          </p>
          <div className="grid two">
            <NumberField label="Head band min (mm)" value={crowd.head_band_min}
                         onChange={(v) => send({ type: "venue", crowd: { head_band_min: v } })} />
            <NumberField label="Head band max (mm)" value={crowd.head_band_max}
                         onChange={(v) => send({ type: "venue", crowd: { head_band_max: v } })} />
            <NumberField label="Min X (mm)" value={crowd.min_x}
                         onChange={(v) => send({ type: "venue", crowd: { min_x: v } })} />
            <NumberField label="Max X (mm)" value={crowd.max_x}
                         onChange={(v) => send({ type: "venue", crowd: { max_x: v } })} />
            <NumberField label="Min Z (mm)" value={crowd.min_z}
                         onChange={(v) => send({ type: "venue", crowd: { min_z: v } })} />
            <NumberField label="Max Z (mm)" value={crowd.max_z}
                         onChange={(v) => send({ type: "venue", crowd: { max_z: v } })} />
          </div>
          <p className="small muted" style={{ marginBottom: 0 }}>
            Room is {((state.venue.width ?? 0) / 1000).toFixed(1)} ×{" "}
            {((state.venue.depth ?? 0) / 1000).toFixed(1)} m. Narrowing this is
            the cheapest way to buy back floor sweeps.
          </p>
        </Card>
      )}

      {canopy && (
        <Card title="Canopy">
          <Toggle label="Canopy rigged"
                  hint="beams terminate on it and it glows"
                  on={canopy.enabled}
                  onChange={(on) => send({ type: "venue", canopy: { enabled: on } })} />
          <div className="grid two" style={{ marginTop: "0.6rem" }}>
            <NumberField label="Height (mm)" value={canopy.height}
                         onChange={(v) => send({ type: "venue", canopy: { height: v } })} />
            <NumberField label="Radius (mm)" value={canopy.radius}
                         onChange={(v) => send({ type: "venue", canopy: { radius: v } })} />
          </div>
          <p className="small muted" style={{ marginBottom: 0 }}>
            This is what clipped the aerials on the night — those poses were
            computed as though the beams kept going. Check the Move tab: an
            aerial look should land on <b>canopy</b>, not <b>ceiling</b>.
          </p>
        </Card>
      )}

      <Card title="Save">
        <p className="small muted" style={{ marginTop: 0 }}>
          Changes above are live already. Saving writes them to venue.json so
          they survive a restart — the file's explanatory comments are kept.
        </p>
        <button className="on" style={{ width: "100%" }}
                onClick={() => send({ type: "venue_save" })}>
          Save to venue.json
        </button>
      </Card>
    </>
  );
}

/**
 * A number field that does not fight you while you type.
 *
 * Bound straight to server state it is unusable: every keystroke sends a
 * command, the echo re-renders the input, and half-typed values like "16" get
 * committed and then overwritten. So it holds a local draft while focused and
 * commits once — on blur or Enter. Same reasoning as the fader tracking locally
 * mid-drag, and the same narrow exception to "the UI never predicts".
 */
function NumberField({ label, value, onChange }: {
  label: string; value: number; onChange: (v: number) => void;
}) {
  const [draft, setDraft] = useState<string | null>(null);

  const commit = () => {
    if (draft === null) return;
    const v = Number(draft);
    if (draft.trim() !== "" && Number.isFinite(v)) onChange(v);
    setDraft(null);
  };

  return (
    <label className="field">
      {label}
      <input type="number" step={50}
             value={draft ?? value}
             onChange={(e) => setDraft(e.target.value)}
             onBlur={commit}
             onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }} />
    </label>
  );
}
