/**
 * The engine's commands: what each takes, what each answers with, and who may
 * send it.
 *
 * GENERATED from the `@command` declarations in engine/controller/ (see
 * engine/commands.py) by engine/tests/dump_designer_fixtures.py. Do not edit:
 * change the declaration and run that script. `test_api` fails while this
 * file is out of date.
 */

export type Tier = "view" | "operate" | "configure";

/** Each command's arguments, by name. An optional argument may also be null,
 *  which the engine reads as absent. */
export interface CommandArgs {
  auto: {
    axis: "timing" | "look_changes" | "palette" | "energy";
    on: boolean;
  };
  auto_interval: {
    axis: "looks" | "palette";
    /** phrases between changes */
    value: number;
  };
  blackout: {
    on?: boolean | null;
  };
  bpm: {
    value: number;
  };
  /** Record where a head is pointing right now, against a known target. */
  capture: {
    fixture: string;
    /** [x, y, z] in millimetres */
    target: number[];
    label?: string | null;
    pan?: number | null;
    tilt?: number | null;
  };
  capture_clear: {
    fixture?: string | null;
  };
  clear_panic: {};
  clear_slot: {
    slot: "movement" | "color" | "level";
    group?: string | null;
  };
  /** Per-fixture picker and global quick palette, in one command. */
  color: {
    /** a group, a fixture name, or "all" */
    target?: string | null;
    /** [r, g, b], each 0 to 1 */
    color: number[];
    white?: number | null;
    clear?: boolean | null;
  };
  /** Jump straight to a cue. Fades like any other take. */
  cue: {
    index: number;
  };
  cue_back: {};
  /** Duplicate the cue called `cue` as `to`, straight after it: the entry as
   *  the file has it, under the new name.
   */
  cue_copy: {
    cue: string;
    to?: string | null;
    /** the rev you opened, or "" for a new file -- so a save never overwrites
     *  a change it has not seen
     */
    base_rev: string;
  };
  /** Delete the cue called `cue`. Not the last one: a cue list cannot be
   *  empty. If it was the current cue, the list falls back to the cue before
   *  it and what is on stage stays.
   */
  cue_delete: {
    cue: string;
    /** the rev you opened, or "" for a new file -- so a save never overwrites
     *  a change it has not seen
     */
    base_rev: string;
  };
  /** Move the cue called `cue` to place `to` in the running order (0 is
   *  first). The list keeps its place: the current cue stays current.
   */
  cue_move: {
    cue: string;
    /** a place in the running order, 0 for first */
    to: number;
    /** the rev you opened, or "" for a new file -- so a save never overwrites
     *  a change it has not seen
     */
    base_rev: string;
  };
  cue_reset: {};
  /** Write one cue: a new one (`was` absent) at place `at` in the running
   *  order, last when not said -- or a change to the one called `was`, which
   *  renames it when `cue.name` differs and moves it when `at` is given.
   */
  cue_save: {
    cue: object;
    /** the cue's name as you opened it */
    was?: string | null;
    /** a place in the running order, 0 for first; last when not said */
    at?: number | null;
    /** the rev you opened, or "" for a new file -- so a save never overwrites
     *  a change it has not seen
     */
    base_rev: string;
  };
  /** Name the cue list itself: the title of the console's GO card. */
  cues_name: {
    name?: string | null;
    /** the rev you opened, or "" for a new file -- so a save never overwrites
     *  a change it has not seen
     */
    base_rev: string;
  };
  /** Read cues.json again now -- after an edit by hand, or by MCP. A file
   *  that does not load is said, and the list that is running runs on.
   */
  cues_reload: {};
  downbeat: {};
  /** Compare fresh ball readings against the stored calibration. */
  drift: {
    /** one [pan, tilt] per head, in rig order */
    readings?: number[][] | null;
  };
  energy: {
    source?: "manual" | "phrase" | null;
    value?: number | null;
  };
  /** Momentary bump on a group, a fixture, or everything. */
  flash: {
    /** a group, a fixture name, or "all" */
    target?: string | null;
    on?: boolean | null;
  };
  /** Release every bump. */
  flash_clear: {};
  /** Arm or disarm Follow DJ: whether the timeline may drive the rig. */
  follow: {
    armed: boolean;
  };
  go: {};
  hello: {
    name?: string | null;
  };
  /** Drive one head at literal DMX so it can be aimed by eye. */
  jog: {
    fixture: string;
    pan: number;
    tilt: number;
  };
  jog_clear: {
    fixture?: string | null;
  };
  /** Dim one fixture, one group, or everything, by hand. */
  level: {
    /** a group, a fixture name, or "all" */
    target?: string | null;
    /** 0-1, a multiplier on the look */
    value?: number | null;
    clear?: boolean | null;
  };
  /** Delete a block look nothing names. One a cue, a preset, a routine or a
   *  timeline still names is refused with where, so a delete can never leave
   *  a show asking for a look that is gone. A stored look cannot be deleted
   *  at all -- looks.json is generated -- only hidden.
   */
  look_delete: {
    look: string;
    /** the rev you opened, or "" for a new file -- so a save never overwrites
     *  a change it has not seen
     */
    base_rev: string;
  };
  /** Hide a look from the picker (`hidden`, true unless said), or show it
   *  again. Any look, stored or block: hidden is not removed -- it still
   *  plays for a cue or a routine that names it, and auto mode skips it.
   */
  look_hide: {
    look: string;
    hidden?: boolean | null;
    /** a look's name */
    replaced_by?: string | null;
    note?: string | null;
    /** the rev you opened, or "" for a new file -- so a save never overwrites
     *  a change it has not seen
     */
    base_rev: string;
  };
  /** Turn one routine's own knobs, live. */
  look_params: {
    /** a look's name, as the picker lists it */
    name: string;
    /** argument -> value, as the block declares them */
    values?: object | null;
    reset?: boolean | null;
  };
  /** Write one block look: a new one (`was` absent), or a change to the one
   *  called `was` -- which renames it when `look.name` differs.
   */
  look_save: {
    look: object;
    /** the look's name as you opened it */
    was?: string | null;
    hides?: {
      look: string;
      note?: string | null;
    } | null;
    /** the rev you opened, or "" for a new file -- so a save never overwrites
     *  a change it has not seen
     */
    base_rev: string;
  };
  /** Read both look files again now -- after an edit by hand, or by MCP. A
   *  file that does not load is said, and the library that is running runs
   *  on.
   */
  looks_reload: {};
  /** Live shape controls over whatever movement look is up. */
  macro: {
    size?: number | null;
    spread?: number | null;
    /** [bearing, elevation] in degrees */
    center?: number[] | null;
    reset?: boolean | null;
  };
  master: {
    /** 0 to 1, the show's overall level */
    value: number;
  };
  /** Bind a parameter to a musical waveform. */
  modulate: {
    param: string;
    look?: string | null;
    shape?: "sine" | "triangle" | "ramp" | "saw" | "square" | "hold" | "energy" | null;
    bars?: number | null;
    low?: number | null;
    high?: number | null;
    phase?: number | null;
    seed?: number | null;
  };
  /** Stop one modulator, or all of them. */
  modulate_clear: {
    param?: string | null;
    look?: string | null;
    all?: boolean | null;
  };
  /** Stack another movement routine on top of the one that is up. */
  movement_add: {
    /** a look's name, as the picker lists it */
    name: string;
  };
  movement_remove: {
    name?: string | null;
    all?: boolean | null;
  };
  next_look: {};
  nudge_phase: {
    beats: number;
  };
  /** Delete a library palette. Its copies stay where they are -- each
   *  timeline and set keeps its own -- so nothing goes dark.
   */
  palette_delete: {
    palette: string;
    /** the rev you opened, or "" for a new file -- so a save never overwrites
     *  a change it has not seen
     */
    base_rev: string;
  };
  /** Write a library palette. Its copies in timelines and sets are not
   *  touched: `palette_sync` does that, on request. Its name must be its own:
   *  copies are found by name, so two library palettes of one name would both
   *  claim the same copies.
   */
  palette_save: {
    /** the whole document */
    doc: object;
    /** the rev you opened, or "" for a new file -- so a save never overwrites
     *  a change it has not seen
     */
    base_rev: string;
  };
  palette_select: {
    index: number;
  };
  /** Give copies of a library palette its colors: `files` are the timelines
   *  and sets to update, as /api/palettes lists them.
   */
  palette_sync: {
    palette: string;
    /** the timelines and sets to update, as /api/palettes lists them */
    files: string[];
  };
  panic: {};
  patch_add: {
    name: string;
    manufacturer: string;
    model: string;
    mode: string;
    address?: number | null;
    universe?: number | null;
    tags?: string[] | null;
    /** {"x", "y", "z"} in millimetres, y up, from the front-left floor */
    position?: {
      x: number;
      y: number;
      z: number;
    } | null;
    beam_deg?: number | null;
    notes?: string | null;
  };
  patch_address: {
    name: string;
    address: number;
    universe?: number | null;
  };
  patch_apply: {};
  patch_autopatch: {
    start?: number | null;
    universe?: number | null;
  };
  patch_position: {
    name: string;
    position: {
      x: number;
      y: number;
      z: number;
    };
  };
  patch_remove: {
    name: string;
  };
  patch_tags: {
    name: string;
    tags?: string[] | null;
  };
  preset_apply: {
    name: string;
  };
  preset_delete: {
    name: string;
  };
  /** Put a preset on a different pad, SWAPPING with whatever is there. */
  preset_move: {
    name: string;
    bank?: number | null;
    cell?: number | null;
  };
  /** Snapshot all three slots plus the tempo feel under one name. */
  preset_save: {
    name: string;
    bank?: number | null;
    cell?: number | null;
    tags?: string[] | null;
    /** {"id": "<routine id>", "variation"?, "params"?}, or null for a pad of
     *  looks only
     */
    routine?: object | null;
  };
  preset_tag: {
    name: string;
    tags?: string[] | null;
  };
  /** The designer takes the stage: its transport drives the rig through this
   *  track's timeline. Refused while a DJ is playing, unless forced.
   */
  preview_arm: {
    track_id: string;
    force?: boolean | null;
  };
  preview_release: {};
  /** Where the designer's audio is, and whether it is playing. */
  preview_transport: {
    /** seconds into the track */
    time_s: number;
    playing?: boolean | null;
  };
  /** Take a lane from the timeline without changing what is on it. */
  program_grab: {
    slot: "movement" | "color" | "level";
  };
  /** Give a lane (or every lane) back to the timeline. */
  program_release: {
    slot?: "movement" | "color" | "level" | null;
  };
  /** How fast one slot's chase runs, relative to everything else. */
  rate: {
    slot?: "movement" | "color" | "level" | null;
    value?: number | null;
    reset?: boolean | null;
  };
  /** Prep tracks from the DJ's rekordbox collection into the show folder:
   *  what the designer's collection browser picked, by rekordbox id.
   */
  rekordbox_prep: {
    /** rekordbox track ids */
    ids: number[];
  };
  release: {};
  /** Delete a routine nothing uses. One that a timeline, a template set or
   *  show.json still names is refused with where, so a delete can never leave
   *  a show playing a routine that is gone.
   */
  routine_delete: {
    routine: string;
    /** the rev you opened, or "" for a new file -- so a save never overwrites
     *  a change it has not seen
     */
    base_rev: string;
  };
  /** Check an unsaved routine: the format's rules, then the routine bound to
   *  THIS rig as it is -- and in each of its variations -- for what will not
   *  work here (a role no fixture carries, a block with nothing to aim).
   */
  routine_draft: {
    /** the whole document */
    doc: object;
  };
  /** Rename a routine and every reference to it (showfiles.rename_routine:
   *  new file first, references next, old file last). Answered with the files
   *  written; the folder reloads after.
   */
  routine_rename: {
    routine: string;
    to: string;
    /** the rev you opened, or "" for a new file -- so a save never overwrites
     *  a change it has not seen
     */
    base_rev: string;
  };
  routine_save: {
    /** the whole document */
    doc: object;
    /** the rev you opened, or "" for a new file -- so a save never overwrites
     *  a change it has not seen
     */
    base_rev: string;
  };
  /** Select a look INTO ITS OWN SLOT, worked out from what it sets. */
  select_look: {
    /** a look's name, as the picker lists it */
    name: string;
    slot?: "movement" | "color" | "level" | null;
    hold?: boolean | null;
  };
  /** How far ahead of a source's position the lights run, in ms. Applied at
   *  once and saved to the show folder's show.json (decided with the user,
   *  F19i), where it follows the show.
   */
  show_latency: {
    /** e.g. "rkbx" */
    source: string;
    ms: number;
  };
  /** Read the show folder again now, rather than at the next poll. */
  show_reload: {};
  /** Write show.json: the show's template set, the pause policy and idle
   *  routine, Follow's start. Refused if it changed since `base_rev` -- the
   *  phone's latency slider writes it too. Applies when the folder reloads,
   *  as any change to it does.
   */
  show_save: {
    /** the whole document */
    doc: object;
    /** the rev you opened, or "" for a new file -- so a save never overwrites
     *  a change it has not seen
     */
    base_rev: string;
  };
  /** Solve from the captures taken so far, optionally writing them. */
  solve: {
    write?: boolean | null;
  };
  speed: {
    value: number;
  };
  /** A position from something that already knows -- a CDJ, rekordbox. */
  sync: {
    bpm?: unknown | null;
    beat?: unknown | null;
    beat_in_bar?: unknown | null;
    phrase_measured?: unknown | null;
    phrase_label?: unknown | null;
    phrase_ends_in?: unknown | null;
    phrase_into?: unknown | null;
    source?: unknown | null;
    deck?: unknown | null;
    track?: unknown | null;
    track_time?: unknown | null;
    title?: unknown | null;
    artist?: unknown | null;
    album?: unknown | null;
    duration?: unknown | null;
    bpm_original?: unknown | null;
    pitch?: unknown | null;
    playing?: unknown | null;
    on_air?: unknown | null;
    master?: unknown | null;
    rekordbox_id?: unknown | null;
    signature?: unknown | null;
    beat_number?: unknown | null;
    loaded_deck?: unknown | null;
    loaded_title?: unknown | null;
    loaded_artist?: unknown | null;
    loaded_album?: unknown | null;
    loaded_duration?: unknown | null;
    loaded_rekordbox_id?: unknown | null;
    loaded_signature?: unknown | null;
  };
  /** Take the clock back by hand. */
  sync_off: {};
  tap: {};
  /** The taper policy: how far a beam over the crowd is dimmed, and how
   *  smoothly. `crowd_level` 0 restores a hard guard at the cost of every
   *  floor-sweep pose.
   */
  taper: {
    crowd_level?: number | null;
    margin_deg?: number | null;
    slew_per_second?: number | null;
    enabled?: boolean | null;
  };
  /** Delete a template set that is not the show's. */
  template_delete: {
    template: string;
    /** the rev you opened, or "" for a new file -- so a save never overwrites
     *  a change it has not seen
     */
    base_rev: string;
  };
  /** Check an unsaved template set: the format's rules, then what it asks of
   *  the routines (one that is not there, a variation or parameter it lacks)
   *  -- the same checks a folder load makes, before the save.
   */
  template_draft: {
    /** the whole document */
    doc: object;
  };
  /** Rename a template set, and show.json with it if it is the show's. */
  template_rename: {
    template: string;
    to: string;
    /** the rev you opened, or "" for a new file -- so a save never overwrites
     *  a change it has not seen
     */
    base_rev: string;
  };
  /** Write a template set, refused if the file changed since `base_rev`. */
  template_save: {
    /** the whole document */
    doc: object;
    /** the rev you opened, or "" for a new file -- so a save never overwrites
     *  a change it has not seen
     */
    base_rev: string;
  };
  /** Switch template set -- a vibe, live. It takes over on the next downbeat
   *  (decided with the user), crossfading over its own transition; `set:
   *  null` turns templates off. Not saved: show.json's `template_set` is what
   *  the next start begins with.
   */
  template_set: {
    /** a set's id, or null for none */
    set?: string | null;
  };
  /** Delete a track's timeline and keep the track: the template set (or the
   *  operator's show) lights it again. Refused if the file changed since
   *  `base_rev`. Like a save, it applies from the track's next play: one
   *  playing now keeps the timeline it started with.
   */
  timeline_delete: {
    track: string;
    /** the rev you opened, or "" for a new file -- so a save never overwrites
     *  a change it has not seen
     */
    base_rev: string;
  };
  /** Check an unsaved timeline: the format's rules, then a compile against
   *  THIS rig. Answers with errors, warnings and problems. If the designer is
   *  driving the rig on that track, the draft is what plays.
   */
  timeline_draft: {
    /** the whole document */
    doc: object;
  };
  /** Write a timeline, refused if the file changed since `base_rev`. It
   *  applies to the track from its next play, like any folder change.
   */
  timeline_save: {
    /** the whole document */
    doc: object;
    /** the rev you opened, or "" for a new file -- so a save never overwrites
     *  a change it has not seen
     */
    base_rev: string;
  };
  /** Take a track out of the show: tracks/<id>.json and, with it, that
   *  track's timeline and waveform (showfiles.remove_track -- the track's own
   *  file last). `base_rev` is the track's rev and `timeline_rev` its
   *  timeline's ("" for none), so neither goes unseen. Refused while the
   *  track is on the rig: the deck's, or Studio's preview. Answered with the
   *  files removed; the audio it names is never touched.
   */
  track_delete: {
    track: string;
    /** the rev you opened, or "" for a new file -- so a save never overwrites
     *  a change it has not seen
     */
    base_rev: string;
    /** its timeline's rev as you read it, or "" if it had none */
    timeline_rev?: string | null;
  };
  /** This playing track IS that prepped track -- for a guest's copy the
   *  matcher could not place (a different tag, a different export).
   */
  track_link: {
    track_id: string;
    title?: string | null;
    artist?: string | null;
    album?: string | null;
  };
  /** Nudge a routine's numbers around, reproducibly. */
  vary: {
    /** a look's name, as the picker lists it */
    name: string;
    /** 0-1: how much of each range to move within */
    amount?: number | null;
    seed?: number | null;
  };
  /** Edit the crowd zone and canopy live, from the phone at load-in. */
  venue: {
    crowd?: {
      min_x?: number | null;
      max_x?: number | null;
      min_z?: number | null;
      max_z?: number | null;
      head_band_min?: number | null;
      head_band_max?: number | null;
    } | null;
    canopy?: {
      enabled?: boolean | null;
      height?: number | null;
      radius?: number | null;
    } | null;
  };
  /** Write the live venue back to venue.json, preserving its comments. */
  venue_save: {};
}

