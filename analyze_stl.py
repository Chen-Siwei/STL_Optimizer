#!/usr/bin/env python3
"""Reverse-engineer and audit an existing STL.

Use this as the FIRST step whenever the user hands you a mesh instead of a
description: it recovers the geometry you need to write a parametric model,
and it reports the structural/manufacturing numbers you need to justify an
optimization.

Reports:
  - envelope: bbox, extents, volume, surface area, mass estimate
  - integrity: watertight, Euler number, connected components
  - wall thickness: ray-cast thickness distribution on all 3 axes
    (min / median / max) - tells you if anything is thinner than your
    nozzle or load case allows
  - through channels: rays hitting 4+ surfaces, clustered -> hole/pocket
    positions and approximate diameters
  - overhang audit: surface area by face tilt (needs support < 45 deg)
  - cross-sections: ASCII occupancy maps on three orthogonal planes

Optionally compares against a second STL (--compare) to quantify what an
optimization actually changed.

Usage:
    python analyze_stl.py part.stl
    python analyze_stl.py part.stl --density 1.24        # PLA, for mass
    python analyze_stl.py part.stl --compare opt.stl
    python analyze_stl.py part.stl --json                # machine readable
"""

import argparse
import json
import sys
from collections import Counter

import numpy as np
import trimesh

AXES = {
    "X": (1, 0, 0),
    "Y": (0, 1, 0),
    "Z": (0, 0, 1),
}


def load(path):
    m = trimesh.load(path, force="mesh")
    if m.is_empty:
        raise SystemExit(f"empty mesh: {path}")
    return m


def envelope(m, density):
    bb = m.bounds
    out = {
        "bbox_min": [round(float(v), 2) for v in bb[0]],
        "bbox_max": [round(float(v), 2) for v in bb[1]],
        "extents_mm": [round(float(v), 2) for v in m.extents],
        "volume_mm3": round(float(m.volume), 1),
        "surface_mm2": round(float(m.area), 1),
        "triangles": int(len(m.faces)),
        "vertices": int(len(m.vertices)),
    }
    if density:
        out["density_g_cm3"] = density
        out["mass_g"] = round(float(m.volume) * density / 1000.0, 1)
    return out


def integrity(m):
    try:
        comps = len(m.split(only_watertight=False))
    except Exception:
        comps = None
    return {
        "watertight": bool(m.is_watertight),
        "euler_number": int(m.euler_number),
        "connected_components": comps,
    }


def ray_hits(m, origin, direction):
    """Sorted coordinates along the ray axis where it crosses the surface."""
    locs, _, _ = m.ray.intersects_location(
        np.array([origin], dtype=float), np.array([direction], dtype=float),
        multiple_hits=True,
    )
    if len(locs) == 0:
        return []
    axis = 0 if direction[0] != 0 else (1 if direction[1] != 0 else 2)
    return sorted(float(v) for v in locs[:, axis])


def grid_axes(m, n=24):
    """Sample grid spanning the part on each axis."""
    lo, hi = m.bounds
    return [
        tuple(np.linspace(lo[i] + 0.5, hi[i] - 0.5, n)) for i in range(3)
    ]


def wall_thickness(m, n=24):
    """Two-hit rays give a direct wall/section thickness."""
    result = {}
    grids = grid_axes(m, n)
    for name, direction in AXES.items():
        axis = "XYZ".index(name)
        others = [i for i in range(3) if i != axis]
        g0, g1 = grids[others[0]], grids[others[1]]
        thick, thin = [], []
        for a in g0:
            for b in g1:
                origin = [0.0, 0.0, 0.0]
                origin[others[0]] = a
                origin[others[1]] = b
                origin[axis] = m.bounds[0][axis] - 5.0
                h = ray_hits(m, origin, direction)
                if len(h) == 2:
                    thick.append(h[1] - h[0])
                elif len(h) >= 3:
                    # first solid span
                    thick.append(h[1] - h[0])
        if thick:
            arr = np.array(thick)
            result[name] = {
                "min_mm": round(float(arr.min()), 2),
                "median_mm": round(float(np.median(arr)), 2),
                "max_mm": round(float(arr.max()), 2),
                "samples": int(len(arr)),
                "below_1_2mm": int((arr < 1.2).sum()),
                "below_2_0mm": int((arr < 2.0).sum()),
            }
    return result


