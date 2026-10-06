import { useMemo, useState, useSyncExternalStore } from "react";
import type { ReactNode } from "react";
import { apiFetch } from "../useEngine";
import { ALL_TRACKS } from "../studioRoute";
import { mmss, normalizeName } from "./model";
import type { Catalogue, CataloguePlaylist, CatalogueTrack, TrackLine } from "./model";
import { DetailEmpty, DetailHead, DetailSection } from "./detail";

/**
 * The DJ's rekordbox collection, to pick tracks from: the playlist tree in
 * Studio's sidebar, a playlist's tracks (or a search) in the main column, and
 * how much of the playlist the show covers beside it.
 *
 * The engine does not read rekordbox; the prep bridge does, in a child
 * process (engine/collection.py), and this page gets what it read. Loaded only
 * when asked -- a big collection is a megabyte -- then kept for the life of the
 * page, so going into a timeline and back does not read it again. Reload
 * re-reads it, so a playlist edited in rekordbox shows up without restarting
 * anything.
 *
 * A track already in the show says so and opens; one rekordbox never analysed
 * cannot be ticked, because there is no grid to put a show on.
 */

const SHOWN = 300;          // rows drawn at once; a search narrows the rest
export const ALL = ALL_TRACKS;

// -- the catalogue, shared by the sidebar and the page ---------------------------

interface CatalogueState { cat: Catalogue | null; loading: boolean; error: string | null }

let store: CatalogueState = { cat: null, loading: false, error: null };
const listeners = new Set<() => void>();
function setStore(next: Partial<CatalogueState>) {
  store = { ...store, ...next };
  listeners.forEach((l) => l());
}

export function loadCatalogue(refresh = false): void {
  if (store.loading) return;
  setStore({ loading: true, error: null });
  apiFetch<Catalogue>(`/api/rekordbox${refresh ? "?refresh=1" : ""}`)
    .then((cat) => setStore({ cat, loading: false }))
    .catch((e: Error) => setStore({ loading: false, error: e.message }));
}

