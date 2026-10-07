import { useEffect, useMemo, useRef, useState } from "react";
import { apiFetch } from "../useEngine";
import type { Engine } from "./Designer";
import { ID_RE, freeId as freeIdAmong, normalizeName, usageCount } from "./model";
import type { RoutineDoc, RoutineSummary } from "./model";
import { Badge, DetailHead, DetailSection, MoreMenu, Task, useWrite } from "./detail";
import type { MenuItem } from "./detail";

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

const UNFILED = "";
type View = { kind: "all" } | { kind: "folder"; folder: string } | { kind: "rig" }
  | { kind: "unused" };
type Sort = "name" | "used" | "length";

/** A routine's files' rows, as a strip per row: movement, color, level. */
const SLOT_CLASS: Record<string, string> = { movement: "m", color: "c", level: "l" };

export function nameOf(r: RoutineSummary): string { return r.name || r.id; }

/** An id not taken by any routine, from a stem. */
export function freeId(routines: RoutineSummary[], stem: string): string {
  return freeIdAmong(routines.map((r) => r.id), stem);
}

export function RoutinesView({ routines, selected, onSelect, onAction, onNew }: {
  routines: RoutineSummary[] | null;
  /** Open + New's dialog, for a routine. */
  onNew: () => void;
  selected: string | null; onSelect: (id: string) => void;
  /** Asked from a card's menu: the details panel does it. */
  onAction: (id: string, action: Action) => void;
}) {
  const [view, setView] = useState<View>({ kind: "all" });
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<Sort>("name");
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
  const is = (v: View) => JSON.stringify(v) === JSON.stringify(view);
  const chip = (v: View, label: string, n: number) => (
    <button key={label} className={`s-chip${is(v) ? " on" : ""}`} aria-pressed={is(v)}
            onClick={() => setView(v)}>{label} <span className="s-n">{n}</span></button>
  );

  return (
    <section className="s-page" aria-label="routines">
      <div className="s-page-head">
        <div>
          <h1>Routines</h1>
          <span className="muted small">{list.length} routines: a few bars each, written for
            roles rather than fixtures, so they play on any rig.</span>
        </div>
        <span className="grow" />
        <button onClick={onNew}>New routine…</button>
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
                <MoreMenu label={nameOf(r)}
                          items={[{ label: "Open", href: `#studio/routine/${r.id}` },
                                  ...actionItems(r, (a) => onAction(r.id, a))]} />
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
const ACTIONS: Action[] = ["duplicate", "rename", "folder", "download", "delete"];

const ACTION_LABEL: Record<Action, string> = {
  duplicate: "Duplicate…", rename: "Rename…", folder: "Move to folder…",
  download: "Download the file", delete: "Delete…",
};

/** A routine's menu, on its card and in its details: what can be done to its
 *  file. One that is used cannot be deleted, and says so. */
function actionItems(r: RoutineSummary, pick: (a: Action) => void): MenuItem[] {
  const count = usageCount(r.used_by);
  return ACTIONS.map((a) => ({
    label: ACTION_LABEL[a], danger: a === "delete", onClick: () => pick(a),
    ...(a === "delete" && count > 0
      ? { disabled: true, note: `Used in ${count} place${count === 1 ? "" : "s"}` } : {}),
  }));
}

// -- the selected routine -------------------------------------------------------------

async function readRoutine(id: string): Promise<{ doc: RoutineDoc; rev: string }> {
  return apiFetch<{ doc: RoutineDoc; rev: string }>(`/api/routines/${id}`);
}

/** Save a file this browser made, with the name it should have. */
export function download(name: string, doc: unknown): void {
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

export function RoutineDetail({ engine, r, routines, action, actionKey, onAsked, onDone,
                               onSelect }: {
  engine: Engine; r: RoutineSummary; routines: RoutineSummary[];
  /** What the card's menu asked for, to open on; `actionKey` changes each
   *  time it is asked, so asking twice opens it twice. */
  action: Action | null; actionKey: number;
  /** The ask was taken: forget it, so this panel mounting again (the details
   *  shown again, a look at another page) does not download twice or reopen
   *  a form that was closed. */
  onAsked: () => void;
  /** Something was written, and what: re-read the folder, and say so where
   *  it survives this panel (a rename or a delete takes it away). */
  onDone: (said: string) => void;
  onSelect: (id: string) => void;
}) {
  const canWrite = engine.tier === "configure";
  const used = r.used_by ?? { timelines: [], templates: [], show: [] };
  const count = usageCount(used);
  const folders = [...new Set(routines.map((x) => x.folder).filter((f): f is string => !!f))].sort();
  const [mode, setMode] = useState<Action | null>(null);
  const [dupId, setDupId] = useState(() => freeId(routines, `${r.id}-copy`));
  const [toId, setToId] = useState(r.id);
  const [folder, setFolder] = useState(r.folder ?? "");
  const { busy, error, setError, run: write, ask } = useWrite(engine, onDone);
  const field = useRef<HTMLInputElement | null>(null);
  useEffect(() => {
    if (!action) return;
    setMode(action);
    onAsked();
    // Only a new ask opens a form; the callback is the parent's.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [action, actionKey]);
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
  const run = (work: () => Promise<string>) => write(work, () => setMode(null));

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
  const close = () => { setMode(null); setError(null); };

  return (
    <div className="s-detail" aria-label="selected routine">
      <DetailHead kind={`Routine${r.folder ? ` · ${r.folder}` : ""}`} title={nameOf(r)}
                  meta={<span className="mono">{r.id} · {r.bars} bar{r.bars === 1 ? "" : "s"}
                    {r.loop ? ", loops" : ", once"}</span>}
                  badges={r.rig && <span title={`Uses ${r.rig}'s own looks or presets`}>
                    <Badge tone="warn">This rig only</Badge></span>}
                  menu={<MoreMenu label={nameOf(r)}
                                  items={actionItems(r, (a) => { setError(null); setMode(a); })} />} />
      <a className="s-button d-primary s-wide" href={`#studio/routine/${r.id}`}>Open the routine</a>

      {mode === "duplicate" && (
        <Task title={`Duplicate ${r.id}`} readOnly={!canWrite} onCancel={close}>
          <form className="d-form" onSubmit={(e) => { e.preventDefault(); if (dupOk) void duplicate(); }}>
            <input ref={field} value={dupId} aria-label="id of the copy"
                   onChange={(e) => setDupId(e.target.value.trim())} />
            <button type="submit" className="d-primary" disabled={!dupOk || busy || !canWrite}>
              Make the copy</button>
          </form>
          <span className="muted small">Everything, under a new id. Nothing that uses {r.id} changes.</span>
        </Task>
      )}
      {mode === "rename" && (
        <Task title={`Rename ${r.id}`} readOnly={!canWrite} onCancel={close}>
          <form className="d-form" onSubmit={(e) => { e.preventDefault(); if (toOk) void rename(); }}>
            <input ref={field} value={toId} aria-label="new id"
                   onChange={(e) => setToId(e.target.value.trim())} />
            <button type="submit" className="d-primary" disabled={!toOk || busy || !canWrite}>
              Rename it</button>
          </form>
          <span className="muted small">{refs
            ? `Also rewrites the ${refs} file${refs === 1 ? "" : "s"} that use it, in one go.`
            : "Nothing uses it, so only its own file changes."} The id is its file name;
            its display name is set in the routine.</span>
        </Task>
      )}
      {mode === "folder" && (
        <Task title="Move to folder" readOnly={!canWrite} onCancel={close}>
          <form className="d-form" onSubmit={(e) => { e.preventDefault(); void move(); }}>
            <input ref={field} value={folder} list="s-folders" aria-label="folder"
                   placeholder="none: unfiled" onChange={(e) => setFolder(e.target.value)} />
            <datalist id="s-folders">{folders.map((f) => <option key={f} value={f} />)}</datalist>
            <button type="submit" className="d-primary"
                    disabled={busy || !canWrite || folder.trim() === (r.folder ?? "")}>Move</button>
          </form>
          <span className="muted small">A folder is a name: a new one appears when a routine
            is put in it.</span>
        </Task>
      )}
      {mode === "delete" && !count && (
        <Task title={`Delete ${r.id}`} label="confirm delete" readOnly={!canWrite} onCancel={close}>
          <span className="small">Delete routines/{r.id}.json? Studio cannot undo it.</span>
          <div className="d-form">
            <button className="d-bad" disabled={busy || !canWrite} onClick={() => void remove()}>
              Delete it</button>
            <button onClick={close}>Keep it</button>
          </div>
        </Task>
      )}
      {error && <p className="small d-error" role="alert">{error}</p>}

      <DetailSection title="Used in" label="where it is used" count={count}>
        {!count && <span className="muted small">Nowhere yet: no timeline places it, no template
          set picks it.</span>}
        <div className="s-uses">
          {used.timelines.map((t) => (
            <a key={t.track} className="s-use" href={`#studio/track/${t.track}`}>
              <b>{t.title ?? t.track}</b>
              <span className="muted small">{t.clips} clip{t.clips === 1 ? "" : "s"}
                {t.variations.length ? ` · ${t.variations.join(", ")}` : ""}</span>
            </a>
          ))}
          {used.templates.map((t) => (
            <a key={t.id} className="s-use" href={`#studio/templates/${t.id}`}>
              <b>{t.name ?? t.id}</b>
              <span className="muted small">template set · {t.where.join(", ")}</span>
            </a>
          ))}
          {used.show.map((s) => (
            <a key={s} className="s-use" href="#studio/show">
              <b>Show settings</b><span className="muted small">{s}</span></a>
          ))}
        </div>
      </DetailSection>
    </div>
  );
}
