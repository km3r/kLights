import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import App from "../App";
import {
  currentSocket, despacioState, installMockSocket, stateWith,
} from "./mockSocket";

/**
 * The UI, driven against a snapshot captured from a real despacio engine.
 *
 * Two kinds of assertion, kept apart on purpose:
 *   - given this state, does the UI show the right thing;
 *   - given this press, does it send the right command.
 * The UI is server-authoritative and never predicts, so conflating the two
 * would let a UI that renders its own optimistic guesses pass both.
 */

function mount() {
  installMockSocket();
  render(<App />);
  const socket = currentSocket();
  act(() => socket.open());
  return socket;
}

/**
 * Click a TAB, not just any button whose label happens to match.
 *
 * "Move" also appears as "Move changes" in the auto panel and inside look
 * names, so an unscoped query is ambiguous the moment the app grows. Scoping to
 * the tab bar is both unambiguous and closer to what a user does.
 */
async function goTo(user: ReturnType<typeof userEvent.setup>, label: RegExp) {
  const bar = document.querySelector("nav.tabbar") as HTMLElement;
  await user.click(within(bar).getByRole("button", { name: label }));
}

beforeEach(() => {
  localStorage.clear();
  // The app persists the current tab in the URL hash so a phone waking up comes
  // back where it was. jsdom keeps `location` across tests in a file, so
  // without this every test after the first starts on whichever tab the
  // previous one left open.
  location.hash = "";
});

describe("connection", () => {
  it("says hello with a name so presence is not anonymous", () => {
    installMockSocket();
    render(<App />);
    const socket = currentSocket();
    act(() => socket.open());
    expect(socket.sent[0]).toMatchObject({ type: "hello" });
    expect((socket.sent[0] as { name: string }).name).toBeTruthy();
  });

  it("warns that the rig is holding its last frame when the socket drops", () => {
    const socket = mount();
    expect(screen.queryByText(/Disconnected/)).toBeNull();
    act(() => socket.close());
    expect(screen.getByText(/holding its last frame/i)).toBeInTheDocument();
  });

  it("shows nothing but a wait message before the first state arrives", () => {
    installMockSocket();
    render(<App />);
    expect(screen.getByText(/Waiting for the engine/i)).toBeInTheDocument();
  });
});

describe("header", () => {
  it("renders the live tempo and event from the engine", () => {
    mount();
    expect(screen.getByText(/despacio/)).toBeInTheDocument();
    expect(screen.getByText(despacioState.clock.effective_bpm.toFixed(1)))
      .toBeInTheDocument();
  });

  it("sends master level while dragging", async () => {
    const socket = mount();
    const fader = screen.getByLabelText(/master/i, { selector: "input" });
    act(() => {
      Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype, "value")!.set!.call(fader, "0.42");
      fader.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(socket.last()).toEqual({ type: "master", value: 0.42 });
  });

  it("toggles blackout against the CURRENT server state, not a local guess", async () => {
    const user = userEvent.setup();
    const socket = mount();
    await user.click(screen.getByRole("button", { name: /^blackout$/i }));
    expect(socket.last()).toEqual({ type: "blackout", on: true });

    act(() => socket.push(stateWith((s) => { s.blackout = true; })));
    await user.click(screen.getByRole("button", { name: /blackout on/i }));
    expect(socket.last()).toEqual({ type: "blackout", on: false });
  });

  it("distinguishes blackout from panic in words, not just in colour", () => {
    const socket = mount();
    act(() => socket.push(stateWith((s) => { s.blackout = true; s.panicked = true; })));
    // Blackout leaves the show running underneath; panic bypasses it entirely.
    expect(screen.getByText(/still running underneath/i)).toBeInTheDocument();
    expect(screen.getByText(/not being evaluated at all/i)).toBeInTheDocument();
  });

  it("offers to release a panic from the banner, wherever you are", async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(stateWith((s) => { s.panicked = true; })));
    // The panic BUTTON now lives on Setup -- it is not something to have under
    // a thumb next to the master -- but releasing must stay one tap from
    // anywhere, so the banner carries it.
    await user.click(screen.getByRole("button", { name: /^release$/i }));
    expect(socket.last()).toEqual({ type: "clear_panic" });
  });

  it("keeps panic off the header, where blackout is enough", () => {
    mount();
    const header = document.querySelector(".header") as HTMLElement;
    expect(within(header).getByRole("button", { name: /blackout/i })).toBeInTheDocument();
    expect(within(header).queryByRole("button", { name: /^panic/i })).toBeNull();
  });
});