/** For tests: forget what was read. */
export function resetCatalogue(): void {
  store = { cat: null, loading: false, error: null };
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export function useCatalogue(): CatalogueState {
  return useSyncExternalStore(subscribe, () => store);
}

/** Which prepped track each rekordbox row already is, in this database. */
function inShowMap(tracks: TrackLine[], db: string): Map<number, TrackLine> {
  const out = new Map<number, TrackLine>();
  for (const t of tracks) {
    for (const r of t.rekordbox ?? []) if (r.db === db) out.set(r.id, t);
  }
  return out;
}

/** The tracks under a playlist or folder, in playlist order. */
function scopedTracks(cat: Catalogue, scope: string): CatalogueTrack[] {
  if (scope === ALL) return cat.tracks;
  const byId = new Map(cat.tracks.map((t) => [t.id, t]));
  const under = new Set([scope]);
  for (const p of cat.playlists) if (p.parent && under.has(p.parent)) under.add(p.id);
  const ids = new Set<number>();
  for (const p of cat.playlists) if (under.has(p.id)) p.tracks.forEach((i) => ids.add(i));
  return [...ids].map((i) => byId.get(i)).filter((t): t is CatalogueTrack => !!t);
}

export function scopeName(cat: Catalogue | null, scope: string): string {
  if (scope === ALL || !cat) return "All tracks";
  return cat.playlists.find((p) => p.id === scope)?.name.trim() || "Playlist";
}

// -- the sidebar ------------------------------------------------------------------

export function RekordboxNav({ scope }: { scope: string | null }) {
  const { cat, loading, error } = useCatalogue();
  const [open, setOpen] = useState<Set<string>>(new Set());
  const children = useMemo(() => {
    const out = new Map<string | null, CataloguePlaylist[]>();
    for (const p of cat?.playlists ?? []) out.set(p.parent, [...(out.get(p.parent) ?? []), p]);
    return out;
  }, [cat]);
  // The folders above the playlist on screen stay open.
  const shownOpen = useMemo(() => {
    const out = new Set(open);
    let at = cat?.playlists.find((p) => p.id === scope)?.parent ?? null;
    while (at) {
      out.add(at);
      at = cat?.playlists.find((p) => p.id === at)?.parent ?? null;
    }
    return out;
  }, [open, scope, cat]);

  const tree = (parent: string | null, depth: number): ReactNode =>
    (children.get(parent) ?? []).map((p) => (
      <li key={p.id}>
        {p.kind === "smart" ? (
          <span className="s-nav-item muted" style={{ paddingLeft: 10 + depth * 14 }}
                aria-label={`smart ${p.name.trim()}`} aria-disabled="true"
                title="A smart playlist is a query rekordbox runs; its tracks are not stored, so they are not listed here">
            {p.name.trim()}<span className="s-n">smart</span></span>
        ) : (
          <a className={`s-nav-item${scope === p.id ? " on" : ""}`}
             style={{ paddingLeft: 10 + depth * 14 }} href={`#studio/rekordbox/${p.id}`}
             aria-label={`${p.kind} ${p.name.trim()}`}
             aria-current={scope === p.id ? "page" : undefined}
             onClick={() => {
               if (p.kind !== "folder") return;
               setOpen((s) => {
                 const next = new Set(s);
                 if (next.has(p.id)) next.delete(p.id); else next.add(p.id);
                 return next;
               });
             }}>
            {p.kind === "folder" && <span className="s-caret" aria-hidden="true">
              {shownOpen.has(p.id) ? "▾" : "▸"}</span>}
            <span className="s-nav-name">{p.name.trim()}</span>
            {p.kind === "playlist" && <span className="s-n">{p.tracks.length}</span>}
          </a>
        )}
        {p.kind === "folder" && shownOpen.has(p.id) && <ul>{tree(p.id, depth + 1)}</ul>}
      </li>
    ));

  return (
    <div className="s-nav-group" aria-label="rekordbox">
      <div className="s-sec">rekordbox
        {cat && <span className="s-sec-note">read {cat.read_at.slice(11, 16)}</span>}
        {cat && <button className="s-icon s-sec-btn" disabled={loading}
                        title="Read rekordbox again, for playlists changed since"
                        aria-label="reload rekordbox" onClick={() => loadCatalogue(true)}>↻</button>}
      </div>
      {!cat ? (
        <div className="s-nav-pad">
          <button onClick={() => loadCatalogue(false)} disabled={loading}>
            {loading ? "Reading rekordbox…" : "Browse rekordbox"}</button>
          {error && <p className="d-error small" role="alert">{error}</p>}
        </div>
      ) : (
        <nav aria-label="playlists">
          <ul className="s-tree">
            <li><a className={`s-nav-item${scope === ALL ? " on" : ""}`} href="#studio/rekordbox"
                   aria-current={scope === ALL ? "page" : undefined}>
              <span className="s-nav-name">All tracks</span>
              <span className="s-n">{cat.tracks.length.toLocaleString()}</span></a></li>
            {tree(null, 0)}
          </ul>
        </nav>
      )}
    </div>
  );
}

// -- the page ---------------------------------------------------------------------

type Filter = "all" | "not" | "in" | "cant";

export function RekordboxView({ scope, find, tracks, ticked, setTicked, onAdd, canWrite }: {
  scope: string; find?: string; tracks: TrackLine[];
  ticked: Set<number>; setTicked: (next: Set<number>) => void;
  onAdd: (picked: CatalogueTrack[]) => void;
  canWrite: boolean;
}) {
  const { cat, loading, error } = useCatalogue();
  const [query, setQuery] = useState(find ?? "");
  const [everywhere, setEverywhere] = useState(!!find);
  const [filter, setFilter] = useState<Filter>("all");

  const inShow = useMemo(() => inShowMap(tracks, cat?.db ?? ""), [tracks, cat]);
  const hay = useMemo(() => new Map((cat?.tracks ?? []).map(
    (t) => [t.id, normalizeName(`${t.title} ${t.artist} ${t.album}`)])), [cat]);
  const scoped = useMemo(() => (cat ? scopedTracks(cat, everywhere ? ALL : scope) : []),
                         [cat, scope, everywhere]);

  if (!cat) {
    return (
      <section className="s-page s-empty" aria-label="rekordbox collection">
        <h1>rekordbox</h1>
        <p className="muted">Pick tracks straight from rekordbox, by playlist or by search.
          Nothing to export first.</p>
        <div><button className="d-primary" onClick={() => loadCatalogue(false)} disabled={loading}>
          {loading ? "Reading rekordbox…" : "Browse rekordbox"}</button></div>
        {error && <p className="d-error" role="alert">{error}</p>}
      </section>
    );
  }

  const words = normalizeName(query).split(" ").filter(Boolean);
  const searched = words.length
    ? scoped.filter((t) => words.every((w) => hay.get(t.id)!.includes(w))) : scoped;
  const counts = {
    all: searched.length,
    not: searched.filter((t) => t.analysed && !inShow.has(t.id)).length,
    in: searched.filter((t) => inShow.has(t.id)).length,
    cant: searched.filter((t) => !t.analysed && !inShow.has(t.id)).length,
  };
  const listed = searched.filter((t) => filter === "all" ? true
    : filter === "in" ? inShow.has(t.id)
    : filter === "not" ? t.analysed && !inShow.has(t.id)
    : !t.analysed && !inShow.has(t.id));
  const shown = listed.slice(0, SHOWN);
  const pickable = shown.filter((t) => t.analysed);
  const allTicked = pickable.length > 0 && pickable.every((t) => ticked.has(t.id));
  const notYet = scoped.filter((t) => t.analysed && !inShow.has(t.id));
  const picked = cat.tracks.filter((t) => ticked.has(t.id));
  const fresh = picked.filter((t) => !inShow.has(t.id)).length;
  const again = picked.length - fresh;
  const name = everywhere ? "the whole collection" : scopeName(cat, scope);

  const toggle = (id: number) => {
    const next = new Set(ticked);
    if (next.has(id)) next.delete(id); else next.add(id);
    setTicked(next);
  };
  const FILTERS: [Filter, string][] = [
    ["all", "All"], ["not", "Not in the show"], ["in", "In the show"], ["cant", "Can't add"]];

  return (
    <section className="s-page" aria-label="rekordbox collection">
      <div className="s-page-head">
        <div>
          <span className="s-kicker">{scope === ALL ? "rekordbox" : "rekordbox playlist"}</span>
          <h1>{scopeName(cat, scope)}</h1>
          <span className="muted small">{scoped.length.toLocaleString()} tracks · rekordbox{" "}
            {cat.rekordbox ?? ""} · read {cat.read_at.slice(11, 16)}</span>
        </div>
        <span className="grow" />
        {notYet.length > 0 && scope !== ALL && (
          <button onClick={() => setTicked(new Set([...ticked, ...notYet.map((t) => t.id)]))}>
            Tick the {notYet.length} not in the show</button>)}
      </div>

      <div className="s-toolbar">
        <span className="d-chips" role="group" aria-label="show">
          {FILTERS.map(([id, label]) => (
            <button key={id} className={`s-chip${filter === id ? " on" : ""}`}
                    aria-pressed={filter === id} onClick={() => setFilter(id)}>
              {label} <span className="s-n">{counts[id]}</span></button>))}
        </span>
        <span className="grow" />
        <input type="search" value={query} onChange={(e) => setQuery(e.target.value)}
               placeholder={`Search ${name}`} aria-label="search rekordbox" />
        {scope !== ALL && (
          <label className="small s-inline">
            <input type="checkbox" checked={everywhere}
                   onChange={(e) => setEverywhere(e.target.checked)} /> whole collection</label>)}
      </div>

      <div className="s-table-wrap">
        <table className="s-table">
          <thead>
            <tr>
              <th className="s-tick"><input type="checkbox" aria-label="tick every track shown"
                         checked={allTicked} disabled={!pickable.length}
                         onChange={() => {
                           const next = new Set(ticked);
                           for (const t of pickable) {
                             if (allTicked) next.delete(t.id); else next.add(t.id);
                           }
                           setTicked(next);
                         }} /></th>
              <th>Track</th><th>BPM</th><th>Key</th><th>Time</th><th>In the show</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((t) => {
              const prepped = inShow.get(t.id);
              return (
                <tr key={t.id} className={`${ticked.has(t.id) ? "ticked" : ""}${t.analysed ? "" : " muted"}`}>
                  <td className="s-tick"><input type="checkbox" aria-label={`tick ${t.title}`}
                             checked={ticked.has(t.id)} disabled={!t.analysed}
                             onChange={() => toggle(t.id)} /></td>
                  <td className="s-track"><b>{t.title}</b><span className="muted">{t.artist}</span></td>
                  <td className="mono">{t.bpm ? t.bpm.toFixed(t.bpm % 1 ? 2 : 0) : ""}</td>
                  <td className="mono">{t.key}</td>
                  <td className="mono">{mmss(t.duration_s)}</td>
                  <td className="s-status">
                    {prepped ? (
                      <>
                        <span className="s-pill"><i className={prepped.has_timeline ? "good" : ""} />
                          {prepped.has_timeline ? "Timeline" : "No timeline"}</span>
                        <a className="d-link small" href={`#studio/track/${prepped.id}`}
                           aria-label={`open ${t.title}`}>Open</a>
                      </>
                    ) : !t.analysed ? (
                      <span className="s-warn small"
                            title="Analyse it in rekordbox first: there is no beat grid">not analysed</span>
                    ) : <span className="muted small">not yet</span>}
                    {!t.local && <span className="s-tag"
                      title="A streaming track: it can be prepped, but there is no file for Studio to play">
                      streaming</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {!shown.length && <p className="muted s-pad">Nothing here{words.length ? " matches" : ""}.</p>}
      </div>
      {listed.length > SHOWN && <p className="muted small">The first {SHOWN} of{" "}
        {listed.length}: search to narrow.</p>}

      <div className={`s-actionbar${picked.length ? " on" : ""}`}>
        <b>{picked.length ? `${picked.length} ticked` : "Tick tracks to add them"}</b>
        <span className="muted small">{again ? `${again} already in the show will be prepped again. ` : ""}
          Each is checked against rekordbox's own analysis.</span>
        <span className="grow" />
        {picked.length > 0 && <button onClick={() => setTicked(new Set())}>Clear</button>}
        <button className="d-primary" disabled={!picked.length || !canWrite}
                title={canWrite ? undefined : "Adding tracks writes the show folder: open Studio "
                  + "from the link the engine printed"}
                onClick={() => onAdd(picked)}>
          {fresh ? `Add ${fresh} to the show` : "Add to the show"}{again ? `, re-prep ${again}` : ""}
        </button>
      </div>
    </section>
  );
}

// -- beside it ----------------------------------------------------------------------

/** How much of a playlist the show covers: the question before a gig. */
export function Coverage({ scope, tracks }: { scope: string; tracks: TrackLine[] }) {
  const { cat } = useCatalogue();
  if (!cat) {
    return <DetailEmpty>Browse rekordbox to see how much of each playlist the show
      covers.</DetailEmpty>;
  }
  const inShow = inShowMap(tracks, cat.db);
  const scoped = scopedTracks(cat, scope);
  const own = scoped.filter((t) => inShow.get(t.id)?.has_timeline).length;
  const tpl = scoped.filter((t) => inShow.has(t.id) && !inShow.get(t.id)?.has_timeline).length;
  const cant = scoped.filter((t) => !t.analysed && !inShow.has(t.id));
  const not = scoped.length - own - tpl - cant.length;
  const parts: [string, number, string][] = [
    ["Own timeline", own, "good"], ["In the show, no timeline", tpl, "tpl"],
    ["Not in the show", not, "none"], ["Can't be added yet", cant.length, "warn"]];
  return (
    <div className="s-detail s-coverage" role="region" aria-label="coverage">
      <DetailHead kind={scope === ALL ? "Collection" : "Playlist"}
                  title={scope === ALL ? "The whole collection" : scopeName(cat, scope)}
                  meta={`${scoped.length} track${scoped.length === 1 ? "" : "s"}`} />
      <DetailSection title="Covered by the show" label="how much is covered">
        <div className="s-bar" aria-hidden="true">
          {parts.map(([label, n, k]) => n > 0 && <i key={label} className={k} style={{ flex: n }} />)}
        </div>
        <dl>
          {parts.map(([label, n, k]) => (
            <div key={label}><dt><i className={k} />{label}</dt><dd className="mono">{n}</dd></div>
          ))}
        </dl>
        <span className="muted small">A track with no timeline plays the template set that is
          on, in the show or not. Adding it lets the set follow its phrases, Studio draft it a
          timeline, and the engine recognise it from rekordbox and CDJs.</span>
      </DetailSection>
      {cant.length > 0 && (
        <DetailSection title="Can't be added yet" count={cant.length}>
          <ul className="s-names small">
            {cant.slice(0, 8).map((t) => <li key={t.id}>{t.title}</li>)}
          </ul>
          <span className="muted small">Not analysed: analyse {cant.length === 1 ? "it" : "them"} in
            rekordbox, then reload.</span>
        </DetailSection>
      )}
    </div>
  );
}
