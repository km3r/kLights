import { useState } from "react";
import { ApiError, apiFetch } from "../useEngine";
import type { Engine } from "./Designer";
import { draftFromTemplate, newTimeline } from "./model";
import type { PrepSummary, TemplateSetDoc, TrackDoc } from "./model";

/**
 * Starting tracks: the one dialog behind "Add to the show" (prep from
 * rekordbox, then start each track) and "Draft timelines" (start tracks that
 * are already in the show).
 *
 * Starting a track means one of three things, chosen once for the batch:
 *   draft     a timeline drafted from a template set -- the same draft the
 *             timeline editor makes, saved as the track's timeline
 *   empty     a timeline with one empty scene lane
 *   template  nothing: whichever set is active on the night plays it
 *
 * All of it is client-side: the draft is the editor's own function, and each
 * timeline is written with `timeline_save` and base_rev "" -- a new file,
 * refused if one appeared meanwhile, and validated by the engine like any
 * other save. A track that already has a timeline is left alone.
 */

export type StartAs = "draft" | "empty" | "template";

export interface StartTrack { id: string; title: string; artist?: string | null }

/** A rekordbox row to prep, with the words to show for it while it preps. */
export interface PrepPick { id: number; title: string; artist: string }

type RowState = "queued" | "working" | "done" | "skipped" | "failed";

interface ProgressRow {
  key: string;
  title: string;
  artist?: string | null;
  trackId?: string;
  state: RowState;
  text: string;
}

const OPTIONS: { id: StartAs; title: string; text: string }[] = [
  { id: "draft", title: "A timeline drafted from a template set",
    text: "One routine per rekordbox phrase on a scene lane, and the set's palettes on a "
      + "palette lane. A starting point to edit; saved as each track's timeline." },
  { id: "template", title: "Template only, no timeline",
    text: "Nothing is written but the track. Whichever set is active on the night plays it "
      + "phrase by phrase, so it follows a set switched live." },
  { id: "empty", title: "An empty timeline",
    text: "One scene lane with nothing on it, for a track to build by hand. Until it has "
      + "clips, the active set fills its gaps." },
];

/** Wait for a track the engine is still loading: prep answers before the
 *  folder has reloaded, so a fresh track is a 404 for a moment. */
async function loadTrack(id: string, tries = 20, waitMs = 400): Promise<TrackDoc> {
  for (let i = 0; ; i++) {
    try {
      return (await apiFetch<{ doc: TrackDoc }>(`/api/tracks/${id}`)).doc;
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 404) || i >= tries - 1) throw e;
      await new Promise((r) => setTimeout(r, waitMs));
    }
  }
}

async function hasTimeline(id: string): Promise<boolean> {
  try {
    await apiFetch(`/api/timelines/${id}`);
    return true;
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return false;
    throw e;
  }
}

