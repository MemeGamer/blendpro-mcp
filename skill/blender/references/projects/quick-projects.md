# Quick projects (1–2 minute short-form tutorials)

These are compact recipes for eight small props, taken from short-form Blender tutorials. The notes didn't record the authors. Every step was checked against Blender 5.2. Each section ends with how an agent does it through BlendPro.

Legacy **hair particles** (fur, pollen, scattered leaves) still work in Blender 5.2. The modern alternative is a Curves hair object with the Geometry Nodes hair assets, or `stylized_fur_cards` for real-time and XR.

---

## Flower
1. **Petal:**
   1. Add a petal photo as a reference image.
   2. Trace its outline from a Plane by extruding edges.
   3. Add Subdivision Surface and Solidify modifiers.
2. **Petal texture:** in top view, `U → Project from View`, then fit the UVs over the photo in the UV Editor and use the photo as Base Color.
3. **Curve it:** use Proportional Editing (`O`) for a natural cup shape.
4. **Head:** add a small sphere at the centre and duplicate petals in a ring around it. Tweak each one with proportional edit so no two match.
5. **Pistil:** use a Bezier curve with Geometry → Depth for the stalk and a small sphere at the tip. Convert the curve to a mesh and join it with the sphere.
6. **Pollen:**
   1. Set the origin to the bottom of the pistil.
   2. Separate the top half of the centre sphere into its own object.
   3. Add Particles → **Hair** with Advanced on, Length 5 and Number 300.
   4. Under Render, choose **Object** and pick the pollen mesh.
   5. Under Rotation, set **Phase −0.1** and **Randomize 0.35**.
7. **Stalk and leaf:** a Bezier curve with Depth for the stalk; a traced plane with the photo mapped as colour for the leaf, the same way as the petals.

**BlendPro:**
- Arrange the petals with `radial_array`.
- Build the stalk with `pipe_along_points`.
- Do the pollen in `run_python`, setting these on `ParticleSettings`: `type='HAIR'`, `use_advanced_hair`, `hair_length`, `count`, `render_type='OBJECT'`, `instance_object`, `phase_factor`, `phase_factor_random`.

## Flower pot
1. **Pot:** add a Cylinder and scale the top and bottom loops into a taper. Or build it in one call with `lathe_profile`.
2. **Rim and soil:**
   1. Add a loop cut under the rim, then `Alt E → Extrude Faces Along Normals` for the lip.
   2. Extrude the top face down into the pot, scale it inward, and delete it.
   3. On the remaining loop, use `Face → Grid Fill`.
   4. Bulge a few faces with proportional edit for uneven soil.
3. **Branch:** a Bezier curve. Extrude the end point along a winding path, raise Depth, and convert to a mesh. Taper the end loop to a point with proportional edit.
4. **Leaf and stem:** trace a leaf plane over a reference image and bend it with proportional edit. Add a thin cylinder stem and join them (`Ctrl J`).
5. **Leaf origin:** select the stem's bottom face, use `Shift S → Cursor to Selected`, then `Set Origin → Origin to 3D Cursor`. This makes the scattered leaves attach at their stems. Project the UVs from view and map the photo.
6. **Leaves on the branch:**
   1. Add Particles → Hair (Advanced). Under Render, choose **Object**: the leaf.
   2. Turn on Rotation with Axis **Normal-Tangent**, Randomize 1, Phase 1 and Phase Randomize 2.
   3. Randomise the scale.
7. **Finish:** duplicate branch variants. Add terracotta, soil and brown wood materials.

**BlendPro:**
- Make the pot with `lathe_profile`.
- Scatter leaves with `scatter_on_surface` (Geometry Nodes, exportable via `realize_and_apply_gn`), or with hair particles where `rotation_mode='NOR_TAN'`.

## Fur and hair (particle hair with dynamics)
1. **Particles:** add Particles → **Hair**. Set the count and length.
2. **Dynamics:** turn on **Advanced** and **Hair Dynamics**.
   - **Structure:** mass and stiffness.
   - **Volume:** Air Drag, which controls how floaty the hair is.
3. **Physics:** a little **Brownian** adds instant frizz.
4. **Children:** set to **Interpolated** for density. **Clumping** controls how strands gather at the tips versus the roots.
5. **Bake** the dynamics cache for smooth playback.
6. **Material:** use **Principled Hair BSDF**, and set the colour by melanin or by direct colour.

**BlendPro:**
- For a stylized or real-time look, use `stylized_fur_cards`. See `geometry-nodes/stylized-fur-feathers.md`.
- For particle hair in `run_python`:
  - Particle settings: `brownian_factor`, `child_type='INTERPOLATED'`, `clump_factor`, `roughness_1`.
  - On the particle system: `particle_system.use_hair_dynamics = True`. Its cloth settings live on `particle_system.cloth`.

## Gift box
1. **Box:** on a Cube, add a loop cut near the top for the lid. Select the lid's side faces and use `Alt E → Extrude Faces Along Normals` so the lid overhangs.
2. **Ribbon band:** add a loop cut pair across each axis. Select the cross-shaped strip and extrude it slightly outward.
3. **Bow loop** (a **Bezier Circle**, not a mesh circle, because Depth and Extrude are curve settings):
   1. Squash it into an oval in Z.
   2. Pull the right point in to meet the lower point, and flatten the lower point with `S Z 0`.
   3. Move and rotate the top point to round the loop.
   4. Set the origin at the pinch point.
   5. In the curve's Geometry settings, set **Depth 0.02** and **Extrude 3**, scaled to your box.
