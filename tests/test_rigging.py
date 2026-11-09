"""Tests for recipes/rigging.py — every case runs on an empty factory scene (recipes build demos when target=None)."""
import math

import bpy
from mathutils import Vector


def O(n):
    return bpy.data.objects[n]


def fcurves(owner):
    ad = owner.animation_data
    if not ad or not ad.action:
        return []
    out = []
    for layer in ad.action.layers:
        for strip in layer.strips:
            cb = strip.channelbag(ad.action_slot)
            if cb:
                out.extend(cb.fcurves)
    return out


def bone_paths(owner):
    return {fc.data_path.split('"')[1] for fc in fcurves(owner) if fc.data_path.startswith("pose.bones")}


def _biped(res):
    a = O(res["armature"])
    names = {b.name for b in a.data.bones}
    return (len(a.data.bones) == 22 and {"mixamorig:Hips", "mixamorig:LeftArm", "mixamorig:RightUpLeg",
                                          "mixamorig:Spine2", "mixamorig:LeftToeBase"} <= names
            and a.data.bones["mixamorig:LeftForeArm"].parent.name == "mixamorig:LeftArm" and a.mode == "OBJECT")


def _mp_landmarks():
    # 33 MediaPipe WORLD landmarks (x right, y down, z depth; hips at origin), T-ish pose, with visibility
    P = {"nose": (0, -0.62, -0.08), "left_ear": (0.07, -0.6, 0), "right_ear": (-0.07, -0.6, 0),
         "left_shoulder": (0.18, -0.45, 0), "right_shoulder": (-0.18, -0.45, 0),
         "left_elbow": (0.45, -0.45, 0), "right_elbow": (-0.45, -0.45, 0),
         "left_wrist": (0.7, -0.45, 0), "right_wrist": (-0.7, -0.45, 0),
         "left_index": (0.8, -0.45, 0), "right_index": (-0.8, -0.45, 0),
         "left_pinky": (0.78, -0.45, 0.02), "right_pinky": (-0.78, -0.45, 0.02),
         "left_hip": (0.1, 0, 0), "right_hip": (-0.1, 0, 0), "left_knee": (0.1, 0.42, -0.02),
         "right_knee": (-0.1, 0.42, -0.02), "left_ankle": (0.1, 0.85, 0.02), "right_ankle": (-0.1, 0.85, 0.02),
         "left_foot_index": (0.1, 0.9, -0.12), "right_foot_index": (-0.1, 0.9, -0.12)}
    from addon.recipes.rigging import _MP_NAMES
    out = []
    for n in _MP_NAMES:
        p = P.get(n)
        out.append(list(p) + [0.99] if p else [0.0, 0.0, 0.0, 0.05])  # low visibility -> ignored
    out[_MP_NAMES.index("right_wrist")] = [float("nan"), 0, 0, 0.9]  # broken value -> skipped robustly
    return out


def _mp(res):
    a = O(res["armature"])
    b = a.data.bones
    names = {x.name for x in b}
    ok = {"Hips", "Spine", "Chest", "Neck", "Head", "UpperArm.L", "ForeArm.L", "Hand.L", "Thigh.R", "Shin.R", "Foot.R"} <= names
    # right wrist was NaN -> ForeArm.R/Hand.R skipped, rest present
    ok &= "Hand.R" not in names and "UpperArm.R" in names
    # coords: mediapipe y-down -> Blender z-up ; head above hips, grounded feet
    ok &= b["Head"].head_local.z > b["Hips"].head_local.z > b["Shin.L"].tail_local.z
    ok &= abs(min(min(x.head_local.z, x.tail_local.z) for x in b)) < 1e-4
    ok &= b["UpperArm.L"].head_local.x > 0  # person's left at +X
    return ok and b["Shin.L"].parent.name == "Thigh.L" and res["roots"] == ["Hips"]


def _generic(res):
    a = O(res["armature"])
    b = a.data.bones
    return (len(b) == 3 and b["c"].parent.name == "a" and b["d"].parent is None  # b invalid -> c re-parented to a
            and (b["a"].tail_local - Vector((0, 0, 1))).length < 1e-4 and any("skipped b" in w for w in res["warnings"]))


