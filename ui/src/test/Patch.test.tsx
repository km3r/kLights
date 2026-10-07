import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PatchSection } from "../tabs/Patch";
import type { Command, EngineState } from "../types";
import { despacioState, stateWith } from "./mockSocket";

/**
 * The patch editor on its own: every path that edits rig.json, and every
 * guard in front of one. App.test.tsx covers address, tags and position
 * through the whole console; this covers what it does not -- removing a unit,
 * autopatching, adding one, and input the operator did not mean.
 *
 * Every one of these writes a file that takes an engine restart to undo, and
 * a re-addressed rig is a walk round the room with a torch. So the property
 * held throughout is the same: a command goes out only for a deliberate,
 * complete, changed value.
 */

let sent: Command[];
let send: (c: Command) => void;

beforeEach(() => {
  sent = [];
  send = vi.fn((c: Command) => { sent.push(c); });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

async function unlocked(state: EngineState = despacioState) {
  const user = userEvent.setup();
  render(<PatchSection state={state} send={send} />);
  await user.click(screen.getByRole("button", { name: "Unlock to edit" }));
  return user;
}

describe("patch: the lock", () => {
  it("shows only a count while locked, every row once unlocked, and locks again", async () => {
    const user = userEvent.setup();
    render(<PatchSection state={despacioState} send={send} />);
    expect(screen.getByText(/6 fixture\(s\) patched/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Moving Head #1 address")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Unlock to edit" }));
    expect(screen.getAllByLabelText(/ address$/)).toHaveLength(6);
    await user.click(screen.getByRole("button", { name: "Lock" }));
    expect(screen.queryByLabelText("Moving Head #1 address")).toBeNull();
    expect(sent).toEqual([]);
  });
});

describe("patch: input nobody meant", () => {
  it("an address that is not a number is put back, and nothing is sent", async () => {
    const user = await unlocked();
    const field = screen.getByLabelText("Pinspot #2 address");
    await user.clear(field);
    await user.type(field, "abc");
    await user.tab();
    expect(field).toHaveValue("51");
    expect(sent).toEqual([]);
  });

  it("an address typed back to what it was sends nothing", async () => {
    const user = await unlocked();
    const field = screen.getByLabelText("Pinspot #2 address");
    await user.clear(field);
    await user.type(field, "51");
    await user.tab();
    expect(sent).toEqual([]);
  });

  it("tags are trimmed and empties dropped before they are compared or sent", async () => {
    const user = await unlocked();
    const field = screen.getByLabelText("Pinspot #1 tags");
    await user.clear(field);
    await user.type(field, "  pinspots ,, ");
    await user.tab();
    expect(sent).toEqual([]);                      // the same one tag: no write
    await user.clear(field);
    await user.type(field, " pinspots , specials,");
    await user.tab();
    expect(sent).toEqual([{ type: "patch_tags", name: "Pinspot #1", tags: ["pinspots", "specials"] }]);
  });

  it("Enter in a position field commits the whole position", async () => {
    const user = await unlocked();
    const x = screen.getByLabelText("Pinspot #1 x");
    await user.clear(x);
    await user.type(x, "1234{Enter}");
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({ type: "patch_position", name: "Pinspot #1" });
    expect((sent[0] as { position: { x: number } }).position.x).toBe(1234);
  });

  it("a fixture with no position yet is sent one only once all three axes are numbers", async () => {
    const user = await unlocked(stateWith((s) => { delete s.fixtures[5]!.position; }));
    for (const axis of ["x", "height", "z"]) {
      expect(screen.getByLabelText(`Pinspot #2 ${axis}`)).toHaveValue("");
    }
    await user.type(screen.getByLabelText("Pinspot #2 x"), "100");
    await user.type(screen.getByLabelText("Pinspot #2 height"), "2000");
    await user.click(screen.getByLabelText("Moving Head #1 address"));   // focus leaves
    expect(sent).toEqual([]);
    await user.type(screen.getByLabelText("Pinspot #2 z"), "300");
    await user.click(screen.getByLabelText("Moving Head #1 address"));
    expect(sent).toEqual([{ type: "patch_position", name: "Pinspot #2",
                            position: { x: 100, y: 2000, z: 300 } }]);
  });
});

describe("patch: the edits that touch more than one field", () => {
  it("Remove asks first, and a cancelled confirm sends nothing", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const user = await unlocked();
    const row = screen.getByLabelText("Pinspot #1 address").closest(".fixture") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Remove" }));
    expect(confirm).toHaveBeenCalledWith("Unpatch Pinspot #1?");
    expect(sent).toEqual([]);
  });

  it("removing a moving head warns that every head after it shifts, then removes it", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = await unlocked();
    const row = screen.getByLabelText("Moving Head #2 address").closest(".fixture") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Remove" }));
    expect(confirm.mock.calls[0]![0]).toMatch(/moving head.*calibration no longer lines up/s);
    expect(sent).toEqual([{ type: "patch_remove", name: "Moving Head #2" }]);
  });

  it("Autopatch says every unit must be re-dialled, and only goes on a yes", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    const user = await unlocked();
    await user.click(screen.getByRole("button", { name: "Autopatch" }));
    expect(confirm.mock.calls[0]![0]).toMatch(/re-dial every unit/);
    expect(sent).toEqual([]);
    await user.click(screen.getByRole("button", { name: "Autopatch" }));
    expect(sent).toEqual([{ type: "patch_autopatch", start: 1 }]);
  });
});

describe("patch: adding a unit", () => {
  it("offers only the engine's profiles and their modes, with channel counts", async () => {
    const user = await unlocked();
    await user.click(screen.getByRole("button", { name: "Add fixture" }));
    const profile = screen.getByLabelText("fixture profile");
    expect(within(profile).getAllByRole("option")).toHaveLength(despacioState.profiles!.length);
    await user.selectOptions(profile, "MingJie MJ-OS-018 60W Beam");
    const modes = within(screen.getByLabelText("mode")).getAllByRole("option").map((o) => o.textContent);
    expect(modes).toEqual(["9 Channel (9 channels)", "11 Channel (11 channels)"]);
  });

  it("needs a name, then sends the profile, the mode and the trimmed name, and closes", async () => {
    const user = await unlocked();
    await user.click(screen.getByRole("button", { name: "Add fixture" }));
    const add = screen.getByRole("button", { name: "Add at the first free address" });
    expect(add).toBeDisabled();
    await user.type(screen.getByLabelText("fixture name"), "   ");
    expect(add).toBeDisabled();                    // whitespace is not a name

    await user.selectOptions(screen.getByLabelText("fixture profile"), "MingJie MJ-OS-018 60W Beam");
    await user.selectOptions(screen.getByLabelText("mode"), "11 Channel");
    await user.clear(screen.getByLabelText("fixture name"));
    await user.type(screen.getByLabelText("fixture name"), "  Moving Head #5 ");
    await user.click(add);
    expect(sent).toEqual([{ type: "patch_add", name: "Moving Head #5", manufacturer: "MingJie",
                            model: "MJ-OS-018 60W Beam", mode: "11 Channel", tags: [] }]);
    expect(screen.queryByLabelText("fixture name")).toBeNull();
  });

  it("changing the profile drops a mode the new one does not have", async () => {
    const user = await unlocked();
    await user.click(screen.getByRole("button", { name: "Add fixture" }));
    await user.selectOptions(screen.getByLabelText("fixture profile"), "MingJie MJ-OS-018 60W Beam");
    await user.selectOptions(screen.getByLabelText("mode"), "11 Channel");
    await user.selectOptions(screen.getByLabelText("fixture profile"), "UKing ZQ-B93 Pinspot RGBW");
    expect(screen.getByLabelText("mode")).toHaveValue("6-channel");
    await user.type(screen.getByLabelText("fixture name"), "Pinspot #3");
    await user.click(screen.getByRole("button", { name: "Add at the first free address" }));
    expect(sent[0]).toMatchObject({ model: "ZQ-B93 Pinspot RGBW", mode: "6-channel" });
  });

  it("Cancel closes the form without sending", async () => {
    const user = await unlocked();
    await user.click(screen.getByRole("button", { name: "Add fixture" }));
    await user.type(screen.getByLabelText("fixture name"), "half typed");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByLabelText("fixture name")).toBeNull();
    expect(sent).toEqual([]);
  });

  it("with no profiles at all, says where they should be instead of offering a form", async () => {
    const user = await unlocked(stateWith((s) => { s.profiles = []; }));
    await user.click(screen.getByRole("button", { name: "Add fixture" }));
    expect(screen.getByText(/No fixture profiles found/)).toBeInTheDocument();
    expect(screen.queryByLabelText("fixture name")).toBeNull();
  });
});
