"""The previz scene manifest: the contract the standalone previz app is built on.

Three things are guarded here, and the first is the reason the manifest exists.

1. **The decode the app does equals the decode the show does.** The manifest
   ships each head's resolved aim frame so the C++ app never re-derives a
   calibration. `scene.decode_aim` is that decode, written against the manifest;
   it is swept here against `geometry.decode` + `world_bearing` + `ray` for every
   despacio head AND for synthetic heads covering what despacio does not -- the
   zero elevation anchor, the "table" world flip, inverts, an uncalibrated head.

2. **Nothing a previz block says can stop the show.** Every malformed model,
   aim and optics key is a warning, never an exception.

3. **Only files the manifest names can be served**, and only from events/ and
   shared/.

Run: python engine/tests/test_scene.py
"""

import json
import math
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine import geometry as geo
from engine import rig as rigmod
from engine import scene
from engine import servo as servomod

failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


DESPACIO = REPO / "events" / "despacio"
WORDS = [0, 1, 255, 256, 8191, 12288, 32767, 32768, 32769, 40000, 52000, 65279, 65534, 65535]


def sweep_head(rig: rigmod.Rig, head: int) -> float:
    """Worst disagreement between the manifest decode and the show's, degrees
    (bearing/elevation) or unit-vector components (direction)."""
    block = scene._decode_block(rig, head)
    g = rig.geometry
    worst = 0.0
    for pan in WORDS:
        for tilt in WORDS:
            aim = g.decode(head, pan, tilt)
            want_bearing = g.world_bearing(head, aim)
            got_bearing, got_elev = scene.decode_aim(block, pan, tilt)
            worst = max(worst, abs(got_bearing - want_bearing), abs(got_elev - aim.elev_deg))
            _, ray = g.ray(head, aim)
            want = scene.direction(*ray)
            got = scene.beam_direction(got_bearing, got_elev)
            worst = max(worst, *(abs(a - b) for a, b in zip(got, want)))
    return worst


print("\n1. the manifest decode is the show's decode")
despacio = rigmod.load_rig(DESPACIO)
for i, head in enumerate(despacio.geometry.heads):
    worst = sweep_head(despacio, i)
    check(f"despacio {head.name}: {len(WORDS) ** 2} poses agree", worst < 1e-9, f"worst {worst:.2e}")


def synthetic(mode: str, *, calibrated=True, pan_invert=False, tilt_invert=False,
              facing=None) -> rigmod.Rig:
    """A one-head rig in `mode`. The despacio rig is four calibrated heads in
    "venue" mode, which never exercises the zero anchor, the world flip, or
    the uncalibrated branch -- the cases a C++ port gets wrong first."""
    head = geo.Head(name="synthetic", x=1000.0, z=2000.0, height=3000.0,
                    pan_range_deg=540.0, tilt_range_deg=270.0,
                    calibrated_ball_dmx=(200, 30) if calibrated else None,
                    pan_invert=pan_invert, tilt_invert=tilt_invert,
                    mount_facing_override=facing)
    geometry = geo.RigGeometry(heads=(head,), ball=(4572.0, 2743.0, 4572.0), mount_mode=mode)
    return rigmod.Rig(name="synthetic", fixtures=(), geometry=geometry)


cases = []
for mode in geo.MOUNT_PROFILES:
    for pan_invert in (False, True):
        for tilt_invert in (False, True):
            cases.append((mode, dict(pan_invert=pan_invert, tilt_invert=tilt_invert)))
    cases.append((mode, dict(calibrated=False)))
    cases.append((mode, dict(calibrated=False, facing=-358.0)))
anchors = set()
for mode, kwargs in cases:
    rig = synthetic(mode, **kwargs)
    anchors.add(scene._decode_block(rig, 0)["elevation_anchor"])
    worst = sweep_head(rig, 0)
    check(f"synthetic {mode} {kwargs}", worst < 1e-9, f"worst {worst:.2e}")
check("the synthetic heads cover both elevation anchors", anchors == {"center", "zero"})