def connected_regions(mask, g0, g1):
    """4-connected components of a boolean grid -> point arrays."""
    seen = np.zeros_like(mask)
    out = []
    for i in range(mask.shape[0]):
        for j in range(mask.shape[1]):
            if not mask[i, j] or seen[i, j]:
                continue
            stack, comp = [(i, j)], []
            seen[i, j] = True
            while stack:
                ci, cj = stack.pop()
                comp.append((g0[ci], g1[cj]))
                for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ni, nj = ci + di, cj + dj
                    if (0 <= ni < mask.shape[0] and 0 <= nj < mask.shape[1]
                            and mask[ni, nj] and not seen[ni, nj]):
                        seen[ni, nj] = True
                        stack.append((ni, nj))
            out.append(np.array(comp))
    return out


def through_channels(m, n=40):
    """Rays with 4+ hits pass through a void: bores, pockets, cavities."""
    found = {"X": [], "Y": [], "Z": []}
    grids = grid_axes(m, n)
    for name, direction in AXES.items():
        axis = "XYZ".index(name)
        others = [i for i in range(3) if i != axis]
        g0 = np.asarray(grids[others[0]])
        g1 = np.asarray(grids[others[1]])
        pts = []
        for a in g0:
            for b in grids[others[1]]:
                origin = [0.0, 0.0, 0.0]
                origin[others[0]] = a
                origin[others[1]] = b
                origin[axis] = m.bounds[0][axis] - 5.0
                if len(ray_hits(m, origin, direction)) >= 4:
                    pts.append([a, b])
        if not pts:
            continue
        pts = np.array(pts)
        # snap hit points onto the sample grid and label 4-connected regions
        i0 = np.abs(pts[:, 0][:, None] - g0[None, :]).argmin(axis=1)
        i1 = np.abs(pts[:, 1][:, None] - g1[None, :]).argmin(axis=1)
        mask = np.zeros((len(g0), len(g1)), dtype=bool)
        mask[i0, i1] = True
        clusters = connected_regions(mask, g0, g1)
        for c in clusters:
            lo, hi = c.min(axis=0), c.max(axis=0)
            found[name].append({
                # center/size reported in the two axes perpendicular to the bore
                "center_uv": [
                    round(float((lo[0] + hi[0]) / 2), 1),
                    round(float((lo[1] + hi[1]) / 2), 1),
                ],
                "approx_size_mm": [
                    round(float(hi[0] - lo[0]), 1),
                    round(float(hi[1] - lo[1]), 1),
                ],
            })
    return {k: v for k, v in found.items() if v}


def overhang_audit(m, tilt_limit=45.0):
    """Face area by tilt from horizontal; faces pointing DOWN tilt < limit
    have material stacked above them and need support. Upward-facing faces
    (nz >= 0) never need support."""
    n = m.face_normals
    a = m.area_faces
    c = m.triangles_center
    nz = n[:, 2]
    # tilt from horizontal, only meaningful for downward-facing faces
    tilt = np.where(nz < 0, np.degrees(np.arccos(np.clip(-nz, -1, 1))), 999.0)
    bed = (nz < -0.99) & (np.abs(c[:, 2]) < 0.3)   # faces lying on the bed
    need = (tilt < tilt_limit) & ~bed
    severe = (tilt < 30.0) & ~bed
    return {
        "print_up_axis": "+Z",
        "needs_support_area_mm2": round(float(a[need].sum()), 1),
        "severe_below_30deg_mm2": round(float(a[severe].sum()), 1),
        "total_area_mm2": round(float(a.sum()), 1),
    }


def occupancy(m, plane, fixed, step=2.0):
    """ASCII occupancy slice: '#' = material, '.' = void."""
    lo, hi = m.bounds
    idx = {"x": 0, "y": 1, "z": 2}[plane]
    us = np.arange(lo[(idx + 1) % 3], hi[(idx + 1) % 3], step)
    vs = np.arange(lo[(idx + 2) % 3], hi[(idx + 2) % 3], step)
    pts = []
    for v in vs:
        for uu in us:
            p = [0.0, 0.0, 0.0]
            p[idx] = fixed
            p[(idx + 1) % 3] = uu
            p[(idx + 2) % 3] = v
            pts.append(p)
    res = m.contains(np.array(pts, dtype=float)).reshape(len(vs), len(us))
    lines = [f"  {plane}={fixed}  (rows: {'xyz'[(idx+2)%3]} {vs[0]:.0f}..{vs[-1]:.0f}, "
             f"cols: {'xyz'[(idx+1)%3]} {us[0]:.0f}..{us[-1]:.0f}, step {step})"]
    for i, v in enumerate(vs):
        lines.append(f"  {v:6.1f} " + "".join("#" if b else "." for b in res[i]))
    return "\n".join(lines)


