import type { ReactNode } from "react";
import { Card } from "./components";
import { CONSOLE_GUIDES } from "./guideContent";
import type { GuideId } from "./guideContent";
import type { Mode } from "./types";

/**
 * The console's own tutorial pages: one per tab, plus a first-run tour.
 *
 * In the app rather than in `docs/`, because the person who needs them is the
 * guest handed a phone at the venue, and the runbook is on a laptop they have
 * never seen. In the MAIN bundle rather than a lazy chunk like the designer,
 * because help has to work when things are going wrong -- a guide fetched on
 * demand is a guide that does not load while the engine is down.
 *
 * Shown in place of the tab, under the same header: Master and Blackout stay
 * one tap away while someone is reading, and the banners keep saying why the
 * rig is doing what it is doing.
 */

export interface GuideSection {
  title: string;
  /** Numbered: a walkthrough, where the order is the point. */
  steps?: ReactNode[];
  /** Bulleted: things worth knowing, in no particular order. */
  notes?: ReactNode[];
}

export interface Guide {
  id: string;
  /** The short name, for the row of guides. */
  label: string;
  title: string;
  lead: ReactNode;
  sections: GuideSection[];
}

/** One guide's pages, shared by the console and the designer's drawer. */
export function GuideBody({ guide }: { guide: Guide }) {
  return (
    <div className="guide">
      <Card title={guide.title}>
        <p className="lead">{guide.lead}</p>
      </Card>
      {guide.sections.map((s) => (
        <Card key={s.title} title={s.title}>
          {s.steps && <ol>{s.steps.map((item, i) => <li key={i}>{item}</li>)}</ol>}
          {s.notes && <ul>{s.notes.map((item, i) => <li key={i}>{item}</li>)}</ul>}
        </Card>
      ))}
    </div>
  );
}

const TAB_LABELS: Record<string, string> = {
  show: "Show", color: "Color", move: "Move", bright: "Bright", setup: "Setup",
};

/** `#guide` or `#guide/<id>`: which guide the hash names, or null. */
export function guideFromHash(hash: string): GuideId | null {
  if (hash !== "guide" && !hash.startsWith("guide/")) return null;
  const id = hash.slice("guide/".length);
  return CONSOLE_GUIDES.some((g) => g.id === id) ? (id as GuideId) : "start";
}

export function GuideView({ id, mode, back, onPick, onClose, onOpenTab }: {
  id: GuideId; mode: Mode;
  /** The tab underneath, which Back returns to. */
  back: string;
  onPick: (id: GuideId) => void;
  onClose: () => void;
  onOpenTab: (tab: string) => void;
}) {
  const guide = CONSOLE_GUIDES.find((g) => g.id === id) ?? CONSOLE_GUIDES[0]!;
  const tab = guide.tab;
  return (
    <>
      <div className="pills guide-nav" role="group" aria-label="guides">
        {CONSOLE_GUIDES.map((g) => (
          <button key={g.id} className={g.id === guide.id ? "on" : ""}
                  aria-pressed={g.id === guide.id}
                  onClick={() => onPick(g.id)}>
            {g.label}
          </button>
        ))}
      </div>

      <GuideBody guide={guide} />

      <div className="guide-actions">
        {/* Only when it goes somewhere new. "Open Move" from Move's own guide,
            which Back already does, would be the same button twice. */}
        {tab && tab !== back && (
          <button className="on" onClick={() => onOpenTab(tab)}>
            {/* Setup is Design-only, and a button that silently did nothing in
                Perform would be worse than no button. Saying it switches mode
                is what makes switching acceptable. */}
            {tab === "setup" && mode === "perform"
              ? "Switch to Design and open Setup"
              : `Open ${TAB_LABELS[tab] ?? tab}`}
          </button>
        )}
        {guide.id === "designer" && (
          <a className="slot" href="#designer">Open the designer</a>
        )}
        <button onClick={onClose}>Back to {TAB_LABELS[back] ?? back}</button>
      </div>
    </>
  );
}

/**
 * Offered once per device, never forced.
 *
 * A card, not a modal: the device this most often appears on is a phone handed
 * to someone mid-set, and a dialog standing between them and GO is the wrong
 * first impression of a lighting console. One tap either way and it is gone for
 * good on this device.
 */
export function Welcome({ onTour, onDismiss }: {
  onTour: () => void; onDismiss: () => void;
}) {
  return (
    <section className="card welcome" aria-label="welcome">
      <h2><span className="grow">New here?</span></h2>
      <p className="small" style={{ marginTop: 0 }}>
        A quick tour of the header, the tabs, and the ideas behind them. Every
        tab also has its own guide under the <b>?</b> in the header.
      </p>
      <div className="row">
        <button className="on" style={{ flex: 1 }} onClick={onTour}>Take the tour</button>
        <button onClick={onDismiss}>Not now</button>
      </div>
    </section>
  );
}

const WELCOMED_KEY = "klights.guide.welcomed";

/** Whether this device has seen the tour offered. Unreadable storage counts as
 *  "no", which costs one card; the other way round would hide it for good. */
export function readWelcomed(): boolean {
  try { return localStorage.getItem(WELCOMED_KEY) === "1"; } catch { return false; }
}

export function writeWelcomed(): void {
  try { localStorage.setItem(WELCOMED_KEY, "1"); } catch { /* private mode: shown again */ }
}
