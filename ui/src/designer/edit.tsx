import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { HelpHeading } from "../components";
import type { Reply } from "../types";
import { apiFetch } from "../useEngine";
import type { Engine } from "./Designer";
import {
  BEATS_PER_BAR, barBeat, curveValue, draftFromTemplate, itemName, itemSub, uniqueId,
} from "./model";
import type {
  Item, PaletteSummary, Point, RoutineSummary, Row, TemplateSetDoc, TimelineDoc, TrackDoc,
} from "./model";

/**
 * Editing a timeline: an undo/redo history over the whole document, and the
 * pieces of the designer that change it.
 *
 * Every edit is a pure function of the document -- copy, change, push -- so
 * undo is a pointer move and nothing can half-apply. The engine stays the
 * judge of what is valid: every change is sent as a `timeline_draft` (debounced)
 * and the answer -- errors, warnings, what will not work on this rig -- is shown
 * beside Save. Save writes with the rev the document was read at, so a change
 * made elsewhere (another machine, MCP) is refused rather than overwritten.
 * The working copy is also kept in this browser, so a crash or a closed tab
 * does not lose an evening's work.
 */

export type Snap = "beat" | "bar" | "phrase";

export const AUTOMATION_RANGES: Record<string, [number, number]> = {
  master: [0, 1], size: [0, 3], spread: [-1, 1],
  "center.bearing": [-180, 180], "center.elevation": [-90, 90],
  "rate.movement": [0, 8], "rate.color": [0, 8], "rate.level": [0, 8],
};

export const FADES = [0, 1, 2, 4, 8, 16];
export const ROLES = ["primary", "secondary", "accent"] as const;

// -- history ------------------------------------------------------------------

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
  doc: RowsDoc | null;
  apply(change: (draft: RowsDoc) => void): void;
  snap: Snap;
  snapBeat(beat: number): number;
}

export { uniqueId };

// -- going between pages ----------------------------------------------------------

const BACK_KEY = "klights.designer.back";

/** Where this browser keeps the working copy of an unsaved document. Studio's
 *  library reads it too, to flag a track with unsaved work. */
export type DocKind = "timeline" | "routine" | "template";

export function draftKey(kind: DocKind, ident: string): string {
  return kind === "timeline" ? `klights.draft.${ident}` : `klights.draft.${kind}.${ident}`;
}

/** Remember this page, so the routine editor's back link returns to it. */
export function rememberBack(): void {
  try { sessionStorage.setItem(BACK_KEY, location.hash); } catch { /* fine */ }
}

/** Where the routine editor's back link goes: the track it was opened from,
 *  else Studio's routines. */
export function backHash(): string {
  try {
    const h = sessionStorage.getItem(BACK_KEY);
    if (h && /^#studio\/track\/[a-z0-9][a-z0-9_-]*$/.test(h)) return h;
  } catch { /* fine */ }
  return "#studio/routines";
}

// -- keys ----------------------------------------------------------------------

/** Whether a key went to something being typed in, where it is the field's. */
export function typing(e: KeyboardEvent): boolean {
  const el = e.target as HTMLElement | null;
  if (!el || !el.tagName) return false;
  return el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT"
    || el.isContentEditable;
}

/** A keydown listener on the page that always runs the latest `handler`. */
export function useKeys(handler: (e: KeyboardEvent) => void): void {
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => {
    const on = (e: KeyboardEvent) => ref.current(e);
    addEventListener("keydown", on);
    return () => removeEventListener("keydown", on);
  }, []);
}

/** The designer's plain keys, the same in both editors: Space plays and
 *  stops, Delete (or Backspace) removes what is selected, Escape lets go of it. */
export function useEditorKeys({ history, selected, setSelected, playPause, clip }: {
  history: Edits; selected: string | null; setSelected: (id: string | null) => void;
  playPause: () => void;
  /** Copy, cut, paste, duplicate (Ctrl/Cmd C X V D) and split at the
   *  playhead (S): what kind of document this is, and where the playhead is. */
  clip?: { kind: ClipKind; beat: () => number };
}): void {
  useKeys((e) => {
    if (typing(e) || e.altKey) return;
    const item = selected && !parsePointId(selected) ? selected : null;
    if (clip && (e.metaKey || e.ctrlKey)) {
      const k = e.key.toLowerCase();
      const at = () => Math.max(0, history.snapBeat(clip.beat()));
      if (k === "c" && item) { e.preventDefault(); clipOps.copy(history, item, clip.kind); }
      else if (k === "x" && item) { e.preventDefault(); clipOps.cut(history, item, clip.kind); setSelected(null); }
      else if (k === "v" && clipBoard()?.kind === clip.kind) {
        e.preventDefault();
        setSelected(clipOps.paste(history, clip.kind, at()));
      } else if (k === "d" && item) {
        e.preventDefault();
        setSelected(clipOps.duplicate(history, item, clip.kind) ?? item);
      }
      return;
    }
    if (e.metaKey || e.ctrlKey) return;
    if (clip && item && (e.key === "s" || e.key === "S")) {
      e.preventDefault();
      clipOps.split(history, item, history.snapBeat(clip.beat()));
      return;
    }
    const tag = (e.target as HTMLElement | null)?.tagName;
    if (e.key === " " && tag !== "BUTTON" && tag !== "A") {
      e.preventDefault();
      playPause();
    } else if ((e.key === "Delete" || e.key === "Backspace") && selected) {
      e.preventDefault();
      const id = selected;
      history.apply((d) => removeSelected(d, id));
      setSelected(null);
    } else if (e.key === "Escape" && selected) {
      setSelected(null);
    }
  });
}

/** Remove an item, or an automation point, by its selection id. */
export function removeSelected(d: RowsDoc, id: string): void {
  const pt = parsePointId(id);
  if (pt) {
    const r = rowOf(d, pt.row);
    if (r) r.points = (r.points ?? []).filter((q) => q[0] !== pt.beat);
    return;
  }
  for (const r of d.rows) if (r.items) r.items = r.items.filter((i) => i.id !== id);
}

// -- the clipboard ------------------------------------------------------------------

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
export type Placeable =
  | { kind: "routine"; id: string }
  | { kind: "palette"; name: string; colours?: Record<"primary" | "secondary" | "accent", string> }
  | { kind: "hit"; hit: "flash" | "strobe" | "blackout" };

