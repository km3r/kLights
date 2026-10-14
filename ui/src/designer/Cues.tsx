import { useEffect, useState } from "react";
import type { Dispatch, ReactNode, SetStateAction } from "react";
import type { Engine } from "./Designer";
import { BLOCK_ARGS, SLOTS, freeName } from "./model";
import type { CueDoc, CueMacro, CueSummary, CuesList, LookSummary, LooksList, Slot } from "./model";
import { ArgField } from "./RoutineEditor";
import { Badge, DetailHead, DetailSection, MoreMenu, READ_ONLY, Task, useWrite } from "./detail";

/**
 * This rig's cue list: the night in running order, as the console's GO button
 * steps through it.
 *
 * It is the EVENT's (cues.json), like the looks it names. A cue is the three
 * slots a preset has -- a look for each fixture group in movement, color and
 * level -- plus a fade and a hold in BEATS, and whatever else it restores: a
 * master, a speed, a shape, slot rates, its looks' tuning. Left unset, each of
 * those is left alone when the cue is taken.
 *
 * Every write is the engine's (`cue_save`, `cue_copy`, `cue_move`,
 * `cue_delete`, `cues_name`), and quotes the rev the list was read at. The
 * engine installs the list as it writes and KEEPS ITS PLACE: the cue the
 * console is on stays current wherever it moves and whatever it is renamed,
 * and nothing on stage changes until a cue is taken again.
 *
 * A look the library lacks does not stop a cue being saved. It is flagged, on
 * the row and in the panel: taking the cue skips that look.
 */

const SLOT_NAME: Record<Slot, string> = { movement: "Movement", color: "Color", level: "Level" };
/** Where this page is, for whatever sends someone to it. */
export const CUES_HASH = "#studio/cues";
/** The beats a new cue fades in over: the engine's own default. */
export const NEW_FADE = 8;

/** How a cue comes in and how it leaves, in the console's own words. */
export function fadeText(fade: number): string {
  return fade === 0 ? "cut" : `${fade} beat fade`;
}
export function holdText(hold: number): string {
  return hold > 0 ? `auto after ${hold}` : "waits for GO";
}

/** A cue as it is being edited: its fade and hold may be empty mid-typing. */
export interface CueEdit extends Omit<CueDoc, "fade" | "hold"> {
  fade: number | null;
  hold: number | null;
}
/** The page keeps one per cue with unsaved changes, so looking at another
 *  cue -- to compare, to copy a number -- does not throw the work away. */
export type CueDraft = CueEdit;

function editOf(c: CueDoc): CueEdit {
  return structuredClone({
    name: c.name, movement: c.movement, color: c.color, level: c.level, fade: c.fade,
    hold: c.hold, speed: c.speed, master: c.master, macro: c.macro, rates: c.rates,
    params: c.params, notes: c.notes });
}

const tidy = (name: string) => name.trim().replace(/\s+/g, " ");

/** A cue as the file holds it, which is how it is sent: only what it sets.
 *  An empty slot, a hold of 0 and whatever is left alone are left out. */
export function cueDoc(e: CueEdit): Record<string, unknown> {
  const out: Record<string, unknown> = { name: tidy(e.name) };
  for (const slot of SLOTS) if (Object.keys(e[slot]).length) out[slot] = e[slot];
  out.fade = e.fade;
  if (e.hold) out.hold = e.hold;
  if (e.speed != null) out.speed = e.speed;
  if (e.master != null) out.master = e.master;
  if (e.macro && Object.keys(e.macro).length) out.macro = e.macro;
  if (e.rates && Object.keys(e.rates).length) out.rates = e.rates;
  // Empty is not the same as none: it puts every look back to its saved values.
  if (e.params != null) out.params = e.params;
  if (e.notes.trim()) out.notes = e.notes.trim();
  return out;
}

/** The looks a cue puts in a slot, each once, in slot order. */
function namedLooks(e: Pick<CueDoc, Slot>): string[] {
  const out: string[] = [];
  for (const slot of SLOTS) for (const look of Object.values(e[slot])) if (!out.includes(look)) out.push(look);
  return out;
}

/** What is wrong with an edit that the page can see for itself, before it is
 *  saved: the engine's own flags are for the cue as it is saved. */
