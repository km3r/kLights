import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import App from "../App";
import { BLOCK_PARAMS } from "../blocks";
import type { EngineState } from "../types";
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
    // The panic BUTTON lives at the bottom of Show -- it is not something to
    // have under a thumb next to the master -- but releasing must stay one tap
    // from anywhere, so the banner carries it.
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

/**
 * Banks — the answer to "how do presets grow without the console getting worse".
 *
 * The fixture holds three: two on bank 1 (cells 0 and 3, so there is a gap) and
 * one on bank 2. That covers every case the grid has to render.
 */
describe("preset banks", () => {
  const card = () =>
    screen.getByText(/^Presets/).closest(".card")! as HTMLElement;

  it("draws empty pads, so a preset keeps its position as neighbours change", () => {
    mount();
    // Eight pads on the page whatever is saved. A grid that only rendered its
    // full cells would reflow on every save, which is the one thing a fixed
    // position is for.
    expect(within(card()).getAllByRole("button", { name: /^empty pad 1\./ }))
      .toHaveLength(6);
    // 'peak' is on cell 3, so pad 4 is taken and pads 2 and 3 are not.
    expect(within(card()).queryByLabelText("empty pad 1.4")).toBeNull();
    expect(within(card()).getByLabelText("empty pad 1.2")).toBeInTheDocument();
  });

  it("saves onto the pad you tapped, rather than wherever there is room", async () => {
    const user = userEvent.setup();
    const socket = mount();
    await user.click(within(card()).getByLabelText("empty pad 1.6"));
    await user.type(screen.getByLabelText("preset name"), "drop");
    await user.click(screen.getByRole("button", { name: /^Save$/ }));
    expect(socket.last()).toEqual({
      type: "preset_save", name: "drop", bank: 1, cell: 5,
    });
  });

  it("still saves without a pad chosen, and lets the engine place it", async () => {
    const user = userEvent.setup();
    const socket = mount();
    await user.type(screen.getByLabelText("preset name"), "drop");
    await user.click(screen.getByRole("button", { name: /^Save$/ }));
    expect(socket.last()).toEqual({ type: "preset_save", name: "drop" });
  });

  it("turns the page to a second bank", async () => {
    const user = userEvent.setup();
    mount();
    expect(within(card()).queryByRole("button", { name: /^landing/ })).toBeNull();
    await user.click(within(card()).getByLabelText("bank 2"));
    expect(within(card()).getByRole("button", { name: /^landing/ }))
      .toBeInTheDocument();
    expect(within(card()).queryByRole("button", { name: /^peak/ })).toBeNull();
  });

  it("hides the bank selector when there is only one bank", () => {
    installMockSocket();
    render(<App />);
    const socket = currentSocket();
    act(() => socket.open(stateWith((s) => {
      s.presets = s.presets.filter((p) => p.bank === 1);
      s.preset_banks = { size: 8, count: 1 };
    })));
    // A pager over a single page is a control that introduces a concept nobody
    // has met yet.
    expect(within(card()).queryByLabelText("bank 1")).toBeNull();
  });

  it("does not strand you on a bank that stopped existing", async () => {
    const user = userEvent.setup();
    const socket = mount();
    await user.click(within(card()).getByLabelText("bank 2"));
    expect(within(card()).getByRole("button", { name: /^landing/ }))
      .toBeInTheDocument();

    // Delete the last preset on bank 2 and the engine drops to one bank. The
    // selector only renders when there is more than one — so an unclamped bank
    // left the operator on an empty page with no control that goes back and
    // every preset unreachable short of reloading the console.
    act(() => socket.push(stateWith((s) => {
      s.presets = s.presets.filter((p) => p.bank === 1);
      s.preset_banks = { size: 8, count: 1 };
    })));
    expect(within(card()).getByRole("button", { name: /^opener/ }))
      .toBeInTheDocument();
    expect(within(card()).queryByLabelText("empty pad 2.1")).toBeNull();
  });

  it("finds every drop across banks by its tag", async () => {
    const user = userEvent.setup();
    mount();
    // 'landing' is on bank 2 and tagged ambient; the filter reaches it without
    // turning the page, which is the thing a bank cannot do.
    await user.click(within(card()).getByRole("button", { name: /^ambient$/ }));
    expect(within(card()).getByRole("button", { name: /^landing/ }))
      .toBeInTheDocument();
    expect(within(card()).queryByRole("button", { name: /^peak/ })).toBeNull();
  });

  it("moves a preset onto another pad in two taps", async () => {
    const user = userEvent.setup();
    const socket = mount();
    await user.click(within(card()).getByRole("button", { name: /^Edit$/ }));
    await user.click(within(card()).getByRole("button", { name: /^peak/ }));
    await user.click(within(card()).getByLabelText("empty pad 1.7"));
    expect(socket.last()).toEqual({
      type: "preset_move", name: "peak", bank: 1, cell: 6,
    });
  });

  it("keeps delete behind Edit rather than listing every preset twice", async () => {
    const user = userEvent.setup();
    const socket = mount();
    // The old card listed every preset a second time purely to delete it,
    // doubling the longest thing on the tab for the rarest action.
    expect(within(card()).queryByRole("button", { name: /^Delete/ })).toBeNull();
    await user.click(within(card()).getByRole("button", { name: /^Edit$/ }));
    await user.click(within(card()).getByRole("button", { name: /^peak/ }));
    await user.click(within(card()).getByRole("button", { name: /^Delete peak$/ }));
    expect(socket.last()).toEqual({ type: "preset_delete", name: "peak" });
  });

  it("sends tags on blur, not on every keystroke", async () => {
    const user = userEvent.setup();
    const socket = mount();
    await user.click(within(card()).getByRole("button", { name: /^Edit$/ }));
    await user.click(within(card()).getByRole("button", { name: /^peak/ }));
    const field = within(card()).getByLabelText("tags for peak");
    expect(field).toHaveValue("drop build");
    await user.clear(field);
    await user.type(field, "drop peak");
    expect(socket.last()).not.toMatchObject({ type: "preset_tag" });
    await user.tab();
    expect(socket.last()).toEqual({
      type: "preset_tag", name: "peak", tags: ["drop", "peak"],
    });
  });
});

/**
 * Perform vs Design — the answer to "how does the console stop growing".
 *
 * Every test above runs in Design, because jsdom reports a 1024px window with
 * no coarse pointer, which is a laptop. These are the ones that pin the phone.
 */
