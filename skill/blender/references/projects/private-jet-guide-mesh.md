# Hard-surface vehicle with clean panels (Private Jet, guide-mesh technique)

Notes from Blender Guru's private jet tutorial (Andrew Price). The key idea is cutting panels on a **detail mesh shrinkwrapped to a smooth base**, so the SubD curvature never pinches.

## 1. Blueprints at real scale
- Add the views with `Shift A → Image → Reference`.
- Size a cube to the real dimensions. You can type units straight into the field, for example `30.7ft`. Then scale the blueprints to fit the cube.
- On each image empty, set Depth to **Back** and turn off **Show in Perspective**, so the blueprints only show in ortho views.

## 2. Base hull
1. Start from a 16-vertex Cylinder rotated 90° to lie along the fuselage axis.
2. Add loop cuts (`Ctrl R`) and scale them to follow the nose and tail taper.
3. Delete half the mesh. Add a Mirror modifier with **Clipping** on, and keep Mirror **above** Subdivision Surface.
4. Extrude the wings and the V-tail from the side faces. Flatten them with `S Z 0`.

## 3. Guide mesh / Shrinkwrap (the important part)
Cutting doors and windows straight into a SubD mesh breaks the curvature and causes pinching. Instead:
1. Duplicate the body. Name the original **Base** and the copy **Details**.
2. Give **Details** this stack: **Subdivision Surface → Shrinkwrap (target: Base) → Solidify → Bevel**.
3. Make every cut on Details. Shrinkwrap snaps it back onto Base's smooth surface, however many cuts you add.

## 4. Panels and windows
- Draw door and window shapes on Details with the Knife (`K`).
- Select the panel's faces and press **`Y` (Split)**. The panel becomes its own island in the same object. Solidify gives it thickness and Bevel rounds its edges.
- **Glass:** inset with `I`, split with `Y`, and assign glass. In Blender 4.2+ (and 5.2), enable **Thin Wall** on the Principled BSDF so thin glass doesn't render black. See `materials/thin-wall-transparency.md`.

## 5. UVs and livery
- Mark seams around the wings, tail and engines (`Ctrl E → Mark Seam`), then unwrap.
- Export the UV layout (`UV → Export UV Layout`) and paint the livery in an image editor.
- **Bevel control:** set Bevel Weight to 1.0 on the edges you want rounded, and set the Bevel modifier's Limit Method to **Weight**.

## 6. Lighting, camera and compositing
- **Lighting:** an aerial HDRI, or a physical Sky Texture.
- **Camera:** parent the camera to an Empty at the plane, with a **Track To** constraint so it keeps aiming at the plane. Rotate the Empty for orbit shots.
- **Compositor:**
  - a Glare node set to **Bloom** or **Streaks** for chrome glints (in 5.x, Type is a menu socket on the node)
  - Lens Distortion at about 0.015
  - Color Balance with cool shadows and warm highlights

## Driving it with BlendPro
| Step | Recipe / tool |
|---|---|
| Blueprints | `reference_image_setup` (front, left, top) at real size. `reference_compare` checks the hull silhouette |
| Half hull | `mirror_setup` |
| Details stack | `run_python` (duplicate, Shrinkwrap → Solidify → Bevel) or `solidify_shell` |
| Glass | `glass_material` with `thin_wall=true` for single-surface panes |
| Seams and unwrap | `seams_by_angle_unwrap`, then `texel_density` |
| Weighted bevels | `bevel_weight_by_angle` |
| HDRI / sky | `hdri_world`, `sky_world`, `sun_time_of_day` |
| Camera rig | `look_at_constraint`, `turntable_setup`, `camera_dof_focus` |
| Glare, lens distortion, grading | `compositor_effects` |