def _auto(res):
    m = O(res["mesh"])
    grp = {g.name for g in m.vertex_groups}
    has_w = any(g.weight > 0.5 for v in m.data.vertices for g in v.groups)
    return (res["unweighted"] == 0 and m.parent and m.parent.type == "ARMATURE" and "UpperArm.L" in grp and has_w
            and any(md.type == "ARMATURE" for md in m.modifiers))


def _limit(res):
    m = O(res["mesh"])
    mx = max(len(v.groups) for v in m.data.vertices)
    sums = [sum(g.weight for g in v.groups) for v in m.data.vertices]
    return res["max_influences_before"] == 6 and mx <= 4 and all(abs(s - 1) < 1e-3 for s in sums)


def _ik(res):
    a = O(res["armature"])
    pb = a.pose.bones[res["bone"]]
    ik = [c for c in pb.constraints if c.type == "IK"][0]
    if not (ik.subtarget == res["control"] and ik.pole_subtarget == res["pole"] and ik.chain_count == 2):
        return False
    # moving the IK control up must bend the leg (shin head moves)
    bpy.context.view_layer.update()
    before = pb.head.copy()
    c = a.pose.bones[res["control"]]
    c.location = c.bone.matrix_local.to_3x3().inverted() @ Vector((0, 0, 0.3))
    bpy.context.view_layer.update()
    return (pb.head - before).length > 0.02 and not a.data.bones[res["control"]].use_deform


def _look(res):
    a = O(res["owner"])
    c = a.pose.bones[res["bone"]].constraints[res["constraint"]]
    return c.type == "DAMPED_TRACK" and c.target.name == res["target"] == "LookTarget"


def _childof(res):
    p = O(res["prop"])
    c = p.constraints["PickUp"]
    infl = [fc for fc in fcurves(p) if "influence" in fc.data_path]
    if c.type != "CHILD_OF" or not infl or len(infl[0].keyframe_points) != 4:
        return False
    sc = bpy.context.scene
    sc.frame_set(9)
    w9 = p.matrix_world.to_translation()
    sc.frame_set(10)
    w10 = p.matrix_world.to_translation()
    sc.frame_set(31)
    sc.frame_set(30)
    w30 = p.matrix_world.to_translation()
    sc.frame_set(31)
    w31 = p.matrix_world.to_translation()
    return (w9 - w10).length < 1e-3 and (w30 - w31).length < 1e-3  # no jumps at pick/release


def _colls(res):
    a = O(res["armature"])
    cols = {c.name for c in a.data.collections_all}
    hand = a.data.bones["Hand.L"]
    return ({"Torso", "Arm.L", "Arm.R", "Leg.L", "Leg.R", "Controls"} <= cols and "Arm.L" in [c.name for c in hand.collections]
            and hand.color.palette != "DEFAULT" and not a.data.collections["Controls"].is_visible)


def _rename(res):
    a = O(res["armature"])
    n = {b.name for b in a.data.bones}
    return res["renamed"] == 22 and {"mixamorig:LeftArm", "mixamorig:Hips", "mixamorig:RightToeBase"} <= n and res["from"] == "blender"


def _rest(res):
    a = O(res["armature"])
    b = a.data.bones["UpperArm.L"]
    pb = a.pose.bones["UpperArm.L"]
    return (b.tail_local.z < b.head_local.z - 0.05 and pb.matrix_basis.is_identity is not None
            and abs(pb.rotation_quaternion.angle) < 1e-5 and a.mode == "OBJECT")


def _scale(res):
    a = O(res["armature"])
    cube = O(res["children_fixed"][0])
    zs = [(cube.matrix_world @ v.co).z for v in cube.data.vertices]
    return (all(abs(s - 1) < 1e-6 for s in a.scale) and 1.7 < res["rig_height"] < 1.9 and all(abs(s - 1) < 1e-6 for s in cube.scale)
            and abs(min(zs) - 0.7) < 1e-3 and abs(max(zs) - 1.1) < 1e-3)


def _root(res):
    a = O(res["armature"])
    return a.data.bones["Hips"].parent.name == "Root" and not a.data.bones["Root"].use_deform and res["reparented"] == ["Hips"]


def _kf(res):
    o = O(res["target"])
    fcs = fcurves(o)
    loc = [fc for fc in fcs if fc.data_path == "location"]
    bpy.context.scene.frame_set(24)
    return len(loc) == 3 and len(loc[0].keyframe_points) == 3 and abs(math.degrees(o.rotation_euler.z) - 180) < 0.1 \
        and res["frame_range"] == [1.0, 24.0]