describe("perform mode", () => {
  function performing() {
    localStorage.setItem("klights.mode", "perform");
    return mount();
  }

  it("drops Setup from the tab bar, and keeps the four that make light", () => {
    performing();
    const bar = document.querySelector("nav.tabbar") as HTMLElement;
    expect(within(bar).queryByRole("button", { name: /Setup/ })).toBeNull();
    for (const t of [/Show/, /Color/, /Move/, /Bright/]) {
      expect(within(bar).getByRole("button", { name: t })).toBeInTheDocument();
    }
  });

  it("still reaches panic, which must never be behind a mode", () => {
    performing();
    // It moved off Setup for exactly this reason: Perform hides that tab, and a
    // rig you cannot force to zero from the surface in your hand is not a rig
    // anyone should be running.
    expect(screen.getByRole("button", { name: /^Panic/ })).toBeInTheDocument();
  });

  it("hides the readouts that change nothing", async () => {
    const user = userEvent.setup();
    performing();
    await goTo(user, /Bright/);
    expect(screen.queryByText("What each fixture is actually at")).toBeNull();
    // But the controls on the same tab are all still there.
    expect(screen.getByText("Dimmers")).toBeInTheDocument();
    expect(screen.getByText("Flash")).toBeInTheDocument();
  });

  it("shows the strobe warning anyway when there is no policy at all", async () => {
    const user = userEvent.setup();
    localStorage.setItem("klights.mode", "perform");
    installMockSocket();
    render(<App />);
    const socket = currentSocket();
    act(() => socket.open(stateWith((s) => {
      s.strobe_policy = { enabled: true, ceiling: 1, max_seconds: 0 };
    })));
    await goTo(user, /Bright/);
    // The card that says "you are capped" is reassurance and Perform drops it.
    // The card that says "nothing is capping this" is the reason the card
    // exists, and photosensitive epilepsy does not care what mode you are in.
    expect(screen.getByText(/UNLIMITED/)).toBeInTheDocument();
  });

  it("hides the reassuring version of the same card", async () => {
    const user = userEvent.setup();
    performing();
    await goTo(user, /Bright/);
    expect(screen.queryByText("Strobe policy")).toBeNull();
  });

  it("is one tap from the full console, never a lock", async () => {
    const user = userEvent.setup();
    performing();
    await user.click(screen.getByRole("button", { name: /^Perform$/ }));
    const bar = document.querySelector("nav.tabbar") as HTMLElement;
    expect(within(bar).getByRole("button", { name: /Setup/ })).toBeInTheDocument();
    expect(localStorage.getItem("klights.mode")).toBe("design");
  });

  it("comes back to Show rather than a tab with no button", async () => {
    const user = userEvent.setup();
    mount();
    await goTo(user, /Setup/);
    expect(screen.getByText("Who you are")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^Design$/ }));
    expect(screen.queryByText("Who you are")).toBeNull();
    expect(screen.getByText(/^Presets/)).toBeInTheDocument();
  });
});

/**
 * Finding #17: two lists that grew one 44px row per fixture, forever.
 */
