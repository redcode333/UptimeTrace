"""Procedural GLB models for the 3D infrastructure view.

Writes the first supplied models into frontend/public/assets/infrastructure/<category>/<component_type>.glb.
The geometry is generated here from simple primitives (boxes and prisms). No third-party meshes or textures are used,
so there is no licence to attribute beyond this project's own.

Conventions (docs/3D_INFRASTRUCTURE.md): metres, origin at the centre of the base, +Y up, front facing +Z, no baked
health colours or labels, and anchor nodes named anchor_<port> whose offsets match the registry port offsets.

Run from the frontend folder:  python scripts/infra-models/build_models.py
Only the standard library and numpy are needed.
"""
from __future__ import annotations

import json
import math
import pathlib
import struct
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "public" / "assets" / "infrastructure"

# Material name -> (baseColor RGBA, metallic, roughness)
MATERIALS = {
    "chassis": ((0.23, 0.27, 0.31, 1.0), 0.55, 0.45),
    "panel": ((0.42, 0.46, 0.50, 1.0), 0.60, 0.35),
    "dark": ((0.09, 0.10, 0.12, 1.0), 0.20, 0.60),
    "accent": ((0.55, 0.62, 0.70, 1.0), 0.50, 0.30),
    "side": ((0.20, 0.26, 0.34, 1.0), 0.45, 0.50),
    "cap": ((0.50, 0.58, 0.66, 1.0), 0.50, 0.32),
}


class Geo:
    """Accumulates triangles per material. Winding is corrected so every triangle faces its stated normal."""

    def __init__(self) -> None:
        self.tris: dict[str, tuple[list, list]] = {}

    def _tri(self, mat: str, a, b, c, n) -> None:
        a, b, c = (np.asarray(p, dtype=float) for p in (a, b, c))
        n = np.asarray(n, dtype=float)
        if np.dot(np.cross(b - a, c - a), n) < 0:
            b, c = c, b
        pos, nrm = self.tris.setdefault(mat, ([], []))
        pos.extend([a, b, c])
        nrm.extend([n, n, n])

    def quad(self, mat: str, a, b, c, d, n) -> None:
        self._tri(mat, a, b, c, n)
        self._tri(mat, a, c, d, n)

    def box(self, mat: str, center, size) -> None:
        c = np.asarray(center, dtype=float)
        h = np.asarray(size, dtype=float) / 2.0
        for ax in range(3):
            for s in (-1.0, 1.0):
                n = np.zeros(3)
                n[ax] = s
                u, v = (ax + 1) % 3, (ax + 2) % 3
                eu = np.zeros(3)
                eu[u] = h[u]
                ev = np.zeros(3)
                ev[v] = h[v]
                fc = c + n * h[ax]
                self.quad(mat, fc - eu - ev, fc + eu - ev, fc + eu + ev, fc - eu + ev, n)

    def prism(self, side: str, cap: str, pts_xz, y0: float, y1: float) -> None:
        """Closed vertical prism over a convex polygon given as (x, z) points."""
        P = [np.array([x, 0.0, z], dtype=float) for x, z in pts_xz]
        k = len(P)
        centre = np.mean(P, axis=0)
        for i in range(k):
            a, b = P[i], P[(i + 1) % k]
            e = b - a
            n = np.array([e[2], 0.0, -e[0]])
            n /= np.linalg.norm(n)
            if np.dot(n, (a + b) / 2 - centre) < 0:
                n = -n
            a0, b0 = np.array([a[0], y0, a[2]]), np.array([b[0], y0, b[2]])
            a1, b1 = np.array([a[0], y1, a[2]]), np.array([b[0], y1, b[2]])
            self.quad(side, a0, b0, b1, a1, n)
        top_c = np.array([centre[0], y1, centre[2]])
        bot_c = np.array([centre[0], y0, centre[2]])
        for i in range(k):
            a, b = P[i], P[(i + 1) % k]
            self._tri(cap, top_c, np.array([b[0], y1, b[2]]), np.array([a[0], y1, a[2]]), (0.0, 1.0, 0.0))
            self._tri(cap, bot_c, np.array([a[0], y0, a[2]]), np.array([b[0], y0, b[2]]), (0.0, -1.0, 0.0))

    def tube(self, mat: str, pts_xz, y0: float, y1: float) -> None:
        """Side wall only (no caps) of a vertical prism over a convex polygon. Used for stacked bands."""
        P = [np.array([x, 0.0, z], dtype=float) for x, z in pts_xz]
        k = len(P)
        centre = np.mean(P, axis=0)
        for i in range(k):
            a, b = P[i], P[(i + 1) % k]
            e = b - a
            n = np.array([e[2], 0.0, -e[0]])
            n /= np.linalg.norm(n)
            if np.dot(n, (a + b) / 2 - centre) < 0:
                n = -n
            a0, b0 = np.array([a[0], y0, a[2]]), np.array([b[0], y0, b[2]])
            a1, b1 = np.array([a[0], y1, a[2]]), np.array([b[0], y1, b[2]])
            self.quad(mat, a0, b0, b1, a1, n)

    def disk(self, mat: str, pts_xz, y: float, up: bool) -> None:
        """Flat cap of a convex polygon at height y, facing up or down."""
        P = [np.array([x, y, z], dtype=float) for x, z in pts_xz]
        centre = np.mean(P, axis=0)
        normal = (0.0, 1.0, 0.0) if up else (0.0, -1.0, 0.0)
        k = len(P)
        for i in range(k):
            self._tri(mat, centre, P[i], P[(i + 1) % k], normal)

    def arrays(self, mat: str):
        pos, nrm = self.tris[mat]
        return np.asarray(pos, dtype=np.float32), np.asarray(nrm, dtype=np.float32)


