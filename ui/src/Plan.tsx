import { Card, rgbCss } from "./components";
import type { EngineState, FixtureState } from "./types";

/**
 * The room from above, with every beam drawn where it actually lands.
 *
 * This is the previz that will get used. The Unreal path renders a beautiful
 * room and needs a GPU, a 90 GB engine install and a second machine; every
 * number this needs is already in the snapshot the phone in your hand is
 * receiving ten times a second. At a venue, the question is never "how does
 * this look in a render" — it is "where is that beam going", and this answers
 * that on the show laptop with nothing installed.
 *
 * SVG rather than canvas, deliberately. There are six fixtures and ten frames a
 * second, so the imperative draw loop buys nothing; and an SVG plan is made of
 * elements a test can assert on, where a canvas is one opaque bitmap that would
 * need a mock context and would prove nothing about what was drawn.
 *
 * TOP-DOWN, so `y` (height) is dropped: x runs right, z runs down the screen.
 * Height is the one axis a plan cannot show, which is why a beam's landing
 * SURFACE is labelled rather than left to be inferred — "floor" and "ceiling"
 * are the same dot from up here.
 */

/** Room millimetres to draw a fixture dot at, so the rig is visible in a room
 *  the size of a warehouse without being blobs in a small one. */
const DOT_MM = 260;
const MARGIN_MM = 900;

/**
 * How much of the room one beam's footprint may cover before the drawing stops
 * being useful, as a fraction of the room's shorter side.
 *
 * `throw × tan(beam/2)` is the honest radius and it is unbounded: a 120° wash —
 * legal, and what half the fixtures in a club are — lands a disc wider than the
 * room, and the schema's own maximum of 180° produces an effectively infinite
 * one. Past this point the disc has stopped saying "the beam covers here" and
 * started saying nothing at all while painting over everything that does.
 *
 * Capped rather than hidden: a wide fixture IS lighting a large area, and
 * dropping it would under-report the rig. The cap is marked so it does not read
 * as a measurement.
 */
const MAX_SPOT = 0.28;

/** Landing markers, by what the beam hit. Height is invisible in plan, so the
 *  thing a beam lands ON has to be said some other way. */
const SURFACE: Record<string, { label: string; dash?: string }> = {
  floor: { label: "floor" },
  wall: { label: "wall", dash: "300 200" },
  ceiling: { label: "ceiling", dash: "300 200" },
  canopy: { label: "canopy", dash: "600 260" },
  ball: { label: "ball" },
};

