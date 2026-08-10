import { createContext, useCallback, useContext, useState } from "react";
import type { ReactNode } from "react";
import type { Mode } from "./types";

/**
 * Perform or Design — how much of the console is on screen.
 *
 * The console grew every tab it needed and then kept growing: patch editors,
 * calibration, per-fixture readouts, a strobe policy. All of it is necessary at
 * load-in and none of it is anything you want under your thumb at 1am, when the
 * only things that matter are the cue list, the presets, the master and the
 * tempo.
 *
 * This is NOT a permission. `Tier` is what the engine enforces from the token,
 * and it is the only thing that decides whether a command is obeyed. This is a
 * local preference about screen real estate, deliberately one tap from being
 * turned off — a mode you cannot leave is a mode that traps someone with a rig
 * to fix and no way to fix it.
 *
 * What Perform hides is chosen by one rule: does it CHANGE THE SHOW, or does it
 * describe the show? Diagnostics, readouts and setup go; every control that
 * makes light stays.
 */

const ModeContext = createContext<Mode>("design");

/** True when the full console is showing — the guard for design-only cards. */
export function useDesign(): boolean {
  return useContext(ModeContext) === "design";
}

export const ModeProvider = ModeContext.Provider;

/**
 * Where a client starts.
 *
 * A phone is a performance surface and a laptop is a setup surface, near
 * enough, and getting the default right is what stops the toggle from being
 * something everyone has to find. Coarse pointer OR a narrow window: a tablet
 * held at the desk is the case that width alone would get wrong.
 */
function defaultMode(): Mode {
  const coarse = typeof matchMedia === "function"
    && matchMedia("(pointer: coarse)").matches;
  return coarse || innerWidth < 900 ? "perform" : "design";
}

export function useMode(): [Mode, (m: Mode) => void] {
  // Persisted per device, because it is a property of the thing you are holding
  // rather than of the show. A phone that reopens into Design after a reload
  // mid-set is the whole problem back again.
  const [mode, setStored] = useState<Mode>(() => {
    const saved = localStorage.getItem("klights.mode");
    return saved === "perform" || saved === "design" ? saved : defaultMode();
  });
  const set = useCallback((next: Mode) => {
    localStorage.setItem("klights.mode", next);
    setStored(next);
  }, []);
  return [mode, set];
}

/**
 * A card that only exists in Design mode.
 *
 * Rendered as nothing rather than hidden with CSS, so a Perform-mode phone is
 * not also paying to lay out sixteen fixture readouts it will never show.
 */
export function DesignOnly({ children }: { children: ReactNode }) {
  return useDesign() ? <>{children}</> : null;
}
