import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import type { Command } from "../types";
import {
  BLOCK_ARGS, BLOCK_SLOT, CHASE_ORDERS, EASINGS, Grid, PARAM_TYPES, blocksFor, curveValue,
  decodeWave,
  whoDrives,
} from "../designer/model";
import {
  AUTOMATION_RANGES, paramSpec, routineLaneSpecs, timelineLaneSpecs,
} from "../designer/edit";
import blockLists from "../designer/__fixtures__/blocks.json";
import type { RoutineDoc, TimelineDoc } from "../designer/model";
import vectors from "../designer/__fixtures__/grid-vectors.json";
import trackDoc from "../../../shared/show-example/tracks/synth-128.json";
import timelineDoc from "../../../shared/show-example/timelines/synth-128.json";
import clubDoc from "../../../shared/show-example/templates/club.json";
import fanDrop from "../../../shared/show-example/routines/fan-drop.json";
import idleOrbit from "../../../shared/show-example/routines/idle-orbit.json";
import { currentSocket, installMockSocket, stateWith, type MockSocket } from "./mockSocket";

/**
 * The designer, against the example show folder served by a fake `/api` and a
 * fake engine socket. As with the console: what it shows for a document, and
 * what it SENDS when used -- including the commands that wait for an answer,
 * which the tests answer by hand.
 */

// -- the pure parts, against the engine's own numbers ----------------------------

describe("designer model", () => {
  it("converts beats and seconds exactly as the engine does", () => {
    for (const [name, v] of Object.entries(vectors as Record<string, {
      segments: number[][]; beat_at: number[][]; time_at: number[][]; bpm_at: number[][];
    }>)) {
      const grid = new Grid(v.segments);
      for (const [t, b] of v.beat_at) expect(grid.beatAt(t!), name).toBeCloseTo(b!, 9);
      for (const [b, t] of v.time_at) expect(grid.timeAt(b!), name).toBeCloseTo(t!, 9);
      for (const [t, bpm] of v.bpm_at) expect(grid.bpmAt(t!), name).toBeCloseTo(bpm!, 9);
    }
  });

  it("shapes a curve the way the engine does", () => {
    const pts: [number, number, string?][] = [[0, 0], [8, 1], [16, 0.5, "step"], [24, 1, "ease"]];
    expect(curveValue(pts as never, 4)).toBeCloseTo(0.5);
    expect(curveValue(pts as never, 15.99)).toBeCloseTo(1);
    expect(curveValue(pts as never, 20)).toBeCloseTo(0.75);
    expect(curveValue(pts as never, -5)).toBe(0);
  });

  it("says who drives each lane: the higher lane wins, an owning lane's gap rests", () => {
    const doc = timelineDoc as unknown as TimelineDoc;
    const at370 = whoDrives(doc, 370);
    expect(at370.find((d) => d.lane === "movement")?.item?.id).toBe("lazy");
    expect(at370.find((d) => d.lane === "color")?.item?.id).toBe("outro");
    const at100 = whoDrives(doc, 100);
    expect(at100.find((d) => d.lane === "palette")?.source).toBe("blank");
  });

  it("offers exactly the blocks, arguments and targets the engine has", () => {
    // blocks.json is written from engine/blocks.py and engine/showfiles.py
    expect(Object.keys(BLOCK_ARGS).sort()).toEqual(Object.keys(blockLists.slots).sort());
    expect(BLOCK_SLOT).toEqual(blockLists.slots);
    for (const [block, names] of Object.entries(blockLists.numeric)) {
      const numeric = BLOCK_ARGS[block]!.filter((a) => a.kind === "number").map((a) => a.name);
      expect(numeric.sort(), block).toEqual([...names].sort());
    }
    expect(CHASE_ORDERS).toEqual(blockLists.orders);
    expect(EASINGS).toEqual(blockLists.easings);
    expect(AUTOMATION_RANGES).toEqual(blockLists.automation);
    expect([...PARAM_TYPES]).toEqual(blockLists.param_types);
  });

  it("takes every block's defaults, steps and ranges from the engine", () => {
    // These used to be typed here by hand and held to the engine only on
    // argument NAMES, so a default could drift without any test noticing.
    // Now they are derived from blocks.PARAMS; this pins a few by value so a
    // broken derivation, not just a missing name, fails here.
    const arg = (block: string, name: string) =>
      BLOCK_ARGS[block]!.find((a) => a.name === name)!;
    expect(arg("orbit", "radius")).toMatchObject(
      { kind: "number", default: 20, min: 0, max: 90, step: 1, unit: "°" });
    expect(arg("pendulum", "width").default).toBe(30);
    expect(arg("chase", "order")).toMatchObject(
      { kind: "choice", default: "index", choices: CHASE_ORDERS });
    // No fixed default: half the width unless given. Undefined, so the field
    // says "auto" instead of a number that is wrong once the width changes.
    expect(arg("fan_sweep", "sweep").default).toBeUndefined();
    // An integer is a number to a field, stepping by whole numbers.
    expect(arg("scatter", "stations")).toMatchObject({ kind: "number", step: 1 });
    // A unit that only repeats the name is dropped rather than shown twice.
    expect(arg("orbit", "bars").unit).toBeUndefined();
  });

  it("ranges a parameter's lane by its declaration, then by the arguments it feeds", () => {
    const fan = routineLaneSpecs(fanDrop as unknown as RoutineDoc);
    // declared: what the engine holds the lane to
    expect(fan["param.width"]).toMatchObject(
      { kind: "number", min: 0, max: 120, lo: 0, hi: 120, unit: "deg", start: 40 });
    // a rate says nothing and is still held to 0-8, as the engine holds it
    expect(fan["param.rate"]).toMatchObject({ min: 0, max: 8, start: 1 });
    expect(fan["param.color"]).toMatchObject({ kind: "color", start: "@primary" });
    // open-ended: accepted as anything, drawn on the orbit radius it feeds
    const open = routineLaneSpecs({
      params: { r: { type: "number", default: 10 }, which: { type: "look" } },
      rows: [{ id: "m", type: "clips", target: "movement",
               items: [{ id: "o", at: 0, len: 4, block: "orbit", args: { radius: "$r" } }] }],
    });
    expect(open["param.r"]).toMatchObject({ lo: 0, hi: 90, min: undefined, max: undefined });
    // a look is chosen when the routine is built; no lane can change it
    expect(open["param.which"]).toBeUndefined();
    expect(paramSpec("which", { type: "look" })).toBeNull();
  });

  it("offers a track's timeline one lane per parameter name of the routines on it", () => {
    const specs = timelineLaneSpecs(timelineDoc as unknown as TimelineDoc, [
      { id: "fan-drop", params: fanDrop.params },
      { id: "idle-orbit", params: idleOrbit.params },
      { id: "verse-sweep", params: { width: { type: "number", min: 10, max: 90 } } },
      { id: "not-placed", params: { gobo: { type: "number" } } },
    ]);
    expect(specs["param.color"]!.reaches).toEqual(["fan-drop", "idle-orbit"]);
    // one lane drives every routine with the name, so it takes the narrowest range
    expect(specs["param.width"]).toMatchObject(
      { min: 10, max: 90, reaches: ["fan-drop", "verse-sweep"] });
    expect(specs["param.radius"]!.reaches).toEqual(["idle-orbit"]);
    expect(specs["param.gobo"]).toBeUndefined();
  });

  it("offers the blocks the console's parametric looks are built from", () => {
    // One building-block system: a routine and a console look use the same
    // parts, so a block added for one is available to the other.
    expect(blocksFor("movement")).toEqual(
      expect.arrayContaining(["figure8", "spiral", "scatter"]));
    expect(blocksFor("color")).toEqual(expect.arrayContaining(["hue_cycle", "duo"]));
    expect(blocksFor("level")).toEqual(expect.arrayContaining(["breathe"]));
    expect(BLOCK_ARGS.spiral!.find((a) => a.name === "direction"))
      .toMatchObject({ kind: "choice", choices: ["out", "in"] });
  });

  it("decodes rekordbox's colour waveform", () => {
    // rrrgggbbbhhhhh-- : full red, height 31
    const v = (7 << 13) | (31 << 2);
    const data = btoa(String.fromCharCode(v >> 8, v & 0xff));
    const wave = decodeWave({ detail: { format: "pwv5", rate: 150, data } })!;
    expect(wave.heights[0]).toBe(1);
    expect(wave.colors![0]).toEqual([1, 0, 0]);
  });
});

