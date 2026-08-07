import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
  localStorage.clear();
});

// jsdom has no Wake Lock API. The UI asks for one on mount, and without this
// every test logs an unhandled rejection that buries real failures.
Object.defineProperty(navigator, "wakeLock", {
  configurable: true,
  // release() returns a Promise in the real API and the cleanup path chains
  // .catch() onto it, so the mock has to as well.
  value: {
    request: vi.fn().mockResolvedValue({
      release: vi.fn().mockResolvedValue(undefined),
    }),
  },
});
