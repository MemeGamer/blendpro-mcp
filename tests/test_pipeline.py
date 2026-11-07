"""Tests for recipes/pipeline.py (geometry nodes procedural + XR pipeline). Each case runs on an empty scene."""
import os
import tempfile

import bpy

TMP = os.path.join(tempfile.gettempdir(), "blendpro_test_pipeline")


def ev(res, key="instances"):
    return res["evaluated"].get(key, 0)


def faces(res):
    return res["evaluated"].get("faces", 0)


def _lods_ok(res):
    tris = [l["tris"] for l in res["lods"]]
    names = [l["name"] for l in res["lods"]]
    return (names == [f"LodRock_LOD{i}" for i in range(4)] and all(a > b for a, b in zip(tris, tris[1:]))
            and bpy.data.objects[names[0]].parent.name == res["group"])


def _ucx_ok(res):
    o = bpy.data.objects[res["collision"]]
    return o.name.startswith("UCX_PropBody") and 4 <= res["tris"] <= 255 and o.parent.name == "PropBody"


def _uv2_ok(res):
    info = res["objects"]["LightmapBox"]
    me = bpy.data.objects["LightmapBox"].data
    lo, hi = info["uv2_range"]
    return info["status"] == "packed" and me.uv_layers[1].name == "UV2" and 0 <= lo < hi <= 1 and me.uv_layers.active_index == 0


def _freeze_ok(res):
    c = res["copies"][0]
    o = bpy.data.objects[c["copy"]]
    src = bpy.data.objects[c["source"]]
    zs = [v.co.z for v in o.data.vertices]
    xs = [v.co.x for v in o.data.vertices]
    rot_w = 2 * 0.866 + 0.5  # 2x1 box rotated 30 deg -> world X extent
    return (tuple(o.scale) == (1, 1, 1) and tuple(o.rotation_euler) == (0, 0, 0) and c["applied_modifiers"] == 1
            and len(o.data.vertices) > len(src.data.vertices) and abs(min(zs)) < 1e-4 and abs(max(zs) - 1) < 1e-4
            and abs((max(xs) - min(xs)) - rot_w) < 0.08 and abs((max(xs) + min(xs)) / 2 - 1) < 1e-3)  # bevel trims corners


def _files_ok(res, n=2, ext=".fbx"):
    fs = res["files"]
    return len(fs) == n and all(f["path"].endswith(ext) and os.path.getsize(f["path"]) > 0 for f in fs)


def _names_ok(res):
    objs = res["renames"]["objects"]
    return (objs.get("Cube.001") == "SM_Cube" and objs.get("crate") == "SM_crate" and "SM_Cube" in bpy.data.objects
            and "M_Material" in bpy.data.materials and "T_albedo" in bpy.data.images
            and bpy.data.objects["SM_Cube"].data.name == "SM_Cube")


def _budget_ok(res):
    return (res["totals"]["tris"] >= 256 * 128 and res["totals"]["draw_calls"] >= 1 and res["totals"]["texture_mb"] > 0
            and res["top_objects"][0]["name"] == "BudgetSphere" and "tris" in res["status"])


def _resize_ok(res):
    d = res["resized"][0]
    img = bpy.data.images[d["new"]]
    return d["to"] == [1024, 512] and os.path.getsize(d["path"]) > 0 and tuple(img.size) == (1024, 512)


def _merge_ok(res):
    return len(res["merged"]) == 2 and res["draw_calls_before"] == 6 and res["draw_calls_after"] == 2 \
        and all(m["tris"] == 36 for m in res["merged"])


def _pivot_ok(res):
    o = res["objects"][0]
    ob = bpy.data.objects[o["object"]]
    return abs(o["pivot_world"][2] - 1.0) < 1e-4 and abs(o["world_min_z"] - 1.0) < 1e-4 \
        and abs(ob.location.x - 2) < 1e-4 and min(v.co.z for v in ob.data.vertices) > -1e-5


def _gltf_ok(res):
    return os.path.getsize(res["filepath"]) > 0 and res["raw_size_kb"] > 0 and res["size_kb"] < res["raw_size_kb"]


