import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Command, EngineState, Reply, Tier } from "../types";
import { apiFetch, apiUrl } from "../useEngine";
import { HelpHeading } from "../components";
import { PlanSvg } from "../Plan";
import {
  BEATS_PER_BAR, DESIGNER_CHUNK, Grid, barBeat, clock, decodeWave, draftFromTemplate, findItem,
  itemName, whoDrives,
} from "./model";
import type {
  Item, PaletteSummary, Row, RoutineSummary, TemplateSetDoc, TimelineDoc, TrackDoc, Wave,
} from "./model";
import { PHRASE_HUE, phraseFamily, phraseMatch } from "./model";
import {
  Editor, ParamLanes, clipOps, copyRange, cutRange, parsePointId, parseWaveId, rememberBack,
  setClipBoard, timelineLaneSpecs, uniqueId, useClipBoard, useEditorKeys, useHistory,
} from "./edit";
import type { Placeable, RowsDoc } from "./edit";
import { Browser } from "./Browser";
import { Lane, Phrases, Ruler, WaveLane } from "./lanes";
import RoutineEditor from "./RoutineEditor";
import { useDesignerGuide } from "./guide";
import Studio from "./Studio";
import { clearPending, peekPending } from "./pending";
import { PanelToggle, usePanels } from "./panels";
import type { StudioRoute } from "../studioRoute";
import "./designer.css";

/**
 * The designer: a desktop view of one track's show, laid out as arrangement
 * lanes (layout B, chosen with the user from the mock-ups).
 *
 *   top        transport, position, snap, track and match, undo/redo/save
 *   lanes      bar ruler, rekordbox's phrases, the waveform, then the
 *              timeline's rows top to bottom -- the higher lane wins -- with
 *              hits, automation and the VJ lane last
 *   right      the rig from above, live from the engine, and who drives each
 *              lane at the playhead
 *   bottom     the selected clip, and inside the routine it plays
 *
 * It is a client of the engine like any console: documents come over
 * `GET /api/*`, writes and previews are commands answered on the socket. The
 * engine is the only thing that evaluates a beam, so "Drive the rig" puts THIS
 * page's transport on the real rig (`preview_arm`) rather than simulating it.
 *
 * Loaded lazily from `#studio`: a phone never downloads it.
 */


export interface Engine {
  state: EngineState | null;
  status: string;
  send: (c: Command) => void;
  request: (c: Command, timeoutMs?: number) => Promise<Reply>;
  tier: Tier;
  /** This console's id on the engine, once it has said hello. */
  clientId?: string | null;
}

const HEADER_W = 170;
const ZOOMS = [2, 3, 4, 6, 8, 12, 16, 24];

export default function Designer({ engine, route }: { engine: Engine; route: StudioRoute }) {
  if (route.view === "routine") {
    return <RoutineEditor key={route.id} engine={engine} routineId={route.id} />;
  }
  if (route.view === "track") return <TrackDesigner key={route.id} engine={engine} trackId={route.id} />;
  return <Studio engine={engine} route={route} />;
}

/** What a copied timeline's start needs saying: how far the two tracks'
 *  phrases agree, since that is how far the copied clips are on the right
 *  phrases. */
function copyNote(from: TrackDoc, to: TrackDoc): string {
  const name = from.identity.title;
  const a = from.phrases?.items ?? [];
  const b = to.phrases?.items ?? [];
  const same = phraseMatch(a, b);
  const tail = " Nothing is saved until you press Save.";
  if (a.length && same === a.length && same === b.length) {
    return `Copied ${name}'s timeline. Its phrases are this track's, phrase for phrase.${tail}`;
  }
  if (same > 0) {
    const bar = Math.floor(a[same - 1]![1] / BEATS_PER_BAR);
    return `Copied ${name}'s timeline. The phrases agree up to bar ${bar}: check the clips after `
      + `that.${tail}`;
  }
  return `Copied ${name}'s timeline. The phrases differ, so the clips sit on the same bars, `
    + `not the same phrases: check them.${tail}`;
}

