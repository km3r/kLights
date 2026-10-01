"""
Write the starter glTF models previz ships with, from nothing but the stdlib.

    python shared/tools/gen_models.py            # write them
    python shared/tools/gen_models.py --check    # exit 1 if any is stale

Two jobs, both small enough that a hand-written GLB writer beats a dependency:

  * ``shared/models/test/axes.glb`` -- the ORIENTATION CHECK. A checker-textured
    half-metre cube with three 1 m arms along glTF +X (red), +Y (green, up) and
    +Z (blue, the asset's front). Load it anywhere and you can read off exactly
    how a glTF frame lands in Unreal, and whether textures survived the trip.

  * ``shared/models/fixtures/`` -- the DEFAULT BODIES the previz draws for
    fixtures with no model of their own: an articulated moving head and a can
    light, both built to the body convention in docs/models.md and mapped to
    profiles by shared/fixtures/bodies.json.

Output is byte-for-byte deterministic -- no timestamps, no float formatting that
depends on the platform -- so ``--check`` can guard the committed files the way
``gen_schemas.py`` guards the schemas.

glTF conventions, since every model this project accepts must follow them:
metres, +Y up, +Z front, right-handed. Unreal's runtime reader maps glTF
(X, Y, Z) to Unreal (X, Z, Y) and the previz scales by 100 to centimetres.
"""

from __future__ import annotations

import json
import math
import struct
import sys
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MODELS = REPO / "shared" / "models"

# glTF component types and buffer-view targets.
FLOAT = 5126
UNSIGNED_INT = 5125
ARRAY_BUFFER = 34962
ELEMENT_ARRAY_BUFFER = 34963


# --------------------------------------------------------------------- png ---

def png(width: int, height: int, pixel) -> bytes:
    """An 8-bit RGB PNG from `pixel(x, y) -> (r, g, b)`."""
    raw = bytearray()
    for y in range(height):
        raw.append(0)                      # filter: none
        for x in range(width):
            raw.extend(pixel(x, y))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


# ------------------------------------------------------------------ shapes ---

class Geometry:
    """Triangles with per-vertex normals and UVs, accumulated shape by shape."""

    def __init__(self):
        self.positions: list[tuple[float, float, float]] = []
        self.normals: list[tuple[float, float, float]] = []
        self.uvs: list[tuple[float, float]] = []
        self.indices: list[int] = []

    def quad(self, corners, normal, uvs=((0, 1), (1, 1), (1, 0), (0, 0))):
        """Four corners counter-clockwise seen from the side `normal` points to."""
        base = len(self.positions)
        for corner, uv in zip(corners, uvs):
            self.positions.append(tuple(float(c) for c in corner))
            self.normals.append(tuple(float(n) for n in normal))
            self.uvs.append((float(uv[0]), float(uv[1])))
        self.indices += [base, base + 1, base + 2, base, base + 2, base + 3]

    def box(self, size, center=(0.0, 0.0, 0.0)):
        sx, sy, sz = (s / 2 for s in size)
        cx, cy, cz = center

        def p(x, y, z):
            return (cx + x * sx, cy + y * sy, cz + z * sz)

        self.quad([p(1, -1, 1), p(1, -1, -1), p(1, 1, -1), p(1, 1, 1)], (1, 0, 0))
        self.quad([p(-1, -1, -1), p(-1, -1, 1), p(-1, 1, 1), p(-1, 1, -1)], (-1, 0, 0))
        self.quad([p(-1, 1, 1), p(1, 1, 1), p(1, 1, -1), p(-1, 1, -1)], (0, 1, 0))
        self.quad([p(-1, -1, -1), p(1, -1, -1), p(1, -1, 1), p(-1, -1, 1)], (0, -1, 0))
        self.quad([p(-1, -1, 1), p(1, -1, 1), p(1, 1, 1), p(-1, 1, 1)], (0, 0, 1))
        self.quad([p(1, -1, -1), p(-1, -1, -1), p(-1, 1, -1), p(1, 1, -1)], (0, 0, -1))
        return self

    def cylinder(self, radius, length, segments=24, center=(0.0, 0.0, 0.0), axis="y"):
        """A capped cylinder along `axis` ('x', 'y' or 'z')."""
        cx, cy, cz = center

        def place(a, r1, r2):
            # (along-axis, radial-1, radial-2) -> xyz, keeping right-handedness.
            if axis == "y":
                return (cx + r1, cy + a, cz + r2)
            if axis == "x":
                return (cx + a, cy + r2, cz + r1)
            return (cx + r2, cy + r1, cz + a)

        half = length / 2
        for i in range(segments):
            a0 = 2 * math.pi * i / segments
            a1 = 2 * math.pi * (i + 1) / segments
            c0, s0, c1, s1 = math.cos(a0), math.sin(a0), math.cos(a1), math.sin(a1)
            n0 = place(0, c0, s0)
            n1 = place(0, c1, s1)
            n0 = tuple(n - c for n, c in zip(n0, center))
            n1 = tuple(n - c for n, c in zip(n1, center))
            base = len(self.positions)
            for along, cs, sn, normal, u in ((-half, c0, s0, n0, i / segments),
                                             (-half, c1, s1, n1, (i + 1) / segments),
                                             (half, c1, s1, n1, (i + 1) / segments),
                                             (half, c0, s0, n0, i / segments)):
                self.positions.append(place(along, radius * cs, radius * sn))
                self.normals.append(normal)
                self.uvs.append((u, 0.0 if along > 0 else 1.0))
            self.indices += [base, base + 2, base + 1, base, base + 3, base + 2]
            for along, sign in ((half, 1.0), (-half, -1.0)):
                cap_normal = tuple(n - c for n, c in zip(place(sign, 0, 0), center))
                base = len(self.positions)
                for cs, sn in ((0.0, 0.0), (c0, s0), (c1, s1)):
                    self.positions.append(place(along, radius * cs, radius * sn))
                    self.normals.append(cap_normal)
                    self.uvs.append((0.5 + cs / 2, 0.5 + sn / 2))
                self.indices += ([base, base + 2, base + 1] if sign > 0
                                 else [base, base + 1, base + 2])
        return self


