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

export interface EngineState {
  type: "state";
  rev: number;
  event: string;
  clock: ClockState;
  auto: AutoState;
  looks: { name: string; manual_only: boolean }[];
  palette: RGB[];
  palette_index: number;
  master: number;
  blackout: boolean;
  panicked: boolean;
  color_overrides: Record<string, RGB>;
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
  | { type: "select_look"; name: string; hold?: boolean }
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
  | { type: "palette_select"; index: number }
  | { type: "jog"; fixture: string; pan: number; tilt: number }
  | { type: "jog_clear"; fixture?: string }
  | { type: "capture"; fixture: string; target: number[]; label: string }
  | { type: "capture_clear"; fixture?: string }
  | { type: "solve"; write?: boolean }
  | { type: "drift"; readings: number[][] };

export type ConnectionStatus = "connecting" | "open" | "closed";
