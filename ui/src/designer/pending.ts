import type { RoutineDoc, TemplateSetDoc } from "./model";

/**
 * A start handed from one Studio page to the page it opens: "make a timeline
 * for this track, drafted from that set", "a new routine, as this copy". The
 * page that opens reads it, applies it as an unsaved edit, and clears it --
 * nothing is written until Save there.
 *
 * Kept in this tab's sessionStorage, keyed to the document it is for, and
 * only for a minute: a click whose page never opened must not surprise a
 * later visit. Read without taking (`peekPending`) and cleared once used
 * (`clearPending`), because React may run a page's setup twice and a read
 * that removed it would hand the second run nothing.
 */

const KEY = "klights.studio.pending";
export const PENDING_MS = 60_000;

export type Pending =
  /** A timeline to start: drafted from a set, or copied from another track. */
  | { kind: "timeline"; id: string; set?: string; copy?: string; at: number }
  | { kind: "routine"; id: string; doc: RoutineDoc; at: number }
  | { kind: "template"; id: string; doc: TemplateSetDoc; at: number };

type Of<K extends Pending["kind"]> = Extract<Pending, { kind: K }>;
/** Omit, applied to each kind of Pending in turn. */
type WithoutAt<T> = T extends unknown ? Omit<T, "at"> : never;

export function putPending(p: WithoutAt<Pending>): void {
  try { sessionStorage.setItem(KEY, JSON.stringify({ ...p, at: Date.now() })); } catch { /* opens plain */ }
}

export function peekPending<K extends Pending["kind"]>(kind: K, id: string): Of<K> | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return null;
    const p = JSON.parse(raw) as Partial<Pending>;
    if (p.kind !== kind || p.id !== id) return null;
    if (typeof p.at !== "number" || Date.now() - p.at > PENDING_MS) {
      sessionStorage.removeItem(KEY);
      return null;
    }
    return p as Of<K>;
  } catch {
    return null;
  }
}

export function clearPending(kind: Pending["kind"], id: string): void {
  try {
    const raw = sessionStorage.getItem(KEY);
    const p = raw ? (JSON.parse(raw) as Partial<Pending>) : null;
    if (p && p.kind === kind && p.id === id) sessionStorage.removeItem(KEY);
  } catch { /* nothing to clear */ }
}