function rowOf(doc: RowsDoc, id: string): Row | undefined {
  return doc.rows.find((r) => r.id === id);
}

function itemOf(doc: RowsDoc, id: string): Item | undefined {
  for (const r of doc.rows) {
    const it = (r.items ?? []).find((i) => i.id === id);
    if (it) return it;
  }
  return undefined;
}

// -- the toolbar ------------------------------------------------------------------

interface DraftCheck { errors: string[]; warnings: string[]; problems: string[] }
interface Kept<D> { doc: D; rev: string; at: number }

function Toolbar<D extends object>({ history, rev, setRev, engine, kind, ident }: {
  history: History<D>; rev: string; setRev: (r: string) => void; engine: Engine;
  /** What is being edited, and which one: the commands and the recovery copy
   *  follow from it. */
  kind: DocKind; ident: string;
}) {
  const { doc } = history;
  const [check, setCheck] = useState<DraftCheck | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [showProblems, setShowProblems] = useState(false);
  const key = draftKey(kind, ident);
  // The lanes' tools mean nothing to a document without lanes.
  const lanes = kind !== "template";
  // `request` is stable; `engine` is a new object on every snapshot, and a
  // debounce keyed on it would be reset ten times a second and never fire.
  const { request } = engine;
  const connected = engine.status === "open";

  // A working copy this browser kept from last time (the tab died, the laptop
  // slept): offered once the document loads, and nothing is written over it
  // until it is restored or discarded. `undefined` = not looked yet.
  const [kept, setKept] = useState<Kept<D> | null | undefined>(undefined);
  useEffect(() => {
    if (!doc || kept !== undefined) return;
    let found: Kept<D> | null = null;
    try {
      const raw = localStorage.getItem(key);
      if (raw) found = JSON.parse(raw) as Kept<D>;
    } catch { /* unreadable: nothing to offer */ }
    setKept(found?.doc && JSON.stringify(found.doc) !== JSON.stringify(doc) ? found : null);
  }, [doc, key, kept]);

  // Ask the engine what it thinks of the working copy, a moment after typing
  // stops -- and once it is connected, so the first check is not lost to a
  // page that loaded faster than its socket. Keep the working copy in this
  // browser in case the tab dies.
  useEffect(() => {
    if (!doc) return;
    if (kept === null) {
      try {
        if (history.dirty) localStorage.setItem(key, JSON.stringify({ doc, rev, at: Date.now() }));
        else localStorage.removeItem(key);
      } catch { /* private mode: no recovery copy */ }
    }
    if (!connected) return;
    const timer = setTimeout(async () => {
      const reply = await request(kind === "timeline" ? { type: "timeline_draft", doc }
        : kind === "routine" ? { type: "routine_draft", doc } : { type: "template_draft", doc });
      if (reply.ok && reply.data) setCheck(reply.data as DraftCheck);
    }, 500);
    return () => clearTimeout(timer);
  }, [doc, request, connected, history.dirty, key, rev, kind, kept]);

  // Ctrl/Cmd+Z, Shift+Ctrl/Cmd+Z or Ctrl+Y, Ctrl/Cmd+S. Undo in a text field
  // is the field's own; Save is the page's wherever the cursor is, and the
  // browser's "save this page" never is.
  const errorCount = check?.errors.length ?? 0;
  useKeys((e) => {
    if (!(e.metaKey || e.ctrlKey) || e.altKey) return;
    const k = e.key.toLowerCase();
    if (k === "s") {
      e.preventDefault();
      if (history.dirty && !saving && errorCount === 0) void save();
      return;
    }
    if (typing(e)) return;
    if (k === "z" && !e.shiftKey) { e.preventDefault(); history.undo(); }
    else if ((k === "z" && e.shiftKey) || k === "y") { e.preventDefault(); history.redo(); }
  });

  const restore = () => {
    const copy = kept?.doc;
    setKept(null);
    if (!copy) return;
    // One undoable edit: Undo goes back to the file as saved.
    history.apply((d) => {
      const bag = d as Record<string, unknown>;
      for (const k of Object.keys(bag)) delete bag[k];
      Object.assign(d, structuredClone(copy));
    });
  };
  const discard = () => {
    try { localStorage.removeItem(key); } catch { /* fine */ }
    setKept(null);
  };

  const save = async () => {
    if (!doc) return;
    setSaving(true);
    setSaveError(null);
    const reply: Reply = await engine.request(
      kind === "timeline" ? { type: "timeline_save", doc, base_rev: rev }
        : kind === "routine" ? { type: "routine_save", doc, base_rev: rev }
          : { type: "template_save", doc, base_rev: rev });
    setSaving(false);
    if (reply.ok) {
      setRev((reply.data as { rev: string }).rev);
      history.markSaved(doc);
      try { localStorage.removeItem(key); } catch { /* fine */ }
    } else {
      setSaveError(reply.error ?? "the engine refused");
    }
  };

  const errors = check?.errors.length ?? 0;
  const problems = (check?.problems.length ?? 0) + (check?.warnings.length ?? 0);
  return (
    <span className="d-tools">
      {lanes && <label className="small muted">Snap{" "}
        <select value={history.snap} aria-label="snap"
                onChange={(e) => history.setSnap(e.target.value as Snap)}>
          <option value="beat">beat</option>
          <option value="bar">bar</option>
          <option value="phrase">phrase</option>
        </select>
      </label>}
      <button onClick={history.undo} disabled={!history.canUndo}>Undo</button>
      <button onClick={history.redo} disabled={!history.canRedo}>Redo</button>
      {lanes && <button className={history.listView ? "on" : ""}
              onClick={() => history.setListView(!history.listView)}>List</button>}
      <button className={check && errors ? "d-bad" : problems ? "d-warn" : ""}
              onClick={() => setShowProblems(!showProblems)}
              title="What the engine thinks of this draft">
        {!check ? "checking…" : errors ? `${errors} error(s)`
          : problems ? `${problems} note(s)` : "valid"}
      </button>
      <button onClick={() => void save()} disabled={!history.dirty || saving || errors > 0}
              className={history.dirty ? "d-primary" : ""}>
        {saving ? "Saving…" : history.dirty ? "Save" : "Saved"}
      </button>
      {saveError && <span className="d-error small" role="alert">{saveError}</span>}
      {kept && (
        <div className="d-pop" role="alertdialog" aria-label="unsaved changes">
          <p className="small">
            Unsaved changes from {new Date(kept.at).toLocaleString()} were kept in this browser.
            {kept.rev !== rev && " The file has been saved since (another machine, MCP or "
              + "another tab), so they cannot simply be restored over it."}
          </p>
          <div className="d-chips">
            {kept.rev === rev && <button className="d-primary" onClick={restore}>Restore them</button>}
            <a className="d-link small" download={`${ident}.unsaved.json`}
               href={`data:application/json;charset=utf-8,${encodeURIComponent(
                 JSON.stringify(kept.doc, null, 2))}`}>Download them</a>
            <button onClick={discard}>Discard</button>
          </div>
        </div>
      )}
      {showProblems && check && (
        <div className="d-pop" role="dialog" aria-label="draft check">
          {[...check.errors.map((e) => ["error", e]),
            ...check.problems.map((p) => ["rig", p]),
            ...check.warnings.map((w) => ["note", w])].map(([k, t], i) => (
              <div key={i} className={`small d-${k}`}>{k}: {t}</div>))}
          {!errors && !problems && <div className="small">Nothing to report.</div>}
        </div>
      )}
    </span>
  );
}

