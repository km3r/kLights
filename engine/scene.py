"""
The room and the rig as a renderer sees them: the scene the previz app draws.

    python -m engine.scene events/despacio            # print the manifest
    python -m engine.scene despacio -o scene.json     # write it
    python -m engine.scene --self-test

The engine serves this at ``GET /api/previz/scene`` and the packaged previz app
(``previz/unreal``) builds its whole world from it. That makes this module the
CONTRACT between the two, versioned by ``FORMAT``/``VERSION``: the app refuses a
manifest whose version it does not know rather than guessing at one.

What is resolved here, in Python, and why
-----------------------------------------
The app is C++, and every piece of logic it re-implements is a piece that can
drift from the show. So the hard parts are worked out HERE and shipped as data:

  * **Each moving head's decode frame.** Backing a head's mount facing and its
    elevation zero-error out of a hand-aimed ball reading is `geometry`'s
    `_build_frame` -- the part with every subtle trap in it (the unwrapped
    bearing delta, Python's ``%`` that C's ``fmod`` is not). The manifest
    carries its RESULT, so the app's decode is two linear maps and a sine.
    `decode_aim` below is the same maps, written against the manifest, and the
    tests prove it equals `geometry.decode` for every head.
  * **Servo rates** in the 16-bit words the yokes are addressed in.
  * **Optics**: the room's tuned look, merged over the defaults below.
  * **Models**: every glTF file resolved, contained, and content-hashed. The
    app fetches by hash, so a model cannot be served that the manifest did not
    name, and an unchanged model is never fetched twice.

What stays in the app is how things LOOK -- brightness normalisation, the
mirror ball's reflections, the beam meshes. That is the renderer's job, and the
renderer owns it.

**Nothing in a previz block can stop the show.** Every malformed model, aim or
optics key becomes a line in ``warnings``; this module never raises on previz
data. The rig, venue and calibration it reads were already validated by the
loaders the show itself runs on.

Frame conversion, which is the only subtle thing in the geometry
---------------------------------------------------------------
The show's world frame is **millimetres, y up**, origin at the room's front-left
floor corner, with bearing 0 deg = +z and 90 deg = +x (`geometry.bearing_between`
is ``atan2(dx, dz)``).

Unreal is **centimetres, Z up**, X forward, Y right, and a rotator's yaw turns
the forward vector from +X toward +Y while positive pitch lifts it toward +Z.

Mapping ``UE_X = z``, ``UE_Y = x``, ``UE_Z = y`` (and dividing by 10) makes those
line up exactly: **UE yaw = world bearing** and **UE pitch = world elevation**,
no sign flips, no offsets. `head_rotation` is the one place an aim becomes a
rotator, and the self-test asserts it against `geometry.ray`. If someone
"tidies" the mapping into UE_X = x, every identity breaks and the previz points
beams at the wrong walls -- convincingly.

So the room's size is given as ``size = [X, Y, Z]`` in Unreal's own axes:
**X spans the venue's depth, Y its width**. The old editor builder carried
``width``/``depth`` and mixed them up in two places, which a square room hides.
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:          # run as a script by `python -m engine.tests`
    sys.path.insert(0, str(REPO))

from engine import geometry as geo     # noqa: E402
from engine import rig as rigmod       # noqa: E402
from engine import servo as servomod   # noqa: E402

FORMAT = "klights-previz-scene"
VERSION = 1

# Model files may only come from these trees. The engine serves whatever the
# manifest names, so containment is a security property, not tidiness.
MODEL_ROOTS = (REPO / "events", REPO / "shared")
SHARED_MODELS = REPO / "shared" / "models"
BODIES_JSON = REPO / "shared" / "fixtures" / "bodies.json"


# ------------------------------------------------------------ frame mapping --

def mm(value: float) -> float:
    """A millimetre length as Unreal centimetres."""
    return float(value) / 10.0


def point(x: float, y: float, z: float) -> list[float]:
    """A world point (mm, y up) as an Unreal location [X, Y, Z] in cm."""
    return [mm(z), mm(x), mm(y)]


def direction(x: float, y: float, z: float) -> list[float]:
    """A world DIRECTION as an Unreal one: the axis permutation, no scale.

    A direction is a difference of two points divided by its own length, so
    scaling it by the mm->cm factor again would only un-normalise it.
    """
    return [float(z), float(x), float(y)]


def head_rotation(bearing_deg: float, elev_deg: float) -> list[float]:
    """A world aim as an Unreal rotator [pitch, yaw, roll], degrees.

    Both components pass straight through under this module's axis mapping --
    see the docstring. A function anyway, so there is one place to fix if the
    mapping ever moves, and something for the self-test to check.
    """
    return [float(elev_deg), float(bearing_deg), 0.0]


def forward_vector(pitch_deg: float, yaw_deg: float) -> tuple[float, float, float]:
    """Unreal's own rotator -> direction convention."""
    p, y = math.radians(pitch_deg), math.radians(yaw_deg)
    return (math.cos(p) * math.cos(y), math.cos(p) * math.sin(y), math.sin(p))


def aim_from(origin, target) -> tuple[float, float]:
    """(bearing, elevation) in degrees from one world point to another.

    Built on `geometry.bearing_between` rather than a second atan2 written out
    here, because two spellings of one convention is how they drift apart.
    """
    ox, oy, oz = origin
    tx, ty, tz = target
    return (geo.bearing_between(ox, oz, tx, tz),
            math.degrees(math.atan2(ty - oy, math.hypot(tx - ox, tz - oz))))


# ------------------------------------------------------------------ optics --

