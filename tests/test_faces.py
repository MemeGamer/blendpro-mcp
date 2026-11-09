"""Face expression recipes: atlas, decal setup, set by name, animate with blinks, sheet import."""
import os
import tempfile

import bpy
import numpy as np

D = os.path.join(tempfile.gettempdir(), "blendpro_face_tests")
os.makedirs(D, exist_ok=True)
SHEET = os.path.join(D, "sheet_2x2.png")


def _make_sheet():
    S = 400
    yy, xx = np.mgrid[0:S, 0:S].astype(np.float64)
    a = np.ones((S, S, 4))
    for cy in (100, 300):
        for cx in (100, 300):
            for ex in (cx - 45, cx + 45):  # two dot eyes per cell
                a[np.hypot(xx - ex, yy - cy) < 14, :3] = 0
            a[(abs(xx - cx) < 18) & (abs(yy - (cy + 28)) < 5), :3] = 0  # flat mouth
    img = bpy.data.images.new("sheet", S, S, alpha=True)
    img.pixels.foreach_set(a[::-1].astype(np.float32).ravel())
    img.filepath_raw = SHEET
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


_make_sheet()


def _atlas(res):
    assert res["faces"] == 50 and res["grid"] == [8, 7], res
    return True


def _setup(res):
    o = bpy.data.objects[res["decal"]]
    assert o.parent and o.parent.name == "Head"
    assert o["expression"] == 5, o["expression"]  # "wink"
    nodes = [n for n in bpy.data.materials[res["material"]].node_tree.nodes if n.bl_idname == "ShaderNodeAttribute"]
    assert nodes and nodes[0].attribute_name == "expression"
    return True


def _prep_decal():
    from addon.recipes.faces import face_expression_setup
    face_expression_setup(expression="happy")


def _set(res):
    assert res["expression"] == 9 and res["name"] == "angry", res
    return bpy.data.objects["FaceDecal"]["expression"] == 9


def _anim(res):
    assert len(res["keys"]) == 3, res
    assert len(res["blinks"]) >= 2, res  # happy (blink->content) for 120 frames at every ~48
    return True


def _sheet(res):
    assert res["count"] == 4, res
    a = np.empty(1024 * 1024 * 4, dtype=np.float32)
    img = bpy.data.images.load(os.path.join(D, "out", "Face_1.png"))
    img.pixels.foreach_get(a)
    a = a.reshape(1024, 1024, 4)[::-1]
    ys, xs = np.nonzero(a[..., 3] > 0.5)
    assert abs((xs.min() + xs.max()) / 2 - 512) < 6, (xs.min(), xs.max())
    return True


CASES = [
    ("face_atlas_build", {}, _atlas),
    ("face_expression_setup", {"expression": "wink"}, _setup),
    ("face_expression_set", {"expression": "angry", "frame": 10}, _set, _prep_decal),
    ("face_expression_animate", {"keys": [[1, "happy"], [60, "surprised"], [100, "happy"]], "blink_every": 48,
                                 "blink_jitter": 0, "end": 160}, _anim, _prep_decal),
    ("face_sheet_import", {"sheet_path": SHEET, "out_dir": os.path.join(D, "out"), "start_index": 1, "rows": 2, "cols": 2},
     _sheet),
]


def _rigged_head():
    from addon.recipes.rigging import squash_stretch_rig
    squash_stretch_rig()  # demo "Ball" + SquashRig


def _follow(res):
    """The face must ride the rig: move the root and the decal moves with it."""
    assert res["follows_rig"] == "SquashRig", res
    decal = bpy.data.objects[res["decal"]]
    def cx():
        dg = bpy.context.evaluated_depsgraph_get()
        ev = decal.evaluated_get(dg); me = ev.to_mesh()
        x = sum((ev.matrix_world @ v.co).x for v in me.vertices) / len(me.vertices)
        ev.to_mesh_clear(); return x
    x0 = cx()
    bpy.data.objects["SquashRig"].pose.bones["root"].location = (1.0, 0, 0)  # root bone X = world X
    bpy.context.view_layer.update()
    assert abs(cx() - x0 - 1.0) < 0.05, (x0, cx())
    return True


CASES.append(("face_expression_setup", {"target": "Ball", "expression": "happy"}, _follow, _rigged_head))
