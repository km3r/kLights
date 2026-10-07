import { useEffect, useState } from "react";
import type { Engine } from "./Designer";
import { ROLES } from "./edit";
import { freeId, freeName } from "./model";
import type { FoundPalette, PaletteDoc, PalettePlace, PaletteSummary } from "./model";
import { download } from "./Routines";
import { Badge, DetailHead, DetailSection, MoreMenu, READ_ONLY, Task, useWrite } from "./detail";

/**
 * The show's palette library: one file a palette in `palettes/`.
 *
 * A library palette is a source, not a link. Timelines and template sets keep
 * their own copies by name, as they always have, so each still describes its
 * whole show and the engine compiles nothing differently. What the library
 * adds is knowing where every copy is: change a palette here, save it, and
 * Studio offers to bring the copies that still have the old colors up to
 * date (`palette_sync`) -- one click, and never behind anyone's back.
 *
 * Palettes made inside a single timeline or set before there was a library
 * are listed too, with a way to add each to it.
 */

const COLOR_RE = /^#[0-9a-f]{6}$/i;
/** As the engine's own new documents carry it, for an editor's completion. */
const PALETTE_SCHEMA = "../schemas/palette.schema.json";
type Colors = Pick<PaletteDoc, "primary" | "secondary" | "accent">;

/** A palette as it would be saved -- name trimmed, colors in lower case -- so
 *  "#FF0000" typed and "#ff0000" saved are the same, and a save leaves the
 *  panel clean. */
function savedForm(e: Colors & { name: string }): string {
  return JSON.stringify({ name: e.name.trim(), primary: e.primary.toLowerCase(),
                          secondary: e.secondary.toLowerCase(), accent: e.accent.toLowerCase() });
}

export function placeHref(p: PalettePlace): string {
  return p.kind === "timeline" ? `#studio/track/${p.id}` : `#studio/templates/${p.id}`;
}

function placeName(p: PalettePlace): string {
  return `${p.title ?? p.id}${p.kind === "template_set" ? " (set)" : ""}`;
}

function Swatches({ c, size = "s" }: { c: Partial<Colors>; size?: "s" | "l" }) {
  return (
    <span className={`s-swatches ${size}`} aria-hidden="true">
      {ROLES.map((r) => <i key={r} style={{ background: c[r] ?? "#2a3140" }} />)}
    </span>
  );
}

