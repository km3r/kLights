import { useSyncExternalStore } from "react";
import { itemOf, parseAudioId, parsePointId, parseWaveId, rowOf } from "./ids";
import type { Edits, RowsDoc } from "./history";
import { uniqueId } from "./model";
import type { Item, Row } from "./model";

/**
 * Clips cut, copied and pasted -- between lanes, editors and tracks -- split
 * and removed, and dragged in from the browser.
 */
/** Remove an item, or an automation point, by its selection id. */
export function removeSelected(d: RowsDoc, id: string): void {
  const waved = parseWaveId(id);
  if (waved) {
    const r = rowOf(d, waved);
    if (r) delete r.wave;
    return;
  }
  const heard = parseAudioId(id);
  if (heard) {
    const r = rowOf(d, heard);
    if (r) delete r.audio;
    return;
  }
  const pt = parsePointId(id);
  if (pt) {
    const r = rowOf(d, pt.row);
    if (r) r.points = (r.points ?? []).filter((q) => q[0] !== pt.beat);
    return;
  }
  for (const r of d.rows) if (r.items) r.items = r.items.filter((i) => i.id !== id);
}

export type ClipKind = "timeline" | "routine";

/** Clips copied from a document: each with the row it came from, at a beat
 *  counted from the start of what was copied, and how long that stretch is.
 *  Kept for the page, so a copy from one track pastes into the next -- but
 *  only into the same kind of document: a timeline's clips are not a
 *  routine's rows. */
export interface ClipBoard {
  kind: ClipKind;
  span: number;
  clips: { row: Pick<Row, "id" | "type" | "target" | "role" | "gap">; item: Item }[];
}

let board: ClipBoard | null = null;

const boardListeners = new Set<() => void>();

export function clipBoard(): ClipBoard | null { return board; }

export function setClipBoard(next: ClipBoard | null): void {
  board = next;
  boardListeners.forEach((l) => l());
}

function subscribeBoard(listener: () => void): () => void {
  boardListeners.add(listener);
  return () => { boardListeners.delete(listener); };
}

/** The clipboard, for a button that can only paste when it holds something. */
export function useClipBoard(): ClipBoard | null {
  return useSyncExternalStore(subscribeBoard, clipBoard);
}

const CLIP_ROWS = new Set(["clips", "hits"]);

function rowKey(row: Row): ClipBoard["clips"][number]["row"] {
  const key: ClipBoard["clips"][number]["row"] = { id: row.id, type: row.type };
  if (row.target !== undefined) key.target = row.target;
  if (row.role !== undefined) key.role = row.role;
  if (row.gap !== undefined) key.gap = row.gap;
  return key;
}

/** Take [start, end) out of a row: an item inside it goes, one across an
 *  edge is trimmed to the outside, and one across both is split in two. */
export function cutRange(d: RowsDoc, row: Row, start: number, end: number): void {
  if (!row.items || end <= start) return;
  const out: Item[] = [];
  for (const it of row.items) {
    const a = it.at;
    const b = it.at + it.len;
    if (b <= start || a >= end) { out.push(it); continue; }
    if (a < start) {
      const head: Item = { ...it, len: start - a };
      if ((head.fade ?? 0) > head.len) head.fade = head.len;
      out.push(head);
    }
    if (b > end) {
      // What carries on after the cut starts there, with no fade in of its own.
      const tail: Item = { ...structuredClone(it), id: a < start ? uniqueId(d, it.id) : it.id,
                           at: end, len: b - end };
      delete tail.fade;
      out.push(tail);
    }
  }
  row.items = out;
}

/** What overlaps [start, end) on the clip and hit rows, trimmed to it. */
export function copyRange(doc: RowsDoc, start: number, end: number, kind: ClipKind): ClipBoard {
  const clips: ClipBoard["clips"] = [];
  for (const row of doc.rows) {
    if (!CLIP_ROWS.has(row.type)) continue;
    for (const it of row.items ?? []) {
      const a = Math.max(it.at, start);
      const b = Math.min(it.at + it.len, end);
      if (b <= a) continue;
      const item: Item = { ...structuredClone(it), at: a - start, len: b - a };
      if (it.at < start) delete item.fade;
      else if ((item.fade ?? 0) > item.len) item.fade = item.len;
      clips.push({ row: rowKey(row), item });
    }
  }
  return { kind, span: end - start, clips };
}

export function copyItem(doc: RowsDoc, id: string, kind: ClipKind): ClipBoard | null {
  for (const row of doc.rows) {
    const it = (row.items ?? []).find((i) => i.id === id);
    if (it) return { kind, span: it.len, clips: [{ row: rowKey(row), item: { ...structuredClone(it), at: 0 } }] };
  }
  return null;
}

/** Paste at a beat: each clip onto the row it came from, else a row of the
 *  same kind, else a new one -- over what is there, which the stretch pasted
 *  clears first. Returns the new items' ids. */
