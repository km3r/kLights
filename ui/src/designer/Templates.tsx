import { Fragment, useEffect, useMemo, useState } from "react";
import { ApiError, apiFetch } from "../useEngine";
import type { Engine } from "./Designer";
import { Editor, FromLibrary, PaletteOrigin, ROLES, useHistory } from "./edit";
import {
  EXACT_LABELS, PHRASE_FAMILIES, PHRASE_HUE, freeId, phraseFamily, pickFor,
} from "./model";
import type {
  PaletteSummary, RoutineSummary, ShowSummary, TemplatePick, TemplateSetDoc, TemplateSummary,
  TrackLine,
} from "./model";
import { DRAFT_ON_OPEN } from "./Library";
import type { PendingDraft } from "./Library";
import { download } from "./Routines";

/**
 * Template sets: for each rekordbox phrase, which routine -- the exact label
 * (Up 2), else its family (Up), else `*` -- plus a bar cycle for tracks with a
 * grid and no phrases, the set's own palettes, and the fade between changes.
 *
 * What a set does TODAY is draft timelines: Studio lays it onto a track's
 * phrases as an editable start. Playing tracks that have no timeline from the
 * show's set, live, is F19 milestone 2; until then this page says so rather
 * than let a set look like it lights anything by itself.
 *
 * The editor is the timeline editor's machinery: one undo history over the
 * document, the engine checking every change (`template_draft`), Save quoting
 * the rev it read, and a recovery copy in this browser.
 */

const ID_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const ANY = "*";

export function newTemplateSet(id: string, routines: RoutineSummary[]): TemplateSetDoc {
  const first = routines[0]?.id ?? "";
  return { kind: "klights.template_set", version: 1, id, name: id,
           phrases: { [ANY]: { routine: first } } };
}

function setName(s: { id: string; name?: string | null }): string { return s.name || s.id; }

// -- the page -----------------------------------------------------------------------

export function TemplatesView({ engine, sets, routines, library, current, onDoc }: {
  engine: Engine; sets: TemplateSummary[] | null; routines: RoutineSummary[];
  /** The show's palette library, to copy a palette in from. */
  library: PaletteSummary[];
  /** The set on screen: the route's, else the first. */
  current: string | null;
  /** The working copy, for the panel beside: try it, make it the show's. */
  onDoc: (doc: TemplateSetDoc | null, dirty: boolean, rev: string) => void;
}) {
  const [newId, setNewId] = useState("");
  const list = sets ?? [];
  const idOk = ID_RE.test(newId) && !list.some((s) => s.id === newId);
  return (
    <section className="s-page" aria-label="template sets">
      <div className="s-page-head">
        <div>
          <h1>Template sets</h1>
          <span className="muted small">A set picks a routine for each rekordbox phrase.
            Studio drafts timelines from it; template sets do not play tracks live yet
            (that is F19 milestone 2).</span>
        </div>
        <span className="grow" />
        <form className="d-form" onSubmit={(e) => {
          e.preventDefault();
          if (idOk) location.hash = `#studio/templates/${newId}`;
        }}>
          <input value={newId} onChange={(e) => setNewId(e.target.value.trim())}
                 placeholder="new-set-id" aria-label="new set id" />
          <button type="submit" className="d-primary" disabled={!idOk}>New set</button>
          {newId && !idOk && <span className="small d-error">
            {list.some((s) => s.id === newId) ? "already there"
              : "lower-case letters, digits, - and _ -- it is also the file name"}</span>}
        </form>
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
        <p className="muted">No template sets yet. Name one above to start it.</p>)}
      {current && (
        <TemplateEditor key={current} engine={engine} id={current} routines={routines}
                        library={library} onDoc={onDoc} />
      )}
    </section>
  );
}