describe("per-fixture lists", () => {
  it("puts groups first and individual heads behind a disclosure", async () => {
    const user = userEvent.setup();
    mount();
    await goTo(user, /Color/);
    const applies = screen.getByText("Applies to").closest(".card")! as HTMLElement;
    expect(within(applies).getByRole("button", { name: /^all$/ })).toBeInTheDocument();
    // A <details> renders its contents in the DOM either way, so the assertion
    // that means anything is whether it is OPEN — that is what decides how tall
    // the card is on a phone with forty heads patched.
    const disclosure = applies.querySelector("details")!;
    expect(disclosure.open).toBe(false);
    await user.click(within(applies).getByText(/One fixture at a time/));
    expect(disclosure.open).toBe(true);
    await user.click(within(applies).getByRole("button", { name: /^Moving Head #1/ }));
    // Still the same command it always was — this is a layout change, not a
    // behaviour one.
    await user.click(screen.getByLabelText("palette 0"));
    expect((currentSocket().last() as { target: string }).target)
      .toBe("Moving Head #1");
  });

  it("opens the disclosure by itself when a fixture is trimmed", async () => {
    const user = userEvent.setup();
    const socket = mount();
    await goTo(user, /Bright/);
    const details = () => screen.getByText("Dimmers").closest(".card")!
      .querySelector("details")!;
    expect(details().open).toBe(false);
    // A trim nobody can see is a fixture stuck dim with no explanation, so the
    // one case the collapsed list must not hide is a trim inside it.
    act(() => socket.push(stateWith((s) => {
      s.level_overrides = { "Moving Head #2": 0.3 };
    })));
    expect(details().open).toBe(true);
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

    // Scoped to this head's card, inside the Heads card: several heads
    // legitimately share a taper reason, and every fixture's name now also
    // appears in the plan view's SVG <title> tooltips at the top of the tab.
    const heads = screen.getByText("Heads").closest(".card")! as HTMLElement;
    const card = within(heads).getByText("Moving Head #2").closest(".fixture")!;
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

/**
 * The DJ sync row.
 *
 * Every test here is about a failure state, because the success state is a word
 * on a card and the failure states are what decide whether anyone trusts it.
 */
describe("follow dj (track card)", () => {
  const program = (edit: (p: NonNullable<EngineState["program"]>) => void = () => {}) =>
    stateWith((s) => {
      s.program = {
        armed: false, engaged: false, mode: "fallback", reason: "disarmed",
        beat: null, bar: null, lanes: {}, grabbed: [], policy: "idle",
        problems: 0, first_problem: null, latency_ms: { blt: 0 },
      };
      s.track = { state: "playing", title: "synthetic 128", artist: "kLights",
                  album: "test track", duration: 180, source: "blt", deck: "1",
                  time: 75, rate: 1, age: 0.02, track_seq: 1, jump_seq: 0,
                  on_air: true,
                  match: { track_id: "synth-128", via: "title_artist_album",
                           candidates: ["synth-128"], stale: false,
                           has_timeline: true },
                  grid_warning: null };
      edit(s.program!);
    });

  it("is not there without a show folder", () => {
    mount();
    expect(screen.queryByText("Track")).toBeNull();
  });

  it("starts SAFE, says why nothing is driving, and arms with one tap", async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(program()));
    expect(screen.getByText(/drives nothing until you arm it/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Follow SAFE" }));
    expect(socket.last()).toEqual({ type: "follow", armed: true });
  });

  it("links the matched track to the designer", () => {
    const socket = mount();
    act(() => socket.push(program()));
    expect(screen.getByRole("link", { name: "Open this track in the designer" }))
      .toHaveAttribute("href", "#designer/synth-128");
  });

  it("shows who has each lane, and grabs and releases them", async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(program((p) => {
      Object.assign(p, {
        armed: true, engaged: true, mode: "timeline", reason: null, beat: 161,
        bar: 41, grabbed: ["color"],
        lanes: { movement: "timeline", color: "operator", level: "timeline" },
      });
    })));
    expect(screen.getByRole("button", { name: "Follow ARMED" })).toBeInTheDocument();
    expect(screen.getByText(/Timeline driving/)).toBeInTheDocument();
    expect(screen.getByText("operator")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Release" }));
    expect(socket.last()).toEqual({ type: "program_release", slot: "color" });
    await user.click(screen.getAllByRole("button", { name: "Grab" })[0]!);
    expect(socket.last()).toEqual({ type: "program_grab", slot: "movement" });
  });

  it("says when the track's show has problems on this rig", () => {
    const socket = mount();
    act(() => socket.push(program((p) => {
      Object.assign(p, { armed: true, engaged: true, mode: "timeline",
                         reason: null, problems: 2,
                         first_problem: "look 'Nope' is not in this rig's library" });
    })));
    expect(screen.getByText(/2 problem\(s\) building this track's show/))
      .toBeInTheDocument();
  });

  it("sends a latency change for the playing source when the slider is let go", () => {
    const socket = mount();
    act(() => socket.push(program()));
    const slider = screen.getByRole("slider", { name: "Latency (blt)" });
    fireEvent.change(slider, { target: { value: "0.6" } });
    fireEvent.pointerUp(slider);
    expect(socket.last()).toEqual({ type: "show_latency", source: "blt", ms: 40 });
  });
});

describe("designer preview banner", () => {
  it("tells every console the designer is driving, and lets it be released", async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(stateWith((s) => {
      s.preview = { client: "c4", name: "laptop", track_id: "synth-128",
                    draft: true, playing: true, ready: true };
    })));
    expect(screen.getByText(/DESIGNER \(laptop\) is driving the rig/))
      .toBeInTheDocument();
    expect(screen.getByText(/unsaved draft/)).toBeInTheDocument();
    const banner = screen.getByText(/DESIGNER \(laptop\)/).closest(".banner")!;
    await user.click(within(banner as HTMLElement).getByRole("button",
                                                           { name: "Release" }));
    expect(socket.last()).toEqual({ type: "preview_release" });
  });

  it("tells a view-only phone too, without offering a Release it cannot do", () => {
    const socket = mount();
    act(() => socket.onmessage?.({ data: JSON.stringify({ type: "welcome", id: "v1",
                                                          tier: "view" }) }));
    act(() => socket.push(stateWith((s) => {
      s.preview = { client: "c4", name: "laptop", track_id: "synth-128",
                    draft: false, playing: true, ready: true };
    })));
    const banner = screen.getByText(/DESIGNER \(laptop\)/).closest(".banner")!;
    expect(within(banner as HTMLElement).queryByRole("button", { name: "Release" })).toBeNull();
  });
});

describe("dj sync", () => {
  const driving = (edit: (s: EngineState["sync"]) => void = () => {}) =>
    stateWith((s) => {
      s.sync = {
        listening: true, driving: true, age: 0.2, phrase: "Build",
        phrase_ends_in: 16, deck: "2", track: "Cosmic Slop",
      };
      s.clock.source = "prolink";
      edit(s.sync);
    });

  it("says nothing at all when no bridge is configured", () => {
    mount();
    // The fixture has the port closed, which is the normal case forever for
    // anyone who never sets this up. A console that nags about an unused
    // feature every night is a console people stop reading.
    expect(screen.queryByText("DJ sync")).toBeNull();
  });

  it("shows the port as open before anything has spoken through it", () => {
    const socket = mount();
    act(() => socket.push(stateWith((s) => {
      s.sync = { ...s.sync, listening: true };
    })));
    expect(screen.getByText("DJ sync")).toBeInTheDocument();
    expect(screen.getByText(/nothing has spoken through it yet/i))
      .toBeInTheDocument();
    expect(screen.getByRole("button", { name: /take over/i })).toBeDisabled();
  });

  it("reports locked, with the phrase and what is playing", () => {
    const socket = mount();
    act(() => socket.push(driving()));
    expect(screen.getByText("LOCKED")).toBeInTheDocument();
    expect(screen.getByText("Cosmic Slop")).toBeInTheDocument();
    expect(screen.getByText(/Build/)).toBeInTheDocument();
    // 16 beats is 4 bars — counted down here from an absolute beat the engine
    // holds, so a bridge can speak once a bar rather than once a beat.
    expect(screen.getByText(/4 bar\(s\) left/)).toBeInTheDocument();
  });

  it("says loudly when the bridge has gone quiet", () => {
    const socket = mount();
    act(() => socket.push(driving((s) => { s.age = 9; })));
    // THE failure this row exists for: the clock keeps free-running at the last
    // tempo with `source` still naming it, so the console looks locked while it
    // drifts away from a DJ nobody is listening to.
    expect(screen.getByText("NO SIGNAL")).toBeInTheDocument();
    expect(screen.getByText(/free-running on the tempo it left behind/i))
      .toBeInTheDocument();
  });

  it("keeps take-over one tap away, always", async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(driving()));
    await user.click(screen.getByRole("button", { name: /take over/i }));
    expect(socket.last()).toEqual({ type: "sync_off" });
  });

  it("names the track and where in it, once the transport has it", () => {
    const socket = mount();
    act(() => socket.push(stateWith((s) => {
      s.sync = { ...s.sync, listening: true, driving: true, age: 0.1,
                 track: "Night Drive" };
      s.track = { state: "playing", title: "Night Drive", artist: "Someone",
                  album: "EP", duration: 372.4, source: "rkbx", deck: "2",
                  time: 61.7, rate: 1.06, age: 0.02, track_seq: 1,
                  jump_seq: 0, on_air: null };
    })));
    expect(screen.getByText("Night Drive")).toBeInTheDocument();
    expect(screen.getByText(/— Someone/)).toBeInTheDocument();
    // Position against length, and the DJ's pitch: what you need to see to
    // believe a timeline is going to land where it should.
    expect(screen.getByText(/1:01 \/ 6:12 · playing · \+6\.0%/)).toBeInTheDocument();
  });

  it("says which prepped track it is, how it knows, and when a change applies", () => {
    const socket = mount();
    const playing = (match: object, grid_warning: object | null = null) =>
      stateWith((s) => {
        s.sync = { ...s.sync, listening: true, driving: true, age: 0.1 };
        s.track = { state: "playing", title: "Night Drive", artist: null,
                    album: null, duration: null, source: "blt", deck: "1",
                    time: 30, rate: 1, age: 0.02, track_seq: 3, jump_seq: 0,
                    on_air: true, match: match as never,
                    grid_warning: grid_warning as never };
      });
    act(() => socket.push(playing({ track_id: "night-drive", via: "signature",
                                    candidates: ["night-drive"], stale: false,
                                    has_timeline: true })));
    expect(screen.getByText(/· by signature · timeline/)).toBeInTheDocument();
    expect(screen.getByText("night-drive")).toBeInTheDocument();

    // Matched, but nobody has drawn it a show yet: worth knowing before arming.
    act(() => socket.push(playing({ track_id: "night-drive", via: "signature",
                                    candidates: ["night-drive"], stale: false,
                                    has_timeline: false })));
    expect(screen.getByText(/· by signature · no timeline/)).toBeInTheDocument();

    // A save or a manual link mid-song is not ignored -- it is waiting.
    act(() => socket.push(playing({ track_id: "night-drive", via: "title_artist",
                                    candidates: ["night-drive"], stale: true })));
    expect(screen.getByText(/folder changed, applies next play/)).toBeInTheDocument();

    act(() => socket.push(playing({ track_id: null, via: "ambiguous",
                                    candidates: ["dup-1", "dup-2"], stale: false })));
    expect(screen.getByText(/could be dup-1, dup-2 — not guessing/)).toBeInTheDocument();

    act(() => socket.push(playing({ track_id: null, via: "none", candidates: [],
                                    stale: false })));
    expect(screen.getByText("not in the show folder")).toBeInTheDocument();
  });

  it("warns when the deck's beats disagree with the prepped grid", () => {
    const socket = mount();
    act(() => socket.push(stateWith((s) => {
      s.sync = { ...s.sync, listening: true, driving: true, age: 0.1 };
      s.track = { state: "playing", title: "Night Drive", artist: null,
                  album: null, duration: null, source: "rkbx", deck: null,
                  time: 30, rate: 1, age: 0.02, track_seq: 3, jump_seq: 0,
                  on_air: null,
                  match: { track_id: "night-drive", via: "alias",
                           candidates: ["night-drive"], stale: false },
                  grid_warning: { kind: "phase", offset_beats: -0.5 } };
    })));
    expect(screen.getByText(/puts the beat 0.5 beat\(s\) behind the prepped grid/))
      .toBeInTheDocument();
  });

  it("says when the packets are late, rather than calling the deck stopped", () => {
    const socket = mount();
    act(() => socket.push(stateWith((s) => {
      s.sync = { ...s.sync, listening: true, driving: true, age: 0.4 };
      s.track = { state: "stalled", title: "Night Drive", artist: null,
                  album: null, duration: null, source: "rkbx", deck: null,
                  time: 75, rate: 1, age: 0.4, track_seq: 1, jump_seq: 2,
                  on_air: null };
    })));
    expect(screen.getByText(/1:15 · packets late/)).toBeInTheDocument();
  });

  it("distinguishes silence from a bridge it cannot read", () => {
    const socket = mount();
    act(() => socket.push(driving((s) => {
      s.port = { port: 9000, bind: "127.0.0.1", received: 0, rejected: 12,
                 last_reject: "127.0.0.1: b'\\x00'" };
    })));
    // "No bridge" and "a bridge sending something I cannot parse" look
    // identical from behind the console and have completely different fixes.
    expect(screen.getByText(/12 unreadable packet/i)).toBeInTheDocument();
  });
});

/**
 * The plan view — the previz that runs on the show laptop.
 *
 * Assertions are on the geometry, not on "it rendered": a plan that draws a
 * beam in the wrong place is worse than one that draws nothing, because it is
 * believable.
 */
describe("plan view", () => {
  const plan = async (user: ReturnType<typeof userEvent.setup>) => {
    await goTo(user, /Move/);
    return screen.getByLabelText("plan view of the room") as unknown as SVGSVGElement;
  };

  it("frames the whole room, whatever size it is", async () => {
    const user = userEvent.setup();
    mount();
    const svg = await plan(user);
    const box = svg.getAttribute("viewBox")!.split(" ").map(Number);
    const v = despacioState.venue;
    // Origin negative and extent bigger than the room: the margin is what stops
    // a fixture on the wall being drawn half outside the picture.
    expect(box[0]).toBeLessThan(0);
    expect(box[2]).toBeGreaterThan(v.width!);
    expect(box[3]).toBeGreaterThan(v.depth!);
  });

  it("draws each lit beam from its fixture to where it actually lands", async () => {
    const user = userEvent.setup();
    mount();
    const svg = await plan(user);
    // Whichever head is lit at the phase the fixture was captured at — naming
    // one hardcodes where the Spotlight chase happened to be that second, and
    // three of the four movers are legitimately dark in it.
    const mh = despacioState.fixtures.find(
      (f) => f.lands_at && (f.intensity ?? 0) > 0.001);
    if (!mh) throw new Error("the fixture has no lit beam to check");
    const beam = Array.from(svg.querySelectorAll("line")).find((l) =>
      Number(l.getAttribute("x1")) === mh.position![0]
      && Number(l.getAttribute("y1")) === mh.position![2]);
    if (!beam) throw new Error(`no beam drawn for ${mh.name}`);
    // x/z of the landing point, not x/y: this is a plan, and height is the
    // axis it cannot show.
    expect(Number(beam.getAttribute("x2"))).toBe(mh.lands_at![0]);
    expect(Number(beam.getAttribute("y2"))).toBe(mh.lands_at![2]);
  });

  it("draws a dark fixture without a beam", async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(stateWith((s) => {
      s.fixtures.forEach((f) => { f.intensity = 0; });
    })));
    const svg = await plan(user);
    expect(svg.querySelectorAll("line")).toHaveLength(0);
    // The fixtures are still there — a dark rig is not an empty room.
    expect(svg.querySelectorAll("circle").length).toBeGreaterThan(0);
  });

  it("clips a wash's footprint instead of painting over the room", async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(stateWith((s) => {
      const f = s.fixtures.find((x) => x.lands_at && (x.intensity ?? 0) > 0.001)!;
      // 120 degrees is legal, and is what half the fixtures in a club are.
      // Unclamped, `throw x tan(60°)` puts a disc wider than the room on top of
      // everything else in the drawing.
      f.beam_deg = 120;
      f.throw_mm = 12000;
    })));
    const svg = await plan(user);
    const room = Math.min(despacioState.venue.width!, despacioState.venue.depth!);
    const radii = Array.from(svg.querySelectorAll("circle"))
      .map((c) => Number(c.getAttribute("r")));
    expect(Math.max(...radii)).toBeLessThan(room * 0.4);
    // And it says so IN THE LIST rather than only in a tooltip, for the same
    // reason the landing surface is written out there: a phone has no hover.
    const list = document.querySelector(".plan-landings") as HTMLElement;
    expect(within(list).getByText(/drawn clipped/)).toBeInTheDocument();
  });

  it("says how many fixtures it is NOT showing", async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(stateWith((s) => {
      s.fixtures.slice(0, 2).forEach((f) => { delete f.position; });
    })));
    await plan(user);
    // The dangerous failure is silent omission: a plan missing two fixtures
    // still looks like a complete plan.
    expect(screen.getByText(/2 fixture\(s\) have no position/i))
      .toBeInTheDocument();
  });

  it("rings a tapered beam rather than only dimming it", async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(stateWith((s) => {
      s.fixtures.forEach((f) => {
        if (f.safety) f.safety = { taper: 0.5, reason: "over the crowd" };
      });
    })));
    await plan(user);
    // A beam at 50% because the operator pulled it down and a beam at 50%
    // because it is over someone's head are different facts, and opacity alone
    // cannot tell them apart.
    expect(screen.getByText(/ringed in amber/i)).toBeInTheDocument();
  });

  it("names each landing surface in TEXT, because a phone has no hover", async () => {
    const user = userEvent.setup();
    // Perform mode, which is where this matters: the Heads card that also
    // reports landings is Design-only, and the SVG <title> tooltips need a
    // pointer that a phone does not have.
    localStorage.setItem("klights.mode", "perform");
    const socket = mount();
    act(() => socket.push(stateWith((s) => {
      const mh = s.fixtures.find((f) => f.name === "Moving Head #1")!;
      mh.intensity = 0.9;
      mh.lands_on = "ceiling";
      mh.lands_at = [4000, 6900, 4000];
    })));
    await goTo(user, /Move/);
    // Scoped to the landing list, not the card: every fixture's name is also in
    // an SVG <title> above it, which is exactly the thing being replaced here.
    const list = document.querySelector(".plan-landings") as HTMLElement;
    // Wall and ceiling are the same dashed line from above, and they are the
    // two worth telling apart — one of them is at head height.
    const row = within(list).getByText(/Moving Head #1/).closest("div")!;
    expect(row.textContent).toContain("ceiling");
  });

  it("explains itself instead of drawing a room it has no size for", async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(stateWith((s) => {
      s.venue = { name: "somewhere new" };
    })));
    await goTo(user, /Move/);
    expect(screen.queryByLabelText("plan view of the room")).toBeNull();
    expect(screen.getByText(/No room dimensions/i)).toBeInTheDocument();
  });
});

