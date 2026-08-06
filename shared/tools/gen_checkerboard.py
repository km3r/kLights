import sys

def generate_checkerboard_obj(grid_size=100, output_path="checkerboard.obj"):
    lines = [f"# Checkerboard {grid_size}x{grid_size} cubes (every other position)"]
    face_lines = []

    v_offset = 0

    for z in range(grid_size):
        for x in range(grid_size):
            if (x + z) % 2 == 0:
                # Unit cube at (x, 0, z) to (x+1, 1, z+1)
                # Vertices (0-indexed local):
                # 0: (x,   0, z  )
                # 1: (x+1, 0, z  )
                # 2: (x+1, 1, z  )
                # 3: (x,   1, z  )
                # 4: (x,   0, z+1)
                # 5: (x+1, 0, z+1)
                # 6: (x+1, 1, z+1)
                # 7: (x,   1, z+1)
                lines.append(f"v {x}   0 {z}  ")
                lines.append(f"v {x+1} 0 {z}  ")
                lines.append(f"v {x+1} 1 {z}  ")
                lines.append(f"v {x}   1 {z}  ")
                lines.append(f"v {x}   0 {z+1}")
                lines.append(f"v {x+1} 0 {z+1}")
                lines.append(f"v {x+1} 1 {z+1}")
                lines.append(f"v {x}   1 {z+1}")

                o = v_offset + 1  # OBJ is 1-indexed
                # Faces, CCW winding = outward normals
                face_lines.append(f"f {o}   {o+3} {o+2} {o+1}")  # front  (-Z)
                face_lines.append(f"f {o+4} {o+5} {o+6} {o+7}")  # back   (+Z)
                face_lines.append(f"f {o}   {o+1} {o+5} {o+4}")  # bottom (-Y)
                face_lines.append(f"f {o+3} {o+7} {o+6} {o+2}")  # top    (+Y)
                face_lines.append(f"f {o}   {o+4} {o+7} {o+3}")  # left   (-X)
                face_lines.append(f"f {o+1} {o+2} {o+6} {o+5}")  # right  (+X)

                v_offset += 8

    all_lines = lines + face_lines
    with open(output_path, "w") as f:
        f.write("\n".join(all_lines))

    cube_count = v_offset // 8
    print(f"Written {output_path}: {cube_count} cubes, {v_offset} vertices, {len(face_lines)} faces")

if __name__ == "__main__":
    size = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    out  = sys.argv[2] if len(sys.argv) > 2 else "checkerboard.obj"
    generate_checkerboard_obj(size, out)
