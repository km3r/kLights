/**
 * The wire protocol, mirroring engine/server.py's snapshot().
 *
 * Hand-written rather than generated. The engine is the authority and this is a
 * reader of it, so the useful property is that a field the engine stops sending
 * shows up as a type error here rather than as `undefined` at 2am.
 */

export type RGB = [number, number, number];

export interface ClockState {
  bpm: number;
  effective_bpm: number;
  speed: number;
  beat: number;
  bar: number;
  phrase: number;
  beat_in_bar: number;
  source: string;
  /** False when phrase is COUNTED from a tapped downbeat rather than measured.
   *  Auto mode lands changes on bars instead when this is false, and the UI
   *  says so — a counted phrase drifts and the operator should know. */
  phrase_measured: boolean;
  taps: number;
}

export interface AutoAxes {
  timing: boolean;
  look_changes: boolean;
  palette: boolean;
  energy: boolean;
}

export interface AutoState {
  look: string | null;
  held: boolean;
  color: RGB;
  energy: number;
  rate: number;
  strobe: boolean;
  changes: number;
  palette_changes: number;
  last_change: string;
  axes: AutoAxes;
  /** How often each timed axis fires, in phrases. */
  intervals: { looks: number; palette: number };
  /** How fast each slot's chase runs, relative to everything else. MULTIPLIES
   *  `rate` above rather than replacing it, so auto's energy response still
   *  drives the lot. 1 is identity; 0 freezes that slot where it stands. */
  slot_rates: Record<Slot, number>;
}

export interface SafetyState {
  taper: number;
  reason: string;
}

export interface FixtureState {
  id: number;
  name: string;
  tags: string[];
  head: number | null;
  universe: number;
  address: number;
  is_mover: boolean;
  /** Whether this fixture's profile has a white channel at all — not every
   *  fixture does, and the Color tab uses this to decide whether to offer
   *  a white control for the current target. */
  has_white: boolean;
  /** [x, y, z] in mm — present only for fixtures with geometry. */
  position?: [number, number, number];
  beam_deg?: number;
  intensity?: number;
  color?: RGB;
  /** RGBW white component, 0..1. Present only when non-zero — a swatch drawn
   *  from RGB alone reads colder than the fixture actually is. */
  white?: number;
  /** Shutter position in the fixture's slow-to-fast strobe band, 0..1. Not Hz:
   *  the profile declares no frequency at either end. */
  strobe?: number;
  safety?: SafetyState;
  aim?: { bearing: number; elevation: number };
  lands_on?: string;
  throw_mm?: number;
  /** Where the beam actually lands, [x, y, z] in mm. Sent rather than derived:
   *  `aim.bearing` is the servo's delta from its mount facing, and the mount
   *  facing lives in the calibration the UI does not have. */
  lands_at?: [number, number, number];
  jogging?: boolean;
  captures?: number;
  /** Asked to go further than its own travel allows, so it has stopped at the
   *  rail on these axes. A rig can mix fixtures with different travel, and the
   *  centre is bounded by the most capable head -- so a lesser one can be
   *  asked for somewhere it cannot reach. Absent when it is where it was sent. */
  at_limit?: Axis[];
}

/** The two axes a head travels on, as offsets from its own ball aim. */
export type Axis = "bearing" | "elevation";

export interface VenueState {
  name?: string;
  width?: number;
  depth?: number;
  height?: number;
  ball?: number[];
  crowd?: {
    min_x: number; max_x: number; min_z: number; max_z: number;
    head_band_min: number; head_band_max: number;
  } | null;
  canopy?: { enabled: boolean; height: number; radius: number } | null;
}

export interface Peer {
  id: string;
  name: string;
  connected_for: number;
  last_action: string;
  last_action_ago: number | null;
}

export interface DriftRow {
  head: string;
  bearing: number;
  elevation: number;
  significant: boolean;
}

export interface TaperState {
  /** What a beam whose core is over the crowd is dimmed TO. 0 is a hard guard
   *  and costs every floor-sweep pose; the despacio policy is 0.5. */
  crowd_level: number;
  margin_deg: number;
  slew_per_second: number;
  enabled: boolean;
}