/**
 * Per-slot rate — the last thing the three slots did not have independently.
 */
describe("slot rate", () => {
  const rateCard = () =>
    screen.getByText("Rate").closest(".card")! as HTMLElement;

  it("sends a rate for the slot whose tab it is on", async () => {
    const user = userEvent.setup();
    const socket = mount();
    await goTo(user, /Color/);
    await user.click(within(rateCard()).getByLabelText("color rate 0.5"));
    expect(socket.last()).toEqual({ type: "rate", slot: "color", value: 0.5 });
    await goTo(user, /Bright/);
    await user.click(within(rateCard()).getByLabelText("level rate 2"));
    expect(socket.last()).toEqual({ type: "rate", slot: "level", value: 2 });
  });

  it("calls a rate of zero a hold, because that is what it is", async () => {
    const user = userEvent.setup();
    const socket = mount();
    await goTo(user, /Move/);
    // Queried by aria-label, because that is where the accessible name comes
    // from; the VISIBLE text is what this test is actually about, so it is
    // asserted rather than searched for.
    const hold = within(rateCard()).getByLabelText("movement rate 0");
    expect(hold).toHaveTextContent("hold");
    await user.click(hold);
    expect(socket.last()).toEqual({ type: "rate", slot: "movement", value: 0 });
  });

  it("renders the engine's rate rather than remembering its own", async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(stateWith((s) => {
      s.auto.slot_rates = { movement: 1, color: 0.25, level: 1 };
    })));
    await goTo(user, /Color/);
    expect(within(rateCard()).getByLabelText("color rate 0.25"))
      .toHaveClass("on");
    expect(within(rateCard()).getByLabelText("color rate 1")).not.toHaveClass("on");
  });

  it("resets one slot without touching the others", async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(stateWith((s) => {
      s.auto.slot_rates = { movement: 1, color: 0.25, level: 1 };
    })));
    await goTo(user, /Color/);
    await user.click(screen.getByLabelText("reset color rate"));
    expect(socket.last()).toEqual({ type: "rate", slot: "color", value: 1 });
  });

  it("stops duplicating the global Speed on the Move tab", async () => {
    const user = userEvent.setup();
    mount();
    await goTo(user, /Move/);
    // Move used to carry its own copy of the Show tab's Speed row: two controls
    // doing one thing in two places, and now genuinely confusing beside a Rate
    // that also makes the move faster. Speed is a clock control and stays with
    // the tempo.
    expect(screen.queryByText("Speed")).toBeNull();
    await goTo(user, /Show/);
    expect(screen.getByText("Speed")).toBeInTheDocument();
  });
});

