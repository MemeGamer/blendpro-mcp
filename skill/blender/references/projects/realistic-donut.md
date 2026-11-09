# Realistic donut (sculpted dough, liquid-sim icing, SSS)

Notes from Bro 3D's realistic donut tutorial. This goes past the beginner donut with sculpted dough, a fluid-simulated icing drip, real-scale subsurface scattering and painted sprinkles.

## 1. Dough
1. Add a Torus and scale it down a little in Z. Real donuts aren't round tubes.
2. In Edit Mode, `Alt Click` the inner edge loop and scale it down to flatten the hole wall.
3. Add a **Multiresolution** modifier and subdivide twice.
4. In Sculpt Mode, use **Clay Strips** at low strength with quick random strokes. Turn on Cavity in the viewport shading popover to see the result.

## 2. Liquid-simulated icing
1. In Edit Mode, select the top half of the faces, duplicate them (`Shift D`) and separate them (`P`).
2. Give the icing a Solidify modifier and apply it.
3. `F3 → Quick Liquid`. Set the domain resolution to about 64–100.
4. Make the donut a **Fluid Effector** (Collision).
5. Play the simulation. Stop around frame 8, when the drips have just started.
6. Apply the liquid result, then use `Object Data → Remesh → Voxel` with a size of about **0.01 at the model's scale**. Smooth it with the Smooth brush.

The simulation is the slow, heavy part. For a quick or agent-driven build, BlendPro's reference donut uses a cheaper procedural route instead:
1. Copy the top half of the dough.
2. Shrinkwrap (Outside Surface) → Solidify (offset −1) → SubD.
3. Pull 4–7 drips down with proportional editing.

See `modeling/reference-pipeline.md` and `examples/donut/`.

## 3. Materials
- **Real scale matters for SSS:** about 2.5 cm tall and 8–10 cm across.
- **Icing:** Principled BSDF with Roughness about 0.2 and Subsurface Weight on. Set Subsurface Scale to about **0.02 m** and Radius to about `1.0, 0.3, 0.3`, for a soft, slightly cool scatter.
- **Dough colour:**
  1. Run Gradient Texture (Generated coordinates, rotated 90° in Y) → Color Ramp → Base Color, so a pale band sits between the browned top and bottom.
  2. A normal-based ramp works too, using |Normal Z|.
- **Fried lumps:** feed a Noise texture through a crunched ramp into a Displacement node. Set the material's **Displacement** setting (Settings → Surface) to Displacement Only, or Displacement and Bump.
- **Sugar sheen:** set Coat Weight to about 0.4, with Noise driving Coat Roughness for a patchy look.

## 4. Sprinkles (Geometry Nodes)
1. Make 3–4 sprinkle variants in a collection.
2. Weight-paint where they should go on the icing.
3. Build the node tree: Distribute Points on Faces (density from the painted vertex group, boosted with a Multiply node) → Instance on Points, using Collection Info with **Separate Children**, **Reset Children** and **Pick Instance** → Join Geometry.
4. Randomise rotation with Random Value. Rotate Rotation is the modern node; Rotate Euler still works.
5. Raise each sprinkle mesh slightly above its origin so it doesn't sink into the icing. The opposite trap: if a variant's mesh sits far from its origin, the instances fly off the surface. **Centre each mesh on its origin first.**

## Driving it with BlendPro
| Step | Recipe / tool |
|---|---|
| Proportions and colours from photos | `reference_measure`, `reference_details` (sprinkle count, length range, colours), `reference_image_setup` |
| Organic surface | Displace (Clouds) on a SubD torus, or `remesh_voxel` before sculpting |
| Sprinkles | `scatter_with_min_distance` with `vertex_group` (the weight map) and a collection of variants |
| Score against the reference | `reference_compare` |
| Hero render | `three_point_lighting`, `set_render_preset`, `render_still` |