export function StartDialog({ engine, title, prep, tracks, sets, activeSet, onClose, onDone }: {
  engine: Engine;
  title: string;
  /** rekordbox rows to prep first; the tracks they become are then started. */
  prep?: PrepPick[];
  /** Tracks already in the show, to start. */
  tracks?: StartTrack[];
  sets: { id: string; name?: string }[];
  activeSet: string | null;
  onClose: () => void;
  /** The folder changed: re-read what the page lists. */
  onDone: () => void;
}) {
  const [start, setStart] = useState<StartAs>(sets.length ? "draft" : "empty");
  const [setId, setSetId] = useState<string>(
    (activeSet && sets.some((s) => s.id === activeSet) ? activeSet : sets[0]?.id) ?? "");
  const [phase, setPhase] = useState<"choose" | "running" | "done">("choose");
  const [rows, setRows] = useState<ProgressRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const canWrite = engine.tier === "configure";
  const count = prep?.length ?? tracks?.length ?? 0;
  const setName = sets.find((s) => s.id === setId)?.name ?? setId;
  const options = OPTIONS.filter((o) => (prep ? true : o.id !== "template"))
    .filter((o) => o.id !== "draft" || sets.length > 0);

  const patch = (key: string, change: Partial<ProgressRow>) =>
    setRows((prev) => prev.map((r) => (r.key === key ? { ...r, ...change } : r)));

  const run = async () => {
    setPhase("running");
    setError(null);
    let targets: ProgressRow[] = (tracks ?? []).map((t) => ({
      key: t.id, trackId: t.id, title: t.title, artist: t.artist, state: "queued", text: "Queued" }));
    if (prep) {
      setRows(prep.map((p) => ({ key: `rb:${p.id}`, title: p.title, artist: p.artist,
                                 state: "working", text: "Reading rekordbox's analysis…" })));
      // A playlist's worth of analysis files takes seconds, not milliseconds.
      const reply = await engine.request({ type: "rekordbox_prep", ids: prep.map((p) => p.id) },
                                         600_000);
      if (!reply.ok) {
        setError(reply.error ?? "the engine refused");
        setRows((prev) => prev.map((r) => ({ ...r, state: "failed", text: "Not prepped" })));
        setPhase("done");
        return;
      }
      const summary = reply.data as PrepSummary;
      targets = summary.results.map((r) => ({
        key: r.track_id, trackId: r.track_id, title: r.title, artist: r.artist,
        state: "queued",
        text: [r.status === "created" ? "Prepped" : `Prepped (${r.status})`,
               r.signature ? "CDJ signature" : "no CDJ signature", ...r.notes].join(" · "),
      }));
      setRows([...targets, ...summary.skipped.map((s): ProgressRow => ({
        key: `skip:${s.rekordbox_id}`, title: s.title, artist: s.artist, state: "failed",
        text: s.reason }))]);
      onDone();
    } else {
      setRows(targets);
    }

    if (start === "template") {
      for (const t of targets) {
        patch(t.key, { state: "done", text: `${t.text} · the active template set plays it` });
      }
      setPhase("done");
      return;
    }

    let ts: TemplateSetDoc | null = null;
    if (start === "draft") {
      try {
        ts = (await apiFetch<{ doc: TemplateSetDoc }>(`/api/templates/${setId}`)).doc;
      } catch (e) {
        setError(`could not read the template set ${setId}: ${(e as Error).message}`);
        setPhase("done");
        return;
      }
    }
    const prefix = (t: ProgressRow) => (prep ? `${t.text} · ` : "");
    for (const t of targets) {
      const id = t.trackId!;
      patch(t.key, { state: "working", text: `${prefix(t)}${ts ? `drafting from ${setName}` : "making it a timeline"}…` });
      try {
        const track = await loadTrack(id);
        if (await hasTimeline(id)) {
          patch(t.key, { state: "skipped", text: `${prefix(t)}it has a timeline already: left as it is` });
          continue;
        }
        const doc = newTimeline(track);
        const problem = ts ? draftFromTemplate(doc, track, ts) : null;
        if (problem) {
          patch(t.key, { state: "failed", text: `${prefix(t)}${problem}` });
          continue;
        }
        const reply = await engine.request({ type: "timeline_save", doc, base_rev: "" });
        if (!reply.ok) {
          patch(t.key, { state: "failed", text: `${prefix(t)}${reply.error ?? "the engine refused"}` });
          continue;
        }
        const clips = doc.rows.find((r) => r.target === "scene")?.items?.length ?? 0;
        patch(t.key, { state: "done", text: `${prefix(t)}${ts
          ? `drafted ${clips} clip${clips === 1 ? "" : "s"} from ${setName}` : "empty timeline"}` });
      } catch (e) {
        patch(t.key, { state: "failed", text: `${prefix(t)}${(e as Error).message}` });
      }
    }
    setPhase("done");
    onDone();
  };

  const finished = rows.filter((r) => r.state !== "queued" && r.state !== "working").length;
  return (
    <div className="s-modal" role="presentation">
      <section className="s-dialog" role="dialog" aria-modal="true" aria-label={title}>
        <header className="s-dialog-head">
          <div>
            <h2>{title}</h2>
            {prep && <span className="muted small">Prepping reads each track's grid, phrases,
              waveform and cues from rekordbox, and works out its CDJ signature.</span>}
          </div>
          <span className="grow" />
          <button className="s-icon" aria-label="close" disabled={phase === "running"}
                  onClick={onClose}>×</button>
        </header>

        {phase === "choose" ? (
          <div className="s-dialog-body">
            <b>Start {count === 1 ? "it" : `each of the ${count}`} as</b>
            {options.map((o) => (
              <button key={o.id} className={`s-opt${start === o.id ? " on" : ""}`}
                      aria-pressed={start === o.id} onClick={() => setStart(o.id)}>
                <span className="s-radio" aria-hidden="true"><i /></span>
                <span><b>{o.title}</b><span className="muted small">{o.text}</span></span>
              </button>
            ))}
            {start === "draft" && (
              <label className="small s-inline">Template set{" "}
                <select value={setId} aria-label="template set"
                        onChange={(e) => setSetId(e.target.value)}>
                  {sets.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name ?? s.id}{s.id === activeSet ? " (active)" : ""}</option>))}
                </select>
              </label>
            )}
            <ul className="s-names small">
              {(prep ?? tracks ?? []).map((t) => (
                <li key={t.id}>{t.title}{t.artist && <span className="muted"> · {t.artist}</span>}</li>
              ))}
            </ul>
          </div>
        ) : (
          <div className="s-dialog-body" aria-live="polite">
            <div className="s-progress-head">
              <b>{phase === "done" ? "Done" : "Working"}</b>
              <span className="muted small">{finished} of {rows.length}</span>
              <span className="s-meter" aria-hidden="true">
                <i style={{ width: `${rows.length ? (100 * finished) / rows.length : 0}%` }} /></span>
            </div>
            {error && <p className="d-error" role="alert">{error}</p>}
            <ul className="s-progress" aria-label="progress">
              {rows.map((r) => (
                <li key={r.key} className={`s-row-${r.state}`}>
                  <span className="s-dot" aria-label={r.state} />
                  <span className="s-progress-name"><b>{r.title}</b>
                    {r.artist && <span className="muted"> · {r.artist}</span>}</span>
                  <span className="muted small s-progress-text">{r.text}</span>
                  {r.trackId && (r.state === "done" || r.state === "skipped")
                    && <a className="d-link small" href={`#studio/track/${r.trackId}`}>Open</a>}
                </li>
              ))}
            </ul>
          </div>
        )}

        <footer className="s-dialog-foot">
          <span className="muted small grow">
            {canWrite ? "Nothing leaves the engine's machine. A timeline is only written for a "
              + "track that has none."
              : "This writes the show folder: open Studio from the link the engine printed."}
          </span>
          {phase === "choose" ? (
            <>
              <button onClick={onClose}>Cancel</button>
              <button className="d-primary" disabled={!canWrite || !count
                        || (start === "draft" && !setId)}
                      onClick={() => void run()}>
                {prep ? `Add ${count} to the show` : `Start ${count}`}</button>
            </>
          ) : (
            <button className="d-primary" disabled={phase === "running"} onClick={onClose}>
              {phase === "running" ? "Working…" : "Close"}</button>
          )}
        </footer>
      </section>
    </div>
  );
}
