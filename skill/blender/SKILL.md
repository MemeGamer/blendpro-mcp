---
name: blender
description: Practical Blender know-how — modeling (character blockouts, SubD, shrinkwrap, eye/beak tricks), boolean cleanup and quad retopology of boolean seams, sculpting (voxel remesh, brushes, masking, face sets, sculpt vertex paint), materials and texture painting, Thin Wall transparency, Geometry Nodes (stylized fur/feathers, custom normals, instance animation), stylized Eevee rendering, free add-on recommendations, and running Blender headlessly with Python (bpy). Use whenever the user asks how to do something in Blender, wants a Blender workflow, node setup, modifier stack, render settings, or add-on suggestion, or wants a bpy script written or run — even if they only mention a .blend file, Eevee, Cycles, Geometry Nodes, or "3D model" without naming Blender.
---

# Blender

Curated, workflow-level Blender knowledge. Read only the reference file(s) that match the question — each is short and self-contained.

## Reference index

| Topic | File | Covers |
|---|---|---|
| Character modeling | `references/modeling/character-modeling.md` | SubD blockout from a cube, Shrinkwrap + Solidify for wings/armor, "eye pancake" trick, Tangent Normal Project, cylinders instead of cones under SubD |
| Boolean cleanup | `references/modeling/boolean-cleanup.md` | Bool Tool workflow, Merge by Distance, knife retopo of boolean seams, n-gon → quads, stepping edge density down (24→12), Grid Fill, bevel weights + Bevel modifier (Weight limit, Arc miter), SubD check, Symmetrize, LoopTools Relax |
| Sculpting (beginner, stylized) | `references/sculpting/sculpting-for-beginners.md` | Tablet/navigation prefs, ortho-only reference images, apply scale before sculpting, voxel remesh (`R` / `Ctrl R`), core brushes (Grab, Clay Strips, Scrape, Draw Sharp, Pinch), masking and Face Sets, sculpt-mode vertex painting with cavity auto-masking |
| Texture painting | `references/materials/texture-painting.md` | Face-selection masking, gradient fill, non-destructive line-art layer via second Image Texture + Mix Color |
| Thin Wall transparency | `references/materials/thin-wall-transparency.md` | Principled BSDF Thin Wall for windows, leaves, bubbles, paper; replaces Solidify hacks |
| Stylized fur & feathers (Geo Nodes) | `references/geometry-nodes/stylized-fur-feathers.md` | Scatter/instance on surface, borrowing a sphere's normals via Sample Nearest Surface + Set Mesh Normal, Scene Time noise wind animation, per-instance random color attribute |
| Stylized Eevee rendering | `references/rendering/stylized-eevee.md` | Ray tracing off for toon looks, soft "fast GI" values, Fresnel/Layer Weight rim light in the shader |
| Essential modeling add-ons | `references/addons/essential-modeling-addons.md` | Edge Flow, Mesh Align Plus, Tissue, LoopTools, Quick Snap, Re-Primitive — with exact hotkeys/menus |
| Free add-ons mega list | `references/addons/free-addons-mega-list.md` | ~25 free add-ons grouped by CAD, procedural, assets, cables/cloth, retopo, export/utility |

When a request spans topics, combine them, e.g. stylized bird character: modeling → fur/feathers → texture painting → stylized Eevee; sculpted character: sculpting → (retopo) → texture painting or sculpt vertex paint → stylized Eevee; hard-surface part: boolean cleanup → bevel/SubD → materials.

## How to answer

- Give the concrete path: menu location, node names exactly as Blender labels them, hotkeys, and numeric starting values. Name nodes in `backticks` (`Sample Nearest Surface`, `Set Mesh Normal`).
- Prefer non-destructive solutions (modifiers, node layers, attributes) unless the user wants to bake/apply.
- Blender UI and node names shift between versions (e.g. Auto Smooth became a modifier in 4.1; Eevee was rebuilt in 4.2; 5.x continues to rename nodes/sockets). If the user's version matters and is unknown, ask or state the version the answer assumes. Treat version-specific claims in the references as "verify in your build" rather than guaranteed. Verified against Blender 5.2.2: Principled BSDF has a boolean `Thin Wall` input; the Shrinkwrap method is named `Target Normal Project`; `Set Mesh Normal` (Free mode) exposes a `Custom Normal` input and only works on real mesh, so put it after `Realize Instances`; LoopTools and Bool Tool are not bundled and install from Get Extensions (Bool Tool shortcuts are `Ctrl Shift Numpad -` difference / `Ctrl Shift Numpad +` union). Voxel remesh size is in scene units, so values like `0.03` assume a roughly 1–2 m object with scale applied. Since 4.3, sculpt brushes are assets picked from the asset shelf, not a fixed toolbar.
- For add-ons: note which are bundled (LoopTools is under Preferences → Get Extensions / Add-ons) vs. third-party downloads, and that availability changes — don't invent download URLs.

