import { useEffect, useMemo, useState } from "react";
import { apiFetch, apiUrl } from "../useEngine";
import { PHRASE_HUE, decodeWave, mmss, normalizeName, phraseFamily } from "./model";
import type { TrackLine } from "./model";
import { draftKey } from "./edit";

/**
 * Studio's track library: every track in the show folder, what will light it
 * on the night, and what needs attention -- with the selected track's details
 * beside it.
 *
 * Everything here comes from one `/api/tracks` read (a line per track, phrases
 * included) plus this browser's own recovery copies; the details panel reads
 * the selected track's waveform and asks the engine whether it can find its
 * audio, one track at a time.
 */

/** Pending "draft this track from a set" for the timeline page to pick up:
 *  the draft is made there, as an undoable edit, and saved only on Save. */
export const DRAFT_ON_OPEN = "klights.studio.draft";
/** How long a pending draft waits for its timeline to open. Longer, and it is
 *  a click from another visit that never got there -- applying it then would
 *  be a surprise. */
export const DRAFT_ON_OPEN_MS = 60_000;

export interface PendingDraft { track: string; set: string; at: number }

/** The pending draft for a track, taken (it is read once), if it is fresh. */
export function takePendingDraft(track: string): PendingDraft | null {
  try {
    const raw = sessionStorage.getItem(DRAFT_ON_OPEN);
    if (!raw) return null;
    const pending = JSON.parse(raw) as Partial<PendingDraft>;
    if (pending.track !== track) return null;
    sessionStorage.removeItem(DRAFT_ON_OPEN);
    if (!pending.set || typeof pending.at !== "number"
        || Date.now() - pending.at > DRAFT_ON_OPEN_MS) return null;
    return pending as PendingDraft;
  } catch {
    return null;
  }
}

export interface ActiveSet { id: string | null; name: string | null }

type Filter = "all" | "timeline" | "template" | "attention";
type Sort = "edited" | "title" | "artist" | "bpm";

export function hasDraft(id: string): boolean {
  try { return localStorage.getItem(draftKey("timeline", id)) != null; } catch { return false; }
}

export function gridMoved(t: TrackLine): boolean {
  return !!(t.timeline?.grid_rev && t.grid_rev && t.timeline.grid_rev !== t.grid_rev);
}

/** What needs attention on a track, worst first, in words. */
export function attention(t: TrackLine): string[] {
  const out: string[] = [];
  if (gridMoved(t)) {
    out.push("rekordbox has re-gridded this track since its timeline was drawn, so clips "
      + "may sit off the beat.");
  }
  if ((t.signatures ?? 0) === 0) {
    out.push("No CDJ signature: a CDJ playing it from a USB stick can only be matched by "
      + "title and artist. Prep it again from rekordbox to add one.");
  }
  return out;
}

/** What lights the track on the night, in a few words. A template set does
 *  not play live yet (that is F19 milestone 2): until then, a track with no
 *  timeline gets whatever the operator is running, auto mode included. The
 *  show's set is what new timelines are drafted from. */
export function nightOf(t: TrackLine): string {
  return t.has_timeline ? "Timeline" : "Operator's show";
}

export function PhraseStrip({ items, tall }: { items?: [number, number, string][]; tall?: boolean }) {
  if (!items?.length) {
    return <span className={`s-phrases none${tall ? " tall" : ""}`} title="No phrases from rekordbox" />;
  }
  return (
    <span className={`s-phrases${tall ? " tall" : ""}`}
          title={items.map(([, , l]) => l).join(" · ")}>
      {items.map(([s, e, label]) => (
        <i key={`${s}-${label}`} style={{ flex: Math.max(1, e - s),
                                          background: PHRASE_HUE[phraseFamily(label)] ?? "#475569" }} />
      ))}
    </span>
  );
}