/** What a command sent with `request` answers with, for the commands that
 *  answer with data. Any other answers with none. */
export interface CommandReplies {
  cue_copy: {
    /** cues.json's rev now */
    rev: string;
    name?: string | null;
    warnings?: string[] | null;
    deleted?: string | null;
    /** where the cue is now in the running order, 0 first */
    index?: number | null;
    /** how many cues the list holds */
    count?: number | null;
  };
  cue_delete: {
    /** cues.json's rev now */
    rev: string;
    name?: string | null;
    warnings?: string[] | null;
    deleted?: string | null;
    /** where the cue is now in the running order, 0 first */
    index?: number | null;
    /** how many cues the list holds */
    count?: number | null;
  };
  cue_move: {
    /** cues.json's rev now */
    rev: string;
    name?: string | null;
    warnings?: string[] | null;
    deleted?: string | null;
    /** where the cue is now in the running order, 0 first */
    index?: number | null;
    /** how many cues the list holds */
    count?: number | null;
  };
  cue_save: {
    /** cues.json's rev now */
    rev: string;
    name?: string | null;
    warnings?: string[] | null;
    deleted?: string | null;
    /** where the cue is now in the running order, 0 first */
    index?: number | null;
    /** how many cues the list holds */
    count?: number | null;
  };
  cues_name: {
    /** cues.json's rev now */
    rev: string;
    name?: string | null;
    warnings?: string[] | null;
    deleted?: string | null;
    /** where the cue is now in the running order, 0 first */
    index?: number | null;
    /** how many cues the list holds */
    count?: number | null;
  };
  cues_reload: {
    /** cues.json's rev now */
    rev: string;
    name?: string | null;
    warnings?: string[] | null;
    deleted?: string | null;
    /** where the cue is now in the running order, 0 first */
    index?: number | null;
    /** how many cues the list holds */
    count?: number | null;
  };
  look_delete: {
    /** parametric_looks.json's rev now */
    rev: string;
    /** cues a rename moved to the new name */
    cues: number;
    /** presets a rename moved to the new name */
    presets: number;
    name?: string | null;
    warnings?: string[] | null;
    /** show-folder files a rename rewrote */
    written?: string[] | null;
    deleted?: string | null;
    hidden?: boolean | null;
  };
  look_hide: {
    /** parametric_looks.json's rev now */
    rev: string;
    /** cues a rename moved to the new name */
    cues: number;
    /** presets a rename moved to the new name */
    presets: number;
    name?: string | null;
    warnings?: string[] | null;
    /** show-folder files a rename rewrote */
    written?: string[] | null;
    deleted?: string | null;
    hidden?: boolean | null;
  };
  look_save: {
    /** parametric_looks.json's rev now */
    rev: string;
    /** cues a rename moved to the new name */
    cues: number;
    /** presets a rename moved to the new name */
    presets: number;
    name?: string | null;
    warnings?: string[] | null;
    /** show-folder files a rename rewrote */
    written?: string[] | null;
    deleted?: string | null;
    hidden?: boolean | null;
  };
  looks_reload: {
    /** parametric_looks.json's rev now */
    rev: string;
    /** cues a rename moved to the new name */
    cues: number;
    /** presets a rename moved to the new name */
    presets: number;
    name?: string | null;
    warnings?: string[] | null;
    /** show-folder files a rename rewrote */
    written?: string[] | null;
    deleted?: string | null;
    hidden?: boolean | null;
  };
  palette_delete: {
    /** the file removed, in the show folder */
    deleted: string;
  };
  palette_save: {
    /** the file's rev now: the next save quotes it */
    rev: string;
    /** where it was written, in the show folder */
    path: string;
  };
  palette_sync: {
    /** every file rewritten, in the show folder */
    written: string[];
  };
  preview_arm: {
    track_id: string;
  };
  rekordbox_prep: {
    results: {
      status: string;
      track_id: string;
      rekordbox_ids: number[];
      title: string;
      artist: string;
      signature: boolean;
      notes: string[];
    }[];
    skipped: {
      rekordbox_id: number;
      title: string;
      artist: string;
      reason: string;
    }[];
    show_dir?: string | null;
  };
  routine_delete: {
    /** the file removed, in the show folder */
    deleted: string;
  };
  routine_draft: {
    errors: string[];
    warnings: string[];
    /** what will not work on this rig */
    problems: string[];
  };
  routine_rename: {
    /** every file rewritten, in the show folder */
    written: string[];
  };
  routine_save: {
    /** the file's rev now: the next save quotes it */
    rev: string;
    /** where it was written, in the show folder */
    path: string;
  };
  show_latency: {
    source: string;
    ms: number;
  };
  show_save: {
    /** the file's rev now: the next save quotes it */
    rev: string;
    /** where it was written, in the show folder */
    path: string;
  };
  template_delete: {
    /** the file removed, in the show folder */
    deleted: string;
  };
  template_draft: {
    errors: string[];
    warnings: string[];
    /** what will not work on this rig */
    problems: string[];
  };
  template_rename: {
    /** every file rewritten, in the show folder */
    written: string[];
  };
  template_save: {
    /** the file's rev now: the next save quotes it */
    rev: string;
    /** where it was written, in the show folder */
    path: string;
  };
  template_set: {
    /** the set taking over on the next downbeat; null for none */
    pending?: string | null;
  };
  timeline_delete: {
    /** the file removed, in the show folder */
    deleted: string;
  };
  timeline_draft: {
    errors: string[];
    warnings: string[];
    /** what will not work on this rig */
    problems: string[];
  };
  timeline_save: {
    /** the file's rev now: the next save quotes it */
    rev: string;
    /** where it was written, in the show folder */
    path: string;
  };
  track_delete: {
    /** the files removed, the track's own last */
    deleted: string[];
  };
  track_link: {
    queued: boolean;
    track_id: string;
    applies: "next_play";
  };
}

