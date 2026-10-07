/**
 * A real engine for a browser to drive, and a wire to listen to.
 *
 * Everything the e2e suite proves runs through these two: `python -m
 * engine.server`, serving the COMMITTED ui/dist exactly as a show laptop does,
 * and a UDP socket standing where the rig's Art-Net node would be. Nothing is
 * mocked between a tap in Chromium and a byte on the wire.
 *
 * Each engine runs on throwaway copies -- the event (presets, patches and the
 * engine lock are written into it) and, when asked, the example show folder --
 * on ports nobody else holds, and stops through `--stop-file`, the way the
 * launcher stops one, so it shuts down cleanly on every platform.
 */
import { spawn, type ChildProcess } from "node:child_process";
import { createSocket, type Socket } from "node:dgram";
import { cpSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const REPO = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");

/** The interpreter to run the engine with. CI sets up the one it tests. */
const PYTHON = process.env.KLIGHTS_PYTHON
  ?? (process.platform === "win32" ? "python" : "python3");

/** 1-based DMX addresses on the despacio rig (events/despacio/rig.json): four
 *  11-channel movers from 1, two 6-channel RGBW pinspots from 45. The suite
 *  reads these from the wire, so they are written down once, here. */
export const DESPACIO = {
  movers: [1, 12, 23, 34],
  pinspots: [45, 51],
  /** Offsets within one fixture, from its .qxf. */
  mover: { pan: 0, tilt: 2, dimmer: 7 },
  pinspot: { control: 0, red: 1, green: 2, blue: 3, white: 4 },
} as const;

/** The value at a 1-based address plus an offset. */
export function at(dmx: Uint8Array, address: number, offset = 0): number {
  return dmx[address - 1 + offset] ?? 0;
}

export function moverDimmers(dmx: Uint8Array): number[] {
  return DESPACIO.movers.map((a) => at(dmx, a, DESPACIO.mover.dimmer));
}

export function pinspotRgbw(dmx: Uint8Array): number[][] {
  const p = DESPACIO.pinspot;
  return DESPACIO.pinspots.map((a) => [p.red, p.green, p.blue, p.white].map((o) => at(dmx, a, o)));
}

export async function freePort(): Promise<number> {
  return new Promise((ok, fail) => {
    const server = createServer();
    server.once("error", fail);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      server.close(() => (typeof address === "object" && address ? ok(address.port) : fail(new Error("no port"))));
    });
  });
}

/** A UDP port nothing holds, for --sync-port. Probed over UDP, not TCP: a free
 *  TCP port says nothing about the same number over UDP, and on Windows the
 *  engine's bind was refused ("access forbidden") on one that was not. */
