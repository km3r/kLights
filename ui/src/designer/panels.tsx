import { useCallback, useState } from "react";

/**
 * Which of Studio's side panels are open: the library's sidebar (`nav`) and
 * details (`side`), and the editors' right-hand panel (`edit`).
 *
 * Kept per browser rather than per page, because it is a property of the
 * screen: a laptop wants the timeline wide, a big monitor has room for
 * everything, and that does not change between one track and the next.
 */

export interface Panels { nav: boolean; side: boolean; edit: boolean }

const KEY = "klights.studio.panels";
const OPEN: Panels = { nav: true, side: true, edit: true };

function read(): Panels {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? { ...OPEN, ...(JSON.parse(raw) as Partial<Panels>) } : OPEN;
  } catch {
    return OPEN;
  }
}

export function usePanels(): [Panels, (which: keyof Panels) => void] {
  const [panels, setPanels] = useState<Panels>(read);
  const toggle = useCallback((which: keyof Panels) => {
    setPanels((prev) => {
      const next = { ...prev, [which]: !prev[which] };
      try { localStorage.setItem(KEY, JSON.stringify(next)); } catch { /* this page only */ }
      return next;
    });
  }, []);
  return [panels, toggle];
}

/** The button that opens and closes a panel, drawn as the panel it moves. */
export function PanelToggle({ open, side, label, onToggle }: {
  open: boolean; side: "left" | "right"; label: string; onToggle: () => void;
}) {
  const x = side === "left" ? 3 : 15;
  return (
    <button className={`s-icon s-panel-toggle${open ? " on" : ""}`} aria-pressed={open}
            aria-label={`${open ? "Hide" : "Show"} the ${label}`}
            title={`${open ? "Hide" : "Show"} the ${label}`} onClick={onToggle}>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           strokeWidth="1.8" aria-hidden="true">
        <rect x="3" y="4" width="18" height="16" rx="2" />
        <rect x={x} y="4" width="6" height="16" rx="1" fill={open ? "currentColor" : "none"}
              opacity={open ? 0.55 : 1} />
      </svg>
    </button>
  );
}