describe("banners", () => {
  it("warns loudly that jog has bypassed the safety taper", () => {
    // The fixture has Moving Head #1 jogging, which is the state nobody should
    // be in without knowing.
    mount();
    const banner = screen.getByText(/safety taper is\s+bypassed/i);
    expect(banner).toBeInTheDocument();
    expect(banner.textContent).toContain("Moving Head #1");
    expect(banner.textContent).toMatch(/empty room/i);
  });

  it("explains a dark rig when the master is down", () => {
    const socket = mount();
    act(() => socket.push(stateWith((s) => { s.master = 0; s.blackout = false; })));
    expect(screen.getByText(/Master is at zero/i)).toBeInTheDocument();
  });

  it("does not cry wolf when everything is fine", () => {
    const socket = mount();
    act(() => socket.push(stateWith((s) => {
      s.fixtures.forEach((f) => { f.jogging = false; });
      s.panicked = false; s.blackout = false; s.master = 0.9;
      s.stats.drops = 0; s.last_error = null;
    })));
    expect(document.querySelectorAll(".banner")).toHaveLength(0);
  });

  it("surfaces dropped frames as a machine problem", () => {
    const socket = mount();
    act(() => socket.push(stateWith((s) => { s.stats.drops = 3; })));
    expect(screen.getByText(/3 dropped frame/i)).toBeInTheDocument();
  });
});

/**
 * The three slots are the heart of the reorganisation: colour, movement and
 * level are picked on their own tabs and do not disturb each other. These tests
 * guard that separation, since it is invisible until it breaks.
 */
describe("slots", () => {
  it("shows what each slot holds, and they are all filled at once", () => {
    mount();
    // The fixture has a move, a colour AND a level chase loaded together --
    // which was impossible while one selection replaced the whole show.
    const now = screen.getByText(/On now/i).closest(".card")!;
    expect(now.textContent).toContain("Lazy Circle");
    expect(now.textContent).toContain("MH Red");
    expect(now.textContent).toContain("Spotlight");
  });

  it("offers only movement looks on the Move tab", async () => {
    const user = userEvent.setup();
    mount();
    await goTo(user, /Move/);
    const card = screen.getByText(/^Route$/).closest(".card")! as HTMLElement;
    // 'MH Red' is a colour: it must not be reachable from here, or picking a
    // route could clobber the colour.
    await user.type(within(card).getByLabelText(/filter movement/), "MH Red");
    expect(within(card).queryByRole("button", { name: /^MH Red/ })).toBeNull();
  });

  it("offers only colour looks on the Color tab", async () => {
    const user = userEvent.setup();
    mount();
    await goTo(user, /Color/);
    const card = screen.getByText("Colour look").closest(".card")! as HTMLElement;
    await user.type(within(card).getByLabelText(/filter color/), "Lazy Circle");
    expect(within(card).queryByRole("button", { name: /^Lazy Circle/ })).toBeNull();
  });

  it("selects into a slot and can clear it again", async () => {
    const user = userEvent.setup();
    const socket = mount();
    await goTo(user, /Bright/);
    const card = screen.getByText("Bright pattern").closest(".card")! as HTMLElement;
    await user.click(within(card).getByRole("button", { name: /^Clear/ }));
    expect(socket.last()).toEqual({ type: "clear_slot", slot: "level" });
  });

  it("files a chase's own steps under the chase, not beside it", async () => {
    const user = userEvent.setup();
    mount();
    await goTo(user, /Bright/);
    const card = screen.getByText("Bright pattern").closest(".card")! as HTMLElement;
    // 'Spotlight Step 1' is one step of the 'Spotlight' chase. Four of them
    // beside the chase itself is the flat-list problem in miniature.
    await user.type(within(card).getByLabelText(/filter level/), "Spotlight Step");
    expect(within(card).queryByRole("button", { name: /^Spotlight Step 1/ })).toBeNull();
    await user.click(within(card).getByRole("button", { name: /individual chase step/i }));
    expect(within(card).getByRole("button", { name: /Spotlight Step 1/ }))
      .toBeInTheDocument();
  });
});