/** The three independent slots. Picking a color must not disturb the movement
 *  and vice versa, which is what having slots at all is for. */
export type Slot = "movement" | "color" | "level";

export interface LookInfo {
  name: string;
  manual_only: boolean;
  kind?: string;
  /** Which tab owns this look. Sent by the engine rather than derived here, so
   *  the UI cannot disagree with the engine about where a look goes. */
  slot: Slot;
  /** The fixture groups this look writes — what the pill filters select on. */
  groups: string[];
  /** Set when this is one step of a chase that also ported — filed under the
   *  parent rather than listed beside it. */
  step_of?: string | null;
  /** A movement look that drives the dimmer itself: it goes dark to travel and
   *  snaps on when it arrives. Flagged because an operator who picks one and
   *  watches the rig start blinking should be able to tell the routine from a
   *  fault. */
  cued?: boolean;
  /** The block behind a parametric look, keyed into `BLOCK_PARAMS` (see
   *  blocks.ts). Null on every ported look, which is the honest answer — a
   *  stored table of DMX has nothing to tune. Its presence is what makes the
   *  Tweak card appear. */
  block?: string | null;
  /** The look's own arguments, over its block's declared defaults. */
  args?: Record<string, ParamValue>;
  /** Hidden from the picker because something covers it now. Hidden, never
   *  removed: looks.json is generated and its round-trip proof needs every
   *  entry present, so going back to the original is one toggle away. */
  retired?: boolean;
  replaced_by?: string | null;
}

/** Anything a parameter can be. Matches `params.KINDS` in the engine. */
export type ParamValue = number | boolean | string | RGB;

/** One knob, as the engine declares it.
 *
 *  The UI renders a control from this rather than hardcoding one per parameter.
 *  That is the whole point of the descriptor: a range used to be written out in
 *  the engine's clamp, in a config Spec and again in a slider's arguments here,
 *  with nothing keeping the three in step. */
export interface ParamSpec {
  name: string;
  label: string;
  /** The last four are block arguments the routine editor renders and the
   *  console's Tweak card does not: a color list, room points, and names in
   *  THIS rig's library. They are validated by the engine against a rig. */
  kind: "number" | "integer" | "bool" | "choice" | "color"
      | "colors" | "points" | "look" | "preset";
  /** Null where there is no fixed default — a fan's sweep is half its width
   *  unless given — so an editor can say "auto" rather than a wrong number. */
  default: ParamValue | unknown[] | null;
  /** Absent rather than null when unbounded — the engine omits these keys. */
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
  choices?: string[];
  /** The sentence shown under the control. */
  help?: string;
  /** An absolute angle from the ball: its real range is the rig's reach on
   *  this axis (`EngineState.reach`), and `min`/`max` only the fallback for
   *  when there is no rig -- the routine editor, which is rig-free. */
  reach?: Axis;
}

/** A parameter that is moving on its own.
 *
 *  `look` absent means it drives a shape macro; otherwise it names the routine
 *  whose parameter is being swung. Two fields rather than one dotted string
 *  because look names contain spaces and slashes ("Duo Pink/Cyan"). */
export interface ModulatorSpec {
  param: string;
  look?: string;
  shape: string;
  /** Cycle length in bars — musical, so it does not change when a slot rate
   *  does. `energy` ignores it entirely. */
  bars: number;
  low: number;
  high: number;
  /** In CYCLES, like every other offset here, so two modulators a half-cycle
   *  apart stay that way at any period. */
  phase: number;
  seed?: number;
}

/** Per slot, per fixture group: which look is loaded. A pinspot color and a
 *  mover color are different decisions and are held separately. */
export type SlotSelection = Record<string, string>;

export interface Selection {
  movement: SlotSelection;
  color: SlotSelection;
  level: SlotSelection;
}

export interface PadRoutine { id: string; variation?: string; params?: Record<string, unknown> }

