import { Fragment, useEffect, useMemo, useState } from "react";
import { ApiError, apiFetch } from "../useEngine";
import type { Engine } from "./Designer";
import { Editor, FromLibrary, PaletteOrigin, ROLES, RoleBinds, newVisuals, useHistory } from "./edit";
import {
  EXACT_LABELS, ID_RE, NEW_COLOURS, PHRASE_FAMILIES, PHRASE_HUE, freeId, phraseFamily, pickFor,
} from "./model";
import type {
  PaletteSummary, RoutineSummary, ShowSummary, TemplatePick, TemplateSetDoc, TemplateSummary,
  TrackLine,
} from "./model";
import { clearPending, peekPending, putPending } from "./pending";
import { download } from "./Routines";

/**
 * Template sets: for each rekordbox phrase, which routine -- the exact label
 * (Up 2), else its family (Up), else `*` -- plus a bar cycle for tracks with a
 * grid and no phrases, the set's own palettes, and the fade between changes.
 *
 * A set does two things. Live, with Follow armed, the playing set lights a
 * track that has no timeline -- a routine per phrase -- and shows through a
 * timeline's gaps (F22b). Here, it is what Studio drafts timelines from: laid
 * onto a track's phrases as an editable start.
 *
 * The editor is the timeline editor's machinery: one undo history over the
 * document, the engine checking every change (`template_draft`), Save quoting
 * the rev it read, and a recovery copy in this browser.
 */

const ANY = "*";

export function newTemplateSet(id: string, routines: RoutineSummary[]): TemplateSetDoc {
  const first = routines[0]?.id ?? "";
  return { kind: "klights.template_set", version: 1, id, name: id,
           phrases: { [ANY]: { routine: first } } };
}

function setName(s: { id: string; name?: string | null }): string { return s.name || s.id; }

// -- the page -----------------------------------------------------------------------

