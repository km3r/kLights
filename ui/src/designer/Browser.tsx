import { useState } from "react";
import { PLACE_MIME } from "./edit";
import type { Placeable } from "./edit";
import { normalizeName } from "./model";
import type { PaletteSummary, RoutineSummary } from "./model";
import type { LookInfo } from "../types";

/**
 * The timeline's browser: what can go on a lane, in one column on the left --
 * routines (filed by their folders), palettes (this track's, then the
 * library's), hits, and this rig's own looks and presets (as snapshots). Click
 * one to place it at the playhead on the lane it belongs on, or drag it onto
 * the lane and the beat you want. A click on a lane's empty space offers the
 * same things for that lane.
 *
 * It replaces the routine buttons that sat in the right-hand panel, which grew
 * into a wall once a show had more than a dozen routines.
 */

type Tab = "routines" | "palettes" | "hits" | "looks";
const TABS: Tab[] = ["routines", "palettes", "hits", "looks"];
const SLOT_ORDER = ["movement", "color", "level"] as const;
const HITS = [
  { hit: "flash", label: "Flash", text: "a burst, decaying" },
  { hit: "strobe", label: "Strobe", text: "for a bar" },
  { hit: "blackout", label: "Blackout", text: "a beat of dark" },
] as const;

function drag(what: Placeable) {
  return (e: React.DragEvent) => {
    e.dataTransfer.setData(PLACE_MIME, JSON.stringify(what));
    e.dataTransfer.effectAllowed = "copy";
  };
}

