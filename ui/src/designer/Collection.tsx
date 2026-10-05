import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import { apiFetch } from "../useEngine";
import type { Engine } from "./Designer";
import { normalizeName } from "./model";
import type {
  Catalogue, CataloguePlaylist, CatalogueTrack, PrepSummary, TrackLine,
} from "./model";

/**
 * The DJ's rekordbox collection, to pick tracks from: playlists on the left,
 * a playlist's tracks (or a search) on the right, and "Add to the show" to
 * prep the ones ticked.
 *
 * The engine does not read rekordbox; the prep bridge does, in a child
 * process (engine/collection.py), and this page gets what it read. Loaded only
 * when asked -- a big collection is a megabyte -- and re-read on Reload, so a
 * playlist edited in rekordbox shows up without restarting anything.
 *
 * A track already in the show says so and opens; one rekordbox never analysed
 * cannot be ticked, because there is no grid to put a show on.
 */

const SHOWN = 300;          // rows drawn at once; a search narrows the rest
const ALL = "all";

export function CollectionBrowser({ engine, tracks, onPrepped }: {
  engine: Engine; tracks: TrackLine[]; onPrepped: () => void;
}) {
  const [cat, setCat] = useState<Catalogue | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = (refresh: boolean) => {
    setLoading(true);
    setError(null);
    apiFetch<Catalogue>(`/api/rekordbox${refresh ? "?refresh=1" : ""}`)
      .then(setCat).catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  };

  if (!cat) {
    return (
      <section className="d-rb-closed">
        <p className="muted small">Pick tracks straight from rekordbox: by playlist or by
          search. Nothing to export first.</p>
        <button onClick={() => load(false)} disabled={loading}>
          {loading ? "Reading rekordbox…" : "Browse rekordbox"}</button>
        {error && <p className="d-error" role="alert">{error}</p>}
      </section>
    );
  }
  return <Browser cat={cat} engine={engine} tracks={tracks} onPrepped={onPrepped}
                  reload={() => load(true)} loading={loading} error={error} />;
}

