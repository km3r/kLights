# Testing

Four layers, each proving something the others cannot:

| layer | run it | what it proves | needs |
|---|---|---|---|
| **Engine suites** | `python -m engine.tests` | every engine module, its failure paths, and the real server over a real socket | Python 3.10+ |
| **Fuzz** | (one of the engine suites) | hostile input at every door into a running show | Python |
| **UI units** | `cd ui && npm test` | the console and Studio, against a snapshot captured from a real engine | Node |
| **Browser e2e** | `cd ui && npm run e2e` | a tap in Chromium reaches the rig: the real engine, serving the committed bundle, checked on the Art-Net it sends | Python, Node, Chromium |

`python scripts/preflight.py` runs the engine suites before a show. The browser
suite is not in it: a show laptop has no Node, and does not need one. CI runs all
four, on Ubuntu and Windows (see [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)).

## Engine suites

```bash
python -m engine.tests              # everything
python -m engine.tests -k patch     # suites whose name contains "patch"
python -m engine.tests -v           # stream each suite's own output
python engine/tests/test_patch.py   # one suite, directly
```

Standalone scripts, stdlib only, no test framework. Each suite runs in its own
subprocess, because several install a real frame clock, bind real sockets and
set process-wide timing, and one suite's leftovers must never decide another's
result. The runner prints the tail of a failing suite, so a check's detail says
by how much it failed, not just that it did.

The suites assert **failure paths**: a refused command, a corrupt config, a
crash mid-write, a rig that cannot load. A test that only proves the happy path
passes when the feature is deleted.

Every suite that writes works on a **throwaway copy**. That covers `events/despacio`,
the venue library, the show folder and the fixture folder, redirected before
anything runs. A suite that crashed between a save and its cleanup must not leave
junk in tonight's show.

A few suites to know:

- **`test_server.py`** drives the real server over a real socket, through a
  WebSocket client written independently of the server's framing.
- **`test_patch.py`** tests the patch editor's rules, called directly. It also
  compares `check_addresses` with `shared/tools/validate_patch.py` on 300 random
  patches: one rule in two places, kept in step by the test.
- **`test_websocket.py`** sends the frames a well-behaved client never sends:
  every length encoding at its boundary, pings between fragments, lying lengths,
  peers that vanish mid-frame.
- **`test_cli.py`** checks that every documented entry point starts. It runs
  `python -m engine.demo` against a UDP socket and checks its packets, holds the
  engine's ArtDmx encoder and the bench sender and listener to one wire format,
  and runs the `--check` mode of every generator whose output is committed.
- **`test_geometry_parity.py`** and **`test_qlc_parity.py`** are load-bearing:
  aims against the code that drove the real show, and whole DMX frames against
  QLC+.

## Fuzz

`engine/tests/test_fuzz.py` generates the input nobody writes a test case for, at
the three doors into a running show:

1. **The DJ-sync port**, which is unauthenticated by design. Random, mutated and
   hostile-JSON datagrams go in. `sync.parse` must answer None for what it cannot
   use, and let nothing NaN, infinite, unbounded or off-whitelist through.
2. **Config and show-folder files.** Every schema and kind is mutated. The
   validator must say no or yes without raising. Whatever it *accepts*, the engine
   must load, because that is the reason the validator exists.