export interface Preset extends Selection {
  name: string;
  speed?: number;
  master?: number;
  /** Slot rates it was built at. Absent means "leave whatever is dialled in
   *  alone" — a preset that always wrote 1× would silently undo a rate set
   *  after it was saved. */
  rates?: Partial<Record<Slot, number>>;
  /** Per-look tuning for the parametric looks this preset names.
   *
   *  Unlike `rates`, an entry is written even when a routine sits at its
   *  authored values — a rate is a ride the operator keeps a hand on, while a
   *  look's radius is part of the picture the preset exists to get back to.
   *  Absent entirely when the preset names no parametric look. */
  params?: Record<string, Record<string, ParamValue>>;
  /** Where it sits on the grid: a page counting from 1, and a position within
   *  that page. A FIXED place, not a sort order — the whole point of a bank is
   *  that a preset stays where you put it when its neighbours change. The
   *  engine assigns these at load, so they are always present here. */
  bank: number;
  cell: number;
  /** Cross-cutting labels — `intro` / `build` / `drop` / `ambient` are the ones
   *  the filter row offers. A tag finds every drop in the show; a bank cannot,
   *  because a bank is a place. */
  tags: string[];
  /** A routine from the show folder, played over the looks from the next
   *  downbeat when the pad is pressed (milestone 2). */
  routine?: PadRoutine;
}

/** One fixture definition the engine can resolve, with its modes and their
 *  channel counts. Only these manufacturer/model pairs can be patched. */
export interface ProfileInfo {
  manufacturer: string;
  model: string;
  type: string;
  modes: Record<string, number>;
}

/** The DJ link. `listening` is whether the port is open; `driving` is whether
 *  anything has ever spoken through it. Both, because "no bridge configured"
 *  and "a bridge that has gone quiet" need different words on screen. */
export interface SyncState {
  listening: boolean;
  driving: boolean;
  /** Seconds since the last accepted packet, or null if there has been none.
   *  The one number that separates LOCKED from a bridge that died holding the
   *  tempo it last sent. */
  age: number | null;
  phrase: string | null;
  phrase_ends_in: number | null;
  deck: string | null;
  track: string | null;
  port?: {
    port: number; bind: string; received: number; rejected: number;
    /** Understood and deliberately unused (rkbx_link's phrase/next). Not a
     *  fault, so never shown as one. */
    ignored?: number;
    last_reject: string | null;
  };
}

/** Which track the DJ is playing and where in it (F19), from the engine's
 *  transport. `time` is the position in the audio, in seconds. */
export interface TrackState {
  state: "no_track" | "playing" | "stalled" | "paused" | "reverse";
  title: string | null;
  artist: string | null;
  album: string | null;
  duration: number | null;
  source: string | null;
  deck: string | null;
  time: number | null;
  /** Audio seconds per wall second: the DJ's pitch. */
  rate: number;
  age: number | null;
  /** Bump on every track change and every jump (loop, hot cue). */
  track_seq: number;
  jump_seq: number;
  on_air: boolean | null;
  /** Which prepped track this is (F19f). Null with no show folder, or with
   *  nothing identified playing. Absent from an engine older than F19f. */
  match?: TrackMatch | null;
  /** The deck's own beats disagree with the prepped grid. */
  grid_warning?: GridWarning | null;
  /** What the OTHER decks have loaded (milestone 2, beat-link-trigger only),
   *  matched, with their shows built in advance. Absent before F22e. */
  decks?: DeckLoaded[];
}

/** One built-in visuals item on now: its scene and parameters, and how far
 *  into it the show is, in beats. */
export interface VisualItem {
  key: string;
  scene: string;
  params: Record<string, unknown>;
  elapsed: number;
  len: number;
  source: string;
}

export interface VisualsState {
  mode: string;
  /** The show's beat when the snapshot was taken, and how fast it runs: the
   *  page runs on from it between snapshots. bpm 0: paused. */
  beat: number | null;
  bpm: number | null;
  phrase: string | null;
  /** Palette roles as #rrggbb. */
  palette: Record<string, string>;
  items: VisualItem[];
}

export interface OutputsState {
  osc: { target: string; sent: number; errors: number; last_error: string | null;
         on: number } | null;
  /** The MIDI sidecar: what is on is notes and CCs held. Absent before F23c. */
  midi?: { target: string; sent: number; errors: number; last_error: string | null;
           on: number } | null;
  /** Art-Net timecode: the matched track's position. `now` is the last time
   *  sent, null while silent (paused, disarmed, nothing matched). Absent
   *  before F23b. */
  timecode?: { target: string; fps: number; sent: number; errors: number;
               last_error: string | null; now: string | null } | null;
  /** Why an output is off: a bad address in show.json or klights.local.json. */
  problems: string[];
}