export async function freeUdpPort(): Promise<number> {
  const socket = createSocket("udp4");
  await new Promise<void>((ok) => socket.bind(0, "127.0.0.1", ok));
  const { port } = socket.address();
  await new Promise<void>((ok) => socket.close(() => ok()));
  return port;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/**
 * Where the rig's node would be: every ArtDmx packet the engine sends, decoded
 * independently of the engine's own encoder (byte layout from the Art-Net 4
 * spec, as shared/tools/artnet_listener.py reads it).
 */
export class ArtNet {
  readonly latest = new Map<number, Uint8Array>();
  packets = 0;

  private constructor(private readonly socket: Socket, readonly port: number) {
    socket.on("message", (data) => {
      if (data.length < 18 || data.toString("latin1", 0, 8) !== "Art-Net\0") return;
      if (data.readUInt16LE(8) !== 0x5000) return;                  // ArtDmx only
      const universe = (data[15]! << 8) | data[14]!;
      const length = data.readUInt16BE(16);
      this.latest.set(universe, new Uint8Array(data.subarray(18, 18 + length)));
      this.packets += 1;
    });
  }

  static async open(): Promise<ArtNet> {
    const socket = createSocket("udp4");
    await new Promise<void>((ok) => socket.bind(0, "127.0.0.1", ok));
    return new ArtNet(socket, socket.address().port);
  }

  frame(universe = 0): Uint8Array | undefined {
    return this.latest.get(universe);
  }

  /** The latest frame, as soon as it satisfies `predicate` -- which may be one
   *  that arrived before the call: wait for a state the previous frame could
   *  not already be in. Fails with what the wire last said, so a red test
   *  reads as "dimmers were [230, 0, ...]". */
  async waitFor(what: string, predicate: (dmx: Uint8Array) => boolean,
                { timeout = 5000, universe = 0 } = {}): Promise<Uint8Array> {
    const deadline = Date.now() + timeout;
    for (;;) {
      const dmx = this.frame(universe);
      if (dmx && predicate(dmx)) return dmx;
      if (Date.now() > deadline) {
        const said = dmx
          ? `movers' dimmers ${JSON.stringify(moverDimmers(dmx))}, pinspots' RGBW `
            + `${JSON.stringify(pinspotRgbw(dmx))}, ${dmx.filter((v) => v).length} non-zero channels`
          : "nothing at all";
        throw new Error(`waited ${timeout} ms for the wire to show ${what}; it showed ${said}`);
      }
      await sleep(20);
    }
  }

  /** Every frame for `ms`, for properties that must hold throughout. */
  async sample(ms: number, universe = 0): Promise<Uint8Array[]> {
    const out: Uint8Array[] = [];
    const before = this.packets;
    const deadline = Date.now() + ms;
    let seen = before;
    while (Date.now() < deadline) {
      if (this.packets !== seen) {
        seen = this.packets;
        const dmx = this.frame(universe);
        if (dmx) out.push(dmx);
      }
      await sleep(5);
    }
    return out;
  }

  close(): void {
    this.socket.close();
  }
}

export interface EngineOptions {
  /** The control token. `null` runs with --no-token. */
  token?: string | null;
  /** Serve a copy of shared/show-example as the show folder (for Studio). */
  showDir?: boolean;
  /** Listen for DJ tempo on a UDP port of its own. */
  syncPort?: boolean;
}

export class Engine {
  log = "";
  private proc: ChildProcess | null = null;

  private constructor(
    readonly root: string,
    readonly port: number,
    readonly artnetPort: number,
    readonly token: string | null,
    readonly eventDir: string,
    readonly showDir: string | null,
    readonly syncPort: number | null,
  ) {}

  static async start(options: EngineOptions & { artnetPort: number }): Promise<Engine> {
    const root = mkdtempSync(join(tmpdir(), "klights-e2e-"));
    const skip = new Set(["__pycache__", ".engine.lock", "backups"]);
    const copy = (from: string, to: string) => cpSync(from, to, {
      recursive: true,
      filter: (src) => !skip.has(basename(src)) && !src.endsWith(".bak"),
    });
    const eventDir = join(root, "despacio");
    copy(join(REPO, "events", "despacio"), eventDir);
    let showDir: string | null = null;
    if (options.showDir) {
      showDir = join(root, "shows");
      copy(join(REPO, "shared", "show-example"), showDir);
    }
    const engine = new Engine(root, await freePort(), options.artnetPort,
                              options.token === undefined ? "e2e" : options.token,
                              eventDir, showDir, options.syncPort ? await freeUdpPort() : null);
    await engine.launch();
    return engine;
  }

  private get stopFile(): string {
    return join(this.root, "stop");
  }

  private async launch(): Promise<void> {
    const args = ["-m", "engine.server", "--event", this.eventDir,
                  "--port", String(this.port), "--bind", "127.0.0.1",
                  "--artnet", `127.0.0.1:${this.artnetPort}`, "--stop-file", this.stopFile,
                  ...(this.token === null ? ["--no-token"] : ["--token", this.token]),
                  ...(this.showDir ? ["--show-dir", this.showDir] : []),
                  ...(this.syncPort ? ["--sync-port", String(this.syncPort)] : [])];
    const proc = spawn(PYTHON, args, {
      cwd: REPO,
      // No show folder unless one was asked for: an operator's own
      // $KLIGHTS_SHOW_DIR must not leak into a test.
      env: { ...process.env, KLIGHTS_SHOW_DIR: "", PYTHONIOENCODING: "utf-8", PYTHONUNBUFFERED: "1" },
      stdio: ["ignore", "pipe", "pipe"],
    });
    this.proc = proc;
    proc.stdout?.on("data", (b: Buffer) => { this.log += b.toString(); });
    proc.stderr?.on("data", (b: Buffer) => { this.log += b.toString(); });

    const deadline = Date.now() + 20_000;
    for (;;) {
      if (proc.exitCode !== null) {
        throw new Error(`the engine exited (${proc.exitCode}) before serving:\n${this.log}`);
      }
      try {
        const res = await fetch(`http://127.0.0.1:${this.port}/`);
        if (res.ok) return;
      } catch {
        // not listening yet
      }
      if (Date.now() > deadline) throw new Error(`the engine never served:\n${this.log}`);
      await sleep(50);
    }
  }

  /** The console's address, with the token unless told otherwise. */
  url(hash = "", { token = this.token }: { token?: string | null } = {}): string {
    const query = token ? `?token=${encodeURIComponent(token)}` : "";
    return `http://127.0.0.1:${this.port}/${query}${hash ? `#${hash}` : ""}`;
  }

  /** One OSC message to the sync port, as a DJ bridge sends it. */
  async sendOsc(address: string, ...args: (number | string)[]): Promise<void> {
    if (!this.syncPort) throw new Error("this engine was started without syncPort");
    const pad = (b: Buffer) => Buffer.concat([b, Buffer.alloc(4 - (b.length % 4))]);
    let tags = ",";
    const body: Buffer[] = [];
    for (const a of args) {
      if (typeof a === "string") {
        tags += "s";
        body.push(pad(Buffer.from(a)));
      } else {
        tags += "f";
        const b = Buffer.alloc(4);
        b.writeFloatBE(a);
        body.push(b);
      }
    }
    const packet = Buffer.concat([pad(Buffer.from(address)), pad(Buffer.from(tags)), ...body]);
    const socket = createSocket("udp4");
    await new Promise<void>((ok, fail) =>
      socket.send(packet, this.syncPort!, "127.0.0.1", (err) => (err ? fail(err) : ok())));
    socket.close();
  }

  /** Stop the process, cleanly if it will. */
  async halt(): Promise<void> {
    const proc = this.proc;
    if (!proc || proc.exitCode !== null) return;
    writeFileSync(this.stopFile, "");
    const exited = new Promise<void>((ok) => proc.once("exit", () => ok()));
    const timedOut = await Promise.race([exited.then(() => false), sleep(8000).then(() => true)]);
    if (timedOut) {
      proc.kill("SIGKILL");
      await exited;
    }
  }

  /** The same engine back on the same port -- what a laptop's restart looks
   *  like to every phone connected to it. */
  async restart(): Promise<void> {
    await this.halt();
    await this.launch();
  }

  async stop(): Promise<void> {
    await this.halt();
    rmSync(this.root, { recursive: true, force: true });
  }
}
