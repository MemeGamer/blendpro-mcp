<details><summary><b>Face expressions</b> (5)</summary>

| Recipe | What it does |
|---|---|
| `face_atlas_build` | Pack Face_N.png files into one atlas (8 columns, padded cells), packed into the .blend and written to disk for Unity/WebXR. |
| `face_expression_animate` | Expression timeline with CONSTANT keys plus automatic blinks using each face's blink partner from faces.json. |
| `face_expression_set` | Set or keyframe the face by name ("happy", "angry", "uwu"), tag or index. |
| `face_expression_setup` | Face decal on a head (Shrinkwrap-projected, parented) with an atlas material driven by the decal's `expression` property. 50 faces bundled. |
| `face_sheet_import` | Slice an AI-generated grid sheet of faces into Face_N.png in the shared layout (one scale for the whole sheet, eyes at y=516). |

</details>

<details><summary><b>Reference images</b> (4)</summary>

| Recipe | What it does |
|---|---|
| `reference_details` | Small surface details in a reference (sprinkles, seeds, rivets): count, density, length/width spread, orientation spread, colour palette. |
| `reference_compare` | Render the model's silhouette from the reference view and score it: IoU, area ratio, centroid offset, per-row width error. |
| `reference_image_setup` | Put a reference image in the scene as an Image empty, aligned to front/left/top/... and scaled so the silhouette is real size. |
| `reference_measure` | Measure a reference view as text: silhouette size, row-by-row width profile, holes, k-means palette, top/middle/bottom colour bands. |

</details>

<details><summary><b>Modeling and UV</b> (25)</summary>

| Recipe | What it does |
|---|---|
| `apply_transforms` | Apply (freeze) rotation/scale (and optionally location) into mesh/curve data without bpy.ops. |
| `array_along_curve` | Repeat `target` along a curve (Array + Curve modifiers): chains, fences, rails, tank treads. |
| `bevel_weight_by_angle` | Set bevel weight on edges whose face angle exceeds `angle` (deg) and add a weight-limited Bevel modifier, |
| `boolean_cut` | Boolean DIFFERENCE/UNION/INTERSECT of `target` with a cutter object, then merge/degenerate cleanup. |
| `decimate_to_tris` | Reduce a mesh to roughly target_tris triangles (Decimate COLLAPSE, ratio computed from current count). |
| `fix_ngons` | Remove ngons (faces with >4 sides): mode TRIANGULATE splits only ngons into tris (game export safe); |
| `join_objects` | Join several mesh objects into one (first object keeps its origin/transform), optional rename and |
| `lathe_profile` | Revolve a 2D (radius, height) profile around Z: vases, bottles, columns, wheels, lamp shades. |
| `low_poly_rock` | Faceted low-poly rock/boulder: noise-displaced icosphere, flattened, flat shaded, sitting on z=0. |
| `mesh_cleanup` | One-shot cleanup for imported/boolean/scan meshes: merge by distance, dissolve degenerate, |
| `mirror_setup` | Symmetric modeling: Mirror modifier with bisect (cuts away the -side), clipping and merge. |
| `pipe_along_points` | Tube/pipe/cable through a list of 3D points (local to location). smooth=True uses a smooth NURBS path, |
| `radial_array` | Radial (circular) array of `target` around its own origin using an Array modifier + rotated pivot Empty. |
| `remesh_voxel` | Voxel remesh into uniform, manifold quads (fixes non-manifold/intersecting scans or kitbash before sculpting). |
| `rounded_box` | Box with real dimensions (scale stays 1) + Bevel modifier + Weighted Normal for clean hard-surface shading. |
| `seams_by_angle_unwrap` | Mark UV seams on edges sharper than `angle` (deg) + open boundaries, then unwrap and pack. |
| `separate_parts` | Split one mesh object into several: by LOOSE (connected parts) or MATERIAL. Returns new object names. |
| `set_origin` | Move the object origin without moving the geometry. mode: BOTTOM_CENTER (floor placement), |
| `smart_uv_unwrap` | Unwrap a mesh: SMART (smart project, good default for hard-surface), CUBE (box mapping), UNWRAP |
| `smooth_by_angle` | Blender 5.x "auto smooth": shade smooth + mark edges sharper than `angle` (degrees) as sharp |
| `solidify_shell` | Give a surface real thickness with a Solidify modifier (walls, cloth, armor, leaves that must cast shadows). |
| `stairs` | Solid straight staircase (one closed mesh) climbing +Y, width along X centred on origin, base at z=0. |
| `symmetrize` | Make a mesh exactly symmetric by copying one half onto the other (Mesh > Symmetrize, via bmesh). |
| `texel_density` | Report texel density (pixels per metre, world scale included) of a mesh's UVs for a given texture size; |
| `text_3d` | 3D text (extruded, optional bevel), centred on its origin; to_mesh=True converts to a mesh for export. |

</details>

<details><summary><b>Materials, lighting, camera, render, bake</b> (25)</summary>