print("\n2. the despacio manifest")
built = scene.build_for(DESPACIO)
m = built.manifest
check("format and version", m["format"] == scene.FORMAT and m["version"] == scene.VERSION)
kinds = [f["kind"] for f in m["fixtures"]]
check("four movers and two fixed fixtures", kinds.count("mover") == 4 and kinds.count("static") == 2,
      str(kinds))
check("nothing unplaced, nothing warned", not m["unplaced"] and not m["warnings"],
      f"{m['unplaced']} {m['warnings']}")
check("the room is [X=depth, Y=width, Z=height] cm", m["room"]["size"] == [1828.8, 1828.8, 690.0])
check("serialises without NaN, canonically", built.to_json() == scene._canonical(m))
check("the revision is stable", scene.build_for(DESPACIO).rev == built.rev)

for f in m["fixtures"]:
    if f["kind"] != "mover":
        continue
    fixture = despacio.by_id(f["fid"])
    want = servomod.for_fixture(fixture, despacio.geometry.heads[fixture.head])._rates()
    check(f"{f['name']} servo rates", (f["servo"]["pan_words_per_s"], f["servo"]["tilt_words_per_s"]) == want)
    # The rest pose is the calibrated ball aim, so a scene with no DMX yet
    # shows every head on the ball -- the cheapest calibration check there is.
    d = scene.forward_vector(*f["rest_rotation"][:2])
    to_ball = [b - o for b, o in zip(m["ball"]["location"], f["location"])]
    n = math.sqrt(sum(v * v for v in to_ball))
    check(f"{f['name']} rests on the ball", all(abs(a - b / n) < 1e-9 for a, b in zip(d, to_ball)))

for f in m["fixtures"]:
    if f["kind"] == "static":
        d = scene.forward_vector(*f["rest_rotation"][:2])
        to_ball = [b - o for b, o in zip(m["ball"]["location"], f["location"])]
        n = math.sqrt(sum(v * v for v in to_ball))
        check(f"{f['name']} aims at the ball by default",
              all(abs(a - b / n) < 1e-9 for a, b in zip(d, to_ball)))

print("\n3. a captured frame decodes as the editor driver drew it")
frame = json.loads((REPO / "engine" / "tests" / "data" / "ball_frame.json").read_text())["0"]
for f in m["fixtures"]:
    level, color, split = scene.fixture_output(f, frame)
    if f["kind"] == "mover":
        # Wheel 34 is the blue slot and the dimmer is at 230.
        check(f"{f['name']}: blue at 230/255", math.isclose(level, 230 / 255)
              and color == (0.0, 0.0, 1.0) and split is None, f"{level} {color}")
        pan = scene.channel_word(f, frame, rigmod.PAN, rigmod.PAN_FINE)
        tilt = scene.channel_word(f, frame, rigmod.TILT, rigmod.TILT_FINE)
        bearing, elev = scene.decode_aim(f["decode"], pan, tilt)
        rest_pitch, rest_yaw, _ = f["rest_rotation"]
        # The capture is the ball pose read off 8-bit faders, so it lands
        # within one coarse step of the calibrated aim, not exactly on it.
        check(f"{f['name']}: the captured pose is the ball pose",
              abs(geo.norm180(bearing - rest_yaw)) < 2.5 and abs(elev - rest_pitch) < 2.5,
              f"bearing {bearing:.2f} vs {rest_yaw:.2f}, elev {elev:.2f} vs {rest_pitch:.2f}")
    else:
        check(f"{f['name']}: level is the color's magnitude",
              math.isclose(level, 138 / 255) and max(color) == 1.0, f"{level} {color}")


print("\n4. previz data never stops the show")


def event_with(venue_previz=None, fixture_extra=None) -> Path:
    """A throwaway copy of despacio with its room inlined, so both can be edited."""
    tmp = Path(tempfile.mkdtemp(prefix="klights-scene-"))
    rig = json.loads((DESPACIO / "rig.json").read_text(encoding="utf-8"))
    rig.pop("venue", None)
    for entry in rig["fixtures"]:
        entry.update((fixture_extra or {}).get(entry["name"], {}))
    (tmp / "rig.json").write_text(json.dumps(rig), encoding="utf-8")
    shutil.copy(DESPACIO / "calibration.json", tmp / "calibration.json")
    venue = json.loads((REPO / "shared" / "venues" / "despacio-room.json").read_text(encoding="utf-8"))
    if venue_previz is not None:
        venue["previz"] = venue_previz
    (tmp / "venue.json").write_text(json.dumps(venue), encoding="utf-8")
    return tmp


