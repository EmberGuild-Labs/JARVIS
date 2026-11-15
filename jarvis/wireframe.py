"""Load a 3D model as vertices + edges for the HUD's holographic wireframe."""
from __future__ import annotations

import json
from pathlib import Path


def load_model(path: Path) -> tuple[list, list]:
    """.json {"verts": [[x,y,z]...], "edges": [[i,j]...]} (Blender's Z-up), or a Wavefront
    .obj (Y-up, converted to Z-up). Faces become their outline edges."""
    if path.suffix.lower() == ".json":
        d = json.loads(path.read_text(encoding="utf-8"))
        verts = d.get("verts") or d.get("vertices") or []
        edges = d.get("edges") or []
        if not edges and d.get("faces"):
            edges = _face_edges(d["faces"])
        return verts, edges
    if path.suffix.lower() == ".stl":
        return _load_stl(path)
    verts, faces, lines = [], [], []
    with path.open(encoding="utf-8", errors="ignore") as f:
        for ln in f:
            p = ln.split()
            if not p:
                continue
            if p[0] == "v" and len(p) >= 4:
                x, y, z = map(float, p[1:4])
                verts.append([x, -z, y])          # Y-up -> Z-up
            elif p[0] == "f":
                faces.append([_idx(t, len(verts)) for t in p[1:]])
            elif p[0] == "l":
                lines.append([_idx(t, len(verts)) for t in p[1:]])
    edges = _face_edges(faces)
    for ln in lines:
        edges += [[a, b] for a, b in zip(ln, ln[1:])]
    return verts, edges


def _load_stl(path: Path) -> tuple[list, list]:
    """STL triangles -> shared vertices + outline edges (CAD exports are Z-up already)."""
    import struct
    data = path.read_bytes()
    tris = []
    n = struct.unpack_from("<I", data, 80)[0] if len(data) >= 84 else 0
    if len(data) == 84 + n * 50 and n:            # binary
        for i in range(n):
            vals = struct.unpack_from("<12f", data, 84 + i * 50)
            tris.append([vals[3:6], vals[6:9], vals[9:12]])
    else:                                          # ASCII
        pts = []
        for ln in data.decode("utf-8", "ignore").splitlines():
            p = ln.split()
            if p[:1] == ["vertex"]:
                pts.append(tuple(map(float, p[1:4])))
        tris = [pts[i:i + 3] for i in range(0, len(pts) - 2, 3)]
    index, verts, faces = {}, [], []
    for t in tris:
        f = []
        for v in t:
            k = tuple(round(c, 5) for c in v)
            if k not in index:
                index[k] = len(verts)
                verts.append(list(k))
            f.append(index[k])
        faces.append(f)
    return verts, _face_edges(faces)


def _idx(tok: str, n: int) -> int:
    i = int(tok.split("/")[0])
    return i - 1 if i > 0 else n + i


def _face_edges(faces) -> list:
    seen, out = set(), []
    for f in faces:
        for a, b in zip(f, f[1:] + f[:1]):
            k = (min(a, b), max(a, b))
            if k not in seen:
                seen.add(k)
                out.append([a, b])
    return out
