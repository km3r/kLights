import { useEffect, useRef } from "react";
import { apiUrl, useWakeLock } from "../useEngine";
import type { useEngine } from "../useEngine";
import type { VisualItem, VisualsState } from "../types";
import { SCENES, VISUALS_CHUNK, beatNow, reanchor, strobeLevel } from "./scenes";
import type { Anchor, StrobePolicy } from "./scenes";
import "./visuals.css";

/**
 * `#visuals` (milestone 3): the built-in visuals, full screen, for a laptop on
 * a projector. Its own chunk: a phone never downloads it.
 *
 * It draws what the snapshot's `visuals` section says is on -- scenes from the
 * track's timeline or the template set's picks -- and runs on between
 * snapshots at the show's tempo (`scenes.reanchor`). Generative scenes are
 * painted on a canvas every animation frame; videos from the show folder's
 * media/ play underneath them (give a scene an opacity to see one through it).
 * Tap for full screen. The screen is kept awake, as the console's is: a
 * projector laptop that sleeps mid-set is a black screen until someone walks
 * over to it.
 */

type Engine = ReturnType<typeof useEngine>;

const OFF: StrobePolicy = { enabled: false, ceiling: 0, max_seconds: 0 };

export interface Live {
  vis: VisualsState | null;
  policy: StrobePolicy;
  snapBeat: number;      // the snapshot's own beat, for each item's elapsed
}

export default function Visuals({ engine }: { engine: Engine }) {
  const { state, status } = engine;
  const vis = state?.visuals ?? null;
  const policy = state?.strobe_policy ?? OFF;
  const canvas = useRef<HTMLCanvasElement>(null);
  const anchor = useRef<Anchor | null>(null);
  const live = useRef<Live>({ vis: null, policy: OFF, snapBeat: 0 });
  const strobeSince = useRef(new Map<string, number>());
  useWakeLock(true);

  useEffect(() => {
    if (vis && vis.beat != null) {
      anchor.current = reanchor(anchor.current, vis.beat, vis.bpm ?? 0, performance.now());
    } else {
      anchor.current = null;
    }
    live.current = { vis, policy, snapBeat: vis?.beat ?? 0 };
  }, [vis, policy]);

  useEffect(() => {
    let raf = 0;
    let ctx: CanvasRenderingContext2D | null | undefined;
    const draw = () => {
      raf = requestAnimationFrame(draw);
      const el = canvas.current;
      if (!el) return;
      if (ctx === undefined) ctx = el.getContext("2d");
      if (!ctx) return;
      const ratio = window.devicePixelRatio || 1;
      const w = Math.max(1, Math.round(el.clientWidth * ratio));
      const h = Math.max(1, Math.round(el.clientHeight * ratio));
      if (el.width !== w || el.height !== h) { el.width = w; el.height = h; }
      ctx.clearRect(0, 0, w, h);
      paintFrame(ctx, w, h, live.current, anchor.current, strobeSince.current,
                 performance.now());
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, []);

  const items = vis?.items ?? [];
  const videos = items.filter((i) => i.scene === "video");
  return (
    <div className="visuals" data-chunk={VISUALS_CHUNK}
         data-scenes={items.map((i) => i.scene).join(" ")}
         onClick={() => { document.documentElement.requestFullscreen?.().catch(() => {}); }}>
      {videos.map((item) => (
        <Video key={item.key} item={item} bpm={vis?.bpm ?? 0} />))}
      <canvas ref={canvas} className="visuals-canvas" aria-label="visuals" />
      {status !== "open" && (
        <p className="visuals-note">kLights visuals · waiting for the engine</p>)}
    </div>
  );
}

/** One frame of the generative scenes, in the order the snapshot lists them.
 *  Only the first strobe item flashes: two at different rates would add up
 *  to more than MAX_FLASH_HZ between them, whatever each one keeps to.
 *  Exported for tests, with a recording context in place of a canvas's. */
export function paintFrame(ctx: CanvasRenderingContext2D, w: number, h: number,
                           live: Live, anchor: Anchor | null,
                           strobeSince: Map<string, number>, now: number): void {
  const { vis, policy, snapBeat } = live;
  const beat = beatNow(anchor, now);
  const on = new Set<string>();
  if (vis && beat != null) {
    const bpm = vis.bpm ?? 0;
    let strobing = false;
    for (const item of vis.items) {
      const paint = SCENES[item.scene];
      if (!paint) continue;
      let strobe = 0;
      if (item.scene === "strobe") {
        if (strobing) continue;
        strobing = true;
        on.add(item.key);
        if (!strobeSince.has(item.key)) strobeSince.set(item.key, now);
        const rate = typeof item.params.rate === "number" ? item.params.rate : 1;
        strobe = strobeLevel(beat, bpm, rate, policy,
                             (now - strobeSince.get(item.key)!) / 1000);
      }
      paint({ ctx, w, h, beat, bpm, elapsed: item.elapsed + (beat - snapBeat),
              params: item.params, palette: vis.palette, strobe });
    }
  }
  // A strobe that went off and came back is a new one: its clock restarts.
  for (const key of [...strobeSince.keys()]) if (!on.has(key)) strobeSince.delete(key);
}

/** A video item: the show folder's file, from where the show is in it. With
 *  `rate: "beat"` it plays at the DJ's tempo over its own `bpm`. */
function Video({ item, bpm }: { item: VisualItem; bpm: number }) {
  const ref = useRef<HTMLVideoElement>(null);
  const file = typeof item.params.file === "string" ? item.params.file : "";
  const own = typeof item.params.bpm === "number" ? item.params.bpm : 0;
  const rate = item.params.rate === "beat" && own > 0 && bpm > 0
    ? Math.max(0.25, Math.min(4, bpm / own)) : 1;
  const opacity = typeof item.params.opacity === "number" ? item.params.opacity : 1;

  useEffect(() => {
    if (ref.current) ref.current.playbackRate = rate;
  }, [rate]);

  return (
    <video ref={ref} className="visuals-video" src={apiUrl(`/api/media/${file}`)}
           muted playsInline autoPlay loop={item.params.loop !== false}
           style={{ opacity }} data-key={item.key}
           onLoadedMetadata={(e) => {
             // Joined part-way through the item: start where the show is.
             const v = e.currentTarget;
             const at = bpm > 0 ? (item.elapsed * 60) / bpm * rate : 0;
             if (Number.isFinite(v.duration) && v.duration > 0) {
               v.currentTime = item.params.loop === false
                 ? Math.min(at, v.duration) : at % v.duration;
             }
             v.playbackRate = rate;
           }} />
  );
}
