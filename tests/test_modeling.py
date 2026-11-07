"""Tests for recipes/modeling.py. Each case runs on an empty factory scene.

Recipes that edit an existing object get their scene built lazily by `Prep`: the runner expands params with
`fn(**params)`, which calls Prep.keys() *after* the scene reset, so setup runs at the right moment."""
import math
from collections.abc import Mapping

import bmesh
import bpy
from mathutils import Matrix


class Prep(Mapping):
    def __init__(self, setup, **params):
        self.setup, self.params = setup, params

    def keys(self):
        self.setup()
        return self.params.keys()

    def __getitem__(self, k):
        return self.params[k]

    def __iter__(self):
        return iter(self.params)

    def __len__(self):
        return max(1, len(self.params))


def _obj(name, build, location=(0, 0, 0)):
    bm = bmesh.new()
    build(bm)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    o = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(o)
    o.location = location
    return o


def cube(name="Cube", size=1.0, location=(0, 0, 0)):
    return _obj(name, lambda bm: bmesh.ops.create_cube(bm, size=size), location)


def sphere(name="Sphere", segs=64, rings=32):
    return _obj(name, lambda bm: bmesh.ops.create_uvsphere(bm, u_segments=segs, v_segments=rings, radius=1.0))


def cylinder(name="Cyl"):
    return _obj(name, lambda bm: bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=16,
                                                        radius1=0.5, radius2=0.5, depth=2))


def two_cubes(name="Pair"):
    def b(bm):
        bmesh.ops.create_cube(bm, size=1)
        bmesh.ops.create_cube(bm, size=1, matrix=Matrix.Translation((3, 0, 0)))
    return _obj(name, b)


def dirty():
    def b(bm):
        bmesh.ops.create_cube(bm, size=1)
        bmesh.ops.create_cube(bm, size=1)  # overlapping duplicate geometry
        bm.verts.new((5, 5, 5))           # loose vertex
    return _obj("Dirty", b)


def unwrapped_cube():
    o = cube("UVCube", 2.0)
    uv = o.data.uv_layers.new(name="UVMap")
    co = []
    for p in o.data.polygons:  # simple tiled layout: every face fills the 0-1 square
        co += [0, 0, 1, 0, 1, 1, 0, 1][: 2 * p.loop_total]
    uv.data.foreach_set("uv", co)


def transformed_parent():
    p = cube("Parent", 1.0, (1, 2, 3))
    p.rotation_euler = (0, 0, math.radians(45))
    p.scale = (2, 3, 1)
    c = cube("Child", 0.2, (0, 0, 0))
    bpy.context.view_layer.update()
    c.parent = p
    c.matrix_parent_inverse = p.matrix_world.inverted()
    c.location = (5, 0, 0)


def _world(name):
    bpy.context.view_layer.update()
    return bpy.data.objects[name].matrix_world.translation.copy()


def check_apply(res):
    o = bpy.data.objects["Parent"]
    assert res["scale"] == [1.0, 1.0, 1.0] and res["rotation_deg"] == [0.0, 0.0, 0.0], res
    assert sorted(res["dimensions"])[0] > 0.99 and abs(max(res["dimensions"]) - 3.536) < 0.01, res
    bpy.context.view_layer.update()
    assert (bpy.data.objects["Child"].matrix_world.translation - (o.matrix_world.translation)).length > 0
    return abs(bpy.data.objects["Child"].matrix_world.translation.x - 5) < 1e-4


