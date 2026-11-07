# How Blender works internally (for writing correct bpy code)

This is a mental model of Blender's C/C++ core, written so the right Python call and the reason it fails are obvious. Source paths are relative to the Blender repository (projects.blender.org/blender/blender, mirrored on GitHub). Names drift between versions, so on the installed build use `api_lookup` (BlendPro) to confirm exact identifiers.

## 1. Data: DNA → RNA → Python

| Layer | Where | What it is |
|---|---|---|
| **DNA** | `source/blender/makesdna/DNA_*_types.h` | Plain C structs that are written byte-for-byte into .blend files (`Object`, `Mesh`, `Material`, `bNodeTree`...). File compatibility works by "SDNA" struct descriptions stored in each file, so old files load by struct-name/field-name matching. |
| **RNA** | `source/blender/makesrna/intern/rna_*.cc` | A reflection layer over DNA. It defines properties (type, range, default, enum items, update callbacks) and functions. *Everything* bpy exposes, the UI, drivers, keyframes and the outliner goes through RNA. |
| **bpy** | `source/blender/python/intern/bpy_rna.cc` | Generic Python wrapper around RNA. `obj.location` is an RNA property lookup, not a Python attribute. That's why `dir()` and `obj.bl_rna.properties` show the truth, and why a typo raises `AttributeError` instead of silently creating an attribute. |

Consequences:
- **Property updates fire RNA `update` callbacks**, which tag the depsgraph. Writing raw data (`mesh.vertices[i].co`, `foreach_set`) does **not always** tag. Call `mesh.update()` or `obj.data.update()`, or for attributes `mesh.attributes[...]`, then `view_layer.update()`.
- **ID datablocks** (`bpy.types.ID`: Object, Mesh, Material, NodeTree, Image, Action, Collection, World, Scene...) live in `bpy.data.*` lists (`Main` in C, `BKE_main.hh`). Everything else, such as modifiers, nodes, bones and keyframes, is owned by an ID.
- **Names are unique per ID type** and truncated to 63 bytes. `new("X")` may return "X.001". Always use the returned object's `.name`.
- **Users / refcount:** an ID with 0 users is an orphan and is dropped on save (unless `use_fake_user`). `bpy.data.orphans_purge(do_recursive=True)` removes them now. `obj.data = other_mesh` changes users. Multiple objects can share one Mesh (linked duplicates), so applying modifiers or transforms on shared data fails or affects all users. Make it single-user first (`obj.data = obj.data.copy()`).
- **Library linking and overrides:** linked IDs (`id.library`) are read-only. Library overrides (`id.override_library`) allow local edits on linked data.
- **Python references go stale.** After undo/redo, file load, or adding or removing items in a collection (e.g. `mesh.vertices.add`, `bpy.data.objects.remove`), stored `bpy_struct` references can point to freed memory. Re-fetch by name.

## 2. Evaluation: the dependency graph

- **Where:** `source/blender/depsgraph/`. Per view layer, Blender builds a graph of components (transform, geometry, shading, animation, parameters...) and their relations (parent → child transforms, modifier inputs, drivers, constraints, GN Object Info...).
- **Copy-on-evaluation:** the depsgraph makes *evaluated copies* of IDs (`obj.evaluated_get(depsgraph)`) and runs animation, drivers, constraints and the **modifier stack / geometry nodes** on those copies. The original data (`bpy.data`) is never modified by evaluation.
  - `obj.data` is always the original, un-modified mesh. For the result after modifiers, use `obj.evaluated_get(dg).to_mesh()` (and `to_mesh_clear()`), or `bpy.data.meshes.new_from_object(eval_obj)` to keep it.
  - `obj.matrix_world` on the original is synced back after evaluation. `obj.bound_box` is the evaluated bounds.