# --------------------------------------------------------------------- glb ---

class Glb:
    """Just enough of glTF 2.0 to write static, textured, named hierarchies."""

    def __init__(self, generator="kLights gen_models.py"):
        self.doc = {"asset": {"version": "2.0", "generator": generator},
                    "scene": 0, "scenes": [{"nodes": []}], "nodes": [],
                    "meshes": [], "materials": [], "accessors": [],
                    "bufferViews": [], "buffers": []}
        self.blob = bytearray()

    def _view(self, data: bytes, target=None) -> int:
        while len(self.blob) % 4:
            self.blob.append(0)
        view = {"buffer": 0, "byteOffset": len(self.blob), "byteLength": len(data)}
        if target is not None:
            view["target"] = target
        self.blob.extend(data)
        self.doc["bufferViews"].append(view)
        return len(self.doc["bufferViews"]) - 1

    def _accessor(self, values, kind: str, component: int, target, bounds=False) -> int:
        flat = [c for v in values for c in (v if isinstance(v, tuple) else (v,))]
        fmt = "<%d%s" % (len(flat), "f" if component == FLOAT else "I")
        accessor = {"bufferView": self._view(struct.pack(fmt, *flat), target),
                    "componentType": component, "count": len(values), "type": kind}
        if bounds:
            width = len(values[0])
            accessor["min"] = [min(v[i] for v in values) for i in range(width)]
            accessor["max"] = [max(v[i] for v in values) for i in range(width)]
        self.doc["accessors"].append(accessor)
        return len(self.doc["accessors"]) - 1

    def texture(self, png_bytes: bytes) -> int:
        self.doc.setdefault("images", []).append(
            {"bufferView": self._view(png_bytes), "mimeType": "image/png"})
        self.doc.setdefault("samplers", [{}])
        self.doc.setdefault("textures", []).append(
            {"sampler": 0, "source": len(self.doc["images"]) - 1})
        return len(self.doc["textures"]) - 1

    def material(self, name, color=(0.8, 0.8, 0.8), metallic=0.0, roughness=0.8,
                 emissive=None, texture=None) -> int:
        pbr = {"baseColorFactor": [*map(float, color), 1.0],
               "metallicFactor": float(metallic), "roughnessFactor": float(roughness)}
        if texture is not None:
            pbr["baseColorTexture"] = {"index": texture}
        material = {"name": name, "pbrMetallicRoughness": pbr}
        if emissive is not None:
            material["emissiveFactor"] = [float(e) for e in emissive]
        self.doc["materials"].append(material)
        return len(self.doc["materials"]) - 1

    def mesh(self, name, parts) -> int:
        """`parts` is [(Geometry, material index)], one primitive each."""
        primitives = []
        for geometry, material in parts:
            attributes = {
                "POSITION": self._accessor(geometry.positions, "VEC3", FLOAT, ARRAY_BUFFER, bounds=True),
                "NORMAL": self._accessor(geometry.normals, "VEC3", FLOAT, ARRAY_BUFFER),
                "TEXCOORD_0": self._accessor(geometry.uvs, "VEC2", FLOAT, ARRAY_BUFFER),
            }
            indices = self._accessor(geometry.indices, "SCALAR", UNSIGNED_INT, ELEMENT_ARRAY_BUFFER)
            primitives.append({"attributes": attributes, "indices": indices, "material": material})
        self.doc["meshes"].append({"name": name, "primitives": primitives})
        return len(self.doc["meshes"]) - 1

    def node(self, name, mesh=None, translation=None, rotation=None, children=(),
             root=False) -> int:
        node = {"name": name}
        if mesh is not None:
            node["mesh"] = mesh
        if translation is not None:
            node["translation"] = [float(t) for t in translation]
        if rotation is not None:
            node["rotation"] = [float(r) for r in rotation]   # quaternion x, y, z, w
        if children:
            node["children"] = list(children)
        self.doc["nodes"].append(node)
        index = len(self.doc["nodes"]) - 1
        if root:
            self.doc["scenes"][0]["nodes"].append(index)
        return index

    def bytes(self) -> bytes:
        while len(self.blob) % 4:
            self.blob.append(0)
        doc = {k: v for k, v in self.doc.items() if v != []}
        doc["buffers"] = [{"byteLength": len(self.blob)}]
        text = json.dumps(doc, separators=(",", ":"), sort_keys=True).encode("utf-8")
        text += b" " * (-len(text) % 4)
        body = (struct.pack("<I", len(text)) + b"JSON" + text
                + struct.pack("<I", len(self.blob)) + b"BIN\x00" + bytes(self.blob))
        return b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body


