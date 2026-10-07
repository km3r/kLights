import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { PlanView } from "../Plan";
import type { EngineState } from "../types";
import { despacioState, stateWith } from "./mockSocket";

/**
 * The plan view: the room from above, each lit beam drawn where it lands at
 * the width it really spreads to. It is what an operator aims by at a venue,
 * so what it must never do is mislead -- a wide wash drawn as a measured disc
 * the size of the room, a beam the taper is holding down shown as if the
 * operator had dimmed it, a fixture with no position silently missing from a
 * plan that looks complete. Each of those is asserted on the SVG and on the
 * written legend (a phone has no hover, so a tooltip alone does not count).
 */

afterEach(cleanup);

function plan(edit?: (s: EngineState) => void) {
  const state = edit ? stateWith(edit) : despacioState;
  const { container } = render(<PlanView state={state} />);
  const svg = container.querySelector("svg[aria-label='plan view of the room']");
  return { container, svg, legend: container.querySelector(".plan-landings") };
}

const titles = (svg: Element | null) =>
  Array.from(svg?.querySelectorAll("title") ?? []).map((t) => t.textContent ?? "");

/** Index of the fixture in the despacio fixture list. */
const at = (name: string) => despacioState.fixtures.findIndex((f) => f.name === name);

describe("plan: what is drawn", () => {
  it("draws the room to scale, the crowd zone, the canopy and the ball", () => {
    const { svg } = plan();
    expect(svg).not.toBeNull();
    expect(screen.getByText("18.3 × 18.3 m")).toBeInTheDocument();
    const t = titles(svg);
    expect(t.some((x) => x.startsWith("Crowd zone"))).toBe(true);
    expect(t).toContain("Canopy at 4.6 m");
    expect(t).toContain("Mirror ball");
  });

  it("draws a beam only for a lit fixture that lands somewhere", () => {
    const { svg } = plan();
    const beams = titles(svg).filter((x) => x.includes(" → "));
    // Moving Head #3 is at intensity 0 in the capture; the pinspots land nowhere.
    expect(beams.map((b) => b.split(" → ")[0]).sort())
      .toEqual(["Moving Head #1", "Moving Head #2", "Moving Head #4"]);
  });

  it("says what each beam landed on, in text as well as on the drawing", () => {
    const { svg, legend } = plan();
    expect(titles(svg)).toContain("Moving Head #1 → canopy at 5.9 m");
    expect(legend?.textContent).toMatch(/Moving Head #1 → canopy\s*5\.9m/);
    expect(legend?.textContent).toMatch(/Moving Head #2 → wall/);
  });

  it("an unknown landing surface is named as the engine named it, not dropped", () => {
    const { svg } = plan((s) => { s.fixtures[at("Moving Head #1")]!.lands_on = "mezzanine"; });
    expect(titles(svg).some((x) => x.startsWith("Moving Head #1 → mezzanine"))).toBe(true);
  });

  it("a dark fixture still shows where it is, as an unlit dot", () => {
    const { svg } = plan();
    expect(titles(svg)).toContain("Moving Head #3");
  });
});

describe("plan: what it must not let anyone misread", () => {
  it("a wash wider than the room is drawn hollow and clipped, and says so in the legend", () => {
    const { svg, legend } = plan((s) => {
      const f = s.fixtures[at("Moving Head #1")]!;
      f.beam_deg = 120;
      f.throw_mm = 8000;
    });
    const beam = titles(svg).find((x) => x.startsWith("Moving Head #1 → "))!;
    expect(beam).toMatch(/spreads 27\.7 m wide, drawn clipped/);
    expect(legend?.textContent).toMatch(/Moving Head #1 → canopy.*wide, drawn clipped/);
    // Never a disc bigger than the cap: 0.28 of the room's shorter side.
    const radii = Array.from(svg!.querySelectorAll("circle[stroke-dasharray='400 300']"))
      .map((c) => Number(c.getAttribute("r")));
    expect(radii).toHaveLength(1);
    expect(radii[0]).toBeCloseTo(18288 * 0.28);
  });

  it("an ordinary beam is a filled disc at its true radius, never a hairline", () => {
    const { svg } = plan();
    const f = despacioState.fixtures[at("Moving Head #1")]!;
    const radius = f.throw_mm! * Math.tan((f.beam_deg! / 2) * Math.PI / 180);
    const [cx, , cz] = f.lands_at!;
    const disc = Array.from(svg!.querySelectorAll("circle"))
      .find((c) => Number(c.getAttribute("cx")) === cx && Number(c.getAttribute("cy")) === cz);
    expect(Number(disc?.getAttribute("r"))).toBeCloseTo(radius);
    expect(disc?.getAttribute("fill-opacity")).toBe("0.28");
  });

  it("a beam the safety taper is holding down is ringed in amber and counted", () => {
    const { svg, legend, container } = plan((s) => {
      s.fixtures[at("Moving Head #2")]!.safety = { taper: 0.5, reason: "over the crowd" };
    });
    expect(titles(svg).find((x) => x.startsWith("Moving Head #2")))
      .toMatch(/taper holding it at 50%/);
    expect(svg!.querySelectorAll("circle[stroke='var(--warn)']")).toHaveLength(1);
    expect(legend?.textContent).toMatch(/Moving Head #2 → wall.*held at 50%/);
    expect(container.textContent).toMatch(/1 ringed in amber/);
  });

  it("a jogging head is outlined and says the taper is bypassed", () => {
    const { svg } = plan((s) => { s.fixtures[at("Moving Head #4")]!.jogging = true; });
    expect(titles(svg)).toContain("Moving Head #4 — JOGGING, taper bypassed");
  });

  it("fixtures with no position are counted, so the plan never looks complete when it is not", () => {
    const { container, svg } = plan((s) => {
      delete s.fixtures[at("Pinspot #1")]!.position;
      delete s.fixtures[at("Pinspot #2")]!.position;
    });
    expect(container.textContent).toMatch(/2 fixture\(s\) have no position/);
    expect(titles(svg).some((x) => x.startsWith("Pinspot"))).toBe(false);
  });

  it("with no room dimensions, it says what to set instead of drawing a wrong plan", () => {
    const { svg, container } = plan((s) => { s.venue.width = 0; });
    expect(svg).toBeNull();
    expect(container.textContent).toMatch(/No room dimensions/);
  });

  it("with no ball and no canopy, it draws the room and the beams without them", () => {
    const { svg } = plan((s) => { delete s.venue.ball; s.venue.canopy = undefined as never; });
    const t = titles(svg);
    expect(t).not.toContain("Mirror ball");
    expect(t.some((x) => x.startsWith("Canopy"))).toBe(false);
    expect(t.some((x) => x.startsWith("Moving Head #1 → "))).toBe(true);
  });
});