/** A track loaded on a deck that is not the master. `ready`: its timeline is
 *  built, so it drives from its first frame when the DJ makes it the master. */
export interface DeckLoaded {
  deck: string;
  title: string | null;
  track_id: string | null;
  has_timeline: boolean;
  ready: boolean;
}

/** What the playing track was matched to in the show folder. Fixed for the
 *  whole play: a change to the folder, a manual link included, applies from
 *  the track's next play, and `stale` says one is waiting. */
export interface TrackMatch {
  track_id: string | null;
  via: "signature" | "rekordbox_id" | "alias" | "title_artist_album"
     | "title_artist" | "ambiguous" | "none";
  /** Every track that fitted; more than one is `ambiguous`. At most five. */
  candidates: string[];
  /** The matched track has a hand-built timeline (F19g). Absent before F19g. */
  has_timeline?: boolean;
  stale: boolean;
}

/** `offset_beats` is how far AHEAD of the grid the deck puts the beat.
 *  "phase" is rekordbox's bar phase (blind to whole bars); "number" is a CDJ's
 *  beat count (whole beats only). */
export interface GridWarning {
  kind: "phase" | "number";
  offset_beats: number;
}

/** Who drives a lane: the track's timeline, the operator (a grab), the
 *  pause idle routine, or the operator's/auto show because the timeline is not
 *  driving at all. */
export type LaneSource = "timeline" | "template" | "operator" | "idle" | "fallback";

/** What the template set plays now (milestone 2). */
export interface TemplateNow {
  set: string | null;
  /** The phrase that chose it ("Chorus", "Verse 2"), "*", or "bars". */
  label: string;
  routine: string;
  start: number;
  fading: boolean;
}

/** Playback (F19i): whether Follow DJ is armed, whether the timeline is on
 *  stage, and who has each lane. */
export interface ProgramState {
  armed: boolean;
  engaged: boolean;
  mode: "timeline" | "template" | "preview" | "idle" | "fallback";
  /** Why the timeline is not driving: "disarmed", "no track", "matching",
   *  "not in the show folder", "no timeline", "compiling", "paused"... */
  reason: string | null;
  beat: number | null;
  bar: number | null;
  lanes: Partial<Record<Slot, LaneSource>>;
  grabbed: Slot[];
  policy: "idle" | "freeze" | "continue";
  problems: number;
  first_problem: string | null;
  /** Per source, how far ahead of its position the lights run. */
  latency_ms: Record<string, number>;
  /** The active template set, the one switching in on the next downbeat
   *  ("off" for none), and every set in the folder. Absent before F22b. */
  set?: string | null;
  pending?: string | null;
  sets?: { id: string; name: string }[];
  template?: TemplateNow | null;
}

/** A designer's preview: who armed it, on which track, playing what. */
export interface PreviewState {
  client: string;
  name: string;
  track_id: string;
  /** Playing an unsaved draft rather than the saved timeline. */
  draft: boolean;
  playing: boolean;
  /** Its program has compiled; until then the operator's show runs. */
  ready: boolean;
}

/** The show folder the engine is pointed at, in summary. The documents
 *  themselves are never in the snapshot. */
export interface ShowFolderState {
  dir: string;
  /** Changes whenever any loaded document does. */
  rev: string;
  tracks: number;
  timelines: number;
  routines: number;
  templates: number;
  palettes?: number;
  errors: number;
  warnings: number;
  /** Files broken since they last loaded, running on their last good version. */
  failed: number;
  /** The first few problems, errors first. */
  problems: string[];
}