describe("parametric routines", () => {
  /** Select a routine into a slot, as the engine would report it back. */
  const loaded = (name: string, slot: "movement" | "color" | "level",
                  group = "corner movers") =>
    stateWith((s) => { s.selection[slot] = { [group]: name }; });

  it("renders a control per parameter from what the engine declared", async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(loaded("Ball Orbit", "movement")));
    await goTo(user, /Move/);
    // Nothing in the UI knows what an orbit is. These three controls exist
    // because engine/blocks.py declared them.
    expect(screen.getByLabelText("Radius")).toBeTruthy();
    expect(screen.getByLabelText("Flatten")).toBeTruthy();
    expect(screen.getByLabelText("Cycle")).toBeTruthy();
  });

  it("takes the slider's range from the engine, not from a literal here",
     async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(loaded("Ball Orbit", "movement")));
    await goTo(user, /Move/);
    const radius = screen.getByLabelText("Radius") as HTMLInputElement;
    // The range a parameter has used to be written out in the engine's clamp,
    // in a config Spec and again in the slider's arguments, with nothing
    // keeping them in step. It now comes from the engine's own declaration,
    // generated into blocks.generated.json.
    const spec = BLOCK_PARAMS.orbit!.find((p) => p.name === "radius")!;
    expect(radius.min).toBe(String(spec.min));
    expect(radius.max).toBe(String(spec.max));
    expect(radius.step).toBe(String(spec.step));
  });

  it("sends only the parameter that moved", async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(loaded("Ball Orbit", "movement")));
    await goTo(user, /Move/);
    fireEvent.change(screen.getByLabelText("Radius"), { target: { value: "25" } });
    // Sparse on purpose: sending the whole resolved set would bake every other
    // parameter's current value into the override and detach the routine from
    // what parametric_looks.json authored.
    expect(socket.last()).toEqual({
      type: "look_params", name: "Ball Orbit", values: { radius: 25 } });
  });

  it("shows the dialled-in value over the authored one", async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(stateWith((s) => {
      s.selection.movement = { "corner movers": "Ball Orbit" };
      s.look_params = { "Ball Orbit": { radius: 42 } };
    })));
    await goTo(user, /Move/);
    expect((screen.getByLabelText("Radius") as HTMLInputElement).value)
      .toBe("42");
  });

  it("only offers Reset once something has been dialled in", async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(loaded("Ball Orbit", "movement")));
    await goTo(user, /Move/);
    const card = () =>
      screen.getByText(/Tweak · Ball Orbit/).closest(".card")! as HTMLElement;
    expect(within(card()).getByRole("button", { name: /reset Ball Orbit/i }))
      .toHaveProperty("disabled", true);

    act(() => socket.push(stateWith((s) => {
      s.selection.movement = { "corner movers": "Ball Orbit" };
      s.look_params = { "Ball Orbit": { radius: 42 } };
    })));
    const reset = within(card()).getByRole("button", { name: /reset Ball Orbit/i });
    expect(reset).toHaveProperty("disabled", false);
    await user.click(reset);
    expect(socket.last()).toEqual({
      type: "look_params", name: "Ball Orbit", reset: true });
  });

  it("shows no Tweak card for a ported look, rather than an empty one",
     async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(loaded("Ball Wave", "movement")));
    await goTo(user, /Move/);
    // A stored table of DMX has nothing to turn. On the Move tab that is most
    // of the library, and a permanently-empty card is furniture.
    expect(screen.queryByText(/^Tweak/)).toBeNull();
  });

  it("marks which looks in the picker can be tuned", async () => {
    const user = userEvent.setup();
    mount();
    await goTo(user, /Move/);
    const orbit = screen.getByRole("button", { name: /Ball Orbit/ });
    expect(within(orbit).getByText("tune")).toBeTruthy();
    const ported = screen.getByRole("button", { name: /^Ball Wave/ });
    expect(within(ported).queryByText("tune")).toBeNull();
  });

  it("renders a choice parameter as pills, not a slider", async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(loaded("Wind Out", "movement")));
    await goTo(user, /Move/);
    const direction = screen.getByRole("group", { name: "Direction" });
    await user.click(within(direction).getByRole("button", { name: "in" }));
    expect(socket.last()).toEqual({
      type: "look_params", name: "Wind Out",
      values: { direction: "in" } });
  });
});

