import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useHelp } from "../components";
import type { Engine } from "./Designer";

/**
 * What every right-hand panel in Studio is made of, so each reads the same way
 * and says each thing once.
 *
 *   head       what it is (a kicker), its name, one line of facts, its badges,
 *              and a ⋯ menu of what can be done to its file
 *   primary    the one thing most often done next, as a wide button
 *   task       the form a menu item opened (rename, duplicate...), in a box
 *   sections   a small heading each, with a count or a tool at its right
 *
 * The library's details panel (Studio.tsx) and the editors' side panels share
 * the section heading; the editors' sections also fold, and stay folded per
 * browser, like the panels themselves (panels.tsx).
 */

export interface MenuItem {
  label: string;
  /** Said under the label, for a disabled item: why. */
  note?: string;
  danger?: boolean;
  disabled?: boolean;
  /** A link, else `onClick`. A disabled one is drawn as a disabled item. */
  href?: string;
  onClick?: () => void;
}

/** Why a write is refused on a page without the configure token. */
export const READ_ONLY = "Read only: open Studio from the link the engine printed to change "
  + "the show folder.";

/** A ⋯ button and its menu, closed by a pick, Escape, or a click or focus
 *  anywhere outside it -- so opening another menu closes this one. */
export function MoreMenu({ label, items }: { label: string; items: MenuItem[] }) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLSpanElement | null>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: Event) => {
      if (!wrap.current?.contains(e.target as Node | null)) setOpen(false);
    };
    const escape = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    addEventListener("mousedown", away);
    addEventListener("focusin", away);
    addEventListener("keydown", escape);
    return () => {
      removeEventListener("mousedown", away);
      removeEventListener("focusin", away);
      removeEventListener("keydown", escape);
    };
  }, [open]);
  return (
    <span className="s-menu-wrap" ref={wrap}>
      <button className="s-icon" aria-label={`more for ${label}`} aria-haspopup="menu"
              aria-expanded={open} title="More"
              onClick={(e) => { e.stopPropagation(); setOpen(!open); }}>⋯</button>
      {open && (
        <span className="s-menu" role="menu" aria-label={`${label} actions`}>
          {items.map((it) => it.href && !it.disabled ? (
            <a key={it.label} role="menuitem" href={it.href}
               className={it.danger ? "s-danger" : ""} onClick={() => setOpen(false)}>
              <span>{it.label}</span>
              {it.note && <span className="s-menu-note">{it.note}</span>}
            </a>
          ) : (
            <button key={it.label} role="menuitem" disabled={it.disabled}
                    className={it.danger ? "s-danger" : ""}
                    onClick={(e) => { e.stopPropagation(); setOpen(false); it.onClick?.(); }}>
              <span>{it.label}</span>
              {it.note && <span className="s-menu-note">{it.note}</span>}
            </button>
          ))}
        </span>
      )}
    </span>
  );
}

/** The panel's head: what it is, its name, a line of facts, badges, the menu. */
export function DetailHead({ kind, title, meta, badges, menu }: {
  kind: ReactNode;
  /** The name: text for a heading, or a field to edit it in. */
  title: ReactNode;
  meta?: ReactNode; badges?: ReactNode; menu?: ReactNode;
}) {
  return (
    <header className="s-dh">
      <div className="s-dh-row">
        <span className="s-kicker">{kind}</span>
        {badges && <span className="s-dh-badges">{badges}</span>}
        <span className="grow" />
        {menu}
      </div>
      {typeof title === "string" ? <h2>{title}</h2> : title}
      {meta && <div className="s-dh-meta">{meta}</div>}
    </header>
  );
}

/** A badge in a panel's head: good (green), warn (amber), info (blue) or plain. */
export function Badge({ tone, children }: { tone?: "good" | "warn" | "info"; children: ReactNode }) {
  return <span className={`s-badge${tone ? ` ${tone}` : ""}`}>{children}</span>;
}