def report(m, path, density, slices=True):
    r = {
        "file": path,
        "envelope": envelope(m, density),
        "integrity": integrity(m),
        "wall_thickness": wall_thickness(m),
        "through_channels": through_channels(m),
        "overhang_audit_up_Z": overhang_audit(m),
    }
    print(f"=== {path} ===")
    e = r["envelope"]
    print(f"  envelope : {e['extents_mm'][0]} x {e['extents_mm'][1]} x {e['extents_mm'][2]} mm"
          f"   volume {e['volume_mm3']} mm3"
          + (f"   mass ~{e['mass_g']} g @ {e['density_g_cm3']} g/cm3" if density else ""))
    i = r["integrity"]
    print(f"  integrity: watertight={i['watertight']}  euler={i['euler_number']}"
          f"  components={i['connected_components']}  tris={e['triangles']}")
    print("  wall thickness (ray-cast solid spans):")
    for ax, v in r["wall_thickness"].items():
        print(f"    {ax}: min {v['min_mm']} / median {v['median_mm']} / max {v['max_mm']} mm"
              f"   (<1.2mm: {v['below_1_2mm']}, <2.0mm: {v['below_2_0mm']} of {v['samples']})")
    ch = r["through_channels"]
    if ch:
        print("  through channels (bore/pocket candidates):")
        for ax, lst in ch.items():
            for c in lst:
                print(f"    axis {ax}: center_uv {c['center_uv']}  approx {c['approx_size_mm']} mm")
    else:
        print("  through channels: none detected")
    o = r["overhang_audit_up_Z"]
    print(f"  overhang : needs support (<45deg) {o['needs_support_area_mm2']} mm2,"
          f" severe (<30deg) {o['severe_below_30deg_mm2']} mm2 of {o['total_area_mm2']} mm2")
    if slices:
        lo, hi = m.bounds
        print("  cross-sections:")
        print(occupancy(m, "z", float((lo[2] + hi[2]) / 2)))
        print(occupancy(m, "y", float((lo[1] + hi[1]) / 2)))
        print(occupancy(m, "x", float((lo[0] + hi[0]) / 2)))
    return r


def compare(a, b, density):
    ea, eb = envelope(a, density), envelope(b, density)
    oa, ob = overhang_audit(a), overhang_audit(b)
    dv = eb["volume_mm3"] - ea["volume_mm3"]
    pct = 100.0 * dv / ea["volume_mm3"]
    print("=== A/B comparison ===")
    print(f"  volume   : {ea['volume_mm3']} -> {eb['volume_mm3']} mm3 ({pct:+.1f}%)"
          + (f"   mass {ea['mass_g']} -> {eb['mass_g']} g" if density else ""))
    print(f"  extents  : {ea['extents_mm']} -> {eb['extents_mm']} mm")
    def delta_pct(a0, b0):
        return f"({100*(b0-a0)/max(a0,1e-9):+.1f}%)" if a0 else ""
    print(f"  support  : {oa['needs_support_area_mm2']} -> {ob['needs_support_area_mm2']} mm2 "
          + delta_pct(oa["needs_support_area_mm2"], ob["needs_support_area_mm2"]))
    print(f"  severe   : {oa['severe_below_30deg_mm2']} -> {ob['severe_below_30deg_mm2']} mm2 "
          + delta_pct(oa["severe_below_30deg_mm2"], ob["severe_below_30deg_mm2"]))
    return {
        "volume_delta_mm3": dv,
        "volume_delta_pct": round(pct, 2),
        "support_area_delta_mm2": ob["needs_support_area_mm2"] - oa["needs_support_area_mm2"],
        "severe_delta_mm2": ob["severe_below_30deg_mm2"] - oa["severe_below_30deg_mm2"],
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("stl")
    p.add_argument("--compare", help="second STL to diff against")
    p.add_argument("--density", type=float, default=None,
                   help="material density g/cm3 for mass (PLA 1.24, PETG 1.27, ABS 1.04)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-slices", action="store_true")
    args = p.parse_args(argv)

    m = load(args.stl)
    r = report(m, args.stl, args.density, slices=not args.no_slices)
    if args.compare:
        r["comparison"] = compare(m, load(args.compare), args.density)
    if args.json:
        print(json.dumps(r, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