describe("presets", () => {
  it("recalls a whole picture, and names what it holds", async () => {
    const user = userEvent.setup();
    const socket = mount();
    const card = screen.getByText(/^Presets/).closest(".card")! as HTMLElement;
    // The name appears twice: once as a recall tile, once in the delete list.
    // The tile is first.
    await user.click(within(card).getAllByRole("button", { name: /^peak/ })[0]!);
    expect(socket.last()).toEqual({ type: "preset_apply", name: "peak" });
  });

  it("saves the current picture under a typed name", async () => {
    const user = userEvent.setup();
    const socket = mount();
    await user.type(screen.getByLabelText("preset name"), "drop");
    await user.click(screen.getByRole("button", { name: /^Save$/ }));
    expect(socket.last()).toEqual({ type: "preset_save", name: "drop" });
  });

  it("will not save an unnamed preset", () => {
    mount();
    expect(screen.getByRole("button", { name: /^Save$/ })).toBeDisabled();
  });
});

describe("show tab", () => {
  it("offers to release a held look", async () => {
    const user = userEvent.setup();
    const socket = mount();
    await user.click(screen.getByRole("button", { name: /release hold/i }));
    expect(socket.last()).toEqual({ type: "release" });
  });

  it("selects a look by name", async () => {
    const user = userEvent.setup();
    const socket = mount();
    await goTo(user, /Move/);
    await user.click(screen.getByRole("button", { name: /^Slow Sweep/ }));
    expect(socket.last()).toEqual({ type: "select_look", name: "Slow Sweep" });
  });

  // 206 looks came out of the workspace. A flat list that long is exactly why
  // only a handful got used, so they split by slot and group by what they do.
  it("groups the ported library by kind", async () => {
    const user = userEvent.setup();
    mount();
    await goTo(user, /Move/);
    expect(screen.getByRole("button", { name: /Moves · \d+/ })).toBeInTheDocument();
    await goTo(user, /Color/);
    expect(screen.getByRole("button", { name: /Colours · \d+/ })).toBeInTheDocument();
  });

  it("filters across every group, not just the open one", async () => {
    const user = userEvent.setup();
    mount();
    await goTo(user, /Move/);
    // 'Heads - Ball' is a pose; the first group open by default is Moves.
    expect(screen.queryByRole("button", { name: /^Heads - Ball/ })).toBeNull();
    await user.type(screen.getByLabelText("filter movement looks"), "Heads - Ball");
    expect(screen.getByRole("button", { name: /^Heads - Ball/ })).toBeInTheDocument();
  });

  it("says so when a filter matches nothing", async () => {
    const user = userEvent.setup();
    mount();
    await goTo(user, /Move/);
    await user.type(screen.getByLabelText("filter movement looks"), "zzzz");
    expect(screen.getByText(/Nothing matches/)).toBeInTheDocument();
  });

  it("taps tempo", async () => {
    const user = userEvent.setup();
    const socket = mount();
    await user.click(screen.getByRole("button", { name: /TAP/ }));
    expect(socket.last()).toEqual({ type: "tap" });
  });

  it("sets speed without touching tempo", async () => {
    const user = userEvent.setup();
    const socket = mount();
    const speeds = screen.getAllByRole("button", { name: "0.5×" });
    await user.click(speeds[0]!);
    expect(socket.last()).toEqual({ type: "speed", value: 0.5 });
  });

  it("says that changes land on bars when phrase is only counted", () => {
    const socket = mount();
    act(() => socket.push(stateWith((s) => {
      s.clock.phrase_measured = false;
      s.auto.axes.look_changes = true;
    })));
    expect(screen.getByText(/counted from\s+your last downbeat/i))
      .toBeInTheDocument();
    expect(screen.getByText(/land on bars instead/i)).toBeInTheDocument();
  });

  it("does not nag about counting when phrase is measured", () => {
    const socket = mount();
    act(() => socket.push(stateWith((s) => {
      s.clock.phrase_measured = true;
      s.auto.axes.look_changes = true;
    })));
    expect(screen.queryByText(/land on bars instead/i)).toBeNull();
  });

  it("toggles auto axes one at a time", async () => {
    const user = userEvent.setup();
    const socket = mount();
    await user.click(screen.getByRole("button", { name: /Move changes/i }));
    expect(socket.last()).toEqual({ type: "auto", axis: "look_changes", on: true });
    // The fixture already has palette on, so this must turn it OFF.
    await user.click(screen.getByRole("button", { name: /^Palette/i }));
    expect(socket.last()).toEqual({ type: "auto", axis: "palette", on: false });
  });
});

