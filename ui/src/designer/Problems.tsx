import { useState } from "react";
import type { FolderProblem, ShowSummary } from "./model";

/**
 * What is wrong in the show folder, as the engine found it when it loaded:
 * files it could not read, and things one file asks of another that is not
 * there. Each names its file, and links to the page that edits it where
 * Studio has one.
 *
 * Errors are files the engine did not load (a broken one that loaded before
 * keeps playing its last good version, and says so); warnings load, and may
 * not do what was meant. Shown where a show is prepared: over the track list,
 * and on the Show settings page.
 */

/** The ids Studio has a page for, by the folder they live in. */
export interface Pages {
  tracks: string[];
  routines: string[];
  templates: string[];
  palettes: string[];
}

/** The folder's problems as rows, errors first. An engine that sends only the
 *  sentences gets them whole, with no file to link. */
export function problemsOf(show: ShowSummary | null): FolderProblem[] {
  if (!show) return [];
  return show.problems ?? [
    ...show.errors.map((text) => ({ level: "error" as const, file: null, text })),
    ...show.warnings.map((text) => ({ level: "warning" as const, file: null, text })),
  ];
}

/** The page that edits a file, or null where there is none: a file that did
 *  not load (its page would have nothing to open), a sync conflict copy. */
export function pageFor(file: string | null, pages: Pages): string | null {
  if (file === "show.json") return "#studio/show";
  const match = /^([a-z]+)\/(.+)\.json$/.exec(file ?? "");
  if (!match) return null;
  const [, dir, id] = match as unknown as [string, string, string];
  switch (dir) {
    // A track's page is its timeline; its waveform has no page of its own.
    case "tracks": case "timelines": case "waveforms":
      return pages.tracks.includes(id) ? `#studio/track/${id}` : null;
    case "routines": return pages.routines.includes(id) ? `#studio/routine/${id}` : null;
    case "templates": return pages.templates.includes(id) ? `#studio/templates/${id}` : null;
    case "palettes": return pages.palettes.includes(id) ? "#studio/palettes" : null;
    default: return null;
  }
}

function count(n: number, one: string): string {
  return `${n} ${one}${n === 1 ? "" : "s"}`;
}

export function FolderProblems({ show, pages, clean }: {
  show: ShowSummary | null; pages: Pages;
  /** Said when there is nothing wrong; nothing is shown without it. */
  clean?: string;
}) {
  const rows = problemsOf(show);
  const errors = rows.filter((r) => r.level === "error").length;
  const warnings = rows.length - errors;
  const kept = Object.keys(show?.failed ?? {}).length;
  // Errors are shown without being asked for; warnings alone wait for a click.
  const [asked, setAsked] = useState<boolean | null>(null);
  const open = asked ?? errors > 0;
  if (!show) return null;
  if (!rows.length) return clean ? <p className="muted small s-problems-clean">{clean}</p> : null;
  const summary = [errors ? count(errors, "error") : "", warnings ? count(warnings, "warning") : ""]
    .filter(Boolean).join(" and ");
  return (
    <section className={`s-problems${errors ? " bad" : ""}`} aria-label="show folder problems">
      <button className="s-problems-head" aria-expanded={open} onClick={() => setAsked(!open)}>
        <span className="s-warn-mark" aria-hidden="true">!</span>
        <span><b>The show folder has {summary}.</b>
          {kept > 0 && <> {count(kept, "file")} {kept === 1 ? "is" : "are"} broken and
            playing {kept === 1 ? "its" : "their"} last good version.</>}</span>
        <span className="grow" />
        <span className="muted small">{open ? "Hide" : "Show"}</span>
      </button>
      {open && (
        <ul className="s-problem-list" aria-label="problems">
          {rows.map((row, i) => {
            const href = pageFor(row.file, pages);
            return (
              <li key={i} className={row.level}>
                <span className="s-level">{row.level === "error" ? "Error" : "Warning"}</span>
                <div>
                  {row.file && (href
                    ? <a className="mono" href={href}>{row.file}</a>
                    : <span className="mono">{row.file}</span>)}
                  {row.file ? " " : ""}{row.text}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