function Browser({ cat, engine, tracks, onPrepped, reload, loading, error }: {
  cat: Catalogue; engine: Engine; tracks: TrackLine[]; onPrepped: () => void;
  reload: () => void; loading: boolean; error: string | null;
}) {
  const [scope, setScope] = useState<string>(ALL);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const [ticked, setTicked] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [summary, setSummary] = useState<PrepSummary | null>(null);
  const [prepError, setPrepError] = useState<string | null>(null);

  const byId = useMemo(() => new Map(cat.tracks.map((t) => [t.id, t])), [cat]);
  const children = useMemo(() => {
    const out = new Map<string | null, CataloguePlaylist[]>();
    for (const p of cat.playlists) {
      out.set(p.parent, [...(out.get(p.parent) ?? []), p]);
    }
    return out;
  }, [cat]);
  const hay = useMemo(() => new Map(cat.tracks.map(
    (t) => [t.id, normalizeName(`${t.title} ${t.artist} ${t.album}`)])), [cat]);
  // Which prepped track each rekordbox row already is, in this database.
  const inShow = useMemo(() => {
    const out = new Map<number, string>();
    for (const t of tracks) {
      for (const r of t.rekordbox ?? []) if (r.db === cat.db) out.set(r.id, t.id);
    }
    return out;
  }, [tracks, cat.db]);

  const scoped: CatalogueTrack[] = useMemo(() => {
    if (scope === ALL) return cat.tracks;
    const under = new Set([scope]);
    for (const p of cat.playlists) if (p.parent && under.has(p.parent)) under.add(p.id);
    const ids = new Set<number>();
    for (const p of cat.playlists) if (under.has(p.id)) p.tracks.forEach((i) => ids.add(i));
    return [...ids].map((i) => byId.get(i)).filter((t): t is CatalogueTrack => !!t);
  }, [scope, cat, byId]);
  const words = normalizeName(query).split(" ").filter(Boolean);
  const listed = words.length
    ? scoped.filter((t) => words.every((w) => hay.get(t.id)!.includes(w)))
    : scoped;
  const shown = listed.slice(0, SHOWN);
  const pickable = shown.filter((t) => t.analysed);
  const scopeName = scope === ALL ? "the whole collection"
    : cat.playlists.find((p) => p.id === scope)?.name.trim() ?? "";

  const toggle = (id: number) => setTicked((s) => {
    const next = new Set(s);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const allTicked = pickable.length > 0 && pickable.every((t) => ticked.has(t.id));

  const add = async () => {
    setBusy(true);
    setPrepError(null);
    setSummary(null);
    // A playlist's worth of analysis files takes seconds, not milliseconds.
    const r = await engine.request({ type: "rekordbox_prep", ids: [...ticked] }, 600_000);
    setBusy(false);
    if (!r.ok) {
      setPrepError(r.error ?? "the engine refused");
      return;
    }
    setSummary(r.data as PrepSummary);
    setTicked(new Set());
    onPrepped();
  };

  const tree = (parent: string | null, depth: number): ReactNode =>
    (children.get(parent) ?? []).map((p) => (
      <li key={p.id}>
        <button className={`d-rb-node${scope === p.id ? " on" : ""}`}
                style={{ paddingLeft: 6 + depth * 14 }}
                aria-label={`${p.kind} ${p.name.trim()}`}
                title={p.kind === "smart" ? "A smart playlist is a query rekordbox runs; "
                  + "its tracks are not stored, so they are not listed here" : undefined}
                disabled={p.kind === "smart"}
                onClick={() => {
                  setScope(p.id);
                  if (p.kind === "folder") {
                    setOpen((s) => {
                      const next = new Set(s);
                      if (next.has(p.id)) next.delete(p.id); else next.add(p.id);
                      return next;
                    });
                  }
                }}>
          {p.kind === "folder" ? (open.has(p.id) ? "▾ " : "▸ ") : ""}{p.name.trim()}
          {p.kind === "playlist" && <span className="muted small"> {p.tracks.length}</span>}
        </button>
        {p.kind === "folder" && open.has(p.id) && <ul>{tree(p.id, depth + 1)}</ul>}
      </li>
    ));

  return (
    <section className="d-rb" aria-label="rekordbox collection">
      <div className="d-form">
        <span className="small muted">
          rekordbox {cat.rekordbox ?? ""} · {cat.tracks.length} tracks · read{" "}
          {cat.read_at.slice(11, 16)}
        </span>
        <button onClick={reload} disabled={loading}
                title="Read rekordbox again, for playlists changed since">
          {loading ? "Reading…" : "Reload"}</button>
        {error && <span className="d-error small" role="alert">{error}</span>}
      </div>
      <div className="d-rb-body">
        <nav className="d-rb-tree" aria-label="playlists">
          <ul>
            <li>
              <button className={`d-rb-node${scope === ALL ? " on" : ""}`}
                      onClick={() => setScope(ALL)}>All tracks
                <span className="muted small"> {cat.tracks.length}</span></button>
            </li>
            {tree(null, 0)}
          </ul>
        </nav>
        <div className="d-rb-list">
          <div className="d-form">
            <input type="search" value={query} onChange={(e) => setQuery(e.target.value)}
                   placeholder={`Search ${scopeName}`} aria-label="search rekordbox" />
            <span className="small muted">
              {listed.length > SHOWN ? `the first ${SHOWN} of ${listed.length}; search to narrow`
                : `${listed.length} track${listed.length === 1 ? "" : "s"}`}
            </span>
            <span className="grow" />
            <button className="d-primary" disabled={!ticked.size || busy
                      || engine.tier !== "configure"}
                    title={engine.tier !== "configure"
                      ? "Adding tracks writes the show folder: open the designer from "
                        + "the URL the engine printed" : undefined}
                    onClick={() => void add()}>
              {busy ? "Prepping…" : `Add ${ticked.size || ""} to the show`}</button>
          </div>
          {prepError && <p className="d-error" role="alert">{prepError}</p>}
          {summary && <Summary summary={summary} onClose={() => setSummary(null)} />}
          <table className="d-rb-table">
            <thead>
              <tr>
                <th><input type="checkbox" aria-label="tick every track shown"
                           checked={allTicked} disabled={!pickable.length}
                           onChange={() => setTicked((s) => {
                             const next = new Set(s);
                             for (const t of pickable) {
                               if (allTicked) next.delete(t.id); else next.add(t.id);
                             }
                             return next;
                           })} /></th>
                <th>Title</th><th>Artist</th><th>BPM</th><th>Key</th><th>Time</th><th />
              </tr>
            </thead>
            <tbody>
              {shown.map((t) => {
                const prepped = inShow.get(t.id);
                return (
                  <tr key={t.id} className={t.analysed ? "" : "muted"}>
                    <td><input type="checkbox" aria-label={`tick ${t.title}`}
                               checked={ticked.has(t.id)} disabled={!t.analysed}
                               onChange={() => toggle(t.id)} /></td>
                    <td>{t.title}</td>
                    <td className="muted">{t.artist}</td>
                    <td className="mono">{t.bpm ? t.bpm.toFixed(t.bpm % 1 ? 2 : 0) : ""}</td>
                    <td className="mono">{t.key}</td>
                    <td className="mono">{t.duration_s
                      ? `${Math.floor(t.duration_s / 60)}:${String(t.duration_s % 60).padStart(2, "0")}`
                      : ""}</td>
                    <td className="small">
                      {prepped ? <a className="d-link" href={`#designer/${prepped}`}>in the show</a>
                        : !t.analysed ? <span title="Analyse it in rekordbox first: there is no beat grid">not analysed</span>
                          : !t.local ? <span className="muted" title="A streaming track: it can be prepped, but there is no file for the designer to play">streaming</span>
                            : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}

function Summary({ summary, onClose }: { summary: PrepSummary; onClose: () => void }) {
  const count = (s: string) => summary.results.filter((r) => r.status === s).length;
  const notes = summary.results.filter((r) => r.notes.length);
  return (
    <div className="d-rb-summary" role="status">
      <div className="d-form">
        <b>Prepped {summary.results.length}:</b>
        <span>{count("created")} new, {count("updated")} updated,{" "}
          {count("unchanged")} unchanged</span>
        {summary.skipped.length > 0 && <span className="d-error">
          {summary.skipped.length} skipped</span>}
        <span className="grow" />
        <button onClick={onClose} aria-label="close the summary">×</button>
      </div>
      <ul className="small">
        {summary.results.filter((r) => r.status === "created").map((r) => (
          <li key={r.track_id}><a className="d-link" href={`#designer/${r.track_id}`}>
            {r.artist ? `${r.artist} – ` : ""}{r.title}</a>
            {!r.signature && <span className="muted"> · no CDJ signature</span>}</li>
        ))}
        {notes.map((r) => r.notes.map((n) => (
          <li key={`${r.track_id}:${n}`} className="muted">{r.title}: {n}</li>
        )))}
        {summary.skipped.map((s) => (
          <li key={s.rekordbox_id} className="d-error">{s.title}: {s.reason}</li>
        ))}
      </ul>
    </div>
  );
}