describe("colour", () => {
  async function openColor(user: ReturnType<typeof userEvent.setup>) {
    await goTo(user, /Color/);
  }

  it("applies a palette colour to the chosen target", async () => {
    const user = userEvent.setup();
    const socket = mount();
    await openColor(user);
    await user.click(screen.getByRole("button", { name: "pinspots" }));
    await user.click(screen.getByLabelText("palette 3"));
    expect(socket.last()).toMatchObject({ type: "color", target: "pinspots" });
  });

  it("defaults to everything, so a colour is never silently scoped", async () => {
    const user = userEvent.setup();
    const socket = mount();
    await openColor(user);
    await user.click(screen.getByLabelText("palette 1"));
    expect(socket.last()).toMatchObject({ target: "all" });
  });

  it("clears an override rather than overwriting it with white", async () => {
    const user = userEvent.setup();
    const socket = mount();
    await openColor(user);
    // The fixture has an override on pinspots.
    await user.click(screen.getByRole("button", { name: "pinspots" }));
    await user.click(within(screen.getByText("Quick palette").closest(".card") as HTMLElement)
        .getByRole("button", { name: /^clear$/i }));
    expect(socket.last()).toMatchObject({ type: "color", target: "pinspots", clear: true });
  });
});

describe("move tab", () => {
  it("shows where each beam lands and why it is dimmed", async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(stateWith((s) => {
      const mh = s.fixtures.find((f) => f.name === "Moving Head #2")!;
      mh.safety = { taper: 0.5, reason: "beam core is in the crowd head band (held at 50%)" };
      mh.lands_on = "floor";
      mh.throw_mm = 6480;
    })));
    await goTo(user, /Move/);

    // Scoped to this head's card: with the real library loaded, several heads
    // legitimately share a taper reason, so a page-wide query is ambiguous.
    const card = screen.getByText("Moving Head #2").closest(".fixture")!;
    const scoped = within(card as HTMLElement);
    expect(scoped.getByText(/beam core is in the crowd head band/i))
      .toBeInTheDocument();
    expect(scoped.getByText(/floor/)).toBeInTheDocument();
    expect(scoped.getByText("50%")).toBeInTheDocument();
    expect(card.textContent).toContain("6.5 m");
  });
});

describe("rig info (now at the bottom of Setup)", () => {
  it("surfaces what the engine is NOT driving", async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(stateWith((s) => {
      s.warnings = ["YeeSite bar: 90 channels claim role 'dimmer' -- only the first is driven"];
    })));
    await goTo(user, /Setup/);
    expect(screen.getByText(/only the first is driven/)).toBeInTheDocument();
  });

  it("reports engine health honestly", async () => {
    const user = userEvent.setup();
    mount();
    await goTo(user, /Setup/);
    expect(screen.getByText("40.00 fps")).toBeInTheDocument();
    expect(screen.getByText("0 drop(s)")).toBeInTheDocument();
  });
});

