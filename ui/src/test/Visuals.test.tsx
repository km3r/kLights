import { act, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import type { VisualsState } from "../types";
import {
  MAX_FLASH_HZ, RUN_ON_S, beatNow, flashesPerBeat, pulsePhase, reanchor, resolveColor,
  strobeLevel,
} from "../visuals/scenes";
import { paintFrame } from "../visuals/Visuals";
import { currentSocket, installMockSocket, stateWith } from "./mockSocket";

const ON = { enabled: true, ceiling: 0.6, max_seconds: 8 };

describe("visuals: time", () => {
  it("runs on from a snapshot at the show's tempo", () => {
    const a = { beat: 160, bpm: 120, at: 1000 };
    expect(beatNow(a, 1000)).toBe(160);
    expect(beatNow(a, 1500)).toBeCloseTo(161);           // half a second at 120
    expect(beatNow({ ...a, bpm: 0 }, 1500)).toBe(160);   // paused: it holds
  });

  it("holds after a while without snapshots, rather than racing ahead", () => {
    const a = { beat: 0, bpm: 120, at: 0 };
    expect(beatNow(a, 60_000)).toBeCloseTo(RUN_ON_S * 2);
  });

  it("eases a small correction in, and takes a jump at once", () => {
    const a = { beat: 100, bpm: 120, at: 0 };
    const eased = reanchor(a, 101.2, 120, 500);          // predicted 101
    expect(eased.beat).toBeCloseTo(101 + 0.2 * 0.3);
    expect(reanchor(a, 64, 120, 500).beat).toBe(64);      // a hot cue back
    expect(reanchor(null, 7, 120, 0).beat).toBe(7);
  });

  it("reads palette roles and hex, and nothing else", () => {
    const palette = { primary: "#ff2d6f" };
    expect(resolveColor("@primary", palette)).toBe("#ff2d6f");
    expect(resolveColor("@accent", palette, "#000000")).toBe("#000000");
    expect(resolveColor("#00ff00", palette)).toBe("#00ff00");
    expect(resolveColor("MH Red", palette)).toBe("#ffffff");
  });
});

describe("visuals: the strobe obeys the strobe policy", () => {
  it("never flashes more than the broadcast limit, halving to stay on the beat", () => {
    expect(flashesPerBeat(4, 128)).toBe(1);               // 128 bpm: at most ~1.4 a beat
    expect(flashesPerBeat(1, 60)).toBe(1);                // 1 Hz: allowed
    expect(flashesPerBeat(16, 300) * 300 / 60).toBeLessThanOrEqual(MAX_FLASH_HZ);
  });

  it("is dark with strobe off, at most the ceiling, and stops after max_seconds", () => {
    expect(strobeLevel(0.05, 120, 1, { ...ON, enabled: false }, 0)).toBe(0);
    expect(strobeLevel(0.05, 120, 1, ON, 0)).toBe(0.6);
    expect(strobeLevel(0.5, 120, 1, ON, 0)).toBe(0);          // between flashes
    expect(strobeLevel(0.05, 120, 1, ON, 8)).toBe(0);         // run out
    expect(strobeLevel(0.05, 0, 1, ON, 0)).toBe(0);           // paused
  });

  it("pulses a whole-screen wash no faster either: once a beat, or every two", () => {
    expect(pulsePhase(160.5, 120)).toBeCloseTo(0.5);      // 2 Hz: once a beat
    expect(pulsePhase(161, 200)).toBeCloseTo(0.5);        // 3.3 Hz a beat: every two
    for (const bpm of [60, 128, 174, 200, 300]) {
      expect(flashesPerBeat(1, bpm) * bpm / 60).toBeLessThanOrEqual(MAX_FLASH_HZ);
    }
  });
});

/** A canvas context that only remembers what was asked of it. */
function recorder() {
  const calls: { op: string; style: string }[] = [];
  const ctx = {
    fillStyle: "", strokeStyle: "", lineWidth: 1,
    fillRect() { calls.push({ op: "fill", style: String(ctx.fillStyle) }); },
    strokeRect() { calls.push({ op: "stroke", style: String(ctx.strokeStyle) }); },
    clearRect() {},
  };
  return { ctx: ctx as unknown as CanvasRenderingContext2D, calls };
}

function vis(items: Partial<VisualsState["items"][number]>[], extra: Partial<VisualsState> = {}): VisualsState {
  return {
    mode: "timeline", beat: 160, bpm: 120, phrase: "Chorus",
    palette: { primary: "#ff2d6f", secondary: "#2d6fff", accent: "#ffffff" },
    items: items.map((i, n) => ({ key: `k${n}`, scene: "wash", params: {}, elapsed: 0,
                                  len: 32, source: "timeline", ...i })),
    ...extra,
  };
}

describe("visuals: a frame", () => {
  it("paints each scene on, in the palette's colors", () => {
    const { ctx, calls } = recorder();
    const live = { vis: vis([{ scene: "wash", params: { color: "@primary" } }]),
                   policy: ON, snapBeat: 160 };
    paintFrame(ctx, 100, 50, live, { beat: 160, bpm: 120, at: 0 }, new Map(), 0);
    expect(calls).toEqual([{ op: "fill", style: "rgba(255, 45, 111, 1.000)" }]);
  });

  it("draws no strobe at all while the engine's policy has strobe off", () => {
    const { ctx, calls } = recorder();
    const since = new Map<string, number>();
    const live = { vis: vis([{ scene: "strobe" }]), policy: { ...ON, enabled: false },
                   snapBeat: 160 };
    paintFrame(ctx, 100, 50, live, { beat: 160, bpm: 120, at: 0 }, since, 0);
    expect(calls).toEqual([]);
    paintFrame(ctx, 100, 50, { ...live, policy: ON }, { beat: 160, bpm: 120, at: 0 }, since, 0);
    expect(calls).toEqual([{ op: "fill", style: "rgba(255, 255, 255, 0.600)" }]);
  });

  it("pulses a wash at the capped rate, from the snapshot's tempo", () => {
    const { ctx, calls } = recorder();
    const live = { vis: vis([{ scene: "wash", params: { color: "@primary", pulse: 1 } }],
                            { bpm: 200 }),
                   policy: ON, snapBeat: 161 };
    paintFrame(ctx, 100, 50, live, { beat: 161, bpm: 200, at: 0 }, new Map(), 0);
    // Half way through a two-beat pulse, not the start of a one-beat one.
    expect(calls).toEqual([{ op: "fill", style: "rgba(255, 45, 111, 0.500)" }]);
  });

  it("flashes one strobe item at a time, however many are on", () => {
    const { ctx, calls } = recorder();
    const since = new Map<string, number>();
    const live = { vis: vis([{ scene: "strobe" }, { scene: "strobe", params: { rate: 2 } }]),
                   policy: ON, snapBeat: 160 };
    paintFrame(ctx, 10, 10, live, { beat: 160, bpm: 120, at: 0 }, since, 0);
    expect(calls).toEqual([{ op: "fill", style: "rgba(255, 255, 255, 0.600)" }]);
    expect([...since.keys()]).toEqual(["k0"]);
  });

  it("times a strobe from when it came on, and forgets it once it goes", () => {
    const { ctx, calls } = recorder();
    const since = new Map<string, number>();
    const anchor = { beat: 160, bpm: 120, at: 0 };
    const live = { vis: vis([{ scene: "strobe" }]), policy: ON, snapBeat: 160 };
    paintFrame(ctx, 10, 10, live, anchor, since, 0);
    paintFrame(ctx, 10, 10, live, { ...anchor, at: 9000 }, since, 9000);  // 9 s on
    expect(calls.length).toBe(1);                         // the cut-off held
    paintFrame(ctx, 10, 10, { ...live, vis: vis([]) }, anchor, since, 9100);
    expect(since.size).toBe(0);
  });
});

describe("the #visuals page", () => {
  // Plain functions, not setup.ts's mocks: restoreAllMocks below runs before
  // the page unmounts, and would leave a mocked release() returning nothing.
  const wakeRequests: string[] = [];
  beforeEach(() => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    sessionStorage.setItem("klights.token", "tok");
    wakeRequests.length = 0;
    Object.defineProperty(navigator, "wakeLock", {
      configurable: true,
      value: { request: async (type: string) => {
        wakeRequests.push(type);
        return { release: async () => {} };
      } },
    });
  });
  afterEach(() => {
    vi.restoreAllMocks();
    sessionStorage.clear();
    location.hash = "";
  });

  it("is its own page: the scenes on now, and videos from media/ with the token", async () => {
    location.hash = "#visuals";
    installMockSocket();
    const { container } = render(<App />);
    const socket = currentSocket();
    act(() => socket.open());
    act(() => socket.push(stateWith((s) => {
      s.visuals = vis([{ scene: "video", params: { file: "intro.mp4", rate: "beat", bpm: 120 } },
                       { scene: "tunnel", params: { color: "@primary" } }]);
    })));
    const page = await waitFor(() => {
      const el = container.querySelector(".visuals");
      expect(el).not.toBeNull();
      return el!;
    });
    expect(page.getAttribute("data-scenes")).toBe("video tunnel");
    expect(page.getAttribute("data-chunk")).toBe("klights-visuals");
    const video = page.querySelector("video")!;
    expect(video.getAttribute("src")).toBe("/api/media/intro.mp4?token=tok");
    expect(video.muted).toBe(true);
    expect(container.querySelector(".tabs")).toBeNull();   // not the console
  });

  it("keeps the projector awake", async () => {
    location.hash = "#visuals";
    installMockSocket();
    const { container } = render(<App />);
    await waitFor(() => expect(container.querySelector(".visuals")).not.toBeNull());
    await waitFor(() => expect(wakeRequests).toContain("screen"));
  });

  it("is black with nothing on, and says so only while the engine is away", async () => {
    location.hash = "#visuals";
    installMockSocket();
    const { container } = render(<App />);
    await waitFor(() => expect(container.querySelector(".visuals")).not.toBeNull());
    expect(container.textContent).toContain("waiting for the engine");
    act(() => currentSocket().open());
    act(() => currentSocket().push(stateWith((s) => { s.visuals = vis([]); })));
    await waitFor(() => expect(container.textContent).not.toContain("waiting"));
    expect(container.querySelector(".visuals")!.getAttribute("data-scenes")).toBe("");
  });
});
