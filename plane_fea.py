"""Plane-stress FEA on a variable-thickness plate model of a bracket.

Why: section properties (I, A) cannot see triangulation. A diagonal brace
changes the load path from bending to axial, and the only honest way to
compare an L against a triangulated L is to solve for compliance.

How: sample the solid z-extent at every (x, y) cell -> a thickness map.
Treat that as a variable-thickness plate in plane stress, mesh it with 1 mm
constant-strain triangles, fix one leg's mounting face, load the other leg's
tip perpendicular to its plate (= the corner-opening mode), and measure
displacement. Absolute numbers are approximate; the before/after RATIO is
the useful output, because every model gets the identical mesh and BC.

Usage: python plane_fea.py a.stl b.stl c.stl
"""

import sys
import numpy as np
import trimesh
import scipy.sparse as sp
import scipy.sparse.linalg as spl
from scipy.sparse.csgraph import connected_components

E = 2400.0      # MPa  (PLA; only scales the result, ratios are E-independent)
NU = 0.35
H_CELL = 1.0    # mm in-plane mesh
SPAN = 80.0     # mm model extent in x and y
Z_TOP = 40.0    # mm part height
LOAD_N = 100.0  # N total, applied at the free tip of leg B, in -X
MIN_T = 0.5     # mm - cells thinner than this are dropped (bores, edges)


def thickness_map(m, chunk=200):
    """Solid z-extent at each (x, y) cell, by ray casting along +Z.
    One ray per cell (6400) instead of one point per voxel (256k) - the
    point-in-mesh route allocates rays x triangles and dies on a 12k-tri
    mesh, so walk the entry/exit pairs instead."""
    xs = np.arange(H_CELL / 2, SPAN, H_CELL)
    ys = np.arange(H_CELL / 2, SPAN, H_CELL)
    # order must match reshape(len(xs), len(ys)): x varies slowest
    pts = np.array([[x, y] for x in xs for y in ys], dtype=float)
    tk = np.zeros(len(pts))
    dirs = np.tile(np.array([[0.0, 0.0, 1.0]]), (chunk, 1))
    for s in range(0, len(pts), chunk):
        blk = pts[s:s + chunk]
        origins = np.column_stack([blk, np.full(len(blk), -5.0)])
        locs, ri, _ = m.ray.intersects_location(
            origins, dirs[:len(blk)], multiple_hits=True)
        for k in range(len(blk)):
            zs = np.sort(locs[ri == k][:, 2])
            # entry/exit pairs: everything between hit 2n and 2n+1 is solid
            tk[s + k] = float(np.sum(zs[1::2] - zs[0::2])) if len(zs) >= 2 else 0.0
    return xs, ys, tk.reshape(len(xs), len(ys))


