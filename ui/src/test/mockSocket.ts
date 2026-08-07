import { vi } from "vitest";
import type { Command, EngineState } from "../types";
import fixture from "../__fixtures__/despacio.json";

/**
 * A fake WebSocket that behaves like the engine.
 *
 * The state it serves is `src/__fixtures__/despacio.json`, captured from a real
 * running engine by `engine/tests/dump_snapshot.py`. A hand-written fixture
 * would drift from the protocol silently and then these tests would pass
 * against a shape the engine no longer produces; regenerating is one command.
 *
 * Commands are recorded rather than acted on. The UI is server-authoritative --
 * it never predicts -- so "did pressing this send the right command" and "does
 * the UI render the state it is given" are genuinely separate questions, and
 * conflating them would let a UI that renders its own optimistic guesses pass.
 */

export const despacioState = fixture as unknown as EngineState;

export class MockSocket {
  static instances: MockSocket[] = [];
  static OPEN = 1;

  url: string;
  readyState = 0;
  sent: Command[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockSocket.instances.push(this);
  }

  send(data: string) { this.sent.push(JSON.parse(data)); }
  close() { this.readyState = 3; this.onclose?.(); }

  /** Complete the handshake and deliver a state frame, as the engine does. */
  open(state: EngineState = despacioState) {
    this.readyState = MockSocket.OPEN;
    this.onopen?.();
    this.push(state);
  }

  push(state: EngineState) {
    this.onmessage?.({ data: JSON.stringify(state) });
  }

  /** Commands sent since the hello handshake. */
  get commands(): Command[] {
    return this.sent.filter((c) => c.type !== "hello");
  }

  last(): Command | undefined { return this.commands[this.commands.length - 1]; }
}

export function installMockSocket() {
  MockSocket.instances = [];
  vi.stubGlobal("WebSocket", MockSocket);
  return MockSocket;
}

export function currentSocket(): MockSocket {
  const s = MockSocket.instances[MockSocket.instances.length - 1];
  if (!s) throw new Error("no socket was opened");
  return s;
}

/** A copy of the fixture with `edit` applied — for testing one changed field. */
export function stateWith(edit: (s: EngineState) => void): EngineState {
  const copy = structuredClone(despacioState);
  edit(copy);
  return copy;
}