CASES = [
    ("rounded_box", {"size": (2, 1, 0.5), "bevel_width": 0.05, "segments": 3},
     lambda r: r["modifiers"] == ["BEVEL", "WEIGHTED_NORMAL"] and r["dimensions"] == [2.0, 1.0, 0.5] and r["verts"] > 8),
    ("boolean_cut", Prep(lambda: cube("Box", 2.0), target="Box", cutter_size=(1, 1, 1), cutter_location=(1, 1, 1)),
     lambda r: r["cutter"] is None and "Box_Cutter" not in bpy.data.objects and r["verts"] > 8 and r["dimensions"] == [2.0, 2.0, 2.0]),
    ("pipe_along_points", {"points": [(0, 0, 0), (2, 0, 0), (2, 2, 0)], "radius": 0.1},
     lambda r: bpy.data.objects["Pipe"].type == "MESH" and r["verts"] > 20 and abs(r["dimensions"][0] - 2.1) < 0.02 and r["dimensions"][2] == 0.2),
    ("radial_array", Prep(lambda: cube("Spoke", 0.5), target="Spoke", count=6, radius=2.0, apply=True),
     lambda r: r["verts"] == 48 and r["pivot"] is None and 4.4 < r["dimensions"][0] < 5.0),
    ("array_along_curve", Prep(lambda: cube("Link", 0.2), target="Link", points=[(0, 0, 0), (2, 1, 0), (4, 0, 0)]),
     lambda r: r["modifiers"] == ["ARRAY", "CURVE"] and r["verts"] > 8 * 10 and r["dimensions"][0] > 3.5),
    ("mirror_setup", Prep(lambda: cube("Half", 1.0, (0, 0, 0)), target="Half", axis="X", apply=True),
     lambda r: r["verts"] == 12 and r["dimensions"][0] == 1.0),
    ("lathe_profile", {"steps": 24},
     lambda r: r["faces"] >= 24 * 4 and abs(r["dimensions"][2] - 0.8) < 1e-3 and r["dimensions"][0] > 0.6),
    ("text_3d", {"text": "XR", "size": 1.0, "extrude": 0.1},
     lambda r: r["verts"] > 20 and abs(r["dimensions"][2] - 0.2) < 1e-3 and abs(_world(r["object"]).x) < 1e-6),
    ("low_poly_rock", {"seed": 3, "subdivisions": 2},
     lambda r: r["tris"] == 80 and r["dimensions"][2] < r["dimensions"][0] * 1.2
     and abs(min((bpy.data.objects["Rock"].matrix_world @ v.co).z for v in bpy.data.objects["Rock"].data.vertices)) < 1e-5),
    ("stairs", {"steps": 5, "width": 1.2},
     lambda r: r["dimensions"] == [1.2, 1.4, 0.9] and r["non_manifold_edges"] == 0 and r["ngons"] == 0),
    ("mesh_cleanup", Prep(dirty, target="Dirty"),
     lambda r: r["after"]["verts"] == 8 and r["after"]["faces"] == 6 and r["loose_removed"] >= 1 and r["non_manifold_edges"] == 0),
    ("fix_ngons", Prep(cylinder, target="Cyl", mode="TRIANGULATE"),
     lambda r: r["before"]["ngons"] == 2 and r["after"]["ngons"] == 0 and r["after"]["tris"] == r["before"]["tris"]),
    ("apply_transforms", Prep(transformed_parent, target="Parent"), check_apply),
    ("set_origin", Prep(lambda: cube("Crate", 1.0, (0, 0, 3)), target="Crate", mode="BOTTOM_CENTER"),
     lambda r: r["location"] == [0.0, 0.0, 2.5] and abs(min(v.co.z for v in bpy.data.objects["Crate"].data.vertices)) < 1e-6),
    ("decimate_to_tris", Prep(sphere, target="Sphere", target_tris=500),
     lambda r: r["tris_before"] > 3000 and r["tris_after"] <= 550 and r["applied"] and not bpy.data.objects["Sphere"].modifiers),
    ("remesh_voxel", Prep(lambda: sphere("Blob", 16, 8), target="Blob", voxel_size=0.1),
     lambda r: r["verts"] > 500 and not r.get("modifiers") and 1.8 < r["dimensions"][0] < 2.2),
    ("separate_parts", Prep(two_cubes, target="Pair", by="LOOSE"),
     lambda r: r["count"] == 2 and all(v == 8 for v in r["verts"].values())),
    ("join_objects", Prep(lambda: [cube("A"), cube("B", 1, (2, 0, 0)), cube("C", 1, (4, 0, 0))], targets=["A", "B", "C"], name="Joined"),
     lambda r: r["object"] == "Joined" and r["verts"] == 24 and len(bpy.data.objects) == 1 and r["dimensions"][0] == 5.0),
    ("smooth_by_angle", Prep(cylinder, target="Cyl", angle=30, weighted_normals=True),
     lambda r: r["sharp_edges"] == 32 and r["smooth_faces"] == r["faces"] and r["modifiers"] == ["WEIGHTED_NORMAL"]),
    ("bevel_weight_by_angle", Prep(lambda: cube("Box"), target="Box", angle=30, width=0.05),
     lambda r: r["weighted_edges"] == 12 and r["modifiers"] == ["BEVEL"] and r["verts"] > 8
     and "bevel_weight_edge" in bpy.data.objects["Box"].data.attributes),
    ("smart_uv_unwrap", Prep(lambda: cube("Box"), target="Box", method="SMART"),
     lambda r: r["uv_layers"] == ["UVMap"] and r["uv_bounds"][0] >= -1e-4 and r["uv_bounds"][3] <= 1.0001 and r["uv_bounds"][2] > 0.3),
    ("solidify_shell", Prep(lambda: _obj("Wall", lambda bm: bmesh.ops.create_grid(bm, x_segments=2, y_segments=2, size=1)), target="Wall", thickness=0.1),
     lambda r: r["modifiers"] == ["SOLIDIFY"] and abs(r["dimensions"][2] - 0.1) < 1e-3),
    ("seams_by_angle_unwrap", Prep(lambda: cube("Box"), target="Box", angle=45),
     lambda r: r["seams"] == 12 and r["uv_bounds"] is not None and max(r["uv_bounds"]) <= 1.0001),
    ("symmetrize", Prep(lambda: cube("Lop", location=(0.4, 0, 0)), target="Lop", axis="X"),
     lambda r: abs(r["extent_on_axis"][0] + r["extent_on_axis"][1]) < 1e-3),
    ("texel_density", Prep(unwrapped_cube, target="UVCube", texture_size=1024, target_density=256),
     lambda r: abs(r["density_px_per_m"] - 512) < 0.5 and abs(r["density_after"] - 256) < 0.5),
]
