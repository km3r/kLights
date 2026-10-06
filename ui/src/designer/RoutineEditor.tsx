import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, apiFetch } from "../useEngine";
import type { Engine } from "./Designer";
import {
  BEATS_PER_BAR, BLOCK_ARGS, BLOCK_SLOT, DESIGNER_CHUNK, PARAM_TYPES,
  RIG_BOUND, SLOTS, barBeat, blocksFor, findItem, itemName,
} from "./model";
import type { ArgSpec, Item, ParamDef, RoutineDoc, Row, Slot } from "./model";
import {
  AutomationMenu, EditTags, Editor, FADES, ParamLanes, PickMenu, ROLES, Reach, backHash,
  externalRow, offeredLooks, parsePointId, parseWaveId, rigTags, routineLaneSpecs, snapDown,
  uniqueId,
  useEditorKeys, useHistory, useMenuDismiss,
} from "./edit";
import type { History, LaneSpecs, PickEntry } from "./edit";
import { Lane, Ruler } from "./lanes";
import { useDesignerGuide } from "./guide";
import { PanelToggle, usePanels } from "./panels";
import { clearPending, peekPending } from "./pending";
import "./designer.css";

/**
 * The routine editor (F19l, part 3): one routine's rows in its own beats, with
 * the same lanes as a track's timeline -- in loop mode, since a routine has no
 * audio and repeats every `bars`.
 *
 *   top        a loop transport at a tempo of your choosing, the routine's
 *              name, and the same snap / undo / redo / check / save
 *   lanes      a bar ruler over the routine's length, then its rows: a clips
 *              row per slot and role, hits, automation. A click on a clips or
 *              hits lane's empty space offers what that lane can hold
 *   right      the routine itself: length and looping, its roles, its open
 *              parameters, its variations, and the blocks to add
 *   bottom     the selected block's arguments -- each a value, or "$param"
 *
 * A routine binds to ROLES, not fixtures, so nothing here needs a rig except the
 * check: `routine_draft` asks the engine what will not work on THIS rig. To see
 * it on the rig, place it on a track's timeline and drive the rig from there.
 */

type Doc = RoutineDoc;
type RHistory = History<Doc>;

const HEADER_W = 170;
const ZOOMS = [8, 12, 16, 24, 32, 48];
const NAME_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const LANE_NAMES: Record<string, string> = { movement: "Movement", color: "Colour", level: "Level" };

export function newRoutine(id: string): Doc {
  return { kind: "klights.routine", version: 1, id, name: id, bars: 4, loop: true,
           roles: { movers: { default: "movers" } }, params: {}, variations: {}, rows: [] };
}

