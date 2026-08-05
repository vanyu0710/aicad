from __future__ import annotations

import math
import struct
from pathlib import Path


def stl_to_obj(stl_path: Path, obj_path: Path) -> Path:
    data = stl_path.read_bytes()
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    index: dict[tuple[float, float, float], int] = {}

    def add_vertex(v: tuple[float, float, float]) -> int:
        key = tuple(round(float(x), 6) for x in v)
        if key not in index:
            index[key] = len(vertices) + 1
            vertices.append(key)
        return index[key]

    if data[:5].lower() == b"solid" and b"facet normal" in data[:2000].lower():
        triangles = _parse_ascii_stl(data.decode("utf-8", errors="ignore"))
    else:
        triangles = _parse_binary_stl(data)

    for tri in triangles:
        face = tuple(add_vertex(v) for v in tri)
        if len(set(face)) == 3:
            faces.append(face)

    lines = ["o mechcad_preview"]
    for x, y, z in vertices:
        lines.append(f"v {x:.6f} {y:.6f} {z:.6f}")
    for a, b, c in faces:
        lines.append(f"f {a} {b} {c}")
    obj_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return obj_path


def _parse_ascii_stl(text: str) -> list[tuple[tuple[float, float, float], ...]]:
    triangles = []
    tri: list[tuple[float, float, float]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("vertex"):
            parts = stripped.split()
            if len(parts) == 4:
                tri.append((float(parts[1]), float(parts[2]), float(parts[3])))
        elif stripped.startswith("endfacet"):
            if len(tri) == 3:
                triangles.append(tuple(tri))
            tri = []
    return triangles


def _parse_binary_stl(data: bytes) -> list[tuple[tuple[float, float, float], ...]]:
    if len(data) < 84:
        return []
    count = struct.unpack_from("<I", data, 80)[0]
    offset = 84
    triangles = []
    for _ in range(count):
        if offset + 50 > len(data):
            break
        chunk = data[offset : offset + 50]
        coords = struct.unpack("<12fH", chunk)
        tri = (
            (coords[3], coords[4], coords[5]),
            (coords[6], coords[7], coords[8]),
            (coords[9], coords[10], coords[11]),
        )
        triangles.append(tri)
        offset += 50
    return triangles