describe("setup tab", () => {
  const openSetup = (user: ReturnType<typeof userEvent.setup>) => goTo(user, /Setup/);

  it("computes capture targets from the head's own position", async () => {
    const user = userEvent.setup();
    const socket = mount();
    await openSetup(user);
    // The adjacent corners — the ones ~90 degrees either side of the ball, and
    // the only targets that span enough bearing for the solver to derive
    // handedness. Derived from the fixture rather than hardcoded: the room's
    // dimensions are a property of the venue file and changed once already, and
    // a test that pins them fails for a reason that is not a bug.
    const head = despacioState.fixtures.find((f) => f.name === "Moving Head #1")!;
    const [hx, , hz] = head.position!;
    const { width: w, depth: d } = despacioState.venue;
    await user.click(screen.getByRole("button", { name: /corner across/ }));
    expect(socket.last()).toMatchObject({
      type: "capture", fixture: "Moving Head #1", target: [hx, 0, d! - hz],
    });
    await user.click(screen.getByRole("button", { name: /corner along wall/ }));
    expect(socket.last()).toMatchObject({ target: [w! - hx, 0, hz] });
  });

  it("captures the ball at the venue's actual ball position", async () => {
    const user = userEvent.setup();
    const socket = mount();
    await openSetup(user);
    await user.click(screen.getByRole("button", { name: /^Ball$/ }));
    expect(socket.last()).toMatchObject({
      type: "capture", target: despacioState.venue.ball,
    });
  });

  it("will not capture a head that is not being jogged", async () => {
    const user = userEvent.setup();
    // Moving Head #2 is not in the fixture's jog set. A capture records where
    // the head IS, and that number does not exist until it has been jogged --
    // capturing anyway used to record (0, 0), which the solver then fitted into
    // a calibration with a large residual and no other complaint.
    const socket = mount();
    await openSetup(user);
    await user.click(screen.getByRole("button", { name: /MH #2/ }));

    expect(screen.getByRole("button", { name: /^Ball$/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /corner across/ })).toBeDisabled();
    expect(screen.getByText(/nothing to record/)).toBeInTheDocument();

    const before = socket.sent.length;
    await user.click(screen.getByRole("button", { name: /^Ball$/ }));
    expect(socket.sent.length).toBe(before);

    // Jogging it opens the capture up again.
    await user.click(screen.getAllByRole("button", { name: "+10" })[0]!);
    expect(socket.last()).toMatchObject({ type: "jog", fixture: "Moving Head #2" });
  });

  it("jogs by the requested step", async () => {
    const user = userEvent.setup();
    const socket = mount();
    await openSetup(user);
    await user.click(screen.getAllByRole("button", { name: "+10" })[0]!);
    expect(socket.last()).toMatchObject({
      type: "jog", fixture: "Moving Head #1", pan: 138,
    });
  });

  it("shows who else is connected and what they did", async () => {
    const user = userEvent.setup();
    mount();
    await openSetup(user);
    const peers = document.querySelector(".presence")!;
    expect(within(peers as HTMLElement).getByText("tablet")).toBeInTheDocument();
    expect(peers.textContent).toContain("coloured pinspots");
  });

  it("shows the solver's own words when captures disagree", async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(stateWith((s) => {
      s.notices = ["Moving Head #1: captures disagree by 17.1 deg RMS"];
    })));
    await openSetup(user);
    expect(screen.getByText(/captures disagree by 17.1 deg RMS/))
      .toBeInTheDocument();
  });

  it("flags a head that has drifted", async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(stateWith((s) => {
      s.drift = [
        { head: "Moving Head #1", bearing: 0.1, elevation: 0.2, significant: false },
        { head: "Moving Head #3", bearing: -4.2, elevation: 18.9, significant: true },
      ];
    })));
    await openSetup(user);
    expect(screen.getByText("MOVED")).toBeInTheDocument();
    expect(screen.getByText(/\+18\.90° elev/)).toBeInTheDocument();
  });
});