export function TemplatesView({ engine, sets, routines, library, current, onDoc, onNew }: {
  engine: Engine; sets: TemplateSummary[] | null; routines: RoutineSummary[];
  /** Open + New's dialog, for a set. */
  onNew: () => void;
  /** The show's palette library, to copy a palette in from. */
  library: PaletteSummary[];
  /** The set on screen: the route's, else the first. */
  current: string | null;
  /** The working copy, for the panel beside: try it, make it the show's. */
  onDoc: (doc: TemplateSetDoc | null, dirty: boolean, rev: string) => void;
}) {
  const list = sets ?? [];
  return (
    <section className="s-page" aria-label="template sets">
      <div className="s-page-head">
        <div>
          <h1>Template sets</h1>
          <span className="muted small">A set picks a routine for each rekordbox phrase.
            Live, the playing set lights tracks with no timeline and shows through a
            timeline's gaps; Studio also drafts timelines from it.</span>
        </div>
        <span className="grow" />
        <button onClick={onNew}>New set…</button>
      </div>
      {sets == null && <p className="muted">Loading the show folder…</p>}
      {list.length > 0 && (
        <nav className="s-tabs" aria-label="sets">
          {list.map((s) => (
            <a key={s.id} href={`#studio/templates/${s.id}`}
               className={`s-tab${s.id === current ? " on" : ""}`}
               aria-current={s.id === current ? "page" : undefined}>
              {setName(s)}{s.show && <span className="s-show-badge">show's</span>}</a>
          ))}
          {current && !list.some((s) => s.id === current) && (
            <span className="s-tab on" aria-current="page">{current}
              <span className="s-tag">new</span></span>)}
        </nav>
      )}
      {sets != null && !list.length && !current && (
        <p className="muted">No template sets yet. Make one with New set.</p>)}
      {current && (
        <TemplateEditor key={current} engine={engine} id={current} routines={routines}
                        library={library} onDoc={onDoc} />
      )}
    </section>
  );
}

// -- one set ---------------------------------------------------------------------------

type Visuals = NonNullable<TemplatePick["visuals"]>;

/** What the built-in visuals show while a pick plays: none, or a scene and its
 *  settings -- the same controls as a visuals cue on a timeline. */
function PickVisuals({ label, visuals, onChange }: {
  label: string; visuals: Visuals | undefined; onChange: (v: Visuals | undefined) => void;
}) {
  if (!visuals) {
    return (
      <div className="s-pick-visuals" role="group" aria-label={`${label} visuals`}>
        <span className="small muted">Visuals: none -- the built-in visuals show nothing for it.</span>
        <button className="small" onClick={() => onChange(newVisuals())}>+ visuals</button>
      </div>
    );
  }
  return (
    <div className="s-pick-visuals" role="group" aria-label={`${label} visuals`}>
      <span className="small muted">Visuals</span>
      <Editor.VisualCue item={{ id: label, at: 0, len: 0, scene: visuals.scene, params: visuals.params }}
                        set={(fields) => onChange({ scene: fields.scene ?? visuals.scene,
                                                    params: fields.params ?? visuals.params ?? {} })} />
      <button className="small" aria-label={`no visuals for ${label}`}
              onClick={() => onChange(undefined)}>×</button>
    </div>
  );
}

function TemplateEditor({ engine, id, routines, library, onDoc }: {
  engine: Engine; id: string; routines: RoutineSummary[]; library: PaletteSummary[];
  onDoc: (doc: TemplateSetDoc | null, dirty: boolean, rev: string) => void;
}) {
  const history = useHistory<TemplateSetDoc>();
  const { doc, setBase, apply } = history;
  const [rev, setRev] = useState("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);     // a pick whose params show
  const [exact, setExact] = useState("");
  const [palName, setPalName] = useState("");
  // A start handed over by New: a copy, or a set made from a timeline.
  const [pending] = useState(() => peekPending("template", id));

  useEffect(() => {
    apiFetch<{ doc: TemplateSetDoc; rev: string }>(`/api/templates/${id}`)
      .then((r) => { setBase(r.doc); setRev(r.rev); })
      .catch((e: Error) => {
        // Not in the folder: start it, saved with base_rev "" -- a new file.
        if (!(e instanceof ApiError && e.status === 404)) { setLoadError(e.message); return; }
        setBase(pending?.doc ?? newTemplateSet(id, routines));
        setRev("");
        clearPending("template", id);
      });
    // Only the id decides what loads; the routines are only a first pick.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, setBase]);
  useEffect(() => { onDoc(doc, history.dirty, rev); }, [doc, history.dirty, rev, onDoc]);
  useEffect(() => () => onDoc(null, false, ""), [onDoc]);

  if (loadError) return <p className="d-error" role="alert">{loadError}</p>;
  if (!doc) return <p className="muted">Loading {id}…</p>;

  const byId = new Map(routines.map((r) => [r.id, r]));
  const palettes = Object.keys(doc.palettes ?? {});
  const exacts = Object.keys(doc.phrases).filter((l) => l !== ANY && !PHRASE_FAMILIES.includes(l))
    .sort((a, b) => (EXACT_LABELS.indexOf(a) - EXACT_LABELS.indexOf(b)) || a.localeCompare(b));
  const labels = [...PHRASE_FAMILIES.flatMap((f) => [f, ...exacts.filter((l) => phraseFamily(l) === f)]),
                  ...exacts.filter((l) => !PHRASE_FAMILIES.includes(phraseFamily(l))), ANY];
  const usedPalettes = new Set([...Object.values(doc.phrases), ...(doc.bars?.cycle ?? [])]
    .map((p) => p.palette).filter(Boolean));

  const setPick = (label: string, next: TemplatePick | null) => apply((d) => {
    if (next) d.phrases[label] = next; else delete d.phrases[label];
  });
  const setStep = (i: number, next: TemplatePick) => apply((d) => { d.bars!.cycle[i] = next; });

  /** The fields of one pick: routine, variation, its settings (parameters,
   *  which fixtures its roles play on, what the built-in visuals show),
   *  palette. */
  const pickFields = (key: string, pick: TemplatePick | undefined,
                      onChange: (p: TemplatePick | null) => void, none: string | null) => {
    const routine = pick ? byId.get(pick.routine) : undefined;
    const set = Object.keys(pick?.params ?? {}).length + Object.keys(pick?.bind ?? {}).length
      + (pick?.visuals ? 1 : 0);
    return (
      <>
        <select value={pick?.routine ?? ""} aria-label={`${key} routine`}
                onChange={(e) => {
                  const v = e.target.value;
                  if (!v) { onChange(null); return; }
                  // A routine's variation, parameters and role bindings are
                  // its own: they do not carry over to another. The palette
                  // and the visuals do.
                  onChange({ routine: v, ...(pick?.palette ? { palette: pick.palette } : {}),
                             ...(pick?.visuals ? { visuals: pick.visuals } : {}) });
                }}>
          {none != null && <option value="">{none}</option>}
          {pick && !byId.has(pick.routine) && <option value={pick.routine}>{pick.routine} (missing)</option>}
          {routines.map((r) => <option key={r.id} value={r.id}>{r.name || r.id}</option>)}
        </select>
        {pick && routine && routine.variations.length > 0 ? (
          <select value={pick.variation ?? ""} aria-label={`${key} variation`}
                  onChange={(e) => {
                    const { variation: _old, ...rest } = pick;
                    onChange(e.target.value ? { ...rest, variation: e.target.value } : rest);
                  }}>
            <option value="">default</option>
            {routine.variations.map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
        ) : <span className="muted small">{pick ? "no variations" : ""}</span>}
        {pick ? (
          <button className={`small${open === key ? " on" : ""}`} aria-expanded={open === key}
                  aria-label={`${key} settings`}
                  title="Its parameters, which fixtures its roles play on, and its visuals"
                  onClick={() => setOpen(open === key ? null : key)}>
            {set ? `${set} set` : "settings"}</button>
        ) : <span />}
        {pick ? (
          <select value={pick.palette ?? ""} aria-label={`${key} palette`}
                  onChange={(e) => {
                    const { palette: _old, ...rest } = pick;
                    onChange(e.target.value ? { ...rest, palette: e.target.value } : rest);
                  }}>
            <option value="">{palettes.length ? "the set's default" : "no palettes"}</option>
            {palettes.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        ) : <span />}
      </>
    );
  };
  const paramRow = (key: string, pick: TemplatePick | undefined,
                    onChange: (p: TemplatePick) => void) => {
    const routine = pick ? byId.get(pick.routine) : undefined;
    if (open !== key || !pick) return null;
    // A routine missing from routines/ has no parameters or roles to show;
    // the pick's visuals are its own, so they stay in reach.
    return (
      <div className="s-param-row" role="group" aria-label={`${key} parameters`}>
        {routine && Object.entries(routine.params).map(([name, param]) => (
          <Editor.Param key={name} name={name} param={param} value={pick.params?.[name]}
                        onChange={(v) => {
                          const params = { ...(pick.params ?? {}) };
                          if (v === undefined) delete params[name]; else params[name] = v;
                          const { params: _old, ...rest } = pick;
                          onChange(Object.keys(params).length ? { ...rest, params } : rest);
                        }} />
        ))}
        {routine && (
          <RoleBinds label={key} roles={routine.roles} bind={pick.bind} state={engine.state}
                     onChange={(bind) => {
                       const { bind: _old, ...rest } = pick;
                       onChange(bind ? { ...rest, bind } : rest);
                     }} />)}
        <PickVisuals label={key} visuals={pick.visuals}
                     onChange={(visuals) => {
                       const { visuals: _old, ...rest } = pick;
                       onChange(visuals ? { ...rest, visuals } : rest);
                     }} />
      </div>
    );
  };

  const hex = (v: unknown) => (typeof v === "string" && /^#[0-9a-f]{6}$/i.test(v) ? v : "#ffffff");
  const freeExact = EXACT_LABELS.filter((l) => !(l in doc.phrases));

  return (
    <div className="s-editor" aria-label={`set ${id}`}>
      <div className="s-editor-head">
        <label className="small s-inline">Name{" "}
          <input value={doc.name ?? ""} aria-label="set name"
                 onChange={(e) => { const v = e.target.value; apply((d) => { d.name = v; }); }} />
        </label>
        <span className="muted mono small">templates/{id}.json</span>
        {rev === "" && <span className="s-tag">new</span>}
        <span className="grow" />
        <Editor.Toolbar history={history} rev={rev} setRev={setRev} engine={engine}
                        kind="template" ident={id} />
      </div>

      <section className="s-box" aria-label="phrases">
        <header><b>Phrases</b><span className="muted small">An exact label (Up 2) wins over its
          family (Up), which wins over Anything else.</span></header>
        <div className="s-picks">
          <span className="s-picks-head">Phrase</span><span className="s-picks-head">Routine</span>
          <span className="s-picks-head">Variation</span><span className="s-picks-head" />
          <span className="s-picks-head">Palette</span><span className="s-picks-head" />
          {labels.map((label) => {
            const pick = doc.phrases[label];
            const family = label === ANY ? null : phraseFamily(label);
            const isExact = family != null && family !== label;
            const falls = !pick && label !== ANY ? pickFor(doc, label) : undefined;
            const shown = label === ANY ? "Anything else" : label;
            return (
              <Fragment key={label}>
                <span className={`s-phrase-label${isExact ? " exact" : ""}`}>
                  <i style={{ background: family ? PHRASE_HUE[family] ?? "#475569" : "#46516a" }} />
                  {shown}
                  {isExact && <span className="s-tag">exact</span>}</span>
                {pickFields(shown, pick, (p) => setPick(label, p),
                            label === ANY ? "nothing" : isExact ? "as its family"
                              : "as Anything else")}
                {isExact ? (
                  <button className="s-icon" aria-label={`remove ${label}`}
                          onClick={() => setPick(label, null)}>×</button>
                ) : <span className="muted small">{falls
                  ? `→ ${byId.get(falls.routine)?.name ?? falls.routine}` : ""}</span>}
                {paramRow(shown, pick, (p) => setPick(label, p))}
              </Fragment>
            );
          })}
        </div>
        <form className="d-form" onSubmit={(e) => {
          e.preventDefault();
          if (!exact) return;
          apply((d) => {
            const from = d.phrases[phraseFamily(exact)] ?? d.phrases[ANY];
            if (from) d.phrases[exact] = structuredClone(from);
            else if (routines[0]) d.phrases[exact] = { routine: routines[0].id };
          });
          setExact("");
        }}>
          <select value={exact} aria-label="exact label" onChange={(e) => setExact(e.target.value)}>
            <option value="">Exact label…</option>
            {freeExact.map((l) => <option key={l} value={l}>{l}</option>)}
          </select>
          <button type="submit" disabled={!exact || (!routines.length
            && !(doc.phrases[phraseFamily(exact)] ?? doc.phrases[ANY]))}>Add</button>
          <span className="muted small">For one numbered phrase that should differ from its
            family: Up 2 as the bigger build, say.</span>
        </form>
      </section>

      <div className="s-boxes">
        <section className="s-box" aria-label="bar cycle">
          <header><b>No phrases? A bar cycle</b></header>
          {!doc.bars ? (
            <>
              <p className="muted small">Without one, a track with a grid and no phrases cannot be
                drafted from this set.</p>
              <div><button disabled={!(doc.phrases[ANY]?.routine ?? routines[0]?.id)}
                           onClick={() => apply((d) => {
                const first = d.phrases[ANY]?.routine ?? routines[0]?.id;
                if (first) d.bars = { every: 16, cycle: [{ routine: first }] };
              })}>Add a bar cycle</button></div>
            </>
          ) : (
            <>
              <label className="small s-inline">A new step every{" "}
                <input type="number" min={1} max={256} value={doc.bars.every} aria-label="bars per step"
                       style={{ width: 64 }}
                       onChange={(e) => {
                         const v = Number(e.target.value);
                         if (v >= 1 && v <= 256) apply((d) => { d.bars!.every = v; });
                       }} /> bars, in turn:</label>
              <div className="s-picks s-steps">
                {doc.bars.cycle.map((step, i) => (
                  <Fragment key={i}>
                    <span className="mono small">{i + 1}</span>
                    {pickFields(`step ${i + 1}`, step, (p) => {
                      if (p) setStep(i, p);
                    }, null)}
                    <span className="s-step-tools">
                      <button className="s-icon" aria-label={`step ${i + 1} earlier`} disabled={i === 0}
                              onClick={() => apply((d) => {
                                const c = d.bars!.cycle;
                                [c[i - 1], c[i]] = [c[i]!, c[i - 1]!];
                              })}>↑</button>
                      <button className="s-icon" aria-label={`remove step ${i + 1}`}
                              disabled={doc.bars!.cycle.length < 2}
                              onClick={() => apply((d) => { d.bars!.cycle.splice(i, 1); })}>×</button>
                    </span>
                    {paramRow(`step ${i + 1}`, step, (p) => setStep(i, p))}
                  </Fragment>
                ))}
              </div>
              <div className="d-form">
                <button onClick={() => apply((d) => {
                  const c = d.bars!.cycle;
                  c.push(structuredClone(c[c.length - 1]!));
                })}>+ Step</button>
                <button className="d-bad" onClick={() => apply((d) => { delete d.bars; })}>
                  Remove the bar cycle</button>
              </div>
            </>
          )}
        </section>

        <section className="s-box" aria-label="palettes">
          <header><b>This set's palettes</b><span className="muted small">Copies: changing a
            colour here changes this set only. The library's are on the{" "}
            <a className="d-link" href="#studio/palettes">Palettes</a> page.</span></header>
          {palettes.map((name) => {
            const pal = doc.palettes![name]!;
            return (
              <div key={name} className="s-palette-row">
                <label className="small s-inline">
                  <input type="radio" name={`default-palette-${id}`} checked={doc.palette === name}
                         aria-label={`${name} is the default`}
                         onChange={() => apply((d) => { d.palette = name; })} />{name}</label>
                <PaletteOrigin library={library} name={name} colours={pal} here="set"
                               onUseLibrary={(c) => apply((d) => { d.palettes![name] = c; })} />
                {ROLES.map((role) => (
                  <input key={role} type="color" aria-label={`${name} ${role}`} value={hex(pal[role])}
                         onChange={(e) => {
                           const v = e.target.value;
                           apply((d) => { d.palettes![name]![role] = v; });
                         }} />
                ))}
                <button className="s-icon" aria-label={`remove palette ${name}`}
                        disabled={usedPalettes.has(name)}
                        title={usedPalettes.has(name) ? "A phrase or step switches to it" : undefined}
                        onClick={() => apply((d) => {
                          delete d.palettes![name];
                          if (d.palette === name) delete d.palette;
                          if (!Object.keys(d.palettes!).length) delete d.palettes;
                        })}>×</button>
              </div>
            );
          })}
          <form className="d-form" onSubmit={(e) => {
            e.preventDefault();
            const name = palName.trim();
            if (!name || palettes.includes(name)) return;
            apply((d) => {
              d.palettes = { ...(d.palettes ?? {}),
                             [name]: { ...NEW_COLOURS } };
              if (!d.palette) d.palette = name;
            });
            setPalName("");
          }}>
            <input value={palName} onChange={(e) => setPalName(e.target.value)}
                   placeholder="palette name" aria-label="new palette name" style={{ width: 130 }} />
            <button type="submit" disabled={!palName.trim() || palettes.includes(palName.trim())}>
              + Palette</button>
            <FromLibrary library={library} has={palettes}
                         onPick={(name, colours) => apply((d) => {
                           d.palettes = { ...(d.palettes ?? {}), [name]: colours };
                           if (!d.palette) d.palette = name;
                         })} />
          </form>
        </section>

        <section className="s-box" aria-label="changes">
          <header><b>Between phrases</b></header>
          <label className="small s-inline">Fade{" "}
            <input type="number" min={0} max={64} aria-label="fade beats" style={{ width: 64 }}
                   value={doc.transition?.fade_beats ?? ""} placeholder="0"
                   onChange={(e) => {
                     const raw = e.target.value;
                     const v = Number(raw);
                     apply((d) => {
                       if (raw === "") delete d.transition;
                       else if (v >= 0 && v <= 64) d.transition = { fade_beats: v };
                     });
                   }} /> beats, given to each drafted clip as its fade in</label>
        </section>
      </div>
    </div>
  );
}

// -- beside it: try it, make it the show's, its file ------------------------------------------

export function TemplateAside({ engine, id, doc, dirty, rev, summary, sets, tracks, show,
                               onDone }: {
  engine: Engine; id: string; doc: TemplateSetDoc | null; dirty: boolean; rev: string;
  summary: TemplateSummary | undefined; sets: TemplateSummary[];
  tracks: TrackLine[]; show: ShowSummary | null;
  /** Something was written, and what. */
  onDone: (said: string) => void;
}) {
  const canWrite = engine.tier === "configure";
  const phrased = useMemo(() => tracks.filter((t) => (t.phrase_items ?? []).length > 0), [tracks]);
  const [trackId, setTrackId] = useState<string>(phrased[0]?.id ?? "");
  const [mode, setMode] = useState<"duplicate" | "rename" | "delete" | null>(null);
  const [dupId, setDupId] = useState(() => freeId(sets.map((s) => s.id), `${id}-copy`));
  const [toId, setToId] = useState(id);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { if (!trackId && phrased[0]) setTrackId(phrased[0].id); }, [phrased, trackId]);
  const track = phrased.find((t) => t.id === trackId);
  const saved = rev !== "" && !dirty;
  const isShows = summary?.show ?? (show?.show?.template_set === id);

  const run = async (work: () => Promise<string>) => {
    setBusy(true);
    setError(null);
    try {
      onDone(await work());
      setMode(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const ask = async (command: Parameters<Engine["request"]>[0]) => {
    const reply = await engine.request(command);
    if (!reply.ok) throw new Error(reply.error ?? "the engine refused");
    return reply.data;
  };
  const makeShows = () => run(async () => {
    if (!show?.show) throw new Error("this show folder has no show.json: make one in Show settings");
    await ask({ type: "show_save", doc: { ...show.show, template_set: id },
                base_rev: show.show_rev ?? "" });
    return `${setName({ id, name: doc?.name })} is the show's template set.`;
  });
  const duplicate = () => run(async () => {
    if (!doc) throw new Error("nothing to copy yet");
    const copy: TemplateSetDoc = { ...structuredClone(doc), id: dupId,
                                   name: `${setName({ id, name: doc.name })} (copy)` };
    await ask({ type: "template_save", doc: copy, base_rev: "" });
    location.hash = `#studio/templates/${dupId}`;
    return `Duplicated ${id} as ${dupId}.`;
  });
  const rename = () => run(async () => {
    const { written } = await ask({ type: "template_rename", template: id, to: toId,
                                    base_rev: rev || summary?.rev || "" }) as { written: string[] };
    location.hash = `#studio/templates/${toId}`;
    return `Renamed ${id} to ${toId}: ${written.length} file${written.length === 1 ? "" : "s"} written.`;
  });
  const remove = () => run(async () => {
    await ask({ type: "template_delete", template: id, base_rev: rev || summary?.rev || "" });
    location.hash = "#studio/templates";
    return `Deleted templates/${id}.json.`;
  });
  const draftTrack = () => {
    if (!track) return;
    putPending({ kind: "timeline", id: track.id, set: id });
    location.hash = `#studio/track/${track.id}`;
  };
  const dupOk = ID_RE.test(dupId) && !sets.some((s) => s.id === dupId);
  const toOk = ID_RE.test(toId) && toId !== id && !sets.some((s) => s.id === toId);

  return (
    <div className="s-detail" aria-label="selected set">
      <div>
        <span className="s-kicker">Template set{isShows ? " · the show's" : ""}</span>
        <h2>{setName({ id, name: doc?.name ?? summary?.name })}</h2>
      </div>
      {isShows ? (
        <p className="s-note"><b>The show's set</b>The engine starts on it: with Follow armed it
          lights tracks with no timeline, until the operator switches set. New timelines
          draft from it by default.</p>
      ) : (
        <div className="s-action">
          <button onClick={() => void makeShows()} disabled={busy || !canWrite || rev === ""}
                  title={rev === "" ? "Save it first" : undefined}>Make it the show's set</button>
          <span className="muted small">The engine then starts on it, and new timelines draft
            from it by default.</span>
        </div>
      )}

      <section className="s-try" aria-label="try it on a track">
        <b className="small">Try it on a track</b>
        {phrased.length ? (
          <>
            <select value={trackId} aria-label="track to try" onChange={(e) => setTrackId(e.target.value)}>
              {phrased.map((t) => <option key={t.id} value={t.id}>{t.title}{t.artist ? ` · ${t.artist}` : ""}</option>)}
            </select>
            {track && doc && (
              <div className="s-try-strip" aria-label="what it would draft">
                {(track.phrase_items ?? []).map(([s, e, label]) => {
                  const pick = pickFor(doc, label);
                  return (
                    <span key={`${s}-${label}`} style={{ flex: Math.max(1, e - s) }}
                          title={`${label}: ${pick?.routine ?? "nothing"}${pick?.variation ? ` (${pick.variation})` : ""}`}>
                      <i style={{ background: PHRASE_HUE[phraseFamily(label)] ?? "#475569" }} />
                      <em>{pick?.routine ?? "—"}</em>
                    </span>
                  );
                })}
              </div>
            )}
            <button onClick={draftTrack} disabled={!saved || !track}
                    title={saved ? "Open the track's timeline with this set's draft on it, unsaved"
                      : "Save the set first: a draft reads the saved set"}>
              Draft {track?.title ?? "it"} from this set</button>
            {!saved && <span className="muted small">Save first: a draft reads the saved set.</span>}
          </>
        ) : <span className="muted small">No track in the show has phrases to try it on.</span>}
      </section>

      {error && <p className="small d-error" role="alert">{error}</p>}
      <div className="s-actions">
        <div className="s-action">
          <button className={mode === "duplicate" ? "on" : ""} aria-expanded={mode === "duplicate"}
                  onClick={() => setMode(mode === "duplicate" ? null : "duplicate")}>Duplicate</button>
          {mode === "duplicate" && (
            <form className="d-form" onSubmit={(e) => { e.preventDefault(); if (dupOk) void duplicate(); }}>
              <input value={dupId} aria-label="id of the copy" onChange={(e) => setDupId(e.target.value.trim())} />
              <button type="submit" className="d-primary" disabled={!dupOk || busy || !canWrite}>
                Make the copy</button>
              <span className="muted small">As it is on screen, saved or not.</span>
            </form>
          )}
        </div>
        <div className="s-action">
          <button className={mode === "rename" ? "on" : ""} aria-expanded={mode === "rename"}
                  disabled={rev === ""} onClick={() => setMode(mode === "rename" ? null : "rename")}>
            Rename</button>
          {mode === "rename" && (
            <form className="d-form" onSubmit={(e) => { e.preventDefault(); if (toOk) void rename(); }}>
              <input value={toId} aria-label="new id" onChange={(e) => setToId(e.target.value.trim())} />
              <button type="submit" className="d-primary" disabled={!toOk || busy || !canWrite || dirty}>
                Rename it</button>
              <span className="muted small">{dirty ? "Save or undo the changes first. " : ""}
                {isShows ? "show.json is changed with it." : ""}</span>
            </form>
          )}
        </div>
        <div className="s-action">
          <button disabled={!doc} onClick={() => doc && download(`${id}.json`, doc)}>
            Download the file</button>
        </div>
        <div className="s-action">
          {isShows ? (
            <button disabled title="Make another set the show's first">Delete: it is the show's set</button>
          ) : rev === "" ? null : mode === "delete" ? (
            <div className="d-form" role="group" aria-label="confirm delete">
              <span className="small">Delete templates/{id}.json? Studio cannot undo it.</span>
              <button className="d-bad" disabled={busy || !canWrite} onClick={() => void remove()}>
                Delete it</button>
              <button onClick={() => setMode(null)}>Keep it</button>
            </div>
          ) : <button className="d-bad" onClick={() => setMode("delete")}>Delete…</button>}
        </div>
      </div>
    </div>
  );
}
