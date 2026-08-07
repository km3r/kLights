import { act, render, screen, within } from "@testing-library/react";
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

beforeEach(() => localStorage.clear());

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
    await user.click(screen.getByRole("button", { name: /^blackout$/i }));
    expect(socket.last()).toEqual({ type: "blackout", on: false });
  });

  it("offers to clear a panic rather than re-panicking", async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(stateWith((s) => { s.panicked = true; })));
    await user.click(screen.getByRole("button", { name: /panicked/i }));
    expect(socket.last()).toEqual({ type: "clear_panic" });
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

describe("show tab", () => {
  it("marks the running look and offers to release the hold", async () => {
    const user = userEvent.setup();
    const socket = mount();
    // The fixture is held on 'sweep'.
    expect(screen.getByRole("button", { name: /^sweep$/ }).className)
      .toContain("on");
    expect(screen.getByText(/Held/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /release hold/i }));
    expect(socket.last()).toEqual({ type: "release" });
  });

  it("selects a look by name", async () => {
    const user = userEvent.setup();
    const socket = mount();
    await user.click(screen.getByRole("button", { name: /^drift$/ }));
    expect(socket.last()).toEqual({ type: "select_look", name: "drift" });
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
    await user.click(screen.getByRole("button", { name: /Look changes/i }));
    expect(socket.last()).toEqual({ type: "auto", axis: "look_changes", on: true });
    // The fixture already has palette on, so this must turn it OFF.
    await user.click(screen.getByRole("button", { name: /^Palette/i }));
    expect(socket.last()).toEqual({ type: "auto", axis: "palette", on: false });
  });
});

describe("colour", () => {
  async function openColor(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getByRole("button", { name: /Color/ }));
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
    await user.click(screen.getByRole("button", { name: /^clear$/i }));
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
    await user.click(screen.getByRole("button", { name: /Move/ }));

    expect(screen.getByText(/beam core is in the crowd head band/i))
      .toBeInTheDocument();
    expect(screen.getByText(/floor/)).toBeInTheDocument();
    expect(screen.getByText("50%")).toBeInTheDocument();
  });
});

describe("rig tab", () => {
  it("surfaces what the engine is NOT driving", async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(stateWith((s) => {
      s.warnings = ["YeeSite bar: 90 channels claim role 'dimmer' -- only the first is driven"];
    })));
    await user.click(screen.getByRole("button", { name: /Rig/ }));
    expect(screen.getByText(/only the first is driven/)).toBeInTheDocument();
  });

  it("reports engine health honestly", async () => {
    const user = userEvent.setup();
    mount();
    await user.click(screen.getByRole("button", { name: /Rig/ }));
    expect(screen.getByText("40.00 fps")).toBeInTheDocument();
    expect(screen.getByText("0 drop(s)")).toBeInTheDocument();
  });
});

describe("setup tab", () => {
  async function openSetup(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getByRole("button", { name: /Setup/ }));
  }

  it("computes capture targets from the head's own position", async () => {
    const user = userEvent.setup();
    const socket = mount();
    await openSetup(user);
    // Moving Head #1 is at (8644, 8644) in a 9144 mm room, so its adjacent
    // corners — the ones ~90 degrees either side of the ball — are at
    // (8644, 500) and (500, 8644).
    await user.click(screen.getByRole("button", { name: /corner across/ }));
    expect(socket.last()).toMatchObject({
      type: "capture", fixture: "Moving Head #1", target: [8644, 0, 500],
    });
    await user.click(screen.getByRole("button", { name: /corner along wall/ }));
    expect(socket.last()).toMatchObject({ target: [500, 0, 8644] });
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

describe("venue tab", () => {
  async function openVenue(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getByRole("button", { name: /Venue/ }));
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
    await user.click(screen.getByRole("button", { name: /Setup/ }));
    expect(location.hash).toBe("#setup");
  });
});
