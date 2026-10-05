import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
import { resetCatalogue } from "../designer/Collection";
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

/** What `prep.py catalogue` says, cut down: a folder, a playlist in it with
 *  rekordbox's trailing space, a smart playlist, and one track of each kind. */
const CATALOGUE = {
  kind: "klights.rekordbox_catalogue", db: "collection:TEST",
  path: "C:/rekordbox/master.db", rekordbox: "7.2.14", read_at: "2026-10-03T07:39:22",
  playlists: [
    { id: "10", name: "Gigs", parent: null, kind: "folder", tracks: [] },
    { id: "11", name: "Friday ", parent: "10", kind: "playlist", tracks: [101, 102] },
    { id: "13", name: "Smart", parent: null, kind: "smart", tracks: [] },
  ],
  tracks: [
    { id: 101, title: "synthetic 128", artist: "kLights", album: "", genre: "", key: "",
      bpm: 128, duration_s: 360, local: true, analysed: true, added: "2026-01-01" },
    { id: 102, title: "Night Drive", artist: "Kölsch", album: "Night EP", genre: "", key: "8A",
      bpm: 124, duration_s: 400, local: true, analysed: true, added: "2026-01-02" },
    { id: 103, title: "Raw Demo", artist: "Someone", album: "", genre: "", key: "",
      bpm: null, duration_s: 200, local: true, analysed: false, added: "2026-01-03" },
    { id: 104, title: "Streamed Tune", artist: "Streamer", album: "", genre: "", key: "",
      bpm: 128, duration_s: 300, local: false, analysed: true, added: "2026-01-04" },
  ],
};
let rekordbox: [number, unknown] = [200, CATALOGUE];

const PHRASE_ITEMS = trackDoc.phrases.items as [number, number, string][];

/** A prepped track read back: the example track under another name. */
function trackAs(id: string, title: string, artist: string) {
  return { ...structuredClone(trackDoc), id, identity: { ...trackDoc.identity, title, artist } };
}

function serve(path: string): [number, unknown] {
  if (path === "/api/tracks") {
    return [200, { tracks: [
      { id: "synth-128", title: "synthetic 128", artist: "kLights",
        bpm: 128, phrases: 8, has_timeline: true, grid_rev: "g:834af7",
        has_waveform: false, has_audio: false, phrase_items: PHRASE_ITEMS,
        timeline: { rows: 7, items: 13, grid_rev: "g:834af7" }, edited: 2,
        rekordbox: [{ db: "collection:TEST", id: 101 }], signatures: 1 },
      // From another collection: not this rekordbox's row 102, so the browser
      // still offers it.
      { id: "kolsch-night-drive", title: "Night Drive", artist: "Kölsch", bpm: 124,
        phrases: 8, has_timeline: false, grid_rev: "g:834af7", has_waveform: false,
        has_audio: true, audio_here: false, phrase_items: PHRASE_ITEMS, timeline: null,
        edited: 1, rekordbox: [{ db: "xml:OTHER", id: 7 }], signatures: 0 },
    ] }];
  }
  if (path === "/api/show") {
    return [200, { dir: "/shows", rev: "r:s", show: { kind: "klights.show", template_set: "club" },
                   errors: [], warnings: [] }];
  }
  if (path === "/api/tracks/kolsch-night-drive") {
    return [200, { doc: trackAs("kolsch-night-drive", "Night Drive", "Kölsch"), rev: "r:k" }];
  }
  if (path === "/api/tracks/streamer-streamed-tune") {
    return [200, { doc: trackAs("streamer-streamed-tune", "Streamed Tune", "Streamer"), rev: "r:st" }];
  }
  if (path === "/api/rekordbox") return rekordbox;
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
  sessionStorage.clear();
  resetCatalogue();
  rekordbox = [200, CATALOGUE];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const [status, body] = serve(new URL(url, "http://engine").pathname);
    return { ok: status === 200, status, json: async () => body };
  }));
});
afterEach(() => {
  vi.unstubAllGlobals();
  location.hash = "";
});

