import { useEffect, useRef } from "react";
import { clipBoard, clipOps, removeSelected } from "./clipboard";
import type { ClipKind } from "./clipboard";
import { parsePointId } from "./ids";
import type { Edits } from "./history";

/**
 * The editors' keys: Space plays and pauses wherever the focus is, Delete
 * removes, and a text field keeps its own keys.
 */
/** Whether a key went to something being typed in, where it is the field's. */
export function typing(e: KeyboardEvent): boolean {
  const el = e.target as HTMLElement | null;
  if (!el || !el.tagName) return false;
  return el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT"
    || el.isContentEditable;
}

/** The inputs that take no text: a button, a tick box, a slider. */
const NOT_TEXT = new Set(["button", "checkbox", "color", "file", "image", "radio", "range",
                          "reset", "submit"]);

/** Whether a key went to a field that takes text. Narrower than `typing`: a
 *  menu, a tick box or a slider is something to operate, not to type in. */
export function inText(e: KeyboardEvent): boolean {
  const el = e.target as HTMLElement | null;
  if (!el || !el.tagName) return false;
  if (el.tagName === "INPUT") return !NOT_TEXT.has((el as HTMLInputElement).type);
  return el.tagName === "TEXTAREA" || el.isContentEditable;
}

/** Whether a key is the one that plays and stops: Space on its own, anywhere
 *  but in a field that takes text. */
function playKey(e: KeyboardEvent): boolean {
  return e.key === " " && !e.metaKey && !e.ctrlKey && !e.altKey && !inText(e);
}

/** A key listener on the page that always runs the latest `handler`. */
export function useKeys(handler: (e: KeyboardEvent) => void,
                        type: "keydown" | "keyup" = "keydown"): void {
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => {
    const on = (e: KeyboardEvent) => ref.current(e);
    addEventListener(type, on);
    return () => removeEventListener(type, on);
  }, [type]);
}

/** The designer's plain keys, the same in both editors: Space plays and
 *  stops, Delete (or Backspace) removes what is selected, Escape lets go of it. */
export function useEditorKeys({ history, selected, setSelected, playPause, clip }: {
  history: Edits; selected: string | null; setSelected: (id: string | null) => void;
  playPause: () => void;
  /** Copy, cut, paste, duplicate (Ctrl/Cmd C X V D) and split at the
   *  playhead (S): what kind of document this is, and where the playhead is. */
  clip?: { kind: ClipKind; beat: () => number };
}): void {
  // Space is the transport's wherever the focus is, short of a text field:
  // the button just clicked, a menu or a tick box does not take it instead.
  // Held down, it is still one press.
  useKeys((e) => {
    if (!playKey(e)) return;
    e.preventDefault();
    if (!e.repeat) playPause();
  });
  // A button is pressed as the key comes back up, and in Firefox whether or
  // not the way down was refused -- so the way up is refused too.
  useKeys((e) => { if (playKey(e)) e.preventDefault(); }, "keyup");
  useKeys((e) => {
    if (typing(e) || e.altKey) return;
    const item = selected && !parsePointId(selected) ? selected : null;
    if (clip && (e.metaKey || e.ctrlKey)) {
      const k = e.key.toLowerCase();
      const at = () => Math.max(0, history.snapBeat(clip.beat()));
      if (k === "c" && item) { e.preventDefault(); clipOps.copy(history, item, clip.kind); }
      else if (k === "x" && item) { e.preventDefault(); clipOps.cut(history, item, clip.kind); setSelected(null); }
      else if (k === "v" && clipBoard()?.kind === clip.kind) {
        e.preventDefault();
        setSelected(clipOps.paste(history, clip.kind, at()));
      } else if (k === "d" && item) {
        e.preventDefault();
        setSelected(clipOps.duplicate(history, item, clip.kind) ?? item);
      }
      return;
    }
    if (e.metaKey || e.ctrlKey) return;
    if (clip && item && (e.key === "s" || e.key === "S")) {
      e.preventDefault();
      clipOps.split(history, item, history.snapBeat(clip.beat()));
      return;
    }
    if ((e.key === "Delete" || e.key === "Backspace") && selected) {
      e.preventDefault();
      const id = selected;
      history.apply((d) => removeSelected(d, id));
      setSelected(null);
    } else if (e.key === "Escape" && selected) {
      setSelected(null);
    }
  });
}