function editFlags(e: CueEdit, looks: LookSummary[] | undefined): string[] {
  if (!looks) return [];
  const out: string[] = [];
  for (const slot of SLOTS) {
    for (const [group, name] of Object.entries(e[slot])) {
      const look = looks.find((l) => l.name === name);
      if (!look) out.push(`${slot} wants '${name}' for '${group}', which is not in the library: taking the cue skips it`);
      else if (look.slot !== slot) out.push(`${slot} holds '${name}', which is a ${look.slot} look`);
    }
  }
  return out;
}

/** The looks one lane of a slot can be given: the ones that write that group
 *  first, then the rest of the slot's. Hidden looks and a chase's own steps
 *  are left out, unless one is what the lane holds. */
function choices(looks: LookSummary[], slot: Slot, group: string, current?: string) {
  const inSlot = looks.filter((l) => l.slot === slot && (l.name === current || (!l.retired && !l.step_of)));
  const mine = inSlot.filter((l) => !l.groups.length || l.groups.includes(group));
  return { mine, rest: inSlot.filter((l) => !mine.includes(l)) };
}

// -- the list -------------------------------------------------------------------------

function SlotLine({ slot, held, missing }: { slot: Slot; held: Record<string, string>; missing: string[] }) {
  const lanes = Object.entries(held);
  if (!lanes.length) return null;
  return (
    <span className="s-cue-slot">
      <span className={`s-slot ${slot[0]}`}>{SLOT_NAME[slot]}</span>
      <span className="s-cue-held">
        {lanes.map(([group, look]) => (
          <span key={group}>
            <span className={missing.includes(look) ? "s-cue-missing" : undefined}
                  title={missing.includes(look) ? "Not in the library: the cue skips it" : group}>
              {look}</span>
            {lanes.length > 1 && <span className="muted"> ({group})</span>}
          </span>
        ))}
      </span>
    </span>
  );
}