// -- the transport ------------------------------------------------------------

/** Where the designer is in the track, playing or not. Its own clock always
 *  runs; when the audio element is really playing it is the source of truth
 *  and the clock follows it -- so the page works with no audio at all. */
function useTransport(duration: number) {
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const audio = useRef<HTMLAudioElement | null>(null);
  const anchor = useRef({ time: 0, at: 0 });
  const timeRef = useRef(0);
  timeRef.current = time;

  useEffect(() => {
    if (!playing) return;
    anchor.current = { time: timeRef.current, at: performance.now() };
    let frame = 0;
    const tick = () => {
      const el = audio.current;
      let t: number;
      if (el && !el.paused && el.readyState >= 2) {
        t = el.currentTime;
      } else {
        t = anchor.current.time + (performance.now() - anchor.current.at) / 1000;
      }
      if (duration && t >= duration) {
        setTime(duration);
        setPlaying(false);
        return;
      }
      setTime(t);
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [playing, duration]);

  const seek = useCallback((t: number) => {
    const clamped = Math.max(0, duration ? Math.min(duration, t) : t);
    setTime(clamped);
    anchor.current = { time: clamped, at: performance.now() };
    const el = audio.current;
    if (el) { try { el.currentTime = clamped; } catch { /* not loaded */ } }
  }, [duration]);

  const play = useCallback((on: boolean) => {
    setPlaying(on);
    const el = audio.current;
    if (!el) return;
    try {
      if (on) { el.currentTime = timeRef.current; void el.play()?.catch?.(() => {}); }
      else el.pause();
    } catch { /* no media support (tests), the clock carries on */ }
  }, []);

  return { time, playing, seek, play, audio };
}

// -- one track ----------------------------------------------------------------

function TrackDesigner({ engine, trackId }: { engine: Engine; trackId: string }) {
  const [track, setTrack] = useState<TrackDoc | null>(null);
  const [routines, setRoutines] = useState<RoutineSummary[]>([]);
  const [wave, setWave] = useState<Wave | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const history = useHistory();
  const { doc, setBase } = history;
  const [rev, setRev] = useState<string>("");
  const [zoom, setZoom] = useState(6);
  const [selected, setSelected] = useState<string | null>(null);
  const [audioSrc, setAudioSrc] = useState<string | null>(apiUrl(`/api/audio/${trackId}`));
  const [audioState, setAudioState] = useState<"loading" | "ok" | "none">("loading");
  const guide = useDesignerGuide("designer");
  const [panels, togglePanel] = usePanels();
  const [notice, setNotice] = useState<string | null>(null);
  // The show's palette library, for the browser to copy from.
  const [library, setLibrary] = useState<PaletteSummary[]>([]);
  // A phrase picked as a section: what Fill, Copy, Paste and Clear act on.
  const [section, setSection] = useState<{ start: number; end: number; label: string } | null>(null);
  // A clip's menu, open at the pointer.
  const [menu, setMenu] = useState<{ id: string; x: number; y: number } | null>(null);
  const board = useClipBoard();

  useEffect(() => {
    apiFetch<{ doc: TrackDoc }>(`/api/tracks/${trackId}`)
      .then((r) => setTrack(r.doc)).catch((e: Error) => setLoadError(e.message));
    apiFetch<{ doc: TimelineDoc; rev: string }>(`/api/timelines/${trackId}`)
      .then((r) => { setBase(r.doc); setRev(r.rev); })
      .catch(() => {
        // No timeline yet: start one. Saved with base_rev "" (a new file).
        setBase({ kind: "klights.timeline", version: 1, track: trackId, rows: [] });
        setRev("");
      });
    apiFetch<{ routines: RoutineSummary[] }>("/api/routines")
      .then((r) => setRoutines(r.routines)).catch(() => setRoutines([]));
    apiFetch<{ palettes: PaletteSummary[] }>("/api/palettes")
      .then((r) => setLibrary(r.palettes)).catch(() => setLibrary([]));
    apiFetch<{ doc: { preview?: string; detail?: { format: string; rate?: number; data: string } } }>(
      `/api/waveforms/${trackId}`)
      .then((r) => setWave(decodeWave(r.doc))).catch(() => setWave(null));
  }, [trackId, setBase]);

  // "Draft from" in the library: the draft is made here, once the timeline has
  // loaded, as one undoable edit -- nothing is written until Save.
  const { apply } = history;
  const drafted = useRef(false);
  useEffect(() => {
    if (drafted.current || !track || !doc) return;
    drafted.current = true;
    const pending = peekPending("timeline", trackId);
    if (!pending) return;
    clearPending("timeline", trackId);
    const base = doc;
    if (pending.copy) {
      // Another track's timeline as this one's start: its lanes, on this
      // track's grid. Bars are bars, so a clip lands on the same bar -- which
      // is the same phrase only as far as the two tracks' phrases agree.
      const from = pending.copy;
      Promise.all([apiFetch<{ doc: TimelineDoc }>(`/api/timelines/${from}`),
                   apiFetch<{ doc: TrackDoc }>(`/api/tracks/${from}`)])
        .then(([{ doc: other }, { doc: otherTrack }]) => {
          apply((d) => {
            const tl = d as unknown as TimelineDoc;
            tl.rows = structuredClone(other.rows);
            if (other.palettes) tl.palettes = structuredClone(other.palettes);
            else delete tl.palettes;
            if (other.palette) tl.palette = other.palette; else delete tl.palette;
            if (track.grid.rev) tl.grid_rev = track.grid.rev;
          });
          setNotice(copyNote(otherTrack, track));
        })
        .catch((e: Error) => setNotice(`Could not copy the timeline of ${from}: ${e.message}`));
      return;
    }
    if (!pending.set) return;
    const setId = pending.set;
    apiFetch<{ doc: TemplateSetDoc }>(`/api/templates/${setId}`)
      .then(({ doc: ts }) => {
        const problem = draftFromTemplate(structuredClone(base), track, ts);
        if (problem) { setNotice(`Could not draft from ${ts.name ?? setId}: ${problem}.`); return; }
        apply((d) => { draftFromTemplate(d, track, ts); });
        setNotice(`Drafted from ${ts.name ?? setId}. Nothing is saved until you press Save, `
          + "and Undo takes the draft back.");
      })
      .catch((e: Error) => setNotice(`Could not read the template set ${setId}: ${e.message}`));
  }, [track, doc, trackId, apply]);

  const grid = useMemo(() => (track ? new Grid(track.grid.segments) : null), [track]);
  const { setPhrases } = history;
  useEffect(() => {
    setPhrases((track?.phrases?.items ?? []).flatMap(([s, e]) => [s, e]));
  }, [track, setPhrases]);
  const duration = track?.identity.duration_s ?? 0;
  const transport = useTransport(duration);
  const beat = grid ? grid.beatAt(transport.time) : 0;
  const totalBeats = useMemo(() => {
    if (!grid) return 64;
    const end = duration ? grid.beatAt(duration) : 0;
    const items = (doc?.rows ?? []).flatMap((r) => (r.items ?? []).map((i) => i.at + i.len));
    return Math.ceil(Math.max(end, ...items, 64) / BEATS_PER_BAR + 2) * BEATS_PER_BAR;
  }, [grid, duration, doc]);

  // -- driving the rig ------------------------------------------------------
  // `send` is stable; `engine` itself is a new object on every snapshot (10 Hz),
  // so no effect here may depend on it -- a cleanup keyed on it would let go of
  // the rig on the next snapshot.
  const { send } = engine;
  const [driving, setDriving] = useState(false);
  const [driveError, setDriveError] = useState<string | null>(null);
  const preview = engine.state?.preview ?? null;

  const arm = async (force = false) => {
    setDriveError(null);
    const reply = await engine.request({ type: "preview_arm", track_id: trackId, force });
    if (reply.ok) setDriving(true);
    else setDriveError(reply.error ?? "the engine refused");
  };
  const release = () => {
    setDriving(false);
    engine.send({ type: "preview_release" });
  };
  // The engine can end this page's preview without being asked: the socket
  // dropped (it lets go of a designer that disconnects), another console
  // pressed Release, or another designer took the rig. Stop driving then --
  // a page that went on sending its transport would be talking to nobody, and
  // say so in every console's notices ten times a second.
  const seenMine = useRef(false);
  const mine = preview != null && engine.clientId != null && preview.client === engine.clientId;
  useEffect(() => {
    if (!driving) { seenMine.current = false; return; }
    if (engine.status !== "open") {
      setDriving(false);
      setDriveError("The connection to the engine dropped, so the rig went back to the show.");
    } else if (preview && engine.clientId && preview.client !== engine.clientId) {
      setDriving(false);
      setDriveError(`${preview.name} took the rig.`);
    } else if (mine) {
      seenMine.current = true;
    } else if (!preview && seenMine.current) {
      setDriving(false);
      setDriveError("The rig was released from another console.");
    }
  }, [driving, preview, mine, engine.status, engine.clientId]);
  useEffect(() => {
    if (!driving) return;
    const push = () => send({ type: "preview_transport",
                              time_s: transportRef.current.time,
                              playing: transportRef.current.playing });
    push();
    const timer = setInterval(push, 100);
    return () => clearInterval(timer);
  }, [driving, send]);
  const transportRef = useRef(transport);
  transportRef.current = transport;
  useEffect(() => {
    if (driving) engine.send({ type: "preview_transport", time_s: transport.time,
                               playing: transport.playing });
    // A seek or play/pause goes at once, not on the next 100 ms tick.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [transport.playing]);
  useEffect(() => () => { if (driving) send({ type: "preview_release" }); },
            [driving, send]);

  const beatRef = useRef(0);
  beatRef.current = beat;
  useEditorKeys({ history, selected, setSelected,
                  playPause: () => transport.play(!transport.playing),
                  clip: { kind: "timeline", beat: () => beatRef.current } });
  // A menu closes on a click anywhere else, or Escape.
  useEffect(() => {
    if (!menu) return;
    const close = (e: Event) => {
      if (e instanceof KeyboardEvent && e.key !== "Escape") return;
      if (e instanceof MouseEvent && (e.target as Element | null)?.closest?.(".d-ctx")) return;
      setMenu(null);
    };
    addEventListener("mousedown", close);
    addEventListener("keydown", close);
    return () => { removeEventListener("mousedown", close); removeEventListener("keydown", close); };
  }, [menu]);

  // While playing, keep the playhead in view: page the lanes along when it
  // reaches the right edge, so it does not run off the screen.
  const lanesRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = lanesRef.current;
    if (!el || !transport.playing) return;
    const at = HEADER_W + beat * zoom;
    if (at > el.scrollLeft + el.clientWidth - 40 || at < el.scrollLeft + HEADER_W) {
      el.scrollLeft = Math.max(0, at - HEADER_W - 40);
    }
  }, [beat, zoom, transport.playing]);

  const seekBeat = (b: number) => {
    if (!grid) return;
    transport.seek(grid.timeAt(b));
    if (driving) engine.send({ type: "preview_transport", time_s: grid.timeAt(b),
                               playing: transport.playing });
  };

  if (loadError) {
    return (
      <div className="designer" data-chunk={DESIGNER_CHUNK}>
        <header className="d-top"><a className="d-link" href="#studio">Studio</a></header>
        <p className="d-error">{loadError}</p>
      </div>
    );
  }
  if (!track || !doc || !grid) {
    return <div className="designer" data-chunk={DESIGNER_CHUNK}>
      <p className="muted" style={{ padding: 16 }}>Loading {trackId}…</p></div>;
  }

  const x = (b: number) => b * zoom;
  const width = totalBeats * zoom;
  const drivers = whoDrives(doc, beat);
  const selectedItem = findItem(doc, selected);
  const selectedPoint = parsePointId(selected);
  const pointRow = selectedPoint ? doc.rows.find((r) => r.id === selectedPoint.row) : undefined;
  const waveRow = doc.rows.find((r) => r.id === parseWaveId(selected) && r.wave);
  const match = engine.state?.track?.match;
  const live = engine.state?.track;
  const paramLanes = timelineLaneSpecs(doc, routines);

  /** One placement as an edit, its new item's id read from a dry run on a
   *  copy (an edit is applied when React renders, too late to read back). */
  const edit = (mutate: (d: RowsDoc) => string | null): string | null => {
    const id = mutate(structuredClone(doc));
    if (id) history.apply((d) => { mutate(d); });
    return id;
  };
  const laneFor = (d: RowsDoc, target: string, make: () => Row): Row =>
    d.rows.find((r) => r.type === "clips" && r.target === target)
      ?? (() => { const r = make(); if (target === "scene") d.rows.unshift(r); else d.rows.push(r); return r; })();
  /** Place what the browser offers: at a beat, on a lane if it was dropped
   *  on one, else on the lane it belongs on. */
  const place = (what: Placeable, at: number, rowId?: string) => {
    const start = Math.max(0, history.snapBeat(at));
    const onto = rowId ? doc.rows.find((r) => r.id === rowId) : undefined;
    let made: string | null = null;
    if (what.kind === "routine") {
      const r = routines.find((x) => x.id === what.id);
      if (!r) return;
      if (onto && !(onto.type === "clips" && onto.target !== "palette")) {
        setNotice("A routine goes on a scene, movement, colour or level lane.");
        return;
      }
      made = edit((d) => {
        const lane = onto ? d.rows.find((x) => x.id === onto.id)!
          : laneFor(d, "scene", () => ({ id: uniqueId(d, "scene"), type: "clips", target: "scene",
                                         gap: "fill", items: [] }));
        const id = uniqueId(d, r.id);
        (lane.items ??= []).push({ id, kind: "routine", routine: r.id, at: start,
                                   len: r.bars * BEATS_PER_BAR });
        return id;
      });
    } else if (what.kind === "palette") {
      if (onto && onto.target !== "palette") {
        setNotice("A palette goes on the palette lane.");
        return;
      }
      // Until the phrase ends, if the playhead is in one; else four bars.
      const phrase = (track.phrases?.items ?? []).find(([s, e]) => s <= start && start < e);
      const len = phrase ? phrase[1] - start : 4 * BEATS_PER_BAR;
      made = edit((d) => {
        const tl = d as unknown as TimelineDoc;
        if (!(tl.palettes ?? {})[what.name]) {
          if (!what.colours) return null;
          // From the library: this track gets its own copy, under its name.
          tl.palettes = { ...(tl.palettes ?? {}), [what.name]: { ...what.colours } };
        }
        const lane = onto ? d.rows.find((x) => x.id === onto.id)!
          : laneFor(d, "palette", () => ({ id: uniqueId(d, "palette"), type: "clips",
                                           target: "palette", gap: "exclusive", items: [] }));
        const id = uniqueId(d, `pal-${start}`);
        (lane.items ??= []).push({ id, kind: "palette", palette: what.name, at: start, len });
        return id;
      });
    } else {
      if (onto && onto.type !== "hits") {
        setNotice("A hit goes on a hits lane.");
        return;
      }
      made = edit((d) => {
        let lane = onto ? d.rows.find((x) => x.id === onto.id) : d.rows.find((r) => r.type === "hits");
        if (!lane) {
          lane = { id: uniqueId(d, "hits"), type: "hits", items: [] };
          d.rows.push(lane);
        }
        const id = uniqueId(d, `${what.hit}-${start}`);
        const item: Item = { id, hit: what.hit, at: start,
                             len: what.hit === "strobe" ? 4 : what.hit === "flash" ? 2 : 1 };
        if (what.hit === "flash") item.envelope = "decay";
        (lane.items ??= []).push(item);
        return id;
      });
    }
    if (made) setSelected(made);
  };

  // -- a phrase as a section ------------------------------------------------
  const fillSection = (routineId: string) => {
    if (!section) return;
    const { start, end } = section;
    setSelected(edit((d) => {
      const lane = laneFor(d, "scene", () => ({ id: uniqueId(d, "scene"), type: "clips",
                                                 target: "scene", gap: "fill", items: [] }));
      cutRange(d, lane, start, end);
      const id = uniqueId(d, `${routineId}-${start}`);
      (lane.items ??= []).push({ id, kind: "routine", routine: routineId, at: start, len: end - start });
      return id;
    }));
  };
  const copySection = () => {
    if (!section) return;
    const b = copyRange(doc, section.start, section.end, "timeline");
    setClipBoard(b);
    setNotice(`Copied ${section.label}: ${b.clips.length} clip${b.clips.length === 1 ? "" : "s"} and `
      + "hits. Pick another phrase and Paste, or press Ctrl+V at the playhead.");
  };
  const clearSection = () => {
    if (!section) return;
    history.apply((d) => {
      for (const row of d.rows) {
        if (row.type === "clips" || row.type === "hits") cutRange(d, row, section.start, section.end);
      }
    });
  };
  const menuItem = menu ? findItem(doc, menu.id) : null;

  return (
    <ParamLanes.Provider value={paramLanes}>
    <div className="designer" data-chunk={DESIGNER_CHUNK}>
      <header className="d-top">
        <PanelToggle open={panels.browse} side="left" label="browser"
                     onToggle={() => togglePanel("browse")} />
        <a className="d-link" href="#studio" title="Back to Studio's library">◂</a>
        <button className={transport.playing ? "on" : ""}
                onClick={() => transport.play(!transport.playing)}>
          {transport.playing ? "Pause" : "Play"}
        </button>
        <span className="mono" aria-label="position">
          bar {barBeat(beat)} · {clock(transport.time)}
        </span>
        <span className="mono muted">
          {grid.bpmAt(transport.time).toFixed(2)} bpm
        </span>
        <span className="d-title">
          <b>{track.identity.title}</b>
          {track.identity.artist && <span className="muted"> — {track.identity.artist}</span>}
          {live?.match?.track_id === trackId && (
            <span className="small muted"> · playing live now ({match?.via?.replace(/_/g, " ")})</span>
          )}
        </span>
        <span className="grow" />
        <label className="small muted">Zoom{" "}
          <select value={zoom} onChange={(e) => setZoom(Number(e.target.value))}
                  aria-label="zoom">
            {ZOOMS.map((z) => <option key={z} value={z}>{z * 4} px/bar</option>)}
          </select>
        </label>
        <Editor.Toolbar history={history} rev={rev} setRev={setRev} engine={engine}
                        kind="timeline" ident={trackId} />
        {driving
          ? <button className="on" onClick={release}>Release the rig</button>
          : preview
            ? <button onClick={release}
                      title={`${preview.name} is driving the rig on ${preview.track_id}`}>
                Release {preview.name}'s preview</button>
            : <button onClick={() => void arm(false)}
                      title="Put this page's transport on the real rig">Drive the rig</button>}
        {guide.button}
        <PanelToggle open={panels.edit} side="right" label="side panel"
                     onToggle={() => togglePanel("edit")} />
      </header>
      {section && (
        <div className="d-section" role="toolbar" aria-label="section">
          <i style={{ background: PHRASE_HUE[phraseFamily(section.label)] ?? "#475569" }} />
          <b>{section.label}</b>
          <span className="muted small">bars {Math.floor(section.start / BEATS_PER_BAR) + 1} to{" "}
            {Math.floor(section.end / BEATS_PER_BAR)}</span>
          <span className="grow" />
          <label className="small">Fill with{" "}
            <select value="" aria-label="fill the section with"
                    onChange={(e) => { if (e.target.value) fillSection(e.target.value); }}>
              <option value="">a routine…</option>
              {routines.map((r) => <option key={r.id} value={r.id}>{r.name || r.id}</option>)}
            </select>
          </label>
          <button onClick={copySection}>Copy section</button>
          <button disabled={board?.kind !== "timeline"}
                  title="Over what is there, from the start of this phrase"
                  onClick={() => setSelected(clipOps.paste(history, "timeline", section.start))}>
            Paste here</button>
          <button onClick={clearSection}>Clear</button>
          <button aria-label="let go of the section" onClick={() => setSection(null)}>×</button>
        </div>
      )}
      {notice && (
        <div className="d-banner d-info" role="status">
          {notice}<button onClick={() => setNotice(null)}>OK</button>
        </div>
      )}
      {driveError && (
        <div className="d-banner">
          {driveError}
          {/force/.test(driveError) && (
            <button onClick={() => void arm(true)}>Take the rig anyway</button>)}
        </div>
      )}
      {guide.banner}
      {audioState === "none" && (
        <div className="d-banner">
          No audio for this track from the engine. Open the file from this machine
          (it stays here; nothing is uploaded):{" "}
          <input type="file" accept="audio/*" aria-label="open audio file"
                 onChange={(e) => {
                   const f = e.target.files?.[0];
                   if (!f) return;
                   setAudioSrc(URL.createObjectURL(f));
                   setAudioState("loading");
                 }} />
        </div>
      )}
      {audioSrc && (
        <audio ref={transport.audio} src={audioSrc} preload="auto"
               onLoadedMetadata={(e) => {
                 const d = (e.target as HTMLAudioElement).duration;
                 if (duration && Number.isFinite(d) && Math.abs(d - duration) > 2) {
                   setDriveError(`That audio is ${d.toFixed(0)} s long; the track is `
                     + `${duration.toFixed(0)} s. It may be a different edit.`);
                 }
                 setAudioState("ok");
               }}
               onError={() => setAudioState("none")} />
      )}

      <div className="d-body" style={{ gridTemplateColumns: [
        panels.browse ? "220px" : "", "minmax(0, 1fr)", panels.edit ? "320px" : "",
        guide.open ? "min(400px, 34vw)" : ""].filter(Boolean).join(" ") }}>
        {panels.browse && (
          <Browser routines={routines} palettes={Object.keys(doc.palettes ?? {})} library={library}
                   onPlace={(what) => place(what, beat)} />
        )}
        <div className="d-lanes" role="region" aria-label="lanes" ref={lanesRef}>
          <div className="d-scroll" style={{ width: width + HEADER_W }}>
            <Ruler totalBeats={totalBeats} x={x} width={width} onSeek={seekBeat} />
            <Phrases track={track} x={x} width={width} picked={section?.start ?? null}
                     onPick={(start, end, label) => setSection(
                       section?.start === start ? null : { start, end, label })} />
            <WaveLane wave={wave} grid={grid} duration={duration} x={x} width={width} />
            {doc.rows.map((row, index) => (
              <Lane key={row.id} row={row} index={index} x={x} width={width}
                    zoom={zoom} selected={selected} onSelect={setSelected}
                    history={history} beat={beat}
                    onMenu={(id, cx, cy) => setMenu({ id, x: cx, y: cy })}
                    onDropItem={(rowId, what, at) => place(what, at, rowId)} />
            ))}
            <Editor.AddLane history={history} />
            {section && (
              <div className="d-section-mark" aria-hidden="true"
                   style={{ left: HEADER_W + x(section.start), width: x(section.end) - x(section.start) }} />)}
            <div className="d-playhead" aria-hidden="true"
                 style={{ left: HEADER_W + x(beat) }} />
          </div>
        </div>

        {panels.edit && <aside className="d-side" aria-label="side panel">
          <section>
            <h3>Preview · from the engine</h3>
            {engine.state ? <PlanSvg state={engine.state} />
              : <p className="muted small">Not connected.</p>}
            <p className="small muted">
              {driving
                ? "This page is driving the rig. Every console shows it."
                : preview
                  ? `${preview.name} is driving the rig on ${preview.track_id}.`
                  : "The rig is on the live show. Drive the rig to put this "
                    + "page's transport on it."}
            </p>
          </section>
          <section>
            <HelpHeading topic="At the playhead" help={<>
              <p>Which lane drives each slot right now. Higher lanes win: a movement
                lane above the scene lane only overrides the scene's movement.</p>
              <p><b>rest</b> means the lane that owns this slot is empty here, so
                nothing drives it. <b>template set / show</b> means no lane has anything
                here, so the template set that is on shows through, or the operator's show
                where the set has nothing for this phrase.</p>
            </>}>At the playhead · bar {barBeat(beat)}</HelpHeading>
            <table className="d-who">
              <tbody>
                {drivers.map((d) => (
                  <tr key={d.lane}>
                    <th>{d.lane === "color" ? "colour" : d.lane}</th>
                    <td>{d.source === "clip" && d.item
                      ? <><b>{itemName(d.item)}</b> <span className="muted">· {d.row}</span></>
                      : d.source === "blank"
                        ? <span className="muted">rest ({d.row} owns it)</span>
                        : <span className="muted">template set / show</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
          <Editor.Shelf history={history} routines={routines} beat={beat} track={track} />
        </aside>}
        {guide.drawer}
      </div>

      {waveRow && !history.listView
        ? <Editor.WaveInspector row={waveRow} history={history} onSelect={setSelected} />
        : selectedPoint && pointRow && !history.listView
        ? <Editor.PointInspector row={pointRow} beat={selectedPoint.beat} history={history}
                                 onSelect={setSelected} />
        : <Editor.Inspector history={history} item={selectedItem} routines={routines}
                            engine={engine} onDeleted={() => setSelected(null)}
                            beat={beat} onSelect={setSelected} />}

      {menu && menuItem && (
        <div className="d-ctx" role="menu" aria-label={`${itemName(menuItem.item)} actions`}
             style={{ left: Math.min(menu.x, innerWidth - 240), top: Math.min(menu.y, innerHeight - 300) }}>
          {[
            ["Copy", "Ctrl+C", () => { clipOps.copy(history, menu.id, "timeline"); }],
            ["Cut", "Ctrl+X", () => { clipOps.cut(history, menu.id, "timeline"); setSelected(null); }],
            ...(board?.kind === "timeline"
              ? [["Paste at the playhead", "Ctrl+V",
                  () => setSelected(clipOps.paste(history, "timeline", Math.max(0, history.snapBeat(beat))))] as const]
              : []),
            ["Duplicate after it", "Ctrl+D",
             () => setSelected(clipOps.duplicate(history, menu.id, "timeline"))],
          ].map(([label, key, run]) => (
            <button key={label as string} role="menuitem"
                    onClick={() => { (run as () => void)(); setMenu(null); }}>
              {label as string}<span className="k">{key as string}</span></button>
          ))}
          <button role="menuitem" disabled={!clipOps.inside(doc, menu.id, history.snapBeat(beat))}
                  onClick={() => { setSelected(clipOps.split(history, menu.id, history.snapBeat(beat))); setMenu(null); }}>
            Split at the playhead<span className="k">S</span></button>
          {menuItem.item.kind === "routine" && menuItem.item.routine && (
            <a role="menuitem" href={`#studio/routine/${menuItem.item.routine}`}
               onClick={() => rememberBack()}>Open the routine</a>)}
          <button role="menuitem" className="danger"
                  onClick={() => {
                    const id = menu.id;
                    history.apply((d) => { for (const r of d.rows) if (r.items) r.items = r.items.filter((i) => i.id !== id); });
                    setSelected(null);
                    setMenu(null);
                  }}>Delete<span className="k">Del</span></button>
        </div>
      )}
    </div>
    </ParamLanes.Provider>
  );
}

