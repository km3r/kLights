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
}

/** One fixture definition the engine can resolve, with its modes and their
 *  channel counts. Only these manufacturer/model pairs can be patched. */
export interface ProfileInfo {
  manufacturer: string;
  model: string;
  type: string;
  modes: Record<string, number>;
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
  event: string;
  taper: TaperState;
  clock: ClockState;
  auto: AutoState;
  looks: LookInfo[];
  /** What is loaded into each of the three independent slots. */
  selection: Selection;
  presets: Preset[];
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
  | { type: "preset_save"; name: string }
  | { type: "preset_apply"; name: string }
  | { type: "preset_delete"; name: string }
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
  | { type: "drift"; readings: number[][] }
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
  | { type: "patch_autopatch"; start?: number; universe?: number };

export type ConnectionStatus = "connecting" | "open" | "closed";

/** What this client may do, decided by the engine from the URL's token.
 *  `view` can watch but every command it sends is refused. */
export type Tier = "view" | "operate" | "configure";
