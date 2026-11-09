# One-minute puffy sofa (cloth pressure)

Notes from BlenderVitals' 1-minute sofa: inflate one cushion with a cloth simulation, then duplicate it into a whole couch.

## 1. Inflate a cushion
1. Add a Cube. Scale it flat in Z and stretch it into a cushion shape.
2. Add loop cuts (`Ctrl R`): about 10 along the length, 10 across the width and 1 around the height. Then Subdivide once.
3. Go to `Physics → Cloth`.
   - Enable **Pressure** and set it to about **2.0**.
   - Set **Field Weights → Gravity** to **0**.
4. Play the simulation and stop early, while the cushion looks plump.

## 2. Assemble
1. **Apply** the Cloth modifier at that frame to freeze the shape, then add Subdivision Surface.
2. Duplicate the cushion (`Shift D`):
   - three for the seats
   - one stretched in Edit Mode for the backrest
   - two rotated 90° for the arms
   - a couple leaning on the back
3. Select everything and apply scale (`Ctrl A → Scale`) before sculpting or simulating again.

## 3. Wrinkles
In Sculpt Mode, the **Cloth** brush makes tension folds and soft wrinkles where you drag.

## 4. Legs and material
1. Model one leg: a small stretched cube with a Bevel modifier. Duplicate it to the four corners.
2. Add a fabric or leather PBR material.

## Driving it with BlendPro
| Step | Recipe / tool |
|---|---|
| Cushion simulation | `run_python`: add the Cloth modifier, set `settings.use_pressure = True`, `settings.uniform_pressure_force = 2.0` and `settings.effector_weights.gravity = 0`. Then step frames with `scene.frame_set(i)` and apply the modifier at the chosen frame. That's headless-safe, with no Play button needed |
| Legs | `rounded_box` |
| Fabric / leather | `pbr_material_from_textures` with a texture folder, or `procedural_material` |
| Check the cushions don't overlap | `spatial_report` (resting contacts and real intersections, as text) |
