import type { RowsDoc } from "./history";
import type { Item, Row } from "./model";

/**
 * What the editors select, by id: a row or an item by its own, and a point,
 * a wave or an audio band -- which have none -- by their row's. The
 * clipboard, the lanes and the automation editors all read these, so they
 * live apart from all three and import nothing.
 */

export function rowOf(doc: RowsDoc, id: string): Row | undefined {
  return doc.rows.find((r) => r.id === id);
}

export function itemOf(doc: RowsDoc, id: string): Item | undefined {
  for (const r of doc.rows) {
    const it = (r.items ?? []).find((i) => i.id === id);
    if (it) return it;
  }
  return undefined;
}

/** A point's selection id: points have no ids of their own, so the row and
 *  the beat name one. */
export function pointId(rowId: string, beat: number): string {
  return `pt:${rowId}:${beat}`;
}

export function parsePointId(id: string | null): { row: string; beat: number } | null {
  const m = id ? /^pt:(.+):(-?[\d.]+(?:e-?\d+)?)$/.exec(id) : null;
  return m ? { row: m[1]!, beat: Number(m[2]) } : null;
}

/** A lane's wave's selection id: a row has at most one. */
export function waveId(rowId: string): string {
  return `wave:${rowId}`;
}

export function parseWaveId(id: string | null): string | null {
  return id?.startsWith("wave:") ? id.slice(5) : null;
}

/** A lane's audio band's selection id: a row has at most one. */
export function audioId(rowId: string): string {
  return `audio:${rowId}`;
}

export function parseAudioId(id: string | null): string | null {
  return id?.startsWith("audio:") ? id.slice(6) : null;
}