def _walk(res):
    a = O(res["armature"])
    fcs = fcurves(a)
    bones = bone_paths(a)
    if not ({"Thigh.L", "Shin.R", "UpperArm.L", "Hips"} <= bones and all(any(m.type == "CYCLES" for m in fc.modifiers) for fc in fcs)):
        return False
    sc = bpy.context.scene
    # left foot goes forward (-Y) at quarter cycle and back at 3/4
    sc.frame_set(7)
    f1 = a.pose.bones["Foot.L"].head.y
    sc.frame_set(19)
    f2 = a.pose.bones["Foot.L"].head.y
    k = len([fc for fc in fcs if 'Thigh.L' in fc.data_path][0].keyframe_points)
    return f1 < f2 - 0.1 and k == 9 and res["frame_range"] == [1, 25]


def _idle(res):
    a = O(res["target"])
    return {"Chest", "UpperChest", "Head", "Shoulder.L"} <= bone_paths(a) and res["frame_range"] == [1, 97] \
        and all(any(m.type == "CYCLES" for m in fc.modifiers) for fc in fcurves(a))


def _path(res):
    o = O(res["target"])
    c = o.constraints[res["constraint"]]
    sc = bpy.context.scene
    sc.frame_set(1)
    p1 = o.matrix_world.to_translation()
    sc.frame_set(31)
    p2 = o.matrix_world.to_translation()
    return c.type == "FOLLOW_PATH" and (p1 - p2).length > 1.0 and 2.0 < p2.length < 4.5 and len(fcurves(o)[0].keyframe_points) == 2


def _bake(res):
    a = O(res["target"])
    nocon = all(len(pb.constraints) == 0 for pb in a.pose.bones)
    fc = [f for f in fcurves(a) if 'Thigh.L' in f.data_path and 'rotation_quaternion' in f.data_path]
    if not (nocon and fc and len(fc[0].keyframe_points) == 24):
        return False
    vals = [fc[1].evaluate(f) for f in (1, 12)]  # thigh rotates when IK target lifted
    return abs(vals[0] - vals[1]) > 0.02 and res["action"].endswith("_baked")


def _nla(res):
    o = O(res["target"])
    tr = o.animation_data.nla_tracks
    s = tr[0].strips
    return len(tr) == 1 and len(s) == 2 and s[1].frame_start >= s[0].frame_end and o.animation_data.action is None


def _report(res):
    return (res["action"] == "Walk" and res["fcurves"] > 30 and res["cyclic_fcurves"] == res["fcurves"]
            and res["channels"]["Thigh.L"]["keys"] == 9 and "rotation_quaternion" in res["channels"]["Thigh.L"]["channels"])


def _interp(res):
    o = O(res["target"])
    fcs = fcurves(o)
    return all(kp.interpolation == "LINEAR" for fc in fcs for kp in fc.keyframe_points) and \
        all(any(m.type == "CYCLES" for m in fc.modifiers) for fc in fcs) and res["fcurves"] == len(fcs)


def _shape(res):
    m = O(res["mesh"])
    key = m.data.shape_keys
    fcs = fcurves(key)
    kb = key.key_blocks["Blink"]
    moved = sum(1 for i, d in enumerate(kb.data) if (d.co - m.data.vertices[i].co).length > 1e-4)
    bpy.context.scene.frame_set(4)
    return res["shape_keys"] == ["Basis", "Blink"] and len(fcs) == 1 and len(fcs[0].keyframe_points) == 3 \
        and moved == res["vertices_moved"] > 0 and abs(kb.value - 1) < 1e-4


def _driver(res):
    o = O(res["target"])
    e = O("DriverCtrl")
    e.location.x = 1.0
    bpy.context.view_layer.update()
    return res["valid"] and abs(o.rotation_euler.z - 2.0) < 1e-4 and res["current_value"] == 1.0


def _retarget(res):
    t = O(res["target"])
    s = O(res["source"])
    sc = bpy.context.scene
    if not ({"mixamorig:LeftUpLeg", "mixamorig:Hips"} <= bone_paths(t) and res["mapped_bones"] == 22):
        return False
    # foot directions should match source (same pose)
    sc.frame_set(7)
    ds = (s.pose.bones["Shin.L"].tail - s.pose.bones["Shin.L"].head).normalized()
    dt = (t.pose.bones["mixamorig:LeftLeg"].tail - t.pose.bones["mixamorig:LeftLeg"].head).normalized()
    return ds.dot(dt) > 0.999 and len(fcurves(t)[0].keyframe_points) == 24