describe("modulation", () => {
  const withRoutine = () => stateWith((s) => {
    s.selection.movement = { "corner movers": "Ball Orbit" };
  });

  it("offers the loaded routine's numbers and the shape macros", async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(withRoutine()));
    await goTo(user, /Move/);
    const group = screen.getByRole("group", { name: "parameter to modulate" });
    expect(within(group).getByRole("button", { name: "Ball Orbit · Radius" }))
      .toBeTruthy();
    expect(within(group).getByRole("button", { name: "Shape · Size" }))
      .toBeTruthy();
    // Only numbers: there is no halfway between two choices, and the engine
    // refuses a choice target rather than rounding it.
    expect(within(group).queryByRole("button", { name: /Axis/ })).toBeNull();
  });

  it("binds a parameter to a waveform in two taps", async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(withRoutine()));
    await goTo(user, /Move/);
    await user.click(screen.getByRole("button", { name: "Ball Orbit · Radius" }));
    await user.click(within(screen.getByRole("group", { name: "waveform" }))
      .getByRole("button", { name: "sine" }));
    // No range sent: with none given the engine sweeps the target's whole
    // declared range, which is what makes binding one a single decision.
    expect(socket.last()).toEqual({
      type: "modulate", look: "Ball Orbit", param: "radius",
      shape: "sine", bars: 16 });
  });

  it("lists what is running and stops one", async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(stateWith((s) => {
      s.selection.movement = { "corner movers": "Ball Orbit" };
      s.modulators = [{ param: "radius", look: "Ball Orbit", shape: "sine",
                        bars: 16, low: 0, high: 60, phase: 0 }];
    })));
    await goTo(user, /Move/);
    expect(screen.getByText(/sine · 16b · 0–60/)).toBeTruthy();
    await user.click(screen.getByRole("button",
      { name: "stop modulating Ball Orbit · Radius" }));
    expect(socket.last()).toEqual({
      type: "modulate_clear", look: "Ball Orbit", param: "radius" });
  });

  it("only enables Stop all when something is actually running", async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(withRoutine()));
    await goTo(user, /Move/);
    const stopAll = () =>
      screen.getByRole("button", { name: "stop all modulation" });
    expect(stopAll()).toHaveProperty("disabled", true);

    act(() => socket.push(stateWith((s) => {
      s.selection.movement = { "corner movers": "Ball Orbit" };
      s.modulators = [{ param: "size", shape: "triangle", bars: 8,
                        low: 0.5, high: 2, phase: 0 }];
    })));
    expect(stopAll()).toHaveProperty("disabled", false);
    await user.click(stopAll());
    expect(socket.last()).toEqual({ type: "modulate_clear", all: true });
  });

  it("shows no modulation card where nothing can be modulated", async () => {
    const user = userEvent.setup();
    const socket = mount();
    // The Bright tab with a ported level look loaded: no routine parameters,
    // and the shape macros belong to Move.
    act(() => socket.push(stateWith((s) => {
      s.selection.level = { "corner movers": "Dim Chase" };
    })));
    await goTo(user, /Bright/);
    expect(screen.queryByText("Modulation")).toBeNull();
  });
});

