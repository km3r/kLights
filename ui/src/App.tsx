import { Suspense, lazy, useEffect, useRef, useState } from "react";
import { useEngine, useWakeLock } from "./useEngine";
import { ModeProvider, useMode } from "./mode";
import { Banner, BeatDots, Fader } from "./components";
import { GuideView, Welcome, guideFromHash, readWelcomed, writeWelcomed } from "./Guide";
import type { GuideId } from "./guideContent";
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

// The designer (F19l), split into its own chunk: only a desk that opens
// #designer downloads it -- a phone never does.
const Designer = lazy(() => import("./designer/Designer"));

/** `#designer`, `#designer/<track>` or `#designer/routine/<id>`; null for
 *  the console. */
function designerRoute(): { track: string | null; routine: string | null } | null {
  const hash = location.hash.slice(1);
  if (hash !== "designer" && !hash.startsWith("designer/")) return null;
  const parts = hash.split("/");
  if (parts[1] === "routine") return { track: null, routine: parts[2] || null };
  return { track: parts[1] || null, routine: null };
}

export default function App() {
  const engine = useEngine();
  // Decided BEFORE the console's tab effects run: they rewrite the hash to the
  // current tab, which would throw a fresh #designer link straight back to Show.
  const [route, setRoute] = useState(designerRoute);
  useEffect(() => {
    const onHash = () => setRoute(designerRoute());
    addEventListener("hashchange", onHash);
    return () => removeEventListener("hashchange", onHash);
  }, []);
  if (route) {
    return (
      <Suspense fallback={<p className="muted" style={{ padding: 16 }}>Loading the designer…</p>}>
        <Designer engine={engine} track={route.track} routine={route.routine} />
      </Suspense>
    );
  }
  return <Console engine={engine} />;
}

/** The tab a hash names, if this mode has a button for it. Checked against the
 *  tabs THIS mode has, not all of them: a phone that was last on Setup and
 *  reopens in Perform would otherwise restore a tab with no button in the bar
 *  and no way back to it. */
function tabFromHash(hash: string, mode: string): TabId | null {
  const tab = TABS.find((t) => t.id === hash);
  return tab && (mode === "design" || !("design" in tab)) ? tab.id : null;
}

function Console({ engine }: { engine: ReturnType<typeof useEngine> }) {
  const { state, status, send, name, setName, tier } = engine;
  const [mode, setMode] = useMode();
  // Keep the tab in the URL hash so a reload, or a phone waking up, comes back
  // where it was rather than to the front page mid-set. Read when the state is
  // created rather than in an effect: an effect ran after the first write of
  // the hash, and the hashchange that write queued could land after the
  // restore and undo it.
  const [tab, setTab] = useState<TabId>(
    () => tabFromHash(location.hash.slice(1), mode) ?? "show");
  // A guide open over the tab, which stays underneath for Back to return to.
  const [guide, setGuide] = useState<GuideId | null>(
    () => guideFromHash(location.hash.slice(1)));
  const [welcomed, setWelcomed] = useState(readWelcomed);
  // Local only while a finger is down; see Fader's comment.
  const [masterDrag, setMasterDrag] = useState<number | null>(null);

  useWakeLock(true);

  const tabs = TABS.filter((t) => mode === "design" || !("design" in t));

  useEffect(() => { location.hash = guide ? `guide/${guide}` : tab; }, [tab, guide]);
  // And follow it. The On now rows and the guides navigate with plain links,
  // which change the hash and, until this listened, changed nothing else.
  useEffect(() => {
    const follow = () => {
      const hash = location.hash.slice(1);
      const named = guideFromHash(hash);
      if (named) { setGuide(named); return; }
      const next = tabFromHash(hash, mode);
      if (next) { setTab(next); setGuide(null); }
    };
    addEventListener("hashchange", follow);
    return () => removeEventListener("hashchange", follow);
  }, [mode]);
  // Dropping to Perform while standing on Setup would otherwise leave the tab
  // rendered with no way back to it in the bar.
  useEffect(() => {
    if (!tabs.some((t) => t.id === tab)) setTab("show");
  }, [mode]);

  // A guide starts at its top. The main column keeps its scroll across
  // renders, so one opened from halfway down a tab would open halfway down.
  const mainRef = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (guide && mainRef.current) mainRef.current.scrollTop = 0;
  }, [guide]);

  const openGuide = (id: GuideId) => {
    setGuide(id);
    // Opening any guide answers the first-run card's question.
    if (!welcomed) { writeWelcomed(); setWelcomed(true); }
  };
  const openTab = (id: TabId) => {
    setTab(id);
    setGuide(null);
  };

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
          {/* The guide to whatever tab is up. In the header because it is the
              one place on screen on every tab, and a "?" because the
              explanation is the button's whole job. */}
          <button className={guide ? "small on" : "small"} aria-label="Guide"
                  aria-pressed={guide != null}
                  title="How this tab works"
                  onClick={() => (guide ? setGuide(null) : openGuide(tab))}>
            ?
          </button>
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

      <main className="main" ref={mainRef}>
        {!welcomed && !guide && (
          <Welcome onTour={() => openGuide("start")}
                   onDismiss={() => { writeWelcomed(); setWelcomed(true); }} />
        )}
        {/* Before the state check: a guide needs nothing from the engine, and
            "the engine is not answering" is exactly when someone reaches for
            one. */}
        {guide ? (
          <GuideView id={guide} mode={mode} back={tab}
                     onPick={openGuide} onClose={() => setGuide(null)}
                     onOpenTab={(id) => {
                       if (id === "setup" && mode !== "design") setMode("design");
                       openTab(id as TabId);
                     }} />
        ) : !state ? (
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
          <button key={t.id} className={tab === t.id && !guide ? "on" : ""}
                  onClick={() => openTab(t.id)}>
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
export function Banners({ state, status, send, tier }: {
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
  if (state?.preview) {
    // On EVERY console: a rig following a designer's scrub bar is the one state
    // where a DJ playing out and the lights disagree on purpose, and the
    // operator at the front has to know whose transport the rig is on.
    banners.push(<Banner key="preview" kind="warn">
      DESIGNER ({state.preview.name}) is driving the rig on
      {" "}{state.preview.track_id}{state.preview.draft ? " (unsaved draft)" : ""}.
      {/* Releasing is configure-tier; a view-only phone is told, not offered
          a button that can only fail. */}
      {tier === "configure" && (
        <button className="small" style={{ marginLeft: "auto" }}
                onClick={() => send({ type: "preview_release" })}>Release</button>)}
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
