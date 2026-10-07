/**
 * The console's small pure modules, each held to its own contract directly:
 * Studio's addresses (studioRoute.ts), the hand-off between Studio pages
 * (designer/pending.ts) and group names (groups.ts). They are reached through
 * the big component suites too, but only along the paths those happen to
 * take -- a stale link, a corrupt sessionStorage or a rig's new group name
 * are not among them.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { groupLabel } from "../groups";
import { PENDING_MS, clearPending, peekPending, putPending } from "../designer/pending";
import type { RoutineDoc } from "../designer/model";
import { ALL_TRACKS, fromDesignerHash, studioHref, studioRoute } from "../studioRoute";

describe("studioRoute: every address Studio answers to", () => {
  it("reads each documented address", () => {
    expect(studioRoute("#studio")).toEqual({ view: "tracks" });
    expect(studioRoute("#studio/routines")).toEqual({ view: "routines" });
    expect(studioRoute("#studio/templates")).toEqual({ view: "templates" });
    expect(studioRoute("#studio/templates/club")).toEqual({ view: "templates", id: "club" });
    expect(studioRoute("#studio/palettes")).toEqual({ view: "palettes" });
    expect(studioRoute("#studio/show")).toEqual({ view: "show" });
    expect(studioRoute("#studio/rekordbox")).toEqual({ view: "rekordbox", scope: ALL_TRACKS });
    expect(studioRoute("#studio/rekordbox/42")).toEqual({ view: "rekordbox", scope: "42" });
    expect(studioRoute("#studio/rekordbox?find=despa%20cito"))
      .toEqual({ view: "rekordbox", scope: ALL_TRACKS, find: "despa cito" });
    expect(studioRoute("#studio/track/synth-128")).toEqual({ view: "track", id: "synth-128" });
    expect(studioRoute("#studio/routine/fan-drop")).toEqual({ view: "routine", id: "fan-drop" });
  });

  it("is not Studio for the console's own tabs and guides", () => {
    for (const hash of ["", "#", "#show", "#setup", "#guide/show", "#visuals", "#studios", "#xstudio"]) {
      expect(studioRoute(hash), hash).toBeNull();
    }
  });

  it("lands an address it cannot use on the library, not on a broken page", () => {
    expect(studioRoute("#studio/track")).toEqual({ view: "tracks" });        // no id
    expect(studioRoute("#studio/routine/")).toEqual({ view: "tracks" });
    expect(studioRoute("#studio/nonsense/x")).toEqual({ view: "tracks" });
    expect(studioRoute("#studio/rekordbox?find=")).toEqual({ view: "rekordbox", scope: ALL_TRACKS });
  });

  it("rewrites every old #designer link to the Studio page it meant", () => {
    expect(fromDesignerHash("#designer")).toBe("#studio");
    expect(fromDesignerHash("#designer/synth-128")).toBe("#studio/track/synth-128");
    expect(fromDesignerHash("#designer/routine/fan-drop")).toBe("#studio/routine/fan-drop");
    expect(fromDesignerHash("#designer/routine")).toBe("#studio/routines");
    for (const hash of ["#studio", "#designers", "#design", "", "#show"]) {
      expect(fromDesignerHash(hash), hash).toBeNull();
    }
    // And each rewrite is an address Studio reads as that page.
    expect(studioRoute(fromDesignerHash("#designer/synth-128")!)).toEqual({ view: "track", id: "synth-128" });
  });

  it("links into Studio carry the token, so the new tab can save", () => {
    sessionStorage.setItem("klights.token", "s3cret");
    try {
      expect(studioHref()).toMatch(/\?token=s3cret#studio$/);
      expect(studioHref("track/synth-128")).toMatch(/\?token=s3cret#studio\/track\/synth-128$/);
    } finally {
      sessionStorage.removeItem("klights.token");
    }
    expect(studioHref("routines")).toMatch(/[^?]#studio\/routines$/);
  });
});

describe("pending: one Studio page handing a start to the next", () => {
  const routine = { id: "copy", name: "Copy" } as unknown as RoutineDoc;

  beforeEach(() => sessionStorage.clear());
  afterEach(() => vi.useRealTimers());

  it("is read back only by the page it was for", () => {
    putPending({ kind: "timeline", id: "synth-128", set: "club" });
    expect(peekPending("timeline", "other")).toBeNull();
    expect(peekPending("routine", "synth-128")).toBeNull();
    expect(peekPending("timeline", "synth-128")).toMatchObject({ set: "club" });
  });

  it("survives a second read -- React may run a page's setup twice -- until cleared", () => {
    putPending({ kind: "routine", id: "copy", doc: routine });
    expect(peekPending("routine", "copy")?.doc).toEqual(routine);
    expect(peekPending("routine", "copy")?.doc).toEqual(routine);
    clearPending("routine", "copy");
    expect(peekPending("routine", "copy")).toBeNull();
  });

  it("clearing for a different page leaves this one's start alone", () => {
    putPending({ kind: "timeline", id: "a" });
    clearPending("timeline", "b");
    clearPending("routine", "a");
    expect(peekPending("timeline", "a")).not.toBeNull();
  });

  it("expires after a minute, so a click whose page never opened does not surprise later", () => {
    vi.useFakeTimers();
    vi.setSystemTime(1_000_000);
    putPending({ kind: "timeline", id: "late" });
    vi.setSystemTime(1_000_000 + PENDING_MS);
    expect(peekPending("timeline", "late")).not.toBeNull();
    vi.setSystemTime(1_000_000 + PENDING_MS + 1);
    expect(peekPending("timeline", "late")).toBeNull();
    expect(sessionStorage.getItem("klights.studio.pending")).toBeNull();     // and is gone
  });

  it("treats whatever else is in storage as nothing pending, never as an error", () => {
    for (const junk of ["{not json", "null", "42", '"a string"', "[]",
                        '{"kind":"timeline","id":"x"}',                 // no time
                        '{"kind":"timeline","id":"x","at":"yesterday"}']) {
      sessionStorage.setItem("klights.studio.pending", junk);
      expect(() => peekPending("timeline", "x"), junk).not.toThrow();
      expect(peekPending("timeline", "x"), junk).toBeNull();
      expect(() => clearPending("timeline", "x"), junk).not.toThrow();
    }
  });

  it("opens plain, rather than failing, when storage itself refuses", () => {
    const set = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });
    const get = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    try {
      expect(() => putPending({ kind: "timeline", id: "x" })).not.toThrow();
      expect(peekPending("timeline", "x")).toBeNull();
      expect(() => clearPending("timeline", "x")).not.toThrow();
    } finally {
      set.mockRestore();
      get.mockRestore();
    }
  });
});

describe("groupLabel", () => {
  it("names the groups people say, and leaves a new rig's own groups as they are", () => {
    expect(groupLabel("corner movers")).toBe("Movers");
    expect(groupLabel("movers")).toBe("Movers");
    expect(groupLabel("pinspots")).toBe("Pinspots");
    expect(groupLabel("pars")).toBe("Pars");
    expect(groupLabel("bars")).toBe("Bars");
    expect(groupLabel("uplights")).toBe("uplights");
    expect(groupLabel("")).toBe("");
    // A tag that collides with an Object.prototype name is still just a tag.
    expect(groupLabel("constructor")).toBe("constructor");
    expect(groupLabel("toString")).toBe("toString");
  });
});