# How the room renders. These were constants scattered through the editor
# driver and builder; they are config now, merged per KEY under a venue's
# `previz.optics`, so a room that only needs different fog says only that.
#
# Every one was eyeballed against a single photograph of despacio, which is
# honest for despacio and nothing at all for a second room. The re-sweep
# procedure is in docs/models.md.
OPTICS: dict[str, float] = {
    # Fog extinction along a beam, per metre: what is left after d metres is
    # exp(-k d). A beam you can see in haze is one losing light.
    "beam_extinction_per_m": 0.09,
    # Emissive gain on a beam's shaft mesh. Additive over a near-black room
    # saturates fast, so this is what keeps a beam's colour judgeable.
    "beam_gain": 1.4,
    # The mirror ball's dots on the walls, and the shafts that reach them.
    "dot_gain": 2.3,
    "ray_gain": 1.8,
    # A reflected shaft dimmer than this (about one 8-bit level) is not drawn.
    "ray_floor": 1.0 / 255.0,
    # Brightness RATIOS between fixtures on the stand-in meshes are compressed
    # as ratio ** contrast: Bulb Lumens overstate how much brighter a narrow
    # beam is, and a pinned exposure removes the eye's own adaptation.
    "mesh_contrast": 0.22,
    # The most reflections one fixture draws per frame.
    "reflect_budget": 220.0,
    # How far a dot floats off the wall it lands on, cm.
    "dot_lift_cm": 1.0,
    # What the ball sprays back into the room, as a fraction of a fixture's own
    # output, and how hard that light works on the haze.
    "ball_glow_fraction": 140.0 / 1300.0,
    "ball_glow_scatter": 1.0,
    # The room's surfaces: diffuse reflectance, and a self-lit floor (as a
    # multiple of the albedo) so an unlit wall is not the same as no wall.
    "room_albedo": 0.16,
    "room_emissive": 0.35,
    # The haze.
    "fog_density": 0.35,
    "fog_height_falloff": 0.005,
    "fog_scattering_distribution": 0.4,
    # Ambient sky light, kept just above black.
    "sky_light": 0.15,
    # Pinned exposure and bloom.
    "exposure": 1.0,
    "bloom_intensity": 0.6,
    "bloom_threshold": 0.4,
    # Each fixture's own volumetric scatter, and its shadow-map multiplier. The
    # latter is NOT a quality dial: at 1.0 a 3-degree beam's shadow map is too
    # coarse to hold the ball's shadow at all, and 2.0 is the engine's ceiling.
    "spot_scattering": 2.5,
    "shadow_resolution_scale": 2.0,
    # Output at full for a fixture whose profile declares no Bulb Lumens.
    "undeclared_lumens": 400.0,
}

# The mirror ball. Given as facet SIZES, not counts: a real ball keeps its
# mirror size and gains more of them as it grows. `previz/mirrorball.py` is
# where the reasoning for each number lives.
BALL: dict[str, float] = {
    # The motor. ASSUMED: nothing records the real one.
    "rpm": 2.0,
    # The mirrors the ball wears (drawn once), and the coarser sample light is
    # computed on (each costs a shaft and a dot per frame).
    "mirror_spacing_mm": 22.0,
    "reflect_spacing_mm": 45.0,
    # The fixture lens's diameter, which sets a reflected dot's size. The one
    # number in the ball model that is neither measured nor derived.
    "aperture_mm": 40.0,
    # How much of its share of the ball a tile covers (the rest is grout), and
    # how far it floats off the core, cm.
    "tile_coverage": 0.92,
    "tile_lift_cm": 0.15,
}

# Roles the previz reads, by 0-based index into the fixture's universe frame.
PREVIZ_ROLES = (
    rigmod.PAN, rigmod.PAN_FINE, rigmod.TILT, rigmod.TILT_FINE,
    rigmod.DIMMER, rigmod.RED, rigmod.GREEN, rigmod.BLUE, rigmod.WHITE,
    rigmod.COLOR_WHEEL, rigmod.STROBE,
)


def _merged(defaults: dict[str, float], block: Any, where: str,
            warnings: list[str]) -> dict[str, float]:
    """`defaults` overridden key by key from `block`, warning about the rest.

    Unknown keys are WARNED rather than ignored: a typo in an optics key is
    otherwise a value that silently does nothing, and "my change had no effect"
    is the hardest kind of nothing to debug. `_`-prefixed keys are notes.
    """
    out = dict(defaults)
    if block is None:
        return out
    if not isinstance(block, dict):
        warnings.append(f"{where} should be an object; using the defaults")
        return out
    for key, value in block.items():
        if key.startswith("_"):
            continue
        if key not in defaults:
            warnings.append(f"{where}.{key} is not a known setting "
                            f"(known: {', '.join(sorted(defaults))})")
        elif isinstance(value, bool) or not isinstance(value, (int, float)) \
                or not math.isfinite(value):
            warnings.append(f"{where}.{key} should be a number, got {value!r}")
        elif not _in_range(key, value):
            lo, hi = LIMITS.get(key, (0.0, math.inf))
            span = f"at least {lo:g}" if hi == math.inf else f"{lo:g} to {hi:g}"
            warnings.append(f"{where}.{key} should be {span}, got {value!r}; "
                            f"using {defaults[key]:g}")
        else:
            out[key] = float(value)
    return out


# The range a setting may take; anything not listed may not be negative. Outside
# these a value is not a different look but a broken app: a facet spacing of 0
# divides by zero, and 0.22 where 22 was meant asks for sixteen million tiles.
LIMITS: dict[str, tuple[float, float]] = {
    "rpm": (-60.0, 60.0),
    "mirror_spacing_mm": (5.0, 500.0),
    "reflect_spacing_mm": (10.0, 500.0),
    "aperture_mm": (1.0, 1000.0),
    "tile_coverage": (0.05, 1.0),
    "tile_lift_cm": (0.0, 10.0),
    "reflect_budget": (1.0, 5000.0),
    "shadow_resolution_scale": (0.1, 2.0),
}
# How many facets a ball may have even inside those ranges: a big ball at a fine
# spacing is legal and still too many. Mirrors are instances drawn once; the
# reflect lattice is traced every frame.
MAX_MIRRORS = 100_000
MAX_REFLECTORS = 20_000


def _in_range(key: str, value: float) -> bool:
    lo, hi = LIMITS.get(key, (0.0, math.inf))
    return lo <= value <= hi


def _fit_spacing(spacing: float, radius_mm: float, most: int, key: str,
                 warnings: list[str]) -> float:
    """`spacing`, widened if the ball would otherwise have more than `most`
    facets. Uses the app's own lattice rule (Ball::Lattice)."""
    rings = max(6, round(math.pi * radius_mm / spacing))
    if 2 * rings * rings <= most:
        return spacing
    fitted = math.pi * radius_mm / math.floor(math.sqrt(most / 2))
    warnings.append(f"previz.ball.{key} {spacing:g} mm on a {radius_mm:g} mm ball is "
                    f"{2 * rings * rings:,} facets; using {fitted:.1f} mm ({most:,} at most)")
    return fitted


# ------------------------------------------------------------------ models --

_HASHES: dict[tuple[str, int, int], str] = {}


def _sha256(path: Path) -> str:
    """Content hash, cached on (path, mtime, size): the scene is rebuilt on
    every poll, and re-reading a 30 MB venue model each time would be absurd.

    The cache can be fooled by a re-export that keeps the size and lands in the
    same mtime tick. `read_model` is what catches that, at the one moment it
    matters: when the bytes are about to be sent under this hash.
    """
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size)
    found = _HASHES.get(key)
    if found is None:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
        found = digest.hexdigest()
        _HASHES[key] = found
    return found


def _forget(path: Path) -> None:
    """Drop every cached hash and inspection of `path`, so the next scene
    rebuild reads it afresh."""
    for cache in (_HASHES, _INSPECTED):
        for key in [k for k in cache if k[0] == str(path)]:
            del cache[key]


