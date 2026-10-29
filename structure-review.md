# Structural Review

Companion to `design-review.md`. That file asks "will it print cleanly?".
This one asks the two questions that actually decide whether a part is any
good: **does it carry its load without breaking**, and **is there material
in it that isn't doing any work?**

Use this at Phase 3, and any time you are handed an existing mesh to
improve. Every item below is quantitative where possible — measure, don't
eyeball.

## The two goals, in order

1. **Structural efficiency** — carry the design load with the least
   material. Stiffness and strength come from *where* material sits, not
   how much of it there is.
2. **Remove redundancy** — material in low-stress regions is dead weight.
   It costs print time and adds nothing but mass.

Manufacturability (supports, overhangs, wall thickness) is a **constraint**
on both, not a goal in itself. A part that prints beautifully and snaps in
service is a failure.

## Load path first

Never optimize geometry you haven't mentally loaded. Before touching
dimensions, state:

- Where does the load enter, where does it leave? Draw the path.
- What is the dominant mode: bending, tension/compression, shear, torsion,
  buckling?
- Is it static, cyclic (fatigue), or impact? Cyclic and impact loads
  punish stress concentrators far more than a static load does.

Then place material along that path and take it away everywhere else.
If the user cannot describe the load, say so and state your assumption in
the script header (`# assumed load: ...`) so they can correct it.

## Stress concentrators — the usual killers

Fix these before adding material anywhere. A radius costs nothing and buys
more strength than extra wall thickness.

| Feature | Bad | Good |
|---|---|---|
| Inner corner radius | r = 0 (sharp) | r >= 0.5 * t, ideally r ~ t |
| Section change | abrupt step | taper / fillet over >= 3x the step |
| Hole near edge | edge distance < 1x hole dia | >= 1.5x dia (2x for cyclic) |
| Hole near hole | spacing < 2x dia | >= 2.5x dia |
| Hole in a loaded plate | bare hole | boss / flange / rim around it |
| Cantilever root | constant section | tapered root or gusset |
| Wall thickness jump | t1 -> t2 instantly | blend over >= 3x |t1-t2| |

Sharp inner corners are the single most common cause of FDM parts cracking.
If a fillet is geometrically impossible, chamfer it — a 45° chamfer is
still far better than a sharp corner.

## Where stiffness actually comes from

Bending stiffness is `E * I`. `I` (second moment of area) rewards material
**far from the neutral axis**, not material piled up near it.

- Doubling a plate's thickness: 8x the stiffness, 2x the mass.
- Adding ribs on the outside of the same plate: can beat 1.5x thickness in
  stiffness at a fraction of the mass.
- Rib rule of thumb: rib thickness `0.5-0.6 * t` of the parent wall,
  rib height `<= 3 * rib thickness` (taller ribs buckle or warp), fillet
  the rib root `0.25-0.5 * rib thickness`.
- A hollow box or I-section beats a solid slug of the same mass every time.

## Removing redundant material

Work down this list; stop when stiffness or print quality starts to
suffer.

1. **Hollow it out.** Solid blocks are almost always over-designed. Shell
   to `2-3 walls` worth of thickness (2.0-2.4mm for 0.4mm nozzle) and let
   infill carry the rest — or shell it and rib it.
2. **Pocket the low-stress zones.** Round or oval pockets, never sharp
   rectangles. Keep every pocket corner radius `>= 1mm` and leave at least
   `2x wall` of material between a pocket and any edge or hole.
3. **Replace thickness with ribs.** If a wall is thicker than ~4mm and not
   a wear or load-bearing surface, it is a candidate: shell it to 2.4mm
   and add ribs on the load path.
4. **Delete duplicate structure.** Two plates doing one plate's job,
   redundant bosses, cosmetic thickness — cut them.
5. **Watch the pockets you add.** A pocket that interrupts the load path
   costs more strength than the mass it saves. Never place a pocket
   between two load-bearing features; go around it.

## FDM anisotropy — design for it

Printed parts are weaker across layer lines than along them. Treat the
build direction as a material property, not just a manufacturing detail.

- Interlayer (Z) strength is roughly **50-70%** of in-layer strength.
- Loads that pull layers apart (peel / tension across Z) are the failure
  mode. Loads pressing layers together are fine.
- **Put the load path in-plane.** If a bracket takes a vertical load,
  print it so those layers run along the load, not stacked across it.
- Thin walls loaded in Z fail first — a 2-wall (0.8mm) wall is fine in
  shear, marginal in Z tension.
- Sharp horizontal holes and pockets create layer-line stress risers.

This is the one place where printability and structure genuinely conflict.
If the best print orientation and the best structural orientation disagree,
say so explicitly and let the user choose — do not silently pick one.

## Measurement helpers

Run `analyze_stl.py` for the envelope, wall thickness, channel detection,
and overhang audit. For the structural numbers below, drop this into a
script next to the STL:

