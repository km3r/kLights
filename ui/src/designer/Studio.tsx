import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { apiFetch } from "../useEngine";
import type { StudioRoute } from "../studioRoute";
import type { Engine } from "./Designer";
import { DESIGNER_CHUNK } from "./model";
import type { CatalogueTrack, RoutineSummary, ShowSummary, TrackLine } from "./model";
import { ALL, Coverage, RekordboxNav, RekordboxView, loadCatalogue } from "./Collection";
import { TrackDetail, TracksView } from "./Library";
import type { ActiveSet } from "./Library";
import { StartDialog } from "./Start";
import type { PrepPick, StartTrack } from "./Start";
import { PanelToggle, usePanels } from "./panels";
import { useDesignerGuide } from "./guide";
import "./designer.css";

/**
 * Studio's library: the show folder's tracks and routines, and the DJ's
 * rekordbox collection to add tracks from.
 *
 *   top        Studio, the event, what the rig is doing, the console
 *   left       the sidebar: the library, then rekordbox's playlists
 *   middle     the page: a table of tracks, routines, or a playlist
 *   right      details: the selected track, or how much of a playlist the
 *              show covers
 *
 * Both side panels fold away (and stay folded, per browser) so a laptop can
 * give the whole width to a table. The timeline and routine editors are their
 * own pages (Designer.tsx); this one stays mounted while moving between the
 * library's views, so a selection or a tick survives a look at a playlist.
 */

interface Library {
  tracks: TrackLine[] | null;
  error: string | null;
  routines: RoutineSummary[] | null;
  show: ShowSummary | null;
  sets: { id: string; name?: string }[];
  reload: () => void;
}

/** The folder's documents, re-read whenever the engine says the folder
 *  changed: tracks prepped here or anywhere else appear when the engine has
 *  reloaded them, not when a reply guesses it has. */
