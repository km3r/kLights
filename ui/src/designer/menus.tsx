import { Fragment, useEffect, useRef, useState } from "react";
import { normalizeName } from "./model";

/**
 * The menu of what can be added where a lane was clicked.
 */
/** A menu that closes on a press anywhere outside it (`.d-ctx`), or Escape. */
export function useMenuDismiss(open: boolean, close: () => void): void {
  const closeRef = useRef(close);
  closeRef.current = close;
  useEffect(() => {
    if (!open) return;
    const handler = (e: Event) => {
      if (e instanceof KeyboardEvent && e.key !== "Escape") return;
      if (e instanceof MouseEvent && (e.target as Element | null)?.closest?.(".d-ctx")) return;
      closeRef.current();
    };
    addEventListener("mousedown", handler);
    addEventListener("keydown", handler);
    return () => { removeEventListener("mousedown", handler); removeEventListener("keydown", handler); };
  }, [open]);
}

/** One thing a lane's menu offers: what it shows, the words it is found by,
 *  the heading it is filed under, and what choosing it does. */
export interface PickEntry {
  key: string;
  label: React.ReactNode;
  text?: string;
  group?: string;
  add: () => void;
}

/** Past this many entries, the menu has a search box (and starts in it). */
const PICK_SEARCH = 12;

/**
 * What can go where a lane's empty space was clicked, as a menu at the
 * pointer: the entries under their headings, with a search box once there are
 * enough to need one -- a show's routines and its rig's looks run to
 * hundreds. Enter in the search box takes the first match.
 */
export function PickMenu({ label, head, x, y, entries, empty }: {
  label: string; head: React.ReactNode; x: number; y: number;
  entries: PickEntry[];
  /** Said when there is nothing at all to offer. */
  empty: string;
}) {
  const [query, setQuery] = useState("");
  const search = entries.length > PICK_SEARCH;
  const words = normalizeName(query).split(" ").filter(Boolean);
  const shown = entries.filter((e) => words.every((w) => normalizeName(e.text ?? e.key).includes(w)));
  // Placed for the whole list, so it stays put while a search narrows it.
  const height = Math.min(entries.length * 28 + (search ? 110 : 60), 420, innerHeight * 0.6);
  return (
    <div className="d-ctx d-pick" role="menu" aria-label={label}
         style={{ left: Math.max(8, Math.min(x, innerWidth - 240)),
                  top: Math.max(8, Math.min(y, innerHeight - height - 8)) }}>
      <span className="d-ctx-head small muted">{head}</span>
      {search && (
        <input type="search" value={query} placeholder="Search" aria-label={`search ${label}`}
               autoFocus onChange={(e) => setQuery(e.target.value)}
               onKeyDown={(e) => {
                 if (e.key !== "Enter" || !shown.length) return;
                 e.preventDefault();
                 shown[0]!.add();
               }} />)}
      <div className="d-pick-list">
        {shown.map((e, i) => (
          <Fragment key={e.key}>
            {e.group && e.group !== shown[i - 1]?.group && (
              <span className="d-ctx-group">{e.group}</span>)}
            <button role="menuitem" autoFocus={!search && i === 0} onClick={e.add}>{e.label}</button>
          </Fragment>
        ))}
      </div>
      {!shown.length && (
        <span className="small muted d-ctx-head">{entries.length ? "Nothing matches." : empty}</span>)}
    </div>
  );
}