3. **Console commands.** Messages are built from the fields each `_cmd_*`
   handler reads, taken from `engine/server.py` itself, and sent through `submit()`
   onto the real output thread. Afterwards every frame must still render, the
   clock must stay a number, the snapshot must stay strict JSON (one `NaN` there
   and every phone's `JSON.parse` fails), and the DMX thread must still be alive.

Seeds are fixed, so CI and a failure report always agree on the input. To look
for new trouble instead of re-checking the old, shift every seed at once:

```bash
KLIGHTS_FUZZ_SEED=7 python engine/tests/test_fuzz.py
```

Every input the fuzz has found is pinned in its last section, so it fails by
name if it ever comes back. Add new finds there.

## UI units

```bash
cd ui && npm test          # vitest + jsdom
cd ui && npm run test:watch
```

The console is driven against `ui/src/__fixtures__/despacio.json`, a snapshot
captured from a real engine (`engine/tests/dump_snapshot.py`), through a mock
socket. Studio is driven against the example show folder. Its beat grid, blocks
and waves are held to fixtures the engine generates
(`engine/tests/dump_designer_fixtures.py`). `test_api.py` fails if those are
stale.

The assertions keep two questions apart: *given this state, does the UI show the
right thing* and *given this press, does it send the right command*. The UI is
server-authoritative and never predicts, so mixing the two would let a UI that
renders its own guesses pass both.

`src/test/scenes.test.ts` counts the projector's flashes rather than trusting
the formula. The strobe is sampled every millisecond, and rising edges are
counted in every one-second window, across the tempos a DJ plays. Three a second
is the photosensitive-epilepsy limit (see [`SAFETY.md`](SAFETY.md)).

## Browser e2e

```bash
cd ui
npm ci
npx playwright install chromium   # once; the build @playwright/test is pinned to
npm run e2e
npm run e2e -- -g "Blackout"      # one journey
npx playwright show-trace test-results/<test>/trace.zip   # after a failure
```

`ui/e2e/engine.ts` starts `python -m engine.server` for each test. It serves the
**committed** `ui/dist`, exactly as a show laptop does, with Art-Net sent to a
UDP socket that stands where the rig's node would be. Nothing is mocked between
a tap and a byte on the wire. Each engine runs on throwaway copies of the event
(and of `shared/show-example` for Studio), on free ports, and stops through
`--stop-file`. Set `KLIGHTS_PYTHON` to choose the interpreter.

What it covers:

- **The console:** Blackout, the Master fader, Panic, the cue list, a palette
  colour, tempo typed and from a DJ bridge, two consoles sharing state, a
  view-only console, and a console riding out an engine restart.
- **Layout:** a phone starts in Perform and a laptop in Design, no tab scrolls
  sideways, and the first visit offers the tour.
- **Studio:** the library, an inspector edit saved through the engine to the
  file on disk, and Studio driving the rig until a console takes it back.

Writing a journey:

- Use the fixtures in `ui/e2e/fixtures.ts`: `engine`, `artnet` and
  `openConsole(page)`. Set `test.use({ engineOptions: { showDir: true } })` for
  Studio and `{ syncPort: true }` for a DJ bridge.
- **Assert on the wire** wherever the claim is about the rig. Use
  `artnet.waitFor(what, predicate)` with the despacio addresses in `DESPACIO`.
  The UI updates at 10 Hz and the wire at 40 fps, so a check on the wire is the
  one that cannot be fooled by the screen.
- A page that logs an error, or makes a request that fails, fails its test. If a
  test expects one, name it with `allowedErrors` (console text) or
  `allowedFailures` (URL), and say why.
- **No retries**, on purpose. A race in the console or the engine is what this
  suite exists to find, and a retry would turn it into a pass.

## Measuring coverage

Coverage is a map of where to look, not a target. To measure it without adding a
dependency to the project:

```bash
# engine: coverage in a scratch venv, following subprocesses too
python -m venv /tmp/cov && /tmp/cov/bin/pip install coverage
printf '[run]\nparallel = True\ndata_file = /tmp/cov/data\nsource = engine,launcher,mcp,bridges,shared/tools\nomit = engine/tests/*\n' > /tmp/covrc
echo "import coverage; coverage.process_startup()" > "$(/tmp/cov/bin/python -c 'import site; print(site.getsitepackages()[0])')/cov.pth"
COVERAGE_PROCESS_START=/tmp/covrc /tmp/cov/bin/python -m engine.tests
/tmp/cov/bin/python -m coverage combine --rcfile=/tmp/covrc && /tmp/cov/bin/python -m coverage report --rcfile=/tmp/covrc --sort=miss

# ui
cd ui && npm i --no-save @vitest/coverage-v8@3.2 && npx vitest run --coverage --coverage.include='src/**'
```