4. **Bow:** duplicate the loop linked (`Alt D`) and rotate it 90°. Press **`Shift R`** (Repeat Last) to keep going around the centre.
5. **Finish:** turn the bow 45° in Z so it sits diagonally, and add a small sphere as the knot.

**BlendPro:**
- Build the box with `rounded_box`.
- The bow is a `run_python` curve with `bevel_depth` and `extrude`; repeat the loops with `radial_array`.

## Puffy office chair (Cloth Filter inflate)
1. **Base:** on a Cube, delete the front and top faces to leave an L-shape. Subdivide once. Lower the front for the seat slope and tilt the back.
2. **Thickness:** add Subdivision (`Ctrl 2`) and Solidify (thickness about 0.16), then **apply** both.
3. **Seams:** add a loop around the outer rim and scale it out slightly. Bevel the edges that split the cushion panels, adding 2 segments inside the bevel.
4. With those panel faces still selected, add another Subdivision in Object Mode and apply it.
5. **Inflate:**
   1. In Sculpt Mode, use `Face Sets → Face Set from Edit Mode Selection`.
   2. Choose the **Cloth Filter** tool, set Filter Type **Inflate**, and turn on **Use Face Sets**.
   3. Drag gently. The panels puff up like stuffed upholstery.
6. **Material:** add leather or fabric.

**BlendPro:** use `run_operators` for the sculpt steps. They need Sculpt Mode context:
- `sculpt.face_sets_create(mode='SELECTION')`
- `sculpt.cloth_filter(type='INFLATE', use_face_sets=True)`

For a fully procedural inflate without sculpting, see `cloth-sofa.md` (cloth pressure).

## Roof tiles (double Array)
1. **Profile:** add a Circle and rotate it 90° in front view. Delete the left half to leave a semicircle.
2. Extrude the bottom-right vertex straight out in X. Apply rotation and scale (`Ctrl A`).
3. **Length:** in top view, extrude everything along Y and scale the far end down slightly so the tile tapers.
4. Flatten the two right-most vertices with `S X 0`.
5. **Slope:** in side view, lower the far-end vertices a little.
6. **Thickness:** Solidify at 0.08 with **Even Thickness** on.
7. **Array 1:** a relative X offset so the tiles touch side by side.
8. **Array 2:** X set to 0, a Y offset so each row overlaps the one below, and a small **Z** step so the rows stack up. Raise both counts to cover the roof.

**BlendPro:** one `run_python` call (the profile with bmesh, then Solidify plus two Arrays). For export, follow it with `realize_and_apply_gn` or apply the modifiers, then `xr_budget_report`.

## Shiny vase (Spin)
1. **Profile:**
   1. Add a vase reference image.
   2. Merge any mesh down to a single vertex (`M → At Center`) and place it at the bottom centre of the vase.
   3. Extrude upward along the **right outer edge only**.
2. **Pivot:** select the bottom centre vertex, use `Shift S → Cursor to Selected`, then set the origin to the 3D cursor.
3. **Revolve:** select all, use the **Spin** tool at 360°, and Merge by Distance at the seam.
4. **Cleanup:** delete the top cap to open the vase. Fill the base if needed.
5. **Smooth it:** add Solidify and Subdivision Surface.
6. **UVs:** mark one vertical seam down the back.
7. **Texture:** with **Node Wrangler** (still bundled), select the Principled node and press `Ctrl Shift T` to load a set of PBR maps. Use **Cylinder Projection** from the front view and scale the UVs to fit.

**BlendPro:**
- Model the vase with `lathe_profile`, which is a profile plus Spin in one call. With the reference pipeline, use `reference_measure` on a side photo and its `profile` rows as the (radius, height) pairs.
- Load textures with `pbr_material_from_textures`. It also sets colour spaces correctly.
- UV with `seams_by_angle_unwrap` or `smart_uv_unwrap`.

## Tennis ball
1. **Seam curve:**
   1. Add a UV Sphere with a ring count divisible by 4, so the halves line up after rotating.
   2. Add Subdivision (`Ctrl 1`).
   3. In Edit Mode, separate exactly half (`P`) and rotate that half 90°.
   4. Join the halves (`Ctrl J`) and use `M → By Distance`. The weld follows the classic S-shaped seam.
2. **Groove:** select the seam loop and bevel it (`Ctrl B`). Extrude the new faces inward and scale them down slightly to make a trench.
3. **Materials:** white rubber in the groove, yellow-green felt on the rest.
4. **Fuzz:**
   1. Add Particles → Hair (Advanced) with Length **0.01** and a very high count.
   2. Set **Brownian 0.005**.
   3. Set Children to **Interpolated**, with **Roughness Random 0.005**.
5. **Less fuzz on the seam:** move the groove faces to their own object with a lower count. If the hair takes the wrong colour, the particle material comes from the slot order, so move the felt material to slot 1.

**BlendPro:** use `run_python` for the split, rotate, join and merge, then `mesh_analysis` to confirm it's manifold with no duplicate vertices left.

---

## Index
| Prop | Core technique |
|---|---|
| Flower | traced petal plane + project-from-view UVs, hair-particle pollen |
| Flower pot | grid-filled soil, curve branches, particle leaves with Normal-Tangent rotation |
| Fur / hair | hair dynamics, Brownian frizz, interpolated children, Principled Hair BSDF |
| Gift box | Alt E rim, Bezier-circle bow with Depth/Extrude, `Shift R` repeat |
| Office chair | Face Sets from selection + Cloth Filter Inflate |
| Roof tiles | Solidify + two Array modifiers (X row, YZ stacked rows) |
| Vase | single-vertex profile + Spin 360°, Node Wrangler PBR, cylinder UVs |
| Tennis ball | half-sphere rotate-and-weld seam, beveled groove, particle fuzz |