export function TracksView({ tracks, error, set, liveTrack, selected, onSelect, ticked,
                            setTicked, onStart }: {
  tracks: TrackLine[] | null; error: string | null; set: ActiveSet;
  liveTrack: string | null;
  selected: string | null; onSelect: (id: string) => void;
  ticked: Set<string>; setTicked: (next: Set<string>) => void;
  onStart: (ids: string[]) => void;
}) {
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<Sort>("edited");
  const all = tracks ?? [];

  const counts = useMemo(() => ({
    all: all.length,
    timeline: all.filter((t) => t.has_timeline).length,
    template: all.filter((t) => !t.has_timeline).length,
    attention: all.filter((t) => attention(t).length > 0).length,
  }), [all]);
  const words = normalizeName(query).split(" ").filter(Boolean);
  const shown = all
    .filter((t) => filter === "all" ? true : filter === "timeline" ? t.has_timeline
      : filter === "template" ? !t.has_timeline : attention(t).length > 0)
    .filter((t) => {
      if (!words.length) return true;
      const hay = normalizeName(`${t.title} ${t.artist ?? ""} ${t.album ?? ""}`);
      return words.every((w) => hay.includes(w));
    })
    .sort((a, b) => sort === "title" ? a.title.localeCompare(b.title)
      : sort === "artist" ? (a.artist ?? "").localeCompare(b.artist ?? "")
        || a.title.localeCompare(b.title)
      : sort === "bpm" ? (a.bpm ?? 0) - (b.bpm ?? 0)
      : (b.edited ?? 0) - (a.edited ?? 0));
  const startable = all.filter((t) => ticked.has(t.id) && !t.has_timeline);
  const allTicked = shown.length > 0 && shown.every((t) => ticked.has(t.id));
  const FILTERS: [Filter, string][] = [
    ["all", "All"], ["timeline", "Has a timeline"], ["template", "No timeline"],
    ["attention", "Needs attention"]];

  return (
    <section className="s-page" aria-label="tracks">
      <div className="s-page-head">
        <div>
          <h1>Tracks</h1>
          <span className="muted small">
            {tracks == null ? "Loading the show folder…"
              : `${counts.all} in the show folder. ${counts.timeline} ha${counts.timeline === 1 ? "s" : "ve"} `
                + "a timeline" + (counts.template
                  ? `; the operator's show runs for the other ${counts.template} until they have one.`
                  : ".")
                + (set.id ? ` New timelines draft from ${set.name ?? set.id}.` : "")}
          </span>
        </div>
        <span className="grow" />
        <button disabled={!startable.length} onClick={() => onStart(startable.map((t) => t.id))}
                title="Give the ticked tracks that have no timeline one: drafted from a template set, or empty">
          {startable.length ? `Make timelines for ${startable.length}…` : "Make timelines for ticked…"}</button>
        <a className="s-button d-primary" href="#studio/rekordbox">Add from rekordbox</a>
      </div>

      <div className="s-toolbar">
        <span className="d-chips" role="group" aria-label="show">
          {FILTERS.map(([id, label]) => (
            <button key={id} className={`s-chip${filter === id ? " on" : ""}`}
                    aria-pressed={filter === id} onClick={() => setFilter(id)}>
              {label} <span className="s-n">{counts[id]}</span></button>))}
        </span>
        <span className="grow" />
        <input type="search" value={query} onChange={(e) => setQuery(e.target.value)}
               placeholder="Filter by title, artist or album" aria-label="filter tracks" />
        <label className="small s-inline muted">Sort{" "}
          <select value={sort} aria-label="sort tracks" onChange={(e) => setSort(e.target.value as Sort)}>
            <option value="edited">Recently edited</option>
            <option value="title">Title</option>
            <option value="artist">Artist</option>
            <option value="bpm">BPM</option>
          </select>
        </label>
      </div>

      {error && <p className="d-error" role="alert">{error}</p>}
      {tracks?.length === 0 && (
        <div className="s-empty">
          <p>No tracks in the show folder yet.</p>
          <p><a className="s-button d-primary" href="#studio/rekordbox">Add some from rekordbox</a></p>
        </div>
      )}
      {!!all.length && (
        <div className="s-table-wrap">
          <table className="s-table s-tracks">
            <thead>
              <tr>
                <th className="s-tick"><input type="checkbox" aria-label="tick every track shown"
                           checked={allTicked} disabled={!shown.length}
                           onChange={() => {
                             const next = new Set(ticked);
                             for (const t of shown) {
                               if (allTicked) next.delete(t.id); else next.add(t.id);
                             }
                             setTicked(next);
                           }} /></th>
                <th>Track</th><th>On the night</th><th>Phrases</th><th>BPM</th><th>Time</th>
                <th><span className="sr-only">Notes</span></th>
              </tr>
            </thead>
            <tbody>
              {shown.map((t) => {
                const notes = attention(t);
                const draft = hasDraft(t.id);
                return (
                  <tr key={t.id} className={t.id === selected ? "sel" : undefined}
                      aria-selected={t.id === selected} onClick={() => onSelect(t.id)}
                      onDoubleClick={() => { location.hash = `#studio/track/${t.id}`; }}>
                    <td className="s-tick" onClick={(e) => e.stopPropagation()}>
                      <input type="checkbox" aria-label={`tick ${t.title}`} checked={ticked.has(t.id)}
                             onChange={() => {
                               const next = new Set(ticked);
                               if (next.has(t.id)) next.delete(t.id); else next.add(t.id);
                               setTicked(next);
                             }} /></td>
                    <td className="s-track">
                      <button className="s-link" onClick={() => onSelect(t.id)}>
                        <b>{t.title}</b><span className="muted">{t.artist}</span></button>
                    </td>
                    <td><span className="s-pill"><i className={t.has_timeline ? "good" : ""} />
                      {nightOf(t)}</span></td>
                    <td className="s-phrase-cell"><PhraseStrip items={t.phrase_items} /></td>
                    <td className="mono">{t.bpm ? t.bpm.toFixed(t.bpm % 1 ? 2 : 0) : ""}</td>
                    <td className="mono">{mmss(t.duration_s)}</td>
                    <td className="s-flags">
                      {t.id === liveTrack && <span className="s-live" title="Playing on a deck now">LIVE</span>}
                      {notes.length > 0 && <span className="s-warn-mark" role="img"
                                                 aria-label={notes.join(" ")} title={notes.join(" ")}>!</span>}
                      {draft && <span className="s-draft-mark" role="img"
                                      aria-label="unsaved changes in this browser"
                                      title="Unsaved changes are kept in this browser" />}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {!shown.length && <p className="muted s-pad">No track matches.</p>}
        </div>
      )}
    </section>
  );
}

// -- the selected track ---------------------------------------------------------------

type AudioCheck = "checking" | "found" | "missing" | "none" | "unknown";

function useWaveform(t: TrackLine): number[] | null {
  const [heights, setHeights] = useState<number[] | null>(null);
  useEffect(() => {
    setHeights(null);
    if (!t.has_waveform) return;
    let live = true;
    apiFetch<{ doc: { preview?: string; detail?: { format: string; rate?: number; data: string } } }>(
      `/api/waveforms/${t.id}`)
      .then(({ doc }) => {
        // The 400-column overview is plenty for a panel; the scrolling detail
        // is for the timeline.
        const wave = decodeWave(doc.preview ? { preview: doc.preview } : doc);
        if (!live || !wave) return;
        const n = 150;
        const per = wave.heights.length / n;
        const out: number[] = [];
        for (let i = 0; i < n; i++) {
          let peak = 0;
          for (let j = Math.floor(i * per); j < Math.floor((i + 1) * per); j++) {
            peak = Math.max(peak, wave.heights[j] ?? 0);
          }
          out.push(peak);
        }
        setHeights(out);
      })
      .catch(() => { if (live) setHeights(null); });
    return () => { live = false; };
  }, [t.id, t.has_waveform]);
  return heights;
}

/** Whether the engine can find the track's audio on its machine: one byte of
 *  it, asked for the way the timeline's player asks. */
function useAudioCheck(t: TrackLine): AudioCheck {
  const [state, setState] = useState<AudioCheck>("checking");
  useEffect(() => {
    if (!t.has_audio) { setState("none"); return; }
    if (t.audio_here) { setState("found"); return; }
    setState("checking");
    const ctl = new AbortController();
    fetch(apiUrl(`/api/audio/${t.id}`), { headers: { Range: "bytes=0-0" }, signal: ctl.signal })
      .then((r) => setState(r.status === 200 || r.status === 206 ? "found"
        : r.status === 404 ? "missing" : "unknown"))
      .catch(() => { if (!ctl.signal.aborted) setState("unknown"); });
    return () => ctl.abort();
  }, [t.id, t.has_audio, t.audio_here]);
  return state;
}

const AUDIO_TEXT: Record<AudioCheck, string> = {
  checking: "Looking for its audio file…",
  found: "Audio file found on this machine",
  missing: "No audio file on this machine: the timeline plays silent until you open one",
  none: "A streaming track: no file for Studio to play",
  unknown: "Could not ask the engine for its audio",
};

export function TrackDetail({ t, set, sets, live }: {
  t: TrackLine; set: ActiveSet; sets: { id: string; name?: string }[]; live: boolean;
}) {
  const heights = useWaveform(t);
  const audio = useAudioCheck(t);
  const [draftSet, setDraftSet] = useState<string>(set.id ?? sets[0]?.id ?? "");
  useEffect(() => {
    if (!draftSet && (set.id || sets[0])) setDraftSet(set.id ?? sets[0]!.id);
  }, [set.id, sets, draftSet]);
  const notes = attention(t);
  const families = new Map<string, number>();
  for (const [, , label] of t.phrase_items ?? []) {
    const f = phraseFamily(label);
    families.set(f, (families.get(f) ?? 0) + 1);
  }
  const night = t.has_timeline
    ? `Its own timeline plays: ${t.timeline?.rows ?? 0} lane${t.timeline?.rows === 1 ? "" : "s"}, `
      + `${t.timeline?.items ?? 0} clips and points.`
    : "No timeline yet, so when it plays the operator's show runs, as for any unknown "
      + "track. Draft one from a template set to give it its own."
      + (t.phrases ? "" : " With no phrases, a draft follows the set's bar cycle.");
  const checks: [boolean, string][] = [
    [!!t.grid_rev, t.grid_rev ? "Beat grid from rekordbox" : "No beat grid"],
    [t.phrases > 0, t.phrases ? `${t.phrases} phrases from rekordbox` : "No phrase analysis"],
    [t.has_waveform, t.has_waveform ? "Waveform" : "No waveform"],
    [audio === "found" || audio === "checking", AUDIO_TEXT[audio]],
    [(t.signatures ?? 0) > 0, (t.signatures ?? 0) > 0
      ? "CDJ signature: a CDJ will recognise it" : "No CDJ signature"],
  ];
  if (t.has_timeline) {
    checks.push([!gridMoved(t), gridMoved(t) ? "Timeline drawn on an older grid"
      : "Timeline is on the current grid"]);
  }
  const wavePath = heights?.map((h, i) => {
    const x = i * 2 + 1;
    const y = Math.max(0.5, h * 18);
    return `M${x} ${(20 - y).toFixed(1)}v${(2 * y).toFixed(1)}`;
  }).join("") ?? "";
  const draft = hasDraft(t.id);
  const openDraft = () => {
    try {
      const pending: PendingDraft = { track: t.id, set: draftSet, at: Date.now() };
      sessionStorage.setItem(DRAFT_ON_OPEN, JSON.stringify(pending));
    }
    catch { /* the timeline opens without it */ }
    location.hash = `#studio/track/${t.id}`;
  };

  return (
    <div className="s-detail" aria-label="selected track">
      <div>
        <span className="s-kicker">{nightOf(t)}{live ? " · playing now" : ""}</span>
        <h2>{t.title}</h2>
        <span className="muted">{[t.artist, t.album].filter(Boolean).join(" · ")}</span>
      </div>
      <div className="s-wave">
        {heights ? (
          <svg viewBox="0 0 300 40" preserveAspectRatio="none" aria-label="waveform">
            <path d={wavePath} />
          </svg>
        ) : <span className="muted small">{t.has_waveform ? "" : "No waveform"}</span>}
        <PhraseStrip items={t.phrase_items} tall />
        {families.size > 0 && <span className="muted small">
          {[...families].map(([f, n]) => (n > 1 ? `${f} ×${n}` : f)).join(", ")}</span>}
      </div>
      <dl className="s-facts">
        <div><dt>BPM</dt><dd className="mono">{t.bpm ? t.bpm.toFixed(t.bpm % 1 ? 2 : 0) : "–"}</dd></div>
        <div><dt>Time</dt><dd className="mono">{mmss(t.duration_s) || "–"}</dd></div>
        <div><dt>Phrases</dt><dd className="mono">{t.phrases}</dd></div>
      </dl>
      <div className="s-note"><b>On the night</b>{night}</div>
      {notes.map((n) => <div key={n} className="s-note warn" role="note">{n}</div>)}
      {draft && <div className="s-note info" role="note">Unsaved changes are kept in this
        browser. Open the timeline to restore or discard them.</div>}
      <ul className="s-checks" aria-label="checks">
        {checks.map(([ok, text]) => (
          <li key={text} className={ok ? "ok" : "bad"}>
            <span aria-hidden="true">{ok ? "✓" : "!"}</span>{text}</li>
        ))}
      </ul>
      <div className="s-detail-actions">
        <a className="s-button d-primary s-wide" href={`#studio/track/${t.id}`}>
          {t.has_timeline ? "Open timeline" : "Make a timeline"}</a>
        {sets.length > 0 && (
          <div className="s-inline">
            <button onClick={openDraft} disabled={!draftSet}
                    title="Open the timeline with this set's draft laid on its scene lane: undoable, and saved only when you press Save">
              {t.has_timeline ? "Redraft from" : "Draft from"}</button>
            <select value={draftSet} aria-label="draft from template set"
                    onChange={(e) => setDraftSet(e.target.value)}>
              {sets.map((s) => <option key={s.id} value={s.id}>{s.name ?? s.id}</option>)}
            </select>
          </div>
        )}
      </div>
    </div>
  );
}