| Recipe | What it does |
|---|---|
| `bake_maps` | Bake texture maps from an object's materials (Cycles) into one image per map and save PNGs. |
| `bake_normal_high_to_low` | Bake a tangent-space normal map from a high-poly mesh onto a low-poly mesh's UVs (Cycles selected-to-active). |
| `camera_dof_focus` | Enable depth of field focused on an object (tracks it when it moves) or at a fixed focus_distance (m). |
| `compositor_effects` | Rebuild the scene compositor with common post FX: exposure -> glare/bloom -> lens distortion/dispersion -> vignette. |
| `emissive_neon_material` | Neon-tube emission: saturated colour at grazing angles, hot white-ish core facing the camera. |
| `frame_objects_camera` | Aim and dolly a camera so targets (default all visible meshes) exactly fill the frame + margin. |
| `glass_material` | Physically based glass that renders correctly in both EEVEE and Cycles. |
| `hdri_world` | Image-based lighting: environment texture world with rotation (Mapping Z) and strength. |
| `material_report` | Audit materials: users, objects using each, image textures, MISSING texture files, empty slots, objects without |
| `outline_effect` | Ink outlines. method='inverted_hull': Solidify (flipped, outward) + backface-culled outline material on target - |
| `pbr_material_from_textures` | Build a Principled PBR material from a texture set (folder or file list), auto-detecting maps by filename. |
| `procedural_material` | Procedural (texture-free, UV-free) material preset: brushed_metal, polished_metal, wood, concrete, car_paint, plastic, rubber. |
| `random_color_per_object` | One shared material that gives every object a different colour from a palette (Object Info > Random). |
| `render_still` | Render one frame to filepath and return path + pixel size; scene settings are restored afterwards. |
| `replace_material` | Swap material `old` for `new` in every slot (object- or data-linked) of all objects, or only `objects`. |
| `set_render_preset` | Apply a coherent render configuration: preset draft/preview/final sets resolution % and samples per engine |
| `shadow_catcher_ground` | Ground plane that only shows shadows/reflections (Cycles shadow catcher) under targets (default all meshes), |
| `sky_world` | Physical sky world (Blender 5.x Sky Texture: MULTIPLE_SCATTERING / SINGLE_SCATTERING = Nishita, or PREETHAM, |
| `sun_time_of_day` | Place a Sun lamp from time of day + date + latitude (or explicit elevation/azimuth in degrees). |
| `three_point_lighting` | Key/fill/rim area-light rig scaled to the bounds of targets (default: all visible meshes). |
| `toon_shader_material` | Cel/toon shader: Diffuse -> Shader to RGB -> constant ColorRamp bands (+ optional fresnel rim light). |
| `triplanar_material` | Box/triplanar-projected image material - textures meshes with no or bad UVs (scans, AI/Tripo meshes, kitbash). |
| `turntable_setup` | Seamless 360-degree turntable over `frames` frames with linear keys. |
| `uv_checker_material` | Temporarily put a UV checker on objects to inspect stretching/seams, then restore originals. |
| `vertex_color_material` | Material that displays a mesh colour attribute (vertex/corner colours) - for scans, AI-generated and painted meshes. |

</details>

<details><summary><b>Rigging and animation</b> (26)</summary>

| Recipe | What it does |
|---|---|
| `squash_stretch_rig` | Squash & stretch rig: root/bottom/top controls, Stretch To B-Bone body with volume preservation, widgets, bone collections, auto weights with a pinned base. |
| `action_report` | Summarise an object's animation: active action + slot, frame range, F-curve count, keys and channels per bone |
| `add_root_bone` | Add a root bone at the armature origin (pointing +Y, on the floor) and parent all current top-level bones to it. |
| `apply_pose_as_rest` | Make the current pose the new rest pose (e.g. convert A-pose to T-pose for Unity). Skinned meshes are first |
| `armature_from_joint_positions` | Build an armature from joint positions: one bone per joint, head at the joint, tail at its child. |
| `auto_weight_parent` | Parent a mesh to an armature with automatic (bone-heat) weights; falls back to envelope then to nearest-bone |
| `bake_action` | Bake constraint/IK/driver-driven motion into plain keyframes (visual transforms) on a new action — required before |
| `biped_armature` | Create a 22-bone humanoid T-pose armature (Hips..Head, arms, legs, toes) that maps to Unity Humanoid. |
| `child_of_pickup` | Animate a prop being picked up by a bone (hand) and optionally released: Child Of constraint with keyed influence |
| `driver_add` | Add a scripted-expression driver. variables=[{"name":"var","type":"TRANSFORMS","target":"Empty", |
| `follow_path` | Move an object along a path: builds a curve from points (default: 4 m circle), adds Follow Path constraint with |
| `idle_breathing` | Subtle looping idle: chest/upper chest breathe (rotation + slight scale), shoulders rise, head drifts, hips sway. |
| `ik_chain` | Add an IK control bone + pole target for a limb. bone = the LAST bone of the chain (shin/forearm; canonical keys ok). |
| `keyframe_transform` | Insert transform keyframes in one call. keys=[{frame, location?, rotation_deg?, scale?}, ...]; rotation is Euler |
| `limit_weights_and_normalize` | Keep the strongest `limit` bone influences per vertex (Unity/glTF = 4), prune weights < prune_below, normalize to 1. |
| `look_at_constraint` | Make an object or a bone aim at a target (Damped Track for bones / Track To for objects like cameras). |
| `nla_push_and_stack` | Put actions on NLA strips: mode='sequence' lays them end-to-end on one track (gap frames between), mode='stack' |
| `organize_bone_collections` | Sort bones into Bone Collections (Blender 4+/5 API, replaces layers) and colour them. |
| `procedural_walk_cycle` | Keyframe a looping in-place walk on a humanoid rig (any naming convention): thighs/shins/feet, arm swing, |
| `rename_bones_to_convention` | Rename humanoid bones between conventions: 'blender' (UpperArm.L), 'mixamo' (mixamorig:LeftArm), 'unity' |
| `retarget_simple` | Copy animation from a source armature to a target armature by bone mapping (auto: matched via canonical humanoid |
| `root_motion_extract` | Remove (mode='in_place') or move (mode='to_root' / 'to_object') the horizontal travel of the hips. |
| `scale_rig_fix` | Apply rotation+scale on an armature (and its child meshes) so everything is at scale 1 without moving — fixes |
| `set_fps_and_range` | Set scene FPS and frame range. fit_to=object name uses its active action's frame range (handy before exporting a |
| `set_interpolation` | Set keyframe interpolation (CONSTANT/LINEAR/BEZIER/SINE/...) and easing on all (or filtered) F-curves of a |
| `shape_key_animate` | Create a shape key (blendshape, exported to Unity) by offsetting vertices in a region/vertex group, then keyframe |

</details>

<details><summary><b>Geometry Nodes</b> (11)</summary>

| Recipe | What it does |
|---|---|
| `cable_between_points` | Sagging cable/wire/rope between two points as a tube mesh (catenary-shaped cosh profile). |
| `instance_along_curve` | Place instances of an object every `spacing` metres along a curve object (posts, lamps, chain links, bricks). |
| `procedural_building` | Procedural building blockout: box of floors x floor_height with an inset window grid on all side walls. |
| `procedural_fence` | Fence/railing along a curve: box posts every post_spacing m + round rails following the curve. |
| `procedural_stairs` | Solid straight staircase (each step a box down to the floor) climbing along +Y from the object origin. |
| `random_greebles` | Random sci-fi greebles: inset + extrude a random subset of faces by random heights (non-destructive). |
| `realize_and_apply_gn` | Bake an object's full modifier/Geometry Nodes result (instances realized) into a real, exportable mesh. |
| `scatter_on_surface` | Scatter instances (object or collection) randomly over a mesh with a Geometry Nodes modifier. |
| `scatter_with_min_distance` | Poisson-disk scatter: instances never closer than min_distance (trees, rocks, crowd spots, no overlaps). |
| `stylized_fur_cards` | Stylized fur/grass/feather cards with the "steal the surface normals" trick for soft toon shading. |
| `terrain_from_noise` | Noise-displaced grid terrain (smooth shaded, with UVMap) as a new mesh object `name`. |

</details>

<details><summary><b>XR / game-engine pipeline</b> (14)</summary>

| Recipe | What it does |
|---|---|
| `apply_and_freeze_for_export` | Make export-ready copies: modifiers applied, rotation/scale (and location unless keep_location) baked into the mesh, |
| `batch_export` | Export each object (by="object") or each collection (by="collection") to its own file with an export preset |
| `generate_lods` | Create LOD0..N copies (Decimate collapse, modifiers applied) named <base>_LOD0.. under an empty <base>_LODGroup. |
| `gltf_optimize_export` | Export an optimized GLB for WebXR / three.js / model-viewer: mesh compression (draco / meshopt / none) and |
| `lightmap_uv2` | Ensure each mesh has a second, non-overlapping UV channel for lightmaps (Unity UV1 / "UV2"). |
| `merge_by_material` | Cut draw calls: join meshes that share the same material set into one mesh per set (world transforms and |
| `naming_convention_fix` | Apply game-engine naming: SM_ static meshes, SK_ skinned meshes (armature modifier), SKEL_ armatures, |
| `purge_orphans` | Delete all datablocks with zero users (meshes, materials, images, node groups...), recursively by default. |
| `scene_units_setup` | Set scene units for engine export (Unity/WebXR/visionOS: metric, unit scale 1.0 => 1 BU = 1 m) and report |
| `set_pivot_bottom_center` | Move each mesh's origin to its bottom-center (or "center") without moving the geometry in the world, so props |
| `texture_files_maintenance` | External-file housekeeping. action: report (missing/packed/external files for images, libraries, fonts, sounds, |
| `texture_resize` | Downscale images larger than max_size px (longest side, aspect kept) into new files and optionally swap them in |
| `unity_collision_mesh` | Build a convex-hull collision mesh UCX_<base>_00 parented to the target (same transform), wireframe, no render. |
| `xr_budget_report` | Scene performance report vs an XR budget: evaluated tris (instances included), draw calls (~material slots per |

</details>
