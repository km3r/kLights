import { useEffect, useState } from "react";
import type { Engine } from "./Designer";
import type { RoutineSummary, ShowSummary, TemplateSummary } from "./model";

/**
 * The show's settings: show.json, which until now only a text editor or the
 * phone's latency slider could change.
 *
 * Only what the engine reads is offered: the show's template set (what new
 * timelines draft from), what happens when the decks pause (policy, idle
 * routine, how long a silence counts as a pause, the fade into idle), and how
 * Follow starts and how quickly it believes a track change. Each applies when
 * the folder reloads, which a save causes. `fallback` is in the format but no
 * part of the engine reads it yet, so it is not offered here. Latency is the
 * phone's, set live from the Track card; it is shown, not edited, so the two
 * cannot fight over it.
 */

type ShowDoc = NonNullable<ShowSummary["show"]>;

interface Pause { policy?: string; grace_s?: number; idle_routine?: string; fade_beats?: number }
interface Follow { default?: string; min_track_change_s?: number }

const POLICY: Record<string, string> = {
  idle: "Hand over to the idle routine, after the silence below.",
  freeze: "Hold everything where it is.",
  continue: "Keep moving at the last tempo, as if the music had not stopped.",
};

export function ShowSettingsView({ engine, show, sets, routines, onDone }: {
  engine: Engine; show: ShowSummary | null; sets: TemplateSummary[];
  routines: RoutineSummary[];
  /** Saved, and what was said about it. */
  onDone: (said: string) => void;
}) {
  const saved = show?.show ?? null;
  const [doc, setDoc] = useState<ShowDoc | null>(saved);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // `base` is the show.json the working copy started from. A newer one (it
  // loading, a save here, the phone's latency, another machine) replaces the
  // working copy only if nothing has been changed here since `base`.
  const savedText = JSON.stringify(saved);
  const [base, setBase] = useState(savedText);
  const dirty = JSON.stringify(doc) !== savedText;
  useEffect(() => {
    if (savedText === base) return;
    if (JSON.stringify(doc) === base) setDoc(saved);
    setBase(savedText);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [savedText]);
  const canWrite = engine.tier === "configure";

  if (!show) return <section className="s-page" aria-label="show settings"><p className="muted">
    Loading the show folder…</p></section>;
  if (!doc) {
    return (
      <section className="s-page" aria-label="show settings">
        <h1>Show settings</h1>
        <p className="muted">This show folder has no show.json yet, so every setting is the
          engine's default.</p>
        <div><button className="d-primary" disabled={!canWrite || busy} onClick={async () => {
          setBusy(true);
          const reply = await engine.request({ type: "show_save", base_rev: "",
                                               doc: { kind: "klights.show", version: 1 } });
          setBusy(false);
          if (reply.ok) onDone("Made show.json."); else setError(reply.error ?? "the engine refused");
        }}>Make show.json</button></div>
        {error && <p className="d-error" role="alert">{error}</p>}
      </section>
    );
  }

  const pause = (doc.pause ?? {}) as Pause;
  const follow = (doc.follow ?? {}) as Follow;
  const setPause = (change: Partial<Pause>) => setDoc((d) => d && clean({ ...d, pause: { ...pause, ...change } }));
  const setFollow = (change: Partial<Follow>) => setDoc((d) => d && clean({ ...d, follow: { ...follow, ...change } }));
  const num = (raw: string, lo: number, hi: number): number | undefined => {
    if (raw === "") return undefined;
    const v = Number(raw);
    return Number.isFinite(v) && v >= lo && v <= hi ? v : undefined;
  };
  const sources = Object.entries((doc.sources ?? {}) as Record<string, { latency_ms?: number }>);
  const save = async () => {
    setBusy(true);
    setError(null);
    const reply = await engine.request({ type: "show_save", doc, base_rev: show.show_rev ?? "" });
    setBusy(false);
    if (reply.ok) onDone("Saved show.json. The engine applies it as it reloads the folder.");
    else setError(reply.error ?? "the engine refused");
  };

  return (
    <section className="s-page" aria-label="show settings">
      <div className="s-page-head">
        <div>
          <h1>Show settings</h1>
          <span className="muted small mono">show.json</span>
        </div>
        <span className="grow" />
        {dirty && <button onClick={() => { setDoc(saved); setError(null); }}>Revert</button>}
        <button className="d-primary" disabled={!dirty || busy || !canWrite} onClick={() => void save()}>
          {busy ? "Saving…" : dirty ? "Save" : "Saved"}</button>
      </div>
      {error && (
        <p className="d-error" role="alert">{error}
          {/changed since/.test(error) && <> <button onClick={() => { setDoc(saved); setError(null); }}>
            Take the newer one</button></>}</p>)}

      <section className="s-box" aria-label="template set">
        <header><b>Template set</b></header>
        <label className="s-field">The show's set
          <select value={typeof doc.template_set === "string" ? doc.template_set : ""}
                  aria-label="the show's template set"
                  onChange={(e) => setDoc((d) => d && clean({ ...d, template_set: e.target.value || undefined }))}>
            <option value="">none</option>
            {sets.map((s) => <option key={s.id} value={s.id}>{s.name || s.id}</option>)}
          </select>
        </label>
        <p className="muted small">New timelines draft from it by default. Template sets do not
          play tracks live yet (F19 milestone 2): a track with no timeline gets the operator's
          show.</p>
      </section>

      <section className="s-box" aria-label="when the decks pause">
        <header><b>When the decks pause</b></header>
        <label className="s-field">Then
          <select value={pause.policy ?? "idle"} aria-label="pause policy"
                  onChange={(e) => setPause({ policy: e.target.value })}>
            <option value="idle">Idle routine</option>
            <option value="freeze">Freeze</option>
            <option value="continue">Keep moving</option>
          </select>
          <span className="muted small">{POLICY[pause.policy ?? "idle"]}</span>
        </label>
        {(pause.policy ?? "idle") === "idle" && (
          <label className="s-field">Idle routine
            <select value={pause.idle_routine ?? ""} aria-label="idle routine"
                    onChange={(e) => setPause({ idle_routine: e.target.value || undefined })}>
              <option value="">none: nothing changes</option>
              {routines.map((r) => <option key={r.id} value={r.id}>{r.name || r.id}</option>)}
            </select>
          </label>
        )}
        <label className="s-field">Silence before it counts as a pause
          <span><input type="number" min={0} max={60} step={0.5} style={{ width: 70 }}
                       value={pause.grace_s ?? ""} placeholder="default" aria-label="grace seconds"
                       onChange={(e) => setPause({ grace_s: num(e.target.value, 0, 60) })} /> s</span>
        </label>
        <label className="s-field">Fade into it
          <span><input type="number" min={0} max={64} style={{ width: 70 }}
                       value={pause.fade_beats ?? ""} placeholder="0" aria-label="fade beats"
                       onChange={(e) => setPause({ fade_beats: num(e.target.value, 0, 64) })} /> beats</span>
        </label>
      </section>

      <section className="s-box" aria-label="follow">
        <header><b>Follow DJ</b></header>
        <label className="s-field">When the engine starts
          <select value={follow.default ?? "disarmed"} aria-label="follow default"
                  onChange={(e) => setFollow({ default: e.target.value })}>
            <option value="disarmed">Safe: the DJ feed drives nothing until armed</option>
            <option value="armed">Armed</option>
          </select>
          <span className="muted small">Anyone on the network can send to the DJ feed, which is
            why it starts safe unless you choose otherwise.</span>
        </label>
        <label className="s-field">A track change counts after
          <span><input type="number" min={0} max={60} step={0.5} style={{ width: 70 }}
                       value={follow.min_track_change_s ?? ""} placeholder="default"
                       aria-label="track change seconds"
                       onChange={(e) => setFollow({ min_track_change_s: num(e.target.value, 0, 60) })} /> s</span>
        </label>
      </section>

      {sources.length > 0 && (
        <section className="s-box" aria-label="latency">
          <header><b>Latency</b><span className="muted small">Set live from the phone's Track
            card, where you can hear it.</span></header>
          <dl className="s-latency">
            {sources.map(([name, src]) => (
              <div key={name}><dt className="mono">{name}</dt>
                <dd className="mono">{(src.latency_ms ?? 0) > 0 ? "+" : ""}{src.latency_ms ?? 0} ms</dd></div>
            ))}
          </dl>
        </section>
      )}
    </section>
  );
}

/** Drop what was cleared, so a save writes only what was set. */
function clean(doc: ShowDoc): ShowDoc {
  const out: Record<string, unknown> = { ...doc };
  for (const key of ["pause", "follow"] as const) {
    const part = { ...(out[key] as Record<string, unknown> | undefined) };
    for (const k of Object.keys(part)) if (part[k] === undefined) delete part[k];
    if (Object.keys(part).length) out[key] = part; else delete out[key];
  }
  if (out.template_set === undefined) delete out.template_set;
  return out as ShowDoc;
}
