import { useEffect, useState } from "react";
import { apiFetch } from "../useEngine";
import { SideSection } from "./detail";
import type { History } from "./history";
import { ROLES, newHit } from "./items";
import type { HitKind } from "./items";
import { NEW_COLORS, draftFromTemplate, hexColor, uniqueId } from "./model";
import type { Item, PaletteSummary, RoutineSummary, TemplateSetDoc, TrackDoc } from "./model";

/**
 * The shelf: routines to drag onto a lane, template sets to draft from,
 * pads to record onto, and the show's palettes.
 */
export function Shelf({ history, routines, beat, track }: {
  history: History; routines: RoutineSummary[]; beat: number; track?: TrackDoc | null;
}) {
  const doc = history.doc;
  if (!doc) return null;
  // The routines to place are in the browser, on the left; this keeps what
  // works on the whole track: its palettes, then the tools that write lanes.
  void routines;
  return (
    <>
      <Palettes history={history} />
      <Templates history={history} track={track ?? null} />
      <RecordPads history={history} beat={beat} />
    </>
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
    <SideSection id="timeline-draft" title="Draft from template" help={<>
      <p>Fills the scene lane with one routine per rekordbox phrase. It looks
        for the exact label (Verse 2), then the family (Verse), then{" "}
        <code>*</code>. If the set has palettes, it fills the palette lane
        too, and if its picks have visuals, the visuals lane.</p>
      <p>It replaces what's there, but it's only a starting point. Undo puts the
        lanes back.</p>
    </>}>
      <div className="d-templates">
        {sets.map((s) => (
          <button key={s.id} onClick={() => void draft(s.id)}
                  title="Replace the scene lane with this template's routines, phrase by phrase">
            {s.name ?? s.id}</button>))}
      </div>
      {error && <p className="d-error small">{error}</p>}
    </SideSection>
  );
}

/** Record pads: tap along while the track plays, and each tap lands as an
 *  item at the snapped playhead. */
function RecordPads({ history, beat }: { history: History; beat: number }) {
  const add = (hit: HitKind) => history.apply((d) => {
    let lane = d.rows.find((r) => r.type === "hits");
    if (!lane) {
      lane = { id: uniqueId(d, "hits"), type: "hits", items: [] };
      d.rows.push(lane);
    }
    const at = Math.max(0, Math.round(beat));
    (lane.items ??= []).push({ id: uniqueId(d, `${hit}-${at}`), at, ...newHit(hit) });
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
    <SideSection id="timeline-record" title="Record pads" topic="Record" help={<>
      <p>Arm it and play the track. Each pad lands at the playhead:{" "}
        <b>Flash</b>, <b>Strobe</b> and <b>Blackout</b> as hits, and{" "}
        <b>Next scene</b> splits the scene clip there.</p>
    </>} aside={
      <button className={`small${history.recording ? " on" : ""}`}
              onClick={() => history.setRecording(!history.recording)}>
        {history.recording ? "● Recording" : "Record"}</button>
    }>
      {history.recording ? (
        <div className="d-pads">
          <button onClick={() => add("flash")}>Flash</button>
          <button onClick={() => add("strobe")}>Strobe</button>
          <button onClick={() => add("blackout")}>Blackout</button>
          <button onClick={nextScene}>Next scene</button>
        </div>
      ) : <span className="small muted">Tap hits in while the track plays.</span>}
    </SideSection>
  );
}

/** Where a document's palette came from, said beside it: a copy of the
 *  library's (and whether it still matches), or one that lives only here.
 *  Editing it here never changes the library -- this says so, in place. */
export function PaletteOrigin({ library, name, colors, here, onUseLibrary }: {
  library: PaletteSummary[]; name: string; colors: Record<string, unknown>;
  /** "track" or "set": whose copy this is. */
  here: string;
  onUseLibrary: (colors: { primary: string; secondary: string; accent: string }) => void;
}) {
  const lib = library.find((p) => p.name === name);
  if (!lib) {
    return <span className="d-origin" title={`Made in this ${here}: no library palette is called ${name}`}>
      only here</span>;
  }
  const same = ROLES.every((r) => hexColor(colors[r]) != null
    && hexColor(colors[r]) === hexColor(lib[r]));
  const tip = `A copy of the library's ${name}. Changing it here changes this ${here} only; `
    + "the library's is changed on Studio's Palettes page.";
  return same ? <span className="d-origin lib" title={tip}>copy of library</span> : (
    <span className="d-origin differs" title={tip}>differs from library
      <button className="small" aria-label={`use the library's colors for ${name}`}
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
    <SideSection id="timeline-palettes" title="This track's palettes" topic="Palettes"
                 count={Object.keys(palettes).length} help={<>
      <p>Each palette has three colors: primary, secondary and accent.
        Routines use these roles instead of fixed colors, so they change with
        the palette.</p>
      <p>The selected palette plays wherever the palette lane is empty. A
        palette clip switches it for its length.</p>
      <p>These are this track's own copies. Changing a color here changes
        this track only; the library's palettes are on Studio's Palettes
        page, which can bring copies up to date.</p>
    </>} aside={<a className="small d-link" href="#studio/palettes"
                   title="The show's palette library: changing a color here changes this track only">
      Library</a>}>
      {Object.entries(palettes).map(([name, pal]) => (
        <div key={name} className="d-palette">
          <label className="small">
            <input type="radio" name="default-palette" checked={doc.palette === name}
                   onChange={() => history.apply((d) => { d.palette = name; })} />
            {name}
          </label>
          <PaletteOrigin library={library} name={name} colors={pal as Record<string, unknown>}
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
      <div className="d-form">
        <FromLibrary library={library} has={Object.keys(palettes)}
                     onPick={(name, colors) => history.apply((d) => {
                       d.palettes = { ...(d.palettes ?? {}), [name]: colors };
                       if (!d.palette) d.palette = name;
                     })} />
        <button className="small" onClick={() => history.apply((d) => {
          const name = `Palette ${Object.keys(d.palettes ?? {}).length + 1}`;
          d.palettes = { ...(d.palettes ?? {}),
                         [name]: { ...NEW_COLORS } };
          if (!d.palette) d.palette = name;
        })}>+ palette</button>
      </div>
    </SideSection>
  );
}

/** A select of library palettes, for an editor that keeps its own copies: the
 *  picked one is copied in under its library name. */
export function FromLibrary({ library, has, onPick }: {
  library: PaletteSummary[]; has: string[];
  onPick: (name: string, colors: { primary: string; secondary: string; accent: string }) => void;
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