- **Tagging:** RNA updates tag nodes in the graph. `bpy.context.evaluated_depsgraph_get()` evaluates pending changes and returns the graph. `view_layer.update()` also flushes. Reading `dimensions` or `matrix_world` right after setting `location` without an update can return stale values.
- **Instances** (collection instances, particles, GN instances) are not objects in `bpy.data`. Iterate `depsgraph.object_instances` (`is_instance`, `instance_object`, `matrix_world`, `parent`).
- **Frame changes:** `scene.frame_set(f)` re-evaluates animation for that frame. Setting `scene.frame_current` alone doesn't evaluate.

## 3. Mesh data layout (Blender 4.x/5.x)

- **Where:** `source/blender/blenkernel/BKE_mesh.hh` and `intern/mesh*.cc`. Mesh is **attribute-based**:
  - positions (`"position"`, point domain)
  - edges (`".edge_verts"`)
  - faces as offsets into corners (`face_offsets`, with `".corner_vert"` and `".corner_edge"`)
  - everything else is a named attribute on a **domain**: POINT, EDGE, FACE, CORNER (face corner / "loop"), plus INSTANCE and CURVE/LAYER in other geometry types
- **Built-in attributes:** `sharp_face`, `sharp_edge`, `material_index`, `.select_vert`, `crease_edge`, `bevel_weight_edge`, UV maps (`FLOAT2` on CORNER), and color attributes (`FLOAT_COLOR` / `BYTE_COLOR` on POINT or CORNER). Leading-dot names are internal.
- **Removed or changed in recent versions:**
  - `mesh.use_auto_smooth` and `calc_normals_split` are gone. Use the "Smooth by Angle" modifier/node group, or `sharp_edge`/`sharp_face` with custom normals.
  - Custom normals are set with `mesh.normals_split_custom_set(...)`.
- **Fast bulk I/O:** `mesh.attributes["position"].data.foreach_get("vector", arr)` and `vertices.foreach_get("co", arr)` with numpy is about 100× faster than Python loops.
- **bmesh** (`source/blender/bmesh/`) is the editable half-edge structure (BMVert, BMEdge, BMFace, BMLoop) that edit mode uses.
  - `bmesh.new(); bm.from_mesh(me) ... bm.to_mesh(me); bm.free()`.
  - In edit mode use `bmesh.from_edit_mesh(me)` + `bmesh.update_edit_mesh(me)`.
  - `bmesh.ops.*` are the same C operators edit-mode tools use (bevel, extrude, remove_doubles, triangulate, bisect_plane, convex_hull...).
  - Call `ensure_lookup_table()` before indexing.
  - **Edit mode holds its own copy.** `obj.data` (Mesh) is stale while in edit mode until `obj.update_from_editmode()` or leaving edit mode, and writes to Mesh are overwritten when edit mode exits.

## 4. Operators, context, and why `bpy.ops` fails

- **Where:** `source/blender/windowmanager/` (wm_operators.cc, WM_types.hh) and `source/blender/editors/*/`. An operator (`wmOperatorType`) has `poll(C)`, `exec(C, op)`, optionally `invoke` (interactive, with event) and `modal`.
- **`bpy.ops.x.y()`** checks `poll()` with the **current context** (window, screen, area, region, active object, mode, selection), then runs `exec`.
  - `RuntimeError: Operator bpy.ops.X.poll() failed, context is incorrect` means the context is wrong: wrong area (view3d ops need a VIEW_3D area), wrong mode (mesh ops need edit mode), or no active/selected object.
- **Fix:** `with bpy.context.temp_override(window=..., area=..., region=..., active_object=o, selected_objects=[o]):`. BlendPro's `run_operators` does this. Headless (`--background`) has no window, area or region, so UI-bound ops (view3d.*, screen.*) can't run there.
- **Undo:** operators with the `REGISTER|UNDO` flags push undo steps. Data-API edits from Python don't. BlendPro pushes `ed.undo_push` after each call in the GUI. Global undo restores the whole Main, which invalidates every Python reference.
- **Prefer the data API** (`obj.modifiers.new`, `bmesh.ops`, `mesh.attributes`, `node_tree.nodes.new`). It's faster, context-free and deterministic. Use ops only for things without a data API: booleans *applied* via `object.modifier_apply`, `object.transform_apply`, `uv.smart_project`, importers/exporters, `object.join`, mode switching, baking.

