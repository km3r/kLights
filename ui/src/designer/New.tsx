import { useEffect, useMemo, useRef, useState } from "react";
import { apiFetch } from "../useEngine";
import type { Engine } from "./Designer";
import {
  BEATS_PER_BAR, ID_RE, NEW_COLOURS, freeId, phraseMatch, templateFromTimeline,
} from "./model";
import type {
  FoundPalette, PaletteDoc, PaletteSummary, RoutineDoc, RoutineSummary, TemplateSetDoc,
  TemplateSummary, TimelineDoc, TrackDoc, TrackLine,
} from "./model";
import { newRoutine } from "./RoutineEditor";
import { newTemplateSet } from "./Templates";
import { putPending } from "./pending";

/**
 * Studio's + New: one way in to making anything, from any library page -- a
 * timeline for a track, a routine, a template set, a palette -- each with
 * the starts that make sense for it.
 *
 * A timeline, a routine or a set opens in its editor UNSAVED, its start
 * applied as an edit there: nothing is written until Save, as with any new
 * file. A palette has no editor page of its own, so it is written to the
 * library at once, the same as the Palettes page's own New.
 */

export type NewKind = "timeline" | "routine" | "template" | "palette";

const KINDS: { kind: NewKind; title: string; text: string; key: string }[] = [
  { kind: "timeline", title: "A timeline for a track", text: "For a track already in the show", key: "t" },
  { kind: "routine", title: "A routine", text: "A few bars for roles, to use anywhere", key: "r" },
  { kind: "template", title: "A template set", text: "Phrase to routine, to draft timelines from", key: "s" },
  { kind: "palette", title: "A palette", text: "Into the library: primary, secondary, accent", key: "p" },
];

export function NewMenu({ onPick }: { onPick: (kind: NewKind) => void }) {
  const [open, setOpen] = useState(false);
  const first = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    if (!open) return;
    first.current?.focus();
    const close = (e: Event) => {
      if (e instanceof KeyboardEvent) {
        if (e.key === "Escape") { setOpen(false); return; }
        const k = KINDS.find((x) => x.key === e.key.toLowerCase());
        if (k && !e.ctrlKey && !e.metaKey && !e.altKey) { e.preventDefault(); setOpen(false); onPick(k.kind); }
        return;
      }
      if ((e.target as Element | null)?.closest?.(".s-new")) return;
      setOpen(false);
    };
    addEventListener("mousedown", close);
    addEventListener("keydown", close);
    return () => { removeEventListener("mousedown", close); removeEventListener("keydown", close); };
  }, [open, onPick]);
  return (
    <span className="s-new">
      <button className="d-primary" aria-haspopup="menu" aria-expanded={open}
              onClick={() => setOpen(!open)}>+ New</button>
      {open && (
        <span className="s-new-menu" role="menu" aria-label="New">
          {KINDS.map((k, i) => (
            <button key={k.kind} role="menuitem" ref={i === 0 ? first : undefined}
                    onClick={() => { setOpen(false); onPick(k.kind); }}>
              <span><b>{k.title}</b><span className="muted small">{k.text}</span></span>
              <span className="k" aria-hidden="true">{k.key.toUpperCase()}</span>
            </button>
          ))}
          <i aria-hidden="true" />
          <a role="menuitem" href="#studio/rekordbox" onClick={() => setOpen(false)}>
            <span><b>Tracks from rekordbox</b><span className="muted small">Add a playlist's
              tracks to the show</span></span></a>
        </span>
      )}
    </span>
  );
}

// -- the dialog -------------------------------------------------------------------------

interface Option { id: string; title: string; text: string }

function Options({ options, value, onChange, children }: {
  options: Option[]; value: string; onChange: (id: string) => void;
  /** What the chosen start needs picked, under it. */
  children?: Record<string, React.ReactNode>;
}) {
  return (
    <div className="s-options" role="radiogroup" aria-label="start from">
      {options.map((o) => (
        <div key={o.id} className="s-opt-wrap">
          <button className={`s-opt${value === o.id ? " on" : ""}`} role="radio"
                  aria-checked={value === o.id} onClick={() => onChange(o.id)}>
            <span className="s-radio" aria-hidden="true"><i /></span>
            <span><b>{o.title}</b><span className="muted small">{o.text}</span></span>
          </button>
          {value === o.id && children?.[o.id] && <div className="s-opt-more">{children[o.id]}</div>}
        </div>
      ))}
    </div>
  );
}