/** The playhead going round the routine at a tempo picked here. */
function useLoop(length: number, bpm: number) {
  const [beat, setBeat] = useState(0);
  const [playing, setPlaying] = useState(false);
  const anchor = useRef({ beat: 0, at: 0 });
  const beatRef = useRef(0);
  beatRef.current = beat;

  useEffect(() => {
    if (!playing || length <= 0) return;
    anchor.current = { beat: beatRef.current, at: performance.now() };
    let frame = 0;
    const tick = () => {
      const b = anchor.current.beat + (performance.now() - anchor.current.at) / 1000 * bpm / 60;
      setBeat(((b % length) + length) % length);
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [playing, length, bpm]);

  const seek = useCallback((b: number) => {
    const clamped = Math.max(0, Math.min(length, b));
    setBeat(clamped);
    anchor.current = { beat: clamped, at: performance.now() };
  }, [length]);

  return { beat, playing, setPlaying, seek };
}

export default function RoutineEditor({ engine, routineId }: { engine: Engine; routineId: string }) {
  const history = useHistory<Doc>();
  const { doc, setBase, setSnap } = history;
  const [rev, setRev] = useState("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [zoom, setZoom] = useState(16);
  const [bpm, setBpm] = useState(128);
  const [selected, setSelected] = useState<string | null>(null);
  // The menu of what a lane can hold, open where its empty space was clicked.
  const [adding, setAdding] = useState<Adding | null>(null);
  const [back] = useState(backHash);
  const guide = useDesignerGuide("routines");
  const [panels, togglePanel] = usePanels();
  // A start handed over by New: a copy, a look wrapped, a blank with settings.
  const [pending] = useState(() => peekPending("routine", routineId));

  useEffect(() => {
    setSnap("beat");
    apiFetch<{ doc: Doc; rev: string }>(`/api/routines/${routineId}`)
      .then((r) => { setBase(r.doc); setRev(r.rev); })
      .catch((e: Error) => {
        // Not in the folder: start it. Saved with base_rev "" -- a new file,
        // refused if one appeared meanwhile (or is there but unreadable).
        if (!(e instanceof ApiError && e.status === 404)) { setLoadError(e.message); return; }
        setBase(pending?.doc ?? newRoutine(routineId));
        setRev("");
        clearPending("routine", routineId);
      });
  }, [routineId, setBase, setSnap]);

  const length = (doc?.bars ?? 4) * BEATS_PER_BAR;
  const loop = useLoop(length, bpm);
  const beatRef = useRef(0);
  beatRef.current = loop.beat;
  useEditorKeys({ history, selected, setSelected,
                  playPause: () => loop.setPlaying(!loop.playing),
                  clip: { kind: "routine", beat: () => beatRef.current } });
  useMenuDismiss(!!adding, () => setAdding(null));
  const totalBeats = useMemo(() => {
    const ends = (doc?.rows ?? []).flatMap((r) => (r.items ?? []).map((i) => i.at + i.len));
    return Math.ceil(Math.max(length, ...ends) / BEATS_PER_BAR) * BEATS_PER_BAR;
  }, [doc, length]);

  if (loadError) {
    return (
      <div className="designer" data-chunk={DESIGNER_CHUNK}>
        <header className="d-top"><a className="d-link" href="#studio/routines">Studio</a></header>
        <p className="d-error">{loadError}</p>
      </div>
    );
  }
  if (!doc) {
    return <div className="designer" data-chunk={DESIGNER_CHUNK}>
      <p className="muted" style={{ padding: 16 }}>Loading {routineId}…</p></div>;
  }

  const x = (b: number) => b * zoom;
  const width = totalBeats * zoom;
  const roles = Object.keys(doc.roles);
  const rigBound = usesRig(doc);
  const selectedPoint = parsePointId(selected);
  const pointRow = selectedPoint ? doc.rows.find((r) => r.id === selectedPoint.row) : undefined;
  const waveRow = doc.rows.find((r) => r.id === parseWaveId(selected) && r.wave);
  const paramLanes = routineLaneSpecs(doc);

  return (
    <ParamLanes.Provider value={paramLanes}>
    <div className="designer" data-chunk={DESIGNER_CHUNK}>
      <header className="d-top">
        <a className="d-link" href={back}
           title={back === "#studio/routines" ? "Back to Studio's routines"
             : `Back to ${back.slice("#studio/track/".length)}`}>◂</a>
        <button className={loop.playing ? "on" : ""} onClick={() => loop.setPlaying(!loop.playing)}>
          {loop.playing ? "Stop" : "Play"}</button>
        <span className="mono" aria-label="position">bar {barBeat(loop.beat)} of {doc.bars}</span>
        <label className="small muted">Tempo{" "}
          <input type="number" min={40} max={220} value={bpm} aria-label="tempo"
                 style={{ width: 60 }}
                 onChange={(e) => { const v = Number(e.target.value); if (v >= 40 && v <= 220) setBpm(v); }} />
          {" "}bpm</label>
        <span className="d-title">
          <b>{doc.name || doc.id}</b>
          <span className="muted small"> · routine {doc.id}</span>
          {rev === "" && <span className="d-badge">new</span>}
          {rigBound && <span className="d-badge">this rig only</span>}
        </span>
        <span className="grow" />
        <label className="small muted">Zoom{" "}
          <select value={zoom} onChange={(e) => setZoom(Number(e.target.value))} aria-label="zoom">
            {ZOOMS.map((z) => <option key={z} value={z}>{z * 4} px/bar</option>)}
          </select>
        </label>
        <Editor.Toolbar history={history} rev={rev} setRev={setRev} engine={engine}
                        kind="routine" ident={routineId} />
        {guide.button}
        <PanelToggle open={panels.edit} side="right" label="side panel"
                     onToggle={() => togglePanel("edit")} />
      </header>
      {guide.banner}

      <div className={`d-body${guide.open ? " d-with-guide" : ""}${panels.edit ? "" : " d-no-side"}`}>
        <div className="d-lanes" role="region" aria-label="lanes">
          <div className="d-scroll" style={{ width: width + HEADER_W }}>
            <Ruler totalBeats={totalBeats} x={x} width={width} onSeek={loop.seek} />
            {doc.rows.map((row, index) => (
              <Lane key={row.id} row={row} index={index} x={x} width={width} zoom={zoom}
                    selected={selected} onSelect={setSelected} history={history}
                    beat={loop.beat} roles={roles}
                    onAdd={(rowId, at, cx, cy) => setAdding({ row: rowId, at, x: cx, y: cy })} />
            ))}
            <AddLane history={history} roles={roles} params={paramLanes} />
            {doc.rows.length === 0 && (
              <p className="small muted" style={{ paddingLeft: HEADER_W + 8 }}>
                Empty. Add a lane from “+ lane” and click in it to add a block, or
                pick a block on the right.</p>)}
            <div className="d-loop-end" aria-hidden="true" style={{ left: HEADER_W + x(length) }}
                 title={doc.loop !== false ? "it repeats from here" : "it holds its end from here"} />
            <div className="d-playhead" aria-hidden="true" style={{ left: HEADER_W + x(loop.beat) }} />
          </div>
        </div>

        {panels.edit && <aside className="d-side" aria-label="side panel">
          <Settings history={history} doc={doc} engine={engine} rigBound={rigBound} />
          <Roles history={history} doc={doc} engine={engine} />
          <Params history={history} doc={doc} engine={engine} />
          <Variations history={history} doc={doc} engine={engine} />
          <Blocks history={history} doc={doc} engine={engine} beat={loop.beat}
                  selected={selected} onAdded={setSelected} />
        </aside>}
        {guide.drawer}
      </div>

      {history.listView
        ? <Editor.EventList history={history} />
        : waveRow
          ? <Editor.WaveInspector row={waveRow} history={history} onSelect={setSelected} />
        : selectedPoint && pointRow
          ? <Editor.PointInspector row={pointRow} beat={selectedPoint.beat} history={history}
                                   onSelect={setSelected} />
          : <BlockInspector history={history} doc={doc} engine={engine} selected={selected}
                            onDeleted={() => setSelected(null)} />}

      {adding && (
        <AddMenu history={history} doc={doc} engine={engine} adding={adding}
                 playhead={loop.beat}
                 onAdded={(id) => { setSelected(id); setAdding(null); }} />)}
    </div>
    </ParamLanes.Provider>
  );
}

function usesRig(doc: Doc): boolean {
  return doc.rows.some((r) => (r.items ?? []).some((i) => RIG_BOUND.includes(i.block ?? "")));
}

// -- lanes ------------------------------------------------------------------------

/** Where a lane's empty space was clicked: its row, the beat (null for the
 *  playhead) and the point on the screen to open the menu at. */
interface Adding { row: string; at: number | null; x: number; y: number }

const HITS = [
  { hit: "flash", text: "a burst, decaying" },
  { hit: "strobe", text: "for a bar" },
  { hit: "blackout", text: "a beat of dark" },
] as const;

/** A hit's starting length and envelope, as the track's browser places them. */
function newHit(hit: (typeof HITS)[number]["hit"]): Pick<Item, "hit" | "len" | "envelope"> {
  return { hit, len: hit === "strobe" ? 4 : hit === "flash" ? 2 : 1,
           ...(hit === "flash" ? { envelope: "decay" as const } : {}) };
}

/**
 * What a lane can hold, offered where its empty space was clicked: a clips
 * lane's blocks (its slot's, then the rig's own), a hits lane's hits. The one
 * picked goes on THAT lane -- the shelf on the right can only reach the first
 * lane of a slot -- from the beat clicked until the next item on the lane, or
 * the routine's end, and is selected so its arguments open below.
 */
function AddMenu({ history, doc, engine, adding, playhead, onAdded }: PanelProps & {
  adding: Adding; playhead: number; onAdded: (id: string) => void;
}) {
  const row = doc.rows.find((r) => r.id === adding.row);
  if (!row) return null;
  const length = doc.bars * BEATS_PER_BAR;
  const at = adding.at == null ? history.snapBeat(playhead) : snapDown(history, adding.at);
  const start = Math.max(0, Math.min(length - 1, at));
  const next = Math.min(length, ...(row.items ?? []).map((i) => i.at).filter((b) => b > start));
  const until = Math.max(1, next - start);
  const place = (id: string, change: (d: Doc, lane: Row) => void) => {
    history.apply((d) => {
      const lane = d.rows.find((r) => r.id === row.id);
      if (lane) change(d, lane);
    });
    onAdded(id);
  };
  const blocks = row.type === "hits" ? [] : blocksFor(row.target ?? "");
  const slotName = LANE_NAMES[row.target ?? ""] ?? row.target ?? "";
  const entries: PickEntry[] = row.type === "hits"
    ? HITS.map(({ hit, text }) => ({
        key: hit,
        label: <>{hit}<span className="k">{text}</span></>,
        add: () => {
          // Named from the document as it is now: the edit may run later.
          const id = uniqueId(doc, hit);
          place(id, (_, lane) => { (lane.items ??= []).push({ id, at: start, ...newHit(hit) }); });
        },
      }))
    : [...blocks.filter((b) => !RIG_BOUND.includes(b)), ...blocks.filter((b) => RIG_BOUND.includes(b))]
        .map((block) => ({
          key: block,
          group: RIG_BOUND.includes(block) ? "This rig only" : slotName,
          label: <>{block}{RIG_BOUND.includes(block) && <span className="d-badge">rig</span>}</>,
          add: () => {
            const id = uniqueId(doc, block);
            place(id, (d, lane) => addBlock(d, lane, { id, block, at: start, len: until },
                                            engine.state?.event));
          },
        }));
  return (
    <PickMenu label={`add to ${row.id}`} x={adding.x} y={adding.y} entries={entries}
              empty="Nothing goes on this lane."
              head={<>Add at bar {barBeat(start)}{row.role ? `, for ${row.role}` : ""}</>} />
  );
}

function AddLane({ history, roles, params }: {
  history: RHistory; roles: string[]; params: LaneSpecs;
}) {
  return (
    <div className="d-row d-add">
      <div className="d-head">
        <select aria-label="add lane" value=""
                onChange={(e) => {
                  const target = e.target.value;
                  if (!target) return;
                  history.apply((d) => {
                    // A routine can cue a VJ app too, wherever it plays: on a
                    // track, from a template, on a pad (milestone 3).
                    const external = externalRow(d, target);
                    if (external) { d.rows.push(external); return; }
                    const id = uniqueId(d, target);
                    d.rows.push(target === "hits" ? { id, type: "hits", items: [] }
                      : { id, type: "clips", target, role: roles[0] ?? "", items: [] });
                  });
                }}>
          <option value="">+ lane</option>
          {SLOTS.map((s) => <option key={s} value={s}>{LANE_NAMES[s]}</option>)}
          <option value="hits">Hits</option>
          <option value="osc">OSC cues</option>
          <option value="osc-curve">OSC curve</option>
          <option value="midi">MIDI cues</option>
          <option value="midi-curve">MIDI curve</option>
          <option value="visuals">Visuals</option>
        </select>
        <AutomationMenu history={history} params={params} />
      </div>
    </div>
  );
}

// -- the routine itself -----------------------------------------------------------

interface PanelProps { history: RHistory; doc: Doc; engine: Engine }

function Settings({ history, doc, engine, rigBound }: PanelProps & { rigBound: boolean }) {
  const loops = doc.loop !== false;
  const event = engine.state?.event;
  // The folders the library already has, to file this one with the others.
  const [folders, setFolders] = useState<string[]>([]);
  useEffect(() => {
    apiFetch<{ routines: { folder?: string | null }[] }>("/api/routines")
      .then((r) => setFolders([...new Set(r.routines.map((x) => x.folder)
        .filter((f): f is string => !!f))].sort()))
      .catch(() => setFolders([]));
  }, []);
  const folder = typeof doc.folder === "string" ? doc.folder : "";
  return (
    <section>
      <h3>Routine</h3>
      <div className="d-form">
        <label className="small">Name{" "}
          <input value={doc.name ?? ""} aria-label="routine name"
                 onChange={(e) => { const v = e.target.value; history.apply((d) => { d.name = v; }); }} />
        </label>
        <label className="small">Bars{" "}
          <input type="number" min={0.25} max={256} step={1} value={doc.bars} aria-label="bars"
                 style={{ width: 64 }}
                 onChange={(e) => {
                   const v = Number(e.target.value);
                   if (v >= 0.25 && v <= 256) history.apply((d) => { d.bars = v; });
                 }} />
        </label>
        <label className="small">
          <input type="checkbox" checked={loops} aria-label="loops"
                 onChange={() => history.apply((d) => { d.loop = !loops; })} /> loops
        </label>
        <label className="small" title="Where Studio's library files it. Nothing about how it plays">
          Folder{" "}
          <input value={folder} list="d-folders" aria-label="folder" placeholder="unfiled"
                 style={{ width: 110 }}
                 onChange={(e) => {
                   const v = e.target.value;
                   history.apply((d) => { if (v.trim()) d.folder = v; else delete d.folder; });
                 }} />
          <datalist id="d-folders">{folders.map((f) => <option key={f} value={f} />)}</datalist>
        </label>
      </div>
      <p className="small muted">
        {loops ? `Repeats every ${doc.bars} bar${doc.bars === 1 ? "" : "s"} for as long as its clip lasts.`
          : "Plays once; past its end it keeps running its last moment."}</p>
      {(rigBound || doc.rig) && (
        <div className="d-form">
          <label className="small">Rig{" "}
            <input value={doc.rig ?? ""} aria-label="rig" placeholder="the event it was built for"
                   onChange={(e) => {
                     const v = e.target.value;
                     history.apply((d) => { if (v) d.rig = v; else delete d.rig; });
                   }} />
          </label>
          {event && doc.rig !== event && (
            <button className="small" onClick={() => history.apply((d) => { d.rig = event; })}>
              This rig ({event})</button>)}
          <p className="small muted">It uses looks or presets, which exist on one rig only.</p>
        </div>
      )}
    </section>
  );
}

/** A small "name it and add it" field: ids that are also keys in the file. */
function NameAdder({ label, taken, onAdd }: {
  label: string; taken: string[]; onAdd: (name: string) => void;
}) {
  const [name, setName] = useState("");
  const clean = name.trim();
  const problem = !clean ? null : !NAME_RE.test(clean)
    ? "lower-case letters, digits, - and _" : taken.includes(clean) ? "already there" : null;
  return (
    <form className="d-form" onSubmit={(e) => {
      e.preventDefault();
      if (!clean || problem) return;
      onAdd(clean);
      setName("");
    }}>
      <input value={name} onChange={(e) => setName(e.target.value)} aria-label={`${label} name`}
             placeholder="name" style={{ width: 110 }} />
      <button type="submit" disabled={!clean || !!problem}>{label}</button>
      {problem && <span className="small d-error">{problem}</span>}
    </form>
  );
}

function Roles({ history, doc, engine }: PanelProps) {
  const tags = rigTags(engine.state);
  const fixtures = (engine.state?.fixtures ?? []).map((f) => f.name).filter((n) => !tags.includes(n));
  const inUse = (name: string) => doc.rows.some((r) => r.role === name
    || (r.items ?? []).some((i) => i.role === name));
  const names = Object.keys(doc.roles);
  return (
    <section>
      <h3>Roles</h3>
      <p className="small muted">Rows play on roles. Each plays on the fixtures with its tag
        (or one fixture, by name), unless the clip that uses the routine binds it to another.</p>
      {Object.entries(doc.roles).map(([name, spec]) => (
        <div key={name} className="d-palette">
          <span className="grow d-role-name"><span className="mono small">{name}</span>
            <Reach state={engine.state} tag={spec.default} optional={spec.optional} /></span>
          <input list="d-tags" value={spec.default} aria-label={`${name} tag`} style={{ width: 100 }}
                 onChange={(e) => {
                   const v = e.target.value;
                   history.apply((d) => { d.roles[name]!.default = v; });
                 }} />
          <label className="small" title="Nothing is wrong when no fixture carries the tag">
            <input type="checkbox" checked={!!spec.optional} aria-label={`${name} optional`}
                   onChange={() => history.apply((d) => {
                     const r = d.roles[name]!;
                     if (r.optional) delete r.optional; else r.optional = true;
                   })} /> optional</label>
          <button className="small" aria-label={`remove role ${name}`}
                  disabled={names.length < 2 || inUse(name)}
                  title={inUse(name) ? "A row uses it" : names.length < 2 ? "A routine needs a role" : ""}
                  onClick={() => history.apply((d) => { delete d.roles[name]; })}>×</button>
        </div>
      ))}
      <datalist id="d-tags">
        {tags.map((t) => <option key={t} value={t} />)}
        {fixtures.map((n) => <option key={n} value={n} label="one fixture" />)}
      </datalist>
      <p className="small muted">Which fixtures carry a tag is set on the console's Setup
        tab. <EditTags /></p>
      <NameAdder label="+ role" taken={names}
                 onAdd={(name) => history.apply((d) => { d.roles[name] = { default: name }; })} />
    </section>
  );
}

const PARAM_DEFAULTS: Record<ParamDef["type"], ParamDef> = {
  color: { type: "color", default: "@primary" },
  number: { type: "number", default: 1, min: 0, max: 10 },
  rate: { type: "rate", default: 1, min: 0, max: 8 },
  look: { type: "look" },
};

/** One value of a parameter: a palette role or a colour, a number, a look. */
function ParamValue({ name, def, value, onChange, engine }: {
  name: string; def: ParamDef; value: unknown; onChange: (v: unknown) => void; engine: Engine;
}) {
  if (def.type === "color") {
    return <ColorValue label={name} value={value} onChange={onChange} params={[]} />;
  }
  if (def.type === "look") {
    const looks = offeredLooks(engine.state?.looks).map((l) => l.name);
    return (
      <>
        <input list="d-looks" value={typeof value === "string" ? value : ""} aria-label={name}
               onChange={(e) => onChange(e.target.value || undefined)} />
        <datalist id="d-looks">{looks.map((l) => <option key={l} value={l} />)}</datalist>
      </>
    );
  }
  return (
    <input type="number" min={def.min} max={def.max} step={def.type === "rate" ? 0.25 : "any"}
           value={typeof value === "number" ? value : ""} aria-label={name} style={{ width: 70 }}
           onChange={(e) => onChange(e.target.value === "" ? undefined : Number(e.target.value))} />
  );
}

function Params({ history, doc, engine }: PanelProps) {
  const params = doc.params ?? {};
  const used = (name: string) => doc.rows.some((r) => (r.items ?? []).some(
    (i) => JSON.stringify(i.args ?? {}).includes(`"$${name}"`)));
  return (
    <section>
      <h3>Open parameters</h3>
      <p className="small muted">A block argument set to <span className="mono">$name</span> takes
        the value from the clip that uses the routine, or its variation, or this default.</p>
      {Object.entries(params).map(([name, def]) => (
        <div key={name} className="d-param">
          <div className="d-palette">
            <span className="mono small grow">${name}</span>
            <select value={def.type} aria-label={`${name} type`}
                    onChange={(e) => {
                      const type = e.target.value as ParamDef["type"];
                      history.apply((d) => {
                        d.params![name] = { ...PARAM_DEFAULTS[type] };
                        for (const v of Object.values(d.variations ?? {})) delete v[name];
                      });
                    }}>
              {PARAM_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
            <button className="small" aria-label={`remove param ${name}`} disabled={used(name)}
                    title={used(name) ? "A block uses it" : ""}
                    onClick={() => history.apply((d) => {
                      delete d.params![name];
                      for (const v of Object.values(d.variations ?? {})) delete v[name];
                    })}>×</button>
          </div>
          <div className="d-form">
            <span className="small muted">default</span>
            <ParamValue name={`${name} default`} def={def} value={def.default} engine={engine}
                        onChange={(v) => history.apply((d) => {
                          const p = d.params![name]!;
                          if (v === undefined) delete p.default; else p.default = v;
                        })} />
            {(def.type === "number" || def.type === "rate") && (["min", "max"] as const).map((k) => (
              <label key={k} className="small muted">{k}{" "}
                <input type="number" value={def[k] ?? ""} aria-label={`${name} ${k}`} style={{ width: 56 }}
                       onChange={(e) => {
                         const v = e.target.value;
                         history.apply((d) => {
                           const p = d.params![name]!;
                           if (v === "") delete p[k]; else p[k] = Number(v);
                         });
                       }} />
              </label>
            ))}
          </div>
        </div>
      ))}
      <NameAdder label="+ parameter" taken={Object.keys(params)}
                 onAdd={(name) => history.apply((d) => {
                   d.params = { ...(d.params ?? {}), [name]: { ...PARAM_DEFAULTS.number } };
                 })} />
    </section>
  );
}

function Variations({ history, doc, engine }: PanelProps) {
  const variations = doc.variations ?? {};
  const params = doc.params ?? {};
  return (
    <section>
      <h3>Variations</h3>
      <p className="small muted">Named sets of parameter values, picked per clip.</p>
      {Object.entries(variations).map(([vname, values]) => (
        <div key={vname} className="d-param">
          <div className="d-palette">
            <b className="small grow">{vname}</b>
            <button className="small" aria-label={`remove variation ${vname}`}
                    onClick={() => history.apply((d) => { delete d.variations![vname]; })}>×</button>
          </div>
          {Object.entries(params).map(([pname, def]) => (
            <div key={pname} className="d-form">
              <label className="small">
                <input type="checkbox" checked={pname in values} aria-label={`${vname} sets ${pname}`}
                       onChange={() => history.apply((d) => {
                         const v = d.variations![vname]!;
                         if (pname in v) delete v[pname];
                         else v[pname] = def.default ?? PARAM_DEFAULTS[def.type].default ?? "";
                       })} /> ${pname}</label>
              {pname in values && (
                <ParamValue name={`${vname} ${pname}`} def={def} value={values[pname]} engine={engine}
                            onChange={(v) => history.apply((d) => {
                              const vals = d.variations![vname]!;
                              if (v === undefined) delete vals[pname]; else vals[pname] = v;
                            })} />)}
            </div>
          ))}
          {!Object.keys(params).length && <p className="small muted">No parameters to vary yet.</p>}
        </div>
      ))}
      <NameAdder label="+ variation" taken={Object.keys(variations)}
                 onAdd={(name) => history.apply((d) => {
                   d.variations = { ...(d.variations ?? {}), [name]: {} };
                 })} />
    </section>
  );
}

// -- blocks -------------------------------------------------------------------------

/** A block onto a lane, with its starting arguments. One that uses this rig's
 *  looks or presets makes the routine this rig's own, if it is not already. */
function addBlock(d: Doc, lane: Row, item: { id: string; block: string; at: number; len: number },
                  event: string | undefined): void {
  (lane.items ??= []).push({ ...item, args: startingArgs(item.block) });
  if (RIG_BOUND.includes(item.block) && !d.rig && event) d.rig = event;
}

/** What a new block starts with: the arguments the engine NEEDS (colours,
 *  points); everything else is left to the engine's default. */
function startingArgs(block: string): Record<string, unknown> {
  const args: Record<string, unknown> = {};
  for (const spec of BLOCK_ARGS[block] ?? []) {
    if (spec.kind === "color" || spec.kind === "colors" || spec.kind === "points") {
      args[spec.name] = structuredClone(spec.default);
    }
  }
  return args;
}

function Blocks({ history, doc, engine, beat, selected, onAdded }: PanelProps & {
  beat: number; selected: string | null; onAdded: (id: string) => void;
}) {
  const length = doc.bars * BEATS_PER_BAR;
  const selectedRow = findItem(doc, selected)?.row;
  const add = (block: string, slot: Slot | null) => {
    // Named from the document as it is now, not inside the edit: React may
    // run an update later than this click, and the new block is selected.
    const added = uniqueId(doc, block);
    history.apply((d) => {
      const target = slot ?? (selectedRow?.type === "clips" ? selectedRow.target as Slot : "color");
      let lane = selectedRow && selectedRow.type === "clips" && selectedRow.target === target
        ? d.rows.find((r) => r.id === selectedRow.id)
        : d.rows.find((r) => r.type === "clips" && r.target === target);
      if (!lane) {
        lane = { id: uniqueId(d, target), type: "clips", target,
                 role: Object.keys(d.roles)[0] ?? "", items: [] };
        d.rows.push(lane);
      }
      const at = Math.max(0, Math.min(length - 1, history.snapBeat(beat)));
      addBlock(d, lane, { id: added, block, at, len: Math.max(1, length - at) }, engine.state?.event);
    });
    onAdded(added);
  };
  return (
    <section>
      <h3>Blocks</h3>
      <p className="small muted">To put a block on a particular lane, click an empty spot on
        that lane. Or click one here to add it at the playhead, to the end of the routine,
        on the selected block's lane or the first lane of its slot.</p>
      {SLOTS.map((slot) => (
        <div key={slot}>
          <span className="small muted">{LANE_NAMES[slot]}</span>
          <div className="d-shelf">
            {blocksFor(slot).filter((b) => BLOCK_SLOT[b] === slot).map((b) => (
              <button key={b} onClick={() => add(b, slot)}>{b}</button>))}
          </div>
        </div>
      ))}
      <span className="small muted">This rig only</span>
      <div className="d-shelf">
        {RIG_BOUND.map((b) => (
          <button key={b} onClick={() => add(b, null)}
                  title="Uses this rig's own looks or presets: the routine becomes this-rig-only">
            {b}<span className="d-badge">rig</span></button>))}
      </div>
    </section>
  );
}

// -- the inspector -----------------------------------------------------------------

function BlockInspector({ history, doc, engine, selected, onDeleted }: PanelProps & {
  selected: string | null; onDeleted: () => void;
}) {
  const found = findItem(doc, selected);
  if (!found) {
    return (
      <footer className="d-inspector muted small">
        Select a block or hit to edit it. Drag to move, drag the right edge to change
        its length; positions snap to the {history.snap}. Beats count from the
        routine's start.
      </footer>
    );
  }
  const { row, item: it } = found;
  const roles = Object.keys(doc.roles);
  const params = doc.params ?? {};
  const set = (change: (target: Item) => void) => history.apply((d) => {
    const target = findItem(d, it.id)?.item;
    if (target) change(target);
  });
  const setArg = (name: string, value: unknown) => set((t) => {
    const args = { ...(t.args ?? {}) };
    if (value === undefined) delete args[name]; else args[name] = value;
    t.args = args;
  });
  const paramsOf = (...types: string[]) =>
    Object.entries(params).filter(([, p]) => types.includes(p.type)).map(([n]) => n);
  const remove = () => {
    history.apply((d) => {
      for (const r of d.rows) if (r.items) r.items = r.items.filter((i) => i.id !== it.id);
    });
    onDeleted();
  };

  if (row.type === "external") {
    return (
      <footer className="d-inspector" aria-label="inspector">
        <div className="d-insp-head">
          <b>{itemName(it)}</b>
          <span className="muted"> · {row.output} cue on {row.id}</span>
          <span className="muted mono"> · beat {it.at} → {it.at + it.len} ({it.len} beats)</span>
          <span className="grow" />
          <button onClick={remove}>Delete</button>
        </div>
        <div className="d-insp-grid">
          {row.output === "osc" &&
            <Editor.OscCue item={it} set={(fields) => set((t) => { Object.assign(t, fields); })} />}
          {row.output === "midi" &&
            <Editor.MidiCue item={it} set={(fields) => set((t) => { Object.assign(t, fields); })} />}
          {(row.output === "visuals" || row.output === "vj") &&
            <Editor.VisualCue item={it} set={(fields) => set((t) => { Object.assign(t, fields); })} />}
        </div>
      </footer>
    );
  }

  return (
    <footer className="d-inspector" aria-label="inspector">
      <div className="d-insp-head">
        <b>{it.hit ?? it.block}</b>
        <span className="muted"> · {it.hit ? "hit" : `${LANE_NAMES[row.target ?? ""] ?? row.target} block`}
          {" "}on {row.id}{row.role && !it.hit ? `, for ${it.role ?? row.role}` : ""}</span>
        {RIG_BOUND.includes(it.block ?? "") && <span className="d-badge">this rig only</span>}
        <span className="muted mono"> · beat {it.at} → {it.at + it.len} ({it.len} beats)</span>
        <span className="grow" />
        <button onClick={() => {
          history.apply((d) => {
            for (const r of d.rows) if (r.items) r.items = r.items.filter((i) => i.id !== it.id);
          });
          onDeleted();
        }}>Delete</button>
      </div>
      <div className="d-insp-grid">
        {it.hit ? (
          <>
            <div className="d-chips">
              {(["flash", "strobe", "blackout"] as const).map((h) => (
                <button key={h} className={it.hit === h ? "on" : ""}
                        onClick={() => set((t) => { t.hit = h; })}>{h}</button>))}
            </div>
            <label className="small">Level{" "}
              <input type="range" min={0} max={1} step={0.05} value={it.level ?? 1}
                     aria-label="hit level"
                     onChange={(e) => { const v = Number(e.target.value); set((t) => { t.level = v; }); }} />
            </label>
            <label className="small">Who{" "}
              <select value={it.role ?? ""} aria-label="hit role"
                      onChange={(e) => { const v = e.target.value; set((t) => { if (v) t.role = v; else delete t.role; }); }}>
                <option value="">every role</option>
                {roles.map((r) => <option key={r} value={r}>{r}</option>)}
              </select>
            </label>
            {it.hit === "flash" && (
              <button className={it.envelope === "decay" ? "on" : ""}
                      onClick={() => set((t) => { t.envelope = t.envelope === "decay" ? "hold" : "decay"; })}>
                {it.envelope === "decay" ? "decays" : "holds"}</button>)}
          </>
        ) : (
          <>
            <label className="small">Block{" "}
              <select value={it.block} aria-label="block"
                      onChange={(e) => {
                        const block = e.target.value;
                        set((t) => { t.block = block; t.args = startingArgs(block); });
                      }}>
                {blocksFor(row.target ?? "").map((b) => <option key={b} value={b}>{b}</option>)}
              </select>
            </label>
            <label className="small">Role{" "}
              <select value={it.role ?? ""} aria-label="block role"
                      onChange={(e) => { const v = e.target.value; set((t) => { if (v) t.role = v; else delete t.role; }); }}>
                <option value="">the lane's ({row.role})</option>
                {roles.map((r) => <option key={r} value={r}>{r}</option>)}
              </select>
            </label>
            <div>
              <span className="small muted">Fade in</span>
              <div className="d-chips">
                {FADES.filter((f) => f <= it.len).map((f) => (
                  <button key={f} className={(it.fade ?? 0) === f ? "on" : ""}
                          onClick={() => set((t) => { t.fade = f; })}>{f ? `${f}` : "cut"}</button>))}
              </div>
            </div>
            {(BLOCK_ARGS[it.block ?? ""] ?? []).map((spec) => (
              <ArgField key={spec.name} spec={spec} value={it.args?.[spec.name]} engine={engine}
                        params={spec.kind === "number" ? paramsOf("number", "rate")
                          : spec.kind === "color" || spec.kind === "colors" ? paramsOf("color")
                          : spec.kind === "look" ? paramsOf("look") : []}
                        onChange={(v) => setArg(spec.name, v)} />
            ))}
          </>
        )}
      </div>
    </footer>
  );
}

/** A colour: a palette role, a colour of its own, or one of the routine's
 *  colour parameters. */
function ColorValue({ label, value, onChange, params }: {
  label: string; value: unknown; onChange: (v: unknown) => void; params: string[];
}) {
  const role = typeof value === "string" && value.startsWith("@") ? value.slice(1) : null;
  return (
    <span className="d-chips" role="group" aria-label={label}>
      {ROLES.map((r) => (
        <button key={r} className={role === r ? "on" : ""} onClick={() => onChange(`@${r}`)}>{r}</button>))}
      <input type="color" aria-label={`${label} direct colour`}
             value={typeof value === "string" && value.startsWith("#") ? value : "#ffffff"}
             onChange={(e) => onChange(e.target.value)} />
      {params.map((p) => (
        <button key={p} className={value === `$${p}` ? "on" : ""} onClick={() => onChange(`$${p}`)}>
          ${p}</button>))}
    </span>
  );
}

function ArgField({ spec, value, params, onChange, engine }: {
  spec: ArgSpec; value: unknown; params: string[]; onChange: (v: unknown) => void; engine: Engine;
}) {
  const fromParam = typeof value === "string" && value.startsWith("$");
  const label = (
    <span className="small muted" title={spec.help}>
      {spec.name}{spec.unit ? ` (${spec.unit})` : ""}</span>
  );
  // Numbers and looks can come from a parameter; colours offer theirs inline.
  const source = params.length > 0 && (spec.kind === "number" || spec.kind === "look") ? (
    <select aria-label={`${spec.name} from`} value={fromParam ? String(value) : ""}
            onChange={(e) => onChange(e.target.value || undefined)}>
      <option value="">{spec.kind === "number" ? "a number" : "a look"}</option>
      {params.map((p) => <option key={p} value={`$${p}`}>${p}</option>)}
    </select>
  ) : null;

  let field: React.ReactNode = null;
  if (fromParam && spec.kind !== "color" && spec.kind !== "colors") {
    field = <span className="mono small">{String(value)}</span>;
  } else if (spec.kind === "number") {
    field = (
      <input type="number" step={spec.step ?? "any"} min={spec.min} max={spec.max} style={{ width: 70 }}
             value={typeof value === "number" ? value : ""}
             placeholder={spec.default === undefined ? "auto" : String(spec.default)}
             aria-label={spec.name}
             onChange={(e) => onChange(e.target.value === "" ? undefined : Number(e.target.value))} />
    );
  } else if (spec.kind === "bool") {
    field = <input type="checkbox" checked={value === true} aria-label={spec.name}
                   onChange={() => onChange(value === true ? undefined : true)} />;
  } else if (spec.kind === "choice") {
    // Every choice argument from its declaration -- a chase's order, an
    // easing, a spiral's direction -- rather than one hardcoded list each.
    const choices = spec.choices ?? [];
    field = (
      <select value={typeof value === "string" ? value : String(spec.default)} aria-label={spec.name}
              onChange={(e) => onChange(e.target.value === spec.default ? undefined : e.target.value)}>
        {choices.map((c) => <option key={c} value={c}>{c}</option>)}
      </select>
    );
  } else if (spec.kind === "color") {
    field = <ColorValue label={spec.name} value={value} onChange={onChange} params={params} />;
  } else if (spec.kind === "colors") {
    const list = Array.isArray(value) ? value : [];
    field = (
      <div>
        {list.map((c, i) => (
          <div key={i} className="d-form">
            <ColorValue label={`${spec.name} ${i + 1}`} value={c} params={params}
                        onChange={(v) => onChange(list.map((q, j) => (j === i ? v : q)))} />
            <button className="small" aria-label={`remove ${spec.name} ${i + 1}`} disabled={list.length < 2}
                    onClick={() => onChange(list.filter((_, j) => j !== i))}>×</button>
          </div>
        ))}
        <button className="small" onClick={() => onChange([...list, "@accent"])}>+ colour</button>
      </div>
    );
  } else if (spec.kind === "points") {
    const list = (Array.isArray(value) ? value : []) as number[][];
    field = (
      <div>
        {list.map((p, i) => (
          <div key={i} className="d-form">
            {["x", "y", "z"].map((axis, k) => (
              <input key={axis} type="number" min={0} max={1} step={0.05} style={{ width: 56 }}
                     value={p[k] ?? 0} aria-label={`point ${i + 1} ${axis}`}
                     onChange={(e) => {
                       const v = Number(e.target.value);
                       onChange(list.map((q, j) => (j === i ? q.map((c, m) => (m === k ? v : c)) : q)));
                     }} />))}
            <button className="small" aria-label={`remove point ${i + 1}`} disabled={list.length < 2}
                    onClick={() => onChange(list.filter((_, j) => j !== i))}>×</button>
          </div>
        ))}
        <button className="small" onClick={() => onChange([...list, [0.5, 0.5, 0]])}>+ point</button>
      </div>
    );
  } else if (spec.kind === "look") {
    const looks = offeredLooks(engine.state?.looks).map((l) => l.name);
    field = (
      <>
        <input list="d-looks" value={typeof value === "string" ? value : ""} aria-label={spec.name}
               onChange={(e) => onChange(e.target.value || undefined)} />
        <datalist id="d-looks">{looks.map((l) => <option key={l} value={l} />)}</datalist>
      </>
    );
  } else if (spec.kind === "preset") {
    field = (
      <select value={typeof value === "string" ? value : ""} aria-label={spec.name}
              onChange={(e) => onChange(e.target.value || undefined)}>
        <option value="">(none)</option>
        {engine.state?.presets.map((p) => <option key={p.name} value={p.name}>{p.name}</option>)}
      </select>
    );
  }
  return <div className="d-arg">{label} {field} {source}</div>;
}