export function CuesView({ engine, list, error, selected, unsaved, onSelect, onNew, onDone }: {
  engine: Engine; list: CuesList | null; error: string | null;
  selected: string | null; onSelect: (name: string) => void;
  /** The cues with edits not yet saved: kept while another cue is looked at. */
  unsaved: ReadonlySet<string>;
  /** Open + New's dialog, for a cue. */
  onNew: () => void;
  onDone: (said: string) => void;
}) {
  const { busy, error: failed, setError, run, ask } = useWrite(engine, onDone);
  const [naming, setNaming] = useState<string | null>(null);
  const [drag, setDrag] = useState<string | null>(null);
  const [over, setOver] = useState<number | null>(null);
  const cues = list?.cues ?? [];
  const rev = list?.rev ?? "";
  const live = engine.state?.cues ?? null;
  const at = live?.index ?? -1;
  const started = at >= 0;
  const canWrite = engine.tier === "configure" && !!list && !list.stale;
  const canMove = canWrite && !busy;

  const move = (name: string, to: number) => run(async () => {
    await ask({ type: "cue_move", cue: name, to, base_rev: rev });
    return `Moved ${name} to ${to + 1} of ${cues.length}. The console's list is in the new order.`;
  });
  const rename = () => run(async () => {
    const to = tidy(naming ?? "");
    await ask({ type: "cues_name", name: to, base_rev: rev });
    return `The list is called ${to}: the title of the console's GO card.`;
  }, () => setNaming(null));

  return (
    <section className="s-page" aria-label="cues">
      <div className="s-page-head">
        <div>
          <h1>Cues</h1>
          <span className="muted small">
            {list ? cues.length ? <>{cues.length} cue{cues.length === 1 ? "" : "s"} on <b>{list.event}</b>,
              in running order: the console's GO card{list.name ? <>, <b>{list.name}</b>,</> : ""} steps
              through them. Fades and holds are in beats.</>
              : <>The night as one GO button, for <b>{list.event}</b>.</>
              : "This rig's cue list: the night as one GO button."}</span>
        </div>
        <span className="grow" />
        {list?.rev != null && (
          <MoreMenu label="the cue list" items={[
            { label: "Rename the list…", onClick: () => { setError(null); setNaming(list.name ?? ""); } },
          ]} />)}
        <button disabled={!started || engine.tier === "view" || engine.status !== "open"}
                title="Rewind to before the first cue. Nothing is taken: what is on stage stays"
                onClick={() => engine.send({ type: "cue_reset" })}>Reset</button>
        <button onClick={onNew}>New cue…</button>
      </div>

      {list && (list.stale || list.problem) && (
        <div className="s-note warn" role="alert">
          <b>{list.problem ? `${list.file} on disk does not load, so the engine is running the `
            + "list it had." : `${list.file} changed on disk since the engine read it.`}</b>
          {list.problem ? <span className="mono small s-pre">{list.problem}</span>
            : "The engine reads it again by itself in a moment. What is listed here is the "
              + "list that is running; a save before then would be refused."}
          <div className="d-form">
            <button className="d-primary" disabled={busy || engine.tier !== "configure"}
                    onClick={() => void run(async () => {
                      await ask({ type: "cues_reload" });
                      return `Read ${list.file} again.`;
                    })}>Read the cues again</button>
            {engine.tier !== "configure" && <span className="muted small">{READ_ONLY}</span>}
          </div>
        </div>
      )}
      {naming != null && (
        <Task title="Rename the cue list" readOnly={engine.tier !== "configure"}
              onCancel={() => { setNaming(null); setError(null); }}>
          <form className="d-form" onSubmit={(e) => { e.preventDefault(); if (tidy(naming) && canWrite) void rename(); }}>
            <input value={naming} aria-label="name of the cue list" autoFocus
                   onChange={(e) => setNaming(e.target.value)} />
            <button type="submit" className="d-primary" disabled={!tidy(naming) || busy || !canWrite}>
              Rename</button>
          </form>
          <span className="muted small">The title of the GO card on every console.</span>
        </Task>
      )}
      {(error ?? failed) && <p className="d-error" role="alert">{error ?? failed}</p>}

      {cues.length > 0 && (
        <p className="s-cue-now" role="status" aria-label="where the list is">
          {started && live ? <>Now: <b>{live.current}</b> <span className="muted">
            ({at + 1} of {live.count}{live.auto_advance ? ", moving on by itself" : ""})</span></>
            : <span className="muted">Not started</span>}
          {live?.next ? <> · GO takes <b>{live.next}</b></>
            : live ? <span className="muted"> · the end of the list</span> : null}
        </p>
      )}

      {list == null && !error && <p className="muted">Loading the cues…</p>}
      {list != null && !cues.length && !list.problem && (
        <p className="muted">This event has no cue list yet. Make the first cue with New cue: it
          starts {list.file}, and every console gets its GO card.</p>)}
      {cues.length > 0 && (
        <div className="s-table-wrap">
          <table className="s-table s-cues">
            <thead>
              <tr><th>#</th><th>Cue</th><th>Looks</th><th>Comes in, leaves</th>
                <th><span className="sr-only">State</span></th>
                <th><span className="sr-only">Order</span></th></tr>
            </thead>
            <tbody>
              {cues.map((c, i) => {
                const isLive = started && i === at && live?.current === c.name;
                const isNext = !!live && i === at + 1 && live.next === c.name;
                return (
                  <tr key={`${i}:${c.name}`} draggable={canMove}
                      className={`${c.name === selected ? "sel" : ""}${over === i && drag && drag !== c.name ? " s-drop" : ""}`}
                      onClick={() => onSelect(c.name)}
                      onDragStart={(e) => {
                        setDrag(c.name);
                        if (e.dataTransfer) {
                          e.dataTransfer.effectAllowed = "move";
                          e.dataTransfer.setData("text/plain", c.name);
                        }
                      }}
                      onDragOver={(e) => { if (drag) { e.preventDefault(); setOver(i); } }}
                      onDragEnd={() => { setDrag(null); setOver(null); }}
                      onDrop={(e) => {
                        e.preventDefault();
                        if (drag && drag !== c.name && canMove) void move(drag, i);
                        setDrag(null);
                        setOver(null);
                      }}>
                    <td className="mono muted">{i + 1}</td>
                    <td className="s-look">
                      <button className="s-look-name" aria-pressed={c.name === selected}
                              onClick={(e) => { e.stopPropagation(); onSelect(c.name); }}>
                        <b>{c.name}</b></button>
                      {c.notes && <span className="muted small s-cue-notes">{c.notes}</span>}
                    </td>
                    <td className="small s-cue-looks">
                      {SLOTS.map((slot) => <SlotLine key={slot} slot={slot} held={c[slot]} missing={c.missing} />)}
                      {!namedLooks(c).length && <span className="muted">no looks: it leaves the slots as they are</span>}
                    </td>
                    <td className="small s-cue-timing">
                      <span>{fadeText(c.fade)}</span>
                      <span className={c.hold > 0 ? undefined : "muted"}>{holdText(c.hold)}</span>
                    </td>
                    <td className="s-look-state">
                      {unsaved.has(c.name) && <Badge tone="info">Unsaved</Badge>}
                      {isLive && <Badge tone="good">Live</Badge>}
                      {isNext && <Badge>Next</Badge>}
                      {c.flags.length > 0 && (
                        <span title={c.flags.join("\n")}><Badge tone="warn">
                          {c.missing.length ? `${c.missing.length} missing look${c.missing.length === 1 ? "" : "s"}`
                            : `${c.flags.length} to check`}</Badge></span>)}
                    </td>
                    <td className="s-cue-order">
                      <button className="s-icon" aria-label={`move ${c.name} up`} title="Earlier in the night"
                              disabled={!canMove || i === 0}
                              onClick={(e) => { e.stopPropagation(); void move(c.name, i - 1); }}>▲</button>
                      <button className="s-icon" aria-label={`move ${c.name} down`} title="Later in the night"
                              disabled={!canMove || i === cues.length - 1}
                              onClick={(e) => { e.stopPropagation(); void move(c.name, i + 1); }}>▼</button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {cues.length > 1 && canWrite && (
        <span className="muted small">Drag a row, or use its arrows, to change the running order. The
          console stays on the cue it is on.</span>)}
    </section>
  );
}

// -- the selected cue ---------------------------------------------------------------

function Num({ label, unit, value, onChange, min, max, step, placeholder }: {
  label: string; unit?: string; value: number | null | undefined;
  onChange: (v: number | null) => void;
  min?: number; max?: number; step?: number; placeholder?: string;
}) {
  return (
    <label className="s-num small">
      <span className="muted">{label}{unit ? ` (${unit})` : ""}</span>
      <input type="number" aria-label={label} value={value ?? ""} min={min} max={max}
             step={step ?? "any"} placeholder={placeholder}
             onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))} />
    </label>
  );
}

/** Something a cue restores only if it says so: off, the cue leaves it alone. */
function Opt({ label, on, onToggle, children }: {
  label: string; on: boolean; onToggle: (on: boolean) => void; children: ReactNode;
}) {
  return (
    <div className="s-cue-opt">
      <label className="small s-inline">
        <input type="checkbox" checked={on} onChange={(e) => onToggle(e.target.checked)} />
        {label}</label>
      {on && <div className="s-cue-opt-body">{children}</div>}
    </div>
  );
}

/** One slot of a cue: a lane for each fixture group, and the look it holds. */
function SlotLanes({ slot, edit, setEdit, list, looks }: {
  slot: Slot; edit: CueEdit; setEdit: Dispatch<SetStateAction<CueEdit>>;
  list: CuesList; looks: LookSummary[];
}) {
  const held = edit[slot];
  const set = (next: Record<string, string>) => setEdit((e) => ({ ...e, [slot]: next }));
  const first = (group: string) => {
    const c = choices(looks, slot, group);
    return c.mine[0]?.name;
  };
  // The groups a lane can be opened for: the rig's that have a look in this
  // slot. Movement is one look for the whole rig, so one lane is all it needs.
  const free = list.groups.filter((g) => !(g in held) && first(g) !== undefined);
  const more = slot === "movement" ? Object.keys(held).length === 0 : true;
  return (
    <div className="s-cue-lanes" role="group" aria-label={`${slot} looks`}>
      <span className={`s-slot ${slot[0]}`}>{SLOT_NAME[slot]}</span>
      {Object.entries(held).map(([group, name]) => {
        const c = choices(looks, slot, group, name);
        const known = looks.find((l) => l.name === name);
        const listed = c.mine.some((l) => l.name === name) || c.rest.some((l) => l.name === name);
        return (
          <div key={group} className="s-cue-lane">
            <span className="small">{group}</span>
            <select value={name} aria-label={`${slot} look for ${group}`}
                    className={known ? undefined : "s-cue-missing"}
                    onChange={(e) => set({ ...held, [group]: e.target.value })}>
              {!listed && <option value={name}>{name} ({known ? `a ${known.slot} look` : "not in the library"})</option>}
              {c.mine.length > 0 && (
                <optgroup label={`For ${group}`}>
                  {c.mine.map((l) => <option key={l.name} value={l.name}>{l.name}</option>)}
                </optgroup>)}
              {c.rest.length > 0 && (
                <optgroup label="For other fixtures">
                  {c.rest.map((l) => <option key={l.name} value={l.name}>{l.name}</option>)}
                </optgroup>)}
            </select>
            <button className="s-icon" aria-label={`remove ${slot} for ${group}`} title="Leave this alone"
                    onClick={() => { const next = { ...held }; delete next[group]; set(next); }}>×</button>
          </div>
        );
      })}
      {more && free.length > 0 && (
        <select value="" aria-label={`add a ${slot} look for`}
                onChange={(e) => { const g = e.target.value; const look = first(g); if (g && look) set({ ...held, [g]: look }); }}>
          <option value="">{Object.keys(held).length ? "+ another group…" : "+ a look for…"}</option>
          {free.map((g) => <option key={g} value={g}>{g}</option>)}
        </select>
      )}
      {!Object.keys(held).length && <span className="muted small">Nothing: the cue leaves {slot} as it is.</span>}
    </div>
  );
}

/** The numbers of a block look a cue can carry tuning for. */
function tunableArgs(l: LookSummary) {
  return (BLOCK_ARGS[l.block ?? ""] ?? []).filter((spec) => spec.kind === "number");
}

type Mode = "duplicate" | "delete";

export function CueDetail({ engine, c, index, list, looks, draft, onDraft, onDone, onSelect }: {
  engine: Engine; c: CueSummary;
  /** Where it is in the running order. */
  index: number;
  list: CuesList;
  /** This rig's looks, once read: what each slot's lane is picked from. */
  looks: LooksList | null;
  /** What was unsaved here when the panel was last on screen, if anything. */
  draft?: CueDraft;
  /** The unsaved edit, for the page to keep -- or null once there is none. */
  onDraft: (draft: CueDraft | null) => void;
  /** Something was written, and what: say so where it survives this panel. */
  onDone: (said: string) => void;
  onSelect: (name: string | null) => void;
}) {
  const canWrite = engine.tier === "configure" && !list.stale;
  const rev = list.rev ?? "";
  const names = list.cues.map((x) => x.name);
  const library = looks?.looks ?? [];
  const saved = editOf(c);
  const savedKey = JSON.stringify(cueDoc(saved));
  const [edit, setEdit] = useState<CueEdit>(draft ?? saved);
  // A newer save (this panel's, the console's file, another tab's) is taken
  // while nothing here is unsaved.
  const [base, setBase] = useState(savedKey);
  const editKey = JSON.stringify(cueDoc(edit));
  useEffect(() => {
    if (savedKey === base) return;
    if (editKey === base || editKey === savedKey) setEdit(saved);
    setBase(savedKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [savedKey]);
  const dirty = editKey !== savedKey;
  // What is unsaved outlives the panel: the page keeps it by the cue's name
  // until it is saved, reverted, or made the same as what is saved again.
  useEffect(() => {
    onDraft(dirty ? edit : null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dirty, editKey]);
  const [mode, setMode] = useState<Mode | null>(null);
  const [copyName, setCopyName] = useState(() => freeName(names, `${c.name} copy`));
  const [warning, setWarning] = useState<string | null>(null);
  const { busy, error, setError, run: write, ask } = useWrite(engine, onDone);
  const run = (work: () => Promise<string>) => write(work, () => setMode(null));
  const close = () => { setMode(null); setError(null); };

  const live = engine.state?.cues ?? null;
  const isLive = !!live && live.index === index && live.current === c.name;
  const isNext = !!live && live.index + 1 === index && live.next === c.name;
  const name = tidy(edit.name);
  const nameTaken = name !== c.name && names.includes(name);
  const valid = !!name && !nameTaken && edit.fade != null && edit.fade >= 0 && (edit.hold ?? 0) >= 0;
  const flags = dirty ? editFlags(edit, looks?.looks) : c.flags;
  const noted = (data: unknown) => {
    const warnings = (data as { warnings?: string[] } | undefined)?.warnings ?? [];
    setWarning(warnings.length ? warnings.join(". ") + "." : null);
  };

  const save = () => run(async () => {
    noted(await ask({ type: "cue_save", cue: cueDoc(edit), was: c.name, base_rev: rev }));
    const kept = isLive ? " The console is still on it, with what it took: take it again to see the change."
      : " The console takes it as saved.";
    if (name === c.name) return `Saved ${name}.${kept}`;
    onDraft(null);              // it was kept under the old name
    onSelect(name);
    return `Renamed ${c.name} to ${name}.${isLive ? " The console is still on it." : ""}`;
  });
  const duplicate = () => run(async () => {
    const to = tidy(copyName);
    await ask({ type: "cue_copy", cue: c.name, to, base_rev: rev });
    onSelect(to);
    return `Duplicated ${c.name} as ${to}, straight after it.`;
  });
  const remove = () => run(async () => {
    await ask({ type: "cue_delete", cue: c.name, base_rev: rev });
    onDraft(null);
    onSelect(null);
    return isLive ? `Deleted ${c.name}. The console was on it: the list is on the cue before, and `
      + "what is on stage has not changed." : `Deleted ${c.name}.`;
  });
  const move = (to: number) => run(async () => {
    await ask({ type: "cue_move", cue: c.name, to, base_rev: rev });
    return `Moved ${c.name} to ${to + 1} of ${list.cues.length}.`;
  });

  const copyOk = !!tidy(copyName) && !names.includes(tidy(copyName));
  const only = list.cues.length === 1;
  const menu = (
    <MoreMenu label={c.name} items={[
      { label: "Duplicate…", onClick: () => { setError(null); setMode("duplicate"); } },
      { label: "Move up", disabled: busy || !canWrite || index === 0, onClick: () => void move(index - 1) },
      { label: "Move down", disabled: busy || !canWrite || index === list.cues.length - 1,
        onClick: () => void move(index + 1) },
      { label: "Delete…", danger: true,
        ...(only ? { disabled: true, note: "The only cue: a list cannot be empty" } : {}),
        onClick: () => { setError(null); setMode("delete"); } },
    ]} />
  );
  const badges: ReactNode = (
    <>
      {dirty && <Badge tone="info">Unsaved</Badge>}
      {isLive && <Badge tone="good">Live</Badge>}
      {isNext && <Badge>Next</Badge>}
    </>
  );

  // -- what else it restores
  const macro = edit.macro;
  const setMacro = (next: CueMacro) => setEdit((e) => ({ ...e, macro: next }));
  const macroNumber = (key: "size" | "spread", v: number | null) => {
    const next = { ...(macro ?? {}) };
    if (v == null) delete next[key]; else next[key] = v;
    setMacro(next);
  };
  const center = (i: 0 | 1, v: number | null) => {
    const now = macro?.center;
    const next = { ...(macro ?? {}) };
    const other = now?.[i === 0 ? 1 : 0];
    if (v == null && (other == null || other === 0)) delete next.center;
    else next.center = i === 0 ? [v ?? 0, other ?? 0] : [other ?? 0, v ?? 0];
    setMacro(next);
  };
  const named = namedLooks(edit);
  const tunable = named.map((n) => library.find((l) => l.name === n))
    .filter((l): l is LookSummary => !!l && l.source === "block" && tunableArgs(l).length > 0);
  const setParam = (look: string, key: string, v: unknown) => setEdit((e) => {
    const params = { ...(e.params ?? {}) };
    const values = { ...(params[look] ?? {}) };
    if (v === undefined) delete values[key]; else values[key] = v;
    if (Object.keys(values).length) params[look] = values; else delete params[look];
    return { ...e, params };
  });
  const strays = Object.keys(edit.params ?? {}).filter((look) => !named.includes(look));

  return (
    <div className="s-detail" aria-label="selected cue">
      <DetailHead kind={`Cue ${index + 1} of ${list.cues.length}`}
                  title={<input className="s-title-input" value={edit.name} aria-label="cue name"
                                onChange={(e) => setEdit({ ...edit, name: e.target.value })} />}
                  meta={<span>{fadeText(c.fade)} · {holdText(c.hold)} · <span className="mono">{list.file}</span></span>}
                  badges={badges} menu={menu} />

      {list.stale && <p className="s-note warn">Read the cues again (at the top of the page)
        before changing this one.</p>}

      {mode === "duplicate" && (
        <Task title={`Duplicate ${c.name}`} readOnly={!canWrite} onCancel={close}>
          <form className="d-form" onSubmit={(e) => { e.preventDefault(); if (copyOk) void duplicate(); }}>
            <input value={copyName} aria-label="name of the copy" autoFocus
                   onChange={(e) => setCopyName(e.target.value)} />
            <button type="submit" className="d-primary" disabled={!copyOk || busy || !canWrite}>
              Make the copy</button>
          </form>
          <span className="muted small">{dirty ? "A copy of the cue as it is saved, not of what is "
            + "unsaved here. " : ""}It goes in straight after {c.name}.</span>
        </Task>
      )}
      {mode === "delete" && (
        <Task title={`Delete ${c.name}`} label="confirm delete" readOnly={!canWrite} onCancel={close}>
          <span className="small">Delete {c.name} from {list.file}? Studio cannot undo it.
            {isLive ? " The console is on this cue: the list falls back to the one before it, and "
              + "what is on stage stays." : ""}</span>
          <div className="d-form">
            <button className="d-bad" disabled={busy || !canWrite} onClick={() => void remove()}>
              Delete it</button>
            <button onClick={close}>Keep it</button>
          </div>
        </Task>
      )}
      {error && <p className="small d-error" role="alert">{error}</p>}
      {warning && <p className="s-note warn" role="status">{warning}</p>}

      {flags.length > 0 && (
        <div className="s-note warn" role="group" aria-label="to check">
          <b>{flags.length === 1 ? "One thing to check" : `${flags.length} things to check`}</b>
          <ul className="s-cue-flags">{flags.map((f) => <li key={f}>{f}</li>)}</ul>
          <span className="muted small">The cue still loads and can be taken: it does less than
            it reads.</span>
        </div>
      )}

      <DetailSection title="Looks">
        {SLOTS.map((slot) => (
          <SlotLanes key={slot} slot={slot} edit={edit} setEdit={setEdit} list={list} looks={library} />))}
        <span className="muted small">A look for each fixture group, slot by slot, as a preset
          holds them. The rig runs one movement look.{looks ? "" : " (Reading the looks…)"}</span>
      </DetailSection>

      <DetailSection title="Timing">
        <div className="s-cue-row">
          <Num label="fade" unit="beats" value={edit.fade} min={0} step={1}
               onChange={(v) => setEdit({ ...edit, fade: v })} />
          <Num label="hold" unit="beats" value={edit.hold} min={0} step={1} placeholder="0"
               onChange={(v) => setEdit({ ...edit, hold: v })} />
        </div>
        <span className="muted small">A fade of 0 is a cut. A hold of 0 waits for GO; any more
          and the cue moves on by itself after that many beats.</span>
      </DetailSection>

      <DetailSection title="Also restores">
        <span className="muted small">Each is taken with the cue only if it is ticked. Left off,
          the cue leaves it alone, so the list does not fight a hand on the console.</span>
        <Opt label="Master" on={edit.master != null}
             onToggle={(on) => setEdit({ ...edit, master: on ? 1 : null })}>
          <Num label="master" unit="0 to 1" value={edit.master} min={0} max={1} step={0.05}
               onChange={(v) => setEdit({ ...edit, master: v ?? 0 })} />
        </Opt>
        <Opt label="Speed" on={edit.speed != null}
             onToggle={(on) => setEdit({ ...edit, speed: on ? 1 : null })}>
          <Num label="speed" unit="× the music's tempo" value={edit.speed} min={0.015625} max={64}
               step={0.25} onChange={(v) => setEdit({ ...edit, speed: v ?? 1 })} />
        </Opt>
        <Opt label="Shape" on={macro != null}
             onToggle={(on) => setEdit({ ...edit, macro: on ? { size: 1, spread: 0 } : null })}>
          <div className="s-cue-row">
            <Num label="size" unit="×" value={macro?.size} min={0} max={3} step={0.05}
                 placeholder="as it is" onChange={(v) => macroNumber("size", v)} />
            <Num label="spread" value={macro?.spread} min={-1} max={1} step={0.05}
                 placeholder="as it is" onChange={(v) => macroNumber("spread", v)} />
          </div>
          <div className="s-cue-row">
            <Num label="center bearing" unit="°" value={macro?.center?.[0]} step={1}
                 placeholder="as it is" onChange={(v) => center(0, v)} />
            <Num label="center elevation" unit="°" value={macro?.center?.[1]} step={1}
                 placeholder="as it is" onChange={(v) => center(1, v)} />
          </div>
        </Opt>
        <Opt label="Slot rates" on={edit.rates != null}
             onToggle={(on) => setEdit({ ...edit, rates: on ? {} : null })}>
          <div className="s-cue-row">
            {SLOTS.map((slot) => (
              <Num key={slot} label={`${slot} rate`} unit="×" value={edit.rates?.[slot]} min={0} max={8}
                   step={0.25} placeholder="as it is"
                   onChange={(v) => setEdit((e) => {
                     const rates = { ...(e.rates ?? {}) };
                     if (v == null) delete rates[slot]; else rates[slot] = v;
                     return { ...e, rates };
                   })} />))}
          </div>
        </Opt>
        <Opt label="Look tuning" on={edit.params != null}
             onToggle={(on) => setEdit({ ...edit, params: on ? {} : null })}>
          <span className="muted small">Taken with the cue, every block look it names goes back to
            its saved values, but for what is set here.</span>
          {tunable.map((l) => {
            const turned = engine.state?.look_params?.[l.name];
            return (
              <div key={l.name} className="s-cue-tune" role="group" aria-label={`tuning for ${l.name}`}>
                <span className="small"><b>{l.name}</b> <span className="muted">{l.block}</span></span>
                <div className="s-args">
                  {tunableArgs(l).map((spec) => (
                    <ArgField key={spec.name} engine={engine} params={[]}
                              spec={{ ...spec, default: l.args?.[spec.name] ?? spec.default }}
                              value={edit.params?.[l.name]?.[spec.name]}
                              onChange={(v) => setParam(l.name, spec.name, v)} />))}
                </div>
                {turned && Object.keys(turned).length > 0 && (
                  <div className="d-form">
                    <button onClick={() => setEdit((e) => ({ ...e, params: {
                      ...(e.params ?? {}), [l.name]: { ...(e.params?.[l.name] ?? {}), ...turned } } }))}>
                      Take the console's values</button>
                    <span className="muted small">{Object.entries(turned)
                      .map(([k, v]) => `${k} ${JSON.stringify(v)}`).join(", ")}</span>
                  </div>
                )}
              </div>
            );
          })}
          {!tunable.length && <span className="muted small">None of its looks is a block look with
            numbers to tune, so this only puts them back as saved.</span>}
          {strays.length > 0 && (
            <span className="muted small">It also carries tuning for {strays.join(", ")}, which
              it does not put in a slot: kept in the file, and ignored when the cue is taken.</span>)}
        </Opt>
      </DetailSection>

      <DetailSection title="Notes">
        <textarea className="s-notes" rows={3} value={edit.notes} aria-label="notes"
                  placeholder="What this moment of the night is, for whoever runs it"
                  onChange={(e) => setEdit({ ...edit, notes: e.target.value })} />
      </DetailSection>

      {nameTaken && <p className="small d-error">There is already a cue called {name}.</p>}
      {dirty && (
        <div className="d-form">
          <button className="d-primary" disabled={!valid || busy || !canWrite}
                  onClick={() => void save()}>{name !== c.name ? "Save and rename" : "Save the cue"}</button>
          <button onClick={() => { setEdit(saved); setError(null); }}>Revert</button>
          {engine.tier !== "configure" && <span className="muted small">{READ_ONLY}</span>}
        </div>
      )}

      <DetailSection title="On the rig">
        <div className="d-form">
          <button disabled={dirty || engine.tier === "view" || engine.status !== "open"}
                  onClick={() => engine.send({ type: "cue", index })}>
            {isLive ? "Take it again" : "Take it on the rig"}</button>
        </div>
        <span className="muted small">{dirty ? "Save first: the rig takes the saved cue."
          : isLive ? "The console is on this cue. What is on stage is the cue as it was taken: a "
            + "save does not take it again."
            : `The same as jumping to it on the console: it ${c.fade === 0 ? "cuts in" : `fades in over ${c.fade} beats`}.`}</span>
      </DetailSection>
    </div>
  );
}
