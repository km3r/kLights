import math
import sys

def generate_mirrorball_obj(radius=0.5, segments=18, rings=12, output_path="mirrorball.obj"):
    """Faceted UV sphere for the QLC+ 3D view. Low segment/ring counts keep the
    facets visible so it reads as a mirror ball instead of a smooth sphere."""
    verts = []   # (x, y, z)
    faces = []   # lists of 1-indexed vertex ids, CCW from outside

    # Ring vertices (exclude poles)
    for r in range(1, rings):
        theta = math.pi * r / rings          # 0 = top pole
        for s in range(segments):
            phi = 2 * math.pi * s / segments
            x = radius * math.sin(theta) * math.cos(phi)
            y = radius * math.cos(theta)     # Y up, matches QLC+ 3D
            z = radius * math.sin(theta) * math.sin(phi)
            verts.append((x, y, z))

    top = len(verts) + 1
    verts.append((0.0, radius, 0.0))
    bottom = len(verts) + 1
    verts.append((0.0, -radius, 0.0))

    def vid(r, s):
        # r in [1, rings-1] -> row index r-1
        return (r - 1) * segments + (s % segments) + 1

    # Top cap
    for s in range(segments):
        faces.append([top, vid(1, s + 1), vid(1, s)])
    # Body quads
    for r in range(1, rings - 1):
        for s in range(segments):
            faces.append([vid(r, s), vid(r, s + 1), vid(r + 1, s + 1), vid(r + 1, s)])
    # Bottom cap
    for s in range(segments):
        faces.append([bottom, vid(rings - 1, s), vid(rings - 1, s + 1)])

    lines = [f"# Mirror ball: faceted UV sphere r={radius} ({segments}x{rings})"]
    for x, y, z in verts:
        lines.append(f"v {x:.5f} {y:.5f} {z:.5f}")
    lines.append("s off")  # flat shading -> visible facets
    for f in faces:
        lines.append("f " + " ".join(str(i) for i in f))

    with open(output_path, "w") as fh:
        fh.write("\n".join(lines))
    print(f"Written {output_path}: {len(verts)} vertices, {len(faces)} faces")

if __name__ == "__main__":
    radius = float(sys.argv[1]) if len(sys.argv) > 1 else 0.5
    out = sys.argv[2] if len(sys.argv) > 2 else "mirrorball.obj"
    generate_mirrorball_obj(radius=radius, output_path=out)