describe("venue (now part of Setup)", () => {
  async function openVenue(user: ReturnType<typeof userEvent.setup>) {
    await goTo(user, /Setup/);
  }

  it("states plainly what the crowd level buys", async () => {
    const user = userEvent.setup();
    const socket = mount();
    await openVenue(user);
    // The despacio policy is 0.5 — a glare guard, not a hard guarantee.
    expect(screen.getByText(/glare guard, not a hard optical-safety guarantee/i))
      .toBeInTheDocument();

    act(() => socket.push(stateWith((s) => { s.taper.crowd_level = 0; })));
    expect(screen.getByText(/costs every floor-sweep pose/i)).toBeInTheDocument();
  });

  it("sends the crowd level as it is dragged", async () => {
    const user = userEvent.setup();
    const socket = mount();
    await openVenue(user);
    const slider = screen.getByLabelText("crowd level");
    act(() => {
      Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype, "value")!.set!.call(slider, "0.25");
      slider.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(socket.last()).toEqual({ type: "taper", crowd_level: 0.25 });
  });

  it("shouts when the taper is switched off entirely", async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(stateWith((s) => { s.taper.enabled = false; })));
    await openVenue(user);
    expect(screen.getByText(/no longer dimmed over the crowd/i))
      .toBeInTheDocument();
  });

  it("edits the head band, committing once rather than per keystroke", async () => {
    const user = userEvent.setup();
    const socket = mount();
    await openVenue(user);
    const field = screen.getByLabelText(/Head band min/i);
    await user.clear(field);
    await user.type(field, "1600");

    // Nothing sent yet: a field that committed mid-typing would fire on "1",
    // "16", "160" and then fight the echo on every one.
    expect(socket.commands.some((c) => c.type === "venue")).toBe(false);

    await user.tab();
    expect(socket.last()).toMatchObject({
      type: "venue", crowd: { head_band_min: 1600 },
    });
    expect(socket.commands.filter((c) => c.type === "venue")).toHaveLength(1);
  });

  it("commits on Enter too, for a phone keyboard's done key", async () => {
    const user = userEvent.setup();
    const socket = mount();
    await openVenue(user);
    const field = screen.getByLabelText(/Canopy.*Radius|Radius/i);
    await user.clear(field);
    await user.type(field, "2500{Enter}");
    expect(socket.last()).toMatchObject({ type: "venue", canopy: { radius: 2500 } });
  });

  it("ignores a cleared field rather than committing zero", async () => {
    const user = userEvent.setup();
    const socket = mount();
    await openVenue(user);
    const field = screen.getByLabelText(/Head band max/i);
    await user.clear(field);
    await user.tab();
    expect(socket.commands.some((c) => c.type === "venue")).toBe(false);
  });

  it("toggles the canopy, which decides whether aerials glow or overshoot", async () => {
    const user = userEvent.setup();
    const socket = mount();
    await openVenue(user);
    await user.click(screen.getByRole("button", { name: /Canopy rigged/i }));
    expect(socket.last()).toEqual({ type: "venue", canopy: { enabled: false } });
  });

  it("saves to venue.json only when asked", async () => {
    const user = userEvent.setup();
    const socket = mount();
    await openVenue(user);
    expect(socket.commands.some((c) => c.type === "venue_save")).toBe(false);
    await user.click(screen.getByRole("button", { name: /Save to venue.json/i }));
    expect(socket.last()).toEqual({ type: "venue_save" });
  });
});

describe("navigation", () => {
  it("keeps the tab in the URL so a reload lands where you were", async () => {
    const user = userEvent.setup();
    mount();
    await goTo(user, /Setup/);
    expect(location.hash).toBe("#setup");
  });
});

describe("patch", () => {
  const openSetup = async (user: ReturnType<typeof userEvent.setup>) =>
    goTo(user, /Setup/);

  it("is locked until you say otherwise", async () => {
    const user = userEvent.setup();
    mount();
    await openSetup(user);
    // Locked: the summary shows, the editing controls do not. A re-addressed
    // rig is a walk around the room with a torch, unlike every other control
    // on this surface.
    expect(screen.getByRole("button", { name: /Unlock to edit/i })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^Autopatch$/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /Add fixture/i })).toBeNull();
  });

  it("reveals a row per fixture once unlocked", async () => {
    const user = userEvent.setup();
    mount();
    await openSetup(user);
    await user.click(screen.getByRole("button", { name: /Unlock to edit/i }));
    expect(screen.getByLabelText("Moving Head #1 address")).toBeTruthy();
    expect(screen.getByLabelText("Pinspot #2 tags")).toBeTruthy();
    expect(screen.getByRole("button", { name: /^Autopatch$/i })).toBeTruthy();
  });

  it("sends an address change on blur, not per keystroke", async () => {
    const user = userEvent.setup();
    const socket = mount();
    await openSetup(user);
    await user.click(screen.getByRole("button", { name: /Unlock to edit/i }));

    const field = screen.getByLabelText("Pinspot #1 address");
    await user.clear(field);
    await user.type(field, "300");
    // Bound straight to the server this would have sent 3, then 30, then 300 --
    // the first two of which are real addresses that clash with the movers.
    expect(socket.commands.some((c) => c.type === "patch_address")).toBe(false);

    await user.tab();
    expect(socket.last()).toEqual({
      type: "patch_address", name: "Pinspot #1", address: 300,
    });
  });

  it("does not send anything when the address is unchanged", async () => {
    const user = userEvent.setup();
    const socket = mount();
    await openSetup(user);
    await user.click(screen.getByRole("button", { name: /Unlock to edit/i }));
    const field = screen.getByLabelText("Pinspot #1 tags");
    await user.click(field);
    await user.tab();
    expect(socket.commands.some((c) => c.type === "patch_tags")).toBe(false);
  });

  it("says so, permanently, when a saved patch is not the running one", async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(stateWith((s) => { s.pending_patch = true; })));
    await openSetup(user);
    expect(screen.getByText(/still running the rig it last loaded/i)).toBeTruthy();
  });

  it("applies a pending patch without a restart", async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(stateWith((s) => { s.pending_patch = true; })));
    await openSetup(user);
    await user.click(screen.getByRole("button", { name: /Apply now/i }));
    expect(socket.last()).toEqual({ type: "patch_apply" });
  });
});

