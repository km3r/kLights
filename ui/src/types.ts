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
}

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

/** The three independent slots. Picking a colour must not disturb the movement
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
}

/** Per slot, per fixture group: which look is loaded. A pinspot colour and a
 *  mover colour are different decisions and are held separately. */
export type SlotSelection = Record<string, string>;

export interface Selection {
  movement: SlotSelection;
  color: SlotSelection;
  level: SlotSelection;
}

export interface Preset extends Selection {
  name: string;
  speed?: number;
  master?: number;
  /** Slot rates it was built at. Absent means "leave whatever is dialled in
   *  alone" — a preset that always wrote 1× would silently undo a rate set
   *  after it was saved. */
  rates?: Partial<Record<Slot, number>>;
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
export type LaneSource = "timeline" | "operator" | "idle" | "fallback";

/** Playback (F19i): whether Follow DJ is armed, whether the timeline is on
 *  stage, and who has each lane. */
export interface ProgramState {
  armed: boolean;
  engaged: boolean;
  mode: "timeline" | "preview" | "idle" | "fallback";
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
  /** A patch edit is saved to rig.json that the running show is not using. */
  pending_patch: boolean;
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
  auto: AutoState;
  looks: LookInfo[];
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
  | { type: "preset_save"; name: string; bank?: number; cell?: number;
      tags?: string[] }
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
  | { type: "color"; target: string; color: RGB; clear?: boolean }
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
  /** The designer (F19j). Drafts and saves are answered from the worker;
   *  send them with an id and wait for the reply. */
  | { type: "timeline_draft"; doc: unknown }
  | { type: "timeline_save"; doc: unknown; base_rev: string }
  | { type: "routine_draft"; doc: unknown }
  | { type: "routine_save"; doc: unknown; base_rev: string }
  | { type: "track_link"; track_id: string }
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
