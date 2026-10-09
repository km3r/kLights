import { useEffect, useMemo, useState } from "react";
import type { Dispatch, ReactNode, SetStateAction } from "react";
import type { Engine } from "./Designer";
import { BLOCK_ARGS, BLOCK_SLOT, SLOTS, freeName, hexColor, lookUses, normalizeName } from "./model";
import type { ArgSpec, LookSummary, LookUsage, LooksList, Slot } from "./model";
import { ArgField } from "./RoutineEditor";
import { download } from "./Routines";
import { Badge, DetailHead, DetailSection, MoreMenu, READ_ONLY, Task, useWrite } from "./detail";

/**
 * This rig's looks: what the console's picker offers, and what a routine's
 * `look` block plays.
 *
 * They are the EVENT's, not the show folder's -- a look names fixture groups
 * and offsets from this rig's own calibration -- and they come from two files:
 *
 *   block looks     one block and its arguments, in parametric_looks.json.
 *                   Made, changed, renamed and deleted here.
 *   stored looks    tables ported out of QLC+ into looks.json. That file is
 *                   generated, so a stored look is never changed: it can be
 *                   hidden from the picker, and where one block says the same
 *                   thing (one color, one level, one offset for every head)
 *                   it can be remade as a block look, which can.
 *
 * Every write is the engine's (`look_save`, `look_delete`, `look_hide`), and
 * quotes the rev the list was read at. The engine reloads the library as it
 * writes, so a saved look is on the console's picker at once -- and a rename
 * moves everything that names the look, the show folder's files included.
 */

const SLOT_NAME: Record<Slot, string> = { movement: "Movement", color: "Color", level: "Level" };
/** Where this page is, for whatever sends someone to it. */
export const LOOKS_HASH = "#studio/looks";
/** The block a new look is offered first: the one most looks are. */
export const FIRST_BLOCK = "orbit";
/** Why the original is hidden, when a stored look is remade as a block look. */
const REMADE_NOTE = "Remade as a block look in Studio.";
const NO_USE: LookUsage = { cues: [], presets: [], routines: [], timelines: [], templates: [],
                            hides: [] };

/** The blocks a look can be: every block with a slot of its own. */
export function lookBlocks(slot?: Slot): string[] {
  return Object.keys(BLOCK_ARGS).filter((b) => BLOCK_SLOT[b] != null && (!slot || BLOCK_SLOT[b] === slot));
}

/** What a new look of this block starts with: every argument the engine has a
 *  default for, written out -- so the file says what the look is, rather than
 *  leaving it to whatever the block defaults to next year. */
export function startingArgs(block: string): Record<string, unknown> {
  const args: Record<string, unknown> = {};
  for (const spec of BLOCK_ARGS[block] ?? []) {
    if (spec.default !== undefined && spec.default !== null) args[spec.name] = structuredClone(spec.default);
  }
  return args;
}

/** A color as a look stores it: [r, g, b] of 0..1, which is what the console's
 *  own sliders turn. A palette role ("@primary") stays as it is. */