/** A section of a panel: a small heading, a count or a tool at its right. */
export function DetailSection({ title, label, count, aside, children }: {
  title: string;
  /** Its accessible name, when that is not its title. */
  label?: string;
  count?: number; aside?: ReactNode; children: ReactNode;
}) {
  return (
    <section className="s-ds" aria-label={label ?? title}>
      <header className="s-ds-head">
        <h3>{title}</h3>
        {count != null && <span className="s-n">{count}</span>}
        <span className="grow" />
        {aside}
      </header>
      {children}
    </section>
  );
}

/** The form a menu item opened, in a box under the head, with a way out --
 *  and, on a page that cannot write, why its button is disabled. */
export function Task({ title, label, readOnly, onCancel, children }: {
  title: string; label?: string; readOnly?: boolean; onCancel: () => void; children: ReactNode;
}) {
  return (
    <div className="s-task" role="group" aria-label={label ?? title}>
      <div className="s-task-head">
        <b>{title}</b>
        <span className="grow" />
        <button className="s-icon" aria-label="cancel" title="Cancel" onClick={onCancel}>×</button>
      </div>
      {children}
      {readOnly && <span className="muted small">{READ_ONLY}</span>}
    </div>
  );
}

/**
 * One write from a panel at a time: `run` marks it busy, and says what it did
 * (`onDone`) or why not (`error`). `after` runs once it worked, before
 * `onDone` -- which may take the panel away (a rename, a delete). `ask` sends
 * the engine a command and throws its refusal.
 */
export function useWrite(engine: Engine, onDone: (said: string) => void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = async (work: () => Promise<string>, after?: () => void) => {
    setBusy(true);
    setError(null);
    try {
      const said = await work();
      after?.();
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
  return { busy, error, setError, run, ask };
}

/** What the last change did, at the top of a panel. */
export function Said({ text }: { text: string | null }) {
  if (!text) return null;
  return <p className="s-said" role="status"><span aria-hidden="true">✓</span>{text}</p>;
}

/** A panel with nothing selected: what to do to see something. */
export function DetailEmpty({ children }: { children: ReactNode }) {
  return <p className="s-side-empty">{children}</p>;
}

// -- the editors' side panels -------------------------------------------------------

const FOLDED = "klights.studio.folded";

function readFolded(): string[] {
  try {
    const raw: unknown = JSON.parse(localStorage.getItem(FOLDED) ?? "[]");
    return Array.isArray(raw) ? raw.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

/**
 * A section of an editor's side panel: a heading that folds it, a "?" with
 * what it does, and a count or a tool at its right. Folded per browser, by
 * `id`, because which sections a person keeps open is a habit, not a property
 * of one routine or track. The tool at its right stays when it is folded:
 * Record must still say it is armed, and still disarm.
 */
export function SideSection({ id, title, topic, help, count, aside, children }: {
  id: string; title: ReactNode;
  /** What its "?" is called, when the title says more (a bar number, say). */
  topic?: string;
  help?: ReactNode; count?: number; aside?: ReactNode;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(() => !readFolded().includes(id));
  const name = topic ?? (typeof title === "string" ? title : id);
  const explain = useHelp(name);
  const toggle = () => {
    const next = !open;
    setOpen(next);
    try {
      const folded = new Set(readFolded());
      if (next) folded.delete(id); else folded.add(id);
      localStorage.setItem(FOLDED, JSON.stringify([...folded]));
    } catch { /* this page only */ }
  };
  return (
    <section className={`d-sec${open ? "" : " folded"}`} aria-label={name}>
      <header className="d-sec-head">
        <h3>
          <button className="d-sec-toggle" aria-expanded={open} onClick={toggle}>
            <span className="d-sec-caret" aria-hidden="true">{open ? "▾" : "▸"}</span>
            <span>{title}</span>
            {count != null && <span className="s-n">{count}</span>}
          </button>
        </h3>
        {help != null && explain.button}
        <span className="grow" />
        {aside}
      </header>
      {help != null && explain.panel(help)}
      {open && <div className="d-sec-body">{children}</div>}
    </section>
  );
}