describe("shape macros", () => {
  const openMove = async (user: ReturnType<typeof userEvent.setup>) =>
    goTo(user, /Move/);

  it("shows the identity values when nothing is dialled in", async () => {
    const user = userEvent.setup();
    mount();
    await openMove(user);
    expect(screen.getByText("1.00×")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Reset/i })).toHaveProperty(
      "disabled", true);
  });

  it("sends size live while dragging, not on release", async () => {
    const user = userEvent.setup();
    const socket = mount();
    await openMove(user);
    const size = screen.getByLabelText("Size") as HTMLInputElement;
    // fireEvent.change, not a hand-dispatched input event: React keeps its own
    // value tracker and ignores a native event whose value it did not set.
    // These are performance controls — watching the rig respond is how you find
    // the value you want — so this must fire during the drag, not on release.
    fireEvent.change(size, { target: { value: "2" } });
    expect(socket.last()).toEqual({ type: "macro", size: 2 });
  });

  it("offers a reset once anything is off identity", async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(stateWith((s) => {
      s.macro = { size: 1.8, spread: 0.25, center: [0, -40] };
    })));
    await openMove(user);
    const reset = screen.getByRole("button", { name: /Reset/i });
    expect(reset).toHaveProperty("disabled", false);
    await user.click(reset);
    expect(socket.last()).toEqual({ type: "macro", reset: true });
  });

  it("renders the engine's values rather than its own", async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(stateWith((s) => {
      s.macro = { size: 0.5, spread: 1, center: [90, -30] };
    })));
    await openMove(user);
    expect((screen.getByLabelText("Size") as HTMLInputElement).value).toBe("0.5");
    expect((screen.getByLabelText("Spread") as HTMLInputElement).value).toBe("1");
    expect(screen.getByText(/90° round/)).toBeTruthy();
    expect(screen.getByText(/-30° up\/down/)).toBeTruthy();
  });
});

describe("strobe policy", () => {
  it("states the limits where strobe is visible", async () => {
    const user = userEvent.setup();
    mount();
    await goTo(user, /Bright/);
    expect(screen.getByText(/75%/)).toBeTruthy();
    expect(screen.getByText(/8s/)).toBeTruthy();
    // The honesty that matters: this is not a frequency limit, and saying it is
    // would be inventing rigour the fixture profiles cannot support.
    expect(screen.getByText(/Not a frequency limit/i)).toBeTruthy();
  });

  it("says so loudly when nothing is limiting it", async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(stateWith((s) => {
      s.strobe_policy = { enabled: true, ceiling: 1, max_seconds: 0 };
    })));
    await goTo(user, /Bright/);
    expect(screen.getByText(/UNLIMITED/)).toBeTruthy();
  });

  it("reports a fully blocked shutter", async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(stateWith((s) => {
      s.strobe_policy = { enabled: false, ceiling: 1, max_seconds: 0 };
    })));
    await goTo(user, /Bright/);
    expect(screen.getByText(/Blocked entirely/i)).toBeTruthy();
  });
});