export function PlanView({ state }: { state: EngineState }) {
  const v = state.venue;
  // Every one of these is needed to place anything at all, so there is no
  // partial version of this view worth drawing.
  if (!v.width || !v.depth) {
    return (
      <Card title="Plan">
        <p className="small muted" style={{ margin: 0 }}>
          No room dimensions in the venue file, so there is nothing to draw a
          plan of. Set width and depth on the Setup tab.
        </p>
      </Card>
    );
  }

  const placed = state.fixtures.filter((f) => f.position);
  const ball = v.ball;
  const crowd = v.crowd;
  const canopy = v.canopy;

  return (
    <Card title="Plan" right={
      <span className="small muted mono">
        {(v.width / 1000).toFixed(1)} × {(v.depth / 1000).toFixed(1)} m
      </span>
    }>
      <svg role="img" aria-label="plan view of the room"
           className="plan"
           viewBox={`${-MARGIN_MM} ${-MARGIN_MM} ${v.width + MARGIN_MM * 2} ${v.depth + MARGIN_MM * 2}`}
           style={{ width: "100%", height: "auto", display: "block" }}>
        {/* Room first, so everything else sits on top of it. */}
        <rect x={0} y={0} width={v.width} height={v.depth}
              fill="var(--panel-2)" stroke="var(--line)" strokeWidth={60} />

        {crowd && (
          <rect x={crowd.min_x} y={crowd.min_z}
                width={crowd.max_x - crowd.min_x}
                height={crowd.max_z - crowd.min_z}
                fill="rgba(255, 176, 32, 0.10)"
                stroke="rgba(255, 176, 32, 0.5)" strokeWidth={40}
                strokeDasharray="240 200">
            <title>Crowd zone — the taper dims beams that cross head height here</title>
          </rect>
        )}

        {canopy?.enabled && ball && (
          <circle cx={ball[0]} cy={ball[2]} r={canopy.radius}
                  fill="none" stroke="var(--line)" strokeWidth={40}
                  strokeDasharray="600 300">
            <title>Canopy at {(canopy.height / 1000).toFixed(1)} m</title>
          </circle>
        )}

        {/* Beams under the fixtures, so a dot is never hidden by its own beam. */}
        {placed.map((f) => (
          <Beam key={`beam-${f.id}`} f={f}
                cap={Math.min(v.width!, v.depth!) * MAX_SPOT} />
        ))}
        {placed.map((f) => <Dot key={`dot-${f.id}`} f={f} />)}

        {ball && (
          <g>
            <circle cx={ball[0]} cy={ball[2]} r={DOT_MM * 1.1}
                    fill="none" stroke="var(--text)" strokeWidth={50} />
            <circle cx={ball[0]} cy={ball[2]} r={DOT_MM * 0.35}
                    fill="var(--text)" />
            <title>Mirror ball</title>
          </g>
        )}
      </svg>

      <Legend state={state} placed={placed}
              cap={Math.min(v.width!, v.depth!) * MAX_SPOT} />
    </Card>
  );
}

/**
 * One beam: its axis, and a disc where it lands.
 *
 * The disc is the beam's real footprint — `throw × tan(half-angle)` — which is
 * the number that makes a plan worth looking at. A beam drawn as a hairline
 * says a head is pointing somewhere; a beam drawn at its actual width says
 * whether it is going to cover the dancefloor or a doorway.
 *
 * A beam pointing nearly straight up collapses to almost nothing here, and that
 * is correct rather than a bug: from above, an aerial beam IS a short line. The
 * landing label is what tells you it went to the canopy.
 */
function Beam({ f, cap }: { f: FixtureState; cap: number }) {
  const from = f.position!;
  const to = f.lands_at;
  const lit = f.intensity ?? 0;
  if (!to || lit <= 0.001) return null;

  const color = rgbCss(f.color, "#888");
  const taper = f.safety?.taper ?? 1;
  const surface = SURFACE[f.lands_on ?? ""] ?? { label: f.lands_on ?? "?" };
  // The beam's radius where it lands: throw x tan(half-angle). `throw_mm` is
  // the 3D distance and this is a plan, but the footprint is a property of the
  // beam rather than of the projection, so it is the honest radius to draw.
  const spread = (f.throw_mm ?? 0)
    * Math.tan(((f.beam_deg ?? 8) / 2) * Math.PI / 180);
  const radius = Math.min(Math.max(spread, 120), cap);
  const capped = spread > cap;

  return (
    <g opacity={Math.max(0.18, lit)}>
      <line x1={from[0]} y1={from[2]} x2={to[0]} y2={to[2]}
            stroke={color} strokeWidth={70} strokeLinecap="round"
            strokeDasharray={surface.dash} />
      {/* Drawn hollow once capped: a filled disc at the cap looks like a
          measured footprint, and this one is "at least this big". */}
      <circle cx={to[0]} cy={to[2]} r={radius}
              fill={color} fillOpacity={capped ? 0.10 : 0.28}
              strokeDasharray={capped ? "400 300" : undefined}
              stroke={color} strokeWidth={40} strokeOpacity={0.7} />
      {/* The taper is the one thing here that is not simply geometry, so it
          gets a mark of its own rather than being inferred from a dimmer
          beam — a beam at 50% because the operator pulled it down and a beam
          at 50% because it is over someone's head are different facts. */}
      {taper < 1 && (
        <circle cx={to[0]} cy={to[2]} r={radius + 180}
                fill="none" stroke="var(--warn)" strokeWidth={50}
                strokeDasharray="200 160" />
      )}
      <title>
        {f.name} → {surface.label}
        {f.throw_mm != null && ` at ${(f.throw_mm / 1000).toFixed(1)} m`}
        {capped && ` · spreads ${(spread * 2 / 1000).toFixed(1)} m wide, `
                 + `drawn clipped`}
        {taper < 1 && ` · taper holding it at ${Math.round(taper * 100)}%`}
      </title>
    </g>
  );
}

