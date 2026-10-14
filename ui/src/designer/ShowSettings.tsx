import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import type { Engine } from "./Designer";
import type { RoutineSummary, ShowSummary, TemplateSummary } from "./model";
import type { OutputsState } from "../types";

/**
 * The show's settings: show.json, which until now only a text editor or the
 * phone's latency slider could change.
 *
 * Only what the engine reads is offered: the show's template set (what new
 * timelines draft from), what happens when the decks pause (policy, idle
 * routine, how long a silence counts as a pause, the fade into idle), how
 * Follow starts and how quickly it believes a track change, and where the
 * other outputs go (OSC, the MIDI sidecar, Art-Net timecode). Each applies
 * when the folder reloads, which a save causes. `fallback` is in the format
 * but no part of the engine reads it yet, so it is not offered here. Latency
 * is the phone's, set live from the Track card; it is shown, not edited, so
 * the two cannot fight over it.
 *
 * The outputs are the one setting a machine can overrule: its own
 * klights.local.json wins over show.json there, key by key, because the VJ
 * laptop's address is the venue's business. The page says so, and says when
 * the engine it is talking to is such a machine.
 */

type ShowDoc = NonNullable<ShowSummary["show"]>;

interface Pause { policy?: string; grace_s?: number; idle_routine?: string; fade_beats?: number }
interface Follow { default?: string; min_track_change_s?: number }
interface Place { host?: string; port?: number; fps?: number }
type OutputName = "osc" | "midi" | "timecode";
type Outputs = Partial<Record<OutputName, Place>>;

const POLICY: Record<string, string> = {
  idle: "Hand over to the idle routine, after the silence below.",
  freeze: "Hold everything where it is.",
  continue: "Keep moving at the last tempo, as if the music had not stopped.",
};

/** The other outputs, as engine/outputs.py places them: what each is, where
 *  it goes when show.json does not say, and what turning it on starts with.
 *  OSC has no default port -- it is whatever the VJ app listens on. The hosts,
 *  ports and frame rates are the engine's (`outputs.defaults()`), and a test
 *  holds them to it through __fixtures__/outputs.json. */
export const OUTPUTS: { name: OutputName; label: string; what: string; host: string;
                        port: number | null; start: Place }[] = [
  { name: "osc", label: "OSC", host: "127.0.0.1", port: null, start: { port: 7000 },
    what: "To a VJ app, or anything that listens: the cues and curves of a timeline's OSC "
      + "lanes. Resolume listens on 7000." },
  { name: "midi", label: "MIDI", host: "127.0.0.1", port: 9123, start: {},
    what: "To the MIDI sidecar (bridges/midi), which owns the MIDI port: notes, CCs and "
      + "program changes from the MIDI lanes." },
  { name: "timecode", label: "Timecode", host: "255.255.255.255", port: 6454, start: {},
    what: "Art-Net timecode: the matched track's position, for a VJ app with its own "
      + "timeline per track." },
];
export const TIMECODE_FPS = [24, 25, 29.97, 30];
export const TIMECODE_FPS_DEFAULT = 30;
/** "localhost", or four numbers 0-255 with no leading zeros: what the engine
 *  takes (showfiles.host_problem). */
const OCTET = "(25[0-5]|2[0-4]\\d|1\\d\\d|[1-9]?\\d)";
const HOST_RE = new RegExp(`^(localhost|${OCTET}(\\.${OCTET}){3})$`);

/** Why the outputs cannot be saved as they are, in words; none when they can.
 *  The engine checks the same on a save; this is so Save says why first. */
export function outputProblems(outputs: Outputs): string[] {
  const out: string[] = [];
  for (const { name, label } of OUTPUTS) {
    const place = outputs[name];
    if (!place) continue;
    if (place.host !== undefined && !HOST_RE.test(place.host)) {
      out.push(`${label}: give the machine's address (192.168.1.20), not its name.`);
    }
    if (name === "osc" && place.port === undefined) {
      out.push("OSC needs a port: the one the VJ app listens on.");
    }
  }
  return out;
}

/** Where this engine sends each output now, in a line -- with this machine's
 *  klights.local.json applied, which is the point of saying it. */