def solve(m):
    xs, ys, tk = thickness_map(m)
    nx, ny = len(xs), len(ys)

    def nid(i, j):
        return i * (ny + 1) + j

    nnode = (nx + 1) * (ny + 1)
    tris = []            # (n1, n2, n3, thickness)
    for i in range(nx):
        for j in range(ny):
            if tk[i, j] < MIN_T:
                continue
            a, b = nid(i, j), nid(i + 1, j)
            c, d = nid(i + 1, j + 1), nid(i, j + 1)
            tris.append((a, b, c, tk[i, j]))
            tris.append((a, c, d, tk[i, j]))
    if not tris:
        return None

    # keep only the largest connected component of the element graph
    adj = sp.lil_matrix((nnode, nnode))
    for a, b, c, _ in tris:
        for p, q in ((a, b), (b, c), (a, c)):
            adj[p, q] = 1
            adj[q, p] = 1
    adj = adj.tocsr()
    used = np.array(sorted({n for t in tris for n in t[:3]}))
    ncomp, lab = connected_components(adj[used][:, used], directed=False)
    if ncomp > 1:
        sizes = np.bincount(lab)
        keep_lab = sizes.argmax()
        keep = set(used[lab == keep_lab].tolist())
        tris = [t for t in tris if t[0] in keep]
        print(f"    (dropped {ncomp-1} disconnected patch(es))")

    D = E / (1 - NU ** 2) * np.array([[1, NU, 0],
                                      [NU, 1, 0],
                                      [0, 0, (1 - NU) / 2]])

    rows, cols, vals = [], [], []
    for a, b, c, th in tris:
        # node coordinates: x = index // (ny+1), y = index % (ny+1) -> times H_CELL
        p = [( (n // (ny + 1)) * H_CELL, (n % (ny + 1)) * H_CELL ) for n in (a, b, c)]
        (x1, y1), (x2, y2), (x3, y3) = p
        det = (x2 - x1) * (y3 - y1) - (x3 - x1) * (y2 - y1)
        area = abs(det) / 2.0
        if area < 1e-9:
            continue
        bb = [y2 - y3, y3 - y1, y1 - y2]
        cc = [x3 - x2, x1 - x3, x2 - x1]
        B = np.zeros((3, 6))
        for k in range(3):
            B[0, 2 * k] = bb[k]
            B[1, 2 * k + 1] = cc[k]
            B[2, 2 * k] = cc[k]
            B[2, 2 * k + 1] = bb[k]
        B /= (2 * area)
        Ke = th * area * (B.T @ D @ B)
        dofs = [2 * n for n in (a, b, c)]
        idx = []
        for n in (a, b, c):
            idx += [2 * n, 2 * n + 1]
        for r in range(6):
            for s in range(6):
                rows.append(idx[r]); cols.append(idx[s]); vals.append(Ke[r, s])

    ndf = 2 * nnode
    K = sp.coo_matrix((vals, (rows, cols)), shape=(ndf, ndf)).tocsr()

    # --- boundary conditions ---
    in_el = np.zeros(nnode, dtype=bool)
    for t in tris:
        in_el[t[0]] = in_el[t[1]] = in_el[t[2]] = True

    # Two boundary conditions, because the ratio depends on it:
    #   'face'  - the whole mounting face is clamped (a rigid panel, the
    #             idealised and very optimistic case)
    #   'bolts' - only the two bore locations are held, which is what a
    #             bolted joint actually does; leg A can then also bend
    fixed_modes = {}
    face_fixed = []
    for i in range(nx + 1):
        if i * H_CELL <= 73.0:              # leg A mounting face, y = 0
            n = nid(i, 0)
            if in_el[n]:
                face_fixed += [2 * n, 2 * n + 1]
    bolt_fixed = []
    for i in range(nx + 1):
        for j in range(3):
            x = i * H_CELL
            if abs(x - 20.0) <= 4.0 or abs(x - 60.0) <= 4.0:
                n = nid(i, j)
                if in_el[n]:
                    bolt_fixed += [2 * n, 2 * n + 1]
    fixed_modes["face"] = face_fixed
    fixed_modes["bolts"] = bolt_fixed
    # load enters where panel B is bolted on: the two bores of leg B.
    # (Loading the free tip instead would contradict the "tip is unloaded"
    #  assumption used to taper the tips, and unfairly penalise that design.)
    load_nodes = []
    for i in range(nx + 1):
        for j in range(ny + 1):
            x, y = i * H_CELL, j * H_CELL
            if x >= 74.0 and (16.0 <= y <= 24.0 or 56.0 <= y <= 64.0):
                n = nid(i, j)
                if in_el[n]:
                    load_nodes.append(n)
    if not load_nodes:
        return None
    f = np.zeros(ndf)
    for n in load_nodes:
        f[2 * n] = -LOAD_N / len(load_nodes)

    # only solve on DOFs that belong to a retained element (the rest are
    # zero rows and make K singular)
    kept_dofs = np.concatenate([np.where(in_el)[0] * 2, np.where(in_el)[0] * 2 + 1])
    out = {"elements": len(tris), "nodes": len(load_nodes),
           "mass_g": m.volume * 1.24 / 1000}
    for mode, fixed in fixed_modes.items():
        free = np.setdiff1d(kept_dofs, np.array(fixed, dtype=int))
        u = np.zeros(ndf)
        u[free] = spl.spsolve(K[free][:, free].tocsc(), f[free])
        d = abs(float(np.mean([u[2 * n] for n in load_nodes])))
        out[f"disp_{mode}_mm"] = d
        out[f"k_{mode}"] = LOAD_N / d
    return out


def main():
    print(f"plane-stress FEA | E={E} MPa, nu={NU}, {H_CELL} mm mesh, "
          f"{LOAD_N} N at leg B tip (-X), leg A face y=0 fixed\n")
    results = {}
    for path in sys.argv[1:]:
        m = trimesh.load(path, force="mesh")
        r = solve(m)
        if r is None:
            print(f"{path}: no load path found (check the model)"); continue
        results[path] = r
        print(f"  {path.split('/')[-1]:26s} "
              f"k(bolts)={r['k_bolts']:8.1f} N/mm   k(face)={r['k_face']:8.1f} N/mm   "
              f"mass={r['mass_g']:5.1f} g")
    for mode in ("bolts", "face"):
        base = list(results.values())[0]
        print(f"\n  relative to the first model  [BC: {mode}]")
        for p, r in results.items():
            kb = base[f"k_{mode}"]
            print(f"    {p.split('/')[-1]:26s} stiffness x{r[f'k_{mode}']/kb:6.2f}   "
                  f"stiffness/mass x{(r[f'k_{mode}']/r['mass_g'])/(kb/base['mass_g']):6.2f}")


if __name__ == "__main__":
    main()