## 5. Modifiers and Geometry Nodes

- **Where:** `source/blender/modifiers/` and `source/blender/nodes/geometry/`.
  - Each modifier is a function from a geometry set to a geometry set, evaluated in stack order on the evaluated copy.
  - `show_viewport`/`show_render` gate evaluation.
  - `object.modifier_apply` bakes the stack up to that modifier into the original mesh.
- **Geometry Nodes** is a modifier (`type='NODES'`) whose `node_group` is a `GeometryNodeTree`.
  - **GeometrySet:** one evaluation can carry mesh + curves + point cloud + instances + volume + Grease Pencil at once.
  - **Fields:** a socket like `Position` or `Index` isn't a value but a *function evaluated per element* in the context of the node that consumes it (the geometry and domain it reads from). Diamond-shaped sockets accept fields; circles are single values.
  - Capture Attribute freezes a field at one point. Store Named Attribute writes it out (visible to shaders via an Attribute node; use type "Instancer" for instance-domain attributes).
  - **Instances** are cheap references until `Realize Instances`. Many mesh nodes, including `Set Mesh Normal`, act only on real mesh, so realize first when needed.
  - **Modifier inputs** are the group's interface sockets (`node_group.interface.items_tree`). In **5.2+**, set them with `mod.properties.inputs.Socket_2.value = 5.0` (`.attribute_name` for attribute inputs). The old ID-property access `mod["Socket_2"]` now raises `TypeError: this type doesn't support IDProperties`. Before 5.2 it was `mod["Socket_2"]` / `mod["Socket_2_attribute_name"]`. Then call `obj.update_tag()`. BlendPro has the version-safe helpers `util.gn_input_get` / `gn_input_set`.
  - **Bake:** the Bake node / modifier bake stores results to disk. `realize` + `object.convert(target='MESH')` produces a static mesh.
- **Node trees in general** (`source/blender/nodes/`, `blenkernel/intern/node*.cc`):
  - `bNodeTree` has nodes and links. Sockets have `identifier` (stable) vs `name` (display, may repeat).
  - Unavailable sockets (`socket.enabled == False`) exist but are hidden, e.g. Mix node data types and Math operations.
  - Node-group interfaces use `tree.interface.new_socket(name, in_out, socket_type)` (4.0+).
  - Shader nodes compile to GLSL for EEVEE (`source/blender/gpu/`, `draw/engines/eevee/`) or to SVM bytecode for Cycles (`intern/cycles/scene/shader_nodes.cpp`). That's why some nodes are Cycles-only, e.g. Bevel/AO behave differently, and Shader-to-RGB is EEVEE-only.

## 6. Animation

- **Where:** `source/blender/animrig/` and `blenkernel/intern/anim*.cc`, `fcurve.cc`.
  - Animated properties are **F-Curves** keyed by RNA path (`data_path="location", array_index=0`) inside an **Action**.
  - Since 4.4/5.x Actions are **layered/slotted**: `action.layers[].strips[].channelbag(slot).fcurves`, and an object binds a slot via `animation_data.action_slot`. `action.fcurves` may not exist. `obj.keyframe_insert(data_path, frame=)` and `pose_bone.keyframe_insert` work either way.
- **Evaluation order per frame:** NLA tracks/strips → active action → drivers → constraints. Drivers (`animation_data.drivers`) are F-Curves whose value comes from a Python-like expression or variables. They're re-evaluated by the depsgraph, and expressions outside the simple-expression subset need "Auto Run Python Scripts".
- **Armatures:**
  - Bones (`armature.bones`, rest data) vs **edit bones** (`armature.edit_bones`, only valid in edit mode; head/tail/roll) vs **pose bones** (`object.pose.bones`, per-object animated transforms, constraints, custom shapes).
  - Pose-bone loc/rot/scale are **in the bone's local rest space**, not world. Use `pose_bone.matrix` (armature space) and `obj.matrix_world @ pb.matrix` for world.
  - Skinning is the Armature modifier: vertex groups named like bones get weights. Unity and glTF want ≤4 influences per vertex, normalized.
  - Bone collections (4.0+) replaced armature layers.

## 7. Rendering and drawing

- **Engines:** EEVEE (`source/blender/draw/engines/eevee/`, GPU, real-time, raytraced screen-space GI since 4.2), Cycles (`intern/cycles/`, path tracer; CPU, CUDA, OptiX, HIP, Metal, oneAPI) and Workbench (`draw/engines/workbench/`, the solid-mode look and fastest).
- **Draw manager** (`source/blender/draw/`): the viewport is drawn per engine plus overlay engine (selection outlines, wire, gizmos).
- Headless renders work for all three engines (EEVEE needs a GPU context, which is available on desktop Windows).
- **Color management:** OpenColorIO. View transform AgX is the default since 4.0, and "Standard" gives the literal sRGB look.
  - Image textures for color must be sRGB, but data maps (normal, roughness, metallic) must be **Non-Color**. Wrong color spaces are the #1 PBR mistake.
- **Compositor:**
  - 5.x stores it as a node group on `scene.compositing_node_group` (earlier versions used `scene.node_tree` with `use_nodes`).
  - GPU and CPU compositors share nodes.

## 8. File I/O and add-ons

- **.blend:** an SDNA header plus ID blocks; reading is `blenloader/` (readfile.cc, versioning_*.cc). Versioning code upgrades old data on load, so files saved in newer versions may lose data in older ones.
- **Importers/exporters:**
  - FBX: Python, `scripts/addons_core/io_scene_fbx`.
  - glTF: Python, `io_scene_gltf2`, maintained by Khronos.
  - OBJ, PLY, STL and USD: C++ in `source/blender/io/`.
  - FBX axis conversion produces the classic Unity -90° X rotation. Use "Apply Transform" (`bake_space_transform`) or export with axis settings.
- **Add-ons and extensions:**
  - Since 4.2, extensions (`blender_manifest.toml`) install into repos and import as `bl_ext.<repo>.<id>`. Legacy add-ons (`bl_info`) still load from `scripts/addons`.
  - Registration is `register()`/`unregister()` with `bpy.utils.register_class`.
  - Timers (`bpy.app.timers`) are the only safe way to run code on the main thread from other threads. bpy is **not thread-safe**, which is why BlendPro queues socket commands onto a timer.
- **Handlers:** `bpy.app.handlers.depsgraph_update_post`, `frame_change_pre`, `load_post`, `save_pre`, etc. Use `@persistent` to survive file loads.

## 9. Practical rules that fall out of this

1. Read the evaluated result for anything modifier/GN/animation-dependent, and the original for editing.
2. After writing raw data: `me.update()`, then `bpy.context.view_layer.update()` before reading derived values.
3. Never keep Python references across undo, file load or collection removal. Re-fetch by name.
4. Make data single-user before applying modifiers or transforms. Apply scale before bevel/solidify/physics/export.
5. Use data API first. When an op is unavoidable, give it an explicit context with `temp_override` and set the mode.
6. Use socket `identifier`s or indices when names repeat. Set node props (data_type, operation) **before** setting socket values.
7. Set color-space correctness on every image node, and ≤4 bone influences for real-time targets.
8. In 5.x, check layered actions, compositor node groups, `use_nodes` deprecation and EEVEE engine id (`BLENDER_EEVEE`) via `api_lookup` before assuming older APIs.
