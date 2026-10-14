import type { EngineState } from "../types";
import type { Engine } from "./Designer";
import { clipOps } from "./clipboard";
import { itemOf } from "./ids";
import { rememberBack } from "./document";
import type { Edits, History, RowsDoc } from "./history";
import { FADES, ROLES } from "./items";
import { BEATS_PER_BAR, barBeat, itemName, itemSub } from "./model";
import type { Item, RoutineSummary, Row } from "./model";
import { MidiCue, OscCue, VisualCue } from "./outputCues";
import { EditTags, RoleBinds, offeredLooks, rigTags, tagged } from "./roles";

/**
 * The selected item, edited: the inspector, and the list of events.
 */
export function Inspector({ history, item, routines, engine, onDeleted, beat, onSelect }: {
  history: History; item: { row: Row; item: Item } | null; routines: RoutineSummary[];
  engine: Engine; onDeleted: () => void;
  /** The playhead, for Split, and how to select what an operation made. */
  beat?: number; onSelect?: (id: string | null) => void;
}) {
  if (history.listView) return <EventList history={history} />;
  if (!item) {
    return (
      <footer className="d-inspector muted small">
        Select a clip, hit or point to edit it. Drag a clip to move it; drag its
        right edge to change its length. Positions snap to the {history.snap}.
      </footer>
    );
  }
  const { row, item: it } = item;
  const set = (fields: Partial<Item>) => history.apply((d) => {
    const target = itemOf(d, it.id);
    if (target) Object.assign(target, fields);
  });
  const routine = it.kind === "routine" ? routines.find((r) => r.id === it.routine) : undefined;
  const rigBound = it.kind === "look" || it.kind === "snapshot" || !!routine?.rig;
  const looks = offeredLooks(engine.state?.looks).map((l) => l.name);
  // A hit's "role" is read as a role's tag is: any tag, or one fixture's name.
  const hitTargets = [...new Set([...rigTags(engine.state),
                                  ...(engine.state?.fixtures ?? []).map((f) => f.name)])];
  const palettes = Object.keys(history.doc?.palettes ?? {});

  return (
    <footer className="d-inspector" aria-label="inspector">
      <div className="d-insp-head">
        <b>{itemName(it)}</b>
        <span className="muted"> · {it.hit ? "hit" : it.kind ?? "item"} on {row.id}</span>
        {rigBound && <span className="d-badge">this rig only</span>}
        <span className="muted mono"> · bar {barBeat(it.at)} → {barBeat(it.at + it.len)}
          {" "}({it.len} beats)</span>
        <span className="grow" />
        <span className="d-clip-tools" role="group" aria-label="clip">
          <button title="Ctrl+C" onClick={() => clipOps.copy(history, it.id, "timeline")}>Copy</button>
          <button title="Ctrl+X" onClick={() => { clipOps.cut(history, it.id, "timeline"); onDeleted(); }}>
            Cut</button>
          <button title="Ctrl+D: a copy straight after it"
                  onClick={() => onSelect?.(clipOps.duplicate(history, it.id, "timeline"))}>
            Duplicate</button>
          <button title="S: in two, at the playhead"
                  disabled={beat == null || !clipOps.inside(history.doc, it.id, history.snapBeat(beat))}
                  onClick={() => onSelect?.(clipOps.split(history, it.id, history.snapBeat(beat ?? 0)))}>
            Split</button>
        </span>
        <button onClick={() => {
          history.apply((d) => {
            for (const r of d.rows) if (r.items) r.items = r.items.filter((i) => i.id !== it.id);
          });
          onDeleted();
        }}>Delete</button>
      </div>
      <div className="d-insp-grid">
        {row.type === "external" && row.output === "osc" && (
          <OscCue item={it} set={set} />
        )}
        {row.type === "external" && row.output === "midi" && (
          <MidiCue item={it} set={set} />
        )}
        {row.type === "external" && (row.output === "visuals" || row.output === "vj") && (
          <VisualCue item={it} set={set} />
        )}
        {!it.hit && row.type !== "external" && (
          <div>
            <span className="small muted">Fade in</span>
            <div className="d-chips">
              {FADES.filter((f) => f <= it.len).map((f) => (
                <button key={f} className={(it.fade ?? 0) === f ? "on" : ""}
                        onClick={() => set({ fade: f })}>{f ? `${f}` : "cut"}</button>))}
            </div>
          </div>
        )}
        {it.kind === "routine" && (
          <>
            <label className="small">Routine{" "}
              <select value={it.routine} aria-label="routine"
                      onChange={(e) => set({ routine: e.target.value, variation: undefined,
                                             params: {}, bind: undefined })}>
                {routines.map((r) => <option key={r.id} value={r.id}>{r.name ?? r.id}</option>)}
              </select>
            </label>
            {routine && routine.variations.length > 0 && (
              <div>
                <span className="small muted">Variation</span>
                <div className="d-chips">
                  <button className={!it.variation ? "on" : ""}
                          onClick={() => set({ variation: undefined })}>default</button>
                  {routine.variations.map((v) => (
                    <button key={v} className={it.variation === v ? "on" : ""}
                            onClick={() => set({ variation: v })}>{v}</button>))}
                </div>
              </div>
            )}
            {routine && Object.entries(routine.params).map(([name, p]) => (
              <Param key={name} name={name} param={p} value={it.params?.[name]}
                     onChange={(v) => {
                       const params = { ...(it.params ?? {}) };
                       if (v === undefined) delete params[name]; else params[name] = v;
                       set({ params });
                     }} />
            ))}
            {routine && (
              <RoleBinds label={it.id} roles={routine.roles} bind={it.bind} state={engine.state}
                         onChange={(bind) => set({ bind })} />)}
            {routine && (
              <a className="small d-link" href={`#studio/routine/${routine.id}`}
                 onClick={() => rememberBack()}>
                Open routine · {routine.bars} bars{routine.loop ? ", loops" : ""}</a>)}
          </>
        )}
        {it.kind === "look" && (
          <>
            <label className="small">Look{" "}
              <input list="d-looks" value={it.look ?? ""} aria-label="look"
                     onChange={(e) => set({ look: e.target.value })} />
              <datalist id="d-looks">{looks.map((l) => <option key={l} value={l} />)}</datalist>
            </label>
            <LookGroups groups={it.groups} state={engine.state}
                        onChange={(groups) => set({ groups })} />
          </>
        )}
        {it.kind === "snapshot" && (
          <label className="small">Preset{" "}
            <select value={it.preset ?? ""} aria-label="preset"
                    onChange={(e) => set({ preset: e.target.value })}>
              <option value="">(none)</option>
              {engine.state?.presets.map((p) => <option key={p.name} value={p.name}>{p.name}</option>)}
            </select>
          </label>
        )}
        {it.kind === "palette" && (
          <label className="small">Palette{" "}
            <select value={it.palette ?? ""} aria-label="palette"
                    onChange={(e) => set({ palette: e.target.value })}>
              {palettes.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>
        )}
        {it.hit && (
          <>
            <div className="d-chips">
              {(["flash", "strobe", "blackout"] as const).map((h) => (
                <button key={h} className={it.hit === h ? "on" : ""}
                        onClick={() => set({ hit: h })}>{h}</button>))}
            </div>
            <label className="small">Level{" "}
              <input type="range" min={0} max={1} step={0.05} value={it.level ?? 1}
                     aria-label="hit level"
                     onChange={(e) => set({ level: Number(e.target.value) })} /></label>
            <label className="small">Who{" "}
              <select value={it.role ?? ""} aria-label="hit role"
                      onChange={(e) => set({ role: e.target.value || undefined })}>
                <option value="">everything</option>
                {it.role && !hitTargets.includes(it.role) && (
                  <option value={it.role}>{it.role} (not on this rig)</option>)}
                {hitTargets.map((g) => <option key={g} value={g}>{g}</option>)}
              </select>
            </label>
            {it.hit === "flash" && (
              <button className={it.envelope === "decay" ? "on" : ""}
                      onClick={() => set({ envelope: it.envelope === "decay" ? "hold" : "decay" })}>
                {it.envelope === "decay" ? "decays" : "holds"}</button>)}
          </>
        )}
      </div>
    </footer>
  );
}

/** Which fixtures a look clip plays on: every one the look covers, or only
 *  those with the tags picked (`groups`, read as the engine reads a role's
 *  tag: a tag, or one fixture's name). */
function LookGroups({ groups, state, onChange }: {
  groups: string[] | undefined; state: EngineState | null | undefined;
  onChange: (groups: string[] | undefined) => void;
}) {
  const tags = rigTags(state);
  const on = groups ?? [];
  const offered = [...tags, ...on.filter((g) => !tags.includes(g))];
  const toggle = (tag: string) => {
    const next = on.includes(tag) ? on.filter((g) => g !== tag) : [...on, tag];
    onChange(next.length ? next : undefined);
  };
  if (!offered.length) return null;
  return (
    <div>
      <span className="small muted">Only on</span>
      <div className="d-chips" role="group" aria-label="look plays on">
        <button className={on.length ? "" : "on"} onClick={() => onChange(undefined)}>
          all it covers</button>
        {offered.map((t) => (
          <button key={t} className={on.includes(t) ? "on" : ""} aria-pressed={on.includes(t)}
                  title={tagged(state, t).map((f) => f.name).join(", ") || "nothing on this rig"}
                  onClick={() => toggle(t)}>{t}</button>))}
        <EditTags />
      </div>
    </div>
  );
}

export function Param({ name, param, value, onChange }: {
  name: string; param: { type: string; default?: unknown; min?: number; max?: number; unit?: string };
  value: unknown; onChange: (v: unknown) => void;
}) {
  const shown = value ?? param.default;
  if (param.type === "color") {
    const role = typeof shown === "string" && shown.startsWith("@") ? shown.slice(1) : null;
    return (
      <div>
        <span className="small muted">${name}: open parameter</span>
        <div className="d-chips">
          {ROLES.map((r) => (
            <button key={r} className={role === r ? "on" : ""}
                    onClick={() => onChange(`@${r}`)}>{r}</button>))}
          <input type="color" aria-label={`${name} direct color`}
                 value={typeof shown === "string" && shown.startsWith("#") ? shown : "#ffffff"}
                 onChange={(e) => onChange(e.target.value)} />
          {value !== undefined && <button onClick={() => onChange(undefined)}>default</button>}
        </div>
      </div>
    );
  }
  if (param.type === "number" || param.type === "rate") {
    const lo = param.min ?? 0;
    const hi = param.max ?? (param.type === "rate" ? 8 : 100);
    const num = typeof shown === "number" ? shown : lo;
    return (
      <label className="small">${name} · {num}{param.unit === "deg" ? "°" : ""}{" "}
        <input type="range" min={lo} max={hi} step={param.type === "rate" ? 0.25 : 1}
               value={num} aria-label={`${name}`}
               onChange={(e) => onChange(Number(e.target.value))} />
      </label>
    );
  }
  return (
    <label className="small">${name}{" "}
      <input value={typeof shown === "string" ? shown : ""} aria-label={name}
             onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

/** The same document as a list: every item by position, with nudges. */
export function EventList({ history }: { history: Edits & { doc: RowsDoc | null } }) {
  const doc = history.doc;
  if (!doc) return null;
  const rows = doc.rows.flatMap((r) => (r.items ?? []).map((it) => ({ r, it })))
    .sort((a, b) => a.it.at - b.it.at || a.r.id.localeCompare(b.r.id));
  const nudge = (id: string, beats: number) => history.apply((d) => {
    const it = itemOf(d, id);
    if (it) it.at = Math.max(-64, it.at + beats);
  });
  return (
    <footer className="d-inspector d-list" aria-label="event list">
      <table>
        <thead><tr><th>bar</th><th>lane</th><th>what</th><th>len</th><th>fade</th><th /></tr></thead>
        <tbody>
          {rows.map(({ r, it }) => (
            <tr key={it.id}>
              <td className="mono">{barBeat(it.at)}</td>
              <td>{r.id}</td>
              <td>{itemName(it)} <span className="muted small">{itemSub(it)}</span></td>
              <td className="mono">{it.len}</td>
              <td className="mono">{it.fade ?? 0}</td>
              <td>
                <button className="small" aria-label={`${it.id} back a bar`}
                        onClick={() => nudge(it.id, -BEATS_PER_BAR)}>−bar</button>
                <button className="small" aria-label={`${it.id} back a beat`}
                        onClick={() => nudge(it.id, -1)}>−1</button>
                <button className="small" aria-label={`${it.id} on a beat`}
                        onClick={() => nudge(it.id, 1)}>+1</button>
                <button className="small" aria-label={`${it.id} on a bar`}
                        onClick={() => nudge(it.id, BEATS_PER_BAR)}>+bar</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </footer>
  );
}
