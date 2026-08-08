import { useEffect, useState } from "react";
import { useEngine, useWakeLock } from "./useEngine";
import { Banner, BeatDots, Fader } from "./components";
import { ShowTab } from "./tabs/Show";
import { ColorTab } from "./tabs/Color";
import { MoveTab } from "./tabs/Move";
import { BrightTab } from "./tabs/Bright";
import { SetupTab } from "./tabs/Setup";
import type { Command, EngineState } from "./types";

// One tab per thing you can independently change, plus Show for what applies
// across all of them and Setup for the room and the rig. Venue and Rig used to
// be their own tabs; they are both setup, and splitting them meant three places
// to look for one job.
const TABS = [
  { id: "show", label: "Show", glyph: "★" },
  { id: "color", label: "Color", glyph: "🎨" },
  { id: "move", label: "Move", glyph: "↔" },
  { id: "bright", label: "Bright", glyph: "☀" },
  { id: "setup", label: "Setup", glyph: "⚙" },
] as const;

type TabId = (typeof TABS)[number]["id"];

export default function App() {
  const { state, status, send, name, setName } = useEngine();
  const [tab, setTab] = useState<TabId>("show");
  // Local only while a finger is down; see Fader's comment.
  const [masterDrag, setMasterDrag] = useState<number | null>(null);

  useWakeLock(true);

  // Keep the tab in the URL hash so a reload, or a phone waking up, comes back
  // where it was rather than to the front page mid-set.
  useEffect(() => {
    const fromHash = location.hash.slice(1) as TabId;
    if (TABS.some((t) => t.id === fromHash)) setTab(fromHash);
  }, []);
  useEffect(() => { location.hash = tab; }, [tab]);

  const master = masterDrag ?? state?.master ?? 0;

  return (
    <div className="app">
      <header className="header">
        <div className="header-row">
          <span className={`status-dot ${status}`} title={status} />
          <span className="grow small muted">
            {state ? state.event : "connecting"}
            {state && <> · <span className="mono">{state.clock.effective_bpm.toFixed(1)}</span> bpm</>}
          </span>
          {state && <BeatDots beatInBar={state.clock.beat_in_bar} />}
        </div>

        <div className="header-row">
          <Fader value={master}
                 onInput={(v) => { setMasterDrag(v); send({ type: "master", value: v }); }}
                 onCommit={() => setMasterDrag(null)}
                 label="Master" />
          {/* Two different things, so they no longer look like one thing twice.
              BLACKOUT takes the master to zero: the show carries on underneath,
              moves keep moving, and letting go picks up exactly where it got to.
              PANIC bypasses the show entirely and forces zeros onto the wire,
              and keeps sending them — it does not need the show to be healthy or
              the evaluation to be working, which is the whole point of it. */}
          <button className={state?.blackout ? "on" : ""}
                  title="Master to zero. The show keeps running underneath."
                  onClick={() => send({ type: "blackout", on: !state?.blackout })}>
            {state?.blackout ? "Blackout ON" : "Blackout"}
          </button>
          {/* Panic lives on Setup, not here. Blackout covers everything you
              reach for mid-set; panic is for when the show itself has gone
              wrong, which is rare enough that having it under a thumb next to
              the master was more risk than help. The banner below still offers
              a one-tap release whenever it IS engaged. */}
        </div>

        <Banners state={state} status={status} send={send} />
      </header>

      <main className="main">
        {!state ? (
          <p className="muted">Waiting for the engine…</p>
        ) : tab === "show" ? (
          <ShowTab state={state} send={send} />
        ) : tab === "color" ? (
          <ColorTab state={state} send={send} />
        ) : tab === "move" ? (
          <MoveTab state={state} send={send} />
        ) : tab === "bright" ? (
          <BrightTab state={state} send={send} />
        ) : (
          <SetupTab state={state} send={send} name={name} setName={setName} />
        )}
      </main>

      <nav className="tabbar">
        {TABS.map((t) => (
          <button key={t.id} className={tab === t.id ? "on" : ""}
                  onClick={() => setTab(t.id)}>
            <span className="glyph">{t.glyph}</span>
            <span>{t.label}</span>
          </button>
        ))}
      </nav>
    </div>
  );
}

/**
 * The always-visible warnings.
 *
 * Carried over from the old console, which learned the hard way that a rig
 * doing nothing needs an on-screen reason. Each of these answers a specific
 * "why are the lights not doing what I asked" — and jog is here because it
 * silently disables the safety taper, which is the one state nobody should be
 * in without knowing.
 */
function Banners({ state, status, send }: {
  state: EngineState | null; status: string; send: (c: Command) => void;
}) {
  const banners = [];

  if (status !== "open") {
    banners.push(
      <Banner key="conn" kind="bad">
        {status === "connecting" ? "Connecting to the engine…"
          : "Disconnected — the rig is holding its last frame. Retrying."}
      </Banner>);
  }
  if (state?.panicked) {
    banners.push(<Banner key="panic" kind="bad">
      PANIC — zeros are being forced onto the wire and the show is not being
      evaluated at all.
      <button className="small" style={{ marginLeft: "auto" }}
              onClick={() => send({ type: "clear_panic" })}>Release</button>
    </Banner>);
  }
  const jogging = state?.fixtures.filter((f) => f.jogging) ?? [];
  if (jogging.length) {
    banners.push(<Banner key="jog" kind="bad">
      JOG on {jogging.map((f) => f.name).join(", ")} — the safety taper is
      bypassed. Empty room only.
    </Banner>);
  }
  if (state?.blackout) {
    banners.push(<Banner key="bo" kind="warn">
      Blackout — master is at zero. The show is still running underneath, so
      releasing picks up where it has got to.
    </Banner>);
  }
  if (state && !state.blackout && state.master < 0.02) {
    banners.push(<Banner key="master" kind="warn">
      Master is at zero, so nothing will be visible however the look is set.
    </Banner>);
  }
  if (state && state.stats.drops > 0) {
    banners.push(<Banner key="drops" kind="warn">
      {state.stats.drops} dropped frame(s) — the machine is struggling.
    </Banner>);
  }
  if (state?.last_error) {
    banners.push(<Banner key="err" kind="warn">
      A show error occurred; the last good frame is being held.
    </Banner>);
  }

  if (!banners.length) return null;
  return <div className="banners">{banners}</div>;
}
