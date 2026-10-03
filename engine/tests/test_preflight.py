"""scripts/preflight.py on a machine that is not the one the show was built on.

Two things, both about where fixture profiles come from:

- The rig step has to load the way a fresh clone will. The engine searches
  ~/QLC+/Fixtures and the gitignored qlcplus/ tree after shared/fixtures/, so a
  .qxf that lives only in one of those loads on the machine that has it and
  nowhere else -- and the preflight is run on exactly that machine, before
  leaving. This builds that trap with a stand-in home directory, shows the
  engine's own search order falls into it, and checks the step does not.
- despacio's venue checks used to FAIL wherever QLC+ was not installed (every
  container, CI runner and fresh laptop), over a fixture def nothing at the
  venue loads any more. They have to pass with no QLC+ at all.

Run: python engine/tests/test_preflight.py
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent

_spec = importlib.util.spec_from_file_location("preflight", REPO / "scripts" / "preflight.py")
preflight = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(preflight)

failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def run(cmd: list[str], home: Path) -> subprocess.CompletedProcess:
    # Path.home() reads USERPROFILE on Windows and HOME elsewhere. Point both at
    # the stand-in, so the result neither depends on nor touches the real one --
    # the development machine has a QLC+ user dir, CI does not.
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    return subprocess.run(cmd, cwd=REPO, env=env, capture_output=True,
                          text=True, errors="replace")


def tail(res: subprocess.CompletedProcess) -> str:
    return (res.stdout + res.stderr).strip()[-400:]


SAMPLE = REPO / "engine" / "tests" / "data" / "events" / "sample"
DESPACIO = REPO / "events" / "despacio"
LOCAL_MODEL = "Par 36 Only On This Machine"

tmp = Path(tempfile.mkdtemp(prefix="klights-preflight-"))
try:
    home = tmp / "home"
    local_fixtures = home / "QLC+" / "Fixtures"
    local_fixtures.mkdir(parents=True)

    print("\n1. every profile in the repo")
    for event in (DESPACIO, SAMPLE):
        res = run(preflight.rig_step(event).cmd, home)
        check(f"{event.name}: the rig step passes", res.returncode == 0, tail(res))

    print("\n2. a profile only this machine has")
    source = (REPO / "shared" / "fixtures" / "UKing-Par-36-Custom.qxf").read_text(encoding="utf-8")
    assert "<Model>Par 36 Custom</Model>" in source
    local_qxf = local_fixtures / "UKing-Par-36-Local.qxf"
    local_qxf.write_text(source.replace("<Model>Par 36 Custom</Model>",
                                        f"<Model>{LOCAL_MODEL}</Model>"), encoding="utf-8")
    event = tmp / "event"
    shutil.copytree(SAMPLE, event)
    cfg = json.loads((event / "rig.json").read_text(encoding="utf-8"))
    for fixture in cfg["fixtures"]:
        if fixture["name"] == "Stage Par":
            fixture["model"] = LOCAL_MODEL
    (event / "rig.json").write_text(json.dumps(cfg, indent=1), encoding="utf-8")

    res = run([sys.executable, "-c",
               "import sys; sys.path.insert(0, '.'); from pathlib import Path;"
               "from engine import rig; r = rig.load_rig(Path(sys.argv[1]));"
               "print([f.profile.path for f in r.fixtures if f.name == 'Stage Par'][0])",
               str(event)], home)
    check("the engine's own search order loads it, from the stand-in QLC+ dir",
          res.returncode == 0 and res.stdout.strip() == str(local_qxf), tail(res))

    res = run(preflight.rig_step(event).cmd, home)
    out = res.stdout + res.stderr
    check("the rig step fails", res.returncode == 1, tail(res))
    check("...naming the fixture and the model", "Stage Par" in out and LOCAL_MODEL in out)
    check("...and the file it is loading from instead", str(local_qxf) in out)
    check("...and the command that fixes it", "python -m engine.patch import" in out)
    check("...as a message, not a traceback", "Traceback" not in out)

    print("\n3. a profile nobody has")
    local_qxf.unlink()
    res = run(preflight.rig_step(event).cmd, home)
    out = res.stdout + res.stderr
    check("the rig step fails", res.returncode == 1, tail(res))
    check("...and says it is nowhere", "anywhere else on this machine" in out)

    print("\n4. despacio's venue checks with no QLC+ installed")
    shutil.rmtree(home / "QLC+")
    res = run([sys.executable, str(DESPACIO / "preflight.py")], home)
    verdict = next((ln.strip() for ln in res.stdout.splitlines() if "RESULT" in ln), "")
    check("pass", res.returncode == 0, tail(res) if res.returncode else verdict)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("preflight: all checks pass")