// -- the designer itself ------------------------------------------------------------

const TIMELINE_REV = "r:aaaaaaaaaaaa";
const ROUTINE_REV = "r:bbbbbbbbbbbb";
const ROUTINES = [
  { id: "fan-drop", name: "Fan sweep (drop)", bars: 8, loop: true, rig: null,
    params: fanDrop.params, variations: ["tight", "wide"], roles: fanDrop.roles },
  { id: "idle-orbit", name: "Idle orbit", bars: 8, loop: true, rig: null,
    params: idleOrbit.params, variations: [], roles: idleOrbit.roles },
];

function serve(path: string): [number, unknown] {
  if (path === "/api/tracks") {
    return [200, { tracks: [{ id: "synth-128", title: "synthetic 128", artist: "kLights",
                              bpm: 128, phrases: 8, has_timeline: true,
                              has_waveform: false, has_audio: false }] }];
  }
  if (path === "/api/tracks/synth-128") return [200, { doc: trackDoc, rev: "r:t" }];
  if (path === "/api/timelines/synth-128") return [200, { doc: timelineDoc, rev: TIMELINE_REV }];
  if (path === "/api/routines") return [200, { routines: ROUTINES }];
  if (path === "/api/routines/fan-drop") return [200, { doc: fanDrop, rev: ROUTINE_REV }];
  if (path === "/api/templates") return [200, { templates: [{ id: "club", name: "Club" }] }];
  if (path === "/api/templates/club") return [200, { doc: clubDoc, rev: "r:c" }];
  if (path === "/api/media") return [200, { media: [{ file: "loop-1.mp4", size: 5120 }] }];
  return [404, { error: `no ${path}` }];
}

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const [status, body] = serve(new URL(url, "http://engine").pathname);
    return { ok: status === 200, status, json: async () => body };
  }));
});
afterEach(() => {
  vi.unstubAllGlobals();
  location.hash = "";
});

async function open(hash = "#designer/synth-128") {
  location.hash = hash;
  installMockSocket();
  render(<App />);
  const socket = currentSocket();
  act(() => socket.open());
  return socket;
}

/** Answer the last command of a type that asked for a reply. */
function reply(socket: MockSocket, type: string, ok: boolean, data?: unknown, error?: string) {
  const sent = [...socket.sent].reverse().find((c) => c.type === type) as
    (Command & { id?: string }) | undefined;
  expect(sent, `a ${type} was sent`).toBeTruthy();
  act(() => socket.onmessage?.({ data: JSON.stringify({ type: "reply", id: sent!.id, ok,
                                                        data, error }) }));
  return sent!;
}

