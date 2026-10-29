---
name: parametric-3d-printing
description: "Use this skill whenever the user wants a real, manufactured physical part - whether they describe it from scratch or hand you an existing mesh to improve. Triggers: any mention of '3D print', 'STL', 'STEP', 'parametric model', 'Gridfinity', 'enclosure', 'bracket', 'mount', 'case', 'housing', 'CadQuery', 'OpenSCAD', or a specific FDM printer (Bambu Lab, Prusa, Ender); requests to import, analyse, reverse-engineer, repair, or optimize an existing STL/STEP file the user already has ('优化这个结构', '这个件太重了', '帮我看看这个模型', '减重', '轻量化'); structural asks such as 'make it stronger', 'it cracked here', 'stress concentration', 'add ribs/gussets', 'wall thickness', 'load path', 'stiffness', 'buckling'; material-reduction asks such as 'remove redundant material', 'lighter', 'hollow it out', 'lightening pockets', 'strength-to-weight'; and functional parts like Arduino enclosures, cable organizers, wall mounts, adapters, brackets, or mechanical components. Do NOT use for: 3D rendering, animation, game assets, digital-only art, photogrammetry, sculpting, or any 3D work that is not heading toward a real manufactured part."
---

# Parametric Structural Design & Optimization with CadQuery

## Overview

This skill designs and optimizes real, manufactured parts as **parametric
CadQuery models**. Two entry points, same destination:

- **Design from scratch** — describe an object, get a parametric model.
- **Import an existing mesh** — hand it an STL/STEP, it reverse-engineers
  the geometry, rebuilds it as editable parametric code, and optimizes it.

CadQuery (Python) is used because it installs via pip, has a Pythonic API,
and handles fillets, chamfers, booleans and assemblies better than the
alternatives.

**The optimization goal is structural efficiency: carry the design load
with the least material.** That means two things, in this order:
1. **Strength / stiffness** — put material where the load path needs it.
2. **Remove redundancy** — take it away everywhere else.

Manufacturability (supports, overhangs, minimum wall) is a **constraint**
on both, not the goal. A part that prints beautifully and snaps in service
is a failure. Read `structure-review.md` for the structural checklist and
`design-review.md` for the printability one.

## Setup

```bash
# CadQuery requires Python 3.10-3.12 (OCC kernel lacks 3.13+ wheels)
python3.12 -m venv .venv && source .venv/bin/activate

# Install CadQuery and preview dependencies
pip install cadquery trimesh pyrender Pillow numpy lxml

# Analysis / repair helpers used by this skill
pip install shapely rtree manifold3d matplotlib
```

CadQuery uses the OpenCASCADE kernel under the hood. trimesh, pyrender,
Pillow and numpy drive the preview-analyze-iterate loop; lxml backs the 3MF
writer (`stl_to_3mf.py`). `shapely` + `rtree` are needed for section
analysis, `manifold3d` to repair meshes whose booleans left degenerate
faces, `matplotlib` for section plotting. No display server is needed;
everything renders headlessly via pyrender's offscreen backend.

### Windows — if a venv already exists, skip the setup above

If a pre-built venv sits next to this skill, **use its interpreter for every CadQuery script, preview, and wrapper invocation:**

```
# Windows
<skill-dir>\.venv\Scripts\python.exe
# macOS / Linux
<skill-dir>/.venv/bin/python
```

`<skill-dir>` is wherever this folder lives (e.g. `~/.claude/skills/parametric-3d-printing`).
Fill in the exact path once per machine; do not call the naked `python`.