async function open(hash = "#studio/track/synth-128") {
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
    expect(screen.getByTitle("Back to synth-128")).toHaveAttribute("href", "#studio/track/synth-128");
  });

  it("is reached from Studio's routines, and lays a routine out as lanes on roles", async () => {
    const user = userEvent.setup();
    await open("#studio/routines");
    await user.click(await screen.findByRole("link", { name: /Fan sweep \(drop\)/ }));
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
    const socket = await open("#studio/routine/fan-drop");
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
    const socket = await open("#studio/routine/fan-drop");
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
    const socket = await open("#studio/routines");
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
    const socket = await open("#studio/routine/fan-drop");
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
    await open("#studio/routine/fan-drop");
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

type TSaved = { doc: TimelineDoc; base_rev: string };

describe("studio routes", () => {
  it("rewrites the old designer's addresses to Studio's", async () => {
    await open("#designer");
    expect(location.hash).toBe("#studio");
    expect(await screen.findByRole("region", { name: "tracks" })).toBeInTheDocument();
  });

  it("takes an old link to a track or a routine to the same place", async () => {
    await open("#designer/synth-128");
    expect(location.hash).toBe("#studio/track/synth-128");
    expect(await screen.findByRole("region", { name: "lanes" })).toBeInTheDocument();
    cleanup();
    await open("#designer/routine/fan-drop");
    expect(location.hash).toBe("#studio/routine/fan-drop");
  });
});

describe("studio library", () => {
  it("lists the show folder's tracks with what lights each on the night", async () => {
    await open("#studio");
    const tracks = await screen.findByRole("region", { name: "tracks" });
    const row = (await within(tracks).findByText("synthetic 128")).closest("tr")!;
    expect(within(row).getByText("Timeline")).toBeInTheDocument();
    const second = within(tracks).getByText("Night Drive").closest("tr")!;
    // No timeline: the operator's show runs. Template sets do not play live
    // yet (F19 milestone 2), so the library must not say they do.
    expect(within(second).getByText("Operator's show")).toBeInTheDocument();
    // No CDJ signature is something to fix before the night.
    expect(within(second).getByRole("img", { name: /No CDJ signature/ })).toBeInTheDocument();
    expect(within(tracks).getByText(/2 in the show folder\. 1 has a timeline/)).toBeInTheDocument();
  });

  it("filters by what a track needs, and by name", async () => {
    const user = userEvent.setup();
    await open("#studio");
    const tracks = await screen.findByRole("region", { name: "tracks" });
    await within(tracks).findByText("synthetic 128");
    await user.click(within(tracks).getByRole("button", { name: /No timeline/ }));
    expect(within(tracks).queryByText("synthetic 128")).toBeNull();
    expect(within(tracks).getByText("Night Drive")).toBeInTheDocument();
    await user.click(within(tracks).getByRole("button", { name: /^All/ }));
    await user.type(within(tracks).getByLabelText("filter tracks"), "kolsch");
    expect(within(tracks).queryByText("synthetic 128")).toBeNull();
    expect(within(tracks).getByText("Night Drive")).toBeInTheDocument();
  });

  it("shows the selected track's details, and opens its timeline", async () => {
    const user = userEvent.setup();
    await open("#studio");
    const tracks = await screen.findByRole("region", { name: "tracks" });
    await user.click(await within(tracks).findByRole("button", { name: /Night Drive/ }));
    const details = screen.getByRole("complementary", { name: "details" });
    expect(within(details).getByRole("heading", { name: "Night Drive" })).toBeInTheDocument();
    expect(within(details).getByText(/when it plays the operator's show runs/)).toBeInTheDocument();
    expect(within(details).getByRole("link", { name: "Make a timeline" }))
      .toHaveAttribute("href", "#studio/track/kolsch-night-drive");
    // The engine is asked whether it can find the audio; the mock has none.
    expect(await within(details).findByText(/No audio file on this machine/)).toBeInTheDocument();
  });

  it("folds its side panels away, and remembers that", async () => {
    const user = userEvent.setup();
    await open("#studio");
    await screen.findByRole("region", { name: "tracks" });
    expect(screen.getByRole("navigation", { name: "Studio" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Hide the sidebar" }));
    expect(screen.queryByRole("navigation", { name: "Studio" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Hide the details" }));
    expect(screen.queryByRole("complementary", { name: "details" })).toBeNull();
    expect(JSON.parse(localStorage.getItem("klights.studio.panels")!))
      .toMatchObject({ nav: false, side: false });
    cleanup();
    await open("#studio");
    await screen.findByRole("region", { name: "tracks" });
    expect(screen.queryByRole("navigation", { name: "Studio" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Show the sidebar" }));
    expect(screen.getByRole("navigation", { name: "Studio" })).toBeInTheDocument();
  });

  it("folds the timeline's side panel away, so the lanes take the width", async () => {
    const user = userEvent.setup();
    await open();
    await screen.findByRole("region", { name: "lanes" });
    expect(screen.getByRole("complementary", { name: "side panel" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Hide the side panel" }));
    expect(screen.queryByRole("complementary", { name: "side panel" })).toBeNull();
    expect(document.querySelector(".d-body")).toHaveClass("d-no-side");
  });

  it("drafts a track from a set when its timeline opens, as an undoable unsaved edit", async () => {
    const user = userEvent.setup();
    await open("#studio");
    const tracks = await screen.findByRole("region", { name: "tracks" });
    await user.click(await within(tracks).findByRole("button", { name: /synthetic 128/ }));
    const details = screen.getByRole("complementary", { name: "details" });
    await user.click(within(details).getByRole("button", { name: "Redraft from" }));
    expect(location.hash).toBe("#studio/track/synth-128");
    expect(await screen.findByText(/Drafted from Club\. Nothing is saved/)).toBeInTheDocument();
    const lanes = screen.getByRole("region", { name: "lanes" });
    // Club plays Idle orbit on the Intro, where the saved timeline has a
    // snapshot: one routine per phrase, from the track's own phrases.
    expect(within(lanes).getByLabelText("idle-orbit at bar 1.1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Undo" }));
    expect(within(lanes).queryByLabelText("idle-orbit at bar 1.1")).toBeNull();
  });

  it("makes timelines for ticked tracks that have none, drafted from a set", async () => {
    const user = userEvent.setup();
    const socket = await open("#studio");
    const tracks = await screen.findByRole("region", { name: "tracks" });
    await user.click(await within(tracks).findByLabelText("tick Night Drive"));
    await user.click(within(tracks).getByLabelText("tick synthetic 128"));
    // synthetic 128 has a timeline already: only one is offered.
    await user.click(within(tracks).getByRole("button", { name: "Make timelines for 1…" }));
    const dialog = screen.getByRole("dialog", { name: "Make timelines for 1 track" });
    await user.click(within(dialog).getByRole("button", { name: "Start 1" }));
    await waitFor(() => expect(socket.sent.some((c) => c.type === "timeline_save")).toBe(true));
    const saved = reply(socket, "timeline_save", true, { rev: "r:n" }) as unknown as TSaved;
    expect(saved.base_rev).toBe("");
    expect(saved.doc).toMatchObject({ kind: "klights.timeline", track: "kolsch-night-drive",
                                      grid_rev: "g:834af7" });
    const scene = saved.doc.rows.find((r) => r.target === "scene")!;
    expect(scene.items!.map((i) => i.routine)).toEqual([
      "idle-orbit", "verse-sweep", "build-rise", "fan-drop", "idle-orbit", "build-rise",
      "fan-drop", "idle-orbit"]);
    expect(await within(dialog).findByText(/drafted 8 clips from Club/)).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "Open" }))
      .toHaveAttribute("href", "#studio/track/kolsch-night-drive");
  });

  it("lists routines and starts a new one from its own page", async () => {
    const user = userEvent.setup();
    await open("#studio/routines");
    const page = await screen.findByRole("region", { name: "routines" });
    expect(await within(page).findByRole("link", { name: /Fan sweep \(drop\)/ }))
      .toHaveAttribute("href", "#studio/routine/fan-drop");
    expect(within(page).getByText("$color $width $rate")).toBeInTheDocument();
    await user.click(screen.getByRole("link", { name: /^Tracks/ }));
    expect(await screen.findByRole("region", { name: "tracks" })).toBeInTheDocument();
  });
});

describe("rekordbox collection", () => {
  async function browse(hash = "#studio/rekordbox") {
    const user = userEvent.setup();
    const socket = await open(hash);
    const sidebar = screen.getByRole("navigation", { name: "Studio" });
    await user.click(within(sidebar).getByRole("button", { name: "Browse rekordbox" }));
    const region = await screen.findByRole("region", { name: "rekordbox collection" });
    await within(region).findByRole("table");
    return { user, socket, region, sidebar };
  }

  it("is read only when asked: a big collection is a megabyte", async () => {
    await open("#studio");
    await screen.findByText("synthetic 128");
    const asked = (vi.mocked(fetch).mock.calls as unknown as [string][])
      .map(([url]) => new URL(url, "http://engine").pathname);
    expect(asked).not.toContain("/api/rekordbox");
  });

  it("lists every track, and says which are in the show, unanalysed or streamed", async () => {
    const { region, sidebar } = await browse();
    const table = within(region).getByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(5);   // header + 4
    expect(within(table).getByRole("link", { name: "open synthetic 128" }))
      .toHaveAttribute("href", "#studio/track/synth-128");
    expect(within(table).getByLabelText("tick Raw Demo")).toBeDisabled();
    expect(within(table).getByText("not analysed")).toBeInTheDocument();
    expect(within(table).getByText("streaming")).toBeInTheDocument();
    expect(within(sidebar).getByLabelText("smart Smart")).toHaveAttribute("aria-disabled", "true");
    const coverage = screen.getByRole("region", { name: "coverage" });
    expect(within(coverage).getByText("Own timeline").closest("div")).toHaveTextContent("1");
  });

  it("browses folder, then playlist, then its tracks; and searches the way the matcher compares", async () => {
    const { user, sidebar } = await browse();
    const tree = within(sidebar).getByRole("navigation", { name: "playlists" });
    expect(within(tree).queryByLabelText("playlist Friday")).toBeNull();
    await user.click(within(tree).getByLabelText("folder Gigs"));
    await user.click(within(tree).getByLabelText("playlist Friday"));
    expect(location.hash).toBe("#studio/rekordbox/11");
    const region = await screen.findByRole("region", { name: "rekordbox collection" });
    expect(within(region).getByRole("heading", { name: "Friday" })).toBeInTheDocument();
    const table = within(region).getByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(3);
    expect(within(table).getByText("Night Drive")).toBeInTheDocument();
    expect(within(table).queryByText("Streamed Tune")).toBeNull();
    await user.type(within(region).getByLabelText("search rekordbox"), "kolsch night");
    expect(within(table).getAllByRole("row")).toHaveLength(2);
    expect(within(table).getByText("Night Drive")).toBeInTheDocument();
  });

  it("adds the ticked tracks to the show and drafts each a timeline in one go", async () => {
    const { user, socket, region } = await browse();
    const add = within(region).getByRole("button", { name: /to the show/ });
    expect(add).toBeDisabled();
    await user.click(within(region).getByLabelText("tick Night Drive"));
    await user.click(within(region).getByLabelText("tick Streamed Tune"));
    await user.click(within(region).getByRole("button", { name: "Add 2 to the show" }));
    const dialog = screen.getByRole("dialog", { name: "Add 2 tracks to the show" });
    expect(within(dialog).getByRole("button", { name: /drafted from a template set/ }))
      .toHaveAttribute("aria-pressed", "true");
    await user.click(within(dialog).getByRole("button", { name: "Add 2 to the show" }));
    const sent = reply(socket, "rekordbox_prep", true, {
      results: [{ status: "created", track_id: "kolsch-night-drive", rekordbox_ids: [102],
                  title: "Night Drive", artist: "Kölsch", signature: true, notes: [] },
                { status: "created", track_id: "streamer-streamed-tune", rekordbox_ids: [104],
                  title: "Streamed Tune", artist: "Streamer", signature: false,
                  notes: ["a streaming track: there is no file for the designer to play"] }],
      skipped: [] }) as Command & { ids: number[] };
    expect(sent.ids).toEqual([102, 104]);
    // Each new track is drafted and saved as a new file, one after the other.
    await waitFor(() => expect(socket.sent.filter((c) => c.type === "timeline_save")).toHaveLength(1));
    reply(socket, "timeline_save", true, { rev: "r:1" });
    await waitFor(() => expect(socket.sent.filter((c) => c.type === "timeline_save")).toHaveLength(2));
    const second = reply(socket, "timeline_save", true, { rev: "r:2" }) as unknown as TSaved;
    expect(second.doc.track).toBe("streamer-streamed-tune");
    const progress = within(dialog).getByRole("list", { name: "progress" });
    await waitFor(() => expect(within(progress).getAllByText(/drafted 8 clips from Club/)).toHaveLength(2));
    expect(progress).toHaveTextContent(/no CDJ signature/);
    expect(progress).toHaveTextContent(/no file for the designer to play/);
    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(within(region).getByRole("button", { name: "Add to the show" })).toBeDisabled();
  });

  it("can just add tracks, with no timeline yet", async () => {
    const { user, socket, region } = await browse();
    await user.click(within(region).getByLabelText("tick Night Drive"));
    await user.click(within(region).getByRole("button", { name: "Add 1 to the show" }));
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: /Just add it/ }));
    await user.click(within(dialog).getByRole("button", { name: "Add 1 to the show" }));
    reply(socket, "rekordbox_prep", true, {
      results: [{ status: "created", track_id: "kolsch-night-drive", rekordbox_ids: [102],
                  title: "Night Drive", artist: "Kölsch", signature: true, notes: [] }],
      skipped: [] });
    expect(await within(dialog).findByText(/in the show, no timeline yet/)).toBeInTheDocument();
    expect(socket.sent.some((c) => c.type === "timeline_save")).toBe(false);
  });

  it("keeps the ticks when the dialog is cancelled", async () => {
    const { user, region } = await browse();
    await user.click(within(region).getByLabelText("tick Night Drive"));
    await user.click(within(region).getByRole("button", { name: "Add 1 to the show" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(within(region).getByLabelText("tick Night Drive")).toBeChecked();
    // Escape closes it too, and keeps them as well.
    await user.click(within(region).getByRole("button", { name: "Add 1 to the show" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(within(region).getByLabelText("tick Night Drive")).toBeChecked();
  });

  it("starts only the tracks the prep created: one ticked again is only re-prepped", async () => {
    const { user, socket, region } = await browse();
    await user.click(within(region).getByLabelText("tick Night Drive"));
    await user.click(within(region).getByLabelText("tick Streamed Tune"));
    await user.click(within(region).getByRole("button", { name: "Add 2 to the show" }));
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Add 2 to the show" }));
    reply(socket, "rekordbox_prep", true, {
      results: [{ status: "updated", track_id: "kolsch-night-drive", rekordbox_ids: [102],
                  title: "Night Drive", artist: "Kölsch", signature: true, notes: [] },
                { status: "created", track_id: "streamer-streamed-tune", rekordbox_ids: [104],
                  title: "Streamed Tune", artist: "Streamer", signature: false, notes: [] }],
      skipped: [] });
    await waitFor(() => expect(socket.sent.filter((c) => c.type === "timeline_save")).toHaveLength(1));
    const saved = reply(socket, "timeline_save", true, { rev: "r:1" }) as unknown as TSaved;
    expect(saved.doc.track).toBe("streamer-streamed-tune");
    expect(await within(dialog).findByText(/already in the show, so left as it was/)).toBeInTheDocument();
  });

  it("shows what the engine said when it cannot prep", async () => {
    const { user, socket, region } = await browse();
    await user.click(within(region).getByLabelText("tick Night Drive"));
    await user.click(within(region).getByRole("button", { name: "Add 1 to the show" }));
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Add 1 to the show" }));
    reply(socket, "rekordbox_prep", false, undefined,
          "a prep from rekordbox is already running; wait for it");
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("already running");
  });

  it("says why when rekordbox cannot be read, in the bridge's words", async () => {
    rekordbox = [503, { error: "master.db is encrypted and no key was given: set RB_CIPHER_KEY" }];
    const user = userEvent.setup();
    await open("#studio/rekordbox");
    const page = await screen.findByRole("region", { name: "rekordbox collection" });
    await user.click(within(page).getByRole("button", { name: "Browse rekordbox" }));
    expect((await screen.findAllByRole("alert"))[0]).toHaveTextContent("set RB_CIPHER_KEY");
  });

  it("finds a track by name when the console sends someone to add it", async () => {
    await open("#studio/rekordbox?find=Night%20Drive");
    const region = await screen.findByRole("region", { name: "rekordbox collection" });
    const table = await within(region).findByRole("table");
    expect(within(region).getByLabelText("search rekordbox")).toHaveValue("Night Drive");
    expect(within(table).getAllByRole("row")).toHaveLength(2);
  });
});
