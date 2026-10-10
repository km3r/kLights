import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { apiFetch, apiUrl } from "../useEngine";
import type { Engine } from "./Designer";
import { PHRASE_HUE, decodeWave, mmss, normalizeName, phraseFamily } from "./model";
import type { TrackLine } from "./model";
import { draftKey } from "./edit";
import { putPending } from "./pending";
import { Badge, DetailHead, DetailSection, MoreMenu, Task, useWrite } from "./detail";
import type { MenuItem } from "./detail";

/**
 * Studio's track library: every track in the show folder, what will light it
 * on the night, and what needs attention -- with the selected track's details
 * beside it.
 *
 * Everything here comes from one `/api/tracks` read (a line per track, phrases
 * included) plus this browser's own recovery copies; the details panel reads
 * the selected track's waveform and asks the engine whether it can find its
 * audio, one track at a time. Its menu is what can be done to the track's
 * files: another description for it to answer to (`track_link`), its timeline
 * deleted (`timeline_delete`), the track taken out of the show
 * (`track_delete`) -- each after saying exactly what it will do.
 */

export interface ActiveSet { id: string | null; name: string | null }

type Filter = "all" | "timeline" | "template" | "attention";
type Sort = "edited" | "title" | "artist" | "bpm";

export function hasDraft(id: string): boolean {
  try { return localStorage.getItem(draftKey("timeline", id)) != null; } catch { return false; }
}

export function gridMoved(t: TrackLine): boolean {
  return !!(t.timeline?.grid_rev && t.grid_rev && t.timeline.grid_rev !== t.grid_rev);
}

/** The two problems that can make a track go wrong on the night, said the
 *  same way in the table's "!" and the details panel's checks. */
const GRID_MOVED = { text: "Timeline drawn on an older grid",
                     why: "rekordbox has re-gridded the track since, so clips may sit off the beat." };
const NO_SIGNATURE = { text: "No CDJ signature",
                       why: "A CDJ playing it from a USB stick can only be matched by title and "
                         + "artist. Prep it again from rekordbox to add one." };

/** What needs attention on a track, worst first, in words: the table's "!"
 *  and its Needs attention filter. The details panel's checks (trackChecks)
 *  say these and everything else a track lacks, which is not all urgent. */
export function attention(t: TrackLine): string[] {
  return [gridMoved(t) ? GRID_MOVED : null, (t.signatures ?? 0) === 0 ? NO_SIGNATURE : null]
    .filter((c) => c != null).map((c) => `${c.text}: ${c.why}`);
}

/** What lights the track on the night, in a few words, with Follow armed: its
 *  timeline; else the template set that is playing, phrase by phrase; else,
 *  with no set on, whatever the operator is running, auto mode included. */
