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
  RoutineSummary, TemplateSetDoc, TimelineDoc, TrackDoc, Wave,
} from "./model";
import { Editor, parsePointId, useEditorKeys, useHistory } from "./edit";
import { Lane, Phrases, Ruler, WaveLane } from "./lanes";
import RoutineEditor from "./RoutineEditor";
import { useDesignerGuide } from "./guide";
import Studio from "./Studio";
import { takePendingDraft } from "./Library";
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
    const pending = takePendingDraft(trackId);
    if (!pending) return;
    const base = doc;
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

  useEditorKeys({ history, selected, setSelected,
                  playPause: () => transport.play(!transport.playing) });

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
  const match = engine.state?.track?.match;
  const live = engine.state?.track;

  return (
    <div className="designer" data-chunk={DESIGNER_CHUNK}>
      <header className="d-top">
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

      <div className={`d-body${guide.open ? " d-with-guide" : ""}${panels.edit ? "" : " d-no-side"}`}>
        <div className="d-lanes" role="region" aria-label="lanes" ref={lanesRef}>
          <div className="d-scroll" style={{ width: width + HEADER_W }}>
            <Ruler totalBeats={totalBeats} x={x} width={width} onSeek={seekBeat} />
            <Phrases track={track} x={x} width={width} />
            <WaveLane wave={wave} grid={grid} duration={duration} x={x} width={width} />
            {doc.rows.map((row, index) => (
              <Lane key={row.id} row={row} index={index} x={x} width={width}
                    zoom={zoom} selected={selected} onSelect={setSelected}
                    history={history} beat={beat} />
            ))}
            <Editor.AddLane history={history} />
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
                nothing drives it. <b>template / show</b> means no lane has anything
                here.</p>
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
                        : <span className="muted">template / show</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
          <Editor.Shelf history={history} routines={routines} beat={beat} track={track} />
        </aside>}
        {guide.drawer}
      </div>

      {selectedPoint && pointRow && !history.listView
        ? <Editor.PointInspector row={pointRow} beat={selectedPoint.beat} history={history}
                                 onSelect={setSelected} />
        : <Editor.Inspector history={history} item={selectedItem} routines={routines}
                            engine={engine} onDeleted={() => setSelected(null)} />}
    </div>
  );
}