function storedColor(v: unknown): unknown {
  if (typeof v !== "string" || !/^#[0-9a-f]{6}$/i.test(v)) return v;
  return [1, 3, 5].map((i) => Math.round(parseInt(v.slice(i, i + 2), 16) / 255 * 1000) / 1000);
}

function storedArg(spec: ArgSpec, v: unknown): unknown {
  if (spec.kind === "color") return storedColor(v);
  if (spec.kind === "colors" && Array.isArray(v)) return v.map(storedColor);
  return v;
}

/** The colors a look states outright, for a row's swatches. Only what its
 *  block DECLARES as a color: a room point is three numbers in 0..1 as well,
 *  and an aim_points look is not three shades of olive. */
function swatchesOf(l: LookSummary): string[] {
  const out: string[] = [];
  const take = (v: unknown) => { const hex = hexColor(v); if (hex && !out.includes(hex)) out.push(hex); };
  for (const rgb of l.stored?.swatches ?? []) take(rgb);
  for (const spec of BLOCK_ARGS[l.block ?? ""] ?? []) {
    const v = l.args?.[spec.name];
    if (spec.kind === "color") take(v);
    else if (spec.kind === "colors" && Array.isArray(v)) v.forEach(take);
  }
  return out.slice(0, 6);
}

/** What a look is, in a few words: its block, or what its table holds. */
function whatOf(l: LookSummary): string {
  if (l.source === "block") return `${l.block} block`;
  const s = l.stored;
  return s ? `${s.what}${s.steps ? ` · ${s.steps} steps over ${s.bars} bars` : ""}` : "stored";
}

function usedLine(u: LookUsage): string {
  const n = (count: number, one: string) => count ? `${count} ${one}${count === 1 ? "" : "s"}` : "";
  return [n(u.cues.length, "cue"), n(u.presets.length, "preset"), n(u.routines.length, "routine"),
          n(u.timelines.length, "timeline"), n(u.templates?.length ?? 0, "set")]
    .filter(Boolean).join(" · ");
}

/** The movement look on stage and what fills the other slots, by name. */
function onStage(engine: Engine): Set<string> {
  const sel = engine.state?.selection;
  const names = new Set<string>();
  for (const slot of SLOTS) for (const n of Object.values(sel?.[slot] ?? {})) names.add(n);
  for (const n of engine.state?.movement_extra ?? []) names.add(n);
  return names;
}

type Show = "all" | "block" | "stored" | "hidden" | "unused";
type Sort = "name" | "picker";

export function LooksView({ engine, list, error, selected, unsaved, onSelect, onNew, onDone }: {
  engine: Engine; list: LooksList | null; error: string | null;
  selected: string | null; onSelect: (name: string) => void;
  /** The looks with edits not yet saved: kept while another look is looked at. */
  unsaved: ReadonlySet<string>;
  /** Open + New's dialog, for a look. */
  onNew: () => void;
  onDone: (said: string) => void;
}) {
  const [slot, setSlot] = useState<Slot | "all">("all");
  const [show, setShow] = useState<Show>("all");
  const [group, setGroup] = useState("");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<Sort>("name");
  // A chase's own steps are looks too (the port keeps each), and there are
  // dozens: filed away, as the console's picker files them under their chase,
  // until asked for or searched for.
  const [steps, setSteps] = useState(false);
  const { busy, error: failed, run, ask } = useWrite(engine, onDone);
  const every = list?.looks ?? [];
  const stepCount = every.filter((l) => l.step_of).length;
  const live = onStage(engine);
  const words = normalizeName(query).split(" ").filter(Boolean);
  const looks = every.filter((l) => steps || words.length > 0 || !l.step_of || l.name === selected
    || unsaved.has(l.name));
  const inSlot = looks.filter((l) => slot === "all" || l.slot === slot);
  const is: Record<Show, (l: LookSummary) => boolean> = {
    all: () => true,
    block: (l) => l.source === "block",
    stored: (l) => l.source === "stored",
    hidden: (l) => l.retired,
    unused: (l) => !l.retired && lookUses(l.used_by) === 0,
  };
  const shown = inSlot
    .filter(is[show])
    .filter((l) => !group || l.groups.includes(group))
    .filter((l) => {
      if (!words.length) return true;
      const hay = normalizeName(`${l.name} ${l.block ?? ""} ${l.stored?.what ?? ""} ${l.groups.join(" ")} ${l.notes}`);
      return words.every((w) => hay.includes(w));
    });
  const rows = sort === "name" ? [...shown].sort((a, b) => a.name.localeCompare(b.name)) : shown;
  const blocks = every.filter((l) => l.source === "block").length;
  const canWrite = engine.tier === "configure";

  const chip = <T extends string>(value: T, current: T, set: (v: T) => void, label: string, n: number) => (
    <button key={value} className={`s-chip${value === current ? " on" : ""}`}
            aria-pressed={value === current} onClick={() => set(value)}>
      {label} <span className="s-n">{n}</span></button>
  );

  return (
    <section className="s-page" aria-label="looks">
      <div className="s-page-head">
        <div>
          <h1>Looks</h1>
          <span className="muted small">
            {list ? <>{every.length} looks on <b>{list.event}</b>: what the console's picker
              offers, and what a routine's look block plays. {blocks} are built from a block
              and are edited here; {every.length - blocks} are stored tables from QLC+, which
              can be hidden, or remade as a block.</>
              : "This rig's looks: what the console's picker offers."}</span>
        </div>
        <span className="grow" />
        <button onClick={onNew}>New look…</button>
      </div>

      {list && (list.stale || list.problem) && (
        <div className="s-note warn" role="alert">
          <b>{list.problem ? `${list.file} on disk does not load, so the engine is running the `
            + "library it had." : `${list.file} changed on disk since the engine read it.`}</b>
          {list.problem ? <span className="mono small s-pre">{list.problem}</span>
            : "The engine reads it again by itself in a moment. What is listed here is the "
              + "library that is running; a save before then would be refused."}
          <div className="d-form">
            <button className="d-primary" disabled={busy || !canWrite}
                    onClick={() => void run(async () => {
                      await ask({ type: "looks_reload" });
                      return `Read ${list.file} again.`;
                    })}>Read the looks again</button>
            {!canWrite && <span className="muted small">{READ_ONLY}</span>}
          </div>
        </div>
      )}
      {(error ?? failed) && <p className="d-error" role="alert">{error ?? failed}</p>}

      <div className="s-toolbar">
        <span className="d-chips" role="group" aria-label="slot">
          {chip<Slot | "all">("all", slot, setSlot, "All", looks.length)}
          {SLOTS.map((s) => chip<Slot | "all">(s, slot, setSlot, SLOT_NAME[s],
                                               looks.filter((l) => l.slot === s).length))}
        </span>
        <span className="d-chips" role="group" aria-label="kind">
          {chip<Show>("all", show, setShow, "Every kind", inSlot.length)}
          {chip<Show>("block", show, setShow, "Blocks", inSlot.filter(is.block).length)}
          {chip<Show>("stored", show, setShow, "Stored", inSlot.filter(is.stored).length)}
          {chip<Show>("hidden", show, setShow, "Hidden", inSlot.filter(is.hidden).length)}
          {chip<Show>("unused", show, setShow, "Unused", inSlot.filter(is.unused).length)}
        </span>
        {stepCount > 0 && (
          <label className="small s-inline muted" title="Each step of a stored chase is a look of its own">
            <input type="checkbox" checked={steps} onChange={(e) => setSteps(e.target.checked)} />
            Chase steps <span className="s-n">{stepCount}</span></label>
        )}
        <span className="grow" />
        {(list?.groups.length ?? 0) > 1 && (
          <label className="small s-inline muted">Fixtures{" "}
            <select value={group} aria-label="filter by fixture group"
                    onChange={(e) => setGroup(e.target.value)}>
              <option value="">any</option>
              {list!.groups.map((g) => <option key={g} value={g}>{g}</option>)}
            </select>
          </label>
        )}
        <input type="search" value={query} onChange={(e) => setQuery(e.target.value)}
               placeholder="Filter by name, block or note" aria-label="filter looks" />
        <label className="small s-inline muted">Sort{" "}
          <select value={sort} aria-label="sort looks" onChange={(e) => setSort(e.target.value as Sort)}>
            <option value="name">Name</option>
            <option value="picker">As the picker lists them</option>
          </select>
        </label>
      </div>

      {list == null && !error && <p className="muted">Loading the looks…</p>}
      {list != null && !every.length && (
        <p className="muted">This event has no looks yet, so the engine is running its starter
          set. Make the first one with New look.</p>)}
      {rows.length > 0 && (
        <div className="s-table-wrap">
          <table className="s-table s-looks">
            <thead>
              <tr><th>Look</th><th>Slot</th><th>Fixtures</th><th>Used by</th>
                <th><span className="sr-only">State</span></th></tr>
            </thead>
            <tbody>
              {rows.map((l) => {
                const colors = swatchesOf(l);
                const used = usedLine(l.used_by);
                return (
                  <tr key={l.name} className={`${l.name === selected ? "sel" : ""}${l.retired ? " s-dim" : ""}`}
                      onClick={() => onSelect(l.name)}>
                    <td className="s-look">
                      <button className="s-look-name" aria-pressed={l.name === selected}
                              onClick={(e) => { e.stopPropagation(); onSelect(l.name); }}>
                        <b>{l.name}</b></button>
                      <span className="muted small">
                        {colors.length > 0 && (
                          <span className="s-swatches" aria-hidden="true">
                            {colors.map((c) => <i key={c} style={{ background: c }} />)}</span>)}
                        {whatOf(l)}{l.step_of ? ` · a step of ${l.step_of}` : ""}
                        {l.cued ? " · drives the dimmer itself" : ""}</span>
                    </td>
                    <td><span className={`s-slot ${l.slot[0]}`}>{SLOT_NAME[l.slot]}</span></td>
                    <td className="small">{l.groups.join(", ")
                      || <span className="muted">{l.slot === "movement" ? "the movers" : "everything"}</span>}</td>
                    <td className="small">{used || <span className="muted">nothing</span>}</td>
                    <td className="s-look-state">
                      {unsaved.has(l.name) && <Badge tone="info">Unsaved</Badge>}
                      {live.has(l.name) && <Badge tone="good">On stage</Badge>}
                      {l.retired && <Badge>Hidden</Badge>}
                      {l.source === "stored" ? <span className="s-tag">stored</span>
                        : l.supersedes && <span className="s-tag" title="Takes over the stored look of the same name">
                          takes over</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {list != null && every.length > 0 && !rows.length && <p className="muted">No look matches.</p>}
    </section>
  );
}

// -- the selected look --------------------------------------------------------------

/** A block look as it is being edited. The page keeps one per look that has
 *  unsaved changes (`LookDraft`), so looking at another look -- to compare, to
 *  copy a number -- does not throw the work away. */
export type LookDraft = Edit;

interface Edit {
  name: string;
  block: string;
  args: Record<string, unknown>;
  groups: string[];
  notes: string;
}

function editOf(l: LookSummary): Edit {
  return { name: l.name, block: l.block ?? "", args: structuredClone(l.args ?? {}),
           groups: [...l.groups], notes: l.notes };
}

/** A look as the engine would store it, so "typed and put back" is clean. */
function savedForm(e: Edit): string {
  return JSON.stringify({ name: e.name.trim().replace(/\s+/g, " "), block: e.block, args: e.args,
                          groups: e.groups, notes: e.notes.trim() });
}

function lookDoc(e: Edit): Record<string, unknown> {
  const notes = e.notes.trim();
  return { name: e.name.trim().replace(/\s+/g, " "), block: e.block, groups: e.groups,
           args: e.args, ...(notes ? { notes } : {}) };
}

function Uses({ l }: { l: LookSummary }) {
  const u = l.used_by ?? NO_USE;
  const count = lookUses(u);
  return (
    <DetailSection title="Used by" label="where it is used" count={count}>
      {!count && <span className="muted small">Nothing names it: no cue or preset puts it in a
        slot, no routine, timeline or template set plays it. It is on the console's picker all the same
        {l.retired ? ", behind the hidden toggle" : ""}.</span>}
      <div className="s-uses">
        {u.cues.map((c) => (
          <span key={`c:${c}`} className="s-use"><b>{c}</b><span className="muted small">cue</span></span>))}
        {u.presets.map((p) => (
          <span key={`p:${p}`} className="s-use"><b>{p}</b><span className="muted small">preset</span></span>))}
        {u.routines.map((r) => (
          <a key={`r:${r.id}`} className="s-use" href={`#studio/routine/${r.id}`}>
            <b>{r.name ?? r.id}</b><span className="muted small">routine</span></a>))}
        {u.timelines.map((t) => (
          <a key={`t:${t.track}`} className="s-use" href={`#studio/track/${t.track}`}>
            <b>{t.title ?? t.track}</b><span className="muted small">timeline</span></a>))}
        {(u.templates ?? []).map((t) => (
          <a key={`s:${t.id}`} className="s-use" href={`#studio/templates/${t.id}`}>
            <b>{t.name ?? t.id}</b><span className="muted small">template set</span></a>))}
      </div>
      {u.hides.length > 0 && (
        <span className="muted small">Hidden in its favour: {u.hides.join(", ")}.</span>)}
    </DetailSection>
  );
}

/** A stored look's own section: what its table holds, and the way out of it. */
function StoredLook({ l, onRemake }: { l: LookSummary; onRemake: () => void }) {
  return (
    <DetailSection title="What it is">
      <span className="small">A table ported from QLC+: {l.stored?.what ?? "a stored look"}.
        It plays exactly as it was stored, and has no arguments to change.</span>
      {l.notes && <span className="muted small">{l.notes}</span>}
      {l.block_version ? (
        <>
          <button className="d-primary s-wide" onClick={onRemake}>Make a block look from it…</button>
          <span className="muted small">One <b>{l.block_version.block}</b> block says the same
            thing, and a block look can be changed here and turned on the console.</span>
        </>
      ) : (
        <span className="muted small">No single block says the same thing, so there is nothing
          to remake it from. To replace it, make a new look and hide this one.</span>
      )}
    </DetailSection>
  );
}

/** A block look's editor: its block and arguments, the fixtures it writes and
 *  its notes. It only changes the edit; saving is the panel's. */
function BlockEditor({ engine, l, list, edit, setEdit }: {
  engine: Engine; l: LookSummary; list: LooksList;
  edit: Edit; setEdit: Dispatch<SetStateAction<Edit>>;
}) {
  const specs = useMemo(() => (BLOCK_ARGS[edit.block] ?? []).map((spec) => {
    // An absolute angle spans what this rig's heads can reach, as on the console.
    const span = spec.reach ? engine.state?.reach?.[spec.reach as "bearing" | "elevation"] : undefined;
    return span ? { ...spec, min: span[0], max: span[1] } : spec;
  }), [edit.block, engine.state?.reach]);
  const tuned = engine.state?.look_params?.[l.name];
  const tunedKeys = Object.keys(tuned ?? {});
  // The groups it can be given: the rig's, and any it names that the rig lacks.
  const groups = [...list.groups, ...edit.groups.filter((g) => !list.groups.includes(g))];
  const setArg = (spec: ArgSpec, v: unknown) => setEdit((e) => {
    const args = { ...e.args };
    if (v === undefined) delete args[spec.name]; else args[spec.name] = storedArg(spec, v);
    return { ...e, args };
  });
  return (
    <>
      {l.supersedes && (
        <p className="s-note">
          <b>Takes over the stored look of the same name.</b>
          The cues and presets that name it play this instead.{" "}
          {l.exact === false ? "It has been changed since, on purpose: it no longer says "
            + "what the stored look says."
            : "It still says exactly what the stored look says; change it and that is "
              + "recorded, so nothing holds it to the original."}</p>)}
      <DetailSection title="Block">
        <label className="s-field">
          <select value={edit.block} aria-label="block"
                  onChange={(e) => setEdit({ ...edit, block: e.target.value,
                                             args: startingArgs(e.target.value) })}>
            {lookBlocks(l.slot).map((b) => <option key={b} value={b}>{b}</option>)}
          </select>
          <span className="muted small">The {SLOT_NAME[l.slot].toLowerCase()} blocks: the same ones
            a routine is built from. Another block starts from its own defaults.</span>
        </label>
        <div className="s-args">
          {specs.map((spec) => (
            <ArgField key={`${edit.block}:${spec.name}`} spec={spec} value={edit.args[spec.name]}
                      params={[]} engine={engine} onChange={(v) => setArg(spec, v)} />))}
        </div>
        {tunedKeys.length > 0 && (
          <div className="s-note info">
            <b>The console has it turned.</b>
            {tunedKeys.map((k) => `${k} ${JSON.stringify(tuned![k])}`).join(", ")}, over what
            is saved here.
            <div className="d-form">
              <button onClick={() => setEdit((e) => ({ ...e, args: { ...e.args, ...tuned } }))}>
                Take the console's values</button>
            </div>
          </div>
        )}
      </DetailSection>

      <DetailSection title="Fixtures">
        <div className="d-chips" role="group" aria-label="fixture groups">
          {groups.map((g) => {
            const on = edit.groups.includes(g);
            return (
              <button key={g} className={`s-chip${on ? " on" : ""}`} aria-pressed={on}
                      title={list.groups.includes(g) ? undefined : "Nothing on this rig carries this tag"}
                      onClick={() => setEdit({ ...edit, groups: on ? edit.groups.filter((x) => x !== g)
                        : [...edit.groups, g] })}>
                {g}{list.groups.includes(g) ? "" : " (not on this rig)"}</button>
            );
          })}
        </div>
        <span className="muted small">{edit.groups.length
          ? "It writes these groups and leaves the rest of the rig alone."
          : l.slot === "movement" ? "None picked: it moves every moving head."
            : "None picked: it writes every fixture."}</span>
      </DetailSection>

      <DetailSection title="Notes">
        <textarea className="s-notes" rows={3} value={edit.notes} aria-label="notes"
                  placeholder="What it is for, for whoever finds it later"
                  onChange={(e) => setEdit({ ...edit, notes: e.target.value })} />
      </DetailSection>
    </>
  );
}

type Mode = "duplicate" | "hide" | "delete" | "remake";

export function LookDetail({ engine, l, list, draft, onDraft, onDone, onSelect }: {
  engine: Engine; l: LookSummary; list: LooksList;
  /** What was unsaved here when the panel was last on screen, if anything. */
  draft?: LookDraft;
  /** The unsaved edit, for the page to keep -- or null once there is none. */
  onDraft: (draft: LookDraft | null) => void;
  /** Something was written, and what: say so where it survives this panel. */
  onDone: (said: string) => void;
  onSelect: (name: string | null) => void;
}) {
  const canWrite = engine.tier === "configure" && !list.stale;
  const rev = list.rev ?? "";
  const names = list.looks.map((x) => x.name);
  const isBlock = l.source === "block";
  const saved = editOf(l);
  const savedKey = savedForm(saved);
  const [edit, setEdit] = useState<Edit>(draft ?? saved);
  // A newer save (this panel's, the console's file, another tab's) is taken
  // while nothing here is unsaved.
  const [base, setBase] = useState(savedKey);
  useEffect(() => {
    if (savedKey === base) return;
    if (savedForm(edit) === base || savedForm(edit) === savedKey) setEdit(saved);
    setBase(savedKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [savedKey]);
  const editKey = savedForm(edit);
  const dirty = isBlock && editKey !== savedKey;
  // What is unsaved outlives the panel: the page keeps it by the look's name
  // until it is saved, reverted, or made the same as what is saved again.
  useEffect(() => {
    onDraft(dirty ? edit : null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dirty, editKey]);
  const [mode, setMode] = useState<Mode | null>(null);
  const [copyName, setCopyName] = useState(() => freeName(names, `${l.name} copy`));
  const [remakeName, setRemakeName] = useState(() => freeName(names, l.name));
  const [hideOriginal, setHideOriginal] = useState(true);
  const [replacedBy, setReplacedBy] = useState("");
  const [hideNote, setHideNote] = useState("");
  const [warning, setWarning] = useState<string | null>(null);
  const { busy, error, setError, run: write, ask } = useWrite(engine, onDone);
  const run = (work: () => Promise<string>) => write(work, () => setMode(null));
  const close = () => { setMode(null); setError(null); };

  const live = onStage(engine).has(l.name);
  const uses = lookUses(l.used_by);
  const name = edit.name.trim().replace(/\s+/g, " ");
  const nameTaken = name !== l.name && names.includes(name);
  const valid = !!name && !nameTaken;
  const others = list.looks.filter((x) => x.name !== l.name && !x.retired);
  const noted = (data: unknown) => {
    const warnings = (data as { warnings?: string[] } | undefined)?.warnings ?? [];
    setWarning(warnings.length ? warnings.join(" ") : null);
  };

  const save = () => run(async () => {
    const data = await ask({ type: "look_save", look: lookDoc(edit), was: l.name, base_rev: rev }) as
      { cues?: number; presets?: number; written?: string[] };
    noted(data);
    if (name === l.name) return `Saved ${name}. It is on the console's picker as saved.`;
    onDraft(null);              // it was kept under the old name
    onSelect(name);
    const n = (count: number, one: string) => count ? `${count} ${one}${count === 1 ? "" : "s"}` : "";
    const what = [n(data.cues ?? 0, "cue"), n(data.presets ?? 0, "preset"),
                  n(data.written?.length ?? 0, "show file")].filter(Boolean);
    return `Renamed ${l.name} to ${name}`
      + (what.length ? `, and moved what named it: ${what.join(", ")}.` : ".")
      + (l.supersedes ? ` The stored look ${l.name} is back under that name, hidden in favour of ${name}.` : "");
  });
  const duplicate = () => run(async () => {
    noted(await ask({ type: "look_save", base_rev: rev,
                      look: { ...lookDoc(saved), name: copyName.trim() } }));
    onSelect(copyName.trim());
    return `Duplicated ${l.name} as ${copyName.trim()}.`;
  });
  const remake = () => run(async () => {
    const to = remakeName.trim();
    // One command: the look is made and the original hidden in the same
    // write, or neither is. Two would leave a failure half done.
    await ask({ type: "look_save", base_rev: rev,
                look: { name: to, ...l.block_version, groups: l.groups,
                        notes: `Made from the stored look ${l.name}.` },
                ...(hideOriginal ? { hides: { look: l.name, note: REMADE_NOTE } } : {}) });
    onSelect(to);
    return `Made ${to} from ${l.name}${hideOriginal ? `, and hid ${l.name} from the picker` : ""}.`;
  });
  const hide = (hidden: boolean) => run(async () => {
    await ask({ type: "look_hide", look: l.name, hidden, base_rev: rev,
                ...(hidden && replacedBy ? { replaced_by: replacedBy } : {}),
                ...(hidden && hideNote.trim() ? { note: hideNote.trim() } : {}) });
    return hidden ? `Hid ${l.name} from the picker. It still plays for whatever names it.`
      : `${l.name} is back in the picker.`;
  });
  const remove = () => run(async () => {
    await ask({ type: "look_delete", look: l.name, base_rev: rev });
    onDraft(null);
    // One that took over a stored look leaves that look behind, under the same
    // name: it stays on screen, to hide if it is not wanted either.
    onSelect(l.supersedes ? l.name : null);
    return l.supersedes ? `Deleted the block look ${l.name}. The stored look of that name is back `
      + "in its place: hide it if you do not want it on the picker." : `Deleted ${l.name}.`;
  });

  const copyOk = !!copyName.trim() && !names.includes(copyName.trim());
  const remakeOk = !!remakeName.trim() && !names.includes(remakeName.trim());
  const menu = (
    <MoreMenu label={l.name} items={[
      ...(isBlock ? [{ label: "Duplicate…", onClick: () => { setError(null); setMode("duplicate" as Mode); } }] : []),
      l.retired ? { label: "Show in the picker", disabled: busy || !canWrite,
                    onClick: () => void hide(false) }
        : { label: "Hide from the picker…", onClick: () => { setError(null); setMode("hide" as Mode); } },
      ...(isBlock ? [
        { label: "Download the look", onClick: () => download(`${l.name.replace(/[^\w-]+/g, "-")}.json`, lookDoc(saved)) },
        { label: "Delete…", danger: true,
          ...(uses > 0 && !l.supersedes ? { disabled: true, note: `Used in ${uses} place${uses === 1 ? "" : "s"}` }
            : l.used_by.hides.length && !l.supersedes
              ? { disabled: true, note: `${l.used_by.hides.join(", ")} is hidden for it` } : {}),
          onClick: () => { setError(null); setMode("delete" as Mode); } },
      ] : []),
    ]} />
  );
  const badges: ReactNode = (
    <>
      {dirty && <Badge tone="info">Unsaved</Badge>}
      {live && <Badge tone="good">On stage</Badge>}
      {l.retired && <Badge>Hidden</Badge>}
    </>
  );

  return (
    <div className="s-detail" aria-label="selected look">
      <DetailHead kind={`${isBlock ? "Block look" : "Stored look"} · ${SLOT_NAME[l.slot]}`}
                  title={isBlock
                    ? <input className="s-title-input" value={edit.name} aria-label="look name"
                             onChange={(e) => setEdit({ ...edit, name: e.target.value })} />
                    : l.name}
                  meta={<span>{whatOf(l)} · <span className="mono">
                    {isBlock ? list.file : `looks.json${l.stored?.source ? ` (${l.stored.source})` : ""}`}</span></span>}
                  badges={badges} menu={menu} />

      {list.stale && <p className="s-note warn">Read the looks again (at the top of the page)
        before changing this one.</p>}

      {mode === "duplicate" && (
        <Task title={`Duplicate ${l.name}`} readOnly={!canWrite} onCancel={close}>
          <form className="d-form" onSubmit={(e) => { e.preventDefault(); if (copyOk) void duplicate(); }}>
            <input value={copyName} aria-label="name of the copy" autoFocus
                   onChange={(e) => setCopyName(e.target.value)} />
            <button type="submit" className="d-primary" disabled={!copyOk || busy || !canWrite}>
              Make the copy</button>
          </form>
          <span className="muted small">{dirty ? "A copy of the look as it is saved, not of what is "
            + "unsaved here. " : ""}Nothing that names {l.name} changes.</span>
        </Task>
      )}
      {mode === "remake" && l.block_version && (
        <Task title={`Make a block look from ${l.name}`} readOnly={!canWrite} onCancel={close}>
          <form className="d-form" onSubmit={(e) => { e.preventDefault(); if (remakeOk) void remake(); }}>
            <input value={remakeName} aria-label="name of the block look" autoFocus
                   onChange={(e) => setRemakeName(e.target.value)} />
            <button type="submit" className="d-primary" disabled={!remakeOk || busy || !canWrite}>
              Make it</button>
          </form>
          <label className="small s-inline">
            <input type="checkbox" checked={hideOriginal}
                   onChange={(e) => setHideOriginal(e.target.checked)} />
            Hide {l.name} from the picker, pointing at the new look</label>
          <span className="muted small">A <b>{l.block_version.block}</b> block that says the same
            thing, under a name of its own.{uses ? ` What names ${l.name} now keeps playing the `
              + "stored look: point it at the new one when you are ready." : ""}</span>
        </Task>
      )}
      {mode === "hide" && (
        <Task title={`Hide ${l.name} from the picker`} readOnly={!canWrite} onCancel={close}>
          <label className="s-field">What covers it now
            <select value={replacedBy} aria-label="replaced by"
                    onChange={(e) => setReplacedBy(e.target.value)}>
              <option value="">nothing in particular</option>
              {others.map((x) => <option key={x.name} value={x.name}>{x.name}</option>)}
            </select>
          </label>
          <label className="s-field">Why, for whoever finds it later
            <input value={hideNote} aria-label="why it is hidden"
                   onChange={(e) => setHideNote(e.target.value)} />
          </label>
          <div className="d-form">
            <button className="d-primary" disabled={busy || !canWrite} onClick={() => void hide(true)}>
              Hide it</button>
            <button onClick={close}>Cancel</button>
          </div>
          <span className="muted small">Hidden, not removed: it still plays for a cue, a preset or
            a routine that names it, and auto mode stops picking it.</span>
        </Task>
      )}
      {mode === "delete" && isBlock && (
        <Task title={`Delete ${l.name}`} label="confirm delete" readOnly={!canWrite} onCancel={close}>
          <span className="small">{l.supersedes
            ? `Delete this block look? The stored look called ${l.name} takes its place again, so `
              + "whatever names it keeps playing."
            : `Delete ${l.name} from ${list.file}? Studio cannot undo it.`}</span>
          <div className="d-form">
            <button className="d-bad" disabled={busy || !canWrite} onClick={() => void remove()}>
              Delete it</button>
            <button onClick={close}>Keep it</button>
          </div>
        </Task>
      )}
      {error && <p className="small d-error" role="alert">{error}</p>}
      {warning && <p className="s-note warn" role="status">{warning}</p>}

      {l.retired && (
        <div className="s-note">
          <b>Hidden from the picker{l.replaced_by ? `, for ${l.replaced_by}` : ""}.</b>
          {l.retired_note || "It still plays for whatever names it."}
          <div className="d-form">
            <button disabled={busy || !canWrite} onClick={() => void hide(false)}>Show it in the picker</button>
          </div>
        </div>
      )}

      {!isBlock && <StoredLook l={l} onRemake={() => { setError(null); setMode("remake"); }} />}

      {isBlock && (
        <>
          <BlockEditor engine={engine} l={l} list={list} edit={edit} setEdit={setEdit} />
          {nameTaken && <p className="small d-error">There is already a look called {name}.</p>}
          {dirty && (
            <div className="d-form">
              <button className="d-primary" disabled={!valid || busy || !canWrite}
                      onClick={() => void save()}>{name !== l.name ? "Save and rename" : "Save the look"}</button>
              <button onClick={() => { setEdit(saved); setError(null); }}>Revert</button>
              {engine.tier !== "configure" && <span className="muted small">{READ_ONLY}</span>}
            </div>
          )}
          {dirty && name !== l.name && (
            <span className="muted small">
              {uses ? `Renaming also rewrites what names it: ${usedLine(l.used_by)}.`
                : "Nothing names it, so only the look itself changes."}
              {l.supersedes ? ` The stored look ${l.name} comes back under that name, hidden in `
                + "favour of the new one." : ""}</span>)}
        </>
      )}

      <DetailSection title="On the rig">
        <div className="d-form">
          <button disabled={dirty || engine.tier === "view" || engine.status !== "open"}
                  onClick={() => engine.send({ type: "select_look", name: l.name })}>
            {live ? "On stage now" : "Play it on the rig"}</button>
        </div>
        <span className="muted small">{dirty ? "Save first: the rig plays the saved look."
          : `The same as picking it on the console: it takes the ${SLOT_NAME[l.slot].toLowerCase()} slot`
            + `${l.groups.length ? ` for ${l.groups.join(", ")}` : ""}.`}</span>
      </DetailSection>

      <Uses l={l} />
    </div>
  );
}
