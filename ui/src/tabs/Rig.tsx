import { Banner, Card, rgbCss } from "../components";
import type { EngineState } from "../types";

/**
 * What is patched, what it is doing, and what the engine is not driving.
 *
 * Read-only, and last on the Setup tab: it is reference material for when
 * something is wrong, not a control surface.
 *
 * The last part matters most. The old console had an "Unsorted" section for
 * widgets it did not know how to place, so nothing could exist in the workspace
 * without appearing somewhere. The equivalent here is the warnings list: a
 * channel with no role, a pixel bar whose cells are not being driven, a mover
 * quantising to 8 bits. Surfacing them beats discovering them on the night.
 */
export function RigSection({ state }: { state: EngineState }) {
  const v = state.venue;

  return (
    <>
      {state.warnings.length > 0 && (
        <Card title="Not driven">
          <div className="banners">
            {state.warnings.map((w, i) => (
              <Banner key={i} kind="warn">{w}</Banner>
            ))}
          </div>
        </Card>
      )}

      <Card title="Patch">
        <div className="grid two">
          {state.fixtures.map((f) => (
            <div key={f.id} className="fixture">
              <div className="name">
                <span className="grow">{f.name}</span>
                <span className="chip" style={{
                  background: rgbCss(f.color), borderColor: "transparent", minWidth: 22,
                }} />
              </div>
              <div className="small muted mono">
                u{f.universe} · ch {f.address}
                {f.head != null && <> · head {f.head}</>}
              </div>
              <div className="row tight">
                {f.tags.map((t) => <span key={t} className="chip">{t}</span>)}
              </div>
              <div className="bar">
                <i style={{ width: `${Math.round((f.intensity ?? 0) * 100)}%` }} />
              </div>
            </div>
          ))}
        </div>
      </Card>

      <Card title="Room">
        <div className="small muted">
          {v.name} — {((v.width ?? 0) / 1000).toFixed(1)} ×{" "}
          {((v.depth ?? 0) / 1000).toFixed(1)} m,{" "}
          {((v.height ?? 0) / 1000).toFixed(1)} m high
        </div>
        {v.crowd && (
          <p className="small muted">
            Crowd head band {v.crowd.head_band_min / 1000}–
            {v.crowd.head_band_max / 1000} m. A beam whose core crosses it over
            the crowd is dimmed rather than cut — the goal is not blinding, not
            never landing on anyone.
          </p>
        )}
        {v.canopy && (
          <p className="small muted" style={{ marginBottom: 0 }}>
            Canopy {v.canopy.enabled ? "rigged" : "not rigged"} at{" "}
            {(v.canopy.height / 1000).toFixed(1)} m, radius{" "}
            {(v.canopy.radius / 1000).toFixed(1)} m.
          </p>
        )}
      </Card>

      <Card title="Engine">
        <div className="grid two small mono">
          <div>{state.stats.fps.toFixed(2)} fps</div>
          <div>{state.stats.frames} frames</div>
          <div>{state.stats.drops} drop(s)</div>
          <div>{state.stats.eval_errors} show error(s)</div>
          <div>worst {state.stats.worst_error_ms.toFixed(2)} ms</div>
          <div>rev {state.rev}</div>
        </div>
        {state.last_error && (
          <pre className="small" style={{
            whiteSpace: "pre-wrap", color: "var(--warn)",
            maxHeight: 160, overflow: "auto",
          }}>{state.last_error}</pre>
        )}
      </Card>
    </>
  );
}