def _rootmo(res):
    a = O(res["armature"])
    sc = bpy.context.scene
    sc.frame_set(24)
    hy = a.pose.bones["Hips"].head.y
    return abs(hy) < 1e-3 and abs(res["travel_extracted"][1] + 2.0) < 1e-3


def _rootmo_to_root(res):
    a = O(res["armature"])
    sc = bpy.context.scene
    sc.frame_set(24)
    return abs(a.pose.bones["Hips"].head.y + 2.0) < 1e-3 and abs(a.pose.bones["Root"].head.y + 2.0) < 1e-3


def _fps(res):
    sc = bpy.context.scene
    return sc.render.fps == 60 and sc.frame_start == 1 and sc.frame_end == 120 and res["duration_s"] == 2.0


CASES = [
    ("biped_armature", {"name": "Hero", "naming": "mixamo", "height": 1.75}, _biped),
    ("armature_from_joint_positions", {"joints": _mp_landmarks(), "preset": "mediapipe", "coords": "mediapipe",
                                       "ground": True, "name": "MPRig"}, _mp),
    ("armature_from_joint_positions", {"joints": {"a": [0, 0, 0], "b": [0, 0, None], "c": [0, 0, 1], "d": [1, 0, 0]},
                                       "parents": {"b": "a", "c": "b", "d": "zzz_unknown"}}, _generic),
    ("auto_weight_parent", {}, _auto),
    ("limit_weights_and_normalize", {"limit": 4}, _limit),
    ("ik_chain", {"bone": "shin_L"}, _ik),
    ("look_at_constraint", {}, _look),
    ("child_of_pickup", {"frame_pick": 10, "frame_release": 31}, _childof),
    ("organize_bone_collections", {"hide": ["Controls"]}, _colls),
    ("rename_bones_to_convention", {"to": "mixamo"}, _rename),
    ("apply_pose_as_rest", {}, _rest),
    ("scale_rig_fix", {}, _scale),
    ("add_root_bone", {}, _root),
    ("keyframe_transform", {}, _kf),
    ("procedural_walk_cycle", {}, _walk),
    ("idle_breathing", {}, _idle),
    ("follow_path", {"frames": 120, "bank_deg": 15}, _path),
    ("bake_action", {}, _bake),
    ("nla_push_and_stack", {"gap": 5}, _nla),
    ("action_report", {}, _report),
    ("set_interpolation", {"interpolation": "LINEAR", "cyclic": True}, _interp),
    ("shape_key_animate", {}, _shape),
    ("driver_add", {}, _driver),
    ("retarget_simple", {}, _retarget),
    ("root_motion_extract", {}, _rootmo),
    ("root_motion_extract", {"mode": "to_root"}, _rootmo_to_root),
    ("set_fps_and_range", {"fps": 60, "frame_start": 1, "frame_end": 120}, _fps),
]


def _squash_check(res):
    """Pull the top control down 30%: the ball must get shorter and wider, and its base must stay put."""
    from mathutils import Vector
    arm = bpy.data.objects[res["armature"]]
    ball = bpy.data.objects[res["target"]]

    def dims():
        dg = bpy.context.evaluated_depsgraph_get()
        ev = ball.evaluated_get(dg)
        me = ev.to_mesh()
        ps = [ev.matrix_world @ v.co for v in me.vertices]
        ev.to_mesh_clear()
        return (max(p.z for p in ps) - min(p.z for p in ps), max(p.x for p in ps) - min(p.x for p in ps),
                min(p.z for p in ps))
    h0, w0, z0 = dims()
    arm.pose.bones["top"].location = (0, -0.3, 0)  # bone-local -Y = down for an upward bone
    bpy.context.view_layer.update()
    h1, w1, z1 = dims()
    assert h1 < h0 * 0.8, (h0, h1)
    assert w1 > w0 * 1.05, (w0, w1)
    assert abs(z1 - z0) < 0.01, (z0, z1)
    assert res["pinned_base_vertices"] > 0 and arm.data.collections["DEFORM"].is_visible is False
    return True


CASES.append(("squash_stretch_rig", {}, _squash_check))