function Dot({ f }: { f: FixtureState }) {
  const p = f.position!;
  const lit = f.intensity ?? 0;
  return (
    <g>
      <circle cx={p[0]} cy={p[2]} r={DOT_MM}
              fill={lit > 0.001 ? rgbCss(f.color, "#888") : "var(--panel)"}
              stroke={f.jogging ? "var(--bad)" : "var(--text)"}
              strokeWidth={f.jogging ? 90 : 45} />
      <title>{f.name}{f.jogging ? " — JOGGING, taper bypassed" : ""}</title>
    </g>
  );
}

/**
 * What the picture means, and what it is not showing.
 *
 * The count of fixtures with no position is the important half: a plan that
 * quietly omits an unpositioned fixture looks complete, and someone would
 * reasonably conclude the rig is doing nothing over there.
 */
function Legend({ state, placed, cap }: {
  state: EngineState; placed: FixtureState[]; cap: number;
}) {
  const clipped = (f: FixtureState) =>
    (f.throw_mm ?? 0) * Math.tan(((f.beam_deg ?? 8) / 2) * Math.PI / 180) > cap;
  const missing = state.fixtures.length - placed.length;
  const landing = placed.filter((f) => f.lands_at && (f.intensity ?? 0) > 0.001);
  const tapered = landing.filter((f) => (f.safety?.taper ?? 1) < 1);

  return (
    <>
      {/* Every beam's landing surface IN TEXT, not only in the SVG tooltips.
          Height is the axis a plan cannot draw, so "wall" and "ceiling" are the
          same dashed line from up here — and they are exactly the two worth
          telling apart, since one of them is at head height. The tooltips were
          the first answer to that and they are useless on the surface this is
          built for: a phone has no hover. */}
      {landing.length > 0 && (
        <div className="grid two plan-landings" style={{ marginTop: "0.6rem" }}>
          {landing.map((f) => {
            const taper = f.safety?.taper ?? 1;
            return (
              <div key={f.id} className="small">
                <span className="chip" style={{
                  background: rgbCss(f.color), borderColor: "transparent",
                  minWidth: 14, marginRight: 6,
                }} />
                {f.name} → <b>{SURFACE[f.lands_on ?? ""]?.label ?? f.lands_on}</b>
                {f.throw_mm != null && (
                  <span className="muted mono"> {(f.throw_mm / 1000).toFixed(1)}m</span>
                )}
                {/* In the list, not only in the SVG tooltip — a phone has no
                    hover, which is the same reason the landing surface is
                    written out here. */}
                {clipped(f) && <span className="muted"> · wide, drawn clipped</span>}
                {taper < 1 && (
                  <span style={{ color: "var(--warn)" }}>
                    {" "}· held at {Math.round(taper * 100)}%
                  </span>
                )}
              </div>
            );
          })}
        </div>
      )}

      <p className="small muted" style={{ margin: "0.6rem 0 0" }}>
        Top-down, so height is the one axis this cannot show — which is why each
        beam names what it landed on. Discs are the width the beam really
        spreads to; the dashed box is the crowd zone and the dashed circle the
        canopy.
        {tapered.length > 0 && (
          <> <span style={{ color: "var(--warn)" }}>
            {tapered.length} ringed in amber — the safety taper is holding them
            down.
          </span></>
        )}
        {missing > 0 && (
          <> <b>{missing} fixture(s) have no position in the patch and are not
            shown</b> — add one on Setup so the plan is the whole rig.</>
        )}
      </p>
    </>
  );
}