def warned(previz=None, extra=None):
    tmp = event_with(previz, extra)
    try:
        return scene.build_for(tmp).manifest
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


got = warned({"optics": {"fog_densty": 0.5, "beam_gain": "bright", "dot_gain": 3.0}})
check("an unknown optics key is a warning", any("fog_densty" in w for w in got["warnings"]))
check("a non-number optics value is a warning", any("beam_gain" in w for w in got["warnings"]))
check("a good optics key still applies", got["optics"]["dot_gain"] == 3.0)
check("...and defaults fill the rest", got["optics"]["fog_density"] == scene.OPTICS["fog_density"])

got = warned("not an object")
check("a previz block that is not an object is a warning", any("previz" in w for w in got["warnings"]))

got = warned({"models": [{"file": "nowhere/missing.glb"}, {"file": "set.fbx"},
                         {"file": "test/axes.glb", "position": {"x": 1000, "y": 0, "z": 2000},
                          "rotation": {"yaw": 90}, "scale": 2},
                         "not an object",
                         {"file": "test/axes.glb", "position": {"x": "far"}}]})
check("a missing model is a warning", any("missing.glb" in w for w in got["warnings"]))
check("a non-GLB model is a warning", any("set.fbx" in w for w in got["warnings"]))
check("a malformed entry is a warning", any("models[3]" in w for w in got["warnings"]))
placed = got["models"]
check("only the good model is placed, from shared/models/", len(placed) == 1, str(placed))
check("a malformed position skips the model with a warning",
      any("models[4].position" in w for w in got["warnings"]))
if placed:
    first = placed[0]
    check("placed in Unreal axes and cm", first["location"] == [200.0, 100.0, 0.0])
    check("yaw passes straight through", first["rotation"] == [0.0, 90.0, 0.0])
    check("collides by default", first["collide"] is True and first["scale"] == 2.0)
    check("the asset is listed by hash", first["model"] in got["assets"]
          and got["assets"][first["model"]]["name"] == "axes.glb")

got = warned({"models": [{"file": "../../../../../../Windows/win.ini.glb"}]})
check("a path escaping the repo is not served", not got["models"] and got["warnings"])

got = warned(extra={"Pinspot #1": {"aim": {"x": 9144, "y": 0, "z": 9144}},
                    "Pinspot #2": {"aim": "the ball, please"}})
p1 = next(f for f in got["fixtures"] if f["name"] == "Pinspot #1")
p2 = next(f for f in got["fixtures"] if f["name"] == "Pinspot #2")
d = scene.forward_vector(*p1["rest_rotation"][:2])
target = scene.point(9144, 0, 9144)
to = [t - o for t, o in zip(target, p1["location"])]
n = math.sqrt(sum(v * v for v in to))
check("a fixture's `aim` point is honoured", all(abs(a - b / n) < 1e-9 for a, b in zip(d, to)))
check("a malformed `aim` is a warning, and falls back to the ball",
      any("Pinspot #2.aim" in w for w in got["warnings"]) and p2["rest_rotation"][0] < 0)

got = warned(extra={"Moving Head #1": {"body": {"model": "test/axes.glb",
                                                "nodes": {"lens": "z_axis"}}}})
body = next(f for f in got["fixtures"] if f["name"] == "Moving Head #1")["body"]
check("a rig body override resolves its model", body["model"] in got["assets"])
check("...and overrides only the nodes it names",
      body["nodes"] == {"base": "base", "yoke": "yoke", "head": "head", "lens": "z_axis"})
check("a fixture with no model still gets its .qxf size, cm",
      next(f for f in got["fixtures"] if f["name"] == "Pinspot #1")["body"]["size"] == [10.6, 18.7, 21.5])

before = scene.build_for(DESPACIO).rev
after = warned({"optics": {"fog_density": 0.5}})["rev"]
check("the revision changes when the look does", before != after)

print("\n5. the engine inspects every model before the app loads it")
import struct  # noqa: E402