export type CommandName = keyof CommandArgs;

/** One command, as sent: its name as `type`, and its arguments. */
export type Command = { [K in CommandName]: { type: K } & CommandArgs[K] }[CommandName];

/** What command `K` answers with. */
export type ReplyData<K extends CommandName> =
  K extends keyof CommandReplies ? CommandReplies[K] : undefined;

/** Who may send each command. */
export const COMMAND_TIERS: Readonly<Record<CommandName, Tier>> = {
  auto: "operate",
  auto_interval: "operate",
  blackout: "operate",
  bpm: "operate",
  capture: "configure",
  capture_clear: "configure",
  clear_panic: "operate",
  clear_slot: "operate",
  color: "operate",
  cue: "operate",
  cue_back: "operate",
  cue_copy: "configure",
  cue_delete: "configure",
  cue_move: "configure",
  cue_reset: "operate",
  cue_save: "configure",
  cues_name: "configure",
  cues_reload: "configure",
  downbeat: "operate",
  drift: "configure",
  energy: "operate",
  flash: "operate",
  flash_clear: "operate",
  follow: "operate",
  go: "operate",
  hello: "view",
  jog: "configure",
  jog_clear: "configure",
  level: "operate",
  look_delete: "configure",
  look_hide: "configure",
  look_params: "operate",
  look_save: "configure",
  looks_reload: "configure",
  macro: "operate",
  master: "operate",
  modulate: "operate",
  modulate_clear: "operate",
  movement_add: "operate",
  movement_remove: "operate",
  next_look: "operate",
  nudge_phase: "operate",
  palette_delete: "configure",
  palette_save: "configure",
  palette_select: "operate",
  palette_sync: "configure",
  panic: "operate",
  patch_add: "configure",
  patch_address: "configure",
  patch_apply: "configure",
  patch_autopatch: "configure",
  patch_position: "configure",
  patch_remove: "configure",
  patch_tags: "configure",
  preset_apply: "operate",
  preset_delete: "operate",
  preset_move: "operate",
  preset_save: "operate",
  preset_tag: "operate",
  preview_arm: "configure",
  preview_release: "configure",
  preview_transport: "configure",
  program_grab: "operate",
  program_release: "operate",
  rate: "operate",
  rekordbox_prep: "configure",
  release: "operate",
  routine_delete: "configure",
  routine_draft: "configure",
  routine_rename: "configure",
  routine_save: "configure",
  select_look: "operate",
  show_latency: "configure",
  show_reload: "configure",
  show_save: "configure",
  solve: "configure",
  speed: "operate",
  sync: "operate",
  sync_off: "operate",
  tap: "operate",
  taper: "configure",
  template_delete: "configure",
  template_draft: "configure",
  template_rename: "configure",
  template_save: "configure",
  template_set: "operate",
  timeline_delete: "configure",
  timeline_draft: "configure",
  timeline_save: "configure",
  track_delete: "configure",
  track_link: "configure",
  vary: "operate",
  venue: "configure",
  venue_save: "configure",
};

const RANK: Readonly<Record<Tier, number>> = { view: 0, operate: 1, configure: 2 };

/** Whether a console at `tier` may send `command` -- the engine's own rule,
 *  so a control can say so before it is pressed. */
export function mayRun(tier: Tier, command: CommandName): boolean {
  return RANK[tier] >= RANK[COMMAND_TIERS[command]];
}
