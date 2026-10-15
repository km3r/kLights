/// <reference types="vite/client" />
/**
 * No two modules import each other, however far round.
 *
 * A cycle works until it does not: whichever module the bundler happens to
 * evaluate first reads the other's `const` exports before they exist, and the
 * page dies at load with a TDZ error the type checker never saw. Studio's
 * shared editor modules had one (automation, the clipboard and the lanes each
 * reached into another for a selection id or a lane height), which is why the
 * ids are in designer/ids.ts and LANE_H in laneSpecs.ts.
 *
 * Only imports that run count: `import type` is erased, and a dynamic
 * `import()` (a lazy page) is loaded after everything it could reach.
 */
import { describe, expect, it } from "vitest";

const sources = import.meta.glob(["../**/*.{ts,tsx}", "!../**/*.test.{ts,tsx}", "!../test/**"],
                                 { query: "?raw", import: "default", eager: true }) as
  Record<string, string>;

/** A module's runtime imports of other modules here, as glob keys. */
function importsOf(file: string, text: string, known: Set<string>): string[] {
  const dir = file.slice(0, file.lastIndexOf("/"));
  const out: string[] = [];
  // `import x from`, `import { a, b } from` (over lines), `export { a } from`;
  // not `import type` / `export type`, nor `import "./x.css"`.
  const re = /^(?:import|export)\s+(?!type\b)[^;]*?\bfrom\s+"(\.{1,2}\/[^"]+)"/gm;
  for (const m of text.matchAll(re)) {
    const parts = `${dir}/${m[1]}`.split("/");
    const path: string[] = [];
    for (const p of parts) {
      if (p === ".." && path.length && path[path.length - 1] !== "..") path.pop();
      else if (p !== ".") path.push(p);
    }
    const base = path.join("/");
    const hit = [base, `${base}.ts`, `${base}.tsx`, `${base}/index.ts`, `${base}/index.tsx`]
      .find((c) => known.has(c));
    if (hit) out.push(hit);
  }
  return out;
}

describe("imports", () => {
  it("found the modules", () => {
    expect(Object.keys(sources).length).toBeGreaterThan(40);
    expect(Object.keys(sources)).toContain("../designer/ids.ts");
  });

  it("form no cycle", () => {
    const known = new Set(Object.keys(sources));
    const graph = new Map([...known].map((f) => [f, importsOf(f, sources[f]!, known)]));
    // Read as they are written: one name, several over lines, a sibling folder.
    expect(graph.get("../designer/inspector.tsx")).toContain("../designer/ids.ts");
    expect(graph.get("../designer/Designer.tsx")).toEqual(
      expect.arrayContaining(["../designer/ids.ts", "../useEngine.ts", "../designer/automation.tsx"]));
    // And not the type-only ones: every editor names `Engine` from Designer.tsx.
    expect(graph.get("../designer/Studio.tsx")).not.toContain("../designer/Designer.tsx");
    const cycles: string[] = [];
    const state = new Map<string, "open" | "done">();
    const stack: string[] = [];
    const visit = (f: string) => {
      state.set(f, "open");
      stack.push(f);
      for (const next of graph.get(f) ?? []) {
        if (state.get(next) === "open") {
          cycles.push([...stack.slice(stack.indexOf(next)), next].join(" > "));
        } else if (!state.has(next)) {
          visit(next);
        }
      }
      stack.pop();
      state.set(f, "done");
    };
    for (const f of known) if (!state.has(f)) visit(f);
    expect(cycles).toEqual([]);
  });
});