sys.path.insert(0, str(REPO / "shared" / "tools"))
import gen_models  # noqa: E402


def glb(doc: dict, blob: bytes = b"") -> Path:
    """A .glb from a JSON document and a binary chunk, written to a temp file."""
    text = json.dumps(doc).encode()
    text += b" " * (-len(text) % 4)
    blob += b"\0" * (-len(blob) % 4)
    body = struct.pack("<I", len(text)) + b"JSON" + text
    if blob:
        body += struct.pack("<I", len(blob)) + b"BIN\0" + blob
    path = Path(tempfile.mkdtemp(prefix="klights-glb-")) / "model.glb"
    path.write_bytes(b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body)
    return path


def problems_of(path: Path) -> str:
    return " | ".join(scene.inspect_glb(path).problems)


for name in ("generic_moving_head.glb", "generic_can.glb"):
    info = scene.inspect_glb(REPO / "shared" / "models" / "fixtures" / name)
    check(f"{name} is clean", not info.problems, str(info.problems))
check("the moving head is a yoke > head chain",
      scene.inspect_glb(REPO / "shared" / "models" / "fixtures" / "generic_moving_head.glb").nodes["head"] == ("yoke", False))

big = gen_models.Glb()
big.node("stage", big.mesh("stage", [(gen_models.Geometry().box((400.0, 60.0, 300.0)), big.material("m"))]), root=True)
path = Path(tempfile.mkdtemp(prefix="klights-glb-")) / "cm.glb"
path.write_bytes(big.bytes())
check("a model exported in centimetres is called out", "centimetres" in problems_of(path), problems_of(path))
check("an external buffer is called out",
      "separate file" in problems_of(glb({"asset": {"version": "2.0"}, "buffers": [{"uri": "stage.bin", "byteLength": 4}]})))
check("an extension the runtime reader lacks is called out",
      "KHR_texture_basisu" in problems_of(glb({"asset": {"version": "2.0"}, "extensionsRequired": ["KHR_texture_basisu"]})))
png_header = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 8192, 8192) + b"\x08\x02\0\0\0"
check("a huge texture is called out",
      "8192x8192" in problems_of(glb({"asset": {"version": "2.0"}, "bufferViews": [{"buffer": 0, "byteLength": len(png_header)}],
                                      "images": [{"bufferView": 0, "mimeType": "image/png"}]}, png_header)))
heavy = {"asset": {"version": "2.0"}, "accessors": [{"count": 3_000_000, "type": "SCALAR", "componentType": 5125},
                                                     {"count": 10, "type": "VEC3", "componentType": 5126,
                                                      "min": [0, 0, 0], "max": [1, 1, 1]}],
         "meshes": [{"primitives": [{"attributes": {"POSITION": 1}, "indices": 0}]}], "nodes": [{"mesh": 0}]}
check("a model over the triangle budget is called out", "1,000,000 triangles" in problems_of(glb(heavy)), problems_of(glb(heavy)))
check("something that is not a .glb at all is called out",
      "not a binary glTF" in problems_of(Path(tempfile.mkstemp(suffix=".glb")[1])))

got = warned(extra={"Moving Head #1": {"body": {"model": "test/axes.glb"}}})
check("a mover whose body has no yoke and head is warned about",
      any("Moving Head #1.body" in w and "will not follow its beam" in w for w in got["warnings"]), str(got["warnings"]))
got = warned(extra={"Pinspot #1": {"body": {"model": "test/axes.glb"}}})
check("...but a fixed fixture's body needs none",
      not any("Pinspot #1.body" in w for w in got["warnings"]), str(got["warnings"]))

print("\n5b. a model the inspection chokes on, an event elsewhere, a re-export, a ball too fine")
import hashlib  # noqa: E402
import os  # noqa: E402

