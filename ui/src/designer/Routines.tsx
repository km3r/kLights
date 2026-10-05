import { useEffect, useMemo, useRef, useState } from "react";
import { apiFetch } from "../useEngine";
import type { Engine } from "./Designer";
import { normalizeName, usageCount } from "./model";
import type { RoutineDoc, RoutineSummary } from "./model";

/**
 * Studio's routine library: every routine as a card -- what its rows drive, its
 * roles and open parameters, where it is used -- filed into folders, with the
 * things a library needs: duplicate, rename, move, download, delete.
 *
 * Where a routine is used comes from the engine (`used_by` on /api/routines),
 * so a rename or a delete is judged before it is tried. Rename and delete are
 * the engine's (`routine_rename` moves every reference with it; `routine_delete`
 * refuses while anything names it); duplicate and move are ordinary saves.
 */

const ID_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const UNFILED = "";
type View = { kind: "all" } | { kind: "folder"; folder: string } | { kind: "rig" }
  | { kind: "unused" };
type Sort = "name" | "used" | "length";

/** A routine's files' rows, as a strip per row: movement, colour, level. */
const SLOT_CLASS: Record<string, string> = { movement: "m", color: "c", level: "l" };

export function nameOf(r: RoutineSummary): string { return r.name || r.id; }

/** An id not taken by any routine, from a stem. */
export function freeId(routines: RoutineSummary[], stem: string): string {
  const taken = new Set(routines.map((r) => r.id));
  const base = stem.toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 58)
    || "routine";
  let id = base;
  for (let n = 2; taken.has(id); n++) id = `${base}-${n}`;
  return id;
}

