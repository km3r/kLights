import { useCallback, useState } from "react";
import { BEATS_PER_BAR } from "./model";
import type { Row, TimelineDoc } from "./model";

/**
 * An editor's undo/redo history over its whole document.
 *
 * Every edit is a pure function of the document -- copy, change, push -- so undo
 * is a pointer move and nothing can half-apply.
 */
export type Snap = "beat" | "bar" | "phrase";

/** Any document made of rows: a track's timeline, or a routine. */
export interface RowsDoc { rows: Row[]; [key: string]: unknown }

interface HistoryState<D> {
  past: D[];
  doc: D | null;
  future: D[];
  saved: D | null;
}

// Any document: a timeline and a routine are rows, a template set is not, and
// undo needs nothing of either.
export function useHistory<D extends object = TimelineDoc>() {
  const [h, setH] = useState<HistoryState<D>>({ past: [], doc: null, future: [], saved: null });
  const [snap, setSnap] = useState<Snap>("bar");
  const [phrases, setPhrases] = useState<number[]>([]);
  const [recording, setRecording] = useState(false);
  const [listView, setListView] = useState(false);

  const setBase = useCallback((doc: D) => {
    setH({ past: [], doc, future: [], saved: doc });
  }, []);

  /** One undoable edit: `change` mutates a copy. */
  const apply = useCallback((change: (draft: D) => void) => {
    setH((prev) => {
      if (!prev.doc) return prev;
      const next = structuredClone(prev.doc);
      change(next);
      return { past: [...prev.past.slice(-199), prev.doc], doc: next, future: [],
               saved: prev.saved };
    });
  }, []);

  const undo = useCallback(() => setH((prev) => {
    const last = prev.past[prev.past.length - 1];
    if (!last || !prev.doc) return prev;
    return { past: prev.past.slice(0, -1), doc: last, future: [prev.doc, ...prev.future],
             saved: prev.saved };
  }), []);
  const redo = useCallback(() => setH((prev) => {
    const next = prev.future[0];
    if (!next || !prev.doc) return prev;
    return { past: [...prev.past, prev.doc], doc: next, future: prev.future.slice(1),
             saved: prev.saved };
  }), []);
  const markSaved = useCallback((doc: D) => {
    setH((prev) => ({ ...prev, saved: doc }));
  }, []);

  /** A beat, snapped to the grid the toolbar says. */
  const snapBeat = useCallback((beat: number): number => {
    if (snap === "phrase" && phrases.length) {
      let best = phrases[0]!;
      for (const p of phrases) if (Math.abs(p - beat) < Math.abs(best - beat)) best = p;
      return best;
    }
    const step = snap === "beat" ? 1 : BEATS_PER_BAR;
    return Math.round(beat / step) * step;
  }, [snap, phrases]);

  return {
    doc: h.doc, setBase, apply, undo, redo, markSaved,
    canUndo: h.past.length > 0, canRedo: h.future.length > 0,
    dirty: h.doc !== h.saved,
    snap, setSnap, snapBeat, setPhrases, recording, setRecording, listView, setListView,
  };
}

export type History<D extends object = TimelineDoc> = ReturnType<typeof useHistory<D>>;

/** What the lanes need of a history -- the same for a timeline and a routine. */
export interface Edits {
  /** The document as it is now -- to name a new item before adding it. */
  readonly doc: RowsDoc | null;
  apply(change: (draft: RowsDoc) => void): void;
  snap: Snap;
  snapBeat(beat: number): number;
}