- Requires **Python 3.10-3.12** (CadQuery's OCC kernel has no wheel for 3.13+).
  If the system Python is newer, install 3.12 with `uv python install 3.12`
  and point `uv venv --python 3.12 .venv` at it.
- Verified against: Python 3.12.16, cadquery 2.8.0, trimesh 4.12.2,
  pyrender 0.1.45, Pillow, numpy, lxml, shapely, rtree, manifold3d, matplotlib.
- Self-check: `pytest tests` → 32 passed; `run_cadquery_model.py --preview
  --strict examples/gridfinity_d110_bin.py` → STL written, watertight,
  preview PNG rendered.
- Harmless startup warning `cannot open font '...\mstmc.ttf'` can be ignored.
- If PyPI times out, add `--index-url <mirror>` to `uv pip install`.

**If CadQuery fails to install** (OCC kernel build errors), try:
```bash
# Option 1: Use conda (CadQuery's officially recommended method)
conda install -c cadquery -c conda-forge cadquery

# Option 2: Use the pre-built wheels
pip install cadquery --find-links https://github.com/CadQuery/CadQuery/releases
```

## Importing & Reverse-Engineering an Existing Mesh

When the user attaches an STL/STEP instead of describing a part, do **not**
refuse and do **not** try to edit the mesh directly. Recover the design,
then rebuild it as parametric code. Mesh editing loses every dimension;
parametric rebuild is what makes the part actually improvable.

### Step 1 — measure it

```bash
python analyze_stl.py part.stl --density 1.24
python analyze_stl.py part.stl --compare optimized.stl    # A/B numbers
```

`analyze_stl.py` reports the envelope, mass, watertightness, ray-cast wall
thickness per axis, through-channels (bores/pockets with centers and
approximate sizes), the overhang audit, and ASCII cross-sections on three
planes. Read the cross-sections — they are the fastest way to understand
what a part actually is.

For anything the script cannot see (chamfers, blend radii, taper), measure
directly:

```python
import numpy as np, trimesh
m = trimesh.load("part.stl", force="mesh")

# thickness along a ray
locs, _, _ = m.ray.intersects_location(
    np.array([[40., 40., -1]]), np.array([[0., 0., 1.]]), multiple_hits=True)
print(sorted(locs[:, 2]))            # surface crossings along Z

# is a point inside?
m.contains(np.array([[40., 3., 20.]]))
```

### Step 2 — rebuild it as a parametric script

Write `rebuild_original.py` with every recovered dimension in the
PARAMETERS block. Then **verify the rebuild before optimizing**:

```bash
python analyze_stl.py original.stl --compare rebuild_original.stl
```

Volume within ~2% and matching extents means the reverse-engineering is
sound (a coarse source mesh shifts volume slightly). If the gap is bigger,
find the feature you got wrong — do not optimize a model you have not
reproduced.

### Step 3 — then optimize

Copy to `optimized.py` and change parameters/geometry there. Keeping both
scripts lets you diff the two and quote before/after numbers honestly.

Deliverables for an import job: the rebuild script, the optimized script,
both STLs, the STEP of the optimized part, and a before/after table.

## Structural Optimization

Read `structure-review.md` in full before optimizing. Summary of the
levers, roughly in order of payoff:

**Strength / stiffness (cheap to expensive)**
1. **Fillet every inner corner.** `r >= 0.5 * t`, ideally `r ~ t`. Sharp
   re-entrant corners are the #1 cause of cracked FDM parts.
2. **Blend section changes.** Any thickness jump gets a taper or fillet
   over at least 3x the step height.
3. **Add gussets / ribs at roots and corners**, not thickness everywhere.
   Rib `t = 0.5-0.6x` parent wall, rib `h <= 3x` rib `t`, fillet the root.
4. **Reinforce loaded holes** with bosses or flanges; keep hole edge
   distance `>= 1.5x` diameter (`2x` for cyclic loads).
5. **Orient the print so the load runs in-plane.** Interlayer (Z) strength
   is only ~50-70% of in-layer; never design a load that peels layers apart.

**Removing redundancy**
1. **Shell solid blocks** to 2.0-2.4mm and let infill or ribs carry the rest.
2. **Pocket low-stress zones** — round or oval, corner `r >= 1mm`, at least
   `2x wall` of material left to any edge or hole.
3. **Never let a pocket cross the load path.** Mass saved there costs more
   strength than it is worth.
4. **Delete duplicate structure** — two plates doing one plate's job,
   cosmetic thickness, redundant bosses.

**Trade-off to state explicitly:** if an optimization adds mass (bigger
fillet radius, thicker root), say so and justify it in stiffness-per-gram
terms. Grams alone are not the metric.

## Real-World Dimension Research

When designing objects that interface with real products (phones, chargers, PCBs, connectors, etc.), **use web search to find accurate dimensions** before writing any geometry code. Don't guess or use approximate values. Even 1-2mm off can make a part unusable.

**What to research:**
- Connector/port dimensions (USB-C: 8.4 x 2.6mm opening, Lightning, barrel jacks)
- Device dimensions (phone width/thickness, PCB footprints, charger puck diameters)
- Mounting hole patterns and screw sizes (M2.5, M3, etc.)
- Standard component specs (MagSafe puck: 56mm diameter, 5.6mm thick)
- Cable bend radii and strain relief requirements

**How to use it:**
1. Search for "[product] dimensions mm" or "[component] datasheet"
2. Cross-reference at least 2 sources when precision matters
3. Add the sourced dimensions as comments in the PARAMETERS section:
   ```python
   # MagSafe puck dimensions (source: Apple spec + iFixit teardown)
   puck_diameter = 56.0    # mm
   puck_thickness = 5.6    # mm
   ```
4. When in doubt, add 0.3-0.5mm clearance to external dimensions

This is especially important for: phone cases/stands, charger mounts, PCB enclosures, cable management, adapter fittings, and anything that clips onto or wraps around an existing product.

## Core Workflow

1. **Gather requirements** (see Requirements Gathering below)
2. **[If a mesh was provided] Measure, rebuild, verify** (see Importing above)
3. **Research dimensions** of any real-world products involved
4. **Phase 1, Base shape**: Build outer shell, preview, get user feedback
5. **Phase 2, Features**: Add functional details, preview, get user feedback
6. **Phase 3, Final delivery**: Fillets, structural cleanup, preview + STL + STEP + recommendations
7. **Offer parameter tweaks** after delivery

This is a **collaborative, show-as-you-go** process. Do NOT disappear and come back with a finished model. Show the user your progress at each phase and incorporate their feedback before moving on.

## Requirements Gathering

Before writing any code, walk through these topics with the user **conversationally**. Don't dump all questions at once. Ask the most important ones first, then follow up based on answers. Use reasonable defaults when the user doesn't specify.

**What is it?**
Object type, purpose, what it holds/protects/attaches to. Get a clear mental model of the object before anything else.

**What load does it carry?** ← ask this early; it drives every structural decision
Which direction is the force? Roughly how much (hand-tight, body weight, a dropped bag)? Static, repeated, or impact? Is stiffness (bending) or pure strength the concern? If the user doesn't know, state your assumption in the script header.

**Critical dimensions**
Must-fit measurements, like PCB size, phone width, screw spacing, diameter of the thing it wraps around, etc. These are non-negotiable and drive everything else.

**Mounting & attachment**
How does it connect to things? Screws (what size?), snap-fit, adhesive tape, magnets, freestanding on a desk? This affects wall thickness, boss placement, and overall structure.

**Weight target**
Does mass matter (moving part, handheld, shipping cost)? If yes, material removal is a priority and you should report grams.

**Printer & material**
What printer do they have? (Bambu, Prusa, Ender, etc.) Nozzle size? Material (PLA, PETG, TPU)? This sets tolerances, minimum features, and the anisotropy constraint. Defaults: 0.4mm nozzle, PLA, 0.2mm layer height.

**Functional needs**
Ventilation/airflow, water resistance, cable routing, access panels, visibility windows, stacking, weight limits. Ask only what's relevant to the object.

**Aesthetic preferences**
Rounded vs sharp edges, minimal vs industrial look, color considerations (affects visibility of layer lines). Ask briefly. Most users care more about function than form.

## Progressive Preview Workflow

Build the model in phases. At each phase, export an STL, render a preview, self-review it, then **show it to the user and ask for feedback** before proceeding. This catches problems early and keeps the user involved.

### Preview recipe (use at every phase)

**One-shot (run script + render + parse result as JSON):**
```bash
python3 run_cadquery_model.py model.py --preview --strict
```
This executes `model.py`, finds the STL it wrote, renders the multi-view preview, and emits a JSON result with `success`, `stdout`, `stderr`, `stl`, `preview`, and `watertight`. With `--strict`, a non-watertight mesh is a hard failure. Use this as the default loop: if `success` is false, read the `stderr` field to fix the CadQuery script, then re-run.

**Rendering only (when the STL already exists):**
```bash
python3 preview.py model.stl preview.png --views multi
```

Then view the preview image, self-review it against `design-review.md` (printability) and `structure-review.md` (strength/redundancy), and fix any issues you spot **before** showing it to the user.

---

### Phase 1: Base Shape

Build the basic outer form: overall dimensions, shell/walls, bottom plate. No cutouts, no fillets, no details yet.

1. Write the script with parameters and basic geometry
2. Export STL and render preview
3. Self-review: Does the shape and size look right? Is the bottom flat for printing?
4. **Show the preview to the user**: "Here's the basic shape. Does this look right before I add details?" Include key dimensions.
5. Wait for feedback. If the user wants changes, iterate here before moving on.

### Phase 2: Features

Add functional details: holes, cutouts, mounting bosses, cable slots, ventilation, snap-fits, internal structures.

1. Add features to the script
2. Export STL and render preview
3. Self-review: Are all features visible? Do booleans look clean? Are holes in the right positions?
4. **Show the preview to the user**: "I've added [list features]. Anything to change before I finalize?"
5. Wait for feedback. Iterate if needed.

### Phase 3: Final Delivery

Apply finishing touches: fillets, chamfers, structural cleanup. Do a full structural review.

1. Add fillets/chamfers (largest radius first, apply after shell)
2. Export final STL + STEP and render preview
3. **Full self-review**: `structure-review.md` checklist plus the printability checklist in `design-review.md`
4. Fix any issues found, re-export if needed
5. **Deliver to the user**: final STL + STEP + preview image + a before/after table (mass, min wall, corner radius, support area) + print recommendations

---

**Important:** Do NOT skip phases or combine them unless the model is very simple (e.g., a flat bracket with two holes). For anything with enclosed geometry, multiple features, or tight tolerances, follow all three phases.

### Print Recommendations (final delivery)

Include a short slicer recipe plus the rationale. Bambu Studio, PrusaSlicer and OrcaSlicer already set sensible defaults from their filament + process presets, so **do not restate every slicer option**. Only say what matters for *this* model: material, layer height, walls, infill, supports, orientation.

**Baseline recipe (0.4mm nozzle, typical FDM):**
> PLA, 0.2mm layer, 2 walls, 15% gyroid infill, no supports, orientation: flat side on bed.

**When to deviate from the baseline:**
- **Load-bearing brackets / hooks / hinges**: bump infill to 25-40%, 3-4 walls, consider PETG over PLA for toughness.
- **Thin decorative walls or vases**: 0 infill, vase mode or 1 wall.
- **Tall narrow parts**: add a brim for bed adhesion.
- **Flexible parts (gaskets, grips)**: TPU 95A, 0.2mm layer, slower speed, no supports.
- **Functional overhangs the geometry can't avoid**: tree supports, or call them out so the user knows.
- **Outdoor / hot environments**: PETG or ASA, not PLA.
- **Food / skin contact**: call out that FDM parts are not food-safe and recommend a food-safe coating.

**Format at delivery time:**
```
Print settings: PLA, 0.2mm layer, 2 walls, 15% gyroid infill, no supports.
Orientation: place flat back side on the bed (front face up).
Why: the case has no overhangs above 45°, and 15% infill is plenty for a
TPU-adjacent protective shell.
```

Keep it to ~3 lines. Never dump every slicer setting; the slicer already knows.

## Script Template

ALWAYS structure scripts like this:

```python
import cadquery as cq

# ============================================================
# PARAMETERS - Edit these to customize the model
# ============================================================
# Assumed load: (record it - drives every structural choice below)
# ------------------------------------------------------------
# Overall dimensions
width = 60.0        # mm - outer width
depth = 40.0        # mm - outer depth
height = 25.0       # mm - outer height

# Wall and structural
wall = 2.0          # mm - wall thickness (min 1.2 for FDM)
corner_r = 2.0      # mm - inner corner fillet radius (>= 0.5 * wall)
rib_t = 1.2         # mm - rib thickness (0.5-0.6 * wall)
rib_h = 3.6         # mm - rib height (<= 3 * rib_t)

# Tolerances
fit_clearance = 0.3 # mm - clearance for press-fit (adjust per printer)

# ============================================================
# MODEL
# ============================================================
result = (
    cq.Workplane("XY")
    .box(width, depth, height, centered=(True, True, False))
    # ... build geometry using parameters above (bottom at Z=0)
)

# ============================================================
# EXPORT
# ============================================================
# Use tolerance=0.01, angularTolerance=0.1 for consistent tessellation
# across models. Defaults give coarser, wildly variable STL sizes.
cq.exporters.export(result, "output.stl",
                    tolerance=0.01, angularTolerance=0.1)
cq.exporters.export(result, "output.step")   # for CAD hand-off
print(f"Exported: {width}x{depth}x{height}mm")
```

## Key Rules

### Parameters First
- ALL dimensions go in the PARAMETERS section at the top
- Use descriptive names: `screw_hole_d`, not `d1`
- Add units in comments (always mm)
- Group related parameters with blank lines and section comments
- Record the assumed load case in a comment — the structure follows from it

### Structural Defaults

The numbers that decide whether the part survives:

| Feature | Minimum | Recommended |
|---------|---------|-------------|
| Inner corner fillet | 0.5 x wall | ~1.0 x wall |
| Wall thickness (cosmetic) | 1.2mm | 2.0mm |
| Wall thickness (load-bearing) | 2.0mm | 2.4-3.0mm |
| Rib thickness | 0.4 x wall | 0.5-0.6 x wall |
| Rib height | - | <= 3 x rib thickness |
| Rib root fillet | 0.25 x rib t | 0.5 x rib t |
| Hole edge distance | 1.0 x hole dia | 1.5-2.0 x dia |
| Hole-to-hole spacing | 2.0 x dia | 2.5 x dia |
| Pocket corner radius | 1.0mm | >= 2.0mm |
| Pocket to edge / hole | 1 x wall | 2 x wall |
| Section change blend | 1 x step | >= 3 x step |

### Manufacturing Constraints

Not goals — limits you must not cross while optimizing for structure:

| Property | Minimum | Recommended |
|----------|---------|-------------|
| Wall thickness | 1.2mm | 2.0mm |
| Layer height | 0.08mm | 0.2mm |
| Hole clearance | 0.2mm | 0.3mm |
| Press-fit interference | 0.1mm | 0.15mm |
| Min feature size | 0.4mm (nozzle) | 0.8mm |
| Fillet radius (bottom edge) | 0.5mm | 1.0mm |
| Bridge span | - | < 20mm unsupported |
| Overhang angle | - | < 45 degrees from vertical |

**Material-specific adjustments:** TPU needs larger clearances (~0.5mm) due to flex. PETG is stickier, so add +0.1mm to fit clearances. ABS shrinks ~0.5-0.7%, so scale critical dimensions up slightly. When in doubt, print a small test piece first.

**Anisotropy:** interlayer (Z) strength is ~50-70% of in-layer. Choose the build direction so the main load runs in-plane; never let the design load peel layers apart. If print orientation and structural orientation conflict, say so and let the user decide.

### Orientation Awareness
- Design with print orientation in mind
- Flat bottom surfaces print best
- Avoid supports when possible by designing around overhangs
- Add chamfers to bottom edges instead of fillets (fillets need supports)
- Comment the intended print orientation in the script

### CadQuery Patterns

Common patterns to know:

**Hollow enclosure (boolean subtraction, preferred):**
```python
outer = (
    cq.Workplane("XY")
    .box(width, depth, height, centered=(True, True, False))
    .edges("|Z").fillet(corner_r)
)
inner = (
    cq.Workplane("XY")
    .workplane(offset=floor_t)
    .box(width - 2*wall, depth - 2*wall, height, centered=(True, True, False))
    .edges("|Z").fillet(max(0.1, corner_r - wall))
)
result = outer.cut(inner)
```

**Screw boss:**
```python
.pushPoints([(x, y)])
.circle(boss_od / 2).extrude(boss_h)
.pushPoints([(x, y)])
.hole(screw_d + fit_clearance)
```

**Snap-fit clip:**
```python
# Cantilever beam with overhang hook
.workplane(offset=wall)
.moveTo(x, y).rect(clip_w, clip_l).extrude(clip_h)
# Add hook at tip with a small overhang (< 45 deg)
```

**Ventilation grid:**
```python
.pushPoints(vent_positions)
.slot2D(slot_l, slot_w).cutThruAll()
```

**Corner gusset (the cheapest stiffness you can buy):**
```python
# Triangular web in the inner corner of two plates - far more effective
# per gram than thickening either plate
gusset = (
    cq.Workplane("XZ")
    .moveTo(0, 0).lineTo(0, g_h).lineTo(g_w, 0).close()
    .extrude(g_t)
)
result = result.union(gusset)
```

**Lightening pocket (spherical, print-friendly ceiling):**
```python
# A raw sphere pocket leaves an unsupported ceiling; intersect the upper
# hemisphere with a 45-degree cone so every wall stays self-supporting
import numpy as np
cone_len = pocket_r / np.sin(np.radians(45.0)) + 2
cone = cq.Solid.makeCone(0, cone_len, cone_len,
                         cq.Vector(cx, cy, cz), cq.Vector(0, 0, 1))
pocket = cq.Workplane("XY").sphere(pocket_r).translate((cx, cy, cz)).cut(cq.Workplane(obj=cone))
result = result.cut(pocket)
```

Other patterns: mounting brackets, cable routing channels, text/labels (`.text()`), multi-part assemblies with alignment pins.

### Common Pitfalls

- **Hollowing: prefer boolean subtraction over `.shell()`**. `.shell()` is fragile. It fails on tapered bodies, lofted shapes, unions of multiple primitives, and anything with many fillets. The reliable pattern is:
  ```python
  outer = cq.Workplane("XY").box(w, d, h, centered=(True, True, False)).edges("|Z").fillet(corner_r)
  inner = (
      cq.Workplane("XY")
      .workplane(offset=floor_t)
      .box(w - 2*wall, d - 2*wall, h, centered=(True, True, False))
      .edges("|Z").fillet(max(0.1, corner_r - wall))
  )
  result = outer.cut(inner)
  ```
  Only reach for `.shell()` when the body is a single simple primitive (one `.box()` or `.cylinder()`) with a uniform wall thickness on all sides. If in doubt, use boolean subtraction.
- **Build order: fillet → cut, not cut → fillet**. Apply fillets while the geometry is still a clean primitive. Once you have cut holes/slots/pockets into a body, filleting the resulting edges often fails or produces bad geometry. Same rule for chamfers.
- **Fillet failures**: Apply fillets from largest to smallest radius. **Do not wrap fillets in `try/except` to silently shrink the radius.** A fillet failure means the geometry or the radius is wrong; find the root cause (too-large radius, wall thinner than radius, adjacent faces that the fillet would degenerate) and fix that.
- **Sphere/cylinder booleans leave degenerate seam edges.** OCC's parametric sphere tessellates with a seam; cutting it can produce zero-length edges, so the STL is not watertight even though the solid is fine. Rebuild through `manifold3d` after export:
  ```python
  import numpy as np, trimesh, manifold3d as m3d
  raw = trimesh.load("model_raw.stl", force="mesh")
  man = m3d.Manifold(m3d.Mesh(
      vert_properties=np.array(raw.vertices, dtype=np.float32),
      tri_verts=np.array(raw.faces, dtype=np.uint32)))
  gl = man.to_mesh()
  fixed = trimesh.Trimesh(vertices=np.array(gl.vert_properties)[:, :3],
                          faces=np.array(gl.tri_verts))
  fixed.export("model.stl")
  assert fixed.is_watertight
  ```
- **Cavity ceilings vs floors.** With Z up, the *upper* half of an internal cavity is a ceiling (material stacked above it) and needs support; the lower half prints as a natural floor. Fill ceilings with a 45-degree cone opening *upward* from the cavity center. Filling the wrong half makes overhangs worse, not better.
- **Zero-thickness geometry**: Ensure boolean operations don't create infinitely thin walls. Add a small epsilon (0.01mm) when cutting bodies that are meant to pass just through a surface.
- **Coordinate system**: CadQuery centers geometry at origin by default. Use `centered=(True, True, False)` on `.box()` to place the bottom at Z=0 so `.faces("<Z")` is always the print bed.
- **Hole direction**: `.hole()` cuts through the entire part by default. Use `.cboreHole()` or `.cskHole()` for counterbore/countersink.
- **Taper direction**: In `.extrude(taper=angle)`, a **positive** taper angle narrows the shape (draft inward), **negative** flares it outward. This is opposite to what many people expect.
- **Loft is fragile**: `.loft()` fails on many cross-section combinations. Prefer `.extrude(taper=angle)` when transitioning between a shape and a scaled version of itself. Only use `.loft()` when you need to transition between genuinely different profiles (e.g., circle to rectangle).
- **Export errors / non-watertight STL**: If export fails or the preview reports a non-watertight mesh, the geometry is invalid (usually self-intersecting booleans or zero-thickness faces). Fix the cause, don't paper over it. Run `python3 preview.py model.stl --strict` to fail loudly on non-watertight output.

## Export

```python
# STL (for slicing) - always set tolerance + angularTolerance for
# consistent tessellation. Defaults produce variable file sizes and
# over-tessellated glyphs on text features.
cq.exporters.export(result, "model.stl",
                    tolerance=0.01, angularTolerance=0.1)

# STEP (for further CAD editing)
cq.exporters.export(result, "model.step")
```

Export STL for printing. **Also export STEP whenever the part might be
edited later** — for optimization jobs it should be the default, since the
user will likely want to iterate in Fusion 360 or similar.

## Multi-Part Models

For models with multiple parts (e.g., enclosure + lid):

```python
# Export each part separately
cq.exporters.export(body, "enclosure_body.stl")
cq.exporters.export(lid, "enclosure_lid.stl")
```

Name files descriptively so the user knows which part is which.

## Gridfinity Bins

For any Gridfinity request (bins, inserts, cradles for the 42mm modular grid system), **use the bundled `gridfinity.py` module; never hand-roll the base profile**. The module is tested, spec-compatible (base profile, stacking lip, 6x2mm magnet bores, M3 screw holes on the 26mm square), and validates parameters with actionable errors.

**Workflow:**
1. Copy `gridfinity.py` from this skill's directory next to the model script (it is a vendored module; generated scripts must stay runnable standalone).
2. Build with the `GridfinityBin` API:

```python
import cadquery as cq
from gridfinity import GridfinityBin

bin = GridfinityBin(grid_x=3, grid_y=2, height_units=3,
                    stacking_lip=True, magnets=False)
bin.add_pocket(length=88.6, width=72.6, corner_r=(12.0, 1.0))  # cradle
# or: bin.add_compartments(cols=3, rows=2, scoop_r=6, label_tab=True)
# or: bin.add_polygon_pocket(points, depth=15, clearance=0.3)
# or: bin.add_cylinder_pocket(diameter=22, center=(42, 27),  # finger well; overlapping pockets merge
#         tilt=35, tilt_dir=(-1, 0), round_bottom=True)      # smooth angled thumb scoop
# or: bin.add_finger_notch(side="+Y", width=20, offset=40)   # offset slides it along the wall
result = bin.build()
cq.exporters.export(result, "my_bin.stl", tolerance=0.01, angularTolerance=0.1)
print(bin.summary())
```

**Cheat sheet:**
- Spec constants: grid pitch 42mm, height unit 7mm. Every derived number (footprint, lip height, floor Z, usable cavity depth) is exposed on the builder: `bin.outer_w`, `bin.outer_d`, `bin.total_h`, `bin.floor_z`, `bin.max_depth`, and `bin.summary()` prints them all. Read those instead of recomputing.
- Sizing an object pocket: add 0.3mm per side fit clearance, plus ~1mm per side if the object must drop in and out easily.
- Pocket matching a real object's shape: `outline_from_scan.py` extracts the max cross-section outline from an STL scan of the object (align, scale to spec dims, slice, union), ready for `add_polygon_pocket`.
- Height choice: whatever holds the contents; the contents may protrude above the rim (see `examples/gridfinity_d110_bin.py`).
- `magnets=True` needs a magnetic baseplate; skip it for drawer bins.

The builder raises `GridfinityError` with a specific message when a pocket is too big, a wall too thin, or the floor too shallow for screws (a few checks fire in the `add_*` calls, the rest in `build()`). Fix the parameters; do not bypass the validation.

If the user asks for something the module does not cover (baseplates, odd lips), fall back to custom geometry but keep the spec constants from `gridfinity.py` as the source of truth. When delivering a release, ship `gridfinity.py` alongside the model script.

## Files in this skill

| File | Purpose |
|---|---|
| `SKILL.md` | This file: workflow, rules, patterns |
| `structure-review.md` | **Structural** checklist: load path, stress concentrators, rib/pocket rules, anisotropy, before/after reporting |
| `design-review.md` | **Printability** checklist: visual inspection, dimensional verification, overhang/bridge analysis |
| `analyze_stl.py` | Reverse-engineer a mesh: envelope, mass, wall thickness per axis, hole/pocket detection, overhang audit, ASCII cross-sections, A/B compare |
| `preview.py` | Headless STL to 6-view PNG renderer (`--strict` fails on non-watertight meshes) |
| `run_cadquery_model.py` | Runs a CadQuery script, captures errors, optionally renders, emits JSON so you can self-correct |
| `gridfinity.py` | Gridfinity bin generator |
| `outline_from_scan.py` | Extract a pocket outline from a 3D scan |
| `stl_to_3mf.py` | STL to 3MF converter for Bambu Studio / PrusaSlicer |
| `mesh_io.py` | STL loading with validation |

## Parameter Adjustment Offer

After delivering the final model, **always present the key parameters as a summary table** and offer to tweak them. This lets the user fine-tune without re-explaining the whole design.

Example:
```
Here's your final model! Current parameters:

| Parameter       | Value  |
|----------------|--------|
| Width          | 90 mm  |
| Depth          | 65 mm  |
| Height         | 30 mm  |
| Wall thickness | 4 mm   |
| Corner radius  | 3 mm   |
| Rib thickness  | 1.2 mm |
| Cable slot     | 18 mm  |
| Fit clearance  | 0.3 mm |

Want me to adjust anything? Just say e.g. "make it 5mm taller" or "wider cable slot."
```

Only include parameters the user would plausibly want to change. Skip internal constants like `eps` or `nozzle_d`. Group them logically (dimensions first, then structural, then tolerances).

## Output Checklist

Before delivering a model, verify:
- [ ] All dimensions are parameterized (no magic numbers in geometry code)
- [ ] Assumed load case recorded in a comment
- [ ] Every inner corner has a fillet; no sharp re-entrant corners
- [ ] Wall thickness >= 1.2mm (>= 2.0mm where it carries load)
- [ ] Holes keep >= 1.5x diameter edge distance
- [ ] No pocket crosses the load path; pockets keep >= 2x wall to edges
- [ ] Manufacturable: within the overhang/bridge limits, or supports called out
- [ ] Print orientation noted in comments, load path running in-plane where possible
- [ ] STL exported, watertight, reasonable file size (not 0 bytes)
- [ ] STEP exported
- [ ] Clear parameter names with units
- [ ] Script runs without errors
- [ ] **Multi-view preview generated and visually inspected**
- [ ] **Preview shows correct shape, features, and proportions**
- [ ] **Bounding box dimensions match requirements**
- [ ] **Before/after table with mass, min wall, corner radius, support area**

For an imported mesh, additionally:
- [ ] `analyze_stl.py` run on the original, findings reported to the user
- [ ] Rebuild script reproduces the original (volume within ~2%)
- [ ] Both rebuild and optimized scripts delivered, not just the STL