export function RoutinesView({ routines, selected, onSelect, onAction }: {
  routines: RoutineSummary[] | null;
  selected: string | null; onSelect: (id: string) => void;
  /** Asked from a card's menu: the details panel does it. */
  onAction: (id: string, action: Action) => void;
}) {
  const [view, setView] = useState<View>({ kind: "all" });
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<Sort>("name");
  const [newId, setNewId] = useState("");
  const [menu, setMenu] = useState<string | null>(null);
  const list = routines ?? [];
  const folders = useMemo(() => [...new Set(list.map((r) => r.folder ?? UNFILED))]
    .filter((f) => f !== UNFILED).sort((a, b) => a.localeCompare(b)), [list]);
  const unfiled = list.filter((r) => !r.folder).length;
  const words = normalizeName(query).split(" ").filter(Boolean);
  const shown = list
    .filter((r) => view.kind === "all" ? true
      : view.kind === "folder" ? (r.folder ?? UNFILED) === view.folder
      : view.kind === "rig" ? !!r.rig : usageCount(r.used_by) === 0)
    .filter((r) => {
      if (!words.length) return true;
      const blocks = (r.lanes ?? []).flatMap((l) => l.blocks).join(" ");
      const hay = normalizeName(`${nameOf(r)} ${r.id} ${Object.keys(r.roles).join(" ")} `
        + `${Object.keys(r.params).join(" ")} ${blocks}`);
      return words.every((w) => hay.includes(w));
    })
    .sort((a, b) => sort === "used" ? usageCount(b.used_by) - usageCount(a.used_by)
      || nameOf(a).localeCompare(nameOf(b))
      : sort === "length" ? a.bars - b.bars || nameOf(a).localeCompare(nameOf(b))
      : nameOf(a).localeCompare(nameOf(b)));
  const idOk = ID_RE.test(newId) && !list.some((r) => r.id === newId);
  const is = (v: View) => JSON.stringify(v) === JSON.stringify(view);
  const chip = (v: View, label: string, n: number) => (
    <button key={label} className={`s-chip${is(v) ? " on" : ""}`} aria-pressed={is(v)}
            onClick={() => setView(v)}>{label} <span className="s-n">{n}</span></button>
  );

  // A menu closes on a click anywhere else, or Escape.
  useEffect(() => {
    if (!menu) return;
    const close = (e: Event) => {
      if (e instanceof KeyboardEvent && e.key !== "Escape") return;
      if (e instanceof MouseEvent && (e.target as Element | null)?.closest?.(".s-menu-wrap")) return;
      setMenu(null);
    };
    addEventListener("mousedown", close);
    addEventListener("keydown", close);
    return () => { removeEventListener("mousedown", close); removeEventListener("keydown", close); };
  }, [menu]);

  return (
    <section className="s-page" aria-label="routines">
      <div className="s-page-head">
        <div>
          <h1>Routines</h1>
          <span className="muted small">{list.length} routines: a few bars each, written for
            roles rather than fixtures, so they play on any rig.</span>
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

      <div className="s-toolbar">
        <span className="d-chips" role="group" aria-label="folders">
          {chip({ kind: "all" }, "All", list.length)}
          {folders.map((f) => chip({ kind: "folder", folder: f }, f,
                                   list.filter((r) => r.folder === f).length))}
          {unfiled > 0 && folders.length > 0 && chip({ kind: "folder", folder: UNFILED }, "Unfiled", unfiled)}
          {chip({ kind: "rig" }, "This rig only", list.filter((r) => r.rig).length)}
          {chip({ kind: "unused" }, "Unused", list.filter((r) => usageCount(r.used_by) === 0).length)}
        </span>
        <span className="grow" />
        <input type="search" value={query} onChange={(e) => setQuery(e.target.value)}
               placeholder="Filter by name, block, role or parameter" aria-label="filter routines" />
        <label className="small s-inline muted">Sort{" "}
          <select value={sort} aria-label="sort routines" onChange={(e) => setSort(e.target.value as Sort)}>
            <option value="name">Name</option>
            <option value="used">Most used</option>
            <option value="length">Length</option>
          </select>
        </label>
      </div>

      {routines == null && <p className="muted">Loading the show folder…</p>}
      <div className="s-cards">
        {shown.map((r) => {
          const used = usageCount(r.used_by);
          const tl = r.used_by?.timelines.length ?? 0;
          const sets = r.used_by?.templates.map((t) => t.name ?? t.id) ?? [];
          return (
            <article key={r.id} className={`s-card${r.id === selected ? " on" : ""}`}
                     aria-label={nameOf(r)} onClick={() => onSelect(r.id)}>
              <div className="s-lanes" aria-hidden="true">
                {(r.lanes ?? []).filter((l) => l.type !== "automation").slice(0, 4).map((l, i) => (
                  <span key={i} className={`s-lane ${SLOT_CLASS[l.target ?? ""] ?? "h"}`}>
                    {l.type === "hits" ? "hits" : (l.blocks.filter(Boolean).join(" · ") || l.target)}</span>
                ))}
              </div>
              <div className="s-card-head">
                <a className="s-link" href={`#studio/routine/${r.id}`}
                   onClick={(e) => e.stopPropagation()}>
                  <b>{nameOf(r)}</b>
                  <span className="muted mono">{r.id} · {r.bars} bar{r.bars === 1 ? "" : "s"}
                    {r.loop ? ", loops" : ", once"}</span>
                </a>
                <span className="s-menu-wrap">
                  <button className="s-icon" aria-label={`more for ${nameOf(r)}`}
                          aria-haspopup="menu" aria-expanded={menu === r.id}
                          onClick={(e) => { e.stopPropagation(); setMenu(menu === r.id ? null : r.id); }}>
                    ⋯</button>
                  {menu === r.id && (
                    <span className="s-menu" role="menu" aria-label={`${nameOf(r)} actions`}>
                      <a role="menuitem" href={`#studio/routine/${r.id}`}>Open</a>
                      {(["duplicate", "rename", "folder", "download", "delete"] as Action[]).map((a) => (
                        <button key={a} role="menuitem" className={a === "delete" ? "s-danger" : ""}
                                onClick={(e) => { e.stopPropagation(); setMenu(null); onAction(r.id, a); }}>
                          {ACTION_LABEL[a]}</button>
                      ))}
                    </span>
                  )}
                </span>
              </div>
              <div className="d-chips">
                {Object.entries(r.roles).map(([name, role]) => (
                  <span key={name} className="s-role" title={`binds to the ${role.default} tag`}>
                    {name}{role.optional ? "?" : ""}</span>))}
                {r.rig && <span className="d-badge">this rig only</span>}
                {r.folder && <span className="s-tag">{r.folder}</span>}
              </div>
              {Object.keys(r.params).length > 0 && (
                <span className="mono small muted">
                  {Object.keys(r.params).map((p) => `$${p}`).join(" ")}
                  {r.variations.length ? ` · ${r.variations.join(", ")}` : ""}</span>)}
              <span className={`small s-card-foot${used ? "" : " s-warn"}`}>
                {used ? [tl ? `in ${tl} timeline${tl === 1 ? "" : "s"}` : "",
                         ...sets, ...(r.used_by?.show.length ? ["idle routine"] : [])]
                  .filter(Boolean).join(" · ") : "Unused"}</span>
            </article>
          );
        })}
      </div>
      {routines != null && !shown.length && <p className="muted">No routine matches.</p>}
    </section>
  );
}