function useLibrary(engine: Engine): Library {
  const [tracks, setTracks] = useState<TrackLine[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [routines, setRoutines] = useState<RoutineSummary[] | null>(null);
  const [show, setShow] = useState<ShowSummary | null>(null);
  const [sets, setSets] = useState<{ id: string; name?: string }[]>([]);
  const [asked, setAsked] = useState(0);
  const folderRev = engine.state?.show?.rev;
  useEffect(() => {
    let live = true;
    apiFetch<{ tracks: TrackLine[] }>("/api/tracks")
      .then((r) => { if (live) { setTracks(r.tracks); setError(null); } })
      .catch((e: Error) => { if (live) setError(e.message); });
    apiFetch<{ routines: RoutineSummary[] }>("/api/routines")
      .then((r) => { if (live) setRoutines(r.routines); })
      .catch(() => { if (live) setRoutines([]); });
    apiFetch<ShowSummary>("/api/show")
      .then((r) => { if (live) setShow(r); }).catch(() => { if (live) setShow(null); });
    apiFetch<{ templates: { id: string; name?: string }[] }>("/api/templates")
      .then((r) => { if (live) setSets(r.templates); }).catch(() => { if (live) setSets([]); });
    return () => { live = false; };
  }, [folderRev, asked]);
  const reload = useCallback(() => setAsked((n) => n + 1), []);
  return { tracks, error, routines, show, sets, reload };
}

interface Dialog { title: string; prep?: PrepPick[]; tracks?: StartTrack[]; ran?: boolean }

export default function Studio({ engine, route }: {
  engine: Engine; route: Exclude<StudioRoute, { view: "track" } | { view: "routine" }>;
}) {
  const [panels, toggle] = usePanels();
  const lib = useLibrary(engine);
  const guide = useDesignerGuide("designer");
  const [selected, setSelected] = useState<string | null>(null);
  const [tickedTracks, setTickedTracks] = useState<Set<string>>(new Set());
  const [tickedRb, setTickedRb] = useState<Set<number>>(new Set());
  const [dialog, setDialog] = useState<Dialog | null>(null);

  // Something to look at in the details panel from the start.
  useEffect(() => {
    if (!lib.tracks?.length) return;
    if (!selected || !lib.tracks.some((t) => t.id === selected)) setSelected(lib.tracks[0]!.id);
  }, [lib.tracks, selected]);
  // A link that asks for a track by name (the console's "Add it in Studio")
  // means to look in rekordbox: read it without waiting for a click.
  const find = route.view === "rekordbox" ? route.find : undefined;
  useEffect(() => { if (find) loadCatalogue(false); }, [find]);

  const setId = typeof lib.show?.show?.template_set === "string" ? lib.show.show.template_set : null;
  const set: ActiveSet = { id: setId, name: lib.sets.find((s) => s.id === setId)?.name ?? setId };
  const liveTrack = engine.state?.track?.match?.track_id ?? null;
  const tracks = lib.tracks ?? [];
  const sel = tracks.find((t) => t.id === selected) ?? null;
  const canWrite = engine.tier === "configure";

  let main: ReactNode;
  let side: ReactNode | null = null;
  if (route.view === "rekordbox") {
    main = (
      <RekordboxView key={`${route.scope}:${route.find ?? ""}`} scope={route.scope}
                     find={route.find} tracks={tracks} ticked={tickedRb} setTicked={setTickedRb}
                     canWrite={canWrite}
                     onAdd={(picked: CatalogueTrack[]) => setDialog({
                       title: `Add ${picked.length} track${picked.length === 1 ? "" : "s"} to the show`,
                       prep: picked.map((t) => ({ id: t.id, title: t.title, artist: t.artist })),
                     })} />
    );
    side = <Coverage scope={route.find ? ALL : route.scope} tracks={tracks} />;
  } else if (route.view === "routines") {
    main = <RoutinesView routines={lib.routines} />;
  } else {
    main = (
      <TracksView tracks={lib.tracks} error={lib.error} set={set} liveTrack={liveTrack}
                  selected={selected} onSelect={setSelected}
                  ticked={tickedTracks} setTicked={setTickedTracks}
                  onStart={(ids) => {
                    const picked = tracks.filter((t) => ids.includes(t.id));
                    setDialog({ title: `Make timelines for ${picked.length} track${picked.length === 1 ? "" : "s"}`,
                                tracks: picked.map((t) => ({ id: t.id, title: t.title, artist: t.artist })) });
                  }} />
    );
    side = sel ? <TrackDetail key={sel.id} t={sel} set={set} sets={lib.sets} live={sel.id === liveTrack} />
      : <p className="muted small">Select a track to see what it needs.</p>;
  }

  return (
    <div className="designer studio" data-chunk={DESIGNER_CHUNK}>
      <header className="d-top s-top">
        <PanelToggle open={panels.nav} side="left" label="sidebar" onToggle={() => toggle("nav")} />
        <a className="s-brand" href="#studio" aria-label="Studio home">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
               strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
            <circle cx="12" cy="15" r="5" /><path d="M12 2.5v5M4 5.5l3.6 4.4M20 5.5l-3.6 4.4" /></svg>
          <span>kLights <b>Studio</b></span>
        </a>
        {engine.state?.event && <span className="s-event">{engine.state.event}</span>}
        <span className="grow" />
        <LivePill engine={engine} />
        <a className="s-button" href="#show" title="Back to the console, in this tab">Console</a>
        {guide.button}
        {side != null && (
          <PanelToggle open={panels.side} side="right" label="details" onToggle={() => toggle("side")} />)}
      </header>
      {guide.banner}
      <div className="s-body">
        {panels.nav && (
          <nav className="s-nav" aria-label="Studio">
            <div className="s-sec">Library</div>
            <a className={`s-nav-item${route.view === "tracks" ? " on" : ""}`} href="#studio"
               aria-current={route.view === "tracks" ? "page" : undefined}>
              <span className="s-nav-name">Tracks</span><span className="s-n">{lib.tracks?.length ?? ""}</span></a>
            <a className={`s-nav-item${route.view === "routines" ? " on" : ""}`} href="#studio/routines"
               aria-current={route.view === "routines" ? "page" : undefined}>
              <span className="s-nav-name">Routines</span><span className="s-n">{lib.routines?.length ?? ""}</span></a>
            <RekordboxNav scope={route.view === "rekordbox" && !route.find ? route.scope : null} />
          </nav>
        )}
        <main className="s-main">
          {main}
          {engine.status !== "open" && <p className="d-error">Not connected to the engine.</p>}
        </main>
        {panels.side && side != null && <aside className="s-side" aria-label="details">{side}</aside>}
        {guide.drawer}
      </div>
      {dialog && (
        <StartDialog engine={engine} title={dialog.title} prep={dialog.prep} tracks={dialog.tracks}
                     sets={lib.sets} activeSet={setId}
                     onClose={() => {
                       // The ticks go only once they have been acted on: a
                       // Cancel keeps them, to adjust and try again.
                       if (dialog.ran) {
                         if (dialog.prep) setTickedRb(new Set()); else setTickedTracks(new Set());
                       }
                       setDialog(null);
                     }}
                     onDone={() => {
                       setDialog((d) => (d ? { ...d, ran: true } : d));
                       lib.reload();
                     }} />
      )}
    </div>
  );
}

/** What the rig is doing, on every Studio page: whose transport it is on, and
 *  whether Follow is armed -- the same facts the console's banners give. */
export function LivePill({ engine }: { engine: Engine }) {
  const s = engine.state;
  if (engine.status !== "open") {
    return <span className="s-live-pill bad" role="status"><i />Not connected to the engine</span>;
  }
  if (s?.preview) {
    return (
      <span className="s-live-pill warn" role="status"><i />
        {s.preview.client === engine.clientId ? "This page" : s.preview.name} is driving the rig
        on {s.preview.track_id}
        {engine.tier === "configure" && (
          <button className="s-pill-btn" onClick={() => engine.send({ type: "preview_release" })}>
            Release</button>)}
      </span>
    );
  }
  const playing = s?.track && s.track.state !== "no_track" ? s.track.title : null;
  return (
    <span className="s-live-pill good" role="status"><i />Rig on the live show
      {playing && <span className="muted"> · {playing}</span>}
      {s?.program && <span className="muted"> · Follow {s.program.armed ? "ARMED" : "SAFE"}</span>}
    </span>
  );
}

// -- routines ------------------------------------------------------------------

function RoutinesView({ routines }: { routines: RoutineSummary[] | null }) {
  const [newId, setNewId] = useState("");
  const list = routines ?? [];
  const idOk = /^[a-z0-9][a-z0-9_-]{0,63}$/.test(newId) && !list.some((r) => r.id === newId);
  return (
    <section className="s-page" aria-label="routines">
      <div className="s-page-head">
        <div>
          <h1>Routines</h1>
          <span className="muted small">The reusable pieces a timeline's clips play: a few bars
            written for roles, not fixtures, so they work on any rig.</span>
        </div>
        <span className="grow" />
        <form className="d-form" onSubmit={(e) => {
          e.preventDefault();
          if (idOk) location.hash = `#studio/routine/${newId}`;
        }}>
          <input value={newId} onChange={(e) => setNewId(e.target.value.trim())}
                 placeholder="new-routine-id" aria-label="new routine id" />
          <button type="submit" className="d-primary" disabled={!idOk}>New routine</button>
          {newId && !idOk && <span className="small d-error">
            {list.some((r) => r.id === newId) ? "already there"
              : "lower-case letters, digits, - and _ -- it is also the file name"}</span>}
        </form>
      </div>
      {routines == null && <p className="muted">Loading the show folder…</p>}
      {!!list.length && (
        <div className="s-table-wrap">
          <table className="s-table">
            <thead>
              <tr><th>Routine</th><th>Length</th><th>Roles</th><th>Parameters</th><th>Variations</th></tr>
            </thead>
            <tbody>
              {list.map((r) => (
                <tr key={r.id}>
                  <td className="s-track"><a className="s-link" href={`#studio/routine/${r.id}`}>
                    <b>{r.name ?? r.id}</b><span className="muted mono">{r.id}</span></a>
                    {r.rig && <span className="d-badge">this rig only</span>}</td>
                  <td>{r.bars} bars{r.loop ? ", loops" : ", once"}</td>
                  <td>{Object.keys(r.roles).join(", ")}</td>
                  <td className="mono small">{Object.keys(r.params).map((p) => `$${p}`).join(" ")}</td>
                  <td>{r.variations.join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
