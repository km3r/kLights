import { useState } from "react";
import { Banner, Card } from "../components";
import type { Command, EngineState } from "../types";

/**
 * Editing the rig, from the phone, with the rig in front of you.
 *
 * A SECTION of Setup rather than a sixth tab. Rig and Venue were both tabs once
 * and became sections here for the same reason: they are all one job — what is
 * plugged in, where the room is, what was measured — and splitting them meant
 * three places to look. Five tabs is already a lot of thumb on a phone.
 *
 * Two guards, both about the same failure:
 *
 *   * **Locked by default.** Nothing here is reachable until you say so. Every
 *     other control on this surface is recoverable by pressing it again; a
 *     re-addressed rig is a walk around the room with a torch.
 *   * **Nothing takes effect until it is applied**, and it says so until it
 *     is. Each edit is saved to rig.json at once; Apply now swaps the saved rig
 *     into the running show at a frame boundary (`patch_apply`), and a rig that
 *     will not load is refused while the old one keeps running. Until then a
 *     saved patch and the running rig genuinely disagree, and that is exactly
 *     the state that has to be visible.
 */
/**
 * A saved change the running show is not using yet, and the button that loads
 * it: a patch edit (rig.json) or a solved calibration (calibration.json).
 *
 * Shown in the card where that change was made — Patch for rig.json, Capture
 * for calibration.json — because that is where the operator is looking when
 * they make it; a banner at the other end of a phone-length tab is one nobody
 * sees. Both say everything that is waiting, since one Apply loads it all: the
 * engine reads the rig from both files.
 */
export function PendingApply({ state, send, file }: {
  state: EngineState; send: (c: Command) => void; file: string;
}) {
  // An engine that predates the list only says *that* something is pending,
  // and then it was a patch.
  const files = state.pending_files?.length ? state.pending_files
    : state.pending_patch ? ["rig.json"] : [];
  if (!files.includes(file)) return null;
  return (
    <Banner kind="warn">
      {/* One span: a banner is a flex row, and bare text either side of the
          <code> would lay out as columns. */}
      <span>
        Saved to {files.map((f, i) => (
          <span key={f}>{i ? " and " : ""}<code>{f}</code></span>
        ))}, but the show is still running the rig it last loaded. Applying
        swaps it in at a frame boundary; if the new one does not load, the
        current one keeps running.
        {files.includes("calibration.json") &&
          " Every look that aims at something moves to the new calibration."}
      </span>
      {/* Never shrunk: the sentence beside it is long enough to squeeze the
          button to a letter a line. */}
      <button className="small" style={{ marginLeft: "auto", flexShrink: 0, whiteSpace: "nowrap" }}
              onClick={() => send({ type: "patch_apply" })}>
        Apply now
      </button>
    </Banner>
  );
}

export function PatchSection({ state, send }: {
  state: EngineState; send: (c: Command) => void;
}) {
  const [unlocked, setUnlocked] = useState(false);
  const [adding, setAdding] = useState(false);

  return (
    <Card title="Patch" right={
      <button className={unlocked ? "danger on small" : "small"}
              onClick={() => setUnlocked(!unlocked)}>
        {unlocked ? "Lock" : "Unlock to edit"}
      </button>
    }>
      <PendingApply state={state} send={send} file="rig.json" />

      {!unlocked ? (
        <p className="small muted" style={{ marginBottom: 0 }}>
          {state.fixtures.length} fixture(s) patched. Unlock to change
          addresses, tags and positions. Each edit is saved
          to <code>rig.json</code> and goes live when you apply it, with no
          restart — but a re-addressed rig means re-dialling every unit, so it
          is load-in work rather than something to reach for mid-set.
        </p>
      ) : (
        <>
          <div className="grid" style={{ gap: "0.5rem" }}>
            {state.fixtures.map((f) => (
              <FixtureRow key={f.id} fixture={f} send={send} />
            ))}
          </div>

          <div className="row" style={{ marginTop: "0.7rem", gap: "0.5rem" }}>
            <button className="small" onClick={() => setAdding(!adding)}>
              {adding ? "Cancel" : "Add fixture"}
            </button>
            <button className="small"
                    title="Re-address everything end to end with no gaps. Every unit has to be re-dialled to match."
                    onClick={() => {
                      if (confirm("Re-address every fixture with no gaps?\n\n"
                        + "You will have to re-dial every unit on the rig to match.")) {
                        send({ type: "patch_autopatch", start: 1 });
                      }
                    }}>
              Autopatch
            </button>
          </div>

          {adding && <AddFixture state={state} send={send}
                                 done={() => setAdding(false)} />}
        </>
      )}
    </Card>
  );
}

/** Where a fixture is, as the three fields show it: x, height, z. */
const AXES = ["X", "Height", "Z"] as const;

/** One patched unit. Address is the field that gets edited at a venue; tags are
 *  the field that gets edited when a look is scoped wrongly; position is the
 *  one that gets edited when the room is not the one the rig was drawn for. */
