# Procedural sushi roll (Geometry Nodes)

Notes from Bad Normals' procedural sushi tutorial: a customizable roll built from rice-grain instances, a nori wrap and boolean-cut fillings.

## 1. Rice
1. Make a base volume, for example a short cylinder.
2. Turn it into rice positions with **Mesh to Volume → Distribute Points in Volume**. Or use Distribute Points on Faces over a solidified shell.
3. Model one slightly lumpy grain and scatter it with **Instance on Points**.
4. Feed Random Value into Rotation and Scale for natural variation.

## 2. Irregular shape
- Before instancing, move the base points with a Noise Texture.
- To keep the top and bottom flat, mask the offset to X and Y: Noise → Vector Math (Multiply) by `(1, 1, 0)`, or Separate/Combine XYZ, then Set Position.

## 3. Nori wrap
1. Take the outer boundary of the base: the circle profile, or the mesh boundary through Mesh to Curve.
2. Wrap a thin band around it with Curve to Mesh, or an extrude plus Solidify.
3. **Material:** dark green, Roughness about 0.6, with a fine Noise driving Bump for the roasted, crinkly texture.

## 4. Fillings
1. Instance salmon, avocado and cucumber shapes near the centre.
2. Cut the rice away from the fillings with **Mesh Boolean** inside the node tree, so nothing intersects. Realize the instances first if they need to be booleaned.
3. Expose **Roll Radius**, **Rice Density** and **Filling Size** on the Group Input, so each roll can be tuned from the modifier panel.

## Driving it with BlendPro
| Step | Recipe / tool |
|---|---|
| Whole tree in one call | `node_tree_build` (a JSON spec of nodes, links and exposed inputs), then `node_tree_get` to check |
| Grain scatter | `scatter_with_min_distance` (Poisson, no overlapping grains) or `scatter_on_surface` |
| Bake for export | `realize_and_apply_gn` |
| Check the count | `mesh_analysis` / `scene_overview` give the evaluated instance and triangle count. Rice gets heavy fast, so run `xr_budget_report` for XR |