def write_glb(path: pathlib.Path, root_name: str, geo: Geo, anchors: list[tuple[str, tuple[float, float, float]]]) -> dict:
    mats = list(geo.tris.keys())
    buf = bytearray()
    views, accessors, meshes = [], [], []

    def add_view(data: bytes, target: int) -> int:
        while len(buf) % 4:
            buf.append(0)
        views.append({"buffer": 0, "byteOffset": len(buf), "byteLength": len(data), "target": target})
        buf.extend(data)
        return len(views) - 1

    def add_acc(view: int, ctype: int, count: int, typ: str, lo=None, hi=None) -> int:
        d = {"bufferView": view, "componentType": ctype, "count": count, "type": typ}
        if lo is not None:
            d["min"], d["max"] = lo, hi
        accessors.append(d)
        return len(accessors) - 1

    for mi, mat in enumerate(mats):
        pos, nrm = geo.arrays(mat)
        n = len(pos)
        idx = np.arange(n, dtype=np.uint32)
        pv = add_view(pos.astype("<f4").tobytes(), 34962)
        nv = add_view(nrm.astype("<f4").tobytes(), 34962)
        iv = add_view(idx.astype("<u4").tobytes(), 34963)
        pa = add_acc(pv, 5126, n, "VEC3", [float(x) for x in pos.min(axis=0)], [float(x) for x in pos.max(axis=0)])
        na = add_acc(nv, 5126, n, "VEC3")
        ia = add_acc(iv, 5125, n, "SCALAR")
        meshes.append({"name": mat, "primitives": [{"attributes": {"POSITION": pa, "NORMAL": na}, "indices": ia, "material": mi}]})

    nodes = [{"name": root_name, "children": []}]
    for mi, mat in enumerate(mats):
        nodes.append({"name": mat, "mesh": mi})
        nodes[0]["children"].append(len(nodes) - 1)
    for name, (x, y, z) in anchors:
        nodes.append({"name": f"anchor_{name}", "translation": [float(x), float(y), float(z)]})
        nodes[0]["children"].append(len(nodes) - 1)

    materials = []
    for mat in mats:
        rgba, metallic, rough = MATERIALS[mat]
        materials.append({"name": mat, "pbrMetallicRoughness": {
            "baseColorFactor": list(rgba), "metallicFactor": metallic, "roughnessFactor": rough}})

    while len(buf) % 4:
        buf.append(0)
    doc = {
        "asset": {"version": "2.0", "generator": "UptimeTrace frontend/scripts/infra-models/build_models.py"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": materials,
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [{"byteLength": len(buf)}],
    }
    json_bytes = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    json_bytes += b" " * ((4 - len(json_bytes) % 4) % 4)
    bin_bytes = bytes(buf)
    total = 12 + 8 + len(json_bytes) + 8 + len(bin_bytes)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(struct.pack("<4sII", b"glTF", 2, total))
        f.write(struct.pack("<II", len(json_bytes), 0x4E4F534A))
        f.write(json_bytes)
        f.write(struct.pack("<II", len(bin_bytes), 0x004E4942))
        f.write(bin_bytes)

    lo = np.min([np.min(geo.arrays(m)[0], axis=0) for m in mats], axis=0)
    hi = np.max([np.max(geo.arrays(m)[0], axis=0) for m in mats], axis=0)
    return {"path": str(path), "bytes": total, "size_m": [round(float(v), 4) for v in hi - lo],
            "min": [round(float(v), 4) for v in lo], "max": [round(float(v), 4) for v in hi],
            "triangles": sum(len(geo.tris[m][0]) // 3 for m in mats), "anchors": [a for a, _ in anchors]}


# ---- physical-server: compute.physical-server, footprint 1.4 x 0.7 x 1.0 -------------------------------------------
def physical_server() -> tuple[Geo, list]:
    g = Geo()
    g.box("chassis", (0, 0.35, 0), (1.4, 0.7, 1.0))
    g.box("panel", (0, 0.35, 0.502), (1.32, 0.6, 0.004))           # front faceplate, proud of the face
    for i in range(6):                                              # six drive bays
        g.box("dark", ((i - 2.5) * 0.2, 0.35, 0.504), (0.16, 0.2, 0.008))
    g.box("accent", (0.6, 0.12, 0.504), (0.05, 0.05, 0.008))        # power button (neutral, not a status light)
    for i in range(5):                                              # top vent slots
        g.box("dark", (0, 0.702, (i - 2) * 0.14), (0.9, 0.004, 0.03))
    anchors = [("top", (0, 0.7, 0)), ("bottom", (0, 0, 0)), ("front", (0, 0.35, 0.5)),
               ("back", (0, 0.35, -0.5)), ("left", (-0.7, 0.35, 0)), ("right", (0.7, 0.35, 0))]
    return g, anchors


# ---- microservice: application.microservice, footprint 1.0 x 1.0 x 1.0 -------------------------------------------
def microservice() -> tuple[Geo, list]:
    g = Geo()
    # Regular octagon with flat faces at x = +-0.5 and z = +-0.5, so its footprint is exactly 1.0 x 1.0.
    radius = 0.5 / math.cos(math.radians(22.5))
    pts = [(radius * math.cos(math.radians(22.5 + 45 * k)), radius * math.sin(math.radians(22.5 + 45 * k))) for k in range(8)]
    g.prism("side", "cap", pts, 0.0, 1.0)
    g.box("accent", (0, 1.003, 0), (0.36, 0.006, 0.36))             # raised service core on top
    anchors = [("top", (0, 1.0, 0)), ("bottom", (0, 0, 0)), ("front", (0, 0.5, 0.5)),
               ("back", (0, 0.5, -0.5)), ("left", (-0.5, 0.5, 0)), ("right", (0.5, 0.5, 0))]
    return g, anchors


# ---- network-switch: networking.network-switch, footprint 1.6 x 0.25 x 0.8 ---------------------------------------
def network_switch() -> tuple[Geo, list]:
    g = Geo()
    g.box("chassis", (0, 0.125, 0), (1.6, 0.25, 0.8))
    for row_y in (0.165, 0.085):                                    # two rows of twelve ports on the front
        for i in range(12):
            g.box("dark", (-0.66 + i * 0.12, row_y, 0.402), (0.07, 0.055, 0.004))
    g.box("accent", (0, 0.012, 0.402), (1.6, 0.02, 0.004))          # thin edge strip along the bottom of the front
    for i in range(10):                                             # top ventilation slots
        g.box("dark", (-0.72 + i * 0.16, 0.251, 0), (0.03, 0.002, 0.5))
    anchors = [("top", (0, 0.25, 0)), ("bottom", (0, 0, 0)), ("front", (0, 0.125, 0.4)),
               ("back", (0, 0.125, -0.4)), ("left", (-0.8, 0.125, 0)), ("right", (0.8, 0.125, 0))]
    return g, anchors


# ---- postgresql: data.postgresql, footprint 1.0 x 1.1 x 1.0 ----------------------------------------------------------
def postgresql() -> tuple[Geo, list]:
    """A stacked-disc database cylinder: the conventional database form. Its bands and seams use the same
    materials as the other models, so it reads as part of the same set."""
    g = Geo()
    segments = 32  # vertices at radius 0.5 include the four axis points, so the bounds are exactly 1.0 x 1.0
    pts = [(0.5 * math.cos(2 * math.pi * k / segments), 0.5 * math.sin(2 * math.pi * k / segments)) for k in range(segments)]
    bands = [("chassis", 0.0, 0.36), ("dark", 0.36, 0.38), ("chassis", 0.38, 0.72), ("dark", 0.72, 0.74), ("chassis", 0.74, 1.1)]
    for mat, y0, y1 in bands:
        g.tube(mat, pts, y0, y1)
    g.disk("cap", pts, 1.1, up=True)     # lit top disc
    g.disk("side", pts, 0.0, up=False)   # base
    anchors = [("top", (0, 1.1, 0)), ("bottom", (0, 0, 0)), ("front", (0, 0.55, 0.5)),
               ("back", (0, 0.55, -0.5)), ("left", (-0.5, 0.55, 0)), ("right", (0.5, 0.55, 0))]
    return g, anchors


MODELS = {
    "compute/physical-server": physical_server,
    "application/microservice": microservice,
    "networking/network-switch": network_switch,
    "data/postgresql": postgresql,
}


def main() -> int:
    report = {}
    for rel, builder in MODELS.items():
        geo, anchors = builder()
        info = write_glb(OUT / f"{rel}.glb", rel.split("/")[1], geo, anchors)
        report[rel] = info
        print(f"{rel}: size {info['size_m']} m, {info['triangles']} triangles, {info['bytes']} bytes, anchors {info['anchors']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
