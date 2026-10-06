import table from "./blocks.generated.json";
import type { ParamSpec, Slot } from "./types";

/**
 * What every block takes, as the engine declares it.
 *
 * Generated from `engine/blocks.py` by `engine/tests/dump_designer_fixtures.py`
 * and held current by `test_api`. Both the routine editor and the console's
 * Tweak card render from this one table — before it, the editor carried a
 * hand-typed copy of every block's defaults, steps and units, and only the
 * argument NAMES were checked against the engine.
 *
 * A build-time import rather than part of the live snapshot: these are
 * constants of the engine build, and the snapshot goes out ten times a second
 * to every phone in the room. The bundle and the engine are committed together,
 * so they cannot disagree.
 */

interface BlockEntry {
  slot: Slot | null;
  params: ParamSpec[];
}

const blocks = table.blocks as unknown as Record<string, BlockEntry>;

/** Each block's arguments, in the order the engine declares them. */
export const BLOCK_PARAMS: Record<string, ParamSpec[]> = Object.fromEntries(
  Object.entries(blocks).map(([name, b]) => [name, b.params]));

/** The slot each block drives; null for the rig-bound adapters. */
export const BLOCK_SLOTS: Record<string, Slot | null> = Object.fromEntries(
  Object.entries(blocks).map(([name, b]) => [name, b.slot]));

/** The four shape macros, described exactly as a block's arguments are. */
export const MACRO_PARAMS = table.macros as unknown as ParamSpec[];

/** The waveforms a modulator can follow. */
export const MODULATOR_SHAPES: string[] = table.modulator_shapes;

/** The waveforms an automation lane's wave can follow (`waves.SHAPES`): the
 *  modulator's, less `energy`, which a show folder has no room to listen to. */
export const WAVE_SHAPES: string[] = table.wave_shapes;