```python
import numpy as np, trimesh

m = trimesh.load("part.stl", force="mesh")
lo, hi = m.bounds

# --- mass / volume ---
DENSITY = {"PLA": 1.24, "PETG": 1.27, "ABS": 1.04, "TPU": 1.21,
           "AL6061": 2.70, "STEEL": 7.85}
print("mass g:", round(m.volume * DENSITY["PLA"] / 1000, 1))

# --- second moment of area of a section (stiffness proxy) ---
# This is I of the WHOLE section about its own centroid axis. It is only
# meaningful as a before/after comparison on the same plane and offset
# (same geometry family), not as an absolute local bending number.
# Sanity check on a plain rectangle b x h: I = b*h**3/12.
def section_I(plane, offset, step=0.5):
    idx = {"x": 0, "y": 1, "z": 2}[plane]
    a, b = (idx + 1) % 3, (idx + 2) % 3
    us = np.arange(lo[a], hi[a], step); vs = np.arange(lo[b], hi[b], step)
    grid = []
    for v in vs:
        for u in us:
            p = [0.0, 0.0, 0.0]; p[idx] = offset; p[a] = u; p[b] = v
            grid.append(p)
    inside = m.contains(np.array(grid, dtype=float)).reshape(len(vs), len(us))
    h = inside.sum(axis=0) * step            # material extent per column
    if h.sum() == 0:
        return 0.0
    uc = (us * h).sum() / h.sum()
    return float((h * (us - uc) ** 2).sum() * step + (h ** 3).sum() * step / 12.0)

print("section I at z=20:", round(section_I("z", 20.0), 1), "mm^4")
# step=0.5 keeps this fast; step=0.2 on an 80mm part is ~160k contains() calls

# --- wall thickness sweep (ray cast; analyze_stl.py does this per axis) ---
def wall_stats(direction=(0, 0, 1), n=40):
    us = np.linspace(lo[0], hi[0], n); vs = np.linspace(lo[1], hi[1], n)
    th = []
    for u in us:
        for v in vs:
            locs, _, _ = m.ray.intersects_location(
                np.array([[u, v, lo[2] - 5]], float),
                np.array([direction], float), multiple_hits=True)
            if len(locs) >= 2:
                zs = sorted(locs[:, 2]); th.append(zs[1] - zs[0])
    return round(min(th), 2), round(float(np.median(th)), 2)
print("wall (min, median):", wall_stats())
```

### `plane_fea.py` — when section properties cannot see the design

`I` and `A` are **blind to triangulation**. Adding a diagonal brace does not
change any leg's own section, yet it can multiply the part's stiffness ten
times over, because it converts the load path from bending to axial. When a
proposed redesign relies on changing the *load path* (braces, gussets,
closed sections), do not argue from section properties — solve it.

`plane_fea.py` does that: it samples the solid z-extent at every (x, y)
cell, treats the result as a variable-thickness plate in plane stress,
meshes it with 1 mm constant-strain triangles, and reports tip stiffness
for a corner-opening load case under two boundary conditions (whole face
clamped vs. bolts only).

```
python3 plane_fea.py original.stl variant_a.stl variant_b.stl
```

Read the **ratio** between models, not the absolute number: every model
gets the identical mesh, material, and load, so the comparison is fair
while the absolute value is only as good as the idealisation (in-plane
load, mounting-face clamp). If the load case does not match the user's
service load, say so.

Worked example (90 deg corner bracket, load at the bolt positions):

| model                       | mass   | stiffness (bolt BC) | stiffness/mass |
|-----------------------------|--------|---------------------|----------------|
| original L, pockets at bores| 39.3 g | 49 N/mm   (x1.0)    | x1.0           |
| same L, pockets deleted     | 48.9 g | 112 N/mm  (x2.3)    | x1.8           |
| triangulated + bosses       | 60.1 g | 1462 N/mm (x30)     | x19            |

One 8 x 20 mm diagonal brace bought a 13x stiffness increase for +23%
mass. No amount of extra wall thickness gets close to that.

## Review checklist

- [ ] Load path stated, and the script header records the assumption
- [ ] Every inner corner has a radius; no sharp re-entrant corners
- [ ] Wall thickness uniform, or changes are blended
- [ ] Minimum wall >= 1.2mm (0.4mm nozzle) / >= 2.0mm if it carries load
- [ ] Hole edge distance >= 1.5x hole diameter
- [ ] Loaded holes have bosses / flanges, not bare holes in a thin plate
- [ ] Ribs: t = 0.5-0.6x parent wall, h <= 3x rib t, root filleted
- [ ] Pockets are round/oval, keep >= 2x wall to any edge or hole
- [ ] No pocket interrupts the load path
- [ ] Build direction chosen so the main load runs in-plane, not across Z
- [ ] Mass reported before and after, in grams (density x volume)
- [ ] Volume / mass reduction quoted as a percentage
- [ ] Stiffness proxy (section I) not reduced where it matters
- [ ] Load-path changes verified with plane_fea.py, not section I alone
- [ ] Bolted joints: bearing length and a flat seat for the head/washer
- [ ] Tool clearance checked for every fastener (head or socket diameter)
- [ ] Optimization validated: preview rendered, watertight, min wall checked

## Reporting an optimization

Always close with the numbers, side by side. Claims without numbers are
just opinions:

```
| Metric                | Before  | After   | Change  |
|-----------------------|---------|---------|---------|
| Mass (PLA)            | 39.3 g  | 41.2 g  | +4.9%   |
| Min wall              | 1.8 mm  | 1.8 mm  | -       |
| Inner corner radius   | 5 mm    | 7 mm    | +40%    |
| Needs-support area    | 337 mm2 | 169 mm2 | -50%    |
| Section I (load path) | n/a     | n/a     | -       |

Traded: +1.9 g of material for a 40% larger corner radius (lower stress
concentration) and half the support area.
```

If an optimization makes the part heavier, say so and justify it — stiffness
per gram is the metric that matters, not grams alone.
