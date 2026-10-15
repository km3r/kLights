import type { EngineState, FixtureState, LookInfo } from "../types";

/**
 * Who a role plays on: the rig's groups and fixtures as an editor offers
 * them, and how far its heads can reach.
 */
/** The fixtures a role's tag reaches, found the way the engine finds them
 *  (`Rigging.tagged`): carrying the tag, or named it. */
export function tagged(state: EngineState | null | undefined, tag: string): FixtureState[] {
  return (state?.fixtures ?? []).filter((f) => f.tags.includes(tag) || f.name === tag);
}

/** Every tag on the rig's fixtures, the widest first. Not the engine's
 *  `groups`: those fold tags that name the same fixtures into one (despacio's
 *  heads are both "movers" and "corner movers") to make filters, and a role
 *  may name any of them. */
export function rigTags(state: EngineState | null | undefined): string[] {
  const count = new Map<string, number>();
  for (const f of state?.fixtures ?? []) for (const t of f.tags) count.set(t, (count.get(t) ?? 0) + 1);
  return [...count].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([t]) => t);
}

/** How many fixtures a tag reaches on this rig -- or that it reaches none, so
 *  a role on it lights nothing. Nothing is said with no engine to ask. */
export function Reach({ state, tag, optional }: {
  state: EngineState | null | undefined; tag: string; optional?: boolean;
}) {
  if (!state || !tag) return null;
  const found = tagged(state, tag);
  if (found.length) {
    return <span className="small muted d-reach" title={found.map((f) => f.name).join(", ")}>
      {found.length} fixture{found.length === 1 ? "" : "s"}</span>;
  }
  return <span className={`small d-reach${optional ? " muted" : " none"}`}
               title="No fixture carries this tag or has this name. Tags are set on the console's Setup tab">
    {optional ? "none here (optional)" : "no fixtures on this rig"}</span>;
}

/** The looks a picker offers: none retired for something better, and no
 *  single step of a chase -- the console files those under their chase. */
export function offeredLooks(looks: LookInfo[] | undefined): LookInfo[] {
  return (looks ?? []).filter((l) => !l.retired && !l.step_of);
}

/** Where a fixture's tags are set: the console's Setup tab. */
export function EditTags() {
  return <a className="small d-link" href="#setup"
            title="Tags are set per fixture on the console's Setup tab (Design mode)">Edit tags</a>;
}

/**
 * Which fixtures each of a routine's roles plays on, for ONE use of it -- a
 * clip, a template set's pick: its default tag, or another tag, or one
 * fixture by name (`bind`). A binding the routine has no role for -- left by
 * an edit to the routine -- is shown so it can be taken off.
 */
export function RoleBinds({ label, roles, bind, state, onChange }: {
  label: string;
  roles: Record<string, { default: string; optional?: boolean }>;
  bind: Record<string, string> | undefined;
  state: EngineState | null | undefined;
  onChange: (bind: Record<string, string> | undefined) => void;
}) {
  const tags = rigTags(state);
  const names = (state?.fixtures ?? []).map((f) => f.name).filter((n) => !tags.includes(n));
  const setRole = (role: string, value: string) => {
    const next = { ...(bind ?? {}) };
    if (value) next[role] = value; else delete next[role];
    onChange(Object.keys(next).length ? next : undefined);
  };
  const stale = Object.keys(bind ?? {}).filter((r) => !(r in roles));
  return (
    <div className="d-binds" role="group" aria-label={`${label} roles`}>
      <span className="small muted">Plays on</span>
      {Object.entries(roles).map(([role, spec]) => {
        const value = bind?.[role] ?? "";
        const known = !value || tags.includes(value) || names.includes(value);
        return (
          <span key={role} className="d-bind">
            <span className="mono small">{role}</span>
            <select value={value} aria-label={`${role} plays on`}
                    onChange={(e) => setRole(role, e.target.value)}>
              <option value="">its default, {spec.default}</option>
              {!known && <option value={value}>{value} (not on this rig)</option>}
              {tags.length > 0 && (
                <optgroup label="Tags">{tags.map((t) => <option key={t} value={t}>{t}</option>)}</optgroup>)}
              {names.length > 0 && (
                <optgroup label="One fixture">
                  {names.map((n) => <option key={n} value={n}>{n}</option>)}</optgroup>)}
            </select>
            <Reach state={state} tag={value || spec.default} optional={spec.optional} />
          </span>
        );
      })}
      {stale.map((role) => (
        <span key={role} className="d-bind small d-error">
          binds {role}, which this routine has no role for
          <button className="small" aria-label={`drop the binding for ${role}`}
                  onClick={() => setRole(role, "")}>×</button>
        </span>
      ))}
      <EditTags />
    </div>
  );
}
