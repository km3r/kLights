import { useCallback, useEffect, useState } from "react";
import type { FoundPalette, PaletteSummary, TemplateSetDoc, TemplateSummary } from "./model";
import { PaletteDetail, PalettesView } from "./Palettes";
import { TemplateAside, TemplatesView } from "./Templates";
import { ShowSettingsView } from "./ShowSettings";
import type { ReactNode } from "react";
import { apiFetch } from "../useEngine";
import type { StudioRoute } from "../studioRoute";
import type { Engine } from "./Designer";
import { DESIGNER_CHUNK } from "./model";
import type { CatalogueTrack, RoutineSummary, ShowSummary, TrackLine } from "./model";
import { ALL, Coverage, RekordboxNav, RekordboxView, loadCatalogue } from "./Collection";
import { TrackDetail, TracksView } from "./Library";
import type { ActiveSet } from "./Library";
import { RoutineDetail, RoutinesView } from "./Routines";
import type { Action } from "./Routines";
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
  sets: TemplateSummary[];
  palettes: PaletteSummary[] | null;
  found: FoundPalette[];
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
  const [sets, setSets] = useState<TemplateSummary[]>([]);
  const [palettes, setPalettes] = useState<{ palettes: PaletteSummary[] | null; found: FoundPalette[] }>(
    { palettes: null, found: [] });
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
    apiFetch<{ templates: TemplateSummary[] }>("/api/templates")
      .then((r) => { if (live) setSets(r.templates); }).catch(() => { if (live) setSets([]); });
    apiFetch<{ palettes: PaletteSummary[]; found: FoundPalette[] }>("/api/palettes")
      .then((r) => { if (live) setPalettes(r); })
      .catch(() => { if (live) setPalettes({ palettes: [], found: [] }); });
    return () => { live = false; };
  }, [folderRev, asked]);
  const reload = useCallback(() => setAsked((n) => n + 1), []);
  return { tracks, error, routines, show, sets, palettes: palettes.palettes,
           found: palettes.found, reload };
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
  const [routineId, setRoutineId] = useState<string | null>(null);
  // What a card's menu asked the details panel to open on; `n` makes the
  // same ask twice count twice.
  const [routineAsk, setRoutineAsk] = useState<{ action: Action | null; n: number }>(
    { action: null, n: 0 });
  // What the last routine change did. Kept here, not in the panel: a rename
  // or a delete replaces the panel that did it.
  const [routineSaid, setRoutineSaid] = useState<string | null>(null);
  // The template set being edited, as the panel beside it needs it: the
  // working copy, unsaved or not.
  const [tpl, setTpl] = useState<{ doc: TemplateSetDoc | null; dirty: boolean; rev: string }>(
    { doc: null, dirty: false, rev: "" });
  const onTemplateDoc = useCallback((doc: TemplateSetDoc | null, dirty: boolean, rev: string) =>
    setTpl({ doc, dirty, rev }), []);
  // What the last template-set or show.json change did (as above).
  const [setSaid, setSetSaid] = useState<string | null>(null);
  const tplId = route.view === "templates" ? (route.id ?? lib.sets[0]?.id ?? null) : null;
  // The palette on screen. Undefined: none chosen yet, so the first; null:
  // none, on purpose (the one shown was just deleted).
  const [paletteId, setPaletteId] = useState<string | null | undefined>(undefined);
  const shownPalette = paletteId === undefined ? (lib.palettes?.[0]?.id ?? null) : paletteId;
  useEffect(() => { setSetSaid(null); }, [tplId, route.view]);

  // Something to look at in the details panel from the start.
  useEffect(() => {
    if (!lib.tracks?.length) return;
    if (!selected || !lib.tracks.some((t) => t.id === selected)) setSelected(lib.tracks[0]!.id);
  }, [lib.tracks, selected]);
  // The first routine, once there are routines -- but never in place of one
  // that is about to appear (a duplicate, a rename) or has just gone.
  useEffect(() => {
    if (routineId == null && lib.routines?.length) setRoutineId(lib.routines[0]!.id);
  }, [lib.routines, routineId]);
  const selectRoutine = (id: string) => {
    if (id !== routineId) { setRoutineSaid(null); setRoutineAsk((a) => ({ action: null, n: a.n })); }
    setRoutineId(id);
  };
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
  } else if (route.view === "templates") {
    main = <TemplatesView engine={engine} sets={lib.routines == null ? null : lib.sets}
                          routines={lib.routines ?? []} library={lib.palettes ?? []}
                          current={tplId} onDoc={onTemplateDoc} />;
    side = (
      <>
        {setSaid && <p className="small s-ok" role="status">{setSaid}</p>}
        {tplId ? (
          <TemplateAside key={tplId} engine={engine} id={tplId} doc={tpl.doc} dirty={tpl.dirty}
                         rev={tpl.rev} summary={lib.sets.find((s) => s.id === tplId)}
                         sets={lib.sets} tracks={tracks} show={lib.show}
                         onDone={(said) => { setSetSaid(said); lib.reload(); }} />
        ) : <p className="muted small">No template set yet.</p>}
      </>
    );
  } else if (route.view === "palettes") {
    main = (
      <PalettesView engine={engine} palettes={lib.palettes} found={lib.found}
                    selected={shownPalette} onSelect={(id) => { setSetSaid(null); setPaletteId(id); }}
                    onDone={(said) => { setSetSaid(said); lib.reload(); }} />
    );
    const p = lib.palettes?.find((x) => x.id === shownPalette) ?? null;
    side = (
      <>
        {setSaid && <p className="small s-ok" role="status">{setSaid}</p>}
        {p ? (
          <PaletteDetail key={p.id} engine={engine} p={p} palettes={lib.palettes ?? []}
                         onSelect={setPaletteId}
                         onDone={(said) => { setSetSaid(said); lib.reload(); }} />
        ) : <p className="muted small">{lib.palettes?.length ? "Select a palette."
          : "The library is empty."}</p>}
      </>
    );
  } else if (route.view === "show") {
    main = (
      <>
        {setSaid && <p className="small s-ok" role="status">{setSaid}</p>}
        <ShowSettingsView engine={engine} show={lib.show} sets={lib.sets}
                          routines={lib.routines ?? []}
                          onDone={(said) => { setSetSaid(said); lib.reload(); }} />
      </>
    );
  } else if (route.view === "routines") {
    main = (
      <RoutinesView routines={lib.routines} selected={routineId} onSelect={selectRoutine}
                    onAction={(id, action) => {
                      selectRoutine(id);
                      setRoutineAsk((a) => ({ action, n: a.n + 1 }));
                      if (!panels.side) toggle("side");
                    }} />
    );
    const r = lib.routines?.find((x) => x.id === routineId) ?? null;
    side = (
      <>
        {routineSaid && <p className="small s-ok" role="status">{routineSaid}</p>}
        {r ? (
          <RoutineDetail key={r.id} engine={engine} r={r} routines={lib.routines ?? []}
                         action={routineAsk.action} actionKey={routineAsk.n}
                         onSelect={setRoutineId}
                         onDone={(said) => {
                           setRoutineSaid(said);
                           // Done is done: the next panel opens on nothing.
                           setRoutineAsk((ask) => ({ action: null, n: ask.n }));
                           lib.reload();
                         }} />
        ) : <p className="muted small">{lib.routines?.length ? "Select a routine to see where it is used."
          : "No routines yet."}</p>}
      </>
    );
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
            <a className={`s-nav-item${route.view === "templates" ? " on" : ""}`} href="#studio/templates"
               aria-current={route.view === "templates" ? "page" : undefined}>
              <span className="s-nav-name">Template sets</span><span className="s-n">{lib.sets.length || ""}</span></a>
            <a className={`s-nav-item${route.view === "palettes" ? " on" : ""}`} href="#studio/palettes"
               aria-current={route.view === "palettes" ? "page" : undefined}>
              <span className="s-nav-name">Palettes</span><span className="s-n">{lib.palettes?.length || ""}</span></a>
            <div className="s-sec">Show</div>
            <a className={`s-nav-item${route.view === "show" ? " on" : ""}`} href="#studio/show"
               aria-current={route.view === "show" ? "page" : undefined}>
              <span className="s-nav-name">Show settings</span></a>
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