def _baked_ok(res):
    o = bpy.data.objects[res["object"]]
    return o.type == "MESH" and not o.modifiers and res["verts"] > 121 * 2 and res["source"] == "ScatterGround"


def _fur_ok(res):
    ob = bpy.data.objects[res["object"]]
    me = ob.evaluated_get(bpy.context.evaluated_depsgraph_get()).data
    return faces(res) > 1000 and "fur_color" in me.attributes and me.attributes["fur_color"].domain == "POINT"


CASES = [
    # ---------------- geometry nodes
    ("scatter_on_surface", {"density": 5, "seed": 3}, lambda r: ev(r) > 50 and r["demo"]),
    ("scatter_with_min_distance", {"min_distance": 0.8, "density_max": 20},
     lambda r: 20 < ev(r) < 300),
    ("instance_along_curve", {"spacing": 0.5}, lambda r: ev(r) > 10),
    ("procedural_fence", {"post_spacing": 1.0, "rail_count": 3}, lambda r: faces(r) > 100 and r["rail_count"] == 3),
    ("procedural_building", {"floors": 4, "windows_x": 5, "windows_y": 3},
     lambda r: faces(r) > 4 * (5 + 3) * 2 * 5 and abs(r["evaluated"]["dimensions"][2] - 12) < 1e-3),
    ("procedural_stairs", {"steps": 10, "step_height": 0.2},
     lambda r: faces(r) == 60 and abs(r["evaluated"]["dimensions"][2] - 2.0) < 1e-3),
    ("cable_between_points", {"start": (0, 0, 3), "end": (6, 0, 3), "sag": 1.0},
     lambda r: faces(r) > 100 and 0.9 < r["evaluated"]["dimensions"][2] < 1.2),
    ("stylized_fur_cards", {"density": 300}, _fur_ok),
    ("terrain_from_noise", {"resolution": 64, "height": 4},
     lambda r: faces(r) == 63 * 63 and r["evaluated"]["dimensions"][2] > 0.5),
    ("random_greebles", {"probability": 0.5}, lambda r: faces(r) > r["faces_before"] + 50),
    ("realize_and_apply_gn", {}, _baked_ok),
    # ---------------- pipeline
    ("generate_lods", {}, _lods_ok),
    ("unity_collision_mesh", {"max_tris": 120}, lambda r: _ucx_ok(r) and r["tris"] <= 120),
    ("lightmap_uv2", {}, _uv2_ok),
    ("apply_and_freeze_for_export", {}, _freeze_ok),
    ("batch_export", {"preset": "unity_fbx", "directory": os.path.join(TMP, "fbx")}, _files_ok),
    ("batch_export", {"preset": "glb", "directory": os.path.join(TMP, "glb")}, lambda r: _files_ok(r, 2, ".glb")),
    ("naming_convention_fix", {"demo": True}, _names_ok),
    ("purge_orphans", {"demo": True}, lambda r: r["removed"] >= 3 and r["by_type"].get("meshes") == 1),
    ("texture_files_maintenance", {"action": "report", "demo": True},
     lambda r: r["missing_count"] == 1 and "demo_albedo" in r["external"]),
    ("texture_files_maintenance", {"action": "pack", "demo": True},
     lambda r: r["changed"] == ["demo_albedo"] and bpy.data.images["demo_albedo"].packed_file is not None),
    ("scene_units_setup", {"fps": 60},
     lambda r: r["system"] == "METRIC" and r["scale_length"] == 1.0 and r["length_unit"] == "METERS" and r["fps"] == 60),
    ("xr_budget_report", {"platform": "quest", "demo": True}, _budget_ok),
    ("texture_resize", {"max_size": 1024, "demo": True, "output_dir": os.path.join(TMP, "tex")}, _resize_ok),
    ("merge_by_material", {}, _merge_ok),
    ("set_pivot_bottom_center", {}, _pivot_ok),
    ("gltf_optimize_export", {"filepath": os.path.join(TMP, "opt", "scene.glb"), "compression": "draco"}, _gltf_ok),
]