def _contained(path: Path, roots: tuple[Path, ...] = MODEL_ROOTS) -> bool:
    for root in roots:
        try:
            path.relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False


# ---------------------------------------------------------------- inspection --
#
# The engine's half of loading a model: everything that can be known about a
# .glb without Unreal, checked while the scene is built, so a problem is a line
# in the scene's warnings -- on the app's overlay and in `previz doctor` --
# rather than a model that silently fails to load, or a moving head whose body
# quietly stops following its beam.

# Not limits: a heavier model still loads. It loads SLOWLY -- a colliding mesh
# has its collision cooked on the game thread, a hitch while the room builds --
# and the warning says which one.
MODEL_TRIANGLE_BUDGET = 500_000
MODEL_TEXTURE_BUDGET = 4096                  # pixels, either side
# glTF is metres. Outside this, a model was almost certainly exported in
# centimetres (100x too big) or millimetres (1000x).
MODEL_EXTENT_M = (0.01, 250.0)
BODY_EXTENT_M = 3.0
# Extensions Unreal's runtime glTF reader (GLTFCore) understands. A model that
# REQUIRES anything else will not load as intended.
SUPPORTED_EXTENSIONS = frozenset({
    "KHR_draco_mesh_compression", "KHR_mesh_quantization", "KHR_texture_transform",
    "KHR_materials_emissive_strength", "KHR_materials_unlit", "KHR_lights_punctual",
    "KHR_materials_variants", "KHR_materials_ior", "KHR_materials_specular",
    "KHR_materials_transmission", "KHR_materials_clearcoat", "KHR_materials_sheen",
})


@dataclass(frozen=True)
class ModelInfo:
    """What a .glb holds, as far as the previz cares."""
    triangles: int
    extent_m: float                                       # largest side of any mesh, metres
    nodes: dict[str, tuple[Optional[str], bool]]          # name -> (parent's name, rotated at rest)
    problems: tuple[str, ...]


_INSPECTED: dict[tuple[str, int, int], ModelInfo] = {}