// -- one set ---------------------------------------------------------------------------

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

  useEffect(() => {
    apiFetch<{ doc: TemplateSetDoc; rev: string }>(`/api/templates/${id}`)
      .then((r) => { setBase(r.doc); setRev(r.rev); })
      .catch((e: Error) => {
        // Not in the folder: start it, saved with base_rev "" -- a new file.
        if (!(e instanceof ApiError && e.status === 404)) { setLoadError(e.message); return; }
        setBase(newTemplateSet(id, routines));
        setRev("");
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

  /** The fields of one pick: routine, variation, parameters, palette. */
  const pickFields = (key: string, pick: TemplatePick | undefined,
                      onChange: (p: TemplatePick | null) => void, none: string | null) => {
    const routine = pick ? byId.get(pick.routine) : undefined;
    const params = Object.entries(routine?.params ?? {});
    const set = Object.keys(pick?.params ?? {}).length;
    return (
      <>
        <select value={pick?.routine ?? ""} aria-label={`${key} routine`}
                onChange={(e) => {
                  const v = e.target.value;
                  if (!v) { onChange(null); return; }
                  // A routine's variation and parameters are its own: they do
                  // not carry over to another. The palette does.
                  onChange({ routine: v, ...(pick?.palette ? { palette: pick.palette } : {}) });
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
        {pick && params.length > 0 ? (
          <button className={`small${open === key ? " on" : ""}`} aria-expanded={open === key}
                  onClick={() => setOpen(open === key ? null : key)}>
            {set ? `${set} set` : "parameters"}</button>
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
    if (open !== key || !pick || !routine) return null;
    return (
      <div className="s-param-row" role="group" aria-label={`${key} parameters`}>
        {Object.entries(routine.params).map(([name, param]) => (
          <Editor.Param key={name} name={name} param={param} value={pick.params?.[name]}
                        onChange={(v) => {
                          const params = { ...(pick.params ?? {}) };
                          if (v === undefined) delete params[name]; else params[name] = v;
                          const { params: _old, ...rest } = pick;
                          onChange(Object.keys(params).length ? { ...rest, params } : rest);
                        }} />
        ))}
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
            d.phrases[exact] = from ? structuredClone(from) : { routine: routines[0]?.id ?? "" };
          });
          setExact("");
        }}>
          <select value={exact} aria-label="exact label" onChange={(e) => setExact(e.target.value)}>
            <option value="">Exact label…</option>
            {freeExact.map((l) => <option key={l} value={l}>{l}</option>)}
          </select>
          <button type="submit" disabled={!exact}>Add</button>
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
              <div><button onClick={() => apply((d) => {
                const first = d.phrases[ANY]?.routine ?? routines[0]?.id ?? "";
                d.bars = { every: 16, cycle: [{ routine: first }] };
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
                             [name]: { primary: "#ffffff", secondary: "#888888", accent: "#ff0000" } };
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
                                    base_rev: summary?.rev ?? "" }) as { written: string[] };
    location.hash = `#studio/templates/${toId}`;
    return `Renamed ${id} to ${toId}: ${written.length} file${written.length === 1 ? "" : "s"} written.`;
  });
  const remove = () => run(async () => {
    await ask({ type: "template_delete", template: id, base_rev: summary?.rev ?? "" });
    location.hash = "#studio/templates";
    return `Deleted templates/${id}.json.`;
  });
  const draftTrack = () => {
    if (!track) return;
    try {
      const pending: PendingDraft = { track: track.id, set: id, at: Date.now() };
      sessionStorage.setItem(DRAFT_ON_OPEN, JSON.stringify(pending));
    } catch { /* the timeline opens without it */ }
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
        <p className="s-note"><b>The show's set</b>New timelines draft from it by default. Sets
          do not play tracks live yet; when they do (F19 milestone 2), this is the one.</p>
      ) : (
        <div className="s-action">
          <button onClick={() => void makeShows()} disabled={busy || !canWrite || rev === ""}
                  title={rev === "" ? "Save it first" : undefined}>Make it the show's set</button>
          <span className="muted small">New timelines then draft from it by default.</span>
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