describe("designer", () => {
  it("lists the show folder's tracks", async () => {
    await open("#designer");
    expect(await screen.findByText("synthetic 128")).toBeInTheDocument();
    expect(screen.getByText(/8 phrases · timeline/)).toBeInTheDocument();
  });

  it("lays the track out as lanes: phrases, clips, hits, automation, the OSC lanes", async () => {
    await open();
    expect(await screen.findByRole("region", { name: "lanes" })).toBeInTheDocument();
    const lanes = screen.getByRole("region", { name: "lanes" });
    for (const phrase of ["Intro", "Verse 1", "Chorus", "Outro"]) {
      expect(within(lanes).getAllByText(phrase).length).toBeGreaterThan(0);
    }
    expect(within(lanes).getByLabelText("fan-drop at bar 41.1")).toBeInTheDocument();
    expect(within(lanes).getByLabelText("Lazy Circle at bar 89.1")).toBeInTheDocument();
    expect(within(lanes).getByLabelText("flash at bar 41.1")).toBeInTheDocument();
    expect(within(lanes).getByLabelText("automation master")).toBeInTheDocument();
    expect(within(lanes).getByLabelText("clips/3/connect at bar 41.1")).toBeInTheDocument();
    expect(within(lanes).getByLabelText("automation vj-opacity")).toBeInTheDocument();
    expect(within(lanes).getByLabelText("vj-opacity address"))
      .toHaveValue("/composition/layers/1/video/opacity");
    // Lane order is the file's: the movement lane sits above the scene lane.
    const order = screen.getAllByLabelText(/^lane /).map((el) => el.getAttribute("aria-label"));
    expect(order.slice(0, 2)).toEqual(["lane move", "lane scene"]);
  });

  it("says who drives each lane at the playhead", async () => {
    await open();
    await screen.findByRole("region", { name: "lanes" });
    expect(screen.getByText(/At the playhead · bar 1.1/)).toBeInTheDocument();
    const table = document.querySelector(".d-who") as HTMLElement;
    expect(within(table).getAllByText("Phase a").length).toBe(3);   // the intro snapshot
    expect(within(table).getByText(/rest \(palette owns it\)/)).toBeInTheDocument();
  });

  it("puts its transport on the rig, and lets go", async () => {
    const user = userEvent.setup();
    const socket = await open();
    await screen.findByRole("region", { name: "lanes" });
    await user.click(screen.getByRole("button", { name: "Drive the rig" }));
    const armed = reply(socket, "preview_arm", false, undefined,
                        "a DJ is playing (rkbx); preview with force to take the rig from them anyway");
    expect(armed).toMatchObject({ type: "preview_arm", track_id: "synth-128", force: false });
    await user.click(await screen.findByRole("button", { name: "Take the rig anyway" }));
    reply(socket, "preview_arm", true, { track_id: "synth-128" });
    await waitFor(() => expect(socket.sent.some((c) => c.type === "preview_transport")).toBe(true));
    await user.click(screen.getByRole("button", { name: "Release the rig" }));
    expect(socket.sent.some((c) => c.type === "preview_release")).toBe(true);
  });

  it("stops driving when the engine lets go of its preview, or another console takes it", async () => {
    const user = userEvent.setup();
    const socket = await open();
    act(() => socket.onmessage?.({ data: JSON.stringify({ type: "welcome", id: "c9",
                                                          tier: "configure" }) }));
    await screen.findByRole("region", { name: "lanes" });
    const ours = { client: "c9", name: "desk", track_id: "synth-128", draft: false,
                   playing: false, ready: true };

    await user.click(screen.getByRole("button", { name: "Drive the rig" }));
    reply(socket, "preview_arm", true, { track_id: "synth-128" });
    expect(await screen.findByRole("button", { name: "Release the rig" })).toBeInTheDocument();
    act(() => socket.push(stateWith((s) => { s.preview = ours; })));
    // a phone pressed Release on its banner
    act(() => socket.push(stateWith((s) => { s.preview = null; })));
    expect(await screen.findByText(/released from another console/)).toBeInTheDocument();
    const after = socket.sent.length;
    await new Promise((r) => setTimeout(r, 300));
    expect(socket.sent.slice(after).some((c) => c.type === "preview_transport")).toBe(false);
    expect(screen.getByRole("button", { name: "Drive the rig" })).toBeInTheDocument();

    // and again, until another designer forces its way on
    await user.click(screen.getByRole("button", { name: "Drive the rig" }));
    reply(socket, "preview_arm", true, { track_id: "synth-128" });
    await screen.findByRole("button", { name: "Release the rig" });
    act(() => socket.push(stateWith((s) => { s.preview = ours; })));
    act(() => socket.push(stateWith((s) => {
      s.preview = { ...ours, client: "c2", name: "laptop" };
    })));
    expect(await screen.findByText("laptop took the rig.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Release laptop's preview" })).toBeInTheDocument();
  });

  it("keeps the rig, and keeps checking drafts, while the engine's snapshots stream in", async () => {
    const user = userEvent.setup();
    const socket = await open();
    await screen.findByRole("region", { name: "lanes" });
    // 10 Hz, as the engine sends them: every one re-renders the page
    const stream = setInterval(() => act(() => socket.push(stateWith(() => {}))), 100);
    try {
      await waitFor(() => expect(socket.sent.some((c) => c.type === "timeline_draft")).toBe(true),
                    { timeout: 2000 });
      await user.click(screen.getByRole("button", { name: "Drive the rig" }));
      reply(socket, "preview_arm", true, { track_id: "synth-128" });
      // (not inside act: that would hold the snapshots' renders until it ends)
      await new Promise((r) => setTimeout(r, 450));
      expect(socket.sent.some((c) => c.type === "preview_release")).toBe(false);
      expect(socket.sent.filter((c) => c.type === "preview_transport").length)
        .toBeGreaterThan(2);
    } finally {
      clearInterval(stream);
    }
  });

  it("edits a clip in the inspector, checks it with the engine, saves with the rev", async () => {
    const user = userEvent.setup();
    const socket = await open();
    const lanes = await screen.findByRole("region", { name: "lanes" });
    fireEvent.pointerDown(within(lanes).getByLabelText("fan-drop at bar 41.1").querySelector("rect")!);
    const inspector = screen.getByRole("contentinfo", { name: "inspector" });
    expect(within(inspector).getByText("fan-drop")).toBeInTheDocument();
    await user.click(within(inspector).getByRole("button", { name: "tight" }));
    expect(within(lanes).getByText(/tight · color @primary/)).toBeInTheDocument();

    // Every change goes to the engine as a draft; its verdict gates Save.
    await waitFor(() => expect(socket.sent.some((c) => c.type === "timeline_draft")).toBe(true),
                  { timeout: 2000 });
    reply(socket, "timeline_draft", true,
          { errors: [], warnings: [], problems: ["look 'X' is not in this rig's library"] });
    expect(await screen.findByRole("button", { name: "1 note(s)" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Save" }));
    const save = reply(socket, "timeline_save", true, { rev: "r:bbbbbbbbbbbb" }) as
      Command & { doc: TimelineDoc; base_rev: string };
    expect(save.base_rev).toBe(TIMELINE_REV);
    const scene = save.doc.rows.find((r) => r.id === "scene")!;
    expect(scene.items!.find((i) => i.id === "chorus1")!.variation).toBe("tight");
    expect(await screen.findByRole("button", { name: "Saved" })).toBeDisabled();
  });

  it("cues a VJ app over OSC: a lane, a cue, its messages, saved", async () => {
    const user = userEvent.setup();
    const socket = await open();
    const lanes = await screen.findByRole("region", { name: "lanes" });
    // The example's drop cue: on when it starts, a clear when it ends.
    fireEvent.pointerDown(within(lanes).getByLabelText("clips/3/connect at bar 41.1")
      .querySelector("rect")!);
    let inspector = screen.getByRole("contentinfo", { name: "inspector" });
    expect(within(inspector).getByLabelText("on address"))
      .toHaveValue("/composition/layers/1/clips/3/connect");
    expect(within(inspector).getByLabelText("off address"))
      .toHaveValue("/composition/layers/1/clear");
    expect(within(inspector).queryByText("Fade in")).toBeNull();

    await user.selectOptions(screen.getByLabelText("add lane"), "osc");
    await user.click(await screen.findByRole("button", { name: "add a cue to osc" }));
    inspector = screen.getByRole("contentinfo", { name: "inspector" });
    const address = within(inspector).getByLabelText("on address");
    await user.clear(address);
    await user.type(address, "/layer/2/go");
    const args = within(inspector).getByLabelText("on args");
    await user.clear(args);
    await user.type(args, "1, $bar, club");
    await user.tab();
    await user.click(within(inspector).getByRole("button", { name: "+ off message" }));
    const off = within(inspector).getByLabelText("off address");
    await user.clear(off);
    await user.type(off, "/layer/2/clear");
    expect(within(lanes).getByLabelText(/layer\/2\/go at bar 1.1/)).toBeInTheDocument();

    await waitFor(() => expect(socket.sent.some((c) => c.type === "timeline_draft")).toBe(true),
                  { timeout: 2000 });
    reply(socket, "timeline_draft", true, { errors: [], warnings: [], problems: [] });
    await user.click(screen.getByRole("button", { name: "Save" }));
    const save = reply(socket, "timeline_save", true, { rev: "r:cccccccccccc" }) as
      Command & { doc: TimelineDoc };
    const row = save.doc.rows.find((r) => r.id === "osc")!;
    expect(row).toMatchObject({ type: "external", output: "osc" });
    expect(row.items![0]).toMatchObject({
      at: 0, len: 16, on: { address: "/layer/2/go", args: [1, "$bar", "club"] },
      off: { address: "/layer/2/clear", args: [] },
    });
  });

  it("plays MIDI through the sidecar: a note cue made a CC, and a CC curve", async () => {
    const user = userEvent.setup();
    const socket = await open();
    const lanes = await screen.findByRole("region", { name: "lanes" });
    await user.selectOptions(screen.getByLabelText("add lane"), "midi");
    await user.click(await screen.findByRole("button", { name: "add a cue to midi" }));
    const inspector = screen.getByRole("contentinfo", { name: "inspector" });
    expect(within(lanes).getByLabelText("note 60 at bar 1.1")).toBeInTheDocument();
    await user.click(within(within(inspector).getByRole("group", { name: "midi kind" }))
      .getByRole("button", { name: "CC" }));
    const cc = within(inspector).getByLabelText("midi cc");
    await user.clear(cc);
    await user.type(cc, "7");
    const then = within(inspector).getByLabelText("midi off_value");
    await user.type(then, "0");
    const channel = within(inspector).getByLabelText("midi channel");
    await user.clear(channel);
    await user.type(channel, "3");
    expect(within(lanes).getByLabelText("cc 7 at bar 1.1")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("add lane"), "midi-curve");
    expect(await screen.findByLabelText("midi-curve cc")).toHaveValue(1);

    await waitFor(() => expect(socket.sent.some((c) => c.type === "timeline_draft")).toBe(true),
                  { timeout: 2000 });
    reply(socket, "timeline_draft", true, { errors: [], warnings: [], problems: [] });
    await user.click(screen.getByRole("button", { name: "Save" }));
    const save = reply(socket, "timeline_save", true, { rev: "r:dddddddddddd" }) as
      Command & { doc: TimelineDoc };
    const sent = JSON.parse(JSON.stringify(save.doc)) as TimelineDoc;
    const item = sent.rows.find((r) => r.id === "midi")!.items![0]!;
    expect(item).toMatchObject({ cc: 7, value: 127, off_value: 0, channel: 3 });
    expect(item.note).toBeUndefined();
    expect(sent.rows.find((r) => r.id === "midi-curve")).toMatchObject({
      type: "external", output: "midi", channel: 1, cc: 1, points: [[0, 0]] });
  });

  it("puts a scene on the projector: a visuals lane, a tunnel, a video from media/", async () => {
    const user = userEvent.setup();
    const socket = await open();
    const lanes = await screen.findByRole("region", { name: "lanes" });
    expect(within(lanes).getByLabelText("tunnel at bar 41.1")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("add lane"), "visuals");
    await user.click(await screen.findByRole("button", { name: "add a cue to visuals" }));
    const inspector = screen.getByRole("contentinfo", { name: "inspector" });
    const scenes = within(inspector).getByRole("group", { name: "visuals scene" });
    await user.click(within(scenes).getByRole("button", { name: "tunnel" }));
    await user.type(within(inspector).getByLabelText("visuals speed"), "3");
    await user.selectOptions(within(inspector).getByLabelText("visuals color"), "@accent");
    expect(within(lanes).getByLabelText("tunnel at bar 1.1")).toBeInTheDocument();
    await user.click(within(scenes).getByRole("button", { name: "video" }));
    const file = await within(inspector).findByLabelText("visuals file");
    await waitFor(() => expect(within(file).getByRole("option", { name: "loop-1.mp4" }))
      .toBeInTheDocument());
    await user.selectOptions(file, "loop-1.mp4");
    await user.click(within(scenes).getByRole("button", { name: "tunnel" }));
    await user.type(within(inspector).getByLabelText("visuals speed"), "3");

    await waitFor(() => expect(socket.sent.some((c) => c.type === "timeline_draft")).toBe(true),
                  { timeout: 2000 });
    reply(socket, "timeline_draft", true, { errors: [], warnings: [], problems: [] });
    await user.click(screen.getByRole("button", { name: "Save" }));
    const save = reply(socket, "timeline_save", true, { rev: "r:eeeeeeeeeeee" }) as
      Command & { doc: TimelineDoc };
    const row = save.doc.rows.find((r) => r.id === "visuals")!;
    expect(row).toMatchObject({ type: "external", output: "visuals" });
    expect(row.items![0]).toMatchObject({ scene: "tunnel", params: { color: "@primary", speed: 3 } });
  });

  it("refuses to save while the engine says the draft is invalid", async () => {
    const user = userEvent.setup();
    const socket = await open();
    const lanes = await screen.findByRole("region", { name: "lanes" });
    fireEvent.pointerDown(within(lanes).getByLabelText("fan-drop at bar 41.1").querySelector("rect")!);
    await user.click(screen.getByRole("button", { name: "wide" }));
    await waitFor(() => expect(socket.sent.some((c) => c.type === "timeline_draft")).toBe(true),
                  { timeout: 2000 });
    reply(socket, "timeline_draft", true,
          { errors: ["row 'scene' item 'x' has length 0"], warnings: [], problems: [] });
    expect(await screen.findByRole("button", { name: "1 error(s)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("offers back the working copy a closed tab left behind", async () => {
    const user = userEvent.setup();
    const kept = structuredClone(timelineDoc) as unknown as TimelineDoc;
    kept.rows.find((r) => r.id === "scene")!.items!.find((i) => i.id === "chorus1")!.variation = "tight";
    localStorage.setItem("klights.draft.synth-128",
                         JSON.stringify({ doc: kept, rev: TIMELINE_REV, at: Date.now() }));
    await open();
    const lanes = await screen.findByRole("region", { name: "lanes" });
    const offer = await screen.findByRole("alertdialog", { name: "unsaved changes" });
    // not overwritten while it is being offered
    expect(localStorage.getItem("klights.draft.synth-128")).toContain("tight");
    await user.click(within(offer).getByRole("button", { name: "Restore them" }));
    expect(within(lanes).getByText(/tight · color @primary/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Undo" }));
    expect(within(lanes).queryByText(/tight · color @primary/)).toBeNull();
  });

  it("will not restore a kept copy over a file saved since, but lets it be downloaded", async () => {
    const user = userEvent.setup();
    localStorage.setItem("klights.draft.synth-128", JSON.stringify(
      { doc: { ...timelineDoc, palette: "Hot" }, rev: "r:000000000000", at: Date.now() }));
    await open();
    const offer = await screen.findByRole("alertdialog", { name: "unsaved changes" });
    expect(within(offer).getByText(/has been saved since/)).toBeInTheDocument();
    expect(within(offer).queryByRole("button", { name: "Restore them" })).toBeNull();
    expect(within(offer).getByRole("link", { name: "Download them" }).getAttribute("href"))
      .toMatch(/^data:application\/json/);
    await user.click(within(offer).getByRole("button", { name: "Discard" }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(localStorage.getItem("klights.draft.synth-128")).toBeNull();
  });

  it("undoes and redoes", async () => {
    const user = userEvent.setup();
    await open();
    const lanes = await screen.findByRole("region", { name: "lanes" });
    fireEvent.pointerDown(within(lanes).getByLabelText("fan-drop at bar 41.1").querySelector("rect")!);
    await user.click(screen.getByRole("button", { name: "tight" }));
    expect(within(lanes).getByText(/tight · color @primary/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Undo" }));
    expect(within(lanes).getByText(/wide · color @primary/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Redo" }));
    expect(within(lanes).getByText(/tight · color @primary/)).toBeInTheDocument();
  });

  it("drags a clip along the lane, snapped to the bar", async () => {
    await open();
    const lanes = await screen.findByRole("region", { name: "lanes" });
    const rect = within(lanes).getByLabelText("fan-drop at bar 41.1").querySelector("rect")!;
    fireEvent.pointerDown(rect, { clientX: 100, pointerId: 1 });
    fireEvent.pointerMove(rect, { clientX: 100 + 6 * 9, pointerId: 1 });   // 9 beats at 6 px
    fireEvent.pointerUp(rect, { clientX: 100 + 6 * 9, pointerId: 1 });
    expect(within(lanes).getByLabelText("fan-drop at bar 43.1")).toBeInTheDocument();
  });

  it("places a routine from the shelf, records hits, and lists every item", async () => {
    const user = userEvent.setup();
    await open();
    const lanes = await screen.findByRole("region", { name: "lanes" });
    await user.click(screen.getByRole("button", { name: "Idle orbit" }));
    expect(within(lanes).getAllByLabelText("idle-orbit at bar 1.1").length).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: "Record" }));
    await user.click(screen.getByRole("button", { name: "Strobe" }));
    expect(within(lanes).getByLabelText("strobe at bar 1.1")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "List" }));
    const list = screen.getByRole("contentinfo", { name: "event list" });
    expect(within(list).getAllByRole("row").length).toBeGreaterThan(10);
    await user.click(within(list).getByRole("button", { name: "bo1 on a bar" }));
    expect(within(lanes).getByLabelText("blackout at bar 41.4")).toBeInTheDocument();
  });

  it("drafts the scene lane from a template set, phrase by phrase", async () => {
    const user = userEvent.setup();
    await open();
    const lanes = await screen.findByRole("region", { name: "lanes" });
    await user.click(screen.getByRole("button", { name: "Club" }));
    await waitFor(() =>
      expect(within(lanes).getByLabelText("verse-sweep at bar 17.1")).toBeInTheDocument());
    // Chorus -> fan-drop "wide", per the template
    expect(within(lanes).getAllByLabelText("fan-drop at bar 41.1").length).toBeGreaterThan(0);
  });

  it("adds automation lanes and points, and edits a point's value and curve", async () => {
    const user = userEvent.setup();
    const socket = await open();
    const lanes = await screen.findByRole("region", { name: "lanes" });
    await user.selectOptions(within(lanes).getByLabelText("add automation"), "spread");
    const spread = within(lanes).getByLabelText("automation spread");
    fireEvent.click(spread, { clientX: 6 * 32, clientY: 10 });
    const point = within(spread).getByLabelText(/point at bar 9.1/);
    // a new point is selected, and the inspector edits it
    const inspector = screen.getByRole("contentinfo", { name: "inspector" });
    expect(within(inspector).getByText(/automation point on spread/)).toBeInTheDocument();
    await user.click(within(inspector).getByRole("button", { name: "ease" }));
    fireEvent.change(within(inspector).getByLabelText("point value"), { target: { value: "-0.5" } });
    expect(within(spread).getByLabelText("point at bar 9.1: -0.5")).toBeInTheDocument();

    // dragged a bar later (6 px a beat), as one edit
    fireEvent.pointerDown(within(spread).getByLabelText(/point at bar 9.1/), { clientX: 192, clientY: 20, pointerId: 1 });
    fireEvent.pointerMove(spread, { clientX: 192 + 24, clientY: 20, pointerId: 1 });
    fireEvent.pointerUp(spread, { clientX: 192 + 24, clientY: 20, pointerId: 1 });
    expect(within(spread).getByLabelText("point at bar 10.1: -0.5")).toBeInTheDocument();
    expect(point).toBeTruthy();

    // Delete removes what is selected; Ctrl+Z brings it back
    fireEvent.keyDown(document.body, { key: "Delete" });
    expect(within(spread).queryByLabelText(/point at bar 10.1/)).toBeNull();
    fireEvent.keyDown(document.body, { key: "z", ctrlKey: true });
    expect(within(spread).getByLabelText("point at bar 10.1: -0.5")).toBeInTheDocument();

    // and the curve went into the document
    await waitFor(() => expect(socket.sent.some((c) => c.type === "timeline_draft")).toBe(true),
                  { timeout: 2000 });
    const draft = [...socket.sent].reverse().find((c) => c.type === "timeline_draft") as
      unknown as { doc: TimelineDoc };
    const row = draft.doc.rows.find((r) => r.target === "spread")!;
    expect(row.points).toContainEqual([36, -0.5, "ease"]);
  });

  it("offers a lane for each parameter of the routines on the track, naming them", async () => {
    const user = userEvent.setup();
    await open();
    const lanes = await screen.findByRole("region", { name: "lanes" });
    const menu = within(lanes).getByLabelText("add automation");
    await waitFor(() => expect(within(menu).getByRole("option", {
      name: "$color · fan-drop, idle-orbit" })).toBeInTheDocument());
    expect(within(menu).getByRole("option", { name: "$radius (deg) · idle-orbit" }))
      .toBeInTheDocument();
    await user.selectOptions(menu, "param.radius");
    const radius = within(lanes).getByLabelText("automation param.radius");
    expect(within(radius).getByLabelText("point at bar 1.1: 10")).toBeInTheDocument();
  });

  it("answers the editing keys: Space plays, Ctrl+S saves, Escape lets go", async () => {
    const socket = await open();
    const lanes = await screen.findByRole("region", { name: "lanes" });
    fireEvent.keyDown(document.body, { key: " " });
    expect(screen.getByRole("button", { name: "Pause" })).toBeInTheDocument();
    fireEvent.keyDown(document.body, { key: " " });
    expect(screen.getByRole("button", { name: "Play" })).toBeInTheDocument();

    fireEvent.pointerDown(within(lanes).getByLabelText("fan-drop at bar 41.1").querySelector("rect")!);
    expect(screen.getByRole("contentinfo", { name: "inspector" })).toBeInTheDocument();
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(screen.queryByRole("contentinfo", { name: "inspector" })).toBeNull();

    fireEvent.pointerDown(within(lanes).getByLabelText("fan-drop at bar 41.1").querySelector("rect")!);
    fireEvent.keyDown(document.body, { key: "Backspace" });
    expect(within(lanes).queryByLabelText("fan-drop at bar 41.1")).toBeNull();
    fireEvent.keyDown(document.body, { key: "s", metaKey: true });
    const save = reply(socket, "timeline_save", true, { rev: "r:k" }) as unknown as
      { doc: TimelineDoc; base_rev: string };
    expect(save.doc.rows.find((r) => r.id === "scene")!.items!.some((i) => i.id === "chorus1"))
      .toBe(false);
  });

  it("flips a lane between filling gaps and owning the track", async () => {
    const user = userEvent.setup();
    await open();
    await screen.findByRole("region", { name: "lanes" });
    const toggles = screen.getAllByRole("button", { name: "fills gaps" });
    await user.click(toggles[0]!);
    expect(screen.getAllByRole("button", { name: "owns track" }).length).toBe(2);
  });
});

describe("routine editor", () => {
  type Saved = { doc: RoutineDoc; base_rev: string };

  it("goes back to the track it was opened from", async () => {
    const user = userEvent.setup();
    await open();
    const lanes = await screen.findByRole("region", { name: "lanes" });
    fireEvent.pointerDown(within(lanes).getByLabelText("fan-drop at bar 41.1").querySelector("rect")!);
    await user.click(screen.getByRole("link", { name: /Open routine/ }));
    await screen.findByLabelText("fan_sweep at bar 1.1");
    expect(screen.getByTitle("Back to synth-128")).toHaveAttribute("href", "#designer/synth-128");
  });

  it("is reached from the picker, and lays a routine out as lanes on roles", async () => {
    const user = userEvent.setup();
    await open("#designer");
    await user.click(await screen.findByRole("link", { name: "Fan sweep (drop)" }));
    const lanes = await screen.findByRole("region", { name: "lanes" });
    expect(within(lanes).getByLabelText("fan_sweep at bar 1.1")).toBeInTheDocument();
    expect(within(lanes).getByLabelText("chase at bar 1.1")).toBeInTheDocument();
    expect(within(lanes).getByLabelText("chase at bar 5.1")).toBeInTheDocument();
    expect(within(lanes).getByLabelText("strobe at bar 8.1")).toBeInTheDocument();
    // a routine's lane plays on a role; there is no gap mode in a routine
    expect(within(lanes).getByLabelText("p role")).toHaveValue("pins");
    expect(screen.queryByRole("button", { name: "fills gaps" })).toBeNull();
    expect(screen.getByLabelText("position")).toHaveTextContent("bar 1.1 of 8");
  });

  it("edits a block's arguments -- a value or a parameter -- and saves with the rev", async () => {
    const user = userEvent.setup();
    const socket = await open("#designer/routine/fan-drop");
    const lanes = await screen.findByRole("region", { name: "lanes" });
    const fan = within(lanes).getByLabelText("fan_sweep at bar 1.1").querySelector("rect")!;
    fireEvent.pointerDown(fan, { clientX: 10, pointerId: 1 });
    fireEvent.pointerUp(fan, { clientX: 10, pointerId: 1 });
    const inspector = screen.getByRole("contentinfo", { name: "inspector" });
    expect(within(inspector).getByLabelText("width from")).toHaveValue("$width");
    await user.selectOptions(within(inspector).getByLabelText("width from"), "");
    fireEvent.change(within(inspector).getByLabelText("width"), { target: { value: "25" } });
    await user.selectOptions(within(inspector).getByLabelText("sweep from"), "$width");
    await waitFor(() => expect(socket.sent.some((c) => c.type === "routine_draft")).toBe(true),
                  { timeout: 2000 });
    reply(socket, "routine_draft", true, { errors: [], warnings: [], problems: [] });
    expect(await screen.findByRole("button", { name: "valid" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save" }));
    const saved = reply(socket, "routine_save", true, { rev: "r:cccccccccccc" }) as unknown as Saved;
    expect(saved.base_rev).toBe(ROUTINE_REV);
    expect(saved.doc.rows[0]!.items![0]!.args).toEqual(
      { width: 25, bars: 4, spread: 0.5, rate: "$rate", sweep: "$width" });
    expect(await screen.findByRole("button", { name: "Saved" })).toBeInTheDocument();
  });

  it("adds blocks, roles, parameters and variations", async () => {
    const user = userEvent.setup();
    const socket = await open("#designer/routine/fan-drop");
    const lanes = await screen.findByRole("region", { name: "lanes" });
    await user.click(screen.getByRole("button", { name: "pulse" }));
    // on the routine's level lane, at the playhead, to the routine's end
    expect(within(lanes).getByLabelText("pulse at bar 1.1")).toBeInTheDocument();
    const inspector = screen.getByRole("contentinfo", { name: "inspector" });
    expect(within(inspector).getByLabelText("block")).toHaveValue("pulse");

    await user.type(screen.getByLabelText("+ role name"), "wash");
    await user.click(screen.getByRole("button", { name: "+ role" }));
    expect(screen.getByLabelText("wash tag")).toHaveValue("wash");
    expect(screen.getByRole("button", { name: "remove role movers" })).toBeDisabled();

    await user.type(screen.getByLabelText("+ parameter name"), "depth");
    await user.click(screen.getByRole("button", { name: "+ parameter" }));
    await user.selectOptions(within(inspector).getByLabelText("depth from"), "$depth");

    await user.type(screen.getByLabelText("+ variation name"), "soft");
    await user.click(screen.getByRole("button", { name: "+ variation" }));
    await user.click(screen.getByLabelText("soft sets depth"));
    fireEvent.change(screen.getByLabelText("soft depth"), { target: { value: "0.4" } });

    await user.click(screen.getByRole("button", { name: "Save" }));
    const { doc } = reply(socket, "routine_save", true, { rev: "r:d" }) as unknown as Saved;
    expect(doc.roles.wash).toEqual({ default: "wash" });
    expect(doc.params!.depth!.type).toBe("number");
    expect(doc.variations!.soft).toEqual({ depth: 0.4 });
    const level = doc.rows.find((r) => r.id === "p")!;
    expect(level.items!.find((i) => i.block === "pulse"))
      .toMatchObject({ at: 0, len: 32, args: { depth: "$depth" } });
  });

  it("automates its own parameters on lanes, held to their declared range", async () => {
    const user = userEvent.setup();
    const socket = await open("#designer/routine/fan-drop");
    const lanes = await screen.findByRole("region", { name: "lanes" });
    const menu = within(lanes).getByLabelText("add automation");
    // each open parameter, by name and unit -- and a look would not be here
    expect(within(menu).getByRole("option", { name: "$width (deg)" })).toBeInTheDocument();
    expect(within(menu).getByRole("option", { name: "$rate" })).toBeInTheDocument();
    await user.selectOptions(menu, "param.width");
    expect(within(menu).queryByRole("option", { name: "$width (deg)" })).toBeNull();

    // the lane starts at the default, so adding it changes nothing
    const width = within(lanes).getByLabelText("automation param.width");
    expect(within(width).getByLabelText("point at bar 1.1: 40")).toBeInTheDocument();
    expect(within(lanes).getByText("$width (deg)")).toBeInTheDocument();
    fireEvent.click(width, { clientX: 16 * 16, clientY: 3 });   // bar 5, the top
    expect(within(width).getByLabelText("point at bar 5.1: 120")).toBeInTheDocument();
    const inspector = screen.getByRole("contentinfo", { name: "inspector" });
    expect(within(inspector).getByText(/0 to 120 deg/)).toBeInTheDocument();
    // past the declared max is refused here, as the engine would refuse it
    fireEvent.change(within(inspector).getByLabelText("point value"), { target: { value: "500" } });
    expect(within(width).getByLabelText("point at bar 5.1: 120")).toBeInTheDocument();
    fireEvent.change(within(inspector).getByLabelText("point value"), { target: { value: "90" } });

    // a colour parameter's lane takes colours, picked in the inspector
    await user.selectOptions(menu, "param.color");
    const colour = within(lanes).getByLabelText("automation param.color");
    fireEvent.click(colour, { clientX: 8 * 16, clientY: 20 });
    expect(within(colour).getByLabelText("point at bar 3.1: @primary")).toBeInTheDocument();
    await user.click(within(screen.getByRole("group", { name: "point colour" }))
      .getByRole("button", { name: "accent" }));
    expect(within(colour).getByLabelText("point at bar 3.1: @accent")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Save" }));
    const { doc } = reply(socket, "routine_save", true, { rev: "r:f" }) as unknown as Saved;
    expect(doc.rows.find((r) => r.target === "param.width"))
      .toMatchObject({ type: "automation", points: [[0, 40], [16, 90]] });
    expect(doc.rows.find((r) => r.target === "param.color"))
      .toMatchObject({ points: [[0, "@primary"], [8, "@accent"]] });
  });

  it("starts a new routine and saves it as a new file", async () => {
    const user = userEvent.setup();
    const socket = await open("#designer");
    await user.type(await screen.findByLabelText("new routine id"), "Bad Name");
    expect(screen.getByRole("button", { name: "New routine" })).toBeDisabled();
    await user.clear(screen.getByLabelText("new routine id"));
    await user.type(screen.getByLabelText("new routine id"), "my-sweep");
    await user.click(screen.getByRole("button", { name: "New routine" }));
    const lanes = await screen.findByRole("region", { name: "lanes" });
    expect(screen.getByText("new")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "solid" }));
    expect(within(lanes).getByLabelText("solid at bar 1.1")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save" }));
    const saved = reply(socket, "routine_save", true, { rev: "r:e" }) as unknown as Saved;
    expect(saved.base_rev).toBe("");
    expect(saved.doc).toMatchObject({ kind: "klights.routine", id: "my-sweep", bars: 4 });
    expect(saved.doc.rows[0]).toMatchObject({ type: "clips", target: "color", role: "movers" });
    expect(saved.doc.rows[0]!.items![0]!.args).toEqual({ color: "@primary" });
  });

  it("gives a routine an OSC lane, so wherever it plays it can cue a VJ app", async () => {
    const user = userEvent.setup();
    await open("#designer/routine/fan-drop");
    await screen.findByRole("region", { name: "lanes" });
    await user.selectOptions(screen.getByLabelText("add lane"), "osc");
    await user.click(await screen.findByRole("button", { name: "add a cue to osc" }));
    const inspector = screen.getByRole("contentinfo", { name: "inspector" });
    expect(within(inspector).getByText(/osc cue on osc/)).toBeInTheDocument();
    expect(within(inspector).getByLabelText("on address"))
      .toHaveValue("/composition/layers/1/clips/1/connect");
    await user.click(within(inspector).getByRole("button", { name: "+ while message" }));
    expect(within(inspector).getByLabelText("while address")).toHaveValue("/");
  });

  it("shows what the engine says will not work on this rig", async () => {
    const user = userEvent.setup();
    const socket = await open("#designer/routine/fan-drop");
    await screen.findByRole("region", { name: "lanes" });
    await user.clear(screen.getByLabelText("routine name"));
    await waitFor(() => expect(socket.sent.some((c) => c.type === "routine_draft")).toBe(true),
                  { timeout: 2000 });
    reply(socket, "routine_draft", true, { errors: [], warnings: [], problems: [
      "routine 'fan-drop': role 'movers' ('movers') has no fixtures on this rig"] });
    await user.click(await screen.findByRole("button", { name: "1 note(s)" }));
    expect(screen.getByRole("dialog", { name: "draft check" }))
      .toHaveTextContent(/no fixtures on this rig/);
  });
});

describe("designer guide", () => {
  it("offers its guide once, and opens it beside the lanes rather than over them", async () => {
    const user = userEvent.setup();
    await open();
    const lanes = await screen.findByRole("region", { name: "lanes" });
    await user.click(screen.getByRole("button", { name: "Open the guide" }));
    const guide = screen.getByRole("complementary", { name: "guide" });
    expect(within(guide).getByText("Design a track's show")).toBeInTheDocument();
    // Learned by doing: the timeline is still there to do it on.
    expect(lanes).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open the guide" })).toBeNull();
    expect(localStorage.getItem("klights.guide.designer")).toBe("1");

    await user.click(within(guide).getByRole("button", { name: "Routine" }));
    expect(within(guide).getByText("Build a routine")).toBeInTheDocument();
    await user.click(within(guide).getByRole("button", { name: "close the guide" }));
    expect(screen.queryByRole("complementary", { name: "guide" })).toBeNull();
  });

  it("opens to the routine guide from the routine editor", async () => {
    const user = userEvent.setup();
    await open("#designer/routine/fan-drop");
    await screen.findByRole("region", { name: "lanes" });
    await user.click(screen.getByRole("button", { name: "Guide" }));
    expect(within(screen.getByRole("complementary", { name: "guide" }))
      .getByText("Build a routine")).toBeInTheDocument();
  });

  it("answers ? from the keyboard, but not while something is being typed", async () => {
    await open();
    await screen.findByRole("region", { name: "lanes" });
    fireEvent.keyDown(document.body, { key: "?" });
    expect(screen.getByRole("complementary", { name: "guide" })).toBeInTheDocument();
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(screen.queryByRole("complementary", { name: "guide" })).toBeNull();

    const zoom = screen.getByRole("combobox", { name: "zoom" });
    fireEvent.keyDown(zoom, { key: "?" });
    expect(screen.queryByRole("complementary", { name: "guide" })).toBeNull();
  });

  it("explains who drives each lane, where the answer is not obvious", async () => {
    const user = userEvent.setup();
    await open();
    await screen.findByRole("region", { name: "lanes" });
    await user.click(screen.getByRole("button", { name: "Help: At the playhead" }));
    expect(screen.getByText(/Higher lanes win/)).toBeInTheDocument();
  });
});