def _image_size(blob: bytes) -> Optional[tuple[int, int]]:
    """(width, height) of a PNG or JPEG, from its header alone."""
    if blob[:8] == b"\x89PNG\r\n\x1a\n" and len(blob) >= 24:
        return struct.unpack(">II", blob[16:24])
    if blob[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(blob):
            if blob[i] != 0xFF:
                i += 1
                continue
            marker = blob[i + 1]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                height, width = struct.unpack(">HH", blob[i + 5:i + 9])
                return width, height
            i += 2 + struct.unpack(">H", blob[i + 2:i + 4])[0]
    return None


def inspect_glb(path: Path) -> ModelInfo:
    """Read a .glb's header and JSON and say what is wrong with it, if anything.

    Cached on (path, mtime, size), like the hash: the scene is rebuilt on every
    poll and a model is only re-read when it changes. Never raises: a file the
    inspection cannot make sense of -- JSON that is valid but not glTF-shaped,
    a node that is a number, a file that vanished -- is one more problem with
    that model, not an exception that takes the whole scene down with it.
    """
    try:
        st = path.stat()
    except OSError as exc:
        return ModelInfo(0, 0.0, {}, (f"could not be read ({exc.strerror or exc})",))
    key = (str(path), st.st_mtime_ns, st.st_size)
    if key not in _INSPECTED:
        try:
            info = _inspect(path.read_bytes())
        except Exception as exc:                      # noqa: BLE001 -- see docstring
            info = ModelInfo(0, 0.0, {}, (f"could not be inspected ({type(exc).__name__}: "
                                          f"{exc}) -- is it a valid glTF?",))
        _INSPECTED[key] = info
    return _INSPECTED[key]


def _inspect(data: bytes) -> ModelInfo:
    if len(data) < 20 or data[:4] != b"glTF":
        return ModelInfo(0, 0.0, {}, ("is not a binary glTF -- export it as a single .glb",))
    problems: list[str] = []
    if struct.unpack_from("<I", data, 4)[0] != 2:
        problems.append("is not glTF 2.0")
    json_length, json_type = struct.unpack_from("<I4s", data, 12)
    if json_type != b"JSON":
        return ModelInfo(0, 0.0, {}, ("has no JSON chunk where a .glb must have one",))
    try:
        doc = json.loads(data[20:20 + json_length])
    except (ValueError, UnicodeDecodeError):
        return ModelInfo(0, 0.0, {}, ("has a JSON chunk that is not JSON",))
    if not isinstance(doc, dict):
        return ModelInfo(0, 0.0, {}, ("has a JSON chunk that is not a glTF object",))
    bin_start = 20 + json_length
    blob = b""
    if bin_start + 8 <= len(data):
        bin_length, bin_type = struct.unpack_from("<I4s", data, bin_start)
        if bin_type == b"BIN\x00":
            blob = data[bin_start + 8:bin_start + 8 + bin_length]

    for kind in ("buffers", "images"):
        for entry in doc.get(kind, []):
            uri = entry.get("uri")
            if uri and not uri.startswith("data:"):
                problems.append(f"refers to a separate file ({uri}), which the app is never sent "
                                f"-- export with everything embedded")
    missing = set(doc.get("extensionsRequired", [])) - SUPPORTED_EXTENSIONS
    if missing:
        problems.append(f"requires {', '.join(sorted(missing))}, which Unreal's runtime reader "
                        f"does not support")

    accessors = doc.get("accessors", [])

    def count(index) -> int:
        return accessors[index].get("count", 0) if isinstance(index, int) and index < len(accessors) else 0

    per_mesh, extent, skipped = [], 0.0, 0
    for mesh in doc.get("meshes", []):
        triangles = 0
        for primitive in mesh.get("primitives", []):
            mode = primitive.get("mode", 4)
            position = primitive.get("attributes", {}).get("POSITION")
            n = count(primitive["indices"]) if "indices" in primitive else count(position)
            if mode == 4:
                triangles += n // 3
            elif mode in (5, 6):
                triangles += max(0, n - 2)
            else:
                skipped += 1                          # points and lines
            if isinstance(position, int) and position < len(accessors):
                lo, hi = accessors[position].get("min"), accessors[position].get("max")
                if lo and hi:
                    extent = max(extent, *(b - a for a, b in zip(lo, hi)))
        per_mesh.append(triangles)
    if skipped:
        problems.append(f"has {skipped} point or line primitive(s); only triangles are drawn")

    nodes_json = doc.get("nodes", [])
    parent_of = {child: i for i, node in enumerate(nodes_json) for child in node.get("children", [])}
    names = [node.get("name") or f"node{i}" for i, node in enumerate(nodes_json)]
    nodes: dict[str, tuple[Optional[str], bool]] = {}
    total = 0
    for i, node in enumerate(nodes_json):
        rotation = node.get("rotation")
        rotated = "matrix" in node or (rotation is not None and
                                       any(abs(a - b) > 1e-6 for a, b in zip(rotation, (0, 0, 0, 1))))
        nodes[names[i]] = (names[parent_of[i]] if i in parent_of else None, rotated)
        mesh = node.get("mesh")
        if isinstance(mesh, int) and mesh < len(per_mesh):
            total += per_mesh[mesh]
    if total > MODEL_TRIANGLE_BUDGET:
        problems.append(f"has {total:,} triangles (budget {MODEL_TRIANGLE_BUDGET:,}): it will load, "
                        f"slowly, and a colliding one hitches the room while it builds")
    if extent and not (MODEL_EXTENT_M[0] <= extent <= MODEL_EXTENT_M[1]):
        unit = "centimetres or millimetres" if extent > MODEL_EXTENT_M[1] else "the wrong unit"
        problems.append(f"is {extent:g} across -- glTF is metres, so this was probably "
                        f"exported in {unit}")
    if doc.get("skins") or doc.get("animations"):
        problems.append("is skinned or animated; it is drawn in its rest pose")

    views = doc.get("bufferViews", [])
    for i, image in enumerate(doc.get("images", [])):
        view = image.get("bufferView")
        if not isinstance(view, int) or view >= len(views):
            continue
        start = views[view].get("byteOffset", 0)
        size = _image_size(blob[start:start + views[view].get("byteLength", 0)])
        if size and max(size) > MODEL_TEXTURE_BUDGET:
            problems.append(f"has a {size[0]}x{size[1]} texture (image {i}); above "
                            f"{MODEL_TEXTURE_BUDGET} it costs memory the previz does not need")
    return ModelInfo(total, extent, nodes, tuple(problems))


@dataclass
class _Models:
    """Every model file the manifest names, by hash.

    `roots` is where a model may live: events/ and shared/, plus the folders of
    the event and venue being drawn -- an event opened from somewhere else (the
    launcher can browse to one) still gets its own models served. The person
    who started the engine chose those folders; nothing a client sends can.
    """
    roots: tuple[Path, ...] = MODEL_ROOTS
    files: dict[str, Path] = field(default_factory=dict)
    sizes: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    info: dict[str, ModelInfo] = field(default_factory=dict)

    def resolve(self, name: Any, base: Path, where: str) -> Optional[str]:
        """The sha256 of the model `name` names, or None with a warning.

        Relative to `base` (the directory of the file that names it), then
        `shared/models/`. GLB only: the app's runtime reader takes a single
        self-contained file, and a .gltf with external buffers would arrive
        without them.
        """
        if not isinstance(name, str) or not name.strip():
            self.warnings.append(f"{where}: model should be a file name, got {name!r}")
            return None
        if not name.lower().endswith(".glb"):
            self.warnings.append(f"{where}: {name} is not a .glb -- export a "
                                 f"binary glTF (one self-contained file)")
            return None
        for candidate in (base / name, SHARED_MODELS / name):
            resolved = candidate.resolve()
            if resolved.is_file():
                if not _contained(resolved, self.roots):
                    self.warnings.append(f"{where}: {name} resolves outside events/, "
                                         f"shared/ and this event's folder, so it is not served")
                    return None
                try:
                    sha = _sha256(resolved)
                    self.sizes[sha] = resolved.stat().st_size
                except OSError as exc:
                    self.warnings.append(f"{where}: {name} could not be read "
                                         f"({exc.strerror or exc})")
                    return None
                if sha not in self.info:
                    # Once per model, however many fixtures use it.
                    self.info[sha] = inspect_glb(resolved)
                    self.warnings.extend(f"{where}: {resolved.name} {problem}"
                                         for problem in self.info[sha].problems)
                self.files[sha] = resolved
                return sha
        self.warnings.append(f"{where}: no such model {name} (looked in "
                             f"{base} and {SHARED_MODELS})")
        return None


def _vector(value: Any, where: str, warnings: list[str],
            keys=("x", "y", "z")) -> Optional[tuple[float, ...]]:
    if not isinstance(value, dict):
        warnings.append(f"{where} should be an object with {', '.join(keys)}")
        return None
    out = []
    for key in keys:
        v = value.get(key, 0.0 if keys != ("x", "y", "z") else None)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            warnings.append(f"{where}.{key} should be a number, got {v!r}")
            return None
        out.append(float(v))
    return tuple(out)


def _rotation(value: Any, where: str, warnings: list[str]) -> list[float]:
    """{yaw, pitch, roll} degrees -> an Unreal rotator [pitch, yaw, roll].

    Yaw is a world BEARING (0 = +z, 90 = +x) -- which under this module's axis
    mapping is exactly Unreal yaw, so it passes straight through.
    """
    if value is None:
        return [0.0, 0.0, 0.0]
    got = _vector(value, where, warnings, keys=("pitch", "yaw", "roll"))
    return [0.0, 0.0, 0.0] if got is None else list(got)


def _bodies(warnings: list[str]) -> dict[str, Any]:
    """`shared/fixtures/bodies.json`: which model draws which profile."""
    if not BODIES_JSON.is_file():
        return {}
    try:
        data = json.loads(BODIES_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"{BODIES_JSON.name}: unreadable ({exc}); fixtures get stand-in bodies")
        return {}
    if not isinstance(data, dict):
        warnings.append(f"{BODIES_JSON.name} should be an object")
        return {}
    return {k: v for k, v in data.items() if not k.startswith("_")}


# ----------------------------------------------------------------- manifest --

@dataclass
class Scene:
    """A manifest and the files it may serve."""
    manifest: dict
    files: dict[str, Path]

    @property
    def rev(self) -> str:
        return self.manifest["rev"]

    def to_json(self) -> bytes:
        return _canonical(self.manifest)

    def read_model(self, sha: str) -> Optional[bytes]:
        """The bytes of model `sha`, or None if the file no longer hashes to it.

        Checked on every serve, because the app caches a model under its hash
        FOREVER: a re-export that slipped past the hash cache (same size, same
        mtime tick) would otherwise be stored under the old name and never
        fetched again. None also forgets the stale hash, so the next scene
        names the file's real one and the app fetches that.
        """
        path = self.files.get(sha)
        if path is None:
            return None
        try:
            body = path.read_bytes()
        except OSError:
            _forget(path)
            return None
        if hashlib.sha256(body).hexdigest() != sha:
            _forget(path)
            return None
        return body


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def unit_key(fixture: rigmod.PatchedFixture) -> str:
    """`h<head>` for a mover, `f<fid>` for anything else -- head 0 and fixture
    id 0 are different objects, and the prefix is what keeps them apart."""
    return f"h{fixture.head}" if fixture.head is not None else f"f{fixture.fid}"


def _color_slots(fixture: rigmod.PatchedFixture) -> list[list[int]]:
    """A mechanical wheel's slots as [lo, hi, r, g, b, r1, g1, b1, r2, g2, b2]:
    the slot's averaged colour, then the two halves actually in the aperture.
    The halves repeat the average on an ordinary single-colour slot."""
    for name in fixture.profile.modes[fixture.mode]:
        channel = fixture.profile.channels[name]
        if channel.role == rigmod.COLOR_WHEEL:
            return [[c.lo, c.hi, *c.rgb,
                     *(c.pair[0] if c.pair else c.rgb),
                     *(c.pair[1] if c.pair else c.rgb)]
                    for c in channel.color_slots]
    return []


def _decode_block(rig: rigmod.Rig, head: int) -> dict:
    """Head `head`'s resolved AimFrame: everything the decode needs, already
    derived from its calibration. See `decode_aim`."""
    f = rig.geometry.frame(head)
    res = rig.geometry.resolution
    return {
        "bearing_channel": f.bearing_channel,
        "elevation_channel": f.elevation_channel,
        "bearing_max": f.bearing_max,
        "elevation_max": f.elevation_max,
        "bearing_invert": f.bearing_invert,
        "elevation_invert": f.elevation_invert,
        "elevation_anchor": f.elevation_anchor,
        "elevation_offset": f.elevation_offset,
        "mount_facing": f.mount_facing,
        "half": res.half,
        "span": res.span,
    }


def _body(fixture: rigmod.PatchedFixture, bodies: dict, models: _Models,
          event_dir: Path, warnings: list[str]) -> dict:
    """What to draw for a fixture's housing.

    The rig's own `body` wins over `shared/fixtures/bodies.json`, which maps a
    profile ("Manufacturer/Model") to a model. With neither, the app draws a
    stand-in box sized from the .qxf's declared dimensions.
    """
    where = f"{fixture.name}.body"
    override = fixture.previz.get("body")
    entry = None
    base = event_dir
    if override is not None:
        if not isinstance(override, dict):
            warnings.append(f"{where} should be an object")
        else:
            entry = override
    def names_model(e):
        return e is not None and ("model" in e or "file" in e)

    if not names_model(entry):
        mapped = bodies.get(f"{fixture.profile.manufacturer}/{fixture.profile.model}")
        if mapped is not None:
            entry = {**mapped, **(entry or {})}
            base = BODIES_JSON.parent

    out: dict[str, Any] = {
        "model": None,
        "nodes": {"base": "base", "yoke": "yoke", "head": "head", "lens": "lens"},
        "rotation": None,
        "size": None,
    }
    if fixture.profile.dimensions is not None:
        w, h, d = fixture.profile.dimensions
        out["size"] = [mm(w), mm(h), mm(d)]
    if entry is None:
        return out
    if names_model(entry):
        # `model` in rig.json, `file` in bodies.json -- either is accepted in both.
        out["model"] = models.resolve(entry.get("model", entry.get("file")), base, where)
    nodes = entry.get("nodes")
    if isinstance(nodes, dict):
        out["nodes"].update({k: v for k, v in nodes.items()
                             if k in out["nodes"] and isinstance(v, str)})
    elif nodes is not None:
        warnings.append(f"{where}.nodes should be an object")
    if "rotation" in entry:
        out["rotation"] = _rotation(entry["rotation"], f"{where}.rotation", warnings)
    if out["model"] is not None:
        _check_body(fixture, out, models.info[out["model"]], models.files[out["model"]].name, warnings)
    return out


def _check_body(fixture: rigmod.PatchedFixture, body: dict, info: ModelInfo, name: str,
                warnings: list[str]) -> None:
    """Will the app be able to pose this body? Said here, before it tries."""
    where = f"{fixture.name}.body"
    if info.extent_m > BODY_EXTENT_M:
        warnings.append(f"{where}: {name} is {info.extent_m:g} m across -- a fixture, so "
                        f"probably exported in centimetres or millimetres")
    if fixture.head is None:
        return                                        # a fixed fixture is posed whole
    yoke, head = body["nodes"]["yoke"], body["nodes"]["head"]
    if yoke not in info.nodes or head not in info.nodes:
        warnings.append(f"{where}: {name} has no '{yoke}' and '{head}' nodes, so the head will "
                        f"not follow its beam (docs/models.md, 'Fixture bodies')")
    elif info.nodes[head][0] != yoke:
        warnings.append(f"{where}: in {name}, '{head}' must be a child of '{yoke}' for the "
                        f"head to tilt on the yoke")
    elif info.nodes[yoke][1] or info.nodes[head][1]:
        warnings.append(f"{where}: in {name}, '{yoke}' or '{head}' is rotated at rest; the body "
                        f"convention needs them unrotated, or the head points off its beam")


def build(rig: rigmod.Rig, event_dir: Path) -> Scene:
    """The manifest for an already-loaded rig.

    From a `Rig` rather than a directory so the running engine serves the rig
    it is actually driving -- including a venue edited live from the UI and not
    yet saved -- rather than whatever is on disk. `event_dir` is where the
    rig's own model paths are resolved from.
    """
    venue = rig.venue
    if venue is None:
        raise ValueError(f"{rig.name}: no venue, so there is no room to build")
    event_dir = Path(event_dir)
    venue_dir = rig.venue_file.parent if rig.venue_file else event_dir

    warnings: list[str] = list(rig.validate())
    models = _Models(roots=(*MODEL_ROOTS, event_dir.resolve(), venue_dir.resolve()))
    previz = venue.previz if venue.previz is not None else {}
    if not isinstance(previz, dict):
        warnings.append(f"venue {venue.name!r}: previz should be an object; ignored")
        previz = {}

    optics = _merged(OPTICS, previz.get("optics"), "previz.optics", warnings)
    ball_block = previz.get("ball")
    ball_model = None
    if isinstance(ball_block, dict):
        if "model" in ball_block:
            ball_model = models.resolve(ball_block["model"], venue_dir, "previz.ball.model")
        ball_block = {k: v for k, v in ball_block.items() if k != "model"}
    ball_cfg = _merged(BALL, ball_block, "previz.ball", warnings)
    for key, most in (("mirror_spacing_mm", MAX_MIRRORS), ("reflect_spacing_mm", MAX_REFLECTORS)):
        ball_cfg[key] = _fit_spacing(ball_cfg[key], venue.ball_radius, most, key, warnings)

    bodies = _bodies(warnings)
    fixtures: list[dict] = []
    unplaced: list[str] = []

    for f in rig.fixtures:
        channels = {role: f.index_of(role) for role in PREVIZ_ROLES
                    if f.index_of(role) is not None}
        common = {
            "fid": f.fid, "name": f.name, "key": unit_key(f),
            "manufacturer": f.profile.manufacturer, "model": f.profile.model,
            "mode": f.mode, "universe": f.universe, "address": f.address,
            "tags": list(f.tags),
            "beam_deg": f.output_beam_deg,
            "lumens": float(f.output_lumens or optics["undeclared_lumens"]),
            "channels": channels,
            "color_slots": _color_slots(f),
        }
        if f.head is not None and rig.geometry is not None:
            head = rig.geometry.heads[f.head]
            aim = rig.geometry.aim_at_ball(f.head)
            pan_rate, tilt_rate = servomod.for_fixture(f, head)._rates()
            fixtures.append({
                **common, "kind": "mover", "head": f.head,
                "location": point(head.x, head.height, head.z),
                "rest_rotation": head_rotation(
                    rig.geometry.world_bearing(f.head, aim), aim.elev_deg),
                "decode": _decode_block(rig, f.head),
                "servo": {"pan_words_per_s": pan_rate, "tilt_words_per_s": tilt_rate},
                "body": _body(f, bodies, models, event_dir, warnings),
            })
        elif f.position is not None:
            # Placed but not steerable. Its aim is a fact about the bracket:
            # the rig's `aim` point if it gives one, else the mirror ball, which
            # is what every fixed fixture in the one rig that exists points at.
            target = venue.ball
            if "aim" in f.previz:
                got = _vector(f.previz["aim"], f"{f.name}.aim", warnings)
                if got is not None:
                    target = got
            if math.dist(target, f.position) < 1.0:
                warnings.append(f"{f.name}: aim point is on the fixture itself; "
                                f"aiming at the mirror ball instead")
                target = venue.ball
            fixtures.append({
                **common, "kind": "static", "head": None,
                "location": point(*f.position),
                "rest_rotation": head_rotation(*aim_from(f.position, target)),
                "body": _body(f, bodies, models, event_dir, warnings),
            })
        else:
            # Not an error, but silently dropping it would leave the previz
            # quietly missing hardware that is really in the room.
            unplaced.append(f.name)

    placed_models = []
    raw_models = previz.get("models", [])
    if not isinstance(raw_models, list):
        warnings.append("previz.models should be a list")
        raw_models = []
    for i, entry in enumerate(raw_models):
        where = f"previz.models[{i}]"
        if not isinstance(entry, dict):
            warnings.append(f"{where} should be an object")
            continue
        sha = models.resolve(entry.get("file"), venue_dir, where)
        if sha is None:
            continue
        at = _vector(entry.get("position", {"x": 0, "y": 0, "z": 0}),
                     f"{where}.position", warnings)
        if at is None:
            # Skipped, not drawn at the origin: a set piece silently parked in
            # the room's corner is worse than one visibly missing.
            continue
        scale = entry.get("scale", 1.0)
        if isinstance(scale, bool) or not isinstance(scale, (int, float)) or scale <= 0:
            warnings.append(f"{where}.scale should be a positive number, got {scale!r}")
            scale = 1.0
        placed_models.append({
            "name": str(entry.get("name") or Path(str(entry["file"])).stem),
            "model": sha,
            "location": point(*at),
            "rotation": _rotation(entry.get("rotation"), f"{where}.rotation", warnings),
            "scale": float(scale),
            # Venue geometry stops beams unless told otherwise: a beam aimed at
            # a wall that is in the model really does stop there.
            "collide": bool(entry.get("collide", True)),
        })

    bx, by, bz = venue.ball
    size = [mm(venue.depth), mm(venue.width), mm(venue.height)]
    room = {
        "size": size,
        "walls": bool(previz.get("room_walls", True)),
    }

    canopy = None
    if venue.canopy is not None and venue.canopy.enabled:
        canopy = {"location": point(venue.canopy.center_x, venue.canopy.height,
                                    venue.canopy.center_z),
                  "radius": mm(venue.canopy.radius)}

    truss = None
    if venue.truss is not None and venue.truss.enabled:
        fp = venue.truss.footprint
        h, bar = venue.truss.height, venue.truss.bar
        half = bar / 2.0
        bars = []
        for label, cx, cz, ex, ez in (
                ("front", (fp.min_x + fp.max_x) / 2, fp.min_z, venue.truss.span_x / 2 + half, half),
                ("back", (fp.min_x + fp.max_x) / 2, fp.max_z, venue.truss.span_x / 2 + half, half),
                ("left", fp.min_x, (fp.min_z + fp.max_z) / 2, half, venue.truss.span_z / 2 + half),
                ("right", fp.max_x, (fp.min_z + fp.max_z) / 2, half, venue.truss.span_z / 2 + half)):
            # Half-extents already in Unreal's axis order: X is the venue's z.
            bars.append({"label": label, "center": point(cx, h, cz),
                         "extent": [mm(ez), mm(ex), mm(half)]})
        truss = {"bars": bars, "bar": mm(bar), "height": mm(h)}

    crowd = None
    if venue.crowd_zone is not None:
        b = venue.crowd_zone.footprint
        lo, hi = venue.crowd_zone.head_band_min, venue.crowd_zone.head_band_max
        crowd = {
            "center": point((b.min_x + b.max_x) / 2, (lo + hi) / 2, (b.min_z + b.max_z) / 2),
            "extent": [mm((b.max_z - b.min_z) / 2), mm((b.max_x - b.min_x) / 2),
                       mm((hi - lo) / 2)],
        }

    manifest = {
        "format": FORMAT,
        "version": VERSION,
        "event": rig.name,
        "venue": venue.name,
        "mount_mode": "" if rig.geometry is None else rig.geometry.mount_mode,
        "universes": list(rig.universes),
        "room": room,
        "ball": {
            "location": point(bx, by, bz),
            "radius": mm(venue.ball_radius),
            # In mm as well, because the facet lattices are specified as facet
            # sizes in mm and are worked out in the show's own units.
            "radius_mm": float(venue.ball_radius),
            **ball_cfg,
            "model": ball_model,
        },
        "canopy": canopy,
        "truss": truss,
        "crowd_zone": crowd,
        # Long enough that a beam always reaches a wall from anywhere in the room.
        "max_throw": mm(math.dist((0, 0, 0), (venue.width, venue.height, venue.depth))),
        "optics": optics,
        "fixtures": fixtures,
        "models": placed_models,
        "assets": {sha: {"name": path.name, "bytes": models.sizes[sha]}
                   for sha, path in sorted(models.files.items())},
        "unplaced": unplaced,
        "warnings": warnings + models.warnings,
    }
    manifest["views"] = camera_views(manifest)
    manifest["rev"] = hashlib.sha256(_canonical(manifest)).hexdigest()[:16]
    return Scene(manifest=manifest, files=dict(models.files))


def build_for(event_dir: Path) -> Scene:
    """Load an event from disk and build its manifest. For tools and tests; the
    server builds from the rig it already has."""
    event_dir = Path(event_dir)
    return build(rigmod.load_rig(event_dir), event_dir)


# ---------------------------------------------------------------- cameras ----

def camera_views(spec: dict) -> dict:
    """Where to stand to photograph this room, derived from its own geometry.

    The framing RULES are what is worth keeping, and every one of them was
    learned by taking a bad photograph:

      * Never stand on a diagonal. Heads live in the corners aiming inward, so a
        camera on the line takes a beam straight down the barrel.
      * Never stand on a mid-line. That is where pinspots hang, aimed at the
        ball, so their bodies eclipse the thing you are looking at.
      * Stand above the truss. A bar at beam height cuts the frame in half.
      * Stand in the clear floor between the rig and the wall, not outside the
        room. A cutaway wall is a wall not bouncing light.

    `fog_start` is per view because volumetric fog's slices are measured FROM
    THE CAMERA: a value that flatters a camera standing well back erases the
    fog from every beam within that range of one standing inside.
    """
    room, ball = spec["room"], spec["ball"]
    sx, sy = float(room["size"][0]), float(room["size"][1])
    cx, cy = sx / 2.0, sy / 2.0
    bx, by, bz = (float(v) for v in ball["location"])
    ball_r = float(ball.get("radius") or 30.0)

    truss = spec.get("truss") or {}
    rig_top = float(truss.get("height") or bz)
    if truss.get("bars"):
        xs = [b["center"][0] for b in truss["bars"]]
        ys = [b["center"][1] for b in truss["bars"]]
        margin = max(30.0, min(min(xs), min(ys), sx - max(xs), sy - max(ys)))
    else:
        margin = min(sx, sy) * 0.2

    off_axis = max(80.0, min(sx, sy) * 0.06)
    # Above the bars, but inside a low room: a camera ON the ceiling plane sees
    # the ceiling edge-on whether or not it is hidden.
    eye = min(rig_top + max(150.0, min(sx, sy) * 0.1), float(room["size"][2]) * 0.92)

    crowd = spec.get("crowd_zone")
    if crowd:
        near = float(crowd["center"][0]) - float(crowd["extent"][0])
        stand = near + min(50.0, float(crowd["extent"][0]) * 0.07)
        head = float(crowd["center"][2]) - float(crowd["extent"][2]) + 25.0
    else:
        stand, head = margin + 50.0, 165.0

    def view(location, target, fov, hide_ceiling, fog_start):
        return {"location": [float(v) for v in location],
                "target": [float(v) for v in target],
                "fov": float(fov), "hide_ceiling": hide_ceiling,
                "fog_start": float(fog_start)}

    return {
        # The working view: inside the near wall, outside the rig, lifted
        # above the bars.
        "overview": view((margin * 0.25, cy + off_axis, eye), (bx, by, bz), 70.0, True, 80.0),
        # Down the room's diagonal from the empty corner -- but NOT on it.
        "corner": view((margin * 0.33, margin * 0.68, eye),
                       (bx, by, bz - ball_r * 0.6), 70.0, True, 80.0),
        # In the crowd at eye height, blinding beams and all.
        "audience": view((stand, cy + off_axis, head), (bx, by, bz - ball_r), 85.0, False, 0.0),
        # Close on the ball, framed off its own radius but clamped to the room.
        "ball": view((cx + off_axis, by - min(ball_r * 11.5, by * 0.85), bz + ball_r * 0.5),
                     (bx, by, bz), 26.0, True, 0.0),
    }


# ----------------------------------------------- the decode, against the data --
#
# These are the DEFINITION of what the app does with a manifest and a DMX frame.
# They read only manifest fields -- never a Rig -- so they describe exactly the
# information the app has, and `shared/tools/gen_previz_parity.py` sweeps them
# into the golden vectors the app's tests are held to.

def channel_word(fixture: dict, frame, coarse: str, fine: str) -> int:
    """A 16-bit position from coarse/fine channels; coarse << 8 with no fine."""
    channels = fixture["channels"]
    hi = channels.get(coarse)
    if hi is None:
        return 0
    lo = channels.get(fine)
    return (frame[hi] << 8) | (0 if lo is None else frame[lo])


def decode_aim(decode: dict, pan: float, tilt: float) -> tuple[float, float]:
    """(world bearing, elevation) in degrees for a head's (pan, tilt) words.

    Built from `geometry`'s own DMX<->angle helpers rather than restated, so
    this is the show's decode applied to the manifest's numbers, not a copy.
    """
    res = geo.Resolution(bits=16 if decode["span"] > 255 else 8,
                         span=decode["span"], half=decode["half"])
    by_channel = {"pan": pan, "tilt": tilt}
    bearing_delta = geo.from_dmx_centered(by_channel[decode["bearing_channel"]],
                                          decode["bearing_max"],
                                          decode["bearing_invert"], res)
    elev = geo.decode_elev(by_channel[decode["elevation_channel"]],
                           decode["elevation_max"], decode["elevation_invert"],
                           decode["elevation_anchor"], res)
    return decode["mount_facing"] + bearing_delta, elev - decode["elevation_offset"]


def beam_direction(bearing_deg: float, elev_deg: float) -> tuple[float, float, float]:
    """The unit beam direction in Unreal axes -- the forward vector of
    `head_rotation(bearing, elev)`."""
    return forward_vector(elev_deg, bearing_deg)


def decode_color(fixture: dict, frame) -> tuple[tuple[float, float, float],
                                                 Optional[tuple[tuple[float, ...], tuple[float, ...]]]]:
    """((r, g, b), split) 0-1 for a fixture, from whichever colour system it has.

    A mixing fixture adds white into each primary. A wheel snaps its value to
    the slot table; above the last slot the wheel is spinning, and white is the
    honest stand-in for "some colour, changing". `split` is (top, bottom) for a
    wheel parked between two segments -- the first-named colour of a slot goes
    on TOP -- and None otherwise.
    """
    channels = fixture["channels"]
    if rigmod.RED in channels:
        def value(role):
            i = channels.get(role)
            return 0.0 if i is None else frame[i] / 255.0
        w = value(rigmod.WHITE)
        return (min(1.0, value(rigmod.RED) + w), min(1.0, value(rigmod.GREEN) + w),
                min(1.0, value(rigmod.BLUE) + w)), None
    idx = channels.get(rigmod.COLOR_WHEEL)
    if idx is None:
        return (1.0, 1.0, 1.0), None
    raw = frame[idx]
    for slot in fixture["color_slots"]:
        if slot[0] <= raw <= slot[1]:
            rgb = tuple(c / 255.0 for c in slot[2:5])
            first, second = tuple(slot[5:8]), tuple(slot[8:11])
            split = None if first == second else (
                tuple(c / 255.0 for c in first), tuple(c / 255.0 for c in second))
            return rgb, split
    return (1.0, 1.0, 1.0), None


def fixture_output(fixture: dict, frame):
    """(level, colour, split) for one fixture this frame.

    One rule for every kind of fixture: level is the dimmer (full if there is
    none) times the colour's own magnitude, and the colour is normalised to it.
    The editor driver had two rules -- dimmer-and-raw-colour for movers,
    magnitude-and-normalised for fixed fixtures -- which agree on every product
    a renderer draws, and this is that agreement written once.
    """
    color, split = decode_color(fixture, frame)
    dim = fixture["channels"].get(rigmod.DIMMER)
    level = (1.0 if dim is None else frame[dim] / 255.0) * max(color)
    if max(color) <= 0.0:
        return 0.0, (1.0, 1.0, 1.0), split
    return level, tuple(c / max(color) for c in color), split


# -------------------------------------------------------------- self-test ----

def _self_test() -> None:
    """The axis mapping agrees with `geometry.ray()`, and the manifest decode
    agrees with `geometry.decode()`.

    `ray()` is what the safety taper casts, so it is the definition of where a
    beam goes. A previz that draws it anywhere else is wrong plausibly -- four
    believable beams on four incorrect walls.
    """
    rig = geo.despacio_reference_rig("venue")
    for i in range(len(rig.heads)):
        for target in [(0.0, 0.0, 0.0), (9144.0, 4600.0, 0.0),
                       (4572.0, 2743.0, 4572.0), (1000.0, 1500.0, 8000.0)]:
            aim = rig.aim_at_point(i, *target)
            origin, ray = rig.ray(i, aim)
            pitch, yaw, roll = head_rotation(rig.world_bearing(i, aim), aim.elev_deg)
            assert roll == 0.0
            got = forward_vector(pitch, yaw)
            want = direction(*ray)
            for g, w in zip(got, want):
                assert math.isclose(g, w, abs_tol=1e-9), (i, target, got, want)
            head = rig.heads[i]
            assert point(*origin) == point(head.x, head.height, head.z)
    assert math.isclose(mm(9144.0), 914.4)

    # A fixed fixture's rotator really points at what it was aimed at.
    for origin in [(4572.0, 2971.0, 500.0), (0.0, 0.0, 0.0), (9144.0, 4600.0, 9144.0)]:
        target = (4572.0, 2743.0, 4572.0)
        f = forward_vector(*head_rotation(*aim_from(origin, target))[:2])
        o, t = point(*origin), point(*target)
        reach = math.dist(o, t)
        for k in range(3):
            assert math.isclose(o[k] + f[k] * reach, t[k], abs_tol=1e-6)

    # Cameras: the framing rules, against rooms that are NOT square -- the
    # case the old editor builder got wrong. Room [X, Y, Z] in cm.
    for sx, sy, ball_z in ((1828.8, 1828.8, 274.3), (4000.0, 900.0, 500.0),
                           (900.0, 4000.0, 500.0), (600.0, 600.0, 200.0)):
        spec = {
            "room": {"size": [sx, sy, ball_z * 2.5]},
            "ball": {"location": [sx / 2, sy / 2, ball_z], "radius": 30.0},
            "truss": {"height": ball_z * 0.9, "bars": [
                {"center": [sx * 0.25, sy / 2, ball_z * 0.9]},
                {"center": [sx * 0.75, sy / 2, ball_z * 0.9]},
                {"center": [sx / 2, sy * 0.25, ball_z * 0.9]},
                {"center": [sx / 2, sy * 0.75, ball_z * 0.9]}]},
            "crowd_zone": {"center": [sx / 2, sy / 2, ball_z * 0.6],
                           "extent": [sx * 0.18, sy * 0.18, 30.0]},
        }
        for name, v in camera_views(spec).items():
            x, y, z = v["location"]
            assert 0.0 < x < sx, f"{name} outside the room in X: {x} ({sx}x{sy})"
            assert 0.0 < y < sy, f"{name} outside the room in Y: {y} ({sx}x{sy})"
            assert 0.0 < z < ball_z * 2.5, f"{name} outside the room in Z: {z}"
            assert v["target"] != v["location"]

    # fixture_output agrees with the old driver's two rules on what is drawn.
    wheel = {"channels": {rigmod.DIMMER: 0, rigmod.COLOR_WHEEL: 1},
             "color_slots": [[0, 9, 255, 255, 255, 255, 255, 255, 255, 255, 255],
                             [10, 19, 0, 128, 255, 0, 255, 0, 0, 0, 255]]}
    level, color, split = fixture_output(wheel, [128, 12])
    assert math.isclose(level, 128 / 255) and color == (0.0, 128 / 255, 1.0)
    assert split == ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    rgbw = {"channels": {rigmod.RED: 0, rigmod.GREEN: 1, rigmod.BLUE: 2, rigmod.WHITE: 3},
            "color_slots": []}
    level, color, split = fixture_output(rgbw, [51, 0, 102, 0])
    assert math.isclose(level, 0.4) and math.isclose(color[2], 1.0) and split is None
    assert fixture_output(rgbw, [0, 0, 0, 0])[0] == 0.0


def main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Print an event's previz scene manifest.")
    parser.add_argument("event", nargs="?", default=None,
                        help="event folder name under events/, or a path")
    parser.add_argument("-o", "--out", help="write JSON here (default: stdout)")
    parser.add_argument("--models", metavar="DIR",
                        help="also copy every model the scene names into DIR as <sha256>.glb "
                             "-- with the JSON, a bundle the app opens with no engine "
                             "(-Scene=scene.json -ModelCache=DIR)")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    _self_test()
    if args.self_test:
        print("engine.scene self-test: PASS")
        return 0
    if args.event is None:
        parser.error("name an event")

    event_dir = Path(args.event)
    if not event_dir.exists():
        event_dir = REPO / "events" / args.event
    scene = build_for(event_dir)
    text = json.dumps(scene.manifest, indent=2, sort_keys=True)
    if args.models:
        import shutil
        target = Path(args.models)
        target.mkdir(parents=True, exist_ok=True)
        for sha, path in scene.files.items():
            shutil.copyfile(path, target / f"{sha}.glb")
        print(f"{len(scene.files)} model(s) -> {target}", file=sys.stderr)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        m = scene.manifest
        print(f"{m['event']} in {m['venue']}: {len(m['fixtures'])} fixture(s) placed, "
              f"{len(m['models'])} model(s), rev {m['rev']} -> {args.out}", file=sys.stderr)
        for name in m["unplaced"]:
            print(f"  [unplaced] {name}", file=sys.stderr)
        for w in m["warnings"]:
            print(f"  [WARN] {w}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
