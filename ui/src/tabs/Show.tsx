import { useEffect, useState } from "react";
import { apiFetch } from "../useEngine";
import { Banner, Card, Fader, Toggle } from "../components";
import { DesignOnly, useDesign } from "../mode";
import type {
  Command, DeckLoaded, EngineState, LaneSource, OutputsState, Preset, Slot, TrackMatch,
} from "../types";

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
      <Cues state={state} send={send} />
      <Track state={state} send={send} />
      <Now state={state} send={send} />
      <Presets state={state} send={send} />
      <Sync state={state} send={send} />
      <Tempo state={state} send={send} />
      <Auto state={state} send={send} />
      <Panic state={state} send={send} />
    </>
  );
}

/**
 * The bottom of the last tab, which is exactly where it belongs.
 *
 * It was on Setup until Perform mode arrived, and Perform hides Setup — a rig
 * you cannot force to zero from the surface in your hand is not a thing to ship.
 * Still nowhere near the master, and still not in the header: the whole reason
 * it is not up there is that it should take a deliberate scroll to reach.
 */
function Panic({ state, send }: { state: EngineState; send: (c: Command) => void }) {
  return (
    <Card title="Panic">
      <p className="small muted" style={{ marginTop: 0 }}>
        Forces zeros onto the wire and stops evaluating the show at all. It does
        not need the show to be healthy or the engine to be keeping up, which is
        what makes it different from Blackout.
      </p>
      <p className="small muted">
        <b>Blackout</b> is the one you want mid-set: the show carries on
        underneath, so letting go picks up where it has got to. Reach for Panic
        when something has gone wrong, not when you want the room dark.
      </p>
      <button className={state.panicked ? "danger on" : "danger"}
              style={{ width: "100%" }}
              onClick={() => send(state.panicked
                ? { type: "clear_panic" } : { type: "panic" })}>
        {state.panicked ? "Release panic" : "Panic — force output to zero"}
      </button>
    </Card>
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
      // Disabled rather than absent, like every other heading action: auto mode
      // takes and drops the hold on its own, so a conditional button here made
      // the card twitch taller and shorter with nobody touching anything.
      <button className="small" disabled={!state.auto.held}
              onClick={() => send({ type: "release" })}>
        Release hold
      </button>
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

const slotCount = (p: Preset) =>
  [p.movement, p.color, p.level].reduce((n, m) => n + Object.keys(m ?? {}).length, 0);

/**
 * Named combinations of all three slots, on a grid of pads.
 *
 * The cost of making the slots independent is that a picture you liked takes
 * three taps to rebuild and is easy to lose. A preset is the answer: it stores
 * what each slot held, plus the speed and master it was built at.
 *
 * They used to be a flat uncapped grid, which is finding #17 of the review and
 * the thing that makes a console worse the more you use it: presets accumulate,
 * the grid grows, and nothing is where it was last week. So a page of EIGHT,
 * with each preset at a FIXED position — the APC40 layout the show was run from
 * for two years, where "the drop is bottom-right of bank 2" is muscle memory
 * that already exists.
 *
 * The position is the whole point. Saving over a preset keeps its pad; adding
 * and deleting neighbours does not shuffle it. That is what a paged grid buys
 * over a list, and a list that merely paginated would buy nothing.
 *
 * There is deliberately no "recently used" or "favourites" section. Both were
 * on the table and both are a SECOND place the same preset lives — which is the
 * clutter this is trying to remove, wearing a helpful hat. A fixed pad is
 * already the answer to "where is it".
 */
function Presets({ state, send }: { state: EngineState; send: (c: Command) => void }) {
  const design = useDesign();
  const { size, count } = state.preset_banks;
  // CLAMPED to what exists, every render. Deleting the last preset on the
  // highest bank drops `count`, and the bank selector only renders when there
  // is more than one bank — so an unclamped `bank` left the operator looking at
  // an empty page with no control that goes back, and every preset they had
  // unreachable without reloading the console. Derived rather than corrected in
  // an effect, so there is not even one frame showing the empty page.
  const [rawBank, setBank] = useState(1);
  const bank = Math.min(Math.max(rawBank, 1), count);
  const [name, setName] = useState("");
  const [target, setTarget] = useState<number | null>(null);
  const [editing, setEditing] = useState(false);
  const [picked, setPicked] = useState<string | null>(null);
  const [tag, setTag] = useState<string | null>(null);
  // Routines a pad can carry (milestone 2) -- only with a show folder.
  const [routines, setRoutines] = useState<{ id: string; name?: string;
                                             variations: string[] }[]>([]);
  const [routine, setRoutine] = useState("");
  const [variation, setVariation] = useState("");
  const showFolder = state.program != null;
  useEffect(() => {
    if (!showFolder) return;
    apiFetch<{ routines: { id: string; name?: string; variations: string[] }[] }>(
      "/api/routines").then((r) => setRoutines(r.routines)).catch(() => setRoutines([]));
  }, [showFolder]);
  const chosen = routines.find((r) => r.id === routine);

  // Edit is Design-only, so leaving Design has to put the card back in a state
  // that makes sense — otherwise a half-finished move survives into Perform
  // with nothing on screen explaining why a pad is highlighted.
  useEffect(() => {
    if (!design) { setEditing(false); setPicked(null); }
  }, [design]);

  const at = (cell: number) =>
    state.presets.find((p) => p.bank === bank && p.cell === cell);
  const tags = Array.from(new Set(state.presets.flatMap((p) => p.tags))).sort();
  const matching = tag ? state.presets.filter((p) => p.tags.includes(tag)) : [];

  const save = (cell?: number) => {
    const trimmed = name.trim();
    if (!trimmed) return;
    const where = cell ?? target;
    const pad = routine
      ? { routine: { id: routine, ...(variation ? { variation } : {}) } } : {};
    send(where == null
      ? { type: "preset_save", name: trimmed, ...pad }
      : { type: "preset_save", name: trimmed, bank, cell: where, ...pad });
    setName("");
    setTarget(null);
    setRoutine("");
    setVariation("");
  };

  const tapped = (p: Preset | undefined, cell: number) => {
    if (!editing) {
      // An empty pad is a save target, not a dead button: tapping it and then
      // typing is the fastest path from "I like this" to "it is stored", and
      // the alternative was a preset landing wherever the engine had room.
      if (p) send({ type: "preset_apply", name: p.name });
      else setTarget(cell);
      return;
    }
    if (picked) {
      // Second tap completes a move. The engine swaps rather than refusing, so
      // this reorders a bank without needing an empty pad to shuffle through.
      if (picked !== p?.name) send({ type: "preset_move", name: picked, bank, cell });
      setPicked(null);
    } else if (p) {
      setPicked(p.name);
    }
  };

  return (
    <Card title={`Presets — ${state.presets.length}`} right={
      <DesignOnly>
        <button className={editing ? "small on" : "small"}
                disabled={state.presets.length === 0}
                onClick={() => { setEditing(!editing); setPicked(null); }}>
          {editing ? "Done" : "Edit"}
        </button>
      </DesignOnly>
    }>
      {/* Only when there is more than one page. A bank selector over a single
          bank is a control that explains a concept nobody has met yet. */}
      {count > 1 && (
        <div className="row tight" style={{ marginBottom: "0.5rem", flexWrap: "wrap" }}>
          <span className="small muted" style={{ minWidth: "3.5em" }}>Bank</span>
          {Array.from({ length: count }, (_, i) => i + 1).map((b) => (
            <button key={b} className={b === bank ? "small on" : "small"}
                    aria-label={`bank ${b}`}
                    onClick={() => { setBank(b); setTarget(null); setPicked(null); }}>
              {b}
            </button>
          ))}
        </div>
      )}

      {tag ? (
        <div className="grid tiles">
          {matching.map((p) => (
            <button key={p.name}
                    onClick={() => send({ type: "preset_apply", name: p.name })}>
              {p.name}
              <div className="small muted mono">
                {p.bank}.{p.cell + 1} · {slotCount(p)} slot(s)
              </div>
            </button>
          ))}
          {matching.length === 0 && (
            <p className="small muted" style={{ margin: 0 }}>
              Nothing tagged {tag}.
            </p>
          )}
        </div>
      ) : (
        <div className="grid tiles">
          {Array.from({ length: size }, (_, cell) => {
            const p = at(cell);
            const isTarget = target === cell;
            const isPicked = picked != null && p?.name === picked;
            const pad = p && state.pad?.name === p.name ? state.pad : null;
            return (
              <button key={cell}
                      className={isPicked || isTarget || (pad && !pad.waiting) ? "on"
                        : pad ? "pending" : p ? "" : "ghost"}
                      aria-label={p ? undefined : `empty pad ${bank}.${cell + 1}`}
                      onClick={() => tapped(p, cell)}>
                {p ? (
                  <>
                    {p.name}
                    <div className="small muted">
                      {p.routine ? `↻ ${p.routine.id}` : `${slotCount(p)} slot(s)`}
                      {pad && (pad.waiting ? " · next downbeat" : " · playing")}
                      {p.tags.length > 0 && ` · ${p.tags.join(" ")}`}
                    </div>
                  </>
                ) : (
                  <>
                    <span className="muted">+</span>
                    <div className="small muted mono">{bank}.{cell + 1}</div>
                  </>
                )}
              </button>
            );
          })}
        </div>
      )}

      {editing && (
        <PresetEditor state={state} send={send} picked={picked}
                      onDeleted={() => setPicked(null)} />
      )}

      <div className="row" style={{ marginTop: "0.6rem" }}>
        <input className="field" value={name} aria-label="preset name"
               placeholder={target == null
                 ? "name this picture…" : `name it — goes to ${bank}.${target + 1}`}
               onChange={(e) => setName(e.target.value)}
               onKeyDown={(e) => { if (e.key === "Enter") save(); }}
               style={{
                 flex: "1 1 10rem", minWidth: 0, padding: "0.55rem", minHeight: 44,
                 background: "var(--panel-2)", border: "1px solid var(--line)",
                 borderRadius: 8,
               }} />
        <button onClick={() => save()} disabled={!name.trim()}>Save</button>
      </div>
      {routines.length > 0 && (
        <div className="row tight" style={{ marginTop: "0.4rem", flexWrap: "wrap" }}>
          <label className="small muted">With a routine{" "}
            <select value={routine} aria-label="pad routine"
                    onChange={(e) => { setRoutine(e.target.value); setVariation(""); }}>
              <option value="">none — the looks only</option>
              {routines.map((r) => <option key={r.id} value={r.id}>{r.name ?? r.id}</option>)}
            </select>
          </label>
          {chosen && chosen.variations.length > 0 && (
            <select value={variation} aria-label="pad routine variation"
                    onChange={(e) => setVariation(e.target.value)}>
              <option value="">default</option>
              {chosen.variations.map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
          )}
        </div>
      )}

      {/* Absent until something is tagged, which keeps the cost of the feature
          at zero for anyone not using it. Tags cross banks; a bank cannot,
          because a bank is a place and a tag is a question. */}
      {tags.length > 0 && (
        <div className="row tight" style={{ marginTop: "0.5rem", flexWrap: "wrap" }}>
          <span className="small muted" style={{ minWidth: "3.5em" }}>Tagged</span>
          {tags.map((t) => (
            <button key={t} className={t === tag ? "small on" : "small"}
                    onClick={() => setTag(t === tag ? null : t)}>
              {t}
            </button>
          ))}
        </div>
      )}

      <p className="small muted" style={{ marginBottom: 0 }}>
        {editing
          ? picked
            ? <>Now tap where <b>{picked}</b> should go — landing on a full pad swaps the two.</>
            : "Tap a preset to pick it up and move it, or use the buttons below to delete or tag it."
          : <>Saves the move, colour and level that are up now, with the speed and
             master. A pad keeps its preset: saving over one leaves it exactly
             where it is.</>}
      </p>
    </Card>
  );
}

/**
 * Delete and tag, behind the Edit toggle rather than beside every pad.
 *
 * The old card listed every preset twice — once to recall, once to delete —
 * which doubled the longest thing on the tab to make room for the rarest
 * action. One editor for the pad you picked up costs nothing until you ask.
 */
function PresetEditor({ state, send, picked, onDeleted }: {
  state: EngineState; send: (c: Command) => void;
  picked: string | null; onDeleted: () => void;
}) {
  const preset = state.presets.find((p) => p.name === picked);
  const [draft, setDraft] = useState("");
  // Re-seeded whenever a different preset is picked up, and NOT while it is the
  // same one: overwriting the box from the broadcast would delete what is being
  // typed on every state frame, which at 10 a second is unusable.
  useEffect(() => { setDraft(preset ? preset.tags.join(" ") : ""); }, [picked]);
  if (!preset) return null;

  return (
    <div className="row" style={{ marginTop: "0.5rem", gap: "0.4rem" }}>
      <input className="field" value={draft}
             aria-label={`tags for ${preset.name}`}
             placeholder="intro build drop ambient"
             onChange={(e) => setDraft(e.target.value)}
             onBlur={() => send({ type: "preset_tag", name: preset.name,
                                  tags: draft.split(/\s+/).filter(Boolean) })}
             style={{
               flex: "1 1 8rem", minWidth: 0, padding: "0.55rem", minHeight: 44,
               background: "var(--panel-2)", border: "1px solid var(--line)",
               borderRadius: 8,
             }} />
      <button className="small danger"
              onClick={() => {
                send({ type: "preset_delete", name: preset.name });
                onDeleted();
              }}>
        Delete {preset.name}
      </button>
    </div>
  );
}

/** Beyond this many seconds without a packet, a bridge is not driving any more,
 *  it is a tempo somebody left behind. Two bars at 120 bpm — long enough that a
 *  sparse sender does not flicker, short enough to notice before a whole
 *  phrase has gone by. */
const SYNC_STALE = 4;

/**
 * What the DJ link is doing, and how to take it back.
 *
 * Absent entirely when no port is open, because "you have not set this up" is
 * not something a console should say every night to someone who never will.
 *
 * The rest of it exists for one failure: a bridge that dies leaves the timeline
 * free-running at whatever tempo it last sent, with the clock still naming it.
 * The console looks locked while it drifts away from a DJ nobody is listening
 * to any more. So the age of the last packet is the loudest thing here, and
 * taking over is always one tap — a bridge must never silently own the clock.
 */
/** What the transport is doing, in the operator's words. "packets late" rather
 *  than "stalled": the deck is probably still playing; the bridge is not
 *  keeping up. */
const TRACK_STATE: Record<string, string> = {
  playing: "playing", stalled: "packets late", paused: "paused",
  reverse: "scratching",
};

const LANE_NAMES: Record<Slot, string> = {
  movement: "Movement", color: "Colour", level: "Level",
};
const LANE_SOURCE: Record<LaneSource, string> = {
  timeline: "timeline", template: "template", operator: "operator", idle: "idle",
  fallback: "show",
};
/** Why the timeline is not on stage, in the operator's words. */
const NOT_DRIVING: Record<string, string> = {
  disarmed: "Follow is SAFE — the DJ feed drives nothing until you arm it.",
  "no track": "Nothing identified is playing.",
  matching: "Matching the track…",
  "not in the show folder": "This track is not in the show folder — the "
    + "operator's show runs.",
  "no timeline": "Matched, but nobody has drawn this track a show yet.",
  "no template for this phrase": "The template set has nothing for this "
    + "phrase, and no bar cycle — the operator's show runs.",
  compiling: "Building this track's show…",
  "compile failed": "This track's show could not be built — see the notices. "
    + "The operator's show runs.",
  "no position": "Waiting for the deck's position.",
  "paused (no idle routine)": "Paused, and show.json names no idle routine.",
  "preview: no timeline yet": "The designer is driving a track with no "
    + "timeline yet.",
};

/**
 * Follow DJ: whether the matched track's timeline drives the rig, and who has
 * each lane. Only with a show folder. Arming is one tap and deliberate — the
 * feed it follows arrives on an unauthenticated port.
 */
function Track({ state, send }: { state: EngineState; send: (c: Command) => void }) {
  const prog = state.program;
  const [latency, setLatency] = useState<number | null>(null);
  if (!prog) return null;
  const track = state.track;
  const source = track?.source ?? null;
  const saved = source != null ? (prog.latency_ms[source] ?? 0) : 0;
  const shown = latency ?? saved;
  const slots: Slot[] = ["movement", "color", "level"];

  return (
    <Card title="Track" right={
      <button className={prog.armed ? "on" : ""}
              aria-pressed={prog.armed}
              onClick={() => send({ type: "follow", armed: !prog.armed })}>
        {prog.armed ? "Follow ARMED" : "Follow SAFE"}
      </button>
    }>
      {track && track.state !== "no_track" && (
        <div className="small">
          <b>{track.title}</b>
          {track.artist && <span className="muted"> — {track.artist}</span>}
          {track.match && <MatchLine match={track.match} />}
        </div>
      )}
      {state.outputs && <OutputsLine outputs={state.outputs} />}
      {(track?.decks?.length ?? 0) > 0 && (
        <div className="small muted" aria-label="other decks">
          {track!.decks!.map((d) => (
            <div key={d.deck}>
              Deck {d.deck}: {d.title ?? "?"} · {deckShow(d)}
            </div>
          ))}
        </div>
      )}
      <div className="small" style={{ marginTop: "0.3rem" }}>
        {prog.mode === "timeline" || prog.mode === "preview"
          ? <>{prog.mode === "preview" ? "The designer is driving" : "Timeline driving"}
              {prog.bar != null && <> · bar <b>{prog.bar}</b></>}</>
          : prog.mode === "template"
            ? <>Template driving{prog.bar != null && <> · bar <b>{prog.bar}</b></>}
                {prog.reason && <span className="muted"> ({prog.reason})</span>}</>
            : prog.mode === "idle"
              ? <>Paused — the idle routine is running</>
              : <span className="muted">
                  {NOT_DRIVING[prog.reason ?? ""] ?? prog.reason}
                </span>}
      </div>
      {prog.template && (
        <div className="small muted" aria-label="template now">
          {prog.template.label === "bars" ? "bar cycle" : prog.template.label}
          {" → "}<b>{prog.template.routine}</b>
          {prog.template.fading && " (crossfading)"}
        </div>
      )}
      {(prog.sets?.length ?? 0) > 0 && (
        <div style={{ marginTop: "0.5rem" }}>
        <div className="small muted">Template set</div>
        <div className="pills" role="group" aria-label="template set">
          {[{ id: null as string | null, name: "Off" }, ...(prog.sets ?? [])].map((s) => {
            const active = (prog.set ?? null) === s.id;
            const waiting = prog.pending != null
              && (prog.pending === "off" ? s.id === null : prog.pending === s.id);
            return (
              <button key={s.id ?? "off"} aria-pressed={active}
                      className={active ? "on" : waiting ? "pending" : ""}
                      title={waiting ? "Switches on the next downbeat" : undefined}
                      onClick={() => send({ type: "template_set", id: s.id })}>
                {s.name}{waiting && " · next downbeat"}
              </button>
            );
          })}
        </div>
        </div>
      )}
      {prog.engaged && (
        <div className="lanes" style={{ marginTop: "0.4rem" }}>
          {slots.map((slot) => {
            const who = prog.lanes[slot] ?? "fallback";
            const grabbed = prog.grabbed.includes(slot);
            return (
              <div className="spread small" key={slot}>
                <span>{LANE_NAMES[slot]} <span className="muted mono">
                  {LANE_SOURCE[who]}</span></span>
                {grabbed
                  ? <button onClick={() => send({ type: "program_release", slot })}>
                      Release
                    </button>
                  : <button onClick={() => send({ type: "program_grab", slot })}>
                      Grab
                    </button>}
              </div>
            );
          })}
        </div>
      )}
      {prog.problems > 0 && (
        <Banner kind="warn">
          {prog.problems} problem(s) building this track's show on this rig:
          {" "}{prog.first_problem}
        </Banner>
      )}
      {source && (
        <Fader label={`Latency (${source})`} value={(shown + 200) / 400}
               format={() => `${shown > 0 ? "+" : ""}${shown} ms`}
               onInput={(v) => setLatency(Math.round((v * 400 - 200) / 5) * 5)}
               onCommit={(v) => {
                 const ms = Math.round((v * 400 - 200) / 5) * 5;
                 setLatency(null);
                 send({ type: "show_latency", source, ms });
               }} />
      )}
      <div className="small muted" style={{ marginTop: "0.4rem" }}>
        <a href={track?.match?.track_id ? `#designer/${track.match.track_id}` : "#designer"}>
          {track?.match?.track_id ? "Open this track in the designer" : "Open the designer"}</a>
        {" "}· on a computer
      </div>
    </Card>
  );
}

/** How a track was matched, in the operator's words -- how much to trust it
 *  before arming a show on it. */
const MATCH_VIA: Record<string, string> = {
  signature: "by signature", rekordbox_id: "by rekordbox id",
  alias: "by manual link", title_artist_album: "by title, artist and album",
  title_artist: "by title and artist",
};

/** Where the show's cues for a VJ app go, and whether they are getting there. */
function OutputsLine({ outputs }: { outputs: OutputsState }) {
  const osc = outputs.osc;
  const midi = outputs.midi;
  const tc = outputs.timecode;
  return (
    <div className="small muted" aria-label="outputs">
      {midi && <div>
        MIDI → sidecar {midi.target}{midi.on > 0 && <> · <b>{midi.on}</b> on</>}
        {midi.errors > 0 && <span className="warn-text"> · {midi.errors} failed</span>}
      </div>}
      {tc && <div>
        Timecode → {tc.target} · <span className="mono">{tc.now ?? "silent"}</span>
        {" "}({tc.fps} fps)
        {tc.errors > 0 && <span className="warn-text"> · {tc.errors} failed</span>}
      </div>}
      {osc && <div>
        OSC → {osc.target}{osc.on > 0 && <> · <b>{osc.on}</b> on</>}
        {osc.errors > 0 && <span className="warn-text"> · {osc.errors} failed
          {osc.last_error ? ` (${osc.last_error})` : ""}</span>}
      </div>}
      {outputs.problems.map((p) => <div key={p} className="warn-text">{p}</div>)}
    </div>
  );
}

/** What a deck's loaded track will bring when it becomes the master. */
function deckShow(d: DeckLoaded): string {
  if (!d.track_id) return "not in the show folder";
  if (!d.has_timeline) return `${d.track_id}, no timeline`;
  return d.ready ? `${d.track_id}, show ready` : `${d.track_id}, building its show`;
}

function MatchLine({ match }: { match: TrackMatch }) {
  if (match.track_id) {
    return (
      <div className="muted">
        show <b>{match.track_id}</b> · {MATCH_VIA[match.via] ?? match.via}
        {match.has_timeline != null &&
          (match.has_timeline ? " · timeline" : " · no timeline")}
        {match.stale && " · folder changed, applies next play"}
      </div>
    );
  }
  if (match.via === "ambiguous") {
    return (
      <div className="muted">
        could be {match.candidates.join(", ")} — not guessing
      </div>
    );
  }
  return <div className="muted">not in the show folder</div>;
}

function clockTime(seconds: number | null): string {
  if (seconds == null) return "–:––";
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function Sync({ state, send }: { state: EngineState; send: (c: Command) => void }) {
  const sync = state.sync;
  const track = state.track;
  if (!sync?.listening && !sync?.driving) return null;

  const stale = sync.age != null && sync.age > SYNC_STALE;
  const locked = sync.driving && !stale;

  return (
    <Card title="DJ sync" right={
      <span className="small mono" style={{
        color: locked ? "var(--good)" : stale ? "var(--bad)" : undefined,
      }}>
        {locked ? "LOCKED" : stale ? "NO SIGNAL" : "waiting"}
      </span>
    }>
      {stale && (
        <Banner kind="bad">
          Nothing from <b>{state.clock.source}</b> for {Math.round(sync.age!)}s.
          The show is free-running on the tempo it left behind — it has not
          stopped, it has stopped being right.
        </Banner>
      )}

      <div className="spread small">
        <span className="muted">
          {sync.driving ? <>source <b>{state.clock.source}</b></>
            : "port open, nothing has spoken through it yet"}
          {sync.deck && <> · deck {sync.deck}</>}
        </span>
        {sync.age != null && (
          <span className="mono muted">{sync.age.toFixed(1)}s ago</span>
        )}
      </div>

      {track && track.state !== "no_track" ? (
        <div className="small" style={{ marginTop: "0.3rem" }}>
          <b>{track.title}</b>
          {track.artist && <span className="muted"> — {track.artist}</span>}
          <div className="mono muted">
            {clockTime(track.time)}
            {track.duration ? ` / ${clockTime(track.duration)}` : ""}
            {" · "}{TRACK_STATE[track.state]}
            {track.state === "playing" && Math.abs(track.rate - 1) >= 0.0005 &&
              ` · ${track.rate > 1 ? "+" : ""}${((track.rate - 1) * 100).toFixed(1)}%`}
          </div>
          {track.match && <MatchLine match={track.match} />}
          {track.grid_warning && (
            <Banner kind="warn">
              The deck puts the beat {Math.abs(track.grid_warning.offset_beats)}
              {" "}beat(s) {track.grid_warning.offset_beats > 0 ? "ahead of" : "behind"}
              {" "}the prepped grid. Re-gridded since it was prepped, or a
              different edit — its cues would land off the beat.
            </Banner>
          )}
        </div>
      ) : sync.track && (
        <div className="small" style={{ marginTop: "0.3rem" }}>{sync.track}</div>
      )}

      {sync.phrase && (
        <div className="small" style={{ marginTop: "0.3rem" }}>
          Phrase <b>{sync.phrase}</b>
          {/* Counted down here from an absolute beat the engine holds, rather
              than from a number the bridge re-sends — which is what lets a
              sender speak once a bar instead of once a beat. */}
          {sync.phrase_ends_in != null && sync.phrase_ends_in > 0 && (
            <span className="muted">
              {" "}· {Math.ceil(sync.phrase_ends_in / 4)} bar(s) left
            </span>
          )}
        </div>
      )}

      <div className="row" style={{ marginTop: "0.6rem" }}>
        <button style={{ flex: 1 }} disabled={!sync.driving}
                onClick={() => send({ type: "sync_off" })}>
          Take over
        </button>
      </div>
      <p className="small muted" style={{ marginBottom: 0 }}>
        {sync.driving
          ? "Taking over leaves the tempo and the phase exactly where they are — it only changes who decides next."
          : "Start a bridge and point it at the engine's sync port. bridges/prolink/README.md, or --fake to try it with no players."}
        {sync.port && sync.port.rejected > 0 && (
          <> <span style={{ color: "var(--warn)" }}>
            {sync.port.rejected} unreadable packet(s) — something is sending,
            but not in a shape this understands.
          </span></>
        )}
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

      {/* The WHOLE show, which is why it lives here beside the tempo and no
          longer on Move as well. Each slot has its own Rate on its own tab;
          this one moves musical time itself, so cue holds and auto boundaries
          come with it. */}
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
      <p className="small muted" style={{ margin: "0.4rem 0 0" }}>
        Speed moves the whole show, cue holds included. To run one slot faster
        than the others, use Rate on its own tab.
      </p>
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

      {/* How often the timed axes fire. `auto_interval` has had a handler since
          F7 and nothing that sent one, so the rate at which the show rearranges
          itself was the one auto setting you could only change in code. */}
      <div className="row tight" style={{ marginTop: "0.6rem", flexWrap: "wrap" }}>
        {(["looks", "palette"] as const).map((axis) => (
          <div key={axis} className="row tight" style={{ gap: "0.3rem" }}>
            {/* "Colours every", not "Palette every": there is a Palette toggle
                two lines above, and two controls whose labels both start with
                the same word is ambiguous to read and ambiguous to click. */}
            <span className="small muted">
              {axis === "looks" ? "Looks every" : "Colours every"}
            </span>
            {[0.5, 1, 2, 4, 8].map((v) => (
              <button key={v} className={
                        Math.abs(auto.intervals[axis] - v) < 0.01 ? "small on" : "small"}
                      // The accessible name comes from here, not the visible text,
                      // so this has to avoid the "Palette" toggle above just as
                      // the label does.
                      aria-label={`${axis === "looks" ? "looks" : "colours"} `
                                  + `every ${v} phrases`}
                      onClick={() => send({ type: "auto_interval", axis, value: v })}>
                {v}
              </button>
            ))}
          </div>
        ))}
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

/**
 * The night as one GO button.
 *
 * The QLC+ show had this and the port lost it: its six Collections were the
 * actual shape of the set, and every one was skipped with "rebuild with motion
 * primitives". A guest operator who knows nothing about the rig can run the
 * whole night off GO, which is what it was for.
 *
 * Absent entirely when the event has no cues.json, rather than showing an empty
 * shell — a show driven by hand off the look picker is still a show.
 */
function Cues({ state, send }: { state: EngineState; send: (c: Command) => void }) {
  const cues = state.cues;
  const [open, setOpen] = useState(false);
  if (!cues) return null;

  const started = cues.index >= 0;

  return (
    <Card title={cues.name} right={
      <span className="small muted mono">
        {started ? `${cues.index + 1}/${cues.count}` : `— /${cues.count}`}
      </span>
    }>
      <p className="small" style={{ marginBottom: "0.6rem" }}>
        {/* Before the first GO there is no current cue, and saying "now: Warm
            Up" when nothing has been taken is the one thing that would make an
            operator distrust the whole list. */}
        {started ? <>Now: <b>{cues.current}</b></> : <span className="muted">Not started</span>}
        {cues.next
          ? <> · next: <b>{cues.next}</b></>
          : <span className="muted"> · end of the list</span>}
        {state.fading && <span className="muted"> · fading…</span>}
      </p>

      <div className="row" style={{ gap: "0.5rem" }}>
        <button className="small" disabled={cues.index <= 0}
                onClick={() => send({ type: "cue_back" })}>Back</button>
        <button style={{ flex: 2 }} disabled={!cues.next}
                onClick={() => send({ type: "go" })}>
          {started ? `GO — ${cues.next ?? "end"}` : `GO — ${cues.next}`}
        </button>
      </div>

      <button className="small" style={{ width: "100%", marginTop: "0.5rem",
                                         justifyContent: "flex-start" }}
              onClick={() => setOpen(!open)}>
        {open ? "Hide" : "Show"} all {cues.count} cues
      </button>
      {open && (
        <div className="grid tiles" style={{ marginTop: "0.4rem" }}>
          {cues.cues.map((c, i) => (
            <button key={c.name} className={i === cues.index ? "on" : ""}
                    onClick={() => send({ type: "cue", index: i })}>
              {i + 1}. {c.name}
              <div className="small muted">
                {c.fade === 0 ? "cut" : `${c.fade} beat fade`}
                {c.hold > 0 && ` · auto after ${c.hold}`}
              </div>
            </button>
          ))}
        </div>
      )}
    </Card>
  );
}