export function nightOf(t: TrackLine, playing: ActiveSet): string {
  if (t.has_timeline) return "Timeline";
  return playing.id ? `Template: ${playing.name ?? playing.id}` : "Operator's show";
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

export function TracksView({ tracks, error, set, playing, liveTrack, selected, onSelect, ticked,
                            setTicked, onStart, problems }: {
  tracks: TrackLine[] | null; error: string | null;
  /** What is wrong in the show folder, over the list (Problems.tsx). */
  problems?: ReactNode;
  /** show.json's set, which new timelines draft from; and the set playing
   *  tracks with no timeline now (the operator can switch it, or turn it off). */
  set: ActiveSet; playing: ActiveSet;
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
                  ? (playing.id
                    ? `; the ${playing.name ?? playing.id} template set plays the other `
                      + `${counts.template} until they have one.`
                    : `; with no template set on, the operator's show runs for the other `
                      + `${counts.template}.`)
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
      {problems}

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
                      {nightOf(t, playing)}</span></td>
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

/** One thing a track needs before the night: whether it has it, and if not,
 *  why that matters. `ok` null is not known yet. */
interface Check { ok: boolean | null; text: string; why?: string }

function audioCheck(audio: AudioCheck): Check {
  switch (audio) {
    case "found": return { ok: true, text: "Audio file" };
    case "checking": return { ok: null, text: "Looking for its audio file…" };
    case "missing": return { ok: false, text: "No audio file on this machine",
                             why: "The timeline plays silent until you open one." };
    case "none": return { ok: false, text: "A streaming track", why: "No file for Studio to play." };
    default: return { ok: false, text: "Could not ask the engine for its audio" };
  }
}

/** Everything a track needs, problems and all: the details panel's checklist.
 *  The table's "!" flags only the urgent two (attention). */
export function trackChecks(t: TrackLine, audio: AudioCheck, draft = false): Check[] {
  const checks: Check[] = [
    t.grid_rev ? { ok: true, text: "Beat grid" } : { ok: false, text: "No beat grid" },
    t.phrases > 0 ? { ok: true, text: "Phrases" }
      : { ok: false, text: "No phrase analysis",
          why: "A draft from a template set follows its bar cycle instead." },
    { ok: t.has_waveform, text: t.has_waveform ? "Waveform" : "No waveform" },
    audioCheck(audio),
    (t.signatures ?? 0) > 0 ? { ok: true, text: "CDJ signature" } : { ok: false, ...NO_SIGNATURE },
  ];
  if (t.has_timeline) {
    checks.push(gridMoved(t) ? { ok: false, ...GRID_MOVED }
      : { ok: true, text: "Timeline on the current grid" });
  }
  if (draft) {
    checks.unshift({ ok: false, text: "Unsaved changes in this browser",
                     why: "Open the timeline to restore or discard them." });
  }
  return checks;
}

type TrackTask = "link" | "timeline" | "remove";

/** The longest title, artist or album a link takes (the engine's LINK_TEXT_MAX). */
const LINK_TEXT_MAX = 200;

/** A description as one line: title, then artist and album where it has them. */
function describe(d: { title?: string | null; artist?: string | null; album?: string | null }): string {
  return [d.title, d.artist, d.album].filter(Boolean).join(" · ");
}

export function TrackDetail({ engine, t, set, playing, sets, live, onDone }: {
  engine: Engine; t: TrackLine; set: ActiveSet; playing: ActiveSet;
  sets: { id: string; name?: string | null }[]; live: boolean;
  /** Something was written, and what: re-read the folder, and say so where
   *  it survives this panel (removing the track takes it away). */
  onDone: (said: string) => void;
}) {
  const heights = useWaveform(t);
  const audio = useAudioCheck(t);
  const canWrite = engine.tier === "configure";
  const [draftSet, setDraftSet] = useState<string>(set.id ?? sets[0]?.id ?? "");
  useEffect(() => {
    if (!draftSet && (set.id || sets[0])) setDraftSet(set.id ?? sets[0]!.id);
  }, [set.id, sets, draftSet]);
  const [task, setTask] = useState<TrackTask | null>(null);
  const [alias, setAlias] = useState({ title: "", artist: "", album: "" });
  const { busy, error, setError, run: write, ask } = useWrite(engine, onDone);
  const field = useRef<HTMLInputElement | null>(null);
  useEffect(() => { if (task === "link") field.current?.focus(); }, [task]);
  const run = (work: () => Promise<string>) => write(work, () => setTask(null));
  const close = () => { setTask(null); setError(null); };
  const families = new Map<string, number>();
  for (const [, , label] of t.phrase_items ?? []) {
    const f = phraseFamily(label);
    families.set(f, (families.get(f) ?? 0) + 1);
  }
  const size = `${t.timeline?.rows ?? 0} lane${t.timeline?.rows === 1 ? "" : "s"}, `
    + `${t.timeline?.items ?? 0} clips and points`;
  const night = t.has_timeline
    ? `Its own timeline plays: ${size}.`
    : playing.id
      ? `No timeline yet: the ${playing.name ?? playing.id} template set lights it, a routine `
        + "per phrase."
      : "No timeline yet, and no template set is on: the operator's show runs.";
  const draft = hasDraft(t.id);
  const checks = trackChecks(t, audio, draft);
  const issues = checks.filter((c) => c.ok !== true);
  const passed = checks.filter((c) => c.ok === true);
  const wavePath = heights?.map((h, i) => {
    const x = i * 2 + 1;
    const y = Math.max(0.5, h * 18);
    return `M${x} ${(20 - y).toFixed(1)}v${(2 * y).toFixed(1)}`;
  }).join("") ?? "";
  const openDraft = () => {
    putPending({ kind: "timeline", id: t.id, set: draftSet });
    location.hash = `#studio/track/${t.id}`;
  };
  const facts = [t.bpm ? `${t.bpm.toFixed(t.bpm % 1 ? 2 : 0)} BPM` : "", mmss(t.duration_s)]
    .filter(Boolean).join(" · ");
  const byline = [t.artist, t.album].filter(Boolean).join(" · ");

  /** What lights it once its timeline is gone. */
  const without = playing.id ? `the ${playing.name ?? playing.id} template set lights it again`
    : "the operator's show runs for it again";
  // What has it on the rig: the engine refuses to remove a track that is on
  // it (the refusal is the rule; this is why the item is not offered).
  const previewing = engine.state?.preview?.track_id === t.id;
  const onRig = live ? "It is on the deck now" : previewing ? "Studio is driving the rig on it" : null;
  // What a deck is playing, if it is not this track already: linking it takes
  // the deck's own description, and its signature where a CDJ sends one.
  const deck = engine.state?.track;
  const heard = deck && deck.state !== "no_track" && deck.title && deck.match?.track_id !== t.id
    ? deck : null;
  const aliases = t.aliases ?? [];
  const files = [`tracks/${t.id}.json`,
                 ...(t.has_timeline ? [`timelines/${t.id}.json`] : []),
                 ...(t.has_waveform ? [`waveforms/${t.id}.json`] : [])];

  // The engine answers a link when it is queued, not when it is written: say
  // what was asked, and where the result shows.
  const applies = "It shows under Also answers to once the engine has written it, and applies "
    + "from the track's next play.";
  const linkTyped = () => run(async () => {
    const title = alias.title.trim();
    await ask({ type: "track_link", track_id: t.id, title, artist: alias.artist.trim(),
                album: alias.album.trim() });
    setAlias({ title: "", artist: "", album: "" });
    return `Linking "${title}" to ${t.title}. ${applies}`;
  });
  const linkHeard = () => run(async () => {
    await ask({ type: "track_link", track_id: t.id });
    return `Linking what is playing ("${heard?.title ?? ""}") to ${t.title}. ${applies}`;
  });
  const deleteTimeline = () => run(async () => {
    await ask({ type: "timeline_delete", track: t.id, base_rev: t.timeline?.rev ?? "" });
    return `Deleted timelines/${t.id}.json. ${t.title} is still in the show.`;
  });
  const remove = () => run(async () => {
    const { deleted } = await ask({ type: "track_delete", track: t.id, base_rev: t.rev ?? "",
                                    timeline_rev: t.timeline?.rev ?? "" }) as { deleted: string[] };
    return `Removed ${t.title} from the show: ${deleted.join(", ")}.`;
  });
  const pick = (next: TrackTask) => () => { setError(null); setTask(next); };
  const menu: MenuItem[] = [
    { label: "Link another description…", onClick: pick("link") },
    { label: "Delete its timeline…", danger: true, onClick: pick("timeline"),
      ...(t.has_timeline ? {} : { disabled: true, note: "It has none" }) },
    { label: "Remove from the show…", danger: true, onClick: pick("remove"),
      ...(onRig ? { disabled: true, note: onRig } : {}) },
  ];

  return (
    <div className="s-detail" aria-label="selected track">
      <DetailHead kind="Track" title={t.title}
                  badges={live ? <Badge tone="good">Playing now</Badge> : undefined}
                  meta={(byline || facts) ? <>
                    {byline && <span>{byline}</span>}
                    {facts && <span className="mono">{facts}</span>}
                  </> : undefined}
                  menu={<MoreMenu label={t.title} items={menu} />} />
      <div className="s-wave">
        {heights && (
          <svg viewBox="0 0 300 40" preserveAspectRatio="none" aria-label="waveform">
            <path d={wavePath} />
          </svg>
        )}
        <PhraseStrip items={t.phrase_items} tall />
        {families.size > 0 && <span className="muted small">
          {[...families].map(([f, n]) => (n > 1 ? `${f} ×${n}` : f)).join(", ")}</span>}
      </div>
      <div className="s-detail-actions">
        <a className="s-button d-primary s-wide" href={`#studio/track/${t.id}`}>
          {t.has_timeline ? "Open timeline" : "Make a timeline"}</a>
        {sets.length > 0 && (
          <div className="s-split">
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

      {task === "link" && (
        <Task title="Link another description" label="link a description" readOnly={!canWrite}
              onCancel={close}>
          <span className="small">A guest's copy with different tags: when a deck names a track
            this way, it is {t.title}, and its show plays.</span>
          {heard && (
            <div className="s-heard">
              <span className="small">Playing now: <b>{describe(heard)}</b>
                {heard.match?.track_id ? ` (matched to ${heard.match.track_id})` : " (not matched)"}</span>
              <button className="s-self" disabled={busy || !canWrite} onClick={() => void linkHeard()}
                      title="Takes the deck's own description, and its signature when a CDJ sends one">
                Link what is playing</button>
            </div>
          )}
          <form className="s-stack" onSubmit={(e) => {
            e.preventDefault();
            if (alias.title.trim() && !busy && canWrite) void linkTyped();
          }}>
            <input ref={field} value={alias.title} aria-label="title on the deck" maxLength={LINK_TEXT_MAX}
                   placeholder="Title, as the deck shows it"
                   onChange={(e) => setAlias({ ...alias, title: e.target.value })} />
            <input value={alias.artist} aria-label="artist on the deck" maxLength={LINK_TEXT_MAX}
                   placeholder="Artist" onChange={(e) => setAlias({ ...alias, artist: e.target.value })} />
            <input value={alias.album} aria-label="album on the deck" maxLength={LINK_TEXT_MAX}
                   placeholder="Album, if the deck sends one"
                   onChange={(e) => setAlias({ ...alias, album: e.target.value })} />
            <button type="submit" className="d-primary s-self"
                    disabled={!alias.title.trim() || busy || !canWrite}>Link it</button>
          </form>
          <span className="muted small">Capitals, accents and punctuation do not matter; the
            words do. It applies from the track's next play.</span>
        </Task>
      )}
      {task === "timeline" && t.has_timeline && (
        <Task title="Delete its timeline" label="confirm delete timeline" readOnly={!canWrite}
              onCancel={close}>
          <span className="small">Delete timelines/{t.id}.json ({size})? The track stays in the
            show, and {without}. Studio cannot undo it.</span>
          {live && <span className="small s-warn">It is playing now: this play keeps the timeline
            it started with.</span>}
          <div className="d-form">
            <button className="d-bad" disabled={busy || !canWrite} onClick={() => void deleteTimeline()}>
              Delete the timeline</button>
            <button onClick={close}>Keep it</button>
          </div>
        </Task>
      )}
      {task === "remove" && !onRig && (
        <Task title={`Remove ${t.title}`} label="confirm remove track" readOnly={!canWrite}
              onCancel={close}>
          <span className="small">This deletes, from the show folder:</span>
          <ul className="s-files" aria-label="files to delete">
            {files.map((f) => (
              <li key={f}><span className="mono">{f}</span>
                {f.startsWith("timelines/") && <span className="muted"> · its timeline, {size}</span>}
              </li>))}
          </ul>
          <span className="small">Its audio file and rekordbox are not touched, so the track can
            be added again{t.has_timeline ? "; its timeline cannot be brought back" : ""}. Studio
            cannot undo it.</span>
          <div className="d-form">
            <button className="d-bad" disabled={busy || !canWrite} onClick={() => void remove()}>
              Remove the track</button>
            <button onClick={close}>Keep it</button>
          </div>
        </Task>
      )}
      {error && <p className="small d-error" role="alert">{error}</p>}

      <DetailSection title="On the night">
        <p className="s-text">{night}</p>
      </DetailSection>
      {aliases.length > 0 && (
        <DetailSection title="Also answers to" label="also answers to" count={aliases.length}>
          <ul className="s-files" aria-label="linked descriptions">
            {aliases.map((a, i) => <li key={i}>{describe(a)}</li>)}
          </ul>
        </DetailSection>
      )}
      <DetailSection title="Checks" aside={<span className="small muted">
        {passed.length} of {checks.length} ready</span>}>
        <ul className="s-checks" aria-label="checks">
          {issues.map((c) => (
            <li key={c.text} className={c.ok === false ? "bad" : "wait"}>
              <span aria-hidden="true">{c.ok === false ? "!" : "…"}</span>
              <div>{c.text}{c.why && <span className="s-why">{c.why}</span>}</div>
            </li>
          ))}
          {passed.length > 0 && (
            <li className="ok">
              <span aria-hidden="true">✓</span>
              <div>{passed.map((c) => c.text).join(" · ")}</div>
            </li>
          )}
        </ul>
      </DetailSection>
    </div>
  );
}
