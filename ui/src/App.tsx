import { useEffect, useState } from "react";
import { useEngine, useWakeLock } from "./useEngine";
import { ModeProvider, useMode } from "./mode";
import { Banner, BeatDots, Fader } from "./components";
import { ShowTab } from "./tabs/Show";
import { ColorTab } from "./tabs/Color";
import { MoveTab } from "./tabs/Move";
import { BrightTab } from "./tabs/Bright";
import { SetupTab } from "./tabs/Setup";
import type { Command, EngineState, Tier } from "./types";

// One tab per thing you can independently change, plus Show for what applies
// across all of them and Setup for the room and the rig. Venue and Rig used to
// be their own tabs; they are both setup, and splitting them meant three places
// to look for one job.
// The glyphs are all text-presentation dingbats, drawn in the tab colour like
// any other text. Colour used to be 🎨, which has emoji presentation forced by
// Unicode -- so it alone rendered as a full-colour bitmap that ignored the
// active/inactive tint and sat at a different weight from its neighbours. The
// filled circle is what the old console used for the same tab.
// `setup` is Design-only. It is the one tab with nothing on it that makes
// light — and the one tab that can leave the rig unable to run.
const TABS = [
  { id: "show", label: "Show", glyph: "★" },
  { id: "color", label: "Color", glyph: "●" },
  { id: "move", label: "Move", glyph: "↔" },
  { id: "bright", label: "Bright", glyph: "☀" },
  { id: "setup", label: "Setup", glyph: "⚙", design: true },
] as const;

type TabId = (typeof TABS)[number]["id"];

export default function App() {
  const { state, status, send, name, setName, tier } = useEngine();
  const [mode, setMode] = useMode();
  const [tab, setTab] = useState<TabId>("show");
  // Local only while a finger is down; see Fader's comment.
  const [masterDrag, setMasterDrag] = useState<number | null>(null);

  useWakeLock(true);

  const tabs = TABS.filter((t) => mode === "design" || !("design" in t));

  // Keep the tab in the URL hash so a reload, or a phone waking up, comes back
  // where it was rather than to the front page mid-set.
  useEffect(() => {
    const fromHash = location.hash.slice(1) as TabId;
    // Checked against the tabs THIS mode has, not against all of them: a phone
    // that was last on Setup and reopens in Perform would otherwise restore a
    // tab with no button in the bar and no way back to it.
    if (tabs.some((t) => t.id === fromHash)) setTab(fromHash);
  }, []);
  useEffect(() => { location.hash = tab; }, [tab]);
  // Dropping to Perform while standing on Setup would otherwise leave the tab
  // rendered with no way back to it in the bar.
  useEffect(() => {
    if (!tabs.some((t) => t.id === tab)) setTab("show");
  }, [mode]);

  const master = masterDrag ?? state?.master ?? 0;

  return (
    <ModeProvider value={mode}>
    <div className="app">
      <header className="header">
        <div className="header-row">
          <span className={`status-dot ${status}`} title={status} />
          <span className="grow small muted">
            {state ? state.event : "connecting"}
            {state && <> · <span className="mono">{state.clock.effective_bpm.toFixed(1)}</span> bpm</>}
          </span>
          {state && <BeatDots beatInBar={state.clock.beat_in_bar} />}
          {/* Always one tap from the other mode, and never a lock: someone who
              needs the patch editor mid-set needs it now, not after finding a
              setting. */}
          <button className="small"
                  title={mode === "perform"
                    ? "Showing only what drives the show. Tap for the full console."
                    : "The full console. Tap to hide setup and diagnostics."}
                  onClick={() => setMode(mode === "perform" ? "design" : "perform")}>
            {mode === "perform" ? "Perform" : "Design"}
          </button>
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

        <Banners state={state} status={status} send={send} tier={tier} />
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
        {tabs.map((t) => (
          <button key={t.id} className={tab === t.id ? "on" : ""}
                  onClick={() => setTab(t.id)}>
            <span className="glyph">{t.glyph}</span>
            <span>{t.label}</span>
          </button>
        ))}
      </nav>
    </div>
    </ModeProvider>
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
function Banners({ state, status, send, tier }: {
  state: EngineState | null; status: string; send: (c: Command) => void;
  tier: Tier;
}) {
  const banners = [];

  // Say it up front. Without this, a client that arrived without the token gets
  // a console that looks completely normal and quietly ignores every press —
  // which is the exact failure the tiers exist to prevent, relocated.
  if (tier === "view") {
    banners.push(
      <Banner key="tier" kind="warn">
        VIEW ONLY — you can watch, but nothing you press will reach the rig.
        Open the full link the engine printed (the one ending in
        <code> ?token=…</code>) to take control.
      </Banner>);
  }

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