# ------------------------------------------------------------------ models ---

def axes() -> bytes:
    glb = Glb()
    checker = glb.texture(png(64, 64, lambda x, y: (235, 235, 235) if (x // 8 + y // 8) % 2
                              else (40, 40, 40)))
    cube = glb.material("checker", (1, 1, 1), roughness=0.6, texture=checker)
    red = glb.material("x_red", (0.9, 0.05, 0.05), emissive=(0.4, 0, 0))
    green = glb.material("y_green", (0.05, 0.9, 0.05), emissive=(0, 0.4, 0))
    blue = glb.material("z_blue", (0.05, 0.1, 0.9), emissive=(0, 0, 0.4))

    arm, thick = 1.0, 0.05
    kids = [
        glb.node("cube", glb.mesh("cube", [(Geometry().box((0.5, 0.5, 0.5)), cube)])),
        glb.node("x_axis", glb.mesh("x_axis", [(Geometry().box((arm, thick, thick), (arm / 2, 0, 0)), red)])),
        glb.node("y_axis", glb.mesh("y_axis", [(Geometry().box((thick, arm, thick), (0, arm / 2, 0)), green)])),
        glb.node("z_axis", glb.mesh("z_axis", [(Geometry().box((thick, thick, arm), (0, 0, arm / 2)), blue)])),
    ]
    glb.node("axes", children=kids, root=True)
    return glb.bytes()


def moving_head() -> bytes:
    """A generic moving head, sized like the MJ-OS-018 (180 x 280 x 200 mm).

    Built to the body convention in docs/models.md, which is what lets the
    previz articulate it:

      * nodes `base`, `yoke`, `head`, `lens`, each a child of the one before;
      * at rest the fixture stands on its base, +Y up, and the beam leaves
        `lens` along +Z (the asset's front);
      * the yoke pans about +Y and the head tilts about +X;
      * no rotation on `yoke` or `head` at rest, and the head's tilt pivot sits
        ON the pan axis, so panning never moves it.
    """
    glb = Glb()
    shell = glb.material("housing", (0.06, 0.06, 0.065), metallic=0.3, roughness=0.45)
    trim = glb.material("trim", (0.18, 0.18, 0.19), metallic=0.6, roughness=0.35)
    glass = glb.material("lens", (0.85, 0.9, 1.0), metallic=0.0, roughness=0.05,
                         emissive=(0.25, 0.27, 0.3))

    # The head is centred on its tilt axle; the lens is its front face.
    lens = glb.node("lens", glb.mesh("lens", [(Geometry().cylinder(0.045, 0.006, 32, axis="z"), glass)]),
                    translation=(0.0, 0.0, 0.1))
    head_shape = Geometry().cylinder(0.062, 0.16, 32, axis="z")
    head_shape.cylinder(0.055, 0.02, 32, (0.0, 0.0, 0.09), axis="z")
    head = glb.node("head", glb.mesh("head", [(head_shape, shell)]),
                    translation=(0.0, 0.135, 0.0), children=[lens])

    yoke_shape = Geometry().box((0.18, 0.02, 0.07), (0.0, 0.01, 0.0))
    yoke_shape.box((0.018, 0.16, 0.06), (0.081, 0.09, 0.0))
    yoke_shape.box((0.018, 0.16, 0.06), (-0.081, 0.09, 0.0))
    yoke_shape.cylinder(0.02, 0.17, 16, (0.0, 0.135, 0.0), axis="x")      # the tilt axle
    yoke = glb.node("yoke", glb.mesh("yoke", [(yoke_shape, trim)]),
                    translation=(0.0, 0.075, 0.0), children=[head])

    base_shape = Geometry().box((0.18, 0.07, 0.2), (0.0, 0.035, 0.0))
    base_shape.cylinder(0.05, 0.006, 24, (0.0, 0.073, 0.0), axis="y")    # the pan bearing
    base = glb.node("base", glb.mesh("base", [(base_shape, shell)]), children=[yoke])
    glb.node("moving_head", children=[base], root=True)
    return glb.bytes()


def can() -> bytes:
    """A generic can light -- par, pinspot -- pointing along +Z, its front.

    One node is enough: a fixture that cannot move is posed as a whole, with
    its front along the beam. The origin is the lens, where the beam starts.
    """
    glb = Glb()
    shell = glb.material("housing", (0.06, 0.06, 0.065), metallic=0.3, roughness=0.45)
    glass = glb.material("lens", (0.85, 0.9, 1.0), roughness=0.05, emissive=(0.25, 0.27, 0.3))
    body = Geometry().cylinder(0.06, 0.18, 32, (0.0, 0.0, -0.095), axis="z")
    body.box((0.012, 0.16, 0.012), (0.0, -0.02, -0.09))                    # the bracket
    lens = Geometry().cylinder(0.05, 0.006, 32, (0.0, 0.0, -0.003), axis="z")
    glb.node("can", glb.mesh("can", [(body, shell), (lens, glass)]), root=True)
    return glb.bytes()


def room_shell() -> bytes:
    """A 12 x 4.5 x 8 m room, in the VENUE convention: origin at the front-left
    floor corner, +X across, +Y up, receding along -Z. For testing a venue whose
    walls come from a model rather than the drawn box."""
    glb = Glb()
    wall = glb.material("plaster", (0.16, 0.16, 0.18), roughness=0.85)
    glb.node("room_shell", glb.mesh("room_shell", [
        (Geometry().box((12.0, 4.5, 8.0), (6.0, 2.25, -4.0)), wall)]), root=True)
    return glb.bytes()


def riser() -> bytes:
    """A 4 x 0.6 x 2 m stage riser with a 2 m back panel, origin at its front
    centre on the floor, front along +Z."""
    glb = Glb()
    deck = glb.material("deck", (0.09, 0.07, 0.06), roughness=0.7)
    panel = glb.material("panel", (0.03, 0.03, 0.035), roughness=0.9)
    shape = Geometry().box((4.0, 0.6, 2.0), (0.0, 0.3, -1.0))
    back = Geometry().box((4.0, 2.0, 0.08), (0.0, 1.6, -1.96))
    glb.node("riser", glb.mesh("riser", [(shape, deck), (back, panel)]), root=True)
    return glb.bytes()


OUTPUTS = {
    MODELS / "test" / "room_shell.glb": room_shell,
    MODELS / "test" / "riser.glb": riser,
    MODELS / "test" / "axes.glb": axes,
    MODELS / "fixtures" / "generic_moving_head.glb": moving_head,
    MODELS / "fixtures" / "generic_can.glb": can,
}


def _self_test() -> None:
    """Every triangle winds counter-clockwise around its own normal.

    glTF's front face is counter-clockwise, and a single wrongly wound face
    renders as a hole from outside (or, on a two-sided material, as a face lit
    from the wrong side), which is easy to miss on a black fixture body.
    """
    shapes = [Geometry().box((1, 2, 3), (0.5, 0, 0))]
    shapes += [Geometry().cylinder(0.2, 1.0, 16, (0.1, 0.2, 0.3), axis) for axis in "xyz"]
    for geometry in shapes:
        for t in range(0, len(geometry.indices), 3):
            a, b, c = (geometry.positions[i] for i in geometry.indices[t:t + 3])
            e1 = [b[k] - a[k] for k in range(3)]
            e2 = [c[k] - a[k] for k in range(3)]
            face = (e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2],
                    e1[0] * e2[1] - e1[1] * e2[0])
            normal = geometry.normals[geometry.indices[t]]
            assert sum(f * n for f, n in zip(face, normal)) > 0, (
                f"triangle {t // 3} winds against its normal")


def main(argv: list[str]) -> int:
    _self_test()
    check = "--check" in argv
    stale = []
    for path, build in OUTPUTS.items():
        data = build()
        if check:
            if not path.is_file() or path.read_bytes() != data:
                stale.append(path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        print(f"wrote {path.relative_to(REPO)} ({len(data)} bytes)")
    if stale:
        for path in stale:
            print(f"stale: {path.relative_to(REPO)} -- run shared/tools/gen_models.py")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