## Driving Blender: BlendPro MCP (preferred)

When the `blendpro` MCP tools are available, use them instead of shelling out to Blender. Source: this repository (`src/blendpro`). The add-on auto-starts on port 9877 when Blender opens. If no Blender window is open, call `blender_session(action="start_headless", blend_file=...)`.

Token-cheap loop, with images last:
1. `scene_overview` (filter or types for big scenes), then `inspect` / `node_tree_get` only for what you'll touch.
2. Edit in as few calls as possible:
   - `edit_objects` for batched simple edits (rotations are in degrees).
   - `node_tree_build` to build or patch a whole material or GN tree in one call.
   - `run_python` for everything else (assign `result`).
   - `run_operators` for `bpy.ops` that need edit mode, a selection or viewport context, including add-on operators.
3. Read the returned `diff`. Don't re-query to confirm.
4. Verify with text: `mesh_analysis` (pass `target="quest"`/`"webxr"`/`"unity"` for budgets) and `spatial_report` (floating/sunk objects, intersections, camera framing, ASCII maps).
5. Use `render_views` once to judge looks. It returns one contact sheet; keep `size` at 256–400 and pass `objects` to isolate. Use `viewport_screenshot` only to see what the user sees.
- Unsure of an operator arg, socket name or enum? Call `api_lookup` (`op:`, `node:`, `struct:`, `path:`, `enum:`, `search:`) before writing code.
- Call `checkpoint("save", name)` before destructive steps (applying modifiers or booleans, remeshing, deleting).
- Export with `export_asset` presets: `unity_fbx`, `webxr_glb` (Draco), `glb`, `obj`, `usd`.
- **Tripo3D (AI 3D generation; spends the user's Tripo API credits):**
  - Use it for organic or complex assets: characters, creatures, detailed props. Model simple or hard-surface pieces directly instead.
  - Iterate with `tripo_generate(preset="draft")`, which has no texture and is cheapest, then rerun with `standard`/`detailed`, or `xr_lowpoly`/`game_ready` for Quest/WebXR. Pin `options.model_seed` to keep a shape you like.
  - Always pass `target_size` (metres) and `location`, then check with `mesh_analysis(target=...)`.
  - Post-process with `tripo_process`:
    - `retopo` (face_limit, quad, smart_uv)
    - `rig` (`spec="unity"` or `"mixamo"`)
    - `retarget` (`animation="preset:walk"`)
    - `convert`
  - Use `tripo_task("balance")` before batch jobs.
  - If the user generates in Tripo Studio instead, the Tripo Bridge add-on pushes models into Blender (`tripo_task("bridge")` shows its state). Find them with `scene_overview`.

### Recipes: 100 tested one-call tasks

Prefer `recipes` over hand-written bpy when one fits. Each recipe is a parameterized function that was tested headless on Blender 5.2.
- Discover: `recipes(action="list", category=..., query=...)`.
- Read the parameters: `recipes(action="doc", name=...)`.
- Execute: `recipes(action="run", name=..., params={...})`. It returns the result plus the scene diff.

Categories:
- **modeling**: rounded_box, boolean_cut, pipes, arrays, lathe, text, rocks, stairs, mesh_cleanup, fix_ngons, apply_transforms, set_origin, decimate_to_tris, remesh, smooth_by_angle, UV unwrap/lightmap_uv2/checker/texel_density
- **shading**: PBR from texture folders, procedural materials, lighting rigs, HDRI/sky, camera framing/turntable, render presets, baking (AO, normal, diffuse, atlas), compositor
- **rigging**: biped and joint-position armatures, auto weights, ≤4-influence limits, IK, bone renaming for Mixamo/Unity, keyframing, procedural walk/idle, bake action, NLA, shape keys, drivers, retarget
- **geonodes / pipeline**: scatter, curve instancing, procedural buildings and fences, cables, stylized fur, terrain; LODs, UCX collision, batch export, naming, purge, XR budget report, texture resize, draw-call merge

Source: `blendpro-mcp/src/blendpro/addon/recipes/*.py`. Tests: `tests/test_*.py`, run with `blender --background --factory-startup --python tests/run_recipe_tests.py -- <module>`.

### Character pose understanding (MediaPipe)

`pose_analyze(objects=[character_root], view="front", rest_pose=True, create="lines")`:
- Renders an orthographic view, runs MediaPipe Pose (33 landmarks), and returns an overlay image with the skeleton drawn. Subject's left is blue, right is orange.
- Lifts each joint into 3D at the middle of the limb (ray entry and exit through the mesh). It also derives pelvis, spine, chest, neck and head_top.
- Draws an orange skeleton in the viewport, in front of the mesh (`MP_Pose` collection).
- Returns metrics: elbow and knee angles, arm elevation (T-pose or A-pose), limb lengths and L/R asymmetry, spine lean, facing direction and ankle heights.
- Use `rest_pose=True` for rigging. Detection is much better on T/A poses than on twisted action frames.
- `frames=[...]` checks an animation frame by frame (knee and elbow angles, ankle heights for foot contact) without images.
- Feed `joints` to the rigging recipe `armature_from_joint_positions` to build a starter armature.
- Limits:
  - MediaPipe needs a face-like head (a faceless or logo head fails) and a roughly humanoid figure facing the view. The character should face −Y.
  - When MediaPipe mirrors left and right, the labels are corrected automatically for front and back views.
  - Joints outside the silhouette are marked `estimated`.

### Blender internals

Read `references/internals/blender-architecture.md` when debugging stale or evaluated data, operator context failures, node sockets, layered actions, or 5.x API changes. It covers DNA/RNA, the depsgraph and evaluated copies, mesh attributes and bmesh, operators and poll, Geometry Nodes fields and instances, animation slots, render engines and color management, with Blender source paths.

### Recommended free add-ons and the operators to call via `run_operators`

These are optional. Install them from Blender's Get Extensions (extensions.blender.org); check what's present with `addons(action="list")`.

| Add-on | Operators |
|---|---|
| LoopTools | `mesh.looptools_relax`, `_circle`, `_space`, `_flatten`, `_curve`, `_bridge`, `_gstretch` (edit mode, needs a selection) |
| Bool Tool | `object.boolean_auto_difference`, `_union`, `_intersect`, `_slice` (objects=[target, cutter]; the cutter is consumed), `object.boolean_brush_*`, `object.carve_box` |
| EdgeFlow | `mesh.set_edge_flow`, `mesh.set_edge_linear`, `mesh.set_edge_curve` (edit mode, select edge loops) |
| Mesh Align Plus (RC) | `maplus.alignpointsobject`, `maplus.alignplanesobject`, `maplus.alignlinesobject`. These depend on items stored in its panel, so they're awkward for an agent; usually prefer `run_python` with matrices. |
| Tissue | `object.tissue_tessellate`, `object.dual_mesh`, `object.polyhedral_wireframe`, `object.lattice_along_surface` |
| LODGen | `lodgen.generate_lods`, `lodgen.add_selected` |
| Magic UV | `uv.muv_*` (look them up with `api_lookup("search:muv")`) |
| Blender→Unity FBX (EdyJ) | `export_scene.unity_fbx`. It modifies the scene and then undoes the changes, so in agent workflows use `export_asset(preset="unity_fbx")` instead. |

Deliberately not recommended: LODify (bundles native binaries and launches subprocesses) and QRemeshify (native DLLs, compatibility unverified).

## Running Blender from Claude Code without the MCP (bpy)

Find the Blender executable first: on Windows usually `C:\Program Files\Blender Foundation\Blender <ver>\blender.exe`, on macOS `/Applications/Blender.app/Contents/MacOS/Blender`, on Linux `blender` on PATH.

Headless script execution (no UI):

```bash
blender --background --factory-startup --python script.py
# operate on an existing file:
blender --background scene.blend --python script.py
# pass args to the script after "--":
... --python script.py -- --out render.png
```

bpy guidelines:
- Prefer data-API calls (`bpy.data.objects`, `obj.modifiers.new(...)`, `mat.node_tree.nodes.new(...)`) over `bpy.ops`; ops depend on context/selection and often fail in `--background`.
- Look up nodes/sockets by name (`node.inputs["Base Color"]`) and guard for renamed sockets across versions.
- Save with `bpy.ops.wm.save_as_mainfile(filepath=...)`; render with `bpy.context.scene.render.filepath = ...` then `bpy.ops.render.render(write_still=True)`.
- Never overwrite the user's `.blend` without asking — write to a new file by default.
- To check API details for the installed version: `blender --background --python-expr "import bpy; print(bpy.app.version_string)"`.

Example — the character-modeling SubD blockout as a script:

```python
import bpy, os
bpy.ops.mesh.primitive_cube_add()
body = bpy.context.active_object
body.name = "Body_Blockout"
sub = body.modifiers.new("Subdivision", "SUBSURF")
sub.levels = sub.render_levels = 2
# use an absolute path: "//" is relative to the open .blend and is unreliable for unsaved files
bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath("blockout.blend"))
```
(Verified on Blender 5.2.2 LTS in `--background --factory-startup` mode.)

## Extending this skill

Add new notes as short markdown files under the matching `references/<topic>/` folder and add a row to the index table above.