export type Action = "duplicate" | "rename" | "folder" | "download" | "delete";

const ACTION_LABEL: Record<Action, string> = {
  duplicate: "Duplicate…", rename: "Rename…", folder: "Move to folder…",
  download: "Download the file", delete: "Delete…",
};

// -- the selected routine -------------------------------------------------------------

async function readRoutine(id: string): Promise<{ doc: RoutineDoc; rev: string }> {
  return apiFetch<{ doc: RoutineDoc; rev: string }>(`/api/routines/${id}`);
}

/** Save a file this browser made, with the name it should have. */
function download(name: string, doc: unknown): void {
  const url = URL.createObjectURL(new Blob([`${JSON.stringify(doc, null, 2)}\n`],
                                           { type: "application/json" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function RoutineDetail({ engine, r, routines, action, actionKey, onDone, onSelect }: {
  engine: Engine; r: RoutineSummary; routines: RoutineSummary[];
  /** What the card's menu asked for, to open on; `actionKey` changes each
   *  time it is asked, so asking twice opens it twice. */
  action: Action | null; actionKey: number;
  /** Something was written, and what: re-read the folder, and say so where
   *  it survives this panel (a rename or a delete takes it away). */
  onDone: (said: string) => void;
  onSelect: (id: string) => void;
}) {
  const canWrite = engine.tier === "configure";
  const used = r.used_by ?? { timelines: [], templates: [], show: [] };
  const count = usageCount(used);
  const folders = [...new Set(routines.map((x) => x.folder).filter((f): f is string => !!f))].sort();
  const [mode, setMode] = useState<Action | null>(action);
  const [dupId, setDupId] = useState(() => freeId(routines, `${r.id}-copy`));
  const [toId, setToId] = useState(r.id);
  const [folder, setFolder] = useState(r.folder ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const field = useRef<HTMLInputElement | null>(null);
  useEffect(() => { setMode(action); }, [action, actionKey]);
  useEffect(() => { if (mode && mode !== "download" && mode !== "delete") field.current?.focus(); },
            [mode]);
  // A download needs nothing more said: do it.
  useEffect(() => {
    if (mode !== "download") return;
    setMode(null);
    readRoutine(r.id).then(({ doc }) => download(`${r.id}.json`, doc))
      .catch((e: Error) => setError(e.message));
  }, [mode, r.id]);

  /** One write: `work` answers with what it did, or throws why not. */
  const run = async (work: () => Promise<string>) => {
    setBusy(true);
    setError(null);
    try {
      const said = await work();
      setMode(null);
      onDone(said);
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

  const duplicate = () => run(async () => {
    const { doc } = await readRoutine(r.id);
    const copy: RoutineDoc = { ...structuredClone(doc), id: dupId, name: `${nameOf(r)} (copy)` };
    await ask({ type: "routine_save", doc: copy, base_rev: "" });
    onSelect(dupId);
    return `Duplicated ${r.id} as ${dupId}.`;
  });
  const rename = () => run(async () => {
    const { written } = await ask({ type: "routine_rename", routine: r.id, to: toId,
                                    base_rev: r.rev ?? "" }) as { written: string[] };
    onSelect(toId);
    return `Renamed ${r.id} to ${toId}: ${written.length} file${written.length === 1 ? "" : "s"} written.`;
  });
  const move = () => run(async () => {
    const { doc, rev } = await readRoutine(r.id);
    const next: RoutineDoc = structuredClone(doc);
    const to = folder.trim();
    if (to) next.folder = to; else delete next.folder;
    await ask({ type: "routine_save", doc: next, base_rev: rev });
    return to ? `Moved ${r.id} to ${to}.` : `Took ${r.id} out of its folder.`;
  });
  const remove = () => run(async () => {
    await ask({ type: "routine_delete", routine: r.id, base_rev: r.rev ?? "" });
    return `Deleted routines/${r.id}.json.`;
  });

  const dupOk = ID_RE.test(dupId) && !routines.some((x) => x.id === dupId);
  const toOk = ID_RE.test(toId) && toId !== r.id && !routines.some((x) => x.id === toId);
  const refs = used.timelines.length + used.templates.length + used.show.length;

  return (
    <div className="s-detail" aria-label="selected routine">
      <div>
        <span className="s-kicker">Routine{r.folder ? ` · ${r.folder}` : ""}</span>
        <h2>{nameOf(r)}</h2>
        <span className="muted mono small">{r.id} · {r.bars} bars{r.loop ? ", loops" : ", once"}</span>
        {r.rig && <span className="d-badge">this rig only ({r.rig})</span>}
      </div>
      <a className="s-button d-primary s-wide" href={`#studio/routine/${r.id}`}>Open the routine</a>

      <section aria-label="where it is used" className="s-uses">
        <b className="small">Where it's used</b>
        {!count && <span className="muted small">Nowhere yet: no timeline places it, no template
          set picks it.</span>}
        {used.timelines.map((t) => (
          <a key={t.track} className="s-use" href={`#studio/track/${t.track}`}>
            <b>{t.title ?? t.track}</b>
            <span className="muted small">{t.clips} clip{t.clips === 1 ? "" : "s"}
              {t.variations.length ? ` · ${t.variations.join(", ")}` : ""}</span>
          </a>
        ))}
        {used.templates.map((t) => (
          <div key={t.id} className="s-use">
            <b>{t.name ?? t.id} <span className="muted small">template set</span></b>
            <span className="muted small">{t.where.join(", ")}</span>
          </div>
        ))}
        {used.show.map((s) => (
          <div key={s} className="s-use"><b>Show settings</b><span className="muted small">{s}</span></div>
        ))}
      </section>

      {error && <p className="small d-error" role="alert">{error}</p>}
      {!canWrite && <p className="muted small">Changing routines writes the show folder: open
        Studio from the link the engine printed.</p>}

      <div className="s-actions">
        <div className="s-action">
          <button className={mode === "duplicate" ? "on" : ""} aria-expanded={mode === "duplicate"}
                  onClick={() => setMode(mode === "duplicate" ? null : "duplicate")}>Duplicate</button>
          {mode === "duplicate" && (
            <form className="d-form" onSubmit={(e) => { e.preventDefault(); if (dupOk) void duplicate(); }}>
              <input ref={field} value={dupId} aria-label="id of the copy"
                     onChange={(e) => setDupId(e.target.value.trim())} />
              <button type="submit" className="d-primary" disabled={!dupOk || busy || !canWrite}>
                Make the copy</button>
              <span className="muted small">Everything, under a new id. Nothing that uses
                {" "}{r.id} changes.</span>
            </form>
          )}
        </div>
        <div className="s-action">
          <button className={mode === "rename" ? "on" : ""} aria-expanded={mode === "rename"}
                  onClick={() => setMode(mode === "rename" ? null : "rename")}>Rename</button>
          {mode === "rename" && (
            <form className="d-form" onSubmit={(e) => { e.preventDefault(); if (toOk) void rename(); }}>
              <input ref={field} value={toId} aria-label="new id"
                     onChange={(e) => setToId(e.target.value.trim())} />
              <button type="submit" className="d-primary" disabled={!toOk || busy || !canWrite}>
                Rename it</button>
              <span className="muted small">{refs
                ? `Also rewrites the ${refs} file${refs === 1 ? "" : "s"} that use it, in one go.`
                : "Nothing uses it, so only its own file changes."} The id is its file name;
                its display name is set in the routine.</span>
            </form>
          )}
        </div>
        <div className="s-action">
          <button className={mode === "folder" ? "on" : ""} aria-expanded={mode === "folder"}
                  onClick={() => setMode(mode === "folder" ? null : "folder")}>Move to folder</button>
          {mode === "folder" && (
            <form className="d-form" onSubmit={(e) => { e.preventDefault(); void move(); }}>
              <input ref={field} value={folder} list="s-folders" aria-label="folder"
                     placeholder="none: unfiled" onChange={(e) => setFolder(e.target.value)} />
              <datalist id="s-folders">{folders.map((f) => <option key={f} value={f} />)}</datalist>
              <button type="submit" className="d-primary"
                      disabled={busy || !canWrite || folder.trim() === (r.folder ?? "")}>Move</button>
              <span className="muted small">A folder is a name: a new one appears when a routine
                is put in it.</span>
            </form>
          )}
        </div>
        <div className="s-action">
          <button onClick={() => setMode("download")}>Download the file</button>
        </div>
        <div className="s-action">
          {count ? (
            <button disabled title="Take it out of the timelines and sets above first">
              Delete: used in {count} place{count === 1 ? "" : "s"}</button>
          ) : mode === "delete" ? (
            <div className="d-form" role="group" aria-label="confirm delete">
              <span className="small">Delete routines/{r.id}.json? Studio cannot undo it.</span>
              <button className="d-bad" disabled={busy || !canWrite} onClick={() => void remove()}>
                Delete it</button>
              <button onClick={() => setMode(null)}>Keep it</button>
            </div>
          ) : (
            <button className="d-bad" onClick={() => setMode("delete")}>Delete…</button>
          )}
        </div>
      </div>
    </div>
  );
}