describe("stacked routines and variation", () => {
  it("stacks another route and lists what is on top", async () => {
    const user = userEvent.setup();
    const socket = mount();
    await goTo(user, /Move/);
    const stackCard = () => screen.getByRole("button",
      { name: "clear stacked routines" }).closest(".card")! as HTMLElement;
    await user.click(screen.getByRole("button", { name: /Stack another route/ }));
    // Scoped: the Route picker above lists every movement look too, so an
    // unscoped query for a look name matches twice.
    await user.click(within(stackCard())
      .getByRole("button", { name: /^Ball Orbit/ }));
    expect(socket.last()).toEqual({ type: "movement_add", name: "Ball Orbit" });

    act(() => socket.push(stateWith((s) => {
      s.movement_extra = ["Ball Orbit"];
    })));
    expect(screen.getByText(/\+ 1 on top/)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "unstack Ball Orbit" }));
    expect(socket.last()).toEqual({
      type: "movement_remove", name: "Ball Orbit" });
  });

  it("does not offer the base route as something to stack on itself",
     async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(stateWith((s) => {
      s.selection.movement = { "corner movers": "Ball Orbit" };
    })));
    await goTo(user, /Move/);
    await user.click(screen.getByRole("button", { name: /Stack another route/ }));
    const stack = screen.getByText(/on top|Just the one route/)
      .closest(".card")! as HTMLElement;
    // Stacking a look on itself just doubles its excursion, which is what Size
    // is for — the engine refuses it, so the UI should not offer it.
    expect(within(stack).queryByRole("button", { name: /^Ball Orbit/ }))
      .toBeNull();
  });

  it("stops offering more once the cap is reached", async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(stateWith((s) => {
      s.movement_extra = ["Nod", "Drift", "Restless"];
      s.movement_stack_max = 3;
    })));
    await goTo(user, /Move/);
    expect(screen.queryByRole("button", { name: /Stack another route/ }))
      .toBeNull();
    expect(screen.getByText(/3 is the limit/)).toBeTruthy();
  });

  it("offers Vary on a routine, and it is not the same button as Reset",
     async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(stateWith((s) => {
      s.selection.movement = { "corner movers": "Ball Orbit" };
    })));
    await goTo(user, /Move/);
    // Vary is always available; Reset only once something is dialled in. They
    // sit next to each other, so the distinction has to be visible.
    expect(screen.getByRole("button", { name: "reset Ball Orbit" }))
      .toHaveProperty("disabled", true);
    const vary = screen.getByRole("button", { name: "vary Ball Orbit" });
    expect(vary).toHaveProperty("disabled", false);
    await user.click(vary);
    expect(socket.last()).toEqual({
      type: "vary", name: "Ball Orbit", amount: 0.35 });
  });
});

describe("the rig's reach", () => {
  it("spans the centre slider over where the heads can actually go", async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(stateWith((s) => {
      s.reach = { bearing: [-77, 199], elevation: [-275, 452] };
    })));
    await goTo(user, /Move/);
    // Not the fixed +-180 it used to be: on this rig that offered a hundred
    // degrees no head could reach and refused eighteen every head could.
    const bearing = screen.getByLabelText("Centre —") as HTMLInputElement;
    expect([bearing.min, bearing.max]).toEqual(["-77", "199"]);
    const elev = screen.getByLabelText("Centre |") as HTMLInputElement;
    expect([elev.min, elev.max]).toEqual(["-275", "452"]);
  });

  it("falls back to the declared range on a rig with no moving heads", async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(stateWith((s) => { s.reach = {}; })));
    await goTo(user, /Move/);
    const bearing = screen.getByLabelText("Centre —") as HTMLInputElement;
    expect([bearing.min, bearing.max]).toEqual(["-180", "180"]);
  });

  it("says when a head has stopped at the end of its travel", async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(stateWith((s) => {
      s.fixtures.find((f) => f.name === "Moving Head #3")!.at_limit = ["bearing"];
    })));
    await goTo(user, /Move/);
    // Under the Shape card, in either mode: the operator turning the centre is
    // the one who needs to know.
    expect(screen.getByText(/Moving Head #3 is at the end of its travel/))
      .toBeTruthy();
  });

  it("does not warn when every head is where it was sent", async () => {
    const user = userEvent.setup();
    mount();
    await goTo(user, /Move/);
    expect(screen.queryByText(/at the end of/)).toBeNull();
  });

  it("turns a position button into an offset whose range is the rig's", async () => {
    const user = userEvent.setup();
    const socket = mount();
    act(() => socket.push(stateWith((s) => {
      s.selection.movement = { "corner movers": "Heads - Floor" };
      s.reach = { bearing: [-77, 199], elevation: [-275, 452] };
    })));
    await goTo(user, /Move/);
    // The ported button, superseded in place by an `offset` block: same name,
    // same place in the picker, but now it can be turned.
    const up = screen.getByLabelText("Up") as HTMLInputElement;
    expect(Number(up.value)).toBeCloseTo(-25, 0);
    expect([up.min, up.max]).toEqual(["-275", "452"]);
  });
});

