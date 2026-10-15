import { useEffect, useState } from "react";
import { ApiError, apiFetch } from "../useEngine";
import type { Engine } from "./Designer";
import { useHistory } from "./history";
import type { History, Snap } from "./history";
import { typing, useKeys } from "./keys";
import { clearPending, peekPending } from "./pending";

/**
 * One document open in an editor: the way back to where it was opened from,
 * the working copy kept in this browser, the engine's verdict on it, and Save.
 *
 * The engine stays the judge of what is valid: every change is sent as a draft
 * (debounced) and the answer -- errors, warnings, what will not work on this
 * rig -- is shown beside Save. Save writes with the rev the document was read
 * at, so a change made elsewhere (another machine, MCP) is refused rather than
 * overwritten. The working copy is also kept in this browser, so a crash or a
 * closed tab does not lose an evening's work.
 */
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

interface DraftCheck { errors: string[]; warnings: string[]; problems: string[] }

interface Kept<D> { doc: D; rev: string; at: number }

/** Where each kind of document is read from: `GET /api/<this>/<id>`. */
const API_PATH: Readonly<Record<DocKind, string>> = {
  timeline: "timelines", routine: "routines", template: "templates",
};

/**
 * A document as an editor opens it: read with the rev a save must quote, or --
 * where the folder has none -- started from `fresh` and saved with base_rev ""
 * (a new file, refused if one appeared meanwhile). For a routine or a template
 * set, the start New handed over (`pending.ts`) is what `fresh` is given, and
 * it is cleared once used.
 *
 * Any other failure to read it is `loadError`, never a blank document: one
 * that could not be read is not one that is not there.
 */
export function useSavedDoc<D extends object>(kind: DocKind, id: string,
                                              fresh: (handed: D | null) => D) {
  const history = useHistory<D>();
  const { setBase } = history;
  const [rev, setRev] = useState("");
  const [loadError, setLoadError] = useState<string | null>(null);
  // Read once, as the editor opens: what New left for this document, if it is
  // a routine or a set. A timeline's start is a recipe the editor applies
  // over what loaded, so it reads its own.
  const [handed] = useState<D | null>(() => {
    const p = kind === "timeline" ? null : peekPending(kind, id);
    return p ? (p.doc as unknown as D) : null;
  });
  const [start] = useState(() => fresh);

  useEffect(() => {
    let live = true;
    apiFetch<{ doc: D; rev: string }>(`/api/${API_PATH[kind]}/${id}`)
      .then((r) => {
        if (!live) return;
        setBase(r.doc);
        setRev(r.rev);
      })
      .catch((e: Error) => {
        if (!live) return;
        if (!(e instanceof ApiError && e.status === 404)) { setLoadError(e.message); return; }
        setBase(start(handed));
        setRev("");
        if (handed) clearPending(kind, id);
      });
    return () => { live = false; };
  }, [kind, id, setBase, start, handed]);

  return { history, rev, setRev, loadError };
}

export function Toolbar<D extends object>({ history, rev, setRev, engine, kind, ident }: {
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
      if (reply.ok && reply.data) setCheck(reply.data);
    }, 500);
    return () => clearTimeout(timer);
  }, [doc, request, connected, history.dirty, key, rev, kind, kept]);

  // Ctrl/Cmd+Z, Shift+Ctrl/Cmd+Z or Ctrl+Y, Ctrl/Cmd+S. Undo in a text field
  // is the field's own; Save is the page's wherever the cursor is, and the
  // browser's "save this page" never is.
  const errorCount = check?.errors.length ?? 0;
  // A file not written yet is unsaved as it stands: a start from New -- a
  // copy, a look, a draft -- is worth saving before anything is changed.
  const unsaved = !!doc && (history.dirty || rev === "");
  useKeys((e) => {
    if (!(e.metaKey || e.ctrlKey) || e.altKey) return;
    const k = e.key.toLowerCase();
    if (k === "s") {
      e.preventDefault();
      if (unsaved && !saving && errorCount === 0) void save();
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
    const reply = await engine.request(
      kind === "timeline" ? { type: "timeline_save", doc, base_rev: rev }
        : kind === "routine" ? { type: "routine_save", doc, base_rev: rev }
          : { type: "template_save", doc, base_rev: rev });
    setSaving(false);
    if (reply.ok && reply.data) {
      setRev(reply.data.rev);
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
      <button onClick={() => void save()} disabled={!unsaved || saving || errors > 0}
              className={unsaved ? "d-primary" : ""}>
        {saving ? "Saving…" : unsaved ? "Save" : "Saved"}
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