# Valid JSON that is not glTF-shaped once took the WHOLE scene down with it: an
# AttributeError out of inspect_glb was a 503 for every model in the room.
for label, doc in (("a JSON chunk that is a list", [1, 2]),
                   ("a node that is a number", {"asset": {"version": "2.0"}, "nodes": [5]}),
                   ("bounds that are words", {"asset": {"version": "2.0"},
                                              "accessors": [{"count": 3, "min": ["a"], "max": ["b"]}],
                                              "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}]})):
    try:
        found = problems_of(glb(doc))
        check(f"{label}: a problem with the model, not an exception", bool(found), found)
    except Exception as exc:                            # noqa: BLE001
        check(f"{label}: a problem with the model, not an exception", False, repr(exc))

tmp = event_with({"models": [{"file": "models/broken.glb"}, {"file": "models/stage.glb"}]})
try:
    (tmp / "models").mkdir()
    shutil.copy(glb({"asset": {"version": "2.0"}, "nodes": [5]}), tmp / "models" / "broken.glb")
    stage_glb = tmp / "models" / "stage.glb"
    shutil.copy(REPO / "shared" / "models" / "test" / "axes.glb", stage_glb)
    built = scene.build_for(tmp)
    m = built.manifest
    check("...and in a scene it is a warning, the scene still builds",
          any("broken.glb" in w and "could not be inspected" in w for w in m["warnings"]), str(m["warnings"]))
    # tmp is outside events/: an event the launcher's Browse can open.
    sha = next((s for s, a in m["assets"].items() if a["name"] == "stage.glb"), None)
    check("an event outside events/ has its own models served", sha is not None
          and not any("outside" in w for w in m["warnings"]), str(m["warnings"]))
    if sha is not None:
        check("read_model serves the bytes the hash names", built.read_model(sha) == stage_glb.read_bytes())
        check("...and nothing the scene does not name", built.read_model("0" * 64) is None)
        # A re-export that keeps the size and lands in the same mtime tick.
        st = stage_glb.stat()
        changed = bytearray(stage_glb.read_bytes())
        changed[-1] ^= 0xFF
        stage_glb.write_bytes(bytes(changed))
        os.utime(stage_glb, ns=(st.st_atime_ns, st.st_mtime_ns))
        stale = scene.build_for(tmp).manifest
        check("(the hash cache cannot see that re-export: why read_model checks)",
              any(a["name"] == "stage.glb" for s, a in stale["assets"].items() if s == sha))
        check("read_model will not serve it under the old hash", built.read_model(sha) is None)
        fresh = scene.build_for(tmp).manifest
        check("...and the next scene names its real hash",
              any(a["name"] == "stage.glb" and s == hashlib.sha256(changed).hexdigest()
                  for s, a in fresh["assets"].items()))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

got = warned({"ball": {"mirror_spacing_mm": 0, "reflect_spacing_mm": 0.45},
              "optics": {"fog_density": -1, "reflect_budget": 0}})
check("a ball spacing of 0 is refused, not divided by",
      got["ball"]["mirror_spacing_mm"] == scene.BALL["mirror_spacing_mm"]
      and any("mirror_spacing_mm should be" in w for w in got["warnings"]))
check("...and 0.45 where 45 was meant", got["ball"]["reflect_spacing_mm"] == scene.BALL["reflect_spacing_mm"])
check("a negative or zero optics value is refused",
      got["optics"]["fog_density"] == scene.OPTICS["fog_density"]
      and got["optics"]["reflect_budget"] == scene.OPTICS["reflect_budget"])
fit_warnings: list[str] = []
fitted = scene._fit_spacing(5.0, 2000.0, scene.MAX_MIRRORS, "mirror_spacing_mm", fit_warnings)
rings = max(6, round(math.pi * 2000.0 / fitted))
check("a big ball at a fine spacing is widened to the facet cap",
      2 * rings * rings <= scene.MAX_MIRRORS and bool(fit_warnings), f"{fitted} {fit_warnings}")
check("despacio's own ball is untouched", scene.build_for(DESPACIO).manifest["ball"]["mirror_spacing_mm"]
      == scene.BALL["mirror_spacing_mm"])

print("\n6. generated files are current")
import subprocess  # noqa: E402
for tool in ("gen_previz_parity.py", "gen_models.py"):
    result = subprocess.run([sys.executable, str(REPO / "shared" / "tools" / tool), "--check"],
                            capture_output=True, text=True)
    check(f"{tool} --check", result.returncode == 0, (result.stdout + result.stderr).strip())

print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("scene: all checks pass")