export interface EngineState {
  type: "state";
  rev: number;
  /** Engine version. The bundle is served from disk but a phone can hold a
   *  cached one, so this is the only reliable answer to "which engine is this". */
  version: string;
  /** Something is saved that the running show is not using: a patch edit or
   *  a solved calibration. `patch_apply` loads it. */
  pending_patch: boolean;
  /** Which files, in the order they were saved: "rig.json" from a patch edit,
   *  "calibration.json" from Solve & write. Empty when nothing is pending. */
  pending_files: string[];
  /** Fixture definitions available to patch, from shared/fixtures/. */
  profiles: ProfileInfo[];
  /** Live shape controls over whatever movement look is up. Identity is
   *  size 1, spread 0, centre [0, 0]. */
  macro: { size: number; spread: number; center: [number, number] };
  /** The night as an ordered list of cues, or null when the event has none.
   *  `index` is -1 before the first GO — not the same as being on cue 0. */
  cues: {
    name: string; index: number; count: number;
    current: string | null; next: string | null; auto_advance: boolean;
    cues: { name: string; fade: number; hold: number; notes: string }[];
  } | null;
  /** A crossfade is in progress. */
  fading: boolean;
  /** Targets currently bumped by a held flash. */
  flashing: string[];
  /** What the rig is allowed to do with the shutter. `ceiling` is a position in
   *  the fixture's own slow-to-fast band, NOT a frequency — the profile
   *  declares no Hz. See docs/SAFETY.md. */
  strobe_policy: { enabled: boolean; ceiling: number; max_seconds: number };
  event: string;
  taper: TaperState;
  clock: ClockState;
  sync: SyncState;
  /** Absent from an engine older than F19d. */
  track?: TrackState;
  /** Null with no show folder; absent from an engine older than F19f. */
  show?: ShowFolderState | null;
  /** Whether the timeline drives the rig (F19i). Null with no show folder. */
  program?: ProgramState | null;
  /** The designer driving the rig from its own transport (F19j), or null. */
  preview?: PreviewState | null;
  /** A routine pad, waiting for its downbeat or playing. Absent before F22d. */
  pad?: { name: string; routine: string; waiting: boolean } | null;
  /** The other outputs (milestone 3): where OSC goes and how it is doing.
   *  Null when none is configured; absent before F23a. */
  outputs?: OutputsState | null;
  /** What a #visuals page draws (milestone 3). Null without a show folder;
   *  absent before F23d. */
  visuals?: VisualsState | null;
  auto: AutoState;
  looks: LookInfo[];
  /** What the operator has turned on each parametric look, by look name,
   *  over what parametric_looks.json authored. Sparse — only the keys actually moved — and sent
   *  separately from `looks[].params` so the UI can tell "dialled in" from
   *  "authored" and offer a Reset that means something. */
  look_params: Record<string, Record<string, ParamValue>>;
  /** Parameters that are moving on their own. A list, not a map: the key is a
   *  (look, param) pair and the UI shows them as a rack of running modulators. */
  modulators: ModulatorSpec[];
  /** How far this rig's heads can travel from the ball, per axis: the widest
   *  any head can go. The real range of every reach-bounded control. Empty for
   *  a rig with no moving heads. */
  reach: Partial<Record<Axis, [number, number]>>;
  /** Movement routines stacked over the base route, in the order added. Their
   *  offsets ADD — only the base pose layer assigns — which is why stacking
   *  works at all and why the sum is well defined. */
  movement_extra: string[];
  /** How many may be stacked. A cap for legibility, not a limit the maths
   *  needs; sent so the UI and engine cannot disagree about it. */
  movement_stack_max: number;
  /** The seed the last `vary` used, so a variation worth keeping can be
   *  written down and reproduced exactly. */
  vary_seed: number;
  /** What is loaded into each of the three independent slots. */
  selection: Selection;
  presets: Preset[];
  /** The shape of the preset grid. `count` is at least 1 even with nothing
   *  saved, so the empty pads you save onto never disappear. */
  preset_banks: { size: number; count: number };
  /** Fixture groups in the rig, biggest first — the pill filters. */
  groups: string[];
  palette: RGB[];
  palette_index: number;
  master: number;
  blackout: boolean;
  panicked: boolean;
  color_overrides: Record<string, RGB>;
  /** RGBW white by target, 0..1. Separate from color_overrides since a
   *  target can mix fixtures with and without a white channel — only present
   *  for a target where white was explicitly set. */
  white_overrides: Record<string, number>;
  /** Hand dimming by target ("all", a group, or a fixture name), 0..1. A
   *  multiplier over whatever the Bright pattern is doing. */
  level_overrides: Record<string, number>;
  fixtures: FixtureState[];
  venue: VenueState;
  presence: Peer[];
  stats: {
    fps: number; frames: number; drops: number;
    eval_errors: number; worst_error_ms: number;
  };
  notices: string[];
  warnings: string[];
  last_error: string | null;
  drift: DriftRow[] | null;
}