export function Browser({ routines, palettes, library, looks, presets, onPlace }: {
  routines: RoutineSummary[];
  /** This track's own palette names. */
  palettes: string[];
  library: PaletteSummary[];
  /** This rig's looks and presets: a timeline that uses them is this rig's own. */
  looks: LookInfo[];
  presets: string[];
  onPlace: (what: Placeable) => void;
}) {
  const [tab, setTab] = useState<Tab>("routines");
  const [query, setQuery] = useState("");
  const words = normalizeName(query).split(" ").filter(Boolean);
  const matches = (text: string) => words.every((w) => normalizeName(text).includes(w));

  const shown = routines.filter((r) => matches(`${r.name ?? ""} ${r.id} ${r.folder ?? ""}`));
  const folders = [...new Set(shown.map((r) => r.folder ?? ""))]
    .sort((a, b) => (a === "" ? 1 : b === "" ? -1 : a.localeCompare(b)));
  const fromLibrary = library.filter((p) => !palettes.includes(p.name) && matches(p.name));
  const shownLooks = looks.filter((l) => !l.retired && !l.step_of && matches(`${l.name} ${l.slot}`));

  return (
    <aside className="d-browser" aria-label="browser">
      <div className="d-tabs" role="tablist" aria-label="browse">
        {TABS.map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "on" : ""}
                  onClick={() => setTab(t)}>{t[0]!.toUpperCase() + t.slice(1)}</button>
        ))}
      </div>
      {tab !== "hits" && (
        <input type="search" value={query} onChange={(e) => setQuery(e.target.value)}
               placeholder={`Search ${tab}`} aria-label={`search ${tab}`} />)}

      {tab === "routines" && (
        <div className="d-browse-list">
          {folders.map((f) => (
            <div key={f || "unfiled"} role="group" aria-label={f || "unfiled"}>
              {folders.length > 1 && <span className="d-browse-group">{f || "Unfiled"}</span>}
              {shown.filter((r) => (r.folder ?? "") === f).map((r) => {
                const slots = new Set((r.lanes ?? []).map((l) => l.target));
                return (
                  <button key={r.id} className="d-browse-item" draggable
                          onDragStart={drag({ kind: "routine", id: r.id })}
                          onClick={() => onPlace({ kind: "routine", id: r.id })}
                          title={`${r.bars} bars${r.loop ? ", loops" : ""}${r.rig ? ", this rig only" : ""}`
                            + ". Click to place at the playhead on the scene lane, or drag onto a lane"}>
                    <span className="d-browse-name">{r.name || r.id}</span>
                    <span className="d-browse-meta" aria-hidden="true">
                      <span className="mono">{r.bars}</span>
                      <span className="d-slots">
                        {SLOT_ORDER.map((s) => <i key={s} className={slots.has(s) ? s : ""} />)}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>
          ))}
          {!shown.length && <span className="muted small">No routine matches.</span>}
        </div>
      )}

      {tab === "palettes" && (
        <div className="d-browse-list">
          <span className="d-browse-group">This track's</span>
          {palettes.filter(matches).map((name) => (
            <button key={name} className="d-browse-item" draggable
                    onDragStart={drag({ kind: "palette", name })}
                    onClick={() => onPlace({ kind: "palette", name })}
                    title="Click to switch to it at the playhead, on the palette lane">
              <span className="d-browse-name">{name}</span></button>
          ))}
          {!palettes.length && <span className="muted small">None yet.</span>}
          {fromLibrary.length > 0 && <span className="d-browse-group">From the library</span>}
          {fromLibrary.map((p) => {
            const colours = { primary: p.primary, secondary: p.secondary, accent: p.accent };
            return (
              <button key={p.id} className="d-browse-item" draggable
                      onDragStart={drag({ kind: "palette", name: p.name, colours })}
                      onClick={() => onPlace({ kind: "palette", name: p.name, colours })}
                      title="Placing it copies it into this track first: the copy is this track's own">
                <span className="d-browse-name">{p.name}</span>
                <span className="d-swatch-row" aria-hidden="true">
                  <i style={{ background: p.primary }} /><i style={{ background: p.secondary }} />
                  <i style={{ background: p.accent }} />
                </span>
              </button>
            );
          })}
        </div>
      )}

      {tab === "hits" && (
        <div className="d-browse-list">
          {HITS.map((h) => (
            <button key={h.hit} className="d-browse-item" draggable
                    onDragStart={drag({ kind: "hit", hit: h.hit })}
                    onClick={() => onPlace({ kind: "hit", hit: h.hit })}>
              <span className="d-browse-name">{h.label}</span>
              <span className="muted small" aria-hidden="true">{h.text}</span>
            </button>
          ))}
        </div>
      )}
      {tab === "looks" && (
        <div className="d-browse-list">
          <span className="muted small">This rig's own: a track that uses one plays on this
            rig only.</span>
          {SLOT_ORDER.map((slot) => {
            const these = shownLooks.filter((l) => l.slot === slot);
            if (!these.length) return null;
            return (
              <div key={slot} role="group" aria-label={`${slot} looks`}>
                <span className="d-browse-group">{slot === "color" ? "colour" : slot}</span>
                {these.map((l) => (
                  <button key={l.name} className="d-browse-item" draggable
                          onDragStart={drag({ kind: "look", name: l.name })}
                          onClick={() => onPlace({ kind: "look", name: l.name })}
                          title="Click to place at the playhead on the scene lane, or drag onto a lane">
                    <span className="d-browse-name">{l.name}</span></button>
                ))}
              </div>
            );
          })}
          {presets.filter(matches).length > 0 && (
            <div role="group" aria-label="snapshots">
              <span className="d-browse-group">Snapshots</span>
              {presets.filter(matches).map((name) => (
                <button key={name} className="d-browse-item" draggable
                        onDragStart={drag({ kind: "snapshot", preset: name })}
                        onClick={() => onPlace({ kind: "snapshot", preset: name })}
                        title="A preset, all three slots: click to place on the scene lane, or drag onto one">
                  <span className="d-browse-name">{name}</span></button>
              ))}
            </div>
          )}
          {!shownLooks.length && !presets.filter(matches).length && (
            <span className="muted small">{looks.length || presets.length ? "Nothing matches."
              : "No looks or presets on this rig."}</span>)}
        </div>
      )}
      <p className="muted small">Click to place at the playhead, or drag onto a lane.</p>
    </aside>
  );
}