export function PalettesView({ engine, palettes, found, selected, onSelect, onDone, onNew }: {
  engine: Engine; palettes: PaletteSummary[] | null; found: FoundPalette[];
  /** Open + New's dialog, for a palette. */
  onNew: () => void;
  selected: string | null; onSelect: (id: string) => void;
  onDone: (said: string) => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const list = palettes ?? [];
  const ids = list.map((p) => p.id);
  const canWrite = engine.tier === "configure";

  const create = async (name: string, colors: Colors, said: string) => {
    setError(null);
    const id = freeId(ids, name);
    const doc: PaletteDoc = { $schema: PALETTE_SCHEMA, kind: "klights.palette", version: 1, id,
                              name, ...colors };
    const reply = await engine.request({ type: "palette_save", doc, base_rev: "" });
    if (!reply.ok) { setError(reply.error ?? "the engine refused"); return; }
    onSelect(id);
    onDone(said);
  };

  return (
    <section className="s-page" aria-label="palettes">
      <div className="s-page-head">
        <div>
          <h1>Palettes</h1>
          <span className="muted small">The show's palette library. Timelines and template sets
            keep their own copies by name; change one here and Studio offers to update them.</span>
        </div>
        <span className="grow" />
        <button onClick={onNew}>New palette…</button>
      </div>
      {error && <p className="d-error" role="alert">{error}</p>}
      {palettes == null && <p className="muted">Loading the show folder…</p>}
      {palettes != null && !list.length && (
        <p className="muted">No palettes in the library yet. Make one with New palette, or add
          one that already lives in a timeline or set, below.</p>)}

      <div className="s-palette-cards">
        {list.map((p) => {
          const differ = p.copies.filter((c) => !c.same).length;
          const tl = p.copies.filter((c) => c.kind === "timeline").length;
          const sets = p.copies.length - tl;
          return (
            <button key={p.id} className={`s-palette-card${p.id === selected ? " on" : ""}`}
                    aria-pressed={p.id === selected} onClick={() => onSelect(p.id)}>
              <Swatches c={p} size="l" />
              <span className="s-palette-card-text">
                <b>{p.name}</b>
                <span className="muted small">{p.copies.length
                  ? [tl ? `${tl} timeline${tl === 1 ? "" : "s"}` : "", sets ? `${sets} set${sets === 1 ? "" : "s"}` : ""]
                    .filter(Boolean).join(" · ")
                  : "no copies yet"}</span>
                {differ > 0 && <span className="small s-warn">{differ} cop{differ === 1 ? "y differs" : "ies differ"}</span>}
              </span>
            </button>
          );
        })}
      </div>

      {found.length > 0 && (
        <section className="s-box s-found" aria-label="palettes only in files">
          <header><b>{found.length} palette{found.length === 1 ? " lives" : "s live"} only inside
            timelines and sets</b><span className="muted small">Add one to the library to keep
            its copies in step from here.</span></header>
          {found.map((f) => {
            const first = f.places[0]!.colors;
            const mixed = f.places.some((p) => ROLES.some((r) => p.colors[r] !== first[r]));
            const usable = ROLES.every((r) => COLOR_RE.test(first[r] ?? ""));
            return (
              <div key={f.name} className="s-found-row">
                <Swatches c={first as Partial<Colors>} />
                <b>{f.name}</b>
                <span className="muted small s-found-where">
                  {f.places.map((p) => placeName(p)).join(", ")}
                  {mixed ? " · not the same colors in each" : ""}</span>
                <button disabled={!canWrite || !usable}
                        title={mixed ? "Takes the colors of the first; the others then show as different" : undefined}
                        onClick={() => void create(f.name, first as Colors, `Added ${f.name} to the library.`)}>
                  Add to the library</button>
              </div>
            );
          })}
        </section>
      )}
    </section>
  );
}

// -- the selected palette ----------------------------------------------------------

export function PaletteDetail({ engine, p, palettes, onDone, onSelect }: {
  engine: Engine; p: PaletteSummary; palettes: PaletteSummary[];
  onDone: (said: string) => void; onSelect: (id: string | null) => void;
}) {
  const canWrite = engine.tier === "configure";
  const saved: Colors & { name: string } = { name: p.name, primary: p.primary,
                                             secondary: p.secondary, accent: p.accent };
  const [edit, setEdit] = useState(saved);
  const { busy, error, setError, run, ask } = useWrite(engine, onDone);
  const [confirm, setConfirm] = useState(false);
  const savedKey = savedForm(saved);
  // A newer save (this panel's, or another machine's) is taken while nothing
  // here is unsaved.
  const [base, setBase] = useState(savedKey);
  useEffect(() => {
    if (savedKey === base) return;
    if (savedForm(edit) === base || savedForm(edit) === savedKey) setEdit(saved);
    setBase(savedKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [savedKey]);
  const dirty = savedForm(edit) !== savedKey;
  const valid = ROLES.every((r) => COLOR_RE.test(edit[r])) && !!edit.name.trim()
    && !palettes.some((x) => x.id !== p.id && x.name === edit.name.trim());
  const older = p.copies.filter((c) => !c.same);

  const doc = (): PaletteDoc => ({ $schema: PALETTE_SCHEMA, kind: "klights.palette", version: 1,
                                   id: p.id, name: edit.name.trim(),
                                   primary: edit.primary.toLowerCase(),
                                   secondary: edit.secondary.toLowerCase(),
                                   accent: edit.accent.toLowerCase() });
  const save = () => run(async () => {
    await ask({ type: "palette_save", doc: doc(), base_rev: p.rev ?? "" });
    return `Saved ${edit.name.trim()} to the library. Copies keep their own colors until you `
      + "give them these.";
  });
  const sync = () => run(async () => {
    const { written } = await ask({ type: "palette_sync", palette: p.id,
                                    files: older.map((c) => c.file) }) as { written: string[] };
    return `Gave ${written.length} cop${written.length === 1 ? "y" : "ies"} of ${p.name} the library's colors.`;
  });
  const duplicate = () => run(async () => {
    const name = freeName(palettes.map((x) => x.name), `${p.name} copy`);
    const id = freeId(palettes.map((x) => x.id), name);
    await ask({ type: "palette_save", base_rev: "",
                doc: { ...doc(), id, name, primary: p.primary, secondary: p.secondary,
                       accent: p.accent } });
    onSelect(id);
    return `Duplicated ${p.name} as ${name}.`;
  });
  const remove = () => run(async () => {
    await ask({ type: "palette_delete", palette: p.id, base_rev: p.rev ?? "" });
    onSelect(null);
    return `Deleted ${p.name} from the library. Its copies stay in their files.`;
  });

  return (
    <div className="s-detail" aria-label="selected palette">
      <DetailHead kind="Library palette"
                  title={<input className="s-title-input" value={edit.name} aria-label="palette name"
                                onChange={(e) => setEdit({ ...edit, name: e.target.value })} />}
                  meta={<span className="mono">palettes/{p.id}.json</span>}
                  badges={dirty ? <Badge tone="info">Unsaved</Badge> : undefined}
                  menu={<MoreMenu label={p.name} items={[
                    { label: "Duplicate", disabled: busy || !canWrite,
                      ...(canWrite ? {} : { note: "Read only" }), onClick: () => void duplicate() },
                    { label: "Download the file", onClick: () => download(`${p.id}.json`, doc()) },
                    { label: "Delete…", danger: true, onClick: () => { setError(null); setConfirm(true); } },
                  ]} />} />

      {confirm && (
        <Task title={`Delete ${p.name}`} label="confirm delete" readOnly={!canWrite}
              onCancel={() => setConfirm(false)}>
          <span className="small">Delete palettes/{p.id}.json? Its copies stay where they are.</span>
          <div className="d-form">
            <button className="d-bad" disabled={busy || !canWrite} onClick={() => void remove()}>
              Delete it</button>
            <button onClick={() => setConfirm(false)}>Keep it</button>
          </div>
        </Task>
      )}
      {error && <p className="small d-error" role="alert">{error}</p>}

      <DetailSection title="Colors">
        <div className="s-roles">
          {ROLES.map((r) => (
            <div key={r} className="s-role-row">
              <b>{r[0]!.toUpperCase() + r.slice(1)}</b>
              <input type="color" aria-label={`${r} color`}
                     value={COLOR_RE.test(edit[r]) ? edit[r] : "#000000"}
                     onChange={(e) => setEdit({ ...edit, [r]: e.target.value })} />
              <input className="mono" aria-label={`${r} hex`} value={edit[r]}
                     onChange={(e) => setEdit({ ...edit, [r]: e.target.value.trim() })} />
            </div>
          ))}
        </div>
        {edit.name.trim() !== p.name && p.copies.length > 0 && (
          <p className="s-note warn">Copies are found by name: the {p.copies.length} cop
            {p.copies.length === 1 ? "y" : "ies"} named {p.name} would no longer count as copies of
            this one.</p>)}
        {dirty && (
          <div className="d-form">
            <button className="d-primary" disabled={!valid || busy || !canWrite}
                    onClick={() => void save()}>Save to the library</button>
            <button onClick={() => setEdit(saved)}>Revert</button>
            {!canWrite && <span className="muted small">{READ_ONLY}</span>}
          </div>
        )}
      </DetailSection>

      <DetailSection title="Copies" label="copies" count={p.copies.length}>
        {p.copies.length ? <span className="muted small">Timelines and sets keep their own
          copy, by name. Saving here changes none of them until you update them.</span>
          : <span className="muted small">No timeline or set has a palette called {p.name} yet.
            Pick it from the library in either editor.</span>}
        <div className="s-uses">
          {p.copies.map((c) => (
            <a key={c.file} className="s-use s-copy" href={placeHref(c)}>
              <b>{placeName(c)}</b>
              <span className="s-copy-state">
                <Swatches c={c.colors as Partial<Colors>} />
                <span className={`small ${c.same ? "muted" : "s-warn"}`}>
                  {c.same ? "the same" : "different"}</span>
              </span>
            </a>
          ))}
        </div>
        {older.length > 0 && (
          <>
            <button className="d-primary s-self" disabled={busy || dirty || !canWrite}
                    onClick={() => void sync()}>
              Give {older.length} cop{older.length === 1 ? "y" : "ies"} the library's colors</button>
            <span className="muted small">{dirty ? "Save first: copies take the saved colors."
              : "Overwrites those copies' colors in their files. One changed meanwhile is left "
                + "alone and named."}</span>
          </>
        )}
      </DetailSection>
    </div>
  );
}
