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
import { AUTOMATION_RANGES } from "../designer/edit";
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

  it("lays the track out as lanes: phrases, clips, hits, automation, the VJ lane", async () => {
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
    expect(within(lanes).getByText(/Output: vj/)).toBeInTheDocument();
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