/** Everything the UI can ask the engine to do. */
export type Command =
  | { type: "hello"; name: string }
  | { type: "select_look"; name: string; hold?: boolean; slot?: Slot }
  | { type: "clear_slot"; slot: Slot; group?: string }
  /** Turn a routine's own knobs. Only the keys being changed are sent; the
   *  engine keeps the rest. `reset` drops every override and returns the
   *  look to what parametric_looks.json authored. Refused for a ported look, which
   *  is a stored table of DMX and has nothing to tune. */
  | { type: "look_params"; name: string;
      values?: Record<string, ParamValue>; reset?: boolean }
  /** Bind a parameter to a musical waveform. Omit `look` for a shape macro.
   *  `low`/`high` default to the target's full declared range and are clamped
   *  to it, so a modulator can only sweep where a finger could have dragged.
   *  Binding the same target twice replaces rather than stacking. */
  | { type: "modulate"; param: string; look?: string; shape?: string;
      bars?: number; low?: number; high?: number; phase?: number; seed?: number }
  | { type: "modulate_clear"; param?: string; look?: string; all?: boolean }
  /** Stack another movement routine over the base route. Refused for a look
   *  that is not movement, for the base route itself, and past the cap. */
  | { type: "movement_add"; name: string }
  | { type: "movement_remove"; name?: string; all?: boolean }
  /** Nudge a routine's numbers within their declared ranges, reproducibly.
   *  `amount` is the fraction of each range to move within. Omit `seed` and the
   *  engine picks the next one and publishes it. `bars` is never varied — the
   *  cycle length is a musical decision, not a shape one. */
  | { type: "vary"; name: string; amount?: number; seed?: number }
  | { type: "preset_save"; name: string; bank?: number; cell?: number;
      tags?: string[]; routine?: PadRoutine | null }
  | { type: "preset_apply"; name: string }
  | { type: "preset_delete"; name: string }
  | { type: "preset_move"; name: string; bank: number; cell: number }
  | { type: "preset_tag"; name: string; tags: string[] }
  | { type: "release" }
  | { type: "next_look" }
  | { type: "master"; value: number }
  | { type: "blackout"; on: boolean }
  | { type: "panic" }
  | { type: "clear_panic" }
  | { type: "tap" }
  | { type: "bpm"; value: number }
  | { type: "speed"; value: number }
  | { type: "nudge_phase"; beats: number }
  | { type: "downbeat" }
  | { type: "auto"; axis: keyof AutoAxes; on: boolean }
  | { type: "auto_interval"; axis: "looks" | "palette"; value: number }
  | { type: "energy"; source: "manual" | "phrase"; value?: number }
  | { type: "color"; target: string; color: RGB; white?: number | null; clear?: boolean }
  | { type: "level"; target: string; value?: number; clear?: boolean }
  | { type: "palette_select"; index: number }
  | { type: "jog"; fixture: string; pan: number; tilt: number }
  | { type: "jog_clear"; fixture?: string }
  | { type: "capture"; fixture: string; target: number[]; label: string }
  | { type: "capture_clear"; fixture?: string }
  | { type: "solve"; write?: boolean }
  /** With no readings, the engine checks each head's current jog position. */
  | { type: "drift"; readings?: number[][] }
  | { type: "venue"; crowd?: Partial<{
        min_x: number; max_x: number; min_z: number; max_z: number;
        head_band_min: number; head_band_max: number;
      }>; canopy?: Partial<{ enabled: boolean; height: number; radius: number }> }
  | { type: "taper"; crowd_level?: number; margin_deg?: number;
      slew_per_second?: number; enabled?: boolean }
  | { type: "venue_save" }
  | { type: "patch_add"; name: string; manufacturer: string; model: string;
      mode: string; address?: number; universe?: number; tags?: string[];
      position?: { x: number; y: number; z: number }; beam_deg?: number }
  | { type: "patch_remove"; name: string }
  | { type: "patch_address"; name: string; address: number; universe?: number }
  | { type: "patch_tags"; name: string; tags: string[] }
  | { type: "patch_position"; name: string;
      position: { x: number; y: number; z: number } }
  | { type: "patch_autopatch"; start?: number; universe?: number }
  | { type: "patch_apply" }
  | { type: "go" }
  | { type: "cue_back" }
  | { type: "cue"; index: number }
  | { type: "cue_reset" }
  | { type: "flash"; target: string; on?: boolean }
  | { type: "flash_clear" }
  | { type: "macro"; size?: number; spread?: number;
      center?: [number, number]; reset?: boolean }
  /** How fast ONE slot's chase runs. Distinct from `speed`, which is the clock
   *  and moves the whole show including cue holds and auto boundaries. */
  | { type: "rate"; slot: Slot; value: number }
  | { type: "rate"; reset: true }
  /** Take the clock back from a bridge. Tempo and phase stay put; only who
   *  decides next changes. */
  | { type: "sync_off" }
  /** Follow DJ (F19i): may the matched track's timeline drive the rig. */
  | { type: "follow"; armed: boolean }
  | { type: "program_grab"; slot: Slot }
  | { type: "program_release"; slot?: Slot }
  | { type: "show_latency"; source: string; ms: number }
  /** Milestone 2: switch template set (null = off), on the next downbeat. */
  | { type: "template_set"; id: string | null }
  /** The designer (F19j). Drafts and saves are answered from the worker;
   *  send them with an id and wait for the reply. */
  | { type: "timeline_draft"; doc: unknown }
  | { type: "timeline_save"; doc: unknown; base_rev: string }
  | { type: "routine_draft"; doc: unknown }
  | { type: "routine_save"; doc: unknown; base_rev: string }
  /** Rename a routine and every reference to it (timelines, template sets,
   *  show.json). `routine`, not `id`: `id` is the request's own. */
  | { type: "routine_rename"; routine: string; to: string; base_rev: string }
  /** Delete a routine nothing uses; refused with where, if anything does. */
  | { type: "routine_delete"; routine: string; base_rev: string }
  | { type: "template_draft"; doc: unknown }
  | { type: "template_save"; doc: unknown; base_rev: string }
  /** Rename a template set, and show.json with it if it is the show's. */
  | { type: "template_rename"; template: string; to: string; base_rev: string }
  /** Delete a template set that is not the show's. */
  | { type: "template_delete"; template: string; base_rev: string }
  /** Write show.json, refused if it changed since `base_rev`. */
  | { type: "show_save"; doc: unknown; base_rev: string }
  /** A palette of the library: its own file in palettes/. */
  | { type: "palette_save"; doc: unknown; base_rev: string }
  /** Delete a library palette; its copies stay in their files. */
  | { type: "palette_delete"; palette: string; base_rev: string }
  /** Give copies of a library palette its colors: `files` as /api/palettes lists them. */
  | { type: "palette_sync"; palette: string; files: string[] }
  | { type: "track_link"; track_id: string }
  /** Prep tracks from the DJ's rekordbox collection into the show folder, by
   *  rekordbox id. Answered when the bridge has finished. */
  | { type: "rekordbox_prep"; ids: number[] }
  | { type: "preview_arm"; track_id: string; force?: boolean }
  | { type: "preview_transport"; time_s: number; playing: boolean }
  | { type: "preview_release" };

/** The engine's answer to a command sent with an id (`useEngine.request`). */
export interface Reply {
  type: "reply";
  id: string | number;
  ok: boolean;
  error?: string;
  data?: unknown;
}

export type ConnectionStatus = "connecting" | "open" | "closed";

/** What this client may do, decided by the engine from the URL's token.
 *  `view` can watch but every command it sends is refused. */
export type Tier = "view" | "operate" | "configure";

/** How much of the console to show. A local preference, not a permission —
 *  `Tier` is what the engine enforces, this is only what is on screen. */
export type Mode = "perform" | "design";