function sendingNow(now: OutputsState | null | undefined): string {
  const parts = [
    now?.osc ? `OSC to ${now.osc.target}` : "",
    now?.midi ? `MIDI to the sidecar at ${now.midi.target}` : "",
    now?.timecode ? `timecode to ${now.timecode.target} at ${now.timecode.fps} fps` : "",
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : "none of them";
}

export function ShowSettingsView({ engine, show, sets, routines, onDone, problems }: {
  engine: Engine; show: ShowSummary | null; sets: TemplateSummary[];
  routines: RoutineSummary[];
  /** Saved, and what was said about it. */
  onDone: (said: string) => void;
  /** What is wrong in the show folder (Problems.tsx). */
  problems?: ReactNode;
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
        {problems}
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
  /** A number field's value: undefined when emptied (the engine's default),
   *  null when it is out of range -- ignored, rather than read as emptied. */
  const num = (raw: string, lo: number, hi: number): number | undefined | null => {
    if (raw === "") return undefined;
    const v = Number(raw);
    return Number.isFinite(v) && v >= lo && v <= hi ? v : null;
  };
  const sources = Object.entries((doc.sources ?? {}) as Record<string, { latency_ms?: number }>);
  const outputs = (doc.outputs ?? {}) as Outputs;
  const setOutput = (name: OutputName, place: Place | undefined) =>
    setDoc((d) => d && clean({ ...d, outputs: { ...((d.outputs ?? {}) as Outputs), [name]: place } }));
  /** A port field's value, as `num` above but a whole number. */
  const port = (raw: string): number | undefined | null => {
    const v = num(raw, 1, 65535);
    return typeof v === "number" && !Number.isInteger(v) ? null : v;
  };
  const unsendable = outputProblems(outputs);
  const live = engine.state?.outputs;
  const local = (live?.local ?? []).map((n) => OUTPUTS.find((o) => o.name === n)?.label ?? n);
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
        <button className="d-primary" disabled={!dirty || busy || !canWrite || unsendable.length > 0}
                title={unsendable[0]} onClick={() => void save()}>
          {busy ? "Saving…" : dirty ? "Save" : "Saved"}</button>
      </div>
      {error && (
        <p className="d-error" role="alert">{error}
          {/changed since/.test(error) && <> <button onClick={() => { setDoc(saved); setError(null); }}>
            Take the newer one</button></>}</p>)}
      {problems}

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
        <p className="muted small">The set the engine starts on: with Follow armed it lights a
          track that has no timeline, a routine per phrase, and the operator can switch it
          from the Show tab. With none, such a track gets the operator's show. New timelines
          draft from it by default.</p>
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
                       onChange={(e) => {
                         const v = num(e.target.value, 0, 60);
                         if (v !== null) setPause({ grace_s: v });
                       }} /> s</span>
        </label>
        <label className="s-field">Fade into it
          <span><input type="number" min={0} max={64} style={{ width: 70 }}
                       value={pause.fade_beats ?? ""} placeholder="0" aria-label="fade beats"
                       onChange={(e) => {
                         const v = num(e.target.value, 0, 64);
                         if (v !== null) setPause({ fade_beats: v });
                       }} /> beats</span>
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
                       onChange={(e) => {
                         const v = num(e.target.value, 0, 60);
                         if (v !== null) setFollow({ min_track_change_s: v });
                       }} /> s</span>
        </label>
      </section>

      <section className="s-box" aria-label="other outputs">
        <header><b>Other outputs</b><span className="muted small">Where the show sends what is
          not light.</span></header>
        {OUTPUTS.map((o) => {
          const place = outputs[o.name];
          return (
            <div key={o.name} className="s-output" role="group" aria-label={o.label}>
              <label className="s-inline"><input type="checkbox" checked={!!place}
                       aria-label={`send ${o.label}`}
                       onChange={(e) => setOutput(o.name, e.target.checked ? { ...o.start } : undefined)} />
                {" "}<b>{o.label}</b></label>
              {place && (
                <span className="s-output-place">to{" "}
                  <input value={place.host ?? ""} placeholder={o.host} aria-label={`${o.label} host`}
                         size={15} spellCheck={false}
                         onChange={(e) => setOutput(o.name, { ...place, host: e.target.value.trim() || undefined })} />
                  {" : "}
                  <input type="number" min={1} max={65535} style={{ width: 80 }}
                         value={place.port ?? ""} placeholder={o.port == null ? "port" : String(o.port)}
                         aria-label={`${o.label} port`}
                         onChange={(e) => {
                           const v = port(e.target.value);
                           if (v !== null) setOutput(o.name, { ...place, port: v });
                         }} />
                  {o.name === "timecode" && <>{" at "}
                    <select value={place.fps ?? TIMECODE_FPS_DEFAULT} aria-label="timecode fps"
                            onChange={(e) => setOutput(o.name, { ...place, fps: Number(e.target.value) })}>
                      {TIMECODE_FPS.map((f) => <option key={f} value={f}>{f}</option>)}
                    </select>{" fps"}</>}
                </span>
              )}
              <span className="muted small">{o.what}</span>
            </div>
          );
        })}
        {unsendable.map((p) => <p key={p} className="small s-warn">{p}</p>)}
        <p className="s-note" role="note" aria-label="what overrides these">
          <b>A machine's klights.local.json overrides these.</b>
          These are the show's, saved in show.json, so they travel with the show folder. An
          engine whose own klights.local.json has "outputs" uses that instead, output by output
          and key by key: the venue's addresses win over the show's, and nothing saved here
          changes them on that machine.
        </p>
        {local.length > 0 && (
          <p className="s-note warn" role="status" aria-label="overridden on this engine">
            <b>This engine's klights.local.json sets {local.join(" and ")}.</b>
            What it says is what is sent from this machine, whatever is saved here.
          </p>
        )}
        <p className="muted small" aria-label="sending now">This engine is sending:{" "}
          <span className="mono">{sendingNow(live)}</span>.
          {(live?.problems ?? []).map((p) => <span key={p} className="s-warn"> {p}.</span>)}</p>
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
  // An output turned off is left out; one left on with nothing set is `{}`,
  // which is that output at its defaults.
  const places: Record<string, unknown> = {};
  for (const [name, place] of Object.entries((out.outputs ?? {}) as Record<string, unknown>)) {
    if (place === undefined) continue;
    const part = { ...(place as Record<string, unknown>) };
    for (const k of Object.keys(part)) if (part[k] === undefined) delete part[k];
    places[name] = part;
  }
  if (Object.keys(places).length) out.outputs = places; else delete out.outputs;
  return out as ShowDoc;
}
