import { useState } from "react";
import { Card } from "./components";
import type { Command, EngineState, LookInfo, Slot } from "./types";

/**
 * The library, filtered to one slot.
 *
 * Shared by the Color, Move and Levels tabs, because they are the same
 * interaction over different material — and because the thing that made the old
 * console unusable was not the number of looks but the fact that finding one
 * meant scanning a single flat list of everything.
 *
 * Four things do the work:
 *
 *   * **One slot per tab.** A colour cannot appear on the Move tab, so nothing
 *     you press here can disturb what another tab set.
 *   * **Pill filters per fixture type.** "MH Red" and "Pin Ball Glow" are both
 *     colours but they are different decisions about different lights, and they
 *     were sharing one list of 51. Each group also holds its own selection, so
 *     a pinspot colour and a mover colour are up at the same time.
 *   * **A chase's own steps are filed under the chase.** "Spotlight Step 1..4"
 *     are meaningless alone and were four entries wide in a list of twenty.
 *   * **A filter box**, because past about thirty items typing beats scanning.
 */

/** "corner movers" is what the rig file calls them; "Movers" is what a person
 *  calls them at 2am. Falls through to the raw tag for anything unrecognised,
 *  so a new rig's groups still appear rather than vanishing. */
const GROUP_LABELS: Record<string, string> = {
  "corner movers": "Movers", movers: "Movers", pinspots: "Pinspots",
  pars: "Pars", bars: "Bars",
};
const groupLabel = (g: string) => GROUP_LABELS[g] ?? g;

const KIND_LABELS: Record<string, string> = {
  pose: "Positions", path: "Moves", mixed: "Position + colour",
  color: "Colours", color_path: "Colour chases",
  intensity: "Levels", level_path: "Level chases",
};
// Per slot, because the first group is the one that opens by default and the
// right default differs. A single global order opened "Colour chases" (5 of
// them) on the Color tab and left the 51 plain colours collapsed, which is the
// wrong way round: the common case should be the visible one.
const KIND_ORDER: Record<Slot, string[]> = {
  movement: ["path", "pose", "mixed"],
  color: ["color", "color_path"],
  level: ["level_path", "intensity"],
};

export function LookPicker({ state, send, slot, title, empty }: {
  state: EngineState; send: (c: Command) => void;
  slot: Slot; title: string;
  /** Wording for the "nothing loaded" case, per slot. */
  empty?: string;
}) {
  const [filter, setFilter] = useState("");
  const [openKind, setOpenKind] = useState<string | null>(null);
  const [showSteps, setShowSteps] = useState(false);
  const [group, setGroup] = useState<string | null>(null);

  const loaded = state.selection[slot] ?? {};
  const mine = state.looks.filter((l) => l.slot === slot);

  // Only groups this slot actually has looks for: offering a "Pinspots" filter
  // on the Move tab, where pinspots cannot move, is a dead end.
  const groups = state.groups.filter((g) => mine.some((l) => l.groups.includes(g)));
  const active = group && groups.includes(group) ? group : null;

  const needle = filter.trim().toLowerCase();
  const inGroup = active ? mine.filter((l) => l.groups.includes(active)) : mine;
  const steps = inGroup.filter((l) => l.step_of);
  const matching = inGroup.filter((l) =>
    (showSteps || !l.step_of || Object.values(loaded).includes(l.name))
    && l.name.toLowerCase().includes(needle));

  // By KIND, within the chosen fixture type. Named `byKind` rather than
  // `groups`, which now means fixture type.
  const byKind = new Map<string, LookInfo[]>();
  for (const look of matching) {
    const kind = look.kind ?? "look";
    if (!byKind.has(kind)) byKind.set(kind, []);
    byKind.get(kind)!.push(look);
  }
  const order = KIND_ORDER[slot];
  const kinds = [...order.filter((k) => byKind.has(k)),
                 ...[...byKind.keys()].filter((k) => !order.includes(k))];
  // While filtering, show everything — hiding a match behind a collapsed group
  // defeats the point of having typed.
  const expanded = needle ? kinds : (openKind ? [openKind] : kinds.slice(0, 1));

  const clearable = slot !== "movement"
    && (active ? loaded[active] : Object.keys(loaded).length > 0);

  return (
    <Card title={title} right={
      clearable ? (
        <button className="small" onClick={() => send({
          type: "clear_slot", slot, ...(active ? { group: active } : {}),
        })}>
          Clear{active ? ` ${groupLabel(active)}` : " all"}
        </button>
      ) : undefined
    }>
      {groups.length > 1 && (
        <div className="pills" role="group" aria-label="fixture type">
          <button className={active === null ? "on" : ""}
                  onClick={() => setGroup(null)}>All</button>
          {groups.map((g) => (
            <button key={g} className={active === g ? "on" : ""}
                    onClick={() => setGroup(g)}>
              {groupLabel(g)}
              {loaded[g] && <span className="dot" aria-label="loaded" />}
            </button>
          ))}
        </div>
      )}

      <p className="small muted" style={{ marginTop: groups.length > 1 ? "0.6rem" : 0 }}>
        {Object.keys(loaded).length === 0
          ? (empty ?? "Nothing loaded — this slot is not contributing.")
          : groups.filter((g) => loaded[g]).map((g) => (
              <span key={g} style={{ display: "block" }}>
                {groupLabel(g)}: <b>{loaded[g]}</b>
              </span>
            ))}
      </p>

      <input className="field" placeholder="filter…" value={filter}
             aria-label={`filter ${slot} looks`}
             onChange={(e) => setFilter(e.target.value)}
             style={{
               width: "100%", padding: "0.55rem", minHeight: 44,
               background: "var(--panel-2)", border: "1px solid var(--line)",
               borderRadius: 8, marginBottom: "0.6rem",
             }} />

      {kinds.map((kind) => (
        <div key={kind} style={{ marginBottom: "0.5rem" }}>
          <button className="small"
                  style={{ width: "100%", justifyContent: "flex-start" }}
                  onClick={() => setOpenKind(openKind === kind ? null : kind)}>
            {KIND_LABELS[kind] ?? kind} · {byKind.get(kind)!.length}
          </button>
          {expanded.includes(kind) && (
            <div className="grid tiles" style={{ marginTop: "0.4rem" }}>
              {byKind.get(kind)!.map((l) => (
                <button key={l.name}
                        className={Object.values(loaded).includes(l.name) ? "on" : ""}
                        onClick={() => send({ type: "select_look", name: l.name })}>
                  {l.name}
                  {l.step_of && <div className="small muted">of {l.step_of}</div>}
                  {/* Which lights this touches, when the list is not already
                      filtered to one type. */}
                  {!active && l.groups.length > 0 && !l.step_of && (
                    <div className="small muted">
                      {l.groups.map(groupLabel).join(", ")}
                    </div>
                  )}
                </button>
              ))}
            </div>
          )}
        </div>
      ))}

      {matching.length === 0 && (
        <p className="small muted">Nothing matches “{filter}”.</p>
      )}

      {steps.length > 0 && (
        <button className="small" style={{ width: "100%" }}
                onClick={() => setShowSteps(!showSteps)}>
          {showSteps ? "Hide" : "Show"} {steps.length} individual chase step(s)
        </button>
      )}
    </Card>
  );
}
