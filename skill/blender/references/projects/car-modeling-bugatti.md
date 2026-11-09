# Car modeling from blueprints (Bugatti workflow)

Notes from JOYCE 3D's "Bugatti Car Modeling" tutorial. The workflow goes from a blueprint-traced edge outline to clean quad panels with weighted bevels. Hotkeys are Blender 4.2–5.2.

## 1. Setup
- **Blueprints:** use front, side, top and rear views, added with `Shift A → Image → Reference`. Scale all of them to one cube sized to the car's real length, width and height.
  - On each image empty, set **Side: Front** so it doesn't block the view when you orbit. Turning off **Show in Perspective** keeps it to the ortho views.
- **Add-ons:** LoopTools and F2 are **extensions** in 4.2+, not built-in add-ons. Get them from `Edit → Preferences → Get Extensions`. Both are free.

## 2. Trace the body as an edge outline
1. Add a Plane and merge it to a single vertex with `M → At Center`.
2. In the side view, extrude the vertex (`E`) along the body's silhouette and character lines.
3. To get evenly spaced points on a long edge, select it and use `Right-click → Subdivide`. That's quicker than a loop cut.
4. Move the points in X, Y and Z until they sit on the line in **every** view.

## 3. Build the surface
- **Wheel arches:** select the faces over the wheel well, inset with `I`, and press `B` during the inset to toggle Boundary so the opening keeps a clean border. Then delete or shape the opening.
- **Flow lines:** with the Knife (`K`), press **`Z` to cut through** the whole mesh, not just the visible faces. (`C` toggles angle snapping.) Use the knife to follow the aero lines.
- **Overlapping vertices in X-ray:** hide the half you aren't working on (`H`, and `Alt H` to unhide).

## 4. Shape and clean topology
- **Flatten surfaces:** in Edit Mode, use `LoopTools → Flatten`, or `S Z 0` on a selection. The sculpt Flatten brush is the Sculpt Mode equivalent.
- **Bulge fenders and the hood along their normals:** Shrink/Fatten, `Alt S`.
- **Too many loops:** make a redirecting knife cut, then remove the trailing loops with `X → Dissolve Edges`.
- **Quads:** keep the mesh in quads. Put any unavoidable triangle or n-gon on a flat or hidden area.

## 5. Separate panels
- Before splitting a panel such as the windshield, give its border edges **Bevel Weight 1.0**.
- **Rip:** `V` rips along an edge so a door or vent becomes its own island. For a clean split use `Y` (Split), or `P` to separate it into its own object.
- **Bridge two loops** with `LoopTools → Loft`, or `Edge → Bridge Edge Loops`. Use this for vent interiors and panel gaps.

## 6. Modifier stack
- **Normals:** turn on the Face Orientation overlay often. Fix red (flipped) faces with `Shift N`.
- **Stack order:** Mirror (top, clipping on, a small Merge distance to stop the seam flickering) → Bevel (Limit Method: **Weight**) → Subdivision (`Ctrl 3`).
- **Broken symmetry:** fix it with `Mesh → Symmetrize`.
- **Sharp creases:** `Select → Select Sharp Edges`, adjust the selection by hand, set Bevel Weight to 1.0, then tune individual weights in the N panel.
- **Share the stack:** `Ctrl L → Copy Modifiers` copies it to every separated panel.

## 7. Wheels
- **Rim:**
  1. Model one spoke.
  2. Add an Empty at the hub and apply its rotation and scale.
  3. Give the spoke an Array modifier with Object Offset set to the Empty.
  4. Rotate the Empty 360°/n, for example 72° for 5 spokes.
- **Four wheels:** put the tire, rim and hub in one collection (`M → New Collection`), then use `Add → Collection Instance` (or right-click the collection → Instance to Scene) four times. The instances update when you edit the original wheel.

## Driving it with BlendPro
| Step | Recipe / tool |
|---|---|
| Blueprints at real size | `reference_image_setup` for each view (`real_width` = car length in the side view), then `reference_compare` to score the body's silhouette per view |
| Mirror half | `mirror_setup` (bisect, clipping, merge) |
| Weighted bevels on sharp edges | `bevel_weight_by_angle` (marks the edges and adds a weight-limited Bevel) |
| Fix symmetry | `symmetrize` |
| Spokes | `radial_array` |
| Topology check | `mesh_analysis` (n-gons, flipped normals, non-manifold) instead of screenshots |
| Panel thickness | `solidify_shell` |
| Game or XR export | `xr_budget_report`, then `export_asset` |

Tracing outlines vertex by vertex is slow through an API. For an agent, block out with primitives sized from `reference_measure`, then refine against `reference_compare` row errors.