export function NewDialog({ engine, kind, tracks, routines, sets, showSet, palettes, found,
                           onClose, onPalette }: {
  engine: Engine; kind: NewKind;
  tracks: TrackLine[]; routines: RoutineSummary[]; sets: TemplateSummary[];
  /** show.json's set: the first choice for a draft. */
  showSet: string | null;
  palettes: PaletteSummary[]; found: FoundPalette[];
  onClose: () => void;
  /** A palette was written to the library: show it. */
  onPalette: (id: string, said: string) => void;
}) {
  const dialog = useRef<HTMLElement | null>(null);
  // The name field takes focus where there is one; else the dialog itself.
  useEffect(() => {
    if (!dialog.current?.contains(document.activeElement)) dialog.current?.focus();
  }, []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canWrite = engine.tier === "configure";
  const title = { timeline: "New timeline", routine: "New routine", template: "New template set",
                  palette: "New palette" }[kind];

  // -- what each kind is started from
  const bare = tracks.filter((t) => !t.has_timeline).sort((a, b) => a.title.localeCompare(b.title));
  const drawn = tracks.filter((t) => t.has_timeline).sort((a, b) => a.title.localeCompare(b.title));
  const [trackId, setTrackId] = useState(bare[0]?.id ?? "");
  const [start, setStart] = useState<string>(
    kind === "timeline" ? (sets.length ? "draft" : "empty") : "blank");
  const [setId, setSetId] = useState(showSet && sets.some((s) => s.id === showSet) ? showSet : sets[0]?.id ?? "");
  const [copyTrack, setCopyTrack] = useState(drawn[0]?.id ?? "");
  const [name, setName] = useState(kind === "palette" ? "" : kind === "routine" ? "New routine" : "New set");
  const [idEdited, setIdEdited] = useState<string | null>(null);
  const takenIds = kind === "routine" ? routines.map((r) => r.id)
    : kind === "template" ? sets.map((s) => s.id) : palettes.map((p) => p.id);
  const id = idEdited ?? freeId(takenIds, name || kind);
  const [bars, setBars] = useState(4);
  const [loops, setLoops] = useState(true);
  const [folderTyped, setFolder] = useState<string | null>(null);
  const [copyRoutine, setCopyRoutine] = useState(routines[0]?.id ?? "");
  // A copy is filed with its original, unless a folder is typed.
  const folder = folderTyped
    ?? (start === "copy" ? routines.find((r) => r.id === copyRoutine)?.folder ?? "" : "");
  const looks = engine.state?.looks ?? [];
  const [look, setLook] = useState(looks[0]?.name ?? "");
  // A rig has a couple of hundred looks: listed by the slot they play on.
  const lookSlots = useMemo(() => {
    const by = new Map<string, string[]>();
    for (const l of looks) by.set(l.slot, [...(by.get(l.slot) ?? []), l.name]);
    return [...by];
  }, [looks]);
  const [copySet, setCopySet] = useState(sets[0]?.id ?? "");
  const [fromTimeline, setFromTimeline] = useState(drawn[0]?.id ?? "");
  const [copyPalette, setCopyPalette] = useState(palettes[0]?.id ?? "");
  const [foundName, setFoundName] = useState(found[0]?.name ?? "");
  const folders = useMemo(() => [...new Set(routines.map((r) => r.folder).filter((f): f is string => !!f))].sort(),
                          [routines]);

  const idOk = ID_RE.test(id) && !takenIds.includes(id);
  const nameTaken = kind === "palette" && palettes.some((p) => p.name === name.trim());

  const run = async (work: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try { await work(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };

  // -- each kind's Go ---------------------------------------------------------------
  const goTimeline = () => {
    if (!trackId) return;
    if (start === "draft") putPending({ kind: "timeline", id: trackId, set: setId });
    else if (start === "copy") putPending({ kind: "timeline", id: trackId, copy: copyTrack });
    location.hash = `#studio/track/${trackId}`;
    onClose();
  };
  const goRoutine = () => run(async () => {
    let doc: RoutineDoc;
    if (start === "copy") {
      const { doc: from } = await apiFetch<{ doc: RoutineDoc }>(`/api/routines/${copyRoutine}`);
      doc = { ...structuredClone(from), id, name: name.trim() || id };
    } else if (start === "look") {
      const info = looks.find((l) => l.name === look);
      if (!info) throw new Error("pick a look");
      const role = info.groups[0] || "movers";
      doc = { ...newRoutine(id), name: name.trim() || id, bars, loop: loops,
              roles: { [role]: { default: role } }, rig: engine.state?.event,
              rows: [{ id: "look", type: "clips", target: info.slot, role,
                       items: [{ id: "look", at: 0, len: bars * 4, block: "look",
                                 args: { look: info.name } }] }] };
    } else {
      doc = { ...newRoutine(id), name: name.trim() || id, bars, loop: loops };
    }
    if (folder.trim()) doc.folder = folder.trim(); else delete doc.folder;
    if (!doc.rig) delete doc.rig;
    putPending({ kind: "routine", id, doc });
    location.hash = `#studio/routine/${id}`;
    onClose();
  });
  const goTemplate = () => run(async () => {
    let doc: TemplateSetDoc;
    if (start === "copy") {
      const { doc: from } = await apiFetch<{ doc: TemplateSetDoc }>(`/api/templates/${copySet}`);
      doc = { ...structuredClone(from), id, name: name.trim() || id };
    } else if (start === "timeline") {
      const [{ doc: track }, { doc: tl }] = await Promise.all([
        apiFetch<{ doc: TrackDoc }>(`/api/tracks/${fromTimeline}`),
        apiFetch<{ doc: TimelineDoc }>(`/api/timelines/${fromTimeline}`)]);
      const made = templateFromTimeline(id, name.trim() || id, track, tl);
      if (!made) throw new Error(`${track.identity.title}'s scene lane plays no routine over its `
        + "phrases, so there is nothing to make a set from");
      doc = made;
    } else {
      doc = { ...newTemplateSet(id, routines), name: name.trim() || id };
    }
    putPending({ kind: "template", id, doc });
    location.hash = `#studio/templates/${id}`;
    onClose();
  });
  const goPalette = () => run(async () => {
    const label = name.trim();
    let colours = { ...NEW_COLOURS };
    if (start === "copy") {
      const p = palettes.find((x) => x.id === copyPalette);
      if (p) colours = { primary: p.primary, secondary: p.secondary, accent: p.accent };
    } else if (start === "found") {
      const f = found.find((x) => x.name === foundName)?.places[0]?.colours;
      if (f && f.primary && f.secondary && f.accent) {
        colours = { primary: f.primary, secondary: f.secondary, accent: f.accent };
      }
    }
    const doc: PaletteDoc = { $schema: "../schemas/palette.schema.json", kind: "klights.palette",
                              version: 1, id, name: label, ...colours };
    const reply = await engine.request({ type: "palette_save", doc, base_rev: "" });
    if (!reply.ok) throw new Error(reply.error ?? "the engine refused");
    location.hash = "#studio/palettes";
    onPalette(id, `Made ${label}.`);
    onClose();
  });

  // -- the body ------------------------------------------------------------------------
  const copyInfo = (() => {
    const to = tracks.find((t) => t.id === trackId);
    const from = tracks.find((t) => t.id === copyTrack);
    if (!to || !from) return null;
    const a = from.phrase_items ?? [];
    const same = phraseMatch(a, to.phrase_items ?? []);
    if (a.length && same === a.length && same === (to.phrase_items ?? []).length) {
      return "Their phrases are the same, phrase for phrase.";
    }
    return same ? `Their phrases agree up to bar ${Math.floor(a[same - 1]![1] / BEATS_PER_BAR)}; after that, `
      + "check the clips."
      : "Their phrases differ: clips keep their bars, not their phrases.";
  })();

  let body: React.ReactNode;
  let go: (() => void) | null = null;
  let goLabel = "Open it";
  let ready = true;
  let foot = "It opens unsaved: nothing is written until you press Save.";

  const nameRow = (
    <div className="s-name-row">
      <label className="s-field">Name
        <input value={name} aria-label="name" autoFocus
               onChange={(e) => setName(e.target.value)} />
      </label>
      {kind !== "palette" && (
        <label className="s-field">Id, also the file name
          <input className="mono" value={id} aria-label="id"
                 onChange={(e) => setIdEdited(e.target.value.trim())} />
        </label>
      )}
      {kind !== "palette" && !idOk && <span className="small d-error">
        {takenIds.includes(id) ? "already there" : "lower-case letters, digits, - and _"}</span>}
      {nameTaken && <span className="small d-error">the library has a palette of that name</span>}
    </div>
  );

  if (kind === "timeline") {
    goLabel = "Open the timeline";
    go = goTimeline;
    ready = !!trackId && (start !== "copy" || !!copyTrack) && (start !== "draft" || !!setId);
    foot = "The timeline opens with this start applied, unsaved: nothing is written until you press Save.";
    body = !bare.length ? (
      <p className="muted">Every track in the show has a timeline. Add more from{" "}
        <a className="d-link" href="#studio/rekordbox" onClick={onClose}>rekordbox</a>.</p>
    ) : (
      <>
        <label className="s-field">Track
          <select value={trackId} aria-label="track" onChange={(e) => setTrackId(e.target.value)}>
            {bare.map((t) => <option key={t.id} value={t.id}>{t.title}{t.artist ? ` · ${t.artist}` : ""}</option>)}
          </select>
          <span className="muted small">Tracks without a timeline.</span>
        </label>
        <b className="small">Start from</b>
        <Options value={start} onChange={setStart} options={[
          ...(sets.length ? [{ id: "draft", title: "A draft from a template set",
                               text: "One routine per phrase on a scene lane, and the set's palettes." }] : []),
          ...(drawn.length ? [{ id: "copy", title: "Another track's timeline",
                                text: "Its lanes, on this track's bars." }] : []),
          { id: "empty", title: "Empty", text: "Nothing on it yet." },
        ]}>{{
          draft: (
            <select value={setId} aria-label="template set" onChange={(e) => setSetId(e.target.value)}>
              {sets.map((s) => <option key={s.id} value={s.id}>{s.name || s.id}{s.show ? " (the show's)" : ""}</option>)}
            </select>),
          copy: (
            <>
              <select value={copyTrack} aria-label="copy from" onChange={(e) => setCopyTrack(e.target.value)}>
                {drawn.map((t) => <option key={t.id} value={t.id}>{t.title}{t.artist ? ` · ${t.artist}` : ""}</option>)}
              </select>
              {copyInfo && <span className="muted small">{copyInfo}</span>}
            </>),
        }}</Options>
      </>
    );
  } else if (kind === "routine") {
    go = () => void goRoutine();
    ready = idOk && (start !== "copy" || !!copyRoutine) && (start !== "look" || !!look);
    body = (
      <>
        {nameRow}
        <b className="small">Start from</b>
        <Options value={start} onChange={setStart} options={[
          { id: "blank", title: "Blank", text: "A role, movers, and nothing on it: add blocks in the editor." },
          ...(routines.length ? [{ id: "copy", title: "A copy of a routine",
                                   text: "Everything, under the new name. The original is left alone." }] : []),
          ...(looks.length ? [{ id: "look", title: "A look from the console",
                                text: "The look on its own lane, as a routine. It is this rig's own, so the routine is too." }] : []),
        ]}>{{
          copy: (
            <select value={copyRoutine} aria-label="copy of" onChange={(e) => setCopyRoutine(e.target.value)}>
              {routines.map((r) => <option key={r.id} value={r.id}>{r.name || r.id}</option>)}
            </select>),
          look: (
            <select value={look} aria-label="look" onChange={(e) => setLook(e.target.value)}>
              {lookSlots.map(([slot, names]) => (
                <optgroup key={slot} label={slot}>
                  {names.map((n) => <option key={n} value={n}>{n}</option>)}
                </optgroup>))}
            </select>),
        }}</Options>
        <div className="s-name-row">
          {start !== "copy" && <>
            <label className="s-field">Bars
              <input type="number" min={0.25} max={256} value={bars} aria-label="bars" style={{ width: 70 }}
                     onChange={(e) => { const v = Number(e.target.value); if (v >= 0.25 && v <= 256) setBars(v); }} />
            </label>
            <label className="small s-inline"><input type="checkbox" checked={loops}
                   onChange={(e) => setLoops(e.target.checked)} /> loops</label>
          </>}
          <label className="s-field">Folder
            <input value={folder} list="s-new-folders" aria-label="folder" placeholder="unfiled"
                   onChange={(e) => setFolder(e.target.value)} />
            <datalist id="s-new-folders">{folders.map((f) => <option key={f} value={f} />)}</datalist>
          </label>
        </div>
      </>
    );
  } else if (kind === "template") {
    go = () => void goTemplate();
    ready = idOk && (start !== "copy" || !!copySet) && (start !== "timeline" || !!fromTimeline);
    body = (
      <>
        {nameRow}
        <b className="small">Start from</b>
        <Options value={start} onChange={setStart} options={[
          ...(sets.length ? [{ id: "copy", title: "A copy of a set",
                               text: "Every phrase, the bar cycle and the palettes." }] : []),
          ...(drawn.length ? [{ id: "timeline", title: "A track's timeline",
                                text: "For each phrase family, what its scene lane plays most; its palettes too." }] : []),
          { id: "blank", title: "Blank", text: "Anything else plays one routine until you choose more." },
        ]}>{{
          copy: (
            <select value={copySet} aria-label="copy of" onChange={(e) => setCopySet(e.target.value)}>
              {sets.map((s) => <option key={s.id} value={s.id}>{s.name || s.id}</option>)}
            </select>),
          timeline: (
            <select value={fromTimeline} aria-label="from the timeline of"
                    onChange={(e) => setFromTimeline(e.target.value)}>
              {drawn.map((t) => <option key={t.id} value={t.id}>{t.title}{t.artist ? ` · ${t.artist}` : ""}</option>)}
            </select>),
        }}</Options>
      </>
    );
  } else {
    go = () => void goPalette();
    goLabel = "Make it";
    ready = !!name.trim() && !nameTaken && idOk;
    foot = "Written to the library now. Timelines and sets copy it from there.";
    // A file's palette comes with its name, unless one has been typed.
    const named = !name.trim() || name === foundName;
    body = (
      <>
        {nameRow}
        <b className="small">Start from</b>
        <Options value={start} options={[
          { id: "blank", title: "Three colours", text: "White, grey and red, to change on the Palettes page." },
          ...(palettes.length ? [{ id: "copy", title: "A copy of a library palette",
                                   text: "The same three colours, to change one." }] : []),
          ...(found.length ? [{ id: "found", title: "A palette that lives in a file",
                                text: "Its colours, from the timeline or set it is in." }] : []),
        ]} onChange={(v) => { setStart(v); if (v === "found" && !name.trim()) setName(foundName); }}>{{
          copy: (
            <select value={copyPalette} aria-label="copy of" onChange={(e) => setCopyPalette(e.target.value)}>
              {palettes.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>),
          found: (
            <select value={foundName} aria-label="from the file palette"
                    onChange={(e) => { setFoundName(e.target.value); if (named) setName(e.target.value); }}>
              {found.map((f) => <option key={f.name} value={f.name}>{f.name}</option>)}
            </select>),
        }}</Options>
      </>
    );
  }

  return (
    <div className="s-modal" role="presentation"
         onKeyDown={(e) => { if (e.key === "Escape" && !busy) { e.stopPropagation(); onClose(); } }}>
      <section className="s-dialog" role="dialog" aria-modal="true" aria-label={title}
               tabIndex={-1} ref={dialog}>
        <header className="s-dialog-head">
          <h2>{title}</h2>
          <span className="grow" />
          <button className="s-icon" aria-label="close" disabled={busy} onClick={onClose}>×</button>
        </header>
        <div className="s-dialog-body">{body}
          {error && <p className="d-error" role="alert">{error}</p>}
        </div>
        <footer className="s-dialog-foot">
          <span className="muted small grow">{kind === "palette" && !canWrite
            ? "This writes the show folder: open Studio from the link the engine printed." : foot}</span>
          <button onClick={onClose} disabled={busy}>Cancel</button>
          <button className="d-primary" disabled={!go || !ready || busy || (kind === "palette" && !canWrite)}
                  onClick={() => go?.()}>{busy ? "Working…" : goLabel}</button>
        </footer>
      </section>
    </div>
  );
}