describe("retired looks", () => {
  // A ported PATH, not a pose: "path" is first in the Move tab's KIND_ORDER and
  // so is the group the picker opens by default. Retiring a pose would leave it
  // inside a collapsed group, and "the tile is absent" would pass whether or not
  // retirement did anything.
  // ...and not the one that is currently loaded, which stays visible on purpose
  // (see the third test). The fixture has a movement look held, so without this
  // the obvious pick is exactly the one retirement must not hide.
  const withRetired = () => stateWith((s) => {
    const up = new Set(Object.values(s.selection.movement ?? {}));
    const target = s.looks.find(
      (l) => l.slot === "movement" && l.kind === "path"
             && !l.block && !l.retired && !l.step_of && !up.has(l.name))!;
    target.retired = true;
    target.replaced_by = "Ball Orbit";
  });
  // Found by the marker this test set, not as "the retired one": the real
  // library retires looks of its own now (Lazy Circle), so the first retired
  // look in the state is not necessarily the one under test.
  const hiddenIn = (state: EngineState) =>
    state.looks.find((l) => l.replaced_by === "Ball Orbit")!.name;
  const retiredOnMove = (state: EngineState) =>
    state.looks.filter((l) => l.slot === "movement" && l.retired).length;

  it("hides a retired look and says how many are hidden", async () => {
    const user = userEvent.setup();
    const socket = mount();
    const state = withRetired();
    const hidden = hiddenIn(state);
    act(() => socket.push(state));
    await goTo(user, /Move/);
    // Hidden, never deleted: looks.json is generated and its round-trip proof
    // needs every entry present. The list must not quietly shrink either.
    expect(screen.queryByRole("button", { name: new RegExp(`^${hidden}`) }))
      .toBeNull();
    expect(screen.getByRole("button",
      { name: new RegExp(`Show ${retiredOnMove(state)} retired`) })).toBeTruthy();
  });

  it("brings it back, pointing at what replaced it", async () => {
    const user = userEvent.setup();
    const socket = mount();
    const state = withRetired();
    const hidden = hiddenIn(state);
    act(() => socket.push(state));
    await goTo(user, /Move/);
    await user.click(screen.getByRole("button", { name: /Show \d+ retired/ }));
    const tile = screen.getByRole("button", { name: new RegExp(`^${hidden}`) });
    expect(within(tile).getByText(/retired — use Ball Orbit/)).toBeTruthy();
  });

  it("still shows a retired look that is actually loaded", async () => {
    const user = userEvent.setup();
    const socket = mount();
    const state = withRetired();
    const hidden = hiddenIn(state);
    state.selection.movement = { "corner movers": hidden };
    act(() => socket.push(state));
    await goTo(user, /Move/);
    // Hiding what is on stage would leave the picker claiming nothing is
    // selected while the rig plainly disagrees.
    expect(screen.getByRole("button", { name: new RegExp(`^${hidden}`) }))
      .toBeTruthy();
  });
});

describe("shape macros", () => {
  const openMove = async (user: ReturnType<typeof userEvent.setup>) =>
    goTo(user, /Move/);
  const shapeCard = () =>
    screen.getByText("Shape").closest(".card")! as HTMLElement;

  it("shows the identity values when nothing is dialled in", async () => {
    const user = userEvent.setup();
    mount();
    await openMove(user);
    expect(screen.getByText("1.00×")).toBeTruthy();
    // Scoped to the Shape card. There is a Rate card on this tab with its own
    // Reset, and an unscoped query for a word that common is ambiguous the
    // moment the tab grows a second one.
    expect(within(shapeCard()).getByRole("button", { name: /Reset/i }))
      .toHaveProperty("disabled", true);
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
    const reset = within(shapeCard()).getByRole("button", { name: /Reset/i });
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

describe("cue list", () => {
  it("does not claim a current cue before the first GO", async () => {
    mount();
    // -1 is not the same as being on cue 0. Saying "now: Warm Up" when nothing
    // has been taken is the one thing that would make an operator distrust it.
    expect(screen.getByText(/Not started/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: /GO — Warm Up/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /^Back$/ })).toHaveProperty(
      "disabled", true);
  });

  it("sends GO", async () => {
    const user = userEvent.setup();
    const socket = mount();
    await user.click(screen.getByRole("button", { name: /GO —/ }));
    expect(socket.last()).toEqual({ type: "go" });
  });

  it("shows where it is once started", async () => {
    const socket = mount();
    act(() => socket.push(stateWith((s) => {
      s.cues!.index = 4;
      s.cues!.current = "Cathedral";
      s.cues!.next = "Peak";
    })));
    expect(screen.getByText("Cathedral")).toBeTruthy();
    expect(screen.getByText("5/9")).toBeTruthy();
  });

  it("can jump straight to a cue", async () => {
    const user = userEvent.setup();
    const socket = mount();
    await user.click(screen.getByRole("button", { name: /Show all 9 cues/i }));
    await user.click(screen.getByRole("button", { name: /8\. Drop/ }));
    expect(socket.last()).toEqual({ type: "cue", index: 7 });
  });

  it("marks the cut cue as a cut rather than a fade", async () => {
    const user = userEvent.setup();
    mount();
    await user.click(screen.getByRole("button", { name: /Show all 9 cues/i }));
    expect(screen.getByRole("button", { name: /8\. Drop cut/ })).toBeTruthy();
  });

  it("disables GO at the end rather than wrapping", async () => {
    const socket = mount();
    act(() => socket.push(stateWith((s) => {
      s.cues!.index = 8; s.cues!.current = "Landing"; s.cues!.next = null;
    })));
    expect(screen.getByRole("button", { name: /GO —/ })).toHaveProperty(
      "disabled", true);
    expect(screen.getByText(/end of the list/i)).toBeTruthy();
  });
});

describe("controls that had handlers but no sender", () => {
  it("flash is held, not latched", async () => {
    const user = userEvent.setup();
    const socket = mount();
    await goTo(user, /Bright/);
    const button = screen.getByRole("button", { name: "flash Movers" });
    // A click fires on release, and a bump that lands when you let go is not a
    // bump — so this has to be pointer down/up.
    await user.pointer({ keys: "[MouseLeft>]", target: button });
    expect(socket.last()).toEqual({ type: "flash", target: "corner movers" });
    await user.pointer({ keys: "[/MouseLeft]", target: button });
    expect(socket.last()).toEqual({
      type: "flash", target: "corner movers", on: false });
  });

  it("releases the flash when the pointer slides off the button", async () => {
    const user = userEvent.setup();
    const socket = mount();
    await goTo(user, /Bright/);
    const button = screen.getByRole("button", { name: "flash all" });
    await user.pointer({ keys: "[MouseLeft>]", target: button });
    // A thumb sliding off never sends a normal release, and a flash stuck on is
    // a group stuck at full.
    fireEvent.pointerLeave(button);
    expect(socket.last()).toEqual({ type: "flash", target: "all", on: false });
  });

  it("sends auto_interval, which nothing sent before", async () => {
    const user = userEvent.setup();
    const socket = mount();
    await user.click(screen.getByLabelText("looks every 4 phrases"));
    expect(socket.last()).toEqual({
      type: "auto_interval", axis: "looks", value: 4 });
  });

  it("sends palette_select, which nothing sent before", async () => {
    const user = userEvent.setup();
    const socket = mount();
    await goTo(user, /Color/);
    fireEvent.contextMenu(screen.getByLabelText("palette 3"));
    expect(socket.last()).toEqual({ type: "palette_select", index: 3 });
  });

  it("clears any held flash on reconnect", async () => {
    const socket = mount();
    // The release never arrives if the socket drops mid-press, and the operator
    // is then looking at a reconnected console that appears fine.
    expect(socket.commands.some((c) => c.type === "flash_clear")).toBe(true);
  });
});