// -- lanes ----------------------------------------------------------------------

const LANE_H = 40;

function LaneSvg({ row, x, width, zoom, selected, onSelect, history, onMenu, onDropItem }: {
  row: Row; x: (b: number) => number; width: number; zoom: number;
  selected: string | null; onSelect: (id: string | null) => void; history: Edits;
  /** A clip's menu, asked for with the other mouse button. */
  onMenu?: (id: string, clientX: number, clientY: number) => void;
  /** Something dragged from the browser, let go at a beat on this lane. */
  onDropItem?: (what: Placeable, beat: number) => void;
}) {
  const menu = (e: React.MouseEvent, id: string) => {
    if (!onMenu) return;
    e.preventDefault();
    onSelect(id);
    onMenu(id, e.clientX, e.clientY);
  };
  // A drag is shown live from local state and committed as ONE edit on release.
  const [drag, setDrag] = useState<{ id: string; mode: "move" | "resize";
                                     start: number; at: number; len: number;
                                     dAt: number; dLen: number } | null>(null);
  const hits = row.type === "hits";
  const items = row.items ?? [];

  const begin = (e: React.PointerEvent, it: Item, mode: "move" | "resize") => {
    e.stopPropagation();
    onSelect(it.id);
    (e.target as Element).setPointerCapture?.(e.pointerId);
    setDrag({ id: it.id, mode, start: e.clientX, at: it.at, len: it.len, dAt: 0, dLen: 0 });
  };
  const move = (e: React.PointerEvent) => {
    if (!drag) return;
    const beats = (e.clientX - drag.start) / zoom;
    if (drag.mode === "move") {
      setDrag({ ...drag, dAt: history.snapBeat(drag.at + beats) - drag.at });
    } else {
      const end = history.snapBeat(drag.at + drag.len + beats);
      setDrag({ ...drag, dLen: Math.max(history.snap === "beat" ? 1 : 4, end - drag.at) - drag.len });
    }
  };
  const end = () => {
    if (!drag) return;
    const { id, dAt, dLen } = drag;
    setDrag(null);
    if (!dAt && !dLen) return;
    history.apply((d) => {
      const it = itemOf(d, id);
      if (!it) return;
      it.at = it.at + dAt;
      it.len = it.len + dLen;
      if ((it.fade ?? 0) > it.len) it.fade = it.len;
    });
  };

  return (
    <svg width={width} height={LANE_H} className="d-lane"
         aria-label={`lane ${row.id}`}
         onPointerMove={move} onPointerUp={end} onPointerCancel={end}
         onClick={(e) => {
           if (e.target === e.currentTarget) onSelect(null);
         }}
         onDragOver={(e) => {
           if (onDropItem && e.dataTransfer.types.includes(PLACE_MIME)) e.preventDefault();
         }}
         onDrop={(e) => {
           const raw = e.dataTransfer.getData(PLACE_MIME);
           if (!onDropItem || !raw) return;
           e.preventDefault();
           const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
           onDropItem(JSON.parse(raw) as Placeable, Math.max(0, (e.clientX - r.left) / zoom));
         }}>
      {items.map((it) => {
        const live = drag?.id === it.id ? drag : null;
        const at = it.at + (live?.dAt ?? 0);
        const len = it.len + (live?.dLen ?? 0);
        const sel = selected === it.id;
        if (hits) {
          const cx = x(at);
          return (
            <g key={it.id} className={`d-hit d-hit-${it.hit}${sel ? " sel" : ""}`}
               onPointerDown={(e) => begin(e, it, "move")} role="button"
               onContextMenu={(e) => menu(e, it.id)}
               aria-label={`${it.hit} at bar ${barBeat(at)}`}>
              <rect x={cx} y={LANE_H / 2 - 3} width={Math.max(2, x(len) - x(0))} height={6}
                    rx={3} className="d-hit-span" />
              <path d={`M${cx} ${LANE_H / 2 - 9} l7 9 l-7 9 l-7 -9 z`} />
            </g>
          );
        }
        const w = Math.max(3, x(len) - x(0) - 1);
        const fadeW = Math.min(w, x(it.fade ?? 0) - x(0));
        return (
          <g key={it.id} className={`d-clip d-clip-${it.kind ?? "block"}${sel ? " sel" : ""}`}
             role="button" aria-label={`${itemName(it)} at bar ${barBeat(at)}`}
             onContextMenu={(e) => menu(e, it.id)}>
            <rect x={x(at)} y={2} width={w} height={LANE_H - 4} rx={4}
                  onPointerDown={(e) => begin(e, it, "move")} />
            {fadeW > 0 && (
              <path d={`M${x(at)} ${LANE_H - 2} L${x(at) + fadeW} 2 L${x(at)} 2 z`}
                    className="d-fade" pointerEvents="none" />)}
            <text x={x(at) + 5} y={16} className="d-clip-name" pointerEvents="none">
              {itemName(it)}</text>
            <text x={x(at) + 5} y={30} className="d-clip-sub" pointerEvents="none">
              {itemSub(it)}</text>
            <rect x={x(at) + w - 6} y={2} width={6} height={LANE_H - 4}
                  className="d-resize" aria-label={`resize ${it.id}`}
                  onPointerDown={(e) => begin(e, it, "resize")} />
          </g>
        );
      })}
    </svg>
  );
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

const CURVES = ["linear", "step", "ease"] as const;

/**
 * An automation lane. Click empty space to add a point there; click a point to
 * select it (the inspector edits its value and the curve that arrives at it);
 * drag a point to move it -- across to another beat, up and down to another
 * value -- as one edit when it is let go.
 */
function AutoSvg({ row, x, width, history, selected, onSelect }: {
  row: Row; x: (b: number) => number; width: number; history: Edits;
  selected?: string | null; onSelect?: (id: string | null) => void;
}) {
  const [lo, hi] = AUTOMATION_RANGES[row.target ?? ""] ?? [0, 1];
  const points = row.points ?? [];
  const perBeat = x(1) - x(0);
  const y = (v: number) => LANE_H - 3 - ((v - lo) / (hi - lo)) * (LANE_H - 6);
  const [drag, setDrag] = useState<{ beat: number; x0: number; y0: number; v0: number | null;
                                     dBeat: number; value: number | null } | null>(null);

  // What is drawn: the points as they are, or as the drag has them.
  const shown: Point[] = drag
    ? points.map((p) => (p[0] === drag.beat
      ? [p[0] + drag.dBeat, drag.value ?? p[1], ...(p.length > 2 ? [p[2]] : [])] as Point
      : p)).sort((a, b) => a[0] - b[0])
    : points;
  const samples: string[] = [];
  if (shown.length) {
    const first = shown[0]![0];
    const last = shown[shown.length - 1]![0];
    const lead = curveValue(shown, first);
    if (lead != null) samples.push(`${x(Math.min(0, first))},${y(lead)}`);
    for (let b = first; b <= last; b += 0.5) {
      const v = curveValue(shown, b);
      if (v != null) samples.push(`${x(b)},${y(v)}`);
    }
    // After the last point the value holds, to the end of the lane.
    const tail = curveValue(shown, last);
    if (tail != null) samples.push(`${x(last)},${y(tail)}`, `${width},${y(tail)}`);
  }

  const begin = (e: React.PointerEvent, p: Point) => {
    e.stopPropagation();
    onSelect?.(pointId(row.id, p[0]));
    (e.target as Element).setPointerCapture?.(e.pointerId);
    const v0 = typeof p[1] === "number" ? p[1] : null;
    setDrag({ beat: p[0], x0: e.clientX, y0: e.clientY, v0, dBeat: 0, value: v0 });
  };
  const move = (e: React.PointerEvent) => {
    if (!drag) return;
    const beat = history.snapBeat(drag.beat + (e.clientX - drag.x0) / perBeat);
    const value = drag.v0 == null ? null : Math.round(Math.max(lo, Math.min(hi,
      drag.v0 - ((e.clientY - drag.y0) / (LANE_H - 6)) * (hi - lo))) * 100) / 100;
    setDrag({ ...drag, dBeat: beat - drag.beat, value });
  };
  const end = () => {
    if (!drag) return;
    const { beat, dBeat, value, v0 } = drag;
    setDrag(null);
    if (!dBeat && value === v0) return;
    const to = beat + dBeat;
    history.apply((d) => {
      const target = rowOf(d, row.id);
      const pt = target?.points?.find((q) => q[0] === beat);
      if (!target || !pt) return;
      const moved = [to, value ?? pt[1], ...(pt.length > 2 ? [pt[2]] : [])] as Point;
      target.points = [...(target.points ?? []).filter((q) => q[0] !== beat && q[0] !== to), moved]
        .sort((a, b) => a[0] - b[0]);
    });
    onSelect?.(pointId(row.id, to));
  };

  return (
    <svg width={width} height={LANE_H} className="d-lane d-auto-svg"
         aria-label={`automation ${row.target}`}
         onPointerMove={move} onPointerUp={end} onPointerCancel={end}
         onClick={(e) => {
           if (e.target !== e.currentTarget) return;
           const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
           const beat = history.snapBeat((e.clientX - r.left) / perBeat);
           const frac = 1 - (e.clientY - r.top - 3) / (LANE_H - 6);
           const value = Math.round((lo + Math.max(0, Math.min(1, frac)) * (hi - lo)) * 100) / 100;
           history.apply((d) => {
             const target = rowOf(d, row.id);
             if (!target) return;
             const pts = (target.points ?? []).filter((p) => p[0] !== beat);
             pts.push([beat, value]);
             pts.sort((a, b) => a[0] - b[0]);
             target.points = pts;
           });
           onSelect?.(pointId(row.id, beat));
         }}>
      <polyline points={samples.join(" ")} className="d-curve" pointerEvents="none" />
      {shown.map((p, i) => {
        const id = pointId(row.id, drag && p[0] === drag.beat + drag.dBeat ? drag.beat : p[0]);
        return (
          <circle key={i} cx={x(p[0])} cy={typeof p[1] === "number" ? y(p[1]) : LANE_H / 2}
                  r={selected === id ? 6 : 4}
                  className={`d-point${selected === id ? " sel" : ""}`} role="button"
                  aria-label={`point at bar ${barBeat(p[0])}: ${String(p[1])}`}
                  onPointerDown={(e) => begin(e, p)}
                  onClick={(e) => e.stopPropagation()} />
        );
      })}
    </svg>
  );
}

/** The selected automation point: its place, its value, and the curve that
 *  arrives at it (it shapes the segment from the point before). */
function PointInspector({ row, beat, history, onSelect }: {
  row: Row; beat: number; history: Edits; onSelect: (id: string | null) => void;
}) {
  const pt = row.points?.find((p) => p[0] === beat);
  if (!pt) return null;
  const [lo, hi] = AUTOMATION_RANGES[row.target ?? ""] ?? [0, 1];
  const curve = (pt[2] as string | undefined) ?? "linear";
  const set = (to: Point) => {
    history.apply((d) => {
      const target = rowOf(d, row.id);
      if (!target) return;
      target.points = [...(target.points ?? []).filter((q) => q[0] !== beat && q[0] !== to[0]), to]
        .sort((a, b) => a[0] - b[0]);
    });
    onSelect(pointId(row.id, to[0]));
  };
  const withCurve = (b: number, v: number | string, c: string): Point =>
    (c === "linear" ? [b, v] : [b, v, c]);
  return (
    <footer className="d-inspector" aria-label="inspector">
      <div className="d-insp-head">
        <b>{row.target === "master" ? "Master" : row.target}</b>
        <span className="muted"> · automation point on {row.id} · bar {barBeat(beat)}</span>
        <span className="grow" />
        <button onClick={() => {
          history.apply((d) => {
            const target = rowOf(d, row.id);
            if (target) target.points = (target.points ?? []).filter((q) => q[0] !== beat);
          });
          onSelect(null);
        }}>Delete</button>
      </div>
      <div className="d-insp-grid">
        <label className="small">Beat{" "}
          <input type="number" step={1} value={beat} aria-label="point beat" style={{ width: 70 }}
                 onChange={(e) => {
                   if (e.target.value === "") return;
                   set(withCurve(Number(e.target.value), pt[1], curve));
                 }} />
        </label>
        {typeof pt[1] === "number" ? (
          <label className="small">Value{" "}
            <input type="number" min={lo} max={hi} step={(hi - lo) / 100} value={pt[1]}
                   aria-label="point value" style={{ width: 70 }}
                   onChange={(e) => {
                     const v = Number(e.target.value);
                     if (e.target.value !== "" && v >= lo && v <= hi) set(withCurve(beat, v, curve));
                   }} />
            <span className="muted"> {lo} to {hi}</span>
          </label>
        ) : (
          <label className="small">Value{" "}
            <input value={String(pt[1])} aria-label="point value"
                   onChange={(e) => set(withCurve(beat, e.target.value, curve))} />
          </label>
        )}
        <div>
          <span className="small muted">Arrives by</span>
          <div className="d-chips">
            {CURVES.map((c) => (
              <button key={c} className={curve === c ? "on" : ""}
                      title={c === "step" ? "holds the value before until this point"
                        : c === "ease" ? "eases out of the point before and into this one"
                          : "a straight line from the point before"}
                      onClick={() => set(withCurve(beat, pt[1], c))}>{c}</button>))}
          </div>
        </div>
      </div>
    </footer>
  );
}

function GapToggle({ row, history }: { row: Row; history: Edits }) {
  const owns = row.gap === "exclusive";
  return (
    <button className={`small d-gap${owns ? " on" : ""}`}
            title={owns ? "This lane owns the track: in its gaps nothing drives it"
              : "Template fills gaps: the lanes below and the template show through"}
            onClick={() => history.apply((d) => {
              const r = rowOf(d, row.id);
              if (r) r.gap = owns ? "fill" : "exclusive";
            })}>
      {owns ? "owns track" : "fills gaps"}
    </button>
  );
}

function LaneMenu({ row, index, history }: { row: Row; index: number; history: Edits }) {
  const move = (delta: number) => history.apply((d) => {
    const i = d.rows.findIndex((r) => r.id === row.id);
    const j = i + delta;
    if (i < 0 || j < 0 || j >= d.rows.length) return;
    const [r] = d.rows.splice(i, 1);
    d.rows.splice(j, 0, r!);
  });
  return (
    <span className="d-lane-menu">
      {index >= 0 && <>
        <button className="small" aria-label={`move ${row.id} up`} onClick={() => move(-1)}>↑</button>
        <button className="small" aria-label={`move ${row.id} down`} onClick={() => move(1)}>↓</button>
      </>}
      <button className="small" aria-label={`remove lane ${row.id}`}
              onClick={() => history.apply((d) => { d.rows = d.rows.filter((r) => r.id !== row.id); })}>
        ×</button>
    </span>
  );
}

const NEW_LANES: [string, string][] = [
  ["scene", "Scene"], ["movement", "Movement"], ["color", "Colour"], ["level", "Level"],
  ["palette", "Palette"], ["hits", "Hits"],
];

/** A new automation lane for `target`, starting at its neutral value. */
export function automationRow(d: RowsDoc, target: string): Row {
  const [lo, hi] = AUTOMATION_RANGES[target] ?? [0, 1];
  const neutral = target === "master" || target === "size" || target.startsWith("rate.") ? 1 : 0;
  return { id: uniqueId(d, target), type: "automation", target,
           points: [[0, Math.min(hi, Math.max(lo, neutral))]] };
}

function AddLane({ history }: { history: History }) {
  const doc = history.doc;
  if (!doc) return null;
  const automated = new Set(doc.rows.filter((r) => r.type === "automation").map((r) => r.target));
  return (
    <div className="d-row d-add">
      <div className="d-head">
        <select aria-label="add lane" value=""
                onChange={(e) => {
                  const target = e.target.value;
                  if (!target) return;
                  history.apply((d) => {
                    const id = uniqueId(d, target);
                    d.rows.push(target === "hits"
                      ? { id, type: "hits", items: [] }
                      : { id, type: "clips", target, gap: target === "palette" ? "exclusive" : "fill",
                          items: [] });
                  });
                }}>
          <option value="">+ lane</option>
          {NEW_LANES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <select aria-label="add automation" value=""
                onChange={(e) => {
                  const target = e.target.value;
                  if (!target) return;
                  history.apply((d) => { d.rows.push(automationRow(d, target)); });
                }}>
          <option value="">+ automation</option>
          {Object.keys(AUTOMATION_RANGES).filter((t) => !automated.has(t)).map((t) => (
            <option key={t} value={t}>{t}</option>))}
        </select>
      </div>
    </div>
  );
}

// -- the shelf ------------------------------------------------------------------

function Shelf({ history, routines, beat, track }: {
  history: History; routines: RoutineSummary[]; beat: number; track?: TrackDoc | null;
}) {
  const doc = history.doc;
  if (!doc) return null;
  // The routines to place are in the browser, on the left; this keeps what
  // works on the whole track.
  void routines;
  return (
    <section>
      <Templates history={history} track={track ?? null} />
      <RecordPads history={history} beat={beat} />
      <Palettes history={history} />
    </section>
  );
}

/** Draft from template: a template set's phrase -> routine mapping, laid onto
 *  the scene lane from rekordbox's phrases, as an editable starting point. */
function Templates({ history, track }: { history: History; track: TrackDoc | null }) {
  const [sets, setSets] = useState<{ id: string; name?: string }[]>([]);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    apiFetch<{ templates: { id: string; name?: string }[] }>("/api/templates")
      .then((r) => setSets(r.templates)).catch(() => setSets([]));
  }, []);
  if (!sets.length) return null;
  const draft = async (id: string) => {
    setError(null);
    try {
      const { doc: ts } = await apiFetch<{ doc: TemplateSetDoc }>(`/api/templates/${id}`);
      if (!track) return;
      // Checked on a copy first: an edit that only says "no phrases" would
      // still be a step on the undo stack.
      const problem = draftFromTemplate(structuredClone(history.doc!), track, ts);
      if (problem) { setError(problem); return; }
      history.apply((d) => { draftFromTemplate(d, track, ts); });
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <div className="d-templates">
      <HelpHeading topic="Draft from template" help={<>
        <p>Fills the scene lane with one routine per rekordbox phrase. It looks
          for the exact label (Verse 2), then the family (Verse), then{" "}
          <code>*</code>. If the set has palettes, it fills the palette lane
          too.</p>
        <p>It replaces what's there, but it's only a starting point. Undo puts the
          lanes back.</p>
      </>}>Draft from template</HelpHeading>
      {sets.map((s) => (
        <button key={s.id} onClick={() => void draft(s.id)}
                title="Replace the scene lane with this template's routines, phrase by phrase">
          {s.name ?? s.id}</button>))}
      {error && <p className="d-error small">{error}</p>}
    </div>
  );
}

/** Record pads: tap along while the track plays, and each tap lands as an
 *  item at the snapped playhead. */
function RecordPads({ history, beat }: { history: History; beat: number }) {
  const add = (hit: "flash" | "strobe" | "blackout") => history.apply((d) => {
    let lane = d.rows.find((r) => r.type === "hits");
    if (!lane) {
      lane = { id: uniqueId(d, "hits"), type: "hits", items: [] };
      d.rows.push(lane);
    }
    const at = Math.max(0, Math.round(beat));
    (lane.items ??= []).push({ id: uniqueId(d, `${hit}-${at}`), hit, at,
                               len: hit === "strobe" ? 4 : hit === "flash" ? 2 : 1,
                               ...(hit === "flash" ? { envelope: "decay" as const } : {}) });
  });
  const nextScene = () => history.apply((d) => {
    const lane = d.rows.find((r) => r.type === "clips" && r.target === "scene");
    const at = history.snapBeat(beat);
    const it = lane?.items?.find((i) => i.at < at && at < i.at + i.len);
    if (!lane || !it) return;
    const tail: Item = { ...structuredClone(it), id: uniqueId(d, it.id), at,
                         len: it.at + it.len - at };
    it.len = at - it.at;
    lane.items!.push(tail);
  });
  return (
    <div className="d-record">
      <HelpHeading topic="Record" help={<>
        <p>Arm it and play the track. Each pad lands at the playhead:{" "}
          <b>Flash</b>, <b>Strobe</b> and <b>Blackout</b> as hits, and{" "}
          <b>Next scene</b> splits the scene clip there.</p>
      </>}>
        <button className={history.recording ? "on" : ""}
                onClick={() => history.setRecording(!history.recording)}>
          {history.recording ? "● Recording" : "Record"}</button>
      </HelpHeading>
      {history.recording && (
        <div className="d-pads">
          <button onClick={() => add("flash")}>Flash</button>
          <button onClick={() => add("strobe")}>Strobe</button>
          <button onClick={() => add("blackout")}>Blackout</button>
          <button onClick={nextScene}>Next scene</button>
        </div>
      )}
    </div>
  );
}

/** Where a document's palette came from, said beside it: a copy of the
 *  library's (and whether it still matches), or one that lives only here.
 *  Editing it here never changes the library -- this says so, in place. */
export function PaletteOrigin({ library, name, colours, here, onUseLibrary }: {
  library: PaletteSummary[]; name: string; colours: Record<string, unknown>;
  /** "track" or "set": whose copy this is. */
  here: string;
  onUseLibrary: (colours: { primary: string; secondary: string; accent: string }) => void;
}) {
  const lib = library.find((p) => p.name === name);
  if (!lib) {
    return <span className="d-origin" title={`Made in this ${here}: no library palette is called ${name}`}>
      only here</span>;
  }
  const same = ROLES.every((r) => typeof colours[r] === "string"
    && (colours[r] as string).toLowerCase() === lib[r].toLowerCase());
  const tip = `A copy of the library's ${name}. Changing it here changes this ${here} only; `
    + "the library's is changed on Studio's Palettes page.";
  return same ? <span className="d-origin lib" title={tip}>copy of library</span> : (
    <span className="d-origin differs" title={tip}>differs from library
      <button className="small" aria-label={`use the library's colours for ${name}`}
              onClick={() => onUseLibrary({ primary: lib.primary, secondary: lib.secondary,
                                            accent: lib.accent })}>use library's</button>
    </span>
  );
}

function Palettes({ history }: { history: History }) {
  const doc = history.doc;
  // The show's library, to copy a palette in from.
  const [library, setLibrary] = useState<PaletteSummary[]>([]);
  useEffect(() => {
    apiFetch<{ palettes: PaletteSummary[] }>("/api/palettes")
      .then((r) => setLibrary(r.palettes)).catch(() => setLibrary([]));
  }, []);
  if (!doc) return null;
  const palettes = doc.palettes ?? {};
  const hex = (v: unknown) => (typeof v === "string" && v.startsWith("#") ? v : "#ffffff");
  return (
    <div className="d-palettes">
      <HelpHeading topic="Palettes" help={<>
        <p>Each palette has three colours: primary, secondary and accent.
          Routines use these roles instead of fixed colours, so they change with
          the palette.</p>
        <p>The selected palette plays wherever the palette lane is empty. A
          palette clip switches it for its length.</p>
        <p>These are this track's own copies. Changing a colour here changes
          this track only; the library's palettes are on Studio's Palettes
          page, which can bring copies up to date.</p>
      </>}>This track's palettes</HelpHeading>
      <p className="small muted">Changing a colour here changes this track only.{" "}
        <a className="d-link" href="#studio/palettes">The library</a></p>
      {Object.entries(palettes).map(([name, pal]) => (
        <div key={name} className="d-palette">
          <label className="small">
            <input type="radio" name="default-palette" checked={doc.palette === name}
                   onChange={() => history.apply((d) => { d.palette = name; })} />
            {name}
          </label>
          <PaletteOrigin library={library} name={name} colours={pal as Record<string, unknown>}
                         here="track"
                         onUseLibrary={(c) => history.apply((d) => { d.palettes![name] = c; })} />
          {ROLES.map((role) => (
            <input key={role} type="color" aria-label={`${name} ${role}`} value={hex(pal[role])}
                   onChange={(e) => {
                     const value = e.target.value;
                     history.apply((d) => { d.palettes![name]![role] = value; });
                   }} />
          ))}
        </div>
      ))}
      <FromLibrary library={library} has={Object.keys(palettes)}
                   onPick={(name, colours) => history.apply((d) => {
                     d.palettes = { ...(d.palettes ?? {}), [name]: colours };
                     if (!d.palette) d.palette = name;
                   })} />
      <button className="small" onClick={() => history.apply((d) => {
        const name = `Palette ${Object.keys(d.palettes ?? {}).length + 1}`;
        d.palettes = { ...(d.palettes ?? {}),
                       [name]: { primary: "#ffffff", secondary: "#888888", accent: "#ff0000" } };
        if (!d.palette) d.palette = name;
      })}>+ palette</button>
    </div>
  );
}

/** A select of library palettes, for an editor that keeps its own copies: the
 *  picked one is copied in under its library name. */
export function FromLibrary({ library, has, onPick }: {
  library: PaletteSummary[]; has: string[];
  onPick: (name: string, colours: { primary: string; secondary: string; accent: string }) => void;
}) {
  const offer = library.filter((p) => !has.includes(p.name));
  if (!library.length) return null;
  return (
    <select value="" aria-label="add a palette from the library" disabled={!offer.length}
            onChange={(e) => {
              const p = library.find((x) => x.id === e.target.value);
              if (p) onPick(p.name, { primary: p.primary, secondary: p.secondary, accent: p.accent });
            }}>
      <option value="">{offer.length ? "+ From the library…" : "Every library palette is here"}</option>
      {offer.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
    </select>
  );
}

// -- the inspector ----------------------------------------------------------------

function Inspector({ history, item, routines, engine, onDeleted, beat, onSelect }: {
  history: History; item: { row: Row; item: Item } | null; routines: RoutineSummary[];
  engine: Engine; onDeleted: () => void;
  /** The playhead, for Split, and how to select what an operation made. */
  beat?: number; onSelect?: (id: string | null) => void;
}) {
  if (history.listView) return <EventList history={history} />;
  if (!item) {
    return (
      <footer className="d-inspector muted small">
        Select a clip, hit or point to edit it. Drag a clip to move it; drag its
        right edge to change its length. Positions snap to the {history.snap}.
      </footer>
    );
  }
  const { row, item: it } = item;
  const set = (fields: Partial<Item>) => history.apply((d) => {
    const target = itemOf(d, it.id);
    if (target) Object.assign(target, fields);
  });
  const routine = it.kind === "routine" ? routines.find((r) => r.id === it.routine) : undefined;
  const rigBound = it.kind === "look" || it.kind === "snapshot" || !!routine?.rig;
  const looks = engine.state?.looks.map((l) => l.name) ?? [];
  const groups = engine.state?.groups ?? [];
  const palettes = Object.keys(history.doc?.palettes ?? {});

  return (
    <footer className="d-inspector" aria-label="inspector">
      <div className="d-insp-head">
        <b>{itemName(it)}</b>
        <span className="muted"> · {it.hit ? "hit" : it.kind ?? "item"} on {row.id}</span>
        {rigBound && <span className="d-badge">this rig only</span>}
        <span className="muted mono"> · bar {barBeat(it.at)} → {barBeat(it.at + it.len)}
          {" "}({it.len} beats)</span>
        <span className="grow" />
        <span className="d-clip-tools" role="group" aria-label="clip">
          <button title="Ctrl+C" onClick={() => clipOps.copy(history, it.id, "timeline")}>Copy</button>
          <button title="Ctrl+X" onClick={() => { clipOps.cut(history, it.id, "timeline"); onDeleted(); }}>
            Cut</button>
          <button title="Ctrl+D: a copy straight after it"
                  onClick={() => onSelect?.(clipOps.duplicate(history, it.id, "timeline"))}>
            Duplicate</button>
          <button title="S: in two, at the playhead"
                  disabled={beat == null || !clipOps.inside(history.doc, it.id, history.snapBeat(beat))}
                  onClick={() => onSelect?.(clipOps.split(history, it.id, history.snapBeat(beat ?? 0)))}>
            Split</button>
        </span>
        <button onClick={() => {
          history.apply((d) => {
            for (const r of d.rows) if (r.items) r.items = r.items.filter((i) => i.id !== it.id);
          });
          onDeleted();
        }}>Delete</button>
      </div>
      <div className="d-insp-grid">
        {!it.hit && (
          <div>
            <span className="small muted">Fade in</span>
            <div className="d-chips">
              {FADES.filter((f) => f <= it.len).map((f) => (
                <button key={f} className={(it.fade ?? 0) === f ? "on" : ""}
                        onClick={() => set({ fade: f })}>{f ? `${f}` : "cut"}</button>))}
            </div>
          </div>
        )}
        {it.kind === "routine" && (
          <>
            <label className="small">Routine{" "}
              <select value={it.routine} aria-label="routine"
                      onChange={(e) => set({ routine: e.target.value, variation: undefined,
                                             params: {} })}>
                {routines.map((r) => <option key={r.id} value={r.id}>{r.name ?? r.id}</option>)}
              </select>
            </label>
            {routine && routine.variations.length > 0 && (
              <div>
                <span className="small muted">Variation</span>
                <div className="d-chips">
                  <button className={!it.variation ? "on" : ""}
                          onClick={() => set({ variation: undefined })}>default</button>
                  {routine.variations.map((v) => (
                    <button key={v} className={it.variation === v ? "on" : ""}
                            onClick={() => set({ variation: v })}>{v}</button>))}
                </div>
              </div>
            )}
            {routine && Object.entries(routine.params).map(([name, p]) => (
              <Param key={name} name={name} param={p} value={it.params?.[name]}
                     onChange={(v) => {
                       const params = { ...(it.params ?? {}) };
                       if (v === undefined) delete params[name]; else params[name] = v;
                       set({ params });
                     }} />
            ))}
            {routine && (
              <a className="small d-link" href={`#studio/routine/${routine.id}`}
                 onClick={() => rememberBack()}>
                Open routine · {routine.bars} bars{routine.loop ? ", loops" : ""}</a>)}
          </>
        )}
        {it.kind === "look" && (
          <label className="small">Look{" "}
            <input list="d-looks" value={it.look ?? ""} aria-label="look"
                   onChange={(e) => set({ look: e.target.value })} />
            <datalist id="d-looks">{looks.map((l) => <option key={l} value={l} />)}</datalist>
          </label>
        )}
        {it.kind === "snapshot" && (
          <label className="small">Preset{" "}
            <select value={it.preset ?? ""} aria-label="preset"
                    onChange={(e) => set({ preset: e.target.value })}>
              <option value="">(none)</option>
              {engine.state?.presets.map((p) => <option key={p.name} value={p.name}>{p.name}</option>)}
            </select>
          </label>
        )}
        {it.kind === "palette" && (
          <label className="small">Palette{" "}
            <select value={it.palette ?? ""} aria-label="palette"
                    onChange={(e) => set({ palette: e.target.value })}>
              {palettes.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>
        )}
        {it.hit && (
          <>
            <div className="d-chips">
              {(["flash", "strobe", "blackout"] as const).map((h) => (
                <button key={h} className={it.hit === h ? "on" : ""}
                        onClick={() => set({ hit: h })}>{h}</button>))}
            </div>
            <label className="small">Level{" "}
              <input type="range" min={0} max={1} step={0.05} value={it.level ?? 1}
                     aria-label="hit level"
                     onChange={(e) => set({ level: Number(e.target.value) })} /></label>
            <label className="small">Who{" "}
              <select value={it.role ?? ""} aria-label="hit role"
                      onChange={(e) => set({ role: e.target.value || undefined })}>
                <option value="">everything</option>
                {groups.map((g) => <option key={g} value={g}>{g}</option>)}
              </select>
            </label>
            {it.hit === "flash" && (
              <button className={it.envelope === "decay" ? "on" : ""}
                      onClick={() => set({ envelope: it.envelope === "decay" ? "hold" : "decay" })}>
                {it.envelope === "decay" ? "decays" : "holds"}</button>)}
          </>
        )}
      </div>
    </footer>
  );
}

function Param({ name, param, value, onChange }: {
  name: string; param: { type: string; default?: unknown; min?: number; max?: number; unit?: string };
  value: unknown; onChange: (v: unknown) => void;
}) {
  const shown = value ?? param.default;
  if (param.type === "color") {
    const role = typeof shown === "string" && shown.startsWith("@") ? shown.slice(1) : null;
    return (
      <div>
        <span className="small muted">${name}: open parameter</span>
        <div className="d-chips">
          {ROLES.map((r) => (
            <button key={r} className={role === r ? "on" : ""}
                    onClick={() => onChange(`@${r}`)}>{r}</button>))}
          <input type="color" aria-label={`${name} direct colour`}
                 value={typeof shown === "string" && shown.startsWith("#") ? shown : "#ffffff"}
                 onChange={(e) => onChange(e.target.value)} />
          {value !== undefined && <button onClick={() => onChange(undefined)}>default</button>}
        </div>
      </div>
    );
  }
  if (param.type === "number" || param.type === "rate") {
    const lo = param.min ?? 0;
    const hi = param.max ?? (param.type === "rate" ? 8 : 100);
    const num = typeof shown === "number" ? shown : lo;
    return (
      <label className="small">${name} · {num}{param.unit === "deg" ? "°" : ""}{" "}
        <input type="range" min={lo} max={hi} step={param.type === "rate" ? 0.25 : 1}
               value={num} aria-label={`${name}`}
               onChange={(e) => onChange(Number(e.target.value))} />
      </label>
    );
  }
  return (
    <label className="small">${name}{" "}
      <input value={typeof shown === "string" ? shown : ""} aria-label={name}
             onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

/** The same document as a list: every item by position, with nudges. */
function EventList({ history }: { history: Edits & { doc: RowsDoc | null } }) {
  const doc = history.doc;
  if (!doc) return null;
  const rows = doc.rows.flatMap((r) => (r.items ?? []).map((it) => ({ r, it })))
    .sort((a, b) => a.it.at - b.it.at || a.r.id.localeCompare(b.r.id));
  const nudge = (id: string, beats: number) => history.apply((d) => {
    const it = itemOf(d, id);
    if (it) it.at = Math.max(-64, it.at + beats);
  });
  return (
    <footer className="d-inspector d-list" aria-label="event list">
      <table>
        <thead><tr><th>bar</th><th>lane</th><th>what</th><th>len</th><th>fade</th><th /></tr></thead>
        <tbody>
          {rows.map(({ r, it }) => (
            <tr key={it.id}>
              <td className="mono">{barBeat(it.at)}</td>
              <td>{r.id}</td>
              <td>{itemName(it)} <span className="muted small">{itemSub(it)}</span></td>
              <td className="mono">{it.len}</td>
              <td className="mono">{it.fade ?? 0}</td>
              <td>
                <button className="small" aria-label={`${it.id} back a bar`}
                        onClick={() => nudge(it.id, -BEATS_PER_BAR)}>−bar</button>
                <button className="small" aria-label={`${it.id} back a beat`}
                        onClick={() => nudge(it.id, -1)}>−1</button>
                <button className="small" aria-label={`${it.id} on a beat`}
                        onClick={() => nudge(it.id, 1)}>+1</button>
                <button className="small" aria-label={`${it.id} on a bar`}
                        onClick={() => nudge(it.id, BEATS_PER_BAR)}>+bar</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </footer>
  );
}

export const Editor = {
  Toolbar, LaneSvg, AutoSvg, GapToggle, LaneMenu, AddLane, Shelf, Inspector, EventList, Param,
  PointInspector,
};