function FixtureRow({ fixture, send }: {
  fixture: EngineState["fixtures"][number]; send: (c: Command) => void;
}) {
  const [address, setAddress] = useState(String(fixture.address));
  const [tags, setTags] = useState(fixture.tags.join(", "));
  const [position, setPosition] = useState(
    fixture.position?.map((v) => String(v)) ?? ["", "", ""]);

  // Committed when focus leaves the three fields, not each one: a position is
  // one value, and moving a head one axis at a time writes rig.json once per
  // axis, each with its own "calibration no longer lines up" notice. Nothing
  // is sent until all three are numbers -- a fixture with no position yet has
  // to be typed in whole, and half of one is not somewhere to put it.
  const commitPosition = () => {
    const next = position.map((v) => (v.trim() === "" ? NaN : Number(v)));
    if (!next.every(Number.isFinite)) return;
    const current = fixture.position;
    if (current && next.every((v, i) => v === current[i])) return;
    send({ type: "patch_position", name: fixture.name,
           position: { x: next[0]!, y: next[1]!, z: next[2]! } });
  };

  return (
    <div className="fixture">
      <div className="name">
        <span className="grow">{fixture.name}</span>
        <span className="small muted mono">u{fixture.universe}</span>
      </div>
      <div className="row tight" style={{ gap: "0.4rem", flexWrap: "wrap" }}>
        <label className="small muted">ch</label>
        {/* Committed on blur, not per keystroke: bound straight to the server
            this would send "1", "14", "140" as you typed 1400 — the same trap
            the venue form's NumberField was built to avoid. */}
        <input className="field mono" inputMode="numeric" value={address}
               aria-label={`${fixture.name} address`}
               style={{ width: 72, minHeight: 44 }}
               onChange={(e) => setAddress(e.target.value)}
               onBlur={() => {
                 const next = parseInt(address, 10);
                 if (Number.isFinite(next) && next !== fixture.address) {
                   send({ type: "patch_address", name: fixture.name,
                          address: next });
                 } else {
                   setAddress(String(fixture.address));
                 }
               }} />
        <input className="field" value={tags} placeholder="tags"
               aria-label={`${fixture.name} tags`}
               style={{ flex: 1, minWidth: 120, minHeight: 44 }}
               onChange={(e) => setTags(e.target.value)}
               onBlur={() => {
                 const next = tags.split(",").map((t) => t.trim()).filter(Boolean);
                 if (next.join(",") !== fixture.tags.join(",")) {
                   send({ type: "patch_tags", name: fixture.name, tags: next });
                 }
               }} />
        <button className="small danger"
                onClick={() => {
                  if (confirm(`Unpatch ${fixture.name}?`
                    + (fixture.head != null
                      ? "\n\nThis is a moving head, so every head index after it "
                        + "shifts and the calibration no longer lines up." : ""))) {
                    send({ type: "patch_remove", name: fixture.name });
                  }
                }}>Remove</button>
      </div>
      <div className="row tight" style={{ gap: "0.4rem", marginTop: "0.4rem" }}
           onBlur={(e) => {
             if (!e.currentTarget.contains(e.relatedTarget as Node | null)) {
               commitPosition();
             }
           }}>
        {AXES.map((axis, i) => (
          <label key={axis} className="field" style={{ flex: 1, minWidth: 0 }}>
            {axis} (mm)
            <input className="mono" inputMode="numeric" value={position[i]}
                   aria-label={`${fixture.name} ${axis.toLowerCase()}`}
                   style={{ width: "100%" }}
                   onChange={(e) => setPosition(
                     position.map((v, j) => (j === i ? e.target.value : v)))}
                   onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }} />
          </label>
        ))}
      </div>
    </div>
  );
}

/** Adding a unit. The profile list comes from the engine, so the only
 *  manufacturer/model/mode combinations offered are ones that will resolve. */
function AddFixture({ state, send, done }: {
  state: EngineState; send: (c: Command) => void; done: () => void;
}) {
  const profiles = state.profiles ?? [];
  const [index, setIndex] = useState(0);
  const [name, setName] = useState("");
  const [mode, setMode] = useState("");

  const profile = profiles[index];
  const modes = profile ? Object.keys(profile.modes) : [];
  const chosenMode = mode && modes.includes(mode) ? mode : modes[0] ?? "";

  if (!profile) {
    return <p className="small muted">
      No fixture profiles found in <code>shared/fixtures/</code>.
    </p>;
  }

  return (
    <div style={{ marginTop: "0.7rem" }}>
      <label className="small muted">Fixture</label>
      <select className="field" value={index} style={{ width: "100%", minHeight: 44 }}
              aria-label="fixture profile"
              onChange={(e) => { setIndex(Number(e.target.value)); setMode(""); }}>
        {profiles.map((p, i) => (
          <option key={`${p.manufacturer}/${p.model}`} value={i}>
            {p.manufacturer} {p.model}
          </option>
        ))}
      </select>

      <label className="small muted" style={{ marginTop: "0.5rem", display: "block" }}>
        Mode
      </label>
      <select className="field" value={chosenMode} aria-label="mode"
              style={{ width: "100%", minHeight: 44 }}
              onChange={(e) => setMode(e.target.value)}>
        {modes.map((m) => (
          <option key={m} value={m}>{m} ({profile.modes[m]} channels)</option>
        ))}
      </select>

      <label className="small muted" style={{ marginTop: "0.5rem", display: "block" }}>
        Name — looks and calibration are both keyed by it, so it has to be unique
      </label>
      <input className="field" value={name} aria-label="fixture name"
             style={{ width: "100%", minHeight: 44 }}
             placeholder="e.g. Par 1"
             onChange={(e) => setName(e.target.value)} />

      <button className="small" style={{ width: "100%", marginTop: "0.6rem" }}
              disabled={!name.trim()}
              onClick={() => {
                send({ type: "patch_add", name: name.trim(),
                       manufacturer: profile.manufacturer, model: profile.model,
                       mode: chosenMode, tags: [] });
                setName("");
                done();
              }}>
        Add at the first free address
      </button>
    </div>
  );
}