export function pasteBoard(d: RowsDoc, b: ClipBoard, at: number): string[] {
  const rows = new Map<string, Row>();
  for (const { row: key } of b.clips) {
    if (rows.has(key.id)) continue;
    let row = d.rows.find((r) => r.id === key.id && r.type === key.type && r.target === key.target)
      ?? d.rows.find((r) => r.type === key.type && r.target === key.target
                     && (key.role === undefined || r.role === key.role));
    if (!row) {
      row = { ...key, id: uniqueId(d, key.target ?? key.type), items: [] };
      d.rows.push(row);
    }
    cutRange(d, row, at, at + b.span);
    rows.set(key.id, row);
  }
  const ids: string[] = [];
  for (const { row: key, item } of b.clips) {
    const id = uniqueId(d, item.id);
    (rows.get(key.id)!.items ??= []).push({ ...structuredClone(item), id, at: at + item.at });
    ids.push(id);
  }
  return ids;
}

/** Split an item at a beat inside it. Returns the second half's id. */
export function splitAt(d: RowsDoc, id: string, beat: number): string | null {
  for (const row of d.rows) {
    const it = (row.items ?? []).find((i) => i.id === id);
    if (!it) continue;
    if (!(it.at < beat && beat < it.at + it.len)) return null;
    const tail: Item = { ...structuredClone(it), id: uniqueId(d, it.id), at: beat,
                         len: it.at + it.len - beat };
    delete tail.fade;
    it.len = beat - it.at;
    if ((it.fade ?? 0) > it.len) it.fade = it.len;
    row.items!.push(tail);
    return tail.id;
  }
  return null;
}

/** The clip operations as one undoable edit each, for the keys, the clip's
 *  menu and the inspector alike. Each answers with what should be selected:
 *  a paste's first new clip, a duplicate, a split's second half. Ids are
 *  worked out on a copy first -- an edit is applied when React renders, too
 *  late to read them back from it -- and `uniqueId` gives the same answer on
 *  the same document. */
export const clipOps = {
  copy(h: Edits, id: string, kind: ClipKind): boolean {
    const b = h.doc ? copyItem(h.doc, id, kind) : null;
    if (b) setClipBoard(b);
    return b != null;
  },
  cut(h: Edits, id: string, kind: ClipKind): void {
    if (clipOps.copy(h, id, kind)) h.apply((d) => removeSelected(d, id));
  },
  paste(h: Edits, kind: ClipKind, at: number): string | null {
    const b = clipBoard();
    if (!b || b.kind !== kind || !h.doc) return null;
    const ids = pasteBoard(structuredClone(h.doc), b, at);
    h.apply((d) => { pasteBoard(d, b, at); });
    return ids[0] ?? null;
  },
  duplicate(h: Edits, id: string, kind: ClipKind): string | null {
    if (!h.doc) return null;
    const b = copyItem(h.doc, id, kind);
    const it = itemOf(h.doc, id);
    if (!b || !it) return null;
    const at = it.at + it.len;
    const ids = pasteBoard(structuredClone(h.doc), b, at);
    h.apply((d) => { pasteBoard(d, b, at); });
    return ids[0] ?? null;
  },
  split(h: Edits, id: string, beat: number): string | null {
    if (!h.doc) return null;
    const tail = splitAt(structuredClone(h.doc), id, beat);
    if (tail) h.apply((d) => { splitAt(d, id, beat); });
    return tail;
  },
  /** Whether a beat falls inside an item, so it can be split there. */
  inside(doc: RowsDoc | null, id: string, beat: number): boolean {
    const it = doc ? itemOf(doc, id) : undefined;
    return !!it && it.at < beat && beat < it.at + it.len;
  },
};

/** The kind of thing a browser or a drop places, as dragged between them. */
export const PLACE_MIME = "application/x-klights-place";

/** What was dropped, if it is something this page can place: a drag can come
 *  from another window, so its data is read, not trusted. */
export function placeable(raw: string): Placeable | null {
  try {
    const v = JSON.parse(raw) as Partial<Placeable> | null;
    if (v?.kind === "routine" && typeof v.id === "string") return v as Placeable;
    if (v?.kind === "palette" && typeof v.name === "string") return v as Placeable;
    if (v?.kind === "hit" && ["flash", "strobe", "blackout"].includes(v.hit as string)) return v as Placeable;
    if (v?.kind === "look" && typeof v.name === "string") return v as Placeable;
    if (v?.kind === "snapshot" && typeof v.preset === "string") return v as Placeable;
  } catch { /* not ours */ }
  return null;
}

export type Placeable =
  | { kind: "routine"; id: string }
  | { kind: "palette"; name: string; colors?: Record<"primary" | "secondary" | "accent", string> }
  | { kind: "hit"; hit: "flash" | "strobe" | "blackout" }
  /** This rig's own: a look from its library, or a preset as a snapshot. */
  | { kind: "look"; name: string }
  | { kind: "snapshot"; preset: string };
